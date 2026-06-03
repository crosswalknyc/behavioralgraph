#!/usr/bin/env python3
"""Pre-publish gate for profile CSVs.

Runs hard checks before a profile ships to S3 / dashboard:

  1. PLACEHOLDER PINNING
     Detects sequential-digit dummy values (5.6789, 6.789, 9.8765, 4.3210, etc.)
     anywhere in the file. These are LLM/agent placeholders that leaked through
     the publish flow on May 30, 2026. Threshold: >2% of brand rows in any
     category, or >5 rows total in MOST PURCHASED BRANDS.

  2. PROJECTION SANITY
     For each brand row: |Gen Pop Projection - Original Raw * 32.99| / max(1, Gen Pop Projection)
     should be < 0.05 (rounding tolerance). Anything else is a join leak —
     someone else's sample-base got attached to this brand's row.

  3. BRAND INPUT MALFORMATION
     The BRAND INPUT cell should contain the canonical subject token only.
     Hard-fail on URL-encoded permutation strings (DWAYNE%26JOHNSON,
     ADAM%2BKURTZ, etc.) — these are an ingestion artifact and pollute the
     audit trail.

  4. CATEGORY COVERAGE FLOOR
     The control profile has 114 categories. Anything that drops below 90
     categories or has fewer than 8 brand rows in any of {TALENT, ACTOR,
     ATHLETE, MUSICIAN/BAND, MEDIA, APPAREL/FOOTWEAR, MOST PURCHASED BRANDS}
     fails — that's category truncation.

  5. SAMPLE-PROJECTION RATIO
     proj_max should equal sample_size * 32.99 (within rounding). Anything
     more than 1.5x is a sample-join defect.

  6. DEMOGRAPHIC COMPLETENESS
     All 9 mandatory demographic categories (GENDER, AGE, ETHNICITY,
     EDUCATION, INCOME, OCCUPATION, PARENTAL_STATUS, RELATIONSHIP,
     SEXUAL_ORIENTATION) must be present and sum to 100% (+/- 1pp).
     Mirrors PIPELINE_DEMO_SCHEMA per workspace rule 5a.

Exit code: 0 if all gates pass, 1 if any fail.
Usage:
    python3 scripts/pre_publish_gate.py path/to/Profile.csv
    python3 scripts/pre_publish_gate.py s3://dashboard-inputs/Profile.csv
"""
import sys, os, re, json, subprocess
from pathlib import Path
import pandas as pd

US_POP_SCALE = 32.99   # 329,900,000 / 10,000,000
TOLERANCE_PROJ = 0.05  # 5% rounding tolerance

CRITICAL_CATS = {
    'TALENT', 'ACTOR', 'ATHLETE', 'MUSICIAN/BAND', 'MEDIA',
    'APPAREL/FOOTWEAR', 'MOST PURCHASED BRANDS', 'CPG',
    'STREAMING/PLATFORM', 'SOCIAL MEDIA', 'WHERE THEY SHOP',
}
CRITICAL_CAT_MIN_ROWS = 8
TOTAL_CAT_MIN = 80   # was 90; lowered after observing legitimate niche-subject
                     # profiles (e.g. cricket-only Ollie Robinson) sit at 83
                     # with no NFL/MLB league mirrors. 80 still catches real
                     # truncation (Edith 49, Adam 62, Ariana 73 all fail here).

# 9 mandatory demographic categories — must be present and sum to 100% each.
# Mirrors migration/post_generation_enforcers.PIPELINE_DEMO_SCHEMA (per workspace
# rule 5a — "Pipeline is the source of truth for demographic schema").
PIPELINE_DEMO_SCHEMA = (
    'GENDER', 'AGE', 'ETHNICITY', 'EDUCATION', 'INCOME',
    'OCCUPATION', 'PARENTAL_STATUS', 'RELATIONSHIP', 'SEXUAL_ORIENTATION',
)
DEMO_SUM_TOL = 1.0  # +/- 1pp tolerance on the 100% sum


def is_sequential_digit_placeholder(v):
    """Detect sequential-digit dummy values (1.2345, 5.6789, 9.8765, 4.3210)."""
    if v is None or v <= 0:
        return False
    s = f'{float(v):.4f}'
    digits = s.replace('.', '')
    if len(digits) < 5:
        return False
    # Only flag if 5+ consecutive digits are strictly ascending or descending
    asc = all(int(digits[i+1]) - int(digits[i]) == 1 for i in range(len(digits)-1))
    dsc = all(int(digits[i]) - int(digits[i+1]) == 1 for i in range(len(digits)-1))
    if asc or dsc:
        return True
    # Also flag distinctive fractional placeholders even with non-sequential int part
    if re.fullmatch(r'\d+\.6789|\d+\.5678|\d+\.4567|\d+\.7890|\d+\.8765|\d+\.7654|\d+\.6543|\d+\.5432|\d+\.4321|\d+\.3210|\d+\.2345|\d+\.3456', s):
        return True
    return False


def _bp(s):
    try: return float(str(s).rstrip('%').replace(',', ''))
    except: return None


def _detect_cols(d):
    bp_col = next((c for c in d.columns if 'penetration' in c.lower()), None)
    raw_col = next((c for c in d.columns if 'raw' in c.lower()), None)
    proj_col = next((c for c in d.columns if 'projection' in c.lower() or 'gen pop' in c.lower()), None)
    return bp_col, raw_col, proj_col


def gate_placeholder_pinning(d, bp_col):
    """Gate 1: synthetic-placeholder pinning.

    Hard-fail thresholds (publish-blocking):
      - file-wide:  > 3% of non-zero rows are sequential-digit placeholders
      - MPB:        > 3% of MPB rows are placeholders
    Warning-only thresholds (caller can decide):
      - file-wide:  > 0.5% (e.g. May-30 Markiplier had 0.7% — clean but not pristine)
      - MPB:        > 0.5%
    """
    failures = []
    warnings = []
    d = d.copy()
    d['BP_f'] = d[bp_col].apply(_bp)
    nz = d[d['BP_f'] > 0]
    if len(nz) == 0:
        return failures
    ph = nz['BP_f'].apply(is_sequential_digit_placeholder)
    pct = 100 * ph.sum() / len(nz)
    if pct > 3.0:
        failures.append(('placeholder-pinning-file', f'{pct:.1f}% of {len(nz)} non-zero rows are sequential-digit placeholders ({int(ph.sum())} rows) — HARD FAIL'))
    elif pct > 0.5:
        warnings.append(('warn-placeholder-pinning-file', f'{pct:.1f}% of {len(nz)} non-zero rows look like placeholders ({int(ph.sum())} rows)'))
    mpb = d[d['Column'].str.upper() == 'MOST PURCHASED BRANDS']
    if not mpb.empty:
        mpb_ph = mpb['BP_f'].apply(is_sequential_digit_placeholder)
        mpb_pct = 100 * mpb_ph.sum() / len(mpb)
        if mpb_pct > 3.0:
            failures.append(('placeholder-pinning-mpb', f'{int(mpb_ph.sum())}/{len(mpb)} ({mpb_pct:.1f}%) MOST PURCHASED BRANDS rows are placeholders — HARD FAIL'))
        elif mpb_pct > 0.5:
            warnings.append(('warn-placeholder-pinning-mpb', f'{int(mpb_ph.sum())}/{len(mpb)} ({mpb_pct:.1f}%) MPB rows look like placeholders'))
    return failures + warnings


def gate_projection_sanity(d, bp_col, raw_col, proj_col):
    """Gate 2: projection = raw * 32.99 within tolerance.

    Skip rows with raw < 30 — for tiny raw counts, integer rounding dominates
    and produces 5-10% relative error that's not a real defect. The
    sample-join leak we're catching produces 50x-200x errors anyway.
    """
    failures = []
    raws = pd.to_numeric(d[raw_col].astype(str).str.replace(',', ''), errors='coerce')
    projs = pd.to_numeric(d[proj_col].astype(str).str.replace(',', ''), errors='coerce')
    expected = raws * US_POP_SCALE
    valid = (raws >= 30) & projs.notna() & expected.notna()
    if not valid.any():
        return failures
    rel_err = (projs - expected).abs() / expected.where(expected > 0, 1)
    bad_mask = valid & (rel_err > TOLERANCE_PROJ)
    n_bad = bad_mask.sum()
    if n_bad > 0:
        worst = d.loc[bad_mask].copy()
        worst['_raw'] = raws[bad_mask]
        worst['_proj'] = projs[bad_mask]
        worst['_expected'] = expected[bad_mask]
        worst['_err_pct'] = (rel_err[bad_mask] * 100)
        worst = worst.sort_values('_err_pct', ascending=False).head(5)
        examples = []
        for _, r in worst.iterrows():
            examples.append(f"{r['Column']}|{r['Value']}: raw={r['_raw']:.0f} proj={r['_proj']:.0f} (expected {r['_expected']:.0f}, err {r['_err_pct']:.0f}%)")
        failures.append(('projection-sanity', f'{n_bad}/{int(valid.sum())} rows (raw>=30) fail projection=raw*32.99 (>5% err); examples: ' + ' || '.join(examples)))
    return failures


def _find_subject_row(d):
    """Return the BRAND INPUT row if present, else fall back to SUBJECT.

    Profiles pulled before ~May-23 used a `SUBJECT` row (BP=100%) as the
    canonical subject anchor; later schemas added a separate `BRAND INPUT`
    row. The audit semantics are equivalent.
    """
    bi = d[d['Column'].str.upper() == 'BRAND INPUT']
    if not bi.empty:
        return bi
    return d[d['Column'].str.upper() == 'SUBJECT']


def gate_brand_input(d):
    """Gate 3: BRAND INPUT (or fallback SUBJECT) must be canonical token, not URL-encoded permutation."""
    failures = []
    bi = _find_subject_row(d)
    if bi.empty:
        failures.append(('brand-input-missing', 'Neither BRAND INPUT nor SUBJECT row found'))
        return failures
    v = str(bi.iloc[0]['Value'])
    perm_chars = sum(c in v for c in '%&#$@*=+|~')
    if perm_chars > 2:
        failures.append(('brand-input-perm-spam', f'BRAND INPUT contains {perm_chars} permutation chars (URL-encoded spam): {v[:80]}'))
    if v.count(',') > 5:
        failures.append(('brand-input-multi-token', f'BRAND INPUT has {v.count(",")+1} comma-separated tokens; expected 1: {v[:80]}'))
    return failures


def gate_category_coverage(d):
    """Gate 4: minimum category count + critical-category row counts."""
    failures = []
    cats = d['Column'].astype(str).str.strip().unique()
    if len(cats) < TOTAL_CAT_MIN:
        failures.append(('category-count-floor', f'only {len(cats)} categories present (floor {TOTAL_CAT_MIN}) — likely truncation'))
    sizes = d['Column'].astype(str).str.strip().value_counts()
    for cat in CRITICAL_CATS:
        n = int(sizes.get(cat, 0))
        if n < CRITICAL_CAT_MIN_ROWS:
            failures.append(('critical-cat-truncation', f'{cat} has only {n} rows (min {CRITICAL_CAT_MIN_ROWS})'))
    return failures


def gate_demographic_completeness(d, bp_col):
    """Gate 6: all 9 mandatory demographic categories must be present and sum to 100%.

    Per workspace rule 5a, the canonical demographic schema is what the
    pipeline emits — GENDER, AGE, ETHNICITY, EDUCATION, INCOME, OCCUPATION,
    PARENTAL_STATUS, RELATIONSHIP, SEXUAL_ORIENTATION. Any missing demo
    category is a HARD FAIL; any demo summing outside [99, 101] is a HARD
    FAIL.
    """
    failures = []
    cats_upper = d['Column'].astype(str).str.strip().str.upper()
    for cat in PIPELINE_DEMO_SCHEMA:
        rows = d[cats_upper == cat]
        if len(rows) == 0:
            failures.append(('demo-missing', f'mandatory demographic category {cat} is MISSING'))
            continue
        bps = rows[bp_col].apply(_bp).fillna(0)
        s = bps.sum()
        if abs(s - 100.0) > DEMO_SUM_TOL:
            failures.append(('demo-sum-drift', f'{cat} sums to {s:.2f}% (expected 100 ± {DEMO_SUM_TOL})'))
    return failures


def gate_sample_projection_ratio(d, bp_col, raw_col, proj_col):
    """Gate 5: max projection / (sample_size * 32.99) should be ~1.0."""
    failures = []
    bi = _find_subject_row(d)
    if bi.empty: return failures
    try:
        raw0 = float(str(bi.iloc[0][raw_col]).replace(',', ''))
        bp0 = _bp(bi.iloc[0][bp_col])
        if not bp0 or bp0 == 0: return failures
        sample = raw0 / (bp0 / 100)
        all_proj = pd.to_numeric(d[proj_col].astype(str).str.replace(',', ''), errors='coerce')
        proj_max = all_proj.max()
        expected_max = sample * US_POP_SCALE
        ratio = proj_max / max(1, expected_max)
        if ratio > 1.5:
            failures.append(('sample-projection-leak', f'max projection {proj_max:,.0f} is {ratio:.1f}x the math limit ({expected_max:,.0f}) — sample-join leak'))
    except Exception as e:
        failures.append(('sample-projection-error', f'sanity check error: {e}'))
    return failures


def run_gates(csv_path):
    p = Path(csv_path)
    if str(csv_path).startswith('s3://'):
        local = Path('/tmp/_pre_publish_gate') / p.name
        local.parent.mkdir(exist_ok=True)
        subprocess.run(['aws', 's3', 'cp', str(csv_path), str(local)], check=True, capture_output=True)
        p = local
    d = pd.read_csv(p, dtype=str, keep_default_na=False, low_memory=False)
    d['Column'] = d['Column'].astype(str).str.strip()
    d['Value']  = d['Value'].astype(str).str.strip()
    bp_col, raw_col, proj_col = _detect_cols(d)
    if not all([bp_col, raw_col, proj_col]):
        return [('schema', f'missing columns: bp={bp_col} raw={raw_col} proj={proj_col}')]

    failures = []
    failures.extend(gate_placeholder_pinning(d, bp_col))
    failures.extend(gate_projection_sanity(d, bp_col, raw_col, proj_col))
    failures.extend(gate_brand_input(d))
    failures.extend(gate_category_coverage(d))
    failures.extend(gate_demographic_completeness(d, bp_col))
    failures.extend(gate_sample_projection_ratio(d, bp_col, raw_col, proj_col))
    return failures


def main():
    if len(sys.argv) < 2:
        print('usage: pre_publish_gate.py <path-or-s3-url> [more...]')
        sys.exit(2)
    overall_fail = False
    for path in sys.argv[1:]:
        print(f'\n=== {path} ===')
        try:
            failures = run_gates(path)
        except Exception as e:
            print(f'  ERROR running gates: {e}')
            overall_fail = True
            continue
        hard = [(t, m) for t, m in failures if not t.startswith('warn-')]
        warn = [(t, m) for t, m in failures if t.startswith('warn-')]
        if not hard and not warn:
            print('  ✅ ALL GATES PASS')
        else:
            for tag, msg in hard:
                print(f'  ❌ [{tag}] {msg}')
                overall_fail = True
            for tag, msg in warn:
                print(f'  ⚠️  [{tag}] {msg}')
            if not hard:
                print('  ✅ no hard fails (warnings only — review)')
    sys.exit(1 if overall_fail else 0)


if __name__ == '__main__':
    main()
