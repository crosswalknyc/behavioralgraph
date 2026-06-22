#!/usr/bin/env python3
"""Pull Blue Crush, The Grinch, and Cat in the Hat on Peacock.

Forces the headline reach to match the sample size from the
corresponding "<TITLE> - Viewers.csv" panel-input files in
s3://dashboard-inputs/. The SVOD pipeline writes
"Total Show Watchers" = reach_us / 32.99 (panel units, where 10M panel
≈ 329.9M US gen pop). To pin the panel value to the viewer file's
"Original Raw Numbers" column, we set `reach_us_override` to the
corresponding "US Gen Pop Projection" — that back-divides cleanly to
the sample size.

Targets (sourced from s3://dashboard-inputs/<TITLE> - Viewers.csv):
    Blue Crush:     sample 21,658     → gen pop 714,549
    The Grinch:     sample 153,179    → gen pop 5,053,394
    Cat in the Hat: sample 1,554,550  → gen pop 51,284,604

All three are Universal Pictures releases pre-2021 (2000–2003). Since
Universal is owned by NBCUniversal (the same parent as Peacock), these
are NATIVE content on Peacock — conversion calibrated to 0.50% (native
catalog default) rather than the licensed-catalog 0.06% used for older
non-platform-owner films.

All three are pre-2021 releases, so the pipeline auto-applies the
pre-2021 panel-cutoff disclaimer:
    Analysis Date Range pinned to 2021-01-01 → 2025-12-31
    Episodes-tab dashboard note: "Episodes tracked were watched after
    the original air date due to availability of data"
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO))

os.environ.setdefault("USE_CLAUDE_REASONING", "1")

from SVOD_Churn_Attribution import run_synthetic_attribution  # noqa: E402


CONFIGS: list[dict] = [
    {
        "project_name":   "Blue_Crush",
        "title":          "Blue Crush",
        "platform":       "peacock",
        "release":        "2002-08-16",
        "genre":          "Movie - Sports Drama",
        "reach_us_override":  714_549,
        "context_note": (
            "Blue Crush (2002) — Universal Pictures theatrical surfing "
            "sports drama, directed by John Stockwell. Kate Bosworth, "
            "Michelle Rodriguez, Sanoe Lake. Streams on Peacock as part "
            "of NBCUniversal's owned-catalog library — i.e. NATIVE "
            "content for SVOD conversion-rate purposes (Universal is "
            "the studio arm of NBCUniversal, which also owns Peacock). "
            "Headline reach pinned to the BLUE CRUSH - Viewers.csv "
            "panel file in s3://dashboard-inputs/ "
            "(sample = 21,658 panel viewers, projecting to 714,549 US "
            "uniques)."
        ),
    },
    {
        "project_name":   "The_Grinch",
        "title":          "The Grinch",
        "platform":       "peacock",
        "release":        "2018-11-09",
        "genre":          "Movie - Holiday Family Animation",
        "reach_us_override":  5_053_394,
        "context_note": (
            "The Grinch (2018) — Illumination animated Universal "
            "Pictures Christmas-season feature. Benedict Cumberbatch, "
            "Rashida Jones. Literal-title match for the "
            "THE GRINCH - Viewers.csv panel file (the 2000 Jim Carrey "
            "live-action movie is officially titled 'How the Grinch "
            "Stole Christmas'). Streams year-round on Peacock with a "
            "predictable Nov/Dec usage spike. NATIVE NBCU catalog "
            "(Universal/Illumination → Peacock). Headline reach pinned "
            "to viewer-panel file: sample = 153,179, projecting to "
            "5,053,394 US uniques."
        ),
    },
    {
        "project_name":   "Cat_in_the_Hat",
        "title":          "Cat in the Hat",
        "platform":       "peacock",
        "release":        "2003-11-21",
        "genre":          "Movie - Family Comedy",
        "reach_us_override":  51_284_604,
        "context_note": (
            "Dr. Seuss' The Cat in the Hat (2003) — live-action "
            "Universal Pictures theatrical family comedy directed by "
            "Bo Welch, starring Mike Myers as the Cat. NATIVE NBCU "
            "catalog title on Peacock. The viewer-panel sample is "
            "unusually large for a 2003 family movie (1.55M panel "
            "viewers in 2025) — consistent with it being a year-round "
            "Peacock kids-tile staple with a Halloween/holiday surge. "
            "Headline reach pinned to viewer-panel file: sample = "
            "1,554,550, projecting to 51,284,604 US uniques."
        ),
    },
]


def build_config(spec: dict) -> dict:
    release_dt = datetime.strptime(spec["release"], "%Y-%m-%d")
    return {
        "project_name":        spec["project_name"],
        "show_search_terms":   [spec["title"]],
        "platform_name":       spec["platform"],
        "campaign_start":      release_dt,
        "campaign_end":        release_dt,
        "exclusion_days":      180,
        "attribution_window":  30,
        "genre":               spec["genre"],
        "content_cadence":     "Binge",
        "is_new_show":         True,
        "episode_dates":       [{
            "episode_num":   1,
            "air_date":      release_dt,
            "display_label": "Movie Release",
        }],
        "reach_us_override":   spec["reach_us_override"],
        "conversion_pct":      0.50,
        "upload_to_s3":        True,
        "s3_bucket":           "svod-acquisition",
        "dashboard_category":  "MOVIES - UNIVERSAL",
        "output_dir":          "/tmp/svod_synthetic_runs",
        "context_note":        spec["context_note"],
    }


def main():
    print(f"🎬 Peacock/Universal movie batch: {len(CONFIGS)} titles")
    print(f"   Reach overridden from corresponding *-Viewers.csv panel files")
    print()
    for idx, spec in enumerate(CONFIGS, 1):
        print(f"\n{'='*70}")
        print(f"  [{idx}/{len(CONFIGS)}] {spec['title']} on {spec['platform']}")
        print(f"      Target panel sample ≈ {spec['reach_us_override'] // 33:,}")
        print(f"      Target gen pop      = {spec['reach_us_override']:,}")
        print('='*70)
        try:
            cfg = build_config(spec)
            r = run_synthetic_attribution(cfg)
            key = r.get('s3_key') if isinstance(r, dict) else None
            reach = r.get('reach_us') if isinstance(r, dict) else None
            sign = r.get('new_signups_us') if isinstance(r, dict) else None
            if key:
                print(f"  ✅ uploaded {key}")
                print(f"     reach_us = {reach:,}  signups_us = {sign:,}")
            else:
                print(f"  ⚠️ unexpected result: {r}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"  ❌ {spec['title']}: {e}")


if __name__ == "__main__":
    main()
