#!/usr/bin/env python3
"""Test social media demographic review on all SOCIAL MEDIA profiles in S3."""
import os, sys, io
import pandas as pd
import boto3
from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from bg import (ai_social_media_demographic_review, _enforce_all_demographics,
                _AGE_MIDPOINTS, _AGE_CALIBRATION_TARGETS)

s3 = boto3.client('s3',
    aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
    region_name='us-east-2')

BUCKET = 'dashboard-inputs'

SOCIAL_MEDIA_KEYS = [
    'BLUESKY_11_07_2025_10_32.csv',
    'Facebook_11_07_2025_09_41.csv',
    'Instagram_11_07_2025_09_41.csv',
    'LINKEDIN_11_07_2025_09_41.csv',
    'ONLYFANS_11_07_2025_10_32.csv',
    'Pinterest_11_07_2025_10_32.csv',
    'SNAPCHAT_11_07_2025_10_32.csv',
    'TUMBLR_11_07_2025_10_32.csv',
    'Threads_11_07_2025_11_11.csv',
    'TikTok_11_07_2025_11_41.csv',
    'Truth_Social_11_07_2025_19_19.csv',
    'X_11_07_2025_09_41.csv',
]

def get_demo_summary(df, demo_cat):
    bp_col = 'Brand Penetration (Row)'
    mask = df['Column'].str.upper().str.strip() == demo_cat
    if not mask.any():
        return {}
    items = []
    for _, row in df[mask].iterrows():
        val = str(row.get('Value', '')).strip()
        try:
            bp = float(str(row.get(bp_col, 0)).replace('%', '').replace(',', ''))
        except:
            bp = 0
        items.append((val, bp))
    total = sum(bp for _, bp in items)
    if total <= 0:
        return {}
    return {v: round(bp/total*100, 1) for v, bp in items}

def calc_weighted_age(shares):
    total = 0
    weight = 0
    for label, pct in shares.items():
        mid = _AGE_MIDPOINTS.get(label)
        if mid:
            total += mid * pct
            weight += pct
    return round(total / weight, 1) if weight > 0 else 0

for key in SOCIAL_MEDIA_KEYS:
    print(f"\n{'='*70}")
    print(f"Processing: {key}")
    print(f"{'='*70}")

    try:
        obj = s3.get_object(Bucket=BUCKET, Key=key)
        df = pd.read_csv(io.BytesIO(obj['Body'].read()))
    except Exception as e:
        print(f"  ❌ Error reading: {e}")
        continue

    bi_mask = df['Column'].str.upper().str.strip() == 'BRAND INPUT'
    brand = str(df.loc[bi_mask, 'Value'].iloc[0]).strip() if bi_mask.any() else 'Unknown'
    brand_clean = brand.split(',')[0].replace('-', ' ').replace('_', ' ').strip()

    print(f"  Brand: {brand_clean}")

    age_before = get_demo_summary(df, 'AGE')
    gender_before = get_demo_summary(df, 'GENDER')
    so_before = get_demo_summary(df, 'SEXUAL_ORIENTATION')

    if age_before:
        print(f"  BEFORE age: {age_before} (weighted avg: {calc_weighted_age(age_before)})")
    if gender_before:
        print(f"  BEFORE gender: {gender_before}")
    if so_before:
        yes_pct = so_before.get('YES', so_before.get('Yes', 0))
        print(f"  BEFORE LGBTQ+: YES={yes_pct}%")

    df_fixed = ai_social_media_demographic_review(df.copy(), 'SOCIAL MEDIA', brand_clean, [brand_clean])

    age_after = get_demo_summary(df_fixed, 'AGE')
    gender_after = get_demo_summary(df_fixed, 'GENDER')
    so_after = get_demo_summary(df_fixed, 'SEXUAL_ORIENTATION')

    if age_after:
        print(f"  AFTER  age: {age_after} (weighted avg: {calc_weighted_age(age_after)})")
    if gender_after:
        print(f"  AFTER  gender: {gender_after}")
    if so_after:
        yes_pct = so_after.get('YES', so_after.get('Yes', 0))
        print(f"  AFTER  LGBTQ+: YES={yes_pct}%")

    # Write back to S3
    csv_buf = io.BytesIO()
    df_fixed.to_csv(csv_buf, index=False)
    csv_buf.seek(0)
    s3.put_object(Bucket=BUCKET, Key=key, Body=csv_buf.getvalue(), ContentType='text/csv')
    print(f"  ✅ Written back to S3: {key}")

print(f"\n{'='*70}")
print("Done! All 12 SOCIAL MEDIA profiles processed.")
