"""'Can we discuss the exact pull first?' opens a discussion (Jenna
2026-10-26, Emmet Riley at Kartel).

Emmet asked it twice. The first time it drew a which-one question that
offered the profile open on his screen; the second time it drew the
same $100 quote he had been trying to talk about. Neither was an
answer. The words ask for the scope of the pull in plain language
before anything is approved, and that is what comes back: who counts in
the audience, the window, what the file carries, the cuts on top of
it, the cost and that nothing is charged until the brief is approved,
then what to adjust and the one open question. No quote, no draft, no
clarify about the page, no charge.

The subject is the thread's: the subject the reader set
(thread_subject), the brief already drafted in the thread (its window,
size and cost are reused rather than drawn again), the subject the
thread has been about. With none of those the lane asks which audience
the pull should be on, in one line.

  wants_discussion(text)        the ask shapes
  draft_in_thread(history)      the brief the thread already holds
  answer(...)                   the reply payload, or None to pass

Pure text plus a read of the seat's price table through the host.
"""
from __future__ import annotations

import datetime as _dt
import re

from . import faults as _faults

__all__ = ['wants_discussion', 'draft_in_thread', 'answer', 'compose', 'subject_for']

_PULL_NOUN = (r"(?:pull|read|build|profile\s+pull|profile\s+build|brief|scope|request|order|ask|"
              r"data\s*set|dataset|deliverable|audience\s+definition|definition|universe|specifics|details)")
_TALK = (r"(?:discuss|talk\s+(?:through|about|over)|walk\s+(?:me|us)\s+through|go\s+over|go\s+through|review|"
         r"scope\s+(?:out)?|nail\s+down|clarify|define|agree\s+on|settle|align\s+on|think\s+through|work\s+through|"
         r"understand|confirm|check)")
_WANTS_RXS = (
    # "can we discuss the exact pull first", "let's talk through the pull before we run it"
    re.compile(r"\b(?:can|could|should|shall|may)\s+we\s+(?:first\s+)?" + _TALK + r"\b[^.?!]{0,40}?\b(?:the\s+|this\s+|that\s+)?(?:exact\s+|full\s+|whole\s+|proposed\s+)?" + _PULL_NOUN + r"\b", re.I),
    re.compile(r"^\W*(?:let'?s|lets|i'?d\s+like\s+to|i\s+want\s+to|i\s+would\s+like\s+to|we\s+(?:should|need\s+to|want\s+to))\s+(?:first\s+)?" + _TALK + r"\b[^.?!]{0,40}?\b(?:the\s+|this\s+|that\s+|it\s+)?(?:exact\s+|full\s+|whole\s+|proposed\s+)?" + _PULL_NOUN + r"\b", re.I),
    re.compile(r"\b(?:walk\s+(?:me|us)\s+through|talk\s+(?:me|us)\s+through|go\s+over|discuss|clarify|explain|describe|lay\s+out|spell\s+out|scope\s+out)\s+(?:the\s+|this\s+|that\s+)?(?:exact\s+|full\s+|whole\s+|proposed\s+)?" + _PULL_NOUN + r"\b", re.I),
    # "let's talk through it first", "can we go over it first", "discuss first"
    re.compile(r"^\W*(?:let'?s|lets|can\s+we|could\s+we|should\s+we|shall\s+we|i'?d\s+like\s+to|i\s+want\s+to)\s+(?:just\s+)?" + _TALK + r"\s+(?:this|that|it|things|everything)?\s*(?:through|over|out)?\s*(?:first|before\s+(?:we|you|i)\s+\w+)\b", re.I),
    # "before we run, what exactly will this pull", "before we approve, what would it include"
    re.compile(r"\bbefore\s+(?:we|you|i)\s+(?:run|build|pull|approve|start|commit|kick\s+off|go|spend|pay)\b[^.?!]{0,80}\b(?:what|which|how|exactly|include|cover|carry|look\s+like)\b", re.I),
    re.compile(r"\b(?:what|which|how)\b[^.?!]{0,40}\bbefore\s+(?:we|you|i)\s+(?:run|build|pull|approve|start|commit|kick\s+off|go|spend|pay)\b", re.I),
    # "what would this include", "what exactly does the pull cover", "what will the brief carry"
    re.compile(r"\b(?:what|which)\s+(?:exactly\s+|all\s+)?(?:will|would|does|do|should|is|are|did)\s+(?:exactly\s+)?"
               r"(?:this|that|it|the\s+(?:exact\s+)?" + _PULL_NOUN + r"|this\s+(?:new\s+)?(?:profile|file)|your\s+" + _PULL_NOUN + r")\s+"
               r"(?:actually\s+|exactly\s+)?(?:include|cover|carry|contain|entail|consist\s+of|come\s+with|look\s+like|get\s+(?:me|us)|give\s+(?:me|us)|involve|be\s+based\s+on|measure)\b", re.I),
    # "what's in the pull", "what is included in the profile"
    re.compile(r"\bwhat(?:'s|\s+is)\s+(?:all\s+)?(?:in|included\s+in|covered\s+by|part\s+of)\s+(?:the|this|that|a)\s+(?:exact\s+)?(?:" + _PULL_NOUN + r"|profile|file)\b", re.I),
)
_NOT_RXS = (
    re.compile(r"^\W*(?:approve|run|build|go\s+ahead|do\s+it|yes|ok)\b", re.I),
    re.compile(r"\bpull\s+(?:a|the|an|me)\s+(?:profile|read|list|report)\b", re.I),
    re.compile(r"\b(?:how\s+much|what)\s+(?:does|will|would)\s+(?:it|this|that|the\s+\w+)\s+cost\b", re.I),
)

_DRAFT_HEAD_RX = re.compile(r"^\s*(?:New\s+(?P<subj>.+?)\s+profile,\s+ready\s+to\s+run|(?P<subj2>.+?)\s+refresh,\s+ready\s+to\s+run)\b", re.I | re.M)
_DRAFT_SUBJECT_RX = re.compile(r"^\s*Subject:\s*(?P<v>[^\n]+)$", re.I | re.M)
_DRAFT_WINDOW_RX = re.compile(r"^\s*Window:\s*(?P<v>[^\n]+)$", re.I | re.M)
_DRAFT_US_RX = re.compile(r"about\s+(?P<lo>[\d,]+)\s+to\s+(?P<hi>[\d,]+)\s+US\s+people", re.I)
_DRAFT_COST_RX = re.compile(r"^\s*Cost:\s*(?P<v>[$\u00a3\u20ac]\s?[\d,]+(?:\.\d+)?)", re.I | re.M)
_DRAFT_AVID_RX = re.compile(r"^\s*Avid\s+tier:", re.I | re.M)
_RESOLVED_RX = re.compile(r"^resolved\s+from\s+\S+\s*[-:]\s*(?P<name>[^(\n]+?)(?:\s*\(|,|$)", re.I)

# typed in a hurry too: "unversity", "univeristy", "colege"
_INSTITUTION_RX = re.compile(r"\b(?:un?i?ver?s?i?ty|univeristy|col+e?ge|school|institute|academy|polytechnic|conservatory|\buva\b|\bucla\b|\busc\b|\bnyu\b|\bmit\b)\b", re.I)
_VIEWERS_RX = re.compile(r"\b(?P<name>.+?)\s+(?P<verb>viewers|watchers|readers|listeners|players|subscribers)\b(?:\s+on\s+(?P<plat>[A-Za-z0-9+&' .-]{2,40}))?$", re.I)
_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
           'September', 'October', 'November', 'December')


def wants_discussion(text):
    """True when the words ask to talk through the scope of the pull
    before it runs, rather than ordering it or asking a data question."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 400:
        return False
    for rx in _NOT_RXS:
        if rx.search(t):
            return False
    return any(rx.search(t) for rx in _WANTS_RXS)


def draft_in_thread(history):
    """The brief the thread already carries (the card the dashboard wrote
    into the thread), newest first: {'subject', 'window', 'lo_us',
    'hi_us', 'cost', 'avid'}; None when the thread holds none."""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    for t in reversed(turns):
        if str(t.get('role') or '') == 'user':
            continue
        txt = str(t.get('text') or t.get('content') or '')
        m = _DRAFT_HEAD_RX.search(txt)
        if not m or 'ready to run' not in txt:
            continue
        out = {'subject': (m.group('subj') or m.group('subj2') or '').strip(), 'window': '', 'lo_us': 0, 'hi_us': 0,
               'cost': '', 'avid': bool(_DRAFT_AVID_RX.search(txt))}
        sm = _DRAFT_SUBJECT_RX.search(txt)
        if sm:
            v = sm.group('v').strip()
            rm = _RESOLVED_RX.match(v)
            if rm:
                v = rm.group('name').strip()
            if v and len(v) <= 120:
                out['subject'] = v
        wm = _DRAFT_WINDOW_RX.search(txt)
        if wm:
            out['window'] = wm.group('v').strip()
        um = _DRAFT_US_RX.search(txt)
        if um:
            try:
                out['lo_us'] = int(um.group('lo').replace(',', ''))
                out['hi_us'] = int(um.group('hi').replace(',', ''))
            except ValueError:
                pass
        cm = _DRAFT_COST_RX.search(txt)
        if cm:
            out['cost'] = cm.group('v').replace(' ', '')
        if out['subject']:
            return out
    return None


def _default_window_phrase():
    try:
        from migration.event_window import default_window as _dw
        a, b = _dw()
    except Exception:
        today = _dt.date.today()
        try:
            a = today.replace(year=today.year - 1)
        except ValueError:
            a = today.replace(year=today.year - 1, day=28)
        a, b = a.isoformat(), today.isoformat()
    try:
        from .continuation import window_phrase as _wp
        return _wp((str(a), str(b)))
    except Exception:
        return f"{a} to {b}"


def _symbol(host, uname):
    try:
        if host is not None and host.has('pricing_copy_for'):
            copy = str(host.pricing_copy_for(uname) or '')
            if '\u00a3' in copy and '$' not in copy:
                return '\u00a3'
            if '\u20ac' in copy and '$' not in copy:
                return '\u20ac'
    except Exception:
        pass
    return '$'


def _prices(host, uname):
    """(build_label, cut_label) in the seat's currency."""
    from . import rate_card as _rc
    build, cut = _rc.default('profile_iq_build'), _rc.default('profile_iq_derived_cut')
    try:
        if host is not None and host.has('price_table_for'):
            pt = host.price_table_for(uname) or {}
            if isinstance(pt, dict):
                build = float(pt.get('new_build') or build) or build
                cut = float(pt.get('addon_cut') or pt.get('derive_cut') or cut) or cut
    except Exception as e:
        _faults.note('pull_discussion.prices', e)
    sym = _symbol(host, uname)
    return _rc.money(build, sym), _rc.money(cut, sym)


def _is_pointer(s):
    return str(s or '').strip().lower() in ('this', 'that', 'it', 'the same', 'this one', 'that one')


def subject_for(history, ctx=None, page=''):
    """(subject, how) for the pull under discussion: the subject the
    reader set in the thread, the brief already drafted, the thread's
    own subject. The open page is never the answer here. ('', '') when
    the thread names nothing."""
    pin, strong = '', False
    try:
        from . import thread_subject as _ts
        pin = _ts.pinned_subject()
        strong = bool(_ts.pinned_subject(strong_only=True))
    except Exception:
        pin, strong = '', False
    if pin and strong:
        return pin, 'pinned'
    # the brief already drafted carries the resolved spelling ("University
    # of Virginia" for "UVA consumers for the unversity of virgina")
    d = draft_in_thread(history)
    if d and d.get('subject') and not _is_pointer(d['subject']):
        try:
            from . import thread_subject as _ts2
            if not _ts2.is_dropped(d['subject']):
                return d['subject'], 'draft'
        except Exception:
            return d['subject'], 'draft'
    if pin:
        return pin, 'pinned'
    try:
        from . import subject_rules as _sr
        from . import thread_subject as _ts
        for cand in _sr.thread_subjects(history, page):
            if cand and not _sr.same_entity(cand, page) and not _ts.is_dropped(cand) and not _sr.is_metric_phrase(cand):
                return cand, 'thread'
    except Exception:
        pass
    return '', ''


def universe_line(subject):
    """Who counts, in plain words, for this kind of subject."""
    s = str(subject or '').strip()
    if _INSTITUTION_RX.search(s):
        if re.match(r"^(?:university|college|school|institute|academy)\b", s, re.I):
            s = 'the ' + s
        return (f"people with observed digital activity around {s} over the window: athletics, admissions, "
                f"alumni, academic and medical center properties, and search and social activity on the institution. "
                f"Observed behavior, not stated affiliation.")
    m = _VIEWERS_RX.match(s)
    if m:
        name = m.group('name').strip()
        plat = (m.group('plat') or '').strip()
        verb = m.group('verb').lower()
        act = {'viewers': 'watched', 'watchers': 'watched', 'readers': 'read', 'listeners': 'listened to',
               'players': 'played', 'subscribers': 'subscribed to'}.get(verb, 'engaged with')
        return (f"people who {act} {name}" + (f" on {plat}" if plat else '') +
                " over the window, counted once each. Observed activity, not a stated preference.")
    return (f"people with observed digital activity around {s} over the window: its own sites and apps, search and "
            f"social activity on it, and purchases, sign-ups and streaming where they apply. Observed behavior, "
            f"not stated affiliation.")


def open_question(subject):
    """The one question still open on this pull, and the chips."""
    s = str(subject or '').strip()
    if _INSTITUTION_RX.search(s):
        team = ' (the Cavaliers)' if re.search(r"\bvirgini?a\b|\buva\b", s, re.I) and not re.search(r"\b(?:tech|commonwealth|west)\b", s, re.I) else ''
        q = (f"One thing to settle: the whole institution, or the athletics audience{team}? They are different "
             f"pools, and the second is younger and more sports-weighted.")
        return q, [f"Run a profile on {s}", f"Run a profile on {s} athletics", "Change the window"]
    return ("Anything to narrow it: a product line, a platform, a market, or a different window?",
            [f"Run a profile on {s}", "Change the window", "Add a cut"])


def compose(subject, *, window='', build_label='', cut_label='', draft=None):
    """The discussion reply: plain paragraphs, no quote, no card."""
    s = str(subject or '').strip()
    d = draft or {}
    win = (d.get('window') or window or _default_window_phrase()).strip()
    win_note = ('That is the window on the brief.' if d.get('window')
                else 'That is the default, the last 12 months; name another window if you want one.')
    cost = (d.get('cost') or build_label or '').strip()
    size = ''
    if d.get('lo_us') and d.get('hi_us'):
        size = f" The brief sizes it at about {int(d['lo_us']):,} to {int(d['hi_us']):,} US people."
    q, _chips = open_question(s)
    paras = [
        f"Here is the {s} pull as it stands, before anything runs.",
        f"Who counts: {universe_line(s)}",
        f"Window: {win}. {win_note}",
        ("What the file carries: who they are (age, gender, income, education, parental status) and where they are "
         "by DMA, then the brands, platforms, streaming services, retailers, talent, events and nonprofits this "
         "audience over-indexes on, each with its reach and its index against the US average."),
        ("Cuts on top of it: the Avid cut (the most engaged tier) comes with the build; gender, age and DMA cuts "
         + (f"are {cut_label} each." if cut_label else "can be added.")),
        (f"Cost: {cost} for the Total Universe with its Avid cut, charged only when you approve the brief."
         if cost else "Nothing is charged until you approve the brief.") + size,
        f"What would you like to adjust? {q}",
    ]
    return '\n\n'.join(paras)


def answer(text, uname, tid, history, ctx=None, *, host=None, user=None):
    """The discussion reply for a thread with a subject; the one-line
    which-audience question for a thread without one; None when the
    words are not a discussion ask."""
    if not wants_discussion(text):
        return None
    page = ''
    try:
        from . import subject_rules as _sr
        page = _sr._page_name(ctx)
    except Exception:
        page = ''
    subject, how = subject_for(history, ctx, page)
    if not subject:
        opts = []
        try:
            from . import thread_subject as _ts
            from . import subject_rules as _sr
            if page and not _ts.is_dropped(page) and not _sr.page_rejected_in_thread(history, page):
                opts.append({'label': f'{page} (open on your screen)', 'subject': page})
        except Exception:
            pass
        reply = ("Happy to lay the pull out before anything runs. Which audience is it on? Name the brand, person, "
                 "title or behavior and I will walk through who counts, the window, what the file carries and the cost.")
        return {'success': True, 'action': 'answer', 'reply': reply,
                'followups': [o['label'] for o in opts] + ['Something else'] if opts else [],
                'offer_deck': False, 'deck_angle': None, 'pull_discussion': {'subject': '', 'asked': True},
                **({'memory_confirm': {'question': str(text or ''), 'options': opts}} if opts else {})}
    draft = draft_in_thread(history)
    if draft and draft.get('subject') and how == 'pinned':
        try:
            from . import subject_rules as _sr
            if not _sr.same_entity(draft['subject'], subject):
                draft = None     # a brief on another subject is not this pull's
        except Exception:
            pass
    build_label, cut_label = _prices(host, uname)
    reply = compose(subject, build_label=build_label, cut_label=cut_label, draft=draft)
    _q, chips = open_question(subject)
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': chips,
            'offer_deck': False, 'deck_angle': None, 'subject': subject,
            'pull_discussion': {'subject': subject, 'from': how, 'draft_reused': bool(draft)}}
