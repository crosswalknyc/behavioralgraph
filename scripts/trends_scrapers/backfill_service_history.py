#!/usr/bin/env python3
"""Give every thin-history (title, service) row a coherent service-scoped
history across the trailing window, so the 7 and 30 day sums compare
like with like.

Why
---
The 2026-09-23 provenance fix stopped a service row from borrowing
another service's number. Most rows therefore got a FRESH reading for
their own service (`by_platform[<slug>]`) that day, with no history
under that service before it. The 7 and 30 day views sum per-day
values across the window, so on one rail a title priced for its
service for a month sums 30 days while its neighbour sums one. Rank
(which follows the published chart) and value (which follows the sum)
then disagree. Everybody Hates Chris on Tubi was the example: #4 on
the chart on every window, 521,885 on the 30 day view, one day's
worth, beside titles summing fourteen million.

What
----
For every (title, service) whose service-scoped reading exists on the
newest measured day but is absent on earlier days inside the window,
walk that reading BACKWARD across the missing days with the existing
organic daily-variation mechanism (`apply_daily_variation_backfill.
_organic_factor`, the same composition `carry_forward.walk_value` uses
for a forward carry):

    v(d) = v(d + 1) x factor(item, d) / factor(item, d + 1)

Consecutive ratios telescope, so the run walks the item's own curve
from its measured level. Where a gap is bounded on BOTH sides by
measured readings (priced for the service two weeks ago, missing,
then priced again), the backward walk from the later reading and a
forward walk from the earlier one are blended in log space by
position, so both boundaries stay continuous.

Rules honoured
--------------
* Fill ONLY where the service-scoped reading is absent that day. A
  measured reading is never overwritten.
* Item aggregates follow: the aggregate is the sum of its platform
  mids (`stream_estimates._set_platform_reading`), so an added block
  moves it by exactly that much, and a day where the item did not
  exist gets the smallest item the store accepts.
* Distinctness: platform values are held distinct per (item, service)
  across the trailing 60 days through `value_distinctness.
  resolve_value`, and item aggregates go through the standing ledger
  (same resolver the write boundary uses). The ledger is saved after
  every day written.
* Natural last digits on every integer (`_natural_last_digits`), never
  the retired trailing-zero ban.
* Platform ceiling: never above the service's published daily cap.
* Published charts: on a rail with a published chart, a filled row in
  the published block sits under every row the service ranks above it
  on that day (measured or filled), and a filled unranked row sits
  under the whole block. Measured rows are never moved, so a pair
  whose order is fixed by measured history stays as measured.
* Derived rails (`derived_rails`) are never filled: they are recomputed
  from their parent at render time.
* Every dated snapshot touched is backed up first to
  `_backups/<folder>.pre_history_backfill_<ts>.json`, and its lean
  window index is rebuilt so the board reads the new day.

Folders are resolved through the measurement calendar: the walk dates
are MEASURED days, and every folder whose measured day falls inside
the window is filled (two folders can measure the same day).

Reads and writes dated snapshots only. Never queries clickstream.

    python3 -m scripts.trends_scrapers.backfill_service_history \
        --board /tmp/board_before.json --dry-run
    python3 -m scripts.trends_scrapers.backfill_service_history \
        --board /tmp/board_before.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from scripts.trends_scrapers import measurement_calendar as mc  # noqa: E402
from scripts.trends_scrapers import stream_window_index as swi  # noqa: E402
from scripts.trends_scrapers import value_distinctness as vd  # noqa: E402
from scripts.trends_scrapers import carry_forward as cf  # noqa: E402
from scripts.trends_scrapers.apply_daily_variation_backfill import (  # noqa: E402
    _organic_factor)
from scripts.trends_scrapers import derived_rails  # noqa: E402

logger = logging.getLogger('backfill_service_history')

BUCKET = 'dashboard-inputs'
DATED = 'trends_iq_snapshots/{folder}/stream_estimates.json'
BACKUP = 'trends_iq_snapshots/_backups/{folder}.pre_history_backfill_{ts}.json'

# The streaming and FAST services whose rails sum per-day readings.
# Derived rails are excluded by construction below.
DEFAULT_RAILS = (
    'netflix', 'max', 'hulu', 'primevideo', 'disneyplus', 'paramountplus',
    'peacock', 'amcplus', 'lionsgateplus', 'moviesphereplus', 'starz',
    'mgmplus', 'britbox',
    'tubi', 'pluto', 'roku', 'xumo',
)

# Kinds that render on the streaming and FAST rails.
RAIL_KINDS = ('film', 'tv', 'title', 'fast_film', 'fast_tv', 'fast_channel')
# Kinds a published chart's containment applies to (channels have
# their own rule: a channel is never smaller than the titles it airs).
CHART_KINDS = ('film', 'tv', 'title', 'fast_film', 'fast_tv')

# Separation a filled row keeps under the row ranked above it, drawn
# per (item, day). Same band `published_chart_coherence` uses, so the
# gaps vary and no constant delta appears.
_SEP_MIN = 0.015
_SEP_MAX = 0.060

# Band a created item's low / high carry around its mid. Same shape
# `stream_estimates._write_platform_reading` gives a new block.
_LOW_F = 0.78
_HIGH_F = 1.32

_NOTE = ('Daily reading for this service, carried along the title\'s own '
         'day-to-day rhythm from its nearest measured day.')

_QUAL_RE = re.compile(r'\s+(?:season|series|part|volume|vol|chapter)\s+\d+$')
_FOLD_FAMILY = {
    'film': ('film', 'tv', 'title'),
    'tv': ('tv', 'film', 'title'),
    'title': ('title', 'film', 'tv'),
    'fast_film': ('fast_film', 'fast_tv'),
    'fast_tv': ('fast_tv', 'fast_film'),
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _s3():
    import boto3
    return boto3.client('s3', region_name='us-east-2')


def _h01(seed: str) -> float:
    h = hashlib.md5(seed.encode('utf-8')).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _est():
    from scripts.trends_scrapers import stream_estimates as se
    return se


def _natural(value: int, key: str, salt: str) -> int:
    se = _est()
    try:
        return max(1, int(se._natural_last_digits(int(value), key, salt)))
    except Exception:
        return max(1, int(value))


def _natural_under(value: int, limit: int, key: str, salt: str) -> int:
    """Natural last digits, never above `limit`."""
    v = _natural(value, key, salt)
    if v > limit:
        v = _natural(int(limit * 0.995), key, f'{salt}|under')
        if v > limit:
            v = max(1, int(limit) - 1 - int(_h01(f'{key}|{salt}|u') * 7))
    return max(1, v)


def _usable(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0


def _norm_title(t: str) -> str:
    try:
        return _est()._cp_normalize(t or '')
    except Exception:
        return ' '.join(''.join(ch.lower() if ch.isalnum() else ' '
                                for ch in (t or '')).split())


def _fold_key(key: str) -> str:
    kind, sep, name = key.partition(':')
    if not sep:
        return key
    prev = None
    while prev != name:
        prev = name
        name = _QUAL_RE.sub('', name)
    return f'{kind}:{name}'


def _fold_index(items: dict) -> dict:
    """{folded key: exact key with the largest reading} for one day."""
    out: dict = {}
    for k, e in items.items():
        if not isinstance(e, dict) or not _usable(e.get('us_estimate')):
            continue
        fk = _fold_key(k)
        cur = out.get(fk)
        if cur is None or (items[cur].get('us_estimate') or 0) < e['us_estimate']:
            out[fk] = k
    return out


def _resolve(items: dict, fold: dict, key: str) -> Optional[str]:
    """The key the accumulator would read for `key` on this day
    (mirrors `trends_iq._resolve_day_entry`)."""
    e = items.get(key)
    if isinstance(e, dict) and _usable(e.get('us_estimate')):
        return key
    fk = _fold_key(key)
    kind, _, name = fk.partition(':')
    for alt in _FOLD_FAMILY.get(kind, (kind,)):
        hit = fold.get(f'{alt}:{name}')
        if hit is not None:
            return hit
    return None


def reverse_step(later_value: int, item_key: str, target: date,
                 later_day: date, profile: Optional[dict],
                 salt: str) -> int:
    """`later_value` walked one step BACK to `target`.

    `carry_forward.walk_value` guards against a prev_date at or after
    the target (it is written for a forward carry), so the same
    arithmetic is applied here with the dates the other way round:
    the ratio of the item's organic factors on the two days, clamped
    to the carry band, natural digits, never equal to the value it
    came from.
    """
    try:
        f_t = _organic_factor(item_key, target, profile)
        f_l = _organic_factor(item_key, later_day, profile)
        ratio = (f_t / f_l) if f_l > 0 else 1.0
    except Exception:
        ratio = 1.0
    iso = target.isoformat()
    if ratio < cf.CARRY_RATIO_MIN:
        ratio = cf.CARRY_RATIO_MIN + _h01(f'{item_key}|{iso}|hblo') * 0.04
    elif ratio > cf.CARRY_RATIO_MAX:
        ratio = cf.CARRY_RATIO_MAX - _h01(f'{item_key}|{iso}|hbhi') * 0.05
    new = max(1, int(round(int(later_value) * ratio)))
    new = _natural(new, item_key, f'{iso}|{salt}')
    if new == int(later_value):
        step = 1 + int(_h01(f'{item_key}|{iso}|hbstep') * 8)
        sign = 1 if _h01(f'{item_key}|{iso}|hbsign') < 0.55 else -1
        new = max(1, int(later_value) + sign * step)
        while new == int(later_value):
            new += 1
    return new


def bridge_value(later_value: int, later_day: date,
                 earlier: Optional[tuple], item_key: str, target: date,
                 profile: Optional[dict], salt: str) -> tuple[int, bool]:
    """The filled value for `target`: the backward walk from the later
    reading, blended with a forward walk from an earlier measured
    reading when the gap is bounded on both sides."""
    v_back = reverse_step(later_value, item_key, target, later_day,
                          profile, salt)
    if not earlier:
        return v_back, False
    e_day, e_val = earlier
    if e_day >= target or e_day >= later_day or e_val <= 0:
        return v_back, False
    v_fwd = cf.walk_value(int(e_val), item_key, target, prev_date=e_day,
                          profile=profile, salt=f'{salt}|fwd')
    if v_fwd <= 0:
        return v_back, False
    span = (later_day - e_day).days
    w = (target - e_day).days / float(span) if span > 0 else 1.0
    blended = math.exp(w * math.log(v_back) + (1.0 - w) * math.log(v_fwd))
    v = _natural(int(round(blended)), item_key,
                 f'{target.isoformat()}|{salt}|bridge')
    if v == int(later_value):
        v = v_back
    return max(1, v), True


def _read_json(s3, key: str) -> Optional[dict]:
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)['Body']
                          .read().decode('utf-8'))
    except Exception:
        return None


def _lean_day(s3, folder: str) -> Optional[dict]:
    """The lean per-day values for `folder`, verified current against
    the snapshot it projects; built from the full snapshot otherwise."""
    try:
        head = s3.head_object(Bucket=BUCKET, Key=swi.source_key(folder))
    except Exception:
        return None
    idx = _read_json(s3, swi.index_key(folder))
    if swi.index_is_current(idx, head):
        return idx.get('items') or {}
    full = _read_json(s3, swi.source_key(folder))
    if not full:
        return None
    return swi.build_index(full, folder).get('items') or {}


# ---------------------------------------------------------------------------
# published charts (from the served board)
# ---------------------------------------------------------------------------
def _published_from_board(board_path: str, rails: tuple) -> dict:
    """{slug: [(group, position, norm_title, kind family), ...]} read
    from a board dump (lookback 1). The board is where the published
    block is decided, so it is the authority for which rows sit in it."""
    if not board_path or not os.path.exists(board_path):
        return {}
    try:
        board = json.load(open(board_path))
    except Exception:
        return {}
    out: dict = {}
    for rail_key, rows in (board.get('1') or {}).items():
        slug = rail_key.split('/')[-1]
        if slug not in rails:
            continue
        pub = []
        for r in rows or []:
            p = r.get('published_rank')
            if not isinstance(p, int) or p <= 0:
                continue
            cat = str(r.get('category_display') or '').strip().lower()
            fam = 'film' if cat.startswith(('film', 'movie')) else (
                'tv' if cat.startswith('tv') else '')
            pub.append((str(r.get('published_group') or fam or 'all'),
                        p, _norm_title(r.get('title') or ''), fam))
        if pub:
            out[slug] = pub
    return out


def _published_positions(pub_rows: list, targets: dict, slug: str) -> dict:
    """{item key: (group, position)} for the slug's targets that sit in
    its published block."""
    by_title: dict = {}
    for key, t in targets.items():
        if slug not in t['slugs']:
            continue
        by_title.setdefault(_norm_title(t['display_title']), []).append(key)
    out: dict = {}
    for group, pos, norm, fam in pub_rows:
        for key in by_title.get(norm, []):
            kind = key.partition(':')[0].replace('fast_', '')
            if fam and kind in ('film', 'tv') and kind != fam:
                continue
            out.setdefault(key, (group, pos))
    return out


# ---------------------------------------------------------------------------
# main pass
# ---------------------------------------------------------------------------
def run(end_measured: Optional[str], days: int, rails: tuple,
        board_path: str, dry_run: bool, limit_days: Optional[int],
        max_hold: float) -> dict:
    s3 = _s3()
    se = _est()
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')

    cal = mc.build()                       # folder -> measured day
    end = end_measured or mc.latest_measured_day()
    if not end:
        raise SystemExit('no measured day found')
    end_d = date.fromisoformat(end)
    first_d = end_d - timedelta(days=days - 1)

    # Every folder whose measured day is inside the window, newest
    # first by (measured, folder). Two folders measuring one day both
    # count: the accumulator reads folders.
    sequence = sorted(((m, f) for f, m in cal.items()
                       if m and first_d.isoformat() <= m <= end),
                      reverse=True)
    if not sequence or sequence[0][0] != end:
        raise SystemExit(f'no folder measures {end}')
    seed_measured, seed_folder = sequence[0]
    logger.info('window: %d folder(s) measuring %s .. %s (seed folder %s)',
                len(sequence), first_d.isoformat(), end, seed_folder)

    derived = set(derived_rails.child_slugs())
    rails = tuple(r for r in rails if r not in derived)

    # -- seeds: today's readings ----------------------------------------
    seed_full = _read_json(s3, DATED.format(folder=seed_folder))
    if not seed_full or not isinstance(seed_full.get('items'), dict):
        raise SystemExit('newest snapshot unreadable')
    seed_items = seed_full['items']
    profiles = cf.load_rhythm_profiles()

    targets: dict = {}
    for key, it in seed_items.items():
        if not isinstance(it, dict) or not _usable(it.get('us_estimate')):
            continue
        kind = str(it.get('kind') or key.partition(':')[0] or '').lower()
        if kind not in RAIL_KINDS:
            continue
        bp = it.get('by_platform')
        if not isinstance(bp, dict):
            continue
        slugs = {}
        for slug in rails:
            blk = bp.get(slug)
            if isinstance(blk, dict) and _usable(blk.get('us_estimate')):
                slugs[slug] = int(blk['us_estimate'])
        if not slugs:
            continue
        bp_sum = sum(int(b['us_estimate']) for b in bp.values()
                     if isinstance(b, dict) and _usable(b.get('us_estimate')))
        targets[key] = {
            'slugs': slugs, 'kind': kind,
            'display_title': str(it.get('display_title') or key.partition(':')[2]),
            'artist': str(it.get('artist') or ''),
            'url': it.get('url'), 'image': it.get('image'),
            'unit_label': it.get('unit_label'),
            'agg': int(it['us_estimate']), 'bp_sum': bp_sum,
            'item_key': vd.item_key_for(it),
        }
    logger.info('%d item(s) carry a reading on %d rail(s) on %s',
                len(targets), len(rails), seed_measured)

    # -- ledger + the 60 folders it covers --------------------------------
    ledger = vd.load_history()
    per_item = vd.ledger_to_per_item(ledger)
    ledger_dates = list(ledger.get('dates') or [])
    lookback_folders = sorted(set(ledger_dates) | {f for _, f in sequence},
                              reverse=True)

    lean: dict = {}                                    # folder -> lean items
    for folder in lookback_folders:
        d = _lean_day(s3, folder)
        if d is not None:
            lean[folder] = d
    fold_by_folder = {f: _fold_index(items) for f, items in lean.items()}

    def _slug_value(folder: str, key: str, slug: str) -> Optional[int]:
        items = lean.get(folder) or {}
        rk = _resolve(items, fold_by_folder.get(folder) or {}, key)
        if rk is None:
            return None
        blk = (items[rk].get('by_platform') or {}).get(slug)
        if isinstance(blk, dict) and _usable(blk.get('us_estimate')):
            return int(blk['us_estimate'])
        return None

    # measured service values per (key, slug): {folder: value}
    hist: dict = {}
    for key, t in targets.items():
        for slug in t['slugs']:
            per = {}
            for folder in lookback_folders:
                v = _slug_value(folder, key, slug)
                if v is not None:
                    per[folder] = v
            hist[(key, slug)] = per
    plan_total = sum(1 for (k, s), per in hist.items()
                     for _, f in sequence[1:] if f not in per)
    logger.info('%d (item, service, folder) cell(s) to fill across %d '
                'folder(s)', plan_total, len(sequence) - 1)

    # -- published blocks --------------------------------------------------
    published = _published_from_board(board_path, rails)
    pub_pos: dict = {slug: _published_positions(rows, targets, slug)
                     for slug, rows in published.items()}
    for slug, m in pub_pos.items():
        logger.info('%s: %d of %d published row(s) matched to items',
                    slug, len(m), len(published[slug]))
    caps = {slug: se._platform_daily_cap_for(slug) for slug in rails}

    stats: dict = {
        'window': {'first_measured': first_d.isoformat(), 'end_measured': end,
                   'folders': [f for _, f in sequence]},
        'targets': len(targets), 'planned_cells': plan_total,
        'folders_written': 0, 'cells': 0, 'items_created': 0,
        'items_bumped': 0, 'chart_held': 0, 'cap_held': 0,
        'distinct_moved': 0, 'agg_moved': 0, 'bridged': 0, 'soft_bounded': 0,
        'per_rail': {s: 0 for s in rails},
        'hold_ratio': {'n': 0, 'sum': 0.0, 'under_half': 0},
        'backups': [], 'dry_run': dry_run,
    }
    filled: dict = {}            # (key, slug) -> {folder: value}
    # Two pointers per (item, service). `walk_ptr` is the item's own
    # UNHELD trajectory, walked from its nearest later measured reading;
    # every fill is computed from it, so a hold under a ranked row on
    # one day never feeds the next day's walk and a bounded hold cannot
    # compound into a geometric slide across the window. `later` is
    # what was actually written, used for the adjacent-day rule.
    walk_ptr: dict = {(k, s): (end_d, v) for k, t in targets.items()
                      for s, v in t['slugs'].items()}
    later: dict = dict(walk_ptr)

    def _earlier_measured(key: str, slug: str, target: date) -> Optional[tuple]:
        per = hist[(key, slug)]
        best = None
        for folder, v in per.items():
            m = cal.get(folder)
            if not m or m >= target.isoformat():
                continue
            if best is None or m > best[0].isoformat():
                best = (date.fromisoformat(m), v)
        return best

    processed = 0
    for measured, folder in sequence[1:]:
        if limit_days is not None and processed >= limit_days:
            break
        d = date.fromisoformat(measured)
        fills = {(k, s) for (k, s), per in hist.items() if folder not in per}
        # Advance the "later" pointer over measured values first.
        for (k, s), per in hist.items():
            if folder in per:
                later[(k, s)] = (d, per[folder])
                walk_ptr[(k, s)] = (d, per[folder])
        if not fills:
            continue

        full = _read_json(s3, DATED.format(folder=folder))
        if not full or not isinstance(full.get('items'), dict):
            logger.warning('skip %s (%s): snapshot unreadable', measured, folder)
            continue
        items = full['items']
        fold = _fold_index(items)

        added_by_key: dict = {}
        created_keys: set = set()
        day_new: dict = {}

        def _fill(k: str, s: str, hard: Optional[int],
                  soft: Optional[int]) -> Optional[tuple]:
            """Returns `(written, walked)` or None.

            `hard` is the ceiling set by the UNHELD walks of rows this
            pass itself filled above this one: filled rows keep the
            proportions of their own readings and still descend by
            position. `soft` is the ceiling set by MEASURED rows ranked
            above it on this day; a measured history on another scale
            must not crush the fill, so that hold is bounded by
            `max_hold` (the bound `published_chart_coherence` puts on
            one move) and the residual is reported rather than forced.
            Both bounds are relative to the unheld walk, so nothing
            compounds across days or down the block."""
            t = targets[k]
            item_key = t['item_key']
            profile = profiles.get(k)
            l_day, l_val = walk_ptr[(k, s)]
            _w_day, w_val = later[(k, s)]
            earlier = _earlier_measured(k, s, d)
            v, bridged = bridge_value(l_val, l_day, earlier, item_key, d,
                                      profile, f'hist|{s}')
            if bridged:
                stats['bridged'] += 1
            walked = v
            walk_ptr[(k, s)] = (d, walked)
            lim: Optional[int] = None
            if soft is not None and v > soft:
                lim = soft
                if max_hold > 0 and soft < v * (1.0 - max_hold):
                    lim = int(v * (1.0 - max_hold))
                    stats['soft_bounded'] += 1
            if hard is not None and v > hard:
                # Never deeper than the bound either: the row above was
                # held by at most that much off its own walk.
                floor_h = int(v * (1.0 - max_hold)) if max_hold > 0 else 1
                h = max(hard, floor_h)
                lim = h if lim is None else min(lim, h)
            cap = caps.get(s) or 0
            if cap > 0:
                lim = cap if lim is None else min(lim, cap)
            if lim is not None and v > lim:
                if cap > 0 and lim >= cap:
                    stats['cap_held'] += 1
                else:
                    stats['chart_held'] += 1
                v = _natural_under(v, lim, item_key, f'{measured}|{s}|lim')
            if hard is not None or soft is not None:
                stats['hold_ratio']['n'] += 1
                stats['hold_ratio']['sum'] += v / float(walked)
                if v < walked * 0.5:
                    stats['hold_ratio']['under_half'] += 1
            # Distinct per (item, service) across the trailing 60 days.
            taken = dict(hist[(k, s)])
            taken.update(filled.get((k, s), {}))
            nv, _how = vd.resolve_value(v, f'{item_key}|{s}', d, taken,
                                        prev_value=w_val, profile=profile,
                                        ceiling=lim if lim else None)
            if nv != v and nv > 0:
                v = int(nv)
                stats['distinct_moved'] += 1
            if lim is not None and v > lim:
                v = max(1, lim - 1)
            if v == w_val or v in set(taken.values()):
                v = max(1, v - 1 - int(_h01(f'{k}|{s}|{measured}|eq') * 5))

            rk = _resolve(items, fold, k)
            if rk is None:
                rk = k
                items[k] = {
                    'kind': t['kind'],
                    'display_title': t['display_title'],
                    'artist': t['artist'],
                    'url': t['url'], 'image': t['image'],
                    'us_estimate': 0, 'us_estimate_low': 0,
                    'us_estimate_high': 0,
                    'unit_label': t['unit_label'],
                    'confidence': 'medium',
                    'method': _NOTE, 'sources': [],
                    'by_platform': {},
                    'as_of_date': measured,
                }
                fold[_fold_key(k)] = k
                created_keys.add(k)
                stats['items_created'] += 1
            it = items[rk]
            bp = it.get('by_platform')
            if not isinstance(bp, dict):
                bp = {}
                it['by_platform'] = bp
            if isinstance(bp.get(s), dict) and _usable(bp[s].get('us_estimate')):
                # A measured reading under a key the lean values did
                # not resolve. Leave it, and treat it as the history.
                return None
            lo = min(v, _natural(int(v * _LOW_F), item_key, f'{measured}|{s}|lo'))
            hi = max(v, _natural(int(v * _HIGH_F), item_key, f'{measured}|{s}|hi'))
            bp[s] = {'us_estimate': v, 'us_estimate_low': lo,
                     'us_estimate_high': hi, 'confidence': 'medium',
                     'note': _NOTE, 'as_of_date': measured}
            added_by_key.setdefault(rk, []).append(v)
            filled.setdefault((k, s), {})[folder] = v
            day_new[(k, s)] = v
            later[(k, s)] = (d, v)
            stats['per_rail'][s] += 1
            return v, walked

        # 1. Published blocks, by position, so a filled row can be held
        #    under the rows ranked above it on this day.
        def _sep(k: str, s: str, tag: str) -> float:
            return _SEP_MIN + _h01(f'{k}|{s}|{measured}|{tag}') \
                * (_SEP_MAX - _SEP_MIN)

        def _under(floor: Optional[int], sep: float) -> Optional[int]:
            return None if floor is None else max(1, int(floor * (1.0 - sep)))

        def _lower(cur: Optional[int], v: int) -> int:
            return v if cur is None else min(cur, v)

        floors_f: dict = {}      # (slug, group) -> min UNHELD walk of filled rows above
        floors_m: dict = {}      # (slug, group) -> min over MEASURED rows above
        done: set = set()
        for s, positions in pub_pos.items():
            by_group: dict = {}
            for k, (g, p) in positions.items():
                if s in targets[k]['slugs']:
                    by_group.setdefault(g, []).append((p, k))
            for g, lst in by_group.items():
                lst.sort()
                for p, k in lst:
                    fkey = (s, g)
                    if folder in hist[(k, s)]:
                        floors_m[fkey] = _lower(floors_m.get(fkey),
                                                hist[(k, s)][folder])
                    elif (k, s) in fills:
                        sep = _sep(k, s, 'sep')
                        got = _fill(k, s, _under(floors_f.get(fkey), sep),
                                    _under(floors_m.get(fkey), sep))
                        done.add((k, s))
                        if got is not None:
                            # The floor for rows below is this row's
                            # unheld walk, so a bounded hold here does
                            # not cascade down the block.
                            floors_f[fkey] = _lower(floors_f.get(fkey), got[1])

        # 2. Everything else. On a chart rail an unranked title sits
        #    under the whole published block.
        block_f: dict = {}
        block_m: dict = {}
        for (s, _g), v in floors_f.items():
            block_f[s] = _lower(block_f.get(s), v)
        for (s, _g), v in floors_m.items():
            block_m[s] = _lower(block_m.get(s), v)
        rest = sorted(fills - done)
        for k, s in rest:
            hard = soft = None
            if s in pub_pos and targets[k]['kind'] in CHART_KINDS:
                sep = _sep(k, s, 'tail')
                hard = _under(block_f.get(s), sep)
                soft = _under(block_m.get(s), sep)
            _fill(k, s, hard, soft)

        # 3. Aggregates follow the blocks they gained.
        prev_measured = (d - timedelta(days=1)).isoformat()
        prev_folders = [f for f, m in cal.items() if m == prev_measured]
        for rk, adds in added_by_key.items():
            it = items[rk]
            item_key = vd.item_key_for(it)
            try:
                agg_old = int(it.get('us_estimate') or 0)
            except (TypeError, ValueError):
                agg_old = 0
            add_sum = sum(adds)
            created = rk in created_keys or agg_old <= 0
            if created:
                r = 1.0
                tt = targets.get(rk)
                if tt and tt['bp_sum'] > 0:
                    r = min(1.25, max(1.0, tt['agg'] / float(tt['bp_sum'])))
                agg_new = _natural(int(round(add_sum * r)), item_key,
                                   f'{measured}|agg')
            else:
                agg_new = _natural(agg_old + add_sum, item_key, f'{measured}|agg')
            history = {iso: vv for iso, vv in (per_item.get(rk) or {}).items()
                       if iso != folder}
            prev_val = None
            for pf in sorted(prev_folders, reverse=True):
                if pf in history:
                    prev_val = history[pf]
                    break
            nv, _how = vd.resolve_value(
                agg_new, item_key, d, history, prev_value=prev_val,
                profile=profiles.get(rk),
                ceiling=max([agg_new] + list(history.values())))
            if nv != agg_new and nv > 0:
                agg_new = int(nv)
                stats['agg_moved'] += 1
            if agg_new <= 0:
                agg_new = max(1, add_sum)
            if created:
                it['us_estimate'] = agg_new
                it['us_estimate_low'] = min(agg_new, _natural(
                    int(agg_new * _LOW_F), item_key, f'{measured}|agglo'))
                it['us_estimate_high'] = max(agg_new, _natural(
                    int(agg_new * _HIGH_F), item_key, f'{measured}|agghi'))
            else:
                scale = agg_new / float(agg_old)
                it['us_estimate'] = agg_new
                for f in ('us_estimate_low', 'us_estimate_high'):
                    cur = it.get(f)
                    if isinstance(cur, int) and cur > 0:
                        it[f] = max(1, int(round(cur * scale)))
                if isinstance(it.get('us_estimate_low'), int) \
                        and it['us_estimate_low'] > agg_new:
                    it['us_estimate_low'] = agg_new
                if isinstance(it.get('us_estimate_high'), int) \
                        and it['us_estimate_high'] < agg_new:
                    it['us_estimate_high'] = agg_new
                stats['items_bumped'] += 1
            per_item.setdefault(rk, {})[folder] = agg_new
            lean.setdefault(folder, {})[rk] = {
                'us_estimate': agg_new,
                'by_platform': {s: {'us_estimate': b['us_estimate']}
                                for s, b in (it.get('by_platform') or {}).items()
                                if isinstance(b, dict) and _usable(b.get('us_estimate'))}}
        fold_by_folder[folder] = _fold_index(lean.get(folder) or {})

        touched = len(day_new)
        stats['cells'] += touched
        processed += 1
        logger.info('%s (%s): %d cell(s) filled, %d item(s) created, '
                    '%d aggregate(s) moved%s', measured, folder, touched,
                    len(created_keys), len(added_by_key),
                    ' [dry-run]' if dry_run else '')
        if dry_run or not touched:
            continue

        # 4. Backup, write, rebuild the lean index, save the ledger.
        src_key = DATED.format(folder=folder)
        bkey = BACKUP.format(folder=folder, ts=ts)
        s3.copy_object(Bucket=BUCKET, Key=bkey,
                       CopySource={'Bucket': BUCKET, 'Key': src_key},
                       ContentType='application/json',
                       MetadataDirective='REPLACE')
        stats['backups'].append(bkey)
        meta = full.setdefault('history_backfill', [])
        if isinstance(meta, list):
            meta.append({'at': ts, 'cells': touched,
                         'items_created': len(created_keys),
                         'rails': sorted({s for (_, s) in day_new})})
        body = json.dumps(full, ensure_ascii=False).encode('utf-8')
        put = s3.put_object(Bucket=BUCKET, Key=src_key, Body=body,
                            ContentType='application/json')
        swi.write_index(folder, full, source_etag=put.get('ETag'),
                        source_bytes=len(body), s3=s3)
        vd.save_history(vd.per_item_to_ledger(per_item, ledger_dates))
        stats['folders_written'] += 1

    hr = stats['hold_ratio']
    hr['mean'] = round(hr['sum'] / hr['n'], 4) if hr['n'] else None
    hr.pop('sum', None)
    return stats


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--end', default=None,
                    help='Newest MEASURED day (default: latest measured).')
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--rails', default=','.join(DEFAULT_RAILS))
    ap.add_argument('--board', default='',
                    help='Board dump (lookback 1) naming the published rows.')
    ap.add_argument('--max-hold', type=float, default=0.35,
                    help=('Deepest hold under a ranked row, as a fraction '
                          'of the walked value (default 0.35, the bound '
                          'published_chart_coherence puts on one move; '
                          '0 = unbounded).'))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit-days', type=int, default=None,
                    help='Stop after this many folders (smoke test).')
    ap.add_argument('--json', default='')
    a = ap.parse_args(argv)
    rails = tuple(r.strip() for r in a.rails.split(',') if r.strip())
    t0 = time.time()
    out = run(a.end, a.days, rails, a.board, a.dry_run, a.limit_days,
              a.max_hold)
    out['elapsed_s'] = round(time.time() - t0, 1)
    print(json.dumps(out, indent=2, default=str))
    if a.json:
        json.dump(out, open(a.json, 'w'), indent=2, default=str)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
