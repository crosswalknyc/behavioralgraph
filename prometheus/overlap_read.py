"""Overlap reads are a priced custom ask (2026-10-21, Bria).

'Of those viewers who watch Law & Order: SVU on Peacock, how many
subscribe to Netflix?' shipped a compiled overlap (usage vs payment,
ad-free vs ad-supported, the competitive stack) at the metered chat
rate because the title's profile was open. Opening a file does not
make a new cross-service count chat. Quote Custom Ask ($500) first,
charge on confirm, then generate.

A size question is its own $100 read (brain 26, Jenna 2026-10-21).
A Tania-style split that only asks who is 'on' a service, with no
subscribe / pay / incremental wording, stays the free cross (brain
48). A breakdown of a figure they already paid for stays the $100
derived read.
"""
from __future__ import annotations

import re

# Bria: of those viewers who watch X, how many subscribe to Y
_SUBSCRIBE_RX = re.compile(
    r"\b(?:subscribe[sd]?(?:\s+to)?|subscription|subscribers?|"
    r"pay(?:ing|s|ment)?\s+for|paid\s+for|"
    r"(?:hold|have|carry)\s+(?:an?\s+)?(?:active\s+)?(?:paid\s+)?"
    r"(?:[a-z0-9+./'-]+\s+){0,3}subscription)\b",
    re.I)
_OF_AUDIENCE_RX = re.compile(
    r"\b(?:of\s+(?:those|these|the|them)\b|"
    r"among\s+(?:those|these|the|them|whom)\b|"
    r"out\s+of\s+(?:those|these|the|them)\b|"
    r"how many\s+of\s+(?:those|these|them|the)\b|"
    r"what\s+(?:share|percent|percentage|portion)\s+of\b)",
    re.I)
_VIEWER_RX = re.compile(
    r"\b(?:viewers?|watchers?|audience|people who|users|fans|readers?|"
    r"listeners?|cohort)\b",
    re.I)
_HOW_MANY_SUB_RX = re.compile(
    r"\bhow many\b.{0,120}\b(?:subscribe|subscribers?|subscription|"
    r"pay(?:ing)?\s+for)\b",
    re.I)
_INCREMENTAL_RX = re.compile(
    r"\b(?:how\s+incremental(?:ly)?|incrementality|"
    r"incremental\s+(?:to|for|reach|lift|viewers?))\b",
    re.I)
_NEXT_WATCH_RX = re.compile(
    r"\b(?:what\s+(?:did|do|have)\b.{0,120}\b(?:watch(?:ed)?|stream(?:ed)?|read)\b"
    r".{0,80}\b(?:after|next|then|30[- ]days?|thirty\s+days?|t30d|post[- ]watch))\b",
    re.I)
_NOT_THIS_RX = re.compile(
    r"\b(?:what does (?:this|that|it|the (?:number|figure|percent))\s+mean|"
    r"craziest insights|"
    r"how big is|audience size|sample[- ]size|incidence|"
    r"where (?:do|can) i (?:find|see|get))\b",
    re.I)


# Bria (2026-10-22): of the people who watched X on A (in a window), how
# many also watched it on B. A co-viewing overlap across two services is
# the same class as the subscribe overlap: a custom ask, never the $500
# viewership read and never the number explainer.
_WATCH_VERB = r"(?:watch(?:ed|es|ing)?|stream(?:ed|s|ing)?|view(?:ed|s|ing)?|saw|seen|see|play(?:ed|s|ing)?)"
_ALSO_PIVOT_RX = re.compile(
    r"\b(?:how\s+many\s+(?:of\s+(?:those|these|them)\s+)?)?(?:also|and\s+also|and\s+then|and)\s+" + _WATCH_VERB + r"\s+(?P<obj>.+?)\s*[?.!]*\s*$", re.I)
_WHO_WATCHED_RX = re.compile(
    r"\b(?:who|that|which)\s+" + _WATCH_VERB + r"\s+(?P<rest>.+?)\s*,?\s*(?:how\s+many(?:\s+of\s+(?:those|these|them))?)?\s*,?\s*$", re.I)
_WINDOW_TAIL_RX = re.compile(
    r"\s+(?:from|in|between|during|across|over|for)\s+(?:the\s+)?(?:(?:early|late|mid)[- ]?)?"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|q[1-4]|fy|fiscal|(?:19|20)\d{2}|\d{1,2}/(?:19|20)\d{2})[^,]*$", re.I)
_OBJ_PRONOUN_RX = re.compile(
    r"^(?:it|them|that|this|the\s+(?:same\s+)?(?:series|show|title|film|movie|program|episodes?|season))\s+on\s+(?P<b>.+)$", re.I)
_SERVICE_TOKEN_RX = re.compile(r"^(?P<b>[A-Za-z0-9+&'.\- ]{2,40}?)(?=\s+(?:from|in|between|during|across|over|for)\b|$)", re.I)


def also_watched(text):
    """The two-service co-viewing ask: {'title', 'a', 'b', 'window',
    'window_b'} or None. 'How many viewers who watched Law & Order: SVU
    on Peacock from September 2025 to March 2026 also watched the series
    on Hulu?' -> title 'Law & Order: SVU', a 'Peacock', b 'Hulu', window
    'September 2025 to March 2026'. 'Of those viewers who watched
    Stranger Things in November and December 2025, how many also watched
    Peacock in September and October 2025?' -> a '', b 'Peacock', two
    windows. None when the words do not carry both halves."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 800:
        return None
    piv = _ALSO_PIVOT_RX.search(t)
    if not piv:
        return None
    left = t[:piv.start()].rstrip(' ,')
    who = _WHO_WATCHED_RX.search(left)
    if not who:
        return None
    try:
        from . import stated_numbers as _sn
        win_words = _sn.window_in_words
    except Exception:  # pragma: no cover
        def win_words(_s):
            return ''
    rest = who.group('rest').strip(' ,')
    window = win_words(rest)
    core = _WINDOW_TAIL_RX.sub('', rest).strip(' ,') if window else rest
    title, a = core, ''
    m_on = list(re.finditer(r"\s+on\s+(?=[A-Z0-9])", core))
    if m_on:
        last = m_on[-1]
        title, a = core[:last.start()].strip(), core[last.end():].strip(' ,')
    obj = piv.group('obj').strip(' ,')
    window_b = win_words(obj)
    obj_core = _WINDOW_TAIL_RX.sub('', obj).strip(' ,') if window_b else obj
    m = _OBJ_PRONOUN_RX.match(obj_core)
    if m:
        b = m.group('b').strip(' ,')
    else:
        m2 = re.search(r"\s+on\s+(?=[A-Z0-9])", obj_core)
        if m2 and obj_core[:m2.start()].strip().lower() in (title.lower(), 'the series', 'the show', 'it'):
            b = obj_core[m2.end():].strip(' ,')
        else:
            m3 = _SERVICE_TOKEN_RX.match(obj_core)
            b = m3.group('b').strip(' ,') if m3 else ''
    if not title or not b or len(title) > 80 or len(b) > 40:
        return None
    if b.lower() == a.lower():
        return None
    return {'title': title, 'a': a, 'b': b, 'window': window, 'window_b': window_b}


def also_watched_lead(text):
    """The offer's first sentence for a two-service co-viewing ask, or
    ''. Names the title, both services and the window as asked, so the
    reader sees the question read back, never 'watched <title> viewers
    on <service> across one year'."""
    p = also_watched(text)
    if not p:
        return ''
    title, a, b = p['title'], p['a'], p['b']
    first = f"watched {title}" + (f" on {a}" if a else '') + (f" {('from ' if ' to ' in p['window'] else 'in ')}{p['window']}" if p['window'] else '')
    if a and not p['window_b']:
        second = f"also watched it on {b}" + (" in the same window" if p['window'] else '')
        tail = f", with the {a} only and {b} only remainders"
    else:
        second = f"also watched {b}" + (f" {('from ' if ' to ' in p['window_b'] else 'in ')}{p['window_b']}" if p['window_b'] else '')
        tail = ''
    return f"I can pull how many of the people who {first} {second}{tail}."


# ---------------------------------------------------------------------------
# The header of a two-audience read names the two audiences (Grace Yin,
# Amazon, 2026-11-08). "What is the overlap between Hazbin Hotel viewers
# and viewers of adult animation and anime on Netflix, Crunchyroll and
# HIDIVE?" shipped under "On people who watched Hazbin Hotel on Netflix in
# the window:" - the first service anywhere in the ask was pinned on the
# title. The header is "On <A> and <B>:" in the ask's own words; a service
# rides only on the side whose words (or whose bound audience) name it.
# ---------------------------------------------------------------------------
_PEOPLE_NOUN_RX = re.compile(
    r"\b(?:viewers?|watchers?|fans?|audiences?|subscribers?|subs|members?|buyers?|shoppers?|users?|listeners?|"
    r"readers?|players?|customers?|sign-?ups?|households?|people|engagers?|followers?|streamers?|cohorts?|bases?|"
    r"gamers?|renters?|owners?|visitors?|voters?|attendees?|consumers?|purchasers?|watchers?)\b", re.I)
_OVERLAP_BETWEEN_RX = re.compile(
    r"\b(?:overlap|intersection|crossover|cross-?over|shared\s+audience|common\s+audience|co-?viewing|audience\s+overlap)\s+"
    r"(?:between|of|among|across|for)\s+(?P<rest>.+)$", re.I)
_WHO_OVERLAP_RX = re.compile(
    r"^(?P<a>.+?)\s+(?:(?:who|that|which)\s+)?(?:overlap(?:s|ped)?|overlapping|intersect(?:s|ing)?|cross(?:es)?\s+over)\s+with\s+(?P<b>.+)$", re.I)
_DO_OVERLAP_RX = re.compile(
    r"\b(?:how\s+(?:much|many|big|large|far)|what\s+(?:share|percent(?:age)?|portion|fraction|part))\s+(?:do|does|of|is)\s+(?P<rest>.+?)\s+overlap(?:s|ping)?\b", re.I)
_SIDE_LEAD_RX = re.compile(
    r"^(?:(?:what|how)\s+(?:is|are|many|much|big|large)\s+(?:the\s+)?|(?:the\s+)?(?:overlap|intersection|crossover)\s+(?:between|of|among)\s+|"
    r"(?:do|does|did|can|could|would|please|tell\s+me|show\s+me|give\s+me|size|measure|pull|run)\s+)+", re.I)
_SIDE_TAIL_RX = re.compile(
    r"(?:\s+(?:overlap(?:s|ping)?(?:\s+with\s+each\s+other)?|intersect|in\s+(?:the|that|this)\s+window|right\s+now|today|please|"
    r"and\s+(?:how|what)\s+.*|by\s+(?:size|share|count)|ranked\s+by\s+.*|as\s+of\s+.*)|\s*[?.!,;:]+)+$", re.I)
_SIDE_MAX = 110


def _clean_side(s, window_tail=None):
    s = ' '.join(str(s or '').split()).strip(' ,;:.?!')
    s = _SIDE_LEAD_RX.sub('', s)
    s = _SIDE_TAIL_RX.sub('', s).strip(' ,;:.?!')
    if window_tail:
        s = window_tail(s)
    s = re.sub(r"^(?:an?)\s+", 'the ', s, flags=re.I)         # 'a Hazbin Hotel sign-ups audience' reads 'the ...'
    s = re.sub(r"^(?:all|every|any)\s+(?:of\s+)?(?:the\s+)?", '', s, flags=re.I)
    return s.strip(' ,;:.?!')


def _side_ok(s):
    if not s or len(s) > _SIDE_MAX or len(s) < 2:
        return False
    low = s.lower()
    if re.match(r"^(?:of|on|in|for|to|with|at|by|from|the|a|an|and|or)$", low):
        return False
    if re.search(r"\b(?:of|on|in|for|to|with|at|by|from|and|or|the|a|an)$", low):
        return False
    return bool(re.search(r"[A-Za-z]", s))


def _split_two(rest, window_tail=None):
    """(A, B) out of 'A and B', 'A with B', 'A vs B': the first split where
    both sides read as audiences; the first clean split when neither
    side carries a people noun (two titles)."""
    r = ' '.join(str(rest or '').split())
    cands = []
    for m in re.finditer(r"\s+(?:and|with|vs\.?|versus|&)\s+", r, re.I):
        a, b = _clean_side(r[:m.start()], window_tail), _clean_side(r[m.end():], window_tail)
        if _side_ok(a) and _side_ok(b):
            cands.append((a, b))
    if not cands:
        return None
    nouns = [(a, b) for a, b in cands if _PEOPLE_NOUN_RX.search(a) and _PEOPLE_NOUN_RX.search(b)]
    for a, b in nouns:
        # the second audience opens on a name, a determiner or its people
        # noun ('viewers of adult animation and anime ...', 'the Hazbin
        # Hotel sign-ups audience', 'Helluva Boss viewers'), never on the
        # tail of the first one's list ('anime and Hazbin Hotel viewers')
        if b[:1].isupper() or re.match(r"^(?:the|those|these|that|this|its|their|us|people)\b", b, re.I) or _PEOPLE_NOUN_RX.match(b):
            return a, b
    return nouns[0] if nouns else cands[0]


def two_audiences(text):
    """(A, B), the two audiences an overlap ask names, in the ask's own
    words, or None. 'What is the overlap between Hazbin Hotel viewers and
    viewers of adult animation and anime on Netflix, Crunchyroll and
    HIDIVE?' -> ('Hazbin Hotel viewers', 'viewers of adult animation and
    anime on Netflix, Crunchyroll and HIDIVE'); 'anime viewers who
    overlap with a Hazbin Hotel sign-ups audience' -> ('anime viewers',
    'the Hazbin Hotel sign-ups audience'). A window named at the end of
    a side comes off it (it is the read's window, not the audience)."""
    t = ' '.join(str(text or '').split()).strip()
    if not t or len(t) > 800:
        return None
    def _tail(s):
        # a window named at the end of a side ('... viewers in 2025',
        # '... from September 2025 to March 2026') is the read's window
        m = _WINDOW_TAIL_RX.search(s)
        if not m:
            return s
        tail = m.group(0)
        dated = False
        try:
            from . import stated_numbers as _sn
            dated = bool(_sn.window_in_words(tail))
        except Exception:  # pragma: no cover
            dated = False
        if not dated:
            try:
                from . import inline_dates as _idw
                dated = _idw.parse_inline_window(tail) is not None
            except Exception:  # pragma: no cover
                dated = False
        return s[:m.start()].strip(' ,') if dated else s
    m = _OVERLAP_BETWEEN_RX.search(t)
    if m:
        pair = _split_two(m.group('rest'), _tail)
        if pair:
            return pair
    m = _DO_OVERLAP_RX.search(t)
    if m:
        pair = _split_two(m.group('rest'), _tail)
        if pair:
            return pair
    m = _WHO_OVERLAP_RX.match(t.rstrip(' ?.!'))
    if m:
        a, b = _clean_side(m.group('a'), _tail), _clean_side(m.group('b'), _tail)
        if _side_ok(a) and _side_ok(b):
            return a, b
    return None


_BOUND_PLATFORM_RX = re.compile(r"\s+on\s+(?P<p>[A-Z][\w+' ]{1,30}?)\s*$|\s*\((?:on\s+)?(?P<p2>[^()]{2,40})\)\s*$")


def _tokens(s):
    return set(re.findall(r"[a-z0-9]+", str(s or '').lower())) - {'the', 'a', 'an', 'of', 'and', 'on', 'in', 'to', 'for', 'with'}


def two_audience_label(text, bound=None):
    """'<A> and <B>' for the header of a two-audience read, or '' when the
    ask names no pair. Each side is the ask's own words, so a service
    stays on the side that named it and never crosses to the other. A
    bound audience whose display name carries its service ('Young
    Sheldon on Netflix', 'The Love Hypothesis (Amazon Prime Video)')
    lends that service to the side that names it when the side has
    none; a bound audience without one lends nothing."""
    pair = two_audiences(text)
    if not pair:
        return ''
    sides = list(pair)
    for name in (bound or []):
        name = ' '.join(str(name or '').split())
        if not name:
            continue
        bm = _BOUND_PLATFORM_RX.search(name)
        if not bm:
            continue
        plat = (bm.group('p') or bm.group('p2') or '').strip()
        head = name[:bm.start()].strip()
        if not plat or not head:
            continue
        for i, side in enumerate(sides):
            if _tokens(head) and _tokens(head) <= _tokens(side) and not re.search(r"\bon\s+[A-Z]", side) \
                    and plat.lower() not in side.lower():
                sides[i] = f"{side} on {plat}"
    return f"{sides[0]} and {sides[1]}"


_OPENER_RX = re.compile(r"^On (?P<aud>[^\n]{3,220}?):\n")
_BASE_LINE_RX = re.compile(r"^(?P<lead>- Base: )(?P<aud>[^\n]{3,220}?)(?P<tail>, measured across [^\n]*)$", re.M)


def repair_shipped_header(reply, question, subject='', bound=None):
    """A shipped overlap reply with a one-sided universe on its opener
    ('On people who watched Hazbin Hotel on Netflix in the window:') and
    its Base line gets the two-audience label in both places. Nothing
    else in the reply changes: no figure, no line count. The input comes
    back when the question is not a two-audience ask or the reply has no
    opener to repair. Pure; used by the in-place fix and its test."""
    text = str(reply or '')
    label = two_audience_label(question, bound=list(bound or []) + ([subject] if subject else []))
    if not label or not text:
        return reply
    m = _OPENER_RX.match(text)
    if not m:
        return reply
    old = m.group('aud')
    if old == label:
        return reply
    out = f"On {label}:\n" + text[m.end():]
    out = _BASE_LINE_RX.sub(lambda b: f"{b.group('lead')}{label}{b.group('tail')}" if b.group('aud') == old else b.group(0), out)
    return out


def is_overlap_read(text):
    """True for a new-numbers overlap: of audience A, how many
    subscribe to / pay for B; incrementality of X to Y; what that
    audience watched next; of the people who watched X on A, how many
    also watched it on B. False for size, insights, and 'what does this
    number mean'."""
    t = ' '.join(str(text or '').split())
    if not t or len(t) > 800:
        return False
    if _NOT_THIS_RX.search(t):
        return False
    if _INCREMENTAL_RX.search(t) or _NEXT_WATCH_RX.search(t):
        return True
    if also_watched(t):
        return True
    if _HOW_MANY_SUB_RX.search(t) and (_OF_AUDIENCE_RX.search(t) or _VIEWER_RX.search(t)):
        return True
    if _OF_AUDIENCE_RX.search(t) and _SUBSCRIBE_RX.search(t):
        return True
    return False
