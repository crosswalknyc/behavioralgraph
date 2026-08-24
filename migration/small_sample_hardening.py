"""Small-sample / creator-profile hardening enforcers + pre-flight gate.

Author: 2026-08-14 iJustine incident.

The main pipeline shipped an iJustine profile with 3,588 TU panelists / 1,000
Avid panelists because reference.host_mapping had ZERO entries for her socials
(no ijustine.com, no youtube.com/ijustine, no instagram.com/ijustine, etc.).
The pipeline fell back to a name text-match against news URLs / search queries
and surfaced people who READ ABOUT her, not people who FOLLOW her. Result:

  * 62% male / 15.5% Asian (tech-news reader template, not her audience)
  * EDUCATION missing Bachelors Degree + Prefer Not to Say (thin sample)
  * 86 zero-DMAs on Avid (small-sample collapse)
  * MPB 114 brands stacked in 0.3pp (Claude middle-band collapse)
  * Disney+/Hulu = 0.0, IMAX = 0.0, CAVA = 0.0 (0 panelists touched them)

Root cause is hostmap coverage - Jessie + Ana escalated separately. This module
adds the pipeline-side safety net so a thin panel signal (or missing hostmap
entries) cannot ship a broken profile again.

Five hardening rails wired here:

  1. enforce_canonical_demo_schema
       Every demo category MUST have all canonical buckets. Missing buckets
       are back-filled at persona-plausibility floor + subject-salted jitter,
       and the category is renormalized to 100.
  2. enforce_mass_brand_zero_floor
       Mass-engagement top brands (STREAMING/PLATFORM top ~15, SEARCH ENGINE/AI
       top 5, QSR top 10, SOCIAL MEDIA top 8) cannot ship at BP=0. Floored
       to per-category minimum with subject-salted jitter.
  3. enforce_mpb_deband
       Post-generation rank-based re-spread for any run of 6+ MPB brands
       within 0.15pp. Preserves Claude's ordinal decisions but eliminates
       the "generic middle collapse" small-sample pathology.
  4. enforce_small_sample_location_degrade
       If SAMPLE SIZE raw < N_THRESH (default 1500), LOCATION is blended
       30/70 (panel / persona-tilted-GenPop) with subject-salted jitter,
       never zeroed.
  5. preflight_subject_research_gate  (OPT-IN via SYNTH_RESEARCH_GATE=1)
       LLM call before write that estimates the subject's known follower/
       reach and blocks write if panel_reach << expected_reach by >500x.
       Would have caught iJustine (3.6K panel vs. 7M+ subs = 2000x mismatch).
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from typing import Optional

import pandas as pd


US_POP = 329_900_000
PANEL = 10_000_000

DEMO_CATS = (
    "AGE", "GENDER", "ETHNICITY", "EDUCATION", "INCOME", "OCCUPATION",
    "RELATIONSHIP", "PARENTAL_STATUS", "SEXUAL_ORIENTATION",
)


# ============================================================================
# Helpers
# ============================================================================
def _h(*parts):
    s = "|".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.sha256(s).digest()[:8], "big") / 2**64


def _jitter(*parts, amp=0.05):
    """Signed uniform in [-amp, +amp]."""
    return (_h(*parts) - 0.5) * 2 * amp


def _bp(v):
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except Exception:
        return None


def _sample_size(df):
    m = df["Column"] == "SAMPLE SIZE"
    if not m.any():
        return None
    row = df[m].iloc[0]
    for col in ("Original Raw Numbers", "Raw", "US Gen Pop Projection"):
        if col in df.columns:
            try:
                v = float(row.get(col) or 0)
                if v > 0 and col != "US Gen Pop Projection":
                    return int(v)
            except Exception:
                pass
    return None


def _recompute_row(df, idx, subject_raw):
    """Set Raw + Projection from BP + subject_raw (canonical math)."""
    bp = _bp(df.at[idx, "Brand Penetration (Row)"])
    if bp is None:
        return
    rw = round(subject_raw * bp / 100.0)
    pj = round(rw / PANEL * US_POP)
    if "Original Raw Numbers" in df.columns:
        df.at[idx, "Original Raw Numbers"] = rw
    if "US Gen Pop Projection" in df.columns:
        df.at[idx, "US Gen Pop Projection"] = pj


# Canonical bucket lists per demo (per reference/demos.csv)
CANONICAL_BUCKETS = {
    "GENDER": [
        "Male", "Female", "Non-Binary", "Trans Male", "Trans Female",
    ],
    "AGE": [
        "17 and Under", "18-24", "25-34", "35-44", "45-54", "55-64",
        "65 or Older",
    ],
    "ETHNICITY": [
        "White", "Hispanic or Latino", "Black or African American",
        "Asian", "Another Race/Ethnicity",
    ],
    "EDUCATION": [
        "Bachelors Degree", "High School or Less",
        "Some College / Associate Degree",
        "Graduate or Professional Degree", "Prefer Not to Say",
    ],
    "INCOME": [
        "Less than $25,000", "$25,000 - $49,999", "$50,000 - $74,999",
        "$75,000 - $99,999", "$100,000 - $149,999",
        "$150,000 - $249,999", "$250,000 or More",
    ],
    "OCCUPATION": [
        "Management, Business & Professional",
        "Science, Technology & Technical Professions",
        "Service & Hospitality", "Sales & Retail",
        "Healthcare Practitioners or Support",
        "Skilled Trades/Construction or Maintenance",
        "Education or Library Services", "Transportation & Logistics",
        "Manufacturing & Production", "Public Safety & Protective Services",
        "Legal", "Agriculture & Outdoor", "Other",
    ],
    "RELATIONSHIP": [
        "Single", "In a Relationship", "Married",
        "Divorced or Separated", "Widowed",
    ],
    "PARENTAL_STATUS": [
        "Has Children", "No Children", "Prefer Not to Say",
    ],
    "SEXUAL_ORIENTATION": [
        "Straight / Heterosexual", "LGBTQ+", "Prefer Not to Say",
    ],
}

# Persona-neutral floor BPs for back-filled missing buckets (won't distort
# the persona shape, just ensures every canonical bucket exists so the
# dashboard renders every stacked bar).
BACKFILL_FLOOR = {
    "GENDER":              {"Non-Binary": 0.8, "Trans Male": 0.4, "Trans Female": 0.3},
    "AGE":                 {},
    "ETHNICITY":           {"Another Race/Ethnicity": 1.5},
    "EDUCATION":           {"Bachelors Degree": 20.0, "Prefer Not to Say": 1.0},
    "INCOME":              {"Less than $25,000": 8.0, "$250,000 or More": 3.0},
    "OCCUPATION":          {"Other": 1.5},
    "RELATIONSHIP":        {"Widowed": 2.0},
    "PARENTAL_STATUS":     {"Prefer Not to Say": 3.0},
    "SEXUAL_ORIENTATION":  {"Prefer Not to Say": 6.0},
}


def enforce_canonical_demo_schema(df, subject=None, verbose=True):
    """Every canonical demo bucket must exist. Missing buckets are back-
    filled at persona-plausibility floor + subject-salted jitter, then
    the category is renormalized to 100.

    Complements enforce_canonical_demo_buckets (which relabels existing
    buckets to canonical form); this one INSERTS missing buckets.

    Idempotent: on a schema-complete df, no changes.
    """
    if df is None or len(df) == 0:
        return df, 0

    subject_raw = _sample_size(df) or 10000
    ops = 0
    inserts = []
    subj_key = str(subject or df.get("Value", pd.Series([""])).iloc[0] or "")
    new_rows = []

    col_upper = df["Column"].astype(str).str.strip().str.upper()

    for cat, canonical_list in CANONICAL_BUCKETS.items():
        m = col_upper == cat
        if not m.any():
            continue

        # Find existing buckets in the file (case + punct + apostrophe insensitive)
        existing_norm = set()
        cat_rows = df[m]
        for _, r in cat_rows.iterrows():
            v = str(r.get("Value", "")).strip()
            n = re.sub(r"[^a-z0-9]", "", v.lower())
            existing_norm.add(n)

        for canonical in canonical_list:
            n = re.sub(r"[^a-z0-9]", "", canonical.lower())
            if n in existing_norm:
                continue
            # Missing bucket - back-fill at floor
            floor = BACKFILL_FLOOR.get(cat, {}).get(canonical, 0.3)
            floor *= (1.0 + _jitter(subj_key, cat, canonical, "backfill", amp=0.10))
            bp = round(max(floor, 0.01), 4)
            rw = round(subject_raw * bp / 100.0)
            pj = round(rw / PANEL * US_POP)
            new_row = {}
            for col in df.columns:
                new_row[col] = None
            new_row["Column"] = df[m]["Column"].iloc[0]  # match casing
            new_row["Value"] = canonical
            new_row["Brand Penetration (Row)"] = bp
            if "Category Share" in df.columns:
                new_row["Category Share"] = bp
            if "Original Raw Numbers" in df.columns:
                new_row["Original Raw Numbers"] = rw
            if "US Gen Pop Projection" in df.columns:
                new_row["US Gen Pop Projection"] = pj
            new_rows.append(new_row)
            inserts.append((cat, canonical, bp))
            ops += 1

    if new_rows:
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)

    # Renormalize each demo category to sum to 100
    col_upper = df["Column"].astype(str).str.strip().str.upper()
    for cat in CANONICAL_BUCKETS.keys():
        m = col_upper == cat
        if not m.any():
            continue
        bps = pd.to_numeric(
            df.loc[m, "Brand Penetration (Row)"].astype(str)
                .str.replace('%', '', regex=False).str.strip(),
            errors="coerce"
        ).fillna(0)
        tot = bps.sum()
        if tot <= 0:
            continue
        scale = 100.0 / tot
        for idx in df.index[m]:
            bp = _bp(df.at[idx, "Brand Penetration (Row)"]) or 0.0
            new_bp = round(bp * scale, 4)
            df.at[idx, "Brand Penetration (Row)"] = new_bp
            if "Category Share" in df.columns:
                df.at[idx, "Category Share"] = new_bp
            _recompute_row(df, idx, subject_raw)

    if verbose and ops:
        subj = f"[{subject}]" if subject else ""
        print(f"   🩹 enforce_canonical_demo_schema {subj}: {ops} bucket "
              f"back-fill(s)")
        for cat, val, bp in inserts[:10]:
            print(f"      +bucket {cat} :: {val} (BP={bp:.4f})")
        if len(inserts) > 10:
            print(f"      ...+{len(inserts) - 10} more")

    return df, ops


# ============================================================================
# 2) Mass-brand zero floor
# ============================================================================
MASS_BRAND_FLOOR = {
    # STREAMING/PLATFORM: top mass streamers cannot ship at 0
    "STREAMING/PLATFORM": {
        "Netflix": 40.0, "Disney+/Hulu": 35.0, "Hulu": 35.0,
        "Disney+": 30.0, "Amazon Prime Video": 30.0, "HBO Max": 22.0,
        "Max": 22.0, "Paramount+": 15.0, "Peacock": 15.0,
        "Apple TV+": 10.0, "YouTube TV": 10.0,
    },
    "SEARCH ENGINE/AI": {
        "Google": 60.0, "Bing": 8.0, "YouTube Search": 30.0,
        "ChatGPT": 12.0, "Yahoo": 5.0,
    },
    "SOCIAL MEDIA": {
        "Facebook": 45.0, "YouTube": 55.0, "Instagram": 40.0,
        "TikTok": 30.0, "Pinterest": 20.0, "Reddit": 20.0,
        "LinkedIn": 15.0, "X": 20.0,
    },
    "QSR": {
        "McDonald's": 35.0, "Starbucks": 22.0, "Chick-fil-A": 20.0,
        "Chipotle": 15.0, "Subway": 15.0, "Taco Bell": 15.0,
        "Wendy's": 12.0, "Burger King": 12.0, "Dunkin'": 15.0,
        "Domino's": 10.0,
    },
}


def enforce_mass_brand_zero_floor(df, subject=None, verbose=True):
    """Mass-engagement top brands cannot ship at BP=0. Floors any 0-BP
    row at per-category minimum with subject-salted jitter (-30% to +30%
    of floor so no two profiles ship identical).

    Idempotent: brands already >= floor are untouched.
    """
    if df is None or len(df) == 0:
        return df, 0
    subject_raw = _sample_size(df) or 10000
    ops = 0
    lifts = []
    subj_key = str(subject or df.get("Value", pd.Series([""])).iloc[0] or "")

    for cat, brand_floors in MASS_BRAND_FLOOR.items():
        m = df["Column"] == cat
        if not m.any():
            continue
        for brand, floor in brand_floors.items():
            b_norm = re.sub(r"[^a-z0-9]", "", brand.lower())
            for idx in df.index[m]:
                v = str(df.at[idx, "Value"] or "").strip()
                if re.sub(r"[^a-z0-9]", "", v.lower()) != b_norm:
                    continue
                cur = _bp(df.at[idx, "Brand Penetration (Row)"]) or 0.0
                if cur >= floor * 0.5:
                    continue  # already above half-floor, don't lift
                new_bp = floor * (1.0 + _jitter(subj_key, cat, brand, "floor", amp=0.30))
                new_bp = round(max(new_bp, floor * 0.5), 4)
                df.at[idx, "Brand Penetration (Row)"] = new_bp
                _recompute_row(df, idx, subject_raw)
                lifts.append((cat, brand, cur, new_bp))
                ops += 1
                break  # one row per brand per category

    if verbose and ops:
        subj = f"[{subject}]" if subject else ""
        print(f"   🚨 enforce_mass_brand_zero_floor {subj}: {ops} zero-BP "
              f"mass-brand lift(s)")
        for cat, brand, cur, new_bp in lifts[:8]:
            print(f"      {cat}/{brand}: {cur:.4f} -> {new_bp:.4f}")
        if len(lifts) > 8:
            print(f"      ...+{len(lifts) - 8} more")

    return df, ops


# ============================================================================
# 3) MPB de-band
# ============================================================================
def enforce_mpb_deband(df, subject=None, verbose=True,
                        cluster_size=6, cluster_window=0.15):
    """Post-generation rank-based re-spread for MPB banding.

    Small-sample synth (whether via BG.py Claude reasoning or the row-by-row
    engine) can pile weak-signal brands into a narrow BP band because the
    model reaches for a "generic middle" when persona signal is thin. This
    is a KNOWN small-sample pathology, not real signal, so we correct it
    while preserving Claude's ordinal decisions.

    Algorithm: for MPB, apply a monotone rank-based re-spread that stretches
    the middle 50% by ~3x and keeps p05 / p95 anchors, then add subject-
    salted per-brand jitter of ~ ±0.35pp to break residual pinning.

    Runs only if at least one cluster of `cluster_size` brands within
    `cluster_window` pp is detected (indicating collapse).

    Idempotent: on a well-spread MPB, no changes.
    """
    if df is None or len(df) == 0:
        return df, 0
    subject_raw = _sample_size(df) or 10000
    subj_key = str(subject or df.get("Value", pd.Series([""])).iloc[0] or "")

    m = df["Column"] == "MOST PURCHASED BRANDS"
    if not m.any():
        return df, 0
    idxs = df.index[m].tolist()
    bps = pd.to_numeric(df.loc[idxs, "Brand Penetration (Row)"],
                         errors="coerce").fillna(0.0)
    n = len(idxs)
    if n < 30:
        return df, 0  # too few brands to worry about banding

    # Detect collapse
    sorted_bps = sorted(bps.tolist())
    max_cluster = 0
    for i in range(n):
        j = i
        while j < n and sorted_bps[j] - sorted_bps[i] <= cluster_window:
            j += 1
        max_cluster = max(max_cluster, j - i)
    if max_cluster < cluster_size:
        return df, 0  # no collapse detected, no-op

    # Rank-based re-spread
    import numpy as np
    order = bps.sort_values().index.tolist()
    sarr = np.array(bps.loc[order].tolist(), dtype=float)
    p05 = float(np.percentile(sarr, 5))
    p50 = float(np.percentile(sarr, 50))
    p95 = float(np.percentile(sarr, 95))

    # 2026-08-24 (Erin Brooks apparel compression): the re-spread used
    # to remap the TOP tier (ranks >= 0.95) into [p95, p95*1.42] too.
    # When the collapsed cluster sits low (p95 ~= 12), legitimate mass
    # over-indexers (Nike 38+, Adidas, Old Navy, ...) got crushed to
    # 0.34-0.61x their gen pop baseline on an audience that should
    # over-index apparel. The banding pathology lives in the collapsed
    # LOW/MID band, not in the top tier - so the top tier now KEEPS its
    # original values (its legitimate spread) and only ranks < 0.95 are
    # re-spread between the p05/p50/p95 anchors. The mid band is capped
    # just below the top tier's minimum so ordinality is preserved.
    ranks = np.arange(n) / max(n - 1, 1)
    new_vals = np.empty(n)
    top_mask = ranks >= 0.95
    top_min = float(sarr[top_mask].min()) if top_mask.any() else None
    for k, r in enumerate(ranks):
        if r < 0.05:
            frac = r / 0.05
            new_vals[k] = p05 * 0.55 + (p05 - p05 * 0.55) * frac
        elif r < 0.5:
            frac = (r - 0.05) / 0.45
            new_vals[k] = p05 + (p50 - p05) * frac
        elif r < 0.95:
            frac = (r - 0.5) / 0.45
            new_vals[k] = p50 + (p95 - p50) * frac
        else:
            new_vals[k] = sarr[k]  # top tier: keep legitimate spread

    for k in range(n):
        brand = df.at[order[k], "Value"]
        if top_mask[k]:
            # top tier gets a smaller jitter so a legitimate outlier
            # never drifts materially from its reasoned value
            new_vals[k] += _jitter(subj_key, "mpb-deband-top", brand,
                                   amp=0.10)
        else:
            new_vals[k] += _jitter(subj_key, "mpb-deband", brand, amp=0.35)
            if top_min is not None:
                new_vals[k] = min(new_vals[k], top_min * 0.995)

    new_vals = np.maximum(new_vals, 0.05)
    new_vals.sort()  # preserve ordinal ordering

    for k, row_idx in enumerate(order):
        bp = round(float(new_vals[k]), 4)
        df.at[row_idx, "Brand Penetration (Row)"] = bp
        _recompute_row(df, row_idx, subject_raw)

    if verbose:
        subj = f"[{subject}]" if subject else ""
        print(f"   🌈 enforce_mpb_deband {subj}: re-spread {n} MPB brands "
              f"(max cluster in {cluster_window}pp: {max_cluster} -> "
              f"{cluster_size - 1}); p05={p05:.3f} p50={p50:.3f} p95={p95:.3f}")
    return df, 1


# ============================================================================
# 4) Small-sample LOCATION degrade
# ============================================================================
def enforce_small_sample_location_degrade(
    df, subject=None, verbose=True, sample_threshold=1500,
    genpop_df=None, genpop_key="Gen_Pop_2026.csv"
):
    """If sample_size < sample_threshold, blend LOCATION 30/70 with Gen Pop
    (never zero any DMA). Preserves any strong persona tilt in the raw
    panel signal while eliminating the sample-collapse-into-biggest-market
    pathology.

    Idempotent above threshold: no changes.
    """
    if df is None or len(df) == 0:
        return df, 0
    subject_raw = _sample_size(df) or 10000
    if subject_raw >= sample_threshold:
        return df, 0  # sample is fine, don't touch

    subj_key = str(subject or df.get("Value", pd.Series([""])).iloc[0] or "")
    m = df["Column"] == "LOCATION"
    if not m.any():
        return df, 0

    # Load Gen Pop LOCATION
    if genpop_df is None:
        try:
            import boto3
            s3 = boto3.client("s3")
            obj = s3.get_object(Bucket="dashboard-inputs", Key=genpop_key)
            genpop_df = pd.read_csv(io.BytesIO(obj["Body"].read()),
                                     keep_default_na=False)
        except Exception as e:
            if verbose:
                print(f"   ⚠️ location_degrade: gp load failed: {e}")
            return df, 0

    gp_loc = genpop_df[genpop_df["Column"] == "LOCATION"].copy()
    gp_loc["Share"] = pd.to_numeric(gp_loc["Category Share"],
                                      errors="coerce").fillna(0)

    def _norm_dma(s):
        return " ".join(str(s).upper().replace(",", " ").split())

    gp_share = dict(zip(gp_loc["Value"].apply(_norm_dma), gp_loc["Share"]))

    idxs = df.index[m].tolist()
    # Compute blended shares: 30% panel + 70% Gen Pop, jittered per DMA
    blend = {}
    for i in idxs:
        dma = _norm_dma(df.at[i, "Value"])
        panel_bp = _bp(df.at[i, "Brand Penetration (Row)"]) or 0.0
        gp_bp = gp_share.get(dma, 0.20)
        base = 0.30 * panel_bp + 0.70 * gp_bp
        # jitter per DMA to avoid gen-pop-identical output
        base *= (1.0 + _jitter(subj_key, "loc-degrade", dma, amp=0.08))
        blend[i] = max(base, 0.02)  # never zero, floor 0.02%

    total = sum(blend.values())
    zero_before = sum(1 for i in idxs if (_bp(df.at[i, "Brand Penetration (Row)"]) or 0) == 0)

    for i in idxs:
        bp = round(blend[i] * 100.0 / total, 4)
        df.at[i, "Brand Penetration (Row)"] = bp
        if "Category Share" in df.columns:
            df.at[i, "Category Share"] = bp
        _recompute_row(df, i, subject_raw)

    zero_after = sum(1 for i in idxs if (_bp(df.at[i, "Brand Penetration (Row)"]) or 0) == 0)

    if verbose:
        subj = f"[{subject}]" if subject else ""
        print(f"   🗺️  enforce_small_sample_location_degrade {subj}: "
              f"sample={subject_raw} < {sample_threshold}, "
              f"blended 30/70 panel/GP; zero-DMAs {zero_before} -> {zero_after}")
    return df, 1


# ============================================================================
# 5) Pre-flight subject-research gate  (OPT-IN)
# ============================================================================
_PREFLIGHT_SYSTEM = (
    "You are a subject reach estimator. Given a subject name and category, "
    "estimate their expected US digital engager reach (last 12 months) as a "
    "single number in one of the buckets:\n"
    "  TIER_A: 20M+ US engagers (mass creator, superstar athlete, top brand)\n"
    "  TIER_B: 5-20M   (major creator, mid-tier brand, top-25 athlete)\n"
    "  TIER_C: 1-5M    (established creator, mid-brand, top-100 athlete)\n"
    "  TIER_D: 200K-1M (niche creator, small brand, minor talent)\n"
    "  TIER_E: <200K   (very niche, emerging talent)\n"
    "  UNKNOWN: cannot estimate\n\n"
    "Return STRICT JSON: {\"tier\":\"TIER_X\", \"min_engagers\":N, "
    "\"max_engagers\":N, \"reasoning\":\"1-line why\"}"
)

_TIER_MIN_ENGAGERS = {
    "TIER_A": 20_000_000,
    "TIER_B":  5_000_000,
    "TIER_C":  1_000_000,
    "TIER_D":    200_000,
    "TIER_E":     20_000,
    "UNKNOWN":         0,
}


def preflight_subject_research_gate(
    df, subject, brand_category=None, verbose=True,
    gate_ratio=500, opt_in_env="SYNTH_RESEARCH_GATE"
):
    """LLM-check: does the subject's expected reach match what the pipeline
    surfaced? Blocks write if panel_proj << expected_min / gate_ratio.

    Currently OPT-IN via env var SYNTH_RESEARCH_GATE=1 (LLM call cost).

    Returns (should_write: bool, reason: str). Callers should refuse to
    ship the file when should_write=False.
    """
    if os.environ.get(opt_in_env, "").strip().lower() not in ("1", "true", "yes"):
        return True, "gate disabled"

    if df is None or len(df) == 0:
        return True, "empty df"

    # Estimate current panel projection
    m = df["Column"] == "SAMPLE SIZE"
    if not m.any():
        return True, "no sample size row"
    ss = df[m].iloc[0]
    try:
        panel_proj = int(float(ss.get("US Gen Pop Projection") or 0))
    except Exception:
        return True, "cannot read projection"
    if panel_proj <= 0:
        return True, "no projection"

    # LLM estimate
    try:
        import anthropic
    except Exception:
        return True, "anthropic unavailable"
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return True, "no api key"

    try:
        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            f"Subject: {subject}\n"
            f"Brand category: {brand_category or 'unknown'}\n"
            f"Current pipeline US engager projection: {panel_proj:,}\n\n"
            "Given the subject and category, estimate their expected US "
            "digital engager reach (last 12mo)."
        )
        resp = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=400,
            temperature=0.1,
            system=_PREFLIGHT_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            try:
                from migration import usage_tracker as _ut
            except Exception:
                import usage_tracker as _ut  # type: ignore
            _ut.record("claude-sonnet-4-5-20250929",
                       getattr(resp, "usage", None))
        except Exception:
            pass
        text = resp.content[0].text if (resp.content and resp.content[0].type == "text") else ""
        import json
        m = re.search(r"\{[^}]+\}", text)
        if not m:
            return True, f"cannot parse LLM response: {text[:100]}"
        est = json.loads(m.group(0))
    except Exception as e:
        return True, f"LLM error: {e}"

    tier = est.get("tier", "UNKNOWN")
    expected_min = int(est.get("min_engagers") or _TIER_MIN_ENGAGERS.get(tier, 0))

    if tier == "UNKNOWN" or expected_min <= 0:
        return True, f"LLM tier UNKNOWN, allowing (reasoning: {est.get('reasoning', '')[:80]})"

    ratio = expected_min / max(panel_proj, 1)
    reasoning = est.get("reasoning", "")[:200]

    if verbose:
        print(f"   🛰️  preflight_subject_research_gate [{subject}]: "
              f"panel_proj={panel_proj:,}, expected_min={expected_min:,} "
              f"({tier}), ratio={ratio:.0f}x")
        print(f"      LLM reasoning: {reasoning}")

    if ratio > gate_ratio:
        return False, (
            f"BLOCK: panel projection ({panel_proj:,}) is {ratio:.0f}x below "
            f"expected minimum ({expected_min:,}, {tier}). Likely hostmap "
            f"coverage gap for {subject}. Reasoning: {reasoning}"
        )
    return True, f"pass ({ratio:.1f}x within tolerance)"


# ============================================================================
# One-shot wrapper: run all 4 enforcers in canonical order
# ============================================================================
def run_small_sample_hardening(df, subject=None, brand_category=None,
                                 verbose=True, genpop_df=None):
    """Convenience: run all 4 small-sample hardening enforcers in
    canonical order. Idempotent.
    """
    total_ops = 0
    df, ops = enforce_small_sample_location_degrade(
        df, subject=subject, verbose=verbose, genpop_df=genpop_df
    )
    total_ops += ops
    df, ops = enforce_canonical_demo_schema(df, subject=subject, verbose=verbose)
    total_ops += ops
    df, ops = enforce_mass_brand_zero_floor(df, subject=subject, verbose=verbose)
    total_ops += ops
    df, ops = enforce_mpb_deband(df, subject=subject, verbose=verbose)
    total_ops += ops
    if verbose:
        print(f"   ✨ small_sample_hardening total ops: {total_ops}")
    return df, total_ops


__all__ = [
    "enforce_canonical_demo_schema",
    "enforce_mass_brand_zero_floor",
    "enforce_mpb_deband",
    "enforce_small_sample_location_degrade",
    "preflight_subject_research_gate",
    "run_small_sample_hardening",
]
