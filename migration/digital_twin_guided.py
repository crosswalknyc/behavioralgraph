"""Predictive Analytics (Digital Twins IQ, 2026-11-18).

Jenna, verbatim: "a digital twins wizard that is like focus group for
dummies. where you would go through a wizard it would ask you what your
audience is. then what your end goal is in terms of like what business
question do you want to understand? like is it if I do x will y happen,
is it will x win then prometheus builds the room and runs through a list
of questions aimed at starting macro and getting micro to give you the
responses and prediction of ending with a full focus group outcome of
your total business question goal." And the same day: "let's call the
wizard Predictive Analytics and have it kinda be its own entry to the
product so you dont have to go through focus group unless you want that
depth and control so maybe it's under digital twins and is an option
that just says predictive analytics and then walks you through the
wizard and gives you an end report of the full procss?"

Its own tab under Digital Twins IQ (``predict``). The focus group stays
for the depth and the control; this is the walk-through: the audience,
the business question, the room planned and run by Prometheus, and the
report of the whole process at the end (``report`` for the page, the
PDF for the file).

Three parts, each plain:

  plan      the audience (a twin) and the business question in the
            user's words, with its kind (If I do X, will Y happen?
            Will X win? Should I do X? What will they pay? Something
            else) go to the planning desk, which writes the goal back
            in one sentence, says what result would count as yes, and
            lays out five to eight questions for the room from the wide
            to the narrow: where the audience stands today, the pull
            and the friction, the specific scenario, and last the one
            question that settles the goal. Deterministic plan when no
            model answers.
  run       the room opens as one group (charged as any room is) and
            the questions run in order on a background thread, each one
            the room's own ask (research, the seats answering in
            character, the read), saved as it lands; the job document
            carries the progress the page polls. A run that stops
            resumes from the first unanswered question; nothing is
            asked twice.
  outcome   when the last question lands the outcome desk reads the
            whole room (every split, read and voice) and answers the
            business question: the call in one sentence, why, the
            numbers it rests on, who in the room drove it, what would
            change it, and one follow-up worth asking. Deterministic
            fallback from the last question's split. Reader copy is
            scrubbed the way every room line is.

Nothing here names a method or a source; the outcome is the room's
answer, stated plainly (no-modeled-or-source-language.mdc).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
import uuid
from typing import Callable, Optional

VERSION = 1
PRODUCT = 'Predictive Analytics'
MIN_QUESTIONS = 5
MAX_QUESTIONS = 8
# A run lives on a thread in the app process; a deploy restarts the process and the thread dies with it (the first Prime
# Video run, 2026-11-18: question 1 landed at 20:32, the app redeployed, and the room sat 'running' for three hours with
# nothing running). A run is STALE when its status says running, no thread in this process holds it, its job document
# has not moved for STALE_S, and no question of its own is still making progress. A stale run is picked back up at the
# first unanswered question on any read of the room or the report, and by the sweeper every SWEEP_S from the running
# index; nothing is charged again and nothing finished is asked twice.
STALE_S = 240.0
SWEEP_S = 90.0
RUNNING_KEY = 'system/digital_twins/guided_running.json'
# The app runs as several worker processes (gunicorn, three workers), each with its own threads and its own memory. A
# per-process registry is not enough: the first pick-up (2026-11-18, 23:58 UTC) fired in more than one worker at once and
# two copies of the run raced, asking the same questions twice. One run holds ONE lock on S3 (a small document with the
# owner and a heartbeat the runner refreshes every HEARTBEAT_S); a run is stale only when its lock is missing or its
# heartbeat is older than LOCK_STALE_S, whichever process is asking. Acquiring is a conditional write (IfNoneMatch on a
# missing lock, IfMatch on a stale one), so two processes cannot both win.
LOCK_PREFIX = 'system/digital_twins/guided_locks/'
LOCK_STALE_S = 150.0
HEARTBEAT_S = 30.0
LIVE: dict = {}        # job_id -> True while a thread in this process runs it (the fast path; the lock is the truth)
_LIVE_LOCK = threading.Lock()
# the reader's words for the three levels of the plan (the wide to the narrow)
LEVEL_WORDS = {'macro': 'The big picture', 'mid': 'Closer in', 'micro': 'The question itself'}
CALL_WORDS = {'yes': 'Yes', 'lean yes': 'Leaning yes', 'lean no': 'Leaning no', 'no': 'No', 'split': 'Split'}
KINDS = (
    ('effect', 'If I do X, will Y happen?', 'A change you could make and the result you want from it.',
     'If we add a holiday boxed set at Target, will more people buy season two?'),
    ('contest', 'Will X win?', 'Two or more options; which one the audience picks.',
     'Will the Flashdance cut or the NCIS cut win them over for a Lululemon spot?'),
    ('go', 'Should I do X?', 'A go or no-go call on one move.',
     'Should we sign her for a two-year activewear deal?'),
    ('price', 'What will they pay?', 'A price, a tier or a bundle.',
     'Will this audience pay $45 for a hoodie from her collection?'),
    ('open', 'Something else', 'Any question about this audience.',
     'What would it take for this audience to see her as a fashion name, not a nostalgia act?'),
)
KIND_IDS = tuple(k[0] for k in KINDS)
LEVELS = ('macro', 'mid', 'micro')
Q_KINDS = ('yes_no', 'multiple_choice', 'open', 'rating')

PLAN_SYSTEM = """You plan a focus group for one business question. The room is a set of archetype representatives who each stand for an archetype in the audience; they answer one question at a time, in character, and the room reports the split, a read and their voices.

Write the plan as JSON only:
{"goal": one plain sentence that restates the business question as the thing the user needs to know,
 "success": one plain sentence saying what result from the room would count as a yes (or as X winning),
 "questions": [ {"q": the question as the room will hear it, "kind": "yes_no" | "multiple_choice" | "open" | "rating", "options": [two to five short options, multiple_choice only], "level": "macro" | "mid" | "micro", "tests": one short line on what this question tells us} ],
 "final": the zero-based index of the question that settles the goal}

Rules.
- Five to eight questions, in order from the wide to the narrow: first where the audience stands today (habit, awareness, what they already do), then the pull and the friction (what moves them, what holds them back), then the specific scenario the business question describes, and last the one question that settles it (a yes or no, or a pick between the options). The last question IS the business question, put to one person in plain words.
- Each question is something one person can answer about themselves in a sentence; never a market question ("what share of people...").
- Plain English a 7th grader follows. Short. No jargon, no brand-speak.
- Options for a pick are short, distinct, and cover the real answers including "neither" or "not for me" where honest.
- Never these words: synthetic, synth, model, modeled, estimated, research, panel, sample, persona, segment, cohort, confidence, data, survey. An archetype is called an archetype, never a kind of person. No em dashes.
- Keep the user's names, prices, titles and dates exactly as written.
"""

OUTCOME_SYSTEM = """You are the moderator writing the outcome of a focus group that ran to answer one business question. You have every question the room was asked, the published split of the audience on each (shares, the people behind each answer, the seats), the read on each, and voices from the seats.

Answer the business question for the person who asked it. JSON only:
{"call": one of "yes", "lean yes", "lean no", "no", "split", or the exact winning option when the goal was a pick,
 "answer": one sentence that states the call plainly with the one number that carries it,
 "why": two or three sentences on why the room landed there, in plain words,
 "numbers": [three to five short lines, each carrying one number from the record exactly as published (a share, a people count, a seat count), the most decisive first],
 "who": one sentence on which archetypes in the room drove the result and which pushed the other way,
 "would_change": one sentence on what would most likely move the result,
 "next": one follow-up question worth asking this room next}

Rules.
- Use only numbers that appear in the record; never invent or round them to a looser figure. State shares in plain words when it helps ("about three in ten") and keep the exact figure beside it.
- Say what the room said; do not hedge the call with "might" or "could" when the split is clear, and do not overstate a narrow one.
- Plain English a 7th grader follows. Never these words: synthetic, synth, model, modeled, estimated, research, panel, sample, persona, segment, cohort, confidence, data, survey, prediction. An archetype is called an archetype, never a kind of person. No em dashes.
"""

_BANNED = re.compile(r"\b(synthetic|synth|modell?ed|model|estimated|research(?:ed)?|panel|sample|personas?|segments?|cohorts?|confidence|survey|prediction)\b", re.I)   # archetype is a reader word (Jenna 2026-11-20)


def kind_of(text: str, hint: str = '') -> str:
    """The kind of business question: the chip when given, else read
    from the words."""
    h = str(hint or '').strip().lower()
    if h in KIND_IDS:
        return h
    t = ' ' + ' '.join(str(text or '').lower().split()) + ' '
    if re.search(r"\b(will|would) .* (win|beat|pick|prefer|choose)\b| or \b|which (one|cut|option|version)", t):
        return 'contest'
    if re.search(r"\$\s?\d|\bprice|\bpay\b|\bcost|\bcheaper|\bexpensive|\bsubscri", t):
        return 'price'
    if re.search(r"\bshould (i|we)\b|\bgo or no\b|\bgreenlight\b|\blaunch\b|\bsign\b|\brenew\b", t):
        return 'go'
    if re.search(r"\bif (i|we)\b|\bwould (it|that|this|they|people)\b|\bwill (it|that|this|they|people|more|fewer)\b", t):
        return 'effect'
    return 'open'


def _clean(s) -> str:
    return ' '.join(str(s or '').replace('\u2014', ', ').replace('\u2013', '-').split())


def _subject(twin: dict) -> str:
    m = (twin or {}).get('meta') or {}
    return str(m.get('subject') or m.get('name') or 'this audience')


def _kinds_words(twin: dict, n: int = 6) -> list:
    groups = ((twin or {}).get('archetypes') or {}).get('groups') or []
    return [str(g.get('label') or '') for g in sorted(groups, key=lambda g: -float(g.get('share_pct') or 0))[:n] if g.get('label')]


# ---------------------------------------------------------------- the plan
def deterministic_plan(twin: dict, goal: str, kind: str) -> dict:
    """A plan with no model: six questions from the wide to the narrow
    around the goal as written. Good enough to run; the desk writes a
    better one when it answers."""
    subj = _subject(twin)
    g = _clean(goal).rstrip('?.!')
    goal_line = (g[0].upper() + g[1:] + '?') if g else f"What does this audience make of {subj}?"
    qs = [
        {'q': f"When you think of {subj} right now, what comes to mind first?", 'kind': 'open', 'level': 'macro', 'tests': 'Where the audience stands today.'},
        {'q': f"How closely do you follow {subj} these days?", 'kind': 'multiple_choice', 'options': ['Closely, I keep up', 'Now and then', 'Rarely', 'Not at all'], 'level': 'macro', 'tests': 'How much of the audience is paying attention.'},
        {'q': f"What would make you spend more time or money on {subj} this year?", 'kind': 'open', 'level': 'mid', 'tests': 'The pull.'},
        {'q': f"What holds you back from spending more time or money on {subj}?", 'kind': 'open', 'level': 'mid', 'tests': 'The friction.'},
    ]
    if kind == 'contest':
        qs.append({'q': f"Thinking about this: {goal_line} Which way do you lean, and why?", 'kind': 'open', 'level': 'micro', 'tests': 'The scenario in their own words.'})
        qs.append({'q': goal_line, 'kind': 'multiple_choice', 'options': ['The first', 'The second', 'Neither'], 'level': 'micro', 'tests': 'The pick that settles it.'})
    elif kind == 'price':
        qs.append({'q': f"Thinking about this: {goal_line} What would you pay, if anything?", 'kind': 'open', 'level': 'micro', 'tests': 'The price in their own words.'})
        qs.append({'q': goal_line, 'kind': 'yes_no', 'level': 'micro', 'tests': 'The yes or no that settles it.'})
    else:
        qs.append({'q': f"Thinking about this: {goal_line} How would that change what you do?", 'kind': 'open', 'level': 'micro', 'tests': 'The scenario in their own words.'})
        qs.append({'q': goal_line, 'kind': 'yes_no', 'level': 'micro', 'tests': 'The yes or no that settles it.'})
    return {'version': VERSION, 'method': 'deterministic', 'kind': kind, 'goal': goal_line,
            'success': 'A clear majority of the audience on the yes side of the last question.' if kind != 'contest' else 'One option ahead of the rest on the last question by a clear margin.',
            'questions': qs, 'final': len(qs) - 1}


def _norm_plan(data: dict, twin: dict, goal: str, kind: str) -> Optional[dict]:
    if not isinstance(data, dict):
        return None
    qs_in = data.get('questions')
    if not isinstance(qs_in, list):
        return None
    qs = []
    for i, q in enumerate(qs_in):
        if not isinstance(q, dict):
            continue
        text = _clean(q.get('q'))
        if not text or _BANNED.search(text):
            continue
        qk = str(q.get('kind') or 'open').strip().lower()
        if qk not in Q_KINDS:
            qk = 'open'
        opts = [_clean(o) for o in (q.get('options') or []) if _clean(o)] if qk == 'multiple_choice' else []
        if qk == 'multiple_choice' and len(opts) < 2:
            qk = 'open'
            opts = []
        lvl = str(q.get('level') or '').strip().lower()
        if lvl not in LEVELS:
            lvl = LEVELS[min(2, i * 3 // max(1, len(qs_in)))]
        row = {'q': text if text.endswith(('?', '.')) else text + '?', 'kind': qk, 'level': lvl, 'tests': _clean(q.get('tests'))[:160]}
        if opts:
            row['options'] = opts[:5]
        qs.append(row)
    if len(qs) < MIN_QUESTIONS:
        return None
    qs = qs[:MAX_QUESTIONS]
    try:
        final = int(data.get('final'))
    except (TypeError, ValueError):
        final = len(qs) - 1
    if not (0 <= final < len(qs)):
        final = len(qs) - 1
    goal_line = _clean(data.get('goal')) or deterministic_plan(twin, goal, kind)['goal']
    success = _clean(data.get('success')) or deterministic_plan(twin, goal, kind)['success']
    if _BANNED.search(goal_line) or _BANNED.search(success):
        return None
    return {'version': VERSION, 'method': 'reasoned', 'kind': kind, 'goal': goal_line, 'success': success, 'questions': qs, 'final': final}


def plan(twin: dict, goal: str, kind: str = '', *, models=None, call: Optional[Callable] = None) -> dict:
    """The plan for a business question on this twin. ``call(system,
    user, max_tokens, temperature) -> dict | None`` is the planning
    desk; when none is given the room's models are used (never under
    REGRESSION_TEST_MODE). Deterministic when nothing answers."""
    kind = kind_of(goal, kind)
    goal = _clean(goal)[:400]
    fallback = deterministic_plan(twin, goal, kind)
    if call is None:
        try:
            from migration import digital_twin_room as ROOM
            if models is not None or not ROOM.regression_mode():
                M = models or ROOM.Models()

                def call(system, user, max_tokens, temperature):   # noqa: E306
                    data, _rec = M.call('guided', system, user, max_tokens=max_tokens, temperature=temperature, tools=None)
                    return data
        except Exception:
            call = None
    if call is None:
        return fallback
    try:
        from migration.digital_twin_room import _twin_brief
        brief = _twin_brief(twin)
    except Exception:
        brief = {'name': _subject(twin)}
    body = {'audience': brief, 'archetypes_in_the_room': _kinds_words(twin, 8), 'business_question': goal,
            'question_kind': dict((k[0], k[1]) for k in KINDS).get(kind, 'Something else'), 'today': time.strftime('%Y-%m-%d', time.gmtime())}
    try:
        data = call(PLAN_SYSTEM, json.dumps(body, default=str)[:14000], 2400, 0.3)
    except Exception as e:
        print(f"[twin-guided] plan desk failed: {str(e)[:120]}")
        data = None
    out = _norm_plan(data, twin, goal, kind)
    if out is None:
        return fallback
    out['asked'] = goal
    return out


# ----------------------------------------------------------------- the run
def is_live(job_id: str) -> bool:
    with _LIVE_LOCK:
        return bool(LIVE.get(str(job_id or '')))


def _set_live(job_id: str, on: bool) -> None:
    with _LIVE_LOCK:
        if on:
            LIVE[str(job_id)] = True
        else:
            LIVE.pop(str(job_id), None)


def _index_read(s3) -> dict:
    try:
        from prometheus.digital_twin_job import _s3 as _host_s3
        s3b, bucket = _host_s3() if s3 is None else (s3, 'dashboard-inputs')
    except Exception:
        s3b, bucket = s3, 'dashboard-inputs'
    if s3b is None:
        return {}
    try:
        return json.loads(s3b.get_object(Bucket=bucket, Key=RUNNING_KEY)['Body'].read().decode('utf-8')) or {}
    except Exception:
        return {}


def _index_write(s3, data: dict) -> None:
    try:
        from prometheus.digital_twin_job import _s3 as _host_s3
        s3b, bucket = _host_s3() if s3 is None else (s3, 'dashboard-inputs')
    except Exception:
        s3b, bucket = s3, 'dashboard-inputs'
    if s3b is None:
        return
    try:
        s3b.put_object(Bucket=bucket, Key=RUNNING_KEY, Body=json.dumps(data, ensure_ascii=False).encode('utf-8'), ContentType='application/json')
    except Exception as e:
        if os.environ.get('REGRESSION_TEST_MODE', '').strip().lower() not in ('1', 'true', 'yes'):
            print(f"[twin-guided] running index write skipped: {str(e)[:120]}")


def _index_add(s3, job_id: str, key: str, sid: str, username: str) -> None:
    """The running index: every guided run in flight, so a sweeper on a
    fresh process can find and pick up the ones the old process lost."""
    data = _index_read(s3)
    data[str(job_id)] = {'key': key, 'session_id': sid, 'user': username, 'since': time.time()}
    _index_write(s3, data)


def _index_remove(s3, job_id: str) -> None:
    data = _index_read(s3)
    if str(job_id) in data:
        data.pop(str(job_id), None)
        _index_write(s3, data)


def _lock_key(job_id: str) -> str:
    return f"{LOCK_PREFIX}{job_id}.json"


def _s3_pair(s3):
    try:
        from prometheus.digital_twin_job import _s3 as _host_s3
        return _host_s3() if s3 is None else (s3, 'dashboard-inputs')
    except Exception:
        return s3, 'dashboard-inputs'


def _lock_read(s3, job_id: str):
    """(lock document, etag) or (None, None)."""
    s3b, bucket = _s3_pair(s3)
    if s3b is None:
        return None, None
    try:
        r = s3b.get_object(Bucket=bucket, Key=_lock_key(job_id))
        doc = json.loads(r['Body'].read().decode('utf-8')) or {}
        return doc, str(r.get('ETag') or '').strip('"') or None
    except Exception:
        return None, None


def lock_fresh(doc, now: Optional[float] = None) -> bool:
    if not isinstance(doc, dict):
        return False
    return (float((now or time.time())) - float(doc.get('heartbeat') or 0)) < LOCK_STALE_S


def _lock_acquire(s3, job_id: str, owner: str) -> bool:
    """Take the run's lock: write it when it is missing (IfNoneMatch), take
    it over when its heartbeat is stale (IfMatch on the etag we read), keep
    it when we already own it. A fresh lock held by someone else is a no.
    With no S3 at all (tests) there is nothing to coordinate: True."""
    s3b, bucket = _s3_pair(s3)
    if s3b is None:
        return True
    doc, etag = _lock_read(s3, job_id)
    body = json.dumps({'job_id': job_id, 'owner': owner, 'heartbeat': time.time(), 'host': os.environ.get('RENDER_INSTANCE_ID') or os.environ.get('HOSTNAME') or '', 'pid': os.getpid()}).encode('utf-8')
    try:
        if doc is None:
            s3b.put_object(Bucket=bucket, Key=_lock_key(job_id), Body=body, ContentType='application/json', IfNoneMatch='*')
            return True
        if str(doc.get('owner') or '') == owner:
            s3b.put_object(Bucket=bucket, Key=_lock_key(job_id), Body=body, ContentType='application/json')
            return True
        if lock_fresh(doc):
            return False
        if etag:
            s3b.put_object(Bucket=bucket, Key=_lock_key(job_id), Body=body, ContentType='application/json', IfMatch=etag)
        else:
            s3b.put_object(Bucket=bucket, Key=_lock_key(job_id), Body=body, ContentType='application/json')
        return True
    except Exception as e:
        # a 412 (someone else wrote first) or any other refusal: not ours
        if os.environ.get('REGRESSION_TEST_MODE', '').strip().lower() not in ('1', 'true', 'yes'):
            print(f"[twin-guided] lock {job_id} not taken: {str(e)[:100]}")
        return False


def _lock_beat(s3, job_id: str, owner: str) -> bool:
    s3b, bucket = _s3_pair(s3)
    if s3b is None:
        return True
    doc, _etag = _lock_read(s3, job_id)
    if doc is not None and str(doc.get('owner') or '') != owner and lock_fresh(doc):
        return False       # someone else took it over: this runner must stop
    try:
        s3b.put_object(Bucket=bucket, Key=_lock_key(job_id), Body=json.dumps({'job_id': job_id, 'owner': owner, 'heartbeat': time.time(), 'pid': os.getpid()}).encode('utf-8'), ContentType='application/json')
        return True
    except Exception:
        return True


def _lock_release(s3, job_id: str, owner: str) -> None:
    s3b, bucket = _s3_pair(s3)
    if s3b is None:
        return
    doc, _etag = _lock_read(s3, job_id)
    if doc is not None and str(doc.get('owner') or '') != owner:
        return
    try:
        s3b.delete_object(Bucket=bucket, Key=_lock_key(job_id))
    except Exception:
        pass


def _status_write(job_id: str, rec: dict) -> None:
    try:
        from prometheus import digital_twin_job as J
        J.status_write(job_id, rec)
    except Exception as e:
        if os.environ.get('REGRESSION_TEST_MODE', '').strip().lower() not in ('1', 'true', 'yes'):
            print(f"[twin-guided] status write skipped: {str(e)[:120]}")


def _record_for(sess: dict, i: int) -> Optional[dict]:
    for rec in sess.get('questions') or []:
        if isinstance(rec, dict) and rec.get('guided_i') == i:
            return rec
    return None


def _finished(rec: Optional[dict]) -> bool:
    if not rec:
        return False
    st = str(rec.get('stage') or '')
    return st == 'failed' or bool(rec.get('finished_at')) or bool(rec.get('read'))


def start_run(s3, payload: dict, key: str, sess: dict, plan_doc: dict, *, username: str = '', twins_by_key: Optional[dict] = None,
              voices_fn: Optional[Callable] = None, usage_extras: Optional[dict] = None, save: Optional[Callable] = None,
              ask: Optional[Callable] = None, outcome_call: Optional[Callable] = None, run_async: bool = True) -> dict:
    """Run the plan on the room: each question in order, saved as it
    lands, then the outcome. Returns the job record. ``save(payload)``
    persists the twin (the API passes DT.save_twin bound to s3 and key);
    ``ask`` stands in for digital_twin_panel.ask in tests."""
    job_id = str((sess.get('guided') or {}).get('job_id') or '') or ('gd' + uuid.uuid4().hex[:10])
    owner = uuid.uuid4().hex[:12]
    if is_live(job_id) or not _lock_acquire(s3, job_id, owner):
        # another process (or another thread here) is running this plan: one runner, ever
        print(f"[twin-guided] {job_id}: already running elsewhere; not started again")
        return None
    g = sess.setdefault('guided', {})
    g.update({'version': VERSION, 'job_id': job_id, 'plan': {k: v for k, v in plan_doc.items() if k != 'method'}, 'status': 'running', 'started_at': g.get('started_at') or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'done': sum(1 for i in range(len(plan_doc.get('questions') or [])) if _finished(_record_for(sess, i))), 'total': len(plan_doc.get('questions') or []), 'error': ''})
    rec = {'job_id': job_id, 'user': username, 'op': 'guided_room', 'status': 'running', 'stage': f"Question {g['done'] + 1} of {g['total']}", 'key': key,
           'session_id': str(sess.get('id') or ''), 'subject': _subject(payload), 'goal': plan_doc.get('goal'), 'done': g['done'], 'total': g['total']}
    _status_write(job_id, rec)
    if save:
        try:
            save(payload)
        except Exception:
            traceback.print_exc()

    _set_live(job_id, True)
    try:
        _index_add(s3, job_id, key, str(sess.get('id') or ''), username)
    except Exception:
        pass

    stop = threading.Event()

    def _beat():
        while not stop.wait(HEARTBEAT_S):
            if not _lock_beat(s3, job_id, owner):
                stop.set()
                break

    def _work():
        hb = threading.Thread(target=_beat, name=f"twin-guided-beat-{job_id}", daemon=True)
        hb.start()
        try:
            _run(s3, payload, key, sess, plan_doc, job_id, username=username, twins_by_key=twins_by_key or {key: payload}, voices_fn=voices_fn,
                 usage_extras=usage_extras, save=save, ask=ask, outcome_call=outcome_call, stop=stop)
        finally:
            stop.set()
            _set_live(job_id, False)
            if str((sess.get('guided') or {}).get('status') or '') != 'running':
                try:
                    _index_remove(s3, job_id)
                except Exception:
                    pass
            _lock_release(s3, job_id, owner)
    if run_async:
        th = threading.Thread(target=_work, name=f"twin-guided-{job_id}", daemon=True)
        th.start()
    else:
        _work()
    return rec


def _run(s3, payload, key, sess, plan_doc, job_id, *, username, twins_by_key, voices_fn, usage_extras, save, ask, outcome_call, stop=None):
    g = sess.setdefault('guided', {})
    qs = plan_doc.get('questions') or []
    total = len(qs)
    try:
        from migration import digital_twin_panel as P
        # the room answers on its own thread (run_async) so the record exists, tagged and saved, the moment the question is
        # put; this loop waits for it. A runner that loses its lock to another process stops at the next boundary.
        asker = ask or (lambda sess_, q, **kw: P.ask(sess_, twins_by_key, q, run_async=True, asked_by=username, s3=s3, twin_key=key, voices_fn=voices_fn, usage_extras=usage_extras, ask_again=True, **kw))
        for i, pq in enumerate(qs):
            if stop is not None and stop.is_set():
                raise RuntimeError('another process took this run over')
            prior = _record_for(sess, i)
            if prior is not None and not _finished(prior):
                # a record of ours still in flight: another process may be finishing it (the room's own pick-up), so wait for it;
                # one with no life behind it (the process that asked it is gone) is dropped and asked fresh
                prior = _await_or_drop(s3, sess, prior, save=save, payload=payload)
            if prior is not None and str(prior.get('stage') or '') == 'failed' and not prior.get('guided_retry'):
                # the room did not finish it once (a stall, a restart): ask it again, once; a second failure stands
                sess['questions'] = [r for r in sess.get('questions') or [] if r is not prior]
                prior = None
                retry = True
            else:
                retry = False
            if _finished(prior):
                continue
            g['status'] = 'running'
            g['stage'] = f"Question {i + 1} of {total}: {pq.get('q')}"
            _status_write(job_id, {'job_id': job_id, 'user': username, 'op': 'guided_room', 'status': 'running', 'stage': g['stage'], 'key': key, 'session_id': str(sess.get('id') or ''),
                                   'subject': _subject(payload), 'goal': plan_doc.get('goal'), 'done': i, 'total': total})
            kw = {'kind': pq.get('kind')}
            if pq.get('options'):
                kw['options'] = list(pq['options'])
            rec = asker(sess, str(pq.get('q') or ''), **kw)
            if isinstance(rec, dict):
                rec['guided_i'] = i
                rec['guided_level'] = pq.get('level')
                if retry:
                    rec['guided_retry'] = 1
                if save:
                    try:
                        save(payload)      # the question is on the record, tagged, before the room answers
                    except Exception:
                        traceback.print_exc()
                _wait_record(s3, rec, stop=stop)
            g['done'] = sum(1 for j in range(total) if _finished(_record_for(sess, j)))
            if save:
                try:
                    save(payload)
                except Exception:
                    traceback.print_exc()
        g['stage'] = 'Writing the outcome'
        _status_write(job_id, {'job_id': job_id, 'user': username, 'op': 'guided_room', 'status': 'running', 'stage': g['stage'], 'key': key, 'session_id': str(sess.get('id') or ''),
                               'subject': _subject(payload), 'goal': plan_doc.get('goal'), 'done': total, 'total': total})
        out = outcome(sess, plan_doc, call=outcome_call)
        g['outcome'] = {k: v for k, v in out.items() if k != 'method'}
        g['status'] = 'done'
        g['stage'] = 'Done'
        g['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        if save:
            try:
                save(payload)
            except Exception:
                traceback.print_exc()
        _status_write(job_id, {'job_id': job_id, 'user': username, 'op': 'guided_room', 'status': 'done', 'stage': 'done', 'key': key, 'session_id': str(sess.get('id') or ''),
                               'subject': _subject(payload), 'goal': plan_doc.get('goal'), 'done': total, 'total': total, 'ready_message': out.get('answer') or 'The room has answered.',
                               'outcome': g['outcome']})
    except Exception as e:
        traceback.print_exc()
        g['status'] = 'stopped'
        g['error'] = 'The room stopped before it finished. Nothing more is charged; resume and it picks up at the next question.'
        if save:
            try:
                save(payload)
            except Exception:
                pass
        _status_write(job_id, {'job_id': job_id, 'user': username, 'op': 'guided_room', 'status': 'error', 'stage': 'stopped', 'key': key, 'session_id': str(sess.get('id') or ''),
                               'subject': _subject(payload), 'goal': plan_doc.get('goal'), 'done': int(g.get('done') or 0), 'total': total, 'error': g['error']})


def _wait_record(s3, rec: dict, *, stop=None, ceiling_s: float = 1800.0) -> dict:
    """Wait for the room to finish a question it is answering on its own
    thread. The record object is shared with the room, so it fills in
    place; the job document is the fallback when it does not. A question
    past the ceiling is marked failed so the run can move on."""
    if _finished(rec):
        return rec
    try:
        from migration import digital_twin_room as R
    except Exception:
        R = None
    jid = str(rec.get('job_id') or '')
    deadline = time.monotonic() + ceiling_s
    while not _finished(rec):
        if stop is not None and stop.is_set():
            break
        if R is not None and s3 is not None and jid:
            job = R.job_read(s3, jid)
            if job is not None:
                jq = job.get('question') if isinstance(job.get('question'), dict) else {}
                if str(job.get('status') or '') in ('done', 'error') or str(job.get('stage') or '') in ('done', 'failed'):
                    for k, v in jq.items():
                        if k != 'trail':
                            rec[k] = v
                    if str(job.get('status') or '') == 'error' and str(rec.get('stage') or '') != 'failed':
                        rec['stage'] = 'failed'
                    break
        elif s3 is None:
            break        # nothing to wait on (tests)
        if time.monotonic() > deadline:
            rec['stage'] = 'failed'
            rec['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            rec.setdefault('read', 'The room did not finish this one in time.')
            break
        time.sleep(8.0)
    return rec


def _norm_q(s) -> str:
    return re.sub(r"[^a-z0-9]+", ' ', str(s or '').lower()).strip()


def reconcile_records(s3, sess: dict, plan_doc: dict) -> int:
    """Before a pick-up: one record per planned question. Records left in
    flight by a dead runner go (nothing holds the lock, so nothing is
    answering them); a finished record that matches a planned question by
    its words is tagged with its index; a second finished record for the
    same index (two runners raced) is dropped, the earliest kept. Questions
    the owner asked by hand are left alone. Returns the number removed."""
    qs = plan_doc.get('questions') or []
    plan_by_norm = {}
    for i, pq in enumerate(qs):
        plan_by_norm.setdefault(_norm_q(pq.get('q')), i)
    recs = list(sess.get('questions') or [])
    # a record the run tagged itself is the one the outcome was read from: it always wins over an untagged copy
    tagged = {rec.get('guided_i') for rec in recs if isinstance(rec, dict) and rec.get('guided_i') is not None and _finished(rec)}
    keep, seen, removed = [], {}, 0
    for rec in recs:
        if not isinstance(rec, dict):
            keep.append(rec)
            continue
        i = rec.get('guided_i')
        was_tagged = i is not None
        if i is None and _norm_q(rec.get('q')) in plan_by_norm:
            i = plan_by_norm[_norm_q(rec.get('q'))]
            if i in tagged:
                removed += 1      # an untagged copy of a question the run already holds
                continue
            rec['guided_i'] = i
            rec['guided_level'] = (qs[i] or {}).get('level')
        if i is None:
            keep.append(rec)
            continue
        if not _finished(rec):
            removed += 1          # in flight with no runner alive: dead
            continue
        if i in seen:
            removed += 1          # a duplicate of a finished question: the first one stands
            continue
        seen[i] = rec
        keep.append(rec)
    sess['questions'] = keep
    return removed


def _await_or_drop(s3, sess: dict, rec: dict, *, save=None, payload=None, wait_s: float = 900.0) -> Optional[dict]:
    """A record of ours still in flight when the run reaches it. Its own
    job document says whether anything is working it: one still moving
    is waited for (polling the document, up to ``wait_s``) and its
    finished fields are carried onto the record; one with no movement
    (no document, or none for the room's stale window) is dropped from
    the session so the question is asked fresh. Returns the record as
    it stands, or None when it was dropped."""
    try:
        from migration import digital_twin_room as R
        from migration import digital_twin_room_jobs as RJ
    except Exception:
        return rec
    jid = str(rec.get('job_id') or '')
    deadline = time.monotonic() + wait_s
    while True:
        job = R.job_read(s3, jid) if (s3 is not None and jid) else None
        if job is None or RJ.is_stale(job) or RJ.is_too_old(job):
            sess['questions'] = [r for r in sess.get('questions') or [] if r is not rec]
            if save:
                try:
                    save(payload)
                except Exception:
                    pass
            return None
        jq = job.get('question') if isinstance(job.get('question'), dict) else {}
        if str(job.get('status') or '') in ('done', 'error') or str(job.get('stage') or '') in ('done', 'failed'):
            for k, v in jq.items():
                if k != 'trail':
                    rec[k] = v
            if str(job.get('status') or '') == 'error' and str(rec.get('stage') or '') != 'failed':
                rec['stage'] = 'failed'
            return rec
        if time.monotonic() > deadline:
            sess['questions'] = [r for r in sess.get('questions') or [] if r is not rec]
            return None
        time.sleep(15.0)


def is_stale(s3, sess: dict, now: Optional[float] = None) -> bool:
    """A running guided run that nothing is running: not live in this
    process, its job document unmoved for STALE_S, and no question of
    its own still making progress on its own job document."""
    g = (sess or {}).get('guided') or {}
    if str(g.get('status') or '') != 'running':
        return False
    jid = str(g.get('job_id') or '')
    if not jid or is_live(jid):
        return False
    now = now or time.time()
    lock, _etag = _lock_read(s3, jid)
    if lock is not None:
        return not lock_fresh(lock, now)      # a runner anywhere keeps the heartbeat fresh
    # no lock on file (a run from before locks, or no S3 to read): fall back to the job document and the start time
    try:
        from prometheus import digital_twin_job as J
        doc = J.status_read(jid) or {}
    except Exception:
        doc = {}
    moved = float(doc.get('updated_at') or 0) if isinstance(doc, dict) else 0.0
    if moved and (now - moved) < STALE_S:
        return False
    if not moved:
        try:
            started = time.mktime(time.strptime(str(g.get('started_at') or ''), '%Y-%m-%dT%H:%M:%SZ')) - time.timezone
        except Exception:
            started = 0.0
        if started and (now - started) < STALE_S:
            return False
    return True


def resume_if_stale(s3, payload: dict, key: str, sess: dict, *, username: str = '', twins_by_key: Optional[dict] = None, voices_fn: Optional[Callable] = None,
                    usage_extras: Optional[dict] = None, save: Optional[Callable] = None, now: Optional[float] = None, ask: Optional[Callable] = None,
                    outcome_call: Optional[Callable] = None, run_async: bool = True) -> bool:
    """Pick a stale run back up at the first unanswered question (on a
    read of the room or the report, or from the sweeper). Nothing is
    charged again. Returns True when a run was restarted."""
    if not is_stale(s3, sess, now):
        return False
    g = sess.get('guided') or {}
    plan_doc = g.get('plan') or {}
    if not (plan_doc.get('questions') or []):
        return False
    try:
        from prometheus import digital_twin_job as J
        doc = J.status_read(str(g.get('job_id') or '')) or {}
    except Exception:
        doc = {}
    done = sum(1 for i in range(len(plan_doc['questions'])) if _finished(_record_for(sess, i)))
    _status_write(str(g.get('job_id') or ''), {'job_id': g.get('job_id'), 'user': username or (doc or {}).get('user') or str(sess.get('created_by') or ''), 'op': 'guided_room', 'status': 'running',
                                               'stage': f"Picking it back up at question {min(done + 1, len(plan_doc['questions']))} of {len(plan_doc['questions'])}", 'key': key,
                                               'session_id': str(sess.get('id') or ''), 'subject': _subject(payload), 'goal': plan_doc.get('goal'), 'done': done, 'total': len(plan_doc['questions']),
                                               'resumes': int((doc or {}).get('resumes') or 0) + 1})
    print(f"[twin-guided] {g.get('job_id')}: picking it back up at question {done + 1} of {len(plan_doc['questions'])} (the run had gone quiet)")
    reconcile_records(s3, sess, plan_doc)
    started = start_run(s3, payload, key, sess, plan_doc, username=username or (doc or {}).get('user') or str(sess.get('created_by') or ''), twins_by_key=twins_by_key or {key: payload},
                        voices_fn=voices_fn, usage_extras=usage_extras, save=save, ask=ask, outcome_call=outcome_call, run_async=run_async)
    return started is not None


def sweep(s3, *, load_twin: Callable, save_for: Callable, context_for: Optional[Callable] = None, now: Optional[float] = None) -> int:
    """From the running index: every guided run still marked running is
    loaded and, when stale, picked back up. ``load_twin(key)`` returns
    the payload; ``save_for(key)`` returns the save callable;
    ``context_for(username)`` returns (voices_fn, usage_extras) for the
    seat that opened the room. An entry whose run is done, or whose room
    is gone, leaves the index. Returns how many runs were restarted."""
    data = _index_read(s3)
    if not data:
        return 0
    restarted, dropped = 0, []
    for jid, ent in list(data.items()):
        key, sid = str((ent or {}).get('key') or ''), str((ent or {}).get('session_id') or '')
        if is_live(jid):
            continue
        try:
            payload = load_twin(key) if key else None
        except Exception:
            payload = None
        sess = next((x for x in ((payload or {}).get('panel_sessions') or []) if isinstance(x, dict) and str(x.get('id') or '') == sid), None) if isinstance(payload, dict) else None
        g = (sess or {}).get('guided') or {}
        if not sess or str(g.get('status') or '') != 'running' or str(g.get('job_id') or '') != str(jid):
            dropped.append(jid)
            continue
        username = str((ent or {}).get('user') or sess.get('created_by') or '')
        voices_fn, usage_extras = (context_for(username) if context_for else (None, None))
        try:
            if resume_if_stale(s3, payload, key, sess, username=username, voices_fn=voices_fn, usage_extras=usage_extras, save=save_for(key), now=now):
                restarted += 1
        except Exception:
            traceback.print_exc()
    if dropped:
        # re-read before writing: a run restarted above may already have finished and removed itself
        cur = _index_read(s3)
        for jid in dropped:
            cur.pop(jid, None)
        _index_write(s3, cur)
    return restarted


# ------------------------------------------------------------- the outcome
def _digest(sess: dict, plan_doc: dict) -> list:
    rows = []
    for i, pq in enumerate(plan_doc.get('questions') or []):
        rec = _record_for(sess, i) or {}
        opts = []
        for o in rec.get('options') or []:
            if isinstance(o, dict) and o.get('label'):
                opts.append({'label': o.get('label'), 'pct': o.get('pct'), 'people': o.get('people'), 'seats': o.get('seats_n') if o.get('seats_n') is not None else o.get('tally')})
        voices = [{'name': v.get('name'), 'line': v.get('line')} for v in (rec.get('voices') or [])[:3] if isinstance(v, dict) and v.get('line')]
        rows.append({'i': i, 'level': pq.get('level'), 'q': rec.get('q') or pq.get('q'), 'kind': rec.get('kind') or pq.get('kind'), 'split': opts, 'read': rec.get('read') or '', 'voices': voices,
                     'failed': str(rec.get('stage') or '') == 'failed'})
    return rows


# A sentence that still says how the answer was made after the house scrub is dropped whole: the reader gets the answer, never
# the method (no-modeled-or-source-language.mdc; the firmness words stay off this surface too)
_DROP_SENTENCE = re.compile(r"\b(synthetic|synth|modell?ed|model|estimat\w*|research\w*|panel|sampl\w*|personas?|segments?|cohorts?|confiden\w*|survey\w*|predict\w*|calibrat\w*|anchor\w*|benchmark\w*|simulat\w*|twins?|seat-weighted|weighted)\b", re.I)


def _scrub(s: str) -> str:
    text = _clean(s)
    try:
        from migration import digital_twin_room as ROOM
        text = ROOM.scrub(text)
    except Exception:
        pass
    parts = re.split(r"(?<=[.!?])\s+", text)
    kept = [pt for pt in parts if pt and not _DROP_SENTENCE.search(pt)]
    return ' '.join(kept).strip()


def deterministic_outcome(sess: dict, plan_doc: dict) -> dict:
    """The outcome with no desk: the last question's leading answer, its
    share and people, the seats, and the kinds behind it."""
    rows = _digest(sess, plan_doc)
    final_i = int(plan_doc.get('final') if plan_doc.get('final') is not None else len(rows) - 1) if rows else 0
    final = rows[final_i] if rows and 0 <= final_i < len(rows) else (rows[-1] if rows else None)
    goal = plan_doc.get('goal') or 'the question'
    if not final or not final['split']:
        read = (final or {}).get('read') or ''
        return {'version': VERSION, 'method': 'deterministic', 'call': 'split', 'answer': f"The room did not land on one side of {goal[0].lower() + goal[1:] if goal else 'it'}", 'why': read, 'numbers': [], 'who': '', 'would_change': '', 'next': ''}
    lead = max(final['split'], key=lambda o: float(o.get('pct') or 0))
    rest = sorted((o for o in final['split'] if o is not lead), key=lambda o: -float(o.get('pct') or 0))
    pct = float(lead.get('pct') or 0)
    lab = str(lead.get('label') or '')
    low = lab.lower()
    kind = plan_doc.get('kind')
    if kind == 'contest':
        call = lab
    elif re.search(r"\byes\b", low) or re.search(r"\bwould\b|\bbuy\b|\bwatch\b", low) and 'not' not in low and "n't" not in low:
        call = 'yes' if pct >= 55 else 'lean yes'
    elif re.search(r"\bno\b|\bnot\b|n't|\bneither\b|\bnever\b", low):
        call = 'no' if pct >= 55 else 'lean no'
    else:
        call = lab
    answer = f"{lab}: {pct:.1f}% of the audience" + (f", {int(lead.get('people') or 0):,} people" if lead.get('people') else '') + (f", {lead.get('seats')} of the seats" if isinstance(lead.get('seats'), int) else '') + '.'
    numbers = [f"{o.get('label')}: {float(o.get('pct') or 0):.1f}%" + (f", {int(o.get('people') or 0):,} people" if o.get('people') else '') for o in [lead] + rest[:3]]
    return {'version': VERSION, 'method': 'deterministic', 'call': call, 'answer': _scrub(answer), 'why': _scrub(final.get('read') or ''), 'numbers': [_scrub(n) for n in numbers],
            'who': '', 'would_change': '', 'next': ''}


def _norm_outcome(data: dict, fallback: dict) -> Optional[dict]:
    if not isinstance(data, dict) or not _clean(data.get('answer')):
        return None
    out = {'version': VERSION, 'method': 'reasoned', 'call': _scrub(data.get('call') or fallback.get('call') or 'split')[:80], 'answer': _scrub(data.get('answer'))[:400],
           'why': _scrub(data.get('why'))[:900], 'numbers': [_scrub(n)[:200] for n in (data.get('numbers') or []) if _clean(n)][:6],
           'who': _scrub(data.get('who'))[:400], 'would_change': _scrub(data.get('would_change'))[:400], 'next': _scrub(data.get('next'))[:300]}
    if not out['numbers']:
        out['numbers'] = fallback.get('numbers') or []
    return out


def outcome(sess: dict, plan_doc: dict, *, models=None, call: Optional[Callable] = None) -> dict:
    """The outcome of the run: the outcome desk's answer to the business
    question, or the deterministic one when nothing answers."""
    fallback = deterministic_outcome(sess, plan_doc)
    if call is None:
        try:
            from migration import digital_twin_room as ROOM
            if models is not None or not ROOM.regression_mode():
                M = models or ROOM.Models()

                def call(system, user, max_tokens, temperature):   # noqa: E306
                    data, _rec = M.call('guided', system, user, max_tokens=max_tokens, temperature=temperature, tools=None)
                    return data
        except Exception:
            call = None
    if call is None:
        return fallback
    body = {'business_question': plan_doc.get('asked') or plan_doc.get('goal'), 'goal': plan_doc.get('goal'), 'success': plan_doc.get('success'), 'kind': plan_doc.get('kind'),
            'final_question_index': plan_doc.get('final'), 'room': _digest(sess, plan_doc), 'seats': len(((sess.get('group') or {}).get('roster')) or []),
            'people': int(((sess.get('group') or {}).get('size_people')) or 0)}
    try:
        data = call(OUTCOME_SYSTEM, json.dumps(body, default=str)[:30000], 1800, 0.3)
    except Exception as e:
        print(f"[twin-guided] outcome desk failed: {str(e)[:120]}")
        data = None
    out = _norm_outcome(data, fallback)
    return out or fallback


def outcome_lines(sess: dict) -> list:
    """The outcome as plain lines for the chat, the CSV and the report."""
    g = (sess or {}).get('guided') or {}
    out = g.get('outcome') or {}
    if not out:
        return []
    lines = []
    if g.get('plan', {}).get('goal'):
        lines.append(f"The question: {g['plan']['goal']}")
    if out.get('answer'):
        lines.append(f"The outcome: {out['answer']}")
    if out.get('why'):
        lines.append(out['why'])
    for n in out.get('numbers') or []:
        lines.append(f"- {n}")
    if out.get('who'):
        lines.append(out['who'])
    if out.get('would_change'):
        lines.append(f"What would change it: {out['would_change']}")
    if out.get('next'):
        lines.append(f"Worth asking next: {out['next']}")
    return lines


def call_word(out: dict, plan_doc: Optional[dict] = None) -> str:
    """The call as the reader sees it: Yes, No, Leaning yes, Leaning no,
    Split, or the winning option on a pick."""
    c = str((out or {}).get('call') or '').strip()
    if not c:
        return 'Split'
    return CALL_WORDS.get(c.lower(), c)


def report(payload: dict, sess: dict) -> dict:
    """The report of the whole process, as data: the business question,
    the audience, the outcome, every question in the order it ran with
    its level, split, read and voices, and who sat in the room. The
    page and the PDF both draw from this."""
    g = (sess or {}).get('guided') or {}
    plan_doc = g.get('plan') or {}
    m = (payload or {}).get('meta') or {}
    grp = (sess or {}).get('group') or {}
    roster = [r for r in (grp.get('roster') or []) if isinstance(r, dict)]
    people = int(grp.get('size_people') or m.get('size_us') or 0)
    qs = []
    for i, pq in enumerate(plan_doc.get('questions') or []):
        rec = _record_for(sess, i) or {}
        split = []
        for r in rec.get('distribution') or rec.get('options') or []:
            if isinstance(r, dict) and r.get('label'):
                split.append({'label': str(r.get('label')), 'pct': r.get('pct'), 'people': r.get('people'), 'seats': r.get('seats_n') if r.get('seats_n') is not None else r.get('tally')})
        lead = max(split, key=lambda o: float(o.get('pct') or 0)) if split else None
        qs.append({'n': i + 1, 'level': pq.get('level'), 'level_word': LEVEL_WORDS.get(str(pq.get('level') or ''), ''), 'q': rec.get('q') or pq.get('q'), 'kind': rec.get('kind') or pq.get('kind'),
                   'tests': pq.get('tests') or '', 'split': split, 'lead': lead, 'read': _scrub(str(rec.get('read') or '')),
                   'voices': [{'name': v.get('name'), 'age': v.get('age'), 'city': v.get('city'), 'line': v.get('line'), 'seat': v.get('seat')} for v in (rec.get('voices') or [])[:3] if isinstance(v, dict) and v.get('line')],
                   'answered': _finished(rec), 'failed': str(rec.get('stage') or '') == 'failed', 'settles': i == int(plan_doc.get('final') if plan_doc.get('final') is not None else -1)})
    out = g.get('outcome') or {}
    seats = [{'seat': r.get('seat'), 'name': r.get('name'), 'age': r.get('age'), 'city': r.get('city'), 'kind': ((r.get('archetype') or {}).get('label') or ''), 'people': r.get('people') or r.get('stands_for'),
              'portrait_url': r.get('portrait_url') or '', 'portrait_hash': r.get('portrait_hash') or ''} for r in roster]
    return {'product': PRODUCT, 'title': str(sess.get('title') or plan_doc.get('goal') or PRODUCT), 'asked': plan_doc.get('asked') or '', 'goal': plan_doc.get('goal') or '', 'success': plan_doc.get('success') or '',
            'kind': plan_doc.get('kind') or '', 'audience': {'name': m.get('name') or m.get('subject') or '', 'people': people, 'seats': len(roster)},
            'status': g.get('status') or '', 'done': g.get('done'), 'total': g.get('total'), 'started_at': g.get('started_at'), 'finished_at': g.get('finished_at'),
            'outcome': ({'call': out.get('call'), 'call_word': call_word(out, plan_doc), 'answer': out.get('answer'), 'why': out.get('why'), 'numbers': list(out.get('numbers') or []),
                         'who': out.get('who'), 'would_change': out.get('would_change'), 'next': out.get('next')} if out else None),
            'questions': qs, 'seats': seats, 'session_id': sess.get('id')}


def report_filename(payload: dict, sess: dict, ext: str = '.pdf') -> str:
    m = (payload or {}).get('meta') or {}
    return re.sub(r"[^A-Za-z0-9]+", '_', f"{m.get('name') or m.get('subject')} Predictive Analytics {sess.get('id')}").strip('_') + ext


def build_report_pdf(payload: dict, sess: dict, portrait_fn=None) -> bytes:
    """The report as a file: the cover with the question and the call,
    the outcome page, one page per question in the order it ran, then
    who sat in the room. The same house pages as every twin export."""
    from migration import digital_twin_exports as X
    rp = report(payload, sess)
    d = X._Doc()
    aud = rp['audience']
    out = rp.get('outcome') or {}
    who = f"{aud['name']}: {aud['seats']} people in the room standing for {aud['people']:,}." if aud.get('people') else f"{aud['name']}."
    # the cover: the question, then the call
    y = d.new_page(True, PRODUCT, rp['goal'] or rp['title'], who)
    if out:
        y = d.tiles([(out.get('call_word') or 'Split', 'The call'), (f"{rp['total'] or len(rp['questions'])} questions", 'Asked, from the wide to the narrow'), (f"{aud['seats']}", 'Seats in the room')], d.m, y - 6, d.right - d.m)
        y = d.paragraph(_scrub(str(out.get('answer') or '')), d.m, y - 10, d.right - d.m, size=13, color=X.OFFWHITE, font=d.f['medium'])
    else:
        y = d.paragraph('The room has not finished yet; the outcome lands when the last question does.', d.m, y - 6, d.right - d.m, size=12)
    if rp.get('success'):
        d.paragraph('What would count as yes: ' + _scrub(str(rp['success'])), d.m, y - 14, d.right - d.m, size=10)
    # the outcome page
    if out:
        y = d.new_page(False, PRODUCT + ', the outcome', _scrub(str(out.get('answer') or ''))[:160] or 'The outcome', '')
        if out.get('why'):
            y = d.paragraph(_scrub(str(out['why'])), d.m, y, d.right - d.m, size=11.5)
        nums = [_scrub(str(n)) for n in (out.get('numbers') or []) if _scrub(str(n))]
        if nums:
            y = d.bullets(nums, d.m, y - 14, d.right - d.m, size=11)
        for lab, key in (('Who drove it', 'who'), ('What would change it', 'would_change'), ('Worth asking next', 'next')):
            txt = _scrub(str(out.get(key) or ''))
            if txt:
                y = d.paragraph(lab + ': ' + txt, d.m, y - 8, d.right - d.m, size=10.5)
    # one page per question, in the order it ran
    read = X._portrait_reader(portrait_fn)
    by_seat = {r.get('seat'): r for r in rp['seats']}
    by_name = {str(r.get('name')): r for r in rp['seats']}
    for i, q in enumerate(rp['questions']):
        eyebrow = f"Question {q['n']} of {len(rp['questions'])}" + (f", {q['level_word'].lower()}" if q.get('level_word') else '')
        lead = q.get('lead') or {}
        sub = (f"{lead.get('label')}: {lead.get('pct')}% ({int(lead.get('people') or 0):,} people)" if lead and lead.get('people') else (f"{lead.get('label')}: {lead.get('pct')}%" if lead else ('The room did not finish this one.' if q.get('failed') else '')))
        y = d.new_page(i % 2 == 0, eyebrow, X.question_header(q.get('q') or ''), sub)
        rows = [(r['label'], f"{r.get('pct')}%", f"{int(r.get('people') or 0):,} people" if r.get('people') else '') for r in q['split'][:8]]
        if rows:
            y = d.table(rows, d.m, y, d.right - d.m, head=('Answer', 'Share', 'People'))
        if q.get('read'):
            y = d.paragraph(q['read'], d.m, y - 10, d.right - d.m, size=10.5)
        pic = 0.34 * 72
        for v in q.get('voices') or []:
            quote = X.clean_quote(v.get('line'))
            if not quote or y < X.PANEL_FLOOR + 40:
                continue
            seat = by_seat.get(v.get('seat')) or by_name.get(str(v.get('name') or '')) or {}
            img = read(seat.get('portrait_hash'))
            x = d.m + (pic + 8 if img is not None else 0)
            if img is not None:
                X._draw_round_portrait(d.c, img, d.m, y - 6 - pic + 9, pic)
            yy = d.paragraph(f"{v.get('name')}, {v.get('age')}, {v.get('city')}: {quote}", x, y - 6, d.right - x, size=10, color=X.OFFWHITE if d.dark else X.GRAPHITE, font=d.f['medium'])
            y = min(yy, y - 6 - pic + 9 - 8) if img is not None else yy
    # who sat in the room
    for pg in X.seat_pages(sess, portrait_fn=portrait_fn, people_total=X._room_total(X._light_group(sess)), eyebrow=PRODUCT, start_dark=not d.dark):
        y = d.new_page(bool(pg.get('dark')), pg.get('eyebrow') or PRODUCT, pg['header'], pg.get('sub') or '')
        X.draw_seats_pdf(d, pg, y)
    return X._fin(d.finish(), '.pdf')


def public_guided(sess: dict) -> Optional[dict]:
    """What the page and the partner payload see of a guided run."""
    g = (sess or {}).get('guided')
    if not isinstance(g, dict):
        return None
    plan_doc = g.get('plan') or {}
    return {'job_id': g.get('job_id'), 'status': g.get('status'), 'stage': g.get('stage'), 'done': g.get('done'), 'total': g.get('total'), 'error': g.get('error') or '',
            'goal': plan_doc.get('goal'), 'success': plan_doc.get('success'), 'kind': plan_doc.get('kind'),
            'questions': [{'q': q.get('q'), 'kind': q.get('kind'), 'level': q.get('level'), 'tests': q.get('tests'), 'options': q.get('options')} for q in plan_doc.get('questions') or []],
            'final': plan_doc.get('final'), 'outcome': ({k: v for k, v in (g.get('outcome') or {}).items() if k != 'method'} if g.get('outcome') else None),
            'started_at': g.get('started_at'), 'finished_at': g.get('finished_at')}


__all__ = ['VERSION', 'PRODUCT', 'KINDS', 'LEVEL_WORDS', 'MIN_QUESTIONS', 'MAX_QUESTIONS', 'STALE_S', 'SWEEP_S', 'RUNNING_KEY', 'LOCK_PREFIX', 'LOCK_STALE_S', 'HEARTBEAT_S', 'kind_of', 'plan',
           'deterministic_plan', 'start_run', 'is_live', 'is_stale', 'lock_fresh', 'reconcile_records', 'resume_if_stale', 'sweep', 'outcome', 'deterministic_outcome', 'outcome_lines',
           'public_guided', 'call_word', 'report', 'report_filename', 'build_report_pdf']
