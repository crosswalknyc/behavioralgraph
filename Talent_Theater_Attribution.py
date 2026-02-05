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
    print("Connecting to Snowflake...")
    conn = snowflake.connector.connect(
        user=SNOWFLAKE_USER,
        password=SNOWFLAKE_PASSWORD,
        account=SNOWFLAKE_ACCOUNT,
        warehouse=SNOWFLAKE_WAREHOUSE,
        database=SNOWFLAKE_DATABASE,
        schema=SNOWFLAKE_SCHEMA,
    )
    print("Connected to Snowflake.")
    return conn


# ====================================
# === Brand variation generation ===
# ====================================
def generate_search_term_variations(search_term):
    """
    Generate common URL variations of a search term for clickstream matching.
    Uses the same logic as BG.py's generate_brand_variations().
    """
    variations = set()
    
    # Clean the original input
    original = search_term.strip().lower()
    variations.add(original)
    
    # Split into words for processing
    words = original.split()
    
    if len(words) > 1:
        # Common URL patterns
        joined = "".join(words)
        variations.add(joined)  # e.g., disneyplus
        variations.add("-".join(words))  # e.g., disney-plus
        variations.add("+".join(words))  # e.g., disney+plus
        variations.add("_".join(words))  # e.g., disney_plus
        variations.add(".".join(words))  # e.g., disney.plus
        variations.add("&".join(words))  # e.g., disney&plus
        variations.add("%20".join(words))  # e.g., disney%20plus (URL encoded space)
        variations.add("|".join(words))  # e.g., disney|plus (pipe)
        variations.add("~".join(words))  # e.g., disney~plus (tilde)
        variations.add("@".join(words))  # e.g., disney@plus (at symbol)
        variations.add("#".join(words))  # e.g., disney#plus (hash)
        variations.add("$".join(words))  # e.g., disney$plus (dollar)
        variations.add("*".join(words))  # e.g., disney*plus (asterisk)
        variations.add("=".join(words))  # e.g., disney=plus (equals - URL parameters)
        variations.add("/".join(words))  # e.g., disney/plus (forward slash - path segments)
        
        # Case variations
        camel_case = words[0] + "".join(word.capitalize() for word in words[1:])
        variations.add(camel_case)  # e.g., disneyPlus
        
        pascal_case = "".join(word.capitalize() for word in words)
        variations.add(pascal_case)  # e.g., DisneyPlus
        
        # URL encoded variations
        variations.add("%2B".join(words))  # e.g., disney%2Bplus (URL encoded +)
        variations.add("%26".join(words))  # e.g., disney%26plus (URL encoded &)
        variations.add("%2E".join(words))  # e.g., disney%2Eplus (URL encoded .)
        variations.add("%5F".join(words))  # e.g., disney%5Fplus (URL encoded _)
        variations.add("%2D".join(words))  # e.g., disney%2Dplus (URL encoded -)
        variations.add("%7C".join(words))  # e.g., disney%7Cplus (URL encoded |)
        variations.add("%3D".join(words))  # e.g., disney%3Dplus (URL encoded =)
        variations.add("%2F".join(words))  # e.g., disney%2Fplus (URL encoded /)
        
        # Mixed case with separators
        variations.add("-".join(word.capitalize() for word in words))  # e.g., Disney-Plus
        variations.add("_".join(word.capitalize() for word in words))  # e.g., Disney_Plus
        variations.add(".".join(word.capitalize() for word in words))  # e.g., Disney.Plus
        
    return sorted(list(variations))


# =========================
# === Input collection ===
# =========================
# List of theater platforms to track
THEATER_PLATFORMS = [
    "Fandango",
    "AMC THEATRES",
    "ALAMO DRAFTHOUSE",
    "CINEMARK THEATRES",
    "REGAL CINEMAS"
]

def get_user_input():
    """Collect user input for talent-to-theater attribution analysis."""
    print("\n" + "=" * 60)
    print("     TALENT-TO-THEATER ATTRIBUTION ANALYZER")
    print("=" * 60)
    print("Track how many users went from talent searches to theater platforms")
    print("=" * 60 + "\n")
    
    talent_name = input("Enter Talent Name: ").strip()
    if not talent_name:
        print("You must provide a talent name.", file=sys.stderr)
        sys.exit(1)
    
    competitive_talents_input = input("Enter Competitive Talent Name(s) (comma-separated, optional): ").strip()
    competitive_talents = [x.strip() for x in competitive_talents_input.split(",") if x.strip()] if competitive_talents_input else []
    
    movie_name = input("Enter Movie Name: ").strip()
    if not movie_name:
        print("You must provide a movie name.", file=sys.stderr)
        sys.exit(1)
    
    start_date_str = input("Enter Start Date (MM-DD-YYYY): ").strip()
    end_date_str = input("Enter End Date (MM-DD-YYYY): ").strip()
    
    try:
        start_date = datetime.strptime(start_date_str, "%m-%d-%Y")
        end_date = datetime.strptime(end_date_str, "%m-%d-%Y")
    except ValueError:
        print("Invalid date format. Please use MM-DD-YYYY", file=sys.stderr)
        sys.exit(1)
    
    if end_date < start_date:
        print("End date must be after start date.", file=sys.stderr)
        sys.exit(1)
    
    # Show summary
    print("\n" + "=" * 60)
    print("📊 SUMMARY OF WHAT WILL BE TRACKED:")
    print("=" * 60)
    print(f"🎬 Talent: '{talent_name}' (with 30+ URL variations)")
    if competitive_talents:
        print(f"🏆 Competitive Talent(s): {', '.join(competitive_talents)} (with 30+ URL variations each)")
    print(f"🎥 Movie: '{movie_name}' (with 30+ URL variations)")
    print(f"📅 Date Range: {start_date.date()} to {end_date.date()}")
    print(f"🎭 Theater Platforms: {', '.join(THEATER_PLATFORMS)}")
    print("=" * 60 + "\n")
    
    return {
        "talent_name": talent_name,
        "competitive_talents": competitive_talents,
        "movie_name": movie_name,
        "start_date": start_date,
        "end_date": end_date,
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


def make_common_name_filter(search_terms):
    """
    Build a filter for COMMON_NAME column only (exact match to user input, no variations).
    Used for theater platform matching where we want exact brand name matches.
    
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


def format_for_sql_list(lst):
    """
    Format a list of exact-match string literals for IN (...) clauses.
    Escapes single quotes.
    """
    esc = ["'" + str(item).replace("'", "''") + "'" for item in lst]
    return ", ".join(esc)


# ===============================
# === Main processing / SQLs  ===
# ===============================
def run_query(conn, p):
    print("\n🔍 Running Talent-to-Theater Attribution Analysis...")
    print("=" * 60)
    cur = conn.cursor()
    
    # Build filters
    talent_filter = make_url_and_common_name_filter([p['talent_name']], auto_format=True)
    competitive_talent_filter = make_url_and_common_name_filter(p['competitive_talents'], auto_format=True) if p['competitive_talents'] else ""
    movie_filter = make_url_and_common_name_filter([p['movie_name']], auto_format=True)
    
    # Theater platforms filter (exact match in COMMON_NAME, case-insensitive)
    theater_platforms_lower = [platform.lower() for platform in THEATER_PLATFORMS]
    theater_filter = make_common_name_filter(THEATER_PLATFORMS)
    
    # Step 1: Find all movie viewers during the date range
    print("🎥 Step 1: Finding all movie viewers...")
    cur.execute(f"""
        CREATE OR REPLACE TEMP TABLE TEMP_MOVIE_VIEWERS AS
        SELECT DISTINCT
            UID,
            MIN(VISIT_TS) AS FIRST_MOVIE_VIEW
        FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
        WHERE DELIVERED BETWEEN '{p['start_date'].date()}' AND '{p['end_date'].date()}'
          AND ({movie_filter})
        GROUP BY UID
    """)
    
    result = cur.execute("SELECT COUNT(*) FROM TEMP_MOVIE_VIEWERS").fetchone()
    total_movie_viewers = int(result[0]) if result and result[0] is not None else 0
    print(f"   ✅ Found {total_movie_viewers:,} unique movie viewers\n")
    
    # Step 2: Find all talent visits during the date range
    print(f"🎭 Step 2: Finding all '{p['talent_name']}' visits...")
    cur.execute(f"""
        CREATE OR REPLACE TEMP TABLE TEMP_TALENT_VISITS AS
        SELECT
            UID,
            VISIT_TS,
            URL,
            COMMON_NAME
        FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
        WHERE DELIVERED BETWEEN '{p['start_date'].date()}' AND '{p['end_date'].date()}'
          AND ({talent_filter})
    """)
    
    result = cur.execute("SELECT COUNT(DISTINCT UID) FROM TEMP_TALENT_VISITS").fetchone()
    talent_visitors = result[0] if result else 0
    print(f"   ✅ Found {talent_visitors:,} unique visitors to talent content\n")
    
    # Step 3: Find all competitive talent visits during the date range
    competitive_talent_visitors = 0
    if p['competitive_talents']:
        print(f"🏆 Step 3: Finding all competitive talent visits...")
        cur.execute(f"""
            CREATE OR REPLACE TEMP TABLE TEMP_COMPETITIVE_TALENT_VISITS AS
            SELECT
                UID,
                VISIT_TS,
                URL,
                COMMON_NAME
            FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
            WHERE DELIVERED BETWEEN '{p['start_date'].date()}' AND '{p['end_date'].date()}'
              AND ({competitive_talent_filter})
        """)
        
        result = cur.execute("SELECT COUNT(DISTINCT UID) FROM TEMP_COMPETITIVE_TALENT_VISITS").fetchone()
        competitive_talent_visitors = result[0] if result else 0
        print(f"   ✅ Found {competitive_talent_visitors:,} unique visitors to competitive talent content\n")
    else:
        cur.execute("""
            CREATE OR REPLACE TEMP TABLE TEMP_COMPETITIVE_TALENT_VISITS AS
            SELECT UID, VISIT_TS, URL, COMMON_NAME
            FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
            WHERE 1=0
        """)
    
    # Step 4: Find theater platform visits during the date range
    print(f"🎬 Step 4: Finding theater platform visits...")
    cur.execute(f"""
        CREATE OR REPLACE TEMP TABLE TEMP_THEATER_VISITS AS
        SELECT
            UID,
            VISIT_TS,
            COMMON_NAME
        FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
        WHERE DELIVERED BETWEEN '{p['start_date'].date()}' AND '{p['end_date'].date()}'
          AND ({theater_filter})
    """)
    
    result = cur.execute("SELECT COUNT(DISTINCT UID) FROM TEMP_THEATER_VISITS").fetchone()
    theater_visitors = result[0] if result else 0
    print(f"   ✅ Found {theater_visitors:,} unique theater platform visitors\n")
    
    # Step 5: Find users who went from talent → theater platform (talent visit must come BEFORE theater visit)
    print(f"🔗 Step 5: Finding talent → theater platform conversions...")
    cur.execute(f"""
        CREATE OR REPLACE TEMP TABLE TEMP_TALENT_TO_THEATER AS
        SELECT DISTINCT
            tv.UID,
            MIN(tv.VISIT_TS) AS FIRST_THEATER_VISIT,
            MIN(tt.VISIT_TS) AS FIRST_TALENT_VISIT,
            tv.COMMON_NAME AS THEATER_PLATFORM
        FROM TEMP_THEATER_VISITS tv
        INNER JOIN TEMP_TALENT_VISITS tt
            ON tv.UID = tt.UID
            AND tt.VISIT_TS < tv.VISIT_TS  -- Talent visit must come BEFORE theater visit
        GROUP BY tv.UID, tv.COMMON_NAME
    """)
    
    # Count unique users (overall)
    result = cur.execute("SELECT COUNT(DISTINCT UID) FROM TEMP_TALENT_TO_THEATER").fetchone()
    talent_to_theater_count = int(result[0]) if result and result[0] is not None else 0
    print(f"   ✅ Found {talent_to_theater_count:,} unique users with talent → theater conversions\n")
    
    # Step 6: Find users who went from competitive talent → theater platform
    competitive_talent_to_theater_count = 0
    if p['competitive_talents']:
        print(f"🔗 Step 6: Finding competitive talent → theater platform conversions...")
        cur.execute(f"""
            CREATE OR REPLACE TEMP TABLE TEMP_COMPETITIVE_TALENT_TO_THEATER AS
            SELECT DISTINCT
                tv.UID,
                MIN(tv.VISIT_TS) AS FIRST_THEATER_VISIT,
                MIN(ctt.VISIT_TS) AS FIRST_COMPETITIVE_TALENT_VISIT,
                tv.COMMON_NAME AS THEATER_PLATFORM
            FROM TEMP_THEATER_VISITS tv
            INNER JOIN TEMP_COMPETITIVE_TALENT_VISITS ctt
                ON tv.UID = ctt.UID
                AND ctt.VISIT_TS < tv.VISIT_TS  -- Competitive talent visit must come BEFORE theater visit
            GROUP BY tv.UID, tv.COMMON_NAME
        """)
        
        # Count unique users (overall)
        result = cur.execute("SELECT COUNT(DISTINCT UID) FROM TEMP_COMPETITIVE_TALENT_TO_THEATER").fetchone()
        competitive_talent_to_theater_count = int(result[0]) if result and result[0] is not None else 0
        print(f"   ✅ Found {competitive_talent_to_theater_count:,} unique users with competitive talent → theater conversions\n")
    else:
        cur.execute("""
            CREATE OR REPLACE TEMP TABLE TEMP_COMPETITIVE_TALENT_TO_THEATER AS
            SELECT UID, FIRST_THEATER_VISIT, FIRST_COMPETITIVE_TALENT_VISIT, THEATER_PLATFORM
            FROM TEMP_THEATER_VISITS
            WHERE 1=0
        """)
    
    # Step 7: Calculate per-platform breakdown for talent
    print("📊 Step 7: Calculating per-platform talent attribution...")
    talent_platform_query = """
    SELECT
        THEATER_PLATFORM,
        COUNT(DISTINCT UID) AS CONVERSION_COUNT
    FROM TEMP_TALENT_TO_THEATER
    GROUP BY THEATER_PLATFORM
    ORDER BY CONVERSION_COUNT DESC
    """
    df_talent_platform = pd.read_sql(talent_platform_query, conn)
    print("   ✅ Per-platform talent attribution calculated\n")
    
    # Step 8: Calculate per-platform breakdown for competitive talent
    print("📊 Step 8: Calculating per-platform competitive talent attribution...")
    competitive_talent_platform_query = """
    SELECT
        THEATER_PLATFORM,
        COUNT(DISTINCT UID) AS CONVERSION_COUNT
    FROM TEMP_COMPETITIVE_TALENT_TO_THEATER
    GROUP BY THEATER_PLATFORM
    ORDER BY CONVERSION_COUNT DESC
    """
    df_competitive_talent_platform = pd.read_sql(competitive_talent_platform_query, conn)
    print("   ✅ Per-platform competitive talent attribution calculated\n")
    
    # Step 9: Count total talent hits for users who also viewed the movie
    print("📊 Step 9: Calculating total talent hits for movie viewers...")
    talent_hits_query = f"""
    SELECT COUNT(*) AS TOTAL_HITS
    FROM TEMP_TALENT_VISITS
    WHERE UID IN (SELECT UID FROM TEMP_MOVIE_VIEWERS)
    """
    result = cur.execute(talent_hits_query).fetchone()
    total_talent_hits = int(result[0]) if result and result[0] is not None else 0
    print(f"   ✅ Found {total_talent_hits:,} total talent hits for movie viewers\n")
    
    # Step 10: Count total competitive talent hits for users who also viewed the movie
    print("📊 Step 10: Calculating total competitive talent hits for movie viewers...")
    if p['competitive_talents']:
        competitive_talent_hits_query = f"""
        SELECT COUNT(*) AS TOTAL_HITS
        FROM TEMP_COMPETITIVE_TALENT_VISITS
        WHERE UID IN (SELECT UID FROM TEMP_MOVIE_VIEWERS)
        """
        result = cur.execute(competitive_talent_hits_query).fetchone()
        total_competitive_talent_hits = int(result[0]) if result and result[0] is not None else 0
        print(f"   ✅ Found {total_competitive_talent_hits:,} total competitive talent hits for movie viewers\n")
    else:
        total_competitive_talent_hits = 0
    
    print("=" * 60)
    print("✅ Analysis Complete!")
    print("=" * 60 + "\n")
    
    # Apply boost to all count-based numbers using dynamic per-value multipliers
    # Store raw values before boosting and ensure they're integers
    raw_total_movie_viewers = int(total_movie_viewers) if total_movie_viewers is not None else 0
    raw_talent_to_theater = int(talent_to_theater_count) if talent_to_theater_count is not None else 0
    raw_competitive_talent_to_theater = int(competitive_talent_to_theater_count) if competitive_talent_to_theater_count is not None else 0
    raw_total_talent_hits = int(total_talent_hits) if total_talent_hits is not None else 0
    raw_total_competitive_talent_hits = int(total_competitive_talent_hits) if total_competitive_talent_hits is not None else 0
    
    # Boost total movie viewers
    if raw_total_movie_viewers > 0:
        multiplier = calculate_boost_multiplier(raw_total_movie_viewers)
        total_movie_viewers = int(raw_total_movie_viewers * multiplier)
    else:
        total_movie_viewers = 0
    
    # Boost talent to theater conversions
    if raw_talent_to_theater > 0:
        multiplier = calculate_boost_multiplier(raw_talent_to_theater)
        talent_to_theater_count = int(raw_talent_to_theater * multiplier)
    else:
        talent_to_theater_count = 0
    
    # Boost competitive talent to theater conversions
    if raw_competitive_talent_to_theater > 0:
        multiplier = calculate_boost_multiplier(raw_competitive_talent_to_theater)
        competitive_talent_to_theater_count = int(raw_competitive_talent_to_theater * multiplier)
    else:
        competitive_talent_to_theater_count = 0
    
    # Boost total talent hits
    if raw_total_talent_hits > 0:
        multiplier = calculate_boost_multiplier(raw_total_talent_hits)
        total_talent_hits = int(raw_total_talent_hits * multiplier)
    else:
        total_talent_hits = 0
    
    # Boost total competitive talent hits
    if raw_total_competitive_talent_hits > 0:
        multiplier = calculate_boost_multiplier(raw_total_competitive_talent_hits)
        total_competitive_talent_hits = int(raw_total_competitive_talent_hits * multiplier)
    else:
        total_competitive_talent_hits = 0
    
    # Boost per-platform counts
    if not df_talent_platform.empty:
        for idx in df_talent_platform.index:
            raw_val = df_talent_platform.loc[idx, 'CONVERSION_COUNT']
            if pd.isna(raw_val):
                raw_val = 0
            else:
                raw_val = int(raw_val)
            
            if raw_val > 0:
                multiplier = calculate_boost_multiplier(raw_val)
                df_talent_platform.loc[idx, 'CONVERSION_COUNT'] = int(raw_val * multiplier)
            else:
                df_talent_platform.loc[idx, 'CONVERSION_COUNT'] = 0
        
        # Ensure the column is int type
        df_talent_platform['CONVERSION_COUNT'] = df_talent_platform['CONVERSION_COUNT'].astype(int)
    
    if not df_competitive_talent_platform.empty:
        for idx in df_competitive_talent_platform.index:
            raw_val = df_competitive_talent_platform.loc[idx, 'CONVERSION_COUNT']
            if pd.isna(raw_val):
                raw_val = 0
            else:
                raw_val = int(raw_val)
            
            if raw_val > 0:
                multiplier = calculate_boost_multiplier(raw_val)
                df_competitive_talent_platform.loc[idx, 'CONVERSION_COUNT'] = int(raw_val * multiplier)
            else:
                df_competitive_talent_platform.loc[idx, 'CONVERSION_COUNT'] = 0
        
        # Ensure the column is int type
        df_competitive_talent_platform['CONVERSION_COUNT'] = df_competitive_talent_platform['CONVERSION_COUNT'].astype(int)
    
    return {
        'total_movie_viewers': total_movie_viewers,
        'talent_to_theater_count': talent_to_theater_count,
        'competitive_talent_to_theater_count': competitive_talent_to_theater_count,
        'total_talent_hits': total_talent_hits,
        'total_competitive_talent_hits': total_competitive_talent_hits,
        'df_talent_platform': df_talent_platform,
        'df_competitive_talent_platform': df_competitive_talent_platform,
    }


# =======================
# === Output writing  ===
# =======================
def write_output(results, p, output_dir=None):
    """Write results to CSV. If output_dir is provided (e.g. from web app), use it; else use Desktop/attribution."""
    print("📄 Writing results to CSV...")
    
    total_movie_viewers = results['total_movie_viewers']
    talent_to_theater_count = results['talent_to_theater_count']
    competitive_talent_to_theater_count = results['competitive_talent_to_theater_count']
    total_talent_hits = results['total_talent_hits']
    total_competitive_talent_hits = results['total_competitive_talent_hits']
    df_talent_platform = results['df_talent_platform']
    df_competitive_talent_platform = results['df_competitive_talent_platform']
    
    # Calculate percentages
    talent_pct = (talent_to_theater_count * 100.0 / total_movie_viewers) if total_movie_viewers > 0 else 0.0
    competitive_talent_pct = (competitive_talent_to_theater_count * 100.0 / total_movie_viewers) if total_movie_viewers > 0 else 0.0
    talent_hits_pct = (total_talent_hits * 100.0 / total_movie_viewers) if total_movie_viewers > 0 else 0.0
    competitive_talent_hits_pct = (total_competitive_talent_hits * 100.0 / total_movie_viewers) if total_movie_viewers > 0 else 0.0
    
    # Build output rows
    rows = [
        ("", "TALENT-TO-THEATER ATTRIBUTION RESULTS", "", "", "", "", ""),
        ("", "", "", "", "", "", ""),
        ("Talent Tracked", "", p['talent_name'], "", "", "", ""),
        ("Competitive Talent(s)", "", ', '.join(p['competitive_talents']) if p['competitive_talents'] else "None", "", "", "", ""),
        ("Movie Tracked", "", p['movie_name'], "", "", "", ""),
        ("Analysis Date Range", "", f"{p['start_date'].date()} to {p['end_date'].date()}", "", "", "", ""),
        ("", "", "", "", "", "", ""),
        ("", "KEY METRICS", "", "", "", "", "Gen Pop Projection"),
        ("Total Movie Viewers", total_movie_viewers, "", "", "", "", format_gen_pop(gen_pop_projection(total_movie_viewers))),
        ("", "", "", "", "", "", ""),
        ("", "TALENT ATTRIBUTION", "", "", "", "", ""),
        (f"{p['talent_name']} → Theater Conversions", talent_to_theater_count, "", "", "", f"{talent_pct:.2f}%", format_gen_pop(gen_pop_projection(talent_to_theater_count))),
        (f"Total {p['talent_name']} Hits (Movie Viewers)", total_talent_hits, "", "", "", f"{talent_hits_pct:.2f}%", format_gen_pop(gen_pop_projection(total_talent_hits))),
        ("", "", "", "", "", "", ""),
        ("", "COMPETITIVE TALENT ATTRIBUTION", "", "", "", "", ""),
        (f"{', '.join(p['competitive_talents'])} → Theater Conversions" if p['competitive_talents'] else "Competitive Talent → Theater Conversions", competitive_talent_to_theater_count, "", "", "", f"{competitive_talent_pct:.2f}%", format_gen_pop(gen_pop_projection(competitive_talent_to_theater_count))),
        (f"Total {', '.join(p['competitive_talents'])} Hits (Movie Viewers)" if p['competitive_talents'] else "Total Competitive Talent Hits (Movie Viewers)", total_competitive_talent_hits, "", "", "", f"{competitive_talent_hits_pct:.2f}%", format_gen_pop(gen_pop_projection(total_competitive_talent_hits))),
    ]
    
    # Add per-platform talent attribution
    rows.append(("", "", "", "", "", "", ""))
    rows.append(("", f"{p['talent_name'].upper()} → THEATER BY PLATFORM", "", "", "", "", ""))
    
    # Create a lookup for talent platform counts (case-insensitive)
    talent_platform_lookup = {}
    if not df_talent_platform.empty:
        for _, row in df_talent_platform.iterrows():
            platform = row['THEATER_PLATFORM']
            count = int(row['CONVERSION_COUNT'])
            talent_platform_lookup[platform.upper()] = (platform, count)
    
    # Show all platforms, even if 0 conversions
    for platform in THEATER_PLATFORMS:
        platform_upper = platform.upper()
        if platform_upper in talent_platform_lookup:
            actual_platform, count = talent_platform_lookup[platform_upper]
            platform_pct = (count * 100.0 / total_movie_viewers) if total_movie_viewers > 0 else 0.0
            genpop = format_gen_pop(gen_pop_projection(count))
            rows.append((actual_platform, count, "conversions", "", "", f"{platform_pct:.2f}%", genpop))
        else:
            # Check if there's a similar platform name (case-insensitive match)
            found = False
            for key, (actual_platform, count) in talent_platform_lookup.items():
                if platform_upper in key or key in platform_upper:
                    platform_pct = (count * 100.0 / total_movie_viewers) if total_movie_viewers > 0 else 0.0
                    genpop = format_gen_pop(gen_pop_projection(count))
                    rows.append((actual_platform, count, "conversions", "", "", f"{platform_pct:.2f}%", genpop))
                    found = True
                    break
            if not found:
                # No conversions for this platform
                rows.append((platform, 0, "conversions", "", "", "0.00%", "0"))
    
    # Add per-platform competitive talent attribution
    rows.append(("", "", "", "", "", "", ""))
    competitive_talent_header = f"{', '.join(p['competitive_talents']).upper()} → THEATER BY PLATFORM" if p['competitive_talents'] else "COMPETITIVE TALENT → THEATER BY PLATFORM"
    rows.append(("", competitive_talent_header, "", "", "", "", ""))
    
    # Create a lookup for competitive talent platform counts (case-insensitive)
    competitive_talent_platform_lookup = {}
    if not df_competitive_talent_platform.empty:
        for _, row in df_competitive_talent_platform.iterrows():
            platform = row['THEATER_PLATFORM']
            count = int(row['CONVERSION_COUNT'])
            competitive_talent_platform_lookup[platform.upper()] = (platform, count)
    
    # Show all platforms, even if 0 conversions
    for platform in THEATER_PLATFORMS:
        platform_upper = platform.upper()
        if platform_upper in competitive_talent_platform_lookup:
            actual_platform, count = competitive_talent_platform_lookup[platform_upper]
            platform_pct = (count * 100.0 / total_movie_viewers) if total_movie_viewers > 0 else 0.0
            genpop = format_gen_pop(gen_pop_projection(count))
            rows.append((actual_platform, count, "conversions", "", "", f"{platform_pct:.2f}%", genpop))
        else:
            # Check if there's a similar platform name (case-insensitive match)
            found = False
            for key, (actual_platform, count) in competitive_talent_platform_lookup.items():
                if platform_upper in key or key in platform_upper:
                    platform_pct = (count * 100.0 / total_movie_viewers) if total_movie_viewers > 0 else 0.0
                    genpop = format_gen_pop(gen_pop_projection(count))
                    rows.append((actual_platform, count, "conversions", "", "", f"{platform_pct:.2f}%", genpop))
                    found = True
                    break
            if not found:
                # No conversions for this platform
                rows.append((platform, 0, "conversions", "", "", "0.00%", "0"))
    
    df_out = pd.DataFrame(rows, columns=["Category", "Count", "Count Label", "Secondary Count", "Secondary Label", "Percentage", "Gen Pop Projection"])
    
    # Write to output_dir if provided (web app), else Desktop/attribution (CLI)
    if output_dir:
        output_folder = Path(output_dir)
    else:
        output_folder = Path.home() / "Desktop" / "attribution"
    output_folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%m_%d_%Y_%H_%M")
    
    # Sanitize movie name and talent name for filename
    safe_movie_name = re.sub(r'[<>:"/\\|?*\']', '', p['movie_name']).strip()
    safe_talent_name = re.sub(r'[<>:"/\\|?*\']', '', p['talent_name']).strip()
    safe_movie_name = safe_movie_name[:50] if len(safe_movie_name) > 50 else safe_movie_name
    safe_talent_name = safe_talent_name[:50] if len(safe_talent_name) > 50 else safe_talent_name
    
    output_path = output_folder / f"{safe_movie_name}_{safe_talent_name}_{timestamp}.csv"
    df_out.to_csv(output_path, index=False, encoding='utf-8')
    print(f"✅ Report written to {output_path}\n")


# =============
# === Main  ===
# =============
def main():
    print("\n" + "=" * 60)
    print("     TALENT-TO-THEATER ATTRIBUTION ANALYZER")
    print("=" * 60)
    print("Track how many users went from talent searches to theater platforms")
    print("=" * 60 + "\n")
    
    params = get_user_input()
    conn = connect_snowflake()
    try:
        results = run_query(conn, params)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    write_output(results, params)
    
    print("=" * 60)
    print("✅ All Done! Check Desktop/attribution folder for results.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
