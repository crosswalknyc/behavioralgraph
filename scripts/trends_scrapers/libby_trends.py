"""
Libby (OverDrive) trending scraper.

Pulls the "Popular" spotlight from LA County Library's Libby collection,
split by media type (ebook / audiobook / magazine). Libby is powered by
OverDrive; their public Thunder API returns the same ranked lists the
Libby app renders behind `libbyapp.com/library/lacountylibrary/spotlight-popular/page-1`.

Snapshot shape (kind='libby'):

    {
      "source":     "libby_trends",
      "kind":       "libby",
      "label":      "Libby popular",
      "library":    "lacountylibrary",
      "fetched_at": "...",
      "sources": {
        "ebook":     {"label": "Popular eBooks",     "items": [{...}], "available": bool},
        "audiobook": {"label": "Popular Audiobooks", "items": [{...}], "available": bool},
        "magazine":  {"label": "Popular Magazines",  "items": [{...}], "available": bool}
      }
    }

Every `items[i]` has:

    { rank, title, artist, url, image, holds, availability, formats, subjects }

The `url` deep-links back to Libby so users can borrow with one click.

Standalone:

    python3 -m scripts.trends_scrapers.libby_trends
"""

from __future__ import annotations

import logging
import sys
import time
import urllib.parse
from typing import Any

import requests

logger = logging.getLogger(__name__)

_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) '
        'Gecko/20100101 Firefox/120.0')

# LA County's Libby key. Changing this + one line in `_libby_url` swaps
# to a different partner library (SF, NYC, LAPL, etc). Left library-
# specific because Jenna asked for LA County by name.
_LIBRARY_KEY = 'lacountylibrary'

# OverDrive Thunder search API. Sort by popularity DESC = the same rail
# Libby's Popular tab renders. The API is public + rate-friendly (~1
# req/sec fine).
_THUNDER_URL = ('https://thunder.api.overdrive.com/v2/libraries/'
                 f'{_LIBRARY_KEY}/media')


# Media types Libby exposes. Kept as an ordered list so the frontend
# tab order is deterministic (ebook -> audiobook -> magazine).
_MEDIA_TYPES = ['ebook', 'audiobook', 'magazine']


def _libby_deep_link(reserve_id: str, kind: str) -> str:
    """Build a `libbyapp.com` URL that opens the title's card. Works
    across web + mobile Libby via universal-link handling.
    """
    if not reserve_id:
        return ''
    return (f'https://libbyapp.com/library/{_LIBRARY_KEY}/'
            f'similar-{urllib.parse.quote(str(reserve_id))}/page-1')


def _best_cover(covers_obj: dict | None) -> str:
    """Pick the biggest available cover, falling back through
    OverDrive's canonical sizes."""
    covers = covers_obj or {}
    for key in ('cover510Wide', 'cover300Wide', 'cover150Wide', 'cover'):
        val = covers.get(key)
        if isinstance(val, dict):
            href = val.get('href') or val.get('url') or ''
            if href:
                return href
        elif isinstance(val, str) and val:
            return val
    return ''


def _first_creator_name(item: dict) -> str:
    """Prefer the primary author from the `creators` list, fall back to
    `firstCreatorName`. Magazines routinely omit both (publisher is the
    identity), which is why the frontend renders an empty artist row
    cleanly for those."""
    creators = item.get('creators') or []
    for c in creators:
        name = (c or {}).get('name')
        role = (c or {}).get('role') or ''
        if name and role in ('', 'Author', 'Editor'):
            return name
    if creators and (creators[0] or {}).get('name'):
        return creators[0]['name']
    return item.get('firstCreatorName') or ''


def _fetch_media(media_type: str, limit: int = 30) -> list[dict]:
    """Query the Thunder API for one media type. Sorted by popularity
    descending. Retries 2x on 429/5xx.
    """
    params = {
        'sortBy':     'popularity:desc',
        'mediaTypes': media_type,
        'perPage':    str(limit),
        'page':       '1',
    }
    resp = None
    for attempt in range(3):
        try:
            resp = requests.get(_THUNDER_URL,
                                headers={'User-Agent': _UA,
                                          'Accept': 'application/json'},
                                params=params,
                                timeout=15)
        except Exception as e:
            logger.info("libby %s attempt %d: %s", media_type, attempt + 1, e)
            time.sleep(1 + attempt)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            logger.info("libby %s attempt %d: http %s",
                        media_type, attempt + 1, resp.status_code)
            time.sleep(2 + attempt)
            continue
        break
    if not resp or not resp.ok:
        logger.warning("libby %s: gave up (last=%s)", media_type,
                        getattr(resp, 'status_code', None))
        return []
    try:
        data = resp.json()
    except Exception as e:
        logger.warning("libby %s: json parse: %s", media_type, e)
        return []
    raw_items = data.get('items') or []
    items: list[dict] = []
    for i, it in enumerate(raw_items[:limit], start=1):
        rid = it.get('id') or it.get('reserveId') or ''
        title = it.get('title') or ''
        if not title:
            continue
        author = _first_creator_name(it)
        image = _best_cover(it.get('covers'))
        formats = [(f or {}).get('name') for f in (it.get('formats') or []) if (f or {}).get('name')]
        subjects = [(s or {}).get('name') for s in (it.get('subjects') or []) if (s or {}).get('name')]
        items.append({
            'rank':          i,
            'title':         title,
            'artist':        author,           # normalized to match music/book rows
            'url':           _libby_deep_link(rid, media_type),
            'image':         image,
            'reserve_id':    rid,
            'holds':         it.get('holdsCount') or 0,
            'availability':  'Available' if it.get('isAvailable') else 'On hold',
            'formats':       formats[:3],
            'subjects':      subjects[:3],
            'publisher':     (it.get('publisher') or {}).get('name') if isinstance(it.get('publisher'), dict) else it.get('publisher') or '',
        })
    return items


_LABELS = {
    'ebook':     'Popular eBooks',
    'audiobook': 'Popular Audiobooks',
    'magazine':  'Popular Magazines',
}
_SUBS = {
    'ebook':     'Most-borrowed eBooks on Libby right now.',
    'audiobook': 'Most-borrowed audiobooks on Libby right now.',
    'magazine':  'Most-read magazines on Libby right now.',
}


def fetch() -> dict[str, Any]:
    sources: dict[str, dict] = {}
    all_flat: list[dict] = []
    for mt in _MEDIA_TYPES:
        items = _fetch_media(mt, limit=30)
        sources[mt] = {
            'label':     _LABELS[mt],
            'sub':       _SUBS[mt],
            'items':     items,
            'available': bool(items),
        }
        for it in items:
            all_flat.append({**it, 'media_type': mt})

    return {
        'library':   _LIBRARY_KEY,
        # Mirror combined ebook + audiobook (magazines less "trend-y")
        # onto `national` for the standard summary index.
        'national':  (sources['ebook']['items'] +
                      sources['audiobook']['items'])[:50],
        'available': any(s['available'] for s in sources.values()),
        'sources':   sources,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    from ._base import run_scraper
    result = run_scraper('libby_trends', 'Libby popular', 'libby', fetch)
    srcs = result.get('sources') or {}
    for slug, panel in srcs.items():
        print(f"{slug}: n={len(panel.get('items', []))}  ok={panel.get('available')}",
               file=sys.stderr)
        for it in (panel.get('items') or [])[:3]:
            print(f"   #{it['rank']} {it['title']} - {it['artist']}",
                   file=sys.stderr)
