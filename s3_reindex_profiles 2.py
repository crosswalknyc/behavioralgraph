#!/usr/bin/env python3
"""
Phase 2: Reindex all S3 profile CSVs with archetype-based demographic skews.

For each profile, determines the brand archetype and applies common-sense
demographic adjustments so profiles reflect their audience:
  - Taylor Swift -> younger, female-skewing, higher pop culture affinity
  - The Rock -> male-skewing, sports-heavy, fitness brands
  - Netflix -> broad, slight young-skew, tech-savvy
  - Sephora -> female-skewing, younger, beauty/fashion
  - etc.

Usage:
  PYTHONUNBUFFERED=1 python3 s3_reindex_profiles.py --dry-run   # preview
  PYTHONUNBUFFERED=1 python3 s3_reindex_profiles.py              # for real
"""

import os, sys, io, re, urllib.parse, math, hashlib

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

# ============================================================================
# ARCHETYPE DEFINITIONS
# Each archetype has demographic skew multipliers relative to Gen Pop
# >1.0 means over-index, <1.0 means under-index
# ============================================================================

ARCHETYPE_SKEWS = {
    'YOUNG_FEMALE_POP': {
        'AGE': {'18-24': 1.6, '25-34': 1.4, '35-44': 0.9, '45-54': 0.7, '55-64': 0.5, '65+': 0.3},
        'GENDER': {'Female': 1.5, 'Male': 0.6, 'Non-Binary': 1.3},
    },
    'YOUNG_MALE_ACTION': {
        'AGE': {'18-24': 1.3, '25-34': 1.5, '35-44': 1.2, '45-54': 0.8, '55-64': 0.5, '65+': 0.4},
        'GENDER': {'Female': 0.6, 'Male': 1.5, 'Non-Binary': 0.9},
    },
    'SPORTS_MALE': {
        'AGE': {'18-24': 1.2, '25-34': 1.4, '35-44': 1.3, '45-54': 1.0, '55-64': 0.7, '65+': 0.5},
        'GENDER': {'Female': 0.5, 'Male': 1.6, 'Non-Binary': 0.7},
    },
    'BEAUTY_FASHION': {
        'AGE': {'18-24': 1.5, '25-34': 1.4, '35-44': 1.0, '45-54': 0.7, '55-64': 0.5, '65+': 0.3},
        'GENDER': {'Female': 1.7, 'Male': 0.4, 'Non-Binary': 1.2},
    },
    'TECH_DIGITAL': {
        'AGE': {'18-24': 1.3, '25-34': 1.4, '35-44': 1.2, '45-54': 0.8, '55-64': 0.6, '65+': 0.4},
        'GENDER': {'Female': 0.8, 'Male': 1.2, 'Non-Binary': 1.1},
    },
    'BROAD_MAINSTREAM': {
        'AGE': {'18-24': 1.1, '25-34': 1.2, '35-44': 1.1, '45-54': 1.0, '55-64': 0.9, '65+': 0.7},
        'GENDER': {'Female': 1.05, 'Male': 0.95, 'Non-Binary': 1.0},
    },
    'OLDER_CONSERVATIVE': {
        'AGE': {'18-24': 0.4, '25-34': 0.6, '35-44': 0.8, '45-54': 1.2, '55-64': 1.5, '65+': 1.6},
        'GENDER': {'Female': 0.8, 'Male': 1.2, 'Non-Binary': 0.6},
    },
    'FAMILY_ORIENTED': {
        'AGE': {'18-24': 0.6, '25-34': 1.3, '35-44': 1.5, '45-54': 1.1, '55-64': 0.7, '65+': 0.5},
        'GENDER': {'Female': 1.2, 'Male': 0.9, 'Non-Binary': 0.8},
    },
    'HIP_HOP_URBAN': {
        'AGE': {'18-24': 1.5, '25-34': 1.4, '35-44': 1.0, '45-54': 0.6, '55-64': 0.4, '65+': 0.2},
        'GENDER': {'Female': 0.8, 'Male': 1.2, 'Non-Binary': 1.0},
        'ETHNICITY': {'Black or African American': 1.6, 'Hispanic or Latino': 1.3, 'White': 0.8},
    },
    'LATINO_CROSSOVER': {
        'AGE': {'18-24': 1.3, '25-34': 1.4, '35-44': 1.1, '45-54': 0.8, '55-64': 0.5, '65+': 0.3},
        'GENDER': {'Female': 1.1, 'Male': 0.9, 'Non-Binary': 1.0},
        'ETHNICITY': {'Hispanic or Latino': 1.8, 'White': 0.8, 'Black or African American': 0.9},
    },
    'COUNTRY_HEARTLAND': {
        'AGE': {'18-24': 0.7, '25-34': 1.1, '35-44': 1.3, '45-54': 1.2, '55-64': 1.0, '65+': 0.8},
        'GENDER': {'Female': 1.1, 'Male': 0.9, 'Non-Binary': 0.6},
        'ETHNICITY': {'White': 1.3, 'Black or African American': 0.5, 'Hispanic or Latino': 0.7},
    },
    'ROCK_CLASSIC': {
        'AGE': {'18-24': 0.5, '25-34': 0.8, '35-44': 1.2, '45-54': 1.4, '55-64': 1.3, '65+': 1.0},
        'GENDER': {'Female': 0.7, 'Male': 1.3, 'Non-Binary': 0.8},
    },
    'GAMING_ESPORTS': {
        'AGE': {'18-24': 1.7, '25-34': 1.4, '35-44': 0.8, '45-54': 0.5, '55-64': 0.3, '65+': 0.2},
        'GENDER': {'Female': 0.5, 'Male': 1.5, 'Non-Binary': 1.2},
    },
    'FOOD_BEVERAGE': {
        'AGE': {'18-24': 1.0, '25-34': 1.2, '35-44': 1.2, '45-54': 1.0, '55-64': 0.9, '65+': 0.7},
        'GENDER': {'Female': 1.1, 'Male': 0.9, 'Non-Binary': 1.0},
    },
    'LUXURY_PREMIUM': {
        'AGE': {'18-24': 0.7, '25-34': 1.2, '35-44': 1.4, '45-54': 1.2, '55-64': 0.8, '65+': 0.5},
        'GENDER': {'Female': 1.1, 'Male': 0.9, 'Non-Binary': 1.0},
        'INCOME': {'$200,000+': 1.6, '$150,000-$199,999': 1.4, '$100,000-$149,999': 1.2, '$50,000-$74,999': 0.8, 'Less than $25,000': 0.5},
    },
    'KIDS_FAMILY_ENTERTAINMENT': {
        'AGE': {'18-24': 0.8, '25-34': 1.4, '35-44': 1.5, '45-54': 0.8, '55-64': 0.5, '65+': 0.3},
        'GENDER': {'Female': 1.2, 'Male': 0.8, 'Non-Binary': 0.9},
    },
    'NEWS_POLITICAL': {
        'AGE': {'18-24': 0.5, '25-34': 0.8, '35-44': 1.0, '45-54': 1.3, '55-64': 1.4, '65+': 1.3},
        'GENDER': {'Female': 0.9, 'Male': 1.1, 'Non-Binary': 0.8},
    },
    'FINANCE_BANKING': {
        'AGE': {'18-24': 0.7, '25-34': 1.3, '35-44': 1.3, '45-54': 1.1, '55-64': 0.9, '65+': 0.6},
        'GENDER': {'Female': 0.8, 'Male': 1.2, 'Non-Binary': 0.9},
        'INCOME': {'$200,000+': 1.3, '$150,000-$199,999': 1.2, '$100,000-$149,999': 1.1, 'Less than $25,000': 0.7},
    },
}

# Map brand categories to archetypes
CATEGORY_TO_ARCHETYPE = {
    'ACTOR': 'BROAD_MAINSTREAM',
    'MUSICIAN/BAND': 'BROAD_MAINSTREAM',
    'HOST/PERSONALITY': 'BROAD_MAINSTREAM',
    'ATHLETE': 'SPORTS_MALE',
    'POLITICS/ACTIVIST': 'NEWS_POLITICAL',
    'WRITER/DIRECTOR/AUTHOR/ARTIST': 'BROAD_MAINSTREAM',
    'CREATOR/INFLUENCER': 'YOUNG_FEMALE_POP',
    'QSR': 'FOOD_BEVERAGE',
    'CASUAL DINING': 'FOOD_BEVERAGE',
    'FAST CASUAL': 'FOOD_BEVERAGE',
    'MEDIA': 'BROAD_MAINSTREAM',
    'SOCIAL MEDIA': 'TECH_DIGITAL',
    'TELECOM': 'BROAD_MAINSTREAM',
    'DIGITAL BANKING': 'FINANCE_BANKING',
    'BANKING': 'FINANCE_BANKING',
    'STREAMING/PLATFORM': 'TECH_DIGITAL',
    'STREAMING/MUSIC': 'TECH_DIGITAL',
    'GAMES': 'GAMING_ESPORTS',
    'INSURANCE': 'BROAD_MAINSTREAM',
    'AUTOMOBILE': 'BROAD_MAINSTREAM',
    'TRAVEL': 'BROAD_MAINSTREAM',
    'BETTING': 'SPORTS_MALE',
    'RETAILERS': 'BROAD_MAINSTREAM',
    'GROCERY': 'FAMILY_ORIENTED',
    'APPAREL': 'YOUNG_FEMALE_POP',
    'FOOTWEAR': 'BROAD_MAINSTREAM',
    'BEAUTY': 'BEAUTY_FASHION',
    'BEVERAGE': 'FOOD_BEVERAGE',
    'TOY': 'KIDS_FAMILY_ENTERTAINMENT',
    'PHARMACY': 'BROAD_MAINSTREAM',
    'PHARMA': 'BROAD_MAINSTREAM',
    'PODCAST': 'BROAD_MAINSTREAM',
    'NON PROFIT/CHARITY': 'BROAD_MAINSTREAM',
    'MOVIE THEATER': 'BROAD_MAINSTREAM',
    'AMUSEMENT PARKS': 'FAMILY_ORIENTED',
    'HEAVY MACHINERY': 'YOUNG_MALE_ACTION',
    'COLLEGE/UNIVERSITY': 'TECH_DIGITAL',
    'INVESTMENTS': 'FINANCE_BANKING',
    'CREDIT PROVIDER': 'FINANCE_BANKING',
    'TECHNOLOGY/DEVICE': 'TECH_DIGITAL',
    'SPORTS ORGANIZATIONS': 'SPORTS_MALE',
    'NFL': 'SPORTS_MALE',
    'NBA': 'SPORTS_MALE',
    'MLB': 'SPORTS_MALE',
    'NHL': 'SPORTS_MALE',
    'MLS': 'SPORTS_MALE',
    'WNBA': 'YOUNG_FEMALE_POP',
    'TENNIS': 'BROAD_MAINSTREAM',
    'GOLF': 'OLDER_CONSERVATIVE',
}

# Override archetypes for specific well-known brands
BRAND_ARCHETYPE_OVERRIDES = {
    'TAYLOR SWIFT': 'YOUNG_FEMALE_POP',
    'BEYONCE': 'YOUNG_FEMALE_POP',
    'ARIANA GRANDE': 'YOUNG_FEMALE_POP',
    'BILLIE EILISH': 'YOUNG_FEMALE_POP',
    'OLIVIA RODRIGO': 'YOUNG_FEMALE_POP',
    'SABRINA CARPENTER': 'YOUNG_FEMALE_POP',
    'CHARLI XCX': 'YOUNG_FEMALE_POP',
    'CHAPPELL ROAN': 'YOUNG_FEMALE_POP',
    'SELENA GOMEZ': 'YOUNG_FEMALE_POP',
    'DOJA CAT': 'YOUNG_FEMALE_POP',
    'DUA LIPA': 'YOUNG_FEMALE_POP',
    'SZA': 'HIP_HOP_URBAN',
    'RIHANNA': 'YOUNG_FEMALE_POP',
    'LADY GAGA': 'YOUNG_FEMALE_POP',
    'MEGAN THEE STALLION': 'HIP_HOP_URBAN',
    'CARDI B': 'HIP_HOP_URBAN',
    'LIZZO': 'YOUNG_FEMALE_POP',
    'ADDISON RAE': 'YOUNG_FEMALE_POP',
    'THE ROCK': 'YOUNG_MALE_ACTION',
    'DWAYNE JOHNSON': 'YOUNG_MALE_ACTION',
    'RYAN REYNOLDS': 'BROAD_MAINSTREAM',
    'MARK WAHLBERG': 'YOUNG_MALE_ACTION',
    'KEVIN HART': 'HIP_HOP_URBAN',
    'VIN DIESEL': 'YOUNG_MALE_ACTION',
    'JASON STATHAM': 'YOUNG_MALE_ACTION',
    'JASON MOMOA': 'YOUNG_MALE_ACTION',
    'DAVE CHAPPELLE': 'HIP_HOP_URBAN',
    'DRAKE': 'HIP_HOP_URBAN',
    'KENDRICK LAMAR': 'HIP_HOP_URBAN',
    'TRAVIS SCOTT': 'HIP_HOP_URBAN',
    'KANYE WEST': 'HIP_HOP_URBAN',
    'J. COLE': 'HIP_HOP_URBAN',
    'LIL BABY': 'HIP_HOP_URBAN',
    'FUTURE': 'HIP_HOP_URBAN',
    'METRO BOOMIN': 'HIP_HOP_URBAN',
    'POST MALONE': 'HIP_HOP_URBAN',
    'BAD BUNNY': 'LATINO_CROSSOVER',
    'SHAKIRA': 'LATINO_CROSSOVER',
    'J BALVIN': 'LATINO_CROSSOVER',
    'KAROL G': 'LATINO_CROSSOVER',
    'PESO PLUMA': 'LATINO_CROSSOVER',
    'MORGAN WALLEN': 'COUNTRY_HEARTLAND',
    'LUKE COMBS': 'COUNTRY_HEARTLAND',
    'ZACH BRYAN': 'COUNTRY_HEARTLAND',
    'LUKE BRYAN': 'COUNTRY_HEARTLAND',
    'JASON ALDEAN': 'COUNTRY_HEARTLAND',
    'CHRIS STAPLETON': 'COUNTRY_HEARTLAND',
    'JELLY ROLL': 'COUNTRY_HEARTLAND',
    'METALLICA': 'ROCK_CLASSIC',
    'AC/DC': 'ROCK_CLASSIC',
    'LINKIN PARK': 'ROCK_CLASSIC',
    'FOO FIGHTERS': 'ROCK_CLASSIC',
    'GREEN DAY': 'ROCK_CLASSIC',
    'SEPHORA': 'BEAUTY_FASHION',
    'ULTA': 'BEAUTY_FASHION',
    'FENTY': 'BEAUTY_FASHION',
    'NIKE': 'SPORTS_MALE',
    'UNDER ARMOUR': 'SPORTS_MALE',
    'LEBRON JAMES': 'SPORTS_MALE',
    'STEPHEN CURRY': 'SPORTS_MALE',
    'PATRICK MAHOMES': 'SPORTS_MALE',
    'TOM BRADY': 'SPORTS_MALE',
    'CONOR MCGREGOR': 'SPORTS_MALE',
    'LIONEL MESSI': 'SPORTS_MALE',
    'CRISTIANO RONALDO': 'SPORTS_MALE',
    'SERENA WILLIAMS': 'YOUNG_FEMALE_POP',
    'SIMONE BILES': 'YOUNG_FEMALE_POP',
    'CAITLIN CLARK': 'YOUNG_FEMALE_POP',
    'DONALD TRUMP': 'OLDER_CONSERVATIVE',
    'JOE BIDEN': 'NEWS_POLITICAL',
    'KAMALA HARRIS': 'NEWS_POLITICAL',
    'FOX NEWS': 'OLDER_CONSERVATIVE',
    'CNN': 'NEWS_POLITICAL',
    'MSNBC': 'NEWS_POLITICAL',
    'NETFLIX': 'TECH_DIGITAL',
    'DISNEY+': 'KIDS_FAMILY_ENTERTAINMENT',
    'DISNEY PLUS': 'KIDS_FAMILY_ENTERTAINMENT',
    'HULU': 'TECH_DIGITAL',
    'APPLE TV+': 'TECH_DIGITAL',
    'PEACOCK': 'BROAD_MAINSTREAM',
    'PARAMOUNT+': 'BROAD_MAINSTREAM',
    'ROBLOX': 'GAMING_ESPORTS',
    'FORTNITE': 'GAMING_ESPORTS',
    'MINECRAFT': 'GAMING_ESPORTS',
    'CALL OF DUTY': 'GAMING_ESPORTS',
    'EA SPORTS': 'GAMING_ESPORTS',
    'STARBUCKS': 'FOOD_BEVERAGE',
    'MCDONALDS': 'FOOD_BEVERAGE',
    "MCDONALD'S": 'FOOD_BEVERAGE',
    'CHICK-FIL-A': 'FOOD_BEVERAGE',
    'LOUIS VUITTON': 'LUXURY_PREMIUM',
    'GUCCI': 'LUXURY_PREMIUM',
    'CHANEL': 'LUXURY_PREMIUM',
    'HERMES': 'LUXURY_PREMIUM',
    'APPLE': 'TECH_DIGITAL',
    'GOOGLE': 'TECH_DIGITAL',
    'AMAZON': 'TECH_DIGITAL',
    'WALMART': 'BROAD_MAINSTREAM',
    'TARGET': 'FAMILY_ORIENTED',
    'COSTCO': 'FAMILY_ORIENTED',
}

DEMO_CATEGORIES = ['AGE', 'GENDER', 'ETHNICITY', 'INCOME', 'EDUCATION',
                    'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS', 'OCCUPATION']


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


def get_brand_info(df):
    brand_name = ''
    brand_category = ''
    col = df.columns[0]
    bi_mask = df[col].astype(str).str.strip().str.upper() == 'BRAND INPUT'
    if bi_mask.any():
        raw = str(df.loc[bi_mask].iloc[0, 1])
        brand_name = raw.split(',')[0].strip()
    bc_mask = df[col].astype(str).str.strip().str.upper() == 'BRAND CATEGORY'
    if bc_mask.any():
        brand_category = str(df.loc[bc_mask].iloc[0, 1]).strip()
    return brand_name, brand_category


def filename_to_brand(key):
    fn = key.split('/')[-1].replace('.csv', '')
    fn = re.sub(r'_\d{2}_\d{2}_\d{4}_\d{2}_\d{2}$', '', fn)
    fn = re.sub(r'_\d{2}_\d{2}_\d{4}$', '', fn)
    fn = re.sub(r'_\d{4}_\d{2}_\d{2}_\d{2}_\d{2}$', '', fn)
    fn = re.sub(r'_\d{4}_\d{2}_\d{2}$', '', fn)
    return fn.replace('_', ' ').strip()


def deterministic_jitter(brand_name, category, value, base_mult):
    """Add small deterministic noise to a multiplier so profiles differ slightly."""
    seed_str = f"{brand_name}:{category}:{value}"
    h = int(hashlib.md5(seed_str.encode()).hexdigest()[:8], 16)
    noise = ((h % 1000) / 1000 - 0.5) * 0.1
    return max(0.05, base_mult + noise)


def normalize_demo_label(label):
    """Normalize demographic labels for matching against archetype keys."""
    v = str(label).strip().lower().replace('&', ' ').replace('+', ' ')
    v = re.sub(r'\s+', ' ', v).strip()
    CANON = {
        'black': 'black or african american',
        'black african american': 'black or african american',
        'african american': 'black or african american',
        'latino': 'hispanic or latino',
        'latinx': 'hispanic or latino',
        'hispanic': 'hispanic or latino',
        'other': 'another race/ethnicity',
        'completed hs only': 'high school or less',
        'complete hs only': 'high school or less',
        'completed high school only': 'high school or less',
        'complete college/university': "bachelor's degree",
        'completed college/university': "bachelor's degree",
        'complete grad school': 'graduate or professional degree',
        'completed grad school': 'graduate or professional degree',
        'none': 'prefer not to say',
        'divorced': 'divorced or separated',
    }
    return CANON.get(v, v)


def find_matching_skew_key(archetype_cat_skews, demo_value):
    """Find matching skew key in archetype definition for a demographic value."""
    if not archetype_cat_skews:
        return None
    norm = normalize_demo_label(demo_value)
    for key, mult in archetype_cat_skews.items():
        if normalize_demo_label(key) == norm:
            return key
    return None


def get_archetype(brand_name, brand_category):
    """Determine the archetype for a given brand."""
    norm_brand = normalize_brand(brand_name)
    if norm_brand in BRAND_ARCHETYPE_OVERRIDES:
        return BRAND_ARCHETYPE_OVERRIDES[norm_brand]
    bc_upper = (brand_category or '').strip().upper()
    bc_base = bc_upper.split(' - ')[0].strip() if ' - ' in bc_upper else bc_upper
    if bc_base.startswith('SERIES'):
        return 'BROAD_MAINSTREAM'
    return CATEGORY_TO_ARCHETYPE.get(bc_base, 'BROAD_MAINSTREAM')


def process_file(s3, key, gp_demo, dry_run=True):
    """Process a single profile CSV, applying archetype skews to demographics."""
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
        df = pd.read_csv(io.BytesIO(obj['Body'].read()))
    except Exception as e:
        print(f"  ERROR reading: {e}")
        return False

    brand_name, brand_category = get_brand_info(df)
    filename_brand = filename_to_brand(key)
    display_name = brand_name or filename_brand

    archetype_name = get_archetype(brand_name or filename_brand, brand_category)
    archetype = ARCHETYPE_SKEWS.get(archetype_name, ARCHETYPE_SKEWS['BROAD_MAINSTREAM'])

    print(f"  Brand: {display_name} | Category: {brand_category} | Archetype: {archetype_name}")

    if dry_run:
        return True

    col_name = df.columns[0]
    val_name = df.columns[1]
    bp_col = df.columns[2]

    sample_size = 0
    ss_mask = df[col_name].astype(str).str.strip().str.upper() == 'SAMPLE SIZE'
    if ss_mask.any():
        try:
            sample_size = int(float(str(df.loc[ss_mask].iloc[0, 4]).replace(',', '')))
        except (ValueError, TypeError):
            sample_size = 100000

    skip_cats = {'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE', 'BRAND CATEGORY', 'AVID FAN', 'CASUAL FAN'}
    changed_count = 0

    for cat in DEMO_CATEGORIES:
        cat_mask = df[col_name].astype(str).str.strip().str.upper() == cat
        cat_rows = df.loc[cat_mask]
        if cat_rows.empty:
            continue

        cat_skews = archetype.get(cat, {})
        gp_cat = gp_demo.get(cat, {})

        new_pcts = {}
        for idx, row in cat_rows.iterrows():
            value = str(row[val_name]).strip()
            try:
                current_pct = float(str(row[bp_col]).replace('%', '').strip())
            except (ValueError, TypeError):
                continue

            gp_pct = 0
            for gp_key, gp_val in gp_cat.items():
                if normalize_demo_label(gp_key) == normalize_demo_label(value):
                    gp_pct = gp_val
                    break

            skew_key = find_matching_skew_key(cat_skews, value)
            if skew_key:
                base_mult = cat_skews[skew_key]
            else:
                base_mult = 1.0

            mult = deterministic_jitter(display_name, cat, value, base_mult)

            if gp_pct > 0:
                target = gp_pct * mult
            else:
                target = current_pct * mult

            target = max(0.01, target)
            new_pcts[idx] = target

        if not new_pcts:
            continue

        total = sum(new_pcts.values())
        if total > 0:
            for idx in new_pcts:
                new_pcts[idx] = (new_pcts[idx] / total) * 100.0

        for idx, pct in new_pcts.items():
            pct_str = f"{pct:.4f}%"
            df.at[idx, bp_col] = pct_str

            cs_col = df.columns[3] if len(df.columns) > 3 else None
            if cs_col:
                df.at[idx, cs_col] = round(pct, 4)

            if sample_size > 0:
                raw = round((pct / 100) * sample_size)
                us_proj = round((raw / GENPOP_SAMPLE_CAP) * US_POP)
                if len(df.columns) > 4:
                    df.at[idx, df.columns[4]] = raw
                if len(df.columns) > 5:
                    df.at[idx, df.columns[5]] = us_proj

            changed_count += 1

    if changed_count > 0:
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        buf.seek(0)
        s3.put_object(Bucket=S3_BUCKET, Key=key, Body=buf.getvalue(), ContentType='text/csv')
        print(f"  -> Updated {changed_count} demographic rows")
    return changed_count > 0


def load_genpop_demographics(gp):
    """Extract demographic distributions from Gen Pop CSV."""
    col_name = gp.columns[0]
    val_name = gp.columns[1]
    bp_name = gp.columns[2]

    demo = {}
    for _, row in gp.iterrows():
        cat = str(row[col_name]).strip().upper()
        if cat in DEMO_CATEGORIES:
            val = str(row[val_name]).strip()
            try:
                pct = float(str(row[bp_name]).replace('%', '').strip())
                if cat not in demo:
                    demo[cat] = {}
                demo[cat][val] = pct
            except (ValueError, TypeError):
                pass
    return demo


def main():
    dry_run = '--dry-run' in sys.argv
    print(f"{'DRY RUN' if dry_run else 'LIVE RUN'}: Reindex profiles with archetype-based demographic skews")
    print(f"Bucket: {S3_BUCKET}")
    print()

    s3 = boto3.client('s3', region_name=S3_REGION)

    print(f"Loading Gen Pop baseline: {GEN_POP_KEY} ...")
    obj = s3.get_object(Bucket=S3_BUCKET, Key=GEN_POP_KEY)
    gp = pd.read_csv(io.BytesIO(obj['Body'].read()))
    gp_demo = load_genpop_demographics(gp)
    demo_cats_found = {k: len(v) for k, v in gp_demo.items()}
    print(f"  Gen Pop demographics loaded: {demo_cats_found}")
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
            if process_file(s3, key, gp_demo, dry_run=dry_run):
                changed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            errors += 1

    print()
    print(f"Done. {changed} files {'would be' if dry_run else ''} modified. {errors} errors.")


if __name__ == '__main__':
    main()
