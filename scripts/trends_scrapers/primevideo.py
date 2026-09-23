"""
Amazon Prime Video trending scraper.

Requires donated cookies for `amazon.com` (same session as the Amazon
shopping site - Prime Video's storefront lives on amazon.com and
inherits the parent Amazon session).

Donate via:
    python3 scripts/trends_scrapers/donate_cookies.py --domain amazon.com

Standalone:
    python3 -m scripts.trends_scrapers.primevideo

Parser strategy
---------------
Prime Video ships hydrated content in

    <script id="dv-web-page-hydration-data" type="application/json">

with structure:

    init:
      preparations:
        body:
          containers: list[
            { title: "Popular now" | "Featured Originals ..." | ...
              entities: list[
                { displayTitle: "Every Year After",
                  entityType:   "TV Show" | "Movie",
                  link:         { url: "/gp/video/detail/B0GZ.../" },
                  titleID:      "B0GZ7FKMRR",
                  releaseYear:  "2026",
                  ...
                }
              ]
            }
          ]

Sampled 2026-07-07: 7 containers x ~20 entities each on the storefront,
so a full pull yields ~120 titles per page. We dedupe by displayTitle
across containers and rank in container order (Continue Watching,
Featured Originals, Popular Now, then genre rails), matching how
Amazon surfaces them.

Prime Video's own chart
-----------------------
One of those containers is the real thing: 'Top 10 TV shows in the US'
and 'Top 10 movies in the US' are Amazon's own published rankings, and
they arrive in the hydration blob in chart order like any other rail.
Until 2026-09-23 they were treated as just another container and then
lost, because the pull kept the first 20 deduped titles across four
pages and the storefront's Hero Carousel got there first. What shipped
as the Prime Video rail was therefore marketing placement: Neagley,
Elle, You+Me, four hero slots and a row of Featured Originals.

They now come first, in the order Amazon gives them, and
`_PUBLISHED_CHARTS` in stream_estimates reads them off the collection
name so those titles hold those positions on the board.

Which Top 10 rails hydrate varies by run: TV is reliable on
/gp/video/tv, movies appears on /gp/video/movies some runs and on the
storefront others. Every page is scanned and whatever charted rails
came back are used, so a run that only sees one still gets that one
right rather than falling back to promotional order for both.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from html import unescape
from typing import Any

from ._base import run_scraper
from ._playwright import render_pages

logger = logging.getLogger(__name__)


PRIME_URLS = [
    ('Storefront',  'https://www.amazon.com/gp/video/storefront'),
    ('Explore',     'https://www.amazon.com/gp/video/explore'),
    ('TV',          'https://www.amazon.com/gp/video/tv'),
    ('Movies',      'https://www.amazon.com/gp/video/movies'),
]


_PRIME_HYDRATE_SELECTORS = [
    'article[data-testid="card"]',
    'a[data-testid="card-title"]',
    'div[data-testid="carousel"]',
    'div[data-automation-id="hero-title"]',
]


_HYDRATION_BLOB_RE = re.compile(
    r'<script[^>]+id="dv-web-page-hydration-data"[^>]*>(.+?)</script>',
    re.DOTALL | re.IGNORECASE,
)


# Amazon's own published rankings, as the container is titled in the
# hydration blob. Matched loosely because the wording varies by page
# and by locale ('Top 10 in the US', 'Top 10 movies in the US', 'Top
# 10 TV shows in the US'), but anchored on 'top 10' plus a US marker
# so a 'Top 10 for you' personalised rail never qualifies.
_TOP10_RE = re.compile(r'\btop\s*10\b.*\bin\s+the\s+u\.?s\.?\b', re.I)


def _is_published_chart_rail(rail: str) -> bool:
    return bool(_TOP10_RE.search(rail or ''))


def _classify_entity(entity_type: str) -> str:
    """Prime uses 'TV Show', 'Movie', 'Live Event', 'Miniseries', etc."""
    et = (entity_type or '').lower()
    if 'movie' in et or 'film' in et:
        return 'Film'
    if 'tv' in et or 'series' in et or 'show' in et or 'episode' in et:
        return 'TV'
    if 'live' in et or 'event' in et:
        return 'Live'
    return ''


def _extract_prime_hydration(html: str) -> list[dict]:
    """Parse the dv-web-page-hydration-data blob and pull out real titles.
    Returns [] if the blob is missing (unauthenticated marketing shell)
    or malformed. Rails are traversed in on-screen order; Continue
    Watching is intentionally excluded since it's a per-user list, not
    trending.
    """
    m = _HYDRATION_BLOB_RE.search(html)
    if not m:
        return []
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    try:
        containers = obj['init']['preparations']['body']['containers']
    except (KeyError, TypeError):
        return []
    if not isinstance(containers, list):
        return []

    # Amazon's own Top 10 rails are read before anything else, so a
    # charted title is never dropped as a duplicate of the same title
    # sitting in a promotional rail further up the page.
    def _rail_name(c):
        rail = c.get('title') or c.get('text') or ''
        if isinstance(rail, dict):
            rail = rail.get('text') or rail.get('displayText') or ''
        return str(rail).strip()

    ordered = (
        [c for c in containers
         if isinstance(c, dict) and _is_published_chart_rail(_rail_name(c))]
        + [c for c in containers
           if isinstance(c, dict)
           and not _is_published_chart_rail(_rail_name(c))])

    seen: set[str] = set()
    out: list[dict] = []
    for c in ordered:
        if not isinstance(c, dict):
            continue
        rail = _rail_name(c)
        if c.get('isContinueWatching'):
            continue
        entities = c.get('entities') or []
        if not isinstance(entities, list):
            continue
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            title = ent.get('displayTitle')
            if not isinstance(title, str):
                title = ent.get('title')
            if not isinstance(title, str):
                continue
            title = title.strip()
            if len(title) < 2 or len(title) > 220:
                continue
            key = title.lower()
            if key in seen:
                continue
            link = ent.get('link') or {}
            url_path = ''
            if isinstance(link, dict):
                url_path = link.get('url') or ''
            if not isinstance(url_path, str):
                url_path = ''
            if url_path.startswith('/'):
                url = f'https://www.amazon.com{url_path.split("?")[0]}'
            elif url_path.startswith('http'):
                url = url_path.split('?')[0]
            elif ent.get('titleID'):
                url = f'https://www.amazon.com/gp/video/detail/{ent["titleID"]}'
            else:
                continue

            seen.add(key)
            out.append({
                'rank':             len(out) + 1,
                'title':            title,
                'url':              url,
                'category_display': _classify_entity(ent.get('entityType') or ''),
                'collection':       rail,
            })
    return out


# Pure-DOM fallback in case Amazon ships a different hydration shape one
# day. Matches <a href="/gp/video/detail/..." aria-label="Show name">.
_PRIME_CARD_RE = re.compile(
    r'<a[^>]+href="(/gp/video/detail/[A-Z0-9]+/[^"]+)"[^>]*'
    r'aria-label="([^"]{2,180})"',
    re.IGNORECASE,
)


def _extract_from_dom(html: str, limit: int = 20) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for m in _PRIME_CARD_RE.finditer(html):
        href  = m.group(1)
        title = unescape(m.group(2)).strip()
        key = title.lower()
        if key in seen or len(title) < 3:
            continue
        seen.add(key)
        url = f'https://www.amazon.com{href.split("?")[0]}'
        out.append({
            'rank':             len(out) + 1,
            'title':            title,
            'url':              url,
            'category_display': '',
            'collection':       '',
        })
        if len(out) >= limit:
            break
    return out


def fetch() -> dict[str, Any]:
    rendered = render_pages(PRIME_URLS,
                             homepage='https://www.amazon.com/',
                             cookie_domain='amazon.com',
                             wait_selectors=_PRIME_HYDRATE_SELECTORS,
                             hydration_wait_ms=10000,
                             assert_signed_in='amazon.com')

    charted: list[dict] = []
    rest: list[dict] = []
    seen: set[str] = set()
    for label, html in rendered:
        items = _extract_prime_hydration(html)
        if not items:
            items = _extract_from_dom(html, limit=25)
        n_chart = 0
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            if _is_published_chart_rail(it['collection']):
                charted.append(it)
                n_chart += 1
            else:
                rest.append(it)
        logger.info("primevideo %s: parsed %d titles from %d-byte HTML "
                     "(%d on a published Top 10 rail)",
                     label, len(items), len(html), n_chart)

    if not charted:
        # Not a failure: Amazon does not hydrate the Top 10 rails on
        # every run. The pull still ships, and the collector treats
        # every row as an unranked listing rather than inventing an
        # order, which is the honest reading of a page with no chart
        # on it.
        logger.warning("primevideo: no Top 10 rail hydrated this run; "
                        "shipping the storefront with no chart")

    # 60 rather than 20. The two Top 10 rails alone are 20 rows, and
    # cutting at 20 was what buried the chart under the Hero Carousel
    # in the first place.
    all_items = charted + rest
    for i, it in enumerate(all_items[:60], start=1):
        it['rank'] = i
    logger.info("primevideo: %d titles, %d of them on a published "
                 "Top 10 rail", min(len(all_items), 60), len(charted))
    return {'national': all_items[:60]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('primevideo', 'Prime Video', 'streaming', fetch)
    print(f"primevideo: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
