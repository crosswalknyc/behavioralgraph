"""House-standard BPIQ attributable-to-partnership dollar value.

Established 2026-09-01 as the single canonical formula after Liz
flagged that two shipped L'Oreal decks had computed the same subtotal
via two different ratios. See `.cursor/rules/bpiq-attributable-formula.mdc`
for the methodology rationale and drift history.

The single canonical formula:

    attributable = BLV + CV × min(1.0, max(0.0, adj_lift_pp / pre_baseline_pct))

with a hard $0 floor for the whole value. Counterfactual / non-sponsor
brands with `adj_lift_pp <= 0` return `max(0.0, BLV)`.
"""

from __future__ import annotations

from typing import Optional


def compute_bpiq_attributable(
    blv_usd: float,
    cv_usd: float,
    adj_lift_pp: float,
    pre_baseline_pct: float,
) -> float:
    """House-standard BPIQ attributable-to-partnership dollar value.

    ``attributable = BLV + CV × min(1.0, max(0.0, adj_lift_pp / pre_baseline_pct))``

    Ratio interpretation: the adjusted lift as a share of the
    pre-campaign baseline engagement rate. "The campaign lifted
    engagement N% relative to baseline" applied to CV credits an
    equivalent N%-of-baseline slice of conversion economics to the
    partnership.

    Parameters
    ----------
    blv_usd
        Brand Lift Value in dollars. Non-negative for a sponsor brand;
        may be zero for a counterfactual comparison brand.
    cv_usd
        Conversion Value in dollars. Non-negative; zero when the brand
        run had no shoppable conversion signal.
    adj_lift_pp
        Control-drift-adjusted lift in percentage points (post_pen_pct
        minus pre_pen_pct minus control_drift_pp). May be negative for
        counterfactual brands.
    pre_baseline_pct
        Pre-campaign penetration percentage for the treatment cohort
        (e.g. 22.8 for 22.8%). Must be > 0 for the ratio to be
        meaningful; a non-positive value returns ``max(0.0, blv_usd)``.

    Returns
    -------
    Attributable dollar value, always ``>= 0``. Counterfactual /
    non-sponsor brands with ``adj_lift_pp <= 0`` return
    ``max(0.0, blv_usd)`` (no CV credit).

    Notes
    -----
    Cap at 1.0: the ratio is capped at 1.0 so an unusually large
    adjusted lift on a tiny baseline never credits more than 100% of
    CV to the partnership.

    Floor at 0.0: the ratio is floored at 0.0 so a negative adjusted
    lift (control cohort out-lifted the treatment cohort) contributes
    nothing to CV credit; if BLV is also non-positive the whole value
    floors to 0.
    """
    try:
        blv = float(blv_usd or 0.0)
        cv = float(cv_usd or 0.0)
        adj = float(adj_lift_pp or 0.0)
        pre = float(pre_baseline_pct or 0.0)
    except (TypeError, ValueError):
        return 0.0

    if pre <= 0.0:
        # No usable baseline; refuse to invent a share. Keep BLV
        # (already control-adjusted upstream) and drop CV credit.
        return max(0.0, blv)

    share = max(0.0, min(1.0, adj / pre))
    return max(0.0, blv + cv * share)


def resolve_attributable_from_payload(
    payload: dict,
    *,
    fallback_blv: Optional[float] = None,
    fallback_cv: Optional[float] = None,
    fallback_has_conversions: bool = False,
) -> float:
    """Return the attributable-to-partnership figure for a BPIQ payload.

    Priority:

    1. ``payload['valuation']['attributable_to_partnership']`` when
       present (historical payloads render as they were built, keeping
       shipped decks byte-stable).
    2. Recompute via :func:`compute_bpiq_attributable` when the payload
       carries the ingredients (BLV, CV, adj_lift_pp, pre_baseline_pct).
    3. Fallback to ``fallback_blv + fallback_cv`` (only if
       ``fallback_has_conversions``) for legacy payloads that pre-date
       the strict-attributable field entirely.

    Parameters
    ----------
    payload
        A BPIQ payload dict (top-level, containing ``valuation`` and
        ``control_group``).
    fallback_blv, fallback_cv, fallback_has_conversions
        Values to fall back on when neither (1) nor (2) resolves.
        These mirror the pre-2026-09-01 fallback in the deck builder.
    """
    val = payload.get("valuation") or {}
    stored = val.get("attributable_to_partnership")
    if stored is not None:
        try:
            return float(stored)
        except (TypeError, ValueError):
            pass

    # Attempt to reconstruct from control_group + valuation.
    cg = payload.get("control_group") or {}
    blv = val.get("brand_lift_value")
    cv = val.get("conversion_value")
    adj = cg.get("incremental_lift_pp")
    pre = cg.get("treat_pre_pen_pct")
    if all(x is not None for x in (blv, cv, adj, pre)):
        return compute_bpiq_attributable(blv, cv, adj, pre)

    # Legacy fallback.
    blv_fb = float(fallback_blv or 0.0)
    cv_fb = float(fallback_cv or 0.0) if fallback_has_conversions else 0.0
    return max(0.0, blv_fb + cv_fb)


__all__ = ["compute_bpiq_attributable", "resolve_attributable_from_payload"]
