#!/usr/bin/env python3
"""Writers render the store they write; the dashboard renders the copy.

2026-11-05. The dashboard reads `trends_iq_snapshots/published/`, a
copy of `latest/` made at the 12:01 AM Pacific flip and hourly between
flips (`publish_board`). The coverage gate, the terminal bracket and
the board gate write latest/ and then render to measure their own
result. When that render read published/, every row the bracket had
just filled still tallied as missing and 45 rows reached the alert as
"bracket written, row still renders blank: a render-side key
mismatch". This test holds the contract that fixed it:

  1. `live_store_reads()` makes a live snapshot read resolve to
     latest/; outside it, to published/ (with the latest/ fallback
     only when the published key is absent).
  2. A render inside `live_store_reads()` caches under a separate
     prefix, never under a key the dashboard serves.
  3. `invalidate_live_compute_view_caches` publishes first unless a
     nightly run holds the flip (then it refreshes nothing), and
     refreshes the dashboard's caches from published/ even when the
     calling process reads latest/.
  4. `coverage_gate.run_gate` restores the previous setting on exit,
     including on an exception.
  5. `terminal_bracket.fill` reports skips by row with the title
     apart from the reason, so a title with a colon in it is not
     attributed to the wrong row.
  6. `BoardLock` holds a shared S3 lease as well as the host file
     lock, so a writer on the other host waits instead of writing the
     store back over this one's readings (the box watch dropped the
     Mac gate's bracket writes at 17:55Z the same day).

Hermetic: S3 is a dict; no network.

    python3 scripts/test_trends_live_store_reads.py
"""
from __future__ import annotations

import json
import os
import sys
import types
from contextlib import contextmanager
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault('TRENDS_IQ_CACHE_BUCKET', 'test-bucket')

import trends_iq  # noqa: E402


class _NoKey(Exception):
    pass


class _FakeS3:
    """Just enough of boto3's S3 client for the paths under test."""

    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)
        self.gets: list[str] = []
        self.puts: list[str] = []
        self.deletes: list[str] = []

    def get_object(self, Bucket, Key):
        self.gets.append(Key)
        if Key not in self.objects:
            raise _NoKey(Key)
        body = self.objects[Key]
        return {'Body': types.SimpleNamespace(read=lambda: body),
                'Metadata': {}}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _NoKey(Key)
        return {'ContentLength': len(self.objects[Key]), 'Metadata': {}}

    def put_object(self, Bucket, Key, Body, **kw):
        self.puts.append(Key)
        self.objects[Key] = Body if isinstance(Body, bytes) else str(Body).encode()
        return {}

    def delete_object(self, Bucket, Key):
        self.deletes.append(Key)
        self.objects.pop(Key, None)
        return {}

    def get_paginator(self, name):
        objs = self.objects

        class _P:
            def paginate(self, Bucket, Prefix):
                keys = sorted(k for k in objs if k.startswith(Prefix))
                yield {'Contents': [{'Key': k, 'Size': len(objs[k])}
                                    for k in keys]}
        return _P()


PUB = trends_iq._SNAPSHOT_PREFIX
LIVE = trends_iq._SNAPSHOT_LIVE_PREFIX

_FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        _FAILS.append(msg)


@contextmanager
def _fake_s3(objects: dict[str, bytes]):
    s3 = _FakeS3(objects)
    with mock.patch.object(trends_iq, '_s3_client', return_value=s3):
        yield s3


def _snap(name: str) -> bytes:
    return json.dumps({'items': [{'title': name}], 'source': name}).encode()


def test_read_prefix_switches() -> None:
    print('1. live snapshot reads resolve to published/ or latest/ by mode')
    objs = {f'{PUB}demo.json': _snap('published'),
            f'{LIVE}demo.json': _snap('latest')}
    with _fake_s3(objs) as s3:
        trends_iq.use_live_store(False)
        got = trends_iq._read_snapshot('demo')
        check((got or {}).get('source') == 'published',
              'default mode reads published/')
        with trends_iq.live_store_reads():
            check(trends_iq.reads_live_store(), 'inside the block the mode is live')
            got = trends_iq._read_snapshot('demo')
            check((got or {}).get('source') == 'latest',
                  'live mode reads latest/')
        check(not trends_iq.reads_live_store(), 'mode restored after the block')
        # Fallback to latest/ only applies to the published read.
        s3.objects.pop(f'{PUB}demo.json')
        got = trends_iq._read_snapshot('demo')
        check((got or {}).get('source') == 'latest',
              'published/ absent: default mode falls back to latest/')
        with trends_iq.live_store_reads():
            s3.gets.clear()
            trends_iq._read_snapshot('demo')
            check(all(k.startswith(LIVE) for k in s3.gets),
                  'live mode never touches published/')


def test_cache_namespace() -> None:
    print('2. a live-mode render caches apart from the dashboard entries')
    filters = {'geo_type': 'National', 'geo_value': '', 'lookback_days': 1,
               'asof': '2026-11-05'}
    trends_iq.use_live_store(False)
    dash = trends_iq._cache_key(filters)
    with trends_iq.live_store_reads():
        live = trends_iq._cache_key(filters)
    check(dash.startswith(trends_iq.S3_CACHE_PREFIX),
          'dashboard key under S3_CACHE_PREFIX')
    check(live.startswith(trends_iq.S3_CACHE_LIVE_PREFIX),
          'live key under S3_CACHE_LIVE_PREFIX')
    check(not live.startswith(trends_iq.S3_CACHE_PREFIX),
          'the dashboard listing excludes live-mode entries')
    check(dash.rsplit('/', 1)[1] == live.rsplit('/', 1)[1],
          'same filters hash the same way in both namespaces')


def test_invalidate_publishes_first() -> None:
    print('3. invalidation publishes first, respects the hold, refreshes published/')
    from scripts.trends_scrapers import publish_board as pb
    seen: dict = {}

    def _refresh():
        seen['mode_during_refresh'] = trends_iq.reads_live_store()
        return 7

    # (a) no hold: publish then refresh, with published/ reads even
    # when the caller is in live mode.
    with mock.patch.object(pb, 'read_hold', return_value=None), \
         mock.patch.object(pb, 'copy_latest',
                           side_effect=lambda reason, s3=None: seen.setdefault('copied', reason)) , \
         mock.patch.object(trends_iq, '_refresh_dashboard_caches', _refresh):
        with trends_iq.live_store_reads():
            n = trends_iq.invalidate_live_compute_view_caches()
    check(seen.get('copied') == 'writer', 'latest/ copied onto published/ first')
    check(seen.get('mode_during_refresh') is False,
          'the dashboard refresh renders published/ even inside live mode')
    check(n == 7, 'refresh outcome returned')

    # (b) held: nothing copied, nothing refreshed.
    seen.clear()
    with mock.patch.object(pb, 'read_hold', return_value={'started': 'x'}), \
         mock.patch.object(pb, 'copy_latest',
                           side_effect=lambda *a, **k: seen.setdefault('copied', True)), \
         mock.patch.object(trends_iq, '_refresh_dashboard_caches', _refresh):
        n = trends_iq.invalidate_live_compute_view_caches()
    check(n == 0 and 'copied' not in seen and 'mode_during_refresh' not in seen,
          'under a hold the dashboard keeps its board: no copy, no refresh')

    # (c) publish_board failing never blocks the refresh.
    seen.clear()
    with mock.patch.object(pb, 'read_hold', side_effect=RuntimeError('s3 down')), \
         mock.patch.object(trends_iq, '_refresh_dashboard_caches', _refresh):
        n = trends_iq.invalidate_live_compute_view_caches()
    check(n == 7 and seen.get('mode_during_refresh') is False,
          'a publish failure still refreshes published/ as it stands')


def test_run_gate_restores_mode() -> None:
    print('4. coverage_gate.run_gate renders live and restores the mode')
    from scripts.trends_scrapers import coverage_gate as cg
    seen: dict = {}

    def _impl(dry_run=False, *, terminal_only=False):
        seen['live'] = trends_iq.reads_live_store()
        return {'ok': True}

    trends_iq.use_live_store(False)
    with mock.patch.object(cg, '_run_gate_impl', _impl):
        out = cg.run_gate(terminal_only=True)
    check(out == {'ok': True} and seen.get('live') is True,
          'the gate body runs in live mode')
    check(not trends_iq.reads_live_store(), 'mode restored after the gate')

    def _boom(dry_run=False, *, terminal_only=False):
        raise RuntimeError('pass failed')

    with mock.patch.object(cg, '_run_gate_impl', _boom):
        try:
            cg.run_gate()
        except RuntimeError:
            pass
    check(not trends_iq.reads_live_store(), 'mode restored after an exception')


def test_bracket_skip_rows() -> None:
    print('5. bracket skips are attributed by row, title apart from reason')
    from scripts.trends_scrapers import terminal_bracket as tb
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import _base
    # One blank row whose title carries a colon, alone on its list, so
    # no bracket can be held; the store is empty and never written.
    path = 'streaming_trending.netflix.films'
    title = 'Taken: Natalee Holloway'
    payload = {'cards': {'streaming_trending': {'netflix': {'films': [
        {'title': title, 'rank': 1, 'kind': 'film'},
    ]}}}}
    with mock.patch.object(se, '_read_snapshot', return_value={'items': {}}), \
         mock.patch.object(_base, 'write_snapshot', return_value=None), \
         mock.patch.object(se, '_platform_daily_cap_for', return_value=0, create=True), \
         mock.patch.object(se, 'credibility_floor', return_value=100, create=True):
        stats = tb.fill(payload, [(path, title)], '2026-11-04', dry_run=True)
    rows = stats.get('skipped_rows') or []
    check(len(rows) >= 1, f'the row was reported as skipped ({len(rows)} rows)')
    if rows:
        p0, t0, why = rows[0]
        check(p0 == path, f'path attributed: {p0}')
        check(t0 == title, f'title kept whole: {t0!r}')
        check(bool(why) and not why.startswith(title),
              f'reason carried apart from the title: {why!r}')
        check((p0, f'{t0}: {why}') in stats['skipped'],
              'legacy skipped shape unchanged')
        # The gate keys its attribution from skipped_rows, so the alert
        # names this row under its full title, not under 'Taken'.
        attributed = {(pp, tt): f'{tt}: {w}' for pp, tt, w in rows}
        check((path, title) in attributed and (path, 'Taken') not in attributed,
              'gate-side attribution lands on the full title')


class _LeaseS3:
    """In-memory S3 with `If-None-Match: *` create semantics."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, **kw):
        if kw.get('IfNoneMatch') == '*' and Key in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError({'Error': {'Code': 'PreconditionFailed',
                                         'Message': 'exists'},
                               'ResponseMetadata': {'HTTPStatusCode': 412}},
                              'PutObject')
        self.objects[Key] = Body if isinstance(Body, bytes) else str(Body).encode()
        return {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _NoKey(Key)
        body = self.objects[Key]
        return {'Body': types.SimpleNamespace(read=lambda: body)}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)
        return {}


def test_board_lease() -> None:
    print('6. BoardLock holds a shared lease across hosts')
    import json
    import tempfile
    import threading
    from scripts.trends_scrapers import run_guard as rg
    s3 = _LeaseS3()
    key = rg.BOARD_LEASE_KEY
    d = tempfile.mkdtemp()
    path_a = os.path.join(d, 'a.lock')
    path_b = os.path.join(d, 'b.lock')     # a second host: its own file lock
    os.environ.pop(rg._BOARD_LOCK_HELD_ENV, None)
    with mock.patch('boto3.client', return_value=s3), \
         mock.patch.object(rg.time, 'sleep', lambda s: None):
        with rg.BoardLock('host A', path=path_a, wait_s=0) as a:
            check(a.acquired and a.lease_held, 'first writer takes the lease')
            body = json.loads(s3.objects[key])
            check(body.get('label') == 'host A' and body.get('token'),
                  'lease names its holder')
            # Re-entrant child inherits, never touches the lease.
            with rg.BoardLock('child', path=path_a) as c:
                check(c.inherited and not c.lease_held,
                      'a child of the holder passes through')
            # Simulate the other host in this process: drop the
            # re-entrancy marker so B does not pass through as a child.
            os.environ.pop(rg._BOARD_LOCK_HELD_ENV, None)
            # The other host: own file lock free, lease taken, no wait.
            with rg.BoardLock('host B', path=path_b, wait_s=0) as b:
                check(b.acquired and not b.lease_held,
                      'other host past its wait goes ahead without the lease')
            check(json.loads(s3.objects[key]).get('label') == 'host A',
                  'the holder\'s lease survives the other host\'s pass')
            os.environ[rg._BOARD_LOCK_HELD_ENV] = str(os.getpid())
        os.environ.pop(rg._BOARD_LOCK_HELD_ENV, None)
        check(key not in s3.objects, 'lease released on exit')

        # The other host waits while the holder works, then takes over.
        order: list[str] = []
        held = threading.Event()
        release = threading.Event()

        def _holder():
            with rg.BoardLock('host A', path=path_a, wait_s=0):
                os.environ.pop(rg._BOARD_LOCK_HELD_ENV, None)
                held.set()
                release.wait(5)
                order.append('A done')
                os.environ[rg._BOARD_LOCK_HELD_ENV] = str(os.getpid())
            os.environ.pop(rg._BOARD_LOCK_HELD_ENV, None)

        t = threading.Thread(target=_holder)
        t.start()
        held.wait(5)
        # Let B spin a few polls, then release A.
        real_sleep = __import__('time').sleep

        def _poll_sleep(sec):
            real_sleep(0.01)
            release.set()
        with mock.patch.object(rg.time, 'sleep', _poll_sleep):
            with rg.BoardLock('host B', path=path_b, wait_s=30) as b:
                order.append('B in')
                check(b.lease_held, 'other host took the lease once free')
        t.join(5)
        os.environ.pop(rg._BOARD_LOCK_HELD_ENV, None)
        check(order == ['A done', 'B in'], f'B waited for A: {order}')

        # A dead holder is broken.
        s3.objects[key] = json.dumps({'token': 'x', 'host': 'gone', 'pid': 1,
                                      'label': 'crashed',
                                      'started': '2020-01-01T00:00:00+00:00'}).encode()
        with rg.BoardLock('host B', path=path_b, wait_s=0) as b:
            check(b.lease_held, 'a stale lease is broken and taken')
        os.environ.pop(rg._BOARD_LOCK_HELD_ENV, None)

    # No S3 at all: the stage runs on the host lock alone.
    with mock.patch('boto3.client', side_effect=RuntimeError('no creds')):
        with rg.BoardLock('host A', path=path_a, wait_s=0) as a:
            check(a.acquired and not a.lease_held,
                  'no S3: host lock alone, never blocks')
    os.environ.pop(rg._BOARD_LOCK_HELD_ENV, None)


def main() -> int:
    prev = trends_iq.use_live_store(False)
    try:
        test_read_prefix_switches()
        test_cache_namespace()
        test_invalidate_publishes_first()
        test_run_gate_restores_mode()
        test_bracket_skip_rows()
        test_board_lease()
    finally:
        trends_iq.use_live_store(prev)
    if _FAILS:
        print(f'\n{len(_FAILS)} FAILED')
        for f in _FAILS:
            print('  -', f)
        return 1
    print('\nall passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
