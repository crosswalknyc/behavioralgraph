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

PLAN_SYSTEM = """You plan a focus group for one business question. The room is a set of people who each stand for a kind of person in the audience; they answer one question at a time, in character, and the room reports the split, a read and their voices.

Write the plan as JSON only:
{"goal": one plain sentence that restates the business question as the thing the user needs to know,
 "success": one plain sentence saying what result from the room would count as a yes (or as X winning),
 "questions": [ {"q": the question as the room will hear it, "kind": "yes_no" | "multiple_choice" | "open" | "rating", "options": [two to five short options, multiple_choice only], "level": "macro" | "mid" | "micro", "tests": one short line on what this question tells us} ],
 "final": the zero-based index of the question that settles the goal}

Rules.
- Five to eight questions, in order from the wide to the narrow: first where the audience stands today (habit, awareness, what they already do), then the pull and the friction (what moves them, what holds them back), then the specific scenario the business question describes, and last the one question that settles it (a yes or no, or a pick between the options). The last question IS the business question, put to one person in plain words.
- Each question is something one person can answer about themselves in a sentence; never a market question ("what share of people...").
- Plain English a high school student follows. Short. No jargon, no brand-speak.
- Options for a pick are short, distinct, and cover the real answers including "neither" or "not for me" where honest.
- Never these words: synthetic, synth, model, modeled, estimated, research, panel, sample, archetype, persona, segment, cohort, confidence, data, survey. No em dashes.
- Keep the user's names, prices, titles and dates exactly as written.
"""

OUTCOME_SYSTEM = """You are the moderator writing the outcome of a focus group that ran to answer one business question. You have every question the room was asked, the published split of the audience on each (shares, the people behind each answer, the seats), the read on each, and voices from the seats.

Answer the business question for the person who asked it. JSON only:
{"call": one of "yes", "lean yes", "lean no", "no", "split", or the exact winning option when the goal was a pick,
 "answer": one sentence that states the call plainly with the one number that carries it,
 "why": two or three sentences on why the room landed there, in plain words,
 "numbers": [three to five short lines, each carrying one number from the record exactly as published (a share, a people count, a seat count), the most decisive first],
 "who": one sentence on which kinds of people in the room drove the result and which pushed the other way,
 "would_change": one sentence on what would most likely move the result,
 "next": one follow-up question worth asking this room next}

Rules.
- Use only numbers that appear in the record; never invent or round them to a looser figure. State shares in plain words when it helps ("about three in ten") and keep the exact figure beside it.
- Say what the room said; do not hedge the call with "might" or "could" when the split is clear, and do not overstate a narrow one.
- Plain English a high school student follows. Never these words: synthetic, synth, model, modeled, estimated, research, panel, sample, archetype, persona, segment, cohort, confidence, data, survey, prediction. No em dashes.
"""

_BANNED = re.compile(r"\b(synthetic|synth|modell?ed|model|estimated|research(?:ed)?|panel|sample|archetypes?|personas?|segments?|cohorts?|confidence|survey|prediction)\b", re.I)


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
    body = {'audience': brief, 'kinds_of_people_in_the_room': _kinds_words(twin, 8), 'business_question': goal,
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

    def _work():
        _run(s3, payload, key, sess, plan_doc, job_id, username=username, twins_by_key=twins_by_key or {key: payload}, voices_fn=voices_fn,
             usage_extras=usage_extras, save=save, ask=ask, outcome_call=outcome_call)
    if run_async:
        th = threading.Thread(target=_work, name=f"twin-guided-{job_id}", daemon=True)
        th.start()
    else:
        _work()
    return rec


def _run(s3, payload, key, sess, plan_doc, job_id, *, username, twins_by_key, voices_fn, usage_extras, save, ask, outcome_call):
    g = sess.setdefault('guided', {})
    qs = plan_doc.get('questions') or []
    total = len(qs)
    try:
        from migration import digital_twin_panel as P
        asker = ask or (lambda sess_, q, **kw: P.ask(sess_, twins_by_key, q, run_async=False, asked_by=username, s3=s3, twin_key=key, voices_fn=voices_fn, usage_extras=usage_extras, ask_again=True, **kw))
        for i, pq in enumerate(qs):
            if _finished(_record_for(sess, i)):
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
_DROP_SENTENCE = re.compile(r"\b(synthetic|synth|modell?ed|model|estimat\w*|research\w*|panel|sampl\w*|archetyp\w*|personas?|segments?|cohorts?|confiden\w*|survey\w*|predict\w*|calibrat\w*|anchor\w*|benchmark\w*|simulat\w*|twins?|seat-weighted|weighted)\b", re.I)


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


__all__ = ['VERSION', 'PRODUCT', 'KINDS', 'LEVEL_WORDS', 'MIN_QUESTIONS', 'MAX_QUESTIONS', 'kind_of', 'plan', 'deterministic_plan', 'start_run', 'outcome', 'deterministic_outcome',
           'outcome_lines', 'public_guided', 'call_word', 'report', 'report_filename', 'build_report_pdf']
