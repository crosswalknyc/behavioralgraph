"""What the user did next (2026-10-02, Jenna: "answer questions people
are asking without so many errors").

The ask log grades a reply by its route label. A one second ledger
replay that the reader answered with "thats not what i asked for"
logs as answered, and the four identical asks cpearson sent in one
afternoon look like four answered questions. This module reads the
only verdict that matters: what the same person did next.

Two signals, both pure text:

    rejection   the next message says the reply was wrong or useless
                ("no", "that's not what I asked", "answer this now",
                "?", "wrong", "try again", "you did not answer")
    repeat      the next message is the same question again

`detect(question, history)` runs online inside the ask log wrapper so
the live record carries `user_signal`. `grade_sequence(records)` runs
offline over a day of ask records and returns one grade per reply the
user pushed back on. Pure functions, no network, never raise.
"""
from __future__ import annotations

import datetime as dt
import re

FAILED_BY_USER = 'failed_by_user'

# A message that is only a confirm word is a step in a guided flow
# (the widget re-posts it per card), never a repeat signal.
_CONFIRM_ONLY_RE = re.compile(
    r"^\s*(?:yes|y|yep|yeah|ok|okay|sure|go|go ahead|run it|approve|"
    r"approved|confirm|confirmed|do it|please|thanks|thank you|1|2|3)"
    r"\s*[.!]*\s*$", re.IGNORECASE)

# The widget appends the date-range chip to the original ask when the
# user confirms a window. That is the same ask moving forward, not a
# repeat of a failed one.
_DATE_RANGE_SUFFIX_RE = re.compile(
    r"\s*[.;,]?\s*date range\s*:.*$", re.IGNORECASE | re.DOTALL)

_REJECTION_RE = re.compile(
    r"^\s*(?:"
    r"no+|nope|wrong|incorrect|"
    r"\?+|"
    r"no[,.!]?\s+(?:that|thats|that's|this|it)\b.*|"
    r"(?:no[,.!]?\s+)?(?:that|thats|that's|this|it)(?:'s| is| was)?\s+"
    r"(?:not|wrong|incorrect|useless|not it|not right|not what)\b.*|"
    r"(?:that|this|it)(?:'s| is)?\s+not\s+what\s+i\s+(?:asked|meant|"
    r"wanted|said)\b.*|"
    r"not\s+what\s+i\s+(?:asked|meant|wanted|said)\b.*|"
    r"(?:just\s+|please\s+)?answer\s+(?:this|it|the\s+question|me)"
    r"(?:\s+now)?\b.*|"
    r"answer\s+this\s+now\b.*|"
    r"(?:please\s+)?try\s+again\b.*|"
    r"(?:i\s+)?(?:already\s+)?(?:said|told\s+you|asked|answered)\s+"
    r"(?:that|this|you)?\b.*|"
    r"you\s+did(?:n't|\s+not)\s+(?:answer|read|listen|do)\b.*|"
    r"(?:that|this)\s+(?:does\s*n[o']t|didn't|did\s+not)\s+"
    r"(?:help|answer|work)\b.*|"
    r"stop\s+(?:asking|repeating|building|offering)\b.*|"
    r"i\s+do(?:n't|\s+not)\s+want\s+(?:a|to|another)\s+"
    r"(?:build|profile|pull)\b.*|"
    r"(?:read|look\s+at)\s+(?:the|my)\s+(?:question|screen|ask)\b.*|"
    r"(?:are\s+you\s+there|hello|still\s+(?:waiting|there))|"
    r"(?:why\s+)?(?:is\s+this|are\s+you)\s+(?:so\s+)?(?:slow|broken|"
    r"not\s+working)\b.*|"
    r"what\s*\?+|huh\s*\?*"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE | re.DOTALL)

_WORD_RE = re.compile(r'[a-z0-9]+')

# A longer message that still says the reply was wrong (Jessie
# 2026-11-04: "this doesnt make sense with a 6,068,313 total US audience",
# "your analysis ... doesn't make sense, as Samsung TV Plus isn't even in
# the data", "You're drawing conclusions based on what already exists in
# the profile. Instead, run a new ask ..."). `is_rejection` is the short,
# anchored form the ask log grades on; this is the thread-level form a
# replay consults before handing the same words back.
_PUSHBACK_RE = re.compile(
    r"\b(?:"
    r"does\s*n[o']?t\s+make\s+(?:any\s+)?sense|doesnt\s+make\s+sense|makes\s+no\s+sense|not\s+make\s+sense|"
    r"is\s*n[o']?t\s+even\s+in\b|not\s+(?:even\s+)?in\s+the\s+(?:data|file|profile)|"
    r"does\s*n[o']?t\s+add\s+up|can\s*n[o']?t\s+be\s+right|cannot\s+be\s+right|"
    r"(?:that|this|it)(?:'s|\s+is|\s+was)?\s+(?:wrong|incorrect|not\s+right|off)|"
    r"wrong\s+(?:answer|base|number|numbers|figure|figures|read|data)|"
    r"not\s+what\s+i\s+(?:asked|meant|wanted|said)|"
    r"(?:you(?:'re|\s+are)\s+)?drawing\s+conclusions|"
    r"go\s+back\s+(?:in\s+)?an?d?\b|redo\b|re-?run\b|rerun\b|run\s+it\s+again|try\s+again|still\s+wrong|"
    r"instead,?\s+run\b|run\s+(?:a\s+)?(?:new|fresh)\s+(?:ask|read|pull|look|pass)|"
    r"(?:a\s+)?fresh\s+(?:ask|read|pull|look|pass)|new\s+look\s*/?\s*pull"
    r")\b", re.IGNORECASE)


# the widget's note for a dropped-in file; never a verdict on the reply above it
_UPLOAD_NOTE_RE = re.compile(r"^\s*(?:uploaded|attached|attaching|here is the file|file:)\b", re.IGNORECASE)


def pushes_back(text):
    """True when the message says the prior reply was wrong, whatever its
    length: the short anchored rejection, or a sentence that names what
    did not make sense and asks for it again."""
    t = str(text or '').strip()
    if not t:
        return False
    if is_rejection(t):
        return True
    return bool(_PUSHBACK_RE.search(t))


def _reply_fold(text, width=240):
    t = str(text or '')
    t = re.sub(r"^\s*Same read as earlier[^\n]*\n+", '', t)   # a replay lead never changes the words
    return norm(t)[:width]


def rejected_reply(reply, history):
    """True when this thread already showed ``reply`` and the reader's
    next message pushed back on it. A stored read the reader rejected is
    never handed back again as the answer to a repeat (Jessie 2026-11-04:
    the 62.4% FAST read came back verbatim after "isn't even in the
    data"). Pure; never raises."""
    try:
        want = _reply_fold(reply)
        if len(want) < 40:
            return False
        turns = [t for t in (history or []) if isinstance(t, dict)]
        for i, t in enumerate(turns):
            if str(t.get('role') or '') == 'user':
                continue
            have = _reply_fold(t.get('text') or t.get('content') or '')
            if not have or not (have == want or have.startswith(want) or want.startswith(have)):
                continue
            # the reader's verdict is the first thing they SAID after it; a
            # file they dropped in on the way ("Uploaded X.csv") is not a verdict
            for x in turns[i + 1:]:
                if str(x.get('role') or '') != 'user':
                    continue
                said = str(x.get('text') or x.get('content') or '')
                if _UPLOAD_NOTE_RE.match(said):
                    continue
                if pushes_back(said):
                    return True
                break
        return False
    except Exception:
        return False


def norm(text):
    """Casefold, strip the date-range chip, collapse to words."""
    t = _DATE_RANGE_SUFFIX_RE.sub('', str(text or '').casefold())
    return ' '.join(_WORD_RE.findall(t))


def _tokens(text):
    return set(norm(text).split())


def has_date_chip(text):
    """True when the widget's date-range chip rides on the message."""
    return bool(_DATE_RANGE_SUFFIX_RE.search(str(text or '')))


def is_confirm_only(text):
    """A bare confirm word, with or without the date-range chip."""
    t = _DATE_RANGE_SUFFIX_RE.sub('', str(text or ''))
    return bool(_CONFIRM_ONLY_RE.match(t))


def is_flow_step(question, prior_question):
    """The same ask coming back with the date-range chip attached is
    the guided flow moving forward (the user picked a window), not a
    repeat of a failed reply."""
    return (has_date_chip(question) and not has_date_chip(prior_question)
            and norm(question) == norm(prior_question))


def is_rejection(text):
    """True when the message pushes back on the prior reply rather
    than asking something new. Long messages that happen to start
    with 'no' are a new ask, so the match is anchored at both ends and
    capped at a short length."""
    t = str(text or '').strip()
    if not t or len(t) > 160:
        return False
    if is_confirm_only(t):
        return False
    return bool(_REJECTION_RE.match(t))


def is_repeat(question, prior_question, threshold=0.85):
    """True when `question` is the same ask as `prior_question`: equal
    after normalisation, or a token overlap at or above `threshold`
    on asks of at least three words. Confirm-only words never count."""
    a, b = norm(question), norm(prior_question)
    if not a or not b:
        return False
    if is_confirm_only(question) or is_flow_step(question, prior_question):
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    if min(len(ta), len(tb)) < 3:
        return False
    inter = len(ta & tb)
    union = len(ta | tb)
    return union > 0 and (inter / union) >= threshold


def prior_turns(question, history):
    """(prior_user_text, prior_agent_text) from a widget history list
    of {role, text}. The history may or may not already end with the
    current question; either way the turn before it is returned."""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    # The current ask is only "already in the history" when it is the
    # very last turn with no reply after it. A matching user turn that
    # was answered is a prior ask (that is the repeat case).
    if turns and str(turns[-1].get('role') or '') == 'user' \
            and norm(turns[-1].get('text')) == norm(question):
        turns = turns[:-1]
    user_turns = [t for t in turns if str(t.get('role') or '') == 'user']
    agent_turns = [t for t in turns if str(t.get('role') or '') != 'user']
    prior_user = str(user_turns[-1].get('text') or '') if user_turns else ''
    prior_agent = str(agent_turns[-1].get('text') or '') \
        if agent_turns else ''
    return prior_user, prior_agent


def detect(question, history):
    """Online signal for the ask being logged now. Returns None, or
    {'signal': 'rejection'|'repeat', 'prior_question': str,
    'prior_reply': str}. Never raises."""
    try:
        prior_q, prior_a = prior_turns(question, history)
        if not (prior_q or prior_a):
            return None
        sig = None
        if is_rejection(question):
            sig = 'rejection'
        elif prior_q and is_repeat(question, prior_q):
            sig = 'repeat'
        if not sig:
            return None
        return {'signal': sig,
                'prior_question': prior_q[:200],
                'prior_reply': prior_a[:300]}
    except Exception:
        return None


def _parse_ts(value):
    try:
        return dt.datetime.strptime(str(value or ''), '%Y-%m-%dT%H:%M:%SZ') \
            .replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


def grade_sequence(records, window_s=300, skip_users=('unknown', ''),
                   chain_s=5, reject_window_s=3600):
    """Offline grading over ask-log records (dicts with ts, user,
    question, route, outcome, view). For each reply, the same user's
    next message decides: a repeat within `window_s` or a rejection
    within `reject_window_s` grades the reply FAILED_BY_USER. (A
    rejection names the prior reply however long the reader took to
    type it; a repeat only reads as a retry when it is quick.)

    Returns a list of grades, one per failed reply, each carrying the
    failed record's fields plus `signal`, `next_question`, `next_ts`
    and `gap_s`. Records are read in time order per user; a reply is
    graded once, by the first qualifying follow-up."""
    out = []
    by_user = {}
    try:
        from . import probe_identity as _pid
        _is_probe = _pid.is_probe_user
    except Exception:
        _is_probe = lambda u, record=None: False  # noqa: E731
    for r in records:
        if not isinstance(r, dict):
            continue
        u = str(r.get('user') or '')
        if u in skip_users or _is_probe(u):
            continue
        if not str(r.get('question') or '').strip():
            continue
        by_user.setdefault(u, []).append(r)
    for u, recs in by_user.items():
        recs = sorted(recs, key=lambda r: str(r.get('ts') or ''))
        for i, cur in enumerate(recs):
            t0 = _parse_ts(cur.get('ts'))
            if t0 is None:
                continue
            for nxt in recs[i + 1:]:
                t1 = _parse_ts(nxt.get('ts'))
                if t1 is None:
                    continue
                gap = (t1 - t0).total_seconds()
                if gap < 0:
                    continue
                if gap > max(window_s, reject_window_s):
                    break
                nq = str(nxt.get('question') or '')
                same = norm(nq) == norm(cur.get('question'))
                if same:
                    # `ts` is written when a request ends. The widget
                    # chains a second request on the same text the
                    # moment the first answers (analyze, then
                    # interpret), and the one-door route logs both
                    # surfaces of one call. Neither is a human
                    # re-ask: the second request STARTED within a few
                    # seconds of the first one ending.
                    try:
                        started = t1 - dt.timedelta(
                            milliseconds=int(nxt.get('ms') or 0))
                    except Exception:
                        started = t1
                    if (started - t0).total_seconds() <= chain_s:
                        continue
                if is_flow_step(nq, cur.get('question')):
                    # The guided flow moved forward (date chip).
                    break
                if is_rejection(nq):
                    sig = 'rejection'
                elif gap <= window_s and is_repeat(nq, cur.get('question')):
                    sig = 'repeat'
                else:
                    # A different ask: the reply was accepted enough
                    # to move on. Stop looking at this reply.
                    break
                g = {k: cur.get(k) for k in ('ts', 'user', 'view',
                                             'question', 'surface',
                                             'route', 'outcome', 'ms',
                                             'subject')}
                g.update({'grade': FAILED_BY_USER, 'signal': sig,
                          'next_question': nq[:300],
                          'next_ts': nxt.get('ts'),
                          'gap_s': int(gap)})
                out.append(g)
                break
    out.sort(key=lambda g: str(g.get('ts') or ''))
    return out


def summarize(grades, total_asks):
    """Counts the status tile and the nightly email read."""
    by_route, by_outcome, by_signal, by_user = {}, {}, {}, {}
    for g in grades:
        by_route[g.get('route') or '?'] = \
            by_route.get(g.get('route') or '?', 0) + 1
        by_outcome[g.get('outcome') or '?'] = \
            by_outcome.get(g.get('outcome') or '?', 0) + 1
        by_signal[g.get('signal') or '?'] = \
            by_signal.get(g.get('signal') or '?', 0) + 1
        by_user[g.get('user') or '?'] = \
            by_user.get(g.get('user') or '?', 0) + 1
    n = len(grades)
    rate = (n / total_asks) if total_asks else 0.0
    return {'asks': int(total_asks), 'failed_by_user': n,
            'rate': round(rate, 4),
            'by_route': dict(sorted(by_route.items(),
                                    key=lambda kv: -kv[1])),
            'by_outcome': dict(sorted(by_outcome.items(),
                                      key=lambda kv: -kv[1])),
            'by_signal': by_signal,
            'by_user': dict(sorted(by_user.items(),
                                   key=lambda kv: -kv[1]))}


__all__ = ['FAILED_BY_USER', 'detect', 'grade_sequence', 'summarize',
           'is_rejection', 'is_repeat', 'is_confirm_only', 'norm',
           'prior_turns', 'pushes_back', 'rejected_reply']
