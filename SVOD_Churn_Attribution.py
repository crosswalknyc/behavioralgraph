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


# =========================
# === Snowflake creds ====
# =========================
# ⚠️ Hard-coded credentials (note: insecure for production use)
SNOWFLAKE_USER = "hotdogsandcheezeits"
SNOWFLAKE_PASSWORD = "S3nshine2282!"
SNOWFLAKE_ACCOUNT = "qsodrkt-hgb46445"
SNOWFLAKE_WAREHOUSE = "SUBSCRIBERIQ"  # 6XL warehouse for Subscriber IQ pipeline
SNOWFLAKE_DATABASE = "PROCESSEDCLICKSTREAM"
SNOWFLAKE_SCHEMA = "PUBLIC"


# =========================
# === Snowflake connect ===
# =========================
def connect_snowflake():
    print("Connecting to ClickHouse...")
    conn = connect_clickhouse()
    print("Connected to ClickHouse.")
    return conn


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
    "Movie - Drama",
    "Movie - Family & Animation",
    "Movie - Action",
    "Movie - Comedy",
    "Movie - Horror",
    "Movie - Romcom",
    "Movie - Documentary",
    "Movie - SciFi",
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
        
        # Sort episodes chronologically by air_date so campaign date range is correct
        episode_dates.sort(key=lambda ep: ep['air_date'])
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

    # Ensure episode dates are sorted chronologically
    if episode_dates:
        episode_dates.sort(key=lambda ep: ep['air_date'])
        p['episode_dates'] = episode_dates

    # Ensure campaign_start <= campaign_end (swap if reversed)
    if p['campaign_start'] > p['campaign_end']:
        print(f"⚠️  Date range reversed: {p['campaign_start'].date()} > {p['campaign_end'].date()}, swapping...")
        p['campaign_start'], p['campaign_end'] = p['campaign_end'], p['campaign_start']

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
        # Done in two steps so we don't reference an aggregate (MIN) from a correlated
        # subquery's WHERE clause — Snowflake allowed it, ClickHouse rejects it with
        # ILLEGAL_AGGREGATION. Step A computes FIRST_PLATFORM_VISIT per user, step B
        # joins that to the episode table (no correlated subquery needed).
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
            CREATE OR REPLACE TEMP TABLE TEMP_NEW_PLATFORM_SIGNUPS AS
            SELECT
                fpv.UID,
                fpv.FIRST_SHOW_WATCH,
                fpv.FIRST_PLATFORM_VISIT,
                fpv.DAYS_TO_SIGNUP,
                epi_max.ATTRIBUTED_EPISODE
            FROM TEMP_FIRST_PLATFORM_VISIT fpv
            LEFT JOIN (
                SELECT fpv2.UID, MAX(epi.EPISODE_NUM) AS ATTRIBUTED_EPISODE
                FROM TEMP_FIRST_PLATFORM_VISIT fpv2
                INNER JOIN TEMP_SHOW_WATCHERS_WITH_EPISODES epi
                    ON epi.UID = fpv2.UID
                WHERE epi.VISIT_TS < fpv2.FIRST_PLATFORM_VISIT
                GROUP BY fpv2.UID
            ) epi_max ON epi_max.UID = fpv.UID
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
    # Apply same sample size inflation as bg.py: try 55x, 25x, 5x, 2.5x, or 1x (whichever keeps result ≤10M)
    raw_show_watchers = 0
    if 'TOTAL_SHOW_WATCHERS' in df_summary.columns and not pd.isna(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS']):
        raw_show_watchers = int(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS'])
        inflation_factor = calculate_inflation_factor(raw_show_watchers)
        inflated_watchers = int(raw_show_watchers * inflation_factor)
        inflated_watchers = min((inflated_watchers // 10) * 10, SAMPLE_REPRESENTS)
        df_summary.loc[0, 'TOTAL_SHOW_WATCHERS'] = inflated_watchers
        print(f"   📊 Raw show watchers: {raw_show_watchers:,}")
        print(f"   📊 Inflation factor: {inflation_factor}x (chosen so result ≤ 10M)")
        print(f"   📊 Inflated show watchers: {inflated_watchers:,}")
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
        
        # Build VALUES clause for all episodes (Snowflake syntax)
        values_clause = ', '.join([f'({ep_num})' for ep_num in all_episode_nums])
        
        # Build query that includes ALL episodes, even those with 0 signups
        episode_attribution_query = f"""
        WITH all_episodes AS (
            SELECT EPISODE_NUM
            FROM VALUES {values_clause} AS t(EPISODE_NUM)
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
    
    # Use same inflation factor as TOTAL_SHOW_WATCHERS for ALL counts to preserve ratios
    inflated_total_watchers = int(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS']) if ('TOTAL_SHOW_WATCHERS' in df_summary.columns and not pd.isna(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS'])) else 0
    raw_new_signups = int(df_summary.loc[0, 'NEW_SIGNUPS']) if ('NEW_SIGNUPS' in df_summary.columns and not pd.isna(df_summary.loc[0, 'NEW_SIGNUPS'])) else 0
    
    # Use the SAME inflation factor that was applied to total_show_watchers
    inflation_factor = calculate_inflation_factor(raw_show_watchers) if raw_show_watchers > 0 else 55
    print(f"🔥 Applying {inflation_factor}x inflation factor to all count-based numbers (consistent with total show watchers)...\n")
    
    # Inflate NEW_SIGNUPS with consistent inflation factor
    if 'NEW_SIGNUPS' in df_summary.columns and raw_new_signups > 0:
        inflated_new_signups = min(int(raw_new_signups * inflation_factor), SAMPLE_REPRESENTS)
        df_summary.loc[0, 'NEW_SIGNUPS'] = inflated_new_signups
        
        # Recalculate TOTAL_SHOW_CONVERSION_RATE from projected (Gen Pop) values
        tw_proj = gen_pop_projection(inflated_total_watchers)
        nps_proj = gen_pop_projection(inflated_new_signups)
        if tw_proj > 0:
            df_summary.loc[0, 'TOTAL_SHOW_CONVERSION_RATE'] = round((nps_proj / tw_proj) * 100.0, 2)
    else:
        inflated_new_signups = 0
    
    # Inflate other summary counts with same inflation factor
    for col in ['PRE_EXISTING_USERS', 'CLEAN_SAMPLE_SIZE']:
        if col in df_summary.columns:
            for idx in df_summary.index:
                raw_val = int(df_summary.loc[idx, col]) if not pd.isna(df_summary.loc[idx, col]) else 0
                if raw_val > 0:
                    df_summary.loc[idx, col] = min(int(raw_val * inflation_factor), SAMPLE_REPRESENTS)
    
    # Clean Conversion Rate = New Signups / Clean Sample Size
    if 'NEW_SIGNUPS' in df_summary.columns and 'CLEAN_SAMPLE_SIZE' in df_summary.columns:
        current_signups = int(df_summary.loc[0, 'NEW_SIGNUPS']) if not pd.isna(df_summary.loc[0, 'NEW_SIGNUPS']) else 0
        clean_sample = int(df_summary.loc[0, 'CLEAN_SAMPLE_SIZE']) if not pd.isna(df_summary.loc[0, 'CLEAN_SAMPLE_SIZE']) else 0
        if clean_sample > 0:
            df_summary.loc[0, 'CLEAN_CONVERSION_RATE'] = round((current_signups * 100.0) / clean_sample, 2)
    
    # Inflate demographic counts with same inflation factor
    if 'COUNT' in df_demo.columns:
        for idx in df_demo.index:
            raw_val = int(df_demo.loc[idx, 'COUNT']) if not pd.isna(df_demo.loc[idx, 'COUNT']) else 0
            if raw_val > 0:
                df_demo.loc[idx, 'COUNT'] = min(int(raw_val * inflation_factor), SAMPLE_REPRESENTS)

    # AI demographic validation: correct AGE/GENDER distributions using web research
    show_name = ', '.join(p.get('show_search_terms', []))
    print("🧬 Running AI demographic validation...")
    df_demo, demo_changes = ai_validate_demographics(
        show_name, p['platform_name'], df_demo, inflated_new_signups
    )
    if demo_changes:
        print("   Applied demographic corrections:")
        for dc in demo_changes:
            print(f"     → {dc}")
    else:
        print("   ✅ Demographics look plausible (no corrections needed)")

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
        f'Search for real viewership data for the TV show/series "{search_query}". '
        f'I need UNIQUE VIEWER COUNTS (number of individual people who watched), '
        f'NOT viewing minutes or hours.\n\n'
        f'PRIORITY ORDER for data sources:\n'
        f'1. Nielsen "unique audience" or "reach" numbers (number of distinct people/households)\n'
        f'2. Samba TV household reach data\n'
        f'3. Platform-reported "accounts that watched" or "households that watched"\n'
        f'4. Third-party estimates of unique viewers from Parrot Analytics, Antenna, etc.\n'
        f'5. LAST RESORT: viewing hours/minutes (note clearly that this is NOT unique viewers)\n\n'
        f'Report:\n'
        f'- Total unique US viewers/households for this show/season (number of people)\n'
        f'- If only global numbers exist, report those AND estimate US share\n'
        f'- Premiere episode unique viewers\n'
        f'- Which platform it aired on and when\n'
        f'- Platform subscriber count at that time\n\n'
        f'WARNING: Netflix, Disney+, etc. often report "viewing hours" which is NOT the same as '
        f'unique viewers. One person watching 10 episodes = 10 hours but only 1 unique viewer. '
        f'If only hours/minutes are available, say so explicitly and do NOT convert them to '
        f'unique viewers — that conversion is unreliable.\n\n'
        f'Cite specific sources. Be concise — just the key numbers.'
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


def ai_validate_metrics(show_name, platform_name, total_watchers, new_signups,
                        conversion_rate, genre='', content_cadence='',
                        episode_count=0, pre_existing_viewers=0,
                        analysis_date_range='', is_new_show=False):
    """
    Use GPT-4o with web-researched viewership data to validate whether
    Total Show Watchers, New Platform Signups, and conversion rates are
    plausible. Adjusts only downward when numbers appear inflated.
    """
    # Hard sanity check: catch obviously impossible numbers before calling AI
    if conversion_rate > 30 and total_watchers > 0:
        plat_info = _get_platform_info(platform_name)
        reasonable_conv = min(5.0, 100.0 - plat_info['pct'])
        suggested_signups = int(total_watchers * reasonable_conv / 100.0)
        return {
            'passed': False,
            'watchers_plausible': True,
            'watchers_note': 'Watchers not evaluated — conversion rate is the primary issue',
            'signups_plausible': False,
            'signups_note': f'Conversion rate of {conversion_rate:.1f}% is impossible. '
                           f'This likely means the analysis window is too wide, capturing '
                           f'coincidental signups unrelated to the show.',
            'conversion_plausible': False,
            'conversion_note': f'{conversion_rate:.1f}% conversion is physically impossible — '
                              f'most viewers already had the platform to watch the show.',
            'flags': [f'conversion rate {conversion_rate:.1f}% is impossible',
                     'analysis window likely too wide',
                     'pre-existing viewer filter may have no data'],
            'suggested_conversion_range': [0.5, reasonable_conv],
            'suggested_watchers_range_panel': [total_watchers, total_watchers],
            'suggested_signups_range_panel': [suggested_signups // 2, suggested_signups],
            'overall_assessment': f'Conversion rate of {conversion_rate:.1f}% is clearly wrong. '
                                 f'Narrow the analysis window to the actual season dates.'
        }

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

    projection_mult = US_POPULATION / SAMPLE_REPRESENTS
    panel_div = SAMPLE_REPRESENTS / US_POPULATION

    research_block = ""
    if viewership_research:
        research_block = f"""
=== REAL-WORLD VIEWERSHIP DATA (from web search) ===
{viewership_research}
"""

    prompt = f"""You are an expert analyst validating SVOD subscriber acquisition data. You have access to REAL viewership research and must use it to validate our numbers.

=== HOW OUR DATA WORKS ===
We track UNIQUE INDIVIDUAL US VIEWERS (not minutes, not hours — actual people) from a panel of {SAMPLE_REPRESENTS:,} people representing the US population of {US_POPULATION:,}.

CONVERSION FORMULAS:
- Panel count × {projection_mult:.2f} = US Gen Pop Projection
- Real-world US unique viewers / {projection_mult:.2f} = expected panel count

WORKED EXAMPLE — you MUST follow this math exactly:
  If research says a show had 50,000,000 unique US viewers:
  Panel count = 50000000 / {projection_mult:.2f} = {int(50000000 / projection_mult):,}
  So suggested_watchers_range_panel should be around [{int(50000000 / projection_mult)}, {int(50000000 / projection_mult)}]
  NOT [1515, 1515] — that would project to only ~50K viewers, not 50M.
  The panel numbers should be in the HUNDREDS OF THOUSANDS for shows with millions of viewers.

IMPORTANT RULES FOR INTERPRETING RESEARCH DATA:
- If research reports GLOBAL viewers, estimate US share (typically 30-50% for English-language content).
- If research reports viewing MINUTES/HOURS instead of unique viewers, DO NOT blindly divide
  to get unique viewers. That math is unreliable because one viewer watches many hours.
  Instead, look for any separate unique viewer/reach/household data. If ONLY hours data exists,
  note this limitation and use your general knowledge of the show's popularity to estimate
  a reasonable unique viewer count. For reference: a top-10 Netflix show typically reaches
  20-60 million unique US viewers; a mid-tier show 5-15M; a niche show 1-5M.

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

OUR DATA:
- Total Show Watchers (panel): {total_watchers:,.0f} → US Gen Pop Projected: {watchers_projected:,.0f}
- Pre-Existing Platform Viewers (panel): {pre_existing_viewers:,.0f}
- New Platform Signups (panel): {new_signups:,.0f} → US Gen Pop Projected: {signups_projected:,.0f}
- Total Show Conversion Rate: {conversion_rate:.2f}%
{research_block}
=== PHASE A: VALIDATE TOTAL SHOW WATCHERS ===
1. From the research, determine how many UNIQUE US VIEWERS this show/season actually had.
   - If research says X million unique US viewers, our Gen Pop Projected number should be
     close to X million. Convert to panel: X_million / {projection_mult:.2f} = target panel count.
   - If research only has global numbers, estimate US share (typically 30-50% for US-centric
     English-language content on major platforms, lower for non-English).
   - If research only has viewing minutes/hours, note this and estimate unique viewers
     cautiously (e.g. 1B minutes over 10 episodes ÷ ~6 hrs avg watch = ~17M unique viewers).
2. Our projected number ({watchers_projected:,.0f}) should be in the right ballpark of reality.
   If it is far off, flag it and suggest the correct panel number.
3. CRITICAL: If the real viewer count is X and our projection is 2X or higher, set
   "watchers_plausible" to FALSE and "passed" to FALSE. Do NOT say "passed: true"
   while simultaneously suggesting a different number. If your suggested panel range
   is significantly different from the actual {total_watchers:,.0f}, it MUST fail.

=== PHASE B: VALIDATE NEW SIGNUPS & REACTIVATIONS ===
"New Platform Signups" includes BOTH brand-new subscribers AND reactivated/dormant accounts.
The pipeline later splits these into "Attributed Signups" (watched then signed up) and
"Dormant to Reactive" (reactivated lapsed accounts). But the TOTAL must make sense first.

REALITY CHECK: Most people who watch a show ALREADY HAVE the platform. The conversion rate
(new signups / total watchers) should reflect this reality, but use the REAL DATA as your guide.

Use platform penetration as a baseline anchor — higher penetration = fewer potential new subs.
But let the actual data tell you what's reasonable. A breakout show on any platform can drive
higher-than-typical conversion. Just catch numbers that are clearly absurd (e.g. 20-30% on
Netflix is impossible since almost everyone already subscribes).

Your job is NOT to force conversion into a narrow band. Your job is to catch obvious inflation.
If the data supports a 3-5% conversion for a specific situation, that could be fine. But if
the numbers imply 15-30% of watchers are new subscribers on a dominant platform, that is
clearly wrong and should be adjusted down.

If signups appear clearly inflated, suggest a lower number.
NEVER suggest increasing signups — only reduce if inflated.

=== PHASE C: TIME FRAME & CONTENT SCALE ===
- Short windows (1-2 weeks) naturally produce lower numbers. Do NOT flag low numbers.
- Minor titles, stand-up specials, indie films have modest numbers — that is expected.
- Only flag numbers that are clearly impossible or significantly inflated.

=== ABSOLUTE RULES (override everything else) ===
- A conversion rate above 20% is ALWAYS implausible on ANY platform. Flag it immediately.
- A conversion rate above 50% is physically impossible — it implies most viewers didn't have
  the platform, which contradicts the fact that they watched the show ON the platform.
- If the analysis date range spans more than 1 year, the data is almost certainly capturing
  coincidental signups unrelated to the show. Flag this and suggest much lower signups.
- If Pre-Existing Viewers is 0 but Total Watchers is high, the exclusion window likely had
  no data — meaning EVERYONE is counted as "new" when most were actually pre-existing. Flag this.

Show your math in the notes. Respond with ONLY a JSON object — no markdown code fences, no comments, no trailing commas, no text outside the JSON.
CRITICAL: Do NOT use commas in numbers inside JSON. Write 50000 not 50,000. Write 1500000 not 1,500,000.
{{"passed": true, "watchers_plausible": true, "watchers_note": "math here", "signups_plausible": true, "signups_note": "reasoning", "conversion_plausible": true, "conversion_note": "reasoning", "flags": [], "suggested_conversion_range": [0.0, 0.0], "suggested_watchers_range_panel": [0, 0], "suggested_signups_range_panel": [0, 0], "overall_assessment": "summary"}}"""

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
        import re as _re
        raw = _re.sub(r',\s*}', '}', raw)
        raw = _re.sub(r',\s*]', ']', raw)
        raw = _re.sub(r'//[^\n]*', '', raw)
        raw = _re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))', '', raw)
        result = json.loads(raw)

        # Post-AI sanity check: if AI suggests significantly different numbers
        # but still said "passed", override to fail
        suggested_w = result.get('suggested_watchers_range_panel', [])
        if (len(suggested_w) == 2 and suggested_w[0] and suggested_w[1]
                and total_watchers > 0):
            suggested_mid = (suggested_w[0] + suggested_w[1]) / 2.0
            if suggested_mid > 0:
                ratio = total_watchers / suggested_mid
                if ratio > 1.5 or ratio < 0.5:
                    result['passed'] = False
                    if result.get('watchers_plausible', True):
                        result['watchers_plausible'] = False
                        result['watchers_note'] = (
                            result.get('watchers_note', '') +
                            f' [Override: our panel {total_watchers:,} is {ratio:.1f}x the '
                            f'suggested {int(suggested_mid):,}]'
                        )
                    if 'watchers significantly off' not in str(result.get('flags', [])):
                        result.setdefault('flags', []).append(
                            f'watchers {ratio:.1f}x off from research-based estimate')

        suggested_s = result.get('suggested_signups_range_panel', [])
        if (len(suggested_s) == 2 and suggested_s[0] and suggested_s[1]
                and new_signups > 0):
            suggested_mid_s = (suggested_s[0] + suggested_s[1]) / 2.0
            if suggested_mid_s > 0 and new_signups / suggested_mid_s > 2.0:
                result['passed'] = False
                result['signups_plausible'] = False

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
                for idx in df_out.index:
                    cat = str(df_out.loc[idx, "Category"] or "").strip()
                    if cat == "Total Show Watchers":
                        old_val = df_out.loc[idx, "Count"]
                        df_out.loc[idx, "Count"] = target_watchers
                        df_out.loc[idx, "Gen Pop Projection"] = format_gen_pop(gen_pop_projection(target_watchers))
                        changes.append(f"Total Show Watchers: {old_val} → {target_watchers} (anchored to real viewership data)")

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


# =========================================
# === AI Demographic Validation (SVOD)  ===
# =========================================
_demo_research_cache = {}

def ai_validate_demographics(show_name, platform_name, df_demo, new_signups):
    """
    Use GPT-4o with web research to validate and correct demographic distributions
    for new platform signups attributed to a show. Returns corrected df_demo with
    realistic AGE and GENDER distributions based on the show's known audience.
    """
    if df_demo.empty or new_signups <= 0:
        return df_demo, []

    try:
        from openai import OpenAI
        client = OpenAI()
    except Exception:
        return df_demo, []

    cache_key = show_name.strip().lower()
    if cache_key in _demo_research_cache:
        research = _demo_research_cache[cache_key]
    else:
        clean = show_name.replace('_', ' ').replace('-', ' ').strip()
        season_match = re.search(r'(?i)(season\s*\d+|s\d+)', clean)
        if season_match:
            season_str = season_match.group(0)
            show_part = clean[:season_match.start()].strip().rstrip(' -')
            search_query = f'{show_part} {season_str}'
        else:
            search_query = clean

        research_prompt = (
            f'Search for the real audience demographics of the TV show "{search_query}". '
            f'I need the actual demographic breakdown of viewers who watch this show.\n\n'
            f'Report:\n'
            f'- Gender split (% male vs female vs other)\n'
            f'- Age distribution (% in each bracket: under 18, 18-24, 25-34, 35-44, 45-54, 55-64, 65+)\n'
            f'- Cite Nielsen, Samba TV, YouGov, Morning Consult, or other audience measurement sources\n'
            f'- Note if this show skews particularly young/old, male/female\n'
            f'- Platform it airs on: {platform_name}\n\n'
            f'Be specific with percentages. Cite sources.'
        )
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-search-preview",
                messages=[{"role": "user", "content": research_prompt}],
                web_search_options={"search_context_size": "medium"},
            )
            research = resp.choices[0].message.content.strip() if resp.choices else ""
        except Exception:
            research = ""
        _demo_research_cache[cache_key] = research

    if not research:
        return df_demo, []

    current_age = {}
    current_gender = {}
    for _, row in df_demo.iterrows():
        cat = str(row.get('CATEGORY', '')).strip()
        val = str(row.get('VALUE', '')).strip()
        count = int(row['COUNT']) if not pd.isna(row.get('COUNT')) else 0
        if cat == 'AGE':
            current_age[val] = count
        elif cat == 'GENDER':
            current_gender[val] = count

    age_total = sum(current_age.values())
    gender_total = sum(current_gender.values())

    age_list = ', '.join(f'{k}: {v} ({v*100//age_total}%)' for k, v in current_age.items()) if age_total else 'none'
    gender_list = ', '.join(f'{k}: {v} ({v*100//gender_total}%)' for k, v in current_gender.items()) if gender_total else 'none'

    correction_prompt = (
        f'You are a demographic data analyst. Based on real audience research for "{show_name}" '
        f'on {platform_name}, correct the demographic distribution of new platform signups.\n\n'
        f'RESEARCH DATA:\n{research}\n\n'
        f'OUR CURRENT DATA (panel counts):\n'
        f'AGE (total {age_total}): {age_list}\n'
        f'GENDER (total {gender_total}): {gender_list}\n\n'
        f'Using the research, provide corrected PERCENTAGE distributions that reflect '
        f'the real audience of this show. The corrected percentages must sum to 100% for '
        f'each category.\n\n'
        f'IMPORTANT RULES:\n'
        f'- Use the research data to determine realistic splits\n'
        f'- These are people who signed up for {platform_name} because of this show, '
        f'so the demographics should match the show\'s known audience profile\n'
        f'- If the show skews female, female % should be higher than male\n'
        f'- If the show skews young, younger age brackets should dominate\n'
        f'- Keep Non-Binary/Trans/Other at realistic small percentages (1-3% total)\n'
        f'- Keep "Prefer Not to Say" under 1%\n\n'
        f'Return ONLY a JSON object with this exact structure (no comments, no commas in numbers):\n'
        f'{{\n'
        f'  "age": {{"17 and Under": <pct>, "18-24": <pct>, "25-34": <pct>, "35-44": <pct>, '
        f'"45-54": <pct>, "55-64": <pct>, "65 or Older": <pct>}},\n'
        f'  "gender": {{"Male": <pct>, "Female": <pct>, "Non-Binary": <pct>, '
        f'"Trans Male": <pct>, "Trans Female": <pct>, "Prefer Not to Say": <pct>}},\n'
        f'  "reasoning": "brief explanation of why these percentages match the show"\n'
        f'}}\n'
        f'Percentages should be numbers (e.g. 35.0 not "35%"). Each category must sum to ~100.'
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": correction_prompt}],
            temperature=0.2,
        )
        raw = resp.choices[0].message.content.strip()
    except Exception:
        return df_demo, []

    try:
        start = raw.find('{')
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
        result = json.loads(raw_json)
    except Exception:
        return df_demo, []

    changes = []
    age_corrections = result.get('age', {})
    gender_corrections = result.get('gender', {})
    reasoning = result.get('reasoning', '')

    if age_corrections and age_total > 0:
        assigned = 0
        items = list(age_corrections.items())
        for i, (bracket, pct) in enumerate(items):
            if i == len(items) - 1:
                new_count = age_total - assigned
            else:
                new_count = int(round(age_total * float(pct) / 100.0))
                assigned += new_count
            for idx in df_demo.index:
                if str(df_demo.loc[idx, 'CATEGORY']).strip() == 'AGE' and str(df_demo.loc[idx, 'VALUE']).strip() == bracket:
                    old_count = int(df_demo.loc[idx, 'COUNT']) if not pd.isna(df_demo.loc[idx, 'COUNT']) else 0
                    df_demo.loc[idx, 'COUNT'] = new_count
                    df_demo.loc[idx, 'PERCENTAGE'] = round(new_count * 100.0 / age_total, 1) if age_total > 0 else 0
                    if old_count != new_count:
                        changes.append(f"AGE {bracket}: {old_count} → {new_count}")

    if gender_corrections and gender_total > 0:
        assigned = 0
        items = list(gender_corrections.items())
        for i, (bracket, pct) in enumerate(items):
            if i == len(items) - 1:
                new_count = gender_total - assigned
            else:
                new_count = int(round(gender_total * float(pct) / 100.0))
                assigned += new_count
            for idx in df_demo.index:
                if str(df_demo.loc[idx, 'CATEGORY']).strip() == 'GENDER' and str(df_demo.loc[idx, 'VALUE']).strip() == bracket:
                    old_count = int(df_demo.loc[idx, 'COUNT']) if not pd.isna(df_demo.loc[idx, 'COUNT']) else 0
                    df_demo.loc[idx, 'COUNT'] = new_count
                    df_demo.loc[idx, 'PERCENTAGE'] = round(new_count * 100.0 / gender_total, 1) if gender_total > 0 else 0
                    if old_count != new_count:
                        changes.append(f"GENDER {bracket}: {old_count} → {new_count}")

    if reasoning:
        changes.append(f"Reasoning: {reasoning}")

    return df_demo, changes


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
    # If there are labels with 0 and we still have room due to rounding, leave as-is;
    # downstream count reconciliation ensures integer totals align exactly.
    return norm


def _apply_pct_plan_to_df_out(df_out, indices, pct_plan, total_count):
    """
    Apply percentage plan to a set of df_out row indices.
    Ensures integer counts sum exactly to total_count.
    """
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
        old_count = 0
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

    # Pull show title from final output rows (Show/Content Tracked row).
    show_name = ""
    for idx in df_out.index:
        cat = str(df_out.loc[idx, "Category"] or "").strip()
        if cat == "Show/Content Tracked":
            show_name = str(df_out.loc[idx, "Count Label"] or "").strip()
            break
    if not show_name:
        return df_out, []

    # Pull New Platform Signups count from final output rows.
    nps_count = 0
    for idx in df_out.index:
        cat = str(df_out.loc[idx, "Category"] or "").strip()
        if cat == "New Platform Signups":
            try:
                nps_count = int(float(str(df_out.loc[idx, "Count"]).replace(",", "")))
            except (ValueError, TypeError):
                nps_count = 0
            break
    if nps_count <= 0:
        return df_out, []

    # Locate final AGE/GENDER demographic rows in output frame.
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

    # Build current distribution summary for prompt grounding.
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

    # Step 1: external research (web-enabled model).
    research = ""
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

    # Step 2: GPT-4o correction plan constrained to existing labels.
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
        changes.extend(_apply_pct_plan_to_df_out(
            df_out,
            section_rows["AGE"],
            parsed.get("age", {}),
            nps_count
        ))
    if section_rows["GENDER"]:
        skew_hint = _detect_gender_skew_hint(parsed.get("gender_skew"), research)
        gender_plan = parsed.get("gender", {}) or {}
        gender_plan, skew_changes = _enforce_gender_skew_in_plan(gender_plan, gender_labels, skew_hint)
        changes.extend(skew_changes)
        changes.extend(_apply_pct_plan_to_df_out(
            df_out,
            section_rows["GENDER"],
            gender_plan,
            nps_count
        ))

    rationale = str(parsed.get("reasoning") or "").strip()
    if rationale:
        changes.append(f"Final agent rationale: {rationale}")
    return df_out, changes


def enforce_attribution_summary_consistency(df_out):
    """
    Ensure Attributed Signups + Dormant to Reactive == New Platform Signups.
    If needed, proportionally scale attribution rows to exactly match NPS.
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
    # Total Show Conversion Rate = (projected NPS / projected Total Show Watchers) * 100 (use Gen Pop projected numbers)
    tw_projected = gen_pop_projection(total_watchers)
    nps_projected = gen_pop_projection(new_signups)
    total_show_conversion = round((nps_projected / tw_projected) * 100.0, 2) if tw_projected > 0 else 0.0
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
        ("Clean Conversion Rate", "", "", "", "", "", "", "", f"{clean_conversion:.2f}%", ""),
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
        dormant_to_reactive = new_signups - total_attributed_signups
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        rows.append(("", "", "ATTRIBUTION SUMMARY", "", "", "", "", "", "", ""))
        rows.append(("", "", "(% of Total Show Watchers)", "", "", "", "", "", "", ""))
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        attributed_pct_of_watchers = round((total_attributed_signups * 100.0) / total_watchers, 2) if total_watchers > 0 else 0.0
        attributed_genpop = format_gen_pop(gen_pop_projection(total_attributed_signups))
        rows.append(("Attributed Signups", "", total_attributed_signups, "signups", "", "(signed up then watched)", "", "", f"{attributed_pct_of_watchers}%", attributed_genpop))
        dormant_pct_of_watchers = round((dormant_to_reactive * 100.0) / total_watchers, 2) if total_watchers > 0 else 0.0
        dormant_genpop = format_gen_pop(gen_pop_projection(dormant_to_reactive))
        rows.append(("Dormant to Reactive", "", dormant_to_reactive, "signups", "", "(signed up before the exclusion period)", "", "", f"{dormant_pct_of_watchers}%", dormant_genpop))
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
    OUTPUT_DIVISOR = 10
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

    # Enforce attribution summary consistency: Attributed + Dormant = Total Signups = New Platform Signups
    # After OUTPUT_DIVISOR rounding, the sum can drift by ±1 — force alignment
    new_platform_row = None
    attributed_row = None
    dormant_row = None
    total_signups_row = None
    total_watchers_row = None
    for idx in df_out.index:
        cat = str(df_out.loc[idx, "Category"] or "").strip()
        if cat == "New Platform Signups":
            new_platform_row = idx
        elif cat == "Total Show Watchers":
            total_watchers_row = idx
        elif cat == "Attributed Signups":
            attributed_row = idx
        elif cat == "Dormant to Reactive":
            dormant_row = idx
        elif cat == "TOTAL SIGNUPS":
            total_signups_row = idx

    if new_platform_row is not None and attributed_row is not None and dormant_row is not None:
        try:
            nps_count = int(float(str(df_out.loc[new_platform_row, "Count"]).replace(",", "")))
            attr_count = int(float(str(df_out.loc[attributed_row, "Count"]).replace(",", "")))
            dorm_count_orig = int(float(str(df_out.loc[dormant_row, "Count"]).replace(",", "")))
            tw_count = int(float(str(df_out.loc[total_watchers_row, "Count"]).replace(",", ""))) if total_watchers_row is not None else 0

            raw_sum = attr_count + dorm_count_orig
            if raw_sum != nps_count and raw_sum > 0:
                attr_count = int(round(attr_count * nps_count / raw_sum))
                dorm_count = nps_count - attr_count
            else:
                dorm_count = dorm_count_orig

            df_out.loc[attributed_row, "Count"] = attr_count
            df_out.loc[dormant_row, "Count"] = dorm_count

            if total_signups_row is not None:
                df_out.loc[total_signups_row, "Count"] = nps_count

            attr_pct = round((attr_count * 100.0) / tw_count, 2) if tw_count > 0 else 0.0
            dorm_pct = round((dorm_count * 100.0) / tw_count, 2) if tw_count > 0 else 0.0
            total_pct = round((nps_count * 100.0) / tw_count, 2) if tw_count > 0 else 0.0
            df_out.loc[attributed_row, "Percentage"] = f"{attr_pct}%"
            df_out.loc[dormant_row, "Percentage"] = f"{dorm_pct}%"
            if total_signups_row is not None:
                df_out.loc[total_signups_row, "Percentage"] = f"{total_pct}%"

            attr_gp = format_gen_pop(gen_pop_projection(attr_count))
            dorm_gp = format_gen_pop(gen_pop_projection(dorm_count))
            total_gp = format_gen_pop(gen_pop_projection(nps_count))
            df_out.loc[attributed_row, "Gen Pop Projection"] = attr_gp
            df_out.loc[dormant_row, "Gen Pop Projection"] = dorm_gp
            if total_signups_row is not None:
                df_out.loc[total_signups_row, "Gen Pop Projection"] = total_gp
        except (ValueError, TypeError):
            pass

    # Enforce demographics: each category (AGE, GENDER) sums to New Platform Signups
    # Rescale counts proportionally, recalculate percentages and gen pop from final values
    if new_platform_row is not None:
        try:
            nps_final = int(float(str(df_out.loc[new_platform_row, "Count"]).replace(",", "")))
            if nps_final > 0:
                current_section = None
                section_rows = {'AGE': [], 'GENDER': []}
                for idx in df_out.index:
                    cat = str(df_out.loc[idx, "Category"] or "").strip()
                    clabel = str(df_out.loc[idx, "Count Label"] or "").strip()
                    if cat == "AGE":
                        current_section = "AGE"
                        continue
                    elif cat == "GENDER":
                        current_section = "GENDER"
                        continue
                    elif cat and clabel != "people":
                        if current_section in section_rows and cat not in ("", "DEMOGRAPHICS - New Signups"):
                            current_section = None
                    if current_section and clabel == "people":
                        section_rows[current_section].append(idx)

                for demo_cat, indices in section_rows.items():
                    if not indices:
                        continue
                    raw_counts = []
                    for idx in indices:
                        try:
                            c = int(float(str(df_out.loc[idx, "Count"]).replace(",", "")))
                        except (ValueError, TypeError):
                            c = 0
                        raw_counts.append(max(c, 0))

                    current_sum = sum(raw_counts)
                    if current_sum <= 0:
                        continue

                    scaled = []
                    running = 0
                    for i, c in enumerate(raw_counts):
                        if i == len(raw_counts) - 1:
                            scaled.append(nps_final - running)
                        else:
                            sc = int(round(c * nps_final / current_sum))
                            scaled.append(sc)
                            running += sc

                    pct_running = 0.0
                    for i, idx in enumerate(indices):
                        sc = scaled[i]
                        if i == len(indices) - 1:
                            pct = round(100.0 - pct_running, 1)
                        else:
                            pct = round(sc * 100.0 / nps_final, 1) if nps_final > 0 else 0.0
                            pct_running += pct
                        gp = format_gen_pop(gen_pop_projection(sc))
                        df_out.loc[idx, "Count"] = sc
                        df_out.loc[idx, "Percentage"] = f"{pct:.1f}%"
                        df_out.loc[idx, "Gen Pop Projection"] = gp
        except (ValueError, TypeError):
            pass

    # Hard sanity checks before AI validation
    date_range_days = 0
    if p.get('campaign_start') and p.get('campaign_end'):
        try:
            date_range_days = (p['campaign_end'] - p['campaign_start']).days
        except Exception:
            pass

    if date_range_days > 365:
        print(f"⚠️  Analysis window is {date_range_days} days ({date_range_days/365:.1f} years).")
        print("   Attribution works best with a single-season window (weeks to months).")
        print("   A multi-year window captures coincidental signups unrelated to the show.")

    if total_show_conversion > 25:
        print(f"⚠️  Conversion rate is {total_show_conversion:.1f}% — almost certainly inflated.")
        print("   Most platforms see <5% conversion even for hit shows.")
        if date_range_days > 180:
            print(f"   Likely cause: analysis window is too wide ({date_range_days} days).")
            print("   Over a long period, people sign up for unrelated reasons.")

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

    # Final-step GPT-4o audience alignment:
    # Use Show/Content Tracked + external research to align AGE/GENDER before save.
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

    # Write to output_dir from params (e.g. server output dir on Render) or default Desktop/attribution
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
    conn = connect_snowflake()
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

