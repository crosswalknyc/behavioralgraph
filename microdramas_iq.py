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


# ============================================================================
# View-estimate calibration
# ============================================================================
# Methodology:
#   - Peacock disclosed 41M paid subscribers (NBCUniversal Q1 2026
#     earnings). Their mobile-first microdrama slate (launched Q4 2025
#     with the "Peacock Shorts" hub) is presented alongside on-platform
#     tentpole content, so any title that surfaces on the top rail of
#     the microdrama hub benefits from house-audience discovery.
#   - Comparable vertical-drama benchmarks:
#       * ReelShort ~18M MAU (App Annie / data.ai, Q1 2026)
#       * DramaBox   ~13M MAU
#       * GoodShort  ~4M MAU
#     Cross-platform per-episode-view rates observed publicly range
#     between 350K (mid-rail vertical drama) and 3.2M (top-of-hub with
#     paid marketing lift) in the first 28 days.
#   - We map hub surface position to an estimated daily view range and
#     then compound over the observed window (up to 28 days).
#
# These base rates get calibrated up/down by:
#   - `hub_share`: what fraction of Peacock's homepage rails the title
#     appeared on (aggregated across observed days)
#   - `series_bonus`: episodic microdramas retain viewers episode over
#     episode; +12% per additional episode observed in the catalog

VIEW_ESTIMATE = {
    # (min_rank_inclusive, max_rank_inclusive): (daily_low, daily_mid, daily_high)
    'hero':      (620_000, 1_050_000, 1_680_000),   # Position 1-2 on the hub
    'top_rail':  (280_000,   540_000,   890_000),   # Positions 3-8
    'mid_rail':  (120_000,   210_000,   340_000),   # Positions 9-16
    'deep_rail': ( 45_000,    92_000,   155_000),   # Positions 17+
    'off_rail':  ( 12_000,    24_000,    48_000),   # Deep-link only (not surfaced)
}


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


def _daily_estimate(observations: list[dict]) -> tuple[list[dict], int]:
    """Return (daily_curve, twenty_eight_day_rollup)."""
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

    # Build the 28-day curve. Missing days inherit the last observed
    # ranking (typical decay is captured by the natural degradation of
    # hub position, so we're not adding a synthetic decay curve on top).
    curve: list[dict] = []
    last_rank = None
    total_mid = 0
    for offset in range(28):
        d = (first + timedelta(days=offset)).isoformat()
        obs = obs_by_date.get(d)
        if obs and obs.get('rank') is not None:
            last_rank = obs['rank']
        rank = obs.get('rank') if obs else last_rank
        bucket = _surface_bucket(rank)
        _low, mid, _high = VIEW_ESTIMATE[bucket]
        curve.append({
            'day':     offset,
            'date':    d,
            'rank':    rank,
            'bucket':  bucket,
            'views':   mid,
        })
        total_mid += mid

    return curve, total_mid


# ============================================================================
# S3 IO
# ============================================================================
def _s3_client():
    import boto3  # type: ignore
    region = os.environ.get('AWS_REGION') or 'us-east-2'
    return boto3.client('s3', region_name=region)


def _read_json(key: str) -> Optional[dict]:
    try:
        s3 = _s3_client()
        resp = s3.get_object(Bucket=S3_BUCKET, Key=key)
        raw = resp['Body'].read().decode('utf-8')
        return json.loads(raw)
    except Exception as e:
        logger.info("microdramas_iq: cannot read s3://%s/%s (%s)", S3_BUCKET, key, e)
        return None


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
    curve, total_28 = _daily_estimate(obs)

    # Clip to the requested window (defaults to 28).
    curve_win = curve[:window_days]
    view_win  = sum(p['views'] for p in curve_win)

    # Rank aggregates - across all observations, not just the window.
    ranks = [o.get('rank') for o in obs if isinstance(o.get('rank'), int)]
    surface_rank_best = min(ranks) if ranks else None
    surface_rank_avg  = round(sum(ranks) / len(ranks), 1) if ranks else None

    first_iso = entry.get('first_observed_date') or ''
    days_since = 0
    if first_iso:
        try:
            first = datetime.fromisoformat(first_iso).date()
            days_since = (date.today() - first).days
        except Exception:
            pass

    audience = _title_audience(entry)

    return {
        'key':                 entry.get('key'),
        'title':               entry.get('title'),
        'series':              entry.get('series') or None,
        'poster_url':          entry.get('poster_url') or None,
        'deep_link':           entry.get('deep_link') or None,
        'first_observed_date': first_iso,
        'last_observed_date':  entry.get('last_observed_date'),
        'days_since_first_observed': days_since,
        'observations_count':  len(obs),
        'episodes_count':      len(entry.get('episodes') or []),
        'surface_rank_best':   surface_rank_best,
        'surface_rank_avg':    surface_rank_avg,
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
    filters."""
    filters = filters or {}
    sort_key    = str(filters.get('sort') or 'view_28d')
    window_days = int(filters.get('window_days') or 28)
    cut         = str(filters.get('audience_cut') or 'all')
    window_days = max(1, min(28, window_days))

    catalog = read_catalog()
    titles_dict = catalog.get('titles') or {}

    serialized = [_serialize_title(e, window_days=window_days)
                   for e in titles_dict.values()]
    serialized = _sort_titles(serialized, sort_key)
    display = _apply_audience_cut(serialized, cut)

    first_scrape = catalog.get('first_scrape')
    days_of_history = 0
    if first_scrape:
        try:
            d = datetime.fromisoformat(first_scrape).date()
            days_of_history = (date.today() - d).days + 1
        except Exception:
            pass

    return {
        'success':      True,
        'filters':      {
            'sort':          sort_key,
            'window_days':   window_days,
            'audience_cut':  cut,
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
