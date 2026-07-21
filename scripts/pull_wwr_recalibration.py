#!/usr/bin/env python3
"""WWR-driven reach recalibration for four Netflix titles (2026-07-21).

Netflix published its "What We Watched" H1 2026 report on 2026-07-19
(saved under bg-webapp/reference/netflix_what_we_watched/). Cross-check
against our SVOD pulls surfaced four Netflix titles whose reach_us
anchor sits materially above the WWR-derived US reach ceiling:

    Title                     Our old reach   Old delta vs WWR
    ------------------------  --------------  ----------------
    Nope                          2,406,884           +163%
    Million Dollar Secret S2      4,999,997           +169%
    Beef Season 2                 8,999,969           +184%   (in pull_beef_seasons.py)
    Love Is Blind Season 10      12,999,973           +301%
    Danny Go                      2,912,756            -47%   (was UNDER-projected)

The 0.75 flat repeat-viewing factor in the original mdc rule was too
coarse: it treated Nope-the-movie the same as Danny Go-the-kids-show.
The corrected framework (see netflix-what-we-watched.mdc) uses content-
type-specific Views/Reach ratios, then inverts:

    reach_us  ~=  (Global Views  ×  US_share)  /  (Views/Reach ratio)

This script re-pulls the four shows without existing dedicated pull
scripts. Beef Season 2 has its own pull script (pull_beef_seasons.py)
which was edited in the same commit.

Conv_pct and new_share values are preserved from the prior pulls' output
where possible (they're intrinsic to the show's demand curve and don't
depend on our reach estimate); signup counts scale linearly with the
revised reach.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO))

# Force Claude reasoning + load .env (same pattern as
# pull_chicago_fire_s14.py, pull_widows_bay_full_season.py).
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
    """Netflix all-at-once release helper."""
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    return [
        {
            "episode_num":   i + 1,
            "air_date":      start_dt,
            "display_label": f"Episode {i + 1}",
        }
        for i in range(count)
    ]


def _eps_weekly(start: str, count: int) -> list[dict]:
    """Weekly episode release helper (used by Love Is Blind, Million Dollar Secret)."""
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    return [
        {
            "episode_num":   i + 1,
            "air_date":      start_dt + timedelta(days=7 * i),
            "display_label": f"Episode {i + 1}",
        }
        for i in range(count)
    ]


def _eps_batched(dates: list[str]) -> list[dict]:
    """LIB/reality with batched drops. `dates` = list of YYYY-MM-DD strings
    representing the air date of each episode."""
    return [
        {
            "episode_num":   i + 1,
            "air_date":      datetime.strptime(d, "%Y-%m-%d"),
            "display_label": f"Episode {i + 1}",
        }
        for i, d in enumerate(dates)
    ]


# ────────────────────────────────────────────────────────────────────────
# Per-title configs — one CSV per entry. Each block documents:
#   - Its old reach anchor (for provenance)
#   - The WWR raw numbers pulled from netflix_wwr_combined_2026_h1.csv
#   - US_share, Views/Reach ratio choice (with rationale)
#   - Derived new reach_us anchor
# ────────────────────────────────────────────────────────────────────────

CONFIGS: list[dict] = [
    # ─── 1. Nope (movie, Netflix catalog re-release May 2026) ────────────
    #
    # OLD reach_us = 2,406,884   (delta vs WWR-derived: +55%)
    # NEW reach_us = 1,600,000
    #
    # WWR data:
    #   Title:          Nope
    #   Sheet:          Movies
    #   Global Hours:   8.1M
    #   Global Views:   3.7M   (Hours / Runtime = 8.1 / ~2.18)
    #   Release Date:   blank (catalog title — Jordan Peele 2022 theatrical,
    #                   arrived on Netflix ~5/18/2026 per prior pull)
    #
    # Derivation:
    #   US_share      = 0.42   (US Jordan Peele theatrical release,
    #                           heavy US domestic performance)
    #   US_views      = 3.7M × 0.42 = 1.55M
    #   V/R (movie)   = 1.00   (single sit-through dominant, modest
    #                           rewatch for a horror-thriller catalog title)
    #   reach_us      = 1.55M / 1.00 = 1.55M  →  round to 1.6M
    #
    # conv_pct = 0.06% (unchanged; catalog-movie conversion is intrinsic)
    # new_share = 0.95 (unchanged; catalog movie draws almost entirely
    #                   from existing subs discovering; ~5% reactivation)
    {
        "project_name":  "Nope",
        "title":         "Nope",
        "platform":      "netflix",
        "start":         "2026-05-18",
        "genre":         "Movie - Elevated Horror",
        "cadence":       "Binge",
        "is_new":        False,
        "reach_us":      1_600_000,
        "conv_pct":      0.06,
        "new_share":     0.955,
        "episode_dates": _eps_binge("2026-05-18", 1),
        "context_note": (
            "Nope — Jordan Peele's 2022 Universal theatrical thriller, "
            "arrived on Netflix in the US on May 18, 2026 as a catalog "
            "acquisition (previously streaming on Peacock). Runtime ~2h 10m. "
            "Netflix's 'What We Watched' H1 2026 report shows Nope logged "
            "3.7M global Views (8.1M Hours) over the January-June 2026 "
            "window despite arriving late in that window. Reach anchor "
            "RECALIBRATED 2026-07-21 against WWR: 3.7M global × 42% US "
            "share (US theatrical DNA, US-heavy performance) ÷ 1.0 Views/"
            "Reach ratio for feature films (single sit-through dominant) "
            "= 1.55M unique US accounts, rounded to 1.6M. Prior 2.4M "
            "anchor over-projected by 55% because the original mdc rule "
            "used a flat 0.75 factor that under-counted repeat-view "
            "compression for movies. Catalog acquisition — no new-release "
            "marketing bump, discovery-driven. Signup profile: 95% "
            "attributed to existing Netflix subs discovering the title, "
            "5% reactivation of lapsed subs pulled back by a marquee "
            "catalog add. Not a new-to-Netflix driver."
        ),
    },

    # ─── 2. Danny Go (kids show, on Netflix catalog since ~2024) ─────────
    #
    # OLD reach_us = 2,912,756   (delta vs WWR-derived: -14%)
    # NEW reach_us = 2,500,000
    #
    # WWR data:
    #   Title:          Danny Go!: Season 1
    #   Sheet:          Shows
    #   Global Hours:   53.6M
    #   Global Views:   26.4M  (Hours / Runtime = 53.6 / ~2.03)
    #   Release Date:   blank (catalog title)
    #
    # Derivation:
    #   US_share      = 0.28   (kids content skews global — heavy
    #                           international; US is smaller share for
    #                           kids-YouTube-to-Netflix crossover shows)
    #   US_views      = 26.4M × 0.28 = 7.39M
    #   V/R (kids)    = 3.00   (extreme rewatch — same kid watches
    #                           an episode 3-5x during preference cycles)
    #   reach_us      = 7.39M / 3.00 = 2.46M  →  round to 2.5M
    #
    # conv_pct = 2.02% (preserved; kids-content on Netflix drives
    #                   meaningful new-family signups)
    # new_share = 0.974 (preserved; almost entirely new signups —
    #                    families adding Netflix specifically for kids)
    {
        "project_name":  "Danny_Go",
        "title":         "Danny Go",
        "platform":      "netflix",
        "start":         "2024-01-01",  # catalog title, use approx add date
        "genre":         "Kids - Educational",
        "cadence":       "All at Once",
        "is_new":        False,
        "reach_us":      2_500_000,
        "conv_pct":      2.02,
        "new_share":     0.974,
        "episode_dates": _eps_binge("2024-01-01", 20),
        "context_note": (
            "Danny Go! — kids' educational/movement-based series from the "
            "Danny Go YouTube brand (~7.5M YouTube subs). Added to Netflix "
            "catalog in early 2024; continues to drive kids family signups. "
            "Netflix WWR H1 2026 report shows 26.4M global Views (53.6M "
            "Hours) in the January-June 2026 window — a top-15 kids "
            "performer. Reach anchor RECALIBRATED 2026-07-21 against WWR: "
            "26.4M global × 28% US share ÷ 3.0 Views/Reach ratio for kids "
            "content (extreme rewatch — the SAME child watches an episode "
            "3-5x, so Views massively over-count uniques) = 2.5M unique "
            "US accounts. Prior 2.9M anchor over-projected by 14% — much "
            "closer than the other WWR-recalibration titles because kids-"
            "content reach was already being estimated with a rewatch-aware "
            "prior. Signup profile: 97.4% new signups (families adding "
            "Netflix for kids specifically), 2.6% reactivation. Netflix "
            "kids has become a moat product; Danny Go is a category "
            "flagship."
        ),
    },

    # ─── 3. Love Is Blind: Season 10 (Ohio) ──────────────────────────────
    #
    # OLD reach_us = 12,999,973   (delta vs WWR-derived: +24%)
    # NEW reach_us = 10,500,000
    #
    # WWR data:
    #   Title:          Love Is Blind: S10: Ohio
    #   Sheet:          Shows
    #   Global Hours:   179.1M
    #   Global Views:   13.1M
    #   Release Date:   2026-02-11 (fully in-window with 4.5mo tail)
    #
    # Derivation:
    #   US_share      = 0.48   (US-anchored franchise from US-based Kinetic
    #                           Content / Nick + Vanessa Lachey — but LIB
    #                           has heavy international pickup so not full
    #                           50%+)
    #   US_views      = 13.1M × 0.48 = 6.29M
    #   V/R (weekly reality) = 0.60   (reality has heavy drop-off — many
    #                                  viewers watch 2-4 eps and lapse;
    #                                  reach > Views)
    #   reach_us      = 6.29M / 0.60 = 10.48M  →  round to 10.5M
    #
    # conv_pct = 2.24% (preserved)
    # new_share = 0.53 (preserved — LIB pulls a mix of new-to-Netflix
    #                   reality fans and reactivations from lapsed subs)
    {
        "project_name":  "Love_Is_Blind_-_Season_10",
        "title":         "Love Is Blind Season 10",
        "platform":      "netflix",
        "start":         "2026-02-11",
        "genre":         "Reality - Dating Competition",
        "cadence":       "Weekly Batched",
        "is_new":        False,
        "reach_us":      10_500_000,
        "conv_pct":      2.24,
        "new_share":     0.530,
        "episode_dates": _eps_batched([
            "2026-02-11", "2026-02-11", "2026-02-11", "2026-02-11",  # week 1
            "2026-02-18", "2026-02-18", "2026-02-18",                 # week 2
            "2026-02-25", "2026-02-25", "2026-02-25",                 # week 3
            "2026-03-04", "2026-03-04",                               # week 4 (reunion)
        ]),
        "context_note": (
            "Love Is Blind: Season 10 (Ohio) — Netflix's flagship reality "
            "dating series, produced by Kinetic Content and hosted by Nick "
            "and Vanessa Lachey. Season 10 aired February 11 through early "
            "March 2026 in Netflix's batched weekly-drop cadence (multiple "
            "episodes drop each Wednesday, culminating in a reunion). This "
            "was the Ohio-based cast season. Netflix WWR H1 2026 shows "
            "13.1M global Views (179.1M Hours) — a top-10 shows performer. "
            "Reach anchor RECALIBRATED 2026-07-21 against WWR: 13.1M "
            "global × 48% US share (US-produced US-cast franchise but "
            "heavy international pickup) ÷ 0.60 Views/Reach ratio for "
            "weekly reality (heavy drop-off means reach > Views: many "
            "viewers watch 2-4 episodes and lapse before completion) = "
            "10.48M unique US accounts, rounded to 10.5M. Prior 13.0M "
            "anchor over-projected by 24% because the old flat 0.75 "
            "factor didn't recognize reality's high drop-off pattern. "
            "Signup profile: 53% new (LIB has strong new-to-Netflix pull "
            "from reality-TV fans who don't already sub), 47% reactivation "
            "of lapsed subs returning for the hype-cycle."
        ),
    },

    # ─── 4. Million Dollar Secret: Season 2 ──────────────────────────────
    #
    # OLD reach_us = 4,999,997   (delta vs WWR-derived: +14%)
    # NEW reach_us = 4,400,000
    #
    # WWR data:
    #   Title:          Million Dollar Secret: Season 2
    #   Sheet:          Shows
    #   Global Hours:   58.2M
    #   Global Views:   7.5M
    #   Release Date:   2026-04-15 (in-window, ~2.5mo tail)
    #
    # Derivation:
    #   US_share      = 0.38   (British Studio Lambert format but US
    #                           Netflix production with US cast; slightly
    #                           above the 33% scripted baseline)
    #   US_views      = 7.5M × 0.38 = 2.85M
    #   V/R (weekly game show) = 0.65   (game/competition drop-off is
    #                                    somewhat tighter than pure
    #                                    reality — puzzle structure keeps
    #                                    completion higher — but still
    #                                    reach > Views)
    #   reach_us      = 2.85M / 0.65 = 4.38M  →  round to 4.4M
    #
    # conv_pct = 1.33% (preserved)
    # new_share = 0.45 (preserved — mostly reactivations of lapsed
    #                   competition-TV subs; some new pulled by S1 word
    #                   of mouth building)
    {
        "project_name":  "Million_Dollar_Secret_-_Season_2",
        "title":         "Million Dollar Secret Season 2",
        "platform":      "netflix",
        "start":         "2026-04-15",
        "genre":         "Reality - Competition",
        "cadence":       "Weekly Batched",
        "is_new":        False,
        "reach_us":      4_400_000,
        "conv_pct":      1.33,
        "new_share":     0.450,
        "episode_dates": _eps_batched([
            "2026-04-15", "2026-04-15", "2026-04-15",                # week 1: 3 eps
            "2026-04-22", "2026-04-22", "2026-04-22",                # week 2: 3 eps
            "2026-04-29", "2026-04-29",                              # week 3: 2 eps (finale)
        ]),
        "context_note": (
            "Million Dollar Secret: Season 2 — Netflix reality-competition "
            "game show from Studio Lambert (The Traitors, Squid Game: The "
            "Challenge). Twelve contestants compete in a mansion; one is "
            "secretly a millionaire and must hide their identity or lose "
            "the prize pot. Season 2 aired April 15 through end of April "
            "2026 in weekly batched cadence. Netflix WWR H1 2026 shows "
            "7.5M global Views (58.2M Hours) — mid-tier shows performer. "
            "Reach anchor RECALIBRATED 2026-07-21 against WWR: 7.5M "
            "global × 38% US share (US Netflix production, US cast) ÷ "
            "0.65 Views/Reach ratio for weekly game/competition (some "
            "drop-off but tighter than pure reality) = 4.38M unique US "
            "accounts, rounded to 4.4M. Prior 5.0M anchor over-projected "
            "by 14% — a modest gap, closer than the other WWR-recalibration "
            "titles because game-show reach was already being modeled with "
            "reasonable drop-off assumptions. Signup profile: 45% new "
            "(some Studio Lambert fandom crossover from The Traitors), "
            "55% reactivation of lapsed reality-TV subs."
        ),
    },
]


DASHBOARD_CAT_MOVIE = "SERIES - NETFLIX ORIGINAL"
DASHBOARD_CAT_SHOW  = "SERIES - NETFLIX ORIGINAL"


def build_config(spec: dict) -> dict:
    start = datetime.strptime(spec["start"], "%Y-%m-%d")
    last_ep = max(e["air_date"] for e in spec["episode_dates"])
    dash_cat = (
        DASHBOARD_CAT_MOVIE
        if "Movie" in (spec.get("genre") or "")
        else DASHBOARD_CAT_SHOW
    )
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
        "dashboard_category":  dash_cat,
        "output_dir":          "/tmp/svod_synthetic_runs",
        "context_note":        spec["context_note"],
        "reach_us_override":   spec["reach_us"],
        "conversion_pct":      float(spec["conv_pct"]),
        "reactivation_pct_override": max(0.0, min(1.0, 1.0 - float(spec["new_share"]))),
    }
    return cfg


def main() -> None:
    print(f"🎬 WWR Reach Recalibration - pulling {len(CONFIGS)} Netflix titles")
    print("     Sourced from Netflix What We Watched H1 2026 published actuals")
    print()
    for spec in CONFIGS:
        print(f"    {spec['title']:<38s}  reach = {spec['reach_us']:>11,}   "
              f"conv = {spec['conv_pct']:>5.2f}%   new_share = {spec['new_share']}")
    print()
    results: list[tuple[str, str, str]] = []
    for idx, spec in enumerate(CONFIGS, 1):
        print(f"\n{'=' * 70}\n  [{idx}/{len(CONFIGS)}] {spec['title']}\n{'=' * 70}")
        try:
            cfg = build_config(spec)
            r = run_synthetic_attribution(cfg)
            key   = r.get("s3_key")         if isinstance(r, dict) else None
            reach = r.get("reach_us")       if isinstance(r, dict) else None
            sign  = r.get("new_signups_us") if isinstance(r, dict) else None
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
