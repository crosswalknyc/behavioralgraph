"""
Target bestsellers scraper (Playwright).

Target's bestseller pages are behind Akamai's bot management. Plain
`requests` gets a 403 challenge page 100% of the time; a headless browser
with a fresh session gets through. We use Playwright (Chromium) launched
in stealth mode.

Setup on Hetzner (one-time):

    apt-get install -y libnss3 libatk-bridge2.0-0 libcups2 libdrm2 \\
        libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \\
        libgbm1 libpango-1.0-0 libcairo2 libasound2
    pip3 install --user playwright playwright-stealth
    python3 -m playwright install chromium

Standalone:

    python3 -m scripts.trends_scrapers.target
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


TARGET_URLS = [
    ('Top Selling',    'https://www.target.com/c/top-deals/-/N-4xu13'),
    ('Trending',       'https://www.target.com/c/what-s-new/-/N-t80ha'),
    ('Bestsellers',    'https://www.target.com/c/target-bullseye-shop/-/N-8gvfl'),
]


def _extract_from_html(html: str, limit: int = 10) -> list[dict]:
    """Target embeds a Redux __TGT_DATA__ blob in every listing page."""
    m = re.search(r'window\.__TGT_DATA__\s*=\s*({.+?})\s*;\s*</script>',
                   html, re.DOTALL)
    if not m:
        m = re.search(r'"products":\s*(\[[^\]]{500,50000}\])',
                       html, re.DOTALL)
        if not m:
            return []
        try:
            arr = json.loads(m.group(1))
        except json.JSONDecodeError:
            return []
        return _from_products_array(arr, limit)
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    _walk(data, seen, out, limit)
    return out


def _from_products_array(arr: list, limit: int) -> list[dict]:
    out: list[dict] = []
    for i, p in enumerate(arr[:limit]):
        if not isinstance(p, dict):
            continue
        tcin = p.get('tcin') or p.get('id') or ''
        title = (p.get('title') or p.get('name')
                  or p.get('productDescription', {}).get('title') if isinstance(p.get('productDescription'), dict) else '')
        url = p.get('url') or p.get('pdpUrl') or ''
        image = p.get('image') or p.get('primary_image_url') or ''
        price = p.get('price') or {}
        price_str = ''
        if isinstance(price, dict):
            price_str = price.get('formatted_current_price') or price.get('current_retail') or ''
        if not (tcin and title and url):
            continue
        if url.startswith('/'):
            url = 'https://www.target.com' + url
        out.append({
            'rank':  i + 1,
            'name':  str(title)[:180],
            'url':   url,
            'image': image if isinstance(image, str) else '',
            'price': f"${price_str}" if price_str and not str(price_str).startswith('$') else str(price_str or ''),
            'tcin':  str(tcin),
        })
    return out


def _walk(node, seen: set[str], out: list[dict], limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(node, dict):
        tcin = node.get('tcin') or node.get('productId')
        title = (node.get('title') or node.get('description')
                  or node.get('name'))
        url = node.get('url') or node.get('pdpUrl')
        image = (node.get('primary_image_url') or node.get('image')
                  or node.get('imageUrl'))
        price_obj = node.get('price')
        price_str = ''
        if isinstance(price_obj, dict):
            price_str = (price_obj.get('formatted_current_price')
                          or price_obj.get('current_retail')
                          or price_obj.get('reg_retail') or '')
        elif isinstance(price_obj, (int, float, str)):
            price_str = str(price_obj)
        if tcin and title and url:
            key = str(tcin)
            if key not in seen:
                seen.add(key)
                full = str(url)
                if full.startswith('/'):
                    full = 'https://www.target.com' + full
                out.append({
                    'rank':  len(out) + 1,
                    'name':  str(title)[:180],
                    'url':   full,
                    'image': image if isinstance(image, str) else '',
                    'price': f"${price_str}" if price_str and not str(price_str).startswith('$') else str(price_str or ''),
                    'tcin':  key,
                })
                if len(out) >= limit:
                    return
        for v in node.values():
            _walk(v, seen, out, limit)
    elif isinstance(node, list):
        for v in node:
            _walk(v, seen, out, limit)


def fetch() -> dict[str, Any]:
    rendered = render_pages(TARGET_URLS, homepage='https://www.target.com/')
    categories: list[dict] = []
    for label, html in rendered:
        items = _extract_from_html(html, limit=10)
        if items:
            categories.append({'label': label, 'items': items})
        else:
            logger.info("target %s: playwright rendered %d bytes but 0 items parsed",
                         label, len(html))
    national = categories[0]['items'] if categories else []
    return {
        'national':   national,
        'categories': categories,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('target', 'Target', 'retailer', fetch)
    print(f"target: national={len(result.get('national', []))} "
           f"categories={len(result.get('categories', []))} "
           f"error={result.get('error')}", file=sys.stderr)
