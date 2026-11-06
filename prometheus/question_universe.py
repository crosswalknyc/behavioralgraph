"""The base of a read is the universe the question names (Jenna 2026-11-06).

Jenna asked, with a profile open on her dashboard: "over the past three
years show me the fluctuation of when people start christmas holiday toy
shopping and give me a social media and paid search strategy to hit
potential paw patrol consumers." The reply opened "On Paw Patrol toy
buyers in the Starz audience:" and every figure in it was a share of the
Starz profile. The question never mentioned Starz. (The page itself was
dropped; the catalog's typo tier then read the word "start" as a
one-letter misspelling of "Starz" and bound that file. The lane had no
rule that said the question had already named its universe.)

The rule, shared by the sizing lane and the reasoned read lane:

  * The page's audience (the open profile, the open twin, the thread's
    subject) is the base ONLY when the question points at it ("this
    audience", "these viewers", "them", "of those" after a page read, a
    bare "how many people") or names the page's own subject.
  * A question that names its own universe ("people who ...", "potential
    <brand> consumers", "<brand> buyers", "US households", a population
    behavior such as "when people start holiday toy shopping") is read on
    that universe, US people, and the page is ignored for the base, the
    framing and the "On <subject>" header. No other file is guessed in
    for it either: the typo tier and the partial-name tier stand down.
  * Output guard: a reply whose base names an audience the question did
    not name, while the question named its own universe, never ships;
    it is read again on the named universe.

`question_universe(text, page)` is the one reading both lanes take.
`points_at_page` is the sizing lane's pointer test, kept here so there
is one copy. Pure text; never raises.
"""
from __future__ import annotations

import re
from collections import namedtuple

__all__ = ['Universe', 'question_universe', 'named_universe', 'page_pointer', 'points_at_page',
           'names_page', 'names_other_universe', 'ordinary_word', 'panel_base', 'US_PEOPLE']

US_PEOPLE = 'US people'

# kind: 'page' (the open page is the base), 'named' (the question names
# its own universe), 'none' (nothing named, nothing pointed at).
# label: the audience the read is about, as the reader would say it.
# base: the universe the figures describe (US people for a population).
# phrases: what the words named, in order.
# explicit: the words name an audience outright (a brand's consumers,
# "people who ...", a population such as US households). A population
# behavior alone ("when people start holiday shopping", "how many people
# watched X") is a named universe without an explicit audience: the page
# is still not its base, but a library file the words nearly name may be
# (the title the people watched).
Universe = namedtuple('Universe', 'kind label base phrases explicit')
NONE = Universe('none', '', '', (), False)


def _squash(text):
    return ' '.join(str(text or '').split())


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or '').lower())


# ---------------------------------------------------------------- pointers
# The pronoun as the thing being sized: "how big is it", "how many people
# are in this", "the size of that one", "size this", "incidence of the open
# profile". Never a determiner ("that season", "this show", "it rolled").
_PAGE_POINTER_RX = re.compile(
    r"\bhow (?:big|large|many)(?: (?:people|users|panelists|individuals|folks|of them|of these|of those))?(?: (?:is|are|was|were|does|do|did))?"
    r"(?: (?:in|inside|within|for|on))?(?: (?:the|this|that))? (?P<p>it|this|that|here|this one|that one|the open (?:one|profile|page|file)|"
    r"(?:this|that) (?:one|profile|page|audience|file|universe|pull|data ?set))\b(?!\s+(?:season|series|show|episode|title|film|movie|book|brand|campaign))|"
    r"\b(?:sizes?|sizing|incidence|headcount|head count|reach|sample[- ]size|audience[- ]size|universe[- ]size|projected band|band) (?:of|for|on) "
    r"(?P<q>it|this|that|here|this one|that one|the open (?:one|profile|page|file)|(?:this|that) (?:one|profile|page|audience|file|universe|pull|data ?set))\b"
    r"(?!\s+(?:season|series|show|episode|title|film|movie|book|brand|campaign))|"
    r"\b(?:it|this|that|this one|that one)(?:'s| is| has|’s) (?:a |an |the )?(?:total |projected |overall )?(?:sizes?|incidence|headcount|head count|sample[- ]size|audience[- ]size|universe[- ]size|band|reach)\b|"
    r"^\W*(?:(?:please |can you |could you )?(?:sizes?|sizing|size (?:it|this|that|this one)|incidence|how big|how large|how many|headcount|sample size|audience size)\W*)$", re.I)


def points_at_page(text):
    """True when a sizing ask points at the open page and names nothing
    else: a pronoun in the subject slot, or a bare size word."""
    t = _squash(text)
    if not t or len(t) > 240:
        return False
    if not _PAGE_POINTER_RX.search(t):
        return False
    # a title word or a named thing beside the pronoun means the words
    # name their own subject ("how big is that season of Fightland")
    rest = _PAGE_POINTER_RX.sub(' ', t)
    rest = re.sub(r"\b(?:how (?:big|large|many)|sizes?|sizing|incidence|headcount|head count|sample[- ]size|audience[- ]size|universe[- ]size|band|reach|"
                  r"people|users|panelists|individuals|folks|audience|profile|page|file|open|one|the|a|an|of|for|on|in|is|are|was|were|does|do|did|"
                  r"what|whats|what's|tell|give|show|me|us|please|thanks|projected|total|overall|exactly|roughly|about|now|again|and|also|so|just|"
                  r"this|that|it|here|these|those|them|its|be|would|could|can|i|we|you|have|has|get|know|see|want|need|sample|size|data|set|dataset|universe|"
                  r"pull|count|number|figure|figures|numbers|band|window|year|months?|trailing|last|past|days?|weeks?|within|inside|hold|holds|contain|contains|"
                  r"big|large|many|much|up|out|there|then|currently|selected|current|loaded|displayed|shown|showing|whole|entire|full|same|own|my|our|your|their)\b", ' ', rest, flags=re.I)
    rest = re.sub(r"[^A-Za-z]+", ' ', rest).strip()
    return not any(len(w) >= 3 for w in rest.split())


_AUD_NOUN = (r"(?:audience|profile|file|universe|cohort|group|segment|base|data ?set|dataset|pull|"
             r"viewers|people|users|fans|subscribers|buyers|shoppers|customers|consumers|households|folks|members|"
             r"panelists|individuals|readers|listeners|players|parents|moms|dads|kids|teens|adults|men|women)")
# a strong pointer: the page's audience is the subject of the sentence
_STRONG_POINTER_RX = re.compile(
    r"\b(?:this|that|these|those|the open|the current|the selected|the loaded)\s+" + _AUD_NOUN + r"\b|"
    r"\b(?:of|among|within|in|from|across)\s+(?:those|these|them)\b|"
    r"\bhow many of (?:them|those|these)\b|"
    r"\b(?:what|which)\s+(?:share|percent(?:age)?|portion|fraction|%)\s+of\s+(?:them|those|these)\b|"
    r"\bon\s+(?:this|the)\s+(?:screen|page)\b|\bthe open (?:one|profile|page|file)\b|"
    r"^\W*(?:and |so |then |ok(?:ay)? |now )?(?:how many|how big|how large)(?: (?:people|users|viewers|individuals|folks))?"
    r"(?: (?:are|is) (?:there|in (?:it|this|that|here)))?\W*$", re.I)
# a weak pointer: an anaphor that only means the page when nothing else is named
_WEAK_POINTER_RX = re.compile(
    r"\b(?:reach|target|hit|get to|talk to|sell to|market to|convert|win|keep|retain|email|find)\s+them\b|"
    r"\bthey\s+(?:are|were|do|did|watch|buy|shop|spend|start|tend|skew|lean|live|use|prefer)\b|"
    r"\b(?:their|its)\s+(?:median|average|share|split|mix|age|gender|income|behavior|habits)\b|"
    r"\bhere\b", re.I)


def page_pointer(text, strong_only=False):
    """True when the words point at the open page: a pronoun with the
    audience noun in the subject slot ("this audience", "these viewers",
    "of those", "how many of them"), the sizing pointer ("how big is
    it"), or a bare "how many people". With `strong_only` False an
    anaphor ("reach them", "they skew") counts too."""
    t = _squash(text)
    if not t:
        return False
    if _STRONG_POINTER_RX.search(t) or points_at_page(t):
        return True
    if strong_only:
        return False
    return bool(_WEAK_POINTER_RX.search(t))


# ------------------------------------------------------------ the page named
_PAGE_GENERIC = frozenset(('the', 'a', 'an', 'of', 'and', 'or', 'on', 'in', 'for', 'to', 'at', 'by', 'with',
                           'audience', 'audiences', 'profile', 'fans', 'fan', 'viewers', 'viewer', 'users', 'user',
                           'customers', 'customer', 'buyers', 'buyer', 'shoppers', 'shopper', 'consumers', 'consumer',
                           'subscribers', 'subscriber', 'members', 'member', 'people', 'total', 'universe', 'avid',
                           'casual', 'cut', 'cuts', 'tu', 'potential', 'us', 'iq', 'q1', 'q2', 'q3', 'q4',
                           'ticket', 'purchasers', 'purchaser', 'listeners', 'readers', 'players', 'parents', 'moms',
                           'children', 'kids', 'twin', 'room'))


def _page_tokens(page):
    head = str(page or '').split(' - ')[0]
    toks = [w for w in re.sub(r"[^a-z0-9]+", " ", head.lower()).split() if w and w not in _PAGE_GENERIC]
    toks = [w for w in toks if not re.fullmatch(r"\d{2}_\d{2}_\d{4}", w)]
    return toks


def names_page(text, page):
    """True when the words name the page's own subject: every distinctive
    token of the page's name (before any ' - ' cut suffix) appears in the
    words, as a whole word, or the name's possessive."""
    toks = _page_tokens(page)
    if not toks:
        return False
    low = ' ' + re.sub(r"[^a-z0-9']+", " ", _squash(text).lower()) + ' '
    low = low.replace("'s ", ' ').replace("' ", ' ')
    return all(f" {w} " in low for w in toks)


# ------------------------------------------------------- the named universe
_CONSUMER_NOUN = (r"(?:consumers?|buyers?|customers?|shoppers?|purchasers?|subscribers?|viewers?|users?|owners?|"
                  r"renters?|members?|players?|readers?|listeners?|voters?|drivers?|patients?|donors?|guests?|"
                  r"diners?|travell?ers?|fans?|watchers?|streamers?|households?|parents?|moms?|dads?|families|"
                  r"gift[- ]givers?|gifters?|holders?|account ?holders?|cardholders?|cord[- ]cutters?|switchers?|"
                  r"intenders?|prospects?|leads?|adopters?|enthusiasts?|loyalists?)")
_POP_NOUN = (r"(?:people|consumers|shoppers|households|parents|families|americans|adults|men|women|kids|children|"
             r"teens|teenagers|buyers|customers|moms|mothers|dads|fathers|gift[- ]givers|folks|everyone|anyone)")
_POP_VERB = (r"(?:start|begin|shop|buy|purchase|spend|search|look|browse|plan|order|book|travel|watch|stream|listen|read|"
             r"play|sign up|subscribe|cancel|churn|switch|return|visit|go|tend|usually|typically|generally|normally|"
             r"use|choose|decide|prefer|pay|give|gift|wrap|ship|save|wait|"
             r"start(?:ed|s|ing)|beg(?:an|ins|inning)|shop(?:ped|s|ping)|b(?:ought|uys|uying)|purchas(?:ed|es|ing)|"
             r"sp(?:ent|ends|ending)|search(?:ed|es|ing)|plan(?:ned|s|ning)|order(?:ed|s|ing)|watch(?:ed|es|ing)|"
             r"stream(?:ed|s|ing)|visit(?:ed|s|ing)|return(?:ed|s|ing)|switch(?:ed|es|ing)|cancel(?:led|ed|s|ling|ing)|"
             r"subscrib(?:ed|es|ing)|pa(?:id|ys|ying)|g(?:ave|ives|iving)|wait(?:ed|s|ing))")
_BRAND_TOK = r"[A-Za-z0-9][\w&'+.!-]*"
_QUAL = r"(?:potential|prospective|likely|future|would-be|possible|probable|new|current|existing|lapsed|recent|frequent|heavy|light|first-time|repeat|loyal|active|lapsed)"

# 1. people who <verb> ...; those who ...; households that ...
_WHO_RX = re.compile(
    r"\b(?P<p>(?:" + _POP_NOUN + r"|those|users|viewers|subscribers|customers|fans|readers|listeners|players|voters|shoppers)"
    r"\s+(?:who|that|which)\s+(?:(?!then\b|and then\b|but\b|what\b|how\b|when\b|where\b|why\b|over\b|in the\b|during\b)[\w'&+.-]+\s*){1,10}?)"
    r"(?=\s*(?:,|;|:|\?|\.|$|\band then\b|\bthen\b|\bbut\b|\bwhat\b|\bhow\b|\bwhen\b|\bwhere\b|\bwhy\b|\bover the\b|\bin the (?:last|past|trailing|window)\b|\bduring\b|\band (?:give|show|tell|what|how|then)\b))", re.I)
# 2. potential <brand> consumers
_QUAL_BRAND_RX = re.compile(
    r"\b(?P<p>" + _QUAL + r"\s+(?P<brand>(?:(?!" + _CONSUMER_NOUN + r"\b)" + _BRAND_TOK + r"\s+){1,4})" + _CONSUMER_NOUN + r")\b", re.I)
# 3. <brand> buyers / buyers of <brand>
_BRAND_NOUN_RX = re.compile(
    r"\b(?P<p>(?P<brand>(?:(?!(?:the|a|an|of|for|to|in|on|at|by|with|and|or|this|that|these|those|its|their|our|your|my|"
    r"all|any|some|more|most|many|few|other|same|new|old|first|last|next|each|every|both|such|no|not|only|own|us|u\.s\.|"
    r"american|total|avid|casual|potential|prospective|likely|future|would-be|how|what|which|who|where|when|why|hit|reach|target|"
    r"give|show|tell|me|strategy|plan|social|paid|search|media|toy|toys|holiday|christmas|gift|gifts|online|digital|mobile|"
    r"typical|average|median|overall|general|core|top|key|main|heavy|light|loyal|active|lapsed|current|existing|"
    r"these|million|thousand|percent|share)\b)" + _BRAND_TOK + r"\s+){1,3})" + _CONSUMER_NOUN + r")\b", re.I)
_NOUN_OF_BRAND_RX = re.compile(
    r"\b(?P<p>" + _CONSUMER_NOUN + r"\s+of\s+(?:the\s+)?(?P<brand>(?:" + _BRAND_TOK + r"\s*){1,4}?))(?=\s*(?:,|;|:|\?|\.|$|\b(?:and|or|who|that|which|in|on|over|during|then|but|what|how|when|where|why)\b))", re.I)
# 4. a population
_POP_RX = re.compile(
    r"\b(?P<p>(?:(?:all\s+)?(?:us|u\.s\.|american|america's)\s+(?:households|adults|consumers|shoppers|people|population|public|"
    r"parents|moms|dads|families|men|women|teens|kids|children|gen z|millennials|boomers|gen x|voters|homes|buyers|customers)|"
    r"americans|(?:the\s+)?general population|everyone in the (?:us|u\.s\.|country)|gen pop|the (?:us|u\.s\.) population|"
    r"(?:all\s+)?(?:us|u\.s\.) (?:adults|households|consumers)))\b", re.I)
# 5. a population behavior: when people start ..., how households shop ...
_POP_BEHAVIOR_RX = re.compile(
    r"\b(?P<p>" + _POP_NOUN + r"\s+(?:(?:usually|typically|generally|normally|actually|really|first|now|still|already|often|mostly|tend to|start to|begin to)\s+)?"
    r"(?:" + _POP_VERB + r")\b(?:\s+(?!then\b|and\b|but\b|what\b|how\b|when\b|where\b|why\b|over\b|give\b|show\b|tell\b)[\w'&+.-]+){0,7})", re.I)
# a parent / family universe: parents of kids 2 to 6, moms of toddlers
_PARENT_OF_RX = re.compile(
    r"\b(?P<p>(?:parents|moms|mothers|dads|fathers|households|families|grandparents|caregivers)\s+(?:of|with)\s+"
    r"(?:(?!and\b|then\b|but\b|what\b|how\b|when\b|where\b|why\b|over\b|give\b|show\b)[\w'&+.-]+\s*){1,6}?)"
    r"(?=\s*(?:,|;|:|\?|\.|$|\band\b|\bthen\b|\bbut\b|\bwhat\b|\bhow\b|\bwhen\b|\bwhere\b|\bwhy\b|\bover the\b|\bin the\b|\bwho\b|\bthat\b))", re.I)

_LABEL_LOWER = frozenset(('potential', 'prospective', 'likely', 'future', 'would-be', 'possible', 'probable', 'new',
                          'current', 'existing', 'lapsed', 'recent', 'frequent', 'heavy', 'light', 'first-time',
                          'repeat', 'loyal', 'active', 'who', 'that', 'which', 'of', 'the', 'a', 'an', 'and', 'or',
                          'on', 'in', 'at', 'to', 'for', 'with', 'from', 'by', 'people', 'those', 'anyone', 'everyone',
                          'households', 'parents', 'moms', 'dads', 'families', 'adults', 'americans', 'kids', 'children',
                          'teens', 'men', 'women', 'consumers', 'shoppers', 'buyers', 'customers', 'users', 'viewers',
                          'subscribers', 'fans', 'readers', 'listeners', 'players', 'voters', 'members', 'owners',
                          'purchasers', 'renters', 'watchers', 'streamers', 'drivers', 'patients', 'donors', 'guests',
                          'diners', 'travelers', 'travellers', 'gift', 'givers', 'gift-givers', 'intenders', 'prospects',
                          'holders', 'switchers', 'enthusiasts', 'loyalists', 'adopters', 'leads', 'account', 'cardholders',
                          'cord-cutters', 'us', 'toy', 'toys', 'holiday', 'christmas', 'shopping', 'start', 'started',
                          'begin', 'shop', 'buy', 'bought', 'purchase', 'spend', 'watched', 'watch', 'stream', 'streamed',
                          'read', 'play', 'played', 'listen', 'listened', 'use', 'used', 'visit', 'visited', 'ordered',
                          'order', 'online', 'last', 'year', 'years', 'month', 'months', 'season', 'window', 'past',
                          'three', 'two', 'one', 'their', 'his', 'her', 'own', 'first', 'ever', 'still', 'also', 'not',
                          'no', 'any', 'all', 'some', 'most', 'more', 'than', 'under', 'over', 'ages', 'aged', 'age',
                          'between', 'through', 'into', 'out', 'up', 'down', 'off', 'about', 'around', 'per', 'every',
                          'each', 'both', 'either', 'neither', 'such', 'other', 'same', 'few', 'several', 'many', 'much',
                          'are', 'were', 'is', 'was', 'have', 'has', 'had', 'do', 'did', 'does', 'will', 'would', 'tend',
                          'usually', 'typically', 'generally', 'normally', 'actually', 'really', 'now', 'already',
                          'often', 'mostly', 'yet', 'then', 'early', 'late', 'earlier', 'later', 'before', 'after',
                          'during', 'since', 'until', 'while', 'when', 'where', 'how', 'why', 'what', 'service',
                          'app', 'site', 'store', 'stores', 'brand', 'brands', 'product', 'products', 'category',
                          'kid', 'child', 'baby', 'babies', 'toddler', 'toddlers', 'preschooler', 'preschoolers',
                          'boys', 'girls', 'son', 'daughter', 'sons', 'daughters', 'grandparents', 'caregivers', 'home',
                          'homes', 'house', 'houses', 'ticket', 'tickets', 'card', 'cards', 'plan', 'plans', 'film',
                          'films', 'movie', 'movies', 'show', 'shows', 'series', 'title', 'titles', 'episode', 'episodes',
                          'book', 'books', 'game', 'games', 'song', 'songs', 'album', 'albums', 'podcast', 'podcasts'))
_ACRONYM_RX = re.compile(r"^[A-Z0-9&+.'-]{2,6}$")


def _clean_phrase(p):
    p = _squash(p).strip(" ,.;:?!'\"")
    p = re.sub(r"\s+(?:and|or|then|but|who|that|which|of|in|on|at|to|for|with|from|by|are|is|were|was|do|did|does)$", '', p, flags=re.I)
    # a trailing verb is the question's, not the universe's ("parents of
    # kids 4-7 buying" -> "parents of kids 4-7")
    words = p.split()
    while len(words) > 2 and re.fullmatch(r"[a-z]+ing", words[-1]) and ordinary_word(words[-1]):
        words.pop()
    return ' '.join(words).strip(" ,.;:?!")


def _display(p):
    """A universe phrase as a reader would write it: brand tokens cased
    as names, ordinary words left as they are ('potential paw patrol
    consumers' -> 'potential Paw Patrol consumers')."""
    out = []
    for w in _clean_phrase(p).split():
        bare = w.strip("'\".,;:!?()")
        if not bare:
            continue
        if _ACRONYM_RX.match(bare) or any(c.isupper() for c in bare[1:]):
            out.append(w)                       # NFL, iPhone, HBO Max: as written
        elif bare.lower() in _LABEL_LOWER or re.fullmatch(r"[\d.,%$+-]+", bare):
            out.append(w.lower() if bare.lower() in _LABEL_LOWER else w)
        else:
            out.append(w[:1].upper() + w[1:])
    return ' '.join(out)


def _is_population(phrase):
    low = ' ' + _clean_phrase(phrase).lower() + ' '
    if _POP_RX.search(low):
        return True
    return bool(re.match(r"\s(?:people|consumers|shoppers|households|parents|families|americans|adults|men|women|kids|"
                         r"children|teens|teenagers|moms|mothers|dads|fathers|gift[- ]givers|folks|everyone|anyone|those)\b", low))


def _population_label(phrase):
    low = _clean_phrase(phrase).lower()
    m = _POP_RX.search(low)
    if m:
        return _display(m.group('p')).replace('Us ', 'US ').replace('us ', 'US ')
    return US_PEOPLE


def named_universe(text):
    """The universe phrases the words name on their own, in order of
    appearance: 'people who watched Fightland on Starz', 'potential Paw
    Patrol consumers', 'Costco members', 'US households', 'parents of
    kids 2 to 6', a population behavior ('people start christmas holiday
    toy shopping'). Each item is (phrase, population) where population
    is True when the phrase means US people at large (or a slice of
    them, such as parents) rather than a named brand's audience."""
    t = _squash(text)
    if not t or len(t) > 1200:
        return []
    found = []

    def _add(start, phrase, population):
        c = _clean_phrase(phrase)
        if not c or len(c.split()) > 14:
            return
        key = _norm(c)
        if any(key == _norm(f[1]) or key in _norm(f[1]) for f in found):
            return
        found.append((start, c, population))

    for m in _QUAL_BRAND_RX.finditer(t):
        _add(m.start(), m.group('p'), False)
    for m in _NOUN_OF_BRAND_RX.finditer(t):
        b = _clean_phrase(m.group('brand'))
        if b and not re.fullmatch(r"(?:the|this|that|these|those|them|it|us|our|your|their|my|his|her)", b, re.I):
            noun = m.group('p').split()[0].lower()
            # "parents of kids 2 to 6", "buyers of toys": a slice of US people, not a brand's audience
            pop = noun in ('parents', 'moms', 'dads', 'mothers', 'fathers', 'households', 'families') \
                or all(ordinary_word(w) or re.fullmatch(r"[\d.,%$+-]+", w) for w in b.lower().split())
            _add(m.start(), m.group('p'), pop)
    for m in _BRAND_NOUN_RX.finditer(t):
        # the brand is the name beside the noun: leading verbs and function
        # words ("what do Costco members", "compare Gunna fans") come off
        btoks = _clean_phrase(m.group('brand')).split()
        while btoks and (ordinary_word(btoks[0]) or btoks[0].lower() in _LABEL_LOWER) and not _ACRONYM_RX.match(btoks[0]):
            btoks.pop(0)
        if not btoks:
            continue
        b = ' '.join(btoks)
        if any(ordinary_word(w) for w in b.lower().split()) and not any(c.isupper() for c in b):
            continue        # 'toy buyers', 'holiday shoppers': a category, not a brand's audience
        noun = m.group('p').split()[-1]
        _add(m.start() + m.group('p').lower().find(b.lower()), f"{b} {noun}", False)
    for m in _WHO_RX.finditer(t):
        _add(m.start(), m.group('p'), True)
    for m in _PARENT_OF_RX.finditer(t):
        _add(m.start(), m.group('p'), True)
    for m in _POP_RX.finditer(t):
        _add(m.start(), m.group('p'), True)
    for m in _POP_BEHAVIOR_RX.finditer(t):
        _add(m.start(), m.group('p'), True)
    found.sort(key=lambda f: f[0])
    # a longer phrase that contains a shorter one carries it
    out = []
    for _s, phrase, pop in found:
        k = _norm(phrase)
        if any(k != _norm(o[0]) and k in _norm(o[0]) for o in out):
            continue
        out = [o for o in out if not (_norm(o[0]) != k and _norm(o[0]) in k)]
        out.append((phrase, pop))
    return out


def question_universe(text, page='', history=None):
    """The one reading: Universe(kind, label, base, phrases).

    kind 'page'  when the words name the page's own subject, or point at
                 the page (a strong pointer anywhere; a weak anaphor only
                 when no universe is named).
    kind 'named' when the words name their own universe. label is that
                 audience as a reader would write it ('potential Paw
                 Patrol consumers'); base is 'US people' for a population
                 (or a population behavior), else the label itself.
    kind 'none'  otherwise."""
    del history   # the thread is the caller's to consult; the words decide here
    t = _squash(text)
    page = str(page or '').strip()
    if not t:
        return NONE
    named = named_universe(t)
    if page and names_page(t, page):
        # the page named as the PLATFORM inside the question's own universe
        # ("people who watched Fightland on Starz" with Starz open) is not
        # the page named as the audience
        head = str(page).split(' - ')[0].strip()
        as_platform = bool(head) and bool(re.search(
            r"\b(?:on|via|from|through|at|off)\s+(?:the\s+)?" + r"\s+".join(re.escape(w) for w in head.split()) + r"\b", t, re.I))
        explicit_named = any(not pop for _p, pop in named) or any(_WHO_OF_RX.match(p) for p, _pop in named)
        if not (as_platform and explicit_named and not re.search(
                r"\b" + r"\s+".join(re.escape(w) for w in head.split()) + r"(?:'s)?\s+" + _AUD_NOUN + r"\b", t, re.I)):
            return Universe('page', page, page, (page,), True)
    if page and page_pointer(t, strong_only=True):
        return Universe('page', page, page, (page,), True)
    if named:
        brands = [p for p, pop in named if not pop]
        pops = [p for p, pop in named if pop]
        explicit = bool(brands)
        if brands:
            label = _display(brands[0])
            base = US_PEOPLE if pops else label
        else:
            who = [p for p in pops if _POP_RX.search(' ' + p.lower() + ' ') or _WHO_OF_RX.match(p)]
            explicit = bool(who)
            lead = who[0] if who else pops[0]
            label = _population_label(lead) if _POP_RX.search(' ' + lead.lower() + ' ') else (
                _display(lead) if _WHO_OF_RX.match(lead) else US_PEOPLE)
            base = US_PEOPLE
        return Universe('named', label, base, tuple(p for p, _pop in named), explicit)
    if page and page_pointer(t):
        return Universe('page', page, page, (page,), True)
    return NONE


_WHO_OF_RX = re.compile(r"(?:people|those|anyone|everyone|parents|moms|mothers|dads|fathers|households|families|grandparents|caregivers)\s+(?:who|that|which|of|with)\b", re.I)


def _edit1(a, b):
    """One edit apart (a typo), for words of five letters or more."""
    if a == b:
        return True
    if len(a) < 5 or len(b) < 5 or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        if len(diff) == 1:
            return True
        return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
    s, l = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(s) and s[i] == l[i]:
        i += 1
    return s[i:] == l[i + 1:]


def _subject_named(text, subject):
    """The base's subject is named in the words (its distinctive tokens,
    before any ' - ' cut suffix, all present), a one-letter misspelling
    of a token counting as the token when the reader's word is not an
    ordinary English word ("cotsco members" names Costco; "start" never
    names Starz)."""
    toks = _page_tokens(subject)
    if not toks:
        return False
    words = re.sub(r"[^a-z0-9']+", " ", _squash(text).lower()).replace("'s ", ' ').replace("' ", ' ').split()
    wset = set(words)
    for t in toks:
        if t in wset:
            continue
        if not any(_edit1(t, w) for w in wset if not ordinary_word(w)):
            return False
    return True


def names_other_universe(text, base_subject, page=''):
    """The output guard. True when the question names its own universe
    and `base_subject` (the page, a library file, a thread subject) is
    not that universe and is not named in the words: a read on that base
    never ships for this question."""
    u = question_universe(text, page)
    if u.kind != 'named' or not u.explicit:
        return False
    subj = str(base_subject or '').strip()
    if not subj:
        return False
    if _subject_named(text, subj):
        return False
    sk = _norm(subj.split(' - ')[0])
    for p in u.phrases:
        pk = _norm(p)
        if sk and pk and (sk == pk or (len(sk) >= 4 and sk in pk)):
            return False
    if _norm(u.label) == sk or _norm(US_PEOPLE) == sk or sk in ('everyoneintheus', 'everyone', 'genpop', 'generalpopulation'):
        return False
    return True


EVERYONE = "Everyone in the US"


def _proper_name_in(text, name):
    """`name` is a title or brand the words name: every token present and
    at least one of them not an ordinary English word."""
    n = _squash(name)
    if not n or not names_page(text, n):
        return False
    toks = [w for w in re.sub(r"[^a-z0-9]+", " ", n.lower()).split() if w not in _PAGE_GENERIC]
    return bool(toks) and not all(ordinary_word(w) for w in toks)


def subject_for(universe, current='', text=''):
    """The subject a no-base read is quoted and run on when the question
    names its own universe: the named audience ('potential Paw Patrol
    consumers', 'Costco members'); for a population, the title or brand
    the words name as the thing consumed when the lane already holds one
    ('Carrie' in "how many people watched Carrie on Prime Video"), else
    the population itself ('US households', or everyone in the US)."""
    u = universe if isinstance(universe, Universe) else question_universe(universe)
    cur = _squash(current)
    if u.kind != 'named':
        return cur
    if u.label and u.label != US_PEOPLE and not _POP_RX.fullmatch(u.label):
        return u.label
    if cur and _proper_name_in(text, cur):
        return cur
    if u.label and u.label != US_PEOPLE:
        return u.label
    return EVERYONE


def panel_base(universe):
    """The base record a named universe runs on: the panel projected to
    US people, the named audience as the subject, no file."""
    u = universe if isinstance(universe, Universe) else question_universe(universe)
    if u.kind != 'named':
        return None
    return {'subject': u.label, 's3_key': '', 'source': 'panel', 'universe': u.base}


def universe_label(universe):
    """What the opener names when the model did not name a universe:
    'potential Paw Patrol consumers in the US', 'US households', 'US
    people'."""
    u = universe if isinstance(universe, Universe) else question_universe(universe)
    if u.kind != 'named':
        return ''
    if u.label and u.label != US_PEOPLE and not _POP_RX.fullmatch(u.label):
        if u.base == US_PEOPLE and not re.search(r"\b(?:us|u\.s\.|american|america)\b", u.label, re.I):
            return f"{u.label} in the US"
        return u.label
    return u.label or US_PEOPLE


_FRAMED_ON_RX = r"\b(?:in|of|within|among|across|inside|from)\s+(?:the\s+|this\s+|that\s+)?{name}(?:'s)?\s+(?:audience|profile|base|universe|file|fans|viewers|subscribers|cohort)\b"


def base_leak(reply, res, dashboard_names, text=''):
    """The dashboard audience a reply framed itself on, or ''. Checks the
    opener ('On <X>:'), the read's own subject and universe, and the
    first lines of the body for 'in the <X> audience' phrasing. Only the
    names handed in count (the open page, the base that was dropped),
    and never one the question itself named: a universe the model wrote
    in its own words is never a leak."""
    txt = str(reply or '')
    head = txt.strip().split('\n', 1)[0][:200]
    body = txt[:900]
    res = res if isinstance(res, dict) else {}
    labels = [str(res.get('universe') or ''), str(res.get('subject') or ''), str(res.get('cohort') or ''), head]
    for name in dashboard_names or ():
        name = str(name or '').split(' - ')[0].strip()
        toks = _page_tokens(name)
        if not toks or (text and names_page(text, name)):
            continue
        if any(names_page(lab, name) for lab in labels if lab):
            return name
        pat = _FRAMED_ON_RX.format(name=r"\s+".join(re.escape(t) for t in name.split()))
        if re.search(pat, body, re.I):
            return name
    return ''


def framing_block(universe, question=''):
    """The prompt block that holds a named-universe read to its universe."""
    u = universe if isinstance(universe, Universe) else question_universe(universe)
    if u.kind != 'named':
        return ''
    lines = [
        "UNIVERSE (the question names its own):",
        f"- The audience this read is about: {u.label}.",
        f"- The base the figures describe: {u.base}" + (
            " (the panel projected to the US population; shares are shares of US people, counts are projected US people)."
            if u.base == US_PEOPLE else " (the people who meet that description, projected to the US)."),
        "- No profile, twin or file on the dashboard is the base of this read. Never frame a figure as a share of any "
        "audience the question did not name ('in the <X> audience', 'of this audience', 'audience wide'). Never open "
        "with 'On <a profile>:'. Set \"universe\" to the audience in plain words, for example "
        f"\"{u.label}\" or \"US people who bought a holiday toy\".",
        "- A library file may inform a rate (a lean, an index) as a comparison, named as a comparison, never as the base.",
    ]
    if question:
        lines.append(f"- Answer every part of the question: {_squash(question)[:400]}")
    return '\n'.join(lines)


# ----------------------------------------------------------- ordinary words
# A word the reader wrote that is an ordinary English word is never a
# one-letter misspelling of a library subject ("start" is not "Starz",
# 2026-11-06). The list is the common vocabulary that collides with brand
# tokens; the referents module's function words ride along.
_ORDINARY = frozenset("""
about above across after again against ahead along alone already also although always among amount annual another
answer anyone anything apart appear apply april area around arrive article asked asking assume august autumn
average avoid aware award away back based basic basis beach began begin behind being below beside better between
beyond black block board bonus books bought bound brand break brief bring broad broke brown budget build built
business buyer buyers buying cable calls camera cannot cards carry cases catch cause cents chain chair chance change
charge chart cheap check chief child china choice choose chose church claim class clean clear click clips close
cloth cloud coach coast color comes coming common company compare count country couple course court cover craft
create credit cross crowd cycle daily dance dated dates dealer deals death debut decide delay delta depth design
detail device dinner direct doing dollar double doubt dozen draft drama drawn dress drink drive drove during early
earth easy eight either email empty ended enjoy enter entry equal error event every exact exist extra faced facts
fails faith falls false family fancy fast fault favor fewer fiber field fifth fifty fight figure final finds first
fixed flash fleet floor focus folks force forms forth forum found frame fresh front fruit fully funny games giant
gifts given gives glass globe going goods grade grand grant graph grass great green gross group grown guess guide
happy heads heard heart heavy hello hence higher holds holiday homes hopes horse hotel hours house human ideal ideas
image index inner input issue items joint judge juice kinds knife known label large later laugh layer leads learn
least leave legal lemon level light likes limit lines links lists lived lives local logic loose lower lucky lunch
magic major maker makes march match maybe meals means meant media meets metal might miles minor minus mixed model
money month moral motor mount mouse mouth moved moves movie music names nerve never newer newly night noise north
noted notes novel nurse occur ocean offer often older olive onset opens order other ought outer owned owner pages
paint panel paper party pause peace penny phase phone photo piece pilot pitch place plain plane plant plate plays
point pound power press price pride prime print prior prize proof proud prove queen quick quiet quite quote radio
raise range rapid rated rates ratio reach ready refer reign relax reply reset rider rides right rings rises risks
rival river roads rocks rooms rough round route royal rules rural safer sales sauce saved saves scale scene scope
score seats seeds seems sells sense serve seven shall shape share sharp sheet shelf shift shine shirt shock shoes
shoot shops short shots shown shows sides sight signs since sixth sized sizes skill skirt sleep slice slide small
smart smile smoke snack solid solve songs sorry sound south space spare speak speed spend spent split spoke sport
spots spray squad staff stage stake stand stare stars start state stays steal steam steel steep steer stick stiff
still stock stone stood store storm story strip stuck study stuff style sugar suite super sweet swing table taken
takes tales talks taste taxes teach teams teeth tells tends terms tests thank theme there these thick thing think
third those three throw tight times tired title today token total touch tough tower towns track trade trail train
treat trend trial tribe trick tried tries trips truck truly trust truth twice under union unite units until upper
upset urban usage users usual valid value video views visit vital voice volume votes wages waits walks wants waste
watch water weeks weigh wheel where which while white whole whose wider woman women words works world worry worse
worth would write wrong wrote yards years young yours youth
started starts starting began begins beginning shopping shopped buying bought spending spent searching searched
planning planned ordered ordering watching watched streaming streamed visiting visited returned returning switched
switching cancelled canceled subscribing subscribed paying paid giving gifted waiting waited
""".split())


def ordinary_word(word):
    """An ordinary English word in the reader's own sentence: never a
    typo of a library subject's token."""
    w = str(word or '').strip().lower()
    if not w or not w.isalpha():
        return False
    if w in _ORDINARY:
        return True
    try:
        from .referents import _COMMON_WORDS
        if w in _COMMON_WORDS:
            return True
    except Exception:
        pass
    return False
