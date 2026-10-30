#!/usr/bin/env python3
"""One-off: re-level stored Amazon / Apple Books / Audible readings to
the 3M daily US book-sales pool (book_sales_level.py), Oct 1 to today
plus latest/.

Each dated folder is levelled from its own book_charts.json, so a
title's number on a past day follows its rank that day. Day-over-day
trend fields on the touched titles are recomputed against the levelled
day before. Backups go to
_backups/{d}.stream_estimates.pre_book_sales_level.json before a write.

    PYTHONPATH=. python3 -m scripts.trends_scrapers.relevel_books_sales_pool_2026_10_30 --dry-run
    PYTHONPATH=. python3 -m scripts.trends_scrapers.relevel_books_sales_pool_2026_10_30
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

import boto3

from . import book_sales_level as bsl
from . import stream_window_index as swi
from .stream_estimates import _attach_dod_trend, _cp_normalize

BUCKET = 'dashboard-inputs'
DATED = 'trends_iq_snapshots/{d}/{name}.json'
LATEST = 'trends_iq_snapshots/latest/{name}.json'
BACKUP = 'trends_iq_snapshots/_backups/{d}.stream_estimates.pre_book_sales_level.json'
SAMPLES = ('amazon', 'apple', 'audible')


def _get(s3, key):
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    return json.loads(obj['Body'].read()), obj.get('ETag')


def _level_day(snap: dict, charts: dict, prev: dict) -> set:
    items = snap.setdefault('items', {})
    target = snap.get('target_date')
    touched = bsl.level(items, charts, target)
    _attach_dod_trend({k: items[k] for k in touched}, prev,
                      prev_date_iso=(prev or {}).get('target_date'),
                      today_iso=target)
    snap['count'] = len(items)
    snap['book_sales_levelled_at'] = bsl.LEVEL_TAG
    return touched


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='2026-10-01')
    ap.add_argument('--end', default=date.today().isoformat())
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    s3 = boto3.client('s3', region_name='us-east-1')

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    days = [(start + timedelta(days=i)).isoformat()
            for i in range(-1, (end - start).days + 1)]
    print(f'book sales re-level {days[1]} .. {days[-1]} (base {days[0]})'
          f'{"  [DRY RUN]" if args.dry_run else ""}')
    prev, last_snap, last_etag, before_last = None, None, None, None
    for i, d in enumerate(days):
        try:
            snap, etag = _get(s3, DATED.format(d=d, name='stream_estimates'))
            charts, _ = _get(s3, DATED.format(d=d, name='book_charts'))
        except Exception as e:
            print(f'  {d}: unreadable ({type(e).__name__}); skipped')
            prev = None
            continue
        before = {k: json.dumps(v.get('by_platform'), sort_keys=True)
                  for k, v in (snap.get('items') or {}).items()
                  if (v or {}).get('kind') == 'book'}
        touched = _level_day(snap, charts, prev)
        if i > 0:
            new = sum(1 for k in touched if k not in before)
            tops = []
            for pl in SAMPLES:
                row = ((charts.get('sources') or {}).get(pl) or {}).get('items') or []
                if row:
                    k = 'book:' + _cp_normalize(
                        f"{(row[0].get('title') or '').strip()} {(row[0].get('artist') or '').strip()}")
                    tops.append(f"{pl} #1 {snap['items'].get(k, {}).get('by_platform', {}).get(pl, {}).get('us_estimate')}")
            print(f"  {d} (target {snap.get('target_date')}): levelled {len(touched):3d}, "
                  f"new entries {new:3d}  |  {', '.join(tops)}")
            if not args.dry_run:
                s3.copy_object(Bucket=BUCKET, Key=BACKUP.format(d=d),
                               CopySource={'Bucket': BUCKET,
                                           'Key': DATED.format(d=d, name='stream_estimates')})
                s3.put_object(Bucket=BUCKET, Key=DATED.format(d=d, name='stream_estimates'),
                              Body=json.dumps(snap).encode(), ContentType='application/json')
                swi.write_index(d, snap, s3=s3)
        before_last = prev
        prev, last_snap, last_etag = snap, snap, etag

    try:
        head = s3.head_object(Bucket=BUCKET, Key=LATEST.format(name='stream_estimates'))
        same = head.get('ETag') == last_etag
    except Exception:
        same = False
    print(f'latest/: {"same object as newest day, mirrored" if same else "differs from newest day, levelled on its own"}')
    if not args.dry_run:
        if same and last_snap is not None:
            body = last_snap
        else:
            body, _ = _get(s3, LATEST.format(name='stream_estimates'))
            charts, _ = _get(s3, LATEST.format(name='book_charts'))
            _level_day(body, charts, before_last)
        s3.copy_object(Bucket=BUCKET, Key=BACKUP.format(d='latest'),
                       CopySource={'Bucket': BUCKET,
                                   'Key': LATEST.format(name='stream_estimates')})
        s3.put_object(Bucket=BUCKET, Key=LATEST.format(name='stream_estimates'),
                      Body=json.dumps(body).encode(), ContentType='application/json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
