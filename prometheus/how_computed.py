"""How a figure is computed, and what we hold at the service level, answered
on the spot (Cara Dorfman, Amazon, 2026-11-16; Jenna the same day: "Its
answering cara horribly. look at her questions and fix so it doesnt happen
again").

Cara asked three things, several ways each, and none was answered:

  "how do you estimate churn/retention for a service like paramount+?"
  "so give me the math behind how you would calculate a retention curve
   for paramount+, say over the past 12 months"          (drew a $300 build)
  "I DONT WANT THE TITLE. I WANT TO KNOW ABOUT THE SURVIVAL CURVE"
  "NO. NOT TITLES. SUBSCRIBER COUNTS. LIKE HOW MANY CUSTOMERS SUBSCRIBE TO A
   SERVICE, THIER CHURN AND SURVIVAL CURVE."             (drew a $100 quote)

A question about how a figure is worked out is a question about us. It has
one plain answer from the house definitions and is never a build, a quote,
a read on whatever page is open, a which-audience question or a product
description. Three things live here:

  method    "how do you calculate / estimate X", "the math behind X", "the
            formula for X", "walk me through how you get X": the definition
            and how it is computed. Service-level churn, retention and the
            retention (survival) curve have their own entries below; every
            other term stays with prometheus.kpi_definitions and its lane.
  about     "I want to know about the survival curve", "what is a retention
            curve", "do you have churn for a whole service", "where are
            retention curves": the same entry, with where it sits.
  overview  the service-level things named together ("subscriber counts,
            their churn and survival curve"): each answered in turn, the
            subscriber bases from prometheus.service_subscribers.

A question that names a service and asks for its figure ("what is Paramount+
churn over the past 12 months") is a read and is not answered here.

Pure text; no Flask, no network, no model call. Never raises. Wired in
prometheus.service (_standing_answer). Regression:
scripts/test_pm_cara_2026_11_16.py.
"""
from __future__ import annotations

import re

__all__ = ['is_method_ask', 'topics_of', 'answer', 'ENTRIES', 'names_service_level']

_MAX_LEN = 420

# ------------------------------------------------------------------ entries
# The curve wording matches what Cara was told on 2026-11-16 (cohorts by
# month, share still active k months later, churn at k from the curve): one
# definition, said the same way every time.
ENTRIES = {
    'curve': {
        'label': 'Retention curve',
        'definition': (
            "A retention curve shows, for the accounts active on a service in a starting month, the share still "
            "active in each later month. It starts at 100% and steps down. A survival curve is the same thing."),
        'computed': (
            "Every account active on the service in a given month is that month's cohort, so a 12 month window "
            "has 12 cohorts. An account is active in a month when it has at least one session on the service in "
            "it. For a cohort, the share still active k months later is the cohort's accounts active in month k "
            "divided by the cohort's size. An account with a full month and no session counts as gone for that "
            "month, and counts again in any later month it comes back. The curve is that share at month 1 through "
            "month 12, averaged across the cohorts old enough to reach each month and weighted by cohort size. "
            "Monthly churn at month k is 1 minus the share at k divided by the share at k minus 1."),
    },
    'churn': {
        'label': 'Churn and retention for a service',
        'definition': (
            "Churn is the share of a service's active accounts that stop using it. Retention is the share that "
            "stay."),
        'computed': (
            "An account is active in a month when it has at least one session on the service in that month. "
            "Monthly churn is the accounts active last month with no session this month, divided by the accounts "
            "active last month. Retention is 1 minus churn. An account that comes back after a full month away "
            "is a reactivation: it counts as active again from the month it returns, and never as a new signup. "
            "An average over a window is weighted by the accounts active at the start of each month."),
    },
}

# One answer said "active" is a session and a published study said it is billing; a reader checking one
# against the other was right to ask which (Cara Dorfman, 2026-11-16). Both are true of different things,
# and the entry says so every time: the rule above is for a read from viewing activity, and a published
# study's figures follow the rule that study states (prometheus.study_record answers from its record).
_BASIS_LINE = ("That is the rule for a read from viewing activity. A published study states its own rule, and its "
               "figures follow that one.")
_WHERE_LINE = (
    "Where this sits: Subscriber IQ on the dashboard reads subscribers one title at a time, the people a title "
    "brought in and how many of them stayed. Churn and the retention curve for a whole service are not a standing "
    "page. They are a read you ask me for, by service and window.")
_NEXT_LINE = "For the figures on one service, ask for it by name and window, for example: {example}."

# ------------------------------------------------------------------- shapes
_WHO = r"(?:you|we|u|crosswalk|prometheus|it|this|the dashboard|the platform|the site|the tool)"
_COMPUTE = (r"(?:calculat\w*|comput\w*|estimat\w*|measur\w*|deriv\w*|determin\w*|defin\w*|count\w*|figur\w*|quantif\w*|"
            r"work(?:s|ed|ing)?\s+out|com(?:e|es|ing)\s+up\s+with|arriv\w*\s+at|get(?:s|ting)?\s+(?:to|at)|"
            r"build\w*|produc\w*|track\w*|model\w*)")
_METHOD_RXS = (
    re.compile(r"\bhow\s+(?:do|would|does|did|can|could|will|might|should)\s+" + _WHO + r"\s+(?:\w+\s+){0,2}?" + _COMPUTE + r"\b", re.I),
    re.compile(r"\bhow\s+(?:is|are|was|were)\s+.{0,90}?\b(?:calculated|computed|estimated|measured|derived|determined|"
               r"defined|counted|figured|worked\s+out|arrived\s+at|built|produced|tracked|modeled|modelled)\b", re.I),
    re.compile(r"\b(?:math|maths|mathematics|formula|formulas|equation|equations|calculation|calculations|methodology|"
               r"method|logic|arithmetic)\s+(?:behind|for|of|used|you\s+use|we\s+use|to\s+(?:calculate|compute|get))\b", re.I),
    re.compile(r"\b(?:walk|talk|take)\s+me\s+through\s+(?:how|the\s+(?:math|calculation|method|formula))\b", re.I),
    re.compile(r"\bexplain\s+(?:to\s+me\s+)?how\s+" + _WHO + r"?\s*(?:\w+\s+){0,2}?" + _COMPUTE + r"\b", re.I),
    re.compile(r"\bwhat\s+(?:goes|went)\s+into\b|\bhow\s+does\s+.{0,60}?\bwork\b", re.I),
)
_WHERE_RX = re.compile(
    r"\bwhere\s+(?:(?:can|do|would|could|should|might)\s+(?:i|we|you|one)\s+|to\s+|is\s+|are\s+|does\s+|did\s+)"
    r"|\bwhere(?:'s| is| are)\b|\b(?:how|where)\s+do\s+i\s+(?:find|get\s+to|see|open|view|access)\b|"
    r"\b(?:see|find|locate|view|access)\s+where\b", re.I)
_ABOUT_RX = re.compile(
    r"\b(?:i\s+(?:just\s+|only\s+|really\s+)?(?:want|need|would\s+like|wanted|am\s+trying|'m\s+trying)\s+to\s+"
    r"(?:know|understand|learn|hear|see|find|ask)|i\s+(?:just\s+|only\s+)?care\s+about|i\s+(?:am|'m)\s+(?:asking|talking)\s+about|"
    r"tell\s+me\s+(?:more\s+)?about|explain|what(?:'s|\s+is|\s+are)\s+(?:an?\s+|the\s+|your\s+)?|what\s+about|"
    r"do\s+you\s+(?:have|show|track|hold|carry|offer|estimate|calculate|measure|report)|is\s+there\s+(?:an?\s+|any\s+)?|"
    r"are\s+there\s+(?:any\s+)?|can\s+(?:i|you|we)\s+(?:see|get|find|show)|does\s+(?:crosswalk|the\s+dashboard|this|it)\s+(?:have|show|track))\b", re.I)
# an order for the figures themselves: another lane's
_ORDER_RX = re.compile(
    r"^\W*(?:please\s+|can\s+you\s+|could\s+you\s+|go\s+ahead\s+and\s+)?(?:run|pull|build|create|generate|make|queue|start|"
    r"launch|refresh|draft|chart|plot|send|email|e-mail)\b", re.I)
_FIGURE_RX = re.compile(
    r"^\W*(?:what(?:'s|\s+is|\s+was|\s+are|\s+were)|show\s+me|give\s+me|get\s+me|how\s+(?:high|low|big|much)\s+is)\b", re.I)

# ------------------------------------------------------------------- topics
_CURVE_RX = re.compile(
    r"\b(?:retention|survival|survivor|churn|attrition|cohort|tenure|decay)\s+curves?\b|\bcohort\s+(?:survival|retention)\b|"
    r"\bsurvival\s+(?:rate|rates|analysis|table|function)\b|\bcurves?\s+(?:of|for)\s+(?:retention|survival|churn)\b", re.I)
_CHURN_RX = re.compile(r"\b(?:churn\w*|attrition|retention|retain\w*|cancel+ations?|cancel\s+rates?|stay\s+subscribed|drop[- ]?off\s+rate)\b", re.I)
_SUBS_RX = re.compile(
    r"\b(?:subscriber\s+(?:counts?|numbers?|estimates?|bases?|totals?|figures?)|number\s+of\s+subscribers|"
    r"how\s+many\s+(?:customers|people|subscribers|accounts|users|members)\s+(?:subscribe|are\s+subscribed|pay|have\s+a\s+subscription)|"
    r"subscribers?\s+(?:to|of|for|on)\s+(?:a|each|every|the|any)\s+(?:streaming\s+)?(?:service|platform)s?)\b", re.I)
_SERVICE_LEVEL_RX = re.compile(
    r"\b(?:(?:a|the|each|every|any|whole|entire|one|per)\s+(?:streaming\s+)?(?:service|platform|streamer)s?|service[- ](?:level|wide)|"
    r"platform[- ](?:level|wide)|overall|as\s+a\s+whole|streaming\s+(?:service|platform)s?|svod|subscribers?|subscription|"
    r"not\s+(?:a\s+|the\s+|any\s+|specific\s+to\s+(?:a|any)\s+)?(?:single\s+)?(?:content\s+)?titles?|"
    r"(?:don'?t|dont|do\s+not)\s+(?:want|care\s+about|mean)\s+(?:the\s+|a\s+)?(?:titles?|content))\b", re.I)
_NO_TITLE_RX = re.compile(
    r"\bnot\s+(?:a\s+|the\s+|any\s+|about\s+|specific\s+to\s+(?:a|any)\s+)?(?:single\s+|one\s+)?(?:content\s+)?titles?\b|"
    r"\b(?:don'?t|dont|do\s+not)\s+(?:want|care\s+about|mean)\s+(?:the\s+|a\s+)?(?:titles?|content)\b|\bjust\s+overall\b|"
    r"\bas\s+a\s+whole\b|\b(?:service|platform)[- ](?:level|wide)\b|\bwhole\s+(?:service|platform)\b", re.I)


def _squash(text):
    return ' '.join(str(text or '').split())


def _services(text):
    try:
        from . import service_subscribers as _ss
        return list(_ss._named(text))
    except Exception:
        return []


def is_method_ask(text):
    """True when the words ask how a figure is worked out ("how do you
    estimate churn", "the math behind a retention curve")."""
    t = _squash(text)
    if not t or len(t) > _MAX_LEN:
        return False
    return any(rx.search(t) for rx in _METHOD_RXS)


def names_service_level(text):
    """The words are about a service as a whole, not one title."""
    t = _squash(text)
    return bool(t) and (bool(_SERVICE_LEVEL_RX.search(t)) or bool(_services(t)))


def topics_of(text):
    """The service-level things the words name, in the order they are
    answered: 'subscribers', 'churn', 'curve'."""
    t = _squash(text)
    out = []
    if not t:
        return out
    curve = bool(_CURVE_RX.search(t))
    if _SUBS_RX.search(t):
        out.append('subscribers')
    # churn beside the curve is its own topic only when the words carry it apart from the curve's own name
    rest = _CURVE_RX.sub(' ', t)
    if _CHURN_RX.search(rest):
        out.append('churn')
    if curve:
        out.append('curve')
    return out


def _entry_text(key):
    e = ENTRIES[key]
    return f"{e['label']}: {e['definition']}\n\nHow it is computed: {e['computed']} {_BASIS_LINE}"


def _subscribers_text():
    try:
        from . import service_subscribers as _ss
        # one registry, one wording: the subscriber-base lane's own reply
        reply, _chips = _ss.compose({'services': list(_ss.SERVICES), 'all': True})
        return str(reply).split('\n\n')[0] + "\n\nThese are subscriber bases, not viewers."
    except Exception:
        return ''


def _example(text):
    svcs = _services(text)
    name = svcs[0]['name'] if svcs else 'Paramount+'
    return f"\"{name} churn, past 12 months\""


def answer(text, ctx=None, *, correction=False):
    """The standing payload, or None when the words are not one of these
    (the lanes keep them). ``correction`` is True when the reader has just
    said the last reply missed: the service-level things named on their
    own ("Subscriber counts. Like how many customers subscribe to a
    service, their churn and survival curve.") are answered in turn."""
    try:
        t = _squash(text)
        if not t or len(t) > _MAX_LEN:
            return None
        method = is_method_ask(t)
        where = bool(_WHERE_RX.search(t))
        about = bool(_ABOUT_RX.search(t))
        topics = topics_of(t)
        svc_named = bool(_services(t))
        view_id = ''
        try:
            view_id = str((((ctx or {}).get('view_context') or {}).get('view_id')) or '')
        except Exception:
            view_id = ''

        # an order for the figures, or a named service's own figure, is a read
        if not method and (_ORDER_RX.search(t) or (svc_named and _FIGURE_RX.search(t) and not where)):
            return None

        # how a figure is worked out
        if method:
            service_level = names_service_level(t) or 'curve' in topics
            keys = [k for k in topics if k in ENTRIES] if service_level else []
            if 'curve' in keys and 'churn' in keys and not re.search(r"\bchurn\w*\b", _CURVE_RX.sub(' ', t), re.I):
                keys = ['curve']     # "retention curve" names one thing
            if keys:
                parts = [_entry_text(k) for k in keys]
                if 'subscribers' in topics:
                    sub = _subscribers_text()
                    if sub:
                        parts.insert(0, sub)
                reply = '\n\n'.join(parts) + '\n\n' + _NEXT_LINE.format(example=_example(t))
                return _payload(reply, keys, 'method')
            # every other term is the glossary's, in its own lane with its own route (prometheus.kpi_definitions:
            # "how is penetration calculated?" on a profile, "how are reactivated accounts counted?" on
            # Subscriber IQ); "how would you measure the lift from our campaign" proposes work
            return None

        if not topics:
            return None
        # a title's own churn on the Subscriber IQ page is the glossary's and the page's
        if topics == ['churn'] and not names_service_level(t):
            return None
        fragment = correction and len(t.split()) <= 40 and not _ORDER_RX.search(t)
        if not (where or about or fragment):
            return None
        if topics == ['subscribers']:
            # the subscriber-base lane's own reply, for the services named or for each of them
            try:
                from . import service_subscribers as _ss
                parsed = _ss.parse(t) or {'services': list(_ss.SERVICES), 'all': True}
                reply, chips = _ss.compose(parsed)
                out = _payload(reply, topics, 'where' if where else ('about' if about else 'overview'))
                out['followups'] = list(chips or [])
                out['service_subscribers'] = {'as_of': _ss.AS_OF, 'services': [
                    {'name': s['name'], 'us_subscribers': s['us_subscribers']} for s in parsed['services']]}
                return out
            except Exception:
                return None
        parts = []
        if _NO_TITLE_RX.search(t) or (fragment and len(topics) > 1):
            parts.append("This is for a service as a whole, not one title.")
        for k in topics:
            if k == 'subscribers':
                sub = _subscribers_text()
                if sub:
                    parts.append(sub)
            else:
                parts.append(_entry_text(k))
        if not parts:
            return None
        if any(k in ENTRIES for k in topics):
            parts.append(_WHERE_LINE)
            parts.append(_NEXT_LINE.format(example=_example(t)))
        return _payload('\n\n'.join(parts), topics, 'where' if where else ('about' if about else 'overview'))
    except Exception:
        return None


def _payload(reply, keys, shape):
    return {'success': True, 'action': 'answer', 'reply': reply, 'followups': [], 'offer_deck': False,
            'deck_angle': None, 'how_computed': {'topics': [k for k in keys if k], 'shape': shape}}
