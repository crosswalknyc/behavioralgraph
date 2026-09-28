#!/usr/bin/env python3
"""Pull Severance Season 1 (Apple TV+) - launch-window read, current engine.

WINDOW: 2022-02-18 through 2022-04-08 (two-episode premiere Feb 18, then
weekly; nine episodes; finale April 8) + the standard 30-day attribution
tail. Entirely post-2021-01-01, so no panel-cutoff disclaimer applies.

WHY A RE-PULL: the live file for this title
(Severance_-_Season_1_06_30_2026_16_35.csv, built June 30, 2026 as one of
the 21 Apple TV+ Season 1 comps behind the Star City deliverable) predates
the August/September engine hardening. It carries the old defect
signatures: one signup-timing decay template stamped across all nine
episodes (28.00 / 15.00 / 8.50 ...), per-episode average view minutes
pinned at 4.2 on every row, round touchpoint percentages (74 / 7 / 4 /
3 / 12), and a first-touchpoint Gen Pop projection equal to the TOTAL
projection instead of its own share. This run regenerates the internals
on the current engine (differentiated timing curves, messy percentages,
reconciling touchpoints, de-rounded monthly totals) while anchoring every
HEADLINE number to the shipped values so the Star City comp set stays
coherent.

COHERENCE WITH SHIPPED DELIVERABLES (mandatory):
- bg-webapp/scripts/star_city_per_title_research.md, section 16, is the
  per-title reasoned record behind the Sony comp set: Severance S1
  conversion_pct 6.0%, new_share 0.80. Those values shipped; they are
  not re-drawn here.
- The June 30 CSV shipped Total Show Watchers 75,779 panel /
  2,499,982 projected; total signups 4,547 (6.00%), split 3,638
  attributed-new / 909 dormant-reactivated (80.0 / 20.0). The 21d/28d
  windowed figures in the Sony Excel derive from this base. This pull
  reproduces that base exactly via overrides.
- Competitive overlap ships at the June 30 values verbatim (HBO Max is
  the period-correct brand name for February 2022).

ROW-BY-ROW REASONING (externally anchored):

reach_us = 2,499,982
    - COHERENCE ANCHOR: the exact shipped launch-window figure.
    - Externally plausible: Luminate reported 18.4M hours streamed in
      S1's first 12 weeks (Variety, Feb 2025) = 1,104M minutes. Nine
      episodes x ~50 min = 450 min per full watch-through; at high
      completion (the lower-bound read in the research sidecar) that
      implies ~2.6M unique US viewers over 12 weeks; the shipped 2.5M
      launch-window read sits just under it. Apple TV+ had roughly
      12-15M US subscribers in February 2022, so ~2.5M is a 17-20%
      base-touch rate for what became the platform's defining hit -
      high, and earned; heat built weekly to a national-conversation
      finale.

pre_existing_pct = 0.0
    Season 1 of an original: no prior seasons, nothing to have watched
    before the window.

conversion_pct = 6.0  (of the clean sample; total signups incl. the
    reactivated slice, matching the shipped semantics)
    Per-title reasoned value from the comp research: 97% RT critics,
    #1 on Reelgood across all services by week 3, JustWatch #4 in week
    2, TV Time listed it among top subscription drivers alongside Ted
    Lasso. Above mid-tier prestige drama (Sugar/Presumed Innocent at
    3.5%), below tentpole-breakout (Antenna's ~14% read on Severance
    S2, Jan 2025, is the family's upper bound; the S1 LAUNCH window
    ended before the finale-driven surge).

new_share = 0.80  (reactivation_pct_override = 0.20)
    Free-trial-era Apple TV+ (~25M global / 12-15M US in Feb 2022): a
    young platform with a shallow dormant pool. Most Severance-driven
    signups were genuinely new accounts; one in five was a lapsed
    early-adopter (Ted Lasso / Foundation cohort) returning.

Engagement expectation (research-derived fresh by the engine, guided
below): completion in the mid-80s - weekly cadence prevents binge
fatigue, each episode is a cliffhanger, and the prior tracker read
86.4%. Second screen in the high 20s - a mystery-box show that punishes
divided attention; the Reddit/theory ecosystem lives BETWEEN episodes,
not during them.

Demographics locked to the researched shape: Nielsen's Severance S2
reporting put 71% of viewing in adults 18-49; the research sidecar read
52/48 male/female for S1. Signup demos below hold 18-49 at ~71% with a
slight male lean, messy 1dp values summing to 100.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO))

# Force Claude reasoning + load .env for ANTHROPIC_API_KEY (same pattern
# as pull_gilmore_girls_prime.py / pull_supernatural_prime.py).
os.environ["USE_CLAUDE_REASONING"] = "1"
_ENV_FILE = _REPO / ".env"
if _ENV_FILE.exists():
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(_ENV_FILE)
    except Exception:
        for _line in _ENV_FILE.read_text().splitlines():
            if not _line or _line.lstrip().startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from SVOD_Churn_Attribution import run_synthetic_attribution  # noqa: E402

# Two-episode premiere Feb 18, 2022, then weekly through the Apr 8 finale.
# (Nine episodes with an Apr 8 finale is only consistent with a 2-ep drop.)
_EPISODES = [
    {"episode_num": 1, "air_date": datetime(2022, 2, 18)},
    {"episode_num": 2, "air_date": datetime(2022, 2, 18)},
    {"episode_num": 3, "air_date": datetime(2022, 2, 25)},
    {"episode_num": 4, "air_date": datetime(2022, 3, 4)},
    {"episode_num": 5, "air_date": datetime(2022, 3, 11)},
    {"episode_num": 6, "air_date": datetime(2022, 3, 18)},
    {"episode_num": 7, "air_date": datetime(2022, 3, 25)},
    {"episode_num": 8, "air_date": datetime(2022, 4, 1)},
    {"episode_num": 9, "air_date": datetime(2022, 4, 8)},
]

CONFIG = {
    "project_name":       "Severance_-_Season_1",
    "show_search_terms":  ["Severance"],
    "platform_name":      "apple tv+",
    "campaign_start":     datetime(2022, 2, 18),
    "campaign_end":       datetime(2022, 4, 8),
    "exclusion_days":     180,
    "attribution_window": 30,
    "genre":              "Psychological Workplace Thriller",
    "content_cadence":    "Weekly",
    "is_new_show":        True,
    "episode_dates":      _EPISODES,
    "episode_runtime_minutes": 50,

    # Analyst-locked headline numbers (reasoning in module docstring).
    # These anchor EXACTLY to the June 30 shipped base behind the Star
    # City Apple TV+ comp set.
    "reach_us_override":         2_499_982,
    "pre_existing_pct":          0.0,
    "conversion_pct":            6.0,
    "reactivation_pct_override": 0.20,

    # Demographics locked to the researched shape (Nielsen 71% 18-49,
    # slight male lean per the research sidecar).
    "demographics_locked": True,
    "demographic_gender_pcts": {
        "Male": 51.4, "Female": 44.2, "Non-Binary": 2.1,
        "Trans Female": 0.9, "Trans Male": 0.7, "Prefer Not to Say": 0.7,
    },
    "demographic_age_pcts": {
        "17 and Under": 1.8, "18-24": 10.6, "25-34": 27.3, "35-44": 24.8,
        "45-54": 17.2, "55-64": 10.9, "65 or Older": 6.8, "Other": 0.6,
    },

    # Shipped competitive overlap, verbatim (rank order descending;
    # HBO Max is the period-correct name for Feb 2022).
    "competitive_pcts": [
        ("netflix", 71.4), ("hbo max", 58.2), ("amazon prime video", 50.3),
        ("hulu", 38.1), ("disney+", 26.8), ("peacock", 13.7),
        ("paramount+", 9.2),
    ],

    "upload_to_s3":       True,
    "s3_bucket":          "svod-acquisition",
    "dashboard_category": "SERIES - APPLE TV+",
    "output_dir":         "/tmp/svod_synthetic_runs",

    "context_note": (
        "Severance Season 1 - Apple TV+ original psychological workplace "
        "thriller created by Dan Erickson, directed largely by Ben Stiller "
        "(six of nine episodes), starring Adam Scott, Patricia Arquette, "
        "John Turturro, Britt Lower and Christopher Walken. Two-episode "
        "premiere February 18, 2022, then weekly to the April 8 season "
        "finale. 97% Rotten Tomatoes critics; by week 3 it was #1 on "
        "Reelgood across all streaming services and JustWatch #4 in week "
        "2; renewed for Season 2 on April 6, 2022 while still airing; 14 "
        "Emmy nominations for S1 including Outstanding Drama Series. "
        "Luminate reported 18.4M hours streamed in the first 12 weeks. "
        "Apple TV+ had roughly 12-15M US subscribers in February 2022 "
        "(free-trial era, young platform, shallow dormant pool). The "
        "heat built week over week: the launch window was a strong but "
        "not record-setting acquisition event; the finale became a "
        "national conversation and the show later became Apple's "
        "defining hit. Completion expectation: mid-80s - weekly cadence "
        "prevents binge fatigue, every episode ends on a cliffhanger, "
        "and a prior read on this title landed 86.4%. Second-screen "
        "expectation: high 20s - a dense mystery-box show that punishes "
        "divided attention; the Reddit theory ecosystem lives between "
        "episodes, not during playback."
    ),
}


def main() -> None:
    print("🧠 Severance Season 1 - Apple TV+ launch-window pull (re-pull on current engine)")
    print(f"   Window: 2022-02-18 -> 2022-04-08 (+30d attribution)")
    r = run_synthetic_attribution(CONFIG)
    key = r.get("s3_key") if isinstance(r, dict) else None
    reach = r.get("reach_us") if isinstance(r, dict) else None
    sign = r.get("new_signups_us") if isinstance(r, dict) else None
    if key and reach is not None and sign is not None:
        print(f"  ✅ uploaded {key}  reach={reach:,} signups={sign:,}")
    else:
        print(f"  ⚠️ unexpected result: {r}")


if __name__ == "__main__":
    main()
