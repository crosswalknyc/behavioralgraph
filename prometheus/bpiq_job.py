"""Brand Partnership IQ job helpers: one run per ask (Jenna 2026-10-28).

The valuation job body lives in prometheus/legacy/chat.py
(_pm_run_bpiq_job). These are the pieces it calls around the build:

  * prepare(): the names take their known casing, the run key is
    claimed in the shared registry, and a key another job already owns
    is FOLLOWED (wait for it, point this job at its file) instead of
    built a second time.
  * settle(): the registry closes on the file and the finished
    valuation's note lands in the thread that asked.
  * fail(): the claim is marked failed so the next ask can run.

The chat module is imported lazily so this file never creates an
import cycle. Nothing here raises into the job.
"""
from __future__ import annotations

import json
import time
import traceback

FOLLOW_WAIT_S = 25 * 60     # a duplicate job follows the owner this long
FOLLOW_POLL_S = 15


def _chat():
    from prometheus.legacy import chat as _c
    return _c


def ready_note(username, job_id, payload, bare_key):
    """The finished valuation's note in the thread that asked (the
    worker is another process, so the widget's own poll may be gone).
    Idempotent by job id; skipped when a ready line for the same
    subject already sits in the thread's last turns. Never raises."""
    if not username:
        return False
    try:
        c = _chat()
        subject = str((payload or {}).get('project_name') or '').strip()
        tbv = ((payload or {}).get('valuation') or {}).get('total_brand_value')
        text = (f"Your Brand Partnership Valuation is ready: {subject} is live "
                f"in the Brand Partnership tab now.")
        if tbv:
            text += f" Total brand value: ${float(tbv):,.0f}."
        read_line = str(((payload or {}).get('campaign') or {}).get('read_line') or '').strip()
        if read_line:
            text += ' ' + read_line
        tid = c._pm_thread_for_job(username, job_id)
        history = c._pm_load_thread_or_active(username, tid)
        if c._pm_history_has_job_turn(history, 'bpiq_job_id', job_id):
            return False
        for t in list(history or [])[-8:]:
            try:
                if str(t.get('role') or '') == 'agent' and \
                        'Brand Partnership Valuation is ready' in str(t.get('text') or '') and \
                        subject and subject.lower() in str(t.get('text') or '').lower():
                    return False
            except Exception:
                continue
        history.append({'role': 'agent', 'text': text, 'ts': c._pm_iso_now(),
                        'meta': {'bpiq_job_id': job_id, 'kind': 'bpiq',
                                 'subject': subject[:120], 's3_key': bare_key}})
        c._pm_save_thread_or_active(username, tid, history)
        return True
    except Exception:
        traceback.print_exc()
        return False


def follow_owner(job_id, username, inputs, owner, reg, status_write):
    """This job's key is owned by another run (a second Accept, a
    re-walk, another caller). Wait for it and point this job at the
    same file; never build a second one. Returns the owner's finished
    document, or None when the owner went stale or failed and this job
    should build."""
    from migration.bpiq_synthesis import run_key as _rk, run_is_live as _live
    key = _rk(inputs)
    waited = 0.0
    doc = dict(owner or {})
    subject = f"{inputs.get('qualifier')} x {inputs.get('brand_partner')}"
    while True:
        status = str(doc.get('status') or '')
        if status == 'done' and doc.get('s3_key'):
            status_write(job_id, {
                'job_id': job_id, 'user': username, 'status': 'done',
                'subject': doc.get('project_name') or subject,
                's3_key': doc.get('s3_key'), 'total_brand_value': doc.get('total_brand_value'),
                'duplicate_of': doc.get('job_id'), 'finished_at': time.time()})
            print(f"[bpiq-job {job_id}] same ask as {doc.get('job_id')} ({key}); pointed at its file")
            return doc
        if not _live(doc) or waited >= FOLLOW_WAIT_S:
            return None
        status_write(job_id, {
            'job_id': job_id, 'user': username, 'status': 'running', 'subject': subject,
            'stage': 'the same valuation is already running', 'following': doc.get('job_id'),
            'started_at': time.time(), 'inputs': inputs})
        time.sleep(FOLLOW_POLL_S)
        waited += FOLLOW_POLL_S
        doc = reg.load(key) or {}


def prepare(job_id, username, inputs, extras, host, status_write):
    """Before the build. Returns (inputs, registry, settled): `inputs`
    in their known casing, the registry this job claimed its key in,
    and settled=True when the job was pointed at another run's file
    (nothing to build)."""
    from migration.bpiq_synthesis import (registry_for, claim_run, canonical_inputs,
                                          catalog_lookup, touch_run)
    try:
        inputs = canonical_inputs(inputs, catalog_lookup())
    except Exception as e:
        print(f"[bpiq-job {job_id}] casing lookup skipped: {e}")
    reg = registry_for(host)
    thread_id = str((extras or {}).get('thread_id') or '')
    try:
        owner, mine = claim_run(reg, inputs, job_id, user=username, thread_id=thread_id)
    except Exception as e:
        print(f"[bpiq-job {job_id}] registry claim skipped: {e}")
        owner, mine = None, True
    if not mine and owner:
        done = follow_owner(job_id, username, inputs, owner, reg, status_write)
        if done:
            try:
                bare = str(done.get('s3_key') or '')
                s3 = getattr(host, 's3_client')
                bucket = getattr(host, 'S3_BUCKET', None) or getattr(host, 'bucket', None) or 'dashboard-inputs'
                doc = json.loads(s3.get_object(Bucket=bucket, Key=f"brand-partnership-iq/{bare}")['Body'].read())
                ready_note(username, job_id, doc, bare)
            except Exception:
                pass
            return inputs, reg, True
        # the owner went stale or failed: this job takes the key
        try:
            claim_run(reg, inputs, job_id, user=username, thread_id=thread_id)
        except Exception:
            pass
    try:
        touch_run(reg, inputs, job_id, 'running')
    except Exception:
        pass
    return inputs, reg, False


def settle(reg, inputs, job_id, username, bare_key, payload):
    """After the file is written: the registry closes on it and the
    thread that asked gets its note."""
    try:
        from migration.bpiq_synthesis import finish_run
        finish_run(reg, inputs, job_id, s3_key=bare_key,
                   project_name=(payload or {}).get('project_name'),
                   total_brand_value=((payload or {}).get('valuation') or {}).get('total_brand_value'))
    except Exception as e:
        print(f"[bpiq-job {job_id}] registry finish skipped: {e}")
    ready_note(username, job_id, payload, bare_key)
    return True


def fail(reg, inputs, job_id, err):
    """The claim is marked failed so the next ask with this key runs."""
    try:
        if reg is not None:
            from migration.bpiq_synthesis import fail_run
            fail_run(reg, inputs, job_id, reason=str(err)[:160])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# operator: correct a shipped valuation in place (same key, same file)
# ---------------------------------------------------------------------------

def _s3_of(host):
    s3 = getattr(host, 's3_client')
    bucket = getattr(host, 'S3_BUCKET', None) or getattr(host, 'bucket', None) or 'dashboard-inputs'
    return s3, bucket


def rerun_in_place(run_key, host, claude_json, *, tools=None, reason='', email='auto',
                   material_rel=0.05, hints=None, log=print):
    """Research the valuation again under the SAME run key and write the
    corrected read over the SAME dated file (in-place corrections,
    2026-08-25). A copy of the prior file and of the prior underpinnings
    land under _backups/ first. The registry, the job document, the
    catalog, the ledger and the thread note move with it; a second run
    is never created. When the total moved by more than `material_rel`
    (or email=True) one short updated note goes to the requester from
    Prometheus. Returns a summary dict; never a second file."""
    import json as _json
    import time as _t
    from migration.bpiq_synthesis import (S3Registry, S3Underpinnings, registry_key, underpinnings_key,
                                          synthesize, run_salt)
    s3, bucket = _s3_of(host)
    reg = S3Registry(s3, bucket)
    doc = reg.load(run_key) or {}
    bare = str(doc.get('s3_key') or '')
    inputs = dict(doc.get('inputs') or {})
    job_id = str(doc.get('job_id') or '')
    username = str(doc.get('user') or '')
    if not (bare and inputs and job_id):
        raise RuntimeError(f'no finished run owns key {run_key}')
    if hints:
        inputs['research_hints'] = list(hints)     # verified facts for the research asks; not part of the key
    key = f'brand-partnership-iq/{bare}'
    prior = _json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8'))
    stamp = _t.strftime('%Y%m%dT%H%M%SZ', _t.gmtime())
    s3.put_object(Bucket=bucket, Key=f'brand-partnership-iq/_backups/{bare[:-5]}.pre_rerun_{stamp}.json',
                  Body=_json.dumps(prior, indent=2).encode('utf-8'), ContentType='application/json')
    try:
        up = s3.get_object(Bucket=bucket, Key=underpinnings_key(run_key))['Body'].read()
        s3.put_object(Bucket=bucket, Key=f'system/bpiq_underpinnings/_backups/{run_key}.pre_rerun_{stamp}.json',
                      Body=up, ContentType='application/json')
    except Exception:
        pass
    log(f'[bpiq-rerun {job_id}] backups written ({stamp}); researching again under {run_key}')
    payload = synthesize(inputs, claude_json, tools=tools, created_by=prior.get('created_by') or username or 'prometheus',
                         underpinnings=S3Underpinnings(s3, bucket), job_id=job_id, force_research=True)
    category = payload.pop('_bpiq_category', None)
    payload['created_at'] = prior.get('created_at') or payload.get('created_at')   # the dated file keeps its build time
    payload['corrected_at'] = _t.strftime('%Y-%m-%dT%H:%M:%SZ', _t.gmtime())
    s3.put_object(Bucket=bucket, Key=key, Body=_json.dumps(payload, indent=2).encode('utf-8'),
                  ContentType='application/json')
    new_total = float((payload.get('valuation') or {}).get('total_brand_value') or 0)
    old_total = float((prior.get('valuation') or {}).get('total_brand_value') or 0)
    log(f'[bpiq-rerun {job_id}] {key} rewritten: ${old_total:,.2f} -> ${new_total:,.2f}')
    # registry and job document
    try:
        doc.update({'total_brand_value': new_total, 'updated_at': _t.time(), 'corrected_at': payload['corrected_at'],
                    'project_name': payload.get('project_name') or doc.get('project_name')})
        reg.put(run_key, doc)
    except Exception:
        traceback.print_exc()
    try:
        try:
            _pfx = _chat()._PM_BPIQ_JOB_PREFIX
        except Exception:
            _pfx = 'system/pm_bpiq_jobs/'
        jk = f"{_pfx}{job_id}.json"
        jd = _json.loads(s3.get_object(Bucket=bucket, Key=jk)['Body'].read())
        jd['total_brand_value'] = new_total
        jd['corrected_at'] = payload['corrected_at']
        s3.put_object(Bucket=bucket, Key=jk, Body=_json.dumps(jd).encode('utf-8'), ContentType='application/json')
    except Exception:
        traceback.print_exc()
    # the metadata sidecar keeps the title; refresh the category when it moved
    try:
        meta = host.load_bpiq_metadata()
        if isinstance(meta, dict) and bare in meta and category and meta[bare].get('category') != category:
            meta[bare]['category'] = category
            s3.put_object(Bucket=bucket, Key=host.BPIQ_METADATA_KEY, Body=_json.dumps(meta, indent=2).encode('utf-8'),
                          ContentType='application/json')
    except Exception:
        pass
    # catalog and ledger: the new figures replace the old ones
    try:
        from migration import corpus_catalog as _cc
        _cc.index_bpiq(key, payload, user=username)
    except Exception:
        traceback.print_exc()
    try:
        import insights_ledger as il
        subject = str(payload.get('project_name') or '')
        il.retract(key=il.entry_key(subject, 'brand_partnership'))
        il.retract(key=il.entry_key(subject, 'audience'))
        il.persist(subject=subject, metric_family='brand_partnership',
                   question=f'Brand Partnership IQ read: {subject}', route='delivered_bpiq',
                   metrics=[{'name': 'Total brand value', 'value': new_total, 'unit': 'usd',
                             'definition': 'Brand Partnership IQ valuation'}],
                   reply=f'Brand Partnership IQ read {bare}',
                   provenance=getattr(il, 'PROV_DELIVERED', 'delivered_artifact'), sync=True)
    except Exception:
        traceback.print_exc()
    # the thread note carries the corrected figure
    try:
        c = _chat()
        tid = c._pm_thread_for_job(username, job_id)
        hist = c._pm_load_thread_or_active(username, tid)
        subject = str(payload.get('project_name') or '')
        text = (f"Your Brand Partnership Valuation is ready: {subject} is live in the Brand Partnership tab now. "
                f"Total brand value: ${new_total:,.0f}.")
        rl = str((payload.get('campaign') or {}).get('read_line') or '').strip()
        if rl:
            text += ' ' + rl
        hit = False
        for t in hist or []:
            if (t.get('meta') or {}).get('bpiq_job_id') == job_id:
                t['text'] = text
                t['meta']['corrected_at'] = payload['corrected_at']
                hit = True
        if not hit:
            hist.append({'role': 'agent', 'text': text, 'ts': c._pm_iso_now(),
                         'meta': {'bpiq_job_id': job_id, 'kind': 'bpiq', 'subject': subject[:120], 's3_key': bare}})
        c._pm_save_thread_or_active(username, tid, hist)
    except Exception:
        traceback.print_exc()
    # one updated note from Prometheus when the figure moved materially
    sent = None
    moved = (abs(new_total - old_total) / old_total) if old_total else 1.0
    if email is True or (email == 'auto' and moved > material_rel):
        try:
            from prometheus.delivered import _seat, dashboard_url
            from prometheus.outbound_mail import send_user_email
            from prometheus import bpiq_wizard as _w
            seat, who = _seat(username, host=host, s3_client=s3, bucket=bucket)
            addr = str(seat.get('email') or '').strip()
            if addr and '@' in addr:
                talent = inputs.get('qualifier') or 'the talent'
                campaign = inputs.get('campaign') or inputs.get('brand_partner') or 'the campaign'
                subj = f"{campaign}: valuation with {talent} updated"
                body = (f"Hi {who},\n\n" if who else "Hi,\n\n") + \
                       (f"The {campaign} valuation with {talent} was updated. Total brand value: ${new_total:,.2f}.\n\n"
                        f"Open it: {dashboard_url('bpiq')}\n\nPrometheus\nCrosswalk")
                sent = send_user_email(to=addr, subject=subj, body=body, instructed=True, caller='delivered_bpiq_update')
                log(f"[bpiq-rerun {job_id}] updated note sent: {sent}")
        except Exception:
            traceback.print_exc()
    return {'run_key': run_key, 'job_id': job_id, 's3_key': bare, 'old_total': old_total, 'new_total': new_total,
            'moved_rel': round(moved, 4), 'email': sent, 'reason': reason}
