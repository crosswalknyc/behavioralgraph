#!/usr/bin/env python3
"""
Go through root-level CSV files in S3 bucket dashboard-inputs and remove duplicate
rows that are both at 100% (Brand Penetration) in the same category. When duplicates
exist, keep the row whose Value has no punctuation/special characters (e.g. keep
MCDONALDS over MCDONALD'S, PAPA JOHNS over PAPA-JOHNS). Only dedupe when BOTH (all)
duplicates in that category have 100%.
"""

import io
import os
import re
import sys

try:
    import boto3
    import pandas as pd
except ImportError:
    print("Required: pip install boto3 pandas")
    sys.exit(1)

S3_BUCKET = "dashboard-inputs"
S3_REGION = os.environ.get("AWS_REGION", "us-east-2")

SKIP_COLUMNS = {
    "INPUT_METADATA", "BRAND INPUT", "SAMPLE SIZE", "AVID FAN", "CASUAL FAN",
    "BRAND CATEGORY", "GENDER", "AGE", "ETHNICITY", "INCOME", "EDUCATION",
    "RELATIONSHIP", "SEXUAL_ORIENTATION", "PARENTAL_STATUS", "LOCATION", "OCCUPATION",
}


def normalize_value(value):
    """Normalize for duplicate detection: lowercase, remove all non-alphanumeric."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def punctuation_count(value):
    """Count characters that are not letters, numbers, or spaces (e.g. apostrophe, hyphen)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0
    s = str(value)
    return sum(1 for c in s if not c.isalnum() and not c.isspace())


def is_100_percent(val):
    """True if value represents 100% (100, 100.0, 100.00, etc.)."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    try:
        return float(str(val).replace(",", "").strip()) >= 99.99
    except (ValueError, TypeError):
        return False


def process_csv(content: str):
    """
    Remove duplicate 100% rows per category: keep the Value with no punctuation.
    Returns (new_csv_content, changed).
    """
    df = pd.read_csv(io.StringIO(content), dtype=str, keep_default_na=False)
    if df.empty or len(df.columns) < 2:
        return content, False

    col_col = df.columns[0]
    val_col = "Value" if "Value" in df.columns else df.columns[1]
    bp_col = "Brand Penetration (Row)" if "Brand Penetration (Row)" in df.columns else None
    if not bp_col:
        return content, False

    # Rows we will drop (index)
    to_drop = set()

    # Group by (Column, normalized Value)
    groups = {}
    for idx, row in df.iterrows():
        col_val = str(row.get(col_col, "")).strip().upper()
        if col_val in SKIP_COLUMNS:
            continue
        val = row.get(val_col, "")
        norm = normalize_value(val)
        if not norm:
            continue
        key = (col_val, norm)
        if key not in groups:
            groups[key] = []
        groups[key].append((idx, val, row.get(bp_col)))

    for (col, norm), rows in groups.items():
        if len(rows) <= 1:
            continue
        # All must be 100% to dedupe
        if not all(is_100_percent(r[2]) for r in rows):
            continue
        # Keep the row with minimum punctuation count; drop the rest
        rows_with_punct = [(punctuation_count(r[1]), r[0]) for r in rows]
        rows_with_punct.sort(key=lambda x: (x[0], x[1]))  # min punctuation, then first index
        keep_idx = rows_with_punct[0][1]
        for _, idx in rows_with_punct[1:]:
            to_drop.add(idx)

    if not to_drop:
        return content, False

    df_new = df.drop(index=list(to_drop)).reset_index(drop=True)
    out = io.StringIO()
    df_new.to_csv(out, index=False)
    return out.getvalue(), True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Dedup 100% rows in S3 CSVs, keep no-punctuation Value.")
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

    print("Listing root-level CSV files in S3...", flush=True)
    paginator = s3.get_paginator("list_objects_v2")
    keys = []
    for page in paginator.paginate(Bucket=S3_BUCKET, Delimiter="/"):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if k.lower().endswith(".csv"):
                keys.append(k)

    print(f"Found {len(keys)} root-level CSV file(s).", flush=True)
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
