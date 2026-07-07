"""
Nike best-of / trending scraper.

Nike doesn't publish an official "best sellers" listing, but their
`/w/best-77x6q` page ("Best of Nike") is the closest curated equivalent
and drives their homepage merchandising. Product tiles are rendered
server-side inside `<div data-testid="product-card">` with a JSON
data-attribute payload (`data-nike-product-card`) that carries name,
color, price, and image.

Standalone:

    python3 -m scripts.trends_scrapers.nike
"""

from __future__ import annotations

import json
import logging
import re
import sys
from html import unescape
from typing import Any

from ._base import browser_headers, http_get, run_scraper

logger = logging.getLogger(__name__)


NIKE_URLS = [
    ('Best of Nike',   'https://www.nike.com/w/best-77x6q'),
    ('Trending',       'https://www.nike.com/w/mens-shoes-nik1zy7ok'),
]


# Nike embeds an INITIAL_REDUX_STATE JSON in the HTML head with the full
# product list. Fall back to card-level regex if the redux payload isn't
# present.
_REDUX_RE = re.compile(
    r'window\.INITIAL_REDUX_STATE\s*=\s*({.+?});\s*</script>',
    re.DOTALL,
)


def _extract_from_redux(html: str, limit: int) -> list[dict]:
    m = _REDUX_RE.search(html)
    if not m:
        return []
    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    # The redux tree is nested deeply and Nike's shape changes over time.
    # Walk everything looking for objects with a stable set of product
    # keys (name + fullPrice + url).
    products: list[dict] = []
    seen_ids: set[str] = set()

    def _walk(node):
        if isinstance(node, dict):
            pid = node.get('productId') or node.get('cloudProductId') or node.get('id')
            title = node.get('title') or node.get('fullTitle') or node.get('name')
            url = node.get('pdpUrl') or node.get('url') or node.get('link')
            price = (node.get('currentPrice') or node.get('fullPrice')
                      or node.get('salePrice') or node.get('price'))
            imgs = node.get('images') or node.get('imageUrls') or []
            if pid and title and url:
                pid = str(pid)
                if pid not in seen_ids:
                    image = ''
                    if isinstance(imgs, list) and imgs:
                        first = imgs[0]
                        image = first if isinstance(first, str) else first.get('url', '')
                    elif isinstance(imgs, dict):
                        image = imgs.get('portraitURL') or imgs.get('squarishURL') or ''
                    seen_ids.add(pid)
                    products.append({
                        'rank':  len(products) + 1,
                        'name':  unescape(str(title))[:180],
                        'url':   str(url) if str(url).startswith('http') else f'https://www.nike.com{url}',
                        'image': image,
                        'price': f"${price}" if price else '',
                    })
                    if len(products) >= limit:
                        return
            for v in node.values():
                if len(products) >= limit:
                    return
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                if len(products) >= limit:
                    return
                _walk(v)

    _walk(state)
    return products


_CARD_TITLE_RE = re.compile(
    r'<div[^>]*class="[^"]*product-card__title[^"]*"[^>]*>([^<]+)</div>',
    re.IGNORECASE,
)
_CARD_LINK_RE = re.compile(
    r'<a[^>]+class="[^"]*product-card__link-overlay[^"]*"[^>]+href="([^"]+)"',
    re.IGNORECASE,
)
_CARD_PRICE_RE = re.compile(
    r'<div[^>]*data-testid="product-price"[^>]*>([^<]+)</div>',
    re.IGNORECASE,
)
_CARD_IMG_RE = re.compile(
    r'<img[^>]+src="(https://static\.nike\.com/[^"]+)"',
    re.IGNORECASE,
)


def _extract_from_cards(html: str, limit: int) -> list[dict]:
    titles = _CARD_TITLE_RE.findall(html)
    links  = _CARD_LINK_RE.findall(html)
    prices = _CARD_PRICE_RE.findall(html)
    images = _CARD_IMG_RE.findall(html)
    out: list[dict] = []
    for i in range(min(limit, len(titles), len(links))):
        url = links[i]
        if url.startswith('/'):
            url = 'https://www.nike.com' + url
        out.append({
            'rank':  i + 1,
            'name':  unescape(titles[i].strip())[:180],
            'url':   url,
            'image': images[i] if i < len(images) else '',
            'price': unescape(prices[i].strip()) if i < len(prices) else '',
        })
    return out


def fetch() -> dict[str, Any]:
    categories: list[dict] = []
    for label, url in NIKE_URLS:
        r = http_get(url, headers=browser_headers(
            referer='https://www.nike.com/'))
        if r is None or not r.ok:
            logger.warning("nike %s: fetch failed", label)
            continue
        items = _extract_from_redux(r.text, limit=10) or \
                _extract_from_cards(r.text, limit=10)
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
    result = run_scraper('nike', 'Nike', 'retailer', fetch)
    print(f"nike: national={len(result.get('national', []))} "
           f"categories={len(result.get('categories', []))} "
           f"error={result.get('error')}", file=sys.stderr)
