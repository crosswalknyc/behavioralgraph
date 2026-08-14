#!/usr/bin/env python3
"""P-Valley Season 2 (Starz) — Subscriber IQ pull.

======================================================================
SHOW BACKGROUND
======================================================================

Creator:   Katori Hall (Pulitzer Prize winner; based on her play
           'Pussy Valley')
Platform:  Starz (linear + Starz app + on-demand)
Genre:     Southern Noir Drama (adult drama, Black-female-led)
Setting:   The Pynk, a strip club in the fictional Chucalissa, MS Delta
Cast:      Brandee Evans (Mercedes), Nicco Annan (Uncle Clifford,
           GLAAD winner), Elarica Johnson (Autumn Night), Shannon
           Thornton (Miss Mississippi), J. Alphonse Nicholson (Lil
           Murda), Skyler Joy (Gidget), Parker Sawyers (Andre)

Season 2 release schedule (Starz app Fridays / linear Sundays):
    S2E1  "Pussyland"              — Fri 6/3/2022 (linear Sun 6/5)
    S2E2  "Seven Pounds of Pressure" — 6/12/2022
    S2E3  "The Dirty Dozen"        — 6/19/2022
    S2E4  "Demethrius"             — 6/26/2022
    S2E5  "White Knights"          — 7/3/2022
    S2E6  "Savage"                 — 7/10/2022
    (7/17 skipped — Independence-Day week hiatus)
    S2E7  "Jackson"                — 7/24/2022
    S2E8  "The Death Drop"         — 7/31/2022
    S2E9  "Snow"                   — 8/7/2022
    S2E10 "Mississippi Rule"       — 8/14/2022

Analysis window: 30 days from 6/3/2022 premiere → 7/3/2022. Captures
E1-E5 in-window (E6-E10 outside 30-day cume, standard SubIQ window).

======================================================================
ANCHOR REASONING
======================================================================

reach_us = 7,000,000
    Starz S2 official PR: "10.3M viewers per episode across linear,
    VOD and streaming platforms domestically" (Starz press release,
    10/20/2022). That's Starz's own cross-platform per-episode cume
    including LATE catch-up (measured months after finale).

    For SubIQ 30-day-from-premiere unique reach, we need the union of
    US Starz accounts that engaged with S2E1-E5 within 30 days:
      - E1 30d cume: ~10-11M (peak episode, S2 launch event)
      - E2 30d cume: ~8-9M (27d catch-up)
      - E3 30d cume: ~7M (20d catch-up)
      - E4 30d cume: ~5M (13d catch-up)
      - E5 30d cume: ~3M (6d catch-up)
      - UNIQUE union (heavy overlap): ~11-13M IF measuring "any S2 ep"
      - UNIQUE union CAMPAIGN-attributable (P-Valley-driven Starz
        engagement, not incidental Starz users): ~7M

    Anchor 7M reflects "P-Valley-attributable" 30-day US unique reach
    — the audience whose Starz session in the window was P-Valley-
    directed. Below the naive 10.3M/ep because that number is late-
    tail lifetime cume, above single-week-cume because binge-catch-up
    stacks E1-E4.

    Starz's total US sub base was ~14-15M at S2 launch (Q2 2022).
    7M P-Valley 30d reach = ~48% of the sub base, plausible for
    "biggest show on the platform" status.

    S2 3-day premiere cume: 4.5M cross-platform (per THR / Starz PR)
    — that's E1 alone in 72 hours. 30-day cume across E1-E5 landing
    at ~7M campaign-attributable uniques is consistent (E1 3d 4.5M →
    E1 30d ~10M, then union of E1-E5 at 7M campaign-attributable
    after de-overlapping the same-user cross-episode watchers).

conv_pct = 1.2%
    Starz niche/premium band (0.8-1.5%). P-Valley sits at the top:
      - S2 premiere drove +1,018% (11.2x) Starz app usage vs S1
        premiere (THR: "record growth on the premium cable outlet's
        streaming app")
      - Culturally-specific pull (Black female audience, Southern
        milieu) creates targeted signup driver
      - #1 on Starz app at launch
      - S2 launch was "record for a Starz series opener across all
        platforms" (surpassed Power Book IV: Force's 3.3M premiere)

    1.2% conversion of 7M reach → ~84K US signups panel-attributable.

new_share = 0.52
    P-Valley audience skews Black female — heavily under-indexed on
    Starz's historic sub base (which skewed white/older/male via the
    Power franchise + Outlander gravity). Cultural-pull shows on Starz
    tend to drive high new-share signups.

    Offsetting factors:
      - S1 aired 2020, so S1's new-Starz signups (many of whom stayed)
        count as REACTIVATION for S2 launch, not new
      - S2 had 2 years of Starz churn between S1 and S2
      - Word-of-mouth on Meg Thee Stallion S2 cameo + Twitter buzz
        (abortion/homophobia/DV storylines) pulled fresh viewership

    52% new / 48% react — new-signup majority reflects the cultural-
    pull demographic mismatch with Starz base, moderated by the
    reactivation-heavy pattern typical for returning shows.

pre_existing_pct = 0.18
    S1 aired July-September 2020 (~22 months before S2 launch). Some
    S1 fans stayed on Starz through the gap; most churned out. Of the
    S2 30-day audience, ~18% had previously watched P-Valley S1 on
    Starz WITHIN THE PANEL (pipeline can only see post-1/1/2021 panel
    activity, so any S1 rewatch pre-2021 is invisible).

    Note: S1 aired 7/12/2020, before the 1/1/2021 panel cutoff. Only
    S1 REWATCH activity between 1/1/2021 and 6/3/2022 is trackable
    → ~18% of S2 audience has trackable S1-on-Starz history. Real
    S1 overlap is higher (~30-35% by industry norms for premium
    weekly serials) but the untrackable portion is left out per the
    1/1/2021 cutoff rule.

is_new_show = False
    S2 is a returning series (S1 launched 2020).
"""

from __future__ import annotations

import os
import sys
import time
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


DASHBOARD_CAT = "SERIES - STARZ ORIGINAL"


def _ep(date: str, num: int, title: str) -> dict:
    return {
        "episode_num":   num,
        "air_date":      datetime.strptime(date, "%Y-%m-%d"),
        "display_label": f"S2E{num}: {title}",
    }


CONFIG = {
    "project_name":     "P-Valley_-_Season_2",
    "title":            "P-Valley Season 2",
    "platform":         "starz",
    "start":            "2022-06-03",
    "genre":            "Southern Noir Drama",
    "cadence":          "Weekly",
    "is_new":           False,
    "reach_us":         7_000_000,
    "conv_pct":         1.2,
    "new_share":        0.52,
    "pre_existing_pct": 0.18,
    "dashboard_category": DASHBOARD_CAT,
    "episode_dates": [
        _ep("2022-06-03",  1, "Pussyland"),
        _ep("2022-06-12",  2, "Seven Pounds of Pressure"),
        _ep("2022-06-19",  3, "The Dirty Dozen"),
        _ep("2022-06-26",  4, "Demethrius"),
        _ep("2022-07-03",  5, "White Knights"),
        _ep("2022-07-10",  6, "Savage"),
        _ep("2022-07-24",  7, "Jackson"),
        _ep("2022-07-31",  8, "The Death Drop"),
        _ep("2022-08-07",  9, "Snow"),
        _ep("2022-08-14", 10, "Mississippi Rule"),
    ],
    "context_note": (
        "P-Valley Season 2 - Starz weekly serial premiered Friday "
        "6/3/2022 on Starz app + Sunday 6/5/2022 on linear. Created "
        "by Pulitzer Prize winner Katori Hall (based on her play "
        "'Pussy Valley'). Southern-noir drama set at The Pynk, a "
        "strip club in the fictional Chucalissa Mississippi Delta. "
        "Cast: Brandee Evans (Mercedes), Nicco Annan (Uncle Clifford - "
        "GLAAD Award winner), Elarica Johnson (Autumn Night), Shannon "
        "Thornton (Miss Mississippi), J. Alphonse Nicholson (Lil Murda), "
        "Skyler Joy, Parker Sawyers. Meg Thee Stallion S2E4 cameo. "
        "10 one-hour episodes released weekly Fridays 6/3-8/14/2022 "
        "(week of 7/17 skipped for Independence-Day hiatus). Critical "
        "hit: 95% RT critic / 94% audience. S1 (2020) had a 100% RT "
        "rating. \n\n"
        "Starz official PR (10/20/2022 renewal announcement): S2 "
        "averages 10.3M viewers/episode across linear + VOD + "
        "streaming domestically, up +23% vs S1 same-window measure. "
        "S2 premiere cross-platform 3-day cume: 4.5M (243K linear + "
        "rest streaming). S2 launch drove +1,018% (11.2x) Starz app "
        "usage vs S1 premiere - largest app growth in Starz series "
        "history at the time. S2 opener surpassed Power Book IV: Force "
        "(3.3M premiere) to become Starz's biggest cross-platform "
        "series debut. \n\n"
        "reach 7M US 30-day anchor: 10.3M/ep is Starz's lifetime "
        "cross-platform cume per-episode. For SubIQ 30-day-from-"
        "premiere unique reach, we take the union of E1-E5 viewers "
        "within the 30d window (E1 30d ~10M, E2 27d ~8-9M, E3 20d "
        "~7M, E4 13d ~5M, E5 6d ~3M) and de-overlap for the campaign-"
        "attributable slice = ~7M unique US accounts whose Starz "
        "session was P-Valley-directed. Below naive 10.3M/ep because "
        "that number is lifetime late-tail cume; above single-week "
        "cume because binge-catch-up stacks E1-E4. Starz total US "
        "subs Q2 2022: ~14-15M; 7M P-Valley 30d reach = ~48% of the "
        "sub base, consistent with 'biggest show on the platform.' \n\n"
        "conv 1.2% top of Starz niche/premium band (0.8-1.5%). Record "
        "app growth + culturally-specific pull (Black female + "
        "Southern) creates strong signup driver. new_share 52% - "
        "P-Valley audience heavily under-indexed on Starz's historic "
        "Power/Outlander base (older/whiter/male) - cultural-pull "
        "audience delivers new-signup majority. Moderated by "
        "reactivation-heavy pattern typical for weekly serials in "
        "S2 returning cadence. pre_existing 18% - S1 aired 7/12/"
        "2020 (before 1/1/2021 panel cutoff), so pre-existing "
        "captures only S1 REWATCH activity between 1/1/2021 and "
        "6/3/2022. Real S1 overlap likely 30-35% but pre-panel-"
        "cutoff portion is untrackable per pipeline rules. "
        "is_new=False (returning series)."
    ),
}


def build_config(spec: dict) -> dict:
    start   = datetime.strptime(spec["start"], "%Y-%m-%d")
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
        "dashboard_category":        spec["dashboard_category"],
        "output_dir":                "/tmp/svod_synthetic_runs",
        "context_note":              spec["context_note"],
        "reach_us_override":         spec["reach_us"],
        "conversion_pct":            float(spec["conv_pct"]),
        "reactivation_pct_override": max(0.0, min(1.0, 1.0 - float(spec["new_share"]))),
        "pre_existing_pct":          float(spec["pre_existing_pct"]),
    }


def main() -> None:
    print(f"🎬  P-Valley Season 2 (Starz) pull")
    print(f"    reach={CONFIG['reach_us']:,}  conv={CONFIG['conv_pct']}%  new_share={CONFIG['new_share']*100:.0f}%")
    print(f"    pre_existing={CONFIG['pre_existing_pct']*100:.0f}%  window: 6/3/2022 + 30d")
    print()
    r = run_synthetic_attribution(build_config(CONFIG))
    if isinstance(r, dict):
        print(f"\n✅ {r.get('s3_key')}")
        print(f"   reach     = {r.get('reach_us'):>12,}")
        print(f"   signups   = {r.get('new_signups_us'):>12,}")


if __name__ == "__main__":
    main()
