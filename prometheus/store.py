"""A relational store for the things S3 JSON documents do badly (audit
item 7, Jenna 2026-10-14): the ask log, threads, jobs and ledger facts.

S3 stays the system of record until the switch is thrown; this module
is wired as a shadow writer at each of those four seams, so the tables
fill from the moment a database exists, and the readers here give the
morning review and the ops page real queries instead of prefix scans.

Backends, in order: Postgres through DATABASE_URL (psycopg2 or psycopg),
SQLite through PM_STORE_SQLITE (local runs and tests), else disabled.
`enabled()` tells the seams whether to write. Every call is fail-safe:
a store fault is counted (prometheus.faults) and never reaches a reader.

Tables (created by `migrate()`):
  pm_ask_log      one row per ask (ts, user, view, route, outcome, ms, question, reply, extra)
  pm_threads      one row per thread (user, tid, title, turns, updated_at)
  pm_jobs         one row per job document (kind, job_id, user, status, doc, updated_at)
  pm_ledger_facts one row per published figure (subject, metric, value, unit, source, window, as_of, doc)

Backfill: scripts/backfill_pm_store.py reads the S3 documents into the
tables; idempotent (upserts on the natural keys).
"""
from __future__ import annotations

import json
import os
import threading
import time

_conn = None
_lock = threading.Lock()
_kind = None     # 'pg' | 'sqlite' | None
_migrated = False

DDL = [
    """CREATE TABLE IF NOT EXISTS pm_ask_log (
        id TEXT PRIMARY KEY, ts TEXT NOT NULL, day TEXT NOT NULL, username TEXT, view TEXT, surface TEXT,
        route TEXT, outcome TEXT, ms INTEGER, subject TEXT, question TEXT, reply TEXT, trace_id TEXT, extra TEXT)""",
    "CREATE INDEX IF NOT EXISTS pm_ask_log_day ON pm_ask_log (day)",
    "CREATE INDEX IF NOT EXISTS pm_ask_log_user ON pm_ask_log (username, ts)",
    """CREATE TABLE IF NOT EXISTS pm_threads (
        username TEXT NOT NULL, tid TEXT NOT NULL, title TEXT, turns TEXT, n_turns INTEGER, updated_at TEXT,
        PRIMARY KEY (username, tid))""",
    """CREATE TABLE IF NOT EXISTS pm_jobs (
        kind TEXT NOT NULL, job_id TEXT NOT NULL, username TEXT, status TEXT, subject TEXT, doc TEXT, started_at REAL, updated_at TEXT,
        PRIMARY KEY (kind, job_id))""",
    "CREATE INDEX IF NOT EXISTS pm_jobs_user ON pm_jobs (username, updated_at)",
    """CREATE TABLE IF NOT EXISTS pm_ledger_facts (
        fact_id TEXT PRIMARY KEY, subject TEXT, subject_key TEXT, metric TEXT, value REAL, unit TEXT, source TEXT,
        window_start TEXT, window_end TEXT, as_of TEXT, product TEXT, doc TEXT)""",
    "CREATE INDEX IF NOT EXISTS pm_ledger_subject ON pm_ledger_facts (subject_key)",
    # the key surface (2026-10-14): one row per API call; the rate limit,
    # the daily cap, idempotency replay and the usage endpoint all read it
    """CREATE TABLE IF NOT EXISTS pm_api_calls (
        call_id TEXT PRIMARY KEY, ts TEXT NOT NULL, day TEXT NOT NULL, key_id TEXT NOT NULL, owner TEXT, endpoint TEXT,
        kind TEXT, status INTEGER, ms INTEGER, trace_id TEXT, request_id TEXT, idempotency_key TEXT, conversation_id TEXT,
        charged_usd REAL, response TEXT)""",
    "CREATE INDEX IF NOT EXISTS pm_api_calls_key_ts ON pm_api_calls (key_id, ts)",
    "CREATE INDEX IF NOT EXISTS pm_api_calls_idem ON pm_api_calls (key_id, idempotency_key)",
    # metered spend per request id, accumulated by the usage log on every
    # model call (dashboard, API and the build server alike)
    """CREATE TABLE IF NOT EXISTS pm_request_charges (
        request_id TEXT PRIMARY KEY, billed_usd REAL, cost_usd REAL, calls INTEGER, updated_at TEXT)""",
]


def _now_iso():
    import datetime as _dt
    return _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _note(label, exc):
    try:
        from . import faults as _f
        _f.note(label, exc)
    except Exception:
        pass


def backend():
    """'pg', 'sqlite' or None."""
    if os.environ.get('DATABASE_URL', '').startswith(('postgres://', 'postgresql://')):
        return 'pg'
    if os.environ.get('PM_STORE_SQLITE'):
        return 'sqlite'
    return None


def enabled():
    return backend() is not None


def _connect():
    global _conn, _kind
    with _lock:
        if _conn is not None:
            return _conn
        b = backend()
        if b == 'pg':
            url = os.environ['DATABASE_URL']
            try:
                import psycopg2
                _conn = psycopg2.connect(url)
                _conn.autocommit = True
            except ImportError:
                import psycopg
                _conn = psycopg.connect(url, autocommit=True)
            _kind = 'pg'
        elif b == 'sqlite':
            import sqlite3
            _conn = sqlite3.connect(os.environ['PM_STORE_SQLITE'], check_same_thread=False)
            _conn.isolation_level = None
            _kind = 'sqlite'
        return _conn


def _q(sql):
    """Parameter style: %s for Postgres, ? for SQLite."""
    return sql if _kind == 'pg' else sql.replace('%s', '?')


def _exec(sql, params=()):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(_q(sql), params)
    return cur


def _upsert(table, keys, cols, values):
    """INSERT ... ON CONFLICT (keys) DO UPDATE for both backends."""
    names = ', '.join(cols)
    ph = ', '.join(['%s'] * len(cols))
    sets = ', '.join(f"{c} = EXCLUDED.{c}" for c in cols if c not in keys)
    sql = f"INSERT INTO {table} ({names}) VALUES ({ph}) ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {sets}"
    _exec(sql, tuple(values))


def migrate():
    global _migrated
    if not enabled():
        return False
    try:
        for stmt in DDL:
            _exec(stmt)
        _migrated = True
        return True
    except Exception as e:
        _note('store.migrate', e)
        return False


def _ensure():
    if not enabled():
        return False
    if not _migrated:
        return migrate()
    return True


# ------------------------------------------------------------- writers
def record_ask(rec):
    """Shadow-write one ask-log record."""
    try:
        if not _ensure():
            return False
        ts = str(rec.get('ts') or '')
        rid = f"{ts}:{rec.get('user')}:{abs(hash(str(rec.get('question') or '')[:200])) % 10**9}"
        extra = rec.get('extra') if isinstance(rec.get('extra'), dict) else {}
        _upsert('pm_ask_log', ('id',),
                ('id', 'ts', 'day', 'username', 'view', 'surface', 'route', 'outcome', 'ms', 'subject', 'question', 'reply', 'trace_id', 'extra'),
                (rid, ts, ts[:10], str(rec.get('user') or ''), str(rec.get('view') or ''), str(rec.get('surface') or ''), str(rec.get('route') or ''),
                 str(rec.get('outcome') or ''), int(rec.get('ms') or 0), str(rec.get('subject') or ''), str(rec.get('question') or '')[:2000],
                 str(rec.get('reply') or '')[:6000], str(extra.get('trace_id') or ''), json.dumps(extra, ensure_ascii=False)[:20000]))
        return True
    except Exception as e:
        _note('store.record_ask', e)
        return False


def upsert_thread(username, tid, turns, title=''):
    try:
        if not _ensure():
            return False
        turns = list(turns or [])
        _upsert('pm_threads', ('username', 'tid'), ('username', 'tid', 'title', 'turns', 'n_turns', 'updated_at'),
                (str(username or ''), str(tid or ''), str(title or '')[:200], json.dumps(turns, ensure_ascii=False), len(turns),
                 time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))
        return True
    except Exception as e:
        _note('store.upsert_thread', e)
        return False


def upsert_job(kind, doc):
    try:
        if not _ensure() or not isinstance(doc, dict):
            return False
        _upsert('pm_jobs', ('kind', 'job_id'), ('kind', 'job_id', 'username', 'status', 'subject', 'doc', 'started_at', 'updated_at'),
                (str(kind or ''), str(doc.get('job_id') or ''), str(doc.get('user') or ''), str(doc.get('status') or ''),
                 str(doc.get('subject') or doc.get('angle') or doc.get('question') or '')[:300], json.dumps(doc, default=str, ensure_ascii=False)[:200000],
                 float(doc.get('started_at') or 0) or None, time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))
        return True
    except Exception as e:
        _note('store.upsert_job', e)
        return False


def upsert_fact(subject, subject_key, metric, value, unit='', source='', window=None, as_of='', product='', doc=None):
    try:
        if not _ensure():
            return False
        w = window if isinstance(window, dict) else {}
        fid = f"{subject_key}|{product}|{metric}|{w.get('start', '')}|{w.get('end', '')}|{as_of}"
        _upsert('pm_ledger_facts', ('fact_id',), ('fact_id', 'subject', 'subject_key', 'metric', 'value', 'unit', 'source', 'window_start', 'window_end', 'as_of', 'product', 'doc'),
                (fid[:300], str(subject or '')[:200], str(subject_key or '')[:200], str(metric or '')[:200], float(value) if value is not None else None,
                 str(unit or '')[:40], str(source or '')[:40], str(w.get('start') or ''), str(w.get('end') or ''), str(as_of or ''), str(product or '')[:40],
                 json.dumps(doc or {}, default=str, ensure_ascii=False)[:20000]))
        return True
    except Exception as e:
        _note('store.upsert_fact', e)
        return False


# ------------------------------------------------------------- readers
def _rows(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def asks(day=None, username=None, limit=5000):
    try:
        if not _ensure():
            return []
        where, params = [], []
        if day:
            where.append('day = %s')
            params.append(day)
        if username:
            where.append('username = %s')
            params.append(username)
        sql = 'SELECT * FROM pm_ask_log' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY ts LIMIT %s'
        params.append(int(limit))
        out = _rows(_exec(sql, tuple(params)))
        for r in out:
            try:
                r['extra'] = json.loads(r.get('extra') or '{}')
            except Exception:
                r['extra'] = {}
        return out
    except Exception as e:
        _note('store.asks', e)
        return []


def thread(username, tid):
    try:
        if not _ensure():
            return None
        rows = _rows(_exec('SELECT * FROM pm_threads WHERE username = %s AND tid = %s', (username, tid)))
        if not rows:
            return None
        r = rows[0]
        r['turns'] = json.loads(r.get('turns') or '[]')
        return r
    except Exception as e:
        _note('store.thread', e)
        return None


def jobs(username=None, kinds=None, status=None, limit=200):
    try:
        if not _ensure():
            return []
        where, params = [], []
        if username:
            where.append('username = %s')
            params.append(username)
        if kinds:
            where.append('kind IN (' + ', '.join(['%s'] * len(kinds)) + ')')
            params.extend(list(kinds))
        if status:
            where.append('status = %s')
            params.append(status)
        sql = 'SELECT * FROM pm_jobs' + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY updated_at DESC LIMIT %s'
        params.append(int(limit))
        out = _rows(_exec(sql, tuple(params)))
        for r in out:
            try:
                r['doc'] = json.loads(r.get('doc') or '{}')
            except Exception:
                r['doc'] = {}
        return out
    except Exception as e:
        _note('store.jobs', e)
        return []


def facts(subject_key, limit=200):
    try:
        if not _ensure():
            return []
        return _rows(_exec('SELECT * FROM pm_ledger_facts WHERE subject_key = %s ORDER BY as_of DESC LIMIT %s', (subject_key, int(limit))))
    except Exception as e:
        _note('store.facts', e)
        return []


def counts():
    """Row counts per table, for the ops page and the backfill."""
    out = {}
    try:
        if not _ensure():
            return out
        for t in ('pm_ask_log', 'pm_threads', 'pm_jobs', 'pm_ledger_facts'):
            out[t] = _exec(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
    except Exception as e:
        _note('store.counts', e)
    return out


def reset_for_tests():
    global _conn, _kind, _migrated
    with _lock:
        try:
            if _conn is not None:
                _conn.close()
        except Exception:
            pass
        _conn, _kind, _migrated = None, None, False


# ---------------------------------------------------------------- key surface
def add_request_charge(request_id, billed_usd, cost_usd=0.0):
    """Accumulate one model call's metered charge under its request id."""
    if not enabled() or not request_id:
        return False
    try:
        _ensure()
        if _kind == 'pg':
            _exec("INSERT INTO pm_request_charges (request_id, billed_usd, cost_usd, calls, updated_at) VALUES (%s, %s, %s, 1, %s) "
                  "ON CONFLICT (request_id) DO UPDATE SET billed_usd = pm_request_charges.billed_usd + EXCLUDED.billed_usd, "
                  "cost_usd = pm_request_charges.cost_usd + EXCLUDED.cost_usd, calls = pm_request_charges.calls + 1, updated_at = EXCLUDED.updated_at",
                  (str(request_id), float(billed_usd or 0.0), float(cost_usd or 0.0), _now_iso()))
        else:
            _exec("INSERT INTO pm_request_charges (request_id, billed_usd, cost_usd, calls, updated_at) VALUES (%s, %s, %s, 1, %s) "
                  "ON CONFLICT (request_id) DO UPDATE SET billed_usd = billed_usd + excluded.billed_usd, "
                  "cost_usd = cost_usd + excluded.cost_usd, calls = calls + 1, updated_at = excluded.updated_at",
                  (str(request_id), float(billed_usd or 0.0), float(cost_usd or 0.0), _now_iso()))
        return True
    except Exception as e:
        _note('store.add_request_charge', e)
        return False


def request_charge(request_id):
    """(billed_usd, cost_usd, calls) for a request id, zeros when unknown."""
    if not enabled() or not request_id:
        return 0.0, 0.0, 0
    try:
        _ensure()
        cur = _exec("SELECT billed_usd, cost_usd, calls FROM pm_request_charges WHERE request_id = %s", (str(request_id),))
        row = cur.fetchone()
        return (float(row[0] or 0), float(row[1] or 0), int(row[2] or 0)) if row else (0.0, 0.0, 0)
    except Exception as e:
        _note('store.request_charge', e)
        return 0.0, 0.0, 0


def record_api_call(row):
    """One API call row (dict with the pm_api_calls columns)."""
    if not enabled():
        return False
    try:
        _ensure()
        cols = ['call_id', 'ts', 'day', 'key_id', 'owner', 'endpoint', 'kind', 'status', 'ms', 'trace_id', 'request_id',
                'idempotency_key', 'conversation_id', 'charged_usd', 'response']
        vals = [row.get(c) for c in cols]
        if isinstance(vals[-1], (dict, list)):
            vals[-1] = json.dumps(vals[-1], default=str)
        _upsert('pm_api_calls', ['call_id'], cols, vals)
        return True
    except Exception as e:
        _note('store.record_api_call', e)
        return False


def api_calls_since(key_id, endpoint, since_iso):
    if not enabled():
        return None
    try:
        _ensure()
        cur = _exec("SELECT COUNT(*) FROM pm_api_calls WHERE key_id = %s AND endpoint = %s AND ts >= %s", (key_id, endpoint, since_iso))
        return int(cur.fetchone()[0])
    except Exception as e:
        _note('store.api_calls_since', e)
        return None


def api_spent_on(key_id, day):
    if not enabled():
        return None
    try:
        _ensure()
        cur = _exec("SELECT COALESCE(SUM(charged_usd), 0) FROM pm_api_calls WHERE key_id = %s AND day = %s", (key_id, day))
        return float(cur.fetchone()[0] or 0.0)
    except Exception as e:
        _note('store.api_spent_on', e)
        return None


def api_idempotent_response(key_id, idem_key, since_iso):
    if not enabled() or not idem_key:
        return None
    try:
        _ensure()
        cur = _exec("SELECT response, status FROM pm_api_calls WHERE key_id = %s AND idempotency_key = %s AND ts >= %s AND response IS NOT NULL "
                    "ORDER BY ts DESC LIMIT 1", (key_id, idem_key, since_iso))
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        return json.loads(row[0]), int(row[1] or 200)
    except Exception as e:
        _note('store.api_idempotent_response', e)
        return None


def api_usage(key_id, day_from, day_to):
    """Per-day calls and charges for a key between two days inclusive."""
    if not enabled():
        return None
    try:
        _ensure()
        cur = _exec("SELECT day, kind, COUNT(*), COALESCE(SUM(charged_usd), 0), COALESCE(AVG(ms), 0), "
                    "SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) FROM pm_api_calls "
                    "WHERE key_id = %s AND day >= %s AND day <= %s GROUP BY day, kind ORDER BY day", (key_id, day_from, day_to))
        return [{'day': r[0], 'kind': r[1], 'calls': int(r[2]), 'charged_usd': float(r[3] or 0), 'avg_ms': int(r[4] or 0), 'errors': int(r[5] or 0)}
                for r in cur.fetchall()]
    except Exception as e:
        _note('store.api_usage', e)
        return None


def api_key_summary(key_id, days=30):
    """Calls, spend, error and pending rates for the admin key view and the scorecard."""
    if not enabled():
        return None
    try:
        _ensure()
        import datetime as _dt
        since = (_dt.datetime.utcnow() - _dt.timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
        cur = _exec("SELECT COUNT(*), COALESCE(SUM(charged_usd), 0), SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN kind = 'pending' THEN 1 ELSE 0 END), MAX(ts) FROM pm_api_calls WHERE key_id = %s AND ts >= %s", (key_id, since))
        r = cur.fetchone()
        return {'calls': int(r[0] or 0), 'charged_usd': float(r[1] or 0), 'errors': int(r[2] or 0), 'pending': int(r[3] or 0), 'last_call': r[4]}
    except Exception as e:
        _note('store.api_key_summary', e)
        return None


def api_latency(key_id, days=7):
    """p50 / p90 latency in ms by kind over the window."""
    if not enabled():
        return None
    try:
        _ensure()
        import datetime as _dt
        since = (_dt.datetime.utcnow() - _dt.timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
        cur = _exec("SELECT kind, ms FROM pm_api_calls WHERE key_id = %s AND ts >= %s AND ms IS NOT NULL", (key_id, since))
        by = {}
        for kind, ms in cur.fetchall():
            by.setdefault(kind or 'answer', []).append(int(ms))
        out = {}
        for kind, arr in by.items():
            arr.sort()
            out[kind] = {'n': len(arr), 'p50': arr[len(arr) // 2], 'p90': arr[min(len(arr) - 1, int(len(arr) * 0.9))]}
        return out
    except Exception as e:
        _note('store.api_latency', e)
        return None
