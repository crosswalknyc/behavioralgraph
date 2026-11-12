"""A progress line is posted only for a run that exists, an outcome
replaces it, and no thread ends on a promise with nothing behind it
(Jenna 2026-11-11).

Jessie, thread ebf520b02f, 19:07 UTC. Her Paramount Plus build started
("Profile building: Georgie & Mandy's First Marriage Viewers -
Paramount+ ... I will email you when it is complete"), was refused by
the engine 17 seconds later, and the thread then read:

    Georgie & Mandy's First Marriage Viewers - Paramount+ is building.
    Working on it. Confirmed your task is in progress and I will email
    you when it completes.

The engine's status carried the line she should have read ("We can't
see ... on Paramount+ at this time. Notifying Crosswalk and will get
back to you soon."). The status route blanked it and the widget drew
the calm line for every failed run; with no browser open the server
closed the launch with no line at all. Armine, 21:14 UTC: a follow-up
drew the calm line with no job, no held record, nothing.

2026-11-10 (prometheus.order_truth, prometheus.build_ready) covered an
order that never starts and a build that finishes. This covers what was
left:

  1. THE OUTCOME REPLACES THE PROGRESS LINE. A build that is refused,
     fails or is cancelled after it started has its "Profile building"
     line rewritten, in the thread it was launched from, to the real
     outcome in the client's words: the engine's own client line when it
     wrote one (the platform refusal, the title confirmation, the
     refund line), the cancel note, else the house line for a build
     that could not be completed. The "X is building." lines and the
     calm line a tab drew under it are withdrawn. The server does this
     from the sweep with no browser open (``settle_launch``) and from
     the status poll the moment a tab sees it (``settle_from_status``).
  2. A PROMISE HAS A RECORD. The calm line ("Working on it. Confirmed
     your task is in progress and I will email you when it completes.",
     Jenna 2026-09-30) is a promise that a person answers by email. It
     goes out only with something behind it: a job or a run, or a held
     record with a due time that the ops watch and the daily digest
     read (``hold``). ``back_calm`` puts the record behind a calm reply
     that has none, at the one exit every reply passes; ``back_thread``
     does the same for a calm line a tab wrote into a thread on its own
     (a failed fetch, a blank turn); ``job_failed`` does it for a read
     or a deck job that died, and lands the line on the job's own
     progress turn so the progress line does not outlive the job.
  3. THE SWEEP. ``thread_tail`` reads how a thread ends: on an answer,
     or on a promise, and what is behind the promise. The read-only
     sweep (scripts/pm_unbacked_promise_sweep.py) lists every thread
     that ends on one with nothing behind it.

Nothing here writes to a reader's thread except ``settle_launch`` /
``settle_from_status`` (the outcome of the reader's own build) and
``job_failed`` (the reader's own job). No mail is sent from here.
Never raises. Regression: scripts/test_pm_truth_2026_11_11.py.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone

__all__ = ['CALM_LINE', 'is_calm', 'promise_kind', 'outcome_line', 'settle_history', 'settle_launch', 'settle_from_status',
           'hold', 'find_held', 'back_calm', 'back_thread', 'job_failed', 'land_note', 'thread_tail', 'classify_tail', 'WITHDRAWN_TEXT', 'HELD_PREFIX', 'DEAD']

CALM_LINE = "Working on it. Confirmed your task is in progress and I will email you when it completes."
HELD_PREFIX = 'system/ops/held_replies/'
DEAD = ('failed', 'error', 'cancelled', 'canceled', 'refunded')
WITHDRAWN_TEXT = "This line is withdrawn: the build above did not finish. Its outcome is in the note above."
GENERIC_FAILED = "This build could not be completed. Credits for this run have been refunded."
GENERIC_CANCELLED = "Stopped at your request. The charge for this run was returned."

# the engine's own client lines: the words a reader may be shown as written
_CLIENT_LINE_RXS = (
    re.compile(r"^We can't see .{1,200} on .{1,120} at this time\. Notifying Crosswalk and will get back to you soon\."
               r"(?: The charge for this run has been refunded\.)?$"),
    re.compile(r"^We need to confirm which .{1,200} you mean before we build this\. Notifying Crosswalk and will get back to you soon\."
               r"(?: The charge for this run has been refunded\.)?$"),
    re.compile(r"^This build could not be completed\. (?:The charge for this run has been refunded|Credits for this run have been refunded)\.$"),
    re.compile(r"^Stopped at your request\. (?:The charge for this run was returned|Credits for this run were returned)\.$"),
)
_PROMISE_RXS = (
    ('calm', re.compile(r"^\s*(?:Included with your plan\.\s*)?Working on it[.!]\s*(?:Confirmed your task is in progress|I will email you)", re.I)),
    ('build', re.compile(r"^\s*(?:Included with your plan\.\s*)?(?:Profile building:|Queued:)|^\s*.{2,160}\s+is\s+(?:building|queued)\.\s*$", re.I)),
    ('read', re.compile(r"^\s*(?:Included with your plan\.\s*)?(?:On it\b|Already on it\b)|\bthe read will land right here\b", re.I)),
    ('deck', re.compile(r"^\s*Working on it\.\s*The deck will land\b|^\s*Working on the (?:PowerPoint|deck) now\b|"
                        r"^\s*Building (?:an|the) [^.\n]{0,160}insights deck\b", re.I)),
    ('email', re.compile(r"\b(?:I am (?:also )?looking at it(?: now)? and will email you|I will email you the (?:read|results))\b", re.I)),
)
_JOB_KEYS = ('read_job_id', 'deck_job_id', 'jiq_job_id', 'bpiq_job_id', 'fw_job_id', 'aiq_job_id', 'tlist_job_id', 'twin_job_id')
_WIDGET_LINE_RX = re.compile(r"^\s*(?P<subj>.{2,200}?)\s*(?:is queued\.|is building\.|:\s*(?:failed|error|cancelled|queued|running)\.)\s*$", re.I | re.S)


def _iso(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _squash(s):
    return ' '.join(str(s or '').split())


def _meta(t):
    m = t.get('meta') if isinstance(t, dict) else None
    return m if isinstance(m, dict) else {}


def is_calm(text):
    return _squash(text).startswith('Working on it. Confirmed your task is in progress')


def promise_kind(text):
    """'calm' | 'build' | 'read' | 'deck' | 'email' when the words are a
    progress line or a promise; '' for anything else."""
    t = str(text or '')
    if not t.strip():
        return ''
    head = t.strip()[:400]
    for kind, rx in _PROMISE_RXS:
        if rx.search(head):
            return kind
    return ''


# ------------------------------------------------------------------ 1. the outcome
def outcome_line(run_doc):
    """(the line a reader is shown for a run that ended without a file,
    its state) or ('', '') while the run is alive or finished."""
    doc = run_doc if isinstance(run_doc, dict) else {}
    st = str(doc.get('status') or '').strip().lower()
    if st not in DEAD:
        return '', ''
    if st in ('cancelled', 'canceled'):
        note = _squash(doc.get('cancel_note') or '')
        return (note if any(rx.match(note) for rx in _CLIENT_LINE_RXS) else GENERIC_CANCELLED), 'cancelled'
    line = _squash(doc.get('client_message') or doc.get('error') or '')
    if line and any(rx.match(line) for rx in _CLIENT_LINE_RXS):
        return line, 'failed'
    # the engine's client line followed by its refund sentence, or a line with an internal word in it: the house line
    m = re.match(r"^(We (?:can't see|need to confirm which) .{1,320}? Notifying Crosswalk and will get back to you soon\.)", line)
    if m:
        refund = ' The charge for this run has been refunded.' if 'refunded' in line.lower() else ''
        return m.group(1) + refund, 'failed'
    return GENERIC_FAILED, 'failed'


def _is_run_ack(t, rid):
    m = _meta(t)
    return isinstance(t, dict) and str(t.get('role') or '') in ('agent', 'assistant') and str(m.get('run_id') or '') == rid \
        and not m.get('build_outcome')


def _same_subject(a, b):
    na, nb = re.sub(r"[^a-z0-9]+", '', str(a or '').lower()), re.sub(r"[^a-z0-9]+", '', str(b or '').lower())
    return bool(na) and bool(nb) and (na == nb or (min(len(na), len(nb)) >= 6 and (na in nb or nb in na)))


def settle_history(history, run_id, run_doc, now=None):
    """The thread with the run's progress lines replaced by its outcome.
    Returns (history, how): 'settled' (the ack rewritten), 'appended'
    (no ack in the thread: the outcome added as its own turn), 'already'
    or 'alive'."""
    rid = re.sub(r"[^A-Za-z0-9_.-]", '', str(run_id or ''))[:80]
    line, state = outcome_line(run_doc)
    turns = [dict(t) if isinstance(t, dict) else t for t in (history or [])]
    if not rid or not line:
        return turns, 'alive'
    stamp = _iso(now)
    for t in turns:
        bo = _meta(t).get('build_outcome') if isinstance(t, dict) else None
        if isinstance(bo, dict) and bo.get('run_id') == rid:
            return turns, 'already'
    subject = _squash((run_doc or {}).get('subject') or '')
    ack_i = next((i for i, t in enumerate(turns) if _is_run_ack(t, rid)), None)
    outcome_meta = {'build_outcome': {'run_id': rid, 'status': state, 'at': stamp}, 'corrected_at': stamp, 'server_note': True,
                    'status': str((run_doc or {}).get('status') or state), 'kind': 'build_outcome'}
    how = 'settled'
    if ack_i is None:
        turns.append({'role': 'agent', 'text': line, 'ts': _iso(now), 'turn_id': f"outcome-{rid}",
                      'meta': dict(outcome_meta, run_id=rid, subject=subject[:120])})
        ack_i = len(turns) - 1
        how = 'appended'
        scan_from = 0
    else:
        ack = turns[ack_i]
        subject = subject or _squash(_meta(ack).get('subject') or '')
        ack['meta'] = dict(_meta(ack), retired=str(ack.get('text') or '')[:400], **outcome_meta)
        ack['text'] = line
        scan_from = ack_i + 1
    # the lines a tab drew for this run: "<subject> is building.", "<subject> is queued.", the calm line under them
    seen_user = False
    for j in range(scan_from, len(turns)):
        t = turns[j]
        if j == ack_i or not isinstance(t, dict):
            continue
        if str(t.get('role') or '') == 'user':
            seen_user = True
            continue
        m = _meta(t)
        if m.get('withdrawn') or m.get('build_outcome') or any(m.get(k) for k in _JOB_KEYS) or m.get('run_id'):
            continue
        text = str(t.get('text') or '')
        wm = _WIDGET_LINE_RX.match(text)
        mine = bool(wm and subject and _same_subject(wm.group('subj'), subject))
        calm_under = is_calm(text) and not seen_user and how == 'settled' and not m.get('held_ref')
        if mine or calm_under:
            t['meta'] = dict(m, withdrawn=True, corrected_at=stamp, server_note=True, retired=text[:400], build_run_id=rid)
            t['text'] = WITHDRAWN_TEXT
    return turns, how


def settle_launch(launch, run_doc, *, exists, load, save, ensure=None, relist=None, now=None):
    """The outcome of one launched build, in the thread that launched
    it. Returns 'settled', 'appended', 'already', 'alive', 'no_thread'
    or 'failed'."""
    try:
        user = str((launch or {}).get('user') or '')
        tid = str((launch or {}).get('thread_id') or '')
        rid = str((launch or {}).get('run_id') or (run_doc or {}).get('run_id') or '')
        if not (user and rid):
            return 'failed'
        if not outcome_line(run_doc)[0]:
            return 'alive'
        if not tid:
            return 'no_thread'
        if not exists(user, tid):
            if tid.startswith('api-') and callable(ensure):
                ensure(user, tid, title='API conversation', via='api')
            elif callable(relist):
                relist(user, tid)
            else:
                return 'no_thread'
        doc = dict(run_doc or {})
        doc.setdefault('subject', (launch or {}).get('subject') or '')
        history, how = settle_history(list(load(user, tid) or []), rid, doc, now=now)
        if how in ('settled', 'appended'):
            save(user, tid, history)
        return how
    except Exception:
        return 'failed'


def settle_from_status(uname, run_id, run_doc, *, service=None, launch_get=None):
    """The status poll saw a run that ended without a file: the thread
    that launched it is settled now. Returns (line, how)."""
    line, _state = outcome_line(run_doc)
    if not line:
        return '', 'alive'
    how = 'no_thread'
    try:
        if service is None:
            from . import service as service        # noqa: PLW0642
        launch = None
        if callable(launch_get):
            launch = launch_get(run_id)
        if not isinstance(launch, dict):
            from . import build_ready as _br
            from .host import host as _h
            for prefix in (_br.PREFIX, _br.CLOSED_PREFIX):
                try:
                    obj = _h.s3_client.get_object(Bucket=_h.bucket, Key=f"{prefix}{_br._safe(run_id)}.json")
                    launch = json.loads(obj['Body'].read().decode('utf-8'))
                    break
                except Exception:
                    launch = None
        if isinstance(launch, dict) and str(launch.get('user') or '') == str(uname or launch.get('user') or ''):
            how = settle_launch(launch, run_doc, exists=service.thread_exists, load=service.load_thread, save=service.save_thread,
                                ensure=service.ensure_thread, relist=service.relist_thread)
    except Exception:
        how = 'failed'
    return line, how


# ------------------------------------------------------------------ 2. a promise has a record
def _default_writer(key, doc):
    from .host import host as _h
    _h.s3_client.put_object(Bucket=_h.bucket, Key=key, Body=json.dumps(doc).encode('utf-8'), ContentType='application/json')


def _held_record(uname, question, *, kind='held', reason='', told=CALM_LINE, thread_id='', job_id='', now=None):
    """(key, doc) of the held record for one promise; nothing is written."""
    when = datetime.fromtimestamp(now or time.time(), timezone.utc)
    rid = hashlib.sha1(f"{uname}|{question}|{thread_id}|{job_id}|{when.isoformat()}".encode()).hexdigest()[:10]
    key = f"{HELD_PREFIX}{when.strftime('%Y-%m-%d')}/{when.strftime('%H%M%S')}_{rid}.json"
    doc = {'id': rid, 'user': str(uname or ''), 'question': str(question or '')[:400], 'told': str(told or '')[:400],
           'reason': str(reason or '')[:400], 'kind': str(kind or 'held'), 'opened_at': when.strftime('%Y-%m-%dT%H:%M:%SZ'),
           'due_by': (when + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ'), 'status': 'open'}
    if thread_id:
        doc['thread_id'] = str(thread_id)[:96]
    if job_id:
        doc['job_id'] = str(job_id)[:64]
    return key, doc


def _writer_for(uname, writer=None):
    """The writer a held record goes through, or None when none may be
    written (a probe identity, a test process)."""
    if writer is not None:
        return writer
    from . import probe_identity as _pid
    return _default_writer if _pid.may_record(uname) else None


def hold(uname, question, *, kind='held', reason='', told=CALM_LINE, thread_id='', job_id='', writer=None, now=None):
    """Open the held record behind a promise: the task the ops watch and
    the daily digest read, with its due time. Returns the record's key,
    or '' when none was written (a probe, a test process, a failure)."""
    try:
        writer = _writer_for(uname, writer)
        if writer is None:
            return ''
        key, doc = _held_record(uname, question, kind=kind, reason=reason, told=told, thread_id=thread_id, job_id=job_id, now=now)
        writer(key, doc)
        try:
            from flask import g as _g
            _g._pm_held_ref = key
        except Exception:
            pass
        return key
    except Exception:
        return ''


def _who():
    """The reader this request is for, from the request itself."""
    try:
        from flask import g as _g, session as _session
        for k in ('_pm_uname', '_pm_ask_user', '_pm_api_key_owner'):
            v = str(getattr(_g, k, '') or '').strip()
            if v:
                return v
        try:
            return str(_session.get('username') or _session.get('email') or '').strip()
        except Exception:
            return ''
    except Exception:
        return ''


def find_held(uname, question, *, within_s=1200, now=None, lister=None, getter=None):
    """The key of an open held record this reader already has for this
    question (opened in the last ``within_s`` seconds), or ''. One
    promise, one record: the reply's exit and the thread save both ask
    here before opening another."""
    try:
        if not uname or not str(question or '').strip():
            return ''
        if lister is None or getter is None:
            from .host import host as _h
            s3, bucket = _h.s3_client, _h.bucket

            def lister(prefix):       # noqa: E306
                r = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
                return [str(o.get('Key') or '') for o in (r.get('Contents') or [])]

            def getter(key):          # noqa: E306
                return json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8'))
        when = now or time.time()
        want = _squash(question)[:400]
        days = {datetime.fromtimestamp(when, timezone.utc).strftime('%Y-%m-%d'),
                datetime.fromtimestamp(when - within_s, timezone.utc).strftime('%Y-%m-%d')}
        for day in sorted(days, reverse=True):
            for key in sorted(lister(f"{HELD_PREFIX}{day}/") or [], reverse=True)[:200]:
                try:
                    doc = getter(key)
                except Exception:
                    continue
                if not isinstance(doc, dict) or str(doc.get('user') or '') != str(uname) or str(doc.get('status') or '') != 'open':
                    continue
                if _squash(doc.get('question'))[:400] != want:
                    continue
                try:
                    opened = datetime.strptime(str(doc.get('opened_at')), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()
                except Exception:
                    opened = 0
                if opened and 0 <= when - opened <= within_s:
                    return key
    except Exception:
        return ''
    return ''


def _has_artifact(raw):
    try:
        from . import order_truth as _ot
        return bool(_ot.has_artifact(raw))
    except Exception:
        return False


def back_calm(raw, *, uname='', question='', thread_id='', writer=None):
    """The one exit every reply passes: a calm reply with no job, no run
    and no held record behind it gets the held record. Returns the
    payload (``held_ref`` set when a record stands behind it)."""
    try:
        if not isinstance(raw, dict):
            return raw
        text = str(raw.get('reply') or raw.get('error') or '')
        if not is_calm(text.replace('Included with your plan. ', '', 1)) or _has_artifact(raw) or raw.get('held_ref'):
            return raw
        ref = ''
        try:
            from flask import g as _g
            ref = str(getattr(_g, '_pm_held_ref', '') or '')
            if not question:
                question = str(getattr(_g, '_pm_ask_text', '') or '')
        except Exception:
            pass
        uname = uname or _who()
        if not ref:
            ref = hold(uname, question, kind='promise', reason='the calm line went out with no job behind it; the answer is owed by email',
                       thread_id=thread_id, writer=writer)
        if ref:
            raw['held_ref'] = ref
    except Exception:
        pass
    return raw


def back_thread(uname, tid, incoming, stored=None, *, writer=None, load=None, finder=None):
    """A thread save from a tab: a calm line the tab wrote on its own (a
    fetch that failed, a blank turn) that is new to the stored thread
    gets a held record, stamped on the turn. ``load()`` reads the stored
    thread, called only when the incoming copy carries such a line.
    Returns the incoming turns (patched in place where a record was
    opened)."""
    try:
        if not isinstance(incoming, list):
            return incoming
        if not any(isinstance(t, dict) and str(t.get('role') or '') != 'user' and is_calm(t.get('text'))
                   and not _meta(t).get('held_ref') for t in incoming):
            return incoming
        if stored is None and callable(load):
            try:
                stored = load()
            except Exception:
                stored = None
            if stored is None:
                return incoming          # the stored thread could not be read: nothing is opened on a guess
        known = set()
        for t in stored or []:
            if isinstance(t, dict) and str(t.get('role') or '') != 'user':
                known.add((str(t.get('ts') or ''), _squash(t.get('text'))[:80]))
        last_q = ''
        for t in incoming:
            if not isinstance(t, dict):
                continue
            if str(t.get('role') or '') == 'user':
                last_q = str(t.get('text') or '')
                continue
            m = _meta(t)
            text = str(t.get('text') or '')
            if not is_calm(text) or m.get('held_ref') or m.get('withdrawn') or m.get('corrected_at') or m.get('run_id') \
                    or any(m.get(k) for k in _JOB_KEYS):
                continue
            if (str(t.get('ts') or ''), _squash(text)[:80]) in known:
                continue
            # the reply's own exit may have opened the record for this ask already: one promise, one record
            ref = (find_held(uname, last_q) if finder is None else finder(uname, last_q)) or hold(
                uname, last_q, kind='client', reason='a tab wrote the calm line into the thread with no job behind it',
                thread_id=tid, writer=writer)
            if ref:
                t['meta'] = dict(m, held_ref=ref)
    except Exception:
        pass
    return incoming


def job_failed(kind, uname, job_id, question='', *, land=None, writer=None, thread_id=''):
    """A read or a deck job died after its progress line was posted: the
    calm promise lands on the job's own progress turn (``land(payload)``
    is the job's thread landing) with the held record behind it, so the
    progress line does not outlive the job. A job whose answer had
    already landed (``land`` says 'already') opens no record: nothing is
    owed. Returns the record's key, or ''."""
    try:
        writer = _writer_for(uname, writer)
        if writer is None:
            return ''
        key, doc = _held_record(
            uname, question, kind=f"{kind}_failed", job_id=job_id, thread_id=thread_id,
            reason=f"the {kind} job ended without an answer after its progress line was posted; the answer is owed by email")
        how = None
        if callable(land):
            how = land({'success': True, 'action': 'answer', 'reply': CALM_LINE, 'followups': [], 'offer_deck': False, 'deck_angle': None,
                        'held_ref': key, 'job_failed': True})
        if how == 'already':
            return ''                    # the answer is in the thread: the failure came after it landed
        writer(key, doc)
        return key
    except Exception:
        return ''


def land_note(tid, uname, job_id, payload, *, service=None, now=None):
    """Land a held promise for a deck job that died: one server turn in
    the job's thread carrying the held record, and the deck's own
    progress line above it withdrawn. Nothing is written with no thread
    or no record. Returns True when the thread was written."""
    try:
        ref = str((payload or {}).get('held_ref') or '')
        if not (tid and uname and job_id and ref):
            return False
        if service is None:
            from . import service as service        # noqa: PLW0642
        turns = [dict(t) if isinstance(t, dict) else t for t in (service.load_thread(uname, tid) or [])]
        if any(isinstance(t, dict) and t.get('turn_id') == f"held-{job_id}" for t in turns):
            return False
        stamp = _iso(now)
        for t in reversed(turns):
            if not isinstance(t, dict):
                continue
            if str(t.get('role') or '') == 'user':
                break
            m = _meta(t)
            if promise_kind(t.get('text')) == 'deck' and not m.get('withdrawn') and not m.get('held_ref'):
                t['meta'] = dict(m, withdrawn=True, corrected_at=stamp, server_note=True, retired=str(t.get('text') or '')[:400])
                t['text'] = "This line is withdrawn: the deck did not finish. The note below has what happens next."
                break
        turns.append({'role': 'agent', 'text': str((payload or {}).get('reply') or CALM_LINE), 'ts': stamp, 'turn_id': f"held-{job_id}",
                      'meta': {'kind': 'held', 'server_note': True, 'held_ref': ref, 'failed_job': str(job_id)}})
        service.save_thread(uname, tid, turns)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------ 3. how a thread ends
def thread_tail(history):
    """How a thread ends: {'ends_on': 'answer' | 'promise' | 'user' |
    'empty', 'kind', 'text', 'ts', 'refs': {...}} where refs are the
    records the last agent turn carries (a run, a job, a held record, an
    outcome). The sweep checks each ref against its store."""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    turns = [t for t in turns if not _meta(t).get('withdrawn')]
    if not turns:
        return {'ends_on': 'empty'}
    last = turns[-1]
    if str(last.get('role') or '') == 'user':
        return {'ends_on': 'user', 'text': str(last.get('text') or '')[:200], 'ts': last.get('ts')}
    m = _meta(last)
    text = str(last.get('text') or '')
    kind = promise_kind(text)
    if m.get('landed_at') and not is_calm(text):
        kind = ''                                   # a landed read that opens "On ..." is the answer
    if m.get('build_outcome') or m.get('kind') in ('build_ready', 'build_outcome', 'deck'):
        kind = ''
    refs = {}
    for k in ('run_id', 'build_run_id', 'held_ref') + _JOB_KEYS:
        if m.get(k):
            refs[k] = str(m[k])
    ra = m.get('ride_along') if isinstance(m.get('ride_along'), dict) else {}
    if ra.get('deck_job'):
        refs['deck_job_id'] = str(ra['deck_job'])
    if ra.get('record'):
        refs['ride_along'] = str(ra['record'])
    question, status = '', str(m.get('status') or '')
    for t in reversed(turns[:-1]):
        if str(t.get('role') or '') == 'user':
            question = str(t.get('text') or '')[:300]
            break
        # a tab's "X is building." line carries no run: the run is on the build's own line just above it
        tm = _meta(t)
        if kind == 'build' and not refs.get('run_id') and tm.get('run_id'):
            refs['run_id'] = str(tm['run_id'])
            status = status or str(tm.get('status') or '')
    return {'ends_on': 'promise' if kind else 'answer', 'kind': kind, 'text': text[:240], 'ts': last.get('ts'), 'refs': refs,
            'question': question, 'status': status, 'landed': bool(m.get('landed_at'))}


_ALIVE_RUN = ('queued', 'pending', 'running', 'working', 'starting', 'tu_done', 'avid_done', 'addon_cuts', 'cut_done', 'subiq_done')
_ALIVE_JOB = ('queued', 'working', 'running', 'pending', 'started')
IN_FLIGHT_S = 45 * 60          # a job with no record yet is given this long before its line counts as bare
STALE_BUILD_S = 12 * 3600      # no build runs this long: a thread that still reads as building is stale


def classify_tail(tail, *, run_status='', job_status='', held=None, ride_along=None, age_s=None):
    """Whether the promise a thread ends on has something behind it.
    Returns (verdict, why): 'backed', 'unbacked' or 'not_a_promise'.

    ``run_status`` is the engine's status for the run the last turn
    carries; ``job_status`` the status of its read or deck job; ``held``
    a held record that stands behind it (matched by its key, its job or
    its reader and question); ``ride_along`` a pending deliverable
    record for the thread; ``age_s`` how long ago the line was written."""
    t = tail if isinstance(tail, dict) else {}
    if t.get('ends_on') != 'promise':
        return 'not_a_promise', ''
    kind = str(t.get('kind') or '')
    refs = t.get('refs') if isinstance(t.get('refs'), dict) else {}
    rs, js = str(run_status or '').lower(), str(job_status or '').lower()
    if held:
        return 'backed', 'a held record with a due time'
    if kind == 'build' or refs.get('run_id'):
        if rs in _ALIVE_RUN and age_s is not None and age_s > STALE_BUILD_S:
            return 'unbacked', f"the thread still reads as building {int(age_s // 3600)} hours on"
        if rs in _ALIVE_RUN:
            return 'backed', f"the build is {rs}"
        if rs == 'complete':
            return 'unbacked', 'the build finished and the thread still reads as building'
        if rs in DEAD:
            return 'unbacked', f"the build ended ({rs}) and the thread still reads as building"
        if refs.get('run_id'):
            return 'unbacked', 'the engine has no record of the run the line names'
    if any(refs.get(k) for k in _JOB_KEYS) or kind in ('read', 'deck'):
        if js in _ALIVE_JOB:
            return 'backed', f"the job is {js}"
        if js == 'held':
            return 'backed', 'the job is held for another pass'
        if js in ('done', 'complete', 'completed'):
            return 'unbacked', 'the job finished and its answer is not on the line that promised it'
        if js in ('error', 'failed'):
            return 'unbacked', 'the job ended in an error and nothing replaced its progress line'
        if any(refs.get(k) for k in _JOB_KEYS):
            return 'unbacked', 'no record of the job the line names'
    if ride_along:
        return 'backed', 'a deliverable on record for the read'
    if age_s is not None and age_s < IN_FLIGHT_S and kind in ('read', 'deck', 'build'):
        return 'backed', 'written minutes ago; its job may not have reported yet'
    return 'unbacked', ('the calm line with no job, no run and no held record' if kind in ('calm', 'email')
                        else f"a {kind or 'progress'} line with no job behind it")
