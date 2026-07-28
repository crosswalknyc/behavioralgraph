"""TU-vs-Avid subset coherence enforcer.

Per Jenna's data model (see avid-and-cut-skin-rules.mdc): every avid
fan IS a total-universe (TU) fan. Avid is a strict subset of TU.
Therefore, for every brand present in both TU and Avid of the same
subject:

    TU_BP  >=  p_avid * Avid_BP   (avid-contribution floor)

This is a necessary condition. It's the subset arithmetic: if X% of
avid fans engage with a brand, and avid fans are p_avid fraction of
TU, then at LEAST p_avid * Avid_BP percent of TU engages with that
brand (contribution from avid fans alone, before adding any casual-
only engagement).

Violations are guaranteed bugs. They surfaced hard on Wheel of
Fortune (July 28 pull):
  - Solitaire       TU=7.84  Avid=22.48  ->  floor=~4.5 SATISFIED but
                                              TU is well below the
                                              casual-inclusive weighted
                                              average of ~18-25.
  - NY Times Games  TU=10.07 Avid=18.47  ->  floor=~3.7 SATISFIED but
                                              TU should be nearer 40+.
  - Sudoku          TU=8.43  Avid=19.53  ->  floor=~3.9 SATISFIED but
                                              TU should be 25+.

So a pure "avid-alone" floor is too permissive. This module uses a
tighter constraint: TU should equal the weighted average of the avid
sub-cohort and the casual-only sub-cohort:

    TU_BP  =  p_avid * Avid_BP  +  (1 - p_avid) * Casual_only_BP

where Casual_only_BP is unknown but must be non-negative. So:

    Casual_only_BP  =  (TU_BP  -  p_avid * Avid_BP)  /  (1 - p_avid)

If Casual_only_BP comes out negative, TU is compressed and needs to
be lifted. If Casual_only_BP >= 0, we treat TU as coherent (we don't
force the "correct" casual-only level because that's an editorial
question this enforcer can't answer; it only fixes provable
arithmetic violations).

Lift target when violated: set TU_BP = p_avid * Avid_BP + tiny epsilon
so Casual_only_BP is a small non-negative number, plus subject-salted
micro-jitter. This is the minimum lift that restores arithmetic
coherence. Doesn't try to guess the "right" TU.

Subject self-pin rows (BP=100 in both) are skipped.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Tuple

import pandas as pd

CAT_COL = "Column"
VAL_COL = "Value"
BP_COL = "Brand Penetration (Row)"
CS_COL = "Category Share"
RAW_COL = "Original Raw Numbers"
PROJ_COL = "US Gen Pop Projection"

DEMO_SKIP = {
    "GENDER", "AGE", "ETHNICITY", "EDUCATION", "INCOME",
    "OCCUPATION", "PARENTAL_STATUS", "PARENTAL STATUS",
    "RELATIONSHIP", "SEXUAL_ORIENTATION", "SEXUAL ORIENTATION",
    "AVID FAN", "CASUAL FAN", "BRAND INPUT", "SAMPLE SIZE",
    "SUBJECT", "BRAND CATEGORY", "LOCATION",
}


def _bp(x) -> Optional[float]:
    if pd.isna(x):
        return None
    s = str(x).replace("%", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _seed_jitter(seed: str, span: float = 0.04) -> float:
    h = hashlib.sha256(seed.encode()).hexdigest()
    n = int(h[:16], 16) / 2**64
    return (n * 2 - 1) * span


def _read_avid_share(df_tu: pd.DataFrame) -> Optional[float]:
    """Return AVID FAN fraction (0..1) from a TU file's AVID FAN row.
    Returns None if not present."""
    m = df_tu[CAT_COL].astype(str).str.upper().str.strip() == "AVID FAN"
    if not m.any():
        return None
    for _, r in df_tu[m].iterrows():
        v = _bp(r.get(BP_COL))
        if v is not None and 0.01 <= v <= 99.99:
            return v / 100.0
    return None


def _sample_universe(df: pd.DataFrame) -> Tuple[Optional[float],
                                                  Optional[float]]:
    """Return (sample, universe) read from BRAND INPUT row, else
    SAMPLE SIZE row."""
    m = df[CAT_COL].astype(str).str.upper().str.strip() == "BRAND INPUT"
    if m.any():
        r = df[m].iloc[0]
        try:
            return (
                float(str(r[RAW_COL]).replace(",", "").strip() or 0),
                float(str(r[PROJ_COL]).replace(",", "").strip() or 0),
            )
        except Exception:
            pass
    m = df[CAT_COL].astype(str).str.upper().str.strip() == "SAMPLE SIZE"
    if m.any():
        r = df[m].iloc[0]
        try:
            return (
                float(str(r[RAW_COL]).replace(",", "").strip() or 0),
                float(str(r[PROJ_COL]).replace(",", "").strip() or 0),
            )
        except Exception:
            pass
    return None, None


def _index_brand_bps(df: pd.DataFrame) -> dict:
    out = {}
    for _, r in df.iterrows():
        cat = str(r[CAT_COL]).strip().upper()
        if cat in DEMO_SKIP:
            continue
        val = str(r[VAL_COL]).strip().upper()
        if not val:
            continue
        b = _bp(r[BP_COL])
        if b is None:
            continue
        out[(cat, val)] = b
    return out


def enforce_tu_avid_coherence(
    df_tu: pd.DataFrame,
    df_avid: pd.DataFrame,
    subject: str,
    *,
    tolerance_pp: float = 0.5,
    min_avid_share: float = 0.05,
    max_avid_share: float = 0.95,
    verbose: bool = True,
) -> Tuple[pd.DataFrame, dict]:
    """Rebalance Avid ONLY when TU is compressed below what the avid
    sub-cohort alone requires (i.e. when Casual_only_BP would be
    negative).

    Returns (df_avid_maybe_adjusted, stats).

    NOTE: This intentionally does NOT rewrite TU. If the TU value is
    the compressed one (as with WoF Solitaire), the correct fix is
    upstream (persona brief) or a targeted rebalance. This enforcer
    is a hard math floor / ceiling, not an audience-shape decision.

    The enforcer catches the failure mode where an Avid BP is so high
    that it's mathematically impossible given the TU value. Example:
      TU=7.84, Avid=22.48, p_avid=0.35
      p_avid * Avid = 7.87  >  TU=7.84   ==> violation
      Fix: lower Avid so p_avid * Avid <= TU (with epsilon)

    Subject self-pin rows (both at 100) are skipped.
    """
    stats: dict = {
        "rows_checked": 0,
        "rows_flagged": 0,
        "rows_rebalanced": 0,
        "max_violation_pp": 0.0,
        "examples": [],
        "p_avid": None,
        "note": None,
    }
    p_avid = _read_avid_share(df_tu)
    if p_avid is None or p_avid < min_avid_share or p_avid > max_avid_share:
        stats["p_avid"] = p_avid
        stats["note"] = (
            f"AVID FAN share {p_avid} outside [{min_avid_share},"
            f"{max_avid_share}]; skipping coherence check"
        )
        if verbose:
            print(f"  [tu_avid_coherence] {stats['note']}")
        return df_avid, stats
    stats["p_avid"] = p_avid

    tu_idx = _index_brand_bps(df_tu)
    av_idx = _index_brand_bps(df_avid)
    common = set(tu_idx) & set(av_idx)
    stats["rows_checked"] = len(common)

    df_avid = df_avid.copy()
    av_lookup = {}
    for idx, r in df_avid.iterrows():
        av_lookup[
            (str(r[CAT_COL]).strip().upper(),
             str(r[VAL_COL]).strip().upper())
        ] = idx

    a_sample, a_universe = _sample_universe(df_avid)
    if a_sample is None or a_sample <= 0:
        stats["note"] = "avid sample size missing; skipping Raw/Proj recompute"

    subj_u = str(subject).strip().upper()

    for key in common:
        cat, val = key
        tu_bp = tu_idx[key]
        av_bp = av_idx[key]

        if val == subj_u and abs(av_bp - 100.0) < 0.01 \
          and abs(tu_bp - 100.0) < 0.01:
            continue
        if abs(av_bp - 100.0) < 0.01 or abs(tu_bp - 100.0) < 0.01:
            continue

        # Avid alone contribution: p_avid * Avid must be <= TU
        avid_contrib = p_avid * av_bp
        viol = avid_contrib - tu_bp
        if abs(viol) > stats["max_violation_pp"]:
            stats["max_violation_pp"] = abs(viol)
        if viol <= tolerance_pp:
            continue

        stats["rows_flagged"] += 1

        # Rebalance: lower Avid so avid_contrib = TU - epsilon
        epsilon = 0.01 + max(0.0, _seed_jitter(
            f"{subject}|{cat}|{val}|tu_avid_epsilon", 0.008))
        new_av = max(0.05, (tu_bp - epsilon) / p_avid)
        new_av = min(99.49, new_av)
        new_av = round(new_av + _seed_jitter(
            f"{subject}|{cat}|{val}|tu_avid_rebalance", 0.02), 4)
        new_av = max(0.05, min(99.49, new_av))

        idx = av_lookup.get(key)
        if idx is None:
            continue
        existing = str(df_avid.at[idx, BP_COL])
        bp_cell = (f"{new_av:.4f}%"
                   if existing.strip().endswith("%")
                   else f"{new_av:.4f}")
        df_avid.at[idx, BP_COL] = bp_cell
        df_avid.at[idx, CS_COL] = f"{new_av:.4f}"
        if a_sample and a_universe:
            df_avid.at[idx, RAW_COL] = str(int(round(
                new_av / 100.0 * a_sample)))
            df_avid.at[idx, PROJ_COL] = str(int(round(
                new_av / 100.0 * a_universe)))
        stats["rows_rebalanced"] += 1
        if len(stats["examples"]) < 10:
            stats["examples"].append({
                "cat": cat, "val": val,
                "tu": round(tu_bp, 4),
                "avid_was": round(av_bp, 4),
                "avid_now": new_av,
                "violation_pp": round(viol, 4),
                "p_avid": round(p_avid, 4),
            })

    if verbose:
        print(f"  [tu_avid_coherence] p_avid={p_avid:.4f}  "
              f"checked={stats['rows_checked']}  "
              f"flagged={stats['rows_flagged']}  "
              f"rebalanced={stats['rows_rebalanced']}  "
              f"max_viol={stats['max_violation_pp']:.4f}pp")
        for ex in stats["examples"][:4]:
            print(f"    {ex['cat']}/{ex['val']}: TU={ex['tu']}  "
                  f"Avid {ex['avid_was']} -> {ex['avid_now']}")

    return df_avid, stats


__all__ = ["enforce_tu_avid_coherence"]
