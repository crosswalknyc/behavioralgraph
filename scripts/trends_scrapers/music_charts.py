"""
Music charts scraper - Spotify Top 200, Apple Music Top 100, Shazam
Top 200, YouTube Music (US weekly), and Amazon Music All Hits.

Aggregates the biggest free (and cookie-donated) music trending signals
into a single snapshot the dashboard renders as one tab:

    Spotify Daily Top 200 US -> mainstream streaming (via kworb.net)
    Apple Music Top 100 US   -> what Apple Music subscribers play
    Shazam Top 200 US        -> what people are IDing right now (discovery)
    YouTube Music US Weekly  -> what's most-played on YouTube in the US
                                (via kworb.net, YouTube's own stream data)
    Amazon Music All Hits    -> Amazon's editorial flagship hits
                                playlist (needs music.amazon.com cookies)

Snapshot shape (kind='music'):

    {
      "source":     "music_charts",
      "kind":       "music",
      "label":      "Music",
      "fetched_at": "...",
      "sources": {
        "spotify":  {"label": "Spotify Daily Top 200 (US)", "items": [{...}]},
        "apple":    {"label": "Apple Music Top 100 (US)",   "items": [{...}]},
        "shazam":   {"label": "Shazam Top 200 (US)",        "items": [{...}]},
        "youtube":  {"label": "YouTube Music (US)",         "items": [{...}]},
        "amazon":   {"label": "Amazon Music: All Hits (US)", "items": [{...}], "available": bool}
      }
    }

Every `items[i]` has at least:

    { rank, title, artist, url, image? }

Standalone:

    python3 -m scripts.trends_scrapers.music_charts
"""

from __future__ import annotations

import csv
import html as _html
import io
import json
import logging
import re
import sys
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


# User rule 2026-07-29: NEVER surface operator-facing text to the
# dashboard. When a bot-walled or cookie-gated source can't be
# scraped, show a neutral "warming up" line and let
# `cookie_gap_notify.notify_cookie_gap()` handle the offline
# re-donation ask via SES to jenna+jessie (deduped to one email per
# source/domain per day).
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


_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) '
        'Gecko/20100101 Firefox/120.0')

# ---------------------------------------------------------------------------
# Spotify Daily Top 200 US  (via kworb.net)
# ---------------------------------------------------------------------------
# Spotify locked their own public chart CSVs behind a login in 2022
# (charts.spotify.com). kworb.net has continuously mirrored the daily
# US chart from Spotify's own API into a simple HTML table, and their
# scrape is community-standard for this data (used by Billboard's own
# tracking team, MRC, etc.). The URL updates daily around 08:00 UTC.
_KWORB_URL = 'https://kworb.net/spotify/country/us_daily.html'

# Row shape in the HTML:
#   <tr>
#     <td class="np">1</td>                                    (rank)
#     <td class="np">=</td>                                    (rank change, unused)
#     <td class="text mp"><div>
#       <a href="../artist/{id}.html">Artist Name</a>
#       -
#       <a href="../track/{id}.html">Track Title</a>
#     </div></td>
#     ...
_KWORB_ROW_RE = re.compile(
    r'<tr>\s*<td class="np">(\d+)</td>\s*'
    r'<td class="np">[^<]*</td>\s*'
    r'<td class="text mp"><div>\s*'
    r'<a href="\.\./artist/[^"]+\.html">([^<]+)</a>\s*'
    r'-\s*'
    r'<a href="\.\./track/([^"]+)\.html">([^<]+)</a>',
    re.DOTALL,
)


def _fetch_spotify(limit: int = 100) -> list[dict]:
    """Parse kworb.net's US daily table into a list of items shaped
    exactly like the other music sub-sources (rank/title/artist/url).
    The URL points at open.spotify.com/track/{id} so clicks go direct
    to Spotify.

    Silent failure returns []: the snapshot still writes with the
    other three sources so the card just goes blank for one day
    instead of taking the whole tab down."""
    try:
        r = requests.get(_KWORB_URL, headers={'User-Agent': _UA,
                                              'Accept': 'text/html'},
                          timeout=20)
    except Exception as e:
        logger.warning("spotify (kworb): %s", e)
        return []
    if not r.ok:
        logger.warning("spotify (kworb): http %s", r.status_code)
        return []
    items: list[dict] = []
    for m in _KWORB_ROW_RE.finditer(r.text or ''):
        try:
            rank = int(m.group(1))
        except ValueError:
            continue
        artist   = _html.unescape((m.group(2) or '').strip())
        track_id = (m.group(3) or '').strip()
        title    = _html.unescape((m.group(4) or '').strip())
        if not (title and artist and track_id):
            continue
        items.append({
            'rank':   rank,
            'title':  title,
            'artist': artist,
            # Spotify's track IDs on kworb match the open.spotify.com URI,
            # so we can link straight to the track without an extra API call.
            'url':    f'https://open.spotify.com/track/{track_id}',
        })
        if len(items) >= limit:
            break
    return items


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
# YouTube Music US Weekly  (via kworb.net)
# ---------------------------------------------------------------------------
# YouTube's own charts pages (charts.youtube.com/charts/TopSongs/us/weekly)
# are a heavy Angular SPA that requires Playwright to hydrate. kworb.net's
# `/youtube/insights/us.html` is community-standard for this data: it
# aggregates weekly view counts directly from YouTube's own stream data
# and publishes them as a clean HTML table refreshed weekly. Every major
# music-industry tracker uses this feed for YouTube ranking.
#
# Row format on the page:
#   <tr ><td class="np">1</td>
#     <td class="np">=</td>                            (rank change)
#     <td class="text mp"><div>Artist - Track</div></td>
#     <td>Wks</td><td>Peak</td><td>(xN)</td>
#     <td>8,580,866</td>                                (streams this week)
#     <td>+2,730,838</td>                               (delta)
#   </tr>
_YTM_URL = 'https://kworb.net/youtube/insights/us.html'

# Same anchor pattern as _KWORB_ROW_RE but without the artist/track
# anchor tags: kworb's YouTube page collapses artist + title into a
# single `<div>Artist - Track</div>` text node.
_YTM_ROW_RE = re.compile(
    r'<tr[^>]*>\s*<td class="np">(\d+)</td>\s*'
    r'<td class="np">[^<]*</td>\s*'
    r'<td class="text mp"><div>([^<]+)</div></td>',
    re.DOTALL,
)


def _fetch_youtube_music(limit: int = 100) -> list[dict]:
    """Parse kworb's US YouTube weekly chart table. Rows read as
    'Artist - Track'; we split on the first ' - ' to recover both.
    Deep-link goes to a YouTube Music search since kworb doesn't
    expose the video ID.

    Silent failure returns [] - the snapshot still writes with the
    other sources so the card just goes blank for one day."""
    try:
        r = requests.get(_YTM_URL, headers={'User-Agent': _UA,
                                             'Accept': 'text/html'},
                          timeout=20)
    except Exception as e:
        logger.warning("youtube music (kworb): %s", e)
        return []
    if not r.ok:
        logger.warning("youtube music (kworb): http %s", r.status_code)
        return []
    items: list[dict] = []
    for m in _YTM_ROW_RE.finditer(r.text or ''):
        try:
            rank = int(m.group(1))
        except ValueError:
            continue
        combined = _html.unescape((m.group(2) or '').strip())
        if not combined:
            continue
        # Split on the FIRST " - " so titles containing hyphens
        # (e.g. "Love The Way You Lie (feat. Rihanna)" isn't affected
        # but "TOTO - Africa" splits cleanly). "Artist - Track" is
        # kworb's stable format.
        if ' - ' in combined:
            artist, title = combined.split(' - ', 1)
            artist = artist.strip()
            title  = title.strip()
        else:
            # Track only (rare - usually a compilation entry).
            artist = ''
            title  = combined
        if not title:
            continue
        q = requests.utils.quote(f'{title} {artist}'.strip())
        items.append({
            'rank':   rank,
            'title':  title,
            'artist': artist,
            'url':    f'https://music.youtube.com/search?q={q}',
        })
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------------------------
# Apple Music Top 100 US  (public RSS/JSON, no auth)
# ---------------------------------------------------------------------------
# 2026-07-22: bumped from Top 50 to Top 100. Jenna asked for 200 but
# Apple's public marketing RSS caps out at 100 - anything higher returns
# HTTP 500. The legacy iTunes RSS Generator (`itunes.apple.com/us/rss/
# topsongs/limit=200/json`) accepts limit=200 but only returns ~80
# entries AND measures iTunes Store PURCHASES, not Apple Music streams.
# So 100 is the ceiling for a real Apple Music stream signal from a
# public unauthenticated feed.
_APPLE_URL = ('https://rss.applemarketingtools.com/api/v2/us/music/'
               'most-played/100/songs.json')


def _fetch_apple(limit: int = 100) -> list[dict]:
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
# iTunes Search API artwork enrichment
# ---------------------------------------------------------------------------
# Apple Music's RSS ships artwork out of the box, but the Spotify (kworb HTML)
# and Shazam (CSV) feeds don't. iTunes Search API (itunes.apple.com/search)
# is free, unauthenticated, and returns the same `artworkUrl100` field Apple's
# own RSS uses. We hit it once per Spotify/Shazam item to backfill artwork so
# every card in the Music tab renders with a thumbnail, not just Apple's.
#
# Rate limit: undocumented but ~20 req/sec is safe. 100 Spotify + 100 Shazam
# lookups run in ~10-15s with 8 concurrent workers. Cached in-process by
# (artist, title) so if Shazam and Spotify both list the same track we only
# pay for one lookup.
_ITUNES_SEARCH_URL = 'https://itunes.apple.com/search'


_DEEZER_SEARCH_URL = 'https://api.deezer.com/search'


def _try_itunes(title: str, artist: str) -> str:
    """iTunes Search API lookup. Empty string on miss/error."""
    try:
        r = requests.get(
            _ITUNES_SEARCH_URL,
            params={
                'term':   f'{title} {artist}'.strip(),
                'entity': 'song',
                'limit':  1,
                'media':  'music',
            },
            headers={'User-Agent': _UA},
            timeout=8,
        )
        if not r.ok:
            return ''
        results = ((r.json() or {}).get('results') or [])
        if not results:
            return ''
        art = results[0].get('artworkUrl100') or ''
        # Upgrade 100x100 to 300x300 - iTunes CDN honors any square
        # size in the URL path pattern .../100x100bb.jpg. Nicer for
        # retina thumbnails.
        if '100x100' in art:
            art = art.replace('100x100', '300x300')
        return art
    except Exception as e:
        logger.debug("itunes lookup failed for %r %r: %s", title, artist, e)
        return ''


def _try_deezer(title: str, artist: str) -> str:
    """Deezer's public search API - fallback when iTunes doesn't have
    the track. Deezer indexes newer/regional/TikTok-driven releases
    faster than iTunes, so Shazam's discovery chart matches better
    here. Returns 250x250 `album.cover_medium` (empty on miss)."""
    try:
        r = requests.get(
            _DEEZER_SEARCH_URL,
            params={
                # Use Deezer's structured query syntax so we get an
                # exact match on both title and artist, not a fuzzy
                # OR search that returns cover songs.
                'q':     f'track:"{title}" artist:"{artist}"',
                'limit': 1,
            },
            headers={'User-Agent': _UA},
            timeout=8,
        )
        if not r.ok:
            return ''
        results = ((r.json() or {}).get('data') or [])
        if not results:
            # Retry without structured operators - Deezer's exact
            # match sometimes over-restricts on tracks with punctuation
            # differences ("hate that i made you love me" vs "Hate That
            # I Made You Love Me"). One free-text retry often lands it.
            r = requests.get(
                _DEEZER_SEARCH_URL,
                params={'q': f'{title} {artist}', 'limit': 1},
                headers={'User-Agent': _UA},
                timeout=8,
            )
            if not r.ok:
                return ''
            results = ((r.json() or {}).get('data') or [])
            if not results:
                return ''
        album = (results[0] or {}).get('album') or {}
        # Prefer cover_big (500px) > cover_medium (250px) > cover_small
        return album.get('cover_big') or album.get('cover_medium') or ''
    except Exception as e:
        logger.debug("deezer lookup failed for %r %r: %s", title, artist, e)
        return ''


def _itunes_artwork_lookup(title: str, artist: str,
                            cache: dict[tuple[str, str], str]
                            ) -> str:
    """Return an artwork URL for the (title, artist). Tries iTunes
    first (widest catalog for mainstream), Deezer second (better on
    TikTok-driven / new / regional releases). '' if both miss.
    Cached in-place by (title, artist) key."""
    key = (title.strip().lower(), (artist or '').strip().lower())
    if key in cache:
        return cache[key]
    art = _try_itunes(title, artist)
    if not art:
        art = _try_deezer(title, artist)
    cache[key] = art
    return art


def _enrich_with_itunes_artwork(items: list[dict],
                                 cache: dict[tuple[str, str], str],
                                 max_workers: int = 8) -> None:
    """Mutate `items` in place to add an `image` field via iTunes Search.
    Skips items that already have an image (Apple's own feed).
    """
    if not items:
        return
    needs: list[dict] = [it for it in items
                          if not it.get('image')
                          and it.get('title')
                          and it.get('artist')]
    if not needs:
        return
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(
                lambda it: it.__setitem__(
                    'image',
                    _itunes_artwork_lookup(it['title'], it['artist'], cache)
                ),
                needs))
    except Exception as e:
        logger.info("itunes artwork batch failed: %s", e)


# ---------------------------------------------------------------------------
# Amazon Music "All Hits" playlist  (Playwright + donated cookies)
# ---------------------------------------------------------------------------
# Amazon Music retired their global "Top 100 Songs" chart in the 2025
# product refresh and never replaced it with a public rank-ordered
# equivalent. What DOES exist is a stable of editor-curated "hit"
# playlists: `All Hits` (B01M11SBC8), `2026!` (B0DQRHRXMQ), and
# `Hits Different` (B0CCZTRH1W). `All Hits` is Amazon Music's own answer
# to Spotify's "Today's Top Hits" - the same mainstream flagship shape,
# refreshed by Amazon's editorial team.
#
# The playlist page is a heavily-JavaScripted PWA. Anonymous callers
# get a ~11KB shell with `<music-image-row>` skeletons stuck in
# `loading=""`; the chart data only arrives after a POST to
# `na.web.skill.music.a2z.com/api/showPlaylistPage` that requires the
# `x-amzn-authentication` client token embedded in the logged-in
# session. So we drive real Chrome via Playwright with a donated
# `music.amazon.com` session, wait for hydration, scroll the virtualized
# list to force lazy-load of every row, and read the populated custom
# element attributes back out via `page.evaluate`.
#
# One-time operator setup (already done):
#   1. Log in to https://music.amazon.com in your local Chrome
#   2. `python3 scripts/trends_scrapers/donate_cookies.py music.amazon.com`
#   3. Cookies auto-refresh via the launchd donation loop; if the
#      Amazon Music card goes empty for 24h, re-run step 2.
_AMAZON_MUSIC_ANCHOR_ASIN  = 'B01M11SBC8'  # All Hits (editorial flagship)
_AMAZON_MUSIC_ANCHOR_LABEL = 'All Hits'
_AMAZON_MUSIC_HOMEPAGE     = 'https://music.amazon.com/'
_AMAZON_MUSIC_PLAYLIST_URL = f'https://music.amazon.com/playlists/{_AMAZON_MUSIC_ANCHOR_ASIN}'


def _fetch_amazon_music(limit: int = 100) -> tuple[list[dict], str]:
    """Scrape Amazon Music's `All Hits` playlist via Playwright + donated
    cookies. Returns `(items, sub)` where `sub` is the operator-facing
    note used when items[] is empty (missing cookies or Playwright).

    Every item has: {rank, title, artist, url, image}. The playlist is
    ~60 tracks (Amazon's editorial size), not 200 - the historical
    "top 200" spec was based on a chart Amazon retired. Present for
    parity with Spotify / Apple Music / Shazam surfaces.
    """
    try:
        from ._playwright import _lazy_playwright, _launch_browser, _try_stealth, UA
        from ._base import (load_donated_cookies_playwright, cookie_donation_status,
                            classify_hydration_failure)
        from ._amazon_music import (goto_past_picker, dismiss_profile_picker,
                                    SIGNED_IN_MARKERS)
    except Exception as e:
        logger.info("amazon music: playwright helpers unavailable: %s", e)
        _mark_cookie_gap('amazon_music', 'music.amazon.com',
                          reason=f'playwright helpers unavailable: {e}')
        return [], _WARMING_UP_HINT

    sp = _lazy_playwright()
    if sp is None:
        logger.warning(
            "amazon music: playwright not installed - install with "
            "`pip3 install --break-system-packages playwright playwright-stealth`"
        )
        _mark_cookie_gap('amazon_music', 'music.amazon.com',
                          reason='playwright not installed on scraper host')
        return [], _WARMING_UP_HINT

    donated = load_donated_cookies_playwright('music.amazon.com')
    if not donated:
        status = cookie_donation_status('music.amazon.com')
        logger.warning(
            "amazon music: no donated cookies for music.amazon.com "
            "(status=%s). Run `python3 scripts/trends_scrapers/"
            "donate_cookies.py music.amazon.com` from a logged-in laptop.",
            status,
        )
        _mark_cookie_gap('amazon_music', 'music.amazon.com',
                          reason=('no donated cookies present for '
                                  f'music.amazon.com (status={status})'))
        return [], _WARMING_UP_HINT

    items: list[dict] = []
    try:
        with sp() as pw:
            try:
                browser, _channel = _launch_browser(pw, prefer_chrome=True)
            except Exception as e:
                logger.warning("amazon music: playwright launch failed: %s", e)
                _mark_cookie_gap('amazon_music', 'music.amazon.com',
                                  reason=f'playwright launch failed: {e}')
                return [], _WARMING_UP_HINT

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

            # Warm homepage so the auth-context bootstrap fires (this is
            # what surfaces the x-amzn-authentication token used by
            # subsequent /api/ calls). Skipping this leaves later
            # showPlaylistPage returning a "Service error" template.
            # A signed-in account lands on the household profile
            # chooser here, which intercepts every later navigation
            # until a profile is picked, so clear it during the warmup.
            try:
                page.goto(_AMAZON_MUSIC_HOMEPAGE,
                          wait_until='domcontentloaded', timeout=45000)
                page.wait_for_timeout(3500)
                dismiss_profile_picker(page)
            except Exception as e:
                logger.info("amazon music: homepage warmup: %s", e)

            goto_past_picker(page, _AMAZON_MUSIC_PLAYLIST_URL)

            # Wait for the first ~10 rows to hydrate.
            _row_selector = 'music-image-row[primary-text]'
            try:
                page.wait_for_function(
                    f"() => document.querySelectorAll("
                    f"'{_row_selector}').length >= 10",
                    timeout=25000,
                )
            except Exception:
                try:
                    final_url = page.url or ''
                    page_text = page.evaluate(
                        "() => document.body ? document.body.innerText : ''"
                    ) or ''
                except Exception:
                    final_url, page_text = '', ''
                kind, why, notify = classify_hydration_failure(
                    target_url=_AMAZON_MUSIC_PLAYLIST_URL,
                    selector=_row_selector,
                    final_url=final_url,
                    page_text=page_text,
                    cookie_count=len(donated),
                    signed_in_markers=SIGNED_IN_MARKERS,
                )
                logger.warning("amazon music: %s (%s)", why, kind)
                try:
                    ctx.close(); browser.close()
                except Exception:
                    pass
                if notify:
                    _mark_cookie_gap('amazon_music', 'music.amazon.com',
                                      reason=why)
                return [], _WARMING_UP_HINT

            # Virtualized list: repeatedly scroll the last row into view
            # to fetch the next page. Bail out when the row count stops
            # growing across 2 consecutive rounds or we've hit the cap.
            prev_n = 0
            steady = 0
            for _ in range(30):
                n = page.evaluate(
                    "document.querySelectorAll('music-image-row[primary-text]').length"
                )
                if n == prev_n:
                    steady += 1
                    if steady >= 3:
                        break
                else:
                    steady = 0
                    prev_n = n
                page.evaluate("""() => {
                    const els = document.querySelectorAll('music-image-row');
                    if (els.length) els[els.length - 1]
                        .scrollIntoView({behavior:'instant', block:'end'});
                }""")
                page.wait_for_timeout(500)

            rows = page.evaluate("""(limit) => {
                const out = [];
                document.querySelectorAll('music-image-row').forEach((el) => {
                    const p  = el.getAttribute('primary-text')     || el.primaryText     || '';
                    const s1 = el.getAttribute('secondary-text-1') || el.secondaryText1  || '';
                    const s2 = el.getAttribute('secondary-text-2') || el.secondaryText2  || '';
                    const img = el.getAttribute('image-src')       || '';
                    const href = el.getAttribute('primary-href')   || '';
                    if (p && p.length >= 1) {
                        out.push({title: p, artist: s1, album: s2, image: img, href});
                    }
                });
                return out.slice(0, limit);
            }""", limit)

            try:
                ctx.close(); browser.close()
            except Exception:
                pass

            for i, r in enumerate(rows, start=1):
                href = r.get('href') or ''
                url = ('https://music.amazon.com' + href) if href.startswith('/') else href
                if not url:
                    url = _AMAZON_MUSIC_PLAYLIST_URL
                items.append({
                    'rank':   i,
                    'title':  (r.get('title')  or '').strip(),
                    'artist': (r.get('artist') or '').strip(),
                    'album':  (r.get('album')  or '').strip(),
                    'url':    url,
                    'image':  r.get('image') or '',
                })
    except Exception as e:
        logger.warning("amazon music: playwright pass failed: %s", e)
        _mark_cookie_gap('amazon_music', 'music.amazon.com',
                          reason=f'playwright pass failed: {e}')
        return [], _WARMING_UP_HINT

    if not items:
        # Rows hydrated (we got past the wait above) and then extracted
        # to nothing, so the session is demonstrably good and the
        # attribute names on the row element have moved. No cookie-gap
        # notification: re-donating cannot fix a renamed attribute.
        logger.warning(
            "amazon music: %s hydrated but `music-image-row` rows "
            "extracted to 0 items. The row element's attributes have "
            "changed. This is not a cookie problem.",
            _AMAZON_MUSIC_PLAYLIST_URL)
        return [], _WARMING_UP_HINT
    return items, ''


def fetch() -> dict[str, Any]:
    """Pull all sources in sequence. Each is best-effort - a single
    source failing produces an empty items[] for that source but the
    snapshot still writes.

    Order of `sources` here doesn't dictate render order (the frontend
    picks that); we sort roughly by production cost."""
    spotify_items = _fetch_spotify(limit=100)
    apple_items   = _fetch_apple(limit=100)
    shazam_items  = _fetch_shazam(limit=100)
    ytm_items     = _fetch_youtube_music(limit=100)
    amz_items, amz_sub = _fetch_amazon_music(limit=100)

    # Backfill artwork thumbnails from iTunes Search API for every
    # source that doesn't ship its own image field. Shared cache so a
    # track that appears on multiple charts is only looked up once.
    # Apple items already carry `artworkUrl100` from the RSS, so
    # `_enrich_with_itunes_artwork` no-ops on them.
    art_cache: dict[tuple[str, str], str] = {}
    _enrich_with_itunes_artwork(spotify_items, art_cache)
    _enrich_with_itunes_artwork(shazam_items,  art_cache)
    _enrich_with_itunes_artwork(ytm_items,     art_cache)
    logger.info("itunes artwork cache: %d unique lookups", len(art_cache))

    # TikTok retired 2026-09-23 (Jenna). TikTok rebuilt the Creative
    # Center as TikTok One and its music routes now redirect to the
    # hashtag tab, so no public songs chart exists to read. The
    # Billboard partnership ended in March 2025, the Viral 50 is
    # in-app only, and SoundOn gates its charts behind a login. There
    # is nothing to substitute that would still be TikTok's own
    # measurement, so the rail is gone rather than permanently empty.

    return {
        # `national` mirrors Spotify (the biggest reach) so the standard
        # snapshot summary in _index.json shows a useful count. The real
        # breakdown lives in `sources` and is what compute_view reads.
        'national': spotify_items[:100] or apple_items[:100],
        'available': bool(spotify_items or apple_items or shazam_items
                          or ytm_items or amz_items),
        'sources': {
            'spotify': {
                'label':     'Spotify Daily Top 200 (US)',
                'sub':       "What people are streaming right now on Spotify.",
                'items':     spotify_items,
                'available': bool(spotify_items),
            },
            'apple': {
                'label':     'Apple Music Top 100 (US)',
                'sub':       'What Apple Music subscribers are playing.',
                'items':     apple_items,
                'available': bool(apple_items),
            },
            'youtube': {
                'label':     'YouTube Music (US)',
                'sub':       'What people are watching and listening to on YouTube.',
                'items':     ytm_items,
                'available': bool(ytm_items),
            },
            'shazam': {
                'label':     'Shazam Top 200 (US)',
                'sub':       "What people are IDing right now - the discovery signal.",
                'items':     shazam_items,
                'available': bool(shazam_items),
            },
            'amazon': {
                'label':     'Amazon Music',
                'sub':       (amz_sub or 'What Amazon Music subscribers are playing.'),
                'items':     amz_items,
                'available': bool(amz_items),
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
