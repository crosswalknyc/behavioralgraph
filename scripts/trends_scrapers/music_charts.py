"""
Music charts scraper - Shazam Top 200, Apple Music Top 50, TikTok Sounds.

Aggregates the three biggest free music trending signals into a single
snapshot the dashboard renders as one tab:

    Shazam Top 200 US       -> what people are IDing right now (discovery)
    Apple Music Top 50 US   -> what people are streaming right now
    TikTok Trending Sounds  -> what's about to hit the charts (leading indicator)

Snapshot shape (kind='music'):

    {
      "source":     "music_charts",
      "kind":       "music",
      "label":      "Music",
      "fetched_at": "...",
      "sources": {
        "shazam":   {"label": "Shazam Top 200 (US)",   "items": [{...}]},
        "apple":    {"label": "Apple Music Top 50 (US)","items": [{...}]},
        "tiktok":   {"label": "TikTok Sounds",         "items": [{...}], "available": bool}
      }
    }

Every `items[i]` has at least:

    { rank, title, artist, url, image? }

Standalone:

    python3 -m scripts.trends_scrapers.music_charts
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import sys
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) '
        'Gecko/20100101 Firefox/120.0')

# ---------------------------------------------------------------------------
# Shazam Top 200 US  (public CSV endpoint, no auth)
# ---------------------------------------------------------------------------
_SHAZAM_URL = 'https://www.shazam.com/services/charts/csv/top-200/united-states/'


def _fetch_shazam(limit: int = 100) -> list[dict]:
    """CSV format: leading BOM line + date line + 'Rank,Artist,Title' header,
    then Rank,"Artist","Title" rows. `csv.reader` handles the quoting."""
    try:
        r = requests.get(_SHAZAM_URL, headers={'User-Agent': _UA,
                                                 'Accept': 'text/csv, */*'},
                          timeout=20)
    except Exception as e:
        logger.warning("shazam: %s", e)
        return []
    if not r.ok:
        logger.warning("shazam: http %s", r.status_code)
        return []
    text = (r.text or '').lstrip('\ufeff')
    reader = csv.reader(io.StringIO(text))
    items: list[dict] = []
    seen_header = False
    for row in reader:
        if not row:
            continue
        # Skip the "Thursday, 9 July 2026 [performance over the past 7 days]"
        # single-cell line + the header row.
        if not seen_header:
            if row[0].strip().lower() == 'rank':
                seen_header = True
            continue
        if len(row) < 3:
            continue
        try:
            rank = int(row[0].strip())
        except ValueError:
            continue
        artist = row[1].strip()
        title  = row[2].strip()
        if not (artist and title):
            continue
        # Shazam search URL as the deep link. We don't have a track ID
        # in the CSV but the query gets a hit reliably.
        q = requests.utils.quote(f'{title} {artist}')
        items.append({
            'rank':   rank,
            'title':  title,
            'artist': artist,
            'url':    f'https://www.shazam.com/search?q={q}',
        })
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------------------------
# Apple Music Top 50 US  (public RSS/JSON, no auth)
# ---------------------------------------------------------------------------
_APPLE_URL = ('https://rss.applemarketingtools.com/api/v2/us/music/'
               'most-played/50/songs.json')


def _fetch_apple(limit: int = 50) -> list[dict]:
    """Apple's RSS marketing API is normally instant but occasionally
    returns transient 502s. Retry up to 3 times with backoff."""
    import time
    data: dict = {}
    for attempt in range(3):
        try:
            r = requests.get(_APPLE_URL, headers={'User-Agent': _UA}, timeout=15)
        except Exception as e:
            logger.info("apple attempt %d: %s", attempt + 1, e)
            time.sleep(1 + attempt)
            continue
        if r.ok:
            try:
                data = r.json()
                break
            except Exception as e:
                logger.info("apple attempt %d: json parse failed: %s", attempt + 1, e)
                time.sleep(1 + attempt)
                continue
        else:
            logger.info("apple attempt %d: http %s", attempt + 1, r.status_code)
            time.sleep(1 + attempt)
    if not data:
        logger.warning("apple: gave up after 3 attempts")
        return []
    results = ((data or {}).get('feed') or {}).get('results') or []
    items: list[dict] = []
    for i, t in enumerate(results[:limit], start=1):
        items.append({
            'rank':   i,
            'title':  t.get('name') or '',
            'artist': t.get('artistName') or '',
            'url':    t.get('url') or '',
            'image':  t.get('artworkUrl100') or '',
        })
    return items


# ---------------------------------------------------------------------------
# TikTok Sounds  (Creative Center - Playwright-based, see follow-up)
# ---------------------------------------------------------------------------
# TikTok's Creative Center music page is fully client-side rendered and
# their JSON API is now behind X-Bogus request signing (same barrier we
# hit for /popular_trend/hashtag/list). Getting sounds requires the same
# Playwright DOM-scrape flow used by scripts/trends_scrapers/tiktok.py
# with donated ads.tiktok.com cookies to unlock the full list. This is
# a meaningful chunk of work so we're shipping the music tab without
# TikTok Sounds first and treating this scraper stub as a placeholder.
_TIKTOK_URL_TEMPLATE = (
    'https://ads.tiktok.com/creative_radar_api/v1/popular_trend/sound/list'
    '?period=7&page={page}&limit=20&order_by=vv&country_code=US'
)


def _fetch_tiktok_sounds(limit: int = 40, *,
                          cookies: Optional[dict] = None) -> tuple[list[dict], bool]:
    """Returns (items, cookie_ok). Currently a no-op stub - see
    module-level comment. Anonymous API returns 40101 no permission;
    unlocking the feed requires the Playwright DOM path from
    scripts/trends_scrapers/tiktok.py adapted to the /sound/pc/en
    page. Logged as a follow-up. Cookies parameter is retained so
    the follow-up scraper can slot in without a signature change."""
    items:  list[dict] = []
    cookie_ok = False
    for page in (1, 2):
        try:
            r = requests.get(
                _TIKTOK_URL_TEMPLATE.format(page=page),
                headers={
                    'User-Agent':      _UA,
                    'Accept':          'application/json, text/plain, */*',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Referer':         'https://ads.tiktok.com/business/creativecenter/inspiration/popular/music/pc/en',
                },
                cookies=cookies or {},
                timeout=20,
            )
        except Exception as e:
            logger.warning("tiktok sounds page %d: %s", page, e)
            break
        if not r.ok:
            logger.warning("tiktok sounds page %d: http %s", page, r.status_code)
            break
        try:
            data = r.json()
        except Exception:
            logger.warning("tiktok sounds page %d: not json", page)
            break
        payload = ((data or {}).get('data') or {})
        rows    = payload.get('sound_list') or payload.get('list') or []
        if not rows:
            break
        for row in rows:
            # Field names vary between the anonymous preview and the
            # authenticated feed. Guard for both.
            title = row.get('title') or row.get('song_name') or ''
            author = row.get('author_name') or row.get('author') or row.get('musician') or ''
            cover = row.get('cover') or (row.get('cover_medium') or {})
            if isinstance(cover, dict):
                cover_url = cover.get('url_list', [''])[0] if cover.get('url_list') else ''
            else:
                cover_url = cover
            deep_url = (row.get('link') or row.get('detail_url') or
                        row.get('share_url') or '')
            if not title:
                continue
            items.append({
                'rank':   len(items) + 1,
                'title':  title,
                'artist': author,
                'url':    deep_url,
                'image':  cover_url,
            })
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break
        if payload.get('has_more') is False:
            break
    # Cookie was valid if we got >5 items (the anonymous preview caps
    # at ~3-5 so 5+ means the cookie unlocked the full feed).
    cookie_ok = len(items) > 5
    return items, cookie_ok


def _load_tiktok_cookies_from_s3() -> Optional[dict]:
    """Read donated cookies from s3://dashboard-inputs/trends_iq_cookies/
    ads.tiktok.com.json. Returns {name: value} or None if unavailable."""
    try:
        import boto3
        s3  = boto3.client('s3')
        obj = s3.get_object(Bucket='dashboard-inputs',
                             Key='trends_iq_cookies/ads.tiktok.com.json')
        raw = json.loads(obj['Body'].read().decode('utf-8'))
    except Exception as e:
        logger.info("tiktok sounds: no cookies (%s)", e)
        return None
    # Cookie donation shape can be either [{"name":.., "value":..}, ...]
    # or {"name": "value", ...}. Handle both.
    if isinstance(raw, list):
        return {c['name']: c['value'] for c in raw
                if c.get('name') and c.get('value')}
    if isinstance(raw, dict):
        return {k: v for k, v in raw.items() if isinstance(v, str)}
    return None


def fetch() -> dict[str, Any]:
    """Pull all three sources in sequence. Each is best-effort - a single
    source failing produces an empty items[] for that source but the
    snapshot still writes."""
    shazam_items = _fetch_shazam(limit=100)
    apple_items  = _fetch_apple(limit=50)

    # TikTok Sounds intentionally skipped for now (see module comment).
    # When the Playwright DOM scraper is wired up, re-enable:
    #   tt_cookies = _load_tiktok_cookies_from_s3()
    #   tt_items, tt_ok = _fetch_tiktok_sounds(limit=40, cookies=tt_cookies)
    tt_items: list[dict] = []
    tt_ok = False

    return {
        # `national` mirrors Apple's top 50 so the standard snapshot
        # summary in _index.json still shows a useful count. The real
        # breakdown lives in `sources` and is what compute_view reads.
        'national': apple_items[:50],
        'available': bool(shazam_items or apple_items or tt_items),
        'sources': {
            'shazam': {
                'label':     'Shazam Top 200 (US)',
                'sub':       "What people are IDing right now - the discovery signal.",
                'items':     shazam_items,
                'available': bool(shazam_items),
            },
            'apple': {
                'label':     'Apple Music Top 50 (US)',
                'sub':       'What people are streaming right now.',
                'items':     apple_items,
                'available': bool(apple_items),
            },
            'tiktok': {
                'label':     'TikTok Sounds (7d)',
                'sub':       "Leading indicator for chart hits. Coming soon - Playwright build in progress.",
                'items':     tt_items,
                'available': bool(tt_items),
                'cookie_ok': tt_ok,
            },
        },
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    from ._base import run_scraper
    result = run_scraper('music_charts', 'Music', 'music', fetch)
    srcs = result.get('sources') or {}
    for slug, panel in srcs.items():
        print(f"{slug}: n={len(panel.get('items', []))}  ok={panel.get('available')}",
               file=sys.stderr)
        for it in (panel.get('items') or [])[:3]:
            print(f"   #{it['rank']} {it['title']} - {it['artist']}", file=sys.stderr)
