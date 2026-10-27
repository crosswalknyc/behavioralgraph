"""Focus Group IQ room jobs: stalls, resumes, the failed state, and the
duplicate-question guard (2026-10-26).

Live use the night the room engine shipped: Jenna asked the same
question three times in one room within a minute (each click started
another job) and, a minute in, all three read 'researching'. The jobs
were running (the first finished in two and a half minutes; three
parallel research calls on the strong model are slow), but two things
were missing and live here:

  Stalls and the failed state. A room job runs on a background thread
  of the web process. If that process is recycled or the deploy
  restarts, the thread dies and the record would read 'researching'
  forever. ``check_stale`` runs whenever a job or a session is read:
  a running job with no update for STALE_S is picked back up once
  (``resume_job`` rebuilds the question from the job document and the
  twin and runs the stages again on this process, reusing the cached
  research); a job that stalls again, or is older than MAX_AGE_S, is
  marked ``stage: failed`` on the job document and the record with one
  plain line (FAILED_LINE). Nothing is charged again: the question
  counts as asked and the user asks again.

  Duplicates. ``find_duplicate`` matches a question by its normalized
  words and its stimulus (price, cast, platform). While the first is in
  flight an identical ask returns the in-flight record (same id, same
  job) instead of starting another; after it finishes an identical ask
  in the same session returns the finished record with an 'asked again'
  note, unless the moderator's Ask again chip (``ask_again``) is used.
  The guard runs in ``digital_twin_panel.ask`` and, before any charge,
  in the API routes and the Prometheus lane.
"""
from __future__ import annotations

import re
import threading
import time
import traceback
from typing import Optional

STALE_S = 300.0        # a running job with no update for five minutes is picked back up
MAX_AGE_S = 1500.0     # a job older than twenty-five minutes that is still running has failed
RESUMES_MAX = 1
IN_FLIGHT_STAGES = ('researching', 'the room is thinking', 'moderator is reading', 'picking it back up')
FAILED_LINE = 'The room did not finish this question. Ask it again and it runs fresh.'
ASKED_AGAIN_NOTE = 'You asked this earlier in this session; here is that answer. Use Ask again to put it to the room fresh.'
IN_FLIGHT_NOTE = 'The room is already on this question; here is where it stands.'


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


# ------------------------------------------------------------- duplicates
def question_key(q: str, stimulus: Optional[dict] = None) -> str:
    """The identity of a question in a room: its words (case, spacing
    and end punctuation aside) plus the stimulus that changes the
    answer (price, cast, platform)."""
    text = str(q or '').lower()
    words = ' '.join(re.sub(r"[^\w$%.]+", ' ', text).split()).rstrip(' ?.!')
    st = stimulus or {}
    bits = []
    for k in ('price', 'cast', 'platform'):
        v = st.get(k)
        if v in (None, '', 0):
            continue
        if k == 'price' and re.search(r"\$\s?\d", text):
            continue   # the words already carry the price; the engine copies it onto the record's stimulus
        if k == 'platform' and re.search(r"\b" + re.escape(str(v).strip().lower()) + r"\b", text):
            continue
        try:
            v = f"{float(v):g}" if k == 'price' else str(v).strip().lower()
        except (TypeError, ValueError):
            v = str(v).strip().lower()
        bits.append(f"{k}={v}")
    return words + ('|' + '&'.join(bits) if bits else '')


def in_flight(rec: Optional[dict]) -> bool:
    return bool(rec and rec.get('job_id') and str(rec.get('stage') or '') in IN_FLIGHT_STAGES)


def find_duplicate(session: dict, q: str, stimulus: Optional[dict] = None) -> Optional[dict]:
    """The most recent record in the room asking the same thing (same
    words, same stimulus), in flight or finished; never a failed one."""
    key = question_key(q, stimulus)
    if not key.strip('| '):
        return None
    for rec in reversed(list((session or {}).get('questions') or [])):
        if str(rec.get('stage') or '') in ('failed', 'stopped'):
            continue
        if question_key(rec.get('q'), rec.get('stimulus')) == key:
            return rec
    return None


def mark_asked_again(rec: dict) -> dict:
    """The record a repeated ask returns: the same record, with the
    plain note and the time it was asked again (the record itself is
    not re-answered)."""
    rec.setdefault('asked_again', []).append(_now())
    rec['note'] = IN_FLIGHT_NOTE if in_flight(rec) else ASKED_AGAIN_NOTE
    return rec


def wants_fresh(text: str) -> bool:
    """The chat words that mean 'put it to the room again' (the Ask
    again chip, 're-ask')."""
    return bool(re.search(r"^\s*(ask (?:it |them |the room )?again|re-?ask|again[,:]?\s)", str(text or ''), re.I))


# ------------------------------------------------------------ the stalls
def _s3_default():
    from migration import digital_twin_room as R
    return R._s3_default()


def _job_write(s3, job: dict):
    from migration import digital_twin_room as R
    job = dict(job)
    job['updated_at'] = time.time()
    R._put_json(s3, R.job_key(str(job.get('job_id'))), job)
    return job


def _record_update(s3, twin_key: str, session_id: str, qid: str, mutate) -> Optional[dict]:
    """Load the twin, mutate the one record in place, save. Returns the
    record or None."""
    try:
        from migration import digital_twin_synthesis as SYN
        payload = SYN.load_twin(s3, twin_key)
        sess = next((s for s in (payload or {}).get('panel_sessions') or [] if s.get('id') == session_id), None)
        rec = next((q for q in (sess or {}).get('questions') or [] if q.get('id') == qid), None)
        if rec is None:
            return None
        mutate(rec)
        sess['updated_at'] = _now()
        SYN.save_twin(s3, twin_key, payload)
        return rec
    except Exception as e:
        print(f"[twin-room-jobs] record update skipped: {e}")
        return None


def fail_record(rec: dict, reason: str = '') -> dict:
    rec['stage'] = 'failed'
    rec['read'] = FAILED_LINE
    rec['finished_at'] = _now()
    rec.setdefault('trail', {})['failed'] = {'at': _now(), 'reason': str(reason or 'stalled')[:200]}
    return rec


def fail_job(s3, job: dict, reason: str = 'stalled') -> dict:
    """The failed state on the job document and the record: stage
    'failed', one plain line, nothing left reading 'researching'."""
    from migration import digital_twin_room as R
    job = dict(job)
    q = dict(job.get('question') or {})
    fail_record(q, reason)
    q.pop('trail', None)
    job.update({'status': 'error', 'stage': 'failed', 'stage_label': 'Did not finish', 'error': FAILED_LINE, 'finished_at': time.time(), 'question': q, 'failed_reason': str(reason)[:200]})
    job = _job_write(s3, job)
    _record_update(s3, str(job.get('twin_key') or job.get('s3_key') or ''), str(job.get('session_id') or ''), str(job.get('question_id') or ''), lambda r: fail_record(r, reason))
    print(f"[twin-room-jobs] {job.get('job_id')} failed: {reason}")
    return job


def is_stale(job: dict, now: Optional[float] = None) -> bool:
    now = now or time.time()
    if str(job.get('status') or '') != 'running':
        return False
    updated = float(job.get('updated_at') or job.get('created_at') or now)
    return (now - updated) > STALE_S


def is_too_old(job: dict, now: Optional[float] = None) -> bool:
    now = now or time.time()
    if str(job.get('status') or '') != 'running':
        return False
    created = float(job.get('created_at') or job.get('updated_at') or now)
    return (now - created) > MAX_AGE_S


def resume_job(s3, job_id: str, *, model_fn=None, run_async: bool = False, counted: bool = False) -> Optional[dict]:
    """Pick a stalled question back up from its job document and the
    twin: the same record (id, job id, words), the stages run again on
    this process (the research brief is cached, so it is quick). Returns
    the record (finished when run_async is False, else as it stands)."""
    from migration import digital_twin_room as R
    from migration import digital_twin_synthesis as SYN
    from migration.digital_twin_engine import VERSION
    s3 = s3 if s3 is not None else _s3_default()
    job = R.job_read(s3, job_id)
    if not job or str(job.get('op') or '') != 'panel_ask':
        return None
    twin_key = str(job.get('twin_key') or job.get('s3_key') or '')
    sid, qid = str(job.get('session_id') or ''), str(job.get('question_id') or '')
    payload = SYN.load_twin(s3, twin_key)
    sess = next((s for s in (payload or {}).get('panel_sessions') or [] if s.get('id') == sid), None)
    if not payload or not sess:
        return fail_job(s3, job, 'the room could not be found').get('question')
    idx = next((i for i, q in enumerate(sess.get('questions') or []) if q.get('id') == qid), None)
    if idx is None:
        return fail_job(s3, job, 'the question could not be found').get('question')
    rec = sess['questions'][idx]
    twins_by_key = {twin_key: payload}
    for tk in sess.get('twin_keys') or []:
        if tk and tk not in twins_by_key:
            t = SYN.load_twin(s3, tk)
            if t:
                twins_by_key[tk] = t
    group = sess.get('group') or {}
    twin = twins_by_key.get(twin_key) or next(iter(twins_by_key.values()), {})
    last = sess['questions'][idx - 1] if idx > 0 else None
    # the record starts the stages over: the seats answer again (their memory of earlier questions is intact)
    for k, v in (('options', []), ('distribution', []), ('by_segment', []), ('read', ''), ('voices', []), ('answers', []), ('probes_next', []), ('facts_used', []), ('tally', [])):
        rec[k] = v
    rec['finished_at'] = None
    trail = rec.setdefault('trail', {})
    trail.setdefault('resumes', []).append({'at': _now(), 'from_stage': job.get('stage')})
    rec['stage'] = 'picking it back up'
    ctx = {'rec': rec, 'session': sess, 'twins_by_key': twins_by_key, 'twin': twin, 'roster': list(group.get('roster') or []), 'models': R.Models(model_fn=model_fn),
           's3': s3, 'twin_key': twin_key, 'job_id': job_id, 'seed': f"{sid}|{idx}|{rec.get('q')}|{VERSION}|{R.ROOM_VERSION}", 'session_seed': f"room|{sid}|{VERSION}",
           'last': last, 'asked_by': str(job.get('user') or rec.get('asked_by') or ''), 'subject': R._twin_name(twin), 't0': time.time(), 'created_at': float(job.get('created_at') or time.time()),
           'notify': job.get('notify') or {}, 'wait_for_save': False}
    resumes = int(job.get('resumes') or 0) + (0 if counted else 1)     # check_stale counts the resume before it starts the thread
    ctx['resumes'] = resumes
    job2 = dict(job)
    job2.update({'status': 'running', 'stage': 'picking it back up', 'stage_label': 'Picking the question back up', 'resumes': resumes, 'resumed_at': time.time(),
                 'question': {k: v for k, v in rec.items() if k != 'trail'}})
    _job_write(s3, job2)
    if run_async:
        threading.Thread(target=R.run_question, args=(ctx,), daemon=True, name=f"twin-room-resume-{job_id}").start()
        return rec
    return R.run_question(ctx)


def check_stale(s3, job: Optional[dict], *, resume: bool = True, now: Optional[float] = None) -> Optional[dict]:
    """Called on every read of a room job: a stalled job is picked back
    up once on this process (in the background), a job that stalls
    again or is too old is marked failed. Returns the job document as
    it should be served."""
    if not job or str(job.get('op') or '') != 'panel_ask' or str(job.get('status') or '') != 'running':
        return job
    now = now or time.time()
    if is_too_old(job, now):
        return fail_job(s3, job, 'ran past the time limit')
    if not is_stale(job, now):
        return job
    if resume and int(job.get('resumes') or 0) < RESUMES_MAX:
        try:
            job2 = dict(job)
            job2.update({'stage': 'picking it back up', 'stage_label': 'Picking the question back up', 'resumes': int(job.get('resumes') or 0) + 1, 'resumed_at': now})
            _job_write(s3, job2)
            threading.Thread(target=_resume_quiet, args=(s3, str(job.get('job_id'))), daemon=True, name=f"twin-room-resume-{job.get('job_id')}").start()
            return job2
        except Exception:
            traceback.print_exc()
    return fail_job(s3, job, 'stalled')


def _resume_quiet(s3, job_id: str):
    try:
        resume_job(s3, job_id, run_async=False, counted=True)
    except Exception as e:
        traceback.print_exc()
        try:
            from migration import digital_twin_room as R
            job = R.job_read(s3, job_id)
            if job and str(job.get('status') or '') == 'running':
                fail_job(s3, job, f"resume failed: {str(e)[:120]}")
        except Exception:
            pass


def sweep_session(s3, twin_key: str, session: dict, *, resume: bool = True, now: Optional[float] = None) -> int:
    """On a session read: every in-flight record is checked against its
    job document; a missing document or a stale job is handled by
    ``check_stale`` and the record in hand is brought up to date.
    Returns how many records changed."""
    from migration import digital_twin_room as R
    changed = 0
    now = now or time.time()
    for rec in (session or {}).get('questions') or []:
        if not in_flight(rec):
            continue
        job = R.job_read(s3, str(rec.get('job_id')))
        if job is None:
            started = rec.get('started_at') or rec.get('asked_at') or ''
            try:
                t_start = time.mktime(time.strptime(started, '%Y-%m-%dT%H:%M:%SZ')) - time.timezone
            except Exception:
                t_start = 0.0
            if t_start and (now - t_start) > STALE_S:
                fail_record(rec, 'no job document')
                _record_update(s3, twin_key, str(session.get('id') or ''), str(rec.get('id') or ''), lambda r: fail_record(r, 'no job document'))
                changed += 1
            continue
        job2 = check_stale(s3, job, resume=resume, now=now)
        if job2 is not job:
            q2 = (job2 or {}).get('question') or {}
            if q2.get('stage') == 'failed':
                fail_record(rec, (job2 or {}).get('failed_reason') or 'stalled')
            else:
                rec['stage'] = q2.get('stage') or rec.get('stage')
            changed += 1
        elif str(job.get('stage') or '') == 'failed' or str(job.get('status') or '') == 'error':
            fail_record(rec, job.get('failed_reason') or 'stalled')   # the job failed elsewhere; the record carries the plain line
            changed += 1
        elif str(job.get('stage') or '') and job.get('stage') != rec.get('stage'):
            rec['stage'] = job['stage']   # the thread is ahead of the saved payload
            if job.get('stage') == 'done' and isinstance(job.get('question'), dict) and job['question'].get('read'):
                for k, v in job['question'].items():
                    if k not in ('trail',):
                        rec[k] = v
            changed += 1
    return changed


__all__ = ['question_key', 'find_duplicate', 'in_flight', 'mark_asked_again', 'wants_fresh', 'check_stale', 'is_stale', 'is_too_old', 'fail_job', 'fail_record',
           'resume_job', 'sweep_session', 'STALE_S', 'MAX_AGE_S', 'RESUMES_MAX', 'FAILED_LINE', 'ASKED_AGAIN_NOTE', 'IN_FLIGHT_NOTE', 'IN_FLIGHT_STAGES']
