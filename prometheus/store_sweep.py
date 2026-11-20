"""The nightly reconcile of the store's document mirror (Jenna 2026-11-20:
compare mirror rows to S3 ETags and repair drift silently, S3 always wins,
logged, no email).

One daemon thread per web process wakes every ten minutes. In the night
window (04:00 to 04:59 UTC) when the marker says today has not run, it
claims the one lock under ``system/prometheus_store/`` with a conditional
put (two instances, one run; a lock older than two hours is stale and is
taken over), runs ``store.reconcile`` against the host's S3 client, writes
the marker with the counts, and releases the lock. Everything it writes
lives under the system prefix; no reader surface, no mail.

Off under ``PM_STORE_RECONCILE=0``, in worker mode, under
``REGRESSION_TEST_MODE``, and when the store is off. The operator can run
the same reconcile at any time through ``POST /api/prometheus/ops/store-
reconcile`` (prometheus.store_ops).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import time

from . import store as _store

PREFIX = 'system/prometheus_store/'
LOCK_KEY = PREFIX + 'reconcile_lock.json'
MARKER_KEY = PREFIX + 'reconcile.json'
HOUR_UTC = int(os.environ.get('PM_STORE_RECONCILE_HOUR', '4'))
TICK_S = float(os.environ.get('PM_STORE_RECONCILE_TICK_S', '600'))
LOCK_STALE_S = 7200.0

_started = {'thread': None}


def _today():
    return _dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d')


def _read_json(s3, bucket, key):
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8')), str(obj.get('ETag') or '')
    except Exception:
        return None, ''


def _claim(s3, bucket, owner, now=None):
    """The one lock: a conditional create; a stale lock is taken over."""
    now = float(now or time.time())
    body = json.dumps({'owner': owner, 'at': now}).encode('utf-8')
    doc, etag = _read_json(s3, bucket, LOCK_KEY)
    try:
        if doc is None:
            s3.put_object(Bucket=bucket, Key=LOCK_KEY, Body=body, ContentType='application/json', IfNoneMatch='*')
            return True
        if now - float(doc.get('at') or 0) < LOCK_STALE_S:
            return False
        if etag:
            s3.put_object(Bucket=bucket, Key=LOCK_KEY, Body=body, ContentType='application/json', IfMatch=etag)
        else:
            s3.put_object(Bucket=bucket, Key=LOCK_KEY, Body=body, ContentType='application/json')
        return True
    except Exception:
        return False


def _release(s3, bucket):
    try:
        s3.delete_object(Bucket=bucket, Key=LOCK_KEY)
    except Exception:
        pass


def due(s3, bucket, now=None):
    """In the night window and not yet run today."""
    now_dt = _dt.datetime.now(_dt.timezone.utc) if now is None else now
    if now_dt.hour != HOUR_UTC:
        return False
    marker, _ = _read_json(s3, bucket, MARKER_KEY)
    return not (isinstance(marker, dict) and marker.get('day') == now_dt.strftime('%Y-%m-%d'))


def run_once(s3, bucket, owner='web', force=False):
    """One reconcile under the lock. Returns the counts, or None when the
    lock was held elsewhere or the run was not due."""
    if not _store.enabled():
        return None
    if not force and not due(s3, bucket):
        return None
    if not _claim(s3, bucket, owner):
        return None
    try:
        out = _store.reconcile(s3, bucket)
        try:
            s3.put_object(Bucket=bucket, Key=MARKER_KEY, ContentType='application/json',
                          Body=json.dumps({'day': _today(), 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                                           'owner': owner, 'counts': out}).encode('utf-8'))
        except Exception as e:
            _store._note('store.reconcile_marker', e)
        return out
    finally:
        _release(s3, bucket)


def _loop(host):
    owner = f"{os.environ.get('RENDER_INSTANCE_ID') or 'web'}:{os.getpid()}"
    while True:
        try:
            time.sleep(TICK_S)
            if not _store.enabled():
                continue
            s3 = getattr(host, 's3_client', None)
            bucket = getattr(host, 'S3_BUCKET', None)
            if s3 is None or not bucket:
                continue
            run_once(s3, bucket, owner=owner)
        except Exception as e:
            _store._note('store.reconcile_loop', e)


def start(host):
    """Start the sweeper once per process. Returns the thread or None."""
    if os.environ.get('REGRESSION_TEST_MODE') or os.environ.get('PM_WORKER_MODE') == '1' \
            or os.environ.get('PM_STORE_RECONCILE', '1') == '0' or not _store.enabled():
        return None
    if _started['thread'] is not None:
        return _started['thread']
    t = threading.Thread(target=_loop, args=(host,), daemon=True, name='pm-store-reconcile')
    t.start()
    _started['thread'] = t
    print('[store-reconcile] nightly sweeper started', flush=True)
    return t
