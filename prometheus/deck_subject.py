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

A title is the same title with "series", "show" or "tv" added
(2026-11-12, the same reader: "summary deck for mistletoe murders series
viewers" with Mistletoe Murders Viewers open and in the library). The
name read as a new subject, "Mistletoe Murders Series Viewers", matched
nothing, and the deck step asked "did you mean" about the profile on her
screen. ``plain_title`` is the name with those words taken out; it is
tried only after the name as written has matched nothing, so a library
name that carries the word itself ("The Rainmaker TV Series") still
wins on its own spelling.

The did-you-mean is a question with choices (``choice_payload``): it
carries its own sentence, so every reader of the reply (the page, the
ask log, the copy the team reads) sees the question that was asked. A
choice the reader taps is the answer (``choice_picked``): the deck
starts, and the page is never confirmed a second time.

Pure functions; the catalog and the thread are handed in. Nothing here
raises into a request. No em dashes in anything a reader sees.
"""
from __future__ import annotations

import re

__all__ = ['named', 'pick', 'for_name', 'is_recipient', 'question', 'pick_or_ask', 'plain_title', 'choice_payload',
           'choice_picked', 'CHOICE_DEFAULT_QUESTION', 'plain_match', 'buyer']

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


# the kind-of-title words a reader adds that a library name seldom carries
_MEDIUM_RX = re.compile(r"\b(?:tv|television|series|show|movie|film)\b", re.I)


def plain_title(name):
    """The name with the kind-of-title words a reader adds taken out:
    'mistletoe murders series viewers' is 'mistletoe murders viewers'.
    '' when the name carries none of them, or is nothing without them."""
    n = _squash(name)
    out = _squash(_MEDIUM_RX.sub(' ', n))
    if len(_fold(out)) < 3 or _fold(out) == _fold(n):
        return ''
    return out


def plain_match(name, match):
    """(name, candidates) for the library resolver once the name as
    written has matched nothing: the plain title and what it matches, or
    the name as it was and no candidates. `match(phrase)` is the
    resolver's own matcher. Never raises."""
    try:
        plain = plain_title(name)
        cands = list(match(plain) or []) if plain else []
    except Exception:
        cands = []
    return (plain, cands) if cands else (name, [])


def buyer(partner, subject):
    """The buyer a deck is made for, or '' when the name after "for" is
    the deck's own subject ("a summary deck for Mistletoe Murders
    Viewers" with that profile open): a profile is never its own buyer."""
    p = _squash(partner)
    return '' if (p and _same(p, subject)) else p


# ------------------------------------------------ the did-you-mean question
from .ask_outcome import CHOICE_DEFAULT_QUESTION  # noqa: E402  (one sentence, shared with the reply classifier)


def choice_payload(block):
    """The reply that carries the deck step's did-you-mean. The choices
    ride in ``clarify`` as before (the page draws them as chips); the
    question also rides as the reply's own sentence, so a reply that asks
    something is never read as a reply that says nothing."""
    blk = dict(block) if isinstance(block, dict) else {}
    q = _squash(blk.get('question') or '') or CHOICE_DEFAULT_QUESTION
    blk['question'] = q
    return {'success': True, 'clarify': blk, 'reply': q}


def _is_choice_question(text):
    t = _squash(text)
    return bool(t) and (t == CHOICE_DEFAULT_QUESTION or t.startswith('I could not find an exact match for')
                        or t.startswith('Here are the closest profiles in the library'))


def choice_picked(history, ctx):
    """True when the profile the request is bound to is the choice the
    reader just tapped on the deck step's did-you-mean: the last thing
    said in the thread is that question, it offered this profile, and
    the reader's turn after it names it. The pick is the answer, so the
    page is not confirmed again."""
    try:
        prim = (ctx or {}).get('primary') if isinstance(ctx, dict) else None
        if not isinstance(prim, dict):
            return False
        key = str(prim.get('s3_key') or '').strip()
        name = _squash(prim.get('name') or '').split(' - ')[0].strip()
        turns = [t for t in (history or []) if isinstance(t, dict)]
        idx = next((i for i in range(len(turns) - 1, -1, -1)
                    if str(turns[i].get('role') or '').lower() in ('agent', 'assistant')), None)
        if idx is None:
            return False
        asked = turns[idx]
        after = [t for t in turns[idx + 1:] if str(t.get('role') or '').lower() in ('user', 'human')]
        if len(after) != 1:
            return False                     # the pick is the one turn that follows the question
        said = _squash(after[0].get('text') or after[0].get('content') or '')
        meta = asked.get('meta') if isinstance(asked.get('meta'), dict) else {}
        offered = False
        for o in (meta.get('options') or []):
            if not isinstance(o, dict):
                continue
            bind = o.get('deckBind') if isinstance(o.get('deckBind'), dict) else {}
            bkey = str(((bind.get('primary') or {}) if isinstance(bind.get('primary'), dict) else {}).get('s3_key') or '').strip()
            lab = _squash(o.get('label') or '')
            if key and bkey == key and said and (said == lab or said == _squash(o.get('send') or '')):
                return True
            if bkey:
                offered = True
        if offered:
            return False                     # the question carried its choices and this was not one of them
        if not _is_choice_question(asked.get('text') or asked.get('content') or ''):
            return False
        head = _squash(re.split(r"\s+\(", said, maxsplit=1)[0])
        return bool(name) and bool(head) and _fold(head) == _fold(name)
    except Exception:
        return False


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
        alt = plain_title(name)             # "mistletoe murders series viewers" is Mistletoe Murders Viewers
        page = ''
        try:
            page = _squash(((ctx or {}).get('primary') or {}).get('name') or '').split(' - ')[0].strip()
        except Exception:
            page = ''
        if page and (_same(name, page) or (alt and _same(alt, page))):
            return ''                       # the page is what was named: nothing to settle
        for s in _thread_names(history, page):
            if (_same(name, s) or (alt and _same(alt, s))) and not (page and _same(s, page)):
                return s                    # the thread's own subject, as the thread spells it
        disp, cat = _library(name, catalog)
        if not disp and alt:
            disp, cat = _library(alt, catalog)
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
        alt = plain_title(name)
        if not page or _same(name, page) or (alt and _same(alt, page)):
            return None
        if any(_same(name, s) or (alt and _same(alt, s)) for s in _thread_names(history, page)) \
                or _library(name, catalog)[0] or (alt and _library(alt, catalog)[0]):
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
