"""
Amazon Prime Video trending scraper.

Requires donated cookies for `amazon.com` (same session as the Amazon
shopping site - Prime Video's storefront lives on amazon.com and
inherits the parent Amazon session).

Donate via:
    python3 scripts/trends_scrapers/donate_cookies.py --domain amazon.com

Standalone:
    python3 -m scripts.trends_scrapers.primevideo
"""

from __future__ import annotations

import logging
import re
import sys
from html import unescape
from typing import Any

from ._base import run_scraper
from ._playwright import render_pages
from ._streaming_common import parse_streaming_html

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


# Prime Video renders title cards with `data-card-title` on the anchor.
# When the JSON-blob path in _streaming_common comes up empty (Amazon
# ships less structured `state` than Disney), this pure-DOM regex is a
# reliable fallback.
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
                             hydration_wait_ms=10000)

    all_items: list[dict] = []
    seen: set[str] = set()
    for label, html in rendered:
        items = parse_streaming_html(html, host='www.amazon.com', limit=25)
        if not items:
            items = _extract_from_dom(html, limit=25)
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            all_items.append(it)
        logger.info("primevideo %s: parsed %d titles from %d-byte HTML",
                     label, len(items), len(html))

    for i, it in enumerate(all_items[:20], start=1):
        it['rank'] = i
    return {'national': all_items[:20]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('primevideo', 'Prime Video', 'streaming', fetch)
    print(f"primevideo: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
