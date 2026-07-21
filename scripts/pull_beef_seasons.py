#!/usr/bin/env python3
"""Pull Beef (Netflix) — Season 1 and Season 2 independently.

Beef is an A24-produced Netflix anthology comedy-drama created by Lee
Sung Jin. Season 1 (2023) starred Steven Yeun and Ali Wong and won
8 Primetime Emmys including Outstanding Limited Series. Season 2
(2026) is an anthology continuation with a completely new cast
(Oscar Isaac, Carey Mulligan, Charles Melton, Cailee Spaeny) and new
story — country-club blackmail-war narrative.

Season structure:
    S1 (April 6, 2023):  10 episodes, all-at-once Netflix binge drop
    S2 (April 16, 2026): 8 episodes,  all-at-once Netflix binge drop

Both premieres are POST-1/1/2021 → fully within our panel tracking
window. No pre-2021 disclaimer needed.

Reach + conversion overrides are grounded in:
    - Antenna Netflix limited-series reach benchmarks (Dahmer, Painkiller,
      Watcher, Painkiller, Griselda tier)
    - Nielsen streaming top-10 (Beef S1 held Netflix Top-10 for 3 weeks
      post-launch; S2 currently in Top-3 as of pull date 7/8/26)
    - Emmy-win halo effects (S1 got a measurable ~+30% post-Emmy bump
      in September 2023 — outside the 30-day analysis window but
      confirms the audience trajectory)
    - Netflix subscriber base growth 2023→2026 (~75M → ~90M US paid subs
      per Antenna Q1'26) — larger addressable base for S2 but higher
      saturation and lower incremental-conversion headroom
    - Anthology-continuation dynamics — S2 leverages S1 brand for
      marketing pull without requiring S1 viewership as a prerequisite;
      draws BOTH lapsed S1 viewers (reactivation) AND new-to-brand
      viewers pulled by Oscar Isaac / Carey Mulligan star power

Each season is pulled INDEPENDENTLY with its own campaign window, reach,
and conversion overrides. S1 is is_new=True (Netflix franchise debut);
S2 is is_new=False (branded returning anthology).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO))

# Force Claude reasoning + load .env for ANTHROPIC_API_KEY (same pattern
# as pull_chicago_fire_s14.py, pull_widows_bay_full_season.py). Using =
# instead of setdefault so an empty env var from the parent shell still
# gets flipped on.
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
    """Netflix all-at-once binge release — all episodes drop day one."""
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    return [
        {
            "episode_num":   i + 1,
            "air_date":      start_dt,
            "display_label": f"Episode {i + 1}",
        }
        for i in range(count)
    ]


def _last_episode_date(episode_dates: list[dict]) -> datetime:
    return max(e["air_date"] for e in episode_dates)


# ── Genre + dashboard bucket ───────────────────────────────────────────
COMEDY_DRAMA_ANTHOLOGY = "Comedy Drama Anthology"
DASHBOARD_CAT          = "SERIES - NETFLIX ANTHOLOGY"


# ──────────────────────────────────────────────────────────────────────
# Per-season configs. Two independent trackers, one CSV each.
# ──────────────────────────────────────────────────────────────────────

CONFIGS: list[dict] = [
    # ─── Season 1 (April 6, 2023) ──────────────────────────────────────
    #
    # ROW-BY-ROW REASONING (per user methodology):
    #
    # reach_us = 8.0M
    #   Anchors: Netflix limited-series 30-day US reach tier for critically
    #   acclaimed but non-viral titles. Comparable 2022-24 series:
    #     - Dahmer (2022, viral):     ~25M 30-day → too high a comp
    #     - Wednesday S1 (2022):      ~40M 30-day → viral tier, wrong bucket
    #     - The Watcher (2022):       ~18M 30-day → mystery-thriller, broader
    #     - Painkiller (2023):        ~7M 30-day  → adjacent tier
    #     - Griselda (2024):          ~10M 30-day → biopic pull higher
    #     - Ripley (2024):            ~5.5M 30-day → prestige-adjacent
    #   Beef sits BETWEEN Painkiller (7M) and Griselda (10M): critical
    #   phenomenon but not high-concept-hook driven. Nielsen Top-10 for
    #   3 weeks post-launch. Anchor: 8M.
    #
    # conv_pct = 1.8%
    #   Netflix Q1'23 had ~75M US paid subs — mature but not saturated.
    #   Emmy-caliber limited-series prestige launch pulls a modest new-sub
    #   layer (SNL-style word-of-mouth). Antenna prestige-limited-series
    #   BB/AA range: 1.2-2.5%. Anchor mid: 1.8% → ~144K new signups.
    #
    # new_share = 0.55
    #   2023-era Netflix acquisition was ~55% new / 45% reactivation
    #   (mature service, more reactivations than new-to-Netflix). For a
    #   prestige-limited series with strong critical buzz, new_share
    #   slightly ABOVE Netflix baseline (some SXSW/festival attention
    #   pulls first-time subs). Anchor: 0.55.
    {
        "project_name":  "Beef_-_Season_1",
        "title":         "Beef Season 1",
        "platform":      "netflix",
        "start":         "2023-04-06",
        "genre":         COMEDY_DRAMA_ANTHOLOGY,
        "cadence":       "All at Once",
        "is_new":        True,
        "reach_us":      8_000_000,
        "conv_pct":      1.8,
        "new_share":     0.55,
        "episode_dates": _eps_binge("2023-04-06", 10),
        "context_note": (
            "Beef Season 1 — Netflix limited-series comedy-drama, 10 "
            "episodes released all-at-once on April 6, 2023. Created by "
            "Lee Sung Jin, produced by A24. Starring Steven Yeun and Ali "
            "Wong as Danny Cho and Amy Lau, two strangers whose road-"
            "rage incident escalates into a prolonged psychological "
            "feud. Premiered at SXSW on March 18, 2023, ahead of full "
            "Netflix release. Went on to win 8 Primetime Emmys including "
            "Outstanding Limited Series, plus Golden Globes, SAG, "
            "Critics Choice, PGA, WGA, and AFI honors. Held Netflix "
            "Top-10 US position for 3 weeks post-launch. Post-Emmy "
            "September 2023 saw a measurable ~30% catalog-viewing bump "
            "but that falls outside the 30-day analysis window. Netflix "
            "US paid subs at launch: ~75M (Antenna Q1'23). Cast: Steven "
            "Yeun, Ali Wong, Joseph Lee, Young Mazino, David Choe, "
            "Patti Yasutake."
        ),
    },

    # ─── Season 2 (April 16, 2026) ─────────────────────────────────────
    #
    # ROW-BY-ROW REASONING (RECALIBRATED 2026-07-21 against Netflix "What
    # We Watched" H1 2026 published actuals):
    #
    # reach_us = 5,100,000  (was 9,000,000, -43%)
    #   Netflix WWR H1 2026 for "BEEF: Season 2":
    #     - Global Hours Viewed: 75.1M
    #     - Global Views:        12.8M  (Hours / Runtime, completion-eq)
    #     - Release:             2026-04-16 (in-window, ~2.5 mo tail)
    #
    #   Derivation using content-type-specific Views/Reach ratio for
    #   prestige binge dramas (see netflix-what-we-watched.mdc):
    #     US_share      = 0.40   (US-Asian cast + US market anchor,
    #                             above the 33% scripted-original baseline
    #                             because Emmy-nom coverage was US-heavy)
    #     US_views      = 12.8M × 0.40 = 5.12M
    #     V/R (prestige) = 1.00   (high completion + modest rewatch)
    #     reach_us      = 5.12M / 1.00 = 5.12M
    #
    #   Anchor: 5.1M. Prior 9.0M anchor was set BEFORE we had Netflix's
    #   published Views data and effectively counted every viewer of the
    #   Emmy-nominations coverage as a probable streamer. WWR shows the
    #   actual completion-equivalent US audience is roughly half that.
    #   Cross-check: 5.1M matches Nielsen streaming top-10 shape for
    #   Beef S2 (peaked ~1.3B minutes viewed in week 2 = ~5-6M uniques).
    #
    # conv_pct = 1.4% (unchanged)
    #   Rate is intrinsic to the show's demand curve; absolute signup
    #   count now scales with the revised reach (~5.1M × 1.4% ≈ 71K
    #   new signups panel-projected, vs the prior 126K).
    #
    # new_share = 0.42 (unchanged)
    #   Anthology-continuation dynamics + Netflix maturity → 42% new /
    #   58% reactivation. Unchanged from prior reasoning.
    {
        "project_name":  "Beef_-_Season_2",
        "title":         "Beef Season 2",
        "platform":      "netflix",
        "start":         "2026-04-16",
        "genre":         COMEDY_DRAMA_ANTHOLOGY,
        "cadence":       "All at Once",
        "is_new":        False,
        "reach_us":      5_100_000,
        "conv_pct":      1.4,
        "new_share":     0.42,
        "episode_dates": _eps_binge("2026-04-16", 8),
        "context_note": (
            "Beef Season 2 — Netflix anthology-limited-series continuation, "
            "8 episodes released all-at-once on April 16, 2026. Created "
            "and showrun by Lee Sung Jin (returning from S1), produced "
            "by A24 with Steven Yeun and Ali Wong as executive producers "
            "(not on-camera). Completely new cast: Oscar Isaac and Carey "
            "Mulligan as billionaire country-club owners Joshua and "
            "Lindsay Martín, Charles Melton and Cailee Spaeny as young "
            "engaged couple Austin and Ashley Miller, Youn Yuh-jung as "
            "Chairwoman Park. New story: blackmail war between two "
            "couples over a viral country-club argument video. Currently "
            "Netflix Top-3 US as of 7/8/26 pull date. Announced with 16 "
            "Emmy nominations for the 2026 ceremony (leading the "
            "limited/anthology category). Released three years and ten "
            "days after S1. Netflix US paid subs at launch: ~90M "
            "(Antenna Q1'26). Reach anchor RECALIBRATED 2026-07-21 "
            "against Netflix's published 'What We Watched' H1 2026 "
            "actuals: Beef S2 registered 12.8M global Views over the "
            "H1 window; at 40% US share and 1.0 Views/Reach ratio for "
            "prestige-binge drama, that resolves to ~5.1M unique US "
            "accounts (down from prior 9.0M anchor set before WWR data "
            "was available). Anthology framing means S1 viewership is "
            "NOT a prerequisite, opening the audience funnel while "
            "leveraging brand recall for marketing pull."
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
    print(f"🥩 Beef — pulling {len(CONFIGS)} seasons independently")
    print()
    results: list[tuple[str, str, str]] = []
    for idx, spec in enumerate(CONFIGS, 1):
        print(f"\n{'=' * 70}\n  [{idx}/{len(CONFIGS)}] {spec['title']}\n{'=' * 70}")
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
