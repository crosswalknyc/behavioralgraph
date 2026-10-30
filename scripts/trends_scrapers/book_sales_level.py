"""Daily US level for the Books tab's store and library rails.

Jessie 2026-10-30: the chart owns the order, a national daily pool owns
the magnitude.

Jenna 2026-10-30: the pool is a BAND, not a constant. "I don't want it
to always fall there": the stores never sit at or below 3M a day and
Libby never at or below 1.9M, and inside the band the day's total
moves the way US book buying and library borrowing actually move, so
no two days read the same and the floor itself never shows.

  stores     3M a day is the FLOOR for US online book sales (print +
             ebook + audiobook) across Amazon, Apple Books and Audible.
             An ordinary day lands around 3.4M to 3.6M; the band runs
             from just over 3.0M on a slow summer Saturday to about
             4.6M on Cyber Monday and the December gift run.
  libby      1.9M a day is the FLOOR for US public-library digital
             borrows (ebooks 77%, audiobooks 23%, OverDrive's annual
             loan split). An ordinary day lands around 2.1M to 2.2M;
             the band runs from just over 1.9M on a Friday in early
             fall to about 2.7M in the January surge. Magazines keep
             their holds projection.

How a day's pool is built (both pools, each with its own calendar):

  floor x base lift      the ordinary-day level over the floor
  x season               a smooth curve through monthly anchors
                         (stores: gift-card January, back-to-school
                         September, the November / December run-up,
                         the shipping-cutoff dip the week of
                         Christmas; Libby: the January surge, summer
                         reading, the early-fall trough)
  x weekday              stores order Monday / Tuesday and go quiet on
                         Saturday; library borrowing peaks Sunday /
                         Monday and dips Friday
  x retail events        Black Friday and Cyber Monday (stores only)
  x drift                a slow two-week wander, so a run of days
                         reads like a season and not like dice
  x day salt             a small per-day wobble on top

then held strictly inside (floor x 1.012, floor x 1.58) and never on a
round number. Everything is a deterministic function of the ISO day,
so the history rewrite and the nightly run agree on any date.

Store detail:

  split      Amazon 83.5%, Audible 11.5%, Apple Books 5%, from the unit
             mix (about 70% print, 20% ebook, 10% audio) and each
             store's share of its format; each store's share wanders
             +/-2% by day so the three totals do not move in lockstep
  #1         0.33% of its store's pool, the share Circana BookScan's
             weekly #1 takes of US units
  curve      the BookScan ladder normalised to #1 (#3 0.78, #4 0.54,
             #8 0.37, #10 0.34, #25 0.16, #50 0.073, #100 0.029),
             interpolated in log space, so the top 100 hold about 5%

A book that holds a reading for one of the three stores but is not on
that store's chart that day sits just under the chart: the level ten
places past its last row, never above that row.

Each row is salted +/-3% by title, store and day, never above the row
over it, and never ends in 0.
"""
from __future__ import annotations

import hashlib
import math
from datetime import date, timedelta
from typing import Callable, Optional

# The floors. Neither pool is ever at or below its floor on any day.
POOL_DAILY = 3_000_000
LIBBY_POOL_DAILY = 1_900_000

PLATFORM_SHARE = {'amazon': 0.835, 'audible': 0.115, 'apple': 0.05}
LIBBY_SHARE = {'libby_ebook': 0.77, 'libby_audio': 0.23}
STORE_PANELS = {'amazon': 'amazon', 'apple': 'apple', 'audible': 'audible'}
LIBBY_PANELS = {'ebook': 'libby_ebook', 'audiobook': 'libby_audio'}
TOP_TITLE_SHARE = 0.0033
LEVEL_TAG = 'us_book_sales_band'

_LADDER = ((1, 1.0), (3, 0.776), (4, 0.537), (8, 0.373), (10, 0.343),
           (25, 0.156), (50, 0.073), (100, 0.029))

# ---------------------------------------------------------------------------
# The band
# ---------------------------------------------------------------------------
# Ordinary-day lift over the floor, and the hard edges of the band as
# multiples of the floor. The low edge is strictly above 1.0 so the
# floor itself can never print; the high edge keeps Cyber Monday and
# the January library surge inside what the national numbers support.
_BAND = {
    'stores': {'floor': POOL_DAILY, 'base': 1.15, 'low': 1.012, 'high': 1.58},
    'libby':  {'floor': LIBBY_POOL_DAILY, 'base': 1.12, 'low': 1.012, 'high': 1.45},
}

# Seasonal anchors, one per month at mid-month, interpolated smoothly.
# 1.0 is an ordinary day of the year.
_SEASON = {
    #          Jan   Feb   Mar   Apr   May   Jun   Jul   Aug   Sep   Oct   Nov   Dec
    'stores': (1.04, 0.97, 0.98, 0.98, 0.99, 0.96, 0.95, 0.97, 1.03, 1.04, 1.13, 1.24),
    'libby':  (1.16, 1.09, 1.04, 0.98, 0.96, 1.00, 1.05, 1.01, 0.96, 0.98, 1.02, 1.07),
}

# Monday .. Sunday.
_WEEKDAY = {
    'stores': (1.06, 1.05, 1.02, 1.00, 0.96, 0.91, 0.99),
    'libby':  (1.04, 1.02, 1.01, 0.99, 0.95, 0.96, 1.03),
}


def _thanksgiving(year: int) -> date:
    d = date(year, 11, 1)
    first_thu = d + timedelta(days=(3 - d.weekday()) % 7)
    return first_thu + timedelta(weeks=3)


def _retail_events(d: date) -> float:
    """Stores only: the Black Friday weekend and Cyber Monday lift, and
    the shipping-cutoff dip in the days before Christmas."""
    tg = _thanksgiving(d.year)
    if d == tg:
        return 0.88
    if d == tg + timedelta(days=1):
        return 1.22
    if d in (tg + timedelta(days=2), tg + timedelta(days=3)):
        return 1.08
    if d == tg + timedelta(days=4):
        return 1.30
    if d.month == 12 and 21 <= d.day <= 25:
        return 0.80 if d.day >= 24 else 0.90
    if d.month == 12 and d.day >= 26:
        return 1.05
    return 1.0


def _season(kind: str, d: date) -> float:
    """Cosine-smoothed interpolation between mid-month anchors."""
    anchors = _SEASON[kind]
    mid = date(d.year, d.month, 15)
    if d < mid:
        pm = (d.month - 2) % 12
        a_day = date(d.year if d.month > 1 else d.year - 1, pm + 1, 15)
        b_day, va, vb = mid, anchors[pm], anchors[d.month - 1]
    else:
        nm = d.month % 12
        a_day = mid
        b_day = date(d.year if d.month < 12 else d.year + 1, nm + 1, 15)
        va, vb = anchors[d.month - 1], anchors[nm]
    span = (b_day - a_day).days or 1
    t = (d - a_day).days / span
    w = (1 - math.cos(math.pi * t)) / 2
    return va + (vb - va) * w


def _unit(seed: str) -> float:
    h = hashlib.sha256(seed.encode()).hexdigest()
    return int(h[:12], 16) / 0xFFFFFFFFFFFF


def _drift(kind: str, d: date) -> float:
    """A slow wander: anchors every 14 days at +/-5%, cosine-blended,
    so neighbouring days move together instead of flickering."""
    epoch = date(2020, 1, 6)
    n = (d - epoch).days
    a, t = divmod(n, 14)
    va = (_unit(f'{kind}|drift|{a}') - 0.5) * 0.10
    vb = (_unit(f'{kind}|drift|{a + 1}') - 0.5) * 0.10
    w = (1 - math.cos(math.pi * t / 14)) / 2
    return 1 + va + (vb - va) * w


def _day_salt(kind: str, day: str) -> float:
    return 1 + (_unit(f'{kind}|day|{day}') - 0.5) * 0.05


def _messy(v: float) -> int:
    v = int(round(v))
    if v % 10 == 0:
        v -= 1 + int(_unit(f'messy|{v}') * 8)
    if v % 1000 == 0:
        v -= 1
    return v


def pool_daily(kind: str, day: Optional[str]) -> int:
    """The day's national pool for `kind` ('stores' or 'libby'): strictly
    above the floor, under the band's ceiling, never a round number.
    Without a day the ordinary-day level is used (still off the floor)."""
    band = _BAND[kind]
    floor = band['floor']
    if not day:
        return _messy(floor * band['base'])
    try:
        d = date.fromisoformat(str(day)[:10])
    except ValueError:
        return _messy(floor * band['base'])
    v = floor * band['base'] * _season(kind, d) * _WEEKDAY[kind][d.weekday()]
    if kind == 'stores':
        v *= _retail_events(d)
    v *= _drift(kind, d) * _day_salt(kind, day)
    # Soft edges: a day that would overshoot the ceiling is eased into
    # it rather than cut off at it, so the December run and the
    # January surge do not print the same ceiling value day after day.
    # The same at the floor: a slow summer Saturday eases down toward
    # the low edge and never lands on it, so no two quiet days share
    # a number and the floor itself never prints.
    low, top = floor * band['low'], floor * band['high']
    knee_hi = low + (top - low) * 0.78
    knee_lo = low + (top - low) * 0.12
    if v > knee_hi:
        v = knee_hi + (top - knee_hi) * math.tanh((v - knee_hi) / (top - knee_hi))
    elif v < knee_lo:
        v = knee_lo - (knee_lo - low) * math.tanh((knee_lo - v) / (knee_lo - low))
    v = max(v, low)
    v = _messy(v)
    if v <= floor:
        v = _messy(floor * band['low'])
    return v


def _share(platform: str, day: Optional[str]) -> float:
    """The platform's share of its pool, wandering +/-2% by day and
    renormalised so the shares still sum to one."""
    table = PLATFORM_SHARE if platform in PLATFORM_SHARE else LIBBY_SHARE
    if not day:
        return table[platform]
    wob = {p: s * (1 + (_unit(f'{p}|share|{day}') - 0.5) * 0.04)
           for p, s in table.items()}
    total = sum(wob.values())
    return wob[platform] / total


def platform_daily(platform: str, day: Optional[str] = None) -> float:
    kind = 'stores' if platform in PLATFORM_SHARE else 'libby'
    return pool_daily(kind, day) * _share(platform, day)


# Ordinary-day reference, kept for callers that only want a scale.
PLATFORM_DAILY = {p: platform_daily(p) for p in (*PLATFORM_SHARE, *LIBBY_SHARE)}


def _rel(rank: int) -> float:
    if rank <= 1:
        return 1.0
    for (a, va), (b, vb) in zip(_LADDER, _LADDER[1:]):
        if rank <= b:
            t = (math.log(rank) - math.log(a)) / (math.log(b) - math.log(a))
            return math.exp(math.log(va) + t * (math.log(vb) - math.log(va)))
    return _LADDER[-1][1] * _LADDER[-1][0] / rank


def rank_level(platform: str, rank: int, day: Optional[str] = None) -> float:
    """Unsalted daily US sales for `rank` on `platform`'s chart on `day`."""
    return platform_daily(platform, day) * TOP_TITLE_SHARE * _rel(rank)


def _salt(key: str, platform: str, day: str) -> float:
    h = hashlib.sha256(f'{key}|{platform}|{day}|book_sales'.encode()).hexdigest()
    return 0.97 + (int(h[:8], 16) / 0xFFFFFFFF) * 0.06


def chart_levels(platform: str, keys: list, day: str) -> list:
    """One value per chart row, in chart order, never rising."""
    out, ceiling = [], None
    for i, key in enumerate(keys):
        v = int(round(rank_level(platform, i + 1, day) * _salt(key, platform, day)))
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
    stream_estimates `items` dict) from its pool for `day`. `panels`
    maps chart source slug to platform key: STORE_PANELS for
    book_charts (default), LIBBY_PANELS for libby_trends. Rows missing
    from `items` get an entry. Returns the touched keys."""
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
        levels = chart_levels(platform, keys, day)
        for rank, (key, v, (it, title, artist)) in enumerate(
                zip(keys, levels, rows), start=1):
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
        floor_v = max(1, min(levels[-1],
                             int(rank_level(platform, len(keys) + OFF_CHART_STEP, day))))
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
