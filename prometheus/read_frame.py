""""Same thing but for X" carries the previous ask's frame (Nov 23 2026
ask audit, fix 7; Jenna 2026-11-23: "platform scope, window, metric and
audience carry to the new subject unless the new words change them").

Armine's thread: a pasted ask sized one title's US audience on
YouTube's Free with ads shelf; "let's now do the same thing but for
Moonfall in the window of (...)" and the same for a second title carried
the shelf because the landed reads were still in the recent history;
the third, "let's now do the same thing but for Dogma in the window of
(11/1/26-11/9/26)", came after two held sends and two re-sends, the
first ask had slid out of the turns the read sees, and the read ran
Dogma on a service of the model's own choosing (MovieSphere+). The
same title read on YouTube in a fresh thread gave a different figure.

The frame of a read is what its first full question said: the platform
(where), the window (when), the metrics asked for and the audience. A
turn that says "same thing but for X" is that question again with X as
its subject; the frame rides in the words the read core is handed, never
left to the history or the model.

* `same_for_other(text)`: {'subject', 'window'} when the words are the
  same-again shape with a new subject, else None.
* `root_question(history)`: the first full question of the chain in the
  thread (walking back past every same-again turn), with the subject it
  was read on and the platform it named.
* `carry(text, history, new_subject, new_window=None)`: the root
  question rewritten for the new subject (and the new window when the
  words give one), with one plain closing line that names the frame.
  '' when the thread holds no root to carry.

Pure text and the thread; no model, no S3, no Flask.
Regression: scripts/test_pm_audit_1123_same_for_other.py.
"""
from __future__ import annotations

import re

__all__ = ['same_for_other', 'root_question', 'carry', 'frame_of', 'needs_carry', 'last_read_subject', 'SAME_FOR_OTHER_RX',
           'bare_name', 'follows_same_again']

SAME_FOR_OTHER_RX = re.compile(
    r"^\W*(?:(?:please|pls|now|then|next|and|also|ok(?:ay)?|so|let'?s|lets|can you|could you|would you|can we|could we)[,\s]+)*"
    r"(?:(?:now|then|also)\s+)?(?:(?:do|run|pull|try|repeat|redo|give\s+me|show\s+me|get\s+me|see|have)\s+)?"
    r"(?:(?:the|that|this|exactly\s+the|exact)\s+)?same\s+"
    r"(?:(?:thing|read|analysis|report|pull|numbers|figures|data|breakdown|exercise|view|look|cut|ask|question|table)\s+)?"
    r"(?:again\s+)?(?:but\s+)?(?:for|on|with)\s+(?P<subject>.+?)"
    r"(?:\s*,?\s*(?:in|for|over|during|across|within)\s+(?:the\s+)?(?:window|period|range|dates?)\s+(?:of\s+)?(?P<win>[^.?!]+?)"
    r"|\s+(?P<win2>\(?\s*\d{1,2}/\d{1,2}/\d{2,4}\s*(?:-|\u2013|\u2014|to|through)\s*\d{1,2}/\d{1,2}/\d{2,4}\s*\)?)"
    r"|\s+in\s+(?P<win3>(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4}|q[1-4]\s+\d{4}|20\d{2}))?"
    r"\s*[.!?]*\s*$", re.I)
_SUBJECT_TAIL_RX = re.compile(r"\s*,?\s*(?:please|thanks|thank you|instead|too|as well)\s*$", re.I)
_ACK_LEAD_RX = re.compile(r"^\W*(?:(?:ok(?:ay)?|yes|yep|sure|great|thanks|thank you|cool|got it|perfect|so|and|also|hmm|well|alright|right|actually|"
                          r"how about this|try this|here'?s (?:one|another|the thing)|one more|another one|quick one|also this)\b[\s,.:;!-]*)+", re.I)
_SHORT_RX = re.compile(r"^\W*(?:(?:re-?run|rerun|run|do|pull|redo|repeat)\b|same\b|again\b|yes\b|ok\b|approve\b|email\b|send\b|download\b)", re.I)
_METRIC_OR_WHERE_RX = re.compile(r"\b(?:viewers?|views|plays|watched|streamed|hours|minutes|subscribers|signups|buyers|shoppers|reach|unique|"
                                 r"audience|engagement|how many|size|share|penetration|index|on\s+[A-Z])", re.I)


def _squash(s):
    return ' '.join(str(s or '').split())


def same_for_other(text):
    """{'subject': 'Dogma', 'window': {'start','end','label'} or None} for
    "let's now do the same thing but for Dogma in the window of
    (11/1/26-11/9/26)"; None for any other words."""
    t = _squash(text)
    if not t or len(t) > 240:
        return None
    m = SAME_FOR_OTHER_RX.match(t)
    if not m:
        return None
    subj = _SUBJECT_TAIL_RX.sub('', (m.group('subject') or '').strip(' ,;:"\'')).strip()
    subj = re.sub(r"^(?:the|a|an)\s+", '', subj, flags=re.I).strip()
    if not subj or len(subj.split()) > 12:
        return None
    win_text = m.group('win') or m.group('win2') or m.group('win3') or ''
    win = _window_of(win_text) if win_text else None
    if not win and m.end('subject') < len(t):
        win = _window_of(t[m.end('subject'):])
    # a window written inside the subject ("Chicago PD for the year 2020", "Nike in Q4 2025") comes off it; a
    # "subject" that is only a window is a re-run of the same read, not a new subject
    inner = _window_in(subj)
    if inner:
        if inner['start_pos'] <= 0:
            return None
        head = subj[:inner['start_pos']]
        head = re.sub(r"\s*(?:,|for|in|over|during|across|within|on)?\s*(?:the\s+)?(?:(?:calendar|full|whole|entire)\s+)?(?:year|month|quarter|window|period|dates?)?\s*(?:of)?\s*$", '', head, flags=re.I)
        subj = head.strip(' ,;:')
        win = win or inner['window']
    # a subject that still carries a window in parentheses sheds it
    subj = re.sub(r"\s*\([^()]*\d[^()]*\)\s*$", '', subj).strip(' ,;:')
    if not subj or _window_in(subj) and _window_in(subj)['start_pos'] <= 0:
        return None
    if _WINDOW_ONLY_RX.fullmatch(subj):
        return None             # "the same for 2023": the same read on another window, the re-run's
    try:
        from . import platform_scope as _ps
        if _ps.canon(subj):
            return None         # "the same but on Hulu": a change of where, on the same subject; not a new subject
    except Exception:
        pass
    return {'subject': subj, 'window': win}


_BARE_LEAD_RX = re.compile(r"^\W*(?:(?:and|also|then|next|plus|or|now|ok(?:ay)?|what about|how about|and what about|and how about|same for|the same for|"
                           r"and for|also for|for|and do|and also|do|and the same for|likewise|ditto)\s+)*", re.I)
_BARE_TAIL_RX = re.compile(r"\s*,?\s*(?:too|as well|also|please|pls|thanks|thank you|next|then|after that)\s*$", re.I)
_BARE_STOP = frozenset(('this', 'that', 'these', 'those', 'it', 'them', 'they', 'same', 'one', 'ones', 'other', 'others', 'both', 'all', 'each',
                        'examples', 'example', 'summary', 'powerpoint', 'deck', 'pdf', 'csv', 'file', 'chart', 'table', 'breakdown', 'numbers',
                        'viewers', 'viewer', 'views', 'audience', 'fans', 'readers', 'subscribers', 'buyers', 'shoppers', 'users', 'people',
                        'women', 'men', 'female', 'male', 'females', 'males', 'adults', 'kids', 'teens', 'parents', 'ages', 'age', 'gen', 'millennials',
                        'boomers', 'under', 'over', 'older', 'younger', 'avid', 'casual', 'total', 'universe', 'cut', 'segment', 'cohort',
                        'week', 'month', 'year', 'quarter', 'window', 'period', 'last', 'next', 'past', 'yesterday', 'today', 'tomorrow',
                        'why', 'how', 'what', 'where', 'when', 'who', 'which', 'yes', 'no', 'ok', 'okay', 'sure', 'approve', 'cancel',
                        'more', 'less', 'again', 'rerun', 'redo', 'send', 'email', 'download', 'show', 'give', 'pull', 'run'))


def bare_name(text):
    """'Lore Olympus' for "And Lore Olympus?", "what about Lore Olympus",
    "and for Lore Olympus too": a turn that is only a name, one to six
    words with a capital letter somewhere, no verb, metric, segment or
    window word, not a platform. '' for every other turn."""
    t = _squash(text)
    if not t or len(t) > 80:
        return ''
    body = _BARE_LEAD_RX.sub('', t, count=1)
    body = _BARE_TAIL_RX.sub('', body.rstrip(' ?!.,;:')).strip(' ?!.,;:"\'')
    body = re.sub(r"^(?:the|a|an)\s+(?=[A-Z0-9])", '', body)
    if not body:
        return ''
    words = body.split()
    if not 1 <= len(words) <= 6:
        return ''
    if not re.search(r"[A-Z]", body):
        return ''
    low = [re.sub(r"[^a-z0-9+&'-]", '', w.lower()) for w in words]
    if any(w in _BARE_STOP for w in low):
        return ''
    if re.search(r"\d{1,2}\s*[-/]\s*\d{1,2}|\b(?:19|20)\d{2}\b|\$|%", body):
        return ''
    if _WINDOW_ONLY_RX.fullmatch(body):
        return ''
    try:
        from . import platform_scope as _ps
        if _ps.canon(body):
            return ''           # "and Hulu?": a change of where, on the same subject
    except Exception:
        pass
    return body


def follows_same_again(history):
    """True when the chain the thread is on is a same-again chain: walking
    back over the reader's turns, past any bare-name turns, the latest
    one is a "same thing but for X" turn. So "And Lore Olympus?" right
    after "Can we do this same analysis for unOrdinary?" is the same
    read again, for Lore Olympus."""
    for t in reversed(_turns(history)):
        if str(t.get('role') or '') != 'user':
            continue
        q = _text(t)
        if same_for_other(q):
            return True
        if bare_name(q):
            continue
        return False
    return False


_WINDOW_ONLY_RX = re.compile(r"(?:q[1-4]\s*(?:of\s+)?)?(?:19|20)\d{2}(?:\s*(?:-|to|\u2013)\s*(?:19|20)\d{2})?|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(?:19|20)\d{2}|"
                             r"(?:last|past|trailing|the last|the past)\s+\d+\s+(?:days?|weeks?|months?|years?)|(?:this|last|next)\s+(?:year|month|quarter|week)", re.I)


def _window_in(s):
    """A window phrase inside the words, with where it starts, or None."""
    w = _window_of(s)
    if w and w.get('span'):
        return {'window': w, 'start_pos': int(w['span'][0])}
    try:
        from . import continuation as _cont
        rw = _cont.rerun_window(s)
        yw = _cont.year_window(s)
    except Exception:
        rw, yw = None, None
    for cand in (rw, yw):
        if cand:
            m = re.search(r"\b(?:q[1-4]\s*(?:of\s+)?)?(?:19|20)\d{2}\b|\b(?:last|past|trailing)\s+\d+\s+(?:days?|weeks?|months?)\b|\d{1,2}/\d{1,2}", s, re.I)
            return {'window': {'start': cand[0], 'end': cand[1], 'label': _label(cand[0], cand[1]), 'phrase': (m.group(0) if m else ''), 'span': None},
                    'start_pos': (m.start() if m else 0)}
    return None


def _label(a, b):
    try:
        from . import inline_dates as _idates
        return _idates.window_label(a, b) or f"{a} to {b}"
    except Exception:
        return f"{a} to {b}"


def _window_of(phrase):
    try:
        from . import inline_dates as _idates
        w = _idates.parse_inline_window(str(phrase or ''))
        if isinstance(w, dict) and w.get('start') and w.get('end'):
            lab = ''
            try:
                lab = _idates.window_label(str(w['start'])[:10], str(w['end'])[:10])
            except Exception:
                lab = ''
            return {'start': str(w['start'])[:10], 'end': str(w['end'])[:10], 'label': lab or f"{w['start']} to {w['end']}",
                    'phrase': str(w.get('phrase') or ''), 'span': w.get('span')}
    except Exception:
        pass
    return None


def _turns(history):
    return [t for t in (history or []) if isinstance(t, dict)]


def _text(t):
    return _squash(t.get('text') or t.get('content') or '')


def _is_full_question(t):
    """A question that states its own frame: long enough, not a same-again
    or a short continuation, and it names a metric or a place."""
    if not t or same_for_other(t):
        return False
    # a question that opens on an acknowledgment ("ok - so how about this, how many of ...") is read past it
    body = _ACK_LEAD_RX.sub('', t, count=1)
    if _SHORT_RX.match(body) or bare_name(t):
        return False
    if len(body) < 40:
        return False
    return bool(_METRIC_OR_WHERE_RX.search(body)) or len(body) >= 120


def root_question(history):
    """{'question', 'subject', 'reply', 'platform', 'window'} for the first
    full question of the chain the thread is on: walking back from the
    latest user turn, the most recent user turn that states its own
    frame, with the subject the read that followed it was banked under
    (its landed turn's meta.subject) and the platform its words name.
    None when the thread holds none."""
    turns = _turns(history)
    for i in range(len(turns) - 1, -1, -1):
        t = turns[i]
        if str(t.get('role') or '') != 'user':
            continue
        q = _text(t)
        if not _is_full_question(q):
            continue
        subject, reply = '', ''
        for j in range(i + 1, len(turns)):
            nxt = turns[j]
            if str(nxt.get('role') or '') == 'user':
                break
            meta = nxt.get('meta') if isinstance(nxt.get('meta'), dict) else {}
            body = _text(nxt)
            if _PROGRESS_RX.search(body[:200]) and not meta.get('read_job_id') and str(meta.get('kind') or '') != 'read':
                continue            # the promise or the calm line, not the read
            if meta.get('read_job_id') or str(meta.get('kind') or '') == 'read' or len(body) >= 160:
                subject = str(meta.get('subject') or '').strip()
                reply = body
                if subject or reply:
                    break
        q_body = _ACK_LEAD_RX.sub('', q, count=1).strip() or q
        return {'question': q_body, 'subject': subject, 'reply': reply, 'platform': _platform_of(q, reply), 'window': _window_of(q)}
    return None


_PROGRESS_RX = re.compile(r"^\s*(?:Included with your plan\.\s*)?On it\b|\bthe read will land right here\b|^\s*Working on it\b|^\s*Sent\b|^\s*Same read as earlier", re.I)


def last_read_subject(history):
    """The subject of the most recent landed read in the thread (its
    turn's meta.subject, else its 'On <subject>:' opener), or ''."""
    turns = _turns(history)
    for t in reversed(turns):
        if str(t.get('role') or '') == 'user':
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        body = _text(t)
        if _PROGRESS_RX.search(body[:200]) and not meta.get('read_job_id') and str(meta.get('kind') or '') != 'read':
            continue
        if meta.get('read_job_id') or str(meta.get('kind') or '') == 'read':
            subj = str(meta.get('subject') or '').strip()
            if not subj:
                m = re.match(r"^On\s+([^:\n]{2,120}?):", body)
                subj = m.group(1).strip() if m else ''
            return subj
    return ''


def _platform_of(question, reply=''):
    """The one platform the question names in any role, else the one the
    landed read opened on ("... on YouTube Movies & TV Free with ads")."""
    try:
        from . import platform_scope as _ps
        named = {k: v for k, v in (_ps.named(question) or {}).items() if v != 'example'}
        if len(named) == 1:
            return list(named)[0]
        if not named and reply:
            named_r = {k: v for k, v in (_ps.named(reply[:400]) or {}).items() if v != 'example'}
            if len(named_r) == 1:
                return list(named_r)[0]
    except Exception:
        pass
    return ''


def frame_of(history):
    """The frame the chain is on: {'platform', 'window', 'subject', 'question'} or None."""
    root = root_question(history)
    if not root:
        return None
    return {'platform': root.get('platform') or '', 'window': root.get('window'), 'subject': _root_subject(root),
            'question': root['question']}


def _root_subject(root):
    """The subject the root question was read on: the one title its list
    cue names, else the landed read's subject, else the binder's."""
    q = root.get('question') or ''
    try:
        from . import title_list_read as _tl
        one = _tl.listed_single(q)
        if one:
            return one['title']
    except Exception:
        pass
    subj = str(root.get('subject') or '').strip()
    if subj:
        subj = re.sub(r"\s*\((?:https?://)[^)]*\)", '', subj).strip()
        subj = subj.split(' - ', 1)[0].strip()
        if subj and subj.lower() in q.lower():
            return subj
    try:
        from . import subject_rules as _sr
        s = _sr.bind(q, None, '', None)
        if s and s.source == 'words' and s.name and s.name.lower() in q.lower():
            return s.name
    except Exception:
        pass
    return subj


def _swap_subject(q, old, new):
    """The root question with its subject swapped, the window in
    parentheses beside it kept for the window swap."""
    if not old:
        return q, False
    rx = re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(re.escape(w) for w in old.split()) + r"(?![A-Za-z0-9])", re.I)
    out, n = rx.subn(new, q)
    return out, n > 0


def _swap_window(q, old_win, new_win):
    if not new_win:
        return q
    if old_win and old_win.get('phrase') and old_win['phrase'] in q:
        return q.replace(old_win['phrase'], new_win['phrase'] or new_win['label'], 1)
    return q


def needs_carry(text, history):
    """True when the new words leave the frame to the thread: they name no
    platform while the chain's root does, or they carry no metric word. A
    turn that restates where and what ("same for chicago pd, minutes
    viewed in 2023 on amazon prime") stands on its own words."""
    t = _squash(text)
    frame = frame_of(history)
    if not frame:
        return False
    try:
        from . import platform_scope as _ps
        names_platform = bool({k: v for k, v in (_ps.named(t) or {}).items() if v != 'example'})
    except Exception:
        names_platform = False
    metric = bool(re.search(r"\b(?:viewers?|views|plays|watched|streamed|hours|minutes|subscribers|signups|buyers|shoppers|reach|unique|"
                            r"viewership|audience|engagement|how many|size|share|penetration|index)\b", t, re.I))
    if frame.get('platform') and not names_platform:
        return True
    return not metric


def carry(text, history, new_subject, new_window=None):
    """The question the read runs for "same thing but for <new_subject>":
    the chain's root question with its subject swapped for the new one
    (and its window swapped when the words give one), then one closing
    line that names the frame it keeps. '' when nothing is there to carry."""
    frame = frame_of(history)
    if not frame or not str(new_subject or '').strip():
        return ''
    new_subject = _squash(new_subject)
    q = frame['question']
    old = frame.get('subject') or ''
    if old and old.lower() == new_subject.lower():
        return ''
    q2, swapped = _swap_subject(q, old, new_subject)
    q2 = _swap_window(q2, frame.get('window'), new_window)
    plat = frame.get('platform') or ''
    win = new_window or frame.get('window')
    bits = [f"Same read as the one on {old}" if old else "Same read as before", f"now for {new_subject}"]
    keep = []
    if plat:
        keep.append(f"the same platform ({plat})")
    keep.append("the same metrics and definitions")
    if win and win.get('label'):
        keep.append(f"the window {win['label']}")
    line = ', '.join(bits) + ': ' + ', '.join(keep) + f". The subject of this read is {new_subject}" + (f" on {plat}" if plat else '') + "."
    if not swapped:
        # the root did not name its subject in a form the words carry; the question stands and the line binds the new one
        return q2.rstrip() + '\n\n' + line
    return q2.rstrip() + '\n\n' + line
