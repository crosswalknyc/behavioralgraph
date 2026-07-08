"""
Amazon Prime Video trending scraper.

Requires donated cookies for `amazon.com` (same session as the Amazon
shopping site - Prime Video's storefront lives on amazon.com and
inherits the parent Amazon session).

Donate via:
    python3 scripts/trends_scrapers/donate_cookies.py --domain amazon.com

Standalone:
    python3 -m scripts.trends_scrapers.primevideo

Parser strategy
---------------
Prime Video ships hydrated content in

    <script id="dv-web-page-hydration-data" type="application/json">

with structure:

    init:
      preparations:
        body:
          containers: list[
            { title: "Popular now" | "Featured Originals ..." | ...
              entities: list[
                { displayTitle: "Every Year After",
                  entityType:   "TV Show" | "Movie",
                  link:         { url: "/gp/video/detail/B0GZ.../" },
                  titleID:      "B0GZ7FKMRR",
                  releaseYear:  "2026",
                  ...
                }
              ]
            }
          ]

Sampled 2026-07-07: 7 containers x ~20 entities each on the storefront,
so a full pull yields ~120 titles per page. We dedupe by displayTitle
across containers and rank in container order (Continue Watching,
Featured Originals, Popular Now, then genre rails), matching how
Amazon surfaces them.
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


_HYDRATION_BLOB_RE = re.compile(
    r'<script[^>]+id="dv-web-page-hydration-data"[^>]*>(.+?)</script>',
    re.DOTALL | re.IGNORECASE,
)


def _classify_entity(entity_type: str) -> str:
    """Prime uses 'TV Show', 'Movie', 'Live Event', 'Miniseries', etc."""
    et = (entity_type or '').lower()
    if 'movie' in et or 'film' in et:
        return 'Film'
    if 'tv' in et or 'series' in et or 'show' in et or 'episode' in et:
        return 'TV'
    if 'live' in et or 'event' in et:
        return 'Live'
    return ''


def _extract_prime_hydration(html: str) -> list[dict]:
    """Parse the dv-web-page-hydration-data blob and pull out real titles.
    Returns [] if the blob is missing (unauthenticated marketing shell)
    or malformed. Rails are traversed in on-screen order; Continue
    Watching is intentionally excluded since it's a per-user list, not
    trending.
    """
    m = _HYDRATION_BLOB_RE.search(html)
    if not m:
        return []
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    try:
        containers = obj['init']['preparations']['body']['containers']
    except (KeyError, TypeError):
        return []
    if not isinstance(containers, list):
        return []

    seen: set[str] = set()
    out: list[dict] = []
    for c in containers:
        if not isinstance(c, dict):
            continue
        rail = c.get('title') or c.get('text') or ''
        if isinstance(rail, dict):
            rail = rail.get('text') or rail.get('displayText') or ''
        rail = str(rail).strip()
        if c.get('isContinueWatching'):
            continue
        entities = c.get('entities') or []
        if not isinstance(entities, list):
            continue
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            title = ent.get('displayTitle')
            if not isinstance(title, str):
                title = ent.get('title')
            if not isinstance(title, str):
                continue
            title = title.strip()
            if len(title) < 2 or len(title) > 220:
                continue
            key = title.lower()
            if key in seen:
                continue
            link = ent.get('link') or {}
            url_path = ''
            if isinstance(link, dict):
                url_path = link.get('url') or ''
            if not isinstance(url_path, str):
                url_path = ''
            if url_path.startswith('/'):
                url = f'https://www.amazon.com{url_path.split("?")[0]}'
            elif url_path.startswith('http'):
                url = url_path.split('?')[0]
            elif ent.get('titleID'):
                url = f'https://www.amazon.com/gp/video/detail/{ent["titleID"]}'
            else:
                continue

            seen.add(key)
            out.append({
                'rank':             len(out) + 1,
                'title':            title,
                'url':              url,
                'category_display': _classify_entity(ent.get('entityType') or ''),
                'collection':       rail,
            })
    return out


# Pure-DOM fallback in case Amazon ships a different hydration shape one
# day. Matches <a href="/gp/video/detail/..." aria-label="Show name">.
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
        items = _extract_prime_hydration(html)
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
