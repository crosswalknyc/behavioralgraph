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


def _load_previous_espnplus_snapshot() -> list[dict] | None:
    """Read the current latest/ ESPN+ snapshot from S3. Returns the
    national items list on success, None on any failure. Used to
    preserve last-known-good when today's fetch stumbles on a
    transient network glitch (like the 2026-09-01 08:00 launchd run
    that got net::ERR_INTERNET_DISCONNECTED after Wi-Fi flickered),
    a Bamgrid soft-block that survives the http_get retry, or a
    future parser regression - same pattern max_streaming.py and
    disneyplus.py use."""
    try:
        import boto3, json as _json
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(Bucket='dashboard-inputs',
                          Key='trends_iq_snapshots/latest/espnplus.json')
        d = _json.loads(o['Body'].read().decode('utf-8'))
        items = d.get('national') or []
        return items if isinstance(items, list) and items else None
    except Exception as e:
        logger.info("espnplus: could not read previous snapshot: %s", e)
        return None


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

    # Empty result => Bamgrid soft-block that survived the http_get
    # retry, transient network glitch (2026-09-01 08:00 launchd hit
    # ERR_INTERNET_DISCONNECTED on the browse/espn load), consent
    # shell, or a future parser regression. Fire the offline notifier
    # so operators know to look; the dashboard itself just shows a
    # neutral 'warming up' tile per the no-operator-hints rule.
    #
    # Then preserve yesterday's snapshot rather than overwriting the
    # tile with an empty list. Same pattern as max_streaming.py and
    # disneyplus.py. Only falls back to empty when there is no prior
    # good snapshot to preserve (first-ever run, permanent regression,
    # etc.), so the cookie-gap 'warming up' state can still take over.
    if not all_items:
        biggest = max((len(html) for _, html in rendered), default=0)
        reason = (f'ESPN+ browse/espn returned 0 titles from largest '
                  f'{biggest}-byte page; check that disneyplus.com is '
                  'reachable from the residential IP, re-donate cookies '
                  'for disneyplus.com if needed, or wait for the next '
                  'scheduled run if this was a transient network glitch')
        try:
            from .cookie_gap_notify import notify_cookie_gap
            notify_cookie_gap('espnplus', 'disneyplus.com', reason=reason)
        except Exception as e:
            logger.info("espnplus cookie_gap notify failed: %s", e)
        prev = _load_previous_espnplus_snapshot()
        if prev:
            logger.warning("espnplus: preserving previous snapshot "
                           "(%d items) instead of overwriting with 0",
                           len(prev))
            return {'national': prev,
                    'stale_from_previous': True,
                    'soft_block_reason': reason}
        logger.warning("espnplus: no previous snapshot available; "
                       "letting empty result write so the cookie-gap "
                       "'warming up' state takes over")

    return {'national': all_items[:25]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('espnplus', 'ESPN+', 'streaming', fetch)
    print(f"espnplus: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
