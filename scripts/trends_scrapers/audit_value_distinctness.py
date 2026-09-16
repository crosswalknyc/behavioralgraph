#!/usr/bin/env python3
"""Count how often an item repeats a reading inside its trailing 60 days.

The measurement behind the 2026-09-15 rule: items with two or more
readings, how many of them repeat a value, how many duplicate readings
that is in total, and the breakdown by value band. Band-limited items
(whose honest range cannot hold 60 distinct integers) are counted
separately, because uniqueness there would mean inflating the number.

Reads the dated snapshots only. No clickstream, ever
(`trends-rankers-never-clickstream`).

  python3 -m scripts.trends_scrapers.audit_value_distinctness
  python3 -m scripts.trends_scrapers.audit_value_distinctness --end 2026-09-15
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from scripts.trends_scrapers import value_distinctness as vd  # noqa: E402

BUCKET = 'dashboard-inputs'
DATED = 'trends_iq_snapshots/{date}/stream_estimates.json'

logger = logging.getLogger('audit_value_distinctness')


def band(v: int) -> str:
    if v < 1_000:
        return '<1K'
    if v < 10_000:
        return '1K-10K'
    if v < 100_000:
        return '10K-100K'
    return '>100K'


def read(dates: list[str], workers: int = 10):
    import boto3

    def one(d: str):
        try:
            body = boto3.client('s3').get_object(
                Bucket=BUCKET, Key=DATED.format(date=d))['Body'].read()
        except Exception:
            return d, None, None
        snap = json.loads(body)
        col, limited = {}, set()
        for k, it in (snap.get('items') or {}).items():
            if not isinstance(it, dict):
                continue
            try:
                v = int(it.get('us_estimate') or 0)
            except (TypeError, ValueError):
                continue
            if v > 0:
                col[k] = v
            if it.get(vd.BAND_LIMITED_FIELD):
                limited.add(k)
        return d, col, limited

    cols, marked = {}, set()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for d, col, lim in ex.map(one, dates):
            if col is not None:
                cols[d] = col
                marked |= lim
    return cols, marked


def measure(cols: dict[str, dict[str, int]], marked: set) -> dict:
    series = defaultdict(list)
    for d in sorted(cols):
        for k, v in cols[d].items():
            series[k].append((d, v))

    multi = {k: s for k, s in series.items() if len(s) >= 2}
    by_band = Counter()
    limited_repeat = 0
    repeats = 0
    dup_total = 0
    adj_same = adj_pairs = 0

    for k, s in multi.items():
        vals = [v for _, v in s]
        c = Counter(vals)
        dups = sum(n - 1 for n in c.values() if n > 1)
        if dups:
            if vd.is_band_limited(vals) or k in marked:
                limited_repeat += 1
            else:
                repeats += 1
                by_band[band(max(vals))] += 1
            dup_total += dups
        for i in range(1, len(s)):
            if (date.fromisoformat(s[i][0])
                    - date.fromisoformat(s[i - 1][0])).days != 1:
                continue
            adj_pairs += 1
            if s[i][1] == s[i - 1][1]:
                adj_same += 1

    return {
        'items_multi_day': len(multi),
        'items_repeating': repeats,
        'items_repeating_band_limited': limited_repeat,
        'duplicate_readings': dup_total,
        'by_band': dict(by_band),
        'band_limited_items': sum(
            1 for k, s in multi.items()
            if vd.is_band_limited([v for _, v in s]) or k in marked),
        'adjacent_identical': adj_same,
        'adjacent_pairs': adj_pairs,
        'last_digit': _digits(multi),
    }


def _digits(multi) -> dict:
    c = Counter()
    for s in multi.values():
        for _, v in s:
            c[v % 10] += 1
    tot = sum(c.values()) or 1
    return {str(d): round(100.0 * c[d] / tot, 2) for d in range(10)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--end', default='')
    ap.add_argument('--days', type=int, default=vd.WINDOW_DAYS)
    ap.add_argument('--json', dest='json_out', default='')
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    for noisy in ('botocore', 'boto3', 'urllib3', 's3transfer'):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    end = (date.fromisoformat(args.end) if args.end
           else datetime.now(timezone.utc).date() - timedelta(days=1))
    dates = vd.window_dates(end, args.days)
    cols, marked = read(dates)
    print(f'window {dates[0]} .. {dates[-1]}  '
          f'({len(cols)}/{len(dates)} snapshots loaded)')
    r = measure(cols, marked)
    print(f"items with 2+ days          : {r['items_multi_day']}")
    print(f"items repeating a value     : {r['items_repeating']}")
    print(f"  of which band limited     : "
          f"{r['items_repeating_band_limited']} (counted separately)")
    print(f"duplicate readings total    : {r['duplicate_readings']}")
    for b in ('>100K', '10K-100K', '1K-10K', '<1K'):
        print(f"  {b:<10} {r['by_band'].get(b, 0)}")
    print(f"band-limited items in window: {r['band_limited_items']}")
    print(f"adjacent-day identical      : {r['adjacent_identical']} / "
          f"{r['adjacent_pairs']} pairs "
          f"({100.0 * r['adjacent_identical'] / max(1, r['adjacent_pairs']):.2f}%)")
    print(f"last digit distribution     : {r['last_digit']}")
    if args.json_out:
        with open(args.json_out, 'w') as fh:
            json.dump(r, fh, indent=2, sort_keys=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
