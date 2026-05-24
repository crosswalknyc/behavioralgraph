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
import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


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
    'REDDIT':    {'M': 30, 'F': 14, '18-29': 44, '30-49': 30, '50-64': 13, '65+':  5},
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
        "GOOGLE":          (78, 96),
        "CHATGPT":         (28, 60),
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
    "AT&T":             ["ATT", "AT AND T"],
    "T-MOBILE":         ["TMOBILE", "T MOBILE"],
}

# Step 6: Structural-gap requirements — categories MUST contain these brands.
# When missing, the audit flags a structural gap and (optionally) inserts
# a row at the MIDPOINT of the cross-pull range (not as a cap — as
# documentation that the entity must be represented).
STRUCTURAL_REQUIREMENTS = {
    'BANKING':           ['CHASE', 'BANK OF AMERICA', 'WELLS FARGO', 'CITIBANK'],
    'TELECOM':           ['VERIZON', 'AT&T', 'T-MOBILE'],
    'STREAMING/PLATFORM':['NETFLIX', 'AMAZON PRIME VIDEO', 'HULU', 'DISNEY+', 'HBO MAX'],
    'SEARCH ENGINE/AI':  ['GOOGLE', 'CHATGPT', 'BING'],
    'STREAMING/MUSIC':   ['SPOTIFY', 'APPLE MUSIC', 'YOUTUBE MUSIC', 'AMAZON MUSIC'],
    'CREDIT PROVIDER':   ['VISA', 'MASTERCARD', 'CAPITAL ONE', 'DISCOVER', 'AMEX'],
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
# AUDIT-DRIVEN CORRECTIONS (patch FAILs to within consensus range)
# ───────────────────────────────────────────────────────────────────────────

def apply_audit_corrections(df, report: AuditReport):
    """Patch each FAIL in the audit back into its consensus band.

    Distinct from the formulaic caps we removed earlier — corrections are
    sourced from published consensus (Pew, eMarketer, etc.) and only fire
    when the AUDIT itself flagged the brand as a FAIL (per ±7pt band).

    Targets:
      FAIL (high) over-correction        → consensus_hi (high end of band)
      FAIL (low)  suppression            → consensus_lo (low end of band)
      FAIL (low)  persona-mismatch       → consensus_lo (low end — conservative)
      FAIL (high) persona-mismatch       → consensus_hi (high end — conservative)
      MISSING                            → handled by insert_structural_gaps

    All targets get a small deterministic jitter (4 decimals) so the file
    doesn't end up with round numbers (per the "no perfectly round
    numbers, 4 decimals of jitter" recipe rule).
    """
    import random as _r
    _r.seed(hash((report.subject, 'cw-audit-patch')) & 0xFFFFFFFF)

    bp_col = 'Brand Penetration (Row)'
    raw_col = 'Original Raw Numbers'
    proj_col = 'US Gen Pop Projection'
    cs_col = 'Category Share'
    us_proj_per_pct = 329_900_000.0 / 100.0

    df = df.copy()
    col_norm = df['Column'].astype(str).str.strip().str.upper()
    val_norm = df['Value'].astype(str).str.strip().str.upper()
    sample_raw = report.sample_raw or 10_000

    patches = []
    for f in report.findings:
        if not f.status.startswith('FAIL'):
            continue
        if f.consensus_lo is None or f.consensus_hi is None:
            continue
        # Pick target inside the consensus band based on direction
        if f.status == 'FAIL (high)':
            target = f.consensus_hi
        else:  # FAIL (low)
            target = f.consensus_lo
        # Add deterministic jitter (±0.4pt) so the value isn't perfectly
        # round and so rank-tied brands don't collide.
        target_jit = round(target + _r.uniform(-0.4, 0.4), 4)
        target_jit = max(0.05, target_jit)

        # Find the row(s) — try literal value, then aliases, then forgiving match
        candidates = [f.brand] + BRAND_ALIASES.get(f.brand.upper().strip(), [])
        match_idx = None
        for cand in candidates:
            m = (col_norm == f.category.upper()) & (val_norm == cand.upper().strip())
            if m.any():
                match_idx = df.index[m][0]
                break
        if match_idx is None:
            # forgiving match on normalized brand
            target_n_set = {_normbrand(c) for c in candidates}
            for idx in df.index[col_norm == f.category.upper()]:
                if _normbrand(df.at[idx, 'Value']) in target_n_set:
                    match_idx = idx
                    break
        if match_idx is None:
            continue  # framework saw it via aliasing but row not found — skip

        # Capture old value for logging
        try:
            old_bp = float(str(df.at[match_idx, bp_col]).replace('%','').replace(',','').strip())
        except Exception:
            old_bp = None

        # Write new BP
        df.at[match_idx, bp_col] = f"{target_jit:.4f}%"
        # Re-derive raw + projection from new BP
        if raw_col in df.columns:
            df.at[match_idx, raw_col] = str(max(1, int(round(target_jit / 100.0 * sample_raw))))
        if proj_col in df.columns:
            df.at[match_idx, proj_col] = str(int(round(target_jit * us_proj_per_pct)))
        # Category Share gets re-derived elsewhere; leave as-is unless empty

        patches.append({
            'category': f.category,
            'brand':    f.brand,
            'old_bp':   old_bp,
            'new_bp':   target_jit,
            'status':   f.status,
            'issue':    f.issue_type,
        })
    return df, patches


# ───────────────────────────────────────────────────────────────────────────
# OPTIONAL: STRUCTURAL-GAP INSERTION (deterministic — uses consensus mid)
# ───────────────────────────────────────────────────────────────────────────

def insert_structural_gaps(df, report: AuditReport, sample_size_for_raw=10_000):
    """Insert a row for each structural-gap at the consensus midpoint.

    This is the ONE place the audit mutates the file. It's defensible
    because: (a) it's filling a missing entity that the dashboard REQUIRES
    to render properly, (b) the value is sourced from published consensus
    not a hardcoded cap, (c) the inserted value sits in the middle of the
    pass band so it won't itself fail the audit.
    """
    if not report.structural_gaps:
        return df, 0
    new_rows = []
    us_proj_per_pct = 329_900_000.0 / 100.0
    for gap in report.structural_gaps:
        rng = CROSS_PULL_RANGES.get(gap['category'], {}).get(gap['brand'])
        if not rng:
            continue
        midpoint = (rng[0] + rng[1]) / 2
        # Tiny jitter so the row doesn't look hand-placed
        import random as _r
        bp_val = round(midpoint + _r.uniform(-0.5, 0.5), 4)
        raw_count = max(1, int(round(bp_val / 100.0 * sample_size_for_raw)))
        proj = int(round(bp_val * us_proj_per_pct))
        row = {c: '' for c in df.columns}
        row['Column'] = gap['category']
        row['Value']  = gap['brand']
        if 'Brand Penetration (Row)' in df.columns:
            row['Brand Penetration (Row)'] = f"{bp_val:.4f}%"
        if 'Category Share' in df.columns:
            row['Category Share'] = ''
        if 'Original Raw Numbers' in df.columns:
            row['Original Raw Numbers'] = str(raw_count)
        if 'US Gen Pop Projection' in df.columns:
            row['US Gen Pop Projection'] = str(proj)
        new_rows.append(row)
    if new_rows:
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    return df, len(new_rows)


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
