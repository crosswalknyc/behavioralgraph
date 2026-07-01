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

# ──────────────────────────────────────────────────────────────────────
# CONVERSION OVERRIDES (% of viewers who became new+reactivated subs in
# the 30-day window) and REACTIVATION_PCT (fraction of those signups that
# are returning dormant subs, i.e. 1 - new_share).
#
# These replace the pipeline's genre-keyed lookup-table defaults — the
# defect family that produced identical D values across same-genre shows
# in the previous pull run. Each per-title value is sourced from the
# show-specific research documented in star_city_per_title_research.md:
#   - Antenna Subscriber Views (Severance, Stick, etc.)
#   - Kantar EoD (Slow Horses + Ted Lasso UK Q4'23)
#   - Parrot Analytics demand multiples (Foundation, Silo, etc.)
#   - Nielsen Top 10 entries (Your Friends & Neighbors, Pluribus)
#   - Apple PR + corroborating third-party data (with Apple PR
#     down-weighted where third-party signal contradicts — e.g.
#     Presumed Innocent's Antenna Q2'24 share decline)
#   - Renewal velocity + cancellation outcomes (Constellation, Sugar)
#   - Era-adjusted new_share (free-trial-era 2019-2021: 0.80-0.95;
#     mature-platform 2024-2026: 0.55-0.70 — reflecting deepening
#     dormant-sub pool + Amazon Channels new/reactivation skew)
# ──────────────────────────────────────────────────────────────────────

CONFIGS: list[dict] = [
    # ──────────────────── 2026 NEW RELEASES ────────────────────
    {
        "project_name":   "Star_City_-_Season_1",
        "title":          "Star City Season 1",
        "platform":       "apple tv+",
        "start":          "2026-05-29",
        "genre":          ALT_HISTORY,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       1_600_000,  # FAM spinoff, #4 launch wk per FlixPatrol
        "conv_pct":       3.5,        # critic-favorite spinoff with engagement skew
        "new_share":      0.60,       # mature 2026 platform, more reactivation
        "episode_dates":  _eps_premiere_drop("2026-05-29", premiere_count=2, total_count=8),
        "context_note": (
            "Star City Season 1 — Apple TV+ For All Mankind spinoff, "
            "Soviet-era reframe of the alternate-history space race. "
            "8 eps with 2-ep premiere on 5/29/26, weekly through 7/10/26. "
            "Created by Nedivi/Wolpert/Moore. As of 6/29/26, 21-day window "
            "is fully captured; 28-day window has ~3 days remaining. "
            "97% RT critic, mixed audience reception. Strong franchise "
            "halo from 5 seasons of FAM, but slower-burn pacing and "
            "lack of marquee star limit broad-audience acquisition. "
            "Trades #1 globally with FAM S5 finale; #4 on Apple TV+ "
            "global chart in launch week (FlixPatrol)."
        ),
    },
    {
        "project_name":   "Cape_Fear_-_Season_1",
        "title":          "Cape Fear Season 1",
        "platform":       "apple tv+",
        "start":          "2026-06-05",
        "genre":          CRIME_THRILLER,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       2_200_000,  # top-2/3 Apple TV+ chart position launch wk (FlixPatrol) — up-tiered
        "conv_pct":       4.2,        # tentpole marketing + Spielberg/Scorsese EP + Bardem/Adams draw
        "new_share":      0.55,       # mature platform, deep dormant pool
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
        "reach_us":       1_100_000,  # half-hour launch on mature 2026 platform (~2.4% penetration)
        "conv_pct":       3.1,        # dark-comedy thriller, mature-platform stickiness slightly above Invasion tier
        "new_share":      0.65,       # newer concept skews slightly more new
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
        "reach_us":       1_900_000,  # Matthew Rhys prestige-halo (The Americans) pulls above mid-tier
        "conv_pct":       3.6,        # Hiro Murai directing (Atlanta) + horror-comedy novelty above Star City tier
        "new_share":      0.60,       # mature platform engagement skew
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
        "conv_pct":       3.7,        # top of mid-tier — stacked ensemble (Fanning/Pfeiffer/Kidman/Offerman + A24 + Kelley) pulls strongest sampling
        "new_share":      0.60,       # mature platform, cast-driven sampling
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
        "project_name":   "Pluribus_-_Season_1",
        "title":          "Pluribus Season 1",
        "platform":       "apple tv+",
        "start":          "2025-11-07",
        "genre":          SCIFI_THRILLER,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       3_900_000,  # Apple TV+ all-time biggest drama launch
        "conv_pct":       11.0,       # Vince Gilligan halo; surpassed Severance S2 record
        "new_share":      0.70,       # post-Amazon-Channels mature platform
        "episode_dates":  _eps_premiere_drop("2025-11-07", premiere_count=2, total_count=9),
        "context_note": (
            "Pluribus Season 1 — Apple TV+ sci-fi thriller from Vince "
            "Gilligan (Breaking Bad / Better Call Saul) starring Rhea "
            "Seehorn. 9 eps with 2-ep premiere on 11/7/25, weekly "
            "through 12/26/25. Set the Apple TV+ all-time record for "
            "biggest global drama launch, surpassing Severance S2. "
            "6.4M hours viewed in week 1 (Luminate). Eventually became "
            "Apple TV+'s most-watched series in platform history, "
            "surpassing both Severance and Ted Lasso."
        ),
    },
    {
        "project_name":   "Your_Friends_and_Neighbors_-_Season_1",
        "title":          "Your Friends & Neighbors Season 1",
        "platform":       "apple tv+",
        "start":          "2025-04-11",
        "genre":          DRAMA,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       3_700_000,  # 200-day #1 streak + Nielsen 392M mins finale wk — up-tiered
        "conv_pct":       6.5,        # dethroned Severance S2; Nielsen Top 10 finale wk
        "new_share":      0.65,       # Hamm draw pulls new + reactivates Mad Men adjacency
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
        "project_name":   "Dark_Matter_-_Season_1",
        "title":          "Dark Matter Season 1",
        "platform":       "apple tv+",
        "start":          "2024-05-08",
        "genre":          SCIFI_THRILLER,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       3_000_000,  # #1 globally day 1, Reelgood week 2 cross-platform leader
        "conv_pct":       6.2,        # #1 globally within 24hrs was truly instant conversion signal (above Severance S1 slower-build)
        "new_share":      0.65,       # accessible sci-fi pulls new + reactivates
        "episode_dates":  _eps_premiere_drop("2024-05-08", premiere_count=2, total_count=9),
        "context_note": (
            "Dark Matter Season 1 — Apple TV+ sci-fi thriller adapted from "
            "Blake Crouch's 2016 novel, Joel Edgerton + Jennifer Connelly. "
            "9 eps with 2-ep premiere on 5/8/24, weekly through 6/26/24. "
            "Became Apple TV+'s most-watched series worldwide within 24 "
            "hours (FlixPatrol). Topped Reelgood's cross-platform streaming "
            "chart for the week of May 9-15 (beat Fallout, Bodkin, Baby "
            "Reindeer). Renewed for S2."
        ),
    },
    {
        "project_name":   "Presumed_Innocent_-_Season_1",
        "title":          "Presumed Innocent Season 1",
        "platform":       "apple tv+",
        "start":          "2024-06-12",
        "genre":          LEGAL_THRILLER,
        "cadence":        "Weekly",
        "is_new":         True,
        "reach_us":       6_000_000,  # Apple's biggest series launch ever (per Apple PR July 2024)
        "conv_pct":       3.0,        # Antenna Q2'24 shows Apple TV+ share of gross adds DECLINED during launch — top reach, bottom of prestige-tier conversion
        "new_share":      0.55,       # mature platform, engagement>>acquisition pattern
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
        "reach_us":       1_800_000,  # Colin Farrell mid-tier launch, no chart/Nielsen (down-tiered from 2M)
        "conv_pct":       3.3,        # 81% RT only, no Nielsen Top 10, no chart — below Star City tier
        "new_share":      0.60,       # mature platform, prestige engagement skew
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
        "reach_us":       1_350_000,  # canceled after S1, never made Nielsen Top 10 — smallest of 2024 comp cohort
        "conv_pct":       2.5,        # CANCELED — never made Nielsen Top 10 (Apple does not cancel hit shows)
        "new_share":      0.65,       # narrow sci-fi base, small absolute number
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
        "reach_us":       3_200_000,  # MonsterVerse IP lift; but NO Nielsen Top 10 in S1 (S2 was franchise-first per S2 press) — down-tiered from 3.5M
        "conv_pct":       3.9,        # Reelgood #3 launch wk, franchise sampling behavior (viewers already-subs on Godzilla halo)
        "new_share":      0.65,       # IP brings new + reactivates kaiju fans
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
        "reach_us":       3_300_000,  # Apple "#1 drama in history" May'23 on ~25M-sub platform — up-tiered from 3.0M
        "conv_pct":       7.5,        # "#1 drama in Apple TV+ history" May'23, 5 wks Reelgood top 10, 2-wk S2 renewal
        "new_share":      0.70,       # broader-appeal sci-fi pulls genuine new subs
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
        "conv_pct":       4.5,        # week-2 audience > week-1, JustWatch #3 / Reelgood #5
        "new_share":      0.70,       # Harrison Ford + Ted Lasso pedigree pulls new
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
        "reach_us":       950_000,  # sleeper hit, very quiet S1 in 2022; halo built over later seasons
        "conv_pct":       2.1,        # 95% RT gave modest legit lift above pure niche tier; Kantar's UK stat was at S3 not S1
        "new_share":      0.75,       # 2022-era platform, free-trial tail
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
        "conv_pct":       5.8,        # Reelgood #1 by week 3 — but peak was slow-build to finale; Antenna's 14% was S2 not S1
        "new_share":      0.80,       # 2022-era growing platform, free-trial still in effect
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
        "conv_pct":       2.8,        # IGN "too ambitious, slow" reviews; no chart-topping placements — below Max Pleasure tier
        "new_share":      0.75,       # 2021-era growing platform, free-trial still active
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
        "reach_us":       2_400_000,  # 2021 platform smaller (~15M subs) + mixed reviews ("too dense/slow") — down-tiered from 3.0M
        "conv_pct":       5.5,        # Parrot 35x avg demand, 2-wk S2 renewal, but niche Asimov appeal caps broader conversion
        "new_share":      0.80,       # 2021 free-trial era + small platform
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
        "conv_pct":       1.9,        # international-skew (India/Japan/Singapore per press) limits US-specific conversion vs Slow Horses
        "new_share":      0.85,       # 2020-era tiny platform, virtually no install base
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
        "conv_pct":       12.0,       # Apple-disclosed "25% new viewers" at 10 wk; free-trial era leverage
        "new_share":      0.85,       # 2020-era tiny platform, vast majority truly new
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
        "conv_pct":       15.0,       # day-1 platform launch — most viewers were brand-new signups
        "new_share":      0.95,       # virtually no install base existed to reactivate
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
    if "conv_pct" in spec:
        cfg["conversion_pct"] = float(spec["conv_pct"])
    if "new_share" in spec:
        cfg["reactivation_pct_override"] = max(0.0, min(1.0, 1.0 - float(spec["new_share"])))
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
