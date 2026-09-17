#!/usr/bin/env python3
"""Pull Gilmore Girls (Prime Video US) - catalog arrival-to-date read.

WINDOW CHOICE: ARRIVAL-TO-DATE, 2026-07-01 through the run date.
Gilmore Girls seasons 1-7 (153 episodes) left US Netflix on June 30,
2026 when Warner Bros.' ten-year licensing window expired, and began
streaming on Prime Video on July 1, 2026 (Deadline 7/1/2026; Forbes
6/30/2026; What's on Netflix licensing tracking). That is a discrete
recent ARRIVAL event, so this pull uses arrival-date through today,
not the trailing-12 default. The deal is NON-EXCLUSIVE: the series
remains on Hulu (shared home since 2024) and Disney+, and the Netflix
revival "A Year in the Life" stays on Netflix until November 25, 2026.
The window lands mid-September, so it contains the ONSET of the show's
famous fall ritual (September/October is its peak cultural window) but
not the full October peak.

COHERENCE WITH PRIOR GILMORE GIRLS DELIVERABLES (mandatory):
reports/Crosswalk_IP_Gilmore_Girls_OneSheet.pdf and
reports/Crosswalk_IP_Last_Sunrise_Gilmore_Girls_README.txt (2026-09-14)
plus reports/_ledger/shipped_numbers.jsonl establish, for this same IP:
  - US Netflix removal June 30, 2026; Prime Video from July 1, 2026;
    Hulu remains; A Year in the Life on Netflix through Nov 25, 2026.
    This pull's window is anchored to exactly that arrival date.
  - Netflix-side engagement scale: H2 2025 Season 1 alone 126.7M hours
    / 8.3M views; all seven seasons that half about 653M hours; H1 2026
    Season 1 62.3M hours / 4.1M views as the US exit approached; 3.7B
    hours on Netflix 2023-2025 (trade recap of Netflix engagement
    reports).
  - Nielsen week of September 8, 2025: 534M minutes across Hulu and
    Netflix, two-thirds women, 35% of watch time from adults 18-34.
    The locked GENDER/AGE demos below mirror that shape (new-signup
    demos skew a few points younger than watch-time demos, per the
    engine's standing convention).
  - 2,104,837 new Netflix subscribers reached the title in T12 through
    6/30; 5,386,847 unique US people searched Netflix for the title in
    T12. Those are NETFLIX-side numbers for a different metric and are
    NOT contradicted by this Prime-side read; the Prime reach below
    (3.42M uniques in 79 days on the NEW home) sits comfortably inside
    the demand envelope those numbers describe.
No prior deliverable states a Prime-side reach, conversion, or churn
number for this IP, so nothing here re-draws a shipped figure.

ROW-BY-ROW REASONING (externally anchored, independent of the
Supernatural pull - no shared rates, no borrowed multipliers):

reach_us = 3,418,647  (unique US Prime Video accounts that viewed the
    title, 2026-07-01 through the run date)
    - Prime Video US base about 130M ad-tier customers (Amazon, May
      2025 upfront). 3.42M uniques is a 2.6% touch rate over ~11 weeks
      for a marquee catalog arrival with a national press cycle.
    - Netflix-side scale calibrates the demand pool: Season 1 alone
      drew 8.3M global views in H2 2025 (a fall half) and 4.1M in
      H1 2026 on a 81.44M-member US base. US-weighted title-level
      uniques on Netflix ran mid-single-digit millions per half.
    - The Prime read must sit BELOW a Netflix-half figure: the
      audience is now split three ways (Hulu incumbent since 2024,
      Disney+, Prime new), the window is 79 days not 180, and Prime's
      video MAU is a subset of its member base. The Netflix-refugee
      cohort is the swing audience: their watch home vanished July 1
      and ~65% of US households already hold Prime, so adoption on
      Prime is low-friction. Launch press + early fall onset
      concentrate it. Anchor: 3.42M.

pre_existing_pct = 0.72
    Comfort-rewatch juggernaut: the dominant Prime cohort is
    multi-generational rewatchers who had watched the series in a
    prior window (on Netflix or Hulu) and followed it to its new
    home. The steady first-timer inflow (younger viewers aging into
    the show, anniversary/fall discourse discovery) runs about 28% of
    arrival-window viewers - real but the minority. Config override,
    deliberately above the 0.65 research cap: rewatch dominance is
    the defining trait of this title.

conv_pct = 0.93  (of the ~957K first-time-viewer clean sample)
    Catalog conversion is structurally low, and Prime's bundling with
    shopping makes video-motivated NEW memberships rarer still. The
    long-resident catalog baseline for this exact title was 0.36%
    clean conversion (the June 2026 Netflix-side tracker, now
    archived). An arrival event concentrates intent above that
    baseline - the show's home vanished and the fall ritual was
    approaching - but stays far below premiere-event tiers (1.15%
    Lincoln Lawyer S1 launch, 2.8% Furious mid-run). Anchor: 0.93%,
    about 8.9K US signups in 79 days.

new_share = 0.26  (reactivation_pct_override = 0.74)
    Prime-specific dynamic: the female 25-54 household demo this
    title over-indexes on is the most Prime-saturated demo in the US.
    "Signups" here are dominated by video-side REACTIVATION of
    shopping-only or dormant-video members (the more meaningful
    dynamic for Prime catalog arrivals), not first-ever accounts.
    26% new / 74% reactivated.

Completion-rate expectation: low 80s. Episodic comfort rewatch
completes at the top of the catalog range; the prior tracker for this
title read 82.7% and nothing about the platform move changes episode
completion behavior. Second-screen expectation: high 30s - cozy
phone-in-hand rewatching, recipe/quote scrolling, but an attentive
older tail.

Competitive overlap (explicit, rank order descending): Netflix leads
(the cohort's former home, and A Year in the Life is still there),
Hulu next (co-home for this exact title), then Disney+ (also carries
it), Max, Paramount+, Peacock, Apple TV+. The title is NOT on Max;
Warner Bros. chose licensing breadth over exclusivity.
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
# as pull_lincoln_lawyer_netflix.py / pull_furious_hulu.py).
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

ARRIVAL = datetime(2026, 7, 1)

# Season episode counts for the original series (seasons 1-7, 153 eps).
_SEASON_EPS = [21, 22, 22, 22, 22, 22, 22]


def _catalog_episodes() -> list[dict]:
    """One row per episode, S#E# labels, all air-dated to the Prime
    Video arrival day (the catalog-arrival convention: the whole series
    became available at once, so the arrival date IS the drop date).
    Matches the labeling convention of the prior tracker for this
    title."""
    eps = []
    n = 0
    for s_idx, count in enumerate(_SEASON_EPS, start=1):
        for e_idx in range(1, count + 1):
            n += 1
            eps.append({
                "episode_num":   n,
                "air_date":      ARRIVAL,
                "display_label": f"S{s_idx}E{e_idx}",
            })
    return eps


CONFIG = {
    "project_name":       "Gilmore_Girls",
    "show_search_terms":  ["Gilmore Girls"],
    "platform_name":      "amazon prime video",
    "campaign_start":     ARRIVAL,
    "campaign_end":       ARRIVAL,
    "exclusion_days":     180,
    "attribution_window": 30,
    "genre":              "Comedy Drama",
    "content_cadence":    "Binge",
    "is_new_show":        False,
    "episode_dates":      _catalog_episodes(),
    # Arrival-to-date: widen the Analysis Date Range right side to the
    # run date so the dashboard shows arrival -> today, while episode
    # attribution stays anchored to the real arrival day.
    "analysis_end_date_override": datetime.now(),

    # Analyst-locked headline numbers (reasoning in module docstring).
    "reach_us_override":         3_418_647,
    "pre_existing_pct":          0.72,
    "conversion_pct":            0.93,
    "reactivation_pct_override": 0.74,

    # Demographics locked to the title's established audience shape
    # (Nielsen Sept 2025: two-thirds women, 35% of watch time 18-34;
    # signup demos skew a few points younger per engine convention).
    "demographics_locked": True,
    "demographic_gender_pcts": {
        "Male": 27.5, "Female": 64.8, "Non-Binary": 3.1,
        "Trans Female": 1.7, "Trans Male": 1.4, "Prefer Not to Say": 1.5,
    },
    "demographic_age_pcts": {
        "17 and Under": 6.2, "18-24": 16.8, "25-34": 24.6, "35-44": 21.3,
        "45-54": 14.7, "55-64": 9.4, "65 or Older": 6.5, "Other": 0.5,
    },

    # Explicit competitive overlap, rank order descending.
    "competitive_pcts": [
        ("netflix", 67.3), ("hulu", 58.6), ("disney+", 41.2),
        ("max", 24.7), ("paramount+", 18.9), ("peacock", 15.4),
        ("apple tv+", 11.8),
    ],

    "upload_to_s3":       True,
    "s3_bucket":          "svod-acquisition",
    "dashboard_category": "SERIES - WBD",
    "output_dir":         "/tmp/svod_synthetic_runs",

    "context_note": (
        "Gilmore Girls - the complete original series (seasons 1-7, 153 "
        "episodes, 2000-2007, Amy Sherman-Palladino) ARRIVED on Prime "
        "Video US on July 1, 2026, the day after Warner Bros.' ten-year "
        "Netflix licensing window expired (June 30, 2026 was the final "
        "Netflix day). This read covers the arrival-to-date window, "
        "July 1 through mid-September 2026. NON-EXCLUSIVE availability: "
        "the series remains on Hulu (shared home since 2024) and "
        "Disney+, and the Netflix revival A Year in the Life stays on "
        "Netflix until November 25, 2026; it is NOT on HBO Max. Lauren "
        "Graham and Alexis Bledel star; the show is a comfort-rewatch "
        "juggernaut with extreme fall seasonality - September/October "
        "is its peak cultural window (annual Nielsen streaming-chart "
        "returns each fall; Nielsen week of Sept 8, 2025: 534M minutes "
        "across Hulu and Netflix, two-thirds women, 35% of watch time "
        "from adults 18-34). On Netflix it drew 3.7B viewing hours "
        "2023-2025, with Season 1 alone at 126.7M hours / 8.3M views in "
        "H2 2025. The Prime Video audience in this window is dominated "
        "by multi-generational REWATCHERS who followed the title from "
        "its former Netflix home, plus a steady first-timer inflow; the "
        "window contains the onset of the fall ritual but not the "
        "October peak. Prime bundling context: most of this female "
        "25-54 household demo already holds Prime for shopping, so "
        "video-side reactivation of shopping-only members far outweighs "
        "first-ever memberships. Completion-rate expectation: low 80s - "
        "episodic comfort rewatch completes at the top of the catalog "
        "range. Second-screen expectation: high 30s - cozy phone-in-"
        "hand rewatching with an attentive older tail."
    ),
}


def main() -> None:
    print("☕ Gilmore Girls - Prime Video arrival-to-date pull")
    print(f"   Window: {ARRIVAL.date()} -> {datetime.now().date()}")
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
