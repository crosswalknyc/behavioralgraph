"""Continuation before routing (I2, 2026-10-20).

A day of misroutes had one shape: a turn that continued the
conversation was routed as a fresh ask, so it lost its subject and
its deliverable and fell onto the open tab, a priced offer or a
calm line. Bria: "Give me unique US viewers per surface, not just
hours" after The Office read drew "a custom read on Unique, $500".
Casey: "re-reun in the time frame 9/16 - 9/30" after the Golden Axe
read drew a read with no subject and then "Working on it". Marie,
asked which audience, typed the audience and was told it read as a
task. Tania: "what's the 10 million sample you're mentioning" drew
"Do you want this on BET?". Mark's correction drafted a $300 build.

Every turn is classified against the previous agent turn FIRST:

  new_ask               nothing below applies
  chip_answer           a short reply to the question the agent asked
  correction            no / not that / why do you keep
  question_about_answer what does that mean, what is the X you said
  parameter_change      same subject, a different metric, breakdown,
                        window or cut ("unique viewers per surface",
                        "break that out by month", "for Q4 instead")
  rerun_window          re-run / rerun / re-pull in <window>
  acceptance            yes / approve / go ahead / run it

Anything but new_ask binds its subject and deliverable from the
previous turn and never fires the open-tab confirm.

Pure text plus the thread; no model, no S3, no Flask.
"""
from __future__ import annotations

import datetime as _dt
import re

from . import subject_rules as _sr

__all__ = ['classify', 'previous_deliverable', 'rerun_window', 'window_phrase',
           'rerun_confirm', 'parameter_change_hint', 'KINDS']

KINDS = ('new_ask', 'chip_answer', 'correction', 'question_about_answer',
         'parameter_change', 'rerun_window', 'acceptance')

_RERUN_RX = re.compile(
    r"^\W*(?:please\s+|can you\s+|could you\s+)?(?:re-?r(?:u|eu)n|rerun|re-?pull|re-?do|redo|run\s+(?:it|that|this|the\s+same)\s+again|"
    r"same\s+(?:read|thing|analysis|report|pull)\s+(?:but\s+)?(?:for|in|on|over))\b", re.I)
_MONTHS = ('january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
           'september', 'october', 'november', 'december')
_MON_RX = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_MD = r"(?P<{p}m>\d{{1,2}})\s*/\s*(?P<{p}d>\d{{1,2}})(?:\s*/\s*(?P<{p}y>\d{{2,4}}))?"
_WINDOW_MD_RX = re.compile(_MD.format(p='a') + r"\s*(?:-|to|through|thru|until|till|\u2013|\u2014)\s*" + _MD.format(p='b'), re.I)
_WINDOW_MON_RX = re.compile(
    r"(?P<am>" + _MON_RX + r")\s+(?P<ad>\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(?P<ay>\d{4}))?\s*(?:-|to|through|thru|until|till|\u2013|\u2014)\s*"
    r"(?:(?P<bm>" + _MON_RX + r")\s+)?(?P<bd>\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(?P<by>\d{4}))?", re.I)
_WINDOW_MONTH_ONLY_RX = re.compile(r"\b(?P<m>" + _MON_RX + r")\s+(?P<y>\d{4})\b", re.I)
_WINDOW_RELATIVE_RX = re.compile(r"\b(?:last|past|trailing)\s+(?P<n>\d+)\s+(?P<u>days?|weeks?|months?)\b", re.I)
_WINDOW_Q_RX = re.compile(r"\b(?P<q>q[1-4])\s*(?:of\s+)?(?P<y>\d{4}|last\s+year|this\s+year)?\b", re.I)

_ACCEPT_RX = re.compile(
    r"^\W*(?:accept\b.*|yes|yep|yeah|ok(?:ay)?|sure|approve[d]?|go ahead|do it|run it|proceed|confirm(?:ed)?|sounds good|please do|let'?s do it|go for it|"
    r"yes,?\s+(?:run it again|re-?run it|do it|please|go ahead)|run it again|re-?run it)\W*$", re.I)

_PARAM_WORDS_RX = re.compile(
    r"\b(?:unique\s+(?:us\s+)?(?:viewers|users|people|accounts)|per\s+(?:surface|device|platform|viewer|user|month|week|day)|"
    r"by\s+(?:surface|device|platform|month|week|day|daypart|genre|state|dma|age|gender|demographic)|"
    r"not\s+just\s+(?:hours|views|totals?|counts?)|instead\s+of\s+(?:hours|views|totals?)|"
    r"break\s+(?:that|this|it)\s+(?:out|down)|split\s+(?:that|this|it)|same\s+(?:thing|read|numbers?)\s+(?:but|for)|"
    r"for\s+q[1-4]\b|monthly\s+instead|weekly\s+instead|hours\s+per\s+viewer|as\s+a\s+(?:share|percent|rate)|"
    r"(?:just|only)\s+the\s+(?:avid|female|male|under-?\d+|\d+-\d+)\s+(?:cut|slice|cohort)|cut\s+(?:that|this|it)\s+by)\b", re.I)
_ABOUT_ANSWER_RX = re.compile(
    r"\b(?:is\s+that\s+(?:per|a|the|an)\b|does\s+that\s+(?:include|count|mean)|how\s+(?:is|was)\s+(?:that|this|it)\s+(?:calculated|computed|measured|derived)|"
    r"where\s+(?:does|did)\s+(?:that|this|the)\s+(?:number|figure)\s+come\s+from|what\s+(?:is|'s)\s+the\s+(?:difference|denominator|base)\b|"
    r"is\s+(?:that|this)\s+(?:of|out\s+of)\s+(?:total|all|the)\b)", re.I)

_READ_HEAD_RX = re.compile(r"^On\s+(?P<who>[^:\n(]{1,120}?)(?:\s*\([^)]*\))?\s*(?:-\s*[^:\n]{1,80})?:", re.I)
_PROMISE_RX = re.compile(r"^\s*On it\b|\bthe read will land right here\b|\bWorking on it\b", re.I)
_QUESTION_RX = re.compile(r"\?\s*$|\bwhich (?:subject|audience|profile|one|title)\b|\bdo you (?:want|mean)\b|\bname the\b", re.I)
# A sentence that opens like a question and runs five words or more is a
# new question even without its mark ("how much impact did Spiderwick
# have on Roku"), never a short reply to the question the agent asked.
_INTERROGATIVE_OPEN_RX = re.compile(
    r"^\W*(?:how|what|which|who|whom|whose|when|where|why|do|does|did|is|are|was|were|"
    r"can|could|should|would|will|has|have|had)\b", re.I)


_NEGATED_RX = re.compile(
    r"\b(?:not\s+(?:interested\s+in|about|on|asking\s+(?:about|for))|no\s+not|why\s+(?:(?:do|did|would)\s+)?(?:you\s+)?keep\s+(?:bringing\s+up|mentioning|saying|offering)|"
    r"stop\s+(?:bringing\s+up|mentioning|offering)|instead\s+of|rather\s+than|don'?t\s+(?:want|need|mean))\s+(?P<name>[A-Za-z0-9][A-Za-z0-9&'+.-]*(?:\s+[A-Za-z0-9&'+.-]+){0,3})", re.I)


def _negated_names(text):
    """Names the correction pushes away, normalized ('why do you keep
    bringing up Salish matter' -> {'salish matter'}). Trailing filler
    words ('specifically', 'again') drop."""
    out = set()
    for m in _NEGATED_RX.finditer(str(text or '')):
        words = m.group('name').split()
        while words and words[-1].lower().strip('.,;:!?') in ('specifically', 'again', 'anymore', 'please', 'here', 'now', 'though'):
            words.pop()
        for n in range(len(words), 0, -1):
            k = _sr._norm(' '.join(words[:n]).strip('.,;:!?'))
            if k:
                out.add(k)
    return out


def _turns(history):
    return [t for t in (history or []) if isinstance(t, dict)]


def _text(t):
    return ' '.join(str(t.get('text') or t.get('content') or '').split())


def _last_agent(history):
    for t in reversed(_turns(history)):
        if str(t.get('role') or '') != 'user':
            return t
    return None


def previous_deliverable(history, page=''):
    """The last thing Prometheus delivered or promised in this thread:
    {'kind': read|build|journey|deck|cut|offer|question|promise|none,
     'subject', 'question', 'job_id', 'text', 'viewership': bool}.
    The question is the user turn that drew it."""
    out = {'kind': 'none', 'subject': '', 'question': '', 'job_id': '', 'text': '', 'viewership': False}
    turns = _turns(history)
    idx = None
    for i in range(len(turns) - 1, -1, -1):
        t = turns[i]
        if str(t.get('role') or '') == 'user':
            continue
        txt = _text(t)
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        low = txt.lower()
        if low.startswith('do you want this on') or low.startswith('which subject') or _QUESTION_RX.search(txt[:160]) and len(txt) < 240 and not _READ_HEAD_RX.match(txt):
            if out['kind'] == 'none':
                out.update(kind='question', text=txt)
                idx = i
            continue
        if meta.get('read_job_id') or _READ_HEAD_RX.match(txt):
            m = _READ_HEAD_RX.match(txt)
            who = (m.group('who').strip() if m else '') or str(meta.get('subject') or '')
            out.update(kind='read', subject=who, job_id=str(meta.get('read_job_id') or ''), text=txt)
            idx = i
            break
        if meta.get('deck_job_id'):
            out.update(kind='deck', subject=str(meta.get('subject') or ''), job_id=str(meta.get('deck_job_id') or ''), text=txt)
            idx = i
            break
        if meta.get('jiq_job_id'):
            out.update(kind='journey', subject=str(meta.get('subject') or ''), job_id=str(meta.get('jiq_job_id') or ''), text=txt)
            idx = i
            break
        if meta.get('run_id') or meta.get('job'):
            out.update(kind='build', subject=str(meta.get('subject') or ''), job_id=str(meta.get('run_id') or ''), text=txt)
            idx = i
            break
        if meta.get('offer') or meta.get('panel_offer') or re.search(r"\$\s?\d|\bcredits?\b|\bIt runs\b", txt):
            out.update(kind='offer', subject=str(meta.get('subject') or ''), text=txt)
            idx = i
            break
        if _PROMISE_RX.search(txt):
            m = re.match(r"^On it:\s*reading\s+(?P<what>.+?)\s+now\.?", txt, re.I)
            out.update(kind='promise', subject=(m.group('what') if m else ''), text=txt)
            idx = i
            break
        if len(txt) >= 60:
            out.update(kind='read', subject=str(meta.get('subject') or ''), text=txt)
            idx = i
            break
    if idx is not None:
        for j in range(idx - 1, -1, -1):
            if str(turns[j].get('role') or '') == 'user':
                out['question'] = _text(turns[j])
                break
    if not out['subject'] and out['question']:
        s = _sr.bind(out['question'], None, page)
        if s and s.source == 'words':
            out['subject'] = s.display
    out['subject'] = _sr.clean_display(out['subject']) or out['subject']
    q = (out['question'] or '').lower()
    out['viewership'] = bool(re.search(r"\bhow many\b.*\b(?:watch|view|stream|read|listen|play)", q)) or ('viewership' in q)
    return out


def _year_now():
    return _dt.date.today().year


def _mon_num(s):
    s = str(s or '').lower().rstrip('.')[:3]
    for i, m in enumerate(_MONTHS, 1):
        if m[:3] == s:
            return i
    return 0


def rerun_window(text, today=None):
    """The window a re-run names: (start, end) ISO dates, or None.
    Bare month/day dates resolve to the current year (9/16 to 9/30 is
    September 16 to September 30, 2026)."""
    t = str(text or '')
    yr = (today or _dt.date.today()).year
    m = _WINDOW_MD_RX.search(t)
    if m:
        def _y(v):
            if not v:
                return yr
            v = int(v)
            return v + 2000 if v < 100 else v
        try:
            a = _dt.date(_y(m.group('ay')), int(m.group('am')), int(m.group('ad')))
            b = _dt.date(_y(m.group('by')), int(m.group('bm')), int(m.group('bd')))
            return a.isoformat(), b.isoformat()
        except ValueError:
            return None
    m = _WINDOW_MON_RX.search(t)
    if m:
        am = _mon_num(m.group('am'))
        bm = _mon_num(m.group('bm')) if m.group('bm') else am
        ay = int(m.group('ay') or m.group('by') or yr)
        by = int(m.group('by') or ay)
        try:
            return _dt.date(ay, am, int(m.group('ad'))).isoformat(), _dt.date(by, bm, int(m.group('bd'))).isoformat()
        except ValueError:
            return None
    m = _WINDOW_MONTH_ONLY_RX.search(t)
    if m:
        mn, y = _mon_num(m.group('m')), int(m.group('y'))
        if mn:
            nxt = _dt.date(y + (mn == 12), (mn % 12) + 1, 1)
            return _dt.date(y, mn, 1).isoformat(), (nxt - _dt.timedelta(days=1)).isoformat()
    m = _WINDOW_Q_RX.search(t)
    if m and m.group('y'):
        q = int(m.group('q')[1])
        y = m.group('y').lower()
        y = yr - 1 if 'last' in y else (yr if 'this' in y else int(y))
        a = _dt.date(y, 3 * (q - 1) + 1, 1)
        b = _dt.date(y + (q == 4), (3 * q) % 12 + 1, 1) - _dt.timedelta(days=1)
        return a.isoformat(), b.isoformat()
    m = _WINDOW_RELATIVE_RX.search(t)
    if m:
        n, u = int(m.group('n')), m.group('u').lower()
        end = today or _dt.date.today()
        days = n * (1 if u.startswith('day') else 7 if u.startswith('week') else 30)
        return (end - _dt.timedelta(days=days)).isoformat(), end.isoformat()
    return None


def window_phrase(window):
    """('2026-09-16', '2026-09-30') -> 'September 16 to September 30, 2026'."""
    if not window:
        return ''
    try:
        a = _dt.date.fromisoformat(window[0])
        b = _dt.date.fromisoformat(window[1])
    except Exception:
        return ''
    if a.year == b.year:
        if a.month == b.month and a.day == 1 and (b + _dt.timedelta(days=1)).day == 1:
            return f"{_MONTHS[a.month - 1].title()} {a.year}"
        return f"{_MONTHS[a.month - 1].title()} {a.day} to {_MONTHS[b.month - 1].title()} {b.day}, {a.year}"
    return f"{_MONTHS[a.month - 1].title()} {a.day}, {a.year} to {_MONTHS[b.month - 1].title()} {b.day}, {b.year}"


def _is_chip_answer(text, prev):
    """A short reply to the question the agent just asked."""
    if not prev or str(prev.get('role') or '') == 'user':
        return False
    ptxt = _text(prev)
    meta = prev.get('meta') if isinstance(prev.get('meta'), dict) else {}
    asked = bool(meta.get('options')) or bool(_QUESTION_RX.search(ptxt[-200:])) or 'name the' in ptxt.lower()
    if not asked:
        return False
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 160 or '?' in t:
        return False
    if _sr.explicit_build_subject(t) or _RERUN_RX.match(t):
        return False
    if _INTERROGATIVE_OPEN_RX.match(t) and len(t.split()) >= 5:
        return False    # a question typed without its mark is a new ask
    return True


def classify(text, history, reading=None, page=''):
    """What this turn is, against the previous agent turn.
    Returns {'kind', 'deliverable', 'subject', 'window', 'params',
             'confidence'}; kind 'new_ask' when nothing binds."""
    t = ' '.join(str(text or '').split())
    out = {'kind': 'new_ask', 'deliverable': None, 'subject': '', 'window': None, 'params': {}, 'confidence': 0.0}
    if not t:
        return out
    prev = _last_agent(history)
    deliv = previous_deliverable(history, page) if prev else None
    out['deliverable'] = deliv
    low = t.lower()
    # acceptance
    if _ACCEPT_RX.match(t):
        out.update(kind='acceptance', confidence=0.9, subject=(deliv or {}).get('subject', ''))
        return out
    # re-run in a window
    if _RERUN_RX.match(t) or (re.search(r"\b(?:re-?r(?:u|eu)n|rerun|re-?pull)\b", low) and rerun_window(t)):
        out.update(kind='rerun_window', window=rerun_window(t), confidence=0.9 if rerun_window(t) else 0.7,
                   subject=(deliv or {}).get('subject', ''))
        return out
    # a question that quotes a figure Prometheus stated earlier is a
    # question about that answer, whatever else the words say (2026-10-22,
    # Alexia: "where does the 12,004,203 ... number come from? I only see
    # 6.6M ..." read as a status check, a brand ask and a box office ask)
    try:
        from . import figure_provenance as _fp
        _q = _fp.quoted_from_thread(t, history)
    except Exception:
        _q = None
    if _q:
        out.update(kind='question_about_answer', confidence=0.9, subject=(deliv or {}).get('subject', ''),
                   params={'figure': _q['figure'], 'figure_turn': _q['turn']})
        return out
    # a question about the previous answer
    if _sr.definition_turn(t, reading, page) or _ABOUT_ANSWER_RX.search(t):
        if deliv and deliv.get('kind') in ('read', 'offer', 'build', 'journey', 'deck'):
            out.update(kind='question_about_answer', confidence=0.85, subject=deliv.get('subject', ''))
            return out
        if _sr.definition_turn(t, reading, page):
            out.update(kind='question_about_answer', confidence=0.7)
            return out
    # a correction / why-do-you-keep / clarification of the previous turn
    ct = _sr.conversational_turn(t)
    if ct:
        # the subject a correction continues: the previous deliverable's,
        # else the thread's most recent subject that the correction does
        # not push away ("why do you keep bringing up Salish matter" with
        # Salish Matter open: the thread's DraftKings users, never the page)
        subj = str((deliv or {}).get('subject') or '').strip()
        negated = _negated_names(t)
        if not subj or _sr._norm(subj) in negated or (page and _sr._norm(subj) == _sr._norm(page)):
            subj = ''
            for cand in _sr.thread_subjects(history, page):
                if _sr._norm(cand) not in negated:
                    subj = cand
                    break
        out.update(kind='correction', confidence=0.85, subject=subj, params={'turn': ct, 'negated': sorted(negated)})
        return out
    # a chip answer: short reply to a question just asked
    if _is_chip_answer(t, prev):
        s = _sr.bind(t, None, page, reading)
        out.update(kind='chip_answer', confidence=0.8, subject=(s.display if s else t),
                   params={'qualifiers': dict(s.qualifiers) if s else {}, 'question': (deliv or {}).get('question', '')})
        return out
    # a parameter change on the previous subject: no new subject in the words
    if deliv and deliv.get('kind') in ('read', 'offer') and _PARAM_WORDS_RX.search(t):
        s = _sr.bind(t, None, page, reading)
        if not s or s.source != 'words' or s.kind in ('metric', 'none') or _sr.is_metric_phrase(s.display):
            out.update(kind='parameter_change', confidence=0.8, subject=deliv.get('subject', ''),
                       params=parameter_change_hint(t), window=rerun_window(t))
            return out
    return out


def parameter_change_hint(text):
    """What changed: metric, breakdown, window, cut (words for the read)."""
    t = str(text or '')
    hint = {}
    m = re.search(r"\b(unique\s+(?:us\s+)?(?:viewers|users|people|accounts)|hours\s+per\s+viewer|viewers|hours|views|minutes|sessions|accounts|subscribers)\b", t, re.I)
    if m:
        hint['metric'] = ' '.join(m.group(1).split()).lower()
    m = re.search(r"\b(?:per|by|across)\s+(surface|device|platform|month|week|day|daypart|genre|state|dma|age|gender|demographic)s?\b", t, re.I)
    if m:
        hint['breakdown'] = m.group(1).lower()
    w = rerun_window(t)
    if w:
        hint['window'] = w
    m = re.search(r"\b(avid|female|male|under-?\d+|\d+-\d+)\s+(?:cut|slice|cohort)\b", t, re.I)
    if m:
        hint['cut'] = m.group(1).lower()
    return hint


def rerun_confirm(deliv, window, price_label, *, what=''):
    """The confirm card for a re-run: names the deliverable, the window
    and the standing price. Never a silent 'working on it'."""
    subj = str((deliv or {}).get('subject') or what or 'the last read').strip()
    kind = str((deliv or {}).get('kind') or 'read')
    noun = {'read': 'read', 'build': 'profile', 'journey': 'journey', 'deck': 'deck', 'cut': 'cut'}.get(kind, 'read')
    wp = window_phrase(window)
    if wp:
        # the one offer shape (prometheus/priced_read.py, Jenna 2026-10-21: Approve / Cancel)
        from .priced_read import compose_offer, lead_for
        reply, chips, _body = compose_offer(lead_for('rerun', subj, noun=noun, window=wp), price_label, '', {})
    else:
        reply = f"I can run the {subj} {noun} again. Tell me the window (two dates, a month, or a quarter) and I will quote it; pulling this data will cost {price_label}."
        chips = ['Something else']
    q = str((deliv or {}).get('question') or '').strip()
    merged = (f"{q} Window: {wp}." if (q and wp) else q)
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': chips,
            'offer_deck': False, 'deck_angle': None,
            'rerun_confirm': {'subject': subj, 'kind': kind, 'window': list(window) if window else None,
                              'question': merged, 'price_label': price_label}}
