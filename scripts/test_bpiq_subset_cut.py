#!/usr/bin/env python3
"""Regression tests for the BPIQ subset-cut helper.

Codified 2026-09-01 after Liz caught two waves of subset-invariant
defects on the same-day Wheel of Fortune Boomer cuts of Coca-Cola
and Pepsi. See .cursor/rules/bpiq-subset-cut-invariants.mdc for the
rule tree and bg-webapp/migration/bpiq_subset_cut.py for the helper.

Covers the five subset invariants:

  1. Anchor to observed cohort n, not the panel-base construct.
  2. Byte-identical cohort-defining fields across every peer brand
     read of the same event. One pull, one profile.
  3. Subset never exceeds parent on any per-platform / per-touchpoint /
     conversion row (checked at BOTH raw and projected levels).
  4. Behavioral multipliers x cohort_fraction never exceed 1.0.
  5. Projection weight anchors to the parent's canonical panel weight;
     never derived from the subset's own projected/audience ratio.

Plus the always-on sanity validator (round-number check, demographic
sums, forbidden vocab, em dashes).

Run:  python3 bg-webapp/scripts/test_bpiq_subset_cut.py
"""

import copy
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from migration.bpiq_subset_cut import (  # noqa: E402
    BpiqWriteInvariantError,
    build_subset_payload,
    enforce_shared_cohort_n,
    resolve_observed_cohort_n,
    resolve_projection_weight,
    validate_before_write,
    validate_bpiq_payload,
    verify_subset_invariants,
    _implied_conversion_count,
    _per_platform_incremental_counts,
    _recompute_conversion_valuation,
    _check_rule6_byte_copy,
    _walk_leaves_with_parent,
    _check_rule7_campaign_rate_byte_copy,
    _check_rule8_per_platform_rate_byte_copy,
    _check_rule9_demo_pre_post_movement,
    _check_rule10_users_hits_ratio_coherence,
    _check_rule11_age_filter_tolerance,
    _check_rule12_conversion_rate_consistency,
    apply_auto_fixes_for_rules_7_to_12,
    _autofix_rule9_apply_boomer_demo_shift,
    _autofix_rule11_renormalize_age,
    _autofix_rule12_apply_canonical_conversion_rate,
)

FAILURES = []


def _check(name, cond, detail=""):
    if cond:
        print(f"  OK  {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL {name}: {detail}")


# ---------------------------------------------------------------------
# Fixture parent payload (a compact parent shaped like WoF Rerun)
# ---------------------------------------------------------------------

# Every count in the fixture ends 1-9 per no-round-numbers-in-deliverables.
def _parent_payload(observed_n=475_902):
    return {
        "project_name": "Fixture Brand x Fixture Event",
        "brand_partner": "Fixture Brand",
        "audience_size": 10_000_007,             # panel construct (ends in 7)
        "projected_audience_size": 15_700_003,   # panel projection (ends in 3)
        "totals": {
            "pre_users": 2_041_703, "post_users": 2_483_009,
            "pre_users_projected": 3_205_471, "post_users_projected": 3_898_313,
            "pre_hits": 6_941_783, "post_hits": 8_442_207,
            "lift_pct_users": 21.61, "lift_pct_hits": 3.37,
            "audience_pen_pre_pct": 20.417, "audience_pen_post_pct": 24.83,
        },
        "per_platform": [
            {"platform": "Facebook",
             "pre_users": 1_507_621, "post_users": 1_553_337,
             "pre_users_projected": 2_366_965, "post_users_projected": 2_438_739,
             "lift_pct_users": 3.03, "pre_pen_pct": 15.08, "post_pen_pct": 15.53},
            {"platform": "TikTok",
             "pre_users": 1_140_953, "post_users": 1_196_193,
             "pre_users_projected": 1_791_296, "post_users_projected": 1_878_023,
             "lift_pct_users": 4.84, "pre_pen_pct": 11.41, "post_pen_pct": 11.96},
            {"platform": "Direct (Brand Site)",
             "pre_users": 526_671, "post_users": 576_193,
             "pre_users_projected": 826_873, "post_users_projected": 904_623,
             "lift_pct_users": 9.40, "pre_pen_pct": 5.27, "post_pen_pct": 5.76},
        ],
        "conversions": {
            "pre_users": 15_419, "post_users": 22_887,
            "pre_users_projected": 24_209, "post_users_projected": 35_933,
            "pre_hits": 15_419, "post_hits": 22_887,
            "enabled": True, "low_signal": False,
            "note": ("Conversions = brand.com order confirmations plus "
                     "brand-owned retail landing pages, observed in the "
                     "post-window."),
        },
        "control_group": {
            "enabled": True,
            "treat_pre_pen_pct": 20.417, "treat_post_pen_pct": 24.83,
            "control_pre_pen_pct": 19.973, "control_post_pen_pct": 21.417,
            "treat_delta_pp": 4.413, "control_delta_pp": 1.444,
            "incremental_lift_pp": 2.969,
        },
        "valuation": {
            "brand_lift_value": 5_389_443.0,
            "conversion_value": 1_843_187.0,
            "brand_engagement_value": 12_207_531.0,
            "earned_media_value": 8_452_119.0,
            "total_brand_value": 27_892_281.0,
        },
        "demographics": {
            "pre": {
                "age": [
                    {"value": "65 or Older", "percentage": 33.5},
                    {"value": "55-64", "percentage": 26.4},
                    {"value": "45-54", "percentage": 17.5},
                    {"value": "35-44", "percentage": 11.8},
                    {"value": "25-34", "percentage": 6.3},
                    {"value": "18-24", "percentage": 3.2},
                    {"value": "17 and Under", "percentage": 0.9},
                    {"value": "Other", "percentage": 0.4},
                ],
                "gender": [
                    {"value": "Female", "percentage": 54.3},
                    {"value": "Male", "percentage": 42.5},
                    {"value": "Non-Binary", "percentage": 0.7},
                    {"value": "Prefer Not to Say", "percentage": 2.1},
                    {"value": "Trans Female", "percentage": 0.2},
                    {"value": "Trans Male", "percentage": 0.2},
                ],
            },
            "post": {
                "age": [
                    {"value": "65 or Older", "percentage": 33.5},
                    {"value": "55-64", "percentage": 26.4},
                    {"value": "45-54", "percentage": 17.5},
                    {"value": "35-44", "percentage": 11.8},
                    {"value": "25-34", "percentage": 6.3},
                    {"value": "18-24", "percentage": 3.2},
                    {"value": "17 and Under", "percentage": 0.9},
                    {"value": "Other", "percentage": 0.4},
                ],
                "gender": [
                    {"value": "Female", "percentage": 54.3},
                    {"value": "Male", "percentage": 42.5},
                    {"value": "Non-Binary", "percentage": 0.7},
                    {"value": "Prefer Not to Say", "percentage": 2.1},
                    {"value": "Trans Female", "percentage": 0.2},
                    {"value": "Trans Male", "percentage": 0.2},
                ],
            },
        },
        "sentiment": {
            "pre":  {"positive": 1_413, "neutral": 5_827, "negative": 1_133},
            "post": {"positive": 1_723, "neutral": 6_419, "negative": 1_237},
            "sample_size": 8_379,
        },
        "top_brand_properties": [
            {"common_name": "Fixture Brand", "hits": 1_249_331,
             "hits_projected": 1_961_413},
            {"common_name": "Fixture Brand Rewards", "hits": 214_567,
             "hits_projected": 336_881},
        ],
        "top_brand_properties_pre": [
            {"common_name": "Fixture Brand", "hits": 1_027_499,
             "hits_projected": 1_613_129},
            {"common_name": "Fixture Brand Rewards", "hits": 173_681,
             "hits_projected": 272_671},
        ],
        "diagnostics": {
            "observed_cohort_n": observed_n,
            "significance": {"n_observed": observed_n},
            "projection": {
                "observed_sample": observed_n,
                "projected_universe": 747_167,
                "cohort_weight": 1.5701,
            },
            "study_context": ("Study window: fixture flight on the "
                              "streaming service, following the linear "
                              "airing. Cohort: US viewers of the flight."),
        },
        "created_at": "2026-09-01T12:00:00",
    }


# ---------------------------------------------------------------------
# 1. Anchor uses observed cohort, not panel base
# ---------------------------------------------------------------------

print("--- test_anchor_uses_observed_cohort_not_panel_base ---")

parent = _parent_payload(observed_n=475_902)
subset = build_subset_payload(
    parent,
    cohort_fraction=0.599,
    subject_id="fixture_wof_boomer",
    subset_label="Boomer",
)

# Expected: audience_size derived from 475,902 x 0.599 = 285,065 (with jitter).
# Must NOT be derived from 10,000,007 x 0.599 = 5,990,004.
expected_from_observed = int(round(475_902 * 0.599))       # 285,065
expected_from_panel_base = int(round(10_000_007 * 0.599))  # 5,990,004

_check(
    "subset audience_size within 5% of observed_cohort_n x cohort_fraction",
    abs(subset["audience_size"] - expected_from_observed) / expected_from_observed < 0.05,
    f"got audience_size={subset['audience_size']:,}, expected near {expected_from_observed:,}",
)
_check(
    "subset audience_size NOT anchored to panel base (would be ~6M)",
    subset["audience_size"] < expected_from_panel_base * 0.1,
    f"got audience_size={subset['audience_size']:,}, panel-anchored would be {expected_from_panel_base:,}",
)

# Rule 1 hold when parent lacks observed_cohort_n and no anchor supplied.
# This mirrors the real-world defect signature: the shipped WoF Rerun
# parents pre-date the observed_cohort_n convention and carry a
# perfectly round audience_size = 10,000,000 (a significance-test
# denominator that the panel-construct detector must flag).
print()
print("--- test_anchor_rule1_hold_when_parent_missing ---")
naked_parent = _parent_payload()
naked_parent["diagnostics"].pop("observed_cohort_n", None)
naked_parent["audience_size"] = 10_000_000  # round panel construct
naked_parent["projected_audience_size"] = 15_700_000
naked_parent["diagnostics"]["significance"] = {"n_observed": 10_000_000}
naked_parent["diagnostics"]["projection"] = {}
_check(
    "resolve_observed_cohort_n returns None on panel-construct parent",
    resolve_observed_cohort_n(naked_parent) is None,
    "expected None",
)

hold_raised = False
try:
    build_subset_payload(
        naked_parent, 0.599, "fixture_hold", "Boomer",
    )
except BpiqWriteInvariantError as e:
    hold_raised = True
    print(f"    [OK raise] {str(e)[:120]}")
_check("build_subset_payload raises Rule 1 hold when anchor unresolvable",
       hold_raised, "expected BpiqWriteInvariantError")

# Supplying explicit observed_cohort_n bypasses the Rule 1 hold.
# A naked parent still lacks a projection weight anchor (Rule 5), so
# the caller must also supply `projected_universe=` explicitly (which
# is the operator-override path for parents that pre-date both the
# observed_cohort_n AND projection_weight conventions).
override = build_subset_payload(
    naked_parent, 0.599, "fixture_hold", "Boomer",
    observed_cohort_n=475_902,
    projected_universe=9_404_397,  # 285,065 x 32.99, jittered
)
_check(
    "explicit observed_cohort_n + projected_universe bypass Rule 1+5 hold",
    abs(override["audience_size"] - 285_065) / 285_065 < 0.05,
    f"got {override['audience_size']:,}",
)


# ---------------------------------------------------------------------
# 2. Shared n across two brand reads of the same event
# ---------------------------------------------------------------------

print()
print("--- test_shared_n_across_two_brand_reads ---")

# Two "different brands" of the same event share the same subject_id.
# In practice both are built off the same underlying panel; the fixture
# uses two distinct parent brands whose observed cohort is the same
# 475,902 people. The subject_id ties the frozen n across the pair.
parent_coke = _parent_payload(observed_n=475_902)
parent_coke["brand_partner"] = "Coke"
parent_pepsi = _parent_payload(observed_n=475_902)
parent_pepsi["brand_partner"] = "Pepsi"

sub_coke = build_subset_payload(
    parent_coke, 0.599, "fixture_wof_boomer_shared", "Boomer",
)
sub_pepsi = build_subset_payload(
    parent_pepsi, 0.599, "fixture_wof_boomer_shared", "Boomer",
)

_check(
    "audience_size identical across brand reads (same subject_id)",
    sub_coke["audience_size"] == sub_pepsi["audience_size"],
    f"coke={sub_coke['audience_size']:,}, pepsi={sub_pepsi['audience_size']:,}",
)
_check(
    "projected_audience_size identical across brand reads",
    sub_coke["projected_audience_size"] == sub_pepsi["projected_audience_size"],
    f"coke={sub_coke['projected_audience_size']:,}, "
    f"pepsi={sub_pepsi['projected_audience_size']:,}",
)
_check(
    "diagnostics.observed_cohort_n identical across brand reads",
    (sub_coke["diagnostics"]["observed_cohort_n"] ==
     sub_pepsi["diagnostics"]["observed_cohort_n"]),
    f"coke={sub_coke['diagnostics']['observed_cohort_n']}, "
    f"pepsi={sub_pepsi['diagnostics']['observed_cohort_n']}",
)

# Demographic distributions in the subset dimension are copied from
# parent (both parents carry the same demo shape in the fixture) so
# they match. In production the same coherence is enforced by
# enforce_shared_cohort_n; verify that helper here.
frozen = enforce_shared_cohort_n([sub_coke, sub_pepsi], "fixture_wof_boomer_shared")
_check(
    "enforce_shared_cohort_n returns 2 payloads (deep copies)",
    len(frozen) == 2 and frozen[0] is not sub_coke and frozen[1] is not sub_pepsi,
    "returned list length or identity wrong",
)
_check(
    "post-freeze: audience_size identical",
    frozen[0]["audience_size"] == frozen[1]["audience_size"],
    "not identical",
)
_check(
    "post-freeze: projected_audience_size identical",
    frozen[0]["projected_audience_size"] == frozen[1]["projected_audience_size"],
    "not identical",
)
_check(
    "post-freeze: demographics.pre.age byte-identical",
    (json.dumps(frozen[0]["demographics"]["pre"]["age"], sort_keys=True) ==
     json.dumps(frozen[1]["demographics"]["pre"]["age"], sort_keys=True)),
    "not identical",
)


# ---------------------------------------------------------------------
# 3. Subset never exceeds parent per platform (with a big multiplier)
# ---------------------------------------------------------------------

print()
print("--- test_subset_never_exceeds_parent_per_platform ---")

parent = _parent_payload(observed_n=475_902)
# Request an intentionally large Facebook multiplier that would violate
# Rule 3 if not capped.
sub = build_subset_payload(
    parent, 0.599, "fixture_rule3_cap", "Boomer",
    platform_multipliers={"Facebook": 5.0, "TikTok": 0.30},
)

parent_pp = {row["platform"]: row for row in parent["per_platform"]}
violations_seen = []
for row in sub["per_platform"]:
    name = row["platform"]
    pp = parent_pp[name]
    for k in ("pre_users", "post_users",
              "pre_users_projected", "post_users_projected"):
        if row.get(k, 0) > pp.get(k, 0):
            violations_seen.append((name, k, row[k], pp[k]))

_check(
    "no Rule 3 violation on any per-platform count after cap",
    not violations_seen,
    f"still saw {len(violations_seen)} violations: {violations_seen[:3]}",
)

# Direct verifier call should confirm zero Rule 3 violations too.
v = verify_subset_invariants(sub, parent, 0.599)
rule3 = [x for x in v if x["rule"] == 3]
_check(
    "verify_subset_invariants reports zero Rule 3 hits after cap",
    len(rule3) == 0,
    f"got {len(rule3)}: {rule3[:2]}",
)

# Now build without the cap logic (simulate the pre-fix defect) by
# manually inflating a platform. Verifier must flag the violation.
broken = copy.deepcopy(sub)
# Inflate Facebook pre_users above parent to simulate the defect.
for r in broken["per_platform"]:
    if r["platform"] == "Facebook":
        r["pre_users"] = parent_pp["Facebook"]["pre_users"] + 100_003
        break
v = verify_subset_invariants(broken, parent, 0.599)
rule3 = [x for x in v if x["rule"] == 3]
_check(
    "verify_subset_invariants flags Rule 3 when Facebook exceeds parent",
    any("Facebook" in x["path"] for x in rule3),
    f"got Rule 3 violations: {rule3[:3]}",
)


# ---------------------------------------------------------------------
# 4. Multiplier x cohort_fraction capped at 1.0 (probe 1.75 x 0.599)
# ---------------------------------------------------------------------

print()
print("--- test_multiplier_x_cohort_fraction_capped_at_one ---")

parent = _parent_payload(observed_n=475_902)
sub = build_subset_payload(
    parent, 0.599, "fixture_rule4_probe", "Boomer",
    platform_multipliers={"Facebook": 1.75},
)

# The diagnostics.behavioral_multiplier_caps should record the clamp
# from 1.75 to ~ 1.670 * 0.99 = 1.6533.
caps = sub["diagnostics"].get("behavioral_multiplier_caps") or []
fb_cap = next((c for c in caps if c["platform"] == "Facebook"), None)
_check(
    "clamp recorded for Facebook (requested 1.75 exceeds ceiling)",
    fb_cap is not None,
    f"caps={caps}",
)
if fb_cap:
    max_safe = 1.0 / 0.599
    _check(
        "Facebook applied multiplier just below 1.0/cohort_fraction",
        abs(fb_cap["applied"] - max_safe * 0.99) < 1e-3,
        f"applied={fb_cap.get('applied')}, expected ~ {round(max_safe * 0.99, 4)}",
    )
    _check(
        "Facebook max_safe correctly derived as 1.0/cohort_fraction",
        abs(fb_cap["max_safe"] - max_safe) < 1e-3,
        f"max_safe={fb_cap.get('max_safe')}, expected ~ {round(max_safe, 4)}",
    )

# The applied Facebook subset row must still land at or below parent.
parent_pp = {row["platform"]: row for row in parent["per_platform"]}
fb_sub = next(r for r in sub["per_platform"] if r["platform"] == "Facebook")
_check(
    "Facebook pre_users after cap <= parent Facebook pre_users",
    fb_sub["pre_users"] <= parent_pp["Facebook"]["pre_users"],
    f"sub={fb_sub['pre_users']:,}, parent={parent_pp['Facebook']['pre_users']:,}",
)


# ---------------------------------------------------------------------
# 5. Writer hard-fails on invariant violation
# ---------------------------------------------------------------------

print()
print("--- test_writer_hard_fails_on_invariant_violation ---")

parent = _parent_payload(observed_n=475_902)
sub = build_subset_payload(parent, 0.599, "fixture_writer_fail", "Boomer")

# Corrupt one row to violate Rule 3.
broken = copy.deepcopy(sub)
for r in broken["per_platform"]:
    if r["platform"] == "TikTok":
        r["post_users"] = 999_999_999  # dwarf parent
        break

raised = False
try:
    validate_before_write(broken, parent_payload=parent, cohort_fraction=0.599)
except BpiqWriteInvariantError as e:
    raised = True
    msg = str(e)
    print(f"    [OK raise] {msg[:160]}")

_check("validate_before_write raises on Rule 3 violation", raised,
       "expected BpiqWriteInvariantError")

# A clean subset writes without raising.
try:
    validate_before_write(sub, parent_payload=parent, cohort_fraction=0.599)
    print("    [OK] clean subset passes validate_before_write")
    _check("clean subset passes validate_before_write", True)
except BpiqWriteInvariantError as e:
    _check("clean subset passes validate_before_write", False,
           f"unexpectedly raised: {e}")


# ---------------------------------------------------------------------
# 6. Every count ends in 1-9
# ---------------------------------------------------------------------

print()
print("--- test_ends_in_1_to_9 ---")

parent = _parent_payload(observed_n=475_902)
sub = build_subset_payload(parent, 0.599, "fixture_messy", "Boomer")
sanity = validate_bpiq_payload(sub)
round_hits = [x for x in sanity if x["rule"] in ("count_round", "audience_size_round")]
_check(
    "no count-round violations in a clean subset payload",
    len(round_hits) == 0,
    f"got {len(round_hits)}: {[h['path'] for h in round_hits[:5]]}",
)


# ---------------------------------------------------------------------
# 7. Rates unchanged (sentiment shares preserved within jitter tolerance)
# ---------------------------------------------------------------------

print()
print("--- test_rates_unchanged (sentiment shares) ---")

parent = _parent_payload(observed_n=475_902)
sub = build_subset_payload(parent, 0.599, "fixture_rates", "Boomer")

def _share(block, k):
    t = (block.get("positive", 0) or 0) + (block.get("neutral", 0) or 0) + (block.get("negative", 0) or 0)
    if t == 0:
        return 0
    return (block.get(k, 0) or 0) / t

for phase in ("pre", "post"):
    for k in ("positive", "neutral", "negative"):
        p_share = _share(parent["sentiment"][phase], k)
        s_share = _share(sub["sentiment"][phase], k)
        _check(
            f"sentiment.{phase}.{k} share preserved within 0.02",
            abs(p_share - s_share) < 0.02,
            f"parent={p_share:.4f}, subset={s_share:.4f}",
        )


# ---------------------------------------------------------------------
# 8. No forbidden vocab (modeled / synth / AI-generated / HH / Nielsen)
# ---------------------------------------------------------------------

print()
print("--- test_no_forbidden_vocab ---")

# Fresh subset from the fixture parent (which is clean).
parent = _parent_payload(observed_n=475_902)
sub = build_subset_payload(parent, 0.599, "fixture_vocab", "Boomer")
sanity = validate_bpiq_payload(sub)
vocab = [x for x in sanity if x["rule"] == "forbidden_vocab"]
_check(
    "no forbidden vocab hits on a clean subset",
    len(vocab) == 0,
    f"got {len(vocab)}: {vocab[:3]}",
)

# Inject each forbidden token in a value string; validator must flag.
for token in ("modeled", "synth", "AI-generated", "HH", "households", "Nielsen"):
    dirty = copy.deepcopy(sub)
    dirty["diagnostics"]["study_context"] = f"This study {token} the audience."
    hits = [x for x in validate_bpiq_payload(dirty) if x["rule"] == "forbidden_vocab"]
    _check(
        f"forbidden vocab '{token}' flagged in study_context",
        len(hits) > 0,
        f"expected flag on token '{token}'",
    )

# Em dash flagged.
dirty = copy.deepcopy(sub)
dirty["diagnostics"]["study_context"] = "Study window \u2014 fixture flight."
hits = [x for x in validate_bpiq_payload(dirty)
        if x["rule"] == "forbidden_vocab" and "em_dash" in x.get("hits", [])]
_check("em dash flagged in study_context", len(hits) > 0,
       "expected em_dash hit")

# HHI (household income) preserved via allowlist.
clean_hhi = copy.deepcopy(sub)
clean_hhi["diagnostics"]["study_context"] = ("HHI mid-market cohort with no "
                                             "forbidden vocabulary.")
hits = [x for x in validate_bpiq_payload(clean_hhi) if x["rule"] == "forbidden_vocab"]
_check("HHI (household income overlay) does NOT trip forbidden vocab",
       len(hits) == 0,
       f"unexpected hits: {hits}")


# ---------------------------------------------------------------------
# 9. Rule 5 - projection weight anchors to parent's canonical panel weight
# ---------------------------------------------------------------------
#
# Codified same afternoon as the AM defects, after Liz caught that the
# AM rescope kept a 1.576x subset-internal ratio instead of the
# parent's canonical 32.99x panel-to-population weight. That defect
# hid a Rule 3 Facebook violation: at 32.99x the subset row exceeds
# parent; at 1.576x both sides shrink together and the check falsely
# passes.

print()
print("--- test_projection_weight_anchors_to_parent_rule5 ---")


def _parent_with_weight(observed_n=475_902, panel=10_000_007,
                        projection_weight=32.99):
    """Fixture parent with an explicit canonical panel weight (defaults
    to the WoF-shaped 10M panel to 329.9M US pop conversion)."""
    projected = int(round(panel * projection_weight))
    # Make sure the projected value does not end in 0.
    if projected % 10 == 0:
        projected += 3
    p = _parent_payload(observed_n=observed_n)
    p["audience_size"] = panel
    p["projected_audience_size"] = projected
    p["projection_weight"] = round(float(projection_weight), 4)
    p.setdefault("diagnostics", {}).setdefault("projection", {})
    p["diagnostics"]["projection"]["cohort_weight"] = round(
        float(projection_weight), 4
    )
    p["diagnostics"]["projection"]["projected_universe"] = projected
    # Every parent per_platform row's projected companion must sit at
    # raw x weight so subset scaling stays internally consistent
    # (subset raw = parent raw x cohort_fraction, subset projected =
    # subset raw x weight, must be <= parent projected).
    for row in p.get("per_platform", []):
        for k in ("pre_users", "post_users"):
            raw = row.get(k) or 0
            proj_key = f"{k}_projected"
            proj_val = int(round(raw * projection_weight))
            if proj_val % 10 == 0:
                proj_val += 3
            row[proj_key] = proj_val
    tot = p.get("totals") or {}
    for k in ("pre_users", "post_users"):
        raw = tot.get(k) or 0
        proj_val = int(round(raw * projection_weight))
        if proj_val % 10 == 0:
            proj_val += 3
        tot[f"{k}_projected"] = proj_val
    conv = p.get("conversions") or {}
    if conv:
        for k in ("pre_users", "post_users"):
            raw = conv.get(k) or 0
            proj_val = int(round(raw * projection_weight))
            if proj_val % 10 == 0:
                proj_val += 3
            conv[f"{k}_projected"] = proj_val
    for prop_key in ("top_brand_properties", "top_brand_properties_pre"):
        for prop in p.get(prop_key) or []:
            hits = prop.get("hits") or 0
            hp = int(round(hits * projection_weight))
            if hp % 10 == 0:
                hp += 3
            prop["hits_projected"] = hp
    return p


parent32 = _parent_with_weight(observed_n=475_902, projection_weight=32.99)
sub = build_subset_payload(
    parent32,
    cohort_fraction=0.599,
    subject_id="fixture_rule5_anchor",
    subset_label="Boomer",
)

expected_projected_from_32 = int(round(sub["audience_size"] * 32.99))
# Allow +/- 1 unit for the messy jitter on the projected count.
_check(
    "subset projected_audience_size within 3% of audience_size x 32.99",
    abs(sub["projected_audience_size"] - expected_projected_from_32) /
        max(expected_projected_from_32, 1) < 0.03,
    (f"got projected={sub['projected_audience_size']:,}, expected near "
     f"{expected_projected_from_32:,} (from {sub['audience_size']:,} x 32.99)"),
)
_check(
    "subset carries explicit projection_weight = parent's canonical weight",
    "projection_weight" in sub and abs(sub["projection_weight"] - 32.99) < 1e-3,
    f"got projection_weight={sub.get('projection_weight')!r}, expected ~32.99",
)
# Rule 5 verifier should agree on the anchor.
v = verify_subset_invariants(sub, parent32, 0.599)
rule5 = [x for x in v if x["rule"] == 5]
_check(
    "verify_subset_invariants: zero Rule 5 violations on properly-built subset",
    len(rule5) == 0,
    f"got {len(rule5)}: {rule5[:2]}",
)


print()
print("--- test_projection_weight_hold_when_ambiguous ---")

# Parent with only audience_size + a below-plausibility projected size,
# no explicit projection_weight, no diagnostics.projection block.
ambiguous_parent = _parent_payload(observed_n=475_902)
ambiguous_parent["audience_size"] = 10_000_007
ambiguous_parent["projected_audience_size"] = 12_000_003  # ratio 1.2, subset-shaped
ambiguous_parent.pop("projection_weight", None)
ambiguous_parent["diagnostics"].pop("projection", None)
ambiguous_parent["diagnostics"].pop("cohort_weight", None)

_check(
    "resolve_projection_weight returns None on a subset-shaped ratio parent",
    resolve_projection_weight(ambiguous_parent) is None,
    (f"expected None; got "
     f"{resolve_projection_weight(ambiguous_parent)!r}"),
)

hold_raised = False
try:
    build_subset_payload(
        ambiguous_parent, 0.599, "fixture_rule5_hold", "Boomer",
    )
except BpiqWriteInvariantError as e:
    hold_raised = True
    print(f"    [OK raise] {str(e)[:160]}")
_check(
    "build_subset_payload raises Rule 5 hold when weight unresolvable",
    hold_raised, "expected BpiqWriteInvariantError",
)


print()
print("--- test_projection_weight_rejects_subset_ratio_derivation ---")

# Parent that carries only audience_size + projected_audience_size
# where the derived ratio is a subset artifact (1.58, well below the
# 5.0 plausibility floor). resolve_projection_weight must NOT return
# that value, because it would let a subset-internal ratio masquerade
# as the panel-to-population weight.
subset_shape = _parent_payload(observed_n=285_063)
subset_shape["audience_size"] = 285_063
subset_shape["projected_audience_size"] = 449_179  # ratio 1.58
subset_shape.pop("projection_weight", None)
subset_shape["diagnostics"].pop("projection", None)
subset_shape["diagnostics"].pop("cohort_weight", None)
_check(
    "resolve_projection_weight refuses derivation when ratio < 5.0",
    resolve_projection_weight(subset_shape) is None,
    (f"expected None; got "
     f"{resolve_projection_weight(subset_shape)!r}"),
)

# But when the parent's ratio IS plausible (>= 5.0), derivation is
# allowed and returns the ratio.
plausible = _parent_payload(observed_n=475_902)
plausible["audience_size"] = 10_000_007
plausible["projected_audience_size"] = 329_923_141  # ratio ~32.99
plausible.pop("projection_weight", None)
plausible["diagnostics"].pop("projection", None)
plausible["diagnostics"].pop("cohort_weight", None)
derived = resolve_projection_weight(plausible)
_check(
    "resolve_projection_weight derives from ratio when >= 5.0",
    derived is not None and abs(derived - 32.99) < 0.01,
    f"got {derived!r}, expected ~32.99",
)


print()
print("--- test_shared_projected_universe_byte_identical ---")

# Two brand parents at 32.99x. Build subsets with the same subject_id.
# Rule 2: projected_audience_size must be byte-equal (within 1-unit
# integer-rounding tolerance) across peers.
parent_coke_32 = _parent_with_weight(observed_n=475_902, projection_weight=32.99)
parent_coke_32["brand_partner"] = "Coke"
parent_pepsi_32 = _parent_with_weight(observed_n=475_902, projection_weight=32.99)
parent_pepsi_32["brand_partner"] = "Pepsi"

sub_c32 = build_subset_payload(
    parent_coke_32, 0.599, "fixture_rule5_shared", "Boomer",
)
sub_p32 = build_subset_payload(
    parent_pepsi_32, 0.599, "fixture_rule5_shared", "Boomer",
)

_check(
    "peer projected_audience_size byte-identical (same subject_id)",
    sub_c32["projected_audience_size"] == sub_p32["projected_audience_size"],
    (f"coke={sub_c32['projected_audience_size']:,}, "
     f"pepsi={sub_p32['projected_audience_size']:,}"),
)
_check(
    "peer projection_weight byte-identical (both inherit 32.99)",
    sub_c32.get("projection_weight") == sub_p32.get("projection_weight"),
    (f"coke={sub_c32.get('projection_weight')!r}, "
     f"pepsi={sub_p32.get('projection_weight')!r}"),
)


print()
print("--- test_shared_demographics_byte_identical ---")

# Parents whose demos drift slightly (58.5 vs 58.9 on 65+). After
# enforce_shared_cohort_n, every demographic bucket must be
# byte-identical across the pair. Liz: one pull, one profile.
parent_a = _parent_with_weight(observed_n=475_902, projection_weight=32.99)
parent_b = _parent_with_weight(observed_n=475_902, projection_weight=32.99)
# Manually drift Pepsi's 65+ bucket.
for row in parent_b["demographics"]["pre"]["age"]:
    if row["value"] == "65 or Older":
        row["percentage"] = 33.9  # drifted from parent_a's 33.5

sub_a = build_subset_payload(
    parent_a, 0.599, "fixture_rule2_strict", "Boomer",
)
sub_b = build_subset_payload(
    parent_b, 0.599, "fixture_rule2_strict", "Boomer",
)
# Before enforce_shared_cohort_n, demos drift.
frozen = enforce_shared_cohort_n([sub_a, sub_b], "fixture_rule2_strict")
_check(
    "post-freeze: every demographic.pre.age bucket byte-identical",
    json.dumps(frozen[0]["demographics"]["pre"]["age"], sort_keys=True) ==
    json.dumps(frozen[1]["demographics"]["pre"]["age"], sort_keys=True),
    "demographics.pre.age drifted after freeze",
)
_check(
    "post-freeze: every demographic.pre.gender bucket byte-identical",
    json.dumps(frozen[0]["demographics"]["pre"]["gender"], sort_keys=True) ==
    json.dumps(frozen[1]["demographics"]["pre"]["gender"], sort_keys=True),
    "demographics.pre.gender drifted after freeze",
)
_check(
    "post-freeze: audience_size byte-identical",
    frozen[0]["audience_size"] == frozen[1]["audience_size"],
    "audience_size drifted after freeze",
)
_check(
    "post-freeze: projection_weight byte-identical",
    frozen[0].get("projection_weight") == frozen[1].get("projection_weight"),
    "projection_weight drifted after freeze",
)


print()
print("--- test_verify_flags_projection_weight_drift ---")

# Hand-craft a payload whose projection_weight sits at 1.58 while the
# parent's canonical weight is 32.99. Verifier must flag a Rule 5
# violation.
parent32 = _parent_with_weight(observed_n=475_902, projection_weight=32.99)
sub_broken = build_subset_payload(
    parent32, 0.599, "fixture_rule5_broken", "Boomer",
)
# Post-hoc: set projection_weight AND recompute projected_audience_size
# with the wrong 1.58 ratio, mirroring the AM rescope defect.
sub_broken["projection_weight"] = 1.5761
sub_broken["projected_audience_size"] = int(round(
    sub_broken["audience_size"] * 1.5761
))
# Also drift so it does not end in 0.
if sub_broken["projected_audience_size"] % 10 == 0:
    sub_broken["projected_audience_size"] += 3

v = verify_subset_invariants(sub_broken, parent32, 0.599)
rule5 = [x for x in v if x["rule"] == 5]
_check(
    "verify_subset_invariants flags Rule 5 on projection_weight drift",
    len(rule5) >= 1,
    f"expected at least one Rule 5 hit; got {len(rule5)}: {rule5[:3]}",
)
# Both flavors of the check should fire: explicit weight mismatch AND
# the derived-ratio mismatch.
paths = {x["path"] for x in rule5}
_check(
    "Rule 5 fires on projection_weight scalar mismatch",
    "projection_weight" in paths,
    f"paths={paths}",
)
_check(
    "Rule 5 fires on projected/audience ratio mismatch",
    "projected_audience_size/audience_size" in paths,
    f"paths={paths}",
)


print()
print("--- test_verify_flags_universe_divergence_across_peers ---")

# Hand-craft two peer payloads with different projected_audience_size
# on the same cohort. Rule 2 (strict) must fire.
parent32 = _parent_with_weight(observed_n=475_902, projection_weight=32.99)
peer_a = build_subset_payload(
    parent32, 0.599, "fixture_rule2_diverge", "Boomer",
)
peer_b = copy.deepcopy(peer_a)
# Diverge Pepsi peer's projected size (mirrors the shipped 449177 vs
# 445186 defect, scaled to the fixture).
peer_b["projected_audience_size"] = peer_a["projected_audience_size"] + 3_993

v = verify_subset_invariants(
    peer_a, parent32, 0.599, strict_shared_cohort=peer_b,
)
rule2 = [x for x in v if x["rule"] == 2]
_check(
    "Rule 2 (strict) flags peer projected_audience_size divergence",
    any("projected_audience_size" in x["path"] for x in rule2),
    f"got Rule 2 hits: {[x['path'] for x in rule2[:5]]}",
)


print()
print("--- test_verify_flags_demographic_divergence_across_peers ---")

# Hand-craft two peer payloads with a 0.5pp drift on the 65+ bucket.
# Rule 2 (byte-identical) must fire; the AM tolerance of 0.5pp is gone.
parent32 = _parent_with_weight(observed_n=475_902, projection_weight=32.99)
peer_a = build_subset_payload(
    parent32, 0.599, "fixture_rule2_demo", "Boomer",
)
peer_b = copy.deepcopy(peer_a)
for row in peer_b["demographics"]["pre"]["age"]:
    if row["value"] == "65 or Older":
        row["percentage"] = row["percentage"] + 0.5
        break

v = verify_subset_invariants(
    peer_a, parent32, 0.599, strict_shared_cohort=peer_b,
)
rule2 = [x for x in v if x["rule"] == 2]
_check(
    "Rule 2 (byte-identical) flags 0.5pp demographic bucket drift",
    any("demographics.pre.age" in x["path"] for x in rule2),
    (f"got Rule 2 hits: {[x['path'] for x in rule2[:5]]}"),
)
# And drift below 1e-6 must NOT fire (proves the tolerance is at
# json-serialization noise level, not at 0.5pp).
peer_c = copy.deepcopy(peer_a)
for row in peer_c["demographics"]["pre"]["age"]:
    if row["value"] == "65 or Older":
        row["percentage"] = row["percentage"] + 1e-9  # noise
        break
v = verify_subset_invariants(
    peer_a, parent32, 0.599, strict_shared_cohort=peer_c,
)
rule2_noise = [x for x in v if x["rule"] == 2
               and "demographics.pre.age" in x["path"]]
_check(
    "Rule 2 does NOT fire on json-serialization noise (< 1e-6)",
    len(rule2_noise) == 0,
    f"got {rule2_noise[:2]}",
)


# ---------------------------------------------------------------------
# 10. Smoke test: real shipped WoF Boomer payloads (when available)
# ---------------------------------------------------------------------

print()
print("--- smoke: real shipped WoF Boomer payloads ---")

_ship_dir = "/tmp/bpiq_ship_check"
coke_b_path = os.path.join(_ship_dir, "coke_boomer.json")
pepsi_b_path = os.path.join(_ship_dir, "pepsi_boomer.json")
coke_p_path = os.path.join(_ship_dir, "coke_parent.json")
pepsi_p_path = os.path.join(_ship_dir, "pepsi_parent.json")

if all(os.path.exists(p) for p in (coke_b_path, pepsi_b_path, coke_p_path, pepsi_p_path)):
    coke_b = json.loads(open(coke_b_path).read())
    pepsi_b = json.loads(open(pepsi_b_path).read())
    coke_p = json.loads(open(coke_p_path).read())
    pepsi_p = json.loads(open(pepsi_p_path).read())

    # Back-annotate parents with the known observed cohort n (475,902).
    # The parent payload does not yet carry this field; back-annotation
    # is what the operator would supply when the parent pre-dates the
    # observed_cohort_n convention.
    for p in (coke_p, pepsi_p):
        p.setdefault("diagnostics", {})["observed_cohort_n"] = 475_902

    # Under the extended rule set (Rule 5 added, Rule 2 tightened
    # 2026-09-01 PM), the shipped Boomer payloads are EXPECTED to
    # flag the projection-weight defect Liz caught. This smoke test
    # asserts that the extended verifier CORRECTLY surfaces those
    # defects; it is not a data-fix (data-fix is a separate agent).
    for tag, sub, parent in (("Coke", coke_b, coke_p),
                             ("Pepsi", pepsi_b, pepsi_p)):
        v = verify_subset_invariants(sub, parent, 0.599)
        rule1 = [x for x in v if x["rule"] == 1]
        rule3 = [x for x in v if x["rule"] == 3]
        rule4 = [x for x in v if x["rule"] == 4]
        rule5 = [x for x in v if x["rule"] == 5]
        print(f"    [info] shipped {tag} Boomer under extended rules: "
              f"rule1={len(rule1)}, rule3={len(rule3)}, "
              f"rule4={len(rule4)}, rule5={len(rule5)}")
        for x in rule5[:2]:
            print(f"      Rule 5 - {x['path']}: subset={x['subset_value']}, "
                  f"parent={x['parent_value']}")
        for x in rule3[:2]:
            print(f"      Rule 3 - {x['path']}: subset={x['subset_value']:,}, "
                  f"parent={x['parent_value']:,}")
        # Rules 1 + 4 should be clean on the rescoped payloads.
        _check(
            f"shipped {tag} Boomer: Rule 1 clean (anchor correctly at 285,065)",
            len(rule1) == 0,
            f"got Rule 1 hits: {rule1[:2]}",
        )
        _check(
            f"shipped {tag} Boomer: Rule 4 clean (no uncapped multiplier at raw level)",
            len(rule4) == 0,
            f"got Rule 4 hits: {rule4[:2]}",
        )
        # Rule 5 SHOULD fire because the AM rescope kept 1.576x
        # instead of the parent's 32.99x. This is the defect Liz
        # flagged in the PM memo.
        _check(
            f"shipped {tag} Boomer: Rule 5 correctly flags projection weight defect",
            len(rule5) >= 1,
            (f"expected Rule 5 to fire (subset uses 1.576x, parent "
             f"canonical is 32.99x); got zero hits"),
        )

    # Peer coherence: shipped payloads share n but demos drift.
    # Under the tightened Rule 2 (byte-identical, no jitter tolerance),
    # this drift SHOULD fire. Assert it does.
    v = verify_subset_invariants(
        coke_b, coke_p, 0.599, strict_shared_cohort=pepsi_b,
    )
    rule2 = [x for x in v if x["rule"] == 2]
    print(f"    [info] shipped Coke vs Pepsi Boomer Rule 2 drift: "
          f"{len(rule2)} field(s) fail byte-identical peer coherence")
    for x in rule2[:5]:
        print(f"      Rule 2 - {x['path']}: coke={x['subset_value']}, "
              f"pepsi={x['parent_value']}")
    _check(
        "shipped Coke vs Pepsi Boomer: Rule 2 (strict) flags peer drift",
        len(rule2) >= 1,
        ("expected byte-identical Rule 2 to fire on shipped payloads "
         "(demos drift 59.8% vs 59.4% on 65+ per Liz PM memo)"),
    )
else:
    print(f"    [skip] shipped payloads not present at {_ship_dir}")


# ---------------------------------------------------------------------
# 11. Rule 3 extension - CV cascade + field-copy defect (2026-09-03)
# ---------------------------------------------------------------------
#
# Codified after Liz caught that the F1 Coke Boomer shipped with
# valuation.conversion_value = $1,836,220, which divided by the
# $10 per-conversion rate yielded a 183,622 implied count that
# byte-matched the file's own Direct (Brand Site)
# incremental_users_projected row (a per_platform column, not a
# conversion column). Root cause: a downstream valuation-recompute
# pulled from the wrong per_platform column instead of
# conversions.post_users_projected. The tests below assert:
#
#   (a) A subset payload with conversions.post_users_projected >
#       parent's triggers the Rule 3 extension AND the auto-fix
#       clamps the count in-place to a value at or below parent.
#   (b) A subset payload whose implied CV count byte-matches any
#       per_platform incremental_users_projected fires the Rule 3
#       extension AND the auto-fix nudges the count clear of the
#       collision.
#   (c) The auto-fixed count still complies with
#       .cursor/rules/no-round-sample-sizes.mdc (ends in 1-9, not a
#       forbidden literal).


def _parent_with_conv_rate(observed_n=475_902, projection_weight=32.99,
                            per_user_rate=10.0):
    """Fixture parent with an explicit CV rate + a Direct (Brand
    Site) per_platform row that fires the field-copy collision when
    the wrong source column is pulled by a downstream recompute."""
    projected = int(round(10_000_007 * projection_weight))
    if projected % 10 == 0:
        projected += 3
    p = _parent_payload(observed_n=observed_n)
    p["audience_size"] = 10_000_007
    p["projected_audience_size"] = projected
    p["projection_weight"] = round(float(projection_weight), 4)
    p.setdefault("diagnostics", {}).setdefault("projection", {})
    p["diagnostics"]["projection"]["cohort_weight"] = round(
        float(projection_weight), 4
    )
    p["diagnostics"]["projection"]["projected_universe"] = projected
    # Ensure every per_platform row projects at parent weight so the
    # subset scales below.
    for row in p.get("per_platform", []):
        for k in ("pre_users", "post_users"):
            raw = row.get(k) or 0
            proj_key = f"{k}_projected"
            proj_val = int(round(raw * projection_weight))
            if proj_val % 10 == 0:
                proj_val += 3
            row[proj_key] = proj_val
    tot = p.get("totals") or {}
    for k in ("pre_users", "post_users"):
        raw = tot.get(k) or 0
        proj_val = int(round(raw * projection_weight))
        if proj_val % 10 == 0:
            proj_val += 3
        tot[f"{k}_projected"] = proj_val
    conv = p.get("conversions") or {}
    for k in ("pre_users", "post_users"):
        raw = conv.get(k) or 0
        proj_val = int(round(raw * projection_weight))
        if proj_val % 10 == 0:
            proj_val += 3
        conv[f"{k}_projected"] = proj_val
    for prop_key in ("top_brand_properties", "top_brand_properties_pre"):
        for prop in p.get(prop_key) or []:
            hits = prop.get("hits") or 0
            hp = int(round(hits * projection_weight))
            if hp % 10 == 0:
                hp += 3
            prop["hits_projected"] = hp
    # Rates block + CV = post_users_projected * rate (canonical).
    val = p.get("valuation") or {}
    val.setdefault("rates", {})["conv_value_per_user"] = per_user_rate
    val["conversion_value"] = round(conv["post_users_projected"] * per_user_rate, 2)
    # BEV / EMV / BLV are fixed from the base fixture; refresh TBV.
    val["total_brand_value"] = round(
        float(val.get("brand_engagement_value") or 0.0)
        + float(val.get("earned_media_value") or 0.0)
        + float(val.get("brand_lift_value") or 0.0)
        + float(val["conversion_value"]),
        2,
    )
    val["incremental_conversion_value"] = 0.0
    val["attributable_to_partnership"] = 0.0
    val["attributable_share_of_conversion_pct"] = 0.0
    p["valuation"] = val
    p["conversions"] = conv
    # A cg block so the auto-fix cascade has adj + baseline to work with.
    p["control_group"]["treat_pre_pen_pct"] = 13.069
    p["control_group"]["incremental_lift_pp"] = 2.604
    return p


print()
print("--- test_rule3_ext_verifier_flags_subset_conv_count_over_parent ---")

parent = _parent_with_conv_rate()
sub = build_subset_payload(parent, 0.599, "fixture_r3_ext_over", "Boomer")

# Hand-craft a defect: bump conversions.post_users_projected above parent.
broken = copy.deepcopy(sub)
parent_cv_count = parent["conversions"]["post_users_projected"]
broken["conversions"]["post_users_projected"] = parent_cv_count + 100_003
rate = float(broken["valuation"]["rates"]["conv_value_per_user"])
broken["valuation"]["conversion_value"] = round(
    broken["conversions"]["post_users_projected"] * rate, 2
)
v = verify_subset_invariants(broken, parent, 0.599)
rule3_paths = {x["path"] for x in v if x["rule"] == 3}
_check(
    "verifier flags Rule 3 on CV-implied count > parent's implied count",
    "valuation.conversion_value/rate" in rule3_paths,
    f"got paths={rule3_paths}",
)


print()
print("--- test_rule3_ext_verifier_flags_cv_field_copy_defect ---")

parent = _parent_with_conv_rate()
sub = build_subset_payload(parent, 0.599, "fixture_r3_ext_copy", "Boomer")

# Hand-craft the F1 Coke Boomer defect: force valuation.conversion_value
# to equal Direct (Brand Site) incremental_users_projected * rate.
broken = copy.deepcopy(sub)
direct_incr = None
for row in broken["per_platform"]:
    if "Direct" in (row.get("platform") or ""):
        direct_incr = row["post_users_projected"] - row["pre_users_projected"]
        break
_check(
    "fixture Direct (Brand Site) row present with an incremental value",
    isinstance(direct_incr, int) and direct_incr > 0,
    f"direct_incr={direct_incr}",
)
if direct_incr:
    broken["valuation"]["conversion_value"] = round(direct_incr * rate, 2)
    # Trip the field-copy collision by also aligning the implied count.
    v = verify_subset_invariants(broken, parent, 0.599)
    rule3 = [x for x in v if x["rule"] == 3
             and x["path"] == "valuation.conversion_value/rate"]
    _check(
        "verifier flags Rule 3 field-copy: CV count = Direct incremental",
        any("Direct" in str(x.get("parent_value") or []) for x in rule3),
        f"got Rule 3 hits: {[(x['path'], x.get('parent_value')) for x in rule3[:3]]}",
    )


print()
print("--- test_rule3_ext_verifier_flags_cv_coherence ---")

# Hand-craft: valuation.conversion_value drifts from
# conversions.post_users_projected * rate. Verifier must flag.
parent = _parent_with_conv_rate()
sub = build_subset_payload(parent, 0.599, "fixture_r3_ext_cohere", "Boomer")
broken = copy.deepcopy(sub)
# Push CV to a value that does not match count * rate.
count = broken["conversions"]["post_users_projected"]
broken["valuation"]["conversion_value"] = round(count * rate + 987_651, 2)
v = verify_subset_invariants(broken, parent, 0.599)
paths = {x["path"] for x in v if x["rule"] == 3}
_check(
    "verifier flags Rule 3 coherence when CV != count * rate",
    "valuation.conversion_value" in paths,
    f"got paths={paths}",
)


print()
print("--- test_rule3_ext_autofix_recomputes_cv_from_correct_source ---")

parent = _parent_with_conv_rate()
sub = build_subset_payload(parent, 0.599, "fixture_r3_ext_autofix", "Boomer")

# After build_subset_payload's terminal recompute, CV must equal
# conversions.post_users_projected * rate byte-exact.
subset_count = sub["conversions"]["post_users_projected"]
expected_cv = round(subset_count * rate, 2)
_check(
    "build_subset_payload sets valuation.conversion_value = "
    "conversions.post_users_projected * rate",
    abs(float(sub["valuation"]["conversion_value"]) - expected_cv) < 1e-2,
    f"got CV={sub['valuation']['conversion_value']!r}, expected {expected_cv!r} "
    f"({subset_count} x {rate})",
)
# CV must sit at or below parent's CV.
_check(
    "auto-built CV <= parent's CV (Rule 3)",
    float(sub["valuation"]["conversion_value"]) <=
        float(parent["valuation"]["conversion_value"]),
    f"sub={sub['valuation']['conversion_value']!r}, "
    f"parent={parent['valuation']['conversion_value']!r}",
)
# Implied count must not collide with any per_platform incremental.
implied = _implied_conversion_count(sub)
incrs = set(_per_platform_incremental_counts(sub).values())
_check(
    "auto-built CV-implied count does not collide with per_platform incrementals",
    implied not in incrs or implied is None,
    f"implied={implied}, incrementals={sorted(incrs)}",
)
# Total brand value = BEV + EMV + BLV + CV byte-exact.
val = sub["valuation"]
expected_tbv = round(
    float(val["brand_engagement_value"]) + float(val["earned_media_value"])
    + float(val["brand_lift_value"]) + float(val["conversion_value"]), 2
)
_check(
    "auto-built total_brand_value = BEV + EMV + BLV + CV byte-exact",
    abs(float(val["total_brand_value"]) - expected_tbv) < 1e-2,
    f"got {val['total_brand_value']!r}, expected {expected_tbv!r}",
)
# attributable_to_partnership recomputed via the canonical helper.
adj = float(sub["control_group"]["incremental_lift_pp"])
pre = float(sub["control_group"]["treat_pre_pen_pct"])
share = max(0.0, min(1.0, adj / pre)) if pre > 0 else 0.0
expected_attributable = round(
    float(val["brand_lift_value"]) + float(val["conversion_value"]) * share, 2
)
_check(
    "auto-built attributable_to_partnership uses compute_bpiq_attributable "
    "formula",
    abs(float(val["attributable_to_partnership"]) - expected_attributable) < 1e-2,
    f"got {val['attributable_to_partnership']!r}, "
    f"expected {expected_attributable!r}",
)


print()
print("--- test_rule3_ext_autofix_clamps_conv_count_over_parent ---")

# Simulate: caller mutates subset.conversions.post_users_projected to
# exceed parent. Then re-invokes the recompute helper. The helper
# must clamp the count to <=0.95*parent*cohort_fraction with jitter.
parent = _parent_with_conv_rate()
sub = build_subset_payload(parent, 0.599, "fixture_r3_ext_clamp", "Boomer")
parent_count = parent["conversions"]["post_users_projected"]
sub["conversions"]["post_users_projected"] = parent_count + 200_003
report = _recompute_conversion_valuation(
    sub, parent, 0.599, "fixture_r3_ext_clamp",
)
_check(
    "auto-fix clamps subset count to <= parent count",
    sub["conversions"]["post_users_projected"] <= parent_count,
    f"got {sub['conversions']['post_users_projected']:,}, parent={parent_count:,}",
)
_check(
    "auto-fix reports a conv_count_over_parent guard fire",
    any(g["check"] == "conv_count_over_parent" for g in report["guards"]),
    f"guards={report['guards']}",
)
# no-round-sample-sizes.mdc compliance on the clamped count.
clamped_count = sub["conversions"]["post_users_projected"]
_check(
    "auto-fixed count ends in 1-9 per no-round-sample-sizes.mdc",
    clamped_count % 10 != 0,
    f"got {clamped_count} ends in {clamped_count % 10}",
)
_check(
    "auto-fixed count not in FORBIDDEN_LITERALS",
    clamped_count not in {2001, 12345, 99999, 88888, 77777, 22222, 123456, 654321},
    f"got {clamped_count} in forbidden literals",
)


print()
print("--- test_rule3_ext_autofix_breaks_field_copy_collision ---")

# Simulate: force subset conversions.post_users_projected to byte-match
# Direct (Brand Site) incremental. Recompute helper must nudge it clear
# and produce a CV that does not collide.
parent = _parent_with_conv_rate()
sub = build_subset_payload(parent, 0.599, "fixture_r3_ext_collide", "Boomer")
direct_incr = None
for row in sub["per_platform"]:
    if "Direct" in (row.get("platform") or ""):
        direct_incr = row["post_users_projected"] - row["pre_users_projected"]
        break
# If direct_incr is above parent conv count, we cannot cleanly force a
# collision at a plausible level (the clamp would demote it below). In
# that fixture combination, the test asserts the helper still ships a
# non-colliding CV, which is the invariant we care about.
if direct_incr is not None:
    sub["conversions"]["post_users_projected"] = direct_incr
    report2 = _recompute_conversion_valuation(
        sub, parent, 0.599, "fixture_r3_ext_collide",
    )
    implied = _implied_conversion_count(sub)
    incrs = set(_per_platform_incremental_counts(sub).values())
    _check(
        "auto-fix leaves the implied CV count non-colliding with any "
        "per_platform incremental",
        implied not in incrs,
        f"implied={implied}, incrementals={sorted(incrs)}",
    )
    # Either the collision guard fired OR the over-parent clamp fired
    # ahead of it (both routes produce a clean non-colliding count).
    _check(
        "auto-fix guard fired (over-parent clamp or field-copy nudge)",
        len(report2["guards"]) >= 1,
        f"guards={report2['guards']}",
    )


print()
print("--- test_rule3_ext_no_ops_when_conversions_disabled ---")

# Automotive convention: conversions.enabled = False AND CV rate = 0.
# The recompute must be a no-op.
parent = _parent_with_conv_rate()
sub = build_subset_payload(parent, 0.599, "fixture_r3_ext_noop", "Boomer")
sub["conversions"]["enabled"] = False
sub["valuation"]["rates"]["conv_value_per_user"] = 0.0
before_cv = sub["valuation"]["conversion_value"]
report3 = _recompute_conversion_valuation(
    sub, parent, 0.599, "fixture_r3_ext_noop",
)
_check(
    "recompute helper is a no-op when conversions.enabled is False",
    sub["valuation"]["conversion_value"] == before_cv
    and report3["count_after"] is None,
    f"before={before_cv}, after={sub['valuation']['conversion_value']}, "
    f"report={report3}",
)


# ---------------------------------------------------------------------
# Rule 6 (2026-09-04, Liz F1 Boomer audit)
# ---------------------------------------------------------------------

print()
print("--- test_rule6_flags_significance_byte_copy ---")

# Simulate the F1 Coke Boomer defect signature: subset panel is a Boomer
# subset (panel_ratio ~ 0.03) but diagnostics.significance.n_discordant,
# primary_test_z, detection_floor_pp, and delta_ci_95_pp were left as
# byte copies of the parent's 10M-panel values.
parent = _parent_with_weight(observed_n=475_903, projection_weight=32.99)
parent["diagnostics"]["significance"] = {
    "n_observed": 10_000_007,
    "n_discordant": 1_170_283,
    "primary_test": "pooled_two_sample_z_on_paired_marginals",
    "primary_test_z": 668.127,
    "primary_test_p_value": 0.0,
    "significant": True,
    "delta_pp_point": 11.703,
    "delta_ci_95_pp": [11.669, 11.737],
    "detection_floor_pp": 0.034,
    "notes": "At n=10M the detection floor is ~0.04pp, so p-values "
             "are dropped from the client-facing slide.",
}
parent["incidence_note"] = (
    "All pre and post penetration percentages are incidence rates: "
    "unique brand engagers observed in the window as a share of the "
    "10,000,000-panelist sample."
)

# Build a properly-scaled subset via the helper.
sub = build_subset_payload(parent, 0.602, "fixture_r6_boomer", "Boomer")
# Now inject the F1 defect signature: byte-copy the parent's significance
# stats onto the subset without scaling them for the smaller panel.
sub["diagnostics"]["significance"] = copy.deepcopy(
    parent["diagnostics"]["significance"]
)
sub["diagnostics"]["significance"]["n_observed"] = sub["audience_size"]
sub["incidence_note"] = parent["incidence_note"]

violations = _check_rule6_byte_copy(sub, parent)
paths = {v["path"] for v in violations}
rules = {v["rule"] for v in violations}
_check(
    "Rule 6 flags byte-copied n_discordant on smaller panel",
    "diagnostics.significance.n_discordant" in paths,
    f"got paths={sorted(paths)}",
)
_check(
    "Rule 6 flags byte-copied primary_test_z on smaller panel",
    "diagnostics.significance.primary_test_z" in paths,
    f"got paths={sorted(paths)}",
)
_check(
    "Rule 6 flags byte-copied detection_floor_pp on smaller panel",
    "diagnostics.significance.detection_floor_pp" in paths,
    f"got paths={sorted(paths)}",
)
_check(
    "Rule 6 flags byte-copied delta_ci_95_pp bounds on smaller panel",
    any("delta_ci_95_pp" in p for p in paths),
    f"got paths={sorted(paths)}",
)
_check(
    "Rule 6 flags stale 'n=10M' text in significance.notes",
    "diagnostics.significance.notes" in paths,
    f"got paths={sorted(paths)}",
)
_check(
    "Rule 6 flags stale 10M panel text in incidence_note",
    "incidence_note" in paths,
    f"got paths={sorted(paths)}",
)
_check(
    "Rule 6 violations all carry rule=6",
    rules == {6},
    f"got rules={rules}",
)


print()
print("--- test_rule6_allowlist_holds_for_preserved_rates ---")

# delta_pp_point (post_pen - pre_pen), primary_test_p_value, and the
# `significant` boolean are legitimately byte-identical under uniform
# panel scaling. Byte-copying them from parent must NOT fire Rule 6.
parent = _parent_with_weight(observed_n=475_903, projection_weight=32.99)
parent["diagnostics"]["significance"] = {
    "n_observed": 10_000_007,
    "delta_pp_point": 11.703,
    "primary_test_p_value": 0.0,
    "significant": True,
}
sub = build_subset_payload(parent, 0.602, "fixture_r6_allowlist", "Boomer")
sub["diagnostics"]["significance"]["delta_pp_point"] = 11.703
sub["diagnostics"]["significance"]["primary_test_p_value"] = 0.0
sub["diagnostics"]["significance"]["significant"] = True

violations = _check_rule6_byte_copy(sub, parent)
_check(
    "Rule 6 does NOT flag delta_pp_point byte match (rate delta)",
    not any(v["path"] == "diagnostics.significance.delta_pp_point"
            for v in violations),
    f"got violations={violations}",
)
_check(
    "Rule 6 does NOT flag primary_test_p_value byte match (0.0 stays 0.0)",
    not any(v["path"] == "diagnostics.significance.primary_test_p_value"
            for v in violations),
    f"got violations={violations}",
)
_check(
    "Rule 6 does NOT flag significant boolean byte match",
    not any(v["path"] == "diagnostics.significance.significant"
            for v in violations),
    f"got violations={violations}",
)


print()
print("--- test_rule6_flags_sentiment_sub_cluster_byte_copy ---")

# The exact F1 Coke Boomer defect: sentiment.top_positive[i].count was
# a byte copy of the parent's 4924-sample cluster count on the subset.
parent = _parent_with_weight(observed_n=475_903, projection_weight=32.99)
parent["sentiment"] = {
    "enabled": True,
    "sample_size": 4923,
    "top_positive": [
        {"summary": "Cluster A", "count": 83},
        {"summary": "Cluster B", "count": 61},
        {"summary": "Cluster C", "count": 47},
    ],
    "top_negative": [{"summary": "Cluster N1", "count": 11}],
    "top_neutral": [{"summary": "Cluster T1", "count": 43}],
}
sub = build_subset_payload(parent, 0.602, "fixture_r6_sentiment", "Boomer")
# Force the sentiment sub-cluster counts to byte-match parent (the
# defect signature).
sub["sentiment"]["top_positive"] = copy.deepcopy(parent["sentiment"]["top_positive"])
sub["sentiment"]["top_negative"] = copy.deepcopy(parent["sentiment"]["top_negative"])
sub["sentiment"]["top_neutral"] = copy.deepcopy(parent["sentiment"]["top_neutral"])

violations = _check_rule6_byte_copy(sub, parent)
count_paths = [v["path"] for v in violations
               if v["path"].endswith(".count")]
_check(
    "Rule 6 flags every byte-copied top_positive[i].count",
    all(f"sentiment.top_positive[{i}].count" in count_paths
        for i in range(3)),
    f"got count_paths={count_paths}",
)
_check(
    "Rule 6 flags byte-copied top_negative[0].count",
    "sentiment.top_negative[0].count" in count_paths,
    f"got count_paths={count_paths}",
)
_check(
    "Rule 6 flags byte-copied top_neutral[0].count",
    "sentiment.top_neutral[0].count" in count_paths,
    f"got count_paths={count_paths}",
)


print()
print("--- test_rule6_does_not_fire_on_similar_size_panels ---")

# When the subset panel is close to parent size (panel_ratio >= 0.9),
# byte-copies of size-sensitive fields are not flagged. The check is
# scoped to materially smaller subsets to avoid false positives on
# same-cohort or near-full-cohort reads.
parent = _parent_with_weight(observed_n=475_903, projection_weight=32.99)
parent["diagnostics"]["significance"] = {
    "n_discordant": 1_170_283, "primary_test_z": 668.127,
}
sub = copy.deepcopy(parent)
# Subset panel same size as parent (panel_ratio = 1.0).
violations = _check_rule6_byte_copy(sub, parent)
_check(
    "Rule 6 does NOT fire when panel_ratio >= 0.9",
    len(violations) == 0,
    f"got violations={violations}",
)


print()
print("--- test_rule6_excludes_demographics_frozen_by_rule2 ---")

# demographics.*.count is a Rule 2 frozen peer-shared bucket count. It
# is legitimately byte-identical between the F1 and F2 subsets of the
# same cohort, and coincidentally byte-identical to the parent at
# low-count buckets (Non-Binary=2, Trans Female=1, Other=1). Rule 6
# must skip demographics.* entirely because peer-freeze correctness
# is verified by Rule 2, not Rule 6.
parent = _parent_with_weight(observed_n=475_903, projection_weight=32.99)
parent["demographics"] = {
    "pre": {
        "gender": [
            {"value": "Female", "count": 186, "percentage": 55.0},
            {"value": "Non-Binary", "count": 2, "percentage": 0.6},
            {"value": "Trans Female", "count": 1, "percentage": 0.2},
        ],
        "age": [
            {"value": "Other", "count": 1, "percentage": 0.4},
        ],
    },
}
sub = build_subset_payload(parent, 0.602, "fixture_r6_demo_excl", "Boomer")
sub["demographics"] = {
    "pre": {
        "gender": [
            {"value": "Female", "count": 189, "percentage": 54.41},
            # Coincidentally byte-identical to parent:
            {"value": "Non-Binary", "count": 2, "percentage": 0.67},
            {"value": "Trans Female", "count": 1, "percentage": 0.18},
        ],
        "age": [
            {"value": "Other", "count": 1, "percentage": 0.12},
        ],
    },
}
violations = _check_rule6_byte_copy(sub, parent)
demo_hits = [v["path"] for v in violations
             if v["path"].startswith("demographics.")]
_check(
    "Rule 6 excludes demographics.* from byte-copy checks",
    len(demo_hits) == 0,
    f"unexpected demographics violations: {demo_hits}",
)


print()
print("--- test_rule6_verify_subset_invariants_integration ---")

# End-to-end: verify_subset_invariants must include Rule 6 violations
# in its return list.
parent = _parent_with_weight(observed_n=475_903, projection_weight=32.99)
parent["diagnostics"]["significance"] = {
    "n_observed": 10_000_007,
    "n_discordant": 1_170_283,
    "detection_floor_pp": 0.034,
}
sub = build_subset_payload(parent, 0.602, "fixture_r6_integ", "Boomer")
# Inject the defect
sub["diagnostics"]["significance"]["n_discordant"] = 1_170_283
sub["diagnostics"]["significance"]["detection_floor_pp"] = 0.034
all_violations = verify_subset_invariants(sub, parent, 0.602)
rule6 = [v for v in all_violations if v.get("rule") == 6]
_check(
    "verify_subset_invariants surfaces Rule 6 byte-copy violations",
    len(rule6) >= 2,
    f"got {len(rule6)} rule-6 violations: "
    f"{[v['path'] for v in rule6]}",
)


# ---------------------------------------------------------------------
# Rules 7-12 (2026-09-08, Liz WoF Boomer QC memo)
# ---------------------------------------------------------------------

# --- Rule 7: campaign-level rate byte-copy prohibition ---------------

print()
print("--- test_rule7_campaign_rate_byte_copy_flags_defect ---")
parent = _parent_payload()
sub_bad = copy.deepcopy(parent)
sub_bad["audience_size"] = 285_065  # Boomer subset
sub_bad["projected_audience_size"] = 9_404_311
# Keep parent's campaign-level rates verbatim (the exact defect).
# totals.audience_pen_pre_pct + audience_pen_post_pct byte-match.
violations = _check_rule7_campaign_rate_byte_copy(sub_bad, parent, 0.599)
paths = sorted(v["path"] for v in violations)
_check(
    "Rule 7 flags totals.audience_pen_pre_pct byte-copy",
    "totals.audience_pen_pre_pct" in paths,
    f"got {paths}",
)
_check(
    "Rule 7 flags totals.audience_pen_post_pct byte-copy",
    "totals.audience_pen_post_pct" in paths,
    f"got {paths}",
)

print()
print("--- test_rule7_passes_when_cohort_fraction_ge_090 ---")
# When cohort_fraction >= 0.9, the subset is nearly the whole cohort
# and rate carryover is expected. Rule 7 must NOT fire.
sub_big = copy.deepcopy(sub_bad)
violations = _check_rule7_campaign_rate_byte_copy(sub_big, parent, 0.95)
_check(
    "Rule 7 silent when cohort_fraction >= 0.9",
    len(violations) == 0,
    f"got {len(violations)} violations",
)

print()
print("--- test_rule7_passes_on_cohort_differentiated_rates ---")
# Boomer-differentiated rates (not byte-matching parent to 3dp).
sub_ok = copy.deepcopy(parent)
sub_ok["audience_size"] = 285_065
sub_ok["totals"]["audience_pen_pre_pct"] = 23.431
sub_ok["totals"]["audience_pen_post_pct"] = 25.869
violations = _check_rule7_campaign_rate_byte_copy(sub_ok, parent, 0.599)
_check(
    "Rule 7 silent on cohort-differentiated rates",
    len(violations) == 0,
    f"got {[v['path'] for v in violations]}",
)


# --- Rule 8: per-platform rate byte-copy tolerance -------------------

print()
print("--- test_rule8_per_platform_rate_byte_copy_flags_defect ---")
parent = _parent_payload()
# Extend parent per_platform to 11 rows so the tolerance-of-1 rule
# has room to be meaningful.
extra = [
    {"platform": "YouTube",
     "pre_users": 300_001, "post_users": 320_003,
     "pre_users_projected": 471_003, "post_users_projected": 502_403,
     "lift_pct_users": 6.67, "pre_pen_pct": 3.00, "post_pen_pct": 3.20},
    {"platform": "Instagram",
     "pre_users": 200_003, "post_users": 210_003,
     "pre_users_projected": 314_003, "post_users_projected": 329_003,
     "lift_pct_users": 5.00, "pre_pen_pct": 2.00, "post_pen_pct": 2.10},
    {"platform": "Reddit",
     "pre_users": 150_003, "post_users": 155_003,
     "pre_users_projected": 236_003, "post_users_projected": 244_003,
     "lift_pct_users": 3.33, "pre_pen_pct": 1.50, "post_pen_pct": 1.55},
    {"platform": "Snapchat",
     "pre_users": 5_003, "post_users": 5_013,
     "pre_users_projected": 7_953, "post_users_projected": 7_973,
     "lift_pct_users": 0.20, "pre_pen_pct": 0.05, "post_pen_pct": 0.05},
    {"platform": "Threads",
     "pre_users": 10_003, "post_users": 10_053,
     "pre_users_projected": 15_703, "post_users_projected": 15_783,
     "lift_pct_users": 0.50, "pre_pen_pct": 0.10, "post_pen_pct": 0.10},
    {"platform": "Twitch",
     "pre_users": 12_003, "post_users": 12_063,
     "pre_users_projected": 18_853, "post_users_projected": 18_953,
     "lift_pct_users": 0.50, "pre_pen_pct": 0.12, "post_pen_pct": 0.12},
    {"platform": "LinkedIn",
     "pre_users": 30_003, "post_users": 31_003,
     "pre_users_projected": 47_113, "post_users_projected": 48_683,
     "lift_pct_users": 3.33, "pre_pen_pct": 0.30, "post_pen_pct": 0.31},
    {"platform": "Pinterest",
     "pre_users": 50_003, "post_users": 51_003,
     "pre_users_projected": 78_513, "post_users_projected": 80_083,
     "lift_pct_users": 2.00, "pre_pen_pct": 0.50, "post_pen_pct": 0.51},
]
parent["per_platform"].extend(extra)

# Subset copies parent per-platform rates verbatim on every row.
sub_bad = copy.deepcopy(parent)
sub_bad["audience_size"] = 285_065
violations = _check_rule8_per_platform_rate_byte_copy(sub_bad, parent, 0.599)
_check(
    "Rule 8 fires when many per-platform rates byte-match parent",
    len(violations) == 1,
    f"expected 1 aggregate violation, got {len(violations)}",
)
if violations:
    detail = violations[0].get("subset_value") or {}
    _check(
        "Rule 8 reports byte_match_count > tolerance",
        detail.get("byte_match_count", 0) > 1,
        f"detail={detail}",
    )

print()
print("--- test_rule8_passes_with_at_most_1_platform_field_byte_match ---")
# Differentiated per-platform rates: at most 1 byte-match allowed.
sub_ok = copy.deepcopy(sub_bad)
for i, row in enumerate(sub_ok["per_platform"]):
    row["pre_pen_pct"] = round(float(row["pre_pen_pct"]) * 1.5 + 0.0037 * (i + 1), 4)
    row["post_pen_pct"] = round(float(row["post_pen_pct"]) * 1.6 + 0.0041 * (i + 1), 4)
violations = _check_rule8_per_platform_rate_byte_copy(sub_ok, parent, 0.599)
_check(
    "Rule 8 silent when per-platform rates are cohort-differentiated",
    len(violations) == 0,
    f"got {[v['subset_value'] for v in violations]}",
)


# --- Rule 9: demographic pre/post movement ---------------------------

print()
print("--- test_rule9_flags_frozen_demo_deltas ---")
parent = _parent_payload()
sub = copy.deepcopy(parent)
sub["audience_size"] = 285_065
# Materially moved users (post != pre), but demographics.post == pre.
sub["totals"]["pre_users"] = 100_003
sub["totals"]["post_users"] = 150_007
# demographics.pre == demographics.post is already the case in the fixture.
violations = _check_rule9_demo_pre_post_movement(sub)
paths = sorted(v["path"] for v in violations)
_check(
    "Rule 9 fires on age with frozen pre==post",
    "demographics.post.age" in paths,
    f"got {paths}",
)
_check(
    "Rule 9 fires on gender with frozen pre==post",
    "demographics.post.gender" in paths,
    f"got {paths}",
)

print()
print("--- test_rule9_silent_when_movement_ge_1pct ---")
# One bucket moved by >= 0.01pp - Rule 9 satisfied for that category.
sub_ok = copy.deepcopy(sub)
sub_ok["demographics"]["post"]["age"][0]["percentage"] += 0.31
sub_ok["demographics"]["post"]["age"][1]["percentage"] -= 0.31
sub_ok["demographics"]["post"]["gender"][0]["percentage"] += 0.34
sub_ok["demographics"]["post"]["gender"][1]["percentage"] -= 0.34
violations = _check_rule9_demo_pre_post_movement(sub_ok)
paths_ok = sorted(v["path"] for v in violations)
_check(
    "Rule 9 silent on age after applying pre->post shift",
    "demographics.post.age" not in paths_ok,
    f"got {paths_ok}",
)
_check(
    "Rule 9 silent on gender after applying pre->post shift",
    "demographics.post.gender" not in paths_ok,
    f"got {paths_ok}",
)

print()
print("--- test_rule9_autofix_applies_converter_shift ---")
sub_bad = copy.deepcopy(sub)
sub_fixed = _autofix_rule9_apply_boomer_demo_shift(sub_bad)
post_age = sub_fixed["demographics"]["post"]["age"]
pre_age = sub_fixed["demographics"]["pre"]["age"]
p_older = next(r["percentage"] for r in post_age if r["value"] == "65 or Older")
r_older = next(r["percentage"] for r in pre_age if r["value"] == "65 or Older")
_check(
    "Rule 9 auto-fix moves 65+ share > pre share",
    p_older > r_older,
    f"pre 65+={r_older}, post 65+={p_older}",
)
p_female = next(r["percentage"] for r in sub_fixed["demographics"]["post"]["gender"]
                if r["value"] == "Female")
r_female = next(r["percentage"] for r in sub_fixed["demographics"]["pre"]["gender"]
                if r["value"] == "Female")
_check(
    "Rule 9 auto-fix moves Female share > pre share",
    p_female > r_female,
    f"pre F={r_female}, post F={p_female}",
)


# --- Rule 10: users-pipe / hits-pipe coherence -----------------------

print()
print("--- test_rule10_flags_users_hits_ratio_drift ---")
parent = _parent_payload()
sub = copy.deepcopy(parent)
sub["audience_size"] = 285_065
# Users scaled by cohort_fraction; hits kept at parent scale (the
# defect signature). Ratio drifts from parent's.
sub["totals"]["pre_users"] = 100_003
sub["totals"]["post_users"] = 150_007
# Keep hits AT parent's scale so the ratio spikes.
sub["totals"]["pre_hits"] = 6_941_783
sub["totals"]["post_hits"] = 8_442_207
violations = _check_rule10_users_hits_ratio_coherence(sub, parent)
paths = sorted(v["path"] for v in violations)
_check(
    "Rule 10 fires when hits/users ratio drifts >15% on either phase",
    any("hits_over" in p for p in paths),
    f"got {paths}",
)

print()
print("--- test_rule10_silent_on_coherent_scaled_pipelines ---")
# Scale hits AND users by the same fraction, per-phase. Rule 10 checks
# per-phase hits/users ratio drift, so both phases need coherent
# scaling to keep their ratio near parent's.
scale = 0.049
sub_ok = copy.deepcopy(sub)
sub_ok["totals"]["pre_users"] = int(round(parent["totals"]["pre_users"] * scale))
sub_ok["totals"]["post_users"] = int(round(parent["totals"]["post_users"] * scale))
sub_ok["totals"]["pre_hits"] = int(round(parent["totals"]["pre_hits"] * scale))
sub_ok["totals"]["post_hits"] = int(round(parent["totals"]["post_hits"] * scale))
violations = _check_rule10_users_hits_ratio_coherence(sub_ok, parent)
_check(
    "Rule 10 silent when both pipelines scale together",
    len(violations) == 0,
    f"got {[v['path'] for v in violations]}",
)


# --- Rule 11: age filter tolerance -----------------------------------

print()
print("--- test_rule11_flags_boomer_leakage_below_55 ---")
parent = _parent_payload()
sub = copy.deepcopy(parent)
sub["audience_size"] = 285_065
# Tag the subset as a Boomer cut so Rule 11 knows the targets.
sub["diagnostics"]["cohort_derivation"] = {"cohort": "Boomer 55+"}
# Age distribution carries 1.57% leakage below 55.
sub["demographics"]["pre"]["age"] = [
    {"value": "65 or Older", "percentage": 59.78},
    {"value": "55-64", "percentage": 38.65},
    {"value": "45-54", "percentage": 0.47},
    {"value": "35-44", "percentage": 0.23},
    {"value": "25-34", "percentage": 0.21},
    {"value": "18-24", "percentage": 0.36},
    {"value": "17 and Under", "percentage": 0.18},
    {"value": "Other", "percentage": 0.12},
]
sub["demographics"]["post"]["age"] = copy.deepcopy(sub["demographics"]["pre"]["age"])
violations = _check_rule11_age_filter_tolerance(sub)
_check(
    "Rule 11 fires on Boomer subset with sub-55 leakage > 0.5pp",
    len(violations) >= 1,
    f"got {[v['path'] for v in violations]}",
)

print()
print("--- test_rule11_autofix_renormalizes_leakage_to_targets ---")
sub_fixed = _autofix_rule11_renormalize_age(sub)
for phase in ("pre", "post"):
    age = sub_fixed["demographics"][phase]["age"]
    leak = sum(float(r["percentage"]) for r in age
               if r["value"] not in ("55-64", "65 or Older"))
    _check(
        f"Rule 11 auto-fix zeros non-target buckets ({phase})",
        leak < 1e-4,
        f"phase={phase} leakage={leak}",
    )
    tgt = sum(float(r["percentage"]) for r in age
              if r["value"] in ("55-64", "65 or Older"))
    _check(
        f"Rule 11 auto-fix sums target buckets to ~100% ({phase})",
        abs(tgt - 100.0) < 0.5,
        f"phase={phase} target sum={tgt}",
    )


# --- Rule 12: conversion rate consistency ----------------------------

print()
print("--- test_rule12_flags_wrong_conversion_rate ---")
# Subject-family match on WoF; canonical rate is $10.00.
sub = _parent_payload()
sub["project_name"] = "Coca-Cola x Wheel of Fortune (Next Day Air)"
sub["valuation"]["rates"] = {"conv_value_per_user": 12.00}
violations = _check_rule12_conversion_rate_consistency(sub)
_check(
    "Rule 12 fires when conv_value_per_user disagrees with canonical",
    len(violations) == 1,
    f"got {[v['path'] for v in violations]}",
)
if violations:
    _check(
        "Rule 12 reports canonical rate on violation",
        violations[0].get("parent_value") == 10.00,
        f"parent_value={violations[0].get('parent_value')}",
    )

print()
print("--- test_rule12_silent_on_canonical_rate ---")
sub_ok = copy.deepcopy(sub)
sub_ok["valuation"]["rates"]["conv_value_per_user"] = 10.00
violations = _check_rule12_conversion_rate_consistency(sub_ok)
_check(
    "Rule 12 silent when rate matches canonical family",
    len(violations) == 0,
    f"got {[v['path'] for v in violations]}",
)

print()
print("--- test_rule12_silent_on_unknown_subject_family ---")
sub_unknown = copy.deepcopy(sub)
sub_unknown["project_name"] = "Some Unmatched Subject x Some Event"
sub_unknown["valuation"]["rates"]["conv_value_per_user"] = 99.99
violations = _check_rule12_conversion_rate_consistency(sub_unknown)
_check(
    "Rule 12 silent when subject family is not in the canonical list",
    len(violations) == 0,
    f"got {[v['path'] for v in violations]}",
)

print()
print("--- test_rule12_autofix_stamps_canonical_rate ---")
sub_bad = _parent_payload()
sub_bad["project_name"] = "Pepsi x Wheel of Fortune (Next Day Air) Rerun"
sub_bad["valuation"]["rates"] = {"conv_value_per_user": 12.00}
sub_fixed = _autofix_rule12_apply_canonical_conversion_rate(sub_bad)
_check(
    "Rule 12 auto-fix stamps canonical $10.00 for WoF family",
    sub_fixed["valuation"]["rates"]["conv_value_per_user"] == 10.00,
    f"got {sub_fixed['valuation']['rates']['conv_value_per_user']}",
)


# --- End-to-end: verify_subset_invariants surfaces Rules 7-12 --------

print()
print("--- test_rules_7_to_12_wired_into_verify_subset_invariants ---")
# A subset that carries EVERY defect at once should surface all six
# rule numbers in the aggregate call.
parent = _parent_payload()
parent["per_platform"].extend(extra)  # 11 platforms so Rule 8 has bite
sub = copy.deepcopy(parent)
sub["audience_size"] = 285_065
sub["projected_audience_size"] = 9_404_311
sub["projection_weight"] = 32.99
sub["project_name"] = "Coca-Cola x Wheel of Fortune (Next Day Air) - Boomers"
sub["diagnostics"]["cohort_derivation"] = {"cohort": "Boomer 55+"}
# Rule 7: campaign rates byte-copy parent (leave as-is)
# Rule 8: per-platform rates byte-copy parent (leave as-is)
# Rule 9: post demos == pre demos (leave as-is)
# Rule 10: hits kept at parent scale, users scaled down
sub["totals"]["pre_users"] = 100_003
sub["totals"]["post_users"] = 150_007
# hits stay at parent scale to force ratio drift
# Rule 11: leakage below 55
sub["demographics"]["pre"]["age"] = [
    {"value": "65 or Older", "percentage": 59.78},
    {"value": "55-64", "percentage": 38.65},
    {"value": "45-54", "percentage": 0.47},
    {"value": "35-44", "percentage": 0.90},
]
sub["demographics"]["post"]["age"] = copy.deepcopy(sub["demographics"]["pre"]["age"])
# Rule 12: wrong conversion rate
sub["valuation"]["rates"] = {"conv_value_per_user": 12.00}
violations = verify_subset_invariants(sub, parent, 0.599)
rules_seen = sorted({v.get("rule") for v in violations if isinstance(v.get("rule"), int)})
for rule_num in (7, 8, 9, 10, 11, 12):
    _check(
        f"verify_subset_invariants surfaces Rule {rule_num}",
        rule_num in rules_seen,
        f"rules seen: {rules_seen}",
    )


# --- Composite auto-fix helper --------------------------------------

print()
print("--- test_apply_auto_fixes_for_rules_7_to_12_composite ---")
parent = _parent_payload()
sub = copy.deepcopy(parent)
sub["audience_size"] = 285_065
sub["project_name"] = "Coca-Cola x Wheel of Fortune (Next Day Air) - Boomers"
sub["diagnostics"]["cohort_derivation"] = {"cohort": "Boomer 55+"}
# Rule 9: frozen demos
sub["totals"]["pre_users"] = 100_003
sub["totals"]["post_users"] = 150_007
# Rule 11: leakage
sub["demographics"]["pre"]["age"] = [
    {"value": "65 or Older", "percentage": 59.78},
    {"value": "55-64", "percentage": 38.65},
    {"value": "45-54", "percentage": 0.47},
    {"value": "35-44", "percentage": 0.90},
]
sub["demographics"]["post"]["age"] = copy.deepcopy(sub["demographics"]["pre"]["age"])
# Rule 12: wrong rate
sub["valuation"]["rates"] = {"conv_value_per_user": 12.00}

fixed = apply_auto_fixes_for_rules_7_to_12(sub, parent, 0.599)
# Rule 11 fixed: no leakage
leak = sum(float(r["percentage"]) for r in fixed["demographics"]["pre"]["age"]
           if r["value"] not in ("55-64", "65 or Older"))
_check(
    "Composite auto-fix zeros Rule 11 leakage",
    leak < 1e-4,
    f"leakage after fix: {leak}",
)
# Rule 12 fixed: canonical rate
_check(
    "Composite auto-fix stamps Rule 12 canonical rate",
    fixed["valuation"]["rates"]["conv_value_per_user"] == 10.00,
    f"rate after fix: {fixed['valuation']['rates']['conv_value_per_user']}",
)
# Rule 9 fixed: age shift applied
p_older = next(r["percentage"] for r in fixed["demographics"]["post"]["age"]
               if r["value"] == "65 or Older")
r_older = next(r["percentage"] for r in fixed["demographics"]["pre"]["age"]
               if r["value"] == "65 or Older")
_check(
    "Composite auto-fix moves Rule 9 65+ share pre->post",
    p_older > r_older,
    f"pre 65+={r_older}, post 65+={p_older}",
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
print("PASS: BPIQ subset-cut regression tests")
sys.exit(0)
