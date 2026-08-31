"""
Podcast charts scraper.

Aggregates trending podcast signals into a single snapshot the dashboard
renders as one tab, mirroring the structure of `music_charts.py`.

Sources (2026-07):

    Apple Podcasts Top 100 US    -> `rss.marketingtools.apple.com`, public JSON
    YouTube Popular Podcasts     -> `www.youtube.com/podcasts`, public server-
                                     rendered HTML with an embedded
                                     `ytInitialData` JSON blob. The "Popular
                                     podcasts" shelf is a lockupViewModel
                                     grid of ~24 tiles (Joe Rogan, Rotten
                                     Mango, MeidasTouch, Shawn Ryan Show,
                                     etc.). Each tile carries title, channel
                                     publisher, cover art, and a playlist
                                     link. Public - no cookies.
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
        "youtube_podcasts": {"label": "YouTube Popular Podcasts (US)", "items": [{...}], "available": bool},
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

# User rule 2026-07-29: NEVER surface operator-facing text (e.g.
# "log into ... and run donate_cookies.py") to the dashboard tile.
# When a bot-walled source can't be scraped, show a neutral "warming
# up" line and let cookie_gap_notify.notify_cookie_gap() handle the
# offline re-donation ask via SES to jenna+jessie (deduped to one
# email per source/domain per day). Same pattern as film_ticketing.py.
_WARMING_UP_HINT = 'Warming up. Check back later.'


def _mark_cookie_gap(source: str, domain: str, reason: str = '') -> None:
    """Fire the operator-facing SES notification. Best-effort; never
    raises. Called from any fetcher that returns 0 items because the
    donated cookie session is missing or has been rejected by the
    site. The dashboard tile only ever sees `_WARMING_UP_HINT`."""
    try:
        from .cookie_gap_notify import notify_cookie_gap
        notify_cookie_gap(source, domain, reason=reason)
    except Exception as e:
        logger.info("cookie_gap notify failed for %s/%s: %s",
                     source, domain, e)


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
# YouTube Popular Podcasts  (public HTML + embedded ytInitialData JSON)
# ---------------------------------------------------------------------------
# YouTube's dedicated podcasts landing page at `www.youtube.com/podcasts`
# ships every popular-podcasts tile inlined as JSON inside the standard
# `var ytInitialData = { ... };` bootstrap blob. Anonymous fetch works
# from any IP (Hetzner datacenter included) - no cookies, no OAuth, no
# Data API v3 key required. The "Popular podcasts" shelf is the tile grid
# the YouTube podcasts homepage renders under that header and matches
# what a signed-out user browsing to that URL sees.
#
# ytInitialData shape (2026-08):
#   contents.twoColumnBrowseResultsRenderer.tabs[0].tabRenderer.content
#     .richGridRenderer.contents[]                           # per-shelf
#       .richSectionRenderer.content.richShelfRenderer
#         .title                                             # shelf title
#         .contents[]                                        # per tile
#           .richItemRenderer.content.lockupViewModel
#             .contentId                                     # playlist id
#             .metadata.lockupMetadataViewModel.title.content         # show title
#             .metadata.lockupMetadataViewModel.metadata.contentMetadataViewModel
#               .metadataRows[0].metadataParts[0].text.content        # publisher
#             .contentImage.collectionThumbnailViewModel
#               .primaryThumbnail.thumbnailViewModel.image.sources[]  # covers
#
# `contentId` is a YouTube playlist ID (starts with `PL...`) representing
# the show; we build the canonical `www.youtube.com/playlist?list=<id>`
# deep-link so tapping a tile lands on the show's episode list.
_YOUTUBE_PODCASTS_URL = 'https://www.youtube.com/podcasts'

# Match the ytInitialData JSON blob. YouTube uses two formats
# depending on the response variant: `var ytInitialData = { ... };`
# and `window["ytInitialData"] = { ... };`.
_YT_INITIAL_DATA_RE = re.compile(
    r'(?:var\s+ytInitialData\s*=\s*|ytInitialData"\]\s*=\s*)'
    r'(\{.*?\});\s*</script>',
    re.DOTALL,
)


def _yt_text(obj: Any) -> str:
    """Pull display text out of a YouTube text object (may be
    `simpleText`, `content`, or `runs`)."""
    if not isinstance(obj, dict):
        return ''
    if obj.get('simpleText'):
        return obj['simpleText']
    if obj.get('content'):
        return obj['content']
    runs = obj.get('runs') or []
    if isinstance(runs, list) and runs:
        return ''.join(r.get('text', '') for r in runs if isinstance(r, dict))
    return ''


def _yt_parse_podcast_tile(tile: dict):
    """Extract title / publisher / image / url from one lockupViewModel
    tile. Returns None if the tile doesn't carry a title."""
    lv = ((tile.get('richItemRenderer') or {}).get('content') or {}).get('lockupViewModel') or {}
    if not lv:
        return None
    mv = (lv.get('metadata') or {}).get('lockupMetadataViewModel') or {}
    title = _yt_text(mv.get('title') or {}).strip()
    if not title:
        return None

    # Publisher: first metadata row, first text part.
    publisher = ''
    rows = ((mv.get('metadata') or {}).get('contentMetadataViewModel') or {}).get('metadataRows') or []
    if rows:
        parts = rows[0].get('metadataParts') or []
        if parts:
            publisher = _yt_text(parts[0].get('text') or {}).strip()

    # Cover art: prefer the largest resolution source.
    image = ''
    sources = ((((lv.get('contentImage') or {})
                    .get('collectionThumbnailViewModel') or {})
                    .get('primaryThumbnail') or {})
                    .get('thumbnailViewModel') or {}).get('image', {}).get('sources') or []
    if sources:
        largest = max(sources, key=lambda s: (s.get('width') or 0) if isinstance(s, dict) else 0)
        if isinstance(largest, dict):
            image = largest.get('url') or ''

    # URL: playlist link when contentId is a playlist, else fall back
    # to the channel canonicalBaseUrl surfaced on the tile's rendererContext.
    url = ''
    content_id = lv.get('contentId') or ''
    if content_id and content_id.startswith('PL'):
        url = f'https://www.youtube.com/playlist?list={content_id}'
    else:
        be = ((((lv.get('rendererContext') or {}).get('commandContext') or {}).get('onTap') or {})
              .get('innertubeCommand') or {}).get('browseEndpoint') or {}
        cu = be.get('canonicalBaseUrl') or ''
        if cu:
            url = f'https://www.youtube.com{cu}'
    return {
        'title':     title,
        'publisher': publisher,
        'image':     image,
        'url':       url,
        'contentId': content_id,
    }


def _fetch_youtube_podcasts(limit: int = 50) -> tuple[list[dict], str]:
    """Parse youtube.com/podcasts, walk the richGridRenderer shelves,
    and return the flattened Popular Podcasts list (with the New shows
    shelf folded on for a bit more depth). Returns `(items, sub)` where
    `sub` is the operator-facing note used when items[] is empty
    (transient failure only)."""
    import json as _json
    try:
        r = requests.get(
            _YOUTUBE_PODCASTS_URL,
            headers={
                'User-Agent':      _UA,
                'Accept':          ('text/html,application/xhtml+xml,'
                                     'application/xml;q=0.9,*/*;q=0.8'),
                'Accept-Language': 'en-US,en;q=0.9',
            },
            timeout=25,
        )
    except Exception as e:
        logger.warning("youtube podcasts: %s", e)
        return [], 'Warming up.'
    if not r.ok:
        logger.warning("youtube podcasts: http %s", r.status_code)
        return [], 'Warming up.'
    html = r.text or ''
    if not html:
        return [], 'Warming up.'

    m = _YT_INITIAL_DATA_RE.search(html)
    if not m:
        logger.warning("youtube podcasts: no ytInitialData in response "
                       "(len=%d) - youtube may have changed the shell",
                       len(html))
        return [], 'Warming up.'
    try:
        data = _json.loads(m.group(1))
    except Exception as e:
        logger.warning("youtube podcasts: ytInitialData parse failed: %s", e)
        return [], 'Warming up.'

    tabs = ((((data.get('contents') or {}).get('twoColumnBrowseResultsRenderer') or {})
                .get('tabs')) or [])
    if not tabs:
        return [], 'Warming up.'
    tab0 = (tabs[0] or {}).get('tabRenderer') or {}
    sections = (((tab0.get('content') or {}).get('richGridRenderer') or {}).get('contents')) or []

    # We want SHOW tiles (lockupViewModel with a playlist contentId +
    # publisher), not per-episode video tiles. The Popular podcasts
    # shelf is the canonical source. Curated genre shelves ("Curious
    # minds", "News & Politics", etc.) that carry the same tile shape
    # are also included to reach the 30-50 range without dipping into
    # episode-only shelves.
    _SHELF_ALLOW = ('popular', 'curious', 'news', 'comedy', 'business',
                    'sports', 'true crime', 'health', 'science',
                    'society', 'talk')

    items: list[dict] = []
    seen: set[str] = set()

    def _add_from_shelf(shelf: dict) -> None:
        for c in shelf.get('contents') or []:
            rec = _yt_parse_podcast_tile(c)
            if not rec:
                continue
            title_key = rec['title'].strip().lower()
            if not title_key or title_key in seen:
                continue
            seen.add(title_key)
            items.append({
                'rank':  len(items) + 1,
                'title': rec['title'],
                # Align on the "artist" field the podcast renderer +
                # exporter already key off (matches Apple / Spotify /
                # Audible / Netflix rows).
                'artist':    rec['publisher'],
                'publisher': rec['publisher'],
                'url':       rec['url'],
                'image':     rec['image'],
            })

    # Pass 1: Popular podcasts shelf (always first if present).
    for s in sections:
        shelf = ((s.get('richSectionRenderer') or {}).get('content') or {}).get('richShelfRenderer') or {}
        title_text = _yt_text(shelf.get('title') or {}).strip().lower()
        if title_text.startswith('popular'):
            _add_from_shelf(shelf)
            break

    # Pass 2: fold in additional podcast-genre shelves until we hit
    # `limit`. Episode-only shelves ("New shows and episodes", "Live
    # now") ship different tile shapes and get skipped naturally by
    # `_yt_parse_podcast_tile` returning None on non-lockup tiles.
    if len(items) < limit:
        for s in sections:
            shelf = ((s.get('richSectionRenderer') or {}).get('content') or {}).get('richShelfRenderer') or {}
            title_text = _yt_text(shelf.get('title') or {}).strip().lower()
            if not title_text or title_text.startswith('popular'):
                continue
            if not any(title_text.startswith(prefix) for prefix in _SHELF_ALLOW):
                continue
            _add_from_shelf(shelf)
            if len(items) >= limit:
                break

    if not items:
        return [], 'Warming up.'
    return items[:limit], ''


# ---------------------------------------------------------------------------
# Amazon Music Podcasts  (Playwright + donated music.amazon.com cookies)
# ---------------------------------------------------------------------------
# music.amazon.com/podcasts is a client-side-rendered React SPA that
# stays as a minimal shell until an authenticated `showBrowseWidgetPage`
# GraphQL call hydrates ~10 podcast carousels. Same session as the
# music-charts scraper (music.amazon.com cookies).
#
# Tiles use `<music-vertical-item>` custom elements with the podcast
# title on `primary-text` and the internal `/podcasts/{uuid}/{slug}`
# route on `primary-href`. We flatten every visible carousel, dedupe
# by URL, and preserve first-seen order (which is roughly
# Popular -> True Crime -> News -> Comedy -> ... - the same editorial
# ordering visible in the Amazon Music UI).
_AMAZON_MUSIC_PODCASTS_URL = 'https://music.amazon.com/podcasts'


def _fetch_amazon_podcasts(limit: int = 100) -> tuple[list[dict], str]:
    """Scrape Amazon Music's `/podcasts` browse page via Playwright with
    the donated music.amazon.com session. Returns `(items, sub)`.
    """
    try:
        from ._playwright import _lazy_playwright, _launch_browser, _try_stealth, UA
        from ._base import load_donated_cookies_playwright
    except Exception as e:
        logger.info("amazon podcasts: playwright helpers unavailable: %s", e)
        return [], 'Warming up.'

    sp = _lazy_playwright()
    if sp is None:
        logger.warning("amazon podcasts: playwright not installed")
        return [], 'Warming up.'

    donated = load_donated_cookies_playwright('music.amazon.com')
    if not donated:
        logger.warning(
            "amazon podcasts: no donated cookies for music.amazon.com. "
            "Firing cookie-gap SES notification."
        )
        _mark_cookie_gap('amazon_podcasts', 'music.amazon.com',
                          reason='no donated cookies on S3')
        return [], _WARMING_UP_HINT

    items: list[dict] = []
    try:
        with sp() as pw:
            try:
                browser, _c = _launch_browser(pw, prefer_chrome=True)
            except Exception as e:
                logger.warning("amazon podcasts: playwright launch: %s", e)
                return [], 'Warming up.'

            ctx = browser.new_context(
                user_agent=UA,
                viewport={'width': 1440, 'height': 900},
                locale='en-US',
                timezone_id='America/New_York',
                extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
            )
            ctx.add_cookies(donated)
            page = ctx.new_page()
            _try_stealth(page)

            # Warm homepage so the auth-context bootstrap fires (needed
            # for the /podcasts page to receive the client token used
            # by its subsequent showBrowseWidgetPage GraphQL fetch).
            try:
                page.goto('https://music.amazon.com/',
                          wait_until='domcontentloaded', timeout=45000)
                page.wait_for_timeout(3500)
            except Exception as e:
                logger.info("amazon podcasts: homepage warmup: %s", e)

            page.goto(_AMAZON_MUSIC_PODCASTS_URL,
                      wait_until='domcontentloaded', timeout=45000)
            page.wait_for_timeout(6000)

            # Wait for the first batch of tiles to hydrate. If they
            # never do, cookies are dead - drop out with the operator
            # instruction.
            try:
                page.wait_for_function(
                    "() => document.querySelectorAll("
                    "'music-vertical-item[primary-text]').length >= 10",
                    timeout=25000,
                )
            except Exception:
                logger.warning("amazon podcasts: tiles never hydrated - "
                               "cookies likely expired, firing SES notify")
                try:
                    ctx.close(); browser.close()
                except Exception:
                    pass
                _mark_cookie_gap('amazon_podcasts', 'music.amazon.com',
                                  reason='tiles never hydrated - cookies likely expired')
                return [], _WARMING_UP_HINT

            # Scroll to force lazy carousels below the fold to load.
            for _ in range(8):
                page.mouse.wheel(0, 2500)
                page.wait_for_timeout(500)
            page.wait_for_timeout(1500)

            tiles = page.evaluate("""() => {
                const out = [];
                document.querySelectorAll('music-vertical-item').forEach((el) => {
                    const p    = el.getAttribute('primary-text')     || el.primaryText     || '';
                    const s1   = el.getAttribute('secondary-text-1') || el.secondaryText1  || '';
                    const s2   = el.getAttribute('secondary-text-2') || el.secondaryText2  || '';
                    const img  = el.getAttribute('image-src')        || '';
                    const href = el.getAttribute('primary-href')     || '';
                    if (p && href && href.startsWith('/podcasts/')) {
                        out.push({title: p, sub1: s1, sub2: s2, image: img, href});
                    }
                });
                return out;
            }""")

            try:
                ctx.close(); browser.close()
            except Exception:
                pass

            seen_urls: set[str] = set()
            for t in tiles:
                href = t.get('href') or ''
                if not href:
                    continue
                if href in seen_urls:
                    continue
                seen_urls.add(href)
                items.append({
                    'rank':   len(items) + 1,
                    'title':  (t.get('title') or '').strip(),
                    # No publisher exposed on the tile - the frontend
                    # already handles missing artist/publisher.
                    'artist': (t.get('sub1') or '').strip(),
                    'url':    'https://music.amazon.com' + href,
                    'image':  t.get('image') or '',
                })
                if len(items) >= limit:
                    break
    except Exception as e:
        logger.warning("amazon podcasts: playwright pass failed: %s", e)
        return [], 'Warming up.'

    if not items:
        return [], 'Warming up.'
    return items, ''


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
# `?ipRedirectOverride=true` tells the Audible CDN to skip its IP-based
# geo-redirect. Without it, requests from a Hetzner datacenter IP get
# 302-forwarded to audible.de with the German storefront - the donated
# US-Chrome cookies alone are NOT enough to bypass the redirect, only
# the query flag is (verified 2026-07-27).
_AUDIBLE_PODCASTS_URL = ('https://www.audible.com/ep/podcasts'
                         '?ipRedirectOverride=true')

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

    Requires donated `audible.com` cookies. Without them Audible geo-
    redirects our Hetzner datacenter IP to the German (de-DE) storefront
    which returns a completely different HTML shape and lands 0 items.
    The donated US-Chrome session includes an `ubid-main` + `session-id`
    that locks the response to www.audible.com/en_US.
    """
    try:
        from ._base import load_donated_cookies
        cookies = load_donated_cookies('audible.com') or {}
    except Exception:
        cookies = {}
    if not cookies:
        logger.warning(
            "audible podcasts: no donated cookies for audible.com. "
            "Firing cookie-gap SES notification."
        )
        _mark_cookie_gap('audible_podcasts', 'audible.com',
                          reason='no donated cookies on S3')
        return [], _WARMING_UP_HINT

    try:
        r = requests.get(
            _AUDIBLE_PODCASTS_URL,
            headers={
                'User-Agent':      _UA,
                'Accept':          ('text/html,application/xhtml+xml,'
                                     'application/xml;q=0.9,*/*;q=0.8'),
                'Accept-Language': 'en-US,en;q=0.9',
                # Hint the CDN to keep us on the US storefront even if
                # something in the cookie jar drifts.
                'Referer':         'https://www.audible.com/',
            },
            cookies=cookies,
            timeout=25,
        )
    except Exception as e:
        logger.warning("audible podcasts: %s", e)
        return [], 'Warming up.'
    if not r.ok:
        logger.warning("audible podcasts: http %s", r.status_code)
        return [], 'Warming up.'
    html = r.text or ''
    # German shell also >50k bytes but has `lang="de-DE"` in <html>.
    # Explicitly check we got the US storefront before parsing.
    if 'lang="de-DE"' in html[:2000] or 'lang="de"' in html[:2000]:
        logger.warning("audible podcasts: got de-DE storefront despite "
                       "cookies - cookies may be stale, firing SES notify")
        _mark_cookie_gap('audible_podcasts', 'audible.com',
                          reason='de-DE storefront returned despite cookies - session likely expired')
        return [], _WARMING_UP_HINT
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
    """Pull all five sources. All are live:
      - Apple, Spotify, Netflix ship server-rendered / public API data.
      - Audible needs donated audible.com cookies to lock the US
        storefront (datacenter IPs get geo-redirected to de-DE).
      - Amazon Music needs donated music.amazon.com cookies driven
        through Playwright (same session as the music-charts scraper).
    """
    apple_items = _fetch_apple_podcasts(limit=100)
    spot_items, spot_sub = _fetch_spotify_podcasts(limit=100)
    yt_items,   yt_sub   = _fetch_youtube_podcasts(limit=50)
    amz_items,  amz_sub  = _fetch_amazon_podcasts(limit=100)
    aud_items,  aud_sub  = _fetch_audible_podcasts(limit=100)
    nfx_items,  nfx_sub  = _fetch_netflix_podcasts(limit=60)

    return {
        # Mirror `national` off the biggest working source so the
        # standard summary in `_index.json` reports a real count.
        'national':  apple_items[:50],
        'available': bool(apple_items or spot_items or yt_items or amz_items or aud_items or nfx_items),
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
            'youtube_podcasts': {
                'label':     'YouTube Popular Podcasts (US)',
                'sub':       (yt_sub or "YouTube's Popular Podcasts shelf. What US viewers are watching on youtube.com/podcasts right now."),
                'items':     yt_items,
                'available': bool(yt_items),
            },
            'netflix': {
                'label':     'Netflix video podcasts',
                'sub':       (nfx_sub or 'Every podcast Netflix carries. Editorial list from Netflix Tudum.'),
                'items':     nfx_items,
                'available': bool(nfx_items),
            },
            'amazon': {
                'label':     'Amazon Music Podcasts (US)',
                'sub':       (amz_sub or "Top-listened podcasts across Amazon "
                                          "Music's editorial carousels."),
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
