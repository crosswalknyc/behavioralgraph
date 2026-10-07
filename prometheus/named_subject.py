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
    return 'confirm'


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
