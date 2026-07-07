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
from html import unescape
from typing import Any

from ._base import run_scraper
from ._playwright import render_pages

logger = logging.getLogger(__name__)


# Target rotates N-codes for its landing pages roughly quarterly, so
# when these URLs 404 the fix is to grab fresh ones from the homepage's
# nav (curl target.com and look for /c/*-/N-* hrefs). Last refreshed
# 2026-07-07.
TARGET_URLS = [
    ('Top Deals',        'https://www.target.com/c/top-deals/-/N-4xw74'),
    ('Whats New',        'https://www.target.com/c/what-s-new/-/N-o9rnh'),
    ('Back to School',   'https://www.target.com/c/back-to-school-top-100/-/N-jf6ea'),
]


# Product cards on Target's PLP hydrate client-side; the SSR HTML ships
# an empty React shell. These selectors are what appears once the
# `redsky_aggregations/plp_search_v2` call returns and React renders.
_TARGET_HYDRATE_SELECTORS = [
    'div[data-test="@web/ProductCard/ProductCardVariantDefault"]',
    'div[data-test="@web/site-top-of-funnel/ProductCardWrapper"]',
    'a[data-test="product-title"]',
    '[data-test="itemTitleTextLink"]',
]


_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.+?)</script>',
    re.DOTALL | re.IGNORECASE,
)


# DOM-based extractors (Target lazy-renders products via React, so the
# SSR HTML we get first has 0 tcin but after hydration each card wraps
# in @web/ProductCard/ProductCardVariantWrapper with view-transition-name
# carrying the tcin, plus a @web/ProductCard/title anchor with aria-label
# and a "current-price" span.
# Card chunks average ~40KB apart on Target's PLPs (product images
# swap on hover so each card carries two full <picture> blocks). Cap
# at 80KB per card to give plenty of headroom.
_PRODUCT_CARD_RE = re.compile(
    r'data-test="@web/ProductCard/ProductCardVariantWrapper"'
    r'[^>]*style="[^"]*product-info-(\d+)'
    r'([\s\S]{0,80000}?)'
    r'(?=data-test="@web/ProductCard/ProductCardVariantWrapper"|$)',
    re.IGNORECASE,
)
_TITLE_ANCHOR_RE = re.compile(
    r'aria-label="([^"]{5,240})"[^>]*data-test="@web/ProductCard/title"',
    re.IGNORECASE,
)
_TITLE_HREF_RE = re.compile(
    r'data-test="@web/ProductCard/title"[^>]*href="([^"]+)"',
    re.IGNORECASE,
)
_CARD_IMG_RE = re.compile(
    r'src="(https://target\.scene7\.com/is/image/Target/[^"]+)"',
    re.IGNORECASE,
)
_CARD_PRICE_RE = re.compile(
    r'data-test="current-price"[^>]*>\s*<span>\$?([0-9,.]+)</span>',
    re.IGNORECASE,
)


def _extract_from_html(html: str, limit: int = 10) -> list[dict]:
    """Target's PLP hydrates via React AFTER SSR ships. Two parse paths:

    1. DOM-based (post-hydration): find every ProductCard chunk and pull
       the tcin (from view-transition-name), title (aria-label of the
       @web/ProductCard/title anchor), url (its href), image (first
       scene7 src), and price (current-price span). This is what fires
       once Playwright waits for hydration.
    2. Legacy JSON fallback: `__NEXT_DATA__`, `__TGT_DATA__`, or a
       `"products": [...]` array in the body - only relevant on the rare
       pages that still SSR their product grid.

    We try DOM first because that's what modern Target ships. When
    Target flags props.isBot in __NEXT_DATA__ we log a warning so the
    operator knows to donate fresh cookies.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for m in _PRODUCT_CARD_RE.finditer(html):
        tcin = m.group(1)
        chunk = m.group(2)
        if tcin in seen:
            continue
        title_m = _TITLE_ANCHOR_RE.search(chunk)
        href_m  = _TITLE_HREF_RE.search(chunk)
        img_m   = _CARD_IMG_RE.search(chunk)
        price_m = _CARD_PRICE_RE.search(chunk)
        if not title_m or not href_m:
            continue
        seen.add(tcin)
        url = href_m.group(1).split('#')[0].split('?')[0]
        if url.startswith('/'):
            url = 'https://www.target.com' + url
        out.append({
            'rank':  len(out) + 1,
            'name':  unescape(title_m.group(1)).strip()[:180],
            'url':   url,
            'image': img_m.group(1) if img_m else '',
            'price': f"${price_m.group(1)}" if price_m else '',
            'tcin':  tcin,
        })
        if len(out) >= limit:
            return out

    # Fall-through: JSON-based extraction for the rare pages that
    # actually SSR their products. Also detects the app-layer bot flag.
    m = _NEXT_DATA_RE.search(html)
    if m:
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            is_bot = (((data.get('props') or {}).get('isBot')) is True)
            if is_bot:
                logger.warning("target: props.isBot=True in __NEXT_DATA__ - "
                                "app-layer flagged us. Donate fresh cookies: "
                                "python3 scripts/trends_scrapers/donate_cookies.py target.com")
            _walk(data, seen, out, limit)
            if out:
                return out

    m = re.search(r'window\.__TGT_DATA__\s*=\s*({.+?})\s*;\s*</script>',
                   html, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            _walk(data, seen, out, limit)
            if out:
                return out

    m = re.search(r'"products":\s*(\[[^\]]{500,50000}\])', html, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(1))
            out.extend(_from_products_array(arr, limit - len(out)))
        except json.JSONDecodeError:
            pass
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
    rendered = render_pages(TARGET_URLS,
                             homepage='https://www.target.com/',
                             cookie_domain='target.com',
                             wait_selectors=_TARGET_HYDRATE_SELECTORS,
                             hydration_wait_ms=12000)
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
