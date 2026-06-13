"""Gender + intensity audience-cut synthesis (Reba 2026-06-12 directive).

Produces "skins" of an existing profile for narrower audience segments:

  * Casual Female / Casual Male -- broad-audience cuts of the OG profile,
    pinned to GENDER=100% F or M, with intensity-aware lower BPs (casual
    = light engagement).
  * Avid Female / Avid Male -- gender splits of an EXISTING Avid Fan
    profile (the avid synthesis already happened upstream; we just
    slice it by gender).

Per Jenna 2026-06-12 directive: "the gender split on avid would be
skins from the avid profile". So the avid cuts source the avid CSV,
not the OG; the casual cuts source the OG.

Methodology mirrors migration/avid_fan_row_by_row:
  Phase 1 -- Claude reasons about cohort_fraction (intensity- and
            gender-aware) + demographic targets.
  Phase 2 -- per-category Claude calls that lift / sink each row's BP
            for the (gender, intensity) cohort.
  Phase 3 -- apply the transform: GENDER pin to ~99.99 / ~0.01 (with
            jitter so we never sit on an exact boundary), demos
            renormed to 100, brand rows lifted/sunk per Phase 2.
  Phase 4 -- no-collision pass against the source df (so the cut never
            shares a 4dp BP with its source for any common row).

Public API:

    synthesize_audience_cut(source, *, gender, intensity,
                             source_kind='auto', dry_run=False,
                             register_in_dashboard=True,
                             source_s3_key=None) -> dict

Output filename convention (matches the existing skin family so the
backfill orchestrator's regex skips them as derived):

    "<Subject Name> - Casual Female Fan.csv"
    "<Subject Name> - Casual Male Fan.csv"
    "<Subject Name> - Avid Female Fan.csv"
    "<Subject Name> - Avid Male Fan.csv"
"""
from __future__ import annotations

import io
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Reuse helpers from the avid module -- same source loader, snapshot
# builder, jitter, collision pass, demo/subject/skip sets.
from avid_fan_row_by_row import (  # noqa: E402
    BUCKET, REGION,
    _load_source_df, _seed_jitter, _fbp,
    enforce_no_collisions,
    DEMO_CATS_TF, SUBJECT_PIN_CATS_TF,
    _detect_cols,
)
from super_fan_synthesis import (  # noqa: E402
    build_source_snapshot, _extract_json_block,
)

# =============================================================================
# Constants
# =============================================================================
GENDER_BUCKET_LABELS = {
    "F": ("FEMALE", {"MALE", "OTHER", "NON-BINARY", "NONBINARY"}),
    "M": ("MALE",   {"FEMALE", "OTHER", "NON-BINARY", "NONBINARY"}),
}

INTENSITY_DESCRIPTIONS = {
    "avid": (
        "deeply engaged superfans -- 4+ digital touchpoints/year, active "
        "community participants, repeat purchasers of subject-related "
        "merch, follow on multiple platforms, parasocial attachment"
    ),
    "casual": (
        "broad audience that recognizes / likes the subject but engages "
        "lightly -- 1-2 touchpoints/year, passive viewers, occasional "
        "watchers, not deeply invested, low parasocial pull"
    ),
}


def _label_for_cut(gender: str, intensity: str) -> str:
    g_word = "Female" if str(gender).upper() == "F" else "Male"
    i_word = "Casual" if str(intensity).lower() == "casual" else "Avid"
    return f"{i_word} {g_word} Fan"


def _detect_source_intensity(source: str, source_kind: str) -> Optional[str]:
    """Inspect the source filename to determine if it already represents
    an intensity cohort. Returns 'avid' | 'casual' | None.

    Files like "Reba McEntire - Avid Fan.csv" are themselves avid
    cohorts; gender splits of them should NOT re-apply an intensity
    filter -- they should just split by gender (per Jenna 2026-06-12:
    "the percentage of female in avid should be the sample size for
    avid female...").
    """
    name = (os.path.basename(source) if source_kind != "s3_key"
            else source).lower()
    if " - avid fan" in name or " - avid female fan" in name or \
       " - avid male fan" in name:
        return "avid"
    if " - casual fan" in name or " - casual female fan" in name or \
       " - casual male fan" in name:
        return "casual"
    return None


def _compute_deterministic_cohort_fraction(
    df_source, gender: str, intensity: str,
    source_intensity: Optional[str],
) -> Optional[float]:
    """Compute cohort_fraction directly from the source's GENDER row +
    (when applicable) the source's intensity (AVID FAN / CASUAL FAN)
    BPs. Returns the deterministic fraction or None if it can't be
    computed (caller falls back to Claude's reasoning).

    Math:
      * source already at target intensity (e.g. avid_F from an avid
        CSV): fraction = gender_share_in_source. Splits the avid
        cohort by gender, summing to 100%.
      * source is OG (or unspecified): fraction = gender_share *
        intensity_share, where intensity_share is the source's
        AVID FAN or CASUAL FAN BP.
    """
    cat_col = "Column"
    val_col = "Value"
    bp_col = next(
        (c for c in df_source.columns if "Brand Penetration" in c), None
    )
    if not bp_col:
        return None

    target_label = "FEMALE" if gender.upper() == "F" else "MALE"
    gender_pct: Optional[float] = None
    intensity_pct_for_target: Optional[float] = None

    cats_upper = df_source[cat_col].astype(str).str.upper().str.strip()
    vals_upper = df_source[val_col].astype(str).str.upper().str.strip()

    g_mask = (cats_upper == "GENDER") & (vals_upper == target_label)
    if g_mask.any():
        gender_pct = _fbp(df_source.loc[g_mask, bp_col].iloc[0])
    if gender_pct is None:
        return None

    if source_intensity == intensity:
        # Source IS the target-intensity cohort. Splitting by gender
        # only -- the gender share of the avid (or casual) cohort IS
        # the cohort_fraction.
        return max(0.005, min(0.995, gender_pct / 100.0))

    # Source is OG (or unspecified intensity). Fold the source's
    # AVID FAN or CASUAL FAN BP into the fraction.
    intensity_label = ("AVID FAN" if intensity == "avid"
                       else "CASUAL FAN")
    i_mask = cats_upper == intensity_label
    if i_mask.any():
        intensity_pct_for_target = _fbp(
            df_source.loc[i_mask, bp_col].iloc[0]
        )
    if intensity_pct_for_target is None:
        return max(0.005, min(0.995, gender_pct / 100.0))

    combined = (gender_pct / 100.0) * (intensity_pct_for_target / 100.0)
    return max(0.005, min(0.995, combined))


# =============================================================================
# Phase 1 -- gender + intensity audience reasoning
# =============================================================================
_AUDIENCE_SYSTEM = (
    "You are an audience analytics reasoning agent.\n\n"
    "You're given a public figure's engagement profile (a SOURCE audience). "
    "We want to produce a NARROWER cut of that audience defined by:\n"
    "  - gender_pin: 'F' or 'M'  (the cut is 100% one gender)\n"
    "  - intensity: 'avid' or 'casual'\n\n"
    "Reason about:\n"
    "1. cohort_fraction: what fraction of the SOURCE audience belongs in "
    "this (gender, intensity) cohort. Base rate is roughly:\n"
    "   gender_share × intensity_share. Adjust for known skews -- e.g. "
    "country / talk show audiences skew female; sports / wrestling skew "
    "male; intensity is heavier inside the dominant gender.\n\n"
    "2. us_pop_fraction: fraction of US adults in this cohort. Always "
    "smaller than cohort_fraction times the source's reach.\n\n"
    "3. audience_demo_targets: how each demographic distribution "
    "sharpens for this cohort. GENDER MUST be pinned to ~99.99 for the "
    "target gender and ~0.01 for the others (we'll jitter to avoid "
    "exact boundaries downstream). All other demos shift to reflect "
    "the (gender, intensity) overlap. Each category MUST sum to 100.0 "
    "(within 0.5pp).\n\n"
    "4. reasoning: 2-3 sentences explaining the cohort sizing + how the "
    "non-gender demos reshape (e.g. female casual country fans skew "
    "older + lower income vs the broad audience).\n\n"
    "Output STRICT JSON only, no commentary outside the JSON."
)


def _format_audience_user(snap: dict, gender: str, intensity: str,
                          source_label: str) -> str:
    g_word = "FEMALE" if gender.upper() == "F" else "MALE"
    L = []
    L.append(f"SUBJECT: {snap['subject']}")
    L.append(f"SOURCE PROFILE: {source_label}")
    L.append(f"SOURCE SAMPLE SIZE: {snap['sample_size']}")
    L.append(f"GENDER PIN: {gender.upper()} ({g_word})")
    L.append(f"INTENSITY: {intensity.lower()} -- "
             f"{INTENSITY_DESCRIPTIONS.get(intensity.lower(), '')}")
    if snap.get("avid_fan_bp") is not None:
        L.append(f"AVID FAN BP signal in source: {snap['avid_fan_bp']}%")
    if snap.get("casual_fan_bp") is not None:
        L.append(f"CASUAL FAN BP signal in source: {snap['casual_fan_bp']}%")
    L.append("")
    L.append("DEMOGRAPHIC DISTRIBUTION (source):")
    for cat, rows in snap.get("demos", {}).items():
        L.append(f"  {cat}:")
        for label, bp in rows:
            L.append(f"    {label}: {bp:.2f}%")
    L.append("")
    L.append("TOP-3 BRANDS PER NON-DEMO CATEGORY (signal for cut "
             "calibration):")
    items = list(snap.get("top_brands_per_category", {}).items())
    for cat, rows in items[:30]:
        top = rows[:3]
        s = "; ".join(f"{lbl}={bp:.1f}%" for lbl, bp in top)
        L.append(f"  {cat}: {s}")
    if len(items) > 30:
        L.append(f"  ... and {len(items) - 30} more categories")
    L.append("")
    L.append("Output JSON:")
    L.append(
        '{\n'
        '  "cohort_fraction": 0.30,\n'
        '  "us_pop_fraction": 0.06,\n'
        '  "reasoning": "...",\n'
        '  "audience_demo_targets": {\n'
        f'    "GENDER": {{"FEMALE": 99.99, "MALE": 0.01, ...}}'
        f'        // GENDER pinned to {g_word}\n'
        '    "AGE": {...}, "ETHNICITY": {...}, "INCOME": {...},\n'
        '    "EDUCATION": {...}, "OCCUPATION": {...},\n'
        '    "RELATIONSHIP": {...}, "PARENTAL_STATUS": {...},\n'
        '    "SEXUAL_ORIENTATION": {...}\n'
        '  }\n'
        '}'
    )
    L.append(f"GENDER MUST be pinned: {g_word}=99.99, others=0.01.")
    L.append("Other demos must each sum to 100.0 (within 0.5pp).")
    L.append("Use the EXACT bucket labels shown above.")
    return "\n".join(L)


def reason_audience_cut(snap: dict, gender: str, intensity: str,
                        source_label: str) -> dict:
    """Phase 1: cohort_fraction + demo_targets for this (gender, intensity)
    cut. Falls back to a 50/50 gender split + identity demos on Claude
    failure."""
    g_word = "FEMALE" if gender.upper() == "F" else "MALE"
    other_word = "MALE" if gender.upper() == "F" else "FEMALE"
    fallback = {
        "cohort_fraction": 0.30 if intensity.lower() == "casual" else 0.10,
        "us_pop_fraction": 0.04 if intensity.lower() == "casual" else 0.015,
        "audience_demo_targets": {
            cat: {label: bp for label, bp in rows}
            for cat, rows in snap.get("demos", {}).items()
        },
        "reasoning": "fallback: Claude unavailable, using source demos as-is",
        "claude_used": False,
    }
    # Even on fallback, pin GENDER properly
    fallback["audience_demo_targets"]["GENDER"] = {
        g_word: 99.99, other_word: 0.01,
    }
    try:
        from claude_client import claude_messages
    except Exception as e:
        print(f"[audience-cut] claude import failed: {e}")
        return fallback

    user = _format_audience_user(snap, gender, intensity, source_label)
    try:
        resp = claude_messages(
            system=_AUDIENCE_SYSTEM, user=user,
            max_tokens=4096, temperature=0.4,
        )
    except Exception as e:
        print(f"[audience-cut] phase 1 claude call failed: {e}")
        return fallback
    obj = _extract_json_block(resp) if resp else None
    if not isinstance(obj, dict):
        print(f"[audience-cut] phase 1 returned no JSON; fallback")
        return fallback

    out = dict(fallback)
    cf = obj.get("cohort_fraction")
    uf = obj.get("us_pop_fraction")
    if isinstance(cf, (int, float)) and 0.02 < cf < 0.95:
        out["cohort_fraction"] = float(cf)
    if isinstance(uf, (int, float)) and 0.001 < uf < 0.5:
        out["us_pop_fraction"] = float(uf)
    dt = obj.get("audience_demo_targets")
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
            out["audience_demo_targets"] = cleaned
    if isinstance(obj.get("reasoning"), str):
        out["reasoning"] = obj["reasoning"]

    # Hard-pin GENDER regardless of what Claude said: the cut is
    # definitionally single-gender.
    out["audience_demo_targets"]["GENDER"] = {
        g_word: 99.99, other_word: 0.01,
    }
    out["claude_used"] = True
    return out


# =============================================================================
# Phase 2 -- per-category Claude calls (gender + intensity aware)
# =============================================================================
_CAT_ROW_SYSTEM = (
    "You are a brand-affinity reasoning agent. Given:\n"
    "  - a public-figure subject\n"
    "  - a NARROW audience cut: a single gender, single intensity tier\n"
    "  - a category name\n"
    "  - a list of items in that category with the SOURCE audience BP\n"
    "Decide each item's BP for the cut.\n\n"
    "Rules (strict):\n"
    "  1. Each new_bp MUST differ from the source bp by at least 0.5pp.\n"
    "  2. Brands directly tied to the subject (their own works, "
    "businesses, close collaborators) should still skew up vs source for "
    "the avid cut, and slightly DOWN for the casual cut (casuals like "
    "but don't obsess).\n"
    "  3. Gender-coded brands shift sharply: items strongly coded for "
    "the cohort's gender lift; items strongly coded for the OTHER "
    "gender sink. E.g. for a female cut: cosmetics / Hallmark / QVC "
    "lift, NFL / power tools / Monster Energy sink. For a male cut: "
    "vice versa.\n"
    "  4. Intensity shifts magnitude: avid cuts have sharper highs and "
    "lows; casual cuts compress toward the broad-audience baseline.\n"
    "  5. BPs are percentages 0.0001 to 99.9. Round to 4 decimals.\n\n"
    "Return STRICT JSON only:\n"
    '{"items": [{"label": "...", "new_bp": 12.3456}, ...]}\n'
    "Include EVERY item from the input list. Use exact label spelling."
)


def _audience_summary_text(audience: dict, gender: str, intensity: str) -> str:
    g_word = "FEMALE" if gender.upper() == "F" else "MALE"
    L = [f"gender_pin: {gender.upper()} ({g_word})",
         f"intensity: {intensity.lower()} -- "
         f"{INTENSITY_DESCRIPTIONS.get(intensity.lower(), '')}",
         f"cohort_fraction (of source): "
         f"{audience.get('cohort_fraction', 0):.4f}",
         f"reasoning: {audience.get('reasoning', '')}",
         "",
         "Demo targets (this cut):"]
    for cat, buckets in audience.get("audience_demo_targets", {}).items():
        bs = ", ".join(f"{lbl}={v:.1f}" for lbl, v in
                       sorted(buckets.items(), key=lambda kv: -kv[1])[:5])
        L.append(f"  {cat}: {bs}")
    return "\n".join(L)


def _format_category_user(subject: str, audience_summary: str,
                          category: str, rows: list,
                          gender: str, intensity: str) -> str:
    g_word = "FEMALE" if gender.upper() == "F" else "MALE"
    L = [f"SUBJECT: {subject}",
         f"GENDER PIN: {gender.upper()} ({g_word})",
         f"INTENSITY: {intensity.lower()}",
         "AUDIENCE PROFILE:",
         audience_summary,
         "",
         f"CATEGORY: {category}",
         f"ITEMS ({len(rows)} rows, source_bp = current value in source profile):"]
    for label, bp in rows:
        L.append(f"  - {label} :: source_bp={bp:.4f}")
    L.append("")
    L.append('Return JSON: {"items":[{"label":"...","new_bp":<float>}, ...]}')
    L.append("Every item MUST appear in the response. Each new_bp MUST "
             "differ from source_bp by at least 0.5 percentage points.")
    return "\n".join(L)


def reason_category_rows_cut(subject: str, audience: dict, category: str,
                             rows: list, gender: str, intensity: str,
                             *, chunk_size: int = 200) -> dict:
    """Phase 2 Claude call -- reasons over EVERY row in the category (no
    top-N truncation, no priority gating). Long lists are split into
    sequential chunks of `chunk_size` rows so the agent gets the full
    list across calls; decisions from each chunk are merged.

    Per Jenna 2026-06-12: "no caps on anything anywhere for agents".
    """
    if not rows:
        return {}
    audience_summary = _audience_summary_text(audience, gender, intensity)

    try:
        from claude_client import claude_messages
    except Exception:
        return {}

    rows_sorted = sorted(rows, key=lambda kv: -kv[1])
    decisions: dict = {}
    n_chunks = (len(rows_sorted) + chunk_size - 1) // chunk_size
    for i in range(n_chunks):
        chunk = rows_sorted[i * chunk_size:(i + 1) * chunk_size]
        if n_chunks > 1:
            chunk_label = f"{category} (chunk {i + 1}/{n_chunks})"
        else:
            chunk_label = category
        user = _format_category_user(
            subject, audience_summary, chunk_label, chunk, gender, intensity,
        )
        try:
            resp = claude_messages(
                system=_CAT_ROW_SYSTEM, user=user,
                max_tokens=24000, temperature=0.3,
            )
        except Exception as e:
            print(f"[audience-cut] cat={category} chunk {i+1}/{n_chunks} "
                  f"claude failed: {e}")
            continue
        obj = _extract_json_block(resp) if resp else None
        if not isinstance(obj, dict):
            continue
        items = obj.get("items") or []
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            lbl = str(it.get("label", "")).strip().upper()
            nv = it.get("new_bp")
            if not lbl or not isinstance(nv, (int, float)):
                continue
            decisions[lbl] = max(0.0001, min(99.49, round(float(nv), 4)))
    return decisions


# =============================================================================
# Phase 3 -- apply transform with hard gender pin
# =============================================================================
def apply_audience_cut_transform(df, audience: dict, category_decisions: dict,
                                 subject: str, gender: str):
    """Mirrors avid_fan_row_by_row.apply_avid_transform but with a HARD
    GENDER pin: target bucket gets jittered ~99.99 (never exactly 100,
    never on a 2dp boundary), other buckets get tiny jittered values
    that sum to (100 - target). The non-GENDER demos still get
    Claude's targets + the standard renormalize-to-100 pass.
    """
    import pandas as pd
    df = df.copy()

    cat_col = "Column"
    val_col = "Value"
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)

    demo_targets = audience.get("audience_demo_targets", {}) or {}
    cohort_fraction = float(audience.get("cohort_fraction", 0.20) or 0.20)
    us_pop_fraction = float(audience.get("us_pop_fraction", 0.05) or 0.05)

    # New sample size + projection from the SOURCE's sample, scaled by
    # the cohort_fraction. Source can be the OG (for casual cuts) or
    # the avid CSV (for avid gender splits) -- same arithmetic either
    # way, since cohort_fraction is "fraction OF the source".
    cats_upper = df[cat_col].astype(str).str.upper().str.strip()
    ss_mask = cats_upper == "SAMPLE SIZE"
    if ss_mask.any():
        ss_row = df[ss_mask].iloc[0]
        try:
            old_sample = float(str(ss_row[raw_col]).replace(",", ""))
        except Exception:
            old_sample = 0
        try:
            old_uspop = float(str(ss_row[proj_col]).replace(",", ""))
        except Exception:
            old_uspop = 0
        new_sample = max(500, round(old_sample * cohort_fraction))
        # us_pop_fraction is absolute (frac of US pop), not relative,
        # but we let Claude pick that. Convert via old_uspop ratio.
        if old_uspop > 0 and old_sample > 0:
            new_uspop = max(5000, round(new_sample * old_uspop / old_sample))
        else:
            new_uspop = max(5000, round(330_000_000 * us_pop_fraction))
    else:
        new_sample = 50000
        new_uspop = 5_000_000

    # Cast cols to object so float assignment works
    for c in (bp_col, cs_col, raw_col, proj_col):
        if c in df.columns and df[c].dtype.name not in ("object", "O"):
            df[c] = df[c].astype(object)

    g_target_label, g_other_set = GENDER_BUCKET_LABELS[gender.upper()]

    n_demo = 0
    n_brand = 0
    n_pin = 0
    n_unchanged = 0
    n_gender_pin = 0

    for idx in df.index:
        cat = str(df.at[idx, cat_col]).strip().upper()
        val = str(df.at[idx, val_col]).strip()
        val_u = val.upper()

        if cat == "SAMPLE SIZE":
            df.at[idx, raw_col] = float(new_sample)
            df.at[idx, proj_col] = float(new_uspop)
            df.at[idx, cs_col] = float(new_sample)
            continue
        if cat == "BRAND INPUT":
            df.at[idx, raw_col] = float(new_sample)
            df.at[idx, proj_col] = float(new_uspop)
            continue
        if cat in {"BRAND CATEGORY", "INPUT_METADATA",
                   "BRAND ID", "REPORT INPUT"}:
            continue

        old_bp = _fbp(df.at[idx, bp_col])
        if old_bp is None:
            continue

        # Subject self-pin: still 100% -- both genders & both intensities
        # of fans recognize the subject (it's their fandom level that
        # differs, not the subject identification).
        if cat in SUBJECT_PIN_CATS_TF or cat == "SUBJECT":
            if abs(old_bp - 100.0) < 0.01 and val_u == subject.upper():
                df.at[idx, raw_col] = float(new_sample)
                df.at[idx, proj_col] = float(new_uspop)
                n_pin += 1
                continue

        # GENDER row: HARD pin.
        if cat == "GENDER":
            if val_u == g_target_label:
                # Target bucket: jittered ~99.99 (never exact)
                pinned = 99.99 + _seed_jitter(
                    f"{subject}|gender|{val_u}|cut-{gender}",
                    span=0.012,
                )
                pinned = max(99.50, min(99.997, round(pinned, 4)))
                df.at[idx, bp_col] = f"{pinned:.4f}%"
                df.at[idx, raw_col] = float(round(new_sample * pinned / 100.0))
                df.at[idx, proj_col] = float(round(new_uspop * pinned / 100.0))
            elif val_u in g_other_set or val_u != g_target_label:
                # Non-target buckets: jittered tiny, summing to ~0.01
                tiny = 0.005 + abs(_seed_jitter(
                    f"{subject}|gender|{val_u}|cut-other-{gender}",
                    span=0.008,
                ))
                tiny = max(0.0010, min(0.0490, round(tiny, 4)))
                df.at[idx, bp_col] = f"{tiny:.4f}%"
                df.at[idx, raw_col] = float(round(new_sample * tiny / 100.0))
                df.at[idx, proj_col] = float(round(new_uspop * tiny / 100.0))
            n_gender_pin += 1
            continue

        # Other demos: Claude targets if present, else jitter
        if cat in DEMO_CATS_TF:
            buckets = demo_targets.get(cat, {})
            new_bp = None
            for k, v in buckets.items():
                if str(k).strip().upper() == val_u:
                    new_bp = float(v)
                    break
            if new_bp is None:
                new_bp = old_bp + _seed_jitter(
                    f"{subject}|{cat}|{val_u}|cut-demo-fallback",
                    span=2.0,
                )
                new_bp = max(0.05, min(99.0, round(new_bp, 4)))
            new_bp = round(new_bp, 4)
            if abs(new_bp - old_bp) < 0.01:
                new_bp = round(new_bp + 0.01 + _seed_jitter(
                    f"{subject}|{cat}|{val_u}|cut-demo-collide", span=0.05,
                ), 4)
            df.at[idx, bp_col] = f"{new_bp:.4f}%"
            df.at[idx, raw_col] = float(round(new_sample * new_bp / 100.0))
            df.at[idx, proj_col] = float(round(new_uspop * new_bp / 100.0))
            n_demo += 1
            continue

        # Non-demo brand row. Per Jenna 2026-06-12 "no caps on anything
        # anywhere for agents", we DO NOT apply a default lift to rows
        # the agent didn't decide. Rows the agent didn't return are
        # re-jittered minimally (subject-salted, ±0.05pp) so they
        # don't 4dp-collide with the source -- but they're not pushed
        # up or down beyond that.
        cat_dec = category_decisions.get(cat, {})
        if val_u in cat_dec:
            new_bp = float(cat_dec[val_u])
            n_brand += 1
            new_bp = max(0.0001, min(99.49, round(new_bp, 4)))
        else:
            new_bp = round(
                old_bp + _seed_jitter(
                    f"{subject}|{cat}|{val_u}|cut-no-claude-jitter-{gender}",
                    span=0.10,
                ),
                4,
            )
            new_bp = max(0.0001, min(99.49, new_bp))
            n_unchanged += 1

        # Update raw + projection. Bp itself is only over-written if
        # we have a Claude decision OR a small no-collision jitter; we
        # never add a flat multiplier.
        df.at[idx, bp_col] = f"{new_bp:.4f}%"
        df.at[idx, raw_col] = float(round(new_sample * new_bp / 100.0))
        df.at[idx, proj_col] = float(round(new_uspop * new_bp / 100.0))

    # Renormalize each NON-GENDER demographic category to sum exactly
    # to 100 with subject-salted micro-jitter. GENDER stays pinned.
    n_demo_renorm = 0
    for cat in DEMO_CATS_TF:
        if cat == "GENDER":
            continue
        mask = (df[cat_col].astype(str).str.upper().str.strip() == cat)
        if not mask.any():
            continue
        rows = []
        for idx in df.index[mask]:
            v = _fbp(df.at[idx, bp_col])
            if v is None:
                continue
            label = str(df.at[idx, val_col]).strip()
            jittered = v + _seed_jitter(
                f"{subject}|{cat}|{label}|cut-demo-renorm-{gender}",
                span=0.10,
            )
            rows.append((idx, max(0.01, jittered), label))
        total = sum(v for _, v, _ in rows)
        if total <= 0:
            continue
        for idx, v, _label in rows:
            normed = round(v * 100.0 / total, 4)
            df.at[idx, bp_col] = f"{normed:.4f}%"
            df.at[idx, raw_col] = float(round(new_sample * normed / 100.0))
            df.at[idx, proj_col] = float(round(new_uspop * normed / 100.0))
            n_demo_renorm += 1

    # GENDER specifically: ensure the two/three buckets sum exactly to 100
    g_mask = (df[cat_col].astype(str).str.upper().str.strip() == "GENDER")
    if g_mask.any():
        g_rows = []
        for idx in df.index[g_mask]:
            v = _fbp(df.at[idx, bp_col])
            if v is None:
                continue
            g_rows.append((idx, v))
        g_total = sum(v for _, v in g_rows)
        if g_total > 0 and abs(g_total - 100.0) > 0.001:
            scale = 100.0 / g_total
            for idx, v in g_rows:
                normed = round(v * scale, 4)
                df.at[idx, bp_col] = f"{normed:.4f}%"
                df.at[idx, raw_col] = float(round(new_sample * normed / 100.0))
                df.at[idx, proj_col] = float(round(new_uspop * normed / 100.0))

    # Recompute Category Share within each non-demo category
    for cat, grp in df.groupby(cat_col):
        cu = str(cat).strip().upper()
        if cu in {"BRAND INPUT", "BRAND CATEGORY", "SAMPLE SIZE",
                  "INPUT_METADATA", "BRAND ID", "REPORT INPUT"}:
            continue
        if cu in DEMO_CATS_TF:
            continue
        bp_sum = 0.0
        for i in grp.index:
            v = _fbp(df.at[i, bp_col])
            if v is not None:
                bp_sum += v
        if bp_sum <= 0:
            continue
        for i in grp.index:
            v = _fbp(df.at[i, bp_col])
            if v is None:
                continue
            df.at[i, cs_col] = round(v / bp_sum * 100.0, 4)

    return df, {
        "new_sample_size": new_sample,
        "new_us_pop": new_uspop,
        "n_demo_rows": n_demo,
        "n_demo_renormed": n_demo_renorm,
        "n_brand_rows": n_brand,
        "n_subject_pin_rows": n_pin,
        "n_no_claude_jitter_rows": n_unchanged,
        "n_gender_pin_rows": n_gender_pin,
    }


# =============================================================================
# Orchestrator
# =============================================================================
# NOTE: there is NO PRIORITY_CATS allowlist. Per Jenna 2026-06-12 "no caps
# on anything anywhere for agents" -- the agent reasons about every brand
# category in the source profile, not just a hand-picked subset.
SKIP_CATS = {
    "BRAND INPUT", "BRAND CATEGORY", "INPUT_METADATA", "BRAND ID",
    "REPORT INPUT", "AVID FAN", "CASUAL FAN", "LOCATION", "SUBJECT",
    "SAMPLE SIZE",
}


def synthesize_audience_cut(
    source: str,
    *,
    gender: str,                       # 'F' or 'M'
    intensity: str,                    # 'avid' or 'casual'
    source_kind: str = "auto",
    dry_run: bool = False,
    register_in_dashboard: bool = True,
    source_s3_key: Optional[str] = None,
    subject_override: Optional[str] = None,
) -> dict:
    """End-to-end orchestrator for a (gender, intensity) audience cut.

    For CASUAL cuts, pass `source` = the OG profile (S3 key or local path).
    For AVID cuts, pass `source` = the existing Avid Fan profile so the
    cut is just a gender split of the avid cohort (per Jenna directive
    2026-06-12: "the gender split on avid would be skins from the avid
    profile").

    Returns a dict with `out_key`, `status`, `audience`, `stats`,
    `n_collisions_fixed`, `register_status`.
    """
    if str(gender).upper() not in ("F", "M"):
        raise ValueError(f"gender must be 'F' or 'M', got {gender!r}")
    if str(intensity).lower() not in ("avid", "casual"):
        raise ValueError(f"intensity must be 'avid' or 'casual', got {intensity!r}")
    gender = gender.upper()
    intensity = intensity.lower()

    import boto3
    s3 = boto3.client("s3", region_name=REGION)

    df_source, kind = _load_source_df(source, source_kind=source_kind)
    snap = build_source_snapshot(df_source)
    detected_subject = snap["subject"]
    if subject_override:
        subject = str(subject_override).strip()
        snap["subject"] = subject
        print(f"  subject_override applied: {detected_subject!r} -> "
              f"{subject!r}")
    else:
        subject = detected_subject
    source_label = (os.path.basename(source) if kind == "local_path"
                    else source)
    print(f"  subject={subject!r}  cats={snap['category_count']}  "
          f"sample={snap['sample_size']}  source={source_label}  "
          f"cut={intensity}+{gender}")

    print(f"  -> Phase 1: audience reasoning ({intensity}, gender={gender}) ...")
    audience = reason_audience_cut(snap, gender, intensity, source_label)
    print(f"     cohort_fraction={audience['cohort_fraction']:.4f}  "
          f"us_pop_fraction={audience['us_pop_fraction']:.4f}  "
          f"claude={audience.get('claude_used', False)}")
    print(f"     reasoning: {audience.get('reasoning', '')[:200]}")

    # Deterministic cohort_fraction override.
    # Per Jenna 2026-06-12: "the percentage of female in avid should be
    # the sample size for avid female ... the code needs to know that
    # these are skins off of the avid so if you split by gender it
    # would need to conform to those". When source IS already at the
    # target intensity, cohort_fraction = gender_share_in_source (and
    # the gender splits sum to 100% of source). When source is OG, we
    # multiply gender_share * intensity_share. Either way this is
    # math, not vibes -- so we override Claude's estimate with the
    # data-derived value. us_pop_fraction is rescaled proportionally.
    source_intensity = _detect_source_intensity(source, kind)
    det_cf = _compute_deterministic_cohort_fraction(
        df_source, gender, intensity, source_intensity,
    )
    if det_cf is not None and det_cf > 0:
        old_cf = float(audience.get("cohort_fraction", 0.0) or 0.0)
        old_uf = float(audience.get("us_pop_fraction", 0.0) or 0.0)
        audience["cohort_fraction"] = det_cf
        if old_cf > 0:
            audience["us_pop_fraction"] = max(
                0.001, min(0.95, old_uf * det_cf / old_cf),
            )
        audience["deterministic_cf"] = True
        audience["claude_cf_was"] = old_cf
        audience["source_intensity"] = source_intensity
        print(f"     deterministic cohort_fraction (source_intensity="
              f"{source_intensity!r}, gender={gender}): "
              f"{det_cf:.4f}  (overriding Claude's {old_cf:.4f})  "
              f"us_pop_fraction adjusted to {audience['us_pop_fraction']:.4f}")

    cat_col = "Column"
    val_col = "Value"
    bp_col, _, _, _ = _detect_cols(df_source)
    cats_upper = df_source[cat_col].astype(str).str.upper().str.strip()
    all_non_demo = []
    for cat, _ in df_source.groupby(cats_upper):
        cu = str(cat).strip().upper()
        if cu in DEMO_CATS_TF or cu in SKIP_CATS or cu == "":
            continue
        all_non_demo.append(cu)
    # Sort: largest categories first so any rate-limit hiccup affects
    # tail/small cats, not high-signal ones.
    cat_sizes = {c: int((cats_upper == c).sum()) for c in all_non_demo}
    cats_to_call = sorted(all_non_demo, key=lambda c: -cat_sizes[c])
    total_rows = sum(cat_sizes.values())
    print(f"  -> Phase 2: ALL {len(cats_to_call)} non-demo categories "
          f"({total_rows} rows total) -- no priority gating, no top-N cap ...")
    cat_decisions = {}
    rows_decided = 0
    for i, cat in enumerate(cats_to_call, start=1):
        rows = []
        for _, r in df_source[cats_upper == cat].iterrows():
            v = _fbp(r.get(bp_col, 0))
            if v is None:
                continue
            rows.append((str(r.get(val_col, "")).strip(), v))
        if not rows:
            continue
        decisions = reason_category_rows_cut(
            subject, audience, cat, rows, gender, intensity,
        )
        if decisions:
            cat_decisions[cat] = decisions
        rows_decided += len(decisions)
        print(f"     [{i:>2d}/{len(cats_to_call)}] {cat:32s} "
              f"rows={len(rows):>4d}  claude_returned={len(decisions):>4d}  "
              f"(running total decided={rows_decided}/{total_rows})",
              flush=True)

    print(f"  -> Phase 3: apply transform row-by-row ...")
    df_cut, stats = apply_audience_cut_transform(
        df_source, audience, cat_decisions, subject, gender,
    )
    print(f"     {stats}")

    print(f"  -> Phase 4: no-collision enforcement vs source ...")
    df_cut, n_fixed = enforce_no_collisions(df_cut, df_source, subject)
    print(f"     re-jittered {n_fixed} rows to break collisions")

    # Output filename: "<Subject> - <Intensity> <Gender> Fan.csv"
    subj_clean = re.sub(r"\s+", " ", subject).strip()
    label = _label_for_cut(gender, intensity)
    out_key = f"{subj_clean} - {label}.csv"

    # Defense-in-depth: ensure BRAND CATEGORY row is populated, inheriting
    # from source df if missing on the cut. Same logic as
    # avid_fan_row_by_row's safeguard.
    try:
        col_u_bc = df_cut["Column"].astype(str).str.strip().str.upper()
        bc_mask = col_u_bc == "BRAND CATEGORY"
        bc_value = ""
        if bc_mask.any():
            bc_value = str(df_cut.loc[bc_mask, "Value"].iloc[0]).strip()
        if not bc_value or bc_value.upper() in ("UNKNOWN", "NAN", "NONE"):
            try:
                src_col_u = df_source["Column"].astype(str).str.strip().str.upper()
                src_mask = src_col_u == "BRAND CATEGORY"
                if src_mask.any():
                    bc_value = str(df_source.loc[src_mask, "Value"].iloc[0]).strip()
            except Exception:
                pass
        if bc_value and bc_value.upper() not in ("UNKNOWN", "NAN", "NONE"):
            try:
                from BG import enforce_brand_category_row
                df_cut = enforce_brand_category_row(df_cut, bc_value)
            except Exception:
                if not bc_mask.any():
                    import pandas as _pd_bc
                    new_row = {c: "" for c in df_cut.columns}
                    new_row[df_cut.columns[0]] = "BRAND CATEGORY"
                    new_row[df_cut.columns[1]] = bc_value
                    ss_idx = df_cut.index[col_u_bc == "SAMPLE SIZE"].tolist()
                    insert_at = ss_idx[0] + 1 if ss_idx else 2
                    top = df_cut.iloc[:insert_at]
                    bot = df_cut.iloc[insert_at:]
                    df_cut = _pd_bc.concat(
                        [top, _pd_bc.DataFrame([new_row]), bot],
                        ignore_index=True,
                    )
        else:
            print(f"   ⚠ no BRAND CATEGORY found on source for {subject!r} "
                  f"-- cut will be UNCATEGORIZED. Patch via "
                  f"scripts/categorize_uncategorized_profiles.py.")
    except Exception as _bc_err:
        print(f"   ⚠ BRAND CATEGORY safeguard skipped: {_bc_err}")

    if dry_run:
        return {
            "out_key": out_key, "status": "dry-run",
            "audience": audience, "stats": stats,
            "n_collisions_fixed": n_fixed, "df_cut": df_cut,
        }

    new_body = df_cut.to_csv(index=False).encode("utf-8")
    backup_key = (f"_backups/{out_key}.pre_cut_overwrite_"
                  f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv")
    try:
        s3.head_object(Bucket=BUCKET, Key=out_key)
        s3.copy_object(
            Bucket=BUCKET, Key=backup_key,
            CopySource={"Bucket": BUCKET, "Key": out_key},
        )
    except Exception:
        pass
    s3.put_object(
        Bucket=BUCKET, Key=out_key, Body=new_body, ContentType="text/csv",
    )

    register_status = None
    if register_in_dashboard:
        try:
            try:
                from migration.dashboard_register import register_profile_in_dashboard
            except ImportError:
                from dashboard_register import register_profile_in_dashboard
            parent_key = source if kind == "s3_key" else source_s3_key
            register_status = register_profile_in_dashboard(
                out_key,
                display_name=f"{subj_clean} - {label}",
                source_key=parent_key,
                s3_client=s3,
            )
            print(f"  ✓ registered in dashboard "
                  f"(quick_select_added={register_status.get('quick_select_added')}, "
                  f"cache_added={register_status.get('cache_added')})")
        except Exception as e:
            print(f"  ⚠ dashboard register skipped for {out_key}: {e}")

    return {
        "out_key": out_key, "status": "uploaded",
        "audience": audience, "stats": stats,
        "n_collisions_fixed": n_fixed,
        "register_status": register_status,
    }


__all__ = [
    "synthesize_audience_cut",
    "reason_audience_cut",
    "reason_category_rows_cut",
    "apply_audience_cut_transform",
    "GENDER_BUCKET_LABELS",
    "INTENSITY_DESCRIPTIONS",
]
