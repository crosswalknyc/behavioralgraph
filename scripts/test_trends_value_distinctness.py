#!/usr/bin/env python3
"""Regression cover for the 60-day distinctness rule (Jenna 2026-09-15).

Hermetic: no S3, no network, no clickstream. Exercises the resolver and
the snapshot pass directly.

  python3 scripts/test_trends_value_distinctness.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.trends_scrapers import value_distinctness as vd  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, label: str) -> None:
    print(('  ok   ' if cond else '  FAIL ') + label)
    if not cond:
        FAILS.append(label)


def hist(start: date, values: list[int]) -> dict[str, int]:
    return {(start + timedelta(days=i)).isoformat(): v
            for i, v in enumerate(values)}


def main() -> int:
    key = 'podcast|The Daily|The New York Times'
    d = date(2026, 9, 15)

    print('a distinct reading is accepted untouched')
    h = hist(date(2026, 9, 1), [250_000, 261_400, 249_120])
    v, how = vd.resolve_value(999_111, key, d, h, prev_value=990_000)
    check((v, how) == (999_111, 'ok'), 'unchanged and reported as ok')

    print('a repeat of any earlier day moves')
    v, how = vd.resolve_value(261_400, key, d, h, prev_value=250_500)
    check(how == 'moved', 'reported as moved')
    check(v not in set(h.values()), 'no longer matches any earlier day')
    check(abs(v / 261_400 - 1.0) < 0.01,
          f'level held within 1 percent (moved to {v:,})')

    print('the move is idempotent')
    v2, how2 = vd.resolve_value(v, key, d, h, prev_value=250_500)
    check((v2, how2) == (v, 'ok'), 'a second pass changes nothing')

    print('a move never lifts the reading above the ceiling')
    h2 = hist(date(2026, 9, 1), [1_000, 1_000, 1_000])
    v, _ = vd.resolve_value(1_000, key, d, h2, ceiling=1_000)
    check(0 < v <= 1_000, f'stayed at or below the ceiling ({v})')

    print('a move stays clear of the dead-chip zone')
    h3 = hist(date(2026, 9, 1), [500_000, 512_000])
    v, _ = vd.resolve_value(512_000, key, d, h3, prev_value=512_400)
    check(abs(v / 512_400 - 1.0) >= vd.DEAD_ZONE,
          f'day-over-day move clears 0.2 percent ({v:,} vs 512,400)')

    print('band capacity decides where uniqueness is mandatory')
    check(not vd.is_band_limited([250_000]), 'a six-figure item is not limited')
    check(vd.is_band_limited([18]), 'a 1-to-18 reader story is limited')
    check(vd.band_capacity([18]) < vd.MIN_DISTINCT_CAPACITY,
          'and its band cannot hold 60 distinct integers')

    print('a band-limited item is spaced, not inflated')
    tiny = 'wattpad_story|Red Door|nikkpatel'
    h4 = hist(date(2026, 8, 1), [1, 4, 2, 5, 3, 6, 2, 4, 1, 5])
    v, how = vd.resolve_value(4, tiny, d, h4, prev_value=3)
    check(how == 'spaced', 'reported as spaced')
    check(1 <= v <= 8, f'stayed inside the honest band ({v})')
    check(v != 3, 'never equals the previous day')

    print('the snapshot pass marks band-limited rows and moves the rest')
    items = {
        'podcast:daily': {'kind': 'podcast', 'display_title': 'The Daily',
                          'artist': 'NYT', 'us_estimate': 261_400,
                          'us_estimate_low': 240_000,
                          'us_estimate_high': 300_000, 'by_platform': {}},
        'wattpad_story:red door': {'kind': 'wattpad_story',
                                   'display_title': 'Red Door',
                                   'artist': 'nikkpatel', 'us_estimate': 4,
                                   'us_estimate_low': 3,
                                   'us_estimate_high': 6, 'by_platform': {}},
        'podcast:clean': {'kind': 'podcast', 'display_title': 'Clean',
                          'artist': '', 'us_estimate': 777_123,
                          'us_estimate_low': 700_000,
                          'us_estimate_high': 800_000, 'by_platform': {}},
    }
    history = {'podcast:daily': h, 'wattpad_story:red door': h4,
               'podcast:clean': hist(date(2026, 9, 1), [700_111, 710_222])}
    stats = vd.enforce_snapshot(items, history, d.isoformat())
    check(stats['moved'] == 1, f"one reading moved ({stats['moved']})")
    check(stats['spaced'] == 1, f"one reading spaced ({stats['spaced']})")
    check(stats['band_limited'] == 1,
          f"one row band limited ({stats['band_limited']})")
    check(items['wattpad_story:red door'].get(vd.BAND_LIMITED_FIELD) is True,
          'the band-limited row carries the marker')
    check(vd.BAND_LIMITED_FIELD not in items['podcast:daily'],
          'a wide-band row carries no marker')
    check(items['podcast:clean']['us_estimate'] == 777_123,
          'an already-distinct row is untouched')
    lo = items['podcast:daily']['us_estimate_low']
    mid = items['podcast:daily']['us_estimate']
    hi = items['podcast:daily']['us_estimate_high']
    check(lo <= mid <= hi, 'the band moved with the reading and stays ordered')

    print('the pass is idempotent at snapshot level')
    before = {k: v['us_estimate'] for k, v in items.items()}
    vd.enforce_snapshot(items, history, d.isoformat())
    after = {k: v['us_estimate'] for k, v in items.items()}
    check(before == after, 'a second pass over the same snapshot is a no-op')

    print()
    if FAILS:
        print(f'{len(FAILS)} FAILED')
        for f in FAILS:
            print(f'  - {f}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
