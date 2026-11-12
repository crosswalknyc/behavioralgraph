"""The server posts the finished line into the thread that launched the
build (Jenna 2026-11-10, the day audit: Sony's build and six of Grace
Yin's finished with no line in their chats).

Until now "this chat confirms the moment it finishes" was kept by the
widget: an open tab polled the active runs and drew "X is ready". With
no browser open nothing was written, and the reader came back to a chat
that still said building.

The rule: when an order is started, the launch is recorded with the run
id and the thread it was asked in (``note_launch``). A sweep in the web
process (the read-recovery loop calls ``sweep``; no browser involved)
reads the open launches, asks the build engine which runs finished, and
posts one turn into the launching thread: the name it carries in the
Select Profile dropdown and a chip to open it (``post_ready``).

  * Idempotent. The turn carries the run id (``meta.build_run_id``) and
    a fixed turn id (``ready-<run id>``); a thread that already holds it
    is left alone, two processes that post it at once merge to one turn,
    and the launch record is closed once it is posted.
  * The launching thread, never the active one. A key-surface thread is
    listed again when the index lost it; a dashboard thread is listed
    again under its own id. A launch with no thread (a key ask with no
    conversation that started a build) posts nothing: there is no chat
    to write into, and the build's own ready notice still goes out.
  * A failed, refused or cancelled run has its progress line replaced
    by its real outcome, in the client's words, in the launching thread
    (2026-11-11, prometheus.promise_truth.settle_launch); the launch is
    then closed. The calm promise is never written here.
  * The artifact first. A line is posted only for a run the engine
    reports complete.

Store: one small document per launch under ``system/pm_build_launches/``.
Never raises into a request or the sweep. Regression:
scripts/test_pm_conversation_plumbing.py.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

__all__ = ['PREFIX', 'MAX_AGE_S', 'note_launch', 'ready_text', 'ready_turn', 'post_ready', 'sweep', 'pending']

PREFIX = 'system/pm_build_launches/'
CLOSED_PREFIX = 'system/pm_build_launches_closed/'    # a closed launch leaves the open prefix, so a pass reads only what is open
MAX_AGE_S = 72 * 3600           # a launch older than this is closed unposted
_DONE = ('complete',)
_DEAD = ('failed', 'error', 'cancelled', 'canceled', 'refunded')


def _iso(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def _safe(run_id):
    return re.sub(r"[^A-Za-z0-9_.-]", '', str(run_id or ''))[:80]


def _key(run_id):
    return f"{PREFIX}{_safe(run_id)}.json"


def note_launch(store_put, *, run_id, user, thread_id='', subject='', decision='', via='dashboard', engine_user='', now=None):
    """Record a started order. ``store_put(key, doc)`` writes it. ``user``
    owns the thread; ``engine_user`` is the identity the build engine
    lists the run under (the same when not given)."""
    try:
        rid = _safe(run_id)
        if not rid or not user:
            return None
        doc = {'run_id': rid, 'user': str(user), 'thread_id': str(thread_id or '')[:96],
               'subject': str(subject or '')[:200], 'decision': str(decision or '')[:40],
               'via': str(via or 'dashboard')[:20], 'engine_user': str(engine_user or user)[:200],
               'started_at': float(now or time.time()), 'status': 'open'}
        store_put(_key(rid), doc)
        return doc
    except Exception:
        return None


def ready_text(name, run_doc=None):
    """The one line. Plain, no internal words."""
    name = ' '.join(str(name or '').split()) or 'Your profile'
    doc = run_doc if isinstance(run_doc, dict) else {}
    if doc.get('subiq_s3_key'):
        return f"{name} is ready. The Subscriber IQ read is live in the Subscriber IQ tab."
    return f"{name} is ready. It is in the Select Profile dropdown under {name}."


def ready_turn(run_id, name, run_doc=None, now=None):
    doc = run_doc if isinstance(run_doc, dict) else {}
    name = ' '.join(str(name or '').split()) or 'Your profile'
    meta = {'kind': 'build_ready', 'build_run_id': _safe(run_id), 'server': True, 'landed_at': _iso(now),
            'subject': name[:120],
            'options': [{'label': f"Open {name}"[:80], 'send': f"Open {name}"[:200]}]}
    for k in ('tu_key', 'subiq_s3_key'):
        if doc.get(k):
            meta['profile_key' if k == 'tu_key' else 'subiq_key'] = str(doc[k])[:300]
    return {'role': 'agent', 'text': ready_text(name, doc), 'ts': _iso(now), 'turn_id': f"ready-{_safe(run_id)}", 'meta': meta}


_WIDGET_READY_RX = re.compile(r"\bis finished and ready in the Select Profile dropdown\b|"
                              r"\bis finished\. Your Subscriber IQ read is live\b", re.I)


def _has_ready(history, run_id, name=''):
    """True when the thread already says this run finished: the server's
    own turn, or the line an open tab drew for the same run or name."""
    rid = _safe(run_id)
    low = ' '.join(str(name or '').lower().split())
    for t in history or []:
        if not isinstance(t, dict):
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        if meta.get('build_run_id') == rid or t.get('turn_id') == f"ready-{rid}":
            return True
        if str(t.get('role') or '') == 'user':
            continue
        text = ' '.join(str(t.get('text') or '').split())
        if _WIDGET_READY_RX.search(text) and (
                (meta.get('run_id') == rid) or (low and text.lower().startswith(low))):
            return True
    return False


def post_ready(launch, run_doc, *, exists, load, save, ensure=None, relist=None, now=None):
    """Post the finished line for one launch. Returns 'posted',
    'already', 'no_thread' or 'failed'.

    ``exists(user, tid)``, ``load(user, tid) -> turns``, ``save(user,
    tid, turns)`` (a merging save), ``ensure`` / ``relist`` list a key
    or a dashboard thread the index lost."""
    try:
        user = str((launch or {}).get('user') or '')
        tid = str((launch or {}).get('thread_id') or '')
        rid = _safe((launch or {}).get('run_id'))
        if not (user and rid):
            return 'failed'
        if not tid:
            return 'no_thread'
        if not exists(user, tid):
            if tid.startswith('api-') and callable(ensure):
                ensure(user, tid, title='API conversation', via='api')
            elif callable(relist):
                relist(user, tid)
            else:
                return 'no_thread'
        history = list(load(user, tid) or [])
        name = str((run_doc or {}).get('subject') or (launch or {}).get('subject') or '').strip()
        if _has_ready(history, rid, name):
            return 'already'
        save(user, tid, history + [ready_turn(rid, name, run_doc, now)])
        return 'posted'
    except Exception:
        return 'failed'


def pending(store_list, store_get, now=None):
    """The open launches, oldest first: [(key, doc)]."""
    out = []
    try:
        for key in store_list(PREFIX) or []:
            if not str(key).endswith('.json'):
                continue
            try:
                doc = store_get(key)
            except Exception:
                doc = None
            if isinstance(doc, dict) and doc.get('status') == 'open':
                out.append((key, doc))
    except Exception:
        pass
    out.sort(key=lambda kv: float(kv[1].get('started_at') or 0))
    return out


def sweep(*, store_list, store_get, store_put, runs_for, exists, load, save, ensure=None, relist=None, now=None, log=print,
          store_delete=None):
    """One pass: post the finished line for every open launch whose run
    the engine reports complete. ``runs_for(user) -> [run docs]`` (the
    engine's list for that user). Returns [(run_id, outcome)]."""
    now = float(now or time.time())
    out = []
    try:
        opened = pending(store_list, store_get, now)
        if not opened:
            return out
        by_user = {}
        for key, doc in opened:
            rid, user = _safe(doc.get('run_id')), str(doc.get('engine_user') or doc.get('user') or '')
            age = now - float(doc.get('started_at') or now)
            if user not in by_user:
                try:
                    by_user[user] = {str(r.get('run_id') or ''): r for r in (runs_for(user) or []) if isinstance(r, dict)}
                except Exception:
                    by_user[user] = {}
            run = by_user[user].get(rid)
            st = str((run or {}).get('status') or '').strip().lower()
            outcome = None
            if run and st in _DONE:
                outcome = post_ready(doc, run, exists=exists, load=load, save=save, ensure=ensure, relist=relist, now=now)
                if outcome == 'failed':
                    out.append((rid, 'retry'))
                    continue                       # the next pass tries again
            elif run and st in _DEAD:
                # the outcome replaces the progress line in the launching thread (2026-11-11, prometheus.promise_truth)
                try:
                    from . import promise_truth as _pt
                    how = _pt.settle_launch(doc, run, exists=exists, load=load, save=save, ensure=ensure, relist=relist, now=now)
                except Exception:
                    how = 'failed'
                if how == 'failed' and age < 6 * 3600:
                    out.append((rid, 'retry'))
                    continue                       # the next pass tries again
                outcome = 'closed_' + st + ('' if how in ('no_thread', 'failed', 'alive') else '_' + how)
            elif age > MAX_AGE_S:
                outcome = 'expired'
            if outcome:
                closed = dict(doc, status='closed', outcome=outcome, closed_at=now)
                try:
                    if callable(store_delete):
                        store_put(CLOSED_PREFIX + str(key).rsplit('/', 1)[-1], closed)
                        store_delete(key)
                    else:
                        store_put(key, closed)
                except Exception:
                    pass
                out.append((rid, outcome))
        if out and callable(log):
            log(f"[pm-build-ready] sweep: {out}")
    except Exception as e:                           # noqa: BLE001
        if callable(log):
            log(f"[pm-build-ready] sweep failed: {e}")
    return out


def sweep_with_host(host, service, now=None):
    """The sweep on the bound host (the read-recovery loop calls this)."""
    try:   # the same pass clears what a probe left more than an hour ago (prometheus.probe_files)
        from . import probe_files as _pf
        _pf.sweep_with_host(host, now=now)
    except Exception:
        pass
    try:   # ... and builds a deck that was waiting on a quote nobody answered (prometheus.ride_along)
        from . import ride_along as _ra
        _ra.sweep_with_host(host, now=now)
    except Exception:
        pass
    try:
        if not (host.has('s3_client') and host.has('bucket') and host.bucket and host.has('user_runs')):
            return []
        s3, bucket = host.s3_client, host.bucket

        def _list(prefix):
            keys, token = [], None
            while True:
                kw = dict(Bucket=bucket, Prefix=prefix)
                if token:
                    kw['ContinuationToken'] = token
                r = s3.list_objects_v2(**kw)
                keys += [str(o.get('Key') or '') for o in (r.get('Contents') or [])]
                if not r.get('IsTruncated'):
                    return keys
                token = r.get('NextContinuationToken')

        def _get(key):
            return json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8'))

        def _put(key, doc):
            s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(doc).encode('utf-8'), ContentType='application/json')
        def _delete(key):
            s3.delete_object(Bucket=bucket, Key=key)
        return sweep(store_list=_list, store_get=_get, store_put=_put, store_delete=_delete,
                     runs_for=lambda user: host.user_runs({'username': user, 'email': user}, limit=60),
                     exists=service.thread_exists, load=service.load_thread, save=service.save_thread,
                     ensure=service.ensure_thread, relist=service.relist_thread, now=now)
    except Exception as e:                           # noqa: BLE001
        print(f"[pm-build-ready] host sweep failed: {e}")
        return []
