#!/usr/bin/env python3
"""The seven board violations of 2026-10-01 cannot come back.

What went wrong, with the live keys and values:

  I1  Netflix chart rows rendered their `title:` sibling (a per-item
      reading) while the coherence pass had levelled the `film:` /
      `tv:` key. `title:demon slayer ... castle i` 718,707 vs
      `film:` 359,365; `title:lego one piece` 391,434 vs `tv:` 195,735.
  I3  Prime Video's #7 (The Pendragon Cycle) came from the depth list,
      was never stamped with its position on the page, and rendered as
      catalog above the chart floor.
  I4  Two MovieSphere+ rows clamped under one cap landed 0.18% apart
      (18,657 / 18,691 under 19,285).
  I5  Gilmore Girls was moved to #4 on Prime Video by a RANK band while
      reading 22,235, above a #9 at 592,878.

    python3 -m scripts.trends_scrapers.test_board_invariants_2026_10_01
"""
from __future__ import annotations

import sys

from . import chart_entry_sync as ces
from . import stream_estimates as se

_FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f'  ok   {name}')
        return
    _FAILURES.append(name)
    print(f'  FAIL {name}\n         got  {got!r}\n         want {want!r}')


def entry(agg: int, pr=None, **blocks) -> dict:
    it = {'us_estimate': agg,
          'by_platform': {s: dict(v) for s, v in blocks.items()}}
    if pr is not None:
        it['published_rank'] = pr
    return it


# ---------------------------------------------------------------------------
# I1: one sibling is THE sibling, for the page and for the passes
# ---------------------------------------------------------------------------

def test_preferred_sibling() -> None:
    print('the render and the passes resolve the same sibling')
    researched = {
        'title:demon slayer kimetsu no yaiba infinity castle i':
            entry(2, netflix={'us_estimate': 718707}),
        'film:demon slayer kimetsu no yaiba infinity castle i':
            entry(359365, pr=3, netflix={'us_estimate': 359365}),
    }
    norm = 'demon slayer kimetsu no yaiba infinity castle i'
    # A Netflix films-panel row carries no category, so the render's
    # own order starts at title:. The chart-stamped entry still wins.
    render_order = [f'title:{norm}', f'film:{norm}', f'tv:{norm}']
    check('render picks the chart-stamped film: key',
          ces.preferred(researched, render_order, 'netflix'), f'film:{norm}')
    check('the pass, asking in chart order, agrees',
          ces.preferred(researched,
                        ces.entry_key_candidates('', 'film', norm),
                        'netflix'), f'film:{norm}')

    # Chart-stamped for ANOTHER service but with no block for this
    # one: the sibling that actually reads for this service wins.
    researched = {
        'tv:gilmore girls': entry(300000, pr=15, hulu={'us_estimate': 337000}),
        'title:gilmore girls': entry(90000, netflix={'us_estimate': 90000}),
    }
    check('a chart stamp elsewhere does not pick a blank sibling',
          ces.preferred(researched, ['title:gilmore girls', 'film:gilmore girls',
                                     'tv:gilmore girls'], 'netflix'),
          'title:gilmore girls')
    check('on the service it is charted on, it is the one',
          ces.preferred(researched, ['title:gilmore girls', 'film:gilmore girls',
                                     'tv:gilmore girls'], 'hulu'),
          'tv:gilmore girls')
    check('no entry at all is None',
          ces.preferred(researched, ['film:nothing'], 'hulu'), None)


def test_mirror_across() -> None:
    print('a title stored under two keys carries one number per service')
    researched = {
        'tv:lego one piece': entry(195735, pr=7, netflix={'us_estimate': 195735,
                                                        'est_basis': 'chart'}),
        'title:lego one piece': entry(2, netflix={'us_estimate': 391434,
                                                 'est_basis': 'researched'},
                                      hulu={'us_estimate': 50000}),
        'tv:outside': entry(99775, pr=9, netflix={'us_estimate': 99775}),
        'title:outside': entry(2, netflix={'us_estimate': 199629}),
        'tv:doc': entry(78889, pr=10, netflix={'us_estimate': 78889}),
    }
    moved = ces.mirror_across(se, researched, 'netflix', 'test|mirror')
    check('two titles moved', moved, 2)
    check('lego one piece title: follows the chart key',
          researched['title:lego one piece']['by_platform']['netflix']['us_estimate'],
          195735)
    check('its basis is kept',
          researched['title:lego one piece']['by_platform']['netflix']['est_basis'],
          'researched')
    check('its other service is untouched',
          researched['title:lego one piece']['by_platform']['hulu']['us_estimate'],
          50000)
    check('outside title: follows the chart key',
          researched['title:outside']['by_platform']['netflix']['us_estimate'],
          99775)
    check('a title with one key is left alone',
          researched['tv:doc']['by_platform']['netflix']['us_estimate'], 78889)
    check('second run is a no-op',
          ces.mirror_across(se, researched, 'netflix', 'test|mirror'), 0)


# ---------------------------------------------------------------------------
# I4: seats under a cap are spaced
# ---------------------------------------------------------------------------

def test_cap_seats_spaced() -> None:
    print('rows brought under a cap never share a seat')
    cap = 19285
    original = se._platform_daily_cap_for
    se._platform_daily_cap_for = lambda slug: cap if slug in (
        'moviesphereplus', 'other_svc') else original(slug)
    try:
        researched = {}
        for i in range(8):
            researched[f'film:title {i}'] = entry(
                30000 + i * 13, moviesphereplus={'us_estimate': 30000 + i * 13})
        # One title under two keys counts once.
        researched['tv:title 0'] = entry(
            30000, moviesphereplus={'us_estimate': 30000})
        # A row already inside the top 5% of the cap, not over it.
        researched['film:near'] = entry(
            19000, moviesphereplus={'us_estimate': 19000})
        # Another service on the same cap whose top would collide.
        researched['film:elsewhere'] = entry(
            40000, other_svc={'us_estimate': 40000})
        n = se._reclamp_carried_to_platform_ceiling(researched)
        vals = sorted(
            {k: v['by_platform']['moviesphereplus']['us_estimate']
             for k, v in researched.items()
             if 'moviesphereplus' in v['by_platform']}.values(),
            reverse=True)
        check('every reading is under the cap', all(v < cap for v in vals), True)
        gaps = [(vals[i] - vals[i + 1]) / vals[i] for i in range(len(vals) - 1)
                if vals[i] != vals[i + 1]]
        distinct = sorted(set(vals), reverse=True)
        gaps = [(distinct[i] - distinct[i + 1]) / distinct[i]
                for i in range(len(distinct) - 1)]
        check('no two seats within 0.5% of each other',
              all(g > 0.005 for g in gaps), True)
        check('the two keys of one title read the same',
              researched['film:title 0']['by_platform']['moviesphereplus']['us_estimate'],
              researched['tv:title 0']['by_platform']['moviesphereplus']['us_estimate'])
        # Natural last digits (2026-09-09 amendment): a zero may
        # appear at its natural rate, placeholder shapes never.
        check('no placeholder-shaped value',
              all(v % 1000 != 0 for v in vals), True)
        top_other = researched['film:elsewhere']['by_platform']['other_svc']['us_estimate']
        check('a second service on the same cap sits clear of the first',
              abs(top_other - vals[0]) / max(top_other, vals[0]) > 0.005, True)
        check('something was written', n > 0, True)
        again = se._reclamp_carried_to_platform_ceiling(researched)
        check('a second pass is a no-op', again, 0)
    finally:
        se._platform_daily_cap_for = original


# ---------------------------------------------------------------------------
# I5: a rank band is a value, not a position
# ---------------------------------------------------------------------------

def test_rank_band_is_a_noop_on_positions() -> None:
    print('the render no longer moves a row by rank to satisfy a band')
    rows = [{'title': 'Neagley', 'rank': 1, 'published_rank': 1},
            {'title': 'The Pendragon Cycle', 'rank': 2},
            {'title': 'Gilmore Girls', 'rank': 150}]
    changed = se._clamp_rank_to_band(rows, 'primevideo')
    check('nothing changed', changed, False)
    check('Gilmore Girls keeps the rank its value gave it',
          rows[2]['rank'], 150)


def test_band_levels_the_reading() -> None:
    print('the band is applied to the reading, below the chart floor')
    orig_read = se._read_snapshot
    orig_rail = se._published_rail_rows
    orig_index = se.published_chart_index
    try:
        se._read_snapshot = lambda name, *a, **k: {'national': []}
        chart = [('Neagley', 'tv'), ('Reacher', 'tv'), ('Judy Justice', 'tv')]
        tail = [('Fallout', 'tv'), ('Invincible', 'tv'), ('Slow Horses', 'tv'),
                ('Dark Matter', 'tv'), ('Gilmore Girls', 'tv')]
        se._published_rail_rows = lambda slug, snap, depth: chart + tail
        idx = {'tv:neagley': (1, 'c', 'g'), 'neagley': (1, 'c', 'g'),
               'tv:reacher': (3, 'c', 'g'), 'reacher': (3, 'c', 'g'),
               'tv:judy justice': (10, 'c', 'g'),
               'judy justice': (10, 'c', 'g')}
        se.published_chart_index = lambda slug, snap: idx if slug == 'primevideo' else {}
        researched = {
            'tv:neagley': entry(1847231, primevideo={'us_estimate': 1847231}),
            'tv:reacher': entry(1289073, primevideo={'us_estimate': 1289073}),
            'tv:judy justice': entry(214689, primevideo={'us_estimate': 214689}),
            'tv:fallout': entry(203850, primevideo={'us_estimate': 203850}),
            'tv:invincible': entry(187689, primevideo={'us_estimate': 187689}),
            'tv:slow horses': entry(178419, primevideo={'us_estimate': 178419}),
            'tv:dark matter': entry(156766, primevideo={'us_estimate': 156766}),
            'tv:gilmore girls': entry(22235, primevideo={'us_estimate': 22235}),
            'title:gilmore girls': entry(2, primevideo={'us_estimate': 22235}),
        }
        saved = dict(se._RANK_PLAUSIBILITY_BANDS)
        se._RANK_PLAUSIBILITY_BANDS.clear()
        se._RANK_PLAUSIBILITY_BANDS['gilmore girls'] = {'primevideo': (4, 6)}
        try:
            moved = se._level_banded_titles(researched, '2026-10-01')
        finally:
            se._RANK_PLAUSIBILITY_BANDS.clear()
            se._RANK_PLAUSIBILITY_BANDS.update(saved)
        v = researched['tv:gilmore girls']['by_platform']['primevideo']['us_estimate']
        check('one title moved', moved, 1)
        # Band 4-6 on a rail with 3 chart rows = tail positions 1-3:
        # below the chart floor (214,689), above the row at tail #3
        # (178,419).
        check('reading sits under the chart floor', v < 214689, True)
        check('and above the row at the bottom of the band', v > 178419, True)
        check('the sibling key reads the same',
              researched['title:gilmore girls']['by_platform']['primevideo']['us_estimate'],
              v)
        check('no trailing zero', v % 10 != 0, True)
    finally:
        se._read_snapshot = orig_read
        se._published_rail_rows = orig_rail
        se.published_chart_index = orig_index


def main() -> int:
    test_preferred_sibling()
    test_mirror_across()
    test_cap_seats_spaced()
    test_rank_band_is_a_noop_on_positions()
    test_band_levels_the_reading()
    if _FAILURES:
        print(f'\n{len(_FAILURES)} check(s) failed: {_FAILURES}')
        return 1
    print('\nall 2026-10-01 board invariant checks pass')
    return 0


if __name__ == '__main__':
    sys.exit(main())
