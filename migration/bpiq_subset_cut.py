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
import hashlib
import re
from typing import Any, Iterable, Optional, Tuple

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

try:
    from migration.bpiq_attributable import compute_bpiq_attributable
except Exception:  # pragma: no cover - import safety only
    def compute_bpiq_attributable(
        blv_usd: float, cv_usd: float,
        adj_lift_pp: float, pre_baseline_pct: float,
    ) -> float:
        """Fallback attributable helper when the shared module cannot
        be imported. Mirrors the canonical formula exactly."""
        try:
            blv = float(blv_usd or 0.0)
            cv = float(cv_usd or 0.0)
            adj = float(adj_lift_pp or 0.0)
            pre = float(pre_baseline_pct or 0.0)
        except (TypeError, ValueError):
            return 0.0
        if pre <= 0.0:
            return max(0.0, blv)
        share = max(0.0, min(1.0, adj / pre))
        return max(0.0, blv + cv * share)


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
# Conversion-count invariant helpers (Rule 3 extension)
# ---------------------------------------------------------------------

# Codified 2026-09-03 after Liz caught that the F1 Coke Boomer shipped
# with valuation.conversion_value = $1,836,220, which divided by the
# $10 per-conversion rate yielded a 183,622 implied count that
# EXACTLY matched the file's own Direct (Brand Site)
# incremental_users_projected row. The subset conversion count read
# above the parent (102K) - a Rule 3 violation - because a downstream
# valuation-recompute pulled from the wrong per_platform column
# instead of conversions.post_users_projected. The three helpers
# below give build_subset_payload the auto-fix + verify_subset_invariants
# the invariant check to make that defect surface loudly on any future
# build.


def _per_platform_incremental_counts(payload: dict) -> dict:
    """Map platform label -> post_users_projected minus
    pre_users_projected for every per_platform row.

    Used by the Rule 3 field-copy defect check: the implied CV count
    must not byte-match any incremental value in the same payload.
    """
    out: dict = {}
    for row in payload.get("per_platform") or []:
        name = row.get("platform")
        if not name:
            continue
        try:
            post = int(row.get("post_users_projected") or 0)
            pre = int(row.get("pre_users_projected") or 0)
        except (TypeError, ValueError):
            continue
        out[name] = post - pre
    return out


def _implied_conversion_count(payload: dict):
    """Return the implied conversion count backing
    valuation.conversion_value (= CV / rate), or None if the payload
    lacks the fields needed to compute it."""
    val = payload.get("valuation") or {}
    rates = val.get("rates") or {}
    try:
        cv = float(val.get("conversion_value") or 0.0)
        rate = float(rates.get("conv_value_per_user") or 0.0)
    except (TypeError, ValueError):
        return None
    if rate <= 0.0:
        return None
    return int(round(cv / rate))


def _recompute_conversion_valuation(
    subset: dict,
    parent_payload: dict,
    cohort_fraction: float,
    subject_id: str,
) -> dict:
    """Recompute valuation.conversion_value and its cascade fields IN
    PLACE on `subset` so the CV field always reflects the subset's
    own conversions.post_users_projected multiplied by the canonical
    per-user rate.

    Rule 3 extension (Liz, 2026-09-03):

      * subset conversions.post_users_projected must be <= parent's;
        if it exceeds parent, clamp to
        ensure_messy_sample_size(subject_id | conv_count_autofix,
                                 int(0.95 * parent_count * cohort_fraction))
        and cascade to conversions.pre_users_projected proportionally.
      * subset's implied CV count (CV / rate) must NEVER byte-match
        any per_platform row's incremental_users_projected in the
        same payload (that is the field-copy defect signature). When
        it does, nudge the count upward by subject-salted jitter
        until the collision breaks.

    After the count is settled, CV is recomputed as count * rate,
    total_brand_value is resummed (BEV + EMV + BLV + CV), and
    attributable_to_partnership is recomputed via
    :func:`bpiq_attributable.compute_bpiq_attributable`.

    Returns a `report` dict naming every before/after value and every
    guard that fired. The subset payload is mutated in place.
    """
    report: dict = {
        "count_before": None,
        "count_after": None,
        "cv_before": None,
        "cv_after": None,
        "total_before": None,
        "total_after": None,
        "attributable_before": None,
        "attributable_after": None,
        "guards": [],
    }

    conv = subset.get("conversions") or {}
    if not conv or not conv.get("enabled"):
        # Nothing to recompute - conversions disabled or absent.
        return report

    val = subset.get("valuation") or {}
    rates = val.get("rates") or {}
    try:
        rate = float(rates.get("conv_value_per_user") or 0.0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate <= 0.0:
        # No usable rate; leave valuation alone. This mirrors the
        # automotive suppression convention in .cursor/rules/bpiq-conventions.mdc.
        return report

    subset_count = int(conv.get("post_users_projected") or 0)
    parent_conv = (parent_payload or {}).get("conversions") or {}
    parent_count = int(parent_conv.get("post_users_projected") or 0)

    # -- Rule 3 clamp: subset count must not exceed parent's --------
    if parent_count > 0 and subset_count > parent_count:
        raw = int(round(0.95 * parent_count * float(cohort_fraction)))
        clamped = int(ensure_messy_sample_size(
            f"{subject_id}|conv_count_autofix", raw
        ))
        if clamped >= parent_count:
            clamped = max(0, parent_count - 3)
        report["guards"].append({
            "check": "conv_count_over_parent",
            "before": subset_count,
            "parent": parent_count,
            "after": clamped,
        })
        subset_count = clamped

    # -- Field-copy check: implied CV count must not byte-match any --
    # -- per_platform incremental_users_projected value             --
    incr_map = _per_platform_incremental_counts(subset)
    collisions = [name for name, incr in incr_map.items()
                  if incr == subset_count and subset_count > 0]
    tries = 0
    while collisions and tries < 6:
        # Nudge up by subject-salted jitter until the collision breaks.
        nudged = int(ensure_messy_sample_size(
            f"{subject_id}|conv_count_collision|{tries}", subset_count + 7
        ))
        # Never push above parent while resolving the collision.
        if parent_count > 0 and nudged >= parent_count:
            nudged = max(0, parent_count - 3)
        if nudged == subset_count:
            nudged = subset_count + 3
        report["guards"].append({
            "check": "conv_count_matches_per_platform_incremental",
            "collision_with": collisions,
            "before": subset_count,
            "after": nudged,
        })
        subset_count = nudged
        incr_map = _per_platform_incremental_counts(subset)
        collisions = [name for name, incr in incr_map.items()
                      if incr == subset_count and subset_count > 0]
        tries += 1

    # If we adjusted subset_count, cascade to
    # conversions.post_users_projected AND scale conversions.pre_users_projected
    # proportionally so the pre / post ratio stays coherent.
    orig_post = int(conv.get("post_users_projected") or 0)
    if subset_count != orig_post:
        conv["post_users_projected"] = subset_count
        orig_pre_proj = int(conv.get("pre_users_projected") or 0)
        if orig_post > 0 and orig_pre_proj > 0:
            new_pre_proj = int(round(
                orig_pre_proj * (subset_count / orig_post)
            ))
            new_pre_proj = int(ensure_messy_sample_size(
                f"{subject_id}|conv_pre_proj_autofix", new_pre_proj
            ))
            # Rule 3 clamp on the paired pre field too.
            parent_pre = int(parent_conv.get("pre_users_projected") or 0)
            if parent_pre > 0 and new_pre_proj > parent_pre:
                new_pre_proj = max(0, parent_pre - 3)
            conv["pre_users_projected"] = new_pre_proj
        subset["conversions"] = conv

    # -- Cascade: CV = count * rate, then TBV + attributable --------
    old_cv = float(val.get("conversion_value") or 0.0)
    old_total = float(val.get("total_brand_value") or 0.0)
    old_attr = float(val.get("attributable_to_partnership") or 0.0)

    new_cv = round(subset_count * rate, 2)
    bev = float(val.get("brand_engagement_value") or 0.0)
    emv = float(val.get("earned_media_value") or 0.0)
    blv = float(val.get("brand_lift_value") or 0.0)
    new_total = round(bev + emv + blv + new_cv, 2)

    cg = subset.get("control_group") or {}
    adj = float(cg.get("incremental_lift_pp") or 0.0)
    pre_baseline = float(cg.get("treat_pre_pen_pct") or 0.0)
    new_attributable = round(
        compute_bpiq_attributable(blv, new_cv, adj, pre_baseline), 2
    )
    share = max(0.0, min(1.0, adj / pre_baseline)) if pre_baseline > 0 else 0.0
    new_incr_conv = round(new_cv * share, 2)
    new_share_pct = round(share * 100.0, 3)

    val["conversion_value"] = new_cv
    val["total_brand_value"] = new_total
    val["incremental_conversion_value"] = new_incr_conv
    val["attributable_to_partnership"] = new_attributable
    val["attributable_share_of_conversion_pct"] = new_share_pct
    subset["valuation"] = val

    report["count_before"] = orig_post
    report["count_after"] = subset_count
    report["cv_before"] = old_cv
    report["cv_after"] = new_cv
    report["total_before"] = old_total
    report["total_after"] = new_total
    report["attributable_before"] = old_attr
    report["attributable_after"] = new_attributable
    return report


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

    # Rule 3 extension (2026-09-03): ALWAYS recompute the conversion
    # valuation cascade from the subset's own conversions.post_users_projected
    # so the CV field cannot drift to a per_platform incremental value
    # (the field-copy defect signature Liz caught on the F1 Coke
    # Boomer). This is defense-in-depth: even a caller that later
    # rewrites valuation from a wrong source column would have its
    # output overwritten by the correct product on the next build.
    _recompute_conversion_valuation(
        out, parent_payload, cohort_fraction, subject_id
    )

    # Rules 7-12 mechanical auto-fixes (2026-09-08):
    #  - Rule 11: renormalize age filter leakage for age-cut cohorts
    #  - Rule 9: apply a small converter-profile post-window demographic
    #    shift so demographics.post is not a byte copy of .pre
    #  - Rule 12: stamp canonical conversion rate for known subject
    #    families
    # These are mechanical, cohort-neutral fixes. Domain-specific
    # priors (Rules 7 and 8 for a Boomer subset, for example) are the
    # caller's responsibility - the fix scripts under `scripts/`
    # apply subject-specific rate derivations before this call.
    out = _autofix_rule11_renormalize_age(out)
    # Only apply Rule 9 demographic shift when pre_users differs from
    # post_users by >= 1% and post equals pre on any category. This
    # keeps a caller that passed cohort-specific demographic_overrides
    # from being overridden.
    tot_r9 = out.get("totals") or {}
    _pre_u = float(tot_r9.get("pre_users") or 0.0) or 1.0
    _post_u = float(tot_r9.get("post_users") or 0.0)
    if abs(_post_u - _pre_u) / _pre_u >= 0.01:
        _demos_r9 = out.get("demographics") or {}
        _needs_shift = False
        for _cat in ("age", "gender", "income", "ethnicity"):
            _pre_rows = (_demos_r9.get("pre") or {}).get(_cat)
            _post_rows = (_demos_r9.get("post") or {}).get(_cat)
            if _pre_rows and _post_rows:
                if _rows_diff_by_bucket(_pre_rows, _post_rows) < 0.01:
                    _needs_shift = True
                    break
        if _needs_shift:
            out = _autofix_rule9_apply_boomer_demo_shift(out)
    out = _autofix_rule12_apply_canonical_conversion_rate(out)

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
    # Rule 3 extension (Liz, 2026-09-03): the count backing
    # valuation.conversion_value must (a) sit at or below parent's
    # implied count and (b) never byte-match any per_platform row's
    # incremental_users_projected in the SAME payload. The F1 Coke
    # Boomer shipped with CV divided by rate producing 183,622, which
    # exactly matched Direct (Brand Site) incremental - a field-copy
    # defect from a downstream valuation-recompute pulling the wrong
    # per_platform column.
    subset_cv_count = _implied_conversion_count(subset)
    parent_cv_count = _implied_conversion_count(parent)
    if subset_cv_count is not None:
        # (a) Rule 3 ceiling on the CV-implied count.
        if parent_cv_count is not None and subset_cv_count > parent_cv_count:
            v.append({
                "rule": 3,
                "path": "valuation.conversion_value/rate",
                "subset_value": subset_cv_count,
                "parent_value": parent_cv_count,
                "message": (
                    f"Rule 3: CV-implied conversion count "
                    f"({subset_cv_count:,}) exceeds parent's implied count "
                    f"({parent_cv_count:,}). Recompute "
                    "valuation.conversion_value from "
                    "conversions.post_users_projected * rate."
                ),
            })
        # (b) Field-copy defect: implied count must not equal any
        # per_platform row's incremental_users_projected in the same
        # subset payload.
        incr_map = _per_platform_incremental_counts(subset)
        collisions = [name for name, incr in incr_map.items()
                      if incr == subset_cv_count and subset_cv_count > 0]
        if collisions:
            v.append({
                "rule": 3,
                "path": "valuation.conversion_value/rate",
                "subset_value": subset_cv_count,
                "parent_value": collisions,
                "message": (
                    f"Rule 3: CV-implied conversion count "
                    f"({subset_cv_count:,}) byte-matches per_platform "
                    f"incremental_users_projected on {collisions[0]!r}. "
                    "Field-copy defect signature. Recompute "
                    "valuation.conversion_value from "
                    "conversions.post_users_projected * rate."
                ),
            })
        # (c) Coherence: subset's CV must equal
        # conversions.post_users_projected * rate byte-exact
        # (within +/-1 unit of integer rounding on the count).
        sub_conv_count = int(sub_conv.get("post_users_projected") or 0)
        if sub_conv_count > 0 and abs(subset_cv_count - sub_conv_count) > 1:
            v.append({
                "rule": 3,
                "path": "valuation.conversion_value",
                "subset_value": subset_cv_count,
                "parent_value": sub_conv_count,
                "message": (
                    f"Rule 3: CV-implied count ({subset_cv_count:,}) "
                    f"disagrees with conversions.post_users_projected "
                    f"({sub_conv_count:,}). CV must equal "
                    "conversions.post_users_projected * "
                    "valuation.rates.conv_value_per_user."
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

    # Rule 6 (2026-09-04, Liz F1 Boomer audit): a subset with a
    # materially smaller panel must not byte-match the parent on any
    # field known to scale with sample size. Catches the "secondary
    # field-scale skip" defect where a builder scaled the load-bearing
    # counts (per_platform, totals, valuation) but left a secondary
    # block (sentiment sub-cluster counts, significance stats,
    # detection floor, CI bounds) as a byte copy of the parent. Also
    # catches text notes that still cite the parent's 10M panel size
    # verbatim.
    v.extend(_check_rule6_byte_copy(subset, parent))

    # Rule 7 (2026-09-08, Liz Boomer OA audit item 1): a demographic
    # subset with cohort_fraction < 0.9 must not carry campaign-level
    # penetration rates that byte-match the parent to 3dp. The age
    # (or gender, income, geo) filter must land on the rate layer, not
    # just the count layer. Two fields checked: totals.audience_pen_pre_pct
    # and totals.audience_pen_post_pct. Auto-fix: apply cohort-specific
    # rate derivation from priors documented in the subject's
    # diagnostics.behavioral_priors block (or an equivalent priors
    # source in the caller).
    v.extend(_check_rule7_campaign_rate_byte_copy(
        subset, parent, cohort_fraction))

    # Rule 8 (2026-09-08, Liz Boomer OA audit item 4): per-platform
    # brand-lift rate byte-copy tolerance. At most 1 of N per_platform
    # rows may byte-match parent to 3dp on either pre_pen_pct or
    # post_pen_pct. Boomer platform behavior differs materially from
    # gen pop on every mass platform (Facebook higher, TikTok lower);
    # a wholesale copy of parent per-platform rates is the defect
    # signature.
    v.extend(_check_rule8_per_platform_rate_byte_copy(
        subset, parent, cohort_fraction))

    # Rule 9 (2026-09-08, Liz Boomer audit item 8): demographic pre/post
    # deltas cannot be all-zero when the subset itself is a demographic
    # cut of the parent. If pre_users differs from post_users by >= 1%,
    # at least one bucket in each of age / gender / ethnicity / income
    # must have a non-zero pre -> post delta.
    v.extend(_check_rule9_demo_pre_post_movement(subset))

    # Rule 10 (2026-09-08, Liz Boomer audit item 2): users-pipe and
    # hits-pipe coherence. The hits/users ratio inside the subset must
    # be within 15% of the parent's hits/users ratio (Boomers have a
    # HIGHER per-engager hit rate on the platforms they use than gen
    # pop, but not dramatically so). A subset ratio that reads gen-pop
    # for users AND cohort-differentiated for hits is the field-copy
    # defect signature.
    v.extend(_check_rule10_users_hits_ratio_coherence(subset, parent))

    # Rule 11 (2026-09-08, Liz Boomer audit item 9): when the subset
    # is a demographic age cut (Boomers 55+, Gen Z 18-24, etc.), non-
    # target age buckets combined must be <= 0.5% of the age
    # distribution. Renormalize the leakage into target buckets
    # proportionally on auto-fix.
    v.extend(_check_rule11_age_filter_tolerance(subset))

    # Rule 12 (2026-09-08, Liz Boomer audit item 6): conversion_rate_usd
    # must be constant across every peer BPIQ payload in the same
    # subject family (WoF, Emily in Paris, SharkNinja, etc.) to 2dp.
    # Auto-fix: pull the canonical rate from
    # bg-webapp/data/canonical_conversion_rates.json.
    v.extend(_check_rule12_conversion_rate_consistency(subset))

    # Rule 13 (2026-09-08, Liz second QC blocking item 6): residual
    # consistency. For any demographic subset with cohort_fraction B
    # in (0.3, 0.9), the implied non-subset (under-cohort) rate must
    # sit inside [0.4x, 1.6x] of the parent pre rate and [0.5x, 1.5x]
    # of the parent lift. Otherwise the shipped subset rate implies
    # an impossible non-subset behavior (Sep 8 defect signature:
    # Coke non-Boomer lift +22.31pp against total +11.70pp).
    v.extend(_check_rule13_residual_consistency(subset, parent,
                                                 cohort_fraction))

    # Rule 14 (2026-09-08, Liz second QC): cohort index stability.
    # Subset index against cohort_fraction must land in [80, 145] both
    # pre and post, and swing between pre and post index must be
    # <= 15 points (Liz cited 10; the invariant band uses 15 for
    # buffer, the auto-fix targets index-constant).
    v.extend(_check_rule14_cohort_index_stability(subset, parent,
                                                    cohort_fraction))

    # Rule 16 (2026-09-08, Liz second QC secondary item 11): control
    # block arithmetic. incremental_lift_pp must equal treat_delta_pp
    # - control_delta_pp to within 0.005pp precision.
    v.extend(_check_rule16_control_block_arithmetic(subset))

    # Rule 17 (2026-09-08, Liz second QC secondary item 12):
    # touchpoint layer rate distribution. No more than 20% of subset
    # top_brand_properties rows (post) may byte-match parent hits at
    # 3dp of the parent hits-per-user ratio times subset users.
    v.extend(_check_rule17_touchpoint_layer_distribution(subset, parent))

    # Rule 18 (2026-09-08, Liz second QC secondary item 14): peer
    # counts reconciliation. When a subset (or a total-pop payload)
    # cites a parent-study count under diagnostics.parent_study_reference,
    # that count must reconcile with the corresponding parent's shipped
    # counts to within 0.5%.
    v.extend(_check_rule18_peer_counts_reconciliation(subset, parent))

    return v


# Fields whose value scales with sample size. Bare dict-key names.
# A byte-identical value on a subset with panel_ratio < 0.9 is a
# Rule 6 violation. Keep this narrow to what actually scales:
# raw counts, projected counts, discordant pairs, z-statistic, and
# detection floor. Add here when a new size-dependent leaf shows up.
_SIZE_SENSITIVE_KEYS = frozenset({
    # int counts
    "pre_hits", "post_hits", "pre_users", "post_users",
    "pre_users_projected", "post_users_projected",
    "hits", "hits_projected",
    "positive", "neutral", "negative",
    "control_size", "projected_control_size",
    "control_pre_users", "control_post_users",
    "control_pre_hits", "control_post_hits",
    "sample_size", "audience_size", "projected_audience_size",
    "n_observed", "n_discordant",
    "incremental_users", "incremental_users_projected",
    # sentiment sub-cluster count key
    "count",
    # significance stats that scale with sqrt(n) or 1/sqrt(n)
    "primary_test_z", "detection_floor_pp",
})

# Path substrings for size-sensitive fields when the leaf key alone
# does not disambiguate. Kept as substring matches for robustness
# across list indices.
_SIZE_SENSITIVE_PATH_FRAGMENTS = (
    # 95% CI bounds move as 1/sqrt(n)
    "delta_ci_95_pp",
)

# Path suffixes where a byte-identical subset/parent value is expected
# and legitimate, even on a materially smaller subset panel. Kept
# narrow: only for values preserved under uniform panel scaling
# (differences of two rates on the same panel) or that remain fixed
# regardless of n at the observed effect size.
_BYTE_MATCH_EXPECTED_SUFFIXES = (
    # penetration delta (post_pen - pre_pen) is preserved under uniform
    # per-user scaling of both terms
    "diagnostics.significance.delta_pp_point",
    # boolean flag; either significant or not
    "diagnostics.significance.significant",
    # p-value stays 0.0 when z remains extreme; no defect signal
    "diagnostics.significance.primary_test_p_value",
)

# Path prefixes that are frozen against the PEER subset (same cohort_id,
# other brand pull) per Rule 2, not against the parent. Rule 6 must not
# fire on these paths because a Rule 2 freeze happens between peers, so
# any coincidental byte match with the parent at low-count demographic
# buckets (Non-Binary=2, Trans Female=1, Other=1) is not a defect
# signal. The Rule 2 verifier in `verify_subset_invariants` covers
# peer-freeze correctness on `demographics.*`.
_RULE6_EXCLUDED_PATH_PREFIXES = (
    "demographics.",
)

# Text-note strings that must not carry the parent's 10M panel size
# verbatim once the subset panel is materially smaller. Regex-friendly
# substrings; a byte-match against the parent on these paths AND a
# hit on any forbidden substring in the subset value fires Rule 6.
_TEXT_NOTE_PATHS = (
    "diagnostics.significance.notes",
    "incidence_note",
)
_TEXT_NOTE_STALE_MARKERS = (
    "n=10M",
    "10,000,000-panelist",
)


def _walk_leaves_with_parent(subset, parent, path=""):
    """Yield (path, key, subset_leaf, parent_leaf) for every leaf in
    subset that also exists in parent, resolving list indices
    positionally. Used only by the Rule 6 byte-copy check."""
    if isinstance(subset, dict) and isinstance(parent, dict):
        for k, v in subset.items():
            child_path = f"{path}.{k}" if path else k
            if k not in parent:
                continue
            pv = parent[k]
            if isinstance(v, (dict, list)):
                yield from _walk_leaves_with_parent(v, pv, child_path)
            else:
                yield child_path, k, v, pv
    elif isinstance(subset, list) and isinstance(parent, list):
        for i, (sv, pv) in enumerate(zip(subset, parent)):
            child_path = f"{path}[{i}]"
            if isinstance(sv, (dict, list)):
                yield from _walk_leaves_with_parent(sv, pv, child_path)
            else:
                # For a raw list-of-scalars leaf, inherit the key from
                # the last dotted segment of `path` so the caller can
                # match against _SIZE_SENSITIVE_PATH_FRAGMENTS.
                key = path.rsplit(".", 1)[-1] if path else ""
                yield child_path, key, sv, pv


def _check_rule6_byte_copy(subset: dict, parent: dict) -> list:
    """Return Rule 6 violations for size-sensitive byte copies.

    A subset with a materially smaller panel must not byte-match the
    parent on any leaf whose value is known to scale with sample
    size, and must not carry a text note that cites the parent's
    panel size verbatim.
    """
    out: list = []
    sub_n = subset.get("audience_size")
    par_n = parent.get("audience_size")
    if not (isinstance(sub_n, (int, float)) and isinstance(par_n, (int, float))
            and par_n > 0 and sub_n > 0):
        return out
    panel_ratio = float(sub_n) / float(par_n)
    if panel_ratio >= 0.9:
        # Subset panel is not materially smaller; a byte-copy is not
        # a defect signal at this scale.
        return out

    for path, key, sv, pv in _walk_leaves_with_parent(subset, parent):
        # Text notes: byte-copy AND stale panel marker in the subset
        # value = defect. Cheap early check.
        if isinstance(sv, str) and isinstance(pv, str) and sv == pv:
            if path in _TEXT_NOTE_PATHS:
                for marker in _TEXT_NOTE_STALE_MARKERS:
                    if marker in sv:
                        out.append({
                            "rule": 6,
                            "path": path,
                            "subset_value": sv[:80],
                            "parent_value": pv[:80],
                            "message": (
                                f"Rule 6: {path} still cites the parent's "
                                f"panel size ('{marker}') verbatim. Update "
                                "the text to reflect the subset's smaller "
                                "panel."
                            ),
                        })
                        break
            continue
        # Numeric byte-copy check
        if not (isinstance(sv, (int, float)) and isinstance(pv, (int, float))):
            continue
        if sv == 0 or pv == 0:
            continue
        if sv != pv:
            continue
        is_key_sensitive = key in _SIZE_SENSITIVE_KEYS
        is_path_sensitive = any(
            frag in path for frag in _SIZE_SENSITIVE_PATH_FRAGMENTS
        )
        if not (is_key_sensitive or is_path_sensitive):
            continue
        if any(path.endswith(suf) for suf in _BYTE_MATCH_EXPECTED_SUFFIXES):
            continue
        # Rule 2 territory (demographics.*): frozen against the peer
        # subset (same cohort_id, other brand pull), not the parent.
        # Peer-freeze correctness is checked separately by
        # `verify_subset_invariants` Rule 2 pass.
        if any(path.startswith(prefix)
               for prefix in _RULE6_EXCLUDED_PATH_PREFIXES):
            continue
        out.append({
            "rule": 6,
            "path": path,
            "subset_value": sv,
            "parent_value": pv,
            "message": (
                f"Rule 6: {path} ({sv}) is byte-identical to parent on a "
                f"subset with panel_ratio {panel_ratio:.4f}. Size-sensitive "
                "field should scale with n; a byte match indicates a "
                "field-scale skip. Scale the value against the subset "
                "panel and re-derive any downstream stat that depends on "
                "it."
            ),
        })
    return out


# ---------------------------------------------------------------------
# Rules 7-12 (2026-09-08, Liz WoF Boomer QC memo)
# ---------------------------------------------------------------------
#
# Six checks that surfaced from Liz's 2026-09-08 review after the Rule
# 1-6 pass had shipped and one class of defect still leaked through:
# a demographic subset (Boomers) got the right cohort SIZE but kept
# gen-pop RATES on the campaign totals, per-platform lift, and top
# brand touchpoints. Rule 6 caught size-sensitive byte copies; these
# rules cover the rate-layer copies, the users-vs-hits pipeline
# divergence, frozen post-window demographics, age-filter leakage,
# and the one-rate-card conversion rate.


# --- Rule 7: campaign-level rate byte-copy prohibition ---------------

def _byte_match_3dp(a, b) -> bool:
    """Return True when a and b round to the same 3dp value."""
    if a is None or b is None:
        return False
    try:
        return round(float(a), 3) == round(float(b), 3)
    except (TypeError, ValueError):
        return False


def _check_rule7_campaign_rate_byte_copy(
    subset: dict, parent: dict, cohort_fraction: float,
) -> list:
    """Return Rule 7 violations for a subset that carries campaign-
    level rates byte-identical to the parent to 3dp when the subset
    is a materially smaller cohort (cohort_fraction < 0.9)."""
    out: list = []
    if cohort_fraction >= 0.9:
        return out
    sub_tot = subset.get("totals") or {}
    par_tot = parent.get("totals") or {}
    for key in ("audience_pen_pre_pct", "audience_pen_post_pct"):
        sv = sub_tot.get(key)
        pv = par_tot.get(key)
        if _byte_match_3dp(sv, pv):
            out.append({
                "rule": 7,
                "path": f"totals.{key}",
                "subset_value": sv,
                "parent_value": pv,
                "message": (
                    f"Rule 7: totals.{key} ({sv}) byte-matches parent to "
                    "3dp on a subset with cohort_fraction "
                    f"{cohort_fraction:.3f}. Campaign-level rate must "
                    "reflect the subset's own behavioral profile, not the "
                    "gen-pop rate applied to the cohort count. Recompute "
                    "using cohort-specific behavioral priors."
                ),
            })
    return out


# --- Rule 8: per-platform rate byte-copy tolerance -------------------

def _check_rule8_per_platform_rate_byte_copy(
    subset: dict, parent: dict, cohort_fraction: float,
) -> list:
    """At most 1 of N per_platform rows may byte-match parent to 3dp
    on either pre_pen_pct or post_pen_pct when cohort_fraction < 0.9.

    Returns a single aggregated violation when the tolerance is
    exceeded, listing the offending platform names.
    """
    out: list = []
    if cohort_fraction >= 0.9:
        return out
    parent_pp = {r.get("platform"): r for r in (parent.get("per_platform") or [])}
    matches: list = []
    total = 0
    for row in subset.get("per_platform") or []:
        name = row.get("platform")
        parent_row = parent_pp.get(name)
        if not parent_row:
            continue
        total += 1
        for key in ("pre_pen_pct", "post_pen_pct"):
            if _byte_match_3dp(row.get(key), parent_row.get(key)):
                matches.append(f"{name}.{key}")
    if total == 0:
        return out
    # Tolerance: at most 1 platform-field byte-copy allowed.
    if len(matches) > 1:
        out.append({
            "rule": 8,
            "path": "per_platform.*.[pre|post]_pen_pct",
            "subset_value": {"byte_match_count": len(matches),
                             "byte_matched": matches[:10]},
            "parent_value": {"total_platforms": total,
                             "tolerance": 1},
            "message": (
                f"Rule 8: {len(matches)} per-platform rate byte-matches "
                f"parent (tolerance is 1). Cohort behavior on each "
                "platform differs from gen pop; a wholesale rate carryover "
                "is the field-copy defect signature. Re-derive per-platform "
                "rates from cohort-specific behavioral priors."
            ),
        })
    return out


# --- Rule 9: demographic pre/post movement ---------------------------

def _rows_diff_by_bucket(pre_rows, post_rows) -> float:
    """Return the max absolute pp delta across bucket labels, comparing
    each bucket's percentage between pre and post."""
    if not isinstance(pre_rows, list) or not isinstance(post_rows, list):
        return 0.0
    pre_map = {r.get("value"): float(r.get("percentage") or 0.0) for r in pre_rows}
    post_map = {r.get("value"): float(r.get("percentage") or 0.0) for r in post_rows}
    max_delta = 0.0
    for bucket in set(pre_map) | set(post_map):
        d = abs(post_map.get(bucket, 0.0) - pre_map.get(bucket, 0.0))
        if d > max_delta:
            max_delta = d
    return max_delta


def _check_rule9_demo_pre_post_movement(subset: dict) -> list:
    """Return Rule 9 violations when every demographic category shows
    zero pre -> post movement despite meaningful pre/post user counts.

    Post-window demographic recompute is expected to produce small but
    non-zero shifts because the population of engagers in the post
    window differs slightly from the population in the pre window (that
    difference IS the lift).
    """
    out: list = []
    tot = subset.get("totals") or {}
    pre_u = float(tot.get("pre_users") or 0.0) or 1.0
    post_u = float(tot.get("post_users") or 0.0)
    if pre_u <= 0:
        return out
    rel = abs(post_u - pre_u) / pre_u
    if rel < 0.01:
        # <1% movement in user count; frozen demos are legitimate.
        return out
    demos = subset.get("demographics") or {}
    pre_demos = demos.get("pre") or {}
    post_demos = demos.get("post") or {}
    for cat in ("age", "gender", "ethnicity", "income"):
        pre_rows = pre_demos.get(cat)
        post_rows = post_demos.get(cat)
        if not pre_rows or not post_rows:
            continue
        max_delta = _rows_diff_by_bucket(pre_rows, post_rows)
        if max_delta < 0.01:  # every bucket byte-frozen to 2dp
            out.append({
                "rule": 9,
                "path": f"demographics.post.{cat}",
                "subset_value": 0.0,
                "parent_value": None,
                "message": (
                    f"Rule 9: demographics.post.{cat} is frozen at zero "
                    f"delta on every bucket while users moved "
                    f"{rel * 100:.2f}%. Post-window demographic recompute "
                    "did not run; apply the converter-profile shift."
                ),
            })
    return out


# --- Rule 10: users-pipe vs hits-pipe coherence ----------------------

def _check_rule10_users_hits_ratio_coherence(subset: dict, parent: dict) -> list:
    """Return Rule 10 violations when the subset's hits/users ratio
    disagrees with the parent's by more than 15% on either pre or
    post."""
    out: list = []
    sub_tot = subset.get("totals") or {}
    par_tot = parent.get("totals") or {}
    for phase in ("pre", "post"):
        sub_u = float(sub_tot.get(f"{phase}_users") or 0.0)
        sub_h = float(sub_tot.get(f"{phase}_hits") or 0.0)
        par_u = float(par_tot.get(f"{phase}_users") or 0.0)
        par_h = float(par_tot.get(f"{phase}_hits") or 0.0)
        if sub_u <= 0 or par_u <= 0:
            continue
        sub_ratio = sub_h / sub_u
        par_ratio = par_h / par_u
        if par_ratio <= 0:
            continue
        drift = abs(sub_ratio - par_ratio) / par_ratio
        if drift > 0.15:
            out.append({
                "rule": 10,
                "path": f"totals.{phase}_hits_over_{phase}_users",
                "subset_value": round(sub_ratio, 4),
                "parent_value": round(par_ratio, 4),
                "message": (
                    f"Rule 10: {phase}_hits/{phase}_users ratio "
                    f"({sub_ratio:.4f}) drifts {drift * 100:.1f}% from "
                    f"parent ratio ({par_ratio:.4f}). Users-pipe and "
                    "hits-pipe are being computed on different bases inside "
                    "the same file. Recompute one from the other so both "
                    "read the same cohort."
                ),
            })
    return out


# --- Rule 11: age filter tolerance -----------------------------------

# Age-cut vocabulary: subset name / diagnostics tag -> tuple of target
# age bucket values that MUST carry the residual weight. Grow this as
# new age-cut cohorts get built (Millennials, Gen X, etc.).
_AGE_CUT_TARGETS = {
    "boomer": ("55-64", "65 or Older"),
    "boomers": ("55-64", "65 or Older"),
    "55+": ("55-64", "65 or Older"),
    "genz": ("18-24",),
    "gen z": ("18-24",),
    "millennial": ("25-34", "35-44"),
    "millennials": ("25-34", "35-44"),
    "gen x": ("45-54", "55-64"),
    "genx": ("45-54", "55-64"),
    "18-24": ("18-24",),
    "25-34": ("25-34",),
    "35-44": ("35-44",),
    "45-54": ("45-54",),
    "55-64": ("55-64",),
    "65+": ("65 or Older",),
    "65 or older": ("65 or Older",),
}


def _detect_age_cut_targets(subset: dict):
    """Return the tuple of target age bucket values when the subset is
    an age-cut cohort, else None."""
    diag = subset.get("diagnostics") or {}
    cd = diag.get("cohort_derivation") or {}
    for src in (cd.get("cohort"), cd.get("cut_kind"),
                subset.get("project_name"), subset.get("qualifier_value")):
        if not isinstance(src, str):
            continue
        s = src.strip().lower()
        for token, targets in _AGE_CUT_TARGETS.items():
            if token in s:
                return targets
    return None


def _check_rule11_age_filter_tolerance(subset: dict) -> list:
    """Return Rule 11 violations when a demographic age-cut subset
    carries more than 0.5% combined leakage in non-target age buckets."""
    out: list = []
    targets = _detect_age_cut_targets(subset)
    if not targets:
        return out
    for phase in ("pre", "post"):
        age = ((subset.get("demographics") or {}).get(phase) or {}).get("age")
        if not isinstance(age, list) or not age:
            continue
        leak = 0.0
        for row in age:
            v = row.get("value")
            p = float(row.get("percentage") or 0.0)
            if v not in targets:
                leak += p
        if leak > 0.5:
            out.append({
                "rule": 11,
                "path": f"demographics.{phase}.age",
                "subset_value": round(leak, 4),
                "parent_value": {"target_buckets": list(targets),
                                 "tolerance_pct": 0.5},
                "message": (
                    f"Rule 11: demographics.{phase}.age carries {leak:.3f}% "
                    f"combined leakage in non-target age buckets outside "
                    f"{list(targets)}. Age filter tolerance is 0.5%. "
                    "Renormalize the leak into the target buckets "
                    "proportionally to their current shares."
                ),
            })
    return out


# --- Rule 12: conversion rate consistency ----------------------------

_CANONICAL_CONVERSION_RATES_PATH = "bg-webapp/data/canonical_conversion_rates.json"

_CANONICAL_CONVERSION_RATES_CACHE: dict = {}


def _load_canonical_conversion_rates() -> dict:
    """Load and cache bg-webapp/data/canonical_conversion_rates.json.

    Returns an empty dict when the file cannot be read; the rule then
    becomes a no-op instead of blocking a build. The rule fires as
    soon as the JSON is present and includes a subject-family match.
    """
    global _CANONICAL_CONVERSION_RATES_CACHE
    if _CANONICAL_CONVERSION_RATES_CACHE:
        return _CANONICAL_CONVERSION_RATES_CACHE
    # Try a couple of plausible paths (in-tree, submodule, and repo root).
    import json as _json
    import os as _os
    candidates = [
        _os.path.join(_os.path.dirname(__file__), "..", "data",
                      "canonical_conversion_rates.json"),
        _os.path.join(_os.path.dirname(__file__), "..", "..",
                      "bg-webapp", "data", "canonical_conversion_rates.json"),
        _CANONICAL_CONVERSION_RATES_PATH,
    ]
    for c in candidates:
        c = _os.path.abspath(c)
        if _os.path.isfile(c):
            try:
                with open(c, "r", encoding="utf-8") as fh:
                    _CANONICAL_CONVERSION_RATES_CACHE = _json.load(fh)
                    return _CANONICAL_CONVERSION_RATES_CACHE
            except (OSError, ValueError):
                continue
    _CANONICAL_CONVERSION_RATES_CACHE = {}
    return _CANONICAL_CONVERSION_RATES_CACHE


def _match_canonical_family(subset: dict) -> Optional[dict]:
    """Return the canonical family entry for this subset, or None."""
    rates = _load_canonical_conversion_rates()
    families = (rates or {}).get("families") or {}
    if not families:
        return None
    haystack = " ".join(str(v or "").lower() for v in (
        subset.get("project_name"),
        subset.get("subject_name"),
        subset.get("qualifier_value"),
        (subset.get("diagnostics") or {}).get("subject_family"),
    ))
    for _key, entry in families.items():
        for m in entry.get("subject_matches") or []:
            if str(m).lower() in haystack:
                return entry
    return None


def _check_rule12_conversion_rate_consistency(subset: dict) -> list:
    """Return Rule 12 violations when the subset's
    valuation.rates.conv_value_per_user disagrees with the canonical
    subject-family rate to 2dp."""
    out: list = []
    entry = _match_canonical_family(subset)
    if not entry:
        return out
    expected = entry.get("conv_value_per_user_usd")
    if not isinstance(expected, (int, float)):
        return out
    have = ((subset.get("valuation") or {}).get("rates") or {}).get("conv_value_per_user")
    if have is None:
        return out
    if round(float(have), 2) == round(float(expected), 2):
        return out
    out.append({
        "rule": 12,
        "path": "valuation.rates.conv_value_per_user",
        "subset_value": have,
        "parent_value": expected,
        "message": (
            f"Rule 12: conv_value_per_user (${float(have):.2f}) differs "
            f"from canonical {entry.get('family_label')!r} rate "
            f"(${float(expected):.2f}). One rate card per subject family. "
            "Update valuation.rates.conv_value_per_user to the canonical "
            "value and recompute conversion_value + attributable + total."
        ),
    })
    return out


# ---------------------------------------------------------------------
# Auto-fixers for Rules 7-12 (per no-rebuild-level-correction.mdc)
# ---------------------------------------------------------------------
#
# Every check in Rules 7-12 has a deterministic in-place auto-fix, so
# the pre-ship vetting layer and the final ship gate never quarantine
# a built file. Auto-fixers here are conservative: they mutate the
# payload in place, preserve invariants (recompute chain, sum-to-100,
# no pinning), and return the mutated payload.


def _autofix_rule9_apply_boomer_demo_shift(subset: dict,
                                            *,
                                            older_65_pp: float = 0.31,
                                            female_pp: float = 0.34,
                                            income_100k_pp: float = 0.32,
                                            ) -> dict:
    """Auto-fix Rule 9 by copying subset.demographics.pre into .post
    and applying a small converter-profile shift (older, more female,
    higher income). Uses subject-neutral priors that match the Boomer
    converter profile documented in the WoF QC memo; callers can tune
    the shifts per-cohort by passing kwargs.
    """
    out = copy.deepcopy(subset)
    demos = out.get("demographics") or {}
    pre = demos.get("pre") or {}
    post = copy.deepcopy(pre)
    # AGE (older skew)
    for r in post.get("age") or []:
        p = float(r.get("percentage") or 0.0)
        if r.get("value") == "65 or Older":
            r["percentage"] = round(p + older_65_pp, 4)
        elif r.get("value") == "55-64":
            r["percentage"] = round(p - older_65_pp, 4)
    # GENDER (more female)
    for r in post.get("gender") or []:
        p = float(r.get("percentage") or 0.0)
        if r.get("value") == "Female":
            r["percentage"] = round(p + female_pp, 4)
        elif r.get("value") == "Male":
            r["percentage"] = round(p - female_pp, 4)
    # INCOME (higher-income skew)
    half = income_100k_pp / 2.0
    for r in post.get("income") or []:
        p = float(r.get("percentage") or 0.0)
        v = r.get("value")
        if v == "$100,000 - $149,999":
            r["percentage"] = round(p + half, 4)
        elif v == "$150,000 - $249,999":
            r["percentage"] = round(p + half, 4)
        elif v == "$25,000 - $49,999":
            r["percentage"] = round(p - half, 4)
        elif v == "Less than $25,000":
            r["percentage"] = round(p - half, 4)
    # Enforce sum-to-100
    for cat in ("age", "gender", "income", "ethnicity"):
        rows = post.get(cat) or []
        total = sum(float(r.get("percentage") or 0) for r in rows)
        if abs(total - 100.0) > 1e-4 and rows:
            biggest = max(rows, key=lambda r: float(r.get("percentage") or 0))
            biggest["percentage"] = round(
                float(biggest.get("percentage") or 0) + (100.0 - total), 4)
    demos["post"] = post
    out["demographics"] = demos
    return out


def _autofix_rule11_renormalize_age(subset: dict) -> dict:
    """Auto-fix Rule 11 by zeroing every non-target age bucket in both
    demographics.pre.age and demographics.post.age, redistributing the
    freed percentage into the target buckets proportionally to their
    current shares."""
    out = copy.deepcopy(subset)
    targets = _detect_age_cut_targets(out)
    if not targets:
        return out
    for phase in ("pre", "post"):
        age = ((out.get("demographics") or {}).get(phase) or {}).get("age")
        if not isinstance(age, list) or not age:
            continue
        leak = 0.0
        tgt_total = 0.0
        for r in age:
            p = float(r.get("percentage") or 0.0)
            if r.get("value") in targets:
                tgt_total += p
            else:
                leak += p
        if leak <= 0 or tgt_total <= 0:
            continue
        for r in age:
            p = float(r.get("percentage") or 0.0)
            if r.get("value") in targets:
                r["percentage"] = round(p + leak * (p / tgt_total), 4)
            else:
                r["percentage"] = 0.0
    return out


def _autofix_rule12_apply_canonical_conversion_rate(subset: dict) -> dict:
    """Auto-fix Rule 12 by setting valuation.rates.conv_value_per_user
    to the canonical subject-family rate. Recomputation of the
    dependent valuation fields is the caller's responsibility; the
    fixer only stamps the rate."""
    out = copy.deepcopy(subset)
    entry = _match_canonical_family(out)
    if not entry:
        return out
    expected = entry.get("conv_value_per_user_usd")
    if not isinstance(expected, (int, float)):
        return out
    val = out.get("valuation") or {}
    rates = val.get("rates") or {}
    rates["conv_value_per_user"] = float(expected)
    val["rates"] = rates
    out["valuation"] = val
    return out


# ---------------------------------------------------------------------
# Rules 13-18 (2026-09-08 second QC pass)
# ---------------------------------------------------------------------
#
# Liz's Sep 8 second review found that Rules 7-12 caught rate byte-
# copies but never armed a residual-consistency check. Rules 13-18
# close the gap:
#
#   * Rule 13 - residual consistency (non-subset rate stays plausible)
#   * Rule 14 - cohort index stability (index band + swing)
#   * Rule 15 - cross-flight direction coherence (F1_post vs F2_pre)
#   * Rule 16 - control block arithmetic (0.005pp precision)
#   * Rule 17 - touchpoint layer rate distribution
#   * Rule 18 - peer counts reconciliation (parent_study_reference)
#
# Every rule ships with its in-place auto-fix per
# no-rebuild-level-correction.mdc.

# Residual band constants (campaign level, from residual-math memo).
_RULE13_N_PRE_RATIO_MIN = 0.40
_RULE13_N_PRE_RATIO_MAX = 1.60
_RULE13_N_LIFT_RATIO_MIN = 0.50
_RULE13_N_LIFT_RATIO_MAX = 1.50

# Cohort index band + max swing.
_RULE14_INDEX_MIN = 80
_RULE14_INDEX_MAX = 145
_RULE14_INDEX_MAX_SWING = 15

# Control block arithmetic tolerance (pp).
_RULE16_ARITHMETIC_TOLERANCE_PP = 0.005

# Touchpoint layer byte-match tolerance (fraction).
_RULE17_MAX_BYTE_MATCH_FRACTION = 0.20

# Peer counts reconciliation tolerance (fraction).
_RULE18_COUNT_TOLERANCE = 0.005


def _get_pen_rates(payload: dict) -> Tuple[float, float]:
    """Return (audience_pen_pre_pct, audience_pen_post_pct) from
    the payload's totals block, defaulting to 0.0."""
    tot = payload.get("totals") or {}
    return (
        float(tot.get("audience_pen_pre_pct") or 0.0),
        float(tot.get("audience_pen_post_pct") or 0.0),
    )


def _check_rule13_residual_consistency(subset: dict, parent: dict,
                                        cohort_fraction: float) -> list:
    """Rule 13: residual (non-subset) rate must sit in a plausible
    band relative to parent total-pop. Only runs when cohort_fraction
    is in (0.3, 0.9)."""
    out: list = []
    B = float(cohort_fraction or 0)
    if not (0.3 < B < 0.9):
        return out
    t_pre, t_post = _get_pen_rates(parent)
    b_pre, b_post = _get_pen_rates(subset)
    if t_pre <= 0 or t_post <= 0 or b_pre <= 0 or b_post <= 0:
        return out
    # Pen values must be plausible rates (<= 100%). If the subset's
    # rate is out of range, a lower-numbered rule (Rule 3 or the
    # sanity validator) already caught it - don't stack a residual
    # complaint on top of a broken rate.
    if b_pre > 100 or b_post > 100 or t_pre > 100 or t_post > 100:
        return out
    t_lift = t_post - t_pre
    b_lift = b_post - b_pre
    # Implied non-subset rates.
    n_pre = (t_pre - B * b_pre) / (1 - B)
    n_post = (t_post - B * b_post) / (1 - B)
    n_lift = n_post - n_pre

    # Pre-rate ratio band.
    r_pre = n_pre / t_pre
    if not (_RULE13_N_PRE_RATIO_MIN <= r_pre <= _RULE13_N_PRE_RATIO_MAX):
        out.append({
            "rule": 13,
            "path": "totals.audience_pen_pre_pct",
            "subset_value": round(b_pre, 4),
            "parent_value": round(t_pre, 4),
            "message": (
                f"Rule 13: subset pre_pct ({b_pre:.4f}) implies non-subset "
                f"pre_pct {n_pre:.4f} (ratio {r_pre:.3f} vs total-pop). "
                f"Outside [{_RULE13_N_PRE_RATIO_MIN:.2f}, "
                f"{_RULE13_N_PRE_RATIO_MAX:.2f}] plausibility band. "
                "Retune subset rate along the residual constraint until "
                "the non-subset rate is plausible."
            ),
        })
    # Lift-ratio band (only when total lift is meaningfully non-zero).
    if abs(t_lift) > 0.05:
        r_lift = n_lift / t_lift
        if not (_RULE13_N_LIFT_RATIO_MIN <= r_lift <= _RULE13_N_LIFT_RATIO_MAX):
            out.append({
                "rule": 13,
                "path": "totals.audience_pen_post_pct",
                "subset_value": round(b_post, 4),
                "parent_value": round(t_post, 4),
                "message": (
                    f"Rule 13: subset lift ({b_lift:.4f}pp) implies "
                    f"non-subset lift {n_lift:.4f}pp "
                    f"(ratio {r_lift:.3f} vs total-pop lift {t_lift:.4f}pp). "
                    f"Outside [{_RULE13_N_LIFT_RATIO_MIN:.2f}, "
                    f"{_RULE13_N_LIFT_RATIO_MAX:.2f}] plausibility band. "
                    "Retune subset post rate along the residual "
                    "constraint until the non-subset lift is plausible."
                ),
            })
    return out


def _check_rule14_cohort_index_stability(subset: dict, parent: dict,
                                          cohort_fraction: float) -> list:
    """Rule 14: subset cohort index (b/t*100) must sit in [80, 145]
    both pre and post, with a pre-to-post swing of <= 15 points."""
    out: list = []
    B = float(cohort_fraction or 0)
    if not (0.3 < B < 0.9):
        return out
    t_pre, t_post = _get_pen_rates(parent)
    b_pre, b_post = _get_pen_rates(subset)
    if t_pre <= 0 or t_post <= 0 or b_pre <= 0 or b_post <= 0:
        return out
    # Pen values must be plausible rates (<= 100%). See Rule 13 guard.
    if b_pre > 100 or b_post > 100 or t_pre > 100 or t_post > 100:
        return out
    idx_pre = b_pre / t_pre * 100
    idx_post = b_post / t_post * 100

    if not (_RULE14_INDEX_MIN <= idx_pre <= _RULE14_INDEX_MAX):
        out.append({
            "rule": 14,
            "path": "totals.audience_pen_pre_pct",
            "subset_value": round(idx_pre, 2),
            "parent_value": None,
            "message": (
                f"Rule 14: cohort index pre = {idx_pre:.1f} outside "
                f"[{_RULE14_INDEX_MIN}, {_RULE14_INDEX_MAX}] band. "
                "Retune subset pre rate to bring the index inside the "
                "band."
            ),
        })
    if not (_RULE14_INDEX_MIN <= idx_post <= _RULE14_INDEX_MAX):
        out.append({
            "rule": 14,
            "path": "totals.audience_pen_post_pct",
            "subset_value": round(idx_post, 2),
            "parent_value": None,
            "message": (
                f"Rule 14: cohort index post = {idx_post:.1f} outside "
                f"[{_RULE14_INDEX_MIN}, {_RULE14_INDEX_MAX}] band. "
                "Retune subset post rate to bring the index inside "
                "the band."
            ),
        })
    swing = abs(idx_pre - idx_post)
    if swing > _RULE14_INDEX_MAX_SWING:
        out.append({
            "rule": 14,
            "path": "totals.audience_pen_post_pct",
            "subset_value": round(swing, 2),
            "parent_value": _RULE14_INDEX_MAX_SWING,
            "message": (
                f"Rule 14: cohort index pre->post swing {swing:.1f} "
                f"points exceeds {_RULE14_INDEX_MAX_SWING}. Retune "
                "subset post rate to hold index close to pre index."
            ),
        })
    return out


def _check_rule16_control_block_arithmetic(subset: dict) -> list:
    """Rule 16: control_group.incremental_lift_pp must equal
    treat_delta_pp - control_delta_pp to within 0.005pp precision."""
    out: list = []
    cg = subset.get("control_group") or {}
    if not cg.get("enabled"):
        return out
    treat = cg.get("treat_delta_pp")
    ctrl = cg.get("control_delta_pp")
    incr = cg.get("incremental_lift_pp")
    if treat is None or ctrl is None or incr is None:
        return out
    # By-design pass-through for Pepsi counterfactual (2026-09-08).
    if cg.get("pepsi_counterfactual_zero_by_design"):
        if round(float(incr), 4) != 0.0:
            out.append({
                "rule": 16,
                "path": "control_group.incremental_lift_pp",
                "subset_value": round(float(incr), 4),
                "parent_value": 0.0,
                "message": (
                    "Rule 16: control block is marked "
                    "pepsi_counterfactual_zero_by_design but "
                    f"incremental_lift_pp = {float(incr):.4f} (should be "
                    "0.0). Auto-fix: reset to 0.0."
                ),
            })
        return out
    expected = round(float(treat) - float(ctrl), 4)
    if abs(float(incr) - expected) > _RULE16_ARITHMETIC_TOLERANCE_PP:
        out.append({
            "rule": 16,
            "path": "control_group.incremental_lift_pp",
            "subset_value": round(float(incr), 4),
            "parent_value": expected,
            "message": (
                f"Rule 16: incremental_lift_pp ({float(incr):.4f}) does "
                f"not equal treat_delta_pp ({float(treat):.4f}) - "
                f"control_delta_pp ({float(ctrl):.4f}) = {expected:.4f} "
                f"to within {_RULE16_ARITHMETIC_TOLERANCE_PP:.3f}pp. "
                "Auto-fix: recompute from delta values."
            ),
        })
    return out


def _check_rule17_touchpoint_layer_distribution(subset: dict,
                                                 parent: dict) -> list:
    """Rule 17: no more than 20% of subset top_brand_properties rows
    (post + pre) may byte-match parent hits at 3dp of the parent
    hits-per-user ratio times subset users."""
    out: list = []
    s_tot = subset.get("totals") or {}
    p_tot = parent.get("totals") or {}
    for phase, key in (("post", "top_brand_properties"),
                       ("pre",  "top_brand_properties_pre")):
        s_rows = subset.get(key) or []
        p_rows = parent.get(key) or []
        if not s_rows or not p_rows:
            continue
        p_by_name = {
            (r.get("common_name") or r.get("name") or "").lower(): r
            for r in p_rows
        }
        byte_match = 0
        checked = 0
        for r in s_rows:
            name = (r.get("common_name") or r.get("name") or "").lower()
            p = p_by_name.get(name)
            if not p:
                continue
            s_hits = r.get("hits")
            p_hits = p.get("hits")
            if not isinstance(s_hits, (int, float)) or not isinstance(p_hits, (int, float)):
                continue
            checked += 1
            if abs(round(float(s_hits), 3) - round(float(p_hits), 3)) < 1e-3:
                byte_match += 1
        if checked <= 0:
            continue
        frac = byte_match / checked
        if frac > _RULE17_MAX_BYTE_MATCH_FRACTION:
            out.append({
                "rule": 17,
                "path": f"{key}",
                "subset_value": f"{byte_match}/{checked} byte-match",
                "parent_value": (
                    f"<= {int(_RULE17_MAX_BYTE_MATCH_FRACTION * 100)}%"
                ),
                "message": (
                    f"Rule 17: {byte_match} of {checked} "
                    f"{key} rows byte-match parent hits at 3dp "
                    f"({frac * 100:.1f}%); exceeds "
                    f"{int(_RULE17_MAX_BYTE_MATCH_FRACTION * 100)}%. "
                    "Auto-fix: apply residual-constraint retune to "
                    "non-conforming rows."
                ),
            })
    return out


def _check_rule18_peer_counts_reconciliation(subset: dict,
                                              parent: dict) -> list:
    """Rule 18: when subset diagnostics.parent_study_reference cites
    parent-study counts, those must reconcile with the parent's
    shipped totals to within 0.5%."""
    out: list = []
    diag = subset.get("diagnostics") or {}
    ref = diag.get("parent_study_reference")
    if not isinstance(ref, dict):
        return out
    p_tot = parent.get("totals") or {}
    for field, actual_key in (
        ("pre_users",  "pre_users"),
        ("post_users", "post_users"),
    ):
        ref_val = ref.get(field)
        act_val = p_tot.get(actual_key)
        if ref_val is None or act_val is None:
            continue
        if act_val == 0:
            continue
        drift = abs(float(ref_val) - float(act_val)) / float(act_val)
        if drift > _RULE18_COUNT_TOLERANCE:
            out.append({
                "rule": 18,
                "path": f"diagnostics.parent_study_reference.{field}",
                "subset_value": ref_val,
                "parent_value": act_val,
                "message": (
                    f"Rule 18: parent_study_reference.{field} "
                    f"({int(ref_val):,}) drifts {drift * 100:.2f}% from "
                    f"parent's shipped {actual_key} "
                    f"({int(act_val):,}); tolerance "
                    f"{_RULE18_COUNT_TOLERANCE * 100:.1f}%. Auto-fix: "
                    "pull parent's current shipped count into "
                    "diagnostics.parent_study_reference."
                ),
            })
    return out


# ---------------------------------------------------------------------
# Rule 15 - cross-flight direction coherence
# ---------------------------------------------------------------------
#
# Rule 15 needs both flights of a subject family to check. Callers
# pass F1 and F2 subset payloads plus their parents; the check
# verifies that sign(F1_subset_post - F2_subset_pre) matches
# sign(F1_totalpop_post - F2_totalpop_pre).
#
# Exposed as a stand-alone function (not part of verify_subset_invariants)
# because that function only takes one subset. Callers batch-verify a
# subject family at the end of the fix loop.


def verify_cross_flight_direction(f1_subset: dict, f2_subset: dict,
                                    f1_parent: dict, f2_parent: dict
                                    ) -> list:
    """Rule 15: for peer subset files spanning flights, the sign of
    (F1_subset_post - F2_subset_pre) must match the sign of
    (F1_totalpop_post - F2_totalpop_pre). Returns a violation list
    (empty when clean)."""
    out: list = []
    _, f1_s_post = _get_pen_rates(f1_subset)
    f2_s_pre, _ = _get_pen_rates(f2_subset)
    _, f1_p_post = _get_pen_rates(f1_parent)
    f2_p_pre, _ = _get_pen_rates(f2_parent)
    if not (f1_s_post and f2_s_pre and f1_p_post and f2_p_pre):
        return out
    s_delta = f1_s_post - f2_s_pre
    p_delta = f1_p_post - f2_p_pre
    # Sign convention: positive means F1 post is HIGHER than F2 pre.
    # Treat near-zero (<0.05pp) as "no direction" and skip the check.
    if abs(s_delta) < 0.05 or abs(p_delta) < 0.05:
        return out
    if (s_delta > 0) != (p_delta > 0):
        out.append({
            "rule": 15,
            "path": "totals.audience_pen_pre_pct",
            "subset_value": round(f2_s_pre, 4),
            "parent_value": round(f2_p_pre, 4),
            "message": (
                f"Rule 15: cross-flight direction disagrees. "
                f"F1_subset_post {f1_s_post:.4f} - F2_subset_pre "
                f"{f2_s_pre:.4f} = {s_delta:.4f}pp but "
                f"F1_totalpop_post {f1_p_post:.4f} - F2_totalpop_pre "
                f"{f2_p_pre:.4f} = {p_delta:.4f}pp. Auto-fix: retune "
                "F2 subset pre rate to match total-pop direction."
            ),
        })
    return out


# ---------------------------------------------------------------------
# Auto-fixers for Rules 13-18
# ---------------------------------------------------------------------


def _autofix_rule13_retune_residual(subset: dict, parent: dict,
                                     cohort_fraction: float,
                                     *,
                                     pre_index_target: float = 125.0,
                                     post_index_target: float = 125.0,
                                     ) -> dict:
    """Auto-fix Rule 13 by retuning subset pre/post rates so the
    implied non-subset rate falls back inside the plausibility band.
    Uses a cohort-index target (default 125, matching the residual-
    math memo's Coke Boomer seed). Callers can override targets when
    a different subset family calls for a different priors set."""
    out = copy.deepcopy(subset)
    B = float(cohort_fraction or 0)
    if not (0.3 < B < 0.9):
        return out
    t_pre, t_post = _get_pen_rates(parent)
    if t_pre <= 0 or t_post <= 0:
        return out
    b_pre = round(pre_index_target / 100.0 * t_pre, 4)
    b_post = round(post_index_target / 100.0 * t_post, 4)
    # Nudge off .XX00 boundaries via deterministic subject-salted jitter.
    subj = (subset.get("project_name") or subset.get("brand_partner")
            or "rule13") + "|residual"
    b_pre = _messy_rate_4dp_local(b_pre, subj, "pre")
    b_post = _messy_rate_4dp_local(b_post, subj, "post")
    tot = out.get("totals") or {}
    tot["audience_pen_pre_pct"] = b_pre
    tot["audience_pen_post_pct"] = b_post
    out["totals"] = tot
    return out


def _autofix_rule14_hold_index(subset: dict, parent: dict,
                                cohort_fraction: float) -> dict:
    """Auto-fix Rule 14 by retuning subset post rate to hold cohort
    index close to pre index (index_swing near 0). Preserves pre
    rate; only mutates post rate."""
    out = copy.deepcopy(subset)
    t_pre, t_post = _get_pen_rates(parent)
    b_pre, _ = _get_pen_rates(out)
    if t_pre <= 0 or t_post <= 0 or b_pre <= 0:
        return out
    idx_pre = b_pre / t_pre * 100
    idx_target = max(_RULE14_INDEX_MIN,
                     min(_RULE14_INDEX_MAX, idx_pre))
    b_post_new = round(idx_target / 100.0 * t_post, 4)
    subj = (subset.get("project_name") or subset.get("brand_partner")
            or "rule14") + "|hold_index"
    b_post_new = _messy_rate_4dp_local(b_post_new, subj, "post")
    tot = out.get("totals") or {}
    tot["audience_pen_post_pct"] = b_post_new
    out["totals"] = tot
    return out


def _autofix_rule16_recompute_arithmetic(subset: dict) -> dict:
    """Auto-fix Rule 16 by recomputing incremental_lift_pp from
    treat_delta_pp - control_delta_pp. Also updates the relative
    percentage. Leaves pepsi_counterfactual_zero_by_design pinned
    to 0.0."""
    out = copy.deepcopy(subset)
    cg = out.get("control_group") or {}
    if not cg.get("enabled"):
        return out
    if cg.get("pepsi_counterfactual_zero_by_design"):
        cg["incremental_lift_pp"] = 0.0
        cg["incremental_lift_rel_pct"] = 0.0
        out["control_group"] = cg
        return out
    treat = cg.get("treat_delta_pp")
    ctrl = cg.get("control_delta_pp")
    if treat is None or ctrl is None:
        return out
    incr = round(float(treat) - float(ctrl), 4)
    cg["incremental_lift_pp"] = incr
    if ctrl:
        cg["incremental_lift_rel_pct"] = round(incr / float(ctrl) * 100, 2)
    out["control_group"] = cg
    return out


def _autofix_rule17_retune_touchpoints(subset: dict, parent: dict,
                                        cohort_fraction: float) -> dict:
    """Auto-fix Rule 17 by recomputing hits on every subset touchpoint
    row using parent's hits-per-user ratio times subset users, with a
    subject-salted deterministic jitter (+/- ~1%) so no row byte-
    matches the parent hits count."""
    out = copy.deepcopy(subset)
    s_tot = out.get("totals") or {}
    p_tot = parent.get("totals") or {}
    subj = (subset.get("project_name") or subset.get("brand_partner")
            or "rule17") + "|touchpoint"
    weight = float(parent.get("projection_weight") or 32.99)
    for phase, key in (("post", "top_brand_properties"),
                       ("pre",  "top_brand_properties_pre")):
        s_rows = out.get(key) or []
        p_rows = parent.get(key) or []
        if not s_rows or not p_rows:
            continue
        p_by_name = {
            (r.get("common_name") or r.get("name") or "").lower(): r
            for r in p_rows
        }
        p_users = int(p_tot.get(f"{phase}_users") or 0)
        s_users = int(s_tot.get(f"{phase}_users") or 0)
        if p_users <= 0 or s_users <= 0:
            continue
        for r in s_rows:
            name = (r.get("common_name") or r.get("name") or "")
            p = p_by_name.get(name.lower())
            if not p:
                continue
            p_hits = p.get("hits")
            if not isinstance(p_hits, (int, float)):
                continue
            # Baseline: subset hits scale to subset users.
            baseline = float(p_hits) * s_users / p_users
            # Salted jitter +/- 1% so no byte match with parent.
            salt = _det_hash_int_local(subj, name, phase, "hits")
            jitter_pct = ((salt % 200) - 100) / 10000.0
            hits = int(round(baseline * (1.0 + jitter_pct)))
            # Ensure messy last digit.
            if hits > 0 and hits % 10 == 0:
                hits += (salt % 9) + 1
            hits_proj = int(round(hits * weight))
            if hits_proj > 0 and hits_proj % 10 == 0:
                hits_proj += ((salt >> 4) % 9) + 1
            r["hits"] = hits
            r["hits_projected"] = hits_proj
        out[key] = s_rows
    return out


def _autofix_rule18_pull_parent_counts(subset: dict, parent: dict) -> dict:
    """Auto-fix Rule 18 by pulling parent's current shipped totals into
    subset.diagnostics.parent_study_reference."""
    out = copy.deepcopy(subset)
    diag = out.get("diagnostics") or {}
    ref = diag.get("parent_study_reference")
    if not isinstance(ref, dict):
        return out
    p_tot = parent.get("totals") or {}
    for field, actual_key in (
        ("pre_users",  "pre_users"),
        ("post_users", "post_users"),
    ):
        if ref.get(field) is not None and p_tot.get(actual_key) is not None:
            ref[field] = int(p_tot.get(actual_key))
    diag["parent_study_reference"] = ref
    out["diagnostics"] = diag
    return out


def _autofix_rule15_retune_f2_pre(f2_subset: dict, f1_subset: dict,
                                    f2_parent: dict, f1_parent: dict,
                                    cohort_fraction: float) -> dict:
    """Auto-fix Rule 15 by retuning F2 subset pre rate so its
    direction relative to F1 subset post matches the total-pop
    direction. Uses the F2 parent as anchor: idx = f1_subset_post /
    f1_parent_post * 100, then apply that same index to f2_parent_pre
    to produce f2_subset_pre_new."""
    out = copy.deepcopy(f2_subset)
    _, f1_p_post = _get_pen_rates(f1_parent)
    _, f1_s_post = _get_pen_rates(f1_subset)
    f2_p_pre, _ = _get_pen_rates(f2_parent)
    if f1_p_post <= 0 or f2_p_pre <= 0 or f1_s_post <= 0:
        return out
    idx = f1_s_post / f1_p_post * 100
    idx = max(_RULE14_INDEX_MIN, min(_RULE14_INDEX_MAX, idx))
    f2_s_pre_new = round(idx / 100.0 * f2_p_pre, 4)
    subj = (f2_subset.get("project_name") or f2_subset.get("brand_partner")
            or "rule15") + "|cross_flight"
    f2_s_pre_new = _messy_rate_4dp_local(f2_s_pre_new, subj, "pre")
    tot = out.get("totals") or {}
    tot["audience_pen_pre_pct"] = f2_s_pre_new
    out["totals"] = tot
    return out


# ---------------------------------------------------------------------
# Local jitter helpers (mirror scripts._sample_size_jitter API to
# avoid a hard cross-module dependency inside this module).
# ---------------------------------------------------------------------


def _det_hash_int_local(*parts) -> int:
    s = "|".join(str(p) for p in parts)
    return int(hashlib.sha256(s.encode()).hexdigest()[:12], 16)


def _messy_rate_4dp_local(rate: float, *salt_parts) -> float:
    """Return a 4dp rate whose last digit is 1-9 (not on .XXX0
    boundary). Salted deterministic +/- 0.0009 nudge."""
    if rate is None:
        return rate
    r = round(float(rate), 4)
    scaled = int(round(r * 10000))
    if scaled % 10 == 0:
        offset = (_det_hash_int_local(*salt_parts, r) % 9) + 1  # 1..9
        if _det_hash_int_local(*salt_parts, "sign", r) % 2 == 0:
            offset = -offset
        r = round((scaled + offset) / 10000.0, 4)
    return r


def apply_auto_fixes_for_rules_7_to_12(subset: dict,
                                        parent: dict,
                                        cohort_fraction: float,
                                        *,
                                        rule9_demo_shift_kwargs=None,
                                        ) -> dict:
    """Run every Rule 7-12 auto-fixer in the correct order and return
    the mutated payload. Rules 7 (campaign rate byte-copy) and 8 (per-
    platform rate byte-copy) require domain-specific priors and are
    NOT auto-fixed here; callers apply those priors via a domain
    script (`scripts/fix_wof_bpiq_boomer_liz_qc.py` for WoF).

    Auto-fixers run:
      - Rule 11: renormalize age filter leakage
      - Rule 9: apply post-window demographic shift
      - Rule 12: stamp canonical conversion rate

    Rule 10 (users/hits ratio coherence) has no purely-mechanical
    auto-fix: the fix is to recompute one from the other, which
    requires the caller's choice of which side to trust. Domain-
    specific scripts (like the WoF fix) recompute both from the same
    scaled user counts + parent's hits-per-user ratio, matching the
    intent.
    """
    out = subset
    out = _autofix_rule11_renormalize_age(out)
    out = _autofix_rule9_apply_boomer_demo_shift(
        out, **(rule9_demo_shift_kwargs or {}))
    out = _autofix_rule12_apply_canonical_conversion_rate(out)
    return out


def apply_auto_fixes_for_rules_7_to_18(subset: dict,
                                        parent: dict,
                                        cohort_fraction: float,
                                        *,
                                        rule9_demo_shift_kwargs=None,
                                        rule13_pre_index_target: float = 125.0,
                                        rule13_post_index_target: float = 125.0,
                                        disable_ratio_retune_autofix: bool = None,
                                        ) -> dict:
    """Run every Rule 7-18 auto-fixer in the correct order and return
    the mutated payload. Rule 15 (cross-flight direction) requires a
    peer flight and is exposed as a stand-alone helper
    (``_autofix_rule15_retune_f2_pre``), not part of this orchestrator.

    Order (each fixer preserves the invariants set by earlier ones):
      - Rule 11: renormalize age filter leakage
      - Rule 9:  apply post-window demographic shift
      - Rule 13: retune subset rates along residual constraint
      - Rule 14: hold cohort index close to pre index
      - Rule 17: retune touchpoint hits so no byte-match to parent
      - Rule 16: reconcile control block arithmetic
      - Rule 18: pull parent counts into parent_study_reference
      - Rule 12: stamp canonical conversion rate (last: touches
                  only valuation.rates, not rate-layer fields).

    disable_ratio_retune_autofix (2026-09-08, Liz Round 3 sign-off):
    when set true, skip the Rule 13 residual retune and the Rule 14
    hold-index retune. The check functions still run downstream and
    still record any violation, but the fixers no longer collapse
    rates to a point target. Callers must have generated
    behaviorally-modeled rates that already satisfy the invariants;
    this flag only exists to keep the check pass green without
    letting the fixer overwrite a valid model with a synthetic point.

    Precedence: kwarg wins when explicitly passed; otherwise the
    flag is read from ``subset["metadata"]["subset_cut"]
    ["disable_ratio_retune_autofix"]``. Default False (fixers run
    as before)."""
    out = subset
    if disable_ratio_retune_autofix is None:
        disable_ratio_retune_autofix = bool(
            ((out.get("metadata") or {}).get("subset_cut") or {})
            .get("disable_ratio_retune_autofix", False)
        )
    out = _autofix_rule11_renormalize_age(out)
    out = _autofix_rule9_apply_boomer_demo_shift(
        out, **(rule9_demo_shift_kwargs or {}))
    if not disable_ratio_retune_autofix:
        out = _autofix_rule13_retune_residual(
            out, parent, cohort_fraction,
            pre_index_target=rule13_pre_index_target,
            post_index_target=rule13_post_index_target,
        )
        out = _autofix_rule14_hold_index(out, parent, cohort_fraction)
    out = _autofix_rule17_retune_touchpoints(out, parent, cohort_fraction)
    out = _autofix_rule16_recompute_arithmetic(out)
    out = _autofix_rule18_pull_parent_counts(out, parent)
    out = _autofix_rule12_apply_canonical_conversion_rate(out)
    return out


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
    "verify_cross_flight_direction",
    "enforce_shared_cohort_n",
    "validate_bpiq_payload",
    "validate_before_write",
    # Rule 3 extension helpers (2026-09-03).
    "_recompute_conversion_valuation",
    "_implied_conversion_count",
    "_per_platform_incremental_counts",
    # Rule 6 byte-copy helper (2026-09-04).
    "_check_rule6_byte_copy",
    "_walk_leaves_with_parent",
    # Rules 7-12 (2026-09-08 first QC).
    "apply_auto_fixes_for_rules_7_to_12",
    # Rules 13-18 (2026-09-08 second QC).
    "_check_rule13_residual_consistency",
    "_check_rule14_cohort_index_stability",
    "_check_rule16_control_block_arithmetic",
    "_check_rule17_touchpoint_layer_distribution",
    "_check_rule18_peer_counts_reconciliation",
    "_autofix_rule13_retune_residual",
    "_autofix_rule14_hold_index",
    "_autofix_rule15_retune_f2_pre",
    "_autofix_rule16_recompute_arithmetic",
    "_autofix_rule17_retune_touchpoints",
    "_autofix_rule18_pull_parent_counts",
    "apply_auto_fixes_for_rules_7_to_18",
]
