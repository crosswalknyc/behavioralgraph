"""Terminal pass: dip lift, deep-cut hold, one board writer at a time (2026-10-06).

Run as a module: python3 -m scripts.trends_scrapers.test_board_terminal_2026_10_06
"""
import os
import subprocess
import sys
import time

from scripts.trends_scrapers import board_invariants as bi
from scripts.trends_scrapers import stream_estimates as se
from scripts.trends_scrapers import _base


def _row(title, v, pos, kind='film'):
    return {'title': title, 'category_display': 'Film' if kind == 'film' else 'TV Series',
            'us_streams': {'us_estimate': v}, 'published_rank': pos,
            'published_chart': 'Netflix Top 10 US', 'published_group': 'us_films'}


def _store_for(rows, slug):
    items = {}
    for r in rows:
        k = f"film:{se._cp_normalize(r['title'])}"
        items[k] = {'kind': 'film', 'display_title': r['title'],
                    'us_estimate': r['us_streams']['us_estimate'],
                    'by_platform': {slug: {'us_estimate': r['us_streams']['us_estimate'],
                                           'us_estimate_low': 1, 'us_estimate_high': 10**8}}}
    return items


def test_dip_lifted_not_cascaded():
    slug = 'netflix'
    vals = [900_123, 850_431, 802_117, 9_731, 701_221, 650_557, 600_301, 550_909, 500_117, 450_777]
    rows = [_row(f'T{i}', v, i + 1) for i, v in enumerate(vals)]
    store = _store_for(rows, slug)
    payload = {'cards': {'streaming_trending': {slug: {'items': rows}}}}
    orig_read, orig_caps = se._read_snapshot, bi._daily_caps
    se._read_snapshot = lambda name, *a, **k: {'items': store, 'target_date': '2026-10-06'}
    bi._daily_caps = lambda: {slug: 3_000_000}
    try:
        tf = bi.terminal_fix(payload, write=False)
    finally:
        se._read_snapshot, bi._daily_caps = orig_read, orig_caps
    moved = {d['title']: (d['to'], d['why']) for d in tf['detail']}
    assert 'T3' in moved and moved['T3'][1] == 'I1 chart dip', moved
    to = moved['T3'][0]
    assert 701_221 < to < 802_117, to
    # Nothing below the dip was pulled down.
    assert all(t == 'T3' for t in moved), moved
    assert tf['unresolved'] == 0, tf['unresolved_detail']
    print('dip lift ok', moved)


def test_deep_cut_held():
    slug = 'netflix'
    # Whole chart tiny except the rows below: no dip (median is tiny),
    # so the cascade would want to cut the big rows by 100x. Held.
    vals = [9_731, 8_431, 7_117, 6_221, 5_557, 650_301, 600_909, 550_117]
    rows = [_row(f'T{i}', v, i + 1) for i, v in enumerate(vals)]
    store = _store_for(rows, slug)
    payload = {'cards': {'streaming_trending': {slug: {'items': rows}}}}
    orig_read, orig_caps = se._read_snapshot, bi._daily_caps
    se._read_snapshot = lambda name, *a, **k: {'items': store, 'target_date': '2026-10-06'}
    bi._daily_caps = lambda: {slug: 3_000_000}
    try:
        tf = bi.terminal_fix(payload, write=False)
    finally:
        se._read_snapshot, bi._daily_caps = orig_read, orig_caps
    held = [d for d in tf['unresolved_detail'] if 'cut too deep' in d['why']]
    assert held, tf
    assert not any(d['to'] < 100_000 for d in tf['detail']), tf['detail']
    print('deep cut held ok', len(held))


def test_board_lock_serialises_and_inherits():
    from scripts.trends_scrapers.run_guard import BoardLock
    path = f'/tmp/test_board_lock_{os.getpid()}.lock'
    os.environ.pop('TRENDS_BOARD_LOCK_HELD', None)
    with BoardLock('holder', path=path) as a:
        assert a.acquired and not a.inherited
        assert os.environ.get('TRENDS_BOARD_LOCK_HELD') == str(os.getpid())
        # Child inherits via env: passes straight through.
        with BoardLock('child', path=path) as b:
            assert b.inherited
        # Other process tree without the env waits.
        env = {k: v for k, v in os.environ.items() if k != 'TRENDS_BOARD_LOCK_HELD'}
        code = ("import time,sys;from scripts.trends_scrapers.run_guard import BoardLock;"
                f"t=time.monotonic();l=BoardLock('other', path={path!r}, wait_s=3);l.__enter__();"
                "print('waited', round(l.waited_s), 'acq', l.acquired)")
        t0 = time.monotonic()
        p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                           env=env, cwd=os.getcwd())
        dt = time.monotonic() - t0
        assert 'waited 5' in p.stdout or 'waited 10' in p.stdout, (p.stdout, p.stderr)
        assert dt >= 3, dt
    assert 'TRENDS_BOARD_LOCK_HELD' not in os.environ
    os.remove(path)
    print('board lock ok', p.stdout.strip())


if __name__ == '__main__':
    test_dip_lifted_not_cascaded()
    test_deep_cut_held()
    test_board_lock_serialises_and_inherits()
    print('ALL OK')
