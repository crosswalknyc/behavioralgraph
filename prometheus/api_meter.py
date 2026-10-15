"""Counters for the key surface: rate windows, the daily spend cap,
idempotency replay and the usage report.

Backed by the relational store when there is one (so the cap is global
across every web worker) and by in-process memory otherwise (local runs,
tests). Every call is fail-open on a store fault: a counter outage must
never take the API down, it only widens the window for a moment.
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
import time
import uuid

__all__ = ['check_rate', 'spent_today', 'record', 'idempotent_replay', 'usage', 'reset_memory']

_MEM_CALLS: list = []          # (ts_epoch, key_id, endpoint, day, charged_usd, kind, status, ms)
_MEM_IDEM: dict = {}           # (key_id, idem_key) -> (ts_epoch, response, status)
_MEM_LOCK = threading.Lock()


def _store():
    try:
        from . import store as _s
        return _s if _s.enabled() else None
    except Exception:
        return None


def _iso(ts: float) -> str:
    return _dt.datetime.utcfromtimestamp(ts).strftime('%Y-%m-%dT%H:%M:%SZ')


def _today() -> str:
    return _dt.datetime.utcnow().strftime('%Y-%m-%d')


def reset_memory():
    with _MEM_LOCK:
        _MEM_CALLS.clear()
        _MEM_IDEM.clear()


def _mem_count(key_id, endpoint, since):
    with _MEM_LOCK:
        return sum(1 for r in _MEM_CALLS if r[1] == key_id and r[2] == endpoint and r[0] >= since)


def check_rate(key_id: str, endpoint: str, per_minute: int, per_day: int, now: float = None) -> dict:
    """{'allowed': bool, 'retry_after': int, 'minute': n, 'day': n}.
    Counts calls already recorded; the caller records this one after."""
    now = now or time.time()
    st = _store()
    minute = day = None
    if st is not None:
        minute = st.api_calls_since(key_id, endpoint, _iso(now - 60))
        day = st.api_calls_since(key_id, endpoint, _iso(now - 86400))
    if minute is None or day is None:
        minute = _mem_count(key_id, endpoint, now - 60)
        day = _mem_count(key_id, endpoint, now - 86400)
    if minute >= per_minute:
        return {'allowed': False, 'retry_after': 20, 'minute': minute, 'day': day}
    if day >= per_day:
        return {'allowed': False, 'retry_after': 3600, 'minute': minute, 'day': day}
    return {'allowed': True, 'retry_after': 0, 'minute': minute, 'day': day}


def spent_today(key_id: str) -> float:
    st = _store()
    if st is not None:
        v = st.api_spent_on(key_id, _today())
        if v is not None:
            return float(v)
    today = _today()
    with _MEM_LOCK:
        return float(sum(r[4] or 0.0 for r in _MEM_CALLS if r[1] == key_id and r[3] == today))


def record(*, key_id: str, owner: str, endpoint: str, kind: str, status: int, ms: int, trace_id: str = '',
           request_id: str = '', idempotency_key: str = '', conversation_id: str = '', charged_usd: float = 0.0,
           response: dict = None) -> str:
    """One call row. The response is kept only when an idempotency key
    was sent (that is what replay reads back)."""
    now = time.time()
    call_id = uuid.uuid4().hex[:16]
    row = {'call_id': call_id, 'ts': _iso(now), 'day': _today(), 'key_id': key_id, 'owner': owner, 'endpoint': endpoint,
           'kind': kind, 'status': int(status or 0), 'ms': int(ms or 0), 'trace_id': trace_id or None,
           'request_id': request_id or None, 'idempotency_key': idempotency_key or None,
           'conversation_id': conversation_id or None, 'charged_usd': float(charged_usd or 0.0),
           'response': (json.dumps(response, default=str) if (response is not None and idempotency_key) else None)}
    with _MEM_LOCK:
        _MEM_CALLS.append((now, key_id, endpoint, row['day'], row['charged_usd'], kind, row['status'], row['ms']))
        if len(_MEM_CALLS) > 20000:
            del _MEM_CALLS[:5000]
        if idempotency_key and response is not None:
            _MEM_IDEM[(key_id, idempotency_key)] = (now, response, int(status or 200))
    st = _store()
    if st is not None:
        st.record_api_call(row)
    return call_id


def idempotent_replay(key_id: str, idempotency_key: str, ttl_s: int = 86400):
    """(response, status) recorded under this key within the TTL, or None."""
    if not idempotency_key:
        return None
    now = time.time()
    with _MEM_LOCK:
        hit = _MEM_IDEM.get((key_id, idempotency_key))
    if hit and now - hit[0] <= ttl_s:
        return hit[1], hit[2]
    st = _store()
    if st is not None:
        got = st.api_idempotent_response(key_id, idempotency_key, _iso(now - ttl_s))
        if got:
            return got
    return None


def usage(key_id: str, day_from: str, day_to: str) -> list:
    st = _store()
    rows = st.api_usage(key_id, day_from, day_to) if st is not None else None
    if rows is not None:
        return rows
    agg = {}
    with _MEM_LOCK:
        for r in _MEM_CALLS:
            if r[1] != key_id or not (day_from <= r[3] <= day_to):
                continue
            a = agg.setdefault((r[3], r[5]), {'day': r[3], 'kind': r[5], 'calls': 0, 'charged_usd': 0.0, 'ms': [], 'errors': 0})
            a['calls'] += 1
            a['charged_usd'] += float(r[4] or 0)
            a['ms'].append(int(r[7] or 0))
            a['errors'] += 1 if (r[6] or 0) >= 400 else 0
    out = []
    for a in sorted(agg.values(), key=lambda x: (x['day'], x['kind'])):
        a['avg_ms'] = int(sum(a['ms']) / len(a['ms'])) if a['ms'] else 0
        a.pop('ms', None)
        a['charged_usd'] = round(a['charged_usd'], 6)
        out.append(a)
    return out
