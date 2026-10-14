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
]


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
