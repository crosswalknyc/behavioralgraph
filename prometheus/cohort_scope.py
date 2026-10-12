"""Compound cohorts anchored on an entity (2026-10-12, Jenna / Emmet).

"Republican voters in California who are fans of the LA Rams" is a
buildable universe: the Rams pinned at 100 in their home column, only
California markets in LOCATION, and a conservative-leaning read. The
build gate used to reject it ("I could not find California Republican
Rams as a brand, person, or title") because the ask names an entity
and carries no age or income qualifier, so it read as neither a
persona universe nor a plain entity.

This module reads that shape:

    anchor    the entity the cohort is defined by ("fans of the LA
              Rams", "Hilton guests", "Chime customers")
    states    US states named as the geography ("in California")
    lean      a political lean word (Republican / Democrat /
              Conservative / Liberal / Independent)

An ask is a compound cohort when it has an anchor AND at least one of
state / lean / demographic qualifier. The interpret marks the draft as
a persona universe (the entity check passes by construction), carries
`anchor_entity`, `geo_scope` and `political_lean` on the draft and the
spec, and the engine pins the anchor, scopes LOCATION to those states
and reads the brief with the lean in it.
"""
from __future__ import annotations

import re

US_STATES = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR', 'california': 'CA',
    'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE', 'florida': 'FL', 'georgia': 'GA',
    'hawaii': 'HI', 'idaho': 'ID', 'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
    'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD', 'massachusetts': 'MA',
    'michigan': 'MI', 'minnesota': 'MN', 'mississippi': 'MS', 'missouri': 'MO', 'montana': 'MT',
    'nebraska': 'NE', 'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ', 'new mexico': 'NM',
    'new york': 'NY', 'north carolina': 'NC', 'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK',
    'oregon': 'OR', 'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT', 'vermont': 'VT',
    'virginia': 'VA', 'washington state': 'WA', 'west virginia': 'WV', 'wisconsin': 'WI',
    'wyoming': 'WY', 'washington dc': 'DC', 'district of columbia': 'DC',
}
STATE_NAMES = {v: k.title() if k not in ('washington dc', 'district of columbia', 'washington state') else k.title()
               for k, v in US_STATES.items()}
STATE_NAMES['CA'] = 'California'
STATE_NAMES['WA'] = 'Washington State'
STATE_NAMES['DC'] = 'Washington DC'

_ANCHOR_WORDS = (r"fans?|followers|viewers|listeners|guests|customers|members|subscribers|"
                 r"shoppers|buyers|owners|users|supporters|attendees|season ticket holders")
_ANCHOR_OF_RX = re.compile(
    r"\b(?:" + _ANCHOR_WORDS + r")\s+of\s+(?:the\s+)?(?P<e>[A-Z][\w&'.+-]*(?:\s+(?:of|the|and|&)?\s*[A-Z][\w&'.+-]*){0,4})")
_ANCHOR_PRE_RX = re.compile(
    r"\b(?P<e>[A-Z][\w&'.+-]*(?:\s+[A-Z][\w&'.+-]*){0,4})\s+(?:" + _ANCHOR_WORDS + r")\b")
_ANCHOR_VERB_RX = re.compile(
    r"\b(?:who\s+)?(?:follow|support|root\s+for|cheer\s+for|watch|stream|listen\s+to|shop\s+at|stay\s+at)\s+"
    r"(?:the\s+)?(?P<e>[A-Z][\w&'.+-]*(?:\s+[A-Z][\w&'.+-]*){0,4})")
_LEAN_RX = re.compile(
    r"\b(?P<lean>republican|gop|conservative|right[- ]leaning|maga|democrat(?:ic)?|liberal|"
    r"progressive|left[- ]leaning|independent|libertarian)s?\b", re.I)
_DEMO_RX = re.compile(
    r"\b(?:aged?\s+\d{2}|ages?\s+\d{2}|\d{2}\s*(?:-|to)\s*\d{2}|\d{2}\s*\+|women|men|female|male|moms?|dads?|"
    r"parents|millennials?|gen\s*z|boomers?|household\s+income|\bhhi\b|\$\s*\d{2,3}[,\d]*k?)\b", re.I)
_QUAL_LEAD_RX = re.compile(r"\b(?:in|from|across|within|living in|who live in|based in|residents? of)\s+(?:the\s+state\s+of\s+)?$", re.I)

_LEAN_CANON = {
    'republican': 'Republican', 'gop': 'Republican', 'conservative': 'Conservative',
    'right leaning': 'Conservative', 'right-leaning': 'Conservative', 'maga': 'Republican',
    'democrat': 'Democrat', 'democratic': 'Democrat', 'liberal': 'Liberal', 'progressive': 'Progressive',
    'left leaning': 'Liberal', 'left-leaning': 'Liberal', 'independent': 'Independent',
    'libertarian': 'Libertarian',
}
# Words that are never the anchor entity (they are qualifiers or audience nouns).
_NOT_ENTITY = {'republican', 'democrat', 'democratic', 'conservative', 'liberal', 'independent',
               'gop', 'california', 'texas', 'florida', 'voters', 'voter', 'women', 'men', 'people',
               'adults', 'us', 'usa', 'american', 'americans', 'the', 'who', 'that', 'are', 'in'}


def states_in(text):
    """Two-letter codes for every US state the text names as a
    GEOGRAPHY: after a lead ("in California", "living in Texas"), or
    standing alone between lower-case words. A state word inside a
    brand name ("Texas Roadhouse", "New York Times") is not a
    geography. 'Washington' alone is ambiguous and ignored."""
    t = ' '.join(str(text or '').split())
    out = []
    for name, code in US_STATES.items():
        for m in re.finditer(r"\b" + re.escape(name) + r"\b", t, re.I):
            before = t[:m.start()].rstrip()
            after = t[m.end():].lstrip()
            led = bool(_QUAL_LEAD_RX.search(before + ' '))
            next_word = after.split()[0] if after else ''
            prev_word = before.split()[-1] if before else ''
            inside_name = (bool(next_word) and next_word[0].isupper() and next_word.lower() not in ('who', 'that')) \
                or (bool(prev_word) and prev_word[0].isupper() and prev_word.lower() not in _NOT_ENTITY
                    and not _QUAL_LEAD_RX.search(before + ' '))
            if led or not inside_name:
                if code not in out:
                    out.append(code)
                break
    # "in CA" / "in TX" as a standalone upper-case code after a geography lead
    for m in re.finditer(r"\b(?:in|from|across|within)\s+([A-Z]{2})\b", str(text or '')):
        code = m.group(1)
        if code in STATE_NAMES and code not in out and code not in ('IN', 'OR', 'ME', 'OK', 'HI', 'DE', 'ID', 'LA'):
            out.append(code)
    return out


def lean_in(text):
    m = _LEAN_RX.search(str(text or ''))
    if not m:
        return ''
    return _LEAN_CANON.get(m.group('lean').lower().replace('-', ' '), m.group('lean').title())


def anchor_in(text):
    """The entity the cohort is anchored on, as written ('LA Rams',
    'Hilton'), or ''. Qualifier words never count as the entity."""
    t = str(text or '')
    cands = []
    for m in _ANCHOR_OF_RX.finditer(t):
        cands.append(m.group('e'))
    for m in _ANCHOR_PRE_RX.finditer(t):
        cands.append(m.group('e'))
    for m in _ANCHOR_VERB_RX.finditer(t):
        cands.append(m.group('e'))
    geo_states = set(states_in(t))
    geo_words = {n for n, c in US_STATES.items() if c in geo_states}
    for raw in cands:
        words = [w for w in raw.split() if w]
        def _drop(w):
            wl = w.lower()
            if wl in US_STATES:
                return wl in geo_words          # a state word is dropped only when it is the geography
            return wl in _NOT_ENTITY
        while words and _drop(words[0]):
            words.pop(0)
        while words and _drop(words[-1]):
            words.pop()
        if not words:
            continue
        e = ' '.join(words).strip(" ,.;:'\"")
        if len(e) >= 2:
            return e
    return ''


def detect(text):
    """{'anchor', 'states', 'lean', 'demo'} when the ask is a compound
    cohort anchored on an entity with at least one qualifier; None
    otherwise ("fans of Taylor Swift" alone is an entity build)."""
    t = str(text or '')
    anchor = anchor_in(t)
    if not anchor:
        return None
    states = states_in(t)
    lean = lean_in(t)
    demo = bool(_DEMO_RX.search(t))
    if not (states or lean or demo):
        return None
    return {'anchor': anchor, 'states': states, 'lean': lean, 'demo': demo}


def label_for(det, model_subject=''):
    """A clean universe name: 'California Republican Rams Fans'. The
    model's own subject stands when it is short and names the anchor."""
    anchor = str(det.get('anchor') or '').strip()
    ms = str(model_subject or '').strip()
    short = anchor.split()[-1] if anchor else ''
    if ms and len(ms.split()) <= 6 and short and short.lower() in ms.lower() \
            and re.search(r"\b(?:" + _ANCHOR_WORDS + r")$", ms, re.I):
        return ms
    words = anchor.split()
    if len(words) > 1 and re.fullmatch(r"[A-Z]{2}", words[0]):
        words = words[1:]            # 'LA Rams' reads 'Rams' inside the label
    parts = []
    if det.get('states'):
        parts.append(STATE_NAMES.get(det['states'][0], det['states'][0]))
    if det.get('lean'):
        parts.append(det['lean'])
    parts.append(' '.join(words))
    parts.append('Fans')
    return ' '.join(parts)


def universe_note(det):
    """One plain sentence for the persona notes so the engine reads the
    cohort the way it was asked."""
    anchor = det.get('anchor') or 'the subject'
    bits = []
    if det.get('lean'):
        bits.append(f"{det['lean']} voters")
    if det.get('states'):
        names = ', '.join(STATE_NAMES.get(s, s) for s in det['states'])
        bits.append(f"living in {names}")
    who = ' '.join(bits) if bits else 'people'
    note = (f"Universe: {who} who are fans of {anchor}. {anchor} is pinned at 100 in its home column; "
            f"every member engaged with {anchor} in the window.")
    if det.get('states'):
        note += " LOCATION carries only markets in " + ', '.join(STATE_NAMES.get(s, s) for s in det['states']) + "."
    if det.get('lean'):
        note += (f" Politics, news media, podcasts and causes read {det['lean']}-leaning for this cohort; "
                 "do not flatten them to the US average.")
    return note


def apply_to_draft(draft, text):
    """Mark the draft as a compound cohort when the ask is one. Mutates
    in place; returns the detection or None."""
    det = detect(text)
    if not det or not isinstance(draft, dict):
        return None
    draft['_persona_universe'] = True
    draft['subject_verified'] = True
    draft['anchor_entity'] = det['anchor']
    if det.get('states'):
        draft['geo_scope'] = list(det['states'])
    if det.get('lean'):
        draft['political_lean'] = det['lean']
    # The label is composed only for a geography / lean compound; a
    # demographic-only cohort ("Hilton guests who are Lady Gaga fans,
    # women age 30-35") keeps the name the persona flow already gives it.
    label = label_for(det, draft.get('subject') or draft.get('name') or '') if (det.get('states') or det.get('lean')) else ''
    if label and label != str(draft.get('subject') or ''):
        draft['subject'] = label
        draft['name'] = label
        stem = re.sub(r"\s+", "_", label)
        stem = ''.join(ch for ch in stem if ch.isalnum() or ch in '_-')
        if stem:
            draft['file_stem'] = stem
    note = universe_note(det)
    pn = str(draft.get('persona_notes') or '').strip()
    if note not in pn:
        draft['persona_notes'] = (pn + ('\n' if pn else '') + note)[:4000]
    return det


def apply_to_spec(spec, draft):
    """Carry the cohort fields from the draft onto the engine spec."""
    if not isinstance(spec, dict) or not isinstance(draft, dict):
        return spec
    for k in ('anchor_entity', 'political_lean'):
        v = draft.get(k)
        if isinstance(v, str) and v.strip():
            spec[k] = v.strip()[:120]
    gs = draft.get('geo_scope')
    if isinstance(gs, (list, tuple)):
        codes = [str(s).strip().upper() for s in gs if str(s).strip().upper() in STATE_NAMES]
        if codes:
            spec['geo_scope'] = codes
    return spec


__all__ = ['US_STATES', 'STATE_NAMES', 'states_in', 'lean_in', 'anchor_in', 'detect',
           'label_for', 'universe_note', 'apply_to_draft', 'apply_to_spec']
