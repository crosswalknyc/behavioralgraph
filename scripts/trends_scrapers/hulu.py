"""
Hulu trending scraper.

Requires donated cookies for `hulu.com`. Donate via:

    python3 scripts/trends_scrapers/donate_cookies.py --domain hulu.com

A Hulu session on the Disney+/Hulu/Max bundle plan also carries the
Max entitlement, but Max serves its own domain, so `max.com` cookies
are donated separately.

Standalone:
    python3 -m scripts.trends_scrapers.hulu

Parser strategy
---------------
Hulu's Next.js pages ship a mostly-empty SSR `__NEXT_DATA__` blob and
lazy-fetch actual content from `discover.hulu.com/content/v5/view_hubs`
client-side after page load. Waiting for the client-side hydration is
cheaper than authing against the discover.hulu.com API directly, and
the rendered DOM ends up with a very clean pattern:

    <a href="/series/<uuid>" aria-label="Show Name, Item N of many">

or

    <a href="/movie/<uuid>" aria-label="Movie Title, Item N of many">

Each hub page (Home / TV / Movies / News) surfaces ~30 titles this way.
We dedupe across hubs, strip the "Item N of many" tail, and rank in
discovery order.
"""

from __future__ import annotations

import logging
import re
import sys
from html import unescape
from typing import Any
from urllib.parse import quote

from ._base import http_get, run_scraper
from ._playwright import render_pages

logger = logging.getLogger(__name__)


HULU_URLS = [
    ('Home',        'https://www.hulu.com/hub/home'),
    ('TV',          'https://www.hulu.com/hub/tv'),
    ('Movies',      'https://www.hulu.com/hub/movies'),
    ('News',        'https://www.hulu.com/hub/news'),
]


# Wait for a real content tile to appear before we snapshot. If none
# appear within the hydration budget we still snapshot on the fallback
# timer so we can log the empty-body case.
_HULU_HYDRATE_SELECTORS = [
    'a[href^="/series/"]',
    'a[href^="/movie/"]',
    'a[data-automationid="tile"]',
    'div[data-automationid="collection"]',
]


# The rendered anchor pattern. Two capture groups:
#   1. Path segment: `series/<uuid>`, `movie/<uuid>`, or `watch/<uuid>`
#   2. Aria-label text.
#
# Historic (pre-2026-08): aria-label was "<Title>, Item N of many" or
# ", Season N".
# Current: aria-label is "Play <Title>" on the play-button anchor and
# just "<Title>" on the tile-detail anchor. The regex captures both;
# `_clean_title` normalizes the "Play " prefix out. Adding `/watch/`
# to the path union catches the new watch-page shortcut tiles Hulu is
# rolling out on the Home hub.
_HULU_TILE_RE = re.compile(
    r'<a[^>]+href="(/(?:series|movie|watch)/[a-f0-9\-]+)"[^>]*'
    r'aria-label="([^"]{2,220})"',
    re.IGNORECASE,
)
_HULU_TAIL_ITEM_RE   = re.compile(r',\s*Item\s+\d+\s+of\s+many\s*$', re.IGNORECASE)
_HULU_TAIL_SEASON_RE = re.compile(r',\s*Season\s+\d+\s*$',           re.IGNORECASE)
# 2026-08-14: Hulu now labels the primary tile anchor as
# "Play <Title>" (the play-button becomes the tile's main hit target).
# Strip the verb; the title we want is what follows.
_HULU_HEAD_PLAY_RE   = re.compile(r'^\s*Play\s+',                     re.IGNORECASE)


def _clean_title(raw: str) -> str:
    """Strip Hulu's screen-reader decorations from the aria-label."""
    s = unescape(raw).strip()
    s = _HULU_HEAD_PLAY_RE.sub('', s)
    s = _HULU_TAIL_ITEM_RE.sub('', s)
    s = _HULU_TAIL_SEASON_RE.sub('', s)
    return s.strip()


def _classify_from_path(path: str) -> str:
    p = path.lower()
    if p.startswith('/movie/'):
        return 'Film'
    if p.startswith('/series/'):
        return 'TV'
    # `/watch/` is ambiguous (both films and series share it). Return
    # empty and let the Flask-side lexical classifier decide from the
    # title text (movie-year suffixes, common series markers, etc).
    return ''


def _extract_hulu_dom(html: str, limit: int = 25) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for m in _HULU_TILE_RE.finditer(html):
        path  = m.group(1)
        title = _clean_title(m.group(2))
        if len(title) < 2 or len(title) > 220:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            'rank':             len(out) + 1,
            'title':            title,
            'url':              f'https://www.hulu.com{path}',
            'category_display': _classify_from_path(path),
            'collection':       '',
        })
        if len(out) >= limit:
            break
    return out


# ────────────────────────────────────────────────────────────────────
# JustWatch fallback (public, cookie-free)
# ────────────────────────────────────────────────────────────────────
# Around 2026-08-06, Hulu's WAF started rejecting authenticated
# Playwright sessions from Hetzner's datacenter IP even with fresh
# donated cookies (the page returns the logged-out marketing homepage
# with only ~10 marketing tiles and no `/series/` or `/movie/` anchors).
# JustWatch ranks the same catalog through their own signals and
# exposes it via `data-title="..."` attributes on their public provider
# pages - no auth, no cookies, resilient to Hulu-side DOM changes.
#
# We hit two pages:
#   - https://www.justwatch.com/us/provider/hulu/movies    → films
#   - https://www.justwatch.com/us/provider/hulu/tv-shows  → TV
#
# The pages are server-rendered and return the full list in one
# response, so a single http_get per page (with a real browser UA) is
# enough. Ranking mirrors JustWatch's own "sorted by popularity"
# default which is what most people use their provider pages to see.
_JW_DATA_TITLE_RE = re.compile(r'data-title="([^"]{2,120})"')
_JW_TITLE_HREF_RE = re.compile(
    r'href="/us/(movie|tv-show)/([a-z0-9\-]+)"[^>]*>\s*([^<]{2,120})\s*</a>',
    re.IGNORECASE,
)


def _fetch_justwatch(kind: str, limit: int = 20) -> list[dict]:
    """Fetch popularity-ranked titles on Hulu from JustWatch's public
    provider page. `kind` is 'movie' or 'tv'.
    """
    path = 'movies' if kind == 'movie' else 'tv-shows'
    url  = f'https://www.justwatch.com/us/provider/hulu/{path}'
    try:
        r = http_get(url, timeout=25, headers={
            'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                           'AppleWebKit/537.36 (KHTML, like Gecko) '
                           'Chrome/126.0.0.0 Safari/537.36'),
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'en-US,en;q=0.9',
        })
    except Exception as e:
        logger.info("hulu justwatch fallback %s: request failed: %s", kind, e)
        return []
    if not r or r.status_code != 200 or not r.text:
        logger.info("hulu justwatch fallback %s: status=%s",
                     kind, getattr(r, 'status_code', None))
        return []

    html = r.text
    label = 'Film' if kind == 'movie' else 'TV'

    # Primary parse: JustWatch stamps every tile with data-title. The
    # attribute is populated even for tiles whose visible link text is
    # rendered via CSS pseudo-elements, so it's the most reliable hook.
    seen: set[str] = set()
    out: list[dict] = []
    for m in _JW_DATA_TITLE_RE.finditer(html):
        title = unescape(m.group(1)).strip()
        if not title or len(title) > 200:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({
            'rank':             len(out) + 1,
            'title':            title,
            'url':              '',   # filled in below when we can pair
            'category_display': label,
            'collection':       'JustWatch popularity',
            'source_fallback':  'justwatch',
        })
        if len(out) >= limit:
            break

    # Best-effort URL pairing: the same page carries anchor+slug pairs
    # for many tiles. Pair by title-case match so the frontend can deep
    # link to the JustWatch title page (which fans out to Hulu). Any
    # tile we can't pair keeps `url=''`.
    slug_by_title: dict[str, str] = {}
    for m in _JW_TITLE_HREF_RE.finditer(html):
        anchor_kind = m.group(1).lower()
        slug        = m.group(2)
        anchor_text = unescape(m.group(3)).strip().lower()
        # Only pair anchors whose kind matches this fetch.
        if (kind == 'movie' and anchor_kind != 'movie') or \
           (kind == 'tv'    and anchor_kind != 'tv-show'):
            continue
        if anchor_text and anchor_text not in slug_by_title:
            slug_by_title[anchor_text] = slug

    for it in out:
        slug = slug_by_title.get(it['title'].lower())
        if slug:
            it['url'] = f'https://www.justwatch.com/us/{"movie" if kind == "movie" else "tv-show"}/{slug}'

    logger.info("hulu justwatch fallback %s: parsed %d titles from %d-byte HTML",
                 kind, len(out), len(html))
    return out


def fetch() -> dict[str, Any]:
    rendered = render_pages(HULU_URLS,
                             homepage='https://www.hulu.com/',
                             cookie_domain='hulu.com',
                             wait_selectors=_HULU_HYDRATE_SELECTORS,
                             wait_ms=6000,
                             scroll_ms=2500,
                             hydration_wait_ms=15000)

    # Bucket parsed items by kind (Film vs TV) so we can guarantee film
    # representation in the final 20. Previously we appended every rail
    # into a single list and truncated at 20 - the Home rail alone
    # returns 30 TV series most days, which pushed the Movies rail out
    # entirely and left the Films tab empty on the dashboard.
    films: list[dict] = []
    tv:    list[dict] = []
    seen: set[str] = set()
    for label, html in rendered:
        items = _extract_hulu_dom(html, limit=30)
        parsed_films = parsed_tv = 0
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            if it.get('category_display') == 'Film':
                films.append(it)
                parsed_films += 1
            else:
                tv.append(it)
                parsed_tv += 1
        logger.info("hulu %s: parsed %d films + %d tv from %d-byte HTML",
                     label, parsed_films, parsed_tv, len(html))

    # If the authenticated path yields too few items (Hulu is
    # WAF-blocking datacenter IPs; the marketing page renders instead
    # of the logged-in hub), swap in JustWatch's public popularity
    # ranking. Threshold is intentionally low - we prefer real Hulu
    # ranks when they exist, but 5+ items reads as "half working" and
    # would produce a lopsided panel.
    used_fallback = False
    if (len(films) + len(tv)) < 8:
        jw_films = _fetch_justwatch('movie', limit=20)
        jw_tv    = _fetch_justwatch('tv',    limit=20)
        if (len(jw_films) + len(jw_tv)) >= 8:
            films = jw_films
            tv    = jw_tv
            used_fallback = True
            logger.info("hulu: authenticated path returned <8 items; "
                         "swapping in JustWatch fallback "
                         "(%d films + %d tv)",
                         len(films), len(tv))

    # Interleave so both categories are in the top 20. Cap around 60/40
    # TV/Film mix which mirrors Hulu's actual consumption pattern (Hulu
    # is TV-first) while guaranteeing at least ~8 films visible when
    # the Movies rail returns data.
    all_items: list[dict] = []
    fi = ti = 0
    while (fi < len(films) or ti < len(tv)) and len(all_items) < 20:
        for _ in range(3):
            if ti < len(tv) and len(all_items) < 20:
                all_items.append(tv[ti]); ti += 1
        for _ in range(2):
            if fi < len(films) and len(all_items) < 20:
                all_items.append(films[fi]); fi += 1

    for i, it in enumerate(all_items[:20], start=1):
        it['rank'] = i

    out: dict[str, Any] = {'national': all_items[:20]}
    # Ship pre-split lists too. Flask has a lexical inference fallback
    # but Hulu-via-JustWatch already knows kind cleanly, so we hand
    # them over unambiguously.
    out['us_films'] = [dict(it, rank=i) for i, it in enumerate(films[:20], 1)]
    out['us_tv']    = [dict(it, rank=i) for i, it in enumerate(tv[:20],    1)]
    if used_fallback:
        out['source_note'] = 'JustWatch (Hulu authenticated path unavailable)'
    return out


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('hulu', 'Hulu', 'streaming', fetch)
    print(f"hulu: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
