#!/usr/bin/env python3
"""Time the streaming window read path and prove it renders the same.

Runs the streaming rail twice over the same window, once reading the
lean per-day index and once forced onto the full dated snapshots, and
compares every value both paths produce: first the merged estimates
snapshot every annotator consumes, then the served view payload.

Every value, not a sample. One difference anywhere fails the run.

    python3 -m scripts.trends_scrapers.verify_stream_window_index 1 7 30

Both view runs happen in one warm process with the index flag flipped
between them, so the only thing that changes is which copy of each
dated day was read.

The live-scraped cards (search, headlines, the outlet rails, movers,
the people miner and the fused list that blends them) are compared
separately and reported, not asserted: their upstream feeds move on
their own between two runs a minute apart, so a difference there says
nothing about this change. Everything fed by the estimates archive is
asserted.

Read-only. Nothing is written, no clickstream is touched.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import trends_iq as T  # noqa: E402


# Cards whose contents are settled by the dated estimates archive and
# the dated platform snapshots. These must match exactly.
ARCHIVE_CARDS = (
    'streaming_trending', 'fast_trending', 'music_trending',
    'podcasts_trending', 'books_trending', 'comics_trending',
    'libby_trending', 'broadway_trending', 'films_ticketing',
    'gaming_trending', 'wikipedia_trending', 'lens_scores',
    'lens_config', 'lens_cutoffs',
)

# Cards driven by feeds that move on their own between two runs.
# Reported for information, never asserted.
LIVE_CARDS = (
    'trending_searches', 'trending_searches_by_category',
    'trending_headlines', 'articles_by_source', 'trending_people',
    'business_news', 'business_news_by_source',
    'wall_street_news', 'wall_street_news_by_source',
    'movers', 'fused_trending', 'products_by_retailer',
)

# Fields that record when a run happened rather than what it found.
VOLATILE_KEYS = {
    'generated_at', 'computed_at', 'fetched_at', 'cached_at',
    'elapsed_ms', 'elapsed', 'duration_ms', 'served_at', 'built_at',
}


def diff(a, b, path='', out=None, limit=40):
    """Every leaf difference between two payloads."""
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k in VOLATILE_KEYS:
                continue
            if k not in a:
                out.append(f'{path}.{k}: missing on the index run')
            elif k not in b:
                out.append(f'{path}.{k}: missing on the full run')
            else:
                diff(a[k], b[k], f'{path}.{k}', out, limit)
            if len(out) >= limit:
                return out
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f'{path}: length {len(a)} against {len(b)}')
            return out
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f'{path}[{i}]', out, limit)
            if len(out) >= limit:
                return out
    elif a != b:
        out.append(f'{path}: {a!r} against {b!r}')
    return out


def count_values(obj) -> int:
    if isinstance(obj, dict):
        return sum(count_values(v) for k, v in obj.items()
                   if k not in VOLATILE_KEYS)
    if isinstance(obj, list):
        return sum(count_values(v) for v in obj)
    return 1


def _view(n: int) -> dict:
    return T.compute_view({'geo_type': 'National', 'geo_value': '',
                           'lookback_days': n}, force_refresh=True)


def main() -> int:
    windows = [int(x) for x in (sys.argv[1:] or ['1', '7', '30'])]
    failures = 0

    print('Poster and snapshot caches warm on a process that has already '
          'served a view, so warm the process first.', flush=True)
    t0 = time.time()
    T._STREAM_WINDOW_INDEX_ENABLED = True
    _view(7)
    print(f'warm-up view: {time.time() - t0:.2f}s\n', flush=True)

    for n in windows:
        print(f'=== window {n}d ===', flush=True)

        T._STREAM_WINDOW_INDEX_ENABLED = True
        t0 = time.time()
        lean = T._accumulate_stream_estimates_over_window(n)
        t_lean = time.time() - t0

        T._STREAM_WINDOW_INDEX_ENABLED = False
        t0 = time.time()
        full = T._accumulate_stream_estimates_over_window(n)
        t_full = time.time() - t0

        print(f'  estimates archive read: index {t_lean:6.2f}s   '
              f'full {t_full:6.2f}s   '
              f'{t_full / max(t_lean, 0.001):.1f}x faster')
        print(f'  days summed:            index '
              f'{(lean or {}).get("window_days_fetched")}+'
              f'{(lean or {}).get("window_prev_days_fetched")} of {n}+{n}'
              f'   full '
              f'{(full or {}).get("window_days_fetched")}+'
              f'{(full or {}).get("window_prev_days_fetched")} of {n}+{n}')

        d = diff(lean, full, 'estimates')
        if d:
            failures += 1
            print(f'  MERGED SNAPSHOT DIFFERS ({len(d)} shown):')
            for line in d:
                print('     ', line)
        else:
            print(f'  merged snapshot identical across '
                  f'{count_values(lean):,} values')

        T._STREAM_WINDOW_INDEX_ENABLED = True
        t0 = time.time()
        va = _view(n)
        t_va = time.time() - t0
        T._STREAM_WINDOW_INDEX_ENABLED = False
        t0 = time.time()
        vb = _view(n)
        t_vb = time.time() - t0
        print(f'  whole view:             index {t_va:6.2f}s   '
              f'full {t_vb:6.2f}s')

        ca = (va or {}).get('cards') or {}
        cb = (vb or {}).get('cards') or {}
        sub_a = {k: ca.get(k) for k in ARCHIVE_CARDS}
        sub_b = {k: cb.get(k) for k in ARCHIVE_CARDS}
        d = diff(sub_a, sub_b, 'cards', limit=60)
        if d:
            failures += 1
            print(f'  ARCHIVE CARDS DIFFER ({len(d)} shown):')
            for line in d:
                print('     ', line)
        else:
            print(f'  archive-fed cards identical across '
                  f'{count_values(sub_a):,} values '
                  f'({", ".join(ARCHIVE_CARDS[:4])}, ...)')

        live_d = diff({k: ca.get(k) for k in LIVE_CARDS},
                      {k: cb.get(k) for k in LIVE_CARDS},
                      'cards', limit=5)
        print(f'  live-feed cards:        '
              f'{"drifted between the two runs (expected)" if live_d else "also identical"}')
        print(flush=True)

    print('FAIL' if failures else
          'PASS: every archive-fed value identical on both read paths')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
