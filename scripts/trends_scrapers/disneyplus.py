"""
Disney+ trending scraper.

Requires donated cookies for `disneyplus.com`. Without cookies the
homepage returns a marketing shell with no title data. Donate via:

    python3 scripts/trends_scrapers/donate_cookies.py --domain disneyplus.com

A Disney+ session also unlocks ESPN+ (bundle plan). ESPN+ rankings are
scraped separately (see espnplus.py) but reuse the same cookie set.

Standalone:
    python3 -m scripts.trends_scrapers.disneyplus
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from ._base import run_scraper
from ._playwright import render_pages
from ._streaming_common import parse_streaming_html

logger = logging.getLogger(__name__)


DISNEY_URLS = [
    ('Home',      'https://www.disneyplus.com/home'),
    ('New',       'https://www.disneyplus.com/browse/new-releases'),
    ('Trending',  'https://www.disneyplus.com/browse/trending'),
]


# Disney+ homepage hydrates its collection rails via GraphQL after the
# initial paint. These selectors are what appears once the rails render.
_DISNEY_HYDRATE_SELECTORS = [
    'div[data-testid="set-container"]',
    'div[data-testid="hero-container"]',
    'a[data-gv2elementkey]',
    'div[data-gv2elementvalue]',
]


def fetch() -> dict[str, Any]:
    rendered = render_pages(DISNEY_URLS,
                             homepage='https://www.disneyplus.com/',
                             cookie_domain='disneyplus.com',
                             wait_selectors=_DISNEY_HYDRATE_SELECTORS,
                             hydration_wait_ms=10000)

    all_items: list[dict] = []
    seen: set[str] = set()
    for label, html in rendered:
        items = parse_streaming_html(html, host='www.disneyplus.com', limit=25)
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            all_items.append(it)
        logger.info("disneyplus %s: parsed %d titles from %d-byte HTML",
                     label, len(items), len(html))

    # Re-rank in insertion order
    for i, it in enumerate(all_items[:20], start=1):
        it['rank'] = i
    return {'national': all_items[:20]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('disneyplus', 'Disney+', 'streaming', fetch)
    print(f"disneyplus: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
