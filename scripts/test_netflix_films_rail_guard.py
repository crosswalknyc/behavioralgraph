#!/usr/bin/env python3
"""Regression test for the Netflix carry-forward guard.

On 2026-07-20 and 07-21 the authenticated daily capture parsed its TV
rail but not its films rail, and shipped both days with ten rows instead
of twenty. Both rails render off the same page, so one of them coming
back empty is a render or parse miss, not Netflix publishing an empty
chart. The guard keeps the previous capture's rows for the missing rail.

Runs offline: the page render, the row parser and S3 are all stubbed.

    python3 scripts/test_netflix_films_rail_guard.py
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.trends_scrapers import netflix as nf  # noqa: E402

_REAL_LOADER = nf._load_previous_daily
_REAL_PARSER = nf._extract_top10_rows
_REAL_RENDER = nf._run_netflix_playwright

PREV = {
    'source_path': 'authenticated_daily',
    'us_films': [{'rank': i, 'title': f'F{i}', 'category_display': 'Film',
                  'url': f'https://www.netflix.com/title/{i}',
                  'source': 'authenticated_daily'} for i in range(1, 11)],
    'us_tv': [{'rank': i, 'title': f'T{i}', 'category_display': 'TV',
               'url': f'https://www.netflix.com/title/{100 + i}',
               'source': 'authenticated_daily'} for i in range(1, 11)],
}
TV = [{'rank': i, 'title': f'newT{i}', 'category_display': 'TV',
       'url': f'https://www.netflix.com/title/{200 + i}',
       'source': 'authenticated_daily'} for i in range(1, 11)]
FILMS = [{'rank': i, 'title': f'newF{i}', 'category_display': 'Film',
          'url': f'https://www.netflix.com/title/{300 + i}',
          'source': 'authenticated_daily'} for i in range(1, 11)]

failures: list[str] = []


def check(name: str, got, want) -> None:
    good = got == want
    if not good:
        failures.append(name)
    print(f'  [{"PASS" if good else "FAIL"}] {name}')
    if not good:
        print(f'         got      {got}')
        print(f'         expected {want}')


def shape(parsed_tv, parsed_films, prev):
    """Run the daily path with everything external stubbed out."""
    nf._run_netflix_playwright = lambda: '<html>Top 10</html>'
    nf._extract_top10_rows = lambda html: (list(parsed_tv), list(parsed_films))
    nf._load_previous_daily = lambda: prev
    try:
        out = nf._fetch_authenticated_daily()
    finally:
        nf._run_netflix_playwright = _REAL_RENDER
        nf._extract_top10_rows = _REAL_PARSER
        nf._load_previous_daily = _REAL_LOADER
    if out is None:
        return None
    stale = sorted({r['category_display']
                    for r in out['us_films'] + out['us_tv']
                    if r.get('stale_from_previous')})
    return (len(out['us_films']), len(out['us_tv']),
            len(out['national']), stale, out)


def fake_boto3(payload, boom=False):
    m = types.ModuleType('boto3')

    class _Client:
        def get_object(self, **kw):
            if boom:
                raise RuntimeError('s3 unreachable')
            return {'Body': types.SimpleNamespace(
                read=lambda: json.dumps(payload).encode())}

    m.client = lambda *a, **k: _Client()
    return m


def load_with(payload, boom=False):
    sys.modules['boto3'] = fake_boto3(payload, boom)
    try:
        return _REAL_LOADER()
    finally:
        sys.modules.pop('boto3', None)


def main() -> int:
    print('carry-forward guard')
    r = shape(TV, FILMS, PREV)
    check('both rails parse: nothing carried, nothing marked stale',
          r[:4], (10, 10, 20, []))

    r = shape(TV, [], PREV)
    check('films rail misses: previous films carried, TV untouched',
          r[:4], (10, 10, 20, ['Film']))
    check('carried row keeps every original field',
          {k: v for k, v in r[4]['us_films'][0].items()
           if k != 'stale_from_previous'},
          PREV['us_films'][0])
    check('national interleaves Film/TV across twenty rows',
          [x['category_display'] for x in r[4]['national']],
          ['Film', 'TV'] * 10)
    check('the fresh TV rail is not marked stale',
          any(x.get('stale_from_previous') for x in r[4]['us_tv']), False)

    r = shape([], FILMS, PREV)
    check('TV rail misses: previous TV carried, films untouched',
          r[:4], (10, 10, 20, ['TV']))

    r = shape(TV, [], {})
    check('films miss with no usable previous: ships without it, no crash',
          r[:4], (0, 10, 10, []))

    check('both rails miss: falls through to the weekly path',
          shape([], [], PREV), None)

    print('previous-snapshot reader')
    check('weekly-path previous is refused so row shapes never mix',
          load_with({'source_path': 'weekly_tsv',
                     'us_films': PREV['us_films']}), {})
    check('daily-path previous is accepted',
          load_with(PREV).get('source_path'), 'authenticated_daily')
    check('S3 failure degrades to empty rather than raising',
          load_with(None, boom=True), {})
    check('non-dict payload degrades to empty', load_with([1, 2, 3]), {})

    print()
    if failures:
        print(f'FAILED: {failures}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
