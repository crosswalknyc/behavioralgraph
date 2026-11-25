"""Pilot Testing reads the cut with what came before (2026-11-24).

Jenna, on the methodology line "a payoff late in the cut is not read
against a setup the room saw early on" (verbatim): "what does this mean?
wouldnt we want it to be contextually aware of what happened previouslky
in the show?" Then: "just build it".

Two things travel from one part of the cut to the next, so every seat
reacts to a scene knowing what it already saw:

1. ``story``: the story so far, shared by the room. Who is who (the cast
   matched by face, the characters the dialogue names), what has been set
   up and not yet paid off, what has been resolved, where the story and
   its tone stand. Updated after each part, capped at ``STORY_MAX_WORDS``.
2. ``memory``: each archetype representative's own path through the cut.
   Where its dial rose and fell and on what, the reactions it had, what it
   said, how each part sat with it, what it is waiting to see. Capped at
   ``MEMORY_MAX_WORDS`` (``LIVE_MEMORY_MAX_WORDS`` on a live watch).

Nothing here touches the dial's settings: the habit line, its weight, the
people weighting and the end-of-cut settle are the pilot's own. This is
only what a seat knows when it reacts.
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable, Optional

STORY_MAX_WORDS = 300
STORY_LIMITS = {'who': 10, 'set_up': 8, 'resolved': 6}
STORY_ITEM_WORDS = 18
STORY_LINE_WORDS = 30
MEMORY_MAX_WORDS = 110
LIVE_MEMORY_MAX_WORDS = 45
MEMORY_REACTIONS = 6
MEMORY_SAID = 4
MEMORY_NOTES = 3
ROWS_PER_CALL = 96          # seats x scenes in one call; a bigger part splits its seats so each call answers in time
LIVE_STORY_FIRST = 4        # the first live recap after this many frames
LIVE_STORY_EVERY = 6        # then every this many frames (about a minute and a quarter at a frame every 12 seconds)
LIVE_SINCE_MAX = 8          # frames listed after the recap's last frame

EVENT_WORDS = {'love': 'loved it', 'laugh': 'laughed', 'lean-in': 'leaned in', 'confuse': 'lost the thread',
               'check-out': 'checked out', 'wow': 'wow'}

STORY_SYSTEM = """You keep the story so far for a test-screening room that is watching a cut part by part. You are given the story so far (empty at the start) and the next part's scenes in order: the clock, what is on screen, the dialogue heard, and the cast on screen when a face was matched to the cast list.
Return the updated story so far after this part: who is who (the people the scenes name, by exactly the names given, and the characters the dialogue names, each with what they are to the story), what has been set up and not yet paid off (setups, promises, open questions, rivalries, secrets), what has been resolved (a setup that paid off, a question answered, with what answered it), where the story stands now, and the tone it is in.
Only what the scenes so far show: never guess ahead, never invent a name, never name an actor the scenes do not give. Keep it short: at most 10 people, 8 open threads and 6 resolved, one plain sentence each under 18 words; where_it_stands and tone one sentence each. Move a thread from set_up to resolved when it pays off; drop what no longer matters. No em dashes.
Return JSON only: {"who": ["<name: what they are to the story>"], "set_up": ["..."], "resolved": ["..."], "where_it_stands": "...", "tone": "..."}"""

WATCH_CONTEXT = """When this is not the first part, you also have story_so_far (who is who, what has been set up and not yet paid off, what has been resolved, where the story and its tone stand) and, for each person, your_watch_so_far (their own path through the earlier parts: where their dial rose and fell and on what, the reactions they had, what they said, how each part sat with them, what they are waiting to see). Read every scene against what came before: a payoff lands against the setup the person saw, and their line can say so; suspense can build across parts; a slow stretch can pay off later; a person who checked out earlier needs more to come back; a person waiting for something reacts when it comes. Keep each person consistent with what they felt earlier unless this part gives them a reason to change.
For each person also return waiting_for: what they are waiting to see next, under 15 words, or empty."""

LIVE_CONTEXT = (
    "The story so far and each seat's own watch so far come with the frame when the room has seen enough: read the frame "
    "against them. A payoff lands against the setup the seat saw and its thought can say so; a seat stays consistent with what "
    "it felt earlier unless the screen gives it a reason to change. When the notes ask you to update the story so far, also "
    "return \"story_so_far\": {\"who\": [string], \"set_up\": [string], \"resolved\": [string], \"where_it_stands\": string, "
    "\"tone\": string} (at most 10 people, 8 open threads, 6 resolved, one short sentence each, only what the screen has shown, "
    "the cast by the names the notes give) and \"waiting\": {\"<seat>\": what that person is waiting to see, under 12 words}; "
    "otherwise leave both out."
)


def story_enabled() -> bool:
    """DTIQ_PILOT_STORY=0 restores the earlier read (every part on its own, in parallel)."""
    return (os.environ.get('DTIQ_PILOT_STORY') or '1').strip().lower() not in ('0', 'false', 'no', 'off')


def rows_per_call() -> int:
    try:
        return max(12, int(os.environ.get('DTIQ_PILOT_ROWS_PER_CALL') or ROWS_PER_CALL))
    except ValueError:
        return ROWS_PER_CALL


def _clock(t) -> str:
    try:
        s = max(0, int(round(float(t))))
    except (TypeError, ValueError):
        s = 0
    return f"{s // 60}:{s % 60:02d}"


def _clean(text, max_words: int) -> str:
    t = ' '.join(str(text or '').replace('\u2014', '-').replace('\u2013', '-').split())
    words = t.split(' ')
    if len(words) > max_words:
        t = ' '.join(words[:max_words]).rstrip(',;:') + '...'
    return t


def _words(text: str) -> int:
    return len(str(text or '').split())


# --------------------------------------------------------- the story so far
def empty_story() -> dict:
    return {'who': [], 'set_up': [], 'resolved': [], 'where_it_stands': '', 'tone': ''}


def is_empty(story: Optional[dict]) -> bool:
    s = story or {}
    return not (s.get('who') or s.get('set_up') or s.get('resolved') or s.get('where_it_stands'))


def story_words(story: Optional[dict]) -> int:
    s = story or {}
    return sum(_words(x) for k in STORY_LIMITS for x in (s.get(k) or [])) + _words(s.get('where_it_stands')) + _words(s.get('tone'))


def clip_story(story) -> dict:
    """The story inside its caps: each list short, each line short, the
    whole under ``STORY_MAX_WORDS`` (older resolved threads go first, then
    the longest lists)."""
    src = story if isinstance(story, dict) else {}
    out = empty_story()
    for k, n in STORY_LIMITS.items():
        rows = src.get(k) if isinstance(src.get(k), list) else ([src.get(k)] if isinstance(src.get(k), str) else [])
        seen = set()
        for r in rows:
            line = _clean(r, STORY_ITEM_WORDS)
            key = line.lower()
            if line and key not in seen:
                seen.add(key)
                out[k].append(line)
        out[k] = out[k][-n:] if k == 'resolved' else out[k][:n]
    out['where_it_stands'] = _clean(src.get('where_it_stands'), STORY_LINE_WORDS)
    out['tone'] = _clean(src.get('tone'), 12)
    while story_words(out) > STORY_MAX_WORDS:
        if out['resolved']:
            out['resolved'].pop(0)
        elif len(out['who']) > 6:
            out['who'].pop()
        elif len(out['set_up']) > 4:
            out['set_up'].pop()
        elif out['who']:
            out['who'].pop()
        elif out['set_up']:
            out['set_up'].pop()
        else:
            out['where_it_stands'] = _clean(out['where_it_stands'], 12)
            break
    return out


def story_for_prompt(story: Optional[dict]) -> Optional[dict]:
    """What a part's call is handed: the clipped story, or None before anything has played."""
    s = clip_story(story or {})
    return None if is_empty(s) else s


def story_text(story: Optional[dict]) -> str:
    """The story as one short block of text (the live notes)."""
    s = clip_story(story or {})
    if is_empty(s):
        return ''
    parts = []
    if s['who']:
        parts.append('Who is who: ' + '; '.join(s['who']) + '.')
    if s['set_up']:
        parts.append('Set up and not yet paid off: ' + '; '.join(s['set_up']) + '.')
    if s['resolved']:
        parts.append('Resolved: ' + '; '.join(s['resolved']) + '.')
    if s['where_it_stands']:
        parts.append('Where it stands: ' + s['where_it_stands'].rstrip('.') + '.')
    if s['tone']:
        parts.append('Tone: ' + s['tone'].rstrip('.') + '.')
    return ' '.join(parts)


def _fallback_story(story: dict, rows: list, cast_seen: Optional[list] = None) -> dict:
    """When no recap answers: the cast seen by face joins who is who, and
    where the story stands is the part's last scene. The threads carry over."""
    s = clip_story(story or {})
    have = {w.split(':')[0].strip().lower() for w in s['who']}
    for name in cast_seen or []:
        n = str(name or '').strip()
        if n and n.lower() not in have:
            s['who'].append(f"{n}: on screen")
            have.add(n.lower())
    last = next((r for r in reversed(rows or []) if str(r.get('on_screen') or '').strip()), None)
    if last:
        s['where_it_stands'] = f"At {last.get('clock') or ''}: {last.get('on_screen')}".strip()
    tones = [str(r.get('tone') or '') for r in rows or [] if r.get('tone')]
    if tones:
        s['tone'] = max(sorted(set(tones)), key=tones.count)
    return clip_story(s)


def cast_in(rows: list) -> list:
    out = []
    for r in rows or []:
        for n in r.get('cast_on_screen') or []:
            if n and n not in out:
                out.append(n)
    return out


def update_story(story: Optional[dict], part: str, rows: list, *, models=None, meta: Optional[dict] = None,
                 log: Optional[Callable] = None) -> dict:
    """The story after ``part``: one short call on the story so far and the
    part's scene rows (``digital_twin_watch._scene_rows``). Never raises; a
    call that does not answer keeps the threads and moves where it stands."""
    before = clip_story(story or {})
    if models is None or not rows:
        return _fallback_story(before, rows, cast_in(rows))
    meta = meta or {}
    user = json.dumps({'cut': {'title': meta.get('title') or 'the cut', 'genre': meta.get('genre') or '', 'notes': str(meta.get('notes') or '')[:300]},
                       'story_so_far': before, 'part': part,
                       'scenes': [{k: r.get(k) for k in ('clock', 'on_screen', 'dialogue', 'cast_on_screen') if r.get(k)} for r in rows]}, default=str)
    try:
        data, call = models.call('watch_story', STORY_SYSTEM, user, max_tokens=900, temperature=0.3, tools=None)
    except Exception as e:
        print(f"[twin-story] recap after {part} failed: {e}")
        data, call = None, None
    if log and call is not None:
        log(f"story:{part}", call)
    if not isinstance(data, dict) or not any(data.get(k) for k in ('who', 'set_up', 'resolved', 'where_it_stands')):
        return _fallback_story(before, rows, cast_in(rows))
    got = clip_story(data)
    # a name the face match gave stays in who is who even when the recap left it out
    have = ' '.join(got['who']).lower()
    for n in cast_in(rows):
        if n.lower() not in have and len(got['who']) < STORY_LIMITS['who']:
            got['who'].append(f"{n}: on screen")
    return clip_story(got)


# ------------------------------------------------------- the seat's memory
def new_memory(start: float = 50.0) -> dict:
    try:
        start = round(float(start), 1)
    except (TypeError, ValueError):
        start = 50.0
    return {'start': start, 'path': [], 'reactions': [], 'said': [], 'notes': [], 'waiting_for': ''}


def add_part(memory: Optional[dict], part: str, seat_rows: dict, scene_rows: dict, *, note: str = '', waiting_for: str = '') -> dict:
    """The seat's memory after ``part``. ``seat_rows`` = {scene id: {dial,
    event, say}} the read gave this seat for the part; ``scene_rows`` =
    {scene id: scene row} (clock, on_screen)."""
    m = dict(memory or new_memory())
    for k in ('path', 'reactions', 'said', 'notes'):
        m[k] = list(m.get(k) or [])
    got = [(sid, r) for sid, r in sorted((seat_rows or {}).items()) if isinstance(r, dict) and r.get('dial') is not None]
    if got:
        def at(sid):
            sc = scene_rows.get(sid) or {}
            return {'clock': sc.get('clock') or '', 'on': _clean(sc.get('on_screen'), 9)}
        hi = max(got, key=lambda x: float(x[1]['dial']))
        lo = min(got, key=lambda x: float(x[1]['dial']))
        m['path'].append({'part': part, 'end': round(float(got[-1][1]['dial']), 1),
                          'high': dict(at(hi[0]), dial=round(float(hi[1]['dial']), 1)), 'low': dict(at(lo[0]), dial=round(float(lo[1]['dial']), 1))})
        for sid, r in got:
            if r.get('event'):
                m['reactions'].append({'clock': (scene_rows.get(sid) or {}).get('clock') or '', 'type': r['event'],
                                       'on': _clean((scene_rows.get(sid) or {}).get('on_screen'), 8)})
            if str(r.get('say') or '').strip():
                m['said'].append(_clean(r['say'], 20))
    if str(note or '').strip():
        m['notes'].append({'part': part, 'note': _clean(note, 24)})
    if str(waiting_for or '').strip():
        m['waiting_for'] = _clean(waiting_for, 15)
    m['reactions'] = m['reactions'][-MEMORY_REACTIONS:]
    m['said'] = m['said'][-MEMORY_SAID:]
    m['notes'] = m['notes'][-MEMORY_NOTES:]
    return m


def _cap_words(text: str, n: int) -> str:
    words = str(text or '').split()
    if len(words) <= n:
        return ' '.join(words)
    return ' '.join(words[:n]).rstrip(',;:') + '...'


def memory_text(memory: Optional[dict], max_words: int = MEMORY_MAX_WORDS) -> str:
    """The seat's path so far in a few plain sentences (newest kept when
    the cap bites)."""
    m = memory or {}
    if not (m.get('path') or m.get('reactions') or m.get('said')):
        return ''
    path = list(m.get('path') or [])

    def build(path_rows, reactions, said, notes):
        bits = [f"Your dial started at {m.get('start', 50.0):.0f}."]
        for p in path_rows:
            bits.append(f"{str(p.get('part') or '').capitalize()}: ended at {p.get('end'):.0f}; high {p['high']['dial']:.0f} at {p['high']['clock']} ({p['high']['on']}); "
                        f"low {p['low']['dial']:.0f} at {p['low']['clock']} ({p['low']['on']}).")
        if reactions:
            bits.append('You ' + '; '.join(f"{EVENT_WORDS.get(r['type'], r['type'])} at {r['clock']} ({r['on']})" for r in reactions) + '.')
        if said:
            bits.append('You said: ' + ' / '.join(f'"{s}"' for s in said) + '.')
        if notes:
            bits.append('How it sat: ' + ' '.join(f"{n['part']}: {n['note'].rstrip('.')}." for n in notes))
        if m.get('waiting_for'):
            bits.append(f"Waiting to see: {m['waiting_for'].rstrip('.')}.")
        return ' '.join(bits)

    reactions, said, notes = list(m.get('reactions') or []), list(m.get('said') or []), list(m.get('notes') or [])
    text = build(path, reactions, said, notes)
    while _words(text) > max_words and (len(path) > 1 or len(reactions) > 2 or len(said) > 1 or len(notes) > 1):
        if len(notes) > 1:
            notes.pop(0)
        elif len(path) > 1:
            path.pop(0)
        elif len(reactions) > 2:
            reactions.pop(0)
        else:
            said.pop(0)
        text = build(path, reactions, said, notes)
    return _cap_words(text, max_words)


def seat_groups(seat_ids: list, max_scenes: int, rows_cap: Optional[int] = None) -> list:
    """The seats in fixed groups, each small enough that its biggest part
    stays under ``rows_cap`` rows (seats x scenes). One group when the whole
    room fits, which is the read the room always had on a short cut."""
    ids = list(seat_ids or [])
    if not ids:
        return []
    cap = rows_cap or rows_per_call()
    per = max(1, min(len(ids), cap // max(1, int(max_scenes or 1))))
    n_groups = -(-len(ids) // per)
    base, extra = divmod(len(ids), n_groups)   # even groups: 16 seats at a cap of 6 read as 6, 5, 5, never 6, 6, 4
    out, i = [], 0
    for g in range(n_groups):
        n = base + (1 if g < extra else 0)
        out.append(ids[i:i + n])
        i += n
    return out


# ------------------------------------------------------------ live watch
def live_story_due(live: dict) -> bool:
    frames = (live or {}).get('frames') or []
    n = len(frames) + 1                      # the frame being read now
    done = int(((live or {}).get('story') or {}).get('frames') or 0) if isinstance((live or {}).get('story'), dict) else 0
    if not done:
        return n >= LIVE_STORY_FIRST
    return n - done >= LIVE_STORY_EVERY


def live_story_block(live: dict) -> str:
    """The notes' view of what the room has seen: the story so far through
    its last frame and every frame since, or every frame so far before the
    first recap."""
    frames = [f for f in (live or {}).get('frames') or [] if isinstance(f, dict)]
    st = (live or {}).get('story') if isinstance((live or {}).get('story'), dict) else {}
    text = story_text(st.get('story')) if st else ''
    if text:
        through = float(st.get('t') or 0)
        since = [f for f in frames if float(f.get('t') or 0) > through][-LIVE_SINCE_MAX:]
        block = f" The story so far (through {_clock(through)}): {text}"
        if since:
            block += "\nSince then:\n" + '\n'.join(f"{_clock(f.get('t'))}: {f.get('caption')}" for f in since)
        return block
    if not frames:
        return ' This is the first frame.'
    return " What the room has seen so far:\n" + '\n'.join(f"{_clock(f.get('t'))}: {f.get('caption')}" for f in frames[-LIVE_SINCE_MAX:])


def live_memory_text(live: dict, seat: str) -> str:
    """One seat's own watch so far on a live test, in a line."""
    live = live or {}
    pts = [p for p in ((live.get('seat_dials') or {}).get(str(seat)) or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
    if not pts:
        return ''
    frames = [f for f in live.get('frames') or [] if isinstance(f, dict)]

    def cap_at(t):
        f = min(frames, key=lambda x: abs(float(x.get('t') or 0) - float(t)), default=None) if frames else None
        return _clean((f or {}).get('caption'), 7)
    hi = max(pts, key=lambda p: float(p[1]))
    lo = min(pts, key=lambda p: float(p[1]))
    mine = [e for e in live.get('events') or [] if isinstance(e, dict) and str(e.get('seat')) == str(seat)][-3:]
    said = [x for x in ((live.get('thoughts') or {}).get(str(seat)) or []) if isinstance(x, (list, tuple)) and len(x) >= 2][-2:]
    wait = str((live.get('waiting') or {}).get(str(seat)) or '').strip()

    def build(cap_words, reactions, thoughts):
        bits = [f"started {float(pts[0][1]):.0f}, high {float(hi[1]):.0f} at {_clock(hi[0])}" + (f" ({_clean(cap_at(hi[0]), cap_words)})" if cap_words else '')
                + f", low {float(lo[1]):.0f} at {_clock(lo[0])}" + (f" ({_clean(cap_at(lo[0]), cap_words)})" if cap_words else '')]
        if reactions:
            bits.append('reacted: ' + ', '.join(f"{EVENT_WORDS.get(e.get('type'), e.get('type'))} at {_clock(e.get('t'))}" for e in reactions))
        if thoughts:
            bits.append('thought: ' + ' / '.join(f'"{_clean(x[1], 12)}"' for x in thoughts))
        if wait:
            bits.append(f"waiting to see: {wait}")
        return '; '.join(bits)
    # what the seat is waiting for and its newest thought matter most; the captions at its high and low give way first
    for cap_words, reactions, thoughts in ((7, mine, said), (4, mine, said), (0, mine, said), (0, mine[-1:], said), (0, mine[-1:], said[-1:])):
        text = build(cap_words, reactions, thoughts)
        if _words(text) <= LIVE_MEMORY_MAX_WORDS:
            return text
    return _cap_words(text, LIVE_MEMORY_MAX_WORDS)


def live_fallback_story(live: dict, caption: str, t: float, cast: Optional[list] = None) -> dict:
    """The live recap when no model answered: the threads carry, the cast
    seen joins who is who, where it stands is this frame."""
    st = (live or {}).get('story') if isinstance((live or {}).get('story'), dict) else {}
    rows = [{'clock': _clock(t), 'on_screen': caption, 'tone': ''}]
    return _fallback_story(st.get('story') or {}, rows, cast)


__all__ = ['STORY_MAX_WORDS', 'STORY_LIMITS', 'MEMORY_MAX_WORDS', 'LIVE_MEMORY_MAX_WORDS', 'ROWS_PER_CALL', 'LIVE_STORY_FIRST', 'LIVE_STORY_EVERY',
           'STORY_SYSTEM', 'WATCH_CONTEXT', 'LIVE_CONTEXT', 'story_enabled', 'rows_per_call', 'empty_story', 'is_empty', 'story_words', 'clip_story',
           'story_for_prompt', 'story_text', 'update_story', 'cast_in', 'new_memory', 'add_part', 'memory_text', 'seat_groups', 'live_story_due',
           'live_story_block', 'live_memory_text', 'live_fallback_story']
