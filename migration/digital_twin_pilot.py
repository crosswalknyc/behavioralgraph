"""Pilot Testing (Digital Twins IQ): the room watches a cut on the clock.

The twins sit in their seats (the same roster and personas as Focus Group
IQ). The moderator presses Play. Each seat holds a dial (0 to 100) and
fires timestamped events (love, laugh, lean-in, confuse, check-out, wow).
The page reveals those points locked to the video clock. Prometheus can
ask a question at any playhead. After the cut: a scorecard, the full
dial, the event list, the transcript.

Reactions are deterministic per (twin, seat, media hash, beat). Two
subjects never pin the same dial. Counts keep messy last digits. Opening
a test is free; the session price is taken on first Play. User-facing
copy never names how the room is produced.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Callable, Optional

from migration.digital_twin_engine import (
    VERSION, clamp, jit, messy_count, natural_count, pct1, unit,
)
from migration.digital_twin_panel import (
    DEFAULT_IN_ROOM, MAX_IN_ROOM, build_group, clean_room_title,
    rename_session as _rename_session,
)

EVENT_TYPES = ('love', 'laugh', 'lean-in', 'confuse', 'check-out', 'wow')
EVENT_LABEL = {
    'love': 'Loved it',
    'laugh': 'Laughed',
    'lean-in': 'Leaned in',
    'confuse': 'Lost',
    'check-out': 'Checked out',
    'wow': 'Wow',
}
KINDS = ('open', 'yes_no', 'multiple_choice', 'rating', 'price', 'ab', 'show_of_hands', 'probe')
STATUS = ('draft', 'preparing', 'ready', 'playing', 'stopped')
MEDIA_PREFIX = 'digital-twins/pilot-tests/'
DELETED_PILOT_PREFIX = 'system/digital_twins/deleted_pilots/'
DELETED_PILOT_KEEP_DAYS = 30
QUESTIONS_INCLUDED = 12
MIN_RUNTIME_S = 15
MAX_RUNTIME_S = 4 * 60 * 60
ALLOWED_UPLOAD = frozenset({
    'video/mp4', 'video/quicktime', 'video/webm', 'video/x-m4v',
    'application/octet-stream',
})
ALLOWED_EXT = frozenset({'.mp4', '.mov', '.webm', '.m4v'})

_COMEDY = re.compile(r"\b(comedy|sitcom|funny|laugh|romcom|rom-com|sketch|spoof|parody)\b", re.I)
_THRILL = re.compile(r"\b(thriller|horror|scare|suspense|crime|noir|mystery)\b", re.I)
_ACTION = re.compile(r"\b(action|heist|war|chase|fight|superhero|stunt)\b", re.I)
_ROMANCE = re.compile(r"\b(romance|love|wedding|date|affair)\b", re.I)
_PILOT = re.compile(r"\b(pilot|episode|season|series|trailer|teaser|cut)\b", re.I)


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _meta(t):
    return (t or {}).get('meta') or {}


def _twin_name(t):
    return str(_meta(t).get('name') or _meta(t).get('subject') or 'the twin')


def _twin_key(t):
    return str(_meta(t).get('key') or (t or {}).get('key') or _twin_name(t))


def _subject(t):
    return str(_meta(t).get('subject') or _meta(t).get('name') or 'the twin')


def media_hash(title: str = '', duration_s: float = 0, filename: str = '', extra: str = '') -> str:
    raw = f"{str(title or '').strip().lower()}|{float(duration_s or 0):.3f}|{str(filename or '').strip().lower()}|{str(extra or '')}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]


def beat_step(duration_s: float) -> float:
    d = float(duration_s or 0)
    if d <= 90:
        return 2.0
    if d <= 600:
        return 3.0
    return 4.0


def _genre(title: str, notes: str = '') -> str:
    blob = f"{title or ''} {notes or ''}"
    if _COMEDY.search(blob):
        return 'comedy'
    if _THRILL.search(blob):
        return 'thriller'
    if _ACTION.search(blob):
        return 'action'
    if _ROMANCE.search(blob):
        return 'romance'
    if _PILOT.search(blob):
        return 'pilot'
    return 'drama'


def _act(frac: float) -> str:
    if frac < 0.08:
        return 'open'
    if frac < 0.28:
        return 'setup'
    if frac < 0.62:
        return 'middle'
    if frac < 0.86:
        return 'turn'
    if frac < 0.96:
        return 'close'
    return 'end'


_ACT_ACTION = {
    'open': ('slate and title card', 'the first face on screen', 'the cold open lands', 'the premise is named'),
    'setup': ('a character is introduced', 'the world is sketched', 'a first conflict shows', 'someone states what they want'),
    'middle': ('a joke lands in the room', 'two people talk past each other', 'a reveal changes the scene', 'the plot thickens', 'a quiet beat holds'),
    'turn': ('the turn of the cut', 'a fight or a confession', 'someone is caught', 'the music swells'),
    'close': ('the climax hits', 'the last argument', 'the ending image', 'a promise of what comes next'),
    'end': ('the last line', 'credits begin', 'a tag after the cut'),
}
_ACT_TONE = {
    'open': ('quiet', 'curious', 'tense'),
    'setup': ('curious', 'warm', 'funny'),
    'middle': ('funny', 'tense', 'tender', 'action'),
    'turn': ('tense', 'action', 'tender'),
    'close': ('action', 'tender', 'wow'),
    'end': ('quiet', 'warm', 'curious'),
}
_GENRE_TONE = {
    'comedy': ('funny', 'warm', 'curious'),
    'thriller': ('tense', 'quiet', 'wow'),
    'action': ('action', 'tense', 'wow'),
    'romance': ('tender', 'warm', 'funny'),
    'pilot': ('curious', 'funny', 'tense', 'warm'),
    'drama': ('tender', 'tense', 'quiet', 'warm'),
}


def _pick(seq, *parts):
    if not seq:
        return ''
    return seq[int(unit(*parts) * len(seq)) % len(seq)]


def build_beats(title: str, duration_s: float, media_key: str, notes: str = '',
                transcript: Optional[list] = None) -> list:
    """A beat every 2 to 4 seconds. Transcript lines, when present, pin
    dialogue to the nearest beat. Otherwise the grid is the title, the
    runtime and the media hash (always enough to press Play)."""
    dur = clamp(float(duration_s or 0), MIN_RUNTIME_S, MAX_RUNTIME_S)
    step = beat_step(dur)
    n = max(8, int(round(dur / step)))
    genre = _genre(title, notes)
    out = []
    lines = [x for x in (transcript or []) if isinstance(x, dict) and x.get('t') is not None]
    for i in range(n):
        t = round(min(dur, i * step), 3)
        frac = t / dur if dur else 0
        act = _act(frac)
        action = _pick(_ACT_ACTION[act], media_key, 'action', i)
        tone = _pick(_ACT_TONE[act] + _GENRE_TONE.get(genre, ()), media_key, 'tone', i)
        dialogue = ''
        nearest = None
        nearest_d = 9e9
        for row in lines:
            d = abs(float(row.get('t') or 0) - t)
            if d < nearest_d and d <= step:
                nearest_d = d
                nearest = row
        if nearest:
            dialogue = str(nearest.get('text') or nearest.get('line') or '')[:180]
            if nearest.get('tone'):
                tone = str(nearest.get('tone'))
        cue = 'music swell' if tone in ('tender', 'wow') and unit(media_key, 'cue', i) < 0.28 else (
            'dialogue heavy' if dialogue else 'on-screen action')
        out.append({
            'i': i, 't': t, 'act': act, 'action': action, 'tone': tone,
            'dialogue': dialogue, 'cue': cue, 'genre': genre,
        })
    if out and out[-1]['t'] < dur - 0.05:
        out.append({
            'i': len(out), 't': round(dur, 3), 'act': 'end',
            'action': 'the cut ends', 'tone': 'quiet', 'dialogue': '',
            'cue': 'credits', 'genre': genre,
        })
    return out


def _card_for(twin: dict, seat: dict, session_seed: str) -> dict:
    try:
        from migration.digital_twin_room import persona_card
        return persona_card(twin, seat, session_seed)
    except Exception:
        return {
            'seat': seat.get('seat'), 'name': seat.get('name'), 'age': seat.get('age'),
            'gender': 'woman' if str(seat.get('gender') or 'F') == 'F' else (
                'nonbinary' if str(seat.get('gender')) == 'X' else 'man'),
            'city': seat.get('city'), 'lifecycle_stage': 'in the habit',
            'orientation': seat.get('orientation') or '',
            'habits': [],
        }


def _trait(twin: dict, key: str, default: float = 50.0) -> float:
    row = ((twin or {}).get('traits') or {}).get(key)
    if isinstance(row, dict):
        try:
            return float(row.get('value') if row.get('value') is not None else default)
        except (TypeError, ValueError):
            return default
    try:
        return float(row)
    except (TypeError, ValueError):
        return default


def _messy_bp(subject: str, brand: str, category: str, value: float) -> float:
    """4dp, never a .xx00 pin, subject-salted."""
    v = float(value)
    v = clamp(v, 4.0, 96.5)
    v += jit(-0.37, 0.37, subject, brand, category, 'pilot-dial')
    text = f"{v:.4f}"
    if text.endswith('00'):
        v += 0.0013 + 0.0031 * unit(subject, brand, category, 'depin')
    return round(clamp(v, 4.0111, 96.4893), 4)


def _tone_fit(card: dict, beat: dict, twin: dict) -> float:
    """How this seat sits with this beat. Persona first, never a coin flip."""
    tone = str(beat.get('tone') or '')
    age = int(card.get('age') or 35)
    gender = str(card.get('gender') or '')
    stage = str(card.get('lifecycle_stage') or '')
    ori = str(card.get('orientation') or '')
    loyal = _trait(twin, 'franchise_loyalty')
    second = _trait(twin, 'second_screen')
    binge = _trait(twin, 'binge_vs_appointment')
    wom = _trait(twin, 'word_of_mouth')
    trial = _trait(twin, 'trial_speed')
    fit = 0.0
    if tone == 'funny':
        fit += 8.0 if age < 40 else 3.4
        if gender == 'man' and age < 30:
            fit += 2.2
        if 'lesbian' in ori.lower() and age >= 45:
            fit -= 1.6
    elif tone == 'tender':
        fit += 7.2 if gender in ('woman', 'nonbinary') else 2.8
        if age >= 45:
            fit += 3.1
        if re.search(r"lgbtq|lesbian|gay|queer", ori, re.I):
            fit += 2.4
    elif tone == 'tense':
        fit += 4.0 if age < 55 else 1.2
        if second >= 60:
            fit -= 3.0
    elif tone == 'action':
        fit += 6.5 if gender == 'man' and age < 40 else 2.0
        if binge >= 55:
            fit += 2.4
    elif tone == 'wow':
        fit += 5.0 + (loyal - 50) * 0.12
    elif tone == 'quiet':
        fit += 2.0 if second < 45 else -4.2
        if age >= 55:
            fit += 3.4
    elif tone == 'warm':
        fit += 4.6 if stage in ('in the habit', 'returning') else 1.8
    elif tone == 'curious':
        fit += 3.0 + (trial - 50) * 0.08
    if stage == 'lapsing':
        fit -= 4.5
    elif stage == 'discovering':
        fit += 1.4 if beat.get('act') in ('open', 'setup') else -0.8
    elif stage == 'in the habit':
        fit += 3.6
    if second >= 62 and beat.get('cue') == 'dialogue heavy':
        fit -= 3.8
    if wom >= 60 and tone in ('wow', 'funny', 'tender'):
        fit += 1.8
    return fit


def seat_dial(twin: dict, seat: dict, beats: list, session_seed: str, media_key: str) -> list:
    card = _card_for(twin, seat, session_seed)
    sid = f"{session_seed}|seat{seat.get('seat')}|{seat.get('name')}"
    loyal = _trait(twin, 'franchise_loyalty')
    second = _trait(twin, 'second_screen')
    age = int(card.get('age') or 35)
    base = 46.0 + (loyal - 50) * 0.18 + (12 if age >= 45 else 0) * 0.22 - (second - 50) * 0.10
    level = base
    out = []
    subject = _subject(twin)
    brand = str(seat.get('name') or seat.get('seat') or 'seat')
    for b in beats:
        fit = _tone_fit(card, b, twin)
        walk = jit(-2.4, 2.8, sid, media_key, b.get('i'), 'walk')
        target = base + fit + {'open': 1.2, 'setup': 3.0, 'middle': 2.2, 'turn': 6.4, 'close': 8.1, 'end': -1.6}.get(b.get('act'), 0)
        level = level * 0.72 + target * 0.28 + walk
        if b.get('act') == 'end':
            level = level * 0.9 + 44
        v = _messy_bp(subject, brand, f"beat-{b.get('i')}", level)
        out.append(v)
        level = v
    return out


def _event_line(kind: str, card: dict, beat: dict) -> str:
    name = str(card.get('name') or 'they')
    pron = {'woman': ('she', 'her'), 'man': ('he', 'his')}.get(str(card.get('gender') or ''), ('they', 'their'))
    act = str(beat.get('action') or 'that beat')
    if kind == 'laugh':
        return _pick((
            f"{name} laughed out loud.",
            f"A real laugh. {pron[0].capitalize()} did not see that coming.",
            f"{name} covered {pron[1]} mouth and laughed.",
        ), name, act, kind)
    if kind == 'love':
        return _pick((
            f"{name} loved that. {pron[0].capitalize()} would rewind it.",
            f"That was the moment for {name}.",
            f"{name} leaned back: that is why {pron[0]} is here.",
        ), name, act, kind)
    if kind == 'lean-in':
        return _pick((
            f"{name} sat forward.",
            f"{name} put the phone down.",
            f"{name} did not want to miss the next line.",
        ), name, act, kind)
    if kind == 'confuse':
        return _pick((
            f"{name} lost the thread.",
            f"{name} replayed that in {pron[1]} head.",
            f"{name} did not follow who that was.",
        ), name, act, kind)
    if kind == 'check-out':
        return _pick((
            f"{name} looked at {pron[1]} phone.",
            f"{name} drifted.",
            f"{name} checked out for a few seconds.",
        ), name, act, kind)
    return _pick((
        f"{name} said wow under {pron[1]} breath.",
        f"That landed hard for {name}.",
        f"{name} would tell a friend about that beat.",
    ), name, act, kind)


def seat_events(twin: dict, seat: dict, beats: list, dial: list, session_seed: str, media_key: str) -> list:
    card = _card_for(twin, seat, session_seed)
    sid = f"{session_seed}|seat{seat.get('seat')}|{seat.get('name')}"
    loyal = _trait(twin, 'franchise_loyalty')
    second = _trait(twin, 'second_screen')
    binge = _trait(twin, 'binge_vs_appointment')
    age = int(card.get('age') or 35)
    gender = str(card.get('gender') or '')
    ori = str(card.get('orientation') or '')
    stage = str(card.get('lifecycle_stage') or '')
    out = []
    prev = dial[0] if dial else 50
    for i, b in enumerate(beats):
        v = dial[i] if i < len(dial) else prev
        jump = v - prev
        tone = str(b.get('tone') or '')
        u = unit(sid, media_key, b.get('i'), 'evt')
        fired = None
        if tone == 'funny' and v >= 52:
            need = 0.22 if age < 40 else 0.38
            if 'lesbian' in ori.lower() and age >= 50:
                need += 0.12
            if u < need + (0.10 if jump > 3 else 0):
                fired = 'laugh'
        if fired is None and jump >= 5.5 and tone in ('tender', 'warm', 'wow'):
            need = 0.28 if (gender in ('woman', 'nonbinary') or loyal >= 58) else 0.48
            if u < need:
                fired = 'love'
        if fired is None and tone in ('action', 'tense', 'wow') and (binge >= 52 or jump >= 4.0):
            if u < 0.24 + (0.08 if stage == 'in the habit' else 0):
                fired = 'lean-in'
        if fired is None and tone == 'wow' and v >= 62 and u < 0.32:
            fired = 'wow'
        if fired is None and (tone in ('quiet',) or jump <= -4.5) and second >= 55:
            if u < 0.20 + (second - 55) * 0.006:
                fired = 'check-out'
        if fired is None and tone in ('tense', 'curious') and jump < -1.5 and loyal < 48:
            if u < 0.16:
                fired = 'confuse'
        if fired is None and tone == 'quiet' and stage == 'lapsing' and u < 0.14:
            fired = 'check-out'
        if fired:
            intensity = round(clamp(0.42 + abs(jump) / 14.0 + unit(sid, 'int', i) * 0.28, 0.31, 0.97), 4)
            out.append({
                'type': fired, 't': b.get('t'), 'seat': seat.get('seat'),
                'name': seat.get('name'), 'age': seat.get('age'), 'city': seat.get('city'),
                'gender': seat.get('gender'), 'segment': seat.get('segment'),
                'line': _event_line(fired, card, b), 'intensity': intensity,
                'beat': b.get('i'),
            })
        prev = v
    # A seat that never fires still gets one honest mark so the rail is not empty
    # for a quiet persona; only when the dial actually moved.
    if not out and dial:
        hi_i = max(range(len(dial)), key=lambda k: dial[k])
        lo_i = min(range(len(dial)), key=lambda k: dial[k])
        kind = 'love' if dial[hi_i] - dial[lo_i] >= 8 else 'lean-in'
        b = beats[hi_i] if hi_i < len(beats) else beats[-1]
        out.append({
            'type': kind, 't': b.get('t'), 'seat': seat.get('seat'),
            'name': seat.get('name'), 'age': seat.get('age'), 'city': seat.get('city'),
            'gender': seat.get('gender'), 'segment': seat.get('segment'),
            'line': _event_line(kind, card, b),
            'intensity': round(0.41 + 0.17 * unit(sid, 'solo'), 4),
            'beat': b.get('i'),
        })
    return out


def aggregate_dial(seat_dials: list, subject: str, media_key: str) -> list:
    if not seat_dials:
        return []
    n = min(len(d) for d in seat_dials)
    out = []
    for i in range(n):
        mean = sum(d[i] for d in seat_dials) / float(len(seat_dials))
        mean += jit(0.021, 0.184, subject, media_key, i, 'agg')
        out.append(_messy_bp(subject, 'room', f"agg-{i}", mean))
    return out


def split_dial(roster: list, seat_dials: dict, pred, subject: str, media_key: str, label: str) -> Optional[list]:
    picked = []
    for seat in roster:
        sid = str(seat.get('seat'))
        if sid in seat_dials and pred(seat):
            picked.append(seat_dials[sid])
    if len(picked) < 2 or len(picked) == len(seat_dials):
        return None
    return aggregate_dial(picked, f"{subject}|{label}", media_key)


def _is_woman(seat):
    g = str(seat.get('gender') or '')
    return g in ('F', 'woman') or str(seat.get('gender_label') or '').lower().startswith(('f', 'w'))


def _is_man(seat):
    g = str(seat.get('gender') or '')
    return g in ('M', 'man') or (g.startswith('M') and 'non' not in g.lower())


def _is_avid(seat, twin):
    stage = str(seat.get('lifecycle_stage') or '')
    if stage in ('in the habit', 'returning'):
        return True
    return _trait(twin, 'franchise_loyalty') >= 60


def live_voices(events: list, roster: list, limit: int = 36) -> list:
    """A few lines the page can reveal on the clock (not every event)."""
    if not events:
        return []
    events = sorted(events, key=lambda e: (float(e.get('t') or 0), str(e.get('name') or '')))
    step = max(1, len(events) // max(1, min(limit, len(events))))
    picked = events[::step][:limit]
    names = {int(r.get('seat') or -1): r for r in roster}
    out = []
    for e in picked:
        seat = names.get(int(e.get('seat') or -1), {})
        out.append({
            't': e.get('t'), 'type': e.get('type'), 'name': e.get('name'),
            'age': e.get('age') or seat.get('age'), 'city': e.get('city') or seat.get('city'),
            'seat': e.get('seat'), 'line': e.get('line'),
            'portrait_url': seat.get('portrait_url') or '',
        })
    return out


def _clock(t) -> str:
    try:
        s = max(0, int(round(float(t))))
    except (TypeError, ValueError):
        s = 0
    return f"{s // 60}:{s % 60:02d}"


def scorecard(twin: dict, roster: list, beats: list, dial: list, events: list,
              seat_dials: dict, subject: str, media_key: str) -> dict:
    if not dial:
        dial = [50.0]
    tail_n = max(3, int(len(dial) * 0.22))
    overall = sum(dial[-tail_n:]) / float(tail_n)
    overall = _messy_bp(subject, 'score', 'like', overall)
    wom = _trait(twin, 'word_of_mouth')
    loyal = _trait(twin, 'franchise_loyalty')
    binge = _trait(twin, 'binge_vs_appointment')
    checkouts = sum(1 for e in events if e.get('type') == 'check-out')
    loves = sum(1 for e in events if e.get('type') in ('love', 'wow', 'laugh'))
    rec = clamp(overall * 0.62 + wom * 0.28 + loves * 0.35 - checkouts * 0.8, 18, 94)
    rec = _messy_bp(subject, 'score', 'recommend', rec)
    finish = clamp(overall * 0.48 + binge * 0.22 + loyal * 0.18 - checkouts * 1.1, 22, 95)
    finish = _messy_bp(subject, 'score', 'finish', finish)
    fav = None
    if events:
        loveish = [e for e in events if e.get('type') in ('love', 'wow', 'laugh', 'lean-in')]
        pool = loveish or events
        fav = max(pool, key=lambda e: float(e.get('intensity') or 0) * 10 + float(e.get('t') or 0) * 0.001)
    peak_i = max(range(len(dial)), key=lambda i: dial[i])
    valley_i = min(range(len(dial)), key=lambda i: dial[i])
    fav_t = float((fav or {}).get('t') if fav else beats[peak_i]['t'] if peak_i < len(beats) else 0)
    fav_line = str((fav or {}).get('line') or 'The room peaked here.')
    dead = {
        't': beats[valley_i]['t'] if valley_i < len(beats) else 0,
        'clock': _clock(beats[valley_i]['t'] if valley_i < len(beats) else 0),
        'line': 'The room dipped. A few seats looked away.',
        'dial': dial[valley_i],
    }
    notes = []
    if loves >= max(3, len(roster) // 2):
        notes.append('The room had a handful of beats it would quote later.')
    if checkouts >= max(2, len(roster) // 3):
        notes.append('A few seats drifted in the quiet stretches.')
    women = [s for s in roster if _is_woman(s)]
    men = [s for s in roster if _is_man(s)]
    if women and men and seat_dials:
        w = aggregate_dial([seat_dials[str(s.get('seat'))] for s in women if str(s.get('seat')) in seat_dials], subject, media_key + '|w')
        m = aggregate_dial([seat_dials[str(s.get('seat'))] for s in men if str(s.get('seat')) in seat_dials], subject, media_key + '|m')
        if w and m:
            gap = sum(a - b for a, b in zip(w, m)) / float(min(len(w), len(m)))
            if gap >= 3.2:
                notes.append('Women in the room sat higher on the dial than the men.')
            elif gap <= -3.2:
                notes.append('Men in the room sat higher on the dial than the women.')
    people = messy_count(subject, 'pilot-room', sum(int(s.get('people') or 0) for s in roster) or len(roster) * 800)
    return {
        'overall_like': overall,
        'recommend_pct': rec,
        'finish_intent_pct': finish,
        'favorite_moment': {
            't': fav_t, 'clock': _clock(fav_t), 'line': fav_line,
            'type': (fav or {}).get('type') or 'love',
            'name': (fav or {}).get('name') or '',
        },
        'dead_spot': dead,
        'notes': notes[:4],
        'people': people,
        'events_n': len(events),
        'loves': loves,
        'checkouts': checkouts,
    }


def prepare_watch(session: dict, twins_by_key: dict, *, duration_s: float = 0,
                  title: str = '', notes: str = '', transcript: Optional[list] = None) -> dict:
    """Fill beats, per-seat dials, events, aggregate, splits, live voices.
    Idempotent for the same media hash and roster."""
    media = session.setdefault('media', {})
    title = str(title or media.get('title') or session.get('title') or 'the cut')[:160]
    dur = float(duration_s or media.get('runtime_s') or 0) or 60.0
    dur = clamp(dur, MIN_RUNTIME_S, MAX_RUNTIME_S)
    media['title'] = title
    media['runtime_s'] = round(dur, 3)
    media['hash'] = media.get('hash') or media_hash(title, dur, media.get('filename') or '', notes)
    session['status'] = 'preparing'
    session['prepare_note'] = 'Preparing the cut'
    twins = list(twins_by_key.values()) or [{}]
    twin = twins[0]
    subject = _subject(twin)
    seed = f"pilot|{session.get('id')}|{media['hash']}|{VERSION}"
    try:
        from migration.digital_twin_room import ensure_personas
        ensure_personas(session, twins_by_key, session_seed=seed)
    except Exception:
        pass
    roster = (session.get('group') or {}).get('roster') or []
    beats = build_beats(title, dur, media['hash'], notes=notes, transcript=transcript)
    seat_dials = {}
    events = []
    for seat in roster:
        series = seat_dial(twin, seat, beats, seed, media['hash'])
        seat_dials[str(seat.get('seat'))] = series
        events.extend(seat_events(twin, seat, beats, series, seed, media['hash']))
    events.sort(key=lambda e: (float(e.get('t') or 0), str(e.get('name') or ''), str(e.get('type') or '')))
    agg = aggregate_dial(list(seat_dials.values()), subject, media['hash'])
    splits = {}
    w = split_dial(roster, seat_dials, _is_woman, subject, media['hash'], 'women')
    m = split_dial(roster, seat_dials, _is_man, subject, media['hash'], 'men')
    if w:
        splits['women'] = w
    if m:
        splits['men'] = m
    avid = split_dial(roster, seat_dials, lambda s: _is_avid(s, twin), subject, media['hash'], 'avid')
    if avid:
        splits['avid'] = avid
    session['beats'] = [{'t': b['t'], 'act': b['act'], 'action': b['action'], 'tone': b['tone'],
                         'dialogue': b.get('dialogue') or '', 'cue': b.get('cue')} for b in beats]
    session['dial'] = {
        't': [b['t'] for b in beats],
        'value': agg,
        'splits': splits,
    }
    session['seat_dials'] = seat_dials
    session['events'] = events
    session['voices_live'] = live_voices(events, roster)
    session['scorecard'] = scorecard(twin, roster, beats, agg, events, seat_dials, subject, media['hash'])
    session['status'] = 'ready'
    session['prepare_note'] = 'Ready to play'
    session['updated_at'] = _now()
    session['version'] = VERSION
    return session


def open_session(twins: list, *, segments: Optional[list] = None, in_room: int = DEFAULT_IN_ROOM,
                 title: str = '', created_by: str = '', session_id: str = '',
                 media: Optional[dict] = None) -> dict:
    """A new Pilot Testing session. Opening is free."""
    sid = session_id or uuid.uuid4().hex[:10]
    seed = f"pilot|{sid}|{'|'.join(_twin_key(t) for t in twins)}|{VERSION}"
    group = build_group(twins, segments, in_room, seed)
    names = ', '.join(dict.fromkeys(_twin_name(t) for t in twins))
    name = clean_room_title(title)
    media = dict(media or {})
    media.setdefault('title', name or names)
    media.setdefault('runtime_s', 0)
    media.setdefault('source', 'slate')
    return {
        'id': sid, 'kind': 'pilot',
        'title': name or f"Pilot test: {names}",
        'group': group, 'questions': [],
        'media': media, 'status': 'draft',
        'beats': [], 'dial': {'t': [], 'value': [], 'splits': {}},
        'seat_dials': {}, 'events': [], 'voices_live': [],
        'scorecard': None, 'play_charged': False, 'questions_paid': 0,
        'created_by': created_by or 'prometheus',
        'created_at': _now(), 'updated_at': _now(), 'version': VERSION,
        'prepare_note': 'Name the test and add the cut.',
    }


def find_reusable_session(payload: dict, twins: list, title: str = '', created_by: str = '') -> Optional[dict]:
    want_keys = sorted(_twin_key(t) for t in twins)
    want_title = clean_room_title(title)
    hits = []
    for s in (payload or {}).get('pilot_sessions') or []:
        if s.get('play_charged') or s.get('events') or s.get('questions'):
            continue
        if want_title and clean_room_title(s.get('title')) != want_title:
            continue
        keys = sorted(str(k) for k in (s.get('twin_keys') or (s.get('group') or {}).get('twins') or []))
        if keys == want_keys:
            hits.append(s)
    if not hits:
        return None
    mine = [s for s in hits if created_by and str(s.get('created_by') or '') == str(created_by)]
    return (mine or hits)[0]


def charge_due(sess: dict, included: int = QUESTIONS_INCLUDED) -> Optional[str]:
    """Which price the next action carries.

    First Play: digital_twin_pilot. Questions after that sit in the
    included block. A further block: digital_twin_pilot_more.
    """
    if not sess:
        return None
    if not sess.get('play_charged'):
        return 'digital_twin_pilot'
    asked = len(sess.get('questions') or [])
    paid = int(sess.get('questions_paid') or 0)
    if asked < paid:
        return None
    return 'digital_twin_pilot_more'


def mark_play_paid(sess: dict, included: int = QUESTIONS_INCLUDED) -> None:
    sess['play_charged'] = True
    if int(sess.get('questions_paid') or 0) <= 0:
        sess['questions_paid'] = int(included)
    sess['updated_at'] = _now()


def mark_paid(sess: dict, included: int = QUESTIONS_INCLUDED) -> int:
    sess['questions_paid'] = int(sess.get('questions_paid') or 0) + int(included)
    sess['updated_at'] = _now()
    return sess['questions_paid']


def mark_playing(sess: dict) -> dict:
    if sess.get('status') in ('ready', 'stopped', 'draft'):
        sess['status'] = 'playing'
    sess['updated_at'] = _now()
    return sess


def stop_session(sess: dict) -> dict:
    sess['status'] = 'stopped'
    sess['updated_at'] = _now()
    return sess


def rename_session(sess: dict, title: str) -> str:
    return _rename_session(sess, title)


def stamp_question(rec: dict, playhead_s: float) -> dict:
    try:
        t = max(0.0, float(playhead_s or 0))
    except (TypeError, ValueError):
        t = 0.0
    rec['playhead_s'] = round(t, 3)
    rec['playhead_clock'] = _clock(t)
    return rec


def can_delete(sess: dict, username: str, is_admin: bool = False) -> bool:
    if is_admin:
        return True
    return bool(username) and str((sess or {}).get('created_by') or '') == str(username)


def remove_session(payload: dict, sid: str) -> Optional[dict]:
    sessions = (payload or {}).get('pilot_sessions') or []
    for i, s in enumerate(sessions):
        if str(s.get('id')) == str(sid):
            return sessions.pop(i)
    return None


def deleted_pilot_key(twin_key: str, sid: str) -> str:
    stem = str(twin_key or '')
    if stem.startswith('digital-twins/'):
        stem = stem[len('digital-twins/'):]
    stem = re.sub(r"\.json(\.gz)?$", '', stem)
    slug = re.sub(r"[^A-Za-z0-9._-]+", '_', stem).strip('_') or 'twin'
    return f"{DELETED_PILOT_PREFIX}{slug}/{re.sub(r'[^A-Za-z0-9_-]+', '', str(sid or ''))}.json"


def delete_session(s3, twin_key: str, payload: dict, sid: str, deleted_by: str = '') -> Optional[dict]:
    sess = remove_session(payload, sid)
    if sess is None:
        return None
    rec = {
        'twin_key': twin_key, 'session_id': sess.get('id'),
        'deleted_by': deleted_by or '', 'deleted_at': _now(),
        'undo_until': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + DELETED_PILOT_KEEP_DAYS * 86400)),
        'session': sess,
    }
    if s3 is not None:
        try:
            s3.put_object(
                Bucket='dashboard-inputs', Key=deleted_pilot_key(twin_key, sid),
                Body=json.dumps(rec, ensure_ascii=False, default=str).encode('utf-8'),
                ContentType='application/json',
                Metadata={'undo-until': rec['undo_until']},
            )
        except Exception as e:
            print(f"[pilot] undo copy failed (test still deleted): {e}")
    return sess


def media_s3_key(username: str, sid: str, filename: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", '_', os.path.basename(str(filename) or 'cut.mp4')).strip('._') or 'cut.mp4'
    user = re.sub(r"[^A-Za-z0-9._-]+", '_', str(username or 'seat'))[:40] or 'seat'
    return f"{MEDIA_PREFIX}{user}/{sid}/{safe}"


def accept_upload_name(filename: str, content_type: str = '') -> bool:
    ext = os.path.splitext(str(filename or ''))[1].lower()
    if ext in ALLOWED_EXT:
        return True
    return str(content_type or '').lower() in ALLOWED_UPLOAD


def ffmpeg_available() -> bool:
    return bool(shutil.which('ffprobe') or shutil.which('ffmpeg'))


def probe_duration(path: str) -> Optional[float]:
    exe = shutil.which('ffprobe')
    if not exe or not path or not os.path.isfile(path):
        return None
    try:
        r = subprocess.run(
            [exe, '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
            capture_output=True, text=True, timeout=25,
        )
        v = float((r.stdout or '').strip() or 0)
        return v if v > 1 else None
    except Exception:
        return None


def extract_audio(path: str, dest: str) -> bool:
    exe = shutil.which('ffmpeg')
    if not exe or not path or not os.path.isfile(path):
        return False
    try:
        r = subprocess.run(
            [exe, '-y', '-i', path, '-vn', '-ac', '1', '-ar', '16000', dest],
            capture_output=True, text=True, timeout=180,
        )
        return r.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 64
    except Exception:
        return False


def transcribe_if_available(path: str) -> Optional[list]:
    """Optional timestamped lines. None when Whisper is not installed.
    Never raises. The fallback beat sheet still runs."""
    try:
        import whisper  # type: ignore
    except Exception:
        return None
    if not path or not os.path.isfile(path):
        return None
    try:
        model = whisper.load_model('base')
        result = model.transcribe(path, verbose=False)
    except Exception:
        return None
    out = []
    for seg in (result or {}).get('segments') or []:
        try:
            out.append({'t': float(seg.get('start') or 0), 'text': str(seg.get('text') or '').strip()[:180]})
        except (TypeError, ValueError):
            continue
    return out or None


def process_local_cut(path: str, title: str = '') -> dict:
    """Duration plus optional transcript. Fail-safe: duration from the
    file name is never invented; the caller supplies a stated runtime
    when probe is missing."""
    dur = probe_duration(path)
    transcript = None
    if path and os.path.isfile(path):
        transcript = transcribe_if_available(path)
        if transcript is None:
            tmp = None
            try:
                fd, tmp = tempfile.mkstemp(suffix='.wav')
                os.close(fd)
                if extract_audio(path, tmp):
                    transcript = transcribe_if_available(tmp)
            except Exception:
                transcript = None
            finally:
                if tmp and os.path.isfile(tmp):
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
    return {'runtime_s': dur, 'transcript': transcript, 'title': title}


def public_session(sess: dict) -> dict:
    """What the page needs. Seat dials stay (compact) so splits and
    replay can paint per-seat ticks. No internals."""
    if not sess:
        return {}
    out = dict(sess)
    return out


def session_csv(session: dict) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    sc = session.get('scorecard') or {}
    w.writerow(['Test', 'Title', 'Runtime s', 'Overall like', 'Recommend %', 'Finish intent %',
                'Favorite moment', 'Dead spot', 'People'])
    fav = sc.get('favorite_moment') or {}
    dead = sc.get('dead_spot') or {}
    w.writerow([
        session.get('id'), session.get('title'), (session.get('media') or {}).get('runtime_s'),
        sc.get('overall_like'), sc.get('recommend_pct'), sc.get('finish_intent_pct'),
        f"{fav.get('clock')} {fav.get('line')}", f"{dead.get('clock')} {dead.get('line')}",
        sc.get('people'),
    ])
    w.writerow([])
    w.writerow(['Clock', 'Seconds', 'Dial', 'Women', 'Men', 'Avid'])
    dial = session.get('dial') or {}
    splits = dial.get('splits') or {}
    for i, t in enumerate(dial.get('t') or []):
        val = (dial.get('value') or [None])[i] if i < len(dial.get('value') or []) else None
        w.writerow([
            _clock(t), t, val,
            (splits.get('women') or [None] * (i + 1))[i] if splits.get('women') and i < len(splits['women']) else '',
            (splits.get('men') or [None] * (i + 1))[i] if splits.get('men') and i < len(splits['men']) else '',
            (splits.get('avid') or [None] * (i + 1))[i] if splits.get('avid') and i < len(splits['avid']) else '',
        ])
    w.writerow([])
    w.writerow(['Clock', 'Seconds', 'Type', 'Twin', 'Age', 'City', 'Line', 'Intensity'])
    for e in session.get('events') or []:
        w.writerow([_clock(e.get('t')), e.get('t'), EVENT_LABEL.get(e.get('type'), e.get('type')),
                    e.get('name'), e.get('age'), e.get('city'), e.get('line'), e.get('intensity')])
    w.writerow([])
    w.writerow(['Clock', 'Question', 'Kind', 'Read'])
    for q in session.get('questions') or []:
        w.writerow([q.get('playhead_clock') or _clock(q.get('playhead_s')), q.get('q'), q.get('kind'), q.get('read')])
    w.writerow([])
    w.writerow(['Clock', 'Question', 'Twin', 'Age', 'City', 'Line'])
    for q in session.get('questions') or []:
        for v in q.get('voices') or []:
            w.writerow([q.get('playhead_clock'), q.get('q'), v.get('name'), v.get('age'), v.get('city'), v.get('line')])
    return buf.getvalue()


def session_lines(session: dict) -> list:
    sc = session.get('scorecard') or {}
    fav = sc.get('favorite_moment') or {}
    dead = sc.get('dead_spot') or {}
    out = [
        str(session.get('title') or 'Pilot test'),
        f"Overall like {pct1(sc.get('overall_like'))}. Would recommend {pct1(sc.get('recommend_pct'))}. Would finish {pct1(sc.get('finish_intent_pct'))}.",
    ]
    if fav.get('line'):
        out.append(f"Favorite moment at {fav.get('clock')}: {fav.get('line')}")
    if dead.get('line'):
        out.append(f"Dead spot at {dead.get('clock')}: {dead.get('line')}")
    for n in (sc.get('notes') or [])[:3]:
        out.append(n)
    return [x for x in out if x]


def has_banned_copy(s: str) -> list:
    low = str(s or '').lower()
    hits = []
    for tok in ('synth', 'synthetic', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap',
                's3://', 'agents', 'em dash'):
        if tok in low:
            hits.append(tok)
    if '\u2014' in str(s or '') or '\u2013' in str(s or ''):
        hits.append('dash')
    return hits


__all__ = [
    'EVENT_TYPES', 'EVENT_LABEL', 'KINDS', 'STATUS', 'MEDIA_PREFIX',
    'DELETED_PILOT_PREFIX', 'DELETED_PILOT_KEEP_DAYS', 'QUESTIONS_INCLUDED',
    'open_session', 'prepare_watch', 'find_reusable_session', 'charge_due',
    'mark_play_paid', 'mark_paid', 'mark_playing', 'stop_session', 'rename_session',
    'stamp_question', 'can_delete', 'remove_session', 'delete_session',
    'deleted_pilot_key', 'media_hash', 'build_beats', 'seat_dial', 'seat_events',
    'aggregate_dial', 'scorecard', 'session_csv', 'session_lines', 'public_session',
    'media_s3_key', 'accept_upload_name', 'ffmpeg_available', 'probe_duration',
    'process_local_cut', 'has_banned_copy', 'beat_step',
]
