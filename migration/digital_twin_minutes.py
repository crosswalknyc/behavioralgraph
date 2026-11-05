"""Pilot Testing, minute by minute.

Jenna, 2026-11-05: "it needs to show a ticker with duration and time
across the bottom so you can see where things happen against minutes.
if you hover over a certain minute it should open a summary of what
people were saying at that point so that you can get a read on
sentiment there. every 5 minutes it needs to give you the average
rating based on room/cohort/people whatever you're on and hovering
over. and then if you click on it it would highlight the chats from
that time so you could read them."

This module is the arithmetic and the minute read behind that page:

- ``series``: the one line the page is on (the room, a cohort, a seat)
  as (t, value) on the dial clock, live or scored.
- ``minute_avg`` / ``bucket_avgs``: the average over one minute, and
  over the five minutes before every five-minute mark and the end.
- ``lines_in_window``: what the room said in a minute, with a 30 second
  margin either side, filtered to the seats the active line stands for.
- ``digest``: the minute's read (one or two plain sentences and its
  lean), one model call through the room's ``Models`` helper, cached on
  the session record under ``minute_digests[mode:id:minute]`` so a
  second hover is instant. A minute with no lines is 'Quiet minute.'
  and never calls a model.
- ``precompute``: every minute's Room digest and the five-minute buckets,
  run at stop so hovering a finished test never waits.

Nothing here names how the read was made: the summary goes through the
room's scrub (no model, panel, sample, synthetic or twin-engine words,
no em dashes).
"""
from __future__ import annotations

import math
import threading
import time
import traceback
from collections import Counter
from typing import Optional

from migration.digital_twin_room import Models, scrub

MINUTE_S = 60
BUCKET_S = 300
MARGIN_S = 30
QUIET = 'Quiet minute.'
MODES = ('room', 'cohort', 'person')
LEANS = ('leans warm', 'warming', 'steady', 'split', 'cooling', 'leans cool')
WARM_EVENTS = frozenset({'love', 'laugh', 'lean-in', 'wow'})
COOL_EVENTS = frozenset({'confuse', 'check-out'})
MAX_LINES_TO_MODEL = 18
SUMMARY_MAX = 240

DIGEST_SYSTEM = (
    "You are the moderator's notes for a screening room of real people watching a show together. "
    "You are given what some of them said out loud during one minute, each line with the person's enjoyment reading at that "
    "moment (0 to 100), plus where the room's reading sat at the start and end of the minute. Return JSON only, no prose:\n"
    "{\"read\": string, \"lean\": string}\n"
    "read: one or two plain sentences, 220 characters at most, on what people were saying and feeling in that minute, in the "
    "everyday words a producer would use in the room. Say what landed and what did not. Quote at most one short phrase. Name "
    "nobody by demographic. lean: exactly one of 'leans warm', 'warming', 'steady', 'split', 'cooling', 'leans cool'. "
    "Never describe how the reading was produced, never mention a model, data, a panel, a sample or a twin, and use no dashes."
)

_MEMO: dict = {}
_MEMO_LOCK = threading.Lock()


def _now() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _clock(t) -> str:
    try:
        s = max(0, int(round(float(t))))
    except (TypeError, ValueError):
        s = 0
    return f"{s // 60}:{s % 60:02d}"


def _f(v, default=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(x) or math.isinf(x):
        return default
    return x


def _cohort_key(label: str) -> str:
    try:
        from migration.digital_twin_pilot import cohort_key
        return cohort_key(label)
    except Exception:
        import re
        return 'cohort_' + (re.sub(r"[^0-9a-z]+", '_', str(label or '').lower()).strip('_') or 'x')


def _norm_mode(mode) -> str:
    m = str(mode or 'room').strip().lower()
    if m in ('seat', 'people', 'persons'):
        m = 'person'
    if m in ('cohorts',):
        m = 'cohort'
    return m if m in MODES else 'room'


# ------------------------------------------------------------ the series
def runtime_s(sess: dict) -> float:
    """The clock the ticker spans: the planned or measured runtime, else
    the last dial point, else the last remark."""
    sess = sess or {}
    media = sess.get('media') or {}
    dur = _f(media.get('runtime_s'), 0.0) or 0.0
    ts = [_f(x) for x in ((sess.get('dial') or {}).get('t') or [])]
    ts = [x for x in ts if x is not None]
    if dur <= 0 and ts:
        dur = max(ts)
    if dur <= 0:
        ev = [_f(e.get('t')) for e in (sess.get('events') or []) if isinstance(e, dict)]
        ev = [x for x in ev if x is not None]
        if ev:
            dur = max(ev)
    return max(0.0, dur)


def stop_point_s(sess: dict) -> float:
    """Where the clock stood when the test stopped: the last dial point,
    else the runtime."""
    ts = [_f(x) for x in ((sess or {}).get('dial') or {}).get('t') or []]
    ts = [x for x in ts if x is not None]
    return max(ts) if ts else runtime_s(sess)


def _pairs(ts: list, vs: list) -> tuple:
    out_t, out_v = [], []
    for t, v in zip(ts, vs):
        ft, fv = _f(t), _f(v)
        if ft is None or fv is None:
            continue
        out_t.append(ft)
        out_v.append(fv)
    return out_t, out_v


def series(sess: dict, mode: str = 'room', ident='') -> tuple:
    """(t, value) for the line the page is on. The room is the dial; a
    cohort is its split row on the dial clock (by key or by label); a
    seat is its scored dial when the test is scored, else its live
    frame readings."""
    sess = sess or {}
    mode = _norm_mode(mode)
    d = sess.get('dial') or {}
    ts = list(d.get('t') or [])
    if mode == 'room':
        return _pairs(ts, list(d.get('value') or []))
    if mode == 'cohort':
        sp = d.get('splits') or {}
        key = str(ident or '')
        row = sp.get(key)
        if row is None:
            key2 = _cohort_key(key)
            row = sp.get(key2)
            if row is None:
                want = ' '.join(key.lower().split())
                for k, r in sp.items():
                    if isinstance(r, dict) and ' '.join(str(r.get('label') or '').lower().split()) == want:
                        row = r
                        break
        if not isinstance(row, dict):
            return [], []
        return _pairs(ts, list(row.get('value') or []))
    sno = str(ident or '')
    sd = sess.get('seat_dials') or {}
    scored = sd.get(sno)
    if isinstance(scored, list) and scored and ts and len(scored) == len(ts):
        return _pairs(ts, scored)
    live = ((sess.get('live') or {}).get('seat_dials') or {}).get(sno)
    if isinstance(live, list) and live:
        pts = [p for p in live if isinstance(p, (list, tuple)) and len(p) >= 2]
        return _pairs([p[0] for p in pts], [p[1] for p in pts])
    if isinstance(scored, list) and scored and ts:
        return _pairs(ts, scored)
    return [], []


def seats_for(sess: dict, mode: str = 'room', ident='') -> Optional[set]:
    """The seat numbers (as strings) the line stands for; None for the
    whole room."""
    mode = _norm_mode(mode)
    if mode == 'room':
        return None
    roster = ((sess or {}).get('group') or {}).get('roster') or []
    if mode == 'person':
        return {str(ident)}
    key = str(ident or '')
    want_label = ' '.join(key.lower().split())
    out = set()
    for r in roster:
        seg = str(r.get('segment') or '').strip()
        if not seg:
            continue
        if _cohort_key(seg) == key or key == seg or ' '.join(seg.lower().split()) == want_label or _cohort_key(seg) == _cohort_key(key):
            out.add(str(r.get('seat')))
    return out


# ------------------------------------------------------------ averages
def window_avg(ts: list, vs: list, a: float, b: float) -> tuple:
    """(average, n) over the points with a <= t < b; (None, 0) when none."""
    tot, n = 0.0, 0
    for t, v in zip(ts, vs):
        if a <= t < b:
            tot += v
            n += 1
    return (tot / n if n else None), n


def reading_at(ts: list, vs: list, t: float) -> Optional[float]:
    """The line's reading at clock t: the last point at or before t,
    else the first point."""
    if not ts:
        return None
    last = None
    for x, v in zip(ts, vs):
        if x <= t + 1e-6:
            last = v
        else:
            break
    return last if last is not None else vs[0]


def minute_avg(sess: dict, mode: str = 'room', ident='', minute: int = 0) -> tuple:
    ts, vs = series(sess, mode, ident)
    m = max(0, int(minute))
    return window_avg(ts, vs, m * MINUTE_S, (m + 1) * MINUTE_S)


def bucket_marks(dur: float, upto: Optional[float] = None, width: int = BUCKET_S, finished: bool = True) -> list:
    """The clock points that carry an average: every full ``width`` up to
    the end, and the end itself when the test has stopped and the end is
    not within 45 seconds of the last mark."""
    end = min(_f(upto, dur) if upto is not None else dur, dur)
    end = max(0.0, end or 0.0)
    marks = []
    k = width
    while k <= end + 1e-6:
        marks.append(float(k))
        k += width
    if finished and end > 0 and (not marks or end - marks[-1] > 45):
        marks.append(round(end, 2))
    return marks


def bucket_avgs(sess: dict, mode: str = 'room', ident='', width: int = BUCKET_S, upto: Optional[float] = None, finished: Optional[bool] = None) -> list:
    """[{t, from, avg, n, clock}] at every five-minute mark and the end:
    the average over the preceding five minutes of the line."""
    ts, vs = series(sess, mode, ident)
    if not ts:
        return []
    if finished is None:
        finished = str((sess or {}).get('status') or '') == 'stopped' or bool(((sess or {}).get('live') or {}).get('finished_at'))
    dur = runtime_s(sess) or max(ts)
    end = min(upto if upto is not None else (stop_point_s(sess) if finished else max(ts)), dur)
    out = []
    for te in bucket_marks(dur, end, width, finished=bool(finished)):
        a = te - width
        tot, n = 0.0, 0
        for t, v in zip(ts, vs):
            if a < t <= te + 1e-6:
                tot += v
                n += 1
        if n:
            out.append({'t': te, 'from': max(0.0, a), 'avg': round(tot / n, 1), 'n': n, 'clock': _clock(te)})
    return out


# ------------------------------------------------------------ the lines
def window_bounds(t: float, margin_s: int = MARGIN_S) -> tuple:
    m = max(0, int((_f(t, 0.0) or 0.0) // MINUTE_S))
    return m, m * MINUTE_S - margin_s, (m + 1) * MINUTE_S + margin_s


def lines_in_window(sess: dict, t: float, mode: str = 'room', ident='', margin_s: int = MARGIN_S) -> list:
    """What was said in the minute around clock ``t`` (the minute plus a
    margin either side), oldest first: the live reactions and the answers
    to questions asked then, each with the seat's own reading at that
    moment. Filtered to the seats the line stands for."""
    sess = sess or {}
    mode = _norm_mode(mode)
    _, a, b = window_bounds(t, margin_s)
    seats = seats_for(sess, mode, ident)
    roster = {str(r.get('seat')): r for r in ((sess.get('group') or {}).get('roster') or []) if isinstance(r, dict)}
    cache = {}

    def rating(sno, at):
        if sno not in cache:
            cache[sno] = series(sess, 'person', sno)
        ts, vs = cache[sno]
        v = reading_at(ts, vs, at)
        return round(v, 1) if v is not None else None

    out = []
    for e in sess.get('events') or []:
        if not isinstance(e, dict):
            continue
        te = _f(e.get('t'))
        if te is None or not (a <= te < b):
            continue
        sno = str(e.get('seat'))
        if seats is not None and sno not in seats:
            continue
        text = ' '.join(str(e.get('line') or '').split())
        if not text:
            continue
        r = roster.get(sno) or {}
        out.append({'seat': e.get('seat'), 't': round(te, 2), 'clock': _clock(te), 'text': text, 'rating': rating(sno, te),
                    'type': str(e.get('type') or ''), 'name': str(e.get('name') or r.get('name') or '')})
    for q in sess.get('questions') or []:
        if not isinstance(q, dict):
            continue
        tq = _f(q.get('playhead_s'))
        if tq is None or not (a <= tq < b):
            continue
        for ans in q.get('answers') or []:
            if not isinstance(ans, dict) or ans.get('seat') is None:
                continue
            sno = str(ans.get('seat'))
            if seats is not None and sno not in seats:
                continue
            text = ' '.join(str(ans.get('line') or '').split())
            if not text:
                continue
            r = roster.get(sno) or {}
            out.append({'seat': ans.get('seat'), 't': round(tq, 2), 'clock': _clock(tq), 'text': text, 'rating': rating(sno, tq),
                        'type': 'answer', 'name': str(ans.get('name') or r.get('name') or ''), 'q': ' '.join(str(q.get('q') or '').split())[:140]})
    out.sort(key=lambda x: (x['t'], str(x['seat'])))
    return out


# ------------------------------------------------------------ the lean
def lean_of(sess: dict, mode: str, ident, minute: int, lines: list) -> str:
    """The minute's lean in plain words, from what was said and where the
    line moved against the minute before."""
    ts, vs = series(sess, mode, ident)
    m = max(0, int(minute))
    cur, _ = window_avg(ts, vs, m * MINUTE_S, (m + 1) * MINUTE_S)
    prev, _ = window_avg(ts, vs, (m - 1) * MINUTE_S, m * MINUTE_S) if m > 0 else (None, 0)
    kinds = Counter(str(l.get('type') or '') for l in lines or [])
    warm = sum(kinds[k] for k in WARM_EVENTS)
    cool = sum(kinds[k] for k in COOL_EVENTS)
    if warm and cool and min(warm, cool) >= max(1, 0.4 * max(warm, cool)):
        return 'split'
    delta = (cur - prev) if (cur is not None and prev is not None) else 0.0
    if delta >= 2.0:
        return 'warming'
    if delta <= -2.0:
        return 'cooling'
    if warm > cool:
        return 'leans warm'
    if cool > warm:
        return 'leans cool'
    return 'steady'


def _fallback_read(lines: list, lean: str) -> str:
    """A plain read when no model answers: who spoke up and the first
    thing said."""
    who = len({str(l.get('seat')) for l in lines})
    kinds = Counter(str(l.get('type') or '') for l in lines)
    warm = sum(kinds[k] for k in WARM_EVENTS)
    cool = sum(kinds[k] for k in COOL_EVENTS)
    s = f"{who} {'seat' if who == 1 else 'seats'} spoke up"
    if warm and not cool:
        s += ', all of it warm'
    elif cool and not warm:
        s += ', none of it kind'
    elif warm and cool:
        s += ', pulling two ways'
    first = str(lines[0].get('text') or '').strip().rstrip('.')
    if len(first) > 90:
        first = first[:87].rstrip() + '...'
    s += f'. "{first}"' if first else '.'
    return s


def digest_key(mode: str, ident, minute: int) -> str:
    return f"{_norm_mode(mode)}:{ident if ident is not None else ''}:{int(minute)}"


def bucket_key(mode: str, ident='') -> str:
    mode = _norm_mode(mode)
    return mode if mode == 'room' else f"{mode}:{ident if ident is not None else ''}"


def clear_memo() -> None:
    """The process-level memo (tests)."""
    with _MEMO_LOCK:
        _MEMO.clear()


def _clean_summary(text: str) -> str:
    t = scrub(' '.join(str(text or '').split()))
    t = t.replace('\u2014', '-').replace('\u2013', '-')
    if len(t) > SUMMARY_MAX:
        cut = t[:SUMMARY_MAX - 3]
        if '. ' in cut[80:]:
            cut = cut[:cut.rfind('. ') + 1]
        else:
            cut = cut.rstrip() + '...'
        t = cut
    return t


def _ask_model(sess: dict, mode: str, ident, minute: int, lines: list, lean: str, models: Optional[Models]) -> tuple:
    """(read, lean) from one model call; (None, lean) when none answers."""
    models = models or Models()
    ts, vs = series(sess, mode, ident)
    m = max(0, int(minute))
    start = reading_at(ts, vs, m * MINUTE_S)
    end = reading_at(ts, vs, (m + 1) * MINUTE_S - 0.01)
    media = sess.get('media') or {}
    title = str(media.get('title') or sess.get('title') or 'the cut')
    label = {'room': 'the whole room', 'cohort': f"the {ident} cohort", 'person': 'one seat'}[_norm_mode(mode)]
    if _norm_mode(mode) == 'cohort':
        sp = ((sess.get('dial') or {}).get('splits') or {}).get(str(ident)) or {}
        if sp.get('label'):
            label = f"the {sp['label']} cohort"
    head = (f"The show: {title}. The minute: {_clock(m * MINUTE_S)} to {_clock((m + 1) * MINUTE_S)}. The line: {label}."
            + (f" Its reading went from {start:.0f} to {end:.0f} over the minute." if start is not None and end is not None else ''))
    shown = lines[:MAX_LINES_TO_MODEL]
    body = '\n'.join(f"{l.get('clock')} seat {l.get('seat')}" + (f" ({l.get('name')})" if l.get('name') else '') + (f" at {l['rating']:.0f}" if l.get('rating') is not None else '')
                     + (f" [{l.get('type')}]" if l.get('type') and l.get('type') != 'answer' else (f" answering \"{l.get('q')}\"" if l.get('q') else ''))
                     + f": {l.get('text')}" for l in shown)
    more = f"\n(and {len(lines) - len(shown)} more lines)" if len(lines) > len(shown) else ''
    user = head + "\nWhat they said:\n" + body + more + f"\nThe lean the readings suggest: {lean}."
    try:
        data, _rec = models.call('digest', DIGEST_SYSTEM, user, max_tokens=320, temperature=0.3, tools=None)
    except Exception as e:
        print(f"[twin-minutes] digest call failed: {str(e)[:160]}")
        data = None
    if not isinstance(data, dict):
        return None, lean
    read = ' '.join(str(data.get('read') or data.get('summary') or '').split())
    got = ' '.join(str(data.get('lean') or '').lower().split())
    return (read or None), (got if got in LEANS else lean)


def digest(sess: dict, t: float, mode: str = 'room', ident='', *, models: Optional[Models] = None, margin_s: int = MARGIN_S, memo_key: Optional[str] = None) -> dict:
    """The minute's read for the line the page is on.

    {t, minute, clock, avg, n, lines, summary, lean, mode, id, cached,
    fresh}. ``n`` is how many seats were talking. ``cached`` is True when
    the summary came off the session record; ``fresh`` is True when this
    call wrote a new summary there (the caller saves the twin then)."""
    sess = sess or {}
    mode = _norm_mode(mode)
    ident = '' if ident is None else str(ident)
    m, _a, _b = window_bounds(t, margin_s)
    lines = lines_in_window(sess, t, mode, ident, margin_s)
    avg, _n_pts = minute_avg(sess, mode, ident, m)
    talking = len({str(l.get('seat')) for l in lines})
    base = {'t': m * MINUTE_S, 'minute': m, 'clock': _clock(m * MINUTE_S), 'avg': round(avg, 1) if avg is not None else None,
            'n': talking, 'lines': lines, 'mode': mode, 'id': ident}
    lean = lean_of(sess, mode, ident, m, lines)
    if not lines:
        return dict(base, summary=QUIET, lean=lean, cached=False, fresh=False)
    key = digest_key(mode, ident, m)
    digests = sess.get('minute_digests') if isinstance(sess.get('minute_digests'), dict) else None
    hit = (digests or {}).get(key)
    if isinstance(hit, dict) and hit.get('summary') and int(hit.get('lines_n') or -1) == len(lines):
        return dict(base, summary=hit['summary'], lean=hit.get('lean') or lean, cached=True, fresh=False)
    mk = f"{memo_key}|{key}|{len(lines)}" if memo_key else None
    if mk:
        with _MEMO_LOCK:
            memo = _MEMO.get(mk)
        if memo:
            return dict(base, summary=memo['summary'], lean=memo['lean'], cached=True, fresh=False)
    read, lean2 = _ask_model(sess, mode, ident, m, lines, lean, models)
    summary = _clean_summary(read if read else _fallback_read(lines, lean2))
    if not summary:
        summary = _clean_summary(_fallback_read(lines, lean2))
    rec = {'summary': summary, 'lean': lean2, 'lines_n': len(lines), 'at': _now()}
    if digests is None:
        digests = sess['minute_digests'] = {}
    digests[key] = rec
    if mk:
        with _MEMO_LOCK:
            _MEMO[mk] = rec
            if len(_MEMO) > 4000:
                for k in list(_MEMO)[:1000]:
                    _MEMO.pop(k, None)
    return dict(base, summary=summary, lean=lean2, cached=False, fresh=True)


# ------------------------------------------------------------ at stop
def precompute(sess: dict, *, models: Optional[Models] = None, modes=(('room', ''),)) -> int:
    """Every minute's digest for the given lines (the Room by default)
    and the five-minute buckets, written on the session record. Returns
    the number of minutes covered."""
    sess = sess or {}
    dur = runtime_s(sess)
    if dur <= 0:
        return 0
    n_min = int(math.ceil(dur / MINUTE_S))
    models = models or Models()
    for mode, ident in modes:
        for m in range(n_min):
            try:
                digest(sess, m * MINUTE_S, mode, ident, models=models)
            except Exception:
                traceback.print_exc()
    buckets = sess.get('minute_buckets') if isinstance(sess.get('minute_buckets'), dict) else {}
    for mode, ident in modes:
        try:
            buckets[bucket_key(mode, ident)] = bucket_avgs(sess, mode, ident, finished=True)
        except Exception:
            traceback.print_exc()
    sess['minute_buckets'] = buckets
    sess['minute_digests_done'] = _now()
    return n_min


def precompute_in_background(sess: dict, *, key: str, payload: dict, save, usage_extras: Optional[dict] = None) -> threading.Thread:
    """``precompute`` on a thread, then save, so Stop answers at once."""
    def run():
        try:
            precompute(sess, models=Models(usage_extras=usage_extras))
        except Exception:
            traceback.print_exc()
        try:
            save(key, payload)
        except Exception:
            traceback.print_exc()
    th = threading.Thread(target=run, name=f"pilot-minutes-{(sess or {}).get('id')}", daemon=True)
    th.start()
    return th


__all__ = ['MINUTE_S', 'BUCKET_S', 'MARGIN_S', 'QUIET', 'LEANS', 'runtime_s', 'stop_point_s', 'series', 'seats_for', 'window_avg', 'reading_at',
           'minute_avg', 'bucket_marks', 'bucket_avgs', 'window_bounds', 'lines_in_window', 'lean_of', 'digest_key', 'bucket_key', 'clear_memo',
           'digest', 'precompute', 'precompute_in_background']
