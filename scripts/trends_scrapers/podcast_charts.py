"""
Podcast charts scraper.

Aggregates trending podcast signals into a single snapshot the dashboard
renders as one tab, mirroring the structure of `music_charts.py`.

Sources (2026-07):

    Apple Podcasts Top 100 US    -> `rss.marketingtools.apple.com`, public JSON
    Spotify Podcast Charts       -> stub (their public chart endpoint returns
                                     500 for anonymous callers, and the
                                     `podcastcharts.byspotify.com` UI fetches
                                     the data client-side after an OAuth
                                     handshake). Once Spotify reopens the API
                                     or an operator donates open.spotify.com
                                     cookies, the stub picks up automatically.
    Amazon Music Podcasts        -> stub (music.amazon.com/podcasts renders
                                     client-side; needs donated cookies +
                                     Playwright).
    Audible Podcasts             -> stub (audible.com/pd rendering also client-
                                     side; ships with an operator instruction
                                     until cookies are donated).

Snapshot shape (kind='podcast'):

    {
      "source":     "podcast_charts",
      "kind":       "podcast",
      "label":      "Podcasts",
      "fetched_at": "...",
      "sources": {
        "apple":   {"label": "Apple Podcasts Top 100 (US)", "items": [{...}], "available": bool},
        "spotify": {"label": "Spotify Podcast Charts (US)", "items": [],      "available": False, "sub": "..."},
        "amazon":  {"label": "Amazon Music Podcasts (US)",  "items": [],      "available": False, "sub": "..."},
        "audible": {"label": "Audible Podcasts (US)",       "items": [],      "available": False, "sub": "..."}
      }
    }

Every `items[i]` has at least:

    { rank, title, artist, url, image? }

Standalone:

    python3 -m scripts.trends_scrapers.podcast_charts
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) '
        'Gecko/20100101 Firefox/120.0')


# ---------------------------------------------------------------------------
# Apple Podcasts Top 100 US  (public marketing RSS)
# ---------------------------------------------------------------------------
# 2026-07: rss.applemarketingtools.com was renamed to
# rss.marketingtools.apple.com. Apple still 301s the old hostname but a
# few clients don't follow redirects, so we hit the new hostname directly.
# Higher limits (200, 500) return HTTP 500 the same way Apple Music does;
# 100 is the ceiling for a working public feed.
_APPLE_PODCAST_URL = ('https://rss.marketingtools.apple.com/api/v2/us/'
                       'podcasts/top/100/podcasts.json')


def _fetch_apple_podcasts(limit: int = 100) -> list[dict]:
    """Apple's public podcast RSS. Retries 3x on transient 502/503 like
    the music version does."""
    data: dict = {}
    for attempt in range(3):
        try:
            r = requests.get(_APPLE_PODCAST_URL,
                             headers={'User-Agent': _UA},
                             timeout=15)
        except Exception as e:
            logger.info("apple podcasts attempt %d: %s", attempt + 1, e)
            time.sleep(1 + attempt)
            continue
        if r.ok:
            try:
                data = r.json()
                break
            except Exception as e:
                logger.info("apple podcasts attempt %d: json parse: %s",
                            attempt + 1, e)
                time.sleep(1 + attempt)
                continue
        else:
            logger.info("apple podcasts attempt %d: http %s",
                        attempt + 1, r.status_code)
            time.sleep(1 + attempt)
    if not data:
        logger.warning("apple podcasts: gave up after 3 attempts")
        return []
    results = ((data or {}).get('feed') or {}).get('results') or []
    items: list[dict] = []
    for i, t in enumerate(results[:limit], start=1):
        items.append({
            'rank':   i,
            'title':  t.get('name') or '',
            # Apple calls the show creator `artistName` for consistency
            # with music. We normalize to `artist` so the frontend can
            # render Apple Podcasts + Apple Music with the same helper.
            'artist': t.get('artistName') or '',
            'url':    t.get('url') or '',
            'image':  t.get('artworkUrl100') or '',
            # Apple's genre tags travel through as a hint; a few UI
            # variants may want to badge by genre.
            'genres': [g.get('name') for g in (t.get('genres') or []) if g.get('name')],
        })
    return items


# ---------------------------------------------------------------------------
# Spotify Podcast Charts  (stub - see module docstring for why)
# ---------------------------------------------------------------------------
def _fetch_spotify_podcasts(limit: int = 100) -> tuple[list[dict], str]:
    """Return `(items, sub)`. Items is [] until an operator donates a
    fully-authed open.spotify.com session; `sub` explains state so the
    dashboard can render a helpful message.
    """
    # Spotify locked their podcast chart API behind an internal OAuth
    # scope in mid-2026: the byspotify.com marketing site works, but
    # their `/api/charts/top?region=us` returns 500 for every anonymous
    # caller. Even swapping a working session cookie doesn't unlock it
    # from the marketing subdomain; a proper Web API call requires an
    # app-registered client-credentials token. We'll build that path
    # once an operator gets Spotify to approve API access.
    return [], ('Spotify\u0027s public podcast chart endpoint is currently '
                'gated. We\u0027re working on an alternate signal path.')


# ---------------------------------------------------------------------------
# Amazon Music Podcasts  (stub - needs cookies + Playwright)
# ---------------------------------------------------------------------------
def _fetch_amazon_podcasts(limit: int = 100) -> tuple[list[dict], str]:
    """Stub. music.amazon.com/podcasts is a full React SPA that fetches
    its rails client-side after auth. Populates once amazon.com cookies
    donated via `donate_cookies.py amazon.com` unlock a Playwright DOM
    scrape (planned follow-up).
    """
    return [], 'Warming up.'


# ---------------------------------------------------------------------------
# Audible Podcasts  (stub - needs cookies)
# ---------------------------------------------------------------------------
def _fetch_audible_podcasts(limit: int = 100) -> tuple[list[dict], str]:
    """Stub. audible.com/pd renders client-side; anonymous callers get a
    React shell (~250KB HTML, zero product data). Populates once
    audible.com cookies are donated.
    """
    return [], 'Warming up.'


def fetch() -> dict[str, Any]:
    """Pull all four sources. Only Apple currently returns data; the
    others ship as available=False with an operator-facing `sub`.
    """
    apple_items = _fetch_apple_podcasts(limit=100)
    spot_items, spot_sub = _fetch_spotify_podcasts(limit=100)
    amz_items,  amz_sub  = _fetch_amazon_podcasts(limit=100)
    aud_items,  aud_sub  = _fetch_audible_podcasts(limit=100)

    return {
        # Mirror `national` off the biggest working source so the
        # standard summary in `_index.json` reports a real count.
        'national':  apple_items[:50],
        'available': bool(apple_items or spot_items or amz_items or aud_items),
        'sources': {
            'apple': {
                'label':     'Apple Podcasts Top 100 (US)',
                'sub':       'The Apple Podcasts chart. Shows people are listening to right now.',
                'items':     apple_items,
                'available': bool(apple_items),
            },
            'spotify': {
                'label':     'Spotify Podcast Charts (US)',
                'sub':       spot_sub,
                'items':     spot_items,
                'available': bool(spot_items),
            },
            'amazon': {
                'label':     'Amazon Music Podcasts (US)',
                'sub':       amz_sub,
                'items':     amz_items,
                'available': bool(amz_items),
            },
            'audible': {
                'label':     'Audible Podcasts (US)',
                'sub':       aud_sub,
                'items':     aud_items,
                'available': bool(aud_items),
            },
        },
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    from ._base import run_scraper
    result = run_scraper('podcast_charts', 'Podcasts', 'podcast', fetch)
    srcs = result.get('sources') or {}
    for slug, panel in srcs.items():
        print(f"{slug}: n={len(panel.get('items', []))}  ok={panel.get('available')}",
               file=sys.stderr)
        for it in (panel.get('items') or [])[:3]:
            print(f"   #{it['rank']} {it['title']} - {it['artist']}", file=sys.stderr)
