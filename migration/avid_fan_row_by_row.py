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
import math
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

# --- helpers reused from super_fan_synthesis ---------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
_ROOT = os.path.dirname(HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from super_fan_synthesis import (  # noqa: E402
    build_source_snapshot,
    subject_is_seed_marker,
    _extract_json_block,
    _norm_subject_for_filename,
    DEMO_CATS_TF,
    META_CATS_TF,
    SUBJECT_PIN_CATS_TF,
)

try:
    from scripts._sample_size_jitter import ensure_messy_sample_size
except Exception:  # pragma: no cover - scripts/ not on path in odd envs
    def ensure_messy_sample_size(subj, v, **kw):
        v = int(v or 0) or 9873
        return v + 7 if v % 10 == 0 else v

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


_COHORT_SUFFIX_RE = re.compile(
    r"\s*-\s*(avid|casual|super)?\s*"
    r"(fan|fans|female|male|total universe|tu)"
    r"(\s+(fan|fans|female|male))?\s*$",
    re.IGNORECASE,
)


def _norm_pin(s) -> str:
    """Case + punctuation insensitive normalizer for self-pin matching."""
    return re.sub(r"[^A-Z0-9]+", "", str(s or "").upper())


def _subject_pin_aliases(subject: str) -> set:
    """Normalized aliases that count as the subject for self-pin purposes.

    2026-08-24 (Dylan Minnette / Erin Brooks avids, 99.9895 defect):
    when the collision walk runs with a cohort-label subject
    ('Dylan Minnette - Avid Fan'), the row carrying the BARE parent
    name ('Dylan Minnette') at 100 failed the `vu == subj_u` exemption
    and got jittered off its self-pin to exactly 99.9895
    (100 - 15*0.0007). The bare parent name in a subject-pin category
    is the same entity and must stay pinned. Aliases: the subject as
    passed, the name before ' - <cohort suffix>', and the
    suffix-stripped form, all case/punctuation-insensitive.
    """
    subj = str(subject or "").strip()
    aliases = {_norm_pin(subj)}
    bases = {subj}
    if " - " in subj:
        head = subj.split(" - ")[0].strip()
        aliases.add(_norm_pin(head))
        bases.add(head)
    stripped = _COHORT_SUFFIX_RE.sub("", subj).strip()
    if stripped:
        aliases.add(_norm_pin(stripped))
        bases.add(stripped)
    # 2026-08-24 (Furious audit D5/D6): deliverable labels carry an
    # audience noun the clean entity name lacks ('Furious Viewers' ~
    # series 'Furious'). Strip one trailing noun per base so the clean
    # subject row stays exempt when the caller passes the label.
    _nouns = ("viewers", "watchers", "listeners", "readers", "players",
              "fans", "fan", "audience", "moviegoers")
    for cand in list(bases):
        toks = str(cand or "").strip().split()
        if len(toks) >= 2 and toks[-1].lower() in _nouns:
            aliases.add(_norm_pin(" ".join(toks[:-1])))
    aliases.discard("")
    return aliases


# =============================================================================
# Deterministic cohort fraction (Jenna 2026-08-24)
# =============================================================================
def deterministic_avid_fraction(df_source) -> Optional[float]:
    """Avid cohort fraction read straight off the parent TU's own rows.

    Per Jenna 2026-08-24 (verbatim): "make sure the cut sasmple sizes
    match the total universe and update pipeline to ensure that."
    Rule (avid-and-cut-skin-rules.mdc section 3): the avid skin's
    sample = parent_TU_sample x (parent AVID FAN BP / 100). The
    fraction is MATH read off the parent file at synthesis time, never
    an agent estimate.

    Returns the fraction, or None when the parent has no AVID FAN row -
    in that case the caller keeps the Phase 1 reasoned fraction and
    logs it (TUs built before 2026-08-24 have no row until their next
    refresh; no mass backfill per the standing posture).

    RE-ENABLED 2026-08-24 (reasoned era). Timeline same day: the
    provenance audit found every corpus AVID FAN row was generated by
    the retired BG.py hash block ("ULTRA-FAST BRAND AWARENESS":
    hash(subject) tier bands, clamped 5-35, no PYTHONHASHSEED) - pure
    noise. All 29 hash-era rows were stripped from S3 and this read
    was disabled. Jenna then approved the reasoned-era reversal
    ("Go with what you recommend"): TUs emit a per-subject reasoned
    AVID FAN row (migration/avid_share_reasoner.py, wired into
    scripts/synth_engine_row_by_row.py + BG.py), so any row present on
    a current file is reasoned-era and safe to anchor on.

    Sanity guard (defense in depth): a row whose BP is non-numeric or
    outside [2.0, 60.0] is REJECTED with a loud log and the function
    returns None - the reasoner's own output contract is [3.0, 55.0],
    so anything outside the wider window is corrupt or foreign data,
    never a legitimate anchor.
    """
    try:
        bp_col = next(
            (c for c in df_source.columns if "Brand Penetration" in c),
            None)
        if not bp_col:
            return None
        cats = df_source["Column"].astype(str).str.upper().str.strip()
        mask = cats == "AVID FAN"
        if not mask.any():
            return None
        cell = df_source.loc[mask, bp_col].iloc[0]
        v = _fbp(cell)
        if v is None:
            print(f"   \u26A0 deterministic_avid_fraction: AVID FAN row "
                  f"present but BP cell is non-numeric ({cell!r}) - "
                  f"REJECTED, falling back to reasoned fraction")
            return None
        if not (2.0 <= v <= 60.0):
            print(f"   \u26A0 deterministic_avid_fraction: AVID FAN row "
                  f"BP {v:.4f} outside sanity window [2.0, 60.0] - "
                  f"REJECTED, falling back to reasoned fraction")
            return None
        return v / 100.0
    except Exception:
        return None


def override_with_deterministic_fraction(audience: dict, det_cf: float,
                                         *, note: str = "") -> dict:
    """Force `audience['cohort_fraction']` to the deterministic value.

    The deterministic fraction ALWAYS WINS over any reasoned or
    spec-provided cohort_fraction (Jenna 2026-08-24). us_pop_fraction
    is rescaled proportionally so the projection chain stays coherent.
    Mutates and returns `audience`; records the losing value under
    `claude_cf_was` for the run log.
    """
    old_cf = float(audience.get("cohort_fraction") or 0.0)
    old_uf = float(audience.get("us_pop_fraction") or 0.0)
    audience["cohort_fraction"] = float(det_cf)
    if old_cf > 0 and old_uf > 0:
        audience["us_pop_fraction"] = max(
            0.0005, min(0.95, old_uf * float(det_cf) / old_cf))
    audience["deterministic_cf"] = True
    audience["claude_cf_was"] = old_cf
    if note:
        audience["deterministic_cf_note"] = note
    return audience


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
                                base_delay: float = 2.0,
                                api_key: str = None):
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
            if api_key:
                try:
                    from cut_parallel import cut_claude_call as _ccc
                except Exception:
                    from migration.cut_parallel import (
                        cut_claude_call as _ccc,
                    )
                resp = _ccc(system=system, user=user, api_key=api_key,
                            max_tokens=max_tokens,
                            temperature=temperature)
            else:
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
                         df_source=None,
                         api_key: str = None) -> dict:
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
            api_key=api_key,
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

    # At-birth ladder guard (2026-08-26 Liz QA, Bethenny avid): within
    # one category batch the model sometimes reuses a single fractional
    # part across many rows, stepping only the integer (67.8912 /
    # 55.8912 / ... / 3.8912). Re-salt those suffixes per (subject,
    # category, label) BEFORE the decisions land in the frame; integer
    # parts (the model's magnitude calls) are preserved.
    try:
        try:
            from migration.fractional_ladders import deladder_decision_map
        except ImportError:
            from fractional_ladders import deladder_decision_map  # type: ignore
        category_decisions, _n_deladder = deladder_decision_map(
            category_decisions, subject)
    except Exception as _dl_err:
        print(f"    [deladder] guard skipped ({_dl_err})")

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
    # Workspace rule (no-round-sample-sizes): the parent x fraction
    # product must never ship with a trailing zero. The jitter breaks
    # trailing digits only; the cohort fraction holds within it.
    new_sample = ensure_messy_sample_size(f"{subject}|avid", new_sample)

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
            #
            # PERSONA CARVE-OUT (Jenna 2026-08-24, verbatim: "brand
            # inputs dont need to be 100% on persona style profiles
            # just elevated"): when the SOURCE file deliberately carries
            # an elevated-but-not-100 BRAND INPUT (persona/interest
            # universes whose BI is a screening-brand/scrape-term list,
            # not a self-slug), the cut inherits that elevated value
            # instead of re-pinning to 100. Only clearly-elevated values
            # qualify (>= 90); a deep miss (< 90) is still the Defect
            # 38b signature and gets the 100 pin.
            _bi_bp = _fbp(df.at[idx, bp_col])
            if _bi_bp is not None and 90.0 <= _bi_bp < 99.995:
                df.at[idx, bp_col] = f"{_bi_bp:.4f}%"
            else:
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

        # Subject self-pin: keep at 100% per Rule #3. Alias-robust
        # (2026-08-24): also matches the bare parent name when the
        # subject arrives as a cohort label, case/punct-insensitive.
        if cat in SUBJECT_PIN_CATS_TF or cat == "SUBJECT":
            if (abs(old_bp - 100.0) < 0.01
                    and (val_u == subject.upper()
                         or _norm_pin(val_u)
                         in _subject_pin_aliases(subject))):
                df.at[idx, raw_col] = float(new_sample)
                df.at[idx, proj_col] = float(new_uspop)
                n_pin += 1
                continue

        # Universal 100-pin keep (2026-08-24 Furious audit D6a): ANY
        # non-demo row the parent holds at exactly 100 is a pin by
        # construction - the subject self-pin in a native grid outside
        # SUBJECT_PIN_CATS_TF (SERIES 'Furious'), a viewers-scope
        # platform pin (STREAMING/PLATFORM 'Disney+/Hulu'), companion
        # pins. The cut inherits the pin untouched; only Raw/Proj
        # rescale to the cohort. Without this the row fell through to
        # the brand path and got jittered off 100 (the shared 99.9895
        # artifact) or clamped to 99.49.
        if cat not in DEMO_CATS_TF and old_bp >= 99.995:
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
            # Plausibility guard (2026-08-24, Lincoln 98.49 / Air Fryer
            # avid 99.99 defect): an avid lift claiming near-universal
            # reach for a row whose PARENT BP is tiny is a reasoning
            # misfire, not a signal. Avid lifts are moderate by nature;
            # 4.9 -> 76 never happens for real. Fall back to the
            # source-BP jitter path instead of shipping the misfire.
            if ((new_bp >= 75.0 and old_bp < 5.0)
                    or (new_bp >= 60.0 and old_bp < 1.0)):
                print(f"    [avid-guard] {cat} / {val_u}: claude "
                      f"bp={new_bp:.4f} vs parent {old_bp:.4f} - "
                      f"misfire, keeping parent+jitter")
                new_bp = round(
                    old_bp + _seed_jitter(
                        f"{subject}|{cat}|{val_u}|avid-misfire-jitter",
                        span=0.10,
                    ),
                    4,
                )
                new_bp = max(0.0001, min(99.49, new_bp))
                n_unchanged += 1
            else:
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

    # LOCATION renormalize-to-~100 (2026-08-24 defect E: cut transforms
    # jittered LOCATION rows per-row via the no-decision fallback without
    # ever renormalizing the category, so shipped cuts drifted off the
    # ~100 sum LOCATION carries by construction; SharkNinja Avid summed
    # 110.15). Shape-preserving: every row scales by the same factor,
    # then gets a subject-salted micro-jitter so no row lands on a
    # round 2dp/4dp boundary; the jitter is zero-sum-ish (span 0.02pp)
    # so the post-renorm sum stays within a few hundredths of 100.
    loc_mask = (df[cat_col].astype(str).str.upper().str.strip()
                == "LOCATION")
    if loc_mask.any():
        loc_rows = []
        for idx in df.index[loc_mask]:
            v = _fbp(df.at[idx, bp_col])
            if v is not None and v > 0:
                loc_rows.append((idx, v))
        loc_sum = sum(v for _, v in loc_rows)
        if loc_rows and loc_sum > 0 and abs(loc_sum - 100.0) > 0.05:
            for idx, v in loc_rows:
                label = str(df.at[idx, val_col]).strip()
                normed = v * 100.0 / loc_sum + _seed_jitter(
                    f"{subject}|LOCATION|{label}|avid-loc-renorm",
                    span=0.02,
                )
                normed = round(max(0.0001, normed), 4)
                df.at[idx, bp_col] = f"{normed:.4f}%"
                df.at[idx, raw_col] = float(
                    round(new_sample * normed / 100.0))
                df.at[idx, proj_col] = float(
                    round(new_uspop * normed / 100.0))

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
    # 2026-08-24 self-pin exemption fix: the exemption previously only
    # matched the exact subject string passed in. Cut paths pass the
    # cohort label ('X - Avid Fan') while the pinned row carries the
    # BARE parent name ('X'), so the parent self-pin got jittered off
    # 100 to 99.9895 on every avid/cut. Exempt every subject alias
    # (bare parent included), case/punctuation-insensitive, in
    # subject-pin categories.
    pin_aliases = _subject_pin_aliases(subject)
    for idx in df_avid.index:
        cu = str(df_avid.at[idx, cat_col]).strip().upper()
        # Metadata anchor rows are definitionally allowed to match the
        # parent and must NEVER be jittered: BRAND INPUT and SAMPLE
        # SIZE share the same Column+Value key as the parent's rows
        # and both sit at BP=100, so without this skip they collide
        # by construction and the walk knocks the anchors off 100
        # (2026-08-22: GOOGLE PLAY - TVOD Renters SAMPLE SIZE raw
        # diverged 33,743 -> 33,739; three avid/cut files shipped
        # 99.9895 anchors the same way).
        if cu in ("BRAND INPUT", "SAMPLE SIZE", "BRAND CATEGORY",
                  "SUBJECT"):
            continue
        vu = str(df_avid.at[idx, val_col]).strip().upper()
        avid_bp = _fbp(df_avid.at[idx, bp_col])
        if avid_bp is None:
            continue
        avid4 = round(avid_bp, 4)
        base4 = base_idx.get((cu, vu))
        if base4 is None or avid4 != base4:
            continue
        # Allowed exception: ANY row at exactly 100 on BOTH sides is an
        # intentional pin (subject self-pin in any grid - SERIES
        # 'Furious' included - viewers-scope platform pins, companion
        # pins) and stays. 2026-08-24 Furious audit D6a: the old gate
        # (subject string / alias + SUBJECT_PIN_CATS_TF only) missed
        # the SERIES self-pin because SERIES isn't in the pin-cats set,
        # and walked it to the shared 99.9895 artifact. A 100=100
        # collision can only be a pin by construction (nothing else
        # survives the depin/ceiling passes at exactly 100).
        if abs(avid4 - 100.0) < 0.0001:
            continue
        # (alias exemption retained for sub-100 self-pins: a subject row
        # both files hold at the same eroded value is re-pinned later by
        # the polish, not jittered here into a fake separation)
        if (_norm_pin(vu) in pin_aliases
                and (cu in SUBJECT_PIN_CATS_TF or cu == "SUBJECT"
                     or vu == subj_u)):
            continue
        # Jitter to break the collision. FORMAT-PRESERVING (2026-08-24
        # Furious audit D6b): only write the '%' suffix when the cell
        # already carried one - this pass runs both before AND after
        # the format normalizers depending on the caller, and stamping
        # '%' after normalize_final_format shipped literal '99.9895%'
        # string cells.
        _had_pct = str(df_avid.at[idx, bp_col]).strip().endswith("%")
        for k in range(1, 80):
            cand = round(
                avid_bp + (1 if (k % 2) else -1) * (0.0007 * (k // 2 + 1)),
                4,
            )
            if 0.0005 < cand < 99.99 and cand != base4:
                df_avid.at[idx, bp_col] = (
                    f"{cand:.4f}%" if _had_pct else f"{cand:.4f}")
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
# Phase 4b: subset-coherence pass vs parent (2026-08-24)
# =============================================================================
_SUBSET_COHERENCE_SKIP_CATS = frozenset({
    "BRAND INPUT", "SAMPLE SIZE", "BRAND CATEGORY", "SUBJECT",
    "INPUT_METADATA", "INPUT METADATA", "BRAND ID", "REPORT INPUT",
    "AVID FAN", "CASUAL FAN",
})

# Geo categories participate in the UPWARD raw cap only (2026-08-28:
# a cut can never count more panelists in a DMA than its parent does -
# the Primetime Movie avid shipped Spokane Wa at 15 raw vs parent 13).
# They stay exempt from the downward lift and own-row direction passes:
# geo shares are a composition read, not an intensity read, and the
# post-transform LOCATION renormalization already sets their level.
_GEO_SUBSET_CATS = frozenset({"LOCATION", "DMA", "REGION"})

# Mass-digital-behavior categories where an engaged (avid) slice cannot
# plausibly sit far BELOW the broad audience: intensity selects for
# MORE digital behavior, not less. Talent / politics / persona
# categories are deliberately excluded - a big downward move there can
# be a legitimate persona-shaped read.
_MEGA_REACH_CATS = frozenset({
    "SOCIAL MEDIA", "SEARCH ENGINE/AI", "SEARCH ENGINE",
    "STREAMING/PLATFORM", "STREAMING VIDEO", "STREAMING MUSIC",
    "APP/PLATFORM", "APP/PLATFORM USAGE", "TECHNOLOGY/DEVICE",
    "MOST VISITED WEBSITES",
})


def _read_sample_size(df, raw_col):
    """SAMPLE SIZE Raw, falling back to BRAND INPUT Raw. None if absent."""
    cats_u = df["Column"].astype(str).str.upper().str.strip()
    for anchor in ("SAMPLE SIZE", "BRAND INPUT"):
        m = cats_u == anchor
        if m.any():
            try:
                v = float(str(df.loc[m].iloc[0][raw_col]).replace(",", ""))
                if v > 0:
                    return v
            except Exception:
                continue
    return None


def enforce_avid_subset_coherence(df_avid, df_parent, subject: str,
                                  *, verbose: bool = True,
                                  down_gap_pp: float = 9.0,
                                  down_parent_min_bp: float = 60.0):
    """Both-direction subset coherence between an intensity cut and its
    parent (2026-08-24, Dylan Minnette / Erin Brooks audits).

    The avid (or any reduced-intensity) cohort is a strict subset of
    the parent audience, so for every shared (category, brand) pair:

      UPWARD CAP: avid_raw must not exceed parent_raw, verified at
        the Raw level (round(bp/100 x sample) on both sides) on EVERY
        shared non-exempt row, full range, including parent rows at
        0.0000 BP (2026-08-25 partner finding: Bethenny avid carried
        Real Housewives of New York at 3.4x the parent's panelist
        count; the pre-fix pass skipped parent_bp <= 0 rows and could
        nudge a capped value back across the ceiling). Violators are
        re-derived as a plausible engaged-tier read anchored to the
        parent BP: a subject+brand-salted fraction of the way from
        the parent BP up to the subset ceiling, floored to 4dp (never
        rounded up across the ceiling), walked DOWN off 2dp
        boundaries and parent 4dp collisions, raw-verified. Brands
        mirrored across categories at the same parent BP land on the
        same new value (seed is brand-normed, category-free) so the
        MPB mirror survives.

      DOWNWARD LIFT: on mass-digital-behavior categories
        (_MEGA_REACH_CATS) where parent_bp > `down_parent_min_bp` and
        the avid sits more than `down_gap_pp` below the parent, the
        avid is lifted to parent minus a salted small gap. An engaged
        slice indexing far below the broad audience on YouTube-class
        reach rows is the inverse failure (Erin's avid YouTube 64.7 vs
        parent 77.6). Talent/persona categories are exempt so
        legitimate persona reasoning survives.

    Subject self-pin rows (any alias, see _subject_pin_aliases) are
    exempt. All outputs 4dp, never on a .XX00 2dp boundary, never
    colliding 4dp with the parent value. Raw/Proj recomputed from the
    avid's own sample. Returns (df_avid, stats_dict).
    """
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df_avid)
    p_bp_col, _, p_raw_col, _ = _detect_cols(df_parent)
    stats = {"capped_up": 0, "lifted_down": 0, "direction_lifted": 0,
             "skipped": 0, "examples_up": [], "examples_down": [],
             "examples_direction": []}
    if bp_col is None or p_bp_col is None:
        return df_avid, stats

    # 2026-08-26 (Liz QA, Paw Patrol avid; corrected same day by
    # Jenna's convention): OWN-ROW DIRECTION on the subject's own
    # NON-PIN rows (own merch grids). Rows covered by the
    # own-property / owner-platform pin convention (must_pin_100:
    # FRANCHISE own row, owning platform) are EXCLUDED here - they
    # pin at exactly 100 in base and cuts via pin_own_property_rows,
    # so no direction logic applies to them. For the remaining own
    # rows (TOYS/GAMES/MPB own merch) the avid tier must read AT OR
    # ABOVE the parent; violators are re-derived from the parent BP
    # with a subject-salted engaged-tier premium, raw-verified so the
    # subset invariant (avid_raw <= parent_raw) still holds.
    try:
        try:
            from migration.self_property_coherence import (
                is_subject_own as _spc_is_own,
                must_pin_100 as _spc_must_pin,
            )
        except ImportError:
            from self_property_coherence import (  # type: ignore
                is_subject_own as _spc_is_own,
                must_pin_100 as _spc_must_pin,
            )
    except Exception:
        _spc_is_own = None
        _spc_must_pin = None

    avid_sample = _read_sample_size(df_avid, raw_col)
    parent_sample = _read_sample_size(df_parent, p_raw_col)
    if not avid_sample or not parent_sample:
        if verbose:
            print("   [subset-coherence] sample size unreadable on one "
                  "side; skipping (no-op)")
        return df_avid, stats
    ratio = avid_sample / parent_sample
    if not (0.0 < ratio < 1.0):
        if verbose:
            print(f"   [subset-coherence] avid/parent sample ratio "
                  f"{ratio:.4f} not in (0,1); not a strict subset - "
                  f"skipping (no-op)")
        return df_avid, stats

    # Parent (CAT, VAL) -> bp
    parent_idx = {}
    for _, r in df_parent.iterrows():
        cu = str(r.get("Column", "")).strip().upper()
        vu = str(r.get("Value", "")).strip().upper()
        bp = _fbp(r.get(p_bp_col))
        if bp is None or not cu or not vu:
            continue
        parent_idx[(cu, vu)] = bp

    df_avid = df_avid.copy()
    for c in (bp_col, cs_col, raw_col, proj_col):
        if c in df_avid.columns and df_avid[c].dtype.name not in ("object", "O"):
            df_avid[c] = df_avid[c].astype(object)

    try:
        ss_mask = (df_avid["Column"].astype(str).str.upper().str.strip()
                   == "SAMPLE SIZE")
        uspop = float(str(df_avid.loc[ss_mask].iloc[0][proj_col])
                      .replace(",", "")) if ss_mask.any() else None
    except Exception:
        uspop = None

    pin_aliases = _subject_pin_aliases(subject)

    def _finalize(new_bp, parent_bp, seed):
        """4dp, off 2dp boundaries, not 4dp-colliding with parent."""
        new_bp = max(0.0001, min(99.49, round(new_bp, 4)))
        p4 = round(parent_bp, 4)
        for k in range(6):
            n4 = round(new_bp, 4)
            on_boundary = abs(n4 - round(n4, 2)) < 0.00005
            if n4 != p4 and not on_boundary:
                return n4
            new_bp = n4 + 0.0007 + abs(_seed_jitter(f"{seed}|nudge{k}",
                                                    span=0.0014))
        return round(new_bp, 4)

    def _cap_below_parent_raw(parent_bp, parent_raw, seed):
        """Re-derive a violating avid BP as an engaged-tier read
        anchored to the parent BP: a salted fraction of the way from
        the parent BP up to the subset ceiling, floored to 4dp so
        rounding can never re-cross the ceiling, then walked DOWNWARD
        off 2dp boundaries / parent 4dp collisions, with the actual
        Raw comparison (round(avid_sample x bp / 100) <= parent_raw)
        verified at every step. parent_raw == 0 lands the avid where
        its own Raw rounds to 0 too."""
        u = int(hashlib.md5(f"{seed}|frac".encode()).hexdigest()[:8],
                16) / 0xFFFFFFFF
        # Largest BP whose Raw still rounds to <= parent_raw.
        feas = (parent_raw + 0.499) * 100.0 / avid_sample
        if parent_raw <= 0:
            target = feas * (0.25 + 0.50 * u)
        else:
            hi = min(parent_bp / ratio, feas, 99.49)
            lo = min(parent_bp, hi)
            span = max(hi - lo, 0.0)
            target = min(lo + (0.30 + 0.55 * u) * span,
                         hi - max(0.0003, 0.02 * span))
        target = max(target, 0.0001)
        bp = math.floor(target * 10000.0) / 10000.0
        p4 = round(parent_bp, 4)
        step = 0.0007 + abs(_seed_jitter(f"{seed}|dstep", span=0.0014))
        for _ in range(4000):
            n4 = round(bp, 4)
            if n4 <= 0.0001:
                # 4dp floor. If even 0.0001 overshoots the parent count
                # (large avid sample vs parent raw of 0-1), the avid
                # tier has no measurable panelists for this brand: 0.
                if round(avid_sample * 0.0001 / 100.0) > parent_raw:
                    return 0.0
                return 0.0001
            raw_ok = round(avid_sample * n4 / 100.0) <= parent_raw
            on_boundary = abs(n4 - round(n4, 2)) < 0.00005
            if raw_ok and not on_boundary and n4 != p4:
                return n4
            bp = (min(n4, feas) if not raw_ok else n4) - step
        return round(max(bp, 0.0001), 4)

    for idx in df_avid.index:
        cu = str(df_avid.at[idx, "Column"]).strip().upper()
        if cu in _SUBSET_COHERENCE_SKIP_CATS or cu in DEMO_CATS_TF:
            continue
        vu = str(df_avid.at[idx, "Value"]).strip().upper()
        if not vu:
            continue
        avid_bp = _fbp(df_avid.at[idx, bp_col])
        if avid_bp is None:
            continue
        # Self-pin exemption (any alias)
        if avid_bp >= 99.49 and _norm_pin(vu) in pin_aliases:
            continue
        parent_bp = parent_idx.get((cu, vu))
        if parent_bp is None:
            continue
        if parent_bp < 0:
            # Corrupt parent row (negative BP): raw counts floor at 0,
            # so the avid must land at 0 panelists too.
            parent_bp = 0.0

        had_pct = "%" in str(df_avid.at[idx, bp_col])
        new_bp = None
        parent_raw = round(parent_sample * parent_bp / 100.0)
        avid_raw = round(avid_sample * avid_bp / 100.0)
        max_bp = (parent_bp / ratio) if parent_bp > 0 else 0.0

        if avid_raw > parent_raw:
            # UPWARD violation: avid Raw exceeds parent Raw. Full
            # range (any parent BP, including 0), raw-level verified.
            new_bp = _cap_below_parent_raw(
                parent_bp, parent_raw,
                f"{subject}|{_norm_pin(vu)}|subset-cap")
            if new_bp is not None and new_bp < avid_bp:
                stats["capped_up"] += 1
                if len(stats["examples_up"]) < 5:
                    stats["examples_up"].append(
                        f"{cu}/{vu}: {avid_bp:.4f} -> {new_bp:.4f} "
                        f"(parent {parent_bp:.4f}, ceil {max_bp:.4f})"
                    )
            else:
                new_bp = None
        elif cu in _GEO_SUBSET_CATS:
            # Geo rows: upward cap only (handled above). Never lift or
            # re-derive a geo share downward-direction - composition,
            # not intensity.
            continue
        elif (_spc_is_own is not None and avid_bp < parent_bp - 0.0005
                and parent_bp < 99.2 and _spc_is_own(subject, vu)
                and not (_spc_must_pin is not None
                         and _spc_must_pin(subject, cu, vu))):
            # OWN-ROW DIRECTION violation: avid below parent on the
            # subject's own NON-PIN row (own merch). Pin-convention
            # rows are excluded (they go to exactly 100 via
            # pin_own_property_rows). Re-derive from the parent BP
            # with a salted engaged-tier premium, raw-verified.
            u = int(hashlib.md5(
                f"{subject}|{cu}|{_norm_pin(vu)}|own-direction".encode()
            ).hexdigest()[:8], 16) / 0xFFFFFFFF
            prem = 0.015 + 0.060 * u
            feas = (parent_raw + 0.499) * 100.0 / avid_sample
            target = min(parent_bp * (1.0 + prem), feas * 0.999, 99.2)
            if target > avid_bp:
                new_bp = _finalize(target, parent_bp,
                                   f"{subject}|{cu}|{vu}|own-direction")
                if new_bp > avid_bp and round(
                        avid_sample * new_bp / 100.0) <= parent_raw:
                    stats["direction_lifted"] += 1
                    if len(stats["examples_direction"]) < 5:
                        stats["examples_direction"].append(
                            f"{cu}/{vu}: {avid_bp:.4f} -> {new_bp:.4f} "
                            f"(parent {parent_bp:.4f}, own-row "
                            f"direction)"
                        )
                else:
                    new_bp = None
        elif (cu in _MEGA_REACH_CATS
                and parent_bp > down_parent_min_bp
                and (parent_bp - avid_bp) > down_gap_pp):
            # DOWNWARD violation: engaged slice far below broad
            # audience on a mass-reach digital row.
            gap = 2.5 + abs(_seed_jitter(
                f"{subject}|{cu}|{vu}|subset-lift-gap", span=8.0,
            ))
            target = min(parent_bp - gap, max_bp * 0.995, 99.49)
            if target > avid_bp:
                new_bp = _finalize(target, parent_bp,
                                   f"{subject}|{cu}|{vu}|subset-lift")
                if new_bp > avid_bp:
                    stats["lifted_down"] += 1
                    if len(stats["examples_down"]) < 5:
                        stats["examples_down"].append(
                            f"{cu}/{vu}: {avid_bp:.4f} -> {new_bp:.4f} "
                            f"(parent {parent_bp:.4f})"
                        )
                else:
                    new_bp = None

        if new_bp is None:
            continue
        df_avid.at[idx, bp_col] = (f"{new_bp:.4f}%" if had_pct
                                   else f"{new_bp:.4f}")
        try:
            df_avid.at[idx, raw_col] = float(round(
                avid_sample * new_bp / 100.0))
            if uspop:
                df_avid.at[idx, proj_col] = float(round(
                    uspop * new_bp / 100.0))
        except Exception:
            pass

    if verbose:
        print(f"   [subset-coherence] {subject}: capped_up="
              f"{stats['capped_up']} lifted_down={stats['lifted_down']} "
              f"(ratio={ratio:.4f})")
        for ex in stats["examples_up"][:3]:
            print(f"      up-cap  {ex}")
        for ex in stats["examples_down"][:3]:
            print(f"      lift    {ex}")
    return df_avid, stats


# =============================================================================
# Phase 4c: single-brand ratio-collapse guard vs parent (2026-08-25)
# =============================================================================
def enforce_avid_ratio_collapse_guard(df_avid, df_parent, subject: str,
                                      *, verbose: bool = True,
                                      collapse_ratio: float = 0.5,
                                      peer_hold_ratio: float = 0.9,
                                      min_parent_bp: float = 2.0,
                                      max_parent_bp: float = 20.0):
    """Re-anchor single-brand avid collapses that contradict their own
    category neighborhood (2026-08-25, Liz QA flag on Nicolle Wallace
    Avid: CLAUDE AI base 7.3921 -> avid 1.5098, an 80% collapse, while
    the two nearest base-rank peers COPILOT and DUCKDUCKGO held/rose;
    same signature on Iowa 1st CD Voters Avid CLAUDE AI and on GROK
    across several unrelated pairs).

    On mass-digital-behavior categories a subset cannot organically
    shed most of ONE brand while both of its nearest category peers
    hold or rise - that pattern is the Phase 2 reasoning pass
    lowballing an isolated row, not a persona read. Scope:

      - _MEGA_REACH_CATS only (same category philosophy as the Phase
        4b downward lift: intensity selects for MORE digital behavior,
        not less; talent / politics / interest / persona categories
        are exempt because a hard single-brand drop there is routinely
        the persona read itself - an unrestricted dry run against 8
        shipped pairs re-anchored 108-327 legitimate persona rows per
        file, e.g. UFC/MMA declines on a news-avid audience),
      - brands with parent BP in [`min_parent_bp`, `max_parent_bp`]:
        below 2pp the ratio has no stable signal, above 20pp the
        Phase 2 pass reasons deliberately (top-of-category rows) and a
        halving can be a real persona verdict (e.g. an avid audience
        abandoning one specific platform); the >60pp extreme is
        already covered by enforce_avid_subset_coherence's lift,
      - rank shared brands by parent BP descending,
      - a row triggers when avid/parent < `collapse_ratio` AND its two
        nearest peers by parent rank BOTH have avid/parent >=
        `peer_hold_ratio` (held or rose - 0.9 rather than 1.0 because
        a healthy neighbor can drift a few percent down while the
        anomaly sheds 50%+),
      - the row is re-anchored to parent BP plus a subject-salted
        ADDITIVE jitter (never a multiplier, per the avid-skin rules),
        kept below the subset-arithmetic ceiling (avid_raw <=
        parent_raw), 4dp, off 2dp boundaries, never 4dp-colliding with
        the parent value.

    Brand-general within scope: nothing is hardcoded to a specific
    brand. Raw/Proj recomputed from the avid's own sample; Category
    Share is finalized downstream by the write safety net. Returns
    (df_avid, stats_dict).
    """
    bp_col, cs_col, raw_col, proj_col = _detect_cols(df_avid)
    p_bp_col, _, p_raw_col, _ = _detect_cols(df_parent)
    stats = {"reanchored": 0, "examples": []}
    if bp_col is None or p_bp_col is None:
        return df_avid, stats

    avid_sample = _read_sample_size(df_avid, raw_col)
    parent_sample = _read_sample_size(df_parent, p_raw_col)
    sample_ratio = None
    if avid_sample and parent_sample and 0.0 < (avid_sample / parent_sample) < 1.0:
        sample_ratio = avid_sample / parent_sample

    # Parent (CAT, VAL) -> bp
    parent_idx = {}
    for _, r in df_parent.iterrows():
        cu = str(r.get("Column", "")).strip().upper()
        vu = str(r.get("Value", "")).strip().upper()
        bp = _fbp(r.get(p_bp_col))
        if bp is None or not cu or not vu:
            continue
        parent_idx[(cu, vu)] = bp

    df_avid = df_avid.copy()
    for c in (bp_col, cs_col, raw_col, proj_col):
        if c in df_avid.columns and df_avid[c].dtype.name not in ("object", "O"):
            df_avid[c] = df_avid[c].astype(object)

    try:
        ss_mask = (df_avid["Column"].astype(str).str.upper().str.strip()
                   == "SAMPLE SIZE")
        uspop = float(str(df_avid.loc[ss_mask].iloc[0][proj_col])
                      .replace(",", "")) if ss_mask.any() else None
    except Exception:
        uspop = None

    pin_aliases = _subject_pin_aliases(subject)

    def _finalize(new_bp, parent_bp, seed):
        """4dp, off 2dp boundaries, not 4dp-colliding with parent."""
        new_bp = max(0.0001, min(99.49, round(new_bp, 4)))
        p4 = round(parent_bp, 4)
        for k in range(6):
            n4 = round(new_bp, 4)
            on_boundary = abs(n4 - round(n4, 2)) < 0.00005
            if n4 != p4 and not on_boundary:
                return n4
            new_bp = n4 + 0.0007 + abs(_seed_jitter(f"{seed}|nudge{k}",
                                                    span=0.0014))
        return round(new_bp, 4)

    # Group shared avid rows per category so peers can be ranked.
    # Peers are collected across the full [min_parent_bp, inf) range so
    # ranking context stays truthful; only rows INSIDE
    # [min_parent_bp, max_parent_bp] are eligible to be re-anchored.
    per_cat: dict = {}
    for idx in df_avid.index:
        cu = str(df_avid.at[idx, "Column"]).strip().upper()
        if cu not in _MEGA_REACH_CATS:
            continue
        if cu in _SUBSET_COHERENCE_SKIP_CATS or cu in DEMO_CATS_TF:
            continue
        vu = str(df_avid.at[idx, "Value"]).strip().upper()
        if not vu:
            continue
        avid_bp = _fbp(df_avid.at[idx, bp_col])
        if avid_bp is None:
            continue
        if avid_bp >= 99.49 and _norm_pin(vu) in pin_aliases:
            continue
        parent_bp = parent_idx.get((cu, vu))
        if parent_bp is None or parent_bp < min_parent_bp:
            continue
        if _norm_pin(vu) in pin_aliases and parent_bp >= 99.49:
            continue
        per_cat.setdefault(cu, []).append((idx, vu, parent_bp, avid_bp))

    for cu, rows in per_cat.items():
        if len(rows) < 3:
            continue  # need a brand and two peers
        ranked = sorted(rows, key=lambda t: -t[2])  # by parent BP desc
        for i, (idx, vu, parent_bp, avid_bp) in enumerate(ranked):
            if parent_bp > max_parent_bp:
                continue  # high-BP rows: deliberate reasoning, not lowball
            ratio = avid_bp / parent_bp
            if ratio >= collapse_ratio:
                continue
            # Two nearest peers by parent rank (prefer immediate
            # neighbors; fall back outward at the rank edges).
            peer_pos = [p for p in (i - 1, i + 1, i - 2, i + 2)
                        if 0 <= p < len(ranked) and p != i][:2]
            if len(peer_pos) < 2:
                continue
            peer_ratios = [ranked[p][3] / ranked[p][2] for p in peer_pos]
            if not all(pr >= peer_hold_ratio for pr in peer_ratios):
                continue

            # Re-anchor to parent + additive subject-salted jitter.
            span = max(0.10, min(0.60, parent_bp * 0.05))
            new_bp = parent_bp + _seed_jitter(
                f"{subject}|{cu}|{vu}|ratio-collapse", span=span,
            )
            if sample_ratio:
                new_bp = min(new_bp, (parent_bp / sample_ratio) * 0.995)
            new_bp = _finalize(new_bp, parent_bp,
                               f"{subject}|{cu}|{vu}|ratio-collapse")
            if new_bp <= avid_bp:
                continue

            had_pct = "%" in str(df_avid.at[idx, bp_col])
            df_avid.at[idx, bp_col] = (f"{new_bp:.4f}%" if had_pct
                                       else f"{new_bp:.4f}")
            try:
                if avid_sample:
                    df_avid.at[idx, raw_col] = float(round(
                        avid_sample * new_bp / 100.0))
                if uspop:
                    df_avid.at[idx, proj_col] = float(round(
                        uspop * new_bp / 100.0))
            except Exception:
                pass
            stats["reanchored"] += 1
            if len(stats["examples"]) < 8:
                stats["examples"].append(
                    f"{cu}/{vu}: {avid_bp:.4f} -> {new_bp:.4f} "
                    f"(parent {parent_bp:.4f}, ratio was {ratio:.3f}, "
                    f"peers held at {peer_ratios[0]:.2f}/{peer_ratios[1]:.2f})"
                )

    if verbose:
        print(f"   [ratio-collapse-guard] {subject}: reanchored="
              f"{stats['reanchored']}")
        for ex in stats["examples"][:5]:
            print(f"      re-anchor  {ex}")
    return df_avid, stats


# =============================================================================
# Source loading (s3_key OR local_path) -- mirrors super_fan_synthesis._load_source
# =============================================================================
def _subject_from_source_name(source):
    """Recover a subject label from the parent's S3 key / file name.

    'Happys_Place_06_09_2026_00_13.csv' -> 'Happys Place'. Last-resort
    fallback when BRAND INPUT carries a seed-file marker AND the
    INPUT_METADATA BRAND stamp is missing (see the identity guard in
    synthesize_avid_fan). Returns None when nothing usable remains."""
    base = os.path.basename(str(source or ""))
    base = re.sub(r"\.csv$", "", base, flags=re.I)
    base = re.sub(r"_\d{2}_\d{2}_\d{4}(_\d{2}_\d{2})?$", "", base)
    base = re.sub(r"_\d{4}_\d{2}_\d{2}(_\d{2}_\d{2})?$", "", base)
    return re.sub(r"\s+", " ", base.replace("_", " ")).strip() or None


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
    # Gen Pop baseline columns (Jenna 2026-08-22): parents written after
    # the rollout carry two terminal baseline columns. Strip them here so
    # cut synthesis never sees unexpected columns; the cut's own write
    # path re-appends them fresh against its own BPs.
    try:
        try:
            from migration.genpop_baseline import strip_genpop_columns
        except ImportError:
            from genpop_baseline import strip_genpop_columns  # type: ignore
        df = strip_genpop_columns(df)
    except Exception as _gp_err:
        print(f"   [genpop_baseline] source strip skipped: {_gp_err}")
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
    api_key_pool: Optional[list] = None,
    max_workers: Optional[int] = None,
    ship_gate: bool = True,
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
    # 2026-08-25 identity guard (Happys Place 'CSV - Avid Fan' defect):
    # a seed-file marker ('CSV') or empty subject must never become the
    # deliverable name. build_source_snapshot already substitutes the
    # INPUT_METADATA BRAND slug when BRAND INPUT carries the marker;
    # this is the last-resort net for parents lacking both. Recover
    # from the parent's own S3 key / file name, else refuse to ship a
    # mislabeled cut.
    if subject_is_seed_marker(subject):
        recovered = (_subject_from_source_name(source)
                     if kind in ("s3_key", "local_path") else None)
        if subject_is_seed_marker(recovered):
            raise RuntimeError(
                f"avid naming guard: subject {subject!r} (from parent "
                f"BRAND INPUT / INPUT_METADATA) is a seed-file marker "
                f"or empty and cannot name a deliverable. Parent "
                f"source={source!r}. Fix the parent's INPUT_METADATA "
                f"BRAND stamp or its BRAND INPUT row.")
        print(f"  subject recovered from parent file name: {recovered!r}")
        subject = recovered
        snap["subject"] = subject
    print(f"  subject={subject!r}  cats={snap['category_count']}  "
          f"sample={snap['sample_size']}")

    print(f"  -> Phase 1: audience reasoning ...")
    audience = reason_avid_audience(snap)
    print(f"     cohort_fraction={audience['cohort_fraction']:.4f}  "
          f"us_pop_fraction={audience['us_pop_fraction']:.4f}  "
          f"claude={audience.get('claude_used', False)}")
    print(f"     reasoning: {audience.get('reasoning', '')[:200]}")

    # Deterministic cohort_fraction override (Jenna 2026-08-24: cut
    # sample sizes must match the total universe). When the parent TU
    # carries a reasoned-era AVID FAN row (emitted by every fresh TU
    # since 2026-08-24 via migration/avid_share_reasoner), the avid
    # sample fraction IS that BP/100 - math read off the parent at
    # synthesis time, never Claude's estimate. Parents built before
    # 2026-08-24 have no row (no backfill; they gain one on their next
    # refresh) and keep the reasoned fraction; log which path sized it.
    det_cf = deterministic_avid_fraction(df_baseline)
    if det_cf is not None:
        old_cf = float(audience.get("cohort_fraction") or 0.0)
        override_with_deterministic_fraction(
            audience, det_cf, note="parent AVID FAN BP / 100")
        print(f"     deterministic cohort_fraction={det_cf:.4f} "
              f"(parent AVID FAN BP / 100; overriding Claude's "
              f"{old_cf:.4f})  us_pop_fraction adjusted to "
              f"{audience['us_pop_fraction']:.4f}")
    else:
        print(f"     no AVID FAN row on parent - keeping reasoned "
              f"cohort_fraction={audience['cohort_fraction']:.4f} "
              f"(no deterministic source)")

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
    # 2026-08-20 parallel categories (see cut_parallel.py) -
    # reasoning unchanged, chunks within a category still sequential.
    try:
        from cut_parallel import load_cut_key_pool, resolve_cut_workers
    except Exception:
        from migration.cut_parallel import (
            load_cut_key_pool, resolve_cut_workers,
        )
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading as _th
    _keys = list(api_key_pool) if api_key_pool else load_cut_key_pool()
    _n_workers = (int(max_workers) if max_workers
                  else resolve_cut_workers(_keys))
    cat_rows = {}
    for cat in cats_to_call:
        rows = []
        for _, r in df_baseline[cats_upper == cat].iterrows():
            v = _fbp(r.get(bp_col, 0))
            if v is None:
                continue
            rows.append((str(r.get(val_col, "")).strip(), v))
        if rows:
            cat_rows[cat] = rows
    print(f"     parallel: {_n_workers} workers over "
          f"{max(len(_keys), 1)} key(s)")
    _prog = {"done": 0, "rows_decided": 0}
    _plock = _th.Lock()

    def _reason_one(_idx, _cat):
        _key = _keys[_idx % len(_keys)] if _keys else None
        return reason_category_rows(subject, audience, _cat,
                                    cat_rows[_cat],
                                    df_source=df_baseline,
                                    api_key=_key)

    with ThreadPoolExecutor(max_workers=_n_workers) as _ex:
        _futs = {_ex.submit(_reason_one, _i, _c): _c
                 for _i, _c in enumerate(cat_rows)}
        for _fut in as_completed(_futs):
            _c = _futs[_fut]
            try:
                decisions = _fut.result()
            except Exception as _e:
                print(f"     {_c} FAILED: {_e}", flush=True)
                decisions = {}
            if decisions:
                cat_decisions[_c] = decisions
            with _plock:
                _prog["done"] += 1
                _prog["rows_decided"] += len(decisions)
                print(f"     [{_prog['done']:>2d}/{len(cat_rows)}] "
                      f"{_c:32s} rows={len(cat_rows[_c]):>4d}  "
                      f"claude_returned={len(decisions):>4d}  "
                      f"(running total decided="
                      f"{_prog['rows_decided']}/{total_rows})",
                      flush=True)
    rows_decided = _prog["rows_decided"]

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

    # Phase 4b (2026-08-24 Dylan/Erin audits): subset coherence vs the
    # parent, both directions. Upward: avid_raw must never exceed
    # parent_raw for a shared (category, brand). Downward: mass-reach
    # digital rows must not sit far below the parent on an engaged
    # slice. See enforce_avid_subset_coherence docstring.
    print(f"  -> Phase 4b: subset coherence vs baseline ...")
    try:
        df_avid, _coh_stats = enforce_avid_subset_coherence(
            df_avid, df_baseline, subject,
        )
    except Exception as _coh_err:
        print(f"     subset coherence skipped (non-fatal): {_coh_err}")

    # Phase 4c (2026-08-25, Liz QA flag on Nicolle Wallace Avid CLAUDE
    # AI): single-brand ratio-collapse guard. When one brand sheds
    # 50%+ of its base BP while BOTH nearest base-rank peers held or
    # rose, the Phase 2 reasoning lowballed an isolated row; re-anchor
    # it to parent + subject-salted additive jitter. Category-general.
    print(f"  -> Phase 4c: ratio-collapse guard vs baseline ...")
    try:
        df_avid, _rc_stats = enforce_avid_ratio_collapse_guard(
            df_avid, df_baseline, subject,
        )
    except Exception as _rc_err:
        print(f"     ratio-collapse guard skipped (non-fatal): {_rc_err}")

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
    # cuts and never end up as uncategorized". Resolution is MIRROR semantics
    # with a canonical fallback.
    #
    # 2026-08-10 (Jenna reinforcement): "make sure the brand category of the
    # avid cuts are always the same as the parent profile." Priority order
    # flipped so the PARENT (main) file is the primary source of truth.
    # The caller's kwarg becomes a fallback used only when the parent lacks
    # a BC row. If both are present and they disagree, PARENT wins and we
    # log a WARN so the caller-side drift can be investigated.
    #
    #   Priority 1: BRAND CATEGORY row on the SOURCE (main / baseline) file.
    #               This is the authoritative dashboard category. Avid MUST
    #               mirror it, no exceptions.
    #   Priority 2: `brand_category` kwarg from the caller. Only used when
    #               the parent file lacks a BC row (very rare — the D111
    #               enforcer in BG.py guarantees BC on every main file).
    #   Priority 3: BRAND CATEGORY row already on the avid file itself
    #               (leftover from Phase 3 apply_avid_transform). Only
    #               reached when neither of the above found anything usable.
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

        kwarg_bc = _clean(brand_category)
        parent_bc = ""
        try:
            src_col_u = df_baseline["Column"].astype(str).str.strip().str.upper()
            src_mask = src_col_u == "BRAND CATEGORY"
            if src_mask.any():
                parent_bc = _clean(df_baseline.loc[src_mask, "Value"].iloc[0])
        except Exception:
            pass

        # Priority 1: parent file (authoritative per 2026-08-10 rule).
        if parent_bc:
            bc_value = parent_bc
            bc_source = "baseline_file.BRAND_CATEGORY (parent-authoritative)"
            if kwarg_bc and kwarg_bc.upper() != parent_bc.upper():
                print(
                    f"   ⚠ BRAND CATEGORY DRIFT: caller passed "
                    f"{kwarg_bc!r} but parent file has {parent_bc!r} "
                    f"for {subject!r}. Per 2026-08-10 mirror rule, "
                    f"parent wins. Investigate upstream caller."
                )
        # Priority 2: caller kwarg (only when parent lacks BC).
        elif kwarg_bc:
            bc_value = kwarg_bc
            bc_source = "caller.brand_category (parent lacked BC row)"
            print(
                f"   ⚠ parent file lacks BRAND CATEGORY row for "
                f"{subject!r}; using caller kwarg {kwarg_bc!r}. "
                f"Investigate: main pipeline should have written BC "
                f"via D111 enforce_brand_category_row."
            )
        # Priority 3: leftover on avid file.
        elif bc_mask.any():
            bc_value = _clean(df_avid.loc[bc_mask, "Value"].iloc[0])
            bc_source = "avid_file.BRAND_CATEGORY (leftover from apply_avid_transform)"

        # Priority 4: hard fallback.
        if not (parent_bc or kwarg_bc) and not (bc_mask.any() and _clean(df_avid.loc[bc_mask, "Value"].iloc[0])):
            bc_value = "GENERAL"
            bc_source = "GENERAL (LAST-RESORT FALLBACK)"
            print(
                f"   ⚠ no BRAND CATEGORY resolvable for {subject!r} "
                f"(parent empty, kwarg empty, avid empty) -- forcing "
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

    # Terminal subset re-cap (2026-08-29 Bethenny / Automotive
    # Aftermarket I12 holds): the enforcer chain and the write safety
    # net both mutate BPs with no parent context (MPB deband re-spread,
    # ladder dejitter, panel-reality floors), AFTER Phase 4b and the
    # tu_avid_coherence pass already capped against the parent. Anything
    # they pushed past the subset raw ceiling shipped straight into the
    # terminal gate's I12 check and blocked the run. Re-run the
    # raw-verified cap as the LAST BP-mutating step before the cut
    # write gate; idempotent on a coherent frame.
    try:
        df_avid, _coh2 = enforce_avid_subset_coherence(
            df_avid, df_baseline, subject, verbose=True,
        )
        if _coh2.get("capped_up") or _coh2.get("lifted_down"):
            print(f"   ✅ terminal subset re-cap: "
                  f"capped_up={_coh2.get('capped_up', 0)} "
                  f"lifted_down={_coh2.get('lifted_down', 0)}")
    except Exception as _coh2_err:
        print(f"   ⚠ terminal subset re-cap failed (non-fatal): "
              f"{_coh2_err}")

    # Shared terminal cut write gate (2026-08-24 Furious audit D2/D5/
    # D6): final invariant polish (cohort-label guard + subject re-pin +
    # depin + SUBJECT-row backstop) -> parent no-collision recheck ->
    # numeric-artifact normalize -> canonical sort -> loud pre-upload
    # audit. Same chain every derived-cut path runs; see
    # migration/cut_write_gate.py.
    try:
        try:
            from migration.cut_write_gate import finalize_cut_for_upload
        except ImportError:
            from cut_write_gate import finalize_cut_for_upload  # type: ignore
        df_avid, _gate_report = finalize_cut_for_upload(
            df_avid, _avid_subject, parent_df=df_baseline,
            out_key=out_key, verbose=True,
            # Final ship gate (2026-08-24 Jenna mandate): blocking on
            # real uploads, report-only on dry runs and on the local
            # ops override (ship_gate kwarg).
            ship_gate=(bool(ship_gate) and not dry_run),
        )
    except Exception as _cwg_err:
        # ShipGateError is the blocking verdict - never swallow it.
        try:
            from migration.final_ship_gate import ShipGateError
        except ImportError:
            from final_ship_gate import ShipGateError  # type: ignore
        if isinstance(_cwg_err, ShipGateError):
            raise
        print(f"   ⚠ cut write gate raised (non-fatal): {_cwg_err}")

    # Gen Pop baseline columns (Jenna 2026-08-22): terminal append after
    # every enforcer / safety net so the raw file ships with the current
    # Gen Pop value + index per matched row. Non-fatal.
    try:
        try:
            from migration.genpop_baseline import append_genpop_columns
        except ImportError:
            from genpop_baseline import append_genpop_columns  # type: ignore
        df_avid = append_genpop_columns(df_avid)
    except Exception as _gp_err:
        print(f"   [genpop_baseline] append skipped: {_gp_err}")

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
def synthesize_avid_fan_for_s3_key(s3_key: str, *, dry_run: bool = False,
                                   api_key_pool=None,
                                   max_workers=None,
                                   ship_gate: bool = True) -> dict:
    return synthesize_avid_fan(s3_key, source_kind="s3_key", dry_run=dry_run,
                               ship_gate=ship_gate,
                               api_key_pool=api_key_pool,
                               max_workers=max_workers)


__all__ = [
    "synthesize_avid_fan",
    "synthesize_avid_fan_for_s3_key",
    "AVID_NON_APPLICABLE_CATEGORIES",
    "should_synthesize_avid_for_category",
    "deterministic_avid_fraction",
    "override_with_deterministic_fraction",
]
