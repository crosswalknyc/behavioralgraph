"""'Do not build anything' is honored for the whole ask.

Cara Dorfman at Amazon, 2026-11-16: every one of her questions about a
published study opened with "Do not build anything", "Do not run a
build" or "Text table only". The replies were a persona or whole-service
fork that led to a build, a profile draft with an Approve line, "Nothing
is queued yet", and five read jobs. The words were read by one late
check (the surface decision) and by nothing else.

asked(text)   True when the ask forbids work being started: a build, a
              run, a pull, a read job, a draft, a quote. "Don't build a
              new profile, answer from the existing ones" is narrower (no
              profile, a read of what is held is wanted) and is not this.
mark(text)    Note it on the request (and return it). Every part of a
              split message runs inside the same request, so the mark
              covers the parts too.
active()      True when the request in hand is marked.
REPLY         The one plain line a marked ask gets when no answer lane
              holds the answer. Nothing is started behind it.
refuses(raw)  True when a payload on its way out would start, draft,
              price or ask the way to a build. envelope.wrap swaps such a
              payload for hold(); the lanes that start work check
              active() first, so nothing has been started by then.

Never raises.
"""
from __future__ import annotations

import re

# what is forbidden is work of any kind: "anything", "a build", "a read", "a pull", "a run". "Don't build a new
# profile, answer this from the existing ones" forbids one thing, the profile, and asks for a read of what is
# held; that stays with prometheus.smarter.negated_build (answer mode, the read still runs).
_OBJ = (r"(?:anything|(?:(?:a|an|any|the|another)\s+)?(?:new\s+|fresh\s+)?"
        r"(?:builds?|reads?|pulls?|reports?|jobs?|runs?))")
_FORBID_RX = re.compile(
    r"\b(?:do\s+not|don'?t|dont|never|no\s+need\s+to)\s+(?:build|run|start|queue|launch|pull|create|kick\s+off)\s+" + _OBJ + r"\b|"
    r"\b(?:do\s+not|don'?t|dont)\s+build\b(?=\s*[.!,;:]|\s*$)|"
    r"\bno\s+(?:new\s+)?(?:builds?|read\s+jobs?)\b(?=\s*(?:please|needed|required)?\s*(?:[.,;:!]|$))|"
    r"\bwithout\s+(?:building|running|starting|queu(?:e)?ing)\s+" + _OBJ + r"\b|"
    r"\btext\s+table\s+only\b|"
    r"\b(?:this\s+is|it\s+is|it'?s)\s+(?:just\s+)?a\s+methodology\s+question\b|\bmethodology\s+question\s+only\b|"
    r"\bnothing\s+(?:should|needs\s+to)\s+be\s+(?:built|run|queued)\b", re.I)

# "don't run it yet", "do not start until I say" hold a brief that is wanted; they are not this
_HOLD_RX = re.compile(r"\b(?:do\s+not|don'?t|dont)\s+(?:build|run|start|queue|launch)\s+(?:it|this|that|them)?\s*(?:yet|until|before)\b", re.I)

REPLY = ("Nothing was built or run. I do not hold an answer to that as it stands, and I will not start anything to "
         "get one unless you ask me to.")

_LEAD_RX = re.compile(
    r"(?:(?<=^)|(?<=[.!?\n]))\s*(?:\d{1,2}[.)]\s*)?(?:please\s+)?(?:do\s+not|don'?t|dont)\s+(?:build|run|start|queue)\s+"
    r"(?:anything|a\s+(?:build|read|pull|profile|report))\s*[.!]?|"
    r"(?:(?<=^)|(?<=[.!?\n]))\s*text\s+table\s+only\s*[.!]?|"
    r"(?:(?<=^)|(?<=[.!?\n]))\s*give\s+(?:me\s+)?a\s+text\s+table\s*[.!]?", re.I)


def asked(text):
    """True when the words forbid a build, a run or a read being started."""
    try:
        t = ' '.join(str(text or '').split())
        if not t:
            return False
        if not _FORBID_RX.search(t):
            return False
        if _HOLD_RX.search(t) and not _FORBID_RX.search(_HOLD_RX.sub(' ', t)):
            return False
        return True
    except Exception:
        return False


def strip(text):
    """The ask with its "Do not build anything." / "Text table only."
    sentences removed, when a question is left; else the text as given."""
    try:
        t = str(text or '')
        rest = _LEAD_RX.sub(' ', t)
        rest = re.sub(r"[ \t]{2,}", ' ', rest).strip()
        return rest if len(rest.split()) >= 3 else t
    except Exception:
        return str(text or '')


def _g():
    try:
        from flask import g, has_request_context
        return g if has_request_context() else None
    except Exception:
        return None


_LOCAL = {'on': False}        # outside a request (a unit test, a worker): the last mark


def mark(text, nested=False):
    """Mark the request when the ask forbids a build. Returns whether the
    request is marked. `nested` is a part of a split message or a replayed
    question: it keeps the mark the whole message set."""
    on = asked(text)
    try:
        g = _g()
        if g is not None:
            if on:
                g._pm_no_build = True
            elif not nested:
                g._pm_no_build = False
            return bool(getattr(g, '_pm_no_build', False))
        if on or not nested:
            _LOCAL['on'] = bool(on)
        return bool(_LOCAL['on'])
    except Exception:
        return on


def active():
    try:
        g = _g()
        if g is not None:
            return bool(getattr(g, '_pm_no_build', False))
        return bool(_LOCAL['on'])
    except Exception:
        return False


def clear():
    try:
        g = _g()
        if g is not None:
            g._pm_no_build = False
        _LOCAL['on'] = False
    except Exception:
        pass


_PROMISE_RX = re.compile(
    r"^\s*(?:included\s+with\s+your\s+plan\.\s*)?on\s+it\b|\bthe\s+read\s+will\s+land\s+right\s+here\b|"
    r"\blands\s+right\s+here\s+(?:when|the\s+moment)\b|\bready\s+to\s+run\b|\bapprove\s+to\s+run\b|"
    r"\breply\s+'whole\s+service'\b|\bi\s+will\s+draft\s+the\s+brief\b|\bworking\s+on\s+it\b|\bqueued\b(?!\s+yet)|"
    r"\bsetting\s+up\s+the\s+(?:profile\s+)?brief\b|\bbrief\s+for\s+you\s+to\s+approve\b|\bbuilding\s+the\b[^.]{0,60}\bnow\b", re.I)
_CLARIFY_BUILD_RX = re.compile(
    r"\bwhich\s+subject\s+should\s+this\s+be\s+on\b|\bname\s+the\s+show,\s+brand\s+or\s+person\b|"
    r"\btell\s+me\s+who\s+or\s+what\s+you\s+want\s+pulled\b|\bto\s+build\s+(?:it|all|this)\b|\bbuild\s+it\s+as\b", re.I)
_BUILD_ACTIONS = frozenset(('build_profile', 'draft', 'confirm_build', 'clarify', 'batch_draft', 'subscriber_iq'))


def refuses(raw):
    """True when the payload starts, drafts, prices or asks the way to a
    build or a read: the things a 'do not build' ask must not get."""
    try:
        if not isinstance(raw, dict):
            return False
        if raw.get('no_build_hold') or raw.get('study_record'):
            return False
        if raw.get('panel_offer') or raw.get('read_job_id') or raw.get('read_job_ids') or raw.get('deck_job_id') \
                or raw.get('draft') or raw.get('brief') or raw.get('run_id') or raw.get('audience_confirm'):
            return True
        if str(raw.get('action') or '').strip().lower() in _BUILD_ACTIONS:
            return True
        if str(raw.get('route_hint') or '').strip().lower() == 'interpret':
            return True
        if raw.get('decision') and str(raw.get('decision')) not in ('answer', 'answered'):
            return True
        text = ' '.join(str(raw.get(k) or '') for k in ('reply', 'error', 'message', 'text'))
        if _PROMISE_RX.search(text) or _CLARIFY_BUILD_RX.search(text):
            return True
        return False
    except Exception:
        return False


def hold():
    """The payload a marked ask gets when nothing on hand answers it."""
    return {'success': True, 'action': 'answer', 'reply': REPLY, 'followups': [], 'offer_deck': False,
            'deck_angle': None, 'no_build_hold': True}


__all__ = ['asked', 'strip', 'mark', 'active', 'clear', 'refuses', 'hold', 'REPLY']
