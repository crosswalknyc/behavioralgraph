"""
Max (HBO Max) trending scraper.

Requires donated cookies for `max.com`. Donate via:

    python3 scripts/trends_scrapers/donate_cookies.py --domain max.com

CRITICAL: donate cookies from `play.max.com` (the actual player app),
NOT from the marketing shell `www.max.com`. The two use different
session tokens - only play.max.com issues the one that lets us render
the hydrated home / series / movie pages. When Jenna's signed into HBO
Max in Chrome and visits play.max.com/, the donation script picks up
the right cookie automatically.

Max renders tiles as anchors of the form:

    <a aria-label="⁦⁨⁨Rick and Morty⁩⁩. ⁨1 of 20⁩. ⁨⁨New Episode⁩⁩⁩"
       data-sonic-id="ab553cdc-..."
       data-sonic-type="show"
       data-testid="..._tile"
       href="/show/UUID">

The aria-label uses Unicode isolate characters (U+2066/8/9) to wrap the
title, then ", N of M" for position, then optional ", New" / ", New
Episode" / ", New Season" annotations. Strip those to get the title.

Note: this module is named `max_streaming.py` (not `max.py`) because
`max` shadows Python's builtin `max()` and shows up first in the
package's namespace at import time. The scraper registry in
`run_all.py` uses source key `max`.

Standalone:
    python3 -m scripts.trends_scrapers.max_streaming
"""

from __future__ import annotations

import logging
import re
import sys
from html import unescape
from typing import Any

from ._base import run_scraper
from ._playwright import render_pages

logger = logging.getLogger(__name__)


MAX_URLS = [
    ('Home',      'https://play.max.com/'),
    ('Series',    'https://play.max.com/pages/series'),
    ('Movies',    'https://play.max.com/pages/movies'),
    ('Trending',  'https://play.max.com/pages/trending'),
]


# Max ships its rails as fully-rendered DOM tiles. Once we can find at
# least a few /show/ or /movie/ anchors, the page is hydrated.
_MAX_HYDRATE_SELECTORS = [
    'a[href*="/show/"]',
    'a[href*="/movie/"]',
    'a[data-testid*="_tile"]',
]


# Match a Max tile anchor. `aria-label` first (title source), then
# `href` (deep-link + type classifier).
_MAX_TILE_RE = re.compile(
    r'<a[^>]+aria-label="([^"]{3,300})"'
    r'[^>]*href="(/(?:show|movie)/[a-f0-9\-]+)"',
    re.IGNORECASE,
)


# Unicode "isolate" wrapping characters that Max uses to protect RTL
# rendering of the title. Strip them.
_ISOLATES_RE = re.compile('[\u2066\u2067\u2068\u2069]')


# Trailing "position" and "flag" segments that decorate the title in
# aria-label. Example: "Rick and Morty. 1 of 20. New Episode".
_TAIL_POS_RE     = re.compile(r'\.\s*\d+\s+of\s+\d+.*$', re.IGNORECASE)
_TAIL_NEW_RE     = re.compile(r',\s*(?:New(?:\s+(?:Episode|Season))?|Coming Soon)\s*$',
                                re.IGNORECASE)
_TAIL_HOVER_RE   = re.compile(r',\s*(?:Add to My List|Go to (?:Movie|Show)|Watch\b.*)$',
                                re.IGNORECASE)


def _clean_title(raw: str) -> str:
    t = _ISOLATES_RE.sub('', raw).strip()
    # Max often terminates the title with a period before the "N of M"
    # positional segment. Kill that first.
    t = _TAIL_POS_RE.sub('', t).strip()
    t = _TAIL_NEW_RE.sub('', t).strip()
    t = _TAIL_HOVER_RE.sub('', t).strip()
    # Trailing punctuation that's left over after stripping positions.
    while t.endswith(('.', ',')):
        t = t[:-1].strip()
    return unescape(t)


def _classify_from_path(path: str) -> str:
    if '/movie/' in path:
        return 'Film'
    if '/show/' in path:
        return 'TV'
    return ''


# Same nav-word blacklist we use elsewhere - after stripping isolates
# some interactive elements (Search, Menu, My Stuff) end up looking
# title-shaped.
_NAV_STOPWORDS = frozenset({
    'search', 'menu', 'my stuff', 'my list', 'home', 'browse',
    'sign in', 'log in', 'sign out', 'log out', 'account', 'settings',
    'notifications', 'help', 'downloads', 'watch now', 'sports',
    'live tv', 'main', 'browse menu', 'h b o max home', 'next title',
    'unmute preview', 'mute preview',
})


def _extract_max_dom(html: str, limit: int = 40) -> list[dict]:
    items: list[dict] = []
    seen_paths: set[str] = set()
    for m in _MAX_TILE_RE.finditer(html):
        raw_label = m.group(1)
        path      = m.group(2)
        if path in seen_paths:
            continue
        seen_paths.add(path)
        title = _clean_title(raw_label)
        if not (2 <= len(title) <= 200):
            continue
        if title.lower() in _NAV_STOPWORDS:
            continue
        items.append({
            'rank':             len(items) + 1,
            'title':            title,
            'url':              f'https://play.max.com{path}',
            'category_display': _classify_from_path(path),
            'collection':       '',
        })
        if len(items) >= limit:
            break
    return items


def fetch() -> dict[str, Any]:
    # Max IP-gates non-US ranges (including Hetzner Falkenstein and any
    # residential proxy that lands outside the US). Route through the
    # IPRoyal residential proxy so we hit a US exit. This is a no-op
    # when IPROYAL_PROXY_* env vars aren't set - the scraper just tries
    # the direct route and (on Hetzner) will get a ~10KB rejection page.
    #
    # NOTE: for the proxy to reliably land US exits, the IPRoyal
    # dashboard's Country/Region dropdown must be set to
    # "United States". The default "Random" rotation gives US only
    # ~12% of the time.
    rendered = render_pages(MAX_URLS,
                             homepage='https://www.max.com/',
                             cookie_domain='max.com',
                             wait_selectors=_MAX_HYDRATE_SELECTORS,
                             wait_ms=4000,
                             scroll_ms=3000,
                             hydration_wait_ms=12000,
                             use_proxy=True)

    all_items: list[dict] = []
    seen: set[str] = set()
    for label, html in rendered:
        items = _extract_max_dom(html, limit=40)
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            all_items.append(it)
        logger.info("max %s: parsed %d titles from %d-byte HTML",
                     label, len(items), len(html))

    for i, it in enumerate(all_items[:25], start=1):
        it['rank'] = i
    return {'national': all_items[:25]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('max', 'Max', 'streaming', fetch)
    print(f"max: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
