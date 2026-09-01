"""House-standard BPIQ demographic / behavioral subset cut helper.

Codified 2026-09-01 after Liz caught three subset-invariant defects on
the Wheel of Fortune Boomer cuts of Coca-Cola and Pepsi. Every BPIQ
subset (Boomer, Gen Z, Female, income-scoped, geo-scoped, etc.) must:

    1. Anchor to the observed viewer cohort of its parent, never to a
       synthetic panel-base construct like 10,000,000.
    2. Share ONE frozen n across all brand reads of the same event.
    3. Never exceed the parent on any per-platform, per-touchpoint,
       or per-conversion row.
    4. Cap every behavioral multiplier at 1.0 / cohort_fraction so a
       multiplier can never push the subset row above the parent.

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
    cohort, force byte-identical audience_size / observed_cohort_n /
    demographic distributions across every payload in the set.

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


_PROJECTION_MIN_RATIO = 0.5
_PROJECTION_MAX_RATIO = 12.0


def _projection_ratio(new_panel: int, new_projected: int) -> float:
    """Fallback projection ratio for count -> projected count math. Clamped
    into a plausible band so a degenerate parent projection cannot blow
    up subset math."""
    if new_panel <= 0:
        return 1.0
    ratio = float(new_projected) / float(new_panel)
    return max(_PROJECTION_MIN_RATIO, min(_PROJECTION_MAX_RATIO, ratio))


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
    # Derive the frozen subset n (Rule 2 hinges on this jitter being
    # driven by subject_id, NOT by any brand-specific string).
    raw_subset_n = int(round(observed_cohort_n * cohort_fraction))
    new_panel = int(ensure_messy_sample_size(subject_id, raw_subset_n))

    if projected_universe is None:
        parent_panel = parent_payload.get("audience_size") or observed_cohort_n
        parent_proj = parent_payload.get("projected_audience_size") or parent_panel
        parent_ratio = _projection_ratio(int(parent_panel or 1), int(parent_proj or 1))
        raw_proj = int(round(new_panel * parent_ratio))
    else:
        raw_proj = int(projected_universe)
    new_projected = int(ensure_messy_sample_size(
        f"{subject_id}|projected", raw_proj
    ))

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
        if new_panel > 0:
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
    # provided). Strict on audience_size + n_observed; tolerant (0.5pp)
    # on demographic buckets to survive jitter.
    if strict_shared_cohort is not None:
        peer = strict_shared_cohort
        for key in ("audience_size", "projected_audience_size"):
            a = subset.get(key)
            b = peer.get(key)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                # Allow up to 0.5% drift to survive projection jitter.
                if a != b and abs(a - b) / max(a, 1) > 0.005:
                    v.append({
                        "rule": 2,
                        "path": key,
                        "subset_value": a,
                        "parent_value": b,
                        "message": (
                            f"Rule 2: {key} differs across brand reads of "
                            f"the same cohort ({a} vs peer {b}). Call "
                            "enforce_shared_cohort_n to freeze."
                        ),
                    })
        # Demographics - each bucket within 0.5pp.
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
                    if abs(a - b) > 0.5:
                        v.append({
                            "rule": 2,
                            "path": f"demographics.{phase}.{cat}.{bucket}",
                            "subset_value": a,
                            "parent_value": b,
                            "message": (
                                f"Rule 2: demographic bucket drift {a} vs peer "
                                f"{b} exceeds 0.5pp tolerance. Freeze demos via "
                                "enforce_shared_cohort_n."
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

    return v


# ---------------------------------------------------------------------
# Enforce shared cohort n across a set of peer subset payloads
# ---------------------------------------------------------------------


def enforce_shared_cohort_n(payloads: list, subject_id: str) -> list:
    """Freeze audience_size, projected_audience_size, observed_cohort_n
    and the demographic distributions to byte-identical values across
    every payload in `payloads`.

    Uses the first payload in the list as the canonical source for
    demographic distributions (which mirrors the "one frozen pull"
    contract). audience_size is re-derived through
    ensure_messy_sample_size(subject_id, ...) so the shared value is
    deterministic and messy.

    Returns a NEW list of dicts (deep copies). Does not mutate inputs.
    """
    if not payloads:
        return []
    canonical = copy.deepcopy(payloads[0])

    # Derive the canonical audience_size from the average, then jitter
    # via subject_id so both brand reads share the same value.
    sizes = [p.get("audience_size") for p in payloads
             if isinstance(p.get("audience_size"), (int, float))]
    projected_sizes = [p.get("projected_audience_size") for p in payloads
                       if isinstance(p.get("projected_audience_size"), (int, float))]
    canonical_n = int(round(sum(sizes) / len(sizes))) if sizes else 0
    canonical_n = int(ensure_messy_sample_size(subject_id, canonical_n)) if canonical_n else 0
    canonical_p = int(round(sum(projected_sizes) / len(projected_sizes))) if projected_sizes else 0
    canonical_p = int(ensure_messy_sample_size(
        f"{subject_id}|projected", canonical_p
    )) if canonical_p else 0
    frozen = []
    for p in payloads:
        out = copy.deepcopy(p)
        if canonical_n:
            out["audience_size"] = canonical_n
        if canonical_p:
            out["projected_audience_size"] = canonical_p
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
            if canonical_n:
                proj["cohort_weight"] = round(canonical_p / canonical_n, 4) \
                    if canonical_p else proj.get("cohort_weight")
            diag["projection"] = proj
        # Freeze demographic distributions to canonical.
        canonical_demos = canonical.get("demographics") or {}
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
    "verify_subset_invariants",
    "enforce_shared_cohort_n",
    "validate_bpiq_payload",
    "validate_before_write",
]
