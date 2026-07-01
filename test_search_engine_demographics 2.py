#!/usr/bin/env python3
"""
Self-contained test: find all SEARCH ENGINE/AI brand-category profiles in S3,
run GPT-4o demographic review on each, print before/after, fix ages if needed,
and write corrected CSVs back to S3.
"""

import os, io, json, sys
import pandas as pd
import boto3
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

def log(msg):
    print(msg, flush=True)

S3_BUCKET = 'dashboard-inputs'
S3_REGION = 'us-east-2'

DEMO_CATS = ['AGE', 'GENDER', 'ETHNICITY', 'EDUCATION', 'INCOME',
             'SEXUAL_ORIENTATION', 'PARENTAL_STATUS', 'RELATIONSHIP']

WRITE_BACK = True

client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))
s3 = boto3.client('s3', region_name=S3_REGION,
                   endpoint_url=f'https://s3.{S3_REGION}.amazonaws.com')

_research_cache = {}


def research_brand(subject):
    if subject in _research_cache:
        return _research_cache[subject]
    clean = subject.replace('_', ' ').replace('-', ' ').strip()
    prompt = (
        f'Search the web for current demographic data about "{clean}" '
        f'(search engine and AI platform user base). Report:\n'
        f'- Age distribution (median age, age brackets)\n'
        f'- Gender split (% male vs female)\n'
        f'- Ethnicity / racial composition\n'
        f'- Income level of users\n'
        f'- Education level\n'
        f'- Any known data on LGBTQ+ representation\n'
        f'- Relationship / marital status if available\n'
        f'- Parental status if available\n\n'
        f'Cite specific sources (Pew Research, Comscore, Statista, eMarketer, '
        f'SimilarWeb, YouGov, Morning Consult). Be concise — key numbers only.'
    )
    try:
        resp = client.chat.completions.create(
            model='gpt-4o-search-preview',
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=800,
        )
        text = (resp.choices[0].message.content or '').strip()
        _research_cache[subject] = text
        if text:
            log(f"  🔍 Web research: {len(text)} chars")
        return text
    except Exception as e:
        log(f"  ⚠️  Web research failed: {e}")
        _research_cache[subject] = ""
        return ""


def review_demographics(df, project_name, brands):
    bp_col = 'Brand Penetration (Row)'
    raw_col = 'Original Raw Numbers'
    proj_col = 'US Gen Pop Projection'
    cs_col = 'Category Share' if 'Category Share' in df.columns else 'Percentage'
    MULT = 329_900_000 / 10_000_000

    if bp_col not in df.columns:
        return df

    df = df.copy()
    subject = project_name or (brands[0] if brands else 'Unknown')
    subject_clean = subject.replace('_', ' ').replace('-', ' ').strip()

    web_research = research_brand(subject_clean)
    research_block = (
        "\n=== REAL-WORLD RESEARCH (from web search) ===\n"
        "The following is current, web-sourced information about this platform's demographics.\n"
        "Use this as your PRIMARY reference. Only deviate if the data clearly conflicts\n"
        "with well-established facts.\n\n"
        f"{web_research}\n"
    ) if web_research else ""

    sample_raw = 0
    ss_mask = df['Column'].str.upper().str.strip() == 'SAMPLE SIZE'
    if ss_mask.any():
        try:
            sample_raw = max(1, int(float(
                str(df.loc[ss_mask, raw_col].iloc[0]).replace(',', '')
            )))
        except (ValueError, TypeError):
            sample_raw = 1

    all_shares = {}
    all_indices = {}
    for cat in DEMO_CATS:
        mask = df['Column'].str.upper().str.strip() == cat
        if not mask.any():
            continue
        items = []
        for idx, row in df[mask].iterrows():
            val = str(row.get('Value', '')).strip()
            try:
                bp = float(str(row.get(bp_col, 0)).replace('%', '').replace(',', ''))
            except (ValueError, TypeError):
                bp = 0.0
            items.append((val, bp, idx))
        total = sum(bp for _, bp, _ in items)
        if total <= 0:
            continue
        shares = {val: round(bp / total * 100, 2) for val, bp, _ in items}
        all_shares[cat] = shares
        all_indices[cat] = items

    if not all_shares:
        return df

    demo_block = ""
    key_block = ""
    for cat in DEMO_CATS:
        if cat in all_shares:
            demo_block += f"- {cat}: {json.dumps(all_shares[cat])}\n"
            key_block += f"- {cat} values: {json.dumps(list(all_shares[cat].keys()))}\n"

    prompt = f"""You are a premium-tier US search engine and AI platform audience demographics analyst. Determine PRECISE USER demographics for this search engine or AI platform.

⚠️ CRITICAL RULES:
- EVERY demographic category MUST sum to exactly 100%.
- SEXUAL ORIENTATION: The US LGBTQ+ population is ~7%. Start there as a baseline and only adjust based on evidence from the research data below. AI models consistently over-inflate this — resist that tendency.
- Your job is to reflect REALITY based on available research, not to guess or apply stereotypes.
- KEY CONTEXT: This is a US digital panel measuring who DIGITALLY ENGAGES with search engines and AI tools.

PLATFORM: "{subject_clean}"
CATEGORY: SEARCH ENGINE/AI

=== STEP 1: IDENTIFY THIS PLATFORM ===
What search engine or AI platform is this? Determine its type:
- DOMINANT SEARCH ENGINE (Google) — ~90% US market share, demographics mirror online US population. Median user age ~38-40. Gender ~50/50.
- SECONDARY SEARCH ENGINE (Bing, Yahoo Search, DuckDuckGo) — Bing skews older (median ~48-52, default on Windows). DuckDuckGo skews younger (median ~32-36), male (60-65%), tech-savvy.
- AI CHATBOT (ChatGPT, Gemini, Claude, Copilot, Perplexity) — skews younger (18-44 core), male (55-65%), educated, higher income, early adopter.
- VOICE ASSISTANT (Siri, Alexa, Google Assistant) — Siri = iPhone owners (higher income, younger). Alexa = family/home. Google Assistant = broader Android.

=== STEP 2: USE THE RESEARCH DATA ===
{research_block}

IMPORTANT benchmarks:
- Google Search: median ~38-40, ~50/50 gender, ethnicity close to US census, broad income/education
- Bing: median ~48-52, slightly male, older Windows users, higher income (enterprise)
- Yahoo Search: median ~52-58, legacy users, older
- DuckDuckGo: median ~32-36, male 60-65%, highly educated, higher income, privacy-focused
- ChatGPT: median ~30-34, male 55-60%, college-educated, higher income, ~40% of 18-29 used it (Pew 2024)
- Perplexity: median ~28-32, male 65-70%, very educated, high income, tech-elite
- Gemini/Bard: similar to ChatGPT, male 55-60%, younger
- Copilot: between Bing and ChatGPT demographics, median ~38-42, male 60%

For each demographic category:
1. Check research data first — match specific numbers if provided.
2. If MEDIAN AGE is given, the 50th percentile MUST land at that age.
3. Cross-check: Google = mirrors America. AI tools = young/male/educated. Bing = older.

=== STEP 3: EVALUATE ===
{demo_block}

=== STEP 4: VERDICT ===
{key_block}
If accurate: {{"status":"OK","notes":"reason"}}
If corrections needed: {{"status":"FIX","notes":"what's wrong","corrections":{{"CAT":{{"label":num,...}},...}}}}
Each corrected category sums to 100. JSON only, no markdown."""

    try:
        resp = client.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.05,
            max_tokens=2500,
        )
        text = resp.choices[0].message.content.strip()

        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        depth = 0
        end = 0
        for i, c in enumerate(text):
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > 0:
            text = text[:end]

        result = json.loads(text)

        if result.get('status') != 'FIX' or 'corrections' not in result:
            log(f"  🔍 Review: OK — {result.get('notes', '')[:80]}")
            return df

        corr = result['corrections']
        changes = 0

        for cat_name, new_shares in corr.items():
            cat_upper = cat_name.upper()
            if not isinstance(new_shares, dict) or cat_upper not in all_indices:
                continue
            items = all_indices[cat_upper]
            total_bp = sum(bp for _, bp, _ in items)
            if total_bp <= 0:
                continue
            idx_map = {val.upper(): idx for val, bp, idx in items}
            if not any(l.strip().upper() in idx_map for l in new_shares):
                continue
            for label, new_pct in new_shares.items():
                key = label.strip().upper()
                if key not in idx_map:
                    continue
                idx = idx_map[key]
                new_bp = float(new_pct) * total_bp / 100.0
                df.at[idx, bp_col] = f'{new_bp:.4f}%'
                new_raw = round(sample_raw * new_bp / 100.0)
                df.at[idx, raw_col] = str(new_raw)
                df.at[idx, proj_col] = str(int(round(new_raw * MULT)))
                changes += 1

            all_idx = [idx for _, _, idx in items]
            new_total = sum(
                float(str(df.at[ix, bp_col]).replace('%', '').replace(',', ''))
                for ix in all_idx
            )
            if new_total > 0:
                for ix in all_idx:
                    bp = float(str(df.at[ix, bp_col]).replace('%', '').replace(',', ''))
                    df.at[ix, cs_col] = f"{bp / new_total * 100.0:.4f}%"

        notes = result.get('notes', '')[:80]
        log(f"  🔍 Review: FIXED {changes} values — {notes}")
        return df

    except Exception as e:
        log(f"  ⚠️  Review error: {e}")
        return df


def demo_snapshot(df):
    bp_col = 'Brand Penetration (Row)'
    snap = {}
    for cat in DEMO_CATS:
        mask = df['Column'].str.upper().str.strip() == cat
        if not mask.any():
            continue
        items = []
        for _, row in df[mask].iterrows():
            val = str(row.get('Value', '')).strip()
            try:
                bp = float(str(row.get(bp_col, 0)).replace('%', '').replace(',', ''))
            except (ValueError, TypeError):
                bp = 0.0
            items.append((val, bp))
        total = sum(bp for _, bp in items)
        if total > 0:
            snap[cat] = {v: round(bp / total * 100, 1) for v, bp in items}
    return snap


def print_comparison(name, before, after):
    log(f"\n{'='*70}")
    log(f"  SEARCH ENGINE/AI: {name}")
    log(f"{'='*70}")
    any_change = False
    for cat in DEMO_CATS:
        b = before.get(cat, {})
        a = after.get(cat, {})
        if not b and not a:
            continue
        changed = b != a
        if changed:
            any_change = True
        marker = " ← CHANGED" if changed else ""
        log(f"\n  {cat}{marker}")
        all_keys = list(dict.fromkeys(list(b.keys()) + list(a.keys())))
        for k in all_keys:
            bv = b.get(k, '-')
            av = a.get(k, '-')
            flag = " *" if bv != av else ""
            log(f"    {k:<30} {str(bv):>6}%  →  {str(av):>6}%{flag}")
    if not any_change:
        log("\n  ✅ No changes — GPT-4o deemed demographics accurate.")


def check_ages(df, name):
    """Print age verification for the profile."""
    bp_col = 'Brand Penetration (Row)'
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
    order = {'<16':0, '17 AND UNDER':0, '16-18':1, '18-20':2, '18-24':2.5, '21-25':3,
             '25-34':3.5, '26-30':4, '31-40':5, '35-44':5.5, '41-59':6, '45-54':6.5,
             '55-64':7, '60+':8, '65 OR OLDER':8, '65+':8}
    items.sort(key=lambda x: order.get(x[0].upper(), 99))
    total = sum(bp for _, bp in items)
    if total <= 0:
        return
    mp = {'<16':12, '16-18':17, '18-20':19, '21-25':23, '26-30':28, '31-40':35,
          '41-59':50, '60+':68, '17 AND UNDER':12, '18-24':21, '25-34':30, '35-44':40,
          '45-54':50, '55-64':60, '65 OR OLDER':70, '65+':70}
    wavg = sum(mp.get(v.upper(), 35) * (bp / total * 100) for v, bp in items) / 100
    cum = 0
    p50 = '?'
    for v, bp in items:
        s = bp / total * 100
        cum += s
        if cum >= 50 and p50 == '?':
            p50 = v
    log(f"  📊 Age check: weighted avg ~{wavg:.0f}, 50th pct in {p50}")


def find_profiles(limit=50):
    paginator = s3.get_paginator('list_objects_v2')
    found = []
    scanned = 0

    for page in paginator.paginate(Bucket=S3_BUCKET):
        for obj in page.get('Contents', []):
            key = obj['Key']
            if not key.endswith('.csv'):
                continue
            if key.startswith(('purgatory/', 'system/', 'metadata/')):
                continue
            if 'Gen_Pop' in key:
                continue

            scanned += 1
            if scanned % 50 == 0:
                log(f"    ... scanned {scanned} files, found {len(found)} SEARCH ENGINE/AI so far")
            try:
                resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
                df = pd.read_csv(io.BytesIO(resp['Body'].read()))
                if 'Column' not in df.columns or 'Value' not in df.columns:
                    continue

                bc_mask = df['Column'].str.upper().str.strip() == 'BRAND CATEGORY'
                if bc_mask.any():
                    bc_val = str(df.loc[bc_mask, 'Value'].iloc[0]).strip().upper()
                    if 'SEARCH ENGINE' in bc_val:
                        name = key.replace('.csv', '').split('/')[-1]
                        found.append({'key': key, 'name': name, 'df': df})
                        log(f"  ✓ [{len(found)}] {bc_val}: {key}")
                        if len(found) >= limit:
                            return found, scanned
            except Exception:
                continue

    return found, scanned


def run():
    log("=" * 70)
    log("  SEARCH ENGINE/AI DEMOGRAPHIC REVIEW — S3 TEST")
    log("=" * 70)

    log("\n🔍 Scanning S3 for SEARCH ENGINE/AI brand-category profiles...")
    profiles, scanned = find_profiles(limit=50)
    log(f"\n   Scanned {scanned} CSVs — found {len(profiles)} SEARCH ENGINE/AI profiles.\n")

    if not profiles:
        log("⚠️  No SEARCH ENGINE/AI profiles found. Nothing to test.")
        return

    for i, p in enumerate(profiles, 1):
        name = p['name']
        s3_key = p['key']
        df_orig = p['df'].copy()
        before = demo_snapshot(df_orig)

        log(f"\n{'─'*60}")
        log(f"▶ [{i}/{len(profiles)}] {name}")
        log(f"  S3: {s3_key}")
        log(f"{'─'*60}")

        brands = [name.split('_')[0]]
        df_fixed = review_demographics(df_orig.copy(), name, brands)
        after = demo_snapshot(df_fixed)
        print_comparison(name, before, after)
        check_ages(df_fixed, name)

        if WRITE_BACK and before != after:
            buf = io.BytesIO()
            df_fixed.to_csv(buf, index=False)
            buf.seek(0)
            s3.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=buf.getvalue())
            log(f"\n  ✅ Written back to S3: {s3_key}")

    log("\n" + "=" * 70)
    log("  ALL SEARCH ENGINE/AI PROFILES TESTED")
    log("=" * 70)


if __name__ == '__main__':
    run()
