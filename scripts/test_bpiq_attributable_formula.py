#!/usr/bin/env python3
"""Regression tests for the BPIQ house-standard attributable formula.

Established 2026-09-01 after Liz flagged that two shipped L'Oreal
decks (Emily in Paris S5, Elle S1) had computed the same "of which
attributable to partnership" subtotal via two different ratios.
See .cursor/rules/bpiq-attributable-formula.mdc for methodology.

Canonical formula:

    attributable = BLV + CV × min(1.0, max(0.0, adj_lift_pp / pre_baseline_pct))

Cases covered
-------------
1. Emily in Paris S5 inputs (positive lift within cap) reproduce the
   shipped $5.84M attributable.
2. Elle S1 inputs (positive lift within cap) reproduce the new
   house-standard $3.17M value (down from the previously shipped
   $3.58M under the adj/raw ratio).
3. Counterfactual with negative adjusted lift returns max(0, BLV).
4. Counterfactual with negative BLV and negative adj_lift floors to 0.
5. Very large adjusted lift caps the ratio at 1.0 exactly (not > 1.0).
6. Zero pre-baseline returns max(0, BLV) - refuses to invent a share.
7. Positive BLV with zero CV returns BLV unchanged.
8. Payload resolver prefers a stored attributable field to preserve
   byte-stability on historical payloads (drift protection during
   deck rebuilds).
9. Payload resolver falls back to the helper when only ingredients
   are present.
10. Payload resolver falls back to BLV + CV for legacy payloads that
    pre-date the strict-attributable field entirely.

Run: python3 bg-webapp/scripts/test_bpiq_attributable_formula.py
"""
import os
import sys

# Prefer the bg-webapp submodule root on the import path so we exercise
# the same module the deck builder imports.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from migration.bpiq_attributable import (  # noqa: E402
    compute_bpiq_attributable,
    resolve_attributable_from_payload,
)

FAILURES = []


def _approx(a, b, tol=1.0):
    return abs(float(a) - float(b)) <= tol


def _check(name, got, want, tol=1.0):
    if _approx(got, want, tol=tol):
        print(f"  OK  {name}: got {got:,.2f} ~= want {want:,.2f}")
    else:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")
        print(f"  FAIL {name}: got {got!r} want {want!r}")


# ---------------------------------------------------------------------
# 1. Emily in Paris S5: BLV 5,389,440 + CV 1,843,184 × (6.4 / 26.4)
# ---------------------------------------------------------------------
emily = compute_bpiq_attributable(
    blv_usd=5_389_440.0,
    cv_usd=1_843_184.0,
    adj_lift_pp=6.4,
    pre_baseline_pct=26.4,
)
_check("Emily in Paris S5 attributable", emily, 5_836_272.48, tol=1.0)

# ---------------------------------------------------------------------
# 2. Elle S1 under the house standard: BLV 2,860,815 + CV 868,304
#    × (8.0 / 22.8). Previously shipped as $3,584,401.67 under the
#    retired adj/raw ratio.
# ---------------------------------------------------------------------
elle = compute_bpiq_attributable(
    blv_usd=2_860_815.0,
    cv_usd=868_304.0,
    adj_lift_pp=8.0,
    pre_baseline_pct=22.8,
)
_check("Elle S1 attributable (house standard)", elle, 3_165_483.98, tol=1.0)
if elle >= 3_500_000:
    FAILURES.append(
        "Elle S1 under-corrected: value still resembles adj/raw ratio "
        f"($3,584,401.67); got {elle!r}"
    )

# ---------------------------------------------------------------------
# 3. Counterfactual: adj_lift < 0, BLV positive -> BLV only (no CV credit)
# ---------------------------------------------------------------------
counter = compute_bpiq_attributable(
    blv_usd=250_000.0,
    cv_usd=800_000.0,
    adj_lift_pp=-0.5,
    pre_baseline_pct=15.0,
)
_check("Counterfactual (neg adj, pos BLV)", counter, 250_000.0, tol=0.01)

# ---------------------------------------------------------------------
# 4. Deep counterfactual: BLV also non-positive -> 0
# ---------------------------------------------------------------------
deep_counter = compute_bpiq_attributable(
    blv_usd=-100.0,
    cv_usd=500_000.0,
    adj_lift_pp=-0.03,
    pre_baseline_pct=8.66,
)
_check("Counterfactual (neg adj, neg BLV)", deep_counter, 0.0, tol=0.01)

# ---------------------------------------------------------------------
# 5. Cap engaged: adj 15pp / pre 10pp -> ratio would be 1.5, clamped to 1.0
# ---------------------------------------------------------------------
capped = compute_bpiq_attributable(
    blv_usd=1_000_000.0,
    cv_usd=500_000.0,
    adj_lift_pp=15.0,
    pre_baseline_pct=10.0,
)
_check("Cap engaged (ratio clamped to 1.0)", capped, 1_500_000.0, tol=0.01)
uncapped_would_be = 1_000_000.0 + 500_000.0 * (15.0 / 10.0)
if _approx(capped, uncapped_would_be, tol=0.01):
    FAILURES.append(
        "Cap not engaged: attributable equals uncapped 1.5x product "
        f"({uncapped_would_be!r}); helper failed to clamp"
    )

# ---------------------------------------------------------------------
# 6. Zero baseline: refuse to invent a share, return max(0, BLV)
# ---------------------------------------------------------------------
zero_base = compute_bpiq_attributable(
    blv_usd=750_000.0,
    cv_usd=250_000.0,
    adj_lift_pp=2.0,
    pre_baseline_pct=0.0,
)
_check("Zero baseline (BLV positive)", zero_base, 750_000.0, tol=0.01)

zero_base_neg_blv = compute_bpiq_attributable(
    blv_usd=-5.0,
    cv_usd=250_000.0,
    adj_lift_pp=2.0,
    pre_baseline_pct=0.0,
)
_check("Zero baseline (BLV negative)", zero_base_neg_blv, 0.0, tol=0.01)

# ---------------------------------------------------------------------
# 7. Positive BLV, zero CV -> just BLV
# ---------------------------------------------------------------------
no_cv = compute_bpiq_attributable(
    blv_usd=1_234_567.0,
    cv_usd=0.0,
    adj_lift_pp=5.0,
    pre_baseline_pct=20.0,
)
_check("Zero CV", no_cv, 1_234_567.0, tol=0.01)

# ---------------------------------------------------------------------
# 8. Payload resolver: stored value wins for historical fidelity
# ---------------------------------------------------------------------
stored_wins = resolve_attributable_from_payload(
    {
        "valuation": {
            "brand_lift_value": 100.0,
            "conversion_value": 100.0,
            "attributable_to_partnership": 4242.42,
        },
        "control_group": {
            "incremental_lift_pp": 1.0,
            "treat_pre_pen_pct": 10.0,
        },
    },
    fallback_blv=100.0,
    fallback_cv=100.0,
    fallback_has_conversions=True,
)
_check("Payload resolver: stored value wins", stored_wins, 4242.42, tol=0.01)

# ---------------------------------------------------------------------
# 9. Payload resolver: recomputes when field is absent but ingredients exist
# ---------------------------------------------------------------------
recomputed = resolve_attributable_from_payload(
    {
        "valuation": {
            "brand_lift_value": 2_860_815.0,
            "conversion_value": 868_304.0,
            # attributable_to_partnership deliberately absent
        },
        "control_group": {
            "incremental_lift_pp": 8.0,
            "treat_pre_pen_pct": 22.8,
        },
    },
    fallback_blv=2_860_815.0,
    fallback_cv=868_304.0,
    fallback_has_conversions=True,
)
_check(
    "Payload resolver: recompute from ingredients",
    recomputed,
    3_165_483.98,
    tol=1.0,
)

# ---------------------------------------------------------------------
# 10. Payload resolver: BLV + CV fallback for legacy pre-strict payloads
# ---------------------------------------------------------------------
legacy = resolve_attributable_from_payload(
    {"valuation": {}, "control_group": {}},
    fallback_blv=500_000.0,
    fallback_cv=250_000.0,
    fallback_has_conversions=True,
)
_check("Payload resolver: legacy BLV+CV fallback", legacy, 750_000.0, tol=0.01)

legacy_no_conv = resolve_attributable_from_payload(
    {"valuation": {}, "control_group": {}},
    fallback_blv=500_000.0,
    fallback_cv=250_000.0,
    fallback_has_conversions=False,
)
_check(
    "Payload resolver: legacy fallback drops CV when no conversions",
    legacy_no_conv,
    500_000.0,
    tol=0.01,
)

# ---------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------
print()
if FAILURES:
    print(f"FAIL: {len(FAILURES)} failure(s):")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print("PASS: BPIQ attributable formula regression tests")
sys.exit(0)
