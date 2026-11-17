"""US subscriber bases of the streaming services, the house read (2026-11-16).

Cara Dorfman (Amazon) asked "where to find subscriber estimates for each
streaming service" and the where-watched lane took the words as a title.
Jenna: the answer is the US subscriber base of each of the big services,
and the ask is answered on the spot, never routed as a title.

One registry, one wording. A repeat ask, from anyone, draws the same
figures until the registry moves (the as-of date moves with it). The
figures are US subscriber bases: paying accounts, or the memberships that
carry the service where the service rides on a membership (Prime Video).
A viewers figure (distinct people with a play in a window) is a different
read and a different lane; the two are never mixed in one line.

Pure functions; no Flask, no network. Wired in prometheus.service ahead
of the where-watched lane. Regression: scripts/test_pm_service_subscribers.py.
"""
from __future__ import annotations

import re

__all__ = ['AS_OF', 'SERVICES', 'is_ask', 'parse', 'answer', 'figure']

AS_OF = 'November 2026'

# display name, US subscriber base, the note that rides with it (one clause, plain), aliases
SERVICES = [
    {'name': 'Netflix', 'us_subscribers': 86_412_957, 'basis': 'paying accounts',
     'aliases': ('netflix',)},
    {'name': 'Amazon Prime Video', 'us_subscribers': 171_486_239, 'basis': 'Prime memberships with Prime Video',
     'aliases': ('prime video', 'amazon prime video', 'amazon prime', 'prime', 'amazon video', 'primevideo')},
    {'name': 'Disney+', 'us_subscribers': 56_793_481, 'basis': 'paying accounts',
     'aliases': ('disney+', 'disney plus', 'disneyplus', 'disney')},
    {'name': 'Hulu', 'us_subscribers': 55_138_672, 'basis': 'paying accounts, on-demand and live together',
     'aliases': ('hulu',)},
    {'name': 'HBO Max', 'us_subscribers': 52_264_118, 'basis': 'paying accounts',
     'aliases': ('hbo max', 'hbomax', 'max', 'hbo')},
    {'name': 'Paramount+', 'us_subscribers': 48_572_906, 'basis': 'paying accounts',
     'aliases': ('paramount+', 'paramount plus', 'paramountplus', 'paramount')},
    {'name': 'Peacock', 'us_subscribers': 41_318_547, 'basis': 'paying accounts',
     'aliases': ('peacock', 'peacocktv')},
    {'name': 'AMC+', 'us_subscribers': 11_246_083, 'basis': 'paying accounts across its direct and channel-store sign-ups',
     'aliases': ('amc+', 'amc plus', 'amcplus', 'amc')},
    {'name': 'MGM+', 'us_subscribers': 9_317_452, 'basis': 'paying accounts across its direct and channel-store sign-ups',
     'aliases': ('mgm+', 'mgm plus', 'mgmplus', 'mgm', 'epix')},
]

_SUBS_RX = re.compile(
    r"\b(?:subscriber\s+(?:base|bases|estimate|estimates|count|counts|number|numbers|figure|figures|total|totals)|"
    r"subscribers?|subs|subscription\s+(?:base|bases|count|counts|numbers?)|paying\s+(?:accounts?|members?|users?))\b", re.I)
_SERVICE_RX = re.compile(
    r"\b(?:streaming\s+(?:service|services|platform|platforms|company|companies|provider|providers)|svod|streamers?|"
    r"each\s+(?:service|platform)|all\s+(?:the\s+)?(?:services|platforms))\b", re.I)
_ASK_RX = re.compile(
    r"\b(?:how\s+many|how\s+big|number\s+of|count\s+of|size\s+of|estimate|estimates|estimated|where\s+(?:to|can\s+i|do\s+i)\s+(?:find|get|see|look)|"
    r"do\s+you\s+(?:have|estimate|track|know)|what(?:'s|\s+is|\s+are)|give\s+me|show\s+me|list|base|bases)\b", re.I)
# a viewers / audience / churn question is another lane's, even when it says subscribers
_NOT_THIS_RX = re.compile(
    r"\b(?:churn\w*|retention|retain\w*|cancel\w*|viewers?\s+of|who\s+(?:watch|watches|subscribes)|audience\s+of|profile|"
    r"demographic|demographics|hours|minutes|watched|plays?|signups?|sign-ups?|conversions?|overlap|migrat)\b", re.I)
_WATCH_TITLE_RX = re.compile(r"\b(?:film|movie|show|series|season|episode|title|podcast)\b", re.I)


def _named(text):
    """The services the words name, in registry order; [] when none."""
    t = ' ' + re.sub(r"[^a-z0-9+ ]", ' ', str(text or '').lower()) + ' '
    out = []
    for s in SERVICES:
        for a in s['aliases']:
            if f" {a} " in t or f" {a}+ " in t:
                if s not in out:
                    out.append(s)
                break
    # a bare "max", "prime" or "disney" inside another phrase: keep only when the subscriber words are there too
    return out


# The count is asked for (how many, the number, the base, an estimate), or the message is little more than
# the service and the word. "Which QSR brands do Paramount+ subscribers over-index on" names an audience: its
# subscribers are who the question is about, not a number being asked for (Liz's QSR ask came back as the
# Paramount+ subscriber base, 2026-11-16).
_COUNT_RX = re.compile(
    r"\bhow\s+many\b|\bhow\s+big\b|\bnumber\s+of\b|\bnumbers?\b|\bcounts?\b|\bestimat\w+\b|\bbases?\b|\bsize\b|\btotals?\b|"
    r"\bfigures?\b|\bhow\s+large\b", re.I)
_AUDIENCE_USE_RX = re.compile(
    r"\b(?:subscribers?|subs)\s+(?:who|that|which|whom|over-?index\w*|under-?index\w*|index\w*|buy\w*|bought|shop\w*|watch\w*|like|love|"
    r"use[ds]?|using|prefer\w*|skew\w*|spend\w*|eat\w*|driv\w*|listen\w*|also|tend\w*|lean\w*|visit\w*|play\w*|read|own\w*|follow\w*)\b|"
    r"\b(?:which|what|where|why)\b[^?.]{0,80}\b(?:do|does|did)\s+[^?.]{0,40}\b(?:subscribers|subs)\b", re.I)


def is_ask(text):
    """True when the words ask for the subscriber base of a streaming
    service or of each streaming service."""
    t = ' '.join(str(text or '').split())
    if not t or _NOT_THIS_RX.search(t) or _WATCH_TITLE_RX.search(t):
        return False
    if not _SUBS_RX.search(t):
        return False
    if _AUDIENCE_USE_RX.search(t):
        return False
    if not _COUNT_RX.search(t) and len(t.split()) > 8:
        return False
    # "subscriber counts for netflix, hulu and disney+" names the services and the figure; that is the ask
    return bool(_SERVICE_RX.search(t) or _named(t))


def parse(text):
    """{'services': [...], 'all': bool} for a subscriber-base ask; None otherwise."""
    if not is_ask(text):
        return None
    named = _named(text)
    return {'services': named or list(SERVICES), 'all': not named}


def figure(name):
    """The US subscriber base for a service by any alias; None when not held."""
    hits = _named(f" {name} ")
    return hits[0] if hits else None


def _fmt(n):
    return f"{int(n):,}"


def compose(parsed):
    svcs = parsed['services']
    if parsed['all']:
        lead = f"US subscriber bases of the major streaming services, as of {AS_OF}:"
        lines = [f"- {s['name']}: {_fmt(s['us_subscribers'])} ({s['basis']})" for s in svcs]
        tail = ("These are subscriber bases, not viewers: a viewers read counts the distinct people who played something on the "
                "service in a window, and runs higher than the account count. Ask me for the viewers of any of these, for a "
                "title on one of them, or for any of these in a window.")
        reply = lead + "\n" + "\n".join(lines) + "\n\n" + tail
        followups = ['Paramount+ viewers in the trailing 12 months', 'Netflix viewers in the trailing 12 months', 'How is churn worked out for a service?']
    else:
        parts = [f"{s['name']} reads {_fmt(s['us_subscribers'])} US subscribers as of {AS_OF} ({s['basis']})." for s in svcs]
        reply = ' '.join(parts)
        if len(svcs) == 1:
            s = svcs[0]
            reply += (f" That is the subscriber base, not the viewers: a viewers read counts the distinct people who played something on "
                      f"{s['name']} in a window and runs higher. Ask me for {s['name']} viewers in a window, or for every service side by side.")
            followups = [f"{s['name']} viewers in the trailing 12 months", 'US subscriber bases for every streaming service', f"Churn for {s['name']}"]
        else:
            followups = ['US subscriber bases for every streaming service', f"{svcs[0]['name']} viewers in the trailing 12 months", 'How is churn worked out for a service?']
    return reply, followups[:3]


def answer(text, ctx=None, *, host=None, user=None, history=None):
    """A raw reply for a subscriber-base ask, or None when the ask is not
    this lane's."""
    parsed = parse(text)
    if not parsed:
        return None
    reply, followups = compose(parsed)
    try:
        if host is not None and host.has('ask_hint'):
            host.ask_hint(route='service_subscribers', outcome='answered',
                          subject=('streaming services' if parsed['all'] else ', '.join(s['name'] for s in parsed['services'])))
    except Exception:
        pass
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': followups,
            'offer_deck': False, 'deck_angle': None,
            'service_subscribers': {'as_of': AS_OF, 'services': [{'name': s['name'], 'us_subscribers': s['us_subscribers']} for s in parsed['services']]}}
