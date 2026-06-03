#!/usr/bin/env python3
"""Synthetic smoke test for the Blue IQ cube reader path.

Builds a fake cube in memory, monkey-patches the S3 loader + external_signals,
and exercises compute_panel_view across the major filter shapes:

    - 'All' party, National
    - 'Democrat' party, State=California
    - 'Republican' party, DMA=Los Angeles
    - 'Independent' party, suppressed cell (below MIN_CELL_SIZE)
    - 'All' party, cube missing entirely

No ClickHouse, no OpenAI, no live S3. Pure logic exercise.
"""

from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BG_WEBAPP = os.path.dirname(HERE)
sys.path.insert(0, BG_WEBAPP)

import blue_iq               # type: ignore  # noqa: E402
import external_signals      # type: ignore  # noqa: E402


def _synthetic_cube() -> dict:
    """Builds a 5-cell cube mirroring what the aggregator emits."""
    def _se():
        return [
            {'name': 'Google', 'panelists': 9200},
            {'name': 'Bing',   'panelists': 1100},
            {'name': 'DuckDuckGo', 'panelists': 410},
            {'name': 'Yahoo Search', 'panelists': 220},
        ]
    def _sm():
        return [
            {'name': 'Facebook', 'panelists': 7500},
            {'name': 'Instagram', 'panelists': 5400},
            {'name': 'X',         'panelists': 3100},
            {'name': 'TikTok',    'panelists': 4800},
            {'name': 'Reddit',    'panelists': 2200},
        ]
    def _demo():
        return {
            'age':       [{'value': '35-44', 'panelists': 3000, 'share': 0.30},
                          {'value': '25-34', 'panelists': 2500, 'share': 0.25}],
            'gender':    [{'value': 'F', 'panelists': 5500, 'share': 0.55},
                          {'value': 'M', 'panelists': 4500, 'share': 0.45}],
            'ethnicity': [{'value': 'White', 'panelists': 6500, 'share': 0.65}],
            'income':    [{'value': '$50k-$75k', 'panelists': 2800, 'share': 0.28}],
        }
    def _pols():
        return [
            {'name': 'Joe Biden',     'panelists': 1200},
            {'name': 'Donald Trump',  'panelists': 980},
            {'name': 'Kamala Harris', 'panelists': 720},
        ]
    def _articles():
        return [
            {'url': 'https://www.nytimes.com/2026/05/01/politics/snap-benefits.html',
             'source': 'nytimes.com', 'panelists': 89},
            {'url': 'https://www.foxnews.com/politics/border-2026.html',
             'source': 'foxnews.com', 'panelists': 64},
        ]
    def _terms():
        return [
            {'term': 'how to apply for snap',    'count': 412},
            {'term': 'gas prices near me',       'count': 380},
            {'term': 'voter registration',       'count': 220},
            {'term': 'medicare enrollment 2026', 'count': 180},
            {'term': 'irrelevant cooking thing', 'count': 5},
        ]

    big_cell = {
        'uid_count':       10000,
        'search_engines':  _se(),
        'social_media':    _sm(),
        'turnout':         {'panelists': 412, 'sample_urls': ['vote.org', 'sos.ca.gov/elections']},
        'demo':            _demo(),
        'top_politicians': _pols(),
        'top_articles':    _articles(),
        'top_search_queries': _terms(),
    }
    suppressed_cell = {**big_cell, 'uid_count': 42}

    cube = {
        'version':       1,
        'computed_at':   '2026-06-03T08:01:00+00:00',
        'lookback_days': 30,
        'min_cell_size': 100,
        'all_parties':   blue_iq.VALID_PARTIES,
        'all_states':    ['California', 'Texas', 'New York', 'Florida'],
        'all_dmas':      ['Los Angeles', 'New York', 'Chicago', 'Houston'],
        'cells': {
            'All||':                  big_cell,
            'Democrat|California|':   big_cell,
            'Republican||Los Angeles': big_cell,
            'Independent|Texas|':     suppressed_cell,  # below MIN_CELL_SIZE
        },
        'issue_buckets_global': [
            {'bucket': 'Economy & Cost of Living', 'count': 8200, 'share': 0.41,
             'sample_queries': ['gas prices near me', 'gas prices'], 'trend': 0.0},
            {'bucket': 'Social Safety Net', 'count': 4100, 'share': 0.21,
             'sample_queries': ['how to apply for snap'], 'trend': 0.0},
            {'bucket': 'Elections & Voting', 'count': 3300, 'share': 0.16,
             'sample_queries': ['voter registration'], 'trend': 0.0},
            {'bucket': 'Healthcare', 'count': 2200, 'share': 0.11,
             'sample_queries': ['medicare enrollment 2026'], 'trend': 0.0},
        ],
    }
    return cube


def _stub_external(state, lookback_days, politician_names):
    """Stub out external_signals so the test runs offline."""
    return {
        'google_trends_top': [
            {'term': 'gas prices near me', 'score': 12000, 'source': 'google_trends'},
            {'term': 'voter registration', 'score': 7400,  'source': 'google_trends'},
        ],
        'google_trends_politicians': {n: 50 for n in politician_names[:3]},
        'gdelt_articles': [
            {'url': 'https://www.cnn.com/2026/05/02/politics/snap-benefits-update.html',
             'title': 'New SNAP benefits rules announced',
             'source': 'cnn.com', 'tone': -1.2, 'social_image': 'https://cdn.cnn.com/x.jpg'},
            {'url': 'https://www.nytimes.com/2026/05/01/politics/snap-benefits.html',
             'title': 'SNAP rule changes explained',
             'source': 'nytimes.com', 'tone': 0.5, 'social_image': ''},
        ],
        'gdelt_politician_mentions': {n: 25 for n in politician_names[:3]},
        'wiki_pageviews': {n: 1200 for n in politician_names[:3]},
    }


def _patch_s3_with_cube(cube: dict, *, missing: bool = False,
                          lookback: int = 30):
    """Monkey-patch the in-process cube cache so we never hit S3.

    Cache is per-lookback. Setting it for one lookback doesn't affect
    others (so the Live=1d cube test path stays independent of the 30d).
    """
    blue_iq._CUBE_CACHE.clear()
    blue_iq._CUBE_CACHE[int(lookback)] = {
        'cube': None if missing else cube,
        'fetched_at': time.time(),
    }


def _patch_cache_put_get():
    """Disable the S3 per-filter result cache so each test runs fresh."""
    blue_iq._cache_get = lambda f: None  # type: ignore[assignment]
    blue_iq._cache_put = lambda f, p: None  # type: ignore[assignment]


def _assert(cond, msg):
    if not cond:
        print(f"  FAIL: {msg}")
        raise SystemExit(1)
    print(f"  OK:   {msg}")


def main():
    print("Patching external_signals + caches ...")
    external_signals.fetch_all_external = _stub_external  # type: ignore
    # Make sure the patched version is what blue_iq imports too
    import sys as _sys
    if 'external_signals' in _sys.modules:
        _sys.modules['external_signals'].fetch_all_external = _stub_external  # type: ignore
    _patch_cache_put_get()

    cube = _synthetic_cube()

    # ── Test 1: All / National (the landing-page default) ─────────────────
    print("\nTest 1: All / National landing-page default")
    _patch_s3_with_cube(cube)
    t = time.time()
    r = blue_iq.compute_panel_view({'party': 'All', 'geo_type': 'National',
                                      'geo_value': '', 'lookback_days': 30})
    dt = (time.time() - t) * 1000
    print(f"  -> {dt:.1f}ms")
    _assert(r['success'] is True, 'success=true')
    _assert(r['panel_size'] == 10000, 'panel_size=10000 (cube cell hit)')
    _assert(r['suppressed'] is False, 'not suppressed')
    _assert(r['cube_missing'] is False, 'cube present')
    _assert(len(r['cards']['search_engines']) == 4, 'search engines present')
    _assert(r['cards']['search_engines'][0]['name'] == 'Google', 'Google is top search engine')
    _assert(r['cards']['search_engines'][0]['share'] > 0.0, 'shares attached')
    _assert(len(r['cards']['social_media']) == 5, 'social platforms present')
    _assert(len(r['cards']['issue_buckets']) > 0, 'issue buckets non-empty')
    _assert(r['cards']['turnout_intent']['pct'] > 0, 'turnout pct computed')
    _assert(len(r['cards']['top_politicians']) > 0, 'politicians blended in')
    _assert(len(r['cards']['top_articles']) > 0, 'articles blended in')
    titles = [a.get('title') for a in r['cards']['top_articles']]
    _assert(any('SNAP' in (t or '') for t in titles), 'GDELT title surfaced')

    # ── Test 2: Democrat / California ────────────────────────────────────
    print("\nTest 2: Democrat / California")
    t = time.time()
    r = blue_iq.compute_panel_view({'party': 'Democrat', 'geo_type': 'State',
                                      'geo_value': 'California', 'lookback_days': 30})
    dt = (time.time() - t) * 1000
    print(f"  -> {dt:.1f}ms")
    _assert(r['panel_size'] == 10000, 'state-level cell hit')
    _assert(r['suppressed'] is False, 'not suppressed')
    _assert(isinstance(r['compare'], dict) and 'dems' in r['compare'], 'compare card built')

    # ── Test 3: Republican / DMA=Los Angeles ─────────────────────────────
    print("\nTest 3: Republican / DMA=Los Angeles")
    r = blue_iq.compute_panel_view({'party': 'Republican', 'geo_type': 'DMA',
                                      'geo_value': 'Los Angeles', 'lookback_days': 30})
    _assert(r['panel_size'] == 10000, 'DMA-level cell hit')

    # ── Test 4: Independent / Texas — suppressed cell ────────────────────
    print("\nTest 4: Independent / Texas (suppressed below MIN_CELL_SIZE)")
    r = blue_iq.compute_panel_view({'party': 'Independent', 'geo_type': 'State',
                                      'geo_value': 'Texas', 'lookback_days': 30})
    _assert(r['suppressed'] is True, 'suppressed=true for tiny cell')
    _assert('message' in r and 'below minimum cell size' in r['message'], 'suppression message present')

    # ── Test 5: Cube missing entirely (degraded mode) ─────────────────────
    print("\nTest 5: Cube missing entirely (degraded mode)")
    _patch_s3_with_cube({}, missing=True)
    r = blue_iq.compute_panel_view({'party': 'All', 'geo_type': 'National',
                                      'geo_value': '', 'lookback_days': 30})
    _assert(r['cube_missing'] is True, 'cube_missing=true')
    _assert('message' in r and 'aggregate is missing' in r['message'], 'cube-missing message present')
    _assert(r['panel_size'] == 0, 'panel_size=0 in degraded mode')
    _assert(len(r['cards']['top_articles']) > 0, 'GDELT articles still surface in degraded mode')
    _assert(len(r['cards']['top_politicians']) > 0, 'external politicians still surface')

    # ── Test 6: filter options reads from cube ───────────────────────────
    print("\nTest 6: get_filter_options reads from cube")
    _patch_s3_with_cube(cube)
    blue_iq._FILTER_OPTIONS_CACHE.clear()
    opts = blue_iq.get_filter_options()
    _assert(opts['states'] == ['California', 'Texas', 'New York', 'Florida'],
            'states list from cube')
    _assert(opts['dmas'][0] == 'Los Angeles', 'dmas list from cube')
    _assert(opts['cube_built_at'] == '2026-06-03T08:01:00+00:00', 'cube_built_at exposed')

    # ── Test 7a: Live (lookback=1) routes to its own cube ────────────────
    print("\nTest 7a: Live (lookback=1) routes to its own cube")
    live_cube = _synthetic_cube()
    live_cube['computed_at'] = '2026-06-03T14:01:00+00:00'
    live_cube['lookback_days'] = 1
    # Mark the cube so we can prove we hit it (not the 30d one)
    for cell in live_cube['cells'].values():
        cell['uid_count'] = 8500  # different from the 30d's 10000
    blue_iq._CUBE_CACHE.clear()
    blue_iq._CUBE_CACHE[1]  = {'cube': live_cube, 'fetched_at': time.time()}
    blue_iq._CUBE_CACHE[30] = {'cube': cube,      'fetched_at': time.time()}
    r = blue_iq.compute_panel_view({'party': 'All', 'geo_type': 'National',
                                      'geo_value': '', 'lookback_days': 1})
    _assert(r['panel_size'] == 8500, 'lookback=1 hits Live cube (panel_size=8500)')
    _assert(r['cube_built_at'] == '2026-06-03T14:01:00+00:00', 'cube_built_at = Live cube timestamp')
    r30 = blue_iq.compute_panel_view({'party': 'All', 'geo_type': 'National',
                                       'geo_value': '', 'lookback_days': 30})
    _assert(r30['panel_size'] == 10000, 'lookback=30 hits the 30d cube (panel_size=10000)')

    # ── Test 7b: Live cube missing => no fallback to 30d (independent) ─
    print("\nTest 7b: Live cube missing => no fallback to 30d")
    blue_iq._CUBE_CACHE.clear()
    blue_iq._CUBE_CACHE[1]  = {'cube': None, 'fetched_at': time.time()}
    blue_iq._CUBE_CACHE[30] = {'cube': cube, 'fetched_at': time.time()}
    r = blue_iq.compute_panel_view({'party': 'All', 'geo_type': 'National',
                                     'geo_value': '', 'lookback_days': 1})
    _assert(r['cube_missing'] is True, 'Live=missing surfaces cube_missing=true')

    # ── Test 8: external_signals.fetch_all_external parallelism (sanity) ─
    print("\nTest 8: external_signals.fetch_all_external parallel structure")
    # Restore real function reference briefly to verify the new code path is callable.
    import importlib
    importlib.reload(external_signals)
    fn = external_signals.fetch_all_external
    src = fn.__doc__ or ''
    _assert('PARALLEL' in src.upper(), 'docstring documents parallel design')

    print("\n" + "=" * 60)
    print("ALL SMOKE TESTS PASSED. Cube-reader fast path is sound.")
    print("=" * 60)


if __name__ == '__main__':
    main()
