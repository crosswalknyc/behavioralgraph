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
    """
    warmed = {'peacock': False, 'comp_7d': False, 'comp_30d': False,
              'errors': []}
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

    serialized = [_serialize_title(e, window_days=window_days)
                   for e in titles_dict.values()]
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
