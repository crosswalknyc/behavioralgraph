#!/usr/bin/env python3
"""Batch-pull missing Taylor Sheridan-verse TV trackers.

Already in s3://svod-acquisition/ as of 2026-06-22:
    Tulsa King S1 + S2, Landman S1 + S2, Special Ops: Lioness S1

This script pulls the gaps — every additional Sheridan-created TV series
through mid-2026, season by season — matching the analyst's per-season
tracker convention.

Yellowstone goes on Peacock (the longstanding NBCU-deal US SVOD home for
all five seasons), every other Sheridan-verse series goes on Paramount+
(produced directly for Paramount Global's streamer). Category is set to
"SERIES - PARAMOUNT TV STUDIOS" everywhere to match the existing Tulsa
King / Landman / Lioness convention.

Seasons that aired entirely before 2021-01-01 (Yellowstone S1-S3) will
have the pre-2021 panel-cutoff disclaimer auto-applied by the pipeline:
Analysis Date Range gets pinned to 2021-01-01 → 2025-12-31 and the
dashboard surfaces the "Episodes tracked were watched after the original
air date due to availability of data" note.
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


def _eps(start: str, count: int, *, step_days: int = 7,
         label_prefix: str = "Episode") -> list[dict]:
    """Build an episode-dates list with weekly cadence by default."""
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    return [
        {
            "episode_num":   i + 1,
            "air_date":      start_dt + timedelta(days=i * step_days),
            "display_label": f"{label_prefix} {i + 1}",
        }
        for i in range(count)
    ]


def _eps_explicit(dates: list[str], *, label_prefix: str = "Episode") -> list[dict]:
    """Build an episode list from explicit dates (e.g. for split-season runs
    where back-half premieres aren't on the weekly cadence)."""
    return [
        {
            "episode_num":   i + 1,
            "air_date":      datetime.strptime(d, "%Y-%m-%d"),
            "display_label": f"{label_prefix} {i + 1}",
        }
        for i, d in enumerate(dates)
    ]


# ──────────────────────────────────────────────────────────────────────
# Configs — one per tracker. Each entry produces one CSV in
# s3://svod-acquisition/. Title format matches existing per-season
# convention so dropdown grouping works on the dashboard.
# ──────────────────────────────────────────────────────────────────────

YELLOWSTONE_GENRE = "Modern Western Drama"
HISTORIC_WESTERN  = "Historical Western Drama"
CRIME_DRAMA       = "Crime Drama"
ACTION_THRILLER   = "Action Thriller"

CONFIGS: list[dict] = [
    # ─── Yellowstone S1-S5 (Peacock primary streaming, Paramount Network linear) ───
    {
        "project_name":   "Yellowstone_-_Season_1",
        "title":          "Yellowstone Season 1",
        "platform":       "peacock",
        "start":          "2018-06-20",
        "end":            "2018-08-22",
        "genre":          YELLOWSTONE_GENRE,
        "cadence":        "Weekly",
        "is_new":         True,
        "episode_dates":  _eps_explicit([
            "2018-06-20", "2018-06-27", "2018-07-04", "2018-07-11",
            "2018-07-18", "2018-07-25", "2018-08-01", "2018-08-15",
            "2018-08-22",
        ]),
        "context_note": (
            "Yellowstone Season 1 (9 episodes, June 20 - August 22, 2018) "
            "premiered on Paramount Network as a linear cable broadcast; "
            "for SVOD purposes the longstanding US streaming home for the "
            "entire Yellowstone series has been Peacock (NBCUniversal / "
            "Comcast deal). All season-1 episodes aired pre-2021-01-01 so "
            "the pre-2021 panel-cutoff disclaimer will fire automatically. "
            "Yellowstone is the flagship of the modern Taylor Sheridan "
            "universe, anchoring the 1883/1923/Mayor of Kingstown/etc. "
            "franchise expansion. Cast: Kevin Costner, Kelly Reilly, Cole "
            "Hauser, Wes Bentley, Luke Grimes, Kelsey Asbille."
        ),
    },
    {
        "project_name":   "Yellowstone_-_Season_2",
        "title":          "Yellowstone Season 2",
        "platform":       "peacock",
        "start":          "2019-06-19",
        "end":            "2019-08-28",
        "genre":          YELLOWSTONE_GENRE,
        "cadence":        "Weekly",
        "is_new":         False,
        "episode_dates":  _eps_explicit([
            "2019-06-19", "2019-06-26", "2019-07-03", "2019-07-10",
            "2019-07-17", "2019-07-24", "2019-07-31", "2019-08-07",
            "2019-08-21", "2019-08-28",
        ]),
        "context_note": (
            "Yellowstone S2 (10 episodes, June-August 2019). Returning fan "
            "audience from S1; Paramount Network linear, Peacock SVOD. "
            "Pre-2021 disclaimer applies (all eps aired before 2021-01-01)."
        ),
    },
    {
        "project_name":   "Yellowstone_-_Season_3",
        "title":          "Yellowstone Season 3",
        "platform":       "peacock",
        "start":          "2020-06-21",
        "end":            "2020-08-23",
        "genre":          YELLOWSTONE_GENRE,
        "cadence":        "Weekly",
        "is_new":         False,
        "episode_dates":  _eps("2020-06-21", 10),
        "context_note": (
            "Yellowstone S3 (10 episodes, June-August 2020) — S3 was the "
            "season where the franchise broke through to mainstream cultural "
            "awareness; viewership grew week-over-week. Peacock SVOD. "
            "Pre-2021 disclaimer applies."
        ),
    },
    {
        "project_name":   "Yellowstone_-_Season_4",
        "title":          "Yellowstone Season 4",
        "platform":       "peacock",
        "start":          "2021-11-07",
        "end":            "2022-01-02",
        "genre":          YELLOWSTONE_GENRE,
        "cadence":        "Weekly",
        "is_new":         False,
        "episode_dates":  _eps_explicit([
            "2021-11-07", "2021-11-14", "2021-11-21", "2021-11-28",
            "2021-12-05", "2021-12-12", "2021-12-19", "2021-12-26",
            "2022-01-02", "2022-01-02",  # finale was a 2-hour block on 1/2
        ]),
        "context_note": (
            "Yellowstone S4 (10 episodes, Nov 7, 2021 - Jan 2, 2022) — "
            "the breakout season that made it the highest-rated cable show "
            "on US TV. 14M+ live linear viewers on premiere night. Peacock "
            "SVOD. Some panel-window overlap on the front-half but the "
            "back-half aired in 2022, fully within the panel coverage."
        ),
    },
    {
        "project_name":   "Yellowstone_-_Season_5",
        "title":          "Yellowstone Season 5",
        "platform":       "peacock",
        "start":          "2022-11-13",
        "end":            "2024-12-15",
        "genre":          YELLOWSTONE_GENRE,
        "cadence":        "Weekly",
        "is_new":         False,
        "episode_dates":  _eps_explicit([
            # 5A — first 8 eps, Nov 2022 - Jan 2023
            "2022-11-13", "2022-11-20", "2022-11-27", "2022-12-04",
            "2022-12-11", "2023-01-01", "2023-01-08", "2023-01-15",
            # 5B — final 6 eps, Nov-Dec 2024 (final season for the original
            # Dutton storyline; Costner's departure split the season)
            "2024-11-10", "2024-11-17", "2024-11-24", "2024-12-01",
            "2024-12-08", "2024-12-15",
        ]),
        "context_note": (
            "Yellowstone S5 (14 episodes total, split-season release: "
            "Part 1 = 8 eps Nov 2022 - Jan 2023, Part 2 = 6 eps "
            "Nov-Dec 2024). Final season of the original Dutton-family "
            "Yellowstone storyline; Kevin Costner's departure prompted "
            "the unusual ~2-year gap between halves. Peacock SVOD home."
        ),
    },

    # ─── 1883 (limited prequel, Paramount+) ───
    {
        "project_name":   "1883_-_Limited_Series",
        "title":          "1883 Limited Series",
        "platform":       "paramount+",
        "start":          "2021-12-19",
        "end":            "2022-02-27",
        "genre":          HISTORIC_WESTERN,
        "cadence":        "Weekly",
        "is_new":         True,
        "episode_dates":  _eps("2021-12-19", 10),
        "context_note": (
            "1883 — limited series prequel to Yellowstone, 10 episodes "
            "weekly Dec 19, 2021 - Feb 27, 2022 on Paramount+. Tim "
            "McGraw, Faith Hill, Sam Elliott. Massive Paramount+ "
            "subscriber-acquisition driver at launch — credited as "
            "the title that established Paramount+ as a serious "
            "competitor in the prestige-drama tier."
        ),
    },

    # ─── 1923 — S1 + S2 (Paramount+) ───
    {
        "project_name":   "1923_-_Season_1",
        "title":          "1923 Season 1",
        "platform":       "paramount+",
        "start":          "2022-12-18",
        "end":            "2023-02-26",
        "genre":          HISTORIC_WESTERN,
        "cadence":        "Weekly",
        "is_new":         True,
        "episode_dates":  _eps("2022-12-18", 8),
        "context_note": (
            "1923 Season 1 — Yellowstone prequel set during Prohibition "
            "and the Great Depression. 8 episodes, Dec 18, 2022 - Feb 26, "
            "2023, on Paramount+. Harrison Ford, Helen Mirren. Most-"
            "watched series premiere in Paramount+ history at launch."
        ),
    },
    {
        "project_name":   "1923_-_Season_2",
        "title":          "1923 Season 2",
        "platform":       "paramount+",
        "start":          "2025-02-23",
        "end":            "2025-04-06",
        "genre":          HISTORIC_WESTERN,
        "cadence":        "Weekly",
        "is_new":         False,
        "episode_dates":  _eps("2025-02-23", 7),
        "context_note": (
            "1923 Season 2 — 7 episodes, Feb 23 - Apr 6, 2025 on "
            "Paramount+. Final season; concludes the Spencer/Alex and "
            "Jacob/Cara Dutton arcs. Returning premium audience."
        ),
    },

    # ─── Mayor of Kingstown S1-S4 (Paramount+) ───
    {
        "project_name":   "Mayor_of_Kingstown_-_Season_1",
        "title":          "Mayor of Kingstown Season 1",
        "platform":       "paramount+",
        "start":          "2021-11-14",
        "end":            "2022-01-09",
        "genre":          CRIME_DRAMA,
        "cadence":        "Weekly",
        "is_new":         True,
        "episode_dates":  _eps("2021-11-14", 10),
        "context_note": (
            "Mayor of Kingstown Season 1 — Sheridan's prison-town crime "
            "drama starring Jeremy Renner. 10 episodes weekly Nov 14, "
            "2021 - Jan 9, 2022 on Paramount+. Set in the fictional "
            "Kingstown, Michigan, where the local economy revolves "
            "around the prison-industrial complex."
        ),
    },
    {
        "project_name":   "Mayor_of_Kingstown_-_Season_2",
        "title":          "Mayor of Kingstown Season 2",
        "platform":       "paramount+",
        "start":          "2023-01-15",
        "end":            "2023-03-19",
        "genre":          CRIME_DRAMA,
        "cadence":        "Weekly",
        "is_new":         False,
        "episode_dates":  _eps("2023-01-15", 10),
        "context_note": (
            "Mayor of Kingstown S2 — 10 eps weekly Jan-Mar 2023 on "
            "Paramount+. Returning audience; Renner returned to the "
            "show after his Jan 2023 snowplow accident recovery period."
        ),
    },
    {
        "project_name":   "Mayor_of_Kingstown_-_Season_3",
        "title":          "Mayor of Kingstown Season 3",
        "platform":       "paramount+",
        "start":          "2024-06-02",
        "end":            "2024-08-04",
        "genre":          CRIME_DRAMA,
        "cadence":        "Weekly",
        "is_new":         False,
        "episode_dates":  _eps("2024-06-02", 10),
        "context_note": (
            "Mayor of Kingstown S3 — 10 eps, Jun 2 - Aug 4, 2024 on "
            "Paramount+. Returning crime-drama audience."
        ),
    },
    {
        "project_name":   "Mayor_of_Kingstown_-_Season_4",
        "title":          "Mayor of Kingstown Season 4",
        "platform":       "paramount+",
        "start":          "2025-10-26",
        "end":            "2025-12-28",
        "genre":          CRIME_DRAMA,
        "cadence":        "Weekly",
        "is_new":         False,
        "episode_dates":  _eps("2025-10-26", 10),
        "context_note": (
            "Mayor of Kingstown S4 — 10 eps, Oct-Dec 2025 on "
            "Paramount+. Returning audience."
        ),
    },

    # ─── Lawmen: Bass Reeves (limited series, Paramount+) ───
    {
        "project_name":   "Lawmen_-_Bass_Reeves",
        "title":          "Lawmen: Bass Reeves",
        "platform":       "paramount+",
        "start":          "2023-11-05",
        "end":            "2023-12-17",
        "genre":          HISTORIC_WESTERN,
        "cadence":        "Weekly",
        "is_new":         True,
        "episode_dates":  _eps("2023-11-05", 8),
        "context_note": (
            "Lawmen: Bass Reeves — 8-episode limited series, Nov 5 - "
            "Dec 17, 2023 on Paramount+. David Oyelowo as the historical "
            "first Black U.S. Deputy Marshal west of the Mississippi. "
            "Part of Sheridan's expanded Lawmen anthology franchise."
        ),
    },

    # ─── The Madison (Beth/Rip spinoff, Paramount+) ───
    {
        "project_name":   "The_Madison_-_Season_1",
        "title":          "The Madison Season 1",
        "platform":       "paramount+",
        "start":          "2026-03-15",
        "end":            "2026-05-17",
        "genre":          YELLOWSTONE_GENRE,
        "cadence":        "Weekly",
        "is_new":         True,
        "episode_dates":  _eps("2026-03-15", 10),
        "context_note": (
            "The Madison Season 1 — direct Yellowstone spinoff, "
            "Sheridan-created, Paramount+. New cast led by Michelle "
            "Pfeiffer set in the Madison River Valley of Montana, "
            "following a New York family rebuilding their lives "
            "after tragedy. Releasing 2026. If date specifics need "
            "adjustment based on actual release calendar, Claude's "
            "external research should surface that."
        ),
    },

    # ─── Special Ops: Lioness S2 (Paramount+) ───
    {
        "project_name":   "Special_Ops__Lioness_-_Season_2",
        "title":          "Special Ops: Lioness Season 2",
        "platform":       "paramount+",
        "start":          "2025-10-19",
        "end":            "2025-12-14",
        "genre":          ACTION_THRILLER,
        "cadence":        "Weekly",
        "is_new":         False,
        "episode_dates":  _eps("2025-10-19", 8),
        "context_note": (
            "Special Ops: Lioness Season 2 — 8 eps, Oct-Dec 2025 on "
            "Paramount+. Returning audience from S1 (already in S3). "
            "Zoe Saldaña, Nicole Kidman, Morgan Freeman. Action-thriller "
            "centered on CIA Lioness covert ops program."
        ),
    },
]


def build_config(spec: dict) -> dict:
    start = datetime.strptime(spec["start"], "%Y-%m-%d")
    end   = datetime.strptime(spec["end"],   "%Y-%m-%d")
    return {
        "project_name":        spec["project_name"],
        "show_search_terms":   [spec["title"]],
        "platform_name":       spec["platform"],
        "campaign_start":      start,
        "campaign_end":        end,
        "exclusion_days":      180,
        "attribution_window":  30,
        "genre":               spec["genre"],
        "content_cadence":     spec["cadence"],
        "is_new_show":         spec["is_new"],
        "episode_dates":       spec["episode_dates"],
        "upload_to_s3":        True,
        "s3_bucket":           "svod-acquisition",
        "dashboard_category":  "SERIES - PARAMOUNT TV STUDIOS",
        "output_dir":          "/tmp/svod_synthetic_runs",
        "context_note":        spec["context_note"],
    }


def main():
    print(f"📺 Sheridan-verse batch: {len(CONFIGS)} trackers to pull")
    print(f"   Yellowstone S1-S5 → Peacock; rest → Paramount+")
    print()
    results: list[tuple[str, str, str]] = []
    for idx, spec in enumerate(CONFIGS, 1):
        print(f"\n{'='*70}\n  [{idx}/{len(CONFIGS)}] {spec['title']} ({spec['platform']})\n{'='*70}")
        try:
            cfg = build_config(spec)
            r = run_synthetic_attribution(cfg)
            key = r.get("s3_key") if isinstance(r, dict) else None
            reach = r.get("reach_us") if isinstance(r, dict) else None
            sign = r.get("new_signups_us") if isinstance(r, dict) else None
            print(f"  ✅ uploaded {key}  reach={reach:,} signups={sign:,}"
                  if (key and reach is not None and sign is not None) else
                  f"  ⚠️ unexpected result: {r}")
            results.append(("ok", spec["title"], str(key)))
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  ❌ {spec['title']}: {e}")
            results.append(("fail", spec["title"], str(e)))

    print()
    print("="*70)
    print(f"Done. {sum(1 for s,_,_ in results if s=='ok')}/{len(results)} succeeded.")
    for s, t, msg in results:
        tag = "✅" if s == "ok" else "❌"
        print(f"  {tag} {t}: {msg}")


if __name__ == "__main__":
    main()
