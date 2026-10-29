"""Digital Twins IQ jobs on the read worker (2026-10-26).

Two engine kinds ride the same queue every other engine job uses
(``read_recovery.start_engine_job``; the worker is
migration/prometheus_read_worker.py): ``twin_build`` builds a twin,
``twin_run`` runs one scenario on an existing twin. Both resolve to this
module (``_MODULE_KINDS``); ``run_job`` dispatches on ``inputs['op']``.

Status documents live under ``system/digital_twins/jobs/<id>.json`` and
are what the dashboard polls and the Prometheus status lane reads.
Done means the twin is in the library and the requester can open it:
the index is refreshed, the requester's list is extended when it is an
explicit list, the thread gets a note, and the delivered record is
written with email OFF (the chat note and the status are the notice;
no completion email for twin builds).
"""
from __future__ import annotations

import json
import time
import traceback

from .host import host

JOB_PREFIX = 'system/digital_twins/jobs/'
KINDS = ('twin_build', 'twin_run')
LABEL = {'twin_build': 'twin', 'twin_run': 'twin run'}


def _s3():
    return host.s3_client, (host.bucket if host.has('bucket') else 'dashboard-inputs')


def status_write(job_id, rec):
    s3, bucket = _s3()
    rec = dict(rec)
    rec.setdefault('job_id', job_id)
    rec['updated_at'] = time.time()
    rec.setdefault('product', 'digital_twins_iq')
    s3.put_object(Bucket=bucket, Key=f"{JOB_PREFIX}{job_id}.json", Body=json.dumps(rec, default=str).encode('utf-8'), ContentType='application/json')
    return rec


def status_read(job_id):
    s3, bucket = _s3()
    try:
        return json.loads(s3.get_object(Bucket=bucket, Key=f"{JOB_PREFIX}{job_id}.json")['Body'].read())
    except Exception:
        return None


def build_promise(subject):
    # plain words only: 'corpus' is internal vocabulary (2026-10-26)
    return (f"On it. Building the {subject} twin now: what we already know about audiences like this first, then the outside record, then the twin itself with its first two runs."
            f" It lands on the Twins tab of Digital Twins IQ in a few minutes; I will confirm here when it is ready.")


def run_promise(kind, name):
    from migration.digital_twin_engine import KIND_LABEL
    return f"On it. Running {KIND_LABEL.get(kind, kind)} on the {name} twin now; it lands on the twin's Runs list in a minute or two, and I will confirm here."


def start(kind, job_id, username, inputs, thread_id='', extras=None, start_fn=None):
    """Queue a build or a run. Returns the status record."""
    from . import read_recovery as _rr
    inputs = dict(inputs or {})
    inputs['thread_id'] = thread_id or inputs.get('thread_id') or ''
    inputs.setdefault('op', 'build' if kind == 'twin_build' else 'run')
    starter = start_fn or _rr.start_engine_job
    rec = starter(kind, job_id, username, inputs, extras or {}, status_write, run_job)
    try:
        host.ask_hint(route='digital_twin', outcome='queued', subject=str(inputs.get('subject') or inputs.get('key') or ''))
    except Exception:
        pass
    return rec


# ------------------------------------------------------------------ run
def _claude():
    try:
        return host.claude_data if host.has('claude_data') else None
    except Exception:
        return None


def _tools():
    try:
        import prometheus_analysis as _pma
        return [_pma.WEB_SEARCH_TOOL]
    except Exception:
        return None


def _thread_note(username, thread_id, text, meta):
    try:
        if host.has('load_thread') and host.has('save_thread'):
            tid = thread_id or None
            turns = list(host.load_thread(username, tid) or [])
            turns.append({'role': 'agent', 'text': text, 'ts': time.time(), 'meta': {'kind': 'answer', 'surface': 'analyze', **(meta or {})}})
            host.save_thread(username, tid, turns)
    except Exception as e:
        print(f"[twin-job] thread note skipped: {e}")


def _grant(username, key):
    """The requester always sees their own twin: an explicit list gets
    the key; a seat without the product gets it switched on."""
    try:
        import sys as _sys
        app = _sys.modules.get('app')
        if app is None or not hasattr(app, '_users_cas_mutate'):
            return

        def mut(data):
            u = (data.get('users') or {}).get(username)
            if not u:
                return None
            changed = False
            cur = u.get('allowed_digital_twin_runs')
            if isinstance(cur, list) and '*' not in cur and key not in cur:
                u['allowed_digital_twin_runs'] = cur + [key]
                changed = True
            if not u.get('has_digital_twins_iq_access'):
                u['has_digital_twins_iq_access'] = True
                changed = True
            return data if changed else None
        app._users_cas_mutate(mut)
    except Exception as e:
        print(f"[twin-job] access grant skipped: {e}")


def run_job(job_id, username, inputs, extras=None):
    inputs = dict(inputs or {})
    op = str(inputs.get('op') or 'build')
    rec = status_read(job_id) or {'job_id': job_id, 'user': username, 'inputs': inputs}
    rec.update({'status': 'running', 'queued': False, 'op': op, 'started_run_at': time.time(),
                'stage': 'reading what we already hold' if op == 'build' else 'running it forward on the twin'})
    status_write(job_id, rec)
    s3, bucket = _s3()
    cj = _claude()

    def _cj(system, user_prompt, **kw):
        kw.setdefault('usage_extras', extras)
        return cj(system, user_prompt, **kw)
    claude_json = _cj if cj is not None else None
    try:
        from migration import digital_twin_synthesis as SYN
        from migration import digital_twins as DT
        if op == 'build':
            rec['stage'] = 'building the twin'
            status_write(job_id, rec)

            def _progress(msg):
                rec['stage'] = str(msg or 'pulling the audience first')
                status_write(job_id, rec)

            def _pull(subject, window):
                rec['stage'] = 'pulling the audience first'
                status_write(job_id, rec)
                return SYN.queue_profile_for_twin(subject, window, s3=s3, created_by=username or 'prometheus', progress=_progress)

            inputs.setdefault('pull_profile', _pull)
            payload = SYN.build_twin(inputs, claude_json, tools=_tools(), created_by=username or 'prometheus', s3=s3)
            if str((status_read(job_id) or {}).get('status') or '') == 'cancelled':
                print(f"[twin-job {job_id}] cancelled while building; nothing persisted")
                return
            key = SYN.persist(s3, payload, username or 'prometheus', job_id)
            _grant(username, key)
            m = payload['meta']
            from migration.digital_twin_engine import human_people
            ready = f"Your twin is ready: {m.get('name')} is live on the Twins tab of Digital Twins IQ, {human_people(m.get('size_us'))} people in the US. Ask it anything here, or open it."
            rec.update({'status': 'done', 'success': True, 'stage': 'done', 'finished_at': time.time(), 's3_key': key, 'subject': m.get('name'),
                        'size_us': m.get('size_us'), 'ready_message': ready})
            status_write(job_id, rec)
            _thread_note(username, inputs.get('thread_id'), ready, {'twin_key': key, 'twin_job_id': job_id, 'open_view': 'digitalTwinsIQ',
                                                                       'options': [{'label': 'Open in Digital Twins IQ', 'view': 'digitalTwinsIQ', 'twin_key': key}]})
            try:
                from .delivered import on_done
                on_done('twin', username=username, subject=str(m.get('name') or ''), s3_key=key, payload=payload, host=host, s3_client=s3, job_id=job_id,
                        ready_message=ready, email=False)
            except Exception as e:
                print(f"[twin-job {job_id}] delivered record skipped: {e}")
            print(f"[twin-job {job_id}] built -> {key}")
            return rec
        # a run on an existing twin
        key = str(inputs.get('key') or '')
        payload = DT.load_twin(s3, key)
        if not payload:
            raise RuntimeError('twin not found for the run')
        other = DT.load_twin(s3, str(inputs.get('other_key') or '')) if inputs.get('other_key') else None
        scenario = inputs.get('scenario') if isinstance(inputs.get('scenario'), dict) else {'kind': 'content', 'inputs': {}}
        run = SYN.run_scenario(payload, scenario, claude_json=claude_json, created_by=username or 'prometheus', other_twin=other)
        if str((status_read(job_id) or {}).get('status') or '') == 'cancelled':
            return
        SYN.append_run(s3, key, payload, run)
        o = run.get('outputs') or {}
        from migration.digital_twin_engine import KIND_LABEL
        head = (run.get('read') or {}).get('tldr') or [run.get('recommendation') or '']
        ready = f"Your {KIND_LABEL.get(run.get('kind'), 'run')} on the {payload['meta'].get('name')} twin is in. {head[0]} {run.get('recommendation') or ''}".strip()
        rec.update({'status': 'done', 'success': True, 'stage': 'done', 'finished_at': time.time(), 's3_key': key, 'run_id': run.get('id'), 'kind': run.get('kind'),
                    'subject': payload['meta'].get('name'), 'expected': o.get('expected'), 'odds_pct': o.get('odds_pct'), 'ready_message': ready})
        status_write(job_id, rec)
        _thread_note(username, inputs.get('thread_id'), ready, {'twin_key': key, 'twin_run_id': run.get('id'), 'twin_job_id': job_id, 'open_view': 'digitalTwinsIQ',
                                                                   'options': [{'label': 'Open in Digital Twins IQ', 'view': 'digitalTwinsIQ', 'twin_key': key, 'run_id': run.get('id')}]})
        print(f"[twin-job {job_id}] run {run.get('kind')} -> {key}")
        return rec
    except Exception as e:
        traceback.print_exc()
        rec.update({'status': 'error', 'stage': 'stopped', 'finished_at': time.time(),
                    'error': 'The twin did not finish. Nothing more is charged; ask again and it runs fresh.', 'detail': str(e)[:300]})
        status_write(job_id, rec)   # the status document and the status lane carry the failure; no alert mail from this product
        raise


__all__ = ['JOB_PREFIX', 'KINDS', 'status_write', 'status_read', 'start', 'run_job', 'build_promise', 'run_promise']
