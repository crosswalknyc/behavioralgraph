#!/usr/bin/env python3
"""
Self-contained test: find all MOVIE THEATER brand-category profiles in S3,
run GPT-4o demographic review on each, print before/after, and
write corrected CSVs back to S3.

No Snowflake dependency — reads/writes S3 directly.
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
        f'(movie theater chain patron and moviegoer base). Report:\n'
        f'- Age distribution (median age, age brackets)\n'
        f'- Gender split (% male vs female)\n'
        f'- Ethnicity / racial composition of moviegoers\n'
        f'- Income level of the audience\n'
        f'- Education level\n'
        f'- Any known data on LGBTQ+ representation\n'
        f'- Relationship / marital status if available\n'
        f'- Parental status if available\n\n'
        f'Cite specific sources (MPA/MPAA Theatrical Market Statistics, Comscore, '
        f'National Research Group, Statista, Nielsen, Morning Consult, YouGov). '
        f'Be concise — key numbers only.'
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


def review_movie_theater_demographics(df, project_name, brands):
    """GPT-4o demographic review for a MOVIE THEATER profile. Returns corrected df."""
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
        "The following is current, web-sourced information about this theater chain's demographics.\n"
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

    prompt = f"""You are a premium-tier US movie theater audience demographics analyst. Determine PRECISE PATRON demographics for this theater chain.

⚠️ CRITICAL RULES:
- EVERY demographic category MUST sum to exactly 100%.
- SEXUAL ORIENTATION: The US LGBTQ+ population is ~7%. Start there as a baseline and only adjust based on evidence from the research data below. AI models consistently over-inflate this — resist that tendency.
- Your job is to reflect REALITY based on available research, not to guess or apply stereotypes.
- KEY CONTEXT: This is a US digital panel measuring who DIGITALLY ENGAGES with movie theater brands (buys tickets online, uses apps, visits websites). This skews slightly younger and more digitally savvy than walk-up audiences.

THEATER CHAIN: "{subject_clean}"
CATEGORY: MOVIE THEATER

=== STEP 1: IDENTIFY THIS THEATER CHAIN ===
What theater chain is this? Determine its type from these subcategories:
- MAJOR NATIONAL CHAIN (AMC, Regal, Cinemark) — broad demographics, massive footprint, mainstream programming. AMC is the largest US chain (~950 theaters), Regal is #2 (~500), Cinemark #3 (~300+).
- PREMIUM/LUXURY (iPic, Alamo Drafthouse, Angelika, Landmark, ArcLight) — higher income, more educated, urban/suburban, 25-54 core, film enthusiast skew
- VALUE/DISCOUNT (Cinépolis, Studio Movie Grill, Showcase, Marcus) — broader income range, family-friendly, suburban
- DRIVE-IN (various) — nostalgia audience, families, couples, broader age range
- INDEPENDENT/ARTHOUSE (IFC Center, Film Forum, Laemmle) — older, highly educated, urban, film cinephile audience

Also note: parent company, geographic footprint (national vs regional), pricing tier, loyalty program (AMC Stubs, Regal Crown Club, etc.), IMAX/premium format availability, and food/beverage offerings.

=== STEP 2: USE THE RESEARCH DATA ===
The REAL-WORLD RESEARCH section below contains web-sourced demographic data from MPA (Motion Picture Association) Theatrical Market Statistics, Comscore, National Research Group (NRG), Statista, Nielsen, and similar authoritative sources. This is your PRIMARY source of truth.

IMPORTANT industry benchmarks for US moviegoers (MPA/MPAA 2023-2025 data):
- Frequent moviegoers (1+/month): skew 18-39 (over-index), diverse, urban/suburban
- Overall moviegoer median age: ~34-38
- Gender: roughly 50/50 male/female for overall theatrical, slight male lean for opening weekends
- Hispanic/Latino audiences are the most frequent moviegoers per capita (~29% of tickets vs ~19% of population)
- Black audiences also over-index relative to population share
- Asian audiences over-index at ~7-8% of tickets vs ~6% of population
- White audiences under-index at ~51-55% of tickets vs ~60% of population
- Education: moviegoers skew slightly more educated than general population
- Income: moviegoers skew slightly higher income (theater is not cheap — $12-20/ticket)
- Premium chains attract higher income, more educated patrons
- Value chains attract broader income distribution, more families

For each demographic category (AGE, GENDER, ETHNICITY, EDUCATION, INCOME, SEXUAL_ORIENTATION, PARENTAL_STATUS, RELATIONSHIP):
1. Check what the research data says about this chain's audience.
2. If the research provides specific numbers (e.g. median age, gender split, racial breakdown), your output MUST match those numbers. Build your distribution around them.
3. If the research provides a MEDIAN AGE, construct the age distribution so the 50th percentile lands at that median. This is non-negotiable.
4. If no research data exists for a particular category, reason from the chain's type, pricing, geography, and market positioning — using the MPA benchmarks above as guardrails.
5. Cross-check: does the overall profile make sense? Premium chains should have higher income and education. National chains should be broadly representative but with the Hispanic/young over-index that MPA data shows. Drive-ins should skew families and couples.

{research_block}
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
            log(f"  🎬 Review: OK — {result.get('notes', '')[:80]}")
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
        log(f"  🎬 Review: FIXED {changes} values — {notes}")
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
    log(f"  MOVIE THEATER: {name}")
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


def find_movie_theater_profiles(limit=50):
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
                log(f"    ... scanned {scanned} files, found {len(found)} MOVIE THEATER so far")
            try:
                resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
                df = pd.read_csv(io.BytesIO(resp['Body'].read()))
                if 'Column' not in df.columns or 'Value' not in df.columns:
                    continue

                bc_mask = df['Column'].str.upper().str.strip() == 'BRAND CATEGORY'
                if bc_mask.any():
                    bc_val = str(df.loc[bc_mask, 'Value'].iloc[0]).strip().upper()
                    if bc_val == 'MOVIE THEATER':
                        name = key.replace('.csv', '').split('/')[-1]
                        found.append({'key': key, 'name': name, 'df': df})
                        log(f"  ✓ [{len(found)}] MOVIE THEATER: {key}")
                        if len(found) >= limit:
                            return found, scanned
            except Exception:
                continue

    return found, scanned


def run():
    log("=" * 70)
    log("  MOVIE THEATER DEMOGRAPHIC REVIEW — S3 TEST")
    log("=" * 70)

    log("\n🔍 Scanning S3 for MOVIE THEATER brand-category profiles...")
    profiles, scanned = find_movie_theater_profiles(limit=50)
    log(f"\n   Scanned {scanned} CSVs — found {len(profiles)} MOVIE THEATER profiles.\n")

    if not profiles:
        log("⚠️  No MOVIE THEATER profiles found. Nothing to test.")
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
        df_fixed = review_movie_theater_demographics(df_orig.copy(), name, brands)
        after = demo_snapshot(df_fixed)
        print_comparison(name, before, after)

        if WRITE_BACK and before != after:
            buf = io.BytesIO()
            df_fixed.to_csv(buf, index=False)
            buf.seek(0)
            s3.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=buf.getvalue())
            log(f"\n  ✅ Written back to S3: {s3_key}")

    log("\n" + "=" * 70)
    log("  ALL MOVIE THEATER PROFILES TESTED")
    log("=" * 70)


if __name__ == '__main__':
    run()
