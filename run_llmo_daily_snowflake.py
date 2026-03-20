#!/usr/bin/env python3
"""
Run LLMO daily Snowflake procedure (loads yesterday US/Pacific into LLMO, rebuilds summary, exports to S3).

Requires Snowflake credentials the same way as bg.py (env / local config).

Usage (from bg-webapp directory):
  python3 run_llmo_daily_snowflake.py
"""
import os
import sys

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    try:
        import bg
    except ImportError as e:
        print("Could not import bg:", e)
        sys.exit(1)
    print("Connecting to Snowflake...")
    conn = bg.connect_snowflake()
    cur = conn.cursor()
    cur.execute("USE WAREHOUSE BEHAVIORGRAPH6X")
    cur.execute("USE DATABASE PROCESSEDCLICKSTREAM")
    cur.execute("USE SCHEMA PUBLIC")
    print("Calling PROCESSEDCLICKSTREAM.PUBLIC.SP_LLMO_DAILY() ...")
    cur.execute("CALL PROCESSEDCLICKSTREAM.PUBLIC.SP_LLMO_DAILY()")
    row = cur.fetchone()
    if row:
        print("Result:", row[0])
    else:
        print("Call completed (no return row).")
    conn.close()
    print("Done.")

if __name__ == "__main__":
    main()
