"""First-party calibration (2026-10-06): a partner-reported title-level
feed sets the scale of a service's rail.

Run: PYTHONPATH=. python3 -m scripts.trends_scrapers.test_first_party_calibration
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from scripts.trends_scrapers import first_party_calibration as fpc  # noqa: E402
from scripts.trends_scrapers import stream_estimates as se  # noqa: E402

DAY = '2026-10-06'
SLUG = 'lionsgateplus'


def _doc() -> dict:
    bands = [1091, 1072, 950, 942, 940, 700, 600, 500, 400, 363, 300, 250,
             200, 185, 150, 120, 100, 80, 60, 46] + [max(1, 40 - i) for i in range(40)]
    titles = {
        'john wick chapter 4': {'title': 'John Wick: Chapter 4', 'month': '2026-08',
                                'monthly': 33831, 'daily': 1091, 'rank': 1,
                                'trend_pct': 20783.0},
        'deepwater horizon': {'title': 'Deepwater Horizon', 'month': '2026-08',
                              'monthly': 29201, 'daily': 942, 'rank': 4,
                              'trend_pct': 12.0},
        'lawless': {'title': 'Lawless', 'month': '2026-08', 'monthly': 11242,
                    'daily': 363, 'rank': 10, 'trend_pct': -4.0},
        'peeping tom': {'title': 'Peeping Tom', 'month': '2026-08', 'monthly': 40,
                        'daily': 1, 'rank': 600, 'trend_pct': None},
    }
    return {'slug': SLUG, 'label': 'Lionsgate+', 'unit': 'streams',
            'scope': 'US, films, all offers', 'months': ['2026-04', '2026-08'],
            'service': {'month': '2026-08', 'days': 31, 'monthly_total': 690133,
                        'daily_total': 22262, 'titles': 725},
            'bands': bands, 'titles': titles,
            'range': {'months': 5, 'daily_total_min': 14566,
                      'daily_total_max': 23270, 'title_daily_max': 1918}}


def _row(key: str, title: str, cur: int, kind: str = 'film') -> dict:
    return {'display_title': title, 'kind': kind, 'us_estimate': cur,
            'by_platform': {SLUG: {'us_estimate': cur, 'us_estimate_low': int(cur * .8),
                                   'us_estimate_high': int(cur * 1.2)}}}


def _store() -> dict:
    return {
        'film:silver linings playbook': _row('film:silver linings playbook',
                                             'Silver Linings Playbook', 7840),
        'film:red 2': _row('film:red 2', 'Red 2', 6319),
        'film:john wick chapter 4': _row('film:john wick chapter 4',
                                         'John Wick: Chapter 4', 1956),
        'film:deepwater horizon': _row('film:deepwater horizon', 'Deepwater Horizon', 655),
        'film:lawless': _row('film:lawless', 'Lawless', 2511),
        'film:peeping tom': _row('film:peeping tom', 'Peeping Tom', 109),
        'tv:minx': _row('tv:minx', 'Minx', 3070, 'tv'),
        'tv:heels': _row('tv:heels', 'Heels', 1419, 'tv'),
        'film:unknown feature': _row('film:unknown feature', 'Unknown Feature', 4000),
        'film:tiny already': _row('film:tiny already', 'Tiny Already', 12),
    }


def _patch_chart(monkey: list, positions: dict[str, int]) -> None:
    """Pretend today's published chart carries these titles."""
    orig_snap, orig_idx, orig_rank = (se._read_snapshot, se.published_chart_index,
                                      se.published_rank_for)
    monkey.extend([('_read_snapshot', orig_snap), ('published_chart_index', orig_idx),
                   ('published_rank_for', orig_rank)])
    se._read_snapshot = lambda name: {'fake': True} if str(name) == SLUG else None
    se.published_chart_index = lambda slug, snap: {'positions': positions}
    se.published_rank_for = (lambda index, kind, title:
                             ((index['positions'][title.lower()], 'Top 10 in the U.S.', 'film')
                              if title.lower() in index['positions'] else None))


def _unpatch(monkey: list) -> None:
    for name, fn in monkey:
        setattr(se, name, fn)


def test_levels_chart_titled_held() -> None:
    fpc.set_cached(SLUG, _doc())
    monkey: list = []
    _patch_chart(monkey, {'silver linings playbook': 1, 'red 2': 2})
    try:
        store = _store()
        stats = fpc.apply_to_store(store, DAY, slugs=[SLUG])
    finally:
        _unpatch(monkey)
    per = stats['slugs'][SLUG]
    assert per['chart'] == 2, per
    assert per['titled'] == 4, per
    assert per['held'] >= 3, per
    v = {k: store[k]['by_platform'][SLUG]['us_estimate'] for k in store}
    # chart rows: band of their position, strictly descending
    assert 1000 <= v['film:silver linings playbook'] <= 1150, v
    assert v['film:red 2'] < v['film:silver linings playbook'], v
    # titled rows sit under the chart's last slot, in feed order
    floor = v['film:red 2']
    assert v['film:john wick chapter 4'] < floor, (v, floor)
    assert v['film:john wick chapter 4'] > v['film:deepwater horizon'] > v['film:lawless'], v
    assert v['film:peeping tom'] <= 3, v
    # held rows only ever come down, and land under the floor
    assert v['tv:minx'] < floor and v['tv:heels'] < floor, v
    assert v['tv:minx'] >= v['tv:heels'], v
    assert v['film:unknown feature'] < floor, v
    assert v['film:tiny already'] == 12, v
    # aggregate moved with the platform block
    assert store['film:silver linings playbook']['us_estimate'] < 7840
    print('ok  levels: chart / titled / held')


def test_no_chart_feed_owns_order() -> None:
    fpc.set_cached(SLUG, _doc())
    orig = se._read_snapshot
    se._read_snapshot = lambda name: None
    try:
        store = _store()
        fpc.apply_to_store(store, DAY, slugs=[SLUG])
    finally:
        se._read_snapshot = orig
    v = {k: store[k]['by_platform'][SLUG]['us_estimate'] for k in store}
    # titled rows take their own reading when nothing charts above them
    assert 980 <= v['film:john wick chapter 4'] <= 1200, v
    assert 850 <= v['film:deepwater horizon'] <= 1030, v
    # unknown TV is held at the rank-3 band, unknown film at rank 10
    assert v['tv:minx'] <= 1050, v
    assert v['film:unknown feature'] <= 400, v
    print('ok  no chart: feed owns the order')


def test_prompt_lines() -> None:
    fpc.set_cached(SLUG, _doc())
    s = fpc.service_prompt_line(SLUG)
    assert 'PLATFORM-REPORTED' in s and '22,262' in s and '1,091' in s, s
    t = fpc.title_prompt_line(SLUG, 'John Wick: Chapter 4')
    assert '33,831' in t and '1,091 a day' in t and '#1' in t, t
    assert '+20783%' not in t, t          # arrival, not a trend
    t2 = fpc.title_prompt_line(SLUG, 'Deepwater Horizon')
    assert '+12%' in t2, t2
    assert fpc.title_prompt_line(SLUG, 'Not In Feed') == ''
    print('ok  prompt lines')


def test_kill_switch() -> None:
    fpc.set_cached(SLUG, _doc())
    os.environ['TRENDS_FIRST_PARTY'] = '0'
    try:
        fpc._CACHE.clear()
        assert fpc.load(SLUG) is None
        store = _store()
        stats = fpc.apply_to_store(store, DAY, slugs=[SLUG])
        assert stats['moved'] == 0, stats
        assert fpc.service_prompt_line(SLUG) == ''
    finally:
        os.environ.pop('TRENDS_FIRST_PARTY', None)
    print('ok  kill switch')


if __name__ == '__main__':
    test_levels_chart_titled_held()
    test_no_chart_feed_owns_order()
    test_prompt_lines()
    test_kill_switch()
    print('all first-party calibration tests passed')
