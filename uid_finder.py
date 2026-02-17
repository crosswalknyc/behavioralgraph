"""
UID Finder - Same search terms and dates as Ticket_Sales_Attribution,
but output is just the UID values (movie viewers who visited theater platforms) to uid_list.csv.
"""
import pandas as pd
import snowflake.connector
from datetime import datetime
from pathlib import Path
import sys

# Reuse constants and helpers from Ticket_Sales_Attribution
from Ticket_Sales_Attribution import (
    SNOWFLAKE_USER,
    SNOWFLAKE_PASSWORD,
    SNOWFLAKE_ACCOUNT,
    SNOWFLAKE_WAREHOUSE,
    SNOWFLAKE_DATABASE,
    SNOWFLAKE_SCHEMA,
    THEATER_PLATFORMS,
    GENRE_OPTIONS,
    connect_snowflake,
    generate_search_term_variations,
    format_search_term,
    make_url_and_common_name_filter,
    make_common_name_filter,
)


def get_user_input():
    """Collect date range, movie title, and genre (same as Ticket_Sales_Attribution)."""
    print("\n" + "=" * 60)
    print("     UID FINDER (Ticket Sales Attribution UIDs)")
    print("=" * 60)
    print("Same inputs as Ticket Sales Attribution → output: uid_list.csv")
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
    print("SUMMARY:")
    print("=" * 60)
    print(f"Movie: '{movie_name}'")
    print(f"Genre: '{genre}'")
    print(f"Date Range: {start_date.date()} to {end_date.date()}")
    print("=" * 60 + "\n")

    return {
        "movie_name": movie_name,
        "genre": genre,
        "start_date": start_date,
        "end_date": end_date,
    }


def fetch_uids(conn, p):
    """
    Run same Step 1 + Step 2 as Ticket_Sales_Attribution and return distinct UIDs
    (movie viewers who visited theater platforms).
    """
    cur = conn.cursor()
    movie_filter = make_url_and_common_name_filter([p["movie_name"]], auto_format=True)
    theater_filter = make_common_name_filter(THEATER_PLATFORMS)

    print("Step 1: Finding movie viewers...")
    cur.execute(f"""
        CREATE OR REPLACE TEMP TABLE TEMP_MOVIE_VIEWERS AS
        SELECT DISTINCT UID
        FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL
        WHERE DELIVERED BETWEEN '{p['start_date'].date()}' AND '{p['end_date'].date()}'
          AND ({movie_filter})
    """)
    result = cur.execute("SELECT COUNT(*) FROM TEMP_MOVIE_VIEWERS").fetchone()
    total_movie = int(result[0]) if result and result[0] else 0
    print(f"   Found {total_movie:,} unique movie viewers.")

    print("Step 2: Finding theater visits for those movie viewers...")
    cur.execute(f"""
        CREATE OR REPLACE TEMP TABLE TEMP_THEATER_VISITS_MOVIE_VIEWERS AS
        SELECT tv.UID, tv.COMMON_NAME AS THEATER_PLATFORM
        FROM PROCESSEDCLICKSTREAM.PUBLIC.CLICKSTREAM_FINAL tv
        WHERE tv.DELIVERED BETWEEN '{p['start_date'].date()}' AND '{p['end_date'].date()}'
          AND ({theater_filter})
          AND tv.UID IN (SELECT UID FROM TEMP_MOVIE_VIEWERS)
    """)
    # Distinct UIDs used in attribution (same set as TEMP_THEATER_UIDS)
    df_uids = pd.read_sql("SELECT DISTINCT UID FROM TEMP_THEATER_VISITS_MOVIE_VIEWERS ORDER BY UID", conn)
    print(f"   Found {len(df_uids):,} unique UIDs (movie viewers who visited theater platforms).")
    return df_uids


def main():
    params = get_user_input()
    conn = connect_snowflake()
    try:
        df_uids = fetch_uids(conn, params)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    out_path = Path("uid_list.csv")
    df_uids.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\nUIDs written to {out_path.resolve()} ({len(df_uids):,} rows).\n")


if __name__ == "__main__":
    main()
