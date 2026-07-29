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

from ._base import run_scraper
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
#   1. Path segment: `series/<uuid>` or `movie/<uuid>`
#   2. Aria-label text (which almost always ends "..., Item N of many"
#      or ", Season N" - we normalize both).
_HULU_TILE_RE = re.compile(
    r'<a[^>]+href="(/(?:series|movie)/[a-f0-9\-]+)"[^>]*'
    r'aria-label="([^"]{3,200})"',
    re.IGNORECASE,
)
_HULU_TAIL_ITEM_RE   = re.compile(r',\s*Item\s+\d+\s+of\s+many\s*$', re.IGNORECASE)
_HULU_TAIL_SEASON_RE = re.compile(r',\s*Season\s+\d+\s*$',           re.IGNORECASE)


def _clean_title(raw: str) -> str:
    """Strip Hulu's screen-reader tail decorations from the aria-label."""
    s = unescape(raw).strip()
    s = _HULU_TAIL_ITEM_RE.sub('', s)
    s = _HULU_TAIL_SEASON_RE.sub('', s)
    return s.strip()


def _classify_from_path(path: str) -> str:
    p = path.lower()
    if p.startswith('/movie/'):
        return 'Film'
    if p.startswith('/series/'):
        return 'TV'
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

    # Interleave so both categories are in the top 20. Cap around 60/40
    # TV/Film mix which mirrors Hulu's actual consumption pattern (Hulu
    # is TV-first) while guaranteeing at least ~8 films visible when
    # the Movies rail returns data.
    all_items: list[dict] = []
    fi = ti = 0
    while (fi < len(films) or ti < len(tv)) and len(all_items) < 20:
        # 3 TV : 2 Film ratio per round -> ~8 films in top 20 when
        # both rails are healthy.
        for _ in range(3):
            if ti < len(tv) and len(all_items) < 20:
                all_items.append(tv[ti]); ti += 1
        for _ in range(2):
            if fi < len(films) and len(all_items) < 20:
                all_items.append(films[fi]); fi += 1

    for i, it in enumerate(all_items[:20], start=1):
        it['rank'] = i
    return {'national': all_items[:20]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('hulu', 'Hulu', 'streaming', fetch)
    print(f"hulu: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
