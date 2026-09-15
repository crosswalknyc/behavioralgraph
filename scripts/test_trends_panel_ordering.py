#!/usr/bin/env python3
"""Three properties the Trends IQ board has to keep.

1. A list that shows an audience figure reads descending by it, with
   dense ranks from 1, no rows dropped and no duplicates. Ordering is
   derived from the rendered value, so a later pass that changes a
   level re-orders the list rather than leaving it stale.
2. No row renders above its own service's published daily cap, and a
   corrected row carries a movement chip against a real prior reading
   rather than one the size of the correction.
3. The last-resort baseline is scaled to the rail a row lands on, so
   a thin panel is not handed the leading panel's number.

Hermetic: no network, no S3, no model calls.

    python3 scripts/test_trends_panel_ordering.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import trends_iq as tq  # noqa: E402

FAILURES: list[str] = []


def check(cond, label):
    if cond:
        print(f'  ok   {label}')
    else:
        print(f'  FAIL {label}')
        FAILURES.append(label)


def _row(title, value, rank):
    return {'title': title, 'rank': rank,
            'us_streams': {'us_estimate': value}}


def _values(rows):
    return [r['us_streams']['us_estimate'] for r in rows]


# ---------------------------------------------------------------- 1
def test_ordering():
    print('ordering derived from the rendered value')

    cards = {
        'podcasts_trending': {
            'amazon': {'items': [_row('A', 100, 1), _row('B', 900, 2),
                                  _row('C', 400, 3)]},
        },
        'books_trending': {
            'wattpad_hot': {'items': [_row('X', 10, 1), _row('Y', 50, 2)]},
        },
        'comics_trending': {
            'apple_comics': {'items': [_row('P', 7, 1), _row('Q', 9, 2),
                                        _row('R', 8, 3)]},
        },
        'gaming_trending': {
            'steam': {'most_played': [_row('G1', 5, 1), _row('G2', 80, 2)],
                       'top_sellers': [_row('S1', 3, 1), _row('S2', 60, 2)]},
            'meta_quest': {'free': [_row('F1', 2, 1), _row('F2', 40, 2)],
                            'paid': [_row('P1', 1, 1), _row('P2', 30, 2)]},
        },
        'libby_trending': {
            'ebook': {'items': [_row('L1', 11, 1), _row('L2', 22, 2)]},
        },
        'music_trending': {
            'apple': {'items': [_row('M1', 12, 1), _row('M2', 99, 2)]},
        },
        'broadway_trending': {
            'broadway_weekly_attendance': {
                'items': [_row('B1', 3, 1), _row('B2', 30, 2)]},
        },
        # No audience figure anywhere, so the chart's own order stands.
        'films_ticketing': {
            'amc': {'items': [{'title': 'F1', 'rank': 1},
                               {'title': 'F2', 'rank': 2}]},
        },
    }
    tq._realign_ranks_to_rendered_values(cards)

    def lists_of(root):
        out = []
        for block in (cards[root] or {}).values():
            for v in block.values():
                if isinstance(v, list):
                    out.append(v)
        return out

    for root in ('podcasts_trending', 'books_trending', 'comics_trending',
                 'gaming_trending', 'libby_trending', 'music_trending',
                 'broadway_trending'):
        for rows in lists_of(root):
            vals = _values(rows)
            check(vals == sorted(vals, reverse=True),
                  f'{root} reads descending {vals}')
            ranks = [r['rank'] for r in rows]
            check(ranks == list(range(1, len(rows) + 1)),
                  f'{root} ranks dense from 1 {ranks}')

    check(len(cards['podcasts_trending']['amazon']['items']) == 3,
          'no row dropped')
    check([r['title'] for r in cards['films_ticketing']['amc']['items']]
          == ['F1', 'F2'], 'a list with no audience figure keeps its order')

    # Idempotent, and it re-orders after a later pass moves a level.
    rows = cards['podcasts_trending']['amazon']['items']
    tq._realign_ranks_to_rendered_values(cards)
    check(_values(rows) == [900, 400, 100], 'second run is a no-op')
    rows[2]['us_streams']['us_estimate'] = 5000
    tq._realign_ranks_to_rendered_values(cards)
    check(_values(cards['podcasts_trending']['amazon']['items'])
          == [5000, 900, 400], 'a level change re-orders the list')


# ---------------------------------------------------------------- 2
def test_platform_caps():
    print('no row above its own service cap')

    cap = tq._platform_daily_cap('tv', 'britbox')
    check(cap is not None and cap == 1_500_000 // 7,
          f'BritBox daily cap derives from its weekly one ({cap})')
    check(tq._platform_daily_cap('film', 'mgmplus') == 2_000_000 // 7,
          'MGM+ daily cap derives from its weekly one')
    check(tq._platform_daily_cap('tv', 'not_a_service') is None,
          'a service with no published cap is left alone')

    cards = {
        'streaming_trending': {
            'britbox': {'items': [
                # Far above its cap, with a sane reading of its own
                # yesterday: the chip has to read against that.
                {'title': 'Shameless', 'rank': 1, 'category_display': 'TV',
                 'us_streams': {'us_estimate': 1_822_805,
                                 'prev_estimate': 83_156,
                                 'prev_date': '2026-09-14',
                                 'method': 'old reasoning',
                                 'sources': ['http://example.test']}},
                # Inside its cap: untouched.
                {'title': 'Shetland', 'rank': 2, 'category_display': 'TV',
                 'us_streams': {'us_estimate': 120_000,
                                 'method': 'keep me',
                                 'sources': ['http://example.test']}},
            ]},
        },
    }
    stats = tq._enforce_platform_caps(cards)
    rows = cards['streaming_trending']['britbox']['items']
    fixed, kept = rows[0]['us_streams'], rows[1]['us_streams']

    check(stats['corrected'] == 1, 'exactly the breaching row is corrected')
    check(fixed['us_estimate'] <= cap,
          f"corrected row sits inside the cap ({fixed['us_estimate']})")
    check(kept['us_estimate'] == 120_000, 'a row inside its cap does not move')
    check(kept.get('method') == 'keep me', 'its reasoning survives')
    check(fixed['us_estimate'] != 83_156,
          'the corrected row is not a repeat of yesterday')
    check(abs(fixed.get('delta_pct') or 0) < 1.0,
          f"the chip is a real move, not the correction "
          f"({fixed.get('delta_pct')})")
    check(fixed.get('sources') is None,
          'reasoning that described the old number does not survive')
    check(fixed.get('est_basis') == 'platform_cap',
          'the corrected row is countable')
    check(str(fixed['us_estimate'])[-1] != '0'
          or fixed['us_estimate'] % 1000 != 0,
          'no placeholder-scale round value')

    # Nothing readable on the rail: seated under the cap, not on it,
    # and two titles do not share a number.
    cards2 = {'streaming_trending': {'mgmplus': {'items': [
        {'title': 'Alpha One', 'rank': 1, 'category_display': 'Film',
         'us_streams': {'us_estimate': 9_000_000}},
        {'title': 'Beta Two', 'rank': 2, 'category_display': 'Film',
         'us_streams': {'us_estimate': 8_000_000}},
    ]}}}
    tq._enforce_platform_caps(cards2)
    v = _values(cards2['streaming_trending']['mgmplus']['items'])
    mcap = tq._platform_daily_cap('film', 'mgmplus')
    check(all(0 < x < mcap for x in v), f'both seated under the cap {v}')
    check(v[0] != v[1], 'two corrected rows on one rail differ')

    # Idempotent.
    tq._enforce_platform_caps(cards2)
    check(_values(cards2['streaming_trending']['mgmplus']['items']) == v,
          'second run leaves the corrected rows alone')

    # A wider window sums the days the item appeared, so the cap has
    # to cover the same days.
    check(tq._cap_days_covered({'us_estimate': 1}) == 1,
          'a daily row covers one day')
    check(tq._cap_days_covered({'window_days_covered': 7}) == 7,
          'a windowed row says how many days it covers')
    check(tq._cap_days_covered({'window_days_total': 30}) == 30,
          'and falls back to the window length')
    weekly = {'streaming_trending': {'britbox': {'items': [
        {'title': 'Shetland', 'rank': 1, 'category_display': 'TV',
         'us_streams': {'us_estimate': 900_000, 'window_days_covered': 7,
                        'method': 'keep me'}},
        {'title': 'Outlier', 'rank': 2, 'category_display': 'TV',
         'us_streams': {'us_estimate': 9_000_000,
                        'window_days_covered': 7}},
    ]}}}
    st = tq._enforce_platform_caps(weekly)
    rows2 = weekly['streaming_trending']['britbox']['items']
    check(st['corrected'] == 1,
          'a seven-day sum inside seven days of cap is left alone')
    check(rows2[0]['us_streams']['us_estimate'] == 900_000,
          'and keeps its exact value')
    check(rows2[1]['us_streams']['us_estimate'] <= cap * 7,
          'while a genuine breach still comes inside')


# ---------------------------------------------------------------- 3
def test_platform_baseline():
    print('last-resort baseline scaled to the rail')

    check(tq._coverage_platform_for_path('podcasts_trending.amazon.items')
          == 'amazon', 'podcast rail resolves its service')
    check(tq._coverage_platform_for_path('comics_trending.apple_comics.items')
          == 'apple_comics', 'comics rail resolves its service')
    check(tq._coverage_platform_for_path('books_trending.wattpad_hot.items')
          == 'wattpad', 'every Wattpad rail rolls up to one tier')
    check(tq._coverage_platform_for_path('libby_trending.magazine.items')
          == 'libby_magazine', 'library magazines resolve their own tier')
    check(tq._coverage_platform_for_path('gaming_trending.steam.most_played')
          == 'steam_most_played', 'a split gaming panel resolves per list')
    check(tq._coverage_platform_for_path('gaming_trending.meta_quest.paid')
          == 'meta_quest_paid', 'and per bucket')
    check(tq._coverage_platform_for_path('streaming_trending.hulu.items')
          == 'hulu', 'streaming keeps resolving as it did')
    check(tq._coverage_platform_for_path('broadway_trending.x.items') == '',
          'a cross-platform rail resolves to nothing')

    # One big rail and one thin rail of the same kind.
    snap = {'items': {}}
    for i in range(40):
        snap['items'][f'podcast:big {i}'] = {
            'us_estimate': 500_000 + i,
            'by_platform': {'apple': {'us_estimate': 500_000 + i}}}
    for i in range(40):
        snap['items'][f'podcast:small {i}'] = {
            'us_estimate': 4_000 + i,
            'by_platform': {'audible': {'us_estimate': 4_000 + i}}}

    dist = tq._coverage_baselines_from_estimates(snap)
    check(set(dist) == {'by_kind', 'by_platform'},
          'the snapshot yields both pools')
    check(len(dist['by_platform'][('podcast', 'audible')]) == 40,
          'the thin rail has a pool of its own')

    pooled = tq._coverage_pick_from_dist(dist, 'podcast', 1, 40, '')
    thin = tq._coverage_pick_from_dist(dist, 'podcast', 1, 40, 'audible')
    big = tq._coverage_pick_from_dist(dist, 'podcast', 1, 40, 'apple')
    check(thin < pooled, f'the thin rail comes down ({thin} vs {pooled})')
    check(big > pooled, f'the leading rail comes up ({big} vs {pooled})')
    check(thin < 10_000, f'and lands on its own scale ({thin})')

    # A rail with too few priced rows of its own keeps the kind pool.
    snap['items']['podcast:lonely'] = {
        'us_estimate': 9, 'by_platform': {'netflix': {'us_estimate': 9}}}
    dist2 = tq._coverage_baselines_from_estimates(snap)
    check(tq._coverage_pick_from_dist(dist2, 'podcast', 1, 40, 'netflix')
          == tq._coverage_pick_from_dist(dist2, 'podcast', 1, 40, ''),
          'a rail too thin to have a shape falls back to the kind')

    # A brand-new kind still gets a number.
    check(tq._coverage_pick_from_dist(dist, 'nothing_yet', 1, 10, 'x') > 0,
          'an unpriced kind still gets a baseline')


def main():
    test_ordering()
    test_platform_caps()
    test_platform_baseline()
    print()
    if FAILURES:
        print(f'{len(FAILURES)} failure(s):')
        for f in FAILURES:
            print(f'  - {f}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
