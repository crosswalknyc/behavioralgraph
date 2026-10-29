"""Pilot Testing (Digital Twins IQ): the room watches a cut on the clock.

The twins sit in their seats (the same roster and personas as Focus Group
IQ). The moderator presses Play. Each seat holds a dial (0 to 100) and
fires timestamped events (loved it, laughed, leaned in, lost, checked
out, wow). The page reveals those points locked to the video clock.
Prometheus can ask a question at any playhead, and the question carries
the moment (the last 60 to 90 seconds of what played). After the cut:
the studio scorecard (excellent / very good / total positive, definitely
recommend, would finish, tune-in, urgency, expectations, full price)
next to its norm and genre norm, the dial reads (favorite moment, dead
spot), splits by every demo, the event list, the transcript.

THE WATCH (2026-10-28)
  The beat sheet comes off the file: scene changes, a caption per scene
  from a sampled frame, the dialogue with timestamps, silences. Every
  seat then reads the scenes act by act in character (one call per act,
  all seats in it; migration/digital_twin_watch.py). The trait curve
  (``seat_dial``) is the PRIOR the persona read moves, never the answer.
  Events come from the persona output. A re-open never re-bills: the
  per-seat per-scene read is cached on the session by media hash.
  Without ffmpeg or a model the clock beat sheet and the trait curve
  still run (the regression path), so Play never dead-ends.

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

PILOT_VERSION = 'pilot-2'
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
MODES = ('pilot', 'feature', 'episode', 'binge', 'trailer', 'spot', 'title', 'key_art', 'script', 'concept')
MEDIA_PREFIX = 'digital-twins/pilot-tests/'
DELETED_PILOT_PREFIX = 'system/digital_twins/deleted_pilots/'
DELETED_PILOT_KEEP_DAYS = 30
QUESTIONS_INCLUDED = 12
MIN_RUNTIME_S = 15
MAX_RUNTIME_S = 4 * 60 * 60
WINDOW_BACK_S = 75          # the moment a question carries: the last 60 to 90 seconds
MEDIA_URL_EXPIRES_S = 600   # a signed link lives ten minutes; the player refreshes before that
PRIOR_WEIGHT = 0.30         # the trait curve's share of a seat's dial once the persona read is in
ALLOWED_UPLOAD = frozenset({
    'video/mp4', 'video/quicktime', 'video/webm', 'video/x-m4v',
    'application/octet-stream',
})
ALLOWED_EXT = frozenset({'.mp4', '.mov', '.webm', '.m4v'})

# Phase 2 price lines (decided 2026-10-28, not live): trailer or TV spot $2,500 an asset;
# title/logline $1,500 (up to six); key art $1,500 (up to six); concept $2,500; script read
# $3,500; recut re-test on the same room $2,750. The live price stays digital_twin_pilot.
PHASE2_PRICE_NOTES = {'trailer': 2500.0, 'spot': 2500.0, 'title': 1500.0, 'key_art': 1500.0, 'concept': 2500.0, 'script': 3500.0, 'recut': 2750.0}

_COMEDY = re.compile(r"\b(comedy|sitcom|funny|laugh|romcom|rom-com|sketch|spoof|parody)\b", re.I)
_THRILL = re.compile(r"\b(thriller|horror|scare|suspense|crime|noir|mystery)\b", re.I)
_ACTION = re.compile(r"\b(action|heist|war|chase|fight|superhero|stunt)\b", re.I)
_ROMANCE = re.compile(r"\b(romance|love|wedding|date|affair)\b", re.I)
_PILOT = re.compile(r"\b(pilot|episode|season|series|trailer|teaser|cut)\b", re.I)
_HORROR = re.compile(r"\b(horror|scare|scary|haunt|slasher)\b", re.I)
_FAMILY = re.compile(r"\b(family|kids|animated|animation|cartoon)\b", re.I)
_DOC = re.compile(r"\b(documentary|docuseries|true story|doc)\b", re.I)
_UNSCRIPTED = re.compile(r"\b(reality|unscripted|competition|game show|dating show)\b", re.I)


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
    """The tone family the trait prior leans on (not the norms genre)."""
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


def norms_genre(title: str = '', notes: str = '', votes: Optional[dict] = None) -> str:
    """The norms genre: the room's own read of the frames when it has
    one, else the words in the title and notes, else '' (all genres)."""
    if votes:
        try:
            from migration.digital_twin_watch import genre_from_votes
            g = genre_from_votes(votes, '')
            if g:
                return g
        except Exception:
            pass
    blob = f"{title or ''} {notes or ''}"
    if _HORROR.search(blob):
        return 'horror'
    if _COMEDY.search(blob):
        return 'comedy'
    if _THRILL.search(blob):
        return 'thriller'
    if _ACTION.search(blob):
        return 'action'
    if _ROMANCE.search(blob):
        return 'romance'
    if _FAMILY.search(blob):
        return 'family'
    if _DOC.search(blob):
        return 'documentary'
    if _UNSCRIPTED.search(blob):
        return 'unscripted'
    return ''


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
_TONE_WORDS = (
    ('funny', re.compile(r"\b(laugh|laughs|laughing|joke|jokes|punchline|giggl|deadpan|quip|banter|comic|sitcom|gag)\b", re.I)),
    ('action', re.compile(r"\b(chase|chases|fight|fights|punch|gun|guns|shoot|explosion|explodes|crash|run|running|sprint|car chase|brawl|stunt)\b", re.I)),
    ('tense', re.compile(r"\b(scream|knife|blood|dark|shadow|threat|stalk|argue|argument|confront|standoff|tension|nervous|hides|hiding|sirens?)\b", re.I)),
    ('tender', re.compile(r"\b(kiss|kisses|hug|hugs|embrace|cry|cries|tears|weep|holds? hands|confess|i love you|goodbye|funeral|apolog)\b", re.I)),
    ('wow', re.compile(r"\b(reveal|revealed|twist|unmask|explodes into|stadium|fireworks|cheer|crowd roars|wedding dance|epic)\b", re.I)),
    ('warm', re.compile(r"\b(dinner|kitchen|family|smile|smiles|toast|celebrat|birthday|reunion|porch|hug)\b", re.I)),
    ('quiet', re.compile(r"\b(silence|silent|empty|alone|stares?|staring|sits? alone|still|waiting|credits|black screen|title card)\b", re.I)),
    ('curious', re.compile(r"\b(mystery|clue|letter|map|opens? the (box|door|envelope)|who is|what is|introduc|arriv|new town|first day)\b", re.I)),
)


def _tone_from_text(text: str, default: str = '') -> str:
    blob = str(text or '')
    if not blob.strip():
        return default
    best, hits = default, 0
    for tone, rx in _TONE_WORDS:
        n = len(rx.findall(blob))
        if n > hits:
            best, hits = tone, n
    return best


def _pick(seq, *parts):
    if not seq:
        return ''
    return seq[int(unit(*parts) * len(seq)) % len(seq)]


def _dialogue_in(transcript: Optional[list], a: float, b: float, limit: int = 600):
    """(text, speaker) for the lines whose start falls inside [a, b)."""
    if not transcript:
        return '', ''
    parts, speakers = [], []
    for row in transcript:
        if not isinstance(row, dict):
            continue
        try:
            t = float(row.get('t') if row.get('t') is not None else row.get('start') or 0)
        except (TypeError, ValueError):
            continue
        if a <= t < b or (a <= float(row.get('end') or t) and t < a and float(row.get('end') or t) > a + 0.5):
            txt = ' '.join(str(row.get('text') or row.get('line') or '').split())
            if txt:
                parts.append(txt)
            if row.get('speaker'):
                speakers.append(str(row['speaker']))
    text = ' '.join(parts)
    return (text[:limit], (speakers[0] if speakers else ''))


def _silent(silences: Optional[list], a: float, b: float) -> bool:
    for s in silences or []:
        try:
            s0 = float(s[0])
            s1 = float(s[1]) if s[1] is not None else s0 + 60
        except (TypeError, ValueError, IndexError):
            continue
        if s0 <= a and s1 >= b:
            return True
    return False


# ------------------------------------------------------------ the beats
def build_scenes(title: str, duration_s: float, media_key: str, *, scenes: Optional[list] = None, captions: Optional[dict] = None,
                 transcript: Optional[list] = None, silences: Optional[list] = None, notes: str = '') -> list:
    """The scene list the room reads: off the file when scenes came off
    it (captions, dialogue, silences attached), else a clock grid whose
    stretches carry the dialogue when there is a transcript and a plain
    act line when there is not."""
    dur = clamp(float(duration_s or 0), MIN_RUNTIME_S, MAX_RUNTIME_S)
    tone_genre = _genre(title, notes)
    rows = []
    if scenes:
        src = [s for s in scenes if isinstance(s, dict) and s.get('t_end') is not None]
        src.sort(key=lambda s: float(s.get('t_start') or 0))
    else:
        from migration.digital_twin_cut import scenes_from_cuts
        src = scenes_from_cuts(None, dur)
    caps = captions or {}
    last_cap = None
    for i, s in enumerate(src):
        a = float(s.get('t_start') or 0)
        b = min(dur, float(s.get('t_end') or a))
        if b <= a:
            continue
        sid = int(s.get('id') if s.get('id') is not None else i)
        frac = a / dur if dur else 0
        act = _act(frac)
        cap = caps.get(sid) or caps.get(str(sid)) or {}
        if cap:
            last_cap = cap
        use = cap or (last_cap if (scenes and last_cap) else {})
        dialogue, speaker = _dialogue_in(transcript, a, b)
        caption = str(use.get('caption') or '')
        silent = _silent(silences, a, b)
        tone = str(use.get('tone') or '') or _tone_from_text(f"{caption} {dialogue}", '')
        if not tone:
            tone = _pick(_ACT_TONE[act] + _GENRE_TONE.get(tone_genre, ()), media_key, 'tone', sid)
        if silent and tone in ('funny', 'warm', 'curious') and not dialogue:
            tone = 'quiet'
        action = caption or _pick(_ACT_ACTION[act], media_key, 'action', sid)
        if cap.get('joke') or (tone == 'funny' and dialogue and unit(media_key, 'joke', sid) < 0.5):
            joke = True
        else:
            joke = False
        cue = 'credits' if (act == 'end' and ('credit' in caption.lower() or 'title' in caption.lower())) else (
            'silence' if silent else ('music swell' if tone in ('tender', 'wow') and unit(media_key, 'cue', sid) < 0.28 else ('dialogue heavy' if dialogue else 'on-screen action')))
        rows.append({'id': sid, 't_start': round(a, 3), 't_end': round(b, 3), 'act': act, 'caption': caption[:160], 'action': action[:160],
                     'dialogue': dialogue, 'speaker': speaker[:40], 'tone': tone, 'joke': joke, 'silence': silent, 'cue': cue,
                     'setting': str(use.get('setting') or '')[:40], 'grounded': bool(cap) or bool(dialogue)})
    return rows


def build_beats_from_scenes(scenes: list, duration_s: float, media_key: str, genre: str = '') -> list:
    """A beat every 2 to 4 seconds inside each scene (a scene always
    opens a beat). Beats carry t_start, t_end, scene_id, caption,
    dialogue, speaker, tone, act."""
    dur = clamp(float(duration_s or 0), MIN_RUNTIME_S, MAX_RUNTIME_S)
    step = beat_step(dur)
    out = []
    for s in scenes:
        a, b = float(s['t_start']), float(s['t_end'])
        t = a
        first = True
        while t < b - 0.01 or first:
            te = min(b, t + step)
            out.append({'i': len(out), 't': round(t, 3), 't_start': round(t, 3), 't_end': round(te, 3), 'scene_id': int(s['id']), 'act': s['act'],
                        'caption': s.get('caption') or '', 'action': s.get('action') or '', 'tone': s.get('tone') or '',
                        'dialogue': (s.get('dialogue') or '')[:180], 'speaker': s.get('speaker') or '', 'cue': s.get('cue') or '', 'genre': genre or '',
                        'joke': bool(s.get('joke'))})
            first = False
            t = te
            if te >= b:
                break
    if out and out[-1]['t'] < dur - 0.05:
        last = scenes[-1] if scenes else {'id': 0}
        out.append({'i': len(out), 't': round(dur, 3), 't_start': round(dur, 3), 't_end': round(dur, 3), 'scene_id': int(last.get('id') or 0), 'act': 'end',
                    'caption': '', 'action': 'the cut ends', 'tone': 'quiet', 'dialogue': '', 'speaker': '', 'cue': 'credits', 'genre': genre or '', 'joke': False})
    return out


def build_beats(title: str, duration_s: float, media_key: str, notes: str = '',
                transcript: Optional[list] = None, scenes: Optional[list] = None, captions: Optional[dict] = None,
                silences: Optional[list] = None) -> list:
    """The beat sheet. With scenes (and captions) from the file it is
    the cut itself; with only a transcript the lines pin dialogue to the
    clock; with nothing it is the title, the runtime and the media hash
    (always enough to press Play)."""
    rows = build_scenes(title, duration_s, media_key, scenes=scenes, captions=captions, transcript=transcript, silences=silences, notes=notes)
    beats = build_beats_from_scenes(rows, duration_s, media_key, genre=_genre(title, notes))
    if len(beats) < 8:   # a very short cut still carries a readable curve
        dur = clamp(float(duration_s or 0), MIN_RUNTIME_S, MAX_RUNTIME_S)
        step = dur / 8.0
        beats = []
        sid = rows[0]['id'] if rows else 0
        for i in range(8):
            t = round(i * step, 3)
            sc = next((s for s in rows if float(s['t_start']) <= t < float(s['t_end'])), rows[-1] if rows else {'id': 0, 'act': _act(t / dur), 'tone': 'curious', 'action': 'the cut plays'})
            beats.append({'i': i, 't': t, 't_start': t, 't_end': round(min(dur, t + step), 3), 'scene_id': int(sc.get('id', sid)), 'act': sc.get('act') or _act(t / dur),
                          'caption': sc.get('caption') or '', 'action': sc.get('action') or '', 'tone': sc.get('tone') or 'curious', 'dialogue': (sc.get('dialogue') or '')[:180],
                          'speaker': sc.get('speaker') or '', 'cue': sc.get('cue') or '', 'genre': _genre(title, notes), 'joke': bool(sc.get('joke'))})
    return beats


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
    """How this seat sits with this beat on habits alone (the prior).
    Persona first, never a coin flip."""
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
        if beat.get('joke'):
            fit += 1.8
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
    if beat.get('cue') == 'silence' and second >= 55:
        fit -= 2.6
    if wom >= 60 and tone in ('wow', 'funny', 'tender'):
        fit += 1.8
    return fit


def seat_dial(twin: dict, seat: dict, beats: list, session_seed: str, media_key: str) -> list:
    """The trait curve: where this seat's habits alone put the dial on
    every beat. The prior for the persona read; the whole answer only
    when no model watched."""
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
            level = level * 0.92 + 4.0   # the ending settles toward the middle; credits never spike the dial
        v = _messy_bp(subject, brand, f"beat-{b.get('i')}", level)
        out.append(v)
        level = v
    return out


def blend_dial(prior: list, beats: list, scene_levels: Optional[dict], subject: str, seat_name: str, media_key: str, seed: str) -> list:
    """The seat's dial once the persona read is in: the persona's level
    per scene (eased across the scene from the level before it) moves
    the trait prior (PRIOR_WEIGHT of the answer). Beats in a scene the
    persona did not read keep the prior."""
    if not scene_levels:
        return list(prior)
    out = []
    prev_level = None
    last_scene = None
    scene_start_i = 0
    scene_len = {}
    for b in beats:
        scene_len[b.get('scene_id')] = scene_len.get(b.get('scene_id'), 0) + 1
    k = 0
    for i, b in enumerate(beats):
        sid = b.get('scene_id')
        if sid != last_scene:
            last_scene = sid
            scene_start_i = i
            k = 0
        p = prior[i] if i < len(prior) else (prior[-1] if prior else 50.0)
        lvl = scene_levels.get(sid)
        if lvl is None:
            out.append(p)
            k += 1
            continue
        target = float(lvl)
        start = prev_level if prev_level is not None else target
        n = max(1, scene_len.get(sid, 1))
        eased = start + (target - start) * min(1.0, (k + 1) / float(min(n, 3)))
        v = p * PRIOR_WEIGHT + eased * (1.0 - PRIOR_WEIGHT) + jit(-0.9, 0.9, seed, media_key, i, 'blend')
        out.append(_messy_bp(subject, seat_name, f"blend-{i}", v))
        if k + 1 >= n:
            prev_level = target
        k += 1
    return out


def _event_line(kind: str, card: dict, beat: dict) -> str:
    name = str(card.get('name') or 'they')
    pron = {'woman': ('she', 'her'), 'man': ('he', 'his')}.get(str(card.get('gender') or ''), ('they', 'their'))
    act = str(beat.get('caption') or beat.get('action') or 'that beat')
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


def _event_rec(kind: str, t, seat: dict, line: str, intensity: float, beat_i, scene_id=None, source: str = 'room') -> dict:
    return {'type': kind, 't': t, 'seat': seat.get('seat'), 'name': seat.get('name'), 'age': seat.get('age'), 'city': seat.get('city'),
            'gender': seat.get('gender'), 'segment': seat.get('segment'), 'line': line, 'intensity': round(float(intensity), 4),
            'beat': beat_i, 'scene_id': scene_id, 'source': source}


def seat_events(twin: dict, seat: dict, beats: list, dial: list, session_seed: str, media_key: str) -> list:
    """The deterministic events (the regression path and the fallback
    when no model watched): thresholds on the beat's tone and the dial's
    movement, shaped by who the seat is."""
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
    fired_scenes = set()   # one event per seat per scene: a scene's tone holds across its beats
    scene_len = {}
    for b in beats:
        scene_len[b.get('scene_id')] = scene_len.get(b.get('scene_id'), 0.0) + max(0.5, float(b.get('t_end') or 0) - float(b.get('t_start') or 0))
    for i, b in enumerate(beats):
        v = dial[i] if i < len(dial) else prev
        jump = v - prev
        tone = str(b.get('tone') or '')
        u = unit(sid, media_key, b.get('i'), 'evt')
        fired = None
        if b.get('scene_id') in fired_scenes:
            prev = v
            continue
        # a real room marks a moment about once a minute or two a seat, more at a joke or a turn
        boost = 1.7 if ((tone == 'funny' and b.get('joke')) or abs(jump) >= 5.0 or tone == 'wow') else 1.0
        gate = clamp(scene_len.get(b.get('scene_id'), 3.0) / 75.0 * boost, 0.03, 0.6)
        if unit(sid, media_key, b.get('scene_id'), 'gate') > gate:
            prev = v
            continue
        if tone == 'funny' and v >= 52:
            need = 0.16 if age < 40 else 0.28
            if b.get('joke'):
                need += 0.14
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
            intensity = clamp(0.42 + abs(jump) / 14.0 + unit(sid, 'int', i) * 0.28, 0.31, 0.97)
            out.append(_event_rec(fired, b.get('t'), seat, _event_line(fired, card, b), intensity, b.get('i'), b.get('scene_id'), 'habit'))
            fired_scenes.add(b.get('scene_id'))
        prev = v
    # A seat that never fires still gets one honest mark so the rail is not empty
    # for a quiet persona; only when the dial actually moved.
    if not out and dial:
        hi_i = max(range(len(dial)), key=lambda k: dial[k])
        lo_i = min(range(len(dial)), key=lambda k: dial[k])
        kind = 'love' if dial[hi_i] - dial[lo_i] >= 8 else 'lean-in'
        b = beats[hi_i] if hi_i < len(beats) else beats[-1]
        out.append(_event_rec(kind, b.get('t'), seat, _event_line(kind, card, b), 0.41 + 0.17 * unit(sid, 'solo'), b.get('i'), b.get('scene_id'), 'habit'))
    return out


def persona_events(seat: dict, card: dict, beats: list, dial: list, scene_read: dict, scenes_by_id: dict, session_seed: str, media_key: str) -> list:
    """Events the persona fired while watching: type, time inside the
    scene, the seat's own words (scrubbed upstream), intensity from the
    dial's move and the seat's salt."""
    out = []
    if not scene_read:
        return out
    sid = f"{session_seed}|seat{seat.get('seat')}|{seat.get('name')}"
    beats_by_scene = {}
    for i, b in enumerate(beats):
        beats_by_scene.setdefault(b.get('scene_id'), []).append(i)
    for scene_id, row in sorted(scene_read.items(), key=lambda kv: float((scenes_by_id.get(kv[0]) or {}).get('t_start') or 0)):
        ev = str((row or {}).get('event') or '')
        if ev not in EVENT_TYPES:
            continue
        idxs = beats_by_scene.get(scene_id) or []
        sc = scenes_by_id.get(scene_id) or {}
        if idxs:
            k = idxs[min(len(idxs) - 1, int(unit(sid, media_key, scene_id, 'evt-at') * len(idxs)))]
            b = beats[k]
            t = b.get('t')
            jump = (dial[k] - dial[max(0, k - 1)]) if k < len(dial) else 0.0
        else:
            b = {'caption': sc.get('caption'), 'action': sc.get('action')}
            k = None
            t = sc.get('t_start')
            jump = 0.0
        line = str(row.get('say') or '').strip() or _event_line(ev, card, b)
        intensity = clamp(0.44 + abs(jump) / 12.0 + unit(sid, 'int', scene_id) * 0.26, 0.33, 0.98)
        out.append(_event_rec(ev, t, seat, line, intensity, k, scene_id, 'watch'))
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


def split_dial(roster: list, seat_dials: dict, pred, subject: str, media_key: str, label: str, min_seats: int = 2) -> Optional[list]:
    """The dial of the seats a predicate picks (any predicate)."""
    picked = []
    for seat in roster:
        sid = str(seat.get('seat'))
        if sid in seat_dials and pred(seat):
            picked.append(seat_dials[sid])
    if len(picked) < min_seats or len(picked) == len(seat_dials):
        return None
    return aggregate_dial(picked, f"{subject}|{label}", media_key)


# ----------------------------------------------------------- the splits
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
    if stage in ('discovering', 'trying', 'lapsing'):
        return False
    return _trait(twin, 'franchise_loyalty') >= 60


def _age(seat) -> int:
    try:
        return int(seat.get('age') or 35)
    except (TypeError, ValueError):
        return 35


def _age_bucket(seat) -> str:
    a = _age(seat)
    if a < 25:
        return '18 to 24'
    if a < 35:
        return '25 to 34'
    if a < 45:
        return '35 to 44'
    if a < 55:
        return '45 to 54'
    return '55 and over'


def _lgbtq(seat) -> bool:
    o = str(seat.get('orientation') or '').lower()
    return bool(o) and not ('straight' in o or 'hetero' in o or 'prefer' in o)


def _college(seat) -> bool:
    e = str(seat.get('education') or '').lower()
    return any(k in e for k in ('bachelor', 'graduate', 'college graduate', 'professional', 'master', 'doctor'))


def _ethnicity(seat) -> str:
    e = str(seat.get('ethnicity') or '').strip()
    return e if e and 'prefer' not in e.lower() else ''


def _stage(seat) -> str:
    return str(seat.get('lifecycle_stage') or '')


def quadrant_of(seat) -> str:
    if _is_woman(seat):
        return 'women_under_35' if _age(seat) < 35 else 'women_35_plus'
    if _is_man(seat):
        return 'men_under_35' if _age(seat) < 35 else 'men_35_plus'
    return 'under_35' if _age(seat) < 35 else '35_plus'


QUADRANTS = (('women_under_35', 'Women under 35'), ('women_35_plus', 'Women 35+'), ('men_under_35', 'Men under 35'), ('men_35_plus', 'Men 35+'))
_SPLIT_LABEL = {'women': 'Women', 'men': 'Men', 'avid': 'Avid', 'casual': 'Casual', 'lgbtq': 'LGBTQ+', 'straight': 'Straight',
                'college': 'College graduates', 'no_college': 'No degree', 'fan_of_lead': 'Fans of the lead', 'not_fan_of_lead': 'Not fans of the lead'}


def split_specs(roster: list, twin: dict) -> list:
    """[(key, label, group, predicate)] for every split the room can
    make from what the seats carry."""
    specs = [
        ('women', 'Women', 'gender', _is_woman), ('men', 'Men', 'gender', _is_man),
    ]
    for key, label in QUADRANTS:
        specs.append((key, label, 'quadrant', (lambda s, k=key: quadrant_of(s) == k)))
    for b in ('18 to 24', '25 to 34', '35 to 44', '45 to 54', '55 and over'):
        specs.append(('age_' + re.sub(r"[^0-9a-z]+", '_', b.lower()).strip('_'), b, 'age', (lambda s, bb=b: _age_bucket(s) == bb)))
    eths = sorted({_ethnicity(s) for s in roster if _ethnicity(s)})
    for e in eths:
        specs.append(('eth_' + re.sub(r"[^0-9a-z]+", '_', e.lower()).strip('_'), e, 'ethnicity', (lambda s, ee=e: _ethnicity(s) == ee)))
    specs += [('lgbtq', 'LGBTQ+', 'orientation', _lgbtq), ('straight', 'Straight', 'orientation', (lambda s: not _lgbtq(s) and bool(str(s.get('orientation') or '').strip()))),
              ('college', 'College graduates', 'education', _college), ('no_college', 'No degree', 'education', (lambda s: not _college(s) and bool(str(s.get('education') or '').strip())))]
    for st in ('discovering', 'trying', 'in the habit', 'lapsing', 'returning'):
        specs.append(('stage_' + st.replace(' ', '_'), st[0].upper() + st[1:], 'lifecycle', (lambda s, ss=st: _stage(s) == ss)))
    specs += [('avid', 'Avid', 'fanship', (lambda s: _is_avid(s, twin))), ('casual', 'Casual', 'fanship', (lambda s: not _is_avid(s, twin)))]
    if any(s.get('fan_of_lead') is not None for s in roster):
        specs += [('fan_of_lead', 'Fans of the lead', 'fanship', (lambda s: bool(s.get('fan_of_lead')))),
                  ('not_fan_of_lead', 'Not fans of the lead', 'fanship', (lambda s: s.get('fan_of_lead') is not None and not s.get('fan_of_lead')))]
    return specs


def all_splits(roster: list, seat_dials: dict, twin: dict, subject: str, media_key: str) -> dict:
    """{key: {label, group, n, value: [...]}} for every split with at
    least two seats that is not the whole room."""
    out = {}
    for key, label, group, pred in split_specs(roster, twin):
        n = sum(1 for s in roster if str(s.get('seat')) in seat_dials and pred(s))
        line = split_dial(roster, seat_dials, pred, subject, media_key, key)
        if line:
            out[key] = {'label': label, 'group': group, 'n': n, 'value': line}
    return out


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


# ---------------------------------------------------------- the battery
def format_for(session: dict) -> str:
    """The battery and norms format: the mode when it is one of the
    formats, else read from the runtime (a feature past 65 minutes, a
    trailer under four)."""
    mode = str((session or {}).get('mode') or 'pilot')
    if mode in ('feature', 'episode', 'trailer'):
        return mode
    if mode in ('spot',):
        return 'trailer'
    if mode in ('binge',):
        return 'episode'
    if mode in ('title', 'key_art', 'script', 'concept'):
        return 'feature'
    dur = float(((session or {}).get('media') or {}).get('runtime_s') or 0)
    if dur >= 65 * 60:
        return 'feature'
    if 0 < dur <= 240:
        return 'trailer'
    return 'pilot'


def _seat_people(roster: list, segments: list) -> dict:
    """{seat: people} from the segment's people spread over its seats."""
    seg_people = {str(s.get('label')): int(s.get('people') or 0) for s in segments or []}
    seg_n = {}
    for r in roster:
        seg_n[str(r.get('segment'))] = seg_n.get(str(r.get('segment')), 0) + 1
    out = {}
    for r in roster:
        lab = str(r.get('segment'))
        out[int(r['seat'])] = int(round(seg_people.get(lab, 0) / float(seg_n.get(lab) or 1)))
    return out


def _battery_prior(qid: str, mean_dial: float, options: list) -> dict:
    """The room's own leaning over the card's options from where its
    dial sat (the smoothing prior the published shares use)."""
    import math
    m = clamp(float(mean_dial or 50.0), 0.0, 100.0) / 100.0
    centers = {'overall': [0.9, 0.7, 0.5, 0.3, 0.1], 'recommend': [0.85, 0.6, 0.35, 0.1], 'urgency': [0.9, 0.7, 0.45, 0.15],
               'urgency_pilot': [0.85, 0.5, 0.15], 'expectations': [0.8, 0.5, 0.2]}.get(qid)
    ids = [o['id'] for o in options]
    if centers and len(centers) == len(ids):
        w = [math.exp(-((m - c) ** 2) / (2 * 0.17 ** 2)) for c in centers]
        tot = sum(w) or 1.0
        out = {i: max(0.02, x / tot) for i, x in zip(ids, w)}
    elif len(ids) == 2:
        off = {'full_price': 0.38, 'finish': 0.22, 'tune_in': 0.30}.get(qid, 0.28)
        p = clamp((m - off) / 0.5, 0.06, 0.94)
        out = {ids[0]: p, ids[1]: 1.0 - p}
    else:
        out = {i: 1.0 / len(ids) for i in ids}
    tot = sum(out.values()) or 1.0
    return {k: v / tot for k, v in out.items()}


def _seat_score(card: dict, twin: dict, series: list, seed: str) -> float:
    """One number for the deterministic card: the seat's own dial (mean
    and ending) shaped by its habits, salted."""
    if not series:
        return 50.0
    tail = max(2, int(len(series) * 0.25))
    mean = sum(series) / float(len(series))
    end = sum(series[-tail:]) / float(tail)
    s = 0.55 * mean + 0.45 * end
    habits = ' '.join(card.get('habits') or [])
    if 'deeply loyal' in habits:
        s += 3.2
    if str(card.get('lifecycle_stage') or '') == 'lapsing':
        s -= 3.6
    if 'very price-conscious' in habits:
        s -= 1.4
    return clamp(s + jit(-5.5, 5.5, seed, 'score'), 2.0, 98.0)


def _fallback_answers(qs: list, card: dict, twin: dict, series: list, seed: str) -> dict:
    """The deterministic card for one seat: {qid: oid}."""
    s = _seat_score(card, twin, series, seed)
    habits = ' '.join(card.get('habits') or [])
    binge = _trait(twin, 'binge_vs_appointment')
    out = {}
    # Thresholds sit so an ordinary cut (seat scores around 60) lands near the published
    # norms (excellent about three in ten, total positive about two in three, definitely
    # recommend about half); a cut the dial loved or hated moves every answer with it.
    for qid, _label, _kind, opts in qs:
        ids = [o[0] for o in opts]
        if qid == 'overall':
            pick = 0 if s >= 68 else 1 if s >= 61 else 2 if s >= 54 else 3 if s >= 47 else 4
        elif qid == 'recommend':
            pick = 0 if s >= 65 else 1 if s >= 56 else 2 if s >= 47 else 3
        elif qid == 'finish':
            pick = 0 if (s + (3 if binge >= 55 else 0) + jit(-3, 3, seed, qid)) >= 50 else 1
        elif qid == 'tune_in':
            pick = 0 if (s + jit(-3, 3, seed, qid)) >= 61 else 1
        elif qid == 'urgency':
            pick = 0 if s >= 68 else 1 if s >= 61 else 2 if s >= 53 else 3
        elif qid == 'urgency_pilot':
            pick = 0 if s >= 64 else 1 if s >= 50 else 2
        elif qid == 'expectations':
            pick = 0 if s >= 67 else 1 if s >= 52 else 2
        elif qid == 'full_price':
            pick = 0 if (s >= 66 and 'very price-conscious' not in habits) or (s >= 74) else 1
        else:
            pick = 0
        out[qid] = ids[min(pick, len(ids) - 1)]
    return out


def _confidence(card: dict, series: list, seed: str) -> int:
    if not series:
        return 3
    spread = max(series) - min(series)
    base = 3 + (1 if spread >= 18 else 0) - (1 if spread < 7 else 0)
    return int(clamp(base + (1 if unit(seed, 'conf') < 0.3 else 0), 2, 5))


def _publish(answers: list, options: list, roster: list, segments: list, priors: dict, seed: str) -> tuple:
    """The room's published split for one question: seat votes weighted
    by the people each seat stands for and smoothed by the prior
    (Focus Group IQ's arithmetic: no 0.0%, messy people counts, shares
    to one decimal summing to 100)."""
    from migration.digital_twin_room import weigh
    opts_out, by_segment = weigh(answers, options, roster, segments, priors, seed)
    rows = [{'id': o['id'], 'label': o['label'], 'lean': o['lean'], 'pct': float(o['pct']), 'people': int(o['people']), 'seats_n': int(o.get('seats_n') or 0),
             'tally': o.get('tally') or ''} for o in opts_out if o['id'] != 'other']
    return rows, by_segment


def _question_result(qid: str, label: str, kind: str, rows: list) -> dict:
    out = {'id': qid, 'label': label, 'kind': kind, 'rows': rows}
    if rows:
        out['top_pct'] = round(rows[0]['pct'], 1)
        out['top_people'] = rows[0]['people']
    if qid == 'overall' and len(rows) >= 2:
        out['total_positive_pct'] = round(rows[0]['pct'] + rows[1]['pct'], 1)
        out['total_positive_people'] = rows[0]['people'] + rows[1]['people']
    if kind == 'yes_no' and rows:
        out['yes_pct'] = round(rows[0]['pct'], 1)
    return out


def battery_scorecard(twin: dict, roster: list, segments: list, seat_dials: dict, beats: list, scenes: list, events: list, *, fmt: str, subject: str,
                      media_key: str, session_seed: str, cards: Optional[dict] = None, answers_in: Optional[dict] = None, why_in: Optional[dict] = None) -> dict:
    """The studio battery for the whole room and for every quadrant.
    ``answers_in`` = {seat: {qid: oid}} from the persona card when a
    model answered; seats it misses answer from their own dial."""
    from migration.digital_twin_watch import battery_questions
    qs = battery_questions(fmt)
    cards = cards or {int(r['seat']): _card_for(twin, r, session_seed) for r in roster}
    seat_ans, seat_conf = {}, {}
    seat_fallback = 0
    for r in roster:
        sno = int(r['seat'])
        series = seat_dials.get(str(sno)) or []
        seed = f"{session_seed}|{media_key}|seat{sno}|card"
        got = dict((answers_in or {}).get(sno) or {})
        fb = _fallback_answers(qs, cards[sno], twin, series, seed)
        if not got:
            seat_fallback += 1
        for qid, _l, _k, _o in qs:
            got.setdefault(qid, fb[qid])
        seat_ans[sno] = got
        seat_conf[sno] = _confidence(cards[sno], series, seed)
    people_by_seat = _seat_people(roster, segments)
    room_people = sum(int(s.get('people') or 0) for s in segments) or sum(people_by_seat.values()) or len(roster) * 800

    def mean_of(seat_ids):
        vals = []
        for s in seat_ids:
            series = seat_dials.get(str(s)) or []
            if series:
                tail = max(2, int(len(series) * 0.25))
                vals.append(0.5 * sum(series) / len(series) + 0.5 * sum(series[-tail:]) / tail)
        return sum(vals) / len(vals) if vals else 50.0

    def run(seat_ids, label, people, seed_tag):
        sub = [r for r in roster if int(r['seat']) in seat_ids]
        sub_roster = [dict(r, segment=label) for r in sub]
        segs = [{'label': label, 'people': int(people)}]
        m = mean_of(seat_ids)
        out = {}
        for qid, qlabel, kind, opts in qs:
            options = [{'id': o[0], 'label': o[1], 'lean': o[2]} for o in opts]
            answers = [{'seat': int(r['seat']), 'option': seat_ans[int(r['seat'])][qid], 'confidence': seat_conf[int(r['seat'])]} for r in sub]
            priors = {label: _battery_prior(qid, m, options)}
            rows, _bs = _publish(answers, options, sub_roster, segs, priors, f"{session_seed}|{media_key}|{seed_tag}|{qid}")
            out[qid] = _question_result(qid, qlabel, kind, rows)
        return out, m
    all_ids = [int(r['seat']) for r in roster]
    room_q, room_mean = run(all_ids, 'Whole room', room_people, 'room')
    quadrants = [{'key': 'total', 'label': 'Total', 'n': len(roster), 'people': int(room_people), **_money(room_q)}]
    for key, label in QUADRANTS:
        ids = [int(r['seat']) for r in roster if quadrant_of(r) == key]
        if not ids:
            quadrants.append({'key': key, 'label': label, 'n': 0, 'people': 0, 'excellent_pct': None, 'total_positive_pct': None, 'definitely_recommend_pct': None, 'finish_pct': None})
            continue
        ppl = messy_count(subject, f"quad-{key}", sum(people_by_seat.get(i, 0) for i in ids))
        q, _m = run(ids, label, ppl, f"quad-{key}")
        quadrants.append({'key': key, 'label': label, 'n': len(ids), 'people': int(ppl), **_money(q)})
    by_seat = []
    for r in roster:
        sno = int(r['seat'])
        by_seat.append({'seat': sno, 'name': r.get('name'), 'answers': seat_ans[sno], 'why': str((why_in or {}).get(sno) or '')[:200]})
    head = _money(room_q)
    head.update({'tune_in_pct': room_q['tune_in'].get('yes_pct') if room_q.get('tune_in') else None,
                 'urgency_opening_pct': room_q['urgency'].get('top_pct') if room_q.get('urgency') else (room_q['urgency_pilot'].get('top_pct') if room_q.get('urgency_pilot') else None),
                 'better_than_expected_pct': room_q['expectations'].get('top_pct') if room_q.get('expectations') else None,
                 'full_price_pct': room_q['full_price'].get('yes_pct') if room_q.get('full_price') else None})
    confused = sum(1 for e in events if e.get('type') == 'confuse')
    confused_seats = len({e.get('seat') for e in events if e.get('type') == 'confuse'})
    head['confusion_pct'] = round(clamp(confused_seats / float(len(roster) or 1) * 100.0 + jit(-0.4, 0.4, subject, media_key, 'conf'), 0.1, 99.9), 1) if confused else round(jit(0.3, 2.4, subject, media_key, 'conf0'), 1)
    return {'format': fmt, 'questions': [room_q[qid] for qid, _l, _k, _o in qs], 'headline': head, 'quadrants': quadrants, 'by_seat': by_seat,
            'seats_from_dial': seat_fallback, 'room_mean_dial': round(room_mean, 2)}


def _money(q: dict) -> dict:
    ov = q.get('overall') or {}
    rc = q.get('recommend') or {}
    fn = q.get('finish') or {}
    return {'excellent_pct': ov.get('top_pct'), 'excellent_people': ov.get('top_people'), 'total_positive_pct': ov.get('total_positive_pct'),
            'total_positive_people': ov.get('total_positive_people'), 'definitely_recommend_pct': rc.get('top_pct'), 'definitely_recommend_people': rc.get('top_people'),
            'finish_pct': fn.get('yes_pct')}


# -------------------------------------------------------------- norms
def _norms_s3(s3=None):
    if s3 is not None:
        return s3
    try:
        from migration.digital_twin_norms import _s3_default
        return _s3_default()
    except Exception:
        return None


def norms_for(fmt: str, genre: str = 'all', s3=None) -> dict:
    """The norm and genre norm columns for a format and genre (no
    sources). Public for Phase 2 (modes, a Norms tab)."""
    from migration import digital_twin_norms as N
    try:
        return N.norms_for(fmt, genre or 'all', s3=_norms_s3(s3))
    except Exception as e:
        print(f"[pilot] norms read failed: {e}")
        return N.norms_for(fmt, genre or 'all', doc=N.load(None))


def norm_metrics(session: dict) -> dict:
    """The metrics a finished test contributes to the ledger."""
    sc = (session or {}).get('scorecard') or {}
    head = ((sc.get('battery') or {}).get('headline')) or {}
    out = {}
    for src, dst in (('excellent_pct', 'excellent'), ('total_positive_pct', 'total_positive'), ('definitely_recommend_pct', 'definitely_recommend'),
                     ('finish_pct', 'finish'), ('tune_in_pct', 'tune_in'), ('urgency_opening_pct', 'urgency_opening'), ('confusion_pct', 'confusion')):
        v = head.get(src)
        if v is not None:
            out[dst] = float(v)
    return out


def record_norms(session: dict, s3=None) -> Optional[dict]:
    """Every stopped test adds to the ledger (format x genre x metric).
    Idempotent per test id. Never raises."""
    try:
        from migration import digital_twin_norms as N
        metrics = norm_metrics(session)
        if not metrics:
            return None
        fmt = str((session.get('scorecard') or {}).get('format') or format_for(session))
        doc = N.record(_norms_s3(s3), fmt, str(session.get('genre') or 'all') or 'all', metrics, str(session.get('id') or ''))
        if doc is not None:
            session['norms_recorded_at'] = _now()
        return doc
    except Exception as e:
        print(f"[pilot] norms record skipped: {e}")
        return None


def attach_norms(scorecard: dict, fmt: str, genre: str, s3=None) -> dict:
    from migration import digital_twin_norms as N
    norms = norms_for(fmt, genre or 'all', s3=s3)
    scorecard['norms'] = norms
    head = ((scorecard.get('battery') or {}).get('headline')) or {}
    lines = []
    for metric, key in (('definitely_recommend', 'definitely_recommend_pct'), ('total_positive', 'total_positive_pct'), ('excellent', 'excellent_pct')):
        if head.get(key) is not None:
            lines.append(N.norm_line(metric, head[key], norms, fmt, genre or 'all'))
    scorecard['norm_lines'] = [l for l in lines if l]
    rows_norm = {}
    for metric in N.METRICS:
        a = (norms.get('all') or {}).get(metric)
        g = (norms.get('genre') or {}).get(metric)
        rows_norm[metric] = {'norm': a.get('value') if a else None, 'genre_norm': (g.get('value') if g else None)}
    scorecard['norm_columns'] = rows_norm
    return scorecard


# ----------------------------------------------------------- the probes
def probe_points(beats: list, dial: list, events: list, subject: str = '', limit: int = 3) -> list:
    """The valleys and peaks on the aggregate dial, each with the probe a
    moderator would ask there. One click asks it at that playhead."""
    if not dial or len(dial) < 6:
        return []
    n = len(dial)
    win = max(2, n // 24)
    cands = []
    for i in range(win, n - win):
        seg = dial[i - win:i + win + 1]
        if dial[i] == min(seg) and dial[i] < sum(dial) / n - 1.0:
            cands.append(('valley', i, (sum(dial) / n) - dial[i]))
        elif dial[i] == max(seg) and dial[i] > sum(dial) / n + 1.0:
            cands.append(('peak', i, dial[i] - (sum(dial) / n)))
    out = []
    for kind in ('valley', 'peak'):
        picked = []
        for c in sorted([c for c in cands if c[0] == kind], key=lambda c: -c[2]):
            if all(abs(c[1] - p[1]) > max(3, n // 12) for p in picked):
                picked.append(c)
            if len(picked) >= limit:
                break
        for _k, i, depth in picked:
            b = beats[i] if i < len(beats) else beats[-1]
            on = str(b.get('caption') or '')
            near = [e for e in events if e.get('beat') is not None and abs(int(e['beat']) - i) <= win]
            lost = sum(1 for e in near if e.get('type') in ('confuse', 'check-out'))
            if kind == 'valley':
                chip = 'Ask why they drifted here' if lost else 'Ask what lost them here'
                q = 'What lost you just now?' if not on else f"What lost you in the {on[:60].rstrip('.')}?"
            else:
                chip = 'Ask what landed here'
                q = 'What landed for you just now?' if not on else f"What worked for you in the {on[:60].rstrip('.')}?"
            out.append({'kind': kind, 't': b.get('t'), 'clock': _clock(b.get('t')), 'dial': dial[i], 'depth': round(depth, 2), 'chip': chip, 'q': q,
                        'on_screen': on[:120], 'events_near': len(near)})
    out.sort(key=lambda p: float(p.get('t') or 0))
    return out


def _standard_battery_lines(fmt: str) -> list:
    from migration.digital_twin_watch import battery_questions
    return [f"{label}: " + ' / '.join(o[1] for o in opts) for _q, label, _k, opts in battery_questions(fmt)]


def fallback_guide(scenes: list, duration_s: float, fmt: str = 'pilot') -> dict:
    """The standing guide when no model drafted one: a question at the
    end of the cold open, each act turn, the midpoint and the ending."""
    dur = float(duration_s or (scenes[-1]['t_end'] if scenes else 60))
    marks = [(0.08, 'What do you think this is about so far?', 'open', 'the end of the cold open'),
             (0.28, 'Who are you watching this for?', 'open', 'the setup is in'),
             (0.5, 'Would you keep watching if it were on at home?', 'yes_no', 'the midpoint'),
             (0.62, 'Is anything confusing you?', 'show_of_hands', 'before the turn'),
             (0.86, 'Did that turn land for you?', 'yes_no', 'after the turn'),
             (0.97, 'Rate it one to five.', 'rating', 'the ending')]
    qs = []
    for frac, q, kind, why in marks:
        t = min(dur - 1, max(0.0, frac * dur))
        sc = next((s for s in scenes if float(s['t_start']) <= t < float(s['t_end'])), None)
        on = str((sc or {}).get('caption') or '')
        qs.append({'t': round(float((sc or {}).get('t_start') if sc else t), 3), 'clock': _clock((sc or {}).get('t_start') if sc else t), 'q': q, 'kind': kind,
                   'why': why + (f": {on[:60]}" if on else '')})
    return {'questions': qs, 'battery': _standard_battery_lines(fmt), 'source': 'standard'}


# ---------------------------------------------------------- the moment
def _scene_at(scenes: list, t: float) -> Optional[dict]:
    for s in scenes or []:
        if float(s.get('t_start') or 0) <= t < float(s.get('t_end') or 0):
            return s
    return scenes[-1] if scenes and t >= float(scenes[-1].get('t_end') or 0) else None


def question_window(session: dict, playhead_s: float, back_s: float = WINDOW_BACK_S) -> dict:
    """What just played: the last 60 to 90 seconds of beats (caption,
    dialogue, dial), the current scene, the top events in the window,
    and the moderator's opening clause ('At 12:40, right after the
    hallway reveal')."""
    try:
        t = max(0.0, float(playhead_s or 0))
    except (TypeError, ValueError):
        t = 0.0
    beats = session.get('beats') or []
    scenes = session.get('scenes') or []
    dial = (session.get('dial') or {}).get('value') or []
    ts = (session.get('dial') or {}).get('t') or [b.get('t') for b in beats]
    a = max(0.0, t - back_s)
    rows, seen = [], set()
    vals = []
    for i, b in enumerate(beats):
        bt = float(b.get('t') or 0)
        if bt < a or bt > t + 0.05:
            continue
        v = dial[i] if i < len(dial) else None
        if v is not None:
            vals.append(v)
        key = (b.get('scene_id'), b.get('caption') or b.get('action'), b.get('dialogue'))
        if key in seen:
            continue
        seen.add(key)
        rows.append({'clock': _clock(bt), 't': bt, 'on_screen': str(b.get('caption') or b.get('action') or '')[:140], 'dialogue': str(b.get('dialogue') or '')[:160],
                     'dial': round(v, 1) if v is not None else None, 'tone': b.get('tone') or ''})
    rows = rows[-14:]
    sc = _scene_at(scenes, t)
    evs = [e for e in session.get('events') or [] if a <= float(e.get('t') or 0) <= t + 0.05]
    evs.sort(key=lambda e: -float(e.get('intensity') or 0))
    top = [{'clock': _clock(e.get('t')), 'name': e.get('name'), 'type': EVENT_LABEL.get(e.get('type'), e.get('type')), 'line': e.get('line')} for e in evs[:6]]
    now_v = None
    for i, bt in enumerate(ts):
        if float(bt or 0) <= t and i < len(dial):
            now_v = dial[i]
    on = str((sc or {}).get('caption') or (sc or {}).get('action') or '')
    frame = f"At {_clock(t)}" + (f", right after {on[0].lower() + on[1:] if on else ''}".rstrip(', ') if on else '')
    return {'playhead_s': round(t, 3), 'clock': _clock(t), 'from_clock': _clock(a), 'from_s': round(a, 3),
            'scene': ({'id': sc.get('id'), 'clock': _clock(sc.get('t_start')), 'on_screen': on[:160], 'dialogue': str(sc.get('dialogue') or '')[:220]} if sc else None),
            'beats': rows, 'events': top, 'dial_now': round(now_v, 1) if now_v is not None else None,
            'dial_window_mean': round(sum(vals) / len(vals), 1) if vals else None, 'frame': frame[:200]}


def seat_window(session: dict, seat_no: int, playhead_s: float, back_s: float = WINDOW_BACK_S) -> dict:
    """One seat's own dial and events in the window (what the persona is
    handed as its own experience of the moment)."""
    try:
        t = max(0.0, float(playhead_s or 0))
    except (TypeError, ValueError):
        t = 0.0
    a = max(0.0, t - back_s)
    beats = session.get('beats') or []
    series = (session.get('seat_dials') or {}).get(str(seat_no)) or []
    vals = [(float(b.get('t') or 0), series[i]) for i, b in enumerate(beats) if i < len(series) and a <= float(b.get('t') or 0) <= t + 0.05]
    evs = [e for e in session.get('events') or [] if int(e.get('seat') or -1) == int(seat_no) and a <= float(e.get('t') or 0) <= t + 0.05]
    out = {'dial_now': round(vals[-1][1], 1) if vals else None, 'dial_start': round(vals[0][1], 1) if vals else None,
           'moved': round(vals[-1][1] - vals[0][1], 1) if len(vals) >= 2 else 0.0,
           'events': [{'clock': _clock(e.get('t')), 'type': EVENT_LABEL.get(e.get('type'), e.get('type')), 'said': e.get('line')} for e in evs[-3:]]}
    return out


def ask_stimulus(session: dict, playhead_s: float, extra: Optional[dict] = None) -> dict:
    """The stimulus a question on the clock carries into the room."""
    win = question_window(session, playhead_s)
    seats = {}
    for r in ((session.get('group') or {}).get('roster') or []):
        try:
            seats[str(int(r['seat']))] = seat_window(session, int(r['seat']), playhead_s)
        except Exception:
            continue
    stim = {'in_cut': True, 'window': win, 'seat_windows': seats, 'cut': {'title': str((session.get('media') or {}).get('title') or session.get('title') or ''),
                                                                            'genre': session.get('genre') or '', 'format': format_for(session), 'cut_label': session.get('cut_label') or 'Cut A'}}
    if isinstance(extra, dict):
        for k, v in extra.items():
            stim.setdefault(k, v)
    return stim


# ------------------------------------------------------------ scorecard
def scorecard(twin: dict, roster: list, beats: list, dial: list, events: list,
              seat_dials: dict, subject: str, media_key: str) -> dict:
    """The dial reads: the dial average, favorite moment, dead spot,
    notes, people. The battery is attached by prepare_watch."""
    if not dial:
        dial = [50.0]
    tail_n = max(3, int(len(dial) * 0.22))
    overall = sum(dial[-tail_n:]) / float(tail_n)
    overall = _messy_bp(subject, 'score', 'like', overall)
    checkouts = sum(1 for e in events if e.get('type') == 'check-out')
    loves = sum(1 for e in events if e.get('type') in ('love', 'wow', 'laugh'))
    fav = None
    if events:
        loveish = [e for e in events if e.get('type') in ('love', 'wow', 'laugh', 'lean-in')]
        pool = loveish or events
        fav = max(pool, key=lambda e: float(e.get('intensity') or 0) * 10 + float(e.get('t') or 0) * 0.001)
    n = len(dial)
    lo_edge, hi_edge = int(n * 0.04), max(1, n - 1)    # the first seconds and the last beat are not reads
    body = list(range(lo_edge, hi_edge)) or list(range(n))
    peak_i = max(body, key=lambda i: dial[i])
    valleys = [p for p in probe_points(beats, dial, events, subject) if p.get('kind') == 'valley']
    if valleys:
        deepest = max(valleys, key=lambda p: float(p.get('depth') or 0))
        valley_i = next((i for i, b in enumerate(beats) if b.get('t') == deepest.get('t')), min(body, key=lambda i: dial[i]))
    else:
        valley_i = min(body, key=lambda i: dial[i])
    fav_t = float((fav or {}).get('t') if fav else beats[peak_i]['t'] if peak_i < len(beats) else 0)
    fav_line = str((fav or {}).get('line') or 'The room peaked here.')
    vb = beats[valley_i] if valley_i < len(beats) else {}
    dead_on = str(vb.get('caption') or '')
    lost_near = [e for e in events if e.get('beat') is not None and abs(int(e['beat']) - valley_i) <= max(2, len(dial) // 24) and e.get('type') in ('confuse', 'check-out')]
    dead_line = (lost_near[0].get('line') if lost_near else ('The room dipped here' + (f": {dead_on[0].lower() + dead_on[1:]}" if dead_on else '. A few seats looked away.')))
    dead = {'t': vb.get('t', 0), 'clock': _clock(vb.get('t', 0)), 'line': str(dead_line)[:200], 'dial': dial[valley_i], 'on_screen': dead_on[:140]}
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
        'favorite_moment': {
            't': fav_t, 'clock': _clock(fav_t), 'line': fav_line,
            'type': (fav or {}).get('type') or 'love',
            'name': (fav or {}).get('name') or '',
            'on_screen': str((beats[peak_i].get('caption') if peak_i < len(beats) else '') or '')[:140],
        },
        'dead_spot': dead,
        'notes': notes[:4],
        'people': people,
        'events_n': len(events),
        'loves': loves,
        'checkouts': checkouts,
    }


def _seat_summary(seat_no: int, series: list, beats: list, scenes_by_id: dict, events: list, acts: int = 6) -> dict:
    if not series:
        return {}
    tail = max(2, int(len(series) * 0.25))
    hi = max(range(len(series)), key=lambda i: series[i])
    lo = min(range(len(series)), key=lambda i: series[i])
    mine = [e for e in events if int(e.get('seat') or -1) == seat_no]
    by_type = {}
    for e in mine:
        by_type[e['type']] = by_type.get(e['type'], 0) + 1

    def at(i):
        b = beats[i] if i < len(beats) else {}
        return {'clock': _clock(b.get('t')), 'on_screen': str(b.get('caption') or b.get('action') or '')[:120], 'scene_id': b.get('scene_id')}
    return {'mean': round(sum(series) / len(series), 1), 'end': round(sum(series[-tail:]) / tail, 1), 'peak': at(hi), 'valley': at(lo),
            'events': by_type, 'said': [str(e.get('line') or '')[:120] for e in mine if e.get('source') == 'watch'][:4]}


def _roster_sig(roster: list) -> str:
    return hashlib.sha1('|'.join(f"{r.get('seat')}:{r.get('name')}:{r.get('age')}:{r.get('gender')}" for r in roster).encode('utf-8')).hexdigest()[:12]


def prepare_watch(session: dict, twins_by_key: dict, *, duration_s: float = 0,
                  title: str = '', notes: str = '', transcript: Optional[list] = None,
                  analysis: Optional[dict] = None, models=None, usage_extras: Optional[dict] = None,
                  progress: Optional[Callable] = None, s3=None) -> dict:
    """Fill scenes, beats, per-seat dials (prior + persona read), events,
    aggregate, splits, the scorecard with its battery and norms, the
    probes and the guide. ``analysis`` is what came off the file
    (digital_twin_cut.analyze_cut plus captions); without it the clock
    beat sheet runs. Idempotent for the same media hash and roster: the
    persona read is cached on the session and never re-billed."""
    tell = (lambda m: progress(m)) if progress else (lambda m: None)
    media = session.setdefault('media', {})
    title = str(title or media.get('title') or session.get('title') or 'the cut')[:160]
    an = analysis or {}
    dur = float(duration_s or an.get('runtime_s') or media.get('runtime_s') or 0) or 60.0
    dur = clamp(dur, MIN_RUNTIME_S, MAX_RUNTIME_S)
    media['title'] = title
    media['runtime_s'] = round(dur, 3)
    media['hash'] = media.get('hash') or media_hash(title, dur, media.get('filename') or '', notes)
    session['status'] = 'preparing'
    session['prepare_note'] = 'Preparing the cut'
    session.setdefault('mode', 'pilot')
    session.setdefault('cut_label', 'Cut A')
    session.setdefault('lineage', None)
    session.setdefault('rough_cut', False)
    session.setdefault('cast', [])
    session.setdefault('comps', [])
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
    segments = (session.get('group') or {}).get('segments') or []
    cards = {int(r['seat']): _card_for(twin, r, seed) for r in roster}
    for r in roster:   # the stage on the seat, so splits and the page can read it
        r['lifecycle_stage'] = cards[int(r['seat'])].get('lifecycle_stage') or r.get('lifecycle_stage') or ''
    transcript = transcript or an.get('transcript') or session.get('transcript')
    if an.get('transcript') and not session.get('transcript'):
        session['transcript'] = an['transcript']
    file_scenes = an.get('scenes') or None
    captions = an.get('captions') or {}
    silences = an.get('silences')
    # the watch key: the file plus what came off it (a re-prepare of the same file hits the cache)
    sig = hashlib.sha1(json.dumps({'h': media['hash'], 'n': len(file_scenes or []), 'c': len(captions), 't': len(transcript or []),
                                   'first': [round(float(s.get('t_end') or 0), 1) for s in (file_scenes or [])[:12]]}, sort_keys=True).encode('utf-8')).hexdigest()[:16]
    media_key = f"{media['hash']}|{sig}"
    tell('laying out the beat sheet')
    scenes = build_scenes(title, dur, media_key, scenes=file_scenes, captions=captions, transcript=transcript, silences=silences, notes=notes)
    if not session.get('genre_set_by_user'):
        session['genre'] = norms_genre(title, notes, votes=an.get('genre_votes')) or str(session.get('genre') or '')
    fmt = format_for(session)
    if not session.get('mode_set_by_user') and an.get('format_votes'):
        try:
            from migration.digital_twin_watch import genre_from_votes
            fv = genre_from_votes(an['format_votes'], '')
            if fv in ('feature', 'trailer') and fmt == 'pilot' and fv == 'feature' and dur >= 50 * 60:
                fmt = 'feature'
        except Exception:
            pass
    session['format'] = fmt
    beats = build_beats_from_scenes(scenes, dur, media_key, genre=_genre(title, notes))
    if len(beats) < 8:
        beats = build_beats(title, dur, media_key, notes=notes, transcript=transcript, scenes=file_scenes, captions=captions, silences=silences)
    scenes_by_id = {int(s['id']): s for s in scenes}
    grounded = any(s.get('grounded') for s in scenes)
    source = 'file' if grounded else 'clock'
    # the prior: every seat's trait curve
    tell('the room takes its seats')
    prior = {}
    for seat in roster:
        prior[str(seat.get('seat'))] = seat_dial(twin, seat, beats, seed, media_key)
    # the persona read: cached per media key + roster; only when the sheet carries the cut
    watch = session.get('watch') if isinstance(session.get('watch'), dict) else {}
    rsig = _roster_sig(roster)
    cached = bool(watch) and watch.get('media_key') == media_key and watch.get('roster_sig') == rsig and isinstance(watch.get('by_seat'), dict) and watch.get('by_seat')
    read = {}
    act_notes = {}
    failed_acts = []
    persona_on = False
    if cached:
        read = {int(k): {int(sk): sv for sk, sv in v.items()} for k, v in watch['by_seat'].items()}
        act_notes = watch.get('act_notes') or {}
        failed_acts = watch.get('failed_acts') or []
        persona_on = bool(read)
    elif grounded:
        tell('the room is watching')
        try:
            from migration import digital_twin_watch as W
            starts = {}
            for seat in roster:
                sno = int(seat['seat'])
                series = prior[str(sno)]
                per_act = {}
                for i, b in enumerate(beats):
                    per_act.setdefault(b.get('act'), series[i] if i < len(series) else 50.0)
                starts[sno] = per_act
            got = W.persona_watch(twin, roster, scenes, cards=cards, prior_start=starts, models=models, session_seed=seed, usage_extras=usage_extras,
                                  meta={'title': title, 'genre': session.get('genre') or '', 'notes': notes, 'rough_cut': bool(session.get('rough_cut'))}, duration_s=dur)
            read = got.get('by_seat') or {}
            act_notes = got.get('act_notes') or {}
            failed_acts = got.get('failed_acts') or []
            persona_on = bool(read)
        except Exception as e:
            print(f"[pilot] persona watch skipped: {e}")
            read, persona_on = {}, False
    session['watch'] = {'media_key': media_key, 'roster_sig': rsig, 'source': source, 'persona': persona_on, 'by_seat': {str(k): {str(sk): sv for sk, sv in v.items()} for k, v in read.items()},
                        'act_notes': {str(k): v for k, v in act_notes.items()}, 'failed_acts': failed_acts, 'captions_n': len(captions), 'scenes_n': len(scenes),
                        'tools': an.get('tools') or {}, 'notes': list(an.get('notes') or []), 'at': _now(), 'version': PILOT_VERSION}
    # the dials: prior moved by the persona read
    seat_dials = {}
    events = []
    for seat in roster:
        sno = int(seat['seat'])
        name = str(seat.get('name') or sno)
        levels = {sid: row.get('dial') for sid, row in (read.get(sno) or {}).items() if isinstance(row, dict) and row.get('dial') is not None}
        series = blend_dial(prior[str(sno)], beats, levels, subject, name, media_key, f"{seed}|seat{sno}") if levels else prior[str(sno)]
        seat_dials[str(sno)] = series
        if read.get(sno):
            evs = persona_events(seat, cards[sno], beats, series, read[sno], scenes_by_id, seed, media_key)
            if not evs:
                evs = seat_events(twin, seat, beats, series, seed, media_key)[:2]
            events.extend(evs)
        else:
            events.extend(seat_events(twin, seat, beats, series, seed, media_key))
    events.sort(key=lambda e: (float(e.get('t') or 0), str(e.get('name') or ''), str(e.get('type') or '')))
    agg = aggregate_dial(list(seat_dials.values()), subject, media_key)
    splits = all_splits(roster, seat_dials, twin, subject, media_key)
    session['scenes'] = [{k: s.get(k) for k in ('id', 't_start', 't_end', 'act', 'caption', 'dialogue', 'speaker', 'tone', 'joke', 'silence', 'cue', 'setting')} for s in scenes]
    session['beats'] = [{'i': b['i'], 't': b['t'], 't_start': b['t_start'], 't_end': b['t_end'], 'scene_id': b['scene_id'], 'act': b['act'], 'action': b.get('action') or '',
                         'caption': b.get('caption') or '', 'tone': b['tone'], 'dialogue': b.get('dialogue') or '', 'speaker': b.get('speaker') or '', 'cue': b.get('cue')} for b in beats]
    session['dial'] = {'t': [b['t'] for b in beats], 'value': agg, 'splits': splits}
    session['seat_dials'] = seat_dials
    session['events'] = events
    session['voices_live'] = live_voices(events, roster)
    # the scorecard: dial reads plus the battery, published against the norms
    tell('the room is scoring')
    sc = scorecard(twin, roster, beats, agg, events, seat_dials, subject, media_key)
    answers_in, why_in = {}, {}
    cached_card = (watch.get('card') if cached and isinstance(watch.get('card'), dict) else None)
    if cached_card and cached_card.get('format') == fmt and cached_card.get('answers'):
        answers_in = {int(k): v for k, v in cached_card['answers'].items()}
        why_in = {int(k): v for k, v in (cached_card.get('why') or {}).items()}
    elif persona_on:
        try:
            from migration import digital_twin_watch as W
            summaries = {int(r['seat']): _seat_summary(int(r['seat']), seat_dials[str(r['seat'])], beats, scenes_by_id, events) for r in roster}
            bat = W.battery(twin, roster, summaries, fmt=fmt, cards=cards, models=models, session_seed=seed, usage_extras=usage_extras,
                            meta={'title': title, 'genre': session.get('genre') or '', 'runtime_s': dur, 'rough_cut': bool(session.get('rough_cut'))})
            answers_in = bat.get('answers') or {}
            why_in = bat.get('why') or {}
        except Exception as e:
            print(f"[pilot] battery card skipped: {e}")
    if answers_in:
        session['watch']['card'] = {'format': fmt, 'answers': {str(k): v for k, v in answers_in.items()}, 'why': {str(k): v for k, v in why_in.items()}}
    sc['battery'] = battery_scorecard(twin, roster, segments, seat_dials, beats, scenes, events, fmt=fmt, subject=subject, media_key=media_key,
                                      session_seed=seed, cards=cards, answers_in=answers_in, why_in=why_in)
    head = sc['battery']['headline']
    sc['recommend_pct'] = head.get('definitely_recommend_pct')     # older readers: the money number
    sc['finish_intent_pct'] = head.get('finish_pct')
    sc['format'] = fmt
    sc['genre'] = session.get('genre') or ''
    sc['watch_source'] = source
    attach_norms(sc, fmt, session.get('genre') or 'all', s3=s3)
    session['scorecard'] = sc
    session['probes'] = probe_points(beats, agg, events, subject)
    guide_doc = None
    old_guide = session.get('guide') if isinstance(session.get('guide'), dict) else None
    if cached and old_guide and old_guide.get('source') == 'reasoned' and old_guide.get('questions'):
        guide_doc = old_guide
    elif persona_on or grounded:
        try:
            from migration import digital_twin_watch as W
            g = W.guide(scenes, meta={'title': title, 'genre': session.get('genre') or '', 'format': fmt}, models=models, duration_s=dur, usage_extras=usage_extras)
            if g.get('questions'):
                guide_doc = {'questions': g['questions'], 'battery': _standard_battery_lines(fmt), 'source': 'reasoned'}
        except Exception as e:
            print(f"[pilot] guide skipped: {e}")
    session['guide'] = guide_doc or fallback_guide(scenes, dur, fmt)
    session['status'] = 'ready'
    session['prepare_note'] = 'Ready to play'
    session['updated_at'] = _now()
    session['version'] = VERSION
    session['pilot_version'] = PILOT_VERSION
    return session


def open_session(twins: list, *, segments: Optional[list] = None, in_room: int = DEFAULT_IN_ROOM,
                 title: str = '', created_by: str = '', session_id: str = '',
                 media: Optional[dict] = None, mode: str = 'pilot') -> dict:
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
    mode = str(mode or 'pilot').lower()
    return {
        'id': sid, 'kind': 'pilot',
        'title': name or f"Pilot test: {names}",
        'group': group, 'questions': [],
        'media': media, 'status': 'draft',
        'mode': mode if mode in MODES else 'pilot', 'genre': '', 'cut_label': 'Cut A', 'lineage': None, 'rough_cut': False,
        'cast': [], 'comps': [], 'purge_media_after': False,
        'beats': [], 'scenes': [], 'dial': {'t': [], 'value': [], 'splits': {}},
        'seat_dials': {}, 'events': [], 'voices_live': [], 'probes': [], 'guide': None, 'watch': {},
        'scorecard': None, 'play_charged': False, 'questions_paid': 0,
        'created_by': created_by or 'prometheus',
        'created_at': _now(), 'updated_at': _now(), 'version': VERSION, 'pilot_version': PILOT_VERSION,
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
    sess['stopped_at'] = _now()
    sess['updated_at'] = _now()
    return sess


def rename_session(sess: dict, title: str) -> str:
    return _rename_session(sess, title)


def set_options(sess: dict, body: dict) -> dict:
    """The per-test switches a PATCH may set: genre (a chip the reader
    edits), mode, cut_label, rough_cut, purge_media_after. Returns what
    changed."""
    changed = {}
    if not isinstance(body, dict):
        return changed
    if 'genre' in body:
        from migration.digital_twin_norms import _genre as _ng
        g = str(body.get('genre') or '').strip().lower()
        g = _ng(g) if g else ''
        g = '' if g == 'all' else g
        if g != str(sess.get('genre') or ''):
            sess['genre'] = g
            sess['genre_set_by_user'] = bool(g)
            changed['genre'] = g
            if sess.get('scorecard'):
                try:
                    sess['scorecard']['genre'] = g
                    attach_norms(sess['scorecard'], str(sess['scorecard'].get('format') or format_for(sess)), g or 'all')
                except Exception as e:
                    print(f"[pilot] norms refresh skipped: {e}")
    if 'mode' in body:
        m = str(body.get('mode') or 'pilot').strip().lower()
        if m in MODES and m != sess.get('mode'):
            sess['mode'] = m
            sess['mode_set_by_user'] = True
            changed['mode'] = m
    if 'cut_label' in body:
        cl = ' '.join(str(body.get('cut_label') or '').split())[:40]
        if cl and cl != sess.get('cut_label'):
            sess['cut_label'] = cl
            changed['cut_label'] = cl
    if 'rough_cut' in body:
        v = bool(body.get('rough_cut'))
        if v != bool(sess.get('rough_cut')):
            sess['rough_cut'] = v
            changed['rough_cut'] = v
    if 'purge_media_after' in body:
        v = bool(body.get('purge_media_after'))
        if v != bool(sess.get('purge_media_after')):
            sess['purge_media_after'] = v
            changed['purge_media_after'] = v
    if changed:
        sess['updated_at'] = _now()
    return changed


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


def can_view_media(sess: dict, username: str, is_admin: bool = False) -> bool:
    """The cut streams only to the person who uploaded it (or an admin).
    Everyone else on the twin sees the read, never the file."""
    return can_delete(sess, username, is_admin)


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


def purge_media(s3, sess: dict, reason: str = 'done') -> bool:
    """Remove the uploaded file; keep the read, beats, dial, events and
    transcript. Never raises."""
    media = (sess or {}).get('media') or {}
    mk = media.get('s3_key')
    if not mk:
        return False
    ok = True
    if s3 is not None:
        try:
            s3.delete_object(Bucket='dashboard-inputs', Key=mk)
        except Exception as e:
            print(f"[pilot] media purge failed: {e}")
            ok = False
    if ok:
        media['purged_at'] = _now()
        media['purged_reason'] = str(reason or 'done')[:40]
        media['s3_key'] = ''
        sess['updated_at'] = _now()
    return ok


def _move_media_to_undo(s3, sess: dict, undo_key: str) -> str:
    """On delete the file follows the test into the 30-day undo copy:
    a server-side copy next to the record, then the live object goes."""
    media = (sess or {}).get('media') or {}
    mk = media.get('s3_key')
    if not mk or s3 is None:
        return ''
    dest = undo_key[:-5] + '/media/' + os.path.basename(mk) if undo_key.endswith('.json') else undo_key + '.media'
    try:
        s3.copy_object(Bucket='dashboard-inputs', CopySource={'Bucket': 'dashboard-inputs', 'Key': mk}, Key=dest, MetadataDirective='COPY')
        s3.delete_object(Bucket='dashboard-inputs', Key=mk)
        media['s3_key'] = ''
        media['undo_key'] = dest
        return dest
    except Exception as e:
        print(f"[pilot] media undo copy skipped (file left in place): {e}")
        return ''


def delete_session(s3, twin_key: str, payload: dict, sid: str, deleted_by: str = '') -> Optional[dict]:
    sess = remove_session(payload, sid)
    if sess is None:
        return None
    undo_key = deleted_pilot_key(twin_key, sid)
    media_undo = _move_media_to_undo(s3, sess, undo_key)
    rec = {
        'twin_key': twin_key, 'session_id': sess.get('id'),
        'deleted_by': deleted_by or '', 'deleted_at': _now(),
        'undo_until': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + DELETED_PILOT_KEEP_DAYS * 86400)),
        'media_undo_key': media_undo,
        'session': sess,
    }
    if s3 is not None:
        try:
            s3.put_object(
                Bucket='dashboard-inputs', Key=undo_key,
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


def signed_media_url(s3, sess: dict, expires_s: int = MEDIA_URL_EXPIRES_S) -> Optional[dict]:
    """A short-lived signed link to the cut. {url, expires_in, expires_at}
    or None when there is no file (or no client)."""
    media = (sess or {}).get('media') or {}
    mk = media.get('s3_key')
    if not mk or s3 is None:
        return None
    try:
        url = s3.generate_presigned_url('get_object', Params={'Bucket': 'dashboard-inputs', 'Key': mk, 'ResponseContentType': media.get('content_type') or 'video/mp4',
                                                               'ResponseContentDisposition': 'inline'}, ExpiresIn=int(expires_s))
    except Exception as e:
        print(f"[pilot] signed link failed: {e}")
        return None
    now = time.time()
    return {'url': url, 'expires_in': int(expires_s), 'expires_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now + expires_s)), 'issued_at': now}


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
            out.append({'t': float(seg.get('start') or 0), 'end': float(seg.get('end') or 0), 'text': str(seg.get('text') or '').strip()[:180]})
        except (TypeError, ValueError):
            continue
    return out or None


def process_local_cut(path: str, title: str = '') -> dict:
    """Duration plus optional transcript (the quick pass on upload; the
    full read of the file runs on the worker). Fail-safe: duration from
    the file name is never invented; the caller supplies a stated
    runtime when probe is missing."""
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
    replay can paint per-seat ticks. No internals: the file's storage
    key never travels (the page asks for a signed link), the persona
    read cache stays server-side, the norms carry no sources."""
    if not sess:
        return {}
    out = dict(sess)
    media = dict(out.get('media') or {})
    media['has_file'] = bool(media.get('s3_key'))
    media.pop('s3_key', None)
    media.pop('undo_key', None)
    out['media'] = media
    w = out.get('watch') if isinstance(out.get('watch'), dict) else {}
    out['watch'] = {'source': w.get('source') or 'clock', 'persona': bool(w.get('persona')), 'scenes_n': w.get('scenes_n'), 'captions_n': w.get('captions_n')}
    out.pop('transcript_raw', None)
    return out


def session_csv(session: dict) -> str:
    import csv
    import io
    from migration.digital_twin_norms import METRIC_LABEL
    buf = io.StringIO()
    w = csv.writer(buf)
    sc = session.get('scorecard') or {}
    bat = sc.get('battery') or {}
    head = bat.get('headline') or {}
    norms = sc.get('norm_columns') or {}
    genre = str(session.get('genre') or '') or 'all'
    w.writerow(['Test', 'Title', 'Cut', 'Format', 'Genre', 'Runtime s', 'People', 'Dial average', 'Excellent %', 'Total positive %', 'Definitely recommend %',
                'Would finish %', 'Favorite moment', 'Dead spot'])
    fav = sc.get('favorite_moment') or {}
    dead = sc.get('dead_spot') or {}
    w.writerow([
        session.get('id'), session.get('title'), session.get('cut_label') or 'Cut A', sc.get('format') or session.get('format') or '', genre,
        (session.get('media') or {}).get('runtime_s'), sc.get('people'), sc.get('overall_like'), head.get('excellent_pct'), head.get('total_positive_pct'),
        head.get('definitely_recommend_pct'), head.get('finish_pct'),
        f"{fav.get('clock')} {fav.get('line')}", f"{dead.get('clock')} {dead.get('line')}",
    ])
    w.writerow([])
    w.writerow(['Question', 'Answer', 'Share %', 'People', 'In the room', 'Norm %', f"{genre.title()} norm %"])
    metric_for = {'overall': {'a': 'excellent'}, 'recommend': {'a': 'definitely_recommend'}, 'finish': {'a': 'finish'}, 'tune_in': {'a': 'tune_in'},
                  'urgency': {'a': 'urgency_opening'}, 'urgency_pilot': {'a': 'urgency_opening'}}
    for q in bat.get('questions') or []:
        for r in q.get('rows') or []:
            m = (metric_for.get(q.get('id')) or {}).get(r.get('id'))
            nc = norms.get(m) if m else None
            w.writerow([q.get('label'), r.get('label'), r.get('pct'), r.get('people'), r.get('tally'), (nc or {}).get('norm') if nc else '', (nc or {}).get('genre_norm') if nc else ''])
        if q.get('id') == 'overall':
            nc = norms.get('total_positive') or {}
            w.writerow([q.get('label'), 'Total positive', q.get('total_positive_pct'), q.get('total_positive_people'), '', nc.get('norm'), nc.get('genre_norm')])
    w.writerow([])
    w.writerow(['Quadrant', 'Seats', 'People', 'Excellent %', 'Total positive %', 'Definitely recommend %', 'Would finish %'])
    for qd in bat.get('quadrants') or []:
        w.writerow([qd.get('label'), qd.get('n'), qd.get('people'), qd.get('excellent_pct'), qd.get('total_positive_pct'), qd.get('definitely_recommend_pct'), qd.get('finish_pct')])
    nrm = sc.get('norms') or {}
    if nrm:
        w.writerow(['Norm', '', '', (nrm.get('all') or {}).get('excellent', {}).get('value'), (nrm.get('all') or {}).get('total_positive', {}).get('value'),
                    (nrm.get('all') or {}).get('definitely_recommend', {}).get('value'), (nrm.get('all') or {}).get('finish', {}).get('value')])
        if genre != 'all':
            w.writerow([f"{genre.title()} norm", '', '', (nrm.get('genre') or {}).get('excellent', {}).get('value'), (nrm.get('genre') or {}).get('total_positive', {}).get('value'),
                        (nrm.get('genre') or {}).get('definitely_recommend', {}).get('value'), (nrm.get('genre') or {}).get('finish', {}).get('value')])
    w.writerow([])
    dial = session.get('dial') or {}
    splits = dial.get('splits') or {}
    keys = list(splits.keys())
    w.writerow(['Clock', 'Seconds', 'On screen', 'Dial'] + [splits[k].get('label') or k for k in keys])
    beats = session.get('beats') or []
    for i, t in enumerate(dial.get('t') or []):
        val = (dial.get('value') or [None])[i] if i < len(dial.get('value') or []) else None
        on = (beats[i].get('caption') or beats[i].get('action') or '') if i < len(beats) else ''
        w.writerow([_clock(t), t, on, val] + [(splits[k].get('value') or [])[i] if i < len(splits[k].get('value') or []) else '' for k in keys])
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
    g = session.get('guide') or {}
    if g.get('questions'):
        w.writerow([])
        w.writerow(['Guide clock', 'Suggested question', 'Kind', 'Why there'])
        for q in g['questions']:
            w.writerow([q.get('clock'), q.get('q'), q.get('kind'), q.get('why')])
    return buf.getvalue()


def session_lines(session: dict) -> list:
    sc = session.get('scorecard') or {}
    head = ((sc.get('battery') or {}).get('headline')) or {}
    fav = sc.get('favorite_moment') or {}
    dead = sc.get('dead_spot') or {}
    out = [str(session.get('title') or 'Pilot test')]
    if head.get('total_positive_pct') is not None:
        out.append(f"Excellent {pct1(head.get('excellent_pct'))}%, total positive {pct1(head.get('total_positive_pct'))}%, definitely recommend {pct1(head.get('definitely_recommend_pct'))}%"
                   + (f", would finish {pct1(head.get('finish_pct'))}%." if head.get('finish_pct') is not None else '.'))
    else:
        out.append(f"Dial average {pct1(sc.get('overall_like'))}.")
    for l in (sc.get('norm_lines') or [])[:2]:
        out.append(l)
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
                's3://', 'agents', 'em dash', 'panel-projected'):
        if tok in low:
            hits.append(tok)
    if '\u2014' in str(s or '') or '\u2013' in str(s or ''):
        hits.append('dash')
    return hits


__all__ = [
    'EVENT_TYPES', 'EVENT_LABEL', 'KINDS', 'STATUS', 'MODES', 'MEDIA_PREFIX', 'PILOT_VERSION',
    'DELETED_PILOT_PREFIX', 'DELETED_PILOT_KEEP_DAYS', 'QUESTIONS_INCLUDED', 'WINDOW_BACK_S', 'MEDIA_URL_EXPIRES_S', 'PRIOR_WEIGHT',
    'QUADRANTS', 'PHASE2_PRICE_NOTES',
    'open_session', 'prepare_watch', 'find_reusable_session', 'charge_due',
    'mark_play_paid', 'mark_paid', 'mark_playing', 'stop_session', 'rename_session', 'set_options',
    'stamp_question', 'can_delete', 'can_view_media', 'remove_session', 'delete_session', 'purge_media',
    'deleted_pilot_key', 'media_hash', 'build_beats', 'build_scenes', 'build_beats_from_scenes', 'seat_dial', 'blend_dial', 'seat_events', 'persona_events',
    'aggregate_dial', 'split_dial', 'split_specs', 'all_splits', 'quadrant_of', 'scorecard', 'battery_scorecard', 'norms_for', 'norm_metrics', 'record_norms',
    'attach_norms', 'probe_points', 'fallback_guide', 'question_window', 'seat_window', 'ask_stimulus', 'format_for', 'norms_genre',
    'session_csv', 'session_lines', 'public_session', 'signed_media_url',
    'media_s3_key', 'accept_upload_name', 'ffmpeg_available', 'probe_duration',
    'process_local_cut', 'has_banned_copy', 'beat_step',
]
