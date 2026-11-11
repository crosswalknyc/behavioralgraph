"""The word door never appears in a chat read or a file name (Jenna
2026-11-10, the day audit; the deck rule of 2026-10-28 carried to
Prometheus).

Keith's Claire's read, 2026-11-10: "Claire's buyers are a three-door
audience ... Retail door mix ... the profile's home door". In retail
talk a door is a store; to a reader it is a metaphor, and the house
never writes it.

This module adds one thing to the house helper and writes no second
scrubber: the metaphor frames, the token and the proper-name allowlist
all come from the design pack's ``cw_door_copy`` (through
``migration.design_skill``). What a read needs on top is the retail
sense: where the house scrub would drop the token and leave "a three-
audience", a read says store ("a three-store audience", "Retail store
mix", "the profile's home store").

``scrub_read(text)``      metaphor frames rewritten by the house rules,
                          a remaining door / doors said as store /
                          stores, then the house scrub as the last word.
                          Compound brand names (DoorDash, Glassdoor,
                          Nextdoor) and a venue named The Door are left
                          alone, as in the house helper.
``scrub_filename(name)``  the same for a generated file name, where the
                          words are joined by underscores or dashes.
``has_door(text)``        True when a disallowed token is present.
``scrub_payload(raw)``    the reader-visible strings of a reply payload
                          (reply, headline, follow-up chips, the file's
                          name and label), in place.

Never raises; with the pack absent the built-in frames stand in.
Regression: scripts/test_pm_conversation_plumbing.py.
"""
from __future__ import annotations

import re

__all__ = ['scrub_read', 'scrub_filename', 'has_door', 'scrub_payload']

_TOKEN = re.compile(r"\bdoors?\b", re.I)
_ALLOWED = re.compile(r"\bThe Door(?:\s+NYC)?\b")
# the frames the house helper carries, for a host without the pack
_FALLBACK_FRAMES = [
    (re.compile(r"\bopens?\s+the\s+doors?\s+to\s+", re.I), "brings "),
    (re.compile(r"\bopen(?:ed|ing)\s+the\s+doors?\s+to\s+", re.I), "brings "),
    (re.compile(r"\bopens?\s+doors?\s+to\s+", re.I), "brings "),
    (re.compile(r"\bopens?\s+the\s+doors?\b", re.I), "brings"),
    (re.compile(r"\bwalk(?:s|ed|ing)?\s+through\s+(?:the\s+)?doors?\b", re.I), "arrives"),
    (re.compile(r"\b(?:the\s+)?doorways?\s+to\s+", re.I), ""),
    (re.compile(r"\b(?:the\s+|a\s+)?doors?\s+(?:in)?to\s+", re.I), ""),
]
# a count or a kind in front of the token is the retail sense: 'three-door', 'retail door', 'home door'
_FILE_TOKEN = re.compile(r"(?<![A-Za-z])(d)(oor)(s?)(?![A-Za-z])", re.I)


def _house():
    try:
        from migration import design_skill as _ds
        return _ds
    except Exception:
        return None


def _frames():
    ds = _house()
    try:
        mod = ds.door_copy_module() if ds is not None else None
        fr = getattr(mod, '_FRAMES', None)
        if fr:
            return list(fr)
    except Exception:
        pass
    return _FALLBACK_FRAMES


def _allowed(text, start, end):
    for m in _ALLOWED.finditer(text):
        if m.start() <= start and end <= m.end() and not re.match(r"\s+(to|for)\b", text[m.end():], re.I):
            return True
    return False


def has_door(text):
    t = str(text or '')
    return any(not _allowed(t, m.start(), m.end()) for m in _TOKEN.finditer(t))


def _store(m_text):
    word = 'stores' if m_text.lower().endswith('s') else 'store'
    if m_text.isupper():
        return word.upper()
    return word.capitalize() if m_text[:1].isupper() else word


def scrub_read(text):
    """A chat read with no door in it. Text without the token is returned
    untouched (the usual case costs one regex search)."""
    try:
        if not isinstance(text, str) or not text or not _TOKEN.search(text):
            return text
        if not has_door(text):
            return text
        out = text
        # the retail sense first, where a count, a kind or a possessive sits on the token
        out = re.sub(r"\b([A-Za-z0-9]+)-(doors?)\b", lambda m: f"{m.group(1)}-{_store(m.group(2))}", out)
        out = re.sub(r"\b(retail|retailer|home|named|store|shop|shopping|physical|top|other|each|every|per|by|same|new|"
                     r"two|three|four|five|six|seven|eight|nine|ten|\d+|all|both|these|those|its|their|profile'?s)\s+(doors?)\b",
                     lambda m: f"{m.group(1)} {_store(m.group(2))}", out, flags=re.I)
        out = re.sub(r"\b(doors?)\s+(mix|count|counts|share|shares|list|level|by|per|carrying|carry|stocking|selling)\b",
                     lambda m: f"{_store(m.group(1))} {m.group(2)}", out, flags=re.I)
        # the metaphor, by the house frames (and the one shape they do not carry: 'a door into')
        out = re.sub(r"\b(a|the|one|another|its|their)\s+doors?\s+into\b", lambda m: f"{m.group(1)} way into", out, flags=re.I)
        for pat, repl in _frames():
            out = pat.sub(repl, out)
        # whatever is left is said plainly
        out = _TOKEN.sub(lambda m: m.group(0) if _allowed(out, m.start(), m.end()) else _store(m.group(0)), out)
        out = re.sub(r"[ \t]{2,}", ' ', out)
        out = re.sub(r" +([,.;:!?])", r"\1", out)
        ds = _house()
        if ds is not None and has_door(out):
            out = ds.scrub_door_metaphor(out)       # the house scrub has the last word
        return out
    except Exception:
        return text


def scrub_filename(name):
    """A generated file name with no door in it ('Retail_door_mix.csv' ->
    'Retail_store_mix.csv'). Compound names are untouched."""
    try:
        s = str(name or '')
        if not s or 'door' not in s.lower():
            return name
        if _ALLOWED.search(s.replace('_', ' ')):
            return name
        return _FILE_TOKEN.sub(lambda m: _store(m.group(0)), s)
    except Exception:
        return name


_TEXT_KEYS = ('reply', 'message', 'question', 'summary', 'text', 'error', 'headline', 'title', 'subject')


def scrub_payload(raw):
    """The reader-visible strings of a reply payload, in place: the
    reply and its kin, the follow-up chips, the file's name and label.
    URLs and keys are never touched. Returns the payload."""
    try:
        if not isinstance(raw, dict):
            return raw
        for k in _TEXT_KEYS:
            v = raw.get(k)
            if isinstance(v, str) and 'door' in v.lower():
                raw[k] = scrub_read(v)
        fu = raw.get('followups')
        if isinstance(fu, list):
            raw['followups'] = [scrub_read(f) if isinstance(f, str) else f for f in fu]
        for k in ('filename', 'pdf_filename'):
            if isinstance(raw.get(k), str):
                raw[k] = scrub_filename(raw[k])
        fl = raw.get('file_link')
        if isinstance(fl, dict):
            for k in ('name', 'label'):
                if isinstance(fl.get(k), str):
                    fl[k] = scrub_filename(fl[k]) if k == 'name' else scrub_read(fl[k])
    except Exception:
        pass
    return raw
