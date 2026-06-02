#!/usr/bin/env python3
"""
Go through all CSV files in S3 bucket dashboard-inputs and cap any value in any
brand penetration column that is over 100% to under 90% (89.99%). Recalculates
Original Raw Numbers and US Gen Pop Projection from the capped percentage.
"""

import io
import os
import sys

try:
    import boto3
    import pandas as pd
except ImportError:
    print("Required: pip install boto3 pandas")
    sys.exit(1)

S3_BUCKET = "dashboard-inputs"
S3_REGION = os.environ.get("AWS_REGION", "us-east-2")
US_POP = 329_900_000
CAP_PCT = 89.99  # Under 90%


def get_sample_size_from_df(df):
    """Read sample size from SAMPLE SIZE row."""
    col_col = df.columns[0] if len(df.columns) > 0 else "Column"
    mask = df[col_col].astype(str).str.strip().str.upper() == "SAMPLE SIZE"
    if not mask.any():
        return None
    row = df.loc[mask].iloc[0]
    for c in ["Original Raw Numbers", "Category Share", "Percentage"]:
        if c in df.columns:
            val = row.get(c)
            if pd.notna(val) and str(val).strip():
                try:
                    return int(float(str(val).replace(",", "")))
                except (ValueError, TypeError):
                    pass
    return None


def safe_float(x):
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    try:
        s = str(x).replace(",", "").strip()
        if not s:
            return None
        return float(s)
    except (ValueError, TypeError):
        return None


def process_csv(content):
    """
    Cap any brand penetration column value > 100 to CAP_PCT (89.99%).
    Returns (new_csv_content, changed).
    """
    df = pd.read_csv(io.StringIO(content), dtype=str, keep_default_na=False)
    if df.empty or len(df.columns) < 2:
        return content, False

    # Find any column whose name contains "brand penetration" (case insensitive)
    bp_cols = [c for c in df.columns if "brand penetration" in str(c).lower()]
    if not bp_cols:
        return content, False

    sample_size = get_sample_size_from_df(df)
    raw_col = "Original Raw Numbers" if "Original Raw Numbers" in df.columns else None
    proj_col = "US Gen Pop Projection" if "US Gen Pop Projection" in df.columns else None
    cs_col = "Category Share" if "Category Share" in df.columns else None

    changed = False
    for bp_col in bp_cols:
        for idx in df.index:
            val = safe_float(df.at[idx, bp_col])
            if val is None or val <= 100:
                continue
            # Cap to under 90%
            df.at[idx, bp_col] = f"{CAP_PCT:.2f}"
            if cs_col and cs_col in df.columns:
                df.at[idx, cs_col] = f"{CAP_PCT:.2f}"
            if sample_size is not None and sample_size > 0:
                raw_val = max(0, int(round(CAP_PCT / 100.0 * sample_size)))
                if raw_col:
                    df.at[idx, raw_col] = str(raw_val)
                if proj_col:
                    proj_val = max(0, int(round(CAP_PCT / 100.0 * US_POP)))
                    df.at[idx, proj_col] = str(proj_val)
            changed = True

    if not changed:
        return content, False
    out = io.StringIO()
    df.to_csv(out, index=False)
    return out.getvalue(), True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Cap brand penetration values over 100% to under 90% in S3 CSVs.")
    parser.add_argument("--dry-run", action="store_true", help="Do not upload; only report what would change.")
    args = parser.parse_args()
    dry_run = getattr(args, "dry_run", False)

    s3 = boto3.client(
        "s3",
        region_name=S3_REGION,
        endpoint_url=os.environ.get("AWS_ENDPOINT_URL_S3") or f"https://s3.{S3_REGION}.amazonaws.com",
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )

    print("Listing all CSV files in S3...", flush=True)
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=S3_BUCKET):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if k.lower().endswith(".csv"):
                keys.append(k)

    print(f"Found {len(keys)} CSV file(s).", flush=True)
    updated = 0
    errors = []

    for key in keys:
        try:
            resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
            body = resp["Body"].read().decode("utf-8", errors="replace")
            new_content, changed = process_csv(body)
            if not changed:
                continue
            if dry_run:
                print(f"[dry-run] Would update: {key}")
                updated += 1
                continue
            s3.put_object(
                Bucket=S3_BUCKET,
                Key=key,
                Body=new_content.encode("utf-8"),
                ContentType="text/csv",
            )
            updated += 1
            print(f"Updated: {key}", flush=True)
        except Exception as e:
            errors.append((key, str(e)))
            print(f"Error {key}: {e}", file=sys.stderr)

    print(f"Done. Updated {updated} file(s)." + (" (dry-run)" if dry_run else ""), flush=True)
    if errors:
        for k, err in errors:
            print(f"  {k}: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
