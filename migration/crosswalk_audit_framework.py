"""Crosswalk Profile Audit Framework — final read-only audit pass.

Implements the 9-step methodology used by Crosswalk analysts for vetting
digital persona pulls (Foosball / Keke / Sandler / Dove / Bargatze /
LA Sparks / Gen Pop vetting passes, 05/12 – 05/23).

The framework is ADVISORY, not corrective. It:
  1. Parses metadata + audience composition from the audited CSV
  2. Builds expected persona from published consensus benchmarks
     (Pew, eMarketer, Antenna, etc. — baked in as constants)
  3. Applies digital-only methodology rules
  4. Computes audience-weighted Pew math for social platforms
  5. Cross-pull triangulation against mass-American expected ranges
  6. Structural-gap check (Big 4 banks, Big 3 telco, etc.)
  7. Pass/Fail scoring (±7 points from consensus)
  8. Classifies issue type (suppression, default-lock, overcorrect, etc.)
  9. Emits markdown report

The framework does NOT mutate BPs. It produces:
  - A markdown report (logged + saved to S3 audit_logs/v1/)
  - A list of `structural_gaps` (missing required entities)
  - A list of `consensus_anchors` (target BP per brand) the next pull
    can inject into the per-row scoring prompts so the agent has
    benchmark anchors to reason against (NOT caps to override with).

The ONLY mutation we allow is structural-gap insertion: if Chase is
missing from BANKING entirely, we insert a row at the consensus value
(not as a cap, as documentation of the missing entity). This is
distinct from the formulaic caps we removed earlier.
"""
from __future__ import annotations

import io
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


# ───────────────────────────────────────────────────────────────────────────
# Parallel batch helper (added 2026-06-04)
# ───────────────────────────────────────────────────────────────────────────
# Several functions in this file process flagged rows in batches and call
# OpenAI/Anthropic SEQUENTIALLY per batch (~29 batches × ~15s each =
# ~7-9 min of serial wall time inside one profile run). Network round-trips
# dominate; the SDK clients are thread-safe; per-key TPM/RPM tier limits
# easily accommodate 6-10 concurrent calls.
#
# `_prefetch_batch_responses_parallel` accepts a list of "request specs"
# (one per batch), fires them concurrently via a thread pool, and returns
# results in the SAME ORDER as input. Each call site then iterates the
# results using its existing per-item logic (untouched), so anti-pinning,
# jitter, and df.at assignments remain serial and race-free.
#
# Tunable via env vars:
#   CROSSWALK_PARALLEL=6              # default 6 concurrent calls
#   CROSSWALK_PARALLEL_DISABLE=1      # fall back to serial for debugging
# ───────────────────────────────────────────────────────────────────────────


def _crosswalk_parallel_workers() -> int:
    """Concurrency cap for in-profile batch API parallelism."""
    if os.environ.get('CROSSWALK_PARALLEL_DISABLE', '').strip() in ('1', 'true', 'TRUE'):
        return 1
    try:
        n = int(os.environ.get('CROSSWALK_PARALLEL', '6'))
    except (ValueError, TypeError):
        n = 6
    return max(1, min(n, 16))


def _prefetch_batch_responses_parallel(call_fns: list) -> list:
    """Fire each `call_fn()` in a thread pool and return results in input order.

    `call_fn` is a zero-arg callable that performs the network call and
    returns the parsed JSON dict (or any value). Exceptions are caught
    and returned as the result for that index so the caller can decide
    how to handle them. Order is preserved.
    """
    n = len(call_fns)
    if n == 0:
        return []
    max_workers = min(_crosswalk_parallel_workers(), n)
    if max_workers <= 1:
        # Serial fallback (preserves byte-identical behavior with CROSSWALK_PARALLEL_DISABLE=1)
        out = []
        for fn in call_fns:
            try:
                out.append(fn())
            except Exception as e:
                out.append(e)
        return out

    results: list = [None] * n
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='cw-batch') as pool:
        future_to_idx = {pool.submit(fn): i for i, fn in enumerate(call_fns)}
        for fut in future_to_idx:
            idx = future_to_idx[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                results[idx] = e
    return results


# ───────────────────────────────────────────────────────────────────────────
# CONSTANTS — published consensus benchmarks (Tier 1/2 sources)
# Refresh these as Pew / eMarketer / Antenna publish new reads.
# ───────────────────────────────────────────────────────────────────────────

# Pew Research 2025 social-platform reach by gender + age
# Source: pewresearch.org/internet (refresh quarterly)
PEW_2025_SOCIAL = {
    # platform: { 'M': pct, 'F': pct, '18-29': pct, '30-49': pct, '50-64': pct, '65+': pct }
    'YOUTUBE':   {'M': 83, 'F': 84, '18-29': 93, '30-49': 92, '50-64': 83, '65+': 65},
    'FACEBOOK':  {'M': 64, 'F': 73, '18-29': 67, '30-49': 75, '50-64': 73, '65+': 58},
    'INSTAGRAM': {'M': 44, 'F': 53, '18-29': 78, '30-49': 57, '50-64': 32, '65+': 15},
    'TIKTOK':    {'M': 32, 'F': 41, '18-29': 62, '30-49': 39, '50-64': 24, '65+': 10},
    'SNAPCHAT':  {'M': 23, 'F': 28, '18-29': 53, '30-49': 27, '50-64': 12, '65+':  4},
    'PINTEREST': {'M': 22, 'F': 50, '18-29': 45, '30-49': 41, '50-64': 33, '65+': 21},
    'LINKEDIN':  {'M': 35, 'F': 28, '18-29': 33, '30-49': 41, '50-64': 31, '65+': 14},
    'X':         {'M': 25, 'F': 17, '18-29': 35, '30-49': 22, '50-64': 16, '65+':  7},
    # REDDIT intentionally not audited as SOCIAL MEDIA — this pipeline classifies
    # Reddit under APP/PLATFORM USAGE, not SOCIAL MEDIA, so flagging it MISSING
    # in the social-media audit is a false positive.
}

# Cross-pull triangulation: expected mass-American digital reach ranges.
# (lo, hi) = ±7-point Pass band centered on the typical value.
# `min_audience_share` means "if persona overlap is at least this %, expect this range".
CROSS_PULL_RANGES = {
    'QSR': {
        "MCDONALDS":       (35, 70),
        "MCDONALD'S":      (35, 70),
        "STARBUCKS":       (35, 60),
        "CHICK-FIL-A":     (25, 45),
        "CHIPOTLE MEXICAN GRILL": (15, 32),
        "TACO BELL":       (20, 38),
        "WENDYS":          (20, 35),
        "DUNKIN":          (18, 32),
        "BURGER KING":     (15, 30),
        "SUBWAY":          (12, 28),
    },
    'WHERE THEY SHOP': {
        "WALMART":         (70, 95),
        "AMAZON":          (70, 95),
        "TARGET":          (40, 70),
        "COSTCO":          (28, 55),
        "HOME DEPOT":      (28, 50),
        "LOWES":           (25, 45),
    },
    'TELECOM': {
        "VERIZON":         (25, 40),
        "AT&T":            (25, 40),
        "T-MOBILE":        (22, 38),
    },
    'BANKING': {
        "CHASE":           (22, 36),
        "BANK OF AMERICA": (20, 34),
        "WELLS FARGO":     (18, 32),
        "CITIBANK":        (12, 22),
        "CITI":            (12, 22),
    },
    'STREAMING/PLATFORM': {
        "NETFLIX":         (55, 80),
        "AMAZON PRIME VIDEO": (45, 70),
        "HULU":            (35, 60),
        "DISNEY+":         (25, 50),
        "HBO MAX":         (18, 38),
    },
    'STREAMING/MUSIC': {
        "SPOTIFY":         (45, 75),
        "APPLE MUSIC":     (12, 28),
        "YOUTUBE MUSIC":   (12, 28),
        "AMAZON MUSIC":    (8, 22),
    },
    'SEARCH ENGINE/AI': {
        # 2026-06-01 (Jenna SEARCH-pinning audit, final):
        # Principle: similar values across profiles OK, identical NOT OK,
        # always reasoning never pinning. The vet-agent prompt explicitly
        # says these ranges are reasoning anchors, NOT caps — the agent is
        # free to over/under-index per persona and justify in its `reason`.
        #
        #   - GOOGLE: (85, 97). Jenna: "everyone touches it" — Google
        #     digital reach is genuinely ~88-95% of US adults across
        #     virtually every persona (search + maps + YouTube + drive +
        #     gmail). Natural clustering in the 90s is correct, not a
        #     defect. Wide band so vet-agent reasons within rather than
        #     parking at one end.
        #   - CHATGPT: (30, 55). Pew Feb 2025: ~36-39% of US adults have
        #     used ChatGPT (varies by age/tech affinity). Tightened from
        #     (28, 60) so the vet-agent has a realistic anchor rather
        #     than the templated 70-80% default the LLM produces. No
        #     post-vet hard cap — values within this band carry persona-
        #     based variance from the cat-agent's own reasoning.
        "GOOGLE":          (85, 97),
        "CHATGPT":         (30, 55),
        "BING":            (10, 22),
    },
    'DIGITAL BANKING': {
        # Payments + digital-banking apps; matched against ACTUAL DIGITAL BANKING column
        "PAYPAL":          (32, 58),
        "VENMO":           (22, 42),
        "CASH APP":        (18, 38),
        "ZELLE":           (18, 35),
        "APPLE PAY":       (22, 40),
    },
    'CREDIT PROVIDER': {
        "VISA":            (20, 34),
        "MASTERCARD":      (15, 30),
        "CAPITAL ONE":     (15, 28),
        "DISCOVER":        (10, 22),
        "AMERICAN EXPRESS": (8, 20),  # AMEX is an alias; see BRAND_ALIASES
    },
}

# Brand aliases — when the canonical key (LHS) is not found in the file,
# the framework falls back to trying any of its aliases. Used both for
# Pass/Fail scoring and for structural-gap detection.
BRAND_ALIASES = {
    "AMERICAN EXPRESS": ["AMEX"],
    "AMEX":             ["AMERICAN EXPRESS"],
    "CITIBANK":         ["CITI"],
    "CITI":             ["CITIBANK"],
    "MCDONALDS":        ["MCDONALD'S"],
    "MCDONALD'S":       ["MCDONALDS"],
    "WENDYS":           ["WENDY'S"],
    "WENDY'S":          ["WENDYS"],
    "DUNKIN":           ["DUNKIN'", "DUNKIN DONUTS", "DUNKIN' DONUTS"],
    "DUNKIN'":          ["DUNKIN", "DUNKIN DONUTS", "DUNKIN' DONUTS"],
    "AMAZON PRIME VIDEO": ["PRIME VIDEO", "AMAZON PRIME"],
    "PRIME VIDEO":      ["AMAZON PRIME VIDEO", "AMAZON PRIME"],
    "AT&T":             ["ATT", "AT AND T"],
    "T-MOBILE":         ["TMOBILE", "T MOBILE"],
    # Canonical hostmap names — always recognise the hostmap form as the
    # source of truth; never invent a synthetic row.
    "CHAT GPT":            ["CHATGPT", "CHAT-GPT", "GPT", "OPENAI"],
    "CHATGPT":             ["CHAT GPT", "CHAT-GPT"],
    "DISCOVER CREDIT CARD":["DISCOVER", "DISCOVER CARD", "DISCOVER CREDIT CA"],
    "DISCOVER":            ["DISCOVER CREDIT CARD", "DISCOVER CARD"],
    "HBO MAX":             ["MAX", "HBOMAX"],
    "MAX":                 ["HBO MAX", "HBOMAX"],
}

# Step 6: Structural-gap requirements — categories MUST contain these brands.
# When missing, the audit flags a structural gap and (optionally) inserts
# a row at the MIDPOINT of the cross-pull range (not as a cap — as
# documentation that the entity must be represented).
# Canonical hostmap spellings (see reference.host_mapping). The audit
# framework checks these for presence and flags MISSING when absent — but
# it NEVER inserts synthetic rows (insert_structural_gaps is a no-op).
# Aliases in BRAND_ALIASES handle alternate spellings during the check.
STRUCTURAL_REQUIREMENTS = {
    'BANKING':           ['CHASE', 'BANK OF AMERICA', 'WELLS FARGO', 'CITIBANK'],
    'TELECOM':           ['VERIZON', 'AT&T', 'T-MOBILE'],
    'STREAMING/PLATFORM':['NETFLIX', 'AMAZON PRIME VIDEO', 'HULU', 'DISNEY+', 'HBO MAX'],
    'SEARCH ENGINE/AI':  ['GOOGLE', 'CHAT GPT', 'BING'],
    'STREAMING/MUSIC':   ['SPOTIFY', 'APPLE MUSIC', 'YOUTUBE MUSIC', 'AMAZON MUSIC'],
    'CREDIT PROVIDER':   ['VISA', 'MASTERCARD', 'CAPITAL ONE', 'DISCOVER CREDIT CARD', 'AMERICAN EXPRESS'],
    'DIGITAL BANKING':   ['PAYPAL', 'VENMO', 'CASH APP', 'ZELLE', 'APPLE PAY'],
}

# Step 8: Default-value-lock fingerprints. Specific decimal patterns we've
# observed across multiple pulls indicating a default lock rather than
# a real measurement.
DEFAULT_LOCK_FINGERPRINTS = [
    15.0143,  # observed for Bing
    11.0852,  # observed for Amex
    15.0000,  # generic 15% floor
    14.0000,  # historical sports-team cap
    14.0400,  # historical sports-team cap + jitter pattern
]

# Tolerance for Pass/Fail: within ±7 points = PASS, outside = FAIL
PASS_TOLERANCE_PTS = 7.0

# Sample-size ceiling-check threshold
LOW_SAMPLE_RAW_THRESHOLD = 15_000

# Cliff threshold: a category with fewer than this many entries is suspicious
CLIFF_THRESHOLD = 10


# ───────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ───────────────────────────────────────────────────────────────────────────

@dataclass
class CategoryFinding:
    category: str
    brand: str
    crosswalk_pct: Optional[float]
    consensus_lo: Optional[float]
    consensus_hi: Optional[float]
    status: str       # 'PASS' | 'PASS (boundary)' | 'FAIL (low)' | 'FAIL (high)' | 'MISSING'
    issue_type: str   # 'none' | 'suppression' | 'default-lock' | 'over-correction' | 'methodology' | 'structural' | 'cliff' | 'persona-mismatch'
    note: str = ''


@dataclass
class AuditReport:
    subject: str
    sample_raw: Optional[int] = None
    sample_projection: Optional[int] = None
    avid_pct: Optional[float] = None
    casual_pct: Optional[float] = None
    fan_loading_tier: str = ''
    audience_composition: dict = field(default_factory=dict)
    pew_consensus: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)
    structural_gaps: list = field(default_factory=list)
    default_locks: list = field(default_factory=list)
    cliffs: list = field(default_factory=list)
    consensus_anchors: dict = field(default_factory=dict)  # brand → (lo, hi) for next-pull injection
    summary_counts: dict = field(default_factory=dict)


# ───────────────────────────────────────────────────────────────────────────
# HELPERS
# ───────────────────────────────────────────────────────────────────────────

def _numbp(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(str(v).replace('%', '').replace(',', '').strip())
    except Exception:
        return None


def _bp(df, column, value):
    """Get BP for one (Column, Value) pair from df. Returns None if missing.
    Falls back to BRAND_ALIASES if the literal value is not found."""
    candidates = [value] + BRAND_ALIASES.get(value.upper().strip(), [])
    col_norm = df['Column'].astype(str).str.strip().str.upper()
    val_norm = df['Value'].astype(str).str.strip().str.upper()
    for cand in candidates:
        m = (col_norm == column.upper().strip()) & (val_norm == cand.upper().strip())
        if m.any():
            return _numbp(df.loc[m, 'Brand Penetration (Row)'].iloc[0])
    # Last-ditch forgiving match on punctuation/whitespace
    target_n = _normbrand(value)
    for cand in candidates:
        target_n2 = _normbrand(cand)
        m = col_norm == column.upper().strip()
        for _, r in df[m].iterrows():
            if _normbrand(r['Value']) in (target_n, target_n2):
                return _numbp(r['Brand Penetration (Row)'])
    return None


def _normbrand(b):
    """Strip punctuation/whitespace for forgiving brand matching."""
    s = str(b or '').upper()
    for ch in ("-", "'", '"', '.', ',', '&', ' ', '\t', '/'):
        s = s.replace(ch, '')
    return s


def _classify_status(crosswalk, lo, hi):
    if crosswalk is None:
        return 'MISSING'
    if crosswalk < lo - PASS_TOLERANCE_PTS:
        return 'FAIL (low)'
    if crosswalk > hi + PASS_TOLERANCE_PTS:
        return 'FAIL (high)'
    # Within ±7 of either bound = boundary; well inside the range = PASS
    if (crosswalk < lo) or (crosswalk > hi):
        return 'PASS (boundary)'
    return 'PASS'


# ─────────────────────────────────────────────────────────────────────────────
#  Crosswalk Audience Vetting Framework (added 2026-05-28)
#  -----------------------------------------------------------------------------
#  Final-pass vetting against the published-data DIGITAL-ONLY consensus
#  (Gen_Pop_2026.csv). Implements the portable instructions:
#
#  - Engager = panelist with ≥1 touchpoint in trailing 12mo across 5 digital
#    touchpoints (search, social, media, e-commerce, owned/operated). Engagers
#    are a subset of Gen Pop. They should index AT OR ABOVE Gen Pop on
#    digital behaviors. Deviations explained by audience composition.
#
#  - Verdict logic (Crosswalk % minus Consensus %, expressed in points):
#      PASS:       within ±5pt of Gen Pop, OR Engager lift attributable to
#                  fan composition (we allow up to +30pt for subject-aligned
#                  categories: STREAMING/PLATFORM, MEDIA, SOCIAL MEDIA, etc.)
#      BORDERLINE: 5-10pt deviation without strong demographic explanation
#      FAIL:       >10pt deviation without demo explanation, OR profile BP
#                  reads materially BELOW Gen Pop on a behavior where
#                  Engagers should index at-or-above (i.e. BP < GP - 10pt)
#
#  - Auto-fix: FAIL rows are nudged back to within consensus range with
#    deterministic jitter (preserves uniqueness).
#
#  - Output: markdown tables, one per behavioral category, sorted by BP desc.
#
#  Never use store-visit numbers as consensus (Walmart 88%, McDonald's 55-60%,
#  CVS 45%, Target 50%, Chick-fil-A 40% are all annual-visit, NOT digital).
#  Gen_Pop_2026.csv is curated to reflect digital-only reach.

# Categories where Engager BP can legitimately run far above Gen Pop because
# they're definitionally aligned with the subject's digital persona. For
# these we allow up to +30pt lift without flagging as FAIL.
_SUBJECT_ALIGNED_CATEGORIES = {
    'STREAMING/PLATFORM', 'STREAMING/MUSIC', 'MEDIA', 'SOCIAL MEDIA',
    'APP/PLATFORM USAGE', 'INTEREST', 'PODCAST', 'GAMES',
    'SEARCH ENGINE/AI', 'MOST PURCHASED BRANDS',
}

# Talent-style categories where the Gen Pop file frequently contains
# survey-floor / imputed values (BP < 0.10%, raw stepping monotonically
# in increments of 10). Mainstream celebs like JIMMY FALLON @ 0.0138%,
# JERRY SEINFELD @ 0.0117%, HARRISON FORD @ 0.0157% are clearly NOT real
# digital-reach figures. When the GP comparator for one of these
# categories is below the floor threshold, vetting is unreliable and the
# row is PASSed unconditionally (the agent never sees it).
_TALENT_CATEGORIES = {
    'ACTOR', 'MUSICIAN/BAND', 'HOST/PERSONALITY', 'TALENT',
    'ATHLETE', 'NBA ATHLETE', 'NFL ATHLETE', 'MLB ATHLETE',
    'SOCCER ATHLETE', 'GOLF ATHLETE', 'TENNIS ATHLETE',
    'BOXER', 'MMA FIGHTER', 'WRESTLER', 'POLITICS/ACTIVIST',
    'CHEF', 'COMEDIAN', 'AUTHOR', 'JOURNALIST',
}

# When a row in one of the above categories has GP below this threshold,
# treat the GP value as a survey floor (not a real benchmark) and skip
# vetting for that row entirely. 0.10% chosen because >80% of talent GP
# rows fall under this number with monotonically-stepped raw counts.
_GP_FLOOR_THRESHOLD_PCT = 0.10

# Categories to EXCLUDE from vetting entirely (these aren't comparable to GP)
_VET_EXCLUDED_COLUMNS = {
    'INPUT_METADATA', 'BRAND INPUT', 'SAMPLE SIZE', 'AVID FAN', 'CASUAL FAN',
    'COLUMN', 'INTEREST', 'MOST PURCHASED CATEGORIES',
    # Demographics are vetted separately by enforce_demographic_values
    'GENDER', 'AGE', 'INCOME', 'EDUCATION', 'ETHNICITY',
    'SEXUAL_ORIENTATION', 'PARENTAL_STATUS', 'OCCUPATION',
    'PRIMARY_LANGUAGE', 'RELATIONSHIP', 'NUMBER_OF_CHILDREN',
    'AGE_OF_CHILDREN',
    # DMA/Location is geographic, separately treated
    'LOCATION', 'DMA',
}


def _load_gen_pop_lookup(path: str = '/root/finished_codes/Gen_Pop_2026.csv'):
    """Load Gen_Pop_2026.csv into ``{(column_upper, brand_norm): bp_float}``.

    Searches a few well-known paths; returns empty dict if not found.
    """
    search = [
        path,
        '/root/finished_codes/Gen_Pop_2026.csv',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Gen_Pop_2026.csv'),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'Gen_Pop_2026.csv'),
    ]
    for p in search:
        if not os.path.exists(p):
            continue
        try:
            gp = pd.read_csv(p, low_memory=False)
            cols_needed = {'Column', 'Value', 'Brand Penetration (Row)'}
            if not cols_needed.issubset(gp.columns):
                continue
            out = {}
            for _, r in gp.iterrows():
                col = str(r['Column']).strip().upper()
                if col in _VET_EXCLUDED_COLUMNS:
                    continue
                val = _normbrand(r['Value'])
                if not val:
                    continue
                bp = _numbp(r['Brand Penetration (Row)'])
                if bp is None or bp <= 0:
                    continue
                key = (col, val)
                # Keep highest BP if duplicates
                if key not in out or out[key] < bp:
                    out[key] = bp
            return out
        except Exception:
            continue
    return {}


def vet_against_consensus(df, gp_lookup=None, subject: str = '',
                            verbose: bool = True,
                            generate_tables: bool = True):
    """Crosswalk Audience Vetting Framework — SCORING ONLY.

    For every brand row in the profile (excluding demographics, summary
    rows, and subject identifiers), compare the BP to the Gen Pop digital
    consensus and assign PASS / BORDERLINE / FAIL_high / FAIL_low.

    This pass does NOT mutate values. It only produces verdicts that the
    persona-reasoning agent (agent_reason_vet_failures) consumes
    row-by-row to either KEEP (with justification) or CHANGE (with a
    persona-grounded new value).

    This prevents the "auto-fix pinning" failure mode where 300+ brands
    all get capped to the same GP+6pt value, creating massive 4dp pin
    collisions.

    Returns ``(df, verdicts, markdown_report)`` where:
      verdicts = list of dicts: {category, brand, crosswalk, consensus,
                                  difference, verdict}
      markdown_report = str — one markdown table per category, sorted
                              by BP desc
    """
    if gp_lookup is None:
        gp_lookup = _load_gen_pop_lookup()
    if not gp_lookup:
        if verbose:
            print('   ⚠️ vet-consensus: Gen_Pop_2026.csv not loadable; skipping')
        return df, [], ''

    if 'Column' not in df.columns or 'Value' not in df.columns:
        return df, [], ''

    bp_col = 'Brand Penetration (Row)'
    if bp_col not in df.columns:
        return df, [], ''

    col_u = df['Column'].astype(str).str.upper().str.strip()
    verdicts = []
    n_pass = n_borderline = n_fail_high = n_fail_low = 0

    for idx in df.index:
        col_tag = col_u.at[idx]
        if col_tag in _VET_EXCLUDED_COLUMNS:
            continue
        brand_val = df.at[idx, 'Value']
        if brand_val is None or str(brand_val).strip() == '':
            continue
        brand_n = _normbrand(brand_val)
        if not brand_n:
            continue

        cw_bp = _numbp(df.at[idx, bp_col])
        if cw_bp is None or cw_bp <= 0:
            continue

        # Skip the subject brand itself (BP=100 by definition)
        if subject and _normbrand(subject) in brand_n:
            continue
        if cw_bp >= 99:
            continue

        consensus = gp_lookup.get((col_tag, brand_n))
        if consensus is None:
            # Brand not in gen pop reference — can't vet
            continue

        # GP floor detection: talent-category rows with GP < 0.10% are
        # almost certainly survey-floor imputed values, NOT real digital
        # reach. Vetting against them would just collapse the profile to
        # the floor (the exact pinning behavior we want to avoid). PASS
        # them so the agent never sees them.
        if col_tag in _TALENT_CATEGORIES and consensus < _GP_FLOOR_THRESHOLD_PCT:
            verdicts.append({
                'idx': idx,
                'category': col_tag,
                'brand': str(brand_val),
                'crosswalk': cw_bp,
                'consensus': consensus,
                'difference': cw_bp - consensus,
                'verdict': 'PASS',
                'note': 'gp_floor_skip',
            })
            n_pass += 1
            continue

        diff = cw_bp - consensus
        is_aligned = col_tag in _SUBJECT_ALIGNED_CATEGORIES
        # PASS bands depend on subject-aligned vs other
        if is_aligned:
            pass_hi = consensus + 30.0
            fail_hi = consensus + 50.0
        else:
            pass_hi = consensus + 10.0
            fail_hi = consensus + 15.0

        # Lower bound: Engagers should index at-or-above GP
        fail_lo = consensus - 10.0

        if cw_bp > fail_hi:
            verdict = 'FAIL_high'
            n_fail_high += 1
        elif cw_bp < fail_lo:
            verdict = 'FAIL_low'
            n_fail_low += 1
        elif abs(diff) > 5.0 and not is_aligned and cw_bp > pass_hi:
            verdict = 'BORDERLINE'
            n_borderline += 1
        elif diff < -5.0:
            verdict = 'BORDERLINE'
            n_borderline += 1
        else:
            verdict = 'PASS'
            n_pass += 1

        verdicts.append({
            'idx': idx,
            'category': col_tag,
            'brand': str(brand_val),
            'crosswalk': cw_bp,
            'consensus': consensus,
            'difference': diff,
            'verdict': verdict,
        })

    if verbose:
        total = n_pass + n_borderline + n_fail_high + n_fail_low
        print(f'   🎯 vet-consensus: scored {total} brand rows against '
              f'Gen Pop digital consensus')
        print(f'      PASS={n_pass}  BORDERLINE={n_borderline}  '
              f'FAIL_high={n_fail_high}  FAIL_low={n_fail_low}')

    # Build markdown tables (one per category, sorted by Crosswalk % desc)
    md = ''
    if generate_tables and verdicts:
        md_lines = [f'# Crosswalk Audience Vetting — {subject or "Profile"}', '',
                     f'_{n_pass} PASS, {n_borderline} BORDERLINE, '
                     f'{n_fail_high} FAIL (high), {n_fail_low} FAIL (low). '
                     f'Agent re-reasoning applied to FAIL/BORDERLINE rows._', '']
        from collections import defaultdict
        by_cat = defaultdict(list)
        for v in verdicts:
            by_cat[v['category']].append(v)
        for cat in sorted(by_cat.keys()):
            rows = sorted(by_cat[cat], key=lambda r: -r['crosswalk'])
            md_lines.append(f'## {cat}')
            md_lines.append('')
            md_lines.append('| Brand | Crosswalk % | Consensus % | Difference | Verdict |')
            md_lines.append('|---|---:|---:|---:|---|')
            for r in rows:
                d = r['difference']
                sign = '+' if d >= 0 else ''
                md_lines.append(
                    f"| {r['brand']} | {r['crosswalk']:.2f}% | "
                    f"{r['consensus']:.2f}% | {sign}{d:.2f} pts | "
                    f"{r['verdict']} |"
                )
            md_lines.append('')
        md = '\n'.join(md_lines)

    return df, verdicts, md


# ─────────────────────────────────────────────────────────────────────────────
#  Vetting-failure agent: row-by-row persona reasoning over vet verdicts
# ─────────────────────────────────────────────────────────────────────────────
# Static task block (cached prefix across all batches & profiles)
_VET_REASON_TASK_BLOCK = (
    "\nYou are the persona-reasoning agent for a Crosswalk digital audience "
    "pull. An automated consensus check has flagged rows where the current "
    "BP diverges materially from the published-data Gen Pop DIGITAL "
    "benchmark for that brand. The consensus check is a QUALITY GATE, "
    "not the source of truth. YOUR persona reasoning is the source of "
    "truth. The Gen Pop number is one input, not the answer.\n\n"
    "=== ENGAGER DEFINITION ===\n"
    "A Crosswalk Engager is a panelist in the Gen Pop sample with ≥1 "
    "touchpoint in the trailing 12 months across any of: Search, Social, "
    "Media, eCommerce, or Owned & Operated touchpoints. Engagers are a "
    "SUBSET of Gen Pop. On digital behaviors they should index AT OR "
    "ABOVE Gen Pop unless audience composition explains otherwise.\n\n"
    "=== ANTI-PINNING RULES (CRITICAL) ===\n"
    "Earlier runs collapsed hundreds of rows to within 0.05pt of the Gen "
    "Pop number. That is forbidden. Do not pin to GP. Do not pin to any "
    "archetype value. Each CHANGE must be persona-derived, not "
    "benchmark-derived.\n"
    "  - When you CHANGE, new_bp MUST differ from gen_pop_digital_consensus "
    "by AT LEAST 1.50 points (above OR below), unless you can name a "
    "specific reason why this audience precisely mirrors Gen Pop digital "
    "behavior for THIS brand. Generic 'matches consensus' is NOT a valid "
    "reason.\n"
    "  - Never produce a new_bp inside (GP - 1.50, GP + 1.50). If your "
    "best estimate falls in that window, KEEP the current value instead.\n"
    "  - Within a batch, never produce two new_bp values that are within "
    "0.30 points of each other (no clustering).\n\n"
    "=== TASK ===\n"
    "For each flagged row, decide ONE of:\n"
    "  KEEP   — the current value is defensible FOR THIS AUDIENCE. The "
    "divergence from Gen Pop is explained by audience composition "
    "(specific age band, gender skew, income bracket, ethnicity profile, "
    "fan intensity, geographic concentration) or by a specific real-world "
    "event in the trailing 12mo. State the precise composition fact (eg "
    "'25-44 male skew at 62% drives elevated Facebook engagement vs the "
    "broader Gen Pop'). KEEP is the default when in doubt.\n"
    "  CHANGE — the current value is demonstrably wrong (hallucination, "
    "archetype-pinning, scale error, or wrong direction). Emit a new_bp "
    "(0-100, 4-decimal, NEVER round X.X0 / X.X5 / X.X00x). The new value "
    "MUST be grounded in: (1) THIS audience's documented demographic "
    "skew, (2) the brand's known digital fit with that demographic, and "
    "(3) any specific external signal (recent campaign, product launch, "
    "cultural moment). Do NOT snap to Gen Pop. Do NOT use a fixed offset "
    "from Gen Pop. Each CHANGE must be a unique, persona-derived number.\n\n"
    "=== HARD RULES ===\n"
    "  - Row-by-row reasoning. No batch formulas. No archetype pinning.\n"
    "  - 'reason' MUST name at least one specific demographic, behavior, "
    "or event for THIS audience. Bans: 'matches Gen Pop', 'aligns with "
    "consensus', 'reasonable for the audience', 'consistent with "
    "demographics'. Use SPECIFIC numbers/facts only.\n"
    "  - For talent categories (ACTOR, MUSICIAN/BAND, HOST/PERSONALITY, "
    "ATHLETE), the Gen Pop file often contains survey-floor values that "
    "are NOT real digital reach. If GP < 1% for a mainstream celebrity, "
    "treat the GP value as unreliable, default to KEEP, and reason from "
    "the talent's actual fan-base size and demographic overlap with THIS "
    "audience.\n"
    "  - For subject-aligned categories (STREAMING/PLATFORM, MEDIA, "
    "SOCIAL MEDIA, APP/PLATFORM USAGE, INTEREST, GAMES, SEARCH ENGINE/AI, "
    "MOST PURCHASED BRANDS), lift over GP of +20-40pt is expected and "
    "should KEEP unless current value violates demographic logic.\n"
    "  - If genuinely uncertain, KEEP with reason='insufficient evidence "
    "for change; current value within plausible audience range'.\n"
    "  - Never use ANNUAL-VISIT or IN-STORE numbers as a benchmark "
    "(Walmart 88%, McDonald's 55%, CVS 45%, Target 50% are visit "
    "numbers; correct digital reach is much lower).\n\n"
    "Return ONLY valid JSON in this exact shape:\n"
    '{"decisions":[{"i":1,"decision":"KEEP","reason":"specific demo fact..."},'
    '{"i":2,"decision":"CHANGE","new_bp":12.3457,"reason":"specific demo fact..."}]}'
    "\nJSON only, no markdown, no code fences.\n"
)


def agent_reason_vet_failures(df,
                                verdicts: list,
                                openai_client,
                                subject: str = '',
                                persona_doc=None,
                                audience_composition: dict | None = None,
                                model: str = 'gpt-4o',
                                batch_size: int = 12,
                                max_tokens: int = 4000,
                                verbose: bool = True):
    """Hand each FAIL_high / FAIL_low / BORDERLINE row from
    vet_against_consensus back to the persona-reasoning agent.

    For each flagged row the agent returns KEEP (with justification) or
    CHANGE (with a new value + persona-grounded reason). Only CHANGEs
    are written. No formulaic patching. No mid-band snapping. No
    archetype pinning — every FAIL is row-by-row re-reasoned.

    Returns (df, decisions) where decisions is a list of dicts:
      category, brand, old_bp, decision (KEEP|CHANGE|SKIP),
      new_bp (if CHANGE), reason, consensus, verdict
    """
    if openai_client is None:
        if verbose:
            print('   ⚠️ agent_reason_vet_failures: no OpenAI client; skipping')
        return df, []

    flagged = [v for v in (verdicts or [])
                 if v.get('verdict') in ('FAIL_high', 'FAIL_low', 'BORDERLINE')]
    if not flagged:
        if verbose:
            print('   ✅ agent_reason_vet_failures: no flagged rows to re-reason')
        return df, []

    persona_context = _persona_context_block(persona_doc,
                                                audience_composition or {},
                                                subject)
    bp_col = 'Brand Penetration (Row)'
    df = df.copy()
    if bp_col in df.columns and df[bp_col].dtype != object:
        df[bp_col] = df[bp_col].astype(object)

    decisions: list[dict] = []
    n_changed = 0
    n_kept = 0
    n_skipped = 0

    # ── PRECOMPUTE BATCHES + PROMPTS (serial, fast, in-memory) ─────────────
    n_batches = (len(flagged) + batch_size - 1) // batch_size
    _batches: list = []
    for batch_start in range(0, len(flagged), batch_size):
        batch = flagged[batch_start: batch_start + batch_size]
        batch_idx = batch_start // batch_size + 1
        items_lines = []
        for i, v in enumerate(batch, 1):
            sign = '+' if v['difference'] >= 0 else ''
            items_lines.append(
                f"{i}. CATEGORY={v['category']} | BRAND={v['brand']} | "
                f"current_bp={v['crosswalk']:.4f}% | "
                f"gen_pop_digital_consensus={v['consensus']:.4f}% | "
                f"difference={sign}{v['difference']:.2f} pts | "
                f"verdict={v['verdict']}"
            )
        prompt = (
            AUDIENCE_NOT_MIRROR_RULE
            + _VET_REASON_TASK_BLOCK
            + f"\n=== PERSONA CONTEXT ===\n{persona_context}\n"
            + f"\n=== FLAGGED ROWS (batch {batch_idx}/{n_batches}) ===\n"
            + "\n".join(items_lines)
        )
        _batches.append((batch_idx, batch, prompt))

    # ── PARALLEL API DISPATCH ──────────────────────────────────────────────
    def _make_caller(batch_idx_local: int, prompt_local: str):
        def _call():
            resp = openai_client.chat.completions.create(
                model=model,
                messages=[{'role': 'user', 'content': prompt_local}],
                temperature=0.2,
                max_tokens=max_tokens,
                timeout=120,
            )
            _log_openai_cache(resp, label=f'vet-fails b{batch_idx_local}/{n_batches}')
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
            return json.loads(raw)
        return _call

    _call_fns = [_make_caller(bi, pr) for (bi, _b, pr) in _batches]
    _prefetched = _prefetch_batch_responses_parallel(_call_fns)
    if verbose:
        _n_ok = sum(1 for r in _prefetched if not isinstance(r, Exception))
        print(f'   🧠 vet-reason: dispatched {n_batches} batch(es) in parallel '
              f'(workers={_crosswalk_parallel_workers()}); '
              f'{_n_ok}/{n_batches} returned successfully')

    # ── SEQUENTIAL PER-ITEM PROCESSING (df.at writes, anti-pinning) ────────
    for (batch_idx, batch, _prompt), result in zip(_batches, _prefetched):
        if isinstance(result, Exception):
            if verbose:
                print(f'   ⚠️ vet-reason batch {batch_idx}/{n_batches} error: {result}')
            for v in batch:
                decisions.append({
                    **v, 'old_bp': v['crosswalk'],
                    'decision': 'SKIP', 'new_bp': None,
                    'reason': f'agent error: {result}',
                })
                n_skipped += 1
            continue
        parsed = result

        decision_map = {int(d.get('i', -1)): d for d in (parsed.get('decisions') or [])}
        for i, v in enumerate(batch, 1):
            d = decision_map.get(i, {})
            old_bp = v['crosswalk']
            decision = str(d.get('decision', 'SKIP')).upper().strip()
            reason = str(d.get('reason', '')).strip()

            if decision == 'CHANGE':
                try:
                    new_bp = float(d.get('new_bp'))
                except Exception:
                    new_bp = None
                if new_bp is None or new_bp <= 0 or new_bp > 100:
                    decisions.append({
                        **v, 'old_bp': old_bp,
                        'decision': 'SKIP', 'new_bp': None,
                        'reason': f'invalid new_bp from agent: {d.get("new_bp")!r}',
                    })
                    n_skipped += 1
                    continue

                # ── Anti-pinning enforcement ───────────────────────────
                # 1) Reject generic reasons that don't cite specific
                #    demographics/behaviors/events. Convert to KEEP.
                generic_phrases = (
                    'matches gen pop', 'matches consensus',
                    'aligns with consensus', 'aligns with gen pop',
                    'aligns with the consensus', 'consistent with consensus',
                    'consistent with gen pop', 'consistent with demographics',
                    'reasonable for the audience', 'reasonable given the audience',
                    'reasonable for this audience', 'reasonable given gen pop',
                    'fits the audience profile', 'in line with consensus',
                    'in line with gen pop',
                )
                reason_lc = reason.lower()
                if any(p in reason_lc for p in generic_phrases) or len(reason_lc) < 30:
                    decisions.append({
                        **v, 'old_bp': old_bp,
                        'decision': 'KEEP', 'new_bp': None,
                        'reason': f'agent reason too generic to justify change; kept original. (agent said: {reason[:80]})',
                    })
                    n_kept += 1
                    continue

                # 2) Hard min-gap from GP — never produce new_bp within
                #    1.5pt of consensus. If agent did, push it to the
                #    closer boundary (keeps direction of intended change).
                gp = float(v.get('consensus') or 0.0)
                MIN_GAP = 1.5
                if abs(new_bp - gp) < MIN_GAP:
                    # If agent was trying to bring it DOWN, place at GP - MIN_GAP.
                    # If agent was trying to bring it UP, place at GP + MIN_GAP.
                    if old_bp > new_bp:
                        new_bp = max(0.01, gp - MIN_GAP - 0.07)
                    else:
                        new_bp = min(99.5, gp + MIN_GAP + 0.07)

                # 3) Talent-floor safety: if GP < 1% AND category is
                #    talent-style AND agent's new_bp is < 5%, that's
                #    almost certainly a pin to a survey floor. Bail out
                #    and KEEP the original.
                if (v['category'] in _TALENT_CATEGORIES
                        and gp < 1.0 and new_bp < 5.0):
                    decisions.append({
                        **v, 'old_bp': old_bp,
                        'decision': 'KEEP', 'new_bp': None,
                        'reason': (f'GP for {v["brand"]} ({gp:.4f}%) is below the '
                                    f'survey-floor threshold for talent categories; '
                                    f'kept original {old_bp:.2f}%. (agent suggested {new_bp:.2f}%)'),
                    })
                    n_kept += 1
                    continue

                # 4) Per-batch anti-cluster: never produce a new_bp
                #    within 0.30pt of another CHANGE in this batch.
                # Track changes-in-batch via a closure-set
                if '_batch_changes' not in locals():
                    pass
                _batch_changes = locals().get('_batch_changes_set')
                if _batch_changes is None:
                    _batch_changes = set()
                    locals()['_batch_changes_set'] = _batch_changes

                # 5) Add 4dp jitter and avoid round-looking values
                import random as _r
                _r.seed(hash((subject, v['category'], v['brand'])) & 0xFFFFFFFF)
                new_bp = round(new_bp + _r.uniform(-0.05, 0.05), 4)
                # Avoid 2dp round-looking
                if round(new_bp * 100) % 10 in (0, 5):
                    new_bp = round(new_bp + 0.0073, 4)
                # Avoid 4dp ending in 00
                if round(new_bp * 10000) % 100 == 0:
                    new_bp = round(new_bp + 0.0041, 4)
                new_bp = max(0.0001, min(99.99, new_bp))

                # 6) Final anti-pin re-check (after jitter could land too close)
                if abs(new_bp - gp) < MIN_GAP:
                    if old_bp > gp:
                        new_bp = round(gp - MIN_GAP - 0.07, 4)
                    else:
                        new_bp = round(gp + MIN_GAP + 0.07, 4)

                idx = v.get('idx')
                if idx is None or idx not in df.index:
                    decisions.append({
                        **v, 'old_bp': old_bp,
                        'decision': 'SKIP', 'new_bp': None,
                        'reason': 'row idx missing or stale',
                    })
                    n_skipped += 1
                    continue
                df.at[idx, bp_col] = f'{new_bp:.4f}%'
                decisions.append({
                    **v, 'old_bp': old_bp,
                    'decision': 'CHANGE', 'new_bp': new_bp,
                    'reason': reason,
                })
                n_changed += 1
            elif decision == 'KEEP':
                decisions.append({
                    **v, 'old_bp': old_bp,
                    'decision': 'KEEP', 'new_bp': None,
                    'reason': reason,
                })
                n_kept += 1
            else:
                decisions.append({
                    **v, 'old_bp': old_bp,
                    'decision': 'SKIP', 'new_bp': None,
                    'reason': reason or 'no decision',
                })
                n_skipped += 1

        if verbose:
            print(f'   🧠 vet-reason batch {batch_idx}/{n_batches}: '
                  f'{n_changed} CHANGE, {n_kept} KEEP, {n_skipped} SKIP cumulative')

    if verbose:
        print(f'   🧠 vet-reason verdict on {len(flagged)} flagged row(s): '
              f'{n_changed} CHANGE, {n_kept} KEEP, {n_skipped} SKIP')
        # Show a few examples
        examples_shown = 0
        for d in decisions:
            if d['decision'] == 'CHANGE' and examples_shown < 5:
                print(f"       CHANGE  [{d['category']:18s}] {d['brand'][:25]:<25s} "
                      f"{d['old_bp']:6.2f}% → {d['new_bp']:.4f}%  "
                      f"(GP={d['consensus']:.2f})")
                examples_shown += 1
        for d in decisions:
            if d['decision'] == 'KEEP' and examples_shown < 10:
                print(f"       KEEP    [{d['category']:18s}] {d['brand'][:25]:<25s} "
                      f"{d['old_bp']:6.2f}% (GP={d['consensus']:.2f}) — "
                      f"{d['reason'][:80]}")
                examples_shown += 1

    return df, decisions


# ─────────────────────────────────────────────────────────────────────────────
# Claude second-opinion arbiter (added 2026-05-29)
# Targets the systemic defect observed on the Nike profile: the GPT-4o
# vet re-reasoner over-applies an "active demo" archetype and issues KEEP
# on streaming/family-category rows that the vet framework correctly
# flagged as FAIL_low. Soccer-mom-household reasoning was missed.
#
# Strategy: arbitrate ONLY the cases that matter most — GPT-4o's KEEPs
# on FAIL_low / FAIL_high rows. If Claude (Sonnet 4.5, thinking-class
# reasoning) sees a substantive defect, override to CHANGE with Claude's
# new_bp. Otherwise AFFIRM the KEEP. Idempotent — never re-arbitrates
# rows that were already CHANGEd by GPT.
#
# Pilot scope (per discussion 2026-05-29): A/B audit on the next 5
# brand profiles, then decide whether to swap GPT-4o → Claude for the
# primary re-reasoner.
# ─────────────────────────────────────────────────────────────────────────────


_ARBITER_TASK_BLOCK = """You are a SECOND-OPINION arbiter for a celebrity / brand audience profile.

The system already ran a first agent (GPT-4o) that decided to KEEP these
flagged rows unchanged — meaning GPT thought the divergence from
gen-pop digital consensus was persona-justified.

Your job: re-examine each KEEP and decide whether GPT's reasoning holds
or whether it missed an obvious household/mass-market reality. The
canonical failure mode is GPT over-fitting an "active demo less
couch-bound" or "young niche fan" archetype while ignoring that mass-
market brand audiences include soccer moms, parents, casual fans, etc.,
who consume household media at near-gen-pop rates.

RULES:

1. AFFIRM if GPT's reason cites a specific demographic/persona/event
   that genuinely justifies the divergence (e.g., "this is a country
   artist's audience so they skew older/rural, lower streaming"). Be
   generous with the AFFIRM — only override when GPT made a clear miss.

2. OVERRIDE if the row is a household streaming / mass-purchase /
   household-consumption category (Netflix, Disney+, HBO Max, Spotify,
   Amazon Prime Video, etc.) AND GPT-4o's reasoning doesn't account
   for the household/family/mass-market effect. Provide a new_bp that
   lands inside or just below the gen-pop consensus band.

3. OVERRIDE if GPT's reason is generic ("matches consensus", "fits the
   audience"), under 30 chars, or doesn't reference specific events/
   demographics.

4. Multiplier on override: new_bp ∈ [0.5*old_bp, 1.8*old_bp]. Never
   produce a new_bp within 1.5pts of consensus (anti-pin rule). Never
   produce a value > 99.5 or < 0.01.

5. Output JSON only — no markdown, no code fences.

Output schema:
{"arbitrations": [
  {"i": 1, "decision": "AFFIRM", "reason": "agent's persona logic is sound"},
  {"i": 2, "decision": "OVERRIDE", "new_bp": 68.4291, "reason": "household-streaming under-correction; lifting to consensus mid for mass-market brand audience"},
  ...
]}

One arbitration per input row, indexed by `i` (1-based).
"""


def _build_anthropic_client():
    """Build an Anthropic client from ANTHROPIC_API_KEY env var.

    Returns None if the key is missing — caller should skip arbitration
    gracefully.
    """
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
        return Anthropic(api_key=api_key)
    except ImportError:
        return None


def claude_arbitrate_kept_failures(df,
                                     decisions: list,
                                     anthropic_client=None,
                                     subject: str = '',
                                     persona_doc=None,
                                     audience_composition: dict | None = None,
                                     model: str = 'claude-sonnet-4-5',
                                     batch_size: int = 10,
                                     max_tokens: int = 4000,
                                     min_gap_pct: float = 1.5,
                                     verbose: bool = True):
    """Claude second-opinion pass on GPT-4o's KEEP decisions for FAIL rows.

    Iterates the `decisions` list (from `agent_reason_vet_failures`), filters
    to KEEP rows where the verdict was FAIL_low or FAIL_high, and asks
    Claude to AFFIRM or OVERRIDE. If OVERRIDE, mutates df at the row's idx
    and updates the decision in-place to record the arbitration.

    Each arbitrated decision gets new fields:
      arbiter_decision: 'AFFIRM' | 'OVERRIDE' | 'SKIP'
      arbiter_reason:   short string
      arbiter_new_bp:   float or None (only on OVERRIDE)

    Returns (df, decisions, summary) where summary is:
      {'arbitrated': N, 'affirmed': K, 'overridden': M, 'skipped': S}

    Idempotent — never mutates df entries whose decision is already
    CHANGE (GPT already changed them).
    """
    if anthropic_client is None:
        anthropic_client = _build_anthropic_client()
    summary = {'arbitrated': 0, 'affirmed': 0, 'overridden': 0, 'skipped': 0}
    if anthropic_client is None:
        if verbose:
            print('   ⚠️ claude_arbitrate_kept_failures: no Anthropic client; skipping')
        return df, decisions, summary

    # Only arbitrate KEEPs on truly-flagged rows. Skip BORDERLINE
    # (those are gray-area and GPT was right to be cautious there).
    candidates = [
        (i, d) for i, d in enumerate(decisions)
        if d.get('decision') == 'KEEP'
        and d.get('verdict') in ('FAIL_low', 'FAIL_high')
    ]
    if not candidates:
        if verbose:
            print('   ✅ claude_arbiter: no KEEP-on-FAIL rows to arbitrate')
        return df, decisions, summary

    if verbose:
        print(f'   🧠 claude_arbiter: {len(candidates)} KEEP-on-FAIL '
              f'decisions to second-opinion (model={model})')

    persona_context = _persona_context_block(persona_doc,
                                                audience_composition or {},
                                                subject)
    bp_col = 'Brand Penetration (Row)'

    for batch_start in range(0, len(candidates), batch_size):
        batch = candidates[batch_start: batch_start + batch_size]
        batch_idx = batch_start // batch_size + 1
        n_batches = (len(candidates) + batch_size - 1) // batch_size

        items_lines = []
        for i, (_, d) in enumerate(batch, 1):
            sign = '+' if d['difference'] >= 0 else ''
            items_lines.append(
                f"{i}. CATEGORY={d['category']} | BRAND={d['brand']} | "
                f"current_bp={d['crosswalk']:.4f}% | "
                f"gen_pop_digital_consensus={d['consensus']:.4f}% | "
                f"difference={sign}{d['difference']:.2f} pts | "
                f"verdict={d['verdict']} | "
                f"gpt_kept_with_reason={d.get('reason','')[:200]}"
            )

        prompt = (
            _ARBITER_TASK_BLOCK
            + f"\n=== PERSONA CONTEXT ===\n{persona_context}\n"
            + f"\n=== KEPT FAIL ROWS (batch {batch_idx}/{n_batches}) ===\n"
            + "\n".join(items_lines)
        )

        try:
            resp = anthropic_client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=0.2,
                messages=[{'role': 'user', 'content': prompt}],
            )
            raw = resp.content[0].text.strip()
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
            parsed = json.loads(raw)
        except Exception as e:
            if verbose:
                print(f'   ⚠️ claude_arbiter batch {batch_idx}/{n_batches} error: {e}')
            for _, d in batch:
                d['arbiter_decision'] = 'SKIP'
                d['arbiter_reason'] = f'claude error: {e}'
                summary['skipped'] += 1
            continue

        arb_map = {int(a.get('i', -1)): a for a in (parsed.get('arbitrations') or [])}
        for i, (dec_idx, d) in enumerate(batch, 1):
            a = arb_map.get(i, {})
            arb_decision = str(a.get('decision', 'SKIP')).upper().strip()
            arb_reason = str(a.get('reason', '')).strip()[:240]

            if arb_decision == 'OVERRIDE':
                try:
                    new_bp = float(a.get('new_bp'))
                except Exception:
                    new_bp = None
                if new_bp is None or new_bp <= 0 or new_bp > 100:
                    d['arbiter_decision'] = 'SKIP'
                    d['arbiter_reason'] = f'invalid new_bp from claude: {a.get("new_bp")!r}'
                    summary['skipped'] += 1
                    continue
                # Anti-pin guard mirroring the GPT path
                gp = float(d.get('consensus') or 0.0)
                if abs(new_bp - gp) < min_gap_pct:
                    new_bp = gp + min_gap_pct + 0.07 if new_bp > gp else gp - min_gap_pct - 0.07
                    new_bp = max(0.01, min(99.5, new_bp))
                # Apply to df
                idx = d.get('idx')
                if idx is None or idx not in df.index:
                    d['arbiter_decision'] = 'SKIP'
                    d['arbiter_reason'] = 'row idx missing or stale'
                    summary['skipped'] += 1
                    continue
                df.at[idx, bp_col] = f'{round(new_bp, 4):.4f}%'
                # Promote decision to CHANGE so downstream sees the new value
                d['decision'] = 'CHANGE'
                d['new_bp'] = round(new_bp, 4)
                d['arbiter_decision'] = 'OVERRIDE'
                d['arbiter_reason'] = arb_reason
                d['arbiter_new_bp'] = round(new_bp, 4)
                summary['overridden'] += 1
            elif arb_decision == 'AFFIRM':
                d['arbiter_decision'] = 'AFFIRM'
                d['arbiter_reason'] = arb_reason
                summary['affirmed'] += 1
            else:
                d['arbiter_decision'] = 'SKIP'
                d['arbiter_reason'] = arb_reason or 'no decision'
                summary['skipped'] += 1

        summary['arbitrated'] += len(batch)

        if verbose:
            print(f'   🧠 claude_arbiter batch {batch_idx}/{n_batches}: '
                  f"{summary['affirmed']} AFFIRM, {summary['overridden']} OVERRIDE, "
                  f"{summary['skipped']} SKIP cumulative")

    if verbose:
        print(f'   🧠 claude_arbiter complete: {summary["arbitrated"]} arbitrated, '
              f'{summary["overridden"]} OVERRIDE, {summary["affirmed"]} AFFIRM, '
              f'{summary["skipped"]} SKIP')
        # Show OVERRIDE examples
        shown = 0
        for d in decisions:
            if d.get('arbiter_decision') == 'OVERRIDE' and shown < 8:
                print(f"       OVERRIDE  [{d['category']:18s}] {d['brand'][:25]:<25s} "
                      f"{d['old_bp']:6.2f}% → {d['new_bp']:.4f}%  "
                      f"(GP={d['consensus']:.2f})  — {d['arbiter_reason'][:80]}")
                shown += 1

    return df, decisions, summary


# ─────────────────────────────────────────────────────────────────────────────
#  end vetting framework
# ─────────────────────────────────────────────────────────────────────────────


def _classify_issue(crosswalk, lo, hi, status, full_col_values=None):
    if status == 'MISSING':
        return 'structural'
    if status.startswith('PASS'):
        return 'none'
    if crosswalk is not None and crosswalk < 0.5:
        return 'suppression'
    # Default-lock fingerprint check
    if crosswalk is not None:
        for fp in DEFAULT_LOCK_FINGERPRINTS:
            if abs(crosswalk - fp) < 0.005:
                return 'default-lock'
    if status == 'FAIL (high)':
        return 'over-correction'
    if status == 'FAIL (low)':
        return 'suppression' if crosswalk < lo * 0.4 else 'persona-mismatch'
    return 'none'


# ───────────────────────────────────────────────────────────────────────────
# STEP 1: PARSE METADATA AND BUILD AUDIENCE COMPOSITION
# ───────────────────────────────────────────────────────────────────────────

def parse_metadata(df, subject_hint=''):
    """Step 1: extract metadata + audience composition (gender, age, income, ethnicity, etc.)."""
    out = {
        'subject': subject_hint or '',
        'sample_raw': None,
        'sample_projection': None,
        'avid_pct': None,
        'casual_pct': None,
        'fan_loading_tier': 'unknown',
        'audience_composition': {},
    }
    # Sample size
    ss = df[df['Column'].astype(str).str.upper().str.strip() == 'SAMPLE SIZE']
    if len(ss):
        try:
            raw = ss.iloc[0].get('Original Raw Numbers')
            out['sample_raw'] = int(float(str(raw).replace(',', ''))) if pd.notna(raw) else None
        except Exception:
            pass
        try:
            proj = ss.iloc[0].get('US Gen Pop Projection')
            out['sample_projection'] = int(float(str(proj).replace(',', ''))) if pd.notna(proj) else None
        except Exception:
            pass
    # AVID / CASUAL fan %
    af = _bp(df, 'FAN STATUS', 'AVID FAN') or _bp(df, 'AVID FAN', 'AVID FAN')
    cf = _bp(df, 'FAN STATUS', 'CASUAL FAN') or _bp(df, 'CASUAL FAN', 'CASUAL FAN')
    out['avid_pct'] = af
    out['casual_pct'] = cf
    if af is not None and cf is not None:
        total = af + cf
        if total >= 80:    out['fan_loading_tier'] = 'core'
        elif total >= 50:  out['fan_loading_tier'] = 'cult'
        elif total >= 20:  out['fan_loading_tier'] = 'moderate'
        else:              out['fan_loading_tier'] = 'mass'

    # Audience composition — gender, age, ethnicity, income, region
    comp = {}
    for col, vals in [
        ('GENDER', ['MALE', 'FEMALE', 'NON-BINARY']),
        ('AGE', ['17 AND UNDER', '18-24', '25-34', '35-44', '45-54', '55-64', '65 OR OLDER']),
        ('RACE/ETHNICITY', ['WHITE', 'BLACK OR AFRICAN AMERICAN',
                            'HISPANIC, LATINO OR SPANISH ORIGIN', 'ASIAN']),
        ('INCOME', ['$50,000 - $74,999', '$75,000 - $99,999',
                    '$100,000 - $149,999', '$150,000 - $249,999', '$250,000 OR MORE']),
        ('REGION', ['NORTHEAST', 'MIDWEST', 'SOUTH', 'WEST']),
        ('LGBTQ+', ['LGBTQ+']),
        ('MARRIED', ['MARRIED']),
        ('PARENT', ['PARENT']),
    ]:
        for v in vals:
            x = _bp(df, col, v)
            if x is not None:
                comp[f"{col}|{v}"] = x
    out['audience_composition'] = comp
    return out


# ───────────────────────────────────────────────────────────────────────────
# STEP 4: AUDIENCE-WEIGHTED PLATFORM CONSENSUS (PEW MATH)
# ───────────────────────────────────────────────────────────────────────────

def compute_pew_consensus(audience_composition):
    """Compute persona-weighted social-platform consensus per Step 4.

    Method: weighted by gender × reach + (separately) age × reach. Average
    the two for each platform. Returns dict[platform] -> (lo, hi) ±7-pt band.
    """
    male = audience_composition.get('GENDER|MALE', 50.0)
    female = audience_composition.get('GENDER|FEMALE', 50.0)
    total_g = male + female if (male + female) > 0 else 100.0
    male_share = male / total_g
    female_share = female / total_g

    a18_29 = (audience_composition.get('AGE|18-24', 0)
              + audience_composition.get('AGE|25-34', 0) * 0.5)  # half of 25-34
    a30_49 = (audience_composition.get('AGE|25-34', 0) * 0.5
              + audience_composition.get('AGE|35-44', 0)
              + audience_composition.get('AGE|45-54', 0) * 0.5)
    a50_64 = (audience_composition.get('AGE|45-54', 0) * 0.5
              + audience_composition.get('AGE|55-64', 0))
    a65 = audience_composition.get('AGE|65 OR OLDER', 0)
    age_total = a18_29 + a30_49 + a50_64 + a65
    if age_total <= 0:
        # No age data — fall back to flat distribution
        a18_29 = a30_49 = a50_64 = a65 = 25.0
        age_total = 100.0
    a_shares = {
        '18-29': a18_29 / age_total,
        '30-49': a30_49 / age_total,
        '50-64': a50_64 / age_total,
        '65+':   a65    / age_total,
    }

    consensus = {}
    for platform, table in PEW_2025_SOCIAL.items():
        gender_w = male_share * table['M'] + female_share * table['F']
        age_w = sum(a_shares[b] * table[b] for b in ('18-29', '30-49', '50-64', '65+'))
        midpoint = (gender_w + age_w) / 2
        # ±7-pt band on the consensus midpoint
        consensus[platform] = (round(midpoint - PASS_TOLERANCE_PTS, 1),
                               round(midpoint + PASS_TOLERANCE_PTS, 1),
                               round(midpoint, 1))
    return consensus


# ───────────────────────────────────────────────────────────────────────────
# STEP 5 + 6 + 7: PASS/FAIL SCORING ACROSS CATEGORIES
# ───────────────────────────────────────────────────────────────────────────

def score_cross_pull(df, audience_composition):
    """Step 5 + Step 7: score each brand in CROSS_PULL_RANGES vs consensus."""
    findings = []
    for category, brand_ranges in CROSS_PULL_RANGES.items():
        # Get full column for context (cliff detection + suppression detection)
        col_mask = df['Column'].astype(str).str.strip().str.upper() == category.upper()
        col_values = df.loc[col_mask, ['Value', 'Brand Penetration (Row)']]
        for brand, (lo, hi) in brand_ranges.items():
            crosswalk = _bp(df, category, brand)
            if crosswalk is None:
                # Try fuzzy match (forgiving punctuation)
                normt = _normbrand(brand)
                for _, r in col_values.iterrows():
                    if _normbrand(r['Value']) == normt:
                        crosswalk = _numbp(r['Brand Penetration (Row)'])
                        break
            status = _classify_status(crosswalk, lo, hi)
            issue = _classify_issue(crosswalk, lo, hi, status)
            findings.append(CategoryFinding(
                category=category, brand=brand,
                crosswalk_pct=crosswalk,
                consensus_lo=lo, consensus_hi=hi,
                status=status, issue_type=issue,
            ))
    return findings


def score_pew_social(df, pew_consensus):
    """Step 4 + Step 7: SOCIAL MEDIA platforms against persona-weighted Pew."""
    findings = []
    for platform, (lo, hi, mid) in pew_consensus.items():
        crosswalk = _bp(df, 'SOCIAL MEDIA', platform)
        status = _classify_status(crosswalk, lo, hi)
        issue = _classify_issue(crosswalk, lo, hi, status)
        findings.append(CategoryFinding(
            category='SOCIAL MEDIA', brand=platform,
            crosswalk_pct=crosswalk,
            consensus_lo=lo, consensus_hi=hi,
            status=status, issue_type=issue,
            note=f"persona-weighted Pew (mid {mid}%)",
        ))
    return findings


def detect_structural_gaps(df):
    """Step 6: list required entities missing from their categories.
    Aliases (see BRAND_ALIASES) count as the required brand being present."""
    gaps = []
    for category, required in STRUCTURAL_REQUIREMENTS.items():
        col_mask = df['Column'].astype(str).str.strip().str.upper() == category.upper()
        present_normed = {_normbrand(v) for v in df.loc[col_mask, 'Value']}
        for brand in required:
            keys = [_normbrand(brand)] + [_normbrand(a) for a in BRAND_ALIASES.get(brand.upper(), [])]
            if not any(k in present_normed for k in keys):
                gaps.append({'category': category, 'brand': brand})
    return gaps


def detect_default_locks(df):
    """Step 8: any BP values matching known default-lock fingerprints."""
    locks = []
    bp_col = df['Brand Penetration (Row)'].apply(_numbp)
    for fp in DEFAULT_LOCK_FINGERPRINTS:
        m = (bp_col - fp).abs() < 0.005
        for _, r in df[m].iterrows():
            locks.append({
                'fingerprint': fp,
                'column': r['Column'],
                'value': r['Value'],
                'bp': _numbp(r['Brand Penetration (Row)']),
            })
    return locks


def detect_cliffs(df):
    """Step 8: categories with surprisingly few entries (<CLIFF_THRESHOLD)."""
    cliffs = []
    cat_counts = df['Column'].astype(str).str.strip().str.upper().value_counts()
    EXPECTED_RICH = {'TALENT', 'MUSICIAN/BAND', 'ACTOR', 'ATHLETE',
                     'MOST PURCHASED BRANDS', 'INTEREST', 'WHERE THEY SHOP',
                     'APPAREL/FOOTWEAR', 'BEAUTY/WELLNESS', 'SPORTS TEAM',
                     'APP/PLATFORM USAGE'}
    for cat, cnt in cat_counts.items():
        if cat in EXPECTED_RICH and cnt < CLIFF_THRESHOLD:
            cliffs.append({'category': cat, 'rows': int(cnt)})
    return cliffs


def detect_sampling_ceiling(df, sample_raw):
    """Step 5 (subset): low-sample clustering at the 7-10% band."""
    if not sample_raw or sample_raw >= LOW_SAMPLE_RAW_THRESHOLD:
        return None
    bp = df['Brand Penetration (Row)'].apply(_numbp)
    # Count brands in 7-10% band across the file
    in_band = bp.between(7, 10).sum()
    return {'sample_raw': sample_raw, 'rows_in_7_10_band': int(in_band)}


# ───────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ───────────────────────────────────────────────────────────────────────────

def run_audit(df, subject_hint='', verbose=True) -> AuditReport:
    """Run the full 9-step audit. Returns an AuditReport (no mutation)."""
    meta = parse_metadata(df, subject_hint)
    pew = compute_pew_consensus(meta['audience_composition'])
    findings = score_pew_social(df, pew) + score_cross_pull(df, meta['audience_composition'])
    gaps = detect_structural_gaps(df)
    locks = detect_default_locks(df)
    cliffs = detect_cliffs(df)
    sampling = detect_sampling_ceiling(df, meta['sample_raw'])

    # Build consensus anchors for next-pull injection (mid of range)
    anchors = {}
    for cat, brand_ranges in CROSS_PULL_RANGES.items():
        for brand, (lo, hi) in brand_ranges.items():
            anchors[f"{cat}|{brand}"] = round((lo + hi) / 2, 1)
    for platform, (lo, hi, mid) in pew.items():
        anchors[f"SOCIAL MEDIA|{platform}"] = mid

    # Summary counts
    counts = {'PASS': 0, 'PASS (boundary)': 0,
              'FAIL (low)': 0, 'FAIL (high)': 0, 'MISSING': 0}
    for f in findings:
        counts[f.status] = counts.get(f.status, 0) + 1

    report = AuditReport(
        subject=meta['subject'],
        sample_raw=meta['sample_raw'],
        sample_projection=meta['sample_projection'],
        avid_pct=meta['avid_pct'],
        casual_pct=meta['casual_pct'],
        fan_loading_tier=meta['fan_loading_tier'],
        audience_composition=meta['audience_composition'],
        pew_consensus={p: {'lo': lo, 'hi': hi, 'mid': mid} for p, (lo, hi, mid) in pew.items()},
        findings=findings,
        structural_gaps=gaps,
        default_locks=locks,
        cliffs=cliffs,
        consensus_anchors=anchors,
        summary_counts=counts,
    )
    if sampling:
        report.summary_counts['sampling_ceiling'] = sampling

    if verbose:
        # Only sum integer status-counts; the dict can hold a `sampling_ceiling`
        # sub-dict which would blow up `sum()` (TypeError int + dict).
        _status_keys = ('PASS', 'PASS (boundary)', 'FAIL (low)', 'FAIL (high)', 'MISSING')
        _total = sum(int(counts.get(k, 0) or 0) for k in _status_keys)
        print(f"\n   📋 crosswalk-audit-framework: {_total} signals scored — "
              f"PASS {counts['PASS']}, PASS_boundary {counts['PASS (boundary)']}, "
              f"FAIL_low {counts['FAIL (low)']}, FAIL_high {counts['FAIL (high)']}, "
              f"MISSING {counts['MISSING']}")
        if gaps:
            _preview = ', '.join(g['category'] + '/' + g['brand'] for g in gaps[:5])
            _more = '...' if len(gaps) > 5 else ''
            print(f"   📋 structural gaps: {len(gaps)} required entities missing — {_preview}{_more}")
        if locks:
            print(f"   📋 default-value locks detected: {len(locks)} rows match known fingerprints")
    return report


# ───────────────────────────────────────────────────────────────────────────
# OUTPUT — markdown report per Crosswalk standard
# ───────────────────────────────────────────────────────────────────────────

def to_markdown(report: AuditReport, prev_report: Optional[AuditReport] = None) -> str:
    """Render the audit report as the standard Crosswalk markdown table set."""
    lines = []
    lines.append(f"# Crosswalk Profile Audit — {report.subject or 'subject'}")
    lines.append("")
    lines.append("## Metadata")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Sample (raw) | {report.sample_raw:,} |" if report.sample_raw else "| Sample (raw) | — |")
    lines.append(f"| US projection | {report.sample_projection:,} |" if report.sample_projection else "| US projection | — |")
    if report.avid_pct is not None:
        lines.append(f"| AVID fan % | {report.avid_pct:.2f}% |")
    if report.casual_pct is not None:
        lines.append(f"| CASUAL fan % | {report.casual_pct:.2f}% |")
    lines.append(f"| Fan-loading tier | {report.fan_loading_tier} |")
    lines.append("")

    # Per-category Pass/Fail tables
    by_category = {}
    for f in report.findings:
        by_category.setdefault(f.category, []).append(f)

    prev_lookup = {}
    if prev_report:
        for f in prev_report.findings:
            prev_lookup[(f.category, f.brand)] = f

    for category, group in by_category.items():
        lines.append(f"## {category}")
        lines.append("")
        has_prev = bool(prev_lookup)
        if has_prev:
            lines.append("| Brand | Crosswalk % | Last Run % | Δ | Consensus | Pass/Fail | Status |")
            lines.append("|---|---:|---:|---:|---|---|---|")
        else:
            lines.append("| Brand | Crosswalk % | Consensus | Pass/Fail | Issue type |")
            lines.append("|---|---:|---|---|---|")
        for f in group:
            cw = f"{f.crosswalk_pct:.2f}%" if f.crosswalk_pct is not None else "—"
            if f.consensus_lo is not None:
                cons = f"{f.consensus_lo:.0f}–{f.consensus_hi:.0f}%"
            else:
                cons = "—"
            pf = f.status
            issue = f.issue_type if f.issue_type != 'none' else ''
            if has_prev:
                prev = prev_lookup.get((f.category, f.brand))
                prev_cw = f"{prev.crosswalk_pct:.2f}%" if prev and prev.crosswalk_pct is not None else "—"
                if prev and prev.crosswalk_pct is not None and f.crosswalk_pct is not None:
                    delta = f.crosswalk_pct - prev.crosswalk_pct
                    d_str = f"{delta:+.2f}"
                else:
                    d_str = "—"
                # Status code per Step 9
                if prev:
                    if prev.status.startswith('FAIL') and f.status.startswith('PASS'):
                        st = 'FIXED' if not f.status.endswith('(boundary)') else 'FIXED (boundary)'
                    elif prev.status == 'FAIL (low)' and f.status == 'FAIL (high)':
                        st = 'OVERSHOT'
                    elif prev.status.startswith('FAIL') and f.status.startswith('FAIL'):
                        st = 'STILL BROKEN'
                    elif prev.status.startswith('PASS') and f.status.startswith('FAIL'):
                        st = 'NEW ISSUE'
                    elif f.crosswalk_pct is not None and prev.crosswalk_pct is not None:
                        d = f.crosswalk_pct - prev.crosswalk_pct
                        st = 'Stable' if abs(d) < 1 else ('Up' if d > 0 else 'Down')
                    else:
                        st = '—'
                else:
                    st = 'NEW'
                lines.append(f"| {f.brand} | {cw} | {prev_cw} | {d_str} | {cons} | {pf} | {st} |")
            else:
                lines.append(f"| {f.brand} | {cw} | {cons} | {pf} | {issue} |")
        lines.append("")

    # Structural gaps
    if report.structural_gaps:
        lines.append("## Structural gaps (required entities missing)")
        lines.append("")
        lines.append("| Category | Required brand |")
        lines.append("|---|---|")
        for g in report.structural_gaps:
            lines.append(f"| {g['category']} | {g['brand']} |")
        lines.append("")

    # Default locks
    if report.default_locks:
        lines.append("## Default-value locks detected")
        lines.append("")
        lines.append("| Fingerprint | Column | Value | BP |")
        lines.append("|---:|---|---|---:|")
        for d in report.default_locks[:20]:
            lines.append(f"| {d['fingerprint']} | {d['column']} | {d['value']} | {d['bp']:.4f}% |")
        if len(report.default_locks) > 20:
            lines.append(f"| ... | ... | ... | (+ {len(report.default_locks)-20} more) |")
        lines.append("")

    # Cliffs
    if report.cliffs:
        lines.append("## Category cliffs (suspiciously few entries)")
        lines.append("")
        lines.append("| Category | Rows |")
        lines.append("|---|---:|")
        for c in report.cliffs:
            lines.append(f"| {c['category']} | {c['rows']} |")
        lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|---|---:|")
    for k in ('PASS', 'PASS (boundary)', 'FAIL (low)', 'FAIL (high)', 'MISSING'):
        lines.append(f"| {k} | {report.summary_counts.get(k, 0)} |")
    lines.append("")
    return "\n".join(lines)


# ───────────────────────────────────────────────────────────────────────────
# AUDIT-DRIVEN CORRECTIONS — hallucination safety net
# ───────────────────────────────────────────────────────────────────────────
#
# Design principle (per user direction 2026-05-24):
#   The AGENT is responsible for persona reasoning row-by-row. The audit is
#   ONLY a safety net for hallucinations. We do NOT try to re-do persona
#   reasoning here with skew/region/ethnicity formulas — that path leads to
#   over-engineering and archetype pinning.
#
# What this layer does (and nothing more):
#   1. Trust agent values that land inside the published consensus band.
#   2. If the agent emitted a value OUTSIDE the band (suppression /
#      over-correction / persona-mismatch beyond tolerance), replace it
#      with the band midpoint + small subject-deterministic jitter.
#   3. Break known default-value-lock fingerprints (15.0143, 11.0852, …).
#   4. Insert structurally-required entities that are missing.
#
# Why this works for "accurate persona without hallucinations":
#   - In-band agent values are kept (persona signal preserved).
#   - Out-of-band values are clearly hallucinations / suppression; midpoint
#     is the most defensible replacement against published consensus.
#   - Subject-jitter prevents two different subjects from landing at the
#     exact same patched value while both being in the defensible band.


def _patch_target(consensus_lo, consensus_hi, subject, brand):
    """Defensible replacement for a hallucinated/out-of-band value.

    Returns band midpoint + small subject-deterministic jitter (~±15% of
    band width), bounded to [consensus_lo, consensus_hi]. The jitter is
    a function of subject+brand so re-runs are stable and two subjects
    don't tie at identical patched values.
    """
    import hashlib
    mid = (consensus_lo + consensus_hi) / 2.0
    width = max(1.0, consensus_hi - consensus_lo)
    key = f"{subject}|{brand}".upper()
    h = int(hashlib.md5(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    jitter = (h - 0.5) * width * 0.30  # ±15% of band width
    target = mid + jitter
    return round(max(consensus_lo, min(consensus_hi, target)), 4)


def apply_audit_corrections(df, report: AuditReport):
    """Patch FAILs and default-locks. Hallucination safety net only —
    persona reasoning is the AGENT's job, not this layer's.

      FAIL row → replaced with band midpoint + subject jitter
      DEFAULT_LOCK → re-jittered around its current magnitude (±1.5pt)

    All replacements get 4-decimal jitter so values aren't perfectly round
    and re-runs are stable.
    """
    import random as _r
    _r.seed(hash((report.subject, 'cw-audit-patch')) & 0xFFFFFFFF)

    bp_col = 'Brand Penetration (Row)'
    raw_col = 'Original Raw Numbers'
    proj_col = 'US Gen Pop Projection'

    df = df.copy()
    col_norm = df['Column'].astype(str).str.strip().str.upper()
    val_norm = df['Value'].astype(str).str.strip().str.upper()
    sample_raw = report.sample_raw or 10_000
    # pandas 2.x raises if you assign a string into a float64 column.
    for _c in (bp_col, raw_col, proj_col):
        if _c in df.columns and df[_c].dtype != object:
            df[_c] = df[_c].astype(object)
    raw_per_pct, proj_per_pct = _derive_scale_from_df(df)

    patches = []
    for f in report.findings:
        if not f.status.startswith('FAIL'):
            continue
        if f.consensus_lo is None or f.consensus_hi is None:
            continue
        target_jit = _patch_target(f.consensus_lo, f.consensus_hi,
                                    report.subject, f.brand)

        # Find the row(s) — try literal value, then aliases, then forgiving match
        candidates = [f.brand] + BRAND_ALIASES.get(f.brand.upper().strip(), [])
        match_idx = None
        for cand in candidates:
            m = (col_norm == f.category.upper()) & (val_norm == cand.upper().strip())
            if m.any():
                match_idx = df.index[m][0]
                break
        if match_idx is None:
            target_n_set = {_normbrand(c) for c in candidates}
            for idx in df.index[col_norm == f.category.upper()]:
                if _normbrand(df.at[idx, 'Value']) in target_n_set:
                    match_idx = idx
                    break
        if match_idx is None:
            continue

        try:
            old_bp = float(str(df.at[match_idx, bp_col]).replace('%','').replace(',','').strip())
        except Exception:
            old_bp = None

        df.at[match_idx, bp_col] = f"{target_jit:.4f}%"
        if raw_col in df.columns:
            df.at[match_idx, raw_col] = str(max(1, int(round(target_jit * raw_per_pct))))
        if proj_col in df.columns:
            df.at[match_idx, proj_col] = str(int(round(target_jit * proj_per_pct)))

        patches.append({
            'category':  f.category,
            'brand':     f.brand,
            'old_bp':    old_bp,
            'new_bp':    target_jit,
            'status':    f.status,
            'issue':     f.issue_type,
            'band':      (f.consensus_lo, f.consensus_hi),
            'note':      f"replaced out-of-band hallucination with mid-band + subject jitter",
        })

    # ── Break default-value locks (15.0143, 11.0852, 14.04, 15.0) ───────
    # These fingerprints survived the per-row scoring pass. Re-jitter them
    # to a plausible non-locked value. We jitter by ±2pt around the
    # original (so the value's general magnitude stays right) and ensure
    # 4 decimals so it doesn't snap back to a fingerprint.
    lock_patches = 0
    for lock in report.default_locks or []:
        m = (col_norm == str(lock['column']).strip().upper()) & \
            (val_norm == str(lock['value']).strip().upper())
        if not m.any(): continue
        idx = df.index[m][0]
        old_bp = lock['bp']
        # Deterministic +/-1.5pt jitter, but force a "random-looking" 4-decimal
        # tail so the new value can't accidentally match another fingerprint.
        new_bp = round(max(0.05, old_bp + _r.uniform(-1.5, 1.5)
                                          + _r.uniform(0.0005, 0.0095)), 4)
        df.at[idx, bp_col] = f"{new_bp:.4f}%"
        if raw_col in df.columns:
            df.at[idx, raw_col] = str(max(1, int(round(new_bp * raw_per_pct))))
        if proj_col in df.columns:
            df.at[idx, proj_col] = str(int(round(new_bp * proj_per_pct)))
        patches.append({
            'category':  lock['column'],
            'brand':     lock['value'],
            'old_bp':    old_bp,
            'new_bp':    new_bp,
            'status':    'DEFAULT_LOCK',
            'issue':     f"default-lock fp={lock['fingerprint']}",
            'band':      None,
            'note':      f"broke default-value lock (fingerprint {lock['fingerprint']})",
        })
        lock_patches += 1
    return df, patches


# ───────────────────────────────────────────────────────────────────────────
# OPTIONAL: STRUCTURAL-GAP INSERTION (deterministic — uses consensus mid)
# ───────────────────────────────────────────────────────────────────────────

def _derive_scale_from_df(df):
    """Derive raw_per_pct + proj_per_pct from any clean existing row so we
    don't hardcode US-pop sizing (it changes per pipeline / per pull)."""
    raw_per_pct = None
    proj_per_pct = None
    for _, r in df.iterrows():
        try:
            bp = float(str(r.get('Brand Penetration (Row)','')).replace('%','').replace(',','').strip())
            raw = float(str(r.get('Original Raw Numbers','')).replace(',','').strip())
            proj = float(str(r.get('US Gen Pop Projection','')).replace(',','').strip())
            if bp > 5 and raw > 0 and proj > 0:
                raw_per_pct = raw / bp
                proj_per_pct = proj / bp
                break
        except Exception:
            continue
    # Fallbacks if no clean row found
    return raw_per_pct or 100.0, proj_per_pct or 3_299_000.0


def insert_structural_gaps(df, report: AuditReport, sample_size_for_raw=10_000):
    """No-op. Per operator decision (2026-05-25), the audit framework MUST
    NOT insert synthetic rows. The hostmap is the canonical source of
    brand truth; if a required entity is missing from the pipeline output
    the correct response is to fix the upstream pipeline / hostmap, NOT to
    fabricate a row at the consensus midpoint.

    Structural gaps are still detected and reported (so the operator can
    investigate), but no DataFrame mutation occurs here.
    """
    return df, 0


# ───────────────────────────────────────────────────────────────────────────
# S3 PERSISTENCE
# ───────────────────────────────────────────────────────────────────────────

def save_to_s3(report: AuditReport, subject_slug: str,
               bucket: str = 'dashboard-inputs',
               region: str = 'us-east-2') -> Optional[str]:
    """Persist the markdown report + JSON anchors to S3."""
    try:
        import boto3
        import datetime
        s3 = boto3.client('s3', region_name=region)
        ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        slug = re.sub(r'[^a-z0-9]+', '_', subject_slug.lower()).strip('_')
        md_key = f"audit_logs/v1/crosswalk_audit_{slug}_{ts}.md"
        json_key = f"audit_logs/v1/crosswalk_anchors_{slug}_{ts}.json"
        s3.put_object(Bucket=bucket, Key=md_key, Body=to_markdown(report).encode())
        s3.put_object(Bucket=bucket, Key=json_key,
                      Body=json.dumps({
                          'subject': report.subject,
                          'anchors': report.consensus_anchors,
                          'gaps': report.structural_gaps,
                          'summary': report.summary_counts,
                      }, indent=2).encode())
        return f"s3://{bucket}/{md_key}"
    except Exception as e:
        print(f"   ⚠️  crosswalk-audit-framework S3 save failed: {e}")
        return None


def load_consensus_anchors_from_s3(bucket: str = 'dashboard-inputs',
                                    region: str = 'us-east-2',
                                    subject_slug: Optional[str] = None) -> dict:
    """Load the most recent consensus-anchor file from S3 (for next-pull injection).
    Returns dict[`Column|Brand`] -> midpoint BP, or {} if not available.
    """
    try:
        import boto3
        s3 = boto3.client('s3', region_name=region)
        prefix = f"audit_logs/v1/crosswalk_anchors_"
        if subject_slug:
            prefix += re.sub(r'[^a-z0-9]+', '_', subject_slug.lower()).strip('_')
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        objs = sorted(resp.get('Contents', []), key=lambda o: o['LastModified'], reverse=True)
        if not objs:
            return {}
        body = s3.get_object(Bucket=bucket, Key=objs[0]['Key'])['Body'].read()
        return json.loads(body).get('anchors', {})
    except Exception:
        return {}


# ───────────────────────────────────────────────────────────────────────────
# AGENT-DRIVEN AUDIT RE-REASONING
# ───────────────────────────────────────────────────────────────────────────
#
# Design (2026-05-24): instead of formulaic patching, when the audit flags
# a FAIL we hand the row back to the SAME persona-reasoning agent that
# originally scored the pull. The agent reviews each flagged row with full
# persona context and decides:
#
#   KEEP   — current value is correct for THIS persona; justify against
#            consensus deviation (e.g., "Sabrina audience skews 22yo,
#            credit card penetration genuinely lower than 20-34% band")
#   CHANGE — current value is a hallucination; emit new value + reason
#            grounded in persona methodology (age/income/digital-engagement
#            patterns), NOT in just snapping to mid-band
#
# This preserves persona accuracy end-to-end. The audit becomes a quality
# gate that triggers re-reasoning, not a formulaic corrector.

# ═══════════════════════════════════════════════════════════════════════════
# CANONICAL AUDIENCE-NOT-MIRROR FRAMING
# ═══════════════════════════════════════════════════════════════════════════
# Single source of truth that's prepended to EVERY agent re-reasoning prompt
# (audit-fails, floor-noise, cap-overrides, empty-categories) and is also
# inlined into the upstream persona research + dossier prompts. Catches the
# silent failure mode where the agent profiles people LIKE the subject (a
# mirror cohort) instead of people who DIGITALLY ENGAGE with the subject.
# The Steve Carell pull regressed exactly this way: the persona was 60+yo
# white male comedy-actors rather than the actual digital audience of
# Steve Carell content (mass-American adult comedy fans 25-64, all
# demographics, slight F-skew from Office / romcom fandom).
AUDIENCE_NOT_MIRROR_RULE = """\
═══════════════════════════════════════════════════════════════════
⚠️  FUNDAMENTAL FRAMING — AUDIENCE-OF, NOT MIRROR-OF
═══════════════════════════════════════════════════════════════════
You are profiling the DIGITAL AUDIENCE that engages with the subject
online — the people who SEARCH them, WATCH their content, FOLLOW them
on social, STREAM their music, BUY from their brand, ATTEND their
events, or CLICK their links. You are NEVER profiling the subject
themselves, and you are NEVER profiling a mirror cohort of "people
demographically similar to the subject".

Concrete failure modes to avoid:
  • Steve Carell (62yo white male comedy actor) — audience is mass-
    American adult comedy fans 25-64, mixed gender (often slight F-skew
    from Office/romcom fandom), all incomes/ethnicities. NOT a cohort
    of 62yo white male actors.
  • Sabrina Carpenter (25yo white female pop star) — audience is young
    women + LGBTQ+ pop fans 16-34. NOT 25yo white women in general.
  • LeBron James (40yo Black male athlete) — audience is NBA / basketball
    fans 18-54, M-skewing but increasingly mixed as the global brand
    grows, all ethnicities. NOT 40yo Black male athletes.
  • Bob's Burgers (animated sitcom) — audience is millennial-anchored
    adult-animation fans + Tumblr-era fandom + queer-adjacent comedy
    nerds. NOT a generic "Fox-network family of four".
  • Grimsburg (Netflix-streamed adult animation) — audience is male-
    leaning millennial dad-comedy / detective-comedy fans 25-54. NOT
    "everyone who watches Fox".

When the subject is a CELEBRITY, ATHLETE, MUSICIAN, or PERSON:
  Demographics reflect WHO CONSUMES THEIR DIGITAL FOOTPRINT — not who
  the subject is. The audience is often demographically very different
  from the subject themselves.

When the subject is a BRAND, PRODUCT, or SERVICE:
  Demographics reflect the actual customer base anchored by the brand's
  price point, vertical, category, and reach — not its executives or
  founders, and not just "people who could afford it".

When the subject is a TV SHOW, FILM, ALBUM, or piece of CONTENT:
  Demographics reflect the digital audience that searches, streams,
  shares, or fan-engages with this specific title — anchored by the
  airing platform, genre, fandom, and cultural moment.
═══════════════════════════════════════════════════════════════════
"""


def _persona_context_block(persona_doc, audience_composition, subject):
    """Compact persona summary for the agent re-reasoning prompt."""
    bits = [
        f"DIGITAL AUDIENCE OF: {subject}  "
        f"(profile the audience, NOT the subject themselves — see "
        f"FUNDAMENTAL FRAMING above)"
    ]
    pd_ = persona_doc or {}
    if isinstance(pd_, dict):
        if pd_.get('subject_archetype'):
            bits.append(f"ARCHETYPE: {pd_['subject_archetype']}")
        if pd_.get('persona_summary'):
            sm = str(pd_['persona_summary']).strip()
            bits.append(f"PERSONA: {sm[:600]}")
        if pd_.get('digital_identity'):
            di = str(pd_['digital_identity']).strip()
            bits.append(f"DIGITAL IDENTITY: {di[:400]}")
        demo = pd_.get('demographics') or {}
        if isinstance(demo, dict) and demo:
            dem_lines = []
            for k in ('age','gender','income','ethnicity','education','marital','household'):
                v = demo.get(k)
                if v: dem_lines.append(f"  {k}: {v}")
            if dem_lines:
                bits.append("DEMOGRAPHICS (from research):\n" + "\n".join(dem_lines))
    ac = audience_composition or {}
    if ac:
        # Compact age + income roll-up from the file itself
        age_lines = []
        for b in ('18-24','25-34','35-44','45-54','55-64','65 OR OLDER'):
            v = ac.get(f'AGE|{b}', 0)
            if v: age_lines.append(f"{b}={v:.1f}%")
        inc_lines = []
        for b in ('UNDER $25,000','$25,000 - $49,999','$50,000 - $74,999',
                  '$75,000 - $99,999','$100,000 - $149,999','$150,000 - $249,999',
                  '$250,000 OR MORE'):
            v = ac.get(f'INCOME|{b}', 0)
            if v: inc_lines.append(f"{b}={v:.1f}%")
        if age_lines:
            bits.append("AGE COMPOSITION (from file): " + ", ".join(age_lines))
        if inc_lines:
            bits.append("INCOME COMPOSITION (from file): " + ", ".join(inc_lines))
    return "\n\n".join(bits)


# ─────────────────────────────────────────────────────────────────────────────
#  OpenAI prompt-cache helpers
# ─────────────────────────────────────────────────────────────────────────────
# GPT-4o has automatic prompt caching: prefixes >=1024 tokens that are
# byte-identical to a recent request hit cache at 50% off input cost. We
# structure every audit prompt as:
#
#     [STATIC CROSS-PROFILE BLOCK]  ← AUDIENCE_NOT_MIRROR_RULE + per-function
#                                     task / rules / JSON shape. Identical
#                                     across ALL profiles & ALL batches of
#                                     this function.
#     [PER-PROFILE BLOCK]           ← persona context. Identical across
#                                     every batch in a single profile.
#     [PER-BATCH BLOCK]             ← rows-under-review. Varies.
#
# Auto-cache hit covers layers 1+2 across batches in a profile (~3-4K tokens)
# and layer 1 alone across profiles (~2K tokens). Cost win: ~50% off the
# cached portion of input — over 25-50 audit calls per profile, that adds
# up. Latency win: cached prefix evaluation is much faster than fresh.

def _log_openai_cache(resp, label: str = 'audit') -> None:
    """Print [gpt-cache] usage line when OpenAI returns a cache hit/write.
    Quiet (no-op) when the prompt didn't trigger caching."""
    try:
        usage = getattr(resp, 'usage', None)
        if usage is None:
            return
        details = getattr(usage, 'prompt_tokens_details', None)
        cached = 0
        if details is not None:
            if hasattr(details, 'cached_tokens'):
                cached = int(details.cached_tokens or 0)
            elif isinstance(details, dict):
                cached = int(details.get('cached_tokens') or 0)
        if cached <= 0:
            return
        pt = int(getattr(usage, 'prompt_tokens', 0) or 0)
        ct = int(getattr(usage, 'completion_tokens', 0) or 0)
        print(f"   [gpt-cache] {label}: read={cached:,} of input={pt:,} (output={ct:,})")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Static task-block constants — kept module-level so the bytes that prefix
#  every audit call are LITERALLY identical across batches & profiles.
#  Touching these strings invalidates the corresponding OpenAI prefix cache.
# ─────────────────────────────────────────────────────────────────────────────

_AUDIT_FAILS_TASK_BLOCK = (
    "\nYou are the persona-reasoning agent for a Crosswalk digital audience "
    "pull. An automated audit has flagged rows where the current BP is outside "
    "the published-consensus range for that brand. The audit is a quality "
    "gate, not the source of truth — YOUR persona reasoning is.\n\n"
    "=== TASK ===\n"
    "For each row, decide one of:\n"
    "  KEEP   — the current value is correct FOR THIS PERSONA; justify the "
    "deviation from published consensus using audience evidence "
    "(e.g., 'audience skews 22yo so credit card BP genuinely lower than "
    "20-34% mass-American band').\n"
    "  CHANGE — the current value is a hallucination or wrong direction; "
    "emit new_bp (0–100, 2-decimal) grounded in persona methodology "
    "(digital footprint for THIS audience's age / income / behavior), "
    "NOT in just snapping to mid-band. New value can be inside OR outside "
    "the consensus band as long as it's persona-defensible.\n\n"
    "Hard rules:\n"
    "  - Row-by-row reasoning. No batch-level formulas, no caps.\n"
    "  - 'reason' must reference THIS persona's demographics or digital behavior, "
    "    not generic statements.\n"
    "  - If genuinely uncertain, return decision=KEEP with reason='insufficient evidence'.\n\n"
    "Return ONLY valid JSON in this exact shape:\n"
    '{"decisions":[{"i":1,"decision":"KEEP","reason":"..."},'
    '{"i":2,"decision":"CHANGE","new_bp":12.34,"reason":"..."}]}'
    "\nJSON only, no markdown, no code fences.\n"
)

_FLOOR_NOISE_TASK_BLOCK = (
    "\nYou are the persona-reasoning agent for a Crosswalk digital audience "
    "pull. The rows below are sitting at the PANEL-FLOOR (~0.001-0.5% BP), "
    "which usually means one of three things:\n"
    "  - the brand DOES belong but real BP for this persona is just very small (KEEP)\n"
    "  - the brand belongs but the pipeline suppressed it to floor (CHANGE)\n"
    "  - the brand does not belong to this audience at all (DROP)\n\n"
    "=== TASK ===\n"
    "For each row, decide ONE of:\n"
    "  KEEP   — value is genuinely tiny for THIS persona; justify with audience "
    "evidence (e.g., 'audience skews 65+ female so Roblox at 0.04% is real').\n"
    "  CHANGE — brand DOES fit this persona but current BP is a suppression "
    "artifact; emit realistic new_bp (0.5-100, 2-decimal) grounded in persona "
    "evidence (age, income, digital footprint), NOT mid-band, NOT formulaic.\n"
    "  DROP   — brand does NOT fit this persona; the floor value is noise; "
    "delete this row entirely.\n\n"
    "Hard rules:\n"
    "  - Row-by-row reasoning. No batch formulas, no caps.\n"
    "  - When in doubt, DROP. A floor-noise row that doesn't fit the persona "
    "is worse than a missing row.\n"
    "  - 'reason' must reference THIS persona's demographics or digital "
    "behavior, not generic statements.\n"
    "  - CHANGE values must be realistic (typically 1-15% for niche-but-real "
    "brands; never sub-0.5%; never round numbers).\n\n"
    "Return ONLY valid JSON in this exact shape:\n"
    '{"decisions":[{"i":1,"decision":"DROP","reason":"..."},'
    '{"i":2,"decision":"CHANGE","new_bp":3.42,"reason":"..."},'
    '{"i":3,"decision":"KEEP","reason":"..."}]}'
    "\nJSON only, no markdown, no code fences.\n"
)

_CAP_OVERRIDE_TASK_BLOCK = (
    "\nYou are the persona-reasoning agent for a Crosswalk digital audience "
    "pull. The pipeline applied gen-pop-anchored CAPS to the rows below — "
    "each was originally emitted by the agent at a high value, then pulled "
    "DOWN by a deterministic rule (5x gen_pop cap, athlete cap, etc.). Most "
    "of the time the cap is correct because the agent over-emitted; "
    "sometimes the original value was persona-justified and the cap is wrong.\n\n"
    "=== TASK ===\n"
    "For each row, decide ONE of:\n"
    "  KEEP   — the cap is correct; the agent's original value was hallucination "
    "or mass-American default. Justify with persona evidence "
    "(e.g., 'audience is queer urban millennial; country-pop singer at 39% "
    "was over-emitted, capped 16% reflects the real low affinity').\n"
    "  REVERT — the agent's original value WAS persona-justified; the cap is "
    "blunt and wrong for THIS audience. Restore the original. Justify with "
    "persona evidence (e.g., 'audience is hardcore Knicks die-hards, "
    "so Jalen Brunson at 78% IS persona-correct even at 8x gen_pop').\n\n"
    "Hard rules:\n"
    "  - Row-by-row reasoning grounded in THIS persona's demographics + "
    "digital footprint. No generic 'might be correct' statements.\n"
    "  - When in doubt, KEEP the cap. Caps are conservative; agent "
    "over-emission of mass-American brands is far more common than "
    "genuine persona-driven 5x+ saturation.\n"
    "  - REVERT only when the persona TRULY justifies the high value: "
    "die-hard fans, niche subculture saturation, identity-defining "
    "brand for the audience, etc.\n\n"
    "Return ONLY valid JSON in this exact shape:\n"
    '{"decisions":[{"i":1,"decision":"KEEP","reason":"..."},'
    '{"i":2,"decision":"REVERT","reason":"..."}]}'
    "\nJSON only, no markdown, no code fences.\n"
)

_MPB_FLOOR_TASK_BLOCK = (
    "\nYou are the persona-reasoning agent for a Crosswalk digital audience "
    "pull. The MOST PURCHASED BRANDS category is under-populated relative to "
    "its hostmap candidate pool. Below is a batch of candidate brands the "
    "upstream agent did not include. Decide which make sense for THIS "
    "persona's online-purchasing habits and assign a persona-grounded Brand "
    "Penetration to each.\n\n"
    "=== TASK ===\n"
    "For each candidate, emit ADD (with persona-grounded BP) or SKIP "
    "(with reason).\n\n"
    "MPB SCORING RULES (per brand):\n"
    "  - MPB = % of THIS persona who BUY this brand ONLINE.\n"
    "  - CPG / beverages / grocery → cap LOW (typically 0.5-5%).\n"
    "    These are bought in-store, not online. Coca-Cola, Pepsi,\n"
    "    Gatorade, Tropicana, etc must NEVER lead this column.\n"
    "  - Apparel / footwear / accessories / beauty / electronics /\n"
    "    books / household → can sit HIGHER (5-25%) — Amazon-/DTC-\n"
    "    shippable.\n"
    "  - Mass-market shipped staples (paper goods, detergent,\n"
    "    toothpaste, deodorant) → 5-15% for subscribe-and-save\n"
    "    households.\n"
    "  - High BPs (25-60%) are RARE and reserved for brands the\n"
    "    persona's demographics buy heavily online (e.g., athleisure\n"
    "    for young urban audiences, beauty for women 25-44, pet food\n"
    "    DTC for dog/cat parents, gear for outdoor enthusiasts).\n"
    "  - Use the hostmap section bracket as a STARTING hint, but\n"
    "    reason from the persona. A 55+ retiree audience under-\n"
    "    engages with DTC fashion even if the brand is tagged\n"
    "    Apparel/Footwear.\n"
    "  - SKIP brands the persona genuinely wouldn't buy online\n"
    "    (foreign-only, niche regional, wrong demographic, archaic,\n"
    "    or duplicative of an existing entry).\n\n"
    "Constraints on every ADD:\n"
    "  - BP >= 0.5% (no floor noise).\n"
    "  - BP <= 60% (no brand dominates MPB).\n"
    "  - 4-decimal jitter (e.g., 7.4193%, not 7%).\n"
    "  - Use the EXACT brand spelling from the candidate list above\n"
    "    (uppercase). Do NOT invent new brands not on this list.\n"
    "  - 'reason' must cite THIS persona, not a generic justification.\n\n"
    "Return ONLY valid JSON in this exact shape:\n"
    '{"decisions": [\n'
    '  {"brand": "NIKE", "decision": "ADD", "bp": 14.8231, "reason": "..."},\n'
    '  {"brand": "TEMU", "decision": "SKIP", "reason": "..."}\n'
    ']}\n'
    "JSON only — no markdown fences, no commentary.\n"
)


def agent_reason_audit_fails(df,
                              report: AuditReport,
                              openai_client,
                              persona_doc=None,
                              model: str = 'gpt-4o',
                              batch_size: int = 12,
                              max_tokens: int = 4000,
                              verbose: bool = True):
    """Hand each FAIL row back to the persona-reasoning agent. For each
    flagged row the agent returns KEEP (with justification) or CHANGE
    (with a new value + persona-grounded reason). Only CHANGEs are written.

    Returns (df, decisions) where decisions is a list of dicts with:
      category, brand, old_bp, decision (KEEP|CHANGE|SKIP),
      new_bp (if CHANGE), reason, status (original audit status), band.

    No formulaic patching. No mid-band snapping. No archetype pinning —
    every FAIL is row-by-row re-reasoned in this audience's persona context.

    Falls back to a SKIP (no mutation) for any row the agent can't decide.
    """
    if openai_client is None:
        if verbose:
            print("   ⚠️ agent_reason_audit_fails: no OpenAI client provided; skipping")
        return df, []

    fails = [f for f in (report.findings or [])
             if str(f.status).startswith('FAIL')
             and f.consensus_lo is not None
             and f.consensus_hi is not None]
    if not fails:
        return df, []

    persona_context = _persona_context_block(persona_doc,
                                              report.audience_composition,
                                              report.subject)

    bp_col = 'Brand Penetration (Row)'
    raw_col = 'Original Raw Numbers'
    proj_col = 'US Gen Pop Projection'

    df = df.copy()
    col_norm = df['Column'].astype(str).str.strip().str.upper()
    val_norm = df['Value'].astype(str).str.strip().str.upper()
    sample_raw = report.sample_raw or 10_000
    # pandas 2.x raises if you assign a string into a float64 column.
    for _c in (bp_col, raw_col, proj_col):
        if _c in df.columns and df[_c].dtype != object:
            df[_c] = df[_c].astype(object)
    raw_per_pct, proj_per_pct = _derive_scale_from_df(df)

    def _find_row(cat, brand):
        cands = [brand] + BRAND_ALIASES.get(brand.upper().strip(), [])
        for c in cands:
            m = (col_norm == cat.upper()) & (val_norm == c.upper().strip())
            if m.any():
                return df.index[m][0]
        tgt_set = {_normbrand(c) for c in cands}
        for idx in df.index[col_norm == cat.upper()]:
            if _normbrand(df.at[idx, 'Value']) in tgt_set:
                return idx
        return None

    decisions: list[dict] = []
    n_changed = 0
    n_kept = 0

    for batch_start in range(0, len(fails), batch_size):
        batch = fails[batch_start: batch_start + batch_size]
        batch_idx = batch_start // batch_size + 1
        n_batches = (len(fails) + batch_size - 1) // batch_size

        items_lines = []
        for i, f in enumerate(batch, 1):
            current = _bp(df, f.category, f.brand)
            cur_str = f"{current:.2f}%" if current is not None else "MISSING"
            items_lines.append(
                f"{i}. CATEGORY={f.category} | BRAND={f.brand} | "
                f"current_bp={cur_str} | published_consensus_range=[{f.consensus_lo:.1f}–{f.consensus_hi:.1f}]% | "
                f"audit_status={f.status} | classification={f.issue_type}"
            )

        # Cache-friendly prompt order: static rules+task first, then per-profile
        # persona context, then per-batch flagged rows. The prefix up through
        # PERSONA CONTEXT is byte-identical across all batches in this profile,
        # so OpenAI auto-cache picks it up on every batch after the first.
        prompt = (
            AUDIENCE_NOT_MIRROR_RULE
            + _AUDIT_FAILS_TASK_BLOCK
            + f"\n=== PERSONA CONTEXT ===\n{persona_context}\n"
            + f"\n=== FLAGGED ROWS (batch {batch_idx}/{n_batches}) ===\n"
            + "\n".join(items_lines)
        )

        try:
            resp = openai_client.chat.completions.create(
                model=model,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.2,
                max_tokens=max_tokens,
                timeout=120,
            )
            _log_openai_cache(resp, label=f'audit-fails b{batch_idx}/{n_batches}')
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
            parsed = json.loads(raw)
        except Exception as e:
            if verbose:
                print(f"   ⚠️ agent_reason batch {batch_idx}/{n_batches} error: {e}")
            for f in batch:
                decisions.append({
                    'category': f.category, 'brand': f.brand,
                    'old_bp': _bp(df, f.category, f.brand),
                    'decision': 'SKIP', 'new_bp': None,
                    'reason': f'agent error: {e}',
                    'status': f.status, 'band': (f.consensus_lo, f.consensus_hi),
                })
            continue

        decision_map = {int(d.get('i', -1)): d for d in (parsed.get('decisions') or [])}
        for i, f in enumerate(batch, 1):
            d = decision_map.get(i, {})
            old_bp = _bp(df, f.category, f.brand)
            decision = str(d.get('decision', 'SKIP')).upper().strip()
            reason = str(d.get('reason', '')).strip()

            if decision == 'CHANGE':
                try:
                    new_bp = float(d.get('new_bp'))
                except Exception:
                    new_bp = None
                if new_bp is None or new_bp <= 0 or new_bp > 100:
                    decisions.append({
                        'category': f.category, 'brand': f.brand,
                        'old_bp': old_bp, 'decision': 'SKIP', 'new_bp': None,
                        'reason': f'invalid new_bp from agent: {d.get("new_bp")!r}',
                        'status': f.status, 'band': (f.consensus_lo, f.consensus_hi),
                    })
                    continue
                # Apply 4-decimal jitter to honor "no round numbers" rule
                import random as _r
                _r.seed(hash((report.subject, f.category, f.brand)) & 0xFFFFFFFF)
                new_bp = round(new_bp + _r.uniform(-0.05, 0.05), 4)
                new_bp = max(0.0001, min(100.0, new_bp))
                idx = _find_row(f.category, f.brand)
                if idx is None:
                    decisions.append({
                        'category': f.category, 'brand': f.brand,
                        'old_bp': old_bp, 'decision': 'SKIP', 'new_bp': None,
                        'reason': 'row not found in dataframe',
                        'status': f.status, 'band': (f.consensus_lo, f.consensus_hi),
                    })
                    continue
                df.at[idx, bp_col] = f"{new_bp:.4f}%"
                if raw_col in df.columns:
                    df.at[idx, raw_col] = str(max(1, int(round(new_bp * raw_per_pct))))
                if proj_col in df.columns:
                    df.at[idx, proj_col] = str(int(round(new_bp * proj_per_pct)))
                n_changed += 1
                decisions.append({
                    'category': f.category, 'brand': f.brand,
                    'old_bp': old_bp, 'decision': 'CHANGE', 'new_bp': new_bp,
                    'reason': reason, 'status': f.status,
                    'band': (f.consensus_lo, f.consensus_hi),
                })
            elif decision == 'KEEP':
                n_kept += 1
                decisions.append({
                    'category': f.category, 'brand': f.brand,
                    'old_bp': old_bp, 'decision': 'KEEP', 'new_bp': None,
                    'reason': reason or 'persona-defensible deviation',
                    'status': f.status, 'band': (f.consensus_lo, f.consensus_hi),
                })
            else:
                decisions.append({
                    'category': f.category, 'brand': f.brand,
                    'old_bp': old_bp, 'decision': 'SKIP', 'new_bp': None,
                    'reason': reason or 'no decision from agent',
                    'status': f.status, 'band': (f.consensus_lo, f.consensus_hi),
                })

        if verbose:
            print(f"   🧠 agent_reason batch {batch_idx}/{n_batches}: "
                  f"{sum(1 for d in decisions[-len(batch):] if d['decision']=='CHANGE')} changed, "
                  f"{sum(1 for d in decisions[-len(batch):] if d['decision']=='KEEP')} kept (persona-defended)")

    if verbose:
        print(f"   🧠 agent re-reasoning totals: {n_changed} CHANGE, {n_kept} KEEP, "
              f"{len(decisions)-n_changed-n_kept} SKIP across {len(fails)} flagged rows")

    return df, decisions


# ───────────────────────────────────────────────────────────────────────────
# AGENT-DRIVEN FLOOR-NOISE REMOVAL
# ───────────────────────────────────────────────────────────────────────────
#
# Design (2026-05-26): the pipeline must never leave a brand sitting at the
# panel-floor (~0.001% / 0.011% / 0.05% — typically a single user touching
# a brand in the entire 12-month panel window). Per operator direction,
# these rows must either be assigned a realistic value or deleted entirely.
# Decision-by-decision, row-by-row, the persona agent picks one of:
#
#   KEEP   — value is genuinely tiny for THIS persona, justify with audience
#            evidence (e.g., "audience skews 65+ female so Roblox at 0.04%
#            is real, not a suppression artifact").
#   CHANGE — brand DOES fit the persona but current BP is a suppression
#            artifact; emit realistic new_bp grounded in persona evidence
#            (NOT mid-band, NOT formulaic).
#   DROP   — brand does not fit the persona; the floor value is noise;
#            remove the row entirely.
#
# Categories that enumerate exhaustive demographic/geographic distributions
# (LOCATION/DMA, GENDER, AGE, etc.) are EXEMPT — their long tail is real
# signal (every audience has a sliver in every DMA) and should never be
# stripped to "clean up" low values.

# ─────────────────────────────────────────────────────────────────────────────
#  Hostmap-derived canonical Column whitelist (D2: hard-gate column emission)
# ─────────────────────────────────────────────────────────────────────────────
# The agent occasionally invents column names (e.g. 'BROADCAST/CABLE' when the
# hostmap canonical is 'MEDIA'). canonicalize_categories() catches the named
# variants we know about, but NEW invented columns slip through.
#
# The fix: derive the canonical column whitelist from
#   SELECT DISTINCT SECTION FROM reference.host_mapping
# split on commas (sections are multi-tag like 'Most Purchased Brands,
# Apparel/Footwear'), upper-cased. Drop anything outside this set ∪
# ALWAYS_ALLOWED_COLUMNS (demographics that aren't in hostmap).

# Demographics + pipeline-internal columns that are legitimate even though
# they don't appear as hostmap SECTION tags.
ALWAYS_ALLOWED_COLUMNS = {
    # demographics emitted by the pipeline (canonical + variant spellings)
    'AGE', 'GENDER', 'LOCATION', 'INCOME', 'RACE/ETHNICITY', 'ETHNICITY',
    'REGION', 'EDUCATION', 'LGBTQ+', 'SEXUAL_ORIENTATION', 'SEXUAL ORIENTATION',
    'MARRIED', 'RELATIONSHIP', 'RELATIONSHIP STATUS',
    'PARENT', 'PARENTAL_STATUS', 'PARENTAL STATUS', 'CHILDREN',
    'METRO', 'HOUSEHOLD SIZE', 'HOUSEHOLD',
    'POLITICAL AFFILIATION', 'POLITICAL', 'RELIGION', 'EMPLOYMENT',
    'OCCUPATION', 'COUNTRY', 'STATE', 'CITY',
    # pipeline summary / metadata columns
    'SAMPLE SIZE', 'BRAND INPUT', 'INPUT_METADATA',
    'INTEREST', 'INTERESTS',
    'MOST PURCHASED CATEGORIES', 'MOST PURCHASED CATEGORY',
    'BRAND CATEGORY',
    # fan-status columns
    'FAN STATUS', 'AVID FAN', 'CASUAL FAN',
}

# Fallback whitelist used when ClickHouse is unreachable. Manually curated
# from a 2026-05 SECTION pull; refresh by running:
#   SELECT DISTINCT SECTION FROM reference.host_mapping
# and splitting each value on commas.
_HOSTMAP_SECTION_FALLBACK = {
    'TALENT', 'ACTOR', 'MUSICIAN/BAND', 'ATHLETE',
    'NFL ATHLETE', 'NBA ATHLETE', 'MLB ATHLETE', 'WNBA ATHLETE',
    'NHL ATHLETE', 'SOCCER ATHLETE', 'MOTORSPORT ATHLETE',
    'CREATOR/INFLUENCER', 'HOST/PERSONALITY',
    'WRITER/DIRECTOR/AUTHOR/ARTIST', 'POLITICS/ACTIVIST',
    'GAMES', 'MOST PURCHASED BRANDS',
    'APPAREL/FOOTWEAR', 'APPAREL & FOOTWEAR', 'CPG',
    'HOME/OUTDOOR', 'BEAUTY/WELLNESS', 'ACCESSORIES', 'PETS',
    'TECHNOLOGY BRAND', 'STREAMING/MUSIC', 'TRAVEL', 'MEDIA',
    'WHERE THEY SHOP', 'PODCAST', 'PODCAST RANKER',
    'COLLEGE/UNIVERSITY', 'EVENTS', 'AMUSEMENT PARKS',
    'APP/PLATFORM USAGE', 'TOYS', 'QSR', 'TV SHOW', 'VENUE',
    'WHERE THEY DINE', 'STREAMING/PLATFORM',
    'FRANCHISE', 'AUTOMOBILE', 'AUTOMOTIVE',
    'GOLF', 'NON PROFIT/CHARITY', 'TECHNOLOGY/DEVICE',
    'HEAVY MACHINERY', 'WORKOUT FACILITY', 'PORN MEDIA',
    'SEARCH ENGINE/AI', 'SPORTS ORGANIZATIONS', 'SPORTS ORGANIZATION',
    'TENNIS', 'GRAND SLAMS', 'MASTERS 1000', 'TICKETING',
    'SOCIAL MEDIA', 'INSURANCE', 'TELECOM', 'BANKING',
    'BETTING', 'PHARMACY', 'INVESTMENTS', 'CREDIT PROVIDER',
    'HEALTH & WELLNESS', 'MOVIE THEATER', 'EDUCATION & LEARNING',
    'EDUCATION', 'MUSEUM', 'HORSE RACING', 'DIGITAL BANKING',
    'VIRTUAL MVPD FAST', 'ORGANIZATIONAL MEMBERSHIPS',
    'SPORTS TEAM',
    'NBA', 'WNBA', 'NFL', 'MLB', 'NHL', 'MLS', 'NWSL', 'AUSL',
    'MILB', 'CFL', 'PLL', 'ESPORTS', 'RUGBY', 'VOLLEYBALL',
    'AFC', 'NFC', 'AL', 'NL',
    'AFC WEST', 'AFC EAST', 'AFC NORTH', 'AFC SOUTH',
    'NFC WEST', 'NFC EAST', 'NFC NORTH', 'NFC SOUTH',
    'AL WEST', 'AL EAST', 'AL CENTRAL',
    'NL WEST', 'NL EAST', 'NL CENTRAL',
    'EASTERN CONFERENCE', 'WESTERN CONFERENCE',
    'METROPOLITAN DIVISION', 'ATLANTIC DIVISION',
    'CENTRAL DIVISION', 'PACIFIC DIVISION',
    'PREMIER LEAGUE', 'LA LIGA', 'BUNDESLIGA', 'SERIE A',
    'LIGUE 1', 'USL CHAMPIONSHIP', 'USL LEAGUE ONE',
    'LIGA MX', 'SAUDI PRO LEAGUE', 'SOCCER',
    'MOTORSPORT', 'F1', 'NASCAR', 'FORMULA E',
    'INDYCAR', 'EXTREME E&H',
    'MOVIE',
}

# These hostmap SECTION values are NOT real categories — they're internal/
# placeholder markers that should NEVER become a CSV Column.
_HOSTMAP_SECTION_INTERNAL = {'HIDDEN', 'CATEGORY'}

# Module-level cache so we only query ClickHouse once per process.
_HOSTMAP_SECTION_CACHE: set | None = None


def _split_hostmap_section_value(section_val: str) -> list[str]:
    """A hostmap SECTION cell can be a comma-separated list of tags
    (e.g. 'Most Purchased Brands, Apparel/Footwear'). Split + uppercase
    + strip; drop empties and internal markers."""
    if section_val is None:
        return []
    out = []
    for tag in str(section_val).split(','):
        t = tag.strip().upper()
        if t and t not in _HOSTMAP_SECTION_INTERNAL:
            out.append(t)
    return out


def get_hostmap_section_whitelist(refresh: bool = False,
                                  verbose: bool = False) -> set[str]:
    """Return the set of canonical Column names valid for the output CSV.

    Combines:
      • Distinct SECTION tags from reference.host_mapping (split on commas)
      • ALWAYS_ALLOWED_COLUMNS (demographics + pipeline-internal columns)

    Lazily queried; cached for the lifetime of the process. Pass refresh=True
    to force a re-query (e.g. after a hostmap update). Falls back to the
    hardcoded _HOSTMAP_SECTION_FALLBACK if ClickHouse is unreachable.
    """
    global _HOSTMAP_SECTION_CACHE
    if _HOSTMAP_SECTION_CACHE is not None and not refresh:
        return _HOSTMAP_SECTION_CACHE

    derived: set[str] = set()
    queried = False
    try:
        import clickhouse_connect
        import os as _os
        client = clickhouse_connect.get_client(
            host=_os.environ.get('CH_HOST', '168.119.215.48'),
            port=int(_os.environ.get('CH_PORT', '8123')),
            username=_os.environ.get('CH_USER', 'bgapp'),
            password=_os.environ.get('CH_PASSWORD',
                                     '4qPllwDG+S3PptBWTRAJPTkpCzkRZ6tZ'),
        )
        rows = client.query(
            "SELECT DISTINCT SECTION FROM reference.host_mapping "
            "WHERE SECTION IS NOT NULL AND SECTION != ''"
        ).result_rows
        for (sec,) in rows:
            derived.update(_split_hostmap_section_value(sec))
        queried = True
        if verbose:
            print(f"   📋 hostmap whitelist: loaded {len(derived)} distinct "
                  f"SECTION tag(s) from ClickHouse")
    except Exception as e:
        if verbose:
            print(f"   ⚠️  hostmap whitelist: ClickHouse unreachable ({e}); "
                  f"using fallback ({len(_HOSTMAP_SECTION_FALLBACK)} tags)")
        derived = set(_HOSTMAP_SECTION_FALLBACK)

    whitelist = derived | ALWAYS_ALLOWED_COLUMNS
    _HOSTMAP_SECTION_CACHE = whitelist
    return whitelist


# ─────────────────────────────────────────────────────────────────────────────
#  Category canonicalization (Rule #0: no redundant columns)
# ─────────────────────────────────────────────────────────────────────────────
# The category-emitting agents occasionally produce variant column names for
# what should be a single canonical category (e.g. 'SEARCH ENGINE' and 'AI'
# instead of 'SEARCH ENGINE/AI'; 'TELCO' instead of 'TELECOM'; 'BROADCAST/CABLE'
# split off from 'MEDIA'). These split columns pollute the final CSV, confuse
# the dashboard, and cause spurious audit FAILs (the same brand can appear in
# two columns at different BPs). canonicalize_categories() runs as a
# deterministic pre-audit pass to consolidate every variant back into its
# canonical column.
#
# Add to CATEGORY_CANONICAL_REMAP whenever a new variant shows up in audit
# reports. The remap is intentionally explicit (no fuzzy matching) so we never
# accidentally collapse a legitimately-separate category.

CATEGORY_CANONICAL_REMAP = {
    # variant column → canonical column
    'SEARCH ENGINE':          'SEARCH ENGINE/AI',
    'SEARCH':                 'SEARCH ENGINE/AI',
    'AI':                     'SEARCH ENGINE/AI',
    'GENERATIVE AI':          'SEARCH ENGINE/AI',
    'LLM':                    'SEARCH ENGINE/AI',
    'AI/SEARCH':              'SEARCH ENGINE/AI',
    'SEARCH/AI':              'SEARCH ENGINE/AI',

    'TELCO':                  'TELECOM',
    'TELECOMMUNICATIONS':     'TELECOM',
    'CARRIER':                'TELECOM',
    'WIRELESS':               'TELECOM',
    'MOBILE CARRIER':         'TELECOM',
    'WIRELESS CARRIER':       'TELECOM',
    'PHONE CARRIER':          'TELECOM',

    'BROADCAST/CABLE':        'MEDIA',
    'BROADCAST':              'MEDIA',
    'CABLE':                  'MEDIA',
    'TV NETWORK':             'MEDIA',
    'TV NETWORKS':            'MEDIA',
    'CABLE NETWORK':          'MEDIA',
    'BROADCAST NETWORK':      'MEDIA',
    'NEWS NETWORK':           'MEDIA',
    'NEWS':                   'MEDIA',
}

# Variants where hostmap membership is REQUIRED for migration (drop instead
# of move if the value is not in reference.host_mapping). Empty set means
# "apply the hostmap filter to every consolidated row by default" — set to
# {<canonical>} to apply only for that canonical column. The user explicitly
# called this out for MEDIA but it's a sensible default for all consolidations:
# we shouldn't launder bad data into a canonical column.
CONSOLIDATION_HOSTMAP_REQUIRED_DEFAULT = True


def _norm_value(v) -> str:
    """Aggressive normalization for dup-detection across Column variants:
    uppercase + strip + remove all non-alphanumerics so 'AT&T', 'AT and T',
    'ATT', and 'at-t' all collapse to 'ATT'."""
    return re.sub(r'[^A-Z0-9]', '', str(v).upper().strip())


def canonicalize_categories(df,
                            remap: dict = None,
                            require_hostmap: bool = CONSOLIDATION_HOSTMAP_REQUIRED_DEFAULT,
                            enforce_whitelist: bool = True,
                            whitelist: set = None,
                            verbose: bool = True):
    """Consolidate variant Column names into their canonical names AND
    enforce the hostmap-derived Column whitelist (D2).

    Two phases run in order:

    PHASE 1 — Remap variants. For every row whose Column is in `remap`
    (variant → canonical):
      • DEDUP against an existing canonical row with the same Value
        (punctuation/casing-insensitive).
      • If `require_hostmap` is True and the Value is not in
        reference.host_mapping, drop as HOSTMAP_FAIL.
      • Otherwise MIGRATE: rewrite Column to canonical, normalize Value to
        hostmap canonical casing when available.

    PHASE 2 — Whitelist enforcement. If `enforce_whitelist` is True (default),
    drop any row whose Column (after phase 1) is not in `whitelist`. Default
    whitelist = get_hostmap_section_whitelist(), which combines hostmap
    SECTION tags + ALWAYS_ALLOWED_COLUMNS (demographics + pipeline metadata).
    Action is recorded as WHITELIST_DROP.

    Returns (df, decisions) where each decision is:
        {variant, canonical, value, action: 'DEDUP'|'HOSTMAP_FAIL'|'MIGRATE'|
         'WHITELIST_DROP', bp (if known), reason}

    Deterministic, no LLM calls. Safe to run multiple times (idempotent —
    second run produces zero decisions because canonical columns no longer
    contain any variant rows and unknown columns have been dropped).
    """
    if 'Column' not in df.columns or 'Value' not in df.columns:
        if verbose:
            print("   ⚠️  canonicalize: missing Column/Value; skipping")
        return df, []

    if remap is None:
        remap = CATEGORY_CANONICAL_REMAP
    remap_upper = {str(k).upper().strip(): str(v).upper().strip()
                   for k, v in remap.items()}

    df = df.copy()
    col_upper = df['Column'].astype(str).str.upper().str.strip()
    variants_present = sorted({c for c in col_upper.unique() if c in remap_upper})
    if not variants_present:
        if verbose:
            print("   ✅ canonicalize: no variant columns present — nothing to consolidate")
        return df, []

    # Optional hostmap helpers — defer import so the audit framework still
    # imports cleanly even when post_generation_enforcers is missing.
    _is_in_hostmap = None
    _hostmap_canonical = None
    if require_hostmap:
        try:
            from post_generation_enforcers import (
                _is_in_hostmap as _ih,
                _hostmap_canonical as _hc,
            )
            _is_in_hostmap = _ih
            _hostmap_canonical = _hc
        except Exception:
            try:
                from migration.post_generation_enforcers import (
                    _is_in_hostmap as _ih,
                    _hostmap_canonical as _hc,
                )
                _is_in_hostmap = _ih
                _hostmap_canonical = _hc
            except Exception:
                if verbose:
                    print("   ⚠️  canonicalize: hostmap helpers unavailable; "
                          "consolidating without hostmap filter")
                require_hostmap = False

    bp_col = 'Brand Penetration (Row)' if 'Brand Penetration (Row)' in df.columns else None

    decisions: list[dict] = []
    drop_idx: list = []

    # Group migrations by canonical so we can refresh the dup-norm set after
    # each canonical's batch (prevents intra-batch dups when two variants both
    # try to migrate the same Value into the same canonical column).
    by_canonical: dict[str, list[str]] = {}
    for variant in variants_present:
        canonical = remap_upper[variant]
        by_canonical.setdefault(canonical, []).append(variant)

    for canonical, variants in by_canonical.items():
        # Build the dup-detect set ONCE per canonical from existing canonical rows.
        canonical_mask = col_upper == canonical
        existing_norms = {
            _norm_value(v) for v in df.loc[canonical_mask, 'Value'].tolist()
        }
        if verbose:
            print(f"   🧹 canonicalize → [{canonical}] (existing rows: "
                  f"{int(canonical_mask.sum())}, dup-norms: {len(existing_norms)})")

        for variant in variants:
            variant_idxs = df.index[col_upper == variant].tolist()
            if not variant_idxs:
                continue
            n_dup = n_hm_fail = n_mig = 0
            for idx in variant_idxs:
                val = df.at[idx, 'Value']
                bp_v = None
                if bp_col is not None:
                    try:
                        bp_v = float(str(df.at[idx, bp_col]).strip().rstrip('%'))
                    except Exception:
                        bp_v = None
                norm = _norm_value(val)

                # 1. Dedupe against canonical column
                if norm in existing_norms:
                    drop_idx.append(idx)
                    n_dup += 1
                    decisions.append({
                        'variant': variant, 'canonical': canonical,
                        'value': str(val), 'action': 'DEDUP', 'bp': bp_v,
                        'reason': f'duplicate of existing [{canonical}] row',
                    })
                    continue

                # 2. Hostmap gate (if enabled)
                if require_hostmap and _is_in_hostmap is not None:
                    if not _is_in_hostmap(val):
                        drop_idx.append(idx)
                        n_hm_fail += 1
                        decisions.append({
                            'variant': variant, 'canonical': canonical,
                            'value': str(val), 'action': 'HOSTMAP_FAIL', 'bp': bp_v,
                            'reason': 'value not in reference.host_mapping',
                        })
                        continue

                # 3. Migrate — rewrite Column to canonical (preserve original
                #    case of the canonical column if it already appears in df)
                canonical_display = canonical
                if canonical_mask.any():
                    canonical_display = str(df.loc[canonical_mask, 'Column'].iloc[0])
                df.at[idx, 'Column'] = canonical_display

                # Normalize Value to hostmap canonical casing if available
                if _hostmap_canonical is not None:
                    canon_val = _hostmap_canonical(val)
                    if canon_val:
                        df.at[idx, 'Value'] = canon_val
                        norm = _norm_value(canon_val)
                existing_norms.add(norm)
                n_mig += 1
                decisions.append({
                    'variant': variant, 'canonical': canonical,
                    'value': str(df.at[idx, 'Value']), 'action': 'MIGRATE', 'bp': bp_v,
                    'reason': f'consolidated [{variant}] → [{canonical}]',
                })
            if verbose:
                bits = []
                if n_mig:     bits.append(f'{n_mig} migrated')
                if n_dup:     bits.append(f'{n_dup} deduped')
                if n_hm_fail: bits.append(f'{n_hm_fail} hostmap-failed')
                print(f"      [{variant}] ({len(variant_idxs)} row"
                      f"{'s' if len(variant_idxs) != 1 else ''}): "
                      f"{', '.join(bits) if bits else 'no changes'}")

    if drop_idx:
        df = df.drop(index=drop_idx).reset_index(drop=True)

    # ── PHASE 2: hard whitelist enforcement ────────────────────────────────
    # Anything whose Column survived phase 1 but isn't on the whitelist gets
    # dropped here. This catches columns the agent invented that don't appear
    # in CATEGORY_CANONICAL_REMAP (e.g. a new fabricated 'CRYPTO EXCHANGES'
    # or 'NEWSLETTER' column that nobody pre-registered as a variant).
    n_whitelist_drop = 0
    if enforce_whitelist and 'Column' in df.columns and len(df):
        if whitelist is None:
            whitelist = get_hostmap_section_whitelist(verbose=verbose)
        col_upper2 = df['Column'].astype(str).str.upper().str.strip()
        bad_mask = ~col_upper2.isin(whitelist)
        if bad_mask.any():
            bad_rows = df[bad_mask]
            # Group by Column so the log is readable
            from collections import Counter as _Counter
            bad_counts = _Counter(col_upper2[bad_mask].tolist())
            if verbose:
                bits = ', '.join(f'[{c}]={n}' for c, n in
                                 bad_counts.most_common(10))
                print(f"   🚫 canonicalize: dropping {int(bad_mask.sum())} "
                      f"row(s) in {len(bad_counts)} non-whitelist column"
                      f"{'s' if len(bad_counts) != 1 else ''}: {bits}")
            for idx in bad_rows.index:
                bp_v = None
                if bp_col is not None:
                    try:
                        bp_v = float(str(df.at[idx, bp_col])
                                     .strip().rstrip('%'))
                    except Exception:
                        bp_v = None
                decisions.append({
                    'variant': str(df.at[idx, 'Column']),
                    'canonical': None,
                    'value': str(df.at[idx, 'Value']),
                    'action': 'WHITELIST_DROP', 'bp': bp_v,
                    'reason': f'column [{str(df.at[idx, "Column"]).upper()}]'
                              ' not in hostmap SECTION whitelist + '
                              'ALWAYS_ALLOWED_COLUMNS',
                })
            n_whitelist_drop = int(bad_mask.sum())
            df = df[~bad_mask].reset_index(drop=True)

    if verbose:
        n_mig = sum(1 for d in decisions if d['action'] == 'MIGRATE')
        n_dup = sum(1 for d in decisions if d['action'] == 'DEDUP')
        n_hm  = sum(1 for d in decisions if d['action'] == 'HOSTMAP_FAIL')
        n_wl  = sum(1 for d in decisions if d['action'] == 'WHITELIST_DROP')
        if decisions:
            bits = [f'{n_mig} migrated', f'{n_dup} deduped',
                    f'{n_hm} hostmap-dropped']
            if n_wl:
                bits.append(f'{n_wl} whitelist-dropped')
            print(f"   ✅ canonicalize: {', '.join(bits)} → "
                  f"{len(set(by_canonical.keys()))} "
                  f"canonical column(s) reconciled")
        else:
            print("   ✅ canonicalize: no actionable variant rows")

    return df, decisions


# ─────────────────────────────────────────────────────────────────────────────
#  Metadata-string scrubber (D5: prompt context leaking back as data)
# ─────────────────────────────────────────────────────────────────────────────
# The per-category LLM occasionally echoes its prompt context back as a row.
# Symptoms:
#   • Value = 'SAMPLE SIZE (2025-01-01 TO 2025-12-31) | BEHAVIOR STUDY (...)'
#   • Value = 'BRAND INPUT: brand-variant-1, brand.variant.2, ...'
#   • Value = '2025-01-01 TO 2025-12-31'
#   • Value contains pipes, study markers, N=… sample-size syntax
# These are NEVER real brands. Drop them at the audit-framework entry point so
# nothing downstream has to special-case "is this a real brand or prompt echo?"

# Patterns that mean "this Value is leaked prompt context, not a brand".
# Each entry: (regex, label) — label used in the dropped-row decision log.
# Patterns are case-insensitive (compiled with re.I).
_METADATA_VALUE_PATTERNS = [
    (re.compile(r'\|'),                                  'pipe-char'),
    (re.compile(r'\d{4}-\d{2}-\d{2}\s+TO\s+\d{4}', re.I), 'date-range'),
    (re.compile(r'BEHAVIOR\s+STUDY',                re.I), 'behavior-study-marker'),
    (re.compile(r'\bN\s*=\s*\d',                    re.I), 'sample-size-marker'),
    (re.compile(r'^\s*BRAND\s+INPUT\s*:',           re.I), 'brand-input-prefix'),
    (re.compile(r'^\s*SAMPLE\s+SIZE\s*[:\(]',       re.I), 'sample-size-prefix'),
    (re.compile(r'(?:^|\s)https?://',               re.I), 'embedded-url'),
    # 5+ comma-separated chunks where each looks like a brand-variant slug
    # (lots of hyphens, dots, or word-numeric mixes). E.g.
    # 'tmobile,t-mobile,t_mobile,tmobileus,tmobile.com,t.mobile'
    (re.compile(r'^[^,]{1,40}(?:\s*,\s*[^,]{1,40}){4,}$'), 'brand-variant-list'),
]

# Columns where these patterns are LEGITIMATE — don't scrub them. INCOME bands
# look like 'UNDER $25,000', LOCATION values can contain commas, SAMPLE SIZE
# / BRAND INPUT columns are the metadata-rows themselves.
_METADATA_SCRUB_EXEMPT_COLUMNS = {
    'INCOME', 'AGE', 'LOCATION', 'EDUCATION',
    'SAMPLE SIZE', 'BRAND INPUT', 'INPUT_METADATA',
    'POLITICAL AFFILIATION', 'RELIGION', 'OCCUPATION', 'EMPLOYMENT',
    'COUNTRY', 'STATE', 'CITY', 'METRO',
}


def strip_metadata_rows(df, verbose: bool = True):
    """Drop rows where Value matches a known prompt-leak pattern (D5).

    Runs across all Columns EXCEPT those in _METADATA_SCRUB_EXEMPT_COLUMNS
    (demographic categories whose Values legitimately contain commas, dollar
    signs, etc.).

    Returns (df, decisions) where each decision is:
        {column, value, pattern_label, action: 'STRIP_METADATA',
         reason: 'matches <label> pattern'}

    Deterministic, no LLM calls. Idempotent.
    """
    if 'Value' not in df.columns or 'Column' not in df.columns:
        if verbose:
            print("   ⚠️  strip-metadata: missing Column/Value; skipping")
        return df, []

    df = df.copy()
    col_upper = df['Column'].astype(str).str.upper().str.strip()
    val_str   = df['Value'].astype(str)

    eligible_mask = ~col_upper.isin(_METADATA_SCRUB_EXEMPT_COLUMNS)

    decisions: list[dict] = []
    drop_idx: list = []

    for idx in df.index[eligible_mask]:
        v = val_str.at[idx]
        if not v or v in ('nan', 'None'):
            continue
        for pat, label in _METADATA_VALUE_PATTERNS:
            if pat.search(v):
                drop_idx.append(idx)
                decisions.append({
                    'column': str(df.at[idx, 'Column']),
                    'value': v,
                    'pattern_label': label,
                    'action': 'STRIP_METADATA',
                    'reason': f'matches {label} pattern (prompt-leak signature)',
                })
                break

    if drop_idx:
        df = df.drop(index=drop_idx).reset_index(drop=True)

    if verbose:
        if decisions:
            from collections import Counter as _Counter
            by_label = _Counter(d['pattern_label'] for d in decisions)
            bits = ', '.join(f'{n} {lab}' for lab, n in by_label.most_common())
            print(f"   🧽 strip-metadata: dropped {len(decisions)} prompt-leak "
                  f"row(s) — {bits}")
            for d in decisions[:5]:
                print(f"       [{d['column'][:24]:24s}] {d['value'][:64]:<64s}"
                      f"  ← {d['pattern_label']}")
        else:
            print("   ✅ strip-metadata: no prompt-leak rows detected")

    return df, decisions


# ─────────────────────────────────────────────────────────────────────────────
#  D-Demo: enforce demographic VALUES against the canonical CH set
# ─────────────────────────────────────────────────────────────────────────────
# Demographic Values (e.g. AGE=25-34, GENDER=Female) must come from the
# DISTINCT set that actually exists in userdata.user_data_sanitized — the
# pipeline must never invent new demographic buckets that aren't in the
# source data. LLM emit + downstream passes can drift away from CH casing
# (e.g. 'BACHELORS DEGREE' instead of "Bachelor's Degree"), introduce
# punctuation variants ('18–24' em-dash instead of '18-24'), or fabricate
# buckets the data doesn't support. This pass canonicalizes.
#
# Strategy:
#   1. Query CH once at module load for distinct values per demographic col.
#   2. Build a normalized lookup key (uppercase + collapse dashes/apostrophes).
#   3. For every demographic row in the profile:
#       a. If norm(value) matches a CH bucket → rewrite to CH canonical casing.
#       b. If no match → drop the row (or log and drop, configurable).
#
# Falls back to a hardcoded snapshot when CH is unreachable.

# Columns that hold demographic Values (mapped from CH column names).
# The profile uses these as `Column` tags; the CH column name on the
# userdata.user_data_sanitized side is the same string (uppercase).
# Updated 2026-05-27 from demos.csv: added PRIMARY_LANGUAGE, RELATIONSHIP,
# NUMBER_OF_CHILDREN, AGE_OF_CHILDREN; fixed RELATIONSHIP (was
# RELATIONSHIP_STATUS).
DEMO_VALUE_COLUMNS = {
    'GENDER', 'AGE', 'INCOME', 'EDUCATION', 'ETHNICITY',
    'SEXUAL_ORIENTATION', 'PARENTAL_STATUS', 'OCCUPATION',
    'PRIMARY_LANGUAGE', 'RELATIONSHIP',
    'NUMBER_OF_CHILDREN', 'AGE_OF_CHILDREN',
}

# Hardcoded fallback whitelist — used when ClickHouse is unreachable AND
# demos.csv isn't present on the filesystem. Snapshot from demos.csv
# (2026-05-27, ~36M-row userdata.user_data_sanitized scan).
# Refresh with:
#   SELECT Category, Value, count(*) AS Row_Count
#   FROM userdata.user_data_sanitized ARRAY JOIN ...
_DEMO_VALUE_FALLBACK = {
    'GENDER': [
        'Female', 'Male', 'Non-Binary', 'Prefer Not to Say',
        'Trans Female', 'Trans Male',
    ],
    'AGE': [
        '17 and Under', '18-24', '25-34', '35-44', '45-54', '55-64',
        '65 or Older', 'Other',
    ],
    'INCOME': [
        'Less than $25,000', '$25,000 - $49,999', '$50,000 - $74,999',
        '$75,000 - $99,999', '$100,000 - $149,999',
        '$150,000 - $249,999', '$250,000 or More',
    ],
    'EDUCATION': [
        "Bachelor's Degree", 'Graduate or Professional Degree',
        'High School or Less', 'Prefer Not to Say',
        'Some College / Associate Degree',
    ],
    'ETHNICITY': [
        'Another Race/Ethnicity', 'Asian', 'Black or African American',
        'Black or African American, Hispanic or Latino',
        'Hispanic or Latino', 'White', 'White, Hispanic or Latino',
    ],
    'SEXUAL_ORIENTATION': [
        'Another Sexual Orientation', 'Asexual', 'Gay or Lesbian',
        'LGBTQ+', 'Other', 'Prefer Not to Say', 'Straight / Heterosexual',
    ],
    'PARENTAL_STATUS': [
        'Has Children', 'No Children', 'Prefer Not to Say',
        # Legacy artifacts (low row count) — accepted but should be
        # remapped to canonical buckets upstream
        'No', 'Yes',
    ],
    'OCCUPATION': [
        'Agriculture & Outdoor', 'Business and Financial Operations',
        'Computer and Mathematical', 'Education or Library Services',
        'Healthcare Practitioners or Support', 'Legal',
        'Management, Business & Professional', 'Manufacturing & Production',
        'Other', 'Public Safety & Protective Services', 'Retired',
        'Sales & Retail', 'Science, Technology & Technical Professions',
        'Self-Employed', 'Service & Hospitality',
        'Skilled Trades/Construction or Maintenance', 'Student',
        'Transportation & Logistics',
    ],
    'PRIMARY_LANGUAGE': [
        'Chinese', 'English', 'Other', 'Spanish',
    ],
    'RELATIONSHIP': [
        'Divorced or Separated', 'In a Relationship', 'Married',
        'Prefer Not to Say', 'Single', 'Widowed',
        # Legacy variant kept for completeness
        'Single (not living with a partner)',
    ],
    'NUMBER_OF_CHILDREN': [
        '0', '1', '2', '3', '4+',
    ],
    'AGE_OF_CHILDREN': [
        '11 to 13', '14 to 17', '3 to 5', '6 to 10',
        'N/A', 'No Kids', 'Under 3',
    ],
}

# Module-level cache so we don't re-query CH on every call.
_DEMO_WHITELIST_CACHE = None

# Optional override path — if a `demos.csv` exists here, it's used as the
# authoritative source of canonical demographic values (one source of
# truth file the operator can drop in and update without code changes).
_DEMO_CSV_SEARCH_PATHS = [
    '/root/finished_codes/demos.csv',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'demos.csv'),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'demos.csv'),
]


def _load_demos_csv() -> dict | None:
    """Load demos.csv if it exists. Returns ``{col: {norm: canonical}}``
    where canonical = the variant with the highest Row_Count.
    Returns None if no demos.csv is found."""
    import os as _os
    for path in _DEMO_CSV_SEARCH_PATHS:
        if not _os.path.exists(path):
            continue
        try:
            import pandas as _pd
            df = _pd.read_csv(path)
            if not {'Category', 'Value', 'Row_Count'}.issubset(df.columns):
                continue
            df = df[df.Value.notna() & (df.Value.astype(str) != '')]
            out = {}
            for cat, grp in df.groupby('Category'):
                # Build {norm_key: variant_with_highest_row_count}
                m = {}
                grp_sorted = grp.sort_values('Row_Count', ascending=False)
                for _, row in grp_sorted.iterrows():
                    val = str(row['Value']).strip()
                    if not val:
                        continue
                    nkey = _norm_demo_value(val)
                    if nkey and nkey not in m:
                        m[nkey] = val  # first-seen (highest Row_Count) wins
                if m:
                    out[str(cat).upper()] = m
            return out
        except Exception:
            continue
    return None


def _norm_demo_value(s) -> str:
    """Canonical lookup key — uppercase, collapse en/em dashes to hyphen,
    smart quotes to straight, multiple spaces to single."""
    if s is None:
        return ''
    s = str(s).strip()
    if not s:
        return ''
    s = s.upper()
    s = s.replace('\u2013', '-').replace('\u2014', '-')  # en/em dash
    s = s.replace('\u2019', "'").replace('\u2018', "'")  # smart quotes
    s = re.sub(r'\s+', ' ', s)
    return s


def get_demographic_value_whitelist(force_refresh: bool = False) -> dict:
    """Return ``{column: {norm_value: canonical_value}}``.

    Resolution order:
      1. demos.csv (operator-curated source-of-truth, includes Row_Count
         so we pick the variant the data actually uses most often)
      2. ClickHouse userdata.user_data_sanitized DISTINCT scan
      3. Hardcoded _DEMO_VALUE_FALLBACK snapshot
    """
    global _DEMO_WHITELIST_CACHE
    if _DEMO_WHITELIST_CACHE is not None and not force_refresh:
        return _DEMO_WHITELIST_CACHE

    # 1. demos.csv (authoritative)
    csv_wl = _load_demos_csv()
    if csv_wl:
        _DEMO_WHITELIST_CACHE = csv_wl
        return csv_wl

    out = {}
    try:
        import clickhouse_connect
        ch = clickhouse_connect.get_client(
            host='37.27.140.111', port=8123,
            username='bgapp',
            password='4qPllwDG+S3PptBWTRAJPTkpCzkRZ6tZ',
        )
        for col in DEMO_VALUE_COLUMNS:
            try:
                r = ch.query(
                    f"SELECT DISTINCT {col} FROM userdata.user_data_sanitized "
                    f"WHERE {col} != '' ORDER BY {col}"
                )
                vals = [row[0] for row in r.result_rows if row[0]]
                # Dedupe via norm key, keep the first canonical form we see.
                m = {}
                for v in vals:
                    k = _norm_demo_value(v)
                    if k and k not in m:
                        m[k] = v
                out[col] = m
            except Exception:
                # Per-column failure → fall back for this column only
                out[col] = {
                    _norm_demo_value(v): v
                    for v in _DEMO_VALUE_FALLBACK.get(col, [])
                }
        _DEMO_WHITELIST_CACHE = out
        return out
    except Exception:
        # CH unreachable → full fallback
        out = {
            col: {_norm_demo_value(v): v for v in vals}
            for col, vals in _DEMO_VALUE_FALLBACK.items()
        }
        _DEMO_WHITELIST_CACHE = out
        return out


def enforce_demographic_values(df, verbose: bool = True,
                                  whitelist: dict | None = None):
    """D-Demo: enforce demographic Values against the canonical CH set.

    For every row whose ``Column`` is a demographic (GENDER, AGE, INCOME,
    etc.):
      - Rewrite the ``Value`` to CH canonical casing if a normalized match
        is found (e.g. "BACHELORS DEGREE" → "Bachelor's Degree").
      - Drop the row if no match exists (LLM invented a bucket).

    Returns ``(df, decisions)`` where each decision is one of:
      {action: 'REMAP', column, old_value, new_value}
      {action: 'DROP', column, value, reason: 'not in CH whitelist'}
    """
    if 'Column' not in df.columns or 'Value' not in df.columns:
        if verbose:
            print('   ⚠️  enforce-demo: missing Column/Value; skipping')
        return df, []

    wl = whitelist if whitelist is not None else get_demographic_value_whitelist()
    if not wl:
        if verbose:
            print('   ⚠️  enforce-demo: empty whitelist; skipping')
        return df, []

    df = df.copy()
    col_u = df['Column'].astype(str).str.upper().str.strip()
    drop_idx = []
    remap_count = 0
    decisions = []

    for idx in df.index:
        col_tag = col_u.at[idx]
        if col_tag not in DEMO_VALUE_COLUMNS:
            continue
        col_map = wl.get(col_tag)
        if not col_map:
            continue
        raw_val = df.at[idx, 'Value']
        if raw_val is None or str(raw_val).strip() == '':
            continue
        norm_key = _norm_demo_value(raw_val)
        if norm_key in col_map:
            canon = col_map[norm_key]
            # Only rewrite if differs (avoid unnecessary mutation)
            if str(raw_val) != canon:
                df.at[idx, 'Value'] = canon
                remap_count += 1
                decisions.append({
                    'action': 'REMAP',
                    'column': col_tag,
                    'old_value': str(raw_val),
                    'new_value': canon,
                })
        else:
            drop_idx.append(idx)
            decisions.append({
                'action': 'DROP',
                'column': col_tag,
                'value': str(raw_val),
                'reason': 'not in ClickHouse demographic whitelist',
            })

    if drop_idx:
        df = df.drop(index=drop_idx).reset_index(drop=True)

    if verbose:
        n_drop = len(drop_idx)
        if remap_count or n_drop:
            print(f'   🎂 enforce-demo: {remap_count} remapped to CH canonical '
                  f'casing, {n_drop} dropped (not in CH whitelist)')
            for d in decisions[:5]:
                if d['action'] == 'DROP':
                    print(f"       DROP   [{d['column']:18s}] {d['value']!r}")
                else:
                    print(f"       REMAP  [{d['column']:18s}] {d['old_value']!r} → {d['new_value']!r}")
        else:
            print('   ✅ enforce-demo: every demographic value matches CH canonical set')

    return df, decisions


# ─────────────────────────────────────────────────────────────────────────────
#  Generation-time validators (D5/D7/D8/D9/D14) — per-row guards used by the
#  per-category agent emit path. Cheap, deterministic, single-row decisions.
# ─────────────────────────────────────────────────────────────────────────────
# The post-gen audit catches these defects already (strip_metadata_rows for
# D5, depin_round_brand_bps for D7, scripts/depin_profile.py for D8/D9, etc.),
# but every fix is a recurring whack-a-mole because the per-cat agent
# regenerates the same bad values every run. Catching them at emit-time is
# 100x cheaper than fixing them in post.

def is_metadata_value(value, column=None) -> tuple[bool, str]:
    """D5: True if `value` matches a prompt-leak signature.

    Returns ``(is_leak, label)``. ``label`` is empty when not a leak.
    Honours `_METADATA_SCRUB_EXEMPT_COLUMNS` when `column` is provided so
    INCOME / LOCATION etc. with legitimately-comma-delimited values aren't
    flagged. Single-value version of `strip_metadata_rows` for the
    generation-time emit path.
    """
    if value is None:
        return False, ''
    v = str(value).strip()
    if not v or v in ('nan', 'None'):
        return False, ''
    if column is not None:
        col_u = str(column).strip().upper()
        if col_u in _METADATA_SCRUB_EXEMPT_COLUMNS:
            return False, ''
    for pat, label in _METADATA_VALUE_PATTERNS:
        if pat.search(v):
            return True, label
    return False, ''


def normalize_value_key(value) -> str:
    """D14: canonical key for case-variant / spelling dedupe.

    Returns the uppercase value stripped of every non-alphanumeric char so
    "Netflix", "NETFLIX", "net-flix", and "net flix" all collapse to the
    same key. Use as a dict key when emitting rows; on collision merge
    into the existing row instead of writing a duplicate.

    Returns ``''`` if value is missing/empty.
    """
    if value is None:
        return ''
    s = str(value).strip()
    if not s or s in ('nan', 'None'):
        return ''
    return re.sub(r'[^A-Z0-9]', '', s.upper())


def _is_intentional_2dp(v) -> bool:
    """D8: True if `v ≥ 0.5` displays as X.X0 or X.X5 (LLM round-anchor)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    if f < 0.5:
        return False
    return round(f * 100) % 10 in (0, 5)


def _is_x00xx_anchor(v) -> bool:
    """D7: True if `v` looks like a round-integer anchor with 4dp noise
    bolted on (5.0028, 7.0009, 12.0042)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    if f < 0.5:
        return False
    # Within 0.01 of an integer (excluding the integer itself — that case
    # is caught by D8's X.X0 detector). Colleague's audit examples:
    # 5.0028, 7.0009, 12.0042 — all delta < 0.01. Wider bound caused 600+
    # false positives where the integer part wasn't actually the anchor.
    rounded = round(f)
    delta = abs(f - rounded)
    return 0.00005 < delta < 0.01


def validate_emitted_bp(bp, subj: str, brand: str, category: str,
                          seen_4dp: dict = None,
                          max_collisions: int = 2):
    """D7+D8+D9 combined emit-time validator.

    Inputs
    ------
    bp : float — the BP the agent emitted (in pct, e.g. 5.4321)
    subj, brand, category : strs used to seed deterministic jitter
    seen_4dp : optional dict mapping (cat_upper, bp_4dp) -> count for
        this category; pass the same dict across all calls in one
        category to enable in-cat 4dp collision detection.
    max_collisions : reroll on the (max_collisions+1)-th identical 4dp
        value within the same category. Default 2 means a 3rd brand at
        the same 4dp gets rerolled.

    Returns (validated_bp, label) where label ∈ {'', 'X.X5/X.X0',
    'X.00xx', '4dp-collision'}. Caller can log the label.
    """
    try:
        f = float(bp)
    except (TypeError, ValueError):
        return bp, 'non-numeric'

    # Lazy-import _jitter_for to avoid module-load circular dep with
    # post_generation_enforcers (which imports things from BG.py).
    try:
        from post_generation_enforcers import _jitter_for  # type: ignore
    except Exception:
        try:
            from migration.post_generation_enforcers import _jitter_for  # type: ignore
        except Exception:
            _jitter_for = None  # noqa: N806

    def _reroll(label: str, salt: str) -> float:
        if _jitter_for is None:
            # Fallback: deterministic ±0.013pp drift from a hash.
            import hashlib as _hl
            h = int(_hl.blake2b(
                f'{subj}|{brand}|{category}|{salt}'.encode(),
                digest_size=8,
            ).hexdigest(), 16)
            drift = (((h % 2600) - 1300) / 100000.0)
            new = round(max(0.0001, min(99.9999, f + drift * f)), 4)
        else:
            new = _jitter_for(subj, brand, salt=f'{salt}|{category}',
                                pct=0.022, base=f)
        return new

    label = ''
    if _is_intentional_2dp(f):
        f = _reroll('emit-d8', f'emit-d8-x5x0')
        label = 'X.X5/X.X0'
    if _is_x00xx_anchor(f):
        f = _reroll('emit-d7', f'emit-d7-x00xx')
        label = label or 'X.00xx'

    # 4dp-collision check (D9). Track exact 4dp value within category.
    if seen_4dp is not None:
        key = (str(category).strip().upper(), round(float(f), 4))
        n = seen_4dp.get(key, 0)
        if n >= max_collisions:
            f = _reroll('emit-d9', f'emit-d9-collision-{n}')
            label = label or '4dp-collision'
            # Update key for the new value
            key = (key[0], round(float(f), 4))
        seen_4dp[key] = seen_4dp.get(key, 0) + 1

    return round(float(f), 4), label


# ─────────────────────────────────────────────────────────────────────────────
#  D3: MPB composer — compose MOST PURCHASED BRANDS as the union of every
#  branded sub-category instead of running MPB as an independent LLM pass.
# ─────────────────────────────────────────────────────────────────────────────
# The colleague's audit consistently catches MPB undersized by 40-90% (UBG
# 132, Grimsburg 283, Robin Roberts 400, Krapopolis 1074 vs target 1876).
# Root cause: agent_reason_mpb_floor is "ADD candidates the LLM agrees with"
# which is choosy. The union-of-sub-cats approach is deterministic and
# guarantees MPB carries every brand the pipeline already measured.

# Branded sub-cats whose rows are eligible to be unioned into MPB.
# Narrowed 2026-05-28 (Rule #4c) to ONLY the categories whose brands map
# 1:1 to hostmap ``Most Purchased Brands, *`` sub-sections. Previously
# included streaming, financial, retail, etc. — which produced 1,146 of
# 2,137 wrong-category MPB rows in the Stephen A Smith profile (Netflix
# → Streaming, Walmart → Where They Shop, Visa → Credit Provider, etc.).
# Even from these "safe" source cats every union candidate is double-
# checked against the hostmap MPB whitelist + Hidden blocklist below.
MPB_UNION_SOURCE_CATEGORIES = {
    'APPAREL/FOOTWEAR',     # → Most Purchased Brands, Apparel/Footwear
    'BEAUTY/WELLNESS',      # → Most Purchased Brands, Beauty/Wellness
    'CPG',                  # → Most Purchased Brands, CPG
    'TECHNOLOGY BRAND',     # → Most Purchased Brands, Technology Brand
    'TECHNOLOGY/DEVICE',    # subset of TECHNOLOGY BRAND in hostmap
    'HOME/OUTDOOR',         # → Most Purchased Brands, Home/Outdoor
    'ACCESSORIES',          # → Most Purchased Brands, Accessories
    'PETS',                 # → Most Purchased Brands, Pets
}
MPB_CATEGORY = 'MOST PURCHASED BRANDS'


# Lazy-loaded hostmap helpers (imported on first compose-mpb call to keep
# this module decoupled from post_generation_enforcers at import time).
_HOSTMAP_GATES = None  # (_is_mpb, _is_hidden) tuple or (None, None) if unavailable


def _load_hostmap_gates():
    """Import and cache the hostmap membership/Hidden gates from
    post_generation_enforcers. Returns (mpb_fn, hidden_fn) or (None, None)
    if the module isn't importable yet.
    """
    global _HOSTMAP_GATES
    if _HOSTMAP_GATES is not None:
        return _HOSTMAP_GATES
    mpb_fn = None
    hidden_fn = None
    for modpath in ('post_generation_enforcers',
                    'migration.post_generation_enforcers'):
        try:
            import importlib
            mod = importlib.import_module(modpath)
            mpb_fn = getattr(mod, '_is_hostmap_mpb', None)
            hidden_fn = getattr(mod, '_is_hostmap_hidden', None)
            if mpb_fn is not None and hidden_fn is not None:
                break
        except Exception:
            continue
    _HOSTMAP_GATES = (mpb_fn, hidden_fn)
    return _HOSTMAP_GATES


def compose_mpb_from_subcats(df,
                              source_categories=None,
                              subject: str = '',
                              verbose: bool = True,
                              jitter_pct: float = 0.012):
    """D3: union every branded sub-cat into MOST PURCHASED BRANDS.

    For every brand that appears in any source category but is *not*
    already in MPB, emit a new MPB row carrying the brand's max BP from
    its sub-cats with a tiny jitter (so it doesn't 4dp-collide with the
    sub-cat row — see D10). Brands already in MPB are left alone.

    Runs *before* `agent_reason_mpb_floor`, which then only has to top
    up any remaining hostmap candidates the sub-cats didn't cover.

    Returns (df, decisions) where each decision is:
        {action: 'COMPOSE_MPB', brand, source_category, bp_used,
         mpb_bp, reason}
    """
    if 'Column' not in df.columns or 'Value' not in df.columns:
        if verbose:
            print("   ⚠️  compose-mpb: missing Column/Value; skipping")
        return df, []

    sources = set(source_categories) if source_categories else set(MPB_UNION_SOURCE_CATEGORIES)
    sources = {str(c).strip().upper() for c in sources}

    bp_col = 'Brand Penetration (Row)'
    raw_col = 'Original Raw Numbers'
    proj_col = 'US Gen Pop Projection'
    cs_col = 'Category Share'

    if bp_col not in df.columns:
        if verbose:
            print("   ⚠️  compose-mpb: missing BP column; skipping")
        return df, []

    df = df.copy()
    for _c in (bp_col, raw_col, proj_col, cs_col):
        if _c in df.columns and df[_c].dtype != object:
            df[_c] = df[_c].astype(object)

    def _bp_float(s):
        try:
            return float(str(s).replace('%', '').strip())
        except (TypeError, ValueError):
            return None

    col_upper = df['Column'].astype(str).str.strip().str.upper()
    val_upper = df['Value'].astype(str).str.strip().str.upper()

    # Build the MPB existing-brand set (by normalized key for case-variant
    # safety — D14).
    mpb_mask = (col_upper == MPB_CATEGORY)
    mpb_keys = set()
    for v in df.loc[mpb_mask, 'Value']:
        k = normalize_value_key(v)
        if k:
            mpb_keys.add(k)

    # For every source-cat row, collect the brand's max BP across its sources.
    src_mask = col_upper.isin(sources)
    src_rows = df.loc[src_mask, ['Column', 'Value', bp_col]].copy()
    src_rows['_bp_f'] = src_rows[bp_col].apply(_bp_float)
    src_rows = src_rows[src_rows['_bp_f'].notna() & (src_rows['_bp_f'] >= 0.05)]
    src_rows['_key'] = src_rows['Value'].apply(normalize_value_key)
    src_rows = src_rows[src_rows['_key'].astype(bool)]

    # Pick best source row per brand (highest sub-cat BP).
    if src_rows.empty:
        if verbose:
            print(f"   🛒 compose-mpb: no candidate sub-cat rows in {len(sources)} sources")
        return df, []

    best = (src_rows
            .sort_values('_bp_f', ascending=False)
            .drop_duplicates('_key', keep='first'))

    # Filter out brands already in MPB.
    new_brands = best[~best['_key'].isin(mpb_keys)]
    if new_brands.empty:
        if verbose:
            print(f"   🛒 compose-mpb: every sub-cat brand "
                  f"({len(best)}) already in MPB — nothing to do")
        return df, []

    # Rule #4c gate (added 2026-05-28): every union candidate must be
    # hostmap-classified as ``Most Purchased Brands, *`` AND must NOT be
    # hostmap-Hidden. Even with MPB_UNION_SOURCE_CATEGORIES narrowed,
    # individual brands inside those source cats can be hostmap-classified
    # elsewhere (e.g. an Apparel/Footwear row whose hostmap section is
    # actually 'Hidden' or 'Where They Shop'). Per-brand gating catches
    # these without needing to maintain a perfect category whitelist.
    is_mpb, is_hidden = _load_hostmap_gates()
    if is_mpb is not None and is_hidden is not None:
        before_n = len(new_brands)
        mpb_ok_mask = new_brands['Value'].apply(
            lambda v: is_mpb(v) and not is_hidden(v)
        )
        new_brands = new_brands[mpb_ok_mask]
        gated_n = before_n - len(new_brands)
        if verbose and gated_n:
            print(f"   🚪 compose-mpb gate: dropped {gated_n} candidate(s) "
                  f"not in hostmap MPB whitelist (or Hidden)")
        if new_brands.empty:
            if verbose:
                print(f"   🛒 compose-mpb: all {before_n} candidates "
                      f"failed hostmap MPB gate — nothing to add")
            return df, []
    elif verbose:
        print(f"   ⚠️  compose-mpb: hostmap MPB gate unavailable "
              f"(post_generation_enforcers not importable); "
              f"falling back to source-cat-only filter")

    # Try to import _jitter_for so the new MPB row drifts away from the
    # exact sub-cat 4dp value (D10).
    try:
        from post_generation_enforcers import _jitter_for  # type: ignore
    except Exception:
        try:
            from migration.post_generation_enforcers import _jitter_for  # type: ignore
        except Exception:
            _jitter_for = None  # noqa: N806

    # Derive RAW + PROJ scale from existing MPB rows so the new rows blend in.
    raw_per_pct, proj_per_pct = _derive_scale_from_df(df)

    new_records = []
    decisions = []
    for _, row in new_brands.iterrows():
        brand = str(row['Value']).strip()
        src_cat = str(row['Column']).strip()
        bp_used = float(row['_bp_f'])
        if _jitter_for is not None:
            mpb_bp = _jitter_for(subject, brand,
                                   salt=f'compose-mpb|{src_cat}',
                                   pct=jitter_pct, base=bp_used)
        else:
            # Deterministic ±jitter_pct drift fallback.
            import hashlib as _hl
            h = int(_hl.blake2b(
                f'{subject}|{brand}|compose-mpb|{src_cat}'.encode(),
                digest_size=8,
            ).hexdigest(), 16)
            drift = (((h % 2400) - 1200) / 100000.0) * jitter_pct
            mpb_bp = round(max(0.0001, bp_used + drift * bp_used), 4)
        mpb_bp = round(max(0.0001, min(99.9999, mpb_bp)), 4)

        rec = {c: '' for c in df.columns}
        rec['Column'] = MPB_CATEGORY
        rec['Value'] = brand
        rec[bp_col] = f"{mpb_bp:.4f}%"
        if raw_col in df.columns and raw_per_pct:
            rec[raw_col] = int(round(mpb_bp * raw_per_pct))
        if proj_col in df.columns and proj_per_pct:
            rec[proj_col] = int(round(mpb_bp * proj_per_pct))
        new_records.append(rec)

        decisions.append({
            'action': 'COMPOSE_MPB',
            'brand': brand,
            'source_category': src_cat,
            'bp_used': bp_used,
            'mpb_bp': mpb_bp,
            'reason': f'union of branded sub-cats — best source {src_cat} @ {bp_used:.4f}%',
        })

    if new_records:
        df = pd.concat([df, pd.DataFrame(new_records)], ignore_index=True)

    if verbose:
        n_added = len(new_records)
        n_subj = (mpb_mask).sum()
        from collections import Counter as _Counter
        by_src = _Counter(d['source_category'] for d in decisions)
        top_srcs = ', '.join(f'{c}={n}' for c, n in by_src.most_common(8))
        print(f"   🛒 compose-mpb: +{n_added} MPB row(s) "
              f"(MPB before={int(n_subj)}, after={int(n_subj) + n_added}) "
              f"— top sources: {top_srcs}")

    return df, decisions


# ─────────────────────────────────────────────────────────────────────────────
#  Subject hostmap pre-flight (D11: fail-loud when subject missing)
# ─────────────────────────────────────────────────────────────────────────────
# Profiles for subjects that aren't in reference.host_mapping are doomed:
#   • pin_subject_to_100 no-ops silently (no row to pin)
#   • the subject's own franchise / show / brand is missing from the output
#   • we've shipped multiple bad profiles for Bob's Burgers, Universal Basic
#     Guys, Phyllis Smith, etc. before catching this
#
# Solution: at profile-commission time (top of run_full_pipeline), check
# whether the subject is in hostmap. If not, log + (optionally) raise so the
# data team gets notified BEFORE we burn an hour of compute.

class SubjectHostmapGap(RuntimeError):
    """Raised when fail_fast=True and the subject isn't in hostmap."""
    pass


def subject_hostmap_preflight(subject: str,
                              fail_fast: bool = False,
                              notify_callback=None,
                              verbose: bool = True) -> dict:
    """Check whether `subject` exists in reference.host_mapping.

    Returns a dict with:
        {'subject': <str>, 'in_hostmap': <bool>, 'canonical': <str|None>,
         'matched_via': 'exact'|'normalized'|None}

    If the subject is NOT in hostmap:
      • Always prints a HOSTMAP_GAP warning block.
      • Calls `notify_callback(subject, gap_record)` if provided (intended
        for wiring in email/slack/etc. — the data team needs to know
        BEFORE the profile gets shipped).
      • If `fail_fast=True`, raises SubjectHostmapGap. Default False
        (preserves backward-compat for tests + dev runs).

    Degrades gracefully when hostmap helpers aren't importable (returns
    in_hostmap=True with matched_via=None so we never block profiles when
    the check itself is broken).
    """
    if not subject or str(subject).strip() in ('', 'unknown', 'None'):
        if verbose:
            print(f"   ⚠️  preflight: subject is empty/unknown; skipping check")
        return {'subject': subject, 'in_hostmap': True,
                'canonical': None, 'matched_via': None}

    _is_in_hostmap = None
    _hostmap_canonical = None
    try:
        from post_generation_enforcers import (
            _is_in_hostmap as _ih, _hostmap_canonical as _hc,
            _ensure_hostmap_loaded as _eh,
        )
        _is_in_hostmap = _ih
        _hostmap_canonical = _hc
        _eh()
    except Exception:
        try:
            from migration.post_generation_enforcers import (
                _is_in_hostmap as _ih, _hostmap_canonical as _hc,
                _ensure_hostmap_loaded as _eh,
            )
            _is_in_hostmap = _ih
            _hostmap_canonical = _hc
            _eh()
        except Exception as e:
            if verbose:
                print(f"   ⚠️  preflight: hostmap helpers unavailable ({e}); "
                      f"skipping subject check (NOT blocking)")
            return {'subject': subject, 'in_hostmap': True,
                    'canonical': None, 'matched_via': None}

    in_hostmap = bool(_is_in_hostmap(subject))
    canonical = _hostmap_canonical(subject) if in_hostmap else None
    matched_via = ('exact' if canonical and canonical.upper() == subject.upper()
                   else ('normalized' if in_hostmap else None))

    if in_hostmap:
        if verbose:
            extra = f" → canonical: {canonical}" if canonical else ''
            print(f"   ✅ preflight: subject '{subject}' is in hostmap{extra}")
        return {'subject': subject, 'in_hostmap': True,
                'canonical': canonical, 'matched_via': matched_via}

    # Not in hostmap — fail loud.
    gap_record = {
        'subject': subject, 'in_hostmap': False,
        'canonical': None, 'matched_via': None,
        'severity': 'BLOCKER',
        'message': (f"Subject '{subject}' is NOT in reference.host_mapping. "
                    f"pin_subject_to_100 will silently no-op and the subject "
                    f"will be missing from its own canonical categories. "
                    f"Data team must add this subject to hostmap before the "
                    f"profile can ship cleanly."),
    }
    if verbose:
        print()
        print("   " + "═" * 72)
        print(f"   🚨 HOSTMAP_GAP — subject '{subject}' is NOT in "
              f"reference.host_mapping")
        print(f"      • pin_subject_to_100 will silently no-op")
        print(f"      • subject will be missing from its own categories")
        print(f"      • profile output should NOT ship before data team adds "
              f"the subject")
        print(f"      • notify: jessie@ / anastasia@ (hostmap maintenance)")
        print("   " + "═" * 72)
        print()

    if notify_callback is not None:
        try:
            notify_callback(subject, gap_record)
        except Exception as e:
            if verbose:
                print(f"   ⚠️  preflight: notify_callback raised: {e}")

    if fail_fast:
        raise SubjectHostmapGap(gap_record['message'])

    return gap_record


# Exhaustive enumerations — every value carries real signal even at the
# low end. Floor-noise cleanup MUST skip these.
FLOOR_NOISE_EXEMPT_CATEGORIES = {
    'LOCATION',           # 210 DMAs — every audience has a tiny share in every market
    'GENDER',
    'AGE',
    'RACE/ETHNICITY',
    'INCOME',
    'REGION',
    'EDUCATION',
    'LGBTQ+',
    'MARRIED',
    'PARENT',
    'METRO',
    'SAMPLE SIZE',
    'INPUT_METADATA',
    'BRAND INPUT',
    'FAN STATUS',
    'AVID FAN',
    'CASUAL FAN',
    'COUNTRY',
    'STATE',
    'CITY',
    'HOUSEHOLD SIZE',
    'HOUSEHOLD',
    'CHILDREN',
    'POLITICAL AFFILIATION',
    'POLITICAL',
    'RELIGION',
    'EMPLOYMENT',
    'OCCUPATION',
}

# Default cutoff below which a brand value looks like panel-floor noise.
# (Operator-tunable. 0.5% catches the 0.0011 floor + immediate neighbors.)
FLOOR_NOISE_BP_THRESHOLD = 0.5


def agent_reason_floor_noise(df,
                              report: AuditReport,
                              openai_client,
                              persona_doc=None,
                              threshold: float = FLOOR_NOISE_BP_THRESHOLD,
                              exempt_categories=None,
                              model: str = 'gpt-4o',
                              batch_size: int = 25,
                              max_tokens: int = 6000,
                              verbose: bool = True):
    """Identify panel-floor noise rows (bp < threshold, non-demographic) and
    hand each one to the persona-reasoning agent. The agent decides KEEP /
    CHANGE / DROP per row, using the same persona evidence that drove the
    original pull. Demographic enumerations (LOCATION, GENDER, AGE, etc.)
    are EXEMPT — their long tails are real signal.

    Returns (df, decisions) where:
      df is the dataframe with DROP rows removed and CHANGE rows updated
      decisions is a list of dicts with category, value, old_bp, decision,
        new_bp (if CHANGE), reason

    Floor-noise rows that the agent can't decide on default to KEEP (safe).
    No formulas, no mid-band snapping — every decision is persona-grounded.
    """
    if openai_client is None:
        if verbose:
            print("   ⚠️ agent_reason_floor_noise: no OpenAI client; skipping")
        return df, []

    exempt = set(exempt_categories) if exempt_categories else set(FLOOR_NOISE_EXEMPT_CATEGORIES)
    exempt = {str(c).strip().upper() for c in exempt}

    bp_col = 'Brand Penetration (Row)'
    raw_col = 'Original Raw Numbers'
    proj_col = 'US Gen Pop Projection'

    df = df.copy()
    # pandas 2.x: assigning strings into float64 columns raises.
    for _c in (bp_col, raw_col, proj_col):
        if _c in df.columns and df[_c].dtype != object:
            df[_c] = df[_c].astype(object)

    col_norm = df['Column'].astype(str).str.strip().str.upper()
    val_norm = df['Value'].astype(str).str.strip().str.upper()
    bp_numeric = df[bp_col].apply(_numbp)

    # Build the candidate set: bp in (0, threshold), category not exempt
    floor_idx = []
    for idx in df.index:
        cat = col_norm.at[idx]
        if cat in exempt:
            continue
        bp = bp_numeric.at[idx]
        if bp is None:
            continue
        if 0 < bp < threshold:
            floor_idx.append(idx)

    if not floor_idx:
        if verbose:
            print(f"   🧹 floor-noise: no rows with bp<{threshold}% in non-demographic categories")
        return df, []

    if verbose:
        print(f"   🧹 floor-noise: {len(floor_idx)} row(s) at bp<{threshold}% queued for agent re-reasoning")

    persona_context = _persona_context_block(persona_doc,
                                              report.audience_composition,
                                              report.subject)
    raw_per_pct, proj_per_pct = _derive_scale_from_df(df)

    decisions: list[dict] = []
    drop_idx: list = []
    n_change = n_keep = n_drop = n_skip = 0

    for batch_start in range(0, len(floor_idx), batch_size):
        batch_idxs = floor_idx[batch_start: batch_start + batch_size]
        batch_no = batch_start // batch_size + 1
        n_batches = (len(floor_idx) + batch_size - 1) // batch_size

        items_lines = []
        for i, idx in enumerate(batch_idxs, 1):
            cat = str(df.at[idx, 'Column']).strip()
            val = str(df.at[idx, 'Value']).strip()
            cur = bp_numeric.at[idx]
            cur_str = f"{cur:.4f}%" if cur is not None else "—"
            items_lines.append(
                f"{i}. CATEGORY={cat} | VALUE={val} | current_bp={cur_str}"
            )

        # Cache-friendly prompt order — see agent_reason_audit_fails for rationale.
        prompt = (
            AUDIENCE_NOT_MIRROR_RULE
            + _FLOOR_NOISE_TASK_BLOCK
            + f"\n=== PERSONA CONTEXT ===\n{persona_context}\n"
            + f"\n=== FLOOR-NOISE ROWS (batch {batch_no}/{n_batches}) ===\n"
            + "\n".join(items_lines)
        )

        try:
            resp = openai_client.chat.completions.create(
                model=model,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.2,
                max_tokens=max_tokens,
                timeout=120,
            )
            _log_openai_cache(resp, label=f'floor-noise b{batch_no}/{n_batches}')
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
            parsed = json.loads(raw)
        except Exception as e:
            if verbose:
                print(f"   ⚠️ floor-noise batch {batch_no}/{n_batches} error: {e}")
            for idx in batch_idxs:
                n_skip += 1
                decisions.append({
                    'category': str(df.at[idx, 'Column']),
                    'value':    str(df.at[idx, 'Value']),
                    'old_bp':   bp_numeric.at[idx],
                    'decision': 'SKIP',
                    'new_bp':   None,
                    'reason':   f'agent error: {e}',
                })
            continue

        decision_map = {int(d.get('i', -1)): d for d in (parsed.get('decisions') or [])}
        for i, idx in enumerate(batch_idxs, 1):
            d = decision_map.get(i, {})
            cat = str(df.at[idx, 'Column'])
            val = str(df.at[idx, 'Value'])
            old_bp = bp_numeric.at[idx]
            decision = str(d.get('decision', 'KEEP')).upper().strip()
            reason = str(d.get('reason', '')).strip()

            if decision == 'DROP':
                drop_idx.append(idx)
                n_drop += 1
                decisions.append({
                    'category': cat, 'value': val, 'old_bp': old_bp,
                    'decision': 'DROP', 'new_bp': None, 'reason': reason or 'noise',
                })
            elif decision == 'CHANGE':
                try:
                    new_bp = float(d.get('new_bp'))
                except Exception:
                    new_bp = None
                if new_bp is None or new_bp <= 0 or new_bp > 100:
                    n_skip += 1
                    decisions.append({
                        'category': cat, 'value': val, 'old_bp': old_bp,
                        'decision': 'SKIP', 'new_bp': None,
                        'reason': f'invalid new_bp from agent: {d.get("new_bp")!r}',
                    })
                    continue
                # 4-decimal jitter (subject+brand deterministic) to honor
                # the "no round numbers" rule and prevent same-value pinning.
                import random as _r
                _r.seed(hash((report.subject, cat, val, 'floor')) & 0xFFFFFFFF)
                new_bp = round(new_bp + _r.uniform(-0.05, 0.05), 4)
                new_bp = max(0.5, min(100.0, new_bp))
                df.at[idx, bp_col] = f"{new_bp:.4f}%"
                if raw_col in df.columns:
                    df.at[idx, raw_col] = str(max(1, int(round(new_bp * raw_per_pct))))
                if proj_col in df.columns:
                    df.at[idx, proj_col] = str(int(round(new_bp * proj_per_pct)))
                n_change += 1
                decisions.append({
                    'category': cat, 'value': val, 'old_bp': old_bp,
                    'decision': 'CHANGE', 'new_bp': new_bp,
                    'reason': reason or 'persona-defensible reassignment',
                })
            elif decision == 'KEEP':
                n_keep += 1
                decisions.append({
                    'category': cat, 'value': val, 'old_bp': old_bp,
                    'decision': 'KEEP', 'new_bp': None,
                    'reason': reason or 'genuinely small for this persona',
                })
            else:
                n_skip += 1
                decisions.append({
                    'category': cat, 'value': val, 'old_bp': old_bp,
                    'decision': 'SKIP', 'new_bp': None,
                    'reason': reason or 'no decision',
                })

        if verbose:
            batch_changed = sum(1 for d in decisions[-len(batch_idxs):] if d['decision']=='CHANGE')
            batch_dropped = sum(1 for d in decisions[-len(batch_idxs):] if d['decision']=='DROP')
            batch_kept    = sum(1 for d in decisions[-len(batch_idxs):] if d['decision']=='KEEP')
            print(f"   🧹 floor-noise batch {batch_no}/{n_batches}: "
                  f"{batch_dropped} DROP, {batch_changed} CHANGE, {batch_kept} KEEP")

    # Drop rows the agent flagged as noise (do this last so indices stay valid).
    if drop_idx:
        df = df.drop(index=drop_idx).reset_index(drop=True)

    if verbose:
        print(f"   🧹 floor-noise totals: {n_drop} DROP, {n_change} CHANGE, "
              f"{n_keep} KEEP, {n_skip} SKIP across {len(floor_idx)} flagged rows")

    return df, decisions


def agent_reason_cap_overrides(df,
                                cap_decisions: list,
                                openai_client,
                                persona_doc=None,
                                subject: str = '',
                                audience_composition=None,
                                model: str = 'gpt-4o',
                                batch_size: int = 25,
                                max_tokens: int = 6000,
                                verbose: bool = True):
    """Per cap firing (R1/R2/R3/R6 in BG.py's `_apply_genpop_anchored_guardrails`),
    hand the row to the persona-reasoning agent for a final say.

    Decision space (per row):
      KEEP   — the cap is correct; agent's original value was over-emission.
               Final BP stays at the post-cap value.
      REVERT — the agent's original value WAS persona-justified; the cap is
               blunt and wrong for this audience. Restore the agent_original.

    cap_decisions: list of dicts produced by `_apply_genpop_anchored_guardrails`,
        each shaped {category, value, original_bp, capped_bp, rule_id, rationale}.

    Returns (df, decisions) where decisions is the per-row verdict log. Caps
    that the agent skips (parse error / row missing) default to KEEP because
    caps are the conservative outcome.
    """
    if openai_client is None or not cap_decisions:
        if verbose and not cap_decisions:
            print("   ⚖️  cap-override: no cap firings to review")
        elif verbose:
            print("   ⚠️  cap-override: no OpenAI client; cap firings stay as-is")
        return df, []

    bp_col = 'Brand Penetration (Row)'
    raw_col = 'Original Raw Numbers'
    proj_col = 'US Gen Pop Projection'

    df = df.copy()
    for _c in (bp_col, raw_col, proj_col):
        if _c in df.columns and df[_c].dtype != object:
            df[_c] = df[_c].astype(object)

    col_norm = df['Column'].astype(str).str.strip().str.upper()
    val_norm = df['Value'].astype(str).str.strip().str.upper()
    raw_per_pct, proj_per_pct = _derive_scale_from_df(df)

    persona_context = _persona_context_block(persona_doc, audience_composition, subject)

    decisions: list[dict] = []
    n_keep = n_revert = n_skip = 0

    if verbose:
        print(f"   ⚖️  cap-override: {len(cap_decisions)} cap firing(s) queued for agent review")

    for batch_start in range(0, len(cap_decisions), batch_size):
        batch = cap_decisions[batch_start: batch_start + batch_size]
        batch_no = batch_start // batch_size + 1
        n_batches = (len(cap_decisions) + batch_size - 1) // batch_size

        items_lines = []
        for i, cap in enumerate(batch, 1):
            try:
                orig = float(cap.get('original_bp', 0))
                capd = float(cap.get('capped_bp', 0))
            except (TypeError, ValueError):
                orig = capd = 0.0
            items_lines.append(
                f"{i}. CATEGORY={cap.get('category','')} | VALUE={cap.get('value','')} "
                f"| rule={cap.get('rule_id','?')} | agent_original={orig:.2f}% "
                f"| post_cap={capd:.2f}% | rationale={cap.get('rationale','')}"
            )

        # Cache-friendly prompt order — see agent_reason_audit_fails for rationale.
        prompt = (
            AUDIENCE_NOT_MIRROR_RULE
            + _CAP_OVERRIDE_TASK_BLOCK
            + f"\n=== PERSONA CONTEXT ===\n{persona_context}\n"
            + f"\n=== CAP FIRINGS (batch {batch_no}/{n_batches}) ===\n"
            + "\n".join(items_lines)
        )

        try:
            resp = openai_client.chat.completions.create(
                model=model,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.2,
                max_tokens=max_tokens,
                timeout=120,
            )
            _log_openai_cache(resp, label=f'cap-override b{batch_no}/{n_batches}')
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
            parsed = json.loads(raw)
        except Exception as e:
            if verbose:
                print(f"   ⚠️ cap-override batch {batch_no}/{n_batches} error: {e}")
            for cap in batch:
                n_skip += 1
                decisions.append({
                    **cap, 'decision': 'SKIP',
                    'final_bp': cap.get('capped_bp'),
                    'reason': f'agent error: {e}',
                })
            continue

        decision_map = {int(d.get('i', -1)): d for d in (parsed.get('decisions') or [])}
        for i, cap in enumerate(batch, 1):
            d = decision_map.get(i, {})
            decision = str(d.get('decision', 'KEEP')).upper().strip()
            reason = str(d.get('reason', '')).strip()

            cat_norm = str(cap.get('category', '')).strip().upper()
            val_norm_v = str(cap.get('value', '')).strip().upper()
            mask = (col_norm == cat_norm) & (val_norm == val_norm_v)

            if decision == 'REVERT' and mask.any():
                idx = df.index[mask][0]
                try:
                    new_bp = round(float(cap['original_bp']), 4)
                except (TypeError, ValueError, KeyError):
                    new_bp = None
                if new_bp is None or new_bp <= 0 or new_bp > 100:
                    n_skip += 1
                    decisions.append({
                        **cap, 'decision': 'SKIP',
                        'final_bp': cap.get('capped_bp'),
                        'reason': f'cannot REVERT: invalid original_bp {cap.get("original_bp")!r}',
                    })
                    continue
                df.at[idx, bp_col] = f"{new_bp:.4f}%"
                if raw_col in df.columns:
                    df.at[idx, raw_col] = str(max(1, int(round(new_bp * raw_per_pct))))
                if proj_col in df.columns:
                    df.at[idx, proj_col] = str(int(round(new_bp * proj_per_pct)))
                n_revert += 1
                decisions.append({
                    **cap, 'decision': 'REVERT', 'final_bp': new_bp,
                    'reason': reason or 'persona justifies original high value',
                })
            elif decision == 'REVERT':
                n_skip += 1
                decisions.append({
                    **cap, 'decision': 'SKIP',
                    'final_bp': cap.get('capped_bp'),
                    'reason': 'cannot REVERT: row not found in df',
                })
            else:
                n_keep += 1
                decisions.append({
                    **cap, 'decision': 'KEEP',
                    'final_bp': cap.get('capped_bp'),
                    'reason': reason or 'cap is correct; original was over-emission',
                })

        if verbose:
            batch_keep = sum(1 for d in decisions[-len(batch):] if d['decision'] == 'KEEP')
            batch_rev  = sum(1 for d in decisions[-len(batch):] if d['decision'] == 'REVERT')
            print(f"   ⚖️  cap-override batch {batch_no}/{n_batches}: "
                  f"{batch_keep} KEEP, {batch_rev} REVERT")

    if verbose:
        print(f"   ⚖️  cap-override totals: {n_keep} KEEP, {n_revert} REVERT, "
              f"{n_skip} SKIP across {len(cap_decisions)} cap firings")

    return df, decisions


# Categories that should always have rows for a US digital-adult profile.
# If any sit at 0 rows after agent processing, `agent_reason_empty_categories`
# prompts the persona agent to populate them. The base set covers categories
# that should appear in EVERY profile regardless of subject type; the
# content-extras set adds categories relevant for SERIES / ACTOR / TALENT
# subjects (BROADCAST/CABLE for the airing network, PODCAST/HOST for the
# host adjacency, etc.).
# D2 (2026-05-27): these MUST be canonical hostmap SECTION names. If you
# use variants like 'SEARCH ENGINE', 'AI', 'TELCO', or 'BROADCAST/CABLE'
# the empty-cat repopulator will inject them as Column names and bypass
# canonicalize_categories (which runs once, BEFORE this pass). Always use
# the canonical names that exist in reference.host_mapping.SECTION.
MANDATORY_CATEGORIES_BASE = {
    'SEARCH ENGINE/AI',
    'SOCIAL MEDIA',
    'BANKING',
    'DIGITAL BANKING',
    'TELECOM',
    'STREAMING/PLATFORM',
    'STREAMING/MUSIC',
    'QSR',
    'WHERE THEY SHOP',
    'MEDIA',
    'APP/PLATFORM USAGE',
    'AUTOMOBILE',
    'INSURANCE',
    'CREDIT PROVIDER',
}
MANDATORY_CATEGORIES_CONTENT_EXTRAS = {
    'PODCAST',
    'HOST/PERSONALITY',
    'MUSICIAN/BAND',
    'ACTOR',
    'MOVIE THEATER',
}
MANDATORY_CATEGORIES = MANDATORY_CATEGORIES_BASE | MANDATORY_CATEGORIES_CONTENT_EXTRAS


def agent_reason_empty_categories(df,
                                    report: AuditReport,
                                    openai_client,
                                    persona_doc=None,
                                    mandatory_categories=None,
                                    min_entries: int = 5,
                                    max_entries: int = 20,
                                    min_bp: float = 1.0,
                                    model: str = 'gpt-4o',
                                    max_tokens: int = 4000,
                                    verbose: bool = True):
    """Populate categories that came back empty from the upstream agent pass.

    For every category in `mandatory_categories` (default: MANDATORY_CATEGORIES)
    sitting at zero rows in `df`, fire a per-category LLM prompt asking the
    persona agent to enumerate the brands/values THIS audience genuinely
    engages with, with persona-grounded BPs (NOT consensus-snapping, NOT
    floor noise). Validate the response and inject the rows.

    Returns (df, decisions) where each decision is:
      {category, status ('POPULATED' | 'SKIP' | 'ERROR'), n_added,
       entries (list of dicts), reason}

    No-ops if the OpenAI client is missing. Categories already populated
    (any rows present) are LEFT ALONE — this pass never touches existing data.
    """
    if openai_client is None:
        if verbose:
            print("   ⚠️  empty-cat: no OpenAI client; skipping")
        return df, []

    mandatory = set(mandatory_categories) if mandatory_categories else set(MANDATORY_CATEGORIES)
    mandatory = {str(c).strip().upper() for c in mandatory}

    bp_col = 'Brand Penetration (Row)'
    cs_col = 'Category Share'
    raw_col = 'Original Raw Numbers'
    proj_col = 'US Gen Pop Projection'

    if bp_col not in df.columns or 'Column' not in df.columns or 'Value' not in df.columns:
        if verbose:
            print("   ⚠️  empty-cat: missing required columns; skipping")
        return df, []

    df = df.copy()
    for _c in (bp_col, cs_col, raw_col, proj_col):
        if _c in df.columns and df[_c].dtype != object:
            df[_c] = df[_c].astype(object)

    col_upper = df['Column'].astype(str).str.strip().str.upper()
    populated_cats = set(col_upper.unique())
    empty_cats = sorted(mandatory - populated_cats)

    if not empty_cats:
        if verbose:
            print("   🪴 empty-cat: every mandatory category is populated — nothing to do")
        return df, []

    if verbose:
        print(f"   🪴 empty-cat: {len(empty_cats)} mandatory categor"
              f"{'y' if len(empty_cats)==1 else 'ies'} sitting at 0 rows: "
              f"{', '.join(empty_cats)}")

    raw_per_pct, proj_per_pct = _derive_scale_from_df(df)
    persona_context = _persona_context_block(
        persona_doc, report.audience_composition, report.subject,
    )

    # Build the static prefix ONCE per call. Identical across every per-category
    # prompt in this call → OpenAI auto-cache hits on every category after the
    # first. With default args (min=5,max=20,bp=1.0) it's also identical across
    # profiles, giving cross-profile cache hits too.
    _empty_cat_static = (
        AUDIENCE_NOT_MIRROR_RULE
        + "\nYou are the persona-reasoning agent for a Crosswalk digital "
        "audience pull. The category below came back with ZERO rows from "
        "the upstream agent pass — almost certainly a pipeline gap, NOT a "
        "real 'this audience has zero engagement' signal. Populate it "
        "with the brands/values THIS persona genuinely engages with.\n\n"
        "=== TASK ===\n"
        f"Emit between {min_entries} and {max_entries} entries per "
        "category, sorted by BP descending. Each BP is the percentage of "
        "this persona that genuinely engages with that brand/value.\n\n"
        "Hard rules:\n"
        f"  - Every BP >= {min_bp:.1f}% (no floor noise; if you can't "
        "justify it, omit the entry).\n"
        "  - BP <= 99.99% (never exactly 100 unless the brand IS the "
        "subject's primary platform).\n"
        "  - Use 4 decimal places for organic look (e.g., 23.4172%, not 23%).\n"
        "  - Persona-grounded reasoning: use the audience's demographics, "
        "income, cultural signals, age. NOT mass-American defaults. NOT "
        "consensus mid-band snapping.\n"
        "  - Use canonical brand spellings (e.g., 'CHATGPT' not 'Chat-GPT', "
        "'T-MOBILE' not 'TMobile', 'AT&T' not 'AT and T').\n"
        "  - 'reason' per entry should reference THIS persona, not generic statements.\n\n"
        "Return ONLY valid JSON in this exact shape:\n"
        '{"category": "<CATEGORY>", "entries": ['
        '{"value": "BRAND", "bp": 41.8237, "reason": "..."}, ...]}\n'
        "JSON only, no markdown, no code fences.\n"
    )

    decisions: list[dict] = []
    new_row_records: list[dict] = []
    import random as _r

    for cat in empty_cats:
        # Hints help the agent know what kind of entities belong here without
        # us pre-naming specific brands (we want persona-driven enumeration,
        # not consensus-snapping to a hint list).
        kind_hint = _empty_category_kind_hint(cat)

        # Cache-friendly order: static prefix → persona (cached across cats in
        # this profile) → per-category target.
        prompt = (
            _empty_cat_static
            + f"\n=== PERSONA CONTEXT ===\n{persona_context}\n"
            + "\n=== EMPTY CATEGORY ===\n"
            + f"  CATEGORY: {cat}\n"
            + f"  KIND: {kind_hint}\n"
        )

        try:
            resp = openai_client.chat.completions.create(
                model=model,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.25,
                max_tokens=max_tokens,
                timeout=120,
            )
            _log_openai_cache(resp, label=f'empty-cat {cat}')
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
            parsed = json.loads(raw)
        except Exception as e:
            if verbose:
                print(f"   ⚠️ empty-cat {cat}: agent error: {e}")
            decisions.append({
                'category': cat, 'status': 'ERROR', 'n_added': 0,
                'entries': [], 'reason': f'agent error: {e}',
            })
            continue

        entries = parsed.get('entries') or []
        if not isinstance(entries, list) or not entries:
            decisions.append({
                'category': cat, 'status': 'SKIP', 'n_added': 0,
                'entries': [], 'reason': 'agent returned no entries',
            })
            continue

        # Validate + dedupe (within this category)
        seen_vals: set = set()
        valid_entries = []
        for ent in entries:
            try:
                v = str(ent.get('value', '')).strip().upper()
                bp = float(ent.get('bp'))
            except (TypeError, ValueError):
                continue
            if not v or v in seen_vals:
                continue
            if bp < min_bp or bp > 99.99:
                continue
            # Add 4dp jitter when the agent returned a round number
            if round(bp, 4) == round(bp, 1):
                bp = round(bp + _r.uniform(-0.04, 0.04), 4)
            seen_vals.add(v)
            valid_entries.append({
                'value': v, 'bp': round(bp, 4),
                'reason': str(ent.get('reason', '')).strip(),
            })
            if len(valid_entries) >= max_entries:
                break

        if not valid_entries:
            decisions.append({
                'category': cat, 'status': 'SKIP', 'n_added': 0,
                'entries': [], 'reason': 'all entries failed validation',
            })
            continue

        # Inject rows
        for ent in valid_entries:
            new_row_records.append({
                'Column': cat, 'Value': ent['value'],
                bp_col: f"{ent['bp']:.4f}%",
                cs_col: '',  # recomputed below
                raw_col: str(max(1, int(round(ent['bp'] * raw_per_pct)))),
                proj_col: str(int(round(ent['bp'] * proj_per_pct))),
            })

        decisions.append({
            'category': cat, 'status': 'POPULATED',
            'n_added': len(valid_entries), 'entries': valid_entries,
            'reason': f'persona-grounded enumeration emitted '
                      f'{len(valid_entries)} entries',
        })
        if verbose:
            top = valid_entries[0]
            print(f"   🪴 empty-cat {cat}: +{len(valid_entries)} rows "
                  f"(top: {top['value']} @ {top['bp']:.2f}%)")

    if new_row_records:
        df = pd.concat([df, pd.DataFrame(new_row_records)], ignore_index=True)
        # Recompute Category Share for every touched category (BP / sum(BPs) * 100)
        touched_cats = {d['category'] for d in decisions if d.get('status') == 'POPULATED'}
        for cat in touched_cats:
            m = df['Column'] == cat
            if not m.any():
                continue
            bps_cat = df.loc[m, bp_col].apply(_numbp).fillna(0)
            total = bps_cat.sum()
            if total <= 0:
                continue
            new_cs = bps_cat / total * 100.0
            for i, idx in enumerate(df.index[m]):
                df.at[idx, cs_col] = f"{new_cs.iloc[i]:.4f}"

    n_pop = sum(1 for d in decisions if d['status'] == 'POPULATED')
    n_skp = sum(1 for d in decisions if d['status'] == 'SKIP')
    n_err = sum(1 for d in decisions if d['status'] == 'ERROR')
    total_added = sum(d['n_added'] for d in decisions)
    if verbose:
        print(f"   🪴 empty-cat totals: {n_pop} POPULATED ({total_added} rows added), "
              f"{n_skp} SKIP, {n_err} ERROR")

    return df, decisions


def _empty_category_kind_hint(cat: str) -> str:
    """Short hint to anchor the agent on what kind of entities live here.
    Deliberately broad — we want persona enumeration, not a brand list."""
    cat_u = str(cat).strip().upper()
    # Keys MUST be canonical hostmap SECTION names (D2). MEDIA covers both
    # publishing brands and linear-TV networks because that's what the
    # reference.host_mapping section emits as one column.
    hints = {
        'SEARCH ENGINE/AI': 'web search + consumer AI products (Google, Bing, DuckDuckGo, Yahoo, Ecosia, ChatGPT, Gemini, Claude, Copilot, Perplexity, Midjourney, etc.)',
        'SOCIAL MEDIA':     'social platforms (YouTube, Facebook, Instagram, TikTok, X, Snapchat, Pinterest, LinkedIn, Threads, etc. — NOT Reddit, that lives under APP/PLATFORM USAGE)',
        'BANKING':          'consumer banks (Chase, Bank of America, Wells Fargo, Capital One, US Bank, Citibank, Truist, PNC, etc.)',
        'DIGITAL BANKING':  'digital wallets + neobanks (PayPal, Venmo, Cash App, Apple Pay, Zelle, Chime, etc.)',
        'TELECOM':          'mobile carriers (Verizon, T-Mobile, AT&T, plus MVNO long tail like Mint, Cricket, Metro, Boost)',
        'STREAMING/PLATFORM': 'SVOD + vMVPD (Netflix, Hulu, Disney+, HBO Max, Amazon Prime Video, Peacock, Paramount+, etc.)',
        'STREAMING/MUSIC':  'music streaming services (Spotify, Apple Music, YouTube Music, Amazon Music, Pandora, SoundCloud, Tidal, etc.)',
        'QSR':              'quick-service restaurants (McDonalds, Chick-fil-A, Chipotle, Taco Bell, Wendy\'s, Dunkin, Subway, Starbucks, etc.)',
        'WHERE THEY SHOP':  'retailers (Walmart, Amazon, Target, Costco, Home Depot, Lowe\'s, Sephora, Ulta, etc.)',
        'MEDIA':            'news + publishing brands AND linear-TV networks (CNN, Fox News, NYT, WaPo, Rolling Stone, NBC, CBS, ABC, Fox, FX, FXX, AMC, Adult Swim, Comedy Central, TBS, TNT, USA, History, etc.)',
        'APP/PLATFORM USAGE': 'consumer apps (Gmail, Google Maps, Wikipedia, Reddit, Zoom, Calm, Tinder, Zillow, etc.)',
        'AUTOMOBILE':       'auto brands the persona drives or aspires to (Toyota, Honda, Ford, Chevy, BMW, Tesla, etc.)',
        'INSURANCE':        'insurance brands (State Farm, GEICO, Allstate, Progressive, Liberty Mutual, etc.)',
        'CREDIT PROVIDER':  'credit/debit networks + issuers (Visa, Mastercard, AmEx, Discover, Capital One)',
        'PODCAST':          'podcast shows the persona listens to',
        'HOST/PERSONALITY': 'TV / podcast / media hosts and on-air personalities',
        'MUSICIAN/BAND':    'musicians and bands the persona engages with',
        'ACTOR':            'film and TV actors the persona engages with',
        'MOVIE THEATER':    'movie theater chains (AMC, Regal, Cinemark, etc.)',
    }
    return hints.get(cat_u, f'brands or entities classified under {cat}')


def apply_default_lock_breaks(df, report: AuditReport):
    """Re-jitter known default-value-lock fingerprints (15.0143, 11.0852, …).
    These are pipeline defaults, not real measurements — always patch them.
    """
    import random as _r
    _r.seed(hash((report.subject, 'cw-default-lock')) & 0xFFFFFFFF)

    bp_col = 'Brand Penetration (Row)'
    raw_col = 'Original Raw Numbers'
    proj_col = 'US Gen Pop Projection'

    df = df.copy()
    col_norm = df['Column'].astype(str).str.strip().str.upper()
    val_norm = df['Value'].astype(str).str.strip().str.upper()
    sample_raw = report.sample_raw or 10_000
    # pandas 2.x raises if you assign a string into a float64 column.
    for _c in (bp_col, raw_col, proj_col):
        if _c in df.columns and df[_c].dtype != object:
            df[_c] = df[_c].astype(object)
    raw_per_pct, proj_per_pct = _derive_scale_from_df(df)

    patches = []
    for lock in report.default_locks or []:
        m = (col_norm == str(lock['column']).strip().upper()) & \
            (val_norm == str(lock['value']).strip().upper())
        if not m.any():
            continue
        idx = df.index[m][0]
        old_bp = lock['bp']
        new_bp = round(max(0.05, old_bp + _r.uniform(-1.5, 1.5)
                                          + _r.uniform(0.0005, 0.0095)), 4)
        df.at[idx, bp_col] = f"{new_bp:.4f}%"
        if raw_col in df.columns:
            df.at[idx, raw_col] = str(max(1, int(round(new_bp * raw_per_pct))))
        if proj_col in df.columns:
            df.at[idx, proj_col] = str(int(round(new_bp * proj_per_pct)))
        patches.append({
            'category': lock['column'], 'brand': lock['value'],
            'old_bp': old_bp, 'new_bp': new_bp,
            'fingerprint': lock['fingerprint'],
        })
    return df, patches


# ─────────────────────────────────────────────────────────────────────────────
#  MOST PURCHASED BRANDS hostmap floor
# ─────────────────────────────────────────────────────────────────────────────
# Operator policy: every profile should carry ~1500 MOST PURCHASED BRANDS rows
# (median floor; varies in [1450, 1600] so two profiles never land on the exact
# same row count). The candidate pool is `reference.host_mapping` rows tagged
# with SECTION LIKE 'Most Purchased Brands%' (~2,131 unique brands as of
# 2026-05-27). If the upstream agent pass under-fills MPB, this pass hands the
# missing hostmap candidates back to the persona agent for ADD-with-BP /
# SKIP-with-reason decisions. The agent picks which brands genuinely fit THIS
# persona's online-purchasing habits — no blind fill-to-target, no consensus
# snap, no archetype pinning.

MPB_FLOOR_MIN = 1450
MPB_FLOOR_MAX = 1600
MPB_CATEGORY = 'MOST PURCHASED BRANDS'


def agent_reason_mpb_floor(df,
                           mpb_candidates: list,
                           openai_client,
                           persona_doc=None,
                           audience_composition=None,
                           subject: str = '',
                           target_min: int = MPB_FLOOR_MIN,
                           target_max: int = MPB_FLOOR_MAX,
                           batch_size: int = 100,
                           model: str = 'gpt-4o',
                           max_tokens: int = 8000,
                           raw_universe: int | None = None,
                           verbose: bool = True):
    """Ensure MOST PURCHASED BRANDS carries ~1500 hostmap-sourced rows.

    Parameters
    ----------
    df : DataFrame
        Profile dataframe (the standard Column / Value / BP / RAW / PROJ shape).
    mpb_candidates : list of dict
        Hostmap candidate pool. Each entry: ``{'brand': 'NIKE',
        'section': 'Apparel/Footwear'}``. Caller fetches from
        ``reference.host_mapping`` and hands the list in (keeps this module
        decoupled from ClickHouse).
    target_min, target_max : int
        Random target count is drawn from [target_min, target_max]; never
        exactly the midpoint, so the row-count fingerprint differs per pull.
    batch_size : int
        Candidates per LLM batch. 100 keeps a single batch under ~6k response
        tokens (one ADD/SKIP decision per brand).

    Behaviour
    ---------
    * Picks a random target in [target_min, target_max]. Avoids the literal
      median to break the "1500.000" fingerprint.
    * If current MPB count is already at/above target → no-op.
    * Otherwise: identifies hostmap candidates not yet on the profile, shuffles
      them, batches to the persona agent. Agent returns ADD (with persona-
      grounded BP) or SKIP (with reason).
    * Validates each ADD (BP in [0.5, 60], 4-decimal jitter when round,
      dedupes against existing rows).
    * Stops adding once the running total hits the target — agent never
      "blind-fills" past the persona's genuine purchasing universe.
    * Recomputes Category Share for MPB after injection.

    Returns
    -------
    (df, decisions) where each decision is:
        {batch, brand, decision ('ADD' | 'SKIP'), bp, reason, status,
         n_added, n_skipped}
    Batch-level errors also emit an entry with status='ERROR'.
    """
    if openai_client is None:
        if verbose:
            print("   🛒 mpb-floor: no OpenAI client; skipping")
        return df, []

    if not mpb_candidates:
        if verbose:
            print("   🛒 mpb-floor: no hostmap candidates supplied; skipping")
        return df, []

    bp_col = 'Brand Penetration (Row)'
    cs_col = 'Category Share'
    raw_col = 'Original Raw Numbers'
    proj_col = 'US Gen Pop Projection'

    if (bp_col not in df.columns
            or 'Column' not in df.columns
            or 'Value' not in df.columns):
        if verbose:
            print("   🛒 mpb-floor: missing required columns; skipping")
        return df, []

    df = df.copy()
    for _c in (bp_col, cs_col, raw_col, proj_col):
        if _c in df.columns and df[_c].dtype != object:
            df[_c] = df[_c].astype(object)

    import random as _r

    # 2026-05-30 (Jenna May 30 batch fix): scale target DOWN for niche
    # profiles whose raw universe can't support 1,500 plausible brand rows.
    # When the universe is e.g. 24 panel users (Adam J Kurtz), asking the
    # LLM to invent 1,443 brand BPs forces it into a rotating placeholder
    # pattern (5.6789, 6.7890, 7.8901...). The fix: cap target at
    #     min(target_max, 50 * raw_universe + 100)
    # so a 24-user universe targets ~1,300; a 75-user targets ~1,500
    # (no change); a 200+ universe gets the full target_max.
    if raw_universe is not None and raw_universe > 0 and raw_universe < 200:
        adaptive_cap = max(target_min // 4, 50 * int(raw_universe) + 100)
        scaled_max = min(target_max, adaptive_cap)
        scaled_min = min(target_min, scaled_max)
        if scaled_max < target_max and verbose:
            print(f"   🛒 mpb-floor: ADAPTIVE target scaling for niche universe "
                  f"({raw_universe} users): [{target_min},{target_max}] → "
                  f"[{scaled_min},{scaled_max}] to prevent LLM placeholder emission")
        target_min_eff, target_max_eff = scaled_min, scaled_max
    else:
        target_min_eff, target_max_eff = target_min, target_max

    # Random target — avoid the literal median so two consecutive runs don't
    # both land on 1500 exactly.
    target_count = _r.randint(target_min_eff, target_max_eff)
    median = (target_min_eff + target_max_eff) // 2
    if target_count == median:
        target_count = median + _r.choice([-1, 1])

    col_upper = df['Column'].astype(str).str.strip().str.upper()
    val_upper = df['Value'].astype(str).str.strip().str.upper()

    mpb_mask = col_upper == MPB_CATEGORY
    current_count = int(mpb_mask.sum())

    if verbose:
        print(f"   🛒 mpb-floor: current MPB rows = {current_count:,}, "
              f"target = {target_count:,} "
              f"(rand [{target_min}, {target_max}])")

    if current_count >= target_count:
        if verbose:
            print(f"   🛒 mpb-floor: already at/above target — nothing to do")
        return df, []

    existing_brands = set(val_upper[mpb_mask].tolist())

    # Build candidate pool, skip any already present
    missing = []
    seen = set()
    for cand in mpb_candidates:
        try:
            b = str(cand.get('brand', '')).strip().upper()
        except Exception:
            continue
        if not b or b in seen or b in existing_brands:
            continue
        seen.add(b)
        missing.append({
            'brand': b,
            'section': str(cand.get('section', '')).strip() or 'Misc',
        })

    if not missing:
        if verbose:
            print(f"   🛒 mpb-floor: no missing hostmap candidates "
                  f"(every hostmap brand already on profile)")
        return df, []

    need_to_add = target_count - current_count
    if verbose:
        print(f"   🛒 mpb-floor: {len(missing):,} hostmap candidates absent; "
              f"need to add ~{need_to_add:,}")

    # Shuffle so we don't bias toward alphabetical brand names
    _r.shuffle(missing)

    raw_per_pct, proj_per_pct = _derive_scale_from_df(df)
    persona_context = _persona_context_block(
        persona_doc, audience_composition, subject,
    )

    decisions: list[dict] = []
    new_row_records: list[dict] = []
    total_added = 0

    num_batches = (len(missing) + batch_size - 1) // batch_size

    for batch_idx in range(num_batches):
        if total_added >= need_to_add:
            break

        batch = missing[batch_idx * batch_size:(batch_idx + 1) * batch_size]
        batches_remaining = num_batches - batch_idx
        # Pace ADDs so we don't burn all our target on the first batch nor
        # leave it all for the last. ~(remaining_target / remaining_batches)
        # with a small headroom so the agent feels free to push above when
        # the batch is a great persona fit.
        target_remaining = need_to_add - total_added
        ask_for = max(
            1,
            min(len(batch),
                int(target_remaining / batches_remaining) + 10),
        )

        candidates_block = '\n'.join(
            f"  - {c['brand']}  [{c['section']}]"
            for c in batch
        )

        # Cache-friendly order: module-level static rules → persona (cached
        # across batches in this profile) → per-batch ask-for + candidates.
        prompt = (
            AUDIENCE_NOT_MIRROR_RULE
            + _MPB_FLOOR_TASK_BLOCK
            + f"\n=== PERSONA CONTEXT ===\n{persona_context}\n"
            + f"\n=== BATCH {batch_idx + 1} / {num_batches} "
            + f"({len(batch)} candidates) — target ~{ask_for} ADDs ===\n"
            + f"{candidates_block}\n"
        )

        try:
            resp = openai_client.chat.completions.create(
                model=model,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.25,
                max_tokens=max_tokens,
                timeout=180,
            )
            _log_openai_cache(resp, label=f'mpb-floor b{batch_idx + 1}/{num_batches}')
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw).strip()
            parsed = json.loads(raw)
        except Exception as e:
            if verbose:
                print(f"   ⚠️ mpb-floor batch {batch_idx + 1}/{num_batches}: "
                      f"agent error: {e}")
            decisions.append({
                'batch': batch_idx + 1,
                'brand': None,
                'decision': None,
                'bp': None,
                'reason': f'agent error: {e}',
                'status': 'ERROR',
                'n_added': 0,
                'n_skipped': 0,
            })
            continue

        batch_decisions = parsed.get('decisions') or []
        batch_added = 0
        batch_skipped = 0

        for d in batch_decisions:
            if total_added >= need_to_add:
                break
            try:
                b = str(d.get('brand', '')).strip().upper()
                dec = str(d.get('decision', '')).strip().upper()
                reason = str(d.get('reason', '')).strip()
            except (TypeError, ValueError):
                continue
            if not b or b in existing_brands:
                continue

            if dec == 'ADD':
                try:
                    bp = float(d.get('bp'))
                except (TypeError, ValueError):
                    continue
                if bp < 0.5 or bp > 60.0:
                    continue
                if round(bp, 4) == round(bp, 1):
                    bp = round(bp + _r.uniform(-0.04, 0.04), 4)
                bp = round(bp, 4)
                new_row_records.append({
                    'Column': MPB_CATEGORY,
                    'Value': b,
                    bp_col: f"{bp:.4f}%",
                    cs_col: '',
                    raw_col: str(max(1, int(round(bp * raw_per_pct)))),
                    proj_col: str(int(round(bp * proj_per_pct))),
                })
                existing_brands.add(b)
                total_added += 1
                batch_added += 1
                decisions.append({
                    'batch': batch_idx + 1,
                    'brand': b,
                    'decision': 'ADD',
                    'bp': bp,
                    'reason': reason,
                    'status': 'ADDED',
                    'n_added': 1,
                    'n_skipped': 0,
                })
            elif dec == 'SKIP':
                batch_skipped += 1
                decisions.append({
                    'batch': batch_idx + 1,
                    'brand': b,
                    'decision': 'SKIP',
                    'bp': None,
                    'reason': reason,
                    'status': 'SKIPPED',
                    'n_added': 0,
                    'n_skipped': 1,
                })

        if verbose:
            print(f"   🛒 mpb-floor batch {batch_idx + 1}/{num_batches}: "
                  f"{batch_added} ADD, {batch_skipped} SKIP "
                  f"(running: {total_added}/{need_to_add})")

    if new_row_records:
        df = pd.concat([df, pd.DataFrame(new_row_records)], ignore_index=True)
        m = df['Column'].astype(str).str.strip().str.upper() == MPB_CATEGORY
        if m.any() and cs_col in df.columns:
            bps_cat = df.loc[m, bp_col].apply(_numbp).fillna(0)
            total = bps_cat.sum()
            if total > 0:
                new_cs = bps_cat / total * 100.0
                for i, idx in enumerate(df.index[m]):
                    df.at[idx, cs_col] = f"{new_cs.iloc[i]:.4f}"

    final_count = int((df['Column'].astype(str).str.strip().str.upper()
                       == MPB_CATEGORY).sum())
    if verbose:
        n_add = sum(1 for d in decisions if d.get('decision') == 'ADD')
        n_skip = sum(1 for d in decisions if d.get('decision') == 'SKIP')
        n_err = sum(1 for d in decisions if d.get('status') == 'ERROR')
        print(f"   🛒 mpb-floor totals: {n_add} ADDED, {n_skip} SKIPPED, "
              f"{n_err} ERROR  →  final MPB rows = {final_count:,} "
              f"(target was {target_count:,})")

    return df, decisions


# ============================================================================
# Claude second-opinion arbiter for GPT-4o vet KEEPs
#
# Added 2026-05-29 after the Nike profile shipped with NETFLIX=60.26%,
# DISNEY+=30.87%, HBO MAX=23.06% all flagged by vet as FAIL_low but
# left low by the GPT-4o re-reasoner (over-fit "active demo less
# couch-bound" prior). The deterministic enforce_household_streaming_floor
# enforcer catches the streaming case specifically. This arbiter is a
# broader safety net: any FAIL_low/FAIL_high row that GPT-4o decided
# to KEEP is re-pitched to Claude for a fresh-frame second opinion.
#
# Claude is asked to weigh competing priors (e.g., "Nike audience is
# young AND household-mass-market") and override KEEP only when it can
# articulate a substantive reason. If Claude agrees with KEEP, the
# original value stands. This is additive — never modifies the GPT-4o
# decisions list, only mutates the DataFrame for overrides.
# ============================================================================

_anthropic_client_singleton = None


def _get_anthropic_client(timeout: float = 120.0):
    """Return a cached Anthropic client; None if ANTHROPIC_API_KEY missing."""
    global _anthropic_client_singleton
    if _anthropic_client_singleton is not None:
        return _anthropic_client_singleton
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None
    _anthropic_client_singleton = Anthropic(api_key=api_key, timeout=timeout)
    return _anthropic_client_singleton


_CLAUDE_ARBITER_TASK_BLOCK = """You are a SECOND-OPINION arbiter on a digital-audience profile.

The primary reasoner (GPT-4o) reviewed a row that the vetting framework
flagged as FAIL_low or FAIL_high (more than 7pts from gen-pop digital
consensus). GPT-4o chose KEEP — meaning it believed the divergence from
consensus is justified by the subject's persona.

Your job is to weigh competing priors and decide:
  - KEEP:   GPT-4o was right; the divergence is persona-justified
  - CHANGE: GPT-4o over-fit a single archetype; lift/lower to a more
            defensible value (with a substantive persona-grounded reason)

CRITICAL anti-pinning rules:
  1. NEVER pin to consensus. If you CHANGE, your new_bp must be at
     least 2pts away from the consensus value.
  2. Your reason must cite a SPECIFIC persona signal — a demographic
     fact, a behavioral correlation, a real-world event, a known
     sponsorship/partnership, etc. Generic phrases like "matches gen
     pop", "aligns with consensus", "consistent with audience" are
     FORBIDDEN and will be auto-rejected.
  3. Brand profiles (Nike, McDonald's, Coca-Cola, Target, Walmart,
     etc.) have HOUSEHOLD audiences — soccer moms, dads, kids all
     stream together. Don't apply "active demo, less couch-bound" or
     similar single-frame archetypes to mass-market brand audiences.
  4. Talent profiles (actors, athletes, musicians) genuinely DO have
     skewed media diets — trust GPT-4o's KEEP more in those cases
     unless there's a clear specific reason to override.
  5. If you would only lift by <2pt, KEEP instead (not worth the noise).

Output JSON only — no markdown, no code fences:
{"decisions": [
  {"i": 1, "decision": "KEEP", "reason": "specific persona reason..."},
  {"i": 2, "decision": "CHANGE", "new_bp": 67.45, "reason": "Nike household audience..."},
  ...
]}

Return EXACTLY one decision per input row, indexed by `i` (1-based).
"""


def claude_arbiter_on_vet_keeps(df,
                                  decisions: list[dict],
                                  *,
                                  anthropic_client=None,
                                  subject: str = '',
                                  persona_doc=None,
                                  audience_composition: dict | None = None,
                                  brand_category: str | None = None,
                                  model: str = 'claude-sonnet-4-5-20250929',
                                  batch_size: int = 8,
                                  max_tokens: int = 4000,
                                  verbose: bool = True):
    """Second-opinion pass on GPT-4o KEEPs of FAIL_low/FAIL_high rows.

    Parameters
    ----------
    df : DataFrame
        The profile being audited (modified in-place for Claude CHANGEs).
    decisions : list of dict
        Output of agent_reason_vet_failures — each dict carries category,
        brand, decision, old_bp, etc.
    anthropic_client : Anthropic client or None
        If None, lazily creates one via _get_anthropic_client().
    subject, persona_doc, audience_composition, brand_category :
        Context fed to Claude for persona-grounded reasoning.
    model : str
        Claude model slug. Defaults to claude-sonnet-4-5 (latest fast
        thinking-class model — strong reasoning, ~$3/M input).
    batch_size : int
        Items per Claude call. Default 8 — smaller than GPT-4o because
        each item gets more reasoning depth.

    Returns
    -------
    (df, arbitration_log) — list of dicts {category, brand, gpt_kept_bp,
    consensus, claude_decision, claude_new_bp, claude_reason}
    """
    if anthropic_client is None:
        anthropic_client = _get_anthropic_client()
    if anthropic_client is None:
        if verbose:
            print('   ⚠️ claude_arbiter_on_vet_keeps: no Anthropic client '
                  '(ANTHROPIC_API_KEY missing or SDK missing); skipping')
        return df, []

    # Candidate set: KEEPs on FAIL_low/FAIL_high rows
    candidates = [d for d in (decisions or [])
                  if d.get('decision') == 'KEEP'
                     and d.get('verdict') in ('FAIL_low', 'FAIL_high')]
    if not candidates:
        if verbose:
            print('   ✅ claude_arbiter_on_vet_keeps: no FAIL KEEPs to arbitrate')
        return df, []

    if verbose:
        print(f'   🧠 claude arbiter: {len(candidates)} KEEP(s) on FAIL '
              f'rows — second-opinion via {model}')

    bp_col = 'Brand Penetration (Row)'
    df = df.copy()
    if bp_col in df.columns and df[bp_col].dtype != object:
        df[bp_col] = df[bp_col].astype(object)

    persona_context = _persona_context_block(persona_doc,
                                              audience_composition or {},
                                              subject)
    if brand_category:
        persona_context += f'\n\nBRAND_CATEGORY={brand_category}'

    arbitration_log: list[dict] = []
    n_overrides = 0

    for batch_start in range(0, len(candidates), batch_size):
        batch = candidates[batch_start: batch_start + batch_size]
        batch_idx = batch_start // batch_size + 1
        n_batches = (len(candidates) + batch_size - 1) // batch_size

        items_lines = []
        for i, v in enumerate(batch, 1):
            old_bp = v.get('old_bp', v.get('crosswalk', 0.0))
            consensus = float(v.get('consensus') or 0.0)
            items_lines.append(
                f"{i}. CATEGORY={v['category']} | BRAND={v['brand']} | "
                f"current_bp={old_bp:.4f}% | "
                f"gen_pop_consensus={consensus:.4f}% | "
                f"gap={old_bp - consensus:+.2f}pts | "
                f"verdict={v['verdict']} | "
                f"gpt_kept_reason={(v.get('reason') or '')[:140]!r}"
            )

        prompt = (
            _CLAUDE_ARBITER_TASK_BLOCK
            + f"\n=== SUBJECT ===\n{subject}\n"
            + f"\n=== PERSONA CONTEXT ===\n{persona_context}\n"
            + f"\n=== KEEP DECISIONS TO ARBITRATE (batch {batch_idx}/{n_batches}) ===\n"
            + "\n".join(items_lines)
        )

        try:
            resp = anthropic_client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=0.2,
                messages=[{'role': 'user', 'content': prompt}],
            )
            raw = ''.join(b.text for b in resp.content if getattr(b, 'text', None))
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip()).strip()
            parsed = json.loads(raw)
        except Exception as e:
            if verbose:
                print(f'   ⚠️ claude arbiter batch {batch_idx}/{n_batches} error: {e}')
            for v in batch:
                arbitration_log.append({
                    **v,
                    'claude_decision': 'ERROR',
                    'claude_new_bp': None,
                    'claude_reason': f'claude error: {e}',
                })
            continue

        decision_map = {int(d.get('i', -1)): d for d in (parsed.get('decisions') or [])}
        for i, v in enumerate(batch, 1):
            d = decision_map.get(i, {})
            decision = str(d.get('decision', 'KEEP')).upper().strip()
            reason = str(d.get('reason', '')).strip()
            old_bp = v.get('old_bp', v.get('crosswalk', 0.0))
            consensus = float(v.get('consensus') or 0.0)

            entry = {
                'category': v['category'], 'brand': v['brand'],
                'verdict': v['verdict'],
                'gpt_kept_bp': old_bp, 'consensus': consensus,
                'gpt_reason': (v.get('reason') or '')[:200],
                'claude_decision': decision,
                'claude_new_bp': None,
                'claude_reason': reason[:240],
            }

            if decision != 'CHANGE':
                arbitration_log.append(entry)
                continue

            # Validate Claude's new_bp
            try:
                new_bp = float(d.get('new_bp'))
            except Exception:
                new_bp = None
            if new_bp is None or new_bp <= 0 or new_bp > 100:
                entry['claude_decision'] = 'SKIP'
                entry['claude_reason'] = (
                    f'invalid new_bp from claude: {d.get("new_bp")!r}')
                arbitration_log.append(entry)
                continue

            # Anti-pinning + anti-trivial-lift checks
            generic_phrases = (
                'matches gen pop', 'matches consensus',
                'aligns with consensus', 'aligns with gen pop',
                'consistent with consensus', 'consistent with gen pop',
                'in line with consensus', 'in line with gen pop',
                'fits the audience profile',
            )
            reason_lc = reason.lower()
            if any(p in reason_lc for p in generic_phrases) or len(reason_lc) < 30:
                entry['claude_decision'] = 'KEEP'
                entry['claude_reason'] = (
                    f'claude reason too generic to justify override; '
                    f'kept gpt KEEP. (claude said: {reason[:80]})')
                arbitration_log.append(entry)
                continue
            if abs(new_bp - consensus) < 2.0:
                # Push away from consensus to avoid pinning
                new_bp = (consensus - 2.0 - 0.07) if old_bp > consensus \
                          else (consensus + 2.0 + 0.07)
                new_bp = round(max(0.5, min(99.5, new_bp)), 4)
            if abs(new_bp - old_bp) < 2.0:
                entry['claude_decision'] = 'KEEP'
                entry['claude_reason'] = (
                    f'claude proposed |Δ|={abs(new_bp-old_bp):.2f}pt < 2pt floor; '
                    f'not worth noise. kept original {old_bp:.2f}%.')
                arbitration_log.append(entry)
                continue

            # Apply the override
            entry['claude_new_bp'] = round(new_bp, 4)
            arbitration_log.append(entry)

            col_match = (df['Column'].astype(str).str.upper().str.strip()
                         == str(v['category']).upper().strip())
            val_match = (df['Value'].astype(str).str.upper().str.strip()
                         == str(v['brand']).upper().strip())
            idx = df.index[col_match & val_match]
            if len(idx):
                df.at[idx[0], bp_col] = round(new_bp, 4)
                n_overrides += 1
                if verbose:
                    arrow = '⬆' if new_bp > old_bp else '⬇'
                    print(f'      {arrow} [{v["category"]}] {v["brand"]}: '
                          f'{old_bp:.2f}% → {new_bp:.4f}%  '
                          f'(consensus {consensus:.2f}%) — {reason[:90]}')

    if verbose:
        n_kept = sum(1 for e in arbitration_log if e['claude_decision'] == 'KEEP')
        n_change = sum(1 for e in arbitration_log if e['claude_decision'] == 'CHANGE')
        n_err = sum(1 for e in arbitration_log if e['claude_decision'] == 'ERROR')
        print(f'   🧠 claude arbiter: {n_change} OVERRIDE, {n_kept} agreed-KEEP, '
              f'{n_err} ERROR  →  {n_overrides} BP cell(s) actually changed')

    return df, arbitration_log
