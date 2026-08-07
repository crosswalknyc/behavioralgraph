#!/usr/bin/env python3
"""Pull Young Sherlock Season 1 (Amazon Prime Video, March 4, 2026).

Young Sherlock is a British mystery TV series developed by Peter Harness
and Guy Ritchie, created by Matthew Parkhill, inspired by Andrew Lane's
Young Sherlock Holmes book series (which itself is a pastiche of the
Arthur Conan Doyle canon). Hero Fiennes Tiffin (After franchise) stars
as 19-year-old Sherlock Holmes at Oxford University, 1871. Ensemble:
Dónal Finn as James Moriarty (unlikely ally-before-nemesis), Zine Tseng
(3 Body Problem) as Princess Gulun Shou'an, Joseph Fiennes (Handmaid's
Tale) as father Silas Holmes, Natascha McElhone (Halo) as mother Cordelia
Holmes, Max Irons (Condor) as older brother Mycroft, Colin Firth (King's
Speech) as antagonist Professor Sir Bucephalus Hodge.

Season structure:
    All 8 episodes dropped simultaneously on Wed 3/4/2026 (Prime binge
    cadence). Runtime 43-55 min per episode (~6.5h season total). Guy
    Ritchie directed episodes 1-2 and serves as executive producer.

Season 2 renewed April 2026, one month after launch.

Post-1/1/2021 → fully within tracking window.

Producers: Motive Pictures (physical production) + Amazon MGM Studios
+ Inspirational Entertainment + Toff Guy Films (Ritchie's shingle).

──────────────────────────────────────────────────────────────────────
ROW-BY-ROW REASONING
──────────────────────────────────────────────────────────────────────

reach_us = 6,500,000
    Anchor sources:

    1. Pre-launch buzz: Young Sherlock trailer (released Feb 5, 2026)
       set a NEW RECORD for most-watched Amazon Prime series trailer
       in first 7 days. Screenings held in NYC (Feb 9), Mexico City
       (Feb 17), London (Feb 24) generated strong critical + audience
       word of mouth ahead of launch.

    2. Prime Video launch-week comps (Antenna + Nielsen 30d US uniques):
         Reacher S1  (Feb 2022, binge):        ~7.5M
         Reacher S2  (Dec 2023, binge):        ~9.5M
         Cross S1    (Nov 2024, Patterson IP): ~4.5M
         Fallout S1  (Apr 2024, viral):        ~14M
         Scarpetta   (Mar 2026, Kidman/Curtis):~9.5M
         Elle S1     (Jul 2026, YA IP):        ~5M
         Ride or Die (Jul 2026, buddy comedy): ~12M

    3. Where Young Sherlock lands within Prime's crime-thriller lane:
       (+) Guy Ritchie brand attaches auteur pull (Snatch / Sherlock
           Holmes films fanbase — the 2009/2011 Downey Jr movies
           grossed $1.06B combined worldwide).
       (+) Colin Firth adds prestige-drama pull.
       (+) Sherlock Holmes IP is one of the most globally-recognized
           in fiction — massive brand recall.
       (+) Hero Fiennes Tiffin brings young-female-Gen-Z demo (5M+
           TikTok fanbase from After franchise).
       (-) British mystery period-piece skews older / narrower than
           Reacher's action-thriller mass appeal.
       (-) Fiennes Tiffin has narrower Q rating than Kidman.
       (-) Sherlock TV competition heavy (BBC Sherlock rerun,
           Enola Holmes 3 on Netflix Q1 2026).

    4. Nielsen streaming top-10 charts for weeks of 3/4 and later:
       Young Sherlock did NOT crack the top-10 originals in first two
       weeks (based on Nielsen releases that DID include Scarpetta
       Mar 11 launch that week). That indicates first-week reach
       BELOW the ~1B minute threshold. However, Nielsen top-10 misses
       shows in the 500M-999M range which are still hits — Young
       Sherlock was frequently cited on FlixPatrol top-10 for Prime
       globally through mid-March 2026.

       Interpretation: Young Sherlock is a solid launch but sub-viral,
       comparable to Bosch: Legacy S3 territory (~4-5M) with a slight
       Guy-Ritchie-trailer-record boost.

    Anchor: 6.5M — above Cross S1 (4.5M) reflecting the Ritchie brand
    + trailer record + Colin Firth prestige adjacency; below Reacher
    S2 (9.5M) reflecting narrower British-mystery demo appeal.

conv_pct = 0.85%
    Prime prestige-launch conversion band 0.5-1.3%.

    Guy Ritchie brand + Sherlock IP + trailer record indicate above-
    average conversion drive from Sherlock literature/cinema fans who
    don't yet have Prime. Young Fiennes Tiffin adds Gen Z conversion
    tail.

    Anchor 0.85% — mid-tentpole (below Scarpetta's 0.9%, matching
    Reacher S2's 0.85%). Sherlock IP is bigger than any single
    Prime crime lead, offset by British-period-piece narrower demo.

new_share = 0.42
    Prime acquisition breakdown patterns:
        + Guy Ritchie fanbase + Colin Firth pull moderate new signups
          (both stars have Netflix/HBO-first audiences that skew
          away from Prime).
        + Hero Fiennes Tiffin's After franchise TikTok audience
          (young Gen-Z female) is heavily under-indexed on Prime →
          new-to-Prime conversion.
        - British mystery format skews older-lapsed-Prime demo
          (heavy reactivation) rather than never-Prime.

    Anchor: 42% new / 58% reactivation. Matches Ride or Die and
    Scarpetta because Prime Video's mature-service acquisition is
    generally 40-42% new / 58-60% react across prestige launches.

pre_existing_pct = 0.03
    Original series (first screen adaptation of Andrew Lane's Young
    Sherlock Holmes book series, published 2010-2014). Prior Sherlock
    Holmes adaptations exist (Downey films, BBC Sherlock, Elementary,
    Enola Holmes) but the pipeline tracks THIS specific title, not
    IP family. Small non-zero anchor for trailer-view engagement
    floor.
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


MYSTERY_PERIOD = "Mystery - Period Drama"
DASHBOARD_CAT  = "SERIES - PRIME VIDEO ORIGINAL"


CONFIG: dict = {
    "project_name":     "Young_Sherlock_-_Season_1",
    "title":            "Young Sherlock Season 1",
    "platform":         "amazon prime video",
    "start":            "2026-03-04",
    "genre":            MYSTERY_PERIOD,
    "cadence":          "All at Once",
    "is_new":           True,
    "reach_us":         6_500_000,
    "conv_pct":         0.85,
    "new_share":        0.42,
    "pre_existing_pct": 0.03,
    "episode_dates":    _eps_binge("2026-03-04", 8),
    "context_note": (
        "Young Sherlock Season 1 — Amazon Prime Video British mystery "
        "period drama, 8 episodes released simultaneously on Wed "
        "3/4/2026 (Prime binge cadence). Created by Matthew Parkhill, "
        "developed by Peter Harness and Guy Ritchie (who directed "
        "episodes 1-2 and executive-produced). Inspired by Andrew "
        "Lane's Young Sherlock Holmes book series (published 2010-14), "
        "itself a pastiche of Arthur Conan Doyle's canon. Hero Fiennes "
        "Tiffin (After franchise) stars as 19-year-old Sherlock at "
        "Oxford University in 1871, jailed on false theft charges by "
        "antagonist Professor Sir Bucephalus Hodge (Colin Firth); "
        "teams up with future-nemesis-current-ally James Moriarty "
        "(Dónal Finn) to clear his name. Ensemble: Zine Tseng (3 Body "
        "Problem) as Princess Gulun Shou'an, Joseph Fiennes (Handmaid's "
        "Tale) as father Silas Holmes, Natascha McElhone (Halo) as "
        "mother Cordelia Holmes, Max Irons (Condor) as older brother "
        "Mycroft, Numan Acar, Holly Cattle. Opening theme 'Days Are "
        "Forgotten' by Kasabian. Runtime 43-55 min per episode. "
        "Produced by Motive Pictures + Amazon MGM Studios + Inspirational "
        "Entertainment + Toff Guy Films (Ritchie's shingle). Pre-launch "
        "trailer (released 2/5/2026) set a new Amazon Prime record for "
        "most-watched series trailer in first 7 days. Screenings held "
        "in NYC (2/9), Mexico City (2/17), London (2/24). Season 2 "
        "renewed April 2026. Reach anchor 6.5M reflects strong Guy "
        "Ritchie brand + Sherlock IP + record trailer boost, tempered "
        "by narrower British-period-mystery demo appeal vs Prime's "
        "action-thriller mass tier (Reacher). Prime US paid subs at "
        "launch: ~76M (Antenna Q1'26). Signup profile: 42% new "
        "(Ritchie + Firth + Fiennes Tiffin TikTok Gen-Z pull) / 58% "
        "reactivation (British-period audience skews lapsed-Prime)."
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
    print(f"🕵️  Young Sherlock Season 1 (Prime Video) — SubIQ pull")
    for k in ("reach_us", "conv_pct", "new_share", "pre_existing_pct"):
        print(f"    {k:<18s} = {CONFIG[k]}")
    r = run_synthetic_attribution(build_config(CONFIG))
    if isinstance(r, dict) and r.get("s3_key"):
        print(f"\n  ✅ uploaded {r['s3_key']}  reach={r.get('reach_us'):,}  signups={r.get('new_signups_us'):,}")


if __name__ == "__main__":
    main()
