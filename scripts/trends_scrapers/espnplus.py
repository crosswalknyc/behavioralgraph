"""
ESPN+ trending scraper.

ESPN+ is bundled with Disney+ on the Disney bundle plan. If Jenna's
disneyplus.com session already carries ESPN+ entitlement, we can piggy-
back on those cookies. Otherwise donate plus.espn.com cookies directly.

Donate via:
    python3 scripts/trends_scrapers/donate_cookies.py --domain plus.espn.com

Standalone:
    python3 -m scripts.trends_scrapers.espnplus
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


ESPN_URLS = [
    ('Home',    'https://plus.espn.com/'),
    ('Live',    'https://www.espn.com/watch/'),
    ('Sports',  'https://www.espn.com/watch/collections'),
]


_ESPN_HYDRATE_SELECTORS = [
    'a[data-testid="tile"]',
    'div[data-testid="card"]',
    'a[href*="/watch/"]',
    'div.WatchCard',
]


# ESPN's watch cards render as `<a class="WatchCard__Link" ... aria-label="...">`
_ESPN_CARD_RE = re.compile(
    r'<a[^>]+class="[^"]*WatchCard[^"]*"[^>]*href="([^"]+)"[^>]*'
    r'aria-label="([^"]{5,180})"',
    re.IGNORECASE,
)


def _extract_from_dom(html: str, limit: int = 20) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for m in _ESPN_CARD_RE.finditer(html):
        href  = m.group(1)
        title = unescape(m.group(2)).strip()
        key = title.lower()
        if key in seen or len(title) < 5:
            continue
        seen.add(key)
        url = href if href.startswith('http') else f'https://www.espn.com{href}'
        out.append({
            'rank':             len(out) + 1,
            'title':            title,
            'url':              url,
            'category_display': 'Live' if '/live' in href.lower() else '',
            'collection':       '',
        })
        if len(out) >= limit:
            break
    return out


def fetch() -> dict[str, Any]:
    # Try Disney bundle cookies first (many users get ESPN+ via bundle);
    # fall back to standalone plus.espn.com session.
    rendered = render_pages(ESPN_URLS,
                             homepage='https://www.espn.com/',
                             cookie_domain='plus.espn.com',
                             wait_selectors=_ESPN_HYDRATE_SELECTORS,
                             hydration_wait_ms=10000)

    all_items: list[dict] = []
    seen: set[str] = set()
    for label, html in rendered:
        items = parse_streaming_html(html, host='www.espn.com', limit=25)
        if not items:
            items = _extract_from_dom(html, limit=25)
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            all_items.append(it)
        logger.info("espnplus %s: parsed %d titles from %d-byte HTML",
                     label, len(items), len(html))

    for i, it in enumerate(all_items[:20], start=1):
        it['rank'] = i
    return {'national': all_items[:20]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('espnplus', 'ESPN+', 'streaming', fetch)
    print(f"espnplus: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
