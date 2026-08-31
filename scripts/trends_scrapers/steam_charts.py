"""
Steam trending games scraper.

Valve exposes two public JSON endpoints on api.steampowered.com that
carry the exact rails the /charts pages surface, no cookies required:

  ISteamChartsService/GetMostPlayedGames/v1/
      Weekly top-100 games ranked by 24-hour peak concurrent players.
      Emit as the "Most Played" column.

  IStoreTopSellersService/GetWeeklyTopSellers/v1/
      Weekly top sellers by revenue, US-scoped via `country_code=US`.
      Emit as the "Top Sellers" column.

Both endpoints return app IDs + rank; a follow-up call to

  IStoreBrowseService/GetItems/v1/

hydrates each app ID with name, developer/publisher, release date,
image asset filenames, and tag IDs in one batched request. Tag IDs
map to human-readable genre labels via

  IStoreService/GetTagList/v1/?language=english

which we cache once per scrape run. Anonymous curl_cffi Chrome-TLS
impersonation (via `_base.http_get` with `impersonate='chrome124'`)
is sufficient for every endpoint used here.

Snapshot key: s3://dashboard-inputs/trends_iq_snapshots/latest/steam_charts.json
Layout: matches the FAST-channels / Meta Quest shape - one snapshot,
        `sources` dict with one entry per panel key. `trends_iq.
        _fetch_gaming_trending` reads the sources-keyed layout.

Cookies: not required. If Valve ever tightens abuse posture, the
standard `donate_cookies.py steampowered.com` flow plus the
`DOMAIN_REFRESH_MAP` entry in `refresh_after_donation.py` will inject
a session automatically.

Standalone:
    python3 -m scripts.trends_scrapers.steam_charts
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

from ._base import http_get, run_scraper

logger = logging.getLogger(__name__)


# ----- Endpoints -----
_MOST_PLAYED_URL = (
    'https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/'
)

# Weekly top sellers requires a country_code + a context envelope.
# `page_start` / `page_count` control the page window; 25 fits our
# per-column cap. `steam_realm=1` is the global storefront (2 is the
# Perfect World China realm).
_TOP_SELLERS_INPUT = {
    'country_code': 'US',
    'page_start': 0,
    'page_count': 25,
    'context': {
        'language': 'english',
        'country_code': 'US',
        'steam_realm': 1,
    },
}
_TOP_SELLERS_URL = (
    'https://api.steampowered.com/IStoreTopSellersService/'
    'GetWeeklyTopSellers/v1/?input_json='
    + quote(json.dumps(_TOP_SELLERS_INPUT, separators=(',', ':')))
)

_GET_ITEMS_URL_TMPL = (
    'https://api.steampowered.com/IStoreBrowseService/GetItems/v1/?input_json='
    '{payload}'
)
_TAG_LIST_URL = (
    'https://api.steampowered.com/IStoreService/GetTagList/v1/'
    '?language=english'
)
_APP_PAGE_URL_TMPL = 'https://store.steampowered.com/{store_url_path}'

# Steam's asset CDN. The `assets.asset_url_format` field on a
# store_item is a relative template like `steam/apps/730/${FILENAME}?t=...`;
# combine with the CDN base + the specific asset filename (header /
# main_capsule / library_capsule) to build a full URL.
_STEAM_ASSET_CDN = 'https://shared.akamai.steamstatic.com/store_item_assets/'

# Cap per column. 25 matches the Gaming panel's display cap.
_MAX_ITEMS_PER_PANEL = 25

# Recency window for the NEW badge. Anything released in the past
# 14 days trips `is_new`; the frontend renders a small NEW chip on
# the row card.
_NEW_WINDOW_S = 14 * 24 * 3600


def _fetch_most_played_ranks() -> list[dict]:
    """Return the top-N (up to 25) most-played entries from
    ISteamChartsService, sorted by rank ascending. Each entry has
    `appid`, `rank`, `last_week_rank`, `peak_in_game`."""
    r = http_get(_MOST_PLAYED_URL, cookie_domain='steampowered.com',
                 timeout=30, retries=3)
    if r is None:
        logger.warning("steam_charts: most_played fetch returned None")
        return []
    try:
        data = r.json() if hasattr(r, 'json') else json.loads(r.text)
    except Exception as e:
        logger.warning("steam_charts: most_played JSON decode failed: %s", e)
        return []
    ranks = ((data.get('response') or {}).get('ranks') or [])
    if not isinstance(ranks, list):
        return []
    out: list[dict] = []
    for row in ranks[:_MAX_ITEMS_PER_PANEL]:
        if not isinstance(row, dict):
            continue
        appid = row.get('appid')
        rank = row.get('rank')
        if not appid or not rank:
            continue
        out.append({
            'appid': int(appid),
            'rank': int(rank),
            'last_week_rank': int(row.get('last_week_rank') or 0),
            'peak_in_game': int(row.get('peak_in_game') or 0),
        })
    return out


def _fetch_top_sellers_ranks() -> list[dict]:
    """Return the top-N (up to 25) US weekly top-seller entries.
    Each has `appid`, `rank`, `last_week_rank`, and an `item` dict
    with `name` + `store_url_path` prepopulated."""
    r = http_get(_TOP_SELLERS_URL, cookie_domain='steampowered.com',
                 timeout=30, retries=3)
    if r is None:
        logger.warning("steam_charts: top_sellers fetch returned None")
        return []
    try:
        data = r.json() if hasattr(r, 'json') else json.loads(r.text)
    except Exception as e:
        logger.warning("steam_charts: top_sellers JSON decode failed: %s", e)
        return []
    ranks = ((data.get('response') or {}).get('ranks') or [])
    if not isinstance(ranks, list):
        return []
    out: list[dict] = []
    for row in ranks[:_MAX_ITEMS_PER_PANEL]:
        if not isinstance(row, dict):
            continue
        appid = row.get('appid')
        rank = row.get('rank')
        if not appid or not rank:
            continue
        out.append({
            'appid': int(appid),
            'rank': int(rank),
            'last_week_rank': int(row.get('last_week_rank') or 0),
            'item_preview': row.get('item') or {},
        })
    return out


def _get_items_batch(appids: list[int]) -> dict[int, dict]:
    """Batch-hydrate app IDs via IStoreBrowseService/GetItems. Returns
    {appid: store_item_dict}. Store items include name, url path,
    basic_info (developers / publishers / short_description), tags,
    assets, and release info."""
    if not appids:
        return {}
    payload = {
        'ids': [{'appid': int(a)} for a in appids],
        'context': {
            'language': 'english',
            'country_code': 'US',
            'steam_realm': 1,
        },
        'data_request': {
            'include_assets': True,
            'include_release': True,
            'include_platforms': True,
            'include_all_purchase_options': True,
            'include_screenshots': False,
            'include_trailers': False,
            'include_ratings': True,
            'include_tag_count': 5,
            'include_reviews': False,
            'include_basic_info': True,
            'include_supported_languages': False,
            'include_full_description': False,
        },
    }
    url = _GET_ITEMS_URL_TMPL.format(
        payload=quote(json.dumps(payload, separators=(',', ':')))
    )
    r = http_get(url, cookie_domain='steampowered.com',
                 timeout=45, retries=3)
    if r is None:
        logger.warning("steam_charts: GetItems fetch returned None")
        return {}
    try:
        data = r.json() if hasattr(r, 'json') else json.loads(r.text)
    except Exception as e:
        logger.warning("steam_charts: GetItems JSON decode failed: %s", e)
        return {}
    items = ((data.get('response') or {}).get('store_items') or [])
    out: dict[int, dict] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        aid = it.get('appid') or it.get('id')
        if aid is None:
            continue
        try:
            out[int(aid)] = it
        except (TypeError, ValueError):
            continue
    return out


_TAG_LIST_CACHE: dict[int, str] = {}


def _load_tag_map() -> dict[int, str]:
    """Fetch and cache the tagid -> name map. Returns {} on failure."""
    global _TAG_LIST_CACHE
    if _TAG_LIST_CACHE:
        return _TAG_LIST_CACHE
    r = http_get(_TAG_LIST_URL, cookie_domain='steampowered.com',
                 timeout=20, retries=2)
    if r is None:
        return {}
    try:
        data = r.json() if hasattr(r, 'json') else json.loads(r.text)
    except Exception as e:
        logger.warning("steam_charts: tag_list JSON decode failed: %s", e)
        return {}
    tags = ((data.get('response') or {}).get('tags') or [])
    out: dict[int, str] = {}
    for t in tags:
        if not isinstance(t, dict):
            continue
        tid = t.get('tagid')
        name = (t.get('name') or '').strip()
        if tid is None or not name:
            continue
        try:
            out[int(tid)] = name
        except (TypeError, ValueError):
            continue
    _TAG_LIST_CACHE = out
    return out


def _pick_primary_genre(store_item: dict, tag_map: dict[int, str]) -> str:
    """Pick the most defensible short genre label for a store_item.
    Preference order:
      1. The first entry in `tags` (weight-descending) whose name is
         a recognizable Steam genre (Action, Strategy, RPG, etc.).
      2. Any tag name from `tags[0]` if the tag_map resolves.
      3. Empty string.
    """
    if not isinstance(store_item, dict):
        return ''
    tags = store_item.get('tags') or []
    if not isinstance(tags, list):
        return ''
    for t in tags:
        if not isinstance(t, dict):
            continue
        tid = t.get('tagid')
        if tid is None:
            continue
        try:
            tid_i = int(tid)
        except (TypeError, ValueError):
            continue
        name = tag_map.get(tid_i)
        if name:
            return name
    return ''


def _pick_image_url(store_item: dict) -> str:
    """Build a full CDN URL for the item's best available capsule
    image. Preference: library_capsule (portrait, matches Meta Quest
    tile shape) > main_capsule > header > small_capsule."""
    if not isinstance(store_item, dict):
        return ''
    assets = store_item.get('assets') or {}
    if not isinstance(assets, dict):
        return ''
    fmt = (assets.get('asset_url_format') or '').strip()
    if not fmt or '${FILENAME}' not in fmt:
        return ''
    for key in ('library_capsule', 'main_capsule', 'header',
                'small_capsule', 'library_hero'):
        filename = (assets.get(key) or '').strip()
        if not filename:
            continue
        return _STEAM_ASSET_CDN + fmt.replace('${FILENAME}', filename)
    return ''


def _pick_publisher(store_item: dict) -> str:
    """Prefer the developer (surfaces the studio players recognize -
    Valve, Bungie, Larian, KRAFTON, etc.). Falls back to publisher
    when developer is missing."""
    bi = store_item.get('basic_info') or {}
    if not isinstance(bi, dict):
        return ''
    for key in ('developers', 'publishers'):
        entries = bi.get(key) or []
        if not isinstance(entries, list):
            continue
        for e in entries:
            if isinstance(e, dict):
                name = (e.get('name') or '').strip()
                if name:
                    return name
            elif isinstance(e, str) and e.strip():
                return e.strip()
    return ''


def _is_recent_release(store_item: dict, now_ts: int) -> bool:
    """True when steam_release_date sits within `_NEW_WINDOW_S` of now.
    Steam ships release dates as unix seconds under `release`."""
    rel = store_item.get('release') or {}
    if not isinstance(rel, dict):
        return False
    if rel.get('is_coming_soon'):
        return False
    ts = rel.get('steam_release_date') or rel.get('original_release_date')
    if not ts:
        return False
    try:
        ts_i = int(ts)
    except (TypeError, ValueError):
        return False
    if ts_i <= 0 or ts_i > now_ts:
        return False
    return (now_ts - ts_i) <= _NEW_WINDOW_S


def _hydrate_item(rank_entry: dict, store_item: Optional[dict],
                  tag_map: dict[int, str], bucket_rank: int,
                  now_ts: int, include_players: bool) -> Optional[dict]:
    """Combine a rank entry (from most_played or top_sellers) with its
    hydrated store_item into the row shape the Gaming panel renderer +
    game estimator expect. Returns None if we can't recover a title."""
    appid = rank_entry.get('appid')
    if not appid:
        return None
    title = ''
    if isinstance(store_item, dict):
        title = (store_item.get('name') or '').strip()
    if not title:
        preview = rank_entry.get('item_preview') or {}
        if isinstance(preview, dict):
            title = (preview.get('name') or '').strip()
    if not title:
        return None
    url_path = ''
    if isinstance(store_item, dict):
        url_path = (store_item.get('store_url_path') or '').strip()
    if not url_path:
        preview = rank_entry.get('item_preview') or {}
        if isinstance(preview, dict):
            url_path = (preview.get('store_url_path') or '').strip()
    store_url = (_APP_PAGE_URL_TMPL.format(store_url_path=url_path)
                 if url_path else f'https://store.steampowered.com/app/{appid}/')

    publisher = _pick_publisher(store_item or {})
    genre = _pick_primary_genre(store_item or {}, tag_map)
    image_url = _pick_image_url(store_item or {})
    is_new = _is_recent_release(store_item or {}, now_ts)

    row = {
        'rank':             int(rank_entry.get('rank') or bucket_rank),
        'bucket_rank':      bucket_rank,
        'title':            title,
        'url':              store_url,
        'image':            image_url,
        'publisher':        publisher,
        'genre':            genre,
        'product_id':       str(appid),
        'category_display': 'Game',
        'recently_added':   is_new,
        'is_new':           is_new,
        'last_week_rank':   int(rank_entry.get('last_week_rank') or 0),
    }
    if include_players:
        peak = int(rank_entry.get('peak_in_game') or 0)
        if peak > 0:
            row['current_players'] = peak
    return row


def _load_previous_snapshot() -> Optional[dict]:
    """Best-effort read of yesterday's snapshot so a bad fetch day
    still surfaces something in the dashboard (matches meta_quest's
    stale-from-previous pattern)."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(Bucket='dashboard-inputs',
                          Key='trends_iq_snapshots/latest/steam_charts.json')
        d = json.loads(o['Body'].read().decode('utf-8'))
        return d if isinstance(d, dict) else None
    except Exception as e:
        logger.info("steam_charts: could not read previous snapshot: %s", e)
        return None


def _mark_cookie_gap(reason: str = '') -> None:
    try:
        from .cookie_gap_notify import notify_cookie_gap
        notify_cookie_gap('steam_charts', 'steampowered.com', reason=reason)
    except Exception as e:
        logger.info("cookie_gap notify failed for steam_charts: %s", e)


def _fetch_source(source_key: str, rank_fn, label: str,
                  include_players: bool,
                  tag_map: dict[int, str],
                  hydrated_cache: dict[int, dict]) -> list[dict]:
    """Fetch one rail, batch-hydrate through GetItems (reusing the
    shared cache so a title on both rails is only fetched once), and
    return the ranked row list. `hydrated_cache` is a shared dict so
    the second call reuses the first call's hydration."""
    ranks = rank_fn()
    if not ranks:
        logger.warning("steam_charts: %s rank list empty", source_key)
        return []
    appids = [r['appid'] for r in ranks
              if r.get('appid') and r['appid'] not in hydrated_cache]
    if appids:
        fresh = _get_items_batch(appids)
        hydrated_cache.update(fresh)
    now_ts = int(time.time())
    items: list[dict] = []
    bucket_rank = 0
    for entry in ranks:
        appid = entry.get('appid')
        if not appid:
            continue
        bucket_rank += 1
        row = _hydrate_item(entry, hydrated_cache.get(appid), tag_map,
                            bucket_rank=bucket_rank, now_ts=now_ts,
                            include_players=include_players)
        if row is None:
            bucket_rank -= 1
            continue
        items.append(row)
        if len(items) >= _MAX_ITEMS_PER_PANEL:
            break
    logger.info("steam_charts: %s emitted %d items", label, len(items))
    return items


def fetch() -> dict[str, Any]:
    """Fetch both Most Played + Top Sellers rails and return the
    dashboard-shaped snapshot.

    Layout: `sources` is a dict keyed by panel slug. `trends_iq.
    _fetch_gaming_trending` looks up each panel via `(snapshot_slug,
    source_key)` mapping in `GAMING_PLATFORMS`.
    """
    tag_map = _load_tag_map()
    if not tag_map:
        logger.info("steam_charts: tag_list empty; genres will be blank")

    hydrated_cache: dict[int, dict] = {}
    sources: dict[str, dict] = {}
    prior: Optional[dict] = None
    total_items = 0

    rails = [
        ('steam_most_played', _fetch_most_played_ranks,
         'Steam - Most Played', True),
        ('steam_top_sellers', _fetch_top_sellers_ranks,
         'Steam - Top Sellers', False),
    ]
    for source_key, rank_fn, label, include_players in rails:
        items = _fetch_source(source_key, rank_fn, label,
                              include_players=include_players,
                              tag_map=tag_map,
                              hydrated_cache=hydrated_cache)
        if not items:
            if prior is None:
                prior = _load_previous_snapshot() or {}
            prev_panel = ((prior.get('sources') or {}).get(source_key) or {})
            prev_items = prev_panel.get('items') or []
            if prev_items:
                logger.info("steam_charts: %s empty -> using previous %d items",
                            label, len(prev_items))
                sources[source_key] = {
                    'label':               label,
                    'items':               prev_items,
                    'available':           bool(prev_items),
                    'stale_from_previous': True,
                    'soft_block_reason':   f'{label} live fetch returned 0 items',
                }
                continue
            _mark_cookie_gap(
                reason=f'steam_charts: {label} live fetch returned 0 items'
            )
        sources[source_key] = {
            'label':     label,
            'items':     items,
            'available': bool(items),
        }
        total_items += len(items)

    logger.info("steam_charts: emitting %d items across %d panels",
                total_items, len(sources))

    return {
        'national': [],
        'sources':  sources,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('steam_charts', 'Steam', 'gaming', fetch)
    total = sum(len(s.get('items') or [])
                for s in (result.get('sources') or {}).values())
    print(f"steam_charts: {total} items across "
          f"{len(result.get('sources') or {})} panels  "
          f"error={result.get('error')}", file=sys.stderr)
