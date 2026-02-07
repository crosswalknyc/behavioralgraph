import pandas as pd
import snowflake.connector
from datetime import datetime, timedelta
from pathlib import Path
import sys
import math
import re


# =========================
# === Gen Pop Projection ===
# =========================
# US Population constant (same as BG.py)
US_POPULATION = 324_700_000
# Sample represents this many people
SAMPLE_REPRESENTS = 10_000_000

def gen_pop_projection(raw_number):
    """
    Project a raw number to the US general population.
    Uses same methodology as BG.py: (raw_number / 10,000,000) * 324,700,000
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
    """Format gen pop projection with 8 decimal places, with commas and 'M'/'K' suffix for millions/thousands."""
    try:
        n = float(number)
        if n >= 1_000_000:
            return f"{n / 1_000_000:.8f}M"
        elif n >= 1_000:
            return f"{n / 1_000:.8f}K"
        else:
            return f"{n:.8f}"
    except (ValueError, TypeError):
        return "0.00000000"

def calculate_boost_multiplier(raw_value):
    """
    Calculate the boost multiplier for a given raw value.
    Uses logic from show_platform_tracker.py: 15x default, fallback to 5x, then calculated safe multiplier.
    """
    MAX_ALLOWED_VALUE = 10_000_000
    
    if raw_value <= 0:
        return 15
    
    # Calculate the maximum multiplier that keeps values under 10M
    max_safe_multiplier = MAX_ALLOWED_VALUE // raw_value
    
    # Use 15 if safe, otherwise use 5, otherwise calculate exact safe multiplier
    if raw_value * 15 <= MAX_ALLOWED_VALUE:
        boost_multiplier = 15
    elif raw_value * 5 <= MAX_ALLOWED_VALUE:
        boost_multiplier = 5
    else:
        # Even 5x would exceed 10M, use the max safe multiplier (minimum 1)
        boost_multiplier = max(1, max_safe_multiplier)
    
    return boost_multiplier


# =========================
# === Snowflake creds ====
# =========================
# ⚠️ Hard-coded credentials (note: insecure for production use)
SNOWFLAKE_USER = "hotdogsandcheezeits"
SNOWFLAKE_PASSWORD = "S3nshine2282!"
SNOWFLAKE_ACCOUNT = "qsodrkt-hgb46445"
SNOWFLAKE_WAREHOUSE = "ATTRIBUTIONPROCESSING"
SNOWFLAKE_DATABASE = "PROCESSEDCLICKSTREAM"
SNOWFLAKE_SCHEMA = "PUBLIC"


# =========================
# === Snowflake connect ===
# =========================
def connect_snowflake():
    import os
    # Prefer env vars when set (e.g. on Render) so credentials aren't hardcoded in cloud
    user = os.environ.get("SNOWFLAKE_USER") or SNOWFLAKE_USER
    password = os.environ.get("SNOWFLAKE_PASSWORD") or SNOWFLAKE_PASSWORD
    account = os.environ.get("SNOWFLAKE_ACCOUNT") or SNOWFLAKE_ACCOUNT
    warehouse = os.environ.get("SNOWFLAKE_WAREHOUSE") or SNOWFLAKE_WAREHOUSE
    database = os.environ.get("SNOWFLAKE_DATABASE") or SNOWFLAKE_DATABASE
    schema = os.environ.get("SNOWFLAKE_SCHEMA") or SNOWFLAKE_SCHEMA
    print("Connecting to Snowflake...")
    conn = snowflake.connector.connect(
        user=user,
        password=password,
        account=account,
        warehouse=warehouse,
        database=database,
        schema=schema,
        insecure_mode=True,  # Avoid OCSP/SSL timeouts (e.g. on Render) that can surface as concurrent.futures errors
        connection_timeout=90,
        network_timeout=3600,
    )
    print("Connected to Snowflake.")
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
# Allowed genre options (only these can be selected in CLI or passed from SVOD Acquisition IQ)
ALLOWED_GENRES = [
    "Serialized Drama",
    "Non-Scripted Competition",
    "Adult Animation",
    "Stand Up Comedy",
    "Single Camera Sitcom",
    "Procedural Drama",
    "Multi Camera Sitcom",
    "Live Sports",
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
        # For new shows, all watchers are new first time viewers
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
        # WITH EPISODE ATTRIBUTION: Find which episode they watched last before signing up
        cur.execute(f"""
            CREATE OR REPLACE TEMP TABLE TEMP_NEW_PLATFORM_SIGNUPS AS
            SELECT
                sw.UID,
                sw.FIRST_SHOW_WATCH,
                MIN(cs.VISIT_TS) AS FIRST_PLATFORM_VISIT,
                DATEDIFF(DAY, sw.FIRST_SHOW_WATCH, MIN(cs.VISIT_TS)) AS DAYS_TO_SIGNUP,
                (
                    SELECT MAX(epi.EPISODE_NUM)
                    FROM TEMP_SHOW_WATCHERS_WITH_EPISODES epi
                    WHERE epi.UID = sw.UID
                      AND epi.VISIT_TS < MIN(cs.VISIT_TS)
                ) AS ATTRIBUTED_EPISODE
            FROM TEMP_CLEAN_SHOW_WATCHERS sw
            INNER JOIN PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL cs
                ON sw.UID = cs.UID
            WHERE cs.DELIVERED BETWEEN '{p['campaign_start'].date()}'
                                   AND DATEADD(DAY, {p['attribution_window']}, '{p['campaign_end'].date()}')
              AND LOWER(cs.COMMON_NAME) LIKE '%{platform_filter}%'
              AND cs.VISIT_TS >= sw.FIRST_SHOW_WATCH
            GROUP BY sw.UID, sw.FIRST_SHOW_WATCH
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
    # Multiply Total Show Watchers by 4 before any other calculations (percentages use this as denominator)
    if 'TOTAL_SHOW_WATCHERS' in df_summary.columns and not pd.isna(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS']):
        raw_show_watchers = int(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS'])
        df_summary.loc[0, 'TOTAL_SHOW_WATCHERS'] = raw_show_watchers * 4
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
    LIMIT 14
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
                pct = float(row['PERCENTAGE'])
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

    # Apply boost to all count-based numbers using dynamic per-value multipliers (like show_platform_tracker.py)
    # Each value gets its own multiplier (15x, 5x, or calculated safe multiplier)
    # Post-signup touchpoints get an additional 9x boost (except 1st touchpoint)
    print(f"🔥 Applying dynamic per-value boost multipliers (15x/5x/safe) to all numbers...\n")
    print(f"🔥 Post-signup touchpoints (2nd-5th) get additional 9x boost...\n")
    
    # Store raw values before boosting (TOTAL_SHOW_WATCHERS already multiplied by 4 earlier)
    raw_total_watchers = int(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS']) if ('TOTAL_SHOW_WATCHERS' in df_summary.columns and not pd.isna(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS'])) else 0
    raw_new_signups = int(df_summary.loc[0, 'NEW_SIGNUPS']) if ('NEW_SIGNUPS' in df_summary.columns and not pd.isna(df_summary.loc[0, 'NEW_SIGNUPS'])) else 0
    # Save raw Clean Sample before we overwrite it (needed so Clean Conversion Rate uses denominator * 4)
    raw_clean_sample = int(df_summary.loc[0, 'CLEAN_SAMPLE_SIZE']) if ('CLEAN_SAMPLE_SIZE' in df_summary.columns and not pd.isna(df_summary.loc[0, 'CLEAN_SAMPLE_SIZE'])) else 0
    
    # Boost TOTAL_SHOW_WATCHERS first, then use that boosted value for calculations
    total_watchers_multiplier = None
    if 'TOTAL_SHOW_WATCHERS' in df_summary.columns and raw_total_watchers > 0:
        total_watchers_multiplier = calculate_boost_multiplier(raw_total_watchers)
        boosted_total_watchers = int(raw_total_watchers * total_watchers_multiplier)
        df_summary.loc[0, 'TOTAL_SHOW_WATCHERS'] = boosted_total_watchers
        
        # Recalculate TOTAL_SHOW_CONVERSION_RATE from boosted Total Show Watchers (raw*4*dynamic)
        if 'NEW_SIGNUPS' in df_summary.columns:
            raw_new_signups = int(df_summary.loc[0, 'NEW_SIGNUPS']) if not pd.isna(df_summary.loc[0, 'NEW_SIGNUPS']) else 0
            if raw_new_signups > 0:
                new_signups_multiplier = calculate_boost_multiplier(raw_new_signups)
                boosted_new_signups = int(raw_new_signups * new_signups_multiplier)
                df_summary.loc[0, 'NEW_SIGNUPS'] = boosted_new_signups
            else:
                boosted_new_signups = 0
            if boosted_total_watchers > 0:
                current_signups = int(df_summary.loc[0, 'NEW_SIGNUPS']) if not pd.isna(df_summary.loc[0, 'NEW_SIGNUPS']) else 0
                df_summary.loc[0, 'TOTAL_SHOW_CONVERSION_RATE'] = round((current_signups * 100.0) / boosted_total_watchers, 2)
    
    # Boost other summary counts (using dynamic per-value multipliers like show_platform_tracker.py)
    for col in ['PRE_EXISTING_USERS', 'CLEAN_SAMPLE_SIZE']:
        if col in df_summary.columns:
            for idx in df_summary.index:
                raw_val = int(df_summary.loc[idx, col]) if not pd.isna(df_summary.loc[idx, col]) else 0
                if raw_val > 0:
                    multiplier = calculate_boost_multiplier(raw_val)
                    df_summary.loc[idx, col] = int(raw_val * multiplier)
    
    # Clean Conversion Rate = (boosted New Signups) / (Clean Sample * 4 * same dynamic mult as Total Show Watchers)
    # So the denominator uses the "number multiplied by 4" like Total Show Watchers, not the un-multiplied Clean Sample.
    if raw_clean_sample > 0 and total_watchers_multiplier is not None and 'NEW_SIGNUPS' in df_summary.columns:
        denominator_clean_4x = (raw_clean_sample * 4) * total_watchers_multiplier
        if denominator_clean_4x > 0:
            current_signups = int(df_summary.loc[0, 'NEW_SIGNUPS']) if not pd.isna(df_summary.loc[0, 'NEW_SIGNUPS']) else 0
            df_summary.loc[0, 'CLEAN_CONVERSION_RATE'] = round((current_signups * 100.0) / denominator_clean_4x, 2)
    
    # Boost demographic counts (using dynamic per-value multipliers)
    if 'COUNT' in df_demo.columns:
        for idx in df_demo.index:
            raw_val = int(df_demo.loc[idx, 'COUNT']) if not pd.isna(df_demo.loc[idx, 'COUNT']) else 0
            if raw_val > 0:
                multiplier = calculate_boost_multiplier(raw_val)
                df_demo.loc[idx, 'COUNT'] = int(raw_val * multiplier)
    
    # Boost timing counts (using dynamic per-value multipliers)
    if 'SIGNUP_COUNT' in df_timing.columns:
        for idx in df_timing.index:
            raw_val = int(df_timing.loc[idx, 'SIGNUP_COUNT']) if not pd.isna(df_timing.loc[idx, 'SIGNUP_COUNT']) else 0
            if raw_val > 0:
                multiplier = calculate_boost_multiplier(raw_val)
                df_timing.loc[idx, 'SIGNUP_COUNT'] = int(raw_val * multiplier)
    
    # Boost episode attribution counts (using dynamic per-value multipliers)
    if not df_episode_attribution.empty:
        if 'SIGNUPS_ATTRIBUTED' in df_episode_attribution.columns:
            for idx in df_episode_attribution.index:
                raw_val = int(df_episode_attribution.loc[idx, 'SIGNUPS_ATTRIBUTED']) if not pd.isna(df_episode_attribution.loc[idx, 'SIGNUPS_ATTRIBUTED']) else 0
                if raw_val > 0:
                    multiplier = calculate_boost_multiplier(raw_val)
                    df_episode_attribution.loc[idx, 'SIGNUPS_ATTRIBUTED'] = int(raw_val * multiplier)
        if 'TOTAL_VIEWS' in df_episode_attribution.columns:
            for idx in df_episode_attribution.index:
                raw_val = int(df_episode_attribution.loc[idx, 'TOTAL_VIEWS']) if not pd.isna(df_episode_attribution.loc[idx, 'TOTAL_VIEWS']) else 0
                if raw_val > 0:
                    multiplier = calculate_boost_multiplier(raw_val)
                    df_episode_attribution.loc[idx, 'TOTAL_VIEWS'] = int(raw_val * multiplier)
    
    # Boost monthly signup counts (using dynamic per-value multipliers)
    if not df_monthly_signups.empty:
        if 'UNIQUE_SIGNUPS' in df_monthly_signups.columns:
            for idx in df_monthly_signups.index:
                raw_val = int(df_monthly_signups.loc[idx, 'UNIQUE_SIGNUPS']) if not pd.isna(df_monthly_signups.loc[idx, 'UNIQUE_SIGNUPS']) else 0
                if raw_val > 0:
                    multiplier = calculate_boost_multiplier(raw_val)
                    df_monthly_signups.loc[idx, 'UNIQUE_SIGNUPS'] = int(raw_val * multiplier)
        if 'ENGAGED_WITH_SHOW' in df_monthly_signups.columns:
            for idx in df_monthly_signups.index:
                raw_val = int(df_monthly_signups.loc[idx, 'ENGAGED_WITH_SHOW']) if not pd.isna(df_monthly_signups.loc[idx, 'ENGAGED_WITH_SHOW']) else 0
                if raw_val > 0:
                    multiplier = calculate_boost_multiplier(raw_val)
                    df_monthly_signups.loc[idx, 'ENGAGED_WITH_SHOW'] = int(raw_val * multiplier)
    
    # Boost episode timing counts (using dynamic per-value multipliers)
    if not df_episode_timing.empty:
        if 'SIGNUP_COUNT' in df_episode_timing.columns:
            for idx in df_episode_timing.index:
                raw_val = int(df_episode_timing.loc[idx, 'SIGNUP_COUNT']) if not pd.isna(df_episode_timing.loc[idx, 'SIGNUP_COUNT']) else 0
                if raw_val > 0:
                    multiplier = calculate_boost_multiplier(raw_val)
                    df_episode_timing.loc[idx, 'SIGNUP_COUNT'] = int(raw_val * multiplier)
    
    # Boost monthly churn counts (using dynamic per-value multipliers)
    if not df_monthly_churn.empty:
        for col in ['ACTIVE_USERS', 'PREV_MONTH_ACTIVE', 'CHURNED_USERS']:
            if col in df_monthly_churn.columns:
                for idx in df_monthly_churn.index:
                    raw_val = int(df_monthly_churn.loc[idx, col]) if not pd.isna(df_monthly_churn.loc[idx, col]) else 0
                    if raw_val > 0:
                        multiplier = calculate_boost_multiplier(raw_val)
                        df_monthly_churn.loc[idx, col] = int(raw_val * multiplier)
    
    # Boost post-signup touchpoint counts
    # 1st Touchpoint should equal boosted NEW_SIGNUPS (already set earlier, but ensure it's correct)
    # 2nd-5th Touchpoints get dynamic multiplier * 9x boost
    # Percentages are calculated as % of Total Show Watchers
    if not df_post_signup_touchpoints.empty:
        if 'USER_COUNT' in df_post_signup_touchpoints.columns:
            # Get boosted values
            boosted_new_signups = int(df_summary.loc[0, 'NEW_SIGNUPS']) if ('NEW_SIGNUPS' in df_summary.columns and not pd.isna(df_summary.loc[0, 'NEW_SIGNUPS'])) else 0
            boosted_total_watchers = int(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS']) if ('TOTAL_SHOW_WATCHERS' in df_summary.columns and not pd.isna(df_summary.loc[0, 'TOTAL_SHOW_WATCHERS'])) else 0
            
            for idx in df_post_signup_touchpoints.index:
                touchpoint_rank = int(df_post_signup_touchpoints.loc[idx, 'TOUCHPOINT_RANK']) if not pd.isna(df_post_signup_touchpoints.loc[idx, 'TOUCHPOINT_RANK']) else 0
                raw_val = int(df_post_signup_touchpoints.loc[idx, 'USER_COUNT']) if not pd.isna(df_post_signup_touchpoints.loc[idx, 'USER_COUNT']) else 0
                
                if touchpoint_rank == 1:
                    # 1st Touchpoint should equal boosted NEW_SIGNUPS
                    df_post_signup_touchpoints.loc[idx, 'USER_COUNT'] = boosted_new_signups
                    # Calculate percentage as % of Total Show Watchers
                    if boosted_total_watchers > 0:
                        df_post_signup_touchpoints.loc[idx, 'PERCENTAGE'] = round((boosted_new_signups * 100.0) / boosted_total_watchers, 2)
                    else:
                        df_post_signup_touchpoints.loc[idx, 'PERCENTAGE'] = 0.0
                elif raw_val > 0:
                    # 2nd-5th Touchpoints get dynamic multiplier * 9x boost
                    multiplier = calculate_boost_multiplier(raw_val)
                    boosted_val = int(raw_val * multiplier * 9)
                    df_post_signup_touchpoints.loc[idx, 'USER_COUNT'] = boosted_val
                    # Calculate percentage as % of Total Show Watchers
                    if boosted_total_watchers > 0:
                        df_post_signup_touchpoints.loc[idx, 'PERCENTAGE'] = round((boosted_val * 100.0) / boosted_total_watchers, 2)
                    else:
                        df_post_signup_touchpoints.loc[idx, 'PERCENTAGE'] = 0.0

    return df_summary, df_comp, df_demo, df_timing, df_episode_attribution, df_monthly_signups, df_episode_timing, df_monthly_churn, df_post_signup_touchpoints


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
            pct = float(row["PERCENTAGE"])
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
            pct = float(row["PERCENTAGE"])
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
            for _, row in ep_data.head(10).iterrows():
                days = int(row["DAYS_TO_SIGNUP"])
                count = int(row["SIGNUP_COUNT"])
                pct = float(row["PERCENTAGE"])
                genpop = format_gen_pop(gen_pop_projection(count))
                day_label = "Same Day" if days == 0 else f"Day {days}" if days == 1 else f"{days} Days Later"
                rows.append((f"  {day_label}", "", count, "signups", "", "", "", "", f"{pct:.2f}%", genpop))

    # Post-signup touchpoint analysis (show visits as 1st-5th platform touchpoint)
    if not df_post_signup_touchpoints.empty:
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        rows.append(("", "", "POST-SIGNUP TOUCHPOINT ANALYSIS", "", "", "", "", "", "", ""))
        rows.append(("", "", "(Show visits as 1st-5th platform touchpoint after signup)", "", "", "", "", "", "", ""))
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        total_touchpoint_sum = 0
        for _, row in df_post_signup_touchpoints.iterrows():
            if pd.isna(row["TOUCHPOINT_RANK"]):
                continue
            touchpoint_rank = int(row["TOUCHPOINT_RANK"])
            user_count = int(row["USER_COUNT"]) if not pd.isna(row["USER_COUNT"]) else 0
            pct = float(row["PERCENTAGE"]) if not pd.isna(row["PERCENTAGE"]) else 0.0
            genpop = format_gen_pop(gen_pop_projection(user_count))
            rank_label = f"{touchpoint_rank}{'st' if touchpoint_rank == 1 else 'nd' if touchpoint_rank == 2 else 'rd' if touchpoint_rank == 3 else 'th'} Touchpoint"
            rows.append((rank_label, "", user_count, "users watched show", "", "", "", "", f"{pct:.2f}%", genpop))
            if 1 <= touchpoint_rank <= 5:
                total_touchpoint_sum += user_count
        if total_touchpoint_sum > 0:
            total_watchers = int(df_summary['TOTAL_SHOW_WATCHERS'].iloc[0]) if not df_summary.empty and 'TOTAL_SHOW_WATCHERS' in df_summary.columns else 0
            total_pct = round((total_touchpoint_sum * 100.0) / total_watchers, 2) if total_watchers > 0 else 0.0
            total_genpop = format_gen_pop(gen_pop_projection(total_touchpoint_sum))
            rows.append(("Total Platform Signups", "", total_touchpoint_sum, "users watched show", "", "", "", "", f"{total_pct:.2f}%", total_genpop))

    # Competitive platforms
    if not df_comp.empty:
        rows.append(("", "", "", "", "", "", "", "", "", ""))
        rows.append(("", "", "COMPETITIVE PLATFORMS (% of Show Watchers)", "", "", "", "", "", "", ""))
        for _, row in df_comp.iterrows():
            rows.append((row["COMMON_NAME"], "", "", "", "", "", "", "", f"{row['PERCENT']:.2f}%", ""))

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
                    rows.append((row["VALUE"], "", count, "people", "", "", "", "", f"{row['PERCENTAGE']:.1f}%", genpop_demo))

    df_out = pd.DataFrame(rows, columns=["Category", "Episode Date", "Count", "Count Label", "Secondary Count", "Secondary Label", "Tertiary Count", "Tertiary Label", "Percentage", "Gen Pop Projection"])

    # Write to output_dir from params (e.g. server output dir on Render) or default Desktop/attribution
    output_folder = Path(p['output_dir']) if p.get('output_dir') else Path.home() / "Desktop" / "attribution"
    output_folder = output_folder if isinstance(output_folder, Path) else Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%m_%d_%Y_%H_%M")
    # Sanitize project name for filename (remove invalid characters)
    safe_project_name = re.sub(r'[<>:"/\\|?*\']', '', p['project_name']).strip()
    # Limit length to avoid path issues
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

