"""
Max (formerly HBO Max) trending scraper.

Requires donated cookies for `max.com`. Donate via:

    python3 scripts/trends_scrapers/donate_cookies.py --domain max.com

Note: this module is named `max_streaming.py` (not `max.py`) because
`max` shadows Python's builtin `max()` and shows up first in the
package's namespace at import time. The scraper registry in
`run_all.py` uses source key `max`.

Standalone:
    python3 -m scripts.trends_scrapers.max_streaming
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from ._base import run_scraper
from ._playwright import render_pages
from ._streaming_common import parse_streaming_html

logger = logging.getLogger(__name__)


MAX_URLS = [
    ('Home',      'https://play.max.com/'),
    ('Series',    'https://play.max.com/pages/series'),
    ('Movies',    'https://play.max.com/pages/movies'),
    ('Trending',  'https://play.max.com/pages/trending'),
]


# Max ships its rails as __NEXT_DATA__ once hydrated. These selectors
# indicate the shell finished replacing the marketing landing.
_MAX_HYDRATE_SELECTORS = [
    'a[href*="/show/"]',
    'a[href*="/movie/"]',
    'div[data-testid="collection"]',
    'article[data-testid="tile"]',
]


def fetch() -> dict[str, Any]:
    rendered = render_pages(MAX_URLS,
                             homepage='https://www.max.com/',
                             cookie_domain='max.com',
                             wait_selectors=_MAX_HYDRATE_SELECTORS,
                             hydration_wait_ms=10000)

    all_items: list[dict] = []
    seen: set[str] = set()
    for label, html in rendered:
        items = parse_streaming_html(html, host='play.max.com', limit=25)
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            all_items.append(it)
        logger.info("max %s: parsed %d titles from %d-byte HTML",
                     label, len(items), len(html))

    for i, it in enumerate(all_items[:20], start=1):
        it['rank'] = i
    return {'national': all_items[:20]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('max', 'Max', 'streaming', fetch)
    print(f"max: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
