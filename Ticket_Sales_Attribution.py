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


def ai_align_ticket_sales_totals_and_demographics(movie_name, genre, total_tickets, total_tickets_gen_pop, projected_sales_base, projected_sales_gen_pop, demo_overall):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return total_tickets, total_tickets_gen_pop, projected_sales_base, projected_sales_gen_pop, demo_overall, []
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
    except Exception:
        return total_tickets, total_tickets_gen_pop, projected_sales_base, projected_sales_gen_pop, demo_overall, []

    research = ""
    try:
        research_prompt = (
            f'Research US-only ticket sales and audience demographics for the film "{movie_name}". '
            f'Provide domestic US box office range and likely AGE/GENDER audience skew from credible sources.'
        )
        rr = client.chat.completions.create(
            model="gpt-4o-search-preview",
            messages=[{"role": "user", "content": research_prompt}],
            web_search_options={"search_context_size": "medium"},
        )
        research = (rr.choices[0].message.content or "").strip() if rr.choices else ""
    except Exception:
        research = ""

    default_plan = _default_ticket_demo_plan(genre)
    if not research:
        age_labels = list((demo_overall.get("AGE") or {}).keys())
        gender_labels = list((demo_overall.get("GENDER") or {}).keys())
        if age_labels:
            demo_overall["AGE"] = _normalize_pct_plan(default_plan.get("age", {}), age_labels)
        if gender_labels:
            demo_overall["GENDER"] = _normalize_pct_plan(default_plan.get("gender", {}), gender_labels)
        return total_tickets, total_tickets_gen_pop, projected_sales_base, projected_sales_gen_pop, demo_overall, ["Applied fallback demographic plan (no web research)."]

    prompt = (
        f'You are validating Ticket Sales Tracker output for US only.\n\n'
        f'MOVIE: {movie_name}\nGENRE: {genre}\n'
        f'OUR TOTAL TICKETS (US projected): {float(total_tickets_gen_pop):.2f}\n'
        f'OUR PROJECTED TICKET SALES (US projected): {float(projected_sales_gen_pop):.2f}\n'
        f'CURRENT OVERALL DEMOGRAPHICS: {demo_overall}\n\n'
        f'RESEARCH:\n{research}\n\n'
        f'Return ONLY JSON:\n'
        f'{{\n'
        f'  "sales_adjustment_factor": <0.05-1.0, use <1 only if inflated; never >1>,\n'
        f'  "gender": {{"MALE": <pct>, "FEMALE": <pct>, "NON-BINARY": <pct>, "TRANS MALE": <pct>, "TRANS FEMALE": <pct>}},\n'
        f'  "age": {{"17 AND UNDER": <pct>, "18-24": <pct>, "25-34": <pct>, "35-44": <pct>, "45-54": <pct>, "55-64": <pct>, "65 OR OLDER": <pct>}},\n'
        f'  "reasoning": "brief"\n'
        f'}}'
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        parsed = _extract_json_object(resp.choices[0].message.content if resp.choices else "")
    except Exception:
        parsed = None

    changes = []
    if not parsed:
        return total_tickets, total_tickets_gen_pop, projected_sales_base, projected_sales_gen_pop, demo_overall, changes

    factor = 1.0
    try:
        factor = float(parsed.get("sales_adjustment_factor", 1.0))
    except (ValueError, TypeError):
        factor = 1.0
    factor = max(0.05, min(1.0, factor))
    if factor < 0.999:
        old_sales = projected_sales_gen_pop
        total_tickets = max(0, int(round(total_tickets * factor)))
        total_tickets_gen_pop = max(0.0, total_tickets_gen_pop * factor)
        projected_sales_base = max(0.0, projected_sales_base * factor)
        projected_sales_gen_pop = max(0.0, projected_sales_gen_pop * factor)
        changes.append(f"Reduced projected US ticket sales by factor {factor:.3f} ({old_sales:,.2f} -> {projected_sales_gen_pop:,.2f}).")

    if "AGE" in demo_overall and demo_overall["AGE"]:
        demo_overall["AGE"] = _normalize_pct_plan(parsed.get("age", default_plan.get("age", {})), list(demo_overall["AGE"].keys()))
        changes.append("Aligned AGE demographics to title audience profile.")
    if "GENDER" in demo_overall and demo_overall["GENDER"]:
        demo_overall["GENDER"] = _normalize_pct_plan(parsed.get("gender", default_plan.get("gender", {})), list(demo_overall["GENDER"].keys()))
        changes.append("Aligned GENDER demographics to title audience profile.")

    reason = str(parsed.get("reasoning") or "").strip()
    if reason:
        changes.append(f"AI rationale: {reason}")
    return total_tickets, total_tickets_gen_pop, projected_sales_base, projected_sales_gen_pop, demo_overall, changes


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
    canonical_upper = {p.upper().strip(): p for p in THEATER_PLATFORMS}
    platform_totals = {p: 0 for p in THEATER_PLATFORMS}
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

    # Demographics - overall
    demo_overall = compute_demographics(df_demo_overall)
    total_tickets, total_tickets_gen_pop, projected_sales_base, projected_sales_gen_pop, demo_overall, ai_changes = ai_align_ticket_sales_totals_and_demographics(
        p["movie_name"],
        p.get("genre", ""),
        total_tickets,
        total_tickets_gen_pop,
        projected_sales_base,
        projected_sales_gen_pop,
        demo_overall
    )
    if ai_changes:
        print("🤖 Applied AI ticket-sales alignment:")
        for c in ai_changes:
            print(f"   • {c}")
    rows.append(("", "DEMOGRAPHICS (Overall - all theater UIDs)", "", "", "", ""))
    for field in ["GENDER", "AGE", "INCOME", "ETHNICITY", "LOCATION"]:
        if field in demo_overall:
            rows.append((field, "", "", "", "", ""))
            for value, pct in sorted(demo_overall[field].items(), key=lambda x: -x[1]):
                rows.append(("", value, f"{pct}%", "", "", ""))
        rows.append(("", "", "", "", "", ""))

    # Demographics - per theater
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
