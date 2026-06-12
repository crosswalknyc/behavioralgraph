"""Super-fan profile synthesis (BG.py pipeline integration).

Per Jenna 2026-06-09 directive: extend BG.py with an interactive prompt
that lets the operator generate a super-fan variant for the subject
based on N digital touchpoints per year. Uses Claude reasoning agents
for sizing and brand classification so the result differs meaningfully
from the 1+ baseline rather than being a flat rescale.

Public API
----------
    prompt_super_fan(subject_label) -> int
        Returns the requested touchpoints-per-year (N >= 2) or 0 if the
        operator declined.

    find_latest_subject_profile_in_s3(subject_label) -> str | None
        Locate the most recent baseline (1+) profile for this subject
        in s3://dashboard-inputs/ so we can skip the pipeline pull.

    synthesize_and_upload_super_fan(source, subject_label, n,
                                     source_kind="s3_key" | "local_path")
        End-to-end orchestrator: load source, Claude-reason, transform,
        run enforcer chain, upload, register in dashboard cache.
        Returns the new s3 key.

Two Claude calls per super-fan run:
    1. Sizing + Demo targets (cohort_fraction, us_pop_fraction, demo
       distribution per category)
    2. Brand classification (CORE / GENRE / ANTI lists, batched across
       all non-demographic categories)

Mechanical jittered lifts then apply per row so every BP gets a unique
deterministic value (no pinning) and lift bands scale with N:
    N >= 365 (daily):   CORE 5.0-7.0x   GENRE 2.5-3.5x   ANTI 0.20-0.35x
    N >= 52  (weekly):  CORE 3.0-4.0x   GENRE 2.0-2.5x   ANTI 0.30-0.45x
    N >= 12  (monthly): CORE 2.0-2.5x   GENRE 1.5-1.8x   ANTI 0.40-0.55x
    N >= 4   (quartly): CORE 1.5-1.8x   GENRE 1.25-1.45x ANTI 0.45-0.65x
    N == 2   (twice):   CORE 1.20-1.35x GENRE 1.10-1.25x ANTI 0.65-0.80x
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

# Late imports for boto3/pandas/anthropic so this module is import-safe
# even on machines without those deps (the orchestrator will fail loudly
# with a clear message when actually invoked).


# =============================================================================
# Lift bands scale with N touchpoints — Jenna directive 2026-06-09
# =============================================================================
def _bands_for_n(n: int) -> dict:
    """Return CORE/GENRE/ANTI/NEUTRAL lift bands keyed by touchpoints/yr."""
    n = max(2, int(n))
    if n >= 365:
        return {
            'CORE':    (5.0,  7.0,  0.40),
            'GENRE':   (2.5,  3.5,  0.30),
            'ANTI':    (0.20, 0.35, 0.10),
            'NEUTRAL': (1.10, 1.18, 0.06),
            'COHORT_DEFAULT': 0.005,
            'US_POP_DEFAULT': 0.012,
            'DEMO_SHARPEN_PP': 12.0,
        }
    if n >= 52:
        return {
            'CORE':    (3.0,  4.0,  0.35),
            'GENRE':   (2.0,  2.5,  0.25),
            'ANTI':    (0.30, 0.45, 0.10),
            'NEUTRAL': (1.08, 1.15, 0.06),
            'COHORT_DEFAULT': 0.025,
            'US_POP_DEFAULT': 0.05,
            'DEMO_SHARPEN_PP': 9.0,
        }
    if n >= 12:
        return {
            'CORE':    (2.0,  2.5,  0.25),
            'GENRE':   (1.5,  1.8,  0.18),
            'ANTI':    (0.40, 0.55, 0.10),
            'NEUTRAL': (1.06, 1.12, 0.05),
            'COHORT_DEFAULT': 0.06,
            'US_POP_DEFAULT': 0.10,
            'DEMO_SHARPEN_PP': 6.0,
        }
    if n >= 4:
        return {
            'CORE':    (1.50, 1.80, 0.20),
            'GENRE':   (1.25, 1.45, 0.15),
            'ANTI':    (0.45, 0.65, 0.12),
            'NEUTRAL': (1.04, 1.09, 0.04),
            'COHORT_DEFAULT': 0.15,
            'US_POP_DEFAULT': 0.18,
            'DEMO_SHARPEN_PP': 4.0,
        }
    return {  # n == 2 or 3
        'CORE':    (1.20, 1.35, 0.10),
        'GENRE':   (1.10, 1.22, 0.08),
        'ANTI':    (0.65, 0.80, 0.10),
        'NEUTRAL': (1.02, 1.05, 0.02),
        'COHORT_DEFAULT': 0.40,
        'US_POP_DEFAULT': 0.45,
        'DEMO_SHARPEN_PP': 2.0,
    }


# =============================================================================
# Operator-facing prompt
# =============================================================================
def prompt_super_fan(subject_label: str) -> int:
    """Ask the operator whether to generate a super-fan profile.

    Returns the touchpoints-per-year integer (>= 2) the operator wants,
    or 0 if they declined / aborted. Non-interactive callers (tests, CI)
    can set SUPER_FAN_AUTOPILOT=N to skip the prompt.
    """
    autopilot = (os.environ.get('SUPER_FAN_AUTOPILOT') or '').strip()
    if autopilot.isdigit():
        n = int(autopilot)
        if n >= 2:
            print(f"\n[super-fan] AUTOPILOT={n} (skipping interactive prompt)")
            return n
        return 0

    print()
    print("=" * 72)
    print(f"  SUPER-FAN PROFILE PROMPT for {subject_label}")
    print("=" * 72)
    print("  A super-fan profile slices the 1+ engagement panel down to")
    print("  people who engage N+ times per year with this subject.")
    print("  Sample shrinks, BP affinity sharpens, demos cluster.")
    print("    e.g.  4 = quarterly fan        (Reba 4+, Rock 4+)")
    print("         12 = monthly fan")
    print("         52 = weekly fan")
    print("        365 = daily fan / hardcore loyalist")
    print()
    try:
        ans = input("  Generate a super-fan profile? (Y/N) [N]: ").strip().upper()
    except EOFError:
        return 0
    if ans not in ('Y', 'YES'):
        print("  -> skipping super-fan synthesis.")
        return 0

    while True:
        try:
            raw = input("  How many digital touchpoints per year? [4]: ").strip()
        except EOFError:
            return 0
        if not raw:
            return 4
        if raw.isdigit() and int(raw) >= 2:
            return int(raw)
        print("    please enter an integer >= 2")


# =============================================================================
# Subject lookup — find latest 1+ baseline in S3
# =============================================================================
def _norm_subject_for_filename(subject_label: str) -> str:
    """Convert 'Jason Momoa' -> 'Jason_Momoa' (matches BG.py convention)."""
    s = re.sub(r"[^A-Za-z0-9]+", '_', subject_label.strip())
    s = re.sub(r'_+', '_', s).strip('_')
    return s


def find_latest_subject_profile_in_s3(subject_label: str) -> Optional[str]:
    """Find the most recent baseline (1+) CSV for this subject in
    s3://dashboard-inputs/. Returns the S3 key or None."""
    try:
        import boto3
    except ImportError:
        return None
    s3 = boto3.client('s3', region_name='us-east-2')
    prefix = _norm_subject_for_filename(subject_label) + '_'
    candidates = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket='dashboard-inputs', Prefix=prefix):
        for obj in page.get('Contents', []) or []:
            k = obj['Key']
            if not k.endswith('.csv'):
                continue
            if '/' in k or k.startswith('_'):
                continue
            # Skip super-fan variants — we only want 1+ baselines
            if 'Plus' in k or '4Plus' in k or '_4plus' in k.lower():
                continue
            if 'Super_Fan' in k or 'SuperFan' in k:
                continue
            candidates.append((k, obj['LastModified']))
    if not candidates:
        return None
    candidates.sort(key=lambda kv: kv[1], reverse=True)
    return candidates[0][0]


# =============================================================================
# Source-profile snapshot for Claude prompts
# =============================================================================
def _fbp(v):
    try:
        return float(str(v).replace('%', '').replace(',', '').strip())
    except Exception:
        return None


def build_source_snapshot(df) -> dict:
    """Compact snapshot of the source profile for Claude reasoning.

    Includes: subject label, sample size, AVID/CASUAL FAN BPs, demo
    distribution per category, and the top 12 brands per non-demo
    category by BP. Compact enough to fit comfortably in a Claude
    context window."""
    cats_upper = df['Column'].astype(str).str.strip().str.upper()
    bi = df[cats_upper == 'BRAND INPUT']
    subject = str(bi['Value'].iloc[0]).strip() if len(bi) else ''
    ss = df[cats_upper == 'SAMPLE SIZE']
    sample_size = None
    if len(ss):
        # Standard convention: raw sample count lives in Original Raw
        # Numbers. (For pre-D116 corrupted files where CS held the count
        # instead, the BG.py save-gate already would have run by now.)
        try:
            raw = ss.iloc[0].get('Original Raw Numbers')
            if raw is not None:
                sample_size = float(str(raw).replace(',', '').strip())
        except Exception:
            pass
        if not sample_size or sample_size < 1000:
            cs = _fbp(ss.iloc[0].get('Category Share'))
            if cs and cs > 1000:
                sample_size = cs

    avid = df[cats_upper == 'AVID FAN']
    casual = df[cats_upper == 'CASUAL FAN']
    avid_bp = _fbp(avid.iloc[0]['Brand Penetration (Row)']) if len(avid) else None
    casual_bp = _fbp(casual.iloc[0]['Brand Penetration (Row)']) if len(casual) else None

    DEMO_CATS = {'GENDER', 'AGE', 'ETHNICITY', 'EDUCATION', 'INCOME',
                 'OCCUPATION', 'PARENTAL_STATUS', 'RELATIONSHIP',
                 'SEXUAL_ORIENTATION'}
    META_CATS = {'BRAND INPUT', 'SAMPLE SIZE', 'INPUT_METADATA',
                 'BRAND CATEGORY', 'AVID FAN', 'CASUAL FAN'}

    demos = {}
    brand_blocks = {}
    for cat, grp in df.groupby(cats_upper):
        if cat in META_CATS or cat == '':
            continue
        rows = []
        for _, r in grp.iterrows():
            bp = _fbp(r['Brand Penetration (Row)'])
            if bp is None:
                continue
            rows.append((str(r['Value']).strip(), bp))
        if not rows:
            continue
        rows.sort(key=lambda kv: -kv[1])
        if cat in DEMO_CATS:
            demos[cat] = rows
        else:
            brand_blocks[cat] = rows[:12]

    return {
        'subject': subject,
        'sample_size': sample_size,
        'avid_fan_bp': avid_bp,
        'casual_fan_bp': casual_bp,
        'demos': demos,
        'top_brands_per_category': brand_blocks,
        'category_count': len(brand_blocks),
    }


# =============================================================================
# Claude reasoning agents
# =============================================================================
_SIZING_SYSTEM = """You are an audience-analytics reasoning agent. Given a
1+ engagement panel for a public-figure subject, you reason about how that
panel compresses when filtered to N+ digital touchpoints per year (where N
is provided), and how the audience's demographic distribution sharpens.

Your job has two outputs:
 1. SIZING — what fraction of the 1+ panel would re-engage at N+
    touchpoints/year, and what fraction of US adults that represents.
 2. DEMO TARGETS — for each demographic category, the BP each bucket
    should sit at within the N+ super-fan slice (each category sums to
    100%).

Reasoning principles (CRITICAL):
 - Cohort fraction is NOT a fixed multiplier. It depends on the subject's
   audience structure. Subjects with large passive cultural awareness
   (Oprah, Taylor Swift) have SMALLER super-fan fractions because most
   of the 1+ panel is passive. Subjects with concentrated active fan
   bases (heartthrobs, niche cult figures) have LARGER super-fan
   fractions because most engagers are already active.
 - Demos sharpen at higher engagement: dominant buckets pick up
   percentage points, minority buckets lose them. The amount of
   sharpening scales with N.
 - Use the AVID FAN and CASUAL FAN signals as anchors: AVID is roughly
   the ceiling for super-fan slice, but a true 4+ super-fan is typically
   60-80% of AVID for broad-reach subjects, higher for concentrated
   fanbases.
 - Output STRICT JSON only — no commentary outside the JSON."""

_CLASSIFY_SYSTEM = """You are a brand-affinity reasoning agent. Given a
public-figure subject and a flat list of (category, brand) pairs from
their 1+ panel, you classify each as one of:

  CORE   — directly tied to the subject's own works/businesses/identity
  GENRE  — peers, collaborators, in-genre brands the subject's audience
           heavily overlaps with (but not the subject's own things)
  ANTI   — cultural anti-affinity (this audience would explicitly index
           LOW on these brands; the inverse cultural register)
  NEUTRAL — default; mild positive lift only (super-fans engage slightly
            more across the board even on unrelated brands)

You output ONLY the CORE, GENRE, and ANTI lists — anything not listed is
NEUTRAL by default. Use the exact "CATEGORY/Brand Name" format from the
input. Do NOT invent brands that aren't in the input list. Output strict
JSON only."""


def _format_snapshot_for_sizing(snap: dict, n: int) -> str:
    lines = [f"SUBJECT: {snap['subject']}",
             f"REQUESTED TOUCHPOINTS/YEAR: {n}+",
             f"1+ SAMPLE SIZE: {snap['sample_size']}",
             f"AVID FAN BP: {snap.get('avid_fan_bp')}%",
             f"CASUAL FAN BP: {snap.get('casual_fan_bp')}%",
             "",
             "DEMOGRAPHIC DISTRIBUTION (1+ panel):"]
    for cat, rows in snap['demos'].items():
        lines.append(f"  {cat}:")
        for label, bp in rows:
            lines.append(f"    {label}: {bp:.2f}%")
    lines.append("")
    lines.append("TOP-3 BRANDS PER NON-DEMO CATEGORY (signal only):")
    for cat, rows in list(snap['top_brands_per_category'].items())[:30]:
        top = rows[:3]
        s = '; '.join(f'{lbl}={bp:.1f}%' for lbl, bp in top)
        lines.append(f"  {cat}: {s}")
    if len(snap['top_brands_per_category']) > 30:
        lines.append(f"  ... and {len(snap['top_brands_per_category']) - 30} more categories")
    lines.append("")
    lines.append("Output JSON of the form:")
    lines.append("""{
  "cohort_fraction": 0.18,
  "us_pop_fraction": 0.13,
  "reasoning": "1-3 sentences explaining your sizing call",
  "demo_targets": {
    "GENDER": {"FEMALE": 64.0, "MALE": 33.5, ...},
    "AGE": {"35-44": 26.0, ...},
    "ETHNICITY": {...},
    "INCOME": {...},
    "EDUCATION": {...},
    "OCCUPATION": {...},
    "RELATIONSHIP": {...},
    "PARENTAL_STATUS": {...},
    "SEXUAL_ORIENTATION": {...}
  }
}""")
    lines.append("Each demo_targets category MUST sum to 100.0 (within 0.5pp)")
    lines.append("Use the EXACT bucket labels shown above.")
    return '\n'.join(lines)


def _format_snapshot_for_classify(snap: dict, n: int) -> str:
    lines = [f"SUBJECT: {snap['subject']}",
             f"FILTER: {n}+ touchpoints per year (super-fan slice)",
             "",
             "Below is every brand the 1+ panel showed affinity for, "
             "grouped by category. Classify each into CORE / GENRE / "
             "ANTI as defined in the system prompt. Anything you don't "
             "list is NEUTRAL.",
             ""]
    for cat, rows in snap['top_brands_per_category'].items():
        for label, bp in rows:
            lines.append(f"  {cat}/{label}")
    lines.append("")
    lines.append("Output JSON exactly of the form:")
    lines.append("""{
  "core": ["CATEGORY/Brand Name", ...],
  "genre": ["CATEGORY/Brand Name", ...],
  "anti": ["CATEGORY/Brand Name", ...]
}""")
    lines.append("Use the exact strings from the input list. Do not invent.")
    return '\n'.join(lines)


def _extract_json_block(text: str) -> Optional[dict]:
    """Pull the first {...} JSON block from Claude's response."""
    if not text:
        return None
    text = text.strip()
    if text.startswith('```'):
        # strip code fence
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```\s*$', '', text)
    # find outermost {...}
    start = text.find('{')
    if start < 0:
        return None
    depth = 0
    end = -1
    for i, ch in enumerate(text[start:], start=start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end <= start:
        return None
    try:
        return json.loads(text[start:end])
    except Exception:
        return None


def reason_super_fan(snap: dict, n: int, *, fallback_only: bool = False) -> dict:
    """Run the two Claude reasoning calls and return a single
    structured reasoning dict. If Claude is unavailable or returns
    invalid JSON, fall back to deterministic defaults from
    `_bands_for_n(n)` (so the synthesis never hard-fails)."""
    bands = _bands_for_n(n)
    fallback = {
        'cohort_fraction': bands['COHORT_DEFAULT'],
        'us_pop_fraction': bands['US_POP_DEFAULT'],
        'demo_targets': {cat: {label: bp for label, bp in rows}
                          for cat, rows in snap['demos'].items()},
        'core_set': set(),
        'genre_set': set(),
        'anti_set': set(),
        'reasoning': f'fallback: deterministic defaults for N={n}+',
        'claude_used': False,
    }

    if fallback_only:
        return fallback

    try:
        from claude_client import claude_messages
    except Exception as _imp_err:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from claude_client import claude_messages  # type: ignore
        except Exception:
            print(f"[super-fan] Claude client import failed: {_imp_err}")
            return fallback

    # Call 1: sizing + demo targets
    sizing_user = _format_snapshot_for_sizing(snap, n)
    try:
        resp1 = claude_messages(
            system=_SIZING_SYSTEM, user=sizing_user,
            max_tokens=4096, temperature=0.4,
        )
    except Exception as e:
        print(f"[super-fan] Claude sizing call failed: {e}")
        return fallback
    sizing_obj = _extract_json_block(resp1) if resp1 else None

    # Call 2: brand classification
    classify_user = _format_snapshot_for_classify(snap, n)
    try:
        resp2 = claude_messages(
            system=_CLASSIFY_SYSTEM, user=classify_user,
            max_tokens=8192, temperature=0.3,
        )
    except Exception as e:
        print(f"[super-fan] Claude classify call failed: {e}")
        resp2 = ''
    classify_obj = _extract_json_block(resp2) if resp2 else None

    out = dict(fallback)
    if isinstance(sizing_obj, dict):
        cf = sizing_obj.get('cohort_fraction')
        uf = sizing_obj.get('us_pop_fraction')
        if isinstance(cf, (int, float)) and 0.0001 < cf < 0.95:
            out['cohort_fraction'] = float(cf)
        if isinstance(uf, (int, float)) and 0.0001 < uf < 0.95:
            out['us_pop_fraction'] = float(uf)
        dt = sizing_obj.get('demo_targets')
        if isinstance(dt, dict) and dt:
            cleaned = {}
            for cat, buckets in dt.items():
                if not isinstance(buckets, dict):
                    continue
                clean = {str(k).strip(): float(v) for k, v in buckets.items()
                          if isinstance(v, (int, float))}
                if clean:
                    cleaned[str(cat).strip().upper()] = clean
            if cleaned:
                out['demo_targets'] = cleaned
        if isinstance(sizing_obj.get('reasoning'), str):
            out['reasoning'] = sizing_obj['reasoning']
        out['claude_used'] = True

    if isinstance(classify_obj, dict):
        for src_key, dst_key in (('core', 'core_set'),
                                  ('genre', 'genre_set'),
                                  ('anti', 'anti_set')):
            lst = classify_obj.get(src_key) or []
            if isinstance(lst, list):
                # We pass either "CATEGORY/Brand" or just "Brand" — accept
                # both, normalize to brand-only for matching since the
                # transform engine matches across all categories.
                brands = set()
                for s in lst:
                    if not isinstance(s, str):
                        continue
                    s = s.strip()
                    if '/' in s:
                        s = s.split('/', 1)[1].strip()
                    if s:
                        brands.add(s.upper())
                out[dst_key] = brands

    return out


# =============================================================================
# Transform engine — applies reasoning to the source df row by row
# =============================================================================
DEMO_CATS_TF = {'GENDER', 'AGE', 'ETHNICITY', 'EDUCATION', 'INCOME',
                'OCCUPATION', 'PARENTAL_STATUS', 'RELATIONSHIP',
                'SEXUAL_ORIENTATION'}
META_CATS_TF = {'BRAND INPUT', 'SUBJECT', 'SAMPLE SIZE', 'INPUT_METADATA',
                'BRAND CATEGORY'}
SUBJECT_PIN_CATS_TF = {'BRAND INPUT', 'SUBJECT', 'TALENT', 'ACTOR',
                        'MUSICIAN/BAND', 'HOST/PERSONALITY', 'COMEDIAN',
                        'AUTHOR', 'DIRECTOR', 'PRODUCER',
                        'CREATOR/INFLUENCER', 'ATHLETE', 'NBA ATHLETE',
                        'NFL ATHLETE', 'MLB ATHLETE', 'NHL ATHLETE',
                        'WNBA ATHLETE'}


def _seed_jitter(seed: str, span: float) -> float:
    h = hashlib.md5(seed.encode()).hexdigest()
    u = int(h[:8], 16) / 0xFFFFFFFF
    return -span / 2 + span * u


def _norm_brand_tf(s: str) -> str:
    s = str(s).upper()
    s = re.sub(r"[^A-Z0-9& +]", '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _matches_tf(value: str, candidate: str) -> bool:
    v_norm = _norm_brand_tf(value)
    c_norm = _norm_brand_tf(candidate)
    if v_norm == c_norm:
        return True
    v_toks = set(t for t in v_norm.split() if t)
    c_toks = set(t for t in c_norm.split() if t)
    return bool(c_toks) and c_toks.issubset(v_toks)


def classify_tier(value: str, core_set, genre_set, anti_set) -> str:
    for s in core_set:
        if _matches_tf(value, s):
            return 'CORE'
    for s in genre_set:
        if _matches_tf(value, s):
            return 'GENRE'
    for s in anti_set:
        if _matches_tf(value, s):
            return 'ANTI'
    return 'NEUTRAL'


def lift_for_tier(tier: str, salt: str, n: int) -> float:
    bands = _bands_for_n(n)
    lo, hi, span = bands.get(tier, bands['NEUTRAL'])
    base = (lo + hi) / 2.0
    return base + _seed_jitter(f"{tier}|{salt}|n{n}", span=span)


def _is_seq_digit_bp_local(bp: float) -> bool:
    """Defer to the canonical enforcer detector to stay in sync with
    the gate. Falls back to a local heuristic if import fails."""
    try:
        from post_generation_enforcers import _is_sequential_digit_bp
        return bool(_is_sequential_digit_bp(bp))
    except Exception:
        pass
    if bp is None or bp <= 0 or bp >= 100:
        return False
    decimals = f'{bp:.4f}'.split('.')[1]
    digits = [int(c) for c in decimals]
    if all(digits[i+1] - digits[i] == 1 for i in range(3)):
        return True
    if all(digits[i+1] - digits[i] == -1 for i in range(3)):
        return True
    return False


def _break_sequential(bp: float, salt: str) -> float:
    if not _is_seq_digit_bp_local(bp):
        return bp
    h = hashlib.md5(f'break|{salt}|{bp:.4f}'.encode()).hexdigest()
    direction = 1 if int(h[:2], 16) % 2 == 0 else -1
    for offset in (0.0037, -0.0073, 0.0119, -0.0151, 0.0211):
        nudged = round(bp + direction * offset, 4)
        if not _is_seq_digit_bp_local(nudged):
            return nudged
        direction = -direction
    return round(bp + 0.0317, 4)


def _soft_cap(bp: float, ceiling: float = 95.5,
              span: float = 2.0, salt: str = '') -> float:
    if bp <= ceiling:
        return bp
    j = _seed_jitter(f'soft_cap|{salt}|{bp:.4f}', span=span)
    return min(99.49, ceiling + j)


def synthesize_super_fan(df, reasoning: dict, subject_label: str,
                         n: int, new_subject_label: str):
    """Apply the reasoning to df. Returns a new (transformed) DataFrame.

    Steps mirror scripts/build_super_fan_profiles.py but use the
    Claude-reasoned cohort_fraction / us_pop_fraction / demo_targets /
    brand classifiers instead of hand-coded ones, and lift bands scale
    with N."""
    import pandas as pd

    df = df.copy()
    cf = float(reasoning['cohort_fraction'])
    uf = float(reasoning['us_pop_fraction'])
    demo_targets = reasoning.get('demo_targets') or {}
    core_set = reasoning.get('core_set') or set()
    genre_set = reasoning.get('genre_set') or set()
    anti_set = reasoning.get('anti_set') or set()

    cats_upper = df['Column'].astype(str).str.strip().str.upper()
    bp_col = 'Brand Penetration (Row)'
    raw_col = 'Original Raw Numbers'
    proj_col = 'US Gen Pop Projection'
    cs_col = 'Category Share'

    # Discover baseline sample + us_pop from any 99.95+ pin
    df['_BP'] = df[bp_col].apply(_fbp)
    df['_RAW'] = pd.to_numeric(df[raw_col].astype(str).str.replace(',', ''),
                                errors='coerce')
    df['_PROJ'] = pd.to_numeric(df[proj_col].astype(str).str.replace(',', ''),
                                 errors='coerce')
    pin = df[df['_BP'] >= 99.95]
    base_sample = int(pin['_RAW'].max() or 0)
    base_us_pop = int(pin['_PROJ'].max() or 0)
    df = df.drop(columns=['_BP', '_RAW', '_PROJ'])
    new_sample = max(1, int(round(base_sample * cf)))
    new_us_pop = max(1, int(round(base_us_pop * uf)))
    print(f"  sample: {base_sample:,} -> {new_sample:,} "
          f"({cf*100:.2f}%)")
    print(f"  us_pop: {base_us_pop:,} -> {new_us_pop:,} "
          f"({uf*100:.2f}%)")

    subject_norm = _norm_brand_tf(subject_label)

    # Step 1 — sharpen demos to Claude's targets (renormalize per cat to 100%)
    demo_changes = 0
    for cat in demo_targets.keys():
        mask = cats_upper == cat
        if not mask.any():
            continue
        target_lookup = {str(k).upper(): float(v)
                          for k, v in demo_targets[cat].items()}
        rows = []
        for idx in df.index[mask]:
            label = str(df.at[idx, 'Value']).strip()
            target_bp = target_lookup.get(label.upper())
            if target_bp is None:
                # fuzzy: ignore punctuation
                norm_label = re.sub(r'[^A-Z0-9$]', '', label.upper())
                for k, v in target_lookup.items():
                    if re.sub(r'[^A-Z0-9$]', '', k) == norm_label:
                        target_bp = v
                        break
            if target_bp is None:
                # Claude didn't return this bucket — keep current
                target_bp = _fbp(df.at[idx, bp_col]) or 0.5
            target_bp += _seed_jitter(f'{cat}|{label}|sharpen|n{n}',
                                       span=0.15)
            rows.append((idx, max(0.05, target_bp)))
        total = sum(v for _, v in rows)
        if total <= 0:
            continue
        for idx, v in rows:
            normed = round(v * 100.0 / total, 4)
            df.at[idx, bp_col] = f'{normed:.4f}%'
            demo_changes += 1
    print(f"  demos sharpened (Claude targets, renormalized to 100%): "
          f"{demo_changes} rows")

    # Step 2 — lift brand blocks by tier (jittered)
    brand_changes = 0
    tier_counts = {'CORE': 0, 'GENRE': 0, 'ANTI': 0, 'NEUTRAL': 0}
    for cat, grp in df.groupby(cats_upper):
        if cat in META_CATS_TF or cat in DEMO_CATS_TF or cat == '':
            continue
        for idx in grp.index:
            value = str(df.at[idx, 'Value']).strip()
            cur_bp = _fbp(df.at[idx, bp_col])
            if cur_bp is None:
                continue
            # Subject self-pin
            if (_norm_brand_tf(value) == subject_norm
                    and cat in SUBJECT_PIN_CATS_TF):
                new_bp = 100.0
            else:
                tier = classify_tier(value, core_set, genre_set, anti_set)
                tier_counts[tier] = tier_counts.get(tier, 0) + 1
                mult = lift_for_tier(tier, f'{cat}|{value}', n)
                new_bp = cur_bp * mult
                # Jittered floor
                floor_jit = 0.05 + abs(_seed_jitter(
                    f'floor|{cat}|{value}|n{n}', span=0.05))
                new_bp = max(floor_jit, new_bp)
                new_bp = _soft_cap(new_bp, ceiling=95.5, span=2.0,
                                    salt=f'{cat}|{value}|n{n}')
                new_bp = round(new_bp, 4)
                new_bp = _break_sequential(new_bp, f'{cat}|{value}|n{n}')
            df.at[idx, bp_col] = f'{new_bp:.4f}%'
            df.at[idx, raw_col] = int(round(new_sample * new_bp / 100))
            df.at[idx, proj_col] = int(round(new_us_pop * new_bp / 100))
            brand_changes += 1
    tcs = ' '.join(f'{t}={n_}' for t, n_ in tier_counts.items())
    print(f"  brand-block lifts: {brand_changes} rows  ({tcs})")

    # Step 3 — recompute Raw + Proj across the file with new sample/us_pop
    rp = 0
    for idx in df.index:
        cat = str(df.at[idx, 'Column']).upper().strip()
        if cat in {'SAMPLE SIZE', 'INPUT_METADATA'}:
            continue
        bp = _fbp(df.at[idx, bp_col])
        if bp is None:
            continue
        df.at[idx, raw_col] = int(round(new_sample * bp / 100))
        df.at[idx, proj_col] = int(round(new_us_pop * bp / 100))
        rp += 1
    print(f"  recomputed Raw+Proj: {rp} rows")

    # Step 4 — update SAMPLE SIZE + BRAND INPUT rows
    ss_mask = cats_upper == 'SAMPLE SIZE'
    for idx in df.index[ss_mask]:
        df.at[idx, raw_col] = new_sample
        df.at[idx, proj_col] = new_us_pop
    bi_mask = cats_upper == 'BRAND INPUT'
    for idx in df.index[bi_mask]:
        df.at[idx, 'Value'] = new_subject_label
        df.at[idx, bp_col] = '100.0000%'
        df.at[idx, cs_col] = '100.0000%'
        df.at[idx, raw_col] = new_sample
        df.at[idx, proj_col] = new_us_pop

    # Step 5 — recompute Category Share for every non-meta block
    for cat, grp in df.groupby(cats_upper):
        if cat in META_CATS_TF or cat == '':
            continue
        bps = [_fbp(df.at[i, bp_col]) for i in grp.index]
        bps_clean = [b for b in bps if b is not None]
        total = sum(bps_clean)
        if total <= 0:
            continue
        for i, b in zip(grp.index, bps):
            if b is None:
                continue
            cs = round(b * 100.0 / total, 4)
            df.at[i, cs_col] = f'{cs:.4f}%'

    return df, {
        'new_sample': new_sample,
        'new_us_pop': new_us_pop,
        'cohort_fraction': cf,
        'us_pop_fraction': uf,
        'tier_counts': tier_counts,
        'demo_changes': demo_changes,
        'brand_changes': brand_changes,
    }


# =============================================================================
# Top-level orchestrator
# =============================================================================
def _load_source(source: str, source_kind: str = 'auto'):
    """Load a source dataframe from S3 key or local path."""
    import pandas as pd
    kind = source_kind
    if kind == 'auto':
        kind = 's3_key' if (not os.path.isabs(source)
                             and not source.startswith('./')
                             and not os.path.exists(source)) else 'local_path'
    if kind == 's3_key':
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        obj = s3.get_object(Bucket='dashboard-inputs', Key=source)
        df = pd.read_csv(io.BytesIO(obj['Body'].read()), low_memory=False)
        return df
    return pd.read_csv(source, low_memory=False)


def _run_enforcer_chain(df, subject_label: str):
    """Apply the full canonical enforcer chain so the synthesized
    profile passes the same gates as a fresh pipeline run."""
    try:
        from post_generation_enforcers import (
            apply_strip_tilde_from_brand_input,
            apply_db_canonical_normalize,
            apply_bp_cs_consistency_recovery,
            dejitter_sequential_placeholders,
            dejitter_within_cat_4dp_collisions,
            depin_round_brand_bps,
            apply_recompute_category_share,
            renormalize_demographics_to_100,
            run_pre_publish_gate,
        )
    except Exception as e:
        print(f"[super-fan] enforcer chain import failed: {e}")
        return df, []

    df, _ = apply_strip_tilde_from_brand_input(df, subject_label, verbose=False)
    df, _ = apply_db_canonical_normalize(df, subject_label, verbose=False)
    df, _ = apply_bp_cs_consistency_recovery(df, subject_label, verbose=False)
    df, _ = dejitter_sequential_placeholders(df, subject_label, verbose=False)
    df, _ = dejitter_within_cat_4dp_collisions(df, subject_label, verbose=False)
    df, _ = depin_round_brand_bps(df, subject_label, verbose=False)
    df, _ = apply_recompute_category_share(df, subject_label, verbose=False)
    df, _ = renormalize_demographics_to_100(df, subject=subject_label,
                                              tolerance=0.5, verbose=False)
    defects = run_pre_publish_gate(df, subject_label,
                                    project_name=subject_label,
                                    raise_on_fail=False, verbose=False)
    return df, defects


def synthesize_and_upload_super_fan(source: str, subject_label: str,
                                     n_touchpoints: int,
                                     *, source_kind: str = 'auto',
                                     register_in_dashboard: bool = True
                                     ) -> dict:
    """End-to-end orchestrator. Returns a result dict with the new s3
    key, the reasoning, and the enforcer defect count."""
    if n_touchpoints < 2:
        raise ValueError("n_touchpoints must be >= 2 for a super-fan")

    print(f"\n{'=' * 72}")
    print(f"  SUPER-FAN SYNTHESIS for {subject_label} @ {n_touchpoints}+ touchpoints/yr")
    print(f"{'=' * 72}")
    print(f"  source: {source} ({source_kind})")

    df = _load_source(source, source_kind=source_kind)
    snap = build_source_snapshot(df)
    print(f"  snapshot: subject={snap['subject']!r} sample={snap['sample_size']} "
          f"avid={snap['avid_fan_bp']} casual={snap['casual_fan_bp']} "
          f"cats={snap['category_count']}")

    print(f"  -> Claude reasoning ...")
    reasoning = reason_super_fan(snap, n_touchpoints)
    print(f"  cohort_fraction: {reasoning['cohort_fraction']:.4f}")
    print(f"  us_pop_fraction: {reasoning['us_pop_fraction']:.4f}")
    print(f"  CORE={len(reasoning['core_set'])} GENRE={len(reasoning['genre_set'])} "
          f"ANTI={len(reasoning['anti_set'])}")
    print(f"  reasoning: {reasoning.get('reasoning', '')[:200]}")
    if not reasoning.get('claude_used'):
        print(f"  ⚠ Claude unavailable — used deterministic fallback")

    new_label = f"{subject_label.upper().strip()} {n_touchpoints}+"

    df, summary = synthesize_super_fan(df, reasoning, subject_label,
                                        n_touchpoints, new_label)

    df, defects = _run_enforcer_chain(df, new_label)
    print(f"  pre-publish gate: {len(defects)} defect(s)")
    for d in defects[:5]:
        print(f"    {d}")

    # Upload
    import boto3
    s3 = boto3.client('s3', region_name='us-east-2')
    ts = datetime.now(timezone.utc).strftime('%m_%d_%Y_%H_%M')
    out_key = (f"{_norm_subject_for_filename(subject_label)}_"
               f"{n_touchpoints}Plus_{ts}.csv")
    out_buf = io.StringIO()
    df.to_csv(out_buf, index=False)
    s3.put_object(
        Bucket='dashboard-inputs', Key=out_key,
        Body=out_buf.getvalue().encode('utf-8'),
        ContentType='text/csv',
        Metadata={
            'super-fan-synth': f'n{n_touchpoints}_cohort_{reasoning["cohort_fraction"]:.4f}',
            'cohort-fraction': f'{reasoning["cohort_fraction"]:.4f}',
            'us-pop-fraction': f'{reasoning["us_pop_fraction"]:.4f}',
            'claude-reasoning-used': '1' if reasoning.get('claude_used') else '0',
            'source-of-truth': source if source_kind != 'local_path'
                                else os.path.basename(source),
            'pre-publish-defects': str(len(defects)),
            'fixed-on': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        },
        CacheControl='no-cache, max-age=0',
    )
    print(f"  ✓ uploaded s3://dashboard-inputs/{out_key}")

    if register_in_dashboard:
        try:
            _register_in_dashboard(out_key, subject_label, n_touchpoints, source)
            print(f"  ✓ registered in dashboard cache + quick_selects")
        except Exception as e:
            print(f"  ⚠ dashboard registration skipped: {e}")

    return {
        'out_key': out_key,
        'reasoning': reasoning,
        'defects': defects,
        'summary': summary,
    }


def _register_in_dashboard(out_key: str, subject_label: str,
                            n: int, source: str):
    """Register the new super-fan profile in s3_cache.json AND in the
    correct quick-selects file (`metadata/admin_quick_selects.json` —
    NOT the orphan `system/quick_selects.json`) so it appears in the
    Select Profile dropdown immediately. Inherits image and imdb_id
    from the source profile when available.

    Delegates to the shared `migration.dashboard_register` helper which
    is the single source of truth for this registration logic across
    all profile-builder scripts (avid-fan, super-fan, skins, etc.).
    """
    from .dashboard_register import register_profile_in_dashboard
    src_key = source if source.endswith('.csv') else f"{source}.csv"
    return register_profile_in_dashboard(
        out_key,
        display_name=f"{subject_label} {n}+",
        source_key=src_key,
    )


__all__ = [
    'prompt_super_fan',
    'find_latest_subject_profile_in_s3',
    'build_source_snapshot',
    'reason_super_fan',
    'synthesize_super_fan',
    'synthesize_and_upload_super_fan',
]

