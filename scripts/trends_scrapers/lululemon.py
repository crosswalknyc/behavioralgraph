"""
Lululemon best-sellers scraper.

Source: https://shop.lululemon.com/c/whats-new-whats-selling/_/N-1z0y74w

Lululemon renders their "What's Selling" grid server-side with a JSON
`__NEXT_DATA__` bootstrap payload. Product tiles carry name, primary
image, price, and pdpUrl.

Standalone:

    python3 -m scripts.trends_scrapers.lululemon
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


LULU_URLS = [
    ('Whats Selling', 'https://shop.lululemon.com/c/whats-new-whats-selling/_/N-1z0y74w'),
    ('Womens',        'https://shop.lululemon.com/c/womens-whats-new/_/N-8t6'),
    ('Mens',          'https://shop.lululemon.com/c/mens-whats-new/_/N-8t4'),
]


_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(\{.*?\})</script>',
    re.DOTALL,
)


def _walk_products(node, seen_ids: set[str], out: list[dict], limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(node, dict):
        pid = node.get('productId') or node.get('styleNumber') or node.get('id')
        title = (node.get('productName') or node.get('displayName')
                  or node.get('name') or node.get('title'))
        url = node.get('pdpUrl') or node.get('canonicalUrl') or node.get('url')
        price = (node.get('listPrice') or node.get('displayPrice')
                  or node.get('price'))
        image = ''
        imgs = node.get('altImages') or node.get('images') or node.get('imageUrls')
        if isinstance(imgs, list) and imgs:
            first = imgs[0]
            image = first if isinstance(first, str) else (first.get('url', '')
                    if isinstance(first, dict) else '')
        elif isinstance(imgs, dict):
            image = imgs.get('primary') or imgs.get('url') or ''
        if pid and title and url:
            pid_s = str(pid)
            if pid_s not in seen_ids:
                seen_ids.add(pid_s)
                full_url = str(url)
                if full_url.startswith('/'):
                    full_url = 'https://shop.lululemon.com' + full_url
                out.append({
                    'rank':  len(out) + 1,
                    'name':  str(title)[:180],
                    'url':   full_url,
                    'image': image,
                    'price': f"${price}" if price and str(price).replace('.', '').isdigit() else str(price or ''),
                })
                if len(out) >= limit:
                    return
        for v in node.values():
            _walk_products(v, seen_ids, out, limit)
    elif isinstance(node, list):
        for v in node:
            _walk_products(v, seen_ids, out, limit)


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
    for label, url in LULU_URLS:
        r = http_get(url, headers=browser_headers(
            referer='https://shop.lululemon.com/'), retries=1)
        if r is None or not r.ok:
            continue
        items = _extract_from_next_data(r.text, limit=10)
        if items:
            categories.append({'label': label, 'items': items})
    return categories


def fetch() -> dict[str, Any]:
    categories = _fetch_via_requests()
    if not categories:
        rendered = render_pages(LULU_URLS,
                                  homepage='https://shop.lululemon.com/')
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
    result = run_scraper('lululemon', 'Lululemon', 'retailer', fetch)
    print(f"lululemon: national={len(result.get('national', []))} "
           f"categories={len(result.get('categories', []))} "
           f"error={result.get('error')}", file=sys.stderr)
