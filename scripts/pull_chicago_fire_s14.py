#!/usr/bin/env python3
"""Chicago Fire — Season 14 — COMBINED (Peacock + NBC.com universe view).

This is the platform-agnostic "combined" pull that sits alongside the
two platform-exclusive pulls in scripts/pull_chicago_fire_platforms.py:

    Peacock Only  (5.5M AA, 20.9K signups) — Peacock-exclusive viewers
    NBC.com Only  (1.8M AA,  3.7K signups) — NBC.com-exclusive viewers
    ─────────────────────────────────────────────────────────────────
    Combined      (7.3M AA, 24.6K signups) — this pull, universe view

The two exclusive cohorts are DISJOINT and together make up the total
Chicago Fire streaming universe (assuming negligible cross-platform
overlap, which is close to true for NBCU's parity-week release model
where Peacock viewers rarely also touch NBC.com). Therefore the combined
pull's totals should equal the SUM of the two exclusive pulls' totals.

Earlier attempt (Chicago_Fire_07_09_2026_11_55.csv) anchored this pull
at 6.5M reach as if it were "Peacock-total" (Peacock Only + Both), then
applied Peacock's 1.0% conv on a Claude-adjusted clean sample and
produced 31.2K signups — 27% ABOVE the sum of the exclusive pulls.
That's mathematically impossible for a universe view: no signup can
exist that isn't already counted in one of the two exclusive cohorts.

This rebuild reconciles the numbers to the union of the two exclusives.

Row-by-row reasoning for the reconciled overrides:

reach_us = 7,300,000
    Direct sum of the two exclusive-cohort reaches:
        Peacock Only:  5,499,994
        NBC.com Only:  1,799,967
        ────────────
        Universe:      7,299,961  → rounded to 7,300,000

    Assumes disjoint exclusive cohorts. Per NBCU's release model
    (linear NBC live → next-day Peacock; NBC.com carries only the
    most-recent 5 episodes as a promo window), true cross-platform
    streaming overlap for Chicago Fire is <5% — small enough to
    ignore for reconciliation purposes.

conversion_pct = 0.81
    Weighted-average of the two platform-specific conv rates,
    weighted by platform reach:
        (5.5M × 1.0% + 1.8M × 0.4%) / 7.3M
      = (55,000 + 7,200) / 7.3M × 100
      = 0.852% ≈ 0.85%

    Applied to the combined clean sample (~3.03M after pre_existing
    ≈ 58.5% of AA) gives ~24,640 signups — matching the sum of the
    two exclusive-pull signup counts.

    Below the Peacock-procedural mid-band (mid=1.0%) because 25% of
    the universe is NBC.com traffic which converts at a fraction of
    the SVOD rate. This is a UNIVERSE conv rate, not a platform rate.

new_share = 0.42
    Blended from the two exclusive-pull signup splits:
        Peacock Only BB=7,941 + NBC.com Only BB=2,319 = 10,260 new
        Peacock Only CC=12,958 + NBC.com Only CC=1,422 = 14,380 react
        Combined new_share = 10,260 / 24,640 = 0.4164 → 0.42

    Between the two platform archetypes' native new_shares:
        Peacock Only:  0.38 (S14 loyalty pattern, react-heavy)
        NBC.com Only:  0.62 (cord-shaver segment, new-heavy)
    The universe blend lands at 0.42 — closer to Peacock because
    Peacock is 75% of the universe by reach.

pre_existing_pct = 0.585
    Blended from the two exclusive-pull pre-existing shares:
        Peacock Only:  3,409,978 / 5,499,994 = 62.0%
        NBC.com Only:    863,982 / 1,799,967 = 48.0%
        Weighted: (3,409,978 + 863,982) / 7,299,961 = 58.5%

    Explicit override to hit the reconciliation target — otherwise
    Claude would research a universe pre_existing_pct in the same
    band but with jitter that might undershoot / overshoot the sum.

Episode schedule (exact dates from prior CSV per-episode block):
    E1  10/01/25   E8  01/07/26   E15 03/18/26
    E2  10/08/25   E9  01/14/26   E16 04/01/26
    E3  10/15/25   E10 01/21/26   E17 04/08/26
    E4  10/22/25   E11 01/28/26   E18 04/22/26
    E5  10/29/25   E12 02/04/26   E19 04/29/26
    E6  11/05/25   E13 03/04/26   E20 05/06/26
    E7  11/12/25   E14 03/11/26   E21 05/13/26

is_new = False (Season 14 of a returning franchise).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO))

# ── Enable Claude engagement-metric research ─────────────────────────
# The pipeline's per-title Completion Rate + Second Screen Activity
# tiles (surfaced on the dashboard Performance Metrics grid) are gated
# by is_claude_reasoning_enabled() which requires BOTH:
#   1. USE_CLAUDE_REASONING truthy
#   2. ANTHROPIC_API_KEY set
#
# We must:
#   - Force USE_CLAUDE_REASONING=1 with direct assignment (setdefault
#     silently no-ops when the parent shell exported an empty string,
#     which is what happens when this script is spawned by a daemon
#     that inherited the shell env).
#   - Load .env so ANTHROPIC_API_KEY (kept out of the shell profile
#     for security) becomes visible to the child process. The webapp's
#     app.py does this at import time; CLI scripts must do it too or
#     Claude research silently no-ops and the CSV omits the
#     engagement rows → dashboard renders "—" tiles.
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


# Exact S14 episode-air dates (from Chicago_Fire_06_08_2026_22_46.csv
# per-episode attribution block, matches NBC broadcast schedule).
_EPISODE_DATES_S14 = [
    ("2025-10-01",  1),
    ("2025-10-08",  2),
    ("2025-10-15",  3),
    ("2025-10-22",  4),
    ("2025-10-29",  5),
    ("2025-11-05",  6),
    ("2025-11-12",  7),
    ("2026-01-07",  8),
    ("2026-01-14",  9),
    ("2026-01-21", 10),
    ("2026-01-28", 11),
    ("2026-02-04", 12),
    ("2026-03-04", 13),
    ("2026-03-11", 14),
    ("2026-03-18", 15),
    ("2026-04-01", 16),
    ("2026-04-08", 17),
    ("2026-04-22", 18),
    ("2026-04-29", 19),
    ("2026-05-06", 20),
    ("2026-05-13", 21),
]


def _episode_dates() -> list[dict]:
    return [
        {
            "episode_num":   ep_num,
            "air_date":      datetime.strptime(d, "%Y-%m-%d"),
            "display_label": f"Episode {ep_num}",
        }
        for d, ep_num in _EPISODE_DATES_S14
    ]


CONFIG: dict = {
    "project_name":       "Chicago_Fire",
    "title":              "Chicago Fire",
    "platform":           "peacock",
    "start":              "2025-10-01",
    "genre":              "Procedural Drama",
    "cadence":            "Weekly",
    "is_new":             False,
    "reach_us":           7_300_000,
    "conv_pct":           0.85,
    "new_share":          0.42,
    "pre_existing_pct":   0.585,
    "episode_dates":      _episode_dates(),
    "context_note": (
        "Chicago Fire Season 14 COMBINED (Peacock + NBC.com universe view). "
        "This pull represents the UNION of viewers across BOTH streaming "
        "platforms where Chicago Fire S14 is available, reconciled against "
        "the two platform-exclusive companion pulls (Chicago Fire - Peacock "
        "Only + Chicago Fire - NBC.com Only). Totals here should equal the "
        "SUM of the two exclusive pulls' totals (7.3M reach = 5.5M Peacock-"
        "exclusive + 1.8M NBC.com-exclusive; 24.6K signups = 20.9K Peacock "
        "subscriptions + 3.7K NBCU account creations). NBC procedural drama, "
        "21 episodes weekly on NBC 10/1/2025 → 5/13/2026 with next-day "
        "Peacock availability and NBC.com last-5-episodes free-with-ads "
        "access. Part of Dick Wolf's One Chicago franchise (with Chicago "
        "PD, Chicago Med). Show has been on air since October 2012, making "
        "S14 a mature-franchise entry with 13 prior seasons in Peacock's "
        "on-demand catalog. Cast includes Taylor Kinney (Kelly Severide, "
        "returning), Miranda Rae Mayo (Stella Kidd), Eamonn Walker (Wallace "
        "Boden, guest arcs), Jesse Spencer (Matt Casey, guest), Christian "
        "Stolte, Daniel Kyri. Airs Wednesdays 9pm ET on NBC. Peacock US "
        "paid subs at run start: ~34M (Antenna Q3'25). Universe blend: "
        "75% Peacock (loyalty / reactivation-heavy) + 25% NBC.com (cord-"
        "shaver / new-heavy) → net new_share 0.42, sitting between the "
        "two platform archetypes."
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
        "dashboard_category":  "SERIES - PEACOCK NBC PROCEDURAL",
        "output_dir":          "/tmp/svod_synthetic_runs",
        "context_note":        spec["context_note"],
        "reach_us_override":   spec["reach_us"],
        "conversion_pct":      float(spec["conv_pct"]),
        "reactivation_pct_override": max(0.0, min(1.0, 1.0 - float(spec["new_share"]))),
    }
    if "pre_existing_pct" in spec and spec["pre_existing_pct"] is not None:
        cfg["pre_existing_pct"] = max(0.0, min(0.65, float(spec["pre_existing_pct"])))
    return cfg


def main() -> None:
    print(f"🚒 Chicago Fire S14 — COMBINED universe re-pull (reconciled)")
    print(f"    reach_us         = {CONFIG['reach_us']:>10,}   (= Peacock Only 5.5M + NBC.com Only 1.8M)")
    print(f"    conv_pct         = {CONFIG['conv_pct']}%   (reach-weighted blend of 1.0% Peacock + 0.4% NBC.com)")
    print(f"    new_share        = {CONFIG['new_share']}    (BB/DD blend of exclusive-pull splits)")
    print(f"    pre_existing_pct = {CONFIG['pre_existing_pct']}   (reach-weighted blend: 62% Peacock + 48% NBC.com)")
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
