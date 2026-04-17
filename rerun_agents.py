#!/usr/bin/env python3
"""Rerun the 3-step AI agent pipeline on existing profile CSVs.

Self-contained: calls OpenAI directly using the same prompts as bg.py
without importing bg.py (avoids Snowflake/S3 initialization).
"""

import os, sys, json, time, random, math
import concurrent.futures as _futures

from dotenv import load_dotenv
load_dotenv()
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

import pandas as pd
from openai import OpenAI

client = OpenAI(api_key=os.environ['OPENAI_API_KEY'])

# ── Noise helpers (match updated bg.py) ─────────────────────────────

def organic_noise(val):
    """Shift value by ±0.15-0.9 and generate 4 non-zero random decimal digits."""
    pct_shift = random.uniform(0.01, 0.03) * random.choice([-1, 1])
    shift = max(0.15, abs(val * pct_shift)) * (1 if pct_shift > 0 else -1)
    base = val + shift
    base = max(0.2, min(99.8, base))
    integer_part = int(base)
    d1 = random.randint(1, 9)
    d2 = random.randint(1, 9)
    d3 = random.randint(1, 9)
    d4 = random.randint(1, 9)
    v = integer_part + d1 * 0.1 + d2 * 0.01 + d3 * 0.001 + d4 * 0.0001
    return max(0.1111, min(99.8999, round(v, 4)))


def behavioral_noise(val):
    """Same organic noise for behavioral categories."""
    pct_shift = random.uniform(0.005, 0.02) * random.choice([-1, 1])
    shift = max(0.1, abs(val * pct_shift)) * (1 if pct_shift > 0 else -1)
    base = val + shift
    base = max(0.1, min(99.9, base))
    integer_part = int(base)
    d1 = random.randint(1, 9)
    d2 = random.randint(1, 9)
    d3 = random.randint(1, 9)
    d4 = random.randint(1, 9)
    v = integer_part + d1 * 0.1 + d2 * 0.01 + d3 * 0.001 + d4 * 0.0001
    return max(0.1111, min(99.8999, round(v, 4)))


# ── Quality Gate (mirrors bg.py post_agent_quality_gate) ─────────────

_DEMO_SUM_CATEGORIES = {'AGE', 'GENDER', 'ETHNICITY', 'INCOME', 'EDUCATION',
                        'RELATIONSHIP', 'SEXUAL_ORIENTATION', 'PARENTAL_STATUS', 'OCCUPATION'}

_UNIVERSAL_HIGH_BP = {
    'GOOGLE', 'YOUTUBE', 'AMAZON', 'GMAIL', 'FACEBOOK', 'NETFLIX', 'INSTAGRAM',
}

_CLAMP_EXEMPT_CATS = {'BRAND INPUT', 'SAMPLE SIZE', 'INPUT_METADATA', 'BRAND CATEGORY'}


def _has_bad_decimals(val):
    s = f"{val:.4f}"
    if '.' not in s:
        return True
    frac = s.split('.')[1]
    if frac == '0000' or '000' in frac or frac[:2] == '00':
        return True
    return False


def quality_gate(df, persona_doc=None, brands=None):
    """Run all quality validators — matches bg.py post_agent_quality_gate."""
    df = df.copy()
    bp_col = 'Brand Penetration (Row)'
    pct_col = 'Category Share'
    if bp_col not in df.columns:
        return df

    brand_variants = set()
    if brands:
        for b in brands:
            bu = b.strip().upper()
            brand_variants.add(bu)
            brand_variants.add(bu.replace(' ', '-'))
            brand_variants.add(bu.replace(' ', '_'))
            brand_variants.add(bu.replace(' ', ''))

    corrections = 0

    # V1: Demographic + Location sum check (must be exactly 100%)
    sum_categories = _DEMO_SUM_CATEGORIES | {'LOCATION'}
    for demo_cat in sum_categories:
        mask = df['Column'].astype(str).str.strip().str.upper() == demo_cat
        if not mask.any():
            continue
        idxs = df[mask].index.tolist()
        vals = []
        for idx in idxs:
            try:
                vals.append((idx, float(df.at[idx, bp_col])))
            except (ValueError, TypeError):
                continue
        if not vals:
            continue
        total = sum(v for _, v in vals)
        if abs(total - 100.0) > 0.05:
            factor = 100.0 / total if total > 0 else 1.0
            adj_pct = abs(total - 100.0)
            if adj_pct > 5.0:
                print(f"   ⚠️  QG: {demo_cat} sum was {total:.2f}% (off by {adj_pct:.1f}%), normalizing")
            for idx, v in vals:
                new_v = round(v * factor, 4)
                df.at[idx, bp_col] = new_v
                if pct_col in df.columns:
                    df.at[idx, pct_col] = new_v
                corrections += 1

    # V2: BP distribution gate
    all_cats = df['Column'].astype(str).str.strip().str.upper().unique()
    behavioral_cats = [c for c in all_cats
                       if c not in _DEMO_SUM_CATEGORIES
                       and c not in _CLAMP_EXEMPT_CATS
                       and c != 'LOCATION']
    for cat in behavioral_cats:
        mask = df['Column'].astype(str).str.strip().str.upper() == cat
        if not mask.any():
            continue
        idxs = df[mask].index.tolist()
        bp_vals = []
        for idx in idxs:
            try:
                bp_vals.append((idx, float(df.at[idx, bp_col])))
            except (ValueError, TypeError):
                continue
        if not bp_vals:
            continue

        above_50 = [(idx, v) for idx, v in bp_vals if v > 50.0]
        if len(above_50) > 3:
            above_50_sorted = sorted(above_50, key=lambda x: -x[1])
            for idx, v in above_50_sorted[3:]:
                val_name = str(df.at[idx, 'Value']).strip().upper()
                if val_name in _UNIVERSAL_HIGH_BP or val_name in brand_variants:
                    continue
                clamped = behavioral_noise(random.uniform(25.0, 45.0))
                df.at[idx, bp_col] = clamped
                if pct_col in df.columns:
                    df.at[idx, pct_col] = clamped
                corrections += 1
                print(f"   ⚠️  QG: {cat}/{val_name} clamped {v:.2f}→{clamped:.4f} (>3 items above 50%)")

        for idx, v in bp_vals:
            val_name = str(df.at[idx, 'Value']).strip().upper()
            if v > 90.0 and val_name not in _UNIVERSAL_HIGH_BP and val_name not in brand_variants:
                cat_upper = str(df.at[idx, 'Column']).strip().upper()
                if cat_upper in _CLAMP_EXEMPT_CATS:
                    continue
                clamped = behavioral_noise(random.uniform(55.0, 80.0))
                df.at[idx, bp_col] = clamped
                if pct_col in df.columns:
                    df.at[idx, pct_col] = clamped
                corrections += 1
                print(f"   ⚠️  QG: {cat}/{val_name} clamped {v:.2f}→{clamped:.4f} (>90% non-universal)")

    # V3: Location uniqueness
    loc_mask = df['Column'].astype(str).str.strip().str.upper() == 'LOCATION'
    if loc_mask.any():
        loc_idxs = df[loc_mask].index.tolist()
        seen_vals = {}
        for idx in loc_idxs:
            try:
                v = round(float(df.at[idx, bp_col]), 4)
            except (ValueError, TypeError):
                continue
            if v in seen_vals:
                d4 = random.randint(1, 9)
                jitter = d4 * 0.0001 * random.choice([-1, 1])
                new_v = round(max(0.0011, v + jitter), 4)
                while new_v in seen_vals:
                    d4 = random.randint(1, 9)
                    jitter = d4 * 0.0001 * random.choice([-1, 1])
                    new_v = round(max(0.0011, v + jitter), 4)
                df.at[idx, bp_col] = new_v
                if pct_col in df.columns:
                    df.at[idx, pct_col] = new_v
                seen_vals[new_v] = idx
                corrections += 1
            else:
                seen_vals[v] = idx

    # V4: Noise quality check
    skip_cats = {'INPUT_METADATA', 'SAMPLE SIZE'}
    for idx in df.index:
        cat = str(df.at[idx, 'Column']).strip().upper()
        if cat in skip_cats:
            continue
        try:
            v = float(df.at[idx, bp_col])
        except (ValueError, TypeError):
            continue
        if v >= 99.999 or v <= 0.0001:
            continue
        if _has_bad_decimals(v):
            new_v = behavioral_noise(v)
            df.at[idx, bp_col] = new_v
            if pct_col in df.columns:
                df.at[idx, pct_col] = new_v
            corrections += 1

    # Final pass: re-normalize any sums drifted by V3/V4
    for cat in sum_categories:
        mask = df['Column'].astype(str).str.strip().str.upper() == cat
        if not mask.any():
            continue
        idxs = df[mask].index.tolist()
        vals = []
        for idx in idxs:
            try:
                vals.append((idx, float(df.at[idx, bp_col])))
            except (ValueError, TypeError):
                continue
        if not vals:
            continue
        total = sum(v for _, v in vals)
        if abs(total - 100.0) > 0.01:
            factor = 100.0 / total if total > 0 else 1.0
            for idx, v in vals:
                new_v = round(v * factor, 4)
                df.at[idx, bp_col] = new_v
                if pct_col in df.columns:
                    df.at[idx, pct_col] = new_v

    gate_status = "PASS" if corrections == 0 else f"{corrections} auto-corrections"
    print(f"   🛡️  QualityGate: {gate_status}")
    return df


# ── Step 1: Persona Research Agent ───────────────────────────────────

def persona_research_agent(subject, brand_category):
    cat_label = brand_category or "general entertainment"

    prompt = f"""You are a senior audience-research analyst.  Research **{subject}** ({cat_label}) using real, current online data (fan demographics surveys, social-media analytics, press coverage, industry reports).

Return ONLY a single valid JSON object — no markdown, no commentary.

{{
  "persona_summary": "<3-5 sentence description of the audience — lifestyle, interests, values, median age, skew>",
  "demographics": {{
    "AGE": {{
      "17 AND UNDER": <percent>,
      "18-24": <percent>,
      "25-34": <percent>,
      "35-44": <percent>,
      "45-54": <percent>,
      "55-64": <percent>,
      "65 OR OLDER": <percent>
    }},
    "GENDER": {{
      "MALE": <percent>,
      "FEMALE": <percent>,
      "TRANS MALE": <percent>,
      "TRANS FEMALE": <percent>,
      "NON-BINARY": <percent>
    }},
    "ETHNICITY": {{
      "WHITE": <percent>,
      "BLACK OR AFRICAN AMERICAN": <percent>,
      "HISPANIC OR LATINO": <percent>,
      "ASIAN": <percent>,
      "NATIVE AMERICAN / ALASKA NATIVE": <percent>
    }},
    "INCOME": {{
      "UNDER $25,000": <percent>,
      "$25,000-$49,999": <percent>,
      "$50,000-$74,999": <percent>,
      "$75,000-$99,999": <percent>,
      "$100,000-$149,999": <percent>,
      "$150,000-$249,999": <percent>,
      "$250,000 OR MORE": <percent>
    }},
    "EDUCATION": {{
      "LESS THAN HIGH SCHOOL": <percent>,
      "HIGH SCHOOL GRADUATE": <percent>,
      "SOME COLLEGE": <percent>,
      "ASSOCIATE DEGREE": <percent>,
      "BACHELOR'S DEGREE": <percent>,
      "GRADUATE DEGREE": <percent>
    }},
    "RELATIONSHIP": {{
      "SINGLE": <percent>,
      "MARRIED": <percent>,
      "IN A RELATIONSHIP": <percent>,
      "DIVORCED": <percent>,
      "WIDOWED": <percent>,
      "PREFER NOT TO SAY": <percent>
    }},
    "SEXUAL_ORIENTATION": {{
      "STRAIGHT / HETEROSEXUAL": <percent>,
      "GAY OR LESBIAN": <percent>,
      "ANOTHER SEXUAL ORIENTATION": <percent>,
      "PREFER NOT TO SAY": <percent>
    }},
    "PARENTAL_STATUS": {{
      "YES": <percent>,
      "NO": <percent>
    }},
    "OCCUPATION": {{
      "EMPLOYED FULL-TIME": <percent>,
      "EMPLOYED PART-TIME": <percent>,
      "SELF-EMPLOYED": <percent>,
      "STUDENT": <percent>,
      "HOMEMAKER": <percent>,
      "RETIRED": <percent>,
      "UNEMPLOYED": <percent>,
      "PREFER NOT TO SAY": <percent>
    }}
  }},
  "location": [
    {{"dma": "<DMA name, e.g. NEW YORK>", "percentage": <percent>}},
    ...top 15-20 DMAs with highest affinity; remainder auto-distributed
  ],
  "category_guidance": {{
    "SOCIAL MEDIA": "<1-2 sentence guidance>",
    "STREAMING/PLATFORM": "<…>",
    "INTEREST": "<…>",
    "EVENTS": "<…>",
    "BANKING": "<…>",
    "MOST PURCHASED BRANDS": "<…>",
    "WHERE THEY SHOP": "<…>",
    "WHERE THEY DINE": "<…>",
    "QSR": "<…>",
    "AUTOMOBILE": "<…>"
  }}
}}

RULES:
- Each demographic category MUST sum to exactly 100.
- NEVER return round numbers. Every value must have meaningful variation in all 4 decimal places, as if from a real survey. Good: 29.6283, 44.3718, 7.8142, 15.2694. Bad: 30.0000, 45.0000, 8.0000, 15.0000. The integer part should also not be a clean multiple of 5 when possible (use 29.6 instead of 30.0, 44.3 instead of 45.0).
- Trans population ≈ 0.5-1% of US; Native American ≈ 1%; LGBTQ+ ≈ 7% (higher only if brand has known affinity).
- ETHNICITY IS CRITICAL: Research the subject's OWN race/ethnicity/heritage. If the subject is a person of color (Asian, Black, Hispanic, etc.), their fan base will significantly over-index on that ethnicity vs. general US population. For example, a Chinese-American actor's audience should have ASIAN as one of the top ethnicities (30-50%+), not just 7% US average.
- AGE IS CRITICAL: The subject's OWN age heavily influences their audience age distribution. Research the subject's actual age. A 58-year-old actress will have an audience peaking in the 45-54 and 55-64 brackets (30%+ and 20%+ respectively), with much lower percentages for 18-24 (5-8%) and 17 AND UNDER (2-5%). A 20-year-old pop star will peak at 18-24 (35-45%) and 17 AND UNDER (15-25%). The audience's peak age bracket should align with or be slightly younger than the subject's own age bracket. Never give equal weight to age brackets that are 20+ years apart from the subject's age.
- Do NOT include "Prefer Not to Say" or "Other" in AGE, GENDER, ETHNICITY, or INCOME. Those categories must only contain the exact buckets listed above.
- LOCATION: Provide at least 15-20 top DMAs with realistic, varied percentages. The percentages should NOT be clustered — use a natural distribution where the #1 DMA might be 8-12%, #5 might be 4-6%, #10 might be 2-3%, #15 might be 1-2%, #20 might be 0.5-1%. The sum should be ≤ 100; remainder is auto-spread to the other 190+ DMAs with random variation.
- category_guidance: cover every major behavioral category. Be specific about which items should rank high vs low for THIS audience.
"""

    print(f"  Calling gpt-4o-search-preview...")
    text = ''
    try:
        resp = client.chat.completions.create(
            model='gpt-4o-search-preview',
            web_search_options={"search_context_size": "high"},
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=4096,
        )
        text = (resp.choices[0].message.content or '').strip()
        print(f"  Got {len(text)} chars")
    except Exception as e:
        print(f"  search-preview failed ({e}), trying gpt-4o...")
        resp = client.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.4,
            max_tokens=4096,
        )
        text = (resp.choices[0].message.content or '').strip()

    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    start_idx = text.find('{')
    end_idx = text.rfind('}') + 1
    if start_idx < 0 or end_idx <= start_idx:
        raise RuntimeError(f"No JSON found in response: {text[:300]}")
    persona_doc = json.loads(text[start_idx:end_idx])

    demos = persona_doc.get('demographics', {})
    for cat, buckets in demos.items():
        if not isinstance(buckets, dict):
            continue
        total = sum(float(v) for v in buckets.values())
        if total > 0:
            factor = 100.0 / total
            for k in buckets:
                buckets[k] = round(float(buckets[k]) * factor, 4)

    return persona_doc


# ── Step 2: Category Agent ───────────────────────────────────────────

def run_category_agent(category, values, persona_doc, subject):
    guidance = persona_doc.get('category_guidance', {}).get(category, '')
    summary = persona_doc.get('persona_summary', '')
    demo_snapshot = {k: v for k, v in persona_doc.get('demographics', {}).items()
                     if k in ('AGE', 'GENDER', 'ETHNICITY')}
    values_list = '\n'.join(f"  - {v}" for v in values)

    prompt = f"""You are a consumer research analyst setting Brand Penetration (BP) values for the **{category}** category of a behavioral panel profile for **{subject}**.

Brand Penetration = the % of THIS specific audience that engages with each item in a digital clickstream panel. This is NOT popularity, awareness, or favorability. It measures actual observed digital behavior — what fraction of panelists who follow/engage with {subject} ALSO visited, used, streamed, purchased, or clicked on each item during the study period.

PERSONA:
{summary}

KEY DEMOGRAPHICS:
{json.dumps(demo_snapshot, indent=2)}

CATEGORY GUIDANCE:
{guidance}

ITEMS TO SCORE:
{values_list}

Return ONLY a JSON array — no markdown, no commentary:
[
  {{"value": "<ITEM NAME — exact spelling from list above>", "bp": <number>, "reason": "<one sentence>"}},
  …
]

MANDATORY CALIBRATION — THESE ARE HARD CONSTRAINTS:
Think of BP as "what % of this audience had clickstream activity on this item."  Use real-world digital behavior baselines:

TIER 1 (60-85% BP) — ONLY for near-universal digital platforms that almost everyone uses daily:
  Google, YouTube, Amazon, Gmail, Facebook, Netflix, Instagram.  At most 3-5 items in the ENTIRE profile should be in this tier.

TIER 2 (25-55% BP) — Major platforms with strong persona affinity:
  e.g. TikTok for Gen-Z, Spotify for music fans, Hulu for cord-cutters, Target for suburban moms.  5-10 items per category max.

TIER 3 (8-25% BP) — Moderate-affinity brands/platforms. This is where MOST items should land.

TIER 4 (1-8% BP) — Low-affinity or niche items. Many items should be here.

TIER 5 (<1% BP) — Items with virtually no connection to this persona.

DISTRIBUTION REQUIREMENT:
- At least 50% of items MUST be below 15% BP
- At least 25% of items MUST be below 5% BP
- No more than 3 items per category above 50% BP
- No more than 8 items per category above 30% BP

EVENTS/VENUES CALIBRATION:
Even for a celebrity closely associated with film festivals, most of their digital audience does NOT attend those events. Sundance Film Festival might be 5-12% for an indie film actress's audience, not 70%. Comic-Con might be 3-8% for a superhero actor. Music festivals 2-10% for a musician.

BANKING/FINANCIAL CALIBRATION:
Regional credit unions should be 0.5-3% unless there is a specific geographic reason. National banks (Chase, BofA, Wells Fargo) typically 8-20%. Investment platforms (Vanguard, Fidelity) 3-12%.

DECIMAL PRECISION:
Every value MUST have 4 genuinely random-looking decimal places (simulating real panel data). Use varied, organic decimals like 14.3827, 7.0614, 22.9153, 3.4281. Do NOT use sequential patterns like x.1234, x.4321, x.5678 — those look fake.

ADDITIONAL RULES:
- Rank order must make sense for this specific persona
- DIGITAL PANEL: CPG/grocery brands (Coca-Cola, Tide, Oreo) should be LOW (1-10%) — people buy these in stores, not online
- Every item from the list MUST appear in your output
"""

    token_budget = max(4096, len(values) * 80)
    token_budget = min(token_budget, 16384)

    try:
        resp = client.chat.completions.create(
            model='gpt-4o',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.0,
            max_tokens=token_budget,
        )
        text = resp.choices[0].message.content.strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
        start = text.find('[')
        end = text.rfind(']') + 1
        if start >= 0 and end > start:
            text = text[start:end]
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            last_brace = text.rfind('}')
            if last_brace > 0:
                text = text[:last_brace + 1] + ']'
                if not text.startswith('['):
                    text = '[' + text
                result = json.loads(text)
            else:
                raise
        if isinstance(result, list):
            return result
    except Exception as e:
        print(f"    ⚠️ [{category}] failed: {e}")
    return []


# ── Main pipeline ────────────────────────────────────────────────────

_DEMO_SET = {'AGE', 'GENDER', 'ETHNICITY', 'INCOME', 'EDUCATION', 'RELATIONSHIP',
             'SEXUAL_ORIENTATION', 'PARENTAL_STATUS', 'OCCUPATION', 'LOCATION'}
_SKIP = {'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE', 'AVID FAN', 'CASUAL FAN', ''}

EXPECTED_DEMO_BUCKETS = {
    'AGE': ['17 AND UNDER', '18-24', '25-34', '35-44', '45-54', '55-64', '65 OR OLDER'],
    'GENDER': ['FEMALE', 'MALE', 'NON-BINARY', 'TRANS FEMALE', 'TRANS MALE'],
    'ETHNICITY': ['WHITE', 'BLACK OR AFRICAN AMERICAN', 'HISPANIC OR LATINO', 'ASIAN',
                   'NATIVE AMERICAN / ALASKA NATIVE'],
    'INCOME': ['UNDER $25,000', '$25,000-$49,999', '$50,000-$74,999', '$75,000-$99,999',
               '$100,000-$149,999', '$150,000-$249,999', '$250,000 OR MORE'],
}

import re as _re
def _norm(s):
    s = s.strip().upper()
    s = _re.sub(r'\s*-\s*', '-', s)
    return _re.sub(r'\s+', ' ', s)


def reprocess(filepath, subject, brand_category):
    print(f"\n{'='*70}")
    print(f"  REPROCESSING: {subject}")
    print(f"{'='*70}\n")

    df = pd.read_csv(filepath)
    bp_col = 'Brand Penetration (Row)'
    pct_col = 'Category Share'
    print(f"Loaded {len(df)} rows")

    brands = []
    bi_mask = df['Column'].astype(str).str.strip().str.upper() == 'BRAND INPUT'
    if bi_mask.any():
        raw = str(df.loc[bi_mask, 'Value'].iloc[0])
        brands = [b.strip() for b in raw.split(',') if b.strip()][:5]

    start = time.time()

    # Step 1
    print(f"\n🔬 Step 1: Persona Research Agent for '{subject}'")
    persona_doc = persona_research_agent(subject, brand_category)
    demos = persona_doc.get('demographics', {})
    for k in ['ETHNICITY', 'AGE']:
        if k in demos:
            print(f"  {k}: {demos[k]}")

    # Step 2A: Write demographics with organic noise
    print(f"\n📝 Writing demographics from persona...")
    for cat, buckets in demos.items():
        if not isinstance(buckets, dict):
            continue
        cat_mask = df['Column'].astype(str).str.strip().str.upper() == cat.upper()
        for idx in df[cat_mask].index:
            val_u = _norm(str(df.at[idx, 'Value']))
            for bk, pct in buckets.items():
                if _norm(bk) == val_u:
                    noisy = organic_noise(float(pct))
                    df.at[idx, bp_col] = noisy
                    if pct_col in df.columns:
                        df.at[idx, pct_col] = noisy
                    break

    # Step 2B: Write location from persona
    loc_entries = persona_doc.get('location', [])
    if loc_entries:
        loc_mask = df['Column'].astype(str).str.strip().str.upper() == 'LOCATION'
        loc_lookup = {e['dma'].strip().upper(): float(e['percentage'])
                      for e in loc_entries if isinstance(e, dict) and 'dma' in e}
        assigned_total = 0.0
        unmatched = []
        for idx in df[loc_mask].index:
            val_u = str(df.at[idx, 'Value']).strip().upper()
            matched = False
            for dma_key, pct in loc_lookup.items():
                if dma_key in val_u or val_u in dma_key:
                    df.at[idx, bp_col] = round(pct, 4)
                    if pct_col in df.columns:
                        df.at[idx, pct_col] = round(pct, 4)
                    assigned_total += pct
                    matched = True
                    break
            if not matched:
                unmatched.append(idx)
        remainder = max(0.0, 100.0 - assigned_total)
        if unmatched and remainder > 0:
            n_unmatched = len(unmatched)
            weights = [random.uniform(0.3, 1.7) for _ in range(n_unmatched)]
            w_total = sum(weights)
            for i, idx in enumerate(unmatched):
                raw_pct = (weights[i] / w_total) * remainder
                d1 = random.randint(1, 9)
                d2 = random.randint(1, 9)
                frac = d1 * 0.001 + d2 * 0.0001
                noisy_pct = round(max(0.0011, raw_pct + frac * random.choice([-1, 1])), 4)
                df.at[idx, bp_col] = noisy_pct
                if pct_col in df.columns:
                    df.at[idx, pct_col] = noisy_pct

    # Step 2C: Parallel category agents
    all_cats = df['Column'].astype(str).str.strip().str.upper().unique()
    behavioral_cats = [c for c in all_cats if c not in _DEMO_SET and c not in _SKIP]
    category_values = {}
    for cat in behavioral_cats:
        mask = df['Column'].astype(str).str.strip().str.upper() == cat
        vals = df.loc[mask, 'Value'].astype(str).str.strip().str.upper().tolist()
        idxs = df[mask].index.tolist()
        if vals:
            category_values[cat] = (vals, idxs)

    print(f"\n🤖 Step 2C: Launching {len(category_values)} parallel category agents...")
    results_map = {}
    with _futures.ThreadPoolExecutor(max_workers=12) as pool:
        future_to_cat = {
            pool.submit(run_category_agent, cat, vals, persona_doc, subject): cat
            for cat, (vals, _) in category_values.items()
        }
        for fut in _futures.as_completed(future_to_cat):
            cat = future_to_cat[fut]
            try:
                results_map[cat] = fut.result()
                print(f"    ✅ {cat}: {len(results_map[cat])} items")
            except Exception as e:
                print(f"    ⚠️ {cat}: {e}")
                results_map[cat] = []

    # Write results
    rows_written = 0
    for cat, (vals, idxs) in category_values.items():
        agent_result = results_map.get(cat, [])
        if not agent_result:
            continue
        bp_lookup = {}
        for entry in agent_result:
            if isinstance(entry, dict) and 'value' in entry and 'bp' in entry:
                bp_lookup[str(entry['value']).strip().upper()] = float(entry['bp'])
        for idx in idxs:
            val_u = str(df.at[idx, 'Value']).strip().upper()
            if val_u in bp_lookup:
                new_bp = max(0.0001, min(99.9999, bp_lookup[val_u]))
                df.at[idx, bp_col] = behavioral_noise(new_bp)
                rows_written += 1

    print(f"\n  ✅ Wrote BP for {rows_written} behavioral rows")

    # ── Quality Gate ──────────────────────────────────────────────────
    print(f"\n🛡️  Running post-agent quality gate...")
    df = quality_gate(df, persona_doc, brands)

    # Step 3: Reconcile (sample size based)
    print(f"\n🔒 Step 3: Reconcile raw numbers and Category Share...")
    ss_mask = df['Column'].astype(str).str.strip().str.upper() == 'SAMPLE SIZE'
    sample_size = 50000
    if ss_mask.any():
        try:
            sample_size = int(float(str(df.loc[ss_mask, 'Original Raw Numbers'].iloc[0]).replace(',', '')))
        except Exception:
            pass

    for idx in df.index:
        try:
            bp = float(df.at[idx, bp_col])
        except (ValueError, TypeError):
            continue
        raw = max(1, int(round(bp / 100.0 * sample_size)))
        df.at[idx, 'Original Raw Numbers'] = raw
        df.at[idx, 'US Gen Pop Projection'] = int(round(raw * (324770000 / sample_size)))

    # Lock brand input to 100%
    for idx in df.index:
        cat = str(df.at[idx, 'Column']).strip().upper()
        if cat == 'BRAND INPUT':
            df.at[idx, bp_col] = 100.0
            if pct_col in df.columns:
                df.at[idx, pct_col] = 100.0
            df.at[idx, 'Original Raw Numbers'] = sample_size
            df.at[idx, 'US Gen Pop Projection'] = int(round(sample_size * (324770000 / sample_size)))

    # Lock the subject's own name to 100% in EVERY category
    brand_name_u = subject.strip().upper()
    brand_variants = {brand_name_u, brand_name_u.replace(' ', '-'),
                      brand_name_u.replace(' ', '_')}
    for idx in df.index:
        val_u = str(df.at[idx, 'Value']).strip().upper()
        if val_u in brand_variants or brand_name_u in val_u:
            df.at[idx, bp_col] = 100.0
            if pct_col in df.columns:
                df.at[idx, pct_col] = 100.0
            df.at[idx, 'Original Raw Numbers'] = sample_size
            df.at[idx, 'US Gen Pop Projection'] = int(round(sample_size * (324770000 / sample_size)))

    # Final quality gate pass (catch anything introduced by reconciliation)
    print(f"   🛡️  Final quality gate pass...")
    df = quality_gate(df, persona_doc, brands)

    # Re-reconcile after final quality gate
    for idx in df.index:
        try:
            bp = float(df.at[idx, bp_col])
        except (ValueError, TypeError):
            continue
        raw = max(1, int(round(bp / 100.0 * sample_size)))
        df.at[idx, 'Original Raw Numbers'] = raw
        df.at[idx, 'US Gen Pop Projection'] = int(round(raw * (324770000 / sample_size)))

    # Re-lock brand after final reconcile
    for idx in df.index:
        cat = str(df.at[idx, 'Column']).strip().upper()
        val_u = str(df.at[idx, 'Value']).strip().upper()
        if cat == 'BRAND INPUT' or val_u in brand_variants or brand_name_u in val_u:
            df.at[idx, bp_col] = 100.0
            if pct_col in df.columns:
                df.at[idx, pct_col] = 100.0
            df.at[idx, 'Original Raw Numbers'] = sample_size
            df.at[idx, 'US Gen Pop Projection'] = int(round(sample_size * (324770000 / sample_size)))

    elapsed = time.time() - start
    print(f"\n✅ Done in {elapsed:.1f}s")

    out_path = filepath.replace('.csv', '_FIXED.csv')
    df.to_csv(out_path, index=False)
    print(f"💾 Saved: {out_path}")

    # Spot check
    print(f"\n--- Spot check: {subject} ---")
    for cat in ['ETHNICITY', 'AGE', 'EVENTS', 'BANKING', 'SOCIAL MEDIA']:
        mask = df['Column'].astype(str).str.strip().str.upper() == cat
        if mask.any():
            subset = df.loc[mask, ['Value', bp_col]].head(5)
            print(f"\n  {cat} (top 5):")
            for _, row in subset.iterrows():
                print(f"    {row['Value']}: {row[bp_col]}")

    # QA summary
    print(f"\n--- QA Validation: {subject} ---")
    for cat in sorted(_DEMO_SUM_CATEGORIES | {'LOCATION'}):
        mask = df['Column'].astype(str).str.strip().str.upper() == cat
        if not mask.any():
            continue
        vals = []
        for idx in df[mask].index:
            try:
                vals.append(float(df.at[idx, bp_col]))
            except:
                pass
        total = sum(vals)
        status = 'PASS' if abs(total - 100.0) <= 0.05 else f'FAIL ({total:.2f}%)'
        print(f"  Sum {cat}: {total:.4f}% → {status}")

    loc_mask = df['Column'].astype(str).str.strip().str.upper() == 'LOCATION'
    if loc_mask.any():
        loc_bps = df.loc[loc_mask, bp_col].astype(float).round(4)
        dupes = loc_bps.duplicated().sum()
        print(f"  Location: {loc_bps.nunique()} unique / {len(loc_bps)} DMAs, {dupes} duplicates")

    # Check brand at 100%
    for idx in df.index:
        val_u = str(df.at[idx, 'Value']).strip().upper()
        if val_u in brand_variants or brand_name_u in val_u:
            bp_v = float(df.at[idx, bp_col])
            if bp_v != 100.0:
                print(f"  ⚠️ Brand '{val_u}' at {bp_v}% in {df.at[idx, 'Column']} (should be 100%)")
            break
    else:
        print(f"  Brand lock: OK")


# ── Run ──────────────────────────────────────────────────────────────

FILES = [
    ("/Users/jennamenking/Downloads/Sean_Kaufman_04_17_2026_18_19_FIXED.csv", "Sean Kaufman", "ACTOR"),
]

for filepath, subject, brand_category in FILES:
    reprocess(filepath, subject, brand_category)

print(f"\n{'='*70}")
print("  ALL DONE — check _FIXED.csv files in Downloads")
print(f"{'='*70}")
