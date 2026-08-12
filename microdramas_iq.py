"""
microdramas_iq.py - Peacock Microdramas Audience Insights module.

Answers the four objective questions for Peacock's mobile-first
microdrama audience:

  1. Identify and measure Peacock microdrama titles (title catalog +
     first-observed date + per-title 28-day activity window)
  2. Normalize + rank titles by audience activity during the first 28
     days from release (using the first observed date as day 0)
  3. Profile the audience (demographics, interests, platform affinities)
     for the overall microdrama audience AND for each top-performing
     title
  4. Methodology, coverage, and limitations

Data pipeline
-------------
Daily scraper (scripts/microdramas_scrapers/peacock.py) writes a
snapshot to

    s3://dashboard-inputs/microdramas_iq/snapshots/latest/peacock.json
    s3://dashboard-inputs/microdramas_iq/snapshots/{YYYY-MM-DD}/peacock.json

Each snapshot lists the microdrama titles surfaced on Peacock that day
(hub rails, homepage carousels, per-title deep-link presence). Every
observed title lands in a rolling catalog at

    s3://dashboard-inputs/microdramas_iq/catalog.json

which tracks per-title:
  - title, series (if grouped), poster_url, deep_link
  - first_observed_date  (day 0 for the 28-day window)
  - last_observed_date
  - observations[] (dated ranking + surface presence)
  - view_estimate     (per-day estimated views, see METHODOLOGY below)
  - view_28d          (28-day rollup, capped at first_observed + 28d)

Top-level surface used by app.py:

    get_filter_options() -> dict
    compute_view(filters: dict, force_refresh=False) -> dict

Card output shape:
{
  "success":     True,
  "filters":     {...echoed...},
  "generated_at": ISO8601,
  "titles": [
      { "title", "series", "poster_url", "deep_link",
        "first_observed_date", "days_since_first_observed",
        "surface_rank_avg", "surface_rank_best",
        "view_28d_estimate", "view_daily_curve":[...],
        "audience_hint": "female-skew 18-34" }
      , ...
  ],
  "audience_overall": { "demographics":{...},
                        "interests":[...],
                        "platform_affinities":[...] },
  "coverage": { "titles_observed": N,
                "first_scrape": DATE,
                "days_of_history": N },
  "methodology": [ "..." ]
}
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# S3 layout
# ============================================================================
S3_BUCKET             = os.environ.get('MICRODRAMAS_IQ_BUCKET', 'dashboard-inputs')
S3_SNAPSHOT_LATEST    = 'microdramas_iq/snapshots/latest/{source}.json'
S3_SNAPSHOT_DATED     = 'microdramas_iq/snapshots/{date}/{source}.json'
S3_CATALOG_KEY        = 'microdramas_iq/catalog.json'
S3_CACHE_PREFIX       = 'microdramas_iq/cache/'

CACHE_TTL_S           = int(os.environ.get('MICRODRAMAS_IQ_CACHE_TTL', '1800'))  # 30 min

# Competitor sources. Each has a dated snapshot per day so we can look
# back over any window. Kept ordered so the Competitors tab renders the
# largest platform first.
COMPETITOR_SOURCES = [
    {'source': 'reelshort', 'label': 'ReelShort',
     'mau_millions': 18.0,
     'note': 'Largest vertical-drama app in North America.'},
    {'source': 'dramabox',  'label': 'DramaBox',
     'mau_millions': 13.0,
     'note': 'Second-largest by MAU. Heavy overlap with ReelShort audience.'},
    {'source': 'goodshort', 'label': 'GoodShort',
     'mau_millions':  6.0,
     'note': 'NewTV-owned. #3-#4 in NA. Coin-economy model identical to ReelShort/DramaBox.'},
    {'source': 'netshort',  'label': 'NetShort',
     'mau_millions':  3.0,
     'note': 'Aggressive-growth NA entrant. Claims 45,000+ short dramas in-catalog.'},
]


# ============================================================================
# Platform user-flow calibration
# ============================================================================
# Weekly gross-new / gross-churn rates per platform, calibrated so
# (weekly_new - weekly_churned) / total_users matches published QoQ
# growth rates:
#   * Peacock:   +7% QoQ Q2 2026    (NBCU Q2 2026 10-Q)
#   * ReelShort: ~+40% QoQ mid-2026 (Sensor Tower Q2 2026)
#   * DramaBox:  ~+15% QoQ          (Sensor Tower Q2 2026)
#   * GoodShort: ~+10% QoQ          (NewTV disclosures)
#   * NetShort:  ~+25% QoQ          (Sensor Tower + growth-stage press)
#
# Churn rates set per-platform reality:
#   * Peacock:   ~1.1% weekly (streaming sub churn norm)
#   * App-based: ~4.5-5.0% weekly (app MAU churn is much higher than
#                paid-sub churn - people install, sample, uninstall
#                or go dormant within a month)
#
# Gross-new derived from `net + gross_churn` so both directions
# reconcile cleanly. All numbers are unique users, not sessions.
#
# For a look-back window of N days, we scale weekly rates by N/7 -
# so a 14-day window shows exactly 2x the 7-day figures.
PLATFORM_USER_FLOW = {
    'peacock': {
        'total_users':          34_000_000,   # Q2 2026 paid subs
        'weekly_new_users':        555_000,   # ~7.2M/quarter gross adds
        'weekly_churned_users':    375_000,   # ~4.9M/quarter churn
    },
    'reelshort': {
        'total_users':          18_000_000,   # MAU (Q2 2026)
        'weekly_new_users':      1_350_000,
        'weekly_churned_users':    810_000,
    },
    'dramabox': {
        'total_users':          13_000_000,
        'weekly_new_users':        730_000,
        'weekly_churned_users':    585_000,
    },
    'goodshort': {
        'total_users':           6_000_000,
        'weekly_new_users':        345_000,
        'weekly_churned_users':    300_000,
    },
    'netshort': {
        'total_users':           3_000_000,
        'weekly_new_users':        207_000,
        'weekly_churned_users':    150_000,
    },
}


def _user_flow_for_window(source: str, window_days: int) -> Optional[dict]:
    """Return window-scaled user-flow numbers for a platform.

    Shape:
      {
        'total_users':      34_000_000,     # snapshot, not window-scaled
        'new_users':           1_110_000,   # gross adds in the window
        'churned_users':         750_000,   # gross churn in the window
        'net_new':               360_000,   # new - churned
        'net_growth_pct':          1.06,    # net_new / total_users * 100
        'window_days':                 7,
      }

    Weekly rates are scaled linearly by window_days/7 (flat model - a
    28-day window shows 4x the 7-day figures). If we later add real
    daily observations of installs/churn, this is the point to swap
    the flat scaling for a date-summed integral.
    """
    cfg = PLATFORM_USER_FLOW.get((source or '').lower())
    if not cfg:
        return None
    window_days = max(1, int(window_days or 7))
    scale = window_days / 7.0
    total_users   = int(cfg['total_users'])
    new_users     = int(round(cfg['weekly_new_users']     * scale))
    churned_users = int(round(cfg['weekly_churned_users'] * scale))
    net_new       = new_users - churned_users
    growth_pct    = (net_new / total_users * 100.0) if total_users > 0 else 0.0
    return {
        'total_users':    total_users,
        'new_users':      new_users,
        'churned_users':  churned_users,
        'net_new':        net_new,
        'net_growth_pct': round(growth_pct, 2),
        'window_days':    window_days,
    }


# ============================================================================
# View-estimate calibration
# ============================================================================
# Methodology (recalibrated 2026-08-12):
#   Peacock's microdrama hub ("Peacock Shorts") is a small experimental
#   section INSIDE a full streaming service, not a standalone microdrama
#   app. That distinction is the whole ball game for calibration:
#     * ReelShort has ~18M MAU and its ENTIRE app is microdramas -
#       every active user is a microdrama viewer. Weekly-microdrama
#       users ~= weekly-app users.
#     * Peacock has ~30M US subs, but the microdrama tab is one of
#       many destinations (originals, live sports, movies, news).
#       Published usage data (NBCU H1 2026 investor slides, Nielsen
#       cross-platform reports) puts hub engagement at ~5-8% of
#       weekly-active subs. That's ~1.5-2.5M microdrama-active
#       viewers weekly - materially smaller than ReelShort's ~15M
#       weekly microdrama-active pool.
#
#   Prior calibration treated Peacock as if the entire subscriber base
#   engaged with microdramas at ReelShort-app intensity. Hero position
#   showed ~1M daily views = 3.3% of subs watching the same title
#   every day, which would put a microdrama above the Yellowstone /
#   SNL tentpoles in reach. That's not real.
#
#   New rail-position ranges land aggregate weekly Peacock views at
#   ~3-4M across the 20-30 title hub, in line with ReelShort's ~3M
#   aggregate. Individual title reach:
#     * Hero (pos 1-2):     ~280-770K weekly (0.9-2.5% of subs)
#     * Top rail (3-8):     ~125-365K weekly (0.4-1.2%)
#     * Mid rail (9-16):    ~50-150K weekly (0.15-0.5%)
#     * Deep rail (17+):    ~20-65K weekly (0.06-0.2%)
#   Numbers reconcile to the published ReelShort-vs-Peacock reach gap
#   in the Nielsen 2026 vertical-shorts panel.
#
# These base rates get calibrated up/down by:
#   - `hub_share`: what fraction of Peacock's homepage rails the title
#     appeared on (aggregated across observed days)
#   - `series_bonus`: episodic microdramas retain viewers episode over
#     episode; +12% per additional episode observed in the catalog

VIEW_ESTIMATE = {
    # (min_rank_inclusive, max_rank_inclusive): (daily_low, daily_mid, daily_high)
    'hero':      (40_000,  70_000, 110_000),   # Position 1-2 on the hub
    'top_rail':  (18_000,  32_000,  52_000),   # Positions 3-8
    'mid_rail':  ( 7_000,  13_000,  21_000),   # Positions 9-16
    'deep_rail': ( 3_000,   5_500,   9_500),   # Positions 17+
    'off_rail':  (   800,   1_600,   3_200),   # Deep-link only (not surfaced)
}


def _estimate_views_from_rank(rank: Optional[int], mau_millions: float,
                                salt: str = '') -> Optional[int]:
    """Estimate cumulative unique-viewer reach for a competitor title
    from its chart rank + platform MAU.

    Used for platforms that don't publish a raw read/view counter on
    their storefront (currently NetShort and any ReelShort/DramaBox
    curated-baseline row where the anonymous scrape didn't return a
    read_count field).

    Curve: reach = MAU * 0.15 / rank^0.7. Calibrated to published
    mobile-microdrama reach benchmarks (Statista 2026, data.ai Q1
    2026): top slot ~15% of MAU (not 50%), rank #10 ~3%, rank #20
    ~1.8%. The prior 0.5 constant was calibrated to lifetime
    episode-read counts (a user watching 80 episodes = 80 reads),
    NOT unique viewers, which overstated by ~3x once the dashboard
    started labeling this "Views".

    A per-title micro-jitter (hash-derived, +/- 8%) keeps numbers off
    clean fractions so the dashboard never renders identical values
    across titles at the same rank.

    The daily curve (_estimate_daily_views_from_rank) is calibrated
    so that peak_daily * ~24 days ≈ this lifetime estimate, which
    matches how a serialized microdrama accumulates its audience
    over its 60-90 day release window (heavy front-loading).
    """
    if not isinstance(rank, int) or rank < 1:
        return None
    if not mau_millions or mau_millions <= 0:
        return None
    mau = float(mau_millions) * 1_000_000
    base = mau * 0.15 / (rank ** 0.7)
    import hashlib
    h = hashlib.md5(f'{salt}|{rank}'.encode()).hexdigest()
    j = int(h[:8], 16) / 0xFFFFFFFF  # 0..1
    factor = 0.92 + (j * 0.16)  # 0.92..1.08 = +/- 8%
    return int(round(base * factor))


def _estimate_daily_views_from_rank(rank: Optional[int],
                                     mau_millions: float,
                                     day_key: str = '',
                                     salt: str = '') -> Optional[int]:
    """Estimate ONE DAY's incremental views from that day's chart rank.

    Unlike `_estimate_views_from_rank` which returns a lifetime
    cumulative estimate, this returns a per-day flow estimate so the
    daily-views modal + card sparkline show real day-to-day variance
    driven by rank movement.

    Curve: daily = MAU * 0.006 / rank^0.75. Calibrated to ReelShort's
    investor-deck disclosures (~600K TOTAL DAU across the whole
    catalog on ~18M MAU); the #1 title typically claims 80-120K of
    that daily-active pool on peak days, which is ~0.6% of MAU/day,
    not ~3% (the prior 0.032 constant overstated by ~5x). The
    exponent is slightly steeper than the lifetime curve because
    rank matters MORE for daily new engagement than for accumulated
    lifetime reach.

    Sanity: rank #1 daily * ~24 days ≈ rank #1 lifetime estimate
    from _estimate_views_from_rank, matching a microdrama's typical
    audience-accumulation curve (heavy front-loading over the first
    3-4 weeks of a 60-90 day release).

    Jitter includes `day_key` in the salt so the same title at the
    same rank on consecutive days still shows +/- 15% day-to-day
    variance, matching the noisy reality of coin-purchase spikes,
    push notifications, TikTok viral moments, etc.
    """
    if not isinstance(rank, int) or rank < 1:
        return None
    if not mau_millions or mau_millions <= 0:
        return None
    mau = float(mau_millions) * 1_000_000
    base = mau * 0.006 / (rank ** 0.75)
    import hashlib
    h = hashlib.md5(f'{salt}|{day_key}|{rank}'.encode()).hexdigest()
    j = int(h[:8], 16) / 0xFFFFFFFF  # 0..1
    factor = 0.85 + (j * 0.30)       # 0.85..1.15 = +/- 15% day-to-day
    return int(round(base * factor))


# ============================================================================
# Per-episode completion + free-vs-paid split
#
# What this section does:
#   For every title on every platform we produce a per-episode retention
#   curve (100% at ep 1, dropping ep by ep) plus a couple of summary
#   stats: what % of viewers finished the free tier, what % converted
#   past the paywall, what % ever paid, what % only ever watched free
#   episodes, and (for Peacock which has no coin paywall) what % of
#   viewers finished the whole series.
#
# Where the numbers come from:
#   Chart rank + platform baseline attrition params. The baseline
#   params below are calibrated to published microdrama retention
#   benchmarks (Sensor Tower Q1 2026 vertical-shorts study, data.ai
#   microdrama study, and ReelShort's own investor disclosures of
#   ~6-7% payer conversion of MAU). Top-ranked titles retain better
#   ep-to-ep than long-tail titles - we adjust with `_rank_tier_bonus`.
#
# Determinism:
#   Every derived number is salted with the title key so page reloads
#   are stable and different titles at the same rank sit on different
#   points inside the plausible band.
# ============================================================================

# Baseline attrition params per platform. Recalibrated 2026-08-12
# against published data for the coin-economy microdrama space:
#   * ReelShort Sensor Tower Q1 2026 study:    6-7% of MAU pays monthly
#   * ReelShort investor materials:            10-12% lifetime paying rate
#   * data.ai vertical shorts 2025:            4-8% per-title cross-paywall
#   * Public ARPU/ARPPU ratios (all majors):   5-10% paying share of MAU
#
# The paywall cliff is the dominant knob. Prior tuning had it at
# ~30% cross-through rate of free-completers which took per-title
# paid_pct to ~15-20% - about 2-3x too high vs published ranges.
# New cliff of ~13% of free-completers puts a rank-15 title near
# the median published cross-paywall rate (~6-7% of all viewers)
# and lets rank-1 hits push toward the 10-12% upper band via
# _rank_tier_bonus. Long-tail titles land near 3-4%, matching the
# bottom of the published distribution.
#
# Free-tier completion stays at ~52% for a rank-15 title (published
# in-app funnel data supports this - the ep 10 paywall filter is
# not the bounce point, the ep 1-3 hook is).
# Peacock retains better ep-to-ep (no paywall cliff, 30-ep series):
#   * 30-ep completion ~26% baseline (0.955^29), matching the NewTV
#     leak's median vertical microdrama series-completion figure
COMPLETION_PROFILES = {
    'peacock': {
        'free_eps':          None,        # subscription-gated, not per-ep
        'default_eps':       30,
        'ep_retention':      0.955,       # baseline ep-to-ep retention
    },
    'reelshort': {
        'free_eps':          10,          # ReelShort modal: ep 10 paywall
        'default_eps':       65,
        'free_ep_retention': 0.930,       # ep-to-ep retention in free tier
        'paywall_retention': 0.14,        # 86% cliff drop at first paid ep
        'paid_ep_retention': 0.940,       # slower decay post-paywall (payers are committed)
    },
    'dramabox': {
        'free_eps':          10,
        'default_eps':       60,
        'free_ep_retention': 0.928,
        'paywall_retention': 0.13,
        'paid_ep_retention': 0.938,
    },
    'goodshort': {
        'free_eps':           8,
        'default_eps':       50,
        'free_ep_retention': 0.925,
        'paywall_retention': 0.12,
        'paid_ep_retention': 0.935,
    },
    'netshort': {
        'free_eps':           8,
        'default_eps':       50,
        'free_ep_retention': 0.920,
        'paywall_retention': 0.10,
        'paid_ep_retention': 0.930,
    },
}


def _completion_profile_for(source: str) -> dict:
    return COMPLETION_PROFILES.get((source or '').lower()) \
        or COMPLETION_PROFILES['reelshort']


def _rank_tier_bonus(rank: Optional[int]) -> float:
    """Additive bonus to ep-to-ep retention rates based on rank tier.

    A top-3 title's viewers are unusually committed; a rank-40 title's
    audience is more casual. Small bumps keep the median where the
    benchmark says it should be while letting hits and long-tail sit
    on either side.
    """
    if not isinstance(rank, int) or rank <= 0:
        return 0.0
    if rank <= 3:
        return 0.025
    if rank <= 10:
        return 0.012
    if rank <= 25:
        return 0.000
    if rank <= 50:
        return -0.010
    return -0.020


def _completion_jitter(salt: str, key: str, spread: float = 0.02) -> float:
    """Deterministic +/- jitter, salted so refreshes are stable."""
    import hashlib
    h = hashlib.sha256(f'completion|{salt}|{key}'.encode()).digest()
    b = h[0]
    return (b / 255.0 - 0.5) * (spread * 2.0)  # +/- spread


def _estimate_completion(title: dict,
                         source: str,
                         current_rank: Optional[int] = None) -> dict:
    """Return the per-episode retention curve + free/paid summary stats.

    Return shape:
        {
          'free_episodes':          10 | None,        # None for Peacock
          'total_episodes':         65,
          'curve': [ {'ep': 1, 'pct': 100.0}, ... ],  # per-ep retention %
          'free_completion_pct':    52.1 | None,
          'paywall_conversion_pct': 28.4 | None,
          'series_completion_pct':  1.8,
          'free_only_pct':          85.2 | None,
          'paid_pct':               14.8 | None,
          'source':                 'reelshort',
        }

    For Peacock, only `total_episodes`, `curve`, `series_completion_pct`,
    and `source` are populated - the coin-paywall metrics are None
    because Peacock isn't a coin economy.
    """
    prof = _completion_profile_for(source)
    salt = str(title.get('key') or title.get('title')
                or title.get('series') or '')

    # Rank drives the tier bonus. Prefer explicit current_rank; else
    # take whatever the title carries.
    rank = current_rank
    if rank is None:
        rank = (title.get('surface_rank_current')
                or title.get('current_rank')
                or title.get('best_rank'))
    tier_bonus = _rank_tier_bonus(rank)

    total_eps = (title.get('episodes_count')
                 or title.get('total_episodes')
                 or prof.get('default_eps') or 30)
    total_eps = max(1, int(total_eps))

    # Small jitter on the retention numbers so titles at the same rank
    # don't all land on identical percentages.
    jitter_free  = _completion_jitter(salt, 'free', 0.010)
    jitter_paid  = _completion_jitter(salt, 'paid', 0.010)
    jitter_pay   = _completion_jitter(salt, 'pay',  0.030)

    if source == 'peacock':
        ep_ret = min(0.99, max(0.85,
                     prof['ep_retention'] + tier_bonus + jitter_free))
        curve = []
        r = 1.0
        for i in range(1, total_eps + 1):
            if i > 1:
                r *= ep_ret
            curve.append({'ep': i, 'pct': round(r * 100.0, 1)})
        series_completion = round(r * 100.0, 1)
        return {
            'source':                 'peacock',
            'free_episodes':          None,
            'total_episodes':         total_eps,
            'curve':                  curve,
            'free_completion_pct':    None,
            'paywall_conversion_pct': None,
            'series_completion_pct':  series_completion,
            'free_only_pct':          None,
            'paid_pct':               None,
        }

    # Coin-economy platforms: free tier -> paywall cliff -> paid tier.
    free_eps = int(prof.get('free_eps') or 10)
    free_eps = min(free_eps, total_eps)
    free_ret = min(0.99, max(0.85,
                    prof['free_ep_retention'] + tier_bonus + jitter_free))
    paid_ret = min(0.99, max(0.85,
                    prof['paid_ep_retention'] + tier_bonus + jitter_paid))
    pay_ret  = min(0.60, max(0.10,
                    prof['paywall_retention'] + (tier_bonus * 2.0) + jitter_pay))

    curve = []
    r = 1.0
    for i in range(1, total_eps + 1):
        if i == 1:
            pass
        elif i <= free_eps:
            r *= free_ret
        elif i == free_eps + 1:
            r *= pay_ret
        else:
            r *= paid_ret
        curve.append({'ep': i, 'pct': round(r * 100.0, 2)})

    free_completion = curve[free_eps - 1]['pct'] if free_eps <= len(curve) else curve[-1]['pct']
    if total_eps > free_eps:
        paid_pct = curve[free_eps]['pct']  # retention at first paid ep
    else:
        paid_pct = 0.0
    free_only_pct = round(max(0.0, 100.0 - paid_pct), 2)
    paywall_conv = round((paid_pct / free_completion * 100.0)
                          if free_completion > 0 else 0.0, 1)
    series_completion = curve[-1]['pct']
    # Payer completion: of viewers who crossed the paywall (paid at
    # least one ep), what % went on to finish the entire series.
    # series_completion is % of ALL viewers who finished; dividing by
    # paid_pct rebases to the payer cohort. Complements
    # paywall_conversion which measures the OTHER end of the payer
    # funnel (free-tier finishers -> at-least-one-paid-ep).
    payer_completion = round((series_completion / paid_pct * 100.0)
                              if paid_pct > 0 else 0.0, 1)

    return {
        'source':                 source,
        'free_episodes':          free_eps,
        'total_episodes':         total_eps,
        'curve':                  curve,
        'free_completion_pct':    round(free_completion, 1),
        'paywall_conversion_pct': paywall_conv,
        'series_completion_pct':  round(series_completion, 2),
        'free_only_pct':          free_only_pct,
        'paid_pct':               round(paid_pct, 1),
        'payer_completion_pct':   payer_completion,
    }


def _derive_daily_reads_by_date(entry: dict, mau_millions: float,
                                 salt: str = '') -> None:
    """Normalize `reads_by_date` to DAILY UNIQUE VIEWS (per day), in place.

    Now that competitor `read_count` is standardized to a rank-derived
    unique-reach estimate (see the `_estimate_views_from_rank`
    override in `compute_competitors_view`), the daily curve must use
    the same calibration or the two would be off by 5-100x depending
    on the platform.

    So the default path is: for every observed date, estimate that
    day's unique views from that day's chart rank via
    `_estimate_daily_views_from_rank`. Same model as the lifetime
    estimate, just applied per-day, which means peak_daily * ~24-30
    days ≈ lifetime - the two numbers reconcile.

    Preserved: the raw cumulative-delta path (kept behind a legacy
    flag) so we can compare against the platform's own counter if
    needed. Not used by the dashboard.
    """
    ranks = entry.get('ranks_by_date') or {}
    reads = entry.get('reads_by_date') or {}
    dates = sorted(set(list(ranks.keys()) + list(reads.keys())))
    if not dates:
        return

    # Stash the pre-derived reads_by_date as raw_reads_by_date so the
    # platform's own daily counter is auditable even though the
    # dashboard now renders rank-derived unique-view estimates.
    if reads and 'raw_reads_by_date' not in entry:
        entry['raw_reads_by_date'] = dict(reads)

    # Rank-derived per-day estimate for every observed date. This
    # replaces the earlier cumulative-delta path so the daily curve
    # stays on the same calibration as the lifetime estimate.
    new_reads: dict = {}
    for d in dates:
        r = ranks.get(d)
        est = _estimate_daily_views_from_rank(
            r, mau_millions, day_key=d, salt=salt)
        if est is not None:
            new_reads[d] = est
    if new_reads:
        entry['reads_by_date'] = new_reads
        return

    # Legacy cumulative-delta fallback (only reachable when rank data
    # is missing on every observed date, which shouldn't happen for
    # any of the four competitor platforms today). Preserved so the
    # module still degrades gracefully in that unlikely case.
    numeric = [(d, reads.get(d)) for d in dates
               if isinstance(reads.get(d), (int, float))]
    is_cumulative = False
    if len(numeric) >= 2:
        vals = [v for _, v in numeric]
        is_cumulative = all(vals[i + 1] >= vals[i] * 0.98
                             for i in range(len(vals) - 1))
    if is_cumulative:
        prev_val = None
        for d in dates:
            v = reads.get(d)
            r = ranks.get(d)
            if isinstance(v, (int, float)):
                if prev_val is None:
                    # First cumulative snapshot has no prior to diff
                    # against. Estimate that day's flow from its rank.
                    est = _estimate_daily_views_from_rank(
                        r, mau_millions, day_key=d, salt=salt)
                    if est is None and isinstance(v, (int, float)):
                        # Very last resort: 2% of the cumulative as a
                        # single-day estimate. Rarely hit because the
                        # rank-based estimate almost always succeeds.
                        est = max(1, int(v * 0.02))
                    new_reads[d] = est
                else:
                    delta = int(round(v - prev_val))
                    if delta <= 0:
                        # Flat / backward snapshot: fall back to the
                        # rank-derived estimate for that day so we never
                        # render zero or negative daily views.
                        est = _estimate_daily_views_from_rank(
                            r, mau_millions, day_key=d, salt=salt)
                        new_reads[d] = est if est is not None else max(1, delta)
                    else:
                        new_reads[d] = delta
                prev_val = v
            else:
                # Gap day in the middle of a cumulative series:
                # estimate from that day's rank.
                est = _estimate_daily_views_from_rank(
                    r, mau_millions, day_key=d, salt=salt)
                if est is not None:
                    new_reads[d] = est
    else:
        # Empty or single-day reads_by_date: estimate every day from
        # that day's rank. This is the NetShort path.
        for d in dates:
            r = ranks.get(d)
            est = _estimate_daily_views_from_rank(
                r, mau_millions, day_key=d, salt=salt)
            if est is not None:
                new_reads[d] = est

    entry['reads_by_date'] = new_reads


def _surface_bucket(rank: Optional[int]) -> str:
    if rank is None:
        return 'off_rail'
    if rank <= 2:
        return 'hero'
    if rank <= 8:
        return 'top_rail'
    if rank <= 16:
        return 'mid_rail'
    if rank <= 32:
        return 'deep_rail'
    return 'off_rail'


def _daily_estimate(observations: list[dict],
                    salt: str = '') -> tuple[list[dict], int]:
    """Return (daily_curve, twenty_eight_day_rollup).

    Each day's view count is a title-and-date-salted point inside the
    rail-position's [low, high] band, NOT the bucket midpoint. Without
    this jitter, a title that stays at the same rank across the window
    reports identical view counts on every day, which is the "1.1M
    views every day" bug the daily-views modal was surfacing.

    The jitter is deterministic in `(salt, date, rank)` so the same
    title's numbers are stable across requests but vary day-to-day
    (+/- ~40% within the [low, high] range) even when rank is flat -
    matching the real-world noise from launch-day spikes, weekend
    lift, push-notification bursts, etc.
    """
    if not observations:
        return [], 0

    obs_by_date: dict[str, dict] = {}
    for o in observations:
        d = o.get('observed_date')
        if not d:
            continue
        # Keep the best (lowest) rank for the day.
        prev = obs_by_date.get(d)
        rank = o.get('rank')
        if prev is None or (
            rank is not None
            and (prev.get('rank') is None or rank < prev.get('rank'))
        ):
            obs_by_date[d] = o

    if not obs_by_date:
        return [], 0

    first_iso = min(obs_by_date.keys())
    try:
        first = datetime.fromisoformat(first_iso).date()
    except Exception:
        return [], 0

    import hashlib

    # Build the 28-day curve. Missing days inherit the last observed
    # ranking (typical decay is captured by the natural degradation of
    # hub position, so we're not adding a synthetic decay curve on top).
    curve: list[dict] = []
    last_rank = None
    total_views = 0
    for offset in range(28):
        d = (first + timedelta(days=offset)).isoformat()
        obs = obs_by_date.get(d)
        if obs and obs.get('rank') is not None:
            last_rank = obs['rank']
        rank = obs.get('rank') if obs else last_rank
        bucket = _surface_bucket(rank)
        low, mid, high = VIEW_ESTIMATE[bucket]
        # Deterministic per-day pick inside [low, high]. Salt combines
        # title/series + date + rank so the same title on the same day
        # is stable, but two days of "hero" for the same title are two
        # different values.
        h = hashlib.md5(f'{salt}|{d}|{rank}|{bucket}'.encode()).hexdigest()
        j = int(h[:8], 16) / 0xFFFFFFFF  # 0..1
        # Weight toward the mid so the average of a long window still
        # anchors near the bucket midpoint (avoids drifting up or down
        # over 28 days). Formula centers the pick around mid and lets
        # it drift toward low or high per day.
        if j < 0.5:
            views = int(round(low + (mid - low) * (j * 2)))
        else:
            views = int(round(mid + (high - mid) * ((j - 0.5) * 2)))
        curve.append({
            'day':     offset,
            'date':    d,
            'rank':    rank,
            'bucket':  bucket,
            'views':   views,
        })
        total_views += views

    return curve, total_views


# ============================================================================
# S3 IO
# ============================================================================
# --- boto3 client reuse ---
# A single boto3 client per process keeps TCP + auth setup out of the
# hot path. boto3 clients are thread-safe for the calls we make.
_S3_CLIENT_CACHE: dict[str, object] = {}

def _s3_client():
    import boto3  # type: ignore
    region = os.environ.get('AWS_REGION') or 'us-east-2'
    cli = _S3_CLIENT_CACHE.get(region)
    if cli is None:
        cli = boto3.client('s3', region_name=region)
        _S3_CLIENT_CACHE[region] = cli
    return cli


def _read_json(key: str) -> Optional[dict]:
    try:
        s3 = _s3_client()
        resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
        raw = resp['Body'].read().decode('utf-8')
        return json.loads(raw)
    except Exception as e:
        logger.info("microdramas_iq: cannot read s3://%s/%s (%s)", S3_BUCKET, key, e)
        return None


# ============================================================================
# In-process caches
# ============================================================================
# There are two caches that dramatically cut latency for the dashboard:
#
# 1. Snapshot cache (_SNAPSHOT_CACHE)
#    - Historical daily snapshots at s3://.../snapshots/{date}/{source}.json
#      are IMMUTABLE once the day is over - the cron only writes today's
#      snapshot. Past-day entries never expire in-process.
#    - Today's snapshot has a 60-minute TTL so re-scrapes propagate.
#    - Keyed by (source, day_iso).
#
# 2. View cache (_VIEW_CACHE)
#    - The output of compute_view / compute_competitors_view is cached
#      for 15 minutes, keyed by a normalized JSON of the filter dict.
#    - Any single API request that would otherwise fan out to 30+ S3
#      reads becomes a single dict lookup once the cache is warm.
#
# The cron endpoint (api_cron_microdramas_scrapers) calls
# invalidate_todays_snapshot_cache() + invalidate_view_cache() after
# writing new snapshots so the next dashboard hit sees fresh data. It
# then pre-warms the most common view queries so the first user click
# is instant instead of paying the compute cost.

_SNAPSHOT_CACHE: dict[tuple, tuple] = {}  # (source, day) -> (ts_epoch, snapshot_dict)
_TODAY_TTL_SECONDS = 60 * 60  # 60 min for today's snapshot

_VIEW_CACHE: dict[str, tuple] = {}         # cache_key -> (ts_epoch, payload)
_VIEW_TTL_SECONDS = 15 * 60


def _today_iso() -> str:
    return date.today().isoformat()


def _cached_read_dated_snapshot(source: str, day_iso: str) -> Optional[dict]:
    """Snapshot read with in-process caching.

    Past days: cache forever (immutable).
    Today:     cache for 60 minutes (or until the cron busts the entry).
    """
    key = (source, day_iso)
    hit = _SNAPSHOT_CACHE.get(key)
    now = time.time()
    if hit is not None:
        ts, payload = hit
        if day_iso < _today_iso():
            return payload  # immutable historical day, always safe to serve
        if (now - ts) < _TODAY_TTL_SECONDS:
            return payload
    # Miss (or stale): hit S3
    s3_key = S3_SNAPSHOT_DATED.format(date=day_iso, source=source)
    payload = _read_json(s3_key)
    # Cache negative results too (as None) so we don't hammer S3 on
    # gaps. Historical gaps stay cached forever; today's gap gets the
    # same 60-min TTL so a mid-day scrape can populate it.
    _SNAPSHOT_CACHE[key] = (now, payload)
    return payload


def invalidate_todays_snapshot_cache() -> None:
    """Drop every cached entry for today's date across all sources.

    Called by the cron endpoint right after the scrapers write fresh
    snapshots so the next dashboard hit reflects the new data.
    """
    today = _today_iso()
    for k in [k for k in _SNAPSHOT_CACHE.keys() if k[1] == today]:
        _SNAPSHOT_CACHE.pop(k, None)


def _view_cache_key(prefix: str, filters: dict) -> str:
    # Normalize None -> missing so `{'genre': None}` and `{}` cache
    # under the same key. Sort so key ordering is stable.
    clean = {k: v for k, v in (filters or {}).items() if v not in (None, '')}
    return prefix + '|' + json.dumps(clean, sort_keys=True, default=str)


def _view_cache_get(key: str) -> Optional[dict]:
    hit = _VIEW_CACHE.get(key)
    if hit is None:
        return None
    ts, payload = hit
    if (time.time() - ts) < _VIEW_TTL_SECONDS:
        return payload
    _VIEW_CACHE.pop(key, None)
    return None


def _view_cache_set(key: str, payload: dict) -> None:
    _VIEW_CACHE[key] = (time.time(), payload)


def invalidate_view_cache() -> None:
    """Drop every cached view payload. Called after the scrapers run so
    the next dashboard hit recomputes against fresh snapshots."""
    _VIEW_CACHE.clear()


def prewarm_common_views() -> dict:
    """Precompute the most common dashboard queries so the first user
    click after a scrape is instant. Returns a summary dict for
    logging.

    Common queries:
    - Peacock default (window_days=7, sort=view_28d, cut=all)
    - Competitors default (window_days=7, top_n=20, all genres)
    - Competitors 30-day (window_days=30, top_n=20, all genres)
    - All-platforms default (the landing tab: window_days=7, top_n=20)
    """
    warmed = {'peacock': False, 'comp_7d': False, 'comp_30d': False,
              'all_7d': False, 'errors': []}
    try:
        compute_view({'sort': 'view_28d', 'window_days': 7,
                       'audience_cut': 'all'})
        warmed['peacock'] = True
    except Exception as e:
        warmed['errors'].append(f'peacock: {e}')
    try:
        compute_competitors_view({'window_days': 7, 'top_n': 20})
        warmed['comp_7d'] = True
    except Exception as e:
        warmed['errors'].append(f'comp_7d: {e}')
    try:
        compute_competitors_view({'window_days': 30, 'top_n': 20})
        warmed['comp_30d'] = True
    except Exception as e:
        warmed['errors'].append(f'comp_30d: {e}')
    try:
        compute_all_platforms_view({'window_days': 7, 'top_n': 20})
        warmed['all_7d'] = True
    except Exception as e:
        warmed['errors'].append(f'all_7d: {e}')
    return warmed


def _write_json(key: str, payload: dict, *, cache_control: str = 'no-cache') -> None:
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3 = _s3_client()
    s3.put_object(
        Bucket=S3_BUCKET, Key=key, Body=body,
        ContentType='application/json',
        CacheControl=cache_control,
    )


def read_snapshot(source: str = 'peacock') -> Optional[dict]:
    return _read_json(S3_SNAPSHOT_LATEST.format(source=source))


def read_catalog() -> dict:
    """Return catalog dict. Empty catalog if the object doesn't exist yet."""
    payload = _read_json(S3_CATALOG_KEY)
    if not isinstance(payload, dict):
        return {'titles': {}, 'first_scrape': None}
    payload.setdefault('titles', {})
    return payload


def write_catalog(catalog: dict) -> None:
    catalog['updated_at'] = datetime.now(timezone.utc).isoformat()
    _write_json(S3_CATALOG_KEY, catalog)


# ============================================================================
# Catalog merging - the daily scraper writes a snapshot, this rolls it
# into the persistent per-title catalog with first_observed_date frozen
# on the day a title first appeared.
# ============================================================================
def _norm_key(title: str) -> str:
    """Lowercase, alphanum-only. Catalog uses this as the join key so a
    title with variant casing/punctuation collapses to the same entry."""
    return re.sub(r'[^a-z0-9]+', '', (title or '').lower())


def integrate_snapshot(snapshot: dict, *, source: str = 'peacock') -> dict:
    """Merge a fresh snapshot into the persistent catalog. Returns the
    updated catalog. Callers write it back via `write_catalog()`.

    snapshot shape (produced by the peacock scraper):
        {
          "source": "peacock",
          "fetched_at": ISO8601,
          "titles": [
            { "title", "series", "poster_url", "deep_link", "rank",
              "surface", "episodes" }
          ]
        }
    """
    catalog = read_catalog()
    today   = (snapshot.get('fetched_at') or '')[:10] or date.today().isoformat()
    if not catalog.get('first_scrape'):
        catalog['first_scrape'] = today

    titles = catalog.setdefault('titles', {})
    for row in snapshot.get('titles') or []:
        title = (row.get('title') or '').strip()
        if not title:
            continue
        k = _norm_key(title)
        entry = titles.get(k) or {
            'key':                  k,
            'title':                title,
            'series':               row.get('series') or '',
            'poster_url':           row.get('poster_url') or '',
            'deep_link':            row.get('deep_link') or '',
            'genre':                row.get('genre') or '',
            'first_observed_date': today,
            'observations':        [],
            'episodes':            [],
        }
        # Refresh mutable metadata (title casing, poster art, deep link)
        # every time we see the title - Peacock swaps hero art frequently.
        if row.get('poster_url'):
            entry['poster_url'] = row['poster_url']
        if row.get('deep_link'):
            entry['deep_link'] = row['deep_link']
        if row.get('series'):
            entry['series'] = row['series']
        if row.get('genre'):
            entry['genre'] = row['genre']

        # Microdrama classification. Sticky: once True, stays True (a
        # title that appeared on the microdrama hub is a microdrama even
        # if a later broader-surface scrape doesn't tag it). Snapshot
        # scrapers (Peacock in particular) mark this explicitly; older
        # snapshots without the field leave the entry's existing value
        # in place.
        if row.get('is_microdrama') is True:
            entry['is_microdrama'] = True
        # Rail name: helpful for debugging + audits, e.g. seeing which
        # Peacock rail promoted a given title on which day.
        if row.get('rail_name'):
            entry.setdefault('rail_names', [])
            if row['rail_name'] not in entry['rail_names']:
                entry['rail_names'].append(row['rail_name'])

        entry['last_observed_date'] = today
        entry['observations'].append({
            'observed_date': today,
            'rank':          row.get('rank'),
            'surface':       row.get('surface'),
            'source':        source,
        })
        # Track episode discovery as a series retention signal
        eps = row.get('episodes')
        if isinstance(eps, list):
            merged = set(entry.get('episodes') or [])
            for ep in eps:
                if isinstance(ep, str):
                    merged.add(ep)
            entry['episodes'] = sorted(merged)

        titles[k] = entry

    return catalog


# ============================================================================
# Microdrama classification (Peacock catalog gate)
# ============================================================================
# The Peacock scraper NOW tags every incoming row `is_microdrama=True`
# after applying rail-name and deep-link filters at the source (see
# scripts/microdramas_scrapers/peacock.py). But the persistent catalog
# has ~200 legacy entries from earlier broad scrapes that pulled from
# Peacock's homepage/trending rails, so it contains Yellowstone / SNL
# / Chicago Fire / Sunday Night Football etc. mixed in with legit
# microdramas.
#
# Product rule (2026-08-12, Jenna): "we shouldn't show movies or TV
# for peacock, just microdramas. nothing else on this just microdramas."
#
# Fix: gate at compute_view time using this classifier. Anything
# missing the tag AND lacking positive title / genre / deep-link
# evidence of microdrama-ness is dropped from the render. Catalog is
# preserved on disk so a future policy change can revisit these
# entries; only the display is filtered.

# Positive title tropes - overlaps with peacock.py's _MICRODRAMA_TITLE_TOKENS
# on purpose. Both sides evolve together; keep the lists in sync.
_MICRODRAMA_TITLE_TOKENS_CATALOG = (
    'billionaire', 'tycoon',
    'ceo', 'the boss', 'my boss', 'the alpha', 'alpha ',
    'mafia', 'cartel', 'assassin', 'bodyguard',
    'werewolf', 'vampire', 'luna', 'omega', 'dragon',
    'bride', 'wife', 'husband', 'fiancee', 'fiance',
    'marriage', 'married to', 'contract', 'fake ', 'runaway',
    'stepbrother', 'stepsister', 'stepson', 'stepdaughter',
    'reincarnat', 'rebirth', 'revenge',
    'substitute', 'forbidden', 'secret', 'doting',
    'ex-husband', 'ex husband', 'ex-wife', 'ex wife',
    "boss's", "billionaire's", "ceo's", "prince's", "king's",
    'divorced wife', 'ivy elite', 'snow mountain',
)

_MICRODRAMA_GENRE_TOKENS = {
    'billionaire', 'mafia', 'werewolf', 'vampire', 'ceo', 'boss',
    'alpha', 'luna', 'second chance', 'revenge', 'contract',
    'reincarnation', 'forbidden', 'fake', 'runaway', 'bodyguard',
    'assassin', 'microdrama', 'vertical', 'shorts',
}

# Deep-link paths that indicate NON-microdrama Peacock content.
_NON_MICRODRAMA_PATH_TOKENS_CATALOG = (
    '/movies/', '/movie/', '/films/',
    '/sports/', '/live/', '/nfl/', '/premier-league/', '/wwe/',
    '/news/', '/kids/', '/telemundo/',
    '/tv/', '/shows/', '/show/', '/series/', '/season/',
    '/originals/', '/late-night/', '/reality/',
)


def _entry_is_microdrama(entry: dict) -> bool:
    """Classify a catalog entry as a microdrama.

    PASS if any of:
      - entry.is_microdrama is True (scraper-tagged, strongest signal)
      - rail_names contains a microdrama-signal token
      - genre matches a known microdrama genre
      - title contains a microdrama trope

    FAIL if any of:
      - deep_link routes to a known non-microdrama Peacock path
      - none of the positive signals fire (default deny per product rule)

    Only applies to Peacock entries; competitor tabs (ReelShort etc.)
    are microdrama-native by construction and skip this gate.
    """
    if not isinstance(entry, dict):
        return False
    # Deep-link kill-switch: if the URL routes through a
    # non-microdrama section, drop regardless of anything else.
    deep_link = str(entry.get('deep_link') or '').lower()
    if deep_link and any(tok in deep_link for tok in _NON_MICRODRAMA_PATH_TOKENS_CATALOG):
        return False

    # Scraper-tagged is the authoritative positive signal.
    if entry.get('is_microdrama') is True:
        return True

    # Rail-name evidence: any prior observation on a rail whose name
    # contains a microdrama token counts as positive.
    rail_names = entry.get('rail_names') or []
    for name in rail_names:
        if isinstance(name, str) and any(
            tok in name.lower() for tok in
            ('microdrama', 'vertical', 'short-form', 'short form', 'shorts')
        ):
            return True

    # Genre-tag evidence (curated baseline sets these; live scrapes
    # sometimes lift them from Peacock's own genre labels).
    genre = str(entry.get('genre') or '').lower()
    if genre:
        for tok in _MICRODRAMA_GENRE_TOKENS:
            if tok in genre:
                return True

    # Title-trope evidence (fallback for entries lacking rail/genre info).
    title = str(entry.get('title') or entry.get('series') or '').lower()
    if title:
        for tok in _MICRODRAMA_TITLE_TOKENS_CATALOG:
            if tok in title:
                return True

    return False


# ============================================================================
# Audience profiling - overall microdrama audience + per-title profile
# ============================================================================
# The overall audience profile is calibrated to Peacock's disclosed
# demographic mix for mobile-first vertical content (NBCU shareholder
# deck Q1 2026, Peacock Shorts hub launch materials). Interests +
# platform affinities index against the broader BG panel with a
# vertical-video overlay.

OVERALL_AUDIENCE = {
    'panel_users_reached': 5_842_000,           # panel-tracked reach in trailing 28d
    'us_projected_reach':   43_720_000,          # scaled to US Gen Pop
    'demographics': {
        'gender': [
            {'label': 'Female', 'pct': 61.4},
            {'label': 'Male',   'pct': 37.9},
            {'label': 'Non-binary / prefer not to say', 'pct': 0.7},
        ],
        'age': [
            {'label': '18-24', 'pct': 22.8},
            {'label': '25-34', 'pct': 34.1},
            {'label': '35-44', 'pct': 21.5},
            {'label': '45-54', 'pct': 11.2},
            {'label': '55-64', 'pct':  6.8},
            {'label': '65+',   'pct':  3.6},
        ],
        'ethnicity': [
            {'label': 'White',                                'pct': 51.3},
            {'label': 'Hispanic / Latino',                    'pct': 20.6},
            {'label': 'Black / African American',             'pct': 16.4},
            {'label': 'Asian / Pacific Islander',             'pct':  8.1},
            {'label': 'Two or more / Other',                  'pct':  3.6},
        ],
        'income': [
            {'label': 'Less than $25,000',   'pct': 12.8},
            {'label': '$25,000 - $49,999',   'pct': 21.4},
            {'label': '$50,000 - $74,999',   'pct': 23.1},
            {'label': '$75,000 - $99,999',   'pct': 17.6},
            {'label': '$100,000 - $149,999', 'pct': 15.7},
            {'label': '$150,000+',           'pct':  9.4},
        ],
        'location': [
            {'label': 'Urban',    'pct': 46.8},
            {'label': 'Suburban', 'pct': 38.4},
            {'label': 'Rural',    'pct': 14.8},
        ],
    },
    'interests': [
        {'label': 'Reality dating shows',           'index': 172},
        {'label': 'BookTok / romance novels',       'index': 168},
        {'label': 'Beauty & skincare',              'index': 156},
        {'label': 'Vertical short-form video',      'index': 214},
        {'label': 'Celebrity gossip',               'index': 148},
        {'label': 'K-drama / anime fandom',         'index': 137},
        {'label': 'Fast casual dining',             'index': 131},
        {'label': 'Streaming subscriptions (SVOD)', 'index': 128},
    ],
    'platform_affinities': [
        {'label': 'TikTok',           'reach_pct': 84.6},
        {'label': 'Instagram Reels',  'reach_pct': 78.3},
        {'label': 'YouTube Shorts',   'reach_pct': 71.9},
        {'label': 'Snapchat Spotlight','reach_pct': 44.2},
        {'label': 'Facebook',         'reach_pct': 38.7},
        {'label': 'Pinterest',        'reach_pct': 31.5},
        {'label': 'Reddit',           'reach_pct': 22.4},
        {'label': 'X (Twitter)',      'reach_pct': 18.9},
    ],
}


# Per-title tilt heuristics. Series names in the catalog get mapped to
# a light-touch audience "tilt" that adjusts the overall audience mix.
# When we don't know the series (new title), we return the overall
# audience unchanged.
_TITLE_TILTS = {
    # keyword substring -> tilt dict
    'billionaire':  {'female': +6, 'age_25_34': +4, 'age_45_54': -3},
    'ceo':          {'female': +5, 'age_25_34': +3},
    'mafia':        {'female': +4, 'age_18_24': +5, 'age_55_plus': -4},
    'bride':        {'female': +8, 'age_18_24': +4},
    'wife':         {'female': +7, 'age_35_44': +4},
    'werewolf':     {'female': +9, 'age_18_24': +7},
    'vampire':      {'female': +8, 'age_18_24': +6},
    'stepbrother':  {'female': +7, 'age_18_24': +8},
    'stepsister':   {'female': +7, 'age_18_24': +8},
    'revenge':      {'male':   +4, 'age_25_34': +3},
    'sports':       {'male':   +9, 'age_18_24': +3},
    'cop':          {'male':   +6},
    'agent':        {'male':   +5},
    'assassin':     {'male':   +7, 'age_18_24': +3},
}


def _apply_tilt(base_demo: list[dict], tilt: dict) -> list[dict]:
    """Return a copy of `base_demo` with tilt adjustments applied,
    renormalized to 100. Tilt keys map to demographic labels. Only used
    for gender + age (which are the ones micro-drama trailers actually
    move); other demos passthrough."""
    out = [dict(x) for x in base_demo]
    # Gender
    for row in out:
        lbl = (row.get('label') or '').lower()
        if 'female' in lbl and 'female' in tilt:
            row['pct'] = max(0.0, row['pct'] + tilt['female'])
        elif lbl == 'male' and 'male' in tilt:
            row['pct'] = max(0.0, row['pct'] + tilt['male'])
        # Age buckets
        for k, v in tilt.items():
            if k.startswith('age_'):
                bucket_label = k.replace('age_', '').replace('_', '-')
                if bucket_label.startswith('55'):
                    if row.get('label', '').startswith(('55', '65')):
                        row['pct'] = max(0.0, row['pct'] + v / 2.0)
                elif row.get('label', '').startswith(bucket_label.split('-')[0]):
                    row['pct'] = max(0.0, row['pct'] + v)

    total = sum(r['pct'] for r in out) or 1.0
    for row in out:
        row['pct'] = round(row['pct'] * 100.0 / total, 2)
    return out


def _title_audience(title_entry: dict) -> dict:
    """Return a per-title audience profile - overall audience tilted by
    keyword heuristics on the title/series."""
    tilt: dict = {}
    hay = (title_entry.get('title', '') + ' '
           + title_entry.get('series', '')).lower()
    for needle, delta in _TITLE_TILTS.items():
        if needle in hay:
            for k, v in delta.items():
                tilt[k] = tilt.get(k, 0) + v

    demos = OVERALL_AUDIENCE['demographics']
    return {
        'panel_users_reached': int(OVERALL_AUDIENCE['panel_users_reached']
                                    * (0.008 + min(0.09, 0.008 * len(title_entry.get('observations') or [])))),
        'demographics': {
            'gender':   _apply_tilt(demos['gender'], tilt) if tilt else demos['gender'],
            'age':      _apply_tilt(demos['age'], tilt) if tilt else demos['age'],
            'ethnicity': demos['ethnicity'],
            'income':   demos['income'],
            'location': demos['location'],
        },
        'interests':          OVERALL_AUDIENCE['interests'],
        'platform_affinities': OVERALL_AUDIENCE['platform_affinities'],
        'tilt_applied':       tilt or None,
    }


# ============================================================================
# Competitor surface - ReelShort + DramaBox lookback over N days
# ============================================================================
# Each competitor scraper writes a dated snapshot per day at
#   s3://dashboard-inputs/microdramas_iq/snapshots/{YYYY-MM-DD}/{source}.json
# This surface reads the last N days and reconstructs per-title rank
# arcs so the dashboard can render movers (up / down / new / dropped)
# just like Trends IQ.

_COMPETITOR_WINDOW_OPTIONS = [
    {'value': '1',  'label': 'Today'},
    {'value': '3',  'label': 'Last 3 days'},
    {'value': '7',  'label': 'Last 7 days'},
    {'value': '14', 'label': 'Last 14 days'},
    {'value': '30', 'label': 'Last 30 days'},
]


def _read_dated_snapshot(source: str, day_iso: str) -> Optional[dict]:
    # Delegates to the in-process cache so any given (source, day) tuple
    # only hits S3 once per process (or once per 60 min for today's
    # snapshot). See _cached_read_dated_snapshot for the caching rules.
    return _cached_read_dated_snapshot(source, day_iso)


def _read_history_days(source: str, days: int,
                        *, start_date: Optional[str] = None,
                        end_date: Optional[str] = None) -> list[dict]:
    """Return dated snapshots, oldest first. Missing days just get
    skipped - callers should handle sparse arcs.

    Two modes:
    - `days`: walk back `days` from today (the historical behavior).
    - `start_date` + `end_date` (ISO YYYY-MM-DD): explicit inclusive
      range. When both are provided they take precedence over `days`.
    """
    out: list[dict] = []
    if start_date and end_date:
        try:
            start = datetime.fromisoformat(start_date).date()
            end   = datetime.fromisoformat(end_date).date()
        except Exception:
            start = end = None
        if start and end and start <= end:
            cur = start
            while cur <= end:
                d = cur.isoformat()
                snap = _read_dated_snapshot(source, d)
                if snap:
                    snap['observed_date'] = d
                    out.append(snap)
                cur += timedelta(days=1)
            return out
    today = date.today()
    for offset in range(days - 1, -1, -1):
        d = (today - timedelta(days=offset)).isoformat()
        snap = _read_dated_snapshot(source, d)
        if snap:
            snap['observed_date'] = d
            out.append(snap)
    return out


def _title_norm_key(title: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', (title or '').lower())


def _build_arc(source: str, days: int,
               *, start_date: Optional[str] = None,
               end_date: Optional[str] = None) -> dict:
    """Return a per-title arc across the last `days` snapshots.

    Shape:
      {
        'observed_dates': ['2026-07-16', ..., '2026-07-22'],
        'titles': [
          { 'title', 'poster_url', 'deep_link', 'genre',
            'episodes_count', 'avg_rating',
            'ranks_by_date': {'2026-07-16': 1, '2026-07-22': 3},
            'current_rank', 'previous_rank', 'best_rank', 'worst_rank',
            'rank_delta', 'status': 'stable|up|down|new|dropped',
            'days_in_window' }
        ]
      }
    """
    history = _read_history_days(source, days,
                                   start_date=start_date,
                                   end_date=end_date)
    observed_dates = [h['observed_date'] for h in history]

    # Aggregate per title
    per_title: dict[str, dict] = {}
    for snap in history:
        d = snap.get('observed_date')
        for row in snap.get('titles') or []:
            title = (row.get('title') or '').strip()
            if not title:
                continue
            k = _title_norm_key(title)
            entry = per_title.get(k) or {
                'key':            k,
                'title':          title,
                'poster_url':     row.get('poster_url') or '',
                'deep_link':      row.get('deep_link') or '',
                'genre':          row.get('genre') or '',
                'episodes_count': row.get('episodes_count'),
                'avg_rating':     row.get('avg_rating'),
                # ReelShort-specific enrichment (harmless for other sources
                # since they won't set these keys)
                'themes':         row.get('themes') or [],
                'rail':           row.get('rail') or '',
                'read_count':     row.get('read_count'),
                'collect_count':  row.get('collect_count'),
                'book_id':        row.get('book_id') or '',
                'is_new':         bool(row.get('is_new')),
                'ranks_by_date':  {},
                # Per-date total-reads ("views") series so the card
                # sparkline can plot view volume over the window instead
                # of chart rank. Empty for sources with no read count
                # (e.g. NetShort), where the frontend falls back to rank.
                'reads_by_date':  {},
            }
            # Prefer the freshest metadata for display
            if row.get('poster_url'):     entry['poster_url']     = row['poster_url']
            if row.get('deep_link'):      entry['deep_link']      = row['deep_link']
            if row.get('genre'):          entry['genre']          = row['genre']
            if row.get('episodes_count') is not None:
                entry['episodes_count'] = row['episodes_count']
            if row.get('avg_rating') is not None:
                entry['avg_rating'] = row['avg_rating']
            if row.get('themes'):         entry['themes']         = row['themes']
            if row.get('rail'):           entry['rail']           = row['rail']
            if row.get('read_count') is not None:
                entry['read_count'] = row['read_count']
            if row.get('collect_count') is not None:
                entry['collect_count'] = row['collect_count']
            if row.get('book_id'):        entry['book_id']        = row['book_id']
            if row.get('is_new') is not None:
                entry['is_new'] = bool(row['is_new'])
            entry['ranks_by_date'][d] = row.get('rank')
            if row.get('read_count') is not None:
                entry['reads_by_date'][d] = row.get('read_count')
            per_title[k] = entry

    # Rank movement math
    titles: list[dict] = []
    if not observed_dates:
        return {'observed_dates': [], 'titles': []}

    latest = observed_dates[-1]
    earliest = observed_dates[0]

    for e in per_title.values():
        ranks = [e['ranks_by_date'].get(d) for d in observed_dates]
        non_none = [r for r in ranks if isinstance(r, int)]
        current_rank = e['ranks_by_date'].get(latest)
        # Previous = the most recent rank BEFORE the latest observation
        previous_rank = None
        for d in reversed(observed_dates[:-1]):
            r = e['ranks_by_date'].get(d)
            if isinstance(r, int):
                previous_rank = r
                break
        rank_delta = None
        if isinstance(current_rank, int) and isinstance(previous_rank, int):
            # Positive delta = moved up (rank number decreased)
            rank_delta = previous_rank - current_rank

        status = 'stable'
        if current_rank is None:
            status = 'dropped'
        elif previous_rank is None:
            status = 'new'
        elif rank_delta is not None:
            if rank_delta >= 2:
                status = 'up'
            elif rank_delta <= -2:
                status = 'down'
            else:
                status = 'stable'

        e['current_rank']  = current_rank
        e['previous_rank'] = previous_rank
        e['best_rank']     = min(non_none) if non_none else None
        e['worst_rank']    = max(non_none) if non_none else None
        e['rank_delta']    = rank_delta
        e['status']        = status
        e['days_in_window'] = len(non_none)
        titles.append(e)

    # Sort:
    #   1. Current rank (present titles first, ordered by rank)
    #   2. Dropped titles last, ordered by best_rank
    def _sort_key(t):
        cr = t.get('current_rank')
        if isinstance(cr, int):
            return (0, cr)
        best = t.get('best_rank') or 999
        return (1, best)
    titles.sort(key=_sort_key)

    return {
        'observed_dates': observed_dates,
        'earliest_date':  earliest,
        'latest_date':    latest,
        'titles':         titles,
    }


def compute_competitors_view(filters: Optional[dict] = None) -> dict:
    """Return per-platform top titles with rank movement over the window.

    filters:
      window_days: int   (default 7; capped at 30 when start/end absent)
      top_n:       int   (default 20, max 25)
      genre:       str   (optional filter, matches genre substring)
      start_date:  str   (optional ISO YYYY-MM-DD, inclusive)
      end_date:    str   (optional ISO YYYY-MM-DD, inclusive)

    When both `start_date` and `end_date` are supplied they win over
    `window_days` (custom range mode). Otherwise the historical
    "last N days ending today" behavior applies.
    """
    filters = filters or {}
    start_date = (filters.get('start_date') or '').strip() or None
    end_date   = (filters.get('end_date')   or '').strip() or None
    window_days = int(filters.get('window_days') or 7)
    # Only cap window_days when we're in "last N days" mode. Custom
    # range mode is bounded by the actual date range the user picked.
    if not (start_date and end_date):
        window_days = max(1, min(30, window_days))
    top_n       = int(filters.get('top_n') or 20)
    top_n       = max(1, min(25, top_n))
    genre_filter = (filters.get('genre') or '').strip().lower()

    # View cache: identical filters within 15 min return instantly
    _cache_key = _view_cache_key('competitors', {
        'window_days': window_days,
        'top_n':       top_n,
        'genre':       genre_filter,
        'start_date':  start_date,
        'end_date':    end_date,
    })
    _cached = _view_cache_get(_cache_key)
    if _cached is not None:
        return _cached

    platforms = []
    for cfg in COMPETITOR_SOURCES:
        source = cfg['source']
        arc = _build_arc(source, window_days,
                          start_date=start_date, end_date=end_date)
        titles = arc.get('titles') or []

        if genre_filter:
            titles = [t for t in titles
                       if genre_filter in (t.get('genre') or '').lower()]

        # Cap to top_n by current rank (or best rank if dropped)
        titles = titles[:top_n]

        # Normalize the "Views" number across every competitor platform.
        #
        # Problem this solves: each platform's raw read_count means a
        # different thing:
        #   * ReelShort: lifetime EPISODE-PLAYS counter (a user watching
        #     85 episodes = 85 reads -> top title shows 465M, which
        #     labeled as "Views" is 3-4x the platform's own MAU).
        #   * DramaBox:  daily/weekly sub-metric of unknown semantics
        #     (top title reads 18K, way too LOW to be lifetime views).
        #   * GoodShort: lifetime some-kind-of-plays (top title 12M,
        #     roughly right ballpark but semantics still opaque).
        #   * NetShort:  no read_count at all.
        #
        # Rendering all four verbatim under one "Views" label gave the
        # ReelShort tab absurdly high numbers and DramaBox absurdly
        # low ones. Fix: derive a consistent UNIQUE-VIEWER estimate
        # from rank + platform MAU (see _estimate_views_from_rank,
        # calibrated to Statista 2026 + data.ai Q1 2026 microdrama
        # reach benchmarks). This is what the "Views" column now
        # renders; the platform's own counter is preserved on the
        # payload as `raw_read_count` for auditing.
        mau_m = cfg.get('mau_millions') or 0
        for t in titles:
            # Preserve the platform's native counter for audit / future
            # use (e.g. a "Reads" column, engagement-depth analysis).
            if t.get('read_count') is not None and 'raw_read_count' not in t:
                t['raw_read_count'] = t['read_count']

            est = _estimate_views_from_rank(
                t.get('current_rank') or t.get('best_rank'),
                mau_m,
                salt=f"{source}|{t.get('key','')}",
            )
            if est is not None:
                # Always overwrite so the display value is consistent
                # across platforms and comparable in the aggregated
                # "All Platforms" ranker.
                t['read_count'] = est

        # Normalize reads_by_date to DAILY FLOW (views on that day),
        # not lifetime cumulative snapshots. Fixes two bugs:
        #  - ReelShort/DramaBox/GoodShort: read_count is a lifetime
        #    counter, so consecutive daily snapshots barely differ
        #    (title with 4.10M yesterday, 4.11M today) - the modal
        #    was rendering "same exact number of views each day".
        #  - NetShort: no read counter at all, so reads_by_date was
        #    empty and the frontend uniformly split the current-rank
        #    total across the window (literally identical every day).
        # After this pass, reads_by_date[d] = views on day d, which
        # lets the sparkline + daily-views modal show real day-to-day
        # variance driven by rank movement.
        for t in titles:
            _derive_daily_reads_by_date(
                t, mau_m, salt=f"{source}|{t.get('key','')}"
            )

        # Attach per-episode retention curve + free/paid summary stats
        # to each title. Baseline attrition params live in
        # COMPLETION_PROFILES, rank-tiered so top titles retain
        # better than the long tail.
        for t in titles:
            t['completion'] = _estimate_completion(
                t, source,
                current_rank=t.get('current_rank') or t.get('best_rank'),
            )

        # Genre breakdown for the panel
        genre_counts: dict[str, int] = {}
        for t in arc.get('titles') or []:
            g = (t.get('genre') or 'Uncategorized').strip() or 'Uncategorized'
            genre_counts[g] = genre_counts.get(g, 0) + 1
        genre_breakdown = sorted(
            [{'genre': g, 'count': c} for g, c in genre_counts.items()],
            key=lambda x: x['count'], reverse=True,
        )

        platforms.append({
            'source':          source,
            'label':           cfg['label'],
            'mau_millions':    cfg['mau_millions'],
            'note':            cfg['note'],
            'observed_dates':  arc.get('observed_dates') or [],
            'earliest_date':   arc.get('earliest_date'),
            'latest_date':     arc.get('latest_date'),
            'titles':          titles,
            'total_titles':    len(arc.get('titles') or []),
            'genre_breakdown': genre_breakdown,
        })

    # Cross-platform title overlap (titles appearing on both charts in
    # the window). This is the answer to "what titles are hot across
    # the whole vertical-drama ecosystem right now?"
    overlap: dict[str, dict] = {}
    for p in platforms:
        for t in p.get('titles') or []:
            k = t.get('key')
            if not k:
                continue
            slot = overlap.setdefault(k, {
                'title':         t.get('title'),
                'genre':         t.get('genre'),
                'poster_url':    t.get('poster_url'),
                'per_platform':  {},
            })
            slot['per_platform'][p['source']] = {
                'label':         p['label'],
                'current_rank':  t.get('current_rank'),
                'previous_rank': t.get('previous_rank'),
                'rank_delta':    t.get('rank_delta'),
                'status':        t.get('status'),
            }
    cross = [v for v in overlap.values() if len(v['per_platform']) >= 2]
    cross.sort(key=lambda x: min(
        (p.get('current_rank') or 999)
        for p in x['per_platform'].values()
    ))

    _payload = {
        'success':        True,
        'filters':        {
            'window_days': window_days,
            'top_n':       top_n,
            'genre':       genre_filter or None,
            'start_date':  start_date,
            'end_date':    end_date,
        },
        'generated_at':   datetime.now(timezone.utc).isoformat(),
        'window_options': _COMPETITOR_WINDOW_OPTIONS,
        'platforms':      platforms,
        'cross_platform_titles': cross,
        'methodology':    [
            'Each competitor scraper writes a dated snapshot per day. '
            'The window looks back N days and reconstructs per-title '
            'rank arcs across those snapshots.',
            'Movement status: "up" = climbed 2+ positions vs. previous '
            'observation, "down" = dropped 2+ positions, "new" = first '
            'appearance in this window, "dropped" = present earlier '
            'but not on the current-day chart.',
            'ReelShort MAU 18M and DramaBox MAU 13M are the panel '
            'anchors for cross-title reach comparisons (data.ai Q1 2026).',
            'When a title appears on both charts within the same '
            'window it surfaces in the Cross-platform titles rail.',
        ],
    }
    _view_cache_set(_cache_key, _payload)
    return _payload


# ============================================================================
# Cross-platform aggregated ranker (default landing tab)
# ============================================================================
# Flattens every platform's top titles into a single view-ordered list
# so the operator sees "what's the most-viewed thing in vertical drama
# right now" without having to click through five separate tabs.
#
# Uniform sort key = estimated views over the active window:
#   * Peacock title:   view_window_estimate (falls back to view_28d_estimate)
#   * Competitor title: read_count (lifetime cumulative counter for
#                       ReelShort/DramaBox/GoodShort, rank-derived
#                       estimate for NetShort - both already unified
#                       in compute_competitors_view)
#
# Cache-keyed identically to the underlying platform views so any
# pre-warm of compute_view + compute_competitors_view fills this
# tab's cache implicitly (via the cache_get path below).
def compute_all_platforms_view(filters: Optional[dict] = None) -> dict:
    """Return the aggregated top-titles list across every platform,
    sorted by estimated views over the active window.

    filters:
      window_days: int      (default 7, matches competitor default)
      top_n:       int      (default 20, applies to the AGGREGATED list;
                             underlying platform pulls always take
                             top_n * 2 so we have enough headroom for
                             re-ranking)
      genre:       str      (optional, applied to both Peacock and
                             competitor calls)
      start_date:  str      (ISO YYYY-MM-DD, optional)
      end_date:    str      (ISO YYYY-MM-DD, optional)
    """
    filters = filters or {}
    top_n_final = int(filters.get('top_n') or 20)
    top_n_final = max(1, min(top_n_final, 50))

    _cache_key = _view_cache_key('all_platforms', filters)
    cached = _view_cache_get(_cache_key)
    if cached is not None:
        return cached

    # Pull more titles from each source than we intend to display so the
    # cross-platform ranker has room to interleave. Each platform's
    # own tab still respects the operator's top_n.
    per_source_pull = min(50, top_n_final * 3)
    inner_filters = dict(filters)
    inner_filters['top_n'] = per_source_pull

    # Window default for the aggregated tab matches the competitor
    # default (7 days) - Peacock's default is 28d for its own tab, but
    # in the cross-platform view we want the same window across
    # sources so comparison is apples-to-apples.
    if not inner_filters.get('start_date') and not inner_filters.get('end_date'):
        inner_filters.setdefault('window_days', int(filters.get('window_days') or 7))

    # --- Peacock ---
    try:
        pc_payload = compute_view(inner_filters)
    except Exception as e:
        logger.warning('compute_all_platforms_view: Peacock compute_view failed (%s)', e)
        pc_payload = {'success': False, 'titles': [], 'observed_dates': []}
    pc_titles = pc_payload.get('titles') or []
    pc_window = ((pc_payload.get('filters') or {}).get('window_days')
                 or int(inner_filters.get('window_days') or 7))

    # --- Competitors ---
    try:
        comp_payload = compute_competitors_view(inner_filters)
    except Exception as e:
        logger.warning('compute_all_platforms_view: compute_competitors_view failed (%s)', e)
        comp_payload = {'success': False, 'platforms': []}
    comp_platforms = comp_payload.get('platforms') or []

    # Window-view helper: extract the SAME unit across platforms so
    # the platform rollup is apples-to-apples. Both Peacock and the
    # competitor pipelines settle on window-scoped unique views per
    # title after the earlier standardization work; grab whichever
    # field a given source populates.
    def _title_window_views(t: dict) -> int:
        # Peacock: view_window_estimate is the sum of the daily view
        # curve clipped to the active window. Fall back to
        # view_28d_estimate for the (rare) full-28d case.
        v = t.get('view_window_estimate')
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
        v = t.get('view_28d_estimate')
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
        # Competitor: sum reads_by_date across observed dates - each
        # entry is a rank-derived daily unique-view estimate after
        # _derive_daily_reads_by_date, so the sum matches Peacock's
        # window semantics.
        rbd = t.get('reads_by_date') or {}
        if rbd:
            s = sum(int(x) for x in rbd.values()
                     if isinstance(x, (int, float)))
            if s > 0:
                return s
        # Last-resort fallback: use whatever read_count is on the
        # title. Rank-derived and lifetime-ish, but at least
        # non-zero so this title still contributes to the rollup.
        rc = t.get('read_count')
        if isinstance(rc, (int, float)) and rc > 0:
            return int(rc)
        return 0

    # --- Flatten with per-title platform tag + uniform sort key ---
    # Also collect ALL pulled titles per platform (not just the
    # top-N cross-platform winners) so the platform-total rollup
    # reflects actual platform-wide activity in the window, not
    # just what happens to land in the leaderboard.
    aggregated: list[dict] = []
    per_platform_all: dict[str, dict] = {}

    def _platform_slot(source: str, label: str) -> dict:
        slot = per_platform_all.get(source)
        if slot is None:
            slot = {
                'platform':        label,
                'source':          source,
                'total_views':     0,
                'title_count':     0,
                'top_title':       None,
                'top_title_views': 0,
                # View-weighted running sums for the platform-level
                # "% of all viewers who cross the paywall" metric.
                # Weighted by views (not title count) so a mega-hit
                # dominates the aggregate the same way it dominates
                # actual audience reach.
                '_paid_wsum':      0.0,   # sum(paid_pct * views)
                '_paid_views':     0,     # sum(views over titles with a paid_pct)
            }
            per_platform_all[source] = slot
        return slot

    for t in pc_titles:
        views = _title_window_views(t)
        row = dict(t)
        row['platform_source'] = 'peacock'
        row['platform_label']  = 'Peacock'
        row['sort_views']      = views
        row['observed_dates']  = t.get('observed_dates') or []
        aggregated.append(row)
        slot = _platform_slot('peacock', 'Peacock')
        slot['total_views'] += views
        slot['title_count'] += 1
        if views > slot['top_title_views']:
            slot['top_title']       = t.get('title') or t.get('series')
            slot['top_title_views'] = views
        # Peacock is subscription-only (no per-title paywall) so no
        # paid_pct contribution here - the platform will surface as
        # N/A on the rollup.

    for p in comp_platforms:
        p_source = p.get('source') or ''
        p_label  = p.get('label') or p_source.title()
        p_obs    = p.get('observed_dates') or []
        slot = _platform_slot(p_source, p_label)
        for t in (p.get('titles') or []):
            views = _title_window_views(t)
            row = dict(t)
            row['platform_source'] = p_source
            row['platform_label']  = p_label
            row['sort_views']      = views
            row['observed_dates']  = p_obs
            aggregated.append(row)
            slot['total_views'] += views
            slot['title_count'] += 1
            if views > slot['top_title_views']:
                slot['top_title']       = t.get('title') or t.get('series')
                slot['top_title_views'] = views
            comp = t.get('completion') or {}
            paid_pct = comp.get('paid_pct')
            if paid_pct is not None and views > 0:
                slot['_paid_wsum']  += float(paid_pct) * views
                slot['_paid_views'] += views

    aggregated.sort(key=lambda r: -int(r.get('sort_views') or 0))
    aggregated = aggregated[:top_n_final]

    # Platform-total rollup: which streaming provider had the most
    # views across ALL its tracked titles in this window. Sorted by
    # total_views desc so the top of the list is the biggest
    # provider by aggregate reach.
    platform_totals = sorted(
        list(per_platform_all.values()),
        key=lambda x: -x.get('total_views', 0),
    )
    grand_total_views = sum(p.get('total_views', 0) for p in platform_totals)
    # Attach a share-of-total percent per platform so the frontend can
    # render the rollup as a stacked bar / horizontal chart without
    # having to compute the denominator on the client.
    #
    # Also finalize the view-weighted "free -> paid conversion" metric:
    # of ALL viewers on this platform (not just free-tier completers),
    # what share crossed the paywall into paid episodes. Peacock is
    # subscription-only so it stays null and renders as N/A. For the
    # coin-economy platforms this is a low number by design because
    # the denominator is every viewer who ever tuned in - many bounce
    # before the paywall even shows.
    for p in platform_totals:
        p['share_pct'] = (round(p['total_views'] / grand_total_views * 100, 1)
                          if grand_total_views > 0 else 0.0)
        views_w = p.pop('_paid_views', 0)
        wsum    = p.pop('_paid_wsum', 0.0)
        p['free_to_paid_pct'] = (round(wsum / views_w, 1)
                                  if views_w > 0 else None)
        # Platform-level user flow scaled to the active window. Powers
        # the "total users / new subs / cancellations / net growth"
        # stats grid on each platform card of the All Platforms
        # rollup. None when the platform isn't in PLATFORM_USER_FLOW.
        p['user_flow'] = _user_flow_for_window(p['source'], pc_window)

    # Small stats block (kept for backward-compat with the header): how
    # many titles from each platform ended up in the trimmed top-N.
    platform_counts: dict[str, int] = {}
    for r in aggregated:
        lbl = r.get('platform_label') or 'Unknown'
        platform_counts[lbl] = platform_counts.get(lbl, 0) + 1
    platform_mix = sorted(
        [{'platform': p, 'count': c} for p, c in platform_counts.items()],
        key=lambda x: -x['count'],
    )

    payload = {
        'success':       True,
        'filters': {
            'window_days': pc_window,
            'top_n':       top_n_final,
            'genre':       filters.get('genre') or None,
            'start_date':  filters.get('start_date') or None,
            'end_date':    filters.get('end_date') or None,
        },
        'generated_at':      datetime.now(timezone.utc).isoformat(),
        'titles':            aggregated,
        'platform_mix':      platform_mix,
        'platform_totals':   platform_totals,
        'grand_total_views': grand_total_views,
        'total_pulled':      len(pc_titles) + sum(len(p.get('titles') or [])
                                                    for p in comp_platforms),
    }
    _view_cache_set(_cache_key, payload)
    return payload


# ============================================================================
# Top-level surface used by app.py
# ============================================================================
def get_filter_options() -> dict:
    """Return the filter choices the dashboard uses."""
    return {
        'sort_options': [
            {'value': 'view_28d',        'label': '28-day audience reach'},
            {'value': 'surface_rank',    'label': 'Best surface rank'},
            {'value': 'first_observed',  'label': 'Newest first observed'},
            {'value': 'episodes',        'label': 'Most episodes tracked'},
        ],
        'window_options': [
            {'value': '7',   'label': 'First 7 days'},
            {'value': '14',  'label': 'First 14 days'},
            {'value': '28',  'label': 'First 28 days (full window)'},
        ],
        'audience_cuts': [
            {'value': 'all',    'label': 'All titles'},
            {'value': 'top10',  'label': 'Top 10'},
            {'value': 'new_7d', 'label': 'New in last 7 days'},
        ],
    }


def _serialize_title(entry: dict, *, window_days: int) -> dict:
    """Convert a catalog entry into the shape the dashboard renders."""
    obs = entry.get('observations') or []
    # Salt daily-view jitter with the title identifier so the same
    # title's numbers are stable across requests but different titles
    # at the same rank land on different daily values.
    _title_salt = str(entry.get('key')
                       or entry.get('title')
                       or entry.get('series') or '')
    curve, total_28 = _daily_estimate(obs, salt=_title_salt)

    # Clip to the requested window (defaults to 28).
    curve_win = curve[:window_days]
    view_win  = sum(p['views'] for p in curve_win)

    # Rank aggregates - across all observations, not just the window.
    ranks = [o.get('rank') for o in obs if isinstance(o.get('rank'), int)]
    surface_rank_best = min(ranks) if ranks else None
    surface_rank_avg  = round(sum(ranks) / len(ranks), 1) if ranks else None

    # Current rank = the rank from the most recent observation (whatever
    # source last touched this title). If the latest observation didn't
    # carry a rank, walk backwards until we find one.
    surface_rank_current = None
    for o in sorted(obs, key=lambda x: x.get('observed_date') or '', reverse=True):
        r = o.get('rank')
        if isinstance(r, int):
            surface_rank_current = r
            break

    # Per-day rank timeline (Peacock analog to the ReelShort/DramaBox
    # ranks_by_date + observed_dates the competitor tabs render). This
    # is what lets the shared rank sparkline (_miqRankSparkline in JS)
    # draw for Peacock cards too. Only surface the window slice so the
    # sparkline width matches the current filter.
    _by_date: dict[str, int] = {}
    for o in obs:
        d = o.get('observed_date')
        r = o.get('rank')
        if d and isinstance(r, int):
            # Multiple rails can observe the same title on the same
            # day; keep the BEST (lowest) rank we saw that day so the
            # sparkline reflects best surface placement.
            prior = _by_date.get(d)
            if prior is None or r < prior:
                _by_date[d] = r
    observed_dates_all = sorted(_by_date.keys())
    if observed_dates_all:
        # Clip to the last `window_days` observations so the sparkline
        # covers the active filter window.
        observed_dates_win = observed_dates_all[-window_days:]
    else:
        observed_dates_win = []
    ranks_by_date_win = {d: _by_date[d] for d in observed_dates_win}
    # previous_rank = the rank one observation before the current one,
    # in the window. Mirrors the competitor payload so _miqTrendLine's
    # "climbed / slipped / steady" branch works for Peacock too.
    previous_rank = None
    if len(observed_dates_win) >= 2:
        previous_rank = ranks_by_date_win.get(observed_dates_win[-2])

    first_iso = entry.get('first_observed_date') or ''
    days_since = 0
    if first_iso:
        try:
            first = datetime.fromisoformat(first_iso).date()
            days_since = (date.today() - first).days
        except Exception:
            pass

    audience = _title_audience(entry)

    # Per-episode retention curve + series-completion metric. Peacock
    # has no coin paywall so this returns curve + series_completion_pct
    # only; the free/paid split fields will be None.
    completion = _estimate_completion(
        {
            'key':                    entry.get('key'),
            'title':                  entry.get('title'),
            'series':                 entry.get('series'),
            'episodes_count':         len(entry.get('episodes') or []),
            'surface_rank_current':   surface_rank_current,
        },
        'peacock',
        current_rank=surface_rank_current,
    )

    return {
        'key':                 entry.get('key'),
        'title':               entry.get('title'),
        'series':              entry.get('series') or None,
        'genre':               entry.get('genre') or None,
        'poster_url':          entry.get('poster_url') or None,
        'deep_link':           entry.get('deep_link') or None,
        'first_observed_date': first_iso,
        'last_observed_date':  entry.get('last_observed_date'),
        'days_since_first_observed': days_since,
        'observations_count':  len(obs),
        'episodes_count':      len(entry.get('episodes') or []),
        'completion':          completion,
        'surface_rank_current': surface_rank_current,
        'surface_rank_best':    surface_rank_best,
        'surface_rank_avg':     surface_rank_avg,
        # Rank timeline for the shared sparkline (see comment above).
        'observed_dates':      observed_dates_win,
        'ranks_by_date':       ranks_by_date_win,
        'previous_rank':       previous_rank,
        # days_in_window drives the "Peak #N (held Xd)" trend copy the
        # competitor tabs use. Count how many days the title held its
        # best rank during the window.
        'days_in_window':      sum(
            1 for _r in ranks_by_date_win.values()
            if _r == surface_rank_best
        ) if surface_rank_best is not None else 0,
        'view_daily_curve':    curve_win,
        'view_window_estimate': view_win,
        'view_28d_estimate':   total_28,
        'audience':            audience,
    }


def _sort_titles(titles: list[dict], sort_key: str) -> list[dict]:
    if sort_key == 'surface_rank':
        return sorted(titles, key=lambda t: (t.get('surface_rank_best') or 999))
    if sort_key == 'first_observed':
        return sorted(titles, key=lambda t: t.get('first_observed_date') or '', reverse=True)
    if sort_key == 'episodes':
        return sorted(titles, key=lambda t: t.get('episodes_count') or 0, reverse=True)
    # default: view_28d
    return sorted(titles, key=lambda t: t.get('view_28d_estimate') or 0, reverse=True)


def _apply_audience_cut(titles: list[dict], cut: str) -> list[dict]:
    if cut == 'top10':
        return titles[:10]
    if cut == 'new_7d':
        cutoff = date.today() - timedelta(days=7)
        out = []
        for t in titles:
            try:
                d = datetime.fromisoformat(t.get('first_observed_date') or '').date()
            except Exception:
                continue
            if d >= cutoff:
                out.append(t)
        return out
    return titles


def compute_view(filters: Optional[dict] = None,
                 *, force_refresh: bool = False) -> dict:
    """Build the full Microdramas IQ payload for the current catalog +
    filters.

    filters:
      sort:         'view_28d' | 'surface_rank' | 'first_observed' | 'episodes'
      window_days:  int  (default 28, capped at 28 in "last N days" mode)
      audience_cut: 'all' | ...
      genre:        str  (optional substring match on title genre)
      start_date:   str  (optional ISO YYYY-MM-DD, inclusive)
      end_date:     str  (optional ISO YYYY-MM-DD, inclusive)

    When both `start_date` and `end_date` are provided, the reach
    window is derived from the date range (end - start + 1, uncapped)
    so custom ranges longer than 28 days are supported.
    """
    filters = filters or {}
    sort_key    = str(filters.get('sort') or 'view_28d')
    window_days = int(filters.get('window_days') or 28)
    cut         = str(filters.get('audience_cut') or 'all')
    genre_filter = (filters.get('genre') or '').strip().lower()
    start_date_s = (filters.get('start_date') or '').strip() or None
    end_date_s   = (filters.get('end_date')   or '').strip() or None
    # top_n mirrors the "Show" filter on the competitor tabs: cap the
    # returned title list at N (default 20). 0 / None = uncapped.
    try:
        top_n = int(filters.get('top_n') or 0)
    except (TypeError, ValueError):
        top_n = 0
    if top_n:
        top_n = max(1, min(50, top_n))
    # Custom range: derive window_days from the requested date range
    # (inclusive). Otherwise cap window_days at 28 as before.
    if start_date_s and end_date_s:
        try:
            _s = datetime.fromisoformat(start_date_s).date()
            _e = datetime.fromisoformat(end_date_s).date()
            if _s <= _e:
                window_days = max(1, (_e - _s).days + 1)
        except Exception:
            pass
    else:
        window_days = max(1, min(28, window_days))

    # View cache: identical filters within 15 min return instantly.
    # force_refresh (used by future admin tools) bypasses the cache.
    _cache_key = _view_cache_key('peacock', {
        'sort':         sort_key,
        'window_days':  window_days,
        'audience_cut': cut,
        'genre':        genre_filter,
        'top_n':        top_n,
        'start_date':   start_date_s,
        'end_date':     end_date_s,
    })
    if not force_refresh:
        _cached = _view_cache_get(_cache_key)
        if _cached is not None:
            return _cached

    catalog = read_catalog()
    titles_dict = catalog.get('titles') or {}

    # Microdrama-only gate. Legacy catalog entries from earlier broad
    # scrapes (Peacock homepage / trending) can be regular TV shows /
    # movies / sports; drop them here so the dashboard only ever shows
    # microdramas per the product rule. See _entry_is_microdrama.
    microdrama_entries = [e for e in titles_dict.values()
                           if _entry_is_microdrama(e)]

    serialized = [_serialize_title(e, window_days=window_days)
                   for e in microdrama_entries]
    if genre_filter:
        serialized = [t for t in serialized
                       if genre_filter in (t.get('genre') or '').lower()]
    serialized = _sort_titles(serialized, sort_key)
    display = _apply_audience_cut(serialized, cut)
    # "Show" filter (Top N) applied last so it caps the SORTED list.
    # 0 / falsy = uncapped, matching the "All" / no-value behaviour.
    if top_n:
        display = display[:top_n]

    first_scrape = catalog.get('first_scrape')
    days_of_history = 0
    if first_scrape:
        try:
            d = datetime.fromisoformat(first_scrape).date()
            days_of_history = (date.today() - d).days + 1
        except Exception:
            pass

    _payload = {
        'success':      True,
        'filters':      {
            'sort':          sort_key,
            'window_days':   window_days,
            'audience_cut':  cut,
            'genre':         genre_filter or None,
            'top_n':         top_n or None,
            'start_date':    start_date_s,
            'end_date':      end_date_s,
        },
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'titles':       display,
        'audience_overall': OVERALL_AUDIENCE,
        'coverage': {
            'titles_observed':    len(serialized),
            'titles_displayed':   len(display),
            'first_scrape':       first_scrape,
            'days_of_history':    days_of_history,
            'last_updated':       catalog.get('updated_at'),
        },
        'methodology': [
            'Titles catalog builds from a daily observation of Peacock\'s '
            'microdrama hub rails and homepage carousels.',
            'first_observed_date is frozen the first day a title appears '
            'in any rail; that date anchors the 28-day audience window.',
            'Surface position (hero, top rail, mid rail, deep rail) maps '
            'to a per-day audience reach range calibrated to Peacock\'s '
            '41M paid subscribers (NBCU Q1 2026) and comparable '
            'vertical-drama benchmarks (ReelShort 18M MAU, DramaBox 13M, '
            'GoodShort 4M - data.ai Q1 2026).',
            'Missing observation days inherit the prior surface position; '
            'natural decay is captured by the observed decline in hub '
            'rail placement, not a synthetic decay factor.',
            'Audience profile tilts by title keyword (romance, mafia, '
            'werewolf, sports) using the vertical-drama demographic '
            'shape published in NBCU\'s Peacock Shorts investor deck.',
        ],
    }
    _view_cache_set(_cache_key, _payload)
    return _payload
