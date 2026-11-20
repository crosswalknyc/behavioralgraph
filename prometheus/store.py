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

The document mirror (rebuild stage 6, 2026-11-20). The records Prometheus
writes most and reads most (a reader's chat threads and their index, the
generated-read job documents, the insights ledger index) are mirrored
into ``pm_docs`` byte for byte, keyed by their S3 key, at the moment they
are written to S3 (``doc_put``, from one seam: ``install_mirror`` hooks
the S3 client at bind, and boto3's default session for every client made
after it, so every writer mirrors the same bytes after S3 takes them and
none knows the mirror exists). S3 stays the system of record and is
never deleted or moved; the mirror is write-through. Reading from the
mirror is a second switch, ``PM_STORE_READ=1`` (``read_enabled``), under
which ``_pm_s3_json`` returns the mirrored document first and falls back
to S3 on a miss. The ledger index keeps its S3 ETag compare-and-set, so
its read stays on S3; the mirror of it serves the morning review and
the ops page. Parity: scripts/pm_store_parity.py replays the corpus and
compares every mirrored key against what landed in S3;
``production_parity`` does the same against the live bucket from inside
a running process (the ops route in prometheus.store_ops).
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
        charged_usd REAL, response TEXT, question_hash TEXT)""",
    "CREATE INDEX IF NOT EXISTS pm_api_calls_key_ts ON pm_api_calls (key_id, ts)",
    "CREATE INDEX IF NOT EXISTS pm_api_calls_idem ON pm_api_calls (key_id, idempotency_key)",
    "CREATE INDEX IF NOT EXISTS pm_api_calls_q ON pm_api_calls (key_id, question_hash, ts)",
    # metered spend per request id, accumulated by the usage log on every
    # model call (dashboard, API and the build server alike)
    """CREATE TABLE IF NOT EXISTS pm_request_charges (
        request_id TEXT PRIMARY KEY, billed_usd REAL, cost_usd REAL, calls INTEGER, updated_at TEXT)""",
    # the document mirror (stage 6): one row per mirrored S3 document, the
    # body exactly as S3 holds it
    """CREATE TABLE IF NOT EXISTS pm_docs (
        key TEXT PRIMARY KEY, family TEXT NOT NULL, body TEXT NOT NULL, nbytes INTEGER, sha TEXT, etag TEXT, updated_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS pm_docs_family ON pm_docs (family, updated_at)",
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


# columns added after a table first shipped (CREATE TABLE IF NOT EXISTS leaves an
# existing table alone, so each is added on its own, errors ignored when present)
DDL_ADD_COLUMNS = [
    ('pm_api_calls', 'question_hash', 'TEXT'),
    ('pm_docs', 'etag', 'TEXT'),       # what S3 answered the put with (2026-11-20)
]


def migrate():
    global _migrated
    if not enabled():
        return False
    try:
        # Tables first, then the columns later releases added, then the
        # indexes: an index on a column an older table does not carry
        # yet failed before the ADD COLUMN ran, so migrate() never
        # finished and every call retried it (229 faults in three days
        # on pm_api_calls.question_hash, 2026-10-30).
        _is_index = lambda st: str(st).lstrip().upper().startswith('CREATE INDEX')  # noqa: E731
        for stmt in DDL:
            if not _is_index(stmt):
                _exec(stmt)
        for table, col, typ in DDL_ADD_COLUMNS:
            try:
                _exec(f"ALTER TABLE {table} ADD COLUMN {'IF NOT EXISTS ' if _kind == 'pg' else ''}{col} {typ}")
            except Exception:
                pass
        for stmt in DDL:
            if _is_index(stmt):
                try:
                    _exec(stmt)
                except Exception as ie:
                    _note('store.migrate_index', ie)   # an index never blocks the store
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
        for t in ('pm_ask_log', 'pm_threads', 'pm_jobs', 'pm_ledger_facts', 'pm_docs'):
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


# ------------------------------------------------------------ document mirror
THREADS_PREFIX = 'system/synth_chat_threads/'
READS_PREFIX = 'system/prometheus_reads/'
LEDGER_INDEX_KEY = 'system/insights_ledger/index.json'   # insights_ledger.INDEX_KEY


def family_of(key):
    """'thread' | 'read' | 'ledger_index' | None: which mirrored family an
    S3 key belongs to. A read job document is the one-segment JSON under
    the reads prefix; the queue, chain and in-flight markers under it are
    transient and are not mirrored."""
    k = str(key or '')
    if k.startswith(THREADS_PREFIX) and k.endswith('.json'):
        return 'thread'
    if k == LEDGER_INDEX_KEY:
        return 'ledger_index'
    if k.startswith(READS_PREFIX) and k.endswith('.json') and '/' not in k[len(READS_PREFIX):]:
        return 'read'
    return None


def mirrors(key):
    return family_of(key) is not None


def read_enabled():
    """The second switch: read mirrored documents before S3."""
    return enabled() and os.environ.get('PM_STORE_READ', '') == '1'


# counters for the ops page and the parity harness: how often the mirror
# was written, how often a read came from it, how often it fell through
STATS = {'doc_put': 0, 'doc_hit': 0, 'doc_miss': 0}


def doc_put(key, body, etag=None):
    """Mirror one S3 document, the body exactly as written to S3 (bytes or
    str) and, when the caller has it, the ETag S3 answered the put with.
    True when stored; False when the store is off, the key is not a
    mirrored family, or the write failed (counted, never raised)."""
    fam = family_of(key)
    if not fam or not enabled():
        return False
    try:
        if not _ensure():
            return False
        import hashlib
        text = body.decode('utf-8') if isinstance(body, (bytes, bytearray)) else str(body)
        _upsert('pm_docs', ('key',), ('key', 'family', 'body', 'nbytes', 'sha', 'etag', 'updated_at'),
                (str(key), fam, text, len(text.encode('utf-8')), hashlib.sha1(text.encode('utf-8')).hexdigest(),
                 (str(etag).strip('"') if etag else None), _now_iso()))
        STATS['doc_put'] += 1
        return True
    except Exception as e:
        _note('store.doc_put', e)
        return False


def doc_text(key):
    """The mirrored body as text, or None."""
    if not enabled() or not mirrors(key):
        return None
    try:
        if not _ensure():
            return None
        row = _exec('SELECT body FROM pm_docs WHERE key = %s', (str(key),)).fetchone()
        STATS['doc_hit' if row else 'doc_miss'] += 1
        return row[0] if row else None
    except Exception as e:
        _note('store.doc_text', e)
        return None


def doc_get(key, default=None):
    """The mirrored document parsed, or ``default`` on a miss."""
    text = doc_text(key)
    if text is None:
        return default
    try:
        return json.loads(text)
    except Exception as e:
        _note('store.doc_get', e)
        return default


def doc_keys(family=None, limit=100000):
    """The mirrored keys, newest first."""
    if not enabled():
        return []
    try:
        if not _ensure():
            return []
        if family:
            cur = _exec('SELECT key FROM pm_docs WHERE family = %s ORDER BY updated_at DESC LIMIT %s', (family, int(limit)))
        else:
            cur = _exec('SELECT key FROM pm_docs ORDER BY updated_at DESC LIMIT %s', (int(limit),))
        return [r[0] for r in cur.fetchall()]
    except Exception as e:
        _note('store.doc_keys', e)
        return []


def docs_count(family=None):
    if not enabled():
        return 0
    try:
        if not _ensure():
            return 0
        if family:
            return int(_exec('SELECT COUNT(*) FROM pm_docs WHERE family = %s', (family,)).fetchone()[0])
        return int(_exec('SELECT COUNT(*) FROM pm_docs').fetchone()[0])
    except Exception as e:
        _note('store.docs_count', e)
        return 0


# ------------------------------------------------- the one write seam
# Every writer of a mirrored document (the shared helper, the ETag-guarded
# thread merge, the read status write, the read worker's claim, the
# sweeper, the drilldown job write, the ledger index compare-and-set)
# puts through the S3 client. The mirror hooks the client once, at bind,
# and mirrors after every successful put of a mirrored key: nothing is
# mirrored that S3 refused, nothing a writer does can bypass it, and no
# writer needs to know the mirror exists. Reads are the explicit seams
# (_pm_s3_json, the read status route, service.find_job_doc) under the
# read switch, with S3 as the fallback.
_MARK = '_pm_store_mirror_wrapped'
_CTX = '_pm_store_mirror'


def _provide_params(params, context=None, **_kw):
    try:
        if isinstance(context, dict) and isinstance(params, dict):
            key = params.get('Key')
            if key and mirrors(key):
                body = params.get('Body')
                if isinstance(body, (bytes, bytearray, str)):
                    context[_CTX] = (str(key), body)
    except Exception:
        pass


def _after_call(http_response=None, parsed=None, model=None, context=None, **_kw):
    try:
        if not isinstance(context, dict):
            return
        hit = context.pop(_CTX, None)
        if not hit:
            return
        status = getattr(http_response, 'status_code', 200)
        if status is not None and int(status) >= 300:
            return
        etag = parsed.get('ETag') if isinstance(parsed, dict) else None
        doc_put(hit[0], hit[1], etag=etag)
    except Exception as e:
        _note('store.mirror_after_call', e)


def _register_events(events, own=False):
    tag = 'own-' if own else ''
    reg = events.register_first if own else events.register
    reg('provide-client-params.s3.PutObject', _provide_params, unique_id=f'pm-store-mirror-{tag}params')
    reg('after-call.s3.PutObject', _after_call, unique_id=f'pm-store-mirror-{tag}after')


def _wrap_plain(client):
    """A store client that is not botocore's (the in-memory store the
    tests and the replay use): the mirror on its put_object."""
    fn = getattr(client, 'put_object', None)
    if not callable(fn) or getattr(fn, _MARK, False):
        return

    def inner(*a, **k):
        out = fn(*a, **k)
        try:
            key, body = k.get('Key'), k.get('Body')
            if key and mirrors(key) and isinstance(body, (bytes, bytearray, str)):
                doc_put(key, body, etag=(out.get('ETag') if isinstance(out, dict) else None))
        except Exception as e:
            _note('store.mirror_plain', e)
        return out
    setattr(inner, _MARK, True)
    try:
        setattr(client, 'put_object', inner)
    except Exception:
        pass


def install_mirror(client=None):
    """Put the mirror on ``client`` and on every S3 client made after
    this call. Safe to call more than once. Returns the client."""
    try:
        import boto3
        _register_events(boto3._get_default_session().events)
    except Exception:
        pass
    if client is None:
        return client
    try:
        events = getattr(getattr(client, 'meta', None), 'events', None)
        if events is not None and hasattr(events, 'register'):
            _register_events(events, own=True)
        else:
            _wrap_plain(client)
    except Exception as e:
        _note('store.install_mirror', e)
    return client


def parity(objects):
    """Compare the mirror against ``objects`` ({key: bytes} as S3 holds
    them). Returns {'checked', 'same', 'different', 'only_s3',
    'only_store', 'by_family': {...}} over the mirrored families; the
    keys of every difference ride under 'different_keys' (first 50)."""
    out = {'checked': 0, 'same': 0, 'different': 0, 'only_s3': 0, 'only_store': 0, 'by_family': {}, 'different_keys': []}
    mirrored = {k: v for k, v in (objects or {}).items() if mirrors(k)}
    seen = set()
    for k, body in mirrored.items():
        fam = family_of(k)
        f = out['by_family'].setdefault(fam, {'checked': 0, 'same': 0, 'different': 0, 'only_s3': 0})
        text = body.decode('utf-8') if isinstance(body, (bytes, bytearray)) else str(body)
        have = doc_text(k)
        out['checked'] += 1
        f['checked'] += 1
        seen.add(k)
        if have is None:
            out['only_s3'] += 1
            f['only_s3'] += 1
            if len(out['different_keys']) < 50:
                out['different_keys'].append(('only_s3', k))
        elif have == text:
            out['same'] += 1
            f['same'] += 1
        else:
            out['different'] += 1
            f['different'] += 1
            if len(out['different_keys']) < 50:
                out['different_keys'].append(('different', k))
    for k in doc_keys():
        if k not in seen:
            out['only_store'] += 1
    return out


def _as_dt(v):
    """An aware UTC datetime from what S3 or the store hands back (a
    datetime, or an ISO string), or None."""
    import datetime as _dt
    if v is None:
        return None
    if isinstance(v, _dt.datetime):
        return v if v.tzinfo else v.replace(tzinfo=_dt.timezone.utc)
    t = str(v).strip()
    if not t:
        return None
    try:
        d = _dt.datetime.fromisoformat(t.replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)
    except Exception:
        return None


def _list_prefix(s3, bucket, prefix):
    """Every object under ``prefix`` as (key, LastModified), one page at
    a time. Read-only."""
    out = []
    token = None
    while True:
        kw = {'Bucket': bucket, 'Prefix': prefix}
        if token:
            kw['ContinuationToken'] = token
        resp = s3.list_objects_v2(**kw) or {}
        for o in resp.get('Contents') or []:
            out.append((o.get('Key'), o.get('LastModified')))
        token = resp.get('NextContinuationToken') if resp.get('IsTruncated') else None
        if not token:
            break
    return out


def production_parity(s3, bucket, since=None, limit=2000):
    """The mirror against what S3 holds, from inside a running process
    (the live check behind the ops route; stage 6 in production, Jenna
    2026-11-20). Read-only on both sides. Lists the three mirrored
    families in S3, takes every document S3 changed since the mirror's
    first write (or ``since``, an ISO time), reads it, and compares its
    bytes with the mirrored row. A document the mirror holds that S3 no
    longer lists is 'only_store'; one S3 changed that the mirror never
    saw is 'only_s3' (a writer outside this process). Counts and keys
    only; never a body."""
    import datetime as _dt
    import hashlib
    import time as _time
    t0 = _time.time()
    out = {'enabled': enabled(), 'rows': 0, 'since': None, 'listed': 0, 'older': 0, 'checked': 0,
           'same': 0, 'different': 0, 'only_s3': 0, 'only_store': 0, 'truncated': False,
           'by_family': {}, 'examples': {'different': [], 'only_s3': [], 'only_store': []}}
    if not enabled():
        return out
    try:
        if not _ensure():
            return out
        rows = {r['key']: r for r in _rows(_exec('SELECT key, family, sha, nbytes, etag, updated_at FROM pm_docs'))}
    except Exception as e:
        _note('store.production_parity', e)
        return out
    out['rows'] = len(rows)
    first = None
    for r in rows.values():
        d = _as_dt(r.get('updated_at'))
        if d is not None and (first is None or d < first):
            first = d
    since_dt = _as_dt(since) if since else (first - _dt.timedelta(seconds=90) if first else None)
    out['since'] = since_dt.strftime('%Y-%m-%dT%H:%M:%SZ') if since_dt else None
    listed = []
    for prefix in (THREADS_PREFIX, READS_PREFIX, LEDGER_INDEX_KEY):
        try:
            listed.extend((k, lm) for k, lm in _list_prefix(s3, bucket, prefix) if mirrors(k))
        except Exception as e:
            _note('store.production_parity', e)
    seen = set()
    gets = 0
    for key, lm in listed:
        out['listed'] += 1
        seen.add(key)
        fam = family_of(key)
        f = out['by_family'].setdefault(fam, {'listed': 0, 'older': 0, 'checked': 0, 'same': 0, 'different': 0, 'only_s3': 0, 'only_store': 0})
        f['listed'] += 1
        lmd = _as_dt(lm)
        if since_dt is None or (lmd is not None and lmd < since_dt):
            out['older'] += 1
            f['older'] += 1
            continue
        row = rows.get(key)
        if row is None:
            out['only_s3'] += 1
            f['only_s3'] += 1
            if len(out['examples']['only_s3']) < 20:
                out['examples']['only_s3'].append({'key': key, 's3_modified': lmd.strftime('%Y-%m-%dT%H:%M:%SZ') if lmd else None})
            continue
        if gets >= int(limit):
            out['truncated'] = True
            continue
        gets += 1
        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            body = obj['Body'].read()
        except Exception as e:
            _note('store.production_parity', e)
            continue
        text = body.decode('utf-8') if isinstance(body, (bytes, bytearray)) else str(body)
        sha = hashlib.sha1(text.encode('utf-8')).hexdigest()
        out['checked'] += 1
        f['checked'] += 1
        if sha == str(row.get('sha') or ''):
            out['same'] += 1
            f['same'] += 1
        else:
            out['different'] += 1
            f['different'] += 1
            if len(out['examples']['different']) < 20:
                out['examples']['different'].append({
                    'key': key,
                    's3_bytes': len(body), 's3_modified': lmd.strftime('%Y-%m-%dT%H:%M:%SZ') if lmd else None,
                    's3_etag': str((obj or {}).get('ETag') or '').strip('"') or None,
                    'store_bytes': row.get('nbytes'), 'store_updated': row.get('updated_at'),
                    'store_etag': row.get('etag')})
    for key, row in rows.items():
        if key not in seen:
            out['only_store'] += 1
            f = out['by_family'].setdefault(row.get('family'), {'listed': 0, 'older': 0, 'checked': 0, 'same': 0, 'different': 0, 'only_s3': 0, 'only_store': 0})
            f['only_store'] += 1
            if len(out['examples']['only_store']) < 20:
                out['examples']['only_store'].append({'key': key, 'store_updated': row.get('updated_at')})
    out['elapsed_s'] = round(_time.time() - t0, 1)
    return out


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
                'idempotency_key', 'conversation_id', 'charged_usd', 'response', 'question_hash']
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


def api_repeat_count(key_id, qhash, since_iso):
    if not enabled():
        return None
    try:
        _ensure()
        cur = _exec("SELECT COUNT(*) FROM pm_api_calls WHERE key_id = %s AND question_hash = %s AND endpoint = 'ask' AND ts >= %s",
                    (key_id, qhash, since_iso))
        return int(cur.fetchone()[0])
    except Exception as e:
        _note('store.api_repeat_count', e)
        return None


def api_repeat_response(key_id, qhash, since_iso):
    if not enabled() or not qhash:
        return None
    try:
        _ensure()
        cur = _exec("SELECT response, status FROM pm_api_calls WHERE key_id = %s AND question_hash = %s AND ts >= %s AND response IS NOT NULL "
                    "ORDER BY ts DESC LIMIT 1", (key_id, qhash, since_iso))
        row = cur.fetchone()
        if not row or not row[0]:
            return None
        return json.loads(row[0]), int(row[1] or 200)
    except Exception as e:
        _note('store.api_repeat_response', e)
        return None


def api_spent_since(key_id, since_iso):
    if not enabled():
        return None
    try:
        _ensure()
        cur = _exec("SELECT COALESCE(SUM(charged_usd), 0) FROM pm_api_calls WHERE key_id = %s AND ts >= %s", (key_id, since_iso))
        return float(cur.fetchone()[0] or 0.0)
    except Exception as e:
        _note('store.api_spent_since', e)
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
