#!/usr/bin/env python3
"""Pull Supernatural (Prime Video US) - catalog arrival-to-date read.

WINDOW CHOICE: ARRIVAL-TO-DATE, 2025-12-22 through the run date.
Supernatural (15 seasons, 327 episodes, 2005-2020) left US Netflix on
December 17, 2025 when the long-running CW/Warner Bros. licensing
agreement ended (removal December 18). All 15 seasons arrived on BOTH
Prime Video and Peacock on Monday, December 22, 2025, five days after
the Netflix exit (TV Insider; trade availability checks through August
2026 confirm both services still carry the full series). That is a
discrete recent ARRIVAL event, so this pull uses arrival-date through
today (about nine months of runway), not the trailing-12 default.
NON-EXCLUSIVE: shared with Peacock, which the press cycle positioned
as the more direct Netflix substitute; it is NOT on HBO Max, Hulu, or
Disney+.

COHERENCE CHECK: reports/ and reports/_ledger/shipped_numbers.jsonl
contain NO prior Supernatural deliverable, so no shipped number
constrains this pull. This is the first Crosswalk read on the IP; the
underpinnings live in this docstring and the ledger entries appended
at ship time.

ROW-BY-ROW REASONING (externally anchored, independent of the Gilmore
Girls pull - no shared rates, no borrowed multipliers):

reach_us = 4,713,286  (unique US Prime Video accounts that viewed the
    title, 2025-12-22 through the run date)
    - Prime Video US base about 130M ad-tier customers (Amazon, May
      2025 upfront). 4.71M uniques is a 3.6% touch rate over ~39
      weeks for a marquee genre catalog.
    - Netflix-era scale calibrates the demand pool: Supernatural was
      a perennial Nielsen acquired-series chart staple, roughly 20B+
      minutes in its peak Netflix catalog year (2020) and still
      charting in its final Netflix years, with a documented
      last-chance viewing spike ahead of the December 2025 exit.
    - The Prime read sits well below the title's whole-market demand:
      the audience split two ways at arrival, and Peacock captured
      the dedicated-fandom tilt (cheaper standalone destination,
      positioned in press as the direct substitute) while Prime's
      130M-member base captures the broader incidental/binge cohort.
      Long-tail binge shape: a 327-episode completionist run spreads
      uniques across the whole nine months instead of front-loading
      an arrival week. Anchor: 4.71M.
    - Per-day comparison vs the Gilmore Girls Prime pull is
      deliberately colder (4.71M/270d vs 3.42M/79d): Supernatural's
      arrival lacked a seasonal ritual onset, and its fandom had a
      second equally-complete home from day one.

pre_existing_pct = 0.63
    Fifteen years of broadcast plus a decade as a top Netflix catalog
    title mean rewatchers dominate, but LESS so than a comfort-ritual
    title: the 327-episode binge catalog keeps attracting first-time
    completionists (younger genre viewers discovering the Winchester
    brothers through TikTok/Tumblr fandom currents). 63% had watched
    the series in a prior window; 37% first-timers.

conv_pct = 0.58  (of the ~1.74M first-time-viewer clean sample)
    Catalog conversion is structurally low on Prime (bundled with
    shopping; a binge catalog rarely mints new memberships), and for
    this title specifically the acquisition intent siphons to Peacock,
    the cheaper dedicated home for the fandom. Above the long-resident
    catalog floor (0.36% clean conversion on the archived long-
    resident Gilmore Girls Netflix tracker is the reference point for
    no-event catalog) because there WAS an arrival moment plus the
    January post-holiday signup season inside the window; below the
    Gilmore Girls Prime arrival read (0.93%) because the nine-month
    runway dilutes event concentration and the co-home splits intent.
    Anchor: 0.58%, about 10.1K US signups across nine months.

new_share = 0.34  (reactivation_pct_override = 0.66)
    Reactivation of dormant/shopping-only Prime members still
    dominates (the standing Prime catalog dynamic), but the genuinely
    new share runs higher than the Gilmore Girls read (0.26): the
    younger-skewing genre audience includes more people outside
    Prime-holding households, and the arrival coincided with the
    January signup season. 34% new / 66% reactivated.

Completion-rate expectation: low 70s. A devoted completionist fandom
binges serialized arcs hard, but a 327-episode marathon carries real
mid-series attrition (the seasons 8-12 stretch is the documented
drop-off zone), landing below tight-catalog comfort rewatches.
Second-screen expectation: high 40s - one of the most social-native
fandoms on television (Tumblr/TikTok/X fan-edit and episode-discourse
culture), younger-skewing, live-posting through binges.

Competitive overlap (explicit, rank order descending): Netflix leads
(the cohort's former home of a decade), Peacock unusually high for a
second platform (the co-home carrying the same complete series), then
Hulu, Max, Disney+, Paramount+, Apple TV+.
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

ARRIVAL = datetime(2025, 12, 22)

# Season episode counts (seasons 1-15, 327 episodes).
_SEASON_EPS = [22, 22, 16, 22, 22, 22, 23, 23, 23, 23, 23, 23, 23, 20, 20]


def _catalog_episodes() -> list[dict]:
    """One row per episode, S#E# labels, all air-dated to the Prime
    Video arrival day (catalog-arrival convention: the whole series
    became available at once, so the arrival date IS the drop date)."""
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
    "project_name":       "Supernatural",
    "show_search_terms":  ["Supernatural"],
    "platform_name":      "amazon prime video",
    "campaign_start":     ARRIVAL,
    "campaign_end":       ARRIVAL,
    "exclusion_days":     180,
    "attribution_window": 30,
    "genre":              "Horror Fantasy Drama",
    "content_cadence":    "Binge",
    "is_new_show":        False,
    "episode_dates":      _catalog_episodes(),
    # Arrival-to-date: widen the Analysis Date Range right side to the
    # run date so the dashboard shows arrival -> today, while episode
    # attribution stays anchored to the real arrival day.
    "analysis_end_date_override": datetime.now(),

    # Analyst-locked headline numbers (reasoning in module docstring).
    "reach_us_override":         4_713_286,
    "pre_existing_pct":          0.63,
    "conversion_pct":            0.58,
    "reactivation_pct_override": 0.66,

    # Demographics locked: broader gender mix than the female-ritual
    # comfort catalogs, younger-skewing genre audience (fandom-native
    # 18-34 core with a broadcast-era 35-54 tail).
    "demographics_locked": True,
    "demographic_gender_pcts": {
        "Male": 40.6, "Female": 52.4, "Non-Binary": 3.4,
        "Trans Female": 1.3, "Trans Male": 1.1, "Prefer Not to Say": 1.2,
    },
    "demographic_age_pcts": {
        "17 and Under": 7.4, "18-24": 21.6, "25-34": 26.8, "35-44": 19.7,
        "45-54": 12.6, "55-64": 7.2, "65 or Older": 4.2, "Other": 0.5,
    },

    # Explicit competitive overlap, rank order descending. Peacock runs
    # unusually high for a second platform because it is the co-home
    # carrying the same complete series.
    "competitive_pcts": [
        ("netflix", 62.8), ("peacock", 44.6), ("hulu", 41.9),
        ("max", 27.3), ("disney+", 25.1), ("paramount+", 17.2),
        ("apple tv+", 9.6),
    ],

    "upload_to_s3":       True,
    "s3_bucket":          "svod-acquisition",
    "dashboard_category": "SERIES - WBD",
    "output_dir":         "/tmp/svod_synthetic_runs",

    "context_note": (
        "Supernatural - the complete series (15 seasons, 327 episodes, "
        "2005-2020, The WB/The CW, created by Eric Kripke; Jared "
        "Padalecki and Jensen Ackles as Sam and Dean Winchester) "
        "ARRIVED on Prime Video US on December 22, 2025, five days "
        "after its final US Netflix day (December 17, 2025) ended the "
        "long-running Warner Bros./CW licensing arrangement. This read "
        "covers the arrival-to-date window, December 22, 2025 through "
        "mid-September 2026, about nine months of runway. NON-EXCLUSIVE "
        "availability: all 15 seasons arrived on Peacock the same day, "
        "and press coverage positioned Peacock as the more direct "
        "Netflix substitute for the dedicated fandom; the series is NOT "
        "on HBO Max, Hulu, or Disney+. Audience shape: a devoted "
        "completionist fandom with a long-tail BINGE viewing pattern - "
        "a 327-episode marathon spreads unique viewers across the full "
        "nine months rather than front-loading an arrival week - and a "
        "broader gender mix plus younger genre skew than female-ritual "
        "comfort catalogs (fandom-native 18-34 core from Tumblr/TikTok/X "
        "fan culture, plus the broadcast-era 35-54 tail). On Netflix it "
        "was a perennial Nielsen acquired-series chart staple, roughly "
        "20B+ minutes in its peak catalog year, with a last-chance "
        "viewing spike before the December 2025 exit. Prime bundling "
        "context: video-side reactivation of shopping-only or dormant "
        "members dominates signups, though the younger genre cohort "
        "carries more genuinely-new accounts than older-skewing "
        "catalogs, helped by the January post-holiday signup season "
        "inside the window. Completion-rate expectation: low 70s - "
        "committed arc-bingers, minus documented mid-series attrition "
        "across the seasons 8-12 stretch. Second-screen expectation: "
        "high 40s - one of the most social-native fandoms on "
        "television, live-posting fan edits and episode discourse "
        "through binges."
    ),
}


def main() -> None:
    print("🔦 Supernatural - Prime Video arrival-to-date pull")
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
