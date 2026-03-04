#!/usr/bin/env python3
"""
Go through all CSV files in S3 bucket dashboard-inputs and set any row whose Value
is a brand input (from the BRAND INPUT row) to 100% Brand Penetration (Row), Category Share,
Original Raw Numbers = sample size, and US Gen Pop Projection accordingly.
If a brand was in BRAND INPUT but does not appear as a Value anywhere, we skip it (no worries).
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

# Metadata/demo columns we never treat as "brand" rows
SKIP_COLUMNS = {
    "INPUT_METADATA", "BRAND INPUT", "SAMPLE SIZE", "AVID FAN", "CASUAL FAN",
    "BRAND CATEGORY", "GENDER", "AGE", "ETHNICITY", "INCOME", "EDUCATION",
    "RELATIONSHIP", "SEXUAL_ORIENTATION", "PARENTAL_STATUS", "LOCATION", "OCCUPATION",
}


def get_brand_input_names_from_df(df):
    """Parse BRAND INPUT row Value into a set of normalized names (uppercase and no-space) for matching."""
    names = set()
    col_col = df.columns[0] if len(df.columns) > 0 else "Column"
    val_col = "Value" if "Value" in df.columns else df.columns[1] if len(df.columns) > 1 else None
    if not val_col:
        return names
    mask = df[col_col].astype(str).str.strip().str.upper() == "BRAND INPUT"
    if not mask.any():
        return names
    raw = df.loc[mask, val_col].iloc[0]
    s = str(raw).strip()
    if not s or s.upper() == "CSV":
        return names
    for b in s.split(","):
        b = b.strip()
        if b:
            u = b.upper()
            names.add(u)
            names.add(u.replace(" ", ""))
    return names


def value_matches_brand(value, brand_names_set):
    """True if value (row Value) matches any brand in brand_names_set."""
    if not value or not brand_names_set:
        return False
    v = str(value).strip().upper()
    vns = v.replace(" ", "")
    return v in brand_names_set or vns in brand_names_set


def get_sample_size_from_df(df):
    """Read sample size from SAMPLE SIZE row (Original Raw Numbers or Category Share / Percentage)."""
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


def process_csv(content: str):
    """
    Apply brand-input 100% enforcement. Returns (new_csv_content, changed).
    """
    df = pd.read_csv(io.StringIO(content), dtype=str, keep_default_na=False)
    if df.empty or len(df.columns) < 2:
        return content, False

    col_col = df.columns[0]
    val_col = "Value" if "Value" in df.columns else df.columns[1]
    brand_names = get_brand_input_names_from_df(df)
    if not brand_names:
        return content, False

    sample_size = get_sample_size_from_df(df)
    if sample_size is None or sample_size <= 0:
        return content, False

    bp_col = "Brand Penetration (Row)" if "Brand Penetration (Row)" in df.columns else None
    cs_col = "Category Share" if "Category Share" in df.columns else ("Percentage" if "Percentage" in df.columns else None)
    raw_col = "Original Raw Numbers" if "Original Raw Numbers" in df.columns else None
    proj_col = "US Gen Pop Projection" if "US Gen Pop Projection" in df.columns else None

    changed = False
    for idx, row in df.iterrows():
        col_val = str(row.get(col_col, "")).strip().upper()
        if col_val in SKIP_COLUMNS:
            continue
        val = row.get(val_col, "")
        if not value_matches_brand(val, brand_names):
            continue
        if bp_col:
            df.at[idx, bp_col] = "100.0"
        if cs_col:
            df.at[idx, cs_col] = "100.0"
        if raw_col:
            df.at[idx, raw_col] = str(sample_size)
        if proj_col:
            proj = int(round((sample_size / 10_000_000.0) * US_POP))
            df.at[idx, proj_col] = str(proj)
        changed = True

    if not changed:
        return content, False
    out = io.StringIO()
    df.to_csv(out, index=False)
    return out.getvalue(), True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Set brand input values to 100% in all S3 CSVs.")
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
