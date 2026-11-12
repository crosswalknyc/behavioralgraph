"""The subject a deck request names is the deck's subject (Jenna
2026-11-11: "these errors cannot happen").

Armine Meseldzhyan (Lionsgate), 23:23 UTC on 2026-11-11, with Cruel
Summer Viewers open and a Mistletoe Murders Viewers profile built in the
same thread forty minutes earlier: "can we build a deck for mistletoe
murders". She was asked "Which subject should the deck be on: Mistletoe
Murders Viewers or Cruel Summer Viewers?", tapped "On Mistletoe Murders
Viewers", and read "Building the Cruel Summer Viewers insights deck".

Two causes, both closed here:

  * "a deck for X" was read only as "a deck made for the buyer X", and
    only when X was capitalized. In lower case the name was not read at
    all, so the ask named nothing and the profile on screen stood in.
  * the subject a which-subject chip carries (``bind_subject``) was not
    passed from the one door to the deck step, so the deck step saw a
    confirm with no name and took the profile on screen.

The rule, the same one a read brief follows (prometheus.read_brief: the
titles the words name are the subject; the page stands aside): the name
in the request is the deck's subject. ``pick`` returns that name and the
deck step settles it exactly as it settles a chip
(prometheus.deck_pick.settle): a library profile of that name, else the
read delivered on it in this thread, else one plain line that says
nothing is held on it. Never another subject in its place.

"A deck for <brand>" with another audience on screen still means that
audience's deck made for the brand, as it always has: a brand is a
buyer. A title or a person is what a deck is about, and so is anything
this thread has already been about.

Pure functions; the catalog and the thread are handed in. Nothing here
raises into a request. No em dashes in anything a reader sees.
"""
from __future__ import annotations

import re

__all__ = ['named', 'pick', 'for_name', 'is_recipient', 'question', 'pick_or_ask']

ASKED = 'asked_deck_for_whom'      # the ask log's name for the one question
_DECK_NOUN = r"(?:insights?\s+deck|powerpoint\s+deck|pitch\s+deck|deck|slides?|presentation|one[- ]pagers?|pptx|powerpoint|ppt)"
# "a deck for mistletoe murders", "slides for Noah Wyle please", "a powerpoint for the mistletoe murders profile"
_FOR_RX = re.compile(
    r"\b" + _DECK_NOUN + r"\s+for\s+(?P<name>[^.?!\n]{2,90}?)"
    r"(?=\s*(?:[.?!]|$|\bplease\b|\bthanks?\b|\bwith\s+|\busing\s+|\bfrom\s+(?:this|the|that)\b|\bshowing\s+|\bcovering\s+))", re.I)
# who a deck is made for, never what it is about
_RECIPIENT_RX = re.compile(
    r"^(?:me|us|him|her|them|you|myself|ourselves|everyone|everybody|anyone|review|approval|reference|internal(?:\s+use)?|"
    r"(?:my|our|the|a|an|your|his|her|their)\s+(?:boss|manager|team|teams|client|clients|customer|customers|partner|partners|"
    r"buyer|buyers|sponsor|sponsors|advertiser|advertisers|agency|agencies|board|ceo|cmo|cfo|coo|president|exec(?:utive)?s?|"
    r"leadership|management|sales(?:\s+team)?|marketing(?:\s+team)?|stakeholders?|investors?|colleagues?|group|department|"
    r"meeting|pitch|call|review|presentation|upfronts?|deal|renewal|rfp|offsite|kickoff|conference|session|workshop)\b.*|"
    r"(?:the\s+|a\s+|an\s+|our\s+|my\s+)?(?:meeting|pitch|call|review|presentation|upfronts?|deal|renewal|rfp|client|team|"
    r"board|leadership|sales|marketing|monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|tonight|"
    r"next\s+week|this\s+week|q[1-4])\b.*)$", re.I)
# a purpose or a kind of buyer in general words: still not a subject
_PURPOSE_RX = re.compile(
    r"^(?:(?:new|potential|prospective|future|existing|current|key|top|major|big|national|regional|local)\s+)?"
    r"(?:business|biz|planning|strategy|sales|marketing|social|press|pr|outreach|prospecting|pitching|"
    r"brands?|advertisers?|sponsors?|partners?|clients?|buyers?|agencies|investors?|retailers?|networks?|streamers?|studios?|"
    r"distributors?|platforms?|stakeholders?|executives?|execs?|leadership|management)"
    r"(?:\s+(?:purposes?|use|team|teams|meetings?|pitch(?:es)?|outreach|planning|review|deck))?$", re.I)
_POINTER_RX = re.compile(r"^(?:this|that|it|these|those|them|the\s+(?:data|profile|page|read|audience|above|same)|this\s+\w+|that\s+\w+)$", re.I)
_TAIL_RX = re.compile(r"\s+(?:profile|audience|viewers?\s+profile|data|insights?|read|report)$", re.I)
_LEAD_RX = re.compile(r"^(?:the|a|an)\s+", re.I)

# the families of a title or a person: what a deck is about, never who buys it
_ABOUT_FALLBACK = frozenset((
    'ACTOR', 'ATHLETE', 'COMEDIAN', 'INFLUENCER/CREATOR', 'CREATOR/INFLUENCER', 'EMERGING TALENT', 'HOST/PERSONALITY',
    'MUSICIAN/BAND', 'PODCASTER', 'POLITICS/ACTIVIST', 'WRITER/DIRECTOR/AUTHOR/ARTIST',
    'GAME PLAYERS', 'GAMES', 'GAMES - PLAYERS', 'MOVIE', 'PODCAST', 'VERTICAL SHORTS'))


def _about_categories():
    out = set(_ABOUT_FALLBACK)
    try:
        import iq_rankers as _iq
        mc = getattr(_iq, 'MASTER_CATEGORIES', None) or {}
        for fam in ('TALENT', 'CONTENT'):
            out |= {str(c).strip().upper() for c in (mc.get(fam) or [])}
    except Exception:
        pass
    return out


def _fold(text):
    return re.sub(r"[^a-z0-9]+", '', str(text or '').lower())


def _squash(text):
    return ' '.join(str(text or '').split())


def is_recipient(name):
    """True when the words after "for" say who the deck is made for (my
    boss, the client, the Monday meeting), not what it is about."""
    n = _squash(name).strip(" '\"`,.")
    return (not n) or bool(_RECIPIENT_RX.match(n)) or bool(_POINTER_RX.match(n)) or bool(_PURPOSE_RX.match(n))


def for_name(text):
    """The name in "a deck for <name>", cleaned ('' when the words after
    "for" are a recipient, a pointer or nothing)."""
    m = _FOR_RX.search(_squash(text))
    if not m:
        return ''
    name = m.group('name').strip(" '\"`,.")
    if is_recipient(name):
        return ''
    name = _LEAD_RX.sub('', name)
    prev = None
    while prev != name:
        prev = name
        name = _TAIL_RX.sub('', name).strip(" '\"`,.")
    if not name or is_recipient(name) or len(name.split()) > 8:
        return ''
    return name


def _display(name):
    """A typed name as a reader would write it: lower case typing is
    title cased; anything with its own capitals is left alone."""
    n = _squash(name)
    if n and n == n.lower():
        small = {'of', 'the', 'and', 'in', 'on', 'at', 'a', 'an', 'to', 'for', 'vs', 'x'}
        words = n.split()
        n = ' '.join(w if (i and w in small) else (w[:1].upper() + w[1:]) for i, w in enumerate(words))
    return n


def _same(a, b):
    fa, fb = _fold(str(a or '').split(' - ')[0]), _fold(str(b or '').split(' - ')[0])
    return bool(fa) and bool(fb) and (fa == fb or (min(len(fa), len(fb)) >= 5 and (fa in fb or fb in fa)))


def _thread_names(history, page=''):
    """What this thread has been about: the subjects on its turns (a read
    landed, a build started or finished), most recent first."""
    out = []
    try:
        from . import subject_rules as _sr
        out.extend(_sr.thread_subjects(history, page))
    except Exception:
        pass
    for turn in reversed(list(history or [])):
        if not isinstance(turn, dict) or str(turn.get('role') or '') in ('user', 'human'):
            continue
        meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
        s = _squash(meta.get('subject') or turn.get('subject') or '')
        if s and not any(_fold(s) == _fold(o) for o in out):
            out.append(s)
    return out


def _library(name, catalog):
    """(display name, category) of the library profile the name is, or
    ('', ''). The name written out, or the name plus an audience noun
    ("mistletoe murders" for Mistletoe Murders Viewers). Total Universe
    files only; a cut never leads a deck."""
    want = _fold(name)
    if not want:
        return '', ''
    best = None
    try:
        rows = list(catalog() if callable(catalog) else (catalog or []))
    except Exception:
        rows = []
    for e in rows:
        if not isinstance(e, dict):
            continue
        disp = _squash(e.get('display_name') or '')
        if not disp or ' - ' in disp:
            continue
        for field in (disp, _squash(e.get('subject') or '')):
            f = _fold(field)
            if not f:
                continue
            rest = f[len(want):] if f.startswith(want) else None
            if f == want or (rest is not None and re.fullmatch(
                    r"(?:viewers?|fans?|listeners?|readers?|players?|buyers?|customers?|shoppers?|audience|series|"
                    r"users?|subscribers?|members?|consumers?|watchers?)", rest or 'x')):
                score = (1 if f == want else 0, -len(f))
                if best is None or score > best[0]:
                    best = (score, disp, str(e.get('category') or '').strip().upper())
    return (best[1], best[2]) if best else ('', '')


def named(text, ctx=None, history=None, catalog=None):
    """The subject a deck request names in "a deck for <name>", or ''.

    The name is the subject when this thread has already been about it,
    when it is a title or a person in the library, or when it was typed
    in lower case (a name the partner reading never saw: it is the only
    thing the request names, so it is what the deck is about). A
    capitalized brand with another audience on screen stays the buyer."""
    try:
        name = for_name(text)
        if not name:
            return ''
        page = ''
        try:
            page = _squash(((ctx or {}).get('primary') or {}).get('name') or '').split(' - ')[0].strip()
        except Exception:
            page = ''
        if page and _same(name, page):
            return ''                       # the page is what was named: nothing to settle
        for s in _thread_names(history, page):
            if _same(name, s) and not (page and _same(s, page)):
                return s                    # the thread's own subject, as the thread spells it
        disp, cat = _library(name, catalog)
        if disp:
            about = cat in _about_categories() or cat.startswith('SERIES')
            if about or not page:
                return disp                 # a title or a person; or a brand with nothing else on screen
            return ''                       # a brand, with another audience open: the deck is made for it
        if name != name.lower() and page:
            return ''                       # a capitalized name we do not hold, another audience open: asked (question)
        return _display(name)               # the only thing the request names; settled or said plainly, never swapped
    except Exception:
        return ''


def pick(body, text, ctx=None, history=None, catalog=None):
    """The subject the deck step settles on: the chip the reader tapped
    (``bind_subject`` with the confirm), else the name the request
    carries. '' when the request names nothing (the words and the page
    decide, as before)."""
    try:
        b = body if isinstance(body, dict) else {}
        chip = _squash(b.get('bind_subject') or '')
        if chip and b.get('confirm_open_screen'):
            return chip
        if b.get('confirm_open_screen'):
            return ''                       # "Yes, <page>": the page was confirmed
        return named(text, ctx, history, catalog)
    except Exception:
        return ''


def question(body, text, ctx=None, history=None, catalog=None, hint=None):
    """The one short question for "a deck for <Name>" when the name is
    written as a name, nothing is held on it, and another audience is on
    screen: is the deck that audience's, made for the name, or a deck on
    the name itself? None when there is nothing to ask. The chips carry
    the subject, so the tap builds exactly what it says."""
    try:
        b = body if isinstance(body, dict) else {}
        if b.get('confirm_open_screen') or _squash(b.get('bind_subject') or ''):
            return None
        name = for_name(text)
        if not name or name == name.lower():
            return None
        page = _squash(((ctx or {}).get('primary') or {}).get('name') or '').split(' - ')[0].strip()
        if not page or _same(name, page):
            return None
        if any(_same(name, s) for s in _thread_names(history, page)) or _library(name, catalog)[0]:
            return None
        opts = [{'label': f'{page}, made for {name}', 'subject': page, 'deck': True},
                {'label': f'A deck on {name}', 'subject': name, 'deck': True}]
        reply = (f"I do not hold a profile or a finished read on {name}. Should the deck be on {page}, made for {name}, "
                 f"or on {name} itself?")
        if callable(hint):
            try:
                hint(route='deck', outcome=ASKED, subject=page)
            except Exception:
                pass
        return {'success': True, 'action': 'answer', 'reply': reply,
                'followups': [o['label'] for o in opts] + ['Something else'], 'offer_deck': False, 'deck_angle': None,
                'memory_confirm': {'question': str(text or ''), 'deck': True, 'options': opts}}
    except Exception:
        return None


def pick_or_ask(body, text, ctx=None, history=None, catalog=None, hint=None):
    """(pick, question) for the deck step: the one short question when
    there is one to ask (then pick is ''), else the subject the request
    names ('' when it names none)."""
    q = question(body, text, ctx, history, catalog, hint=hint)
    if q:
        return '', q
    return pick(body, text, ctx, history, catalog), None
