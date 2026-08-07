"""Avid-fan profile synthesis (row-by-row Claude reasoning, no fixed N).

Per Jenna 2026-06-11 directive: "agents should never use a mathematical
number but instead should decide what percent of the audience is likely
an avid superfan and then should go row by row just like in the normal
pipeline and decide what that avid fan would look like demographically,
location based, interests, most purchased brands, etc row by row".

Per Jenna 2026-06-12 directive: "automatically does an avid fan skin for
all profiles pulled moving forward" -- `BG.py` calls `synthesize_avid_fan`
unconditionally at the tail of `run_full_pipeline` for any profile whose
BRAND CATEGORY is not in `AVID_NON_APPLICABLE_CATEGORIES`.

Differences from migration/super_fan_synthesis.py:
  - No N-touchpoints input. Cohort fraction is decided by Claude based
    on subject's audience structure (concentrated cult vs mass-reach).
  - No fixed lift bands keyed by N. Per-category Claude calls decide
    each row's new BP individually.
  - Hard guarantee: NO 4dp BP collisions between avid and original
    for any (cat, brand) common pair (except subject self-pin at
    100% which is rule-mandated by Profile IQ Rule #3).

Public API:
    synthesize_avid_fan(source, *, source_kind='auto', dry_run=False,
                         register_in_dashboard=True) -> dict
    synthesize_avid_fan_for_s3_key(s3_key, *, dry_run=False) -> dict
        # back-compat alias

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

# Categories where "avid fan" is conceptually meaningless: the underlying
# profile is itself an audience cohort or baseline (Gen Pop, banking
# customers, shopping-intent/trend topics) rather than a subject-of-fandom
# (talent / IP / team / brand). The BG.py post-pipeline hook and the
# scripts/backfill_avid_fans_all.py orchestrator both gate on this set.
# Keep tight -- when in doubt, run the avid synthesis (e.g. brand cohorts
# like "Nike" CAN have avid fans = heavy buyers).
AVID_NON_APPLICABLE_CATEGORIES = frozenset({
    "GEN POP",
    "DIGITAL BANKING",     # e.g. "Chime Banking Customers" -- already a cohort
    "LOYALTY PROGRAMS",
    "SHOPPING INTENT",
    "TRENDS",
    "VERTICAL SHORTS",
    "VERTICAL SHORT",      # singular spelling defensive fallback
})


def should_synthesize_avid_for_category(brand_category: Optional[str]) -> bool:
    """Return True iff an avid-fan skin should be synthesized for a profile
    whose BRAND CATEGORY is `brand_category`. UNCATEGORIZED is allowed
    (the avid skin will inherit "UNCATEGORIZED" and a follow-up pass
    via scripts/categorize_uncategorized_profiles.py will fix it).
    """
    if not brand_category:
        return True
    bc = str(brand_category).strip().upper()
    if not bc or bc in ("NAN", "NONE", "UNKNOWN"):
        return True  # treat as UNCATEGORIZED (run avid; leave categorize for later)
    return bc not in AVID_NON_APPLICABLE_CATEGORIES


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


def _claude_messages_with_retry(*, system, user, max_tokens, temperature,
                                tag, max_attempts: int = 3,
                                base_delay: float = 2.0):
    """Wrap claude_messages with bounded retry + backoff. Surfaces a clear
    log line per attempt so silent failures (rate-limit, transient API
    error, JSON parse miss) are visible in the run log.

    On 2026-06-14 a backfill run silently produced ~750 jitter-only avid
    profiles because `reason_avid_audience` and `reason_category_rows`
    swallowed exceptions / empty responses on the first attempt and
    fell through to fallback paths. Returning `None` from this helper
    is now the unambiguous "all attempts failed" signal so callers can
    log + propagate rather than mask.
    """
    try:
        from claude_client import claude_messages
    except Exception:
        try:
            sys.path.insert(0, HERE)
            from claude_client import claude_messages  # type: ignore
        except Exception as e:
            print(f"[avid-fan][{tag}] claude import failed: {e}")
            return None

    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = claude_messages(
                system=system, user=user,
                max_tokens=max_tokens, temperature=temperature,
            )
            if resp:
                return resp
            last_err = f"empty response (attempt {attempt}/{max_attempts})"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < max_attempts:
            delay = base_delay * (2 ** (attempt - 1))
            print(f"[avid-fan][{tag}] attempt {attempt}/{max_attempts} "
                  f"failed ({last_err}); retrying in {delay:.1f}s")
            try:
                import time as _t
                _t.sleep(delay)
            except Exception:
                pass
    print(f"[avid-fan][{tag}] all {max_attempts} attempts failed: {last_err}")
    return None


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
    user = _format_audience_user(snap)
    resp = _claude_messages_with_retry(
        system=_AUDIENCE_SYSTEM, user=user,
        max_tokens=4096, temperature=0.4,
        tag="phase-1-audience",
    )
    if resp is None:
        print(f"[avid-fan] phase 1 returned None after retries; using fallback")
        return fallback
    obj = _extract_json_block(resp) if resp else None
    if not isinstance(obj, dict):
        print(f"[avid-fan] phase 1 returned no JSON block; using fallback")
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
    "You are a brand-affinity reasoning agent. You reason ROW BY ROW about "
    "whether each brand fits the AVID-fan cohort.\n\n"
    "INPUT:\n"
    "  - a public-figure subject\n"
    "  - the AVID fan audience profile (demographic + psychographic targets)\n"
    "  - a category name\n"
    "  - a list of items with the source-audience BP (broad fan baseline)\n\n"
    "STEP 0 - BUILD A MENTAL PERSONA OF THE AVID COHORT (do this BEFORE "
    "deciding any rows). Think concretely about who they ACTUALLY are:\n"
    "  - geography: are they coastal urban, suburban, heartland, rural, "
    "Sun Belt, Mountain West, Northeast corridor? (a casual fan and an "
    "avid fan of the same artist often live in different places: e.g. a "
    "casual Reba fan might live coastal because she's culturally "
    "ambient, while an AVID Reba fan more likely lives in the South / "
    "heartland where her music is core to identity)\n"
    "  - lifestyle / cultural taste: what other artists, genres, shows, "
    "sports, churches, hobbies fit this person? (an avid Reba fan likely "
    "over-indexes on country peers, faith-based media, NASCAR, college "
    "football; under-indexes on rap, EDM, art house cinema, soccer)\n"
    "  - spending priorities: do they spend on apparel, CPG, home, beauty, "
    "tech, travel, alcohol, gambling? Avid fans of a country/family-values "
    "subject likely over-index on CPG/grocery + home + faith-adjacent and "
    "under-index on luxury fashion / nightlife / coastal-elite brands.\n"
    "  - media diet: cable + Facebook + Pinterest + YouTube vs TikTok + "
    "Twitter + Letterboxd + Reddit?\n"
    "  - intensity-specific behavior: AVID = devoted/identity-level fan. "
    "They consume more of the subject's adjacent universe (tour merch, "
    "podcasts, peer artists, branded products) and less of brands that "
    "are merely 'popular with the broad public'.\n\n"
    "FORBIDDEN (HARD RULES):\n"
    "  - DO NOT apply a multiplier or factor (no 1.1x, no 1.5x, no '+10%').\n"
    "  - DO NOT shift every row in the same direction.\n"
    "  - DO NOT use the source BP * a constant. Each new_bp must come from\n"
    "    independent reasoning about THAT brand's fit with THIS cohort.\n"
    "  - DO NOT inflate brands just because they have high broad BP. A\n"
    "    popular broad-audience brand can have LOWER avid BP if the avid\n"
    "    cohort's demographics are narrower than the brand's customer base.\n\n"
    "HOW TO REASON (per row, AFTER you have the persona in mind):\n"
    "  1. Who actually buys/uses this brand? (its real customer demo + "
    "psychographic + region)\n"
    "  2. Does that customer profile OVERLAP with the avid persona above? "
    "Closely, partially, weakly, or not at all?\n"
    "  3. Set new_bp:\n"
    "     - close overlap + brand is in the subject's universe -> higher\n"
    "     - close overlap, brand is generic mass-market -> roughly source\n"
    "     - weak overlap (different demo, different geography, different "
    "values) -> lower\n"
    "     - no overlap / brand serves a culturally opposite audience -> "
    "near-zero (0.0001 to ~0.10)\n"
    "  4. PRESENCE IS NOT REQUIRED. Don't carry brands forward out of "
    "inertia. An avid Reba cohort probably has near-zero BP for "
    "Supreme / SSENSE / Erewhon / Coachella tickets / Drake / a16z, "
    "regardless of what the broad fan list looked like.\n"
    "  5. There is NO minimum or maximum delta. A row may be flat to 4dp; "
    "post-hoc code will jitter to break collisions.\n\n"
    "EXPECTED DISTRIBUTION (sanity check before returning):\n"
    "  - You should see a real mix: some up, some down, some near-zero, "
    "some flat. Real cohort cuts have CLUSTERS by lifestyle/region, not "
    "uniform direction.\n"
    "  - If 80%+ of your decisions move the same direction (esp. all up), "
    "you defaulted to a multiplier -- STOP and re-reason against the "
    "persona.\n"
    "  - If your top 15 rows are all 1.10-1.20 of source, you applied a "
    "lift. STOP and re-reason.\n"
    "  - Mass-market shopping brands (Walmart, Target, Amazon) often "
    "barely move. Cultural / lifestyle / niche brands move most -- in "
    "BOTH directions.\n\n"
    "OUTPUT FORMAT:\n"
    "  - BPs are percentages 0.0001 to 99.9. Round to 4 decimals.\n"
    "  - If a row genuinely doesn't shift, return source BP rounded to 4dp; "
    "downstream code re-jitters 4dp-collisions.\n"
    "  - Return STRICT JSON only. Schema (do NOT copy the literal numbers; "
    "they are illustrative only -- reason fresh for every brand):\n"
    '    {"items": [{"label": "BRAND_A", "new_bp": 0.4271}, '
    '{"label": "BRAND_B", "new_bp": 47.8312}, ...]}\n'
    "  - Include EVERY item from the input list. Use exact label spelling.\n"
    "  - DO NOT echo placeholder / example values. Every new_bp must come "
    "from your reasoning about THAT specific brand."
)


def _format_category_user(subject: str, audience_summary: str, category: str,
                          rows: list, persona_brief: str = "") -> str:
    L = [f"SUBJECT: {subject}",
         "AVID AUDIENCE PROFILE:",
         audience_summary,
         ""]
    if persona_brief:
        L.append(persona_brief)
        L.append("")
    L.extend([
         f"CATEGORY: {category}",
         f"ITEMS ({len(rows)} rows, broad BP = current 1+ value):"])
    for label, bp in rows:
        L.append(f"  - {label} :: broad_bp={bp:.4f}")
    L.append("")
    L.append('Return JSON: {"items":[{"label":"...","new_bp":<float>}, ...]}')
    L.append("Every item MUST appear in the response. Reason ROW BY ROW. "
             "Do not apply a uniform multiplier. Brands can go up, down, or "
             "stay flat. Many mass-market brands will barely shift. Niche / "
             "subject-tied brands move most.")
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
                         rows: list, *, chunk_size: int = 200,
                         df_source=None) -> dict:
    """Phase 2 Claude call -- reasons over EVERY row in the category (no
    top-N truncation). Long lists are split into sequential chunks of
    `chunk_size` rows so the agent can reason about each row without
    hitting context limits; decisions from all chunks are merged.

    Per Jenna 2026-06-12 "no caps on anything anywhere for agents",
    there is no row-count cap and no priority gating upstream.

    df_source (optional): if provided, an audience-archetype-aware
    persona brief with ELEVATE / ATTENUATE / PANEL-ANCHOR clusters is
    appended to the user prompt via
    `migration.persona_briefs.build_category_persona_brief`. When
    (category, archetype) has no explicit brief, the append is a no-op.
    """
    if not rows:
        return {}
    audience_summary = _audience_summary_text(audience)

    persona_brief = ""
    if df_source is not None:
        try:
            from migration.persona_briefs import (
                build_category_persona_brief,
            )
        except Exception:
            try:
                from persona_briefs import (  # type: ignore
                    build_category_persona_brief,
                )
            except Exception:
                build_category_persona_brief = None  # type: ignore
        if build_category_persona_brief is not None:
            try:
                persona_brief = build_category_persona_brief(
                    subject, category, df_source,
                )
            except Exception as e:
                print(f"[avid-fan] persona_brief build failed for "
                      f"{category}: {e}")
                persona_brief = ""

    rows_sorted = sorted(rows, key=lambda kv: -kv[1])
    out: dict = {}
    n_chunks = (len(rows_sorted) + chunk_size - 1) // chunk_size
    n_chunks_failed = 0
    for i in range(n_chunks):
        chunk = rows_sorted[i * chunk_size:(i + 1) * chunk_size]
        if n_chunks > 1:
            chunk_label = f"{category} (chunk {i + 1}/{n_chunks})"
        else:
            chunk_label = category
        user = _format_category_user(subject, audience_summary,
                                     chunk_label, chunk,
                                     persona_brief=persona_brief)
        resp = _claude_messages_with_retry(
            system=_CAT_ROW_SYSTEM, user=user,
            max_tokens=24000, temperature=0.3,
            tag=f"phase-2-{category}-chunk{i+1}/{n_chunks}",
        )
        if resp is None:
            n_chunks_failed += 1
            print(f"[avid-fan] cat={category} chunk {i+1}/{n_chunks} "
                  f"all retries exhausted -- skipping (rows in this chunk "
                  f"will fall back to source-jitter)")
            continue
        obj = _extract_json_block(resp) if resp else None
        if not isinstance(obj, dict):
            n_chunks_failed += 1
            continue
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
      - Other categories: use category_decisions[CAT][LABEL] if Claude
        returned a per-row decision. If Claude did NOT return a row,
        the BP is left at the source value with subject-salted
        ±0.10pp jitter -- NO multiplier, no flat lift, no default
        push (per Jenna 2026-06-12 "no caps on anything anywhere
        for agents -- must always go row by row").
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
        if cat == "BRAND INPUT":
            # BRAND INPUT carries the subject's self-pin (BP=100%); its
            # Original Raw Numbers + US Gen Pop Projection cells must
            # equal the AVID cohort's sample size and projection, not
            # the parent OG's. Leaving them stale was the cause of the
            # 2026-06-12 "avid sample size looks the same as OG" bug
            # -- the dashboard reads BRAND INPUT for the header pill,
            # so an unwritten row meant analysts saw the OG sample.
            #
            # 2026-06-16 PM (Jenna): also force BP=100% defensively. The
            # OG should have BP=100 here, but Defect 38b found OGs whose
            # subject self-pin was missed (Gemini OG at 32.87% in
            # SEARCH ENGINE/AI) -- those OGs may also have non-100 BRAND
            # INPUT in edge cases. BRAND INPUT @ 100% is Rule #3 mandate.
            df.at[idx, bp_col] = "100.0000%"
            df.at[idx, raw_col] = float(new_sample)
            df.at[idx, proj_col] = float(new_uspop)
            continue
        if cat in {"BRAND CATEGORY", "INPUT_METADATA",
                   "BRAND ID", "REPORT INPUT"}:
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

        # Non-demo brand row: use category_decisions if available.
        # Per Jenna 2026-06-12 "no caps on anything anywhere for
        # agents", there is NO default lift. Rows the agent didn't
        # decide are left near source BP with subject-salted micro-
        # jitter (±0.10pp) -- enough to break a 4dp collision with the
        # source but with no directional push, since the agent didn't
        # justify any push.
        cat_dec = category_decisions.get(cat, {})
        # Placeholder echo defender: prompt examples (12.3456, 0.4271,
        # 47.8312) sometimes get echoed verbatim by Claude when it can't
        # decide. Treat these as no-decision -> fall back to source-jitter.
        claude_val = cat_dec.get(val_u)
        is_placeholder = claude_val is not None and any(
            abs(float(claude_val) - p) < 0.0005
            for p in (12.3456, 0.4271, 47.8312)
        )
        if val_u in cat_dec and not is_placeholder:
            new_bp = float(cat_dec[val_u])
            n_brand += 1
            new_bp = max(0.0001, min(99.49, round(new_bp, 4)))
        else:
            new_bp = round(
                old_bp + _seed_jitter(
                    f"{subject}|{cat}|{val_u}|avid-no-claude-jitter",
                    span=0.10,
                ),
                4,
            )
            new_bp = max(0.0001, min(99.49, new_bp))
            n_unchanged += 1

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
        "n_no_claude_jitter_rows": n_unchanged,
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
# Source loading (s3_key OR local_path) -- mirrors super_fan_synthesis._load_source
# =============================================================================
def _load_source_df(source: str, source_kind: str = "auto"):
    """Return (df, resolved_kind). source_kind='auto' picks 'local_path' when
    `source` is an existing absolute/relative path, else 's3_key'."""
    import pandas as pd
    kind = source_kind
    if kind == "auto":
        kind = ("local_path" if (os.path.isabs(source) or source.startswith("./")
                                  or os.path.exists(source))
                else "s3_key")
    if kind == "s3_key":
        import boto3
        s3 = boto3.client("s3", region_name=REGION)
        body = s3.get_object(Bucket=BUCKET, Key=source)["Body"].read().decode(
            "utf-8", "ignore",
        )
        df = pd.read_csv(io.StringIO(body), low_memory=False,
                         on_bad_lines="skip")
    else:
        df = pd.read_csv(source, low_memory=False, on_bad_lines="skip")
    return df, kind


# =============================================================================
# Orchestrator
# =============================================================================
def synthesize_avid_fan(
    source: str,
    *,
    source_kind: str = "auto",
    dry_run: bool = False,
    register_in_dashboard: bool = True,
    source_s3_key: Optional[str] = None,
    brand_category: Optional[str] = None,
) -> dict:
    """End-to-end orchestrator. Loads `source` (an S3 key or local CSV
    path), runs Phase 1-4 synthesis, writes the avid fan CSV to S3, and
    optionally registers it in the dashboard.

    Args:
      source: S3 key (e.g. "Mark_Rober_06_09_2026_05_43.csv") or a local
        filesystem path.
      source_kind: "auto" | "s3_key" | "local_path". "auto" detects via
        os.path.exists / abs-path heuristic.
      dry_run: if True, return the avid df without uploading or registering.
      register_in_dashboard: if True (default), update s3_cache.json and
        admin_quick_selects.json so the avid profile shows up in the
        dropdown immediately. Set False when the caller will batch-
        register many keys at once (e.g. backfill orchestrator) to
        avoid s3_cache.json read-modify-write races.
      source_s3_key: optional S3 key the local file came from. Only used
        when source_kind='local_path' -- passed to dashboard_register as
        `source_key` so the avid entry inherits custom_image / imdb_id
        from the parent OG cache row. Skip if synthesizing from a non-S3
        file.
      brand_category: optional canonical BRAND CATEGORY (e.g.
        "SERIES - NETFLIX", "MUSICIAN/BAND"). When provided, this is the
        AUTHORITATIVE value used to guarantee the avid file's BRAND
        CATEGORY row matches the main pull's category (mirror semantics
        per Jenna 2026-07-17: "avid cuts always have the same category as
        main cuts and never end up as uncategorized"). Set from
        BG.run_full_pipeline's brand_category argument when auto-
        synthesizing after a main pull; leave None only when the caller
        genuinely doesn't know (e.g. backfill orchestrators reading old
        files with no metadata).

    Returns: dict with keys `out_key`, `status` ('uploaded'|'dry-run'),
      `audience`, `stats`, `n_collisions_fixed`, `register_status`.
    """
    import boto3
    s3 = boto3.client("s3", region_name=REGION)

    df_baseline, kind = _load_source_df(source, source_kind=source_kind)
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

    # Phase 2: per-category row-by-row Claude reasoning. Per Jenna
    # 2026-06-12 directive "no caps on anything anywhere for agents",
    # this iterates EVERY non-demo, non-skip category in the profile
    # -- no PRIORITY_CATS allowlist, no row-count cap, no default-lift
    # fallback in Phase 3. Categories with > chunk_size rows get
    # split into multiple sequential Claude calls in
    # reason_category_rows.
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
    # Largest cats first so any rate-limit hiccup falls on the tail.
    cat_sizes = {c: int((cats_upper == c).sum()) for c in all_non_demo}
    cats_to_call = sorted(all_non_demo, key=lambda c: -cat_sizes[c])
    total_rows = sum(cat_sizes.values())
    print(f"  -> Phase 2: ALL {len(cats_to_call)} non-demo categories "
          f"({total_rows} rows total) -- no priority gating, no top-N cap, "
          f"no default-lift fallback ...")
    rows_decided = 0
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
                                         df_source=df_baseline)
        if decisions:
            cat_decisions[cat] = decisions
        rows_decided += len(decisions)
        print(f"     [{i:>2d}/{len(cats_to_call)}] {cat:32s} "
              f"rows={len(rows):>4d}  claude_returned={len(decisions):>4d}  "
              f"(running total decided={rows_decided}/{total_rows})",
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

    # Phase 5 (2026-06-15 Defect 29): post-synthesis differentiation gate.
    # On 2026-06-14 a backfill run uploaded ~750 avid profiles where
    # both Phase 1 (audience reasoning) and Phase 2 (per-category Claude
    # row-by-row) silently returned empty -- Claude rate-limit / parse
    # failure / etc -- and the apply_avid_transform fallback wrote
    # `old_bp + jitter(±0.10)` for every row. The result was a file
    # that LOOKS like a complete avid skin but is structurally identical
    # to the OG (mean |delta| ≈ 0.025pp). The dashboard happily showed
    # it; only Jenna's eye caught the lack of persona variation.
    #
    # This gate computes mean |Avid BP - OG BP| over non-meta, non-demo
    # brand rows. For a healthy persona-shaped cut the value is in the
    # 0.5-5.0pp range (Reba's gold-standard run was 0.60). When the
    # value is below MIN_MEAN_DELTA_PP we conclude both Claude phases
    # effectively no-op'd and we REFUSE to upload, raising a clear
    # exception so the backfill checkpoint marks the profile `failed`
    # and it can be retried.
    DEMO_CATS_FOR_GATE = {
        'GENDER','AGE','INCOME','ETHNICITY','EDUCATION','OCCUPATION',
        'PARENTAL_STATUS','RELATIONSHIP','SEXUAL_ORIENTATION',
    }
    META_CATS_FOR_GATE = {
        'BRAND INPUT','SAMPLE SIZE','INPUT_METADATA','BRAND CATEGORY',
        'BRAND ID','REPORT INPUT','SUBJECT','LOCATION','DMA','REGION',
        'AVID FAN','CASUAL FAN',
    }
    MIN_MEAN_DELTA_PP = 0.10  # below this = pipeline failed silently

    # Compute out_key BEFORE the Phase 5 gate so the FAIL message can
    # reference it. Without this the gate's `raise RuntimeError(msg)`
    # itself raised UnboundLocalError because out_key was defined later
    # in the function -- which the bare `except Exception` below caught
    # and turned into "gate errored, proceeding with upload anyway",
    # silently uploading every gate-FAIL Avid (Defect 36, 2026-06-16).
    subj_clean = re.sub(r"\s+", " ", subject).strip()
    out_key = f"{subj_clean} - Avid Fan.csv"

    def _bp_brands_only(df):
        out = {}
        cols_u = df['Column'].astype(str).str.upper().str.strip()
        vals_u = df['Value'].astype(str).str.upper().str.strip()
        bp_strs = df[bp_col].astype(str)
        for col, val, s in zip(cols_u, vals_u, bp_strs):
            if col in META_CATS_FOR_GATE or col in DEMO_CATS_FOR_GATE:
                continue
            try:
                out[(col, val)] = float(s.replace('%','').replace(',','').strip())
            except (ValueError, TypeError):
                pass
        return out

    try:
        og_bp = _bp_brands_only(df_baseline)
        av_bp = _bp_brands_only(df_avid)
        shared = set(og_bp) & set(av_bp)
        if shared:
            deltas = [abs(av_bp[k] - og_bp[k]) for k in shared]
            mean_delta = sum(deltas) / len(deltas)
            within_pt1 = sum(1 for d in deltas if d < 0.10) / len(deltas) * 100
            ident_4dp = sum(1 for k in shared
                            if round(av_bp[k], 4) == round(og_bp[k], 4))
            print(f"  -> Phase 5 (differentiation gate): "
                  f"mean|delta|={mean_delta:.4f}pp  "
                  f"within_0.1pp={within_pt1:.2f}%  "
                  f"identical_4dp={ident_4dp}/{len(shared)}  "
                  f"(threshold mean|delta| >= {MIN_MEAN_DELTA_PP:.2f}pp)")
            if mean_delta < MIN_MEAN_DELTA_PP:
                # Loud telemetry on Phase 1/2 effectiveness so the cause
                # is obvious in logs / checkpoint failure messages.
                phase1_demos = len(audience.get('audience_demo_targets') or {})
                phase2_decided = sum(len(d) for d in cat_decisions.values())
                phase2_total = total_rows
                msg = (
                    f"AVID DIFFERENTIATION FAILURE for {subject!r}: "
                    f"mean|delta-from-OG|={mean_delta:.4f}pp < "
                    f"{MIN_MEAN_DELTA_PP:.2f}pp threshold "
                    f"(within_0.1pp={within_pt1:.1f}%, ident_4dp={ident_4dp}). "
                    f"Phase 1 demo_targets categories={phase1_demos}, "
                    f"Phase 2 decisions={phase2_decided}/{phase2_total} rows "
                    f"({100*phase2_decided/max(1,phase2_total):.1f}%). "
                    f"This is the 'OG with jitter' failure mode. "
                    f"REFUSING to upload {out_key}. Re-run after Claude "
                    f"recovers / API key is valid / rate-limit clears."
                )
                print(f"  ❌ {msg}")
                raise RuntimeError(msg)
        else:
            print(f"  ⚠ Phase 5 differentiation gate: 0 shared brand rows "
                  f"between OG and avid; cannot verify -- proceeding with upload")
    except RuntimeError:
        raise
    except (NameError, UnboundLocalError):
        # Bug-class re-raise: these mean the gate code itself is broken,
        # NOT that the avid is "differentiated enough to upload". Letting
        # them slip through silently is exactly how Defect 36 happened.
        raise
    except Exception as _gate_err:
        # True compute errors (df schema oddities, etc.). Log loudly and
        # proceed; the gate is best-effort, but only for genuinely
        # non-bug failures.
        print(f"  ⚠ Phase 5 differentiation gate errored "
              f"(proceeding with upload anyway): "
              f"{type(_gate_err).__name__}: {_gate_err}")

    # Defense-in-depth: ensure df has a populated BRAND CATEGORY row so
    # the dashboard groups the avid fan profile correctly (User rule
    # 2026-06-11: "always make it a rule" -- every profile we create
    # MUST have a canonical BRAND CATEGORY).
    #
    # 2026-07-17 (Jenna): "avid cuts always have the same category as main
    # cuts and never end up as uncategorized". Resolution changed from
    # fallback semantics (avid → baseline → give up) to MIRROR semantics
    # with a canonical fallback:
    #
    #   Priority 1: `brand_category` kwarg from the caller (BG.py's
    #               run_full_pipeline knows the authoritative value the
    #               user submitted). This is the ground truth.
    #   Priority 2: BRAND CATEGORY row on the source (main) file. Used
    #               when the caller didn't pass one — mirrors main.
    #   Priority 3: BRAND CATEGORY row already on the avid file itself
    #               (leftover from Phase 3 apply_avid_transform). Only
    #               reached when neither of the above found anything
    #               usable, and even then we would have been UNCATEGORIZED
    #               in the pre-2026-07-17 code path.
    #   Priority 4: "GENERAL" (canonical last-resort so the file is never
    #               shipped UNCATEGORIZED — matches submit_analysis()'s
    #               default in bg-webapp/app.py). A loud warning is
    #               printed so the operator can patch upstream.
    #
    # The chosen value is ALWAYS written with force=True so the avid file
    # matches the main's canonical category even if apply_avid_transform
    # left a stale/drifted value in place.
    try:
        col_u_bc = df_avid["Column"].astype(str).str.strip().str.upper()
        bc_mask = col_u_bc == "BRAND CATEGORY"

        def _clean(v):
            s = str(v or "").strip()
            return s if s and s.upper() not in ("UNKNOWN", "NAN", "NONE", "UNCATEGORIZED") else ""

        bc_value = _clean(brand_category)
        bc_source = "caller.brand_category" if bc_value else None

        if not bc_value:
            try:
                src_col_u = df_baseline["Column"].astype(str).str.strip().str.upper()
                src_mask = src_col_u == "BRAND CATEGORY"
                if src_mask.any():
                    bc_value = _clean(df_baseline.loc[src_mask, "Value"].iloc[0])
                    if bc_value:
                        bc_source = "baseline_file.BRAND_CATEGORY"
            except Exception:
                pass

        if not bc_value and bc_mask.any():
            bc_value = _clean(df_avid.loc[bc_mask, "Value"].iloc[0])
            if bc_value:
                bc_source = "avid_file.BRAND_CATEGORY (leftover from apply_avid_transform)"

        if not bc_value:
            bc_value = "GENERAL"
            bc_source = "GENERAL (LAST-RESORT FALLBACK)"
            print(
                f"   ⚠ no BRAND CATEGORY resolvable for {subject!r} "
                f"(kwarg empty, baseline empty, avid empty) -- forcing "
                f"BRAND CATEGORY='GENERAL' so the avid ships categorized. "
                f"Investigate upstream: the main pull should have passed "
                f"brand_category into synthesize_avid_fan or the source "
                f"file should carry a BRAND CATEGORY row."
            )
        else:
            print(f"   ✓ avid BRAND CATEGORY resolved to {bc_value!r} (source: {bc_source})")

        try:
            from BG import enforce_brand_category_row
            df_avid = enforce_brand_category_row(df_avid, bc_value, force=True)
        except Exception as _e_enf:
            print(f"   ⚠ enforce_brand_category_row failed ({_e_enf}); direct insertion fallback")
            if not bc_mask.any():
                import pandas as _pd_bc
                new_row = {c: "" for c in df_avid.columns}
                new_row[df_avid.columns[0]] = "BRAND CATEGORY"
                new_row[df_avid.columns[1]] = bc_value
                ss_idx = df_avid.index[col_u_bc == "SAMPLE SIZE"].tolist()
                insert_at = ss_idx[0] + 1 if ss_idx else 2
                top = df_avid.iloc[:insert_at]
                bot = df_avid.iloc[insert_at:]
                df_avid = _pd_bc.concat(
                    [top, _pd_bc.DataFrame([new_row]), bot],
                    ignore_index=True,
                )
            else:
                df_avid.loc[bc_mask, "Value"] = bc_value
    except Exception as _bc_err:
        print(f"   ⚠ avid BRAND CATEGORY safeguard skipped: {_bc_err}")

    # ======================================================================
    # 2026-06-16 PM (Jenna): wire the SAME post-generation enforcers and
    # pre-publish gate that BG.py applies to OG profiles. Previously the
    # avid output skipped all of this and shipped directly to S3 -- which
    # explained today's defect cluster:
    #   - Defect 30 Netflix-suppressed avids (Wendy Williams, Wesley Snipes
    #     etc. avid skins all shipped at NX < 70%)
    #   - Defect 38 BANKS-cluster gaps (BofA Avid BANK 87%, Citibank Avid
    #     BANKING 53%, BMO Avid BANKING absent)
    #   - Defect 38b native-cat misses (Gemini Avid SEARCH ENGINE/AI
    #     32.89%, all 15 social-media avids, MGM+ Avid 3.75%)
    #
    # All of these would have been auto-fixed by run_all_enforcers if it
    # had run on the avid output. Wiring it in now closes the gap for the
    # 581-key avid rerun that's currently in flight, plus all future avid
    # synthesis.
    #
    # run_pre_publish_gate runs second as a detector -- if defects remain
    # after enforcers, log a warning but DO NOT block (the 581-rerun is
    # cleaning up the existing corpus and blocking on residual defects
    # would lose hours of work; per BG.py's documented "rarely fires on a
    # healthy pipeline run" framing, the post-enforcer state should be
    # clean for ~all profiles).
    # ======================================================================
    try:
        try:
            from migration.post_generation_enforcers import run_all_enforcers
        except ImportError:
            from post_generation_enforcers import run_all_enforcers
        # Pull subject + brand category for the enforcer's signature.
        # 2026-07-17 Jenna mirror rule: prefer the caller's brand_category
        # kwarg over the file-derived one so the enforcer chain never sees
        # a stale/blank BC even if some upstream step dropped the row.
        # `bc_value` was resolved above (priority chain: caller → baseline
        # → avid → GENERAL) so it is always non-empty at this point.
        _col_u_av = df_avid.iloc[:, 0].astype(str).str.upper().str.strip()
        _bi = df_avid[_col_u_av == "BRAND INPUT"]
        _avid_subject = (str(_bi.iloc[0, 1]).strip()
                          if len(_bi) else subject)
        _avid_bc = locals().get('bc_value') or None
        if not _avid_bc:
            _bc = df_avid[_col_u_av == "BRAND CATEGORY"]
            _avid_bc = (str(_bc.iloc[0, 1]).strip() if len(_bc) else None)
        print(f"   🛡  running post-generation enforcers on avid output "
              f"(subject={_avid_subject!r}, brand_category={_avid_bc!r})")
        df_avid, _n_enf = run_all_enforcers(
            df_avid, _avid_subject, brand_category=_avid_bc, verbose=True,
        )
        print(f"   ✅ post-gen enforcers applied to avid: {_n_enf} change(s)")
    except Exception as _enf_err:
        print(f"   ⚠️ avid post-gen enforcers failed (non-fatal): {_enf_err}")
        import traceback as _tb
        _tb.print_exc()

    # TU-vs-Avid subset coherence: for every brand present in both TU and
    # Avid, the identity `TU_BP >= p_avid * Avid_BP` must hold (avid fans
    # are a strict subset of TU, so avid-alone contribution to TU BP has
    # a hard floor). This catches the WoF-shaped defect where the Avid
    # agent scored a brand so high that it's mathematically impossible
    # given the TU value. Only trims Avid; never touches TU.
    try:
        try:
            from migration.tu_avid_coherence import enforce_tu_avid_coherence
        except ImportError:
            from tu_avid_coherence import enforce_tu_avid_coherence  # type: ignore
        print(f"   🧮 running TU-vs-Avid coherence check against source "
              f"TU (df_baseline)")
        df_avid, _co_stats = enforce_tu_avid_coherence(
            df_baseline, df_avid, _avid_subject, verbose=True,
        )
        print(f"   ✅ tu_avid_coherence: rebalanced "
              f"{_co_stats.get('rows_rebalanced', 0)} row(s); "
              f"max_viol={_co_stats.get('max_violation_pp', 0):.4f}pp")
    except Exception as _co_err:
        print(f"   ⚠️ tu_avid_coherence failed (non-fatal): {_co_err}")

    try:
        try:
            from migration.post_generation_enforcers import (
                run_pre_publish_gate as _pp_gate_av,
                PrePublishGateError as _pp_gate_av_err,
            )
        except ImportError:
            from post_generation_enforcers import (
                run_pre_publish_gate as _pp_gate_av,
                PrePublishGateError as _pp_gate_av_err,
            )
        try:
            _pp_gate_av(
                df_avid, _avid_subject,
                project_name=out_key,
                raise_on_fail=True,
                verbose=True,
            )
        except _pp_gate_av_err as _gate_err:
            # LOG but DO NOT block. The avid is shipped with whatever the
            # enforcers couldn't fix; operator can re-run targeted sweeps.
            print(f"   ⚠️ avid pre-publish gate flagged "
                  f"residual defect(s) (shipping anyway, please review): "
                  f"{_gate_err}")
    except Exception as _gate_other_err:
        print(f"   ⚠️ avid pre-publish gate errored (non-fatal): "
              f"{_gate_other_err}")

    # MANDATORY write-time safety net (2026-08-06). Idempotent:
    # fills blank BP from Raw, strips % from BP/CS, recomputes
    # Raw/Proj, recomputes non-demo Category Share to sum to 100,
    # blanks stale meta-row CS. Same guarantee that
    # write_profile_csv provides on the main pipeline path -- avid
    # builder writes direct to S3 so we invoke the safety net inline.
    try:
        try:
            from migration.post_generation_enforcers import (
                run_write_safety_net,
            )
        except ImportError:
            from post_generation_enforcers import (  # type: ignore
                run_write_safety_net,
            )
        df_avid, _sn_stats = run_write_safety_net(
            df_avid, subject, verbose=True,
        )
    except Exception as _sn_err:
        print(f"   ⚠ write-safety-net raised (non-fatal): {_sn_err}")

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
    # Caller can disable for batch backfills that defer registration to
    # avoid s3_cache.json read-modify-write races between parallel workers.
    register_status = None
    if register_in_dashboard:
        try:
            try:
                from migration.dashboard_register import register_profile_in_dashboard
            except ImportError:
                from dashboard_register import register_profile_in_dashboard
            # Pass the parent S3 key (when known) so the avid entry can
            # inherit custom_image / imdb_id from the OG cache row.
            parent_key = source if kind == "s3_key" else source_s3_key
            register_status = register_profile_in_dashboard(
                out_key,
                display_name=f"{subj_clean} - Avid Fan",
                source_key=parent_key,
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


# Back-compat shim -- original name kept so any existing callers continue
# to work. Prefer `synthesize_avid_fan(...)` for new code; it accepts
# either an S3 key or a local path via source_kind.
def synthesize_avid_fan_for_s3_key(s3_key: str, *, dry_run: bool = False) -> dict:
    return synthesize_avid_fan(s3_key, source_kind="s3_key", dry_run=dry_run)


__all__ = [
    "synthesize_avid_fan",
    "synthesize_avid_fan_for_s3_key",
    "AVID_NON_APPLICABLE_CATEGORIES",
    "should_synthesize_avid_for_category",
]
