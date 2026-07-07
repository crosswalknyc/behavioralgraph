"""
Hulu trending scraper.

Requires donated cookies for `hulu.com`. Donate via:

    python3 scripts/trends_scrapers/donate_cookies.py --domain hulu.com

A Hulu session on the Disney+/Hulu/Max bundle plan also carries the
Max entitlement, but Max serves its own domain, so `max.com` cookies
are donated separately.

Standalone:
    python3 -m scripts.trends_scrapers.hulu
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from ._base import run_scraper
from ._playwright import render_pages
from ._streaming_common import parse_streaming_html

logger = logging.getLogger(__name__)


HULU_URLS = [
    ('Home',        'https://www.hulu.com/hub/home'),
    ('TV',          'https://www.hulu.com/hub/tv'),
    ('Movies',      'https://www.hulu.com/hub/movies'),
    ('News',        'https://www.hulu.com/hub/news'),
]


# Hulu's hub pages hydrate title carousels via `__PRELOADED_STORE__`
# and then paint DOM cards with `data-automationid` markers.
_HULU_HYDRATE_SELECTORS = [
    'a[data-automationid="tile"]',
    'div[data-automationid="collection"]',
    'div[data-testid="collection"]',
    'a[href*="/watch/"]',
]


def fetch() -> dict[str, Any]:
    rendered = render_pages(HULU_URLS,
                             homepage='https://www.hulu.com/',
                             cookie_domain='hulu.com',
                             wait_selectors=_HULU_HYDRATE_SELECTORS,
                             hydration_wait_ms=10000)

    all_items: list[dict] = []
    seen: set[str] = set()
    for label, html in rendered:
        items = parse_streaming_html(html, host='www.hulu.com', limit=25)
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            all_items.append(it)
        logger.info("hulu %s: parsed %d titles from %d-byte HTML",
                     label, len(items), len(html))

    for i, it in enumerate(all_items[:20], start=1):
        it['rank'] = i
    return {'national': all_items[:20]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('hulu', 'Hulu', 'streaming', fetch)
    print(f"hulu: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
