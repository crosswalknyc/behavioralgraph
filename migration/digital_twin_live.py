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
    "\"dial\": {\"<seat>\": number}, \"reactions\": [{\"seat\": number, \"type\": string, \"line\": string}]}\n"
    "caption: what is on screen in one plain sentence (who, where, what is happening), 140 characters max, present tense. "
    "tone: one of funny, tender, tense, action, wow, quiet, warm, curious. joke: true only when the frame is a comic beat. "
    "setting: two or three words. shift: true when this is a different scene from the last caption.\n"
    "dial: a 0 to 100 enjoyment reading for EVERY seat listed, moved from each seat's current reading by what this moment would do "
    "to that specific person given who they are. Small moves for ordinary moments (1 to 4 points), bigger for a laugh, a reveal, a "
    "dull stretch or a confusing one (5 to 12). Never the same number for everyone.\n"
    "reactions: 0 to 3 seats who visibly reacted to THIS moment, type one of love, laugh, lean-in, confuse, check-out, wow, and a "
    "line of 90 characters max in that person's first-person voice about what they just saw. Most frames earn 0 or 1 reaction; "
    "a quiet frame earns none. Never invent what is not in the frame. Never name the actors; use what the screen shows."
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


def frame(sess: dict, twin: dict, t: float, image_b64: str, *, usage_extras: Optional[dict] = None, media_type: str = 'image/jpeg') -> dict:
    """One sampled frame at clock ``t``. Reads it, moves the seats, and
    returns what changed so the page can paint without a reload:
    {t, caption, events, dial, splits, skipped}."""
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
    seats_txt = '\n'.join(f"seat {r.get('seat')}: {r.get('name')}, {r.get('age')}, {r.get('city')}; now at {dials.get(str(r.get('seat')), DEFAULT_DIAL):.0f}. {_seat_line(r)}" for r in roster)
    head = (f"The show: {meta.get('title') or sess.get('title') or 'the cut'} ({sess.get('mode') or 'pilot'}" + (f", {sess.get('genre')}" if sess.get('genre') else '') + ")."
            + (f" Logline: {str(sess.get('logline'))[:240]}." if sess.get('logline') else '')
            + f"\nClock: {_clock(t)}." + (" What the room has seen so far:\n" + '\n'.join(f"{_clock(f.get('t'))}: {f.get('caption')}" for f in recent) if recent else ' This is the first frame.')
            + f"\nThe seats:\n{seats_txt}")
    data = None
    if not (regression_mode() or not model_available()):
        blocks = [{'type': 'text', 'text': head}, {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': raw}}]
        data, model, stats = _vision_json(LIVE_SYSTEM, blocks, max_tokens=900, usage_extras=usage_extras, surface='twin_pilot_live')
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
    frames.append(rec)
    # the dials: every seat moves (a missing seat keeps its reading)
    given = data.get('dial') if isinstance(data.get('dial'), dict) else {}
    seat_dials = live.setdefault('seat_dials', {})
    vals = []
    by_gender = {'F': [], 'M': []}
    for r in roster:
        sno = str(r.get('seat'))
        try:
            v = float(given.get(sno, given.get(int(sno) if sno.isdigit() else sno, dials.get(sno, DEFAULT_DIAL))))
        except (TypeError, ValueError):
            v = dials.get(sno, DEFAULT_DIAL)
        v = max(0.0, min(100.0, v))
        seat_dials.setdefault(sno, []).append([round(t, 2), round(v, 2)])
        vals.append(v)
        g = str(r.get('gender') or '').upper()[:1]
        if g in by_gender:
            by_gender[g].append(v)
    room = round(sum(vals) / len(vals), 2) if vals else DEFAULT_DIAL
    dial = sess.setdefault('dial', {'t': [], 'value': [], 'splits': {}})
    dial.setdefault('t', []).append(round(t, 2))
    dial.setdefault('value', []).append(room)
    splits = dial.setdefault('splits', {})
    split_now = {}
    for key, label, g in (('women', 'Women', 'F'), ('men', 'Men', 'M')):
        xs = by_gender.get(g) or []
        if not xs:
            continue
        v = round(sum(xs) / len(xs), 2)
        sp = splits.setdefault(key, {'label': label, 'group': 'gender', 'n': len(xs), 'value': []})
        # the split series rides the same clock as the room: pad to the room's length so the page can draw it point for point
        while len(sp['value']) < len(dial['value']) - 1:
            sp['value'].append(sp['value'][-1] if sp['value'] else v)
        sp['value'].append(v)
        sp['n'] = len(xs)
        split_now[key] = v
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
    sess.setdefault('scenes', []).append({'id': len(frames) - 1, 't_start': round(t, 2), 't_end': round(t + 12.0, 2), 'caption': caption, 'tone': tone, 'act': ''})
    if len(sess['scenes']) >= 2:
        sess['scenes'][-2]['t_end'] = round(t, 2)
    sess['watch'] = {'source': 'live', 'persona': False, 'captions_n': len(frames), 'scenes_n': len(frames)}
    sess['updated_at'] = _now()
    return {'t': round(t, 2), 'caption': caption, 'events': new_events, 'dial': {'t': round(t, 2), 'value': room}, 'splits': split_now, 'frames': len(frames)}


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
