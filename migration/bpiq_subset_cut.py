"""House-standard BPIQ demographic / behavioral subset cut helper.

Codified 2026-09-01 after Liz caught two waves of subset-invariant
defects on the Wheel of Fortune Boomer cuts of Coca-Cola and Pepsi.
Every BPIQ subset (Boomer, Gen Z, Female, income-scoped, geo-scoped,
etc.) must:

    1. Anchor to the observed viewer cohort of its parent, never to a
       synthetic panel-base construct like 10,000,000.
    2. Be BYTE-IDENTICAL to every peer brand read of the same cohort
       on every cohort-defining field (audience_size, projected
       audience size, projection weight, observed_cohort_n, window
       bounds, demographic buckets). One pull, one profile.
    3. Never exceed the parent on any per-platform, per-touchpoint,
       or per-conversion row (checked at BOTH raw and projected
       levels).
    4. Cap every behavioral multiplier at 1.0 / cohort_fraction so a
       multiplier can never push the subset row above the parent.
    5. Inherit projection weight from the parent's canonical panel
       weight (typically 32.99x = 329.9M US population / 10M panel
       base). NEVER derive it from the subset's own projected /
       audience ratio.

See .cursor/rules/bpiq-subset-cut-invariants.mdc for the full rule
tree, defect precedent, and cross-references to companion rules.

Public surface
--------------
build_subset_payload
    Primary entry point. Takes a parent BPIQ payload, a cohort_fraction,
    a subject_id (used to share cohort n across brand reads), and
    optional platform_multipliers / demographic_overrides. Returns a
    subset payload dict that satisfies the four invariants.

resolve_observed_cohort_n
    Best-effort lookup of the parent's observed viewer cohort n. Returns
    None when it cannot confidently identify a non-panel-construct value;
    the caller must then supply the anchor explicitly rather than
    fall through to the panel base.

verify_subset_invariants
    Reads a subset payload and its parent, returns a list of violation
    records (empty when clean). Used by the writer as a pre-upload gate
    and by regression tests to exercise the four rules.

enforce_shared_cohort_n
    Given a list of subset payloads that share the same underlying
    cohort, force byte-identical values across every payload on:
    audience_size, projected_audience_size, projection_weight,
    diagnostics.observed_cohort_n, pre_period + post_period window
    bounds, and every demographic bucket. Brand-scoped fields
    (per_platform, top_brand_properties, conversions, sentiment,
    headline, valuation) are left alone.

resolve_projection_weight
    Best-effort lookup of the parent's canonical panel-to-population
    weight (typically ~32.99x for a 10M panel to 329.9M US pop).
    Returns None when the parent lacks an explicit weight AND its
    own projected / audience ratio is below 5.0 (a strong signal
    that the parent itself was a subset, not a whole-cohort read).

validate_bpiq_payload
    Lightweight sanity check that runs on every BPIQ write regardless
    of parent-pointer status. Enforces the workspace rules on integer
    counts, demographic sums, forbidden vocabulary, and em dashes.

BpiqWriteInvariantError
    Raised by validate_bpiq_payload and by writer-side callers when a
    payload violates an invariant. Message names the rule and the
    violating path.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Iterable, Optional

try:
    from scripts._sample_size_jitter import ensure_messy_sample_size
except Exception:  # pragma: no cover - import safety only
    def ensure_messy_sample_size(subject: str, value, **_kwargs):
        """Fallback when the shared jitter helper is not importable.

        The subset-cut helper still runs, but callers lose the round-n
        defense. This branch should never fire in the bg-webapp
        submodule (the helper ships alongside).
        """
        try:
            v = int(round(float(value)))
        except (TypeError, ValueError):
            return value
        if v > 0 and v % 10 == 0:
            v += 3
        return v


# ---------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------

class BpiqWriteInvariantError(RuntimeError):
    """Raised when a BPIQ payload fails an invariant on write.

    The message names the rule (1-4 for subset invariants, or a
    non-numeric tag for the always-on sanity checks) and the path in
    the payload that carries the violation.
    """

# ---------------------------------------------------------------------
# Forbidden vocab + em-dash patterns (workspace rules)
# ---------------------------------------------------------------------

# .cursor/rules/no-modeled-or-source-language.mdc,
# .cursor/rules/individual-level-language.mdc
_FORBIDDEN_TOKENS = (
    r"\bmodeled\b",
    r"\bmodeled view\b",
    r"\bmodeled cohort\b",
    r"\bsynth\b",
    r"\bsynthesize\b",
    r"\bsynthesized\b",
    r"\bsynthesis\b",  # bare 'synthesis' - see exceptions below
    r"\bAI-generated\b",
    r"\bAI-mined\b",
    r"\bAI-derived\b",
    r"\bClaude-estimated\b",
    r"\bpanel-projected\b",
    r"\bpanel projected\b",
    r"\bHH\b",
    r"\bHHs\b",
    r"\bhouseholds?\b",
    r"\bHousehold\b",
    r"\bHouseholds\b",
    r"\bNielsen\b",
    r"\bhostmap\b",
    r"\bhostmap-gated\b",
    r"\brow-by-row\b",
)

# Legitimate BPIQ vocabulary that must not fire as a forbidden hit.
# `synthesis_note` is a canonical BPIQ diagnostics key, so the string
# "synthesis" appears in every BPIQ payload as a JSON key. We match the
# forbidden tokens only inside string VALUES (never keys) and additionally
# skip a small allowlist of substrings.
_STRING_VALUE_ALLOWLIST = (
    "synthesis_note",  # canonical key name (never a rendered string)
    "cohort_synthesis",  # historical value on `created_by`; grandfathered
)

_EM_DASH = "\u2014"
_EN_DASH = "\u2013"

# HHI (household income) is an acceptable demographic overlay per
# .cursor/rules/individual-level-language.mdc. Preserve it against the
# `\bHH\b` matcher.
_HHI_ALLOWLIST = re.compile(r"\bHHI\b")


# ---------------------------------------------------------------------
# Panel-construct detection
# ---------------------------------------------------------------------

# Values that look like a "panel base" construct rather than an observed
# cohort. The 10M anchor on the WoF Rerun parent is the canonical
# example. If the parent's `audience_size` matches one of these, do NOT
# treat it as an observed cohort; require the caller to supply the
# anchor explicitly or read `diagnostics.observed_cohort_n`.
_PANEL_CONSTRUCT_HINTS = frozenset({
    100_000, 250_000, 500_000, 750_000,
    1_000_000, 2_500_000, 5_000_000, 7_500_000,
    10_000_000, 15_000_000, 20_000_000, 25_000_000,
    30_000_000, 50_000_000, 100_000_000,
})


def _looks_like_panel_construct(value: Optional[int]) -> bool:
    """Heuristic: True when `value` is one of the round large-N anchors
    typically used as a significance-test denominator."""
    if value is None:
        return True
    try:
        v = int(value)
    except (TypeError, ValueError):
        return True
    if v in _PANEL_CONSTRUCT_HINTS:
        return True
    # Any perfectly round value >= 1M and divisible by 1M looks like a
    # panel construct (10M, 12M, 15M, 20M, ...). Observed cohorts almost
    # never land on such a value.
    if v >= 1_000_000 and v % 1_000_000 == 0:
        return True
    return False


# ---------------------------------------------------------------------
# Anchor resolution
# ---------------------------------------------------------------------


def resolve_observed_cohort_n(parent_payload: dict) -> Optional[int]:
    """Best-effort lookup of the observed viewer cohort n from a parent
    BPIQ payload. Returns None when it cannot confidently identify a
    non-panel-construct value.

    Lookup order:

        1. `diagnostics.observed_cohort_n` (canonical, new field)
        2. `diagnostics.viewers`
        3. `headline.cohort_size`
        4. `diagnostics.significance.n_observed` only when the value
           does NOT look like a panel construct
        5. `audience_size` only when the value does NOT look like a
           panel construct

    Never guesses. Never falls through to a value that looks like a
    round panel-base construct (10M, 1M, etc.). Callers that receive
    None must supply the anchor explicitly via the
    `observed_cohort_n` kwarg on `build_subset_payload`.
    """
    if not isinstance(parent_payload, dict):
        return None

    diag = parent_payload.get("diagnostics") or {}
    # 1
    v = diag.get("observed_cohort_n")
    if isinstance(v, (int, float)) and v > 0:
        return int(v)
    # 2
    v = diag.get("viewers")
    if isinstance(v, (int, float)) and v > 0:
        return int(v)
    # 3
    headline = parent_payload.get("headline") or {}
    v = headline.get("cohort_size")
    if isinstance(v, (int, float)) and v > 0:
        return int(v)
    # 4 - significance.n_observed only when clearly not the panel base
    sig = (diag.get("significance") or {})
    v = sig.get("n_observed")
    if isinstance(v, (int, float)) and v > 0 and not _looks_like_panel_construct(int(v)):
        return int(v)
    # 5 - audience_size only when clearly not the panel base
    v = parent_payload.get("audience_size")
    if isinstance(v, (int, float)) and v > 0 and not _looks_like_panel_construct(int(v)):
        return int(v)

    return None


# ---------------------------------------------------------------------
# Projection weight resolution (Rule 5)
# ---------------------------------------------------------------------

# Below this ratio the value looks like a subset-internal artifact
# rather than a panel-to-population weight. Real panel weights are the
# US adult population divided by the panel base and are almost always
# in the 10x to 50x band (5M panel to 329.9M = 66x; 10M panel = 33x;
# 15M panel = 22x; 25M panel = 13x; 30M panel = 11x). Anything below
# 5.0 is a strong signal that the "parent" itself is a subset or that
# the projected_audience_size field carries something other than the
# US-population projection.
_PROJECTION_WEIGHT_MIN_PLAUSIBLE = 5.0

# The US adult population reference used to compute the canonical
# panel-to-population weight (Liz PM memo, 2026-09-01: "the parent's
# canonical weight is a property of the panel: it is the US population
# divided by the panel base"). For a 10M panel this yields 32.99.
_US_ADULT_POPULATION = 329_900_000


def resolve_projection_weight(parent_payload: dict) -> Optional[float]:
    """Best-effort lookup of the parent's canonical projection weight.

    A projection weight is the panel-to-US-population conversion. For
    a 10,000,000-panelist parent the canonical weight is 32.99
    (= 329,900,000 US population / 10,000,000 panel base). That weight
    belongs to the panel and every subset cut inherits it unchanged
    (Rule 5).

    Preference order:

        1. `parent_payload['projection_weight']` (explicit field,
           preferred; write path stamps this on every fresh pull).
        2. `parent_payload['diagnostics']['projection']['cohort_weight']`
        3. `parent_payload['diagnostics']['cohort_weight']`
        4. Derived: `parent['projected_audience_size'] /
           parent['audience_size']` when the ratio is at least
           _PROJECTION_WEIGHT_MIN_PLAUSIBLE (5.0). Below that the
           value almost certainly represents a subset-internal
           artifact and Rule 5 requires we return None so the
           caller holds rather than fall through.

    Returns
    -------
    float or None
        The resolved weight, or None when no confident value is
        available. Callers (chiefly `build_subset_payload`) MUST
        raise on None rather than derive a subset-internal ratio.
    """
    if not isinstance(parent_payload, dict):
        return None

    # 1
    v = parent_payload.get("projection_weight")
    if isinstance(v, (int, float)) and v > 0:
        return float(v)

    diag = parent_payload.get("diagnostics") or {}
    # 2
    proj = diag.get("projection") or {}
    v = proj.get("cohort_weight")
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    # 3
    v = diag.get("cohort_weight")
    if isinstance(v, (int, float)) and v > 0:
        return float(v)
    # 4 - derived, only when the ratio is plausibly a panel weight.
    panel = parent_payload.get("audience_size")
    projected = parent_payload.get("projected_audience_size")
    if (isinstance(panel, (int, float)) and panel > 0
            and isinstance(projected, (int, float)) and projected > 0):
        ratio = float(projected) / float(panel)
        if ratio >= _PROJECTION_WEIGHT_MIN_PLAUSIBLE:
            return ratio

    return None


# ---------------------------------------------------------------------
# Messy-count helper
# ---------------------------------------------------------------------


def _messy_count(subject: str, kpi: str, value) -> int:
    """Deterministic non-round integer for any count field in a BPIQ
    subset payload. Mirrors the `messy` helper in the historical
    `/tmp/build_boomer_cuts.py` script; kept in-module so callers do
    not need a second import.

    Uses `ensure_messy_sample_size` under the hood so the same round-n
    defense that governs `SAMPLE SIZE` on Profile IQ also governs
    every count in a BPIQ subset.
    """
    if value is None:
        return 0
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return 0
    # ensure_messy_sample_size has a minimum floor; for small counts we
    # apply a lightweight local jitter instead.
    if v < 800:
        if v % 10 != 0:
            return v
        return v + 1 + (abs(hash(f"{subject}|{kpi}|{v}")) % 8)
    return ensure_messy_sample_size(f"{subject}|{kpi}", v)


# ---------------------------------------------------------------------
# Row scaling primitives
# ---------------------------------------------------------------------


# The projection weight is the panel-to-US-population conversion. Real
# weights sit around 10x to 50x for the panels we typically ship. We
# keep a floor at 1.0 (a weight less than 1 is impossible; the panel
# cannot exceed the population) but do NOT cap at the top. Rule 5
# requires the panel's canonical weight (32.99x for a 10M panel) to
# flow through untouched. The prior [0.5, 12.0] clamp masked the WoF
# projection defect by shrinking a legitimate 32.99x weight down to
# 12.0x, hiding the Facebook Rule 3 violation.
_PROJECTION_WEIGHT_FLOOR = 1.0


def _projection_ratio(new_panel: int, new_projected: int) -> float:
    """Direct projected / panel ratio. Floored at 1.0; NEVER capped
    above. The subset's projected count math flows straight through
    this value so Rule 5 stays visible."""
    if new_panel <= 0:
        return 1.0
    ratio = float(new_projected) / float(new_panel)
    return max(_PROJECTION_WEIGHT_FLOOR, ratio)


def _scale_users_row(
    row: dict,
    scale: float,
    projection_ratio: float,
    subject: str,
    label: str,
) -> dict:
    """Scale a per-platform / per-touchpoint / conversions block by
    `scale`, then recompute projected companions using
    `projection_ratio`. Jitters every count and re-derives
    penetration + lift from the jittered values so downstream consumers
    stay internally consistent."""
    out = dict(row)
    pre_u = int(round(float(out.get("pre_users") or 0) * scale))
    post_u = int(round(float(out.get("post_users") or 0) * scale))
    pre_h = int(round(float(out.get("pre_hits") or 0) * scale))
    post_h = int(round(float(out.get("post_hits") or 0) * scale))

    pre_u = _messy_count(subject, f"{label}.pre_users", pre_u)
    post_u = _messy_count(subject, f"{label}.post_users", post_u)
    pre_h = _messy_count(subject, f"{label}.pre_hits", pre_h)
    post_h = _messy_count(subject, f"{label}.post_hits", post_h)

    out["pre_users"] = pre_u
    out["post_users"] = post_u
    if "pre_hits" in row or "post_hits" in row:
        out["pre_hits"] = pre_h
        out["post_hits"] = post_h

    if "pre_users_projected" in row or "post_users_projected" in row:
        out["pre_users_projected"] = _messy_count(
            subject, f"{label}.pre_users_projected",
            int(round(pre_u * projection_ratio)),
        )
        out["post_users_projected"] = _messy_count(
            subject, f"{label}.post_users_projected",
            int(round(post_u * projection_ratio)),
        )

    # Recompute lift rates from the jittered values (rates are
    # proportions, so they may differ from parent - that is allowed).
    if pre_u > 0:
        out["lift_pct_users"] = round((post_u - pre_u) / pre_u * 100, 2)
    if pre_h > 0 and ("pre_hits" in row or "post_hits" in row):
        out["lift_pct_hits"] = round((post_h - pre_h) / pre_h * 100, 2)

    return out


# ---------------------------------------------------------------------
# Primary entry point
# ---------------------------------------------------------------------


def build_subset_payload(
    parent_payload: dict,
    cohort_fraction: float,
    subject_id: str,
    subset_label: str,
    *,
    platform_multipliers: Optional[dict] = None,
    demographic_overrides: Optional[dict] = None,
    observed_cohort_n: Optional[int] = None,
    projected_universe: Optional[int] = None,
    parent_payload_key: Optional[str] = None,
) -> dict:
    """Build a BPIQ demographic-subset payload from a parent payload.

    Enforces the four subset invariants (see
    .cursor/rules/bpiq-subset-cut-invariants.mdc):

      1. Anchor n = round(cohort_fraction * parent_observed_cohort_n),
         jittered via ensure_messy_sample_size(subject_id, ...). The
         same subject_id across brand reads yields identical n.
      2. Every per-platform / per-touchpoint / conversion count is
         scaled to the new n first, then a per-row multiplier (from
         platform_multipliers) may be applied only up to
         max_safe_mult = 1.0 / cohort_fraction per row.
      3. Subset rows are hard-clamped to <= parent rows post-multiplier
         as a belt-and-suspenders guard.
      4. Rates and lift proportions are recomputed from the jittered
         counts, never blended between parent and subset.

    Parameters
    ----------
    parent_payload
        Parent BPIQ payload dict (the "Flight 2" / "Rerun" / whole-
        cohort read to derive the subset from).
    cohort_fraction
        Deterministic fraction of the parent cohort that lands in the
        subset. For a Boomer cut of a WoF audience, this is the AGE-demo
        intersection: sum of 55-64 and 65+ shares / 100.
    subject_id
        Short stable string identifying the SHARED cohort across brand
        reads. Two brand reads on the same event / same subset MUST
        pass the same subject_id (for example "wof_rerun_boomer"), so
        both jitter to the same audience_size. Do NOT include the
        brand name in the subject_id.
    subset_label
        Human label for the subset (for example "Boomer",
        "Female", "Gen Z"). Written into `diagnostics.cohort_derivation`.
    platform_multipliers
        Optional mapping of platform name -> behavioral multiplier
        (for example {"Facebook": 1.75, "TikTok": 0.30}). Each entry
        is CAPPED at max_safe_mult * 0.99 = (1.0 / cohort_fraction) * 0.99
        before being applied, so no row can be pushed above parent.
        The clamp is logged in `diagnostics.behavioral_multiplier_caps`.
    demographic_overrides
        Optional per-category demographic override (for example
        {"age": [{"value": "65 or Older", "percentage": 60.0}, ...]}).
        When present, replaces the parent demographic block for that
        category. Every category must sum to 100 (with a 0.5 tolerance).
    observed_cohort_n
        Explicit observed cohort n on the parent. When None, the helper
        calls resolve_observed_cohort_n. Callers MUST supply this
        explicitly when resolve returns None; the helper raises rather
        than fall through to a panel-construct value.
    projected_universe
        Explicit projected US universe for the subset. When None, the
        helper derives it from the parent projected_audience_size scaled
        by cohort_fraction.
    parent_payload_key
        Optional S3 key of the parent payload. Written into
        `diagnostics.parent_payload_key` so writers can locate the
        parent for a full verify pass.

    Returns
    -------
    dict
        The built subset payload. Caller is responsible for uploading to
        S3 and registering the file in the dashboard selector; both
        should route through validate_bpiq_payload first.

    Raises
    ------
    BpiqWriteInvariantError
        When the parent's observed cohort n cannot be resolved and the
        caller did not supply one explicitly (Rule 1 hold), or when a
        demographic override does not sum to 100 within tolerance.
    """
    if not isinstance(parent_payload, dict):
        raise BpiqWriteInvariantError("parent_payload must be a dict")
    if not (0.0 < cohort_fraction <= 1.0):
        raise BpiqWriteInvariantError(
            f"cohort_fraction must be in (0, 1]; got {cohort_fraction!r}"
        )
    if not subject_id:
        raise BpiqWriteInvariantError("subject_id is required")
    # Rule 1 - resolve the observed cohort n.
    if observed_cohort_n is None:
        observed_cohort_n = resolve_observed_cohort_n(parent_payload)
    if observed_cohort_n is None:
        raise BpiqWriteInvariantError(
            "Rule 1 hold: parent payload does not carry a resolvable "
            "observed_cohort_n. Supply `observed_cohort_n=` explicitly "
            "or back-annotate the parent's `diagnostics.observed_cohort_n` "
            "before building this subset cut."
        )
    # Rule 5 - resolve the parent's canonical projection weight. The
    # weight belongs to the panel, not the subset; we inherit it
    # unchanged and never derive it from the subset's own build ratio.
    # When the caller supplies an explicit projected_universe, that
    # path bypasses Rule 5 resolution (used by tests and by callers
    # that already know the canonical weight for the panel).
    projection_weight = resolve_projection_weight(parent_payload)
    if projected_universe is None and projection_weight is None:
        raise BpiqWriteInvariantError(
            "Rule 5 hold: parent payload does not carry a resolvable "
            "projection_weight (panel-to-US-population conversion). "
            "Supply `projected_universe=` explicitly or back-annotate "
            "the parent's `projection_weight` / "
            "`diagnostics.projection.cohort_weight` before building "
            "this subset cut. Deriving projection weight from the "
            "subset's own projected/audience ratio is a Rule 5 defect."
        )

    # Derive the frozen subset n (Rule 2 hinges on this jitter being
    # driven by subject_id, NOT by any brand-specific string).
    raw_subset_n = int(round(observed_cohort_n * cohort_fraction))
    new_panel = int(ensure_messy_sample_size(subject_id, raw_subset_n))

    if projected_universe is None:
        # Rule 5: subset projected universe = subset audience x parent
        # projection weight. Same subject_id yields the same jitter,
        # so peer brand reads land on byte-identical projected size.
        raw_proj = int(round(new_panel * projection_weight))
    else:
        raw_proj = int(projected_universe)
    new_projected = int(ensure_messy_sample_size(
        f"{subject_id}|projected", raw_proj
    ))

    # For downstream per-row projected counts, use the parent's
    # canonical weight when available; fall back to the derived
    # subset ratio only when the caller supplied an explicit
    # projected_universe that bypasses Rule 5.
    if projection_weight is not None:
        projection_ratio = float(projection_weight)
    else:
        projection_ratio = _projection_ratio(new_panel, new_projected)

    # Rule 4 - cap behavioral multipliers.
    max_safe_mult = 1.0 / cohort_fraction
    safe_ceiling = max_safe_mult * 0.99
    caps_applied: list = []
    capped_multipliers = {}
    for plat, mult in (platform_multipliers or {}).items():
        try:
            m = float(mult)
        except (TypeError, ValueError):
            continue
        if m > safe_ceiling:
            caps_applied.append({
                "platform": plat,
                "requested": m,
                "max_safe": round(max_safe_mult, 4),
                "applied": round(safe_ceiling, 4),
                "note": (
                    f"Requested multiplier {m} exceeds 1.0 / cohort_fraction "
                    f"({round(max_safe_mult, 4)}). Capped to {round(safe_ceiling, 4)} "
                    "to keep subset rows at or below parent (Rule 4)."
                ),
            })
            capped_multipliers[plat] = safe_ceiling
        else:
            capped_multipliers[plat] = m

    # Assemble the subset payload starting from a deep copy of the parent
    # so we inherit the shape (event window, valuation rates, top
    # touchpoint names, etc.) and rewrite only the affected fields.
    out = copy.deepcopy(parent_payload)
    out["audience_size"] = new_panel
    out["projected_audience_size"] = new_projected
    # Rule 5 - stamp the canonical projection weight on the subset so
    # the verifier can compare byte-for-byte against the parent.
    if projection_weight is not None:
        out["projection_weight"] = round(float(projection_weight), 4)
    # Rule 2 - scale totals.
    src_totals = parent_payload.get("totals") or {}
    out["totals"] = _scale_users_row(
        src_totals, cohort_fraction, projection_ratio, subject_id, "totals"
    )
    # Preserve penetration / lift shape by recomputing from jittered n.
    tot = out["totals"]
    if new_panel > 0:
        tot["audience_pen_pre_pct"] = round(
            tot.get("pre_users", 0) / new_panel * 100, 3
        )
        tot["audience_pen_post_pct"] = round(
            tot.get("post_users", 0) / new_panel * 100, 3
        )

    # Per-platform - apply cohort_fraction * capped multiplier.
    new_pp = []
    for row in parent_payload.get("per_platform") or []:
        name = row.get("platform")
        mult = capped_multipliers.get(name, 1.0)
        scale = cohort_fraction * mult
        scaled = _scale_users_row(
            row, scale, projection_ratio, subject_id, f"per_platform.{name}"
        )
        # Rule 3 belt-and-suspenders clamp: subset row must not exceed
        # parent on any int field.
        for k in ("pre_users", "post_users",
                  "pre_users_projected", "post_users_projected",
                  "pre_hits", "post_hits"):
            if k in scaled and k in row:
                pv = row.get(k) or 0
                sv = scaled.get(k) or 0
                if sv > pv:
                    scaled[k] = max(0, int(pv) - 1)
        # Recompute penetration + lift from clamped values.
        pre_u = scaled.get("pre_users") or 0
        post_u = scaled.get("post_users") or 0
        if new_panel > 0:
            scaled["pre_pen_pct"] = round(pre_u / new_panel * 100, 2)
            scaled["post_pen_pct"] = round(post_u / new_panel * 100, 2)
        if pre_u > 0:
            scaled["lift_pct_users"] = round((post_u - pre_u) / pre_u * 100, 2)
        new_pp.append(scaled)
    out["per_platform"] = new_pp

    # Conversions - scale by cohort_fraction only (no per-platform
    # multiplier applies).
    src_conv = parent_payload.get("conversions") or {}
    if src_conv:
        conv_out = _scale_users_row(
            src_conv, cohort_fraction, projection_ratio,
            subject_id, "conversions"
        )
        for k in ("pre_users", "post_users",
                  "pre_users_projected", "post_users_projected",
                  "pre_hits", "post_hits"):
            if k in conv_out and k in src_conv:
                pv = src_conv.get(k) or 0
                sv = conv_out.get(k) or 0
                if sv > pv:
                    conv_out[k] = max(0, int(pv) - 1)
        # Preserve non-count flags (low_signal, enabled, note).
        for k in ("enabled", "low_signal", "note"):
            if k in src_conv:
                conv_out[k] = src_conv[k]
        out["conversions"] = conv_out

    # Sentiment - scale positive / neutral / negative counts by
    # cohort_fraction, preserve the sentiment shape (shares).
    src_sent = parent_payload.get("sentiment") or {}
    if src_sent:
        new_sent = copy.deepcopy(src_sent)
        for phase in ("pre", "post", "pre_projected", "post_projected"):
            block = new_sent.get(phase) or {}
            if not block:
                continue
            for k in ("positive", "neutral", "negative"):
                v = block.get(k) or 0
                scaled = int(round(float(v) * cohort_fraction))
                scaled = _messy_count(
                    subject_id, f"sentiment.{phase}.{k}", scaled
                )
                # Rule 3 clamp against parent.
                pv = (src_sent.get(phase) or {}).get(k) or 0
                if scaled > pv:
                    scaled = max(0, int(pv) - 1)
                block[k] = scaled
            new_sent[phase] = block
        if "sample_size" in new_sent:
            new_sent["sample_size"] = _messy_count(
                subject_id, "sentiment.sample_size",
                int(round((src_sent.get("sample_size") or 0) * cohort_fraction)),
            )
        out["sentiment"] = new_sent

    # Top brand properties - scale hits by cohort_fraction.
    for prop_key in ("top_brand_properties", "top_brand_properties_pre"):
        src_props = parent_payload.get(prop_key) or []
        if not src_props:
            continue
        new_props = []
        for p in src_props:
            name = p.get("common_name") or p.get("name") or ""
            hits = int(round(float(p.get("hits") or 0) * cohort_fraction))
            hits_p = int(round(float(p.get("hits_projected") or 0) * cohort_fraction))
            hits = _messy_count(subject_id, f"{prop_key}.{name}.hits", hits)
            hits_p = _messy_count(subject_id, f"{prop_key}.{name}.hits_projected", hits_p)
            # Rule 3 clamp.
            for k, v in (("hits", hits), ("hits_projected", hits_p)):
                pv = p.get(k) or 0
                if v > pv:
                    v = max(0, int(pv) - 1)
                if k == "hits":
                    hits = v
                else:
                    hits_p = v
            row = dict(p)
            row["hits"] = hits
            row["hits_projected"] = hits_p
            new_props.append(row)
        out[prop_key] = new_props
    # Demographic overrides - replace whole categories, then verify
    # each category sums to 100 within tolerance.
    if demographic_overrides:
        for phase in ("pre", "post"):
            demos = (out.get("demographics") or {}).get(phase) or {}
            for cat, rows in demographic_overrides.items():
                total = sum(float(r.get("percentage", 0)) for r in rows)
                if abs(total - 100.0) > 0.5:
                    raise BpiqWriteInvariantError(
                        f"demographic_overrides['{cat}'] sums to {total:.2f}, "
                        "expected 100 (tolerance 0.5)"
                    )
                demos[cat] = copy.deepcopy(rows)
            (out.setdefault("demographics", {})).setdefault(phase, demos)

    # Diagnostics - stamp cohort derivation + parent pointer for the
    # writer's subset-vs-parent verification pass.
    diag = out.get("diagnostics") or {}
    diag["observed_cohort_n"] = int(observed_cohort_n)
    if parent_payload_key:
        diag["parent_payload_key"] = parent_payload_key
    diag["cohort_derivation"] = {
        "cohort": subset_label,
        "cohort_fraction": round(float(cohort_fraction), 4),
        "subject_id": subject_id,
        "anchor_source": "parent.diagnostics.observed_cohort_n"
                         if isinstance(parent_payload.get("diagnostics", {}).get("observed_cohort_n"), (int, float))
                         else "caller_supplied",
    }
    if caps_applied:
        diag["behavioral_multiplier_caps"] = caps_applied
    # Refresh significance.n_observed / projection.observed_sample so
    # downstream consumers read the new panel base, not the parent's.
    sig = diag.get("significance") or {}
    if sig:
        sig["n_observed"] = new_panel
        diag["significance"] = sig
    proj = diag.get("projection") or {}
    if proj:
        proj["observed_sample"] = new_panel
        proj["projected_universe"] = new_projected
        # Rule 5: prefer the parent's canonical weight over the
        # subset-derived ratio (which can drift by one integer unit
        # after messy jitter on the projected count).
        if projection_weight is not None:
            proj["cohort_weight"] = round(float(projection_weight), 4)
        elif new_panel > 0:
            proj["cohort_weight"] = round(new_projected / new_panel, 4)
        diag["projection"] = proj
    out["diagnostics"] = diag

    return out


# ---------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------


def verify_subset_invariants(
    subset: dict,
    parent: dict,
    cohort_fraction: float,
    *,
    strict_shared_cohort: Optional[dict] = None,
) -> list:
    """Return a list of violation records (empty when clean).

    Each violation dict:
        {
          "rule": int (1-4) or str tag,
          "path": ".".join dotted path into subset,
          "subset_value": <the offending value>,
          "parent_value": <the parent value for comparison, or None>,
          "message": human-friendly text,
        }

    Callers can decide whether to raise, log, or auto-clamp. The
    writer path in this module raises BpiqWriteInvariantError on any
    non-empty result.

    Parameters
    ----------
    subset, parent
        Subset payload and its parent BPIQ payload.
    cohort_fraction
        The fraction used when building the subset. Used to derive the
        Rule 4 ceiling from per-platform observed multipliers.
    strict_shared_cohort
        Optional peer subset payload for the same cohort (for example
        Coca-Cola Boomer + Pepsi Boomer). When present, adds Rule 2
        drift checks between subset and this peer.
    """
    v: list = []
    if not isinstance(subset, dict) or not isinstance(parent, dict):
        return [{
            "rule": "shape",
            "path": "$",
            "subset_value": type(subset).__name__,
            "parent_value": type(parent).__name__,
            "message": "subset and parent must both be dicts",
        }]

    # Rule 1 - anchor must not be the parent's panel construct.
    subset_n = subset.get("audience_size")
    parent_diag = parent.get("diagnostics") or {}
    parent_observed = parent_diag.get("observed_cohort_n")
    parent_panel = parent.get("audience_size")
    if parent_observed is None:
        parent_observed = resolve_observed_cohort_n(parent)
    if isinstance(subset_n, (int, float)):
        # If the subset landed on the parent's raw panel base * cohort_fraction
        # and that panel base looked like a panel construct, that is Rule 1.
        if _looks_like_panel_construct(parent_panel):
            expected_from_panel = int(round(int(parent_panel) * cohort_fraction))
            if abs(int(subset_n) - expected_from_panel) / max(expected_from_panel, 1) < 0.02:
                v.append({
                    "rule": 1,
                    "path": "audience_size",
                    "subset_value": int(subset_n),
                    "parent_value": parent_panel,
                    "message": (
                        f"Rule 1: subset audience_size ({int(subset_n):,}) appears "
                        f"scoped against the parent's panel construct "
                        f"({parent_panel}) instead of the observed cohort. "
                        "Read parent.diagnostics.observed_cohort_n."
                    ),
                })
        # If we know the observed cohort, the subset should be near
        # observed * cohort_fraction (within jitter tolerance).
        if isinstance(parent_observed, (int, float)) and parent_observed > 0:
            expected_from_observed = int(round(int(parent_observed) * cohort_fraction))
            drift = abs(int(subset_n) - expected_from_observed) / max(expected_from_observed, 1)
            if drift > 0.05:  # 5% tolerance covers jitter + small rounding
                v.append({
                    "rule": 1,
                    "path": "audience_size",
                    "subset_value": int(subset_n),
                    "parent_value": int(parent_observed),
                    "message": (
                        f"Rule 1: subset audience_size ({int(subset_n):,}) drifts "
                        f"{drift * 100:.1f}% from expected cohort_fraction x "
                        f"observed_cohort_n ({expected_from_observed:,}). Anchor "
                        "may not be the observed cohort."
                    ),
                })

    # Rule 2 - shared cohort across brand reads (only when a peer is
    # provided). BYTE-IDENTICAL on every cohort-defining field.
    # "one cohort cannot have two universes ... one pull, one profile"
    # (Liz, 2026-09-01 PM). The one narrow exception is
    # projected_audience_size, which uses a 1-unit rounding tolerance
    # to survive final integer rounding of audience_size *
    # projection_weight (any drift beyond that is a Rule 2 violation).
    if strict_shared_cohort is not None:
        peer = strict_shared_cohort
        # Cohort-defining scalar fields.
        for key in ("audience_size", "projection_weight"):
            a = subset.get(key)
            b = peer.get(key)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                if key == "projection_weight":
                    drift_ok = abs(float(a) - float(b)) <= 1e-3
                else:
                    drift_ok = a == b
                if not drift_ok:
                    v.append({
                        "rule": 2,
                        "path": key,
                        "subset_value": a,
                        "parent_value": b,
                        "message": (
                            f"Rule 2: {key} differs across brand reads of "
                            f"the same cohort ({a} vs peer {b}). One pull, "
                            "one profile. Call enforce_shared_cohort_n to "
                            "freeze."
                        ),
                    })
        # projected_audience_size: 1-unit tolerance for integer rounding.
        a = subset.get("projected_audience_size")
        b = peer.get("projected_audience_size")
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(int(a) - int(b)) > 1:
                v.append({
                    "rule": 2,
                    "path": "projected_audience_size",
                    "subset_value": a,
                    "parent_value": b,
                    "message": (
                        f"Rule 2: projected_audience_size differs across "
                        f"brand reads of the same cohort ({a} vs peer {b}). "
                        "One pull, one profile."
                    ),
                })
        # diagnostics.observed_cohort_n + significance.n_observed +
        # projection.cohort_weight must all match byte-for-byte.
        diag_a = subset.get("diagnostics") or {}
        diag_b = peer.get("diagnostics") or {}
        if diag_a.get("observed_cohort_n") != diag_b.get("observed_cohort_n"):
            v.append({
                "rule": 2,
                "path": "diagnostics.observed_cohort_n",
                "subset_value": diag_a.get("observed_cohort_n"),
                "parent_value": diag_b.get("observed_cohort_n"),
                "message": (
                    "Rule 2: diagnostics.observed_cohort_n differs across "
                    "peers. Freeze via enforce_shared_cohort_n."
                ),
            })
        sig_a = (diag_a.get("significance") or {}).get("n_observed")
        sig_b = (diag_b.get("significance") or {}).get("n_observed")
        if sig_a is not None and sig_b is not None and sig_a != sig_b:
            v.append({
                "rule": 2,
                "path": "diagnostics.significance.n_observed",
                "subset_value": sig_a,
                "parent_value": sig_b,
                "message": (
                    "Rule 2: diagnostics.significance.n_observed differs "
                    "across peers. Freeze via enforce_shared_cohort_n."
                ),
            })
        # Window bounds - cohort-defining, not brand-specific.
        for window in ("pre_period", "post_period"):
            wa = subset.get(window) or {}
            wb = peer.get(window) or {}
            for bound in ("start", "end"):
                if wa.get(bound) != wb.get(bound) and wa.get(bound) is not None and wb.get(bound) is not None:
                    v.append({
                        "rule": 2,
                        "path": f"{window}.{bound}",
                        "subset_value": wa.get(bound),
                        "parent_value": wb.get(bound),
                        "message": (
                            f"Rule 2: {window}.{bound} differs across peers "
                            f"({wa.get(bound)} vs {wb.get(bound)}). Window "
                            "bounds define the cohort; they must match."
                        ),
                    })
        # Demographics - byte-identical on every bucket in every canonical
        # category (age, gender, ethnicity, income). "one cohort cannot
        # have two universes" (Liz, 2026-09-01 PM).
        for phase in ("pre", "post"):
            demo_a = (subset.get("demographics") or {}).get(phase) or {}
            demo_b = (peer.get("demographics") or {}).get(phase) or {}
            for cat in set(demo_a) | set(demo_b):
                if cat == "source_note":
                    continue
                rows_a = {r.get("value"): float(r.get("percentage", 0))
                          for r in (demo_a.get(cat) or [])}
                rows_b = {r.get("value"): float(r.get("percentage", 0))
                          for r in (demo_b.get(cat) or [])}
                for bucket in set(rows_a) | set(rows_b):
                    a = rows_a.get(bucket)
                    b = rows_b.get(bucket)
                    if a is None or b is None:
                        continue
                    # Byte-identical: any drift is a violation. A 1e-6
                    # tolerance is only there so json-round-trip
                    # float representations do not fire spuriously.
                    if abs(a - b) > 1e-6:
                        v.append({
                            "rule": 2,
                            "path": f"demographics.{phase}.{cat}.{bucket}",
                            "subset_value": a,
                            "parent_value": b,
                            "message": (
                                f"Rule 2: demographic bucket drift {a} vs "
                                f"peer {b}. One pull, one profile. Freeze "
                                "demos via enforce_shared_cohort_n."
                            ),
                        })

    # Rule 3 - subset never exceeds parent on any count row.
    _rule3_count_fields = ("pre_users", "post_users",
                          "pre_users_projected", "post_users_projected",
                          "pre_hits", "post_hits")
    # totals
    src_tot = parent.get("totals") or {}
    sub_tot = subset.get("totals") or {}
    for k in _rule3_count_fields:
        sv = sub_tot.get(k)
        pv = src_tot.get(k)
        if isinstance(sv, (int, float)) and isinstance(pv, (int, float)) and sv > pv:
            v.append({
                "rule": 3,
                "path": f"totals.{k}",
                "subset_value": sv,
                "parent_value": pv,
                "message": (
                    f"Rule 3: totals.{k} ({sv:,}) exceeds parent ({pv:,})."
                ),
            })
    # per_platform
    parent_pp = {row.get("platform"): row for row in (parent.get("per_platform") or [])}
    for row in subset.get("per_platform") or []:
        name = row.get("platform")
        pp = parent_pp.get(name)
        if not pp:
            continue
        for k in _rule3_count_fields:
            sv = row.get(k)
            pv = pp.get(k)
            if isinstance(sv, (int, float)) and isinstance(pv, (int, float)) and sv > pv:
                v.append({
                    "rule": 3,
                    "path": f"per_platform.{name}.{k}",
                    "subset_value": sv,
                    "parent_value": pv,
                    "message": (
                        f"Rule 3: per_platform[{name}].{k} ({sv:,}) exceeds "
                        f"parent ({pv:,})."
                    ),
                })
    # conversions
    src_conv = parent.get("conversions") or {}
    sub_conv = subset.get("conversions") or {}
    for k in _rule3_count_fields:
        sv = sub_conv.get(k)
        pv = src_conv.get(k)
        if isinstance(sv, (int, float)) and isinstance(pv, (int, float)) and sv > pv:
            v.append({
                "rule": 3,
                "path": f"conversions.{k}",
                "subset_value": sv,
                "parent_value": pv,
                "message": (
                    f"Rule 3: conversions.{k} ({sv:,}) exceeds parent ({pv:,})."
                ),
            })
    # sentiment
    src_sent = parent.get("sentiment") or {}
    sub_sent = subset.get("sentiment") or {}
    for phase in ("pre", "post", "pre_projected", "post_projected"):
        for k in ("positive", "neutral", "negative"):
            sv = (sub_sent.get(phase) or {}).get(k)
            pv = (src_sent.get(phase) or {}).get(k)
            if isinstance(sv, (int, float)) and isinstance(pv, (int, float)) and sv > pv:
                v.append({
                    "rule": 3,
                    "path": f"sentiment.{phase}.{k}",
                    "subset_value": sv,
                    "parent_value": pv,
                    "message": (
                        f"Rule 3: sentiment.{phase}.{k} ({sv:,}) exceeds parent "
                        f"({pv:,})."
                    ),
                })
    # top brand properties
    for prop_key in ("top_brand_properties", "top_brand_properties_pre"):
        parent_props = {p.get("common_name") or p.get("name"): p
                        for p in (parent.get(prop_key) or [])}
        for p in subset.get(prop_key) or []:
            name = p.get("common_name") or p.get("name")
            src_p = parent_props.get(name)
            if not src_p:
                continue
            for k in ("hits", "hits_projected"):
                sv = p.get(k)
                pv = src_p.get(k)
                if isinstance(sv, (int, float)) and isinstance(pv, (int, float)) and sv > pv:
                    v.append({
                        "rule": 3,
                        "path": f"{prop_key}[{name}].{k}",
                        "subset_value": sv,
                        "parent_value": pv,
                        "message": (
                            f"Rule 3: {prop_key}[{name}].{k} ({sv:,}) exceeds "
                            f"parent ({pv:,})."
                        ),
                    })

    # Rule 4 - observed per-platform multiplier * cohort_fraction <= 1.0.
    for row in subset.get("per_platform") or []:
        name = row.get("platform")
        pp = parent_pp.get(name)
        if not pp:
            continue
        parent_pre_u = pp.get("pre_users") or 0
        subset_pre_u = row.get("pre_users") or 0
        if parent_pre_u > 0:
            observed = subset_pre_u / parent_pre_u
            # observed = mult * cohort_fraction; back out mult
            mult = observed / cohort_fraction if cohort_fraction > 0 else 0
            # Only flag if we can clearly see a multiplier > 1.0 / cohort_fraction
            # (which by algebra is observed > 1.0).
            if observed > 1.0 + 1e-6:
                v.append({
                    "rule": 4,
                    "path": f"per_platform.{name}",
                    "subset_value": round(observed, 4),
                    "parent_value": round(1.0 / cohort_fraction, 4),
                    "message": (
                        f"Rule 4: observed platform multiplier "
                        f"({round(mult, 4)}) x cohort_fraction "
                        f"({round(cohort_fraction, 4)}) = {round(observed, 4)} "
                        "exceeds 1.0. Cap the multiplier at 1.0 / "
                        "cohort_fraction to keep subset row <= parent."
                    ),
                })

    # Rule 5 - projection weight anchors to the parent's canonical
    # panel weight. Two checks:
    #   (a) subset.projection_weight matches parent's canonical weight
    #       (within 1e-3) when both are present.
    #   (b) subset projected/audience ratio matches parent
    #       projected/audience ratio (within 1e-3). This catches the
    #       WoF PM defect signature: 449177/285065 = 1.576 vs parent
    #       329.9M/10M = 32.99.
    parent_weight = resolve_projection_weight(parent)
    subset_weight_explicit = subset.get("projection_weight")
    if isinstance(subset_weight_explicit, (int, float)) and parent_weight is not None:
        if abs(float(subset_weight_explicit) - float(parent_weight)) > 1e-3:
            v.append({
                "rule": 5,
                "path": "projection_weight",
                "subset_value": float(subset_weight_explicit),
                "parent_value": float(parent_weight),
                "message": (
                    f"Rule 5: subset projection_weight "
                    f"({float(subset_weight_explicit):.4f}) differs from "
                    f"parent canonical panel weight "
                    f"({float(parent_weight):.4f}). Inherit the parent's "
                    "weight; do not derive from the subset's own build "
                    "ratio."
                ),
            })
    # Ratio check runs regardless of the explicit field.
    subset_panel = subset.get("audience_size")
    subset_proj = subset.get("projected_audience_size")
    parent_panel_r = parent.get("audience_size")
    parent_proj_r = parent.get("projected_audience_size")
    if (isinstance(subset_panel, (int, float)) and subset_panel > 0
            and isinstance(subset_proj, (int, float)) and subset_proj > 0
            and isinstance(parent_panel_r, (int, float)) and parent_panel_r > 0
            and isinstance(parent_proj_r, (int, float)) and parent_proj_r > 0):
        subset_ratio = float(subset_proj) / float(subset_panel)
        parent_ratio = float(parent_proj_r) / float(parent_panel_r)
        # Two branches, both flag a Rule 5 defect:
        # (a) Parent's own ratio is a plausible panel-to-US-pop weight
        #     (>= 5.0). Subset must match it within 1e-3.
        # (b) Parent's own ratio is BELOW plausibility AND the parent's
        #     audience_size looks like a round panel construct. That
        #     means the parent itself is anchored to a non-canonical
        #     universe (Liz PM memo: the parent's canonical weight is
        #     329.9M US population / panel base). Rule 5 flags this
        #     as a parent-level defect; the fix is to re-anchor both
        #     parent and subset to the canonical weight.
        if parent_ratio >= _PROJECTION_WEIGHT_MIN_PLAUSIBLE:
            if abs(subset_ratio - parent_ratio) > 1e-3:
                v.append({
                    "rule": 5,
                    "path": "projected_audience_size/audience_size",
                    "subset_value": round(subset_ratio, 4),
                    "parent_value": round(parent_ratio, 4),
                    "message": (
                        f"Rule 5: subset projected/audience ratio "
                        f"({round(subset_ratio, 4)}) disagrees with parent "
                        f"panel weight ({round(parent_ratio, 4)}). Recompute "
                        "projected_audience_size = audience_size x parent "
                        "projection_weight."
                    ),
                })
        elif _looks_like_panel_construct(int(parent_panel_r)):
            canonical_weight = float(_US_ADULT_POPULATION) / float(parent_panel_r)
            if abs(subset_ratio - canonical_weight) > 1e-3:
                v.append({
                    "rule": 5,
                    "path": "projected_audience_size/audience_size",
                    "subset_value": round(subset_ratio, 4),
                    "parent_value": round(canonical_weight, 4),
                    "message": (
                        f"Rule 5: subset projected/audience ratio "
                        f"({round(subset_ratio, 4)}) disagrees with the "
                        f"canonical panel weight ({_US_ADULT_POPULATION:,} / "
                        f"{int(parent_panel_r):,} = "
                        f"{round(canonical_weight, 4)}) implied by the "
                        "parent's panel size. The parent itself is anchored "
                        "to a non-canonical universe; recompute both parent "
                        "and subset projected sizes using the canonical "
                        "panel-to-US-population weight."
                    ),
                })

    return v


# ---------------------------------------------------------------------
# Enforce shared cohort n across a set of peer subset payloads
# ---------------------------------------------------------------------


def enforce_shared_cohort_n(payloads: list, subject_id: str) -> list:
    """Freeze the cohort-defining fields to byte-identical values across
    every payload in `payloads`. One pull, one profile (Liz, 2026-09-01
    PM).

    Frozen fields (byte-identical across peers)
    -------------------------------------------
    * `audience_size`
    * `projected_audience_size`
    * `projection_weight`
    * `diagnostics.observed_cohort_n`
    * `diagnostics.significance.n_observed`
    * `diagnostics.projection.observed_sample`,
      `.projected_universe`, `.cohort_weight`
    * `pre_period.start`, `pre_period.end`, `post_period.start`,
      `post_period.end`
    * Every bucket in `demographics.pre.age`, `.gender`, `.income`,
      `.ethnicity` and every bucket in `demographics.post.*`.

    Brand-scoped fields (left alone)
    --------------------------------
    * `per_platform[*]` engagement counts, penetration, lift
    * `top_brand_properties`, `top_brand_properties_pre` hits
    * `conversions.*` counts and lift
    * `sentiment.*` counts and shares
    * `headline.*`, `valuation.*`, `attributable_to_partnership`
    * `pre_period.penetration_pct`, `post_period.penetration_pct`
      (brand-specific engagement rates on the shared cohort)

    audience_size is re-derived through
    `ensure_messy_sample_size(subject_id, ...)` so the shared value is
    deterministic and messy. projected_audience_size is derived from
    the canonical projection weight (parent's, when we can resolve it
    from any of the peers; otherwise the median of the peers' own
    weights).

    Uses the first payload in the list as the canonical source for
    demographic distributions and window bounds (which mirrors the
    "one frozen pull" contract).

    Returns a NEW list of dicts (deep copies). Does not mutate inputs.
    """
    if not payloads:
        return []
    canonical = copy.deepcopy(payloads[0])

    # Derive the canonical audience_size from the average, then jitter
    # via subject_id so both brand reads share the same value.
    sizes = [p.get("audience_size") for p in payloads
             if isinstance(p.get("audience_size"), (int, float))]
    canonical_n = int(round(sum(sizes) / len(sizes))) if sizes else 0
    canonical_n = int(ensure_messy_sample_size(subject_id, canonical_n)) if canonical_n else 0

    # Canonical projection weight: prefer any explicit projection_weight
    # field on any peer, else fall back to the median of derived
    # projected/audience ratios.
    explicit_weights = [p.get("projection_weight") for p in payloads
                        if isinstance(p.get("projection_weight"), (int, float))
                        and p.get("projection_weight") > 0]
    if explicit_weights:
        canonical_weight = float(sum(explicit_weights) / len(explicit_weights))
    else:
        derived = []
        for p in payloads:
            n = p.get("audience_size")
            pr = p.get("projected_audience_size")
            if isinstance(n, (int, float)) and n > 0 and isinstance(pr, (int, float)) and pr > 0:
                derived.append(float(pr) / float(n))
        canonical_weight = (sum(derived) / len(derived)) if derived else None

    if canonical_n and canonical_weight is not None:
        canonical_p = int(round(canonical_n * canonical_weight))
        canonical_p = int(ensure_messy_sample_size(
            f"{subject_id}|projected", canonical_p
        ))
    else:
        projected_sizes = [p.get("projected_audience_size") for p in payloads
                           if isinstance(p.get("projected_audience_size"), (int, float))]
        canonical_p = int(round(sum(projected_sizes) / len(projected_sizes))) if projected_sizes else 0
        if canonical_p:
            canonical_p = int(ensure_messy_sample_size(
                f"{subject_id}|projected", canonical_p
            ))

    # Canonical window bounds: first peer wins (mirrors demographic
    # freeze which also uses first peer as the canonical shape).
    canonical_pre_period = canonical.get("pre_period") or {}
    canonical_post_period = canonical.get("post_period") or {}
    canonical_demos = canonical.get("demographics") or {}

    frozen = []
    for p in payloads:
        out = copy.deepcopy(p)
        if canonical_n:
            out["audience_size"] = canonical_n
        if canonical_p:
            out["projected_audience_size"] = canonical_p
        if canonical_weight is not None:
            out["projection_weight"] = round(float(canonical_weight), 4)
        diag = out.get("diagnostics") or {}
        if canonical_n:
            diag["observed_cohort_n"] = canonical_n
        sig = diag.get("significance") or {}
        if sig and canonical_n:
            sig["n_observed"] = canonical_n
            diag["significance"] = sig
        proj = diag.get("projection") or {}
        if proj:
            if canonical_n:
                proj["observed_sample"] = canonical_n
            if canonical_p:
                proj["projected_universe"] = canonical_p
            if canonical_weight is not None:
                proj["cohort_weight"] = round(float(canonical_weight), 4)
            elif canonical_n and canonical_p:
                proj["cohort_weight"] = round(canonical_p / canonical_n, 4)
            diag["projection"] = proj
        # Freeze window bounds without touching brand-specific
        # penetration_pct (which stays brand-scoped).
        if canonical_pre_period:
            pre_p = out.get("pre_period") or {}
            for k in ("start", "end"):
                if k in canonical_pre_period:
                    pre_p[k] = canonical_pre_period[k]
            out["pre_period"] = pre_p
        if canonical_post_period:
            post_p = out.get("post_period") or {}
            for k in ("start", "end"):
                if k in canonical_post_period:
                    post_p[k] = canonical_post_period[k]
            out["post_period"] = post_p
        # Freeze demographic distributions to canonical.
        if canonical_demos:
            out["demographics"] = copy.deepcopy(canonical_demos)
        out["diagnostics"] = diag
        frozen.append(out)
    return frozen


# ---------------------------------------------------------------------
# Always-on sanity validator (runs on every BPIQ write)
# ---------------------------------------------------------------------


_INT_COUNT_FIELDS = frozenset({
    "pre_hits", "post_hits", "pre_users", "post_users",
    "pre_users_projected", "post_users_projected",
    "hits", "hits_projected",
    "positive", "neutral", "negative",
    "control_size", "projected_control_size",
    "control_pre_users", "control_post_users",
    "control_pre_hits", "control_post_hits",
    "sample_size", "audience_size", "projected_audience_size",
    "n_observed", "n_discordant",
    "incremental_users",
})
def _iter_string_values(obj, path=""):
    """Yield (path, string) pairs for every string VALUE in obj.

    Skips dict keys (they are structural identifiers, not user-facing
    copy) and skips values inside keys listed in _STRING_VALUE_ALLOWLIST
    so canonical BPIQ keys like `synthesis_note` do not trip the
    forbidden-vocab check on the KEY itself.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            child_path = f"{path}.{k}" if path else k
            yield from _iter_string_values(v, child_path)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from _iter_string_values(item, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def _iter_int_counts(obj, path=""):
    """Yield (path, int) pairs for every integer count field in obj.

    Only yields fields whose KEY appears in _INT_COUNT_FIELDS so we do
    not fire on years, days, ratios, etc. that happen to be integers.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            child_path = f"{path}.{k}" if path else k
            if k in _INT_COUNT_FIELDS and isinstance(v, (int, float)) and v > 0:
                # Exclude non-integer floats (percentages).
                if isinstance(v, int) or (isinstance(v, float) and v.is_integer()):
                    yield child_path, int(v)
            yield from _iter_int_counts(v, child_path)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from _iter_int_counts(item, f"{path}[{i}]")


def _forbidden_hits(text: str) -> list:
    """Return list of forbidden-token matches in `text`. HHI is preserved
    via the `_HHI_ALLOWLIST` mask before matching `\\bHH\\b`."""
    hits = []
    if not text:
        return hits
    # Mask HHI so it survives the HH matcher.
    masked = _HHI_ALLOWLIST.sub("HHIALLOW", text)
    for pat in _FORBIDDEN_TOKENS:
        m = re.search(pat, masked, flags=re.IGNORECASE)
        if m:
            hits.append(pat)
    if _EM_DASH in text:
        hits.append("em_dash")
    if _EN_DASH in text:
        hits.append("en_dash")
    return hits


def _canonical_demo_categories():
    return ("age", "gender", "ethnicity", "income")
def validate_bpiq_payload(payload: dict, *,
                          allow_round_counts: bool = False) -> list:
    """Lightweight sanity validator run on every BPIQ write.

    Enforces:
      * audience_size present, positive int ending 1-9.
      * every integer count in _INT_COUNT_FIELDS ends 1-9 (unless
        allow_round_counts=True).
      * demographic categories (age, gender, ethnicity, income) sum
        to 100 +/- 0.5 in both `pre` and `post` phases.
      * no forbidden vocab or em dashes in any string VALUE.

    Returns a list of violation dicts. Empty list = payload passes.
    Callers can raise BpiqWriteInvariantError on non-empty.

    Args
    ----
    allow_round_counts
        When True, skips the "ends 1-9" check (used for round-number
        fixtures during test setup only). Default False.
    """
    v: list = []
    if not isinstance(payload, dict):
        return [{"rule": "shape", "path": "$",
                 "message": "payload must be a dict"}]

    # audience_size present + messy.
    a = payload.get("audience_size")
    if not isinstance(a, (int, float)) or a <= 0:
        v.append({"rule": "audience_size",
                  "path": "audience_size",
                  "message": "audience_size must be a positive int"})
    elif not allow_round_counts and int(a) % 10 == 0:
        v.append({"rule": "audience_size_round",
                  "path": "audience_size",
                  "message": (
                      f"audience_size ({int(a):,}) ends in 0. Route "
                      "through ensure_messy_sample_size."
                  )})

    # Integer counts end 1-9.
    if not allow_round_counts:
        for path, val in _iter_int_counts(payload):
            if val > 0 and val % 10 == 0:
                v.append({
                    "rule": "count_round",
                    "path": path,
                    "message": (
                        f"{path} ({val:,}) ends in 0. See "
                        ".cursor/rules/no-round-numbers-in-deliverables.mdc."
                    ),
                })

    # Demographic sums.
    demos = payload.get("demographics") or {}
    for phase in ("pre", "post"):
        block = demos.get(phase) or {}
        for cat in _canonical_demo_categories():
            rows = block.get(cat)
            if not rows:
                continue
            total = sum(float(r.get("percentage") or 0) for r in rows)
            if abs(total - 100.0) > 0.5:
                v.append({
                    "rule": "demo_sum",
                    "path": f"demographics.{phase}.{cat}",
                    "message": (
                        f"demographics.{phase}.{cat} sums to {total:.2f}, "
                        "expected 100 (tolerance 0.5)."
                    ),
                })
    # Forbidden vocab / em dashes.
    for path, s in _iter_string_values(payload):
        # Skip strings inside allowlisted keys (last segment match).
        last_seg = path.rsplit(".", 1)[-1]
        if last_seg in _STRING_VALUE_ALLOWLIST:
            continue
        hits = _forbidden_hits(s)
        if hits:
            v.append({
                "rule": "forbidden_vocab",
                "path": path,
                "hits": hits,
                "message": (
                    f"forbidden vocab / punctuation at {path}: "
                    f"{', '.join(hits)}"
                ),
            })

    return v
# ---------------------------------------------------------------------
# Convenience writer wrapper
# ---------------------------------------------------------------------


def validate_before_write(
    payload: dict,
    *,
    parent_payload: Optional[dict] = None,
    cohort_fraction: Optional[float] = None,
) -> None:
    """One-shot pre-write validator. Runs the always-on sanity checks;
    when the payload carries `diagnostics.parent_payload_key` AND the
    caller supplies `parent_payload` + `cohort_fraction`, also runs
    the four subset invariants.

    Raises BpiqWriteInvariantError on any violation with a message
    that names the rule + violating path.
    """
    sanity = validate_bpiq_payload(payload)
    if sanity:
        first = sanity[0]
        raise BpiqWriteInvariantError(
            f"BPIQ payload failed sanity validator: rule={first['rule']} "
            f"path={first['path']} :: {first['message']} "
            f"(plus {len(sanity) - 1} more)"
        )
    if parent_payload is not None and cohort_fraction is not None:
        subset_violations = verify_subset_invariants(
            payload, parent_payload, cohort_fraction
        )
        if subset_violations:
            first = subset_violations[0]
            raise BpiqWriteInvariantError(
                f"BPIQ subset payload failed invariants: rule={first['rule']} "
                f"path={first['path']} :: {first['message']} "
                f"(plus {len(subset_violations) - 1} more)"
            )


__all__ = [
    "BpiqWriteInvariantError",
    "build_subset_payload",
    "resolve_observed_cohort_n",
    "resolve_projection_weight",
    "verify_subset_invariants",
    "enforce_shared_cohort_n",
    "validate_bpiq_payload",
    "validate_before_write",
]
