"""What the room said about the people named on the test (Digital Twins
IQ Pilot Testing, 2026-11-04).

Jenna, 2026-11-04: "on the report and in that section it should also
list what they said/thought about the talent that was called out during
the data entry prior to starting. like since I put those three talent it
should say that they felt jennifer was under used, shes the reason they
would come back, scott was gratuitous but they liked the relationship,
etc"

The setup names the people in ``session['cast']`` (the Cast field on the
test form, 'LL Cool J', 'Jennifer Beals as Lena'); ``cast_faces`` carries
the same list resolved, and every scene the room watched live carries the
faces seen in it. For each named person this module gathers what the room
said and thought:

  remarks   the seats' reactions during the watch (session['events'], with
            clocks) and the live thoughts feed (session['live']['thoughts'],
            per seat, with clocks)
  ratings   the per-seat ratings of the characters on the card
            (diagnostics.characters.by_seat)
  answers   the room's answers and voices on every question that bears on
            the person (session['questions']): by name, by the character's
            name, or by a role phrase the questions bind to them ('the
            woman running the team' is Jennifer Beals when the question
            that named her put it that way)
  dial      the room's dial across the person's scenes against the whole cut

and writes two or three plain sentences per person in the room's own
terms, with one quote from a seat, through one call to the room's
moderator model. When the model is off the deterministic read writes from
the two most-mentioned themes per person, the room's answer to the
question that named them, and the top quote.

The read agrees with the character metrics on the card: the person who
leads 'who they would follow into a second episode' is never called
forgettable; the person at the bottom of it is never called the draw. The
metrics ride into the call as constraints and the sentences are checked
after; a read that breaks them falls back to the deterministic one. The
metrics and the questions it was built against sign the read, so it is
regenerated whenever the metrics move (the reconcile that lets the room's
spoken answers outrank the inferred scores) or a question that names a
person lands.

Stored at ``session['talent_read']`` ({at, sig, source, people: [{name,
role, said, quote: {seat, name, clock, line}, clocks, evidence}]}) and
mirrored as ``room_said`` on each row of
``diagnostics.characters.characters`` for the card, the report and the CSV.
Never raises into a build.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Optional

TALENT_READ_VERSION = 'talent-read-1'
SENTENCES_MAX = 3
SAID_MAX_CHARS = 560         # three short sentences; a read that runs past this loses its tail
QUOTE_MAX = 230
EVIDENCE_LINES_MAX = 28      # remarks per person handed to the model
ANSWERS_MAX = 16             # answers per bound question handed to the model
MODEL_MAX_TOKENS = 3000      # three people at three sentences and a quote each; the first chain model ran past 1800 on the NCIS test

_ROLE_NOUNS = r"(?:woman|man|guy|lady|girl|boy|boss|chief|agent|partner|lead|captain|lieutenant|detective|cop|officer|kid|mother|mom|father|dad|wife|husband|son|daughter|doctor|nurse|lawyer|teacher|coach|rookie|veteran|sergeant|director|analyst|hacker|killer|suspect|victim)"
_ROLE_PHRASE_RX = re.compile(r"\b(the " + _ROLE_NOUNS + r"(?: (?:running|leading|heading|in charge of|behind|at|in|on|with|from|who runs|who leads) the [a-z]+(?: [a-z]+)?)?)\b", re.I)
_ROLE_TAIL_VERBS = {'land', 'landed', 'lands', 'read', 'reads', 'came', 'come', 'comes', 'felt', 'feel', 'feels', 'play', 'played', 'plays', 'work', 'worked', 'works', 'hold', 'holds', 'held', 'do', 'does', 'did',
                    'was', 'were', 'is', 'are', 'had', 'has', 'have', 'got', 'get', 'gets', 'seem', 'seemed', 'seems', 'change', 'changes', 'changed', 'bring', 'brings', 'brought', 'would', 'could', 'should',
                    'make', 'makes', 'made', 'go', 'goes', 'went', 'keep', 'keeps', 'kept', 'take', 'takes', 'took', 'give', 'gives', 'gave', 'carry', 'carries', 'carried', 'run', 'runs', 'ran', 'look', 'looks',
                    'looked', 'say', 'says', 'said', 'need', 'needs', 'want', 'wants', 'press', 'start', 'starts', 'end', 'ends', 'close', 'closes', 'closing', 'open', 'opens', 'win', 'wins', 'and', 'or', 'but',
                    'who', 'that', 'which', 'when', 'if', 'then', 'so', 'because', 'as', 'than', 'more', 'less', 'again', 'back', 'next', 'now', 'too', 'also', 'just', 'still', 'even', 'not'}
_COMMON_TOKENS = {'the', 'and', 'cool', 'king', 'young', 'little', 'big', 'jay', 'ray', 'may', 'will', 'grace', 'hope', 'joy', 'rose', 'bill', 'rich', 'art', 'guy', 'frank', 'mark', 'pat', 'sam', 'max', 'lee', 'chase', 'hunter', 'reed', 'miles', 'wood', 'stone', 'hill', 'brown', 'white', 'black', 'green', 'ford', 'sun', 'star', 'love', 'lane'}

# the themes a room talks about when it talks about a person, in the words the deterministic read uses
_THEMES = (
    ('more', 'wanted more of them', r"\b(more of (?:her|him|them|this one)|wanted more|want(?:ed)? to see more|not enough of|under ?used|more of the|give me more|more to do|more weight|more scenes|front and center|let her close|let him close|closing scene|the final scene|the last scene|the ending moment|in that ending|close it out)\b"),
    ('command', 'carried the room when on screen', r"\b(presence|command(?:ed|ing)?|authority|owned the room|holds? the room|held the room|in charge|runs? the room|running the room|leading (?:man|woman)|anchor|carries? (?:a |the )?scene|screen presence|the rank|real leader|the leader|strongest person)\b"),
    ('partnership', 'liked the partnership', r"\b(partner(?:ship)?|the two of them|those two|these two|together|chemistry|dynamic|duo|the pair|banter|back[- ]and[- ]forth|the shoulder|hand on (?:the|his|a) shoulder|history between)\b"),
    ('funny', 'got a laugh out of them', r"\b(laugh(?:ed|s)?|funny|chuckle|grin(?:ned|ning)?|joke|comic|humor|smil(?:e|ed|ing))\b"),
    ('gratuitous', 'read as showing off', r"\b(gratuitous|showing off|show[- ]?off|too much|enjoying (?:this|it) (?:way )?too much|cocky|reckless|grandstand(?:ing)?|over the top|hamm(?:ing|y)|mugging|doing (?:his|her) (?:\w+ )?thing|a little much|laying it on)\b"),
    ('familiar', 'knew them from before', r"\b(i know (?:him|her|that face)|the one i know|know (?:him|her) from|recogni[sz]ed?|from (?:the )?ncis nights|from the old show|classic\b)"),
    ('looks', 'liked looking at them', r"\b(so fine|looking like that|good[- ]looking|handsome|gorgeous|hot\b|easy on the eyes|not complaining)\b"),
    ('intense', 'sat up for their scenes', r"\b(sat up|sat forward|leaned (?:right )?in|moved up in my seat|put the phone down|pinned|on the wall|against the wall|slamm(?:ed|ing)|tense|i'm in|now i'm watching|i want the story)\b"),
    ('unclear', 'was not sure who they were', r"\b(who is (?:she|he|this|that)|who's (?:she|he|this|that)|on our side|could not keep (?:her|him) apart|hard to place|did not register|didn't register|barely registered|forgettable)\b"),
    ('absent', 'missed them in the ending', r"\b(not (?:even )?in (?:that|the) (?:closing|final|ending|last)|gone from it|nowhere in it|stepped back from it|handed the (?:close|ending|last image)|off to the side|the sidelines|watching it from)\b"),
)
_THEME_RX = {k: re.compile(rx, re.I) for k, _lab, rx in _THEMES}
_THEME_LABEL = {k: lab for k, lab, _rx in _THEMES}

# what the constraints forbid, by position on 'follow into a second episode'
_FORGETTABLE_RX = re.compile(r"\b(forgettable|barely registered|did not register|didn't register|a miss\b|did not land|didn't land|nobody noticed|no one noticed|fell flat|left no mark|an afterthought|easy to forget|disappear(?:ed|s) into)\b", re.I)
_DRAW_RX = re.compile(r"\b(the draw\b|the reason (?:they|the room|most|many|people) (?:would|will|'d) (?:come|be|tune|press|open|watch|keep)|reason to come back|brings? them back|bring(?:s)? the room back|why they would come back|why they'd come back|carr(?:y|ies|ied) the (?:show|pilot|episode)|the anchor of the (?:show|pilot|episode)|who they would follow|the one they would follow|the one they are following|the reason they would watch)\b", re.I)

TALENT_SYSTEM = """You are Prometheus, the moderator, writing up for a senior executive what the room said and thought about the people the client named before the screening. Plain English a high school student follows; the thinking is senior consultant.
You are given, for each named person: the role they carried in the cut, the character metrics from the card (the share rating them excellent against the usual mark for the part, time on screen, where they stand on who the room would follow into a second episode), the seats' remarks about them during the watch with the clock on each, the room's answers and voices on every question that bears on them (a role phrase like 'the woman running the team' is resolved for you), how the seats rated them, and how the dial moved in their scenes.
Write, for each person:
- said: two or three short sentences (each under 25 words, the whole under 70 words), in the room's own terms, on what they felt about this person and why: what landed, what they wanted more or less of, what they would come back for. One idea per sentence; no lists inside a sentence. Say shares in plain words with the count when it helps ('ten of sixteen said more of her brings them back'). Ground every claim in the evidence given; never invent a remark, a scene or a number. Speak of the person the way the room did, by name; never write 'the actor' or 'the actress', never name a source, say 'the usual mark' rather than 'norm', never use the words research, model, synthetic, estimated, panel, sample, confidence, data, persona, twin.
- quote: one line a seat actually said, copied word for word from the remarks or the answers given (you may stop it early at a sentence end), with that seat's number.
Constraints you never break: the person who leads 'follow into a second episode' is the draw and is never written as forgettable, a miss, or someone who did not register; the person at the bottom of it is never written as the draw, the anchor, or the reason the room would come back. If the room's answers pull the other way, say what the room said as the room's words and keep your own framing inside the card's numbers. Time on screen is a fact you may state, never a reason anyone rates higher or lower and never a reason to doubt what the room said: a cameo can be the best part of the show.
No em dashes. Return JSON only: {"people": [{"name": "<as given>", "said": "<two or three sentences>", "quote": "<a seat's line>", "quote_seat": <seat number>}]}"""


# ------------------------------------------------------------- helpers
def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _clock(t) -> str:
    try:
        s = max(0, int(round(float(t))))
    except (TypeError, ValueError):
        s = 0
    return f"{s // 60}:{s % 60:02d}"


def _norm(s: str) -> str:
    return ' '.join(re.sub(r"[^a-z0-9 ]+", ' ', str(s or '').lower()).split())


def _count_words(n: int, of: int) -> str:
    words = {0: 'none', 1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six', 7: 'seven', 8: 'eight', 9: 'nine', 10: 'ten', 11: 'eleven', 12: 'twelve',
             13: 'thirteen', 14: 'fourteen', 15: 'fifteen', 16: 'sixteen', 17: 'seventeen', 18: 'eighteen', 19: 'nineteen', 20: 'twenty'}
    a = words.get(int(n), str(int(n)))
    b = words.get(int(of), str(int(of)))
    return f"{a} of {b}"


def _ordinal(i: int) -> str:
    return {1: 'first', 2: 'second', 3: 'third', 4: 'fourth', 5: 'fifth'}.get(int(i), f"{int(i)}th")


def _scrub(s: str) -> str:
    try:
        from migration.digital_twin_room import scrub
        t = scrub(s)
    except Exception:
        t = str(s or '')
    t = t.replace('\u2014', ', ').replace('\u2013', '-')
    t = re.sub(r"\bthe act(?:or|ress)\b", 'the person', t, flags=re.I)
    return ' '.join(t.split())


_SENT_RX = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


def _sentences(text: str) -> list:
    """Sentences of a read; a question quoted inside a sentence ('Asked
    "...?", ten of sixteen ...') does not end one."""
    return [s for s in _SENT_RX.split(str(text or '').strip()) if s.strip()]


def _trim_quote(line: str, limit: int = QUOTE_MAX) -> str:
    s = ' '.join(str(line or '').split())
    if len(s) <= limit:
        return s
    cut = s[:limit]
    m = re.search(r"^(.*[.!?])\s", cut)
    if m and len(m.group(1)) >= 60:
        return m.group(1)
    sp = cut.rfind(' ')
    return (cut[:sp] if sp > 60 else cut).rstrip(' ,;:') + '...'


# ------------------------------------------------------------- the people
def named_people(session: dict) -> list:
    """The people the setup named, as [{name, character, entered}]; the
    typed order is kept. Empty when the setup named nobody."""
    try:
        from migration.digital_twin_cast import parse_cast
        people = parse_cast((session or {}).get('cast') or [])
    except Exception:
        people = [{'entered': str(c), 'actor': str(c), 'character': ''} for c in ((session or {}).get('cast') or []) if str(c or '').strip()]
    faces = {}
    for p in (((session or {}).get('cast_faces') or {}).get('people') or []):
        if isinstance(p, dict) and p.get('actor'):
            faces[_norm(p.get('entered') or p['actor'])] = p
    out = []
    for p in people:
        face = faces.get(_norm(p.get('entered'))) or faces.get(_norm(p.get('actor'))) or {}
        name = str(face.get('actor') or p.get('actor') or '').strip()
        if not name:
            continue
        out.append({'name': name, 'character': str(p.get('character') or face.get('character') or '').strip(), 'entered': str(p.get('entered') or name)})
    return out[:16]


def _actor(name: str) -> str:
    """The person's name out of a cast entry ('Jennifer Beals as Lena' ->
    'Jennifer Beals'); a bare name comes back as it is."""
    try:
        from migration.digital_twin_cast import parse_cast
        got = parse_cast([name])
        return str(got[0]['actor']) if got else str(name or '')
    except Exception:
        return str(name or '')


def _same(a: str, b: str) -> bool:
    """One person under two spellings, either side a cast entry with the
    character attached."""
    try:
        from migration.digital_twin_cast import same_person
        return bool(same_person(_actor(a), _actor(b)))
    except Exception:
        return _norm(_actor(a)) == _norm(_actor(b))


class _NameRx:
    """The ways a seat names this person: the full name and a distinctive
    first or last token (any case), the character's name, and a short
    all-caps token ('LL') matched as written so "I'll" never counts."""

    def __init__(self, person: dict):
        loose, strict = [], []
        for raw in (person.get('name'), person.get('character')):
            n = ' '.join(str(raw or '').split())
            if not n:
                continue
            loose.append(re.escape(n))
            toks = n.split()
            if len(toks) >= 2:
                for tok in (toks[0], toks[-1]):
                    if len(tok) >= 3 and tok.lower() not in _COMMON_TOKENS:
                        loose.append(re.escape(tok))
                    elif tok.isupper() and len(tok) >= 2:
                        strict.append(re.escape(tok))
        edge_l, edge_r = r"(?<![A-Za-z'])", r"(?![A-Za-z])"
        self.loose = re.compile(edge_l + '(?:' + '|'.join(loose or [r"(?!x)x"]) + ')' + edge_r, re.I)
        self.strict = re.compile(edge_l + '(?:' + '|'.join(strict) + ')' + edge_r) if strict else None

    def search(self, text):
        t = str(text or '')
        return self.loose.search(t) or (self.strict.search(t) if self.strict else None)


def _name_regex(person: dict) -> _NameRx:
    return _NameRx(person)


def _mentions(rx, phrases: list, text: str) -> bool:
    t = str(text or '')
    if rx.search(t):
        return True
    low = t.lower()
    return any(ph and ph in low for ph in phrases)


def _restore_names(text: str, people: list) -> str:
    """A question typed in lower case ('how did you feel about jennifer
    beals?') reads back with the names as the setup spelled them."""
    t = str(text or '')
    for p in people or []:
        for raw in (p.get('name'), p.get('character')):
            n = ' '.join(str(raw or '').split())
            if len(n) >= 3:
                t = re.sub(r"(?<![A-Za-z'])" + re.escape(n) + r"(?![A-Za-z])", n, t, flags=re.I)
    return t


# ------------------------------------------------------- role phrases
def role_phrases(session: dict, people: list) -> dict:
    """{name: [phrases]}: the role phrases the questions bind to a person.
    A question whose text names exactly one person teaches the phrases
    its framing and options use for them ('the woman running the team');
    a two-word head ('the woman') carries when no one else owns it."""
    rxs = {p['name']: _name_regex(p) for p in people}
    learned = {p['name']: [] for p in people}
    for q in (session or {}).get('questions') or []:
        if not isinstance(q, dict):
            continue
        qtext = str(q.get('q') or '')
        named = [n for n, rx in rxs.items() if rx.search(qtext)]
        if len(named) != 1:
            continue
        who = named[0]
        blob = ' '.join([str(q.get('framing') or ''), qtext] + [str(o.get('label') or '') for o in (q.get('options') or []) if isinstance(o, dict)])
        for m in _ROLE_PHRASE_RX.finditer(blob):
            toks = m.group(1).lower().split()
            while len(toks) > 2 and toks[-1] in _ROLE_TAIL_VERBS:
                toks.pop()
            if len(toks) >= 4 and toks[-2] == 'the' and toks[-1] in _ROLE_TAIL_VERBS:
                toks = toks[:-2]
            ph = ' '.join(toks)
            if len(toks) >= 2 and ph not in learned[who]:
                learned[who].append(ph)
    heads = {}
    for who, phs in learned.items():
        for ph in phs:
            head = ' '.join(ph.split()[:2])
            heads.setdefault(head, set()).add(who)
    out = {}
    for who, phs in learned.items():
        full = list(phs)
        for ph in phs:
            head = ' '.join(ph.split()[:2])
            if len(heads.get(head) or ()) == 1 and head not in full:
                full.append(head)
        full.sort(key=lambda s: -len(s))
        out[who] = full[:8]
    return out


# ------------------------------------------------------------- evidence
def _roster(session: dict) -> dict:
    return {int(r.get('seat') or 0): r for r in ((session or {}).get('group') or {}).get('roster') or [] if isinstance(r, dict)}


def _remarks(session: dict, rx: re.Pattern, phrases: list, roster: dict) -> list:
    """The seats' lines about the person during the watch, in clock
    order: reactions (events) and the live thoughts feed."""
    out = []
    for e in (session or {}).get('events') or []:
        if not isinstance(e, dict) or not _mentions(rx, phrases, e.get('line')):
            continue
        sno = int(e.get('seat') or 0)
        out.append({'t': float(e.get('t') or 0), 'clock': _clock(e.get('t')), 'seat': sno, 'name': str(e.get('name') or (roster.get(sno) or {}).get('name') or ''),
                    'type': str(e.get('type') or ''), 'line': ' '.join(str(e.get('line') or '').split()), 'kind': 'reaction'})
    for sno, lst in (((session or {}).get('live') or {}).get('thoughts') or {}).items():
        try:
            seat = int(sno)
        except (TypeError, ValueError):
            continue
        for item in lst or []:
            if not (isinstance(item, (list, tuple)) and len(item) >= 2):
                continue
            t, say = item[0], item[1]
            if not _mentions(rx, phrases, say):
                continue
            out.append({'t': float(t or 0), 'clock': _clock(t), 'seat': seat, 'name': str((roster.get(seat) or {}).get('name') or seat), 'type': 'thought',
                        'line': ' '.join(str(say or '').split()), 'kind': 'thought'})
    out.sort(key=lambda r: (r['t'], r['seat']))
    seen, uniq = set(), []
    for r in out:
        k = (r['seat'], _norm(r['line'])[:80])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq


def _bound_questions(session: dict, rx: re.Pattern, phrases: list) -> list:
    """The finished questions that bear on the person: the question, its
    framing or an option names them (by name, character or role phrase)."""
    out = []
    for q in (session or {}).get('questions') or []:
        if not isinstance(q, dict) or not (q.get('read') or q.get('answers') or q.get('options')):
            continue
        if q.get('stage') and str(q.get('stage')) not in ('done',):
            continue
        texts = [str(q.get('q') or ''), str(q.get('framing') or '')] + [str(o.get('label') or '') for o in (q.get('options') or []) if isinstance(o, dict)]
        hit_q = _mentions(rx, phrases, texts[0]) or _mentions(rx, phrases, texts[1])
        hit_opt = any(_mentions(rx, phrases, t) for t in texts[2:])
        if hit_q or hit_opt:
            out.append((q, 'question' if hit_q else 'option'))
    return out


def _question_evidence(q: dict, how: str, rx: re.Pattern, phrases: list, roster: dict) -> dict:
    """One bound question as evidence: the split with the seat tally, the
    answers (every seat's line when the question names the person; the
    seats who picked an option that names them when only an option
    does), the voices."""
    from migration.digital_twin_pilot import question_clock, question_split
    opts = [o for o in (q.get('options') or []) if isinstance(o, dict)]
    split = []
    for r in question_split(q):
        split.append({'label': r.get('label'), 'pct': r.get('pct'), 'tally': r.get('tally') or ''})
    naming_ids = {o.get('id') for o in opts if _mentions(rx, phrases, o.get('label'))}
    answers = []
    for a in q.get('answers') or []:
        if not isinstance(a, dict):
            continue
        if how == 'option' and a.get('option') not in naming_ids and not _mentions(rx, phrases, a.get('line')):
            continue
        sno = int(a.get('seat') or 0)
        answers.append({'seat': sno, 'name': str(a.get('name') or (roster.get(sno) or {}).get('name') or ''), 'option': str(a.get('option_label') or ''), 'line': ' '.join(str(a.get('line') or '').split())})
    voices = [{'seat': int(v.get('seat') or 0), 'name': str(v.get('name') or ''), 'line': ' '.join(str(v.get('line') or '').split())} for v in (q.get('voices') or []) if isinstance(v, dict)]
    lead = max(split, key=lambda r: float(r.get('pct') or 0)) if split else None
    return {'id': q.get('id'), 'q': str(q.get('q') or ''), 'clock': question_clock(q), 'how': how, 'split': split, 'lead': lead, 'answers': answers[:ANSWERS_MAX], 'voices': voices[:4],
            'read': str(q.get('read_short') or q.get('read') or '')[:500]}


def _person_scenes(session: dict, person: dict) -> set:
    """The scenes the person is in: the character table's own scene list,
    the faces the live read saw in a scene, and the scenes whose caption
    or dialogue names them (by name or by character)."""
    ids = set()
    try:
        from migration.digital_twin_diagnostics import cast_from_session
        for c in cast_from_session(session):
            if _same(c.get('name'), person.get('name')):
                ids |= {int(x) for x in (c.get('scenes') or [])}
    except Exception:
        pass
    rx = _name_regex(person)
    for s in (session or {}).get('scenes') or []:
        if not isinstance(s, dict) or s.get('id') is None:
            continue
        faces = s.get('cast') or s.get('cast_on_screen') or []
        if any(_same(str(f), person.get('name')) for f in faces if str(f or '').strip()):
            ids.add(int(s['id']))
        elif rx.search(f"{s.get('caption') or ''} {s.get('dialogue') or ''}"):
            ids.add(int(s['id']))
    return ids


def _dial_in_scenes(session: dict, person: dict) -> dict:
    """The room's dial across the person's scenes against the whole cut,
    and the clock where it peaked inside them."""
    beats = (session or {}).get('beats') or []
    dial = ((session or {}).get('dial') or {}).get('value') or []
    if not beats or not dial:
        return {}
    ids = _person_scenes(session, person)
    idx = [i for i, b in enumerate(beats) if b.get('scene_id') in ids and i < len(dial)]
    if not idx:
        return {}
    vals = [float(dial[i]) for i in idx]
    allv = [float(v) for v in dial[:len(beats)]]
    peak_i = max(idx, key=lambda i: float(dial[i]))
    return {'scenes': len(ids), 'mean': round(sum(vals) / len(vals), 1), 'room_mean': round(sum(allv) / len(allv), 1) if allv else None,
            'gap': round(sum(vals) / len(vals) - (sum(allv) / len(allv) if allv else 0), 1), 'peak_clock': _clock(beats[peak_i].get('t')), 'peak': round(float(dial[peak_i]), 1)}


def _metrics(session: dict, people: list) -> dict:
    """The character metrics on the card for every named person: the row,
    the follow share and rank among the named people, the relationship."""
    ch = ((session or {}).get('diagnostics') or {}).get('characters') or {}
    rows = ch.get('characters') or []
    follow = ch.get('follow') or []
    rel = ch.get('relationship') or {}
    out = {}
    fol_named = []
    for p in people:
        row = next((r for r in rows if _same(r.get('name'), p['name'])), None)
        f = next((x for x in follow if _same(x.get('name'), p['name'])), None)
        if f and f.get('pct') is not None:
            fol_named.append((p['name'], float(f['pct'])))
        out[p['name']] = {'role': (row or {}).get('role') or '', 'excellent_pct': (row or {}).get('excellent_pct'), 'norm': (row or {}).get('norm'), 'vs_norm_pp': (row or {}).get('vs_norm_pp'),
                          'screen_clock': (row or {}).get('screen_clock') or '', 'time_vs_like': (row or {}).get('time_vs_like') or '', 'follow_pct': float(f['pct']) if f and f.get('pct') is not None else None,
                          'in_relationship': bool(rel and any(_same(x, p['name']) for x in (rel.get('pair') or []))), 'relationship_excellent_pct': rel.get('excellent_pct') if rel else None,
                          'relationship_with': next((x for x in (rel.get('pair') or []) if not _same(x, p['name'])), '') if rel and any(_same(x, p['name']) for x in (rel.get('pair') or [])) else ''}
    fol_named.sort(key=lambda kv: -kv[1])
    for i, (n, _p) in enumerate(fol_named, 1):
        out[n]['follow_rank'] = i
        out[n]['follow_of'] = len(fol_named)
        out[n]['follow_leads'] = (i == 1 and len(fol_named) >= 2)
        out[n]['follow_bottom'] = (i == len(fol_named) and len(fol_named) >= 2)
    for p in people:
        out[p['name']].setdefault('follow_rank', None)
        out[p['name']].setdefault('follow_of', len(fol_named))
        out[p['name']].setdefault('follow_leads', False)
        out[p['name']].setdefault('follow_bottom', False)
    return out


def _ratings(session: dict, person_name: str) -> dict:
    by_seat = (((session or {}).get('diagnostics') or {}).get('characters') or {}).get('by_seat') or []
    counts = {'a': 0, 'b': 0, 'c': 0, 'd': 0, 'e': 0}
    n = 0
    for row in by_seat:
        rt = (row or {}).get('ratings') or {}
        key = next((k for k in rt if _same(k, person_name)), None)
        if key is None:
            continue
        v = str(rt.get(key) or '')
        if v in counts:
            counts[v] += 1
            n += 1
    return {'seats': n, 'excellent': counts['a'], 'very_good': counts['b'], 'good': counts['c'], 'fair': counts['d'], 'poor': counts['e']}


def gather_evidence(session: dict) -> list:
    """Everything the read draws on, per named person. [] when the setup
    named nobody or the test has no card yet."""
    people = named_people(session)
    if not people or not (session or {}).get('scorecard'):
        return []
    roster = _roster(session)
    phrases = role_phrases(session, people)
    metrics = _metrics(session, people)
    out = []
    for p in people:
        rx = _name_regex(p)
        phs = phrases.get(p['name']) or []
        remarks = _remarks(session, rx, phs, roster)
        qs = [_question_evidence(q, how, rx, phs, roster) for q, how in _bound_questions(session, rx, phs)]
        out.append({'name': p['name'], 'character': p.get('character') or '', 'role_phrases': phs, 'metrics': metrics.get(p['name']) or {}, 'remarks': remarks,
                    'questions': qs, 'ratings': _ratings(session, p['name']), 'dial': _dial_in_scenes(session, p), 'seats_total': len(roster)})
    return out


def _basis(ev: dict) -> dict:
    """What one person's read was built against: the card's metrics for
    them, the finished questions that bear on them, the count of remarks."""
    return {'m': {k: (ev.get('metrics') or {}).get(k) for k in ('excellent_pct', 'norm', 'follow_pct', 'follow_rank', 'role')},
            'q': [q['id'] for q in ev.get('questions') or []], 'r': len(ev.get('remarks') or [])}


def signature(session: dict, evidence: Optional[list] = None) -> str:
    """What the read was built against: the card's character metrics, the
    finished questions that bear on a person, the count of remarks."""
    ev = evidence if evidence is not None else gather_evidence(session)
    basis = {'v': TALENT_READ_VERSION, 'people': [dict(_basis(e), n=e['name']) for e in ev]}
    return hashlib.sha1(json.dumps(basis, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:16]


# ------------------------------------------------------ the fallback
def _themes(ev: dict) -> list:
    """The two most-mentioned themes about the person across the remarks
    and the answers, as (key, count). The partnership belongs to the two
    people in the key relationship; a critical theme (showing off, not
    sure who they were) counts for more so a minority view surfaces."""
    lines = [r['line'] for r in ev.get('remarks') or []]
    for q in ev.get('questions') or []:
        lines += [a['line'] for a in q.get('answers') or []]
    in_rel = bool((ev.get('metrics') or {}).get('in_relationship'))
    counts, weights = {}, {}
    for line in lines:
        for k, rx in _THEME_RX.items():
            if k == 'partnership' and not in_rel:
                continue
            if rx.search(line):
                counts[k] = counts.get(k, 0) + 1
                weights[k] = weights.get(k, 0.0) + (1.6 if k in ('gratuitous', 'unclear') else 1.0)
    ranked = sorted(counts.items(), key=lambda kv: (-weights[kv[0]], [t[0] for t in _THEMES].index(kv[0])))
    return ranked[:2]


def _line_themes(line: str) -> int:
    return sum(1 for rx in _THEME_RX.values() if rx.search(line))


def _top_quote(ev: dict, exclude: Optional[set] = None) -> Optional[dict]:
    """The one line to quote. Every candidate is a seat's own line: an
    answer on a question about the person, a reaction, a thought. Lines
    about the person by name (or on a question that named them) outrank
    the rest; a line that carries a theme outranks a flat one; love and
    lean-in outrank the rest; a line that fills a sentence outranks a
    fragment. ``exclude`` holds lines already quoted for someone else."""
    rx = _name_regex({'name': ev.get('name'), 'character': ev.get('character')})
    phs = ev.get('role_phrases') or []
    cands = []
    for q in ev.get('questions') or []:
        for a in q.get('answers') or []:
            w = 4.0 if q.get('how') == 'question' else 3.0
            w += 2.5 if (q.get('how') == 'question' or _mentions(rx, phs, a['line'])) else 0.0
            w += min(1.5, 0.5 * _line_themes(a['line']))
            w += min(1.0, len(a['line']) / 240.0)
            cands.append((w, {'seat': a['seat'], 'name': a['name'], 'clock': q.get('clock') or '', 'line': _trim_quote(a['line']), 'from': f"asked {q.get('q')}"}))
    for r in ev.get('remarks') or []:
        w = 3.0 if r['kind'] == 'reaction' else 2.5
        w += 2.5 if _mentions(rx, phs, r['line']) else 0.0
        w += min(1.5, 0.5 * _line_themes(r['line']))
        w += {'love': 1.2, 'lean-in': 0.8, 'wow': 0.8, 'laugh': 0.6}.get(r.get('type'), 0.0)
        w += min(1.0, len(r['line']) / 180.0)
        cands.append((w, {'seat': r['seat'], 'name': r['name'], 'clock': r['clock'], 'line': _trim_quote(r['line']), 'from': 'during the watch'}))
    if not cands:
        return None
    cands.sort(key=lambda c: -c[0])
    if exclude:
        fresh = [c for c in cands if _norm(c[1]['line'])[:80] not in exclude]
        if fresh:
            return fresh[0][1]
    return cands[0][1]


def _theme_clause(k: str, name: str, m: dict, lead: bool) -> str:
    """One theme as the room would say it; ``lead`` opens the sentence,
    otherwise it rides as a second clause."""
    with_ = f" with {m['relationship_with']}" if m.get('relationship_with') else ''
    first = {'more': f"The room wanted more of {name}", 'command': f"{name} carried the room when on screen", 'partnership': f"The room liked {name} in the partnership{with_}",
             'funny': f"{name} got a laugh out of the room", 'gratuitous': f"{name} read as showing off to some seats", 'familiar': f"The room knew {name} from before",
             'looks': f"The room liked looking at {name}", 'intense': f"The room sat up for {name}'s scenes", 'unclear': f"The room was not always sure who {name} was",
             'absent': f"The room missed {name} in the ending"}
    second = {'more': 'and wanted more', 'command': 'and felt the command on screen', 'partnership': f"and liked the partnership{with_}", 'funny': 'and got a laugh',
              'gratuitous': 'though some seats found it a little much', 'familiar': 'and knew the face from before', 'looks': 'and liked the look', 'intense': 'and sat up for the scenes',
              'unclear': 'though some were not sure who that was', 'absent': 'and missed the face in the ending'}
    return (first if lead else second).get(k, '')


def _part(role: str) -> str:
    r = str(role or '').strip()
    return f"{r} part" if r in ('supporting', '') else r


def _fallback_said(ev: dict, people: Optional[list] = None) -> str:
    """Two or three sentences from the themes, the room's answer on the
    question that named them, and the card, inside the constraints."""
    name = ev['name']
    m = ev.get('metrics') or {}
    out = []
    themes = _themes(ev)
    if themes:
        k1, c1 = themes[0]
        first = _theme_clause(k1, name, m, True)
        if len(themes) > 1:
            tail = _theme_clause(themes[1][0], name, m, False)
            first = f"{first}, {tail}" if tail else first
        out.append(f"{first} ({c1} {'line' if c1 == 1 else 'lines'} from the room say so).")
    # the room's answer on the question that named them
    qs = sorted(ev.get('questions') or [], key=lambda q: (q.get('how') != 'question', -len(q.get('answers') or [])))
    if qs and qs[0].get('lead'):
        q = qs[0]
        lead = q['lead']
        mt = re.match(r"(\d+) of (\d+)", str(lead.get('tally') or ''))
        share = _count_words(int(mt.group(1)), int(mt.group(2))) if mt else None
        qt = _restore_names(' '.join(q['q'].split()), people or [{'name': name, 'character': ev.get('character')}]).rstrip('?').strip()
        qt = (qt[0].upper() + qt[1:]) if qt else qt
        if share:
            out.append(f"Asked \"{qt}?\", {share} picked \"{lead['label']}\".")
        else:
            out.append(f"Asked \"{qt}?\", the answer that carried was \"{lead['label']}\".")
    # the card, inside the constraints
    if m.get('excellent_pct') is not None:
        line = f"{name} rates {float(m['excellent_pct']):.1f}% excellent" + (f" ({float(m['norm']):.0f}% is the usual mark for a {_part(m.get('role'))})" if m.get('norm') is not None else '')
        if m.get('follow_leads'):
            line += " and leads on who the room would follow into a second episode."
        elif m.get('follow_rank') and int(m.get('follow_of') or 0) >= 2:
            line += f" and sits {_ordinal(m['follow_rank'])} of {m['follow_of']} on who the room would follow into a second episode."
        else:
            line += '.'
        out.append(line)
    if not out:
        out.append(f"The room did not single {name} out in its remarks or its answers.")
    return ' '.join(out[:SENTENCES_MAX])


def _fallback_person(ev: dict, people: Optional[list] = None, exclude: Optional[set] = None) -> dict:
    quote = _top_quote(ev, exclude)
    if quote and exclude is not None:
        exclude.add(_norm(quote['line'])[:80])
    return {'name': ev['name'], 'role': (ev.get('metrics') or {}).get('role') or '', 'said': _scrub(_fallback_said(ev, people)), 'quote': quote,
            'clocks': _clocks(ev), 'evidence': _evidence_counts(ev), 'source': 'deterministic', 'basis': _basis(ev)}


def _clocks(ev: dict) -> list:
    cl = [r['clock'] for r in ev.get('remarks') or []]
    cl += [q['clock'] for q in ev.get('questions') or [] if q.get('clock')]
    seen, out = set(), []
    for c in cl:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out[:24]


def _evidence_counts(ev: dict) -> dict:
    return {'remarks': len(ev.get('remarks') or []), 'questions': [q['id'] for q in ev.get('questions') or []], 'answers': sum(len(q.get('answers') or []) for q in ev.get('questions') or []),
            'rated_by': int((ev.get('ratings') or {}).get('seats') or 0), 'scenes': int((ev.get('dial') or {}).get('scenes') or 0)}


# ------------------------------------------------------- the constraints
def breaks_constraints(said: str, metrics: dict) -> list:
    """The words a read may not use given the person's place on 'follow
    into a second episode'. [] when it holds."""
    hits = []
    t = str(said or '')
    if (metrics or {}).get('follow_leads') and _FORGETTABLE_RX.search(t):
        hits.append('leads follow, written as forgettable')
    if (metrics or {}).get('follow_bottom') and _DRAW_RX.search(t):
        hits.append('bottom of follow, written as the draw')
    return hits


def _match_quote(ev: dict, quote: str, seat) -> Optional[dict]:
    """The model's quote checked against the evidence: it must be a seat's
    own line (or the start of one). Returns the record with the seat,
    name and clock from the evidence, else None."""
    want = _norm(quote)
    if len(want) < 12:
        return None
    pool = []
    for q in ev.get('questions') or []:
        for a in q.get('answers') or []:
            pool.append({'seat': a['seat'], 'name': a['name'], 'clock': q.get('clock') or '', 'line': a['line'], 'from': f"asked {q.get('q')}"})
        for v in q.get('voices') or []:
            pool.append({'seat': v['seat'], 'name': v['name'], 'clock': q.get('clock') or '', 'line': v['line'], 'from': f"asked {q.get('q')}"})
    for r in ev.get('remarks') or []:
        pool.append({'seat': r['seat'], 'name': r['name'], 'clock': r['clock'], 'line': r['line'], 'from': 'during the watch'})
    try:
        seat_i = int(seat) if seat is not None else None
    except (TypeError, ValueError):
        seat_i = None
    best = None
    for rec in pool:
        have = _norm(rec['line'])
        if want in have or (have in want and len(have) >= 24):
            score = (2 if seat_i is not None and rec['seat'] == seat_i else 1) + (1 if want == have else 0)
            if best is None or score > best[0]:
                best = (score, rec)
    if best is None:
        return None
    rec = dict(best[1])
    # keep the model's shorter cut when it is a clean prefix of the line
    if want != _norm(rec['line']) and _norm(rec['line']).startswith(want):
        cut = ' '.join(str(quote).split())
        rec['line'] = cut if re.search(r"[.!?]$", cut) else _trim_quote(rec['line'])
    else:
        rec['line'] = _trim_quote(rec['line'])
    return rec


# ---------------------------------------------------------- the call
def _user_payload(session: dict, evidence: list) -> str:
    media = (session or {}).get('media') or {}
    people = []
    for ev in evidence:
        m = ev.get('metrics') or {}
        people.append({
            'name': ev['name'], 'character': ev.get('character') or '', 'role_in_cut': m.get('role') or '', 'role_phrases_the_room_used': ev.get('role_phrases') or [],
            'card': {'excellent_pct': m.get('excellent_pct'), 'usual_mark_for_the_part_pct': m.get('norm'), 'on_screen': m.get('screen_clock'), 'time_against_liking': m.get('time_vs_like'),
                     'follow_into_second_episode_pct': m.get('follow_pct'), 'follow_rank_among_named': m.get('follow_rank'), 'follow_rank_of': m.get('follow_of'),
                     'leads_follow': bool(m.get('follow_leads')), 'bottom_of_follow': bool(m.get('follow_bottom')),
                     'key_relationship_with': m.get('relationship_with') or '', 'key_relationship_excellent_pct': m.get('relationship_excellent_pct') if m.get('in_relationship') else None},
            'seat_ratings': ev.get('ratings') or {}, 'dial_in_their_scenes': ev.get('dial') or {},
            'remarks_during_the_watch': [{'clock': r['clock'], 'seat': r['seat'], 'name': r['name'], 'kind': r['type'], 'line': r['line']} for r in (ev.get('remarks') or [])[-EVIDENCE_LINES_MAX:]],
            'questions_that_bear_on_them': [{'question': q['q'], 'asked_at': q['clock'], 'bound_by': q['how'], 'split': q['split'], 'moderator_read': q['read'],
                                             'answers': [{'seat': a['seat'], 'name': a['name'], 'picked': a['option'], 'line': a['line']} for a in q['answers']]} for q in ev.get('questions') or []],
        })
    return json.dumps({'title': media.get('title') or session.get('title') or 'the cut', 'seats_in_room': len(_roster(session)), 'people': people}, default=str)[:60000]


def talent_read(session: dict, twin: Optional[dict] = None, *, models=None, usage_extras: Optional[dict] = None, log=None, deterministic: bool = False) -> dict:
    """The read for every named person: the model's sentences when a model
    answers and they hold the constraints, the deterministic read
    otherwise (and always with ``deterministic``). {at, sig, source,
    people: [...]}. Never raises."""
    evidence = gather_evidence(session)
    sig = signature(session, evidence)
    if not evidence:
        return {'at': _now(), 'sig': sig, 'source': 'none', 'people': [], 'version': TALENT_READ_VERSION}
    named = [{'name': ev['name'], 'character': ev.get('character') or ''} for ev in evidence]
    used = set()
    fallback = {ev['name']: _fallback_person(ev, named, used) for ev in evidence}
    people = []
    source = 'deterministic'
    data, call = None, None
    if not deterministic:
        try:
            from migration.digital_twin_room import Models, model_available
            mdl = models or Models(usage_extras=usage_extras)
            if model_available(getattr(mdl, 'fn', None)):
                data, call = mdl.call('moderator', TALENT_SYSTEM, _user_payload(session, evidence), max_tokens=MODEL_MAX_TOKENS, temperature=0.35, tools=None)
        except Exception as e:
            print(f"[twin-talent-read] model call skipped: {str(e)[:160]}")
            data = None
    if log and call:
        try:
            log('talent_read', call)
        except Exception:
            pass
    got = {}
    if isinstance(data, dict):
        for row in data.get('people') or []:
            if isinstance(row, dict) and row.get('name'):
                got[_norm(row['name'])] = row
    for ev in evidence:
        fb = fallback[ev['name']]
        row = got.get(_norm(ev['name'])) or next((v for k, v in got.items() if _same(k, ev['name'])), None)
        rec = dict(fb)
        if row:
            said = _scrub(str(row.get('said') or ''))
            sents = _sentences(said)[:SENTENCES_MAX]
            while len(sents) > 1 and len(' '.join(sents)) > SAID_MAX_CHARS:   # a read that runs long loses its tail, never its first sentence
                sents = sents[:-1]
            said = ' '.join(sents).strip()
            bad = breaks_constraints(said, ev.get('metrics') or {})
            try:
                from migration.digital_twin_room import has_banned
                bad += has_banned(said)
            except Exception:
                pass
            if said and len(sents) >= 1 and not bad and 'the person' not in said.lower():
                rec['said'] = said
                rec['source'] = 'model'
                q = _match_quote(ev, str(row.get('quote') or ''), row.get('quote_seat'))
                if q:
                    rec['quote'] = q
                source = 'model'
            elif bad:
                print(f"[twin-talent-read] {ev['name']}: model read set aside ({'; '.join(bad)})")
        people.append(rec)
    return {'at': _now(), 'sig': sig, 'source': source, 'people': people, 'version': TALENT_READ_VERSION}


def stamp_rows(session: dict) -> int:
    """room_said on each character row the read names (the card reads the
    rows); returns how many rows were stamped."""
    tr = (session or {}).get('talent_read') or {}
    rows = (((session or {}).get('diagnostics') or {}).get('characters') or {}).get('characters') or []
    n = 0
    by_name = {p['name']: p for p in tr.get('people') or [] if isinstance(p, dict) and p.get('name')}
    for r in rows:
        hit = by_name.get(r.get('name')) or next((v for k, v in by_name.items() if _same(k, r.get('name'))), None)
        if hit:
            r['room_said'] = {'said': hit.get('said') or '', 'quote': hit.get('quote') or None, 'clocks': list(hit.get('clocks') or [])[:12]}
            n += 1
        elif 'room_said' in r:
            r.pop('room_said', None)
    return n


def is_stale(session: dict) -> bool:
    """True when the read was built against other metrics or questions
    than the test carries now (or was never built and could be)."""
    tr = (session or {}).get('talent_read')
    if not named_people(session) or not (session or {}).get('scorecard'):
        return False
    if not isinstance(tr, dict) or not tr.get('sig'):
        return True
    return str(tr.get('sig')) != signature(session)


def attach_talent_read(session: dict, twin: Optional[dict] = None, *, models=None, usage_extras: Optional[dict] = None, force: bool = False, reason: str = '', log=None) -> Optional[dict]:
    """The read on the session, built or rebuilt when the metrics or the
    questions it was built against changed (force rebuilds anyway).
    Returns the read, or None when the test names nobody. Never raises."""
    try:
        if not named_people(session) or not (session or {}).get('scorecard'):
            session.pop('talent_read', None) if isinstance(session, dict) and session.get('talent_read') is not None and not named_people(session) else None
            return None
        if not force and not is_stale(session):
            stamp_rows(session)
            return session.get('talent_read')
        tr = talent_read(session, twin, models=models, usage_extras=usage_extras, log=log)
        if reason:
            tr['reason'] = str(reason)[:80]
        session['talent_read'] = tr
        stamp_rows(session)
        session['updated_at'] = _now()
        print(f"[twin-talent-read] {session.get('id')}: {len(tr.get('people') or [])} people, {tr.get('source')}" + (f" ({reason})" if reason else ''))
        return tr
    except Exception as e:
        print(f"[twin-talent-read] skipped: {str(e)[:200]}")
        return (session or {}).get('talent_read')


def question_names_talent(session: dict, rec: dict) -> bool:
    """True when a question (its text, framing or an option) names a
    person the setup named, by name, character or a role phrase."""
    people = named_people(session)
    if not people or not isinstance(rec, dict):
        return False
    phrases = role_phrases(dict(session, questions=list(session.get('questions') or []) + ([rec] if rec not in (session.get('questions') or []) else [])), people)
    texts = [str(rec.get('q') or ''), str(rec.get('framing') or '')] + [str(o.get('label') or '') for o in (rec.get('options') or []) if isinstance(o, dict)]
    for p in people:
        rx = _name_regex(p)
        if any(_mentions(rx, phrases.get(p['name']) or [], t) for t in texts):
            return True
    return False


# --------------------------------------------------------- the surfaces
def fresh_read(session: dict) -> dict:
    """The read as a surface should show it now: the stored one when it
    was built against the metrics and questions the test carries, else
    the deterministic read against the current metrics (no model call on
    a page load or an export; the stored read is rebuilt with the model
    the next time a question lands). Nothing is written to the session."""
    tr = (session or {}).get('talent_read')
    try:
        if isinstance(tr, dict) and tr.get('people') and not is_stale(session):
            return tr
        if not named_people(session) or not (session or {}).get('scorecard'):
            return tr if isinstance(tr, dict) else {}
        out = talent_read(session, deterministic=True)
        # a person whose own metrics and questions did not move keeps the stored sentences (the moderator's prose) when they
        # still hold the constraints; only the people the card moved under are re-read
        stored = {p.get('name'): p for p in ((tr or {}).get('people') or []) if isinstance(p, dict) and p.get('name')} if isinstance(tr, dict) else {}
        metrics = _metrics(session, named_people(session))
        kept = []
        for p in out.get('people') or []:
            old = stored.get(p['name'])
            if old and old.get('said') and old.get('basis') == p.get('basis') and not breaks_constraints(old['said'], metrics.get(p['name']) or {}):
                kept.append(old)
            else:
                kept.append(p)
        out['people'] = kept
        out['source'] = 'model' if any(p.get('source') == 'model' for p in kept) else out.get('source')
        out['refreshed'] = True
        return out
    except Exception as e:
        print(f"[twin-talent-read] fresh read skipped: {str(e)[:160]}")
        return tr if isinstance(tr, dict) else {}


def public_view(session: dict, out: dict) -> dict:
    """The talent read on the session copy a route hands the page: a stale
    read is replaced by the deterministic one against the current card,
    and the character rows on the copy are stamped (the stored session is
    left as it is). Returns ``out``."""
    try:
        tr = fresh_read(session)
        if not isinstance(tr, dict) or not tr.get('people'):
            return out
        if tr is not session.get('talent_read'):
            out['talent_read'] = tr
            d = out.get('diagnostics') if isinstance(out.get('diagnostics'), dict) else None
            if d and isinstance(d.get('characters'), dict):
                import copy
                out['diagnostics'] = dict(d)
                out['diagnostics']['characters'] = copy.deepcopy(d['characters'])
            stamp_rows(out)
    except Exception as e:
        print(f"[twin-talent-read] public view skipped: {str(e)[:160]}")
    return out


def report_lines(session: dict) -> list:
    """[{name, role, said, quote, who}] for the report and the CSV; the
    quote's attribution reads 'Lauren, seat 8, 42:06'. A read built
    against older metrics is replaced by the deterministic one against
    the current card."""
    tr = fresh_read(session) or {}
    out = []
    for p in tr.get('people') or []:
        if not isinstance(p, dict) or not p.get('said'):
            continue
        q = p.get('quote') or {}
        who = ''
        if q:
            bits = [str(q.get('name') or f"seat {q.get('seat')}")]
            if q.get('seat') is not None and q.get('name'):
                bits.append(f"seat {q['seat']}")
            if q.get('clock'):
                bits.append(str(q['clock']))
            who = ', '.join(bits)
        out.append({'name': str(p.get('name') or ''), 'role': str(p.get('role') or ''), 'said': str(p.get('said') or ''), 'quote': str(q.get('line') or '') if q else '', 'who': who})
    return out


__all__ = ['TALENT_SYSTEM', 'TALENT_READ_VERSION', 'named_people', 'role_phrases', 'gather_evidence', 'signature', 'talent_read', 'attach_talent_read', 'stamp_rows', 'is_stale',
           'question_names_talent', 'breaks_constraints', 'fresh_read', 'public_view', 'report_lines']
