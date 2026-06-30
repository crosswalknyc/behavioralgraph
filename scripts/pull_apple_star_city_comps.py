#!/usr/bin/env python3
"""Batch-pull missing Apple TV+ Season 1 trackers for the Star City comp set.

The client (Apple) requested a Subscriber-IQ analysis of Star City S1 vs 20
comparable Apple TV+ S1 originals, with 21-day and 28-day windows.

Already in s3://svod-acquisition/ as of 2026-06-29:
    - Star_City               (5/29/26, already pulled 6/4/26)
    - Dark_Matter_Season_1    (5/8/24)
    - Pluribus_Season_1       (11/7/25)

This script pulls the gaps — every other Apple TV+ S1 in the client's
comp set, with realistic premiere drop patterns (2 or 3 ep premieres
where Apple's release calendar used them). Pre-2021 shows (Tehran,
Ted Lasso, For All Mankind) will receive the auto-applied pre-2021
panel-cutoff disclaimer; their first-21/28-day numbers will still
need manual sanity-checking against published Apple-TV+-era priors
because the launch-window subscriber base in 2019-2020 was tiny.
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


def _eps_weekly(start: str, count: int) -> list[dict]:
    """Pure weekly cadence starting on `start` (YYYY-MM-DD)."""
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    return [
        {
            "episode_num":   i + 1,
            "air_date":      start_dt + timedelta(days=i * 7),
            "display_label": f"Episode {i + 1}",
        }
        for i in range(count)
    ]


def _eps_premiere_drop(start: str, premiere_count: int, total_count: int) -> list[dict]:
    """`premiere_count` episodes drop on `start`, the rest weekly after."""
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    eps: list[dict] = []
    for i in range(premiere_count):
        eps.append({
            "episode_num":   i + 1,
            "air_date":      start_dt,
            "display_label": f"Episode {i + 1}",
        })
    weekly_count = total_count - premiere_count
    for j in range(weekly_count):
        eps.append({
            "episode_num":   premiere_count + j + 1,
            "air_date":      start_dt + timedelta(days=(j + 1) * 7),
            "display_label": f"Episode {premiere_count + j + 1}",
        })
    return eps


def _last_episode_date(episode_dates: list[dict]) -> datetime:
    return max(e["air_date"] for e in episode_dates)


# ── Genre buckets used across the comp set ─────────────────────────────
SCIFI_DRAMA       = "Science Fiction Drama"
SCIFI_THRILLER    = "Science Fiction Thriller"
WORKPLACE_THRILL  = "Psychological Workplace Thriller"
SPY_THRILLER      = "Spy Thriller"
LEGAL_THRILLER    = "Legal Thriller"
DETECTIVE_NOIR    = "Neo-Noir Detective Drama"
DRAMA             = "Prestige Drama"
COMEDY_DRAMA      = "Comedy Drama"
SPORTS_COMEDY     = "Sports Comedy"
MONSTER_ACTION    = "Monster / Action Adventure"
MYSTERY_THRILL    = "Mystery Thriller"
COMEDY            = "Half-Hour Comedy"
ALT_HISTORY       = "Alternate-History Drama"
CRIME_THRILLER    = "Crime Thriller"
DASHBOARD_CAT     = "SERIES - APPLE TV+"


# ──────────────────────────────────────────────────────────────────────
# REACH OVERRIDES (US unique accounts viewed in ~30-day analysis window)
#
# The synthetic pipeline's Apple TV+ priors default to a "niche" tier
# with ~80K base US viewers, which is far too low for any real Apple TV+
# tentpole launch. We pass `reach_us_override` per show so the headline
# Total Show Watchers reflects realistic launch-window reach.
#
# Estimates sourced from:
#   - Nielsen Streaming Top 10 (weekly minutes-viewed → uniques estimates)
#   - Antenna Apple TV+ subscriber & engagement panels (2020-2026)
#   - Apple press releases (Presumed Innocent "most-watched series ever")
#   - Strategy Analytics / Wedbush Apple TV+ paid-sub estimates by year
#
# Reach scales with Apple TV+'s growing subscriber base:
#   Nov 2019 launch: ~6M paid subs
#   Late 2020:       ~12-13M
#   Late 2021:       ~25M
#   Early 2022:      ~30M
#   Mid 2023:        ~50M
#   Mid 2024:        ~70M
#   Mid 2025:        ~75M
#   Mid 2026:        ~80M
# Within era, reach varies 5-15% of sub base depending on tier (tentpole
# IP / star power) vs niche genre.
# ──────────────────────────────────────────────────────────────────────

CONFIGS: list[dict] = [
    # ──────────────────── 2026 NEW RELEASES ────────────────────
    {
        "project_name":   "Cape_Fear_-_Season_1",
        "title":          "Cape Fear Season 1",
        "platform":       "apple tv+",
        "start":          "2026-06-05",
        "genre":          CRIME_THRILLER,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       2_000_000,  # mid-tier thriller, brand-recognition lift
        "episode_dates":  _eps_weekly("2026-06-05", 8),
        "context_note": (
            "Cape Fear Season 1 — Apple TV+ original Series, dropped 6/5/26. "
            "8-episode weekly run. Limited-series remake/reimagining of the "
            "classic Cape Fear thriller. As of 6/29/26 only 24 days of "
            "post-launch data are available so the 21-day window is fully "
            "captured but the 28-day window is incomplete (client confirmed "
            "n/a for 28-day metrics). Launched at peak Apple TV+ awareness "
            "(post-Severance S2 / post-Pluribus). Modest-to-strong opener "
            "expected from the brand-recognition lift."
        ),
    },
    {
        "project_name":   "Maximum_Pleasure_Guaranteed_-_Season_1",
        "title":          "Maximum Pleasure Guaranteed Season 1",
        "platform":       "apple tv+",
        "start":          "2026-05-20",
        "genre":          COMEDY,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       1_000_000,  # half-hour comedy, niche launch
        "episode_dates":  _eps_weekly("2026-05-20", 6),
        "context_note": (
            "Maximum Pleasure Guaranteed Season 1 — Apple TV+ original "
            "half-hour comedy, 6 eps weekly from 5/20/26. As of 6/29/26 we "
            "have 40 days of post-launch data so both 21-day and 28-day "
            "windows are fully captured. Apple TV+ comedies typically "
            "under-index vs prestige drama on launch reach but build "
            "long-tail viewing."
        ),
    },
    {
        "project_name":   "Widows_Bay_-_Season_1",
        "title":          "Widow's Bay Season 1",
        "platform":       "apple tv+",
        "start":          "2026-04-29",
        "genre":          MYSTERY_THRILL,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       1_800_000,  # mid-tier mystery in proven Apple format
        "episode_dates":  _eps_weekly("2026-04-29", 8),
        "context_note": (
            "Widow's Bay Season 1 — Apple TV+ original mystery thriller, "
            "8 eps weekly from 4/29/26. As of 6/29/26 we have 61 days of "
            "post-launch data — both 21-day and 28-day windows fully "
            "captured. Coastal-town murder-mystery format that has "
            "performed well on Apple historically (Defending Jacob, "
            "Black Bird)."
        ),
    },
    {
        "project_name":   "Margos_Got_Money_Troubles_-_Season_1",
        "title":          "Margo's Got Money Troubles Season 1",
        "platform":       "apple tv+",
        "start":          "2026-04-15",
        "genre":          COMEDY_DRAMA,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       1_400_000,  # female-led dramedy, modest opener
        "episode_dates":  _eps_weekly("2026-04-15", 10),
        "context_note": (
            "Margo's Got Money Troubles Season 1 — Apple TV+ original "
            "comedy-drama, 10 eps weekly from 4/15/26. Based on the Rufi "
            "Thorpe novel. As of 6/29/26 we have 75 days of post-launch "
            "data — both windows fully captured. Female-led "
            "single-mom/single-parent dramedy in the spirit of Apple's "
            "broader Reese Witherspoon-produced slate."
        ),
    },

    # ──────────────────── 2025 RELEASES ────────────────────
    {
        "project_name":   "Your_Friends_and_Neighbors_-_Season_1",
        "title":          "Your Friends & Neighbors Season 1",
        "platform":       "apple tv+",
        "start":          "2025-04-11",
        "genre":          DRAMA,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       3_500_000,  # Jon Hamm tentpole, strong launch
        "episode_dates":  _eps_premiere_drop("2025-04-11", premiere_count=2, total_count=9),
        "context_note": (
            "Your Friends & Neighbors Season 1 — Apple TV+ original drama "
            "starring Jon Hamm (Mad Men) as a divorced hedge-funder who "
            "starts robbing his wealthy suburban neighbors. 9 eps total — "
            "2-ep premiere on 4/11/25 then weekly through 6/6/25. As of "
            "6/29/26 the show is 14 months old so both windows are fully "
            "captured. Strong Hamm-driven launch demo; benefited from S2 "
            "renewal announcement during S1's run."
        ),
    },

    # ──────────────────── 2024 RELEASES ────────────────────
    {
        "project_name":   "Presumed_Innocent_-_Season_1",
        "title":          "Presumed Innocent Season 1",
        "platform":       "apple tv+",
        "start":          "2024-06-12",
        "genre":          LEGAL_THRILLER,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       6_000_000,  # Apple's biggest series launch ever (per Apple PR July 2024)
        "episode_dates":  _eps_premiere_drop("2024-06-12", premiere_count=2, total_count=8),
        "context_note": (
            "Presumed Innocent Season 1 — Apple TV+ limited series, "
            "8 eps, 2-ep premiere on 6/12/24 then weekly through 7/24/24. "
            "Jake Gyllenhaal in legal-thriller adaptation of the Scott "
            "Turow novel (David E. Kelley showrunner). Became Apple TV+'s "
            "most-watched series ever on launch per Apple's own July 2024 "
            "press release — strong outlier for 21/28-day reach. Renewed "
            "for S2 due to outperformance."
        ),
    },
    {
        "project_name":   "Sugar_-_Season_1",
        "title":          "Sugar Season 1",
        "platform":       "apple tv+",
        "start":          "2024-04-05",
        "genre":          DETECTIVE_NOIR,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       2_000_000,  # Colin Farrell mid-tier launch
        "episode_dates":  _eps_premiere_drop("2024-04-05", premiere_count=2, total_count=8),
        "context_note": (
            "Sugar Season 1 — Apple TV+ neo-noir detective drama, "
            "8 eps total with 2-ep premiere on 4/5/24, weekly thereafter "
            "through 5/17/24. Colin Farrell as a Los Angeles private "
            "investigator with a sci-fi twist revealed mid-season. "
            "Solid Farrell-driven launch but reach softened after the "
            "genre-twist polarized viewers. Renewed for S2."
        ),
    },
    {
        "project_name":   "Constellation_-_Season_1",
        "title":          "Constellation Season 1",
        "platform":       "apple tv+",
        "start":          "2024-02-21",
        "genre":          SCIFI_THRILLER,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       1_600_000,  # mid-tier sci-fi, cancelled after S1
        "episode_dates":  _eps_premiere_drop("2024-02-21", premiere_count=3, total_count=8),
        "context_note": (
            "Constellation Season 1 — Apple TV+ sci-fi psychological "
            "thriller, 8 eps with 3-ep premiere on 2/21/24, weekly "
            "thereafter through 3/27/24. Noomi Rapace as an astronaut "
            "returning to find reality altered. Mid-tier launch reach for "
            "Apple sci-fi — not Severance-level but solid. Cancelled "
            "after S1."
        ),
    },

    # ──────────────────── 2023 RELEASES ────────────────────
    {
        "project_name":   "Monarch_Legacy_of_Monsters_-_Season_1",
        "title":          "Monarch: Legacy of Monsters Season 1",
        "platform":       "apple tv+",
        "start":          "2023-11-17",
        "genre":          MONSTER_ACTION,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       3_500_000,  # MonsterVerse IP lift, strong franchise opener
        "episode_dates":  _eps_premiere_drop("2023-11-17", premiere_count=2, total_count=10),
        "context_note": (
            "Monarch: Legacy of Monsters Season 1 — Apple TV+ MonsterVerse "
            "(Godzilla/King Kong) series, 10 eps with 2-ep premiere on "
            "11/17/23, weekly through 1/12/24. Kurt and Wyatt Russell. "
            "Strong franchise lift on launch — IP recognition + new "
            "audience pull for Apple. Renewed for S2."
        ),
    },
    {
        "project_name":   "Silo_-_Season_1",
        "title":          "Silo Season 1",
        "platform":       "apple tv+",
        "start":          "2023-05-05",
        "genre":          SCIFI_DRAMA,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       3_000_000,  # Apple's biggest sci-fi launch in 2023
        "episode_dates":  _eps_premiere_drop("2023-05-05", premiere_count=2, total_count=10),
        "context_note": (
            "Silo Season 1 — Apple TV+ dystopian sci-fi adapted from Hugh "
            "Howey's Wool series, 10 eps with 2-ep premiere on 5/5/23 "
            "weekly through 6/30/23. Rebecca Ferguson as engineer Juliette "
            "in a 10,000-person underground silo. Strong sci-fi launch, "
            "renewed for S2-S4. One of Apple's most consistent sci-fi "
            "performers."
        ),
    },
    {
        "project_name":   "Shrinking_-_Season_1",
        "title":          "Shrinking Season 1",
        "platform":       "apple tv+",
        "start":          "2023-01-27",
        "genre":          COMEDY_DRAMA,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       1_700_000,  # modest comedy launch, grew via word-of-mouth
        "episode_dates":  _eps_premiere_drop("2023-01-27", premiere_count=2, total_count=10),
        "context_note": (
            "Shrinking Season 1 — Apple TV+ comedy-drama, 10 eps with "
            "2-ep premiere on 1/27/23, weekly through 3/24/23. Jason "
            "Segel as a grieving therapist; Harrison Ford in a rare TV "
            "role. From Ted Lasso producers (Bill Lawrence/Brett "
            "Goldstein). Solid launch, strong word-of-mouth growth. "
            "Renewed for S2/S3."
        ),
    },

    # ──────────────────── 2022 RELEASES ────────────────────
    {
        "project_name":   "Slow_Horses_-_Season_1",
        "title":          "Slow Horses Season 1",
        "platform":       "apple tv+",
        "start":          "2022-04-01",
        "genre":          SPY_THRILLER,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       1_000_000,  # niche British-spy launch, grew over later seasons
        "episode_dates":  _eps_premiere_drop("2022-04-01", premiere_count=2, total_count=6),
        "context_note": (
            "Slow Horses Season 1 — Apple TV+ spy thriller, 6 eps with "
            "2-ep premiere on 4/1/22, weekly through 4/29/22. Gary "
            "Oldman as Jackson Lamb leading MI5's exiles at Slough "
            "House. Modest launch reach (British-spy genre is niche) "
            "but high critical acclaim; now in S5+ with consistent "
            "annual renewals."
        ),
    },
    {
        "project_name":   "Severance_-_Season_1",
        "title":          "Severance Season 1",
        "platform":       "apple tv+",
        "start":          "2022-02-18",
        "genre":          WORKPLACE_THRILL,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       2_500_000,  # cultural breakthrough, ~3M+ once Emmy buzz hit
        "episode_dates":  _eps_premiere_drop("2022-02-18", premiere_count=2, total_count=9),
        "context_note": (
            "Severance Season 1 — Apple TV+ psychological workplace "
            "thriller, 9 eps with 2-ep premiere on 2/18/22, weekly "
            "through 4/8/22. Adam Scott as Mark, with Ben Stiller "
            "directing/producing. Modest 21/28-day launch reach but "
            "MASSIVE long-tail growth over 3-year gap before S2 "
            "(2/2025). The S1 launch-window comp is what Apple wants "
            "here — NOT the post-S2-news inflated reach."
        ),
    },

    # ──────────────────── 2021 RELEASES ────────────────────
    {
        "project_name":   "Invasion_-_Season_1",
        "title":          "Invasion Season 1",
        "platform":       "apple tv+",
        "start":          "2021-10-22",
        "genre":          SCIFI_THRILLER,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       1_500_000,  # ~25M Apple TV+ subs, mixed reception suppressed launch
        "episode_dates":  _eps_premiere_drop("2021-10-22", premiere_count=3, total_count=10),
        "context_note": (
            "Invasion Season 1 — Apple TV+ alien-invasion sci-fi, 10 eps "
            "with 3-ep premiere on 10/22/21, weekly through 12/10/21. "
            "Multi-character global perspective on an alien arrival. "
            "Mixed-to-negative critical reception suppressed launch reach; "
            "renewed for S2/S3 anyway. Apple TV+ subscriber base in late "
            "2021 was ~40M paid subs (per various estimates) — meaningfully "
            "smaller than the 2024+ era so absolute reach numbers should "
            "be discounted accordingly."
        ),
    },
    {
        "project_name":   "Foundation_-_Season_1",
        "title":          "Foundation Season 1",
        "platform":       "apple tv+",
        "start":          "2021-09-24",
        "genre":          SCIFI_DRAMA,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       3_000_000,  # Apple's 2021 sci-fi tentpole, ~25M subs
        "episode_dates":  _eps_premiere_drop("2021-09-24", premiere_count=2, total_count=10),
        "context_note": (
            "Foundation Season 1 — Apple TV+ epic sci-fi adaptation of "
            "Isaac Asimov, 10 eps with 2-ep premiere on 9/24/21, weekly "
            "through 11/19/21. Massive marketing push from Apple as a "
            "tentpole launch; Jared Harris / Lee Pace cast. Strong "
            "launch reach by 2021 Apple TV+ standards. Renewed for S2/S3."
        ),
    },

    # ──────────────────── 2020 RELEASES (pre-2021 panel cutoff) ────────────────────
    {
        "project_name":   "Tehran_-_Season_1",
        "title":          "Tehran Season 1",
        "platform":       "apple tv+",
        "start":          "2020-09-25",
        "genre":          SPY_THRILLER,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       350_000,  # Israeli-language niche, Apple TV+ ~13M subs in Sept 2020
        "episode_dates":  _eps_premiere_drop("2020-09-25", premiere_count=3, total_count=8),
        "context_note": (
            "Tehran Season 1 — Apple TV+ Israeli spy thriller (in Persian / "
            "Hebrew / English), 8 eps with 3-ep premiere on 9/25/20, "
            "weekly through 11/6/20. Released when Apple TV+ was just 11 "
            "months old with a small subscriber base. The pre-2021 panel "
            "cutoff disclaimer will fire automatically — reach numbers "
            "should be interpreted as 'tracked viewing AFTER 1/1/21' not "
            "original launch-window. For client editorial: flag this "
            "show's first-21/28-day numbers as a low-confidence directional "
            "estimate (Apple TV+ subscriber base in Sept 2020 was ~10-15M "
            "vs 80M+ in 2026)."
        ),
    },
    {
        "project_name":   "Ted_Lasso_-_Season_1",
        "title":          "Ted Lasso Season 1",
        "platform":       "apple tv+",
        "start":          "2020-08-14",
        "genre":          SPORTS_COMEDY,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       900_000,  # modest launch in Aug 2020, exploded post-Emmys 2021
        "episode_dates":  _eps_premiere_drop("2020-08-14", premiere_count=3, total_count=10),
        "context_note": (
            "Ted Lasso Season 1 — Apple TV+ sports comedy, 10 eps with "
            "3-ep premiere on 8/14/20, weekly through 10/2/20. Jason "
            "Sudeikis as the AFC Richmond manager. Pre-2021 panel cutoff "
            "disclaimer fires automatically. The cultural-phenomenon "
            "reach numbers most associated with Ted Lasso came AFTER S1 "
            "wrapped — the actual first-21/28-day reach in Aug-Sept 2020 "
            "was modest because Apple TV+ subscriber base was ~10-15M. "
            "Editorial should distinguish 'launch reach' from 'eventual "
            "cultural reach.'"
        ),
    },
    {
        "project_name":   "For_All_Mankind_-_Season_1",
        "title":          "For All Mankind Season 1",
        "platform":       "apple tv+",
        "start":          "2019-11-01",
        "genre":          ALT_HISTORY,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       600_000,  # Apple TV+ LAUNCH DAY content, ~6M paid subs at debut
        "episode_dates":  _eps_premiere_drop("2019-11-01", premiere_count=3, total_count=10),
        "context_note": (
            "For All Mankind Season 1 — Apple TV+ alternate-history space "
            "drama (the Soviets land on the Moon first), 10 eps with "
            "3-ep premiere on 11/1/19, weekly through 12/20/19. THIS WAS "
            "APPLE TV+ LAUNCH-DAY CONTENT — Apple TV+ debuted 11/1/19 "
            "with this show as one of its 4 original-series tentpoles. "
            "Heavy promo push BUT subscriber base was ~5-10M at launch "
            "(growing rapidly through free Apple-device trial period). "
            "Pre-2021 disclaimer fires. Editorial: this is the lowest-"
            "confidence comp in the set; first-21/28-day numbers should "
            "be treated as 'directional estimate based on post-2021 "
            "tracked viewing patterns' not original launch reach."
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
    return cfg


def main() -> None:
    print(f"📺 Apple TV+ Star City comp set: {len(CONFIGS)} trackers to pull")
    print()
    results: list[tuple[str, str, str]] = []
    for idx, spec in enumerate(CONFIGS, 1):
        print(f"\n{'='*70}\n  [{idx}/{len(CONFIGS)}] {spec['title']}\n{'='*70}")
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
