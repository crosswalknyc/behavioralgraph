#!/usr/bin/env python3
"""
Fix VMVPD/FAST category in S3 CSVs where Brand Penetration (Row) exceeds 100%.

For any file that has at least one VMVPD/FAST row with Brand Penetration > 100%:
- For that row (or each such row): set Brand Penetration = (current - 100).
- For all other rows in VMVPD/FAST: divide Brand Penetration by 2.
- Recalculate Original Raw Numbers from new penetration and sample size.
- Recalculate Category Share = (row raw / sum of raws in category) * 100.
- Recalculate US Gen Pop Projection = (raw / 10_000_000) * 329_900_000.

Only updates files that actually have VMVPD/FAST penetration > 100%.
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
VMVPD_FAST_NAMES = {"VMVPD/FAST", "VIRTUAL MVPD FAST", "VIRTUAL MVPD/FAST"}


def get_sample_size_from_df(df):
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
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return 0.0
    try:
        return float(str(x).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return 0.0


def process_csv(content: str) -> str:
    """
    If any VMVPD/FAST row has Brand Penetration > 100%:
    - Rows > 100%: penetration = penetration - 100
    - Rows <= 100%: penetration = penetration / 2
    Then recalc Original Raw Numbers, Category Share, US Gen Pop Projection.
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
    if not raw_col or not bp_col:
        return content

    col_upper = df[col_col].astype(str).str.strip().str.upper()
    mask = col_upper.isin(VMVPD_FAST_NAMES)
    if not mask.any():
        return content

    indices = df.index[mask].tolist()
    # Check if any row has Brand Penetration > 100
    any_over_100 = False
    for idx in indices:
        pct = safe_pct(df.at[idx, bp_col])
        if pct > 100.0:
            any_over_100 = True
            break
    if not any_over_100:
        return content

    # Apply: > 100% -> (pct - 100); <= 100% -> pct / 2
    for idx in indices:
        pct = safe_pct(df.at[idx, bp_col])
        if pct > 100.0:
            new_pct = pct - 100.0
        else:
            new_pct = pct / 2.0
        df.at[idx, bp_col] = f"{new_pct:.4f}"

    # Recalc Original Raw Numbers from new penetration
    for idx in indices:
        pct = safe_pct(df.at[idx, bp_col])
        new_raw = max(1, int(round((pct / 100.0) * sample_size)))
        df.at[idx, raw_col] = str(new_raw)
        if genpop_col:
            genpop = int((new_raw / SAMPLE_UNIVERSE) * US_POP)
            df.at[idx, genpop_col] = str(genpop)

    # Recalc Category Share within VMVPD/FAST
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
    parser = argparse.ArgumentParser(description="Cap VMVPD/FAST Brand Penetration over 100%; recalc metrics.")
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
