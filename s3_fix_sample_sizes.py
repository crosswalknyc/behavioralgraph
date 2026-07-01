#!/usr/bin/env python3
"""
Retroactively fix sample sizes for all profile CSVs in S3 dashboard-inputs.

For each profile:
  1. Look up the brand in the Gen Pop CSV to derive sample size from penetration
  2. If not found, estimate based on digital panel tier for the BRAND CATEGORY
  3. Recalculate Original Raw Numbers and US Gen Pop Projection for every row

Usage:
  PYTHONUNBUFFERED=1 python3 s3_fix_sample_sizes.py --dry-run   # preview
  PYTHONUNBUFFERED=1 python3 s3_fix_sample_sizes.py              # for real
"""

import os, sys, io, re, urllib.parse

try:
    import boto3
    import pandas as pd
    import numpy as np
except ImportError:
    print("Required: pip install boto3 pandas numpy")
    sys.exit(1)

S3_BUCKET = "dashboard-inputs"
S3_REGION = "us-east-2"
GEN_POP_KEY = "Gen_Pop_2026_03_04_2026_04_29.csv"
GENPOP_SAMPLE_CAP = 10_000_000
US_POP = 329_900_000

BRAND_CATEGORY_TO_GENPOP_CATS = {
    'ACTOR': ['ACTOR', 'TALENT'],
    'MUSICIAN/BAND': ['MUSICIAN/BAND', 'TALENT'],
    'HOST/PERSONALITY': ['HOST/PERSONALITY', 'TALENT'],
    'ATHLETE': ['ATHLETE', 'TALENT'],
    'POLITICS/ACTIVIST': ['POLITICS/ACTIVIST', 'TALENT'],
    'WRITER/DIRECTOR/AUTHOR/ARTIST': ['WRITER/DIRECTOR/AUTHOR/ARTIST', 'TALENT'],
    'CREATOR/INFLUENCER': ['TALENT', 'HOST/PERSONALITY'],
    'QSR': ['QSR', 'WHERE THEY DINE'],
    'MEDIA': ['MEDIA', 'BROADCAST/CABLE'],
    'SOCIAL MEDIA': ['SOCIAL MEDIA', 'APP/PLATFORM USAGE'],
    'TELECOM': ['TELECOM'],
    'DIGITAL BANKING': ['DIGITAL BANKING', 'BANKING'],
    'BANKING': ['BANKING', 'DIGITAL BANKING'],
    'STREAMING/PLATFORM': ['STREAMING/PLATFORM'],
    'STREAMING/MUSIC': ['STREAMING/MUSIC'],
    'GAMES': ['GAMES'],
    'INSURANCE': ['INSURANCE'],
    'AUTOMOBILE': ['AUTOMOBILE'],
    'TRAVEL': ['TRAVEL'],
    'BETTING': ['BETTING'],
    'RETAILERS': ['WHERE THEY SHOP', 'MOST PURCHASED BRANDS'],
    'GROCERY': ['WHERE THEY SHOP', 'MOST PURCHASED BRANDS'],
    'APPAREL': ['APPAREL/FOOTWEAR', 'MOST PURCHASED BRANDS'],
    'FOOTWEAR': ['APPAREL/FOOTWEAR', 'MOST PURCHASED BRANDS'],
    'BEAUTY': ['BEAUTY/WELLNESS', 'MOST PURCHASED BRANDS'],
    'BEVERAGE': ['CPG', 'QSR', 'MOST PURCHASED BRANDS'],
    'TOY': ['TOYS', 'FRANCHISE'],
    'PHARMA': ['PHARMACY'],
    'PLATFORMS': ['APP/PLATFORM USAGE', 'STREAMING/PLATFORM'],
    'PODCAST': ['PODCAST'],
    'NON PROFIT/CHARITY': ['NON PROFIT/CHARITY'],
    'MOVIE THEATER': ['MOVIE THEATER'],
    'AMUSEMENT PARKS': ['AMUSEMENT PARKS'],
    'COLLEGE/UNIVERSITY': ['COLLEGE/UNIVERSITY'],
    'INVESTMENTS': ['INVESTMENTS'],
    'CREDIT PROVIDER': ['CREDIT PROVIDER'],
    'TECHNOLOGY/DEVICE': ['TECHNOLOGY/DEVICE', 'TECHNOLOGY BRAND'],
}

DIGITAL_PANEL_TIER_ESTIMATES = {
    'STREAMING/PLATFORM': (0.15, 0.55),
    'SOCIAL MEDIA': (0.10, 0.45),
    'APP/PLATFORM USAGE': (0.08, 0.40),
    'SEARCH ENGINE/AI': (0.10, 0.45),
    'TELECOM': (0.08, 0.35),
    'STREAMING/MUSIC': (0.05, 0.30),
    'DIGITAL BANKING': (0.05, 0.25),
    'BANKING': (0.03, 0.20),
    'MEDIA': (0.03, 0.20),
    'BROADCAST/CABLE': (0.03, 0.20),
    'GAMES': (0.02, 0.20),
    'ACTOR': (0.01, 0.12),
    'MUSICIAN/BAND': (0.01, 0.15),
    'HOST/PERSONALITY': (0.005, 0.08),
    'ATHLETE': (0.005, 0.10),
    'CREATOR/INFLUENCER': (0.005, 0.08),
    'POLITICS/ACTIVIST': (0.005, 0.10),
    'QSR': (0.03, 0.20),
    'RETAILERS': (0.03, 0.20),
    'GROCERY': (0.03, 0.15),
    'APPAREL': (0.02, 0.12),
    'FOOTWEAR': (0.02, 0.10),
    'BEAUTY': (0.02, 0.12),
    'INSURANCE': (0.02, 0.10),
    'AUTOMOBILE': (0.02, 0.10),
    'TRAVEL': (0.03, 0.15),
    'BETTING': (0.02, 0.10),
    'INVESTMENTS': (0.02, 0.10),
    'CREDIT PROVIDER': (0.02, 0.10),
    'TECHNOLOGY/DEVICE': (0.03, 0.15),
    'PHARMACY': (0.02, 0.10),
    'BEVERAGE': (0.02, 0.12),
    'TOY': (0.01, 0.08),
    'PHARMA': (0.01, 0.08),
    'PODCAST': (0.01, 0.08),
    'NON PROFIT/CHARITY': (0.01, 0.06),
    'MOVIE THEATER': (0.02, 0.08),
    'AMUSEMENT PARKS': (0.01, 0.06),
    'HEAVY MACHINERY': (0.002, 0.03),
    'COLLEGE/UNIVERSITY': (0.005, 0.05),
}


def normalize_brand(name):
    if not name:
        return ''
    s = str(name).strip()
    try:
        s = urllib.parse.unquote(s)
    except Exception:
        pass
    s = re.sub(r'[-._/\\|~#$%&*+=@]+', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip().upper()
    return s


def get_sample_size_from_df(df):
    mask = df.iloc[:, 0].astype(str).str.strip().str.upper() == 'SAMPLE SIZE'
    if not mask.any():
        return 0
    row = df.loc[mask].iloc[0]
    for col_idx in [3, 4, 2]:
        if col_idx < len(df.columns):
            val = row.iloc[col_idx]
            if pd.notna(val):
                try:
                    v = int(float(str(val).replace(',', '')))
                    if v > 0:
                        return v
                except (ValueError, TypeError):
                    pass
    return 0


def get_brand_info(df):
    brand_name = ''
    brand_category = ''
    bi_mask = df.iloc[:, 0].astype(str).str.strip().str.upper() == 'BRAND INPUT'
    if bi_mask.any():
        raw = str(df.loc[bi_mask].iloc[0, 1])
        brand_name = raw.split(',')[0].strip()
    bc_mask = df.iloc[:, 0].astype(str).str.strip().str.upper() == 'BRAND CATEGORY'
    if bc_mask.any():
        brand_category = str(df.loc[bc_mask].iloc[0, 1]).strip()
    return brand_name, brand_category


def lookup_genpop(gp, brand_name, brand_category, filename_brand):
    norm = normalize_brand(brand_name)
    norm_file = normalize_brand(filename_brand)
    col_name = gp.columns[0]
    val_name = gp.columns[1]
    bp_name = gp.columns[2]
    gp_u = gp.copy()
    gp_u['_col'] = gp_u[col_name].astype(str).str.strip().str.upper()
    gp_u['_val'] = gp_u[val_name].astype(str).str.strip().str.upper()

    bc_upper = (brand_category or '').strip().upper()
    search_cats = []
    if bc_upper.startswith('SERIES'):
        search_cats = ['STREAMING/PLATFORM', 'FRANCHISE', 'MEDIA']
    elif bc_upper.startswith('GAMES'):
        search_cats = ['GAMES']
    elif bc_upper:
        search_cats = BRAND_CATEGORY_TO_GENPOP_CATS.get(bc_upper, [bc_upper])

    for try_name in [norm, norm_file]:
        if not try_name:
            continue
        for cat in search_cats:
            mask = (gp_u['_col'] == cat) & (gp_u['_val'] == try_name)
            if mask.any():
                return float(gp_u.loc[mask].iloc[0][bp_name]), cat

    skip = {'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE', 'AVID FAN', 'CASUAL FAN',
            'AGE', 'EDUCATION', 'ETHNICITY', 'GENDER', 'INCOME', 'OCCUPATION',
            'LOCATION', 'PARENTAL_STATUS', 'RELATIONSHIP', 'SEXUAL_ORIENTATION',
            'BRAND CATEGORY'}
    for try_name in [norm, norm_file]:
        if not try_name:
            continue
        for cat in gp_u['_col'].unique():
            if cat in skip:
                continue
            mask = (gp_u['_col'] == cat) & (gp_u['_val'] == try_name)
            if mask.any():
                return float(gp_u.loc[mask].iloc[0][bp_name]), cat

    return None, None


def estimate_sample_size(brand_category, current_sample_size=0):
    bc_upper = (brand_category or '').strip().upper()
    if bc_upper.startswith('SERIES'):
        bc_upper = 'STREAMING/PLATFORM'
    elif bc_upper.startswith('GAMES'):
        bc_upper = 'GAMES'
    tier = DIGITAL_PANEL_TIER_ESTIMATES.get(bc_upper, (0.01, 0.08))
    lo, hi = tier
    if current_sample_size and current_sample_size > 0:
        ratio = min(current_sample_size / GENPOP_SAMPLE_CAP, 1.0)
        pct = lo + (hi - lo) * ratio
    else:
        pct = (lo + hi) / 2
    ss = round(pct * GENPOP_SAMPLE_CAP)
    ss = max(ss, 10_000)
    ss = min(ss, GENPOP_SAMPLE_CAP)
    return (ss // 10) * 10


def filename_to_brand(key):
    fn = key.split('/')[-1].replace('.csv', '')
    fn = re.sub(r'_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}$', '', fn)
    fn = re.sub(r'_\d{2}_\d{2}_\d{4}$', '', fn)
    fn = re.sub(r'_\d{4}_\d{2}_\d{2}_\d{2}_\d{2}$', '', fn)
    fn = re.sub(r'_\d{4}_\d{2}_\d{2}$', '', fn)
    return fn.replace('_', ' ').strip()


def process_file(s3, key, gp, dry_run=True):
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
        df = pd.read_csv(io.BytesIO(obj['Body'].read()))
    except Exception as e:
        print(f"  ERROR reading {key}: {e}")
        return False

    brand_name, brand_category = get_brand_info(df)
    filename_brand = filename_to_brand(key)
    current_ss = get_sample_size_from_df(df)

    gp_pct, gp_cat = lookup_genpop(gp, brand_name, brand_category, filename_brand)

    if gp_pct is not None and gp_pct > 0:
        new_ss = round(gp_pct / 100 * GENPOP_SAMPLE_CAP)
        new_ss = (new_ss // 10) * 10
        new_ss = max(new_ss, 10_000)
        source = f"Gen Pop {gp_cat} @ {gp_pct:.4f}%"
    else:
        new_ss = estimate_sample_size(brand_category, current_ss)
        source = f"estimated ({brand_category or 'UNKNOWN'})"

    if new_ss == current_ss:
        print(f"  {key}: sample size already correct ({current_ss:,})")
        return False

    print(f"  {key}: {current_ss:,} -> {new_ss:,} [{source}]")

    if dry_run:
        return True

    col_name = df.columns[0]
    bp_col = df.columns[2]

    ss_mask = df[col_name].astype(str).str.strip().str.upper() == 'SAMPLE SIZE'
    if ss_mask.any():
        df.loc[ss_mask, df.columns[3]] = new_ss
        df.loc[ss_mask, df.columns[4]] = new_ss
        df.loc[ss_mask, df.columns[5]] = round(new_ss / GENPOP_SAMPLE_CAP * US_POP)

    bi_mask = df[col_name].astype(str).str.strip().str.upper() == 'BRAND INPUT'
    if bi_mask.any():
        df.loc[bi_mask, df.columns[5]] = round(new_ss / GENPOP_SAMPLE_CAP * US_POP)

    skip_cats = {'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'AVID FAN', 'CASUAL FAN'}
    for idx, row in df.iterrows():
        cat = str(row.iloc[0]).strip().upper()
        if cat in skip_cats:
            continue
        try:
            bp = float(str(row.iloc[2]).replace('%', '').strip())
        except (ValueError, TypeError):
            continue
        if bp <= 0:
            continue
        raw = round((bp / 100) * new_ss)
        us_proj = round((raw / GENPOP_SAMPLE_CAP) * US_POP)
        df.at[idx, df.columns[4]] = raw
        df.at[idx, df.columns[5]] = us_proj

    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=buf.getvalue(), ContentType='text/csv')
    return True


def main():
    dry_run = '--dry-run' in sys.argv
    print(f"{'DRY RUN' if dry_run else 'LIVE RUN'}: Fix sample sizes for all S3 profile CSVs")
    print(f"Bucket: {S3_BUCKET}")
    print()

    s3 = boto3.client('s3', region_name=S3_REGION)

    print(f"Loading Gen Pop baseline: {GEN_POP_KEY} ...")
    obj = s3.get_object(Bucket=S3_BUCKET, Key=GEN_POP_KEY)
    gp = pd.read_csv(io.BytesIO(obj['Body'].read()))
    print(f"  {len(gp)} rows loaded")
    print()

    print("Listing top-level CSVs ...")
    paginator = s3.get_paginator('list_objects_v2')
    keys = []
    for page in paginator.paginate(Bucket=S3_BUCKET, Delimiter='/'):
        for o in page.get('Contents', []):
            k = o['Key']
            if k.endswith('.csv') and 'gen_pop' not in k.lower() and 'genpop' not in k.lower():
                keys.append(k)
    print(f"  {len(keys)} profile CSVs found")
    print()

    changed = 0
    errors = 0
    for i, key in enumerate(sorted(keys)):
        pct = (i + 1) / len(keys) * 100
        print(f"[{i+1}/{len(keys)} {pct:.0f}%] Processing {key}")
        try:
            if process_file(s3, key, gp, dry_run=dry_run):
                changed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            errors += 1

    print()
    print(f"Done. {changed} files {'would be' if dry_run else ''} modified. {errors} errors.")


if __name__ == '__main__':
    main()
