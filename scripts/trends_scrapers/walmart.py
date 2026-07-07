"""
Walmart bestsellers scraper (Playwright).

Walmart uses PerimeterX (now HUMAN Security) bot management on every
listing page. Plain `requests` gets a JS challenge; Playwright with a
warm cookie-jar and a slow scroll gets through. Same setup as target.py.

Standalone:

    python3 -m scripts.trends_scrapers.walmart
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from ._base import run_scraper
from ._playwright import render_pages

logger = logging.getLogger(__name__)


WALMART_URLS = [
    ('Best Sellers',    'https://www.walmart.com/shop/deals/bestsellers'),
    ('Trending Now',    'https://www.walmart.com/browse/premium-beauty/1005862'),
    ('Home Bestsellers', 'https://www.walmart.com/cp/best-sellers-home/1231164'),
]


_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>',
    re.DOTALL,
)


def _walk_products(node, seen: set[str], out: list[dict], limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(node, dict):
        # Walmart's item type is "Product"; ID lives at usItemId.
        pid = (node.get('usItemId') or node.get('itemId')
                or node.get('offerId') or node.get('productId'))
        title = node.get('name') or node.get('title')
        url = (node.get('canonicalUrl') or node.get('productUrl')
                or node.get('canonical_url'))
        image = ''
        img_obj = node.get('imageInfo') or node.get('image')
        if isinstance(img_obj, dict):
            image = img_obj.get('thumbnailUrl') or img_obj.get('url', '')
        elif isinstance(img_obj, str):
            image = img_obj
        images = node.get('images')
        if not image and isinstance(images, list) and images:
            first = images[0]
            image = first if isinstance(first, str) else (first.get('url', '')
                    if isinstance(first, dict) else '')
        price_info = node.get('priceInfo') or node.get('price') or {}
        price_str = ''
        if isinstance(price_info, dict):
            cur = price_info.get('currentPrice') or price_info.get('linePrice') or {}
            if isinstance(cur, dict):
                price_str = cur.get('priceString') or ''
            elif isinstance(cur, (int, float, str)):
                price_str = f"${cur}"
        elif isinstance(price_info, (int, float, str)):
            price_str = f"${price_info}"
        if pid and title and url:
            key = str(pid)
            if key not in seen:
                seen.add(key)
                full = str(url)
                if full.startswith('/'):
                    full = 'https://www.walmart.com' + full
                out.append({
                    'rank':      len(out) + 1,
                    'name':      str(title)[:180],
                    'url':       full,
                    'image':     image if isinstance(image, str) else '',
                    'price':     price_str,
                    'item_id':   key,
                })
                if len(out) >= limit:
                    return
        for v in node.values():
            _walk_products(v, seen, out, limit)
    elif isinstance(node, list):
        for v in node:
            _walk_products(v, seen, out, limit)


def _extract_from_html(html: str, limit: int = 10) -> list[dict]:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    _walk_products(data, seen, out, limit)
    return out


def fetch() -> dict[str, Any]:
    rendered = render_pages(WALMART_URLS,
                              homepage='https://www.walmart.com/',
                              cookie_domain='walmart.com',
                              wait_ms=4000, scroll_ms=2000)
    categories: list[dict] = []
    for label, html in rendered:
        items = _extract_from_html(html, limit=10)
        if items:
            categories.append({'label': label, 'items': items})
        else:
            logger.info("walmart %s: playwright rendered %d bytes but 0 items parsed",
                         label, len(html))
    national = categories[0]['items'] if categories else []
    return {
        'national':   national,
        'categories': categories,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('walmart', 'Walmart', 'retailer', fetch)
    print(f"walmart: national={len(result.get('national', []))} "
           f"categories={len(result.get('categories', []))} "
           f"error={result.get('error')}", file=sys.stderr)
