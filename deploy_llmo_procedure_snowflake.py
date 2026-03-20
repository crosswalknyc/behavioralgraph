#!/usr/bin/env python3
"""
Deploy SP_LLMO_DAILY from repo setup_llmo_daily.sql and optionally run it.

Requires: SNOWFLAKE_USER + (SNOWFLAKE_TOKEN or SNOWFLAKE_PASSWORD), same as bg.py.
Optional: SNOWFLAKE_ACCOUNT, SNOWFLAKE_ROLE (default ACCOUNTADMIN).

Usage:
  cd bg-webapp && python3 deploy_llmo_procedure_snowflake.py
  python3 deploy_llmo_procedure_snowflake.py --no-call   # only CREATE PROCEDURE
"""
import argparse
import os
import sys

# Repo root: finished_codes/setup_llmo_daily.sql
_BG = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_BG, ".."))
_SQL_PATH = os.path.join(_REPO_ROOT, "setup_llmo_daily.sql")


def _load_env_files() -> None:
    """Load `.env` from bg-webapp/ then finished_codes/ (gitignored; never commit secrets)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(os.path.join(_BG, ".env"))
    load_dotenv(os.path.join(_REPO_ROOT, ".env"))


def extract_procedure_ddl(sql_text: str) -> str:
    marker = "CREATE OR REPLACE PROCEDURE PROCESSEDCLICKSTREAM.PUBLIC.SP_LLMO_DAILY()"
    start = sql_text.find(marker)
    if start < 0:
        raise ValueError("Procedure SP_LLMO_DAILY not found in SQL file")
    tail_marker = "\n$$;\n\n\n-- Single daily task"
    end = sql_text.find(tail_marker, start)
    if end < 0:
        # fallback: end at first $$; after AS $$
        sub = sql_text[start:]
        as_pos = sub.find("AS\n$$")
        if as_pos < 0:
            raise ValueError("Could not find procedure body delimiter")
        body_start = as_pos + len("AS\n$$")
        close = sub.find("\n$$;", body_start)
        if close < 0:
            raise ValueError("Could not find closing $$;")
        return sub[: close + len("\n$$;")].strip()
    return sql_text[start:end].strip() + "\n$$;"


def main():
    _load_env_files()
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-call", action="store_true", help="Only deploy procedure, do not CALL")
    parser.add_argument("--sql-path", default=_SQL_PATH, help="Path to setup_llmo_daily.sql")
    args = parser.parse_args()

    if not os.environ.get("SNOWFLAKE_USER"):
        print("ERROR: SNOWFLAKE_USER is not set. Set SNOWFLAKE_USER and SNOWFLAKE_TOKEN or SNOWFLAKE_PASSWORD.", file=sys.stderr)
        sys.exit(2)

    if not os.path.isfile(args.sql_path):
        print(f"ERROR: SQL file not found: {args.sql_path}", file=sys.stderr)
        sys.exit(2)

    sql_text = open(args.sql_path, encoding="utf-8").read()
    proc_ddl = extract_procedure_ddl(sql_text)

    os.chdir(_BG)
    import bg  # noqa: E402

    print("Connecting to Snowflake...")
    conn = bg.connect_snowflake()
    cur = conn.cursor()
    try:
        cur.execute("USE ROLE " + os.environ.get("SNOWFLAKE_ROLE", "ACCOUNTADMIN"))
        cur.execute("USE WAREHOUSE BEHAVIORGRAPH6X")
        cur.execute("USE DATABASE PROCESSEDCLICKSTREAM")
        cur.execute("USE SCHEMA PUBLIC")

        print("Deploying PROCESSEDCLICKSTREAM.PUBLIC.SP_LLMO_DAILY ...")
        cur.execute(proc_ddl)
        print("Procedure created successfully.")

        if not args.no_call:
            print("Calling SP_LLMO_DAILY() (may take several minutes)...")
            cur.execute("CALL PROCESSEDCLICKSTREAM.PUBLIC.SP_LLMO_DAILY()")
            row = cur.fetchone()
            if row:
                print("Result:", row[0])
            else:
                print("CALL completed.")
    finally:
        conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
