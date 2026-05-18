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
        "You are a senior box-office research analyst auditing a US-only ticket sales "
        "projection produced by a 10M-person behavioral panel. Your job has THREE "
        "stages, executed in order:\n\n"
        "(1) RESEARCH. Use web_search to find REAL US domestic box office for the "
        "film. Prioritize Box Office Mojo and The Numbers for the dollar figures. "
        "For very recent releases (still in theaters), Variety, Deadline, and THR "
        "weekend recaps are acceptable. Always cite the source for each figure.\n\n"
        "(2) AUDIENCE RESEARCH. Use web_search to find primary audience age and "
        "gender skew. Look at Variety audience reports, Nielsen, Samba TV, "
        "EntTelligence, ComScore PostTrak, CinemaScore exit polls, and major trade "
        "press. State whether the title is male-skew, female-skew, or balanced and "
        "the approximate percentages.\n\n"
        "(3) AUDIT. Compare our panel projection to the researched gross.\n\n"
        "ADJUSTMENT RULES — read carefully:\n"
        "- Scaling is BIDIRECTIONAL. If our projected sales are MUCH LOWER than "
        "  the researched US gross, you MUST scale UP. If MUCH HIGHER, you MUST "
        "  scale DOWN. The panel is just a sample — under-projection is just as "
        "  much of a credibility failure as over-projection.\n"
        "- Target: projected_sales_gen_pop should land at approximately 1.0x the "
        "  researched US domestic gross (acceptable range 0.85x-1.10x).\n"
        "- For demographics: the panel reflects who has time to browse on a "
        "  research panel, NOT who buys movie tickets. If researched audience "
        "  skew clearly differs from the panel-derived percentages, OVERRIDE the "
        "  panel. Don't be timid. A film known to be female-skew must NEVER come "
        "  out male-dominant in the final output, and vice versa.\n\n"
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
        f"- Projected Sales (US Gen Pop): ${projected_sales_gen_pop:,.0f}\n\n"
        f"CURRENT OVERALL DEMOGRAPHICS (from panel):\n{demo_overall}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Run web_search queries to find the REAL US domestic gross for this title "
        f"   (gross to date, opening weekend, wide release date, is-still-in-theaters).\n"
        f"2. Run web_search queries to find primary audience age + gender skew.\n"
        f"3. Audit our projection. Compute: real_gross / our_projected_sales = scale factor.\n"
        f"4. Return the JSON shown below — every field is required.\n\n"
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
        f'  "scale_factor_midpoint": <decimal; e.g. 5.0 means our number is 5x too low, '
        f'0.25 means we are 4x too high>,\n'
        f'  "suggested_projected_sales_range_genpop": [<low_usd>, <high_usd>],\n'
        f'  "suggested_total_tickets_range_genpop": [<low>, <high>],\n'
        f'  "gender_skew": "male" | "female" | "balanced",\n'
        f'  "age": {{"17 AND UNDER": <pct>, "18-24": <pct>, "25-34": <pct>, "35-44": <pct>, "45-54": <pct>, "55-64": <pct>, "65 OR OLDER": <pct>}},\n'
        f'  "gender": {{"MALE": <pct>, "FEMALE": <pct>, "NON-BINARY": <pct>, "TRANS MALE": <pct>, "TRANS FEMALE": <pct>}},\n'
        f'  "research_summary": "5-10 sentences of researched facts with citations",\n'
        f'  "reasoning": "2-3 sentences explaining the adjustment",\n'
        f'  "overall_assessment": "one sentence"\n'
        f"}}\n"
        f"Set passed=false whenever ANY adjustment is needed. The whole point of this "
        f"audit is to anchor the dashboard to reality — don't be timid about flagging."
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
        f"=== PHASE A: VALIDATE TICKETS & SALES (BIDIRECTIONAL) ===\n"
        f"Compare our 'US Gen Pop projected' sales to the REAL US domestic gross above.\n"
        f"- TARGET: projected_sales_gen_pop should land at ~1.0x the researched US gross\n"
        f"  (acceptable range 0.85x-1.10x).\n"
        f"- If our number is MUCH HIGHER than the gross, suggest a DOWN-scale.\n"
        f"- If our number is MUCH LOWER than the gross, suggest an UP-scale. Under-\n"
        f"  projection is just as much of a credibility failure as over-projection.\n"
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


def apply_ai_ticket_adjustments(validation, platform_totals, total_tickets,
                                total_tickets_gen_pop, projected_sales_base,
                                projected_sales_gen_pop, demo_overall):
    """Apply BIDIRECTIONAL corrections from ai_validate_ticket_metrics.

    Anchors projected sales to the researched US domestic gross in EITHER
    direction:
      * panel over-projects (e.g. $1.2B vs BOM $310M) → scale DOWN
      * panel under-projects (e.g. $34M vs BOM $170M)  → scale UP
    Per-platform hits, total tickets, both gen-pop projections, and both
    dollar figures all scale by the same factor so the rows still add up.

    Demographics override is now MANDATORY (no longer gated on the model's
    own ``demographics_plausible`` self-assessment). When the AI returns an
    age/gender plan, we apply it. We then run _enforce_gender_skew so a
    known female-skew title can never come out male-dominant — even if the
    LLM regressed and returned percentages that contradicted its own
    ``gender_skew`` field. Per-theater demographics stay untouched since
    they're tied to actual ClickHouse UID joins.
    """
    changes = []
    if validation.get("passed", True):
        return (platform_totals, total_tickets, total_tickets_gen_pop,
                projected_sales_base, projected_sales_gen_pop, demo_overall, changes)

    # ---- Bidirectional sales/tickets anchoring ----
    suggested_sales = validation.get("suggested_projected_sales_range_genpop") or []
    target_sales = None
    if isinstance(suggested_sales, list) and len(suggested_sales) == 2:
        try:
            lo = float(suggested_sales[0]) if suggested_sales[0] is not None else None
            hi = float(suggested_sales[1]) if suggested_sales[1] is not None else None
            if lo is not None and hi is not None and lo >= 0 and hi >= lo:
                target_sales = (lo + hi) / 2.0
        except (ValueError, TypeError):
            target_sales = None

    # Secondary fallback: if no explicit suggested range, use the researched
    # gross directly as the anchor (assume 1.0x of real US domestic gross).
    if target_sales is None or target_sales <= 0:
        researched_gross = validation.get("researched_domestic_gross_usd")
        if isinstance(researched_gross, (int, float)) and researched_gross > 0:
            target_sales = float(researched_gross)

    if target_sales is not None and projected_sales_gen_pop > 0 and target_sales > 0:
        raw_factor = target_sales / projected_sales_gen_pop
        # Bounds: 0.05x down to 25x up. The upward ceiling is generous because
        # panel under-projection by 5-10x is plausible for mainstream titles
        # whose audience under-indexes on online research panels.
        factor = max(0.05, min(25.0, raw_factor))
        # Only adjust if meaningfully different (more than ±5% off target).
        if abs(factor - 1.0) > 0.05:
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
            researched_gross = validation.get("researched_domestic_gross_usd")
            gross_note = ""
            if isinstance(researched_gross, (int, float)) and researched_gross > 0:
                gross_note = f" (anchored to researched US domestic gross ~${researched_gross:,.0f})"
            changes.append(
                f"{arrow} Scaled per-platform hits, total tickets, and projected sales by "
                f"{factor:.3f}{gross_note}: projected sales ${old_sales:,.0f} -> "
                f"${projected_sales_gen_pop:,.0f}; total tickets {old_total_tickets:,} -> "
                f"{total_tickets:,}."
            )

    # ---- Demographics override (now MANDATORY when AI returns a plan) ----
    age_plan = validation.get("age") or {}
    gender_plan = validation.get("gender") or {}
    researched_skew = (validation.get("gender_skew") or "").strip().lower()

    if "AGE" in demo_overall and demo_overall["AGE"] and age_plan:
        new_age = _normalize_pct_plan(age_plan, list(demo_overall["AGE"].keys()))
        if new_age:
            demo_overall["AGE"] = new_age
            changes.append("Aligned overall AGE demographics to researched audience profile.")

    if "GENDER" in demo_overall and demo_overall["GENDER"] and gender_plan:
        # Enforce researched skew direction BEFORE normalizing — this is the
        # safety net that prevents Devil Wears Prada from outputting MALE
        # dominant when the title is universally known to be female-skew.
        gender_plan = _enforce_gender_skew(gender_plan, researched_skew)
        new_gender = _normalize_pct_plan(gender_plan, list(demo_overall["GENDER"].keys()))
        if new_gender:
            demo_overall["GENDER"] = new_gender
            skew_label = researched_skew or "balanced"
            changes.append(
                f"Aligned overall GENDER demographics to researched {skew_label}-skew audience."
            )

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
    # Note: per-theater demographics are NOT touched by the AI auditor — they
    # are tied to the underlying UID set in ClickHouse and reflect the panel
    # truth. Only the OVERALL section is aligned to the researched audience.
    rows.append(("", "DEMOGRAPHICS PER THEATER", "", "", "", ""))
    theaters = df_demo_per_theater["THEATER_PLATFORM"].unique()
    for theater in theaters:
        df_theater_demo = df_demo_per_theater[df_demo_per_theater["THEATER_PLATFORM"] == theater]
        demo_theater = compute_demographics(df_theater_demo)
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
