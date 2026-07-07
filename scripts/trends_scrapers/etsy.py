"""
Etsy trending scraper.

Etsy publishes a "Trending Now" board at:

    https://www.etsy.com/market/popular_right_now

and category-specific trending pages. The listing tiles are anchor
elements with `class="listing-link"` wrapping an image + title + price.

Standalone:

    python3 -m scripts.trends_scrapers.etsy
"""

from __future__ import annotations

import logging
import re
import sys
from html import unescape
from typing import Any

from ._base import browser_headers, http_get, run_scraper
from ._playwright import render_pages

logger = logging.getLogger(__name__)


ETSY_URLS = [
    ('Popular Right Now', 'https://www.etsy.com/market/popular_right_now'),
    ('Bestsellers',       'https://www.etsy.com/market/bestsellers'),
    ('Trending Gifts',    'https://www.etsy.com/market/trending_gifts'),
]


_LISTING_RE = re.compile(
    r'<a\s+class="[^"]*listing-link[^"]*"\s+href="(https://www\.etsy\.com/listing/[^"]+)"'
    r'([\s\S]{0,4000}?)</a>',
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r'title="([^"]{5,240})"', re.IGNORECASE)
_IMG_RE   = re.compile(r'src="(https://i\.etsystatic\.com/[^"]+)"', re.IGNORECASE)
_PRICE_RE = re.compile(
    r'<span[^>]*class="[^"]*currency-value[^"]*"[^>]*>\$?([0-9,.]+)</span>',
    re.IGNORECASE,
)


def _parse_etsy_listing(html: str, limit: int = 10) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for m in _LISTING_RE.finditer(html):
        url = m.group(1).split('?')[0]
        chunk = m.group(2)
        listing_id_match = re.search(r'/listing/([0-9]+)/', url)
        if not listing_id_match:
            continue
        listing_id = listing_id_match.group(1)
        if listing_id in seen:
            continue
        title_m = _TITLE_RE.search(chunk)
        img_m   = _IMG_RE.search(chunk)
        price_m = _PRICE_RE.search(chunk)
        if not title_m:
            continue
        seen.add(listing_id)
        out.append({
            'rank':       len(out) + 1,
            'name':       unescape(title_m.group(1)).strip()[:180],
            'url':        url,
            'image':      img_m.group(1) if img_m else '',
            'price':      f"${price_m.group(1)}" if price_m else '',
            'listing_id': listing_id,
        })
        if len(out) >= limit:
            break
    return out


def _fetch_via_requests() -> list[dict]:
    categories: list[dict] = []
    for label, url in ETSY_URLS:
        r = http_get(url, headers=browser_headers(
            referer='https://www.etsy.com/'), retries=1)
        if r is None or not r.ok:
            continue
        items = _parse_etsy_listing(r.text, limit=10)
        if items:
            categories.append({'label': label, 'items': items})
    return categories


def fetch() -> dict[str, Any]:
    categories = _fetch_via_requests()
    if not categories:
        rendered = render_pages(ETSY_URLS,
                                  homepage='https://www.etsy.com/')
        for label, html in rendered:
            items = _parse_etsy_listing(html, limit=10)
            if items:
                categories.append({'label': label, 'items': items})
    national = categories[0]['items'] if categories else []
    return {
        'national':   national,
        'categories': categories,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('etsy', 'Etsy', 'retailer', fetch)
    print(f"etsy: national={len(result.get('national', []))} "
           f"categories={len(result.get('categories', []))} "
           f"error={result.get('error')}", file=sys.stderr)
