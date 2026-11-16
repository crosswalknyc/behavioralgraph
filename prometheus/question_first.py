"""A question typed where profiles are built is a question first
(Jenna 2026-11-13).

Samantha Kolton (CAA), 2026-11-06, in the place profiles are built:
"What type of credit card does Paige Spiranac's have?" drew a $300
profile brief. She approved it, the profile was built and emailed, and
the question was never answered.

Two causes.

1. The words were never read as a question. The router keeps status,
   credits, refresh, sample size and incidence asks on the build
   surface, and its word for credits matched the "credit" in "credit
   card", so the question mark never got its say and the ask fell to
   the build flow. ("credit card", "credit union", "credit score" are
   not about the reader's credits; ``understand._OPS_WORDS_RX``.)
2. Nothing remembered the question. A question that led to a build used
   to be kept and asked again when the build landed (Jenna 2026-10-06:
   "if someone asks a question and is then prompted to pull a profile
   it should answer the question asked after profiles been pulled").
   The one place that kept it was the build-first offer. On 2026-10-08
   that offer was retired on purpose ("take out force running a profile
   and just charge 500 for the custom ask", a894d7506), and the keeping
   went with it. Everything that answers a kept question is still
   there: the status poll hands it back and the chat asks it ("Now
   answering your original question."), and the server asks it on the
   thread when the tab is closed (prometheus.pending_answers). Only the
   write was gone.

The rule now.

  * A question about an audience typed where profiles are built gets
    the priced read offer, in the house's own wording, the same as
    anywhere else. The build stays one tap away: the offer carries a
    third chip, "Build a profile on <subject>", only when the question
    was typed where profiles are built and the dashboard does not
    already hold that profile. Nothing about the price changes.
  * A build that follows a question is a build started from that
    question: the reader was quoted the read, did not take it, and
    ordered the profile on the same subject in the same thread (the
    chip, or their own words). The question rides the brief
    (``origin_question``), and when the build is started it is kept
    (the same store, the same one-shot pop). When the profile lands the
    question is asked again on that thread and answered from the
    finished profile. What that answer costs is whatever the same
    question costs on a profile that exists; no price is set here.
  * A question about what we can see or tell apart, typed while a
    brief's own question is open (broad or listeners, which season, the
    window), is answered, and the brief stays open at the same step.
    "Not building a profile here" sets the brief aside. Before this an
    open step took every message as its answer and asked again (David
    Carter at Sony, 2026-11-13, twice).

Pure functions over the words, the thread and the payloads; the store
writes stay with their owners. Nothing here raises. Kill switch
``PM_QUESTION_FIRST=0``.
"""
from __future__ import annotations

import os
import re

__all__ = ['enabled', 'is_question', 'build_chip', 'with_build_chip', 'origin_for_build', 'attach_origin',
           'origin_for_launch', 'at_open_step', 'BUILD_CHIP_LEAD']

BUILD_CHIP_LEAD = 'Build a profile on '
ORIGIN_KEY = 'origin_question'
_MAX_Q = 500

_NOT_BUILDING_RX = re.compile(
    r"^\W*(?:no[,.]?\s+)?(?:i(?:'| a)?m\s+|i\s+am\s+|we(?:'| a)?re\s+)?not\s+(?:building|running|making|pulling|ordering|"
    r"looking\s+to\s+(?:build|run))\b[^?]{0,40}$|"
    r"^\W*(?:no[,.]?\s+)?(?:i\s+)?(?:do\s+not|don'?t|dont)\s+(?:want|need)\s+(?:to\s+(?:build|run|pull)\s+)?(?:a\s+|the\s+|any\s+)?"
    r"(?:profile|build|brief)\b[^?]{0,30}$|"
    r"^\W*no\s+(?:profile|build|brief)\b[^?]{0,20}$", re.I)


def enabled():
    return str(os.environ.get('PM_QUESTION_FIRST', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def _clean(s):
    return ' '.join(str(s or '').split())


def is_question(text):
    """True for a question that is not an order: a question mark or a
    sentence that asks, and no instruction to build, run, pull or cut."""
    t = _clean(text)
    if not t or len(t) > 1200:
        return False
    try:
        from . import guards as _g
        if not _g.is_question_shaped(t):
            return False
    except Exception:
        if '?' not in t:
            return False
    try:
        from . import orders as _ord
        if _ord.is_order(t) or _ord.is_profile_order(t):
            return False
    except Exception:
        pass
    try:
        from . import smarter as _sm
        if _sm.polite_order(t):
            return False
    except Exception:
        pass
    try:
        from . import understand as _u
        if _u._BUILD_VERB_RX.search(t) or _u._CUT_BY_RX.search(t):
            return False
    except Exception:
        pass
    return True


# ------------------------------------------------------------- the build chip
def _name_of(subject):
    try:
        from . import quote_subject as _qs
        return _qs.split(subject)[0]
    except Exception:
        return _clean(subject)


def build_chip(subject):
    """'Build a profile on Paige Spiranac' for a quoted subject, or ''."""
    name = _clean(_name_of(subject))
    if not name or name.lower() in ('that subject', 'this', 'that', 'it') or len(name.split()) > 8:
        return ''
    return BUILD_CHIP_LEAD + name


def _held(name):
    """True when the dashboard already holds a profile under the name."""
    try:
        from . import priced_read as _pr
        return bool(_pr._record_spelling(name))
    except Exception:
        return False


def with_build_chip(raw, text, client_surface='', held=None):
    """The priced offer with the build one tap away, when the question
    was typed where profiles are built. The offer's own words and its
    Approve and Cancel chips are untouched; a third chip is added. Not
    for a sizing or a breakdown quote, a multi-part quote, a subject the
    dashboard already holds, or everyone in the US."""
    try:
        if not enabled() or not isinstance(raw, dict) or str(client_surface or '').lower() != 'interpret':
            return raw
        po = raw.get('panel_offer')
        if not isinstance(po, dict) or po.get('parts') or str(po.get('kind') or '') in ('sizing', 'derived', 'viewership'):
            return raw
        if not is_question(text):
            return raw
        subject = str(po.get('subject') or raw.get('subject') or '')
        try:
            from . import priced_read as _pr
            if _pr.is_everyone(subject):
                return raw
        except Exception:
            pass
        chip = build_chip(subject)
        if not chip:
            return raw
        name = chip[len(BUILD_CHIP_LEAD):]
        if (held(name) if callable(held) else _held(name)):
            return raw
        chips = [str(c) for c in (raw.get('followups') or []) if c]
        if chip in chips:
            return raw
        return dict(raw, followups=chips + [chip], build_chip=chip)
    except Exception:
        return raw


# ----------------------------------------------- a build started from a question
def _same_subject(a, b):
    try:
        from . import pending_answers as _pa
        return bool(_pa.subject_matches(_name_of(a), _name_of(b)) or _pa.subject_matches(_name_of(b), _name_of(a)))
    except Exception:
        return _clean(a).lower() == _clean(b).lower()


def origin_for_build(text, history, open_offer=None):
    """{'question', 'subject'} when this turn orders a build that follows
    a question, else None.

    ``open_offer`` is the open priced offer on this thread
    (prometheus.pending_offers.find): the question it quoted is the
    origin when this turn is an order. A turn that is itself a question
    and still reaches the build flow is its own origin (subject '' then:
    the brief names it)."""
    try:
        if not enabled():
            return None
        t = _clean(text)
        if not t:
            return None
        if is_question(t):
            return {'question': t[:_MAX_Q], 'subject': ''}
        if isinstance(open_offer, dict):
            q = _clean(open_offer.get('question'))
            if q and is_question(q) and not open_offer.get('parts'):
                return {'question': q[:_MAX_Q], 'subject': _clean(open_offer.get('subject'))}
        return None
    except Exception:
        return None


def _draft_of(raw):
    if not isinstance(raw, dict):
        return None
    for k in ('spec_draft', 'draft'):
        if isinstance(raw.get(k), dict):
            return raw[k]
    return None


def _draft_subject(draft):
    return _clean((draft or {}).get('subject') or (draft or {}).get('display_name') or (draft or {}).get('name')
                  or (draft or {}).get('subject_name'))


def attach_origin(raw, origin, thread_id=''):
    """Put the question on the brief the build flow drafted, when the
    brief is on the subject the question was quoted on. Returns True
    when it was attached. A brief that already carries one keeps it."""
    try:
        draft = _draft_of(raw)
        if not enabled() or draft is None or not isinstance(origin, dict):
            return False
        q = _clean(origin.get('question'))
        if not q:
            return False
        if isinstance(draft.get(ORIGIN_KEY), dict) and draft[ORIGIN_KEY].get('question'):
            return False
        subj, osubj = _draft_subject(draft), _clean(origin.get('subject'))
        if not subj:
            return False
        if osubj and not _same_subject(osubj, subj):
            return False        # a build on something else is not started from that question
        draft[ORIGIN_KEY] = {'question': q[:_MAX_Q], 'thread_id': str(thread_id or '')[:64]}
        return True
    except Exception:
        return False


def origin_for_launch(draft, request_origin=None, subject=''):
    """{'question', 'thread_id'} to keep when a build is started, else
    None: the question the brief carries, or (a build that starts in the
    same turn it was ordered) the one noted on the request, when it is
    on this subject."""
    try:
        if not enabled():
            return None
        o = (draft or {}).get(ORIGIN_KEY) if isinstance(draft, dict) else None
        if isinstance(o, str) and o.strip():
            o = {'question': o}
        if isinstance(o, dict) and _clean(o.get('question')):
            q = _clean(o.get('question'))[:_MAX_Q]
            return {'question': q, 'thread_id': str(o.get('thread_id') or '')[:64]} if is_question(q) else None
        r = request_origin if isinstance(request_origin, dict) else None
        if r and _clean(r.get('question')):
            osubj = _clean(r.get('subject'))
            name = _clean(subject) or _draft_subject(draft)
            if osubj and name and not _same_subject(osubj, name):
                return None
            q = _clean(r.get('question'))[:_MAX_Q]
            return {'question': q, 'thread_id': str(r.get('thread_id') or '')[:64]} if is_question(q) else None
        return None
    except Exception:
        return None


def take_origin(draft):
    """The question to keep for the build this brief starts, read off the
    brief (or the request, for a build ordered and started in one turn)
    and taken off the brief so it never rides to the engine. None when
    there is none. Never raises."""
    origin = None
    try:
        noted = None
        try:
            import flask
            noted = getattr(flask.g, '_pm_origin_question', None)
        except Exception:
            noted = None
        origin = origin_for_launch(draft, noted, _draft_subject(draft))
    except Exception:
        origin = None
    try:
        if isinstance(draft, dict):
            draft.pop(ORIGIN_KEY, None)
    except Exception:
        pass
    return origin


def keep(stash, username, subject, origin, thread_id=''):
    """Hand the kept question to the pending-question store (the writer
    is passed in) once the build is started. Never raises."""
    try:
        if not (enabled() and isinstance(origin, dict) and _clean(origin.get('question')) and subject):
            return False
        stash(username, subject, origin['question'],
              thread_id=str(origin.get('thread_id') or thread_id or '') or None)
        return True
    except Exception:
        return False


# ------------------------------------------------ a question at an open brief step
def at_open_step(answer, step='', subject=''):
    """What to say when the message typed at a brief's open question is
    not an answer to it: {'message', 'keep': True} for a question about
    what we can see or tell apart (the brief stays open at the same
    step), {'message', 'discard': True} when the reader says they are
    not building, None for anything else (the step reads it as before)."""
    try:
        if not enabled():
            return None
        t = _clean(answer)
        if not t or len(t) > 600:
            return None
        t2 = re.sub(r"^\W*(?:new|another|one more|different|separate|side|quick|unrelated)\s+question\s*[:,.\-]*\s*", '', t, flags=re.I) or t
        name = _clean(subject)
        name = '' if name.lower() in ('', 'this profile') else name
        if _NOT_BUILDING_RX.match(t) and '?' not in t:
            return {'discard': True,
                    'message': ("Understood. " + (f"The {name} brief" if name else "That brief")
                                + " is set aside and nothing is queued. Ask me anything else.")}
        reply = ''
        try:
            from . import can_do as _cd
            hit = _cd.answer(t2) or _cd.explain(t2)
            if not hit:
                from . import data_capability as _dc
                hit = _dc.answer(t2)
            reply = str((hit or {}).get('reply') or '')
        except Exception:
            reply = ''
        if not reply:
            return None
        back = (f"Your {name} brief is still open. " if name else "Your brief is still open. ") + \
            "Answer the question above to go on, or say cancel to set it aside."
        return {'keep': True, 'message': f"{reply}\n\n{back}"}
    except Exception:
        return None
