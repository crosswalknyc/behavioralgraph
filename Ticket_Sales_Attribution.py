"""
Ticket Sales Attribution - Movie viewers → theater platform hits with ticket sales projections.
Input: date range, movie title, genre. Output: TOTAL HITS (MOVIE VIEWERS) → THEATER BY PLATFORM,
ticket sales projections, and demographics per theater and overall.
"""
import pandas as pd
import snowflake.connector
from datetime import datetime
from pathlib import Path
import sys
import re


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


# =========================
# === Snowflake creds ====
# =========================
SNOWFLAKE_USER = "hotdogsandcheezeits"
SNOWFLAKE_PASSWORD = "S3nshine2282!"
SNOWFLAKE_ACCOUNT = "qsodrkt-hgb46445"
SNOWFLAKE_WAREHOUSE = "TICKETS_SALES_WH_6XL"
SNOWFLAKE_DATABASE = "PROCESSEDCLICKSTREAM"
SNOWFLAKE_SCHEMA = "PUBLIC"


def connect_snowflake():
    import os
    user = os.environ.get("SNOWFLAKE_USER") or SNOWFLAKE_USER
    password = os.environ.get("SNOWFLAKE_PASSWORD") or SNOWFLAKE_PASSWORD
    account = os.environ.get("SNOWFLAKE_ACCOUNT") or SNOWFLAKE_ACCOUNT
    warehouse = os.environ.get("TICKET_SALES_TRACKER_WAREHOUSE") or os.environ.get("SNOWFLAKE_WAREHOUSE") or SNOWFLAKE_WAREHOUSE
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
        insecure_mode=True,
        connection_timeout=90,
        network_timeout=3600,
    )
    print("Connected to Snowflake.")
    return conn


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
    cur.execute(f"""
        CREATE OR REPLACE TEMP TABLE TEMP_MOVIE_VIEWERS AS
        SELECT DISTINCT UID
        FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
        WHERE DELIVERED BETWEEN '{p['start_date'].date()}' AND '{p['end_date'].date()}'
          AND ({movie_filter})
    """)
    result = cur.execute("SELECT COUNT(*) FROM TEMP_MOVIE_VIEWERS").fetchone()
    total_movie_viewers = int(result[0]) if result and result[0] else 0
    print(f"   ✅ Found {total_movie_viewers:,} unique movie viewers\n")

    # Step 2: Theater visits for movie viewers
    print("🎬 Step 2: Finding theater platform visits for movie viewers...")
    cur.execute(f"""
        CREATE OR REPLACE TEMP TABLE TEMP_THEATER_VISITS_MOVIE_VIEWERS AS
        SELECT tv.UID, tv.COMMON_NAME AS THEATER_PLATFORM
        FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL tv
        WHERE tv.DELIVERED BETWEEN '{p['start_date'].date()}' AND '{p['end_date'].date()}'
          AND ({theater_filter})
          AND tv.UID IN (SELECT UID FROM TEMP_MOVIE_VIEWERS)
    """)
    result = cur.execute("SELECT COUNT(DISTINCT UID) FROM TEMP_THEATER_VISITS_MOVIE_VIEWERS").fetchone()
    theater_viewers_count = int(result[0]) if result and result[0] else 0
    print(f"   ✅ Found {theater_viewers_count:,} unique theater visitors among movie viewers\n")

    # Step 3: Theater by platform (TOTAL HITS)
    print("📊 Step 3: Theater by platform breakdown...")
    theater_by_platform_query = """
        SELECT
            MAX(THEATER_PLATFORM) AS THEATER_PLATFORM,
            COUNT(DISTINCT UID) AS HITS
        FROM TEMP_THEATER_VISITS_MOVIE_VIEWERS
        GROUP BY UPPER(TRIM(THEATER_PLATFORM))
        ORDER BY HITS DESC
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
    cur.execute("""
        CREATE OR REPLACE TEMP TABLE TEMP_THEATER_UIDS AS
        SELECT DISTINCT UID, THEATER_PLATFORM
        FROM TEMP_THEATER_VISITS_MOVIE_VIEWERS
    """)
    cur.execute("""
        CREATE OR REPLACE TEMP TABLE TEMP_DEMOS AS
        SELECT d.UID, d.GENDER, d.AGE, d.ETHNICITY, d.INCOME, d.DMA, d.DMA_PROVINCE, d.DMA_COUNTRY
        FROM PROCESSEDUSERFILES.PUBLIC.USER_DATA_SANITIZED d
        INNER JOIN TEMP_THEATER_UIDS u ON d.UID = u.UID
    """)
    # Join with theater platform for per-theater demographics
    cur.execute("""
        CREATE OR REPLACE TEMP TABLE TEMP_DEMOS_WITH_THEATER AS
        SELECT td.*, tu.THEATER_PLATFORM
        FROM TEMP_DEMOS td
        INNER JOIN TEMP_THEATER_UIDS tu ON td.UID = tu.UID
    """)

    # Get demographics as dataframe (overall and per theater) - same structure as bg.py
    demo_query_overall = """
        SELECT UID, GENDER, AGE, ETHNICITY, INCOME,
               CASE WHEN DMA_PROVINCE IS NOT NULL AND TRIM(DMA_PROVINCE) != ''
                    THEN CONCAT(DMA, ' ', DMA_PROVINCE) ELSE DMA END AS LOCATION
        FROM TEMP_DEMOS
    """
    demo_query_per_theater = """
        SELECT UID, THEATER_PLATFORM, GENDER, AGE, ETHNICITY, INCOME,
               CASE WHEN DMA_PROVINCE IS NOT NULL AND TRIM(DMA_PROVINCE) != ''
                    THEN CONCAT(DMA, ' ', DMA_PROVINCE) ELSE DMA END AS LOCATION
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
    total_tickets_raw = sum(platform_totals.values())
    total_tickets_gen_pop_raw = gen_pop_projection(total_tickets_raw)
    NON_SALES_BOOST = 7.5
    total_tickets = int(round(total_tickets_raw * NON_SALES_BOOST))
    total_tickets_gen_pop = total_tickets_gen_pop_raw * NON_SALES_BOOST

    # 15x factor applied, then 75% of that as final projected ticket sales (uses raw counts)
    TICKET_PRICE = 15.0
    PROJECTION_FACTOR = 15
    FINAL_PCT = 0.75
    after_factor_base = total_tickets_raw * TICKET_PRICE * PROJECTION_FACTOR
    after_factor_gen_pop = total_tickets_gen_pop_raw * TICKET_PRICE * PROJECTION_FACTOR
    projected_sales_base = after_factor_base * FINAL_PCT
    projected_sales_gen_pop = after_factor_gen_pop * FINAL_PCT

    rows = [
        ("", "TICKET SALES ATTRIBUTION RESULTS", "", "", "", ""),
        ("", "", "", "", "", ""),
        ("Movie", "", p["movie_name"], "", "", ""),
        ("Genre", "", p.get("genre", ""), "(15x factor, then 75% as final projection)", "", ""),
        ("Date Range", "", f"{p['start_date'].date()} to {p['end_date'].date()}", "", "", ""),
        ("", "", "", "", "", ""),
        ("", "TOTAL HITS (MOVIE VIEWERS) → THEATER BY PLATFORM", "", "", "", ""),
        ("Platform", "Hits", "US Gen Pop Projection", "", "", ""),
    ]

    for platform in THEATER_PLATFORMS:
        hits_raw = platform_totals[platform]
        hits = int(round(hits_raw * NON_SALES_BOOST))
        genpop = format_gen_pop_full(gen_pop_projection(hits_raw) * NON_SALES_BOOST)
        rows.append((platform, hits, genpop, "", "", ""))

    rows.extend([
        ("", "", "", "", "", ""),
        ("Total Tickets Sold (sum of theater hits)", total_tickets, format_gen_pop_full(total_tickets_gen_pop), "", "", ""),
        ("Projected Ticket Sales (Total × $15 × 15 × 75%)", f"${projected_sales_base:,.2f}", f"${projected_sales_gen_pop:,.2f}", "", "", ""),
        ("", "", "", "", "", ""),
    ])

    # Demographics - overall
    demo_overall = compute_demographics(df_demo_overall)
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
