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
    Netflix video podcasts       -> `netflix.com/tudum/podcasts`, public
                                     server-rendered HTML page maintained by
                                     Netflix Tudum editorial. Every podcast
                                     Netflix carries (Bill Simmons, Pardon My
                                     Take, My Favorite Murder, Bridgerton
                                     Official, Skip Intro, etc) is listed here
                                     with title / description / cast / cover.
                                     Rank is authorial order (the Tudum
                                     editors' curated list), not chart data.

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
        "audible": {"label": "Audible Podcasts (US)",       "items": [],      "available": False, "sub": "..."},
        "netflix": {"label": "Netflix video podcasts",      "items": [{...}], "available": bool}
      }
    }

Every `items[i]` has at least:

    { rank, title, artist, url, image? }

Standalone:

    python3 -m scripts.trends_scrapers.podcast_charts
"""

from __future__ import annotations

import html as _html
import logging
import re
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


# ---------------------------------------------------------------------------
# Netflix video podcasts  (public Tudum HTML)
# ---------------------------------------------------------------------------
# Netflix rolled out a video-podcast tier in early 2026 that lets
# subscribers stream shows like The Bill Simmons Podcast, Pardon My Take,
# and My Favorite Murder alongside their series/films. Their editorial
# team keeps the running list at netflix.com/tudum/podcasts - it's the
# only surface Netflix officially publishes for the podcast lineup
# (there is no dedicated /browse/podcasts genre yet, and the Netflix
# Web API doesn't expose the podcast catalog to non-subscribers).
#
# The Tudum page is server-rendered (~900KB HTML with every podcast
# inlined as a media-card block). We regex the card list; class names
# are Emotion-hashed (rebuilt each deploy) so we key off the stable
# `data-sel="heading"` + `data-sel="media-card"` markers.
_NETFLIX_PODCAST_URL = 'https://www.netflix.com/tudum/podcasts'

# Section-marker headings that appear between card groups (e.g.
# "JUNE 19", "JULY 13", "Coming Soon"). We drop any heading that
# matches these patterns before extracting cards.
_NETFLIX_SECTION_HEADING_RE = re.compile(
    r'^(?:'
    r'[A-Z]{3,10}\s+\d{1,2}(?:,\s*\d{4})?'   # dates like "JUNE 19"
    r'|Coming\s+Soon|Now\s+Playing|What[’\'](?:s)?\s+Coming'
    r'|Popular(?:\s+Now)?'
    r')$',
    re.IGNORECASE,
)


def _fetch_netflix_podcasts(limit: int = 60) -> tuple[list[dict], str]:
    """Parse `netflix.com/tudum/podcasts` into a normalized card list.

    Rank is the editorial order Tudum publishes (not a chart), so the
    first card is what Netflix's own editorial team is currently
    leading with.

    Returns (items, sub) where `sub` is the operator-facing note used
    for the dashboard sub-label when items[] is empty.
    """
    try:
        r = requests.get(
            _NETFLIX_PODCAST_URL,
            headers={
                'User-Agent':      _UA,
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept':          ('text/html,application/xhtml+xml,'
                                     'application/xml;q=0.9,*/*;q=0.8'),
            },
            timeout=20,
        )
    except Exception as e:
        logger.warning("netflix podcasts: %s", e)
        return [], 'Warming up.'
    if not r.ok:
        logger.warning("netflix podcasts: http %s", r.status_code)
        return [], 'Warming up.'
    html = r.text or ''
    if not html:
        return [], 'Warming up.'

    # Each podcast card in the Tudum DOM:
    #   <h3 data-sel="heading" ...>TITLE</h3>
    #   <p>DESCRIPTION</p>
    #   <div data-sel="text" data-variant="emphasis-close" ...>Cast</div>
    #   <p>CAST NAMES</p>
    #   ...
    #   <img data-uia="image" src="https://dnm.nflximg.net/..." ...>
    #
    # Section markers between groups (e.g. "JUNE 19", "Coming Soon")
    # ALSO use <h3 data-sel="heading" ...>, but they never have a <p>
    # directly following, so a title+desc pair filter is enough.
    # Slice the HTML into chunks starting at each h3 boundary so we
    # can scope image extraction to the current card only.
    card_boundary = re.compile(r'<h3 data-sel="heading"[^>]*>([^<]{2,150})</h3>')
    boundaries = list(card_boundary.finditer(html))
    items: list[dict] = []
    for i, m in enumerate(boundaries):
        title = _html.unescape(m.group(1).strip())
        if not title:
            continue
        if _NETFLIX_SECTION_HEADING_RE.match(title):
            continue
        # The card body extends until the next heading (or a heuristic
        # cap of 4kb to keep image regex bounded on the final card).
        next_start = boundaries[i + 1].start() if i + 1 < len(boundaries) else min(m.end() + 4000, len(html))
        card = html[m.end():next_start]

        # First <p> after the h3 is the description
        desc_m = re.search(r'<p>([^<]{0,600})</p>', card)
        description = _html.unescape(desc_m.group(1).strip()) if desc_m else ''
        # Skip cards with no description - those are the "Coming Soon"
        # placeholders that don't have full metadata yet.
        if not description:
            continue

        # Cast line is the <p> immediately after the "Cast" label div.
        cast_m = re.search(
            r'data-variant="emphasis-close"[^>]*>Cast</div>\s*<p>([^<]{0,300})</p>',
            card,
        )
        cast = _html.unescape(cast_m.group(1).strip()) if cast_m else ''

        # First <img data-uia="image" src="..."> in the card.
        img_m = re.search(
            r'<img[^>]*data-uia="image"[^>]*src="(https://[^"]+)"',
            card,
        )
        image = img_m.group(1) if img_m else ''

        items.append({
            'rank':        len(items) + 1,
            'title':       title,
            # Cast is the closest analog to "artist" for a podcast row
            # (same field the Music renderer treats as the byline).
            'artist':      cast,
            'description': description,
            # Netflix hasn't linked individual titles from Tudum yet,
            # so route the URL to the Tudum article; users can then
            # tap into the app to play.
            'url':         _NETFLIX_PODCAST_URL,
            'image':       image,
        })
        if len(items) >= limit:
            break

    if not items:
        return [], 'Warming up.'
    return items, ''


def fetch() -> dict[str, Any]:
    """Pull all five sources. Apple + Netflix currently return data
    from public endpoints; Spotify / Amazon / Audible ship as
    available=False with an operator-facing `sub` until cookies land.
    """
    apple_items = _fetch_apple_podcasts(limit=100)
    spot_items, spot_sub = _fetch_spotify_podcasts(limit=100)
    amz_items,  amz_sub  = _fetch_amazon_podcasts(limit=100)
    aud_items,  aud_sub  = _fetch_audible_podcasts(limit=100)
    nfx_items,  nfx_sub  = _fetch_netflix_podcasts(limit=60)

    return {
        # Mirror `national` off the biggest working source so the
        # standard summary in `_index.json` reports a real count.
        'national':  apple_items[:50],
        'available': bool(apple_items or spot_items or amz_items or aud_items or nfx_items),
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
            'netflix': {
                'label':     'Netflix video podcasts',
                'sub':       (nfx_sub or 'Every podcast Netflix carries. Editorial list from Netflix Tudum.'),
                'items':     nfx_items,
                'available': bool(nfx_items),
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
