"""One opener on a read (2026-11-11).

A read opens by naming the audience it counted: 'On people who watched
Mad Men in the window:'. The line is added when the reply does not
already carry it. The Mad Men compare (job 9e5e8d62fd5a) shipped with
two: the model's own headline began 'On people who watched Mad Men:' and
the opener went on top of it, because the check looked for the full
audience label ('... in the window') and the headline said the same
thing in fewer words.

``with_opener(reply, audience)`` keeps one. When the reply already opens
'On <the same audience>:' in other words, that lead-in is replaced by the
audience label and the sentence that followed it stands as the first
line of the answer. A reply that opens 'On <something else>:' (a
platform, a date) gets the opener above it as before.

Pure text; never raises.
"""
from __future__ import annotations

import re

__all__ = ['with_opener', 'same_audience']

_LEAD_RX = re.compile(r'^\s*On\s+(?P<who>[^:\n]{3,160}?):[ \t]*(?P<gap>\n+)?')
_STOP = {'the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for', 'and', 'or', 'who', 'that', 'window', 'this',
         'these', 'those', 'us', 'people', 'viewers', 'users', 'fans', 'audience', 'with', 'by', 'from', 'during',
         'over', 'trailing', 'months', 'last', 'past'}


def _tokens(s):
    return {w for w in re.findall(r"[a-z0-9][a-z0-9'’&+.-]*", str(s or '').lower()) if w not in _STOP and len(w) > 1}


def same_audience(a, b):
    """True when two audience phrases name the same people: every
    distinctive word of the shorter one is in the longer one, and there
    is at least one distinctive word to compare."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return small <= big


def with_opener(reply, audience):
    """The reply with exactly one 'On <audience>:' opener."""
    try:
        text = str(reply or '')
        aud = ' '.join(str(audience or '').split())
        if not aud:
            return text
        if aud.lower() in text[:90].lower():
            return text
        m = _LEAD_RX.match(text)
        if m and same_audience(m.group('who'), aud):
            rest = text[m.end():].lstrip()
            if rest:
                rest = rest[0].upper() + rest[1:]
            return f"On {aud}:\n\n{rest}"
        return f"On {aud}:\n\n{text}"
    except Exception:
        return reply
