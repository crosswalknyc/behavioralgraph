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
