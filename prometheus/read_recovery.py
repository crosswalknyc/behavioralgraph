"""Orphaned read recovery (2026-10-08).

A generated read runs on a daemon thread inside the web process. A
deploy or a restart mid-read leaves the job at status 'working' for
ever: the user was told "the read will land right here", nothing
lands, a paid custom read is never refunded, and a re-ask of the same
question attaches to the dead job ("Already on it"). Seen 2026-10-08:
Jenna's Walsh Family Book Series read (job 537a9c2a7f92) died at
18:00 UTC under a deploy, charged and never delivered; Jordan's Office
thread carried two "Working on it" turns the same way.

Jenna, same day: "we need it to be smarter overall ... respond
appropriately."

How it works:
- Every launch persists a resume record on the job (question, recent
  history, bound subject, the charge, probe flag, page).
- The job heartbeats once a minute while it runs.
- A sweeper (boot + every 5 minutes) and the poll endpoint pick up any
  'working' job with no heartbeat for RECOVER_AFTER seconds, claim it
  with a conditional put (one instance wins), and re-run it as a fresh
  job that the old job id follows (`resumed_as`). The charge rides
  along and is never taken twice.
- A job that dies twice finalizes: refund, a calm line in the thread,
  one ops alert (jenna + jessie).

Pure plumbing; the chat module is imported lazily so this file never
creates an import cycle. Never raises into a request or a job.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import traceback
import uuid

RECOVER_AFTER = 240          # seconds without a heartbeat
HEARTBEAT_EVERY = 60
SWEEP_EVERY = 300
LOOKBACK = 6 * 3600          # a dead job's record stops changing at death
MAX_RESUMES = 2
STALE_STAGE = 'picking the read back up'
FINAL_NOTE = ('That read did not finish on my side, so nothing is charged '
              'for it. Ask it again and I will run it fresh.')

_INSTANCE = f"{socket.gethostname()}:{os.getpid()}"
_started = False
_lock = threading.Lock()


def _chat():
    from prometheus.legacy import chat as _c
    return _c


def _host():
    from prometheus.legacy import H as _h
    return _h


def _s3():
    h = _host()
    return h.s3_client, h.S3_BUCKET


def _prefix():
    return _chat()._PM_READ_PREFIX


def _trim_history(history, n=10, width=2000):
    out = []
    for h in list(history or [])[-n:]:
        if not isinstance(h, dict):
            continue
        row = {}
        for k in ('role', 'content', 'reply', 'text', 'read_job_id'):
            v = h.get(k)
            if isinstance(v, str):
                row[k] = v[:width]
            elif v is not None and k in ('role', 'read_job_id'):
                row[k] = str(v)[:80]
        if row:
            out.append(row)
    return out


def resume_record(*, text, history, base, panel_charge=None, probe=False,
                  switch_page=None, bind_cohort=None):
    """Everything a fresh instance needs to run this read again."""
    base = base if isinstance(base, dict) else {}
    rec = {
        'text': str(text or '')[:1200],
        'history': _trim_history(history),
        'bind_subject': str(base.get('subject') or '')[:200],
        'bind_cohort': str(bind_cohort or '')[:120] or None,
        'base_key': str(base.get('s3_key') or '')[:300],
        'base_source': str(base.get('source') or '')[:40],
        'panel_charge': panel_charge if isinstance(panel_charge, dict) else None,
        'probe': bool(probe),
        'switch_page': str(switch_page or '')[:200] or None,
    }
    try:
        json.dumps(rec)
    except Exception:
        rec['history'] = []
        rec['panel_charge'] = None
    return rec


def last_beat(status):
    try:
        return max(float(status.get('heartbeat_at') or 0),
                   float(status.get('stage_at') or 0),
                   float(status.get('started_at') or 0),
                   float(status.get('claimed_at') or 0))
    except Exception:
        return 0.0


def is_stale(status, now=None, after=RECOVER_AFTER):
    if not isinstance(status, dict):
        return False
    if str(status.get('status') or '') != 'working':
        return False
    if status.get('resumed_as'):
        return False
    beat = last_beat(status)
    if beat <= 0:
        return False
    return (now or time.time()) - beat > after


def carry_resume(job_id, head):
    """The job body's head keeps the resume record (and attempt count)
    the launch wrote, so every stage write preserves it."""
    try:
        s3, bucket = _s3()
        prev = json.loads(s3.get_object(
            Bucket=bucket, Key=f"{_prefix()}{job_id}.json")['Body'].read())
        if isinstance(prev.get('resume'), dict):
            head = dict(head)
            head['resume'] = prev['resume']
            head['resume_attempts'] = prev.get('resume_attempts') or 0
    except Exception:
        pass
    return head


# ----------------------------------------------------------------- beat
def start_heartbeat(job_id, head):
    """A daemon timer that stamps heartbeat_at on the job once a minute
    while it is working. Returns the stop event."""
    stop = threading.Event()

    def _run():
        while not stop.wait(HEARTBEAT_EVERY):
            try:
                s3, bucket = _s3()
                key = f"{_prefix()}{job_id}.json"
                cur = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
                if str(cur.get('status') or '') != 'working' or cur.get('resumed_as'):
                    return
                cur['heartbeat_at'] = time.time()
                _chat()._pm_read_status_write(job_id, cur)
            except Exception:
                return
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        pass
    return stop


# ---------------------------------------------------------------- claim
def _claim(job_id, status):
    """Mark the job as being resumed by this instance. Conditional put
    on the record's ETag so two instances cannot both win."""
    s3, bucket = _s3()
    key = f"{_prefix()}{job_id}.json"
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        etag = str(obj.get('ETag') or '').strip('"')
        cur = json.loads(obj['Body'].read())
    except Exception:
        return None
    if not is_stale(cur):
        return None
    claimed = dict(cur)
    claimed.update({'status': 'working', 'stage': STALE_STAGE,
                    'claimed_by': _INSTANCE, 'claimed_at': time.time(),
                    'resume_attempts': int(cur.get('resume_attempts') or 0) + 1})
    body = json.dumps(claimed).encode('utf-8')
    try:
        kw = dict(Bucket=bucket, Key=key, Body=body, ContentType='application/json')
        if etag:
            kw['IfMatch'] = etag
        s3.put_object(**kw)
    except Exception as e:
        msg = str(e)
        if 'PreconditionFailed' in msg or '412' in msg:
            return None
        if 'IfMatch' in msg or 'Unknown parameter' in msg:
            # boto3 too old for conditional writes: write, then verify
            try:
                s3.put_object(Bucket=bucket, Key=key, Body=body,
                              ContentType='application/json')
                time.sleep(1.0)
                again = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
                if again.get('claimed_by') != _INSTANCE:
                    return None
            except Exception:
                return None
        else:
            return None
    return claimed


# -------------------------------------------------------------- recover
def _finalize(job_id, status, reason):
    """Twice orphaned (or nothing to resume from): refund, a calm line
    in the thread, one ops alert. Never raises."""
    c, h = _chat(), _host()
    uname = str(status.get('user') or '').strip()
    rr = status.get('resume') if isinstance(status.get('resume'), dict) else {}
    charge = rr.get('panel_charge') or status.get('panel_charge')
    try:
        if isinstance(charge, dict):
            c._pm_panel_refund(charge)
    except Exception:
        traceback.print_exc()
    try:
        c._pm_read_status_write(job_id, {
            **{k: v for k, v in status.items() if k != 'resume'},
            'status': 'error', 'final': True, 'final_reason': str(reason)[:200],
            'payload': {'success': True, 'action': 'answer', 'reply': FINAL_NOTE,
                        'followups': [], 'offer_deck': False, 'deck_angle': None}})
    except Exception:
        traceback.print_exc()
    try:
        if uname and not status.get('probe'):
            c._pm_append_read_to_history(uname, job_id, {
                'success': True, 'action': 'answer', 'reply': FINAL_NOTE,
                'followups': []})
    except Exception:
        traceback.print_exc()
    try:
        c._pm_notify_delete(job_id)
    except Exception:
        pass
    try:
        h._chatbot_error_email(
            'brief-chat/read-recovery',
            f"generated read orphaned and not recoverable ({reason}); "
            f"charge refunded, calm note left in the thread",
            user_email=uname or None,
            payload={'job_id': job_id, 'question': str(status.get('question') or '')[:300],
                     'subject': rr.get('bind_subject'), 'attempts': status.get('resume_attempts')})
    except Exception:
        traceback.print_exc()


def recover(job_id, status=None, reason='no heartbeat'):
    """Re-run one orphaned read as a fresh job the old id follows.
    Returns the new job id, 'final' when it was finalized instead, or
    None when nothing was done (not stale, or another instance won)."""
    c, h = _chat(), _host()
    s3, bucket = _s3()
    if status is None:
        try:
            status = json.loads(s3.get_object(
                Bucket=bucket, Key=f"{_prefix()}{job_id}.json")['Body'].read())
        except Exception:
            return None
    if not is_stale(status):
        return None
    claimed = _claim(job_id, status)
    if not claimed:
        return None
    rr = claimed.get('resume') if isinstance(claimed.get('resume'), dict) else None
    uname = str(claimed.get('user') or '').strip()
    attempts = int(claimed.get('resume_attempts') or 1)
    if not rr or not rr.get('text') or not uname:
        _finalize(job_id, claimed, 'no resume record')
        return 'final'
    if attempts > MAX_RESUMES:
        _finalize(job_id, claimed, f'orphaned {attempts} times')
        return 'final'
    try:
        data = h.load_users()
        user = (data.get('users') or {}).get(uname)
    except Exception:
        user = None
    if not isinstance(user, dict):
        _finalize(job_id, claimed, 'requester not found')
        return 'final'
    print(f"[pm-recover] resuming read {job_id} for {uname} (attempt {attempts}, {reason})")
    try:
        headers = {}
        if rr.get('probe'):
            headers['X-Prometheus-Caller'] = 'resume:orphaned-read'
        panel_confirm = None
        if isinstance(rr.get('panel_charge'), dict):
            panel_confirm = {'subject': rr.get('bind_subject') or '',
                             'resume_charge': rr['panel_charge']}
        with h.app.test_request_context('/api/brief-chat/analyze', method='POST',
                                        json={'text': rr['text']}, headers=headers):
            from flask import session as _session
            _session['username'] = uname
            resp = c._pm_generate_metrics_response(
                user, rr['text'], list(rr.get('history') or []),
                prefer_catalog=True,
                bind_subject=rr.get('bind_subject') or None,
                bind_cohort=rr.get('bind_cohort') or None,
                panel_confirm=panel_confirm,
                switch_page=rr.get('switch_page') or None)
        body = resp.get_json() if hasattr(resp, 'get_json') else resp
        body = body if isinstance(body, dict) else {}
        new_id = str(body.get('read_job_id') or '').strip()
        if new_id:
            try:
                # the opt-in email side-file follows the read
                nk = f"{c._PM_NOTIFY_PREFIX}{job_id}.json"
                side = s3.get_object(Bucket=bucket, Key=nk)['Body'].read()
                s3.put_object(Bucket=bucket, Key=f"{c._PM_NOTIFY_PREFIX}{new_id}.json",
                              Body=side, ContentType='application/json')
            except Exception:
                pass
            c._pm_read_status_write(job_id, {
                **{k: v for k, v in claimed.items() if k != 'resume'},
                'status': 'working', 'stage': STALE_STAGE,
                'resumed_as': new_id, 'resumed_at': time.time()})
            return new_id
        # answered synchronously (replay, clarify): that is the result
        ok = bool(body.get('success'))
        c._pm_read_status_write(job_id, {
            **{k: v for k, v in claimed.items() if k != 'resume'},
            'status': 'done' if ok else 'error', 'payload': body})
        if ok and not rr.get('probe'):
            c._pm_append_read_to_history(uname, job_id, body)
        return 'final'
    except Exception as e:
        traceback.print_exc()
        _finalize(job_id, claimed, f'resume failed: {e}'[:160])
        return 'final'


def follow(status, trigger=True):
    """For the poll endpoint: hop to the resumed job's record (up to 3
    hops) and start a recovery when the job is stale. The caller's
    job_id stays on the payload."""
    try:
        if not isinstance(status, dict):
            return status
        s3, bucket = _s3()
        job_id = str(status.get('job_id') or '')
        cur, hops = status, 0
        while str(cur.get('status') or '') == 'working' and cur.get('resumed_as') and hops < 3:
            nxt = json.loads(s3.get_object(
                Bucket=bucket, Key=f"{_prefix()}{cur['resumed_as']}.json")['Body'].read())
            cur = {**nxt, 'job_id': job_id or nxt.get('job_id'), 'resumed_from': cur.get('job_id')}
            hops += 1
        if trigger and is_stale(cur):
            jid = str(cur.get('job_id') or job_id)
            threading.Thread(target=recover, args=(jid,), kwargs={'status': None, 'reason': 'poll'},
                             daemon=True).start()
            cur = {**cur, 'stage': STALE_STAGE}
        cur.pop('resume', None)
        return cur
    except Exception:
        traceback.print_exc()
        return status


# ---------------------------------------------------------------- sweep
def sweep(now=None, lookback=LOOKBACK):
    """Find every working read with no heartbeat and recover it.
    Returns the list of (job_id, result)."""
    now = now or time.time()
    out = []
    try:
        s3, bucket = _s3()
        prefix = _prefix()
        token = None
        keys = []
        while True:
            kw = dict(Bucket=bucket, Prefix=prefix)
            if token:
                kw['ContinuationToken'] = token
            r = s3.list_objects_v2(**kw)
            for o in r.get('Contents') or []:
                k = str(o.get('Key') or '')
                if '/_inflight/' in k or not k.endswith('.json'):
                    continue
                lm = o.get('LastModified')
                try:
                    age = now - lm.timestamp()
                except Exception:
                    age = 0
                if age <= lookback:
                    keys.append(k)
            if not r.get('IsTruncated'):
                break
            token = r.get('NextContinuationToken')
        for k in keys:
            try:
                st = json.loads(s3.get_object(Bucket=bucket, Key=k)['Body'].read())
            except Exception:
                continue
            if is_stale(st, now):
                jid = str(st.get('job_id') or k.rsplit('/', 1)[-1][:-5])
                res = recover(jid, status=st, reason='sweep')
                out.append((jid, res))
    except Exception:
        traceback.print_exc()
    if out:
        print(f"[pm-recover] sweep: {out}")
    return out


def start_background(initial_delay=20, every=SWEEP_EVERY):
    """Boot the sweeper once per process. Off in tests and when
    PM_READ_RECOVERY=0."""
    global _started
    if os.environ.get('PM_READ_RECOVERY', '1') == '0' \
            or os.environ.get('REGRESSION_TEST_MODE'):
        return False
    with _lock:
        if _started:
            return False
        _started = True

    def _loop():
        time.sleep(initial_delay)
        while True:
            try:
                sweep()
            except Exception:
                traceback.print_exc()
            time.sleep(every)
    threading.Thread(target=_loop, daemon=True, name='pm-read-recovery').start()
    return True
