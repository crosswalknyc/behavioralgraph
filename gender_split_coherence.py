"""Gender-split subset coherence enforcer.

Per Jenna's data model: the OG / broad file IS the casual file (every
fan = casual; avid is a subset of casual). When you split a file by
gender, the F + M cuts together cover ~all of that file's audience
(F + M >= 0.90 with at most a small 'other gender' sliver).

The per-brand math constraint is therefore:

    p_F * F[brand] + p_M * M[brand]   ~=  parent[brand]
    --------------------------------------------------
              p_F + p_M

where parent is the file the cuts were sourced FROM:

  - avid_F + avid_M sourced from avid file -> parent = avid file
  - casual_F + casual_M sourced from OG     -> parent = OG file

If Claude reasoned each gender independently it can violate this
constraint -- e.g. for Reba avid FORD: source=44.55, avid_F=52.34,
avid_M=58.23, weighted_avg=53.90 (impossible: both higher than parent
with no third gender to compensate).

This module enforces coherence by REBALANCING: keep Claude's gendered
tilt (F-M gap), but slide the level so the weighted average matches
parent. No multiplier is applied -- it's pure subset-arithmetic
correction.

Per Jenna 2026-06-12: "this would apply to the gender splits for
casual as well since it's the gender split from the large file. the
casual should include the avid since it's a subset. so don't over
complicate it. the casual fan female would just be the sample size of
the female bp from the og."
"""
from __future__ import annotations
import hashlib
from typing import Optional, Tuple
import pandas as pd


CAT_COL = "Column"
VAL_COL = "Value"
BP_COL = "Brand Penetration (Row)"
RAW_COL = "Original Raw Numbers"
PROJ_COL = "US Gen Pop Projection"

DEMO_SKIP = {
    "GENDER", "AGE", "ETHNICITY", "EDUCATION", "INCOME",
    "OCCUPATION", "PARENTAL_STATUS", "PARENTAL STATUS",
    "RELATIONSHIP", "SEXUAL_ORIENTATION", "SEXUAL ORIENTATION",
    "AVID FAN", "CASUAL FAN", "BRAND INPUT", "SUBJECT",
}


def _bp(x) -> Optional[float]:
    if pd.isna(x):
        return None
    s = str(x).replace("%", "").strip()
    try:
        return float(s)
    except Exception:
        return None


def _seed_jitter(seed: str, span: float = 0.04) -> float:
    h = hashlib.sha256(seed.encode()).hexdigest()
    n = int(h[:16], 16) / 2**64
    return (n * 2 - 1) * span


def _gender_shares(df_source: pd.DataFrame) -> Tuple[float, float]:
    """Returns (p_F, p_M) read from source's GENDER row."""
    gen = df_source[df_source[CAT_COL].astype(str).str.upper() == "GENDER"]
    p_F = p_M = 0.0
    for _, r in gen.iterrows():
        v = str(r[VAL_COL]).strip().upper()
        b = _bp(r[BP_COL])
        if b is None:
            continue
        if v == "FEMALE":
            p_F = b / 100.0
        elif v == "MALE":
            p_M = b / 100.0
    return p_F, p_M


def _index_brand_bps(df: pd.DataFrame) -> dict:
    """Returns {(cat_upper, val_upper): bp_float} for non-demo brand rows."""
    out = {}
    for _, r in df.iterrows():
        cat = str(r[CAT_COL]).strip().upper()
        if cat in DEMO_SKIP:
            continue
        val = str(r[VAL_COL]).strip().upper()
        b = _bp(r[BP_COL])
        if b is None:
            continue
        out[(cat, val)] = b
    return out


def enforce_gender_split_coherence(
    df_source: pd.DataFrame,
    df_F: pd.DataFrame,
    df_M: pd.DataFrame,
    subject: str,
    *,
    tolerance_pp: float = 2.0,
    min_coverage: float = 0.90,
) -> Tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Returns (df_F_corrected, df_M_corrected, stats).

    For every brand row present in BOTH F and M cuts AND in source,
    checks whether (p_F * F + p_M * M) / (p_F + p_M) is within
    `tolerance_pp` of source. If not, rebalances by preserving the
    F-M gap and shifting the level to match source.

    Subject self-pin rows (BP=100 in subject pin categories) are
    skipped -- they should already be 100/100/100.
    """
    p_F, p_M = _gender_shares(df_source)
    s = p_F + p_M
    coverage_ok = s >= min_coverage

    stats = {
        "p_F": p_F,
        "p_M": p_M,
        "coverage": s,
        "coverage_ok": coverage_ok,
        "rows_checked": 0,
        "rows_flagged": 0,
        "rows_rebalanced": 0,
        "max_violation_pp": 0.0,
        "examples": [],
    }
    if not coverage_ok:
        stats["note"] = (
            f"coverage {s:.3f} < min_coverage {min_coverage}; not enforcing"
        )
        return df_F, df_M, stats

    src_idx = _index_brand_bps(df_source)
    f_idx = _index_brand_bps(df_F)
    m_idx = _index_brand_bps(df_M)
    common = set(src_idx) & set(f_idx) & set(m_idx)

    # Pull sample sizes for downstream raw/proj recompute
    def _sample_proj(df):
        bi = df[df[CAT_COL].astype(str).str.upper() == "BRAND INPUT"]
        if not len(bi):
            return None, None
        return float(bi.iloc[0][RAW_COL]), float(bi.iloc[0][PROJ_COL])

    f_sample, f_proj = _sample_proj(df_F)
    m_sample, m_proj = _sample_proj(df_M)

    df_F = df_F.copy()
    df_M = df_M.copy()
    f_lookup = {(str(r[CAT_COL]).upper().strip(), str(r[VAL_COL]).upper().strip()): idx
                for idx, r in df_F.iterrows()}
    m_lookup = {(str(r[CAT_COL]).upper().strip(), str(r[VAL_COL]).upper().strip()): idx
                for idx, r in df_M.iterrows()}

    subj_u = subject.strip().upper()

    for key in common:
        cat, val = key
        src_bp = src_idx[key]
        f_bp = f_idx[key]
        m_bp = m_idx[key]

        # Skip subject self-pin
        if val == subj_u and abs(f_bp - 100.0) < 0.01 and abs(m_bp - 100.0) < 0.01:
            continue
        # Skip exact 100 self-pins (should be subject row)
        if abs(src_bp - 100.0) < 0.01:
            continue

        wavg = (p_F * f_bp + p_M * m_bp) / s
        viol = wavg - src_bp
        stats["rows_checked"] += 1
        if abs(viol) > stats["max_violation_pp"]:
            stats["max_violation_pp"] = abs(viol)

        if abs(viol) <= tolerance_pp:
            continue

        # Rebalance: keep gap, shift level to match parent.
        gap = m_bp - f_bp  # signed: + if male higher
        # new_F = parent - (p_M / s) * gap; new_M = parent + (p_F / s) * gap
        new_f = src_bp - (p_M / s) * gap
        new_m = src_bp + (p_F / s) * gap

        # Clamp + readjust to preserve weighted-avg if either falls out of range
        if new_f < 0.0001 or new_f > 99.49 or new_m < 0.0001 or new_m > 99.49:
            new_f = max(0.0001, min(99.49, new_f))
            new_m = max(0.0001, min(99.49, new_m))
            # Re-derive partner so weighted avg = parent exactly
            # parent = (p_F * new_f + p_M * new_m) / s
            # If new_f was clamped, recompute new_m; else recompute new_f
            if new_f == 0.0001 or new_f == 99.49:
                new_m = max(0.0001, min(99.49, (src_bp * s - p_F * new_f) / p_M))
            else:
                new_f = max(0.0001, min(99.49, (src_bp * s - p_M * new_m) / p_F))

        # Subject-salted micro-jitter so rebalanced values don't 4dp-collide
        # with each other (rare but possible) or with source.
        new_f = round(new_f + _seed_jitter(f"{subject}|{cat}|{val}|F-rebalance", 0.02), 4)
        new_m = round(new_m + _seed_jitter(f"{subject}|{cat}|{val}|M-rebalance", 0.02), 4)
        new_f = max(0.0001, min(99.49, new_f))
        new_m = max(0.0001, min(99.49, new_m))

        # Write back to df_F and df_M
        idx_f = f_lookup.get(key)
        idx_m = m_lookup.get(key)
        if idx_f is None or idx_m is None:
            continue
        df_F.at[idx_f, BP_COL] = f"{new_f:.4f}%"
        df_M.at[idx_m, BP_COL] = f"{new_m:.4f}%"
        if f_sample is not None:
            df_F.at[idx_f, RAW_COL] = float(round(f_sample * new_f / 100.0))
            df_F.at[idx_f, PROJ_COL] = float(round(f_proj * new_f / 100.0))
        if m_sample is not None:
            df_M.at[idx_m, RAW_COL] = float(round(m_sample * new_m / 100.0))
            df_M.at[idx_m, PROJ_COL] = float(round(m_proj * new_m / 100.0))
        stats["rows_rebalanced"] += 1
        if len(stats["examples"]) < 8:
            stats["examples"].append({
                "cat": cat, "val": val,
                "src": round(src_bp, 4),
                "F_was": round(f_bp, 4), "F_now": new_f,
                "M_was": round(m_bp, 4), "M_now": new_m,
                "weighted_was": round(wavg, 4),
                "violation_pp": round(viol, 4),
            })

    stats["rows_flagged"] = stats["rows_rebalanced"]
    return df_F, df_M, stats
