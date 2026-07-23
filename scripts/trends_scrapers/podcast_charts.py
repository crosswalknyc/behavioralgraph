"""
Podcast charts scraper.

Aggregates trending podcast signals into a single snapshot the dashboard
renders as one tab, mirroring the structure of `music_charts.py`.

Sources (2026-07):

    Apple Podcasts Top 100 US    -> `rss.marketingtools.apple.com`, public JSON
    Spotify Podcast Charts       -> `podcastcharts.byspotify.com/api/charts/
                                     top-podcasts?region=us&limit=100`. Public
                                     JSON, no OAuth. Returns the same Top 200
                                     shows the byspotify.com UI renders, with
                                     rank movement flags (UP / DOWN /
                                     UNCHANGED). Spotify restructured the site
                                     in 2026 (old `/api/charts/top` gone,
                                     replaced by category-scoped endpoints);
                                     this is the current path.
    Amazon Music Podcasts        -> stub. music.amazon.com/podcasts is a
                                     React SPA (~11KB shell, zero inlined
                                     data). No free-tier public analog to
                                     Spotify's byspotify.com marketing
                                     endpoint exists: podcasts.amazon.com is
                                     dead, podcasters.amazon.com is the
                                     publisher portal, and music.amazon.com's
                                     internal GraphQL requires an auth token.
                                     Populates once music.amazon.com cookies
                                     are donated via donate_cookies.py and a
                                     Playwright DOM scrape ships.
    Audible Podcasts             -> `www.audible.com/ep/podcasts`, public
                                     server-rendered HTML. Audible curates a
                                     landing page with 8 carousels (Popular
                                     Podcasts, Audible Originals, True Crime,
                                     News, History, Comedy, Health & Fitness,
                                     Sports). Each item is an <adbl-product-
                                     grid-item> with ASIN, title, publisher,
                                     cover, and deep-link. We flatten and
                                     dedupe by ASIN into a single ranked
                                     list, section-major then position within
                                     section.
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
        "spotify": {"label": "Spotify Podcast Charts (US)", "items": [{...}], "available": bool},
        "amazon":  {"label": "Amazon Music Podcasts (US)",  "items": [],      "available": False, "sub": "..."},
        "audible": {"label": "Audible Podcasts (US)",       "items": [{...}], "available": bool},
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
# Spotify Podcast Charts (public JSON on podcastcharts.byspotify.com)
# ---------------------------------------------------------------------------
# The byspotify.com marketing site fetches its Top 200 client-side from
# `/api/charts/{categoryId}?region={cc}&limit=100`. That endpoint is
# fully anonymous - no OAuth, no cookies, no fingerprint games needed.
# Spotify restructured the site in 2026: the old `/api/charts/top`
# always 500s now, replaced by category-scoped endpoints. We hit
# `top-podcasts` which is the flagship Top 200 shows list.
#
# Response is a JSON array of:
#   { showUri, chartRankMove, showName, showPublisher,
#     showImageUrl, showDescription }
# Rank is array position (1-indexed). chartRankMove ∈
# {UP, DOWN, UNCHANGED}. showUri is `spotify:show:<id>`, which maps
# 1:1 to `open.spotify.com/show/<id>` for click-through.
_SPOTIFY_PODCAST_URL = 'https://podcastcharts.byspotify.com/api/charts/top-podcasts'


def _fetch_spotify_podcasts(limit: int = 100) -> tuple[list[dict], str]:
    """Fetch the US Top Podcasts chart from Spotify's public byspotify.com
    endpoint. Returns `(items, sub)` where `sub` is the operator-facing
    note used when items[] is empty (transient failure only).
    """
    params = {'region': 'us', 'limit': str(max(limit, 100))}
    headers = {
        'User-Agent':      _UA,
        'Accept':          'application/json',
        # Referer/Origin match the marketing site so requests look like
        # the same fetch the byspotify.com UI issues. Not strictly
        # required today (endpoint is open) but cheap insurance if
        # Spotify tightens the CORS/referrer check later.
        'Referer':         'https://podcastcharts.byspotify.com/us/top-podcasts',
        'Origin':          'https://podcastcharts.byspotify.com',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    resp = None
    for attempt in range(3):
        try:
            resp = requests.get(_SPOTIFY_PODCAST_URL,
                                params=params, headers=headers, timeout=20)
        except Exception as e:
            logger.info("spotify podcasts attempt %d: %s", attempt + 1, e)
            time.sleep(1 + attempt)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            logger.info("spotify podcasts attempt %d: http %s",
                        attempt + 1, resp.status_code)
            time.sleep(2 + attempt)
            continue
        break
    if not resp or not resp.ok:
        logger.warning("spotify podcasts: gave up (last=%s)",
                       getattr(resp, 'status_code', None))
        return [], 'Warming up.'
    try:
        data = resp.json()
    except Exception as e:
        logger.warning("spotify podcasts: json parse: %s", e)
        return [], 'Warming up.'
    if not isinstance(data, list) or not data:
        return [], 'Warming up.'

    items: list[dict] = []
    for i, row in enumerate(data[:limit], start=1):
        if not isinstance(row, dict):
            continue
        title = (row.get('showName') or '').strip()
        if not title:
            continue
        uri = row.get('showUri') or ''
        # spotify:show:XXX -> open.spotify.com/show/XXX
        show_id = uri.rsplit(':', 1)[-1] if uri.startswith('spotify:show:') else ''
        url = f'https://open.spotify.com/show/{show_id}' if show_id else ''
        items.append({
            'rank':        i,
            'title':       title,
            'artist':      (row.get('showPublisher') or '').strip(),
            'description': (row.get('showDescription') or '').strip(),
            'url':         url,
            'image':       row.get('showImageUrl') or '',
            # Rank movement flag ('UP' | 'DOWN' | 'UNCHANGED'). Free
            # signal from Spotify - not currently rendered but useful
            # for future "Movers" surfacing without a re-scrape.
            'move':        row.get('chartRankMove') or 'UNCHANGED',
        })
    return items, ''


# ---------------------------------------------------------------------------
# Amazon Music Podcasts  (stub - needs cookies + Playwright)
# ---------------------------------------------------------------------------
def _fetch_amazon_podcasts(limit: int = 100) -> tuple[list[dict], str]:
    """Stub. music.amazon.com/podcasts is a full React SPA that fetches
    its rails client-side after auth. Unlike Spotify (which has a
    public byspotify.com marketing endpoint) and Audible (whose /ep/
    podcasts page is server-rendered), Amazon Music has NO free-tier
    public analog - podcasts.amazon.com is dead, podcasters.amazon.com
    is the publisher portal, and music.amazon.com's internal GraphQL
    requires an auth token. Populates once music.amazon.com cookies
    are donated via `donate_cookies.py music.amazon.com` and a
    Playwright DOM scrape ships.
    """
    return [], 'Warming up.'


# ---------------------------------------------------------------------------
# Audible Podcasts  (public server-rendered HTML on audible.com/ep/podcasts)
# ---------------------------------------------------------------------------
# Audible curates a podcast landing page with 8 carousels. The page is
# fully server-rendered (~780KB) - every item ships as an <adbl-product-
# grid-item> block with ASIN, title, publisher, cover art and deep-link.
# We flatten across canonical sections, dedupe by ASIN and preserve
# first-seen order (section-major then in-carousel position). Each row
# carries `category` for context; the frontend renders it in the row
# meta line under the publisher.
_AUDIBLE_PODCASTS_URL = 'https://www.audible.com/ep/podcasts'

# Section order = the order we want the flattened list to reflect.
# "Popular Podcasts" is Audible's own editorial top list -> row 1.
# "Audible Originals" is Audible-produced exclusives -> row 19-ish.
# Genre carousels fill out the tail.
_AUDIBLE_SECTIONS = (
    'Popular Podcasts',
    'Audible Originals',
    'True Crime',
    'News',
    'History',
    'Comedy',
    'Health & Fitness',
    'Sports',
)

# Matches one carousel item. Class names on <adbl-product-grid-item>
# are stable (they're custom elements, not utility-CSS hashes); if
# Audible ever renames data-widget/data-asin the regex will just fail
# closed and _fetch_audible_podcasts returns [] with "Warming up.".
_AUDIBLE_ITEM_RE = re.compile(
    r'<div[^>]+adbl-asin-impression[^>]*'
    r'data-asin="(?P<asin>[A-Z0-9]{10})"[^>]*'
    r'data-widget="product-carousel"[^>]*'
    r'data-position="(?P<pos>\d+)"'
    r'.*?'
    r'<img[^>]+src="(?P<img>https://m\.media-amazon\.com/images/[^"]+)"[^>]*'
    r'\s+alt="(?P<alt>[^"]{5,300})"'
    r'.*?'
    r'<adbl-metadata slot="title"[^>]*>\s*<a[^>]+>(?P<title>[^<]+)</a>'
    r'.*?'
    r'<adbl-metadata slot="author"[^>]*>.*?searchAuthor=(?P<pub>[^&"]+)',
    re.DOTALL,
)

# `<h3 slot="title">Popular Podcasts</h3>` (main carousels) OR
# `<h3>Category Name</h3>` (More Categories block).
_AUDIBLE_SECTION_RE = re.compile(
    r'<h3(?:\s+slot="title")?[^>]*>([^<]{3,120})</h3>'
)


def _fetch_audible_podcasts(limit: int = 100) -> tuple[list[dict], str]:
    """Parse audible.com/ep/podcasts into a normalized carousel-flattened
    list. Returns (items, sub) where `sub` is the operator-facing note
    used when items[] is empty (transient failure only).
    """
    try:
        r = requests.get(
            _AUDIBLE_PODCASTS_URL,
            headers={
                'User-Agent':      _UA,
                'Accept':          ('text/html,application/xhtml+xml,'
                                     'application/xml;q=0.9,*/*;q=0.8'),
                'Accept-Language': 'en-US,en;q=0.9',
            },
            timeout=25,
        )
    except Exception as e:
        logger.warning("audible podcasts: %s", e)
        return [], 'Warming up.'
    if not r.ok:
        logger.warning("audible podcasts: http %s", r.status_code)
        return [], 'Warming up.'
    html = r.text or ''
    if len(html) < 50_000:
        logger.warning("audible podcasts: html too small (%d bytes) - "
                       "likely served an anti-bot shell", len(html))
        return [], 'Warming up.'

    # Walk sections in DOM order so we can tag each item with the
    # carousel it came from. Only carousels in _AUDIBLE_SECTIONS count.
    from urllib.parse import unquote
    section_marks = [(m.start(), m.group(1).strip())
                     for m in _AUDIBLE_SECTION_RE.finditer(html)]

    def section_for(offset: int) -> str:
        current = ''
        for pos, label in section_marks:
            if pos > offset:
                break
            current = label
        return current

    # First pass: collect items into per-section buckets so we can
    # emit in canonical section order regardless of DOM ordering.
    buckets: dict[str, list[dict]] = {s: [] for s in _AUDIBLE_SECTIONS}
    seen_asins: set[str] = set()

    for m in _AUDIBLE_ITEM_RE.finditer(html):
        asin = m.group('asin')
        if asin in seen_asins:
            continue
        section = section_for(m.start())
        if section not in buckets:
            continue
        title = _html.unescape(m.group('title').strip())
        if not title:
            continue
        publisher = _html.unescape(
            unquote(m.group('pub').replace('+', ' ')).strip()
        )
        # Prefer the higher-res 500px cover if we can construct it from
        # the 240px src (Audible serves the same asset at multiple sizes
        # via `_SL240_`, `_SL500_`, etc.).
        img = m.group('img')
        if '_SL240_' in img:
            img = img.replace('_SL240_', '_SL500_')
        buckets[section].append({
            'asin':      asin,
            'title':     title,
            'publisher': publisher,
            'image':     img,
            'category':  section,
        })
        seen_asins.add(asin)

    # Flatten in canonical order, assign ranks.
    items: list[dict] = []
    for section in _AUDIBLE_SECTIONS:
        for row in buckets[section]:
            items.append({
                'rank':        len(items) + 1,
                'title':       row['title'],
                # Publisher slots in the same "artist" field music /
                # Spotify / Apple podcasts use so the row renderer needs
                # no special case.
                'artist':      row['publisher'],
                # Section becomes description so the card row has extra
                # context (e.g. "Popular Podcasts", "True Crime").
                'description': row['category'],
                'url':         f'https://www.audible.com/pd/{row["asin"]}',
                'image':       row['image'],
                # Also expose the section as `category` for any future
                # renderer that wants to color-code by carousel.
                'category':    row['category'],
            })
            if len(items) >= limit:
                return items, ''

    if not items:
        return [], 'Warming up.'
    return items, ''


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
    """Pull all five sources. Apple, Spotify, Netflix and Audible return
    live data from public endpoints; Amazon Music remains stubbed
    until music.amazon.com cookies + a Playwright DOM scrape ship.
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
                'sub':       (spot_sub or 'The Spotify US Top 200 podcasts. Refreshes daily.'),
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
                'sub':       (aud_sub or 'What Audible is featuring on their podcast landing page across Popular, Originals, and top genres.'),
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
