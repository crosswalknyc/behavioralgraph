"""The room watches the cut: the model layer behind Pilot Testing
(2026-10-28).

Three jobs, each batched so a watch costs a handful of calls:

1. ``caption_frames``: one caption per sampled frame (several frames a
   call, a vision-capable model), with a tone, whether a joke is landing,
   the setting, and a genre guess per batch.
2. ``persona_watch``: every seat reads the cut act by act. One call per
   act carries ALL the seats and the act's scenes (caption, dialogue,
   tone, cues) plus each seat's trait prior at the act start; the seat
   returns a dial level per scene, an event when one fires (loved it,
   laughed, leaned in, lost, checked out, wow, confused) and a short
   line in its own voice at scene level. Acts run in parallel threads
   under a hard budget; an act that misses lands on the prior.
3. ``battery``: the studio scorecard answered once per seat at the end,
   from the persona and its own dial; ``guide``: the moderator guide
   drafted from the beat sheet before Play.

Model plumbing rides ``digital_twin_room.Models`` (fake models in
tests, REGRESSION_TEST_MODE never calls a model, the cost trail). The
vision call is the one place that speaks to the SDK directly, because
the shared text clients take a string, not image blocks.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from typing import Callable, Optional

from migration.digital_twin_engine import clamp, unit
from migration.digital_twin_room import Models, scrub, regression_mode, model_available, persona_card, _card_text, MODELS
from migration import digital_twin_story as _STORY

TONES = ('funny', 'tender', 'tense', 'action', 'wow', 'quiet', 'warm', 'curious')
EVENTS = ('love', 'laugh', 'lean-in', 'confuse', 'check-out', 'wow')
GENRES = ('procedural', 'drama', 'comedy', 'action', 'thriller', 'horror', 'romance', 'family', 'animation', 'documentary', 'unscripted')
ACTS = ('open', 'setup', 'middle', 'turn', 'close', 'end')
BUDGET_S = {'caption': 240.0, 'act': 170.0, 'battery': 90.0, 'guide': 60.0,
            'story': 480.0, 'recap_wait': 90.0}   # the parts in order with the story so far (2026-11-24): the whole read, and the longest a part waits for its recap
CAPTION_BATCH = 6
CAPTION_WORKERS = 6
VISION_MODELS = [m for m in ((os.environ.get('DTIQ_WATCH_VISION_MODEL') or '').strip(), 'claude-sonnet-4-6', 'claude-haiku-5-5') if m]

# The battery studios quote. Option ids are stable; labels are what the page prints.
BATTERY = {
    'overall': {'label': 'Overall rating', 'kind': 'rating', 'options': [('a', 'Excellent', 'yes'), ('b', 'Very good', 'yes'), ('c', 'Good', 'maybe'), ('d', 'Fair', 'no'), ('e', 'Poor', 'no')]},
    'recommend': {'label': 'Would recommend', 'kind': 'recommend', 'options': [('a', 'Definitely', 'yes'), ('b', 'Probably', 'maybe'), ('c', 'Probably not', 'no'), ('d', 'Definitely not', 'no')]},
    'finish': {'label': 'Would finish it', 'kind': 'yes_no', 'options': [('a', 'Yes', 'yes'), ('b', 'No', 'no')]},
    'tune_in': {'label': 'Would watch the next one', 'kind': 'yes_no', 'options': [('a', 'Yes', 'yes'), ('b', 'No', 'no')]},
    'urgency': {'label': 'How soon', 'kind': 'urgency', 'options': [('a', 'Opening weekend', 'yes'), ('b', 'First two weeks', 'yes'), ('c', 'When I get a chance', 'maybe'), ('d', 'Wait for home', 'no')]},
    'urgency_pilot': {'label': 'How soon', 'kind': 'urgency', 'options': [('a', 'Live or the same week', 'yes'), ('b', 'Later, when it suits me', 'maybe'), ('c', 'Probably never', 'no')]},
    'expectations': {'label': 'Against expectations', 'kind': 'expectations', 'options': [('a', 'Better than I expected', 'yes'), ('b', 'About what I expected', 'maybe'), ('c', 'Worse than I expected', 'no')]},
    'full_price': {'label': 'Worth full price', 'kind': 'yes_no', 'options': [('a', 'Yes', 'yes'), ('b', 'No', 'no')]},
}
# the question label per format (finish / tune-in / urgency read differently for a feature and a pilot)
BATTERY_FOR = {
    'feature': ('overall', 'recommend', 'finish', 'urgency', 'expectations', 'full_price'),
    'pilot': ('overall', 'recommend', 'finish', 'tune_in', 'urgency_pilot', 'expectations'),
    'episode': ('overall', 'recommend', 'finish', 'tune_in', 'urgency_pilot', 'expectations'),
    'trailer': ('overall', 'recommend', 'tune_in', 'urgency', 'expectations'),
    # the screening modes (2026-10-28): a title or key art option set, a script read, a concept
    'title': ('overall', 'recommend', 'tune_in'),
    'key_art': ('overall', 'recommend', 'tune_in'),
    'script': ('overall', 'recommend', 'finish', 'tune_in', 'expectations'),
    'concept': ('overall', 'recommend', 'tune_in', 'expectations'),
}
FINISH_LABEL = {'feature': 'Would finish the film', 'pilot': 'Would finish the episode', 'episode': 'Would finish the season', 'trailer': 'Would watch it',
                'script': 'Would finish the script'}
TUNE_IN_LABEL = {'pilot': 'Would watch a second episode', 'episode': 'Would watch the next episode', 'trailer': 'Would watch the title',
                 'title': 'Would watch a show with this title', 'key_art': 'Would watch the show on this art', 'script': 'Would watch it made', 'concept': 'Would watch it'}
OVERALL_LABEL = {'title': 'Rating of the title set', 'key_art': 'Rating of the art', 'concept': 'Rating of the idea'}


def act_of(frac: float) -> str:
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


def _clock(t) -> str:
    try:
        s = max(0, int(round(float(t))))
    except (TypeError, ValueError):
        s = 0
    return f"{s // 60}:{s % 60:02d}"


def _parse(raw):
    if isinstance(raw, dict):
        return raw
    s = str(raw or '').strip()
    if not s:
        return None
    s = re.sub(r"^```(?:json)?\s*|\s*```$", '', s, flags=re.S)
    try:
        return json.loads(s)
    except Exception:
        a, b = s.find('{'), s.rfind('}')
        if a >= 0 and b > a:
            try:
                return json.loads(s[a:b + 1])
            except Exception:
                return None
    return None


# ------------------------------------------------------------- prompts
CAPTION_SYSTEM = """You are the eyes of a test-screening room. You are shown a few frames from one cut, in order, each with its clock time and any dialogue heard around it. For each frame write what is on screen the way a sharp viewer would say it in one breath: who is there, what they are doing, the setting, the mood. A frame line may say who is on screen, confirmed by face, from the cut's cast list: name those people by those names, exactly as given (an actor's name, or 'actor as character' when the line gives one). Anyone the line does not name is described by role or look. Say when a joke is landing, a reveal is happening, a fight or chase is on, a quiet beat is holding, titles or credits are up, or the frame is black.
Rules: one caption per frame, under 18 words, present tense, concrete. tone is one of: funny, tender, tense, action, wow, quiet, warm, curious. joke is true only when the frame and the words read as a joke landing. Never guess the title. Never name an actor the frame line does not give, and never guess a name from a face yourself. No em dashes.
At the end guess the genre of the whole cut from everything you saw (one of: procedural, drama, comedy, action, thriller, horror, romance, family, animation, documentary, unscripted; procedural is a case-of-the-week cop, detective, forensic or legal show) and whether it reads as a pilot (TV episode), a feature, or a trailer.
Return JSON only: {"frames": [{"k": 1, "caption": "...", "tone": "...", "joke": false, "setting": "<two or three words>", "people": <count>, "text_on_screen": "<words on screen or empty>"}], "genre_guess": "...", "format_guess": "pilot|feature|trailer"}"""

WATCH_SYSTEM = """You are running a test screening. The people in the room are described to you one by one (a card each: who they are, their habits, how they came to this title). They are watching ONE act of a cut, scene by scene; for every scene you have the clock, what is on screen, the dialogue heard, the tone, and cues (a joke landing, a silence, music). Each person also carries a starting level on a 0 to 100 dial, which is where their habits alone would put them before the act plays.
For EVERY person and EVERY scene, decide where their dial sits after that scene (0 to 100): up when the scene lands for THEM, down when it drags, confuses or loses them. Move from the person, not the average: a 58 year old lesbian fan of the lead and a 22 year old man who found the show last month do not react the same; a second-screen scroller drifts in a quiet dialogue stretch; a loyal rewatcher leans in at a callback; a price-conscious skeptic needs more before a wow. Let the dial move in steps of a few points, bigger on a real turn, and let it carry forward scene to scene.
Mark an event ONLY when something notable happens for that person at that scene (at most two in five scenes per person): love (a moment they would rewind), laugh (they laughed out loud), lean-in (they sat forward), wow (something landed hard), confuse (they lost the thread: who, what, why), check-out (they drifted or looked at the phone). With an event, write one short line in that person's own spoken voice about THAT scene (under 20 words, first person, concrete: what they saw and how it hit; a cast member by the name the scene's description gives, never an actor name the scene does not give; no analysis words, never 'as a' anything). With no event, leave say empty.
After the act, one sentence per person on how the act sat with them.
Never mention being an AI, a twin, a persona or a character. No em dashes.
Return JSON only: {"seats": [{"seat": <id>, "scenes": [{"id": <scene id>, "dial": <0-100>, "event": "<love|laugh|lean-in|wow|confuse|check-out or empty>", "say": "<line or empty>"}], "act_note": "<one sentence>"}]}"""

BATTERY_SYSTEM = """You are the people in a test-screening room, filling out the card after the cut. For each person you have their card (who they are), what they watched (the cut's title, genre, length), how their own dial ran (where it sat on average, where it ended, their highest and lowest scenes with what was on screen, what they said during the watch). Answer each question on the card ONCE per person, in character and consistent with that person's dial and what they said: a dial that ended low does not hand out Excellent; a person who laughed all the way through and leaned in at the end recommends it. Spread honestly across the room; real rooms are not unanimous.
Then one sentence per person, in their own words, on why they rated it the way they did (under 22 words, spoken, no analysis words).
Return JSON only: {"seats": [{"seat": <id>, "answers": {"<question id>": "<option id>"}, "why": "<one sentence>", "best_scene": <scene id or null>, "worst_scene": <scene id or null>}]}"""

GUIDE_SYSTEM = """You are Prometheus, moderating a test screening. Before Play you draft the moderator guide from the beat sheet: five to eight questions to ask the room at planned timestamps (the end of the cold open, each act turn, the midpoint, the ending), each tied to what is on screen at that moment, written the way a moderator says it out loud (short, plain, one question each). Choose the timestamp from the scenes given (a scene start). Mark the kind: yes_no, open, rating, show_of_hands. Say in a few words why you would ask it there.
No em dashes. Never name actors. Return JSON only: {"questions": [{"t": <seconds>, "q": "...", "kind": "...", "why": "..."}]}"""


# --------------------------------------------------------- the captions
def _frame_bytes(path: str) -> Optional[bytes]:
    try:
        with open(path, 'rb') as fh:
            b = fh.read()
        return b if len(b) > 200 else None
    except Exception:
        return None


def _vision_json(system: str, blocks: list, *, max_tokens: int = 1800, usage_extras: Optional[dict] = None, surface: str = 'twin_watch_caption') -> tuple:
    """A vision call through the SDK: (data or None, model, stats).
    Never raises."""
    try:
        from claude_client import get_claude_client, _record_tagged_usage, system_blocks
    except Exception:
        return None, '', {}
    client = get_claude_client()
    if client is None:
        return None, '', {}
    tag = (surface, 'chatbot', usage_extras) if usage_extras else (surface, 'chatbot')
    for model in VISION_MODELS:
        t0 = time.monotonic()
        try:
            resp = client.messages.create(model=model, max_tokens=max_tokens, system=system_blocks(system), messages=[{'role': 'user', 'content': blocks}])
        except Exception as e:
            print(f"[twin-watch] vision {model} failed: {str(e)[:160]}; trying next")
            continue
        try:
            _record_tagged_usage(tag, model, resp, duration_s=time.monotonic() - t0)
        except Exception:
            pass
        text = ''
        for b in (getattr(resp, 'content', None) or []):
            if getattr(b, 'text', None):
                text += b.text
        data = _parse(text)
        u = getattr(resp, 'usage', None)
        stats = {'model': model, 'input_tokens': int(getattr(u, 'input_tokens', 0) or 0), 'output_tokens': int(getattr(u, 'output_tokens', 0) or 0)}
        if isinstance(data, dict):
            return data, model, stats
    return None, '', {}


def _dialogue_near(transcript: Optional[list], t: float, span: float = 6.0, limit: int = 220) -> str:
    if not transcript:
        return ''
    parts = []
    for row in transcript:
        try:
            rt = float(row.get('t') or 0)
        except (TypeError, ValueError):
            continue
        if abs(rt - t) <= span:
            parts.append(str(row.get('text') or '').strip())
    return ' '.join(parts)[:limit]


def caption_frames(frames: list, *, models: Optional[Models] = None, transcript: Optional[list] = None, title: str = '', notes: str = '',
                   batch: int = CAPTION_BATCH, workers: int = CAPTION_WORKERS, budget_s: float = BUDGET_S['caption'], usage_extras: Optional[dict] = None,
                   log: Optional[Callable] = None, cast: Optional[list] = None, faces_fn: Optional[Callable] = None) -> dict:
    """{captions: {scene_id: {caption, tone, joke, setting, people, text, cast}},
    genre_votes: {genre: n}, format_votes: {...}, calls: n, failed: n}.
    Frames come from digital_twin_cut.sample_frames. With a fake model
    installed (tests) the frames travel as text and the fake answers;
    REGRESSION_TEST_MODE with no fake returns nothing (the beat sheet
    falls back to dialogue and timing).

    ``cast`` is the resolved cast (digital_twin_cast.people_for): each
    frame is read for their faces first and the frame line says who is
    on screen, so the caption names them (Jenna, 2026-11-02). ``faces_fn``
    is a test's stand-in for digital_twin_cast.faces_in_frame."""
    out = {'captions': {}, 'genre_votes': {}, 'format_votes': {}, 'calls': 0, 'failed': 0}
    frames = [f for f in (frames or []) if f and f.get('path') is not None]
    if not frames:
        return out
    models = models or Models(usage_extras=usage_extras)
    if models.fn is None and (regression_mode() or not model_available()):
        return out
    batches = [frames[i:i + max(1, batch)] for i in range(0, len(frames), max(1, batch))]
    lock = threading.Lock()
    cast_people = [p for p in (cast or []) if isinstance(p, dict)]
    cast_by_scene = {}
    if cast_people:
        try:
            from migration import digital_twin_cast as _CAST
            faces_fn = faces_fn or _CAST.faces_in_frame
            on_screen_line = _CAST.on_screen_line
            cast_names = _CAST.names
        except Exception:
            cast_people = []

    def one(group):
        lines = []
        blocks = []
        for k, f in enumerate(group, 1):
            t = float(f.get('t') or 0)
            line = f"Frame {k}: scene {f.get('scene_id')} at {_clock(t)}" + (f". Heard: {_dialogue_near(transcript, t)}" if _dialogue_near(transcript, t) else '')
            raw = _frame_bytes(str(f.get('path') or '')) if (models.fn is None or cast_people) else None
            if cast_people and raw is not None:
                try:
                    hits = faces_fn(raw, cast_people)
                except Exception:
                    hits = []
                line += ' ' + on_screen_line(hits)
                with lock:
                    cast_by_scene[int(f.get('scene_id'))] = cast_names(hits)
            lines.append(line)
            if models.fn is None:
                if raw is None:
                    continue
                blocks.append({'type': 'text', 'text': line})
                blocks.append({'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': base64.b64encode(raw).decode('ascii')}})
        head = f"The cut: {title or 'untitled'}." + (f" Notes: {notes[:200]}." if notes else '') + f" {len(group)} frames follow in order."
        t0 = time.time()
        if models.fn is not None:
            data, call = models.call('caption', CAPTION_SYSTEM, json.dumps({'head': head, 'frames': lines}), max_tokens=1800, temperature=0.2, tools=None)
            if log:
                log('caption', call)
        else:
            if not blocks:
                return group, None
            data, model, stats = _vision_json(CAPTION_SYSTEM, [{'type': 'text', 'text': head}] + blocks, max_tokens=1800, usage_extras=usage_extras)
            rec = models._log('caption', model, stats, t0, data is not None)
            if log:
                log('caption', rec)
        return group, data
    pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(batches))))
    futures = {pool.submit(one, g): g for g in batches}
    pending = set(futures)
    deadline = time.time() + budget_s
    while pending and time.time() < deadline:
        done, pending = wait(pending, timeout=max(0.2, min(1.0, deadline - time.time())), return_when=FIRST_COMPLETED)
        for f in done:
            try:
                group, data = f.result()
            except Exception as e:
                print(f"[twin-watch] caption batch failed: {e}")
                with lock:
                    out['failed'] += 1
                continue
            with lock:
                out['calls'] += 1
                if not isinstance(data, dict):
                    out['failed'] += 1
                    continue
                rows = data.get('frames') if isinstance(data.get('frames'), list) else []
                for k, fr in enumerate(group, 1):
                    row = next((r for r in rows if isinstance(r, dict) and int(r.get('k') or 0) == k), rows[k - 1] if k - 1 < len(rows) and isinstance(rows[k - 1], dict) else None)
                    if not row or not str(row.get('caption') or '').strip():
                        continue
                    tone = str(row.get('tone') or '').strip().lower()
                    out['captions'][int(fr.get('scene_id'))] = {
                        'caption': scrub(str(row.get('caption')).strip())[:160], 'tone': tone if tone in TONES else '',
                        'joke': bool(row.get('joke')), 'setting': scrub(str(row.get('setting') or ''))[:40],
                        'people': int(row.get('people') or 0) if str(row.get('people') or '').lstrip('-').isdigit() else 0,
                        'text': scrub(str(row.get('text_on_screen') or ''))[:80],
                        'cast': list(cast_by_scene.get(int(fr.get('scene_id')), []))}
                g = str(data.get('genre_guess') or '').strip().lower()
                if g in GENRES:
                    out['genre_votes'][g] = out['genre_votes'].get(g, 0) + 1
                fg = str(data.get('format_guess') or '').strip().lower()
                if fg in ('pilot', 'feature', 'trailer'):
                    out['format_votes'][fg] = out['format_votes'].get(fg, 0) + 1
    for f in pending:
        f.cancel()
        out['failed'] += 1
    pool.shutdown(wait=False)
    return out


def genre_from_votes(votes: dict, fallback: str = '') -> str:
    if votes:
        return max(votes.items(), key=lambda kv: (kv[1], kv[0]))[0]
    return fallback or ''


# ----------------------------------------------------- the persona watch
def _seat_card_text(card: dict, limit: int = 700) -> str:
    text = _card_text(card)
    return text if len(text) <= limit else text[:limit].rsplit('.', 1)[0] + '.'


def _scene_rows(scenes: list) -> list:
    rows = []
    for s in scenes:
        rows.append({'id': int(s.get('id')), 'clock': _clock(s.get('t_start')), 'seconds': round(float(s.get('t_end') or 0) - float(s.get('t_start') or 0), 1),
                     'on_screen': str(s.get('caption') or s.get('action') or '')[:160], 'dialogue': str(s.get('dialogue') or '')[:200],
                     'tone': s.get('tone') or '', 'cues': [c for c in ((['a joke lands'] if s.get('joke') else []) + (['silence'] if s.get('silence') else []) + ([str(s.get('cue'))] if s.get('cue') in ('music swell', 'credits') else [])) if c],
                     **({'cast_on_screen': [str(x) for x in s.get('cast') if str(x or '').strip()][:6]} if isinstance(s.get('cast'), list) and s.get('cast') else {})})
    return rows


def _act_user(meta: dict, dur: float, act: str, sc: list, room: list, starts: dict, *, story: Optional[dict] = None,
              memories: Optional[dict] = None, part_n: int = 0, parts: int = 0) -> str:
    """One part's call: the cut, the part, its scenes and the seats. With
    the story so far, the part also carries what came before and each
    seat's own watch so far (Jenna 2026-11-24)."""
    body = {'cut': {'title': meta.get('title') or 'the cut', 'genre': meta.get('genre') or '', 'notes': (meta.get('notes') or '')[:300],
                    'runtime': _clock(dur), 'rough_cut': bool(meta.get('rough_cut'))},
            'act': {'label': act, 'from': _clock(sc[0].get('t_start')), 'to': _clock(sc[-1].get('t_end'))},
            'scenes': _scene_rows(sc),
            'room': [dict(p, dial_at_act_start=starts[p['seat']], **({'your_watch_so_far': memories[p['seat']]} if (memories or {}).get(p['seat']) else {}))
                     for p in room]}
    if parts:
        body['act']['part'] = f"{part_n} of {parts}"
    if story:
        body['story_so_far'] = story
    return json.dumps(body, default=str)


def _merge_act(out: dict, data, act: str, ids: set, cards: dict, seats: Optional[list] = None) -> Optional[dict]:
    """The part's answer into ``out`` (by_seat, act_notes). Returns {seat:
    {'rows': {scene id: row}, 'note': str, 'waiting_for': str}} for the
    seats it answered, or None when the call did not answer. The caller
    holds the lock."""
    if not isinstance(data, dict) or not isinstance(data.get('seats'), list):
        return None
    allowed = set(seats) if seats is not None else None
    got = {}
    for row in data['seats']:
        if not isinstance(row, dict):
            continue
        try:
            seat = int(row.get('seat'))
        except (TypeError, ValueError):
            continue
        if seat not in cards or (allowed is not None and seat not in allowed):
            continue
        seat_map = out['by_seat'].setdefault(seat, {})
        mine = {}
        n_events = 0
        for sr in (row.get('scenes') or []):
            if not isinstance(sr, dict):
                continue
            try:
                sid = int(sr.get('id'))
                dial = float(sr.get('dial'))
            except (TypeError, ValueError):
                continue
            if sid not in ids:
                continue
            ev = str(sr.get('event') or '').strip().lower().replace('_', '-')
            ev = {'leanin': 'lean-in', 'checkout': 'check-out', 'lost': 'confuse', 'confused': 'confuse', 'loved': 'love', 'laughed': 'laugh'}.get(ev, ev)
            if ev not in EVENTS:
                ev = ''
            if ev:
                n_events += 1
            say = scrub(str(sr.get('say') or '').strip())[:160] if ev else ''
            seat_map[sid] = {'dial': round(clamp(dial, 0.0, 100.0), 1), 'event': ev, 'say': say}
            mine[sid] = seat_map[sid]
        note = scrub(str(row.get('act_note') or '').strip())[:200]
        if note:
            out['act_notes'].setdefault(seat, {})[act] = note
        got[seat] = {'rows': mine, 'note': note, 'waiting_for': scrub(str(row.get('waiting_for') or '').strip())[:120]}
    return got


def persona_watch(twin: dict, roster: list, scenes: list, *, cards: Optional[dict] = None, prior_start: Optional[dict] = None, meta: Optional[dict] = None,
                  models: Optional[Models] = None, session_seed: str = '', budget_s: Optional[float] = None, usage_extras: Optional[dict] = None,
                  log: Optional[Callable] = None, duration_s: float = 0) -> dict:
    """Every seat reads every scene, act by act. Returns {by_seat: {seat:
    {scene_id: {dial, event, say}}}, act_notes: {seat: {act: note}}, acts:
    n, failed_acts: [..], fallback: bool}. ``prior_start`` = {seat: {act:
    level}} from the trait curve. Nothing here invents a reaction when the
    model is off: the caller keeps the prior for those scenes.

    The parts are read in order (Jenna 2026-11-24): each part carries the
    story so far and every seat's own watch so far, so a payoff lands
    against the setup the seat saw (``digital_twin_story``; the result also
    carries ``stories`` and ``memory``). DTIQ_PILOT_STORY=0 reads every part
    on its own, all at once, as before."""
    out = {'by_seat': {}, 'act_notes': {}, 'acts': 0, 'failed_acts': [], 'fallback': False}
    if not roster or not scenes:
        out['fallback'] = True
        return out
    models = models or Models(usage_extras=usage_extras)
    if models.fn is None and (regression_mode() or not model_available()):
        out['fallback'] = True
        return out
    cards = cards or {int(r['seat']): persona_card(twin, r, session_seed) for r in roster}
    dur = float(duration_s or (scenes[-1].get('t_end') if scenes else 0) or 1)
    by_act = {}
    for s in scenes:
        a = str(s.get('act') or act_of(float(s.get('t_start') or 0) / dur))
        by_act.setdefault(a, []).append(s)
    meta = meta or {}
    room = [{'seat': int(r['seat']), 'name': r.get('name'), 'who': _seat_card_text(cards[int(r['seat'])])} for r in roster]
    if _STORY.story_enabled():
        _persona_watch_story(roster, cards, prior_start, meta, models, float(budget_s or BUDGET_S['story']), log, dur, by_act, room, out)
    else:
        _persona_watch_parallel(roster, cards, prior_start, meta, models, float(budget_s or BUDGET_S['act']), log, dur, by_act, room, out)
    if not out['by_seat']:
        out['fallback'] = True
    return out


def _persona_watch_parallel(roster, cards, prior_start, meta, models, budget_s, log, dur, by_act, room, out) -> None:
    """Every act on its own, all at once (the read before 2026-11-24)."""
    lock = threading.Lock()

    def one(act):
        sc = by_act[act]
        starts = {int(r['seat']): round(float(((prior_start or {}).get(int(r['seat'])) or {}).get(act, 50.0)), 1) for r in roster}
        user = _act_user(meta, dur, act, sc, room, starts)
        n_out = len(roster) * len(sc)
        max_tokens = int(clamp(600 + n_out * 16, 1800, 16000))
        data, call = models.call('watch', WATCH_SYSTEM, user, max_tokens=max_tokens, temperature=0.6, tools=None)
        if log:
            log(f"watch:{act}", call)
        return act, data
    acts = [a for a in ACTS if a in by_act]
    pool = ThreadPoolExecutor(max_workers=max(1, min(6, len(acts))))
    futures = {pool.submit(one, a): a for a in acts}
    pending = set(futures)
    deadline = time.time() + budget_s
    while pending and time.time() < deadline:
        done, pending = wait(pending, timeout=max(0.2, min(1.0, deadline - time.time())), return_when=FIRST_COMPLETED)
        for f in done:
            act = futures[f]
            try:
                _a, data = f.result()
            except Exception as e:
                print(f"[twin-watch] act {act} failed: {e}")
                data = None
            with lock:
                out['acts'] += 1
                if _merge_act(out, data, act, {int(s['id']) for s in by_act[act]}, cards) is None:
                    out['failed_acts'].append(act)
    for f in pending:
        f.cancel()
        out['failed_acts'].append(futures[f])
    pool.shutdown(wait=False)


WATCH_STORY_SYSTEM = (WATCH_SYSTEM[:WATCH_SYSTEM.rindex('Return JSON only:')] + _STORY.WATCH_CONTEXT + '\n'
                      + 'Return JSON only: {"seats": [{"seat": <id>, "scenes": [{"id": <scene id>, "dial": <0-100>, "event": "<love|laugh|lean-in|wow|confuse|check-out or empty>", '
                        '"say": "<line or empty>"}], "act_note": "<one sentence>", "waiting_for": "<what they are waiting to see, or empty>"}]}')


def _persona_watch_story(roster, cards, prior_start, meta, models, budget_s, log, dur, by_act, room, out) -> None:
    """The parts in order, each with what came before (Jenna 2026-11-24).

    The story so far needs only the scenes, so its chain runs ahead from
    the start: the story before each part is ready by the time the seats
    reach it. The seats read in fixed groups, each group its own chain of
    parts, so a seat's watch so far is always its own and no group waits
    on another; a big part splits its seats so each call answers in time
    (``digital_twin_story.ROWS_PER_CALL``)."""
    acts = [a for a in ACTS if a in by_act]
    rows_by_act = {a: _scene_rows(by_act[a]) for a in acts}
    scene_rows = {int(r['id']): r for a in acts for r in rows_by_act[a]}
    room_by_seat = {p['seat']: p for p in room}
    lock = threading.Lock()
    deadline = time.time() + budget_s
    stories = {acts[0]: _STORY.empty_story()}
    ready = {a: threading.Event() for a in acts}
    ready[acts[0]].set()
    out.update({'stories': {}, 'memory': {}, 'story_late': [], 'failed_parts': [], 'mode': 'story'})

    state = {'closed': False}

    def story_chain():
        st = _STORY.empty_story()
        try:
            for i, a in enumerate(acts[:-1]):
                if time.time() >= deadline:
                    break
                try:
                    st = _STORY.update_story(st, a, rows_by_act[a], models=models, meta=meta, log=log)
                except Exception as e:
                    print(f"[twin-watch] the story after {a} stopped: {e}")
                with lock:
                    stories[acts[i + 1]] = st
                ready[acts[i + 1]].set()
        finally:
            for ev in ready.values():   # a recap that stopped never holds a seat: the part reads with the latest story
                ev.set()

    seat_ids = [int(r['seat']) for r in roster]
    groups = _STORY.seat_groups(seat_ids, max(len(v) for v in by_act.values()))
    memory = {sid: _STORY.new_memory(((prior_start or {}).get(sid) or {}).get(acts[0], 50.0)) for sid in seat_ids}

    def chain(group):
        for i, a in enumerate(acts):
            left = deadline - time.time()
            if left <= 2.0:
                with lock:
                    out['failed_parts'].append({'part': a, 'seats': list(group)})
                continue
            if not ready[a].wait(timeout=max(1.0, min(BUDGET_S['recap_wait'], left - 2.0))):
                with lock:
                    if a not in out['story_late']:
                        out['story_late'].append(a)
            with lock:
                st = stories.get(a) or next((stories[b] for b in reversed(acts[:i]) if b in stories), None)
            sc = by_act[a]
            starts = {sid: round(float(((prior_start or {}).get(sid) or {}).get(a, 50.0)), 1) for sid in group}
            mem = {sid: _STORY.memory_text(memory[sid]) for sid in group} if i else {}
            user = _act_user(meta, dur, a, sc, [room_by_seat[sid] for sid in group], starts, story=_STORY.story_for_prompt(st), memories=mem,
                             part_n=i + 1, parts=len(acts))
            n_out = len(group) * len(sc)
            max_tokens = int(clamp(600 + n_out * 16 + len(group) * 40, 1800, 16000))
            try:
                data, call = models.call('watch', WATCH_STORY_SYSTEM, user, max_tokens=max_tokens, temperature=0.6, tools=None)
            except Exception as e:
                print(f"[twin-watch] part {a} failed for seats {group}: {e}")
                data, call = None, None
            if log and call is not None:
                log(f"watch:{a}", call)
            with lock:
                if state['closed'] or time.time() > deadline:   # past the budget the read is already handed back; a late answer never lands
                    return
                out['acts'] += 1
                out['stories'].setdefault(a, _STORY.story_for_prompt(st))
                got = _merge_act(out, data, a, {int(s['id']) for s in sc}, cards, seats=group)
                if got is None:
                    out['failed_parts'].append({'part': a, 'seats': list(group)})
                    continue
                for sid in group:
                    g = got.get(sid) or {}
                    memory[sid] = _STORY.add_part(memory[sid], a, g.get('rows') or {}, scene_rows, note=g.get('note') or '', waiting_for=g.get('waiting_for') or '')

    recap = threading.Thread(target=story_chain, name='twin-watch-story', daemon=True)
    recap.start()
    pool = ThreadPoolExecutor(max_workers=max(1, min(16, len(groups))))
    futures = [pool.submit(chain, g) for g in groups]
    pending = set(futures)
    while pending and time.time() < deadline:
        done, pending = wait(pending, timeout=max(0.2, min(1.0, deadline - time.time())), return_when=FIRST_COMPLETED)
        for f in done:
            try:
                f.result()
            except Exception as e:
                print(f"[twin-watch] a seat group stopped: {e}")
    with lock:
        state['closed'] = True
        for f in pending:
            f.cancel()
        listed = {(fp['part'], sid) for fp in out['failed_parts'] for sid in fp['seats']}
        for a in acts:   # a seat with no reading for a part (a group still reading at the budget, a seat the answer left out) is on the habit line there
            ids = {int(x['id']) for x in by_act[a]}
            missing = [sid for sid in seat_ids if (a, sid) not in listed and not (ids & set((out['by_seat'].get(sid) or {}).keys()))]
            if missing:
                out['failed_parts'].append({'part': a, 'seats': missing})
        out['failed_acts'] = [a for a in acts if any(fp['part'] == a for fp in out['failed_parts'])]
        out['memory'] = {sid: dict(memory[sid]) for sid in seat_ids}
        out['stories'] = {a: out['stories'].get(a) for a in acts if a in out['stories']}
    pool.shutdown(wait=False)


# ---------------------------------------------------------- the battery
def battery_questions(fmt: str) -> list:
    """[(qid, label, kind, [(oid, label, lean)])] for the format."""
    fmt = fmt if fmt in BATTERY_FOR else ('feature' if fmt in ('film', 'movie') else ('trailer' if fmt == 'spot' else 'pilot'))
    out = []
    for qid in BATTERY_FOR[fmt]:
        spec = BATTERY[qid]
        label = spec['label']
        if qid == 'finish':
            label = FINISH_LABEL.get(fmt, label)
        elif qid == 'tune_in':
            label = TUNE_IN_LABEL.get(fmt, label)
        elif qid == 'overall':
            label = OVERALL_LABEL.get(fmt, label)
        out.append((qid, label, spec['kind'], list(spec['options'])))
    return out


def battery_with(questions: list, twin: dict, roster: list, seat_summaries: dict, *, cards: Optional[dict] = None, meta: Optional[dict] = None,
                 models: Optional[Models] = None, session_seed: str = '', usage_extras: Optional[dict] = None, log: Optional[Callable] = None,
                 system: str = '') -> dict:
    """``battery`` on a caller's own question list (a mode's card: a
    trailer's interest before and after, a concept's strongest element).
    ``questions`` = [(qid, label, kind, [(oid, label, lean)])]. Same
    return shape as ``battery``; the deterministic card fills what the
    model leaves."""
    out = {'answers': {}, 'why': {}, 'best': {}, 'worst': {}, 'fallback': False}
    if not roster or not questions:
        out['fallback'] = True
        return out
    models = models or Models(usage_extras=usage_extras)
    if models.fn is None and (regression_mode() or not model_available()):
        out['fallback'] = True
        return out
    cards = cards or {int(r['seat']): persona_card(twin, r, session_seed) for r in roster}
    meta = meta or {}
    user = json.dumps({'stimulus': {k: v for k, v in meta.items() if k in ('title', 'genre', 'format', 'logline', 'text', 'options', 'frame', 'rough_cut', 'runtime')},
                       'card': [{'id': qid, 'question': label, 'options': [{'id': o[0], 'label': o[1]} for o in opts]} for qid, label, _k, opts in questions],
                       'room': [{'seat': int(r['seat']), 'name': r.get('name'), 'who': _seat_card_text(cards[int(r['seat'])], 520),
                                 'their_watch': (seat_summaries or {}).get(int(r['seat'])) or {}} for r in roster]}, default=str)
    max_tokens = int(clamp(300 + len(roster) * 120, 1200, 6500))
    data, call = models.call('battery', system or BATTERY_SYSTEM, user, max_tokens=max_tokens, temperature=0.5, tools=None)
    if log:
        log('battery', call)
    if not isinstance(data, dict) or not isinstance(data.get('seats'), list):
        out['fallback'] = True
        return out
    valid = {qid: {o[0] for o in opts} for qid, _l, _k, opts in questions}
    for row in data['seats']:
        if not isinstance(row, dict):
            continue
        try:
            seat = int(row.get('seat'))
        except (TypeError, ValueError):
            continue
        if seat not in cards:
            continue
        ans = {}
        for qid, oid in (row.get('answers') or {}).items() if isinstance(row.get('answers'), dict) else []:
            q = str(qid).strip().lower()
            o = str(oid).strip().lower()
            if q in valid and o in valid[q]:
                ans[q] = o
        if ans:
            out['answers'][seat] = ans
        why = scrub(str(row.get('why') or '').strip())[:200]
        if why:
            out['why'][seat] = why
    if not out['answers']:
        out['fallback'] = True
    return out


def battery(twin: dict, roster: list, seat_summaries: dict, *, fmt: str = 'pilot', cards: Optional[dict] = None, meta: Optional[dict] = None,
            models: Optional[Models] = None, session_seed: str = '', budget_s: float = BUDGET_S['battery'], usage_extras: Optional[dict] = None,
            log: Optional[Callable] = None) -> dict:
    """{answers: {seat: {qid: oid}}, why: {seat: line}, best: {seat: scene},
    worst: {seat: scene}, fallback: bool}. ``seat_summaries`` = {seat:
    {mean, end, peak: {clock, on_screen}, valley: {...}, events: {type:
    n}, said: [lines]}}."""
    out = {'answers': {}, 'why': {}, 'best': {}, 'worst': {}, 'fallback': False}
    if not roster:
        out['fallback'] = True
        return out
    models = models or Models(usage_extras=usage_extras)
    if models.fn is None and (regression_mode() or not model_available()):
        out['fallback'] = True
        return out
    cards = cards or {int(r['seat']): persona_card(twin, r, session_seed) for r in roster}
    qs = battery_questions(fmt)
    meta = meta or {}
    user = json.dumps({'cut': {'title': meta.get('title') or 'the cut', 'genre': meta.get('genre') or '', 'format': fmt, 'runtime': _clock(meta.get('runtime_s') or 0),
                               'rough_cut': bool(meta.get('rough_cut'))},
                       'card': [{'id': qid, 'question': label, 'options': [{'id': o[0], 'label': o[1]} for o in opts]} for qid, label, _k, opts in qs],
                       'room': [{'seat': int(r['seat']), 'name': r.get('name'), 'who': _seat_card_text(cards[int(r['seat'])], 520),
                                 'their_watch': (seat_summaries or {}).get(int(r['seat'])) or {}} for r in roster]}, default=str)
    max_tokens = int(clamp(300 + len(roster) * 110, 1200, 6000))
    data, call = models.call('battery', BATTERY_SYSTEM, user, max_tokens=max_tokens, temperature=0.5, tools=None)
    if log:
        log('battery', call)
    if not isinstance(data, dict) or not isinstance(data.get('seats'), list):
        out['fallback'] = True
        return out
    valid = {qid: {o[0] for o in opts} for qid, _l, _k, opts in qs}
    for row in data['seats']:
        if not isinstance(row, dict):
            continue
        try:
            seat = int(row.get('seat'))
        except (TypeError, ValueError):
            continue
        if seat not in cards:
            continue
        ans = {}
        for qid, oid in (row.get('answers') or {}).items() if isinstance(row.get('answers'), dict) else []:
            q = str(qid).strip().lower()
            o = str(oid).strip().lower()
            if q in valid and o in valid[q]:
                ans[q] = o
        if ans:
            out['answers'][seat] = ans
        why = scrub(str(row.get('why') or '').strip())[:200]
        if why:
            out['why'][seat] = why
        for k in ('best', 'worst'):
            v = row.get(f"{k}_scene")
            try:
                if v is not None:
                    out[k][seat] = int(v)
            except (TypeError, ValueError):
                pass
    if not out['answers']:
        out['fallback'] = True
    return out


# ------------------------------------------------------------- the guide
def guide(scenes: list, *, meta: Optional[dict] = None, models: Optional[Models] = None, duration_s: float = 0,
          budget_s: float = BUDGET_S['guide'], usage_extras: Optional[dict] = None, log: Optional[Callable] = None) -> dict:
    """{questions: [{t, clock, q, kind, why}], fallback: bool}."""
    out = {'questions': [], 'fallback': False}
    if not scenes:
        out['fallback'] = True
        return out
    models = models or Models(usage_extras=usage_extras)
    dur = float(duration_s or scenes[-1].get('t_end') or 1)
    if models.fn is None and (regression_mode() or not model_available()):
        out['fallback'] = True
        return out
    # the scenes at act turns and a thin sample in between keep the call small
    picks = []
    last_act = None
    for i, s in enumerate(scenes):
        a = str(s.get('act') or act_of(float(s.get('t_start') or 0) / dur))
        if a != last_act or i % max(1, len(scenes) // 24) == 0 or i == len(scenes) - 1:
            picks.append(dict(s, act=a))
            last_act = a
    meta = meta or {}
    user = json.dumps({'cut': {'title': meta.get('title') or 'the cut', 'genre': meta.get('genre') or '', 'format': meta.get('format') or 'pilot', 'runtime': _clock(dur)},
                       'scenes': [{'id': int(s['id']), 't': float(s.get('t_start') or 0), 'clock': _clock(s.get('t_start')), 'act': s.get('act'),
                                   'on_screen': str(s.get('caption') or s.get('action') or '')[:140], 'dialogue': str(s.get('dialogue') or '')[:120]} for s in picks[:60]]}, default=str)
    data, call = models.call('guide', GUIDE_SYSTEM, user, max_tokens=1200, temperature=0.4, tools=None)
    if log:
        log('guide', call)
    rows = []
    if isinstance(data, dict) and isinstance(data.get('questions'), list):
        for r in data['questions'][:8]:
            if not isinstance(r, dict) or not str(r.get('q') or '').strip():
                continue
            try:
                t = float(r.get('t') or 0)
            except (TypeError, ValueError):
                continue
            t = clamp(t, 0.0, dur)
            kind = str(r.get('kind') or 'open').strip().lower()
            rows.append({'t': round(t, 3), 'clock': _clock(t), 'q': scrub(str(r['q']).strip())[:200], 'kind': kind if kind in ('yes_no', 'open', 'rating', 'show_of_hands') else 'open',
                         'why': scrub(str(r.get('why') or '').strip())[:120]})
    rows.sort(key=lambda r: r['t'])
    if len(rows) < 3:
        out['fallback'] = True
        return out
    out['questions'] = rows
    return out


__all__ = ['TONES', 'EVENTS', 'GENRES', 'ACTS', 'BUDGET_S', 'BATTERY', 'BATTERY_FOR', 'FINISH_LABEL', 'TUNE_IN_LABEL', 'OVERALL_LABEL', 'VISION_MODELS',
           'act_of', 'caption_frames', 'genre_from_votes', 'persona_watch', 'battery', 'battery_with', 'battery_questions', 'guide', 'Models', 'MODELS', '_vision_json']
