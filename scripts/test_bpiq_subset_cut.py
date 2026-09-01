#!/usr/bin/env python3
"""Regression tests for the BPIQ subset-cut helper.

Codified 2026-09-01 after Liz caught three subset-invariant defects on
the same-day Wheel of Fortune Boomer cuts of Coca-Cola and Pepsi. See
.cursor/rules/bpiq-subset-cut-invariants.mdc for the rule tree and
bg-webapp/migration/bpiq_subset_cut.py for the helper.

Covers the four subset invariants:

  1. Anchor to observed cohort n, not the panel-base construct.
  2. One frozen n across all brand reads of the same event.
  3. Subset never exceeds parent on any per-platform / per-touchpoint /
     conversion row.
  4. Behavioral multipliers x cohort_fraction never exceed 1.0.

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
    validate_before_write,
    validate_bpiq_payload,
    verify_subset_invariants,
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

# Supplying explicit observed_cohort_n bypasses the hold.
override = build_subset_payload(
    naked_parent, 0.599, "fixture_hold", "Boomer",
    observed_cohort_n=475_902,
)
_check(
    "explicit observed_cohort_n bypasses hold",
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
# 9. Smoke test: real shipped WoF Boomer payloads (when available)
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

    for tag, sub, parent in (("Coke", coke_b, coke_p),
                             ("Pepsi", pepsi_b, pepsi_p)):
        v = verify_subset_invariants(sub, parent, 0.599)
        _check(
            f"real shipped {tag} Boomer: zero subset invariant violations",
            len(v) == 0,
            f"got {len(v)}: first={v[0] if v else None}",
        )

    # Peer coherence: shipped payloads share n but demos drift slightly
    # (built independently per brand rather than through the shared
    # helper). We report the strict-tolerance drift so Jenna can decide
    # whether to re-run through enforce_shared_cohort_n.
    v = verify_subset_invariants(coke_b, coke_p, 0.599, strict_shared_cohort=pepsi_b)
    rule2 = [x for x in v if x["rule"] == 2]
    print(f"    [info] shipped Coke vs Pepsi Boomer Rule 2 drift: "
          f"{len(rule2)} bucket(s) exceed 0.5pp / 0.5% tolerance")
    for x in rule2[:3]:
        print(f"      - {x['path']}: coke={x['subset_value']}, "
              f"pepsi={x['parent_value']}")
else:
    print(f"    [skip] shipped payloads not present at {_ship_dir}")


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
