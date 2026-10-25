"""Which subject an ask names, in subject position.

What the user names is never an assumption. Two asks on 2026-10-07
were answered with the open-screen confirm instead of the subject the
user had written down:

- Eliot (Golden State Warriors open): "so is an avid gunna fan more
  likely to buy under armour than Gen Pop especially relative to how
  much more likely they are to buy other brands like Nike and Adidas
  and Puma?" was asked "Do you want this on Golden State Warriors or
  on Gen_Pop?". The ask names Gunna, and the avid cut of Gunna. Gen
  Pop is the baseline every read compares to, never a base.
- Emmet (Brock Mesarich open): "... write me a script for Brock to say
  on his channel to promotoe our product that caters to his
  audience's data?" was asked "... or on Promotoe our Product that
  Caters to?". The ask names the open page by first name; the
  40-character window before "audience" is not a subject.

Rules, in order, for an ask with a profile open:
1. The page named outright (any distinctive page token, first name
   included) binds the page. When a cut of the page's own family is
   named ("avid warriors fans" with the Warriors Total Universe open)
   the cut binds instead.
2. A library subject named in subject position binds that subject;
   its cut when the ask carries a cut cue (avid, female, Gen Z, ...).
3. A phrase that is not a subject (clause glue in the middle, a
   connective at either end, only ordinary words) names nothing.
4. Only when nothing is named does the one-tap confirm fire (Jenna
   2026-10-06: nothing is inferred from the screen).

Comparison targets are not subjects: the phrase is read from the
segment before the first comparison cue ("than Gen Pop", "relative
to ...", "vs Nike"). When the ask opens on the comparison verb
("compare the nike audience to adidas") the subject is the segment
between the verb and the connective.

Pure functions; the caller passes the catalog, the normalizer and
the stop-token set, so this file never imports the app.
"""
from __future__ import annotations

import re

# Comparison cues. The subject sits before the first one.
COMPARE_CUE_RX = re.compile(
    r'\b(compare[ds]?|comparing|comparison|vs\.?|versus|against|'
    r'relative\s+to|compared\s+(?:to|with)|than|overlap)\b', re.I)
# After a leading "compare ...", the subject ends at the connective.
LEADING_CONNECTIVE_RX = re.compile(
    r'\b(?:to|with|against|vs\.?|versus|and)\b', re.I)
# Brand-metric asks about the page mention a brand, not a subject.
METRIC_RX = re.compile(
    r'\b(index(es|ing)?|over.?index(es|ing)?|rank(s|ed|ing)?|'
    r'perform(s|ance|ing)?)\b', re.I)

AUDIENCE_NOUNS = (
    r'audiences?|viewers?|fans?|subscribers?|buyers?|shoppers?|'
    r'listeners?|watchers?|followers?|customers?|consumers?|users?')

NAME_RES = (
    # 'Colin and Samir have a Lexus deal' (Debi Pomerantz,
    # 2026-10-21). The people named before the verb are the subject.
    re.compile(
        r'(?:^|[.!?]\s+)([A-Z][A-Za-z0-9&\'.-]+(?:\s+(?:and|&)\s+'
        r'[A-Z][A-Za-z0-9&\'.-]+)+)\s+(?:have|has|had)\b'),
    # 'profile iq for emily in paris', 'a journey on nike',
    # 'demographics of yellowstone'
    re.compile(
        r'(?:profile(?:\s+iq)?|journey|read|report|data|numbers|'
        r'demo(?:graphic)?s|insights?|audience|breakdown)\s+'
        r'(?:for|on|of|about)\s+([a-z0-9][a-z0-9 .&\'-]{1,60})',
        re.IGNORECASE),
    # 'look at emily in paris', 'pull up nike', 'switch to yellowstone'
    re.compile(
        r'(?:look\s+at|looking\s+at|pull\s+up|switch\s+to|show\s+me|'
        r'open\s+up)\s+([a-z0-9][a-z0-9 .&\'-]{1,60})',
        re.IGNORECASE),
    # 'the yellowstone audience', 'nike buyers', 'an avid gunna fan'
    re.compile(
        r'\b([a-z0-9][a-z0-9 .&\'-]{1,40}?)\s+(?:' + AUDIENCE_NOUNS + r')\b',
        re.IGNORECASE),
)

# Discourse openers and possessives that sit at the edges of a
# subject phrase and are never part of a name.
EDGE_STOP = frozenset((
    'so', 'ok', 'okay', 'well', 'also', 'but', 'just', 'now', 'then',
    'again', 'hey', 'hi', 'hello', 'basically', 'actually', 'curious',
    'wondering', 'quick', 'question', 'his', 'her', 'hers', 'its',
    'whose', 'if', 'an', 'any', 'all', 'some', 'every', 'each',
    'other', 'another', 'own', 'typical', 'average',
    # ask verbs ("how you defined a gunna fan" -> gunna)
    'define', 'defined', 'defining', 'describe', 'explain', 'build',
    'built', 'building', 'make', 'create', 'pull', 'pulled', 'pulling',
    'run', 'running', 'identify', 'find', 'compare', 'target', 'reach',
    'analyze', 'analyse', 'review', 'measure', 'size', 'show', 'showing',
    'profile', 'profiling', 'understand', 'know'))

# Cut cue words leave the phrase before matching ("female gunna fans"
# -> gunna; the cue itself picks the cut in pick()).
_CUE_WORDS_RX = re.compile(
    r"\b(?:avid|casual|super\s?fans?|die[\s-]?hard|hardcore|hard[\s-]?core|"
    r"female|male|women|woman|men|man|ladies|girls|guys|gen\s*[zx]|"
    r"zoomers?|millennials?|boomers?|biggest)\b", re.I)

# Clause glue never sits INSIDE a subject name. "Emily in Paris" and
# "The Big Bang Theory" keep their small words; "promotoe our product
# that caters to his" does not survive this.
MIDDLE_GLUE = frozenset((
    'that', 'which', 'who', 'whom', 'whose', 'to', 'our', 'my',
    'your', 'his', 'her', 'their', 'its', 'this', 'these', 'those',
    'so', 'if', 'when', 'where', 'how', 'what', 'why', 'is', 'are',
    'was', 'were', 'be', 'do', 'does', 'did', 'can', 'could', 'should',
    'would', 'will', 'say', 'buy', 'buys', 'watch', 'like', 'likes',
    'more', 'most', 'less', 'likely', 'than'))

# Cut cues: the phrase in the ask -> the tokens the cut's suffix must
# carry ("Gunna - Avid Fan", "Gunna - Female", "Gunna - Gen Z").
CUT_CUES = (
    (re.compile(r'\b(?:avid|super\s?fans?|die[\s-]?hard|hardcore|'
                r'biggest\s+fans?|hard[\s-]?core)\b', re.I), ('avid', 'fan')),
    (re.compile(r'\b(?:female|women|woman|ladies|girls)\b', re.I), ('female',)),
    (re.compile(r'\b(?:male|men|guys)\b', re.I), ('male',)),
    (re.compile(r'\bgen\s*z\b|\bzoomers?\b', re.I), ('gen', 'z')),
    (re.compile(r'\bmillennials?\b', re.I), ('millennials',)),
    (re.compile(r'\bgen\s*x\b', re.I), ('gen', 'x')),
    (re.compile(r'\bboomers?\b', re.I), ('boomers',)),
)

GEN_POP_RX = re.compile(r'^(?:us\s+)?gen\s?pop(?:ulation)?(?:\s+\d{4})?$')


def is_gen_pop(name, normalize):
    """Gen Pop is the baseline every read compares to, never a base."""
    return bool(GEN_POP_RX.match(normalize(str(name or '').split(' - ')[0])))


EVERYONE_RX = re.compile(
    r'^(?:everyone(?:\s+in\s+the\s+(?:us|u\s?s|united\s+states|country))?|all\s+(?:us\s+)?(?:adults|consumers|people)|'
    r'us\s+(?:adults|consumers|population|people)|the\s+(?:us|whole\s+(?:panel|country|us)|general\s+population|'
    r'us\s+population)|general\s+population|the\s+entire\s+(?:panel|us)|(?:us\s+)?gen\s?pop(?:ulation)?(?:\s+\d{4})?)$')


def is_everyone(name):
    """The whole panel named as an audience ("Everyone in the US", "all US
    adults", "gen pop"): a read on it runs on the panel, never on the
    profile open on the page (2026-10-24)."""
    n = re.sub(r"[^a-z0-9]+", " ", str(name or '').lower()).strip()
    return bool(n) and bool(EVERYONE_RX.match(n))


def subject_segment(text):
    """The part of the ask where its subject sits: before the first
    comparison cue, or between a leading comparison verb and its
    connective."""
    t = str(text or '')
    m = COMPARE_CUE_RX.search(t)
    if not m:
        return t
    if m.start() <= 12 and m.group(1).lower().startswith('compar'):
        tail = t[m.end():]
        c = LEADING_CONNECTIVE_RX.search(tail)
        return tail[:c.start()] if c else tail
    return t[:m.start()]


def _clean_words(phrase, normalize, stop):
    """Stop-strip both edges, keep at most six words, reject clause
    glue in the middle. [] when nothing survives."""
    phrase = _CUE_WORDS_RX.sub(' ', str(phrase or ''))
    words = [w for w in re.split(r'\s+', phrase.strip()) if w]
    edge = set(stop) | EDGE_STOP

    def _n(w):
        return normalize(w)
    while words and (_n(words[0]) in edge or not _n(words[0])):
        words.pop(0)
    while words and (_n(words[-1]) in edge or not _n(words[-1])):
        words.pop()
    if not words or len(words) > 6:
        return []
    middle = [_n(w) for w in words[1:-1]]
    if any(m in MIDDLE_GLUE for m in middle):
        return []
    return words


def phrases(text, normalize, stop):
    """Candidate subject phrases (word lists) in subject position, in
    priority order. Empty for pronoun asks, page-metric asks, and
    asks whose only candidate is glue."""
    t = str(text or '')
    if not t.strip() or METRIC_RX.search(t):
        return []
    seg = subject_segment(t)
    out = []
    for rx in NAME_RES:
        m = rx.search(seg)
        if not m:
            continue
        phrase = re.split(r'[.?!;\n]|\bdate range\b|\bwindow\b',
                          m.group(1))[0]
        words = _clean_words(phrase, normalize, stop)
        if words and words not in out:
            out.append(words)
    return out


def distinct_tokens(s, normalize, stop):
    return {w for w in normalize(str(s or '')).split() if w not in stop}


def cut_cues(text):
    """The cut token-sets the ask carries ('avid gunna fan' -> [{'avid',
    'fan'}])."""
    t = str(text or '')
    return [set(toks) for rx, toks in CUT_CUES if rx.search(t)]


def family_name(entry):
    dn = str((entry or {}).get('display_name') or '').strip()
    if dn:
        return dn.split(' - ')[0].strip()
    return str((entry or {}).get('subject') or '').strip()


def cut_suffix(entry):
    dn = str((entry or {}).get('display_name') or '').strip()
    return dn.split(' - ', 1)[1].strip() if ' - ' in dn else ''


def pick(entries, cues, normalize):
    """Among one family's files: the cut whose suffix carries the most
    cue tokens, else the Total Universe, else the first."""
    if not entries:
        return None
    if cues:
        best, best_score = None, 0
        for e in entries:
            suf = set(normalize(cut_suffix(e)).split())
            if not suf:
                continue
            score = sum(1 for c in cues if c <= suf)
            if score > best_score or (score == best_score and best is not None
                                      and score and len(suf) < len(set(normalize(cut_suffix(best)).split()))):
                best, best_score = e, score
        if best is not None and best_score:
            return best
    for e in entries:
        if not cut_suffix(e):
            return e
    return entries[0]


def named_entry(text, catalog, normalize, stop):
    """The library file the ask names in subject position, cut-aware.
    None when the ask names nothing, names only a non-library subject,
    or names Gen Pop."""
    cands = phrases(text, normalize, stop)
    if not cands:
        return None
    cues = cut_cues(text)
    for words in cands:
        want = distinct_tokens(' '.join(words), normalize, stop)
        if not want:
            continue
        fam = []
        for e in catalog or ():
            if not isinstance(e, dict):
                continue
            name = family_name(e)
            if not name or is_gen_pop(name, normalize):
                continue
            if distinct_tokens(name, normalize, stop) == want:
                fam.append(e)
        if fam:
            return pick(fam, cues, normalize)
    return None


def named_phrase(text, normalize, stop, plausible=None):
    """The first subject-position phrase, title-cased, for a subject
    that may not be in the library yet. '' when nothing plausible."""
    for words in phrases(text, normalize, stop):
        label = ' '.join(
            w if normalize(w) in stop else (w[:1].upper() + w[1:])
            for w in words)
        if plausible is not None and not plausible(label):
            continue
        return label
    return ''


def page_named(text, page, normalize, stop):
    """True when the ask carries a distinctive token of the open page
    (first names count; possessives normalize away), or the page is a
    2-5 letter name written in caps in the ask."""
    pg = str(page or '').split(' - ')[0]
    toks = [w for w in normalize(pg).split()
            if len(w) >= 4 and w not in stop]
    tl = ' ' + normalize(text) + ' '
    if toks and any(f' {w} ' in tl for w in toks):
        return True
    short = [w for w in normalize(pg).split() if w not in stop]
    if len(short) == 1 and 2 <= len(short[0]) <= 5:
        return bool(re.search(r'\b' + re.escape(short[0].upper()) + r'\b',
                              str(text or '')))
    return False


def same_family(entry, page, normalize, stop):
    a = distinct_tokens(family_name(entry), normalize, stop)
    b = distinct_tokens(str(page or '').split(' - ')[0], normalize, stop)
    return bool(a) and bool(b) and (a == b or a <= b or b <= a)


def ask_names_its_audiences(text):
    """True when the ask already names who it is about: a two-cut
    request, or a total-universe cut named alongside another audience
    (Casey Pearson, 2026-09-29, was asked twice whether she meant the
    open Paramount+ profile)."""
    t = str(text or '')
    if re.search(r"\b(two|both)\b.{0,80}\b(cuts?|audiences?|views?)\b", t, re.I):
        return True
    has_tu = bool(re.search(
        r"\btotal universe\b|\bsubscribers active on streaming\b", t, re.I))
    return has_tu and bool(re.search(r"\band\b", t, re.I))


def screen_bind_verdict(text, page, base, page_key, normalize, stop):
    """page | confirm - what the open page is to this ask (Jenna
    2026-10-06: nothing is inferred from the screen). The page binds
    silently only when the ask names the page's own subject outright;
    a different file resolved in the page's own family (cut vs parent)
    still confirms; everything else confirms."""
    if page_named(text, page, normalize, stop):
        if base and str(base.get('source') or '') == 'catalog' \
                and str(base.get('s3_key') or '') != str(page_key or ''):
            return 'confirm'
        return 'page'
    # A topic-only follow-up ("what about potential brand partnerships",
    # "how old is this audience") carries no word that could be another
    # subject, so the open page is the only thing it can mean (2026-10-12,
    # eriley re-sent the question after the confirm). Still confirms when
    # a catalog file outside the page's family resolved for the ask.
    if topic_only(text) and not (
            base and str(base.get('source') or '') == 'catalog'
            and str(base.get('s3_key') or '') != str(page_key or '')):
        return 'page'
    return 'confirm'


def thread_subject(history, normalize, stop, plausible=None, consumption=None, limit=16):
    """The subject the thread is about, newest first: an agent turn that
    recorded the subject it answered on (``subject`` / ``profile`` on
    the turn or its meta), else the most recent user turn that names
    one. '' when the thread names nothing. This is what lets an ask
    run with no page open (2026-10-12, Jenna: never page dependent by
    default): the thread, not the screen, is the default anchor."""
    turns = [t for t in (history or []) if isinstance(t, dict)]
    for t in reversed(turns[-limit:]):
        role = str(t.get('role') or '').lower()
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        if role in ('agent', 'assistant'):
            for k in ('subject', 'profile'):
                v = str(t.get(k) or meta.get(k) or '').strip()
                if v and (plausible is None or plausible(v)):
                    return v
            continue
        if role != 'user':
            continue
        txt = str(t.get('text') or t.get('content') or '')
        if not txt:
            continue
        named = ''
        if consumption is not None:
            try:
                named = str(consumption(txt) or '').strip()
            except Exception:
                named = ''
        if not named:
            try:
                named = str(mentioned_subject(txt, '', normalize, stop, plausible) or '').strip()
            except Exception:
                named = ''
        if named:
            m = re.search(r"\b(?:The|A|An)\s+" + re.escape(named) + r"\b", txt, re.I)
            return m.group(0) if m else named
    return ''


def topic_only(text):
    """True when every meaningful word of the ask is ordinary English or
    topic vocabulary (brand partnerships, pricing, demographics): the
    ask names nothing that could be a subject."""
    try:
        from prometheus import referents as _refs
        common, topic = _refs._COMMON_WORDS, _refs._TOPIC_WORDS
    except Exception:
        return False
    toks = [w for w in re.findall(r"[A-Za-z][A-Za-z'+&.-]*", str(text or ''))]
    low = [w.lower().strip(".'") for w in toks]
    content = [w for w in low if len(w) >= 3]
    if not content:
        return False
    if any(w[:1].isupper() and i > 0 for i, w in enumerate(toks)):
        return False   # a capitalized word mid-sentence can be a name
    return all(w in common or w in topic for w in content)


# Concert / live-event universes (2026-10-07 Jenna, Eliot's "people who
# have attended a Gunna concert"): a ticket-buyer universe named by
# the artist. The engine keys BRAND INPUT on the ticketing pages.
CONCERT_GOER_RX = re.compile(
    r"(?:people|those|fans|anyone|users|folks|consumers)?\s*(?:who|that)?\s*"
    r"(?:have\s+|had\s+|has\s+)?(?:attended|went\s+to|been\s+to|saw|seen|"
    r"bought\s+tickets?\s+(?:to|for)|purchased\s+tickets?\s+(?:to|for)|"
    r"got\s+tickets?\s+(?:to|for))\s+(?:an?\s+|the\s+)?"
    r"(?P<artist>[A-Z][\w.&'-]*(?:\s+[A-Z][\w.&'-]*){0,3}|[\w.&'-]+)"
    r"(?:'s)?\s+(?:concerts?|shows?|tour|live\s+shows?|gigs?|performances?)\b",
    re.I)
CONCERT_LABEL_RX = re.compile(
    r"^(?P<artist>.+?)\s+(?:concert|show|tour)\s*(?:goers?|attendees?|"
    r"ticket\s*(?:buyers?|holders?))$", re.I)


def concert_goer_artist(text):
    """The artist named in a concert-attendance ask or label
    ('people who have attended a Gunna concert' -> 'Gunna', 'Gunna
    Concert Goers' -> 'Gunna'); '' otherwise."""
    t = ' '.join(str(text or '').split())
    m = CONCERT_LABEL_RX.match(t)
    if m:
        return m.group('artist').strip(" '\"")
    m = CONCERT_GOER_RX.search(t)
    if not m:
        return ''
    artist = m.group('artist').strip(" '\"")
    if artist.lower() in ('a', 'an', 'the', 'their', 'his', 'her', 'this',
                          'that', 'live', 'any', 'some'):
        return ''
    return artist


def concert_goer_label(artist):
    return f"{str(artist or '').strip()} Concert Goers".strip()


# ---------------------------------------------------------------------
# Does the ask name ANY subject of its own? (2026-10-08, Jenna)
#
# "how many people read the walsh family book series in the us last
# year?" was asked "Do you want this on Reba McEntire (open on your
# screen)?" with Reba open. Jenna, verbatim: "it should only default
# to think it is the open profile if you say something without
# specifically mentioning a subject. then it could ask. but this
# clearly states the ask and that it is firmly NOT reba."
#
# The subject-position phrases above catch "profile for X", "look at
# X", "X fans". This pass catches the rest: "viewers of X", a quoted
# title, a typed noun phrase ("the X series"), the object of a
# consumption verb when a generic population is counted ("how many
# people read X"), a proper-noun run in the user's own casing.
#
# Three shapes keep the page as the subject, with any named brand an
# attribute of the ask: the page named anywhere in the text; deixis
# to the audience on screen ("what do they buy at Target"); a share /
# percent / index ask with no population noun ("what share use Prime
# Video" is a share of the audience on screen).

AUDIENCE_DEIXIS_RX = re.compile(
    r"\b(?:they|them|their|theirs|themselves|these|those|"
    r"th(?:is|at|e)\s+(?:audience|group|cohort|profile|crowd|base|"
    r"fan\s?base|universe|segment|cut|file|people|folks|fans|viewers|"
    r"subscribers|readers|listeners|shoppers|buyers|users|customers)|"
    r"its|it|he|she|him|his|hers?|everyone\s+here|people\s+here)\b", re.I)
PAGE_METRIC_RX = re.compile(
    r"(?:\bshare\b|\bpercent(?:age)?\b|%|\bindex(?:es|ed|ing)?\b|"
    r"\bover.?index|\bunder.?index|\brank(?:s|ed|ing)?\b|\bskews?\b|"
    r"\bpenetration\b|\bhow many of\b|\bwhat portion\b|\bwhat fraction\b)", re.I)
POPULATION_RX = re.compile(
    r"\b(?:people|persons|americans|adults|households|folks|consumers|"
    r"viewers|readers|listeners|users|shoppers|buyers|subscribers|"
    r"gamers|players|fans|kids|teens|parents|moms|dads|men|women|"
    r"in the (?:us|u\.s\.|united states|country|states)|nationally|"
    r"nationwide|us\s+(?:adults|audience|viewers|readers))\b", re.I)

CONSUME_VERB_RX = (
    r"(?:read|reads|watch|watched|watches|listen(?:ed|s)?\s+to|"
    r"stream|streamed|streams|play|played|plays|view|viewed|views|"
    r"tuned?\s+in(?:to)?|binged?|bought|buy|buys|purchased?|"
    r"download(?:ed|s)?|subscribed?\s+to|subscribes\s+to|follow|"
    r"followed|follows|visit|visited|visits|attend(?:ed|s)?|"
    r"shop(?:ped|s)?\s+at|order(?:ed|s)?\s+from|eat|ate|eats\s+at|"
    r"drink|drank|drinks|wear|wore|wears|drive|drove|drives|use|used|"
    r"uses|saw|see|seen|heard|hear|searched?\s+for)")
_OBJECT_END = (
    r"(?=\s+(?:in|on|across|within|during|over|since|last|this|past|"
    r"each|every|per|monthly|weekly|yearly|by|between|from|for|"
    r"so far|to date|and|or|vs|versus|than|compared|months?|weeks?|"
    r"years?|days?|quarters?|also|too|still|ever|never|then|who|that|"
    r"which|when|while|but|because|if|as\s+well|is|are|was|were|do|"
    r"does|did|have|has|had|will|would|can|could|should|"
    + CONSUME_VERB_RX[3:-1] + r")\b|\s*[?.!,;]|$)")
_CONSUME_OBJECT_RX = re.compile(
    r"\b" + CONSUME_VERB_RX +
    r"\s+(?P<obj>(?:the\s+|a\s+|an\s+)?[A-Za-z0-9][A-Za-z0-9 .&'\-+:!]{1,70}?)"
    + _OBJECT_END, re.I)
_OF_SUBJECT_RX = re.compile(
    r"\b(?:viewers|fans|readers|listeners|buyers|shoppers|subscribers|"
    r"users|customers|players|audience|followers|owners|drivers|guests|"
    r"members)\s+of\s+(?P<obj>(?:the\s+)?[A-Za-z0-9][A-Za-z0-9 .&'\-+:!]{1,70}?)"
    + _OBJECT_END, re.I)

# "how popular is godslap", "who is Gunna", "tell me about Chime": the
# thing asked about sits right after the opener.
_ABOUT_SUBJECT_RX = re.compile(
    r"\b(?:how\s+(?:popular|big|large|famous|successful|well[- ]known|"
    r"mainstream|niche)\s+(?:is|are|was|were)|who\s+(?:is|are|was|were)|"
    r"tell\s+me\s+about|what\s+about|how\s+about|know\s+about)\s+"
    r"(?P<obj>(?:the\s+)?[A-Za-z0-9][A-Za-z0-9 .&'\-+:!]{1,60}?)" + _OBJECT_END, re.I)
_METRIC_LEAD = frozenset((
    'biggest', 'largest', 'best', 'top', 'most', 'least', 'median', 'average',
    'mean', 'total', 'overall', 'typical', 'main', 'primary', 'key',
    'current', 'latest', 'newest', 'highest', 'lowest', 'fastest'))

TYPE_NOUNS = (
    r"series|shows?|books?|novels?|films?|movies?|titles?|franchise|podcasts?|"
    r"brands?|apps?|games?|channels?|networks?|albums?|tours?|teams?|"
    r"leagues?|magazines?|newsletters?|websites?|sites?|stores?|chains?|"
    r"restaurants?|services?|platforms?|labels?|studios?|trilogy|saga|"
    r"docuseries|documentary|musical|comics?|manga|anime|cartoons?|"
    r"sitcoms?|dramas?|reality\s+show|talk\s+show|radio\s+show|band|"
    r"artist|author|retailer|airline|hotel|resort|casino|cruise\s+line|"
    r"university|college|charity|nonprofit|company|startup|product|"
    r"device|sneakers?|toy|video\s+game|board\s+game|sportsbook|bank|"
    r"credit\s+card|insurer|lender|exchange|fund|etf|cereal|soda|beer|"
    r"wine|whiskey|vodka|tequila|coffee|energy\s+drink|supplement|"
    r"skincare\s+line|fragrance|perfume|clothing\s+line|fashion\s+house")
_TYPED_NP_RX = re.compile(
    r"\b(?:the|that)\s+(?P<np>(?:[A-Za-z0-9][A-Za-z0-9&'.\-]*\s+){1,5}"
    r"(?:" + TYPE_NOUNS + r"))\b", re.I)
# "the film mutiny", "the show gilmore girls", "the book 'the kiss
# quotient'": the type noun LEADS and the name follows (2026-10-20,
# Jay Patel: "where people watched the film mutiny" with Prime Video
# open drew "Do you want this on Amazon Prime Video?"). The reader said
# what kind of thing it is, so the name needs no capital letter.
_CARRIAGE_END = (
    r"(?=\s+(?:streaming|available|airing|playing|showing|carried|"
    r"offered|released|premier(?:ed|es|ing)|dropp(?:ed|ing)|landed|"
    r"out|now|without|with|while|after|before|instead|outside|"
    r"beyond|through|via|at|among)\b|"
)
_TYPE_LED_Q_RX = re.compile(
    r"\b(?:the|that|this)\s+(?P<kind>" + TYPE_NOUNS + r")\s+"
    r"(?:called\s+|titled\s+|named\s+)?"
    r"[\"\u201c\u201d'\u2018\u2019](?P<name>[^\"\u201c\u201d'\u2018\u2019]{2,60})"
    r"[\"\u201c\u201d'\u2018\u2019]", re.I)
_TYPE_LED_RX = re.compile(
    r"\b(?:the|that|this)\s+(?P<kind>" + TYPE_NOUNS + r")\s+"
    r"(?:called\s+|titled\s+|named\s+)?"
    r"[\"\u201c\u201d'\u2018\u2019]?"
    r"(?!(?:and|or|but|nor|vs|versus|than|that|which|who|whose|is|are|was|were|"
    r"in|on|of|for|to|with|without|by|at|from|as|if|so|then|there|here|"
    r"it|its|they|them|we|you|he|she|his|her|their|our|your|my)\b)"
    r"(?P<name>(?:the\s+|a\s+|an\s+)?[A-Za-z0-9][A-Za-z0-9 .&'\-+:!]{1,70}?)"
    r"[\"\u201c\u201d'\u2018\u2019]?"
    + _CARRIAGE_END + _OBJECT_END[3:], re.I)
_TYPE_WORD_RX = re.compile(r"^(?:" + TYPE_NOUNS + r")$", re.I)


def type_led_subject(text):
    """The (name, kind) a type-led phrase names: "the film mutiny" ->
    ('Mutiny', 'film'), "that show gilmore girls" -> ('Gilmore Girls',
    'show'). ('', '') when the text carries none. Quotes around the name
    are dropped; a leading "the" inside the name stays ("the book the
    kiss quotient" -> 'The Kiss Quotient')."""
    t = str(text or '')
    hits = list(_TYPE_LED_Q_RX.finditer(t)) or list(_TYPE_LED_RX.finditer(t))
    for m in hits:
        raw = m.group('name').strip(" ,;:.\"\u201c\u201d'\u2018\u2019")
        words = [w for w in raw.split() if w]
        if not words:
            continue
        lead_the = words[0].lower() == 'the' and len(words) > 1
        core = words[1:] if (words[0].lower() in ('a', 'an') or lead_the) else words
        if not core or len(core) > 7 or core[0].lower() in _METRIC_LEAD:
            continue
        if _TYPE_WORD_RX.match(core[0]) and len(core) == 1:
            continue    # "the film series" names no title
        label = _title(core)
        if lead_the:
            label = 'The ' + label
        if label.lower() in _NEVER_SUBJECT or raw.lower() in _NEVER_SUBJECT:
            continue
        kind = re.sub(r"\s+", " ", m.group('kind').lower())
        return label, kind
    return '', ''


def names_in_structure(name, text):
    """True when ``text`` names ``name`` by structure, so the name needs
    no capital letter: a type-led phrase ("the film mutiny"), the thing
    a population consumes ("where people watched mutiny", "who reads
    lore olympus"), or a quoted title. The open page may then never be
    offered in its place."""
    n = str(name or '').strip()
    t = str(text or '')
    if not n or not t:
        return False
    tl, _k = type_led_subject(t)
    if tl and _fold(tl) == _fold(n):
        return True
    for m in _QUOTED_RX.finditer(t):
        if _fold(m.group(1)) == _fold(n) or _fold_the(m.group(1)) == _fold_the(n):
            return True
    population = bool(POPULATION_RX.search(t)) or bool(
        re.match(r"\s*(?:where|who|when|how\s+often)(?:'s|\s+is|\s+are|\s+all|\s+do|\s+did|\s+does)?\s+", t, re.I))
    if population:
        for m in _CONSUME_OBJECT_RX.finditer(t):
            obj = re.sub(r"^(?:the|a|an)\s+", "", m.group('obj').strip(), flags=re.I)
            obj = re.sub(r"^(?:" + TYPE_NOUNS + r")\s+", "", obj, flags=re.I)
            if obj and (_fold(obj) == _fold(n) or _fold_the(obj) == _fold_the(n)):
                return True
    return False


# The condition a consumption ask puts on the count ("without paying
# for it", "outside of theatrical", "at home"). It rides the offer's
# sentence in plain words, never the subject's name (2026-10-20, Jay
# Patel: the priced read offered "Title 'mutiny' Without Paying").
_CONDITION_RX = re.compile(
    r"\b(?P<cond>without\s+(?:paying|a\s+subscription|subscribing|an?\s+account|ads?)(?:\s+for\s+(?:it|them))?|"
    r"outside\s+(?:of\s+)?(?:the\s+|a\s+)?theat(?:er|re|rical)s?(?:\s+release)?|"
    r"(?:not\s+)?in\s+(?:the\s+|a\s+)?theat(?:er|re)s?|at\s+home|for\s+free|free\s+with\s+ads|"
    r"on\s+(?:streaming|demand|digital)|digitally|online|legally|with\s+ads|ad[- ]free|"
    r"on\s+(?:a\s+)?(?:subscription|rental|purchase))\b", re.I)
_CONDITION_PLAIN = {
    'outside of theatrical': 'outside a theater', 'outside theatrical': 'outside a theater',
    'outside of the theatrical': 'outside a theater', 'outside of theaters': 'outside a theater',
    'outside theaters': 'outside a theater', 'outside of theatres': 'outside a theater',
    'outside of the theater': 'outside a theater', 'not in theaters': 'outside a theater',
    'on demand': 'on demand', 'digitally': 'on a digital service', 'legally': 'on a licensed service',
}


def ask_condition(text):
    """The plain-words condition a consumption ask carries, or ''.
    "how many people watched the title 'mutiny' without paying for it?"
    -> 'without paying for it'; "...outside of theatrical and where did
    they watch it" -> 'outside a theater'."""
    m = _CONDITION_RX.search(str(text or ''))
    if not m:
        return ''
    cond = re.sub(r"\s+", " ", m.group('cond').strip().lower())
    return _CONDITION_PLAIN.get(cond, cond)


def strip_condition(text):
    """The ask with its condition clause taken out, so a name extractor
    never swallows it."""
    return _CONDITION_RX.sub(' ', str(text or ''))


def _fold(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or '').lower())


def _fold_the(s):
    return _fold(re.sub(r"^\s*the\s+", "", str(s or ''), flags=re.I))
_QUOTED_RX = re.compile(
    r"[\"\u201c\u201d'\u2018\u2019]([^\"\u201c\u201d'\u2018\u2019]{2,60})"
    r"[\"\u201c\u201d'\u2018\u2019]")
_CAP_RUN_RX = re.compile(
    r"\b([A-Z][A-Za-z0-9&'.\-]+(?:\s+(?:of|the|and|&|de|la|von|van|du|"
    r"for|to)\s+[A-Z][A-Za-z0-9&'.\-]+|\s+[A-Z][A-Za-z0-9&'.\-]+){1,5})\b")
_CAP_SINGLE_RX = re.compile(r"(?<![.?!]\s)(?<!^)\b([A-Z][a-z0-9&'.\-]{3,})\b")
_NEVER_SUBJECT = frozenset((
    'us', 'usa', 'u.s.', 'america', 'american', 'americans', 'united states',
    'the us', 'the united states', 'people', 'person', 'year', 'month',
    'week', 'day', 'last year', 'this year', 'q1', 'q2', 'q3', 'q4',
    'january', 'february', 'march', 'april', 'may', 'june', 'july',
    'august', 'september', 'october', 'november', 'december', 'monday',
    'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
    'tv', 'streaming', 'prometheus', 'crosswalk', 'gen pop', 'genpop',
    'gen z', 'gen x', 'millennials', 'boomers', 'total universe',
    'avid fan', 'avid fans', 'csv', 'pdf', 'deck', 'profile', 'profile iq',
    'subscriber iq', 'digital journey iq', 'trends iq', 'what', 'how',
    'who', 'where', 'when', 'why', 'which', 'something else', 'never mind'))
_SMALL = {'a', 'an', 'the', 'of', 'and', 'or', 'in', 'on', 'for', 'to',
          'at', 'by', 'with', 'vs', 'de', 'la'}
# Sentence openers a capitalized run can start on ("Is Heated Rivalry
# popular?"): never part of a name.
_LEAD_STRIP = frozenset((
    'is', 'are', 'was', 'were', 'do', 'does', 'did', 'has', 'have', 'had',
    'can', 'could', 'will', 'would', 'should', 'may', 'might', 'how', 'what',
    'who', 'which', 'when', 'where', 'why', 'compare', 'show', 'give',
    'tell', 'pull', 'run', 'build', 'does', 'please', 'hey', 'hi', 'ok',
    'okay', 'so', 'and', 'but', 'or', 'if', 'then', 'also', 'now', 'today'))


def _title(words):
    out = []
    for i, w in enumerate(words):
        if w.lower() in _SMALL and i != 0:
            out.append(w.lower())
        elif w.isupper() and len(w) <= 5:
            out.append(w)
        elif any(ch.isupper() for ch in w[1:]):
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return ' '.join(out)


def has_audience_deixis(text):
    """The ask points at the audience on screen ("what do they buy at
    Target"): the page is the subject, the named brand an attribute."""
    return bool(AUDIENCE_DEIXIS_RX.search(str(text or '')))


def page_mentioned(text, page, normalize, stop):
    """Any distinctive page token appears in the ask."""
    page_d = distinct_tokens(page or '', normalize, stop)
    if not page_d:
        return False
    text_d = set(normalize(str(text or '')).split())
    return bool(page_d & text_d)


def mentioned_subject(text, page, normalize, stop, plausible=None):
    """A subject the ask names that is NOT the open page, or '' when the
    ask names nothing of its own (then, and only then, the page may be
    offered). '' as well when the page is named, when the ask points
    at the audience on screen, or when it is a share / percent / index
    ask with no population noun."""
    t = str(text or '').strip()
    if not t:
        return ''
    if page_mentioned(t, page, normalize, stop):
        return ''
    if has_audience_deixis(t):
        return ''
    # "who watches love island?": the interrogative is the population
    # (2026-10-12; the page was offered for a question that named its title)
    population = bool(POPULATION_RX.search(t)) or bool(
        re.match(r"\s*who(?:'s|\s+is|\s+are|\s+all)?\s+" + CONSUME_VERB_RX + r"\b", t, re.I))
    if PAGE_METRIC_RX.search(t) and not population:
        return ''
    page_d = distinct_tokens(page or '', normalize, stop)
    cands = []
    _tl, _tk = type_led_subject(t)
    if _tl:
        cands.append(_tl)      # "the film mutiny": named by its kind, any case
    for m in _OF_SUBJECT_RX.finditer(t):
        cands.append(m.group('obj'))
    for m in _ABOUT_SUBJECT_RX.finditer(t):
        cands.append(m.group('obj'))
    quoted = set()
    for m in _QUOTED_RX.finditer(t):
        cands.append(m.group(1))
        quoted.add(re.sub(r"\s+", " ", m.group(1)).strip(" ,;:.'\"").lower())
    for m in _TYPED_NP_RX.finditer(t):
        cands.append(m.group('np'))
    if population:
        for m in _CONSUME_OBJECT_RX.finditer(t):
            cands.append(m.group('obj'))
    for rx in (_CAP_RUN_RX, _CAP_SINGLE_RX):
        for m in rx.finditer(t):
            # "the Bear" in the user's text: the article is the title's
            lead = 'the ' if re.search(r"\b[Tt]he\s+$", t[:m.start(1)]) else ''
            cands.append(lead + m.group(1))
    for raw in cands:
        raw = re.sub(r"\s+", " ", str(raw or '')).strip(" ,;:.'\"")
        was_quoted = raw.lower() in quoted
        if not raw or len(raw) > 80:
            continue
        words = [w for w in raw.split() if w]
        while words and words[0].lower() in _LEAD_STRIP:
            words.pop(0)
        lead_the = bool(words) and words[0].lower() == 'the'
        core = words[1:] if words and words[0].lower() in ('the', 'a', 'an') else words
        if not core or len(core) > 7 or core[0].lower() in _METRIC_LEAD:
            continue
        # a type noun that leads the object is the kind, not the name:
        # "the film mutiny" -> Mutiny (2026-10-20, Jay Patel)
        if len(core) > 1 and _TYPE_WORD_RX.match(core[0]):
            core = core[1:]
            lead_the = core[0].lower() == 'the' and len(core) > 1
            if lead_the:
                core = core[1:]
        label = _title(core)
        if lead_the and (len(core) == 1 or was_quoted):
            label = 'The ' + label        # "the office" -> The Office; a quoted title is verbatim
        if label.lower() in _NEVER_SUBJECT or raw.lower() in _NEVER_SUBJECT:
            continue
        named_d = distinct_tokens(label, normalize, stop)
        if not named_d or (named_d & page_d) or is_gen_pop(label, normalize):
            continue
        if plausible is None or plausible(label):
            return label
        if lead_the and plausible('The ' + label):
            return 'The ' + label
    return ''


def fuzzy_catalog_subject(text, catalog, *, normalize, typo_eq, expand_alias,
                          generic_tokens, extra_tokens=None):
    """The single library subject the ask nearly names, or None.

    Every distinctive token of the subject must appear in the ask
    either exactly or one typo away, at least one match must have
    needed the typo tolerance (exact matches are upstream), and the
    fuzzy hit must be unique across the library: two candidates is an
    ambiguity, not a bind. A TU and its cuts are one candidate (the TU
    file wins).

    A word the reader wrote that is, exactly, a token of some library
    subject is spoken for: it never serves as a typo of another
    subject's token (2026-10-13, Brock Mesarich open on screen; "Brock"
    is not a typo of "Block", and the reader was asked to choose).
    """
    base_tokens = set(normalize(text).split())
    if extra_tokens:
        base_tokens |= set(extra_tokens)
    q_tokens = expand_alias(base_tokens, text)
    if not q_tokens:
        return None
    rows = []
    for entry in catalog or ():
        subj = str(family_name(entry) or entry.get('subject')
                   or entry.get('display_name') or '').strip()
        if is_gen_pop(subj, normalize):
            continue
        st = [w for w in normalize(subj).split() if w not in generic_tokens]
        if not st or sum(len(w) for w in st) < 5 or not any(len(w) >= 5 for w in st):
            continue
        rows.append((entry, subj, st))
    spoken_for = {w for _e, _s, st in rows for w in st if w in q_tokens and len(w) >= 4}
    hits = {}
    for entry, subj, st in rows:
        needed_typo = False
        ok = True
        for w in st:
            if w in q_tokens:
                continue
            tw = next((qt for qt in q_tokens
                       if qt not in spoken_for and typo_eq(w, qt)), None)
            if tw is None:
                ok = False
                break
            needed_typo = True
        # alias expansion counts as a qualifying near-miss too: 'kuwtk'
        # matches every token exactly AFTER expansion, and the exact
        # tier upstream never saw those tokens
        needed_alias = ok and not set(st) <= base_tokens
        if ok and (needed_typo or needed_alias):
            hk = normalize(subj)
            if hk not in hits or ' - ' not in str(entry.get('display_name') or ''):
                hits[hk] = {'subject': subj, 's3_key': str(entry.get('s3_key') or '')}
    if len(hits) == 1:
        return next(iter(hits.values()))
    return None


# ---------------------------------------------------------------------------
# The open page, continued (2026-10-13 audit)
# ---------------------------------------------------------------------------
# A follow-up that points at what is on screen ("these insights", "this
# audience", "the numbers above") is about the page; so is a subject-less
# follow-up in a thread whose last answer landed on the page; so is an
# ask that names a brand sitting in the open profile's rows. Each of
# these drew "Do you want this on X (open on your screen)?" in the
# 2026-10-07 to 10-12 ask log (Eriley 10 times in one session,
# egoldfarb 3 times) and was re-asked.
DEIXIS_RX = re.compile(
    r"\b(?:this|these|that|those)\s+(?:data|screen|page|view|profile|"
    r"audience|cohort|read|analysis|insights?|findings?|numbers?|figures?|"
    r"stats?|results?|brands?|chart|table|report|dashboard|rows?|indexes|"
    r"indices|list|breakdown|file|output|answer)\b"
    r"|\bon\s+(?:this|the)\s+screen\b"
    r"|\b(?:the\s+)?(?:numbers|data|results|insights|figures)\s+"
    r"(?:here|above|below|on\s+screen)\b"
    r"|\bwhat\s+you\s+(?:just\s+)?(?:said|showed|sent|gave|wrote)\b"
    r"|\bwhy\s+does\s+(?:this|that|it)\s+matter\b"
    r"|\bwhat\s+(?:does|do)\s+(?:this|that|these|it)\s+(?:mean|tell|say)\b",
    re.I)

PLATFORM_WORDS = frozenset((
    'youtube', 'instagram', 'tiktok', 'facebook', 'twitter', 'x', 'snapchat',
    'spotify', 'twitch', 'linkedin', 'pinterest', 'reddit', 'threads',
    'substack', 'patreon', 'discord', 'kick', 'rumble', 'imdb', 'wikipedia',
    'google', 'bing', 'apple podcasts', 'soundcloud', 'bandcamp'))

_PLATFORM_REF_RX = (
    r"\b{p}\s*(?:channel|link|page|account|handle|profile|video|url|post|"
    r"feed|clip|short|reel|stream|bio|followers?|subscribers?|views?|"
    r"comments?|live)s?\b|\b(?:on|via|from|through|his|her|their|my|the)\s+{p}\b"
    r"|{p}\.com|@\w+\s+on\s+{p}\b")


def view_deixis(text):
    """The ask points at what is on screen."""
    return bool(DEIXIS_RX.search(str(text or '')))


def platform_reference(text, candidate):
    """``candidate`` is a platform named as a channel, link, handle or
    surface in the ask ("his youtube channel link", "on TikTok"), not a
    subject of its own. A platform the reader asks ABOUT ("who uses
    YouTube") is still a subject."""
    c = str(candidate or '').strip().lower()
    if c not in PLATFORM_WORDS:
        return False
    rx = re.compile(_PLATFORM_REF_RX.format(p=re.escape(c)), re.I)
    return bool(rx.search(str(text or '')))


def alternative_ok(name, text, in_library, plausible=None):
    """May ``name`` be offered or bound as the other subject of an ask?
    A library subject qualifies unless the ask only names it as a
    platform surface. Anything else must be written in the ask the way
    a name is written (a capitalized run the reader typed) and pass the
    plausibility check: "Age Or Income" title-cased out of lowercase
    prose and "YouTube" from "his youtube channel link" both drew a
    chip on 2026-10-07."""
    n = str(name or '').strip()
    if not n:
        return False
    if platform_reference(text, n):
        return False
    if in_library:
        return True
    if plausible is not None:
        try:
            if not plausible(n):
                return False
        except Exception:
            pass
    t = str(text or '')
    if n.lower() in PLATFORM_WORDS:
        return False
    # named by structure (a type-led phrase, the thing a population
    # consumes, a quoted title): no capital letter needed (2026-10-20)
    if names_in_structure(n, t):
        return True
    # written as a name: every word of it appears capitalized (or as an
    # acronym) in the reader's own text, in order
    pat = r"\b" + r"\s+".join(
        re.escape(w) if (w[:1].isupper() or w.isupper()) else r"(?:" + re.escape(w) + r")"
        for w in n.split()) + r"\b"
    return bool(re.search(pat, t))


_ON_LINE_RX = re.compile(r"^\s*On\s+(?P<who>[^:\n]{1,160}?)\s*(?::|\n|$)")


def thread_on_page(history, page, normalize, limit=12):
    """True when this thread's latest answer landed on the open page:
    the agent turn recorded the page as its subject, or its first line
    reads "On <page>:" (how a page-bound answer opens). A confirm
    question decides nothing; an "On it" promise decides nothing; an
    answer on a different subject ends the search as False."""
    pg = normalize(str(page or '').split(' - ')[0])
    if not pg:
        return False
    turns = [t for t in (history or []) if isinstance(t, dict)]
    for t in reversed(turns[-limit:]):
        role = str(t.get('role') or '').lower()
        if role == 'user':
            txt = str(t.get('text') or t.get('content') or '').strip()
            if txt.lower().startswith('yes, ') and normalize(txt[5:].split(' - ')[0]) == pg:
                return True
            continue
        if role not in ('agent', 'assistant'):
            continue
        meta = t.get('meta') if isinstance(t.get('meta'), dict) else {}
        for k in ('subject', 'profile'):
            v = str(t.get(k) or meta.get(k) or '').strip()
            if v:
                return normalize(v.split(' - ')[0]) == pg
        txt = str(t.get('text') or t.get('content') or '').strip()
        if not txt:
            continue
        low = txt.lower()
        if low.startswith('do you want this on') or low.startswith('on it'):
            continue
        m = _ON_LINE_RX.match(txt)
        if m:
            who = normalize(m.group('who').split(' - ')[0].split(' (')[0])
            return who == pg or (bool(who) and (who in pg or pg in who))
    return False


def page_brand_hit(text, brands, normalize, common=()):
    """The brand the ask names that is a row on the open profile, or
    ''. Single-word brands must be 4+ letters; one that is also an
    ordinary English word counts only when the reader capitalized it
    ("what is Dewitts why is it 27x?" on Brock Mesarich, 2026-10-07)."""
    t = str(text or '')
    tl = ' ' + normalize(t) + ' '
    if not tl.strip():
        return ''
    best = ''
    for b in brands or ():
        b = str(b or '').strip()
        nb = normalize(b)
        if not nb or len(nb) < 4 or f' {nb} ' not in tl:
            continue
        toks = nb.split()
        if len(toks) == 1 and (toks[0] in common or len(toks[0]) < 5):
            if not re.search(r"\b" + re.escape(b) + r"\b", t):
                continue
        if len(nb) > len(normalize(best)):
            best = b
    return best


_CLAUSE_CUT_RX = re.compile(
    r"\s+(?:and|or|but)\s+(?:if|whether|would|could|should|how|what|when|"
    r"where|why|does|do|did|is|are|will|then|also|can)\b.*$"
    r"|,\s*(?:and|or|but)\b.*$"
    r"|\s+if\s+(?:it|they|we|you|there|he|she)\b.*$"
    r"|\s+(?:would|could|should)\s+(?:it|they|we|you|more|fewer|less)\b.*$",
    re.I)


def clip_clause(subject):
    """Cut a subject at the clause that follows it: "Crossroads Spring
    and If It Were" -> "Crossroads Spring" (2026-10-08, a $500 offer
    carried the conditional in the title)."""
    s = str(subject or '').strip()
    if not s:
        return s
    cut = _CLAUSE_CUT_RX.sub('', s).strip(" ,;:'\"")
    return cut or s


def page_brand_on_file(text, page_key, s3_client, bucket, normalize):
    """The brand the ask names that is a row on the open profile, or ''
    ("what is Dewitts why is it 27x?" on Brock Mesarich drew a confirm,
    2026-10-07). The frame comes from the ETag-cached loader the digest
    already uses; any failure reads as no hit."""
    if not page_key or not str(text or '').strip():
        return ''
    try:
        import prometheus_analysis as _pma
        from prometheus import referents as _refs
        df, _etag = _pma.load_profile_df(s3_client, bucket, page_key)
        return page_brand_hit(text, _pma.profile_brands(df), normalize,
                              common=getattr(_refs, '_COMMON_WORDS', ()))
    except Exception:
        return ''
