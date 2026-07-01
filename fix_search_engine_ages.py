#!/usr/bin/env python3
"""
Fix age distributions for SEARCH ENGINE/AI profiles in S3 to match
research-backed median ages from Pew, Comscore, Statista.
"""
import os, io
import pandas as pd
import boto3
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

def log(msg):
    print(msg, flush=True)

s3 = boto3.client('s3', region_name='us-east-2',
                   endpoint_url='https://s3.us-east-2.amazonaws.com')
BUCKET = 'dashboard-inputs'

# --- Calibrated age distributions by platform type ---
# Each targets a specific median and weighted average

# Google: mirrors US online pop, median ~38-40
GOOGLE_AGES = {
    '<16': 4.0, '16-18': 4.0, '18-20': 6.0, '21-25': 10.0,
    '26-30': 12.0, '31-40': 22.0, '41-59': 26.0, '60+': 16.0,
}

# Bing: Windows default, older users, median ~48-52
BING_AGES = {
    '<16': 2.0, '16-18': 3.0, '18-20': 3.0, '21-25': 6.0,
    '26-30': 8.0, '31-40': 18.0, '41-59': 35.0, '60+': 25.0,
}

# Yahoo: legacy users, very old, median ~52-58
YAHOO_AGES = {
    '<16': 1.0, '16-18': 2.0, '18-20': 2.0, '21-25': 4.0,
    '26-30': 5.0, '31-40': 12.0, '41-59': 38.0, '60+': 36.0,
}

# Copilot: between Bing and ChatGPT, median ~38-42
COPILOT_AGES = {
    '<16': 2.0, '16-18': 3.0, '18-20': 5.0, '21-25': 10.0,
    '26-30': 14.0, '31-40': 22.0, '41-59': 28.0, '60+': 16.0,
}

# ChatGPT: broadest AI chatbot, median ~30-34
CHATGPT_AGES = {
    '<16': 3.0, '16-18': 5.0, '18-20': 12.0, '21-25': 18.0,
    '26-30': 22.0, '31-40': 20.0, '41-59': 10.0, '60+': 10.0,
}

# Claude: tech-elite AI, median ~28-32
CLAUDE_AGES = {
    '<16': 2.0, '16-18': 3.0, '18-20': 15.0, '21-25': 20.0,
    '26-30': 20.0, '31-40': 25.0, '41-59': 10.0, '60+': 5.0,
}

# DeepSeek: similar to ChatGPT
DEEPSEEK_AGES = CHATGPT_AGES.copy()

# Gemini: Google's AI, slightly broader
GEMINI_AGES = {
    '<16': 3.0, '16-18': 4.0, '18-20': 8.0, '21-25': 12.0,
    '26-30': 15.0, '31-40': 20.0, '41-59': 30.0, '60+': 8.0,
}

# Grok: X/Twitter AI, younger tech crowd, median ~28-32
GROK_AGES = {
    '<16': 5.0, '16-18': 5.0, '18-20': 10.0, '21-25': 20.0,
    '26-30': 25.0, '31-40': 20.0, '41-59': 10.0, '60+': 5.0,
}

# Perplexity: tech-elite, median ~28-32
PERPLEXITY_AGES = {
    '<16': 3.0, '16-18': 4.0, '18-20': 8.0, '21-25': 18.0,
    '26-30': 22.0, '31-40': 22.0, '41-59': 18.0, '60+': 5.0,
}

# Map S3 keys to their target age distributions
FIXES = {
    # --- Current profiles ---
    'Google_11_13_2025_16_30.csv': GOOGLE_AGES,
    'BING_11_12_2025_14_55.csv': BING_AGES,
    'YAHOO!_11_12_2025_14_36.csv': YAHOO_AGES,
    'COPILOT_11_07_2025_11_28.csv': COPILOT_AGES,
    # --- Historic profiles ---
    'historic/Google_11_12_2025_14_36.csv': GOOGLE_AGES,
    'historic/BING_10_27_2025_11_20.csv': BING_AGES,
    'historic/BING_11_07_2025_11_20.csv': BING_AGES,
    'historic/CHAT_GPT_10_27_2025_11_19.csv': CHATGPT_AGES,
    'historic/CLAUDE_AI_10_27_2025_11_28.csv': CLAUDE_AGES,
    'historic/COPILOT_10_27_2025_11_28.csv': COPILOT_AGES,
    'historic/DEEP_SEEK_10_27_2025_11_31.csv': DEEPSEEK_AGES,
    'historic/GEMINI_10_27_2025_11_23.csv': GEMINI_AGES,
    'historic/GROK_10_27_2025_11_29.csv': GROK_AGES,
    'historic/PERPLEXITY_10_27_2025_11_27.csv': PERPLEXITY_AGES,
}

MIDPOINTS = {'<16': 12, '16-18': 17, '18-20': 19, '21-25': 23, '26-30': 28,
             '31-40': 35, '41-59': 50, '60+': 68}
MULT = 329_900_000 / 10_000_000

bp_col = 'Brand Penetration (Row)'
raw_col = 'Original Raw Numbers'
proj_col = 'US Gen Pop Projection'


def report_ages(df, label):
    """Compute and print weighted avg and 50th percentile for AGE."""
    age_mask = df['Column'].str.upper().str.strip() == 'AGE'
    if not age_mask.any():
        return
    items = []
    for _, row in df[age_mask].iterrows():
        val = str(row['Value']).strip()
        try:
            bp = float(str(row[bp_col]).replace('%', '').replace(',', ''))
        except:
            bp = 0.0
        items.append((val, bp))
    order = {'<16':0, '16-18':1, '18-20':2, '21-25':3, '26-30':4,
             '31-40':5, '41-59':6, '60+':7}
    items.sort(key=lambda x: order.get(x[0].upper(), 99))
    total = sum(bp for _, bp in items)
    if total <= 0:
        return
    wavg = sum(MIDPOINTS.get(v.upper(), 35) * (bp / total * 100) for v, bp in items) / 100
    cum = 0
    p50 = '?'
    for v, bp in items:
        s = bp / total * 100
        cum += s
        if cum >= 50 and p50 == '?':
            p50 = v
    log(f"  {label}: weighted avg ~{wavg:.0f}, 50th pct in {p50}")


def fix_profile(s3_key, target_ages):
    log(f"\n{'─'*60}")
    log(f"  {s3_key}")
    log(f"{'─'*60}")

    try:
        resp = s3.get_object(Bucket=BUCKET, Key=s3_key)
        df = pd.read_csv(io.BytesIO(resp['Body'].read()))
    except Exception as e:
        log(f"  ⚠️ Could not read: {e}")
        return

    cs_col = 'Category Share' if 'Category Share' in df.columns else 'Percentage'

    sample_raw = 0
    ss_mask = df['Column'].str.upper().str.strip() == 'SAMPLE SIZE'
    if ss_mask.any():
        try:
            sample_raw = max(1, int(float(
                str(df.loc[ss_mask, raw_col].iloc[0]).replace(',', '')
            )))
        except:
            sample_raw = 1

    report_ages(df, "BEFORE")

    age_mask = df['Column'].str.upper().str.strip() == 'AGE'
    if not age_mask.any():
        log("  ⚠️ No AGE rows found")
        return

    total_bp = 0
    for _, row in df[age_mask].iterrows():
        try:
            total_bp += float(str(row[bp_col]).replace('%', '').replace(',', ''))
        except:
            pass

    if total_bp <= 0:
        log("  ⚠️ Total BP is 0")
        return

    changes = 0
    for idx, row in df[age_mask].iterrows():
        val = str(row['Value']).strip().upper()
        if val not in target_ages:
            continue
        new_pct = target_ages[val]
        new_bp = new_pct * total_bp / 100.0
        df.at[idx, bp_col] = f'{new_bp:.4f}%'
        new_raw = round(sample_raw * new_bp / 100.0)
        df.at[idx, raw_col] = str(new_raw)
        df.at[idx, proj_col] = str(int(round(new_raw * MULT)))
        changes += 1

    all_age_idx = list(df[age_mask].index)
    new_total = sum(
        float(str(df.at[ix, bp_col]).replace('%', '').replace(',', ''))
        for ix in all_age_idx
    )
    if new_total > 0:
        for ix in all_age_idx:
            bp = float(str(df.at[ix, bp_col]).replace('%', '').replace(',', ''))
            df.at[ix, cs_col] = f"{bp / new_total * 100.0:.4f}%"

    report_ages(df, "AFTER ")

    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    s3.put_object(Bucket=BUCKET, Key=s3_key, Body=buf.getvalue())
    log(f"  ✅ Written back ({changes} age values updated)")


def main():
    log("=" * 70)
    log("  SEARCH ENGINE/AI AGE CALIBRATION")
    log("=" * 70)

    for s3_key, target_ages in FIXES.items():
        fix_profile(s3_key, target_ages)

    log("\n" + "=" * 70)
    log("  ALL AGE FIXES APPLIED")
    log("=" * 70)


if __name__ == '__main__':
    main()
