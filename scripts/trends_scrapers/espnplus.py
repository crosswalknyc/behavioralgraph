"""
ESPN+ trending scraper.

ESPN+ programming lives at `disneyplus.com/browse/espn` under the
Disney bundle. It's a public catalog page - no auth cookies required
to render the content. Uses the exact same stitchDocument parser as
the Disney+ scraper (see `disneyplus.py`).

IP-gate note: same story as Disney+ - Bamgrid IP-gates datacenter
ranges. Run this scraper from a residential IP (Jenna's laptop) or
a residential proxy. See `local_residential_run.py`.

Standalone:
    python3 -m scripts.trends_scrapers.espnplus
"""

from __future__ import annotations

import logging
import sys
from typing import Any

from ._base import run_scraper, http_get
from ._playwright import render_pages
from .disneyplus import _extract_disneyplus, _BAMGRID_ERROR_MARKER

logger = logging.getLogger(__name__)


ESPNPLUS_URLS = [
    # /browse/espn is the only ESPN+ landing on disneyplus.com. Other
    # sport-shaped paths (/browse/sports, /browse/football, etc.) return
    # a hard 404 - Disney+ organizes ESPN+ content into leagues/shows
    # deeper inside /browse/espn, not into top-level browse paths.
    ('browse_espn', 'https://www.disneyplus.com/browse/espn'),
]


def _fetch_via_http(pages: list[tuple[str, str]]) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for label, url in pages:
        r = http_get(url, timeout=30, cookie_domain='disneyplus.com')
        if r is None:
            continue
        try:
            html = r.text if hasattr(r, 'text') else r.decode('utf-8')
        except Exception:
            continue
        results.append((label, html))
    return results


def fetch() -> dict[str, Any]:
    rendered = render_pages(ESPNPLUS_URLS,
                             homepage='https://www.disneyplus.com/',
                             cookie_domain='disneyplus.com',
                             wait_ms=4000,
                             scroll_ms=2500,
                             hydration_wait_ms=12000)

    if rendered and all(_BAMGRID_ERROR_MARKER in html and len(html) < 200_000
                        for _, html in rendered):
        logger.warning("espnplus: all pages returned the Bamgrid IP-gate "
                        "error shell. Datacenter IP is being blocked; run "
                        "from a residential IP.")
        rendered = _fetch_via_http(ESPNPLUS_URLS)

    all_items: list[dict] = []
    seen: set[str] = set()
    for label, html in rendered:
        items = _extract_disneyplus(html)
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            all_items.append(it)
        logger.info("espnplus %s: parsed %d titles from %d-byte HTML",
                     label, len(items), len(html))

    for i, it in enumerate(all_items[:25], start=1):
        it['rank'] = i
    return {'national': all_items[:25]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('espnplus', 'ESPN+', 'streaming', fetch)
    print(f"espnplus: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
