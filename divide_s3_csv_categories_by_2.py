#!/usr/bin/env python3
"""
Update all CSV files in S3 bucket dashboard-inputs (root and historic/)
Use: PYTHONUNBUFFERED=1 python3 divide_s3_csv_categories_by_2.py  (for live progress) so that
specified behavioral categories have values divided by 2, matching the pipeline's
new divide-by-2 logic.

For each matching row we:
1. Divide Original Raw Numbers by 2
2. Recalculate percent of total sample (sample from D4 = SAMPLE SIZE row, 4th column) -> Brand Penetration (Row) or equivalent
3. Recalculate Category Share = (value's raw / sum of raws in that category) * 100
4. Recalculate US Gen Pop Projection = (Original Raw Number / 10_000_000) * 329_900_000
5. Save back to the same S3 key (overwrite).

Categories updated: APP/PLATFORM USAGE, APPS/PLATFORMS, BANKING, TRAVEL,
BROADCAST/CABLE, AUTOMOBILE, GAMES, TELECOM, CREDIT PROVIDER, INVESTMENTS,
INSURANCE, MEDIA.

Run with AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY set (or default creds).
"""

import os
import sys
import io
import csv

try:
    import boto3
    import pandas as pd
except ImportError as e:
    print("Required: pip install boto3 pandas")
    sys.exit(1)

S3_BUCKET = "dashboard-inputs"
S3_REGION = "us-east-2"
US_POP = 329_900_000
SAMPLE_UNIVERSE = 10_000_000

# Categories to divide by 2 (must match pipeline)
DIVIDE_CATEGORIES = {
    "APP/PLATFORM USAGE",
    "APPS/PLATFORMS",
    "BANKING",
    "TRAVEL",
    "BROADCAST/CABLE",
    "AUTOMOBILE",
    "GAMES",
    "TELECOM",
    "CREDIT PROVIDER",
    "INVESTMENTS",
    "INSURANCE",
    "MEDIA",
}


def get_sample_size_from_df(df):
    """Get sample size from SAMPLE SIZE row; column D = index 3, or Category Share / Original Raw Numbers."""
    mask = df.iloc[:, 0].astype(str).str.strip().str.upper() == "SAMPLE SIZE"
    if not mask.any():
        return None
    row = df.loc[mask].iloc[0]
    # Column D = index 3 (0-based)
    if len(df.columns) > 3:
        val = row.iloc[3]
        if pd.notna(val) and str(val).strip():
            try:
                return int(float(str(val).replace(",", "")))
            except (ValueError, TypeError):
                pass
    if "Category Share" in df.columns:
        val = row.get("Category Share")
        if pd.notna(val) and str(val).strip():
            try:
                return int(float(str(val).replace(",", "")))
            except (ValueError, TypeError):
                pass
    if "Original Raw Numbers" in df.columns:
        val = row.get("Original Raw Numbers")
        if pd.notna(val) and str(val).strip():
            try:
                return int(float(str(val).replace(",", "")))
            except (ValueError, TypeError):
                pass
    return None


def safe_raw(x):
    """Parse Original Raw Numbers cell to int."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return 0
    try:
        return int(float(str(x).replace(",", "")))
    except (ValueError, TypeError):
        return 0


def process_csv(content: str) -> str:
    """Process CSV string: divide specified categories by 2 and recalc derived columns. Returns new CSV string."""
    df = pd.read_csv(io.StringIO(content), dtype=str, keep_default_na=False)
    if df.empty or len(df.columns) < 2:
        return content

    # Normalize first column name (often "Column")
    col_col = df.columns[0]
    sample_size = get_sample_size_from_df(df)
    if sample_size is None or sample_size <= 0:
        return content  # Skip if we can't get sample size

    raw_col = "Original Raw Numbers" if "Original Raw Numbers" in df.columns else None
    pct_col = "Category Share" if "Category Share" in df.columns else ("Percentage" if "Percentage" in df.columns else None)
    bp_col = "Brand Penetration (Row)" if "Brand Penetration (Row)" in df.columns else None
    genpop_col = "US Gen Pop Projection" if "US Gen Pop Projection" in df.columns else None

    if not raw_col:
        return content

    # Rows to process: Column (category) in DIVIDE_CATEGORIES
    col_upper = df[col_col].astype(str).str.strip().str.upper()
    for cat in DIVIDE_CATEGORIES:
        mask = col_upper == cat
        if not mask.any():
            continue
        indices = df.index[mask].tolist()
        for idx in indices:
            raw = safe_raw(df.at[idx, raw_col])
            if raw <= 0:
                continue
            new_raw = max(1, raw // 2)
            df.at[idx, raw_col] = str(new_raw)

            # Percent of total sample -> Brand Penetration (Row) only
            pct_of_sample = (new_raw / sample_size) * 100.0
            if bp_col:
                df.at[idx, bp_col] = f"{pct_of_sample:.4f}"

            # US Gen Pop Projection
            if genpop_col:
                genpop = int((new_raw / SAMPLE_UNIVERSE) * US_POP)
                df.at[idx, genpop_col] = str(genpop)

        # Recalculate Category Share within this category (percent of 100 within category)
        if pct_col and indices:
            raws = [safe_raw(df.at[i, raw_col]) for i in indices]
            total = sum(raws)
            if total > 0:
                for i, idx in enumerate(indices):
                    share = (raws[i] / total) * 100.0
                    df.at[idx, pct_col] = f"{share:.4f}"

    out = io.StringIO()
    df.to_csv(out, index=False)
    return out.getvalue()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Divide specified category values by 2 in S3 CSVs and recalc derived columns.")
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

    # Root keys: use Prefix="" and Delimiter="/" to get only root-level keys; then prefix "historic/"
    prefixes = ["", "historic/"]
    updated = 0
    errors = []

    for prefix in prefixes:
        paginator = s3.get_paginator("list_objects_v2")
        page_count = 0
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
            page_count += 1
            if page_count == 1:
                print(f"Listing CSVs (prefix={repr(prefix) or 'root'})...", flush=True)
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.lower().endswith(".csv"):
                    continue
                try:
                    resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
                    body = resp["Body"].read().decode("utf-8", errors="replace")
                    new_content = process_csv(body)
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

    print(f"Done. Updated {updated} file(s)." + (" (dry-run)" if dry_run else ""), flush=True)
    if errors:
        print(f"Errors: {len(errors)}", file=sys.stderr)
        for k, err in errors:
            print(f"  {k}: {err}", file=sys.stderr)
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
