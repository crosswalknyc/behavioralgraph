"""A demographic segment in the data, sized once and split by a brand's
touchpoints (Jenna 2026-11-11).

A client asked: "Could you let us know how many people in your database
fit the following profile: men, aged 30-55, income $50k-$200k,
blue-collar occupations? Of this group, how many have and haven't had
touchpoints with Dixxon?" The unprojected lane read "$50k" as a figure
to bring back to the ten million and answered "50k is 1,516"; reworded
without the dollar signs, the product fact answered with the size of the
ten million itself. Jenna: "give me this answer and log it in prometheus
brain so if anyone asks again it's classified. this should be equivalent
to what this answer would be out of the us general population".

The class: "how many people in your database / data / of the 10 million
fit <a demographic profile>" with three or more demographic attributes
(gender, an age range, an income range, an occupation group, and the
other demographic cuts), with or without "how many of them have and
have not had touchpoints with <brand>".

How it is handled:

  * It is a QUESTION, a sizing read. Never an order, never a build.
  * The filter is ONE joint segment of US people. Its attributes are
    never sized one by one and never bound to a library subject ("Men
    18-34", "Blue Crush"); a money range or an age range inside the
    filter is never a figure to unproject.
  * The answer carries the count in the data and the US count side by
    side. The count in the data is the US count brought back to the 10
    million with the one factor in prometheus.projection; the count in
    the data leads when the words say database, data, sample or the 10
    million, the US count leads otherwise.
  * "Have and have not had touchpoints with <brand>" is a two-way
    partition of the segment: both sides are stated and they add up to
    the segment on both counts. The brand is the brand the words name,
    matched on its own spellings only; nothing near-spelled is taken.
  * A segment already on record answers from the record, every time, in
    any wording: the insights ledger entry banked for it (route
    decision_of_record_segment_read) and the bundled copy in
    segment_reads.json. Never a fresh draw.
  * A segment not on record is new numbers: it is not answered here. The
    sizing lanes, the unprojected lane, the product fact, the roster
    offer and the multi-ask splitter stand down for it
    (``is_segment_ask``); the work gate quotes it as ONE custom ask on
    the segment the words name (prometheus.priced_read: ``classify``,
    ``bound_subject``; the segment label is the subject, never a library
    file and never the page); the question names its own universe
    (prometheus.question_universe) and the read it runs is held to the
    frame in ``framing_lines``.

Definitions the reply states: income is household income; blue-collar
occupations are construction and extraction, installation, maintenance
and repair, production, and transportation and material moving; a
touchpoint is at least one search, social, media, shopping, or brand
site or app interaction in the window.

Vocabulary: "the 10 million US consumers we measure"; never panel as a
noun, never how a figure was made, no source names, no em dashes.

Pure functions over the words, the thread and the record; no model, no
price. Kill switch ``PM_SEGMENT_SIZING=0``. Regression:
scripts/test_pm_database_segment_sizing.py.
"""
from __future__ import annotations

import json
import os
import re

from . import projection as _pj

__all__ = ['enabled', 'parse', 'is_segment_ask', 'is_segment_label', 'wants', 'resolve', 'segment_key', 'segment_label', 'record_for',
           'answer', 'framing_lines', 'registry', 'ledger_payloads', 'TEN_M_PHRASE', 'ROUTE', 'BLUE_COLLAR_LINE',
           'TOUCHPOINT_LINE', 'INCOME_LINE', 'is_clean']

TEN_M_PHRASE = "the 10 million US consumers we measure"
ROUTE = 'decision_of_record_segment_read'
SOURCE = 'segment_read'
COHORT_PREFIX = 'segment:'
MIN_ATTRIBUTES = 3

INCOME_LINE = "Income is household income."
BLUE_COLLAR_LINE = ("Blue-collar occupations are construction and extraction; installation, maintenance and repair; "
                    "production; and transportation and material moving.")
TOUCHPOINT_LINE = ("A touchpoint is at least one search, social, media, shopping, or {brand} site or app interaction "
                   "in the window.")

_DASH_RX = re.compile(r"[\u2010-\u2015\u2212]")
_APOS_RX = re.compile(r"[\u2018\u2019\u02bc]")

# ------------------------------------------------------------- detection
_COUNT_RX = re.compile(
    r"\b(?:how many|number of|count of|head ?count|what (?:share|percent|percentage|portion|proportion|fraction)|"
    r"size of|how (?:big|large)|incidence|sample size|n[- ]size)\b", re.I)
_IN_DATA_RX = re.compile(
    r"\b(?:in|inside|within|from|of|across|out of) (?:your|the|our|crosswalk(?:'s)?) "
    r"(?:database|data ?base|data ?set|data|panel|sample|system|files?|(?:10|ten) ?(?:m|mm|million))\b|"
    r"\b(?:of|in) your (?:panelists|people|consumers|users)\b|\bdo you (?:have|hold|see|track|measure)\b|"
    r"\b(?:un-?projected|raw (?:count|number)|before projection)\b", re.I)
# a title or a behavior of its own makes the ask a read on that behavior, never a plain demographic segment
_BEHAVIOR_RX = re.compile(
    r"\b(?:watch(?:ed|es|ing)?|view(?:ed|ing)|stream(?:ed|s|ing)?|listen(?:ed|s|ing)?|play(?:ed|s|ing)?|binge[ds]?|"
    r"subscrib(?:e|ed|es|ing)|churn(?:ed|s|ing)?|cancel(?:l?ed|s|l?ing)?|sign(?:ed|s|ing)? up|tuned)\b", re.I)
_NOT_THIS_RX = re.compile(r"\b(?:deck|slides?|journey|valuation|partnership|attribution|reconcil\w+)\b", re.I)

_MALE_RX = re.compile(r"\b(?:men|males?|guys)\b", re.I)
_FEMALE_RX = re.compile(r"\b(?:women|females?|ladies)\b", re.I)
_MF_AGE_RX = re.compile(r"(?<![A-Za-z0-9])(?P<g>[MFP])\s?(?P<a>\d{2})\s?(?:-\s?(?P<b>\d{2})|(?P<plus>\+))(?![\d,.])")

_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d{1,3}(?:\.\d+)?)"
_UNIT = r"(?:(?:k|thousand|grand)(?![A-Za-z]))"
_RANGE_SEP = r"\s*(?:-|to|and|through|thru)\s*"
_MONEY_RANGE_RX = re.compile(
    r"(?P<d1>\$)?\s?(?P<a>" + _NUM + r")\s?(?P<au>" + _UNIT + r")?" + _RANGE_SEP +
    r"(?P<d2>\$)?\s?(?P<b>" + _NUM + r")\s?(?P<bu>" + _UNIT + r")?(?!\d|,\d|\.\d)", re.I)
_MONEY_FLOOR_RX = re.compile(
    r"(?:\b(?:over|above|more than|at least|north of|upwards of)\s+(?P<d1>\$)?\s?(?P<a>" + _NUM + r")\s?(?P<au>" + _UNIT + r")?"
    r"|(?P<d2>\$)?\s?(?P<b>" + _NUM + r")\s?(?P<bu>" + _UNIT + r")?\s?(?:\+|and (?:over|up|above)|or more))(?!\d|,\d|\.\d)", re.I)
_MONEY_CEIL_RX = re.compile(
    r"\b(?:under|below|less than|up to|no more than)\s+(?P<d1>\$)?\s?(?P<a>" + _NUM + r")\s?(?P<au>" + _UNIT + r")?(?!\d|,\d|\.\d)", re.I)
_INCOME_CUE_RX = re.compile(r"\b(?:income|incomes|earn(?:s|ing|ings|ed)?|mak(?:e|es|ing)|salar(?:y|ies)|hhi|wages?|paid)\b", re.I)
_AGE_RANGE_RX = re.compile(
    r"(?<![\d$,.\-A-Za-z])(?P<a>\d{2})\s*(?:-|to|through|thru)\s*(?P<b>\d{2})(?!\d|,\d|\.\d|\s*(?:k\b|thousand|grand|%|percent|million|m\b))", re.I)
_AGE_CUE = r"(?:aged?|ages|age of|years? old|year[- ]olds?|yo\b|y/o)"
_AGE_FLOOR_RX = re.compile(
    r"\b(?:aged?|ages)\s*(?P<a>\d{2})\s?(?:\+|and (?:over|older|up|above)|or older)|"
    r"\b(?:over|above|older than|at least)\s+(?P<b>\d{2})(?:\s+" + _AGE_CUE + r")|(?<![\d$,.\-])(?P<c>\d{2})\s?\+\s*" + _AGE_CUE, re.I)
_AGE_CEIL_RX = re.compile(r"\b(?:under|below|younger than)\s+(?P<a>\d{2})(?:\s+" + _AGE_CUE + r")|\b(?:aged?|ages)\s+(?:under|below)\s+(?P<b>\d{2})", re.I)

_OCC_FIXED = (
    ('blue_collar', re.compile(
        r"\bblue[- ]?collar\b|\btrades(?:men|man|people|person|women|woman)\b|\bskilled trades?\b|"
        r"\bmanual (?:labor|labour)(?:ers?)?\b|\bworking[- ]class (?:jobs?|occupations?|professions?)\b", re.I)),
    ('white_collar', re.compile(r"\bwhite[- ]?collar\b", re.I)),
)
_OCC_GENERIC_RX = re.compile(
    r"\b(?:who )?(?:work(?:s|ing)?|employed|jobs?|careers?) in (?:the )?(?P<a>[a-z][a-z&/ -]{2,40}?)"
    r"(?=\s*(?:,|;|\?|\.|$|\band\b|\bwith\b|\bwho\b|\bare\b|\bin (?:your|the|our)\b))|"
    r"\b(?P<b>[a-z][a-z-]{2,20}(?: [a-z][a-z-]{2,20})?) (?:occupations?|professions?|workers)\b", re.I)
_OCC_STOP = frozenset(('the', 'their', 'your', 'our', 'these', 'those', 'such', 'other', 'all', 'any', 'some', 'us', 'american',
                       'following', 'and', 'or', 'of', 'in', 'with', 'collar', 'blue', 'white', 'blue-collar', 'white-collar'))

_EXTRAS = (
    ('ethnicity', re.compile(r"\b(hispanic|latino|latina|latinx|black|african[- ]american|white|caucasian|asian)\b(?![- ]collar)", re.I)),
    ('education', re.compile(r"\b(college[- ]educated|college graduates?|college degree|bachelor'?s|graduate degree|"
                             r"high school (?:educated|graduates?|diploma)|no college|some college)\b", re.I)),
    ('parental', re.compile(r"\b(parents?|with (?:kids|children)|has (?:kids|children)|no (?:kids|children)|without (?:kids|children)|"
                            r"childless|moms|mothers|dads|fathers)\b", re.I)),
    ('relationship', re.compile(r"\b(married|single|divorced|widowed|unmarried)\b", re.I)),
    ('location', re.compile(
        r"\b(?:in|from|living in|who live in|based in) (?:the )?(midwest|south|southeast|southwest|northeast|northwest|west coast|"
        r"east coast|sun belt|rust belt|alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|"
        r"hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|"
        r"mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|"
        r"ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|"
        r"washington|west virginia|wisconsin|wyoming|[a-z .]{3,24} (?:dma|metro(?: area)?))\b|\b(rural|urban|suburban)\b", re.I)),
)

# the brand a touchpoint split names
_TOUCH_CUE_RX = re.compile(
    r"\b(?:touch ?points?|touches|interactions?|engagements?|contact|exposure)\s+(?:with|to|on)\s+(?P<b1>[^?.,;!]{2,60})|"
    r"\b(?:touched|engaged with|interacted with|engage with|interact with|been exposed to|come across|visited|shopped (?:at|with)|"
    r"bought from|purchased from|searched for|follow(?:ed|s)?)\s+(?P<b2>[^?.,;!]{2,60})", re.I)
_BRAND_TAIL_RX = re.compile(
    r"\s+(?:thanks?|thank you|please|in (?:the|your|our)\b.*|over (?:the|that)\b.*|during\b.*|within\b.*|(?:in |over |for )?(?:the )?"
    r"(?:last|past|trailing|previous)\b.*|this year\b.*|and (?:how|what|which|who)\b.*|or not\b.*|at all\b.*|so far\b.*|ever\b.*|"
    r"yet\b.*|before\b.*|vs\.?\b.*|versus\b.*|and (?:have|haven'?t|did|didn'?t)\b.*)$", re.I)
_BRAND_STOP = frozenset(('them', 'it', 'this', 'that', 'these', 'those', 'us', 'you', 'your', 'our', 'the', 'a', 'an', 'any',
                         'brand', 'brands', 'the brand', 'this brand', 'that brand', 'the brands', 'each', 'both', 'either'))
_ANAPHOR_RX = re.compile(
    r"\b(?:of|among|within|in|from|out of)\s+(?:them|those|these|this (?:group|segment|profile|cohort|audience)|"
    r"that (?:group|segment|profile|cohort|audience)|the (?:group|segment|same group|same segment))\b|"
    r"\b(?:they|them|this group|that group|this segment|that segment|those (?:men|women|people|guys|folks))\b", re.I)
_BANNED_RX = re.compile(
    r"\b(?:modell?ed|synthetic|synth|estimated?|panel(?:ists?)?|researched|reasoned|derived|calibrated|anchored)\b|[\u2013\u2014]", re.I)


def enabled():
    return str(os.environ.get('PM_SEGMENT_SIZING', '1')).strip().lower() not in ('0', 'false', 'no', 'off')


def _clean(text):
    return ' '.join(_APOS_RX.sub("'", _DASH_RX.sub('-', str(text or ''))).split())


def _norm(s):
    return re.sub(r"[^a-z0-9]+", '', str(s or '').lower())


def _fmt(n):
    return f"{int(n):,}"


def _money(num, unit, other_unit=False, marked=False):
    """Dollars for one money token: '50k', '$50,000', '200' beside '200k'."""
    s = str(num or '').replace(',', '')
    try:
        v = float(s)
    except ValueError:
        return None
    if unit:
        v *= 1000.0
    elif ',' not in str(num or '') and v < 1000 and (other_unit or marked):
        v *= 1000.0        # "$50-$200k", "earning 50 to 200k", "income of 50 to 200": thousands
    return int(round(v))


def _span_free(spans, a, b):
    return not any(a < y and x < b for x, y in spans)


def _income(t):
    """((lo, hi), span) for the income range the words carry, or (None, None).
    A range is money when it carries a dollar sign, a thousands unit, a
    grouped figure, or sits behind an income word."""
    for m in _MONEY_RANGE_RX.finditer(t):
        grouped = ',' in m.group('a') or ',' in m.group('b')
        marked = bool(m.group('d1') or m.group('d2') or m.group('au') or m.group('bu') or grouped)
        cue = bool(_INCOME_CUE_RX.search(t[max(0, m.start() - 48):m.start()]))
        if not (marked or cue):
            continue
        unit_any = bool(m.group('au') or m.group('bu'))
        lo = _money(m.group('a'), m.group('au'), unit_any, True)
        hi = _money(m.group('b'), m.group('bu'), unit_any, True)
        if lo is None or hi is None or lo >= hi or hi < 1000:
            continue
        return (lo, hi), (m.start(), m.end())
    for rx, floor in ((_MONEY_FLOOR_RX, True), (_MONEY_CEIL_RX, False)):
        for m in rx.finditer(t):
            num = m.group('a') or (m.groupdict().get('b') or '')
            unit = m.group('au') or (m.groupdict().get('bu') or '')
            dollar = m.group('d1') or (m.groupdict().get('d2') or '')
            marked = bool(dollar or unit or ',' in num)
            cue = bool(_INCOME_CUE_RX.search(t[max(0, m.start() - 48):m.start()]))
            if not num or not (marked or cue):
                continue
            v = _money(num, unit, False, True)
            if v is None or v < 1000:
                continue
            return ((v, None) if floor else (None, v)), (m.start(), m.end())
    return None, None


def _age(t, spans):
    """(lo, hi) for the age range the words carry, or None. A money
    range is never an age."""
    for m in _AGE_RANGE_RX.finditer(t):
        if not _span_free(spans, m.start(), m.end()):
            continue
        lo, hi = int(m.group('a')), int(m.group('b'))
        if 13 <= lo < hi <= 99:
            return (lo, hi)
    for m in _AGE_FLOOR_RX.finditer(t):
        if not _span_free(spans, m.start(), m.end()):
            continue
        v = int(m.group('a') or m.group('b') or m.group('c'))
        if 13 <= v <= 99:
            return (v, None)
    for m in _AGE_CEIL_RX.finditer(t):
        if not _span_free(spans, m.start(), m.end()):
            continue
        v = int(m.group('a') or m.group('b'))
        if 13 <= v <= 99:
            return (None, v)
    return None


def _occupation(t):
    for slug, rx in _OCC_FIXED:
        if rx.search(t):
            return slug
    for m in _OCC_GENERIC_RX.finditer(t):
        raw = (m.group('a') or m.group('b') or '').strip().lower()
        words = [w for w in re.split(r"[^a-z]+", raw) if w and w not in _OCC_STOP]
        if words:
            return '_'.join(words[:3])
    return ''


def _brand(t):
    """The brand a touchpoint split names, as written, or ''."""
    for m in _TOUCH_CUE_RX.finditer(t):
        raw = (m.group('b1') or m.group('b2') or '').strip()
        raw = _BRAND_TAIL_RX.sub('', raw).strip(" '\"()")
        raw = re.sub(r"^(?:the |a |an |any )?(?:brand |company |retailer |site |app )?", '', raw, flags=re.I).strip()
        raw = re.sub(r"\s+(?:brand|products?|content|posts?|ads?|website|site|app|store|stores|at all)$", '', raw, flags=re.I).strip()
        if not raw or raw.lower() in _BRAND_STOP or len(raw.split()) > 5:
            continue
        if not re.search(r"[A-Za-z]", raw) or _ANAPHOR_RX.fullmatch(raw):
            continue
        return raw
    return ''


def parse(text):
    """What the words carry: {'gender', 'age', 'income', 'occupation',
    'extras', 'brand', 'in_data', 'count', 'anaphor', 'n'}. ``n`` is the
    number of demographic attribute classes named. Never raises."""
    t = _clean(text)
    out = {'gender': '', 'age': None, 'income': None, 'occupation': '', 'extras': {}, 'brand': '', 'in_data': False,
           'count': False, 'anaphor': False, 'n': 0}
    if not t or len(t) > 900:
        return out
    try:
        spans = []
        income, span = _income(t)
        if income:
            out['income'] = income
            spans.append(span)
        mf = _MF_AGE_RX.search(t)
        if mf and mf.group('g').upper() in ('M', 'F', 'P') and _span_free(spans, mf.start(), mf.end()):
            a = int(mf.group('a'))
            b = int(mf.group('b')) if mf.group('b') else None
            if 13 <= a <= 99 and (b is None or a < b <= 99):
                out['age'] = (a, b)
                if mf.group('g').upper() != 'P':
                    out['gender'] = 'male' if mf.group('g').upper() == 'M' else 'female'
        if out['age'] is None:
            out['age'] = _age(t, spans)
        if not out['gender']:
            male, female = bool(_MALE_RX.search(t)), bool(_FEMALE_RX.search(t))
            out['gender'] = 'male' if male and not female else ('female' if female and not male else '')
        out['occupation'] = _occupation(t)
        for name, rx in _EXTRAS:
            m = rx.search(t)
            if m:
                out['extras'][name] = re.sub(r"[^a-z0-9]+", '_', next(g for g in m.groups() if g).lower()).strip('_')
        out['brand'] = _brand(t)
        out['in_data'] = bool(_IN_DATA_RX.search(t))
        out['count'] = bool(_COUNT_RX.search(t))
        out['anaphor'] = bool(_ANAPHOR_RX.search(t))
        out['n'] = sum(1 for k in ('gender', 'age', 'income', 'occupation') if out[k]) + len(out['extras'])
    except Exception:
        pass
    return out


def _plain_segment(t, sig):
    """True when the words are a count over a demographic filter and
    nothing else: no order, no title or behavior of its own, no other
    audience named as the base."""
    if not sig.get('count') or sig.get('n', 0) < MIN_ATTRIBUTES:
        return False
    if _BEHAVIOR_RX.search(t) or _NOT_THIS_RX.search(t):
        return False
    try:
        from . import orders as _orders
        if _orders.is_order(t):
            return False
    except Exception:
        pass
    try:
        # "how many Carhartt buyers are blue-collar men 30-55 ..." is a cut of the
        # Carhartt audience, not a segment of US people: a brand's audience named
        # as the base (never the brand of the touchpoint split) leaves this class
        from . import question_universe as _qu
        split_brand = _norm(sig.get('brand'))
        for phrase, population in _qu.named_universe(t):
            if not population and not (split_brand and split_brand in _norm(phrase)):
                return False
    except Exception:
        pass
    return True


def is_segment_ask(text):
    """True for the question class: a count of the people who fit a
    demographic profile of three or more attributes, with or without a
    brand touchpoint split. The other lanes stand down on this."""
    if not enabled():
        return False
    t = _clean(text)
    if not t or len(t) > 900:
        return False
    return _plain_segment(t, parse(t))


def is_segment_label(text):
    """True when the words are a demographic segment as this module labels
    one (three or more attributes): a subject in its own right."""
    try:
        return parse(text).get('n', 0) >= MIN_ATTRIBUTES
    except Exception:
        return False


def resolve(text, history=None):
    """The segment the ask is about: the one its own words name, or, for
    a follow-up that points back ("how many of them touched Dixxon"),
    the segment the thread's last segment question named. None when the
    ask is not this class."""
    if not enabled():
        return None
    t = _clean(text)
    if not t or len(t) > 900:
        return None
    sig = parse(t)
    if _plain_segment(t, sig):
        return sig
    # a follow-up on the thread's segment: it points back and names a brand
    # split, or asks for the count again in the data or in the US
    if sig.get('n', 0) >= MIN_ATTRIBUTES or not sig.get('anaphor') or _BEHAVIOR_RX.search(t) or _NOT_THIS_RX.search(t):
        return None
    if not (sig.get('brand') and (sig.get('count') or re.search(r"\b(?:touch|engag|interact)", t, re.I))):
        return None
    turns = [x for x in (history or []) if isinstance(x, dict)]
    if not turns or not _is_own_turn(_last_agent(turns)):
        return None
    for turn in reversed(turns[-10:]):
        if str(turn.get('role') or '') != 'user':
            continue
        prior = _clean(turn.get('text') or '')
        psig = parse(prior)
        if _plain_segment(prior, psig):
            # an attribute the follow-up repeats ("of those men") must be the segment's own
            if any(sig.get(k) and sig.get(k) != psig.get(k) for k in ('gender', 'age', 'income', 'occupation')) \
                    or any(psig['extras'].get(k) != v for k, v in (sig.get('extras') or {}).items()):
                return None
            return dict(psig, brand=sig['brand'], in_data=sig['in_data'] or psig['in_data'], followup=True)
    return None


def wants(text, history=None):
    return resolve(text, history) is not None


def _last_agent(turns):
    for turn in reversed(turns):
        if str(turn.get('role') or '') in ('agent', 'assistant'):
            return turn
    return None


def _is_own_turn(turn):
    if not isinstance(turn, dict):
        return False
    meta = turn.get('meta') if isinstance(turn.get('meta'), dict) else {}
    if isinstance(meta.get('segment_sizing'), dict):
        return True
    return TEN_M_PHRASE in str(turn.get('text') or '') and 'household income' in str(turn.get('text') or '')


# ------------------------------------------------------------ the segment
def _range_key(rng):
    lo, hi = rng
    if lo is not None and hi is not None:
        return f"{lo}-{hi}"
    return f"{lo}+" if lo is not None else f"-{hi}"


def segment_key(sig):
    """The stable key for a segment: 'gender:male|age:30-55|hhi:50000-200000|occ:blue_collar'."""
    parts = []
    if sig.get('gender'):
        parts.append(f"gender:{sig['gender']}")
    if sig.get('age'):
        parts.append(f"age:{_range_key(sig['age'])}")
    if sig.get('income'):
        parts.append(f"hhi:{_range_key(sig['income'])}")
    if sig.get('occupation'):
        parts.append(f"occ:{sig['occupation']}")
    for k in sorted(sig.get('extras') or {}):
        parts.append(f"{k}:{sig['extras'][k]}")
    return '|'.join(parts)


def _money_words(v):
    return f"${int(v):,}"


def segment_label(sig):
    """The segment as a reader would say it: 'men aged 30 to 55 with
    household income of $50,000 to $200,000 in blue-collar occupations'."""
    ex = sig.get('extras') or {}
    who = {'male': 'men', 'female': 'women'}.get(sig.get('gender') or '', 'people')
    lead = [(ex[k].replace('_', ' ').title().replace(' American', '-American') if k == 'ethnicity' else ex[k].replace('_', ' '))
            for k in ('ethnicity', 'relationship', 'education') if ex.get(k)]
    label = ' '.join(lead + [who])
    if sig.get('age'):
        lo, hi = sig['age']
        label += f" aged {lo} to {hi}" if lo is not None and hi is not None else (
            f" aged {lo} and over" if lo is not None else f" aged under {hi}")
    if sig.get('income'):
        lo, hi = sig['income']
        label += " with household income of " + (
            f"{_money_words(lo)} to {_money_words(hi)}" if lo is not None and hi is not None else (
                f"{_money_words(lo)} or more" if lo is not None else f"under {_money_words(hi)}"))
    if sig.get('occupation'):
        label += f" in {sig['occupation'].replace('_', '-') if sig['occupation'].endswith('_collar') else sig['occupation'].replace('_', ' ')} occupations"
    if ex.get('parental'):
        label += f", {ex['parental'].replace('_', ' ')}"
    if ex.get('location'):
        loc = ex['location'].replace('_', ' ')
        label += f", {loc}" if loc in ('rural', 'urban', 'suburban') else f", in {loc.title().replace(' Dma', ' DMA')}"
    return label


# -------------------------------------------------------------- the record
_REGISTRY = None
_REGISTRY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'segment_reads.json')


def registry(force=False):
    """The bundled segment reads of record, by key."""
    global _REGISTRY
    if _REGISTRY is None or force:
        try:
            with open(_REGISTRY_PATH, encoding='utf-8') as fh:
                doc = json.load(fh)
            _REGISTRY = {str(r.get('key')): r for r in (doc.get('reads') or []) if isinstance(r, dict) and r.get('key')}
        except Exception:
            _REGISTRY = {}
    return _REGISTRY


def _ledger_entries(key):
    """(live, retired) decision-of-record entries the insights ledger
    holds for this segment key. Hermetic runs read no ledger."""
    if os.environ.get('REGRESSION_TEST_MODE') and not os.environ.get('PM_SEGMENT_SIZING_LEDGER'):
        return [], []
    try:
        import insights_ledger as _il
        doc = _il._load_index() or {}
    except Exception:
        return [], []
    live, retired = [], []
    want = COHORT_PREFIX + key
    for bucket in (doc.get('subjects') or {}).values():
        for e in (bucket.get('entries') or []):
            if not isinstance(e, dict) or str(e.get('route') or '') != ROUTE or str(e.get('cohort') or '') != want:
                continue
            (retired if e.get('retired') else live).append(e)
    return live, retired


def _with_ledger(read):
    """The bundled read with the ledger's figures laid over it: the
    ledger is where an operator corrects a figure in place. A record
    the ledger holds only as retired entries is withdrawn."""
    live, retired = _ledger_entries(str(read.get('key')))
    if not live:
        return None if retired else read
    vals = {}
    for e in live:
        for m in (e.get('metrics') or []):
            if isinstance(m, dict) and isinstance(m.get('value'), (int, float)):
                vals[str(m.get('name'))] = m
    out = json.loads(json.dumps(read))

    def _pair(name, slot):
        m = vals.get(name)
        if m and float(m['value']) >= 1:
            slot['us'] = int(round(float(m['value'])))
            slot['in_data'] = int(m.get('sample_people') or _pj.unproject(slot['us']))

    _pair('segment_us', out['segment'])
    for bkey, b in (out.get('brands') or {}).items():
        _pair(f"{bkey}_touch_us", b['touch'])
        _pair(f"{bkey}_no_touch_us", b['no_touch'])
        # a corrected pair keeps the partition exact: the no-touch side is the remainder
        b['no_touch']['us'] = out['segment']['us'] - b['touch']['us']
        b['no_touch']['in_data'] = out['segment']['in_data'] - b['touch']['in_data']
        b['touch_pct'] = round(100.0 * b['touch']['us'] / out['segment']['us'], 1)
        b['no_touch_pct'] = round(100.0 - b['touch_pct'], 1)
    return out


def record_for(sig):
    """(read, brand) for a segment on record: the read, and the brand
    block when the ask names a brand the read holds (matched on the
    brand's own spellings, nothing near-spelled). (None, None) when the
    segment is not on record; (read, None) when the brand is not."""
    read = registry().get(segment_key(sig))
    if not read:
        return None, None
    read = _with_ledger(read)
    if not read:
        return None, None
    want = _norm(sig.get('brand'))
    if not want:
        return read, None
    for b in (read.get('brands') or {}).values():
        if want in {_norm(a) for a in ([b.get('name')] + list(b.get('aliases') or []))}:
            return read, b
    return read, None


# ---------------------------------------------------------------- the reply
def _segment_lines(read, in_data_first):
    us, n = read['segment']['us'], read['segment']['in_data']
    label = read['label']
    if in_data_first:
        return (f"{_fmt(n)} of {TEN_M_PHRASE} are {label}. "
                f"Projected to the US, that is {_fmt(us)} people.")
    return (f"{_fmt(us)} US people are {label}. "
            f"That is {_fmt(n)} of {TEN_M_PHRASE}.")


def _definition_lines(read, brand=None):
    lines = [INCOME_LINE] if 'hhi:' in str(read.get('key')) else []
    if 'occ:blue_collar' in str(read.get('key')):
        lines.append(BLUE_COLLAR_LINE)
    if brand:
        lines.append(TOUCHPOINT_LINE.format(brand=brand['name']))
    lines.append(f"Window: {read['window']['label']}.")
    return lines


def compose(read, brand=None, in_data_first=True):
    """The reply for a segment on record, with the brand split when one
    is asked for and held."""
    parts = [_segment_lines(read, in_data_first)]
    if brand:
        t, nt = brand['touch'], brand['no_touch']
        name = brand['name']
        parts.append(
            f"{name} touchpoints in that group:\n"
            f"- At least one touchpoint with {name}: {_fmt(t['in_data'])} in the data, {_fmt(t['us'])} US people, "
            f"{brand['touch_pct']:.1f}% of the group.\n"
            f"- No touchpoint with {name}: {_fmt(nt['in_data'])} in the data, {_fmt(nt['us'])} US people, "
            f"{brand['no_touch_pct']:.1f}% of the group.\n"
            f"The two add up to the group on both counts.")
    parts.append("How these are counted:\n" + '\n'.join(f"- {x}" for x in _definition_lines(read, brand)))
    return '\n\n'.join(parts)


def _figures(read, brand=None):
    figs = [{'label': 'people in the group, US', 'value': read['segment']['us'], 'unit': 'people'},
            {'label': 'people in the group, in the data', 'value': read['segment']['in_data'], 'unit': 'people in the data'}]
    if brand:
        name = brand['name']
        figs += [{'label': f"with a {name} touchpoint, US", 'value': brand['touch']['us'], 'unit': 'people'},
                 {'label': f"with a {name} touchpoint, in the data", 'value': brand['touch']['in_data'], 'unit': 'people in the data'},
                 {'label': f"no {name} touchpoint, US", 'value': brand['no_touch']['us'], 'unit': 'people'},
                 {'label': f"no {name} touchpoint, in the data", 'value': brand['no_touch']['in_data'], 'unit': 'people in the data'},
                 {'label': f"share of the group with a {name} touchpoint", 'value': brand['touch_pct'], 'unit': 'percent'}]
    return figs


def answer(text, history=None, ctx=None):
    """The reply payload for a segment on record, or None: when the ask
    is not this class, when the segment is not on record, or when it
    names a brand the record does not hold (the read lane takes those,
    framed by ``framing_lines``)."""
    del ctx      # the open page is never the base of a segment the words name
    try:
        sig = resolve(text, history)
        if not sig:
            return None
        read, brand = record_for(sig)
        if not read or (sig.get('brand') and not brand):
            return None
        reply = compose(read, brand, in_data_first=bool(sig.get('in_data')))
        detail = {'found': True, 'key': read['key'], 'id': read.get('id'), 'window': dict(read['window']),
                  'segment': dict(read['segment']), 'in_data_first': bool(sig.get('in_data')),
                  'followup': bool(sig.get('followup'))}
        if brand:
            detail['brand'] = {'name': brand['name'], 'touch': dict(brand['touch']), 'no_touch': dict(brand['no_touch']),
                               'touch_pct': brand['touch_pct'], 'no_touch_pct': brand['no_touch_pct']}
        return {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False,
                'deck_angle': None, 'subject': read.get('subject') or read['label'], 'figures': _figures(read, brand),
                'segment_sizing': detail}
    except Exception:
        return None


# ------------------------------------------------- a segment not on record
def framing_lines(text):
    """The frame the read lane holds a segment question to when the
    segment is not on record. [] when the words are not this class."""
    try:
        t = _clean(text)
        sig = parse(t)
        if not enabled() or not _plain_segment(t, sig):
            return []
        label = segment_label(sig)
        lines = [
            "SEGMENT SIZING (the question sizes a demographic segment):",
            f"- The segment is ONE joint group of US people: {label}. Size the group as a whole from how its attributes "
            "sit together in the US population (occupation by sex and age, income by occupation); never multiply "
            "separate shares as if they were independent, and never size the attributes one by one.",
            "- " + INCOME_LINE + (" " + BLUE_COLLAR_LINE if sig.get('occupation') == 'blue_collar' else ''),
            "- State two counts for every figure: the US people, and the count inside the 10 million US consumers we "
            "measure, which is the US count multiplied by 10,000,000 and divided by 329,900,000."
            + (" The question asks about the data, so the count inside the 10 million leads and the US count sits beside it."
               if sig.get('in_data') else ""),
            "- When a choice must be made, take the lower figure. No count ends in a zero.",
            "- No profile, twin or file on the dashboard is the base; none of the words in the filter names a library subject.",
        ]
        if sig.get('brand'):
            lines.append(
                f"- The brand is {sig['brand']}, exactly as written; a near-spelled name is a different brand. Split the "
                f"segment two ways: people with at least one {sig['brand']} touchpoint in the window (search, social, "
                "media, shopping, or the brand's own site or app) and people with none. The two counts add up to the "
                "segment exactly, on the US count and on the count inside the 10 million, and each carries its share "
                "of the segment. The count with a touchpoint is never larger than the brand's whole US reach.")
        return lines
    except Exception:
        return []


# ----------------------------------------------------------- the ledger side
def ledger_payloads(read=None):
    """The insights-ledger banking calls for the bundled reads (or one):
    a list of keyword dicts for ``insights_ledger.bank_record``. Pure;
    scripts/bank_segment_reads.py is the only caller that writes."""
    out = []
    for r in ([read] if read else list(registry().values())):
        brands = list((r.get('brands') or {}).items())
        metrics = [{'name': 'segment_us', 'label': 'People in the group, US', 'value': r['segment']['us'], 'unit': 'people',
                    'sample_people': r['segment']['in_data'],
                    'definition': f"the group is US {r['label']}; the count in the data is the US count x 10,000,000 / 329,900,000"}]
        asks = []
        for bkey, b in brands:
            name = b['name']
            metrics += [
                {'name': f"{bkey}_touch_us", 'label': f"In the group with at least one {name} touchpoint, US"[:90],
                 'value': b['touch']['us'], 'unit': 'people', 'sample_people': b['touch']['in_data'],
                 'definition': f"people in the group with at least one {name} touchpoint in the window (search, social, media, shopping, {name} site or app)"},
                {'name': f"{bkey}_no_touch_us", 'label': f"In the group with no {name} touchpoint, US"[:90],
                 'value': b['no_touch']['us'], 'unit': 'people', 'sample_people': b['no_touch']['in_data'],
                 'definition': f"the rest of the group: no {name} touchpoint in the window; with and without add up to the group"},
                {'name': f"{bkey}_touch_pct", 'label': f"Share of the group with a {name} touchpoint"[:90],
                 'value': b['touch_pct'], 'unit': 'pct', 'definition': f"{name} touchpoint count over the group"},
            ]
            if isinstance(b.get('brand_us_reach'), dict):
                metrics.append({'name': f"{bkey}_us_reach", 'label': f"All US people with at least one {name} touchpoint"[:90],
                                'value': b['brand_us_reach']['us'], 'unit': 'people', 'sample_people': b['brand_us_reach']['in_data'],
                                'definition': f"all US people with at least one {name} touchpoint in the window; the group's count sits inside it"})
            asks.append((f"how many {r['label']} are in your database, and how many have and have not had touchpoints with {name}",
                         compose(r, b, True)))
            asks.append((f"how many {r['label']} have had a touchpoint with {name}", compose(r, b, False)))
        asks.append((f"how many {r['label']} are there in the US", compose(r, None, False)))
        first_brand = brands[0][1] if brands else None
        aliases = [r['label'], r.get('subject') or ''] + list(r.get('aliases') or []) \
            + [a for _k, b in brands for a in ([b['name']] + list(b.get('aliases') or [])[:3])]
        out.append({
            'subject': r.get('subject') or r['label'], 'source': SOURCE, 'metrics': metrics,
            'question': f"how many people in your database are {r['label']}"
                        + (f", and how many have and have not had touchpoints with {first_brand['name']}" if first_brand else ''),
            'reply': compose(r, first_brand, True),
            'window_start': r['window']['start'], 'window_end': r['window']['end'], 'window_label': r['window']['label'],
            'universe': f"US {r['label']}"[:220], 'cohort': COHORT_PREFIX + r['key'], 'metric_family': 'audience',
            'questions': asks, 'subject_aliases': [a for a in aliases if a], 'retire_others': False,
        })
    return out


def is_clean(reply):
    """True when a reply carries none of the words this lane never says."""
    return not _BANNED_RX.search(str(reply or ''))
