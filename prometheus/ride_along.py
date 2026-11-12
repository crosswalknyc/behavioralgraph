"""A deliverable asked for at the end of a question is carried through
(Jenna 2026-11-11).

Armine at Lionsgate, 21:07 UTC: "Looking at YouTube. What type of
categories do best for digital original content: Comedy, lifestyle/arts
& culture, Faith, Young Adult, Drama, and Horror do in this space.? Can
we give some examples?  how is the audience like? Summarize in
powerpoint". The splitter read it as three questions; "Summarize in
powerpoint" rode into the third as words for the read lane. The read
that came back said a deck was built and sent. No deck was ever built.
The night before, "Can we summarize this in a powerpoint?" opening the
same question was answered as a question too: "summarize ... in a
powerpoint" was not a deck ask to anything that reads one.

The rule: a request for a deliverable (a deck, a PowerPoint, slides, a
one-pager, a PDF of it, "email it to me") that rides on a question is
taken off the question before the question is routed, and carried:

  1. The question is answered as it would be without it. The read lane
     never sees the deliverable words, so it has nothing to make a claim
     about.
  2. The deliverable goes down its own normal path, with that path's own
     question when it has one:
       * the answer is already in hand: the deck path runs now on the
         subject of the answer, and its reply (the build line with its
         job, or its one question) is added under the answer;
       * the answer is a read still running: the request is put on
         record against the thread (``system/pm_ride_along/``) and the
         reply says the deck is built from the read when it lands. When
         the read lands, ``on_read_landed`` starts the deck through the
         same deck path, from that read, and posts the path's line into
         the thread with no browser open. A read that is held leaves the
         request pending for the pass that lands it;
       * "email it to me": an answer in hand goes out through the send
         lane now; a read still running gets the reader's email opt-in
         for that job, the same record the "email me when it lands" chip
         writes.
  3. Nothing is dropped and nothing is said that has no record: every
     line this module adds carries a job id or the pending record's key.

A CSV asked for with a question stays with the question (a read writes
its table as a CSV and links it; the read brief keeps "send it as a
CSV"). A message that is only the deliverable ask ("make this a deck")
is the deck surface's own and is not touched here; an order for a build
keeps its deliverable notes on the brief (``prometheus.orders``).

``split(text)``                      {'question', 'kinds', 'words'} or None
``run(service_ask, user, body, via)`` the ask, with the carry
``on_read_landed(user, job_id, payload, ...)``
                                     the landing hook
``pending_records(list, get)``       what is still pending (the sweep)

Never raises into an ask or a landing. Regression:
scripts/test_pm_truth_2026_11_11.py.
"""
from __future__ import annotations

import json
import re
import time

from . import faults as _faults

__all__ = ['split', 'run', 'on_read_landed', 'pending_records', 'PREFIX', 'DECK_NEXT_LINE', 'DECK_AFTER_APPROVAL_LINE',
           'EMAIL_NEXT_LINE', 'DECK_START_LINE']

PREFIX = 'system/pm_ride_along/'
MAX_AGE_S = 72 * 3600
DECK_NEXT_LINE = ("Your PowerPoint is on record with this read: I build the deck from the read when it lands, and it lands in "
                  "this thread.")
DECK_AFTER_APPROVAL_LINE = ("Your PowerPoint is on record with this question: once the read behind it lands, I build the deck "
                            "from it, in this thread.")
DECK_START_LINE = "Working on the PowerPoint now. The deck will land in this thread when it is ready."


def _named(line, words):
    """The line with the format the reader's own words named."""
    w = str(words or '').lower()
    if re.search(r"power\s?point|\bpptx?\b", w):
        return line
    return line.replace('Your PowerPoint is', 'Your deck is').replace('the PowerPoint', 'the deck').replace('PowerPoint', 'deck')
EMAIL_NEXT_LINE = "It also goes to your inbox when it lands."
_MIN_QUESTION_WORDS = 5

_FMT = r"(?P<fmt>power\s?point|pptx?|slide\s+deck|deck|slides?|presentation|one[- ]pager|pdf)"
_ASK_LEAD = (r"(?:(?:and|then|also|plus|oh\s+and|ps|p\.s\.)[,\s]+)*"
             r"(?:(?:can|could|would|will)\s+(?:we|you|i)\s+(?:please\s+)?|please\s+|pls\s+|let'?s\s+|"
             r"i(?:'d|\s+would)\s+like\s+(?:you\s+)?(?:to\s+)?|i\s+(?:want|need)\s+(?:you\s+to\s+)?|we\s+(?:want|need)\s+(?:you\s+to\s+)?)*")
_VERB = (r"(?:(?:also\s+)?(?:summari[sz]e|recap|sum\s+up|wrap(?:\s+(?:it|this|that))?\s+up|put|show|present|package|deliver|"
         r"give\s+(?:me|us)|send(?:\s+(?:me|us))?|turn|make|build|create|generate|export|share|provide|prepare|drop|have|get\s+(?:me|us))\s+)")
_OBJ = (r"(?:(?:it|this|that|these|those|them|everything|all(?:\s+of)?\s+(?:this|that|it)|"
        r"(?:the|these|those|my)\s+(?:results?|answers?|reads?|findings|data|analysis|summary|numbers|whole\s+thing))\s+)")
_DECK_RX = re.compile(
    r"^\W*" + _ASK_LEAD + _VERB + r"?" + _OBJ + r"?(?:all\s+)?(?:in|into|as|on|with|via|to)\s+(?:an?\s+|one\s+|the\s+)?"
    r"(?:(?:short|quick|simple|brief|clean|single|nice|small|tight)\s+)*" + _FMT +
    r"(?:\s+(?:deck|presentation|file|format|form|version|please|too|as\s+well|for\s+me|for\s+us|thanks?))*\W*$", re.I)
# "a deck please", "and a powerpoint too", "deck too please"
_DECK_BARE_RX = re.compile(
    r"^\W*(?:(?:and|then|also|plus)[,\s]+)*(?:(?:with|in|as)\s+)?(?:an?\s+|the\s+)?" + _FMT +
    r"(?:\s+(?:deck|version|file|of\s+(?:it|this|that)))?\s+(?:please|pls|too|as\s+well|also|thanks?)(?:\s+(?:please|thanks?))?\W*$", re.I)
_EMAIL_RX = re.compile(
    r"^\W*" + _ASK_LEAD + r"(?:(?:e-?mail|send|mail|forward)\s+(?:it|this|that|them|"
    r"the\s+(?:results?|answer|read|file|summary|deck|power\s?point|whole\s+thing))\s+(?:to\s+(?:me|my\s+(?:e-?mail|inbox))|over)|"
    r"e-?mail\s+me\s+(?:it|this|that|the\s+(?:results?|answer|read|file|summary|deck)))"
    r"(?:\s+(?:please|too|as\s+well|when\s+(?:it(?:'s|\s+is)\s+)?(?:done|ready)|thanks?))*\W*$", re.I)
_SENT_RX = re.compile(r"(?<=[.!?])\s+|\n+")
# a deliverable glued to the last sentence: "... how is the audience like and put it in a deck"
_TAIL_CUT_RX = re.compile(r"[,;]\s*|\s+(?=(?:and|then|also|plus)\s)", re.I)


def _tail_split(sentence):
    """(head, tail) when the sentence ends in a deliverable clause, the
    shortest such tail; ('', '') otherwise."""
    s = str(sentence or '')
    cuts = [m for m in _TAIL_CUT_RX.finditer(s)]
    for m in reversed(cuts):
        head, tail = s[:m.start()], s[m.end():]
        if len(head) >= 20 and 4 <= len(tail) <= 90 and _kind_of(tail):
            return head.rstrip(' ,;'), tail
    return '', ''


_QUESTION_SIGN_RX = re.compile(
    r"\?|\b(?:what|which|who|how|where|when|why|compare|show|list|rank|break\s+down|tell\s+me|give\s+me|looking\s+at|pull|find|size)\b", re.I)


def _squash(s):
    return ' '.join(str(s or '').split())


def _kind_of(sentence):
    s = _squash(sentence)
    if not s or len(s.split()) > 16:
        return ''
    if _EMAIL_RX.match(s):
        return 'email'
    m = _DECK_RX.match(s) or _DECK_BARE_RX.match(s)
    if m:
        return 'deck'
    return ''


def split(text):
    """{'question', 'kinds', 'words'} when a deliverable request rides on
    a question; None when the message carries none, or is only the
    deliverable ask."""
    try:
        raw = str(text or '')
        if not raw.strip() or len(raw) > 6000:
            return None
        if not re.search(r"power\s?point|pptx?\b|\bdeck\b|\bslides?\b|presentation|one[- ]pager|\bpdf\b|e-?mail|\bsend\b|\bforward\b", raw, re.I):
            return None
        sents = [s for s in (x.strip() for x in _SENT_RX.split(raw)) if s]
        keep, kinds, words = [], [], []
        for s in sents:
            k = _kind_of(s)
            if k:
                kinds.append(k)
                words.append(_squash(s))
                continue
            keep.append(s)
        if keep and not kinds:
            head, tail = _tail_split(keep[-1])
            if tail:
                kinds.append(_kind_of(tail))
                words.append(_squash(tail))
                keep[-1] = head
        if not kinds:
            return None
        question = ' '.join(keep).strip()
        if len(question.split()) < _MIN_QUESTION_WORDS or not _QUESTION_SIGN_RX.search(question):
            return None                  # only the deliverable ask (or a pointer): the deck surface's own
        out_kinds = []
        for k in kinds:
            if k not in out_kinds:
                out_kinds.append(k)
        return {'question': question, 'kinds': out_kinds, 'words': ' '.join(words)[:300]}
    except Exception:
        return None


# ------------------------------------------------------------------ store
def _safe(s):
    return re.sub(r"[^A-Za-z0-9_.@-]", '', str(s or ''))[:96]


def _key(user, tid):
    return f"{PREFIX}{_safe(user)}/{_safe(tid)}.json"


def _host():
    from .host import host
    return host


def _put(key, doc):
    h = _host()
    h.s3_client.put_object(Bucket=h.bucket, Key=key, Body=json.dumps(doc).encode('utf-8'), ContentType='application/json')


def _get(key):
    h = _host()
    try:
        return json.loads(h.s3_client.get_object(Bucket=h.bucket, Key=key)['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _record(user, tid, plan, *, state, job_ids=(), question='', extra=None):
    doc = {'user': str(user), 'thread_id': str(tid or ''), 'kinds': list(plan.get('kinds') or []),
           'words': str(plan.get('words') or '')[:300], 'question': str(question or '')[:600], 'state': state,
           'job_ids': [str(j) for j in job_ids if j], 'created_at': time.time()}
    doc.update(extra or {})
    _put(_key(user, tid), doc)
    return doc


def pending_records(store_list, store_get, now=None):
    """[(key, doc)] of every request still waiting for its read."""
    out = []
    try:
        for key in store_list(PREFIX) or []:
            if not str(key).endswith('.json'):
                continue
            doc = store_get(key)
            if isinstance(doc, dict) and doc.get('state') in ('pending', 'awaiting_read'):
                out.append((key, doc))
    except Exception:
        pass
    return out


# ------------------------------------------------------------------ the ask
def _armed(body):
    try:
        from . import service as _svc
        return bool(_svc._armed(body))
    except Exception:
        return False


def _plan(body):
    if not isinstance(body, dict) or body.get('_multi_part') or body.get('_meta_replay') or body.get('_pm_ride_along'):
        return None
    if _armed(body):
        return None
    text = str(body.get('text') or '')
    plan = split(text)
    if not plan:
        return None
    try:
        from . import orders as _orders
        if _orders.is_order(plan['question']) and _orders.product_of(plan['question']) not in ('', 'read'):
            return None                  # an order keeps its deliverable notes on its own brief
    except Exception:
        pass
    return plan


def _append(env, line, *, options=None, mark=None):
    """One more paragraph on the reply, in every shape a client reads."""
    if not isinstance(env, dict) or not line:
        return env
    env['text'] = (str(env.get('text') or '').rstrip() + '\n\n' + line).strip()
    raw = env.get('raw') if isinstance(env.get('raw'), dict) else (env.get('_raw') if isinstance(env.get('_raw'), dict) else None)
    if isinstance(raw, dict):
        for k in ('reply', 'error', 'message'):
            if isinstance(raw.get(k), str) and raw[k].strip():
                raw[k] = raw[k].rstrip() + '\n\n' + line
                break
        if mark:
            raw['ride_along'] = dict(raw.get('ride_along') or {}, **mark)
    if mark:
        env['ride_along'] = dict(env.get('ride_along') or {}, **mark)
    for o in options or []:
        if isinstance(o, str) and o.strip():
            env.setdefault('options', []).append({'label': o.strip(), 'send': o.strip()})
    return env


def _job_ids(env):
    out = []
    raw = env.get('raw') if isinstance(env.get('raw'), dict) else (env.get('_raw') if isinstance(env.get('_raw'), dict) else {})
    job = env.get('job') if isinstance(env.get('job'), dict) else None
    if job and job.get('type') == 'read' and job.get('id'):
        out.append(str(job['id']))
    for k in ('read_job_id',):
        if raw.get(k) and str(raw[k]) not in out:
            out.append(str(raw[k]))
    for j in raw.get('read_job_ids') or raw.get('jobs') or []:
        jid = j.get('id') if isinstance(j, dict) else j
        if jid and str(jid) not in out:
            out.append(str(jid))
    return out


def _answer_subject(env):
    raw = env.get('raw') if isinstance(env.get('raw'), dict) else (env.get('_raw') if isinstance(env.get('_raw'), dict) else {})
    for src in (raw, env.get('decision') or {}):
        for k in ('subject', 'profile', 'bind_subject'):
            v = src.get(k) if isinstance(src, dict) else None
            if isinstance(v, str) and v.strip():
                return v.strip()[:160]
    m = re.match(r"^On\s+(?P<s>[^\n:]{2,140}):", str(env.get('text') or ''))
    return _squash(m.group('s')) if m else ''


def _unpack(resp):
    from . import service as _svc
    return _svc._unpack(resp)


def _deck_now(user, body, env, plan, via):
    """An answer in hand: the deck path runs on it now. Returns (line,
    options, mark)."""
    h = _host()
    tid = env.get('thread_id')
    subject = _answer_subject(env)
    history = list(body.get('history') or []) if isinstance(body.get('history'), list) else []
    history = history + [{'role': 'user', 'text': plan['question']},
                         {'role': 'agent', 'text': str(env.get('text') or ''), 'meta': {'kind': 'read', 'subject': subject}}]
    deck_body = {'text': (f"Build a deck from this read on {subject}" if subject else 'Build a deck from this'),
                 'history': history, 'thread_id': tid, 'page_context': body.get('context') or body.get('page_context')}
    if subject:
        deck_body.update(bind_subject=subject, confirm_open_screen=True)
    raw, _status = _unpack(h.deck_core(user, deck_body))
    raw = raw if isinstance(raw, dict) else {}
    if raw.get('job_id'):
        return (str(raw.get('confirm_line') or DECK_START_LINE), [], {'kind': 'deck', 'state': 'started', 'deck_job_id': str(raw['job_id'])})
    clar = raw.get('clarify') if isinstance(raw.get('clarify'), dict) else None
    text = str((clar or {}).get('question') or raw.get('reply') or raw.get('error') or '').strip()
    opts = [str(o.get('label') if isinstance(o, dict) else o) for o in ((clar or {}).get('options') or raw.get('followups') or [])][:4]
    if text:
        return ("About the PowerPoint: " + text, opts, {'kind': 'deck', 'state': 'asked'})
    return ('', [], {})


def _email_now(user, body, env, plan):
    h = _host()
    from . import send_file as _sf
    uname = str((user or {}).get('username') or (user or {}).get('email') or '')
    history = list(body.get('history') or []) if isinstance(body.get('history'), list) else []
    raw_env = env.get('raw') if isinstance(env.get('raw'), dict) else {}
    meta = {'kind': 'read', 'subject': _answer_subject(env)}
    if isinstance(raw_env.get('file_link'), dict) and raw_env['file_link'].get('url'):
        meta['link'] = {'url': raw_env['file_link']['url'], 'label': raw_env['file_link'].get('label') or 'Download CSV'}
    history = history + [{'role': 'user', 'text': plan['question']}, {'role': 'agent', 'text': str(env.get('text') or ''), 'meta': meta}]
    out = _sf.answer('Email me this file', uname, user=user, ctx=(body.get('context') or body.get('page_context')),
                     history=history, host=h)
    line = str((out or {}).get('reply') or '').strip()
    return (line, [], {'kind': 'email', 'state': 'sent' if line.lower().startswith('sent') else 'answered'}) if line else ('', [], {})


def _notify_write(job_id, email, uname):
    """The reader's email opt-in for one job (the record the 'email me
    when it lands' chip writes)."""
    from prometheus.legacy import C as _C      # the full app only
    _C._pm_notify_write(job_id, {'email': email, 'user': uname, 'kind': 'read', 'at': time.time(), 'via': 'ride_along'})


def _carry(env, plan, user, body, via):
    if not isinstance(env, dict):
        return env
    uname = str((user or {}).get('username') or (user or {}).get('email') or '')
    tid = env.get('thread_id')
    probe = _faults.is_probe()
    kinds = list(plan.get('kinds') or [])
    kind = str(env.get('kind') or '')
    jobs = _job_ids(env)
    raw = env.get('raw') if isinstance(env.get('raw'), dict) else {}
    waiting = kind in ('clarify', 'guidance', 'nudge') or bool(raw.get('panel_offer')) or bool(raw.get('memory_confirm'))
    if 'deck' in kinds:
        try:
            if jobs or waiting:
                # the answer is not in hand yet (a read still running, a quote to approve, a question back): the
                # deck waits for the read, on record against the thread
                state = 'pending' if jobs else 'awaiting_read'
                line = _named(DECK_NEXT_LINE if jobs else DECK_AFTER_APPROVAL_LINE, plan.get('words'))
                mark = {'kind': 'deck', 'state': state}
                if probe:
                    mark['probe'] = True           # a probe reads the same line a reader would; nothing is recorded
                elif uname and tid:
                    _record(uname, tid, plan, state=state, job_ids=jobs, question=plan['question'])
                    mark['record'] = _key(uname, tid)
                else:
                    # no thread to keep the record on: nothing is promised
                    line = "About the deck: ask for it once this read is in and I will build it from the read."
                    mark = {'kind': 'deck', 'state': 'no_thread'}
                env = _append(env, line, mark=mark)
            elif kind == 'answer':
                if probe:
                    env = _append(env, _named(DECK_START_LINE, plan.get('words')), mark={'kind': 'deck', 'state': 'probe_not_started', 'probe': True})
                else:
                    line, opts, mark = _deck_now(user, body, env, plan, via)
                    if line:
                        env = _append(env, line, options=opts, mark=mark)
            else:
                # a brief to approve, or anything else that is not an answer yet: the deck needs it first. Said
                # plainly, with nothing promised and nothing dropped.
                env = _append(env, _named('About the PowerPoint: it needs this first. Once it is in, say "build the deck" and I '
                                          'will build it from it.', plan.get('words')), mark={'kind': 'deck', 'state': 'needs_answer'})
        except Exception as e:
            _faults.note('ride_along.deck', e)
    if 'email' in kinds:
        try:
            email = str((user or {}).get('email') or '').strip()
            if jobs and email and '@' in email:
                if not probe:
                    for j in jobs:
                        _notify_write(j, email, uname)
                env = _append(env, EMAIL_NEXT_LINE, mark={'email': 'on_landing', **({'probe': True} if probe else {})})
            elif kind == 'answer' and not probe:
                line, opts, mark = _email_now(user, body, env, plan)
                if line:
                    env = _append(env, line, mark=mark)
        except Exception as e:
            _faults.note('ride_along.email', e)
    # a thread the server persists (the key surface, the app) keeps the added line too
    try:
        client = str(body.get('client') or '').strip().lower()
        if bool(body.get('persist', client != 'dashboard')) and tid and uname and env.get('ride_along') and not probe:
            from . import service as _svc
            turns = _svc.load_thread(uname, tid) or []
            for t in reversed(turns):
                if isinstance(t, dict) and str(t.get('role') or '') == 'agent':
                    t['text'] = str(env.get('text') or t.get('text') or '')
                    t['meta'] = dict(t.get('meta') or {}, ride_along=env['ride_along'], corrected_at=_svc._now(), server_note=True)
                    _svc.save_thread(uname, tid, turns)
                    break
    except Exception as e:
        _faults.note('ride_along.persist', e)
    return env


def run(service_ask, user, body, *, via='session'):
    """One ask through ``service_ask`` with a riding deliverable carried."""
    plan = None
    try:
        plan = _plan(body)
    except Exception as e:
        _faults.note('ride_along.plan', e)
        plan = None
    if not plan:
        return service_ask(user, body, via=via)
    b2 = dict(body, text=plan['question'], _pm_ride_along={'kinds': plan['kinds'], 'words': plan['words']})
    env, status = service_ask(user, b2, via=via)
    try:
        env = _carry(env, plan, user, body, via)
    except Exception as e:
        _faults.note('ride_along.carry', e)
    return env, status


# ------------------------------------------------------------------ the landing
def _last_read_subject(history, job_id=''):
    for t in reversed(list(history or [])):
        if not isinstance(t, dict) or str(t.get('role') or '') == 'user':
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        if (job_id and str(meta.get('read_job_id') or '') == str(job_id)) or (not job_id and (meta.get('read_job_id') or meta.get('kind') == 'read')):
            subj = _squash(meta.get('subject') or '')
            if not subj:
                m = re.match(r"^On\s+(?P<s>[^\n:]{2,140}):", str(t.get('text') or ''))
                subj = _squash(m.group('s')) if m else ''
            if subj:
                return subj
    return ''


def _in_flight(history, since):
    """Read jobs acked in this thread that have not landed."""
    from . import read_landing as _rl
    n = 0
    for t in history or []:
        if not isinstance(t, dict) or str(t.get('role') or '') == 'user':
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        if meta.get('read_job_id') and not _rl._is_landed_turn(t):
            n += 1
    return n


def start_deck(user, uname, tid, history, subject, *, deck_core=None, request_context=None):
    """The deck path, from the read that just landed. Returns the turn to
    post into the thread and the mark for the record."""
    h = _host()
    body = {'text': (f"Build a deck from this read on {subject}" if subject else 'Build a deck from this read'),
            'history': list(history or [])[-14:], 'thread_id': tid, 'page_context': None}
    if subject:
        body.update(bind_subject=subject, confirm_open_screen=True)
    core = deck_core or h.deck_core
    if request_context is None:
        from prometheus.legacy import H as _H      # the full app only
        request_context = lambda: _H.app.test_request_context('/api/brief-chat/deck', method='POST')   # noqa: E731
    with request_context():
        raw, _status = _unpack(core(user, body))
    raw = raw if isinstance(raw, dict) else {}
    now = __import__('prometheus.service', fromlist=['_now'])._now()
    if raw.get('job_id'):
        jid = str(raw['job_id'])
        return ({'role': 'agent', 'text': str(raw.get('confirm_line') or DECK_START_LINE), 'ts': now, 'turn_id': f"ride-deck-{jid}",
                 'meta': {'kind': 'deck_started', 'server_note': True, 'ride_along': {'kind': 'deck', 'deck_job': jid},
                          'subject': str(raw.get('subject') or subject)[:120]}},
                {'state': 'started', 'deck_job_id': jid})
    clar = raw.get('clarify') if isinstance(raw.get('clarify'), dict) else None
    text = str((clar or {}).get('question') or raw.get('reply') or raw.get('error') or '').strip()
    if text:
        opts = [{'label': str(o.get('label') if isinstance(o, dict) else o), 'send': str((o.get('send') or o.get('label')) if isinstance(o, dict) else o)}
                for o in ((clar or {}).get('options') or raw.get('followups') or [])][:4]
        return ({'role': 'agent', 'text': 'About the PowerPoint you asked for: ' + text, 'ts': now,
                 'meta': {'kind': 'deck_question', 'server_note': True, 'ride_along': {'kind': 'deck', 'state': 'asked'}, 'options': opts}},
                {'state': 'asked'})
    return None, {'state': 'failed'}


def on_read_landed(uname, job_id, payload=None, *, held=False, probe=False, deck_core=None, request_context=None, user=None):
    """A read landed: a deliverable on record for its thread is started.
    Returns 'none', 'waiting', 'held', 'started', 'asked' or 'failed'."""
    try:
        if probe or not uname or not job_id:
            return 'none'
        from . import service as _svc
        tid = _svc.job_thread_id(job_id)
        if not tid:
            return 'none'
        key = _key(uname, tid)
        rec = _get(key)
        if not isinstance(rec, dict) or rec.get('state') not in ('pending', 'awaiting_read'):
            return 'none'
        if time.time() - float(rec.get('created_at') or 0) > MAX_AGE_S:
            _put(key, dict(rec, state='expired', closed_at=time.time()))
            return 'none'
        if held:
            return 'held'                # the pass that lands it starts the deck
        left = [j for j in (rec.get('job_ids') or []) if str(j) != str(job_id)]
        history = _svc.load_thread(uname, tid) or []
        if left or (not rec.get('job_ids') and _in_flight(history, rec.get('created_at')) > 0):
            _put(key, dict(rec, job_ids=left))
            return 'waiting'
        if 'deck' not in (rec.get('kinds') or []):
            _put(key, dict(rec, state='done', closed_at=time.time()))
            return 'none'
        if user is None:
            users = (_host().load_users() or {}).get('users') or {}
            user = dict(users.get(uname) or {}, username=uname)
        subject = _last_read_subject(history, job_id) or _last_read_subject(history)
        turn, mark = start_deck(user, uname, tid, history, subject, deck_core=deck_core, request_context=request_context)
        _put(key, dict(rec, job_ids=[], closed_at=time.time(), **mark))
        if turn:
            _svc.save_thread(uname, tid, list(history) + [turn])
        if mark.get('state') == 'failed':
            try:
                from . import promise_truth as _pt
                _pt.hold(uname, rec.get('question') or 'a deck of the read', kind='deck', reason='the deck asked for with a question did not start after its read landed',
                         thread_id=tid)
            except Exception as e:
                _faults.note('ride_along.hold', e)
        print(f"[pm-ride-along] read {job_id} landed for {uname}: deck {mark.get('state')} in thread {tid}")
        return str(mark.get('state') or 'none')
    except Exception as e:
        _faults.note('ride_along.landing', e)
        return 'failed'
