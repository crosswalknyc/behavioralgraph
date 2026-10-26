"""A subject set in the thread outlives the open page (Jenna 2026-10-26,
Emmet Riley at Kartel).

Emmet, Summers Eve open on his screen, typed "the subject is the
university of virginia". The reply dropped the page ("That is context
only, and I am dropping it from the read") and every later turn
contradicted it: "Do you mean for UVA consumers ..., or Summers Eve?"
came back two turns later from cross-session memory, and his complaint
("why do you keep bringing up summers eve this is an independent pull")
drafted a $275 build. Debi had the same complaint the same day ("this
has nothing to do with Colin and Samir or Mark Rober").

The thread remembers what the reader set, in one place:

  statement    "the subject is X", "subject: X", "make the subject X",
               "use X as the subject" pins X for the rest of the thread
  correction   "why do you keep bringing up Y", "stop bringing up Y",
               "this has nothing to do with Y", "this is independent of
               Y" drops Y from the thread for good; the thread's own
               subject stays pinned
  offer / draft the subject Prometheus itself quoted or drafted is noted
               as the thread subject when nothing stronger is pinned
               (the dashboard client writes its own turns without the
               server's meta, so the history alone cannot say)

The pin lives on the thread's entry in the user's thread index (the
document the rail already loads), under ``pin``::

    {'subject': 'University of Virginia', 'source': 'statement',
     'set_at': '2026-10-26T21:00:10Z', 'dropped': ['summers eve']}

It is read once at the door (service.stash_binding) onto
``flask.g._pm_thread_pin`` and every clarify and lane consults it
before the page: subject_rules.bound_thread_subject (the thread
subject every binder reads first), page_rejected_in_thread (a dropped
name is never offered), bind_or_continue (a pinned subject binds
instead of the open-screen question), the generate path's memory
ladder (never a which-one question from cross-session memory when the
thread has a subject). Pure text helpers plus small index writes
through the host; fail-safe everywhere.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import faults as _faults

__all__ = ['statement_subject', 'dropped_names', 'pin_of_thread', 'current', 'pinned_subject',
           'pinned_source', 'is_dropped', 'stash', 'scope_to_history', 'pin', 'drop', 'note',
           'last_user_ask', 'STRONG_SOURCES']

# A statement or a correction beats what Prometheus itself quoted. An
# order that names its audience ("query a dataset on UVA consumers")
# sits between: it beats a quote or a draft, never a statement.
STRONG_SOURCES = frozenset(('statement', 'correction'))
_RANK = {'statement': 3, 'correction': 3, 'order': 2, 'draft': 1, 'offer': 1, '': 0}

_POINTERS = frozenset(('this', 'that', 'it', 'the same', 'this one', 'that one', 'this profile',
                       'the profile', 'this audience', 'the audience', 'the open profile',
                       'the page', 'this page', 'whatever is open', 'the one open',
                       # a statement about the subject, not a name
                       'unclear', 'not clear', 'clear', 'wrong', 'right', 'different', 'fine', 'ok', 'okay',
                       'correct', 'incorrect', 'missing', 'blank', 'empty', 'confusing', 'too broad', 'too narrow'))
# "the audience is 44% female", "the audience is mostly women": a description, never a name
_DESCRIPTION_RX = re.compile(r"^(?:\d|%|mostly|mainly|largely|primarily|predominantly|skew|older|younger|female|male|women|men|"
                             r"very|quite|pretty|really|too|more|less|about|around|roughly|over|under|at least)\b", re.I)

_STATEMENT_RXS = (
    # "the subject is the university of virginia", "subject: UVA", "the audience should be Nike runners"
    re.compile(r"^\W*(?:ok(?:ay)?|so|right|sorry|yes|no)?[,.!: ]*(?:the\s+)?(?:profile\s+|build\s+|pull\s+)?"
               r"(?:subject|audience|brand|entity|topic|target\s+audience)\s*"
               r"(?:is|:|=|-|should\s+be|would\s+be|will\s+be|needs?\s+to\s+be|has\s+to\s+be|is\s+going\s+to\s+be)\s+"
               r"(?P<name>[^?\n]{2,120}?)\s*[.!]*\s*$", re.I),
    # "make the subject UVA", "set the subject to UVA", "change the subject to UVA"
    re.compile(r"^\W*(?:please\s+)?(?:make|set|change|switch|update)\s+the\s+(?:subject|audience)\s+(?:to\s+|as\s+)?"
               r"(?P<name>[^?\n]{2,120}?)\s*[.!]*\s*$", re.I),
    # "use UVA as the subject", "UVA is the subject"
    re.compile(r"^\W*(?:please\s+)?(?:use|take|treat)\s+(?P<name>[^?\n]{2,120}?)\s+as\s+the\s+(?:subject|audience)\s*[.!]*\s*$", re.I),
    re.compile(r"^\W*(?P<name>[^?\n]{2,120}?)\s+is\s+the\s+(?:subject|audience)(?:\s+(?:here|of\s+this|for\s+this(?:\s+pull)?))?\s*[.!]*\s*$", re.I),
)

# Names a correction pushes away. "why do you keep bringing up summers
# eve", "stop mentioning Salish Matter", "this has nothing to do with
# Colin and Samir or Mark Rober", "this is independent of LeBron James".
# "bringing up" as people type it in a hurry: bringing, brining, bringin, brought
_BRING_UP = r"br(?:ing|in|ought|igi|ini)\w*\s+up"
_DROP_RX = re.compile(
    r"(?:why\s+(?:do|does|did|would|are)\s+(?:you|it|this|prometheus)\s+(?:keep|still|always)\s+(?:" + _BRING_UP + r"|mentioning|saying|offering|suggesting|pulling\s+(?:up|in)|defaulting\s+to|using|referencing|talking\s+about)|"
    r"(?:keep|keeps)\s+(?:" + _BRING_UP + r"|mentioning|offering|suggesting|coming\s+back\s+to)|"
    r"stop\s+(?:" + _BRING_UP + r"|mentioning|offering|suggesting|using|defaulting\s+to|talking\s+about|referencing)|"
    r"(?:has|have|had|is|was)\s+nothing\s+to\s+do\s+with|nothing\s+to\s+do\s+with|"
    r"(?:is|was|are)\s+(?:an?\s+)?(?:independent|separate|unrelated|different)\s+(?:of|from|to)|"
    r"independent\s+of|unrelated\s+to|separate\s+from|not\s+(?:about|on|for|interested\s+in|asking\s+about)|"
    r"forget\s+(?:about\s+)?|drop|leave\s+out|instead\s+of|rather\s+than)\s+"
    r"(?P<names>[A-Za-z0-9][A-Za-z0-9&'+.:/-]*(?:\s+[A-Za-z0-9&'+.:/-]+){0,7})", re.I)
# the name before the complaint: "no! LeBron James has nothing to do with this"
_DROP_BEFORE_RX = re.compile(
    r"(?P<names>[A-Za-z][A-Za-z0-9&'+.:/-]*(?:\s+[A-Za-z0-9][A-Za-z0-9&'+.:/-]*){0,4}?)\s+"
    r"(?:has|have|had|is|was)\s+(?:nothing\s+to\s+do\s+with|not\s+(?:what|the\s+subject|relevant|related|part\s+of)|"
    r"(?:an?\s+)?(?:independent|separate|unrelated|different)\s+(?:of|from|to)|irrelevant)\b", re.I)
_DROP_BEFORE_SKIP = frozenset(('no', 'nope', 'this', 'that', 'it', 'which', 'what', 'the', 'a', 'an', 'but', 'and',
                               'so', 'okay', 'ok', 'well', 'also', 'again', 'sorry', 'please', 'i', 'we', 'you', 'he',
                               'she', 'they', 'there', 'here', 'pull', 'ask', 'question', 'profile', 'read', 'build',
                               'one', 'this one', 'that one'))
_DROP_TAIL = frozenset(('specifically', 'again', 'anymore', 'please', 'here', 'now', 'though', 'this', 'that',
                        'is', 'it', 'its', 'the', 'a', 'an', 'in', 'on', 'for', 'and', 'or', 'but', 'so', 'then',
                        'profile', 'pull', 'ask', 'question', 'thread', 'build', 'read', 'data', 'dataset',
                        'independent', 'separate', 'unrelated', 'different', 'new', 'other', 'another', 'own'))
_DROP_LEAD = frozenset(('the', 'a', 'an', 'about', 'on', 'with', 'to', 'of', 'that', 'this', 'up'))
_DROP_SPLIT_RX = re.compile(r"\s*(?:,|\bor\b|/)\s*", re.I)
_INSTITUTION_ARTICLE_RX = re.compile(
    r"^the\s+(?=(?:university|college|school|institute|academy|city|state|county|town|church|hospital|"
    r"bank|company|brand|team|league|club|band|network|channel|platform|app|store|chain|agency|"
    r"audience|people|fans|viewers|buyers|users|customers|shoppers|consumers|subscribers|listeners|readers)\b)", re.I)
_DROP_WORDS_NEVER = frozenset(('this', 'that', 'it', 'me', 'us', 'you', 'them', 'these', 'those', 'one',
                               'everything', 'anything', 'nothing', 'something'))


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or '').lower()).strip()


def _now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _clean_display(raw):
    try:
        from . import subject_rules as _sr
        return _sr.clean_display(raw) or ''
    except Exception:
        return ' '.join(str(raw or '').split()).strip(" .,;:'\"")


def statement_subject(text):
    """The subject a statement sets, as a reader sees it, or ''.

    "the subject is the university of virginia" -> "University of
    Virginia"; "subject: nike runners" -> "Nike Runners"; "the subject
    is this profile" -> '' (a pointer, not a name); a question or a
    long message -> ''."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 160 or '?' in t:
        return ''
    for rx in _STATEMENT_RXS:
        m = rx.match(t)
        if not m:
            continue
        raw = m.group('name').strip(" .,;:'\"")
        if not raw or raw.lower() in _POINTERS:
            return ''
        # "the subject is the university of virginia, not summers eve":
        # the name stops at the first clause break
        raw = re.split(r"\s*(?:,|;|\bnot\b|\binstead\b|\brather\b)\s*", raw, maxsplit=1)[0].strip(" .,;:'\"")
        if not raw or raw.lower() in _POINTERS or _DESCRIPTION_RX.match(raw) or '%' in raw:
            return ''
        # "the university of virginia" names the University of Virginia;
        # a title that begins with The ("the office") keeps its article
        raw = _INSTITUTION_ARTICLE_RX.sub('', raw)
        name = _clean_display(raw)
        if name and len(name) >= 2:
            return name
        return ''
    return ''


def dropped_names(text):
    """The names a correction pushes away, lower-cased and folded, most
    specific first. "why do you keep bringing up summers eve this is an
    independent pull" -> ['summers eve']; "this has nothing to do with
    Colin and Samir or Mark Rober" -> ['colin and samir', 'mark rober']
    (the "and" phrase is kept whole, since a duo is one name, and its
    halves ride along so either spelling is excluded)."""
    out = []
    t = ' '.join(str(text or '').split())
    if not t:
        return out

    def _add(k):
        k = _norm(k)
        if k and k not in _DROP_WORDS_NEVER and k not in out and len(k) >= 3:
            out.append(k)

    for m in _DROP_RX.finditer(t):
        words = m.group('names').split()
        while words and words[0].lower().strip('.,;:!?') in _DROP_LEAD:
            words.pop(0)
        # the clause ends where the complaint goes on ("summers eve this
        # is an independent pull" -> summers eve)
        cut = len(words)
        for i, w in enumerate(words):
            if i and w.lower().strip('.,;:!?') in _DROP_TAIL and w.lower() not in ('and', 'or'):
                cut = i
                break
        words = words[:cut]
        while words and words[-1].lower().strip('.,;:!?') in _DROP_TAIL:
            words.pop()
        phrase = ' '.join(words).strip(' .,;:!?')
        if not phrase:
            continue
        for part in _DROP_SPLIT_RX.split(phrase):
            part = part.strip(' .,;:!?')
            if not part:
                continue
            _add(part)
            if re.search(r"\s+and\s+", part, re.I):
                for half in re.split(r"\s+and\s+", part, flags=re.I):
                    _add(half)
    for m in _DROP_BEFORE_RX.finditer(t):
        words = [w for w in m.group('names').split()]
        while words and words[0].lower().strip('.,;:!?') in _DROP_BEFORE_SKIP:
            words.pop(0)
        phrase = ' '.join(words).strip(' .,;:!?')
        if phrase and phrase.lower() not in _DROP_BEFORE_SKIP:
            _add(phrase)
    return out


# ------------------------------------------------------------------ store
def pin_of_thread(idx, tid):
    """The pin on thread ``tid`` in ``idx``, or None."""
    for t in (idx or {}).get('threads', []) or []:
        if isinstance(t, dict) and t.get('id') == tid:
            p = t.get('pin')
            return dict(p) if isinstance(p, dict) and (p.get('subject') or p.get('dropped')) else None
    return None


def current():
    """The pin stashed for the request in flight, or None."""
    try:
        from flask import g as _g
        p = getattr(_g, '_pm_thread_pin', None)
        return p if isinstance(p, dict) else None
    except Exception:
        return None


def pinned_subject(strong_only=False):
    """The subject pinned on the current thread, as a reader sees it, or
    ''. ``strong_only`` ignores a note left by an offer or a draft."""
    p = current() or {}
    s = ' '.join(str(p.get('subject') or '').split()).strip(' .,;:')
    if not s:
        return ''
    if strong_only and str(p.get('source') or '') not in STRONG_SOURCES:
        return ''
    return s


def pinned_source():
    return str((current() or {}).get('source') or '')


def is_dropped(name):
    """True when the reader pushed this name out of the current thread."""
    k = _norm(str(name or '').split(' - ')[0])
    if not k:
        return False
    for d in (current() or {}).get('dropped') or []:
        dk = _norm(d)
        if dk and (dk == k or (min(len(dk), len(k)) >= 5 and (dk in k or k in dk))):
            return True
    return False


def _set_current(p):
    try:
        from flask import g as _g
        _g._pm_thread_pin = dict(p) if isinstance(p, dict) else None
    except Exception:
        pass


def stash(host, uname, tid, idx=None):
    """Read the thread's pin onto the request (flask.g._pm_thread_pin).
    Returns the pin or None. Never raises."""
    try:
        if not uname or not tid:
            _set_current(None)
            return None
        if idx is None:
            idx = host.load_threads_index(uname)
        p = pin_of_thread(idx, tid)
        _set_current(p)
        return p
    except Exception as e:
        _faults.note('thread_subject.stash', e)
        _set_current(None)
        return None


def _fold(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or '').lower())


def scope_to_history(history, extra=()):
    """Keep only the parts of the stashed pin that the conversation the
    client sent actually carries. The dashboard sends the thread's own
    turns, which hold the statement, the order, the quote or the draft
    the pin came from; a history with no trace of the pinned subject (a
    cleared chat, a different conversation under the same id, a test
    fixture) gets no pin. ``extra`` is the turn in hand (and the
    correction a continuation came from), which the history does not
    hold yet. ``None`` history (a server-loaded thread) keeps the record
    as is. Returns the pin in force."""
    p = current()
    if not p or not isinstance(history, list):
        return p
    try:
        blob = _fold(' '.join(str(t.get('text') or t.get('content') or '') for t in history if isinstance(t, dict))
                     + ' ' + ' '.join(str(x or '') for x in (extra or ())))
    except Exception:
        blob = ''
    subj = str(p.get('subject') or '')
    subj_ok = bool(subj) and bool(_fold(subj)) and _fold(subj) in blob
    dropped_ok = [d for d in (p.get('dropped') or []) if _fold(d) and _fold(d) in blob]
    if not subj_ok and not dropped_ok:
        _set_current(None)
        return None
    q = dict(p)
    q['dropped'] = dropped_ok
    if not subj_ok:
        q['subject'], q['source'] = '', ''
    _set_current(q)
    return q


def _mutate(host, uname, tid, fn):
    """Load the index, apply ``fn(entry)``, save when it changed. Returns
    the entry's pin after the change, or None."""
    if not uname or not tid:
        return None
    try:
        idx = host.load_threads_index(uname)
        hit = None
        for t in idx.get('threads', []) or []:
            if isinstance(t, dict) and t.get('id') == tid:
                hit = t
                break
        if hit is None:
            # a thread the index does not list yet (an API conversation
            # created outside the rail): nothing to pin on
            return None
        before = dict(hit.get('pin') or {}) if isinstance(hit.get('pin'), dict) else {}
        before['dropped'] = list(before.get('dropped') or [])
        p = dict(before)
        p['dropped'] = list(before['dropped'])     # its own list: the change test below compares the two
        fn(p)
        p['dropped'] = [d for i, d in enumerate(p.get('dropped') or []) if d and d not in (p['dropped'][:i])][-12:]
        if p != before:
            hit['pin'] = p
            host.s3_put_json(host.threads_index_key(uname), idx)
        _set_current(p)
        return p
    except Exception as e:
        _faults.note('thread_subject.mutate', e)
        return None


def pin(host, uname, tid, subject, *, source='statement', dropped=()):
    """Pin ``subject`` on the thread. A statement or a correction
    overrides anything; a weaker source only fills an empty pin."""
    subject = ' '.join(str(subject or '').split()).strip(' .,;:')
    if not subject:
        return None

    def _fn(p):
        cur_src = str(p.get('source') or '') if p.get('subject') else ''
        if _RANK.get(source, 1) >= _RANK.get(cur_src, 0):
            p['subject'] = subject[:120]
            p['source'] = source
            p['set_at'] = _now()
        for d in dropped or ():
            k = _norm(d)
            if k and k not in p['dropped'] and k != _norm(subject):
                p['dropped'].append(k)
        # a name the reader dropped earlier that is now the subject is back
        p['dropped'] = [d for d in p['dropped'] if _norm(d) != _norm(subject)]
    return _mutate(host, uname, tid, _fn)


def drop(host, uname, tid, names):
    """Drop names from the thread for good. A pinned subject that is one
    of them is unpinned."""
    keys = [_norm(n) for n in (names or ()) if _norm(n)]
    if not keys:
        return None

    def _fn(p):
        for k in keys:
            if k not in p['dropped']:
                p['dropped'].append(k)
        if p.get('subject') and any(_norm(p['subject']) == k or (len(k) >= 5 and (k in _norm(p['subject']) or _norm(p['subject']) in k)) for k in keys):
            p['subject'] = ''
            p['source'] = ''
    return _mutate(host, uname, tid, _fn)


def note(host, uname, tid, subject, *, source='offer'):
    """The subject Prometheus itself just quoted or drafted becomes the
    thread subject when the reader has not set a stronger one. Never
    overrides a statement or a correction; never notes a dropped name
    or a placeholder."""
    subject = ' '.join(str(subject or '').split()).strip(' .,;:')
    if not subject or subject.lower() in ('that subject', 'this audience', 'that audience', 'this subject'):
        return None
    cur = current() or {}
    if cur.get('subject') and _RANK.get(str(cur.get('source') or ''), 0) > _RANK.get(source, 1):
        return cur
    if is_dropped(subject):
        return cur or None
    return pin(host, uname, tid, subject, source=source)


# ----------------------------------------------------------- the last ask
_SUBJECT_STATEMENT_SHORT_RX = re.compile(r"^\W*(?:the\s+)?subject\b", re.I)


def last_user_ask(history, *, skip_text=''):
    """The reader's most recent real ask in the thread: not a correction,
    not a bare yes / no / number, not a chip they tapped on the previous
    agent turn, not a subject statement, not a check that we are there.
    '' when none. This is what a correction continues with."""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    skip = ' '.join(str(skip_text or '').split()).lower()
    try:
        from . import subject_rules as _sr
        from . import guards as _gd
    except Exception:
        _sr = _gd = None
    for i in range(len(turns) - 1, -1, -1):
        t = turns[i]
        if str(t.get('role') or '') != 'user':
            continue
        txt = ' '.join(str(t.get('text') or t.get('content') or '').split())
        if not txt or len(txt.split()) < 2:
            continue
        if skip and txt.lower() == skip:
            continue
        try:
            if _sr is not None and _sr.conversational_turn(txt):
                continue
            if _gd is not None and _gd.bare_reply_kind(txt):
                continue
        except Exception:
            pass
        if statement_subject(txt):
            continue
        # a chip the reader tapped on the agent turn right before it
        prev_opts = []
        for p in reversed(turns[:i]):
            if str(p.get('role') or '') == 'user':
                break
            meta = p.get('meta') if isinstance(p.get('meta'), dict) else {}
            for o in (meta.get('options') or []):
                lab = str((o.get('send') or o.get('label')) if isinstance(o, dict) else o or '').strip().lower()
                if lab:
                    prev_opts.append(lab)
            break
        if txt.lower().strip(' .!') in prev_opts:
            continue
        if re.match(r"^\W*(?:are you|you there|hello|hi|hey)\b", txt, re.I) and len(txt.split()) <= 4:
            continue
        return txt
    return ''
