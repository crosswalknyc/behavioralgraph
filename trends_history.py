"""
Historical arcs and per-item trajectories for Trends IQ.

Reconstructs an individual item's day-by-day trajectory from the dated
snapshot archives already written by two sources:

    1. External Google Trends daily snapshots
       s3://dashboard-inputs/blue_iq/trends_rss/v1/{geo}/{YYYY-MM-DD}.json
       Written by external_signals._trends_snap_put() every day per geo.

    2. Scraper daily snapshots
       s3://dashboard-inputs/trends_iq_snapshots/{YYYY-MM-DD}/{source}.json
       Written by scripts.trends_scrapers._base.write_snapshot() from the
       daily cron.

For any (kind, source, key) tuple this module returns a normalized
trajectory:

    {
      "kind":          "search|social|retailer|headline|article|person",
      "source":        "google|x|tiktok|youtube|instagram|target|...",
      "key":           "Fourth of July",
      "geo":           "US" | "State:Texas" | "DMA:New York",
      "days": [
        {"date": "2026-06-29", "rank": 3, "score": 47, "present": true},
        {"date": "2026-06-30", "rank": null, "score": null, "present": false},
        ...
      ],
      "first_seen":    "2026-06-29",
      "last_seen":     "2026-07-07",
      "present_days":  4,
      "best_rank":     1,
      "current_rank":  3,
      "momentum":      "breakout|rising|falling|sustained|dropped|new|new_arc"
    }

Also powers the alerts engine (see `classify_alert_transition`) which
compares yesterday's trajectory point to today's for every watched item.

Public API
----------
- history_for_search(term, *, geo, days) -> dict
- history_for_scraper(source, kind, key, *, days) -> dict
- history_for_item(kind, source, key, *, geo, days) -> dict     # dispatcher
- classify_alert_transition(yesterday, today) -> str            # for alerts

All I/O is cached in-process for the duration of the request; the
day-file S3 reads are cheap (each < 10KB) and paged access is bounded
by `days` (default 14, hard cap 60).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# S3 layout
# ────────────────────────────────────────────────────────────────────────────
_BUCKET = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')

# Google Trends (external_signals writes this)
_TRENDS_RSS_PREFIX = 'blue_iq/trends_rss/v1/'   # + {geo}/{YYYY-MM-DD}.json

# Scraper snapshots (scripts.trends_scrapers._base writes this)
_SCRAPER_DATED_PREFIX = 'trends_iq_snapshots/'  # + {YYYY-MM-DD}/{source}.json

# Per-item history cache (this module writes this)
_HISTORY_CACHE_PREFIX = 'trends_iq/history_cache/'

DEFAULT_DAYS = 14
MAX_DAYS     = 60
CACHE_TTL_S  = 6 * 3600  # rebuild cached trajectories at most every 6h


def _s3():
    try:
        import boto3  # type: ignore
        return boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')
    except Exception as e:
        logger.debug("trends_history: boto3 unavailable (%s)", e)
        return None


# ────────────────────────────────────────────────────────────────────────────
# Key normalization
# ────────────────────────────────────────────────────────────────────────────
_SLUG_CLEAN_RE = re.compile(r'[^a-z0-9]+')


def _slug(s: str) -> str:
    """Case-insensitive, punctuation-insensitive key. Trailing question
    marks, apostrophes, quotes, and Unicode punctuation all normalize
    to the same slug so 'Taylor Swift', 'taylor swift', "Taylor Swift's",
    and 'Taylor  Swift' all match."""
    if not s:
        return ''
    return _SLUG_CLEAN_RE.sub('-', s.strip().lower()).strip('-')


# In-process cache: {(cache_key, day_iso): parsed_snapshot_or_None}. Wiped
# on process restart. Guards against repeated day fetches during a single
# request that hits multiple items.
_DAY_CACHE: dict[tuple[str, str], Optional[dict]] = {}


def _fetch_day(prefix: str, day_iso: str, suffix: str = '') -> Optional[dict]:
    """Fetch one S3 JSON, in-process cached. Returns None on miss."""
    cache_key = (prefix, day_iso + '|' + suffix)
    if cache_key in _DAY_CACHE:
        return _DAY_CACHE[cache_key]
    s3 = _s3()
    if s3 is None:
        _DAY_CACHE[cache_key] = None
        return None
    key = f"{prefix}{day_iso}.json" if not suffix else f"{prefix}{day_iso}/{suffix}.json"
    try:
        resp = s3.get_object(Bucket=_BUCKET, Key=key)
        data = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        data = None
    _DAY_CACHE[cache_key] = data
    return data


def _iter_recent_days(days: int) -> list[str]:
    """Return the last `days` UTC date strings, oldest -> newest."""
    d = min(max(int(days or DEFAULT_DAYS), 1), MAX_DAYS)
    today = date.today()
    return [(today - timedelta(days=i)).isoformat() for i in range(d - 1, -1, -1)]


# ────────────────────────────────────────────────────────────────────────────
# Google Trends historical
# ────────────────────────────────────────────────────────────────────────────
def _google_geo_key(geo: str) -> str:
    """The Google Trends snapshot bucket uses one file per geo. Currently
    the writers store everything under `US`; higher-fidelity per-state /
    per-DMA rollouts still key to `US`. Preserve any explicit geo the
    caller passed in case that changes."""
    return geo or 'US'


def history_for_search(term: str, *, geo: str = 'US',
                        days: int = DEFAULT_DAYS) -> dict:
    """Reconstruct a Google Trends search term's rank/score arc."""
    slug = _slug(term)
    if not slug:
        return _empty_arc('search', 'google', term, geo)
    geo_key = _google_geo_key(geo)
    day_list = _iter_recent_days(days)
    arc_days: list[dict] = []
    for day_iso in day_list:
        rows = _fetch_day(f"{_TRENDS_RSS_PREFIX}{geo_key}/", day_iso)
        row_map = {}
        if isinstance(rows, list):
            for r in rows:
                if isinstance(r, dict):
                    row_map[_slug(r.get('term', ''))] = r
        hit = row_map.get(slug)
        if hit:
            # Some snapshots include a `rank` field, others just imply it
            # from position. We accept either.
            rank = hit.get('rank')
            if rank is None:
                for i, r in enumerate(rows or []):
                    if _slug((r or {}).get('term', '')) == slug:
                        rank = i + 1
                        break
            arc_days.append({
                'date':    day_iso,
                'rank':    int(rank) if rank is not None else None,
                'score':   hit.get('score'),
                'present': True,
                'related': (hit.get('related') or [])[:5],
            })
        else:
            arc_days.append({'date': day_iso, 'rank': None, 'score': None, 'present': False})
    return _summarize_arc(arc_days, kind='search', source='google', key=term, geo=geo)


# ────────────────────────────────────────────────────────────────────────────
# Scraper historical (retailer, social, headline, article, person)
# ────────────────────────────────────────────────────────────────────────────
_SCRAPER_KIND_MAP = {
    # source -> kind (what the render code treats as the row type)
    'x':         'social',
    'tiktok':    'social',
    'youtube':   'social',
    'instagram': 'social',
    'reddit':    'social',
    'target':    'retailer',
    'walmart':   'retailer',
    'etsy':      'retailer',
    'sephora':   'retailer',
    'lululemon': 'retailer',
    'bestbuy':   'retailer',
    'nike':      'retailer',
    'ulta':      'retailer',
    'amazon':    'retailer',
}


def _item_key_from_scraper_row(source: str, row: dict) -> str:
    """The user-visible identifier a row goes by. Retailers use `name`,
    social platforms use `topic`/`title`/`hashtag`. Fall back sensibly."""
    for k in ('name', 'title', 'topic', 'hashtag', 'term', 'headline', 'text'):
        v = row.get(k)
        if v:
            return str(v)
    return ''


def history_for_scraper(source: str, key: str, *,
                        days: int = DEFAULT_DAYS,
                        geo: str = 'National') -> dict:
    """Reconstruct a retailer/social item's rank arc from dated snapshots."""
    slug = _slug(key)
    if not slug:
        return _empty_arc(_SCRAPER_KIND_MAP.get(source, 'unknown'), source, key, geo)
    kind = _SCRAPER_KIND_MAP.get(source, 'unknown')
    day_list = _iter_recent_days(days)
    arc_days: list[dict] = []
    for day_iso in day_list:
        payload = _fetch_day(f"{_SCRAPER_DATED_PREFIX}", day_iso, suffix=source)
        rows: list = []
        if isinstance(payload, dict):
            rows = payload.get('national') or []
        rank = None
        row_hit = None
        for i, r in enumerate(rows):
            if not isinstance(r, dict):
                continue
            if _slug(_item_key_from_scraper_row(source, r)) == slug:
                rank = int(r.get('rank') or (i + 1))
                row_hit = r
                break
        if row_hit is not None:
            arc_days.append({
                'date':    day_iso,
                'rank':    rank,
                'score':   None,
                'present': True,
                'price':   row_hit.get('price'),
                'url':     row_hit.get('url'),
                'image':   row_hit.get('image'),
            })
        else:
            arc_days.append({'date': day_iso, 'rank': None, 'score': None, 'present': False})
    return _summarize_arc(arc_days, kind=kind, source=source, key=key, geo=geo)


# ────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ────────────────────────────────────────────────────────────────────────────
def history_for_item(kind: str, source: str, key: str, *,
                     geo: str = 'National',
                     days: int = DEFAULT_DAYS,
                     force_refresh: bool = False) -> dict:
    """Route to the right historical reconstructor. Kind is a soft hint;
    the source ultimately determines which bucket layout we read."""
    if not key:
        return _empty_arc(kind, source, key, geo)

    cache_slug = f"{kind}:{source}:{_slug(key)}:{_slug(geo)}:{days}"
    if not force_refresh:
        cached = _read_history_cache(cache_slug)
        if cached is not None:
            return cached

    if source == 'google' or kind == 'search':
        arc = history_for_search(key, geo=geo, days=days)
    elif source in _SCRAPER_KIND_MAP:
        arc = history_for_scraper(source, key, days=days, geo=geo)
    else:
        arc = _empty_arc(kind or 'unknown', source, key, geo)
        arc['error'] = f'no historical source for source={source!r} kind={kind!r}'

    _write_history_cache(cache_slug, arc)
    return arc


# ────────────────────────────────────────────────────────────────────────────
# Summarization
# ────────────────────────────────────────────────────────────────────────────
def _summarize_arc(arc_days: list[dict], *, kind: str, source: str,
                   key: str, geo: str) -> dict:
    """Attach derived stats + a momentum label to a day-by-day arc."""
    present = [d for d in arc_days if d.get('present')]
    first_seen  = present[0]['date']  if present else None
    last_seen   = present[-1]['date'] if present else None
    present_days = len(present)
    ranks = [d['rank'] for d in present if d.get('rank') is not None]
    best_rank    = min(ranks) if ranks else None
    current_rank = arc_days[-1]['rank'] if arc_days else None
    prev_rank    = arc_days[-2]['rank'] if len(arc_days) >= 2 else None

    momentum = _momentum(arc_days, present_days, current_rank, prev_rank)

    return {
        'kind':         kind,
        'source':       source,
        'key':          key,
        'geo':          geo,
        'days':         arc_days,
        'first_seen':   first_seen,
        'last_seen':    last_seen,
        'present_days': present_days,
        'total_days':   len(arc_days),
        'best_rank':    best_rank,
        'current_rank': current_rank,
        'prev_rank':    prev_rank,
        'momentum':     momentum,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


def _momentum(arc: list[dict], present_days: int,
              current_rank: Optional[int], prev_rank: Optional[int]) -> str:
    """One-word label describing the trajectory shape."""
    if not arc:
        return 'unknown'
    if current_rank is None and prev_rank is not None:
        return 'dropped'
    if current_rank is not None and prev_rank is None:
        # First appearance in the window vs any prior day where absent
        earlier_absent = any(not d.get('present') for d in arc[:-1])
        return 'new_arc' if not earlier_absent else 'new'
    if current_rank is not None and prev_rank is not None:
        delta = prev_rank - current_rank  # positive = climbed
        if delta >= 3:
            return 'breakout' if present_days <= 3 else 'rising'
        if delta <= -3:
            return 'falling'
        return 'sustained'
    return 'absent'


def _empty_arc(kind: str, source: str, key: str, geo: str) -> dict:
    return {
        'kind': kind, 'source': source, 'key': key, 'geo': geo,
        'days': [], 'first_seen': None, 'last_seen': None,
        'present_days': 0, 'total_days': 0,
        'best_rank': None, 'current_rank': None, 'prev_rank': None,
        'momentum': 'unknown',
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


# ────────────────────────────────────────────────────────────────────────────
# Cache (S3)
# ────────────────────────────────────────────────────────────────────────────
def _cache_key(slug: str) -> str:
    safe = re.sub(r'[^a-z0-9:_-]', '_', slug.lower())
    return f'{_HISTORY_CACHE_PREFIX}{safe}.json'


def _read_history_cache(slug: str) -> Optional[dict]:
    s3 = _s3()
    if s3 is None:
        return None
    try:
        resp = s3.get_object(Bucket=_BUCKET, Key=_cache_key(slug))
        data = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return None
    gen = data.get('generated_at')
    if gen:
        try:
            dt = datetime.fromisoformat(gen.replace('Z', '+00:00'))
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            if age <= CACHE_TTL_S:
                return data
        except Exception:
            pass
    return None


def _write_history_cache(slug: str, arc: dict) -> None:
    s3 = _s3()
    if s3 is None:
        return
    try:
        s3.put_object(
            Bucket=_BUCKET,
            Key=_cache_key(slug),
            Body=json.dumps(arc, ensure_ascii=False).encode('utf-8'),
            ContentType='application/json',
            CacheControl='public, max-age=300',
        )
    except Exception as e:
        logger.debug("history cache write failed for %s: %s", slug, e)


# ────────────────────────────────────────────────────────────────────────────
# Alert classification (used by the digest job)
# ────────────────────────────────────────────────────────────────────────────
def classify_alert_transition(prev_arc: Optional[dict],
                              curr_arc: dict) -> Optional[dict]:
    """Return an alert dict if today's arc represents a notable change vs
    yesterday's, otherwise None.

    prev_arc is the arc as of the previous digest run (yesterday). curr_arc
    is today's freshly computed arc. Both share the same (kind, source,
    key) tuple.

    Alert types:
      NEW           - item just appeared on the chart for the first time
      BREAKOUT      - present <= 3 days AND climbed >= 3 ranks vs yesterday
      RISING        - present > 3 days AND climbed >= 3 ranks vs yesterday
      FALLING       - fell >= 3 ranks vs yesterday
      DROPPED_OFF   - was present yesterday, absent today
      RETURNED      - was absent yesterday, present today
    Rank change < 3 with same status = no alert (suppress noise).
    """
    key_tuple = (curr_arc.get('kind'), curr_arc.get('source'), curr_arc.get('key'))
    curr_rank = curr_arc.get('current_rank')
    prev_rank = (prev_arc or {}).get('current_rank')

    if prev_rank is None and curr_rank is not None:
        atype = 'NEW' if (prev_arc is None or not (prev_arc.get('days'))) else 'RETURNED'
        return _alert(key_tuple, atype, prev_rank, curr_rank, curr_arc)
    if prev_rank is not None and curr_rank is None:
        return _alert(key_tuple, 'DROPPED_OFF', prev_rank, curr_rank, curr_arc)
    if prev_rank is not None and curr_rank is not None:
        delta = prev_rank - curr_rank  # positive = climbed
        if delta >= 3:
            atype = 'BREAKOUT' if curr_arc.get('present_days', 0) <= 3 else 'RISING'
            return _alert(key_tuple, atype, prev_rank, curr_rank, curr_arc)
        if delta <= -3:
            return _alert(key_tuple, 'FALLING', prev_rank, curr_rank, curr_arc)
    return None


def _alert(key_tuple: tuple, atype: str,
            prev_rank: Optional[int], curr_rank: Optional[int],
            curr_arc: dict) -> dict:
    kind, source, key = key_tuple
    return {
        'kind':       kind,
        'source':     source,
        'key':        key,
        'alert_type': atype,
        'prev_rank':  prev_rank,
        'curr_rank':  curr_rank,
        'momentum':   curr_arc.get('momentum'),
        'best_rank':  curr_arc.get('best_rank'),
        'present_days': curr_arc.get('present_days'),
        'first_seen':   curr_arc.get('first_seen'),
        'occurred_at':  datetime.now(timezone.utc).isoformat(),
    }
