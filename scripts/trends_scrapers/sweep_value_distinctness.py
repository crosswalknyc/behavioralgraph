#!/usr/bin/env python3
"""Sweep the archived daily snapshots so no item repeats a reading
inside its own trailing 60 days.

Companion to `value_distinctness`, which holds the rule going forward.
This applies the same rule to what is already published, because a
repeated reading in the archive is the fingerprint the rule exists to
remove and leaving 22,970 of them behind would undo the point.

Two corrections run per item, in date order:

1. A reading identical to the immediately previous day is WALKED, not
   nudged. That is the day walk the item should have received, and it
   is what the 2026-09-15 restore cohort needs: 12,467 keys came back
   from 2026-09-14 as integers after a truncated read, so 12,559
   readings that day are their predecessor verbatim. A fraction of a
   percent would leave the day looking flat; the walk gives it the
   movement the run would have produced.

2. A reading that matches an EARLIER day inside the window moves along
   the item's own curve until it is new, or, when the item's honest
   band cannot hold 60 distinct readings, is placed as far from its
   last use as the band allows and the row is marked.

Only earlier days are ever consulted, and a reading that is already
distinct is accepted untouched, so a second run is a no-op.

Every snapshot is copied to `trends_iq_snapshots/_backups/{date}/` with
a server-side copy before it is rewritten.

  python3 -m scripts.trends_scrapers.sweep_value_distinctness --dry-run
  python3 -m scripts.trends_scrapers.sweep_value_distinctness --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from scripts.trends_scrapers import value_distinctness as vd  # noqa: E402

logger = logging.getLogger('sweep_value_distinctness')

BUCKET = 'dashboard-inputs'
DATED = 'trends_iq_snapshots/{date}/stream_estimates.json'
LATEST = 'trends_iq_snapshots/latest/stream_estimates.json'
BACKUP = ('trends_iq_snapshots/_backups/{date}/'
          'stream_estimates.pre_distinctness_{ts}.json')


def _s3():
    import boto3
    return boto3.client('s3')


# ---------------------------------------------------------------------------
# Phase A: read the readings only
# ---------------------------------------------------------------------------
def read_columns(dates: list[str], workers: int = 10
                 ) -> tuple[dict[str, dict[str, int]], dict[str, dict]]:
    """({iso: {item key: reading}}, {item key: rhythm identity}) for
    every snapshot that loads."""
    def one(d: str):
        try:
            body = _s3().get_object(Bucket=BUCKET,
                                    Key=DATED.format(date=d))['Body'].read()
        except Exception as e:
            logger.warning('  %s unreadable (%s)', d, e.__class__.__name__)
            return d, None, None
        snap = json.loads(body)
        col, met = {}, {}
        for k, it in (snap.get('items') or {}).items():
            if not isinstance(it, dict):
                continue
            try:
                v = int(it.get('us_estimate') or 0)
            except (TypeError, ValueError):
                continue
            if v > 0:
                col[k] = v
                met[k] = {'kind': it.get('kind'),
                          'display_title': it.get('display_title'),
                          'artist': it.get('artist')}
        return d, col, met

    out: dict[str, dict[str, int]] = {}
    meta: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for d, col, met in ex.map(one, dates):
            if col is None:
                continue
            out[d] = col
            meta.update(met)
            logger.info('  %s %d readings', d, len(col))
    return out, meta


# ---------------------------------------------------------------------------
# Phase B: plan every correction without writing anything
# ---------------------------------------------------------------------------
def plan(columns: dict[str, dict[str, int]], dates: list[str],
         profiles: dict[str, dict], meta: dict[str, dict],
         ) -> tuple[dict[str, dict[str, int]], Counter, set]:
    """Return ({iso: {key: new reading}}, counters, band-limited keys).
    Only changed readings appear in the patch."""
    try:
        from scripts.trends_scrapers.carry_forward import walk_value
    except Exception:
        walk_value = None                                # type: ignore

    present = [d for d in dates if d in columns]
    series: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for d in present:
        for k, v in columns[d].items():
            series[k].append((d, v))

    patch: dict[str, dict[str, int]] = {d: {} for d in present}
    stats = Counter()
    band_limited: set = set()

    for key, obs in series.items():
        if vd.is_band_limited([v for _, v in obs]):
            band_limited.add(key)
            stats['band_limited_items'] += 1
        if len(obs) < 2:
            continue
        m = meta.get(key) or {}
        item_key = vd.item_key_for(m)
        profile = profiles.get(key)
        ceiling = max(v for _, v in obs)
        assigned: dict[str, int] = {}
        prev_iso: Optional[str] = None

        for iso, raw in obs:
            d = date.fromisoformat(iso)
            v = raw
            prev_v = assigned.get(prev_iso) if prev_iso else None
            prev_d = date.fromisoformat(prev_iso) if prev_iso else None

            # 1. adjacent-day repeat -> the day walk it should have had
            if (prev_v is not None and v == prev_v and walk_value
                    and prev_d is not None and (d - prev_d).days == 1):
                walked = walk_value(prev_v, item_key, d,
                                    prev_date=prev_d, profile=profile)
                # A correction never lifts an item above the highest
                # reading it already holds, so a per-service cap that
                # cleared before still clears. Mirroring the move keeps
                # the size of the day's step, only its direction flips.
                if walked > ceiling:
                    walked = min(ceiling, max(1, 2 * prev_v - walked))
                if 0 < walked != prev_v:
                    v = walked
                    stats['walked'] += 1

            # 2. distinct across every earlier day in the window
            new_v, how = vd.resolve_value(
                v, item_key, d, dict(assigned),
                prev_value=prev_v, profile=profile,
                ceiling=max(ceiling, v))
            if how != 'ok':
                stats[how] += 1
            v = new_v

            assigned[iso] = v
            if v != raw:
                patch[iso][key] = v
                stats['changed'] += 1
            prev_iso = iso

    return patch, stats, band_limited


# ---------------------------------------------------------------------------
# Phase C: rewrite the snapshots one day at a time
# ---------------------------------------------------------------------------
def apply_day(iso: str, day_patch: dict[str, int], ts: str,
              band_limited: set, prev_snap: Optional[dict] = None,
              prev_iso: Optional[str] = None,
              also_latest: bool = False) -> tuple[int, dict]:
    """Rewrite one dated snapshot and return (readings corrected, the
    snapshot as written so the next day can compare against it)."""
    from scripts.trends_scrapers.stream_estimates import \
        _rescale_estimate_blocks, _attach_dod_trend

    s3 = _s3()
    key = DATED.format(date=iso)
    snap = json.loads(s3.get_object(Bucket=BUCKET, Key=key)['Body'].read())
    items = snap.get('items') or {}

    touched = 0
    for k, new_v in day_patch.items():
        it = items.get(k)
        if not isinstance(it, dict):
            continue
        try:
            cur = int(it.get('us_estimate') or 0)
        except (TypeError, ValueError):
            continue
        if cur <= 0 or cur == new_v:
            continue
        _rescale_estimate_blocks(it, cur, new_v, k, f'{iso}|distinct')
        touched += 1

    # Mark the rows whose band cannot hold 60 distinct readings, so the
    # exception is countable instead of hiding inside the corpus.
    for k, it in items.items():
        if not isinstance(it, dict):
            continue
        if k in band_limited:
            it[vd.BAND_LIMITED_FIELD] = True
        else:
            it.pop(vd.BAND_LIMITED_FIELD, None)

    # A corrected reading changes the day-over-day move, so the chips
    # are recomputed against the previous day AS CORRECTED. Leaving
    # them would keep the dead 0% chips the verbatim restore produced.
    if prev_snap is not None:
        first = next(iter(items.values()), {}) if items else {}
        _attach_dod_trend(items, prev_snap,
                          prev_date_iso=(first.get('prev_date')
                                         if isinstance(first, dict) else None),
                          today_iso=(first.get('as_of_date')
                                     if isinstance(first, dict) else None))

    snap['distinctness_sweep'] = {
        'at': datetime.now(timezone.utc).isoformat(),
        'window_days': vd.WINDOW_DAYS,
        'readings_corrected': touched,
        'previous_day': prev_iso,
    }
    body = json.dumps(snap, ensure_ascii=False).encode('utf-8')

    s3.copy_object(Bucket=BUCKET,
                   CopySource={'Bucket': BUCKET, 'Key': key},
                   Key=BACKUP.format(date=iso, ts=ts))
    s3.put_object(Bucket=BUCKET, Key=key, Body=body,
                  ContentType='application/json')
    if also_latest:
        s3.copy_object(Bucket=BUCKET,
                       CopySource={'Bucket': BUCKET, 'Key': LATEST},
                       Key=BACKUP.format(date=f'{iso}-latest', ts=ts))
        s3.put_object(Bucket=BUCKET, Key=LATEST, Body=body,
                      ContentType='application/json',
                      CacheControl='public, max-age=60')
    return touched, snap


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def level_report(columns: dict[str, dict[str, int]],
                 patch: dict[str, dict[str, int]],
                 meta: dict[str, dict]) -> None:
    """Top and median per kind, before and after. Corrections are for
    collisions; the levels have to hold."""
    before, after = defaultdict(list), defaultdict(list)
    for iso, col in columns.items():
        p = patch.get(iso) or {}
        for k, v in col.items():
            kind = (meta.get(k) or {}).get('kind') or k.split(':')[0]
            before[kind].append(v)
            after[kind].append(p.get(k, v))
    print(f'{"list":<18}{"top before":>14}{"top after":>14}'
          f'{"med before":>13}{"med after":>13}{"shift":>9}')
    for kind in sorted(before):
        b, a = before[kind], after[kind]
        mb, ma = statistics.median(b), statistics.median(a)
        shift = (ma / mb - 1.0) * 100 if mb else 0.0
        print(f'{kind:<18}{max(b):>14,}{max(a):>14,}'
              f'{mb:>13,.0f}{ma:>13,.0f}{shift:>8.2f}%')


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--end', default='',
                    help='last snapshot date (default: yesterday UTC)')
    ap.add_argument('--days', type=int, default=vd.WINDOW_DAYS)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-latest', action='store_true',
                    help='leave latest/ alone (the nightly run owns it)')
    args = ap.parse_args(argv)
    if not args.apply and not args.dry_run:
        ap.error('pass --dry-run or --apply')
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    end = (date.fromisoformat(args.end) if args.end
           else datetime.now(timezone.utc).date() - timedelta(days=1))
    dates = vd.window_dates(end, args.days)
    logger.info('window %s .. %s', dates[0], dates[-1])

    columns, meta = read_columns(dates)
    logger.info('loaded %d/%d snapshots, identity for %d items',
                len(columns), len(dates), len(meta))

    profiles = {}
    try:
        from scripts.trends_scrapers.stream_estimates import \
            _load_rhythm_profiles
        profiles = _load_rhythm_profiles() or {}
    except Exception:
        logger.warning('rhythm profiles unavailable; hash personalities')
    logger.info('rhythm profiles: %d', len(profiles))

    patch, stats, band_limited = plan(columns, dates, profiles, meta)
    total = sum(len(p) for p in patch.values())
    logger.info('planned corrections: %d readings across %d days',
                total, sum(1 for p in patch.values() if p))
    logger.info('  walked (adjacent-day repeat): %d', stats['walked'])
    logger.info('  moved along own curve       : %d', stats['moved'])
    logger.info('  spaced (band limited)       : %d', stats['spaced'])
    logger.info('  band-limited items          : %d',
                stats['band_limited_items'])

    print()
    level_report(columns, patch, meta)

    if args.dry_run:
        print('\ndry run; nothing written')
        return 0

    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    written = 0
    prev_snap: Optional[dict] = None
    prev_iso: Optional[str] = None
    for iso in dates:
        if iso not in columns:
            continue
        day_patch = patch.get(iso) or {}
        # Only recompute chips when a reading actually moved on one
        # side of the comparison. An untouched pair keeps the chip the
        # run that produced it wrote.
        moved_either = bool(day_patch or (patch.get(prev_iso or '') or {}))
        n, prev_snap = apply_day(
            iso, day_patch, ts, band_limited,
            prev_snap=prev_snap if moved_either else None,
            prev_iso=prev_iso,
            also_latest=(iso == dates[-1] and not args.no_latest))
        prev_iso = iso
        written += n
        logger.info('  %s rewrote %d readings', iso, n)
    logger.info('rewrote %d readings', written)

    # The ledger the nightly run compares against.
    corrected = {d: {k: (patch.get(d) or {}).get(k, v)
                     for k, v in col.items()}
                 for d, col in columns.items()}
    per_item: dict[str, dict[str, int]] = defaultdict(dict)
    for d, col in corrected.items():
        for k, v in col.items():
            per_item[k][d] = v
    vd.save_history(vd.per_item_to_ledger(dict(per_item), dates))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
