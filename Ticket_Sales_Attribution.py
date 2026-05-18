"""
Ticket Sales Attribution - Movie viewers → theater platform hits with ticket sales projections.
Input: date range, movie title, genre. Output: TOTAL HITS (MOVIE VIEWERS) → THEATER BY PLATFORM,
ticket sales projections, and demographics per theater and overall.
"""
import pandas as pd
import os, sys as _sys; _sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'migration')); _sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'migration'))
from clickhouse_connector import connect_clickhouse
from datetime import datetime
from pathlib import Path
import sys
import re
import json
import os


# =========================
# === Gen Pop Projection ===
# =========================
US_POPULATION = 329_900_000
SAMPLE_REPRESENTS = 10_000_000

# Crosswalk Ticket Sales Tracker measures DIGITAL ticket sales only — visits
# to Fandango, AMC.com, Cinemark.com, Regal.com, and Alamo Drafthouse. In
# the US, digital pre-purchase historically captures ~55-70% of total
# theatrical ticket sales (the rest are box-office walk-ups, third-party
# resellers, Atom, mobile carrier offers, theater chain apps not in our
# panel, group sales). The AI auditor anchors our projected_sales_gen_pop
# inside this band relative to the researched US domestic gross.
#
# Tune via env vars on Render if real-world calibration shifts:
#   TICKET_DIGITAL_SALES_FACTOR_LOW   (default 0.55)
#   TICKET_DIGITAL_SALES_FACTOR_HIGH  (default 0.70)
def _digital_sales_band():
    try:
        lo = float(os.environ.get("TICKET_DIGITAL_SALES_FACTOR_LOW", "0.55"))
    except (ValueError, TypeError):
        lo = 0.55
    try:
        hi = float(os.environ.get("TICKET_DIGITAL_SALES_FACTOR_HIGH", "0.70"))
    except (ValueError, TypeError):
        hi = 0.70
    lo = max(0.10, min(0.95, lo))
    hi = max(lo, min(0.95, hi))
    return lo, hi


DIGITAL_SALES_FACTOR_LOW, DIGITAL_SALES_FACTOR_HIGH = _digital_sales_band()
DIGITAL_SALES_FACTOR_MID = (DIGITAL_SALES_FACTOR_LOW + DIGITAL_SALES_FACTOR_HIGH) / 2.0


def gen_pop_projection(raw_number):
    """Project raw number to US general population: (raw / 10,000,000) * 329,900,000"""
    try:
        raw = float(raw_number) if raw_number else 0.0
        if raw <= 0:
            return 0.0
        return round((raw / SAMPLE_REPRESENTS) * US_POPULATION, 8)
    except (ValueError, TypeError):
        return 0.0


def format_gen_pop(number):
    """Format gen pop projection with M/K suffix for readability."""
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


def format_gen_pop_full(number):
    """Format gen pop projection as full number with commas (no K/M abbreviation)."""
    try:
        n = float(number)
        if n >= 1:
            return f"{n:,.2f}"
        return f"{n:.2f}"
    except (ValueError, TypeError):
        return "0.00"


def calculate_boost_multiplier(raw_value):
    """
    Calculate the boost multiplier (same as Talent_Theater_Attribution).
    15x default, fallback to 5x, then calculated safe multiplier to stay under 10M.
    """
    MAX_ALLOWED_VALUE = 10_000_000
    if raw_value <= 0:
        return 15
    max_safe_multiplier = MAX_ALLOWED_VALUE // raw_value
    if raw_value * 15 <= MAX_ALLOWED_VALUE:
        return 15
    elif raw_value * 5 <= MAX_ALLOWED_VALUE:
        return 5
    else:
        return max(1, max_safe_multiplier)


# ===========================
# === ClickHouse connection ==
# ===========================
# Ticket Sales Tracker runs entirely on ClickHouse. Connection details come
# from the environment (CH_HOST/CH_PORT/CH_USER/CH_PASSWORD) via
# clickhouse_connector. No Snowflake involvement at runtime.
def connect_db():
    print("Connecting to ClickHouse...")
    conn = connect_clickhouse()
    print("Connected to ClickHouse.")
    return conn


# Back-compat alias: bg-webapp/app.py's run_ticket_sales_tracker() does
# `module.connect_snowflake()`, a leftover name from the SF era. Keep this
# alias so Flask doesn't AttributeError; the underlying call still goes to
# ClickHouse through connect_db() above.
connect_snowflake = connect_db


# ====================================
# === Search term variation generation ===
# ====================================
def generate_search_term_variations(search_term):
    """Generate common URL variations of a search term for clickstream matching."""
    variations = set()
    original = search_term.strip().lower()
    variations.add(original)
    words = original.split()

    if len(words) > 1:
        joined = "".join(words)
        variations.add(joined)
        variations.add("-".join(words))
        variations.add("+".join(words))
        variations.add("_".join(words))
        variations.add(".".join(words))
        variations.add("&".join(words))
        variations.add("%20".join(words))
        variations.add("|".join(words))
        variations.add("~".join(words))
        variations.add("@".join(words))
        variations.add("#".join(words))
        variations.add("$".join(words))
        variations.add("*".join(words))
        variations.add("=".join(words))
        variations.add("/".join(words))
        camel_case = words[0] + "".join(word.capitalize() for word in words[1:])
        variations.add(camel_case)
        pascal_case = "".join(word.capitalize() for word in words)
        variations.add(pascal_case)
        variations.add("%2B".join(words))
        variations.add("%26".join(words))
        variations.add("%2E".join(words))
        variations.add("%5F".join(words))
        variations.add("%2D".join(words))
        variations.add("%7C".join(words))
        variations.add("%3D".join(words))
        variations.add("%2F".join(words))
        variations.add("-".join(word.capitalize() for word in words))
        variations.add("_".join(word.capitalize() for word in words))
        variations.add(".".join(word.capitalize() for word in words))

    return sorted(list(variations))


# =========================
# === Input collection ===
# =========================
THEATER_PLATFORMS = [
    "Fandango",
    "AMC THEATRES",
    "ALAMO DRAFTHOUSE",
    "CINEMARK THEATRES",
    "REGAL CINEMAS"
]

GENRE_OPTIONS = [
    "Action",
    "Adventure",
    "Horror",
    "Comedy",
    "Drama",
    "Thriller / Suspense",
    "Family & Animation",
    "Musical",
    "Sci-Fi",
    "Documentary",
]


def get_user_input():
    """Collect date range, movie title, and genre."""
    print("\n" + "=" * 60)
    print("     TICKET SALES ATTRIBUTION")
    print("=" * 60)
    print("Movie viewers → theater platform hits with ticket sales projections")
    print("=" * 60 + "\n")

    movie_name = input("Enter Movie Title: ").strip()
    if not movie_name:
        print("You must provide a movie title.", file=sys.stderr)
        sys.exit(1)

    print("\nSelect Genre:")
    for i, g in enumerate(GENRE_OPTIONS, 1):
        print(f"  {i}. {g}")
    while True:
        try:
            choice = input("Enter number (1-{}): ".format(len(GENRE_OPTIONS))).strip()
            idx = int(choice)
            if 1 <= idx <= len(GENRE_OPTIONS):
                genre = GENRE_OPTIONS[idx - 1]
                break
        except ValueError:
            pass
        print("Invalid choice. Please enter a number 1-{}.".format(len(GENRE_OPTIONS)))

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

    print("\n" + "=" * 60)
    print("📊 SUMMARY:")
    print("=" * 60)
    print(f"🎥 Movie: '{movie_name}'")
    print(f"🎬 Genre: '{genre}'")
    print(f"📅 Date Range: {start_date.date()} to {end_date.date()}")
    print("=" * 60 + "\n")

    return {
        "movie_name": movie_name,
        "genre": genre,
        "start_date": start_date,
        "end_date": end_date,
    }


# ====================================
# === Helpers for dynamic filters ===
# ====================================
def format_search_term(term):
    """Format and escape search term for SQL LIKE matching."""
    if not term:
        return ""
    term = term.strip().lower().replace("'", "''").replace('%', '\\%').replace('_', '\\_')
    return term


def make_url_and_common_name_filter(search_terms, auto_format=True):
    """Build filter for URL and COMMON_NAME columns with variations."""
    all_variations = []
    for term in search_terms or []:
        if not term or not term.strip():
            continue
        if auto_format:
            variations = generate_search_term_variations(term)
        else:
            variations = [term.strip().lower()]
        for variation in variations:
            formatted = format_search_term(variation)
            if formatted:
                all_variations.append(formatted)

    if not all_variations:
        return ""
    seen = set()
    unique_variations = []
    for v in all_variations:
        if v not in seen:
            seen.add(v)
            unique_variations.append(v)
    return " OR ".join([
        f"(LOWER(URL) LIKE '%{term}%' OR LOWER(COMMON_NAME) LIKE '%{term}%')"
        for term in unique_variations
    ])


def make_common_name_filter(search_terms):
    """Build filter for COMMON_NAME column only."""
    formatted_terms = []
    for term in search_terms or []:
        if term and term.strip():
            cleaned = term.strip().lower().replace("'", "''")
            formatted_terms.append(cleaned)
    if not formatted_terms:
        return ""
    return " OR ".join([f"LOWER(COMMON_NAME) LIKE '%{term}%'" for term in formatted_terms])


# ===============================
# === Main processing / SQLs  ===
# ===============================
def run_query(conn, p):
    print("\n🔍 Running Ticket Sales Attribution Analysis...")
    print("=" * 60)
    cur = conn.cursor()

    movie_filter = make_url_and_common_name_filter([p['movie_name']], auto_format=True)
    theater_filter = make_common_name_filter(THEATER_PLATFORMS)

    # Step 1: Movie viewers in date range
    print("🎥 Step 1: Finding movie viewers...")
    cur.execute("DROP TABLE IF EXISTS TEMP_MOVIE_VIEWERS")
    cur.execute(f"""
        CREATE TEMPORARY TABLE TEMP_MOVIE_VIEWERS ENGINE = Memory AS
        SELECT DISTINCT UID
        FROM clickstream.clickstream_final
        WHERE DELIVERED BETWEEN toDate('{p['start_date'].date()}') AND toDate('{p['end_date'].date()}')
          AND ({movie_filter})
    """)
    result = cur.execute("SELECT count() FROM TEMP_MOVIE_VIEWERS").fetchone()
    total_movie_viewers = int(result[0]) if result and result[0] else 0
    print(f"   ✅ Found {total_movie_viewers:,} unique movie viewers\n")

    # Step 2: Theater visits for movie viewers
    print("🎬 Step 2: Finding theater platform visits for movie viewers...")
    cur.execute("DROP TABLE IF EXISTS TEMP_THEATER_VISITS_MOVIE_VIEWERS")
    cur.execute(f"""
        CREATE TEMPORARY TABLE TEMP_THEATER_VISITS_MOVIE_VIEWERS ENGINE = Memory AS
        SELECT tv.UID, tv.COMMON_NAME AS THEATER_PLATFORM
        FROM clickstream.clickstream_final tv
        WHERE tv.DELIVERED BETWEEN toDate('{p['start_date'].date()}') AND toDate('{p['end_date'].date()}')
          AND ({theater_filter})
          AND tv.UID IN (SELECT UID FROM TEMP_MOVIE_VIEWERS)
    """)
    result = cur.execute("SELECT uniqExact(UID) FROM TEMP_THEATER_VISITS_MOVIE_VIEWERS").fetchone()
    theater_viewers_count = int(result[0]) if result and result[0] else 0
    print(f"   ✅ Found {theater_viewers_count:,} unique theater visitors among movie viewers\n")

    # Step 3: Theater by platform (TOTAL HITS)
    print("📊 Step 3: Theater by platform breakdown...")
    # Wrapped in a subquery so the outer SELECT can expose the column as
    # THEATER_PLATFORM (downstream code reads df["THEATER_PLATFORM"]). ClickHouse
    # rejects an aggregate alias that collides with a column referenced in GROUP BY,
    # which Snowflake tolerated.
    theater_by_platform_query = """
        SELECT t.PLATFORM_NAME AS THEATER_PLATFORM, t.HITS
        FROM (
            SELECT max(THEATER_PLATFORM) AS PLATFORM_NAME,
                   uniqExact(UID)        AS HITS
            FROM TEMP_THEATER_VISITS_MOVIE_VIEWERS
            GROUP BY upper(trim(THEATER_PLATFORM))
        ) t
        ORDER BY t.HITS DESC
    """
    df_theater = pd.read_sql(theater_by_platform_query, conn)

    # Apply same boosting as Talent_Theater_Attribution (15x default, etc.)
    if not df_theater.empty and "HITS" in df_theater.columns:
        for idx in df_theater.index:
            raw_val = int(df_theater.loc[idx, "HITS"]) if not pd.isna(df_theater.loc[idx, "HITS"]) else 0
            if raw_val > 0:
                multiplier = calculate_boost_multiplier(raw_val)
                df_theater.loc[idx, "HITS"] = int(raw_val * multiplier)
            else:
                df_theater.loc[idx, "HITS"] = 0
        df_theater["HITS"] = df_theater["HITS"].astype(int)

    print(f"   ✅ Theater by platform calculated ({len(df_theater)} platforms, boosted)\n")

    # Step 4: Demographics - create TEMP_DEMOS for movie viewers who visited theaters
    print("📊 Step 4: Fetching demographics for theater UIDs...")
    cur.execute("DROP TABLE IF EXISTS TEMP_THEATER_UIDS")
    cur.execute("""
        CREATE TEMPORARY TABLE TEMP_THEATER_UIDS ENGINE = Memory AS
        SELECT DISTINCT UID, THEATER_PLATFORM
        FROM TEMP_THEATER_VISITS_MOVIE_VIEWERS
    """)
    cur.execute("DROP TABLE IF EXISTS TEMP_DEMOS")
    cur.execute("""
        CREATE TEMPORARY TABLE TEMP_DEMOS ENGINE = Memory AS
        SELECT d.UID, d.GENDER, d.AGE, d.ETHNICITY, d.INCOME, d.DMA, d.DMA_PROVINCE, d.DMA_COUNTRY
        FROM userdata.user_data_sanitized d
        INNER JOIN TEMP_THEATER_UIDS u ON d.UID = u.UID
    """)
    # Join with theater platform for per-theater demographics
    cur.execute("DROP TABLE IF EXISTS TEMP_DEMOS_WITH_THEATER")
    cur.execute("""
        CREATE TEMPORARY TABLE TEMP_DEMOS_WITH_THEATER ENGINE = Memory AS
        SELECT td.*, tu.THEATER_PLATFORM
        FROM TEMP_DEMOS td
        INNER JOIN TEMP_THEATER_UIDS tu ON td.UID = tu.UID
    """)

    # Get demographics as dataframe (overall and per theater) - same structure as bg.py.
    # DMA_PROVINCE is non-Nullable String in CH, so the IS NOT NULL guard collapses
    # to the empty-string check via trim().
    demo_query_overall = """
        SELECT UID, GENDER, AGE, ETHNICITY, INCOME,
               if(trim(DMA_PROVINCE) != '', concat(DMA, ' ', DMA_PROVINCE), DMA) AS LOCATION
        FROM TEMP_DEMOS
    """
    demo_query_per_theater = """
        SELECT UID, THEATER_PLATFORM, GENDER, AGE, ETHNICITY, INCOME,
               if(trim(DMA_PROVINCE) != '', concat(DMA, ' ', DMA_PROVINCE), DMA) AS LOCATION
        FROM TEMP_DEMOS_WITH_THEATER
    """
    df_demo_overall = pd.read_sql(demo_query_overall, conn)
    df_demo_per_theater = pd.read_sql(demo_query_per_theater, conn)
    print(f"   ✅ Demographics retrieved (overall: {len(df_demo_overall):,} UIDs, per-theater: {len(df_demo_per_theater):,} rows)\n")

    print("=" * 60)
    print("✅ Analysis Complete!")
    print("=" * 60 + "\n")

    return {
        "df_theater": df_theater,
        "total_movie_viewers": total_movie_viewers,
        "df_demo_overall": df_demo_overall,
        "df_demo_per_theater": df_demo_per_theater,
    }


def compute_demographics(df_demo, demo_fields=None):
    """
    Compute demographics percentages (gender, age, income, ethnicity, location).
    Uses case-insensitive matching so "WHITE", "White", "white" are grouped as "WHITE".
    Returns dict: {field: {value: percentage}}
    """
    if demo_fields is None:
        demo_fields = ["GENDER", "AGE", "INCOME", "ETHNICITY", "LOCATION"]
    result = {}
    if len(df_demo) == 0:
        return result

    for field in demo_fields:
        if field not in df_demo.columns:
            continue
        # Filter valid values per bg.py logic (case-insensitive where applicable)
        if field == "GENDER":
            gender_upper = df_demo[field].astype(str).str.strip().str.upper()
            allowed = {"FEMALE", "MALE", "TRANS MALE", "TRANS FEMALE", "NON-BINARY"}
            valid = df_demo[gender_upper.isin(allowed)]
        elif field == "AGE":
            age_str = df_demo[field].astype(str).str.strip()
            mask = df_demo[field].notna() & (age_str != "") & (age_str != "nan")
            mask &= ~age_str.str.upper().isin(["OTHER", "PREFER NOT TO SAY"])
            valid = df_demo[mask]
        elif field in ("ETHNICITY", "INCOME"):
            valid = df_demo[df_demo[field].notna() & (df_demo[field].astype(str).str.strip() != "")]
        elif field == "LOCATION":
            excluded = ("", "Other", "Prefer not to say", "De Mi", "Military Base",
                       "Hickory NC", "Salem OR", "Mansfield OH", "Worcester MA",
                       "Manchester NH", "Fort Collins CO", "Jacksonville NC")
            excluded_upper = {x.upper() for x in excluded}
            loc_str = df_demo[field].astype(str).str.strip()
            mask = df_demo[field].notna() & (loc_str != "") & (loc_str != "nan")
            mask &= ~loc_str.str.upper().isin(excluded_upper)
            valid = df_demo[mask]
        else:
            valid = df_demo[df_demo[field].notna() & (df_demo[field].astype(str).str.strip() != "")]

        # Group by case-insensitive value; for INCOME also consolidate "$75K - $100K" vs "$75K-$100K"
        raw = valid[field].astype(str).str.strip()
        raw = raw[(raw != "") & (raw != "nan")]
        if field == "INCOME":
            # Normalize: collapse spaces around hyphens so "$75K - $100K" and "$75K-$100K" merge
            normalized = raw.str.upper().str.replace(r"\s*-\s*", "-", regex=True).str.replace(r"\s+", " ", regex=True).str.strip()
        else:
            normalized = raw.str.upper()
        counts = normalized.value_counts()
        field_total = counts.sum()
        if field_total == 0:
            continue
        if field == "INCOME":
            # Use consistent display format: "$75K - $100K" (space around hyphen)
            def _fmt_income(k):
                s = str(k).replace("-", " - ")
                return s
            result[field] = {_fmt_income(k): round(100.0 * v / field_total, 2) for k, v in counts.items()}
        else:
            result[field] = {str(k): round(100.0 * v / field_total, 2) for k, v in counts.items()}
    return result


def _extract_json_object(text):
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


def _normalize_pct_plan(raw_map, labels):
    if not labels:
        return {}
    label_map = {str(lbl).strip().upper(): str(lbl).strip().upper() for lbl in labels}
    vals = {}
    for k, v in (raw_map or {}).items():
        key = str(k).strip().upper()
        if key in label_map:
            try:
                vals[label_map[key]] = max(0.0, float(v))
            except (ValueError, TypeError):
                continue
    if not vals:
        even = round(100.0 / len(labels), 2)
        return {str(lbl).strip().upper(): even for lbl in labels}
    total = sum(vals.values())
    if total <= 0:
        even = round(100.0 / len(labels), 2)
        return {str(lbl).strip().upper(): even for lbl in labels}
    norm = {}
    running = 0.0
    ordered = [str(lbl).strip().upper() for lbl in labels]
    for i, lbl in enumerate(ordered):
        if i == len(ordered) - 1:
            norm[lbl] = round(max(0.0, 100.0 - running), 2)
        else:
            v = (vals.get(lbl, 0.0) * 100.0) / total
            v = round(v, 2)
            norm[lbl] = v
            running += v
    return norm


def _default_ticket_demo_plan(genre):
    g = (genre or "").lower()
    if "family" in g or "animation" in g:
        return {
            "gender": {"FEMALE": 52.0, "MALE": 46.0, "NON-BINARY": 1.0, "TRANS MALE": 0.5, "TRANS FEMALE": 0.5},
            "age": {"17 AND UNDER": 28.0, "18-24": 18.0, "25-34": 20.0, "35-44": 14.0, "45-54": 10.0, "55-64": 6.0, "65 OR OLDER": 4.0},
        }
    if "action" in g or "adventure" in g or "sci-fi" in g:
        return {
            "gender": {"MALE": 54.0, "FEMALE": 44.0, "NON-BINARY": 1.0, "TRANS MALE": 0.5, "TRANS FEMALE": 0.5},
            "age": {"18-24": 24.0, "25-34": 28.0, "35-44": 20.0, "45-54": 13.0, "17 AND UNDER": 8.0, "55-64": 5.0, "65 OR OLDER": 2.0},
        }
    return {
        "gender": {"FEMALE": 50.0, "MALE": 48.0, "NON-BINARY": 1.0, "TRANS MALE": 0.5, "TRANS FEMALE": 0.5},
        "age": {"18-24": 18.0, "25-34": 24.0, "35-44": 20.0, "45-54": 14.0, "55-64": 10.0, "17 AND UNDER": 8.0, "65 OR OLDER": 6.0},
    }


# ===========================================================================
# === AI Validation Pipeline (Box-Office anchored, Claude-first)         ===
# ===========================================================================
# Two-tier auditor:
#   1) PRIMARY — Claude (Opus 4.7 with native web_search, Sonnet 4.6 fallback)
#      researches the real US domestic box office from Box Office Mojo / The
#      Numbers / Variety / Deadline / THR, audits our projected ticket sales
#      against that gross, and returns a structured JSON plan including a
#      BIDIRECTIONAL scale factor (we may scale UP if under-projecting OR
#      DOWN if over-projecting), an audience-skew override, and a research
#      summary with citations. This mirrors the same Claude-audit pattern
#      used by BG.py's hybrid reasoning path.
#   2) FALLBACK — when ANTHROPIC_API_KEY is missing or the Claude call fails,
#      fall back to the GPT-4o web-search + audit pair below.
#
# A structured "AI VALIDATION" block (PASS/FLAGGED + flags + research
# summary + applied adjustments + model used) is appended to the CSV so
# the analyst can see exactly what was checked and what changed.

_box_office_cache = {}

# Anthropic web-search tool descriptors. Mirror BG hybrid_reasoning.py:
# prefer the 2026-02-09 tool (Opus 4.6+ / Sonnet 4.6+); fall back to the
# 2025-03-05 tool for accounts that don't yet have the newer descriptor.
_ANTHROPIC_WEB_SEARCH = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 8,
}
_ANTHROPIC_WEB_SEARCH_LEGACY = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 8,
}


def _load_claude_helpers():
    """Import the Claude wrapper used elsewhere in the repo (BG.py).

    Returns (claude_messages, get_claude_client) or (None, None) on failure.
    The repo's migration/ folder is on sys.path via the top-of-file shim, so
    both `claude_client` and `migration.claude_client` resolve.
    """
    try:
        from claude_client import claude_messages, get_claude_client  # type: ignore
        return claude_messages, get_claude_client
    except Exception:
        pass
    try:
        from migration.claude_client import claude_messages, get_claude_client  # type: ignore
        return claude_messages, get_claude_client
    except Exception:
        return None, None


def _research_and_validate_with_claude(movie_name, genre, start_date, end_date,
                                        platform_totals, total_tickets,
                                        total_tickets_gen_pop, projected_sales_base,
                                        projected_sales_gen_pop, demo_overall):
    """One-shot Claude call: web-search + audit + structured JSON.

    Uses native Anthropic web_search so the model researches Box Office Mojo /
    The Numbers / Variety / Deadline / THR inline rather than relying on a
    separate research step. Allows BIDIRECTIONAL anchoring (scale up if
    under-projecting, down if over-projecting). Returns the parsed JSON dict
    plus a ``_model_used`` key, or None on failure.
    """
    claude_messages, get_claude_client = _load_claude_helpers()
    if claude_messages is None or get_claude_client is None:
        return None
    if get_claude_client() is None:
        return None

    platform_breakdown = "\n".join(
        f"  - {plat}: {hits:,} hits" for plat, hits in platform_totals.items()
    )

    system = (
        "You are a senior box-office research analyst auditing a US-only DIGITAL "
        "ticket sales projection produced by a 10M-person behavioral panel. Read "
        "the calibration rule below carefully — your output must respect it.\n\n"
        "=== CRITICAL CALIBRATION RULE ===\n"
        "The Crosswalk panel measures DIGITAL ticket sales only — visits to "
        "Fandango, AMC.com, Cinemark.com, Regal.com, and Alamo Drafthouse. "
        f"Digital pre-purchase historically captures {DIGITAL_SALES_FACTOR_LOW*100:.0f}-"
        f"{DIGITAL_SALES_FACTOR_HIGH*100:.0f}% of total US theatrical "
        "ticket sales (the rest is walk-up box office, third-party resellers "
        "like Atom, group/corporate sales, and theater apps not in our panel).\n"
        "Therefore our projected_sales_gen_pop MUST land in the band:\n"
        f"  researched_US_gross * {DIGITAL_SALES_FACTOR_LOW:.2f}  to  "
        f"researched_US_gross * {DIGITAL_SALES_FACTOR_HIGH:.2f}\n"
        f"(midpoint ~{DIGITAL_SALES_FACTOR_MID:.2f}x). This is NOT optional. "
        "Anchoring to 1.0x the researched gross would over-project by ~40%, "
        "which destroys dashboard credibility.\n\n"
        "=== YOUR THREE STAGES ===\n"
        "(1) RESEARCH. Use web_search to find REAL US DOMESTIC box office for "
        "the film (NOT worldwide, NOT international — domestic only). Prioritize "
        "Box Office Mojo and The Numbers for the dollar figures. For very recent "
        "releases still in theaters, Variety, Deadline, and THR weekend recaps "
        "are acceptable. Always cite the source for each figure. Be careful: "
        "Wikipedia AI Overviews often quote WORLDWIDE gross — you need DOMESTIC.\n\n"
        "(2) AUDIENCE RESEARCH. Use web_search to find primary audience age and "
        "gender skew. Look at Variety audience reports, Nielsen, Samba TV, "
        "EntTelligence, ComScore PostTrak, CinemaScore exit polls, and major "
        "trade press. State whether the title is male-skew, female-skew, or "
        "balanced and the approximate percentages.\n\n"
        "(3) AUDIT. Compute target = researched_domestic_gross * "
        f"{DIGITAL_SALES_FACTOR_MID:.3f}. Compute scale_factor = target / "
        "our_current_projected_sales_gen_pop. Round to 3 decimals. State the "
        "scale direction. Suggest a sales range tight on the digital band: "
        f"[researched_gross * {DIGITAL_SALES_FACTOR_LOW:.2f}, "
        f"researched_gross * {DIGITAL_SALES_FACTOR_HIGH:.2f}].\n\n"
        "=== DEMOGRAPHICS ===\n"
        "The panel reflects who has time to browse on a research panel, NOT "
        "who buys movie tickets. If researched audience skew clearly differs "
        "from the panel-derived percentages, OVERRIDE the panel. Don't be "
        "timid. A film known to be female-skew must NEVER come out male-"
        "dominant in the final output, and vice versa.\n\n"
        "GENDER_SKEW RULE — read carefully:\n"
        '- "balanced" is a LAST RESORT, ONLY when CinemaScore / PostTrak / '
        'Nielsen explicitly report a near-50/50 split (49-51% on either side).\n'
        '- Default to "male" or "female" based on:\n'
        '   * star, director, marketing target audience\n'
        '   * source material (e.g. fashion / wedding / friendship-driven '
        'comedies skew female; superhero / war / action skew male)\n'
        '   * franchise history (Devil Wears Prada, Bridget Jones, Sex and '
        'the City, Eat Pray Love are female-skew classics)\n'
        '- If the researched percentages clearly favor one side (>55%), the '
        'gender_skew field MUST be "male" or "female", never "balanced".\n\n'
        "PER-FIELD RULES:\n"
        "- gender: complete plan, percentages sum to ~100, MUST reflect the "
        "  gender_skew direction (FEMALE > MALE when gender_skew = female, "
        "  and vice versa).\n"
        "- age: complete 7-bucket plan, sums to ~100.\n"
        "- income: complete 6-bucket plan, sums to ~100. If unsure, anchor "
        "  toward the target audience's likely working/disposable income.\n"
        "- ethnicity: complete plan with WHITE, BLACK, HISPANIC, ASIAN, "
        "  OTHER, sums to ~100. Default close to US Census if no research.\n"
        "- Never leave any of these fields empty.\n\n"
        "OUTPUT FORMAT: JSON only. No markdown fences, no commentary."
    )

    user = (
        f"MOVIE: {movie_name}\n"
        f"GENRE: {genre}\n"
        f"DATE RANGE: {start_date} to {end_date}\n\n"
        f"PER-PLATFORM HITS (already boosted, sum to Total Tickets):\n{platform_breakdown}\n\n"
        f"OUR PROJECTIONS (panel-derived, projected from {SAMPLE_REPRESENTS:,} to US pop):\n"
        f"- Total Tickets (panel, boosted): {total_tickets:,}\n"
        f"- Total Tickets (US Gen Pop): {total_tickets_gen_pop:,.0f}\n"
        f"- Projected Sales (US Gen Pop, before audit): ${projected_sales_gen_pop:,.0f}\n\n"
        f"CURRENT OVERALL DEMOGRAPHICS (from panel):\n{demo_overall}\n\n"
        f"INSTRUCTIONS — execute in this exact order:\n"
        f"1. web_search the REAL US DOMESTIC gross (NOT worldwide). State the dollar "
        f"   figure, the source, and whether the film is still in theatrical release.\n"
        f"2. web_search the primary audience age + gender skew.\n"
        f"3. Compute target_sales = researched_domestic_gross * "
        f"{DIGITAL_SALES_FACTOR_MID:.3f}. This is the digital-sales midpoint anchor.\n"
        f"4. Compute scale_factor_midpoint = target_sales / "
        f"${projected_sales_gen_pop:,.0f}. Round to 3 decimals.\n"
        f"5. Set suggested_projected_sales_range_genpop = "
        f"   [researched_gross * {DIGITAL_SALES_FACTOR_LOW:.2f}, "
        f"researched_gross * {DIGITAL_SALES_FACTOR_HIGH:.2f}].\n"
        f"6. Set scale_direction to 'up' if scale_factor_midpoint > 1.05, 'down' "
        f"   if < 0.95, else 'none'.\n"
        f"7. Return the JSON below — EVERY field required, including a non-empty "
        f"   gender plan.\n\n"
        f"Return JSON ONLY in this shape:\n"
        f"{{\n"
        f'  "passed": false,\n'
        f'  "tickets_plausible": <bool>,\n'
        f'  "sales_plausible": <bool>,\n'
        f'  "demographics_plausible": <bool>,\n'
        f'  "researched_domestic_gross_usd": <number>,\n'
        f'  "research_sources": ["Box Office Mojo", "Variety", "..."],\n'
        f'  "flags": ["each concern as a complete sentence"],\n'
        f'  "scale_direction": "up" | "down" | "none",\n'
        f'  "scale_factor_midpoint": <decimal>,\n'
        f'  "suggested_projected_sales_range_genpop": [<low_usd>, <high_usd>],\n'
        f'  "suggested_total_tickets_range_genpop": [<low>, <high>],\n'
        f'  "gender_skew": "male" | "female" | "balanced",\n'
        f'  "age": {{"17 AND UNDER": <pct>, "18-24": <pct>, "25-34": <pct>, "35-44": <pct>, "45-54": <pct>, "55-64": <pct>, "65 OR OLDER": <pct>}},\n'
        f'  "gender": {{"MALE": <pct>, "FEMALE": <pct>, "NON-BINARY": <pct>, "TRANS MALE": <pct>, "TRANS FEMALE": <pct>}},\n'
        f'  "income": {{"$0 - $24,999": <pct>, "$25,000 - $49,999": <pct>, "$50,000 - $74,999": <pct>, "$75,000 - $99,999": <pct>, "$100,000 - $149,999": <pct>, "$150,000+": <pct>}},\n'
        f'  "ethnicity": {{"WHITE": <pct>, "BLACK": <pct>, "HISPANIC": <pct>, "ASIAN": <pct>, "OTHER": <pct>}},\n'
        f'  "research_summary": "5-10 sentences of researched facts with citations",\n'
        f'  "reasoning": "2-3 sentences explaining the digital-sales anchor and direction",\n'
        f'  "overall_assessment": "one sentence"\n'
        f"}}\n"
        f"Set passed=false whenever ANY adjustment is needed. NEVER return an "
        f"empty gender plan. Default 'balanced' is forbidden unless research "
        f"explicitly shows 49-51% on either side. Use the researched audience "
        f"profile, marketing target, and franchise history to pick a direction."
    )

    audit_model = os.environ.get("CLAUDE_TICKET_AUDIT_MODEL") or "claude-opus-4-7"

    print(f"   🧠 Asking Claude ({audit_model}) to research + audit ticket sales...")
    raw = claude_messages(
        system=system,
        user=user,
        model=audit_model,
        max_tokens=6000,
        temperature=0.2,
        tools=[_ANTHROPIC_WEB_SEARCH],
    )
    used_model = audit_model
    if not raw:
        fallback_model = "claude-sonnet-4-6"
        print(f"   🔁 Retrying Claude audit with {fallback_model} + legacy web_search")
        raw = claude_messages(
            system=system,
            user=user,
            model=fallback_model,
            max_tokens=6000,
            temperature=0.2,
            tools=[_ANTHROPIC_WEB_SEARCH_LEGACY],
        )
        used_model = fallback_model
    if not raw:
        return None

    parsed = _extract_json_object(raw)
    if not parsed:
        # Some Claude responses include commentary plus an embedded fenced JSON
        # block. Strip fences and retry once.
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lstrip().lower().startswith("json"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        parsed = _extract_json_object(cleaned)
    if not parsed:
        print("   ⚠️  Claude audit returned no parseable JSON")
        return None

    parsed["_model_used"] = used_model
    parsed["research"] = (parsed.get("research_summary") or "").strip()
    return parsed


def _research_box_office(client, movie_name):
    """Web-search for real US domestic box office data for the given film.

    Uses gpt-4o-search-preview and prefers canonical sources (Box Office Mojo,
    The Numbers) with trade-press fallback (Variety, Deadline, THR) for
    very recent releases. Returns a text summary or "" on failure. Results are
    cached in-memory per process.
    """
    if not client or not movie_name:
        return ""
    cache_key = movie_name.strip().lower()
    if cache_key in _box_office_cache:
        return _box_office_cache[cache_key]

    prompt = (
        f'Search for the most recent real US domestic box office data for the film "{movie_name}". '
        f'Report:\n'
        f'- US domestic gross to date (and whether the film is still in theatrical release)\n'
        f'- Opening weekend gross (US)\n'
        f'- Estimated total US tickets sold (gross / ~$11 average ticket price)\n'
        f'- Wide release date\n'
        f'- Primary audience age and gender skew\n\n'
        f'PREFER Box Office Mojo and The Numbers for the dollar figures (these are the canonical '
        f'sources). For very recent releases (still in theaters), Variety, Deadline, and THR '
        f'weekend recaps are acceptable. Cite the source for each number. Be concise — just '
        f'the key figures. If no data is available, say so explicitly.'
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-search-preview",
            messages=[{"role": "user", "content": prompt}],
            web_search_options={"search_context_size": "medium"},
        )
        text = (resp.choices[0].message.content or "").strip() if resp.choices else ""
        _box_office_cache[cache_key] = text
        if text:
            print(f"   🔍 Box office research for '{movie_name}': {len(text)} chars retrieved")
        return text
    except Exception as e:
        print(f"   ⚠️  Box office research failed for '{movie_name}': {e}")
        _box_office_cache[cache_key] = ""
        return ""


def ai_validate_ticket_metrics(movie_name, genre, start_date, end_date,
                                platform_totals, total_tickets, total_tickets_gen_pop,
                                projected_sales_base, projected_sales_gen_pop,
                                demo_overall):
    """Validate Ticket Sales Tracker output against web-researched box-office truth.

    PRIMARY path: Claude (Opus 4.7 → Sonnet 4.6) with native web_search does
    research + audit in a single call. Returns structured JSON with a
    BIDIRECTIONAL scale factor, audience-skew override, and citations.

    FALLBACK path: GPT-4o-search-preview (research) + GPT-4o (audit). Same
    JSON schema. Triggered only when ANTHROPIC_API_KEY is missing or the
    Claude call fails. The fallback also supports bidirectional scaling.
    """
    # Try Claude first — this is the higher-reasoning path.
    claude_result = _research_and_validate_with_claude(
        movie_name=movie_name,
        genre=genre,
        start_date=start_date,
        end_date=end_date,
        platform_totals=platform_totals,
        total_tickets=total_tickets,
        total_tickets_gen_pop=total_tickets_gen_pop,
        projected_sales_base=projected_sales_base,
        projected_sales_gen_pop=projected_sales_gen_pop,
        demo_overall=demo_overall,
    )
    if claude_result:
        return claude_result

    # ----------------- GPT-4o fallback path -----------------
    try:
        from openai import OpenAI
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return {
                "passed": True,
                "flags": [],
                "note": "No OpenAI/Anthropic key; skipping validation",
                "research": "",
                "overall_assessment": "Validation skipped — no API keys configured.",
                "_model_used": "none",
            }
        client = OpenAI(api_key=api_key)
    except Exception as e:
        return {
            "passed": True,
            "flags": [],
            "note": f"OpenAI not available: {e}",
            "research": "",
            "overall_assessment": "Validation skipped — OpenAI client unavailable.",
            "_model_used": "none",
        }

    research = _research_box_office(client, movie_name)
    research_block = ""
    if research:
        research_block = (
            "\n=== REAL-WORLD US BOX OFFICE DATA (from web search) ===\n"
            "This is your PRIMARY reference. Anchor projected US ticket sales to this gross.\n\n"
            f"{research}\n"
        )

    platform_breakdown = "\n".join(
        f"  - {plat}: {hits:,} hits" for plat, hits in platform_totals.items()
    )

    prompt = (
        f"You are validating Ticket Sales Tracker output for a US-only theatrical release.\n\n"
        f"MOVIE: {movie_name}\n"
        f"GENRE: {genre}\n"
        f"DATE RANGE: {start_date} to {end_date}\n\n"
        f"PER-PLATFORM HITS (already boosted; sum to Total Tickets):\n{platform_breakdown}\n\n"
        f"OUR PROJECTED METRICS (from {SAMPLE_REPRESENTS:,}-person panel, projected to US pop):\n"
        f"- Total Tickets Sold (panel, boosted): {total_tickets:,}\n"
        f"- Total Tickets Sold (US Gen Pop projected): {total_tickets_gen_pop:,.0f}\n"
        f"- Projected Ticket Sales (US Gen Pop): ${projected_sales_gen_pop:,.0f}\n\n"
        f"CURRENT OVERALL DEMOGRAPHICS: {demo_overall}\n"
        f"{research_block}\n"
        f"=== PHASE A: VALIDATE TICKETS & SALES (DIGITAL-ONLY ANCHOR) ===\n"
        f"The panel measures DIGITAL ticket sales only (Fandango / AMC.com /\n"
        f"Cinemark.com / Regal.com / Alamo). Digital captures ~"
        f"{DIGITAL_SALES_FACTOR_LOW*100:.0f}-{DIGITAL_SALES_FACTOR_HIGH*100:.0f}% of total US ticket sales.\n"
        f"- TARGET: projected_sales_gen_pop = researched_US_domestic_gross *\n"
        f"  {DIGITAL_SALES_FACTOR_MID:.3f} (acceptable band {DIGITAL_SALES_FACTOR_LOW:.2f}-"
        f"{DIGITAL_SALES_FACTOR_HIGH:.2f} of gross).\n"
        f"- If our number is OUTSIDE that band (above OR below), suggest a scale\n"
        f"  back inside the band. Under-projection is just as much of a credibility\n"
        f"  failure as over-projection.\n"
        f"- Tickets implied by gross = gross / $11 (avg US ticket price). Cross-check.\n\n"
        f"=== PHASE B: VALIDATE DEMOGRAPHICS ===\n"
        f"Compare our AGE/GENDER skew to the researched primary audience.\n"
        f"- The panel reflects who has time to browse online, NOT who buys tickets.\n"
        f"- If research says male-skew but our panel shows female-skew (or vice versa),\n"
        f"  OVERRIDE the panel with the researched percentages. Do NOT be timid.\n"
        f"- A known female-skew title must NEVER come out male-dominant in the final\n"
        f"  output, and vice versa.\n\n"
        f"=== PHASE C: EDGE CASES ===\n"
        f"- Short windows (1-2 weekends) naturally produce smaller numbers — don't flag low.\n"
        f"- Indie / limited / arthouse releases have modest numbers — that is expected.\n"
        f"- The genre 2x factor for Family/Animation is already applied. Do not double-count.\n\n"
        f"Respond in JSON ONLY (no markdown fencing):\n"
        f"{{\n"
        f'  "passed": true/false,\n'
        f'  "tickets_plausible": true/false,\n'
        f'  "tickets_note": "brief explanation referencing real data if available",\n'
        f'  "sales_plausible": true/false,\n'
        f'  "sales_note": "brief explanation",\n'
        f'  "demographics_plausible": true/false,\n'
        f'  "demographics_note": "brief explanation",\n'
        f'  "flags": ["specific concerns if any"],\n'
        f'  "scale_direction": "up" | "down" | "none",\n'
        f'  "scale_factor_midpoint": <decimal; e.g. 5.0 means our number is 5x too low>,\n'
        f'  "suggested_total_tickets_range_genpop": [low, high],\n'
        f'  "suggested_projected_sales_range_genpop": [low, high],\n'
        f'  "researched_domestic_gross_usd": <number or null>,\n'
        f'  "research_sources": ["Box Office Mojo", "Variety", ...],\n'
        f'  "gender_skew": "male|female|balanced",\n'
        f'  "age": {{"17 AND UNDER": <pct>, "18-24": <pct>, "25-34": <pct>, "35-44": <pct>, "45-54": <pct>, "55-64": <pct>, "65 OR OLDER": <pct>}},\n'
        f'  "gender": {{"MALE": <pct>, "FEMALE": <pct>, "NON-BINARY": <pct>, "TRANS MALE": <pct>, "TRANS FEMALE": <pct>}},\n'
        f'  "reasoning": "2-3 sentences",\n'
        f'  "overall_assessment": "one-sentence summary"\n'
        f"}}"
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1200,
        )
        parsed = _extract_json_object(resp.choices[0].message.content if resp.choices else "")
    except Exception as e:
        return {
            "passed": True,
            "flags": [],
            "note": f"AI validation error: {e}",
            "research": research,
            "overall_assessment": "Validation skipped — OpenAI call failed.",
            "_model_used": "gpt-4o",
        }

    if not parsed:
        return {
            "passed": True,
            "flags": [],
            "note": "AI returned no JSON; skipping",
            "research": research,
            "overall_assessment": "Validation skipped — no parseable JSON returned.",
            "_model_used": "gpt-4o",
        }

    parsed["research"] = research
    parsed["_model_used"] = "gpt-4o"
    return parsed


# Genre / franchise heuristic for upgrading "balanced" -> directional skew when
# the AI hedged. These are well-established CinemaScore patterns: titles in
# these buckets historically skew female (or male) regardless of how the AI
# answered. Lower-case keyword matching on the movie title or genre.
_FEMALE_SKEW_KEYWORDS = (
    # Franchise / title cues
    "devil wears prada", "bridget jones", "sex and the city", "eat pray love",
    "mamma mia", "barbie", "wonder woman", "fifty shades", "twilight",
    "hunger games", "wicked", "mean girls", "legally blonde", "princess diaries",
    "crazy rich asians", "love actually", "the notebook", "magic mike",
    "step up", "pitch perfect", "freaky friday", "anyone but you",
    # Genre / theme cues
    "romance", "romantic", "fashion", "wedding", "rom-com", "musical",
)
_MALE_SKEW_KEYWORDS = (
    "fast and furious", "fast & furious", "john wick", "mission impossible",
    "top gun", "transformers", "predator", "alien", "terminator", "rambo",
    "expendables", "rocky", "creed", "gladiator", "dune", "oppenheimer",
    "action", "war", "military", "heist", "spy", "thriller", "sci-fi",
)


def _genre_skew_hint(movie_name, genre):
    """Return 'female', 'male', or '' based on title/genre keyword match.

    Used to upgrade a hedged ``gender_skew = "balanced"`` answer from the AI
    when the title clearly belongs to a known-skew franchise or genre. This
    is the safety net that prevents Devil Wears Prada 2 from coming out
    near-50/50 just because the AI didn't pull the right research.
    """
    haystack = f"{(movie_name or '').lower()} {(genre or '').lower()}"
    for kw in _FEMALE_SKEW_KEYWORDS:
        if kw in haystack:
            return "female"
    for kw in _MALE_SKEW_KEYWORDS:
        if kw in haystack:
            return "male"
    return ""


def _default_income_plan_from_skew(researched_skew, genre):
    """Default income percentage plan when AI omits the income field.

    Coarse heuristic: female-skew titles tend to lean slightly toward dual-
    income households / disposable-income brackets ($75K-$150K); male-skew
    action skews younger / mid-income; family/animation pulls a wider mid-
    income spread. These match the standard 6-bucket schema used by BG.py.
    """
    g = (genre or "").lower()
    if "family" in g or "animation" in g:
        return {"$0 - $24,999": 10.0, "$25,000 - $49,999": 18.0,
                "$50,000 - $74,999": 22.0, "$75,000 - $99,999": 20.0,
                "$100,000 - $149,999": 18.0, "$150,000+": 12.0}
    if researched_skew == "female":
        return {"$0 - $24,999": 10.0, "$25,000 - $49,999": 16.0,
                "$50,000 - $74,999": 20.0, "$75,000 - $99,999": 18.0,
                "$100,000 - $149,999": 20.0, "$150,000+": 16.0}
    if researched_skew == "male":
        return {"$0 - $24,999": 12.0, "$25,000 - $49,999": 20.0,
                "$50,000 - $74,999": 22.0, "$75,000 - $99,999": 18.0,
                "$100,000 - $149,999": 16.0, "$150,000+": 12.0}
    return {"$0 - $24,999": 13.0, "$25,000 - $49,999": 19.0,
            "$50,000 - $74,999": 20.0, "$75,000 - $99,999": 17.0,
            "$100,000 - $149,999": 18.0, "$150,000+": 13.0}


def _default_ethnicity_plan_from_skew(researched_skew, genre):
    """Default ethnicity percentage plan when AI omits the ethnicity field.

    Anchored to US Census-ish percentages (mainstream Hollywood titles
    typically index close to gen pop unless explicitly marketed otherwise).
    """
    return {"WHITE": 60.0, "HISPANIC": 19.0, "BLACK": 13.0,
            "ASIAN": 6.0, "OTHER": 2.0}


def _default_gender_plan_from_skew(researched_skew):
    """Default gender percentage plan synthesized from a researched skew.

    Used as a fallback when the AI confidently reports a gender_skew direction
    but returns an empty or missing ``gender`` field. Without this, an empty
    plan would silently leave the panel-derived percentages in place, which
    is exactly the failure mode that produced MALE 49.79% on Devil Wears
    Prada 2 (a famously female-skew title).
    """
    skew = (researched_skew or "").strip().lower()
    if skew == "female":
        return {"MALE": 28.0, "FEMALE": 70.0, "NON-BINARY": 1.0,
                "TRANS MALE": 0.5, "TRANS FEMALE": 0.5}
    if skew == "male":
        return {"MALE": 70.0, "FEMALE": 28.0, "NON-BINARY": 1.0,
                "TRANS MALE": 0.5, "TRANS FEMALE": 0.5}
    return {"MALE": 49.0, "FEMALE": 49.0, "NON-BINARY": 1.0,
            "TRANS MALE": 0.5, "TRANS FEMALE": 0.5}


def _default_age_plan_from_skew(researched_skew, genre):
    """Default age percentage plan when the AI returns a skew but no age field.

    Coarse heuristic: lean toward the known skew of the genre. Better than
    leaving the panel-derived (often noisy) percentages untouched.
    """
    g = (genre or "").lower()
    if "family" in g or "animation" in g:
        return {"17 AND UNDER": 28.0, "18-24": 14.0, "25-34": 22.0,
                "35-44": 18.0, "45-54": 10.0, "55-64": 5.0, "65 OR OLDER": 3.0}
    if "horror" in g or "thriller" in g:
        return {"17 AND UNDER": 6.0, "18-24": 30.0, "25-34": 30.0,
                "35-44": 18.0, "45-54": 10.0, "55-64": 4.0, "65 OR OLDER": 2.0}
    if "action" in g or "adventure" in g or "sci-fi" in g:
        return {"17 AND UNDER": 8.0, "18-24": 22.0, "25-34": 28.0,
                "35-44": 22.0, "45-54": 12.0, "55-64": 5.0, "65 OR OLDER": 3.0}
    # Comedy / drama / general
    return {"17 AND UNDER": 5.0, "18-24": 22.0, "25-34": 26.0,
            "35-44": 22.0, "45-54": 14.0, "55-64": 7.0, "65 OR OLDER": 4.0}


def _clamp_target_sales_to_digital_band(researched_gross_usd, projected_sales_gen_pop,
                                         suggested_range):
    """Compute the post-AI target projected_sales_gen_pop.

    Hard rule: if we have a researched US domestic gross, the target sales
    figure MUST land inside [gross * DIGITAL_SALES_FACTOR_LOW,
    gross * DIGITAL_SALES_FACTOR_HIGH]. We honor the AI's suggested midpoint
    if it falls inside that band, otherwise we override with the band
    midpoint. This is the safety net that prevents the auditor from
    producing $681M on a $175M film.

    Returns the target projected_sales_gen_pop (float) or None if we can't
    compute one (no researched gross available).
    """
    try:
        gross = float(researched_gross_usd)
    except (ValueError, TypeError):
        return None
    if gross <= 0:
        return None

    band_lo = gross * DIGITAL_SALES_FACTOR_LOW
    band_hi = gross * DIGITAL_SALES_FACTOR_HIGH
    band_mid = gross * DIGITAL_SALES_FACTOR_MID

    ai_mid = None
    if isinstance(suggested_range, list) and len(suggested_range) == 2:
        try:
            lo_ai = float(suggested_range[0])
            hi_ai = float(suggested_range[1])
            if lo_ai >= 0 and hi_ai >= lo_ai:
                ai_mid = (lo_ai + hi_ai) / 2.0
        except (ValueError, TypeError):
            ai_mid = None

    if ai_mid is not None and band_lo <= ai_mid <= band_hi:
        return ai_mid
    return band_mid


def _enforce_gender_skew(gender_plan, researched_skew):
    """Ensure the gender plan reflects the researched skew direction.

    If researched skew is "female" but the plan has MALE >= FEMALE, swap the
    two percentages. Same for "male" with FEMALE >= MALE. This is a hard
    safety net for cases where the LLM returns demographically wrong
    percentages despite explicit instructions (e.g. Devil Wears Prada
    coming out male-dominant). Non-binary / trans buckets are preserved
    untouched. Returns the (possibly swapped) plan dict.
    """
    if not gender_plan or researched_skew not in ("male", "female"):
        return gender_plan
    plan = dict(gender_plan)

    def _pct(key):
        try:
            return float(plan.get(key, 0))
        except (ValueError, TypeError):
            return 0.0

    male_pct = _pct("MALE")
    female_pct = _pct("FEMALE")
    if researched_skew == "female" and male_pct > female_pct:
        plan["MALE"], plan["FEMALE"] = female_pct, male_pct
    elif researched_skew == "male" and female_pct > male_pct:
        plan["MALE"], plan["FEMALE"] = female_pct, male_pct
    return plan


def apply_demo_plan_to_section(demo_section, gender_plan, age_plan,
                                income_plan, ethnicity_plan, researched_skew):
    """Apply a researched demographic plan to a single demographics dict.

    Used to keep OVERALL and every per-theater section coherent with the
    researched audience profile. Operates in place on ``demo_section`` (which
    looks like ``{"GENDER": {...}, "AGE": {...}, "INCOME": {...}, ...}``).
    GENDER runs through _enforce_gender_skew so a known female-skew title
    can never come out male-dominant even when the LLM plan was sloppy.
    Returns the number of fields that were modified.
    """
    modified = 0
    if "GENDER" in demo_section and demo_section["GENDER"] and gender_plan:
        plan = _enforce_gender_skew(gender_plan, researched_skew)
        new_gender = _normalize_pct_plan(plan, list(demo_section["GENDER"].keys()))
        if new_gender:
            demo_section["GENDER"] = new_gender
            modified += 1
    if "AGE" in demo_section and demo_section["AGE"] and age_plan:
        new_age = _normalize_pct_plan(age_plan, list(demo_section["AGE"].keys()))
        if new_age:
            demo_section["AGE"] = new_age
            modified += 1
    if "INCOME" in demo_section and demo_section["INCOME"] and income_plan:
        new_income = _normalize_pct_plan(income_plan, list(demo_section["INCOME"].keys()))
        if new_income:
            demo_section["INCOME"] = new_income
            modified += 1
    if "ETHNICITY" in demo_section and demo_section["ETHNICITY"] and ethnicity_plan:
        new_eth = _normalize_pct_plan(ethnicity_plan, list(demo_section["ETHNICITY"].keys()))
        if new_eth:
            demo_section["ETHNICITY"] = new_eth
            modified += 1
    return modified


def apply_ai_ticket_adjustments(validation, platform_totals, total_tickets,
                                total_tickets_gen_pop, projected_sales_base,
                                projected_sales_gen_pop, demo_overall,
                                genre="", movie_name=""):
    """Apply post-AI corrections anchored to the digital-sales band.

    Pipeline:
      1. Compute the target projected_sales_gen_pop via
         _clamp_target_sales_to_digital_band — this is the SAFETY NET that
         forces our final number to land inside
         [gross * DIGITAL_SALES_FACTOR_LOW, gross * DIGITAL_SALES_FACTOR_HIGH]
         regardless of what the AI suggested. This is what prevents the
         Devil-Wears-Prada-2 failure mode ($175M gross → $681M dashboard).
      2. Apply the resulting scale factor uniformly to per-platform hits,
         total tickets, both gen-pop projections, and both dollar figures so
         every row still reconciles.
      3. ALWAYS apply demographics (no longer gated on demographics_plausible).
         When the AI returns an empty gender/age plan but a confident skew,
         synthesize a default plan from the skew direction so the safety
         net catches even silent regressions.
      4. Run _enforce_gender_skew so a known female-skew title can never
         come out male-dominant.

    Per-theater demographics stay untouched (they're tied to actual
    ClickHouse UID joins, that's panel truth).
    """
    changes = []
    if validation.get("passed", True):
        return (platform_totals, total_tickets, total_tickets_gen_pop,
                projected_sales_base, projected_sales_gen_pop, demo_overall, changes)

    # ---- Anchor to digital-sales band ----
    researched_gross = validation.get("researched_domestic_gross_usd")
    suggested_sales = validation.get("suggested_projected_sales_range_genpop") or []
    target_sales = _clamp_target_sales_to_digital_band(
        researched_gross, projected_sales_gen_pop, suggested_sales,
    )
    if target_sales is None:
        # Fallback path when no researched gross is available: honor the
        # AI's suggested midpoint exactly as before. Better than nothing.
        if isinstance(suggested_sales, list) and len(suggested_sales) == 2:
            try:
                lo = float(suggested_sales[0])
                hi = float(suggested_sales[1])
                if lo >= 0 and hi >= lo:
                    target_sales = (lo + hi) / 2.0
            except (ValueError, TypeError):
                target_sales = None

    if target_sales is not None and projected_sales_gen_pop > 0 and target_sales > 0:
        raw_factor = target_sales / projected_sales_gen_pop
        # Tightened bounds: 0.05x to 10x. The previous 25x ceiling let
        # the auditor scale Devil Wears Prada 2 by 20x ($34M -> $681M).
        # 10x is plenty for legitimate under-projection (a major release
        # whose audience under-indexes on the panel).
        factor = max(0.05, min(10.0, raw_factor))
        if abs(factor - 1.0) > 0.05:  # only act outside the ±5% no-op band
            old_sales = projected_sales_gen_pop
            old_total_tickets = total_tickets
            projected_sales_gen_pop *= factor
            projected_sales_base *= factor
            total_tickets_gen_pop *= factor
            total_tickets = max(0, int(round(total_tickets * factor)))
            platform_totals = {
                plat: max(0, int(round(hits * factor)))
                for plat, hits in platform_totals.items()
            }
            arrow = "↑" if factor > 1.0 else "↓"
            gross_note = ""
            if isinstance(researched_gross, (int, float)) and researched_gross > 0:
                pct_of_gross = (projected_sales_gen_pop / float(researched_gross)) * 100.0
                gross_note = (
                    f" (anchored to {pct_of_gross:.1f}% of researched US gross "
                    f"~${float(researched_gross):,.0f}; digital band "
                    f"{DIGITAL_SALES_FACTOR_LOW*100:.0f}-"
                    f"{DIGITAL_SALES_FACTOR_HIGH*100:.0f}%)"
                )
            changes.append(
                f"{arrow} Scaled per-platform hits, total tickets, and projected sales by "
                f"{factor:.3f}{gross_note}: projected sales ${old_sales:,.0f} -> "
                f"${projected_sales_gen_pop:,.0f}; total tickets {old_total_tickets:,} -> "
                f"{total_tickets:,}."
            )

    # ---- Build the resolved demographic plan ----
    # This plan is applied to OVERALL here, and ALSO returned to the caller
    # so it can be applied to every per-theater section in write_output().
    researched_skew = (validation.get("gender_skew") or "").strip().lower()
    age_plan = validation.get("age") or {}
    gender_plan = validation.get("gender") or {}
    income_plan = validation.get("income") or {}
    ethnicity_plan = validation.get("ethnicity") or {}

    # Upgrade hedged "balanced" answers to a directional skew when the
    # title clearly belongs to a known-skew franchise/genre. This is the
    # safety net for Devil Wears Prada 2 coming out near-50/50 because
    # the AI returned gender_skew="balanced" with a balanced plan.
    if researched_skew in ("", "balanced"):
        hint = _genre_skew_hint(movie_name, genre)
        if hint:
            if researched_skew == "balanced":
                changes.append(
                    f"Upgraded AI's 'balanced' skew to '{hint}' based on title/genre "
                    f"heuristic (Devil Wears Prada / fashion / romance bucket → female; "
                    f"action / war / sci-fi → male)."
                )
            researched_skew = hint

    # Synthesize defaults whenever the AI omitted a field. Without this, the
    # empty-plan case silently leaves the (often noisy) panel-derived
    # percentages in place — which is exactly the Devil Wears Prada 2 bug.
    if not gender_plan and researched_skew in ("male", "female", "balanced"):
        gender_plan = _default_gender_plan_from_skew(researched_skew)
        changes.append(
            f"Synthesized default GENDER plan from researched {researched_skew}-skew "
            f"(AI returned no explicit plan)."
        )
    if not age_plan and researched_skew in ("male", "female", "balanced"):
        age_plan = _default_age_plan_from_skew(researched_skew, genre)
    if not income_plan and researched_skew in ("male", "female", "balanced"):
        income_plan = _default_income_plan_from_skew(researched_skew, genre)
    if not ethnicity_plan and researched_skew in ("male", "female", "balanced"):
        ethnicity_plan = _default_ethnicity_plan_from_skew(researched_skew, genre)

    # Apply to OVERALL section
    n_modified = apply_demo_plan_to_section(
        demo_overall, gender_plan, age_plan, income_plan, ethnicity_plan,
        researched_skew,
    )
    if n_modified > 0:
        skew_label = researched_skew or "balanced"
        changes.append(
            f"Aligned overall demographics ({n_modified} field(s)) to researched "
            f"{skew_label}-skew audience profile."
        )

    # Stash the resolved plan on the validation dict so the caller can also
    # apply it to per-theater sections without recomputing.
    validation["_resolved_plan"] = {
        "gender": gender_plan,
        "age": age_plan,
        "income": income_plan,
        "ethnicity": ethnicity_plan,
        "skew": researched_skew,
    }

    return (platform_totals, total_tickets, total_tickets_gen_pop,
            projected_sales_base, projected_sales_gen_pop, demo_overall, changes)


# =======================
# === Output writing  ===
# =======================
def write_output(results, p):
    print("📄 Writing results to CSV...")

    df_theater = results["df_theater"]
    total_movie_viewers = results["total_movie_viewers"]
    df_demo_overall = results["df_demo_overall"]
    df_demo_per_theater = results["df_demo_per_theater"]

    # Use ONLY exact-match rows for the 5 canonical platforms (exclude "fandango at home", "fandango | google", etc.)
    canonical_upper = {plat.upper().strip(): plat for plat in THEATER_PLATFORMS}
    platform_totals = {plat: 0 for plat in THEATER_PLATFORMS}
    for _, row in df_theater.iterrows():
        comm = (row["THEATER_PLATFORM"] or "").upper().strip()
        if comm in canonical_upper:
            canonical = canonical_upper[comm]
            hits = int(row["HITS"]) if not pd.isna(row["HITS"]) else 0
            platform_totals[canonical] = hits

    # Total Tickets = sum of the 5 displayed platform hits only
    total_tickets = sum(platform_totals.values())
    total_tickets_gen_pop = gen_pop_projection(total_tickets)

    # Genre check for 2x factor (family or animation)
    genre_lower = (p.get("genre") or "").lower()
    is_family_animation = "family" in genre_lower or "animation" in genre_lower
    ticket_multiplier = 2.0 if is_family_animation else 1.0

    # Projected ticket sales: total * $15, then * multiplier if family/animation
    projected_sales_base = total_tickets * 15.0 * ticket_multiplier
    projected_sales_gen_pop = total_tickets_gen_pop * 15.0 * ticket_multiplier

    # Demographics - overall (computed BEFORE building rows so the AI auditor
    # can compare against researched audience skew and the adjustments below
    # actually flow through to the CSV — the old single-pass alignment ran
    # AFTER rows were built so its number changes never reached the file).
    demo_overall = compute_demographics(df_demo_overall)

    # AI plausibility validation against real US box office. Claude (Opus 4.7
    # → Sonnet 4.6) is the primary auditor with native web_search; GPT-4o is
    # the fallback. Scaling is BIDIRECTIONAL — anchored to the researched US
    # domestic gross whether our panel is over- or under-projecting.
    print("🤖 Running AI plausibility check (Box Office anchored, bidirectional)...")
    validation = ai_validate_ticket_metrics(
        movie_name=p["movie_name"],
        genre=p.get("genre", ""),
        start_date=p["start_date"].date(),
        end_date=p["end_date"].date(),
        platform_totals=platform_totals,
        total_tickets=total_tickets,
        total_tickets_gen_pop=total_tickets_gen_pop,
        projected_sales_base=projected_sales_base,
        projected_sales_gen_pop=projected_sales_gen_pop,
        demo_overall=demo_overall,
    )
    model_used = validation.get("_model_used", "unknown")
    print(f"   🧠 Auditor: {model_used}")

    ai_changes = []
    if not validation.get("passed", True):
        print("⚠️  AI flagged potential issues:")
        for flag in validation.get("flags", []) or []:
            print(f"   • {flag}")
        print(f"   Assessment: {validation.get('overall_assessment', 'N/A')}")
        (platform_totals, total_tickets, total_tickets_gen_pop,
         projected_sales_base, projected_sales_gen_pop,
         demo_overall, ai_changes) = apply_ai_ticket_adjustments(
            validation, platform_totals, total_tickets, total_tickets_gen_pop,
            projected_sales_base, projected_sales_gen_pop, demo_overall,
            genre=p.get("genre", ""), movie_name=p.get("movie_name", ""),
        )
        if ai_changes:
            print("🤖 Applied AI corrections:")
            for c in ai_changes:
                print(f"   → {c}")
    else:
        note = validation.get("overall_assessment") or validation.get("note") or "Metrics look plausible"
        print(f"✅ AI validation passed: {note}")

    # ---- Build CSV rows using POST-ADJUSTMENT numbers ----
    rows = [
        ("", "TICKET SALES ATTRIBUTION RESULTS", "", "", "", ""),
        ("", "", "", "", "", ""),
        ("Movie", "", p["movie_name"], "", "", ""),
        ("Genre", "", p.get("genre", ""), f"(2x factor: {'Yes' if is_family_animation else 'No'})", "", ""),
        ("Date Range", "", f"{p['start_date'].date()} to {p['end_date'].date()}", "", "", ""),
        ("", "", "", "", "", ""),
        ("", "TOTAL HITS (MOVIE VIEWERS) → THEATER BY PLATFORM", "", "", "", ""),
        ("Platform", "Hits", "US Gen Pop Projection", "", "", ""),
    ]

    for platform in THEATER_PLATFORMS:
        hits = platform_totals[platform]
        genpop = format_gen_pop_full(gen_pop_projection(hits))
        rows.append((platform, hits, genpop, "", "", ""))

    rows.extend([
        ("", "", "", "", "", ""),
        ("Total Tickets Sold (sum of theater hits)", total_tickets, format_gen_pop_full(total_tickets_gen_pop), "", "", ""),
        ("Projected Ticket Sales (Total × $15" + (" × 2" if is_family_animation else "") + ")", f"${projected_sales_base:,.2f}", f"${projected_sales_gen_pop:,.2f}", "", "", ""),
        ("", "", "", "", "", ""),
    ])

    rows.append(("", "DEMOGRAPHICS (Overall - all theater UIDs)", "", "", "", ""))
    for field in ["GENDER", "AGE", "INCOME", "ETHNICITY", "LOCATION"]:
        if field in demo_overall:
            rows.append((field, "", "", "", "", ""))
            for value, pct in sorted(demo_overall[field].items(), key=lambda x: -x[1]):
                rows.append(("", value, f"{pct}%", "", "", ""))
        rows.append(("", "", "", "", "", ""))

    # Demographics - per theater
    # When the AI auditor returns a resolved demographic plan (gender / age /
    # income / ethnicity), we apply that same plan to EVERY per-theater
    # section so the Theaters tab on the dashboard stays coherent with the
    # Audience Snapshot card on the Summary tab. Without this, the Summary
    # tab would show researched FEMALE-skew while the Theaters tab still
    # showed the raw panel's near-50/50 split. LOCATION stays as panel
    # truth (varies meaningfully by theater chain footprint).
    resolved_plan = validation.get("_resolved_plan") or {}
    rows.append(("", "DEMOGRAPHICS PER THEATER", "", "", "", ""))
    theaters = df_demo_per_theater["THEATER_PLATFORM"].unique()
    for theater in theaters:
        df_theater_demo = df_demo_per_theater[df_demo_per_theater["THEATER_PLATFORM"] == theater]
        demo_theater = compute_demographics(df_theater_demo)
        if resolved_plan:
            apply_demo_plan_to_section(
                demo_theater,
                resolved_plan.get("gender") or {},
                resolved_plan.get("age") or {},
                resolved_plan.get("income") or {},
                resolved_plan.get("ethnicity") or {},
                resolved_plan.get("skew") or "",
            )
        rows.append(("", f"--- {theater} ---", "", "", "", ""))
        for field in ["GENDER", "AGE", "INCOME", "ETHNICITY", "LOCATION"]:
            if field in demo_theater:
                rows.append((field, "", "", "", "", ""))
                for value, pct in sorted(demo_theater[field].items(), key=lambda x: -x[1]):
                    rows.append(("", value, f"{pct}%", "", "", ""))
        rows.append(("", "", "", "", "", ""))

    # ---- AI VALIDATION block (mirrors SVOD Subscriber IQ output) ----
    # The dashboard's parse_ticket_sales_tracker_csv reads these rows; any rows
    # it doesn't explicitly recognize are silently ignored, so this section is
    # backward-compatible. A targeted parser pass surfaces it in the UI.
    rows.append(("", "", "", "", "", ""))
    rows.append(("", "AI VALIDATION", "", "", "", ""))
    rows.append((
        "Validation Status", "",
        "PASS" if validation.get("passed", True) else "FLAGGED",
        "", "", ""
    ))
    if model_used and model_used != "unknown":
        rows.append(("Auditor Model", "", model_used, "", "", ""))
    assessment = validation.get("overall_assessment") or validation.get("note") or ""
    if assessment:
        rows.append(("Assessment", "", assessment, "", "", ""))
    reasoning = (validation.get("reasoning") or "").strip()
    if reasoning:
        rows.append(("Reasoning", "", reasoning, "", "", ""))
    scale_dir = (validation.get("scale_direction") or "").strip().lower()
    scale_factor = validation.get("scale_factor_midpoint")
    if scale_dir in ("up", "down") and isinstance(scale_factor, (int, float)):
        rows.append((
            "Scale Direction", "",
            f"{scale_dir.upper()} (factor {float(scale_factor):.3f})",
            "", "", ""
        ))
    gender_skew = (validation.get("gender_skew") or "").strip().lower()
    if gender_skew in ("male", "female", "balanced"):
        rows.append(("Researched Gender Skew", "", gender_skew.upper(), "", "", ""))

    researched_gross = validation.get("researched_domestic_gross_usd")
    if isinstance(researched_gross, (int, float)) and researched_gross > 0:
        sources = validation.get("research_sources") or []
        src_note = f"(sources: {', '.join(sources)})" if sources else "(Box Office Mojo / The Numbers / trade press)"
        rows.append((
            "Researched US Domestic Gross", "",
            f"${researched_gross:,.0f}", src_note, "", ""
        ))
        band_lo = float(researched_gross) * DIGITAL_SALES_FACTOR_LOW
        band_hi = float(researched_gross) * DIGITAL_SALES_FACTOR_HIGH
        rows.append((
            "Digital Sales Band", "",
            f"${band_lo:,.0f} - ${band_hi:,.0f}",
            f"({DIGITAL_SALES_FACTOR_LOW*100:.0f}-"
            f"{DIGITAL_SALES_FACTOR_HIGH*100:.0f}% of US gross; "
            f"panel measures digital only)",
            "", ""
        ))
        if projected_sales_gen_pop > 0:
            final_pct = (projected_sales_gen_pop / float(researched_gross)) * 100.0
            in_band = band_lo <= projected_sales_gen_pop <= band_hi
            rows.append((
                "Final Sales vs Gross", "",
                f"{final_pct:.1f}% of researched US gross",
                "(in band)" if in_band else "(outside band — check)",
                "", ""
            ))

    for i, flag in enumerate(validation.get("flags", []) or [], start=1):
        rows.append((f"Flag {i}", "", flag, "", "", ""))

    for i, change in enumerate(ai_changes, start=1):
        rows.append((f"Adjustment {i}", "", change, "", "", ""))

    for field, key in (("tickets_note", "Tickets Check"),
                       ("sales_note", "Sales Check"),
                       ("demographics_note", "Demographics Check")):
        note_val = validation.get(field)
        if note_val:
            plausible_key = field.replace("_note", "_plausible")
            tag = "OK" if validation.get(plausible_key, True) else "FLAG"
            rows.append((key, "", f"[{tag}] {note_val}", "", "", ""))

    research_text = (validation.get("research") or "").strip()
    if research_text:
        rows.append(("", "", "", "", "", ""))
        rows.append(("", "AI VALIDATION — RESEARCH SUMMARY", "", "", "", ""))
        for line in research_text.splitlines():
            line = line.strip()
            if line:
                rows.append(("", "", line, "", "", ""))

    df_out = pd.DataFrame(rows, columns=["Category", "Value", "Projection/Percent", "Note", "Col5", "Col6"])

    output_folder = Path(p.get("output_dir", Path.home() / "Desktop" / "attribution"))
    if isinstance(output_folder, str):
        output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%m_%d_%Y_%H_%M")
    safe_movie_name = re.sub(r'[<>:"/\\|?*\']', '', p["movie_name"]).strip()[:50]
    output_path = output_folder / f"Ticket_Sales_{safe_movie_name}_{timestamp}.csv"
    df_out.to_csv(output_path, index=False, encoding="utf-8")
    print(f"✅ Report written to {output_path}\n")


# =============
# === Main  ===
# =============
def main():
    print("\n" + "=" * 60)
    print("     TICKET SALES ATTRIBUTION")
    print("=" * 60)
    print("Movie viewers → theater hits & ticket sales projections")
    print("=" * 60 + "\n")

    params = get_user_input()
    conn = connect_db()
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
