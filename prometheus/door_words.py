"""The word door stays out of deck copy, and only deck copy (Jenna
2026-11-11; the deck rule of 2026-10-28, `no-doors-in-decks.mdc`).

2026-11-10 carried the deck rule to every chat reply and every generated
file name. On 2026-11-11 it mangled names in Armine's read at Lionsgate:
"Hot Ones, Architectural Digest Open Door, Tiny Desk" came back
"Architectural Digest brings", "AD Open Door" came back "AD brings",
"the broader doorway into the originals slate" lost its noun, and Liz's
Get Real read said "the widest front store". The rule is about decks.

  * A chat reply is never run through this module. The one exception is
    a reply that IS deck copy (a deck outline typed into the chat:
    ``is_deck_copy``); ``scrub_payload`` leaves everything else byte for
    byte.
  * A title, a brand or a person's name is never touched, in a deck or
    anywhere: a capital-D Door / Doors beside a capitalized word (AD
    Open Door, The Doors, Three Doors Down, Door County), the venue The
    Door, and compound names (DoorDash, Glassdoor, Nextdoor). The frames,
    the token and the name rule are the design pack's ``cw_door_copy``
    (through ``migration.design_skill``); this module writes no second
    scrubber.
  * What deck copy needs on top of the house scrub is the retail sense:
    where the house scrub would drop the token and leave "a three-
    audience", it says store ("a three-store audience").

``scrub_read(text)``      deck copy with no door in it: names held aside,
                          the retail sense said as store, the metaphor
                          frames rewritten by the house rules.
``scrub_filename(name)``  the same for a deck's file name.
``has_door(text)``        True when a disallowed token is present.
``is_deck_copy(text)``    True for a deck outline typed into a reply.
``scrub_payload(raw)``    a reply payload: untouched unless its reply is
                          deck copy.

Never raises; with the pack absent the built-in frames stand in.
Regression: scripts/test_pm_truth_2026_11_11.py (the names),
scripts/test_pm_conversation_plumbing.py (the retail sense).
"""
from __future__ import annotations

import re

__all__ = ['scrub_read', 'scrub_filename', 'has_door', 'scrub_payload', 'is_deck_copy', 'name_spans']

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


# a title, a brand or a person's name: the house rule when the pack is here, the same shape when it is not
_NAME_FALLBACK = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Z][A-Za-z0-9'\u2019&.+-]*\s+){1,5}Doors?(?![A-Za-z0-9])(?!\s+(?:to|for|into)\b)"
    r"(?:\s+[A-Z][A-Za-z0-9'\u2019&.+-]*){0,5}"
    r"|(?<![A-Za-z0-9])Doors?(?:\s+[A-Z][A-Za-z0-9'\u2019&.+-]*){1,5}")
_DECK_COPY_RX = re.compile(r"(?m)^\s*SLIDE\s+\d+\b|^Here is the deck\b|^Here is a [\w -]*deck\b|^Deck outline\b", re.I)


def name_spans(text):
    """(start, end) of every title, brand or person's name that carries
    Door / Doors. Never raises."""
    t = str(text or '')
    try:
        ds = _house()
        mod = ds.door_copy_module() if ds is not None else None
        fn = getattr(mod, 'proper_name_spans', None)
        if callable(fn):
            return list(fn(t))
    except Exception:
        pass
    try:
        return [(m.start(), m.end()) for m in _NAME_FALLBACK.finditer(t)]
    except Exception:
        return []


def is_deck_copy(text):
    """True when a reply is deck copy: a deck outline typed into the chat."""
    try:
        return bool(_DECK_COPY_RX.search(str(text or '')))
    except Exception:
        return False


def _allowed(text, start, end):
    for m in _ALLOWED.finditer(text):
        if m.start() <= start and end <= m.end() and not re.match(r"\s+(to|for)\b", text[m.end():], re.I):
            return True
    return any(a <= start and end <= b for a, b in name_spans(text))


def _hold_names(text):
    """The text with every name that carries the word set aside, and the
    names, so no rewrite below can reach inside one."""
    held, out = [], text
    for a, b in sorted(name_spans(text), reverse=True):
        held.append(out[a:b])
        out = out[:a] + f"\x00PMNAME{len(held) - 1}\x00" + out[b:]
    return out, held


def _restore_names(text, held):
    for i, name in enumerate(held):
        text = text.replace(f"\x00PMNAME{i}\x00", name)
    return text


def has_door(text):
    t = str(text or '')
    return any(not _allowed(t, m.start(), m.end()) for m in _TOKEN.finditer(t))


def _store(m_text):
    word = 'stores' if m_text.lower().endswith('s') else 'store'
    if m_text.isupper():
        return word.upper()
    return word.capitalize() if m_text[:1].isupper() else word


def scrub_read(text):
    """Deck copy with no door in it. Text without the token is returned
    untouched (the usual case costs one regex search); a title, a brand
    or a person's name that carries the word comes back as written."""
    try:
        if not isinstance(text, str) or not text or not _TOKEN.search(text):
            return text
        if not has_door(text):
            return text
        out, _names = _hold_names(text)
        # the literal senses as the house helper says them: an entrance, a store count, the stores a brand is
        # sold in (Jenna 2026-11-11: every leftover used to become "store", so a deck read "the widest front store")
        ds = _house()
        try:
            _lit = getattr(ds.door_copy_module(), 'literal_senses', None) if ds is not None else None
        except Exception:
            _lit = None
        if callable(_lit):
            out = _lit(out)
        # the retail sense, where a count, a kind or a possessive sits on the token
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
        # whatever is left: the house helper has the last word (an entrance, stores, or the word dropped); a
        # host without the pack says store
        if callable(_lit) and has_door(out):
            out = ds.scrub_door_metaphor(out)
        out = _TOKEN.sub(lambda m: m.group(0) if _allowed(out, m.start(), m.end()) else _store(m.group(0)), out)
        out = re.sub(r"[ \t]{2,}", ' ', out)
        out = re.sub(r" +([,.;:!?])", r"\1", out)
        if ds is not None and has_door(out):
            out = ds.scrub_door_metaphor(out)       # the house scrub has the last word
        return _restore_names(out, _names)
    except Exception:
        return text


def scrub_filename(name):
    """A generated file name with no door in it ('Retail_door_mix.csv' ->
    'Retail_store_mix.csv'). Compound names are untouched."""
    try:
        s = str(name or '')
        if not s or 'door' not in s.lower():
            return name
        spaced = re.sub(r"[_-]+", ' ', s)
        if _ALLOWED.search(spaced) or name_spans(spaced):
            return name              # a title, a brand or a person's name in the file's name stays as written
        return _FILE_TOKEN.sub(lambda m: _store(m.group(0)), s)
    except Exception:
        return name


_TEXT_KEYS = ('reply', 'message', 'question', 'summary', 'text', 'error', 'headline', 'title', 'subject')


def scrub_payload(raw):
    """A reply payload. A chat reply is never touched (2026-11-11): the
    rule is about decks. Only a reply that IS deck copy (a deck outline
    typed into the chat) has its reader-visible strings scrubbed, in
    place; URLs and keys are never touched. Returns the payload."""
    try:
        if not isinstance(raw, dict):
            return raw
        if not any(is_deck_copy(raw.get(k)) for k in ('reply', 'message', 'text', 'summary') if isinstance(raw.get(k), str)):
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
