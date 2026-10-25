"""The open profile is context, never a default subject (Jenna 2026-10-20).

Two days, two threads, one defect family:

  Mark Valdez (Bent Pixels), Salish Matter open on his screen. Three
  turns into a conversation about DraftKings users, the deck lane asked
  "Do you want this on Salish Matter (open on your screen)?"; his
  correction, "No I am interested in showing a lot of aligned
  creators...I am unclear why you keep bringing up Salish matter", was
  read by the analysis pass as a build order and the interpret step
  drafted a $300 DraftKings Users profile. He approved it.

  Jenna, Ghost Energy Drink open. "run a profile on mountain dew" was
  swallowed by a journey intake armed thirty minutes earlier and came
  back as a $500 custom-read offer; the reader's restate for a bare
  "Create a digital journey" read "You want a Digital Journey built for
  Ghost Energy Drink, the subject open on your screen".

Jenna, verbatim: "it keeps trying to default to the open profile. it
shoudlnt." and "I said run a profile on mountain dew".

The rules, pure text, shared by every surface:

  1. explicit_build_subject(text): a build verb plus a named subject
     ("run a profile on mountain dew", "build a profile for X", "pull
     a profile on X") is the Profile IQ build lane, always. Never a
     custom-read offer, never journey inputs, never the page.
  2. conversational_turn(text): a correction ("No I ...", "not X",
     "that's not what I meant"), a why-do-you-keep, a clarification
     of the previous answer. It continues the conversation; nothing
     with a price on it is ever drafted from it.
  3. priced_handoff_allowed(text): the one gate every priced lane
     (build, cut, journey, valuation, custom read, talent list) asks
     before a model's verdict becomes a draft: the words must carry an
     order for the thing and must not be a conversational turn.
  4. page_rejected_in_thread(history, page): once the reader has said
     "not <page>" in the thread, the page is never again the default
     or the lead chip in that thread.
  5. thread_subjects(history, page): the subjects the conversation has
     been about (the reads Prometheus ran, the runs it started), so a
     which-subject question offers what the thread is about before the
     page.
  6. reading_subject(reading, page): a subject the Reader found in the
     words (a title, a brand, a person, a show), not the page and not a
     pointer at the page, wins over the open tab. 2026-10-20, Jay Patel
     at Prime Video: "where people watched the film mutiny" with Amazon
     Prime Video open. The Reader had 'the film mutiny' as a title; the
     open-screen confirm never looked and asked "Do you want this on
     Amazon Prime Video?". Jenna: "it's trying to do the open tab agqain
     instead of just telling him where people watch this piece of
     content".

No Flask, no S3, no model.

One subject, bound once (I1, 2026-10-20, a day of misroutes): ``bind``
is the single binder every lane that shows a subject goes through. It
reads the words first, then the thread, then the page, and returns a
``Subject`` whose ``display`` is the only spelling a reader ever sees:
"the film mutiny" is Mutiny, "the Among Us series on Paramount+" is
Among Us with platform Paramount+, "Title 'mutiny' Without Paying" is
Mutiny, "a betting client like draftkings" is DraftKings, "Everybody
Loves Raymond viewers on Amazon Prime Video, September 2026" is the
title with viewers / platform / window as qualifiers, "Unique" and
"watch scary movies" are a metric and a modifier and name nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ['explicit_build_subject', 'conversational_turn', 'priced_handoff_allowed',
           'page_rejected_in_thread', 'thread_subjects', 'which_subject_payload',
           'CONVERSATIONAL_NOTE', 'strip_lead_negation', 'reading_subject', 'current_reading_subject',
           'definition_turn', 'Subject', 'bind', 'clean_display', 'is_metric_phrase',
           'nested_audience', 'qualified_audience', 'clarify_for', 'wants_breakdown', 'display_for']

# A pointer at what is on the screen, never a subject of its own.
_DEIXIS = frozenset(('<page>', 'this', 'that', 'it', 'this audience', 'this profile',
                     'the audience', 'the profile', 'this one', 'these', 'those',
                     'the page', 'this page', 'the open profile', 'the file', 'this file'))
# The Reader's kinds that name a thing of their own. 'audience' and
# 'cohort' describe the page's people; 'metric' and 'unknown' name nothing.
_NAMED_KINDS = frozenset(('title', 'show', 'series', 'film', 'movie', 'book', 'podcast', 'game',
                          'brand', 'company', 'person', 'talent', 'creator', 'artist', 'team',
                          'league', 'franchise', 'platform', 'channel', 'app', 'event', 'place',
                          'product', 'retailer', 'network', 'studio', 'album', 'song'))


def reading_subject(reading, page, normalize=None):
    """The subject the Reader found in the words that is not the open
    page: its library spelling when it resolved, else as written with
    the type words taken off ('the film mutiny' -> 'Mutiny'). '' when
    the reading names nothing of its own, points at the page, or names
    the page itself."""
    if not isinstance(reading, dict):
        return ''
    norm = normalize or (lambda s: re.sub(r"[^a-z0-9]+", "", str(s or '').lower()))
    page_n = norm(str(page or '').split(' - ')[0])
    for s in (reading.get('subjects') or []):
        if not isinstance(s, dict):
            continue
        as_written = str(s.get('as_written') or '').strip()
        catalog = str(s.get('catalog') or '').strip()
        kind = str(s.get('kind') or '').strip().lower()
        if not as_written and not catalog:
            continue
        if as_written.lower().strip(" .?!") in _DEIXIS:
            continue
        if kind and kind not in _NAMED_KINDS:
            continue
        name = catalog or _bare_name(as_written)
        if not name:
            continue
        n = norm(name.split(' - ')[0])
        if not n or (page_n and (n == page_n or n in page_n or page_n in n)):
            continue
        return name
    return ''


def current_reading_subject(page, normalize=None):
    """reading_subject over the Reader's reading for the request in
    flight (flask.g._pm_reading); '' outside a request or without one."""
    try:
        from flask import g as _g
        return reading_subject(getattr(_g, '_pm_reading', None), page, normalize)
    except Exception:
        return ''


_DEFINITION_TURN_RX = re.compile(
    r"^\W*(?:ok[,.]?\s+|so[,.]?\s+|wait[,.]?\s+|sorry[,.]?\s+)?"
    r"(?:what(?:'s|s| is| are| was| does| do)\b.{0,60}\b(?:you(?:'re| are| were)? ?(?:mention(?:ed|ing)?|referenc(?:ed|ing)|referr(?:ed|ing) to|quot(?:ed|ing)|talking about|said|cited)|that (?:number|figure|term|mean)|this (?:number|figure|term|mean)|the (?:figure|term|number) (?:above|there|you))"
    r"|what do(?:es)? (?:you|that|this|it) mean\b|what (?:does|do) .{1,60}\bmean\b|explain (?:that|this|the (?:number|figure|term|last))|can you (?:explain|clarify|define)\b|define\b|clarify\b)", re.I)


def definition_turn(text, reading=None, page=''):
    """A question about the previous answer or a term in it, naming no
    subject of its own: 'what's the 10 million sample you're mentioning',
    'what does that number mean'. The Reader's reading (intent
    'definition' with no subject) decides when it is in hand; the words
    decide otherwise. Such a turn continues the conversation and is
    never a reason to offer the open profile (2026-10-20, Tania Nasr at
    Paramount+ drew 'Do you want this on BET (open on your screen)?')."""
    r = reading
    if r is None:
        try:
            from flask import g as _g
            r = getattr(_g, '_pm_reading', None)
        except Exception:
            r = None
    if isinstance(r, dict) and r.get('intent'):
        if str(r.get('intent')).lower() == 'definition' and not reading_subject(r, page):
            return True
        if str(r.get('intent')).lower() not in ('definition', 'chat', 'decline', 'unknown', ''):
            return False
    return bool(_DEFINITION_TURN_RX.search(str(text or '')))


_TYPE_LEAD_RX = re.compile(
    r"^(?:the|that|this|a|an)\s+(?:film|movie|show|series|title|book|novel|podcast|game|"
    r"brand|app|channel|network|album|song|franchise|docuseries|documentary|sitcom|drama|"
    r"musical|comic|manga|anime|cartoon|band|artist|author|company|team|league)\s+"
    r"(?:called\s+|titled\s+|named\s+)?", re.I)


def _bare_name(as_written):
    """'the film mutiny' -> 'Mutiny'; "'mutiny'" -> 'Mutiny'; 'The Bear' stays."""
    s = str(as_written or '').strip().strip("\"\u201c\u201d'\u2018\u2019 ")
    s = _TYPE_LEAD_RX.sub('', s).strip().strip("\"\u201c\u201d'\u2018\u2019 ")
    if not s:
        return ''
    if s == s.lower():
        words = s.split()
        s = ' '.join(w if (i and w in ('of', 'the', 'and', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'de', 'la'))
                     else (w[:1].upper() + w[1:]) for i, w in enumerate(words))
    return s

# "run a profile on X", "build a profile for X", "pull a profile of X",
# "run aprofile on X" (the missing space is the common typo), "profile X
# for me" is NOT here (a bare noun order is the interpret step's call).
_BUILD_VERBS = r"(?:run|build|pull|create|queue|start|launch|generate|make|do|kick off|spin up)"
_PROFILE_NOUN = r"(?:a\s*)?(?:new\s+|fresh\s+|full\s+|total\s+universe\s+)?(?:profiles?|porfiles?|profles?|proifles?|profiels?|prfiles?)"
EXPLICIT_BUILD_RX = re.compile(
    r"^\W*(?:please\s+|can you\s+|could you\s+|would you\s+|i want you to\s+|i need you to\s+|let'?s\s+|go ahead and\s+)?"
    r"(?:" + _BUILD_VERBS + r")\s+(?:me\s+|us\s+)?" + _PROFILE_NOUN +
    r"\s+(?:on|for|of|about|around)\s+(?P<subject>[^\n?!.;]{2,120}?)"
    r"(?:\s*[.!?;]|\s+(?:please|now|today|asap|thanks?|thank you)\b.*)?\s*$",
    re.I)
# the audience-universe order shape understand.py also knows
_UNIVERSE_BUILD_RX = re.compile(
    r"^\W*(?:please\s+|can you\s+|could you\s+)?(?:" + _BUILD_VERBS + r")\s+(?:me\s+|us\s+)?"
    r"(?:an?\s+|the\s+)?(?:audience|universe|segment|cohort)s?\s+(?:of|for)\s+(?P<subject>[^\n?!.;]{3,120}?)\s*[.!?]*\s*$",
    re.I)

_TRAILING_NOISE_RX = re.compile(
    r"\s*(?:,|\band\b)?\s*(?:please|now|today|asap|thanks?|thank you|for me|for us|instead|then|too|as well)\s*$", re.I)

# a correction or a question about the previous turn, never an order
_CORRECTION_RX = re.compile(
    r"^\W*(?:no+|nope|nah|not that|not quite|wrong|incorrect|negative|that'?s (?:not|wrong)|that is (?:not|wrong)|"
    r"i did ?n[o']?t (?:want|ask|say|mean)|i do ?n[o']?t (?:want|need|mean)|i never (?:asked|said|wanted)|"
    r"i (?:am|'m) not (?:interested|asking|talking) in?|i wasn'?t (?:asking|talking)|"
    r"this (?:was|is) not (?:correct|right|what i)|that'?s not what i|not what i (?:asked|meant|wanted|said)|"
    r"you (?:misunderstood|misread|got (?:it|that) wrong))\b", re.I)
_WHY_KEEP_RX = re.compile(
    r"\b(?:why (?:do|does|did|would|are) (?:you|it|this|prometheus) (?:keep|still|always|again)|"
    r"why (?:do you|does it) keep|why (?:are|is) (?:you|it) (?:bringing|pulling|using|defaulting)|"
    r"(?:i'?m|i am) (?:unclear|confused|not sure|unsure) (?:why|what|how)|"
    r"stop (?:bringing|pulling|using|defaulting|suggesting|offering)|"
    r"(?:keep|keeps) (?:bringing|pulling|coming) (?:up|back)|"
    r"i (?:did ?n[o']?t|never) (?:say|mention|ask (?:about|for)) )", re.I)
_CLARIFY_PREV_RX = re.compile(
    r"^\W*(?:what do you mean|i mean|i meant|to clarify|clarif(?:y|ication)|let me (?:clarify|rephrase|be clear)|"
    r"in other words|what i (?:meant|want|need|am after|am looking for) (?:is|was)|rather,?|instead,?)\b", re.I)

# a build / cut / journey / valuation ORDER inside a correction keeps it a
# correction ("No, build it on Mountain Dew instead" names the new order,
# so the words still carry the order after the negation is stripped)
_ORDER_RX = re.compile(
    r"\b(?:run|build|pull|create|queue|start|launch|generate|make|value|map|set up|track)\b"
    r"[^.?!]{0,40}\b(?:a?profiles?|porfiles?|profles?|proifles?|profiels?|audience|universe|segment|cohort|cut|journey|valuation|partnership|read|list|campaign|tracker|deck)\b", re.I)

_LEAD_NEGATION_RX = re.compile(
    r"^\W*(?:no+|nope|nah|not that|not quite|wrong|actually|wait|hold on|hmm|um|ok(?:ay)?|sorry)[,.!: ]+", re.I)


def _clean_subject(s):
    s = ' '.join(str(s or '').split()).strip(' ,;:-')
    s = _TRAILING_NOISE_RX.sub('', s).strip(' ,;:-')
    # "the X audience" / "X fans" stay as written; a leading article drops
    s = re.sub(r"^(?:the|a|an)\s+", '', s, flags=re.I)
    return s


def explicit_build_subject(text):
    """The subject of an explicit Profile IQ build order, or ''.

    "run a profile on mountain dew" -> "mountain dew"; "build a profile
    for Nike runners please" -> "Nike runners"; "run aprofile on ncis: new
    york" -> "ncis: new york". A question ("can you run a profile on X?")
    still orders the profile: the reader asked for it politely. A bare
    name, a question about a profile, or a correction returns ''."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 400:
        return ''
    if conversational_turn(t):
        return ''
    m = EXPLICIT_BUILD_RX.match(t.rstrip('?'))
    if not m:
        m = _UNIVERSE_BUILD_RX.match(t.rstrip('?'))
    if not m:
        return ''
    subj = _clean_subject(m.group('subject'))
    if not subj or len(subj) < 2:
        return ''
    low = subj.lower()
    # "this", "it", "that" point at the page: no named subject (rule 1)
    if low in ('this', 'that', 'it', 'them', 'these', 'those', 'this audience', 'this profile',
               'the open profile', 'the profile', 'this one', 'the same'):
        return ''
    return subj


def conversational_turn(text):
    """'correction' | 'why_keep' | 'clarification' | '' for a turn that
    continues the conversation instead of ordering something.

    A correction that carries a fresh order ("No, run a profile on
    Mountain Dew instead") is NOT a conversational turn: the order is
    read after the negation is stripped."""
    t = ' '.join(str(text or '').split())
    if not t:
        return ''
    stripped = strip_lead_negation(t)
    carries_order = bool(_ORDER_RX.search(stripped)) and (
        bool(EXPLICIT_BUILD_RX.match(stripped.rstrip('?'))) or bool(_UNIVERSE_BUILD_RX.match(stripped.rstrip('?')))
        or bool(re.match(r"^\W*(?:run|build|pull|create|queue|start|launch|generate|make|value|map|set up|track)\b", stripped, re.I)))
    if _WHY_KEEP_RX.search(t):
        return 'why_keep'
    if _CORRECTION_RX.match(t) and not carries_order:
        return 'correction'
    if _CLARIFY_PREV_RX.match(t) and not carries_order:
        return 'clarification'
    return ''


def strip_lead_negation(text):
    """"No, build it on Mountain Dew" -> "build it on Mountain Dew"."""
    t = ' '.join(str(text or '').split())
    out = _LEAD_NEGATION_RX.sub('', t, count=1)
    return out if out else t


def priced_handoff_allowed(text, *, order_rx=None):
    """May a model's verdict ("this is a build", "a journey", "a cut", "a
    read") become a draft with a price on it? Only when the words carry
    an order for such a thing and the turn is not a correction or a
    question about the previous answer. Build is never the default."""
    t = ' '.join(str(text or '').split())
    if not t:
        return False
    if conversational_turn(t):
        return False
    rx = order_rx or _ORDER_RX
    return bool(rx.search(t))


def _norm(s):
    return re.sub(r"[^a-z0-9]+", ' ', str(s or '').lower()).strip()


def _page_tokens(page):
    base = str(page or '').split(' - ')[0]
    toks = [w for w in _norm(base).split() if len(w) >= 3]
    return base, toks


def page_rejected_in_thread(history, page):
    """True when a USER turn in the thread said no to the page: "not
    interested in salish matter", "no, not Salish Matter", "instead of
    Salish Matter", "why do you keep bringing up Salish matter"."""
    base, toks = _page_tokens(page)
    if not toks:
        return False
    key = ' '.join(toks)
    for turn in (history or []):
        if not isinstance(turn, dict) or str(turn.get('role') or '') != 'user':
            continue
        low = _norm(turn.get('text') or turn.get('content') or '')
        if key not in low:
            continue
        win = low
        if re.search(r"\b(?:not|no|never|nothing to do with|has nothing|instead of|rather than|other than|besides|without|stop|unclear why|why (?:do|does|did|would) (?:you|it) keep|don t want|do not want|didn t (?:ask|say|mean|want)|did not (?:ask|say|mean|want))\b", win):
            # the negation sits in the same sentence as the page name
            for sent in re.split(r"[.?!]+", str(turn.get('text') or turn.get('content') or '')):
                s = _norm(sent)
                if key in s and re.search(r"\b(?:not|no|never|nothing to do with|has nothing|instead of|rather than|other than|besides|without|stop|unclear why|why (?:do|does|did|would) (?:you|it) keep|don t want|do not want|didn t|did not)\b", s):
                    return True
    return False


_READING_RX = re.compile(r"^On it:\s*reading\s+(?P<what>.+?)\s+now\.?", re.I)
_STARTED_RX = re.compile(r"^Started\s+(?P<what>.+?)\s*(?:\(number|\.|$)", re.I)


def bound_thread_subject():
    """The project the current thread is bound to (the conversation
    follows the project, Debi Pomerantz 2026-10-21), as the label a
    reader sees; '' outside a bound thread or outside a request."""
    try:
        from flask import g as _g
        b = getattr(_g, '_pm_thread_binding', None)
        if isinstance(b, dict) and b.get('subject_key'):
            return ' '.join(str(b.get('subject_label') or '').split()).strip(' .,;:')
    except Exception:
        pass
    return ''


def thread_subjects(history, page='', limit=4):
    """The subjects this thread has been about, most recent first: the
    project the thread is bound to (a bound thread's subject IS the
    thread subject, so a question in the Golden Axe thread is about
    Golden Axe whatever tab is open), then the reads Prometheus started
    ("On it: reading X now"), the runs it started (meta.subject), the
    subjects on agent turn meta. The page is left out when the thread
    is unbound; a binding that names the page keeps it, since on a
    bound thread the page is the project. Duplicates fold
    case-insensitively."""
    out, seen = [], set()
    _, ptoks = _page_tokens(page)
    pkey = ' '.join(ptoks)
    bound = bound_thread_subject()
    if bound and pkey == _norm(bound):
        pkey = ''        # the page is the project: not an exclusion

    def _add(name):
        name = ' '.join(str(name or '').split()).strip(' .,;:')
        if not name or len(name) > 90:
            return
        k = _norm(name)
        if not k or k in seen or (pkey and k == pkey):
            return
        seen.add(k)
        out.append(name)

    if bound:
        _add(bound)
    turns = [t for t in (history or []) if isinstance(t, dict)]
    for pos in range(len(turns) - 1, -1, -1):
        turn = turns[pos]
        if str(turn.get('role') or '') == 'user':
            continue
        meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
        if meta.get('subject'):
            _add(meta['subject'])
        txt = ' '.join(str(turn.get('text') or turn.get('content') or '').split())
        m = _READING_RX.match(txt)
        if m:
            what = m.group('what')
            # "reading X and Y now" names two things; the page rides along
            # in the first clause when it was the open profile. A title
            # that carries 'and' inside it ("Rick and Morty") is one thing
            # (2026-10-22): the user turn the promise answered binds it
            # whole, so the split is skipped when the binder reads the
            # whole phrase as the subject. A part that is a bare prefix
            # of another part is the old binder's truncation ("Jackass:
            # Best &" beside "Jackass: Best & Last") and is dropped.
            prev_user = ''
            for back in range(pos - 1, -1, -1):
                if str(turns[back].get('role') or '') == 'user':
                    prev_user = ' '.join(str(turns[back].get('text') or turns[back].get('content') or '').split())
                    break
            parts = [what.strip()]
            if re.search(r"\s+and\s+", what):
                parts = [x.strip() for x in re.split(r"\s+and\s+", what) if x.strip()]
                try:
                    whole = bind(prev_user, None, '') if prev_user else None
                    if whole is not None and whole.name and _norm(whole.name) == _norm(what):
                        parts = [what.strip()]
                except Exception:
                    pass
                keys = [_norm(x) for x in parts]
                parts = [x for x, k in zip(parts, keys)
                         if not any(o != k and o.startswith(k + ' ') for o in keys)]
            for part in parts:
                if not part or re.search(r"\b(?:all the|the best|which|what|how)\b", part, re.I):
                    continue
                # "a betting client like draftkings" names draftkings
                m_like = re.match(r"^(?:a|an|the)\s+.+?\blike\s+(.+)$", part, re.I)
                _add(m_like.group(1) if m_like else part)
        m2 = _STARTED_RX.match(txt)
        if m2:
            _add(m2.group('what'))
        if len(out) >= limit:
            break
    return out[:limit]


CONVERSATIONAL_NOTE = (
    "READ THIS TURN AS A CORRECTION OR A FOLLOW-UP INSIDE THE CONVERSATION, NOT AS A NEW ORDER. "
    "Answer it from the thread above and from what is on the screen, in the voice of the analyst who "
    "has been in this conversation. The profile on the screen is context only; the subject is whatever "
    "the conversation has been about, and when the reader says they do not want the on-screen profile, "
    "say plainly that it is the profile open on their screen, drop it, and stay on their subject. "
    "When they ask for creators, talent, brands or platforms that fit an audience, list many with the "
    "figures the thread already holds, ranked. Never a build, never a profile offer, never a price, "
    "never 'build_profile' or 'generate_metrics': action must be 'answer'. If one thing is still "
    "unclear, ask that one question in the reply.")


def build_reroute_payload():
    """The analyze surface handing an explicit profile order to the
    interpret flow (the widget's reroute contract)."""
    # the reply names the handoff, so the thread never reads "Nothing came back" (I4c)
    return {'success': True, 'action': 'build_profile', 'route_hint': 'interpret',
            'reply': 'Setting up the profile brief for you to approve.', 'followups': [], 'offer_deck': False, 'deck_angle': None}


def open_screen_question(text, page, history, *, deck=False, what='this'):
    """The which-subject question for an ask that names no subject while
    a profile is open, when the thread has its own subject or has said
    no to the page. None when neither holds (the caller keeps its
    plain one-tap confirm on the page). The options carry the deck flag
    so the deck lane's memory confirm re-runs as a deck."""
    page = str(page or '').strip()
    rejected = page_rejected_in_thread(history, page)
    subs = thread_subjects(history, page)
    if not rejected and not subs:
        return None
    ws = which_subject_payload(page, subs, what=what)
    opts = ws['options']
    if deck:
        opts = [dict(o, deck=True) for o in opts]
    return {'success': True, 'action': 'answer', 'reply': ws['reply'],
            'followups': ws['followups'], 'offer_deck': False, 'deck_angle': None,
            'memory_confirm': {'question': text, 'options': opts, **({'deck': True} if deck else {})},
            'outcome': 'asked_which_subject_page_rejected' if rejected else 'asked_which_subject_thread'}


def conversation_hold(text, history, page, reask, scrub, note=None):
    """A model's build verdict on a turn that orders no build. Reads the
    turn once more as the correction it is (``reask(note)`` returns the
    model's data dict); a plain answer wins, anything else becomes the
    which-subject question. Returns ('answer', data) or ('ask', payload).
    Never a draft, never a price."""
    try:
        d3 = reask(note or CONVERSATIONAL_NOTE) or {}
    except Exception:
        d3 = {}
    if isinstance(d3, list):
        d3 = next((x for x in d3 if isinstance(x, dict)), {})
    reply = scrub(str(d3.get('reply') or '').strip()) if isinstance(d3, dict) else ''
    if reply and str(d3.get('action') or 'answer') not in ('build_profile', 'generate_metrics'):
        data = dict(d3)
        data['action'] = 'answer'
        data['reply'] = reply
        return 'answer', data
    page = str(page or '').strip()
    ws = which_subject_payload(page, thread_subjects(history, page))
    return 'ask', {'success': True, 'action': 'answer', 'reply': ws['reply'],
                   'followups': ws['followups'], 'offer_deck': False, 'deck_angle': None,
                   'memory_confirm': {'question': text, 'options': ws['options']}}


def which_subject_payload(page, subjects, *, what='this'):
    """The one-tap which-subject question when the words name no subject:
    the thread's subjects first, the page last and labeled as the open
    profile, then 'Something else'. Never a yes/no on the page alone."""
    opts = []
    for s in (subjects or []):
        opts.append({'label': f'On {s}', 'subject': s})
    if page:
        opts.append({'label': f'{page} (open on your screen)', 'subject': page})
    names = [o['subject'] for o in opts]
    if len(names) >= 2:
        lead = ', '.join(names[:-1]) + f' or {names[-1]}'
        reply = f'Which subject should {what} be on: {lead}?'
    elif names:
        reply = f'Which subject should {what} be on? {names[0]} is what is open; name another if you mean something else.'
    else:
        reply = f'Which subject should {what} be on?'
    return {'reply': reply, 'options': opts,
            'followups': [o['label'] for o in opts] + ['Something else']}


# ===========================================================================
# One subject, bound once (I1)
# ===========================================================================
@dataclass
class Subject:
    """What an ask is about, bound once for every lane.

    kind: title | brand | person | audience | metric | none
    name: the bound name (library spelling when known)
    display: the only spelling a reader sees (clean, cased)
    qualifiers: window, platform, verb (viewers / readers / ...), cut,
                condition ("without paying for it")
    source: words | thread | page | none
    pointed: the words point at the page ("this audience", "it")
    """
    kind: str = 'none'
    name: str = ''
    display: str = ''
    qualifiers: dict = field(default_factory=dict)
    source: str = 'none'
    confidence: float = 0.0
    pointed: bool = False

    def __bool__(self):
        return bool(self.display) and self.kind != 'none'

    @property
    def named(self):
        """True when the words themselves name the subject."""
        return bool(self) and self.source == 'words'

    def label(self, with_platform=True):
        """'Among Us on Paramount+' for a platform-qualified title."""
        if not self.display:
            return ''
        p = str(self.qualifiers.get('platform') or '')
        return f"{self.display} on {p}" if (with_platform and p) else self.display


# metric and breakdown vocabulary: never a subject on its own
_METRIC_WORDS = frozenset((
    'unique', 'uniques', 'viewers', 'viewer', 'users', 'user', 'people', 'individuals', 'accounts',
    'hours', 'hour', 'minutes', 'views', 'streams', 'sessions', 'visits', 'reach', 'share',
    'shares', 'percent', 'percentage', 'index', 'indexes', 'penetration', 'rate', 'rates',
    'conversion', 'conversions', 'engagement', 'retention', 'churn', 'lift', 'count', 'counts',
    'number', 'numbers', 'total', 'totals', 'average', 'mean', 'median', 'per', 'by', 'surface',
    'surfaces', 'device', 'devices', 'platform', 'platforms', 'daypart', 'dayparts', 'month',
    'months', 'monthly', 'week', 'weeks', 'weekly', 'daily', 'quarter', 'quarters', 'year',
    'years', 'yearly', 'window', 'period', 'breakdown', 'split', 'table', 'chart', 'us', 'u.s.',
    'the', 'a', 'an', 'of', 'and', 'or', 'not', 'just', 'only', 'vs', 'versus', 'television',
    'tv', 'phone', 'mobile', 'desktop', 'tablet', 'ctv', 'demographic', 'demographics', 'demo',
    'demos', 'age', 'gender', 'income', 'geography', 'state', 'dma', 'size', 'sample',
    'incidence', 'audience', 'cohort', 'segment', 'cut', 'profile', 'data', 'metric', 'metrics',
    'kpi', 'kpis', 'figure', 'figures', 'results', 'result', 'frequency', 'volume', 'spend',
    'cost', 'price', 'revenue', 'all', 'every', 'each', 'other', 'rest', 'more', 'less'))
# a leading verb makes the phrase a behavior, not a thing
_DISCOURSE_LEAD_RX = re.compile(
    r"^(?:what|how|which|why|when|where|who|whom|whose|is|are|was|were|can|could|would|should|do|does|did|and|also|but|so|or|"
    r"what about|how about|same for|now|then|ok|okay|yes|no)\b", re.I)
_BEHAVIOR_LEAD_RX = re.compile(
    r"^(?:who\s+)?(?:also\s+)?(?:watch(?:es|ed|ing)?|drink(?:s|ing)?|drank|buy(?:s|ing)?|bought|read(?:s|ing)?|"
    r"listen(?:s|ed|ing)?|play(?:s|ed|ing)?|stream(?:s|ed|ing)?|shop(?:s|ped|ping)?|eat(?:s|ing)?|ate|"
    r"use(?:s|d)?|using|visit(?:s|ed|ing)?|follow(?:s|ed|ing)?|subscribe(?:s|d)?|own(?:s|ed)?|"
    r"like(?:s|d)?|love(?:s|d)?|prefer(?:s|red)?|search(?:es|ed|ing)?|click(?:s|ed)?|attend(?:s|ed)?)\b", re.I)
# wrappers an extractor leaves on a name
_WRAPPER_LEAD_RX = re.compile(
    r"^(?:the\s+)?(?:title|film|movie|show|series|book|podcast|game|brand|app|channel|network|album|song)\s+", re.I)
_LIKE_RX = re.compile(r"^(?:a|an|the|some)\s+.*?\blike\s+(?P<name>.+)$", re.I)
_AUDIENCE_IN_PROFILE_RX = re.compile(
    r"^(?:the\s+)?(?P<name>.+?)\s+(?:audience|viewers|readers|listeners|users|buyers|fans|customers|cohort|profile)\s+in\s+(?:this|the)\s+(?:profile|file|view|page|audience)$", re.I)
_CONDITION_TAIL_RX = re.compile(
    r"\s+(?:without\s+paying(?:\s+for\s+it)?|outside\s+(?:of\s+)?theatrical|at\s+home|for\s+free|on\s+demand|"
    r"with\s+ads|ad[- ]free|digitally|online|legally|in\s+(?:the\s+)?(?:last|past|trailing)\s+\d+\s+(?:days|weeks|months|years))\s*$", re.I)
_SERIES_ON_RX = re.compile(
    r"^(?:the\s+)?(?P<name>.+?)\s+(?P<kind>series|show|film|movie|title|podcast|book|game|franchise)\s+on\s+(?P<platform>[A-Za-z0-9+&' .-]{2,40})$", re.I)
_NESTED_RX = re.compile(
    r"\b(?:people|viewers|users|those|folks|subscribers|accounts|anyone|everyone|audiences?|panelists|respondents|members|adults|americans|households|consumers|customers|fans|readers|listeners|players|shoppers|buyers)\s+(?:who|that)\s+"
    r"(?:also\s+)?(?P<act>watch(?:ed|es)?|stream(?:ed|s)?|view(?:ed|s)?|read|reads|listen(?:ed|s)?\s+to|play(?:ed|s)?|bought|buy|purchased?|"
    r"subscribed?\s+to|follow(?:ed|s)?|binge(?:d|s)?|saw|started|drink|drinks|drank|eat|eats|ate|use|uses|used|own|owns|wear|wears|drive|drives|shop\s+at)\s+"
    r"(?P<obj>(?:the\s+)?[A-Za-z0-9][A-Za-z0-9 .&'\-+:!]{1,70}?)"
    r"(?:\s+on\s+(?P<platform>[A-Z][A-Za-z0-9+&' .-]{1,30}?|paramount\+|hulu|netflix|peacock|max|prime video|amazon prime video|disney\+|apple tv\+|tubi|roku))?"
    # a stop word ends the title only when what follows is not more title
    # (2026-10-22: 'Jackass: Best & Last on Paramount+' bound 'Jackass:
    # Best &', 'Emily in Paris' bound 'Emily', 'Only Murders in the
    # Building' bound 'Only Murders'); a capitalized continuation that is
    # not a month, a quarter or a year keeps the title going, and so does a
    # capitalized stop word ('Jackass: Best & Last', 'Doctor Who'); once a
    # platform is named, the next stop word ends it ('on Paramount+ and ...')
    r"(?=\s*[?.,;!]|\s+(?-i:in|during|over|within|since|last|this|between|from|and|or|but|who|that|which|when|while|after|before|also|too)\b"
    r"(?(platform)|(?!\s+(?:the\s+)?(?-i:(?!(?:January|February|March|April|May|June|July|August|September|October|November|December|Q[1-4]|US|UK)\b)[A-Z][A-Za-z0-9'&:!.-]*)))"
    # a named platform ends at the next lowercase word too ('on Netflix watch next' is Netflix)
    r"|(?(platform)\s+(?-i:[a-z])|(?!))|$)", re.I)
_QUALIFIED_AUDIENCE_RX = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9 .&'\-+:!]{1,70}?)\s+(?P<verb>viewers|readers|listeners|players|watchers|buyers|shoppers|users|fans|subscribers|customers)"
    r"(?:\s+on\s+(?P<platform>[A-Za-z0-9+&' .-]{2,40}?))?"
    r"(?:\s*,\s*|\s+(?:in|for|during)\s+)?(?P<window>(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}|q[1-4]\s+\d{4}|\d{4}|(?:last|trailing|past)\s+\d+\s+(?:days|weeks|months))?\s*$", re.I)
_QUOTED_WITH_TYPE_RX = re.compile(
    r"(?<![A-Za-z0-9])[\"\u201c\u201d'\u2018\u2019](?P<name>[^\"\u201c\u201d'\u2018\u2019]{2,60})[\"\u201c\u201d'\u2018\u2019](?![A-Za-z0-9])\s*(?P<kind>webtoon|comic|manga|book|novel|series|show|film|movie|podcast|game|title)?", re.I)
_LIKE_IN_TEXT_RX = re.compile(
    r"\b(?:a|an|the|some|any)\s+[a-z][a-z ]{1,40}?\s+like\s+(?P<name>[A-Za-z0-9][A-Za-z0-9&'+ .-]{1,40}?)(?=[.,;:!?]|\s+(?:and|or|but|who|that|which|so|to)\b|$)", re.I)
_PLATFORM_TAIL_RX = re.compile(
    r"\s+(?:on\s+)?(?:amazon\s+prime\s+video|prime\s+video|netflix|hulu|paramount\+|paramount\s+plus|peacock|hbo\s+max|max|disney\+|apple\s+tv\+|tubi|roku|youtube)\s*$", re.I)
_READ_TAIL_RX = re.compile(r"\s+(?:crossover\s+|cross-?watch\s+)?(?:read|analysis|report|question|ask|breakdown|numbers?)\s*$", re.I)
_BREAKDOWN_RX = re.compile(
    r"\b(?:by|per|across|broken\s+(?:out|down)\s+by|split\s+by|segmented\s+by)\s+(?:platform|service|surface|device|daypart|month|week|day|geography|state|dma|demographic|age|gender|genre|title|creator|channel)s?\b"
    r"|\bas\s+a\s+table\b|\bin\s+a\s+table\b|\btable\s+(?:format|form)\b|\bshow\s+(?:me\s+)?(?:the\s+)?(?:results|it|them)\s+by\b|\bwhat\s+other\s+(?:streaming\s+)?(?:platforms|services)\b", re.I)

_BRAND_NAMED_RX = re.compile(
    r"\b(?:the|a)\s+brand\s+(?:named\s+|called\s+)?(?P<name>[A-Za-z0-9][A-Za-z0-9&'+.-]*(?:\s+[A-Za-z0-9&'+.-]+){0,4})\s*$", re.I)
_AUDIENCE_IN_PROFILE_TEXT_RX = re.compile(
    r"\b(?:for|of|on|about|in)\s+the\s+(?P<name>[A-Z][A-Za-z0-9&'+.:-]*(?:\s+[A-Za-z0-9&'+.:-]+){0,7}?)\s+"
    r"(?P<verb>audience|viewers|readers|listeners|users|buyers|fans|customers|cohort|profile)\s+in\s+(?:this|the)\s+(?:profile|file|view|page|audience)\b", re.I)
_PLATFORM_VENDORS = frozenset(('amazon', 'apple', 'google', 'youtube', 'paramount', 'disney', 'hbo', 'warner', 'nbc', 'peacock', 'roku', 'pluto', 'tubi', 'fox', 'cbs', 'abc'))
_SIZE_AUDIENCE_RX = re.compile(
    r"\b(?:how\s+many|how\s+big\s+is|size\s+of|the\s+size\s+of|number\s+of)\s+(?:the\s+)?(?P<name>[A-Z][A-Za-z0-9&+'. -]{1,40}?|[a-z0-9][a-z0-9&+'. -]{1,40}?)\s+"
    r"(?P<verb>fans|viewers|readers|listeners|players|users|customers|buyers|shoppers|subscribers|owners|drinkers|watchers)\b", re.I)

_KNOWN_CASING = {
    'draftkings': 'DraftKings', 'fanduel': 'FanDuel', 'mountain dew': 'Mountain Dew', 'netflix': 'Netflix',
    'hulu': 'Hulu', 'prime video': 'Prime Video', 'amazon prime video': 'Amazon Prime Video', 'paramount+': 'Paramount+',
    'paramount plus': 'Paramount+', 'youtube': 'YouTube', 'tiktok': 'TikTok', 'hbo max': 'HBO Max', 'max': 'Max',
    'peacock': 'Peacock', 'disney+': 'Disney+', 'apple tv+': 'Apple TV+', 'tubi': 'Tubi', 'espn': 'ESPN', 'nfl': 'NFL',
    'nba': 'NBA', 'mlb': 'MLB', 'ufc': 'UFC', 'wwe': 'WWE', 'ncis': 'NCIS', 'mrbeast': 'MrBeast', 'ishowspeed': 'IShowSpeed',
    'bet': 'BET', 'cnn': 'CNN', 'webtoon': 'WEBTOON', 'lore olympus': 'Lore Olympus', 'mutiny': 'Mutiny',
    'among us': 'Among Us', 'golden axe': 'Golden Axe', 'the office': 'The Office', 'the office us': 'The Office US',
}
_SMALL_WORDS = frozenset(('a', 'an', 'the', 'of', 'and', 'or', 'in', 'on', 'for', 'to', 'at', 'by', 'with', 'vs', 'de', 'la', 'us'))


def _cased(name, casing=None):
    """Library / known casing for a name, else title case that keeps
    acronyms and inner capitals ("NCIS: New York", "iJustine")."""
    s = ' '.join(str(name or '').split()).strip(" ,;:.'\"")
    if not s:
        return ''
    low = s.lower()
    if casing and low in casing:
        return casing[low]
    if low in _KNOWN_CASING:
        return _KNOWN_CASING[low]
    if s != low and any(ch.isupper() for ch in s):
        return s        # the reader typed capitals: keep them
    out = []
    for i, w in enumerate(low.split()):
        if i and w in _SMALL_WORDS and w != 'us':
            out.append(w)
        elif w == 'us':
            out.append('US')
        elif w.rstrip(':,') in _KNOWN_CASING:
            out.append(_KNOWN_CASING[w.rstrip(':,')] + w[len(w.rstrip(':,')):])
        else:
            out.append(w[:1].upper() + w[1:])
    return ' '.join(out)


def is_metric_phrase(s):
    """'Unique', 'unique US viewers per surface', 'watch scary movies':
    a metric, a breakdown or a behavior, never a subject."""
    t = ' '.join(str(s or '').lower().split()).strip(" ,;:.'\"")
    if not t:
        return True
    if _BEHAVIOR_LEAD_RX.match(t):
        return True
    words = [w for w in re.split(r"[^a-z0-9+.']+", t) if w]
    if not words:
        return True
    return all(w in _METRIC_WORDS for w in words)


def wants_breakdown(text):
    """The ask wants a split or a table, not one count."""
    return bool(_BREAKDOWN_RX.search(str(text or '')))


def clean_display(raw, text='', casing=None):
    """The one spelling a reader sees for a subject an extractor or a
    model produced. '' when the raw is a metric, a modifier, a pointer
    at the page, or ordinary words with no name in them."""
    s = ' '.join(str(raw or '').split()).strip(" ,;:.'\"")
    if not s:
        return ''
    if s.lower() in _DEIXIS or s.lower() in ('<page>', 'page'):
        return ''
    m = re.match(r"^(?:re-?r(?:u|eu)n|re-?pull|redo)\s*\((?P<inner>[^)]+)\)\s*$", s, re.I)
    if m:
        inner = m.group('inner').strip()
        # "(the Golden Axe crossover read)": a lowercase article before a capitalized name is an article
        if re.match(r"^the\s+[A-Z]", inner):
            inner = inner[4:]
        s = inner
    if re.match(r"^(?:re-?r(?:u|eu)n|re-?pull|redo)\b", s, re.I):
        return ''
    s = _READ_TAIL_RX.sub('', s).strip(" ,;:.'\"")
    m = _AUDIENCE_IN_PROFILE_RX.match(s)
    if m:
        s = m.group('name')
    m = _LIKE_RX.match(s)
    if m:
        s = m.group('name')
    s = _CONDITION_TAIL_RX.sub('', s).strip(" ,;:.'\"")
    s = re.sub(r"^(?:title|film|movie|show|series|book|podcast|game)\s+[\"\u201c\u201d'\u2018\u2019]?([^\"\u201c\u201d'\u2018\u2019]+?)[\"\u201c\u201d'\u2018\u2019]?$", r"\1", s, flags=re.I)
    # "'Lore Olympus' webtoon": a quoted name followed by its type noun
    s = re.sub(r"^[\"\u201c\u201d'\u2018\u2019]?([^\"\u201c\u201d'\u2018\u2019]{2,60})[\"\u201c\u201d'\u2018\u2019]\s+(?:webtoon|comic|manga|book|novel|series|show|film|movie|podcast|game|title)$", r"\1", s, flags=re.I)
    # "the toys category that is for the brand Lego": the brand named last is the subject
    m = _BRAND_NAMED_RX.search(s)
    if m:
        s = m.group('name').strip()
    m = _SERIES_ON_RX.match(s)
    if m:
        s = m.group('name')
    s = _WRAPPER_LEAD_RX.sub('', s).strip(" ,;:.'\"")
    s = s.strip("\"\u201c\u201d'\u2018\u2019 ")
    s = re.sub(r"^(?:the\s+)?(?:question|ask|read|analysis|report)\s+(?:on|about|of)\s+", '', s, flags=re.I)
    if len(s.split()) > 2:
        pm = _PLATFORM_TAIL_RX.search(s)
        # "Everybody Loves Raymond Amazon Prime Video" drops the platform;
        # "Amazon Prime Video" IS the platform and keeps its whole name
        if pm and pm.start() > 0 and s[:pm.start()].strip().lower() not in _PLATFORM_VENDORS:
            s = (s[:pm.start()] + s[pm.end():]).strip(" ,;:.'\"")
    if not s or is_metric_phrase(s):
        return ''
    low = s.lower()
    if low in _NEVER_LABEL:
        return ''
    # a bare 'the' title keeps its article ("The Office"); other leading articles drop
    if low.startswith('the ') and len(low.split()) <= 3:
        return _cased(s, casing)
    s = re.sub(r"^(?:a|an)\s+", '', s, flags=re.I)
    return _cased(s, casing)


_NEVER_LABEL = frozenset((
    'us', 'usa', 'u.s.', 'america', 'people', 'person', 'year', 'month', 'week', 'day', 'tv',
    'streaming', 'prometheus', 'crosswalk', 'gen pop', 'genpop', 'total universe', 'avid fan',
    'something else', 'never mind', 'yes', 'no', 'ok', 'okay', 'approve', 're-run', 'rerun',
    'the time frame', 'time frame', 'window', 'the window', 'september', 'october', 'november',
    'q1', 'q2', 'q3', 'q4', 'last year', 'this year', 'approve the run'))


def nested_audience(text):
    """'people who watched the Among Us series on Paramount+' ->
    (name, platform, kind). ('', '', '') when the words carry none."""
    m = _NESTED_RX.search(str(text or ''))
    if not m:
        return '', '', ''
    obj = m.group('obj').strip()
    platform = (m.group('platform') or '').strip()
    kind = ''
    sm = _SERIES_ON_RX.match(obj)
    if sm:
        obj, kind, platform = sm.group('name'), sm.group('kind'), platform or sm.group('platform')
    else:
        km = re.match(r"^(?:the\s+)?(?P<name>.+?)\s+(?P<kind>series|show|film|movie|title|podcast|book|game)$", obj, re.I)
        if km:
            obj, kind = km.group('name'), km.group('kind')
    name = clean_display(obj)
    if not name:
        return '', '', ''
    return name, _cased(platform) if platform else '', kind or 'title'


_ACT_NOUN = (('listen', 'listeners'), ('read', 'readers'), ('play', 'players'), ('bought', 'buyers'), ('buy', 'buyers'),
             ('purchas', 'buyers'), ('subscrib', 'subscribers'), ('follow', 'followers'), ('drink', 'drinkers'), ('drank', 'drinkers'),
             ('eat', 'eaters'), ('ate', 'eaters'), ('use', 'users'), ('own', 'owners'), ('wear', 'wearers'), ('drive', 'drivers'),
             ('shop', 'shoppers'))


def nested_audience_verb(text):
    """The audience noun for a nested audience's act: 'people who drink
    Mountain Dew' -> drinkers; watch / stream / view / binge -> viewers."""
    m = _NESTED_RX.search(str(text or ''))
    act = (m.group('act') if m else '') or ''
    act = act.lower()
    for stem, noun in _ACT_NOUN:
        if act.startswith(stem):
            return noun
    return 'viewers'


def qualified_audience(text):
    """'Everybody Loves Raymond viewers on Amazon Prime Video, September
    2026' -> (name, {'verb','platform','window'}); ('', {}) otherwise.
    The whole text must be the audience name (a chip answer, a build
    subject), not a sentence."""
    t = ' '.join(str(text or '').split()).strip(" .!?")
    if not t or len(t) > 140 or ' who ' in t.lower() or '?' in t:
        return '', {}
    m = _QUALIFIED_AUDIENCE_RX.match(t)
    if not m:
        return '', {}
    name = clean_display(m.group('name'))
    if not name or _BEHAVIOR_LEAD_RX.match(m.group('name')):
        return '', {}
    # a follow-up that opens with a discourse or question word is not an
    # audience name ("what about gender age and international viewers" was
    # priced as a $500 read on "What About Gender Age and International",
    # Jay Patel 2026-10-21); it stays with the thread's subject
    if _DISCOURSE_LEAD_RX.match(m.group('name')):
        return '', {}
    q = {'verb': m.group('verb').lower()}
    if m.group('platform'):
        q['platform'] = _cased(m.group('platform'))
    if m.group('window'):
        q['window'] = ' '.join(m.group('window').split())
    return name, q


_PLACE_LIST_RX = re.compile(
    r"\b(?:at|on|across|between|among|from|via|through|in|to|with)\s+"
    r"(?:(?:the\s+)?[\w&'.+-]+,?\s+(?:and\s+|or\s+|&\s+)?){0,5}$", re.I)


def _place_in_list(name, text):
    """True when every mention of `name` in the text sits in a list of
    places after a locative preposition ("shopping at Amazon, Walmart and
    Target", "on Netflix or Hulu"): the places people shop or watch, not
    the audience the ask is about."""
    t = str(text or '')
    found = False
    for m in re.finditer(r"(?<![\w'])" + re.escape(str(name or '')) + r"(?![\w'])", t, re.I):
        found = True
        head = t[max(0, m.start() - 160):m.start()]
        if not _PLACE_LIST_RX.search(head):
            return False
    return found


def _reading_candidates(reading, page, norm, text=''):
    out = []
    if not isinstance(reading, dict):
        return out
    page_n = norm(str(page or '').split(' - ')[0])
    # Two or more brands or platforms that only appear as places after
    # "at", "on", "across" are where the behavior happens, not who it is
    # about (Scott Carlin 2026-10-24: "shopping at Amazon, Walmart and
    # Target" bound Amazon as the subject). They never become candidates;
    # a single place keeps today's reading.
    places = set()
    if text:
        brands = [str(sd.get('as_written') or sd.get('catalog') or '').strip() for sd in (reading.get('subjects') or [])
                  if isinstance(sd, dict) and str(sd.get('kind') or '').lower() in ('brand', 'platform')]
        brands = [b for b in brands if b]
        if len(brands) >= 2:
            listed = [b for b in brands if _place_in_list(b, text)]
            if len(listed) >= 2:
                places = {norm(b) for b in listed}
    for s in (reading.get('subjects') or []):
        if not isinstance(s, dict):
            continue
        aw = str(s.get('as_written') or '').strip()
        cat = str(s.get('catalog') or '').strip()
        kind = str(s.get('kind') or '').strip().lower()
        if aw.lower().strip(" .?!") in _DEIXIS or aw.lower() == '<page>':
            continue
        if kind in ('metric', 'unknown', 'audience', 'cohort') and not cat:
            # 'audience' as the reader's kind for a named audience ("DraftKings users") still names a brand
            if kind != 'audience':
                continue
        name = cat or clean_display(aw)
        if not name:
            continue
        n = norm(name.split(' - ')[0])
        if page_n and (n == page_n or n in page_n or page_n in n):
            continue
        if places and (norm(aw) in places or n in places):
            continue
        k = kind if kind in ('title', 'brand', 'person', 'audience') else 'title'
        out.append((name, k, 0.8 if cat else 0.7))
    return out


_SENTENCE_AUDIENCE_RX = re.compile(
    r"(?<![\w'])(?P<name>[A-Z][\w&'.:!+-]*(?:\s+(?:[A-Z][\w&'.:!+-]*|\d[\w.:-]*|of|the|and|&|de|la))*?)\s+"
    r"(?P<verb>viewers|fans|readers|listeners|players|watchers|buyers|shoppers|users|customers|subscribers|drinkers|owners)\b")
_SENTENCE_STOP = frozenset(('what', 'which', 'who', 'whom', 'how', 'when', 'where', 'why', 'give', 'show', 'tell',
                            'list', 'the', 'a', 'an', 'do', 'does', 'did', 'are', 'is', 'was', 'were', 'among',
                            'us', 'u.s.', 'our', 'their', 'these', 'those', 'all', 'every', 'any', 'some', 'most',
                            'many', 'top', 'other', 'same', 'new', 'total', 'unique', 'monthly', 'weekly', 'daily'))


def sentence_audience(text, casing=None):
    """An audience named inside a sentence: 'What did Landman viewers
    watch next', 'the share of Landman viewers'. The name that recurs
    wins; ties go to the first. ('', '') when none. (2026-10-21, Liz
    Huszarik: the offer said 'The path for Netflix', the open tab,
    for an ask about Landman viewers.)"""
    t = ' '.join(str(text or '').split())
    if not t:
        return '', ''
    seen, order = {}, []
    for m in _SENTENCE_AUDIENCE_RX.finditer(t):
        raw = m.group('name').strip()
        words = raw.split()
        while words and words[0].lower() in _SENTENCE_STOP:
            words = words[1:]
        if not words:
            continue
        raw = ' '.join(words)
        low = raw.lower()
        if low in _SENTENCE_STOP or low in _NEVER_LABEL or is_metric_phrase(raw):
            continue
        name = clean_display(raw, t, casing) or raw
        if not name or len(name) < 2:
            continue
        key = re.sub(r"[^a-z0-9]+", "", name.lower())
        if key not in seen:
            seen[key] = [name, m.group('verb').lower(), 0]
            order.append(key)
        seen[key][2] += 1
    if not order:
        return '', ''
    best = max(order, key=lambda k: (seen[k][2], -order.index(k)))
    return seen[best][0], seen[best][1]


def _request_reading():
    """The Reader's reading for the request in flight, when there is one."""
    try:
        from flask import g as _g
        r = getattr(_g, '_pm_reading', None)
        return r if isinstance(r, dict) else None
    except Exception:
        return None


def bind(text, history=None, page='', reading=None, catalog_names=None, normalize=None):
    """The one binder. Words beat thread beat page."""
    norm = normalize or (lambda s: re.sub(r"[^a-z0-9]+", "", str(s or '').lower()))
    casing = {str(n).lower(): str(n) for n in (catalog_names or []) if n}
    t = ' '.join(str(text or '').split())
    page = str(page or '').strip()
    quals = {}
    if reading is None:
        reading = _request_reading()
    if not t:
        return Subject()
    if definition_turn(t, reading, page):
        return Subject('none', '', '', {}, 'none', 0.0, True)
    # a pointer at the page
    pointed = bool(re.search(r"\b(?:this|that|it|these|those)\b", t, re.I)) and bool(re.search(
        r"\b(?:this|that)\s+(?:audience|profile|data|file|view|page|one|cohort|group)\b|\b(?:the|this)\s+(?:open\s+)?profile\b|\bit\b", t, re.I))
    # 1. an explicit build order names its subject
    eb = explicit_build_subject(t)
    if eb:
        name, q = qualified_audience(eb)
        if name:
            quals.update(q)
        else:
            name = clean_display(eb, t, casing)
        if name:
            return Subject('brand', name, name, quals, 'words', 0.95)
    # 2. a nested audience: people who watched X on Y
    name, platform, kind = nested_audience(t)
    if name:
        if platform:
            quals['platform'] = platform
        quals['verb'] = nested_audience_verb(t)
        return Subject('title' if kind != 'brand' else 'brand', name, name, quals, 'words', 0.9)
    # 2b. the audience named in words with a pointer at the page: "For the
    # Everybody Loves Raymond Amazon Prime Video audience in this profile"
    m = _AUDIENCE_IN_PROFILE_TEXT_RX.search(t)
    if m:
        raw_name = m.group('name').strip()
        pm = _PLATFORM_TAIL_RX.search(raw_name)
        if pm and len(raw_name.split()) > 2:
            quals['platform'] = _cased(pm.group(0).strip(' ,'))
        name = clean_display(raw_name, t, casing)
        if name:
            quals['verb'] = m.group('verb').lower()
            return Subject('audience', name, name, quals, 'words', 0.85, True)
    # 3. a type-led phrase: the film mutiny / the Among Us series on Paramount+
    try:
        from .named_subject import type_led_subject as _tls, ask_condition as _cond
        tl, tk = _tls(t)
    except Exception:
        tl, tk, _cond = '', '', None
    if tl:
        name = clean_display(tl, t, casing)
        if name:
            sm = _SERIES_ON_RX.search(t)
            if sm and norm(clean_display(sm.group('name'))) == norm(name):
                quals['platform'] = _cased(sm.group('platform'))
            c = _cond(t) if callable(_cond) else ''
            if c:
                quals['condition'] = c
            return Subject('title', name, name, quals, 'words', 0.9)
    # 4. a quoted title ("'Lore Olympus' webtoon", "the title 'mutiny'")
    m = _QUOTED_WITH_TYPE_RX.search(t)
    if m:
        name = clean_display(m.group('name'), t, casing)
        if name:
            c = _cond(t) if callable(_cond) else ''
            if c:
                quals['condition'] = c
            return Subject('title', name, name, quals, 'words', 0.88)
    # 5. the whole text is a qualified audience name (a chip answer)
    name, q = qualified_audience(t)
    if name:
        quals.update(q)
        return Subject('title', name, name, quals, 'words', 0.85)
    # 5b. a size ask on a named audience: "how many UFC fans are there"
    m = _SIZE_AUDIENCE_RX.search(t)
    if m:
        name = clean_display(m.group('name'), t, casing)
        if name:
            quals['verb'] = m.group('verb').lower()
            return Subject('audience', name, f"{name} {quals['verb']}", quals, 'words', 0.8)
    # 5c. an audience named inside the sentence: "What did Landman viewers
    # watch next ... the share of Landman viewers" (Liz, 2026-10-21)
    name, verb = sentence_audience(t, casing)
    if name:
        quals['verb'] = verb
        return Subject('audience', name, f"{name} {verb}", quals, 'words', 0.8)
    # 6. the Reader's subjects for these words
    for name, kind, conf in _reading_candidates(reading, page, norm, text=t):
        disp = clean_display(name, t, casing) or name
        if disp:
            return Subject(kind, disp, disp, quals, 'words', conf)
    # 7. "a betting client like draftkings" names DraftKings
    m = _LIKE_IN_TEXT_RX.search(t)
    if m:
        name = clean_display(m.group('name'), t, casing)
        if name:
            return Subject('brand', name, name, quals, 'words', 0.6)
    # 8. the words name nothing: the thread (its bound project first,
    #    even on a fresh thread), then the page
    subs = thread_subjects(history, page) if (history or bound_thread_subject()) else []
    if subs:
        disp = clean_display(subs[0], '', casing) or subs[0]
        if disp:
            return Subject('audience', disp, disp, quals, 'thread', 0.5, pointed)
    if page and pointed:
        return Subject('audience', page, page, quals, 'page', 0.5, True)
    return Subject('none', '', '', quals, 'none', 0.0, pointed)


def display_for(raw, text='', page='', reading=None, catalog_names=None):
    """What a lane shows for the subject it has in hand: the binder's
    display when the words bind one, else the raw cleaned. '' means
    nothing to show (the line is written without a subject)."""
    s = bind(text, None, page, reading, catalog_names)
    if s and s.source == 'words':
        return s.label()
    return clean_display(raw, text)


def clarify_for(text, page=''):
    """The one question when an ask binds no subject: name the thing."""
    return ("Which subject should this be on? Name the show, brand or person "
            "(one line is enough) and I will take it from there.")


def _page_name(ctx):
    try:
        prim = (ctx or {}).get('primary') or {}
        name = str(prim.get('name') or '').strip()
        if name:
            return name
        vc = (ctx or {}).get('view_context') or {}
        return str(vc.get('view_title') or vc.get('subject') or '').strip()
    except Exception:
        return ''


def _same_family(a, b, norm):
    na, nb = norm(str(a or '').split(' - ')[0]), norm(str(b or '').split(' - ')[0])
    return bool(na) and bool(nb) and (na == nb or na in nb or nb in na)


def _current_continuation(text, history, page):
    """The continuation the service classified for this request, or a
    fresh classification outside one."""
    try:
        from flask import g as _g
        c = getattr(_g, '_pm_continuation', None)
        if isinstance(c, dict) and c.get('kind'):
            return c
    except Exception:
        pass
    try:
        from . import continuation as _cont
        return _cont.classify(text, history, None, page)
    except Exception:
        return {'kind': 'new_ask'}


# "this audience / this profile / this data" is the page (view deixis);
# "the film", "the BookTok exposed viewers", "those people" is the thread
_THREAD_ANAPHOR_RX = re.compile(
    r"\b(?:the|that|those)\s+(?:same\s+)?(?:film|movie|series|show|title|book|episode|season|read|cohort|"
    r"(?:[A-Za-z][A-Za-z-]*\s+){0,3}?(?:exposed\s+)?(?:viewers|readers|listeners|buyers|fans|users|people))\b", re.I)


def thread_anaphor(text, history, page=''):
    """The thread's subject when the words point back at it with a
    definite phrase ("the film", "the BookTok exposed viewers", "those
    people") and name nothing of their own; '' otherwise. The page is
    never the answer here: a phrase like this continues the thread."""
    t = ' '.join(str(text or '').split())
    if not t or not history or not _THREAD_ANAPHOR_RX.search(t):
        return ''
    try:
        s = bind(t, None, '')
        if s and s.named and s.source == 'words':
            return ''
    except Exception:
        pass
    for cand in thread_subjects(history, page):
        if cand and not _same_family(cand, page, _norm):
            return cand
    return ''


def bind_or_continue(text, ctx, history, ask_hint=None):
    """The open-screen confirm's first question (I1 + I2):
      {}                          a continuation: never the page confirm
      {'route': 'bind', ...}      the words bind a subject that is not the page
      None                        the binder has no say; the caller's page tests run
    """
    page = _page_name(ctx)
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", str(s or '').lower())
    try:
        # "answer this now: <question>" is a command to answer THIS
        # question (S5): the words are the ask, never a reply to the
        # question the agent asked a turn ago
        answer_now = False
        try:
            from flask import g as _g_an
            answer_now = bool(getattr(_g_an, '_pm_answer_now', False))
        except Exception:
            answer_now = False
        cont = {'kind': 'new_ask'} if answer_now else _current_continuation(text, history, page)
        if str(cont.get('kind') or 'new_ask') != 'new_ask':
            if callable(ask_hint):
                ask_hint(route='screen_bind', outcome='continuation_' + str(cont['kind']), subject=page)
            return {}
        reading = None
        try:
            from flask import g as _g
            reading = getattr(_g, '_pm_reading', None)
        except Exception:
            reading = None
        s = bind(text, history, page, reading)
        if not (s and s.source == 'words' and s.named):
            # "What did the BookTok exposed viewers watch on Prime Video in
            # the 30 days after the film?" after the Love Hypothesis read:
            # the words point back at the thread, so the ask continues on
            # it (the page rides as context), never the which-subject
            # question (Jay Patel 2026-10-21)
            anaphor = thread_anaphor(text, history, page)
            if anaphor:
                try:
                    from flask import g as _g
                    _g._pm_anaphor_subject = anaphor
                except Exception:
                    pass
                if callable(ask_hint):
                    ask_hint(route='screen_bind', outcome='continued_thread_subject', subject=anaphor)
                return {}
        if s and s.source == 'words' and s.kind in ('title', 'brand', 'person', 'audience') and not _same_family(s.display, page, norm):
            # "people who watched the Among Us series on Paramount+" with
            # Paramount+ open binds Among Us; the page rides as the
            # platform qualifier (Casey Pearson 2026-10-20 drew "Which
            # subject: the Golden Axe series on Paramount+ or Paramount+?"
            # and then "On it: reading Paramount+")
            if callable(ask_hint):
                ask_hint(route='screen_bind', outcome='bound_binder_subject', subject=s.display)
            return {'route': 'bind', 'subject': s.display, 'qualifiers': dict(s.qualifiers)}
    except Exception:
        return None
    return None


def names_other_subject(text, ctx):
    """True when the words name a subject that is not the page's (I6):
    the page never answers for it."""
    page = _page_name(ctx)
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", str(s or '').lower())
    try:
        reading = None
        try:
            from flask import g as _g
            reading = getattr(_g, '_pm_reading', None)
        except Exception:
            reading = None
        s = bind(text, None, page, reading)
        return bool(s) and s.source == 'words' and s.kind in ('title', 'brand', 'person') and not _same_family(s.display, page, norm)
    except Exception:
        return False
