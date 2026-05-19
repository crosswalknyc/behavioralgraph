#!/usr/bin/env python3
"""
Check Total Show Conversion Rate in CSV files in s3://svod-acquisition/ (read-only, does not modify S3).
Reports whether each file's rate matches (New Platform Signups Gen Pop / Total Show Watchers Gen Pop) * 100.
CSV files are never modified — this script only validates and reports.
"""

import boto3
import csv
import io
import os
import re
from botocore.exceptions import ClientError

SUBSCRIBER_S3_BUCKET = 'svod-acquisition'
S3_REGION = os.environ.get('AWS_REGION', 'us-east-1')
S3_PURGATORY_PREFIX = 'purgatory/'


def parse_number(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        cleaned = str(value).strip().replace(',', '').replace('$', '').replace('%', '')
        if not cleaned:
            return None
        return int(float(cleaned)) if '.' in cleaned else int(cleaned)
    except (ValueError, TypeError):
        return None


def parse_pct(value):
    """Parse percentage string like '12.34%' or '12.34' to float."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        cleaned = str(value).strip().replace(',', '').replace('%', '').strip()
        if not cleaned:
            return None
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def ensure_cols(row, min_len):
    if len(row) >= min_len:
        return row
    return list(row) + [''] * (min_len - len(row))


def check_and_fix_conversion_rate(rows):
    """
    Find Total Show Watchers (col2 count, col9 gen_pop), New Platform Signups (col2 count, col9 gen_pop), Total Show Conversion Rate (col8).
    Use projected (Gen Pop) numbers: rate = (NPS gen_pop / TW gen_pop) * 100.
    Returns (changed: bool, error: str|None, details: str).
    """
    total_watchers_proj = None   # col9 Gen Pop Projection
    new_signups_proj = None
    conversion_row_idx = None
    MIN_COLS = 10
    GEN_POP_COL = 9

    for i, row in enumerate(rows):
        if not row:
            continue
        first_col = (row[0].strip() if len(row) > 0 and row[0] else '').strip()
        if 'Total Show Watchers' in first_col:
            total_watchers_proj = parse_number(row[GEN_POP_COL]) if len(row) > GEN_POP_COL else None
        elif 'New Platform Signups' in first_col:
            new_signups_proj = parse_number(row[GEN_POP_COL]) if len(row) > GEN_POP_COL else None
        elif 'Total Show Conversion Rate' in first_col:
            conversion_row_idx = i
            break

    if conversion_row_idx is None:
        return False, "Total Show Conversion Rate row not found", ""
    if total_watchers_proj is None:
        return False, "Total Show Watchers (Gen Pop) not found", ""
    if new_signups_proj is None:
        return False, "New Platform Signups (Gen Pop) not found", ""

    expected_rate = round((new_signups_proj * 100.0) / total_watchers_proj, 2) if total_watchers_proj > 0 else 0.0
    row = rows[conversion_row_idx]
    row = ensure_cols(row, MIN_COLS)
    rows[conversion_row_idx] = row

    current_val = row[8].strip() if len(row) > 8 else ''
    current_rate = parse_pct(current_val)

    details = f"TW_proj={total_watchers_proj}, NPS_proj={new_signups_proj} => expected {expected_rate:.2f}%, current={current_val}"

    if current_rate is not None and abs(current_rate - expected_rate) < 0.01:
        return False, None, details  # already correct (allow tiny float diff)

    row[8] = f"{expected_rate:.2f}%"
    rows[conversion_row_idx] = row
    return True, None, details


def csv_rows_to_content(rows):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


def main():
    # Check-only: never modify CSVs in S3 (report only)
    s3 = boto3.client(
        's3',
        region_name=S3_REGION,
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
    )

    paginator = s3.get_paginator('list_objects_v2')
    keys = []
    for page in paginator.paginate(Bucket=SUBSCRIBER_S3_BUCKET):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if key.startswith('historic/') or key.startswith(S3_PURGATORY_PREFIX) or not key.endswith('.csv'):
                continue
            keys.append(key)

    print(f"Found {len(keys)} CSV files in s3://{SUBSCRIBER_S3_BUCKET}/")
    ok = 0
    fixed = 0
    skipped = 0
    errors = []

    # Skip 56_Days: conversion rate in file is 0.77% (NPS % of 9,999,995), not NPS/TW
    skip_pattern = '56_Days'

    for key in sorted(keys):
        if skip_pattern in key:
            print(f"  Skip {key}: conversion rate uses different denominator (excluded from check)")
            skipped += 1
            continue
        try:
            resp = s3.get_object(Bucket=SUBSCRIBER_S3_BUCKET, Key=key)
            content = resp['Body'].read().decode('utf-8')
        except Exception as e:
            errors.append((key, str(e)))
            continue

        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        changed, err, details = check_and_fix_conversion_rate(rows)
        if err:
            skipped += 1
            print(f"  Skip {key}: {err}")
            continue
        if changed:
            print(f"  Would fix (not modifying S3): {key}: {details}")
            fixed += 1
        else:
            ok += 1
            print(f"  OK {key}: {details}")

    print(f"\nDone. OK: {ok}, Mismatched (not modified): {fixed}, Skipped: {skipped}, Errors: {len(errors)}")
    for k, err in errors:
        print(f"  Error {k}: {err}")
    print("This script is read-only and does not change any CSV files in S3.")


if __name__ == '__main__':
    main()
