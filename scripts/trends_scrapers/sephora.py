"""
Sephora bestsellers scraper.

Source: https://www.sephora.com/shop/best-sellers

Sephora renders their bestsellers grid with a `__NEXT_DATA__` bootstrap
that embeds the product list. If Sephora blocks the plain-UA fetch we'll
fall back to Playwright in a later revision (per Jenna's decision on the
follow-up card).

Standalone:

    python3 -m scripts.trends_scrapers.sephora
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from ._base import browser_headers, http_get, run_scraper
from ._playwright import render_pages

logger = logging.getLogger(__name__)


SEPHORA_URLS = [
    ('Best Sellers',          'https://www.sephora.com/beauty/bestsellers'),
    ('New at Sephora',        'https://www.sephora.com/beauty/new-beauty-products'),
    ('Trending Now',          'https://www.sephora.com/beauty/trending-products'),
    ('Skincare Bestsellers',  'https://www.sephora.com/shop/skincare-best-sellers'),
    ('Makeup Bestsellers',    'https://www.sephora.com/shop/makeup-best-sellers'),
]


_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>',
    re.DOTALL,
)


def _walk_products(node, seen: set[str], out: list[dict], limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(node, dict):
        pid = node.get('productId') or node.get('currentSku') or node.get('skuId')
        title = (node.get('displayName') or node.get('productName')
                  or node.get('name'))
        brand = node.get('brandName') or ''
        if isinstance(brand, dict):
            brand = brand.get('displayName') or ''
        url = node.get('targetUrl') or node.get('productUrl') or node.get('pdpUrl')
        price = (node.get('listPrice') or node.get('salePrice')
                  or node.get('currentSku', {}).get('listPrice') if isinstance(node.get('currentSku'), dict) else None
                  or node.get('price'))
        image = ''
        heroes = node.get('heroImage') or node.get('image')
        if isinstance(heroes, str):
            image = heroes
        elif isinstance(heroes, dict):
            image = (heroes.get('imageUrl') or heroes.get('url')
                     or heroes.get('src') or '')
        imgs = node.get('images')
        if not image and isinstance(imgs, list) and imgs:
            first = imgs[0]
            image = first if isinstance(first, str) else (first.get('imageUrl', '')
                    if isinstance(first, dict) else '')
        if pid and title and url:
            pid_s = str(pid)
            if pid_s not in seen:
                seen.add(pid_s)
                full_url = str(url)
                if full_url.startswith('/'):
                    full_url = 'https://www.sephora.com' + full_url
                if image and image.startswith('/'):
                    image = 'https://www.sephora.com' + image
                display_name = f"{brand} {title}".strip() if brand else str(title)
                out.append({
                    'rank':  len(out) + 1,
                    'name':  display_name[:180],
                    'url':   full_url,
                    'image': image,
                    'price': f"${price}" if price and str(price).replace('.', '').isdigit() else str(price or ''),
                    'brand': brand or None,
                })
                if len(out) >= limit:
                    return
        for v in node.values():
            _walk_products(v, seen, out, limit)
    elif isinstance(node, list):
        for v in node:
            _walk_products(v, seen, out, limit)


def _extract_from_next_data(html: str, limit: int) -> list[dict]:
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


def _fetch_via_requests() -> list[dict]:
    categories: list[dict] = []
    for label, url in SEPHORA_URLS:
        r = http_get(url, headers=browser_headers(
            referer='https://www.sephora.com/'), retries=1)
        if r is None or not r.ok:
            continue
        items = _extract_from_next_data(r.text, limit=10)
        if items:
            categories.append({'label': label, 'items': items})
    return categories


def fetch() -> dict[str, Any]:
    categories = _fetch_via_requests()
    if not categories:
        rendered = render_pages(SEPHORA_URLS,
                                  homepage='https://www.sephora.com/')
        for label, html in rendered:
            items = _extract_from_next_data(html, limit=10)
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
    result = run_scraper('sephora', 'Sephora', 'retailer', fetch)
    print(f"sephora: national={len(result.get('national', []))} "
           f"categories={len(result.get('categories', []))} "
           f"error={result.get('error')}", file=sys.stderr)
