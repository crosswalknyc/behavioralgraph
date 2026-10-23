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
