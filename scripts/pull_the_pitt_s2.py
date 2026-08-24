#!/usr/bin/env python3
"""Pull The Pitt Season 2 (HBO Max) — full-season measurement pull.

The Pitt is HBO Max's breakout real-time medical drama from R. Scott
Gemmill and John Wells, starring Noah Wyle. Each season plays out as a
single 15-hour ER shift, one hour per episode. Season 1 (Jan-Apr 2025)
became an awards-season phenomenon, sweeping the Sept 2025 Emmys
(Outstanding Drama Series, Lead Actor for Wyle). Season 2 is set over
Fourth of July weekend, 10 months after S1.

Season history:
    S1: Thu 01/09/2025 — 15 episodes, weekly (finale 04/10/2025)
    S2: Thu 01/08/2026 — 15 episodes, weekly (finale 04/16/2026)  <- this pull
    S3: renewed, premieres January 2027

Pull timing:
    Finale dropped 04/16/2026; the 30-day attribution window closed
    05/16/2026. Pull executed 08/24/2026 — funnel is 100% mature, so
    analyst anchors are final-state season figures, not projections.

Row-by-row anchor reasoning:

reach_us = 19,400,000
    WBD official (Deadline, Apr 2026): Season 2 averaged 15.4M US
    viewers PER EPISODE, up 50% over Season 1 in the same timeframe;
    the finale drew a series-high 9.7M US viewers in its first
    weekend alone. The Pitt became the sixth current HBO Max series
    to clear 15M domestic viewers (after House of the Dragon, The
    White Lotus, A Knight of the Seven Kingdoms, The Last of Us, and
    IT: Welcome to Derry). Nielsen had S2 above 1B minutes viewed per
    week during its final stretch — the #1 title in the country,
    ahead of The Boys' final season — and the show logged 150+ days
    in the FlixPatrol US top 10.
    Unique unduplicated season reach exceeds the best per-episode
    figure. The Pitt's real-time serialized format produces
    exceptional week-to-week retention (each episode is one hour of
    the same shift — very few casual single-episode samplers), so the
    unique-to-average multiplier sits LOW for a weekly drama: ~1.26x
    vs the 1.4-1.6x typical of episodic procedurals. 15.4M x 1.26 =
    ~19.4M unique US viewers across the Jan 8 - May 16 window.
    Cross-check: ~19.4M is ~33% of WBD's ~58M US HBO Max base —
    consistent with top-title penetration for a platform's #1 show
    of the year (House of the Dragon S2 hit ~30-35%). Anchor: 19.4M.

conv_pct = 2.8%
    HBO Max hit-drama signup-driver reads (Antenna-style BB/AA):
      House of the Dragon S1 2022:  ~4.5%   event-level franchise launch
      The Last of Us S1 2023:       ~4.3%   zeitgeist breakout
      The Last of Us S2 2025:       ~3.2%   sophomore return
      White Lotus S3 2025:          ~2.7%   third-season prestige return
      The Pitt S1 2025:             ~3.4%   breakout discovery (low base)
    The Pitt S2 anchors BELOW its own S1 read because the Sept 2025
    Emmy sweep plus the Dec 2025 TNT linear run already pulled forward
    the biggest catch-up signup wave months before the S2 premiere —
    the most-motivated non-subscribers converted in fall 2025, not
    January 2026. Working against that: the 15-week weekly runway
    gives signups more time to accrue than a binge drop, and +50%
    audience growth means genuinely new demand kept arriving all
    season. Net: 2.8% — sophomore-season band, above White Lotus S3,
    below The Last of Us S2. Anchor: 2.8%.

new_share = 0.38
    HBO Max 2026 mature-service baseline: ~35-40% new / 60-65%
    reactivation. Two Pitt-specific forces:
      - Rebrand history (HBO Now -> HBO Max -> Max -> HBO Max) means
        an unusually deep pool of lapsed accounts; a January prestige
        event is the classic churn-back trigger for subscribers who
        dropped after House of the Dragon / White Lotus runs ended
      - BUT S2 viewership grew +50% over S1 — a meaningful share of
        the S2 audience is genuinely new to the franchise and some of
        that inflow is new to the platform entirely (word-of-mouth +
        Emmy halo reaching people who never had any HBO product)
    The growth story pushes new_share ABOVE a fatigued-franchise read
    (Bear S5 at 0.32) but the lapsed-account depth keeps it below
    coin-flip. Anchor: 0.38 new / 0.62 reactivation.

pre_existing_pct = 0.60
    One prior season, but a massively watched one: S1 averaged ~10M
    US viewers per episode by its April 2025 finale and grew to 18M
    global per episode after the Emmy sweep. The fall-2025 catch-up
    wave plus the December TNT linear airings mean most of the S2
    audience had already seen S1 by premiere night.
    Estimation:
      - S1 continuity viewers (watched S1 in 2025, returned for S2):
        ~52% of S2 audience
      - Emmy/TNT-wave catch-up viewers (watched S1 between Sept 2025
        and the Jan 8 premiere): ~8% of S2 audience
      - Genuinely new-to-franchise (never watched S1 — drawn in by
        S2 word-of-mouth, the #1-show press cycle, or the July 4th
        premise): ~40% of S2 audience -> the clean sample
    Pre-existing = 60%. Lower than a 5th-season franchise (Bear S5
    at 0.70) because +50% audience growth mathematically requires a
    large never-watched-S1 inflow. All S1 viewing is post-1/1/2021,
    so the full pre-existing cohort is trackable. Anchor: 0.60.

Expected panel-level output (analyst-modeled):
    Total Show Watchers (AA)    = ~19,400,000 US GP projection
    Pre-Existing                = 19,400,000 x 0.60 = ~11,640,000 US
    Clean Sample                = 19,400,000 x 0.40 = ~7,760,000 US
    New Platform Signups        = 7,760,000 x 2.8%  = ~217,300 US total
    Split:  ~82,600 new-to-HBO-Max  |  ~134,700 reactivations
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
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


def _eps_weekly(start: str, count: int) -> list[dict]:
    """Weekly Thursday drops — one episode every 7 days."""
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    return [
        {
            "episode_num":   i + 1,
            "air_date":      start_dt + timedelta(days=7 * i),
            "display_label": f"Episode {i + 1}",
        }
        for i in range(count)
    ]


GENRE          = "Medical Drama"
DASHBOARD_CAT  = "SERIES - HBO MAX ORIGINAL"


CONFIG: dict = {
    "project_name":     "The_Pitt_-_Season_2",
    "title":            "The Pitt Season 2",
    "platform":         "hbo max",
    "start":            "2026-01-08",
    "genre":            GENRE,
    "cadence":          "Weekly",
    "is_new":           False,
    "reach_us":         19_400_000,
    "conv_pct":         2.8,
    "new_share":        0.38,
    "pre_existing_pct": 0.60,
    "episode_dates":    _eps_weekly("2026-01-08", 15),
    "context_note": (
        "The Pitt Season 2 — HBO Max original real-time medical drama "
        "from R. Scott Gemmill and John Wells, starring Noah Wyle as "
        "Dr. Robby. 15 episodes released weekly on Thursdays from "
        "01/08/2026 through the 04/16/2026 finale; each episode is one "
        "hour of a single 15-hour ER shift set over Fourth of July "
        "weekend, 10 months after Season 1. Pull executed 08/24/2026, "
        "well after the 30-day attribution window closed 05/16/2026 — "
        "funnel is fully mature. "
        "Franchise stage: SOPHOMORE SEASON of the biggest breakout on "
        "the platform. Season 1 (Jan-Apr 2025) swept the Sept 2025 "
        "Emmys (Outstanding Drama Series, Lead Actor for Wyle) and ran "
        "on TNT linear in Dec 2025 as a promotional on-ramp. Season 2 "
        "averaged 15.4M US viewers per episode per WBD — up 50% over "
        "S1 — with the finale drawing a series-high 9.7M US viewers in "
        "its first weekend; Nielsen had it above 1B minutes viewed per "
        "week during the final stretch, the #1 title in the country. "
        "Audience is ~60% returning S1 viewers / ~40% new to the "
        "franchise (the +50% growth requires a large never-watched-S1 "
        "inflow). Acquisition split is reactivation-heavy (62% react / "
        "38% new) — HBO Max's rebrand history leaves a deep lapsed-"
        "account pool that churns back for January prestige events, "
        "while the Emmy catch-up wave already converted many of the "
        "most-motivated new subscribers in fall 2025, months before "
        "the S2 premiere. HBO Max US base at launch: ~58M domestic. "
        "Completion-rate expectation: high 70s to low 80s — the "
        "real-time one-shift format produces exceptional serialized "
        "retention (viewers who clear the premiere almost always ride "
        "to the finale; very few casual mid-season samplers). Second-"
        "screen activity: moderate — older-skewing prestige-drama "
        "audience, but strong live-discourse pull on premiere nights "
        "(Reddit episode threads, X, medical-professional TikTok "
        "reaction content), so low-to-mid 30s is typical."
    ),
}


def build_config(spec: dict) -> dict:
    start = datetime.strptime(spec["start"], "%Y-%m-%d")
    last_ep = max(e["air_date"] for e in spec["episode_dates"])
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
        "reach_us_override":   spec["reach_us"],
        "conversion_pct":      float(spec["conv_pct"]),
        "reactivation_pct_override":
            max(0.0, min(1.0, 1.0 - float(spec["new_share"]))),
        # Pipeline reads config['pre_existing_pct'] directly. No cap is
        # applied when set via config; only research-derived
        # pre_existing_pct is capped at 0.65.
        "pre_existing_pct": float(spec["pre_existing_pct"]),
    }
    return cfg


def main() -> None:
    print("🏥 The Pitt Season 2 — full-season measurement pull")
    print(f"    reach_us     = {CONFIG['reach_us']:>11,}")
    print(f"    conv_pct     = {CONFIG['conv_pct']}%")
    print(f"    new_share    = {CONFIG['new_share']}  (react = {1 - CONFIG['new_share']:.2f})")
    print(f"    pre_existing = {CONFIG['pre_existing_pct']:.2f}")
    print(f"    is_new       = {CONFIG['is_new']}")
    print(f"    episodes     = {len(CONFIG['episode_dates'])} weekly from "
          f"{CONFIG['episode_dates'][0]['air_date'].date()} to "
          f"{CONFIG['episode_dates'][-1]['air_date'].date()}")
    print()
    cfg = build_config(CONFIG)
    r = run_synthetic_attribution(cfg)
    key = r.get("s3_key") if isinstance(r, dict) else None
    reach = r.get("reach_us") if isinstance(r, dict) else None
    sign = r.get("new_signups_us") if isinstance(r, dict) else None
    print(f"\n✅ uploaded  s3_key={key}")
    print(f"   reach_us={reach}  new_signups_us={sign}")


if __name__ == "__main__":
    main()
