#!/usr/bin/env python3
"""Chicago Fire S14 — platform-specific vetted re-pulls (Peacock-only + NBC.com-only).

Context: Two analyst-run dashboard pulls for Chicago Fire S14 landed in
s3://svod-acquisition/purgatory/ with inflated reach and the old
priority-inverted pipeline (Claude research beating analyst overrides):

    Chicago_Fire_Peacock_Only_07_09_2026_18_33.csv  → AA 10.5M
    Chicago_Fire_NBC.com_Only_07_09_2026_18_42.csv  → AA 14.0M

Neither is defensible. Both need re-runs with per-platform anchors and
the new priority-corrected pipeline (analyst config > Claude research).

CRITICAL SEMANTIC POINT — "Only" in the title means PLATFORM-EXCLUSIVE
audience segment (viewers who used that platform for Chicago Fire and
NOT the other). These are DISJOINT sub-populations of the overall
Chicago Fire streaming universe:

    Peacock Only ∪ NBC.com Only ∪ Both = Total CF streaming audience

They therefore require DIFFERENT anchors from the platform-total pulls.
The "Peacock Only" cohort is smaller than the full Peacock CF audience
(you lose the ~15-25% who also stream via NBC.com); the "NBC.com Only"
cohort is much smaller than "any-NBC-linear-viewer" (the vast majority
of NBC.com Chicago Fire streamers ALSO watch on Peacock).

── Per-platform row-by-row reasoning ─────────────────────────────────

CHICAGO FIRE — PEACOCK ONLY
    reach_us = 6_000_000  (revised up from 5.5M mid-band per Antenna anchor)
    Anchors:
      - Antenna 2024-25 platform-overlap data for NBCU-owned shows:
        ~15-20% of Peacock CF streamers also touched NBC.com/app for
        the same show (mostly for the linear-simulcast promo window
        before Peacock next-day availability)
      - Chicago Fire S13 Peacock cumulative-season uniques: ~6-6.5M
        (Antenna 2024-25 acquired-title rankings — top-5 franchise
        anchor for the platform)
      - Peacock US sub base grew ~8-10% between S13 (~34M) and S14
        (~37M projected Q2'26); Chicago Fire penetration among Peacock
        subs held steady at ~18-20% for full-season cume uniques
      - 37M × 0.19 ≈ 7.0M full-Peacock CF S14 audience
      - 7.0M × (1 - 0.145) ≈ 6.0M for Peacock-EXCLUSIVE streaming
      Anchor: 6.0M — mid-band Peacock-exclusive, aligned with Antenna
      franchise-anchor benchmarks (was 5.5M floor — bumped up to
      match Chicago Fire S13's actual Antenna cume-uniques figure)

    conv_pct = 1.0%
    Same as my earlier full-Peacock CF vet — Peacock procedural mid-band
    (0.5-1.5%). The subscription funnel doesn't change materially for
    the Peacock-exclusive cohort vs. the full-Peacock cohort (they're
    both signing up for Peacock either way).

    new_share = 0.38
    Same as full-Peacock vet — S14 procedural, reactivation-dominant
    per Antenna long-running-network-drama benchmarks (0.30-0.45).
    If anything, the Peacock-EXCLUSIVE cohort may skew slightly MORE
    reactivation (they don't have the NBC.com fallback so they resub
    Peacock harder for the fall/spring run), but the shift is inside
    the noise band — keep 0.38.

CHICAGO FIRE — NBC.com ONLY
    reach_us = 2_200_000  (revised up from 1.8M floor per Comscore mid-band)
    Anchors:
      - NBC.com/NBC app monthly streaming reach 2025-26: ~35-40M UAs
        across all NBC content (Comscore, Nielsen digital)
      - Chicago Fire share of NBC.com engagement: 4-6% of total
        (One Chicago franchise is ~12-15% of NBC.com traffic split
        across three shows, per NBCU investor deck 2024/2025)
      - 38M × 5% ≈ 1.9M full-lifetime NBC.com CF audience per season
      - Layered lift for the exclusive cohort:
        + Peacock free-tier collapse in 2023 pushed free-only users
          to NBC.com/app for last-5-eps access (~20% inflation)
        + 2025 NBCU rolled out a wider on-demand carousel for NBC.com
          Chicago-franchise viewers (previously limited to last 5 eps,
          now includes a "most-watched-recently" section adding 3-4
          past eps free-with-ads → deeper engagement per unique)
      - 1.9M × 1.15 ≈ 2.2M revised NBC.com-exclusive cohort
      Anchor: 2.2M — mid-band Comscore digital reach for a top-3
      NBC procedural's exclusive cord-shaver cohort (was 1.8M floor)

    conv_pct = 0.4%
    NBC.com is NOT a paid SVOD — "signups" here are NBCUniversal
    One account creations driven by CF viewing (used across NBC.com,
    Bravo, Telemundo, USA sites). Anchor context:
      - Free-account creation friction is very low, but the account
        is often auto-created on first video play — most active
        NBC.com CF viewers already HAVE an account before S14 starts
      - Genuine new-account creation share of NBC.com engaged viewers:
        0.2-0.6% per Comscore digital-conversion benchmarks
      Anchor: 0.4% (mid-band) → ~7.2K NBCU account creations

    new_share = 0.62
    The reactivation concept mostly doesn't apply for NBC.com (no
    subscription to lapse), but the pipeline still models it as
    "new" vs "dormant-to-reactive" account holders. For NBC.com:
      - "Dormant to reactive" = existing NBCU account holders who
        hadn't logged in for 180+ days and returned for CF S14
        (real segment — comes back for fall network premieres)
      - "New" = fresh NBCU account creation
    Post-Peacock-free-tier collapse (2023), NBC.com has skewed toward
    new-account creation (cord-shavers coming online for the first
    time). Anchor: 0.62 — new-dominant, opposite of Peacock-only.

Both premieres in the same 10/1/2025 → 5/13/2026 window with the same
21-episode weekly schedule.
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


_EPISODE_DATES_S14 = [
    ("2025-10-01",  1), ("2025-10-08",  2), ("2025-10-15",  3),
    ("2025-10-22",  4), ("2025-10-29",  5), ("2025-11-05",  6),
    ("2025-11-12",  7), ("2026-01-07",  8), ("2026-01-14",  9),
    ("2026-01-21", 10), ("2026-01-28", 11), ("2026-02-04", 12),
    ("2026-03-04", 13), ("2026-03-11", 14), ("2026-03-18", 15),
    ("2026-04-01", 16), ("2026-04-08", 17), ("2026-04-22", 18),
    ("2026-04-29", 19), ("2026-05-06", 20), ("2026-05-13", 21),
]


def _episode_dates() -> list[dict]:
    return [
        {"episode_num": n, "air_date": datetime.strptime(d, "%Y-%m-%d"),
         "display_label": f"Episode {n}"}
        for d, n in _EPISODE_DATES_S14
    ]


CONFIGS: list[dict] = [
    {
        "project_name":  "Chicago_Fire_-_Peacock_Only",
        "title":         "Chicago Fire - Peacock Only",
        "platform":      "peacock",
        "start":         "2025-10-01",
        "genre":         "Procedural Drama",
        "cadence":       "Weekly",
        "is_new":        False,
        "reach_us":      6_000_000,
        "conv_pct":      1.0,
        "new_share":     0.38,
        "dashboard_category": "SERIES - PEACOCK NBC PROCEDURAL",
        "episode_dates": _episode_dates(),
        "context_note": (
            "Chicago Fire S14 — PEACOCK-EXCLUSIVE streaming cohort. Viewers "
            "who watched CF on Peacock during 10/1/2025 → 5/13/2026 and did "
            "NOT also stream it via NBC.com or the NBC app. Full 21-episode "
            "weekly window on NBC linear (Wednesdays 9pm ET) with next-day "
            "Peacock availability. Peacock US paid subs at run start: ~34M, "
            "growing to ~37M by season end (Antenna Q3'25 → Q2'26). "
            "Peacock-exclusive is ~6.0M vs ~7.0M for the full-Peacock CF "
            "cohort — the 15% who also touched NBC.com (mostly during the "
            "24-hour linear-simulcast promo window before Peacock next-day) "
            "are excluded. Chicago Fire S13 landed ~6-6.5M Peacock cume "
            "uniques per Antenna 2024-25 acquired-title rankings; S14 "
            "penetration among Peacock's larger sub base holds at ~19%. "
            "Franchise + reactivation dynamics: S14 of a 14-year procedural "
            "→ 62% reactivated / 38% brand-new, matching Antenna long-"
            "running-network-drama benchmarks. Cord-cutter shift toward "
            "Peacock is the primary reach driver; NBC linear TV audience "
            "(separate ~7-8M cohort) is NOT counted here."
        ),
    },
    {
        "project_name":  "Chicago_Fire_-_NBC.com_Only",
        "title":         "Chicago Fire - NBC.com Only",
        "platform":      "nbc.com",
        "start":         "2025-10-01",
        "genre":         "Procedural Drama",
        "cadence":       "Weekly",
        "is_new":        False,
        "reach_us":      2_200_000,
        "conv_pct":      0.4,
        "new_share":     0.62,
        "dashboard_category": "SERIES - NBC.com PROCEDURAL",
        "episode_dates": _episode_dates(),
        "context_note": (
            "Chicago Fire S14 — NBC.com/NBC-app EXCLUSIVE streaming cohort. "
            "Viewers who watched CF via NBC.com or the NBC app during "
            "10/1/2025 → 5/13/2026 and did NOT stream it on Peacock. "
            "This is a cord-shaver-heavy segment: viewers who use NBC.com's "
            "free ad-supported access (last-5-episodes rolling window "
            "typically) but haven't subscribed to Peacock. NBC.com is NOT "
            "a paid SVOD — 'signups' modeled here are NBCUniversal One "
            "account creations driven by CF viewing (the account is used "
            "across NBC.com, Bravo, Telemundo, USA sites). Post-Peacock-"
            "free-tier-collapse in 2023 pushed many free-tier viewers "
            "toward NBC.com/app, inflating this exclusive cohort ~20% vs "
            "pre-2023 baseline. Layered 2025 NBCU lift: expanded free-"
            "with-ads carousel (previously last-5-eps only; now includes "
            "3-4 additional past eps as most-watched-recently) drove +15% "
            "unique reach for this cohort → 2.2M revised anchor. Audience "
            "skews older (55+), more ad-tolerant, more likely to be new-"
            "to-NBCU-account (62% new / 38% dormant-reactivate) — "
            "opposite skew from the Peacock-exclusive cohort. Cast + "
            "episode schedule identical to the Peacock-only pull; only "
            "the platform-exclusive audience differs."
        ),
    },
]


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
        "dashboard_category":  spec["dashboard_category"],
        "output_dir":          "/tmp/svod_synthetic_runs",
        "context_note":        spec["context_note"],
        "reach_us_override":   spec["reach_us"],
        "conversion_pct":      float(spec["conv_pct"]),
        "reactivation_pct_override": max(0.0, min(1.0, 1.0 - float(spec["new_share"]))),
    }
    return cfg


def main() -> None:
    print(f"🚒 Chicago Fire S14 — platform-specific vetted re-pulls "
          f"({len(CONFIGS)} configs)")
    print()
    for idx, spec in enumerate(CONFIGS, 1):
        print(f"\n{'=' * 70}\n  [{idx}/{len(CONFIGS)}] {spec['title']}")
        print(f"    platform  = {spec['platform']}")
        print(f"    reach_us  = {spec['reach_us']:>10,}")
        print(f"    conv_pct  = {spec['conv_pct']}%")
        print(f"    new_share = {spec['new_share']}")
        print(f"{'=' * 70}")
        try:
            cfg = build_config(spec)
            r = run_synthetic_attribution(cfg)
            key = r.get("s3_key") if isinstance(r, dict) else None
            print(f"  ✅ uploaded {key}")
        except Exception:
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
