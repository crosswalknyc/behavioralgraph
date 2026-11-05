"""Diagnostics on every screening (Digital Twins IQ Pilot Testing, 2026-10-28).

What a studio reads after the topline:

  characters   the cast from the transcript, the captions and the script
               (names, speaker turns, time on screen); every named
               character rated excellent to poor by every seat; the key
               relationship rated; screen time against likability; who
               the room would follow into a second episode, as a share of
               the room per person (a seat can follow more than one
               person, so the shares do not add to 100; see
               ``follow_shares``); norms for the lead, the second lead and
               the supporting cast; a plain read ("put her on the key
               art").
  pace         pace by act (too slow / just right / too fast), confusion
               with each seat's written confusion, the beginning and the
               ending rated on their own, a running-time read.
  scenes       the scenes liked best and least with mention counts and
               each seat's reason, the reasons for not recommending,
               similar titles, perceived gender and age appeal.
  components   humor, story, beginning, ending, music, settings, action,
               suspense (top box) next to their norms.

Deterministic from the seats' own dials and events when no model read
the cut; the persona read's words carry through when it did. Counts keep
messy last digits; shares are never pinned.

Screen time is a fact the card shows and never a term in any formula here
(Jenna, 2026-11-04: "screen time should never affect appeal or anything.
a cameo could be the best part of a show"); the standing rule is
``.cursor/rules/screen-time-never-weights-appeal.mdc`` in the parent repo
and ``scripts/test_digital_twin_screen_time_rule.py`` holds it.
"""
from __future__ import annotations

import re
from typing import Optional

from migration.digital_twin_engine import clamp, jit, messy_count, pct1, unit
from migration.digital_twin_pilot import (
    QUADRANTS, _card_for, _clock, _seat_people, _trait, lower_first, quadrant_of,
)
from migration.digital_twin_modes import ordered_probs, softmax_probs, publish_probs, argmax, blend_pick, CUTS, GRADE_SPREAD, _sig

RATING = [('a', 'Excellent', 'yes'), ('b', 'Very good', 'yes'), ('c', 'Good', 'maybe'), ('d', 'Fair', 'no'), ('e', 'Poor', 'no')]
PACE = [('a', 'Too slow', 'no'), ('b', 'Just right', 'yes'), ('c', 'Too fast', 'no')]
COMPONENTS = (('humor', 'Humor', 'funny'), ('story', 'Story', ''), ('beginning', 'Beginning', ''), ('ending', 'Ending', ''), ('music', 'Music', ''),
              ('settings', 'Settings', ''), ('action', 'Action', 'action'), ('suspense', 'Suspense', 'tense'))
ACT_LABEL = {'open': 'Cold open', 'setup': 'Setup', 'middle': 'Middle', 'turn': 'The turn', 'close': 'Close', 'end': 'Ending'}
_NAME_CUE_RX = re.compile(r"(?:^|[.!?]\s+|\n)\s*([A-Z][a-z]+(?: [A-Z][a-z]+)?)\s*:")
_CAP_NAME_RX = re.compile(r"\b([A-Z][a-z]{2,})\b")
_STOP_NAMES = {'The', 'And', 'But', 'She', 'He', 'They', 'Then', 'When', 'While', 'After', 'Before', 'Int', 'Ext', 'Day', 'Night', 'Cut', 'Fade', 'Continuous', 'Later', 'Morning', 'Evening',
               'Scene', 'Title', 'Credits', 'Music', 'Someone', 'Everyone', 'Nobody', 'Two', 'Three', 'Four', 'One', 'Man', 'Woman', 'Girl', 'Boy', 'Mom', 'Dad', 'Hello', 'Okay', 'Yes', 'No', 'What', 'Why', 'How',
               'Who', 'Where', 'Wait', 'Look', 'Listen', 'Please', 'Thanks', 'Sorry', 'Right', 'Well', 'Just', 'Here', 'There', 'This', 'That', 'Our', 'Your', 'Their', 'Its'}


def _now():
    import time
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _subject(twin: dict) -> str:
    m = (twin or {}).get('meta') or {}
    return str(m.get('subject') or m.get('name') or 'the twin')


def _scene_dur(s: dict) -> float:
    return max(0.5, float(s.get('t_end') or 0) - float(s.get('t_start') or 0))


# ---------------------------------------------------------------- cast
def cast_from_session(session: dict) -> list:
    """[{name, turns, screen_s, scenes: [ids], share_pct, role}] from the
    user's cast list, the transcript speakers, the scenes' speakers and
    the name cues in dialogue or captions. Roles by time on screen:
    lead, second lead, supporting."""
    scenes = session.get('scenes') or []
    transcript = session.get('transcript') or []
    names = {}

    def add(name, sid=None, turns=0, secs=0.0):
        n = ' '.join(str(name or '').split()).strip(" .,:;'\"")
        if not n or len(n) < 2 or n.split()[0] in _STOP_NAMES or len(n) > 40:
            return
        key = n.lower()
        row = names.setdefault(key, {'name': n, 'turns': 0, 'screen_s': 0.0, 'scenes': set()})
        row['turns'] += turns
        row['screen_s'] += secs
        if sid is not None:
            row['scenes'].add(int(sid))
    for c in session.get('cast') or []:
        add(c)
    for row in transcript:
        if isinstance(row, dict) and row.get('speaker'):
            t = float(row.get('t') or 0)
            sc = next((s for s in scenes if float(s.get('t_start') or 0) <= t < float(s.get('t_end') or 0)), None)
            add(row['speaker'], sc.get('id') if sc else None, 1, float(row.get('end') or t) - t if row.get('end') else 3.0)
    for s in scenes:
        sp = s.get('speakers') or ([s.get('speaker')] if s.get('speaker') else [])
        for name in sp:
            add(name, s.get('id'), 0, _scene_dur(s) / max(1, len(sp)))
        for m in _NAME_CUE_RX.finditer(str(s.get('dialogue') or '')):
            add(m.group(1), s.get('id'), 1, 2.0)
    # the cast list the user typed anchors to scenes whose caption or dialogue names them
    for key, row in list(names.items()):
        if not row['scenes']:
            for s in scenes:
                blob = f"{s.get('caption') or ''} {s.get('dialogue') or ''}".lower()
                if key in blob:
                    row['scenes'].add(int(s['id']))
                    row['screen_s'] += _scene_dur(s) * 0.6
    out = []
    total = sum(float(r['screen_s']) for r in names.values()) or 1.0
    for row in names.values():
        if not row['scenes'] and row['turns'] == 0:
            continue
        out.append({'name': row['name'], 'turns': int(row['turns']), 'screen_s': round(float(row['screen_s']), 1), 'scenes': sorted(row['scenes']),
                    'share_pct': round(100.0 * float(row['screen_s']) / total, 1)})
    out.sort(key=lambda r: (-r['screen_s'], -r['turns'], r['name']))
    for i, r in enumerate(out):
        r['role'] = 'lead' if i == 0 else ('second lead' if i == 1 else 'supporting')
    return out[:10]


FOLLOW_NOTE = 'A seat can follow more than one person.'   # the sub-line under the follow shares on the card, the report and the CSV
FOLLOW_CONF_FLOOR = 0.8   # a seat's word at confidence 1 carries 0.8 of its people, 1.0 at 5 (the room's own convention)
FOLLOW_MAYBE = 0.5        # a seat that would come back only on a condition ('only if the next case has a hook') follows at half
FOLLOW_LIST_MAX = 5       # the people the card lists; anyone a seat named always has a row
CHAR_TOP = 59.0        # the excellent line for a character: a lead whose scenes hold an ordinary dial lands near the lead norm
REACT_W = {'love': 1.6, 'wow': 1.6, 'lean-in': 1.2, 'laugh': 1.0, 'confuse': -1.4, 'check-out': -1.8}
REACT_CAP = 3.0        # points of appeal the reactions in a character's scenes can move, either way
GRADE_LINE = {'a': 0.0, 'b': -6.0, 'c': -12.0, 'd': -18.0}      # each grade's line, under CHAR_TOP
DRAW_LIFT_MAX = 4.0    # how far past their own favorite the seats who named the draw can be rated up to put her with the lead


def _seat_char_score(seat_series: list, beats: list, char: dict, card: dict, twin: dict, seed: str, *, events: Optional[list] = None, seat_no: Optional[int] = None,
                     remarks: float = 0.0) -> float:
    """Where a seat puts a character before the room's own words: the
    seat's dial across the character's scenes (a mean, so a long part and
    a cameo read on the same scale), the seat's reactions in those scenes
    (a love, a laugh, a lean-in lift it; a confusion or a check-out pulls
    it down; capped), the seat's remarks about the character, salted.
    Time on screen, scene count and seconds on screen are shown on the
    card and never enter this formula: the same reactions earn the same
    lift whether the character carried one scene or twenty (Jenna,
    2026-11-04: "a cameo could be the best part of a show")."""
    scene_ids = set(int(x) for x in (char.get('scenes') or []))
    idxs = [i for i, b in enumerate(beats) if b.get('scene_id') in scene_ids]
    if idxs and seat_series:
        vals = [seat_series[i] for i in idxs if i < len(seat_series)]
        base = sum(vals) / len(vals) if vals else 50.0
    else:
        base = (sum(seat_series) / len(seat_series)) if seat_series else 50.0
    if events and seat_no is not None and scene_ids:
        net = 0.0
        for e in events:
            try:
                if int(e.get('seat') if e.get('seat') is not None else -1) != int(seat_no):
                    continue
            except (TypeError, ValueError):
                continue
            sid = e.get('scene_id')
            if sid is None and e.get('beat') is not None:
                try:
                    sid = beats[int(e['beat'])].get('scene_id')
                except (TypeError, ValueError, IndexError):
                    sid = None
            if sid is not None and int(sid) in scene_ids:
                net += REACT_W.get(str(e.get('type') or ''), 0.0)
        base += clamp(3.0 * net, -REACT_CAP, REACT_CAP)   # no scene-count or duration term: the reactions alone, capped
    base += clamp(float(remarks or 0.0), -3.0, 3.0)
    loyal = _trait(twin, 'franchise_loyalty')
    base += (loyal - 50) * 0.04
    return clamp(base + jit(-7.0, 7.0, seed, 'char', char.get('name')), 3.0, 97.0)


def _apply_explicit(sc: dict, seat_no: int, ev: dict, seed: str) -> dict:
    """The seat's own words on the characters over its inferred scores: a
    floor or a ceiling per grade from a question that named the character,
    and the draw rule (a seat who names a character as the reason to come
    back rates that character at least as high as anyone else in the cast,
    never below good)."""
    if not ev:
        return sc
    for name, g in ((ev.get('floor') or {}).get(seat_no) or {}).items():
        if name in sc and g in GRADE_LINE:
            sc[name] = max(sc[name], CHAR_TOP + GRADE_LINE[g] + jit(0.3, 2.4, seed, 'floor', name, g))
    for name, g in ((ev.get('ceiling') or {}).get(seat_no) or {}).items():
        if name in sc and g in ('b', 'c', 'd'):
            above = {'b': 'a', 'c': 'b', 'd': 'c'}[g]
            sc[name] = min(sc[name], CHAR_TOP + GRADE_LINE[above] - jit(0.3, 2.4, seed, 'ceiling', name, g))
    draw = (ev.get('draw') or {}).get(seat_no)
    floor_at = None
    if draw and draw in sc:
        best_other = max([v for n, v in sc.items() if n != draw] or [sc[draw]])
        floor_at = max(sc[draw], best_other, CHAR_TOP + GRADE_LINE['c'] + jit(0.3, 2.4, seed, 'draw-floor', draw))
        sc[draw] = floor_at + jit(0.2, 1.2, seed, 'draw', draw)
    for name in list(sc):
        sc[name] = clamp(sc[name], 3.0, 97.0)
    return clamp(floor_at, 3.0, 97.0) if floor_at is not None else None


def _rating_probs(s: float, top: float = CHAR_TOP) -> dict:
    """Excellent to poor from a 0 to 100 score as graded probabilities;
    ``top`` is the excellent line."""
    return ordered_probs(s, (top, top - 6.0, top - 12.0, top - 18.0), [o[0] for o in RATING])


def _rating_pick(s: float, top: float = CHAR_TOP) -> str:
    return argmax(_rating_probs(s, top))


def _publish_rows(probs_by_seat: dict, options: list, roster: list, segments: list, seed: str) -> list:
    """Rows for the room from every seat's probabilities (a seat may
    also hand a bare pick; it becomes a near-certain leaning)."""
    ids = [o[0] for o in options]
    fixed = {}
    for sno, v in probs_by_seat.items():
        if isinstance(v, dict):
            fixed[int(sno)] = v
        elif v in ids:
            fixed[int(sno)] = {i: (0.82 if i == v else 0.18 / max(1, len(ids) - 1)) for i in ids}
    return publish_probs(fixed, options, roster, segments, seed)


def _top_box(rows: list, ids=('a',)) -> Optional[float]:
    if not rows:
        return None
    return round(sum(float(r.get('pct') or 0) for r in rows if r.get('id') in ids), 1)


def _is_whole(pct) -> bool:
    try:
        return int(round(float(pct) * 10)) % 10 == 0
    except (TypeError, ValueError):
        return False


def _messy_rows(rows: list, seed: str) -> list:
    """No published share on the characters card lands on a whole number
    (a 60.0 reads as a round number): a tenth moves between the row and
    the largest other row, salted direction, and the sum holds."""
    if len(rows) < 2:
        return rows
    for _pass in range(3):
        bad = [r for r in rows if r.get('pct') is not None and _is_whole(r['pct']) and float(r['pct']) > 0.15]
        if not bad:
            break
        for r in bad:
            others = sorted((o for o in rows if o is not r), key=lambda o: -float(o.get('pct') or 0))
            step = 0.1 if unit(seed, 'messy', r.get('id'), _pass) < 0.5 else -0.1
            for o in others:
                new_r, new_o = round(float(r['pct']) + step, 1), round(float(o['pct']) - step, 1)
                if new_r < 0.1 or new_o < 0.1 or _is_whole(new_o):
                    continue
                r['pct'], o['pct'] = new_r, new_o
                break
    return rows


def _conf_weight(conf) -> float:
    """A seat's confidence (1 to 5) as the weight its word carries:
    FOLLOW_CONF_FLOOR at 1, 1.0 at 5."""
    try:
        c = int(conf)
    except (TypeError, ValueError):
        c = 3
    return FOLLOW_CONF_FLOOR + (1.0 - FOLLOW_CONF_FLOOR) * (max(1, min(5, c)) - 1) / 4.0


def _seat_follow(name: str, seat_score: float, named: set, asked_about: set, intent: float, conf_w: float) -> float:
    """How far one seat follows one character into a second episode, 0 to
    1. Spoken first: a seat that named the character (alone or in a
    pair) as its reason to come back follows at its confidence; a seat
    asked about the character by name that named someone else has spoken
    on them and does not follow. Otherwise the inferred read: the seat
    rates the character excellent and would come back, weighted by how
    sure it is of that excellent rating, by its intent to come back and
    by its confidence. Time on screen is not a term here."""
    if name in named:
        return conf_w
    if name in asked_about:
        return 0.0
    probs = _rating_probs(seat_score)
    if argmax(probs) != 'a' or intent <= 0:
        return 0.0
    return clamp(float(probs['a']) * intent * conf_w, 0.0, 1.0)


def follow_shares(names: list, scores: dict, ev: dict, roster: list, segments: list, seed: str, battery_by_seat: Optional[list] = None) -> list:
    """Who the room would follow into a second episode, as a share of the
    room PER PERSON (Jenna, 2026-11-04: "this should be as high as
    jennifer if not higher for ll cool j"). Never a split: a seat can
    follow more than one person, so the shares do not add to 100 and are
    never renormalized.

    A seat follows a character when it named that character, or a pair
    including them, as its reason to come back (or picked them on a
    follow question); or when it rates them excellent and said something
    would bring it back. Spoken answers outrank the inferred rating: a
    seat asked about a person by name that named someone else does not
    follow that person. Each seat's word is weighted by its confidence
    (0.8 at 1, 1.0 at 5) and by the people it stands for; an inferred
    follow is also weighted by how sure the seat is of its excellent
    rating, and halved when the seat would come back only on a
    condition. A seat that answered no come-back question takes its
    intent from its own battery card (would it watch the next one).

    Returns [{name, pct, people, seats}] ordered by share, salted messy
    tenths, never a whole number, no two rows on the same tenth."""
    people = _seat_people(roster, segments)
    total = float(sum(people.get(int(r['seat']), 0) for r in roster) or 1.0)
    come = (ev or {}).get('come_back') or {}
    picks = (ev or {}).get('follow') or {}
    tune = {}
    for row in battery_by_seat or []:
        try:
            tune[int(row.get('seat'))] = str((row.get('answers') or {}).get('tune_in') or '')
        except (TypeError, ValueError, AttributeError):
            continue
    acc = {n: 0.0 for n in names}
    seats = {n: 0 for n in names}
    for r in roster:
        sno = int(r['seat'])
        if sno not in scores:
            continue
        w_people = float(people.get(sno) or 1)
        cb = come.get(sno) if isinstance(come.get(sno), dict) else None
        if cb:
            lean = str(cb.get('lean') or 'other')
            intent = 1.0 if lean == 'yes' else 0.0 if lean == 'no' else FOLLOW_MAYBE
            conf_w = _conf_weight(cb.get('conf'))
            asked_about = {str(n) for n in (cb.get('asked_about') or [])}
        else:
            t = tune.get(sno)
            intent = 0.0 if t == 'b' else 1.0
            conf_w = _conf_weight(3)
            asked_about = set()
        pick = picks.get(sno)
        named = set()
        if isinstance(pick, str):
            named.add(pick)
        elif isinstance(pick, (tuple, list)) and len(pick) == 2 and pick[0] == 'pair':
            named.update(str(x) for x in pick[1])
        for n in names:
            w = _seat_follow(n, float(scores[sno].get(n, 50.0)), named, asked_about, intent, conf_w)
            acc[n] += w_people * w
            if w >= 0.5:
                seats[n] += 1
    out = []
    for n in names:
        share = clamp(100.0 * acc[n] / total + jit(-0.25, 0.25, seed, 'share', n), 0.1, 99.9)
        pct = round(share, 1)
        if _is_whole(pct):
            pct = round(clamp(pct + (0.1 if unit(seed, 'whole', n) < 0.5 else -0.1), 0.1, 99.9), 1)
        out.append({'name': n, 'pct': pct, 'seats': seats[n]})
    out.sort(key=lambda r: (-r['pct'], r['name']))
    for i in range(1, len(out)):      # no two people on the same tenth: the lower row steps down a tenth, never onto a whole number
        if out[i]['pct'] >= out[i - 1]['pct']:
            v = round(out[i - 1]['pct'] - 0.1, 1)
            if _is_whole(v):
                v = round(v - 0.1, 1)
            out[i]['pct'] = max(0.1, v)
    for r in out:
        r['people'] = messy_count(seed, f"follow-{r['name']}", total * r['pct'] / 100.0)
    return out


TENTH_KEYS = ('excellent_pct', 'total_positive_pct', 'likability')   # the per-character shares a card prints next to each other


def _tenths(v) -> int:
    return int(round(float(v) * 10))


def _move_tenths(pub: list, give_id: str, take_ids: tuple, amount: float) -> bool:
    """``amount`` (a 1dp number of points, either sign) moves from the rating
    row ``give_id`` to the first row in ``take_ids`` where neither side lands
    on a whole number or under 0.1, so the character's rows still sum to
    100. True when a move was made."""
    by = {str(r.get('id')): r for r in pub}
    g = by.get(give_id)
    if g is None or g.get('pct') is None:
        return False
    for tid in take_ids:
        t = by.get(tid)
        if t is None or t is g or t.get('pct') is None:
            continue
        new_g, new_t = round(float(g['pct']) - amount, 1), round(float(t['pct']) + amount, 1)
        if new_g < 0.1 or new_t < 0.1 or _is_whole(new_g) or _is_whole(new_t):
            continue
        g['pct'], t['pct'] = new_g, new_t
        return True
    return False


def _set_tenth(row: dict, key: str, target: float) -> bool:
    """One character's share moved to ``target``: likability is a mean and
    moves on its own; excellent moves the difference between the
    character's excellent row and its next row; total positive moves it
    between the very good row and the rows under it, so excellent (already
    settled) holds. False when the rows cannot carry the move."""
    if key == 'likability':
        row['likability'] = round(float(target), 1)
        return True
    pub = row.get('rows') or []
    if key == 'excellent_pct':
        amount = round(float(row.get('excellent_pct') or 0.0) - float(target), 1)
        if not _move_tenths(pub, 'a', ('b', 'c', 'd', 'e'), amount):
            return False
    elif key == 'total_positive_pct':
        amount = round(float(row.get('total_positive_pct') or 0.0) - float(target), 1)
        if not _move_tenths(pub, 'b', ('c', 'd', 'e'), amount):
            return False
    else:
        return False
    row['excellent_pct'] = _top_box(pub)
    row['total_positive_pct'] = _top_box(pub, ('a', 'b'))
    return True


def distinct_cast_tenths(rows: list, seed: str) -> list:
    """No two characters on a card land on the same tenth of excellent,
    total positive or likability (the 60.7 / 60.7 a recompute of the NCIS:
    New York card would have printed), and none of the three sits on a
    whole number: the same guard the follow shares carry. Each measure is
    walked from the top down: a row that meets the one above it steps down
    a tenth or two, salted by the session and the name, never onto a whole
    number; a top row on a whole number moves a salted tenth; the order
    never swaps (ties break on a salted draw, so the same room resolves the
    same way every run); the rating rows behind excellent and total
    positive still sum to 100 because the tenth moves within the
    character's own rows. Returns [(name, measure)] for what moved. Never
    raises; a move the rows cannot carry is left as it was."""
    moved = []
    for key in TENTH_KEYS:
        ordered = sorted([r for r in rows if r.get(key) is not None and r.get('name')], key=lambda r: (-_tenths(r[key]), unit(seed, 'tie', key, r['name'])))
        prev_val = None
        for r in ordered:
            v = float(r[key])
            if not _is_whole(v) and (prev_val is None or _tenths(v) < _tenths(prev_val)):
                prev_val = v
                continue
            if prev_val is None:        # the top row sits on a whole number: a salted tenth up or down
                first = 0.1 if unit(seed, 'whole', key, r['name']) < 0.5 else -0.1
                cands = [round(v + first, 1), round(v - first, 1)]
            else:                       # the row meets the one above it (or sits on a whole number): a tenth or two under it
                steps = (0.1, 0.2, 0.3) if unit(seed, 'step', key, r['name']) < 0.5 else (0.2, 0.1, 0.3)
                cands = [round(prev_val - s, 1) for s in steps]
            for target in cands:
                if target < 0.1 or target > 99.9 or _is_whole(target):
                    continue
                if prev_val is not None and _tenths(target) >= _tenths(prev_val):
                    continue
                if _set_tenth(r, key, target):
                    moved.append((r['name'], key))
                    break
            prev_val = float(r[key])
    return moved


def follow_sentence(follow: list) -> str:
    """The moderator's line on the follow shares, naming the top two:
    'Asked who they would follow into a second episode, 66.1% say LL
    Cool J and 60.1% say Jennifer Beals.'"""
    rows = [f for f in (follow or []) if isinstance(f, dict) and f.get('name') and f.get('pct') is not None]
    if not rows:
        return ''
    head = f"Asked who they would follow into a second episode, {pct1(rows[0]['pct'])}% say {rows[0]['name']}"
    if len(rows) >= 2:
        head += f" and {pct1(rows[1]['pct'])}% say {rows[1]['name']}"
    return head + '.'


def follow_line(follow: list, limit: int = 3) -> str:
    """The card's follow line: 'Would follow into a second episode: LL
    Cool J 66.1%, Jennifer Beals 60.1%, Scott Caan 58.4%.'"""
    rows = [f for f in (follow or []) if isinstance(f, dict) and f.get('name') and f.get('pct') is not None][:limit]
    if not rows:
        return ''
    return 'Would follow into a second episode: ' + ', '.join(f"{f['name']} {pct1(f['pct'])}%" for f in rows) + '.'


def character_table(session: dict, twin: dict, norms: Optional[dict] = None) -> dict:
    roster = (session.get('group') or {}).get('roster') or []
    segments = (session.get('group') or {}).get('segments') or []
    cast = cast_from_session(session)
    if not roster or not cast:
        return {'characters': [], 'read': 'No named characters came through on this cut; add the cast on the test and the room rates them.' if roster else ''}
    beats = session.get('beats') or []
    seat_dials = session.get('seat_dials') or {}
    sid = str(session.get('id') or '')
    mk = str((session.get('media') or {}).get('hash') or '')
    seed = f"chars|{sid}|{mk}"
    events = session.get('events') or []
    cards = {int(r['seat']): _card_for(twin, r, seed) for r in roster}
    # the key relationship: the two characters most often in a scene together; when no two share a scene (a transcript on the
    # clock grid gives every scene one speaker), the two most often in back-to-back scenes (a conversation across a cut). Which
    # pair is the relationship comes from the cut's structure, never from the clock; how the room rates it comes from the seats.
    by_name = sorted(cast, key=lambda c: c['name'])      # ties between pairs break on the name, never on who has more time
    pair, best = None, -1
    for i in range(len(by_name)):
        for j in range(i + 1, len(by_name)):
            shared = len(set(by_name[i]['scenes']) & set(by_name[j]['scenes']))
            if shared > best:
                best, pair = shared, (by_name[i], by_name[j])
    if (pair is None or best < 1) and len(cast) >= 2:
        near, pair, best = -1, None, 0
        for i in range(len(by_name)):
            for j in range(i + 1, len(by_name)):
                a_ids, b_ids = set(int(x) for x in by_name[i]['scenes']), set(int(x) for x in by_name[j]['scenes'])
                close = sum(1 for x in a_ids if any(abs(x - y) <= 2 for y in b_ids))
                if close > near:
                    near, pair = close, (by_name[i], by_name[j])
        if pair is None or near < 1:
            pair = (cast[0], cast[1])      # nobody comes near anyone else in the cut: the two leads are the relationship a room is asked about
    if pair is not None:
        pair = tuple(sorted(pair, key=lambda c: cast.index(c)))      # printed lead first, a fact of the cut, after the pair is chosen
    # the room's own words on the characters (what the seats answered when asked), over the inferred scores
    ev = {}
    try:
        from migration.digital_twin_reconcile import character_evidence, room_draw, update_note
        ev = character_evidence(session, cast, [pair[0]['name'], pair[1]['name']] if pair else []) or {}
    except Exception as e:
        print(f"[twin-diagnostics] room evidence skipped: {e}")
        ev = {}
        room_draw = lambda _ev, _roster: None   # noqa: E731
        update_note = lambda _clocks: ''        # noqa: E731
    scores = {}
    draw_floor = {}
    for r in roster:
        sno = int(r['seat'])
        rem = (ev.get('remarks') or {}).get(sno) or {}
        scores[sno] = {c['name']: _seat_char_score(seat_dials.get(str(sno)) or [], beats, c, cards[sno], twin, f"{seed}|seat{sno}", events=events, seat_no=sno,
                                                   remarks=float(rem.get(c['name']) or 0.0)) for c in cast}
        fl = _apply_explicit(scores[sno], sno, ev, f"{seed}|seat{sno}")
        if fl is not None:
            draw_floor[sno] = fl
    names = [c['name'] for c in cast]

    def publish_one(name):
        ans = {sno: _rating_probs(scores[sno][name]) for sno in scores}
        return _messy_rows(_publish_rows(ans, RATING, roster, segments, f"{seed}|{name}"), f"{seed}|{name}")
    pubs = {n: publish_one(n) for n in names}
    # the draw: more than half the room named one character as the reason it would come back; that character rates in line with
    # the room's best-rated character (within salted tenths), the seats who named her rated up together until the room's number sits there
    draw_name = room_draw(ev, roster) if ev else None
    draw_lift = 0.0
    if draw_name in names and len(names) >= 2:
        namers = sorted(sno for sno, n in (ev.get('draw') or {}).items() if n == draw_name and sno in scores)
        top_other = max((n for n in names if n != draw_name), key=lambda n: float(_top_box(pubs[n]) or 0))
        band = jit(0.1, 0.6, seed, 'draw-band') * (1.0 if unit(seed, 'draw-band-sign') < 0.5 else -1.0)
        target = float(_top_box(pubs[top_other]) or 0) + band
        while namers and float(_top_box(pubs[draw_name]) or 0) < target and draw_lift < DRAW_LIFT_MAX:
            draw_lift = round(draw_lift + 0.1, 1)
            for sno in namers:
                scores[sno][draw_name] = clamp(scores[sno][draw_name] + 0.1, 3.0, 97.0)
            pubs[draw_name] = publish_one(draw_name)
        # above the band the room's word still holds her at each namer's own ceiling (she is at least their favorite); the salted
        # surplus over that comes back until she sits with the top character, so "the draw" reads as in line, not as a runaway
        while namers and float(_top_box(pubs[draw_name]) or 0) > target + 0.7 and draw_lift > -DRAW_LIFT_MAX:
            moved = False
            for sno in namers:
                lo = draw_floor.get(sno, scores[sno][draw_name])
                if scores[sno][draw_name] - 0.1 >= lo:
                    scores[sno][draw_name] = clamp(scores[sno][draw_name] - 0.1, 3.0, 97.0)
                    moved = True
            if not moved:
                break
            draw_lift = round(draw_lift - 0.1, 1)
            pubs[draw_name] = publish_one(draw_name)
    norm_cols = (session.get('scorecard') or {}).get('norm_columns') or {}
    rows = []
    for c in cast:
        pub = pubs[c['name']]
        top = _top_box(pub)
        pos = _top_box(pub, ('a', 'b'))
        metric = {'lead': 'lead_character', 'second lead': 'secondary_character'}.get(c['role'], 'supporting_character')
        nc = norm_cols.get(metric) or {}
        row = {'name': c['name'], 'role': c['role'], 'turns': c['turns'], 'screen_s': c['screen_s'], 'screen_clock': _clock(c['screen_s']), 'share_pct': c['share_pct'],
               'excellent_pct': top, 'total_positive_pct': pos, 'rows': pub, 'norm': nc.get('norm'), 'genre_norm': nc.get('genre_norm'), 'vs_norm_pp': None,
               'likability': round(sum(scores[s][c['name']] for s in scores) / len(scores), 1)}
        n_draw = int((ev.get('draw_counts') or {}).get(c['name']) or 0)
        if n_draw:
            row['draw_seats'] = n_draw
        rows.append(row)
    # no two characters on the same tenth of excellent, total positive or likability (salted, order kept, rows still sum to
    # 100); the norm gap reads off the settled excellent
    distinct_cast_tenths(rows, seed)
    for row in rows:
        row['vs_norm_pp'] = (round(float(row['excellent_pct']) - float(row['norm']), 1) if row.get('excellent_pct') is not None and row.get('norm') is not None else None)
    rel = None
    if pair:
        rel_scores = {sno: (scores[sno][pair[0]['name']] + scores[sno][pair[1]['name']]) / 2.0 + jit(-3.0, 3.0, seed, 'rel', sno) for sno in scores}
        for sno, g in (ev.get('rel_floor') or {}).items():
            if sno in rel_scores and g in GRADE_LINE:
                rel_scores[sno] = max(rel_scores[sno], CHAR_TOP + GRADE_LINE[g] + jit(0.3, 2.4, seed, 'rel-floor', sno))
        for sno, g in (ev.get('rel_ceiling') or {}).items():
            if sno in rel_scores and g in ('b', 'c', 'd'):
                rel_scores[sno] = min(rel_scores[sno], CHAR_TOP + GRADE_LINE[{'b': 'a', 'c': 'b', 'd': 'c'}[g]] - jit(0.3, 2.4, seed, 'rel-ceiling', sno))
        ans = {sno: _rating_probs(clamp(v, 3.0, 97.0)) for sno, v in rel_scores.items()}
        pub = _messy_rows(_publish_rows(ans, RATING, roster, segments, f"{seed}|relationship"), f"{seed}|relationship")
        rel = {'pair': [pair[0]['name'], pair[1]['name']], 'scenes_together': best, 'excellent_pct': _top_box(pub), 'total_positive_pct': _top_box(pub, ('a', 'b')), 'rows': pub}
    # who would they follow into a second episode: a share of the room per person, never a split (a seat can follow more
    # than one person, so the shares do not add to 100); the room's spoken come-back answers first, the seats' own excellent
    # ratings behind them, each seat weighted by its confidence and the people it stands for (see follow_shares). The card
    # lists the people the room puts highest, never the five with the most time on screen; anyone a seat named has a row
    picked = set()
    for pick in (ev.get('follow') or {}).values():
        if isinstance(pick, str):
            picked.add(pick)
        elif isinstance(pick, (tuple, list)) and len(pick) == 2 and pick[0] == 'pair':
            picked.update(str(n) for n in pick[1])
    bat_seats = ((session.get('scorecard') or {}).get('battery') or {}).get('by_seat') or []
    follow_all = follow_shares(names, scores, ev, roster, segments, f"{seed}|follow", battery_by_seat=bat_seats)
    follow = follow_all[:FOLLOW_LIST_MAX]
    for row in follow_all[FOLLOW_LIST_MAX:]:
        if row['name'] in picked:
            drop = next((f for f in reversed(follow) if f['name'] not in picked), None)
            if drop is None:
                break
            follow[follow.index(drop)] = row
    follow.sort(key=lambda r: (-float(r['pct']), r['name']))
    # screen time against likability: who earns more time than they get (a fact next to the appeal, never a weight on it)
    for r in rows:
        r['time_vs_like'] = ('earns more time' if r['likability'] >= CHAR_TOP - 2.0 and r['share_pct'] < 20 else 'carries the time' if r['likability'] >= CHAR_TOP - 6.0
                             else 'gets more time than the room gives back' if r['share_pct'] >= 25 and r['likability'] < CHAR_TOP - 12.0 else 'in balance')
    # the card ranks the cast by the room's rating, never by minutes on screen; the lead is the part the cut is built around
    # (a fact carried on the row as the role), not the top row
    rows.sort(key=lambda r: (-float(r['excellent_pct'] if r.get('excellent_pct') is not None else -1.0), -float(r['total_positive_pct'] if r.get('total_positive_pct') is not None else -1.0),
                             -float(r['likability']), r['name']))
    lead = next((r for r in rows if r['role'] == 'lead'), rows[0])
    read = ''
    draw_row = next((r for r in rows if r['name'] == draw_name), None) if draw_name else None
    gap = None
    if draw_row and draw_row['name'] != lead['name'] and draw_row.get('excellent_pct') is not None and lead.get('excellent_pct') is not None:
        # the room's own word leads the read: who it would come back for, rated next to the lead
        gap = float(draw_row['excellent_pct']) - float(lead['excellent_pct'])
        where = f"above {lead['name']}" if gap > 0.65 else f"under {lead['name']}" if gap < -0.65 else f"in line with {lead['name']}"
        read += (f"{draw_row['name']} is the draw: {draw_row.get('draw_seats')} of {len(roster)} would come back. "
                 f"{draw_row['name']} rates {pct1(draw_row['excellent_pct'])}% excellent, {where}, on {pct1(draw_row['share_pct'])}% of the screen time. ")
    if lead.get('excellent_pct') is not None:
        read += f"{lead['name']} rates {pct1(lead['excellent_pct'])}% excellent" + (f" against a lead norm of {float(lead['norm']):.0f}%" if lead.get('norm') is not None else '') + '. '
        if lead.get('vs_norm_pp') is not None and lead['vs_norm_pp'] >= 5:
            read += (f"Put {lead['name']} and {draw_row['name']} on the key art. " if (gap is not None and gap >= -0.65) else f"Put {lead['name']} on the key art. ")
        elif lead.get('vs_norm_pp') is not None and lead['vs_norm_pp'] <= -8:
            read += f"The room is not sold on {lead['name']} yet; the second lead carries more. "
        elif gap is not None and gap > 0.65:
            read += f"Put {draw_row['name']} on the key art. "
    gainer = next((r for r in rows if r['time_vs_like'] == 'earns more time' and r is not draw_row and r is not lead), None)
    if gainer:
        read += f"{gainer['name']} earns more screen time than the cut gives ({pct1(gainer['share_pct'])}% of the time, {pct1(gainer['excellent_pct'])}% excellent). "
    if follow:
        read += follow_sentence(follow)
    out = {'characters': rows, 'relationship': rel, 'follow': follow, 'follow_note': FOLLOW_NOTE, 'read': read.strip(),
           'by_seat': [{'seat': sno, 'name': next((r.get('name') for r in roster if int(r['seat']) == sno), ''), 'ratings': {n: _rating_pick(v) for n, v in sc.items()}} for sno, sc in scores.items()]}
    if ev.get('questions'):
        # the plain line that says the card moved with the room's own words rides at the end of the read (the card, the report
        # and the CSV all print the read), and stands on its own for anything that wants it alone
        out['note'] = update_note(ev.get('clocks') or [])
        out['read'] = (out['read'] + ' ' + out['note']).strip()
        out['from_room'] = {'questions': [q.get('id') for q in ev['questions']], 'draw': draw_name, 'draw_seats': int((ev.get('draw_counts') or {}).get(draw_name) or 0) if draw_name else 0,
                            'draw_lift': draw_lift}
    return out


# ---------------------------------------------------- pace / confusion
def _act_of_beat(b: dict) -> str:
    return str(b.get('act') or 'middle')


def pace_read(session: dict, twin: dict) -> dict:
    roster = (session.get('group') or {}).get('roster') or []
    segments = (session.get('group') or {}).get('segments') or []
    beats = session.get('beats') or []
    seat_dials = session.get('seat_dials') or {}
    events = session.get('events') or []
    if not roster or not beats:
        return {}
    sid = str(session.get('id') or '')
    mk = str((session.get('media') or {}).get('hash') or '')
    seed = f"pace|{sid}|{mk}"
    dur = float((session.get('media') or {}).get('runtime_s') or (beats[-1].get('t') if beats else 60) or 60)
    # four stretches a room talks about: the beginning (cold open and setup), the middle, the turn, the ending (close and credits)
    stretches = (('beginning', 'Beginning', ('open', 'setup')), ('middle', 'Middle', ('middle',)), ('turn', 'The turn', ('turn',)), ('ending', 'Ending', ('close', 'end')))
    acts = []
    too_slow_any = {}
    prev_means = {}
    acts_present = 0
    for key, label, members in stretches:
        idxs = [i for i, b in enumerate(beats) if _act_of_beat(b) in members]
        if not idxs:
            continue
        acts_present += 1
        t0, t1 = float(beats[idxs[0]].get('t') or 0), float(beats[idxs[-1]].get('t_end') or beats[idxs[-1]].get('t') or 0)
        ans = {}
        for r in roster:
            sno = int(r['seat'])
            series = seat_dials.get(str(sno)) or []
            vals = [series[i] for i in idxs if i < len(series)]
            if not vals:
                continue
            mean_all = sum(series) / len(series) if series else 50.0
            mean_act = sum(vals) / len(vals)
            prev = prev_means.get(sno)
            outs = sum(1 for e in events if int(e.get('seat') or -1) == sno and e.get('type') == 'check-out' and t0 <= float(e.get('t') or 0) <= t1)
            lost = sum(1 for e in events if int(e.get('seat') or -1) == sno and e.get('type') == 'confuse' and t0 <= float(e.get('t') or 0) <= t1)
            # slow when the stretch sits under the one before it (or under the seat's own watch), more so when the seat looked away;
            # fast when the seat lost the thread in a short stretch
            gap = (prev - mean_act - 2.0) if prev is not None else (mean_all - mean_act - 5.0)
            p_slow = clamp(0.06 + 0.46 * _sig(gap / 3.0) + 0.30 * min(2, outs), 0.02, 0.92)
            p_fast = clamp(0.03 + 0.22 * min(2, lost) + (0.12 if (prev is not None and mean_act > prev + 9.0 and (t1 - t0) < dur * 0.1) else 0.0), 0.01, 0.6)
            p_fast = min(p_fast, 1.0 - p_slow - 0.02)
            ans[sno] = {'a': p_slow, 'b': max(0.02, 1.0 - p_slow - p_fast), 'c': p_fast}
            if p_slow >= 0.5:
                too_slow_any[sno] = too_slow_any.get(sno, 0) + 1
            prev_means[sno] = mean_act
        pub = _publish_rows(ans, PACE, roster, segments, f"{seed}|{key}")
        acts.append({'act': key, 'label': label, 'from_clock': _clock(t0), 'to_clock': _clock(t1), 'too_slow_pct': _top_box(pub, ('a',)), 'just_right_pct': _top_box(pub, ('b',)),
                     'too_fast_pct': _top_box(pub, ('c',)), 'rows': pub})
    # overall pace: the seat's slow stretches against the stretches it watched
    overall_ans = {}
    for r in roster:
        sno = int(r['seat'])
        k = too_slow_any.get(sno, 0)
        p_slow = clamp(0.08 + 0.36 * k, 0.04, 0.9)
        overall_ans[sno] = {'a': p_slow, 'b': max(0.04, 0.96 - p_slow), 'c': 0.04}
    opub = _publish_rows(overall_ans, PACE, roster, segments, f"{seed}|overall")
    # the written confusions
    confused = []
    for e in events:
        if e.get('type') != 'confuse':
            continue
        line = str(e.get('line') or '')
        if e.get('source') != 'watch':
            b = beats[int(e['beat'])] if e.get('beat') is not None and int(e['beat']) < len(beats) else {}
            who = str(b.get('speaker') or '')
            on = str(b.get('caption') or b.get('action') or '')
            u = unit(seed, 'conf', e.get('seat'), e.get('t'))
            line = (f"Who {who} was to the others at {_clock(e.get('t'))}." if who and u < 0.5 else f"What {lower_first(on) if on else 'that scene'} meant for the story." if u < 0.8 else f"Why they did that at {_clock(e.get('t'))}.")
        confused.append({'seat': e.get('seat'), 'name': e.get('name'), 'clock': _clock(e.get('t')), 't': e.get('t'), 'line': line[:160]})
    seats_confused = len({c['seat'] for c in confused})
    conf_pct = ((session.get('scorecard') or {}).get('battery') or {}).get('headline', {}).get('confusion_pct')
    if conf_pct is None:
        conf_pct = round(clamp(100.0 * seats_confused / max(1, len(roster)) + jit(-0.4, 0.4, seed, 'c'), 0.1, 99.9), 1) if seats_confused else round(jit(0.3, 2.4, seed, 'c0'), 1)
    # the beginning and the ending on their own
    def part_rating(acts_in, tag, lift):
        # the part against the seat's own watch: every dial opens cold and settles at the credits, so the part
        # is pulled halfway to the seat's mean before it is graded
        idxs = [i for i, b in enumerate(beats) if _act_of_beat(b) in acts_in]
        ans = {}
        for r in roster:
            sno = int(r['seat'])
            series = seat_dials.get(str(sno)) or []
            vals = [series[i] for i in idxs if i < len(series)]
            if not vals:
                continue
            mean_all = sum(series) / len(series)
            ans[sno] = _rating_probs(0.5 * sum(vals) / len(vals) + 0.5 * mean_all + lift + jit(-4.0, 4.0, seed, tag, sno), top=62.0)
        pub = _publish_rows(ans, RATING, roster, segments, f"{seed}|{tag}")
        return {'excellent_pct': _top_box(pub), 'total_positive_pct': _top_box(pub, ('a', 'b')), 'rows': pub}
    beginning = part_rating(('open', 'setup'), 'beginning', 3.0)
    ending = part_rating(('close', 'end'), 'ending', 1.5)
    slow = sorted([a for a in acts if (a.get('too_slow_pct') or 0) >= 30], key=lambda a: -(a.get('too_slow_pct') or 0))
    if slow:
        rt = f"Most would trim the {slow[0]['label'].lower()} ({slow[0]['from_clock']} to {slow[0]['to_clock']}): {pct1(slow[0]['too_slow_pct'])}% found it slow."
    else:
        rt = 'The running time holds; no act read as slow for a third of the room.'
    read = (f"{pct1(_top_box(opub, ('b',)))}% call the pace just right. " + rt + f" {pct1(conf_pct)}% were confused somewhere"
            + (f"; the written confusions sit at {', '.join(sorted({c['clock'] for c in confused})[:3])}." if confused else '.')
            + f" The beginning rates {pct1(beginning.get('excellent_pct'))}% excellent, the ending {pct1(ending.get('excellent_pct'))}%.")
    return {'acts': acts, 'overall': {'too_slow_pct': _top_box(opub, ('a',)), 'just_right_pct': _top_box(opub, ('b',)), 'too_fast_pct': _top_box(opub, ('c',))},
            'confusion_pct': conf_pct, 'confused_seats': seats_confused, 'confusions': confused[:14], 'beginning': beginning, 'ending': ending, 'running_time_read': rt, 'read': read}


# --------------------------------------------------------------- scenes
def _scene_reason(session: dict, seat_no: int, scene: dict, liked: bool, seed: str) -> str:
    w = session.get('watch') or {}
    row = ((w.get('by_seat') or {}).get(str(seat_no)) or {}).get(str(scene.get('id')))
    if isinstance(row, dict) and row.get('say'):
        return str(row['say'])[:160]
    tone = str(scene.get('tone') or '')
    on = str(scene.get('caption') or scene.get('action') or 'that scene').rstrip('.')
    u = unit(seed, 'reason', seat_no, scene.get('id'), liked)
    if liked:
        bank = {'funny': ('That was the laugh of the night.', 'I did not see the joke coming.'), 'tender': ('That got me.', 'The quiet in that scene is why I watch.'),
                'action': ('Finally it moved.', 'The best stretch; I sat up.'), 'wow': ('The reveal. I would rewind it.', 'That turn earned the whole episode.'),
                'tense': ('I was holding my breath.', 'The standoff is the show.')}.get(tone, ('That scene is the one I would tell a friend about.', f"The {lower_first(on)} is where it clicked."))
    else:
        bank = {'quiet': ('It just sat there.', 'I looked at my phone.'), 'curious': ('I did not know who anyone was yet.', 'Too much setup, not enough happening.'),
                'tense': ('That dragged for me.', 'I lost the thread of who wanted what.')}.get(tone, ('It ran long.', f"The {lower_first(on)} did not land for me."))
    return bank[int(u * len(bank)) % len(bank)]


def scenes_liked(session: dict, twin: dict) -> dict:
    roster = (session.get('group') or {}).get('roster') or []
    segments = (session.get('group') or {}).get('segments') or []
    scenes = session.get('scenes') or []
    beats = session.get('beats') or []
    seat_dials = session.get('seat_dials') or {}
    if not roster or not scenes:
        return {}
    sid = str(session.get('id') or '')
    mk = str((session.get('media') or {}).get('hash') or '')
    seed = f"scenes|{sid}|{mk}"
    by_id = {int(s['id']): s for s in scenes}
    card = ((session.get('watch') or {}).get('card') or {})
    best_in, worst_in = card.get('best') or {}, card.get('worst') or {}
    best, worst = {}, {}
    for r in roster:
        sno = int(r['seat'])
        series = seat_dials.get(str(sno)) or []
        per_scene = {}
        for i, b in enumerate(beats):
            if i < len(series):
                per_scene.setdefault(b.get('scene_id'), []).append(series[i])
        means = {k: sum(v) / len(v) for k, v in per_scene.items() if v}
        if not means:
            continue
        pool = dict(means)
        if len(pool) >= 4:
            first = min(pool, key=lambda k: float((by_id.get(k) or {}).get('t_start') or 0))
            pool.pop(first, None)    # the first seconds (slate, title card) are not a scene the room judges
        b_id = best_in.get(str(sno)) if str(best_in.get(str(sno)) or '') in {str(k) for k in means} else max(pool.items(), key=lambda kv: kv[1])[0]
        w_id = worst_in.get(str(sno)) if str(worst_in.get(str(sno)) or '') in {str(k) for k in means} else min(pool.items(), key=lambda kv: kv[1])[0]
        best[sno], worst[sno] = int(b_id), int(w_id)

    def ranked(picks, liked):
        counts = {}
        for sno, scn in picks.items():
            counts.setdefault(scn, []).append(sno)
        people_by_seat = _seat_people(roster, segments)
        out = []
        for scn, seats in sorted(counts.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            s = by_id.get(scn) or {}
            quotes = [{'seat': sno, 'name': next((r.get('name') for r in roster if int(r['seat']) == sno), ''), 'line': _scene_reason(session, sno, s, liked, seed)} for sno in seats[:4]]
            out.append({'scene_id': scn, 'clock': _clock(s.get('t_start')), 'on_screen': str(s.get('caption') or s.get('action') or '')[:140], 'mentions': len(seats),
                        'people': messy_count(_subject(twin), f"scene-{scn}-{liked}", sum(people_by_seat.get(x, 0) for x in seats)), 'quotes': quotes})
        return out[:6]
    liked_best = ranked(best, True)
    liked_least = ranked(worst, False)
    # reasons for not recommending: the seats who would not
    bat = (session.get('scorecard') or {}).get('battery') or {}
    nots = []
    for row in bat.get('by_seat') or []:
        rc = (row.get('answers') or {}).get('recommend')
        if rc in ('c', 'd'):
            sno = int(row['seat'])
            why = str(row.get('why') or '')
            if not why:
                w_s = by_id.get(worst.get(sno)) or {}
                u = unit(seed, 'notrec', sno)
                why = (f"Lost the thread at {_clock(w_s.get('t_start'))}." if u < 0.35 else f"Drifted in the {str((w_s.get('act') or 'middle'))}." if u < 0.7 else f"Not my kind of {session.get('genre') or 'show'}.")
            nots.append({'seat': sno, 'name': row.get('name'), 'line': why[:160]})
    # similar titles: what the twin already follows (talent and media rows), the comps on the test
    pool = [str(t.get('label')) for t in ((twin or {}).get('identity') or {}).get('talent') or [] if t.get('label')][:6]
    pool += [str(c.get('title') or c.get('label')) for c in (session.get('comps') or []) if isinstance(c, dict) and (c.get('title') or c.get('label'))][:6]
    pool += [str(r.get('inputs', {}).get('title')) for r in (twin or {}).get('runs') or [] if isinstance(r, dict) and (r.get('inputs') or {}).get('title')][:4]
    pool = [p for p in dict.fromkeys(pool) if p and p.lower() not in ('the twin', 'a new title made for this audience')]
    similar = []
    if pool:
        counts = {}
        for r in roster:
            k = int(unit(seed, 'similar', r['seat']) * len(pool)) % len(pool)
            counts[pool[k]] = counts.get(pool[k], 0) + 1
            if unit(seed, 'similar2', r['seat']) < 0.4 and len(pool) > 1:
                k2 = (k + 1 + int(unit(seed, 'similar3', r['seat']) * (len(pool) - 1))) % len(pool)
                counts[pool[k2]] = counts.get(pool[k2], 0) + 1
        similar = [{'title': t, 'mentions': n} for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:5]
    # perceived appeal from the quadrant battery
    quads = {q.get('key'): q for q in bat.get('quadrants') or []}
    def tp(k):
        v = (quads.get(k) or {}).get('total_positive_pct')
        return float(v) if v is not None else None
    w = [v for v in (tp('women_under_35'), tp('women_35_plus')) if v is not None]
    m = [v for v in (tp('men_under_35'), tp('men_35_plus')) if v is not None]
    y = [v for v in (tp('women_under_35'), tp('men_under_35')) if v is not None]
    o = [v for v in (tp('women_35_plus'), tp('men_35_plus')) if v is not None]
    gl = 'women and men'
    if w and m:
        dw, dm = sum(w) / len(w), sum(m) / len(m)
        gl = 'women' if dw - dm >= 6 else 'men' if dm - dw >= 6 else 'women and men'
    al = 'every age'
    if y and o:
        dy, do = sum(y) / len(y), sum(o) / len(o)
        al = 'under 35' if dy - do >= 6 else '35 and over' if do - dy >= 6 else 'every age'
    appeal = {'gender': gl, 'age': al, 'line': f"Plays as a title for {gl}, {al}."}
    read = ''
    if liked_best:
        read += f"Liked best: {liked_best[0]['clock']}, {liked_best[0]['on_screen'] or 'the peak'} ({liked_best[0]['mentions']} of {len(roster)} name it). "
    if liked_least:
        read += f"Liked least: {liked_least[0]['clock']}, {liked_least[0]['on_screen'] or 'the dip'} ({liked_least[0]['mentions']} name it). "
    if nots:
        read += f"{len(nots)} would not recommend it; the first reason: {nots[0]['line']} "
    read += appeal['line']
    return {'liked_best': liked_best, 'liked_least': liked_least, 'not_recommending': nots, 'similar_titles': similar, 'appeal': appeal, 'read': read.strip()}


# ----------------------------------------------------------- components
def components(session: dict, twin: dict) -> dict:
    roster = (session.get('group') or {}).get('roster') or []
    segments = (session.get('group') or {}).get('segments') or []
    beats = session.get('beats') or []
    scenes = session.get('scenes') or []
    seat_dials = session.get('seat_dials') or {}
    events = session.get('events') or []
    if not roster or not beats:
        return {}
    sid = str(session.get('id') or '')
    mk = str((session.get('media') or {}).get('hash') or '')
    seed = f"components|{sid}|{mk}"
    norm_cols = (session.get('scorecard') or {}).get('norm_columns') or {}
    settings_n = len({str(s.get('setting') or '').lower() for s in scenes if s.get('setting')})
    rows = []
    for key, label, tone in COMPONENTS:
        if tone:
            idxs = [i for i, b in enumerate(beats) if str(b.get('tone') or '') == tone]
        elif key == 'beginning':
            idxs = [i for i, b in enumerate(beats) if _act_of_beat(b) in ('open', 'setup')]
        elif key == 'ending':
            idxs = [i for i, b in enumerate(beats) if _act_of_beat(b) in ('close', 'end')]
        elif key == 'music':
            idxs = [i for i, b in enumerate(beats) if str(b.get('cue') or '') == 'music swell']
        else:
            idxs = list(range(len(beats)))
        ans = {}
        for r in roster:
            sno = int(r['seat'])
            series = seat_dials.get(str(sno)) or []
            if not series:
                continue
            mean_all = sum(series) / len(series)
            vals = [series[i] for i in idxs if i < len(series)]
            if vals:
                v = sum(vals) / len(vals)
            else:
                v = mean_all - {'humor': 9.0, 'action': 7.0, 'suspense': 6.0, 'music': 4.0}.get(key, 2.0)
            if key in ('music', 'beginning', 'ending', 'action', 'suspense', 'humor'):
                v = 0.5 * v + 0.5 * mean_all   # a component against the seat's own watch, not the raw stretch
            if key == 'humor':
                v += 1.5 * sum(1 for e in events if int(e.get('seat') or -1) == sno and e.get('type') == 'laugh')
            elif key == 'settings':
                v += 2.2 if settings_n >= 3 else -1.0
            elif key == 'story':
                v += 0.6 * sum(1 for e in events if int(e.get('seat') or -1) == sno and e.get('type') in ('love', 'wow', 'lean-in')) - 1.2 * sum(1 for e in events if int(e.get('seat') or -1) == sno and e.get('type') == 'confuse')
            v = clamp(v + jit(-5.0, 5.0, seed, key, sno), 3.0, 97.0)
            pa = _sig((v - 62.0) / GRADE_SPREAD)
            ans[sno] = {'a': pa, 'b': 1.0 - pa}
        pub = _publish_rows(ans, [('a', 'Excellent', 'yes'), ('b', 'Less than excellent', 'no')], roster, segments, f"{seed}|{key}")
        top = _top_box(pub)
        nc = norm_cols.get(f"component_{key}") or {}
        rows.append({'key': key, 'label': label, 'excellent_pct': top, 'norm': nc.get('norm'), 'genre_norm': nc.get('genre_norm'),
                     'vs_norm_pp': (round(float(top) - float(nc['norm']), 1) if top is not None and nc.get('norm') is not None else None), 'beats_n': len(idxs)})
    return {'rows': rows, 'read': _components_read(rows)}


def _components_read(rows: list) -> str:
    strong = sorted([r for r in rows if r.get('vs_norm_pp') is not None], key=lambda r: -r['vs_norm_pp'])
    weak = sorted([r for r in rows if r.get('vs_norm_pp') is not None], key=lambda r: r['vs_norm_pp'])
    read = ''
    if strong:
        read += f"{strong[0]['label']} is the strongest component at {pct1(strong[0]['excellent_pct'])}% excellent" + (f" ({'+' if strong[0]['vs_norm_pp'] >= 0 else ''}{strong[0]['vs_norm_pp']} on the norm)" if strong[0].get('vs_norm_pp') is not None else '') + '. '
    if weak and weak[0] is not (strong[0] if strong else None):
        read += f"{weak[0]['label']} is the softest at {pct1(weak[0]['excellent_pct'])}%."
    return read.strip()


def refresh_norm_refs(session: dict) -> dict:
    """The character and component rows carry the norm column as it
    stands now (a norm re-set on the card, a re-filed ledger cell, a
    column that turned from a stand-in into a norm of the format): the
    norm, the genre norm and the gap on every row, the components read
    rebuilt from the rows, and the lead-norm figure in the characters
    read. Nothing else is re-run. Returns what moved."""
    out = {'characters': 0, 'components': 0}
    sc = (session or {}).get('scorecard') if isinstance((session or {}).get('scorecard'), dict) else {}
    cols = (sc or {}).get('norm_columns') or {}
    d = (session or {}).get('diagnostics') if isinstance((session or {}).get('diagnostics'), dict) else {}
    if not cols or not d:
        return out
    chars = d.get('characters') if isinstance(d.get('characters'), dict) else {}
    for row in (chars.get('characters') or []):
        metric = {'lead': 'lead_character', 'second lead': 'secondary_character'}.get(row.get('role'), 'supporting_character')
        nc = cols.get(metric) or {}
        if not nc:
            continue
        new_norm = nc.get('norm')
        if row.get('norm') != new_norm or row.get('genre_norm') != nc.get('genre_norm'):
            out['characters'] += 1
        row['norm'], row['genre_norm'] = new_norm, nc.get('genre_norm')
        row['vs_norm_pp'] = (round(float(row['excellent_pct']) - float(new_norm), 1) if row.get('excellent_pct') is not None and new_norm is not None else None)
    lead = next((r for r in (chars.get('characters') or []) if r.get('role') == 'lead'), None)
    if lead and lead.get('norm') is not None and chars.get('read'):
        chars['read'] = re.sub(r'against a lead norm of \d+%', f"against a lead norm of {float(lead['norm']):.0f}%", str(chars['read']))
    comps = d.get('components') if isinstance(d.get('components'), dict) else {}
    rows = comps.get('rows') or []
    for row in rows:
        nc = cols.get(f"component_{row.get('key')}") or {}
        if not nc:
            continue
        new_norm = nc.get('norm')
        if row.get('norm') != new_norm or row.get('genre_norm') != nc.get('genre_norm'):
            out['components'] += 1
        row['norm'], row['genre_norm'] = new_norm, nc.get('genre_norm')
        row['vs_norm_pp'] = (round(float(row['excellent_pct']) - float(new_norm), 1) if row.get('excellent_pct') is not None and new_norm is not None else None)
    if rows:
        comps['read'] = _components_read(rows)
    return out


# ------------------------------------------------------------- the glue
def run_diagnostics(session: dict, twin: dict, s3=None) -> dict:
    """Every screening carries the four reads. Never raises."""
    out = {}
    for key, fn in (('characters', character_table), ('pace', pace_read), ('scenes', scenes_liked), ('components', components)):
        try:
            out[key] = fn(session, twin) or {}
        except Exception as e:
            print(f"[twin-diagnostics] {key} skipped: {e}")
            out[key] = {}
    out['at'] = _now()
    session['diagnostics'] = out
    return out


def diagnostics_norm_metrics(session: dict) -> dict:
    d = (session or {}).get('diagnostics') or {}
    out = {}
    chars = (d.get('characters') or {}).get('characters') or []
    # the ledger files one mark per part: the lead and the second lead are one row each (the role the cut assigned); the
    # supporting part is the mean of every supporting row, so neither the card's order nor anyone's minutes picks the number
    marks = {}
    for c in chars:
        m = {'lead': 'lead_character', 'second lead': 'secondary_character'}.get(c.get('role'), 'supporting_character')
        if c.get('excellent_pct') is not None:
            marks.setdefault(m, []).append(float(c['excellent_pct']))
    for m, vals in marks.items():
        out[m] = round(sum(vals) / len(vals), 1) if m == 'supporting_character' else vals[0]
    rel = (d.get('characters') or {}).get('relationship') or {}
    if rel.get('excellent_pct') is not None:
        out['key_relationship'] = float(rel['excellent_pct'])
    fol = (d.get('characters') or {}).get('follow') or []
    if fol:
        # the ledger's cell is the LEAD's share ('Would follow the lead into a second episode' in the norms copy), the part
        # the cut is built around: the follow is a per-person share ordered by size, so the top row may be a supporting
        # character the room named; the lead's row is filed, the top row only when the lead has none
        lead_name = next((c.get('name') for c in chars if c.get('role') == 'lead'), None)
        row = next((f for f in fol if f.get('name') == lead_name and f.get('pct') is not None), None) or fol[0]
        out['follow_second_episode'] = float(row['pct'])
    pace = d.get('pace') or {}
    if (pace.get('overall') or {}).get('just_right_pct') is not None:
        out['component_pace_just_right'] = float(pace['overall']['just_right_pct'])
        out['pace_too_slow'] = float(pace['overall'].get('too_slow_pct') or 0)
    if (pace.get('beginning') or {}).get('excellent_pct') is not None:
        out['beginning'] = float(pace['beginning']['excellent_pct'])
    if (pace.get('ending') or {}).get('excellent_pct') is not None:
        out['ending'] = float(pace['ending']['excellent_pct'])
    for r in (d.get('components') or {}).get('rows') or []:
        if r.get('excellent_pct') is not None:
            out[f"component_{r['key']}"] = float(r['excellent_pct'])
    return out


def diagnostics_lines(session: dict) -> list:
    d = (session or {}).get('diagnostics') or {}
    out = []
    for k in ('characters', 'pace', 'scenes', 'components'):
        r = (d.get(k) or {}).get('read')
        if r:
            out.append(str(r))
    return out


__all__ = ['RATING', 'PACE', 'COMPONENTS', 'ACT_LABEL', 'FOLLOW_NOTE', 'TENTH_KEYS', 'cast_from_session', 'follow_shares', 'follow_sentence', 'follow_line',
           'distinct_cast_tenths', 'character_table', 'pace_read', 'scenes_liked', 'components', 'run_diagnostics', 'refresh_norm_refs', 'diagnostics_norm_metrics', 'diagnostics_lines']
