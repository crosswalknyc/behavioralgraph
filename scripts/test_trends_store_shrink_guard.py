#!/usr/bin/env python3
"""The Trends store cannot be wiped by a pass that lost its read.

2026-11-05: a read-modify-write pass on the Mac lane got None back from
`stream_estimates._read_snapshot` (the 54 MB read dropped), carried on
with `or {}`, and wrote the 90 rows it had priced over the 56,844-row
store. Two nets now hold:

1. `_base.write_snapshot` refuses a body under STORE_SHRINK_FLOOR of the
   store it would replace, measured against the largest of latest/ and
   the last two dated copies (so a stub already on latest/ cannot lower
   the bar). Small snapshots are exempt; TRENDS_STORE_ALLOW_SHRINK=1
   lifts it for a deliberate rebuild.
2. `_read_snapshot` retries a failed read and logs it; a missing key is
   still a quiet None.

Hermetic: fake S3, no network. Run: python3 scripts/test_trends_store_shrink_guard.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'x')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'x')

from scripts.trends_scrapers import _base  # noqa: E402
from scripts.trends_scrapers import stream_estimates as se  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(('PASS ' if cond else 'FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


class _Err(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {'Error': {'Code': code}}


class _FakeS3:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)
        self.puts: list[str] = []

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _Err('404')
        return {'ContentLength': len(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body
        self.puts.append(Key)
        return {'ETag': '"x"'}

    def get_object(self, Bucket, Key, **kw):
        if Key not in self.objects:
            raise _Err('NoSuchKey')
        import io
        return {'Body': io.BytesIO(self.objects[Key])}


def _big_store(n: int) -> bytes:
    items = {f'title:row {i}': {'kind': 'title', 'display_title': f'Row {i}' * 8,
                                'by_platform': {'netflix': {'us_audience': 1000 + i}}}
             for i in range(n)}
    return json.dumps({'items': items, 'count': n}).encode()


def run_write(s3, payload, *, source='stream_estimates'):
    from scripts.trends_scrapers import value_distinctness as _vd
    saved, saved_vd = _base._s3_client, _vd.enforce_on_payload
    _base._s3_client = lambda: s3
    _vd.enforce_on_payload = lambda payload, day: None   # the history store is its own S3 write
    try:
        _base.write_snapshot(source, payload, also_dated=False)
    finally:
        _base._s3_client, _vd.enforce_on_payload = saved, saved_vd


def main() -> int:
    now = datetime.now(timezone.utc)
    latest = f'{_base.S3_LATEST_PREFIX}stream_estimates.json'
    dated_y = f'{_base.S3_DATED_PREFIX.format(date=(now.date().fromordinal(now.date().toordinal() - 1)).isoformat())}stream_estimates.json'
    big = _big_store(12000)
    check(len(big) > _base.STORE_SHRINK_MIN_BYTES, f'fixture store is above the guard floor ({len(big):,} bytes)')

    # 1. a 90-row body over a full store is refused and nothing is written
    s3 = _FakeS3({latest: big})
    stub = {'items': {f'comic:c{i}': {'kind': 'comic'} for i in range(90)}, 'count': 90}
    refused = False
    try:
        run_write(s3, stub)
    except _base.StoreShrinkRefused:
        refused = True
    check(refused, 'stub over a full store raises StoreShrinkRefused')
    check(s3.puts == [], 'refused write put nothing')
    check(s3.objects[latest] == big, 'latest/ untouched after refusal')

    # 2. the bar comes from the dated copies too: a stub already on latest/ cannot lower it
    s3 = _FakeS3({latest: json.dumps(stub).encode(), dated_y: big})
    refused = False
    try:
        run_write(s3, {'items': {f'comic:c{i}': {'kind': 'comic'} for i in range(400)}, 'count': 400})
    except _base.StoreShrinkRefused:
        refused = True
    check(refused, 'a slightly larger stub over a stubbed latest/ is still refused against the dated copy')

    # 3. a full-size body writes normally (a few percent smaller is fine)
    s3 = _FakeS3({latest: big})
    run_write(s3, json.loads(_big_store(11500)))
    check(latest in s3.puts, 'a body within the floor writes')

    # 4. the restore itself (a full body over a stub) writes
    s3 = _FakeS3({latest: json.dumps(stub).encode()})
    run_write(s3, json.loads(big))
    check(latest in s3.puts and len(s3.objects[latest]) > 1_000_000, 'a full body over a stub writes')

    # 5. small snapshots are exempt
    small_key = f'{_base.S3_LATEST_PREFIX}britbox.json'
    s3 = _FakeS3({small_key: b'{"items": %s}' % json.dumps([{'t': i} for i in range(400)]).encode()})
    run_write(s3, {'items': [{'t': 1}]}, source='britbox')
    check(small_key in s3.puts, 'a small per-platform snapshot may shrink freely')

    # 6. the override lifts the guard for a deliberate rebuild
    os.environ['TRENDS_STORE_ALLOW_SHRINK'] = '1'
    try:
        s3 = _FakeS3({latest: big})
        run_write(s3, stub)
        check(latest in s3.puts, 'TRENDS_STORE_ALLOW_SHRINK=1 writes the smaller body')
    finally:
        os.environ.pop('TRENDS_STORE_ALLOW_SHRINK', None)

    # 7. _read_snapshot: missing key is a quiet None, a failing read retries then returns None
    calls = {'n': 0}

    class _Flaky:
        def get_object(self, Bucket, Key):
            calls['n'] += 1
            raise _Err('RequestTimeout')

    class _Missing:
        def get_object(self, Bucket, Key):
            calls['n'] += 1
            raise _Err('NoSuchKey')

    saved_s3, saved_sleep = se._s3, se.time.sleep
    se.time.sleep = lambda s: None
    try:
        se._s3 = lambda: _Missing()
        calls['n'] = 0
        check(se._read_snapshot('stream_estimates') is None and calls['n'] == 1, 'missing key: None after one attempt')
        se._s3 = lambda: _Flaky()
        calls['n'] = 0
        check(se._read_snapshot('stream_estimates') is None and calls['n'] == se._READ_SNAPSHOT_ATTEMPTS,
              f'failing read: retried {se._READ_SNAPSHOT_ATTEMPTS} times, then None')
        s3 = _FakeS3({latest: big})
        se._s3 = lambda: s3
        got = se._read_snapshot('stream_estimates')
        check(isinstance(got, dict) and got.get('count') == 12000, 'a good read returns the store')
    finally:
        se._s3, se.time.sleep = saved_s3, saved_sleep

    print()
    print('ALL PASS' if not FAILS else f'{len(FAILS)} FAIL(S): {FAILS}')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
