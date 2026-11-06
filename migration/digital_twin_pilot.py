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
subjects never pin the same dial. Counts keep messy last digits. A test
is one group, charged as it opens at the company's plan rate for its tier
(migration/digital_twin_pricing, 2026-11-05). User-facing copy never
names how the room is produced.
"""
from __future__ import annotations

import copy
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
    VERSION, clamp, human_people, jit, messy_count, natural_count, pct1, unit,
)
from migration.digital_twin_panel import (
    DEFAULT_IN_ROOM, MAX_IN_ROOM, build_group, clean_room_title, copy_name,
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
QUESTIONS_INCLUDED = 12          # the legacy block (pre 2026-11-05 sessions); a group's cap is its tier (digital_twin_pricing)
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

# Pricing (Jenna 2026-11-05): a test is one GROUP, charged as it opens at the company's plan rate for its tier (1-20
# or 1-40 questions on the clock), the same card as a Focus Group IQ room; see migration/digital_twin_pricing.py. The
# per-mode lines of 2026-10-28 (trailer, title, key art, concept, script, recut) are retired; the keys below only
# label old session records.
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
# the TV norms genres words can name (Jenna 2026-11-04): a procedural is read first (NCIS: New York is a cop show before it is a
# drama or a thriller); 'drama' is read last, once nothing more specific matched
_PROCEDURAL = re.compile(r"\b(procedurals?|cops?|cop show|detectives?|precinct|ncis|csi|fbi|law\s*&\s*order|law and order|homicide|forensics?|police drama|police procedural)\b", re.I)
_DRAMA = re.compile(r"\b(dramas?|dramatic series|prestige)\b", re.I)


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
    """The norms genre the system suggests: the room's own read of the
    frames when it has one, else the words in the title and notes, else
    '' (the all-genres column). The user's own choice on the test
    (``genre_set_by_user``) always wins over this (set_norm)."""
    if votes:
        try:
            from migration.digital_twin_watch import genre_from_votes
            g = genre_from_votes(votes, '')
            if g:
                return g
        except Exception:
            pass
    return keyword_genre(title, notes)


def keyword_genre(title: str = '', notes: str = '') -> str:
    """The norms genre the words in a title and notes suggest ('' when
    they name none). Procedural first, drama last."""
    blob = f"{title or ''} {notes or ''}"
    if _PROCEDURAL.search(blob):
        return 'procedural'
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
    if _DRAMA.search(blob):
        return 'drama'
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
                     'setting': str(use.get('setting') or '')[:40], 'grounded': bool(cap) or bool(dialogue),
                     'cast': [str(x) for x in (cap.get('cast') or []) if str(x or '').strip()][:8]})   # the cast on screen, by face (digital_twin_cast)
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


def cohort_labels(roster: list) -> list:
    """The cohort names in seat order when the room was cut into cohorts
    (two or more segments), else []."""
    seen = []
    for r in roster or []:
        lab = str(r.get('segment') or '').strip()
        if lab and lab not in seen:
            seen.append(lab)
    return seen if len(seen) > 1 else []


def cohort_key(label: str) -> str:
    return 'cohort_' + (re.sub(r"[^0-9a-z]+", '_', str(label or '').lower()).strip('_') or 'x')


def split_specs(roster: list, twin: dict) -> list:
    """[(key, label, group, predicate)] for every split the room can
    make from what the seats carry. The cohorts the room was cut into
    come first (Jenna, 2026-11-02)."""
    specs = []
    for lab in cohort_labels(roster):
        specs.append((cohort_key(lab), lab, 'cohort', (lambda s, ll=lab: str(s.get('segment') or '') == ll)))
    specs += [
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
    if mode in ('feature', 'episode', 'trailer', 'title', 'key_art', 'concept'):
        return mode
    if mode in ('spot',):
        return 'trailer'
    if mode in ('binge',):
        return 'episode'
    if mode == 'script':
        # a recorded table read runs as the thing it is (a pilot or a feature); the text read keeps its own battery
        return 'script' if not ((session or {}).get('media') or {}).get('s3_key') else ('feature' if float(((session or {}).get('media') or {}).get('runtime_s') or 0) >= 65 * 60 else 'pilot')
    dur = float(((session or {}).get('media') or {}).get('runtime_s') or 0)
    if dur >= 65 * 60:
        return 'feature'
    if 0 < dur <= 240:
        return 'trailer'
    return 'pilot'


def _seat_people(roster: list, segments: list) -> dict:
    """{seat: people}: the seat's own people when the room was built with
    them (each seat stands for its share of its segment), else the
    segment's people spread over its seats."""
    seg_people = {str(s.get('label')): int(s.get('people') or 0) for s in segments or []}
    seg_n = {}
    for r in roster:
        seg_n[str(r.get('segment'))] = seg_n.get(str(r.get('segment')), 0) + 1
    out = {}
    for r in roster:
        lab = str(r.get('segment'))
        own = _int_or_zero(r.get('people'))
        out[int(r['seat'])] = own if own > 0 else int(round(seg_people.get(lab, 0) / float(seg_n.get(lab) or 1)))
    return out


def _int_or_zero(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def room_people(roster: list, segments: list, twin: Optional[dict] = None, *, quadrant_total=None) -> Optional[int]:
    """The people the seated room stands for, from the first source that
    carries them (2026-11-04: the NCIS: New York Pilot card read '12.7K
    behind them', sixteen seats times a stand-in 800, next to 22.35M on
    the quadrants): the segments' people, else the seats' own people,
    else the whole-room total the battery published, else the twin's US
    size. None when no source carries one; a count is never made up
    from the seat count."""
    segs = sum(_int_or_zero(s.get('people')) for s in (segments or []) if isinstance(s, dict))
    if segs > 0:
        return segs
    seats = sum(_int_or_zero(r.get('people')) for r in (roster or []) if isinstance(r, dict))
    if seats > 0:
        return seats
    if _int_or_zero(quadrant_total) > 0:
        return _int_or_zero(quadrant_total)
    size = _int_or_zero(((twin or {}).get('meta') or {}).get('size_us'))
    return size if size > 0 else None


def session_people(session: dict, twin: Optional[dict] = None) -> Optional[int]:
    """``room_people`` for a saved test: the scorecard page, the room page,
    the CSV and the API all read this one figure, so they agree. A room
    sized to its cuts (``group.sizing``, Jenna 2026-11-05) stands for the
    represented people first; else the battery's whole-room quadrant is
    the published total when the group carries no people of its own."""
    g = (session or {}).get('group') or {}
    sized = _int_or_zero(((g.get('sizing') or {}) if isinstance(g.get('sizing'), dict) else {}).get('people'))
    carried = any(_int_or_zero(x.get('people')) > 0 for x in list(g.get('segments') or []) + list(g.get('roster') or []) if isinstance(x, dict))
    if sized > 0 and _int_or_zero(g.get('size_people')) > 0 and carried:
        return sized
    quads = (((session or {}).get('scorecard') or {}).get('battery') or {}).get('quadrants') or []
    total = next((q.get('people') for q in quads if isinstance(q, dict) and q.get('key') == 'total'), None)
    return room_people(g.get('roster') or [], g.get('segments') or [], twin, quadrant_total=total)


# ------------------------------------------------- the represented audience
# Jenna, 2026-11-05: "somewhere we need to show a total of what the audience in the focus group or panel represents if
# the audience has been cut like in the ncis pilot. it wouldn't project up to that full sample size because that sample
# size is cumulative of all people not just the cuts I made ... so the room would need to be sized accordingly."
# A room built on cuts stands for the people inside those cuts, as one union (a cut inside another cut is counted
# once), never for the whole twin and never for the cuts added up. A room with no cuts stands for the whole twin.
_ETH_VALUE = {'hispanic': 'Hispanic or Latino', 'latino': 'Hispanic or Latino', 'black': 'Black or African American', 'asian': 'Asian', 'white': 'White'}
_MAX_EXACT_CUTS = 10          # inclusion-exclusion runs exactly up to this many cuts on one twin; beyond it, pairwise overlap only


def _demo_values(key: str) -> list:
    from migration import digital_twin_demos as D
    return next((list(d['values']) for d in D.DEMOS if d['key'] == key), [])


def _buckets_overlapping(key: str, lo, hi) -> list:
    """The picker buckets of an age or income span (a legacy (lo, hi)
    filter) that meet the span."""
    from migration import digital_twin_demos as D
    bounds = D._AGE_BOUNDS if key == 'AGE' else D._INCOME_BOUNDS
    try:
        lo, hi = float(lo), float(hi)
    except (TypeError, ValueError):
        return []
    return [v for v in _demo_values(key) if v in bounds and not (bounds[v][1] < lo or bounds[v][0] > hi)]


def cut_of(segment_filter: dict) -> dict:
    """A segment's filter as one cut: ``{'demos': {KEY: set(values)},
    'scalars': {key: value}}``. The standard-demographics selection is
    the cut; a legacy filter (gender 'F', age (45, 80), ethnicity
    'hispanic', parent True, ...) is read into the same vocabulary so two
    cuts can be intersected. Place, lifecycle, party and urbanicity stay
    scalars (equal or disjoint). An empty filter is the whole twin."""
    from migration import digital_twin_demos as D
    f = dict(segment_filter or {})
    demos, scalars = {}, {}
    for key, vals in (f.get('demos') or {}).items() if isinstance(f.get('demos'), dict) else []:
        canon = [D.canon_value(str(key).upper(), v) for v in (vals or [])]
        canon = [v for v in canon if v]
        if canon:
            demos[str(key).upper()] = set(canon)
    g = str(f.get('gender') or '').upper()
    if g in ('M', 'F', 'X') and 'GENDER' not in demos:
        demos['GENDER'] = {'M': 'Male', 'F': 'Female', 'X': 'Non-Binary'}[g]
        demos['GENDER'] = {demos['GENDER']}
    if f.get('age') and 'AGE' not in demos:
        try:
            lo, hi = f['age'][0], f['age'][1]
            b = _buckets_overlapping('AGE', lo, hi)
            if b:
                demos['AGE'] = set(b)
        except (TypeError, IndexError, KeyError):
            pass
    if f.get('ethnicity') and 'ETHNICITY' not in demos:
        v = _ETH_VALUE.get(str(f['ethnicity']).lower())
        if v:
            demos['ETHNICITY'] = {v}
        else:
            scalars['ethnicity'] = str(f['ethnicity'])
    if f.get('parent') is not None and 'PARENTAL_STATUS' not in demos:
        demos['PARENTAL_STATUS'] = {'Has Children' if f['parent'] else 'No Children'}
    if str(f.get('language') or '') == 'Spanish' and 'PRIMARY_LANGUAGE' not in demos:
        demos['PRIMARY_LANGUAGE'] = {'Spanish'}
    if f.get('college') is not None and 'EDUCATION' not in demos:
        demos['EDUCATION'] = {'Bachelors Degree', 'Graduate or Professional Degree'} if f['college'] else {'High School or Less', 'Some College / Associate Degree'}
    if (f.get('income_min') or f.get('income_max')) and 'INCOME' not in demos:
        b = _buckets_overlapping('INCOME', f.get('income_min') or 0, f.get('income_max') or 10_000_000)
        if b:
            demos['INCOME'] = set(b)
    if f.get('lgbtq') and 'SEXUAL_ORIENTATION' not in demos:
        demos['SEXUAL_ORIENTATION'] = {v for v in _demo_values('SEXUAL_ORIENTATION') if v != 'Straight / Heterosexual'}
    if f.get('relationship') and 'RELATIONSHIP' not in demos:
        demos['RELATIONSHIP'] = {'Married', 'In a Relationship'} if str(f['relationship']).lower() == 'married' else {'Single', 'Divorced or Separated', 'Widowed'}
    for k in ('city', 'lifecycle', 'party', 'urbanicity'):
        if f.get(k):
            scalars[k] = str(f[k]).strip().lower()
    return {'demos': demos, 'scalars': scalars}


def intersect_cuts(a: dict, b: dict) -> Optional[dict]:
    """The people in both cuts, as a cut; None when no one can be in
    both (a dimension the two name with no value in common)."""
    demos = {}
    for key in set(a.get('demos') or {}) | set(b.get('demos') or {}):
        va, vb = (a.get('demos') or {}).get(key), (b.get('demos') or {}).get(key)
        v = (set(va) & set(vb)) if (va and vb) else set(va or vb or [])
        if not v:
            return None
        demos[key] = v
    scalars = {}
    for key in set(a.get('scalars') or {}) | set(b.get('scalars') or {}):
        va, vb = (a.get('scalars') or {}).get(key), (b.get('scalars') or {}).get(key)
        if va and vb and va != vb:
            return None
        scalars[key] = va or vb
    return {'demos': demos, 'scalars': scalars}


def cut_share(twin: dict, cut: dict) -> float:
    """The share of the twin inside a cut, from the twin's own
    demographic rows (the same read the segments were sized with)."""
    from migration.digital_twin_panel import _demos_share, _segment_share
    share = 1.0
    demos = {k: sorted(v) for k, v in (cut.get('demos') or {}).items() if v}
    if demos:
        share *= _demos_share(twin, demos)
    scalars = {k: v for k, v in (cut.get('scalars') or {}).items() if v}
    if scalars:
        share *= _segment_share(twin, scalars)
    return clamp(share, 0.0, 1.0)


def union_share(twin: dict, cuts: list, share_fn=None) -> float:
    """The share of the twin inside any of the cuts: inclusion-exclusion
    over the cuts' intersections (exact up to ten cuts, pairwise beyond),
    so a cut nested in another is counted once. ``share_fn(twin, cut)``
    reads one cut's share (the twin's own composition by default)."""
    share_fn = share_fn or cut_share
    cuts = [c for c in cuts if isinstance(c, dict)]
    if not cuts:
        return 1.0
    if any(not (c.get('demos') or c.get('scalars')) for c in cuts):
        return 1.0                      # a cut with no filter is the whole twin
    singles = [share_fn(twin, c) for c in cuts]
    if len(cuts) == 1:
        return clamp(singles[0], 0.0, 1.0)
    total = 0.0
    if len(cuts) <= _MAX_EXACT_CUTS:
        from itertools import combinations
        for k in range(1, len(cuts) + 1):
            sign = 1.0 if k % 2 == 1 else -1.0
            for idx in combinations(range(len(cuts)), k):
                inter = cuts[idx[0]]
                for j in idx[1:]:
                    inter = intersect_cuts(inter, cuts[j])
                    if inter is None:
                        break
                if inter is None:
                    continue
                total += sign * (singles[idx[0]] if k == 1 else share_fn(twin, inter))
    else:
        total = sum(singles)
        for i in range(len(cuts)):
            for j in range(i + 1, len(cuts)):
                inter = intersect_cuts(cuts[i], cuts[j])
                if inter is not None:
                    total -= share_fn(twin, inter)
    return clamp(total, max(singles) if singles else 0.0, 1.0)


def cuts_disjoint(cuts: list) -> bool:
    """True when no two cuts can share a person."""
    cuts = [c for c in cuts if isinstance(c, dict)]
    for i in range(len(cuts)):
        for j in range(i + 1, len(cuts)):
            if intersect_cuts(cuts[i], cuts[j]) is not None:
                return False
    return True


def segment_cut(segment: dict, twin: dict) -> dict:
    """The cut a saved segment stands for: its stored filter, else its
    composition or label read with the twin's own words taken out."""
    from migration.digital_twin_panel import parse_segment, strip_twin_words
    s = segment if isinstance(segment, dict) else {}
    f = dict(s.get('filter') or {})
    if not f:
        words = str(s.get('composition') or s.get('label') or '')
        f = parse_segment(strip_twin_words(words, twin)) if words else {}
    return cut_of(f)


def represented_sizing(twin, segments_or_cuts, *, seed: str = '', share_fn=None) -> dict:
    """What the chosen cuts stand for, from the twin's composition. ``twin``
    is one twin or the list of twins a room seats; the segments name
    theirs by key. Per twin: the union of its cuts (a cut's own share of
    the twin, overlaps removed, a cut with no filter is the whole twin),
    as people. Disjoint cuts add up to exactly the people the segments
    already carry; a room with no cuts stands for the whole twin.
    Returns {people, footprint, twin_people, share_of_twin, cuts_n,
    is_cut, by_twin, step}; ``people`` equals ``footprint`` here (a
    title's own audience may lower it later, see digital_twin_sizing)."""
    twins = twin if isinstance(twin, (list, tuple)) else [twin]
    twins = [t for t in twins if isinstance(t, dict)]
    by_key = {_twin_key(t): t for t in twins}
    segs = [s for s in (segments_or_cuts or []) if isinstance(s, dict)]
    groups = {}
    for s in segs:
        tkey = str(s.get('twin') or s.get('twin_key') or '')
        if tkey not in by_key and twins:
            tkey = _twin_key(twins[0]) if len(twins) == 1 else (next((k for k, t in by_key.items() if _twin_name(t).lower() in str(s.get('label') or '').lower()), _twin_key(twins[0])))
        groups.setdefault(tkey, []).append(s)
    for k in by_key:
        groups.setdefault(k, [])
    by_twin, footprint, twin_people, cuts_n = {}, 0, 0, 0
    for tkey, group in groups.items():
        t = by_key.get(tkey) or {}
        size = _int_or_zero(_meta(t).get('size_us'))
        cuts = [segment_cut(s, t) for s in group]
        real = [c for c in cuts if c.get('demos') or c.get('scalars')]
        cuts_n += len(real)
        if not group or len(real) < len(cuts) or size <= 0:
            # no segments, or a segment with no filter: the whole twin
            fp = size if size > 0 else sum(_int_or_zero(s.get('twin_people') or s.get('people')) for s in group)
            share = 1.0
        else:
            share = union_share(t, cuts, share_fn)
            carried = [_int_or_zero(s.get('twin_people') or s.get('people')) or int(round(size * cut_share(t, c))) for s, c in zip(group, cuts)]
            if cuts_disjoint(cuts):
                fp = min(size, sum(carried))           # cuts that cannot overlap add up exactly
            else:
                fp = messy_count(seed or tkey, f"footprint|{tkey}", size * share)      # a headline count, never on a round figure
                fp = int(clamp(fp, max(carried) if carried else 1, size))
        by_twin[tkey] = {'people': int(fp), 'twin_people': int(size), 'share': round(share, 6), 'cuts_n': len(real)}
        footprint += int(fp)
        twin_people += int(size)
    return {'people': int(footprint), 'footprint': int(footprint), 'twin_people': int(twin_people),
            'share_of_twin': (round(footprint / float(twin_people), 6) if twin_people > 0 else 1.0),
            'cuts_n': int(cuts_n), 'is_cut': bool(cuts_n > 0 and footprint < twin_people), 'by_twin': by_twin,
            'step': 'cuts' if (cuts_n > 0 and footprint < twin_people) else 'twin'}


def represented_people(twin, segments_or_cuts, *, seed: str = '') -> int:
    """The people the chosen cuts stand for (the union of the cuts inside
    the twin's audience); the whole twin when there are no cuts."""
    return int(represented_sizing(twin, segments_or_cuts, seed=seed).get('people') or 0)


def lower_first(text: str) -> str:
    """The first letter lowered to run on after a colon or a lead-in,
    unless the word is an acronym or a name that opens with two capitals
    ('CBS logo', 'LL Cool J', 'I moved' keep their case; 'The room' runs
    on as 'the room')."""
    s = str(text or '')
    if len(s) < 2:
        return s.lower()
    return (s[0].lower() + s[1:]) if s[1].islower() else s


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
    # the whole room's people: the segments, else the seats, else the twin's US size; never the seat count times a stand-in
    room_total = room_people(roster, segments, twin) or 0

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
    room_q, room_mean = run(all_ids, 'Whole room', room_total, 'room')
    quadrants = [{'key': 'total', 'label': 'Total', 'n': len(roster), 'people': int(room_total), **_money(room_q)}]
    seg_people = {str(sg.get('label')): int(sg.get('people') or 0) for sg in (segments or []) if isinstance(sg, dict)}
    for lab in cohort_labels(roster):   # the cohorts the room was cut into, each read on its own
        ids = [int(r['seat']) for r in roster if str(r.get('segment') or '') == lab]
        if not ids:
            continue
        ppl = int(seg_people.get(lab) or 0) or messy_count(subject, f"cohort-{lab}", sum(people_by_seat.get(i, 0) for i in ids))
        q, _m = run(ids, lab, ppl, f"cohort-{cohort_key(lab)}")
        quadrants.append({'key': cohort_key(lab), 'label': lab, 'group': 'cohort', 'n': len(ids), 'people': int(ppl), **_money(q)})
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


def norms_for(fmt: str, genre: str = 'all', s3=None, company: Optional[str] = None) -> dict:
    """The norm and genre norm columns for a format and genre (no
    sources). Public for Phase 2 (modes, a Norms tab). ``company`` reads
    the own-test side from that company's finished tests only (company
    scope, Jenna 2026-11-05); None is every company's (the staff view)."""
    from migration import digital_twin_norms as N
    try:
        return N.norms_for(fmt, genre or 'all', s3=_norms_s3(s3), company=company)
    except Exception as e:
        print(f"[pilot] norms read failed: {e}")
        return N.norms_for(fmt, genre or 'all', doc=N.load(None), company=company)


def twin_company(twin: Optional[dict]) -> str:
    """The company a twin belongs to (company scope); '' when unknown."""
    try:
        from migration import digital_twin_company as CO
        return CO.twin_company(twin, None, str(((twin or {}).get('meta') or {}).get('key') or '')) if isinstance(twin, dict) else ''
    except Exception:
        return ''


def session_company(session: Optional[dict], twin: Optional[dict] = None) -> str:
    """The company a test files its norms under: the twin's when the
    twin is at hand (stamped on the session), else the stamp."""
    co = twin_company(twin) if isinstance(twin, dict) else ''
    if co and isinstance(session, dict):
        session['company'] = co
    if not co and isinstance(session, dict):
        co = str(session.get('company') or '')
    return co


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
    # the mode read and the diagnostics add their own cells (trailer lift, title fit, characters, components, pace)
    try:
        from migration.digital_twin_modes import mode_norm_metrics
        out.update(mode_norm_metrics(session or {}))
    except Exception:
        pass
    try:
        from migration.digital_twin_diagnostics import diagnostics_norm_metrics
        out.update(diagnostics_norm_metrics(session or {}))
    except Exception:
        pass
    cr = (session or {}).get('critics') or {}
    if isinstance(cr.get('norm_metrics'), dict):
        out.update({k: float(v) for k, v in cr['norm_metrics'].items() if v is not None})
    w = (((session or {}).get('recruit') or {}).get('walkouts') or {})
    if w.get('pct') is not None:
        out['walkouts'] = float(w['pct'])
    return out


def forget_norms(session: dict, s3=None) -> Optional[dict]:
    """A deleted test leaves the ledger. Never raises."""
    try:
        from migration import digital_twin_norms as N
        sid = str((session or {}).get('id') or '')
        if not sid or not (session or {}).get('norms_recorded_at'):
            return None
        doc = N.forget_test(_norms_s3(s3), sid)
        if doc is not None:
            session.pop('norms_recorded_at', None)
        return doc
    except Exception as e:
        print(f"[pilot] norms forget skipped: {e}")
        return None


def record_norms(session: dict, s3=None, company: Optional[str] = None, twin: Optional[dict] = None) -> Optional[dict]:
    """Every stopped test adds to the ledger (format x genre x metric),
    filed under its company's own bucket as well. Idempotent per test id.
    Never raises."""
    try:
        from migration import digital_twin_norms as N
        metrics = norm_metrics(session)
        if not metrics:
            return None
        fmt = str((session.get('scorecard') or {}).get('format') or format_for(session))
        co = str(company or '') or session_company(session, twin)
        doc = N.record(_norms_s3(s3), fmt, str(session.get('genre') or 'all') or 'all', metrics, str(session.get('id') or ''), company=co)
        if doc is not None:
            session['norms_recorded_at'] = _now()
        return doc
    except Exception as e:
        print(f"[pilot] norms record skipped: {e}")
        return None


def _tally_of(tally: str) -> int:
    """'5 of 16' -> 16; 0 when the string carries no total."""
    m = re.search(r'\bof\s+(\d+)', str(tally or ''))
    return int(m.group(1)) if m else 0


def battery_tallies(bat: dict) -> dict:
    """The seat tally behind every share on the card, from the battery's
    rows: {'excellent': '1 of 16', 'total_positive': '8 of 16',
    'definitely_recommend': '5 of 16', 'finish': '16 of 16', 'tune_in':
    '13 of 16', 'urgency_opening': '6 of 16', 'better_than_expected':
    '6 of 16', 'seats': 16}. A share is of the people the seats stand
    for; the tally is the seats themselves (Liz Huszarik 2026-11-05: 16
    seats cannot produce 49.4%, so say which is which). '' for a row an
    older card never tallied."""
    out = {'seats': 0}
    seats = 0
    for q in (bat or {}).get('questions') or []:
        for r in q.get('rows') or []:
            seats = max(seats, _tally_of(r.get('tally')))
    out['seats'] = seats
    for q in (bat or {}).get('questions') or []:
        rows = q.get('rows') or []
        if not rows:
            continue
        top = rows[0]
        top_t = str(top.get('tally') or '')
        qid = q.get('id')
        if qid == 'overall':
            out['excellent'] = top_t
            if len(rows) >= 2 and top_t and str(rows[1].get('tally') or ''):
                n = int(top.get('seats_n') or 0) + int(rows[1].get('seats_n') or 0)
                out['total_positive'] = f"{n} of {_tally_of(top_t) or seats}"
            else:
                out['total_positive'] = ''
        elif qid == 'recommend':
            out['definitely_recommend'] = top_t
        elif qid == 'finish':
            out['finish'] = top_t
        elif qid == 'tune_in':
            out['tune_in'] = top_t
        elif qid in ('urgency', 'urgency_pilot'):
            out['urgency_opening'] = top_t
        elif qid == 'expectations':
            out['better_than_expected'] = top_t
        elif qid == 'full_price':
            out['full_price'] = top_t
    return out


def denominator_line(seats: int, people: Optional[int]) -> str:
    """The one plain statement of what the shares are of, for the
    scorecard page, the TLDR, the card and the CSV: 'Shares are of the
    22.35M people the 16 seats stand for; the count beside each share
    is the seats themselves.' Without a people figure the shares are of
    the audience the seats stand for."""
    try:
        n = int(seats or 0)
    except (TypeError, ValueError):
        n = 0
    seat_words = f"the {n} seats" if n else 'the seats'
    if people:
        return f"Shares are of the {human_people(people)} people {seat_words} stand for; the count beside each share is the seats themselves."
    return f"Shares are of the audience {seat_words} stand for; the count beside each share is the seats themselves."


def attach_norms(scorecard: dict, fmt: str, genre: str, s3=None, company: Optional[str] = None) -> dict:
    """The norm and genre-norm columns, the comparison lines and the
    norm choice ('Compared to all pilots', 'Compared to feature
    screenings' while the all column is a stand-in, and the honest 'No
    procedural norm yet; it fills as tests are filed under procedural, 1
    of 5 so far.') on a scorecard. ``genre`` is the norm the test is
    compared to (the user's choice when they set one); '' or 'all'
    reads the all-genres column. Every money line carries the seat tally
    beside the share; would finish gets its line (a norm, or the honest
    'no norm yet' in the same words), never a blank."""
    from migration import digital_twin_norms as N
    norms = norms_for(fmt, genre or 'all', s3=s3, company=company)
    scorecard['norms'] = norms
    bat = scorecard.get('battery') or {}
    head = bat.get('headline') or {}
    tallies = battery_tallies(bat)
    lines = []
    for metric, key in (('definitely_recommend', 'definitely_recommend_pct'), ('total_positive', 'total_positive_pct'), ('excellent', 'excellent_pct'), ('finish', 'finish_pct')):
        if head.get(key) is not None:
            lines.append(N.norm_line(metric, head[key], norms, fmt, genre or 'all', tally=tallies.get(metric) or ''))
    scorecard['norm_lines'] = [l for l in lines if l]
    rows_norm = {}
    pending = norms.get('pending') if isinstance(norms.get('pending'), dict) else {}
    for metric in N.METRICS:
        a = (norms.get('all') or {}).get(metric)
        g = (norms.get('genre') or {}).get(metric)
        # genre_own: the genre column carries a norm of its own for this row (not the all-genres value standing in)
        genre_own = bool(g and g.get('genre_used') not in (None, 'all'))
        pend = pending.get(metric) if isinstance(pending.get(metric), dict) else {}
        rows_norm[metric] = {'norm': a.get('value') if a else None, 'genre_norm': (g.get('value') if g else None), 'genre_own': genre_own,
                             # stand_in: the instrument the row's norm borrows ('feature screening') while it is not a norm of the format
                             'stand_in': ('' if genre_own else str((a or {}).get('stand_in') or '')),
                             'own_tests': int(pend.get('own_tests') or 0) if pend else None,
                             'note': str(pend.get('note') or '') if (pend and not a and not g) else ''}
    scorecard['norm_columns'] = rows_norm
    choice = norms.get('choice') if isinstance(norms.get('choice'), dict) else N.norm_choice(fmt, genre or 'all')
    scorecard['norm_choice'] = choice
    for note in (choice.get('notes') or ([choice['note']] if choice.get('note') else [])):
        if note and note not in scorecard['norm_lines']:
            scorecard['norm_lines'] = list(scorecard['norm_lines']) + [note]
    seats = tallies.get('seats') or len(bat.get('by_seat') or []) or 0
    people = next((q.get('people') for q in (bat.get('quadrants') or []) if isinstance(q, dict) and q.get('key') == 'total'), None) or scorecard.get('people')
    scorecard['denominator_line'] = denominator_line(seats, people)
    scorecard['tallies'] = {k: v for k, v in tallies.items() if k != 'seats'}
    return scorecard


def _share_word(v, high: str, mid: str, high_at: float = 90.0, mid_at: float = 65.0) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ''
    return high if f >= high_at else mid if f >= mid_at else f"{f:.1f}%"


def _with_tally(v, tally: str) -> str:
    return f"{pct1(v)}%" + (f" ({tally})" if tally else '')


def headline_read(session: dict) -> dict:
    """The strongest honest finding, for the TLDR's first lines and the
    scorecard page's header (Liz Huszarik 2026-11-05). Deterministic:

    * every money measure with a norm sits under it: lead with intent
      (would finish, would watch a second episode) against a soft
      Excellent, with the components under the norm as the explanation;
    * every money measure with a norm clears it: lead with total
      positive against the norm;
    * otherwise: total positive with the norm beside every number.

    Returns {lead, header, lines, norm_word}. Headers are one sentence
    of twelve words or fewer, sentence case, a full stop. Shares carry
    the seat tally beside them; the words name the norm they are read
    against ('the feature screening norm', 'the all-pilots norm')."""
    from migration import digital_twin_norms as N
    sc = (session or {}).get('scorecard') or {}
    bat = sc.get('battery') or {}
    head = bat.get('headline') or {}
    if head.get('total_positive_pct') is None:
        return {'lead': 'none', 'header': 'The scorecard.', 'lines': [], 'norm_word': ''}
    cols = sc.get('norm_columns') or {}
    choice = sc.get('norm_choice') if isinstance(sc.get('norm_choice'), dict) else {}
    tallies = battery_tallies(bat)
    who = N.norm_word(choice) if choice else 'the norm'

    def norm_of(metric):
        col = cols.get(metric) or {}
        v = col.get('genre_norm') if col.get('genre_own') else col.get('norm')
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    gaps = {}
    for metric, key in (('excellent', 'excellent_pct'), ('total_positive', 'total_positive_pct'), ('definitely_recommend', 'definitely_recommend_pct')):
        nv = norm_of(metric)
        if nv is not None and head.get(key) is not None:
            gaps[metric] = float(head[key]) - nv
    tp, dr, ex, fin, tune = head.get('total_positive_pct'), head.get('definitely_recommend_pct'), head.get('excellent_pct'), head.get('finish_pct'), head.get('tune_in_pct')
    t = lambda m: tallies.get(m) or ''   # noqa: E731
    money = f"total positive {_with_tally(tp, t('total_positive'))}, definitely recommend {_with_tally(dr, t('definitely_recommend'))}, excellent {_with_tally(ex, t('excellent'))}"
    intent_bits = []
    if fin is not None:
        intent_bits.append(f"{_with_tally(fin, t('finish'))} would finish")
    if tune is not None:
        intent_bits.append(f"{_with_tally(tune, t('tune_in'))} would watch a second episode")
    intent = ' and '.join(intent_bits)
    lines = []
    if gaps and all(g <= -3 for g in gaps.values()) and fin is not None:
        lead = 'intent'
        f_word = _share_word(fin, 'Nearly everyone', 'Most')
        w_word = _share_word(tune, 'nearly everyone', 'most') if tune is not None else ''
        header = f"{f_word} would finish" + (f" and {w_word} would watch again" if w_word else '') + "; Excellent is soft."
        lines.append(f"{intent[0].upper() + intent[1:]}.")
        ex_n = norm_of('excellent')
        pts = int(round(abs(gaps['excellent']))) if 'excellent' in gaps else None
        soft = f"Excellent is soft at {_with_tally(ex, t('excellent'))}" + (f", {pts} points below {who} of {ex_n:.0f}%" if pts is not None and ex_n is not None else '')
        others = [f"total positive {_with_tally(tp, t('total_positive'))}", f"definitely recommend {_with_tally(dr, t('definitely_recommend'))}"]
        lines.append(soft + f"; {' and '.join(others)} also sit under it.")
        comps = ((session.get('diagnostics') or {}).get('components') or {}).get('rows') or []
        weak = sorted([c for c in comps if c.get('vs_norm_pp') is not None and float(c['vs_norm_pp']) <= -5], key=lambda c: float(c['vs_norm_pp']))[:2]
        if weak:
            parts = [f"{c['label'].lower()} ({pct1(c.get('excellent_pct'))}% against {float(c['norm']):.0f}%)" for c in weak if c.get('norm') is not None]
            if parts:
                joined = ' and '.join(parts)
                verb = 'are the components' if len(parts) > 1 else 'is the component'
                lines.append(f"{joined[0].upper() + joined[1:]} {verb} under the norm; that is where the gap sits.")
    elif gaps and all(g >= 3 for g in gaps.values()):
        lead = 'beats'
        tp_n = norm_of('total_positive')
        pts = int(round(gaps['total_positive'])) if 'total_positive' in gaps else None
        header = f"Total positive {pct1(tp)}%, {pts} points above {who}." if pts is not None else f"Total positive {pct1(tp)}% clears the norm."
        lines.append(f"Total positive {_with_tally(tp, t('total_positive'))}" + (f", {pts} points above {who} of {tp_n:.0f}%" if pts is not None and tp_n is not None else '')
                     + f"; definitely recommend {_with_tally(dr, t('definitely_recommend'))} and excellent {_with_tally(ex, t('excellent'))} also clear it.")
        if intent:
            lines.append(f"{intent[0].upper() + intent[1:]}.")
    else:
        lead = 'mixed'
        header = f"Total positive {pct1(tp)}%, with the norm beside every number."
        lines.append(f"{money[0].upper() + money[1:]}.")
        if intent:
            lines.append(f"{intent[0].upper() + intent[1:]}.")
    header = ' '.join(header.split()[:12]).rstrip(',;:')
    if not header.endswith('.'):
        header += '.'
    return {'lead': lead, 'header': header, 'lines': lines, 'norm_word': who}


def set_norm(sess: dict, genre: str, *, by_user: bool = True, twin: Optional[dict] = None, s3=None) -> dict:
    """The user sets what the test is compared to (Jenna 2026-11-04:
    "let's change it to where you set what norm to compare it to").
    ``genre`` is a norms genre or '' for the all-genres column. The
    session's ``norm_genre`` and ``genre`` follow it, ``genre_set_by_user``
    is True when the user touched it (the frames' vote then never
    overrides it). On a scored test the norm lines and columns are
    re-read on the stored scorecard, the test is re-filed in the ledger
    under the new genre, and the forecast re-reads the norms. Nothing is
    re-run. Returns what changed."""
    from migration.digital_twin_norms import _genre as _ng
    g = str(genre or '').strip().lower()
    g = _ng(g) if g else ''
    g = '' if g == 'all' else g
    old = str(sess.get('genre') or '')
    changed = {}
    if by_user and not sess.get('genre_set_by_user'):
        sess['genre_set_by_user'] = True
        changed['genre_set_by_user'] = True
    elif not by_user and not g and sess.get('genre_set_by_user'):
        sess['genre_set_by_user'] = False      # cleared without a choice: the system's guess may fill it again
        changed['genre_set_by_user'] = False
    if g == old and sess.get('norm_genre') == g and not changed:
        return changed
    sess['genre'] = g
    sess['norm_genre'] = g
    changed['genre'] = g
    sc = sess.get('scorecard') if isinstance(sess.get('scorecard'), dict) else None
    if sc:
        fmt = str(sc.get('format') or format_for(sess))
        if g != old and sess.get('norms_recorded_at'):
            # the test's reads move from the old genre's cells to the new one's (the all-genres cells keep them)
            try:
                from migration import digital_twin_norms as N
                if old:
                    N.forget_test_genre(_norms_s3(s3), str(sess.get('id') or ''), fmt, old)
                record_norms(sess, s3=s3)
            except Exception as e:
                print(f"[pilot] norms re-file skipped: {e}")
        try:
            sc['genre'] = g
            attach_norms(sc, fmt, g or 'all', s3=s3, company=session_company(sess))
        except Exception as e:
            print(f"[pilot] norms refresh skipped: {e}")
        try:   # the character and component rows carry the norm as it stands now
            from migration.digital_twin_diagnostics import refresh_norm_refs
            refresh_norm_refs(sess)
        except Exception as e:
            print(f"[pilot] norm refs refresh skipped: {e}")
        if twin is not None:
            try:   # the forecast reads the norm columns and the genre (completion base)
                from migration.digital_twin_forecast import attach_forecast
                attach_forecast(twin, sess, s3=s3)
            except Exception as e:
                print(f"[pilot] forecast refresh skipped: {e}")
    sess['updated_at'] = _now()
    return changed


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
    # the caption runs on after the clock; an acronym or a double-capital name keeps its case ('CBS logo', 'LL Cool J')
    frame = f"At {_clock(t)}" + (f", right after {lower_first(on)}".rstrip(', ') if on else '')
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
              seat_dials: dict, subject: str, media_key: str, segments: Optional[list] = None) -> dict:
    """The dial reads: the dial average, favorite moment, dead spot,
    notes, people (the room's segments, else the seats' own people, else
    the twin's US size; never a stand-in). The battery is attached by
    prepare_watch."""
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
    dead_line = (lost_near[0].get('line') if lost_near else ('The room dipped here' + (f": {lower_first(dead_on)}" if dead_on else '. A few seats looked away.')))
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
    people = room_people(roster, segments or [], twin)
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
    if not analysis:
        # a text or option stimulus (a script, a concept, titles, key art) is its own analysis: the text is the cut
        try:
            from migration import digital_twin_modes as MODES
            analysis = MODES.text_analysis(session)
        except Exception as e:
            print(f"[pilot] text analysis skipped: {e}")
            analysis = None
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
        # the system's best guess (the frames' vote, else the words) until the user sets the norm themselves
        session['genre'] = norms_genre(title, notes, votes=an.get('genre_votes')) or str(session.get('genre') or '')
        session['norm_genre'] = session['genre']
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
    source = str(an.get('source')) if an.get('source') in ('text', 'options') else ('file' if grounded else 'clock')
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
                                  meta={'title': title, 'genre': session.get('genre') or '', 'notes': notes, 'rough_cut': bool(session.get('rough_cut')),
                                        'mode': str(session.get('mode') or 'pilot'), 'logline': str(session.get('logline') or '')[:300],
                                        'prior_cut': str(((session.get('prior_read') or {}).get('frame')) or '')[:300], 'critics_room': bool(session.get('critics_room'))}, duration_s=dur)
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
    sc = scorecard(twin, roster, beats, agg, events, seat_dials, subject, media_key, segments=segments)
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
            best_in, worst_in = bat.get('best') or {}, bat.get('worst') or {}
        except Exception as e:
            print(f"[pilot] battery card skipped: {e}")
    if answers_in:
        session['watch']['card'] = {'format': fmt, 'answers': {str(k): v for k, v in answers_in.items()}, 'why': {str(k): v for k, v in why_in.items()},
                                    'best': {str(k): v for k, v in (locals().get('best_in') or (cached_card or {}).get('best') or {}).items()},
                                    'worst': {str(k): v for k, v in (locals().get('worst_in') or (cached_card or {}).get('worst') or {}).items()}}
    sc['battery'] = battery_scorecard(twin, roster, segments, seat_dials, beats, scenes, events, fmt=fmt, subject=subject, media_key=media_key,
                                      session_seed=seed, cards=cards, answers_in=answers_in, why_in=why_in)
    # the card's people is the figure the battery published for the whole room (the same chain of sources; it agrees with the quadrants)
    sc['people'] = room_people(roster, segments, twin, quadrant_total=next((q.get('people') for q in sc['battery'].get('quadrants') or [] if q.get('key') == 'total'), None))
    head = sc['battery']['headline']
    sc['recommend_pct'] = head.get('definitely_recommend_pct')     # older readers: the money number
    sc['finish_intent_pct'] = head.get('finish_pct')
    sc['format'] = fmt
    sc['genre'] = session.get('genre') or ''
    sc['watch_source'] = source
    attach_norms(sc, fmt, session.get('genre') or 'all', s3=s3, company=session_company(session, twin))
    session['scorecard'] = sc
    try:   # the performance forecast rides on every scored test (Jenna 2026-11-03)
        from migration.digital_twin_forecast import attach_forecast
        attach_forecast(twin, session, s3=s3)
    except Exception as _fe:
        print(f"[pilot] forecast skipped: {_fe}")
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
    # the mode's own read (trailer lift, option ranking, concept read, script notes, episode commitment, the
    # marketing read on a locked cut) and the diagnostics every screening carries (characters, pace, confusion,
    # the ending, scenes liked best and least, components)
    tell('the room is writing its notes')
    try:
        from migration import digital_twin_modes as MODES
        MODES.finish_mode_read(session, twins_by_key, models=models, usage_extras=usage_extras, prior=session.get('prior_read') or None)
    except Exception as e:
        print(f"[pilot] mode read skipped: {e}")
    try:
        from migration import digital_twin_diagnostics as DIAG
        DIAG.run_diagnostics(session, twin, s3=s3)
    except Exception as e:
        print(f"[pilot] diagnostics skipped: {e}")
    try:   # what the room said about the people the setup named, next to their metrics (Jenna 2026-11-04)
        from migration import digital_twin_talent_read as TR
        TR.attach_talent_read(session, twin, models=models, usage_extras=usage_extras, reason='prepared')
    except Exception as e:
        print(f"[pilot] talent read skipped: {e}")
    # the preview stats block (walkouts join the recruit), the critics room's forecast, two audiences on one cut
    try:
        from migration import digital_twin_compare as CMP
        CMP.finish_recruit_block(session)
        if session.get('critics_room'):
            CMP.critic_forecast(session)
        else:
            session.pop('critics', None)
        if len({str(r.get('twin') or '') for r in roster if r.get('twin')}) >= 2:
            session['two_audiences'] = CMP.two_audiences(session, twins_by_key)
        else:
            session.pop('two_audiences', None)
    except Exception as e:
        print(f"[pilot] recruit / critics / audiences read skipped: {e}")
    session['status'] = 'ready'
    session['prepare_note'] = 'Ready to play'
    session['updated_at'] = _now()
    session['version'] = VERSION
    session['pilot_version'] = PILOT_VERSION
    return session


MODE_TITLE_WORD = {'pilot': 'Pilot test', 'feature': 'Screening', 'episode': 'Episode test', 'binge': 'Binge test', 'trailer': 'Trailer test', 'spot': 'Spot test',
                   'title': 'Title test', 'key_art': 'Key art test', 'script': 'Script read', 'concept': 'Concept test'}


MAX_COHORTS = 20     # the Custom count on the setup form runs to twenty (Jenna, 2026-11-03); the room still caps at MAX_IN_ROOM seats


def cohort_segments(cohorts, twin_key: str) -> list:
    """The segments a setup's cohorts become (Jenna, 2026-11-02): each
    cohort's name, its composition in words (the filter), the seats
    wanted in it. [] when there are fewer than two usable cohorts."""
    out = []
    for c in (cohorts or [])[:MAX_COHORTS]:
        if not isinstance(c, dict):
            continue
        name = ' '.join(str(c.get('name') or c.get('label') or '').split())[:60]
        if not name and isinstance(c.get('demos'), dict):
            # a cohort picked from the standard demographics names itself when the name is left blank
            from migration import digital_twin_demos as _D
            name = _D.suggest_name(c.get('demos'))[:60]
        if not name:
            continue
        try:
            n_seats = int(c.get('seats') or c.get('panelists') or 0)
        except (TypeError, ValueError):
            n_seats = 0
        seg = {'label': name, 'composition': ' '.join(str(c.get('composition') or name).split())[:160],
               'seats': max(1, min(24, n_seats)) if n_seats else 0, 'twin': str(twin_key or '')}
        # a cohort picked from the standard demographics (Jenna, 2026-11-03): the selection is the filter and
        # its words are the composition; free text never has to be read
        from migration import digital_twin_demos as D
        demos = D.clean_demos(c.get('demos'))
        if demos:
            seg['demos'] = demos
            seg['composition'] = (D.demos_words(demos) or name)[:160]
            f = {'demos': demos}
            codes = D.gender_codes(demos.get('GENDER') or [])
            if len(codes) == 1:
                f['gender'] = codes[0]
            span = D.age_span(demos.get('AGE') or [])
            if span:
                f['age'] = list(span)
            if demos.get('PRIMARY_LANGUAGE') == ['Spanish']:
                f['language'] = 'Spanish'
            seg['filter'] = f
        out.append(seg)
    return out if len(out) >= 2 else []


def open_session(twins: list, *, segments: Optional[list] = None, in_room: int = DEFAULT_IN_ROOM,
                 title: str = '', created_by: str = '', session_id: str = '',
                 media: Optional[dict] = None, mode: str = 'pilot', logline: str = '', text: str = '',
                 options: Optional[list] = None, rough_cut: bool = False, critics_room: bool = False, group: Optional[dict] = None,
                 research: bool = True, model_fn=None, s3=None) -> dict:
    """A new Pilot Testing session in any screening mode. Opening is
    free. ``group`` reuses a prior test's room (a recut re-test keeps
    the same seats and personas). The room is sized to what it stands
    for (its cuts; a title that has aired, when ``research`` is on and a
    model answers) before the record is returned."""
    sid = session_id or uuid.uuid4().hex[:10]
    seed = f"pilot|{sid}|{'|'.join(_twin_key(t) for t in twins)}|{VERSION}"
    mode = str(mode or 'pilot').lower()
    mode = mode if mode in MODES else 'pilot'
    group = group if isinstance(group, dict) and group.get('roster') else build_group(twins, segments, in_room, seed)
    names = ', '.join(dict.fromkeys(_twin_name(t) for t in twins))
    name = clean_room_title(title)
    media = dict(media or {})
    media.setdefault('title', name or names)
    media.setdefault('runtime_s', 0)
    media.setdefault('source', 'slate' if mode not in ('title', 'key_art', 'script', 'concept') else 'text')
    opts = []
    if options:
        try:
            from migration.digital_twin_modes import normalize_options
            opts = normalize_options(options, mode)
        except Exception:
            opts = []
    note = {'title': 'Name the test, type the titles and the logline.', 'key_art': 'Name the test and upload the art.', 'script': 'Name the test and add the script.',
            'concept': 'Name the test and paste the concept.', 'trailer': 'Name the test and add the trailer.', 'spot': 'Name the test and add the spot.'}.get(mode, 'Name the test and add the cut, or paste a watch link.')
    sess = {
        'id': sid, 'kind': 'pilot',
        'title': name or f"{MODE_TITLE_WORD.get(mode, 'Pilot test')}: {names}",
        'group': group, 'questions': [], 'cohorts': [],
        'media': media, 'status': 'draft',
        'mode': mode, 'genre': '', 'norm_genre': '', 'genre_set_by_user': False, 'cut_label': 'Cut A' if mode not in ('trailer', 'spot') else 'Trailer A', 'lineage': None, 'rough_cut': bool(rough_cut),
        'critics_room': bool(critics_room),
        'cast': [], 'comps': [], 'purge_media_after': False,
        'assets': [], 'options': opts, 'logline': ' '.join(str(logline or '').split())[:600], 'text': str(text or '').strip()[:400000],
        'mode_read': {}, 'asset_reads': {}, 'diagnostics': {}, 'quotas': None, 'recruit': None,
        'beats': [], 'scenes': [], 'dial': {'t': [], 'value': [], 'splits': {}},
        'seat_dials': {}, 'events': [], 'voices_live': [], 'probes': [], 'guide': None, 'watch': {},
        'scorecard': None, 'play_charged': False, 'questions_paid': 0,
        'created_by': created_by or 'prometheus',
        'created_at': _now(), 'updated_at': _now(), 'version': VERSION, 'pilot_version': PILOT_VERSION,
        'prepare_note': note,
    }
    # the room stands for its cuts, and for a title that has aired no more than that title's own audience in those
    # cuts (Jenna 2026-11-05); a reused room is sized again for this test's title
    try:
        from migration.digital_twin_sizing import RESEARCH_WAIT_S, size_session
        size_session(twins[0] if twins else {}, sess, twins=twins, model_fn=model_fn, s3=s3, research=research, seed=seed,
                     wait_s=(None if model_fn is not None else RESEARCH_WAIT_S))      # a room opening in a request does not wait on the web for long
    except Exception as e:
        print(f"[pilot] sizing skipped: {e}")
    sess['cohorts'] = [{'label': str(sg.get('label') or ''), 'composition': str(sg.get('composition') or ''), 'seats': int(sg.get('seats') or 0),
                        'people': int(sg.get('people') or 0), 'share_pct': float(sg.get('share_pct') or 0), 'filter': dict(sg.get('filter') or {})}
                       for sg in (group.get('segments') or []) if isinstance(sg, dict) and sg.get('composition')]
    return sess


def apply_watch_url(sess: dict, raw: str, *, hint_title: str = '', runtime_s: float = 0,
                    mode: str = '', fetch=None) -> dict:
    """Paste any streamer or digital link onto the test. The twins
    watch that title; embeddable players play in the stage, the rest
    open on their own site. Returns the resolved record (empty when
    the string is not a URL)."""
    from migration.digital_twin_watch_url import attach_media, resolve
    rec = resolve(raw, hint_title=hint_title or str((sess or {}).get('title') or ''),
                  runtime_s=runtime_s, mode=mode or str((sess or {}).get('mode') or ''), fetch=fetch)
    if not rec:
        return {}
    media = attach_media((sess or {}).get('media') or {}, rec, fallback_title=hint_title or str((sess or {}).get('title') or ''))
    media['hash'] = media.get('hash') or media_hash(media.get('title') or '', media.get('runtime_s') or 0, rec.get('url') or '', '')
    sess['media'] = media
    sess['prepare_note'] = 'The room is watching ' + (rec.get('platform_label') or 'the episode') + '.'
    sess['updated_at'] = _now()
    return rec


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


def play_price_key(sess: dict) -> str:
    """The pull type first Play carries for this test's mode: the
    session price for a pilot, feature, episode, binge or critics room;
    the per-asset trailer price; the title, key art, concept or script
    price; the recut price when the test is cut B on a prior test's
    room. Opening an empty test never charges."""
    from migration.digital_twins import MODE_PRICE_KEY
    if (sess or {}).get('lineage') and ((sess or {}).get('lineage') or {}).get('parent'):
        return MODE_PRICE_KEY['recut']
    mode = str((sess or {}).get('mode') or 'pilot')
    return MODE_PRICE_KEY.get(mode, 'digital_twin_pilot')


def play_quantity(sess: dict) -> int:
    """How many units first Play bills: one per asset on a trailer or
    TV spot test (up to three), one everywhere else."""
    mode = str((sess or {}).get('mode') or 'pilot')
    if mode in ('trailer', 'spot') and not ((sess or {}).get('lineage') or {}).get('parent'):
        n = len([a for a in (sess.get('assets') or []) if isinstance(a, dict) and (a.get('s3_key') or a.get('runtime_s'))])
        return max(1, min(3, n or 1))
    return 1


def charge_due(sess: dict, included: int = QUESTIONS_INCLUDED) -> Optional[str]:
    """Which price the next action carries.

    First Play: the mode's price (``play_price_key``: digital_twin_pilot
    for a watch, digital_twin_trailer per asset, digital_twin_title,
    digital_twin_key_art, digital_twin_concept, digital_twin_script,
    digital_twin_recut for cut B on the same room). Questions after that
    sit in the included block. A further block: digital_twin_pilot_more.
    """
    if not sess:
        return None
    if not sess.get('play_charged'):
        return play_price_key(sess)
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
        sess['play_started_at'] = _now()   # the clock the auto-stop reads when nobody is left to press Stop (2026-11-03)
        sess.pop('auto_stopped_at', None)
    sess['updated_at'] = _now()
    return sess


AUTO_STOP_GRACE_S = 90.0


def parse_runtime(text) -> float:
    """Seconds from what a moderator types: '42:35' (minutes and seconds),
    '1:02:10' (hours), '42.5' or '42' (minutes). 0 when unreadable."""
    t = str(text if text is not None else '').strip()
    if not t:
        return 0.0
    if ':' in t:
        parts = [p.strip() for p in t.split(':')]
        try:
            nums = [float(p or 0) for p in parts]
        except ValueError:
            return 0.0
        if len(nums) == 2:
            return max(0.0, nums[0] * 60 + nums[1])
        if len(nums) == 3:
            return max(0.0, nums[0] * 3600 + nums[1] * 60 + nums[2])
        return 0.0
    try:
        return max(0.0, float(t) * 60.0)
    except ValueError:
        return 0.0


def auto_stop_due(sess: dict, now: Optional[float] = None) -> bool:
    """A playing test whose runtime has run out (plus a short grace) stops
    on its own (Jenna 2026-11-03: 'if they put in 42:35 it would stop
    automatically at 42 minutes and 35 seconds', so the moderator can
    leave). Read on every poll and load of the test."""
    if str((sess or {}).get('status') or '') != 'playing':
        return False
    try:
        dur = float(((sess or {}).get('media') or {}).get('runtime_s') or 0)
    except (TypeError, ValueError):
        dur = 0.0
    started = str(sess.get('play_started_at') or '')
    if dur <= 0 or not started:
        return False
    try:
        import calendar
        t0 = calendar.timegm(time.strptime(started, '%Y-%m-%dT%H:%M:%SZ'))
    except Exception:
        return False
    return (now if now is not None else time.time()) >= t0 + dur + AUTO_STOP_GRACE_S


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
        # the norm the test is compared to; 'genre_set_by_user' in the body says whether the person touched the
        # Compare-to field (a pre-filled guess they left alone keeps following the frames' vote)
        by_user = bool(body.get('genre_set_by_user')) if 'genre_set_by_user' in body else bool(str(body.get('genre') or '').strip())
        got = set_norm(sess, str(body.get('genre') or ''), by_user=by_user)
        if 'genre' in got:
            changed['genre'] = got['genre']
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
    if 'critics_room' in body:
        v = bool(body.get('critics_room'))
        if v != bool(sess.get('critics_room')):
            sess['critics_room'] = v
            changed['critics_room'] = v
    # the mode's stimulus: options (titles), logline, text (a script or a concept), cast
    if any(k in body for k in ('options', 'logline', 'text', 'cast')):
        try:
            from migration.digital_twin_modes import set_stimulus
            changed.update(set_stimulus(sess, {k: body[k] for k in ('options', 'logline', 'text', 'cast') if k in body}))
        except Exception as e:
            print(f"[pilot] stimulus not set: {e}")
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


def question_clock(rec: dict) -> str:
    """Where the playhead stood when a question was asked: the route's
    stamp, else the clock in the stimulus the question carried."""
    rec = rec or {}
    if rec.get('playhead_clock'):
        return str(rec['playhead_clock'])
    win = (rec.get('stimulus') or {}).get('window') if isinstance(rec.get('stimulus'), dict) else None
    if isinstance(win, dict) and win.get('clock'):
        return str(win['clock'])
    return _clock(rec['playhead_s']) if rec.get('playhead_s') is not None else ''


def question_split(rec: dict) -> list:
    """The split on a question as [{label, pct, people, tally}]: the
    room engine's options when they carry the published share (with the
    seat tally, '7 of 16'), else the distribution rows."""
    opts = (rec or {}).get('options') or []
    rows = opts if (opts and isinstance(opts[0], dict) and 'pct' in opts[0]) else ((rec or {}).get('distribution') or [])
    out = []
    for r in rows:
        if isinstance(r, dict) and str(r.get('label') or '').strip():
            out.append({'label': r.get('label'), 'pct': r.get('pct'), 'people': r.get('people'), 'tally': str(r.get('tally') or '')})
    return out


def can_delete(sess: dict, username: str, is_admin: bool = False) -> bool:
    if is_admin:
        return True
    return bool(username) and str((sess or {}).get('created_by') or '') == str(username)


# ----------------------------------------------------------- saved setups
# Jenna, 2026-11-03: "have a way to save a room without having to re-enter
# everything if you come back later". A setup is the Pilot Testing form as
# typed (what it is, the link, title, runtime, the prior cut, the cast, the
# room name, the second twin, panelists, cohorts, quotas), saved on the
# twin under its own name and reopened into the form later. It is not a
# test: nothing is seated, nothing is charged, no file is stored (a browser
# cannot hand a chosen file back, so the setup keeps the file's name as a
# reminder to pick it again).
MAX_SETUPS = 40
SETUP_TEXT_MAX = 120_000       # a pasted script
_SETUP_FIELD_RX = re.compile(r'^dtwPi[A-Za-z0-9]{1,40}$')


def _setup_clean(body: dict) -> dict:
    fields = {}
    for k, v in (body.get('fields') or {}).items() if isinstance(body.get('fields'), dict) else []:
        k = str(k)
        if not _SETUP_FIELD_RX.match(k) or v is None:
            continue
        if isinstance(v, bool):
            fields[k] = v
        else:
            txt = str(v)
            fields[k] = txt[:SETUP_TEXT_MAX] if k == 'dtwPiText' else txt[:2000]
    options = [str(x)[:120] for x in (body.get('options') or []) if str(x or '').strip()][:6]
    cohorts = []
    for c in (body.get('cohorts') or [])[:20]:
        if not isinstance(c, dict):
            continue
        name, comp = str(c.get('name') or '')[:60], str(c.get('composition') or '')[:160]
        if not (name or comp or (isinstance(c.get('demos'), dict) and any(c['demos'].values()))):
            continue
        try:
            seats = int(c.get('seats') or 0)
        except (TypeError, ValueError):
            seats = 0
        row = {'name': name, 'composition': comp, 'seats': max(1, min(24, seats or 4))}
        try:
            from migration import digital_twin_demos as D
            demos = D.clean_demos(c.get('demos'))
            if demos:
                row['demos'] = demos
                row['composition'] = (D.demos_words(demos) or comp)[:160]
        except Exception:
            pass
        cohorts.append(row)
    try:
        cohort_n = max(1, min(20, int(body.get('cohort_n') or (len(cohorts) if cohorts else 1))))
    except (TypeError, ValueError):
        cohort_n = max(1, len(cohorts))
    files = [str(x)[:160] for x in (body.get('files') or []) if str(x or '').strip()][:7]
    tab = str(body.get('tab') or 'pilot').strip().lower()
    tab = tab if tab in ('pilot', 'trailer', 'titles', 'script', 'concept') else 'pilot'
    mode = str(body.get('mode') or '').strip().lower()
    mode = mode if mode in MODES else ''
    name = str(body.get('name') or fields.get('dtwPiTitle') or fields.get('dtwPiCutTitle') or '').replace('\n', ' ').strip()[:120]
    return {'name': name or 'Untitled setup', 'tab': tab, 'mode': mode, 'fields': fields, 'options': options,
            'cohorts': cohorts, 'cohort_n': cohort_n, 'cohort_custom': bool(body.get('cohort_custom')),
            'file_name': str(body.get('file_name') or '')[:160], 'files': files}


def save_setup(payload: dict, body: dict, username: str = '', s3=None) -> dict:
    """Upsert one saved setup on the twin (by id when the body carries one
    the twin knows; otherwise new, newest first). Returns the record.

    A setup that carries a cut (a duplicate of a test, see
    ``duplicate_session_as_setup``) keeps it across saves; it goes when
    the body says ``drop_media`` or names a different file (the person
    picked another cut, which the browser hands over at Start)."""
    setups = [x for x in (payload.get('pilot_setups') or []) if isinstance(x, dict)]
    rec = _setup_clean(body or {})
    sid = str((body or {}).get('id') or '').strip()
    cur = next((x for x in setups if str(x.get('id')) == sid), None) if sid else None
    now = _now()
    if cur is not None:
        carried = cur.get('media') if isinstance(cur.get('media'), dict) else None
        new_file = str(rec.get('file_name') or '').strip()
        if carried and ((body or {}).get('drop_media') or (new_file and new_file != str(carried.get('filename') or ''))):
            _drop_setup_media(s3, cur)
        elif carried and not new_file:
            rec['file_name'] = str(carried.get('filename') or cur.get('file_name') or '')[:160]
        cur.update(rec)
        cur['updated_at'] = now
        cur['updated_by'] = str(username or '')
        rec = cur
    else:
        rec.update({'id': uuid.uuid4().hex[:10], 'created_by': str(username or ''), 'created_at': now, 'updated_at': now})
        setups.insert(0, rec)
    payload['pilot_setups'] = setups[:MAX_SETUPS]
    return rec


def delete_setup(payload: dict, setup_id: str, s3=None) -> Optional[dict]:
    setups = [x for x in (payload.get('pilot_setups') or []) if isinstance(x, dict)]
    gone = next((x for x in setups if str(x.get('id')) == str(setup_id)), None)
    if gone is None:
        return None
    payload['pilot_setups'] = [x for x in setups if x is not gone]
    _drop_setup_media(s3, gone)      # the cut a duplicate carried goes with the setup; the source test keeps its own file
    return gone


# ----------------------------------------------------------- duplicates
# Jenna, 2026-11-04: "have a duplicate button where you could duplicate a
# room with all of the input if you want to so if you just want to change
# one thing you can". A duplicate is a NEW saved setup: every input of the
# source (a saved setup, or a test read back off its session), none of the
# results, priced exactly like a setup typed from scratch (nothing seated,
# nothing charged until Start the test). It opens in the setup form so the
# one thing can change before the room is seated. A test's cut comes along:
# the file is copied to the setup's own place so nothing is uploaded again.
_TAB_FOR_MODE = {'pilot': 'pilot', 'feature': 'pilot', 'episode': 'pilot', 'binge': 'pilot', 'trailer': 'trailer', 'spot': 'trailer',
                 'title': 'titles', 'key_art': 'titles', 'script': 'script', 'concept': 'concept'}
_MEDIA_INPUT_KEYS = ('title', 'runtime_s', 'source', 'filename', 'content_type', 'bytes', 'hash', 'url', 'watch_url', 'platform', 'platform_label',
                     'content_id', 'embed_url', 'kind', 'show', 'season', 'episode', 'open_label')


def taken_names(payload: dict) -> list:
    """Every name already in the Pilot Testing panel: the saved setups and
    the tests, so a duplicate's name is new on both lists."""
    out = []
    for x in (payload or {}).get('pilot_setups') or []:
        if isinstance(x, dict):
            out.append(str(x.get('name') or ''))
    for s in (payload or {}).get('pilot_sessions') or []:
        if isinstance(s, dict):
            out.append(str(s.get('title') or ''))
    return out


def setup_media_key(username: str, setup_id: str, filename: str) -> str:
    """digital-twins/pilot-tests/<user>/setup-<id>/<file>: the cut a saved
    setup carries."""
    return media_s3_key(username, 'setup-' + re.sub(r"[^A-Za-z0-9_-]+", '', str(setup_id or '')), filename)


def _copy_object(s3, src: str, dest: str) -> bool:
    """A server-side copy inside the bucket. False (never raises) when
    there is no client, no source, or the copy failed."""
    if s3 is None or not src or not dest or src == dest:
        return False
    try:
        s3.copy_object(Bucket='dashboard-inputs', CopySource={'Bucket': 'dashboard-inputs', 'Key': src}, Key=dest, MetadataDirective='COPY')
        return True
    except Exception as e:
        print(f"[pilot] file copy skipped: {e}")
        return False


def _carry_media(media: dict) -> dict:
    """The input side of a media record: the file's name, type, size,
    hash, runtime, title and any watch link. Never the storage key (the
    caller sets the new one), never the undo or purge marks."""
    return {k: copy.deepcopy(v) for k, v in (media or {}).items() if k in _MEDIA_INPUT_KEYS}


def _drop_setup_media(s3, rec: dict) -> bool:
    """Remove the cut a saved setup carries (object and record). Never
    raises; True when the record carried one."""
    media = (rec or {}).get('media') if isinstance((rec or {}).get('media'), dict) else None
    if not media:
        return False
    mk = str(media.get('s3_key') or '')
    if mk and s3 is not None:
        try:
            s3.delete_object(Bucket='dashboard-inputs', Key=mk)
        except Exception as e:
            print(f"[pilot] setup file removal skipped: {e}")
    rec.pop('media', None)
    rec.pop('transcript', None)
    return True


def _runtime_text(seconds) -> str:
    """'42:35' or '1:02:10', the way the setup form takes a runtime; ''
    when there is none."""
    try:
        s = int(round(float(seconds or 0)))
    except (TypeError, ValueError):
        return ''
    if s <= 0:
        return ''
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _num_text(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ''
    return str(int(f)) if f == int(f) else f"{f:g}"


def _names(items) -> list:
    out = []
    for c in items or []:
        s = str((c.get('title') or c.get('name') or c.get('label') or '') if isinstance(c, dict) else c).strip()
        if s:
            out.append(s)
    return out


def setup_from_session(sess: dict, name: str) -> dict:
    """The setup form a test was started from, read back off its session:
    every input (what it is, the switches, the cut's title, runtime and
    watch link, the prior cut, the cast, the logline, the text, the
    titles, the second twin, the panelists, the cohorts, the quotas and
    comps, the remove-the-file switch), none of the results. The cut
    itself and the file names ride on top in
    ``duplicate_session_as_setup``."""
    sess = sess if isinstance(sess, dict) else {}
    mode = str(sess.get('mode') or 'pilot').lower()
    mode = mode if mode in MODES else 'pilot'
    tab = _TAB_FOR_MODE.get(mode, 'pilot')
    media = sess.get('media') if isinstance(sess.get('media'), dict) else {}
    group = sess.get('group') if isinstance(sess.get('group'), dict) else {}
    title = ' '.join(str(sess.get('title') or '').split())
    fields = {'dtwPiTitle': str(name or '')[:120], 'dtwPiRough': bool(sess.get('rough_cut')), 'dtwPiCritics': bool(sess.get('critics_room')),
              'dtwPiPurgeNew': bool(sess.get('purge_media_after'))}
    cut_title = ' '.join(str(media.get('title') or '').split())
    if cut_title and cut_title.lower() != title.lower():
        fields['dtwPiCutTitle'] = cut_title[:160]
    try:
        runtime = float(media.get('runtime_s') or 0)
    except (TypeError, ValueError):
        runtime = 0.0
    if runtime > 0:
        if tab == 'trailer':
            fields['dtwPiSecs'] = _num_text(round(runtime))
        else:
            fields['dtwPiMins'] = _runtime_text(runtime)
    link = str(media.get('url') or '') if media.get('source') == 'watch_url' else str(media.get('watch_url') or '')
    if link:
        fields['dtwPiWatchUrl'] = link[:2000]
    if str(sess.get('logline') or '').strip():
        fields['dtwPiLogline'] = ' '.join(str(sess['logline']).split())[:600]
    if str(sess.get('text') or '').strip():
        fields['dtwPiText'] = str(sess['text'])[:SETUP_TEXT_MAX]
    cast = _names(sess.get('cast'))
    if cast:
        fields['dtwPiCast'] = ', '.join(cast)[:2000]
    keys = [str(k) for k in (sess.get('twin_keys') or group.get('twins') or []) if str(k)]
    if len(keys) > 1:
        fields['dtwPiTwin2'] = keys[1]
    try:
        in_room = int(group.get('in_room') or 0)
    except (TypeError, ValueError):
        in_room = 0
    if in_room:
        fields['dtwPiRoom'] = str(in_room)
    if sess.get('group_tier'):
        fields['dtwPiTier'] = str(sess.get('group_tier'))      # the tier the source test opened on (2026-11-05)
    quotas = sess.get('quotas') if isinstance(sess.get('quotas'), dict) else None
    if quotas is None and isinstance(sess.get('recruit'), dict) and isinstance(sess['recruit'].get('quotas'), dict):
        quotas = sess['recruit']['quotas']
    quotas = quotas or {}
    for fk, qk in (('dtwPiQUnder', 'under_35_pct'), ('dtwPiQWomen', 'women_pct'), ('dtwPiQFans', 'fans_of_lead_pct')):
        if quotas.get(qk) is not None and _num_text(quotas.get(qk)):
            fields[fk] = _num_text(quotas.get(qk))
    try:
        must = int(quotas.get('must_have_seen') or 0)
    except (TypeError, ValueError):
        must = 0
    if must > 0:
        fields['dtwPiQSeen'] = str(must)
    comps = _names(sess.get('comps')) or _names(quotas.get('comps'))
    if comps:
        fields['dtwPiComps'] = ', '.join(comps)[:2000]
    parent = (sess.get('lineage') or {}).get('parent') if isinstance(sess.get('lineage'), dict) else None
    if parent:
        fields['dtwPiPrior'] = str(parent)
    options = [str(o.get('label') if isinstance(o, dict) else o).strip() for o in (sess.get('options') or [])]
    options = [o[:120] for o in options if o][:6]
    cohorts = []
    for c in sess.get('cohorts') or []:
        if not isinstance(c, dict):
            continue
        label = ' '.join(str(c.get('label') or c.get('name') or '').split())[:60]
        comp = ' '.join(str(c.get('composition') or '').split())[:160]
        if not (label or comp):
            continue
        try:
            seats = int(c.get('seats') or 0)
        except (TypeError, ValueError):
            seats = 0
        row = {'name': label, 'composition': comp, 'seats': max(1, min(24, seats or 4))}
        demos = (c.get('filter') or {}).get('demos') if isinstance(c.get('filter'), dict) else None
        if isinstance(demos, dict) and any(demos.values()):
            row['demos'] = copy.deepcopy(demos)
        cohorts.append(row)
    if len(cohorts) < 2:
        cohorts = []
    cohort_n = len(cohorts) if cohorts else 1
    files = [str(a.get('filename') or '') for a in (sess.get('assets') or [])
             if isinstance(a, dict) and a.get('filename') and (str(a.get('kind') or 'video') != 'video' or int(a.get('slot') or 0) >= 1)]
    files += [str(o.get('filename') or '') for o in (sess.get('options') or []) if isinstance(o, dict) and o.get('filename')]
    file_name = str(media.get('filename') or '') if media.get('source') == 'upload' or media.get('s3_key') else ''
    return {'name': str(name or '')[:120] or 'Untitled setup', 'tab': tab, 'mode': mode, 'fields': fields, 'options': options, 'cohorts': cohorts,
            'cohort_n': cohort_n, 'cohort_custom': cohort_n > 6, 'file_name': file_name[:160], 'files': [f[:160] for f in files if f][:7]}


def _carry_file_onto_setup(s3, rec: dict, media: dict, transcript, username: str) -> bool:
    """Copy a cut to the new setup's own key and put its record (and the
    file's words, when the source had them) on the setup. False when
    there is nothing to copy or the copy failed; the setup then keeps
    the file's name as the reminder to pick it again, as a setup saved
    from the form does."""
    src = str((media or {}).get('s3_key') or '')
    if not src:
        return False
    dest = setup_media_key(username or str(rec.get('created_by') or 'seat'), str(rec.get('id') or ''), str(media.get('filename') or 'cut.mp4'))
    if not _copy_object(s3, src, dest):
        return False
    carried = _carry_media(media)
    carried['s3_key'] = dest
    carried.setdefault('source', 'upload')
    rec['media'] = carried
    if transcript:
        rec['transcript'] = copy.deepcopy(transcript)
    return True


def duplicate_setup(payload: dict, setup_id: str, username: str = '', is_admin: bool = False, s3=None) -> Optional[dict]:
    """A saved setup duplicated: a new record, first on the twin, with
    every input of the source and a new name ('<name> copy'). The cut
    the source carries comes along for the person who saved it (or an
    admin); anyone else gets the file's name as the reminder. None when
    the setup is not on the twin; the caller saves the twin."""
    setups = [x for x in (payload.get('pilot_setups') or []) if isinstance(x, dict)]
    src = next((x for x in setups if str(x.get('id')) == str(setup_id)), None)
    if src is None:
        return None
    rec = copy.deepcopy({k: v for k, v in src.items() if k not in ('id', 'created_by', 'created_at', 'updated_at', 'updated_by', 'media', 'transcript', 'duplicated_from', 'duplicated_from_title')})
    rec['name'] = copy_name(src.get('name') or 'Untitled setup', taken_names(payload))
    fields = rec.get('fields') if isinstance(rec.get('fields'), dict) else {}
    fields['dtwPiTitle'] = rec['name']
    rec['fields'] = fields
    now = _now()
    rec.update({'id': uuid.uuid4().hex[:10], 'created_by': str(username or ''), 'created_at': now, 'updated_at': now,
                'duplicated_from': str(src.get('id') or ''), 'duplicated_from_title': str(src.get('name') or '')[:120]})
    media = src.get('media') if isinstance(src.get('media'), dict) else None
    if media and media.get('s3_key') and can_edit_setup(src, username, is_admin):
        _carry_file_onto_setup(s3, rec, media, src.get('transcript'), username)
    setups.insert(0, rec)
    payload['pilot_setups'] = setups[:MAX_SETUPS]
    return rec


def duplicate_session_as_setup(s3, payload: dict, sid: str, username: str = '', is_admin: bool = False) -> Optional[dict]:
    """A test duplicated into a new saved setup (first on the twin): every
    input read back off the session (``setup_from_session``), the cut
    copied to the setup's own key when the caller may see it (the person
    who opened the test, or an admin), the file's words with it. No
    result travels: no seats, no scorecard, no dial, no events, no
    questions, no watch, no forecast, no play or stop clock, nothing
    paid. None when the test is not on the twin; the caller saves the
    twin."""
    sess = next((s for s in (payload.get('pilot_sessions') or []) if isinstance(s, dict) and str(s.get('id')) == str(sid)), None)
    if sess is None:
        return None
    rec = setup_from_session(sess, copy_name(sess.get('title') or 'Untitled test', taken_names(payload)))
    now = _now()
    rec.update({'id': uuid.uuid4().hex[:10], 'created_by': str(username or ''), 'created_at': now, 'updated_at': now,
                'duplicated_from': str(sess.get('id') or ''), 'duplicated_from_title': str(sess.get('title') or '')[:120]})
    media = sess.get('media') if isinstance(sess.get('media'), dict) else {}
    if media.get('s3_key') and can_view_media(sess, username, is_admin):
        _carry_file_onto_setup(s3, rec, media, sess.get('transcript'), username)
    setups = [x for x in (payload.get('pilot_setups') or []) if isinstance(x, dict)]
    setups.insert(0, rec)
    payload['pilot_setups'] = setups[:MAX_SETUPS]
    return rec


def attach_setup_media(s3, sess: dict, setup: dict, username: str = '') -> bool:
    """Start the test with the cut a saved setup carries: the file is
    copied to the new test's own key (under its id), the record lands
    on the test with the runtime and title typed on the form kept when
    they were, and the file's words come along. The setup keeps its
    copy for the next run. False when the setup carries no file or the
    copy failed (the test opens without a file, as before)."""
    media = (setup or {}).get('media') if isinstance((setup or {}).get('media'), dict) else {}
    src = str(media.get('s3_key') or '')
    if not src or not isinstance(sess, dict):
        return False
    dest = media_s3_key(username or str(sess.get('created_by') or 'seat'), str(sess.get('id') or ''), str(media.get('filename') or 'cut.mp4'))
    if not _copy_object(s3, src, dest):
        return False
    carried = _carry_media(media)
    carried['s3_key'] = dest
    carried.setdefault('source', 'upload')
    cur = sess.get('media') if isinstance(sess.get('media'), dict) else {}
    if cur.get('title'):
        carried['title'] = cur['title']
    try:
        typed = float(cur.get('runtime_s') or 0)
    except (TypeError, ValueError):
        typed = 0.0
    if typed > 0:
        carried['runtime_s'] = typed
    carried.setdefault('runtime_s', 0)
    sess['media'] = carried
    if setup.get('transcript') and not sess.get('transcript'):
        sess['transcript'] = copy.deepcopy(setup['transcript'])
    sess['status'] = 'draft'
    sess['watch'] = {}
    sess['prepare_note'] = 'Preparing the cut'
    sess['updated_at'] = _now()
    return True


def public_setup(rec: dict) -> dict:
    """A saved setup for the page: the carried cut's storage key never
    travels (``has_file`` says there is one) and the file's words stay
    server-side."""
    if not isinstance(rec, dict):
        return {}
    out = {k: v for k, v in rec.items() if k != 'transcript'}
    media = rec.get('media') if isinstance(rec.get('media'), dict) else None
    if media is not None:
        m = {k: v for k, v in media.items() if k not in ('s3_key', 'undo_key')}
        m['has_file'] = bool(media.get('s3_key'))
        out['media'] = m
    return out


def public_setups(payload: dict) -> list:
    return [public_setup(x) for x in (payload or {}).get('pilot_setups') or [] if isinstance(x, dict)]


def can_edit_setup(rec: dict, username: str, is_admin: bool = False) -> bool:
    if is_admin:
        return True
    return bool(username) and str((rec or {}).get('created_by') or '') in ('', str(username))


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
    forget_norms(sess, s3)
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


def restore_session(s3, twin_key: str, payload: dict, sid: str, restored_by: str = '') -> Optional[dict]:
    """A deleted test back on its twin from the 30-day undo copy: the
    record returns to pilot_sessions (newest first), the file moves back
    beside it, the undo copy goes. None when there is no copy or the
    test is already on the twin. The person who deleted it or an admin
    (the route checks)."""
    if s3 is None or any(str(x.get('id')) == str(sid) for x in (payload or {}).get('pilot_sessions') or []):
        return None
    undo_key = deleted_pilot_key(twin_key, sid)
    try:
        rec = json.loads(s3.get_object(Bucket='dashboard-inputs', Key=undo_key)['Body'].read().decode('utf-8'))
    except Exception:
        return None
    sess = rec.get('session') if isinstance(rec, dict) else None
    if not isinstance(sess, dict) or not sess.get('id'):
        return None
    media = sess.get('media') if isinstance(sess.get('media'), dict) else {}
    undo_media = str(rec.get('media_undo_key') or media.get('undo_key') or '')
    if undo_media:
        back = media_s3_key(str(sess.get('created_by') or restored_by or 'seat'), str(sess['id']), str(media.get('filename') or os.path.basename(undo_media)))
        try:
            s3.copy_object(Bucket='dashboard-inputs', CopySource={'Bucket': 'dashboard-inputs', 'Key': undo_media}, Key=back, MetadataDirective='COPY')
            s3.delete_object(Bucket='dashboard-inputs', Key=undo_media)
            media['s3_key'] = back
            media.pop('undo_key', None)
        except Exception as e:
            print(f"[pilot] media restore skipped (the read is back, the file is not): {e}")
    sess['restored_at'] = _now()
    sess['restored_by'] = restored_by or ''
    sess['updated_at'] = _now()
    payload.setdefault('pilot_sessions', []).insert(0, sess)
    if str(sess.get('status') or '') in ('stopped', 'done', 'finished', 'complete'):
        record_norms(sess, s3, twin=payload)     # a restored finished test joins its company's ledger again (it left when it was deleted)
    try:
        s3.delete_object(Bucket='dashboard-inputs', Key=undo_key)
    except Exception:
        pass
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
    # the extra assets' reads travel without their caches or file keys; the options without storage keys
    reads = {}
    for aid, rec in (out.get('asset_reads') or {}).items():
        if isinstance(rec, dict):
            r = {k: v for k, v in rec.items() if k not in ('watch', 'seat_dials')}
            reads[aid] = r
    out['asset_reads'] = reads
    out['assets'] = [{k: v for k, v in a.items() if k not in ('s3_key', 'local_path')} | {'has_file': bool(a.get('s3_key'))} for a in (out.get('assets') or []) if isinstance(a, dict)]
    out['options'] = [{k: v for k, v in o.items() if k not in ('s3_key', 'local_path')} | {'has_file': bool(o.get('s3_key'))} for o in (out.get('options') or []) if isinstance(o, dict)]
    pr = out.get('prior_read')
    if isinstance(pr, dict):
        out['prior_read'] = {k: v for k, v in pr.items() if k != 'seat_dials'}
    # every seat says who it stands for (Jenna 2026-11-05: the bio names the people the seat represents out of the
    # room's total); a test from before the figures gets them on the way out, the stored record untouched
    g = out.get('group')
    if isinstance(g, dict) and g.get('roster'):
        try:
            from migration.digital_twin_panel import annotate_seat_stands_for
            out['group'] = annotate_seat_stands_for(dict(g, roster=[dict(r) if isinstance(r, dict) else r for r in g.get('roster') or []]))
        except Exception as e:
            print(f"[pilot] seat stands-for skipped: {e}")
    # what the room stands for travels as its figures and its one line; the basis (what was looked up) never does
    if isinstance(out.get('group'), dict) and isinstance(out['group'].get('sizing'), dict):
        try:
            from migration.digital_twin_sizing import public_sizing
            out['group'] = dict(out['group'], sizing=public_sizing(out['group']['sizing']))
        except Exception as e:
            print(f"[pilot] sizing view skipped: {e}")
    # the forecast's calibration block and recorded actual are audit and never travel; the notes go through the guard
    # (Jenna 2026-11-05: calibrate to published performance, never say so)
    if isinstance(out.get('forecast'), dict):
        try:
            from migration.digital_twin_forecast import public_forecast
            out['forecast'] = public_forecast(out['forecast'])
        except Exception as e:
            print(f"[pilot] forecast view skipped: {e}")
    # what the room said about the people the setup named follows the card's character metrics as they stand (a read built
    # against older metrics shows as the deterministic read until the next question rebuilds it with the moderator)
    try:
        from migration.digital_twin_talent_read import public_view
        public_view(sess, out)
    except Exception as e:
        print(f"[pilot] talent read view skipped: {e}")
    # the group's billing (Jenna 2026-11-05): the tier, the cap, the questions used, the upgrade offer at the cap
    try:
        from migration.digital_twin_pricing import public_fields
        out.update(public_fields(sess))
    except Exception as e:
        print(f"[pilot] group view skipped: {e}")
    return out


def _csv_payload_for(session: dict) -> dict:
    """A payload stand-in for the report pages when the route did not pass
    the twin: the first segment's twin name is the name on the cover."""
    segs = ((session or {}).get('group') or {}).get('segments') or []
    name = next((str(s.get('twin_name') or s.get('label')) for s in segs if isinstance(s, dict) and (s.get('twin_name') or s.get('label'))), 'the twin')
    return {'meta': {'name': name, 'subject': name}}


def session_csv(session: dict, payload: Optional[dict] = None) -> str:
    """The complete record of a test as CSV (2026-11-04: every insight the
    report carries, in every format). Blocks, each under its own header
    row: the test line with the norm it is compared to; the scorecard
    battery with the norm columns; the quadrants; the characters; the
    components; pace by act; the scenes liked best and least; the
    forecast; the recruit; the room; the dial second by second; every
    reaction; the questions put to the room (the read, the voices, the
    split, what the room knew and what people were saying); the guide;
    and the report page by page, so a block added to the report lands
    here too."""
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
    # the norm columns carry the name of what the test is compared to, from the one helper the card and the report use
    # ('All pilots', 'Procedural pilots', 'Feature screening' while the all column is a stand-in); a chosen genre with no
    # norm of its own yet leaves its column blank rather than repeating the all-genres value (2026-11-04)
    from migration import digital_twin_norms as N
    nch = sc.get('norm_choice') if isinstance(sc.get('norm_choice'), dict) else {}
    fmt_csv = str(sc.get('format') or session.get('format') or session.get('mode') or 'pilot')
    if not nch:
        nch = N.norm_choice(fmt_csv, genre, (sc.get('norms') or {}).get('cells') if isinstance(sc.get('norms'), dict) else None)
    all_label = str(nch.get('all_label') or N.choice_label(fmt_csv, 'all'))
    genre_label = str(nch.get('label') or N.choice_label(fmt_csv, genre))
    compared = N.compared_label(nch)
    stand_in = str(nch.get('stand_in') or '')
    first_norm_col = f"{stand_in.capitalize()} norm %" if stand_in else f"{all_label} norm %"
    gown = lambda nc: (nc or {}).get('genre_norm') if (nc and nc.get('genre_own', True) and genre != 'all') else ''   # noqa: E731
    tallies = battery_tallies(bat)
    seats = tallies.get('seats') or len(((session.get('group') or {}).get('roster')) or [])
    people = session_people(session, payload)
    w.writerow(['Test', 'Title', 'Cut', 'Format', 'Genre', 'Runtime s', 'Seats', 'People', 'Dial average', 'Excellent %', 'Excellent seats', 'Total positive %', 'Total positive seats',
                'Definitely recommend %', 'Definitely recommend seats', 'Would finish %', 'Would finish seats', 'Favorite moment', 'Dead spot', 'Compared to', 'Norm note', 'Shares are of'])
    fav = sc.get('favorite_moment') or {}
    dead = sc.get('dead_spot') or {}
    w.writerow([
        session.get('id'), session.get('title'), session.get('cut_label') or 'Cut A', sc.get('format') or session.get('format') or '', genre,
        (session.get('media') or {}).get('runtime_s'), seats, people, sc.get('overall_like'),
        head.get('excellent_pct'), tallies.get('excellent') or '', head.get('total_positive_pct'), tallies.get('total_positive') or '',
        head.get('definitely_recommend_pct'), tallies.get('definitely_recommend') or '', head.get('finish_pct'), tallies.get('finish') or '',
        f"{fav.get('clock')} {fav.get('line')}", f"{dead.get('clock')} {dead.get('line')}",
        compared, nch.get('note') or '', sc.get('denominator_line') or denominator_line(seats, people),
    ])
    from migration.digital_twin_copy import strip_calibration_lines as _no_cal
    for line in _no_cal(list(sc.get('norm_lines') or []) + list(sc.get('notes') or [])):
        w.writerow(['Note', line])
    w.writerow([])
    w.writerow(['Question', 'Answer', 'Share %', 'People', 'In the room', first_norm_col, f"{genre_label} norm %" if genre != 'all' else 'Genre norm %'])
    metric_for = {'overall': {'a': 'excellent'}, 'recommend': {'a': 'definitely_recommend'}, 'finish': {'a': 'finish'}, 'tune_in': {'a': 'tune_in'},
                  'urgency': {'a': 'urgency_opening'}, 'urgency_pilot': {'a': 'urgency_opening'}}
    for q in bat.get('questions') or []:
        for r in q.get('rows') or []:
            m = (metric_for.get(q.get('id')) or {}).get(r.get('id'))
            nc = norms.get(m) if m else None
            w.writerow([q.get('label'), r.get('label'), r.get('pct'), r.get('people'), r.get('tally'), (nc or {}).get('norm') if nc else '', gown(nc)])
        if q.get('id') == 'overall':
            nc = norms.get('total_positive') or {}
            w.writerow([q.get('label'), 'Total positive', q.get('total_positive_pct'), q.get('total_positive_people'), '', nc.get('norm'), gown(nc)])
    w.writerow([])
    # each quadrant's shares are of its own people (the seats' projection); the seat count sits in its own column
    w.writerow(['Quadrant', 'Seats', 'People (shares are of these)', 'Excellent %', 'Total positive %', 'Definitely recommend %', 'Would finish %'])
    for qd in bat.get('quadrants') or []:
        w.writerow([qd.get('label'), qd.get('n'), qd.get('people'), qd.get('excellent_pct'), qd.get('total_positive_pct'), qd.get('definitely_recommend_pct'), qd.get('finish_pct')])
    nrm = sc.get('norms') or {}
    if nrm:
        fin_norm = (nrm.get('all') or {}).get('finish', {}).get('value')
        w.writerow([first_norm_col.replace(' %', ''), '', '', (nrm.get('all') or {}).get('excellent', {}).get('value'), (nrm.get('all') or {}).get('total_positive', {}).get('value'),
                    (nrm.get('all') or {}).get('definitely_recommend', {}).get('value'), fin_norm if fin_norm is not None else ((norms.get('finish') or {}).get('note') or N.metric_note('finish', 0))])
        if genre != 'all' and str(nrm.get('genre_used') or 'all') != 'all':
            w.writerow([f"{genre_label} norm", '', '', (nrm.get('genre') or {}).get('excellent', {}).get('value'), (nrm.get('genre') or {}).get('total_positive', {}).get('value'),
                        (nrm.get('genre') or {}).get('definitely_recommend', {}).get('value'), (nrm.get('genre') or {}).get('finish', {}).get('value')])
        for note in (nch.get('notes') or ([nch['note']] if nch.get('note') else [])):
            w.writerow([note, '', '', '', '', '', ''])
    # the diagnostics blocks the report carries (2026-11-04): the characters, the components, pace by act, the scenes
    d = session.get('diagnostics') if isinstance(session.get('diagnostics'), dict) else {}
    chars_doc = d.get('characters') if isinstance(d.get('characters'), dict) else {}
    chars = [c for c in (chars_doc.get('characters') or []) if isinstance(c, dict)]
    if chars:
        w.writerow([])
        follow = {str(f.get('name')): f.get('pct') for f in (chars_doc.get('follow') or []) if isinstance(f, dict)}
        w.writerow(['Character', 'Role', 'Excellent %', 'Total positive %', first_norm_col, 'Vs norm pp', 'On screen', 'Screen time against the rating', 'Would follow into a second episode %'])
        for c in chars:
            w.writerow([c.get('name'), c.get('role'), c.get('excellent_pct'), c.get('total_positive_pct'), c.get('norm'), c.get('vs_norm_pp'), c.get('screen_clock'), c.get('time_vs_like'), follow.get(str(c.get('name')), '')])
        rel = chars_doc.get('relationship') if isinstance(chars_doc.get('relationship'), dict) else {}
        if rel.get('pair'):
            w.writerow([' and '.join(str(p) for p in rel['pair']), 'relationship', rel.get('excellent_pct'), rel.get('total_positive_pct'), '', '', '', f"{rel.get('scenes_together') or 0} scenes together", ''])
        if follow:
            # the follow shares are per person, never a split: the note rides under the rows so the column is never read as one
            from migration.digital_twin_diagnostics import FOLLOW_NOTE
            w.writerow(['Would follow into a second episode', FOLLOW_NOTE + ' The shares do not add to 100.'])
        if chars_doc.get('read'):
            w.writerow(['Characters read', chars_doc['read']])
    comps = [c for c in ((d.get('components') or {}).get('rows') or []) if isinstance(c, dict)]
    if comps:
        w.writerow([])
        w.writerow(['Component', 'Excellent %', first_norm_col, f"{genre_label} norm %" if genre != 'all' else 'Genre norm %', 'Vs norm pp'])
        for c in comps:
            # the genre column stays blank while the genre has no norm of its own (never the all column's value under the genre's name)
            gcol = (norms.get(f"component_{c.get('key')}") or {})
            w.writerow([c.get('label'), c.get('excellent_pct'), c.get('norm'), (c.get('genre_norm') if (genre != 'all' and gcol.get('genre_own')) else ''), c.get('vs_norm_pp')])
        if (d.get('components') or {}).get('read'):
            w.writerow(['Components read', d['components']['read']])
    pace = d.get('pace') if isinstance(d.get('pace'), dict) else {}
    if pace.get('acts'):
        w.writerow([])
        w.writerow(['Stretch', 'From', 'To', 'Too slow %', 'Just right %', 'Too fast %', 'Excellent %', 'Line'])
        for a in pace['acts']:
            if isinstance(a, dict):
                w.writerow([a.get('label'), a.get('from_clock'), a.get('to_clock'), a.get('too_slow_pct'), a.get('just_right_pct'), a.get('too_fast_pct'), '', ''])
        w.writerow(['Beginning on its own', '', '', '', '', '', (pace.get('beginning') or {}).get('excellent_pct'), ''])
        w.writerow(['Ending on its own', '', '', '', '', '', (pace.get('ending') or {}).get('excellent_pct'), ''])
        w.writerow(['Confused somewhere', '', '', '', '', '', '', f"{pace.get('confusion_pct')}% of the room"])
        for cf in pace.get('confusions') or []:
            if isinstance(cf, dict):
                w.writerow(['Confusion', cf.get('clock') or '', '', '', '', '', '', cf.get('line')])
        for key, label in (('running_time_read', 'Running time'), ('read', 'Pace read')):
            if pace.get(key):
                w.writerow([label, '', '', '', '', '', '', pace[key]])
    scenes = d.get('scenes') if isinstance(d.get('scenes'), dict) else {}
    if scenes.get('liked_best') or scenes.get('liked_least') or scenes.get('not_recommending'):
        w.writerow([])
        w.writerow(['Scenes', 'Clock', 'On screen', 'Mentions', 'People', 'In their words'])
        for label, key in (('Liked best', 'liked_best'), ('Liked least', 'liked_least')):
            for s in scenes.get(key) or []:
                if not isinstance(s, dict):
                    continue
                q = next((x for x in (s.get('quotes') or []) if isinstance(x, dict) and x.get('line')), {})
                w.writerow([label, s.get('clock'), s.get('on_screen'), s.get('mentions'), s.get('people'), f"{q.get('name')}: {q.get('line')}" if q else ''])
        for n in scenes.get('not_recommending') or []:
            if isinstance(n, dict):
                w.writerow(['Would not recommend', '', '', '', '', f"{n.get('name')}: {n.get('line')}"])
        if scenes.get('read'):
            w.writerow(['Scenes read', '', '', '', '', scenes['read']])
    # what the room said about the people the setup named, one row each (Jenna 2026-11-04)
    try:
        from migration.digital_twin_talent_read import report_lines as _talent_lines
        talent_rows = _talent_lines(session)
    except Exception as e:
        print(f"[pilot] csv talent read skipped: {e}")
        talent_rows = []
    if talent_rows:
        w.writerow([])
        w.writerow(['What the room said', 'Role', 'In the room\'s words', 'A seat\'s line', 'Said by'])
        for p in talent_rows:
            w.writerow([p.get('name'), p.get('role'), p.get('said'), p.get('quote'), p.get('who')])
    # what each name attached to the test adds to the first month: the name's pull and the room's verdict in one lift, the
    # billing a fact in its own column (Jenna 2026-11-04)
    attached = [r for r in (session.get('talent_attach') or []) if isinstance(r, dict) and isinstance(r.get('outputs'), dict) and r.get('talent')]
    if attached:
        w.writerow([])
        w.writerow(['Talent attach', 'Billed', 'First-month reach lift %', 'More people', 'Low', 'High', 'Return lift %', 'Name pull index',
                    'Would follow into a second episode %', 'Excellent %', 'Named as the reason to come back', 'Read'])
        for r in attached:
            o = r['outputs']
            v = o.get('room') if isinstance(o.get('room'), dict) else {}
            parts = v.get('parts') or {}
            rm = r.get('room') if isinstance(r.get('room'), dict) else {}
            named = (f"{rm.get('draw_seats')} of {rm.get('seats')}" if rm.get('seen') and rm.get('draw_asked') else '')
            w.writerow([r.get('talent'), r.get('role'), o.get('reach_lift_pct'), o.get('expected'), o.get('low'), o.get('high'), o.get('return_lift_pct'), o.get('already_follow_index'),
                        parts.get('follow', ''), parts.get('excellent', ''), named, r.get('read')])
        w.writerow(['Talent attach by quadrant', 'Name', 'Quadrant', 'Seats', 'People', 'Reach lift %'])
        for r in attached:
            for q in r.get('by_quadrant') or []:
                if isinstance(q, dict) and q.get('n'):
                    w.writerow(['', r.get('talent'), q.get('label'), q.get('n'), q.get('people'), q.get('reach_lift_pct')])
    # the forecast as a reader gets it: no calibration block, no recorded actual, the notes through the guard (Jenna 2026-11-05)
    from migration.digital_twin_forecast import public_forecast as _public_forecast
    fc = _public_forecast(session.get('forecast')) if isinstance(session.get('forecast'), dict) else {}
    if fc.get('episode'):
        ep, se, su = fc['episode'], fc.get('series') or {}, fc.get('subscriptions') or {}
        w.writerow([])
        w.writerow(['Forecast', 'Value', 'Low', 'High', 'Definition'])
        w.writerow(['Week-one audience if released', ep.get('week_one_audience'), ep.get('low'), ep.get('high'), ep.get('definition')])
        w.writerow(['Season completion %', se.get('season_completion_pct'), '', '', se.get('completion_definition')])
        w.writerow(['Second screen %', se.get('second_screen_pct'), '', '', se.get('second_screen_definition')])
        w.writerow(['Season average audience', se.get('season_average_audience'), '', '', 'People per episode across the season'])
        w.writerow(['Finale audience', se.get('finale_audience'), '', '', 'People who watch the season finale'])
        w.writerow([f"Would subscribe to {su.get('platform') or 'the service'} for it, % of people without it", su.get('subscribe_pct_of_nonsubscribers'), '', '', f"About {su.get('subscribe_people')} people"])
        w.writerow(['Would reactivate a dormant account for it, % of dormant accounts', su.get('reactivate_pct_of_dormant'), '', '', f"About {su.get('reactivate_people')} people"])
        for n in fc.get('notes') or []:
            w.writerow(['Note', n, '', '', ''])
    # the recruit and the room (2026-11-04): who was asked for, who was seated
    rec = session.get('recruit') if isinstance(session.get('recruit'), dict) else {}
    if rec:
        w.writerow([])
        w.writerow(['Recruit', 'Value'])
        for label, key in (('Recruited to', 'concept'), ('Invited', 'invited'), ('Showed', 'showed'), ('Seated', 'seated'), ('Invite ratio', 'invite_ratio')):
            if rec.get(key) not in (None, ''):
                w.writerow([label, rec.get(key)])
        wo = rec.get('walkouts') if isinstance(rec.get('walkouts'), dict) else {}
        w.writerow(['Walked out', wo.get('count') if wo.get('count') is not None else 0])
        if wo.get('read'):
            w.writerow(['Walkouts', wo['read']])
        for q in rec.get('quotas_met') or []:
            if isinstance(q, dict):
                w.writerow([f"Quota: {q.get('quota')}", f"{q.get('target_pct')}% asked for, {q.get('seated_pct')}% seated"])
        for c in rec.get('cells') or []:
            if isinstance(c, dict):
                w.writerow([f"Cell: {c.get('label')}", f"{c.get('seats')} seats"])
        if rec.get('stats_line'):
            w.writerow(['Recruit line', rec['stats_line']])
    grp = session.get('group') if isinstance(session.get('group'), dict) else {}
    if grp.get('roster'):
        from migration.digital_twin_exports import segment_shares as _segment_shares
        w.writerow([])
        w.writerow(['Segment', 'Share of room %', 'People', 'Seats'])
        for label, share, people, seats in _segment_shares(session):
            w.writerow([label, share, people, seats])
        w.writerow([])
        w.writerow(['Seat', 'Name', 'Age', 'City', 'Gender', 'Segment'])
        for r in grp['roster']:
            if isinstance(r, dict):
                w.writerow([r.get('seat'), r.get('name'), r.get('age'), r.get('city'), r.get('gender_label') or r.get('gender'), r.get('segment')])
    # the report, page by page: the same list the deck and the PDF are drawn from, so a block added to the report lands in
    # the CSV too (the questions keep their own blocks below; here they carry the header and the sub line only)
    try:
        from migration.digital_twin_report import report_pages as _report_pages
        rpages = _report_pages(payload or _csv_payload_for(session), session, with_chart=False)
    except Exception as e:
        print(f"[pilot] csv report block skipped: {e}")
        rpages = []
    if rpages:
        w.writerow([])
        w.writerow(['Report page', 'Line', 'Value', 'Note'])
        for pg in rpages:
            if not isinstance(pg, dict) or pg.get('kind') == 'cover':
                continue
            w.writerow([pg.get('header') or '', pg.get('sub') or '', '', ''])
            if pg.get('kind') == 'question':
                continue
            for b in pg.get('body') or []:
                w.writerow(['', b, '', ''])
            for val, lab in pg.get('tiles') or []:
                w.writerow(['', lab, val, ''])
            if pg.get('rows'):
                hd = tuple(pg.get('head') or ('', '', ''))
                w.writerow(['', hd[0], hd[1], hd[2]])
                for row in pg['rows']:
                    lab, val, note = (list(row) + ['', '', ''])[:3]
                    w.writerow(['', lab, val, note])
    dial = session.get('dial') or {}
    w.writerow([])
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
    questions = [q for q in (session.get('questions') or []) if isinstance(q, dict)]
    w.writerow(['Clock', 'Question', 'Kind', 'Read'])
    for q in questions:
        w.writerow([question_clock(q), q.get('q'), q.get('kind'), q.get('read')])
    w.writerow([])
    w.writerow(['Clock', 'Question', 'Twin', 'Age', 'City', 'Line'])
    for q in questions:
        for v in q.get('voices') or []:
            w.writerow([question_clock(q), q.get('q'), v.get('name'), v.get('age'), v.get('city'), v.get('line')])
    # the split on every question (2026-11-04): each answer's published share, the people behind it and the seat
    # tally for the whole room, then the same split by segment; questions asked after the cut wrapped included
    w.writerow([])
    w.writerow(['Clock', 'Question', 'Segment', 'Answer', 'Share %', 'People', 'In the room'])
    for q in questions:
        for r in question_split(q):
            w.writerow([question_clock(q), q.get('q'), 'Whole room', r['label'], r['pct'], r['people'], r['tally']])
        for sr in q.get('by_segment') or []:
            for r in (sr.get('distribution') or []) if isinstance(sr, dict) else []:
                w.writerow([question_clock(q), q.get('q'), sr.get('segment'), r.get('label'), r.get('pct'), r.get('people'), ''])
    # what the room knew and what people were saying, one line each (the same block Focus Group IQ carries)
    if any((q.get('facts_used') or q.get('talk') or q.get('framing')) for q in questions):
        w.writerow([])
        w.writerow(['Clock', 'Question', 'Block', 'Line'])
        for q in questions:
            if q.get('framing'):
                w.writerow([question_clock(q), q.get('q'), 'How it was put to the room', q.get('framing')])
            for f in q.get('facts_used') or []:
                w.writerow([question_clock(q), q.get('q'), 'What the room knew', f])
            for t in q.get('talk') or []:
                w.writerow([question_clock(q), q.get('q'), 'What people were saying', t])
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
        t = battery_tallies(sc.get('battery') or {})
        out.append(f"Excellent {_with_tally(head.get('excellent_pct'), t.get('excellent') or '')}, total positive {_with_tally(head.get('total_positive_pct'), t.get('total_positive') or '')}, "
                   f"definitely recommend {_with_tally(head.get('definitely_recommend_pct'), t.get('definitely_recommend') or '')}"
                   + (f", would finish {_with_tally(head.get('finish_pct'), t.get('finish') or '')}." if head.get('finish_pct') is not None else '.'))
        if sc.get('denominator_line'):
            out.append(str(sc['denominator_line']))
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
    # no calibrated / anchored / as aired / ratings-service sentence in the short read (Jenna 2026-11-05)
    from migration.digital_twin_copy import strip_calibration_lines
    return strip_calibration_lines([x for x in out if x])


def has_banned_copy(s: str) -> list:
    low = str(s or '').lower()
    hits = []
    for tok in ('synth', 'synthetic', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap',
                's3://', 'agents', 'em dash', 'panel-projected'):
        if tok in low:
            hits.append(tok)
    if '\u2014' in str(s or '') or '\u2013' in str(s or ''):
        hits.append('dash')
    try:
        from migration.digital_twin_copy import calibration_hits
        hits.extend(calibration_hits(s))
    except Exception:
        pass
    return hits


__all__ = [
    'EVENT_TYPES', 'EVENT_LABEL', 'KINDS', 'STATUS', 'MODES', 'MEDIA_PREFIX', 'PILOT_VERSION',
    'DELETED_PILOT_PREFIX', 'DELETED_PILOT_KEEP_DAYS', 'QUESTIONS_INCLUDED', 'WINDOW_BACK_S', 'MEDIA_URL_EXPIRES_S', 'PRIOR_WEIGHT',
    'QUADRANTS', 'PHASE2_PRICE_NOTES', 'MODE_TITLE_WORD',
    'open_session', 'prepare_watch', 'apply_watch_url', 'find_reusable_session', 'charge_due', 'play_price_key', 'play_quantity',
    'mark_play_paid', 'mark_paid', 'mark_playing', 'stop_session', 'rename_session', 'set_options',
    'stamp_question', 'question_clock', 'question_split', 'can_delete', 'can_view_media', 'remove_session', 'delete_session', 'purge_media',
    'deleted_pilot_key', 'media_hash', 'build_beats', 'build_scenes', 'build_beats_from_scenes', 'seat_dial', 'blend_dial', 'seat_events', 'persona_events',
    'aggregate_dial', 'split_dial', 'split_specs', 'all_splits', 'cohort_labels', 'cohort_key', 'cohort_segments', 'restore_session', 'MAX_COHORTS', 'quadrant_of', 'scorecard', 'battery_scorecard', 'norms_for', 'norm_metrics', 'record_norms',
    'auto_stop_due', 'parse_runtime', 'AUTO_STOP_GRACE_S',
    'attach_norms', 'battery_tallies', 'denominator_line', 'headline_read', 'set_norm', 'keyword_genre', 'probe_points', 'fallback_guide', 'question_window', 'seat_window', 'ask_stimulus', 'format_for', 'norms_genre',
    'session_csv', 'session_lines', 'public_session', 'signed_media_url',
    'media_s3_key', 'accept_upload_name', 'ffmpeg_available', 'probe_duration',
    'process_local_cut', 'has_banned_copy', 'beat_step',
    'save_setup', 'delete_setup', 'can_edit_setup', 'MAX_SETUPS',
    'copy_name', 'taken_names', 'setup_media_key', 'setup_from_session', 'duplicate_setup', 'duplicate_session_as_setup',
    'attach_setup_media', 'public_setup', 'public_setups',
]
