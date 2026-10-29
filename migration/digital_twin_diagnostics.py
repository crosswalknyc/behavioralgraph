"""Diagnostics on every screening (Digital Twins IQ Pilot Testing, 2026-10-28).

What a studio reads after the topline:

  characters   the cast from the transcript, the captions and the script
               (names, speaker turns, time on screen); every named
               character rated excellent to poor by every seat; the key
               relationship rated; screen time against likability; who
               the room would follow into a second episode; norms for the
               lead, the second lead and the supporting cast; a plain read
               ("put her on the key art").
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
"""
from __future__ import annotations

import re
from typing import Optional

from migration.digital_twin_engine import clamp, jit, messy_count, pct1, unit
from migration.digital_twin_pilot import (
    QUADRANTS, _card_for, _clock, _seat_people, _trait, quadrant_of,
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


def _seat_char_score(seat_series: list, beats: list, char: dict, card: dict, twin: dict, seed: str) -> float:
    """Where a seat puts a character: its own dial across the character's
    scenes (the scenes carry the character), the lead's time on screen,
    salted."""
    idxs = [i for i, b in enumerate(beats) if b.get('scene_id') in set(char.get('scenes') or [])]
    if idxs and seat_series:
        vals = [seat_series[i] for i in idxs if i < len(seat_series)]
        base = sum(vals) / len(vals) if vals else 50.0
    else:
        base = (sum(seat_series) / len(seat_series)) if seat_series else 50.0
    base += clamp(float(char.get('share_pct') or 0) / 12.0, 0.0, 4.5)   # a lead with time on screen earns a little room
    loyal = _trait(twin, 'franchise_loyalty')
    base += (loyal - 50) * 0.04
    return clamp(base + jit(-7.0, 7.0, seed, 'char', char.get('name')), 3.0, 97.0)


def _rating_probs(s: float, top: float = 62.0) -> dict:
    """Excellent to poor from a 0 to 100 score as graded probabilities;
    ``top`` is the excellent line (an ordinary dial around the mid
    fifties lands the lead near the published lead norm)."""
    return ordered_probs(s, (top, top - 6.0, top - 12.0, top - 18.0), [o[0] for o in RATING])


def _rating_pick(s: float, top: float = 62.0) -> str:
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
    cards = {int(r['seat']): _card_for(twin, r, seed) for r in roster}
    scores = {}
    for r in roster:
        sno = int(r['seat'])
        scores[sno] = {c['name']: _seat_char_score(seat_dials.get(str(sno)) or [], beats, c, cards[sno], twin, f"{seed}|seat{sno}") for c in cast}
    norm_cols = (session.get('scorecard') or {}).get('norm_columns') or {}
    rows = []
    for c in cast:
        ans = {sno: _rating_probs(scores[sno][c['name']]) for sno in scores}
        pub = _publish_rows(ans, RATING, roster, segments, f"{seed}|{c['name']}")
        top = _top_box(pub)
        pos = _top_box(pub, ('a', 'b'))
        metric = {'lead': 'lead_character', 'second lead': 'secondary_character'}.get(c['role'], 'supporting_character')
        nc = norm_cols.get(metric) or {}
        rows.append({'name': c['name'], 'role': c['role'], 'turns': c['turns'], 'screen_s': c['screen_s'], 'screen_clock': _clock(c['screen_s']), 'share_pct': c['share_pct'],
                     'excellent_pct': top, 'total_positive_pct': pos, 'rows': pub, 'norm': nc.get('norm'), 'genre_norm': nc.get('genre_norm'),
                     'vs_norm_pp': (round(float(top) - float(nc['norm']), 1) if top is not None and nc.get('norm') is not None else None),
                     'likability': round(sum(scores[s][c['name']] for s in scores) / len(scores), 1)})
    # the key relationship: the two characters most often in a scene together
    pair, best = None, -1
    for i in range(len(cast)):
        for j in range(i + 1, len(cast)):
            shared = len(set(cast[i]['scenes']) & set(cast[j]['scenes']))
            if shared > best:
                best, pair = shared, (cast[i], cast[j])
    rel = None
    if (pair is None or best < 1) and len(cast) >= 2:
        pair, best = (cast[0], cast[1]), 0      # no shared scene came through: the two leads are the relationship a room is asked about
    if pair:
        ans = {sno: _rating_probs((scores[sno][pair[0]['name']] + scores[sno][pair[1]['name']]) / 2.0 + jit(-3.0, 3.0, seed, 'rel', sno)) for sno in scores}
        pub = _publish_rows(ans, RATING, roster, segments, f"{seed}|relationship")
        rel = {'pair': [pair[0]['name'], pair[1]['name']], 'scenes_together': best, 'excellent_pct': _top_box(pub), 'total_positive_pct': _top_box(pub, ('a', 'b')), 'rows': pub}
    # who would they follow into a second episode
    top5 = cast[:5]
    follow_ans = {sno: softmax_probs({c['name']: scores[sno][c['name']] + jit(-2.0, 2.0, seed, 'follow', sno, c['name']) for c in top5}, temperature=3.5) for sno in scores}
    fopts = [(c['name'], c['name'], 'yes') for c in top5]
    fpub = _publish_rows(follow_ans, fopts, roster, segments, f"{seed}|follow")
    follow = [{'name': r['id'], 'pct': round(float(r['pct']), 1), 'people': int(r['people'])} for r in sorted(fpub, key=lambda r: -float(r['pct']))]
    # screen time against likability: who earns more time than they get
    for r in rows:
        r['time_vs_like'] = ('earns more time' if r['likability'] >= 60 and r['share_pct'] < 20 else 'carries the time' if r['likability'] >= 56 else 'gets more time than the room gives back' if r['share_pct'] >= 25 and r['likability'] < 50 else 'in balance')
    lead = rows[0]
    read = ''
    if lead.get('excellent_pct') is not None:
        read = f"{lead['name']} rates {pct1(lead['excellent_pct'])}% excellent" + (f" against a lead norm of {float(lead['norm']):.0f}%" if lead.get('norm') is not None else '') + '. '
        if lead.get('vs_norm_pp') is not None and lead['vs_norm_pp'] >= 5:
            read += f"Put {lead['name']} on the key art. "
        elif lead.get('vs_norm_pp') is not None and lead['vs_norm_pp'] <= -8:
            read += f"The room is not sold on {lead['name']} yet; the second lead carries more. "
    gainer = next((r for r in rows[1:] if r['time_vs_like'] == 'earns more time'), None)
    if gainer:
        read += f"{gainer['name']} earns more screen time than the cut gives ({pct1(gainer['share_pct'])}% of the time, {pct1(gainer['excellent_pct'])}% excellent). "
    if follow:
        read += f"Asked who they would follow into a second episode, {pct1(follow[0]['pct'])}% say {follow[0]['name']}."
    return {'characters': rows, 'relationship': rel, 'follow': follow, 'read': read.strip(), 'by_seat': [{'seat': sno, 'name': next((r.get('name') for r in roster if int(r['seat']) == sno), ''),
                                                                                                   'ratings': {n: _rating_pick(v) for n, v in sc.items()}} for sno, sc in scores.items()]}


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
            line = (f"Who {who} was to the others at {_clock(e.get('t'))}." if who and u < 0.5 else f"What {on[0].lower() + on[1:] if on else 'that scene'} meant for the story." if u < 0.8 else f"Why they did that at {_clock(e.get('t'))}.")
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
                'tense': ('I was holding my breath.', 'The standoff is the show.')}.get(tone, ('That scene is the one I would tell a friend about.', f"The {on[0].lower() + on[1:]} is where it clicked."))
    else:
        bank = {'quiet': ('It just sat there.', 'I looked at my phone.'), 'curious': ('I did not know who anyone was yet.', 'Too much setup, not enough happening.'),
                'tense': ('That dragged for me.', 'I lost the thread of who wanted what.')}.get(tone, ('It ran long.', f"The {on[0].lower() + on[1:]} did not land for me."))
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
    strong = sorted([r for r in rows if r.get('vs_norm_pp') is not None], key=lambda r: -r['vs_norm_pp'])
    weak = sorted([r for r in rows if r.get('vs_norm_pp') is not None], key=lambda r: r['vs_norm_pp'])
    read = ''
    if strong:
        read += f"{strong[0]['label']} is the strongest component at {pct1(strong[0]['excellent_pct'])}% excellent" + (f" ({'+' if strong[0]['vs_norm_pp'] >= 0 else ''}{strong[0]['vs_norm_pp']} on the norm)" if strong[0].get('vs_norm_pp') is not None else '') + '. '
    if weak and weak[0] is not (strong[0] if strong else None):
        read += f"{weak[0]['label']} is the softest at {pct1(weak[0]['excellent_pct'])}%."
    return {'rows': rows, 'read': read.strip()}


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
    for c in chars:
        m = {'lead': 'lead_character', 'second lead': 'secondary_character'}.get(c.get('role'), 'supporting_character')
        if c.get('excellent_pct') is not None and m not in out:
            out[m] = float(c['excellent_pct'])
    rel = (d.get('characters') or {}).get('relationship') or {}
    if rel.get('excellent_pct') is not None:
        out['key_relationship'] = float(rel['excellent_pct'])
    fol = (d.get('characters') or {}).get('follow') or []
    if fol:
        out['follow_second_episode'] = float(fol[0]['pct'])
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


__all__ = ['RATING', 'PACE', 'COMPONENTS', 'ACT_LABEL', 'cast_from_session', 'character_table', 'pace_read', 'scenes_liked', 'components', 'run_diagnostics',
           'diagnostics_norm_metrics', 'diagnostics_lines']
