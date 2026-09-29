#!/usr/bin/env python3
"""A Trends view whose sections raised must not cache as a good day.

Hermetic: every fetcher, the snapshot reader, the cache and the ops
alert are stubbed, so this never touches S3, never calls a paid
scraper, and never writes a cache entry.

Guards the defect behind the 2026-09-29 empty Trends/Rankers view. A
section that RAISED was recorded as None and never reached the TTL
choice, so a pass where all of them threw reported success, took the
full 24 hour TTL and sent no mail.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trends_iq as t  # noqa: E402

FAIL_MARKER = 'stubbed failure'


def _install_stubs(mode: str, sent: list):
    """Point every task the live branch can reach at a stub."""
    def boom(*a, **k):
        raise RuntimeError(FAIL_MARKER)

    def snap_ok(source, asof=None):
        return {'source': source, 'sources': {'x': {'items': [{'rank': 1}]}},
                'national': [{'rank': 1}], 'items': {}, 'error': None}

    # Exactly the snapshot sources the live branch fans out over.
    # Reads outside that fan-out (the headline rank-change annotator,
    # the why-trending annotator) are left working on purpose: they
    # run unguarded, so failing them would abort the whole build
    # instead of exercising the per-section path under test.
    fanout_sources = {
        'wikipedia_trending', 'music_charts', 'podcast_charts',
        'book_charts', 'comics_charts', 'film_ticketing', 'libby_trends',
        'wattpad_charts', 'goodreads_charts', 'broadway_grosses',
        'business_news', 'wall_street_news', 'stream_estimates',
        'headline_estimates', 'lens_scores',
    }

    def snap_boom(source, asof=None):
        if asof is None and source in fanout_sources:
            raise RuntimeError(FAIL_MARKER)
        return {'source': source, 'sources': {}, 'national': [],
                'items': {}, 'error': None}

    def empty_map(*a, **k):
        return {}

    def pack_ok(*a, **k):
        return ([], [])

    ok = {
        '_read_snapshot': snap_ok,
        '_fetch_trending_searches': lambda *a, **k: [],
        '_fetch_trending_headlines_and_sources': pack_ok,
        '_fetch_streaming_trending': empty_map,
        '_fetch_fast_trending': empty_map,
        '_fetch_gaming_trending': empty_map,
        'compute_search_movers': lambda *a, **k: {'available': True},
    }
    for name, fn in ok.items():
        if mode != 'fail':
            setattr(t, name, fn)
        else:
            setattr(t, name, snap_boom if name == '_read_snapshot' else boom)

    t._cache_get = lambda *a, **k: None          # never read the real cache
    t._cache_put = lambda *a, **k: None          # never write one either
    t._send_ops_alert = lambda kind, subj, body: sent.append(kind)


def _ttl_seconds(payload):
    gen = datetime.fromisoformat(payload['generated_at'])
    until = datetime.fromisoformat(payload['stale_until'])
    return (until - gen).total_seconds()


def run(mode):
    sent: list = []
    _install_stubs(mode, sent)
    payload = t.compute_view({'geo_type': 'National', 'geo_value': '',
                              'lookback_days': 1}, force_refresh=True)
    return payload, sent, _ttl_seconds(payload)


def main():
    failures = []

    payload, sent, ttl = run('fail')
    n_failed = len(payload.get('failed_sections') or [])
    print('every section raises:')
    print('  failed_sections : %d' % n_failed)
    print('  stale_until in  : %.0fs (partial=%ds, full=%ds)'
          % (ttl, t.PARTIAL_RETRY_TTL_S, t.CACHE_TTL_S))
    print('  ops alerts      : %s' % (sent or 'none'))
    if n_failed == 0:
        failures.append('sections raised but failed_sections was empty')
    if abs(ttl - t.PARTIAL_RETRY_TTL_S) > 2:
        failures.append('a failed pass was cached for %.0fs, expected %d'
                        % (ttl, t.PARTIAL_RETRY_TTL_S))
    if 'section_failed' not in sent:
        failures.append('no ops alert fired for a failed pass')
    if not all(FAIL_MARKER in s for s in (payload.get('failed_sections') or [])):
        failures.append('failed_sections did not name the exception')

    payload, sent, ttl = run('ok')
    n_failed = len(payload.get('failed_sections') or [])
    print('\nevery section succeeds:')
    print('  failed_sections : %d' % n_failed)
    print('  stale_until in  : %.0fs' % ttl)
    print('  ops alerts      : %s' % (sent or 'none'))
    if n_failed:
        failures.append('a clean pass reported %d failed sections' % n_failed)
    if abs(ttl - t.CACHE_TTL_S) > 2:
        failures.append('a clean pass was cached for %.0fs, expected %d'
                        % (ttl, t.CACHE_TTL_S))
    if sent:
        failures.append('a clean pass fired an ops alert: %s' % sent)

    print()
    if failures:
        for f in failures:
            print('FAIL: %s' % f)
        return 1
    print('PASS: a failed pass retries in minutes and pages; a clean one '
          'keeps the day.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
