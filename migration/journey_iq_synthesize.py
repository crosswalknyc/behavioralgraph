"""
journey_iq_synthesize.py — Claude-driven synthetic-data + box-office scaling
for Digital Journey IQ runs on movies.

Two responsibilities:

1. **Box-office scaling.** Given the panel cohort size (real converters
   observed in the clickstream) and the movie's US box-office gross, scale
   COUNT fields (users, reach, converters, ...) up to the implied total
   audience. Percentages are NEVER scaled — only counts. The scaling factor
   is `implied_audience / panel_converters`.

   implied_audience = (box_office_usd / avg_ticket_price) * audience_factor
   where audience_factor defaults to 0.775 — the midpoint of the 70-85%
   single-ticket-per-buyer band the user specified (matinee/family
   double-counts vs solo).

2. **LLM synthesis.** When the cohort is too small to draw conclusions,
   ask Claude to estimate a plausible path-to-purchase + touchpoint mix
   for a movie of this scale + talent profile. Returns the same JSON shape
   as `_aggregate_path_to_purchase` / `_aggregate_touchpoints` so the
   dashboard renders it without conditional logic.

Dashboard mode toggle (real / modeled / blended) lives in the frontend —
this module just produces the synthetic data and the scaling factors; the
adapter at the bottom builds a "blended" view by combining real and
modeled at field level (real where N >= 25, modeled where sparse).
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Optional

try:
    from migration.claude_client import claude_messages, is_hybrid_enabled, get_claude_client
except ImportError:
    try:
        from claude_client import claude_messages, is_hybrid_enabled, get_claude_client  # type: ignore
    except ImportError:
        claude_messages = None       # type: ignore
        is_hybrid_enabled = None     # type: ignore
        get_claude_client = None     # type: ignore


# ── Constants ────────────────────────────────────────────────────────────────

# Supported target types. 'general' means no scaling / no synth (legacy
# behaviour). The other three each have their own implied-audience formula
# and their own Claude synthesis prompt + fallback fixture (different
# journeys: movie = trailer/showtime/ticket; website = SEO/direct/conversion;
# tv_show = trailer/review/streaming-platform/first-watch).
TARGET_TYPES = ('general', 'movie', 'website', 'tv_show')

DEFAULT_TICKET_PRICE      = 15.00
DEFAULT_AUDIENCE_FRACTION = 0.775   # midpoint of 70-85% single-ticket-per-buyer

# Website mode: monthly_uniques × months × WEBSITE_DEDUP_FACTOR
# (0.40 because ~60% of a site's monthly uniques are repeat visitors who
# would already have been counted in a previous month — this gives us the
# distinct-visitor count over a multi-month window without double counting).
WEBSITE_DEDUP_FACTOR      = 0.40

SPARSE_COHORT_THRESHOLD   = 25      # < this many real converters → blend in modeled

# Canonical movie touchpoint mix used as fallback when Claude is offline.
# Numbers chosen to roughly match a tentpole release's path-to-purchase
# (Marvel-tier movies have ~85% trailer reach, ~70% search, etc.).
_FALLBACK_TOUCHPOINTS = [
    {'label': 'TRAILER',           'reach_pct': 78.0, 'avg_days_to_conversion': 21.0,  'avg_touches_per_user': 2.4, 'lift_pct': 65.0},
    {'label': 'ORGANIC_SEARCH',    'reach_pct': 72.0, 'avg_days_to_conversion': 7.0,   'avg_touches_per_user': 3.1, 'lift_pct': 120.0},
    {'label': 'SOCIAL_YOUTUBE',    'reach_pct': 58.0, 'avg_days_to_conversion': 18.0,  'avg_touches_per_user': 2.0, 'lift_pct': 45.0},
    {'label': 'SOCIAL_TIKTOK',     'reach_pct': 41.0, 'avg_days_to_conversion': 12.0,  'avg_touches_per_user': 3.8, 'lift_pct': 38.0},
    {'label': 'SOCIAL_INSTAGRAM',  'reach_pct': 37.0, 'avg_days_to_conversion': 14.0,  'avg_touches_per_user': 2.2, 'lift_pct': 32.0},
    {'label': 'PRESS',             'reach_pct': 28.0, 'avg_days_to_conversion': 22.0,  'avg_touches_per_user': 1.6, 'lift_pct': 18.0},
    {'label': 'REVIEW',            'reach_pct': 24.0, 'avg_days_to_conversion': 4.0,   'avg_touches_per_user': 2.1, 'lift_pct': 96.0},
    {'label': 'SHOWTIME_LOOKUP',   'reach_pct': 86.0, 'avg_days_to_conversion': 1.5,   'avg_touches_per_user': 1.8, 'lift_pct': 310.0},
    {'label': 'GOOGLE_REVIEW',     'reach_pct': 18.0, 'avg_days_to_conversion': 3.0,   'avg_touches_per_user': 1.4, 'lift_pct': 72.0},
    {'label': 'TICKETING',         'reach_pct': 92.0, 'avg_days_to_conversion': 0.5,   'avg_touches_per_user': 1.2, 'lift_pct': None},
    {'label': 'PAID_AD',           'reach_pct': 33.0, 'avg_days_to_conversion': 15.0,  'avg_touches_per_user': 4.6, 'lift_pct': 26.0},
    {'label': 'TALENT_MENTION',    'reach_pct': 22.0, 'avg_days_to_conversion': 19.0,  'avg_touches_per_user': 1.7, 'lift_pct': 41.0},
    {'label': 'CREATOR_INFLUENCER','reach_pct': 19.0, 'avg_days_to_conversion': 11.0,  'avg_touches_per_user': 1.9, 'lift_pct': 58.0},
    {'label': 'BRAND_PARTNERSHIP', 'reach_pct': 11.0, 'avg_days_to_conversion': 25.0,  'avg_touches_per_user': 1.4, 'lift_pct': 22.0},
    {'label': 'SOUNDTRACK',        'reach_pct':  9.0, 'avg_days_to_conversion': 17.0,  'avg_touches_per_user': 1.5, 'lift_pct': 14.0},
]

_FALLBACK_PATH_COLUMN_MIX = [
    # Step -10 ... Step -1: rough "what fraction of converters had which
    # channel at each step before purchase" — calibrated against the
    # Bridgestone/BSFS deck observation that 60-70% of converters enter via
    # search, then funnel through trailer → review → ticket lookup → purchase.
    {-10: {'ORGANIC_SEARCH': 0.55, 'TRAILER': 0.20, 'SOCIAL_YOUTUBE': 0.15, 'PRESS': 0.10}},
    {-9:  {'ORGANIC_SEARCH': 0.45, 'TRAILER': 0.25, 'SOCIAL_YOUTUBE': 0.18, 'SOCIAL_TIKTOK': 0.12}},
    {-8:  {'TRAILER': 0.35, 'ORGANIC_SEARCH': 0.30, 'SOCIAL_TIKTOK': 0.18, 'SOCIAL_INSTAGRAM': 0.17}},
    {-7:  {'TRAILER': 0.30, 'SOCIAL_YOUTUBE': 0.25, 'SOCIAL_TIKTOK': 0.20, 'PRESS': 0.15, 'PAID_AD': 0.10}},
    {-6:  {'TALENT_MENTION': 0.25, 'PRESS': 0.22, 'REVIEW': 0.20, 'TRAILER': 0.18, 'CREATOR_INFLUENCER': 0.15}},
    {-5:  {'CREATOR_INFLUENCER': 0.28, 'TALENT_MENTION': 0.22, 'BRAND_PARTNERSHIP': 0.15, 'TRAILER': 0.20, 'REVIEW': 0.15}},
    {-4:  {'REVIEW': 0.32, 'PRESS': 0.25, 'GOOGLE_REVIEW': 0.18, 'TRAILER': 0.13, 'SOCIAL_INSTAGRAM': 0.12}},
    {-3:  {'GOOGLE_REVIEW': 0.30, 'REVIEW': 0.28, 'ORGANIC_SEARCH': 0.22, 'PRESS': 0.20}},
    {-2:  {'SHOWTIME_LOOKUP': 0.55, 'GOOGLE_REVIEW': 0.20, 'ORGANIC_SEARCH': 0.15, 'REVIEW': 0.10}},
    {-1:  {'TICKETING': 0.78, 'SHOWTIME_LOOKUP': 0.15, 'ORGANIC_SEARCH': 0.07}},
]

_FALLBACK_TOP_PATHS = [
    {'path': ['ORGANIC_SEARCH', 'TRAILER', 'REVIEW', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'],         'pct': 18.0},
    {'path': ['TRAILER', 'SOCIAL_TIKTOK', 'REVIEW', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'],          'pct': 13.0},
    {'path': ['ORGANIC_SEARCH', 'TRAILER', 'CREATOR_INFLUENCER', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'], 'pct': 9.5},
    {'path': ['ORGANIC_SEARCH', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'],                              'pct': 8.0},
    {'path': ['SOCIAL_YOUTUBE', 'TRAILER', 'REVIEW', 'TICKETING', 'CONVERSION'],                            'pct': 7.0},
    {'path': ['TALENT_MENTION', 'TRAILER', 'PRESS', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'],          'pct': 6.5},
    {'path': ['PRESS', 'REVIEW', 'GOOGLE_REVIEW', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'],            'pct': 5.0},
    {'path': ['SOCIAL_TIKTOK', 'TRAILER', 'TICKETING', 'CONVERSION'],                                       'pct': 4.5},
    {'path': ['BRAND_PARTNERSHIP', 'TRAILER', 'SHOWTIME_LOOKUP', 'TICKETING', 'CONVERSION'],                'pct': 3.5},
    {'path': ['CREATOR_INFLUENCER', 'SOCIAL_TIKTOK', 'TICKETING', 'CONVERSION'],                            'pct': 3.0},
]


# ── Public: scaling math ─────────────────────────────────────────────────────

def compute_implied_audience(
    *,
    box_office_millions: float = 0.0,
    ticket_price: float = DEFAULT_TICKET_PRICE,
    audience_fraction: float = DEFAULT_AUDIENCE_FRACTION,
) -> int:
    """Movie: box office $ → implied # of distinct ticket-buyers.

    box_office_usd / ticket_price gives total tickets sold; multiplying by
    audience_fraction (≈0.77) discounts for repeat viewers / family-of-4
    purchases-by-one-person. Returns an int. Returns 0 on bad input.
    """
    try:
        bo = float(box_office_millions or 0) * 1_000_000.0
        tp = max(float(ticket_price or DEFAULT_TICKET_PRICE), 1.0)
        af = max(0.05, min(float(audience_fraction or DEFAULT_AUDIENCE_FRACTION), 1.0))
        return int(round((bo / tp) * af))
    except Exception:
        return 0


def compute_website_implied_audience(
    *,
    monthly_visitors_millions: float,
    date_range_days: int,
    dedup_factor: float = WEBSITE_DEDUP_FACTOR,
) -> int:
    """Website: distinct US visitors expected over the run's date window.

    Formula: monthly_uniques × (window_days / 30) × dedup_factor
    The dedup_factor (~0.40) discounts repeat visitors who would already
    have been counted in earlier months — so a 12.3M monthly site running
    a 90-day analysis gets 12.3M × 3 × 0.4 ≈ 14.8M distinct visitors,
    not 36.9M (the linear, double-counted number).
    """
    try:
        mv = float(monthly_visitors_millions or 0) * 1_000_000.0
        days = max(1, int(date_range_days or 30))
        months = days / 30.0
        df = max(0.05, min(float(dedup_factor or WEBSITE_DEDUP_FACTOR), 1.0))
        return int(round(mv * months * df))
    except Exception:
        return 0


def compute_tv_show_implied_audience(
    *,
    us_viewers_millions: float,
) -> int:
    """TV show: pass-through of the AI-researched (or user-supplied)
    US viewer count. SubscriberIQ's AI lookup already gives us the
    cumulative distinct US viewer number, so no further scaling is
    needed — we just convert millions → ints."""
    try:
        return int(round(float(us_viewers_millions or 0) * 1_000_000.0))
    except Exception:
        return 0


def compute_implied_audience_for_type(
    *,
    target_type: str,
    box_office_millions: float = 0.0,
    ticket_price: float = DEFAULT_TICKET_PRICE,
    monthly_visitors_millions: float = 0.0,
    date_range_days: int = 30,
    us_viewers_millions: float = 0.0,
) -> int:
    """Single entry point: dispatch to the right formula based on type.
    Returns 0 for 'general' or unknown types (no scaling)."""
    t = (target_type or 'general').strip().lower()
    if t == 'movie':
        return compute_implied_audience(
            box_office_millions=box_office_millions,
            ticket_price=ticket_price,
        )
    if t == 'website':
        return compute_website_implied_audience(
            monthly_visitors_millions=monthly_visitors_millions,
            date_range_days=date_range_days,
        )
    if t == 'tv_show':
        return compute_tv_show_implied_audience(
            us_viewers_millions=us_viewers_millions,
        )
    return 0


def compute_scaling_factor(
    *,
    implied_audience: int,
    panel_converters: int,
) -> float:
    """How much we need to multiply panel counts by to match implied audience.

    Returns 1.0 when there's nothing to scale (no panel converters or no
    implied audience) so the caller can pass it through unconditionally.
    """
    if implied_audience <= 0 or panel_converters <= 0:
        return 1.0
    return float(implied_audience) / float(panel_converters)


def scale_summary_counts(summary: dict, scaling_factor: float) -> dict:
    """Return a deep-ish copy of the summary with count fields scaled up.

    Percentages are LEFT ALONE — they're rates, not counts. We only multiply
    fields that represent absolute user/event counts. The dashboard renders
    this as the "Scaled to BO" view alongside the raw panel observation.
    """
    if scaling_factor is None or scaling_factor <= 1.0 + 1e-9:
        return summary  # nothing to scale; pass-through

    def _scale_int(v):
        if isinstance(v, (int, float)) and v >= 0:
            return int(round(v * scaling_factor))
        return v

    out = dict(summary)
    out['meta'] = dict(summary.get('meta') or {})
    out['meta']['scaled_to_box_office'] = True
    out['meta']['scaling_factor'] = round(scaling_factor, 2)

    # KPIs: scale the count-shaped ones, leave rate-shaped ones.
    kpis = dict(summary.get('kpis') or {})
    for k in ('total_users', 'converted_users'):
        if k in kpis:
            kpis[k] = _scale_int(kpis[k])
    out['kpis'] = kpis

    # Touchpoints: scale reach + converters_reached on each row, plus the
    # cohort_size / converters summary fields and the touch_distribution
    # bucket counts. reach_pct / lift_pct / cadence stay as-is.
    tp = dict(summary.get('touchpoints') or {})
    if 'cohort_size' in tp: tp['cohort_size'] = _scale_int(tp['cohort_size'])
    if 'converters'  in tp: tp['converters']  = _scale_int(tp['converters'])
    new_rows = []
    for r in (tp.get('rows') or []):
        rr = dict(r)
        for k in ('reach', 'converters_reached'):
            if k in rr: rr[k] = _scale_int(rr[k])
        new_rows.append(rr)
    tp['rows'] = new_rows
    new_overlap = []
    for p in (tp.get('overlap') or []):
        pp = dict(p)
        for k in ('users', 'converters'):
            if k in pp: pp[k] = _scale_int(pp[k])
        new_overlap.append(pp)
    tp['overlap'] = new_overlap
    new_dist = []
    for b in (tp.get('touch_distribution') or []):
        bb = dict(b)
        for k in ('converters', 'non_converters', 'total'):
            if k in bb: bb[k] = _scale_int(bb[k])
        new_dist.append(bb)
    tp['touch_distribution'] = new_dist
    out['touchpoints'] = tp

    # Path to purchase: scale column user counts.
    ptp = dict(summary.get('path_to_purchase') or {})
    if 'cohort_size' in ptp: ptp['cohort_size'] = _scale_int(ptp['cohort_size'])
    new_cols = []
    for col in (ptp.get('columns') or []):
        cc = dict(col)
        cc['users'] = _scale_int(cc.get('users', 0))
        new_top_labels = []
        for tl in (cc.get('top_labels') or []):
            ttl = dict(tl)
            ttl['users'] = _scale_int(ttl.get('users', 0))
            new_top_labels.append(ttl)
        cc['top_labels'] = new_top_labels
        new_top_hosts = []
        for th in (cc.get('top_hosts') or []):
            tth = dict(th)
            tth['users'] = _scale_int(tth.get('users', 0))
            new_top_hosts.append(tth)
        cc['top_hosts'] = new_top_hosts
        new_cols.append(cc)
    ptp['columns'] = new_cols
    new_paths = []
    for p in (ptp.get('top_paths') or []):
        pp = dict(p)
        pp['users'] = _scale_int(pp.get('users', 0))
        new_paths.append(pp)
    ptp['top_paths'] = new_paths
    out['path_to_purchase'] = ptp

    # Cuts: scale users + converted in each bucket.
    new_cuts: dict[str, list] = {}
    for axis, buckets in (summary.get('cuts') or {}).items():
        new_buckets = []
        for b in (buckets or []):
            bb = dict(b)
            for k in ('users', 'converted'):
                if k in bb: bb[k] = _scale_int(bb[k])
            new_buckets.append(bb)
        new_cuts[axis] = new_buckets
    out['cuts'] = new_cuts

    # Clusters: same shape as cuts buckets.
    new_clusters = []
    for c in (summary.get('clusters') or []):
        cc = dict(c)
        for k in ('users', 'converted'):
            if k in cc: cc[k] = _scale_int(cc[k])
        new_clusters.append(cc)
    out['clusters'] = new_clusters

    # Keywords + post_hosts: scale users
    out['keywords'] = [
        dict(k, users=_scale_int(k.get('users', 0)))
        for k in (summary.get('keywords') or [])
    ]
    out['post_hosts'] = [
        dict(h, users=_scale_int(h.get('users', 0)))
        for h in (summary.get('post_hosts') or [])
    ]

    return out


# ── Per-type fallback fixtures (website, tv_show) ────────────────────────────
# Movie fixture above. These two are sized to be realistic enough that the
# dashboard renders sensible numbers when Claude is offline. They have
# entirely different shapes — a website conversion path doesn't look like a
# movie ticket purchase, and a TV-show first-watch doesn't either.

_FALLBACK_TOUCHPOINTS_WEBSITE = [
    {'label': 'ORGANIC_SEARCH',    'reach_pct': 62.0, 'avg_days_to_conversion': 4.0,   'avg_touches_per_user': 2.8, 'lift_pct': 85.0},
    {'label': 'DIRECT',            'reach_pct': 48.0, 'avg_days_to_conversion': 2.0,   'avg_touches_per_user': 4.2, 'lift_pct': 140.0},
    {'label': 'REFERRAL',          'reach_pct': 32.0, 'avg_days_to_conversion': 6.0,   'avg_touches_per_user': 1.8, 'lift_pct': 42.0},
    {'label': 'SOCIAL_INSTAGRAM',  'reach_pct': 27.0, 'avg_days_to_conversion': 8.0,   'avg_touches_per_user': 2.1, 'lift_pct': 28.0},
    {'label': 'SOCIAL_TIKTOK',     'reach_pct': 24.0, 'avg_days_to_conversion': 9.0,   'avg_touches_per_user': 2.6, 'lift_pct': 22.0},
    {'label': 'SOCIAL_YOUTUBE',    'reach_pct': 21.0, 'avg_days_to_conversion': 11.0,  'avg_touches_per_user': 1.7, 'lift_pct': 18.0},
    {'label': 'EMAIL',             'reach_pct': 35.0, 'avg_days_to_conversion': 3.0,   'avg_touches_per_user': 3.1, 'lift_pct': 110.0},
    {'label': 'PAID_AD',           'reach_pct': 29.0, 'avg_days_to_conversion': 5.0,   'avg_touches_per_user': 3.4, 'lift_pct': 24.0},
    {'label': 'PRESS',             'reach_pct': 14.0, 'avg_days_to_conversion': 12.0,  'avg_touches_per_user': 1.4, 'lift_pct': 16.0},
    {'label': 'REVIEW',            'reach_pct': 19.0, 'avg_days_to_conversion': 7.0,   'avg_touches_per_user': 1.6, 'lift_pct': 38.0},
    {'label': 'COMPARISON',        'reach_pct': 23.0, 'avg_days_to_conversion': 4.0,   'avg_touches_per_user': 2.2, 'lift_pct': 64.0},
    {'label': 'PRICING_PAGE',      'reach_pct': 41.0, 'avg_days_to_conversion': 2.0,   'avg_touches_per_user': 1.9, 'lift_pct': 195.0},
    {'label': 'SIGNUP',            'reach_pct': 96.0, 'avg_days_to_conversion': 0.3,   'avg_touches_per_user': 1.1, 'lift_pct': None},
    {'label': 'CHATBOT',           'reach_pct': 8.0,  'avg_days_to_conversion': 1.5,   'avg_touches_per_user': 1.3, 'lift_pct': 12.0},
    {'label': 'AFFILIATE',         'reach_pct': 11.0, 'avg_days_to_conversion': 5.0,   'avg_touches_per_user': 1.5, 'lift_pct': 31.0},
]

_FALLBACK_PATH_COLUMN_MIX_WEBSITE = [
    {-10: {'ORGANIC_SEARCH': 0.50, 'SOCIAL_TIKTOK': 0.20, 'PAID_AD':   0.20, 'REFERRAL':       0.10}},
    {-9:  {'ORGANIC_SEARCH': 0.40, 'SOCIAL_INSTAGRAM': 0.22, 'REFERRAL': 0.20, 'SOCIAL_YOUTUBE': 0.18}},
    {-8:  {'ORGANIC_SEARCH': 0.35, 'REFERRAL': 0.25, 'PRESS': 0.20, 'REVIEW':                  0.20}},
    {-7:  {'COMPARISON': 0.32, 'REVIEW': 0.28, 'ORGANIC_SEARCH': 0.22, 'AFFILIATE':            0.18}},
    {-6:  {'DIRECT': 0.30, 'EMAIL': 0.25, 'COMPARISON': 0.25, 'ORGANIC_SEARCH':                0.20}},
    {-5:  {'EMAIL': 0.32, 'DIRECT': 0.28, 'PRICING_PAGE': 0.22, 'COMPARISON':                  0.18}},
    {-4:  {'PRICING_PAGE': 0.40, 'COMPARISON': 0.25, 'REVIEW': 0.20, 'CHATBOT':                0.15}},
    {-3:  {'PRICING_PAGE': 0.50, 'DIRECT': 0.25, 'CHATBOT': 0.15, 'EMAIL':                     0.10}},
    {-2:  {'DIRECT': 0.45, 'PRICING_PAGE': 0.30, 'EMAIL': 0.15, 'CHATBOT':                     0.10}},
    {-1:  {'SIGNUP': 0.85, 'DIRECT': 0.10, 'PRICING_PAGE':                                     0.05}},
]

_FALLBACK_TOP_PATHS_WEBSITE = [
    {'path': ['ORGANIC_SEARCH', 'COMPARISON', 'PRICING_PAGE', 'SIGNUP', 'CONVERSION'],                       'pct': 21.0},
    {'path': ['DIRECT', 'PRICING_PAGE', 'SIGNUP', 'CONVERSION'],                                              'pct': 14.0},
    {'path': ['ORGANIC_SEARCH', 'REVIEW', 'PRICING_PAGE', 'SIGNUP', 'CONVERSION'],                            'pct': 11.0},
    {'path': ['SOCIAL_TIKTOK', 'ORGANIC_SEARCH', 'COMPARISON', 'PRICING_PAGE', 'SIGNUP', 'CONVERSION'],       'pct':  8.5},
    {'path': ['EMAIL', 'PRICING_PAGE', 'SIGNUP', 'CONVERSION'],                                               'pct':  7.5},
    {'path': ['REFERRAL', 'COMPARISON', 'PRICING_PAGE', 'SIGNUP', 'CONVERSION'],                              'pct':  6.0},
    {'path': ['PAID_AD', 'PRICING_PAGE', 'SIGNUP', 'CONVERSION'],                                             'pct':  5.5},
    {'path': ['ORGANIC_SEARCH', 'CHATBOT', 'PRICING_PAGE', 'SIGNUP', 'CONVERSION'],                           'pct':  4.5},
    {'path': ['AFFILIATE', 'COMPARISON', 'PRICING_PAGE', 'SIGNUP', 'CONVERSION'],                             'pct':  3.5},
    {'path': ['DIRECT', 'CHATBOT', 'SIGNUP', 'CONVERSION'],                                                   'pct':  3.0},
]

_FALLBACK_TOUCHPOINTS_TV_SHOW = [
    {'label': 'TRAILER',           'reach_pct': 72.0, 'avg_days_to_conversion': 14.0, 'avg_touches_per_user': 2.6, 'lift_pct': 70.0},
    {'label': 'SOCIAL_TIKTOK',     'reach_pct': 51.0, 'avg_days_to_conversion': 9.0,  'avg_touches_per_user': 3.4, 'lift_pct': 48.0},
    {'label': 'SOCIAL_INSTAGRAM',  'reach_pct': 44.0, 'avg_days_to_conversion': 11.0, 'avg_touches_per_user': 2.3, 'lift_pct': 35.0},
    {'label': 'SOCIAL_YOUTUBE',    'reach_pct': 38.0, 'avg_days_to_conversion': 13.0, 'avg_touches_per_user': 2.1, 'lift_pct': 30.0},
    {'label': 'PRESS',             'reach_pct': 32.0, 'avg_days_to_conversion': 16.0, 'avg_touches_per_user': 1.7, 'lift_pct': 22.0},
    {'label': 'REVIEW',            'reach_pct': 41.0, 'avg_days_to_conversion': 5.0,  'avg_touches_per_user': 2.4, 'lift_pct': 88.0},
    {'label': 'CRITIC_AGGREGATOR', 'reach_pct': 27.0, 'avg_days_to_conversion': 4.0,  'avg_touches_per_user': 1.6, 'lift_pct': 105.0},
    {'label': 'TALENT_MENTION',    'reach_pct': 35.0, 'avg_days_to_conversion': 18.0, 'avg_touches_per_user': 1.9, 'lift_pct': 40.0},
    {'label': 'PLATFORM_PAGE',     'reach_pct': 81.0, 'avg_days_to_conversion': 1.5,  'avg_touches_per_user': 2.2, 'lift_pct': 280.0},
    {'label': 'EPG_LOOKUP',        'reach_pct': 29.0, 'avg_days_to_conversion': 2.0,  'avg_touches_per_user': 1.5, 'lift_pct': 95.0},
    {'label': 'PAID_AD',           'reach_pct': 26.0, 'avg_days_to_conversion': 12.0, 'avg_touches_per_user': 3.8, 'lift_pct': 18.0},
    {'label': 'BINGE_RECOMMENDATION','reach_pct': 22.0, 'avg_days_to_conversion': 7.0,  'avg_touches_per_user': 1.4, 'lift_pct': 58.0},
    {'label': 'CREATOR_INFLUENCER','reach_pct': 19.0, 'avg_days_to_conversion': 10.0, 'avg_touches_per_user': 1.8, 'lift_pct': 44.0},
    {'label': 'FIRST_WATCH',       'reach_pct': 95.0, 'avg_days_to_conversion': 0.5,  'avg_touches_per_user': 1.1, 'lift_pct': None},
    {'label': 'SOUNDTRACK',        'reach_pct': 12.0, 'avg_days_to_conversion': 19.0, 'avg_touches_per_user': 1.3, 'lift_pct': 14.0},
]

_FALLBACK_PATH_COLUMN_MIX_TV_SHOW = [
    {-10: {'TRAILER': 0.40, 'SOCIAL_TIKTOK': 0.25, 'SOCIAL_INSTAGRAM': 0.18, 'PRESS': 0.17}},
    {-9:  {'TRAILER': 0.35, 'SOCIAL_TIKTOK': 0.22, 'CREATOR_INFLUENCER': 0.18, 'TALENT_MENTION': 0.25}},
    {-8:  {'PRESS': 0.30, 'TALENT_MENTION': 0.25, 'TRAILER': 0.25, 'SOCIAL_YOUTUBE': 0.20}},
    {-7:  {'REVIEW': 0.34, 'CRITIC_AGGREGATOR': 0.28, 'PRESS': 0.20, 'TALENT_MENTION': 0.18}},
    {-6:  {'REVIEW': 0.30, 'CRITIC_AGGREGATOR': 0.25, 'SOCIAL_INSTAGRAM': 0.25, 'BINGE_RECOMMENDATION': 0.20}},
    {-5:  {'BINGE_RECOMMENDATION': 0.32, 'REVIEW': 0.28, 'PAID_AD': 0.22, 'SOCIAL_TIKTOK': 0.18}},
    {-4:  {'PLATFORM_PAGE': 0.38, 'REVIEW': 0.22, 'EPG_LOOKUP': 0.20, 'BINGE_RECOMMENDATION': 0.20}},
    {-3:  {'PLATFORM_PAGE': 0.45, 'EPG_LOOKUP': 0.25, 'REVIEW': 0.18, 'CRITIC_AGGREGATOR': 0.12}},
    {-2:  {'PLATFORM_PAGE': 0.55, 'EPG_LOOKUP': 0.25, 'TRAILER': 0.12, 'CRITIC_AGGREGATOR': 0.08}},
    {-1:  {'FIRST_WATCH': 0.85, 'PLATFORM_PAGE': 0.12, 'EPG_LOOKUP': 0.03}},
]

_FALLBACK_TOP_PATHS_TV_SHOW = [
    {'path': ['TRAILER', 'REVIEW', 'PLATFORM_PAGE', 'FIRST_WATCH', 'CONVERSION'],                                'pct': 19.0},
    {'path': ['SOCIAL_TIKTOK', 'TRAILER', 'PLATFORM_PAGE', 'FIRST_WATCH', 'CONVERSION'],                          'pct': 14.0},
    {'path': ['TRAILER', 'CRITIC_AGGREGATOR', 'PLATFORM_PAGE', 'FIRST_WATCH', 'CONVERSION'],                      'pct': 10.0},
    {'path': ['PRESS', 'REVIEW', 'PLATFORM_PAGE', 'FIRST_WATCH', 'CONVERSION'],                                   'pct':  8.5},
    {'path': ['TALENT_MENTION', 'TRAILER', 'PLATFORM_PAGE', 'FIRST_WATCH', 'CONVERSION'],                         'pct':  7.5},
    {'path': ['CREATOR_INFLUENCER', 'SOCIAL_TIKTOK', 'PLATFORM_PAGE', 'FIRST_WATCH', 'CONVERSION'],               'pct':  6.0},
    {'path': ['SOCIAL_INSTAGRAM', 'TRAILER', 'REVIEW', 'PLATFORM_PAGE', 'FIRST_WATCH', 'CONVERSION'],             'pct':  5.5},
    {'path': ['BINGE_RECOMMENDATION', 'PLATFORM_PAGE', 'FIRST_WATCH', 'CONVERSION'],                              'pct':  4.5},
    {'path': ['PAID_AD', 'PLATFORM_PAGE', 'FIRST_WATCH', 'CONVERSION'],                                           'pct':  3.5},
    {'path': ['EPG_LOOKUP', 'PLATFORM_PAGE', 'FIRST_WATCH', 'CONVERSION'],                                        'pct':  3.0},
]


# ── Public: Claude synthesis ─────────────────────────────────────────────────

_SYSTEM_SYNTH_MOVIE = """\
You are a senior box-office attribution analyst. You will be given:
  * A movie title, US box office gross, ticket price, and release window.
  * A list of marketing surfaces the campaign actually used (talent
    mentions, brand partnerships, social channels) — parsed from the
    user's "Extra Touchpoint Keywords" rules.
  * What we ALREADY observed in the panel: # converters, # cohort,
    touchpoint reach %, top paths. This may be very sparse or zero.

Your job is to ESTIMATE a plausible attribution mix for a movie of this
scale and talent profile, calibrated against:
  * Tentpole movies (>$200M domestic): 75-90% trailer reach,
    85-95% search reach in the 14 days pre-release.
  * Mid-budget movies ($30-200M domestic): 50-70% trailer reach,
    60-80% search reach.
  * Limited release (<$30M domestic): 30-50% trailer reach, 40-60% search.

Output JSON EXACTLY in this shape (no markdown, no code fences):
{
  "touchpoints": [
    {"label": "TRAILER",         "reach_pct": 74.5, "share_of_converters_pct": 82.0,
     "avg_days_to_conversion": 18.0, "avg_touches_per_user": 2.3, "lift_pct": 55.0},
    {"label": "ORGANIC_SEARCH",  ...},
    {"label": "SOCIAL_YOUTUBE",  ...},
    {"label": "SOCIAL_TIKTOK",   ...},
    {"label": "SOCIAL_INSTAGRAM",...},
    {"label": "PRESS",           ...},
    {"label": "REVIEW",          ...},
    {"label": "SHOWTIME_LOOKUP", ...},
    {"label": "GOOGLE_REVIEW",   ...},
    {"label": "TICKETING",       "reach_pct": 90+, "lift_pct": null},
    {"label": "PAID_AD",         ...},
    {"label": "TALENT_MENTION",  ...},
    {"label": "CREATOR_INFLUENCER",...},
    {"label": "BRAND_PARTNERSHIP",...},
    {"label": "SOUNDTRACK",      ...}
  ],
  "path_columns": [
    {"index": -10, "mix": {"ORGANIC_SEARCH": 0.50, "TRAILER": 0.25, ...}},
    {"index": -9,  "mix": {...}},
    ...
    {"index": -1,  "mix": {"TICKETING": 0.78, ...}}
  ],
  "top_paths": [
    {"path": ["ORGANIC_SEARCH","TRAILER","REVIEW","SHOWTIME_LOOKUP","TICKETING","CONVERSION"], "pct": 17.5},
    ...
  ],
  "avg_touches_before_purchase": 5.8,
  "avg_days_to_purchase": 14.2,
  "conversion_pct_of_cohort": 100.0,
  "notes": "1-2 sentences on why this mix; cite a comparable movie."
}

Hard rules:
  * Every column "mix" object's values must SUM to roughly 1.0 (i.e.
    column percentages, not user counts).
  * Provide at least 8 top_paths.
  * TICKETING must dominate column -1 (the step right before purchase).
  * CONVERSION must end every top_path.
  * lift_pct may be null (e.g. for TICKETING which is the conversion itself).
  * Numbers must reflect the box-office scale you were given. A $50M movie
    should NOT have a $400M's trailer reach.
  * If a marketing surface was named in the input but is irrelevant to
    movies (e.g. /reviews keyword for a B2B brand), still include the
    standard movie touchpoints — just down-weight the irrelevant ones.

Output JSON ONLY."""


_SYSTEM_SYNTH_WEBSITE = """\
You are a senior web-attribution analyst. You will be given:
  * A website (target name / domain), its estimated US monthly visitors,
    and a date range.
  * The marketing surfaces and conversion URL substrings the user defined.
  * What we ALREADY observed in the panel (may be sparse or zero).

Estimate a plausible path-to-conversion for a website of this scale,
calibrated against typical funnel patterns:
  * High-traffic SaaS / direct-response (>10M monthly US visitors):
    60-75% organic-search reach, 30-50% direct reach, conversion
    typically driven by pricing-page + signup.
  * Mid-traffic content/affiliate (1-10M monthly US): 50-70% search,
    20-40% referral, conversion via comparison + signup/CTA.
  * Low-traffic niche (<1M monthly US): 40-60% direct, 30-50% organic,
    conversion via long-form content + email + signup.

Output JSON EXACTLY in this shape (no markdown, no code fences):
{
  "touchpoints": [
    {"label": "ORGANIC_SEARCH", "reach_pct": ..., "share_of_converters_pct": ...,
     "avg_days_to_conversion": ..., "avg_touches_per_user": ..., "lift_pct": ...},
    {"label": "DIRECT",         ...},
    {"label": "REFERRAL",       ...},
    {"label": "SOCIAL_INSTAGRAM",...},
    {"label": "SOCIAL_TIKTOK",  ...},
    {"label": "SOCIAL_YOUTUBE", ...},
    {"label": "EMAIL",          ...},
    {"label": "PAID_AD",        ...},
    {"label": "PRESS",          ...},
    {"label": "REVIEW",         ...},
    {"label": "COMPARISON",     ...},
    {"label": "PRICING_PAGE",   ...},
    {"label": "SIGNUP",         "reach_pct": 90+, "lift_pct": null},
    {"label": "CHATBOT",        ...},
    {"label": "AFFILIATE",      ...}
  ],
  "path_columns": [
    {"index": -10, "mix": {"ORGANIC_SEARCH": 0.50, "PAID_AD": 0.20, ...}},
    ...
    {"index": -1,  "mix": {"SIGNUP": 0.85, ...}}
  ],
  "top_paths": [
    {"path": ["ORGANIC_SEARCH","COMPARISON","PRICING_PAGE","SIGNUP","CONVERSION"], "pct": 20.0},
    ...
  ],
  "avg_touches_before_purchase": 5.5,
  "avg_days_to_purchase": 7.5,
  "conversion_pct_of_cohort": 100.0,
  "notes": "1-2 sentences on why this mix; cite a comparable site."
}

Hard rules:
  * Column "mix" values sum to roughly 1.0 (per-column percentages).
  * At least 8 top_paths. SIGNUP (or the chosen conversion event) MUST
    dominate column -1. CONVERSION ends every top_path.
  * Numbers reflect the monthly-visitor scale you were given.

Output JSON ONLY."""


_SYSTEM_SYNTH_TV_SHOW = """\
You are a senior TV/streaming attribution analyst. You will be given:
  * A show title, total US viewers, release/run window.
  * Marketing surfaces the campaign actually used (talent, social,
    soundtrack, press).
  * What we ALREADY observed in the panel (may be sparse or zero).

Estimate a plausible discovery-to-first-watch journey for a show of this
scale, calibrated against:
  * Tentpole series (>20M US viewers — Stranger Things, The Last of Us):
    75-90% trailer reach, ~85% platform-page reach, 40-60% review reach.
  * Mid-tier series (3-20M US viewers): 55-75% trailer reach,
    65-80% platform-page, 25-45% review.
  * Niche / limited release (<3M US viewers): 35-55% trailer reach,
    50-70% platform-page, 15-30% review.

Output JSON EXACTLY in this shape (no markdown, no code fences):
{
  "touchpoints": [
    {"label": "TRAILER",            "reach_pct": ..., "share_of_converters_pct": ...,
     "avg_days_to_conversion": ..., "avg_touches_per_user": ..., "lift_pct": ...},
    {"label": "SOCIAL_TIKTOK",      ...},
    {"label": "SOCIAL_INSTAGRAM",   ...},
    {"label": "SOCIAL_YOUTUBE",     ...},
    {"label": "PRESS",              ...},
    {"label": "REVIEW",             ...},
    {"label": "CRITIC_AGGREGATOR",  ...},
    {"label": "TALENT_MENTION",     ...},
    {"label": "PLATFORM_PAGE",      ...},
    {"label": "EPG_LOOKUP",         ...},
    {"label": "PAID_AD",            ...},
    {"label": "BINGE_RECOMMENDATION",...},
    {"label": "CREATOR_INFLUENCER", ...},
    {"label": "FIRST_WATCH",        "reach_pct": 90+, "lift_pct": null},
    {"label": "SOUNDTRACK",         ...}
  ],
  "path_columns": [
    {"index": -10, "mix": {"TRAILER": 0.40, "SOCIAL_TIKTOK": 0.25, ...}},
    ...
    {"index": -1,  "mix": {"FIRST_WATCH": 0.85, ...}}
  ],
  "top_paths": [
    {"path": ["TRAILER","REVIEW","PLATFORM_PAGE","FIRST_WATCH","CONVERSION"], "pct": 19.0},
    ...
  ],
  "avg_touches_before_purchase": 5.0,
  "avg_days_to_purchase": 10.0,
  "conversion_pct_of_cohort": 100.0,
  "notes": "1-2 sentences on why this mix; cite a comparable show."
}

Hard rules:
  * Column "mix" values sum to roughly 1.0.
  * At least 8 top_paths. FIRST_WATCH MUST dominate column -1.
  * CONVERSION ends every top_path.
  * Numbers reflect the US-viewer scale you were given.

Output JSON ONLY."""


def synthesize_movie_journey(
    *,
    target: str,
    project_name: str,
    start_date: str,
    end_date: str,
    box_office_millions: float,
    ticket_price: float,
    extra_touchpoint_keywords: dict,
    panel_converters: int,
    panel_observed_touchpoints: Optional[list[dict]] = None,
    panel_top_paths: Optional[list[dict]] = None,
    steps: int = 10,
    max_tokens: int = 3000,
    temperature: float = 0.3,
) -> Optional[dict]:
    """Ask Claude for a plausible movie journey; fall back to canonical
    fixtures when Claude is offline. Returns a dict with keys:
        touchpoints (list), path_columns (list), top_paths (list),
        avg_touches_before_purchase, avg_days_to_purchase,
        conversion_pct_of_cohort, notes, source ('claude'|'fallback').
    """
    # When Claude is offline, return the canonical fixture immediately so
    # the dashboard always has SOMETHING to render. Scaled later by the
    # caller via scale_modeled_to_audience().
    if claude_messages is None or is_hybrid_enabled is None or get_claude_client is None:
        return _fallback_synthesis(steps=steps)
    try:
        if not is_hybrid_enabled() or get_claude_client() is None:
            return _fallback_synthesis(steps=steps)
    except Exception:
        return _fallback_synthesis(steps=steps)

    # Build a focused prompt: keep the input data small so Claude
    # spends its tokens reasoning about the estimate, not parsing inputs.
    payload = {
        'movie_title':                target,
        'project_name':               project_name,
        'release_window':             {'start': start_date, 'end': end_date},
        'us_box_office_millions':     box_office_millions,
        'avg_ticket_price_usd':       ticket_price,
        'implied_audience':           compute_implied_audience(
            box_office_millions=box_office_millions,
            ticket_price=ticket_price,
        ),
        'campaign_surfaces':          extra_touchpoint_keywords or {},
        'steps_to_synthesize':        steps,
        'panel_observed': {
            'converters':             panel_converters,
            'touchpoints': [
                {'label': r.get('label'), 'reach_pct': r.get('reach_pct')}
                for r in (panel_observed_touchpoints or [])[:15]
            ],
            'top_paths':              (panel_top_paths or [])[:5],
        },
    }
    user_msg = (
        "Estimate the path-to-purchase + touchpoint mix for the movie "
        "below. Output JSON only matching the schema in the system prompt.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    try:
        raw = claude_messages(
            system=_SYSTEM_SYNTH_MOVIE,
            user=user_msg,
            max_tokens=max_tokens,
            temperature=temperature,
        ) or ''
    except Exception as e:
        print(f"[Journey IQ synthesize] claude_messages failed: {e}")
        return _fallback_synthesis(target_type='movie', steps=steps)

    parsed = _parse_synth_json(raw)
    if not parsed:
        return _fallback_synthesis(target_type='movie', steps=steps)
    parsed['source'] = 'claude'
    return parsed


def synthesize_journey(
    *,
    target_type: str,
    target: str,
    project_name: str,
    start_date: str,
    end_date: str,
    extra_touchpoint_keywords: dict,
    panel_converters: int,
    panel_observed_touchpoints: Optional[list[dict]] = None,
    panel_top_paths: Optional[list[dict]] = None,
    steps: int = 10,
    # Movie params
    box_office_millions: float = 0.0,
    ticket_price: float = DEFAULT_TICKET_PRICE,
    # Website params
    monthly_visitors_millions: float = 0.0,
    date_range_days: int = 30,
    # TV show params
    us_viewers_millions: float = 0.0,
    # Claude tuning
    max_tokens: int = 3000,
    temperature: float = 0.3,
) -> Optional[dict]:
    """Type-aware synthesis dispatcher. Routes to the right Claude prompt +
    fallback fixture for movie / website / tv_show. Returns the standard
    synth dict shape regardless of type, plus a 'target_type' field so
    downstream code can render type-appropriate labels."""
    t = (target_type or 'general').strip().lower()

    # Movie: keep delegating to the existing function (which already exists
    # and is wire-compatible). This preserves backward compatibility for
    # callers that import synthesize_movie_journey directly.
    if t == 'movie':
        synth = synthesize_movie_journey(
            target=target, project_name=project_name,
            start_date=start_date, end_date=end_date,
            box_office_millions=box_office_millions, ticket_price=ticket_price,
            extra_touchpoint_keywords=extra_touchpoint_keywords,
            panel_converters=panel_converters,
            panel_observed_touchpoints=panel_observed_touchpoints,
            panel_top_paths=panel_top_paths,
            steps=steps, max_tokens=max_tokens, temperature=temperature,
        )
        if synth: synth['target_type'] = 'movie'
        return synth

    # Website / tv_show: same Claude wiring, different system prompt + fixture.
    if t == 'website':
        system = _SYSTEM_SYNTH_WEBSITE
        scale_payload = {
            'us_monthly_visitors_millions': monthly_visitors_millions,
            'analysis_window_days':         date_range_days,
            'implied_audience':             compute_website_implied_audience(
                monthly_visitors_millions=monthly_visitors_millions,
                date_range_days=date_range_days,
            ),
        }
    elif t == 'tv_show':
        system = _SYSTEM_SYNTH_TV_SHOW
        scale_payload = {
            'us_viewers_millions':  us_viewers_millions,
            'release_window':       {'start': start_date, 'end': end_date},
            'implied_audience':     compute_tv_show_implied_audience(
                us_viewers_millions=us_viewers_millions,
            ),
        }
    else:
        return None  # 'general' or unknown — caller shouldn't have called us

    # Offline path -> fallback fixture
    if claude_messages is None or is_hybrid_enabled is None or get_claude_client is None:
        out = _fallback_synthesis(target_type=t, steps=steps)
        out['target_type'] = t
        return out
    try:
        if not is_hybrid_enabled() or get_claude_client() is None:
            out = _fallback_synthesis(target_type=t, steps=steps)
            out['target_type'] = t
            return out
    except Exception:
        out = _fallback_synthesis(target_type=t, steps=steps)
        out['target_type'] = t
        return out

    payload = {
        'target':                target,
        'target_type':           t,
        'project_name':          project_name,
        'campaign_surfaces':     extra_touchpoint_keywords or {},
        'steps_to_synthesize':   steps,
        'panel_observed': {
            'converters':        panel_converters,
            'touchpoints': [
                {'label': r.get('label'), 'reach_pct': r.get('reach_pct')}
                for r in (panel_observed_touchpoints or [])[:15]
            ],
            'top_paths':         (panel_top_paths or [])[:5],
        },
        **scale_payload,
    }
    user_msg = (
        f"Estimate the path-to-conversion + touchpoint mix for the "
        f"{t.replace('_',' ')} below. Output JSON only matching the "
        f"schema in the system prompt.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    try:
        raw = claude_messages(
            system=system, user=user_msg,
            max_tokens=max_tokens, temperature=temperature,
        ) or ''
    except Exception as e:
        print(f"[Journey IQ synthesize] {t} claude_messages failed: {e}")
        out = _fallback_synthesis(target_type=t, steps=steps)
        out['target_type'] = t
        return out

    parsed = _parse_synth_json(raw)
    if not parsed:
        out = _fallback_synthesis(target_type=t, steps=steps)
        out['target_type'] = t
        return out
    parsed['source'] = 'claude'
    parsed['target_type'] = t
    return parsed


# Per-type fixture lookup; keys must match TARGET_TYPES values.
_FIXTURES_BY_TYPE = {
    'movie':   {'touchpoints': _FALLBACK_TOUCHPOINTS,         'columns': _FALLBACK_PATH_COLUMN_MIX,         'top_paths': _FALLBACK_TOP_PATHS,         'avg_touches': 5.5, 'avg_days': 14.0,
                'notes': 'Canonical movie attribution mix (Claude offline — used fallback fixture).'},
    'website': {'touchpoints': _FALLBACK_TOUCHPOINTS_WEBSITE, 'columns': _FALLBACK_PATH_COLUMN_MIX_WEBSITE, 'top_paths': _FALLBACK_TOP_PATHS_WEBSITE, 'avg_touches': 5.5, 'avg_days': 7.5,
                'notes': 'Canonical website conversion mix (Claude offline — used fallback fixture).'},
    'tv_show': {'touchpoints': _FALLBACK_TOUCHPOINTS_TV_SHOW, 'columns': _FALLBACK_PATH_COLUMN_MIX_TV_SHOW, 'top_paths': _FALLBACK_TOP_PATHS_TV_SHOW, 'avg_touches': 5.0, 'avg_days': 10.0,
                'notes': 'Canonical TV-show first-watch mix (Claude offline — used fallback fixture).'},
}


def _fallback_synthesis(*, target_type: str = 'movie', steps: int = 10) -> dict:
    """Static canonical fixture for the requested type — used when Claude
    is unavailable. Defaults to the movie fixture for backward compat."""
    fx = _FIXTURES_BY_TYPE.get((target_type or 'movie').lower(), _FIXTURES_BY_TYPE['movie'])
    return {
        'touchpoints':                  [dict(r) for r in fx['touchpoints']],
        'path_columns':                 _expand_fallback_path_columns(
                                            columns_fixture=fx['columns'], steps=steps),
        'top_paths':                    [dict(p) for p in fx['top_paths']],
        'avg_touches_before_purchase':  fx['avg_touches'],
        'avg_days_to_purchase':         fx['avg_days'],
        'conversion_pct_of_cohort':     100.0,
        'notes':                        fx['notes'],
        'source':                       'fallback',
    }


def _expand_fallback_path_columns(*, columns_fixture: list = None, steps: int = 10) -> list[dict]:
    """Return exactly `steps` columns of the requested fixture, right-aligned.

    When steps <= len(fixture) we keep the LAST `steps` columns (those are
    closest to conversion and most stable). When steps > len(fixture) we
    pad with copies of the oldest column on the left. Indexes are always
    renumbered cleanly so the rightmost is -1.

    Backward compat: when columns_fixture is None we default to the movie
    fixture (the original caller's behavior).
    """
    if columns_fixture is None:
        columns_fixture = _FALLBACK_PATH_COLUMN_MIX
    full = [list(d.items())[0] for d in columns_fixture]  # [(-10, {...}), ...]
    if steps <= len(full):
        slice_ = full[-steps:]
    else:
        pad = [full[0]] * (steps - len(full))
        slice_ = pad + full
    n = len(slice_)
    return [{'index': -(n - i), 'mix': mix} for i, (_, mix) in enumerate(slice_)]


def _parse_synth_json(raw: str) -> Optional[dict]:
    """Tolerate code fences / preamble. Returns dict or None."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?', '', text, count=1).strip()
        if text.endswith('```'):
            text = text[:-3].strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    if '{' in text and '}' in text:
        snippet = text[text.find('{'): text.rfind('}') + 1]
        try:
            return json.loads(snippet)
        except Exception:
            return None
    return None


# ── Public: adapter from synth → dashboard JSON ──────────────────────────────

def synth_to_dashboard_payload(
    synth: dict,
    *,
    target_audience: int,
) -> dict:
    """Convert Claude's structured estimate into the JSON shape the
    dashboard already knows how to render. Returns:
        {'touchpoints': {...}, 'path_to_purchase': {...},
         'kpis': {...}, 'cohort_size': N}
    Everything is sized to `target_audience` (the implied # of buyers).
    """
    n = max(1, int(target_audience or 0))
    # ── Baseline conversion rate (genpop frame) ───────────────────────
    # The modeled cohort IS the implied audience (everyone converted),
    # so a "conv-rate-when-seen vs not-seen within the cohort" framing
    # collapses to 100% / 0% which is mathematically forced and useless.
    #
    # Reframe to the genpop denominator so the columns mean something
    # decision-grade:
    #     baseline_conv_pct = implied_audience / US_GENPOP × 100
    #         (the chance a random US adult 16+ converted at all)
    #     conv_when_not    ≈ baseline_conv_pct
    #         (someone who never touched this surface still converts at
    #          the genpop baseline)
    #     conv_when_seen   ≈ baseline × (1 + lift/100), capped at 100
    #         (lift is preserved exactly as Claude/fallback reported it)
    # When lift is None or 0 we report both as baseline so the columns
    # never display "100% vs 0%" again.
    baseline_conv_pct = min(100.0, (float(n) / float(US_GENPOP_BASELINE)) * 100.0)
    if baseline_conv_pct <= 0:
        baseline_conv_pct = 1.0

    tp_rows = []
    for r in (synth.get('touchpoints') or []):
        reach_pct = float(r.get('reach_pct') or 0.0)
        reach = int(round(n * reach_pct / 100.0))
        sh_conv_pct = float(r.get('share_of_converters_pct') or reach_pct)
        converters_reached = int(round(n * sh_conv_pct / 100.0))
        lift_raw = r.get('lift_pct')
        try:
            lift_frac = float(lift_raw) / 100.0 if lift_raw is not None else 0.0
        except (TypeError, ValueError):
            lift_frac = 0.0
        conv_when_not    = baseline_conv_pct
        conv_when_seen   = min(100.0, baseline_conv_pct * (1.0 + lift_frac))
        tp_rows.append({
            'label':                  r.get('label'),
            'reach':                  reach,
            'reach_pct':              round(reach_pct, 1),
            'converters_reached':     converters_reached,
            'share_of_converters':    round(sh_conv_pct, 1),
            'conv_rate_when_reached': round(conv_when_seen, 2),
            'conv_rate_when_not':     round(conv_when_not, 2),
            'baseline_conv_rate':     round(baseline_conv_pct, 2),
            'lift_pct':               lift_raw,
            'avg_days_to_conversion': r.get('avg_days_to_conversion'),
            'avg_touches_per_user':   r.get('avg_touches_per_user'),
        })

    touchpoints = {
        'baseline_conv_rate': round(baseline_conv_pct, 2),
        'cohort_size':        n,
        'converters':         n,
        'rows':               tp_rows,
        'overlap':            _synth_overlap(tp_rows, n),
        'touch_distribution': _synth_touch_distribution(
            n, avg_touches=synth.get('avg_touches_before_purchase') or 5.0),
    }

    # ── Path to purchase ─────────────────────────────────────────────
    # Survivorship curve: every converter is at CONVERSION (step 0) and
    # step -1 (the touchpoint immediately before purchase), but as you
    # walk further back through the funnel fewer converters actually
    # had a recorded touch at that step. avg_touches_before_purchase
    # from the synth controls the shape of the decay — beyond it the
    # funnel drops off faster.
    avg_touches = float(synth.get('avg_touches_before_purchase') or 5.0)

    def _survivorship_pct(steps_back: int) -> float:
        if steps_back <= 1:
            return 100.0
        if steps_back <= avg_touches:
            pct = 100.0 - (steps_back - 1) * 4.0      # 4 pp per step inside avg_touches
        else:
            pct = 100.0 - (avg_touches - 1) * 4.0 - (steps_back - avg_touches) * 8.0  # then 8 pp
        return max(25.0, pct)

    columns = []
    for c in (synth.get('path_columns') or []):
        idx = int(c.get('index', 0))
        mix = c.get('mix') or {}
        users_pct = _survivorship_pct(abs(idx))
        col_users = int(round(n * users_pct / 100.0))
        # Per-label users now scale to THIS column's users, not the
        # full cohort — fixes the "every label sums to >100%" issue too.
        top_labels = []
        for lbl, frac in sorted(mix.items(), key=lambda kv: -float(kv[1])):
            users = int(round(col_users * float(frac)))
            top_labels.append({
                'label': lbl, 'users': users,
                'pct':   round(100.0 * float(frac), 1),
            })
        columns.append({
            'index':      idx,
            'label':      f'Step {idx}',
            'users':      col_users,
            'users_pct':  round(users_pct, 1),
            'top_labels': top_labels[:6],
            'top_hosts':  [],  # synth doesn't know specific hosts
        })
    # CONVERSION column at index 0 — by definition 100% of converters.
    columns.append({
        'index': 0, 'label': 'CONVERSION',
        'users': n, 'users_pct': 100.0,
        'top_labels': [{'label': 'CONVERSION', 'users': n, 'pct': 100.0}],
        'top_hosts': [],
    })

    top_paths = []
    for p in (synth.get('top_paths') or []):
        pct = float(p.get('pct') or p.get('users_pct') or 0.0)
        users = int(round(n * pct / 100.0))
        top_paths.append({
            'path': list(p.get('path') or []),
            'users': users,
            'users_pct': round(pct, 1),
        })

    path_to_purchase = {
        'mode':         'modeled',
        'cohort_size':  n,
        'steps':        max(1, len(columns) - 1),
        'columns':      columns,
        'top_paths':    top_paths,
    }

    avg_days = float(synth.get('avg_days_to_purchase') or 14.0)
    avg_touches = float(synth.get('avg_touches_before_purchase') or 5.0)
    kpis = {
        'total_users':                n,
        'converted_users':            n,
        'conversion_pct':             100.0,
        'avg_journey_duration_days':  round(avg_days, 1),
        'avg_sessions_to_convert':    round(max(1.0, avg_touches / 2.0), 1),
        'avg_events_per_user':        round(avg_touches + 1, 1),
    }

    return {
        'touchpoints':      touchpoints,
        'path_to_purchase': path_to_purchase,
        'kpis':             kpis,
        'cohort_size':      n,
    }


def _synth_overlap(tp_rows: list[dict], n: int) -> list[dict]:
    """Estimate top co-occurrence pairs from individual reach %s assuming
    independence (lower bound — real overlap usually higher). Used purely
    for the dashboard overlap card in synthetic runs."""
    out = []
    for i in range(len(tp_rows)):
        for j in range(i + 1, len(tp_rows)):
            a, b = tp_rows[i], tp_rows[j]
            pa = (a.get('reach_pct') or 0) / 100.0
            pb = (b.get('reach_pct') or 0) / 100.0
            if pa <= 0 or pb <= 0:
                continue
            users = int(round(n * pa * pb))
            out.append({
                'a':           a['label'],
                'b':           b['label'],
                'users':       users,
                'users_pct':   round(100.0 * pa * pb, 1),
                'converters':  users,
                'conv_rate':   100.0,
            })
    out.sort(key=lambda x: -x['users'])
    return out[:12]


def _synth_touch_distribution(n: int, *, avg_touches: float) -> list[dict]:
    """Lognormal-shaped bucket distribution centred on avg_touches, with
    a small tail of 0-touch and 11+ buckets for realism."""
    # Hand-tuned weights that roughly track real-world touch distributions:
    # most converters get 4-6 touches, a meaningful tail at 7-10 and 11+.
    weights_by_avg = {
        3.0:  [0.02, 0.10, 0.35, 0.30, 0.15, 0.08],
        5.0:  [0.01, 0.05, 0.20, 0.35, 0.25, 0.14],
        7.0:  [0.01, 0.03, 0.10, 0.27, 0.34, 0.25],
        10.0: [0.00, 0.02, 0.05, 0.18, 0.30, 0.45],
    }
    # Pick the nearest preset
    best = min(weights_by_avg.keys(), key=lambda k: abs(k - avg_touches))
    weights = weights_by_avg[best]
    buckets = ['0', '1', '2-3', '4-6', '7-10', '11+']
    out = []
    for b, w in zip(buckets, weights):
        cnt = int(round(n * w))
        out.append({
            'bucket':         b,
            'converters':     cnt,
            'non_converters': 0,
            'total':          cnt,
            'conv_pct':       100.0,
        })
    return out


# ── Blend real + modeled into the canonical dashboard payload ────────────────

def blend_real_and_modeled(
    *,
    real_summary: dict,
    modeled_summary: dict,
    real_converters: int,
    threshold: int = SPARSE_COHORT_THRESHOLD,
) -> dict:
    """Pick the modeled fields whenever the real cohort is sparse for that
    field, otherwise keep real. The dashboard already supports a view-mode
    toggle; this is the default "blended" payload it shows.

    Currently: if real_converters < threshold, use modeled wholesale (still
    keeping real meta so the user knows what the panel actually saw).
    Otherwise use real wholesale. This avoids field-by-field stitching
    that can produce inconsistent percentages.
    """
    if real_converters >= threshold:
        return real_summary

    out = dict(modeled_summary)
    # Preserve the real meta for transparency.
    out['meta'] = dict(real_summary.get('meta') or {})
    out['meta']['blend_mode'] = 'modeled'
    out['meta']['panel_real_converters'] = real_converters
    out['meta']['blend_threshold'] = threshold
    return out


# ── Public: AI-driven audience lookup (mirrors SubscriberIQ pattern) ─────────

US_GENPOP_BASELINE = 260_000_000   # US adults 16+; used as denominator for
                                   # reach_pct_of_genpop calculations.

_RESEARCH_CACHE: dict[str, dict] = {}
_FOOTPRINT_CACHE: dict[str, dict] = {}


def research_audience_size(
    *,
    target_type: str,
    target: str,
    start_date: str = '',
    end_date: str = '',
) -> dict:
    """Use Claude + web_search (Anthropic's native web tool, same engine
    BG.py's persona_research_agent uses) to look up a real-world audience
    number for the target.

    Returns a dict keyed by what the form needs to fill in:
      * movie:   {'box_office_millions': float, 'avg_ticket_price': 15.0,
                  'confidence': 'high'|'medium'|'low', 'source': str, 'notes': str}
      * website: {'monthly_visitors_millions': float, 'confidence': ...,
                  'source': str, 'notes': str}
      * tv_show: {'us_viewers_millions': float, 'confidence': ...,
                  'source': str, 'notes': str}
      * general / unknown: {} (no lookup)

    Results are cached in-memory by (type, target). Returns
    {'_error': '...'} when OpenAI is unavailable so the caller can
    surface a clean error to the user instead of falling silent.
    """
    t = (target_type or 'general').strip().lower()
    if t not in ('movie', 'website', 'tv_show'):
        return {}

    cache_key = f'{t}::{(target or "").strip().lower()}'
    if cache_key in _RESEARCH_CACHE:
        return _RESEARCH_CACHE[cache_key]

    try:
        # Claude client (Anthropic) — same dual-import + web_search pattern
        # used by research_marketing_footprint. ANTHROPIC_API_KEY required.
        _claude_messages = None
        _get_claude_client = None
        try:
            from migration.claude_client import claude_messages as _cm, get_claude_client as _gc
            _claude_messages = _cm; _get_claude_client = _gc
        except ImportError:
            try:
                from claude_client import claude_messages as _cm, get_claude_client as _gc  # type: ignore
                _claude_messages = _cm; _get_claude_client = _gc
            except ImportError:
                return {'_error': 'claude_client not importable'}
        try:
            if _get_claude_client() is None:
                return {'_error': 'ANTHROPIC_API_KEY not configured'}
        except Exception as e:
            return {'_error': f'Claude client check failed: {e}'}

        # Type-specific prompt + JSON schema
        if t == 'movie':
            prompt = (
                f'Look up the US domestic box-office gross for the movie '
                f'"{target}". Use Box Office Mojo, The Numbers, Variety, '
                f'Deadline, or studio releases. If the movie is currently '
                f'in theaters, use the latest reported cumulative US gross. '
                f'If not yet released, use opening-weekend projections.\n\n'
                f'Return JSON ONLY in this shape (no code fences):\n'
                f'{{\n'
                f'  "box_office_millions": <number in millions USD or null>,\n'
                f'  "confidence": "high" | "medium" | "low",\n'
                f'  "source":     "<specific source — e.g. Box Office Mojo as of 5/15/2026>",\n'
                f'  "notes":      "<1 sentence on why you picked this number>"\n'
                f'}}'
            )
            number_key = 'box_office_millions'
        elif t == 'website':
            prompt = (
                f'Estimate the monthly US visitors (uniques) for the website '
                f'"{target}". Use Similarweb, SemRush, Ahrefs, or Comscore '
                f'data if available; otherwise reason from analogous sites of '
                f'similar traffic class.\n\n'
                f'Return JSON ONLY (no code fences):\n'
                f'{{\n'
                f'  "monthly_visitors_millions": <number in millions or null>,\n'
                f'  "confidence": "high" | "medium" | "low",\n'
                f'  "source":     "<specific source — e.g. Similarweb April 2026>",\n'
                f'  "notes":      "<1 sentence>"\n'
                f'}}'
            )
            number_key = 'monthly_visitors_millions'
        else:  # tv_show
            window = f' (between {start_date} and {end_date})' if (start_date and end_date) else ''
            prompt = (
                f'Look up the cumulative US viewers for the TV show / '
                f'streaming series "{target}"{window}. Use Nielsen weekly '
                f'rankings, Samba TV, Luminate, Parrot Analytics, or '
                f'streamer-reported numbers. For platform-original shows, '
                f'use the most recent reported figure.\n\n'
                f'Return JSON ONLY (no code fences):\n'
                f'{{\n'
                f'  "us_viewers_millions": <number in millions or null>,\n'
                f'  "confidence": "high" | "medium" | "low",\n'
                f'  "source":     "<specific source — e.g. Nielsen week of 3/3/2026>",\n'
                f'  "notes":      "<1 sentence>"\n'
                f'}}'
            )
            number_key = 'us_viewers_millions'

        import os
        claude_model = (
            os.environ.get('JOURNEY_IQ_RESEARCH_MODEL')
            or os.environ.get('CLAUDE_PERSONA_MODEL')
            or 'claude-opus-4-7'
        )
        _ws_new = {'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 6}
        _ws_old = {'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 6}
        _audience_system = (
            'You are a senior consumer-research analyst. Use the web_search '
            'tool aggressively to look up real-world audience numbers. '
            '\n\n'
            'CRITICAL OUTPUT RULES — your response MUST be parseable JSON:\n'
            '  1. After you finish researching, your FINAL text output must '
            'be EXACTLY one JSON object matching the schema in the user '
            'message.\n'
            '  2. The first character of your final output MUST be `{` and '
            'the last character MUST be `}`.\n'
            '  3. Do NOT include any prose, narration, summary of your '
            'research, "I found that...", "Based on my searches...", or '
            'thinking text in the final response. JSON ONLY.\n'
            '  4. Do NOT wrap the JSON in markdown fences (no ```json, no '
            '```). Just the raw object.'
        )
        # Primary: try new tool ID + default model. If parse fails OR no JSON
        # braces appear in the response, fall through to the Sonnet fallback.
        def _has_json(t: str) -> bool:
            return ('{' in t and '}' in t and t.find('{') < t.rfind('}'))

        raw = ''
        try:
            raw = _claude_messages(
                system=_audience_system, user=prompt, model=claude_model,
                max_tokens=4000, temperature=0.2, tools=[_ws_new],
            ) or ''
        except Exception as e:
            print(f'[Journey IQ audience] primary Claude+web_search failed: {e}')
            raw = ''
        if not _has_json(raw):
            if raw:
                print(f'[Journey IQ audience] primary returned no JSON — falling back to Sonnet. snippet: {raw[:200]!r}')
            try:
                raw = _claude_messages(
                    system=_audience_system, user=prompt,
                    model='claude-sonnet-4-6',
                    max_tokens=4000, temperature=0.2, tools=[_ws_old],
                ) or ''
            except Exception as e:
                return {'_error': f'Claude+web_search fallback also failed: {e}'}
        if not _has_json(raw):
            return {'_error': 'Claude returned no JSON', '_raw': raw[:500]}

        # Strip code fences if Claude wraps the JSON
        if raw.startswith('```'):
            raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
        start = raw.find('{')
        end = raw.rfind('}')
        if start < 0 or end < 0:
            return {'_error': 'AI response had no JSON object', '_raw': raw[:200]}
        try:
            parsed = json.loads(raw[start: end + 1])
        except Exception as e:
            return {'_error': f'AI response JSON parse failed: {e}', '_raw': raw[:200]}

        # Normalize the response: always include the canonical number key
        out = {
            number_key:  float(parsed.get(number_key) or 0) or None,
            'confidence': parsed.get('confidence') or 'low',
            'source':     parsed.get('source') or '',
            'notes':      parsed.get('notes') or '',
        }
        if t == 'movie':
            out['avg_ticket_price'] = DEFAULT_TICKET_PRICE
        _RESEARCH_CACHE[cache_key] = out
        return out
    except Exception as e:
        return {'_error': f'research_audience_size failed: {e}'}


# ── Public: marketing-footprint research agent ───────────────────────────────
# Goes a level deeper than research_audience_size. Instead of returning just
# one number, this asks Claude (with the native web_search tool) to
# enumerate the actual marketing events the target generated INSIDE the
# user-specified date window — celeb posts, press articles, brand
# partnerships, TikTok virals, talent podcasts, ticketing-site share, etc.
# — and estimate what % of US gen-pop EACH event likely reached. Renders as
# result as nested bubbles: parent category (SOCIAL_MEDIA / PRESS / TALENT
# / etc.) sized by total reach, with child sub-bubbles for each discovered
# event (sized by that event's reach).

_FOOTPRINT_CHANNELS = (
    'social_media',         # TikTok / Instagram / YouTube / X / Reddit / Facebook
    'press',                # Variety / Deadline / Hollywood Reporter / etc.
    'talent_mentions',      # Steph Curry / specific stars in the movie
    'creator_influencers',  # MrBeast / Kai Cenat / podcast hosts
    'brand_partnerships',   # Mercedes / Gatorade / co-promo deals
    'reviews_critics',      # Rotten Tomatoes / Metacritic / IMDb
    'paid_advertising',     # TV spots, OOH, programmatic display
    'showtime_searches',    # Fandango / Atom / theater lookup sites
    'ticketing_sites',      # Fandango / AMC / Regal direct sales
    'soundtrack_music',     # Spotify playlist appearances, official singles
    'organic_search',       # Google search interest spikes
    'press_reviews',        # NYT review, Roger Ebert, etc.
    'forum_discussion',     # r/movies, Letterboxd, niche boards
)


_SYSTEM_FOOTPRINT = """\
You are a senior consumer-attribution analyst running a real-time research
sweep on the marketing footprint of a target (movie, TV show, website, or
brand) DURING A SPECIFIC DATE WINDOW. You have web search. Your job:

  1. Discover the BIGGEST real-world marketing events for this target
     WITHIN THE DATE WINDOW (not just current/most-recent). Use Google
     date-filtered queries and per-platform site: searches. Specifically:

     * Google "short videos" tab — equivalent to:
         google.com/search?q=<TARGET>&udm=39&tbs=cdr:1,cd_min:<start>,cd_max:<end>
       Pull the top 10 short-form videos (TikTok / Shorts / Reels) Google
       surfaces during the window. Note each video's platform, creator,
       view count if visible, and URL.

     * Google "news" tab — equivalent to:
         google.com/search?q=<TARGET>&tbm=nws&tbs=cdr:1,cd_min:<start>,cd_max:<end>
       Pull the top 10 news articles in the window. Note publication, URL,
       headline, and (where available) monthly US uniques of the publication.

     * Google "forums" tab — equivalent to:
         google.com/search?q=<TARGET>&udm=18&tbs=cdr:1,cd_min:<start>,cd_max:<end>
       Pull the top 10 forum discussions (mostly Reddit, plus
       Stack-Exchange-style boards). Note subreddit/forum, URL, upvotes
       or comment count if visible.

     * Per-platform "site:" searches in the window:
         site:tiktok.com <TARGET>     site:youtube.com <TARGET>
         site:instagram.com <TARGET>  site:x.com <TARGET>
         site:reddit.com <TARGET>     site:facebook.com <TARGET>
       Pull the top 5 results per platform inside the date window.

     * Trade press + general press: pull SPECIFIC ARTICLES (not the
       publication homepage). Run dated site:-queries inside the
       window for each major outlet and link to the actual article
       URL. Outlets to cover:
         Trade:   variety.com, deadline.com, hollywoodreporter.com,
                  indiewire.com, screenrant.com, collider.com,
                  the-numbers.com, boxofficepro.com
         General: nytimes.com, latimes.com, washingtonpost.com,
                  rollingstone.com, vulture.com, ew.com,
                  thewrap.com, ign.com, polygon.com
         Reviews: rogerebert.com, slashfilm.com, theplaylist.net,
                  avclub.com, rotten-tomatoes-aggregated critic blurbs
       For EACH outlet that ran something about the target during
       the window, return ONE event with the article URL, headline,
       byline (author) if visible, and the publication's monthly US
       uniques. Aim for 8-12 press events and 5-8 press_reviews
       events. Never link to just the publication's homepage — every
       url MUST point to the specific article.

     * Ticketing surfaces: fandango.com, atomtickets.com, amctheatres.com,
       regmovies.com, cinemark.com — note any Fandango "top sellers" or
       theater-chain pre-sale rankings inside the window.

     * Showtime / EPG-lookup spikes — REASON about the top ~10
       surfaces a real person hits when they want to find showtimes
       for the target during the window. For each surface, name the
       site/feature, describe how that surface picks up the target
       (search-results page, native showtime widget, "Top Sellers"
       carousel, theater-detail page, etc.), and estimate US-genpop
       reach. The canonical pre-purchase showtime-lookup surfaces
       for a US theatrical release are, in approximate descending
       order of reach:
         1. Google "Showtimes near me" Knowledge Panel widget
            (biggest surface — most users never click off Google
            because the widget surfaces all nearby theaters + times)
         2. Fandango direct movie page + "Top Sellers" carousel
            (~50M monthly US uniques)
         3. Google Maps theater search ("movie theater near me" →
            pick theater → see what's playing)
         4. AMC Theatres app + amctheatres.com (largest US chain
            by screens, ~12M app users)
         5. Atom Tickets (atomtickets.com)
         6. Regal Cinemas app + regmovies.com (#2 US chain)
         7. Cinemark app + cinemark.com (#3 US chain)
         8. Regional chains aggregated — Marcus, Alamo Drafthouse,
            Harkins, B&B, Landmark, Cinépolis (long-tail but real
            in non-coastal DMAs)
         9. IMDb showtimes feature (imdb.com/showtimes/title/<tt-id>)
        10. Bing "Showtimes near me" (Edge / Windows default engine)
        11. Yelp + Apple Maps theater search (long-tail)
        12. Moviefone (legacy but still indexed; older skew)
       Aim for 10-12 showtime_searches events covering this whole
       surface stack. Use Fandango's published top-sellers lists,
       AMC/Regal/Cinemark pre-sale rankings, and SimilarWeb traffic
       estimates as sources where available.

     * Organic search — REASON about what real people search for when
       they're in the funnel for this target during this date window,
       across MULTIPLE engines (not just Google). For each likely search
       intent, name the engine the query would skew to and estimate US
       monthly search volume during the window. Use Google Trends
       (trends.google.com), Google Keyword Planner reasoning,
       "people-also-ask" boxes, Reddit/Quora question titles, and
       AnswerThePublic-style intent fan-outs.

       For movies STILL IN THE THEATRICAL WINDOW (target_type='movie'
       and the date window overlaps theatrical release), the top
       PRE-PURCHASE intents are typically:
         - "<TARGET> showtimes" / "<TARGET> tickets" / "<TARGET> near me"
         - "<TARGET> reviews" / "is <TARGET> good" / "<TARGET> rotten tomatoes"
         - "<TARGET> trailer" / "<TARGET> cast" / "<TARGET> runtime"
         - "<TARGET> age rating" / "<TARGET> parents guide"
         - "<TARGET> end credits scene" / "<TARGET> spoilers"
         - "<TARGET> AMC / Regal / Cinemark <city>"
       Do NOT include POST-THEATRICAL queries while the movie is still
       in theaters: drop "<TARGET> streaming", "<TARGET> netflix",
       "<TARGET> hulu", "<TARGET> blu-ray / dvd", "watch <TARGET>
       online free", "<TARGET> torrent". Only add those if the date
       window starts AFTER the theatrical-to-streaming gap (~45-90d
       post-release).

       Engines to enumerate (skew each query to its most-likely engine):
         - Google (general web)            - Google Maps (showtimes / near me)
         - YouTube search (trailer / clips) - Bing (older / Windows users)
         - DuckDuckGo (privacy-leaning)     - Yahoo (older)
         - Apple Spotlight / Siri          - Reddit search (reviews / spoilers)
         - TikTok search (Gen Z reviews)   - Amazon search (only post-theatrical)

       Aim for 8-12 distinct organic_search events covering the most
       likely query intents; mix engines so it's not 10 Google rows.

     * Soundtrack / music — ENUMERATE the playable music
       surfaces for the target during the window. Every event MUST
       link to a place a user can actually LISTEN to the song or
       album right now — not Wikipedia, not a Variety article about
       the soundtrack, not the band's homepage. Valid URL patterns:
         - Spotify track:   https://open.spotify.com/track/<id>
         - Spotify album:   https://open.spotify.com/album/<id>
         - Spotify playlist:https://open.spotify.com/playlist/<id>
         - Apple Music song:https://music.apple.com/us/song/<slug>/<id>
         - Apple Music album:https://music.apple.com/us/album/<slug>/<id>
         - YouTube Music:   https://music.youtube.com/watch?v=<id>
         - YouTube official audio/MV: https://www.youtube.com/watch?v=<id>
         - Tidal:           https://tidal.com/browse/track/<id>
         - Amazon Music:    https://music.amazon.com/albums/<id>
         - SoundCloud:      https://soundcloud.com/<artist>/<track>
         - Bandcamp:        https://<artist>.bandcamp.com/track/<slug>
       For a major US release, expect:
         - The official lead single on Spotify + Apple Music + YouTube
           (3 separate events — same song, different streaming surface)
         - 1-3 additional notable soundtrack tracks
         - The full album / OST on Spotify + Apple Music
         - 1-3 playlist appearances (Spotify "New Music Friday",
           Apple Music "Hot Tracks", Spotify "Rap Caviar" / genre
           playlists if applicable)
         - Trending TikTok sound for the lead single
       Aim for 8-12 listenable events covering 3+ streaming platforms.
       Run dated web_searches:
         "<TARGET> soundtrack Spotify"
         "<TARGET> soundtrack Apple Music"
         "<TARGET> official song"  / "<TARGET> theme song"
         "<TARGET> end credits song"
         site:open.spotify.com <TARGET>
         site:music.apple.com <TARGET>
         site:youtube.com <TARGET> official audio

     * Brand partnerships — DO EXPLICIT WEB SEARCHES to discover the
       full list of co-promotional deals around the target. Don't
       trust ambient memory; run dated queries inside the window:
         - "<TARGET> brand partnership"   - "<TARGET> tie-in"
         - "<TARGET> co-promotion"        - "<TARGET> exclusive"
         - "<TARGET> sponsor"             - "<TARGET> collab"
         - "<TARGET> McDonald's | Burger King | Wendy's | Taco Bell"
         - "<TARGET> DoorDash | Uber Eats | Postmates | Grubhub"
         - "<TARGET> Mercedes | Ford | Chevy | Jeep | Toyota | Tesla"
         - "<TARGET> Coca-Cola | Pepsi | Gatorade | Mountain Dew"
         - "<TARGET> Lay's | Doritos | Cheetos | M&M's | Reese's"
         - "<TARGET> Nike | Adidas | Champion | Under Armour"
         - "<TARGET> Chase | Amex | Visa | Mastercard"
         - "<TARGET> AMC Stubs | Regal Crown Club"
         - "<TARGET> Spotify | Apple Music exclusive"
         - "<TARGET> Funko | Lego | Hot Toys merchandise"
         - "<TARGET> Walmart | Target | Best Buy exclusive"
       For a major US theatrical release, expect 8-12 real
       partnerships across QSR, delivery, auto, beverage, snacks,
       apparel, banking/credit-card, theater-loyalty, music, and
       merchandise categories. Aim for the top 10.

       US-ONLY hard restriction: include ONLY partnerships with
       brands that operate in the United States as a primary market.
       DROP anything that is purely a foreign-market tie-in (e.g.
       Lidl, Carrefour, Tesco, McDonald's-Japan-only Happy Meal toy,
       7-Eleven Korea exclusive, Sky Cinema UK, Mexico-only OXXO,
       India-only Tata Cliq). If a global brand ran a campaign and
       it was active in the US (e.g. Mercedes Super Bowl spot,
       global McDonald's Happy Meal that shipped to US stores), keep
       it. If the campaign was Europe/Asia/LATAM-only, drop it.

       URL rule: every brand_partnerships event MUST link to the
       SPECIFIC collab announcement / campaign page — never the
       brand's homepage. Examples of valid URLs:
         https://about.doordash.com/en-us/news/doordash-x-the-goat/
         https://news.mercedes-benz.com/2026/01/the-goat-partnership.html
         https://www.mcdonalds.com/us/en-us/promotions/the-goat-happy-meal.html
       If you can't find the announcement page via web_search, look
       for the campaign hashtag on Twitter / Instagram (e.g.
       #DashPassGoat, #MercedesGoat) or a trade-press story
       (variety.com, adweek.com, marketing-dive.com) and link to
       that — but never just doordash.com or mercedes-benz.com.

     * Paid-advertising platforms — ENUMERATE the actual major US ad
       platforms the target almost certainly ran on, and look each up
       in its public ad library / transparency tool:
         - Meta Ad Library:  facebook.com/ads/library/?q=<TARGET>
           (returns every active FB+IG ad — search this for the target
            and report what's running, who the advertiser of record is,
            and rough impression bands if shown)
         - Google Ads Transparency Center: adstransparency.google.com
           (lists every active Google/YouTube ad by advertiser)
         - TikTok Creative Center / Top Ads: ads.tiktok.com/business/creativecenter
         - iSpot.tv: ispot.tv/brands/<advertiser>  (national TV ad
            spend + impressions across broadcast networks, cable,
            vMVPD live channels, and ad-supported streaming)
         - Trade press spend reports: variety.com, adage.com, mediapost.com
           — search "<TARGET> ad spend" / "marketing budget" / "campaign"
       Then for EACH of these paid platforms produce ONE event in the
       paid_advertising bucket. Use the MODERN TV taxonomy — NEVER
       the term "linear TV". Instead break TV ad spend out into:
         - vMVPD ads — live-TV streaming inventory on YouTube TV,
           Hulu + Live TV, Sling TV, fuboTV, DirecTV Stream
         - FAST ads — free ad-supported streaming TV on Pluto TV
           (Paramount), Tubi (Fox), Amazon Freevee, The Roku Channel,
           Samsung TV Plus, Xumo, LG Channels
         - AVOD ads — ad-tiers of subscription services: Hulu (ad
           tier), Disney+ Basic with Ads, Max with Ads, Peacock
           Premium, Netflix Ads, Paramount+ Essential
         - SVOD home-tile / content placement — paid promotional
           tiles on Netflix homepage, Disney+ promo carousel,
           Max Today's Tops, Prime Video featured banners (even
           ad-free SVOD has paid theatrical promo placement)
       Other paid platforms to enumerate: Google Ads, Meta Ads,
       TikTok Ads, Amazon DSP, The Trade Desk programmatic,
       Snapchat Ads, Spotify Audio, OOH (out-of-home), Yahoo /
       Microsoft Ads, Reddit Ads. Skip a platform ONLY if you're
       confident the target did NOT run there. Aim for 8-12
       paid_advertising events, not 0-3.

  2. For EACH discovered event, estimate what fraction of US gen-pop
     (US adults 16+ ~= 260M) it likely REACHED, using the actor's
     follower count × US share × engagement rate, or publication's
     monthly US uniques × article share. Be honest about confidence.

  3. Roll the events up by parent channel. Each channel gets one total
     reach_pct_of_genpop number that's the union (NOT the sum — overlap
     matters; if 5 TikTok creators each reached 8% of genpop and have
     ~40% audience overlap, combined union ≈ 22-28%).

  4. For movies: ALSO produce an endpoint_breakdown — what fraction of
     converters bought their ticket via each ticketing site (Fandango /
     AMC / Atom / Regal / Cinemark / studio direct). Base on the
     ticketing sites' US market share, any pre-sale ranking reporting,
     and any window-specific signals (e.g. "Fandango reported Goat
     accounted for 38% of pre-sales on opening Friday"). For
     websites/TV shows, omit endpoint_breakdown.

Output JSON EXACTLY in this shape (no code fences, no markdown):

{
  "target":                "<the target name>",
  "target_type":           "movie|website|tv_show|brand",
  "implied_audience":      <int — for movies: tickets × 0.775; for websites: monthly_uniques; for TV: total US viewers>,
  "us_genpop_baseline":    260000000,
  "confidence":            "high" | "medium" | "low",
  "sources_consulted":     ["Box Office Mojo as of 5/15/2026", "TikTok web search", "Variety", ...],
  "notes":                 "1-3 sentences on what surprised you, what the dominant driver was, etc.",
  "marketing_footprint": {
    "social_media": {
      "reach_pct_of_genpop": 14.5,
      "events": [
        {"platform": "tiktok", "actor": "@charlidamelio", "actor_followers": 152000000,
         "us_share_of_followers": 0.34, "event_type": "promo post",
         "url": "https://tiktok.com/@charlidamelio/video/...",
         "estimated_reach_us": 18000000, "reach_pct_of_genpop": 6.9,
         "confidence": "high", "notes": "Single dance video tagging the soundtrack, May 8"},
        {"platform": "instagram", "actor": "@kingjames", "actor_followers": 158000000,
         "us_share_of_followers": 0.50, "event_type": "story",
         "estimated_reach_us": 12000000, "reach_pct_of_genpop": 4.6,
         "confidence": "medium", "notes": "Story slide with movie poster, ~24h visibility"}
      ]
    },
    "press":              {"reach_pct_of_genpop": ..., "events": [
        {"publication": "Variety",            "headline": "<actual article headline you found>",  "byline": "<author name>",  "url": "https://variety.com/2026/film/news/<actual-slug>-1235998765/",          "publication_monthly_us_uniques": 17000000, "estimated_reach_us":  650000, "reach_pct_of_genpop": 0.25, "date": "2026-01-12", "confidence": "high",   "notes": "Cite the article URL"},
        {"publication": "Deadline",           "headline": "<actual article headline>",            "byline": "<author>",       "url": "https://deadline.com/2026/01/<actual-slug>/",                            "publication_monthly_us_uniques": 12000000, "estimated_reach_us":  480000, "reach_pct_of_genpop": 0.18, "date": "2026-01-13", "confidence": "high",   "notes": "..."},
        {"publication": "The Hollywood Reporter", "headline": "<headline>",                        "byline": "<author>",       "url": "https://www.hollywoodreporter.com/movies/movie-news/<actual-slug>/",     "publication_monthly_us_uniques": 11000000, "estimated_reach_us":  420000, "reach_pct_of_genpop": 0.16, "date": "2026-01-14", "confidence": "high",   "notes": "..."},
        {"publication": "IndieWire",          "headline": "<headline>",                            "byline": "<author>",       "url": "https://www.indiewire.com/news/<actual-slug>/",                           "publication_monthly_us_uniques":  6500000, "estimated_reach_us":  220000, "reach_pct_of_genpop": 0.08, "date": "2026-01-15", "confidence": "medium", "notes": "..."},
        {"publication": "Collider",           "headline": "<headline>",                            "byline": "<author>",       "url": "https://collider.com/<actual-slug>/",                                     "publication_monthly_us_uniques":  8500000, "estimated_reach_us":  280000, "reach_pct_of_genpop": 0.11, "date": "2026-01-16", "confidence": "medium", "notes": "..."},
        {"publication": "ScreenRant",         "headline": "<headline>",                            "byline": "<author>",       "url": "https://screenrant.com/<actual-slug>/",                                   "publication_monthly_us_uniques": 18000000, "estimated_reach_us":  680000, "reach_pct_of_genpop": 0.26, "date": "2026-01-17", "confidence": "medium", "notes": "..."},
        {"publication": "Entertainment Weekly", "headline": "<headline>",                          "byline": "<author>",       "url": "https://ew.com/movies/<actual-slug>/",                                    "publication_monthly_us_uniques":  9000000, "estimated_reach_us":  320000, "reach_pct_of_genpop": 0.12, "date": "2026-01-18", "confidence": "medium", "notes": "..."},
        {"publication": "Vulture / New York Magazine", "headline": "<headline>",                   "byline": "<author>",       "url": "https://www.vulture.com/article/<actual-slug>.html",                     "publication_monthly_us_uniques":  7500000, "estimated_reach_us":  260000, "reach_pct_of_genpop": 0.10, "date": "2026-01-19", "confidence": "low",    "notes": "..."}
    ]},
    "talent_mentions":    {"reach_pct_of_genpop": ..., "events": [{"talent": "Steph Curry", "platform": "podcast",   "estimated_reach_us": ..., ...}]},
    "creator_influencers":{"reach_pct_of_genpop": ..., "events": [{"creator": "MrBeast",    "platform": "youtube",   "estimated_reach_us": ..., ...}]},
    "brand_partnerships": {"reach_pct_of_genpop": ..., "events": [
        {"partner": "Mercedes-Benz USA",  "category": "auto",              "campaign": "<actual campaign name — e.g. Super Bowl LX spot featuring Goat>",                            "us_only": true, "url": "https://news.mercedes-benz.com/<actual-slug>",            "estimated_reach_us": 18000000, "reach_pct_of_genpop": 6.9, "date": "2026-01-10", "confidence": "high",   "notes": "Cite Mercedes press release URL"},
        {"partner": "DoorDash",           "category": "delivery",          "campaign": "<actual campaign — e.g. DashPass exclusive ticket bundle + in-app Goat-themed promo>",       "us_only": true, "url": "https://about.doordash.com/en-us/news/<actual-slug>",     "estimated_reach_us":  9500000, "reach_pct_of_genpop": 3.7, "date": "2026-01-12", "confidence": "high",   "notes": "Cite DoorDash newsroom URL"},
        {"partner": "McDonald's USA",     "category": "QSR",               "campaign": "<actual campaign — e.g. Goat Happy Meal toys, US restaurants>",                              "us_only": true, "url": "https://www.mcdonalds.com/us/en-us/promotions/<actual-slug>", "estimated_reach_us": 22000000, "reach_pct_of_genpop": 8.5, "date": "2026-01-08", "confidence": "high",   "notes": "Cite McDonald's US promo page"},
        {"partner": "Coca-Cola",          "category": "beverage",          "campaign": "<actual campaign — e.g. limited-edition Goat cans + theater concession promo>",              "us_only": true, "url": "https://www.coca-colacompany.com/media-center/<actual-slug>", "estimated_reach_us": 14000000, "reach_pct_of_genpop": 5.4, "date": "2026-01-14", "confidence": "high",   "notes": "Cite Coca-Cola press URL"},
        {"partner": "Lay's / Frito-Lay",  "category": "snacks",            "campaign": "<actual campaign — e.g. limited-edition flavor + on-pack code>",                             "us_only": true, "url": "https://www.fritolay.com/news/<actual-slug>",             "estimated_reach_us":  8000000, "reach_pct_of_genpop": 3.1, "date": "2026-01-15", "confidence": "medium", "notes": "Cite Frito-Lay newsroom"},
        {"partner": "AMC Stubs",          "category": "theater loyalty",   "campaign": "<actual campaign — e.g. AMC Stubs members get early-access Goat screenings + double points>", "us_only": true, "url": "https://www.amctheatres.com/amcstubs/<actual-slug>",     "estimated_reach_us":  6500000, "reach_pct_of_genpop": 2.5, "date": "2026-01-09", "confidence": "high",   "notes": "AMC Stubs page or AMC press release"},
        {"partner": "Chase Sapphire",     "category": "credit card",       "campaign": "<actual campaign — e.g. Chase cardholders early-access to Goat tickets via Fandango>",       "us_only": true, "url": "https://creditcards.chase.com/news/<actual-slug>",       "estimated_reach_us":  4500000, "reach_pct_of_genpop": 1.7, "date": "2026-01-11", "confidence": "medium", "notes": "Chase movie-ticket presale is a recurring tie-in"},
        {"partner": "Spotify",            "category": "music",             "campaign": "<actual campaign — e.g. official Goat soundtrack playlist + branded home tile>",             "us_only": true, "url": "https://newsroom.spotify.com/<actual-slug>",              "estimated_reach_us":  7500000, "reach_pct_of_genpop": 2.9, "date": "2026-01-13", "confidence": "medium", "notes": "Cite Spotify Newsroom"},
        {"partner": "Funko",              "category": "merchandise",       "campaign": "<actual campaign — e.g. Goat Pop! Vinyl line, US retail exclusive at Walmart + Target>",     "us_only": true, "url": "https://funko.com/blog/<actual-slug>",                    "estimated_reach_us":  3500000, "reach_pct_of_genpop": 1.3, "date": "2026-01-16", "confidence": "medium", "notes": "Funko blog or press release"},
        {"partner": "T-Mobile Tuesdays",  "category": "telco loyalty",     "campaign": "<actual campaign — e.g. T-Mobile Tuesdays Goat-ticket giveaway via Fandango app>",           "us_only": true, "url": "https://www.t-mobile.com/news/<actual-slug>",             "estimated_reach_us":  5000000, "reach_pct_of_genpop": 1.9, "date": "2026-01-14", "confidence": "medium", "notes": "T-Mobile Tuesdays is a recurring movie tie-in surface"}
    ]},
    "reviews_critics":    {"reach_pct_of_genpop": ..., "events": [{"site": "Rotten Tomatoes","score": 87, "estimated_reach_us": ..., ...}]},
    "paid_advertising":   {"reach_pct_of_genpop": ..., "events": [
        {"platform": "Google Ads",       "network": "Search + YouTube TrueView",   "campaign": "pre-roll skippable + display remarketing",  "creative_type": "video + display",   "placement": "YouTube + Display Network",        "url": "https://ads.google.com/...", "spend_usd_estimate": 4500000,  "estimated_reach_us": 28000000, "reach_pct_of_genpop": 10.8, "date": "2026-01-10", "confidence": "high",   "notes": "Cite Google Ads Transparency Center, ad-library listing, or trade press estimate"},
        {"platform": "Meta Ads",         "network": "Facebook + Instagram",         "campaign": "Reels + Feed video + Stories",              "creative_type": "video + carousel",  "placement": "Reels / Feed / Stories",            "url": "https://www.facebook.com/ads/library/?q=<TARGET>", "spend_usd_estimate": 3200000, "estimated_reach_us": 21000000, "reach_pct_of_genpop": 8.1,  "date": "2026-01-12", "confidence": "high",   "notes": "Cite Meta Ad Library — REQUIRED to search this for the target"},
        {"platform": "TikTok Ads",       "network": "TikTok",                       "campaign": "Spark Ads + TopView",                       "creative_type": "short-form video",  "placement": "For You feed + TopView",            "url": "https://library.tiktok.com/ads?...", "spend_usd_estimate": 2100000, "estimated_reach_us": 14000000, "reach_pct_of_genpop": 5.4,  "date": "2026-01-15", "confidence": "medium", "notes": "Cite TikTok Creative Center / Top Ads"},
        {"platform": "Amazon DSP",       "network": "Amazon + IMDb + Twitch",       "campaign": "programmatic OLV + display",                "creative_type": "video + display",   "placement": "Fire TV + IMDb + Twitch + Amazon",  "url": "",                                   "spend_usd_estimate": 1800000, "estimated_reach_us": 12000000, "reach_pct_of_genpop": 4.6,  "date": "2026-01-08", "confidence": "medium", "notes": "Estimate from Amazon ad-network reach reports"},
        {"platform": "vMVPD ads",        "network": "YouTube TV + Hulu + Live TV + Sling + fuboTV + DirecTV Stream", "campaign": "live-TV streaming 15s + 30s spots", "creative_type": "video", "placement": "live-channel ad pods (sports + news + entertainment)", "url": "", "spend_usd_estimate": 1800000, "estimated_reach_us": 14000000, "reach_pct_of_genpop": 5.4,  "date": "2026-01-18", "confidence": "medium", "notes": "Cite iSpot.tv for vMVPD GRPs / impressions"},
        {"platform": "FAST ads",         "network": "Pluto TV + Tubi + Freevee + Roku Channel + Samsung TV Plus + Xumo", "campaign": "free-streaming spots + branded channel",     "creative_type": "video",             "placement": "pre-roll + mid-roll on free streaming channels", "url": "", "spend_usd_estimate":  900000, "estimated_reach_us":  9500000, "reach_pct_of_genpop": 3.7,  "date": "2026-01-19", "confidence": "medium", "notes": "FAST has explosive reach 2025-26; cite Roku/Tubi/Freevee ad data"},
        {"platform": "AVOD ads",         "network": "Hulu ad-tier + Disney+ Basic + Max Ads + Peacock Premium + Netflix Ads + Paramount+ Essential", "campaign": "ad-tier 15s + 30s + binge-ad", "creative_type": "video", "placement": "pre-roll + mid-roll on ad-supported SVOD tiers", "url": "", "spend_usd_estimate": 3200000, "estimated_reach_us": 24000000, "reach_pct_of_genpop": 9.2, "date": "2026-01-20", "confidence": "high",   "notes": "AVOD tiers grew massively 2024-26; cite Disney/Netflix ads reach reports"},
        {"platform": "SVOD content tile","network": "Netflix + Disney+ + Max + Prime Video homepage / promo carousels", "campaign": "paid theatrical promo home-tile placement", "creative_type": "static + video tile", "placement": "homepage carousel + Today's Tops + Featured banners", "url": "", "spend_usd_estimate": 1100000, "estimated_reach_us": 22000000, "reach_pct_of_genpop": 8.5, "date": "2026-01-17", "confidence": "medium", "notes": "Even pure-SVOD ad-free tiers carry paid theatrical promo tiles (studio-negotiated)"},
        {"platform": "The Trade Desk",   "network": "programmatic display + CTV",   "campaign": "open-web display + CTV remarketing",        "creative_type": "display + video",   "placement": "long-tail web + CTV apps",          "url": "",                                   "spend_usd_estimate":  900000, "estimated_reach_us":  6000000, "reach_pct_of_genpop": 2.3,  "date": "2026-01-14", "confidence": "low",    "notes": "Standard programmatic add-on for major movie launches"},
        {"platform": "Snapchat Ads",     "network": "Snapchat",                     "campaign": "AR Lens + Snap Ads",                        "creative_type": "AR + video",        "placement": "Discover + AR camera",              "url": "",                                   "spend_usd_estimate":  650000, "estimated_reach_us":  4500000, "reach_pct_of_genpop": 1.7,  "date": "2026-01-18", "confidence": "low",    "notes": "Common for movies targeting under-25 audience"},
        {"platform": "Spotify Audio",    "network": "Spotify",                      "campaign": "audio + podcast pre-roll",                  "creative_type": "audio",             "placement": "free-tier audio + podcasts",        "url": "",                                   "spend_usd_estimate":  400000, "estimated_reach_us":  3500000, "reach_pct_of_genpop": 1.3,  "date": "2026-01-11", "confidence": "low",    "notes": "Audio reach estimate"},
        {"platform": "OOH (out-of-home)","network": "Lamar / Clear Channel / Intersection", "campaign": "billboards + transit + theater lobby", "creative_type": "static + digital OOH", "placement": "LA + NYC + top 25 DMAs",  "url": "",                                   "spend_usd_estimate": 1200000, "estimated_reach_us":  9000000, "reach_pct_of_genpop": 3.5,  "date": "2026-01-22", "confidence": "medium", "notes": "Geopath impression estimate for major-market OOH buy"}
    ]},
    "showtime_searches":  {"reach_pct_of_genpop": ..., "events": [
        {"site": "Google",           "feature": "Showtimes near me Knowledge Panel widget", "query": "<TARGET> showtimes near me",        "search_spike_pct": 850, "estimated_reach_us": 38000000, "reach_pct_of_genpop": 14.6, "date": "2026-01-17", "url": "https://www.google.com/search?q=<TARGET>+showtimes", "confidence": "high",   "notes": "Google's built-in showtime widget is the biggest surface — most users never click off Google for showtimes"},
        {"site": "Fandango",         "feature": "movie detail page + Top Sellers carousel", "query": "<TARGET> fandango",                  "search_spike_pct": 620, "estimated_reach_us": 15000000, "reach_pct_of_genpop":  5.8, "date": "2026-01-17", "url": "https://www.fandango.com/the-goat-2026-tickets",     "confidence": "high",   "notes": "~50M monthly US uniques, mostly showtime intent. Cite Fandango Top Sellers if listed"},
        {"site": "Google Maps",      "feature": "movie theater near me",                    "query": "movie theater near me",              "search_spike_pct": 180, "estimated_reach_us": 12000000, "reach_pct_of_genpop":  4.6, "date": "2026-01-17", "url": "https://www.google.com/maps/search/movie+theater", "confidence": "medium", "notes": "Maps-first behavior; users tap a theater to see what's playing"},
        {"site": "AMC Theatres",     "feature": "app + amctheatres.com showtime listing",   "query": "AMC <TARGET> showtimes",             "search_spike_pct": 410, "estimated_reach_us":  9000000, "reach_pct_of_genpop":  3.5, "date": "2026-01-17", "url": "https://www.amctheatres.com/movies/the-goat",         "confidence": "high",   "notes": "Largest US chain by screens, ~12M app users + web"},
        {"site": "Atom Tickets",     "feature": "movie detail + showtime listing",          "query": "<TARGET> atom tickets",              "search_spike_pct": 220, "estimated_reach_us":  3500000, "reach_pct_of_genpop":  1.3, "date": "2026-01-17", "url": "https://www.atomtickets.com/movies/the-goat",         "confidence": "medium", "notes": "Smaller than Fandango but loyal user base"},
        {"site": "Regal Cinemas",    "feature": "app + regmovies.com showtime listing",     "query": "Regal <TARGET> showtimes",           "search_spike_pct": 310, "estimated_reach_us":  6500000, "reach_pct_of_genpop":  2.5, "date": "2026-01-17", "url": "https://www.regmovies.com/movies/the-goat",           "confidence": "high",   "notes": "#2 US chain"},
        {"site": "Cinemark",         "feature": "app + cinemark.com showtime listing",      "query": "Cinemark <TARGET> showtimes",        "search_spike_pct": 240, "estimated_reach_us":  4800000, "reach_pct_of_genpop":  1.8, "date": "2026-01-17", "url": "https://www.cinemark.com/movies/the-goat",            "confidence": "high",   "notes": "#3 US chain"},
        {"site": "Regional chains (Marcus / Alamo / Harkins / B&B / Landmark / Cinépolis)", "feature": "aggregated regional theater chain showtime lookups", "query": "<TARGET> showtimes <city>", "search_spike_pct": 150, "estimated_reach_us":  3500000, "reach_pct_of_genpop":  1.3, "date": "2026-01-17", "url": "", "confidence": "medium", "notes": "Long-tail aggregated reach across non-coastal DMAs"},
        {"site": "IMDb",             "feature": "showtimes feature (imdb.com/showtimes/title/<tt-id>)", "query": "<TARGET> imdb showtimes", "search_spike_pct":  90, "estimated_reach_us":  1500000, "reach_pct_of_genpop":  0.6, "date": "2026-01-17", "url": "https://www.imdb.com/showtimes/title/tt27613895/",    "confidence": "medium", "notes": "Legacy but still indexed; pulls from local theater feeds"},
        {"site": "Bing",             "feature": "Showtimes near me (Edge / Windows default)", "query": "<TARGET> showtimes near me",        "search_spike_pct":  60, "estimated_reach_us":  1800000, "reach_pct_of_genpop":  0.7, "date": "2026-01-17", "url": "https://www.bing.com/search?q=<TARGET>+showtimes",    "confidence": "low",    "notes": "Default engine on Windows + Edge — meaningful older-demo reach"}
    ]},
    "ticketing_sites":    {"reach_pct_of_genpop": ..., "events": [{"site": "AMC Theatres", "visit_share_pct": ..., "estimated_reach_us": ..., ...}]},
    "soundtrack_music":   {"reach_pct_of_genpop": ..., "events": [
        {"platform": "Spotify",        "track": "<lead single title>",          "album": "<TARGET> (Original Motion Picture Soundtrack)", "artist": "<artist>",  "url": "https://open.spotify.com/track/<actual-id>",       "streams_us":  8500000, "estimated_reach_us":  8500000, "reach_pct_of_genpop": 3.3, "date": "2026-01-10", "confidence": "high",   "notes": "Cite Spotify track page"},
        {"platform": "Apple Music",    "track": "<lead single title>",          "album": "<TARGET> (OST)",                                 "artist": "<artist>",  "url": "https://music.apple.com/us/song/<actual-slug>/<id>", "streams_us":  3200000, "estimated_reach_us":  3200000, "reach_pct_of_genpop": 1.2, "date": "2026-01-10", "confidence": "high",   "notes": "Cite Apple Music song page"},
        {"platform": "YouTube",        "track": "<lead single> (Official Audio)", "album": "<TARGET> (OST)",                               "artist": "<artist>",  "url": "https://www.youtube.com/watch?v=<actual-id>",      "streams_us":  6500000, "estimated_reach_us":  6500000, "reach_pct_of_genpop": 2.5, "date": "2026-01-10", "confidence": "high",   "notes": "Cite YouTube official audio / MV page"},
        {"platform": "Spotify",        "track": "Full OST album",               "album": "<TARGET> (Original Motion Picture Soundtrack)", "artist": "<composer / Various Artists>", "url": "https://open.spotify.com/album/<actual-id>", "streams_us":  4500000, "estimated_reach_us":  4500000, "reach_pct_of_genpop": 1.7, "date": "2026-01-12", "confidence": "high",   "notes": "Cite Spotify album page"},
        {"platform": "Apple Music",    "track": "Full OST album",               "album": "<TARGET> (OST)",                                 "artist": "<composer>", "url": "https://music.apple.com/us/album/<actual-slug>/<id>", "streams_us": 1800000, "estimated_reach_us":  1800000, "reach_pct_of_genpop": 0.7, "date": "2026-01-12", "confidence": "high",   "notes": "Cite Apple Music album page"},
        {"platform": "Spotify",        "track": "Featured on 'New Music Friday'", "album": "Spotify editorial playlist",                  "artist": "Spotify",   "url": "https://open.spotify.com/playlist/37i9dQZF1DX4JAvHpjipBk", "streams_us":  3000000, "estimated_reach_us":  3000000, "reach_pct_of_genpop": 1.2, "date": "2026-01-11", "confidence": "medium", "notes": "Cite the specific playlist URL"},
        {"platform": "Spotify",        "track": "<additional notable track>",   "album": "<TARGET> (OST)",                                 "artist": "<artist>",  "url": "https://open.spotify.com/track/<actual-id>",       "streams_us":  1500000, "estimated_reach_us":  1500000, "reach_pct_of_genpop": 0.6, "date": "2026-01-12", "confidence": "medium", "notes": "Second notable track from OST"},
        {"platform": "YouTube",        "track": "<lead single> (Official Music Video)", "album": "<TARGET> (OST)",                       "artist": "<artist>",  "url": "https://www.youtube.com/watch?v=<actual-id>",      "streams_us":  4200000, "estimated_reach_us":  4200000, "reach_pct_of_genpop": 1.6, "date": "2026-01-11", "confidence": "high",   "notes": "Cite YouTube official MV page"},
        {"platform": "TikTok",         "track": "<lead single> — trending sound", "album": "<TARGET> (OST)",                              "artist": "<artist>",  "url": "https://www.tiktok.com/music/<actual-slug>-<id>",  "streams_us":  9500000, "estimated_reach_us":  9500000, "reach_pct_of_genpop": 3.7, "date": "2026-01-13", "confidence": "medium", "notes": "Cite TikTok sound page + creators-using count if visible"},
        {"platform": "Amazon Music",   "track": "Full OST album",               "album": "<TARGET> (OST)",                                 "artist": "<composer>", "url": "https://music.amazon.com/albums/<actual-id>",     "streams_us":   900000, "estimated_reach_us":   900000, "reach_pct_of_genpop": 0.3, "date": "2026-01-12", "confidence": "medium", "notes": "Cite Amazon Music album page"}
    ]},
    "organic_search":     {"reach_pct_of_genpop": ..., "events": [
        {"engine": "Google",          "query": "<TARGET> showtimes",            "intent": "pre-purchase / ticket lookup", "estimated_searches_us_in_window": 1200000, "estimated_reach_us":  950000, "reach_pct_of_genpop": 0.37, "trend_peak_date": "2026-01-17", "date": "2026-01-15", "url": "https://trends.google.com/trends/explore?q=<TARGET>+showtimes", "confidence": "high",   "notes": "Cite Google Trends spike"},
        {"engine": "Google Maps",     "query": "<TARGET> near me",              "intent": "pre-purchase / theater lookup", "estimated_searches_us_in_window":  800000, "estimated_reach_us":  650000, "reach_pct_of_genpop": 0.25, "trend_peak_date": "2026-01-17", "date": "2026-01-15", "url": "", "confidence": "medium", "notes": "Maps gets opening-weekend showtime traffic"},
        {"engine": "YouTube",         "query": "<TARGET> trailer",              "intent": "pre-purchase / interest",       "estimated_searches_us_in_window": 2400000, "estimated_reach_us": 1900000, "reach_pct_of_genpop": 0.73, "trend_peak_date": "2026-01-08", "date": "2026-01-05", "url": "https://www.youtube.com/results?search_query=<TARGET>+trailer", "confidence": "high",   "notes": "Trailer search peaks 1-2 weeks pre-release"},
        {"engine": "Google",          "query": "is <TARGET> good",              "intent": "pre-purchase / validation",     "estimated_searches_us_in_window":  450000, "estimated_reach_us":  380000, "reach_pct_of_genpop": 0.15, "trend_peak_date": "2026-01-18", "date": "2026-01-16", "url": "", "confidence": "high",   "notes": "Validation query — strong purchase intent"},
        {"engine": "Google",          "query": "<TARGET> rotten tomatoes",      "intent": "pre-purchase / reviews",        "estimated_searches_us_in_window":  600000, "estimated_reach_us":  500000, "reach_pct_of_genpop": 0.19, "trend_peak_date": "2026-01-17", "date": "2026-01-16", "url": "", "confidence": "high",   "notes": "RT score lookup classic pre-purchase signal"},
        {"engine": "Reddit",          "query": "<TARGET> review",               "intent": "pre-purchase / social proof",   "estimated_searches_us_in_window":  220000, "estimated_reach_us":  180000, "reach_pct_of_genpop": 0.07, "trend_peak_date": "2026-01-19", "date": "2026-01-17", "url": "https://www.reddit.com/search/?q=<TARGET>", "confidence": "medium", "notes": "Reddit search + r/movies threads"},
        {"engine": "TikTok",          "query": "<TARGET> movie",                "intent": "pre-purchase / vibe-check",     "estimated_searches_us_in_window":  900000, "estimated_reach_us":  700000, "reach_pct_of_genpop": 0.27, "trend_peak_date": "2026-01-17", "date": "2026-01-15", "url": "https://www.tiktok.com/search?q=<TARGET>", "confidence": "medium", "notes": "Gen-Z discovery channel"},
        {"engine": "Google",          "query": "<TARGET> cast",                 "intent": "pre-purchase / curiosity",      "estimated_searches_us_in_window":  280000, "estimated_reach_us":  230000, "reach_pct_of_genpop": 0.09, "trend_peak_date": "2026-01-15", "date": "2026-01-14", "url": "", "confidence": "high",   "notes": "Cast lookups peak release week"},
        {"engine": "Google",          "query": "<TARGET> runtime",              "intent": "pre-purchase / planning",       "estimated_searches_us_in_window":  150000, "estimated_reach_us":  120000, "reach_pct_of_genpop": 0.05, "trend_peak_date": "2026-01-17", "date": "2026-01-16", "url": "", "confidence": "medium", "notes": "Length lookup before booking"},
        {"engine": "Bing",            "query": "<TARGET> showtimes",            "intent": "pre-purchase / ticket lookup", "estimated_searches_us_in_window":  180000, "estimated_reach_us":  140000, "reach_pct_of_genpop": 0.05, "trend_peak_date": "2026-01-17", "date": "2026-01-15", "url": "", "confidence": "low",    "notes": "Bing default on Windows + Edge"}
    ]},
    "press_reviews":      {"reach_pct_of_genpop": ..., "events": [
        {"publication": "The New York Times", "headline": "<actual review headline>",                "byline": "<critic name>",  "score_or_grade": "B+ / 3.5-star / 80",      "url": "https://www.nytimes.com/2026/01/15/movies/<actual-slug>-review.html",       "publication_monthly_us_uniques": 95000000, "estimated_reach_us": 1200000, "reach_pct_of_genpop": 0.46, "date": "2026-01-15", "confidence": "high",   "notes": "Cite the review URL"},
        {"publication": "Roger Ebert.com",    "headline": "<review headline>",                       "byline": "<critic>",       "score_or_grade": "3/4 stars",                "url": "https://www.rogerebert.com/reviews/<actual-slug>-2026",                     "publication_monthly_us_uniques":  3500000, "estimated_reach_us":  140000, "reach_pct_of_genpop": 0.05, "date": "2026-01-15", "confidence": "high",   "notes": "..."},
        {"publication": "Variety (review)",   "headline": "<review headline>",                       "byline": "<critic>",       "score_or_grade": "positive",                 "url": "https://variety.com/2026/film/reviews/<actual-slug>-1235998765/",            "publication_monthly_us_uniques": 17000000, "estimated_reach_us":  640000, "reach_pct_of_genpop": 0.25, "date": "2026-01-14", "confidence": "high",   "notes": "..."},
        {"publication": "/Film (Slashfilm)",  "headline": "<review headline>",                       "byline": "<critic>",       "score_or_grade": "8/10",                     "url": "https://www.slashfilm.com/<actual-slug>-review/",                            "publication_monthly_us_uniques":  4200000, "estimated_reach_us":  150000, "reach_pct_of_genpop": 0.06, "date": "2026-01-16", "confidence": "medium", "notes": "..."},
        {"publication": "The A.V. Club",      "headline": "<review headline>",                       "byline": "<critic>",       "score_or_grade": "B",                        "url": "https://www.avclub.com/<actual-slug>-review-1234567890",                     "publication_monthly_us_uniques":  3800000, "estimated_reach_us":  130000, "reach_pct_of_genpop": 0.05, "date": "2026-01-15", "confidence": "medium", "notes": "..."},
        {"publication": "IGN",                "headline": "<review headline>",                       "byline": "<critic>",       "score_or_grade": "8.5",                      "url": "https://www.ign.com/articles/<actual-slug>-review",                          "publication_monthly_us_uniques": 24000000, "estimated_reach_us":  820000, "reach_pct_of_genpop": 0.32, "date": "2026-01-16", "confidence": "medium", "notes": "..."}
    ]},
    "forum_discussion":   {"reach_pct_of_genpop": ..., "events": [{"forum": "r/movies",   "url": "...", "upvotes": 4200, "comments": 580, "estimated_reach_us": ..., ...}]}
  },
  "endpoint_breakdown": [
    {"endpoint": "Fandango",       "share_pct": 38.0, "url_pattern": "fandango.com",     "notes": "..."},
    {"endpoint": "AMC Theatres",   "share_pct": 22.0, "url_pattern": "amctheatres.com",  "notes": "..."},
    {"endpoint": "Atom Tickets",   "share_pct": 14.0, "url_pattern": "atomtickets.com",  "notes": "..."},
    {"endpoint": "Regal",          "share_pct": 12.0, "url_pattern": "regmovies.com",    "notes": "..."},
    {"endpoint": "Cinemark",       "share_pct":  8.0, "url_pattern": "cinemark.com",     "notes": "..."},
    {"endpoint": "Studio direct",  "share_pct":  6.0, "url_pattern": "sonypictures.com", "notes": "..."}
  ]
}

Hard rules:
  * Every discovered event MUST cite the URL where you found it (in
    the event's "url" field). For press / press_reviews events, the
    "url" MUST be the SPECIFIC ARTICLE URL — never just the
    publication homepage. A url like "https://variety.com" or
    "variety.com" is INVALID for a press event; it must look like
    "https://variety.com/2026/film/news/<slug>-1235998765/" with a
    path beyond the bare domain. If you genuinely cannot find the
    article URL via web_search, drop the event rather than fall
    back to the homepage. Same rule for press_reviews — link the
    specific review, not the critic's bio page.
  * Every event must have a "date" or "date_estimate" field IN ISO format
    that falls inside the date window provided in the user message.
  * For EVERY channel, AIM for the top ~10 events — reason about
    the full surface stack within that channel and enumerate it,
    don't stop at 2-3 obvious entries. Specifically:
        social_media:          8-12 events (top creators per platform)
        press:                 8-12 articles (across 8+ outlets)
        talent_mentions:       6-10 events (cast + cameo posts)
        creator_influencers:   8-12 events (across YT/TT/IG/podcasts)
        brand_partnerships:   8-12 US-only co-promo deals (QSR /
                               delivery / auto / beverage / snacks /
                               apparel / banking / theater loyalty /
                               music / merchandise / telco)
        reviews_critics:       4-8 aggregator entries (RT / Metacritic
                               / Letterboxd / IMDb / CinemaScore)
        paid_advertising:      5-10 platforms (Google/Meta/TT/Amazon/etc.)
        showtime_searches:    10-12 surfaces (Google widget + every
                               major chain + long-tail Bing/IMDb)
        ticketing_sites:       6-10 (every major chain + studio direct)
        soundtrack_music:     8-12 PLAYABLE music links (Spotify +
                               Apple Music + YouTube + TikTok sound +
                               Amazon Music + playlist appearances)
        organic_search:        8-12 queries across 4+ engines
        press_reviews:         5-8 critic reviews
        forum_discussion:      6-10 threads (Reddit + Letterboxd + niche)
    A channel returning 0-3 events is a RED FLAG that you didn't
    reason hard enough — go back and enumerate more before
    submitting. Only return events:[] when the target genuinely
    has zero footprint on that channel.
  * Every estimated_reach_us must be a REAL number you can defend from
    the actor's audited follower count, the publication's monthly
    uniques, the platform's reported reach for similar campaigns, etc.
    Cite the source in the event's notes.
  * Use the baseline 260,000,000 US adults to compute reach_pct_of_genpop.
  * Roll-up reach_pct_of_genpop per channel = union, not sum
    (assume 30-50% audience overlap between events within a channel).
  * For target_type='movie': endpoint_breakdown is REQUIRED and share_pct
    values across endpoints must sum to ~100. For website / TV show /
    brand: omit endpoint_breakdown (or return []).
  * Be honest about confidence ('low' if you had to extrapolate from
    weak signals).
  * paid_advertising events MUST use these field names so the dashboard
    can render them: "platform" (REQUIRED — human-readable name like
    "Google Ads", "Meta Ads", "TikTok Ads", "Amazon DSP", "vMVPD ads",
    "FAST ads", "AVOD ads", "SVOD content tile", "The Trade Desk",
    "Snapchat Ads", "Spotify Audio", "OOH (out-of-home)",
    "Yahoo / Microsoft Ads", "Reddit Ads"). NEVER use the term
    "Linear TV" or "linear TV" — break TV ad spend into vMVPD /
    FAST / AVOD / SVOD instead,
    "network" (sub-property — e.g. "Search + YouTube TrueView"),
    "campaign" (creative concept), "creative_type", "placement",
    "spend_usd_estimate" (a real number you can defend), and the
    standard "estimated_reach_us" / "reach_pct_of_genpop" / "date" /
    "url" / "confidence" / "notes". Do NOT just put "channel": "TV" —
    the dashboard renders rows as "<platform> — <campaign>".
  * soundtrack_music events MUST use these field names so the
    dashboard can render them: "platform" (REQUIRED — "Spotify",
    "Apple Music", "YouTube", "YouTube Music", "TikTok", "Tidal",
    "Amazon Music", "SoundCloud", "Bandcamp" — NOT "wikipedia" or
    "press"), "track" (REQUIRED — song title or "Full OST album"),
    "album" (the soundtrack album name), "artist", "url" (REQUIRED —
    a PLAYABLE streaming URL on that platform; NEVER Wikipedia,
    never a press article, never the artist's homepage. Must match
    one of: open.spotify.com/track|album|playlist/<id>,
    music.apple.com/us/song|album/<slug>/<id>,
    music.youtube.com/watch?v=<id>, www.youtube.com/watch?v=<id>,
    tidal.com/browse/track/<id>, music.amazon.com/albums/<id>,
    soundcloud.com/<artist>/<track>, www.tiktok.com/music/<slug>),
    "streams_us" (best estimate of US plays in window), and the
    standard estimated_reach_us / reach_pct_of_genpop / date /
    confidence / notes. Aim for 8-12 events covering 3+ streaming
    platforms — typically the lead single on Spotify+Apple+YouTube
    (3 events, same song, different surface), the full OST album
    on Spotify+Apple+Amazon (3 events), 1-2 additional notable
    tracks, the TikTok trending sound, and 1-2 editorial playlist
    appearances. If you cannot find a playable streaming URL, DROP
    the event rather than fall back to a Wikipedia or news page.
  * brand_partnerships events MUST use these field names so the
    dashboard can render them: "partner" (REQUIRED — US brand name,
    e.g. "Mercedes-Benz USA", "DoorDash", "McDonald's USA"),
    "category" (REQUIRED — "QSR", "delivery", "auto", "beverage",
    "snacks", "apparel", "banking / credit card", "theater loyalty",
    "music", "merchandise", "telco loyalty", "retail exclusive"),
    "campaign" (REQUIRED — short human-readable description of the
    actual collab, e.g. "DashPass exclusive ticket bundle"),
    "us_only" (REQUIRED — must be `true`; drop the event entirely
    if the campaign is foreign-market-only), "url" (REQUIRED —
    SPECIFIC collab announcement / press release / campaign page,
    NEVER the brand's homepage), and the standard
    estimated_reach_us / reach_pct_of_genpop / date / confidence /
    notes. Aim for 8-12 events covering distinct brand categories.
    Run explicit "<TARGET> brand partnership" / tie-in / co-promo
    web_searches before submitting; don't skip well-known categories
    like delivery (DoorDash), auto (Mercedes/Ford), or QSR
    (McDonald's). DROP every partnership that is not active in the
    United States.
  * showtime_searches events MUST use these field names so the
    dashboard can render them: "site" (REQUIRED — "Google",
    "Fandango", "Google Maps", "AMC Theatres", "Atom Tickets",
    "Regal Cinemas", "Cinemark", regional-chain aggregate, "IMDb",
    "Bing", "Yelp / Apple Maps", "Moviefone"), "feature" (REQUIRED
    — the specific surface within the site, e.g. "Showtimes near me
    Knowledge Panel widget", "Top Sellers carousel", "movie detail
    page"), "query" (the actual user query that would land here),
    "search_spike_pct" (search-volume lift vs baseline), "url"
    (specific page URL — never just the bare domain when a movie
    page exists, e.g. fandango.com/the-goat-2026-tickets not
    fandango.com), and the standard estimated_reach_us /
    reach_pct_of_genpop / date / confidence / notes. Aim for 10-12
    events covering the full showtime-surface stack documented in
    the system instructions. Even if individual reach is small,
    enumerate the long-tail surfaces (Bing, IMDb, regional chains)
    rather than returning only Fandango + Google.
  * press and press_reviews events MUST use these field names so the
    dashboard can render them: "publication" (REQUIRED — e.g.
    "Variety", "The Hollywood Reporter", "The New York Times"),
    "headline" (REQUIRED — the actual article/review headline you
    found, not a paraphrase), "byline" (author name if visible),
    "url" (REQUIRED — specific article URL with a path beyond the
    bare domain), "publication_monthly_us_uniques" (best estimate),
    and the standard estimated_reach_us / reach_pct_of_genpop /
    date / confidence / notes. For press_reviews also include
    "score_or_grade" (e.g. "B+", "3.5/4 stars", "80/100", or
    "positive"/"mixed"/"negative" if no numeric score). Aim for
    8-12 press events and 5-8 press_reviews events. The dashboard
    renders rows as 'Publication - "<headline>"'.
  * organic_search events MUST use these field names so the dashboard
    can render them: "engine" (REQUIRED — "Google", "Google Maps",
    "YouTube", "Bing", "DuckDuckGo", "Yahoo", "Apple Spotlight / Siri",
    "Reddit", "TikTok", "Amazon"), "query" (REQUIRED — the actual
    search string a real person would type, e.g. "the goat showtimes"
    NOT just "showtimes"), "intent" (one of "pre-purchase / ticket
    lookup", "pre-purchase / interest", "pre-purchase / validation",
    "pre-purchase / reviews", "pre-purchase / social proof",
    "pre-purchase / curiosity", "pre-purchase / planning",
    "post-purchase / spoilers", "post-purchase / streaming"),
    "estimated_searches_us_in_window" (your best estimate),
    "trend_peak_date", and the standard estimated_reach_us /
    reach_pct_of_genpop / date / url / confidence / notes. Aim for
    8-12 distinct events spread across 4+ engines. For movies still
    in theatrical window, ALL events must be pre-purchase intents —
    drop streaming / blu-ray / "watch online" queries entirely. The
    dashboard renders rows as '<engine> — "<query>"'.

CRITICAL OUTPUT RULES — your response MUST be parseable JSON:
  1. After you finish web_searching, your FINAL text output must be
     EXACTLY one JSON object matching the schema above.
  2. The first character of your final output MUST be `{` and the last
     character MUST be `}`.
  3. Do NOT include any prose, narration, summary of your research,
     "I found that...", "Based on my searches...", or thinking text in
     the final response. JSON ONLY.
  4. Do NOT wrap the JSON in markdown fences (no ```json, no ```).
     Just the raw object."""


def research_marketing_footprint(
    *,
    target_type: str,
    target: str,
    start_date: str = '',
    end_date: str = '',
    max_tokens: int = 16000,
) -> dict:
    """Run the marketing-footprint research agent against the target.

    Uses Claude (Anthropic) with the native `web_search` tool — same engine
    BG.py's persona_research_agent uses for live audience research. Falls
    back from new-vintage web_search_20260209 to legacy _20250305 if the
    primary tool ID is rejected. Allows up to 12 web searches per call so
    Claude can hit each Google vertical (videos/news/forums) + per-platform
    site: queries inside the date window.

    Returns the parsed JSON, or {'_error': '...'} when the lookup can't be
    performed (no ANTHROPIC_API_KEY, Claude rejected, parse failure).
    Results are cached in-memory by (target_type, target).
    """
    t = (target_type or 'general').strip().lower()
    if t not in ('movie', 'website', 'tv_show', 'brand'):
        return {}
    cache_key = f'{t}::{(target or "").strip().lower()}'
    if cache_key in _FOOTPRINT_CACHE:
        return _FOOTPRINT_CACHE[cache_key]

    # Try migration.claude_client first, fall back to bg-webapp/claude_client
    # — same dual-import pattern used elsewhere in this module so the agent
    # works whether we're invoked from the migration path or the web app.
    _claude_messages = None
    _get_claude_client = None
    try:
        from migration.claude_client import claude_messages as _cm, get_claude_client as _gc
        _claude_messages = _cm; _get_claude_client = _gc
    except ImportError:
        try:
            from claude_client import claude_messages as _cm, get_claude_client as _gc  # type: ignore
            _claude_messages = _cm; _get_claude_client = _gc
        except ImportError:
            return {'_error': 'claude_client not importable'}
    try:
        if _get_claude_client() is None:
            return {'_error': 'ANTHROPIC_API_KEY not configured'}
    except Exception as e:
        return {'_error': f'Claude client check failed: {e}'}

    if start_date and end_date:
        window = f' running between {start_date} and {end_date}'
        gdate = (f'\n\nDate-window: only include events with dates BETWEEN '
                 f'{start_date} AND {end_date} (inclusive). For each Google '
                 f'vertical search, use the URL parameter '
                 f'`tbs=cdr:1,cd_min:{start_date},cd_max:{end_date}` (or '
                 f'translate to MM/DD/YYYY if the tool requires). Drop any '
                 f'event without a verifiable date in the window.')
    else:
        window = ''
        gdate = ''
    user_msg = (
        f'Research the marketing footprint for the {t.replace("_"," ")} '
        f'"{target}"{window}. Use your web_search tool aggressively — hit '
        f'each Google vertical (short videos, news, forums) AND per-platform '
        f'site:tiktok.com / site:instagram.com / site:youtube.com / '
        f'site:x.com / site:reddit.com searches. Discover the specific '
        f'real-world marketing events (celeb posts, press articles, '
        f'influencer videos, brand partnerships, showtime-search spikes, '
        f'ticketing-site share) and estimate the US-genpop reach of each '
        f'one. Return JSON ONLY matching the schema in the system '
        f'prompt.{gdate}'
    )

    import os
    claude_model = (
        os.environ.get('JOURNEY_IQ_RESEARCH_MODEL')
        or os.environ.get('CLAUDE_PERSONA_MODEL')
        or 'claude-opus-4-7'
    )
    _ws_new = {'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 12}
    _ws_old = {'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 12}
    def _has_json(t: str) -> bool:
        return ('{' in t and '}' in t and t.find('{') < t.rfind('}'))

    raw = ''
    try:
        raw = _claude_messages(
            system=_SYSTEM_FOOTPRINT, user=user_msg, model=claude_model,
            max_tokens=max_tokens, temperature=0.3, tools=[_ws_new],
        ) or ''
    except Exception as e:
        print(f'[Journey IQ footprint] primary Claude+web_search failed: {e}')
        raw = ''
    if not _has_json(raw):
        if raw:
            print(f'[Journey IQ footprint] primary returned no JSON — falling back to Sonnet. snippet: {raw[:200]!r}')
        # Retry with legacy web_search tool ID + Sonnet (same pattern bg.py uses)
        try:
            raw = _claude_messages(
                system=_SYSTEM_FOOTPRINT, user=user_msg,
                model='claude-sonnet-4-6',
                max_tokens=max_tokens, temperature=0.3, tools=[_ws_old],
            ) or ''
        except Exception as e:
            return {'_error': f'Claude+web_search fallback also failed: {e}'}
    if not _has_json(raw):
        return {'_error': 'Claude returned no JSON', '_raw': raw[:500]}

    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    start = raw.find('{'); end = raw.rfind('}')
    if start < 0 or end < 0:
        return {'_error': 'AI response had no JSON object', '_raw': raw[:500]}
    try:
        parsed = json.loads(raw[start: end + 1])
    except Exception as e:
        return {'_error': f'JSON parse failed: {e}', '_raw': raw[:500]}

    # Normalize: ensure every documented channel exists in the output
    # (with empty events list if Claude didn't return one) so the
    # dashboard's bubble loop doesn't need conditional rendering.
    parsed.setdefault('marketing_footprint', {})
    for ch in _FOOTPRINT_CHANNELS:
        parsed['marketing_footprint'].setdefault(ch, {'reach_pct_of_genpop': 0.0, 'events': []})

    # Safety net: rewrite any "Linear TV" leak in paid_advertising
    # platform/network labels into the modern vMVPD/FAST/AVOD/SVOD
    # taxonomy. Claude is instructed to never use "linear TV" but
    # this defends against prompt-drift on older models.
    paid_evs = (parsed['marketing_footprint'].get('paid_advertising') or {}).get('events') or []
    for ev in paid_evs:
        plat_raw = str(ev.get('platform') or '')
        net_raw  = str(ev.get('network')  or '')
        if 'linear tv' in plat_raw.lower() or plat_raw.lower() == 'tv':
            ev['platform'] = 'AVOD ads'
            if not net_raw or 'broadcast' in net_raw.lower() or 'cable' in net_raw.lower():
                ev['network'] = 'Hulu ad-tier + Disney+ Basic + Max Ads + Peacock Premium + Netflix Ads + Paramount+ Essential'
            if 'linear-tv-rewrite' not in (ev.get('notes') or ''):
                ev['notes'] = (ev.get('notes') or '') + ' [agent said "linear TV" — auto-rewritten to AVOD ads; linear-tv-rewrite]'
        if 'linear tv' in net_raw.lower():
            ev['network'] = net_raw.replace('linear TV', 'vMVPD live channels').replace('Linear TV', 'vMVPD live channels').replace('linear tv', 'vMVPD live channels')
    parsed.setdefault('us_genpop_baseline', US_GENPOP_BASELINE)
    parsed.setdefault('confidence', 'medium')
    parsed.setdefault('sources_consulted', [])
    parsed.setdefault('notes', '')
    parsed.setdefault('endpoint_breakdown', [])  # only populated for movies

    _FOOTPRINT_CACHE[cache_key] = parsed
    return parsed


def footprint_to_spider(footprint: dict, *, target: str = '') -> dict:
    """Convert a research_marketing_footprint() result into a 3-layer
    spider/Sankey-shaped graph the dashboard can render with plain SVG:

        [TARGET]
            |
        [channel_1] [channel_2] [channel_3] ...
            |           |           |
        [event_a]   [event_d]   [event_g]    (top events per channel)
        [event_b]   [event_e]
        [event_c]
              \\        |        /
               \\       |       /
                [CONVERTER COHORT]
                  /  |  |  \\
            [Fandango][AMC][Atom][Studio]   (endpoint_breakdown for movies)
                  \\  |  |  /
                  [CONVERSION]

    Returns:
      {'target': '<name>',
       'channels': [{...}, ...],            (top channels by reach)
       'events':   [{...}, ...],            (flat, every event with channel+rank)
       'endpoints':[{...}, ...],            (ticketing sites; empty for non-movies)
       'edges':    [{'from': '...', 'to': '...', 'weight': N}, ...]}
    """
    fp = (footprint or {}).get('marketing_footprint') or {}
    endpoints = list((footprint or {}).get('endpoint_breakdown') or [])
    target_name = target or footprint.get('target') or 'Target'

    label_map = {
        'social_media':         'Social Media',
        'press':                'Press',
        'talent_mentions':      'Talent Mentions',
        'creator_influencers':  'Creator / Influencer',
        'brand_partnerships':   'Brand Partnerships',
        'reviews_critics':      'Reviews / Critics',
        'paid_advertising':     'Paid Advertising',
        'showtime_searches':    'Showtime Searches',
        'ticketing_sites':      'Ticketing Sites',
        'soundtrack_music':     'Soundtrack / Music',
        'organic_search':       'Organic Search',
        'press_reviews':        'Press Reviews',
        'forum_discussion':     'Forums / Reddit',
    }
    channels: list[dict] = []
    events_flat: list[dict] = []
    edges: list[dict] = []

    for ch_key, ch_data in fp.items():
        reach = float((ch_data or {}).get('reach_pct_of_genpop') or 0.0)
        evs   = list((ch_data or {}).get('events') or [])
        if reach <= 0 and not evs:
            continue
        evs_sorted = sorted(evs, key=lambda e: -float(e.get('reach_pct_of_genpop') or 0))[:10]
        channels.append({
            'id':                  f'ch:{ch_key}',
            'channel':             ch_key,
            'label':               label_map.get(ch_key, ch_key.replace('_', ' ').title()),
            'reach_pct_of_genpop': round(reach, 1),
            'event_count':         len(evs_sorted),
            'events':              evs_sorted,
        })
        # edge: TARGET -> channel
        edges.append({'from': 'target', 'to': f'ch:{ch_key}',
                      'weight': round(reach, 1)})
        # flat event list with channel attribution
        for i, e in enumerate(evs_sorted):
            ev_id = f'ev:{ch_key}:{i}'
            actor = (e.get('actor') or e.get('publication') or e.get('talent')
                     or e.get('creator') or e.get('partner') or e.get('site')
                     or e.get('forum') or e.get('track') or e.get('engine')
                     or e.get('channel') or 'event')
            events_flat.append({
                'id':           ev_id,
                'channel':      ch_key,
                'channel_label':label_map.get(ch_key, ch_key),
                'rank':         i + 1,
                'label':        str(actor),
                'url':          e.get('url') or '',
                'reach_us':     int(e.get('estimated_reach_us') or 0),
                'reach_pct':    round(float(e.get('reach_pct_of_genpop') or 0), 1),
                'confidence':   e.get('confidence') or '',
                'date':         e.get('date') or e.get('date_estimate') or '',
                'notes':        e.get('notes') or '',
                'raw':          e,
            })
            # edge: channel -> event
            edges.append({'from': f'ch:{ch_key}', 'to': ev_id,
                          'weight': round(float(e.get('reach_pct_of_genpop') or 0), 1)})

    # Endpoint nodes (movies: ticketing sites). Edge from cohort -> each.
    endpoint_nodes = []
    for i, ep in enumerate(endpoints):
        ep_id = f'ep:{i}'
        endpoint_nodes.append({
            'id':         ep_id,
            'endpoint':   ep.get('endpoint') or f'Endpoint {i+1}',
            'share_pct':  round(float(ep.get('share_pct') or 0), 1),
            'url_pattern':ep.get('url_pattern') or '',
            'notes':      ep.get('notes') or '',
        })
        edges.append({'from': 'cohort', 'to': ep_id,
                      'weight': round(float(ep.get('share_pct') or 0), 1)})
        edges.append({'from': ep_id, 'to': 'conversion',
                      'weight': round(float(ep.get('share_pct') or 0), 1)})

    # Always: every channel funnels into the cohort node.
    for ch in channels:
        edges.append({'from': ch['id'], 'to': 'cohort',
                      'weight': ch['reach_pct_of_genpop']})

    return {
        'target':    target_name,
        'channels':  sorted(channels, key=lambda c: -c['reach_pct_of_genpop']),
        'events':    events_flat,
        'endpoints': endpoint_nodes,
        'edges':     edges,
    }


def footprint_to_bubbles(footprint: dict) -> list[dict]:
    """Convert a research_marketing_footprint() result into the bubble
    schema the dashboard renders. Output:
      [
        {'channel': 'social_media', 'label': 'Social Media',
         'reach_pct_of_genpop': 14.5, 'events': [...]},
        ...
      ]
    Channels are returned in descending-reach order with empty channels
    filtered out (so the bubble chart only renders signal, not noise).
    """
    fp = (footprint or {}).get('marketing_footprint') or {}
    label_map = {
        'social_media':         'Social Media',
        'press':                'Press',
        'talent_mentions':      'Talent Mentions',
        'creator_influencers':  'Creator / Influencer',
        'brand_partnerships':   'Brand Partnerships',
        'reviews_critics':      'Reviews / Critics',
        'paid_advertising':     'Paid Advertising',
        'showtime_searches':    'Showtime Searches',
        'ticketing_sites':      'Ticketing Sites',
        'soundtrack_music':     'Soundtrack / Music',
        'organic_search':       'Organic Search',
        'press_reviews':        'Press Reviews',
        'forum_discussion':     'Forums / Reddit',
    }
    out = []
    for ch_key, ch_data in fp.items():
        reach = float((ch_data or {}).get('reach_pct_of_genpop') or 0.0)
        events = list((ch_data or {}).get('events') or [])
        if reach <= 0 and not events:
            continue
        out.append({
            'channel':             ch_key,
            'label':               label_map.get(ch_key, ch_key.replace('_', ' ').title()),
            'reach_pct_of_genpop': round(reach, 1),
            'events':              events,
            'event_count':         len(events),
        })
    out.sort(key=lambda b: -b['reach_pct_of_genpop'])
    return out


__all__ = [
    'TARGET_TYPES',
    'US_GENPOP_BASELINE',
    'DEFAULT_TICKET_PRICE',
    'DEFAULT_AUDIENCE_FRACTION',
    'WEBSITE_DEDUP_FACTOR',
    'SPARSE_COHORT_THRESHOLD',
    'compute_implied_audience',
    'compute_website_implied_audience',
    'compute_tv_show_implied_audience',
    'compute_implied_audience_for_type',
    'compute_scaling_factor',
    'scale_summary_counts',
    'synthesize_movie_journey',
    'synthesize_journey',
    'synth_to_dashboard_payload',
    'blend_real_and_modeled',
    'research_audience_size',
    'research_marketing_footprint',
    'footprint_to_bubbles',
    'footprint_to_spider',
    'research_site_funnel',
]


# ── Public: site-funnel research agent ──────────────────────────────────
# Used when target_type='website' and we want to model what happens to
# people who LAND on the site but don't convert there. Produces a
# `site_funnel` block describing:
#   - visitor split: converted-on-site / switched-and-bought-elsewhere /
#                    never-transacted
#   - switched_destinations: where the price-shoppers ended up buying
#   - inception_referrers:   how visitors arrived at the site
#   - companion_behaviors:   adjacent verticals the visitor planned for
#                            (dinner reservations, parking, hotels, etc.)
#
# Drives a new "Visitor Funnel" set of dashboard cards. Wired into
# run_research_anchored_job() so any website-typed run gets the funnel
# block populated alongside the marketing-footprint block.

_FUNNEL_CACHE: dict[str, dict] = {}


_SYSTEM_SITE_FUNNEL = """\
You are a senior consumer-behavior analyst specializing in PURCHASE
FUNNEL ANALYSIS for transactional websites. You have web_search. Your
job is to MODEL what really happens to a representative cohort of US
visitors who land on a target site during a date window — how many
convert on the site, how many shop around and ultimately buy
elsewhere, how many abandon entirely, where the switchers end up, what
adjacent verticals they plan for around the purchase (e.g. dinner
before the movie), and how they're arriving at the site in the first
place. You reason like an attribution analyst at the studio / brand
side — concrete, grounded in real industry data.

CRITICAL — reason hard before answering. Don't give 2-3 generic
buckets. Enumerate the long tail.

Output JSON EXACTLY in this shape (no code fences, no markdown):

{
  "target":                "<site name, e.g. Fandango>",
  "url_pattern":           "<root domain, e.g. fandango.com>",
  "us_genpop_baseline":    260000000,
  "visitors_us_in_window": <int — US monthly uniques * (window_days/30)>,
  "vertical":              "<the vertical, e.g. movie ticketing, food delivery, hotel booking, e-commerce>",
  "confidence":            "high" | "medium" | "low",
  "sources_consulted":     ["SimilarWeb fandango.com Jan 2026", "Comscore movie-ticketing share Q4 2025", "Nielsen dining + movie crossover 2024", ...],
  "notes":                 "1-3 sentence summary of the funnel shape and what surprised you",

  "funnel_split": {
    "converted_on_site_pct":      <0-100 — what % bought a ticket on the target site itself>,
    "switched_and_bought_pct":    <0-100 — what % left WITHOUT converting but DID transact elsewhere later>,
    "never_transacted_pct":       <0-100 — what % abandoned entirely (no ticket purchase anywhere)>,
    "notes": "Sum must equal ~100. Cite Comscore / SimilarWeb conversion-rate benchmarks for the vertical."
  },

  "switched_destinations": [
    {"destination": "AMC Theatres",  "share_pct_of_switchers": 28.0, "url_pattern": "amctheatres.com", "notes": "Largest US chain; loyalty members often skip Fandango fees"},
    {"destination": "Cinemark",      "share_pct_of_switchers": 14.0, "url_pattern": "cinemark.com",    "notes": "..."},
    {"destination": "Regal",         "share_pct_of_switchers": 12.0, "url_pattern": "regmovies.com",   "notes": "..."},
    {"destination": "Atom Tickets",  "share_pct_of_switchers":  9.0, "url_pattern": "atomtickets.com", "notes": "Lower fees than Fandango on some chains"},
    {"destination": "Theater box office (walk-up)", "share_pct_of_switchers": 22.0, "url_pattern": "n/a (offline)", "notes": "Older demo / impulse buyers"},
    {"destination": "Regional chain direct", "share_pct_of_switchers": 8.0, "url_pattern": "marcustheatres.com | drafthouse.com | harkinstheatres.com", "notes": "Marcus, Alamo Drafthouse, Harkins, B&B, Landmark"}
    // aim for 6-10 distinct destinations covering the whole switch surface
  ],

  "intermediate_journey": [
    {"step": "Google search for reviews",      "share_pct_of_switchers": 62.0, "url_pattern": "google.com/search", "typical_queries": ["<TARGET MOVIE> reviews", "is <MOVIE> good", "<MOVIE> rotten tomatoes"], "notes": "Validation search is the #1 cause of cart abandonment on ticketing sites"},
    {"step": "Rotten Tomatoes / Metacritic",   "share_pct_of_switchers": 38.0, "url_pattern": "rottentomatoes.com | metacritic.com", "typical_queries": [],  "notes": "Score check"},
    {"step": "Reddit r/movies thread",         "share_pct_of_switchers": 18.0, "url_pattern": "reddit.com/r/movies",                  "typical_queries": ["<MOVIE> review reddit"], "notes": "Social proof"},
    {"step": "Google Maps theater search",     "share_pct_of_switchers": 31.0, "url_pattern": "google.com/maps",                      "typical_queries": ["movie theater near me"],  "notes": "Switching to a different theater chain to save fees / find better seats"},
    {"step": "Price comparison across chains", "share_pct_of_switchers": 24.0, "url_pattern": "amctheatres.com | cinemark.com | regmovies.com", "typical_queries": [], "notes": "Direct fee comparison"}
    // aim for 5-8 typical intermediate steps the switcher takes
  ],

  "inception_referrers": [
    {"source": "Google organic search",                     "share_pct_of_inbound": 38.0, "url_pattern": "google.com/search",         "notes": "Direct showtime / movie-name queries"},
    {"source": "Google 'Showtimes near me' Knowledge Panel","share_pct_of_inbound": 22.0, "url_pattern": "google.com/search?...udm=",  "notes": "The showtime widget links directly to Fandango listings"},
    {"source": "Direct / bookmarked",                       "share_pct_of_inbound": 14.0, "url_pattern": "(direct)",                   "notes": "Loyal users typing fandango.com"},
    {"source": "Email / SMS marketing (Fandango VIP+)",     "share_pct_of_inbound":  8.0, "url_pattern": "(email)",                    "notes": "VIP+ rewards push"},
    {"source": "Studio / movie official site link",         "share_pct_of_inbound":  6.0, "url_pattern": "sonypictures.com | warnerbros.com | disney.com", "notes": "'Get Tickets' button on studio sites"},
    {"source": "Social (Instagram / TikTok / X)",           "share_pct_of_inbound":  5.0, "url_pattern": "instagram.com | tiktok.com | x.com", "notes": "Trailer post 'Tickets in bio'"},
    {"source": "Paid Google Ads (Fandango brand bidding)",  "share_pct_of_inbound":  4.0, "url_pattern": "googleadservices.com",       "notes": "Brand-protection paid search"},
    {"source": "Display / programmatic retargeting",        "share_pct_of_inbound":  2.0, "url_pattern": "doubleclick.net | adnxs.com",  "notes": "..."},
    {"source": "App push notification (Fandango app)",      "share_pct_of_inbound":  1.0, "url_pattern": "(app push)",                 "notes": "..."}
    // aim for 8-12 inbound sources covering the full referral mix
  ],

  "companion_behaviors": [
    {"vertical": "Dinner reservation",          "share_pct_of_visitors": 38.0, "top_sites": ["opentable.com", "resy.com", "yelp.com", "google.com/maps"], "typical_window": "1-3 hours before showtime", "notes": "'Dinner and a movie' is the dominant adjacent vertical — ~38% of theater visits include a sit-down dinner; ~22% include a casual quick-serve meal"},
    {"vertical": "Ride / parking",              "share_pct_of_visitors": 24.0, "top_sites": ["uber.com", "lyft.com", "spothero.com", "parkmobile.com"], "typical_window": "30-60 min before showtime", "notes": "Urban moviegoers (NYC/SF/CHI) heavily use Uber + SpotHero"},
    {"vertical": "Pre/post drinks (bar)",       "share_pct_of_visitors": 18.0, "top_sites": ["yelp.com", "google.com/maps", "untappd.com"], "typical_window": "+/- 90 min around showtime", "notes": "Theater-adjacent bars; Drafthouse chains have in-theater bar"},
    {"vertical": "Babysitter / childcare (family demo)", "share_pct_of_visitors": 7.0, "top_sites": ["care.com", "urbansitter.com"], "typical_window": "1-3 days before", "notes": "Date-night planning"},
    {"vertical": "Hotel (out-of-town premiere)","share_pct_of_visitors":  3.0, "top_sites": ["booking.com", "expedia.com", "hotels.com"], "typical_window": "1-7 days before", "notes": "Premiere-week tourists; mostly NYC / LA"},
    {"vertical": "Concession / food delivery to theater", "share_pct_of_visitors": 5.0, "top_sites": ["doordash.com", "ubereats.com"], "typical_window": "30 min before showtime", "notes": "Mostly Alamo Drafthouse + Studio Movie Grill"},
    {"vertical": "Merchandise / fan gear",      "share_pct_of_visitors":  4.0, "top_sites": ["amazon.com", "boxlunch.com", "shopdisney.com"], "typical_window": "1-7 days before / after", "notes": "Marvel / Star Wars / animated-tentpole tie-ins"},
    {"vertical": "Date-prep (hair / nails / outfit)", "share_pct_of_visitors": 6.0, "top_sites": ["amazon.com", "shein.com", "stylepit-style"], "typical_window": "1-2 days before", "notes": "Premiere / opening-weekend date crowd"}
    // aim for 6-10 distinct companion verticals
  ]
}

Hard rules:
  * funnel_split percentages MUST sum to ~100 (±2 for rounding).
  * switched_destinations share_pct_of_switchers MUST sum to ~100.
  * inception_referrers share_pct_of_inbound MUST sum to ~100.
  * Every percent must be a defensible estimate. Cite the source class
    in the event's notes (SimilarWeb, Comscore, Nielsen, industry
    benchmark, etc.). When you genuinely don't know, set
    confidence: "low" and explain the assumption.
  * For companion_behaviors, share_pct_of_visitors is OVERLAP with the
    cohort that visited the target site — these are NOT mutually
    exclusive (a single user can have a dinner reservation AND an
    Uber ride). Each row's share_pct can be evaluated independently;
    they do NOT need to sum to 100.
  * Use web_search to ground the numbers — pull SimilarWeb / Comscore /
    Nielsen / Statista figures where they exist. For the dinner-and-a-
    movie figure specifically, cite Nielsen Scarborough or
    Restaurant Business Online's theatergoer studies.
  * Aim for top ~10 entries per list (switched_destinations,
    intermediate_journey, inception_referrers, companion_behaviors).
    A list returning 2-3 entries is a RED FLAG that you didn't
    enumerate the long tail.

CRITICAL OUTPUT RULES — your response MUST be parseable JSON:
  1. After you finish web_searching, your FINAL text output must be
     EXACTLY one JSON object matching the schema above.
  2. The first character of your final output MUST be `{` and the last
     character MUST be `}`.
  3. Do NOT include any prose, narration, summary of your research,
     "I found that...", "Based on my searches...", or thinking text in
     the final response. JSON ONLY.
  4. Do NOT wrap the JSON in markdown fences (no ```json, no ```)."""


def research_site_funnel(
    *,
    target: str,
    url_pattern: str = '',
    vertical_hint: str = '',
    start_date: str = '',
    end_date: str = '',
    max_tokens: int = 12000,
) -> dict:
    """Run the site-funnel research agent against the target site.

    Same dual-import + Claude+web_search pattern as
    research_marketing_footprint. Returns the parsed JSON or
    {'_error': '...'} on failure. Cached in-memory by target.
    """
    cache_key = f'sitefunnel::{(target or "").strip().lower()}'
    if cache_key in _FUNNEL_CACHE:
        return _FUNNEL_CACHE[cache_key]

    _claude_messages = None
    _get_claude_client = None
    try:
        from migration.claude_client import claude_messages as _cm, get_claude_client as _gc
        _claude_messages = _cm; _get_claude_client = _gc
    except ImportError:
        try:
            from claude_client import claude_messages as _cm, get_claude_client as _gc  # type: ignore
            _claude_messages = _cm; _get_claude_client = _gc
        except ImportError:
            return {'_error': 'claude_client not importable'}
    try:
        if _get_claude_client() is None:
            return {'_error': 'ANTHROPIC_API_KEY not configured'}
    except Exception as e:
        return {'_error': f'Claude client check failed: {e}'}

    window = ''
    if start_date and end_date:
        window = f' running between {start_date} and {end_date}'
    vert = f' (vertical hint: {vertical_hint})' if vertical_hint else ''
    user_msg = (
        f'Model the visitor purchase funnel for the website "{target}"'
        f'{vert}{window}. Use web_search to ground the numbers in real '
        f'industry data (SimilarWeb, Comscore, Nielsen, Statista, '
        f'trade-press benchmarks). Enumerate the top ~10 entries per '
        f'list (switched_destinations, intermediate_journey, '
        f'inception_referrers, companion_behaviors). Return JSON ONLY '
        f'matching the schema in the system prompt.'
    )

    import os
    claude_model = (
        os.environ.get('JOURNEY_IQ_RESEARCH_MODEL')
        or os.environ.get('CLAUDE_PERSONA_MODEL')
        or 'claude-opus-4-7'
    )
    _ws_new = {'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': 10}
    _ws_old = {'type': 'web_search_20250305', 'name': 'web_search', 'max_uses': 10}
    def _has_json(t: str) -> bool:
        return ('{' in t and '}' in t and t.find('{') < t.rfind('}'))

    raw = ''
    try:
        raw = _claude_messages(
            system=_SYSTEM_SITE_FUNNEL, user=user_msg, model=claude_model,
            max_tokens=max_tokens, temperature=0.3, tools=[_ws_new],
        ) or ''
    except Exception as e:
        print(f'[site funnel] primary Claude+web_search failed: {e}')
        raw = ''
    if not _has_json(raw):
        if raw:
            print(f'[site funnel] primary returned no JSON — falling back to Sonnet. snippet: {raw[:200]!r}')
        try:
            raw = _claude_messages(
                system=_SYSTEM_SITE_FUNNEL, user=user_msg,
                model='claude-sonnet-4-6',
                max_tokens=max_tokens, temperature=0.3, tools=[_ws_old],
            ) or ''
        except Exception as e:
            return {'_error': f'Claude+web_search fallback also failed: {e}'}
    if not _has_json(raw):
        return {'_error': 'Claude returned no JSON', '_raw': raw[:500]}

    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    start = raw.find('{'); end = raw.rfind('}')
    if start < 0 or end < 0:
        return {'_error': 'AI response had no JSON object', '_raw': raw[:500]}
    try:
        parsed = json.loads(raw[start: end + 1])
    except Exception as e:
        return {'_error': f'JSON parse failed: {e}', '_raw': raw[:500]}

    parsed.setdefault('us_genpop_baseline', US_GENPOP_BASELINE)
    parsed.setdefault('confidence', 'medium')
    parsed.setdefault('sources_consulted', [])
    parsed.setdefault('notes', '')
    parsed.setdefault('funnel_split', {})
    parsed.setdefault('switched_destinations', [])
    parsed.setdefault('intermediate_journey', [])
    parsed.setdefault('inception_referrers', [])
    parsed.setdefault('companion_behaviors', [])

    _FUNNEL_CACHE[cache_key] = parsed
    return parsed
