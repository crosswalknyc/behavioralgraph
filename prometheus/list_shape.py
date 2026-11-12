"""A list of subjects is a list of names (Jenna 2026-11-11: "these errors
cannot happen").

Jessie, 22:12 UTC on 2026-11-11, ordering one profile and pasting the
lines of the read above it so the build would match:

    run a NEW profile IQ profile on this audience: On Georgie & Mandy's
    First Marriage Viewers - Paramount+:
    Window: Oct 25, 2024 to Nov 10, 2026.
    (a bullet)
    Paramount+ placements carried into the build: 49

The reply drafted two builds: "Window: Oct 25, 2024 to Nov 10, 2026" and
"Paramount+ Placements Carried Into the Build: 49".

The cause: with no "profiles on" phrase in the order, every line after
the request that opens on a capital letter was taken as a subject. A
labelled detail ("Window: ...", "... carried into the build: 49") opens
on a capital too.

What a real list looks like: each item is a name. A line that states a
window, a count, a price or any other labelled value is a detail of the
order, never a subject. ``settle`` holds the batch step to that:

  * detail lines are set aside; what is left is the list;
  * fewer than two names left: it is one order, and the single brief
    reads it whole (the subject the request line names, with the
    details as its details);
  * two or more names left under an order written for ONE profile, with
    pasted details beside them: one short question, one profile or that
    many, before anything is drafted.

Pure functions. Nothing here raises into a request. No em dashes in
anything a reader sees.
"""
from __future__ import annotations

import re

__all__ = ['detail_line', 'settle', 'names', 'asks_for_one']

_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE = (r"(?:" + _MONTH + r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{2}|\d{1,2}[/-]\d{1,2}[/-](?:19|20)?\d{2}|"
         r"(?:19|20)\d{2}-\d{2}-\d{2}|" + _MONTH + r"\.?\s+(?:19|20)\d{2})")
_RANGE_RX = re.compile(_DATE + r"\s*(?:to|through|thru|until|-|\u2013|\u2014)\s*" + _DATE, re.I)
# a label, a colon, then a value that is a number, a date, a price or a share
_LABEL_VALUE_RX = re.compile(
    r"^[^:]{2,80}:\s*(?:about\s+|around\s+|roughly\s+|~\s*)?(?:[$\u00a3\u20ac]\s*)?(?:\d[\d,.]*\s*(?:%|k|m|b|mm|bn|million|billion|"
    r"thousand|credits?|min(?:utes?)?|hours?|days?|weeks?|months?|years?|people|viewers|accounts|placements?|inputs?|rows?)?"
    r"|" + _DATE + r".*|n/?a|none|yes|no|tbd)\s*\.?$", re.I)
# the labels a brief, a read or a status line carries
_FIELD_LABEL_RX = re.compile(
    r"^(?:the\s+)?(?:window|measurement\s+window|date\s+range|dates?|period|time\s*frame|timeframe|sample(?:\s+size)?|"
    r"estimated\s+audience|projected(?:\s+\w+){0,3}|audience\s+size|subject|cost|price|build\s+time|avid\s+tier|status|"
    r"run\s+id|note|notes|source|sources|total|totals|count|counts|unique(?:\s+\w+){0,4}|reach|how\s+these\s+are\s+counted|"
    r"platforms?|geography|region|market|country|cut|cuts|category|brand\s+category|in\s+select\s+profile\s+as|avid\s+fan\s+cut)"
    r"\s*:", re.I)
_READ_WORDS_RX = re.compile(
    r"\bcarried\s+into\s+the\s+build\b|\bplacements?\s+(?:carried|in\s+the\s+build|on\s+record)\b|\bin\s+the\s+data\b|"
    r"\bprojected\s+us\b|\bunique\s+us\b|\bhow\s+these\s+are\s+counted\b|\bready\s+in\s+the\s+select\s+profile\b|"
    r"\bapprove\s+to\s+run\b|\bis\s+(?:building|finished|ready)\b", re.I)
_ONE_RX = re.compile(
    r"\b(?:a|an|one|1|this|the|that)\s+(?:(?:new|full|fresh|single|complete|separate)\s+){0,2}"
    r"(?:profile\s+iq\s+profile|profile\s+iq|profile|iq)\b", re.I)
_MANY_RX = re.compile(r"\b(?:profiles|iqs|each\s+of|one\s+each|separate\s+profiles?|individual\s+profiles?|both|\d+\s+profiles?)\b", re.I)


def _squash(text):
    return ' '.join(str(text or '').split())


def detail_line(item):
    """True when a line is a detail of an order (a window, a count, a
    price, any labelled value, a line lifted from a read), not a name."""
    s = _squash(item).strip(" \t-*\u2022\u00b7")
    if len(s) < 2:
        return True                       # a bare bullet
    if _RANGE_RX.search(s):
        return True
    if _FIELD_LABEL_RX.match(s):
        return True
    if _LABEL_VALUE_RX.match(s):
        return True
    if _READ_WORDS_RX.search(s):
        return True
    return False


def asks_for_one(text):
    """True when the order is written for one profile ("a profile", "one
    NEW profile IQ profile") and never for several."""
    t = _squash(text)
    return bool(_ONE_RX.search(t)) and not _MANY_RX.search(t)


def _names_phrase(names):
    shown = [str(n) for n in names[:4]]
    if len(names) > 4:
        shown.append(f"{len(names) - 4} more")
    return ', '.join(shown)


def settle(text, subjects):
    """{'subjects': [...], 'ask': payload or None, 'dropped': [...]}.

    ``subjects`` is what the list splitter returned. Detail lines come
    out; fewer than two names left is no list at all (``subjects`` comes
    back empty and the single brief reads the order); two or more names
    under an order for one profile, with details pasted beside them, is
    asked once. A clean list comes back exactly as it was."""
    try:
        items = [str(s) for s in (subjects or []) if str(s or '').strip()]
        if not items:
            return {'subjects': [], 'ask': None, 'dropped': []}
        names = [s for s in items if not detail_line(s)]
        dropped = [s for s in items if detail_line(s)]
        if not dropped:
            return {'subjects': items, 'ask': None, 'dropped': []}
        if len(names) < 2:
            return {'subjects': [], 'ask': None, 'dropped': dropped}
        if asks_for_one(text):
            n = len(names)
            lst = '; '.join(names)
            one = f"Run one combined profile covering all of the following: {lst}"
            many = f"Run separate profiles on: {', '.join(names)}"
            reply = (f"Is this one profile or {n} separate profiles? I read {n} names in what you sent "
                     f"({_names_phrase(names)}) and left the other lines out as details of the order. "
                     f"Say one profile or {n} profiles and I will draft exactly that.")
            return {'subjects': [], 'dropped': dropped,
                    'ask': {'success': False, 'guidance': True, 'error': reply, 'followups': [one, many]}}
        return {'subjects': names, 'ask': None, 'dropped': dropped}
    except Exception:
        return {'subjects': list(subjects or []), 'ask': None, 'dropped': []}


def names(text, subjects):
    """(subjects, ask) for the build step: the list to draft, or the one
    question to send back (then the list is empty)."""
    out = settle(text, subjects)
    return out['subjects'], out.get('ask')
