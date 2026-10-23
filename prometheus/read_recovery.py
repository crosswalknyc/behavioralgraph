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
MAX_RESUMES = 4              # deploys can come minutes apart
STALE_STAGE = 'picking the read back up'
FINAL_NOTE = ('That read did not finish on my side, so nothing is charged '
              'for it. Ask it again and I will run it fresh.')
FINAL_NOTE_FREE = ('That read did not finish on my side. Ask it again and I '
                   'will run it fresh.')

_INSTANCE = f"{socket.gethostname()}:{os.getpid()}"
_started = False
_lock = threading.Lock()

# Reads on the queue (2026-10-08, item 1 of the Prometheus plan): the web
# process no longer runs a read on a thread that every deploy kills. It
# enqueues the job (status working, stage "lining up the read", a marker
# under _queue/) and the Hetzner read worker (migration/
# prometheus_read_worker.py) claims it and runs the same job body inline.
# If nothing claims a queued read within QUEUE_GRACE seconds the sweeper
# runs it locally, so a worker outage costs minutes, never the read.
QUEUE_PREFIX = 'system/prometheus_reads/_queue/'
QUEUE_GRACE = 150


def queue_enabled():
    """Render enqueues unless PM_READS_ON_QUEUE=0; the worker itself and
    tests never enqueue."""
    if os.environ.get('PM_READ_WORKER') or os.environ.get('REGRESSION_TEST_MODE'):
        return False
    return os.environ.get('PM_READS_ON_QUEUE', '1') != '0'


def enqueue(job_id, head):
    """Write the job as queued and drop its marker. Never raises."""
    s3, bucket = _s3()
    rec = dict(head)
    rec.update({'status': 'working', 'stage': 'lining up the read',
                'queued': True, 'queued_at': time.time()})
    _chat()._pm_read_status_write(job_id, rec)
    s3.put_object(Bucket=bucket, Key=f"{QUEUE_PREFIX}{job_id}.json",
                  Body=json.dumps({'job_id': job_id, 'queued_at': rec['queued_at']}).encode('utf-8'),
                  ContentType='application/json')
    print(f"[pm-queue] read {job_id} queued for the read worker")
    return rec


# Decks ride the same queue (item 1 said reads AND decks: a deploy
# restarts an in-flight deck build just as it did a read). A deck
# marker is `deck_<job_id>.json`; its record lives under the deck
# prefix and carries job_type 'deck' plus a resume block with the
# build's arguments. The worker dispatches on the id's `deck:` tag.
DECK_TAG = 'deck:'


# Engine jobs ride the same queue (audit item 5, 2026-10-14: every job
# through the queue, the web tier stateless). Tags: jiq:, bpiq:, fw:,
# aiq:. Their records are the kind's own status documents (the widget
# polls them already) with a resume block of inputs + extras; the
# worker runs the same job body the web process used to run on a thread.
ENGINE_KINDS = ('jiq', 'bpiq', 'fw', 'aiq', 'tlist')
_ENGINE_PREFIX_CAP = {'jiq': 'jiq_prefix', 'bpiq': 'bpiq_prefix', 'fw': 'fw_prefix', 'aiq': 'aiq_prefix'}
_ENGINE_RUNNER = {'jiq': '_pm_run_jiq_job', 'bpiq': '_pm_run_bpiq_job', 'fw': '_pm_run_fw_job', 'aiq': '_pm_run_aiq_job'}
_ENGINE_WRITER = {'jiq': '_pm_jiq_status_write', 'bpiq': '_pm_bpiq_status_write', 'fw': '_pm_fw_status_write', 'aiq': '_pm_aiq_status_write'}
_ENGINE_LABEL = {'jiq': 'journey', 'bpiq': 'valuation', 'fw': 'flywheel read', 'aiq': 'campaign tracker', 'tlist': 'talent list'}
# the talent list (Custom Ask, 2026-10-15) lives in its own module, not the legacy chat
_MODULE_KINDS = {'tlist': 'prometheus.talent_list_job'}


def _kind_module(kind):
    import importlib
    return importlib.import_module(_MODULE_KINDS[kind])


def _engine_writer(kind):
    return _kind_module(kind).status_write if kind in _MODULE_KINDS else getattr(_chat(), _ENGINE_WRITER[kind])


def _engine_runner(kind):
    return _kind_module(kind).run_job if kind in _MODULE_KINDS else getattr(_chat(), _ENGINE_RUNNER[kind])


def _split_tag(job_id):
    """('deck', id), ('jiq', id) ... or ('read', id)."""
    j = str(job_id or '')
    if j.startswith(DECK_TAG):
        return 'deck', j[len(DECK_TAG):]
    for k in ENGINE_KINDS:
        if j.startswith(k + ':'):
            return k, j[len(k) + 1:]
    return 'read', j


def _engine_prefix(kind):
    if kind in _MODULE_KINDS:
        return _kind_module(kind).JOB_PREFIX
    c = _chat()
    return getattr(c, {'jiq': '_PM_JIQ_JOB_PREFIX', 'bpiq': '_PM_BPIQ_JOB_PREFIX', 'fw': '_PM_FW_JOB_PREFIX', 'aiq': '_PM_AIQ_JOB_PREFIX'}[kind])


def _record_key(kind, jid):
    if kind == 'deck':
        pfx = _deck_prefix()
    elif kind in ENGINE_KINDS:
        pfx = _engine_prefix(kind)
    else:
        pfx = _prefix()
    return f"{pfx}{jid}.json"


def _marker_key(kind, jid):
    if kind == 'deck':
        return f"{QUEUE_PREFIX}deck_{jid}.json"
    if kind in ENGINE_KINDS:
        return f"{QUEUE_PREFIX}{kind}_{jid}.json"
    return f"{QUEUE_PREFIX}{jid}.json"


def start_engine_job(kind, job_id, username, inputs, extras, status_write, runner):
    """Queue an engine job for the worker (or, with the queue off, start
    it on a thread as before). The status document carries the inputs
    and extras to resume from; the widget's poll already reads it.
    Never raises; a queue failure falls back to the thread."""
    head = {'job_id': job_id, 'user': username, 'status': 'queued', 'job_type': kind,
            'started_at': time.time(), 'inputs': _jsonable(inputs),
            'resume': {'inputs': _jsonable(inputs), 'extras': _jsonable(extras) or {}}}
    if queue_enabled():
        try:
            s3, bucket = _s3()
            rec = dict(head, queued=True, queued_at=time.time(), stage=f"lining up the {_ENGINE_LABEL.get(kind, kind)}")
            status_write(job_id, rec)
            s3.put_object(Bucket=bucket, Key=_marker_key(kind, job_id),
                          Body=json.dumps({'job_id': job_id, 'kind': kind, 'queued_at': rec['queued_at']}).encode('utf-8'),
                          ContentType='application/json')
            print(f"[pm-queue] {kind} {job_id} queued for the worker")
            return rec
        except Exception:
            traceback.print_exc()
    status_write(job_id, head)
    threading.Thread(target=runner, args=(job_id, username, inputs, extras), daemon=True).start()
    return head


def _jsonable(obj):
    try:
        return json.loads(json.dumps(obj, default=str))
    except Exception:
        return {}


_INFLIGHT_ENGINE = {}        # job_id -> (kind, status doc) the worker is running, for the shutdown handoff
MAX_ENGINE_RESTARTS = 1      # restart a stranded job once, then stop honestly (the sweeper contract)


def run_engine_inline(kind, job_id, status):
    """The worker runs one claimed engine job right here."""
    c = _chat()
    rr = status.get('resume') if isinstance(status.get('resume'), dict) else None
    uname = str(status.get('user') or '').strip()
    inputs = (rr or {}).get('inputs') or status.get('inputs')
    writer = _engine_writer(kind)
    if str(status.get('status') or '') == 'cancelled' or _is_cancelled(kind, job_id):
        return False     # stopped before a worker reached it (2026-10-16)
    if not isinstance(inputs, dict) or not inputs or not uname:
        writer(job_id, {**status, 'status': 'error', 'queued': False,
                        'error': f"queued {_ENGINE_LABEL.get(kind, kind)} without a resume record", 'finished_at': time.time()})
        return False
    _INFLIGHT_ENGINE[job_id] = (kind, dict(status))
    try:
        _engine_runner(kind)(job_id, uname, inputs, dict((rr or {}).get('extras') or {}))
    finally:
        _INFLIGHT_ENGINE.pop(job_id, None)
    return True


def cancel_engine_job(kind, job_id, reason=''):
    """Stop an engine job (Jenna 2026-10-16: "stop it and redo it"). The
    status document reads 'cancelled' and its queue marker goes away, so
    a worker that has not claimed it never will, a restart never requeues
    it, and a worker mid-run drops the result instead of persisting it.
    Returns the record, or None when the job is unknown or already done."""
    s3, bucket = _s3()
    key = _record_key(kind, job_id)
    try:
        doc = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8'))
    except Exception:
        return None
    if str(doc.get('status') or '') in ('done', 'complete', 'completed', 'error', 'failed', 'cancelled'):
        return None
    doc.update({'status': 'cancelled', 'queued': False, 'cancelled_at': time.time(), 'cancel_reason': str(reason or '')[:160],
                'stage': 'stopped', 'finished_at': time.time()})
    _engine_writer(kind)(job_id, doc)
    try:
        s3.delete_object(Bucket=bucket, Key=_marker_key(kind, job_id))
    except Exception:
        pass
    print(f"[pm-queue] {kind} {job_id} cancelled ({reason})")
    return doc


def _is_cancelled(kind, job_id):
    try:
        s3, bucket = _s3()
        doc = json.loads(s3.get_object(Bucket=bucket, Key=_record_key(kind, job_id))['Body'].read().decode('utf-8'))
        return str(doc.get('status') or '') == 'cancelled'
    except Exception:
        return False


def requeue_engine(kind, job_id, doc, reason='restart'):
    """Put a stranded engine job back on the queue from its saved inputs
    (status 'queued' + marker), counting the restart. Returns the new
    record, or None when the job has no inputs to restart from, has
    already restarted MAX_ENGINE_RESTARTS times (2026-10-14: a job a
    worker lost mid-run sat 'running' for hours; the sweeper's web
    thread restart died with the next deploy or autoscale), or was
    cancelled (2026-10-16)."""
    try:
        if _is_cancelled(kind, job_id):
            return None
        c = _chat()
        writer = _engine_writer(kind)
        rr = doc.get('resume') if isinstance(doc.get('resume'), dict) else {}
        inputs = rr.get('inputs') or doc.get('inputs')
        if not isinstance(inputs, dict) or not inputs or not doc.get('user'):
            return None
        restarts = int(doc.get('restarts') or 0)
        if restarts >= MAX_ENGINE_RESTARTS:
            return None
        s3, bucket = _s3()
        rec = {k: v for k, v in doc.items() if k not in ('claimed_by', 'claimed_at', 'error', 'finished_at', 'ended_at', 'handoff_at')}
        rec.update({'status': 'queued', 'queued': True, 'queued_at': time.time(), 'job_type': kind,
                    'restarts': restarts + 1, 'restart_reason': str(reason)[:120],
                    'stage': f"lining up the {_ENGINE_LABEL.get(kind, kind)} again",
                    'resume': {'inputs': _jsonable(inputs), 'extras': _jsonable((rr or {}).get('extras') or {})}})
        writer(job_id, rec)
        s3.put_object(Bucket=bucket, Key=_marker_key(kind, job_id),
                      Body=json.dumps({'job_id': job_id, 'kind': kind, 'queued_at': rec['queued_at']}).encode('utf-8'),
                      ContentType='application/json')
        print(f"[pm-queue] {kind} {job_id} requeued ({reason}, restart {restarts + 1})")
        return rec
    except Exception:
        traceback.print_exc()
        return None


def enqueue_deck(job_id, head):
    """Write the deck job as queued (status 'queued', the state the
    widget's poll already knows) and drop its marker. Never raises."""
    s3, bucket = _s3()
    rec = dict(head)
    rec.update({'status': 'queued', 'job_type': 'deck', 'queued': True,
                'queued_at': time.time(), 'stage': 'lining up the deck'})
    _chat()._pm_deck_status_write(job_id, rec)
    s3.put_object(Bucket=bucket, Key=_marker_key('deck', job_id),
                  Body=json.dumps({'job_id': job_id, 'kind': 'deck',
                                   'queued_at': rec['queued_at']}).encode('utf-8'),
                  ContentType='application/json')
    print(f"[pm-queue] deck {job_id} queued for the read worker")
    return rec


def list_queued():
    """Marker job ids, oldest first. Deck markers come back tagged
    `deck:<id>` so claim_queued / run_inline dispatch on them."""
    s3, bucket = _s3()
    out = []
    try:
        token = None
        while True:
            kw = dict(Bucket=bucket, Prefix=QUEUE_PREFIX)
            if token:
                kw['ContinuationToken'] = token
            r = s3.list_objects_v2(**kw)
            for o in r.get('Contents') or []:
                k = str(o.get('Key') or '')
                if k.endswith('.json'):
                    name = k.rsplit('/', 1)[-1][:-5]
                    if name.startswith('deck_'):
                        name = DECK_TAG + name[5:]
                    else:
                        for ek in ENGINE_KINDS:
                            if name.startswith(ek + '_'):
                                name = ek + ':' + name[len(ek) + 1:]
                                break
                    out.append((o.get('LastModified'), name))
            if not r.get('IsTruncated'):
                break
            token = r.get('NextContinuationToken')
    except Exception:
        traceback.print_exc()
    out.sort(key=lambda x: (x[0] is None, x[0]))
    return [j for _lm, j in out]


def claim_queued(job_id):
    """The worker takes a queued read or deck: conditional put flips
    queued -> claimed; the marker goes. Returns the record or None
    (someone else took it, or it is no longer queued)."""
    s3, bucket = _s3()
    kind, job_id = _split_tag(job_id)
    key = _record_key(kind, job_id)
    marker = _marker_key(kind, job_id)
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        etag = str(obj.get('ETag') or '').strip('"')
        cur = json.loads(obj['Body'].read())
    except Exception:
        try:
            s3.delete_object(Bucket=bucket, Key=marker)
        except Exception:
            pass
        return None
    open_status = 'queued' if kind in ('deck',) + ENGINE_KINDS else 'working'
    if not cur.get('queued') or cur.get('claimed_by') or str(cur.get('status') or '') != open_status:
        try:
            s3.delete_object(Bucket=bucket, Key=marker)
        except Exception:
            pass
        return None
    claimed = dict(cur)
    claimed.update({'queued': False, 'claimed_by': _INSTANCE, 'claimed_at': time.time(),
                    'stage': 'planning the deck' if kind == 'deck' else (f"starting the {_ENGINE_LABEL[kind]}" if kind in ENGINE_KINDS else 'reading the data')})
    body = json.dumps(claimed).encode('utf-8')
    try:
        kw = dict(Bucket=bucket, Key=key, Body=body, ContentType='application/json')
        if etag:
            kw['IfMatch'] = etag
        s3.put_object(**kw)
    except Exception as e:
        if 'PreconditionFailed' in str(e) or '412' in str(e):
            return None
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType='application/json')
            time.sleep(1.0)
            again = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
            if again.get('claimed_by') != _INSTANCE:
                return None
        except Exception:
            return None
    try:
        s3.delete_object(Bucket=bucket, Key=marker)
    except Exception:
        pass
    return claimed


def run_deck_inline(job_id, status):
    """The worker builds one claimed deck right here, through the same
    job body the web process used to run on a thread. Returns True
    when the job body ran."""
    c = _chat()
    kind, job_id = _split_tag(job_id)
    rr = status.get('resume') if isinstance(status.get('resume'), dict) else None
    uname = str(status.get('user') or '').strip()
    if not rr or not isinstance(rr.get('ctx'), dict) or not uname:
        c._pm_deck_status_write(job_id, {**status, 'status': 'error', 'queued': False,
                                          'error': 'queued deck without a resume record',
                                          'finished_at': time.time()})
        return False
    try:
        if isinstance(rr.get('extras'), dict) and rr['extras']:
            c._PM_DECK_PPU_EXTRAS[job_id] = dict(rr['extras'])
    except Exception:
        pass
    c._pm_run_deck_job(job_id, uname, rr['ctx'], list(rr.get('history') or []),
                       str(rr.get('angle') or ''), str(rr.get('charge_user') or ''),
                       str(rr.get('subject') or ''), str(rr.get('partner') or ''))
    return True


def run_inline(job_id, status):
    """The worker runs one claimed read (or deck) right here, through
    the same code path the web process used to run on a thread.
    Returns True when the job body ran."""
    kind, job_id = _split_tag(job_id)
    if kind == 'deck' or str(status.get('job_type') or '') == 'deck':
        return run_deck_inline(job_id, status)
    if kind in ENGINE_KINDS or str(status.get('job_type') or '') in ENGINE_KINDS:
        return run_engine_inline(kind if kind in ENGINE_KINDS else str(status.get('job_type')), job_id, status)
    c, h = _chat(), _host()
    rr = status.get('resume') if isinstance(status.get('resume'), dict) else None
    uname = str(status.get('user') or '').strip()
    if not rr or not rr.get('text') or not uname:
        _finalize(job_id, status, 'queued read without a resume record')
        return False
    try:
        data = h.load_users()
        user = (data.get('users') or {}).get(uname)
    except Exception:
        user = None
    if not isinstance(user, dict):
        _finalize(job_id, status, 'requester not found')
        return False
    headers = {'X-Prometheus-Caller': 'resume:read-worker'} if rr.get('probe') else {}
    panel_confirm = None
    if isinstance(rr.get('panel_charge'), dict):
        panel_confirm = {'subject': rr.get('bind_subject') or '', 'resume_charge': rr['panel_charge']}
    with h.app.test_request_context('/api/brief-chat/analyze', method='POST',
                                    json={'text': rr['text']}, headers=headers):
        from flask import session as _session
        _session['username'] = uname
        c._pm_generate_metrics_response(
            user, rr['text'], list(rr.get('history') or []), prefer_catalog=True,
            bind_subject=rr.get('bind_subject') or None, bind_cohort=rr.get('bind_cohort') or None,
            panel_confirm=panel_confirm, switch_page=rr.get('switch_page') or None,
            inline_job_id=job_id, force_local=True)
    return True
_INFLIGHT = {}               # job_id -> head, for the SIGTERM handoff
_handoff_installed = False


def _chat():
    from prometheus.legacy import chat as _c
    return _c


def _host():
    from prometheus.legacy import H as _h
    return _h


def _s3():
    h = _host()
    return h.s3_client, h.S3_BUCKET


def _deck_prefix():
    try:
        return _chat()._PM_DECK_PREFIX
    except Exception:
        return 'system/prometheus_decks/'


def _queued_deck_fallback(now=None):
    """A queued deck nobody claimed inside QUEUE_GRACE runs here, on a
    thread, so a worker outage costs minutes, never the deck. Returns
    the ids it took. Never raises."""
    out = []
    if os.environ.get('PM_READ_WORKER'):
        return out
    try:
        s3, bucket = _s3()
        now = now or time.time()
        for tagged in list_queued():
            kind, jid = _split_tag(tagged)
            if kind not in ('deck',) + ENGINE_KINDS:
                continue
            try:
                mk = json.loads(s3.get_object(Bucket=bucket, Key=_marker_key(kind, jid))['Body'].read())
                queued_at = float(mk.get('queued_at') or 0)
            except Exception:
                continue
            if not queued_at or now - queued_at <= QUEUE_GRACE:
                continue
            rec = claim_queued(tagged)
            if not rec:
                continue
            print(f"[pm-queue] {kind} {jid} unclaimed for {int(now - queued_at)}s; running it here")
            if kind == 'deck':
                threading.Thread(target=run_deck_inline, args=(jid, rec), daemon=True).start()
            else:
                threading.Thread(target=run_engine_inline, args=(kind, jid, rec), daemon=True).start()
            out.append(jid)
    except Exception:
        traceback.print_exc()
    return out


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


HELD_RETRY_AFTER = 180       # a held read gets one fresh run
MAX_HELD_RETRIES = 1

# The chain a resumed read belongs to (2026-10-22 audit, Casey Pearson's
# "re-run 9/16 to 9/30"). A read the verifier held was retried once; the
# retry was held too; the rule "a retry's own job is never retried again"
# then left it, and a third pass only ran six hours later when the
# grandparent aged out of the sweep's lookback and the rule stopped
# seeing the child as a retry. The answer landed in her thread six
# hours after the ask, and the calm line's "I will email you when it
# completes" was never kept because the email opt-in lives on the first
# job id and a held read drops it. Every resumed job now carries a small
# chain marker (root, depth, whether the reader was promised an email):
# the sweep gives a held read MAX_HELD_PASSES fresh passes in all and
# then finalizes honestly instead of waiting on an accident, and a read
# that lands after the calm line was shown is emailed to the seat.
CHAIN_PREFIX = 'system/prometheus_reads/_chain/'
MAX_HELD_PASSES = 2


def chain_marker(job_id):
    """{'root', 'depth', 'promised_email'} for a resumed job, {} for a
    job that was never resumed. Never raises."""
    try:
        s3, bucket = _s3()
        doc = json.loads(s3.get_object(Bucket=bucket, Key=f"{CHAIN_PREFIX}{job_id}.json")['Body'].read())
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def _write_chain(new_id, parent_id, parent_status, held_retry):
    """The child's marker: depth counts fresh passes after the first run;
    the email promise is inherited and set by a held retry (the held
    read's thread turn is the calm line that promises the email)."""
    try:
        parent = chain_marker(parent_id)
        doc = {'root': parent.get('root') or parent_id, 'parent': parent_id,
               'depth': int(parent.get('depth') or 0) + 1,
               'promised_email': bool(parent.get('promised_email')) or bool(held_retry),
               'user': str((parent_status or {}).get('user') or ''),
               'question': str((parent_status or {}).get('question') or '')[:300],
               'written_at': time.time()}
        s3, bucket = _s3()
        s3.put_object(Bucket=bucket, Key=f"{CHAIN_PREFIX}{new_id}.json",
                      Body=json.dumps(doc).encode('utf-8'), ContentType='application/json')
        return doc
    except Exception:
        traceback.print_exc()
        return {}


def promised_email(job_id):
    """True when the reader was told the answer would come by email (a
    held read's calm line) somewhere up this job's chain."""
    return bool(chain_marker(job_id).get('promised_email'))


def held_passes_left(job_id, status=None):
    """How many more fresh passes a held read may get. A read that was
    never resumed has had one run; each resume adds one."""
    depth = int(chain_marker(job_id).get('depth') or 0)
    return max(0, MAX_HELD_PASSES - depth)


def is_retryable_held(status, now=None, after=HELD_RETRY_AFTER):
    """A read the verifier held, with a resume record, not yet retried:
    one fresh generation often clears the checks (a different draft,
    the findings in hand). The user already saw the calm line; the
    answer lands under it when the retry passes."""
    if not isinstance(status, dict) or str(status.get('status') or '') != 'held':
        return False
    if int(status.get('held_retries') or 0) >= MAX_HELD_RETRIES or status.get('resumed_as'):
        return False
    rr = status.get('resume')
    if not isinstance(rr, dict) or not rr.get('text'):
        return False
    done_at = max(float(status.get('finished_at') or 0), float(status.get('stage_at') or 0),
                  float(status.get('started_at') or 0))
    return done_at > 0 and (now or time.time()) - done_at > after


def is_stale(status, now=None, after=RECOVER_AFTER):
    if not isinstance(status, dict):
        return False
    if str(status.get('status') or '') != 'working':
        return False
    if status.get('resumed_as') and status.get('resumed_as') != status.get('job_id'):
        return False
    if status.get('queued') and not status.get('claimed_by'):
        try:
            return (now or time.time()) - float(status.get('queued_at') or 0) > QUEUE_GRACE
        except (TypeError, ValueError):
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
    while it is working. Returns the stop event. Also registers the job
    for the SIGTERM handoff."""
    stop = threading.Event()
    _INFLIGHT[job_id] = head

    def _run():
        try:
            while not stop.wait(HEARTBEAT_EVERY):
                try:
                    _beat_once(job_id)
                except Exception:
                    break          # finished, resumed elsewhere, or S3 trouble
        finally:
            _INFLIGHT.pop(job_id, None)
    try:
        threading.Thread(target=_run, daemon=True).start()
    except Exception:
        pass
    return stop


def _beat_once(job_id):
    """One heartbeat write; raises to end the loop when the job is no
    longer working here."""
    s3, bucket = _s3()
    key = f"{_prefix()}{job_id}.json"
    cur = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
    if str(cur.get('status') or '') != 'working' or cur.get('resumed_as'):
        raise RuntimeError('job no longer working here')
    cur['heartbeat_at'] = time.time()
    _chat()._pm_read_status_write(job_id, cur)


def handoff_inflight(reason='shutdown'):
    """The process is going away (a deploy): mark every read it is
    running as stale right now so the next instance's boot sweep picks
    it up in seconds instead of minutes, and put every engine job it is
    running back on the queue. Fast, best effort."""
    for job_id, (kind, doc) in list(_INFLIGHT_ENGINE.items()):
        try:
            requeue_engine(kind, job_id, doc, reason=f'worker {reason}')
        except Exception:
            traceback.print_exc()
    jobs = list(_INFLIGHT.items())
    for job_id, head in jobs:
        try:
            s3, bucket = _s3()
            key = f"{_prefix()}{job_id}.json"
            cur = json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read())
            if str(cur.get('status') or '') != 'working' or cur.get('resumed_as'):
                continue
            # pull every beat back so is_stale() is true immediately
            for k in ('heartbeat_at', 'stage_at', 'claimed_at'):
                cur.pop(k, None)
            cur['started_at'] = time.time() - RECOVER_AFTER - 5
            cur['handoff_at'] = time.time()
            cur['handoff_from'] = _INSTANCE
            cur['stage'] = 'handing the read to a fresh instance'
            _chat()._pm_read_status_write(job_id, cur)
            print(f"[pm-recover] handed off read {job_id} ({reason})")
        except Exception:
            traceback.print_exc()
    return len(jobs)


def install_handoff():
    """Chain a SIGTERM handler ahead of gunicorn's so in-flight reads are
    handed off before the worker exits. Main thread only; never raises."""
    global _handoff_installed
    if _handoff_installed:
        return False
    try:
        import signal
        if threading.current_thread() is not threading.main_thread():
            return False
        prev = signal.getsignal(signal.SIGTERM)

        def _on_term(signum, frame):
            try:
                handoff_inflight('SIGTERM')
            except Exception:
                pass
            if callable(prev):
                prev(signum, frame)
            elif prev == signal.SIG_DFL:
                signal.signal(signal.SIGTERM, signal.SIG_DFL)
                os.kill(os.getpid(), signal.SIGTERM)
        signal.signal(signal.SIGTERM, _on_term)
        _handoff_installed = True
        return True
    except Exception:
        return False


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
    held_retry = is_retryable_held(cur)
    if not is_stale(cur) and not held_retry:
        return None
    claimed = dict(cur)
    claimed.update({'status': 'working', 'stage': STALE_STAGE, 'queued': False,
                    'claimed_by': _INSTANCE, 'claimed_at': time.time(),
                    'resume_attempts': int(cur.get('resume_attempts') or 0) + 1})
    if cur.get('queued'):
        try:
            s3.delete_object(Bucket=bucket, Key=f"{QUEUE_PREFIX}{job_id}.json")
        except Exception:
            pass
    if held_retry:
        claimed['held_retries'] = int(cur.get('held_retries') or 0) + 1
        claimed['stage'] = 'taking another pass'
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
    refunded = False
    try:
        if isinstance(charge, dict):
            c._pm_panel_refund(charge)
            refunded = True
    except Exception:
        traceback.print_exc()
    note = FINAL_NOTE if refunded else FINAL_NOTE_FREE
    try:
        c._pm_read_status_write(job_id, {
            **{k: v for k, v in status.items() if k != 'resume'},
            'status': 'error', 'final': True, 'final_reason': str(reason)[:200],
            'payload': {'success': True, 'action': 'answer', 'reply': note,
                        'followups': [], 'offer_deck': False, 'deck_angle': None}})
    except Exception:
        traceback.print_exc()
    try:
        if uname and not status.get('probe'):
            c._pm_append_read_to_history(uname, job_id, {
                'success': True, 'action': 'answer', 'reply': note,
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
            + ("charge refunded, " if refunded else "no charge on the job, ")
            + "calm note left in the thread; the user needs the answer by email",
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
    if not is_stale(status) and not is_retryable_held(status):
        return None
    claimed = _claim(job_id, status)
    if not claimed:
        return None
    rr = claimed.get('resume') if isinstance(claimed.get('resume'), dict) else None
    uname = str(claimed.get('user') or '').strip()
    attempts = int(claimed.get('resume_attempts') or 1)
    if (not rr or not rr.get('text')) and str(claimed.get('question') or '').strip():
        # A launch that predates the resume record still carries the
        # question and the requester: run it from those (2026-10-08,
        # Casey Pearson's genre read was stranded by a deploy and only
        # got a note). The base resolves again from the question.
        rr = resume_record(text=str(claimed.get('question') or ''), history=[],
                           base={}, panel_charge=claimed.get('panel_charge'),
                           probe=bool(claimed.get('probe')))
    if not rr or not rr.get('text') or not uname:
        _finalize(job_id, claimed, 'no question or requester on the job')
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
                switch_page=rr.get('switch_page') or None,
                force_local=True)   # recovery never re-queues (a worker outage must not loop)
        body = resp.get_json() if hasattr(resp, 'get_json') else resp
        body = body if isinstance(body, dict) else {}
        new_id = str(body.get('read_job_id') or '').strip()
        if new_id == job_id:
            # attached to the dead job itself (the in-flight dedupe):
            # nothing is running; leave it stale for the next pass
            print(f"[pm-recover] resume of {job_id} attached to itself; retrying next sweep")
            c._pm_read_status_write(job_id, {
                **{k: v for k, v in claimed.items() if k != 'resume'},
                'resume': rr, 'claimed_by': None, 'claimed_at': None})
            return None
        if new_id:
            try:
                # the opt-in email side-file follows the read
                nk = f"{c._PM_NOTIFY_PREFIX}{job_id}.json"
                side = s3.get_object(Bucket=bucket, Key=nk)['Body'].read()
                s3.put_object(Bucket=bucket, Key=f"{c._PM_NOTIFY_PREFIX}{new_id}.json",
                              Body=side, ContentType='application/json')
            except Exception:
                pass
            # the chain marker follows too: pass count and the email promise
            _write_chain(new_id, job_id, claimed, held_retry=bool(claimed.get('held_retries')))
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
        while str(cur.get('status') or '') == 'working' and cur.get('resumed_as') \
                and cur.get('resumed_as') != cur.get('job_id') and hops < 3:
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
        records = []
        for k in keys:
            try:
                records.append((k, json.loads(s3.get_object(Bucket=bucket, Key=k)['Body'].read())))
            except Exception:
                continue
        # a retry's own job is retried only while its chain has passes left
        # (MAX_HELD_PASSES in all); a held read with none left finalizes
        # honestly instead of sitting until the lookback accident retries it
        retry_children = {str(st.get('resumed_as')) for _k, st in records
                          if st.get('held_retries') and st.get('resumed_as')}
        for k, st in records:
            jid = str(st.get('job_id') or k.rsplit('/', 1)[-1][:-5])
            if is_stale(st, now):
                out.append((jid, recover(jid, status=st, reason='sweep')))
            elif is_retryable_held(st, now):
                depth = int(chain_marker(jid).get('depth') or 0)
                if depth >= MAX_HELD_PASSES or (depth == 0 and jid in retry_children):
                    # no passes left (or a retry child from before the marker): the honest terminal now
                    _finalize(jid, st, f'held after {depth + 1} passes')
                    out.append((jid, 'final'))
                else:
                    out.append((jid, recover(jid, status=st, reason='held-retry')))
    except Exception:
        traceback.print_exc()
    for jid in _queued_deck_fallback(now):
        out.append((jid, 'deck-local'))
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
    install_handoff()

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
