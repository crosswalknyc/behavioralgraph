"""
HBO Max trending scraper.

Requires donated cookies for `hbomax.com`. Donate via:

    python3 scripts/trends_scrapers/donate_cookies.py hbomax.com

CRITICAL: donate cookies from `play.hbomax.com` (the actual player app),
NOT from the marketing shell `www.hbomax.com`. The two use different
session tokens - only play.hbomax.com issues the one that lets us render
the hydrated home / series / movie pages. When you're signed into HBO
Max in Chrome and visit play.hbomax.com/, the donation script picks
up the right cookie automatically.

Naming history: WBD launched "Max" (max.com) in mid-2023, then
reverted to "HBO Max" in mid-2025. The rebrand pushed the app back to
play.hbomax.com. `max.com` no longer resolves the app shell. The
scraper source key stays `max` for backwards compat with the S3
snapshot path (`trends_iq_snapshots/latest/max.json`); everything
customer-facing is HBO Max.

Max renders tiles as anchors of the form:

    <a aria-label="⁦⁨⁨Rick and Morty⁩⁩. ⁨1 of 20⁩. ⁨⁨New Episode⁩⁩⁩"
       data-sonic-id="ab553cdc-..."
       data-sonic-type="show"
       data-testid="..._tile"
       href="/show/UUID">

The aria-label uses Unicode isolate characters (U+2066/8/9) to wrap the
title, then ", N of M" for position, then optional ", New" / ", New
Episode" / ", New Season" annotations. Strip those to get the title.

Note: this module is named `max_streaming.py` (not `max.py`) because
`max` shadows Python's builtin `max()` and shows up first in the
package's namespace at import time. The scraper registry in
`run_all.py` uses source key `max`.

Standalone:
    python3 -m scripts.trends_scrapers.max_streaming
"""

from __future__ import annotations

import logging
import re
import sys
from html import unescape
from typing import Any

from ._base import run_scraper
from ._playwright import render_pages

logger = logging.getLogger(__name__)


MAX_URLS = [
    # 2026-07 rebrand-revert: play.hbomax.com's home rail carries
    # enough tiles for the dashboard on its own (typically 25-40
    # deduped titles across Featured / Trending Now / Continue
    # Watching / Because You Watched). The pre-rebrand /pages/series
    # and /pages/movies routes now redirect-loop and are dropped.
    ('Home', 'https://play.hbomax.com/'),
]


# HBO Max ships its home rail as fully-rendered DOM tiles. Once we
# can find at least a few show / movie anchors the page is hydrated.
# Match BOTH the singular /show/ /movie/ path (currently live as of
# 2026-08-31 - reverted from the plural /shows/ /movies/ that shipped
# briefly in mid-2025) AND the plural variants, so the scraper keeps
# working if HBO Max flips the URL shape again.
_MAX_HYDRATE_SELECTORS = [
    'a[href*="/show/"]',
    'a[href*="/movie/"]',
    'a[href*="/shows/"]',
    'a[href*="/movies/"]',
]


# HBO Max tile anchor looks like (2026-08 shape, singular, no slug):
#
#   <a href="/show/c68e69d7-9317-428a-a615-cdf8fe5a2e06"
#      draggable="false" class="sc-...">
#     <div class="img-collection ...">
#       <img id="pageXXXX-bandYYY-..." src=".../artwork.png" />
#     </div>
#     <p class="sc-...">House of the Dragon</p>
#   </a>
#
# The 2025 rebrand-revert briefly shipped /shows/<slug>/<uuid> with
# the slug in the middle; that came and went. Accept both by making
# the middle slug segment optional. The trailing 36-char UUID is the
# stable entity identifier across both shapes and is what we dedupe
# on. Film vs TV falls out of whether the first path segment is
# show(s) or movie(s). Title lives in the last <p> inside the anchor
# in every shape we've seen since 2025.
_MAX_TILE_RE = re.compile(
    r'<a\s+href="(/(?:shows?|movies?)/(?:[a-z0-9\-]+/)?[a-f0-9\-]{36})"'
    r'[^>]*>'
    r'.*?<p[^>]*>([^<]{1,240})</p>'
    r'\s*</a>',
    re.IGNORECASE | re.DOTALL,
)


def _clean_title(raw: str) -> str:
    """HBO Max post-2025 puts a clean title in the tile's trailing
    <p>. All we need to do is unescape entities + collapse whitespace.
    (The pre-rebrand aria-label parsing with position suffixes and
    Unicode isolates is gone.)"""
    return re.sub(r'\s+', ' ', unescape(raw)).strip()


def _classify_from_path(path: str) -> str:
    if '/movie/' in path or '/movies/' in path:
        return 'Film'
    if '/show/' in path or '/shows/' in path:
        return 'TV'
    return ''


# Same nav-word blacklist we use elsewhere - after stripping isolates
# some interactive elements (Search, Menu, My Stuff) end up looking
# title-shaped.
_NAV_STOPWORDS = frozenset({
    'search', 'menu', 'my stuff', 'my list', 'home', 'browse',
    'sign in', 'log in', 'sign out', 'log out', 'account', 'settings',
    'notifications', 'help', 'downloads', 'watch now', 'sports',
    'live tv', 'main', 'browse menu', 'h b o max home', 'next title',
    'unmute preview', 'mute preview',
})


def _extract_max_dom(html: str, limit: int = 40) -> list[dict]:
    """Dedupe by the trailing UUID (the stable entity id across the
    /show/<uuid>, /shows/<slug>/<uuid>, /movie/<uuid>, and
    /movies/<slug>/<uuid> URL shapes HBO Max has cycled through). The
    same show can appear on Featured, Trending Now, and Because You
    Watched rails; we only want it counted once. First occurrence
    wins, which preserves the "closest to top of page" ranking."""
    items: list[dict] = []
    seen_uuids: set[str] = set()
    for m in _MAX_TILE_RE.finditer(html):
        path  = m.group(1)      # /show/<uuid>  or  /shows/<slug>/<uuid>
        title = _clean_title(m.group(2))
        uuid_key = path.rsplit('/', 1)[-1].lower()
        if uuid_key in seen_uuids:
            continue
        seen_uuids.add(uuid_key)
        if not (2 <= len(title) <= 200):
            continue
        if title.lower() in _NAV_STOPWORDS:
            continue
        items.append({
            'rank':             len(items) + 1,
            'title':            title,
            'url':              f'https://play.hbomax.com{path}',
            'category_display': _classify_from_path(path),
            'collection':       '',
        })
        if len(items) >= limit:
            break
    return items


def fetch() -> dict[str, Any]:
    # Max IP-gates non-US ranges (including Hetzner Falkenstein and any
    # residential proxy that lands outside the US). Route through the
    # IPRoyal residential proxy so we hit a US exit. This is a no-op
    # when IPROYAL_PROXY_* env vars aren't set - the scraper just tries
    # the direct route and (on Hetzner) will get a ~10KB rejection page.
    #
    # NOTE: for the proxy to reliably land US exits, the IPRoyal
    # dashboard's Country/Region dropdown must be set to
    # "United States". The default "Random" rotation gives US only
    # ~12% of the time.
    rendered = render_pages(MAX_URLS,
                             homepage='https://www.hbomax.com/',
                             cookie_domain='hbomax.com',
                             wait_selectors=_MAX_HYDRATE_SELECTORS,
                             wait_ms=4000,
                             scroll_ms=3000,
                             hydration_wait_ms=12000,
                             use_proxy=True)

    all_items: list[dict] = []
    seen: set[str] = set()
    for label, html in rendered:
        items = _extract_max_dom(html, limit=40)
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            all_items.append(it)
        logger.info("max %s: parsed %d titles from %d-byte HTML",
                     label, len(items), len(html))

    for i, it in enumerate(all_items[:25], start=1):
        it['rank'] = i

    # Empty result => cookies are missing/stale OR the account isn't
    # signed in (the anonymous marketing shell still returns ~10-30
    # promoted tiles, so a truly-empty parse means the render failed).
    # Fire the offline notifier so operators know to re-donate from
    # play.hbomax.com; the dashboard itself just shows a neutral
    # 'warming up' tile per the no-operator-hints rule.
    if not all_items:
        try:
            from .cookie_gap_notify import notify_cookie_gap
            notify_cookie_gap('max', 'hbomax.com',
                              reason=('HBO Max home rail returned 0 titles; '
                                      'sign in at play.hbomax.com in Chrome, '
                                      'then re-donate cookies for hbomax.com'))
        except Exception as e:
            logger.info("max cookie_gap notify failed: %s", e)

    return {'national': all_items[:25]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('max', 'HBO Max', 'streaming', fetch)
    print(f"max: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
