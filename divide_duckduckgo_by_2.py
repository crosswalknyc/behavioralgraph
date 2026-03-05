#!/usr/bin/env python3
"""
Update CSV files in S3 bucket dashboard-inputs (root-level only, non-recursive).
Excludes files with 'gen_pop' in the filename.

For DuckDuckGo rows in the SEARCH ENGINE/AI category:
1. Divide Original Raw Numbers by 2
2. Divide Brand Penetration (Row) by 2
3. Divide US Gen Pop Projection by 2
4. Recalculate Category Share for all rows in SEARCH ENGINE/AI category

Run with AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY set (or default creds).
Usage: PYTHONUNBUFFERED=1 python3 divide_duckduckgo_by_2.py
"""

import os
import sys
import io

try:
    import boto3
    import pandas as pd
except ImportError as e:
    print("Required: pip install boto3 pandas")
    sys.exit(1)

S3_BUCKET = "dashboard-inputs"
S3_REGION = "us-east-2"

TARGET_CATEGORY = "SEARCH ENGINE/AI"
TARGET_VALUE = "DUCKDUCKGO"


def safe_float(x):
    """Parse cell to float."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return 0.0
    try:
        return float(str(x).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def safe_raw(x):
    """Parse Original Raw Numbers cell to int."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return 0
    try:
        return int(float(str(x).replace(",", "")))
    except (ValueError, TypeError):
        return 0


def process_csv(content: str, filename: str) -> str:
    """Process CSV string: divide DuckDuckGo values by 2 in SEARCH ENGINE/AI category and recalc Category Share."""
    df = pd.read_csv(io.StringIO(content), dtype=str, keep_default_na=False)
    if df.empty or len(df.columns) < 2:
        return content

    col_col = df.columns[0]  # Usually "Column"
    val_col = df.columns[1] if len(df.columns) > 1 else None  # Usually "Value"
    
    raw_col = "Original Raw Numbers" if "Original Raw Numbers" in df.columns else None
    bp_col = "Brand Penetration (Row)" if "Brand Penetration (Row)" in df.columns else None
    genpop_col = "US Gen Pop Projection" if "US Gen Pop Projection" in df.columns else None
    pct_col = "Category Share" if "Category Share" in df.columns else ("Percentage" if "Percentage" in df.columns else None)

    if not raw_col or not val_col:
        return content

    col_upper = df[col_col].astype(str).str.strip().str.upper()
    val_upper = df[val_col].astype(str).str.strip().str.upper()

    # Find all rows in SEARCH ENGINE/AI category
    category_mask = col_upper == TARGET_CATEGORY
    if not category_mask.any():
        return content

    category_indices = df.index[category_mask].tolist()
    
    # Find DuckDuckGo rows within the category
    duckduckgo_mask = category_mask & (val_upper == TARGET_VALUE)
    if not duckduckgo_mask.any():
        return content

    duckduckgo_indices = df.index[duckduckgo_mask].tolist()
    modified = False

    for idx in duckduckgo_indices:
        # Divide Original Raw Numbers by 2
        if raw_col:
            raw = safe_raw(df.at[idx, raw_col])
            if raw > 0:
                new_raw = max(1, raw // 2)
                df.at[idx, raw_col] = str(new_raw)
                modified = True

        # Divide Brand Penetration (Row) by 2
        if bp_col:
            bp = safe_float(df.at[idx, bp_col])
            if bp > 0:
                new_bp = bp / 2.0
                df.at[idx, bp_col] = f"{new_bp:.4f}"
                modified = True

        # Divide US Gen Pop Projection by 2
        if genpop_col:
            genpop = safe_raw(df.at[idx, genpop_col])
            if genpop > 0:
                new_genpop = max(1, genpop // 2)
                df.at[idx, genpop_col] = str(new_genpop)
                modified = True

    if not modified:
        return content

    # Recalculate Category Share for all rows in SEARCH ENGINE/AI category
    if pct_col and category_indices:
        raws = [safe_raw(df.at[i, raw_col]) for i in category_indices]
        total = sum(raws)
        if total > 0:
            for i, idx in enumerate(category_indices):
                share = (raws[i] / total) * 100.0
                df.at[idx, pct_col] = f"{share:.4f}"

    out = io.StringIO()
    df.to_csv(out, index=False)
    return out.getvalue()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Divide DuckDuckGo values by 2 in SEARCH ENGINE/AI category.")
    parser.add_argument("--dry-run", action="store_true", help="List and process CSVs but do not upload changes.")
    args = parser.parse_args()
    dry_run = getattr(args, "dry_run", False)

    endpoint = f"https://s3.{S3_REGION}.amazonaws.com"
    s3 = boto3.client(
        "s3",
        region_name=S3_REGION,
        endpoint_url=endpoint,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )

    # Root-level only (non-recursive): use Delimiter="/" with empty prefix
    updated = 0
    skipped_genpop = 0
    errors = []

    paginator = s3.get_paginator("list_objects_v2")
    print("Listing CSVs at root level (excluding gen_pop files)...", flush=True)
    
    for page in paginator.paginate(Bucket=S3_BUCKET, Prefix="", Delimiter="/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            
            # Skip non-CSV files
            if not key.lower().endswith(".csv"):
                continue
            
            # Skip files with gen_pop in the name
            if "gen_pop" in key.lower():
                skipped_genpop += 1
                continue

            try:
                resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
                body = resp["Body"].read().decode("utf-8", errors="replace")
                new_content = process_csv(body, key)
                
                if new_content == body:
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

    print(f"\nDone. Updated {updated} file(s)." + (" (dry-run)" if dry_run else ""), flush=True)
    print(f"Skipped {skipped_genpop} gen_pop file(s).", flush=True)
    if errors:
        print(f"Errors: {len(errors)}", file=sys.stderr)
        for k, err in errors:
            print(f"  {k}: {err}", file=sys.stderr)
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
