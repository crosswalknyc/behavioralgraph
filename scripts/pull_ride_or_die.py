#!/usr/bin/env python3
"""Pull Ride or Die Season 1 (Amazon Prime Video, July 15, 2026).

Ride or Die is a Prime Video action-adventure-comedy from creator Tessa
Coates and showrunner Matt Miller (director: Peyton Reed, Ant-Man
trilogy). Octavia Spencer (Debbie Claybourne, American housewife in
England) and Hannah Waddingham (Judith Burton, code-name Whiptail — a
20-year veteran international assassin whose best friend never knew)
lead. Ensemble: Bill Nighy, Ed Skrein, Sylvia Hoeks, Calam Lynch,
Savannah Steyn, Jamie Parker, Jacky Ido. Shot entirely on location in
Prague; European road-trip plot.

Season structure:
    All 8 episodes dropped simultaneously on Wed 7/15/2026 (Prime
    Video binge cadence, following the Reacher / Bosch: Legacy
    blueprint). Runtime 48-55 min per episode (~6.9h season total).

Post-1/1/2021 → fully within tracking window.

Producers: Paramount TV Studios + Amazon MGM Studios, Double Dream
(Andy + Barbara Muschietti), Orit Entertainment (Octavia Spencer).

──────────────────────────────────────────────────────────────────────
ROW-BY-ROW REASONING (per Jenna's methodology — no formulas / no
peer-archetype template stamping; each anchor is externally grounded)
──────────────────────────────────────────────────────────────────────

reach_us = 12,000,000
    Anchor sources:

    1. Luminate US views (definitive US streaming panel):
       Wk1 partial (Jul 10-16, 2 days only): 927K views, #8 originals
       Wk1 full  (Jul 17-23):                3.85M views, #1 Prime
                                              -- 2x Elle's Jul 8-14 wk1
                                              -- 4x Off Campus first wk
       Wk2      (Jul 20-26): held #1 US minutes, ~2.7M views
       Wk3      (Jul 27-Aug 2): 2.57M views, #2 to Netflix Ransom Canyon

       Cumulative through wk3 (Luminate-panel views): ~9.2M
       Extending through wk4 with typical -20% weekly decay: ~11M
       Panel-to-reach uplift (~10-15% miss): 12.6M
       Anchor: 12M unique US 30-day accounts.

    2. Boardroom / Nielsen-adjacent US minutes:
       Wk of Jul 17-23:  1.6B US minutes  (#1 all-originals, ahead
                                            of Netflix Hawk 1.5B and
                                            Little House on the Prairie
                                            1.1B). Ridiculous first-week
                                            number for a low-marketing
                                            launch.

    3. FlixPatrol: #1 Prime Video globally for 3 consecutive weeks
       (Jul 20-Aug 7). #1 in 13 individual countries including US
       for every day of the week ending Jul 26.

    4. Prime Video prestige binge comps (30-day US uniques):
         Reacher S1 (Feb 2022, binge):     ~7.5M
         Reacher S2 (Dec 2023, binge):     ~9.5M
         Fallout S1 (Apr 2024, viral):    ~14M
         Elle S1 (Jul 2026, comparable window): ~5M  (see pull_elle)
         Young Sherlock S1 (Mar 2026):    ~6.5M   (see pull_young_sherlock)

       Ride or Die sits ABOVE Reacher S2 (stronger word-of-mouth,
       higher critical score 98% RT) but BELOW Fallout viral tier.
       Anchor 12M puts it comparable to Fallout / Cross S1 territory,
       reflecting the exceptional early Luminate trajectory.

    5. Star power factors:
       Octavia Spencer: Oscar winner, mass Q rating across all demos.
       Hannah Waddingham: Ted Lasso Emmy winner, culturally-elevated
                          post-Apple TV run.
       Bill Nighy: prestige adjacency (Living Oscar nom).
       Ed Skrein: genre-thriller pull (Deadpool, The Transporter).

       Star ensemble strength = female 50+ audience (highly under-
       served) PLUS male genre-thriller audience = unusually broad
       cross-demo pull for a binge comedy. Matches Deadline's
       observation that Ride or Die "snuck up under the radar"
       (minimal marketing) and eclipsed Prime's heavily-marketed
       Off Campus + Masters of the Universe.

conv_pct = 0.9%
    Prime Video prestige-drama BB/AA benchmarks (Antenna Q2-Q3 2026):
        Reacher S2 (2023):       ~0.85%
        Fallout S1 (2024):       ~1.3%   (viral, event tier)
        Cross S1 (2024):         ~0.6%
        Elle S1 (Jul 2026):      ~0.75%
        Off Campus S1 (2026):    ~0.5%

    Ride or Die specifics: high critical acclaim (98% RT) + broad
    demo pull + #1 Prime for 3+ weeks. Word-of-mouth growth curve
    (opened low with 927K views wk1 partial, exploded to 3.85M wk1
    full) is characteristic of surprise hits that convert late
    signups from lapsed viewers.

    Above Reacher S2 tentpole (0.85%) — Ride or Die has broader
    demo pull. Below Fallout viral (1.3%) — no game-adaptation
    marketing engine. Anchor 0.9%.

new_share = 0.42
    Prime Video acquisition breakdown for original launches:
        Reacher S3 (2025):       ~35% new  / 65% react
        Fallout S1 (2024):       ~48% new  / 52% react
        Elle S1 (2026):          ~48% new  / 52% react
        Bosch: Legacy S3:        ~22% new  / 78% react

    Ride or Die drivers:
      + Octavia Spencer + Hannah Waddingham fanbase pulls new-to-Prime
        signups (Waddingham fans skew Apple-TV-first from Ted Lasso).
      + Female 50+ demo historically under-indexed on Prime → new
        signups from that cohort.
      - Prime saturation is high (~76M US paid subs Q2'26); most
        candidate signers already have Prime.
      - Older audience is heavily lapsed-Prime, not never-Prime.

    Net anchor: 42% new / 58% reactivation. Slightly above Reacher's
    35% because of demo-broadening; slightly below Elle's 48%
    because Elle's Gen-Z-Legally-Blonde pull recruits more virgin
    Prime houses than Ride or Die's older female audience.

pre_existing_pct = 0.03
    Original series, no prior IP on Prime, no book series with an
    existing audience. Small non-zero anchor reflects viewers tagged
    as pre-existing from heavy trailer campaign + press coverage
    baseline (not literal "watched before"). Effectively brand new
    IP with zero prior viewership possible.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO))

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


def _eps_binge(start: str, count: int) -> list[dict]:
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    return [
        {"episode_num": i + 1, "air_date": start_dt, "display_label": f"Episode {i + 1}"}
        for i in range(count)
    ]


ACTION_COMEDY = "Action Adventure Comedy"
DASHBOARD_CAT = "SERIES - PRIME VIDEO ORIGINAL"


CONFIG: dict = {
    "project_name":     "Ride_or_Die_-_Season_1",
    "title":            "Ride or Die Season 1",
    "platform":         "amazon prime video",
    "start":            "2026-07-15",
    "genre":            ACTION_COMEDY,
    "cadence":          "All at Once",
    "is_new":           True,
    "reach_us":         12_000_000,
    "conv_pct":         0.9,
    "new_share":        0.42,
    "pre_existing_pct": 0.03,
    "episode_dates":    _eps_binge("2026-07-15", 8),
    "context_note": (
        "Ride or Die Season 1 — Amazon Prime Video original action-"
        "adventure-comedy, 8 episodes released simultaneously on Wed "
        "7/15/2026 (Prime binge cadence). Created by Tessa Coates, "
        "showrun by Matt Miller (Chuck, Forever), directed by Peyton "
        "Reed (Ant-Man trilogy) with additional eps by Alison Liddi-"
        "Brown, Demane Davis, and Lauren Wolkstein. Octavia Spencer as "
        "Debbie Claybourne (American housewife in England); Hannah "
        "Waddingham as Judith Burton, code-named Whiptail — an elite "
        "20-year veteran international assassin whose best friend "
        "Debbie has never known her true profession. Contract goes "
        "sideways, secret exposed, duo forced on European road-trip "
        "escape chased by law enforcement, rival assassins, and "
        "criminals. Shot entirely on location in Prague. Ensemble: "
        "Bill Nighy, Ed Skrein, Sylvia Hoeks, Calam Lynch, Savannah "
        "Steyn, Jamie Parker, Jacky Ido. Produced by Paramount TV "
        "Studios + Amazon MGM Studios + Double Dream (Andy + Barbara "
        "Muschietti) + Orit Entertainment (Spencer). Critical "
        "reception: 98% Rotten Tomatoes (Certified Fresh), 84% "
        "audience. Streaming performance: #1 Prime Video globally 3+ "
        "consecutive weeks since launch, #1 in 13 countries, US #1 "
        "daily rank held from launch. Luminate US views: wk1 full "
        "(Jul 17-23) 3.85M views (2x Elle's wk1, 4x Off Campus), wk3 "
        "still 2.57M. Boardroom Nielsen-adjacent wk of Jul 17-23: "
        "1.6B US minutes, #1 all-originals (ahead of Netflix's Hawk "
        "1.5B). Word-of-mouth driven (Prime did NOT heavy-market this "
        "vs Off Campus / Masters of the Universe). Reach anchor 12M "
        "reflects the exceptional 3-week retention curve. Prime US "
        "paid subs at launch: ~76M (Antenna Q2'26). Being submitted "
        "for Emmys in the Comedy Series category. Broad cross-demo "
        "pull: female 50+ Waddingham/Spencer fandom + male genre-"
        "thriller Skrein/Nighy adjacency."
    ),
}


def build_config(spec: dict) -> dict:
    start = datetime.strptime(spec["start"], "%Y-%m-%d")
    last_ep = max(e["air_date"] for e in spec["episode_dates"])
    return {
        "project_name":              spec["project_name"],
        "show_search_terms":         [spec["title"]],
        "platform_name":             spec["platform"],
        "campaign_start":            start,
        "campaign_end":              last_ep,
        "exclusion_days":            180,
        "attribution_window":        30,
        "genre":                     spec["genre"],
        "content_cadence":           spec["cadence"],
        "is_new_show":               spec["is_new"],
        "episode_dates":             spec["episode_dates"],
        "upload_to_s3":              True,
        "s3_bucket":                 "svod-acquisition",
        "dashboard_category":        DASHBOARD_CAT,
        "output_dir":                "/tmp/svod_synthetic_runs",
        "context_note":              spec["context_note"],
        "reach_us_override":         spec["reach_us"],
        "conversion_pct":            float(spec["conv_pct"]),
        "reactivation_pct_override": max(0.0, min(1.0, 1.0 - float(spec["new_share"]))),
        "pre_existing_pct":          float(spec["pre_existing_pct"]),
    }


def main() -> None:
    print(f"🏍  Ride or Die Season 1 (Prime Video) — SubIQ pull")
    for k in ("reach_us", "conv_pct", "new_share", "pre_existing_pct"):
        print(f"    {k:<18s} = {CONFIG[k]}")
    r = run_synthetic_attribution(build_config(CONFIG))
    if isinstance(r, dict) and r.get("s3_key"):
        print(f"\n  ✅ uploaded {r['s3_key']}  reach={r.get('reach_us'):,}  signups={r.get('new_signups_us'):,}")


if __name__ == "__main__":
    main()
