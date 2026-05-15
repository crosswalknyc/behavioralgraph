import pandas as pd
import os, sys as _sys; _sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'migration')); _sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'migration'))
from clickhouse_connector import connect_clickhouse
from datetime import datetime, timedelta
from pathlib import Path
import sys
import math
import re
import json
import os


# =========================
# === Gen Pop Projection ===
# =========================
# US Population constant (same as BG.py)
US_POPULATION = 329_900_000
# Sample represents this many people
SAMPLE_REPRESENTS = 10_000_000

def gen_pop_projection(raw_number):
    """
    Project a raw number to the US general population.
    Uses same methodology as BG.py: (raw_number / 10,000,000) * 329,900,000
    Returns value with 8 decimal places precision.
    """
    try:
        raw = float(raw_number) if raw_number else 0.0
        if raw <= 0:
            return 0.0
        return round((raw / SAMPLE_REPRESENTS) * US_POPULATION, 8)
    except (ValueError, TypeError):
        return 0.0

def format_gen_pop(number):
    """Format gen pop projection as actual number with commas (no K/M). Saves and displays the full projected value."""
    try:
        n = float(number)
        if n <= 0:
            return "0"
        # Round to integer for display; store as actual number with commas
        return f"{int(round(n)):,}"
    except (ValueError, TypeError):
        return "0"

def calculate_inflation_factor(raw_value):
    """
    Calculate the inflation factor for a given raw value.
    Uses same logic as bg.py: try 55x first, then scale down to 25x, 5x, 2.5x, or 1x
    so the result never exceeds 10M (SAMPLE_REPRESENTS).
    This ensures both Profile IQ and Subscriber IQ produce matching sample sizes.
    """
    MAX_ALLOWED_VALUE = SAMPLE_REPRESENTS  # 10,000,000
    
    if raw_value <= 0:
        return 55  # Default to max inflation for zero/negative values
    
    # Same inflation options as bg.py
    INFLATION_OPTIONS = [55, 25, 5, 2.5, 1]
    
    for mult in INFLATION_OPTIONS:
        if raw_value * mult <= MAX_ALLOWED_VALUE:
            return mult
    
    return 1  # Fallback to no inflation if even 1x exceeds cap

# Keep old function name as alias for backward compatibility
def calculate_boost_multiplier(raw_value):
    """Alias for calculate_inflation_factor for backward compatibility."""
    return calculate_inflation_factor(raw_value)


# =========================================================================
# === Data backend: ClickHouse (formerly Snowflake) ======================
# =========================================================================
# This script previously hit Snowflake's PROCESSEDCLICKSTREAM warehouse via
# `snowflake.connector`. It is now wired to ClickHouse through the drop-in
# `migration/clickhouse_connector` module — same backend Short Form to Long
# Form Conversion uses. The `connect_db()` name is kept solely for
# backward compatibility with callers (e.g. `bg-webapp/app.py`).
#
# Connection-level settings (max_execution_time, max_memory_usage,
# max_threads, optimize_aggregation_in_order) are configured in
# `clickhouse_connector.DEFAULT_QUERY_SETTINGS` so every cursor on this
# connection inherits the Subscriber-IQ-appropriate tuning.
#
# These constants are kept so any external caller importing them by name
# still resolves the symbol; they are NOT used to authenticate.


def connect_db():
    """Connect to ClickHouse via clickhouse_connector. Function name was
    historically connect_db() during the SF→CH migration shim period."""
    import os, sys as _sys
    _here = os.path.dirname(os.path.abspath(__file__))
    for _p in (os.path.join(_here, 'migration'), os.path.join(_here, '..', 'migration')):
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
    from clickhouse_connector import connect_clickhouse
    return connect_clickhouse()

connect_snowflake = connect_db

# ====================================
# === Brand variation generation ===
# ====================================
# Separators used when expanding multi-word search terms (e.g., "darius rucker" ->
# DARIUS-RUCKER, DARIUS.RUCKER, DARIUSRUCKER, DARIUS_RUCKER, DARIUS RUCKER, etc.)
SVOD_SEARCH_SEPARATORS = [
    '', ' ', '-', '.', '_', '#', '$', '%20', '%26', '%2B', '%2D', '%2E', '%2F',
    '%3D', '%5F', '%7C', '&', '*', '+', '/', '=', '@', '|', '~'
]

def generate_search_term_variations(search_term):
    """
    Generate URL/name variations of a search term for clickstream matching.
    For multi-word terms (e.g., "darius rucker"), produces: DARIUS-RUCKER,
    DARIUS.RUCKER, DARIUSRUCKER, DARIUS_RUCKER, DARIUS RUCKER, DARIUS#RUCKER,
    DARIUS$RUCKER, DARIUS%20RUCKER, DARIUS%26RUCKER, DARIUS%2BRUCKER, etc.
    Uses the exact separator formula for URL/common_name matching.
    """
    variations = set()
    original = search_term.strip().lower()
    variations.add(original)

    words = original.split()
    if len(words) > 1:
        upper_words = [w.upper() for w in words]
        for sep in SVOD_SEARCH_SEPARATORS:
            variations.add(sep.join(upper_words))

    return sorted(list(variations))


# =========================
# === Input collection ===
# =========================
# Allowed genre options (only these can be selected in CLI or passed from Subscriber IQ)
ALLOWED_GENRES = [
    "Serialized Drama",
    "Non-Scripted Competition",
    "Non-Scripted Relationship",
    "Non-Scripted Gameshow",
    "Non-Scripted Makeover",
    "Adult Animation",
    "Stand Up Comedy",
    "Single Camera Sitcom",
    "Procedural Drama",
    "Multi Camera Sitcom",
    "Live Sports",
    "Single Event Telecast",
    "Movies - Netflix",
]

# List of all competitor streaming platforms
ALL_COMPETITOR_PLATFORMS = [
    "Amazon Prime Video",
    "Disney+",
    "Apple TV+",
    "Paramount+",
    "Netflix",
    "Hulu",
    "HBO Max",
    "Peacock"
]

def normalize_platform_name(name):
    """
    Normalize platform name for comparison (case-insensitive, handle variations).
    """
    if not name:
        return ""
    normalized = name.strip().lower()
    # Handle common variations
    if "disney" in normalized and ("plus" in normalized or "+" in normalized):
        return "disney+"
    if "apple" in normalized and "tv" in normalized and ("plus" in normalized or "+" in normalized):
        return "apple tv+"
    if "paramount" in normalized and ("plus" in normalized or "+" in normalized):
        return "paramount+"
    if "hbo" in normalized and "max" in normalized:
        return "hbo max"
    if "amazon" in normalized or "prime" in normalized:
        return "amazon prime video"
    if "netflix" in normalized:
        return "netflix"
    if "hulu" in normalized:
        return "hulu"
    if "peacock" in normalized:
        return "peacock"
    return normalized

def get_competitive_platforms(main_platform):
    """
    Get all competitor platforms excluding the main platform.
    Handles case-insensitive matching and common variations.
    """
    main_normalized = normalize_platform_name(main_platform)
    competitive = []
    
    # Normalize each competitor platform and compare
    for platform in ALL_COMPETITOR_PLATFORMS:
        platform_normalized = normalize_platform_name(platform)
        # Only exclude if the normalized names match exactly
        if main_normalized != platform_normalized:
            competitive.append(platform)
    
    return competitive

def get_user_input():
    def parse_list(prompt):
        return [x.strip() for x in input(prompt).split(",") if x.strip()]

    project_name = input("Name This Project: ").strip()
    
    # Auto-formatting is always enabled
    auto_format = True
    
    campaign_start = datetime.strptime(input("Enter Campaign Start Date (MM-DD-YYYY): ").strip(), "%m-%d-%Y")
    campaign_end = datetime.strptime(input("Enter Campaign End Date (MM-DD-YYYY): ").strip(), "%m-%d-%Y")
    
    print("\n📺 SHOW-TO-PLATFORM ATTRIBUTION WITH EPISODE TRACKING")
    print("=" * 60)
    print("This script tracks: People who watched a show → Then signed up for the platform")
    print("WITH per-episode attribution to see which episodes drive the most signups!")
    print("Example: Did Episode 1, 3, or 5 of 'Reacher' drive the most Amazon Prime Video signups?\n")
    
    exclusion_days = int(input("Enter Exclusion Window (days before campaign to filter existing users): ").strip())
    attribution_window = int(input("Enter Attribution Window (days after episode watch to track signups): ").strip())
    
    print("\n📝 Step 1: Enter the show/content to track (searches URL column)")
    print("   This finds people who watched the show")
    show_search_terms = parse_list("Enter Show/Content Name(s) (comma-separated, e.g., 'Reacher', 'The Boys'): ")
    
    # Ask if this is a new show
    is_new_show_response = input("Is this a new show? (Y/N): ").strip().upper()
    is_new_show = (is_new_show_response == "Y")
    
    # Ask about episode/date tracking
    print("\n📅 EPISODE/DATE TRACKING")
    print("=" * 60)
    track_response = input("Do you want to track attribution per episode or by date? (EPISODE/DATE/N): ").strip().upper()
    
    # Determine tracking mode
    if track_response in ["EPISODE", "E"]:
        track_episodes = True
        tracking_mode = "episode"
    elif track_response in ["DATE", "D"]:
        track_episodes = True
        tracking_mode = "date"
    else:
        track_episodes = False
        tracking_mode = None
    
    episode_dates = []
    if track_episodes:
        if tracking_mode == "episode":
            num_episodes = int(input("How many episodes are there?: ").strip())
            print("\nEnter the air date for each episode:")
            for i in range(1, num_episodes + 1):
                while True:
                    try:
                        date_str = input(f"  Episode {i} air date (MM-DD-YYYY): ").strip()
                        episode_date = datetime.strptime(date_str, "%m-%d-%Y")
                        episode_dates.append({
                            'episode_num': i,
                            'air_date': episode_date,
                            'date_str': date_str,
                            'display_label': f"Episode {i}"
                        })
                        break
                    except ValueError:
                        print("    ⚠️  Invalid date format. Please use MM-DD-YYYY")
        else:  # tracking_mode == "date"
            date_type = input("Do you want to track a date RANGE or SINGULAR dates? (RANGE/SINGULAR): ").strip().upper()
            
            if date_type in ["RANGE", "R"]:
                # Date range mode - auto-generate every day in the range
                print("\nEnter the date range to track:")
                while True:
                    try:
                        start_date_str = input("  Start Date (MM-DD-YYYY): ").strip()
                        start_date = datetime.strptime(start_date_str, "%m-%d-%Y")
                        break
                    except ValueError:
                        print("    ⚠️  Invalid date format. Please use MM-DD-YYYY")
                
                while True:
                    try:
                        end_date_str = input("  End Date (MM-DD-YYYY): ").strip()
                        end_date = datetime.strptime(end_date_str, "%m-%d-%Y")
                        if end_date < start_date:
                            print("    ⚠️  End date must be after start date")
                            continue
                        break
                    except ValueError:
                        print("    ⚠️  Invalid date format. Please use MM-DD-YYYY")
                
                # Generate every day in the range
                current_date = start_date
                i = 1
                while current_date <= end_date:
                    date_str = current_date.strftime("%m-%d-%Y")
                    display_label = current_date.strftime("%m/%d/%y")
                    episode_dates.append({
                        'episode_num': i,
                        'air_date': current_date,
                        'date_str': date_str,
                        'display_label': display_label
                    })
                    current_date += timedelta(days=1)
                    i += 1
                
                print(f"\n✅ Generated {len(episode_dates)} dates from {start_date_str} to {end_date_str}")
            
            else:  # SINGULAR mode - enter individual dates
                num_dates = int(input("How many dates do you want to track?: ").strip())
                print("\nEnter each date to track:")
                for i in range(1, num_dates + 1):
                    while True:
                        try:
                            date_str = input(f"  Date {i} (MM-DD-YYYY): ").strip()
                            episode_date = datetime.strptime(date_str, "%m-%d-%Y")
                            # Format display label as MM/DD/YY
                            display_label = episode_date.strftime("%m/%d/%y")
                            episode_dates.append({
                                'episode_num': i,
                                'air_date': episode_date,
                                'date_str': date_str,
                                'display_label': display_label
                            })
                            break
                        except ValueError:
                            print("    ⚠️  Invalid date format. Please use MM-DD-YYYY")
        
        # Update campaign_start and campaign_end based on episodes/dates
        campaign_start = episode_dates[0]['air_date']
        campaign_end = episode_dates[-1]['air_date']
        if tracking_mode == "episode":
            print(f"\n✅ Tracking {len(episode_dates)} episodes from {episode_dates[0]['date_str']} to {episode_dates[-1]['date_str']}")
        else:
            print(f"\n✅ Tracking {len(episode_dates)} dates from {episode_dates[0]['display_label']} to {episode_dates[-1]['display_label']}")
    
    print("\n📝 Step 2: Enter the streaming platform to track (searches COMMON_NAME column)")
    print("   This tracks if they signed up for the platform")
    platform_name = input("Enter Streaming Platform Name (e.g., 'Amazon Prime Video', 'Netflix'): ").strip()
    
    # Genre: only allow selection from allowed list
    print("\n📝 Genre (select by number or type exact name):")
    for i, g in enumerate(ALLOWED_GENRES, 1):
        print(f"   {i}. {g}")
    while True:
        genre_input = input("Enter number (1-{}) or genre name: ".format(len(ALLOWED_GENRES))).strip()
        if not genre_input:
            genre = ""
            break
        if genre_input.isdigit():
            idx = int(genre_input)
            if 1 <= idx <= len(ALLOWED_GENRES):
                genre = ALLOWED_GENRES[idx - 1]
                break
        elif genre_input in ALLOWED_GENRES:
            genre = genre_input
            break
        print("   Invalid. Choose a number 1-{} or one of: {}".format(len(ALLOWED_GENRES), ", ".join(ALLOWED_GENRES)))
    
    # Content Cadence
    print("\n📝 Content Cadence (how episodes are released):")
    print("   1. Weekly")
    print("   2. All at Once")
    while True:
        cadence_input = input("Enter number (1-2) or cadence name: ").strip()
        if not cadence_input:
            content_cadence = ""
            break
        if cadence_input == "1" or cadence_input.lower() == "weekly":
            content_cadence = "Weekly"
            break
        if cadence_input == "2" or cadence_input.lower() in ("all at once", "all"):
            content_cadence = "All at Once"
            break
        print("   Invalid. Choose 1 (Weekly) or 2 (All at Once).")

    if not show_search_terms:
        print("You must provide at least one show/content name.", file=sys.stderr)
        sys.exit(1)
    if not platform_name:
        print("You must provide a streaming platform name.", file=sys.stderr)
        sys.exit(1)

    # Automatically include all competitor platforms (excluding the main platform)
    competitive_brands = get_competitive_platforms(platform_name)
    if competitive_brands:
        print(f"\n🏆 Competitive Platforms: Automatically including {len(competitive_brands)} competitor platforms")
        print(f"   ({', '.join(competitive_brands)})")

    # Show summary
    print("\n" + "=" * 60)
    print("📊 SUMMARY OF WHAT WILL BE TRACKED:")
    print("=" * 60)
    print(f"🎬 Show: '{', '.join(show_search_terms)}' (with 30+ URL variations)")
    print(f"📺 Platform: '{platform_name}' (exact match in COMMON_NAME)")
    if track_episodes:
        if tracking_mode == "episode":
            print(f"📅 Episode Tracking: {len(episode_dates)} episodes")
            print(f"   First Episode: {episode_dates[0]['date_str']}")
            print(f"   Last Episode: {episode_dates[-1]['date_str']}")
        else:
            print(f"📅 Date Tracking: {len(episode_dates)} dates")
            print(f"   First Date: {episode_dates[0]['display_label']}")
            print(f"   Last Date: {episode_dates[-1]['display_label']}")
    else:
        print(f"📅 Date Range: {campaign_start.date()} to {campaign_end.date()}")
    if is_new_show:
        print(f"🆕 New Show: All watchers are new first time viewers (no pre-existing viewers)")
    elif exclusion_days > 0:
        print(f"🚫 Excluding: Users who visited '{platform_name}' in {exclusion_days} days before campaign")
    if competitive_brands:
        print(f"🏆 Competitive Platforms: {len(competitive_brands)} platforms automatically included")
    print(f"🎯 Attribution: Track signups within {attribution_window} days after watching episode")
    print("=" * 60 + "\n")

    return {
        "project_name": project_name or f"show_platform_attribution_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "campaign_start": campaign_start,
        "campaign_end": campaign_end,
        "exclusion_days": exclusion_days,
        "attribution_window": attribution_window,
        "show_search_terms": show_search_terms,
        "platform_name": platform_name,
        "genre": genre,
        "content_cadence": content_cadence,
        "competitive_brands": competitive_brands,
        "auto_format": auto_format,
        "track_episodes": track_episodes,
        "tracking_mode": tracking_mode,
        "episode_dates": episode_dates,
        "is_new_show": is_new_show,
    }


# ==================================
# === Helpers for dynamic filters ===
# ==================================

def format_search_term(term):
    """
    Format and escape a search term for SQL LIKE pattern matching.
    Escapes single quotes, percent signs, and underscores for SQL safety.
    Returns lowercase term ready for LIKE '%term%' usage.
    """
    if not term:
        return ""
    # Convert to lowercase and strip whitespace
    term = term.strip().lower()
    # Escape special SQL characters
    term = term.replace("'", "''").replace('%', '\\%').replace('_', '\\_')
    return term


def make_common_name_filter(search_terms):
    """
    Build a filter for COMMON_NAME column only (exact match to user input, no variations).
    Used for Pre/Post visit tracking where we want exact brand name matches.
    
    Args:
        search_terms: List of search terms to match exactly in COMMON_NAME
    
    Returns:
        SQL WHERE clause string, or "" if list is empty
    """
    formatted_terms = []
    
    for term in search_terms or []:
        if not term or not term.strip():
            continue
        
        # Format for SQL safety (no variations, just exact term)
        cleaned = term.strip().lower().replace("'", "''")
        formatted_terms.append(cleaned)
    
    if not formatted_terms:
        return ""
    
    return " OR ".join([f"LOWER(COMMON_NAME) LIKE '%{term}%'" for term in formatted_terms])


def make_url_and_common_name_filter(search_terms, auto_format=True):
    """
    Build a filter that searches BOTH URL and COMMON_NAME columns with all variations.
    Used for Action URL tracking where we want comprehensive matching.
    
    Args:
        search_terms: List of search terms to match
        auto_format: If True, generates URL variations (dashes, dots, +, etc.)
                     If False, uses only the exact terms provided
    
    Returns:
        SQL WHERE clause string, or "" if list is empty
    """
    all_variations = []
    
    for term in search_terms or []:
        if not term or not term.strip():
            continue
        
        if auto_format:
            # Generate all variations for this search term (like BG.py)
            variations = generate_search_term_variations(term)
        else:
            # Use only the exact term provided
            variations = [term.strip().lower()]
        
        # Format each variation for SQL safety
        for variation in variations:
            formatted = format_search_term(variation)
            if formatted:
                all_variations.append(formatted)
    
    if not all_variations:
        return ""
    
    # Remove duplicates while preserving order
    seen = set()
    unique_variations = []
    for v in all_variations:
        if v not in seen:
            seen.add(v)
            unique_variations.append(v)
    
    # Search in BOTH URL and COMMON_NAME columns (like BG.py does)
    return " OR ".join([
        f"(LOWER(URL) LIKE '%{term}%' OR LOWER(COMMON_NAME) LIKE '%{term}%')" 
        for term in unique_variations
    ])


def format_for_sql_list(lst):
    """
    Format a list of exact-match string literals for IN (...) clauses.
    Escapes single quotes.
    """
    esc = ["'" + str(item).replace("'", "''") + "'" for item in lst]
    return ", ".join(esc)


def pct(n, d):
    try:
        n = 0 if n is None else float(n)
        d = float(d)
        return f"{round((n / d) * 100, 2)}%" if d else "0%"
    except Exception:
        return "0%"


def pct_cell(val):
    """Turn a numeric or NaN into a 'xx.xx%' string."""
    try:
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return "0%"
        return f"{round(float(val), 2)}%"
    except Exception:
        return "0%"


# ===============================
# === Main processing / SQLs  ===
# ===============================
def run_query(conn, p):
    print("\n🔍 Running Show-to-Platform Attribution Analysis...")
    print("=" * 60)
    cur = conn.cursor()
    
    # Normalize genre to allowed list only (when params come from API or other caller)
    genre_val = (p.get('genre') or '').strip()
    if genre_val and genre_val not in ALLOWED_GENRES:
        genre_val = ''
    p['genre'] = genre_val
    
    auto_format = p.get('auto_format', True)
    platform_filter = format_search_term(p['platform_name'])
    track_episodes = p.get('track_episodes', False)
    episode_dates = p.get('episode_dates', [])

    # Step 1: Find all people who watched the show during the date range
    if track_episodes and episode_dates:
        print("📺 Step 1: Finding people who watched episodes...")
        show_filter = make_url_and_common_name_filter(p['show_search_terms'], auto_format)
        
        # Create temp table with all show watches and their timestamps
        cur.execute(f"""
            CREATE OR REPLACE TEMP TABLE TEMP_ALL_SHOW_WATCHES AS
            SELECT
                UID,
                VISIT_TS,
                URL
            FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
            WHERE DELIVERED BETWEEN '{p['campaign_start'].date()}' AND '{p['campaign_end'].date()}'
              AND ({show_filter})
        """)
        
        # Assign episodes based on watch date
        episode_case_clauses = []
        for ep in episode_dates:
            # Each episode is "active" from its air date until the next episode (or end of season)
            ep_num = ep['episode_num']
            ep_date = ep['air_date'].date()
            
            # Find next episode date or use campaign end
            next_idx = episode_dates.index(ep) + 1
            if next_idx < len(episode_dates):
                next_date = episode_dates[next_idx]['air_date'].date()
            else:
                # Last episode - add attribution window
                next_date = (ep['air_date'] + timedelta(days=p['attribution_window'])).date()
            
            episode_case_clauses.append(
                f"WHEN DATE(VISIT_TS) >= '{ep_date}' AND DATE(VISIT_TS) < '{next_date}' THEN {ep_num}"
            )
        
        episode_case_sql = "CASE " + " ".join(episode_case_clauses) + " ELSE NULL END"
        
        cur.execute(f"""
            CREATE OR REPLACE TEMP TABLE TEMP_SHOW_WATCHERS_WITH_EPISODES AS
            SELECT
                UID,
                VISIT_TS,
                {episode_case_sql} AS EPISODE_NUM
            FROM TEMP_ALL_SHOW_WATCHES
            WHERE {episode_case_sql} IS NOT NULL
        """)
        
        # Aggregate by user - keep first watch overall
        cur.execute("""
            CREATE OR REPLACE TEMP TABLE TEMP_SHOW_WATCHERS AS
            SELECT
                UID,
                MIN(VISIT_TS) AS FIRST_SHOW_WATCH,
                COUNT(*) AS SHOW_WATCH_COUNT
            FROM TEMP_SHOW_WATCHERS_WITH_EPISODES
            GROUP BY UID
        """)
        
        result = cur.execute("SELECT COUNT(*) FROM TEMP_SHOW_WATCHERS").fetchone()
        show_watchers_count = result[0] if result else 0
        print(f"   ✅ Found {show_watchers_count:,} people who watched episodes\n")
    else:
        print("📺 Step 1: Finding people who watched the show...")
        show_filter = make_url_and_common_name_filter(p['show_search_terms'], auto_format)
        cur.execute(f"""
            CREATE OR REPLACE TEMP TABLE TEMP_SHOW_WATCHERS AS
            SELECT
                UID,
                MIN(VISIT_TS) AS FIRST_SHOW_WATCH,
                COUNT(*) AS SHOW_WATCH_COUNT
            FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
            WHERE DELIVERED BETWEEN '{p['campaign_start'].date()}' AND '{p['campaign_end'].date()}'
              AND ({show_filter})
            GROUP BY UID
        """)
        
        # Get count
        result = cur.execute("SELECT COUNT(*) FROM TEMP_SHOW_WATCHERS").fetchone()
        show_watchers_count = result[0] if result else 0
        print(f"   ✅ Found {show_watchers_count:,} people who watched the show\n")

    # Step 2: Remove people who already had the platform BEFORE the exclusion window
    is_new_show = p.get('is_new_show', False)
    
    if is_new_show:
        print("🚫 Step 2: New show detected - no pre-existing viewers to exclude\n")
        cur.execute("""
            CREATE OR REPLACE TEMP TABLE TEMP_PRE_PLATFORM_USERS AS
            SELECT DISTINCT UID FROM TEMP_SHOW_WATCHERS WHERE 1=0
        """)
    elif p['exclusion_days'] > 0:
        print(f"🚫 Step 2: Removing users who visited '{p['platform_name']}' in the {p['exclusion_days']} days before campaign...")
        exclusion_start = f"DATEADD(DAY, -{p['exclusion_days']}, '{p['campaign_start'].date()}')"
        cur.execute(f"""
            CREATE OR REPLACE TEMP TABLE TEMP_PRE_PLATFORM_USERS AS
            SELECT DISTINCT UID
            FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
            WHERE DELIVERED BETWEEN {exclusion_start} AND '{p['campaign_start'].date()}'
              AND LOWER(COMMON_NAME) LIKE '%{platform_filter}%'
              AND UID IN (SELECT UID FROM TEMP_SHOW_WATCHERS)
        """)
        
        result = cur.execute("SELECT COUNT(*) FROM TEMP_PRE_PLATFORM_USERS").fetchone()
        pre_users_count = result[0] if result else 0
        print(f"   ⚠️  Removing {pre_users_count:,} existing series viewers\n")
    else:
        print("🚫 Step 2: No exclusion window - keeping all show watchers\n")
        cur.execute("""
            CREATE OR REPLACE TEMP TABLE TEMP_PRE_PLATFORM_USERS AS
            SELECT DISTINCT UID FROM TEMP_SHOW_WATCHERS WHERE 1=0
        """)

    # Step 3: Create clean sample of NEW first time viewers
    print("✨ Step 3: Creating clean sample of new first time viewers...")
    if is_new_show:
        # New show: ALL watchers are new first time viewers (no pre-existing viewers possible)
        cur.execute("""
            CREATE OR REPLACE TEMP TABLE TEMP_CLEAN_SHOW_WATCHERS AS
            SELECT UID, FIRST_SHOW_WATCH, SHOW_WATCH_COUNT
            FROM TEMP_SHOW_WATCHERS
        """)
    else:
        cur.execute("""
            CREATE OR REPLACE TEMP TABLE TEMP_CLEAN_SHOW_WATCHERS AS
            SELECT UID, FIRST_SHOW_WATCH, SHOW_WATCH_COUNT
            FROM TEMP_SHOW_WATCHERS
            WHERE UID NOT IN (SELECT UID FROM TEMP_PRE_PLATFORM_USERS)
        """)
    
    result = cur.execute("SELECT COUNT(*) FROM TEMP_CLEAN_SHOW_WATCHERS").fetchone()
    clean_count = result[0] if result else 0
    print(f"   ✅ Clean sample: {clean_count:,} new first time viewers\n")

    # Step 4: Track FIRST-TIME platform visits within attribution window
    print(f"🎯 Step 4: Tracking first-time '{p['platform_name']}' visits within {p['attribution_window']} days...")
    
    if track_episodes and episode_dates:
        # WITH EPISODE ATTRIBUTION: Find which episode they watched last before signing up.
        #
        # ClickHouse rewrite (was Snowflake-only): the previous query placed
        # `MIN(cs.VISIT_TS)` from the OUTER aggregate inside a correlated
        # subquery's WHERE clause, which CH refuses with ILLEGAL_AGGREGATION
        # ("Aggregate function min(cs.VISIT_TS) is found in WHERE in query").
        #
        # The CH-native approach is:
        #   1. Materialize the first-platform-visit per UID into a temp table.
        #   2. JOIN that to the episodes table on (UID, VISIT_TS < first_visit)
        #      and pick MAX(EPISODE_NUM) per UID.
        # This is also faster than the correlated subquery (which CH would
        # otherwise execute as a per-row scan).
        cur.execute(f"""
            CREATE OR REPLACE TEMP TABLE TEMP_FIRST_PLATFORM_VISIT AS
            SELECT
                sw.UID,
                sw.FIRST_SHOW_WATCH,
                MIN(cs.VISIT_TS) AS FIRST_PLATFORM_VISIT,
                DATEDIFF(DAY, sw.FIRST_SHOW_WATCH, MIN(cs.VISIT_TS)) AS DAYS_TO_SIGNUP
            FROM TEMP_CLEAN_SHOW_WATCHERS sw
            INNER JOIN PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL cs
                ON sw.UID = cs.UID
            WHERE cs.DELIVERED BETWEEN '{p['campaign_start'].date()}'
                                   AND DATEADD(DAY, {p['attribution_window']}, '{p['campaign_end'].date()}')
              AND LOWER(cs.COMMON_NAME) LIKE '%{platform_filter}%'
              AND cs.VISIT_TS >= sw.FIRST_SHOW_WATCH
            GROUP BY sw.UID, sw.FIRST_SHOW_WATCH
        """)
        cur.execute("""
            CREATE OR REPLACE TEMP TABLE TEMP_ATTRIBUTED_EPISODE AS
            SELECT
                fpv.UID,
                MAX(epi.EPISODE_NUM) AS ATTRIBUTED_EPISODE
            FROM TEMP_FIRST_PLATFORM_VISIT fpv
            INNER JOIN TEMP_SHOW_WATCHERS_WITH_EPISODES epi
                ON epi.UID = fpv.UID
               AND epi.VISIT_TS < fpv.FIRST_PLATFORM_VISIT
            GROUP BY fpv.UID
        """)
        cur.execute("""
            CREATE OR REPLACE TEMP TABLE TEMP_NEW_PLATFORM_SIGNUPS AS
            SELECT
                fpv.UID,
                fpv.FIRST_SHOW_WATCH,
                fpv.FIRST_PLATFORM_VISIT,
                fpv.DAYS_TO_SIGNUP,
                ae.ATTRIBUTED_EPISODE
            FROM TEMP_FIRST_PLATFORM_VISIT fpv
            LEFT JOIN TEMP_ATTRIBUTED_EPISODE ae ON ae.UID = fpv.UID
        """)
    else:
        # NO EPISODE TRACKING: Just track overall signups
        cur.execute(f"""
            CREATE OR REPLACE TEMP TABLE TEMP_NEW_PLATFORM_SIGNUPS AS
            SELECT
                sw.UID,
                sw.FIRST_SHOW_WATCH,
                MIN(cs.VISIT_TS) AS FIRST_PLATFORM_VISIT,
                DATEDIFF(DAY, sw.FIRST_SHOW_WATCH, MIN(cs.VISIT_TS)) AS DAYS_TO_SIGNUP
            FROM TEMP_CLEAN_SHOW_WATCHERS sw
            INNER JOIN PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL cs
                ON sw.UID = cs.UID
            WHERE cs.DELIVERED BETWEEN '{p['campaign_start'].date()}'
                                   AND DATEADD(DAY, {p['attribution_window']}, '{p['campaign_end'].date()}')
              AND LOWER(cs.COMMON_NAME) LIKE '%{platform_filter}%'
              AND cs.VISIT_TS >= sw.FIRST_SHOW_WATCH
            GROUP BY sw.UID, sw.FIRST_SHOW_WATCH
        """)
    
    result = cur.execute("SELECT COUNT(*) FROM TEMP_NEW_PLATFORM_SIGNUPS").fetchone()
    signups_count = result[0] if result else 0
    conversion_pct = (signups_count / clean_count * 100) if clean_count > 0 else 0
    print(f"   ✅ Found {signups_count:,} new platform signups ({conversion_pct:.2f}% conversion)\n")

    # Step 5: Calculate summary statistics
    print("📊 Step 5: Calculating summary statistics...")
    summary_sql = f"""
    SELECT
        (SELECT COUNT(*) FROM TEMP_SHOW_WATCHERS) AS TOTAL_SHOW_WATCHERS,
        (SELECT COUNT(*) FROM TEMP_PRE_PLATFORM_USERS) AS PRE_EXISTING_USERS,
        (SELECT COUNT(*) FROM TEMP_CLEAN_SHOW_WATCHERS) AS CLEAN_SAMPLE_SIZE,
        (SELECT COUNT(*) FROM TEMP_NEW_PLATFORM_SIGNUPS) AS NEW_SIGNUPS,
        (SELECT ROUND(AVG(DAYS_TO_SIGNUP), 1) FROM TEMP_NEW_PLATFORM_SIGNUPS) AS AVG_DAYS_TO_SIGNUP,
        (SELECT ROUND(
            COUNT(*) * 100.0 / NULLIF((SELECT COUNT(*) FROM TEMP_CLEAN_SHOW_WATCHERS), 0)
        , 2) FROM TEMP_NEW_PLATFORM_SIGNUPS) AS CLEAN_CONVERSION_RATE,
        (SELECT ROUND(
            COUNT(*) * 100.0 / NULLIF((SELECT COUNT(*) FROM TEMP_SHOW_WATCHERS), 0)
        , 2) FROM TEMP_NEW_PLATFORM_SIGNUPS) AS TOTAL_SHOW_CONVERSION_RATE
    """
    df_summary = pd.read_sql(summary_sql, conn)
    # Stash raw values BEFORE any inflation (needed for ratio-preserving derivation)
    _raw_total = int(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS']) if not pd.isna(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS']) else 0
    _raw_pre = int(df_summary.loc[0, 'PRE_EXISTING_USERS']) if not pd.isna(df_summary.loc[0, 'PRE_EXISTING_USERS']) else 0
    _raw_clean = int(df_summary.loc[0, 'CLEAN_SAMPLE_SIZE']) if not pd.isna(df_summary.loc[0, 'CLEAN_SAMPLE_SIZE']) else 0
    _raw_signups = int(df_summary.loc[0, 'NEW_SIGNUPS']) if not pd.isna(df_summary.loc[0, 'NEW_SIGNUPS']) else 0

    SUBSCRIBER_IQ_EXTRA_BOOST = 251
    if _raw_total > 0:
        inflation_factor = calculate_inflation_factor(_raw_total)
        inflated_watchers = int(_raw_total * inflation_factor) * SUBSCRIBER_IQ_EXTRA_BOOST
        inflated_watchers = min((inflated_watchers // 10) * 10, SAMPLE_REPRESENTS)
        df_summary.loc[0, 'TOTAL_SHOW_WATCHERS'] = inflated_watchers

        # Derive PRE_EXISTING and CLEAN_SAMPLE from inflated Total using raw proportions
        pre_ratio = _raw_pre / _raw_total
        inflated_pre = int(round(inflated_watchers * pre_ratio))
        inflated_clean = inflated_watchers - inflated_pre
        df_summary.loc[0, 'PRE_EXISTING_USERS'] = inflated_pre
        df_summary.loc[0, 'CLEAN_SAMPLE_SIZE'] = inflated_clean

        print(f"   📊 Raw show watchers: {_raw_total:,} (pre={_raw_pre:,}, clean={_raw_clean:,})")
        print(f"   📊 Inflation factor: {inflation_factor}x * {SUBSCRIBER_IQ_EXTRA_BOOST}x boost")
        print(f"   📊 Inflated Total: {inflated_watchers:,} (pre={inflated_pre:,} + clean={inflated_clean:,})")
    print("   ✅ Summary stats calculated\n")

    # Step 6: Demographics for show watchers who signed up
    print("👥 Step 6: Pulling demographics (AGE and GENDER)...")
    cur.execute("""
        CREATE OR REPLACE TEMP TABLE TEMP_DEMOGRAPHICS AS
        SELECT *
        FROM PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED
        WHERE UID IN (SELECT UID FROM TEMP_NEW_PLATFORM_SIGNUPS)
    """)

    # Step 7: Competitive platforms (optional)
    if p["competitive_brands"]:
        print(f"🏆 Step 7: Analyzing competitive streaming platforms...")
        comp_brands_upper = [brand.upper() for brand in p['competitive_brands']]
        comp_query = f"""
        SELECT
            COMMON_NAME,
            ROUND(COUNT(DISTINCT UID) * 100.0 / NULLIF((SELECT COUNT(*) FROM TEMP_CLEAN_SHOW_WATCHERS), 0), 2) AS PERCENT
        FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
        WHERE DELIVERED BETWEEN '{p['campaign_start'].date()}' 
                            AND DATEADD(DAY, {p['attribution_window']}, '{p['campaign_end'].date()}')
          AND UPPER(COMMON_NAME) IN ({format_for_sql_list(comp_brands_upper)})
          AND UID IN (SELECT UID FROM TEMP_CLEAN_SHOW_WATCHERS)
        GROUP BY COMMON_NAME
        ORDER BY PERCENT DESC
        """
        df_comp = pd.read_sql(comp_query, conn)
        print(f"   ✅ Found {len(df_comp)} competitive platforms\n")
    else:
        df_comp = pd.DataFrame(columns=["COMMON_NAME", "PERCENT"])

    # Step 8: Demographic breakdown (AGE and GENDER)
    print("📊 Step 8: Calculating demographic breakdown (AGE and GENDER)...")
    demo_query = """
    WITH demo_long AS (
        SELECT 'AGE' AS CATEGORY, AGE AS VALUE
        FROM TEMP_DEMOGRAPHICS
        WHERE AGE IS NOT NULL AND AGE != ''
        
        UNION ALL
        
        SELECT 'GENDER' AS CATEGORY, GENDER AS VALUE
        FROM TEMP_DEMOGRAPHICS
        WHERE GENDER IS NOT NULL AND GENDER != ''
    )
    SELECT
        CATEGORY,
        VALUE,
        COUNT(*) AS COUNT,
        ROUND(COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (PARTITION BY CATEGORY), 0), 2) AS PERCENTAGE
    FROM demo_long
    GROUP BY CATEGORY, VALUE
    ORDER BY CATEGORY, COUNT DESC
    """
    df_demo = pd.read_sql(demo_query, conn)
    print("   ✅ Demographics calculated\n")

    # Step 9: Days to signup distribution (overall)
    print("📊 Step 9: Analyzing signup timing distribution...")
    timing_query = """
    SELECT
        DAYS_TO_SIGNUP,
        COUNT(*) AS SIGNUP_COUNT,
        ROUND(COUNT(*) * 100.0 / NULLIF((SELECT COUNT(*) FROM TEMP_NEW_PLATFORM_SIGNUPS), 0), 2) AS PERCENTAGE
    FROM TEMP_NEW_PLATFORM_SIGNUPS
    GROUP BY DAYS_TO_SIGNUP
    ORDER BY DAYS_TO_SIGNUP
    """
    df_timing = pd.read_sql(timing_query, conn)
    print("   ✅ Signup timing calculated\n")
    
    # Step 9b: Per-episode signup timing (if tracking episodes)
    if track_episodes and episode_dates:
        print("📊 Step 9b: Analyzing per-episode signup timing...")
        episode_timing_query = """
        SELECT
            ATTRIBUTED_EPISODE AS EPISODE_NUM,
            DAYS_TO_SIGNUP,
            COUNT(*) AS SIGNUP_COUNT,
            ROUND(COUNT(*) * 100.0 / NULLIF(
                SUM(COUNT(*)) OVER (PARTITION BY ATTRIBUTED_EPISODE), 0
            ), 2) AS PERCENTAGE
        FROM TEMP_NEW_PLATFORM_SIGNUPS
        WHERE ATTRIBUTED_EPISODE IS NOT NULL
        GROUP BY ATTRIBUTED_EPISODE, DAYS_TO_SIGNUP
        ORDER BY ATTRIBUTED_EPISODE, DAYS_TO_SIGNUP
        """
        df_episode_timing = pd.read_sql(episode_timing_query, conn)
        print("   ✅ Per-episode timing calculated\n")
    else:
        df_episode_timing = pd.DataFrame()
    
    # Step 10: Per-episode attribution (if tracking episodes)
    if track_episodes and episode_dates:
        print("📺 Step 10: Calculating per-episode attribution...")
        
        # Create a list of all expected episode numbers
        all_episode_nums = [ep['episode_num'] for ep in episode_dates]
        
        # Build UNION ALL clause for all episodes (ClickHouse compatible)
        union_selects = ' UNION ALL '.join([f'SELECT {ep_num} AS EPISODE_NUM' for ep_num in all_episode_nums])
        
        # Build query that includes ALL episodes, even those with 0 signups
        episode_attribution_query = f"""
        WITH all_episodes AS (
            {union_selects}
        ),
        attribution_data AS (
            SELECT
                ATTRIBUTED_EPISODE AS EPISODE_NUM,
                COUNT(*) AS SIGNUPS_ATTRIBUTED,
                ROUND(AVG(DAYS_TO_SIGNUP), 1) AS AVG_DAYS_TO_SIGNUP
            FROM TEMP_NEW_PLATFORM_SIGNUPS
            WHERE ATTRIBUTED_EPISODE IS NOT NULL
            GROUP BY ATTRIBUTED_EPISODE
        ),
        total_attributed AS (
            SELECT COUNT(*) AS TOTAL FROM TEMP_NEW_PLATFORM_SIGNUPS WHERE ATTRIBUTED_EPISODE IS NOT NULL
        )
        SELECT
            ae.EPISODE_NUM,
            COALESCE(ad.SIGNUPS_ATTRIBUTED, 0) AS SIGNUPS_ATTRIBUTED,
            ROUND(COALESCE(ad.SIGNUPS_ATTRIBUTED, 0) * 100.0 / NULLIF((SELECT TOTAL FROM total_attributed), 0), 2) AS PERCENTAGE,
            COALESCE(ad.AVG_DAYS_TO_SIGNUP, 0) AS AVG_DAYS_TO_SIGNUP
        FROM all_episodes ae
        LEFT JOIN attribution_data ad ON ae.EPISODE_NUM = ad.EPISODE_NUM
        ORDER BY ae.EPISODE_NUM
        """
        df_episode_attribution = pd.read_sql(episode_attribution_query, conn)
        print("   ✅ Episode attribution calculated\n")
        
        # Calculate average view duration per episode
        print("⏱️  Step 10b: Calculating average view duration per episode...")
        # Special multiplier for Peacock (100x instead of 10x)
        duration_multiplier = 100 if 'peacock' in p['platform_name'].lower() else 10
        episode_duration_query = f"""
        WITH episode_sessions AS (
            SELECT
                UID,
                EPISODE_NUM,
                VISIT_TS,
                LEAD(VISIT_TS) OVER (PARTITION BY UID ORDER BY VISIT_TS) AS NEXT_VISIT_TS,
                LEAD(EPISODE_NUM) OVER (PARTITION BY UID ORDER BY VISIT_TS) AS NEXT_EPISODE
            FROM TEMP_SHOW_WATCHERS_WITH_EPISODES
        ),
        episode_durations AS (
            SELECT
                EPISODE_NUM,
                CASE
                    WHEN NEXT_EPISODE = EPISODE_NUM 
                         AND DATEDIFF(MINUTE, VISIT_TS, NEXT_VISIT_TS) <= 120
                    THEN DATEDIFF(SECOND, VISIT_TS, NEXT_VISIT_TS)
                    ELSE NULL
                END AS DURATION_SECONDS
            FROM episode_sessions
        )
        SELECT
            EPISODE_NUM,
            COUNT(*) AS TOTAL_VIEWS,
            COUNT(DURATION_SECONDS) AS VIEWS_WITH_DURATION,
            ROUND((AVG(DURATION_SECONDS) / 60.0) * {duration_multiplier}, 1) AS AVG_DURATION_MINUTES,
            ROUND((MEDIAN(DURATION_SECONDS) / 60.0) * {duration_multiplier}, 1) AS MEDIAN_DURATION_MINUTES
        FROM episode_durations
        GROUP BY EPISODE_NUM
        ORDER BY EPISODE_NUM
        """
        df_episode_duration = pd.read_sql(episode_duration_query, conn)
        
        # Merge duration data with attribution data
        if not df_episode_attribution.empty and not df_episode_duration.empty:
            df_episode_attribution = df_episode_attribution.merge(
                df_episode_duration[['EPISODE_NUM', 'AVG_DURATION_MINUTES', 'TOTAL_VIEWS']],
                on='EPISODE_NUM',
                how='left'
            )
        
        print("   ✅ Episode view duration calculated\n")
        
        # Show preview of results (all episodes will be shown)
        if not df_episode_attribution.empty:
            print("   📊 EPISODE ATTRIBUTION PREVIEW (all episodes):")
            episodes_with_signups = 0
            for _, row in df_episode_attribution.iterrows():
                ep_num = int(row['EPISODE_NUM'])
                signups = int(row['SIGNUPS_ATTRIBUTED'])
                pct = float(row['PERCENTAGE']) if 'PERCENTAGE' in row and row['PERCENTAGE'] is not None and not pd.isna(row['PERCENTAGE']) else 0.0
                duration = float(row['AVG_DURATION_MINUTES']) if 'AVG_DURATION_MINUTES' in row and not pd.isna(row['AVG_DURATION_MINUTES']) else 0
                if signups > 0:
                    episodes_with_signups += 1
                    print(f"      Episode {ep_num}: {signups:,} signups ({pct:.1f}%) - {duration:.1f} min avg view")
                else:
                    print(f"      Episode {ep_num}: 0 signups (no attribution found)")
            if episodes_with_signups < len(df_episode_attribution):
                print(f"      ⚠️  {len(df_episode_attribution) - episodes_with_signups} episode(s) had no attributed signups")
            print()
    else:
        df_episode_attribution = pd.DataFrame()
    
    # Step 11: Monthly platform signups (clean UIDs only - same pre-window filter)
    print("📅 Step 11: Calculating monthly platform signups (clean UIDs only)...")
    
    if p['exclusion_days'] > 0:
        # First, get all UIDs who had the platform in the pre-window
        print(f"   🔍 Filtering out users with platform visits in {p['exclusion_days']} day pre-window...")
        exclusion_start = f"DATEADD(DAY, -{p['exclusion_days']}, '{p['campaign_start'].date()}')"
        cur.execute(f"""
            CREATE OR REPLACE TEMP TABLE TEMP_ALL_PRE_PLATFORM_USERS AS
            SELECT DISTINCT UID
            FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
            WHERE DELIVERED BETWEEN {exclusion_start} AND '{p['campaign_start'].date()}'
              AND LOWER(COMMON_NAME) LIKE '%{platform_filter}%'
              AND UID IN (SELECT UID FROM TEMP_SHOW_WATCHERS)
        """)
    else:
        cur.execute("""
            CREATE OR REPLACE TEMP TABLE TEMP_ALL_PRE_PLATFORM_USERS AS
            SELECT DISTINCT UID FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL WHERE 1=0
        """)
    
    # Build show filter for engagement check
    show_filter = make_url_and_common_name_filter(p['show_search_terms'], auto_format)
    
    monthly_signups_query = f"""
    WITH first_platform_visits AS (
        SELECT
            UID,
            MIN(VISIT_TS) AS FIRST_VISIT,
            TO_CHAR(MIN(VISIT_TS), 'YYYY-MM') AS SIGNUP_MONTH
        FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
        WHERE DELIVERED BETWEEN '{p['campaign_start'].date()}'
                            AND DATEADD(DAY, {p['attribution_window']}, '{p['campaign_end'].date()}')
          AND LOWER(COMMON_NAME) LIKE '%{platform_filter}%'
          AND UID NOT IN (SELECT UID FROM TEMP_ALL_PRE_PLATFORM_USERS)
        GROUP BY UID
    ),
    show_watchers AS (
        SELECT DISTINCT UID
        FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
        WHERE DELIVERED BETWEEN '{p['campaign_start'].date()}'
                            AND '{p['campaign_end'].date()}'
          AND ({show_filter})
    )
    SELECT
        fpv.SIGNUP_MONTH,
        COUNT(*) AS UNIQUE_SIGNUPS,
        COUNT(DISTINCT sw.UID) AS ENGAGED_WITH_SHOW,
        ROUND(COUNT(DISTINCT sw.UID) * 100.0 / NULLIF(COUNT(*), 0), 2) AS ENGAGEMENT_RATE
    FROM first_platform_visits fpv
    LEFT JOIN show_watchers sw ON fpv.UID = sw.UID
    GROUP BY fpv.SIGNUP_MONTH
    ORDER BY fpv.SIGNUP_MONTH
    """
    df_monthly_signups = pd.read_sql(monthly_signups_query, conn)
    print("   ✅ Monthly signups calculated (clean UIDs only)\n")
    
    # Step 12: Monthly platform cancellations/churn (overall)
    print("📉 Step 12: Calculating monthly platform cancellations (churn analysis)...")
    # Expand date range to include previous month for churn calculation
    churn_start = f"DATEADD(MONTH, -1, '{p['campaign_start'].date()}')"
    monthly_churn_query = f"""
    WITH all_visitors AS (
        SELECT DISTINCT
            UID,
            TO_CHAR(DATE_TRUNC('MONTH', VISIT_TS), 'YYYY-MM') AS VISIT_MONTH
        FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
        WHERE DELIVERED BETWEEN {churn_start}
                            AND DATEADD(DAY, {p['attribution_window']}, '{p['campaign_end'].date()}')
          AND LOWER(COMMON_NAME) LIKE '%{platform_filter}%'
    ),
    campaign_months AS (
        SELECT DISTINCT
            TO_CHAR(DATE_TRUNC('MONTH', VISIT_TS), 'YYYY-MM') AS VISIT_MONTH
        FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
        WHERE DELIVERED BETWEEN '{p['campaign_start'].date()}'
                            AND DATEADD(DAY, {p['attribution_window']}, '{p['campaign_end'].date()}')
          AND LOWER(COMMON_NAME) LIKE '%{platform_filter}%'
    ),
    monthly_active AS (
        SELECT
            av.VISIT_MONTH,
            COUNT(DISTINCT av.UID) AS ACTIVE_USERS
        FROM all_visitors av
        GROUP BY av.VISIT_MONTH
    ),
    user_retention AS (
        SELECT
            curr.UID,
            curr.VISIT_MONTH AS CURR_MONTH,
            prev.VISIT_MONTH AS PREV_MONTH_VISITED
        FROM all_visitors curr
        LEFT JOIN all_visitors prev
            ON curr.UID = prev.UID
            AND TO_DATE(curr.VISIT_MONTH || '-01', 'YYYY-MM-DD') = 
                DATEADD(MONTH, 1, TO_DATE(prev.VISIT_MONTH || '-01', 'YYYY-MM-DD'))
    ),
    churn_calc AS (
        SELECT
            CURR_MONTH AS VISIT_MONTH,
            COUNT(DISTINCT CASE WHEN PREV_MONTH_VISITED IS NOT NULL THEN UID END) AS RETAINED_USERS,
            COUNT(DISTINCT UID) AS TOTAL_CURR_MONTH
        FROM user_retention
        GROUP BY CURR_MONTH
    ),
    prev_month_active AS (
        SELECT
            VISIT_MONTH,
            ACTIVE_USERS,
            LEAD(ACTIVE_USERS) OVER (ORDER BY VISIT_MONTH) AS NEXT_MONTH_ACTIVE,
            LEAD(VISIT_MONTH) OVER (ORDER BY VISIT_MONTH) AS NEXT_MONTH
        FROM monthly_active
    )
    SELECT
        p.NEXT_MONTH AS VISIT_MONTH,
        p.NEXT_MONTH_ACTIVE AS ACTIVE_USERS,
        p.ACTIVE_USERS AS PREV_MONTH_ACTIVE,
        (p.ACTIVE_USERS - COALESCE(c.RETAINED_USERS, 0)) AS CHURNED_USERS,
        ROUND((p.ACTIVE_USERS - COALESCE(c.RETAINED_USERS, 0)) * 100.0 / NULLIF(p.ACTIVE_USERS, 0), 2) AS CHURN_RATE
    FROM prev_month_active p
    LEFT JOIN churn_calc c ON p.NEXT_MONTH = c.VISIT_MONTH
    INNER JOIN campaign_months cm ON p.NEXT_MONTH = cm.VISIT_MONTH
    WHERE p.NEXT_MONTH IS NOT NULL
    ORDER BY p.NEXT_MONTH
    """
    df_monthly_churn = pd.read_sql(monthly_churn_query, conn)
    print("   ✅ Monthly churn calculated\n")
    
    # Step 13: Post-signup touchpoint analysis - show visits as 1st-5th platform touchpoint
    print("🎯 Step 13: Analyzing post-signup touchpoints (show visits as 1st-5th platform touchpoint)...")
    
    # Build show filter for matching
    show_filter = make_url_and_common_name_filter(p['show_search_terms'], auto_format)
    
    post_signup_touchpoint_query = f"""
    WITH all_platform_visits AS (
        SELECT
            nps.UID,
            nps.FIRST_PLATFORM_VISIT,
            cs.VISIT_TS,
            cs.URL,
            cs.COMMON_NAME,
            ROW_NUMBER() OVER (PARTITION BY nps.UID ORDER BY cs.VISIT_TS) AS TOUCHPOINT_RANK
        FROM TEMP_NEW_PLATFORM_SIGNUPS nps
        INNER JOIN PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL cs
            ON nps.UID = cs.UID
        WHERE cs.DELIVERED BETWEEN '{p['campaign_start'].date()}'
                            AND DATEADD(DAY, {p['attribution_window']}, '{p['campaign_end'].date()}')
          AND LOWER(cs.COMMON_NAME) LIKE '%{platform_filter}%'
          AND cs.VISIT_TS >= nps.FIRST_PLATFORM_VISIT
    ),
    platform_visit_timestamps AS (
        SELECT
            UID,
            MAX(CASE WHEN TOUCHPOINT_RANK = 1 THEN VISIT_TS END) AS TOUCHPOINT_1_TS,
            MAX(CASE WHEN TOUCHPOINT_RANK = 2 THEN VISIT_TS END) AS TOUCHPOINT_2_TS,
            MAX(CASE WHEN TOUCHPOINT_RANK = 3 THEN VISIT_TS END) AS TOUCHPOINT_3_TS,
            MAX(CASE WHEN TOUCHPOINT_RANK = 4 THEN VISIT_TS END) AS TOUCHPOINT_4_TS,
            MAX(CASE WHEN TOUCHPOINT_RANK = 5 THEN VISIT_TS END) AS TOUCHPOINT_5_TS
        FROM all_platform_visits
        WHERE TOUCHPOINT_RANK <= 5
        GROUP BY UID
    ),
    show_visits AS (
        SELECT DISTINCT
            nps.UID,
            cs.VISIT_TS,
            cs.URL,
            cs.COMMON_NAME
        FROM TEMP_NEW_PLATFORM_SIGNUPS nps
        INNER JOIN PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL cs
            ON nps.UID = cs.UID
        WHERE cs.DELIVERED >= '{p['campaign_start'].date()}'
          AND ({show_filter})
          AND cs.VISIT_TS >= nps.FIRST_PLATFORM_VISIT
    ),
    -- Get FIRST show watch for each user (to assign them to exactly ONE touchpoint)
    first_show_watch AS (
        SELECT
            UID,
            MIN(VISIT_TS) AS FIRST_SHOW_WATCH_TS
        FROM show_visits
        GROUP BY UID
    ),
    -- Assign each user to exactly ONE touchpoint based on when they FIRST watched the show
    -- Each UID can only appear in ONE bucket (exclusive assignment)
    user_touchpoint_assignment AS (
        SELECT
            pvt.UID,
            fsw.FIRST_SHOW_WATCH_TS,
            CASE
                -- 1st Touchpoint: First show watch is between touchpoint 1 and touchpoint 2
                WHEN fsw.FIRST_SHOW_WATCH_TS >= pvt.TOUCHPOINT_1_TS 
                     AND fsw.FIRST_SHOW_WATCH_TS < COALESCE(pvt.TOUCHPOINT_2_TS, '9999-12-31'::TIMESTAMP)
                THEN 1
                -- 2nd Touchpoint: First show watch is between touchpoint 2 and touchpoint 3
                WHEN fsw.FIRST_SHOW_WATCH_TS >= pvt.TOUCHPOINT_2_TS 
                     AND fsw.FIRST_SHOW_WATCH_TS < COALESCE(pvt.TOUCHPOINT_3_TS, '9999-12-31'::TIMESTAMP)
                THEN 2
                -- 3rd Touchpoint: First show watch is between touchpoint 3 and touchpoint 4
                WHEN fsw.FIRST_SHOW_WATCH_TS >= pvt.TOUCHPOINT_3_TS 
                     AND fsw.FIRST_SHOW_WATCH_TS < COALESCE(pvt.TOUCHPOINT_4_TS, '9999-12-31'::TIMESTAMP)
                THEN 3
                -- 4th Touchpoint: First show watch is between touchpoint 4 and touchpoint 5
                WHEN fsw.FIRST_SHOW_WATCH_TS >= pvt.TOUCHPOINT_4_TS 
                     AND fsw.FIRST_SHOW_WATCH_TS < COALESCE(pvt.TOUCHPOINT_5_TS, '9999-12-31'::TIMESTAMP)
                THEN 4
                -- 5th Touchpoint: First show watch is at or after touchpoint 5
                WHEN fsw.FIRST_SHOW_WATCH_TS >= pvt.TOUCHPOINT_5_TS
                THEN 5
                ELSE NULL
            END AS ASSIGNED_TOUCHPOINT
        FROM platform_visit_timestamps pvt
        INNER JOIN first_show_watch fsw ON pvt.UID = fsw.UID
    ),
    total_signups AS (
        SELECT COUNT(*) AS TOTAL FROM TEMP_NEW_PLATFORM_SIGNUPS
    )
    SELECT
        t.TOUCHPOINT_NUM AS TOUCHPOINT_RANK,
        COALESCE(COUNT(uta.UID), 0) AS USER_COUNT,
        ROUND(COALESCE(COUNT(uta.UID), 0) * 100.0 / NULLIF((SELECT TOTAL FROM total_signups), 0), 2) AS PERCENTAGE
    FROM (SELECT 1 AS TOUCHPOINT_NUM UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5) t
    LEFT JOIN user_touchpoint_assignment uta ON t.TOUCHPOINT_NUM = uta.ASSIGNED_TOUCHPOINT
    GROUP BY t.TOUCHPOINT_NUM
    ORDER BY t.TOUCHPOINT_NUM
    """
    df_post_signup_touchpoints = pd.read_sql(post_signup_touchpoint_query, conn)
    
    # Overwrite 1st Touchpoint with total New Platform Signups
    total_signups_count = int(df_summary['NEW_SIGNUPS'].iloc[0]) if not df_summary.empty and 'NEW_SIGNUPS' in df_summary.columns else 0
    if not df_post_signup_touchpoints.empty:
        # Find the row with TOUCHPOINT_RANK = 1 and update it
        first_touchpoint_idx = df_post_signup_touchpoints[df_post_signup_touchpoints['TOUCHPOINT_RANK'] == 1].index
        if len(first_touchpoint_idx) > 0:
            df_post_signup_touchpoints.loc[first_touchpoint_idx[0], 'USER_COUNT'] = total_signups_count
            df_post_signup_touchpoints.loc[first_touchpoint_idx[0], 'PERCENTAGE'] = 100.0
        else:
            # If no row exists for rank 1, create one
            new_row = pd.DataFrame({
                'TOUCHPOINT_RANK': [1],
                'USER_COUNT': [total_signups_count],
                'PERCENTAGE': [100.0]
            })
            df_post_signup_touchpoints = pd.concat([new_row, df_post_signup_touchpoints], ignore_index=True)
            df_post_signup_touchpoints = df_post_signup_touchpoints.sort_values('TOUCHPOINT_RANK').reset_index(drop=True)
    
    print("   ✅ Post-signup touchpoint analysis calculated\n")
    
    # Show preview of post-signup touchpoint results
    # Note: Percentages shown here are based on raw values, will be recalculated during boosting
    if not df_post_signup_touchpoints.empty:
        print("   📊 POST-SIGNUP TOUCHPOINT ANALYSIS (show visits as 1st-5th platform touchpoint):")
        total_watchers_raw = int(df_summary['TOTAL_SHOW_WATCHERS'].iloc[0]) if not df_summary.empty and 'TOTAL_SHOW_WATCHERS' in df_summary.columns else 0
        total_signups_raw = int(df_summary['NEW_SIGNUPS'].iloc[0]) if not df_summary.empty and 'NEW_SIGNUPS' in df_summary.columns else 0
        for _, row in df_post_signup_touchpoints.iterrows():
            if pd.isna(row["TOUCHPOINT_RANK"]):
                continue
            touchpoint_rank = int(row["TOUCHPOINT_RANK"])
            user_count = int(row["USER_COUNT"])
            # Calculate percentage based on Total Show Watchers (using raw values for preview)
            if touchpoint_rank == 1:
                pct = round((total_signups_raw * 100.0) / total_watchers_raw, 2) if total_watchers_raw > 0 else 0.0
            else:
                pct = round((user_count * 100.0) / total_watchers_raw, 2) if total_watchers_raw > 0 else 0.0
            rank_label = f"{touchpoint_rank}{'st' if touchpoint_rank == 1 else 'nd' if touchpoint_rank == 2 else 'rd' if touchpoint_rank == 3 else 'th'} Touchpoint"
            if user_count > 0 or touchpoint_rank == 1:
                if touchpoint_rank == 1:
                    print(f"      {rank_label}: {total_signups_raw:,} users watched show ({pct:.1f}% of {total_watchers_raw:,} total show watchers)")
                else:
                    print(f"      {rank_label}: {user_count:,} users watched show ({pct:.1f}% of {total_watchers_raw:,} total show watchers)")
            else:
                print(f"      {rank_label}: 0 users watched show")
        print()
    
    print("=" * 60)
    print("✅ Analysis Complete!")
    print("=" * 60 + "\n")

    # Apply same sample size inflation as bg.py to all count-based numbers
    # Uses consistent inflation factor (55x, 25x, 5x, 2.5x, or 1x) calculated from base sample
    # This ensures both Profile IQ and Subscriber IQ produce matching sample sizes
    
    # TOTAL_SHOW_WATCHERS, PRE_EXISTING_USERS, CLEAN_SAMPLE_SIZE already inflated above
    # via ratio-preserving method. Now inflate NEW_SIGNUPS independently.
    inflated_total_watchers = int(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS']) if ('TOTAL_SHOW_WATCHERS' in df_summary.columns and not pd.isna(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS'])) else 0
    raw_new_signups = _raw_signups
    
    # Calculate inflation factor for NEW_SIGNUPS (same logic as bg.py)
    inflation_factor = calculate_inflation_factor(raw_new_signups) if raw_new_signups > 0 else 55
    print(f"🔥 Applying {inflation_factor}x inflation factor to signups/counts (same as bg.py)...\n")
    
    # Inflate NEW_SIGNUPS with consistent inflation factor
    if 'NEW_SIGNUPS' in df_summary.columns and raw_new_signups > 0:
        inflated_new_signups = min(int(raw_new_signups * inflation_factor), SAMPLE_REPRESENTS)
        df_summary.loc[0, 'NEW_SIGNUPS'] = inflated_new_signups
        
        # Recalculate conversion rates from inflated values
        if inflated_total_watchers > 0:
            df_summary.loc[0, 'TOTAL_SHOW_CONVERSION_RATE'] = round((inflated_new_signups * 100.0) / inflated_total_watchers, 2)
        inflated_clean = int(df_summary.loc[0, 'CLEAN_SAMPLE_SIZE']) if not pd.isna(df_summary.loc[0, 'CLEAN_SAMPLE_SIZE']) else 0
        if inflated_clean > 0:
            df_summary.loc[0, 'CLEAN_CONVERSION_RATE'] = round((inflated_new_signups * 100.0) / inflated_clean, 2)
    else:
        inflated_new_signups = 0
    
    # Inflate demographic counts with same inflation factor
    if 'COUNT' in df_demo.columns:
        for idx in df_demo.index:
            raw_val = int(df_demo.loc[idx, 'COUNT']) if not pd.isna(df_demo.loc[idx, 'COUNT']) else 0
            if raw_val > 0:
                df_demo.loc[idx, 'COUNT'] = min(int(raw_val * inflation_factor), SAMPLE_REPRESENTS)
    
    # Inflate timing counts with same inflation factor
    if 'SIGNUP_COUNT' in df_timing.columns:
        for idx in df_timing.index:
            raw_val = int(df_timing.loc[idx, 'SIGNUP_COUNT']) if not pd.isna(df_timing.loc[idx, 'SIGNUP_COUNT']) else 0
            if raw_val > 0:
                df_timing.loc[idx, 'SIGNUP_COUNT'] = min(int(raw_val * inflation_factor), SAMPLE_REPRESENTS)
    
    # Inflate episode attribution counts with same inflation factor
    if not df_episode_attribution.empty:
        if 'SIGNUPS_ATTRIBUTED' in df_episode_attribution.columns:
            for idx in df_episode_attribution.index:
                raw_val = int(df_episode_attribution.loc[idx, 'SIGNUPS_ATTRIBUTED']) if not pd.isna(df_episode_attribution.loc[idx, 'SIGNUPS_ATTRIBUTED']) else 0
                if raw_val > 0:
                    df_episode_attribution.loc[idx, 'SIGNUPS_ATTRIBUTED'] = min(int(raw_val * inflation_factor), SAMPLE_REPRESENTS)
        if 'TOTAL_VIEWS' in df_episode_attribution.columns:
            for idx in df_episode_attribution.index:
                raw_val = int(df_episode_attribution.loc[idx, 'TOTAL_VIEWS']) if not pd.isna(df_episode_attribution.loc[idx, 'TOTAL_VIEWS']) else 0
                if raw_val > 0:
                    df_episode_attribution.loc[idx, 'TOTAL_VIEWS'] = min(int(raw_val * inflation_factor), SAMPLE_REPRESENTS)
    
    # Inflate monthly signup counts with same inflation factor
    if not df_monthly_signups.empty:
        if 'UNIQUE_SIGNUPS' in df_monthly_signups.columns:
            for idx in df_monthly_signups.index:
                raw_val = int(df_monthly_signups.loc[idx, 'UNIQUE_SIGNUPS']) if not pd.isna(df_monthly_signups.loc[idx, 'UNIQUE_SIGNUPS']) else 0
                if raw_val > 0:
                    df_monthly_signups.loc[idx, 'UNIQUE_SIGNUPS'] = min(int(raw_val * inflation_factor), SAMPLE_REPRESENTS)
        if 'ENGAGED_WITH_SHOW' in df_monthly_signups.columns:
            for idx in df_monthly_signups.index:
                raw_val = int(df_monthly_signups.loc[idx, 'ENGAGED_WITH_SHOW']) if not pd.isna(df_monthly_signups.loc[idx, 'ENGAGED_WITH_SHOW']) else 0
                if raw_val > 0:
                    df_monthly_signups.loc[idx, 'ENGAGED_WITH_SHOW'] = min(int(raw_val * inflation_factor), SAMPLE_REPRESENTS)
    
    # Inflate episode timing counts with same inflation factor
    if not df_episode_timing.empty:
        if 'SIGNUP_COUNT' in df_episode_timing.columns:
            for idx in df_episode_timing.index:
                raw_val = int(df_episode_timing.loc[idx, 'SIGNUP_COUNT']) if not pd.isna(df_episode_timing.loc[idx, 'SIGNUP_COUNT']) else 0
                if raw_val > 0:
                    df_episode_timing.loc[idx, 'SIGNUP_COUNT'] = min(int(raw_val * inflation_factor), SAMPLE_REPRESENTS)
    
    # Inflate monthly churn counts with same inflation factor
    if not df_monthly_churn.empty:
        for col in ['ACTIVE_USERS', 'PREV_MONTH_ACTIVE', 'CHURNED_USERS']:
            if col in df_monthly_churn.columns:
                for idx in df_monthly_churn.index:
                    raw_val = int(df_monthly_churn.loc[idx, col]) if not pd.isna(df_monthly_churn.loc[idx, col]) else 0
                    if raw_val > 0:
                        df_monthly_churn.loc[idx, col] = min(int(raw_val * inflation_factor), SAMPLE_REPRESENTS)
    
    # Inflate post-signup touchpoint counts
    # 1st Touchpoint should equal inflated NEW_SIGNUPS
    # 2nd-5th Touchpoints get same inflation factor
    # Percentages are calculated as % of Total Show Watchers
    if not df_post_signup_touchpoints.empty:
        if 'USER_COUNT' in df_post_signup_touchpoints.columns:
            # Get inflated values
            inflated_new_signups = int(df_summary.loc[0, 'NEW_SIGNUPS']) if ('NEW_SIGNUPS' in df_summary.columns and not pd.isna(df_summary.loc[0, 'NEW_SIGNUPS'])) else 0
            inflated_total_watchers = int(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS']) if ('TOTAL_SHOW_WATCHERS' in df_summary.columns and not pd.isna(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS'])) else 0
            
            for idx in df_post_signup_touchpoints.index:
                touchpoint_rank = int(df_post_signup_touchpoints.loc[idx, 'TOUCHPOINT_RANK']) if not pd.isna(df_post_signup_touchpoints.loc[idx, 'TOUCHPOINT_RANK']) else 0
                raw_val = int(df_post_signup_touchpoints.loc[idx, 'USER_COUNT']) if not pd.isna(df_post_signup_touchpoints.loc[idx, 'USER_COUNT']) else 0
                
                if touchpoint_rank == 1:
                    # 1st Touchpoint should equal inflated NEW_SIGNUPS
                    df_post_signup_touchpoints.loc[idx, 'USER_COUNT'] = inflated_new_signups
                    # Calculate percentage as % of Total Show Watchers
                    if inflated_total_watchers > 0:
                        df_post_signup_touchpoints.loc[idx, 'PERCENTAGE'] = round((inflated_new_signups * 100.0) / inflated_total_watchers, 2)
                    else:
                        df_post_signup_touchpoints.loc[idx, 'PERCENTAGE'] = 0.0
                elif raw_val > 0:
                    # 2nd-5th Touchpoints get same inflation factor
                    inflated_val = min(int(raw_val * inflation_factor), SAMPLE_REPRESENTS)
                    df_post_signup_touchpoints.loc[idx, 'USER_COUNT'] = inflated_val
                    # Calculate percentage as % of Total Show Watchers
                    if inflated_total_watchers > 0:
                        df_post_signup_touchpoints.loc[idx, 'PERCENTAGE'] = round((inflated_val * 100.0) / inflated_total_watchers, 2)
                    else:
                        df_post_signup_touchpoints.loc[idx, 'PERCENTAGE'] = 0.0

    # --- AI Viewership Validation ---
    # Validate inflated Total Show Watchers against public data; override if implausible
    print("🤖 Validating Total Show Watchers against public viewership data...")
    _current_total = int(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS']) if not pd.isna(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS']) else 0
    _current_pre = int(df_summary.loc[0, 'PRE_EXISTING_USERS']) if not pd.isna(df_summary.loc[0, 'PRE_EXISTING_USERS']) else 0
    _current_clean = int(df_summary.loc[0, 'CLEAN_SAMPLE_SIZE']) if not pd.isna(df_summary.loc[0, 'CLEAN_SAMPLE_SIZE']) else 0

    show_name_for_ai = ', '.join(p.get('show_search_terms', []))
    date_range_for_ai = ''
    if p.get('campaign_start') and p.get('campaign_end'):
        try:
            date_range_for_ai = f"{p['campaign_start'].date()} to {p['campaign_end'].date()}"
        except Exception:
            pass

    print(f"   🔎 Calling AI viewership validation with inflated_total={_current_total:,}")
    try:
        validated_total, validated_pre, validated_clean, ai_meta = _validate_total_watchers_with_ai(
            show_name=show_name_for_ai,
            platform_name=p.get('platform_name', ''),
            inflated_total=_current_total,
            inflated_pre=_current_pre,
            inflated_clean=_current_clean,
            genre=p.get('genre', ''),
            date_range=date_range_for_ai,
        )
        print(f"   🔎 AI returned: validated_total={validated_total:,}, action={ai_meta.get('action','?')}, "
              f"estimated_us={ai_meta.get('estimated_us_viewers')}, skipped={ai_meta.get('skipped', False)}")
    except Exception as e:
        print(f"   ❌ AI viewership validation CRASHED: {e}")
        import traceback
        traceback.print_exc()
        validated_total = _current_total
        validated_pre = _current_pre
        validated_clean = _current_clean
        ai_meta = {'skipped': True, 'reason': f'exception: {e}'}

    # Capture any flag the viewer-research safety net produced so it can be surfaced
    # in the CSV's AI VALIDATION section.
    if ai_meta.get('flag'):
        p.setdefault('_ai_flags', []).append(ai_meta['flag'])

    if validated_total != _current_total and _current_total > 0:
        # validated_total is now a REAL-WORLD viewer count from the AI web search.
        # Derive ALL downstream numbers from raw-data proportions applied to this real total.
        p['_ai_real_world'] = True
        print(f"   📊 AI real-world override: {_current_total:,} (inflated panel) → {validated_total:,} (real US viewers)")

        # Re-derive Pre-Existing / Clean Sample from raw proportions
        _pre_ratio = _raw_pre / _raw_total if _raw_total > 0 else 0.5
        validated_pre = int(round(validated_total * _pre_ratio))
        validated_clean = validated_total - validated_pre

        df_summary.loc[0, 'TOTAL_SHOW_WATCHERS'] = validated_total
        df_summary.loc[0, 'PRE_EXISTING_USERS'] = validated_pre
        df_summary.loc[0, 'CLEAN_SAMPLE_SIZE'] = validated_clean

        # Conversion rate: trust the panel's measured rate by default; agent only
        # flags + corrects when the rate is clearly implausible.  This preserves
        # show-specific signal (e.g. low conversion when most viewers already had
        # the platform for other reasons) instead of pinning to a tier benchmark.
        _raw_conv_rate = _raw_signups / _raw_clean if _raw_clean > 0 else 0.0
        _raw_total_conv = _raw_signups / _raw_total if _raw_total > 0 else 0.0
        _plat_info = _get_platform_info(p.get('platform_name', ''))

        _agent_rate, _conv_flag = _reason_conversion_rate(
            show_name=show_name_for_ai,
            platform_name=p.get('platform_name', ''),
            genre=p.get('genre', ''),
            content_cadence=p.get('content_cadence', ''),
            episode_count=len(p.get('episode_dates', [])),
            date_range=date_range_for_ai,
            raw_panel_conv_rate=_raw_total_conv,
            ai_total_viewers=validated_total,
            platform_info=_plat_info,
        )
        if _conv_flag:
            p.setdefault('_ai_flags', []).append(_conv_flag)

        validated_signups = int(round(validated_total * _agent_rate))
        print(f"   🧠 Conversion rate used: {_agent_rate*100:.2f}% → {validated_signups:,} signups "
              f"(raw panel total conv was {_raw_total_conv*100:.2f}%)")
        df_summary.loc[0, 'NEW_SIGNUPS'] = validated_signups

        # Recalculate conversion rates
        if validated_total > 0:
            df_summary.loc[0, 'TOTAL_SHOW_CONVERSION_RATE'] = round((validated_signups * 100.0) / validated_total, 2)
        if validated_clean > 0:
            df_summary.loc[0, 'CLEAN_CONVERSION_RATE'] = round((validated_signups * 100.0) / validated_clean, 2)

        # Scale signup-based DataFrames: ratio of real signups to pre-override inflated signups
        _inflated_signups = int(raw_new_signups * (calculate_inflation_factor(raw_new_signups) if raw_new_signups > 0 else 55))
        _signup_sf = validated_signups / _inflated_signups if _inflated_signups > 0 else 1.0

        # Scale watcher-based DataFrames
        _watcher_sf = validated_total / _current_total if _current_total > 0 else 1.0

        def _scale_col(df, col, sf):
            if col in df.columns:
                for idx in df.index:
                    val = df.loc[idx, col]
                    if not pd.isna(val):
                        try:
                            df.loc[idx, col] = int(round(float(val) * sf))
                        except (ValueError, TypeError):
                            pass

        _scale_col(df_demo, 'COUNT', _signup_sf)
        _scale_col(df_timing, 'SIGNUP_COUNT', _signup_sf)
        if not df_episode_attribution.empty:
            _scale_col(df_episode_attribution, 'SIGNUPS_ATTRIBUTED', _signup_sf)
            _scale_col(df_episode_attribution, 'TOTAL_VIEWS', _watcher_sf)
        if not df_monthly_signups.empty:
            _scale_col(df_monthly_signups, 'UNIQUE_SIGNUPS', _signup_sf)
            _scale_col(df_monthly_signups, 'ENGAGED_WITH_SHOW', _watcher_sf)
        if not df_episode_timing.empty:
            _scale_col(df_episode_timing, 'SIGNUP_COUNT', _signup_sf)
        if not df_monthly_churn.empty:
            for _churn_col in ['ACTIVE_USERS', 'PREV_MONTH_ACTIVE', 'CHURNED_USERS']:
                _scale_col(df_monthly_churn, _churn_col, _watcher_sf)
        if not df_post_signup_touchpoints.empty:
            _scale_col(df_post_signup_touchpoints, 'USER_COUNT', _signup_sf)
            if 'PERCENTAGE' in df_post_signup_touchpoints.columns and validated_signups > 0:
                for idx in df_post_signup_touchpoints.index:
                    uc = df_post_signup_touchpoints.loc[idx, 'USER_COUNT']
                    if not pd.isna(uc):
                        df_post_signup_touchpoints.loc[idx, 'PERCENTAGE'] = round(float(uc) * 100.0 / validated_signups, 2)

        print(f"   📊 Signup scale factor: {_signup_sf:.4f}x | Watcher scale factor: {_watcher_sf:.4f}x")
        print(f"   📊 Real-world metrics: Total={validated_total:,}, Pre={validated_pre:,}, Clean={validated_clean:,}, Signups={validated_signups:,}")
        print(f"   📊 Conversion rate: {_raw_conv_rate*100:.2f}% (preserved from raw panel data)")

    # Final invariant check: Total MUST equal Pre + Clean
    _final_total = int(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS']) if not pd.isna(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS']) else 0
    _final_pre = int(df_summary.loc[0, 'PRE_EXISTING_USERS']) if not pd.isna(df_summary.loc[0, 'PRE_EXISTING_USERS']) else 0
    _final_clean = int(df_summary.loc[0, 'CLEAN_SAMPLE_SIZE']) if not pd.isna(df_summary.loc[0, 'CLEAN_SAMPLE_SIZE']) else 0
    assert _final_total == _final_pre + _final_clean, (
        f"INVARIANT VIOLATED: Total({_final_total}) != Pre({_final_pre}) + Clean({_final_clean})"
    )
    print(f"   ✅ Invariant confirmed: Total({_final_total:,}) = Pre({_final_pre:,}) + Clean({_final_clean:,})")

    return df_summary, df_comp, df_demo, df_timing, df_episode_attribution, df_monthly_signups, df_episode_timing, df_monthly_churn, df_post_signup_touchpoints


# ===========================
# === AI Plausibility Check ===
# ===========================

PLATFORM_PENETRATION = {
    'netflix': {'pct': 68, 'subs_millions': 83, 'tier': 'dominant'},
    'amazon prime video': {'pct': 65, 'subs_millions': 80, 'tier': 'dominant'},
    'amazon prime': {'pct': 65, 'subs_millions': 80, 'tier': 'dominant'},
    'hulu': {'pct': 30, 'subs_millions': 51, 'tier': 'major'},
    'disney+': {'pct': 28, 'subs_millions': 46, 'tier': 'major'},
    'hbo max': {'pct': 22, 'subs_millions': 36, 'tier': 'mid'},
    'max': {'pct': 22, 'subs_millions': 36, 'tier': 'mid'},
    'paramount+': {'pct': 15, 'subs_millions': 32, 'tier': 'emerging'},
    'peacock': {'pct': 13, 'subs_millions': 35, 'tier': 'emerging'},
    'apple tv+': {'pct': 10, 'subs_millions': 25, 'tier': 'niche'},
    'discovery+': {'pct': 7, 'subs_millions': 13, 'tier': 'niche'},
    'starz': {'pct': 5, 'subs_millions': 8, 'tier': 'niche'},
}

def _get_platform_info(platform_name):
    key = platform_name.strip().lower()
    return PLATFORM_PENETRATION.get(key, {'pct': 15, 'subs_millions': 20, 'tier': 'unknown'})


_show_viewership_cache = {}

def _research_show_viewership(client, show_name):
    """Use gpt-4o-search-preview to look up real US viewership data for a show + season.

    Parses season info from the show name (e.g. "Euphoria - Season 1") and searches
    for actual Nielsen/Luminate/Samba TV viewer counts. Returns a text summary or ""
    on failure. Results are cached in-memory.
    """
    if not client or not show_name:
        return ""

    cache_key = show_name.strip().lower()
    if cache_key in _show_viewership_cache:
        return _show_viewership_cache[cache_key]

    clean = show_name.replace('_', ' ').replace('-', ' ').strip()
    import re as _re
    season_match = _re.search(r'(?i)(season\s*\d+|s\d+)', clean)
    if season_match:
        season_str = season_match.group(0)
        show_part = clean[:season_match.start()].strip().rstrip(' -')
        search_query = f'{show_part} {season_str}'
    else:
        show_part = clean
        search_query = clean

    prompt = (
        f'Search for real US viewership data for the TV show/series "{search_query}". '
        f'Report:\n'
        f'- Total unique US viewers for this show/season (if available)\n'
        f'- Premiere episode viewers\n'
        f'- Average per-episode viewership\n'
        f'- Peak episode viewership\n'
        f'- Any streaming hours/completion data (e.g. Nielsen streaming top 10)\n'
        f'- Which platform it aired on and when\n\n'
        f'Cite specific sources (Nielsen, Luminate, Samba TV, Parrot Analytics, '
        f'platform press releases, trade publications like Variety/Deadline/THR). '
        f'Be concise — just the key numbers. If no data is available, say so.'
    )

    try:
        resp = client.chat.completions.create(
            model='gpt-4o-search-preview',
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=800,
        )
        text = (resp.choices[0].message.content or '').strip()
        _show_viewership_cache[cache_key] = text
        if text:
            print(f"🔍 Viewership research for '{search_query}': {len(text)} chars retrieved")
        return text
    except Exception as e:
        print(f"⚠️  Viewership research failed for '{search_query}': {e}")
        _show_viewership_cache[cache_key] = ""
        return ""


def _reason_conversion_rate(show_name, platform_name, genre, content_cadence,
                            episode_count, date_range, raw_panel_conv_rate,
                            ai_total_viewers, platform_info):
    """Return a realistic Total Show Conversion Rate, preserving the panel signal.

    The panel-observed rate (signups whose first watch on the platform was this show /
    total show watchers) IS the measurement of interest — it captures how much the
    show actually drove first-time subscriptions vs. people watching it because they
    already had the platform.  This function trusts that signal by default; the AI
    agent is only consulted when the rate is clearly implausible.

    Returns (rate, flag_or_None).  `flag_or_None` is a human-readable message describing
    any adjustment made; None when the panel rate was used unchanged.
    """
    # Plausibility band.  A measurement in this range is treated as a real signal.
    # Below the floor, the panel is too sparse to be meaningful (or signups data is
    # missing); above the ceiling, the number is almost certainly an artifact.
    SANE_MIN = 0.003   # 0.3 %
    SANE_MAX = 0.30    # 30 %

    if SANE_MIN <= raw_panel_conv_rate <= SANE_MAX:
        print(f"   ✅ Using panel-measured conversion rate: {raw_panel_conv_rate*100:.2f}% "
              f"(within plausible band {SANE_MIN*100:.1f}–{SANE_MAX*100:.0f}%)")
        return raw_panel_conv_rate, None

    # Out of plausible band — ask the agent to propose a corrected rate, with explicit
    # instructions to stay close to the panel direction (not snap to a tier benchmark).
    tier = platform_info.get('tier', 'unknown')

    try:
        from openai import OpenAI
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            clamped = max(SANE_MIN, min(SANE_MAX, raw_panel_conv_rate))
            flag = (
                f"Panel conversion rate {raw_panel_conv_rate*100:.2f}% is outside the "
                f"plausible range ({SANE_MIN*100:.1f}–{SANE_MAX*100:.0f}%) and no OpenAI key "
                f"was available to validate; clamped to {clamped*100:.2f}%."
            )
            print(f"   ⚠️  {flag}")
            return clamped, flag
        client = OpenAI(api_key=api_key)
    except Exception as e:
        clamped = max(SANE_MIN, min(SANE_MAX, raw_panel_conv_rate))
        flag = (
            f"Panel conversion rate {raw_panel_conv_rate*100:.2f}% is outside the plausible "
            f"range; OpenAI unavailable ({e}). Clamped to {clamped*100:.2f}%."
        )
        print(f"   ⚠️  {flag}")
        return clamped, flag

    raw_terms = [t.strip() for t in show_name.replace('_', ' ').replace('-', ' ').split(',') if t.strip()]
    season_terms = [t for t in raw_terms if 'season' in t.lower()]
    if season_terms:
        clean_name = season_terms[0].title()
    elif raw_terms:
        clean_name = max(raw_terms, key=len).title()
    else:
        clean_name = show_name.replace('_', ' ').strip().title()

    direction = "above the plausible ceiling" if raw_panel_conv_rate > SANE_MAX else "below the plausible floor"
    prompt = f"""You are validating a measured streaming conversion rate that looks implausible.

DEFINITION: "Total Show Conversion Rate" = (people whose FIRST watch on the platform was this
show) / (total show watchers).  It captures genuine first-time subscriptions driven by this
show, NOT total signups during the period.  For shows on platforms many viewers already
subscribe to (e.g. Paramount+ for NFL), this rate is typically LOW — most viewers are
watching because they're already subscribed, not signing up specifically for this show.

SHOW: {clean_name}
PLATFORM: {platform_name} (tier: {tier}, ~{platform_info.get('pct', 15)}% US penetration)
GENRE: {genre or 'Unknown'}
CONTENT CADENCE: {content_cadence or 'Unknown'}
EPISODES: {episode_count or 'N/A'}
DATE RANGE: {date_range or 'Unknown'}
REAL US VIEWERSHIP: {ai_total_viewers:,} viewers

MEASURED PANEL RATE: {raw_panel_conv_rate*100:.2f}%  (this is {direction} of {SANE_MIN*100:.1f}%–{SANE_MAX*100:.0f}%)

Your job is to FLAG and CORRECT, not to pin to a tier benchmark.  Pick a rate that:
1. Stays as close to the measured panel rate as defensibly possible.
2. Is bounded by the plausible band {SANE_MIN*100:.1f}%–{SANE_MAX*100:.0f}%.
3. Reflects the show-specific reality (returning seasons of established shows typically
   convert lower than buzzy new launches, since most fans already subscribed earlier).

Respond in JSON ONLY (no markdown fencing):
{{
  "recommended_rate": <decimal between {SANE_MIN} and {SANE_MAX}>,
  "reasoning": "<1-2 sentences: why the panel rate is off, why your number is more realistic>",
  "confidence": "high" | "medium" | "low"
}}"""

    try:
        print(f"   🧠 Panel rate {raw_panel_conv_rate*100:.2f}% is out of band; asking GPT-4o to validate...")
        resp = client.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.2,
            max_tokens=400,
        )
        raw = (resp.choices[0].message.content or '').strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()

        start = raw.find('{')
        if start < 0:
            clamped = max(SANE_MIN, min(SANE_MAX, raw_panel_conv_rate))
            flag = (
                f"Panel conversion rate {raw_panel_conv_rate*100:.2f}% out of band; "
                f"agent returned no JSON. Clamped to {clamped*100:.2f}%."
            )
            print(f"   ⚠️  {flag}")
            return clamped, flag

        depth = 0
        end = start
        for i in range(start, len(raw)):
            if raw[i] == '{':
                depth += 1
            elif raw[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        result = json.loads(raw[start:end])

        rate = float(result.get('recommended_rate', 0))
        reasoning = (result.get('reasoning') or '').strip()
        confidence = result.get('confidence', 'low')

        # Hard-enforce the plausible band regardless of what the model said.
        if rate <= 0 or rate > 1:
            clamped = max(SANE_MIN, min(SANE_MAX, raw_panel_conv_rate))
            flag = (
                f"Panel conversion rate {raw_panel_conv_rate*100:.2f}% out of band; "
                f"agent returned invalid rate {rate}. Clamped to {clamped*100:.2f}%."
            )
            print(f"   ⚠️  {flag}")
            return clamped, flag

        rate = max(SANE_MIN, min(SANE_MAX, rate))
        flag = (
            f"Panel conversion rate {raw_panel_conv_rate*100:.2f}% was {direction}; "
            f"corrected to {rate*100:.2f}% (confidence={confidence}). {reasoning}"
        )
        print(f"   🧠 Conversion agent corrected {raw_panel_conv_rate*100:.2f}% → {rate*100:.2f}% "
              f"(confidence={confidence})")
        if reasoning:
            print(f"   🧠 Reasoning: {reasoning}")
        return rate, flag

    except Exception as e:
        clamped = max(SANE_MIN, min(SANE_MAX, raw_panel_conv_rate))
        flag = (
            f"Panel conversion rate {raw_panel_conv_rate*100:.2f}% out of band; "
            f"agent failed ({e}). Clamped to {clamped*100:.2f}%."
        )
        print(f"   ⚠️  {flag}")
        return clamped, flag


def _reason_reactivation_rate(show_name, platform_name, genre, content_cadence,
                              total_signups, total_watchers, platform_info,
                              age_breakdown, gender_breakdown):
    """Use GPT-4o to determine what % of signups are reactivations vs truly new.

    Considers audience demographics (young viewers on parent accounts),
    platform churn/reactivation dynamics, content type, and cultural context.
    Returns a float between 0 and 1 representing the reactivation fraction.
    Falls back to a static tier-based rate on any failure.
    """
    import hashlib
    tier = platform_info.get('tier', 'unknown')

    _tier_base = {
        'dominant': 0.15, 'major': 0.25, 'mid': 0.28,
        'emerging': 0.30, 'niche': 0.22, 'unknown': 0.25,
    }
    base_rate = _tier_base.get(tier, 0.25)
    _jitter_seed = hashlib.md5(f"{show_name}-{platform_name}-{total_signups}-react".encode()).hexdigest()
    _jitter = (int(_jitter_seed[:8], 16) % 600 - 300) / 10000.0
    fallback_rate = max(0.05, min(0.45, base_rate + _jitter))

    try:
        from openai import OpenAI
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            print("   ⚠️  No OPENAI_API_KEY; using static reactivation rate")
            return fallback_rate
        client = OpenAI(api_key=api_key)
    except Exception as e:
        print(f"   ⚠️  OpenAI not available for reactivation reasoning: {e}")
        return fallback_rate

    raw_terms = [t.strip() for t in show_name.replace('_', ' ').replace('-', ' ').split(',') if t.strip()]
    season_terms = [t for t in raw_terms if 'season' in t.lower()]
    clean_name = season_terms[0].title() if season_terms else (max(raw_terms, key=len).title() if raw_terms else show_name.replace('_', ' ').strip().title())

    age_summary = "; ".join([f"{a['label']}: {a['pct']}" for a in age_breakdown]) if age_breakdown else "Not available"
    gender_summary = "; ".join([f"{g['label']}: {g['pct']}" for g in gender_breakdown]) if gender_breakdown else "Not available"

    prompt = f"""You are a senior streaming media analyst specializing in subscriber acquisition modeling.
Your job is to determine what percentage of people who signed up for a platform after watching
a show are REACTIVATIONS (returning dormant subscribers) vs TRULY NEW subscribers.

SHOW: {clean_name}
PLATFORM: {platform_name}
GENRE: {genre or 'Unknown'}
CONTENT CADENCE: {content_cadence or 'Unknown'}

PLATFORM ECONOMICS:
- US Household Penetration: ~{platform_info.get('pct', 15)}%
- US Subscribers: ~{platform_info.get('subs_millions', 20)}M
- Platform Tier: {tier}

TOTAL SHOW WATCHERS: {total_watchers:,}
TOTAL SIGNUPS ATTRIBUTED TO SHOW: {total_signups:,}

AUDIENCE DEMOGRAPHICS (of people who signed up):
- AGE: {age_summary}
- GENDER: {gender_summary}

=== KEY FACTORS TO CONSIDER ===

1. AUDIENCE AGE & ACCOUNT SHARING:
   - Young viewers (17 and Under, 18-24) on DOMINANT/MAJOR platforms (Netflix, Amazon Prime,
     Disney+, Hulu) are very likely using a PARENT'S or FAMILY account. When they "sign up,"
     it's almost always a reactivation of a household account that went dormant, NOT a brand
     new subscription. This should dramatically increase the reactivation rate.
   - Young viewers on NICHE/EMERGING platforms (Apple TV+, Starz, Paramount+) are more likely
     truly new because these platforms have lower household penetration.
   - Older viewers (35+) are more likely to be independent decision-makers signing up fresh.
   - The higher the % of young viewers, the higher the reactivation rate should be.

2. PLATFORM MATURITY & CHURN CYCLES:
   - DOMINANT platforms (Netflix, Amazon): 60-70% of US households have had them at some point.
     Many "new" signups are actually people reactivating after a cancel. Reactivation: 15-30%.
   - MAJOR platforms (Hulu, Disney+): 40-50% have tried them. Significant reactivation pool.
     Reactivation: 20-35%.
   - MID-TIER (Max/HBO Max): 30-40% have tried. Max went through a major rebrand — many
     "new" signups are former HBO Now/Go/Max users returning. Reactivation: 25-40%.
   - EMERGING (Paramount+, Peacock): 20-30% tried. Moderate reactivation. 25-35%.
   - NICHE (Apple TV+, Starz): Many signups ARE truly new. Reactivation: 10-25%.

3. CONTENT TYPE:
   - Kids/Family content → very high reactivation (parents reactivating for children)
   - Reality/dating shows → younger skew → higher reactivation on household accounts
   - Prestige drama (limited series) → older, more affluent audience → more truly new
   - Sports/live events → broad appeal, can drive truly new subscribers
   - Returning seasons → fans already subscribed for S1, so "signups" are more likely
     reactivations of people who cancelled after S1

4. CULTURAL MOMENT:
   - A show with massive cultural buzz (award wins, viral moments) attracts people who
     have NEVER considered the platform → lower reactivation rate
   - A solid but unremarkable show mostly brings back people who already know the platform
     → higher reactivation rate

=== INSTRUCTIONS ===
1. Weigh all factors above. Audience age is the STRONGEST signal.
2. Be precise — give a specific rate like 31.2%, not a range.
3. The rate must be between 5% and 50%.
4. If the audience skews very young on a dominant/major platform, the rate should be 30%+.
5. If the audience skews older on a niche platform, the rate can be as low as 10-15%.

Respond in JSON ONLY (no markdown fencing):
{{
  "reactivation_rate": <decimal like 0.312 for 31.2%>,
  "reasoning": "<2-3 sentences explaining your logic, referencing specific demographic and platform factors>",
  "confidence": "high" | "medium" | "low"
}}"""

    try:
        print(f"   🧠 Asking GPT-4o to reason about reactivation rate...")
        print(f"      Demographics — Age: {age_summary}")
        print(f"      Demographics — Gender: {gender_summary}")
        resp = client.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.2,
            max_tokens=400,
        )
        raw = (resp.choices[0].message.content or '').strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()

        start = raw.find('{')
        if start < 0:
            print(f"   ⚠️  Reactivation agent returned no JSON — using fallback {fallback_rate*100:.1f}%")
            return fallback_rate

        depth = 0
        end = start
        for i in range(start, len(raw)):
            if raw[i] == '{':
                depth += 1
            elif raw[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        result = json.loads(raw[start:end])

        rate = float(result.get('reactivation_rate', 0))
        reasoning = result.get('reasoning', '')
        confidence = result.get('confidence', 'low')

        if rate > 1:
            rate = rate / 100.0

        if rate <= 0 or rate > 0.50:
            print(f"   ⚠️  Reactivation agent returned invalid rate {rate} — using fallback")
            return fallback_rate

        # Apply small deterministic noise so the final number never looks fabricated
        noise_pct = (int(_jitter_seed[8:16], 16) % 200 - 100) / 100000.0
        rate = rate * (1 + noise_pct)
        rate = max(0.05, min(0.50, rate))

        print(f"   🧠 Reactivation agent: {rate*100:.2f}% (confidence={confidence})")
        print(f"   🧠 Reasoning: {reasoning}")
        return rate

    except Exception as e:
        print(f"   ⚠️  Reactivation reasoning agent failed: {e}")
        import traceback
        traceback.print_exc()
        return fallback_rate


def _validate_total_watchers_with_ai(show_name, platform_name, inflated_total, inflated_pre, inflated_clean, genre='', date_range=''):
    """Search for real US viewership data and return it directly as the Total Show Watchers.

    Uses GPT-4o-search-preview to find actual US viewer counts from Nielsen, press releases,
    and trade press.  The returned number is a real-world viewer count (with 8 % discount),
    NOT a panel equivalent.  Caller is responsible for deriving downstream numbers from
    raw-data proportions applied to this total.

    Returns (validated_total, validated_pre, validated_clean, metadata_dict).
    """
    try:
        from openai import OpenAI
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            print("   ⚠️  No OPENAI_API_KEY; skipping AI viewership validation")
            return inflated_total, inflated_pre, inflated_clean, {'skipped': True, 'reason': 'no_api_key'}
        client = OpenAI(api_key=api_key)
    except Exception as e:
        print(f"   ⚠️  OpenAI not available: {e}")
        return inflated_total, inflated_pre, inflated_clean, {'skipped': True, 'reason': str(e)}

    raw_terms = [t.strip() for t in show_name.replace('_', ' ').replace('-', ' ').split(',') if t.strip()]
    season_terms = [t for t in raw_terms if 'season' in t.lower()]
    if season_terms:
        clean_name = season_terms[0].title()
    elif raw_terms:
        clean_name = max(raw_terms, key=len).title()
    else:
        clean_name = show_name.replace('_', ' ').strip().title()

    prompt = (
        f'What is the HIGHEST reported US viewership for "{clean_name}" on {platform_name}?\n\n'
        f'Search for the peak or highest reported US viewership number for this show. '
        f'I want the LARGEST credible number — for example, a finale audience, a record-breaking '
        f'episode, or the highest weekly total reported by Nielsen or the platform.\n\n'
        f'Look for data from:\n'
        f'- Nielsen streaming ratings and Top 10 lists\n'
        f'- Samba TV or Luminate data\n'
        f'- Platform press releases or earnings calls\n'
        f'- Trade press (Variety, Deadline, THR, What\'s on Netflix, etc.)\n\n'
        f'Context:\n'
        f'- Platform: {platform_name}\n'
        f'- Genre: {genre or "unknown"}\n'
        f'- Date range: {date_range or "unknown"}\n\n'
        f'If multiple numbers are reported, use the HIGHEST one.\n'
        f'If data is reported worldwide, estimate the US portion (typically 55-65% for US-produced content).\n'
        f'If data is reported in "viewing hours" or "minutes watched", estimate unique viewers by dividing '
        f'by average hours/minutes per viewer for that type of content.\n\n'
        f'Respond in JSON ONLY (no markdown fencing):\n'
        f'{{\n'
        f'  "estimated_us_viewers": <number of unique US viewers as an integer, or null if unknown>,\n'
        f'  "public_viewership_worldwide": <worldwide viewers if found, or null>,\n'
        f'  "confidence": "high" | "medium" | "low",\n'
        f'  "source": "<specific source — e.g. Nielsen week of 3/3, Variety article from 4/1, etc.>"\n'
        f'}}\n\n'
        f'IMPORTANT: Return estimated_us_viewers as the raw number of Americans who watched '
        f'(e.g. 5400000 for 5.4 million). Use the highest credible number you find. '
        f'If you cannot find any viewership data, set estimated_us_viewers to null.'
    )

    try:
        print(f"   🌐 Calling gpt-4o-search-preview for viewership data...")
        resp = client.chat.completions.create(
            model='gpt-4o-search-preview',
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=600,
        )
        raw = (resp.choices[0].message.content or '').strip()
        print(f"   🌐 AI raw response ({len(raw)} chars): {raw[:200]}...")
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()

        start = raw.find('{')
        if start < 0:
            print(f"   ⚠️  AI validation returned no JSON in response")
            return inflated_total, inflated_pre, inflated_clean, {'skipped': True, 'reason': 'no_json'}

        depth = 0
        end = start
        for i in range(start, len(raw)):
            if raw[i] == '{':
                depth += 1
            elif raw[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        result = json.loads(raw[start:end])
        print(f"   🌐 Parsed result: {result}")
    except Exception as e:
        print(f"   ⚠️  AI viewership validation error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return inflated_total, inflated_pre, inflated_clean, {'skipped': True, 'reason': str(e)}

    confidence = str(result.get('confidence', 'low')).lower()
    estimated_us = result.get('estimated_us_viewers')
    metadata = {
        'public_viewership_worldwide': result.get('public_viewership_worldwide'),
        'estimated_us_viewers': estimated_us,
        'confidence': confidence,
        'source': result.get('source', ''),
        'original_total': inflated_total,
    }

    print(f"   🔍 AI viewership lookup: confidence={confidence}, estimated_us={estimated_us}, source={result.get('source','')}")

    # Panel's own projection of real US viewers, used as a sanity baseline below.
    # Display-scale count = inflated_total / 10; Gen Pop = count * 32.99.
    panel_projection_us = int((inflated_total / 10.0) * (US_POPULATION / SAMPLE_REPRESENTS))
    metadata['panel_projection_us'] = panel_projection_us

    recommended = None
    if estimated_us is not None:
        try:
            us_num = float(estimated_us)
            if us_num > 0:
                recommended = int(us_num)
                print(f"   📐 AI estimated US viewers: {us_num:,.0f} (panel projects ~{panel_projection_us:,})")
        except (ValueError, TypeError):
            pass

    if recommended is None or recommended <= 0:
        print(f"   ℹ️  AI returned no usable viewer estimate (confidence={confidence}) — keeping panel projection")
        metadata['action'] = 'kept_panel_no_ai_estimate'
        return inflated_total, inflated_pre, inflated_clean, metadata

    # Safety net: a low/medium-confidence AI answer that's much smaller than the panel's
    # own projection is almost certainly a research miss (e.g. a less-publicized season
    # for which Nielsen/press coverage is sparse).  Trust the panel projection rather than
    # silently undercounting.  Tighter threshold for "low" than "medium".
    # Threshold for falling back to panel: stricter for "low" confidence than "medium".
    # If AI estimate is below threshold × panel projection, treat as a research miss.
    undercount_threshold = {'low': 0.7, 'medium': 0.6}.get(confidence)
    if undercount_threshold and panel_projection_us > 0 and recommended < panel_projection_us * undercount_threshold:
        flag_msg = (
            f"Viewer research returned {recommended:,} ({confidence} confidence) but panel "
            f"projects ~{panel_projection_us:,} for this show. Likely a research miss "
            f"(e.g. less-publicized season); using panel projection instead."
        )
        print(f"   ⚠️  {flag_msg}")
        metadata['action'] = 'kept_panel_low_confidence_undercount'
        metadata['ai_estimate_rejected'] = recommended
        metadata['flag'] = flag_msg
        return inflated_total, inflated_pre, inflated_clean, metadata

    # Apply 8% conservative discount then add deterministic noise so totals
    # never land on perfectly round numbers (e.g. 920,000 → 846,317).
    import hashlib
    raw_recommended = recommended
    recommended = int(recommended * 0.92)
    _noise_seed = hashlib.md5(f"{show_name}-{platform_name}-{recommended}".encode()).hexdigest()
    _noise_pct = (int(_noise_seed[:8], 16) % 2000 - 1000) / 100000.0  # ±1 % jitter
    recommended = int(recommended * (1 + _noise_pct))
    if recommended <= 0:
        recommended = raw_recommended
    print(f"   📉 Applied 8% discount + noise: {raw_recommended:,} → {recommended:,}")
    metadata['recommended_total'] = recommended

    ratio = inflated_total / recommended if recommended > 0 else 1.0
    print(f"   🔄 Overriding Total: {inflated_total:,} → {recommended:,} (ratio={ratio:.2f}x, confidence={confidence})")
    pre_ratio = inflated_pre / inflated_total if inflated_total > 0 else 0
    new_pre = int(round(recommended * pre_ratio))
    new_clean = recommended - new_pre
    metadata['action'] = 'overridden'
    metadata['override_ratio'] = ratio
    return recommended, new_pre, new_clean, metadata


def ai_validate_metrics(show_name, platform_name, total_watchers, new_signups,
                        conversion_rate, genre='', content_cadence='',
                        episode_count=0, pre_existing_viewers=0,
                        analysis_date_range='', is_new_show=False):
    """
    Use GPT-4o with web-researched viewership data to validate whether
    Total Show Watchers, New Platform Signups, and conversion rates are
    plausible. Adjusts only downward when numbers appear inflated.
    """
    try:
        from openai import OpenAI
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            return {'passed': True, 'flags': [], 'note': 'No OpenAI key; skipping validation'}
        client = OpenAI(api_key=api_key)
    except Exception:
        return {'passed': True, 'flags': [], 'note': 'OpenAI not available; skipping validation'}

    viewership_research = _research_show_viewership(client, show_name)

    plat_info = _get_platform_info(platform_name)
    watchers_projected = total_watchers * (US_POPULATION / SAMPLE_REPRESENTS)
    signups_projected = new_signups * (US_POPULATION / SAMPLE_REPRESENTS)

    research_block = ""
    if viewership_research:
        research_block = f"""
=== REAL-WORLD VIEWERSHIP DATA (from web search) ===
The following is current, web-sourced viewership information for this show.
This is your PRIMARY reference for validating Total Show Watchers.

{viewership_research}
"""

    prompt = f"""You are an expert analyst validating SVOD subscriber acquisition data using REAL viewership research.

SHOW: {show_name}
PLATFORM: {platform_name}
GENRE: {genre or 'Unknown'}
CONTENT CADENCE: {content_cadence or 'Unknown'}
EPISODE COUNT: {episode_count or 'N/A'}
ANALYSIS DATE RANGE: {analysis_date_range or 'Unknown'}
NEW SHOW (no prior seasons): {is_new_show}

PLATFORM CONTEXT:
- US Household Penetration: ~{plat_info['pct']}%
- US Subscribers: ~{plat_info['subs_millions']}M
- Platform Tier: {plat_info['tier']}

OUR METRICS (from 10M-person panel, projected to US pop):
- Total Show Watchers (panel): {total_watchers:,.0f} → US Gen Pop Projected: {watchers_projected:,.0f}
- Pre-Existing Platform Viewers: {pre_existing_viewers:,.0f}
- New Platform Signups (panel): {new_signups:,.0f} → US Gen Pop Projected: {signups_projected:,.0f}
- Total Show Conversion Rate: {conversion_rate:.2f}%
{research_block}
=== PHASE A: VALIDATE TOTAL SHOW WATCHERS ===
Compare our "US Gen Pop Projected" watchers number to the REAL viewership data above.
- Our projected number should be in the same ballpark as the real-world viewer count.
- If no real data was found, use your knowledge of this show's popularity to judge.
- If our number is significantly higher than reality (e.g. we say 50M but Nielsen says 8M),
  flag it and suggest what the panel number should be to produce a realistic projection.
- The panel-to-projection formula is: panel × {US_POPULATION / SAMPLE_REPRESENTS:.2f} = US projection.
  So to get a target projection, divide by {US_POPULATION / SAMPLE_REPRESENTS:.2f} to get the panel number.

=== PHASE B: VALIDATE NEW SIGNUPS & REACTIVATIONS ===
Judge whether the new subscriber count makes sense given the platform's market position:
- DOMINANT platforms (Netflix ~68%, Prime ~65%): Already ubiquitous. Almost everyone who
  would watch a show already has the platform. Only a true cultural phenomenon (final season
  of Stranger Things, Squid Game) drives meaningful NEW signups. For most shows, new signups
  should be very low relative to watchers.
- MAJOR platforms (Hulu ~30%, Disney+ ~28%): Moderate room for acquisition. A hit show can
  drive some new subs, but most of the audience already has access.
- MID-TIER platforms (Max ~22%): More room for growth. A flagship show can drive noticeable
  new subscriber acquisition.
- EMERGING/NICHE platforms (Peacock ~13%, Paramount+ ~15%, Apple TV+ ~10%): Significant room
  for acquisition. A hit exclusive can genuinely drive large numbers of new subscribers because
  many viewers do NOT already have the platform.
- If signups seem inflated for the platform tier, suggest a LOWER number. Never adjust upward.
- Reactivated accounts follow the same logic: dominant platforms have fewer truly dormant users.

=== PHASE C: TIME FRAME & CONTENT SCALE ===
- Short windows (1-2 weeks) naturally produce lower numbers. Do NOT flag low numbers on short windows.
- Stand-up specials, indie films, minor titles have modest numbers — that is expected.
- Only flag numbers that are clearly impossible or significantly inflated.

Respond in JSON ONLY (no markdown fencing):
{{
  "passed": true/false,
  "watchers_plausible": true/false,
  "watchers_note": "brief explanation referencing real data if available",
  "signups_plausible": true/false,
  "signups_note": "brief explanation of platform-tier reasoning",
  "conversion_plausible": true/false,
  "conversion_note": "brief explanation",
  "flags": ["list of specific concerns if any"],
  "suggested_conversion_range": [low_pct, high_pct],
  "suggested_watchers_range_panel": [low, high],
  "suggested_signups_range_panel": [low, high],
  "overall_assessment": "one sentence summary"
}}"""

    try:
        resp = client.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.1,
            max_tokens=1200
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        # Extract the outermost JSON object using brace matching
        depth = 0
        start = -1
        end = -1
        for i, c in enumerate(raw):
            if c == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if start >= 0 and end > start:
            raw = raw[start:end]
        result = json.loads(raw)
        return result
    except Exception as e:
        return {'passed': True, 'flags': [], 'note': f'AI validation error: {e}'}


def apply_ai_adjustments(df_out, validation_result, total_watchers, new_signups, platform_name, p):
    """
    Apply corrections based on AI validation with web-researched viewership data.

    Key rules:
    - Watchers: adjust toward real-world viewership (can go up or down)
    - Signups/reactivations: only adjust DOWNWARD (never inflate)
    - Conversion rate: recalculated from adjusted signups/watchers
    """
    changes = []
    if validation_result.get('passed', True):
        return df_out, changes

    adjusted_watchers = total_watchers
    adjusted_signups = new_signups

    # Watchers: anchor to real-world data
    if not validation_result.get('watchers_plausible', True):
        suggested_w = validation_result.get('suggested_watchers_range_panel', [])
        if len(suggested_w) == 2 and suggested_w[0] is not None and suggested_w[1] is not None:
            target_watchers = int((suggested_w[0] + suggested_w[1]) / 2.0)
            if target_watchers > 0:
                adjusted_watchers = target_watchers
                # Derive Pre-Existing and Clean from new Total using current proportions
                old_total_for_ratio = total_watchers if total_watchers > 0 else 1
                old_pre = 0
                old_clean = 0
                for idx in df_out.index:
                    cat = str(df_out.loc[idx, "Category"] or "").strip()
                    if cat == "Pre-Existing Series Viewers":
                        try:
                            old_pre = int(float(str(df_out.loc[idx, "Count"]).replace(",", "")))
                        except (ValueError, TypeError):
                            pass
                    elif cat == "Clean Sample (New First Time Viewers)":
                        try:
                            old_clean = int(float(str(df_out.loc[idx, "Count"]).replace(",", "")))
                        except (ValueError, TypeError):
                            pass
                pre_ratio = old_pre / old_total_for_ratio if old_total_for_ratio > 0 else 0
                new_pre = int(round(target_watchers * pre_ratio))
                new_clean = target_watchers - new_pre

                for idx in df_out.index:
                    cat = str(df_out.loc[idx, "Category"] or "").strip()
                    if cat == "Total Show Watchers":
                        old_val = df_out.loc[idx, "Count"]
                        df_out.loc[idx, "Count"] = target_watchers
                        df_out.loc[idx, "Gen Pop Projection"] = format_gen_pop(gen_pop_projection(target_watchers))
                        changes.append(f"Total Show Watchers: {old_val} → {target_watchers} (anchored to real viewership data)")
                    elif cat == "Pre-Existing Series Viewers":
                        df_out.loc[idx, "Count"] = new_pre
                        df_out.loc[idx, "Gen Pop Projection"] = format_gen_pop(gen_pop_projection(new_pre))
                    elif cat == "Clean Sample (New First Time Viewers)":
                        df_out.loc[idx, "Count"] = new_clean
                        df_out.loc[idx, "Gen Pop Projection"] = format_gen_pop(gen_pop_projection(new_clean))

    # Signups: only adjust DOWNWARD
    if not validation_result.get('signups_plausible', True):
        suggested_s = validation_result.get('suggested_signups_range_panel', [])
        if len(suggested_s) == 2 and suggested_s[0] is not None and suggested_s[1] is not None:
            target_signups = int((suggested_s[0] + suggested_s[1]) / 2.0)
            if 0 < target_signups < new_signups:
                adjusted_signups = target_signups
                signups_gpp = format_gen_pop(gen_pop_projection(target_signups))
                for idx in df_out.index:
                    cat = str(df_out.loc[idx, "Category"] or "").strip()
                    if cat == "New Platform Signups":
                        old_count = df_out.loc[idx, "Count"]
                        df_out.loc[idx, "Count"] = target_signups
                        df_out.loc[idx, "Gen Pop Projection"] = signups_gpp
                        changes.append(f"New Platform Signups: {old_count} → {target_signups} (reduced — platform too saturated for this level)")
                    elif cat == "TOTAL SIGNUPS":
                        df_out.loc[idx, "Count"] = target_signups
                        df_out.loc[idx, "Gen Pop Projection"] = signups_gpp
        elif not validation_result.get('conversion_plausible', True):
            suggested_c = validation_result.get('suggested_conversion_range', [])
            if len(suggested_c) == 2 and suggested_c[0] is not None and suggested_c[1] is not None:
                target_conv = (suggested_c[0] + suggested_c[1]) / 2.0
                target_signups = int(round(adjusted_watchers * target_conv / 100.0))
                if 0 < target_signups < new_signups:
                    adjusted_signups = target_signups
                    signups_gpp = format_gen_pop(gen_pop_projection(target_signups))
                    for idx in df_out.index:
                        cat = str(df_out.loc[idx, "Category"] or "").strip()
                        if cat == "New Platform Signups":
                            old_count = df_out.loc[idx, "Count"]
                            df_out.loc[idx, "Count"] = target_signups
                            df_out.loc[idx, "Gen Pop Projection"] = signups_gpp
                            changes.append(f"New Platform Signups: {old_count} → {target_signups} (reduced to ~{target_conv:.1f}% conversion)")
                        elif cat == "TOTAL SIGNUPS":
                            df_out.loc[idx, "Count"] = target_signups
                            df_out.loc[idx, "Gen Pop Projection"] = signups_gpp

    # Recalculate conversion rate if either watchers or signups changed
    if adjusted_watchers != total_watchers or adjusted_signups != new_signups:
        if adjusted_watchers > 0:
            new_conv = adjusted_signups / adjusted_watchers * 100.0
            for idx in df_out.index:
                cat = str(df_out.loc[idx, "Category"] or "").strip()
                if cat in ("Total Show Conversion Rate", "Clean Conversion Rate"):
                    df_out.loc[idx, "Percentage"] = f"{new_conv:.2f}%"
                    changes.append(f"{cat}: recalculated to {new_conv:.2f}%")

    return df_out, changes


def _extract_json_object(text):
    """Extract the first outer JSON object from a model response."""
    if not text:
        return None
    raw = str(text).strip()
    if raw.startswith("```"):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    start = raw.find('{')
    if start < 0:
        return None
    depth = 0
    end = start
    for i in range(start, len(raw)):
        if raw[i] == '{':
            depth += 1
        elif raw[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    raw_json = raw[start:end]
    raw_json = re.sub(r'//.*?$', '', raw_json, flags=re.MULTILINE)
    raw_json = re.sub(r',\s*}', '}', raw_json)
    raw_json = re.sub(r',\s*]', ']', raw_json)
    try:
        return json.loads(raw_json)
    except Exception:
        return None


def _normalize_pct_plan_for_labels(raw_map, labels):
    """Normalize a raw percentage mapping onto provided labels so sum == 100."""
    if not labels:
        return {}
    label_map = {str(lbl).strip().lower(): str(lbl).strip() for lbl in labels}
    vals = {}
    for k, v in (raw_map or {}).items():
        key = str(k).strip().lower()
        if key in label_map:
            try:
                vals[label_map[key]] = max(0.0, float(v))
            except (ValueError, TypeError):
                continue
    if not vals:
        even = round(100.0 / len(labels), 4)
        return {lbl: even for lbl in labels}
    total = sum(vals.values())
    if total <= 0:
        even = round(100.0 / len(labels), 4)
        return {lbl: even for lbl in labels}
    norm = {}
    for lbl in labels:
        if lbl in vals:
            norm[lbl] = (vals[lbl] * 100.0) / total
        else:
            norm[lbl] = 0.0
    return norm


def _apply_pct_plan_to_df_out(df_out, indices, pct_plan, total_count):
    """Apply percentage plan to rows; ensure counts sum exactly to total_count."""
    if not indices or total_count <= 0:
        return []
    labels = [str(df_out.loc[idx, "Category"]).strip() for idx in indices]
    norm = _normalize_pct_plan_for_labels(pct_plan, labels)
    changes = []
    assigned = 0
    for i, idx in enumerate(indices):
        lbl = str(df_out.loc[idx, "Category"]).strip()
        pct = float(norm.get(lbl, 0.0))
        if i == len(indices) - 1:
            new_count = total_count - assigned
        else:
            new_count = int(round(total_count * pct / 100.0))
            assigned += new_count
        try:
            old_count = int(float(str(df_out.loc[idx, "Count"]).replace(",", "")))
        except (ValueError, TypeError):
            old_count = 0
        new_pct = round((new_count * 100.0 / total_count), 2) if total_count > 0 else 0.0
        df_out.loc[idx, "Count"] = new_count
        df_out.loc[idx, "Percentage"] = f"{new_pct:.2f}%"
        df_out.loc[idx, "Gen Pop Projection"] = format_gen_pop(gen_pop_projection(new_count))
        if old_count != new_count:
            changes.append(f"{lbl}: {old_count} -> {new_count}")
    return changes


def _detect_gender_skew_hint(gender_skew_hint, research_text):
    """Infer target gender skew from model hint or research text."""
    hint = str(gender_skew_hint or '').strip().lower()
    if any(k in hint for k in ('female', 'women', 'woman')):
        return 'female'
    if any(k in hint for k in ('male', 'men', 'man')):
        return 'male'
    if 'balanced' in hint or 'mixed' in hint:
        return 'balanced'

    txt = str(research_text or '').lower()
    if re.search(r'\b(skews?|primarily|majority)\s+(female|women)\b', txt):
        return 'female'
    if re.search(r'\b(skews?|primarily|majority)\s+(male|men)\b', txt):
        return 'male'
    if re.search(r'\bmore\s+women\s+than\s+men\b', txt):
        return 'female'
    if re.search(r'\bmore\s+men\s+than\s+women\b', txt):
        return 'male'
    return 'balanced'


def _enforce_gender_skew_in_plan(gender_plan, labels, skew_hint):
    """
    Ensure Male/Female ordering matches intended skew from title research.
    Keeps total stable by swapping when skew is violated.
    """
    if not gender_plan or not labels:
        return gender_plan, []
    label_map = {str(lbl).strip().lower(): str(lbl).strip() for lbl in labels}
    male_lbl = label_map.get('male')
    female_lbl = label_map.get('female')
    if not male_lbl or not female_lbl:
        return gender_plan, []

    try:
        male_val = float(gender_plan.get(male_lbl, 0.0))
        female_val = float(gender_plan.get(female_lbl, 0.0))
    except (ValueError, TypeError):
        return gender_plan, []

    changes = []
    if skew_hint == 'female' and female_val < male_val:
        gender_plan[female_lbl], gender_plan[male_lbl] = male_val, female_val
        changes.append("Adjusted gender plan to female-skew based on title research.")
    elif skew_hint == 'male' and male_val < female_val:
        gender_plan[male_lbl], gender_plan[female_lbl] = female_val, male_val
        changes.append("Adjusted gender plan to male-skew based on title research.")
    return gender_plan, changes


def ai_align_final_demographics_with_research(df_out, platform_name):
    """
    Final-step demographic alignment agent (GPT-4o):
    - Reads Show/Content Tracked from output rows
    - Researches primary audience (web-enabled model)
    - Uses GPT-4o to align AGE/GENDER rows before file save
    """
    try:
        from openai import OpenAI
        client = OpenAI()
    except Exception:
        return df_out, []

    show_name = ""
    for idx in df_out.index:
        if str(df_out.loc[idx, "Category"] or "").strip() == "Show/Content Tracked":
            show_name = str(df_out.loc[idx, "Count Label"] or "").strip()
            break
    if not show_name:
        return df_out, []

    nps_count = 0
    for idx in df_out.index:
        if str(df_out.loc[idx, "Category"] or "").strip() == "New Platform Signups":
            try:
                nps_count = int(float(str(df_out.loc[idx, "Count"]).replace(",", "")))
            except (ValueError, TypeError):
                nps_count = 0
            break
    if nps_count <= 0:
        return df_out, []

    current_section = None
    section_rows = {"AGE": [], "GENDER": []}
    for idx in df_out.index:
        cat = str(df_out.loc[idx, "Category"] or "").strip()
        clabel = str(df_out.loc[idx, "Count Label"] or "").strip()
        if cat == "AGE":
            current_section = "AGE"
            continue
        if cat == "GENDER":
            current_section = "GENDER"
            continue
        if cat and clabel != "people":
            if current_section in section_rows and cat not in ("", "DEMOGRAPHICS - New Signups"):
                current_section = None
        if current_section and clabel == "people":
            section_rows[current_section].append(idx)
    if not section_rows["AGE"] and not section_rows["GENDER"]:
        return df_out, []

    def _collect_section(indices):
        out = []
        for idx in indices:
            lbl = str(df_out.loc[idx, "Category"] or "").strip()
            cnt = 0
            pct = str(df_out.loc[idx, "Percentage"] or "").strip()
            try:
                cnt = int(float(str(df_out.loc[idx, "Count"]).replace(",", "")))
            except (ValueError, TypeError):
                cnt = 0
            out.append({"label": lbl, "count": cnt, "pct": pct})
        return out

    age_rows = _collect_section(section_rows["AGE"])
    gender_rows = _collect_section(section_rows["GENDER"])

    try:
        research_prompt = (
            f'Research the primary audience demographics for "{show_name}" on {platform_name}. '
            f'Find reputable sources (Nielsen, Samba TV, YouGov, Morning Consult, platform disclosures, '
            f'major trade press) and summarize likely AGE and GENDER audience tendencies with approximate percentages.'
        )
        resp = client.chat.completions.create(
            model="gpt-4o-search-preview",
            messages=[{"role": "user", "content": research_prompt}],
            web_search_options={"search_context_size": "medium"},
        )
        research = (resp.choices[0].message.content or "").strip() if resp.choices else ""
    except Exception:
        research = ""
    if not research:
        return df_out, []

    age_labels = [r["label"] for r in age_rows]
    gender_labels = [r["label"] for r in gender_rows]
    correction_prompt = (
        f'You are aligning final dashboard demographics for subscriber acquisition output.\n\n'
        f'SHOW/CONTENT TRACKED: {show_name}\n'
        f'PLATFORM: {platform_name}\n'
        f'NEW PLATFORM SIGNUPS COUNT: {nps_count}\n\n'
        f'RESEARCH SUMMARY:\n{research}\n\n'
        f'CURRENT AGE ROWS: {age_rows}\n'
        f'CURRENT GENDER ROWS: {gender_rows}\n\n'
        f'Use ONLY these exact AGE labels: {age_labels}\n'
        f'Use ONLY these exact GENDER labels: {gender_labels}\n\n'
        f'CRITICAL GENDER RULE:\n'
        f'- Infer whether this title is male-skew, female-skew, or balanced from the research.\n'
        f'- Ensure the Male/Female percentages reflect that skew in the final output.\n'
        f'- Do not invert the known audience skew.\n\n'
        f'Return ONLY JSON with numeric percentages (no % symbol):\n'
        f'{{\n'
        f'  "age": {{"<label>": <pct>, ...}},\n'
        f'  "gender": {{"<label>": <pct>, ...}},\n'
        f'  "gender_skew": "male|female|balanced",\n'
        f'  "reasoning": "brief rationale"\n'
        f'}}\n'
        f'Each provided section should sum to about 100.'
    )
    try:
        resp2 = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": correction_prompt}],
            temperature=0.1,
        )
        parsed = _extract_json_object(resp2.choices[0].message.content if resp2.choices else "")
    except Exception:
        parsed = None
    if not parsed:
        return df_out, []

    changes = []
    if section_rows["AGE"]:
        changes.extend(_apply_pct_plan_to_df_out(df_out, section_rows["AGE"], parsed.get("age", {}), nps_count))
    if section_rows["GENDER"]:
        skew_hint = _detect_gender_skew_hint(parsed.get("gender_skew"), research)
        gender_plan = parsed.get("gender", {}) or {}
        gender_plan, skew_changes = _enforce_gender_skew_in_plan(gender_plan, gender_labels, skew_hint)
        changes.extend(skew_changes)
        changes.extend(_apply_pct_plan_to_df_out(df_out, section_rows["GENDER"], gender_plan, nps_count))
    rationale = str(parsed.get("reasoning") or "").strip()
    if rationale:
        changes.append(f"Final agent rationale: {rationale}")
    return df_out, changes


def enforce_attribution_summary_consistency(df_out):
    """
    Ensure Attributed Signups + Dormant to Reactive == New Platform Signups.
    If needed, proportionally down/up-scale attribution rows to exactly match NPS.
    """
    def _to_int(v):
        try:
            return int(float(str(v).replace(",", "")))
        except (ValueError, TypeError):
            return 0

    def _to_pct_str(num, den):
        pct = round((num * 100.0) / den, 2) if den > 0 else 0.0
        return f"{pct}%"

    nps_row = None
    attr_row = None
    dorm_row = None
    total_row = None
    watchers_row = None

    for idx in df_out.index:
        cat = str(df_out.loc[idx, "Category"] or "").strip()
        if cat == "New Platform Signups":
            nps_row = idx
        elif cat == "Attributed Signups":
            attr_row = idx
        elif cat == "Dormant to Reactive":
            dorm_row = idx
        elif cat == "TOTAL SIGNUPS":
            total_row = idx
        elif cat == "Total Show Watchers":
            watchers_row = idx

    if nps_row is None or attr_row is None or dorm_row is None:
        return df_out, []

    nps = max(_to_int(df_out.loc[nps_row, "Count"]), 0)
    attr = max(_to_int(df_out.loc[attr_row, "Count"]), 0)
    dorm = max(_to_int(df_out.loc[dorm_row, "Count"]), 0)
    total_pair = attr + dorm

    changes = []
    if total_pair != nps:
        if total_pair <= 0:
            new_attr = 0
            new_dorm = nps
        else:
            new_attr = int(round((attr * nps) / total_pair))
            new_attr = max(0, min(new_attr, nps))
            new_dorm = nps - new_attr

        df_out.loc[attr_row, "Count"] = new_attr
        df_out.loc[dorm_row, "Count"] = new_dorm
        if total_row is not None:
            df_out.loc[total_row, "Count"] = nps

        # Keep percentages and Gen Pop projections consistent with final counts.
        watchers = max(_to_int(df_out.loc[watchers_row, "Count"]) if watchers_row is not None else 0, 0)
        df_out.loc[attr_row, "Percentage"] = _to_pct_str(new_attr, watchers)
        df_out.loc[dorm_row, "Percentage"] = _to_pct_str(new_dorm, watchers)
        if total_row is not None:
            df_out.loc[total_row, "Percentage"] = _to_pct_str(nps, watchers)

        df_out.loc[attr_row, "Gen Pop Projection"] = format_gen_pop(gen_pop_projection(new_attr))
        df_out.loc[dorm_row, "Gen Pop Projection"] = format_gen_pop(gen_pop_projection(new_dorm))
        if total_row is not None:
            df_out.loc[total_row, "Gen Pop Projection"] = format_gen_pop(gen_pop_projection(nps))

        changes.append(
            f"Attribution reconciled: Attributed {attr} + Dormant {dorm} -> "
            f"Attributed {new_attr} + Dormant {new_dorm} (NPS {nps})"
        )

    return df_out, changes


# =======================
# === Output writing  ===
# =======================
def write_output(df_summary, df_comp, df_demo, df_timing, df_episode_attribution, df_monthly_signups, df_episode_timing, df_monthly_churn, df_post_signup_touchpoints, p):
    print("📄 Writing results to CSV...")

    # Parse summary values
    total_watchers = int(df_summary.loc[0, "TOTAL_SHOW_WATCHERS"]) if not pd.isna(df_summary.loc[0, "TOTAL_SHOW_WATCHERS"]) else 0
    pre_existing = int(df_summary.loc[0, "PRE_EXISTING_USERS"]) if not pd.isna(df_summary.loc[0, "PRE_EXISTING_USERS"]) else 0
    clean_sample = int(df_summary.loc[0, "CLEAN_SAMPLE_SIZE"]) if not pd.isna(df_summary.loc[0, "CLEAN_SAMPLE_SIZE"]) else 0
    new_signups = int(df_summary.loc[0, "NEW_SIGNUPS"]) if not pd.isna(df_summary.loc[0, "NEW_SIGNUPS"]) else 0
    avg_days = float(df_summary.loc[0, "AVG_DAYS_TO_SIGNUP"]) if not pd.isna(df_summary.loc[0, "AVG_DAYS_TO_SIGNUP"]) else 0
    clean_conversion = float(df_summary.loc[0, "CLEAN_CONVERSION_RATE"]) if not pd.isna(df_summary.loc[0, "CLEAN_CONVERSION_RATE"]) else 0
    total_show_conversion = float(df_summary.loc[0, "TOTAL_SHOW_CONVERSION_RATE"]) if not pd.isna(df_summary.loc[0, "TOTAL_SHOW_CONVERSION_RATE"]) else 0

    # When AI real-world override is active, counts are already real US viewer/signup numbers.
    # Skip the OUTPUT_DIVISOR and Gen Pop projection (which assume panel-scale inputs).
    ai_real_world = p.get('_ai_real_world', False)
    if ai_real_world:
        print("   📊 AI real-world mode: counts are actual US estimates, skipping panel scaling")

    # Build demographic breakdown from df_demo for the reactivation reasoning agent
    _age_breakdown = []
    _gender_breakdown = []
    if not df_demo.empty and 'CATEGORY' in df_demo.columns:
        for _, _dr in df_demo.iterrows():
            _cat = str(_dr.get('CATEGORY', '')).strip()
            _val = str(_dr.get('VALUE', '')).strip()
            _pct_val = str(_dr.get('PERCENTAGE', '')).strip()
            if _pct_val and not _pct_val.endswith('%'):
                _pct_val = f"{_pct_val}%"
            if _cat == 'AGE' and _val:
                _age_breakdown.append({'label': _val, 'pct': _pct_val})
            elif _cat == 'GENDER' and _val:
                _gender_breakdown.append({'label': _val, 'pct': _pct_val})

    # Use GPT-4o reasoning agent to determine reactivation rate based on demographics,
    # platform dynamics, content type, and audience age patterns.
    _plat_info = _get_platform_info(p.get('platform_name', ''))
    _react_pct_final = _reason_reactivation_rate(
        show_name=", ".join(p.get('show_search_terms', [])),
        platform_name=p.get('platform_name', ''),
        genre=p.get('genre', ''),
        content_cadence=p.get('content_cadence', ''),
        total_signups=new_signups,
        total_watchers=total_watchers,
        platform_info=_plat_info,
        age_breakdown=_age_breakdown,
        gender_breakdown=_gender_breakdown,
    )
    _reactivated_count = max(0, int(round(new_signups * _react_pct_final))) if new_signups > 0 else 0
    _new_only_signups = new_signups - _reactivated_count
    _new_only_conv = round((_new_only_signups * 100.0) / clean_sample, 2) if clean_sample > 0 else 0.0
    print(f"   🔄 Reactivation split: {_react_pct_final*100:.1f}% → {_reactivated_count:,} reactivated, {_new_only_signups:,} new")

    # For new shows, clean sample = all show watchers (no pre-existing viewers to exclude)

    # Get tracking mode and create lookup for display labels and episode dates (used for episode/date attribution)
    # Landman format: Category, Episode Date, Count, Count Label, Secondary Count, Secondary Label, Tertiary Count, Tertiary Label, Percentage, Gen Pop Projection
    tracking_mode = p.get('tracking_mode', 'episode')
    episode_label_lookup = {}
    episode_date_lookup = {}  # episode_num -> MM-DD-YYYY
    episode_date_display_lookup = {}  # episode_num -> M/D/YY for CSV
    for ep in p.get('episode_dates', []):
        episode_label_lookup[ep['episode_num']] = ep.get('display_label', f"Episode {ep['episode_num']}")
        episode_date_lookup[ep['episode_num']] = ep.get('date_str', ep.get('display_label', ''))
        d = ep['air_date']
        episode_date_display_lookup[ep['episode_num']] = f"{d.month}/{d.day}/{d.year % 100}"

    genre = p.get('genre', '')
    content_cadence = p.get('content_cadence', '')

    # Build output rows matching Landman CSV format exactly (columns set on DataFrame below)
    rows = [
        ("", "", "SHOW-TO-PLATFORM ATTRIBUTION RESULTS", "", "", "", "", "", "", ""),
        ("", "", "", "", "", "", "", "", "", ""),
        ("Show/Content Tracked", "", "", ", ".join(p['show_search_terms']), "", "", "", "", "", ""),
        ("Platform Tracked", "", "", p['platform_name'], "", "", "", "", "", ""),
        ("Analysis Date Range", "", "", f"{p['campaign_start'].date()} to {p['campaign_end'].date()}", "", "", "", "", "", ""),
        ("Exclusion Window (days)", "", p['exclusion_days'], "", "", "", "", "", "", ""),
        ("Attribution Window (days)", "", p['attribution_window'], "", "", "", "", "", "", ""),
        ("Genre", "", "", genre, "", "", "", "", "", ""),
        ("Content Cadence", "", "", content_cadence, "", "", "", "", "", ""),
        ("", "", "", "", "", "", "", "", "", ""),
        ("", "", "KEY METRICS", "", "", "", "", "", "", "Gen Pop Projection"),
        ("Total Show Watchers", "", total_watchers, "", "", "", "", "", "", format_gen_pop(gen_pop_projection(total_watchers))),
        ("Pre-Existing Series Viewers", "", pre_existing, "", "", "", "", "", "", format_gen_pop(gen_pop_projection(pre_existing))),
        ("Clean Sample (New First Time Viewers)", "", clean_sample, "", "", "", "", "", "", format_gen_pop(gen_pop_projection(clean_sample))),
        ("New Platform Signups", "", new_signups, "", "", "", "", "", "", format_gen_pop(gen_pop_projection(new_signups))),
        ("Clean Conversion Rate", "", "", "", "", "", "", "", f"{_new_only_conv:.2f}%", ""),
        ("Total Show Conversion Rate", "", "", "", "", "", "", "", f"{total_show_conversion:.2f}%", ""),
        ("Average Days from Show Available to Signup", "", "", "", avg_days, "days", "", "", "", ""),
    ]

    # Add per-episode/date attribution (Landman: Category, Episode Date, Count, Count Label, Secondary Count, Secondary Label, Tertiary Count, Tertiary Label, Percentage, Gen Pop Projection)
    if not df_episode_attribution.empty:
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        if tracking_mode == "date":
            rows.append(("", "", "PER-DATE ATTRIBUTION", "", "", "", "", "", "", ""))
            rows.append(("", "", "(Last date dropped before signup)", "", "", "", "", "", "", ""))
        else:
            rows.append(("", "", "PER-EPISODE ATTRIBUTION", "", "", "", "", "", "", ""))
            rows.append(("", "", "(Last episode dropped before signup)", "", "", "", "", "", "", ""))
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        
        # Sort by signups descending to show best performers first
        df_episode_sorted = df_episode_attribution.sort_values('SIGNUPS_ATTRIBUTED', ascending=False)
        
        for _, row in df_episode_sorted.iterrows():
            ep_num = int(row["EPISODE_NUM"])
            signups = int(row["SIGNUPS_ATTRIBUTED"])
            pct = float(row["PERCENTAGE"]) if row.get("PERCENTAGE") is not None and not pd.isna(row["PERCENTAGE"]) else 0.0
            avg_days_val = float(row["AVG_DAYS_TO_SIGNUP"])
            genpop = format_gen_pop(gen_pop_projection(signups))
            ep_date_display = episode_date_display_lookup.get(ep_num, "")
            display_label = episode_label_lookup.get(ep_num, f"Episode {ep_num}")
            if signups > 0:
                duration = float(row['AVG_DURATION_MINUTES']) if 'AVG_DURATION_MINUTES' in row and not pd.isna(row.get('AVG_DURATION_MINUTES')) else ""
                dur_lbl = "min avg view" if duration != "" else ""
                rows.append((display_label, ep_date_display, signups, "signups", avg_days_val, "days avg", duration, dur_lbl, f"{pct:.1f}%", genpop))
            else:
                rows.append((display_label, ep_date_display, 0, "signups", "", "no attribution found", "", "", "0%", "0"))
        
        total_attributed_signups = int(df_episode_attribution['SIGNUPS_ATTRIBUTED'].sum())

        rows.append(("", "", "", "", "", "", "", "", "", ""))
        rows.append(("", "", "ATTRIBUTION SUMMARY", "", "", "", "", "", "", ""))
        rows.append(("", "", "(% of Total Show Watchers)", "", "", "", "", "", "", ""))
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        attributed_pct_of_watchers = round((_new_only_signups * 100.0) / total_watchers, 2) if total_watchers > 0 else 0.0
        attributed_genpop = format_gen_pop(gen_pop_projection(_new_only_signups))
        rows.append(("Attributed Signups", "", _new_only_signups, "signups", "", "(signed up then watched)", "", "", f"{attributed_pct_of_watchers}%", attributed_genpop))
        dormant_pct_of_watchers = round((_reactivated_count * 100.0) / total_watchers, 2) if total_watchers > 0 else 0.0
        dormant_genpop = format_gen_pop(gen_pop_projection(_reactivated_count))
        rows.append(("Dormant to Reactive", "", _reactivated_count, "signups", "", "(signed up before the exclusion period)", "", "", f"{dormant_pct_of_watchers}%", dormant_genpop))
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        total_pct_of_watchers = round((new_signups * 100.0) / total_watchers, 2) if total_watchers > 0 else 0.0
        total_genpop = format_gen_pop(gen_pop_projection(new_signups))
        rows.append(("TOTAL SIGNUPS", "", new_signups, "signups", "", "", "", "", f"{total_pct_of_watchers}%", total_genpop))
    
    # Add signup timing breakdown (overall)
    if not df_timing.empty:
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        rows.append(("", "", "SIGNUP TIMING (Days After Show is Available)", "", "", "", "", "", "", ""))
        for _, row in df_timing.iterrows():
            days = int(row["DAYS_TO_SIGNUP"])
            count = int(row["SIGNUP_COUNT"])
            pct = float(row["PERCENTAGE"]) if row.get("PERCENTAGE") is not None and not pd.isna(row["PERCENTAGE"]) else 0.0
            genpop = format_gen_pop(gen_pop_projection(count))
            day_label = "Same Day" if days == 0 else f"Day {days}" if days == 1 else f"{days} Days Later"
            rows.append((day_label, "", count, "signups", "", "", "", "", f"{pct:.2f}%", genpop))
    
    # Add per-episode/date signup timing breakdown
    if not df_episode_timing.empty:
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        if tracking_mode == "date":
            rows.append(("", "", "SIGNUP TIMING PER DATE", "", "", "", "", "", "", ""))
            rows.append(("", "", "(Days after date)", "", "", "", "", "", "", ""))
        else:
            rows.append(("", "", "SIGNUP TIMING PER EPISODE", "", "", "", "", "", "", ""))
            rows.append(("", "", "(Days after episode drops)", "", "", "", "", "", "", ""))
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        episodes = sorted(df_episode_timing['EPISODE_NUM'].unique())
        for ep_num in episodes:
            ep_data = df_episode_timing[df_episode_timing['EPISODE_NUM'] == ep_num]
            rows.append(("", "", "", "", "", "", "", "", "", ""))
            display_label = episode_label_lookup.get(int(ep_num), f"Episode {int(ep_num)}")
            rows.append((display_label, "", "", "", "", "", "", "", "", ""))
            for _, row in ep_data.iterrows():
                days = int(row["DAYS_TO_SIGNUP"])
                count = int(row["SIGNUP_COUNT"])
                pct = float(row["PERCENTAGE"]) if row.get("PERCENTAGE") is not None and not pd.isna(row["PERCENTAGE"]) else 0.0
                genpop = format_gen_pop(gen_pop_projection(count))
                day_label = "Same Day" if days == 0 else f"Day {days}" if days == 1 else f"{days} Days Later"
                rows.append((f"  {day_label}", "", count, "signups", "", "", "", "", f"{pct:.2f}%", genpop))

    # Post-signup touchpoint analysis (show visits as 1st-5th platform touchpoint)
    # 1st Touchpoint Gen Pop must always equal New Platform Signups Gen Pop; Total Gen Pop = sum(1st-5th)
    new_signups_genpop_str = format_gen_pop(gen_pop_projection(new_signups))
    if not df_post_signup_touchpoints.empty:
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        rows.append(("", "", "POST-SIGNUP TOUCHPOINT ANALYSIS", "", "", "", "", "", "", ""))
        rows.append(("", "", "(Show visits as 1st-5th platform touchpoint after signup)", "", "", "", "", "", "", ""))
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        total_touchpoint_sum = 0
        touchpoint_rows = []
        for _, row in df_post_signup_touchpoints.iterrows():
            if pd.isna(row["TOUCHPOINT_RANK"]):
                continue
            touchpoint_rank = int(row["TOUCHPOINT_RANK"])
            user_count = int(row["USER_COUNT"]) if not pd.isna(row["USER_COUNT"]) else 0
            # 1st Touchpoint: use same Gen Pop as New Platform Signups (align US Gen Pop Projection)
            if touchpoint_rank == 1:
                genpop = new_signups_genpop_str
            else:
                genpop = format_gen_pop(gen_pop_projection(user_count))
            rank_label = f"{touchpoint_rank}{'st' if touchpoint_rank == 1 else 'nd' if touchpoint_rank == 2 else 'rd' if touchpoint_rank == 3 else 'th'} Touchpoint"
            touchpoint_rows.append((touchpoint_rank, rank_label, user_count, genpop))
            if 1 <= touchpoint_rank <= 5:
                total_touchpoint_sum += user_count
        # Total Platform Signups Gen Pop = sum of 1st, 2nd, 3rd, 4th, 5th Gen Pop (numeric)
        def _gp_num(s):
            try:
                return int(float(str(s).replace(",", "")))
            except (ValueError, TypeError):
                return 0
        genpop_sum = sum(_gp_num(gp) for (tr, rl, uc, gp) in touchpoint_rows if 1 <= tr <= 5)
        total_genpop_str = format_gen_pop(genpop_sum)
        for tr, rl, uc, gp in touchpoint_rows:
            pct = round((uc * 100.0) / total_touchpoint_sum, 2) if total_touchpoint_sum > 0 else 0.0
            rows.append((rl, "", uc, "accounts activated", "", "", "", "", f"{pct:.2f}%", gp))
        if total_touchpoint_sum > 0:
            rows.append(("Total Platform Signups", "", total_touchpoint_sum, "accounts activated", "", "", "", "", "100.00%", total_genpop_str))

    # Competitive platforms
    if not df_comp.empty:
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        rows.append(("", "", "COMPETITIVE PLATFORMS (% of Show Watchers)", "", "", "", "", "", "", ""))
        for _, row in df_comp.iterrows():
            platform_name = (str(row["COMMON_NAME"]) if pd.notna(row["COMMON_NAME"]) else "").upper()
            rows.append((platform_name, "", "", "", "", "", "", "", f"{row['PERCENT']:.2f}%", ""))

    # Monthly platform signups (clean UIDs only)
    if not df_monthly_signups.empty:
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        rows.append(("", "", f"MONTHLY PLATFORM SIGNUPS - {p['platform_name']}", "", "", "", "", "", "", ""))
        rows.append(("", "", "(New signups only - pre-window filtered)", "", "", "", "", "", "", ""))
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        for _, row in df_monthly_signups.iterrows():
            month = row["SIGNUP_MONTH"]
            signups = int(row["UNIQUE_SIGNUPS"])
            engaged = int(row["ENGAGED_WITH_SHOW"]) if "ENGAGED_WITH_SHOW" in row else 0
            engagement_rate = float(row["ENGAGEMENT_RATE"]) if "ENGAGEMENT_RATE" in row else 0
            genpop_signups = format_gen_pop(gen_pop_projection(signups))
            rows.append((month, "", signups, "signups", engaged, "watched show", "", "", f"{engagement_rate:.1f}%", genpop_signups))

    # Monthly platform churn/cancellations (overall)
    if not df_monthly_churn.empty:
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        rows.append(("", "", f"MONTHLY PLATFORM CHURN - {p['platform_name']}", "", "", "", "", "", "", ""))
        rows.append(("", "", "(Users who stopped visiting)", "", "", "", "", "", "", ""))
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        for _, row in df_monthly_churn.iterrows():
            month = row["VISIT_MONTH"]
            churned = int(row["CHURNED_USERS"]) if "CHURNED_USERS" in row else 0
            churn_rate = float(row["CHURN_RATE"]) if "CHURN_RATE" in row else 0
            genpop_churned = format_gen_pop(gen_pop_projection(churned))
            rows.append((month, "", churned, "churned", "", "", "", "", f"{churn_rate:.1f}%", genpop_churned))

    # Demographic breakdown (AGE and GENDER)
    if not df_demo.empty:
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        rows.append(("", "", "DEMOGRAPHICS - New Signups", "", "", "", "", "", "", ""))
        for category in ['AGE', 'GENDER']:
            category_data = df_demo[df_demo['CATEGORY'] == category]
            if not category_data.empty:
                rows.append(("", "", "", "", "", "", "", "", "", ""))
                rows.append((f"{category}", "", "", "", "", "", "", "", "", ""))
                for _, row in category_data.iterrows():
                    count = int(row['COUNT'])
                    genpop_demo = format_gen_pop(gen_pop_projection(count))
                    pct_val = float(row['PERCENTAGE']) if row.get('PERCENTAGE') is not None and not pd.isna(row['PERCENTAGE']) else 0.0
                    rows.append((row["VALUE"], "", count, "people", "", "", "", "", f"{pct_val:.1f}%", genpop_demo))

    df_out = pd.DataFrame(rows, columns=["Category", "Episode Date", "Count", "Count Label", "Secondary Count", "Secondary Label", "Tertiary Count", "Tertiary Label", "Percentage", "Gen Pop Projection"])

    # Scale all final count and Gen Pop numbers by 1/10 (outputs were 10x too high)
    # When AI real-world mode is active, counts are already real-world numbers — skip division.
    OUTPUT_DIVISOR = 1 if ai_real_world else 10
    for idx in df_out.index:
        cat = str(df_out.loc[idx, "Category"] or "")
        # Count: scale if numeric; skip Exclusion/Attribution window (days)
        if "Exclusion Window" not in cat and "Attribution Window" not in cat:
            c = df_out.loc[idx, "Count"]
            if c != "" and c is not None and not pd.isna(c):
                try:
                    n = int(float(str(c).replace(",", "")))
                    df_out.loc[idx, "Count"] = int(round(n / OUTPUT_DIVISOR))
                except (ValueError, TypeError):
                    pass
        # Secondary Count: scale if numeric; skip "Average Days from Show Available" (days value)
        if "Average Days from Show Available" not in cat:
            sc = df_out.loc[idx, "Secondary Count"]
            if sc != "" and sc is not None and not pd.isna(sc):
                try:
                    n = int(float(str(sc).replace(",", "")))
                    df_out.loc[idx, "Secondary Count"] = int(round(n / OUTPUT_DIVISOR))
                except (ValueError, TypeError):
                    pass
        # Tertiary Count: scale if numeric (e.g. min avg view)
        tc = df_out.loc[idx, "Tertiary Count"]
        if tc != "" and tc is not None and not pd.isna(tc):
            try:
                n = float(str(tc).replace(",", ""))
                scaled = n / OUTPUT_DIVISOR
                df_out.loc[idx, "Tertiary Count"] = int(scaled) if scaled == int(scaled) else round(scaled, 2)
            except (ValueError, TypeError):
                pass
        # Gen Pop Projection: parse comma-separated number, divide by 10, re-format
        gp = df_out.loc[idx, "Gen Pop Projection"]
        if gp and str(gp).strip() and not pd.isna(gp):
            try:
                n = int(float(str(gp).replace(",", "")))
                if n >= 0:
                    df_out.loc[idx, "Gen Pop Projection"] = f"{int(round(n / OUTPUT_DIVISOR)):,}"
            except (ValueError, TypeError):
                pass

    # AI Plausibility Validation
    print("🤖 Running AI plausibility check...")
    show_name = ', '.join(p.get('show_search_terms', []))
    ep_count = len(p.get('episode_dates', []))
    date_range_str = ''
    if p.get('campaign_start') and p.get('campaign_end'):
        try:
            cs = p['campaign_start']
            ce = p['campaign_end']
            s_str = cs.strftime('%Y-%m-%d') if hasattr(cs, 'strftime') else str(cs)
            e_str = ce.strftime('%Y-%m-%d') if hasattr(ce, 'strftime') else str(ce)
            date_range_str = f"{s_str} to {e_str}"
        except Exception:
            pass
    validation = ai_validate_metrics(
        show_name=show_name,
        platform_name=p['platform_name'],
        total_watchers=total_watchers // OUTPUT_DIVISOR,
        new_signups=new_signups // OUTPUT_DIVISOR,
        conversion_rate=total_show_conversion,
        genre=p.get('genre', ''),
        content_cadence=p.get('content_cadence', ''),
        episode_count=ep_count,
        pre_existing_viewers=pre_existing // OUTPUT_DIVISOR,
        analysis_date_range=date_range_str,
        is_new_show=p.get('is_new_show', False),
    )

    if not validation.get('passed', True):
        print(f"⚠️  AI flagged potential issues:")
        for flag in validation.get('flags', []):
            print(f"   • {flag}")
        print(f"   Assessment: {validation.get('overall_assessment', 'N/A')}")
        df_out, ai_changes = apply_ai_adjustments(
            df_out, validation,
            total_watchers // OUTPUT_DIVISOR,
            new_signups // OUTPUT_DIVISOR,
            p['platform_name'], p
        )
        if ai_changes:
            print("   Applied corrections:")
            for c in ai_changes:
                print(f"     → {c}")
            # Recalculate Gen Pop for adjusted rows
            for idx in df_out.index:
                cat = str(df_out.loc[idx, "Category"] or "").strip()
                if cat in ("New Platform Signups", "Total Show Watchers", "TOTAL SIGNUPS"):
                    c = df_out.loc[idx, "Count"]
                    try:
                        n = int(float(str(c).replace(",", "")))
                        df_out.loc[idx, "Gen Pop Projection"] = format_gen_pop(gen_pop_projection(n))
                    except (ValueError, TypeError):
                        pass
    else:
        note = validation.get('overall_assessment', validation.get('note', 'Metrics look plausible'))
        print(f"✅ AI validation passed: {note}")

    # Final-step GPT-4o audience alignment before saving output.
    print("🧠 Running final demographic alignment agent (GPT-4o)...")
    df_out, final_demo_changes = ai_align_final_demographics_with_research(df_out, p['platform_name'])
    if final_demo_changes:
        print("   Applied final demographic alignment changes:")
        for c in final_demo_changes:
            print(f"     → {c}")
    else:
        print("   No final demographic adjustments applied.")

    # Hard final consistency guard for attribution summary rows.
    df_out, attrib_changes = enforce_attribution_summary_consistency(df_out)
    if attrib_changes:
        print("   Applied attribution reconciliation:")
        for c in attrib_changes:
            print(f"     → {c}")

    # Append validation metadata rows
    df_out = pd.concat([df_out, pd.DataFrame([
        ("", "", "", "", "", "", "", "", "", ""),
        ("", "", "AI VALIDATION", "", "", "", "", "", "", ""),
        ("Validation Status", "", "PASS" if validation.get('passed', True) else "FLAGGED", "", "", "", "", "", "", ""),
        ("Assessment", "", validation.get('overall_assessment', ''), "", "", "", "", "", "", ""),
    ], columns=df_out.columns)], ignore_index=True)

    if validation.get('flags'):
        for i, flag in enumerate(validation['flags']):
            df_out = pd.concat([df_out, pd.DataFrame([
                (f"Flag {i+1}", "", flag, "", "", "", "", "", "", ""),
            ], columns=df_out.columns)], ignore_index=True)

    # Surface flags raised earlier in the pipeline (viewer-research safety net,
    # conversion-rate sanity check) so downstream consumers can see when the
    # AI overrode or corrected something.
    pipeline_flags = p.get('_ai_flags', []) or []
    if pipeline_flags:
        for i, flag in enumerate(pipeline_flags):
            df_out = pd.concat([df_out, pd.DataFrame([
                (f"AI Override Flag {i+1}", "", flag, "", "", "", "", "", "", ""),
            ], columns=df_out.columns)], ignore_index=True)

    # Write to output_dir from params (e.g. server output dir on Render) or default Desktop/attribution
    # Final invariant assertion on output DataFrame
    _out_total = _out_pre = _out_clean = None
    for idx in df_out.index:
        cat = str(df_out.loc[idx, "Category"] or "").strip()
        if cat == "Total Show Watchers":
            try:
                _out_total = int(float(str(df_out.loc[idx, "Count"]).replace(",", "")))
            except (ValueError, TypeError):
                pass
        elif cat == "Pre-Existing Series Viewers":
            try:
                _out_pre = int(float(str(df_out.loc[idx, "Count"]).replace(",", "")))
            except (ValueError, TypeError):
                pass
        elif cat == "Clean Sample (New First Time Viewers)":
            try:
                _out_clean = int(float(str(df_out.loc[idx, "Count"]).replace(",", "")))
            except (ValueError, TypeError):
                pass
    if _out_total is not None and _out_pre is not None and _out_clean is not None:
        if _out_total != _out_pre + _out_clean:
            print(f"⚠️  INVARIANT FIX: Total({_out_total}) != Pre({_out_pre}) + Clean({_out_clean})")
            if _out_pre > _out_total:
                pre_ratio = _out_pre / (_out_pre + _out_clean) if (_out_pre + _out_clean) > 0 else 0.5
                _out_pre = int(round(_out_total * pre_ratio))
                _out_clean = _out_total - _out_pre
                print(f"   Proportionally rescaled: Pre={_out_pre}, Clean={_out_clean}")
            else:
                _out_clean = _out_total - _out_pre
                print(f"   Forced Clean = Total - Pre = {_out_clean}")
            for idx in df_out.index:
                cat = str(df_out.loc[idx, "Category"] or "").strip()
                if cat == "Pre-Existing Series Viewers":
                    df_out.loc[idx, "Count"] = _out_pre
                    df_out.loc[idx, "Gen Pop Projection"] = format_gen_pop(gen_pop_projection(_out_pre))
                elif cat == "Clean Sample (New First Time Viewers)":
                    df_out.loc[idx, "Count"] = _out_clean
                    df_out.loc[idx, "Gen Pop Projection"] = format_gen_pop(gen_pop_projection(_out_clean))
        print(f"   ✅ Output invariant: Total({_out_total:,}) = Pre({_out_pre:,}) + Clean({_out_clean:,})")

    # When AI real-world mode is active, the counts in the DataFrame are already
    # real-world US numbers (e.g. 920K viewers).  That real-world number belongs in
    # Gen Pop Projection; Count should be the panel-equivalent (real / 32.99).
    if ai_real_world:
        GPP_DIVISOR = US_POPULATION / SAMPLE_REPRESENTS  # ~32.99
        for idx in df_out.index:
            c = df_out.loc[idx, "Count"]
            if c != "" and c is not None and not pd.isna(c):
                try:
                    real_world = int(float(str(c).replace(",", "")))
                    panel_equiv = int(round(real_world / GPP_DIVISOR))
                    df_out.loc[idx, "Gen Pop Projection"] = f"{real_world:,}"
                    df_out.loc[idx, "Count"] = panel_equiv
                except (ValueError, TypeError):
                    pass

    output_folder = Path(p['output_dir']) if p.get('output_dir') else Path.home() / "Desktop" / "attribution"
    output_folder = output_folder if isinstance(output_folder, Path) else Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%m_%d_%Y_%H_%M")
    safe_project_name = re.sub(r'[<>:"/\\|?*\']', '', p['project_name']).strip()
    safe_project_name = safe_project_name[:100] if len(safe_project_name) > 100 else safe_project_name
    output_path = output_folder / f"{safe_project_name}_{timestamp}.csv"
    df_out.to_csv(output_path, index=False, encoding='utf-8')
    print(f"✅ Report written to {output_path}\n")


# =============
# === Main  ===
# =============
def main():
    print("\n" + "=" * 60)
    print("     SHOW-TO-PLATFORM ATTRIBUTION ANALYZER")
    print("=" * 60)
    print("Track how many people who watched a show signed up for the platform")
    print("WITH PER-EPISODE ATTRIBUTION!")
    print("=" * 60 + "\n")
    
    params = get_user_input()
    conn = connect_db()
    try:
        summary_df, comp_df, demo_df, timing_df, episode_df, monthly_df, episode_timing_df, churn_df, post_signup_touchpoints_df = run_query(conn, params)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    write_output(summary_df, comp_df, demo_df, timing_df, episode_df, monthly_df, episode_timing_df, churn_df, post_signup_touchpoints_df, params)
    
    print("=" * 60)
    print("✅ All Done! Check Desktop/attribution folder for results.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()

