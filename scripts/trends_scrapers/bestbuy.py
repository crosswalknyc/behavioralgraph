"""
Best Buy bestseller scraper.

Source: https://www.bestbuy.com/site/best-sellers/pcmcat243700050001.c

Best Buy renders bestseller listings server-side with a stable
`data-testid="list-item"` grid. Each item carries an SKU, product name,
model number, image, and current price. Fetches the flagship "All" tab
(cross-category top 10) plus category sub-pages so we can populate a
tab strip in the UI.

Standalone:

    python3 -m scripts.trends_scrapers.bestbuy
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

from ._base import browser_headers, http_get, run_scraper
from ._playwright import render_pages

logger = logging.getLogger(__name__)


BESTBUY_CATEGORIES = [
    ('Top Deals',      'https://www.bestbuy.com/site/top-deals'),
    ('Best Selling TVs', 'https://www.bestbuy.com/site/tvs/all-flat-screen-tvs/pcmcat1591114941433.c'),
    ('Best Selling Laptops', 'https://www.bestbuy.com/site/computers-pcs/all-laptops/abcat0502000.c'),
    ('Best Selling Games', 'https://www.bestbuy.com/site/video-games/best-selling-video-games/pcmcat243700050004.c'),
    ('Best Selling Speakers', 'https://www.bestbuy.com/site/audio/wireless-and-bluetooth-speakers/abcat0207004.c'),
]


_TITLE_RE = re.compile(r'"name":"([^"]{2,220})"')
_URL_RE   = re.compile(r'"canonicalUrl":"(\\/site\\/[^"]+)"')
_IMG_RE   = re.compile(r'"images":\[{"[^}]*"url":"([^"]+)"')
_PRICE_RE = re.compile(r'"currentPrice":([0-9]+(?:\.[0-9]+)?)')
_SKU_RE   = re.compile(r'"skuId":"?([0-9]{5,})"?')


def _parse_bestbuy_listing(html: str, limit: int = 10) -> list[dict]:
    """Extract product cards from a Best Buy listing HTML page.

    Best Buy embeds a JSON blob per product tile in a JS bootstrap payload;
    the important fields are canonicalUrl + name + images + currentPrice +
    skuId. We slice around each `"skuId"` occurrence and pull the four
    other fields from the same window."""
    items: list[dict] = []
    seen_skus: set[str] = set()
    for m in _SKU_RE.finditer(html):
        sku = m.group(1)
        if sku in seen_skus:
            continue
        window = html[max(0, m.start() - 4000):m.end() + 4000]
        title_m = _TITLE_RE.search(window)
        url_m   = _URL_RE.search(window)
        img_m   = _IMG_RE.search(window)
        price_m = _PRICE_RE.search(window)
        if not title_m or not url_m:
            continue
        title = title_m.group(1).replace('\\u0026', '&').strip()
        url   = 'https://www.bestbuy.com' + url_m.group(1).replace('\\/', '/')
        image = img_m.group(1).replace('\\/', '/') if img_m else ''
        price = f"${price_m.group(1)}" if price_m else ''
        seen_skus.add(sku)
        items.append({
            'rank':  len(items) + 1,
            'name':  title[:180],
            'url':   url,
            'image': image,
            'price': price,
            'sku':   sku,
        })
        if len(items) >= limit:
            break
    return items


def _fetch_via_requests() -> list[dict]:
    """Best Buy occasionally returns 200 for direct GETs when the JA3
    fingerprint is right (curl_cffi impersonate chrome124). Try that
    first before spinning up the heavier Playwright path."""
    categories: list[dict] = []
    for label, url in BESTBUY_CATEGORIES:
        r = http_get(url, headers=browser_headers(
            referer='https://www.bestbuy.com/'), retries=1)
        if r is None or not r.ok:
            continue
        items = _parse_bestbuy_listing(r.text, limit=10)
        if items:
            categories.append({'label': label, 'items': items})
    return categories


def fetch() -> dict[str, Any]:
    categories = _fetch_via_requests()
    if not categories:
        rendered = render_pages(BESTBUY_CATEGORIES,
                                  homepage='https://www.bestbuy.com/')
        for label, html in rendered:
            items = _parse_bestbuy_listing(html, limit=10)
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
    result = run_scraper('bestbuy', 'Best Buy', 'retailer', fetch)
    print(f"bestbuy: national={len(result.get('national', []))} "
           f"categories={len(result.get('categories', []))} "
           f"error={result.get('error')}", file=sys.stderr)
