#!/usr/bin/env python3
"""Time the streaming read path and prove it renders the same.

Builds the whole view twice per window on one process, once with the
published reads (the weeks-on-chart index, the resolved poster art,
and the single-pass snapshot prefetch) and once with the section
working all three out for itself, then compares every value both runs
produce. Every value, not a sample. One difference anywhere fails the
run.

    python3 -m scripts.trends_scrapers.verify_streaming_read_path 1 7 30

Both runs start genuinely cold: the in-process caches are cleared
between them, so the slow run really does re-scan the archive and
re-resolve every poster rather than reading what the fast run left
behind. That is the whole point, and it is why a full run takes a few
minutes.

The live-scraped cards (search, headlines, the outlet rails, movers,
the people miner and the fused list that blends them) are compared and
reported, never asserted: their upstream feeds move on their own
between two runs a minute apart, so a difference there says nothing
about this change. Everything settled by the archive is asserted.

Read-only. Nothing is written, no clickstream is touched.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import trends_iq as T  # noqa: E402

# Settled by the dated archive and the dated platform snapshots, so
# these have to match exactly.
ARCHIVE_CARDS = (
    'streaming_trending', 'fast_trending', 'music_trending',
    'podcasts_trending', 'books_trending', 'comics_trending',
    'libby_trending', 'broadway_trending', 'films_ticketing',
    'gaming_trending', 'wikipedia_trending', 'lens_scores',
    'lens_config', 'lens_cutoffs',
)

# Driven by feeds that move on their own between two runs.
LIVE_CARDS = (
    'trending_searches', 'trending_searches_by_category',
    'trending_headlines', 'articles_by_source', 'trending_people',
    'philanthropy_news', 'philanthropy_news_by_source',
    'business_news', 'business_news_by_source',
    'wall_street_news', 'wall_street_news_by_source',
    'movers', 'fused_trending', 'products_by_retailer',
)

# Record when a run happened rather than what it found.
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
                out.append(f'{path}.{k}: missing on the published run')
            elif k not in b:
                out.append(f'{path}.{k}: missing on the worked-out run')
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


def _cold(fast: bool):
    """Put the process back to how it starts, on the chosen path."""
    T._STREAMING_FAST_READ_ENABLED = fast
    T._reset_streaming_read_caches()


def main() -> int:
    windows = [int(x) for x in (sys.argv[1:] or ['1', '7', '30'])]
    failures = 0

    print('Each run below starts cold on its own path, so the timings '
          'are the cold ones and the comparison is real.\n', flush=True)

    for n in windows:
        print(f'=== window {n}d ===', flush=True)

        # Section on its own, cold, both ways. This is the number the
        # section budget sees on a freshly deployed worker.
        _cold(False)
        t0 = time.time()
        T._fetch_streaming_trending(None, n, keywords=None)
        sec_slow = time.time() - t0
        t0 = time.time()
        T._fetch_streaming_trending(None, n, keywords=None)
        sec_slow_warm = time.time() - t0

        _cold(True)
        t0 = time.time()
        T._fetch_streaming_trending(None, n, keywords=None)
        sec_fast = time.time() - t0
        t0 = time.time()
        T._fetch_streaming_trending(None, n, keywords=None)
        sec_fast_warm = time.time() - t0

        print(f'  streaming_trending cold: worked out {sec_slow:6.2f}s   '
              f'published {sec_fast:6.2f}s   '
              f'{sec_slow / max(sec_fast, 0.001):.1f}x faster')
        print(f'  streaming_trending warm: worked out {sec_slow_warm:6.2f}s'
              f'   published {sec_fast_warm:6.2f}s')

        # Whole view, cold, both ways.
        _cold(False)
        t0 = time.time()
        vb = _view(n)
        t_vb = time.time() - t0
        _cold(True)
        t0 = time.time()
        va = _view(n)
        t_va = time.time() - t0
        print(f'  whole view cold:         worked out {t_vb:6.2f}s   '
              f'published {t_va:6.2f}s')

        pend_a = (va or {}).get('pending_sections') or []
        pend_b = (vb or {}).get('pending_sections') or []
        if pend_a or pend_b:
            failures += 1
            print(f'  PENDING SECTIONS: published {pend_a}, '
                  f'worked out {pend_b}')
        else:
            print('  pending sections:        none on either run')

        ca = (va or {}).get('cards') or {}
        cb = (vb or {}).get('cards') or {}

        st_a, st_b = ca.get('streaming_trending'), cb.get('streaming_trending')
        d = diff(st_a, st_b, 'streaming_trending', limit=60)
        if d:
            failures += 1
            print(f'  STREAMING DIFFERS ({len(d)} shown):')
            for line in d:
                print('     ', line)
        else:
            print(f'  streaming identical across '
                  f'{count_values(st_a):,} rendered values '
                  f'({len(st_a or {})} platforms)')

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
                  f'{count_values(sub_a):,} values')

        live_d = diff({k: ca.get(k) for k in LIVE_CARDS},
                      {k: cb.get(k) for k in LIVE_CARDS},
                      'cards', limit=5)
        print(f'  live-feed cards:         '
              f'{"drifted between the two runs (expected)" if live_d else "also identical"}')
        print(flush=True)

    _cold(True)
    print('FAIL' if failures else
          'PASS: every archive-fed value identical on both read paths')
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
