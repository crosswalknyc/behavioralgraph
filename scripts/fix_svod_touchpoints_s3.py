#!/usr/bin/env python3
"""
Fix POST-SIGNUP TOUCHPOINT ANALYSIS in all CSV files in the svod-acquisition S3 bucket:
- 1st Touchpoint Gen Pop Projection = always New Platform Signups Gen Pop Projection
- Total Platform Signups Gen Pop Projection = sum of 1st, 2nd, 3rd, 4th, 5th Gen Pop
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


def ensure_cols(row, min_len):
    """Ensure row has at least min_len columns (pad with empty string)."""
    if len(row) >= min_len:
        return row
    return list(row) + [''] * (min_len - len(row))


def fix_touchpoints_section(rows):
    """
    Modify rows in place. Find New Platform Signups gen_pop, then in POST-SIGNUP TOUCHPOINT
    section set 1st Touchpoint col9 = that value, and Total Platform Signups col9 = sum(1st-5th).
    CSV: col0=label, col2=Count, col8=Percentage, col9=Gen Pop Projection.
    """
    new_signups_gen_pop = None
    in_touchpoint_section = False
    first_touchpoint_row_idx = None
    touchpoint_row_indices = []  # list of (row_idx, '1'|'2'|...|'5'|'Total')
    GEN_POP_COL = 9
    MIN_COLS = 10

    for i, row in enumerate(rows):
        if not row:
            continue
        first_col = (row[0].strip() if len(row) > 0 and row[0] else '').strip()
        # KEY METRICS section: find New Platform Signups
        if 'New Platform Signups' in first_col:
            row = ensure_cols(row, MIN_COLS)
            rows[i] = row
            val = parse_number(row[GEN_POP_COL]) if len(row) > GEN_POP_COL else None
            if val is not None:
                new_signups_gen_pop = val

        if 'POST-SIGNUP TOUCHPOINT ANALYSIS' in first_col:
            in_touchpoint_section = True
            continue
        if in_touchpoint_section:
            # Leave section on next major header (stop collecting rows)
            if first_col and (
                'COMPETITIVE PLATFORMS' in first_col
                or (len(row) > 2 and 'COMPETITIVE PLATFORMS' in str(row[2] or ''))
            ):
                in_touchpoint_section = False
                continue
            # Match "1st Touchpoint", "2nd Touchpoint", ... "Total Platform Signups"
            if first_col.endswith('Touchpoint'):
                touchpoint_num = first_col.replace('Touchpoint', '').strip().rstrip()
                if touchpoint_num == '1st':
                    first_touchpoint_row_idx = i
                    touchpoint_row_indices.append((i, '1'))
                elif touchpoint_num == '2nd':
                    touchpoint_row_indices.append((i, '2'))
                elif touchpoint_num == '3rd':
                    touchpoint_row_indices.append((i, '3'))
                elif touchpoint_num == '4th':
                    touchpoint_row_indices.append((i, '4'))
                elif touchpoint_num == '5th':
                    touchpoint_row_indices.append((i, '5'))
            elif 'Total Platform Signups' in first_col:
                touchpoint_row_indices.append((i, 'Total'))

    if new_signups_gen_pop is None:
        return False, "New Platform Signups gen pop not found"

    changed = False
    # Set 1st Touchpoint gen_pop = New Platform Signups
    if first_touchpoint_row_idx is not None:
        row = ensure_cols(rows[first_touchpoint_row_idx], MIN_COLS)
        old_val = row[GEN_POP_COL] if len(row) > GEN_POP_COL else ''
        new_val = str(new_signups_gen_pop)
        if old_val != new_val:
            row[GEN_POP_COL] = new_val
            rows[first_touchpoint_row_idx] = row
            changed = True

    # Sum 1st-5th gen_pop (1st already updated) and set Total
    total_gen_pop = 0
    for (idx, key) in touchpoint_row_indices:
        if key == 'Total':
            continue
        row = rows[idx]
        row = ensure_cols(row, MIN_COLS)
        rows[idx] = row
        v = parse_number(row[GEN_POP_COL]) if len(row) > GEN_POP_COL else 0
        if v is not None:
            total_gen_pop += v

    total_row_idx = next((idx for (idx, key) in touchpoint_row_indices if key == 'Total'), None)
    if total_row_idx is not None:
        row = ensure_cols(rows[total_row_idx], MIN_COLS)
        old_val = row[GEN_POP_COL] if len(row) > GEN_POP_COL else ''
        new_val = str(total_gen_pop)
        if old_val != new_val:
            row[GEN_POP_COL] = new_val
            rows[total_row_idx] = row
            changed = True

    return changed, None


def csv_rows_to_content(rows):
    """Write rows back to CSV string (Excel dialect)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


def main():
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
    fixed = 0
    skipped = 0
    errors = []

    for key in sorted(keys):
        try:
            resp = s3.get_object(Bucket=SUBSCRIBER_S3_BUCKET, Key=key)
            content = resp['Body'].read().decode('utf-8')
        except Exception as e:
            errors.append((key, str(e)))
            continue

        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        changed, err = fix_touchpoints_section(rows)
        if err:
            skipped += 1
            print(f"  Skip {key}: {err}")
            continue
        if changed:
            new_content = csv_rows_to_content(rows)
            s3.put_object(
                Bucket=SUBSCRIBER_S3_BUCKET,
                Key=key,
                Body=new_content.encode('utf-8'),
                ContentType='text/csv',
            )
            fixed += 1
            print(f"  Fixed {key}")
        else:
            print(f"  OK (no change) {key}")

    print(f"\nDone. Fixed: {fixed}, No change/skip: {len(keys) - fixed - len(errors)}, Errors: {len(errors)}")
    for key, err in errors:
        print(f"  Error {key}: {err}")


if __name__ == '__main__':
    main()
