"""
Xbox Game Pass Ultimate trending games scraper.

Xbox Cloud Gaming's public storefront ships the full Game Pass Ultimate
catalog and every editorial rail inline as `window.__PRELOADED_STATE__`
on `https://www.xbox.com/en-US/play`. Rails include:

    "Most popular on cloud"   <- primary trending signal (3,361 games
                                  sorted by play popularity)
    "Recently added"          <- freshness signal (~25 games)
    "Leaving soon"            <- churn signal (~5 games)
    "Free to Play"            <- monetization slice
    "Play with touch"         <- mobile-optimized (~129 games)

Only the top ~30 products in each rail come pre-hydrated with title +
poster inside the HTML. For the rest we hit Microsoft's public
DisplayCatalog API which does not require auth:

    https://displaycatalog.mp.microsoft.com/v7.0/products
        ?bigIds=<comma-separated>&market=US&languages=en-US
        &fieldsTemplate=Details

That endpoint returns title, publisher, and a full `Images` array
(Poster / BoxArt / Tile / SuperHeroArt) for any Microsoft Store big
product ID.

Cookies: not strictly required (the /play HTML shell renders the same
rails anonymously) but a donated session (`donate_cookies.py xbox.com`)
picks up the real XToken and slips past Adobe / Clarity analytics
throttling faster.

Standalone:
    python3 -m scripts.trends_scrapers.xbox_gamepass
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from ._base import http_get, run_scraper

logger = logging.getLogger(__name__)


PLAY_URL       = 'https://www.xbox.com/en-US/play'
DISPLAY_CAT_URL = ('https://displaycatalog.mp.microsoft.com/v7.0/products'
                    '?bigIds={ids}&market=US&languages=en-US'
                    '&fieldsTemplate=Details')

# Rails we care about, in emit-priority order. Rail titles come from
# the SIGL's `data.title` field. Multiple SIGLs share the same title
# (market bundle variants) - we dedupe by product ID across rails.
_TRENDING_RAIL_TITLE   = 'Most popular on cloud'
_RECENTLY_ADDED_TITLE  = 'Recently added'

# How many product IDs to pull from the trending rail. 40 is plenty for
# the Gaming panel (which caps at 25 visible) and covers the tail if
# DisplayCatalog drops a couple.
_MAX_TRENDING_IDS      = 40
_MAX_RECENTLY_IDS      = 25

# DisplayCatalog batches. The API accepts up to ~50 bigIds per query,
# but the response gets very heavy at that size - 25 keeps each request
# under ~2MB.
_HYDRATE_BATCH         = 25


def _parse_preloaded_state(html: str) -> dict:
    """Extract and JSON-parse `window.__PRELOADED_STATE__ = {...};` from
    the xbox.com play page. Uses raw_decode so we don't over-capture the
    trailing script bytes."""
    idx = html.find('window.__PRELOADED_STATE__')
    if idx < 0:
        return {}
    eq = html.find('=', idx)
    if eq < 0:
        return {}
    start = html.find('{', eq)
    if start < 0:
        return {}
    try:
        state, _end = json.JSONDecoder().raw_decode(html[start:])
        if isinstance(state, dict):
            return state
    except json.JSONDecodeError as e:
        logger.warning("xbox_gamepass: __PRELOADED_STATE__ JSON decode failed: %s", e)
    return {}


def _extract_rail_product_ids(state: dict, wanted_title: str,
                                cap: int) -> list[str]:
    """Return the first `cap` product IDs from any SIGL rail whose
    `data.title` matches `wanted_title`. When multiple SIGLs share the
    title (they do for 'All games' + 'Most popular on cloud' etc.), the
    longest product list wins (that's the fully-populated one)."""
    sigls = ((state.get('xcloud') or {}).get('sigls') or {})
    best_ids: list[str] = []
    for _sigl_key, wrap in sigls.items():
        inner = (wrap or {}).get('data') or {}
        if inner.get('title') != wanted_title:
            continue
        prods = inner.get('products') or []
        if not isinstance(prods, list):
            continue
        if len(prods) > len(best_ids):
            best_ids = [p for p in prods if isinstance(p, str)]
    return best_ids[:cap]


def _pick_poster_uri(images: list) -> str:
    """DisplayCatalog images come as [{ImagePurpose, Uri, Width, Height},
    ...]. Preferred purpose order for a portrait poster tile:
        Poster > BoxArt > SuperHeroArt > Tile > Logo
    Prepend https: for protocol-relative URIs."""
    if not isinstance(images, list):
        return ''
    by_purpose: dict[str, str] = {}
    for im in images:
        if not isinstance(im, dict):
            continue
        purpose = im.get('ImagePurpose') or ''
        uri     = im.get('Uri') or ''
        if not uri:
            continue
        if uri.startswith('//'):
            uri = 'https:' + uri
        by_purpose.setdefault(purpose, uri)
    for k in ('Poster', 'BoxArt', 'SuperHeroArt', 'Tile', 'Logo',
              'FeaturePromotionalSquareArt'):
        if k in by_purpose:
            return by_purpose[k]
    if by_purpose:
        return next(iter(by_purpose.values()))
    return ''


def _classify_genre(categories: list) -> str:
    """Boil the DisplayCatalog category list down to a single primary
    genre string for the tile subtitle. Xbox categories look like:
        ['Action & adventure', 'Shooter'] etc.
    Returns the first, or ''."""
    if not isinstance(categories, list) or not categories:
        return ''
    for c in categories:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return ''


def _hydrate_via_display_catalog(product_ids: list[str]) -> dict[str, dict]:
    """Batch-hydrate product IDs against Microsoft's public
    DisplayCatalog API. Returns {product_id: {title, publisher, poster,
    genre, url}}."""
    out: dict[str, dict] = {}
    if not product_ids:
        return out
    for i in range(0, len(product_ids), _HYDRATE_BATCH):
        batch = product_ids[i:i + _HYDRATE_BATCH]
        url = DISPLAY_CAT_URL.format(ids=','.join(batch))
        r = http_get(url, timeout=30, retries=2)
        if r is None:
            logger.warning("xbox_gamepass: DisplayCatalog batch %d-%d failed",
                            i, i + len(batch))
            continue
        try:
            data = json.loads(r.text)
        except Exception as e:
            logger.warning("xbox_gamepass: DisplayCatalog decode failed: %s", e)
            continue
        products = data.get('Products') or []
        for p in products:
            pid = p.get('ProductId') or ''
            if not pid:
                continue
            loc_list = p.get('LocalizedProperties') or []
            loc = loc_list[0] if loc_list else {}
            title     = (loc.get('ProductTitle') or loc.get('DisplayTitle') or '').strip()
            publisher = (loc.get('PublisherName') or '').strip()
            poster    = _pick_poster_uri(loc.get('Images') or [])
            props     = p.get('Properties') or {}
            genre     = _classify_genre(props.get('Categories') or [])
            if not title:
                continue
            out[pid] = {
                'title':     title,
                'publisher': publisher,
                'poster':    poster,
                'genre':     genre,
                'url':       f'https://www.xbox.com/en-US/games/store/-/{pid}',
                'product_id': pid,
            }
    return out


def _load_previous_snapshot() -> list[dict] | None:
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(Bucket='dashboard-inputs',
                           Key='trends_iq_snapshots/latest/xbox_gamepass.json')
        d = json.loads(o['Body'].read().decode('utf-8'))
        items = d.get('national') or []
        return items if isinstance(items, list) and items else None
    except Exception as e:
        logger.info("xbox_gamepass: could not read previous snapshot: %s", e)
        return None


def _mark_cookie_gap(reason: str = '') -> None:
    try:
        from .cookie_gap_notify import notify_cookie_gap
        notify_cookie_gap('xbox_gamepass', 'xbox.com', reason=reason)
    except Exception as e:
        logger.info("cookie_gap notify failed for xbox_gamepass: %s", e)


def fetch() -> dict[str, Any]:
    r = http_get(PLAY_URL, cookie_domain='xbox.com', timeout=30, retries=2)
    if r is None:
        reason = 'xbox_gamepass: /play HTTP fetch returned None'
        logger.warning(reason)
        _mark_cookie_gap(reason=reason)
        prev = _load_previous_snapshot()
        if prev:
            return {'national': prev, 'stale_from_previous': True,
                     'soft_block_reason': reason}
        return {'national': []}

    try:
        html = r.text if hasattr(r, 'text') else r.decode('utf-8')
    except Exception:
        html = ''

    state = _parse_preloaded_state(html)
    if not state:
        reason = (f'xbox_gamepass: no __PRELOADED_STATE__ in {len(html):,}-byte '
                   f'HTML (WAF challenge or page shape change?)')
        logger.warning(reason)
        _mark_cookie_gap(reason=reason)
        prev = _load_previous_snapshot()
        if prev:
            return {'national': prev, 'stale_from_previous': True,
                     'soft_block_reason': reason}
        return {'national': []}

    trending_ids  = _extract_rail_product_ids(state, _TRENDING_RAIL_TITLE,
                                                _MAX_TRENDING_IDS)
    recently_ids  = _extract_rail_product_ids(state, _RECENTLY_ADDED_TITLE,
                                                _MAX_RECENTLY_IDS)

    logger.info("xbox_gamepass: rails -> trending=%d, recently_added=%d",
                 len(trending_ids), len(recently_ids))

    if not trending_ids:
        reason = ('xbox_gamepass: "Most popular on cloud" rail is empty '
                   '(SIGL missing or blocked)')
        logger.warning(reason)
        _mark_cookie_gap(reason=reason)
        prev = _load_previous_snapshot()
        if prev:
            return {'national': prev, 'stale_from_previous': True,
                     'soft_block_reason': reason}
        return {'national': []}

    # Hydrate the union of both rails in one pass, then emit trending
    # first (that's the primary Gaming tab list) with each item stamped
    # with `recently_added: True` if it's also in the freshness rail.
    all_ids  = list(dict.fromkeys(trending_ids + recently_ids))
    hydrated = _hydrate_via_display_catalog(all_ids)
    recently_set = set(recently_ids)

    items: list[dict] = []
    for pid in trending_ids:
        h = hydrated.get(pid)
        if not h:
            continue
        items.append({
            'rank':             len(items) + 1,
            'title':            h['title'],
            'url':              h['url'],
            'image':            h['poster'],
            'publisher':        h['publisher'],
            'genre':            h['genre'],
            'product_id':       h['product_id'],
            'category_display': 'Game',
            'recently_added':   pid in recently_set,
            'collection':       'Most popular on cloud',
        })
        if len(items) >= 25:
            break

    if not items:
        reason = (f'xbox_gamepass: hydration returned 0 titles from '
                   f'{len(all_ids)} product IDs (DisplayCatalog API '
                   f'block?)')
        logger.warning(reason)
        prev = _load_previous_snapshot()
        if prev:
            return {'national': prev, 'stale_from_previous': True,
                     'soft_block_reason': reason}
        return {'national': []}

    logger.info("xbox_gamepass: emitting %d hydrated games", len(items))

    # Emit a small "sources" pack too so if we add PlayStation Plus /
    # Steam later, the Gaming card can render tabs. For now there's
    # only one source.
    return {
        'national': items,
        'sources': {
            'xbox_gamepass': {
                'label': 'Xbox Game Pass Ultimate',
                'items': items,
            },
        },
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('xbox_gamepass', 'Xbox Game Pass Ultimate', 'gaming', fetch)
    print(f"xbox_gamepass: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
