"""
Music charts scraper - Spotify Top 200, Apple Music Top 100, Shazam
Top 200, YouTube Music (US weekly), TikTok Sounds, and Amazon Music
All Hits.

Aggregates the biggest free (and cookie-donated) music trending signals
into a single snapshot the dashboard renders as one tab:

    Spotify Daily Top 200 US -> mainstream streaming (via kworb.net)
    Apple Music Top 100 US   -> what Apple Music subscribers play
    Shazam Top 200 US        -> what people are IDing right now (discovery)
    YouTube Music US Weekly  -> what's most-played on YouTube in the US
                                (via kworb.net, YouTube's own stream data)
    TikTok Trending Sounds   -> what's about to hit the charts (leading)
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
        "tiktok":   {"label": "TikTok Sounds",              "items": [{...}], "available": bool},
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
# TikTok Sounds  (Creative Center Trends -> Songs tab, Playwright DOM)
# ---------------------------------------------------------------------------
# As of 2026-07 TikTok has removed the public Songs/Sounds tab from the
# Creative Center. Only Hashtag + Video tabs render even with a fully-
# authenticated session (sessionid + sid_guard + sid_tt on .tiktok.com);
# the Creator tab explicitly reads "Coming soon". The old JSON APIs
# (/creative_radar_api/v1/popular_trend/{sound,song,music}/list) all
# return 404. No other free public source exposes TikTok trending
# music - SoundOn (TikTok's own artist service) redirects the charts
# page to /login for every visitor.
#
# The scraper still runs Playwright to (a) probe whether TikTok ever
# reinstates the Songs tab, and (b) capture a diagnostic in the daily
# snapshot so we can see the day this comes back. If the scrape ever
# yields data the dashboard picks it up automatically.
#
# Until then the frontend card shows an honest "not currently exposed"
# note in place of a fake "Coming soon" placeholder.

_TT_CC_HASHTAG_URL = ('https://ads.tiktok.com/business/creativecenter/'
                      'inspiration/popular/hashtag/pc/en')

# In a logged-in DOM, each Sounds/Songs card looks like:
#   <div .../>#hashtag or Song title text</div>       <-- primary label
#   <span>Artist Name</span>                          <-- author (optional)
#   <span>234.5K</span><span>Posts</span>
#   <span>213M</span><span>Plays</span> or <span>Views</span>
# The class names are Emotion-hashed (rebuilt each deploy) so we match on
# text-node structure and label proximity, exactly like tiktok.py.
_TT_LABEL_RE = re.compile(r'>\s*(Posts|Plays|Views|Publish|Play)\s*<',
                          re.IGNORECASE)
_TT_STAT_RE = re.compile(
    r'>([\d.,]+\s*[KMB]?)</span>\s*<span[^>]*>\s*(Posts|Plays|Views|Publish|Play)',
    re.IGNORECASE,
)


def _parse_shorthand_count(s: str) -> int:
    """'1.2M' -> 1_200_000, '340K' -> 340_000, '9,876' -> 9876."""
    if not s:
        return 0
    txt = s.strip().replace(',', '').replace(' ', '')
    m = re.match(r'^([\d.]+)\s*([KkMmBb])?$', txt)
    if not m:
        return 0
    try:
        num = float(m.group(1))
    except ValueError:
        return 0
    suf  = (m.group(2) or '').upper()
    mult = {'K': 1_000, 'M': 1_000_000, 'B': 1_000_000_000}.get(suf, 1)
    return int(num * mult)


def _fetch_tiktok_sounds(limit: int = 40) -> tuple[list[dict], dict]:
    """Playwright DOM scrape of the CC Songs tab. Returns (items, meta)
    where `meta` describes what happened (auth status, cookie age,
    what the anonymous fallback rendered) so `fetch()` can decide
    whether to surface an actionable message in the snapshot payload.

    Fully auth'd: returns 20-40 sounds with title/artist/plays/posts.
    Anonymous / partial auth: returns [] with meta['auth_required']
    so the dashboard can prompt for a fresh cookie donation."""
    meta: dict = {'auth_required': False, 'cookie_ok': False,
                  'reason': None}

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        meta['reason'] = 'playwright_not_installed'
        return [], meta

    try:
        from ._playwright import UA, _launch_browser, _try_stealth
        from ._proxy import get_proxy_config, playwright_proxy
        from ._base import (load_donated_cookies_playwright,
                            cookie_donation_status)
    except Exception as e:
        meta['reason'] = f'playwright_helpers_missing: {e}'
        return [], meta

    donated_cookies = load_donated_cookies_playwright('ads.tiktok.com')
    donation        = cookie_donation_status('ads.tiktok.com')
    meta['cookie_age_hours'] = donation.get('age_hours')
    meta['cookie_count']     = donation.get('count') or len(donated_cookies)
    # Sessionid + sid_guard are the actual auth cookies. They live on
    # the parent `.tiktok.com` domain, not `ads.tiktok.com`, so the
    # donate_cookies.py fix that harvests parent-domain cookies is
    # what unlocks this path.
    donated_names = {c.get('name') for c in donated_cookies}
    has_session   = bool(donated_names & {'sessionid', 'sid_guard',
                                          'sid_ucp_v1', 'sid_tt'})
    meta['has_session_cookie'] = has_session

    if not donated_cookies:
        meta['reason'] = 'no_donated_cookies'
        meta['auth_required'] = True
        return [], meta

    proxy_dict = playwright_proxy(get_proxy_config()) or None
    final_html = ''
    with sync_playwright() as pw:
        try:
            browser, _channel = _launch_browser(pw, prefer_chrome=True,
                                                 proxy=proxy_dict)
        except Exception as e:
            meta['reason'] = f'playwright_launch_failed: {e}'
            return [], meta
        try:
            ctx = browser.new_context(
                user_agent=UA,
                viewport={'width': 1440, 'height': 900},
                locale='en-US',
                timezone_id='America/New_York',
                extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
            )
            try:
                ctx.add_cookies(donated_cookies)
            except Exception as e:
                logger.info("tiktok sounds: cookie inject failed: %s", e)
            page = ctx.new_page()
            _try_stealth(page)
            # Warm the cookie jar on ads.tiktok.com root before nav.
            try:
                page.goto('https://ads.tiktok.com/', wait_until='domcontentloaded',
                           timeout=30000)
                page.wait_for_timeout(2000)
            except Exception:
                pass
            try:
                page.goto(_TT_CC_HASHTAG_URL, wait_until='domcontentloaded',
                           timeout=45000)
            except Exception as e:
                meta['reason'] = f'cc_nav_failed: {e}'
                return [], meta

            # First hydration check: whether ANY stat labels appeared.
            try:
                page.wait_for_selector('span:has-text("Posts")',
                                        timeout=15000, state='attached')
            except Exception:
                logger.info("tiktok sounds: 'Posts' label never appeared "
                             "on hashtag tab")

            # Try to switch to the Songs tab. As of 2026-07 this tab
            # doesn't exist in the CC anymore (Hashtag + Video only,
            # Creator marked "Coming soon"). We probe for it anyway so
            # this scraper starts working the day TikTok reinstates it.
            switched = False
            for label in ('Songs', 'Music', 'Sounds', 'Song'):
                try:
                    loc = page.get_by_text(label, exact=True)
                    if loc.count() > 0:
                        loc.first.click(timeout=3000)
                        page.wait_for_timeout(2500)
                        switched = True
                        logger.info("tiktok sounds: clicked '%s' tab", label)
                        break
                except Exception:
                    continue
            if not switched:
                meta['reason'] = ('cc_songs_tab_not_present - TikTok has '
                                   'removed the public Songs chart from '
                                   'Creative Center (mid-2026). Only '
                                   'Hashtag + Video tabs render; Creator '
                                   'reads "Coming soon". No other free '
                                   'public source (SoundOn charts require '
                                   'login) currently exposes trending '
                                   'sounds.')
                meta['auth_required'] = False
                meta['source_unavailable'] = True
                return [], meta

            # Progressive scroll to trigger the CC's lazy-load.
            last_count = 0
            stalled = 0
            for i in range(25):
                try:
                    html_now = page.content()
                except Exception:
                    break
                count = len(_TT_LABEL_RE.findall(html_now))
                if count >= limit + 3:
                    final_html = html_now
                    break
                if count == last_count:
                    stalled += 1
                    if stalled >= 4:
                        final_html = html_now
                        break
                else:
                    stalled = 0
                    last_count = count
                try:
                    page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    page.wait_for_timeout(1600)
                except Exception:
                    break
            if not final_html:
                try:
                    final_html = page.content()
                except Exception:
                    pass
        finally:
            try: ctx.close()
            except Exception: pass
            try: browser.close()
            except Exception: pass

    items = _parse_tt_sounds_dom(final_html, limit=limit)
    meta['cookie_ok'] = len(items) > 5
    if not items:
        meta['auth_required'] = True
        meta['reason'] = meta['reason'] or 'dom_parse_yielded_zero'
    return items, meta


def _parse_tt_sounds_dom(html: str, *, limit: int = 40) -> list[dict]:
    """Parse the CC Songs card list. Each card window is delimited by
    a Play/Plays/Posts stat label; we walk from label back to the
    nearest title text and forward to the artist row.

    The DOM structure once hydrated is:
      <div ...>1</div>                    (rank in a bold cell)
      <div class="...truncate...">Choosin' Texas</div>   (title)
      <span class="...">Ella Langley</span>              (artist, optional)
      <span>1.2M</span><span>Posts</span>
      <span>340M</span><span>Plays</span>

    Selectors are hashed classes so we anchor on the "Posts"/"Plays"
    labels and rewind through the preceding text nodes."""
    if not html:
        return []
    # Find every stat label position; each is roughly the end of a card.
    label_matches = list(_TT_LABEL_RE.finditer(html))
    if not label_matches:
        return []

    # A card starts at the previous card's end (or 0) and ends after
    # its Views/Plays label window. Group consecutive labels: each card
    # has 2 label rows (Posts + Plays/Views), so pair them.
    out: list[dict] = []
    seen: set[str] = set()
    idx = 0
    prev_end = 0
    while idx < len(label_matches) - 1 and len(out) < limit:
        # Consume 2 labels per card if they're within 300 chars of each
        # other (adjacent stat spans); otherwise treat as single label.
        first  = label_matches[idx]
        second = label_matches[idx + 1] if idx + 1 < len(label_matches) else first
        if second.start() - first.end() > 400:
            card_end = first.end()
            step = 1
        else:
            card_end = second.end()
            step = 2

        card_html = html[prev_end:card_end]

        # Title: the first text node inside a "truncate" or "font-bold"
        # span that isn't a stat number. Fall back to any long-ish text.
        title = ''
        for tm in re.finditer(
                r'<div[^>]*truncate[^>]*>\s*([^<]{2,120})\s*</div>|'
                r'<span[^>]*font-bold[^>]*>\s*([^<]{2,120})\s*</span>',
                card_html):
            cand = _html.unescape((tm.group(1) or tm.group(2) or '').strip())
            # Skip pure stat numbers like "234.5K"
            if cand and not re.match(r'^[\d.,]+\s*[KMB]?$', cand) \
                    and cand.lower() not in {'posts', 'plays', 'views'}:
                title = cand
                break

        if not title:
            idx += step
            prev_end = card_end
            continue

        # Artist: the next non-stat text node after the title, usually
        # inside a smaller span. Bail if we can't find one.
        artist = ''
        artist_search = card_html[card_html.find(title) + len(title):]
        for am in re.finditer(r'<span[^>]*>\s*([^<]{2,80})\s*</span>',
                              artist_search):
            cand = _html.unescape(am.group(1).strip())
            if not cand or cand.lower() in {'posts', 'plays', 'views'}:
                continue
            if re.match(r'^[\d.,]+\s*[KMB]?$', cand):
                continue
            # Category rows read like "News & Entertainment" - skip
            # if it's clearly a category label rather than an artist.
            if cand in ('News & Entertainment', 'Sports', 'Comedy',
                         'Fashion', 'Beauty', 'Music', 'Lifestyle'):
                continue
            artist = cand
            break

        # Stats
        posts = 0
        plays = 0
        for sm in _TT_STAT_RE.finditer(card_html):
            val   = _parse_shorthand_count(sm.group(1))
            label = sm.group(2).lower()
            if 'post' in label or 'publish' in label:
                posts = max(posts, val)
            elif 'play' in label or 'view' in label:
                plays = max(plays, val)

        key = re.sub(r'\s+', ' ', f"{title}|{artist}").lower()
        if key in seen:
            idx += step
            prev_end = card_end
            continue
        seen.add(key)

        # Deep link: TikTok surfaces music at
        # https://www.tiktok.com/music/<slug-numericid>. Without the ID
        # we can only link to a search fallback.
        q = requests.utils.quote(f"{title} {artist}".strip())
        deep_url = f'https://www.tiktok.com/search/music?q={q}'

        out.append({
            'rank':   len(out) + 1,
            'title':  title,
            'artist': artist,
            'posts':  posts,
            'plays':  plays,
            'url':    deep_url,
        })
        idx += step
        prev_end = card_end

    return out[:limit]


def _load_tiktok_cookies_from_s3() -> Optional[dict]:
    """Kept for reference / debugging - the Playwright path uses
    _base.load_donated_cookies_playwright directly. Returns
    {name: value} for shells that want to test the old API path
    outside Playwright."""
    try:
        import boto3
        s3  = boto3.client('s3')
        obj = s3.get_object(Bucket='dashboard-inputs',
                             Key='trends_iq_cookies/ads.tiktok.com.json')
        raw = json.loads(obj['Body'].read().decode('utf-8'))
    except Exception as e:
        logger.info("tiktok sounds: no cookies (%s)", e)
        return None
    if isinstance(raw, list):
        return {c['name']: c['value'] for c in raw
                if c.get('name') and c.get('value')}
    if isinstance(raw, dict):
        cookies = raw.get('cookies')
        if isinstance(cookies, list):
            return {c['name']: c['value'] for c in cookies
                    if c.get('name') and c.get('value')}
        return {k: v for k, v in raw.items() if isinstance(v, str)}
    return None


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
        from ._base import load_donated_cookies_playwright, cookie_donation_status
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
            try:
                page.goto(_AMAZON_MUSIC_HOMEPAGE,
                          wait_until='domcontentloaded', timeout=45000)
                page.wait_for_timeout(3500)
            except Exception as e:
                logger.info("amazon music: homepage warmup: %s", e)

            page.goto(_AMAZON_MUSIC_PLAYLIST_URL,
                      wait_until='domcontentloaded', timeout=45000)
            page.wait_for_timeout(6000)

            # Wait for the first ~10 rows to hydrate. If they don't,
            # the session is dead - drop out to the empty-sub path.
            try:
                page.wait_for_function(
                    "() => document.querySelectorAll("
                    "'music-image-row[primary-text]').length >= 10",
                    timeout=25000,
                )
            except Exception:
                logger.warning("amazon music: first-batch hydration timed "
                               "out - cookies likely expired, re-donate")
                try:
                    ctx.close(); browser.close()
                except Exception:
                    pass
                _mark_cookie_gap('amazon_music', 'music.amazon.com',
                                  reason=('All Hits playlist hydration '
                                          'timed out after 25s; donated '
                                          'session likely stale or '
                                          'missing session cookie'))
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
        _mark_cookie_gap('amazon_music', 'music.amazon.com',
                          reason=('All Hits playlist returned 0 rows '
                                  'after scroll+extract; session likely '
                                  'valid but page structure changed'))
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
    tt_items, tt_meta = _fetch_tiktok_sounds(limit=40)
    amz_items, amz_sub = _fetch_amazon_music(limit=100)

    # Backfill artwork thumbnails from iTunes Search API for every
    # source that doesn't ship its own image field. Shared cache so a
    # track that appears on multiple charts is only looked up once.
    # Apple items already carry `artworkUrl100` from the RSS, so
    # `_enrich_with_itunes_artwork` no-ops on them. TikTok items rarely
    # have artist metadata, but we run enrichment anyway - it skips
    # items missing artist/title.
    art_cache: dict[tuple[str, str], str] = {}
    _enrich_with_itunes_artwork(spotify_items, art_cache)
    _enrich_with_itunes_artwork(shazam_items,  art_cache)
    _enrich_with_itunes_artwork(ytm_items,     art_cache)
    _enrich_with_itunes_artwork(tt_items,      art_cache)
    logger.info("itunes artwork cache: %d unique lookups", len(art_cache))

    # TikTok sub label reflects what actually happened. Since mid-2026
    # TikTok has removed the public Songs chart entirely; we still
    # probe daily in case it comes back.
    if tt_items:
        tt_sub = "Leading indicator for chart hits. What's about to break."
    elif tt_meta.get('source_unavailable'):
        tt_sub = ('TikTok removed the public Songs chart from Creative '
                  'Center in mid-2026. When they restore it (or when '
                  'SoundOn opens their charts) this card will populate '
                  'automatically. Spotify tracks TikTok-driven streams '
                  'closely, so the Spotify card is the best proxy today.')
    elif tt_meta.get('auth_required'):
        tt_sub = ('Requires a logged-in ads.tiktok.com cookie donation '
                  'from a browser signed in to the Creative Center.')
    else:
        tt_sub = 'TikTok Sounds temporarily unavailable.'

    return {
        # `national` mirrors Spotify (the biggest reach) so the standard
        # snapshot summary in _index.json shows a useful count. The real
        # breakdown lives in `sources` and is what compute_view reads.
        'national': spotify_items[:50] or apple_items[:50],
        'available': bool(spotify_items or apple_items or shazam_items
                          or ytm_items or tt_items),
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
            'tiktok': {
                'label':          'TikTok Sounds (7d)',
                'sub':            tt_sub,
                'items':          tt_items,
                'available':      bool(tt_items),
                'cookie_ok':      tt_meta.get('cookie_ok', False),
                'auth_required':  tt_meta.get('auth_required', False),
                'diagnostic':     tt_meta.get('reason'),
                'cookie_age_h':   tt_meta.get('cookie_age_hours'),
                'has_session':    tt_meta.get('has_session_cookie', False),
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
