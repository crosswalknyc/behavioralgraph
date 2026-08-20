"""
BritBox (US) trending scraper.

BritBox is BBC + ITV's US subscription streamer. Their /us/home shell
ships title data in-DOM as `<a href="/us/{show,season,movie}/..."
aria-label="Title">`, so no hydration payload is needed - a plain HTTP
fetch from a residential IP with a donated bbuser session returns ~50
title anchors per page.

Cookies: sign into britbox.com in Chrome, then

    python3 -m scripts.trends_scrapers.donate_cookies britbox.com

The `bbuser` cookie is the auth token. Anonymous fetches still work but
return the marketing shell (fewer titles surfaced).

Runs residentially by default because BritBox's Akamai config trips on
Hetzner's datacenter IP range for the /us/movies path (returns 403 with
zero titles). From a laptop residential IP, all pages return 200 with
full content.

Standalone:
    python3 -m scripts.trends_scrapers.britbox
"""

from __future__ import annotations

import json
import logging
import re
import sys
from html import unescape
from typing import Any

from ._base import http_get, run_scraper

logger = logging.getLogger(__name__)


# BritBox is >99% TV. /us/home ships 40-60 real title anchors inline
# in the HTML shell (editorially curated rails). Other routes
# (/us/movies, /us/genre/*, /us/programmes, /us/collection/*) are
# React-hydrated after page load and don't include title anchors in
# their initial HTML - Playwright can reach them but /us/home already
# covers the trending rails so we don't bother. If BritBox ever grows
# a real film catalog worth surfacing, add the collection pages here
# via `_playwright.render_pages` instead of plain HTTP.
BRITBOX_URLS = [
    ('home', 'https://www.britbox.com/us/home'),
]


# BritBox href scheme:
#   /us/show/<slug>_<id>       - TV series landing page (rail top-level)
#   /us/season/<slug>_S<n>_<id> - a specific season of a TV series
#   /us/movie/<slug>_<id>      - film landing page
#
# The DOM shape is (multiline, whitespace-permissive):
#   <a class=""
#      href="/us/show/A_Woman_of_Substance_169161"
#      title="A Woman of Substance"
#      onclick="..." ...>
#
# BritBox uses `title="..."` for the display name (not aria-label like
# most React streamers). We match on the URL segment to classify Film
# vs TV without needing an in-payload category hint. `re.DOTALL` lets
# `[^>]*` cross newlines between attributes.
_ANCHOR_RE = re.compile(
    r'<a\b[^>]*?\bhref="(/us/(show|season|movie)/[^"]+)"'
    r'[^>]*?\btitle="([^"]{2,180})"',
    re.IGNORECASE | re.DOTALL,
)
_ANCHOR_RE_REV = re.compile(
    r'<a\b[^>]*?\btitle="([^"]{2,180})"'
    r'[^>]*?\bhref="(/us/(show|season|movie)/[^"]+)"',
    re.IGNORECASE | re.DOTALL,
)


# Nav / promo copy that ends up in aria-labels alongside real titles.
# Case-insensitive exact-match rejection - anything that lexically looks
# like a real show/season/movie name passes.
_NAV_STOPWORDS_LOWER = frozenset({
    'britbox', 'britbox us', 'britbox logo', 'home', 'shows',
    'movies', 'my list', 'account', 'settings', 'search',
    'sign in', 'log in', 'sign out', 'subscribe', 'try free',
    'try britbox free', 'watch free', 'start free trial',
    'next', 'previous', 'play', 'more info', 'add to my list',
    'featured', 'trending', 'popular', 'new arrivals',
    'coming soon', 'recently added', 'continue watching',
})


def _classify(url_segment: str) -> str:
    """`show` and `season` are TV, `movie` is Film."""
    s = (url_segment or '').lower()
    if s == 'movie':
        return 'Film'
    return 'TV'


def _extract(html: str) -> list[dict]:
    """Walk the DOM anchor pattern and pull one row per unique title.

    Rank preserved in on-screen order (BritBox's homepage rails are
    editorially curated, so the earliest anchors are the promoted rows)."""
    seen: set[str] = set()
    out: list[dict] = []
    # Attribute order varies by rail template. Try href-then-title
    # first, then title-then-href to catch both shapes.
    for m in _ANCHOR_RE.finditer(html):
        href, segment, title = m.group(1), m.group(2), m.group(3)
        _add(seen, out, href, segment, title)
    for m in _ANCHOR_RE_REV.finditer(html):
        title, href, segment = m.group(1), m.group(2), m.group(3)
        _add(seen, out, href, segment, title)
    return out


def _add(seen: set[str], out: list[dict],
          href: str, segment: str, title: str) -> None:
    """Insert one row into `out` if the title passes the stopword +
    dedupe filters. Mutates `seen` and `out` in place."""
    title = unescape(title).strip()
    if not (2 <= len(title) <= 200):
        return
    if title.lower() in _NAV_STOPWORDS_LOWER:
        return
    key = title.lower()
    if key in seen:
        return
    seen.add(key)
    url = f'https://www.britbox.com{href.split("?")[0]}'
    out.append({
        'rank':             len(out) + 1,
        'title':            title,
        'url':              url,
        'category_display': _classify(segment),
        'collection':       '',
    })


def _load_previous_snapshot() -> list[dict] | None:
    """Read the current latest/britbox.json snapshot from S3. Used to
    preserve yesterday's items when today's fetch returns zero (soft
    block). Same pattern as disneyplus.py."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(Bucket='dashboard-inputs',
                           Key='trends_iq_snapshots/latest/britbox.json')
        d = json.loads(o['Body'].read().decode('utf-8'))
        items = d.get('national') or []
        return items if isinstance(items, list) and items else None
    except Exception as e:
        logger.info("britbox: could not read previous snapshot: %s", e)
        return None


def _mark_cookie_gap(source: str, domain: str, reason: str = '') -> None:
    try:
        from .cookie_gap_notify import notify_cookie_gap
        notify_cookie_gap(source, domain, reason=reason)
    except Exception as e:
        logger.info("cookie_gap notify failed for %s/%s: %s",
                     source, domain, e)


def fetch() -> dict[str, Any]:
    all_items: list[dict] = []
    seen: set[str] = set()
    parsed_counts: list[int] = []

    for label, url in BRITBOX_URLS:
        r = http_get(url, cookie_domain='britbox.com',
                       timeout=30, retries=2)
        if r is None:
            logger.warning("britbox %s: http_get returned None", label)
            parsed_counts.append(0)
            continue
        try:
            html = r.text if hasattr(r, 'text') else r.decode('utf-8')
        except Exception:
            logger.warning("britbox %s: could not decode response", label)
            parsed_counts.append(0)
            continue
        items = _extract(html)
        parsed_counts.append(len(items))
        logger.info("britbox %s: parsed %d titles from %d-byte HTML",
                     label, len(items), len(html))
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            all_items.append(it)

    # Soft-block detection: if every page came back with 0 titles,
    # preserve yesterday's snapshot instead of overwriting with an
    # empty list, and notify ops via SES.
    if all_items:
        for i, it in enumerate(all_items[:25], start=1):
            it['rank'] = i
        return {'national': all_items[:25]}

    prev = _load_previous_snapshot()
    reason = (f'britbox: {len(BRITBOX_URLS)} pages returned 0 titles '
               f'(counts={parsed_counts}) - datacenter IP block or '
               f'cookies stale?')
    logger.warning("britbox: %s", reason)
    _mark_cookie_gap('britbox', 'britbox.com', reason=reason)
    if prev:
        logger.warning("britbox: preserving previous snapshot "
                        "(%d items) instead of overwriting with 0",
                        len(prev))
        return {'national': prev, 'stale_from_previous': True,
                 'soft_block_reason': reason}
    return {'national': []}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('britbox', 'BritBox', 'streaming', fetch)
    print(f"britbox: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
