"""Brand Partnership IQ job helpers: one run per ask (Jenna 2026-10-28).

The valuation job body lives in prometheus/legacy/chat.py
(_pm_run_bpiq_job). These are the pieces it calls around the build:

  * prepare(): the names take their known casing, the run key is
    claimed in the shared registry, and a key another job already owns
    is FOLLOWED (wait for it, point this job at its file) instead of
    built a second time.
  * hand_over(): the valuation deck (the one builder, prometheus.bpiq_deck)
    is built, stored and signed before the job says done, so the widget
    and the thread hand the deck over with the total (2026-11-11).
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


# what a job in this process knows about itself between its steps: what
# the Accept charged (it rides the job's extras) and the deck it built
_JOBS = {}
_JOBS_KEEP = 6


def _job(job_id):
    jid = str(job_id or '')
    if jid not in _JOBS:
        while len(_JOBS) >= _JOBS_KEEP:
            _JOBS.pop(next(iter(_JOBS)))
        _JOBS[jid] = {}
    return _JOBS[jid]


def hand_over(job_id, username, inputs, bare_key, payload, category, host):
    """The valuation deck for a finished valuation: built by the one
    valuation deck builder from the valuation's own file, stored under
    the job and signed. Returns what the finished status document adds
    (the thread line, the link, the deck record); {} when the deck could
    not be built, and the valuation is handed over as it always was.
    Never raises."""
    try:
        from prometheus import bpiq_deck as _bd
        rec = _job(job_id)
        deck = _bd.deliver(job_id, payload, host=host, bare_key=bare_key, category=category)
        rec['deck'] = deck or {}
        if not deck:
            return {}
        return _bd.status_fields(payload, deck, rec.get('charge'))
    except Exception:
        traceback.print_exc()
        return {}


def deck_files(job_id):
    """[(bytes, filename, mime)] of the deck this job built, for the
    ready email; [] when there is none."""
    try:
        from prometheus import bpiq_deck as _bd
        deck = (_JOBS.get(str(job_id or '')) or {}).get('deck') or {}
        if deck.get('bytes'):
            return [(deck['bytes'], deck.get('filename') or 'valuation.pptx', _bd.PPTX_MIME)]
    except Exception:
        traceback.print_exc()
    return []


def ready_note(username, job_id, payload, bare_key):
    """The finished valuation's note in the thread that asked (the
    worker is another process, so the widget's own poll may be gone).
    Idempotent by job id; skipped when a ready line for the same
    subject already sits in the thread's last turns. Never raises."""
    if not username:
        return False
    try:
        c = _chat()
        from prometheus import bpiq_deck as _bd
        subject = str((payload or {}).get('project_name') or '').strip()
        rec = _JOBS.get(str(job_id or '')) or {}
        deck = rec.get('deck') or {}
        # one plain line: the total, to the cent, and the deck; the charge on its own line (2026-11-11)
        text = _bd.note(payload, deck, rec.get('charge'))
        if not deck.get('url'):
            # no deck to hand over: the tab line, with the collection's read and the people the campaign
            # reached, each named for what it is (migration.bpiq_anchors.label_people)
            head, _sep, paid = text.partition('\n')
            read_line = str(((payload or {}).get('campaign') or {}).get('read_line') or '').strip()
            if read_line:
                head += ' ' + read_line
            people_line = str(((payload or {}).get('campaign') or {}).get('people_line') or '').strip()
            if people_line and people_line not in head:
                head += ' ' + people_line
            text = head + (('\n' + paid) if paid else '')
        tid = c._pm_thread_for_job(username, job_id)
        history = c._pm_load_thread_or_active(username, tid)
        if c._pm_history_has_job_turn(history, 'bpiq_job_id', job_id):
            return False
        for t in list(history or [])[-8:]:
            try:
                if str(t.get('role') or '') == 'agent' and \
                        'brand partnership valuation is ready' in str(t.get('text') or '').lower() and \
                        subject and subject.lower() in str(t.get('text') or '').lower():
                    return False
            except Exception:
                continue
        history.append({'role': 'agent', 'text': text, 'ts': c._pm_iso_now(),
                        'meta': _bd.turn_meta(job_id, payload, bare_key, deck)})
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
        _charge = (extras or {}).get('bpiq_charge')
        if isinstance(_charge, dict) and _charge:
            _job(job_id)['charge'] = dict(_charge)       # for the line that hands the valuation over
    except Exception:
        pass
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
                _more = hand_over(job_id, username, inputs, bare, doc, None, host)   # the same deck for the one that followed
                if _more:
                    status_write(job_id, {
                        'job_id': job_id, 'user': username, 'status': 'done', 'subject': doc.get('project_name'),
                        's3_key': bare, 'total_brand_value': (doc.get('valuation') or {}).get('total_brand_value'),
                        'duplicate_of': done.get('job_id'), 'finished_at': time.time(), **_more})
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


def rerun_in_place(run_key, host, claude_json, *, tools=None, reason='', email='defer',
                   material_rel=0.05, hints=None, research=True, send=None, log=print):
    """Research the valuation again under the SAME run key and write the
    corrected read over the SAME dated file (in-place corrections,
    2026-08-25). A copy of the prior file and of the prior underpinnings
    land under _backups/ first. The registry, the job document, the
    catalog, the ledger and the thread note move with it; a second run
    is never created.

    The updated note (2026-10-28, the Beals note went out at an interim
    figure): a correction pass never mails on its own. It marks a note
    pending; `send_corrected_note` sends ONE note from the LIVE document
    after the last pass, and only when the figure the reader last saw
    moved by more than `material_rel`. email='defer' (the default) leaves
    the marker for that call; email=True or 'auto' runs it right after
    this pass (a single-pass correction). Returns a summary dict; never
    a second file."""
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
    # research=False re-derives from the saved underpinnings (an engine
    # correction on the same anchors; nothing is re-drawn)
    payload = synthesize(inputs, claude_json, tools=tools, created_by=prior.get('created_by') or username or 'prometheus',
                         underpinnings=S3Underpinnings(s3, bucket), job_id=job_id, force_research=bool(research))
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
    # the deck is the valuation's: built again over the same file, so the link never opens the old figure
    try:
        _more = hand_over(job_id, username, inputs, bare, payload, category, host)
        if _more:
            jd2 = _json.loads(s3.get_object(Bucket=bucket, Key=jk)['Body'].read())
            jd2.update(_more)
            s3.put_object(Bucket=bucket, Key=jk, Body=_json.dumps(jd2).encode('utf-8'), ContentType='application/json')
    except Exception:
        traceback.print_exc()
    # catalog and ledger: the new figures replace the old ones
    _update_catalog_and_ledger(key, bare, payload, username)
    # the thread note carries the corrected figure
    _update_thread_note(username, job_id, bare, payload)
    # the note is owed; it is sent from the live document, once, after the last pass
    moved = (abs(new_total - old_total) / old_total) if old_total else 1.0
    mark_note_pending(s3, bucket, run_key, prior_total=old_total, reason=reason)
    sent = None
    if email is True or email == 'auto':
        sent = send_corrected_note(run_key, host, send=send, material_rel=material_rel,
                                   force=(email is True), log=log).get('email')
    return {'run_key': run_key, 'job_id': job_id, 's3_key': bare, 'old_total': old_total, 'new_total': new_total,
            'moved_rel': round(moved, 4), 'email': sent, 'note_pending': sent is None, 'reason': reason}


def _update_catalog_and_ledger(key, bare, payload, username):
    """The catalog and the ledger carry the live figures (stubbed in tests)."""
    import traceback as _tb
    new_total = float((payload.get('valuation') or {}).get('total_brand_value') or 0)
    try:
        from migration import corpus_catalog as _cc
        _cc.index_bpiq(key, payload, user=username)
    except Exception:
        _tb.print_exc()
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
        _tb.print_exc()


def _update_thread_note(username, job_id, bare, payload):
    """The thread note carries the corrected figure (stubbed in tests)."""
    import traceback as _tb
    try:
        c = _chat()
        tid = c._pm_thread_for_job(username, job_id)
        hist = c._pm_load_thread_or_active(username, tid)
        from prometheus import bpiq_deck as _bd
        deck = (_JOBS.get(str(job_id or '')) or {}).get('deck') or {}    # rebuilt over the same file by the rerun
        text = _bd.note(payload, deck)
        if not deck.get('url'):
            rl = str((payload.get('campaign') or {}).get('read_line') or '').strip()
            if rl:
                text += ' ' + rl
        hit = False
        for t in hist or []:
            if (t.get('meta') or {}).get('bpiq_job_id') == job_id:
                # the charge line the first note carried stays as it was said
                _was = str(t.get('text') or '').partition('\n')[2].strip()
                _paid = _was if (_was.startswith('You were charged') or _was == _bd.INCLUDED_LINE) else ''
                t['text'] = text + (('\n' + _paid) if _paid else '')
                _m = {**(t.get('meta') or {}), **_bd.turn_meta(job_id, payload, bare, deck),
                      'corrected_at': payload.get('corrected_at')}
                if not deck.get('url'):
                    # no deck was rebuilt on the corrected figure: an earlier link would open the old one
                    for _k in ('link', 'deck_s3_key', 'files'):
                        _m.pop(_k, None)
                t['meta'] = _m
                hit = True
        if not hit:
            hist.append({'role': 'agent', 'text': text, 'ts': c._pm_iso_now(),
                         'meta': _bd.turn_meta(job_id, payload, bare, deck)})
        c._pm_save_thread_or_active(username, tid, hist)
    except Exception:
        _tb.print_exc()


# ---------------------------------------------------------------------------
# the updated note: once, from the live document, after the last pass
# ---------------------------------------------------------------------------

def _pending_key(run_key):
    from migration.bpiq_synthesis import RUNS_PREFIX
    return f"{RUNS_PREFIX}{run_key}.note_pending.json"


def mark_note_pending(s3, bucket, run_key, *, prior_total=None, reason=''):
    """A correction landed and a note is owed. The marker keeps the
    figure the reader last saw (the first pass's prior total, or the
    figure of the last note sent) so a run of passes compares against
    what is in the inbox, never against the previous pass."""
    import json as _json
    import time as _t
    cur = pending_note(s3, bucket, run_key) or {}
    doc = {'run_key': run_key, 'marked_at': _t.time(), 'passes': int(cur.get('passes') or 0) + 1,
           'reason': str(reason or '')[:200],
           'reader_total': cur.get('reader_total') if cur.get('reader_total') is not None else prior_total}
    try:
        s3.put_object(Bucket=bucket, Key=_pending_key(run_key), Body=_json.dumps(doc, default=str).encode('utf-8'),
                      ContentType='application/json')
    except Exception:
        traceback.print_exc()
    return doc


def pending_note(s3, bucket, run_key):
    import json as _json
    try:
        return _json.loads(s3.get_object(Bucket=bucket, Key=_pending_key(run_key))['Body'].read().decode('utf-8'))
    except Exception:
        return None


def clear_note_pending(s3, bucket, run_key):
    try:
        s3.delete_object(Bucket=bucket, Key=_pending_key(run_key))
    except Exception:
        pass


def note_copy(payload, inputs, who=''):
    """Subject and body of the updated note, from the live payload only.
    Plain lines: the final total with the four lenses, that it replaces
    the earlier figure, and where to open it. No reason beyond updated."""
    from prometheus.delivered import dashboard_url
    v = payload.get('valuation') or {}
    subject_name = str(payload.get('project_name') or
                       f"{inputs.get('qualifier')} x {inputs.get('brand_partner')}").strip()
    total = float(v.get('total_brand_value') or 0)
    subj = f"{subject_name}: valuation updated"
    lenses = (f"Earned media ${float(v.get('earned_media_value') or 0):,.2f}, "
              f"brand engagement ${float(v.get('brand_engagement_value') or 0):,.2f}, "
              f"brand lift ${float(v.get('brand_lift_value') or 0):,.2f}, "
              f"conversions ${float(v.get('conversion_value') or 0):,.2f}.")
    body = (f"Hi {who},\n\n" if who else "Hi,\n\n") + (
        f"The {subject_name} valuation was updated. Total brand value: ${total:,.2f}. {lenses}\n\n"
        f"This figure replaces the one in the earlier note.\n\n"
        f"Open it: {dashboard_url('bpiq')}\n\nPrometheus\nCrosswalk")
    return subj, body


def send_corrected_note(run_key, host, *, send=None, material_rel=0.05, force=False, log=print):
    """ONE updated note from Prometheus, built from the LIVE document at
    send time (the registry, then the file it names), never from a pass's
    local figure. Sends when the live total moved by more than
    `material_rel` against the figure the reader last saw (the pending
    marker's reader_total, else the last note's figure on the registry),
    or when forced. Records the figure sent on the registry and clears
    the marker, so a second call with nothing new sends nothing."""
    import json as _json
    import time as _t
    from migration.bpiq_synthesis import S3Registry
    s3, bucket = _s3_of(host)
    reg = S3Registry(s3, bucket)
    doc = reg.load(run_key) or {}
    bare = str(doc.get('s3_key') or '')
    inputs = dict(doc.get('inputs') or {})
    username = str(doc.get('user') or '')
    if not bare:
        return {'sent': False, 'reason': 'no finished run owns the key', 'email': None}
    payload = _json.loads(s3.get_object(Bucket=bucket, Key=f'brand-partnership-iq/{bare}')['Body'].read().decode('utf-8'))
    live_total = float((payload.get('valuation') or {}).get('total_brand_value') or 0)
    pend = pending_note(s3, bucket, run_key) or {}
    seen = pend.get('reader_total')
    if seen is None:
        seen = doc.get('noted_total')
    try:
        seen_f = float(seen) if seen is not None else None
    except (TypeError, ValueError):
        seen_f = None
    moved = (abs(live_total - seen_f) / seen_f) if seen_f else 1.0
    if not force and seen_f is not None and moved <= material_rel:
        clear_note_pending(s3, bucket, run_key)
        return {'sent': False, 'reason': f'live figure within {material_rel:.0%} of the one the reader has', 'email': None,
                'live_total': live_total, 'reader_total': seen_f}
    if send is None:
        from prometheus.outbound_mail import send_user_email as send
    from prometheus.delivered import _seat
    seat, who = _seat(username, host=host, s3_client=s3, bucket=bucket)
    addr = str(seat.get('email') or '').strip()
    if not (addr and '@' in addr):
        return {'sent': False, 'reason': 'no email on seat', 'email': None, 'live_total': live_total}
    subj, body = note_copy(payload, inputs, who)
    sent = send(to=addr, subject=subj, body=body, instructed=True, caller='delivered_bpiq_update')
    try:
        doc.update({'noted_total': live_total, 'noted_at': _t.time()})
        reg.put(run_key, doc)
    except Exception:
        traceback.print_exc()
    clear_note_pending(s3, bucket, run_key)
    log(f"[bpiq-note {run_key}] updated note sent at ${live_total:,.2f}: {sent}")
    return {'sent': True, 'email': sent, 'live_total': live_total, 'reader_total': seen_f, 'subject': subj, 'body': body}
