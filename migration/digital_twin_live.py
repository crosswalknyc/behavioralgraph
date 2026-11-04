"""Pilot Testing, live: the room watches the screen with you.

Jenna, 2026-10-30: a pasted watch link (Paramount+, Netflix, Hulu, any
streamer) must play in the stage so reactions can be watched in real
time, the room must know what is on screen, and nothing may be scored
before Play.

Streamers refuse to load inside another site's frame, so the page
mirrors the streamer tab into the stage (the browser's own tab
capture) and samples a frame every few seconds. Each frame comes
here: one vision read describes what is on screen and moves every
seat's dial, and a few seats react in their own words. The page paints
that at once. Nothing is prepared ahead of time: the dial, the
reactions and the scorecard exist only for what the room has watched.

Stop turns the frames into the scene list the standing read works
from (``digital_twin_pilot.prepare_watch`` with a grounded analysis),
so the scorecard, norms, diagnostics and guide are built from the
episode that actually played. The live reactions stay on the test as
its record.

REGRESSION_TEST_MODE (or no model) answers every frame deterministically
so the flow can be tested offline.
"""
from __future__ import annotations

import base64
import calendar
import hashlib
import json
import threading
import time
import traceback
from typing import Optional

from migration.digital_twin_room import regression_mode, model_available, scrub
from migration.digital_twin_watch import EVENTS, TONES, _vision_json

FRAME_MIN_GAP_S = 6.0          # frames closer than this are ignored (the page sends one every ~12 s)
FRAME_MAX_BYTES = 600_000      # a 512 px JPEG is 20 to 60 KB; anything bigger is refused
MAX_REACTIONS = 3
DEFAULT_DIAL = 55.0
FINISH_STALE_S = 20 * 60       # a finish that never came back flips to stopped so the page is never stuck

LIVE_SYSTEM = (
    "You are the moderator's notes for a screening room of real people who are watching a show together right now. "
    "You see one frame from the screen plus what the room has seen so far. Return JSON only, no prose:\n"
    "{\"caption\": string, \"tone\": string, \"joke\": boolean, \"setting\": string, \"shift\": boolean, "
    "\"seats\": {\"<seat>\": {\"say\": string, \"move\": number}}, \"reactions\": [{\"seat\": number, \"type\": string, \"line\": string}]}\n"
    "caption: what is on screen in one plain sentence (who, where, what is happening), 140 characters max, present tense. "
    "tone: one of funny, tender, tense, action, wow, quiet, warm, curious. joke: true only when the frame is a comic beat. "
    "setting: two or three words. shift: true when this is a different scene from the last caption.\n"
    "seats: for EVERY seat listed, be that person for a second. say: what they are thinking at this moment, in their own "
    "voice, 80 characters max, grounded in who they are (their cohort, their age, how they watch, what they want from a show "
    "like this) and in what is on screen. move: where that thought takes their enjoyment reading from its current level, "
    "-12 to +12 points, 0 when the moment does nothing for them. The thought comes first and the move follows from it. The "
    "same moment moves different people differently and in different directions because they are different people: a quip "
    "lands for one seat and bores the next, a quiet beat is a breath for one and a reason to check the phone for another, "
    "a fight pulls some in and loses others. Nobody drifts in lockstep. Only a moment the whole room shares (a big laugh, "
    "a reveal, a death) moves everyone the same way. A seat that has been drifting keeps drifting until something on "
    "screen is for them.\n"
    "reactions: 0 to 3 seats whose thought they would say out loud right now, type one of love, laugh, lean-in, confuse, "
    "check-out, wow, and the line in that person's first-person voice (their thought, word for word, is fine), 90 "
    "characters max. Most frames earn 0 or 1 reaction; a quiet frame earns none. Never invent what is not in the frame.\n"
    "The cast: when the notes for this frame list cast on screen (confirmed by face, from the show's cast list), name them by "
    "those names, exactly as given, in the caption and in the seats' lines; anyone the notes do not list is described by what "
    "the screen shows. Never name an actor the notes do not give for this frame, and never guess a name from a face yourself."
)


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _clock(t) -> str:
    try:
        s = max(0, int(round(float(t))))
    except (TypeError, ValueError):
        s = 0
    return f"{s // 60}:{s % 60:02d}"


def is_live(sess: dict) -> bool:
    """A watch-link test: the room watches the mirrored screen, nothing
    is prepared ahead of Play."""
    media = (sess or {}).get('media') or {}
    if bool(((sess or {}).get('live') or {}).get('on')):
        return True
    return str(media.get('source') or '') == 'watch_url' and not media.get('s3_key')


def seat_live(sess: dict, twins_by_key: dict) -> dict:
    """Seat the room for a live watch: personas, an empty dial, no
    scorecard. Idempotent; a finished test is left alone."""
    live = sess.get('live') if isinstance(sess.get('live'), dict) else {}
    if live.get('finished_at'):
        return sess
    try:
        from migration.digital_twin_room import ensure_personas
        ensure_personas(sess, twins_by_key, session_seed=f"pilot|{sess.get('id')}|live")
    except Exception:
        traceback.print_exc()
    media = (sess.get('media') or {})
    sess['live'] = {'on': True, 'frames': live.get('frames') or [], 'seat_dials': live.get('seat_dials') or {}, 'events': live.get('events') or [],
                    'started_at': live.get('started_at'), 'finished_at': None, 'finishing_at': None, 'calls': int(live.get('calls') or 0), 'failed': int(live.get('failed') or 0)}
    sess['watch'] = {'source': 'live', 'persona': False, 'captions_n': len(sess['live']['frames']), 'scenes_n': len(sess['live']['frames'])}
    sess['dial'] = {'t': [], 'value': [], 'splits': {}}
    sess['seat_dials'] = {}
    sess['events'] = []
    sess['scenes'] = []
    sess['beats'] = []
    sess['probes'] = []
    sess['guide'] = None
    sess['scorecard'] = None
    sess['voices_live'] = []
    sess['status'] = 'ready'
    sess['prepare_note'] = 'Press Play, open the episode on ' + str(media.get('platform_label') or 'the service') + ', then mirror it here. The room watches with you.'
    sess['updated_at'] = _now()
    return sess


def start_live(sess: dict) -> dict:
    live = sess.setdefault('live', {'on': True, 'frames': [], 'seat_dials': {}, 'events': []})
    live['on'] = True
    if not live.get('started_at'):
        live['started_at'] = _now()
    sess['prepare_note'] = 'Mirror the episode here. The room reacts to what is on screen.'
    sess['updated_at'] = _now()
    return sess


# ------------------------------------------------------------ one frame
def _seat_line(r: dict) -> str:
    bio = ' '.join(str(r.get('persona') or '').split())
    if bio:
        first = bio.split('. ')[0]
        bio = (first if len(first) <= 170 else first[:167] + '...')
    return bio or f"{r.get('age') or ''} {r.get('city') or ''}".strip()


def _seat_who(twin: dict, r: dict, session_seed: str) -> str:
    """The person behind the seat for the frame read: how they stand with
    the title, how they watch, two of their habits. From the same persona
    card the room's remarks come from, so the dial and the chat are the
    same person."""
    try:
        from migration.digital_twin_pilot import _card_for
        card = _card_for(twin or {}, r, session_seed)
    except Exception:
        card = {}
    bits = []
    if card.get('relationship_to_subject'):
        bits.append(str(card['relationship_to_subject']))
    habits = [str(h) for h in (card.get('habits') or []) if h]
    for h in habits[1:2] + habits[-2:]:   # loyalty, binge or appointment, second screen
        if h and h not in bits:
            bits.append(h)
    if card.get('occupation'):
        bits.append(str(card['occupation']))
    txt = '; '.join(bits)
    return txt[:260]


def _current_dials(sess: dict, roster: list) -> dict:
    live = sess.get('live') or {}
    out = {}
    for r in roster:
        sno = str(r.get('seat'))
        pts = (live.get('seat_dials') or {}).get(sno) or []
        out[sno] = float(pts[-1][1]) if pts else DEFAULT_DIAL
    return out


def _fake_read(sess: dict, roster: list, t: float, dials: dict) -> dict:
    """Deterministic stand-in when no model runs (tests)."""
    seed = f"{sess.get('id')}|{round(float(t))}"
    h = hashlib.sha256(seed.encode('utf-8')).hexdigest()
    out_dial = {}
    for i, r in enumerate(roster):
        sno = str(r.get('seat'))
        step = (int(h[(i * 2) % 60:(i * 2) % 60 + 2], 16) % 9) - 4
        out_dial[sno] = max(0.0, min(100.0, dials.get(sno, DEFAULT_DIAL) + step))
    reactions = []
    if int(h[-2:], 16) % 3 == 0 and roster:
        r = roster[int(h[-4:-2], 16) % len(roster)]
        kind = EVENTS[int(h[-6:-4], 16) % len(EVENTS)]
        reactions.append({'seat': r.get('seat'), 'type': kind, 'line': f"{r.get('name')} reacted at {_clock(t)}."})
    return {'caption': f"On screen at {_clock(t)}.", 'tone': TONES[int(h[:2], 16) % len(TONES)], 'joke': False, 'setting': '', 'shift': False,
            'dial': out_dial, 'reactions': reactions}


def _unit(*parts) -> float:
    """A salted uniform in [0, 1)."""
    h = hashlib.sha256('|'.join(str(x) for x in parts).encode('utf-8')).hexdigest()
    return int(h[:12], 16) / float(16 ** 12)


def _seat_fit(twin: dict, seat: dict, tone: str, joke: bool, session_seed: str) -> float:
    """Where this seat's own habits put this moment: the pilot's tone fit
    (age, gender, orientation, lifecycle, the twin's traits) on the frame's
    tone. 0 when the fit cannot be read."""
    try:
        from migration.digital_twin_pilot import _card_for, _tone_fit
        card = _card_for(twin or {}, seat, session_seed)
        return float(_tone_fit(card, {'tone': tone or '', 'joke': bool(joke), 'act': '', 'cue': ''}, twin or {}))
    except Exception:
        return 0.0


HERD_SHARE = 0.8          # this share of the room moving one way on an ordinary frame is the room, not the people
HERD_KEEP = 0.45          # how much of that shared move survives
FIT_GAIN = 0.55           # points per frame per point of (centered) tone fit
FIT_CAP = 4.5
DRIFT_GAIN = 0.35         # a seat the last frames did nothing for keeps sliding
THOUGHTS_KEEP = 24        # the last thoughts kept per seat


def shape_moves(sess: dict, twin: dict, roster: list, rec: dict, before: dict, wanted: dict, trusted: bool = False) -> dict:
    """Each seat's reading after the model's read.

    ``trusted`` (the read gave each seat its own thought and the move that
    follows from it): the moves stand as the person's own. The one guard
    is a lazy read, most of the room moving one way by about the same
    amount on an ordinary frame; that is the room, not the people, and
    only then is the shared part damped and each seat moved by its own
    fit to the moment's tone.

    Not trusted (an old-shape read, or the stand-in when no model runs):
    the earlier shaping. When the read herds the shared part is damped;
    every seat then moves by its own fit to this moment's tone, centered
    across the room so the room's level is still the read's, plus a slow
    drift for a seat the last frames did nothing for, plus a small salted
    walk. Returns {seat: value}."""
    if not roster:
        return dict(wanted)
    tone = str((rec or {}).get('tone') or '')
    joke = bool((rec or {}).get('joke'))
    shared = bool(joke or tone == 'wow' or (rec or {}).get('shift'))
    seed = f"{sess.get('id')}|{(rec or {}).get('t')}"
    snos = [str(r.get('seat')) for r in roster]
    moves = {s: float(wanted.get(s, before.get(s, DEFAULT_DIAL))) - float(before.get(s, DEFAULT_DIAL)) for s in snos}
    n = len(snos)
    ups = sum(1 for s in snos if moves[s] > 0.4)
    downs = sum(1 for s in snos if moves[s] < -0.4)
    herd = n >= 4 and not shared and max(ups, downs) >= HERD_SHARE * n
    if trusted:
        spread = (max(moves.values()) - min(moves.values())) if moves else 0.0
        if not (herd and spread < 2.5):
            return {s: round(max(0.0, min(100.0, float(wanted.get(s, before.get(s, DEFAULT_DIAL))))), 2) for s in snos}
    if herd:
        common = sum(moves.values()) / float(n)
        for s in snos:
            moves[s] = common * HERD_KEEP + (moves[s] - common)
    fits = {}
    for r in roster:
        fits[str(r.get('seat'))] = _seat_fit(twin, r, tone, joke, str(sess.get('id') or ''))
    mean_fit = sum(fits.values()) / float(n)
    live = sess.get('live') or {}
    out = {}
    for r in roster:
        s = str(r.get('seat'))
        dev = max(-FIT_CAP, min(FIT_CAP, (fits[s] - mean_fit) * FIT_GAIN))
        # the drift: the last three readings for this seat all fell or held while the fit stays under the room's
        pts = (live.get('seat_dials') or {}).get(s) or []
        drift = 0.0
        if len(pts) >= 3 and fits[s] < mean_fit and all(float(pts[i][1]) <= float(pts[i - 1][1]) + 0.2 for i in range(len(pts) - 2, len(pts))):
            drift = -DRIFT_GAIN * (1.0 + _unit(seed, s, 'drift'))
        walk = 0.0 if trusted else (_unit(seed, s, 'walk') - 0.5) * 1.6
        if trusted:
            drift = 0.0
        v = float(before.get(s, DEFAULT_DIAL)) + moves[s] + dev + drift + walk
        out[s] = round(max(0.0, min(100.0, v)), 2)
    return out


def frame(sess: dict, twin: dict, t: float, image_b64: str, *, usage_extras: Optional[dict] = None, media_type: str = 'image/jpeg') -> dict:
    """One sampled frame at clock ``t``. Reads it, moves the seats, and
    returns what changed so the page can paint without a reload:
    {t, caption, events, dial, splits, split_meta, seats, skipped}.

    ``seats`` is every seat's own reading at this frame ({seat: value})
    and ``split_meta`` names each split row ({key: {label, group, n}}),
    so the page can draw the Cohorts and People views while the room is
    still watching (Jenna 2026-11-04: the dial views switch live)."""
    live = sess.setdefault('live', {'on': True, 'frames': [], 'seat_dials': {}, 'events': []})
    roster = (sess.get('group') or {}).get('roster') or []
    try:
        t = max(0.0, float(t or 0))
    except (TypeError, ValueError):
        t = 0.0
    frames = live.setdefault('frames', [])
    if frames and t - float(frames[-1].get('t') or 0) < FRAME_MIN_GAP_S:
        return {'skipped': True, 't': t}
    raw = str(image_b64 or '')
    if ',' in raw[:64] and raw.lstrip().startswith('data:'):
        raw = raw.split(',', 1)[1]
    try:
        nbytes = len(base64.b64decode(raw, validate=False)) if raw else 0
    except Exception:
        nbytes = 0
    if not raw or nbytes <= 0 or nbytes > FRAME_MAX_BYTES:
        return {'skipped': True, 't': t, 'error': 'frame too large' if nbytes > FRAME_MAX_BYTES else 'no frame'}
    dials = _current_dials(sess, roster)
    meta = sess.get('media') or {}
    recent = frames[-4:]
    seed = str(sess.get('id') or '')
    seats_txt = '\n'.join(f"seat {r.get('seat')}: {r.get('name')}, {r.get('age')}, {r.get('city')}" + (f", cohort: {r.get('segment')}" if r.get('segment') else '') + f"; now at {dials.get(str(r.get('seat')), DEFAULT_DIAL):.0f}. {_seat_line(r)} " + _seat_who(twin, r, seed) for r in roster)
    # the cast on screen, by face (digital_twin_cast): prepared once per test, read on every frame
    cast_hits, cast_note = [], ''
    try:
        from migration import digital_twin_cast as _CAST
        people = _CAST.people_for(sess, title=str(meta.get('title') or sess.get('title') or ''), usage_extras=usage_extras)
        if people:
            cast_hits = _CAST.faces_in_frame(raw, people)
            cast_note = '\nThe cast list for this show: ' + ', '.join(_CAST.label(p) for p in people) + '. ' + _CAST.on_screen_line(cast_hits)
    except Exception as e:
        print(f"[twin-live] cast read skipped: {e}")
    head = (f"The show: {meta.get('title') or sess.get('title') or 'the cut'} ({sess.get('mode') or 'pilot'}" + (f", {sess.get('genre')}" if sess.get('genre') else '') + ")."
            + (f" Logline: {str(sess.get('logline'))[:240]}." if sess.get('logline') else '')
            + f"\nClock: {_clock(t)}." + (" What the room has seen so far:\n" + '\n'.join(f"{_clock(f.get('t'))}: {f.get('caption')}" for f in recent) if recent else ' This is the first frame.')
            + cast_note
            + f"\nThe seats:\n{seats_txt}")
    data = None
    if not (regression_mode() or not model_available()):
        blocks = [{'type': 'text', 'text': head}, {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': raw}}]
        data, model, stats = _vision_json(LIVE_SYSTEM, blocks, max_tokens=2200, usage_extras=usage_extras, surface='twin_pilot_live')
        live['calls'] = int(live.get('calls') or 0) + 1
        if not isinstance(data, dict):
            live['failed'] = int(live.get('failed') or 0) + 1
            data = None
    if data is None:
        data = _fake_read(sess, roster, t, dials)
    caption = scrub(str(data.get('caption') or '').strip())[:160] or f"On screen at {_clock(t)}."
    tone = str(data.get('tone') or '').strip().lower()
    tone = tone if tone in TONES else ''
    rec = {'t': round(t, 2), 'caption': caption, 'tone': tone, 'joke': bool(data.get('joke')), 'setting': scrub(str(data.get('setting') or ''))[:40], 'shift': bool(data.get('shift'))}
    if cast_hits:
        rec['cast'] = [str(h.get('actor') or '') for h in cast_hits if h.get('actor')]
    frames.append(rec)
    # the dials: every seat moves (a missing seat keeps its reading). The read gives each seat its own thought and
    # the move that follows from it (Jenna 2026-11-03: "each seat should respond the way that persona would respond;
    # each person's dial should match what they would think, then their cohort is the aggregate of that").
    given = data.get('dial') if isinstance(data.get('dial'), dict) else {}
    given_seats = data.get('seats') if isinstance(data.get('seats'), dict) else {}
    seat_dials = live.setdefault('seat_dials', {})
    thoughts = live.setdefault('thoughts', {})
    vals = []
    by_gender = {'F': [], 'M': []}
    by_cohort = {}
    try:
        from migration.digital_twin_pilot import cohort_labels, cohort_key
        cohorts = cohort_labels(roster)
    except Exception:
        cohorts, cohort_key = [], None
    wanted = {}
    thought_n = 0
    for r in roster:
        sno = str(r.get('seat'))
        row = given_seats.get(sno)
        if row is None and sno.isdigit():
            row = given_seats.get(int(sno))
        cur = float(dials.get(sno, DEFAULT_DIAL))
        if isinstance(row, dict) and row.get('move') is not None:
            try:
                mv = max(-12.0, min(12.0, float(row.get('move'))))
            except (TypeError, ValueError):
                mv = 0.0
            v = cur + mv
            thought_n += 1
            say = scrub(' '.join(str(row.get('say') or '').split()))[:110]
            if say:
                lst = thoughts.setdefault(sno, [])
                lst.append([round(t, 2), say])
                del lst[:-THOUGHTS_KEEP]
        else:
            try:
                v = float(given.get(sno, given.get(int(sno) if sno.isdigit() else sno, cur)))
            except (TypeError, ValueError):
                v = cur
        wanted[sno] = max(0.0, min(100.0, v))
    trusted = roster and thought_n >= max(1, int(0.75 * len(roster)))   # most seats came with their own thought
    shaped = shape_moves(sess, twin, roster, rec, dials, wanted, trusted=bool(trusted))
    seat_now = {}
    for r in roster:
        sno = str(r.get('seat'))
        v = shaped.get(sno, wanted.get(sno, dials.get(sno, DEFAULT_DIAL)))
        seat_dials.setdefault(sno, []).append([round(t, 2), round(v, 2)])
        seat_now[sno] = round(v, 2)
        vals.append(v)
        g = str(r.get('gender') or '').upper()[:1]
        if g in by_gender:
            by_gender[g].append(v)
        lab = str(r.get('segment') or '')
        if lab in cohorts:
            by_cohort.setdefault(lab, []).append(v)
    room = round(sum(vals) / len(vals), 2) if vals else DEFAULT_DIAL
    dial = sess.setdefault('dial', {'t': [], 'value': [], 'splits': {}})
    dial.setdefault('t', []).append(round(t, 2))
    dial.setdefault('value', []).append(room)
    splits = dial.setdefault('splits', {})
    split_now, split_meta = {}, {}
    split_defs = [(cohort_key(lab), lab, 'cohort', by_cohort.get(lab) or []) for lab in cohorts] if cohort_key else []
    split_defs += [('women', 'Women', 'gender', by_gender.get('F') or []), ('men', 'Men', 'gender', by_gender.get('M') or [])]
    for key, label, grp, xs in split_defs:
        if not xs:
            continue
        v = round(sum(xs) / len(xs), 2)
        sp = splits.setdefault(key, {'label': label, 'group': grp, 'n': len(xs), 'value': []})
        # the split series rides the same clock as the room: pad to the room's length so the page can draw it point for point
        while len(sp['value']) < len(dial['value']) - 1:
            sp['value'].append(sp['value'][-1] if sp['value'] else v)
        sp['value'].append(v)
        sp['n'] = len(xs)
        split_now[key] = v
        split_meta[key] = {'label': label, 'group': grp, 'n': len(xs)}
    # the reactions
    by_seat = {str(r.get('seat')): r for r in roster}
    new_events = []
    for rx in (data.get('reactions') if isinstance(data.get('reactions'), list) else [])[:MAX_REACTIONS]:
        if not isinstance(rx, dict):
            continue
        r = by_seat.get(str(rx.get('seat')))
        kind = str(rx.get('type') or '').strip().lower()
        line = scrub(' '.join(str(rx.get('line') or '').split()))[:110]
        if not r or kind not in EVENTS or not line:
            continue
        ev = {'t': round(t, 2), 'type': kind, 'seat': r.get('seat'), 'name': r.get('name'), 'age': r.get('age'), 'city': r.get('city'), 'line': line, 'live': True}
        new_events.append(ev)
    live.setdefault('events', []).extend(new_events)
    sess.setdefault('events', []).extend(new_events)
    sess.setdefault('scenes', []).append({'id': len(frames) - 1, 't_start': round(t, 2), 't_end': round(t + 12.0, 2), 'caption': caption, 'tone': tone, 'act': '',
                                          'cast': list(rec.get('cast') or [])})
    if len(sess['scenes']) >= 2:
        sess['scenes'][-2]['t_end'] = round(t, 2)
    sess['watch'] = {'source': 'live', 'persona': False, 'captions_n': len(frames), 'scenes_n': len(frames)}
    sess['updated_at'] = _now()
    return {'t': round(t, 2), 'caption': caption, 'events': new_events, 'dial': {'t': round(t, 2), 'value': room}, 'splits': split_now, 'split_meta': split_meta,
            'seats': seat_now, 'frames': len(frames), 'cast': list(rec.get('cast') or [])}


# ------------------------------------------------------------ the finish
def analysis_from_live(sess: dict) -> Optional[dict]:
    """The frames as the scene list the standing read takes (what
    ``digital_twin_cut.analyze_cut`` returns for a file). None when the
    room saw nothing."""
    live = sess.get('live') or {}
    frames = [f for f in (live.get('frames') or []) if isinstance(f, dict)]
    if not frames:
        return None
    frames.sort(key=lambda f: float(f.get('t') or 0))
    scenes, captions = [], {}
    for i, f in enumerate(frames):
        a = float(f.get('t') or 0)
        b = float(frames[i + 1].get('t') or 0) if i + 1 < len(frames) else a + 12.0
        if b <= a:
            b = a + 1.0
        scenes.append({'id': i, 't_start': round(a, 3), 't_end': round(b, 3)})
        captions[i] = {'caption': str(f.get('caption') or '')[:160], 'tone': str(f.get('tone') or ''), 'joke': bool(f.get('joke')),
                       'setting': str(f.get('setting') or '')[:40], 'people': 0, 'text': ''}
    runtime = float(scenes[-1]['t_end'])
    return {'scenes': scenes, 'captions': captions, 'runtime_s': runtime, 'source': 'file', 'tools': {'live': True},
            'notes': [f"{len(frames)} frames read live while the room watched."]}


def finish(sess: dict, twins_by_key: dict, *, usage_extras: Optional[dict] = None, s3=None) -> dict:
    """The full read on what the room actually watched, then stopped.
    The live reactions and dial stay on the test as its record."""
    from migration import digital_twin_pilot as PI
    live = sess.setdefault('live', {'on': True, 'frames': [], 'seat_dials': {}, 'events': []})
    live['finishing_at'] = _now()
    keep = {'events': list(sess.get('events') or []), 'dial': json.loads(json.dumps(sess.get('dial') or {})), 'seat_dials': json.loads(json.dumps(live.get('seat_dials') or {}))}
    an = analysis_from_live(sess)
    media = sess.setdefault('media', {})
    dur = float((an or {}).get('runtime_s') or media.get('runtime_s') or 0)
    try:
        PI.prepare_watch(sess, twins_by_key, duration_s=dur, title=str(media.get('title') or sess.get('title') or ''), analysis=an, usage_extras=usage_extras, s3=s3)
    except Exception:
        traceback.print_exc()
    live['record'] = keep
    live['finished_at'] = _now()
    live['finishing_at'] = None
    if sess.get('watch') is None or not isinstance(sess.get('watch'), dict):
        sess['watch'] = {}
    sess['watch']['live'] = True
    sess['watch']['source'] = 'live'
    PI.stop_session(sess)
    return sess


def finish_in_background(sess: dict, twins_by_key: dict, *, key: str, payload: dict, save, usage_extras: Optional[dict] = None, s3=None) -> None:
    """Score on a thread so Stop answers at once; the page polls the
    test until it reads stopped."""
    sess['status'] = 'preparing'
    sess['prepare_stage'] = 'The room is scoring what it watched.'
    sess['prepare_note'] = 'The room is scoring what it watched.'
    live = sess.setdefault('live', {'on': True, 'frames': [], 'seat_dials': {}, 'events': []})
    live['finishing_at'] = _now()
    sess['updated_at'] = _now()

    def run():
        try:
            finish(sess, twins_by_key, usage_extras=usage_extras, s3=s3)
            try:
                from migration import digital_twin_pilot as PI
                if sess.get('scorecard'):
                    PI.record_norms(sess, s3=s3)
            except Exception:
                traceback.print_exc()
        except Exception:
            traceback.print_exc()
            sess['status'] = 'stopped'
            sess['prepare_note'] = 'The room could not finish its scoring this time.'
            (sess.get('live') or {})['finishing_at'] = None
        try:
            save(key, payload)
        except Exception:
            traceback.print_exc()

    threading.Thread(target=run, name=f"pilot-live-finish-{sess.get('id')}", daemon=True).start()


def unstick(sess: dict) -> bool:
    """A finish that never came back (the process went away) flips to
    stopped so the page is never held. True when it changed the test."""
    live = sess.get('live') if isinstance(sess.get('live'), dict) else None
    if not live or sess.get('status') != 'preparing' or not live.get('finishing_at'):
        return False
    try:
        started = calendar.timegm(time.strptime(str(live['finishing_at']), '%Y-%m-%dT%H:%M:%SZ'))
    except Exception:
        return False
    if time.time() - started < FINISH_STALE_S:
        return False
    live['finishing_at'] = None
    sess['status'] = 'stopped'
    sess['prepare_note'] = 'The room could not finish its scoring this time. Press Stop again to score it.'
    sess['updated_at'] = _now()
    return True


__all__ = ['FRAME_MIN_GAP_S', 'FRAME_MAX_BYTES', 'EVENTS', 'is_live', 'seat_live', 'start_live', 'frame', 'analysis_from_live', 'finish', 'finish_in_background', 'unstick']
