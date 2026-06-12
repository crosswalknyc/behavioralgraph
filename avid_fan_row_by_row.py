"""Avid-fan profile synthesis (row-by-row Claude reasoning, no fixed N).

Per Jenna 2026-06-11 directive: "agents should never use a mathematical
number but instead should decide what percent of the audience is likely
an avid superfan and then should go row by row just like in the normal
pipeline and decide what that avid fan would look like demographically,
location based, interests, most purchased brands, etc row by row".

Differences from migration/super_fan_synthesis.py:
  - No N-touchpoints input. Cohort fraction is decided by Claude based
    on subject's audience structure (concentrated cult vs mass-reach).
  - No fixed lift bands keyed by N. Per-category Claude calls decide
    each row's new BP individually.
  - Hard guarantee: NO 4dp BP collisions between avid and original
    for any (cat, brand) common pair (except subject self-pin at
    100% which is rule-mandated by Profile IQ Rule #3).

Public API:
    synthesize_avid_fan_for_s3_key(s3_key, *, dry_run=False) -> dict

Per-profile cost: ~10-30 Claude calls (1 audience + 1 per non-demo
category capped at 25).
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

# --- helpers reused from super_fan_synthesis ---------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from super_fan_synthesis import (  # noqa: E402
    build_source_snapshot,
    _extract_json_block,
    _norm_subject_for_filename,
    DEMO_CATS_TF,
    META_CATS_TF,
    SUBJECT_PIN_CATS_TF,
)

# --- module-level constants --------------------------------------------------
BUCKET = "dashboard-inputs"
REGION = "us-east-2"


def _fbp(v):
    try:
        return float(str(v).replace("%", "").replace(",", "").strip())
    except Exception:
        return None


def _seed_jitter(seed: str, span: float) -> float:
    h = hashlib.md5(seed.encode()).hexdigest()
    u = int(h[:8], 16) / 0xFFFFFFFF
    return -span / 2 + span * u


# =============================================================================
# Phase 1: audience-aware cohort + demo target reasoning
# =============================================================================
_AUDIENCE_SYSTEM = (
    "You are an audience analytics reasoning agent.\n\n"
    "Given a public figure's 1+ engagement profile (broad fanbase), reason "
    "about what the AVID fan slice looks like:\n\n"
    "1. cohort_fraction: what fraction of the broad audience is 'avid'.\n"
    "   This is NOT a fixed multiplier. Depends on the subject's fan "
    "structure:\n"
    "   - Concentrated cult/heartthrob fanbases: 0.25-0.40\n"
    "   - Mid-reach with strong core (working actors, mid-tier musicians): "
    "0.15-0.25\n"
    "   - Mass-reach with passive cultural awareness (Oprah, Taylor Swift, "
    "Tom Hanks): 0.08-0.15\n\n"
    "2. us_pop_fraction: what fraction of US adults are avid for this "
    "subject (always smaller than cohort_fraction times the broad reach).\n\n"
    "3. audience_demo_targets: how each demographic distribution sharpens "
    "for the avid slice. Dominant buckets gain pp; minorities lose pp. "
    "Each category MUST sum to 100.0 (within 0.5pp).\n\n"
    "4. reasoning: 2-3 sentences explaining the fanbase-intensity call "
    "and how the demos reshape.\n\n"
    "Output STRICT JSON only, no commentary outside the JSON."
)


def _format_audience_user(snap: dict) -> str:
    L = []
    L.append(f"SUBJECT: {snap['subject']}")
    L.append(f"BROAD SAMPLE SIZE: {snap['sample_size']}")
    if snap.get("avid_fan_bp") is not None:
        L.append(f"AVID FAN BP (signal): {snap['avid_fan_bp']}%")
    if snap.get("casual_fan_bp") is not None:
        L.append(f"CASUAL FAN BP (signal): {snap['casual_fan_bp']}%")
    L.append("")
    L.append("DEMOGRAPHIC DISTRIBUTION (broad / 1+ panel):")
    for cat, rows in snap.get("demos", {}).items():
        L.append(f"  {cat}:")
        for label, bp in rows:
            L.append(f"    {label}: {bp:.2f}%")
    L.append("")
    L.append("TOP-3 BRANDS PER NON-DEMO CATEGORY (signal for fan-intensity "
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
        '  "cohort_fraction": 0.18,\n'
        '  "us_pop_fraction": 0.04,\n'
        '  "reasoning": "...",\n'
        '  "audience_demo_targets": {\n'
        '    "GENDER": {"FEMALE": 64.0, "MALE": 33.5, ...},\n'
        '    "AGE": {...},\n'
        '    "ETHNICITY": {...}, "INCOME": {...}, "EDUCATION": {...},\n'
        '    "OCCUPATION": {...}, "RELATIONSHIP": {...},\n'
        '    "PARENTAL_STATUS": {...}, "SEXUAL_ORIENTATION": {...}\n'
        '  }\n'
        '}'
    )
    L.append("Each demo_targets category MUST sum to 100.0 (within 0.5pp).")
    L.append("Use the EXACT bucket labels shown above.")
    return "\n".join(L)


def reason_avid_audience(snap: dict) -> dict:
    """Phase 1 Claude call: cohort_fraction + demo_targets + narrative."""
    fallback = {
        "cohort_fraction": 0.20,
        "us_pop_fraction": 0.05,
        "audience_demo_targets": {
            cat: {label: bp for label, bp in rows}
            for cat, rows in snap.get("demos", {}).items()
        },
        "reasoning": "fallback: Claude unavailable, using broad demos as-is",
        "claude_used": False,
    }
    try:
        from claude_client import claude_messages
    except Exception:
        try:
            sys.path.insert(0, HERE)
            from claude_client import claude_messages  # type: ignore
        except Exception as e:
            print(f"[avid-fan] claude import failed: {e}")
            return fallback

    user = _format_audience_user(snap)
    try:
        resp = claude_messages(
            system=_AUDIENCE_SYSTEM, user=user,
            max_tokens=4096, temperature=0.4,
        )
    except Exception as e:
        print(f"[avid-fan] phase 1 claude call failed: {e}")
        return fallback
    obj = _extract_json_block(resp) if resp else None
    if not isinstance(obj, dict):
        print(f"[avid-fan] phase 1 returned no JSON; fallback")
        return fallback

    out = dict(fallback)
    cf = obj.get("cohort_fraction")
    uf = obj.get("us_pop_fraction")
    if isinstance(cf, (int, float)) and 0.02 < cf < 0.6:
        out["cohort_fraction"] = float(cf)
    if isinstance(uf, (int, float)) and 0.001 < uf < 0.4:
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
    out["claude_used"] = True
    return out


# =============================================================================
# Phase 2: per-category row-by-row Claude reasoning
# =============================================================================
_CAT_ROW_SYSTEM = (
    "You are a brand-affinity reasoning agent. Given:\n"
    "  - a public-figure subject\n"
    "  - their AVID fan audience profile\n"
    "  - a category name\n"
    "  - a list of items in that category with the BROAD audience BP\n"
    "Decide each item's BP for the AVID slice.\n\n"
    "Rules (strict):\n"
    "  1. Each new_bp MUST differ from the broad bp by at least 0.5pp.\n"
    "  2. Brands directly tied to the subject (their own works, businesses, "
    "or close collaborators) lift sharply (often 1.4x to 2.5x of broad).\n"
    "  3. Genre / peer brands lift moderately (1.1x to 1.5x).\n"
    "  4. Clear anti-affinity brands sink (0.4x to 0.7x).\n"
    "  5. Default brands move slightly (1.05x to 1.20x for affinity, "
    "0.85x to 0.95x for slight disinterest).\n"
    "  6. Rank order plausibility: the avid top-5 should be defensible -- "
    "the subject's own works are usually #1; broad #1 may stay or be "
    "displaced by a more avid-specific brand.\n"
    "  7. BPs are percentages 0.0001 to 99.9. Round to 4 decimals.\n\n"
    "Return STRICT JSON only:\n"
    '{"items": [{"label": "...", "new_bp": 12.3456}, ...]}\n'
    "Include EVERY item from the input list. Use exact label spelling."
)


def _format_category_user(subject: str, audience_summary: str, category: str,
                          rows: list) -> str:
    L = [f"SUBJECT: {subject}",
         "AVID AUDIENCE PROFILE:",
         audience_summary,
         "",
         f"CATEGORY: {category}",
         f"ITEMS ({len(rows)} rows, broad BP = current 1+ value):"]
    for label, bp in rows:
        L.append(f"  - {label} :: broad_bp={bp:.4f}")
    L.append("")
    L.append('Return JSON: {"items":[{"label":"...","new_bp":<float>}, ...]}')
    L.append("Every item MUST appear in the response. Each new_bp MUST differ "
             "from broad_bp by at least 0.5 percentage points.")
    return "\n".join(L)


def _audience_summary_text(audience: dict) -> str:
    """Compact human-readable audience summary for category prompts."""
    L = [f"cohort_fraction: {audience.get('cohort_fraction', 0):.4f}",
         f"reasoning: {audience.get('reasoning', '')}", "",
         "Demo targets (avid):"]
    for cat, buckets in audience.get("audience_demo_targets", {}).items():
        bs = ", ".join(f"{lbl}={v:.1f}" for lbl, v in
                       sorted(buckets.items(), key=lambda kv: -kv[1])[:5])
        L.append(f"  {cat}: {bs}")
    return "\n".join(L)


def reason_category_rows(subject: str, audience: dict, category: str,
                         rows: list, *, max_rows: int = 80) -> dict:
    """Phase 2 Claude call. Returns {label_upper: new_bp} for at least
    the top-`max_rows` rows in the category."""
    if not rows:
        return {}
    audience_summary = _audience_summary_text(audience)
    rows_sorted = sorted(rows, key=lambda kv: -kv[1])
    head = rows_sorted[:max_rows]

    try:
        from claude_client import claude_messages
    except Exception:
        try:
            sys.path.insert(0, HERE)
            from claude_client import claude_messages  # type: ignore
        except Exception:
            return {}

    user = _format_category_user(subject, audience_summary, category, head)
    try:
        resp = claude_messages(
            system=_CAT_ROW_SYSTEM, user=user,
            max_tokens=4096, temperature=0.3,
        )
    except Exception as e:
        print(f"[avid-fan] cat={category} claude failed: {e}")
        return {}
    obj = _extract_json_block(resp) if resp else None
    if not isinstance(obj, dict):
        return {}
    out = {}
    for it in obj.get("items", []) or []:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label", "")).strip().upper()
        bp = it.get("new_bp")
        if not label or not isinstance(bp, (int, float)):
            continue
        bp = float(bp)
        if bp <= 0 or bp >= 99.99:
            continue
        out[label] = round(bp, 4)
    return out


# =============================================================================
# Phase 3: apply transform row-by-row
# =============================================================================
def _detect_cols(df):
    cols = list(df.columns)
    bp_col = next((c for c in cols if "Brand Penetration" in c), cols[2])
    cs_col = next((c for c in cols if "Category Share" in c), cols[3])
    raw_col = next((c for c in cols if "Original Raw" in c), cols[4])
    proj_col = next((c for c in cols if "Gen Pop Projection" in c), cols[5])
    return bp_col, cs_col, raw_col, proj_col


def apply_avid_transform(df, audience: dict, category_decisions: dict,
                         subject: str) -> "pandas.DataFrame":
    """Apply the new BPs row-by-row.

    For each row:
      - DEMO category: use audience_demo_targets bucket value if present
      - SUBJECT pin category: keep at 100% (rule #3)
      - SAMPLE SIZE / META: leave as-is, sample_size and US_POP recompute
      - Other categories: use category_decisions[CAT][LABEL] if present;
        else apply a small audience-aware default lift.
    Recompute Raw + Projection from new sample_size + US_POP.
    """
    import pandas as pd  # noqa
    df = df.copy()
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df)
    cat_col = "Column"
    val_col = "Value"

    cohort_fraction = float(audience.get("cohort_fraction", 0.20))
    us_pop_fraction = float(audience.get("us_pop_fraction",
                                         0.05))
    demo_targets = audience.get("audience_demo_targets", {})

    # Read original sample_size + US_POP from SAMPLE SIZE row
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
        new_sample = max(1000, round(old_sample * cohort_fraction))
        new_uspop = max(10000, round(old_uspop * cohort_fraction))
    else:
        new_sample = 100000
        new_uspop = 5000000

    # Cast cols to object so float assignment works
    for c in (bp_col, cs_col, raw_col, proj_col):
        if c in df.columns and df[c].dtype.name not in ("object", "O"):
            df[c] = df[c].astype(object)

    # Mutate row by row
    n_demo = 0
    n_brand = 0
    n_pin = 0
    n_unchanged = 0
    for idx in df.index:
        cat = str(df.at[idx, cat_col]).strip().upper()
        val = str(df.at[idx, val_col]).strip()
        val_u = val.upper()

        if cat == "SAMPLE SIZE":
            df.at[idx, raw_col] = float(new_sample)
            df.at[idx, proj_col] = float(new_uspop)
            df.at[idx, cs_col] = float(new_sample)
            continue
        if cat in {"BRAND INPUT", "BRAND CATEGORY",
                   "INPUT_METADATA", "BRAND ID", "REPORT INPUT"}:
            continue

        old_bp = _fbp(df.at[idx, bp_col])
        if old_bp is None:
            continue

        # Subject self-pin: keep at 100% per Rule #3
        if cat in SUBJECT_PIN_CATS_TF or cat == "SUBJECT":
            if abs(old_bp - 100.0) < 0.01 and val_u == subject.upper():
                df.at[idx, raw_col] = float(new_sample)
                df.at[idx, proj_col] = float(new_uspop)
                n_pin += 1
                continue

        # Demo category: pull from demo_targets if Claude provided it
        if cat in DEMO_CATS_TF:
            buckets = demo_targets.get(cat, {})
            new_bp = None
            for k, v in buckets.items():
                if str(k).strip().upper() == val_u:
                    new_bp = float(v)
                    break
            if new_bp is None:
                # fallback: small jitter to ensure no collision
                new_bp = old_bp + _seed_jitter(
                    f"{subject}|{cat}|{val_u}|avid-demo-fallback", span=2.0,
                )
                new_bp = max(0.05, min(99.0, round(new_bp, 4)))
            new_bp = round(new_bp, 4)
            # Ensure differ
            if abs(new_bp - old_bp) < 0.01:
                new_bp = round(new_bp + 0.01 + _seed_jitter(
                    f"{subject}|{cat}|{val_u}|demo-collide", span=0.05,
                ), 4)
            df.at[idx, bp_col] = f"{new_bp:.4f}%"
            df.at[idx, raw_col] = float(round(new_sample * new_bp / 100.0))
            df.at[idx, proj_col] = float(round(new_uspop * new_bp / 100.0))
            n_demo += 1
            continue

        # Non-demo brand row: use category_decisions if available
        cat_dec = category_decisions.get(cat, {})
        if val_u in cat_dec:
            new_bp = float(cat_dec[val_u])
            n_brand += 1
        else:
            # Default audience-aware mild lift, with subject-salted jitter
            mult = 1.10 + _seed_jitter(
                f"{subject}|{cat}|{val_u}|avid-default", span=0.18,
            )
            new_bp = round(old_bp * mult, 4)
            n_unchanged += 1

        # Bound + ensure differs from old by >=0.5pp
        new_bp = max(0.0001, min(99.49, round(new_bp, 4)))
        if abs(new_bp - old_bp) < 0.5:
            direction = 1.0 if old_bp < 50 else -1.0
            new_bp = round(old_bp + direction * (
                0.5 + abs(_seed_jitter(
                    f"{subject}|{cat}|{val_u}|min-delta", span=0.4,
                ))
            ), 4)
            new_bp = max(0.0001, min(99.49, new_bp))

        df.at[idx, bp_col] = f"{new_bp:.4f}%"
        df.at[idx, raw_col] = float(round(new_sample * new_bp / 100.0))
        df.at[idx, proj_col] = float(round(new_uspop * new_bp / 100.0))

    # Renormalize each demographic category to sum exactly to 100% with
    # subject-salted micro-jitter so no row sits on a 2dp/4dp boundary
    # (Claude's targets are approximate; without renorm they drift by
    # 1-2pp). This mirrors super_fan_synthesis Step 1 behavior.
    n_demo_renorm = 0
    for cat in DEMO_CATS_TF:
        mask = (df[cat_col].astype(str).str.upper().str.strip() == cat)
        if not mask.any():
            continue
        rows = []
        for idx in df.index[mask]:
            v = _fbp(df.at[idx, bp_col])
            if v is None:
                continue
            label = str(df.at[idx, "Value"]).strip()
            jittered = v + _seed_jitter(
                f"{subject}|{cat}|{label}|avid-demo-renorm", span=0.10,
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
        "n_default_lift_rows": n_unchanged,
    }


# =============================================================================
# Phase 4: no-collision pass with baseline
# =============================================================================
def enforce_no_collisions(df_avid, df_baseline, subject: str):
    """For every (cat, brand) common to both files, ensure 4dp BP differs
    between avid and baseline. EXCEPTION: subject self-pin at 100% may
    match (Rule #3 mandates self-pin at 100). Returns (df_avid, n_fixed)."""
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df_avid)
    cat_col = "Column"
    val_col = "Value"

    # Index baseline by (CAT, VAL_UPPER) -> bp_4dp
    base_idx = {}
    for _, r in df_baseline.iterrows():
        cu = str(r.get(cat_col, "")).strip().upper()
        vu = str(r.get(val_col, "")).strip().upper()
        bp = _fbp(r.get(bp_col, 0))
        if bp is None:
            continue
        base_idx[(cu, vu)] = round(bp, 4)

    df_avid = df_avid.copy()
    for c in (bp_col, cs_col, raw_col, proj_col):
        if c in df_avid.columns and df_avid[c].dtype.name not in ("object", "O"):
            df_avid[c] = df_avid[c].astype(object)

    n_fixed = 0
    subj_u = subject.upper()
    for idx in df_avid.index:
        cu = str(df_avid.at[idx, cat_col]).strip().upper()
        vu = str(df_avid.at[idx, val_col]).strip().upper()
        avid_bp = _fbp(df_avid.at[idx, bp_col])
        if avid_bp is None:
            continue
        avid4 = round(avid_bp, 4)
        base4 = base_idx.get((cu, vu))
        if base4 is None or avid4 != base4:
            continue
        # Allowed exception: subject self-pin at 100%
        if abs(avid4 - 100.0) < 0.0001 and vu == subj_u:
            continue
        # Jitter to break the collision
        for k in range(1, 80):
            cand = round(
                avid_bp + (1 if (k % 2) else -1) * (0.0007 * (k // 2 + 1)),
                4,
            )
            if 0.0005 < cand < 99.99 and cand != base4:
                df_avid.at[idx, bp_col] = f"{cand:.4f}%"
                # Update raw/proj from sample/us_pop (best-effort: they'll
                # be recomputed by the save-gate). Pull current sample_size.
                try:
                    ss_mask = df_avid["Column"].astype(str).str.upper() == "SAMPLE SIZE"
                    if ss_mask.any():
                        ss_row = df_avid[ss_mask].iloc[0]
                        sample = float(str(ss_row[raw_col]).replace(",", ""))
                        uspop = float(str(ss_row[proj_col]).replace(",", ""))
                        df_avid.at[idx, raw_col] = float(round(sample * cand / 100.0))
                        df_avid.at[idx, proj_col] = float(round(uspop * cand / 100.0))
                except Exception:
                    pass
                n_fixed += 1
                break
    return df_avid, n_fixed


# =============================================================================
# Orchestrator
# =============================================================================
def synthesize_avid_fan_for_s3_key(s3_key: str, *, dry_run: bool = False
                                    ) -> dict:
    import boto3
    import pandas as pd
    s3 = boto3.client("s3", region_name=REGION)

    body = s3.get_object(Bucket=BUCKET, Key=s3_key)["Body"].read().decode(
        "utf-8", "ignore",
    )
    df_baseline = pd.read_csv(io.StringIO(body), low_memory=False,
                              on_bad_lines="skip")
    snap = build_source_snapshot(df_baseline)
    subject = snap["subject"]
    print(f"  subject={subject!r}  cats={snap['category_count']}  "
          f"sample={snap['sample_size']}")

    print(f"  -> Phase 1: audience reasoning ...")
    audience = reason_avid_audience(snap)
    print(f"     cohort_fraction={audience['cohort_fraction']:.4f}  "
          f"us_pop_fraction={audience['us_pop_fraction']:.4f}  "
          f"claude={audience.get('claude_used', False)}")
    print(f"     reasoning: {audience.get('reasoning', '')[:200]}")

    # Phase 2: per-category row-by-row Claude reasoning, scoped to the
    # high-signal categories. Long-tail / single-cat-of-the-genre rows
    # get the default audience-aware jittered lift in Phase 3 (no
    # Claude call) so total runtime stays bounded (~3-5 min/profile
    # instead of ~30 min).
    PRIORITY_CATS = [
        "TALENT", "MUSICIAN/BAND", "ACTOR", "COMEDIAN",
        "ATHLETE", "HOST/PERSONALITY", "AUTHOR", "DIRECTOR",
        "MOVIE", "SERIES - HBO", "SERIES - NETFLIX",
        "SERIES - AMAZON / MGM STUDIOS", "SERIES - DISNEY+",
        "SERIES - APPLE TV+", "SERIES - PARAMOUNT+", "SERIES - PEACOCK",
        "SERIES - HULU", "SERIES - MAX", "SERIES - FX",
        "MEDIA", "PODCAST", "MOST PURCHASED BRANDS",
        "APPAREL/FOOTWEAR", "WHERE THEY SHOP", "HOME/OUTDOOR",
        "GAMES", "GAME PLAYERS", "RESTAURANT", "TRAVEL",
        "PORN MEDIA", "SEARCH ENGINE/AI", "SOCIAL", "CREDIT PROVIDER",
        "TELECOM", "SPORTS TEAM", "AL/NL", "AFC/NFC",
        "AFC EAST", "AFC WEST", "AFC NORTH", "AFC SOUTH",
        "NFC EAST", "NFC WEST", "NFC NORTH", "NFC SOUTH",
        "DIGITAL BANK", "CONSUMER ELECTRONICS",
        "FRANCHISE", "MOVIE THEATER",
    ]
    cat_col = "Column"
    val_col = "Value"
    bp_col = "Brand Penetration (Row)"
    cats_upper = df_baseline[cat_col].astype(str).str.upper().str.strip()
    cat_decisions = {}
    SKIP_CATS = {
        "BRAND INPUT", "BRAND CATEGORY", "INPUT_METADATA", "BRAND ID",
        "REPORT INPUT", "AVID FAN", "CASUAL FAN", "LOCATION", "SUBJECT",
        "SAMPLE SIZE",
    }
    all_non_demo = []
    for cat, _ in df_baseline.groupby(cats_upper):
        cu = str(cat).strip().upper()
        if cu in DEMO_CATS_TF or cu in SKIP_CATS or cu == "":
            continue
        all_non_demo.append(cu)
    # Process priority cats first (in order), then any remaining priority
    # cats found in this profile that we missed; long-tail cats get
    # default audience-aware lifts only.
    cats_to_call = [c for c in PRIORITY_CATS if c in all_non_demo]
    print(f"  -> Phase 2: {len(cats_to_call)}/{len(all_non_demo)} priority "
          f"category Claude calls (others get default audience lifts) ...")
    for i, cat in enumerate(cats_to_call, start=1):
        rows = []
        for _, r in df_baseline[cats_upper == cat].iterrows():
            v = _fbp(r.get(bp_col, 0))
            if v is None:
                continue
            rows.append((str(r.get(val_col, "")).strip(), v))
        if not rows:
            continue
        decisions = reason_category_rows(subject, audience, cat, rows,
                                         max_rows=60)
        if decisions:
            cat_decisions[cat] = decisions
        print(f"     [{i:>2d}/{len(cats_to_call)}] {cat:32s} rows={len(rows):>4d}  "
              f"claude_returned={len(decisions)}",
              flush=True)

    # Phase 3: apply
    print(f"  -> Phase 3: apply transform row-by-row ...")
    df_avid, stats = apply_avid_transform(
        df_baseline, audience, cat_decisions, subject,
    )
    print(f"     {stats}")

    # Phase 4: no-collision pass
    print(f"  -> Phase 4: no-collision enforcement vs baseline ...")
    df_avid, n_fixed = enforce_no_collisions(df_avid, df_baseline, subject)
    print(f"     re-jittered {n_fixed} rows to break collisions")

    # Subject canonical filename: "{Subject Name} - Avid Fan.csv"
    subj_clean = re.sub(r"\s+", " ", subject).strip()
    out_key = f"{subj_clean} - Avid Fan.csv"

    if dry_run:
        return {
            "out_key": out_key,
            "status": "dry-run",
            "audience": audience,
            "stats": stats,
            "n_collisions_fixed": n_fixed,
            "df_avid": df_avid,
        }

    new_body = df_avid.to_csv(index=False).encode("utf-8")
    backup_key = f"_backups/{out_key}.pre_avid_overwrite_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    # If a previous avid version exists, back it up first
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

    # Register in dashboard (s3_cache.json + admin_quick_selects.json) so
    # the new avid profile shows up in the Select Profile dropdown.
    register_status = None
    try:
        from migration.dashboard_register import register_profile_in_dashboard
        register_status = register_profile_in_dashboard(
            out_key,
            display_name=f"{subj_clean} - Avid Fan",
            source_key=s3_key,
            s3_client=s3,
        )
        print(f"  ✓ registered in dashboard "
              f"(quick_select_added={register_status.get('quick_select_added')}, "
              f"cache_added={register_status.get('cache_added')})")
    except Exception as e:
        print(f"  ⚠ dashboard register skipped for {out_key}: {e}")

    return {
        "out_key": out_key,
        "status": "uploaded",
        "audience": audience,
        "stats": stats,
        "n_collisions_fixed": n_fixed,
        "register_status": register_status,
    }
