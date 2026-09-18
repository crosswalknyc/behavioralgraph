#!/usr/bin/env python3
"""Sweep the archive so a derived rail is a subset of its parent on
every archived day, not only today.

Companion to `derived_rails`, which holds the rule going forward, and
to `trends_iq._rederive_derived_rails`, which applies it to the board
being produced. History is rendered through that same board code, so a
past day picks up the fix the moment it is asked for. What this sweep
owns is the two things the archive can hold that would survive it:

1. THE PANEL MIRROR. A derived rail carries its parent's catalog, so
   the dated snapshot of the child panel has to be the dated snapshot
   of the parent panel, row for row and in the same order. The child
   scraper mirrors `latest/<parent>.json` at the moment it runs, which
   is a race against the parent's own scraper: a child that runs first
   mirrors yesterday's catalog and the archive then holds two panels
   whose rows disagree, which renders as titles that exist on one side
   of the pair and not the other. This sweep re-mirrors the child from
   the SAME-DAY parent, which is the thing the race can get wrong.

2. A STORED CHILD READING. A derived rail must never carry a number of
   its own: that is the whole correction, because a stored child and a
   live parent are free to drift and did. Nothing writes one today, so
   this is a guardrail rather than a repair. If any pass ever leaves a
   `by_platform.<child>` block in a dated reading store, this sweep
   removes it so the rail cannot quietly go back to being
   co-estimated.

It also reports, per day, what the derivation produces against that
day's stored parent readings: the share distribution, and the count of
titles where the child would reach its parent. That count is zero by
construction, and the report is how it stays visible.

Idempotent throughout. A day that is already coherent is read and left
alone. Every rewrite copies the file to
`trends_iq_snapshots/_backups/{date}/` first.

    python3 -m scripts.trends_scrapers.sweep_derived_rails --dry-run
    python3 -m scripts.trends_scrapers.sweep_derived_rails --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from scripts.trends_scrapers import derived_rails as dr  # noqa: E402

logger = logging.getLogger('sweep_derived_rails')

BUCKET = 'dashboard-inputs'
PANEL = 'trends_iq_snapshots/{date}/{slug}.json'
READINGS = 'trends_iq_snapshots/{date}/stream_estimates.json'
BACKUP = 'trends_iq_snapshots/_backups/{date}/{name}.pre_derived_rail_{ts}.json'

# Fields a mirrored panel owns rather than inherits. Everything else on
# the child snapshot is the parent's catalog.
_MIRROR_OWN_FIELDS = ('label', 'source', 'kind', 'mirrors',
                      'scrape_elapsed_s', 'error')


def _s3():
    import boto3
    return boto3.client('s3')


def _get(key: str) -> Optional[dict]:
    try:
        body = _s3().get_object(Bucket=BUCKET, Key=key)['Body'].read()
    except Exception:
        return None
    try:
        return json.loads(body)
    except Exception:
        logger.warning('  %s is not readable JSON', key)
        return None


def _titles(snap: Optional[dict]) -> list:
    return [(r.get('title') or '').strip()
            for r in ((snap or {}).get('national') or [])
            if isinstance(r, dict) and (r.get('title') or '').strip()]


# ---------------------------------------------------------------------------
# 1. The panel mirror
# ---------------------------------------------------------------------------
def mirror_payload(child_snap: dict, parent_snap: dict,
                   child_slug: str, parent: str) -> dict:
    """The child panel as it should read: the parent's catalog, the
    child's own identity."""
    out = dict(parent_snap)
    for f in _MIRROR_OWN_FIELDS:
        if f in child_snap:
            out[f] = child_snap[f]
        else:
            out.pop(f, None)
    rail = dr.rail_for(child_slug)
    out['label'] = (rail.label if rail else child_slug)
    out['source'] = child_slug
    out['mirrors'] = parent
    out['source_fetched_at'] = parent_snap.get('fetched_at')
    if child_snap.get('fetched_at'):
        out['fetched_at'] = child_snap['fetched_at']
    rows = []
    for i, r in enumerate(parent_snap.get('national') or [], start=1):
        if not isinstance(r, dict):
            continue
        row = dict(r)
        row['rank'] = i
        rows.append(row)
    out['national'] = rows
    if parent_snap.get('stale_from_previous'):
        out['stale_from_previous'] = True
    else:
        out.pop('stale_from_previous', None)
    return out


def check_mirror(day: str, child_slug: str) -> dict:
    parent = dr.parent_slug(child_slug)
    child_snap = _get(PANEL.format(date=day, slug=child_slug))
    parent_snap = _get(PANEL.format(date=day, slug=parent))
    out: dict[str, Any] = {'day': day, 'child': child_slug,
                           'parent': parent, 'action': 'skip'}
    if child_snap is None or parent_snap is None:
        out['reason'] = 'one side of the pair has no snapshot for this day'
        return out
    ct, pt = _titles(child_snap), _titles(parent_snap)
    out['child_rows'], out['parent_rows'] = len(ct), len(pt)
    if not pt:
        out['reason'] = 'the parent snapshot carries no catalog'
        return out
    if [t.lower() for t in ct] == [t.lower() for t in pt]:
        out['action'] = 'ok'
        return out
    out['action'] = 'remirror'
    out['orphans_child'] = sorted(set(t.lower() for t in ct)
                                  - set(t.lower() for t in pt))[:10]
    out['orphans_parent'] = sorted(set(t.lower() for t in pt)
                                   - set(t.lower() for t in ct))[:10]
    out['_payload'] = mirror_payload(child_snap, parent_snap,
                                     child_slug, parent)
    return out


# ---------------------------------------------------------------------------
# 2. A stored child reading, and the derivation report
# ---------------------------------------------------------------------------
def check_readings(day: str) -> dict:
    """Report the derivation against one day's stored parent readings,
    and find any stored child block that should not exist."""
    snap = _get(READINGS.format(date=day))
    out: dict[str, Any] = {'day': day, 'action': 'skip',
                           'stored_child_blocks': 0, 'shares': [],
                           'child_at_or_above_parent': []}
    if not snap:
        out['reason'] = 'no reading store for this day'
        return out
    items = snap.get('items') or {}
    if not isinstance(items, dict):
        return out

    strip: list = []
    shares: list = []
    breaches: list = []
    for key, it in items.items():
        if not isinstance(it, dict):
            continue
        by_platform = it.get('by_platform') or {}
        if not isinstance(by_platform, dict):
            continue
        kind = str(it.get('kind') or key.split(':', 1)[0])
        title = (it.get('display_title') or '').strip()
        for child_slug in dr.child_slugs():
            if child_slug in by_platform:
                strip.append((key, child_slug))
            parent_blk = by_platform.get(dr.parent_slug(child_slug))
            if not isinstance(parent_blk, dict):
                continue
            try:
                pv = int(parent_blk.get('us_estimate') or 0)
            except (TypeError, ValueError):
                continue
            if pv <= 0:
                continue
            value, _ceil, _how = dr.derive_value(pv, child_slug, title, kind)
            if value <= 0:
                continue
            shares.append(value / float(pv))
            if value >= pv:
                breaches.append((title, pv, value))

    out['stored_child_blocks'] = len(strip)
    out['shares'] = shares
    out['child_at_or_above_parent'] = breaches
    out['action'] = 'strip' if strip else 'ok'
    if strip:
        out['_strip'] = strip
        out['_snap'] = snap
    return out


def apply_strip(day: str, snap: dict, strip: list, ts: str) -> int:
    key = READINGS.format(date=day)
    items = snap.get('items') or {}
    n = 0
    for item_key, child_slug in strip:
        it = items.get(item_key)
        if not isinstance(it, dict):
            continue
        if (it.get('by_platform') or {}).pop(child_slug, None) is not None:
            n += 1
    if not n:
        return 0
    snap['derived_rail_sweep'] = {
        'at': datetime.now(timezone.utc).isoformat(),
        'stored_child_blocks_removed': n,
    }
    s3 = _s3()
    s3.copy_object(Bucket=BUCKET, CopySource={'Bucket': BUCKET, 'Key': key},
                   Key=BACKUP.format(date=day, name='stream_estimates',
                                     ts=ts))
    s3.put_object(Bucket=BUCKET, Key=key,
                  Body=json.dumps(snap, ensure_ascii=False).encode('utf-8'),
                  ContentType='application/json')
    return n


def apply_mirror(day: str, child_slug: str, payload: dict, ts: str) -> None:
    key = PANEL.format(date=day, slug=child_slug)
    s3 = _s3()
    s3.copy_object(Bucket=BUCKET, CopySource={'Bucket': BUCKET, 'Key': key},
                   Key=BACKUP.format(date=day, name=child_slug, ts=ts))
    s3.put_object(Bucket=BUCKET, Key=key,
                  Body=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                  ContentType='application/json')


# ---------------------------------------------------------------------------
def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--end', default='',
                    help='last archived day (default: today UTC)')
    ap.add_argument('--days', type=int, default=60)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--workers', type=int, default=8)
    args = ap.parse_args(argv)
    if not args.apply and not args.dry_run:
        ap.error('pass --dry-run or --apply')
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')

    problems = dr.registered_ceiling_check()
    for p in problems:
        logger.error('registry: %s', p)
    if not problems:
        logger.info('registry: every rail agrees with its parent ceiling')

    end = (date.fromisoformat(args.end) if args.end
           else datetime.now(timezone.utc).date())
    days = [(end - timedelta(days=i)).isoformat()
            for i in range(args.days)][::-1] + ['latest']
    children = dr.child_slugs()
    logger.info('rails: %s', ', '.join(
        f'{c} under {dr.parent_slug(c)}' for c in children) or 'none')
    logger.info('window %s .. %s (+ latest)', days[0], days[-2])

    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    mirror_jobs = [(d, c) for d in days for c in children]
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        mirrors = list(ex.map(lambda a: check_mirror(*a), mirror_jobs))
        readings = list(ex.map(check_readings, days))

    n_remirror = sum(1 for m in mirrors if m['action'] == 'remirror')
    n_present = sum(1 for m in mirrors if m['action'] in ('ok', 'remirror'))
    logger.info('panel mirrors: %d day/rail pair(s) present, %d coherent, '
                '%d to re-mirror', n_present, n_present - n_remirror,
                n_remirror)
    for m in mirrors:
        if m['action'] == 'remirror':
            logger.info('  %s %s: %d rows against the parent\'s %d '
                        '(child-only %s, parent-only %s)',
                        m['day'], m['child'], m.get('child_rows', 0),
                        m.get('parent_rows', 0),
                        m.get('orphans_child'), m.get('orphans_parent'))

    n_strip = sum(r.get('stored_child_blocks', 0) for r in readings)
    all_shares = [s for r in readings for s in r.get('shares') or []]
    breaches = [(r['day'], b) for r in readings
                for b in r.get('child_at_or_above_parent') or []]
    days_priced = sum(1 for r in readings if r.get('shares'))
    logger.info('reading stores: %d day(s) carry parent readings, '
                '%d stored child block(s) to remove', days_priced, n_strip)
    if all_shares:
        logger.info('derived share across %d archived title-day(s): '
                    'min %.4f  median %.4f  max %.4f',
                    len(all_shares), min(all_shares),
                    statistics.median(all_shares), max(all_shares))
    logger.info('archived title-day(s) where the rail would reach its '
                'parent: %d', len(breaches))
    for day, (title, pv, cv) in breaches[:10]:
        logger.error('  %s %r parent %s child %s', day, title,
                     f'{pv:,}', f'{cv:,}')

    if args.dry_run:
        print('\ndry run; nothing written')
        return 0

    wrote = 0
    for m in mirrors:
        if m['action'] == 'remirror':
            apply_mirror(m['day'], m['child'], m['_payload'], ts)
            wrote += 1
            logger.info('  re-mirrored %s %s', m['day'], m['child'])
    removed = 0
    for r in readings:
        if r.get('action') == 'strip':
            removed += apply_strip(r['day'], r['_snap'], r['_strip'], ts)
            logger.info('  %s removed %d stored child block(s)', r['day'],
                        r.get('stored_child_blocks', 0))
    logger.info('re-mirrored %d panel snapshot(s), removed %d stored child '
                'block(s)', wrote, removed)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
