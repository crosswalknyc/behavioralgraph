#!/usr/bin/env python3
"""
Update VMVPD/FAST category in S3 CSV files: multiply Original Raw Numbers by 20
and recalc derived columns (percent of sample, Category Share, US Gen Pop Projection).
Skips any file where any value in VMVPD/FAST is already over 10% (percent of total sample).

Same methodology as divide_s3_csv_categories_by_2.py:
- Sample size from SAMPLE SIZE row, column D (index 3).
- Original Raw Numbers * 20 -> then recalc Brand Penetration (Row), Category Share, US Gen Pop Projection.
- Only process files where no VMVPD/FAST row has percent of sample > 10%.

Use: PYTHONUNBUFFERED=1 python3 multiply_s3_csv_vmvpd_fast_by_20.py
"""

import os
import sys
import io

try:
    import boto3
    import pandas as pd
except ImportError:
    print("Required: pip install boto3 pandas")
    sys.exit(1)

S3_BUCKET = "dashboard-inputs"
S3_REGION = "us-east-2"
US_POP = 329_900_000
SAMPLE_UNIVERSE = 10_000_000
# Category may appear as any of these in CSVs (pipeline uses VIRTUAL MVPD FAST, VMVPD/FAST, etc.)
VMVPD_FAST_NAMES = {"VMVPD/FAST", "VIRTUAL MVPD FAST", "VIRTUAL MVPD/FAST"}
MAX_PCT_THRESHOLD = 10.0  # Skip file if any VMVPD/FAST row is already above this % of sample


def get_sample_size_from_df(df):
    """Get sample size from SAMPLE SIZE row; column D = index 3."""
    mask = df.iloc[:, 0].astype(str).str.strip().str.upper() == "SAMPLE SIZE"
    if not mask.any():
        return None
    row = df.loc[mask].iloc[0]
    if len(df.columns) > 3:
        val = row.iloc[3]
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
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return 0
    try:
        return int(float(str(x).replace(",", "")))
    except (ValueError, TypeError):
        return 0


def safe_pct(x):
    """Parse a percentage cell to float."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return 0.0
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return 0.0


def process_csv(content: str) -> str:
    """
    If VMVPD/FAST exists and no row in that category has percent of sample > 10%,
    multiply all VMVPD/FAST Original Raw Numbers by 20 and recalc derived columns.
    Otherwise return content unchanged.
    """
    df = pd.read_csv(io.StringIO(content), dtype=str, keep_default_na=False)
    if df.empty or len(df.columns) < 2:
        return content

    col_col = df.columns[0]
    sample_size = get_sample_size_from_df(df)
    if sample_size is None or sample_size <= 0:
        return content

    raw_col = "Original Raw Numbers" if "Original Raw Numbers" in df.columns else None
    pct_col = "Category Share" if "Category Share" in df.columns else ("Percentage" if "Percentage" in df.columns else None)
    bp_col = "Brand Penetration (Row)" if "Brand Penetration (Row)" in df.columns else None
    genpop_col = "US Gen Pop Projection" if "US Gen Pop Projection" in df.columns else None
    if not raw_col:
        return content

    # Match VMVPD/FAST variants (case-insensitive)
    col_upper = df[col_col].astype(str).str.strip().str.upper()
    mask = col_upper.isin(VMVPD_FAST_NAMES)
    if not mask.any():
        return content

    indices = df.index[mask].tolist()

    # Check: skip if any row in VMVPD/FAST is already over 10% of sample
    for idx in indices:
        raw = safe_raw(df.at[idx, raw_col])
        if raw <= 0:
            continue
        pct_of_sample = (raw / sample_size) * 100.0
        if pct_of_sample > MAX_PCT_THRESHOLD:
            return content  # Skip this file

    # Also check Brand Penetration (Row) if present, in case it's stored there
    if bp_col:
        for idx in indices:
            pct = safe_pct(df.at[idx, bp_col])
            if pct > MAX_PCT_THRESHOLD:
                return content

    # Apply x20 and recalc
    for idx in indices:
        raw = safe_raw(df.at[idx, raw_col])
        new_raw = raw * 20
        df.at[idx, raw_col] = str(new_raw)

        pct_of_sample = (new_raw / sample_size) * 100.0
        if bp_col:
            df.at[idx, bp_col] = f"{pct_of_sample:.4f}"

        if genpop_col:
            genpop = int((new_raw / SAMPLE_UNIVERSE) * US_POP)
            df.at[idx, genpop_col] = str(genpop)

    # Recalculate Category Share within VMVPD/FAST
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
    parser = argparse.ArgumentParser(description="Multiply VMVPD/FAST by 20 in S3 CSVs; skip if category already >10%.")
    parser.add_argument("--dry-run", action="store_true", help="Do not upload changes.")
    args = parser.parse_args()
    dry_run = getattr(args, "dry_run", False)

    s3 = boto3.client(
        "s3",
        region_name=S3_REGION,
        endpoint_url=f"https://s3.{S3_REGION}.amazonaws.com",
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )

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
                        # Either no VMVPD/FAST, or already >10% and we skipped
                        continue
                    if dry_run:
                        print(f"[dry-run] Would update: {key}", flush=True)
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
                    print(f"Error {key}: {e}", file=sys.stderr, flush=True)

    print(f"Done. Updated {updated} file(s)." + (" (dry-run)" if dry_run else ""), flush=True)
    if errors:
        print(f"Errors: {len(errors)}", file=sys.stderr, flush=True)
        for k, err in errors:
            print(f"  {k}: {err}", file=sys.stderr, flush=True)
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
