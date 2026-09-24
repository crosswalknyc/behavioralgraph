#!/usr/bin/env python3
"""Regression: the coverage pass never prices a service rail off a rank.

The rule: a row on service X shows a reading for X, or its own
service-scoped last reading, or nothing. Never a figure derived from
where it sits in a list.

`trends_iq._ensure_full_audience_coverage` has two branches that stamp
`est_basis='rank_tier'`. The reader branch (headline lists) prices a
story off its OUTLET's median, not its position, and headline lists
are not service rails. The stream branch samples a distribution at
the row's rank percentile, and until 2026-09-24 it was gated only on
the streaming / FAST slug, so it still fired on the chart panels that
are service rails too (Wattpad, Apple Comics, Spotify, Steam). This
test pins the closed state:

  * every path that names a service resolves as a service rail
  * on a service rail, a row with no reading and no history is left
    blank, and the withheld count says so
  * on a cross-platform list the tier still applies
  * a rank-tier block planted on a service rail is removed by the
    post-walk invariant, so a future change cannot reopen the branch
    without the log saying so

No network, no board, no clickstream: every history lookup is stubbed
to "nothing stored".

    python3 -m scripts.trends_scrapers.test_coverage_rank_tier_guard
"""
from __future__ import annotations

import copy
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

logging.disable(logging.CRITICAL)

import trends_iq as t  # noqa: E402

FAILURES: list = []


def check(ok: bool, what: str) -> None:
    print(f'{"PASS" if ok else "FAIL"}  {what}')
    if not ok:
        FAILURES.append(what)


_SERVICE_PATHS = (
    'streaming_trending.netflix.items',
    'streaming_trending.paramountplus.items',
    'streaming_trending.starz_amazon.items',
    'fast_trending.pluto.films',
    'fast_trending.roku.channels',
    'music_trending.spotify.items',
    'podcasts_trending.apple.items',
    'books_trending.amazon.items',
    'books_trending.wattpad_fantasy.items',
    'books_trending.goodreads_most_read.items',
    'libby_trending.ebook.items',
    'comics_trending.apple_comics.items',
    'gaming_trending.steam.top_sellers',
    'gaming_trending.meta_quest.paid',
    'gaming_trending.xbox_gamepass.items',
)
_CROSS_PLATFORM_PATHS = (
    'trending_searches',
    'trending_searches_by_category.tech',
    'trending_people',
    'wikipedia_trending',
    'fused_trending',
    'broadway_trending',
    'movers',
)


def _rows(prefix: str, n: int) -> list:
    return [{'title': f'{prefix} title {i}', 'rank': i} for i in range(1, n + 1)]


def _cards() -> dict:
    """A payload with one list per path family, every row unpriced."""
    return {
        'streaming_trending': {
            'netflix': {'items': _rows('nf', 6)},
            'paramountplus': {'items': _rows('pp', 6)},
            'starz_amazon': {'items': _rows('sa', 6)},
        },
        'fast_trending': {
            'pluto': {'films': _rows('pl', 6)},
            'roku': {'channels': [{'channel_name': f'roku ch {i}', 'rank': i}
                                  for i in range(1, 7)]},
        },
        'music_trending': {'spotify': {'items': _rows('sp', 6)}},
        'podcasts_trending': {'apple': {'items': _rows('ap', 6)}},
        'books_trending': {
            'amazon': {'items': _rows('am', 6)},
            'wattpad_fantasy': {'items': _rows('wf', 6)},
            'goodreads_most_read': {'items': _rows('gr', 6)},
        },
        'libby_trending': {'ebook': {'items': _rows('lb', 6)}},
        'comics_trending': {'apple_comics': {'items': _rows('ac', 6)}},
        'gaming_trending': {
            'steam': {'top_sellers': _rows('st', 6)},
            'meta_quest': {'paid': _rows('mq', 6)},
            'xbox_gamepass': {'items': _rows('xb', 6)},
        },
        'trending_searches': [{'term': f'search {i}', 'rank': i}
                              for i in range(1, 7)],
        'trending_people': [{'name': f'person {i}', 'rank': i}
                            for i in range(1, 7)],
        'broadway_trending': _rows('bw', 6),
    }


def _walk_rows(node, path=''):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk_rows(v, f'{path}.{k}' if path else k)
        return
    if isinstance(node, list):
        rows = [x for x in node if isinstance(x, dict)
                and t._coverage_item_title(x)]
        if rows:
            for r in rows:
                yield path, r
            return
        for x in node:
            yield from _walk_rows(x, path)


def main() -> int:
    # 1. Resolver: every service path is a service rail, every
    #    cross-platform path is not.
    for p in _SERVICE_PATHS:
        check(t._coverage_is_service_rail(p), f'service rail: {p}')
    for p in _CROSS_PLATFORM_PATHS:
        check(not t._coverage_is_service_rail(p), f'cross-platform: {p}')

    # 2. Run the pass with no history anywhere. Stub every lookup the
    #    pass makes so this is pure control flow.
    saved = {
        name: getattr(t, name) for name in (
            '_carry_find_prior', '_carry_find_prior_reader',
            '_coverage_baselines_from_estimates',
            '_coverage_reader_baselines')
    }
    t._carry_find_prior = lambda kind, title, slug: None
    t._carry_find_prior_reader = lambda title: None
    t._coverage_baselines_from_estimates = lambda snap: {
        'by_kind': {'search_term': [1000.0, 5000.0, 20000.0, 90000.0],
                    'title': [1000.0, 5000.0, 20000.0, 90000.0],
                    'trending_person': [1000.0, 5000.0, 20000.0]},
        'by_platform': {}}
    t._coverage_reader_baselines = lambda snap: ({}, 25_000.0)
    try:
        cards = _cards()
        counts = t._ensure_full_audience_coverage(cards, {}, {},
                                                  asof='2026-09-24')
    finally:
        for name, fn in saved.items():
            setattr(t, name, fn)

    service_rows = cross_rows = 0
    for path, row in _walk_rows(cards):
        blk = row.get('us_streams')
        if t._coverage_is_service_rail(path):
            service_rows += 1
            check(blk is None,
                  f'left blank on service rail: {path} / '
                  f'{t._coverage_item_title(row)}')
        else:
            cross_rows += 1
            check(isinstance(blk, dict)
                  and blk.get('est_basis') == 'rank_tier'
                  and blk.get('no_prior_reading') is True
                  and int(blk.get('us_estimate') or 0) >= 100,
                  f'tier still applies off a service rail: {path} / '
                  f'{t._coverage_item_title(row)}')
    check(service_rows > 0 and cross_rows > 0,
          f'fixture exercised both branches (service={service_rows}, '
          f'cross={cross_rows})')
    check(counts.get('withheld_service') == service_rows,
          f'withheld count equals service rows: '
          f'{counts.get("withheld_service")} == {service_rows}')
    check(counts.get('rank_tier') == cross_rows,
          f'rank-tier count equals cross-platform rows: '
          f'{counts.get("rank_tier")} == {cross_rows}')
    check(counts.get('carried_forward') == 0, 'nothing carried (no history)')

    # 3. The post-walk invariant finds nothing on a clean payload...
    check(t._strip_rank_tier_from_service_rails(copy.deepcopy(cards)) == [],
          'invariant finds nothing to remove on the finished payload')

    # ...and removes a planted leak on a service rail, leaving the
    # cross-platform tier and researched rows alone.
    planted = copy.deepcopy(cards)
    victim = planted['streaming_trending']['paramountplus']['items'][3]
    victim['us_streams'] = {'us_estimate': 123_457,
                            'est_basis': 'rank_tier',
                            'no_prior_reading': True}
    researched = planted['streaming_trending']['netflix']['items'][0]
    researched['us_streams'] = {'us_estimate': 2_004_313,
                                'est_basis': 'researched'}
    removed = t._strip_rank_tier_from_service_rails(planted)
    check(removed == [('streaming_trending.paramountplus.items',
                       victim['title'])],
          f'planted leak removed and reported: {removed}')
    check('us_streams' not in victim, 'leaked block is gone from the row')
    check(researched.get('us_streams', {}).get('us_estimate') == 2_004_313,
          'researched row untouched by the invariant')
    n_cross = sum(1 for p, r in _walk_rows(planted)
                  if not t._coverage_is_service_rail(p)
                  and (r.get('us_streams') or {}).get('est_basis') == 'rank_tier')
    check(n_cross == cross_rows,
          f'cross-platform tier rows untouched by the invariant ({n_cross})')

    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILURE(S)')
        for f in FAILURES:
            print(f'  - {f}')
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
