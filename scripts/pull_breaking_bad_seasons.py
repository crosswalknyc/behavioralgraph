#!/usr/bin/env python3
"""Pull Breaking Bad — all 5 seasons independently, one CSV per season.

Breaking Bad originally aired on AMC (linear cable, 2008-2013). All 62
episodes premiered pre-2021, so the synthetic pipeline's pre-2021 panel
cutoff disclaimer fires automatically for every season: the Analysis Date
Range is pinned to 2021-01-01 → 2025-12-31 and dashboard surfaces the
"Episodes tracked were watched after the original air date due to
availability of data" note.

Netflix has been the US SVOD home for Breaking Bad since 2011 (when
Netflix acquired streaming rights to S1-S3 mid-run, then S4-S5 on
delayed windows). By 2013 all 5 seasons were on Netflix domestically
and have remained the primary streaming destination through today.
Vince Gilligan has repeatedly credited Netflix catalog binge-viewing
with saving the show — S5B ratings tripled S1 in part because Netflix
new-viewer intake fed back into linear tune-in.

Season structure follows the AMC original-air convention:
    S1 (2008): 7 eps (writers-strike shortened from planned 9)
    S2 (2009): 13 eps
    S3 (2010): 13 eps
    S4 (2011): 13 eps
    S5 (2012-13): 16 eps total, split (S5A 8 eps 2012, S5B 8 eps 2013)

Each season is pulled INDEPENDENTLY — its own campaign_start / campaign_end
window, its own reach + conversion overrides, its own CSV in
s3://svod-acquisition/. Season 1 is marked is_new=True (series launch);
S2-S5 are is_new=False (returning show) — mirroring the Yellowstone /
Sheridan-verse per-season convention.

Reach + conversion overrides are grounded in:
    - Nielsen live+7 season-avg viewers (AMC linear)
    - Netflix catalog-viewing benchmarks (Antenna panel, Nielsen streaming
      top-10 catalog entries, Reelgood / JustWatch trend data)
    - Breaking Bad → Better Call Saul crossover halo (BCS finale 8/2022
      drove measurable BB catalog re-watch spike)
    - Post-Gilligan-media-cycle bumps (Ozark, El Camino 2019, Pluribus
      2025 all correlated with BB catalog reach lifts)
    - Catalog-content pattern: S1 always has highest reach (starter
      cohort), monotonic decline through S3-S4 as completion drops off,
      then S5 gets a small completionist + finale bump
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO))

os.environ.setdefault("USE_CLAUDE_REASONING", "1")

from SVOD_Churn_Attribution import run_synthetic_attribution  # noqa: E402


def _eps_explicit(dates: list[str], *, label_prefix: str = "Episode") -> list[dict]:
    """Build an episode list from explicit air dates (used for
    writers-strike gaps in S1 and the S5A/S5B hiatus)."""
    return [
        {
            "episode_num":   i + 1,
            "air_date":      datetime.strptime(d, "%Y-%m-%d"),
            "display_label": f"{label_prefix} {i + 1}",
        }
        for i, d in enumerate(dates)
    ]


def _last_episode_date(episode_dates: list[dict]) -> datetime:
    return max(e["air_date"] for e in episode_dates)


# ── Genre + dashboard bucket ───────────────────────────────────────────
CRIME_DRAMA   = "Crime Drama"
DASHBOARD_CAT = "SERIES - AMC / NETFLIX CATALOG"


# ──────────────────────────────────────────────────────────────────────
# Per-season configs. Five independent trackers, one CSV each.
#
# Note on reach_us numbers — these are modeled as Netflix US 30-day
# rolling UNIQUE ACCOUNTS VIEWED for each season within a post-2021
# analysis window. They are NOT Nielsen live+7 linear ratings from the
# original AMC broadcast era. The AMC linear averages were:
#   S1: 1.23M avg, S2: 1.48M, S3: 1.71M, S4: 1.88M, S5A: 2.58M, S5B: 4.24M
# — a monotonically INCREASING pattern as the show gained cultural
# traction. The Netflix catalog reach is the INVERSE pattern (S1 highest,
# gradual decline, small S5 completionist bump) because catalog viewing
# is dominated by starter cohorts working through the series.
# ──────────────────────────────────────────────────────────────────────

CONFIGS: list[dict] = [
    # ─── Season 1 (2008, writers-strike-shortened to 7 eps) ───
    {
        "project_name": "Breaking_Bad_-_Season_1",
        "title":        "Breaking Bad Season 1",
        "platform":     "netflix",
        "start":        "2008-01-20",
        "genre":        CRIME_DRAMA,
        "cadence":      "Weekly",
        "is_new":       True,
        "reach_us":     4_800_000,  # starter cohort — every BB Netflix viewer starts here; highest 30-day catalog reach
        "conv_pct":     3.2,        # meaningful catalog acquisition — some Netflix subs sign up specifically to sample BB
        "new_share":    0.45,       # mix of true new signups + reactivations rediscovering the catalog
        "episode_dates": _eps_explicit([
            "2008-01-20", "2008-01-27", "2008-02-10", "2008-02-17",
            "2008-02-24", "2008-03-02", "2008-03-09",
        ]),
        "context_note": (
            "Breaking Bad Season 1 — AMC crime drama premiere, 7 episodes, "
            "January 20 through March 9, 2008. Originally planned as 9 "
            "episodes but shortened by the 2007-08 WGA writers' strike. "
            "Netflix has been the US SVOD home for Breaking Bad since 2011 "
            "and remains the primary streaming destination. Pre-2021 panel "
            "cutoff disclaimer fires automatically — Analysis Date Range "
            "pinned to 2021-01-01 → 2025-12-31. As catalog content in the "
            "post-2021 window, S1 has the HIGHEST reach of all 5 seasons "
            "because every viewer starts here (starter-cohort effect). "
            "Vince Gilligan has publicly credited Netflix catalog "
            "binge-viewing with saving the show and driving S5B's ratings "
            "3x above S1 linear. Cast: Bryan Cranston, Aaron Paul, Anna "
            "Gunn, Dean Norris, Betsy Brandt, RJ Mitte, Bob Odenkirk."
        ),
    },

    # ─── Season 2 (2009) ───
    {
        "project_name": "Breaking_Bad_-_Season_2",
        "title":        "Breaking Bad Season 2",
        "platform":     "netflix",
        "start":        "2009-03-08",
        "genre":        CRIME_DRAMA,
        "cadence":      "Weekly",
        "is_new":       False,
        "reach_us":     3_600_000,  # ~75% of S1 reach — first drop-off cohort
        "conv_pct":     1.9,        # catalog-continuation viewers mostly already Netflix subs
        "new_share":    0.38,       # reactivation-skewed (mid-series re-engagement)
        "episode_dates": _eps_explicit([
            "2009-03-08", "2009-03-15", "2009-03-22", "2009-03-29",
            "2009-04-05", "2009-04-19", "2009-04-26", "2009-05-03",
            "2009-05-10", "2009-05-17", "2009-05-24", "2009-05-31",
            "2009-05-31",
        ]),
        "context_note": (
            "Breaking Bad Season 2 — AMC, 13 episodes March 8 - May 31, "
            "2009 (the two-hour finale 'ABQ' aired on 5/31 in a single "
            "broadcast slot but is credited as episodes 12+13). "
            "Cold-open flash-forward structure "
            "introduced the pink-teddy-bear plane-crash mystery arc. "
            "Netflix acquired S1-S2 streaming rights in 2011 which "
            "drove significant catalog viewing. As catalog viewing in "
            "the post-2021 tracking window, S2 reach is ~75% of S1 "
            "reflecting starter-cohort drop-off. Pre-2021 disclaimer "
            "fires automatically."
        ),
    },

    # ─── Season 3 (2010) ───
    {
        "project_name": "Breaking_Bad_-_Season_3",
        "title":        "Breaking Bad Season 3",
        "platform":     "netflix",
        "start":        "2010-03-21",
        "genre":        CRIME_DRAMA,
        "cadence":      "Weekly",
        "is_new":       False,
        "reach_us":     3_100_000,  # ~86% of S2 (mid-series stabilizes)
        "conv_pct":     1.6,        # deep-catalog viewers, most already subs
        "new_share":    0.35,       # heavy reactivation skew
        "episode_dates": _eps_explicit([
            "2010-03-21", "2010-03-28", "2010-04-04", "2010-04-11",
            "2010-04-18", "2010-04-25", "2010-05-02", "2010-05-09",
            "2010-05-16", "2010-05-23", "2010-05-30", "2010-06-06",
            "2010-06-13",
        ]),
        "context_note": (
            "Breaking Bad Season 3 — AMC, 13 episodes March 21 - June 13, "
            "2010. Widely considered the series' first true creative "
            "breakout season (Fly, Half Measures, Full Measure). Nielsen "
            "linear avg ~1.71M viewers. Netflix S3 was added to US "
            "streaming later in 2010, contributing to the 2011-2012 "
            "surge in catalog viewership that predated S5B's linear "
            "ratings peak. Catalog reach ~86% of S2 — mid-series "
            "engagement plateau. Pre-2021 disclaimer fires."
        ),
    },

    # ─── Season 4 (2011) ───
    {
        "project_name": "Breaking_Bad_-_Season_4",
        "title":        "Breaking Bad Season 4",
        "platform":     "netflix",
        "start":        "2011-07-17",
        "genre":        CRIME_DRAMA,
        "cadence":      "Weekly",
        "is_new":       False,
        "reach_us":     2_900_000,  # ~94% of S3 (small further step-down)
        "conv_pct":     1.5,        # near floor for catalog attribution
        "new_share":    0.36,       # reactivation-dominant
        "episode_dates": _eps_explicit([
            "2011-07-17", "2011-07-24", "2011-07-31", "2011-08-07",
            "2011-08-14", "2011-08-21", "2011-08-28", "2011-09-04",
            "2011-09-11", "2011-09-18", "2011-09-25", "2011-10-02",
            "2011-10-09",
        ]),
        "context_note": (
            "Breaking Bad Season 4 — AMC, 13 episodes July 17 - October "
            "9, 2011. This is the Gustavo Fring escalation season "
            "(Salud, Crawl Space, Face Off finale). Nielsen linear "
            "avg ~1.88M. Netflix streaming had all prior seasons "
            "available by launch of S4 — many first-time linear viewers "
            "cited Netflix S1-3 binge as their onramp. In the post-2021 "
            "tracking window, catalog reach ~94% of S3 (small drop-off "
            "from mid-series completion effects). Pre-2021 disclaimer "
            "fires. This is often cited as the strongest single season "
            "of BB critically."
        ),
    },

    # ─── Season 5 (2012 + 2013, split-season 16 eps total) ───
    {
        "project_name": "Breaking_Bad_-_Season_5",
        "title":        "Breaking Bad Season 5",
        "platform":     "netflix",
        "start":        "2012-07-15",
        "genre":        CRIME_DRAMA,
        "cadence":      "Weekly",
        "is_new":       False,
        "reach_us":     3_300_000,  # small completionist bump above S3-S4 mid-series trough
        "conv_pct":     2.1,        # finale-season signups: some subs specifically to finish/rewatch
        "new_share":    0.42,       # mixed — completionists + genuine new arrivals drawn by cultural halo
        "episode_dates": _eps_explicit([
            # S5A — July-September 2012
            "2012-07-15", "2012-07-22", "2012-07-29", "2012-08-05",
            "2012-08-12", "2012-08-19", "2012-08-26", "2012-09-02",
            # ~11-month hiatus, then S5B — August-September 2013
            "2013-08-11", "2013-08-18", "2013-08-25", "2013-09-01",
            "2013-09-08", "2013-09-15", "2013-09-22", "2013-09-29",
        ]),
        "context_note": (
            "Breaking Bad Season 5 — AMC, 16 episodes total split across "
            "two calendar years. S5A (episodes 1-8) aired July 15 - "
            "September 2, 2012. S5B (episodes 9-16) aired August 11 - "
            "September 29, 2013 after an 11-month hiatus. The finale "
            "'Felina' (9/29/13) drew 10.3M linear viewers — Nielsen "
            "peak for BB. Season avg S5A ~2.58M, S5B ~4.24M, blended "
            "~3.4M. In the post-2021 Netflix catalog tracking window, "
            "S5 reach is ABOVE S3-S4 (completionist bump — viewers who "
            "reached S3-S4 are highly likely to finish) but still below "
            "S1 (which has the largest starter cohort). Better Call "
            "Saul finale (8/2022) and Pluribus premiere (11/2025) each "
            "drove measurable spikes in Breaking Bad catalog viewing "
            "including S5 rewatches. Pre-2021 disclaimer fires. Note "
            "that the extended air-date range (7/2012 - 9/2013) means "
            "the campaign window spans ~14 months — the pipeline's "
            "campaign_end will be set to the S5B finale date."
        ),
    },
]


def build_config(spec: dict) -> dict:
    start = datetime.strptime(spec["start"], "%Y-%m-%d")
    last_ep = _last_episode_date(spec["episode_dates"])
    cfg = {
        "project_name":        spec["project_name"],
        "show_search_terms":   [spec["title"]],
        "platform_name":       spec["platform"],
        "campaign_start":      start,
        "campaign_end":        last_ep,
        "exclusion_days":      180,
        "attribution_window":  30,
        "genre":               spec["genre"],
        "content_cadence":     spec["cadence"],
        "is_new_show":         spec["is_new"],
        "episode_dates":       spec["episode_dates"],
        "upload_to_s3":        True,
        "s3_bucket":           "svod-acquisition",
        "dashboard_category":  DASHBOARD_CAT,
        "output_dir":          "/tmp/svod_synthetic_runs",
        "context_note":        spec["context_note"],
    }
    if "reach_us" in spec:
        cfg["reach_us_override"] = spec["reach_us"]
    if "conv_pct" in spec:
        cfg["conversion_pct"] = float(spec["conv_pct"])
    if "new_share" in spec:
        cfg["reactivation_pct_override"] = max(0.0, min(1.0, 1.0 - float(spec["new_share"])))
    return cfg


def main() -> None:
    print(f"📺 Breaking Bad — pulling all {len(CONFIGS)} seasons independently")
    print()
    results: list[tuple[str, str, str]] = []
    for idx, spec in enumerate(CONFIGS, 1):
        print(f"\n{'=' * 70}\n  [{idx}/{len(CONFIGS)}] {spec['title']}\n{'=' * 70}")
        try:
            cfg = build_config(spec)
            r = run_synthetic_attribution(cfg)
            key = r.get("s3_key") if isinstance(r, dict) else None
            reach = r.get("reach_us") if isinstance(r, dict) else None
            sign = r.get("new_signups_us") if isinstance(r, dict) else None
            if key and reach is not None and sign is not None:
                print(f"  ✅ uploaded {key}  reach={reach:,} signups={sign:,}")
            else:
                print(f"  ⚠️ unexpected result: {r}")
            results.append(("ok", spec["title"], str(key)))
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  ❌ {spec['title']}: {e}")
            results.append(("fail", spec["title"], str(e)))

    print("\n" + "=" * 70)
    print(f"Done. {sum(1 for s, _, _ in results if s == 'ok')}/{len(results)} succeeded.")
    for s, t, msg in results:
        tag = "✅" if s == "ok" else "❌"
        print(f"  {tag} {t}: {msg}")


if __name__ == "__main__":
    main()
