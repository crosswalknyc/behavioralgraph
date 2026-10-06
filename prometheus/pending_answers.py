"""Build-first follow-through, server side (2026-10-06, Jenna: "if
someone asks a question and is then prompted to pull a profile it
should answer the question asked after profiles been pulled").

A question that arrives before its subject has a profile draws the
build-first offer and is stashed (system/pm_pending_questions.json,
per user). Until now the stash was only read back by the widget's
status poll: if the user closed the tab before the run finished, the
question was never answered.

This module answers it from the server. A daemon thread sweeps the
stash every ~75 s; an entry whose subject now has a Total Universe
profile in the library (catalog display name matching on distinctive
tokens, the same rule the pop uses), created after the question was
asked and at least GRACE_S ago (so an open tab's own poll gets first
claim), is popped under CAS (one-shot, so the client path and this
path never both answer) and re-asked as the user, on the user's own
thread, through the normal ask route. The answer lands in the thread
exactly as if the user had typed the question again; the watch email
to Jenna fires as for any real ask. No email goes to the user (the
standing rule: nothing is emailed to a user unless Jenna says so).

Kill switch: PM_PENDING_ANSWERS=0. Never started under
REGRESSION_TEST_MODE. Every step is fail-safe; a sweep that raises
logs and the next sweep runs.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
from datetime import datetime, timezone

STASH_KEY = 'system/pm_pending_questions.json'
INTERVAL_S = 75
GRACE_S = 180          # the open tab's poll claims first
MAX_AGE_S = 7 * 24 * 3600
CATALOG_KEY = 'system/s3_cache.json'

_started = {'thread': None}


def _parse_ts(v):
    """Epoch seconds from an ISO string or a number; 0.0 when unknown."""
    if v in (None, ''):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        s = str(v).strip().replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


def _tokens_default(s):
    return {w for w in re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).split()
            if len(w) > 1}


def subject_matches(entry_subject, display_name, tokens_fn=_tokens_default):
    """The pop's rule: distinctive-token overlap of 2, or at least half
    of the stashed subject's tokens."""
    et = tokens_fn(entry_subject)
    dt = tokens_fn(display_name)
    if not et or not dt:
        return False
    ov = len(et & dt)
    return ov >= 2 or ov * 2 >= len(et)


def catalog_match(entry, jobs, now, tokens_fn=_tokens_default, grace_s=GRACE_S):
    """The Total Universe catalog job that answers this stash entry, or
    None: a display name without a ' - ' cut suffix whose tokens match
    the stashed subject, created after the question was asked and at
    least `grace_s` ago."""
    asked = float(entry.get('ts') or 0)
    best = None
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        name = str(job.get('display_name') or '').strip()
        if not name or ' - ' in name:
            continue
        if not subject_matches(entry.get('subject'), name, tokens_fn):
            continue
        created = max(_parse_ts(job.get('created_at')), _parse_ts(job.get('last_modified')))
        if created < asked - 600:
            continue
        if now - created < grace_s:
            continue
        if best is None or created > best[0]:
            best = (created, job)
    return best[1] if best else None


def due_entries(stash, jobs, now, tokens_fn=_tokens_default, grace_s=GRACE_S):
    """[(username, entry, job)] for every stash entry whose subject now
    has a finished profile."""
    out = []
    if not isinstance(stash, dict):
        return out
    for uname, lst in stash.items():
        if not isinstance(lst, list):
            continue
        for e in lst:
            if not isinstance(e, dict) or not str(e.get('question') or '').strip():
                continue
            if now - float(e.get('ts') or 0) > MAX_AGE_S:
                continue
            job = catalog_match(e, jobs, now, tokens_fn, grace_s)
            if job is not None:
                out.append((str(uname), e, job))
    return out


def pop_entry(cas_update, bucket, uname, question):
    """Remove exactly this entry under CAS and return it, or None when
    another path (the widget's poll) already took it."""
    got = {'e': None}

    def _mut(doc):
        doc = doc if isinstance(doc, dict) else {}
        lst = [x for x in (doc.get(uname) or []) if isinstance(x, dict)]
        keep = []
        for x in lst:
            if got['e'] is None and str(x.get('question') or '') == str(question):
                got['e'] = x
                continue
            keep.append(x)
        if keep:
            doc[uname] = keep
        else:
            doc.pop(uname, None)
        return doc
    try:
        cas_update(bucket, STASH_KEY, _mut, default=dict,
                   log_name='pm_pending_questions')
    except Exception:
        traceback.print_exc()
        return None
    return got['e']


def ask_as_user(app, users_doc, uname, entry):
    """Re-ask the stashed question as the user through the normal ask
    route, on the thread the question came from (else the active
    thread). Returns (http_status, kind, read_job_id)."""
    rec = {}
    try:
        users = (users_doc or {}).get('users') if isinstance(users_doc, dict) else None
        rec = (users or users_doc or {}).get(uname) or {}
    except Exception:
        rec = {}
    body = {'text': str(entry.get('question') or ''),
            'client': 'dashboard', 'history': []}
    tid = str(entry.get('thread_id') or '').strip()
    if tid:
        body['thread_id'] = tid
    with app.test_client() as c:
        with c.session_transaction() as s:
            s['username'] = uname
            if rec.get('role'):
                s['role'] = rec.get('role')
        r = c.post('/api/prometheus/v1/ask', json=body,
                   headers={'X-Prometheus-Trace': 'pending-answer'})
        env = {}
        try:
            env = r.get_json() or {}
        except Exception:
            env = {}
        job = env.get('job') or {}
        rj = (job.get('id') if isinstance(job, dict) else None) \
            or (env.get('raw') or {}).get('read_job_id')
        return r.status_code, env.get('kind'), rj


def sweep_once(app, s3_client, bucket, cas_update, load_users,
               tokens_fn=_tokens_default, now=None, grace_s=GRACE_S):
    """One pass. Returns a list of {user, subject, question, status}."""
    now = now or time.time()
    try:
        stash = json.loads(s3_client.get_object(
            Bucket=bucket, Key=STASH_KEY)['Body'].read().decode('utf-8'))
    except Exception:
        return []
    if not any(isinstance(v, list) and v for v in (stash or {}).values()):
        return []
    try:
        jobs = (json.loads(s3_client.get_object(
            Bucket=bucket, Key=CATALOG_KEY)['Body'].read().decode('utf-8'))
            or {}).get('jobs') or []
    except Exception:
        return []
    results = []
    for uname, entry, job in due_entries(stash, jobs, now, tokens_fn, grace_s):
        took = pop_entry(cas_update, bucket, uname, entry.get('question'))
        if not took:
            continue
        rec = {'user': uname, 'subject': entry.get('subject'),
               'question': str(entry.get('question') or '')[:120],
               'profile': job.get('display_name')}
        try:
            users_doc = load_users() if callable(load_users) else {}
        except Exception:
            users_doc = {}
        try:
            code, kind, rj = ask_as_user(app, users_doc, uname, took)
            rec.update(status=code, kind=kind, read_job_id=rj)
        except Exception as e:
            traceback.print_exc()
            rec.update(status='error', error=str(e)[:200])
        print(f"[pm-pending-answers] {json.dumps(rec, default=str)}")
        results.append(rec)
    return results


def _loop(app, host):
    while True:
        try:
            sweep_once(app, host.s3_client, host.S3_BUCKET,
                       host._s3_json_cas_update, getattr(host, 'load_users', None))
        except Exception:
            traceback.print_exc()
        time.sleep(INTERVAL_S)


def start(app, host):
    """Start the sweeper once per process. Returns the thread or None."""
    if os.environ.get('REGRESSION_TEST_MODE') or os.environ.get('PM_PENDING_ANSWERS', '1') == '0':
        return None
    if _started['thread'] is not None:
        return _started['thread']
    for attr in ('s3_client', 'S3_BUCKET', '_s3_json_cas_update'):
        if not hasattr(host, attr):
            print(f"[pm-pending-answers] host lacks {attr}; not started")
            return None
    t = threading.Thread(target=_loop, args=(app, host), daemon=True,
                         name='pm-pending-answers')
    t.start()
    _started['thread'] = t
    print('[pm-pending-answers] sweeper started')
    return t
