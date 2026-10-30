"""Daily US level for the Books tab's store and library rails.

Jessie 2026-10-30: the chart owns the order, a national daily pool owns
the magnitude.

  stores     3M daily US book sales (print + ebook + audiobook) across
             Amazon, Apple Books and Audible
  libby      1.9M daily US public-library digital borrows: ebooks 77%,
             audiobooks 23% (OverDrive's annual loan split). Magazines
             keep their holds projection.

Store detail:

  pool       3,000,000 a day across the three stores
  split      Amazon 83.5%, Audible 11.5%, Apple Books 5%, from the unit
             mix (about 70% print, 20% ebook, 10% audio) and each
             store's share of its format
  #1         0.33% of its store's pool, the share Circana BookScan's
             weekly #1 takes of US units
  curve      the BookScan ladder normalised to #1 (#3 0.78, #4 0.54,
             #8 0.37, #10 0.34, #25 0.16, #50 0.073, #100 0.029),
             interpolated in log space, so the top 100 hold about 5%

A book that holds a reading for one of the three stores but is not on
that store's chart that day sits just under the chart: the level ten
places past its last row, never above that row.

Each row is salted +/-3% by title, store and day, never above the row
over it, and never ends in 0. Deterministic: the same chart on
the same day always gives the same numbers, which is what lets the
history rewrite and the nightly run agree.
"""
from __future__ import annotations

import hashlib
import math
from typing import Callable, Optional

POOL_DAILY = 3_000_000
PLATFORM_SHARE = {'amazon': 0.835, 'audible': 0.115, 'apple': 0.05}
LIBBY_POOL_DAILY = 1_900_000
LIBBY_SHARE = {'libby_ebook': 0.77, 'libby_audio': 0.23}
PLATFORM_DAILY = {**{k: POOL_DAILY * v for k, v in PLATFORM_SHARE.items()},
                  **{k: LIBBY_POOL_DAILY * v for k, v in LIBBY_SHARE.items()}}
STORE_PANELS = {'amazon': 'amazon', 'apple': 'apple', 'audible': 'audible'}
LIBBY_PANELS = {'ebook': 'libby_ebook', 'audiobook': 'libby_audio'}
TOP_TITLE_SHARE = 0.0033
LEVEL_TAG = 'us_book_sales_3m'

_LADDER = ((1, 1.0), (3, 0.776), (4, 0.537), (8, 0.373), (10, 0.343),
           (25, 0.156), (50, 0.073), (100, 0.029))


def _rel(rank: int) -> float:
    if rank <= 1:
        return 1.0
    for (a, va), (b, vb) in zip(_LADDER, _LADDER[1:]):
        if rank <= b:
            t = (math.log(rank) - math.log(a)) / (math.log(b) - math.log(a))
            return math.exp(math.log(va) + t * (math.log(vb) - math.log(va)))
    return _LADDER[-1][1] * _LADDER[-1][0] / rank


def rank_level(platform: str, rank: int) -> float:
    """Unsalted daily US sales for `rank` on `platform`'s chart."""
    return PLATFORM_DAILY[platform] * TOP_TITLE_SHARE * _rel(rank)


def _salt(key: str, platform: str, day: str) -> float:
    h = hashlib.sha256(f'{key}|{platform}|{day}|book_sales'.encode()).hexdigest()
    return 0.97 + (int(h[:8], 16) / 0xFFFFFFFF) * 0.06


def chart_levels(platform: str, keys: list, day: str) -> list:
    """One value per chart row, in chart order, never rising."""
    out, ceiling = [], None
    for i, key in enumerate(keys):
        v = int(round(rank_level(platform, i + 1) * _salt(key, platform, day)))
        if ceiling is not None:
            v = min(v, ceiling)
        v = max(v, 1)
        if v % 10 == 0 and v > 1:
            v -= 1
        out.append(v)
        ceiling = v
    return out


OFF_CHART_STEP = 10


def _set_block(entry: dict, platform: str, v: int) -> None:
    blk = entry.setdefault('by_platform', {}).setdefault(platform, {})
    for stale in ('method', 'derived_from'):
        blk.pop(stale, None)
    blk.update({'us_estimate': v,
                'us_estimate_low': max(1, int(v * 0.8)),
                'us_estimate_high': int(v * 1.25) + 1,
                'confidence': 'medium',
                'est_basis': 'sales_pool'})
    entry['sales_level'] = LEVEL_TAG


def _default_key(title: str, artist: str) -> str:
    from .stream_estimates import _cp_normalize
    return 'book:' + _cp_normalize(f'{title} {artist}')


def level(items: dict, book_charts: dict, day: str,
          key_fn: Optional[Callable[[str, str], str]] = None,
          panels: Optional[dict] = None) -> set:
    """Set the store reading of every chart row in `items` (a
    stream_estimates `items` dict) from its pool. `panels` maps chart
    source slug to platform key: STORE_PANELS for book_charts (default),
    LIBBY_PANELS for libby_trends. Rows missing from `items` get an
    entry. Returns the touched keys."""
    key_fn = key_fn or _default_key
    sources = (book_charts or {}).get('sources') or {}
    touched: set = set()
    for slug, platform in (panels or STORE_PANELS).items():
        panel = sources.get(slug) or {}
        label = panel.get('label') or slug
        rows, keys, seen = [], [], set()
        for it in (panel.get('items') or [])[:100]:
            title = (it.get('title') or '').strip()
            artist = (it.get('artist') or '').strip()
            if not title:
                continue
            key = key_fn(title, artist)
            if not key or key == 'book:' or key in seen:
                continue
            seen.add(key)
            rows.append((it, title, artist))
            keys.append(key)
        for rank, (key, v, (it, title, artist)) in enumerate(
                zip(keys, chart_levels(platform, keys, day), rows), start=1):
            entry = items.get(key)
            if not isinstance(entry, dict):
                entry = items[key] = {
                    'kind': 'book', 'display_title': title, 'artist': artist,
                    'chart_labels': [f'{label} #{rank}'], 'best_rank': rank,
                    'image': it.get('image'), 'url': it.get('url'),
                    'unit_label': 'daily US readers', 'confidence': 'medium',
                    'by_platform': {},
                }
            _set_block(entry, platform, v)
            touched.add(key)
        if not keys:
            continue
        on_chart = set(keys)
        floor_v = max(1, min(chart_levels(platform, keys, day)[-1],
                             int(rank_level(platform, len(keys) + OFF_CHART_STEP))))
        for key, entry in items.items():
            if (key in on_chart or not isinstance(entry, dict)
                    or entry.get('kind') != 'book'
                    or platform not in (entry.get('by_platform') or {})):
                continue
            v = max(1, min(floor_v, int(round(floor_v * _salt(key, platform, day)))))
            if v % 10 == 0 and v > 1:
                v -= 1
            _set_block(entry, platform, v)
            touched.add(key)
    for key in touched:
        entry = items[key]
        vals = [b for b in (entry.get('by_platform') or {}).values()
                if isinstance(b, dict) and b.get('us_estimate')]
        entry['us_estimate'] = sum(int(b['us_estimate']) for b in vals)
        entry['us_estimate_low'] = sum(int(b.get('us_estimate_low') or b['us_estimate']) for b in vals)
        entry['us_estimate_high'] = sum(int(b.get('us_estimate_high') or b['us_estimate']) for b in vals)
    return touched
