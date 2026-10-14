"""Stale engine jobs never sit forever (2026-10-13 audit).

A journey, valuation, flywheel or attribution job runs on a thread
inside one web process. A deploy, a restart, or a crash with no
except path leaves its status document at 'running' with no owner; the
widget polls it for hours and the reader sees "still running" with no
end. A journey for Jessie sat that way for a full day.

This module makes the state finite:

- `stale_jobs(host)` lists running docs older than STALE_MIN minutes.
- `sweep(host)` restarts each one ONCE from the inputs the running
  doc now carries (the runner is bound on the host), posts a line to
  the reader's thread, and marks the doc 'requeued'. A job that is
  stale a second time is marked 'error' with a plain message, and the
  thread gets the honest line. Wallet refunds for engine jobs are the
  charge-at-approve path's business; the error line names the credit
  so the reader can ask.
- `sweep_on_boot(host)` runs one sweep shortly after the process
  starts, which is exactly when orphaned runners exist.
- The status lane calls `note_for(doc)` so a stale job reads as "did
  not finish in its first hour; restarting" instead of "running".

Everything is fail-safe: no exception leaves this module.
"""
from __future__ import annotations

import json
import threading
import time
import traceback

STALE_MIN = 60
KINDS = {
    # kind: (prefix capability, runner capability, label)
    'journey': ('jiq_prefix', 'run_jiq_job', 'journey'),
    'bpiq': ('bpiq_prefix', 'run_bpiq_job', 'valuation'),
    'flywheel': ('fw_prefix', 'run_fw_job', 'flywheel read'),
    'attribution': ('aiq_prefix', 'run_aiq_job', 'campaign tracker'),
}
_ENGINE_KIND = {'journey': 'jiq', 'bpiq': 'bpiq', 'flywheel': 'fw', 'attribution': 'aiq'}   # sweeper kind -> queue kind
_LANDS = {
    'journey': 'the Digital Journey tab',
    'bpiq': 'the Brand Partnership tab',
    'flywheel': 'the Flywheel IQ page',
    'attribution': 'the Attribution IQ tab',
}


def _age_min(doc, now):
    try:
        started = float(doc.get('started_at') or 0)
    except Exception:
        started = 0.0
    if not started:
        return 0.0
    return (now - started) / 60.0


def _list_docs(host, prefix):
    out = []
    try:
        s3 = host.s3_client
        bucket = host.bucket
        token = None
        while True:
            kw = {'Bucket': bucket, 'Prefix': prefix, 'MaxKeys': 1000}
            if token:
                kw['ContinuationToken'] = token
            resp = s3.list_objects_v2(**kw)
            for o in resp.get('Contents') or []:
                key = o.get('Key') or ''
                if not key.endswith('.json'):
                    continue
                try:
                    body = s3.get_object(Bucket=bucket, Key=key)['Body'].read()
                    doc = json.loads(body.decode('utf-8') or '{}')
                except Exception:
                    continue
                if isinstance(doc, dict):
                    out.append((key, doc))
            if not resp.get('IsTruncated'):
                break
            token = resp.get('NextContinuationToken')
    except Exception as e:
        print(f"[jobs-sweeper] list {prefix} skipped: {e}")
    return out


def stale_jobs(host, now=None, stale_min=STALE_MIN):
    """[(kind, key, doc)] for every running doc older than `stale_min`."""
    now = now or time.time()
    found = []
    for kind, (pcap, _rcap, _label) in KINDS.items():
        if not host.has(pcap):
            continue
        prefix = getattr(host, pcap)
        for key, doc in _list_docs(host, prefix):
            if str(doc.get('status') or '') not in ('running', 'queued'):   # a queued job nobody claimed is stale too (audit item 5)
                continue
            if _age_min(doc, now) >= stale_min:
                found.append((kind, key, doc))
    return found


def note_for(kind, doc, now=None):
    """The status-lane sentence for a job the sweeper owns, or ''."""
    now = now or time.time()
    label = KINDS.get(kind, ('', '', kind))[2]
    st = str(doc.get('status') or '')
    if st == 'requeued':
        return (f"Your {label} did not finish in its first hour, so I restarted it. "
                f"It lands in {_LANDS.get(kind, 'the dashboard')} when it is done.")
    if st in ('running', 'queued') and _age_min(doc, now) >= STALE_MIN:
        return (f"Your {label} has run past the hour it usually takes. "
                f"I am restarting it now; it lands in {_LANDS.get(kind, 'the dashboard')} when it is done.")
    return ''


def _post_thread_line(host, username, job_id, text, kind):
    try:
        if not (host.has('thread_for_job') and host.has('load_thread') and host.has('save_thread')):
            return False
        tid = host.thread_for_job(username, job_id)
        history = host.load_thread(username, tid)
        for turn in history or []:
            meta = turn.get('meta') or {}
            if meta.get('sweeper_job_id') == job_id and meta.get('sweeper_status') == kind:
                return False
        history.append({'role': 'agent', 'text': text, 'ts': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()),
                        'meta': {'sweeper_job_id': job_id, 'sweeper_status': kind}})
        host.save_thread(username, tid, history)
        return True
    except Exception:
        traceback.print_exc()
        return False


def _write(host, key, doc):
    try:
        host.s3_client.put_object(Bucket=host.bucket, Key=key,
                                  Body=json.dumps(doc).encode('utf-8'),
                                  ContentType='application/json')
        return True
    except Exception as e:
        print(f"[jobs-sweeper] write {key} failed: {e}")
        return False


def _queue_on():
    try:
        from . import read_recovery as _rr
        return bool(_rr.queue_enabled())
    except Exception:
        return False


def _max_restarts():
    try:
        from . import read_recovery as _rr
        return int(_rr.MAX_ENGINE_RESTARTS)
    except Exception:
        return 1


def sweep(host, now=None, dry_run=False, stale_min=STALE_MIN, start_thread=True):
    """Restart each stale job once; give up honestly the second time.
    Returns {'requeued': [...], 'errored': [...], 'seen': n}."""
    now = now or time.time()
    out = {'requeued': [], 'errored': [], 'seen': 0}
    try:
        for kind, key, doc in stale_jobs(host, now=now, stale_min=stale_min):
            out['seen'] += 1
            job_id = str(doc.get('job_id') or key.rsplit('/', 1)[-1].replace('.json', ''))
            user = str(doc.get('user') or '')
            label = KINDS[kind][2]
            inputs = doc.get('inputs') if isinstance(doc.get('inputs'), dict) else None
            if not inputs and isinstance(doc.get('resume'), dict) and isinstance(doc['resume'].get('inputs'), dict):
                inputs = doc['resume']['inputs']
            rcap = KINDS[kind][1]
            restarts = int(doc.get('restarts') or 0) + (1 if doc.get('requeued') and not doc.get('restarts') else 0)
            can_rerun = bool(inputs) and host.has(rcap) and restarts < _max_restarts()
            if can_rerun:
                note = f"restarted after {int(_age_min(doc, now))} minutes"
                if not dry_run:
                    rec = None
                    if _queue_on():
                        # the queue, never a web thread: a thread dies with the next deploy or autoscale (2026-10-14)
                        try:
                            from . import read_recovery as _rr
                            rec = _rr.requeue_engine(_ENGINE_KIND.get(kind, kind), job_id, dict(doc, inputs=inputs), reason=note)
                        except Exception:
                            traceback.print_exc()
                            rec = None
                    if rec is None:
                        new = dict(doc, status='requeued', requeued=True, requeued_at=now, restarts=restarts + 1,
                                   requeue_note=note, inputs=inputs)
                        if not _write(host, key, new):
                            continue
                        runner = getattr(host, rcap)
                        if start_thread:
                            threading.Thread(target=runner, args=(job_id, user, inputs, {}), daemon=True).start()
                        else:
                            runner(job_id, user, inputs, {})
                    _post_thread_line(host, user, job_id, note_for(kind, dict(doc, status='requeued'), now), 'requeued')
                out['requeued'].append((kind, job_id, user))
            else:
                msg = (f"Your {label} did not complete. Nothing was lost on your side; "
                       f"ask me to run it again and I will, or ask about the credit for this run.")
                new = dict(doc, status='error', error=msg, ended_at=now,
                           sweeper_note=(f'gave up after {restarts} restart(s)' if restarts else 'no inputs saved to restart from'))
                if not dry_run:
                    if not _write(host, key, new):
                        continue
                    _post_thread_line(host, user, job_id, msg, 'error')
                out['errored'].append((kind, job_id, user))
        if out['seen']:
            print(f"[jobs-sweeper] seen={out['seen']} requeued={len(out['requeued'])} errored={len(out['errored'])}")
    except Exception:
        traceback.print_exc()
    return out


def sweep_on_boot(host, delay_s=45.0):
    """One sweep shortly after start, on a daemon thread."""
    def _go():
        try:
            time.sleep(delay_s)
            sweep(host)
        except Exception:
            traceback.print_exc()
    try:
        threading.Thread(target=_go, daemon=True, name='pm-jobs-sweeper').start()
    except Exception:
        traceback.print_exc()
