#!/usr/bin/env python3
"""One title on one service reads the same everywhere it is stored,
and a chart brought under its cap does not land on a shared seat.

Pins the four things the 2026-09-25 11:37 PT run got wrong, using the
exact keys and values that were live on the board.

    python3 -m scripts.trends_scrapers.test_chart_entry_sync
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


def entry(agg: int, **blocks) -> dict:
    return {'us_estimate': agg,
            'by_platform': {s: dict(v) for s, v in blocks.items()}}


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def test_candidates() -> None:
    print('candidates follow the render, with the chart\'s kind first')
    check('a film row', ces.entry_key_candidates('', 'film', 'whisper man'),
          ['film:whisper man', 'tv:whisper man', 'title:whisper man'])
    check('a tv row', ces.entry_key_candidates('', 'tv', 'middle'),
          ['tv:middle', 'film:middle', 'title:middle'])
    check('FAST has no title form',
          ces.entry_key_candidates('fast_', 'film', 'x'),
          ['fast_film:x', 'fast_tv:x'])
    check('a blank title has no keys',
          ces.entry_key_candidates('', 'film', ''), [])


# ---------------------------------------------------------------------------
# The rows that were dropped
# ---------------------------------------------------------------------------

def test_sibling_only_row_is_found() -> None:
    print('a row whose only entry is a sibling is no longer dropped')

    # Disney+ Top 10 Series #2. The bracket created it at film:, the
    # chart pass looked for tv: and dropped it, and it rendered
    # 278,915 between 2,087,459 and 1,342,966.
    researched = {'film:american horror story official podcast':
                  entry(278915, disneyplus={'us_estimate': 278915,
                                            'est_basis': 'bracketed'})}
    cands = ces.entry_key_candidates(
        '', 'tv', 'american horror story official podcast')
    check('no exact-kind entry exists',
          'tv:american horror story official podcast' in researched, False)
    check('but the row resolves',
          ces.primary(researched, cands),
          'film:american horror story official podcast')

    # Peacock Top 10 TV #6, same shape.
    r2 = {'film:el señor de los cielos':
          entry(138889, peacock={'us_estimate': 138889,
                                 'est_basis': 'bracketed'})}
    check('El Senor resolves too',
          ces.primary(r2, ces.entry_key_candidates(
              '', 'tv', 'el señor de los cielos')),
          'film:el señor de los cielos')

    check('a title with no entry anywhere still resolves to nothing',
          ces.primary({}, ces.entry_key_candidates('', 'tv', 'nobody')),
          None)


def test_bracket_relevels_and_keeps_its_basis() -> None:
    print('a bracketed row re-levels with its chart and stays bracketed')
    researched = {'film:american horror story official podcast':
                  entry(278915, disneyplus={'us_estimate': 278915,
                                            'est_basis': 'bracketed'})}
    cands = ces.entry_key_candidates(
        '', 'tv', 'american horror story official podcast')
    out = ces.write_across(se, researched, cands, 'disneyplus',
                           1_700_412, 'salt')
    blk = (researched['film:american horror story official podcast']
           ['by_platform']['disneyplus'])
    check('the reading moved with the chart', blk['us_estimate'], 1_700_412)
    check('the basis survives, so tomorrow still targets it',
          blk.get('est_basis'), 'bracketed')
    check('one entry written', out['set'], 1)
    check('nothing invented', out['created'], 0)


# ---------------------------------------------------------------------------
# The rows that leaked to a sibling
# ---------------------------------------------------------------------------

def test_siblings_agree_after_write() -> None:
    print('every entry a reader could resolve gets the same reading')

    # Netflix films #6. The pass wrote film: and the render, on a row
    # with a blank category, read title: and showed 1,557,442.
    researched = {
        'film:whisper man': entry(2831685,
                                  netflix={'us_estimate': 163313}),
        'title:whisper man': entry(1560855,
                                   netflix={'us_estimate': 1557442}),
    }
    cands = ces.entry_key_candidates('', 'film', 'whisper man')
    out = ces.write_across(se, researched, cands, 'netflix', 163313 + 1,
                           'salt')
    vals = {k: researched[k]['by_platform']['netflix']['us_estimate']
            for k in researched}
    check('both siblings now read the same',
          len(set(vals.values())), 1)
    check('and read the chart value', set(vals.values()), {163314})
    check('both were touched', sorted(out['keys']),
          ['film:whisper man', 'title:whisper man'])
    check('the mirror is reported', out['set'], 2)

    # A sibling carrying another service is left alone.
    r2 = {'film:shark tale': entry(123555, netflix={'us_estimate': 119699}),
          'title:shark tale': entry(637930,
                                    netflix={'us_estimate': 674659},
                                    hulu={'us_estimate': 41123})}
    ces.write_across(se, r2, ces.entry_key_candidates('', 'film',
                                                      'shark tale'),
                     'netflix', 200001, 'salt')
    check('netflix agrees across siblings',
          {r2['film:shark tale']['by_platform']['netflix']['us_estimate'],
           r2['title:shark tale']['by_platform']['netflix']['us_estimate']},
          {200001})
    check('the other service on that entry is untouched',
          r2['title:shark tale']['by_platform']['hulu']['us_estimate'],
          41123)


def test_missing_block_is_created() -> None:
    print('an entry with no block for the service gets one')

    # Peacock Top 10 TV #7. `tv:middle` existed with no Peacock block,
    # so the write silently did nothing and the row shipped carried
    # forward under a chart that had moved.
    researched = {'tv:middle': entry(52110, hulu={'us_estimate': 52110})}
    out = ces.write_across(se, researched,
                           ces.entry_key_candidates('', 'tv', 'middle'),
                           'peacock', 488213, 'salt')
    blk = researched['tv:middle']['by_platform'].get('peacock')
    check('a peacock block now exists', bool(blk), True)
    check('carrying the chart value', (blk or {}).get('us_estimate'),
          488213)
    check('reported as created', out['created'], 1)
    check('the service already there is untouched',
          researched['tv:middle']['by_platform']['hulu']['us_estimate'],
          52110)
    check('no bounds carried from another title\'s scale',
          'us_estimate_low' in (blk or {}), False)

    check('nothing to write to is a clean no-op',
          ces.write_across(se, {}, ['tv:nobody'], 'peacock', 10, 's'),
          {'set': 0, 'created': 0, 'keys': []})


# ---------------------------------------------------------------------------
# The shared seat
# ---------------------------------------------------------------------------

def test_cap_fit() -> None:
    print('an over-cap chart is rescaled whole, not clamped onto a seat')
    cap = 2_142_857
    used: dict = {}
    v = {'A': 3_000_000, 'B': 2_500_000, 'C': 1_000_000}
    moved = se._fit_chart_under_cap(v, 'disneyplus', 'series', cap,
                                    '2026-09-25', used_headrooms=used)
    check('it fired', moved, True)
    check('the top slot is under the cap', max(v.values()) < cap, True)
    check('and inside the headroom band',
          0.72 <= max(v.values()) / cap <= 0.93, True)
    check('the shape is kept',
          round((v['B'] / v['A']) - (2_500_000 / 3_000_000), 4), 0.0)
    check('still strictly descending',
          [v['A'] > v['B'], v['B'] > v['C']], [True, True])

    under = {'A': 100, 'B': 50}
    check('a chart already under the cap is left alone',
          se._fit_chart_under_cap(under, 'x', 'y', cap, '2026-09-25',
                                  used_headrooms={}), False)
    check('and unchanged', under, {'A': 100, 'B': 50})
    check('no cap is a no-op',
          se._fit_chart_under_cap({'A': 9}, 'x', 'y', None, 'd',
                                  used_headrooms={}), False)


def test_no_two_charts_share_a_seat() -> None:
    print('two charts under one cap never land within 1% of each other')

    # The live collisions: Disney+'s two charts on 2,142,857, and
    # Pluto's two plus Starz on 714,285.
    for cap, charts in ((2_142_857, [('disneyplus', 'series'),
                                     ('disneyplus', 'movies')]),
                        (714_285, [('pluto', 'movies'),
                                   ('pluto', 'series'),
                                   ('starz', 'movies')])):
        used: dict = {}
        tops = []
        for slug, group in charts:
            v = {'A': cap * 3, 'B': cap * 2, 'C': cap}
            se._fit_chart_under_cap(v, slug, group, cap, '2026-09-25',
                                    used_headrooms=used)
            tops.append(max(v.values()))
        worst = min(abs(a - b) / max(a, b)
                    for i, a in enumerate(tops)
                    for b in tops[i + 1:]) if len(tops) > 1 else 1.0
        check(f'cap {cap:,}: {len(tops)} charts, closest pair '
              f'{worst * 100:.1f}% apart', worst > 0.01, True)
        check(f'cap {cap:,}: all distinct', len(set(tops)), len(tops))

    # Deterministic: the same day draws the same seats.
    def run():
        used: dict = {}
        out = []
        for slug, group in (('pluto', 'movies'), ('starz', 'movies')):
            v = {'A': 3_000_000}
            se._fit_chart_under_cap(v, slug, group, 714_285, '2026-09-25',
                                    used_headrooms=used)
            out.append(v['A'])
        return out
    check('same inputs, same seats', run(), run())

    # A different day moves them.
    used_a: dict = {}
    va = {'A': 3_000_000}
    se._fit_chart_under_cap(va, 'pluto', 'movies', 714_285, '2026-09-25',
                            used_headrooms=used_a)
    used_b: dict = {}
    vb = {'A': 3_000_000}
    se._fit_chart_under_cap(vb, 'pluto', 'movies', 714_285, '2026-09-26',
                            used_headrooms=used_b)
    check('a new day is a new seat', va['A'] != vb['A'], True)


def main() -> int:
    for fn in (test_candidates, test_sibling_only_row_is_found,
               test_bracket_relevels_and_keeps_its_basis,
               test_siblings_agree_after_write,
               test_missing_block_is_created, test_cap_fit,
               test_no_two_charts_share_a_seat):
        fn()
    print()
    if _FAILURES:
        print(f'{len(_FAILURES)} failing: ' + ', '.join(_FAILURES))
        return 1
    print('all chart entry sync + cap fit checks pass')
    return 0


if __name__ == '__main__':
    sys.exit(main())
