#!/usr/bin/env python3
"""One-off continuity seam fix for the 2026-09-04 stream estimates.

Context: on 2026-09-04 the dated stream_estimates corpus for
2026-06-01 .. 2026-09-03 was re-rendered under formula
v3.2026-09-04-releveled (per-item anchor levels smoothed in log space,
see `anchor_relevel.py` + `apply_daily_variation_backfill.py`), and a
forward day-over-day continuity guard was added to
`stream_estimates.py` (first live cron pass: 2026-09-05 12:00 UTC).

Today's cron output (`latest/stream_estimates.json` plus the dated
`2026-09-04/stream_estimates.json` copy) was produced BEFORE both, so
formerly-flapping items can sit far outside the corpus's adjacent-day
band vs the releveled 2026-09-03 values (observed pre-fix: 1,800 of
6,421 shared items out of band, worst ratio ~5,945x). That renders as
an unexplained day-over-day cliff on the dashboard and garbles the
delta chips.

This script closes the seam once, in place, by applying the SAME
shipped guard to today's two files:

  1. Stamp each item's `prev_day_estimate` / `prev_day_date` from the
     releveled 2026-09-03 snapshot (items absent from Sept 3 get a
     None reference and are skipped by the guard).
  2. Run `stream_estimates._apply_continuity_guard(items, '2026-09-04')`
     (imported, not re-implemented): out-of-band movement (>2.5x or
     <0.4x) with no concrete event cited in `day_specificity` is
     pulled to a bounded continuation of the Sept 3 value with
     per-item-per-date hash jitter; low/mid/high bands and per-platform
     blocks rescale in lockstep; nothing lands identical to Sept 3; no
     integer ends in 0. Event-cited items keep their value.
  3. Re-attach day-over-day trend fields via
     `stream_estimates._attach_dod_trend` against the releveled Sept 3
     snapshot so `delta_pct` / `direction` / `prev_estimate` match the
     series the dashboard actually shows (`as_of_date` untouched).
  4. Back up the pre-fix bytes (only if absent, mirroring the backfill
     backup convention) to
       trends_iq_snapshots/_backups/2026-09-04/
         stream_estimates.latest.pre_continuity_seam_fix.json
         stream_estimates.pre_continuity_seam_fix.json
  5. Write the corrected files back to the same keys, then purge the
     live compute_view cache (plus any cached entry pinned to
     asof=2026-09-04) so the dashboard recomputes on next load.

Idempotent: a second run finds every pair inside the band and adjusts
nothing (only the meta timestamp moves).

Run from the bg-webapp root on the box that has the trends env:

    python3 -m scripts.trends_scrapers.fix_stream_estimates_seam_2026_09_04 --dry-run
    python3 -m scripts.trends_scrapers.fix_stream_estimates_seam_2026_09_04
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import boto3

from scripts.trends_scrapers.stream_estimates import (  # noqa: E402
    _CONTINUITY_DOWN_RATIO,
    _CONTINUITY_UP_RATIO,
    _apply_continuity_guard,
    _attach_dod_trend,
    _day_specificity_cites_event,
)

logger = logging.getLogger('fix_stream_estimates_seam_2026_09_04')

BUCKET = 'dashboard-inputs'
FIX_DATE = '2026-09-04'
PREV_DATE = '2026-09-03'

KEY_LATEST = 'trends_iq_snapshots/latest/stream_estimates.json'
KEY_DATED = f'trends_iq_snapshots/{FIX_DATE}/stream_estimates.json'
KEY_PREV = f'trends_iq_snapshots/{PREV_DATE}/stream_estimates.json'

BK_LATEST = (f'trends_iq_snapshots/_backups/{FIX_DATE}/'
             'stream_estimates.latest.pre_continuity_seam_fix.json')
BK_DATED = (f'trends_iq_snapshots/_backups/{FIX_DATE}/'
            'stream_estimates.pre_continuity_seam_fix.json')

_S3 = None


def _s3():
    global _S3
    if _S3 is None:
        _S3 = boto3.client('s3')
    return _S3


def _read_key(key: str) -> Optional[dict]:
    try:
        obj = _s3().get_object(Bucket=BUCKET, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _key_exists(key: str) -> bool:
    try:
        _s3().head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


def _write_key(key: str, payload: dict) -> None:
    _s3().put_object(
        Bucket=BUCKET, Key=key,
        Body=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        ContentType='application/json')


# ---------------------------------------------------------------------------
# Invariant checks (run on the corrected items, reported per file)
# ---------------------------------------------------------------------------
_INT_FIELDS = ('us_estimate', 'us_estimate_low', 'us_estimate_high')


def _iter_int_cells(items: dict[str, Any]):
    """Yield (path, value) for every integer estimate cell, aggregate
    and per-platform."""
    for k, it in items.items():
        if not isinstance(it, dict):
            continue
        for f in _INT_FIELDS:
            yield f'{k}.{f}', it.get(f)
        for pk, pb in (it.get('by_platform') or {}).items():
            if not isinstance(pb, dict):
                continue
            for f in _INT_FIELDS:
                yield f'{k}.by_platform.{pk}.{f}', pb.get(f)


def _verify(items: dict[str, Any], prev_items: dict[str, Any]) -> dict:
    trailing_zero = sum(
        1 for _p, v in _iter_int_cells(items)
        if isinstance(v, int) and v > 0 and v % 10 == 0)

    ladder_bad = 0
    for k, it in items.items():
        if not isinstance(it, dict):
            continue
        blocks = [it] + [pb for pb in (it.get('by_platform') or {}).values()
                         if isinstance(pb, dict)]
        for b in blocks:
            mid, lo, hi = (b.get('us_estimate'), b.get('us_estimate_low'),
                           b.get('us_estimate_high'))
            if isinstance(mid, int) and mid > 0:
                if isinstance(lo, int) and lo > mid:
                    ladder_bad += 1
                if isinstance(hi, int) and hi < mid:
                    ladder_bad += 1

    identical = 0
    ratios: list[float] = []
    oob = 0
    for k, it in items.items():
        if not isinstance(it, dict):
            continue
        p = prev_items.get(k)
        if not isinstance(p, dict):
            continue
        try:
            cur = int(it.get('us_estimate') or 0)
            prev = int(p.get('us_estimate') or 0)
        except Exception:
            continue
        if cur <= 0 or prev <= 0:
            continue
        if cur == prev:
            identical += 1
        r = cur / prev
        ratios.append(r)
        if r > _CONTINUITY_UP_RATIO or r < _CONTINUITY_DOWN_RATIO:
            oob += 1

    ratios.sort()
    n = len(ratios)

    def _pct(q: float) -> float:
        if not n:
            return 0.0
        return ratios[min(n - 1, int(q * n))]

    return {
        'shared_pairs': n,
        'identical_pairs': identical,
        'trailing_zero_ints': trailing_zero,
        'ladder_violations': ladder_bad,
        'still_out_of_band': oob,
        'ratio_p50': round(_pct(0.50), 3),
        'ratio_p99': round(_pct(0.99), 3),
        'ratio_max': round(ratios[-1], 3) if n else 0.0,
        'ratio_min': round(ratios[0], 4) if n else 0.0,
    }


# ---------------------------------------------------------------------------
# Per-file fix
# ---------------------------------------------------------------------------
def fix_one(name: str, key: str, backup_key: str, prev_snap: dict,
            *, dry_run: bool) -> Optional[dict]:
    snap = _read_key(key)
    if snap is None:
        print(f"[{name}] {key}: MISSING, skipped")
        return None
    items = snap.get('items')
    if not isinstance(items, dict) or not items:
        print(f"[{name}] {key}: no items, skipped")
        return None
    prev_items = prev_snap.get('items') or {}

    # 1. Stamp the previous-day reference from the releveled Sept 3
    #    snapshot. Items absent from Sept 3 get None and the guard
    #    skips them.
    stamped = 0
    pre_oob: list[tuple[float, str, int, int, bool]] = []
    for k, it in items.items():
        if not isinstance(it, dict):
            continue
        pe = None
        p = prev_items.get(k)
        if isinstance(p, dict):
            try:
                cand = int(p.get('us_estimate') or 0)
            except Exception:
                cand = 0
            if cand > 0:
                pe = cand
        it['prev_day_estimate'] = pe
        it['prev_day_date'] = PREV_DATE if pe else None
        if pe:
            stamped += 1
            try:
                cur = int(it.get('us_estimate') or 0)
            except Exception:
                cur = 0
            if cur > 0:
                r = cur / pe
                if r > _CONTINUITY_UP_RATIO or r < _CONTINUITY_DOWN_RATIO:
                    cites = _day_specificity_cites_event(
                        str(it.get('day_specificity') or ''))
                    pre_oob.append((r, k, cur, pe, cites))

    # 2. The shipped guard, verbatim.
    adjusted = _apply_continuity_guard(items, FIX_DATE)
    kept_event = sum(1 for _r, _k, _c, _p, cites in pre_oob if cites)

    # 3. Day-over-day trend fields recomputed against the releveled
    #    reference (values the dashboard series actually shows).
    #    as_of_date stays untouched (today_iso=None).
    _attach_dod_trend(items, prev_snap, prev_date_iso=PREV_DATE,
                      today_iso=None)

    # 4. Invariants.
    checks = _verify(items, prev_items)

    # Meta marker, mirroring the backfill's flat-key meta convention.
    meta = dict(snap.get('meta') or {})
    meta['_continuity_seam_fix_applied'] = FIX_DATE
    meta['_continuity_seam_fix_at'] = datetime.now(timezone.utc).isoformat()
    meta['_continuity_seam_fix_reference'] = PREV_DATE
    meta['_continuity_seam_fix_adjusted'] = adjusted
    snap['meta'] = meta

    print(f"[{name}] items={len(items)} sept3_refs={stamped} "
          f"out_of_band_pre={len(pre_oob)} adjusted={adjusted} "
          f"kept_event_cited={kept_event}")
    print(f"[{name}] post-fix checks: {json.dumps(checks)}")

    if dry_run:
        print(f"[{name}] dry-run: no backup, no write")
        return {'name': name, 'adjusted': adjusted, 'checks': checks,
                'pre_oob': pre_oob, 'items': items}

    # 5. Backup (only if absent) + write back to the same key.
    if not _key_exists(backup_key):
        pre = _read_key(key)
        if pre is not None:
            _write_key(backup_key, pre)
            print(f"[{name}] backup -> s3://{BUCKET}/{backup_key}")
    else:
        print(f"[{name}] backup already present, left untouched")
    _write_key(key, snap)
    print(f"[{name}] wrote s3://{BUCKET}/{key}")
    return {'name': name, 'adjusted': adjusted, 'checks': checks,
            'pre_oob': pre_oob, 'items': items}


# ---------------------------------------------------------------------------
# Cache purge
# ---------------------------------------------------------------------------
def purge_caches() -> dict:
    import trends_iq  # bg-webapp root is on sys.path when run as -m
    n_live = trends_iq.invalidate_live_compute_view_caches()
    s3 = _s3()
    deleted_asof = 0
    try:
        paginator = s3.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=trends_iq.S3_CACHE_BUCKET,
                                       Prefix=trends_iq.S3_CACHE_PREFIX):
            for obj in page.get('Contents') or []:
                ckey = obj.get('Key') or ''
                if not ckey.endswith('.json'):
                    continue
                try:
                    data = json.loads(s3.get_object(
                        Bucket=trends_iq.S3_CACHE_BUCKET,
                        Key=ckey)['Body'].read().decode('utf-8'))
                except Exception:
                    continue
                if (data.get('filters') or {}).get('asof') == FIX_DATE:
                    s3.delete_object(Bucket=trends_iq.S3_CACHE_BUCKET,
                                     Key=ckey)
                    deleted_asof += 1
    except Exception as e:
        logger.warning("asof cache scan failed: %s", e)
    return {'live_invalidated': n_live, 'asof_0904_deleted': deleted_asof}


# ---------------------------------------------------------------------------
# Report helper: before/after table for the worst formerly-flapping items
# ---------------------------------------------------------------------------
def print_sample_table(result: dict, prev_items: dict,
                       must_include: str = 'podcast:up first from npr',
                       n_rows: int = 10) -> None:
    pre_oob = sorted(result['pre_oob'], key=lambda t: t[0], reverse=True)
    picked = [t for t in pre_oob if t[1] == must_include]
    for t in pre_oob:
        if len(picked) >= n_rows:
            break
        if t[1] != must_include:
            picked.append(t)
    picked.sort(key=lambda t: t[0], reverse=True)
    items = result['items']
    print(f"\n[{result['name']}] sample of formerly-flapping items "
          f"(sept3 releveled, sept4 pre-fix, sept4 post-fix, post ratio):")
    for _r, k, cur_pre, prev, _cites in picked:
        it = items.get(k) or {}
        cur_post = int(it.get('us_estimate') or 0)
        ratio_post = (cur_post / prev) if prev else 0.0
        title = (it.get('display_title') or k)[:44]
        print(f"   {title:<44} {prev:>12,} {cur_pre:>12,} "
              f"{cur_post:>12,}  x{ratio_post:.2f}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='One-off 2026-09-04 stream_estimates continuity '
                    'seam fix (see module docstring).')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')

    prev_snap = _read_key(KEY_PREV)
    if prev_snap is None or not (prev_snap.get('items') or {}):
        print(f"FATAL: reference {KEY_PREV} missing or empty; aborting")
        return 2
    prev_meta = prev_snap.get('meta') or {}
    print(f"reference {PREV_DATE}: items={len(prev_snap['items'])} "
          f"formula={prev_meta.get('_daily_variation_formula_applied')} "
          f"sweep={prev_meta.get('_adjacent_distinct_sweep')}")

    results = []
    for name, key, bk in (('latest', KEY_LATEST, BK_LATEST),
                          ('dated-2026-09-04', KEY_DATED, BK_DATED)):
        r = fix_one(name, key, bk, prev_snap, dry_run=args.dry_run)
        if r:
            results.append(r)

    if not results:
        print("nothing to fix")
        return 1

    for r in results:
        print_sample_table(r, prev_snap.get('items') or {})

    if not args.dry_run:
        purged = purge_caches()
        print(f"\ncache purge: {json.dumps(purged)}")
    else:
        print("\ndry-run: cache purge skipped")

    bad = any(r['checks']['identical_pairs'] or
              r['checks']['trailing_zero_ints'] or
              r['checks']['ladder_violations'] for r in results)
    return 2 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
