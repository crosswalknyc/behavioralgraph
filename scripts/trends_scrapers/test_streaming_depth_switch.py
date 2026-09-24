#!/usr/bin/env python3
"""Regression: streaming collection depth follows ONE switch, OFF by default.

`stream_estimates._collect_streaming` hands the research step the first
40 rows of each platform rail unless `STREAM_ESTIMATES_FULL_RAIL_DEPTH`
is on, in which case it reads the 200 rows the dashboard renders and
widens the union cap so the deeper union is not truncated back. Pins:

  * default (env unset / '0') is 40 rows per rail, union cap 600
  * '1' is 200 rows per rail, union cap 1,600
  * a rail deeper than 40 contributes exactly 40 titles when OFF and
    every rendered row when ON
  * a title the service ranks on its published chart below the head
    line is still collected when OFF (the chart-mirror exception)
  * the deep rows are lo-tier (page position > 20) and never ask for a
    web_search, so they land on the Haiku batch lane

No network: `_read_snapshot` is stubbed with synthetic rails.

    python3 -m scripts.trends_scrapers.test_streaming_depth_switch
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

logging.disable(logging.CRITICAL)

from scripts.trends_scrapers import stream_estimates as se  # noqa: E402

FAILURES: list = []
ENV = se._FULL_RAIL_DEPTH_ENV


def check(ok: bool, what: str) -> None:
    print(f'{"PASS" if ok else "FAIL"}  {what}')
    if not ok:
        FAILURES.append(what)


def _rail(prefix: str, n: int) -> list:
    return [{'title': f'{prefix} title {i}', 'category_display': 'TV Show',
             'url': f'https://x/{prefix}/{i}'} for i in range(1, n + 1)]


_SNAPS = {
    # A 200-row catalog rail with no published chart.
    'paramountplus': {'national': _rail('pp', 200)},
    # A 129-row rail whose service publishes a Top 10, one member of
    # which sits at page slot 77.
    'lionsgateplus': {'national': _rail('lg', 129)},
    # A 20-row rail: shallower than the head either way.
    'hulu': {'national': _rail('hu', 20)},
}


def _fake_read_snapshot(slug: str, *a, **k):
    return _SNAPS.get(slug)


def _fake_published_chart_index(slug, snap):
    if slug == 'lionsgateplus':
        return {'sentinel': True}
    return None


def _fake_published_rank_for(index, kind, title):
    if index and title == 'lg title 77':
        return (4, 'Lionsgate+ Top 10 in the U.S.', 'top10')
    return None


def _with_env(value):
    if value is None:
        os.environ.pop(ENV, None)
    else:
        os.environ[ENV] = value


def main() -> int:
    saved = (se._read_snapshot, se.published_chart_index,
             se.published_rank_for, se.published_chart_label)
    se._read_snapshot = _fake_read_snapshot
    se.published_chart_index = _fake_published_chart_index
    se.published_rank_for = _fake_published_rank_for
    se.published_chart_label = lambda slug: (
        'Lionsgate+ Top 10 in the U.S.' if slug == 'lionsgateplus' else '')
    try:
        for value in (None, '0', 'off', ''):
            _with_env(value)
            check(not se._streaming_full_depth_enabled(),
                  f'switch reads OFF for {value!r}')
            check(se._streaming_head_cap() == 40
                  and se._streaming_union_cap() == 600,
                  f'OFF depth is 40 / 600 for {value!r}')
        for value in ('1', 'true', 'on', 'YES'):
            _with_env(value)
            check(se._streaming_full_depth_enabled(),
                  f'switch reads ON for {value!r}')
            check(se._streaming_head_cap() == 200
                  and se._streaming_union_cap() == 1_600,
                  f'ON depth is 200 / 1,600 for {value!r}')

        _with_env(None)
        off = se._collect_streaming()
        titles_off = {it['display_title'] for it in off}
        pp_off = sum(1 for x in titles_off if x.startswith('pp '))
        lg_off = sum(1 for x in titles_off if x.startswith('lg '))
        hu_off = sum(1 for x in titles_off if x.startswith('hu '))
        check(pp_off == 40, f'OFF: 200-row rail contributes 40 ({pp_off})')
        check(lg_off == 41, f'OFF: 129-row rail contributes 40 + the charted '
                            f'slot-77 title ({lg_off})')
        check('lg title 77' in titles_off,
              'OFF: published-chart member below the head line is collected')
        check(hu_off == 20, f'OFF: 20-row rail contributes 20 ({hu_off})')
        check(len(off) == 101, f'OFF: union is 101 titles ({len(off)})')

        _with_env('1')
        on = se._collect_streaming()
        titles_on = {it['display_title'] for it in on}
        pp_on = sum(1 for x in titles_on if x.startswith('pp '))
        lg_on = sum(1 for x in titles_on if x.startswith('lg '))
        check(pp_on == 200, f'ON: 200-row rail contributes 200 ({pp_on})')
        check(lg_on == 129, f'ON: 129-row rail contributes 129 ({lg_on})')
        check(len(on) == 349, f'ON: union is 349 titles, under the 1,600 '
                              f'cap ({len(on)})')
        check(titles_off <= titles_on, 'ON is a superset of OFF')

        new = [it for it in on if it['display_title'] not in titles_off]
        check(len(new) == 248, f'ON adds 248 titles ({len(new)})')
        check(all(se._tier_for_item(it) == 'lo' for it in new),
              'every added title is lo-tier (Haiku lane)')
        check(not any(se._search_needed_for_item(it) for it in new),
              'no added title asks for a web_search')
        check(all(int(it['best_rank']) > 40 for it in new),
              'every added title sits past the old head line')

        _with_env(None)
        again = se._collect_streaming()
        check(len(again) == 101,
              f'switching back OFF restores the 40-row read ({len(again)})')
    finally:
        _with_env(None)
        (se._read_snapshot, se.published_chart_index,
         se.published_rank_for, se.published_chart_label) = saved

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
