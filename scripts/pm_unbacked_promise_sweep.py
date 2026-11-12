#!/usr/bin/env python3
"""Read-only sweep: every Prometheus thread that ends on a promise with
nothing behind it (Jenna 2026-11-11, prometheus.promise_truth).

    python3 scripts/pm_unbacked_promise_sweep.py                 # the last 14 days
    python3 scripts/pm_unbacked_promise_sweep.py --days 30 --json out.json
    python3 scripts/pm_unbacked_promise_sweep.py --user jessie

For each reader's thread updated inside the window it reads how the
thread ends (``promise_truth.thread_tail``). A thread that ends on a
progress line or a promise is checked against the record the line
points at: the build engine's status for a run, the job document for a
read or a deck, the held records the ops watch reads, a deliverable on
record for the thread. It prints the threads where nothing stands
behind the line and why.

READ ONLY. It lists and reads objects; it never writes to S3, never
posts to a thread, never sends mail and never calls the model. Probe
identities are skipped.

Needs the usual AWS credentials; the engine lookup uses SYNTH_QUEUE_URL
and SYNTH_QUEUE_SECRET when they are set and is skipped otherwise (a
run is then judged by the status its own thread turn last recorded).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import promise_truth as PT          # noqa: E402
from prometheus import probe_identity as PID        # noqa: E402
from prometheus import ride_along as RA             # noqa: E402

BUCKET = 'dashboard-inputs'
THREADS = 'system/synth_chat_threads/'
READS = 'system/prometheus_reads/'
DECKS = 'system/prometheus_decks/'


def _epoch(v):
    try:
        return datetime.fromisoformat(str(v).replace('Z', '+00:00')).timestamp()
    except Exception:
        return 0.0


def _json(s3, key):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _list(s3, prefix, delimiter=None):
    out, token = [], None
    while True:
        kw = dict(Bucket=BUCKET, Prefix=prefix)
        if delimiter:
            kw['Delimiter'] = delimiter
        if token:
            kw['ContinuationToken'] = token
        r = s3.list_objects_v2(**kw)
        if delimiter:
            out += [p['Prefix'] for p in (r.get('CommonPrefixes') or [])]
        else:
            out += [o['Key'] for o in (r.get('Contents') or [])]
        if not r.get('IsTruncated'):
            return out
        token = r.get('NextContinuationToken')


def _held_records(s3, days):
    out = []
    today = datetime.now(timezone.utc).date()
    for d in range(days + 1):
        day = (today - timedelta(days=d)).strftime('%Y-%m-%d')
        for key in _list(s3, f"{PT.HELD_PREFIX}{day}/"):
            doc = _json(s3, key)
            if isinstance(doc, dict):
                out.append(dict(doc, _key=key))
    return out


def _norm(s):
    return ' '.join(str(s or '').lower().split())[:160]


def _held_for(held, user, tail, turn_epoch):
    refs = tail.get('refs') or {}
    for h in held:
        if refs.get('held_ref') and h.get('_key') == refs['held_ref']:
            return h
        if h.get('job_id') and h['job_id'] in refs.values():
            return h
        if str(h.get('user') or '').lower() != str(user).lower():
            continue
        opened = _epoch(h.get('opened_at'))
        if turn_epoch and opened and abs(opened - turn_epoch) <= 20 * 60 and _norm(h.get('question')) and \
                _norm(h.get('question'))[:80] == _norm(tail.get('question'))[:80]:
            return h
    return None


def _engine_status(run_id):
    url, secret = os.environ.get('SYNTH_QUEUE_URL'), os.environ.get('SYNTH_QUEUE_SECRET')
    if not (url and secret and run_id):
        return ''
    try:
        import requests
        r = requests.get(f"{url}/synth/status/{run_id}", timeout=15, headers={'X-Synth-Auth': secret})
        if r.status_code == 404:
            return 'unknown'
        if r.status_code != 200:
            return ''
        return str((r.json() or {}).get('status') or '').lower()
    except Exception:
        return ''


def sweep(s3, days=14, only_user='', engine=True, now=None):
    now = now or time.time()
    since = now - days * 86400
    held = _held_records(s3, days)
    rows, seen = [], 0
    for prefix in _list(s3, THREADS, delimiter='/'):
        user = prefix[len(THREADS):].strip('/')
        if not user or user.startswith('_') or (only_user and user != only_user):
            continue
        if PID.is_probe_user(user, s3=None) or user.lower().startswith('canary') or user in ('anon', 'sandbox_partner'):
            continue
        idx = _json(s3, f"{prefix}index.json") or {}
        for th in idx.get('threads') or []:
            tid = str(th.get('id') or '')
            if not tid or _epoch(th.get('updated')) < since:
                continue
            hist = _json(s3, f"{prefix}{tid}.json")
            if not isinstance(hist, list) or not hist:
                continue
            seen += 1
            tail = PT.thread_tail(hist)
            if tail.get('ends_on') != 'promise':
                continue
            refs = tail.get('refs') or {}
            turn_epoch = _epoch(tail.get('ts'))
            run_status = ''
            rid = refs.get('run_id') or refs.get('build_run_id')
            if rid:
                run_status = (_engine_status(rid) if engine else '') or str(tail.get('status') or '').lower()
            job_status = ''
            for k, pre in (('read_job_id', READS), ('deck_job_id', DECKS)):
                if refs.get(k):
                    doc = _json(s3, f"{pre}{refs[k]}.json")
                    job_status = str((doc or {}).get('status') or ('' if doc else 'missing')).lower()
                    if job_status == 'missing':
                        job_status = ''
                    break
            ra = None
            rec = _json(s3, RA._key(user, tid))
            if isinstance(rec, dict) and rec.get('state') in ('pending', 'awaiting_read', 'started'):
                ra = rec
            verdict, why = PT.classify_tail(tail, run_status=run_status, job_status=job_status,
                                            held=_held_for(held, user, tail, turn_epoch), ride_along=ra,
                                            age_s=(now - turn_epoch) if turn_epoch else None)
            if verdict == 'unbacked' and tid.startswith('api-') and tail.get('kind') == 'read' and job_status in ('done', 'complete'):
                why += ' (a key-surface thread: the answer went back to the caller through the job result)'
            if verdict == 'unbacked':
                rows.append({'user': user, 'thread_id': tid, 'title': str(th.get('title') or '')[:70], 'at': tail.get('ts'),
                             'kind': tail.get('kind'), 'line': ' '.join(str(tail.get('text') or '').split())[:150],
                             'question': ' '.join(str(tail.get('question') or '').split())[:150], 'why': why,
                             'refs': refs, 'run_status': run_status, 'job_status': job_status})
    rows.sort(key=lambda r: str(r.get('at') or ''), reverse=True)
    return rows, seen


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--user', default='')
    ap.add_argument('--no-engine', action='store_true', help='judge a run by its thread turn only')
    ap.add_argument('--json', default='', help='also write the list to this file')
    args = ap.parse_args(argv)
    import boto3
    s3 = boto3.client('s3')
    rows, seen = sweep(s3, days=args.days, only_user=args.user, engine=not args.no_engine)
    print(f"{seen} threads updated in the last {args.days} days; {len(rows)} end on a promise with nothing behind it")
    for r in rows:
        print(f"- {r['user']} / {r['thread_id']} / {r['at']} [{r['kind']}] {r['why']}")
        print(f"    line: {r['line']}")
        if r['question']:
            print(f"    after: {r['question']}")
    if args.json:
        with open(args.json, 'w') as f:
            json.dump({'days': args.days, 'threads_seen': seen, 'rows': rows}, f, indent=1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
