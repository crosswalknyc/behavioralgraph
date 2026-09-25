#!/usr/bin/env python3
"""A two-chart service never publishes with one chart empty.

Covers the guard itself and the shape each of the four two-chart
scrapers hands it, including the exact case that shipped:
`latest/max.json` on 2026-09-25 carried five movie rows and no series
rows, and the six-row health floor reported the day healthy.

    python3 -m scripts.trends_scrapers.test_chart_rail_guard
"""
from __future__ import annotations

import sys

from . import _chart_rail_guard as guard

EXPECTED = ('series', 'movies')

_FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f'  ok   {name}')
        return
    _FAILURES.append(name)
    print(f'  FAIL {name}\n         got  {got!r}\n         want {want!r}')


def row(title: str, kind: str, collection: str, rank: int = 1) -> dict:
    return {'rank': rank, 'title': title, 'category_display': kind,
            'collection': collection}


# ---------------------------------------------------------------------------
# chart_kind / kind_key
# ---------------------------------------------------------------------------

def test_kind_reading() -> None:
    print('chart_kind reads a heading or a category label')
    check('Film -> movies', guard.chart_kind('Film'), 'movies')
    check('TV -> series', guard.chart_kind('TV'), 'series')
    check('HBO Max series heading',
          guard.chart_kind('Top 10 Series Today'), 'series')
    check('Disney+ movies heading',
          guard.chart_kind('Top 10 Movies in the US Today'), 'movies')
    check('Peacock TV heading',
          guard.chart_kind('Top 10 TV Shows Today'), 'series')
    check('Pluto series heading',
          guard.chart_kind('Top TV Series'), 'series')
    check('Pluto movies heading',
          guard.chart_kind('Most Popular Movies'), 'movies')
    check('a merchandising heading is neither',
          guard.chart_kind('Because You Watched'), '')
    check('empty is neither', guard.chart_kind(''), '')

    # The category the scraper classified wins over the page's wording,
    # so a reworded heading cannot silently reclassify a row.
    check('category_display wins over collection',
          guard.kind_key(row('Fuze', 'Film', 'Trending Now')), 'movies')
    check('collection carries it when the category is blank',
          guard.kind_key(row('Lanterns', '', 'Top 10 Series Today')),
          'series')


# ---------------------------------------------------------------------------
# missing_rails
# ---------------------------------------------------------------------------

def test_missing_rails() -> None:
    print('missing_rails answers presence, not depth')
    both = [row('Supergirl', 'Film', 'Top 10 Movies Today'),
            row('Lanterns', 'TV', 'Top 10 Series Today')]
    check('both present -> nothing missing',
          guard.missing_rails(both, EXPECTED, key_of=guard.kind_key), [])

    # The shipped defect: five movie rows, no series rows. Every
    # row-count floor in these scrapers is 6 or 12, so a count-based
    # check reports this healthy or short, never one-rail-missing.
    max_0925 = [row(t, 'Film', 'Top 10 Movies Today', i) for i, t in
                enumerate(['Supergirl', 'Fuze', 'The Revenant', 'Siren',
                           'The Last Samurai'], 1)]
    check('the max.json 2026-09-25 shape names the series chart',
          guard.missing_rails(max_0925, EXPECTED, key_of=guard.kind_key),
          ['series'])

    one_deep = [row('Supergirl', 'Film', 'Top 10 Movies Today'),
                row('Lanterns', 'TV', 'Top 10 Series Today')]
    check('a one-row chart is present, not missing',
          guard.missing_rails(one_deep, EXPECTED, key_of=guard.kind_key),
          [])
    check('nothing at all names both',
          guard.missing_rails([], EXPECTED, key_of=guard.kind_key),
          ['series', 'movies'])

    # The default key is the collection field the other scrapers stamp.
    check('collection_key default',
          guard.missing_rails([row('A', 'Film', 'Most Popular Movies')],
                              ('Most Popular Movies', 'Top TV Series')),
          ['Top TV Series'])


# ---------------------------------------------------------------------------
# rerender_recovered
# ---------------------------------------------------------------------------

def test_rerender() -> None:
    print('a second render contributes only the rail the first missed')
    first = [row('Supergirl', 'Film', 'Top 10 Movies Today', 1)]
    retry = [row('Supergirl', 'Film', 'Top 10 Movies Today', 1),
             row('Lanterns', 'TV', 'Top 10 Series Today', 1)]
    out = guard.rerender_recovered(first, retry, EXPECTED,
                                   key_of=guard.kind_key)
    check('the missing rail comes back',
          guard.missing_rails(out, EXPECTED, key_of=guard.kind_key), [])
    check('the rail the first render had whole is not duplicated',
          sum(1 for r in out if r['title'] == 'Supergirl'), 1)

    check('a retry that missed it too changes nothing',
          guard.rerender_recovered(first, [row('Fuze', 'Film', 'x')],
                                   EXPECTED, key_of=guard.kind_key),
          first)
    check('an empty retry changes nothing',
          guard.rerender_recovered(first, [], EXPECTED,
                                   key_of=guard.kind_key),
          first)

    complete = [row('Supergirl', 'Film', 'm'), row('Lanterns', 'TV', 's')]
    check('a complete read is never touched',
          guard.rerender_recovered(complete, retry, EXPECTED,
                                   key_of=guard.kind_key),
          complete)


# ---------------------------------------------------------------------------
# carry_missing
# ---------------------------------------------------------------------------

def test_carry() -> None:
    print('the missing chart carries, and only the missing one')
    today = [row('Supergirl', 'Film', 'Top 10 Movies Today', 1)]
    yesterday = [row('Fuze', 'Film', 'Top 10 Movies Today', 1),
                 row('Lanterns', 'TV', 'Top 10 Series Today', 1),
                 row('Peacemaker', 'TV', 'Top 10 Series Today', 2)]
    out, unresolved = guard.carry_missing(today, yesterday, EXPECTED,
                                          key_of=guard.kind_key)
    check('no chart left absent', unresolved, [])
    check('today\'s movie chart is untouched',
          [r['title'] for r in out if r['category_display'] == 'Film'],
          ['Supergirl'])
    check('the series chart carries both rows',
          [r['title'] for r in out if r['category_display'] == 'TV'],
          ['Lanterns', 'Peacemaker'])
    check('carried rows say so',
          [r.get(guard.STALE_FIELD) for r in out
           if r['category_display'] == 'TV'], [True, True])
    check('today\'s rows are not marked stale',
          out[0].get(guard.STALE_FIELD), None)
    check('the previous capture was not modified',
          any(r.get(guard.STALE_FIELD) for r in yesterday), False)

    out2, unresolved2 = guard.carry_missing(today, [], EXPECTED,
                                            key_of=guard.kind_key)
    check('nothing to carry is reported, not invented',
          unresolved2, ['series'])
    check('and the read publishes without it', out2, today)

    out3, unresolved3 = guard.carry_missing(
        today, [row('Old', 'Film', 'Top 10 Movies Today')], EXPECTED,
        key_of=guard.kind_key)
    check('a previous capture missing that chart too is reported',
          unresolved3, ['series'])
    check('and nothing is borrowed from the other chart',
          [r['title'] for r in out3], ['Supergirl'])

    both = [row('Supergirl', 'Film', 'm'), row('Lanterns', 'TV', 's')]
    check('a complete read carries nothing',
          guard.carry_missing(both, yesterday, EXPECTED,
                              key_of=guard.kind_key),
          (both, []))


# ---------------------------------------------------------------------------
# The four scrapers declare the guard and spell their charts the same way
# ---------------------------------------------------------------------------

def test_scrapers_wired() -> None:
    print('every two-chart scraper is wired to the guard')
    import importlib
    for mod_name in ('max_streaming', 'disneyplus_top10', 'peacock_top10',
                     'pluto_popular'):
        mod = importlib.import_module(
            f'scripts.trends_scrapers.{mod_name}')
        check(f'{mod_name} declares both charts',
              getattr(mod, '_EXPECTED_CHARTS', None), EXPECTED)
        check(f'{mod_name} imports the guard',
              getattr(mod, '_guard', None) is guard, True)

    # Each scraper's own chart names have to resolve to the two kinds
    # the guard is asked about, or the guard would report a chart
    # missing that is sitting right there.
    from scripts.trends_scrapers import peacock_top10 as pk
    check('peacock chart slugs resolve to both kinds',
          sorted({guard.chart_kind(kind)
                  for _name, kind in pk.CHART_SLUGS.values()}),
          ['movies', 'series'])

    from scripts.trends_scrapers import pluto_popular as pl
    check('pluto chart entries resolve to both kinds',
          sorted({guard.chart_kind(c[4]) for c in pl.CHARTS}),
          ['movies', 'series'])

    from scripts.trends_scrapers import disneyplus_top10 as dp
    check('disney+ rail names resolve to both kinds',
          sorted({guard.chart_kind(dp._kind_for(n))
                  for n in (dp._MOVIES, dp._SERIES)}),
          ['movies', 'series'])

    from scripts.trends_scrapers import max_streaming as mx
    check('hbo max chart headings resolve to both kinds',
          sorted({guard.chart_kind(n)
                  for n in (mx._SERIES_CHART, mx._MOVIES_CHART)}),
          ['movies', 'series'])


# ---------------------------------------------------------------------------
# The archive walk-back
# ---------------------------------------------------------------------------

def test_archive_walk_back() -> None:
    print('a rail that misses twice carries from the day that has it')
    import io
    import json as _json
    from . import _base

    # HBO Max on 2026-09-25: today's earlier capture is movies-only
    # too, so "the previous capture" cannot cover the series chart.
    # Yesterday's has Lanterns at #1.
    archive = {
        '2026-09-25': [row('Supergirl', 'Film', 'Top 10 Movies Today')],
        '2026-09-24': [row('Supergirl', 'Film', 'Top 10 Movies Today'),
                       row('Lanterns', 'TV', 'Top 10 Series Today', 1)],
    }

    class FakeS3:
        def __init__(self):
            self.asked: list[str] = []

        def get_object(self, Bucket, Key):
            self.asked.append(Key)
            day = Key.split('/')[1] if '/' in Key else ''
            if day not in archive:
                raise RuntimeError('no such key')
            body = _json.dumps({'national': archive[day]}).encode()
            return {'Body': io.BytesIO(body)}

    fake = FakeS3()
    real = _base._s3_client
    _base._s3_client = lambda: fake
    try:
        today = [row('Supergirl', 'Film', 'Top 10 Movies Today')]
        out, unresolved = guard.carry_missing(
            today, today, EXPECTED, key_of=guard.kind_key,
            label='max', archive_source='max', archive_days=5)
    finally:
        _base._s3_client = real

    check('the series chart came back', unresolved, [])
    check('with yesterday\'s rows',
          [r['title'] for r in out if r['category_display'] == 'TV'],
          ['Lanterns'])
    check('marked stale', out[-1].get(guard.STALE_FIELD), True)
    check('and saying which day it came from',
          out[-1].get(guard.STALE_DAY_FIELD), '2026-09-24')
    check('today\'s movie chart untouched',
          [r['title'] for r in out if r['category_display'] == 'Film'],
          ['Supergirl'])
    check('it stopped at the first day that had it',
          fake.asked[-1].split('/')[1], '2026-09-24')

    # Nothing in the window carries it.
    archive.pop('2026-09-24')
    fake2 = FakeS3()
    _base._s3_client = lambda: fake2
    try:
        out2, unresolved2 = guard.carry_missing(
            [row('Supergirl', 'Film', 'm')], [], EXPECTED,
            key_of=guard.kind_key, label='max', archive_source='max',
            archive_days=3)
    finally:
        _base._s3_client = real
    check('an absent rail is reported, never invented',
          unresolved2, ['series'])
    check('and the read ships without it', len(out2), 1)

    # A scraper that names no archive source keeps the old behaviour.
    out3, unresolved3 = guard.carry_missing(
        [row('Supergirl', 'Film', 'm')], [], EXPECTED,
        key_of=guard.kind_key, label='x')
    check('no archive source means no walk-back', unresolved3, ['series'])
    check('and no rows added', len(out3), 1)


def main() -> int:
    for fn in (test_kind_reading, test_missing_rails, test_rerender,
               test_carry, test_archive_walk_back, test_scrapers_wired):
        fn()
    print()
    if _FAILURES:
        print(f'{len(_FAILURES)} failing: ' + ', '.join(_FAILURES))
        return 1
    print('all chart-rail guard checks pass')
    return 0


if __name__ == '__main__':
    sys.exit(main())
