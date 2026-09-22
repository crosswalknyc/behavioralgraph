#!/usr/bin/env python3
"""The coverage gate re-prices a row that is rendering a cap
correction, and it moves nothing else.

1. A row stamped by the cap pass reads as needing pricing, not as
   researched, so the gate stops walking past it.
2. It is collected against the stored entry the rail actually reads,
   carrying the service key that needs a reading of its own. A rail
   that is one distribution path through another service resolves to
   the parent, which is the reading that has to move.
3. A title that is capped on one service and unpriced on another goes
   to the full re-price once, not to both passes.
4. The merge writes ONLY the named service block. The entry's total,
   its blocks for other services and its reasoning survive, so the
   title's rows on services that were reading correctly do not move.

Hermetic: no network, no S3, no model calls.

    python3 scripts/test_coverage_gate_platform_cap.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from scripts.trends_scrapers import coverage_gate as cg  # noqa: E402
from scripts.trends_scrapers import stream_estimates as se  # noqa: E402
from scripts.trends_scrapers import _base  # noqa: E402

FAILURES: list[str] = []


def check(cond, label):
    if cond:
        print(f'  ok   {label}')
    else:
        print(f'  FAIL {label}')
        FAILURES.append(label)


def _row(title, value, rank, cat, basis=None):
    blk = {'us_estimate': value}
    if basis:
        blk['est_basis'] = basis
    return {'title': title, 'rank': rank,
            'category_display': cat, 'us_streams': blk}


# The snapshot the gate reads while collecting and merging.
def _snapshot():
    return {
        'target_date': '2026-09-21',
        'items': {
            'film:southpaw': {
                'kind': 'film',
                'display_title': 'Southpaw',
                'us_estimate': 965_883,
                'method': 'the reasoning that priced the total',
                'sources': ['https://example.test/southpaw'],
                'by_platform': {
                    'starz': {'us_estimate': 471_233},
                    'primevideo': {'us_estimate': 288_117},
                },
            },
            'tv:sweetbitter': {
                'kind': 'tv',
                'display_title': 'Sweetbitter',
                'us_estimate': 206_755,
                'by_platform': {'starz': {'us_estimate': 192_561}},
            },
            # A title the board renders on a TV rail whose stored
            # entry is a film: key. BritBox does this today.
            'film:passenger': {
                'kind': 'film',
                'display_title': 'Passenger',
                'us_estimate': 246_387,
                'by_platform': {},
            },
        },
    }


# ---------------------------------------------------------------- 1
def test_state():
    print('a cap correction reads as a row that still needs pricing')
    capped = {'us_streams': {'us_estimate': 21_148,
                              'est_basis': 'platform_cap'}}
    check(cg._audience_state(capped) == 'platform_cap',
          'the cap basis has its own state')
    check(cg._audience_state(
        {'us_streams': {'us_estimate': 21_148}}) == 'researched',
        'a row with a real reading is still researched')
    check(cg._audience_state(
        {'us_streams': {'us_estimate': 21_148,
                        'est_basis': 'carried_forward'}}) == 'carried',
        'the other bases are unchanged')


# ---------------------------------------------------------------- 2
def test_platform_key():
    print('the service key a capped rail reads')
    check(cg._cap_platform_key(
        'streaming_trending.lionsgateplus.films') == 'lionsgateplus',
        'a streaming rail resolves to its own service')
    check(cg._cap_platform_key(
        'streaming_trending.starz_amazon.films') == 'starz',
        'a distribution path resolves to the service it is part of')
    check(cg._cap_platform_key('fast_trending.pluto.channels') == 'pluto',
          'a FAST rail resolves to its platform')
    check(cg._cap_platform_key('music_trending.spotify.items') == '',
          'a rail outside the cap pass has nothing service-scoped')


# ---------------------------------------------------------------- 3
def test_collect(monkey):
    print('collection: one entry per title, every capped service on it')
    cards = {'streaming_trending': {
        'lionsgateplus': {'films': [
            _row('Southpaw', 18_415, 10, 'Film', 'platform_cap')]},
        'starz': {
            'films': [_row('Southpaw', 214_687, 9, 'Film', 'platform_cap')],
            'tv': [_row('Sweetbitter', 192_561, 16, 'TV')],
        },
        'starz_amazon': {'films': [
            _row('Southpaw', 98_041, 9, 'Film', 'platform_cap')]},
        'britbox': {'tv': [
            _row('Passenger', 197_052, 3, 'TV', 'platform_cap')]},
    }}
    (stream_items, _headline, total, researched,
     baseline, cap_targets) = cg.collect_missing({'cards': cards})

    check(total == 5, f'every rendered row counted ({total})')
    check(researched == 1, f'only the uncapped row read as researched '
                            f'({researched})')
    check(baseline == 0, 'a cap correction is not a carried or rank-tier row')
    check(not stream_items,
          'a capped row does not enter the full re-price population')

    by_key = {t['entry_key']: t for t in cap_targets}
    check(set(by_key) == {'film:southpaw', 'film:passenger'},
          f'one target per stored entry ({sorted(by_key)})')
    sp = by_key.get('film:southpaw') or {}
    check(sp.get('platforms') == {'lionsgateplus', 'starz'},
          f'both capped services on one title, the derived rail folded '
          f'into its parent ({sorted(sp.get("platforms") or [])})')
    check(len(sp.get('rows') or []) == 3,
          f'every capped row is on the trail ({len(sp.get("rows") or [])})')
    check(sp.get('best_rank') == 9, 'the best rank across the rails wins')

    # `Passenger` is a TV row whose stored entry is a film: key. The
    # target has to follow the entry the annotator resolves, not the
    # key its own label would build, or the merge would create a
    # sibling that then wins the lookup.
    pa = by_key.get('film:passenger') or {}
    check(pa.get('kind') == 'film',
          f'priced under the kind of the entry the row reads '
          f'({pa.get("kind")})')


# ---------------------------------------------------------------- 4
def test_collect_overlap(monkey):
    print('a title that is also unpriced is re-priced once, in full')
    cards = {'streaming_trending': {
        'lionsgateplus': {'films': [
            _row('Southpaw', 18_415, 10, 'Film', 'platform_cap')]},
        'starz': {'films': [
            _row('Southpaw', 214_687, 9, 'Film', 'rank_tier')]},
    }}
    (stream_items, _h, _t, _r, baseline,
     cap_targets) = cg.collect_missing({'cards': cards})
    check(baseline == 1, 'the rank-tier row is counted as one')
    check(len(stream_items) == 1,
          'the title is in the full re-price population')
    check(not cap_targets,
          'and is dropped from the capped one so it is priced once')


# ---------------------------------------------------------------- 5
def test_merge(monkey):
    print('the merge writes one service block and leaves the rest alone')
    written = {}

    def _write(source, payload, **kw):
        written['source'] = source
        written['payload'] = payload

    monkey(_base, 'write_snapshot', _write)

    targets = [{
        'entry_key': 'film:southpaw',
        'kind': 'film',
        'display_title': 'Southpaw',
        'artist': '',
        'best_rank': 9,
        'platforms': {'lionsgateplus', 'starz'},
        'rows': ['streaming_trending.lionsgateplus.films'],
    }]
    results = {'film:southpaw': {'us_estimate': 412_337, 'by_platform': {
        'lionsgateplus': {'us_estimate': 9_431},
        'starz':         {'us_estimate': 203_119},
        'primevideo':    {'us_estimate': 777_777},
    }}}
    stats = cg._merge_cap_platform_blocks(results, targets, '2026-09-21')

    check(stats['blocks'] == 2, f'both capped services written '
                                 f'({stats["blocks"]})')
    entry = (written.get('payload') or {}).get('items', {}).get('film:southpaw')
    check(isinstance(entry, dict), 'the entry was written back')
    bp = (entry or {}).get('by_platform') or {}
    check(bp.get('lionsgateplus', {}).get('us_estimate') == 9_431,
          'the service that was reading the total now has its own')
    check(bp.get('starz', {}).get('us_estimate') == 203_119,
          'the second capped service moved too')
    check(bp.get('primevideo', {}).get('us_estimate') == 288_117,
          'a service that was reading correctly did NOT move')
    check(entry.get('us_estimate') == 965_883,
          "the title's total did not move")
    check(entry.get('sources') == ['https://example.test/southpaw'],
          'the reasoning on the entry survived')
    other = (written.get('payload') or {}).get('items', {}).get('tv:sweetbitter')
    check((other or {}).get('us_estimate') == 206_755,
          'no other entry was touched')

    trail = stats['trail']
    check(len(trail) == 2 and all(t['stored_now'] for t in trail),
          'the trail names every reading written')
    check(sorted(t['platform'] for t in trail) == ['lionsgateplus', 'starz'],
          'and only the capped services')


# ---------------------------------------------------------------- 6
def test_merge_holds(monkey):
    print('a service the research could not read keeps its correction')
    calls = []
    monkey(_base, 'write_snapshot',
           lambda source, payload, **kw: calls.append(source))

    targets = [{
        'entry_key': 'film:southpaw', 'kind': 'film',
        'display_title': 'Southpaw', 'artist': '', 'best_rank': 9,
        'platforms': {'lionsgateplus'}, 'rows': ['x'],
    }]
    # Sub-100 is the same credibility floor the other merge applies.
    stats = cg._merge_cap_platform_blocks(
        {'film:southpaw': {'by_platform': {
            'lionsgateplus': {'us_estimate': 8}}}}, targets, '2026-09-21')
    check(stats['blocks'] == 0, 'a degenerate reading is not written')
    check(stats['no_block'] == ['film:southpaw@lionsgateplus'],
          'and is named so the run log says which row held')
    check(not calls, 'nothing is written when nothing was usable')

    stats = cg._merge_cap_platform_blocks({}, targets, '2026-09-21')
    check(stats['blocks'] == 0 and not calls,
          'a title the research skipped entirely holds too')
    check(stats['no_result'] == ['film:southpaw'],
          'and a pass where every title held still names them')


def main():
    saved = []

    def monkey(mod, name, value):
        saved.append((mod, name, getattr(mod, name)))
        setattr(mod, name, value)

    monkey(se, '_read_snapshot',
           lambda source: _snapshot() if source == 'stream_estimates'
           else None)
    try:
        test_state()
        test_platform_key()
        test_collect(monkey)
        test_collect_overlap(monkey)
        test_merge(monkey)
        test_merge_holds(monkey)
    finally:
        for mod, name, value in reversed(saved):
            setattr(mod, name, value)

    print()
    if FAILURES:
        print(f'{len(FAILURES)} failure(s):')
        for f in FAILURES:
            print(f'  - {f}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
