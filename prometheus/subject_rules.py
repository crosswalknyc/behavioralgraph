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
"""
from __future__ import annotations

import re

__all__ = ['explicit_build_subject', 'conversational_turn', 'priced_handoff_allowed',
           'page_rejected_in_thread', 'thread_subjects', 'which_subject_payload',
           'CONVERSATIONAL_NOTE', 'strip_lead_negation', 'reading_subject', 'current_reading_subject']

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
        if re.search(r"\b(?:not|no|never|instead of|rather than|other than|besides|without|stop|unclear why|why (?:do|does|did|would) (?:you|it) keep|don t want|do not want|didn t (?:ask|say|mean|want)|did not (?:ask|say|mean|want))\b", win):
            # the negation sits in the same sentence as the page name
            for sent in re.split(r"[.?!]+", str(turn.get('text') or turn.get('content') or '')):
                s = _norm(sent)
                if key in s and re.search(r"\b(?:not|no|never|instead of|rather than|other than|besides|without|stop|unclear why|why (?:do|does|did|would) (?:you|it) keep|don t want|do not want|didn t|did not)\b", s):
                    return True
    return False


_READING_RX = re.compile(r"^On it:\s*reading\s+(?P<what>.+?)\s+now\.?", re.I)
_STARTED_RX = re.compile(r"^Started\s+(?P<what>.+?)\s*(?:\(number|\.|$)", re.I)


def thread_subjects(history, page='', limit=4):
    """The subjects this thread has been about, most recent first: the
    reads Prometheus started ("On it: reading X now"), the runs it
    started (meta.subject), the subjects on agent turn meta. The page
    is left out; duplicates fold case-insensitively."""
    out, seen = [], set()
    _, ptoks = _page_tokens(page)
    pkey = ' '.join(ptoks)

    def _add(name):
        name = ' '.join(str(name or '').split()).strip(' .,;:')
        if not name or len(name) > 90:
            return
        k = _norm(name)
        if not k or k in seen or (pkey and k == pkey):
            return
        seen.add(k)
        out.append(name)

    for turn in reversed(list(history or [])):
        if not isinstance(turn, dict) or str(turn.get('role') or '') == 'user':
            continue
        meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
        if meta.get('subject'):
            _add(meta['subject'])
        txt = ' '.join(str(turn.get('text') or turn.get('content') or '').split())
        m = _READING_RX.match(txt)
        if m:
            what = m.group('what')
            # "reading X and Y now" names two things; the page rides along
            # in the first clause when it was the open profile
            for part in re.split(r"\s+and\s+", what):
                part = part.strip()
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
    return {'success': True, 'action': 'build_profile', 'route_hint': 'interpret',
            'reply': '', 'followups': [], 'offer_deck': False, 'deck_angle': None}


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
