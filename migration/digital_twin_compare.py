"""Comparison, recruiting and the hooks around a screening (Digital Twins IQ
Pilot Testing, 2026-10-28).

  recut re-test     cut B on the same room: the same seats and personas
                    watch fresh (a new media hash), the memory of cut A
                    allowed in the read; dials side by side aligned by the
                    transcript, scorecard deltas, scene-by-scene deltas
                    ("the trim at 4:10 lifted the first act by 6 points").
  two audiences     two twins on one cut: one chart, two lines, where they
                    split and what each said there.
  comps             a comp set from the twin's own runs, the catalog and
                    the comps the test named; a "versus comps" column.
  recruit block     quotas on the clock (65% under 35, 50 / 50, 40% fans of
                    the lead) set before Play and seated through the room
                    builder; a comp-title fanship screener ("must have seen
                    two of these"); the invite ratio; walkouts from
                    sustained check-outs; the preview stats block and the
                    recruit concept line.
  critics room      a room seated from a critics profile when the catalog
                    has one, else a critics set with that occupation and a
                    stated reviewer lens; a critic score forecast and an
                    awards read.
  hooks             a concept test from a Greenlight run (the read lands
                    next to the odds and feeds the levers); talent attach
                    by quadrant with "who would you watch this with"; an
                    activation brief in the Spend Engine handoff shape.

Deterministic per (twin, seat, stimulus); counts keep messy last digits;
nothing here holds or quarantines a prepared test.
"""
from __future__ import annotations

import copy
import difflib
import re
import time
import uuid
from typing import Callable, Optional

from migration.digital_twin_engine import clamp, jit, messy_count, pct1, unit, human_money, human_people
from migration.digital_twin_pilot import (
    QUADRANTS, _card_for, _clock, _seat_people, _trait, aggregate_dial, quadrant_of, _is_woman, _is_man, _age, VERSION,
)
from migration.digital_twin_modes import publish_probs, softmax_probs, ordered_probs, _sig

CRITIC_RX = re.compile(r"\b(critics?|journalists?|reviewers?|press|film writers?|tv writers?)\b", re.I)
REVIEWER_LENS = ('performances', 'structure and pacing', 'craft and direction', 'originality', 'what an audience will feel', 'writing', 'tone')
SPLIT_RUN_MIN_BEATS = 3
SPLIT_GAP = 6.0


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _subject(twin: dict) -> str:
    m = (twin or {}).get('meta') or {}
    return str(m.get('subject') or m.get('name') or 'the twin')


def _head(sess: dict) -> dict:
    return (((sess or {}).get('scorecard') or {}).get('battery') or {}).get('headline') or {}


def _next_cut_label(label: str) -> str:
    m = re.match(r"^(.*?)([A-Z])$", str(label or 'Cut A').strip())
    if m and m.group(2) < 'Z':
        return f"{m.group(1)}{chr(ord(m.group(2)) + 1)}"
    return 'Cut B'


# -------------------------------------------------------------- recut
def prior_snapshot(parent: dict) -> dict:
    """What a later cut (or a later episode) carries of the earlier
    one: the frame the room is handed, the headline, every seat's dial."""
    head = _head(parent)
    frame = f"The room watched {parent.get('cut_label') or 'the earlier cut'} of {str((parent.get('media') or {}).get('title') or parent.get('title') or 'this title')}"
    if head.get('total_positive_pct') is not None:
        frame += f": total positive {head.get('total_positive_pct')}%, definitely recommend {head.get('definitely_recommend_pct')}%"
    sc = parent.get('scorecard') or {}
    if (sc.get('dead_spot') or {}).get('clock'):
        frame += f"; the dead spot sat at {sc['dead_spot']['clock']}"
    return {'id': parent.get('id'), 'title': parent.get('title'), 'cut_label': parent.get('cut_label') or 'Cut A', 'frame': frame + '.', 'scorecard': {'battery': {'headline': dict(head)}},
            'seat_dials': dict(parent.get('seat_dials') or {}), 'media_hash': (parent.get('media') or {}).get('hash')}


def open_recut(parent: dict, twins: list, *, created_by: str = '', title: str = '', session_id: str = '') -> dict:
    """Cut B on the same room. The group (seats, personas, memory) is
    the parent's; the media is new; the lineage points back."""
    from migration import digital_twin_pilot as PI
    group = copy.deepcopy(parent.get('group') or {})
    label = _next_cut_label(parent.get('cut_label') or 'Cut A')
    sess = PI.open_session(twins, in_room=int(group.get('in_room') or 12), title=title or f"{parent.get('title') or 'Test'} ({label})", created_by=created_by,
                           session_id=session_id or uuid.uuid4().hex[:10], mode=str(parent.get('mode') or 'pilot'), logline=str(parent.get('logline') or ''),
                           rough_cut=bool(parent.get('rough_cut')), critics_room=bool(parent.get('critics_room')), group=group)
    sess['cut_label'] = label
    sess['lineage'] = {'parent': parent.get('id'), 'parent_title': parent.get('title'), 'parent_cut_label': parent.get('cut_label') or 'Cut A', 'label': label, 'root': ((parent.get('lineage') or {}).get('root') or parent.get('id'))}
    sess['prior_read'] = prior_snapshot(parent)
    sess['genre'] = parent.get('genre') or ''
    sess['genre_set_by_user'] = bool(parent.get('genre_set_by_user'))
    sess['cast'] = list(parent.get('cast') or [])
    sess['comps'] = list(parent.get('comps') or [])
    sess['quotas'] = parent.get('quotas')
    sess['recruit'] = copy.deepcopy(parent.get('recruit')) if parent.get('recruit') else None
    sess['twin_keys'] = list(parent.get('twin_keys') or [])
    sess['media'] = {'title': str((parent.get('media') or {}).get('title') or parent.get('title') or ''), 'runtime_s': 0, 'source': 'upload'}
    sess['prepare_note'] = f"Add {label}. The same room watches it fresh."
    parent.setdefault('recuts', []).append({'id': sess['id'], 'label': label, 'title': sess['title'], 'at': _now()})
    parent['updated_at'] = _now()
    return sess


def _scene_text(s: dict) -> str:
    return ' '.join(f"{s.get('dialogue') or ''} {s.get('caption') or ''}".lower().split())


def align_scenes(a_scenes: list, b_scenes: list, a_dur: float, b_dur: float) -> dict:
    """Every scene of cut B against cut A: by the words when both carry
    dialogue or a caption (the transcript aligns the cuts), by position
    on the clock otherwise. Returns {pairs: [{b_id, a_id, sim, method}],
    trimmed: [a scene ids with no B match]}."""
    pairs = []
    used = {}
    for b in b_scenes:
        bt = _scene_text(b)
        best, best_sim, method = None, 0.0, 'clock'
        if len(bt) >= 12:
            for a in a_scenes:
                at = _scene_text(a)
                if len(at) < 12:
                    continue
                sim = difflib.SequenceMatcher(None, at[:400], bt[:400]).ratio()
                if sim > best_sim:
                    best, best_sim = a, sim
            if best is not None and best_sim >= 0.42:
                method = 'words'
            else:
                best, best_sim = None, 0.0
        if best is None and a_scenes:
            frac = float(b.get('t_start') or 0) / max(1.0, b_dur)
            best = min(a_scenes, key=lambda a: abs(float(a.get('t_start') or 0) / max(1.0, a_dur) - frac))
            best_sim = round(1.0 - abs(float(best.get('t_start') or 0) / max(1.0, a_dur) - frac), 3)
        if best is not None:
            pairs.append({'b_id': int(b['id']), 'a_id': int(best['id']), 'sim': round(float(best_sim), 3), 'method': method})
            used[int(best['id'])] = used.get(int(best['id']), 0) + 1
    trimmed = [int(a['id']) for a in a_scenes if int(a['id']) not in used]
    return {'pairs': pairs, 'trimmed': trimmed}


def _scene_means(sess: dict) -> dict:
    beats = sess.get('beats') or []
    dial = (sess.get('dial') or {}).get('value') or []
    acc = {}
    for i, b in enumerate(beats):
        if i < len(dial):
            acc.setdefault(b.get('scene_id'), []).append(dial[i])
    return {k: sum(v) / len(v) for k, v in acc.items() if v}


def _resample(t_src: list, v_src: list, dur_src: float, t_dst: list, dur_dst: float) -> list:
    """The source dial on the destination clock, by fraction of runtime."""
    if not t_src or not v_src:
        return []
    out = []
    j = 0
    for t in t_dst:
        frac = float(t) / max(1.0, dur_dst)
        ts = frac * max(1.0, dur_src)
        while j + 1 < len(t_src) and float(t_src[j + 1]) <= ts:
            j += 1
        out.append(round(float(v_src[min(j, len(v_src) - 1)]), 4))
    return out


def compare_recut(parent: dict, child: dict) -> dict:
    """Cut A against cut B: dials side by side on B's clock, scorecard
    deltas, scene-by-scene and act deltas, the trims and what they did."""
    a_scenes, b_scenes = parent.get('scenes') or [], child.get('scenes') or []
    a_dur = float((parent.get('media') or {}).get('runtime_s') or (a_scenes[-1]['t_end'] if a_scenes else 60) or 60)
    b_dur = float((child.get('media') or {}).get('runtime_s') or (b_scenes[-1]['t_end'] if b_scenes else 60) or 60)
    al = align_scenes(a_scenes, b_scenes, a_dur, b_dur)
    a_means, b_means = _scene_means(parent), _scene_means(child)
    a_by_id = {int(s['id']): s for s in a_scenes}
    b_by_id = {int(s['id']): s for s in b_scenes}
    scene_deltas = []
    for p in al['pairs']:
        bm, am = b_means.get(p['b_id']), a_means.get(p['a_id'])
        if bm is None or am is None:
            continue
        bs = b_by_id.get(p['b_id']) or {}
        scene_deltas.append({'b_id': p['b_id'], 'a_id': p['a_id'], 'clock': _clock(bs.get('t_start')), 'on_screen': str(bs.get('caption') or bs.get('action') or '')[:120], 'a': round(am, 1), 'b': round(bm, 1),
                             'delta': round(bm - am, 1), 'method': p['method']})
    # act deltas on the two cuts' own beats
    def act_means(sess):
        beats = sess.get('beats') or []
        dial = (sess.get('dial') or {}).get('value') or []
        acc = {}
        for i, b in enumerate(beats):
            if i < len(dial):
                acc.setdefault(str(b.get('act') or 'middle'), []).append(dial[i])
        return {k: sum(v) / len(v) for k, v in acc.items() if v}
    am_act, bm_act = act_means(parent), act_means(child)
    act_label = {'open': 'the cold open', 'setup': 'the first act', 'middle': 'the middle', 'turn': 'the turn', 'close': 'the close', 'end': 'the ending'}
    act_deltas = []
    for act in ('open', 'setup', 'middle', 'turn', 'close', 'end'):
        if act in am_act and act in bm_act:
            act_deltas.append({'act': act, 'label': act_label[act], 'a': round(am_act[act], 1), 'b': round(bm_act[act], 1), 'delta': round(bm_act[act] - am_act[act], 1)})
    trims = []
    for aid in al['trimmed']:
        s = a_by_id.get(aid) or {}
        act = str(s.get('act') or 'middle')
        ad = next((d for d in act_deltas if d['act'] == act), None)
        trims.append({'a_id': aid, 'clock': _clock(s.get('t_start')), 'on_screen': str(s.get('caption') or s.get('action') or '')[:120], 'act': act, 'act_label': act_label.get(act, act),
                      'act_delta': ad['delta'] if ad else None})
    ha, hb = _head(parent), _head(child)
    sc_deltas = []
    for key, label in (('total_positive_pct', 'Total positive'), ('definitely_recommend_pct', 'Definitely recommend'), ('excellent_pct', 'Excellent'), ('finish_pct', 'Would finish'),
                       ('tune_in_pct', 'Would watch the next one'), ('urgency_opening_pct', 'Opening weekend'), ('confusion_pct', 'Confused')):
        if ha.get(key) is not None and hb.get(key) is not None:
            sc_deltas.append({'key': key, 'label': label, 'a': ha[key], 'b': hb[key], 'delta': round(float(hb[key]) - float(ha[key]), 1)})
    bt = (child.get('dial') or {}).get('t') or []
    dial = {'t': bt, 'a': _resample((parent.get('dial') or {}).get('t') or [], (parent.get('dial') or {}).get('value') or [], a_dur, bt, b_dur), 'b': (child.get('dial') or {}).get('value') or []}
    lines = []
    tp = next((d for d in sc_deltas if d['key'] == 'total_positive_pct'), None)
    dr = next((d for d in sc_deltas if d['key'] == 'definitely_recommend_pct'), None)
    if tp:
        lines.append(f"Total positive moves {'+' if tp['delta'] >= 0 else ''}{tp['delta']} points, from {tp['a']}% to {tp['b']}%" + (f"; definitely recommend {'+' if dr['delta'] >= 0 else ''}{dr['delta']}." if dr else '.'))
    lifted = sorted([t for t in trims if t.get('act_delta') is not None and t['act_delta'] >= 2.0], key=lambda t: -t['act_delta'])
    if lifted:
        t = lifted[0]
        lines.append(f"The trim at {t['clock']} lifted {t['act_label']} by {t['act_delta']:.0f} points.")
    elif trims:
        t = trims[0]
        lines.append(f"The trim at {t['clock']} ({t['act_label']}) " + (f"moved that stretch {'+' if (t.get('act_delta') or 0) >= 0 else ''}{t.get('act_delta')} points." if t.get('act_delta') is not None else 'did not move the room.'))
    best = max(scene_deltas, key=lambda d: d['delta']) if scene_deltas else None
    worst = min(scene_deltas, key=lambda d: d['delta']) if scene_deltas else None
    if best and best['delta'] >= 3:
        lines.append(f"The biggest gain sits at {best['clock']} ({'+' if best['delta'] >= 0 else ''}{best['delta']}).")
    if worst and worst['delta'] <= -3:
        lines.append(f"The biggest loss sits at {worst['clock']} ({worst['delta']}).")
    return {'a': {'id': parent.get('id'), 'label': parent.get('cut_label') or 'Cut A', 'title': parent.get('title'), 'runtime_s': a_dur, 'headline': ha},
            'b': {'id': child.get('id'), 'label': child.get('cut_label') or 'Cut B', 'title': child.get('title'), 'runtime_s': b_dur, 'headline': hb},
            'dial': dial, 'scorecard_deltas': sc_deltas, 'scene_deltas': scene_deltas, 'act_deltas': act_deltas, 'trims': trims, 'aligned_by_words': sum(1 for p in al['pairs'] if p['method'] == 'words'),
            'same_room': (parent.get('group') or {}).get('roster') is not None and [r.get('name') for r in (parent.get('group') or {}).get('roster') or []] == [r.get('name') for r in (child.get('group') or {}).get('roster') or []],
            'read': ' '.join(lines) or 'The two cuts sit close on every number.'}


# ------------------------------------------------------- two audiences
def two_audiences(session: dict, twins_by_key: dict) -> dict:
    """Two (or more) twins on one cut: a line per twin, where they
    split, what each said there."""
    roster = (session.get('group') or {}).get('roster') or []
    seat_dials = session.get('seat_dials') or {}
    keys = []
    for r in roster:
        k = str(r.get('twin') or '')
        if k and k not in keys:
            keys.append(k)
    if len(keys) < 2:
        return {}
    media_key = str((session.get('media') or {}).get('hash') or '')
    subject = str(session.get('title') or 'test')
    ts = (session.get('dial') or {}).get('t') or []
    lines = []
    bat_seats = {int(r['seat']): (r.get('answers') or {}) for r in (((session.get('scorecard') or {}).get('battery') or {}).get('by_seat') or [])}
    segments = (session.get('group') or {}).get('segments') or []
    people_by_seat = _seat_people(roster, segments)
    for k in keys:
        seats = [r for r in roster if str(r.get('twin') or '') == k]
        series = [seat_dials[str(r['seat'])] for r in seats if str(r['seat']) in seat_dials]
        if not series:
            continue
        t = twins_by_key.get(k) or {}
        name = str(((t.get('meta') or {}).get('name')) or next((r.get('segment') for r in seats), k))
        dial = aggregate_dial(series, f"{subject}|{k}", media_key)
        probs = {int(r['seat']): {'a': 0.82 if (bat_seats.get(int(r['seat'])) or {}).get('overall') in ('a', 'b') else 0.18, 'b': 0.18 if (bat_seats.get(int(r['seat'])) or {}).get('overall') in ('a', 'b') else 0.82} for r in seats}
        tp_rows = publish_probs(probs, [('a', 'Total positive', 'yes'), ('b', 'Below', 'no')], seats, segments, f"{session.get('id')}|two|{k}|tp")
        rec = {int(r['seat']): {'a': 0.82 if (bat_seats.get(int(r['seat'])) or {}).get('recommend') == 'a' else 0.18, 'b': 0.18 if (bat_seats.get(int(r['seat'])) or {}).get('recommend') == 'a' else 0.82} for r in seats}
        dr_rows = publish_probs(rec, [('a', 'Definitely recommend', 'yes'), ('b', 'Not definitely', 'no')], seats, segments, f"{session.get('id')}|two|{k}|dr")
        lines.append({'twin_key': k, 'name': name, 'n': len(seats), 'people': messy_count(subject, f"two-{k}", sum(people_by_seat.get(int(r['seat']), 0) for r in seats)), 'dial': dial,
                      'mean': round(sum(dial) / len(dial), 1) if dial else None, 'total_positive_pct': (tp_rows[0]['pct'] if tp_rows else None),
                      'definitely_recommend_pct': (dr_rows[0]['pct'] if dr_rows else None)})
    if len(lines) < 2:
        return {}
    a, b = lines[0], lines[1]
    splits = []
    run = []
    for i in range(min(len(a['dial']), len(b['dial']), len(ts))):
        gap = a['dial'][i] - b['dial'][i]
        if abs(gap) >= SPLIT_GAP:
            if run and (gap > 0) != (run[-1][1] > 0):
                if len(run) >= SPLIT_RUN_MIN_BEATS:
                    splits.append(run)
                run = []
            run.append((i, gap))
        else:
            if len(run) >= SPLIT_RUN_MIN_BEATS:
                splits.append(run)
            run = []
    if len(run) >= SPLIT_RUN_MIN_BEATS:
        splits.append(run)
    events = session.get('events') or []
    beats = session.get('beats') or []
    seat_twin = {int(r['seat']): str(r.get('twin') or '') for r in roster}
    out_splits = []
    for run in splits[:6]:
        i0, i1 = run[0][0], run[-1][0]
        t0, t1 = float(ts[i0]), float(ts[i1])
        gap = sum(g for _i, g in run) / len(run)
        higher = a if gap > 0 else b
        lower = b if gap > 0 else a
        said = {}
        for e in events:
            if t0 - 1 <= float(e.get('t') or 0) <= t1 + 1:
                said.setdefault(seat_twin.get(int(e.get('seat') or -1), ''), []).append({'name': e.get('name'), 'type': e.get('type'), 'line': e.get('line'), 'clock': _clock(e.get('t'))})
        on = str((beats[i0] if i0 < len(beats) else {}).get('caption') or (beats[i0] if i0 < len(beats) else {}).get('action') or '')[:120]
        out_splits.append({'from_clock': _clock(t0), 'to_clock': _clock(t1), 'from_s': t0, 'to_s': t1, 'gap': round(abs(gap), 1), 'higher': higher['name'], 'lower': lower['name'], 'on_screen': on,
                           'said': {lines[j]['name']: (said.get(lines[j]['twin_key']) or [])[:3] for j in range(len(lines))}})
    out_splits.sort(key=lambda s: -s['gap'])
    read = (f"{a['name']} sits at {a['mean']} on the dial, {b['name']} at {b['mean']}; total positive {pct1(a['total_positive_pct'])}% against {pct1(b['total_positive_pct'])}%. ")
    if out_splits:
        s0 = out_splits[0]
        read += f"They split most from {s0['from_clock']} to {s0['to_clock']} ({s0['gap']:.0f} points, {s0['higher']} higher)" + (f", on {s0['on_screen'][0].lower() + s0['on_screen'][1:]}" if s0['on_screen'] else '') + '.'
    else:
        read += 'The two audiences track each other through the cut.'
    return {'lines': lines, 'splits': out_splits, 't': ts, 'read': read}


# ---------------------------------------------------------------- comps
def comp_set(payload: dict, session: dict, *, catalog_runs: Optional[list] = None, load_twin: Optional[Callable] = None, limit: int = 8) -> dict:
    """Comps for a test: the titles the test named (with any known
    numbers), the twin's Greenlight runs (titles with odds), and prior
    tests on other twins in the catalog (titles with a scorecard). The
    "versus comps" column places this test among them."""
    rows = []
    head = _head(session)
    for c in session.get('comps') or []:
        if not isinstance(c, dict):
            c = {'title': str(c)}
        title = str(c.get('title') or c.get('label') or '').strip()
        if not title:
            continue
        rows.append({'title': title[:120], 'source': 'named on the test', 'total_positive_pct': c.get('total_positive_pct'), 'definitely_recommend_pct': c.get('definitely_recommend_pct'),
                     'excellent_pct': c.get('excellent_pct'), 'odds_pct': c.get('odds_pct'), 'outcome': str(c.get('outcome') or '')[:160], 'ours': False})
    for r in (payload or {}).get('runs') or []:
        if not isinstance(r, dict) or r.get('kind') not in ('greenlight', 'content', 'compare'):
            continue
        o = r.get('outputs') or {}
        title = str(o.get('title') or (r.get('inputs') or {}).get('title') or '').strip()
        if not title or title.lower() in ('a new title made for this audience', 'the idea'):
            continue
        rows.append({'title': title[:120], 'source': 'Greenlight Lab', 'total_positive_pct': None, 'definitely_recommend_pct': None, 'excellent_pct': None, 'odds_pct': o.get('odds_pct'),
                     'outcome': (f"{o.get('odds_pct')}% odds of clearing the goal; {human_people(o.get('expected'))} expected in the first month" if o.get('odds_pct') is not None else ''), 'ours': True, 'run_id': r.get('id')})
    # prior tests on this twin and on others in the catalog (a few loads at most)
    seen = {str(session.get('id'))}
    for s in (payload or {}).get('pilot_sessions') or []:
        if str(s.get('id')) in seen or not _head(s):
            continue
        seen.add(str(s.get('id')))
        h = _head(s)
        rows.append({'title': str(s.get('title') or (s.get('media') or {}).get('title') or 'a prior test')[:120], 'source': 'a prior test on this twin', 'total_positive_pct': h.get('total_positive_pct'),
                     'definitely_recommend_pct': h.get('definitely_recommend_pct'), 'excellent_pct': h.get('excellent_pct'), 'odds_pct': None, 'outcome': '', 'ours': True, 'session_id': s.get('id')})
    loads = 0
    for run in (catalog_runs or [])[:40]:
        if loads >= 4 or load_twin is None:
            break
        key = str(run.get('key') or '')
        if not key or key == str(((payload or {}).get('meta') or {}).get('key') or ''):
            continue
        if not run.get('pilot_tests') and not run.get('has_pilot'):
            continue
        try:
            other = load_twin(key)
            loads += 1
        except Exception:
            other = None
        for s in ((other or {}).get('pilot_sessions') or [])[:2]:
            h = _head(s)
            if not h:
                continue
            rows.append({'title': str(s.get('title') or (s.get('media') or {}).get('title') or run.get('name') or 'a prior test')[:120], 'source': f"a prior test on the {run.get('name') or 'other'} twin",
                         'total_positive_pct': h.get('total_positive_pct'), 'definitely_recommend_pct': h.get('definitely_recommend_pct'), 'excellent_pct': h.get('excellent_pct'), 'odds_pct': None, 'outcome': '', 'ours': True})
    rows = rows[:limit]
    versus = {}
    for key, label in (('definitely_recommend_pct', 'definitely recommend'), ('total_positive_pct', 'total positive'), ('excellent_pct', 'excellent')):
        mine = head.get(key)
        comps = sorted([(float(r[key]), r['title']) for r in rows if r.get(key) is not None])
        if mine is None or not comps:
            continue
        mine = float(mine)
        above = [c for c in comps if c[0] <= mine]
        below = [c for c in comps if c[0] > mine]
        if above and below:
            line = f"Lands between {above[-1][1]} ({above[-1][0]:.1f}%) and {below[0][1]} ({below[0][0]:.1f}%) on {label}."
        elif above:
            line = f"Above every comp on {label}; the nearest is {above[-1][1]} at {above[-1][0]:.1f}%."
        else:
            line = f"Below every comp on {label}; the nearest is {below[0][1]} at {below[0][0]:.1f}%."
        versus[key] = {'mine': mine, 'line': line, 'rank': len(above) + 1, 'of': len(comps) + 1}
    for r in rows:
        r['versus'] = {k: (round(float(head[k]) - float(r[k]), 1) if r.get(k) is not None and head.get(k) is not None else None) for k in ('total_positive_pct', 'definitely_recommend_pct', 'excellent_pct')}
    read = ' '.join(v['line'] for v in versus.values()) or ('No comp carries a number yet; name comps on the test or run them through Greenlight Lab.' if not rows else 'The comps carry no scorecard yet.')
    return {'rows': rows, 'versus': versus, 'read': read}


# ------------------------------------------------------------ recruit
def _norm_quotas(quotas: dict) -> dict:
    q = quotas if isinstance(quotas, dict) else {}
    out = {}
    for k in ('under_35_pct', 'women_pct', 'fans_of_lead_pct'):
        try:
            v = float(q.get(k)) if q.get(k) is not None else None
        except (TypeError, ValueError):
            v = None
        out[k] = clamp(v, 0.0, 100.0) if v is not None else None
    try:
        out['must_have_seen'] = int(q.get('must_have_seen') or 0)
    except (TypeError, ValueError):
        out['must_have_seen'] = 0
    out['comps'] = [' '.join(str(c).split())[:80] for c in (q.get('comps') or []) if str(c).strip()][:8]
    out['lead'] = ' '.join(str(q.get('lead') or '').split())[:60]
    return out


def screener_titles(twin: dict, session: dict, limit: int = 6) -> list:
    """The comp titles a screener asks about: the comps named on the
    test, the people the twin follows (TALENT rows), the twin's own
    subject."""
    out = []
    for c in session.get('comps') or []:
        t = str((c.get('title') if isinstance(c, dict) else c) or '').strip()
        if t:
            out.append(t)
    ident = (twin or {}).get('identity') or {}
    for t in (ident.get('talent') or [])[:6]:
        if t.get('label'):
            out.append(str(t['label']))
    subj = _subject(twin)
    if subj and subj.lower() != 'the twin':
        out.append(subj)
    return [t for t in dict.fromkeys(out)][:limit]


def apply_quotas(session: dict, twins: list, quotas: dict, seed: str = '') -> dict:
    """Reseat the room to the quotas before Play: the four quadrants
    get their seats, the fan-of-the-lead share is marked, the comp-title
    screener runs (seats that have not seen enough are replaced by the
    next invite), the invite ratio is read off the pass rates. Returns
    the recruit block. No charge; a room that has played is left alone."""
    from migration.digital_twin_panel import build_group
    if session.get('play_charged') or session.get('questions'):
        return session.get('recruit') or {}
    q = _norm_quotas(quotas)
    twin = twins[0] if twins else {}
    g = session.get('group') or {}
    n = int(g.get('in_room') or 12)
    seed = seed or f"recruit|{session.get('id')}|{VERSION}"
    roster_now = g.get('roster') or []
    women_now = sum(1 for r in roster_now if _is_woman(r)) / max(1, len(roster_now)) * 100.0 if roster_now else 50.0
    under_now = sum(1 for r in roster_now if _age(r) < 35) / max(1, len(roster_now)) * 100.0 if roster_now else 50.0
    women_pct = q['women_pct'] if q['women_pct'] is not None else women_now
    under_pct = q['under_35_pct'] if q['under_35_pct'] is not None else under_now
    women_n = int(round(n * women_pct / 100.0))
    under_n = int(round(n * under_pct / 100.0))
    wu = int(round(women_n * under_n / float(n))) if n else 0
    wo = women_n - wu
    mu = under_n - wu
    mo = n - wu - wo - mu
    cells = [('Women under 35', {'gender': 'F', 'age': (18, 34)}, wu), ('Women 35 and over', {'gender': 'F', 'age': (35, 64)}, wo),
             ('Men under 35', {'gender': 'M', 'age': (18, 34)}, mu), ('Men 35 and over', {'gender': 'M', 'age': (35, 64)}, mo)]
    roster = []
    segs = []
    seat_no = 0
    for label, f, count in cells:
        if count <= 0:
            continue
        sub = build_group(twins, segments=[{'label': label, 'twin': str(((twin or {}).get('meta') or {}).get('key') or ''), 'filter': dict(f)}], in_room=max(2, count), seed=f"{seed}|{label}")
        taken = (sub.get('roster') or [])[:count]
        for r in taken:
            seat_no += 1
            r = dict(r)
            r['seat'] = seat_no
            r['segment'] = label
            r.pop('persona', None)
            r['memory'] = []
            roster.append(r)
        for sg in sub.get('segments') or []:
            sg = dict(sg)
            sg['seats'] = len(taken)
            segs.append(sg)
    # people: the quota cells against the twin's size, so the room still stands for its people
    total_people = sum(int(sg.get('people') or 0) for sg in segs) or int(g.get('size_people') or 0)
    # fans of the lead
    fans_n = int(round(n * (q['fans_of_lead_pct'] or 0) / 100.0)) if q['fans_of_lead_pct'] is not None else None
    if fans_n is not None:
        order = sorted(roster, key=lambda r: unit(seed, 'fan', r['seat']))
        for i, r in enumerate(order):
            r['fan_of_lead'] = i < fans_n
    # the comp-title screener
    titles = q['comps'] or screener_titles(twin, session)
    must = min(q['must_have_seen'], len(titles)) if titles else 0
    loyal = _trait(twin, 'franchise_loyalty')
    pass_rate = clamp(0.28 + loyal / 240.0, 0.2, 0.78)
    replaced = 0
    for r in roster:
        seen = [t for t in titles if unit(seed, 'seen', r['seat'], t) < pass_rate + (0.18 if r.get('fan_of_lead') else 0.0)]
        if must and len(seen) < must:
            replaced += 1
            # the next invite in the same cell has seen enough: the seat is reseated with the titles marked
            seen = titles[:must] if unit(seed, 'reseat', r['seat']) < 0.5 else titles[-must:]
        r['seen_titles'] = seen[:6]
    show_rate = clamp(0.5 + (loyal - 50) / 400.0, 0.42, 0.66)
    screen_pass = clamp(pass_rate ** max(1, must) if must else 0.9, 0.08, 0.95)
    quota_fit = 0.62
    per_seat = 1.0 / max(0.02, show_rate * screen_pass * quota_fit)
    invited = messy_count(seed, 'invited', n * per_seat)
    showed = messy_count(seed, 'showed', max(n, invited * show_rate * screen_pass))
    ratio = max(2, int(round(invited / float(n))))
    parts = []
    if q['under_35_pct'] is not None:
        parts.append(f"{q['under_35_pct']:.0f}% under 35")
    if q['women_pct'] is not None:
        parts.append(f"{q['women_pct']:.0f}% women" if abs(q['women_pct'] - 50) >= 3 else '50 / 50 women and men')
    if q['fans_of_lead_pct'] is not None:
        parts.append(f"{q['fans_of_lead_pct']:.0f}% fans of {q['lead'] or 'the lead'}")
    concept = ', '.join(parts) if parts else "the twin's own mix"
    if must and titles:
        concept += f"; must have seen {must} of {', '.join(titles[:4])}"
    session['group'] = {'twins': g.get('twins') or [str(((t or {}).get('meta') or {}).get('key') or '') for t in twins], 'segments': segs, 'size_people': total_people or int(g.get('size_people') or 0), 'in_room': len(roster) or n, 'roster': roster}
    session['quotas'] = q
    session['recruit'] = {'concept': concept, 'quotas': q, 'invited': invited, 'showed': showed, 'seated': len(roster), 'invite_ratio': f"{ratio} invited for every seat", 'invite_ratio_n': ratio,
                          'screener': {'titles': titles, 'must_have_seen': must, 'replaced': replaced}, 'cells': [{'label': lab, 'seats': cnt} for lab, _f, cnt in cells if cnt > 0],
                          'quotas_met': [{'quota': 'women', 'target_pct': round(women_pct, 1), 'seated_pct': round(100.0 * sum(1 for r in roster if _is_woman(r)) / max(1, len(roster)), 1)},
                                         {'quota': 'under 35', 'target_pct': round(under_pct, 1), 'seated_pct': round(100.0 * sum(1 for r in roster if _age(r) < 35) / max(1, len(roster)), 1)}]
                                        + ([{'quota': 'fans of the lead', 'target_pct': round(q['fans_of_lead_pct'], 1), 'seated_pct': round(100.0 * sum(1 for r in roster if r.get('fan_of_lead')) / max(1, len(roster)), 1)}] if fans_n is not None else []),
                          'walkouts': None, 'at': _now()}
    session['status'] = 'draft'
    session['watch'] = {}
    session['updated_at'] = _now()
    return session['recruit']


def walkouts(session: dict) -> dict:
    """Seats that left: two check-outs in the last two acts, or one hard
    check-out with the dial under 35 at the end."""
    roster = (session.get('group') or {}).get('roster') or []
    events = session.get('events') or []
    beats = session.get('beats') or []
    seat_dials = session.get('seat_dials') or {}
    late = {float(b.get('t') or 0) for b in beats if str(b.get('act') or '') in ('turn', 'close', 'end')}
    t_late = min(late) if late else 0.0
    out = []
    for r in roster:
        sno = int(r['seat'])
        mine = [e for e in events if int(e.get('seat') or -1) == sno and e.get('type') == 'check-out']
        late_outs = [e for e in mine if float(e.get('t') or 0) >= t_late]
        series = seat_dials.get(str(sno)) or []
        end = sum(series[-max(2, len(series) // 5):]) / max(1, len(series[-max(2, len(series) // 5):])) if series else 50.0
        hard = any(float(e.get('intensity') or 0) >= 0.7 for e in mine) and end < 35
        if len(late_outs) >= 2 or hard:
            left_at = min(float(e.get('t') or 0) for e in (late_outs or mine)) if (late_outs or mine) else None
            out.append({'seat': sno, 'name': r.get('name'), 'clock': _clock(left_at) if left_at is not None else '', 'why': (late_outs or mine)[0].get('line') if (late_outs or mine) else ''})
    pct = round(clamp(100.0 * len(out) / max(1, len(roster)) + jit(-0.3, 0.3, str(session.get('id')), 'walk'), 0.0, 99.9), 1) if out else 0.0
    return {'count': len(out), 'pct': pct, 'seats': out, 'read': (f"{len(out)} of {len(roster)} walked out, the first at {out[0]['clock']}." if out else 'Nobody walked out.')}


def finish_recruit_block(session: dict) -> dict:
    """After the watch: the walkouts join the preview stats block."""
    rec = session.get('recruit') if isinstance(session.get('recruit'), dict) else None
    w = walkouts(session)
    if rec is None:
        roster = (session.get('group') or {}).get('roster') or []
        rec = {'concept': "the twin's own mix", 'quotas': None, 'invited': None, 'showed': None, 'seated': len(roster), 'invite_ratio': '', 'screener': None, 'cells': [], 'quotas_met': [], 'at': _now()}
    rec['walkouts'] = w
    rec['stats_line'] = (f"{rec.get('seated')} seated" + (f" from {rec.get('invited'):,} invited ({rec.get('invite_ratio')})" if rec.get('invited') else '') + f"; {w['count']} walked out.")
    session['recruit'] = rec
    return rec


# ------------------------------------------------------------- critics
def find_critics_twin(catalog_runs: list) -> Optional[dict]:
    for r in catalog_runs or []:
        name = f"{r.get('name') or ''} {r.get('subject') or ''}"
        if CRITIC_RX.search(name):
            return r
    return None


def seat_critics(session: dict, seed: str = '') -> dict:
    """Turn the room into a critics room: every seat a working critic or
    journalist with a stated reviewer lens (the profile's mix of age,
    gender and the rest stays)."""
    roster = (session.get('group') or {}).get('roster') or []
    seed = seed or f"critics|{session.get('id')}"
    outlets = ('a national daily', 'a trade paper', 'a weekly magazine', 'a film and TV site', 'a regional paper', 'a podcast about television', 'a streaming newsletter')
    for i, r in enumerate(roster):
        r['occupation'] = 'Film and TV critic' if unit(seed, 'occ', i) < 0.6 else 'Entertainment journalist'
        r['reviewer_lens'] = REVIEWER_LENS[int(unit(seed, 'lens', i) * len(REVIEWER_LENS)) % len(REVIEWER_LENS)]
        r['outlet'] = outlets[int(unit(seed, 'outlet', i) * len(outlets)) % len(outlets)]
        r['education'] = r.get('education') or 'Bachelors Degree'
        r.pop('persona', None)
        r['memory'] = []
    session['critics_room'] = True
    session['status'] = 'draft' if not session.get('play_charged') else session.get('status')
    session['watch'] = {} if not session.get('play_charged') else session.get('watch') or {}
    session['updated_at'] = _now()
    return session


def critic_forecast(session: dict) -> dict:
    """A critic score forecast and an awards read from a critics room's
    scorecard and components."""
    head = _head(session)
    if head.get('total_positive_pct') is None:
        return {}
    comps = {r['key']: r for r in ((session.get('diagnostics') or {}).get('components') or {}).get('rows') or []}
    story = float((comps.get('story') or {}).get('excellent_pct') or 35.0)
    ending = float((comps.get('ending') or {}).get('excellent_pct') or 35.0)
    tp = float(head.get('total_positive_pct') or 50.0)
    dr = float(head.get('definitely_recommend_pct') or 40.0)
    sid = str(session.get('id') or '')
    score = clamp(40.0 + 0.5 * (tp - 50.0) + 0.25 * (dr - 45.0) + 0.2 * (story - 35.0) + 0.12 * (ending - 35.0) + jit(-2.5, 2.5, sid, 'critic'), 8.0, 97.0)
    lo, hi = clamp(score - 6.0 - 2.0 * unit(sid, 'crit-lo'), 3.0, 99.0), clamp(score + 6.0 + 2.0 * unit(sid, 'crit-hi'), 3.0, 99.0)
    if score >= 78 and story >= 45:
        awards = 'In the awards conversation: the critics room rates the story and the ending with the top of the field.'
    elif score >= 65:
        awards = 'Strong notices likely; an awards push needs the ending to land harder with the room.'
    elif score >= 50:
        awards = 'Mixed notices; the room splits on the story.'
    else:
        awards = 'Soft notices likely; the critics room did not go with it.'
    out = {'critic_score': round(score, 1), 'range': [round(lo, 1), round(hi, 1)], 'awards_read': awards,
           'read': f"Critic score forecast {score:.0f} (likely {lo:.0f} to {hi:.0f}). {awards}", 'norm_metrics': {'critic_score': round(score, 1)}}
    session['critics'] = out
    return out


# --------------------------------------------------------------- hooks
def concept_inputs_from_run(run: dict) -> dict:
    """What a Greenlight run hands a concept test: the idea in words."""
    inputs = (run or {}).get('inputs') or {}
    outputs = (run or {}).get('outputs') or {}
    title = str(outputs.get('title') or inputs.get('title') or inputs.get('idea') or inputs.get('logline') or 'the idea')[:160]
    parts = []
    for k in ('logline', 'idea', 'synopsis', 'treatment', 'description', 'text'):
        v = inputs.get(k)
        if v and str(v).strip() and str(v).strip() not in parts:
            parts.append(str(v).strip())
    if not parts:
        parts.append(title)
    plats = [str(p) for p in (inputs.get('platforms') or []) if str(p).strip()]
    text = ' '.join(parts)
    if plats:
        text += f" For {', '.join(plats[:3])}."
    return {'title': f"Concept: {title}", 'text': text[:6000], 'logline': parts[0][:600], 'linked_run_id': run.get('id'), 'genre': ''}


def attach_concept_to_run(run: dict, session: dict, norms: Optional[dict] = None) -> dict:
    """The concept read lands on the Greenlight run next to the odds and
    feeds the levers (a premise the room is definitely interested in
    earns a lever; a doubt about a name attached lifts the attach
    lever's read)."""
    r = session.get('mode_read') or {}
    if r.get('mode') != 'concept' or r.get('definitely_interested_pct') is None:
        return {}
    o = run.setdefault('outputs', {})
    read = {'session_id': session.get('id'), 'definitely_interested_pct': r.get('definitely_interested_pct'), 'interested_pct': r.get('interested_pct'), 'unique_pct': r.get('unique_pct'),
            'would_watch_pct': r.get('would_watch_pct'), 'who_for': (r.get('who_for') or {}).get('line'), 'strongest': (r.get('strongest') or [{}])[0].get('label'),
            'doubt': (r.get('doubt') or [{}])[0].get('label'), 'read': r.get('read'), 'at': _now()}
    norm = None
    try:
        norm = float((((norms or (session.get('scorecard') or {}).get('norms') or {}).get('all') or {}).get('tune_in') or {}).get('value') or 0) or None
    except (TypeError, ValueError):
        norm = None
    levers = o.get('levers') or []
    interest = float(r.get('definitely_interested_pct') or 0)
    base = norm or 45.0
    delta = round(clamp((interest - base) / max(1.0, base) * 10.0, -6.0, 12.0), 1)
    expected = int(o.get('expected') or 0)
    lever = {'lever': f"Lead with the premise the room liked ({(r.get('strongest') or [{}])[0].get('label', 'the premise').lower()})", 'delta_pct': delta,
             'delta_people': messy_count(str(session.get('id')), 'lever|concept', abs(expected * delta / 100.0)) * (1 if delta >= 0 else -1),
             'read': (f"{pct1(interest)}% of the room is definitely interested on the idea alone" + (f", against {base:.0f}% for a concept." if norm else '.')), 'from_concept_test': True}
    levers = [lv for lv in levers if not lv.get('from_concept_test')] + [lever]
    doubt = str((r.get('doubt') or [{}])[0].get('label') or '')
    if 'name' in doubt.lower():
        for lv in levers:
            if 'attach a name' in str(lv.get('lever') or '').lower():
                lv['read'] = f"The room's biggest doubt about the idea is that it needs a name attached ({pct1((r.get('doubt') or [{}])[0].get('pct'))}%). " + str(lv.get('read') or '')
    levers.sort(key=lambda l: -float(l.get('delta_people') or 0))
    for i, lv in enumerate(levers, 1):
        lv['rank'] = i
    o['levers'] = levers
    run['concept_read'] = read
    run['updated_at'] = _now()
    session['linked_run_id'] = run.get('id')
    return read


def talent_fit_overlap(s3, talent: str, bucket: Optional[str] = None) -> Optional[dict]:
    """The audience overlap Talent Fit IQ already holds for a name, when a
    result file carries one. Best effort; None when nothing is found."""
    if s3 is None or not talent:
        return None
    try:
        import sys as _sys
        import json as _json
        a = _sys.modules.get('app')
        bucket = bucket or (getattr(a, 'TALENT_FIT_S3_BUCKET', None) if a is not None else None)
        if not bucket:
            return None
        resp = s3.list_objects_v2(Bucket=bucket, Prefix='')
        slug = re.sub(r"[^a-z0-9]+", '', talent.lower())
        for obj in resp.get('Contents', []) or []:
            key = str(obj.get('Key') or '')
            if key.startswith('purgatory/') or not key.endswith('.json'):
                continue
            if slug and slug in re.sub(r"[^a-z0-9]+", '', key.lower()):
                doc = _json.loads(s3.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8'))
                for k in ('audience_overlap', 'overlap', 'audience_overlap_pct', 'overlap_pct'):
                    v = doc.get(k) if isinstance(doc, dict) else None
                    if v is not None:
                        return {'key': key, 'overlap': v}
                return {'key': key, 'overlap': None}
    except Exception as e:
        print(f"[twin-compare] talent fit lookup skipped: {e}")
    return None


def talent_attach(session: dict, twin: dict, talent: str, role: str = 'lead', *, s3=None) -> dict:
    """How much a name lifts interest, by quadrant, with the audience
    overlap Talent Fit IQ holds when it has one, and the room's answer
    to "who would you watch this with"."""
    from migration.digital_twin_engine import run_talent_attach
    talent = ' '.join(str(talent or '').split())[:80]
    if not talent:
        return {}
    sid = str(session.get('id') or '')
    seed = f"{sid}|talent|{talent.lower()}"
    base = run_talent_attach(twin, {'talent': talent, 'role': role}, seed)
    out = base.get('outputs') or {}
    roster = (session.get('group') or {}).get('roster') or []
    segments = (session.get('group') or {}).get('segments') or []
    bat = (session.get('scorecard') or {}).get('battery') or {}
    quads = {q.get('key'): q for q in bat.get('quadrants') or []}
    total_tune = None
    for q in bat.get('questions') or []:
        if q.get('id') in ('tune_in', 'finish') and q.get('yes_pct') is not None:
            total_tune = float(q['yes_pct'])
            break
    by_quad = []
    for key, label in QUADRANTS:
        qd = quads.get(key) or {}
        if not qd.get('n'):
            by_quad.append({'key': key, 'label': label, 'n': 0, 'reach_lift_pct': None})
            continue
        rel = (float(qd.get('definitely_recommend_pct') or 0) / max(1.0, float(quads.get('total', {}).get('definitely_recommend_pct') or 50.0))) if quads.get('total') else 1.0
        # a quadrant already sold on the title gains less from a name; the colder one gains more
        lift = clamp(float(out.get('reach_lift_pct') or 6) * (1.35 - 0.35 * clamp(rel, 0.4, 1.6)) * (1 + jit(-0.06, 0.06, seed, key)), 0.5, 60.0)
        by_quad.append({'key': key, 'label': label, 'n': qd.get('n'), 'people': qd.get('people'), 'reach_lift_pct': round(lift, 1)})
    # who would you watch this with
    opts = [('a', 'My partner', 'yes'), ('b', 'Friends', 'yes'), ('c', 'My kids or family', 'yes'), ('d', 'On my own', 'maybe'), ('e', 'A watch party or group chat', 'yes')]
    probs = {}
    for r in roster:
        rel_ = str(r.get('relationship') or '').lower()
        par = str(r.get('parental') or '').lower()
        age = _age(r)
        sc = {'a': 50.0 + (12.0 if any(k in rel_ for k in ('married', 'partner', 'relationship')) else -8.0), 'b': 50.0 + (9.0 if age < 35 else -3.0),
              'c': 50.0 + (11.0 if ('parent' in par or 'child' in par or 'kids' in par) and 'no' not in par[:3] else -10.0), 'd': 50.0 + (6.0 if age >= 45 else 0.0) + (5.0 if _trait(twin, 'second_screen') < 40 else 0.0),
              'e': 50.0 + (8.0 if age < 30 else -6.0) + (4.0 if _trait(twin, 'word_of_mouth') >= 60 else 0.0)}
        sc = {k: v + jit(-4.0, 4.0, seed, 'with', r['seat'], k) for k, v in sc.items()}
        probs[int(r['seat'])] = softmax_probs(sc, temperature=5.0)
    with_rows = publish_probs(probs, opts, roster, segments, f"{seed}|with") if roster else []
    with_rows_sorted = sorted(with_rows, key=lambda r: -float(r['pct']))
    overlap = talent_fit_overlap(s3, talent)
    best_q = max([q for q in by_quad if q.get('reach_lift_pct') is not None], key=lambda q: q['reach_lift_pct'], default=None)
    read = (f"{talent} as {role} lifts first-month reach {pct1(out.get('reach_lift_pct'))}% ({human_people(out.get('expected'))} more people)" + (f", most among {best_q['label'].lower()} ({pct1(best_q['reach_lift_pct'])}%)." if best_q else '.'))
    if with_rows_sorted:
        read += f" Asked who they would watch it with: {with_rows_sorted[0]['label'].lower()} ({pct1(with_rows_sorted[0]['pct'])}%)."
    if overlap and overlap.get('overlap') is not None:
        read += f" Talent Fit IQ puts the audience overlap at {overlap['overlap']}."
    rec = {'talent': talent, 'role': role, 'outputs': out, 'drivers': base.get('drivers'), 'recommendation': base.get('recommendation'), 'by_quadrant': by_quad, 'watch_with': with_rows,
           'talent_fit': overlap, 'read': read, 'at': _now()}
    session.setdefault('talent_attach', []).append(rec)
    session['talent_attach'] = session['talent_attach'][-6:]
    session['updated_at'] = _now()
    return rec


def activation_brief(session: dict, twin: dict) -> dict:
    """The segments that scored highest on definitely recommend become an
    activation brief in the shape the Spend Engine and Launch Lab hand
    off (campaign name, text, channels with spend, goal) plus the
    segments and a Launch Lab line to type."""
    roster = (session.get('group') or {}).get('roster') or []
    segments = (session.get('group') or {}).get('segments') or []
    bat = (session.get('scorecard') or {}).get('battery') or {}
    by_seat = {int(r['seat']): (r.get('answers') or {}) for r in bat.get('by_seat') or []}
    if not roster or not by_seat:
        return {}
    from migration.digital_twin_pilot import split_specs
    sid = str(session.get('id') or '')
    people_by_seat = _seat_people(roster, segments)
    groups = []
    for key, label, group, pred in split_specs(roster, twin):
        seats = [r for r in roster if pred(r)]
        if len(seats) < 2 or len(seats) == len(roster):
            continue
        probs = {int(r['seat']): {'a': 0.84 if by_seat.get(int(r['seat']), {}).get('recommend') == 'a' else 0.16, 'b': 0.16 if by_seat.get(int(r['seat']), {}).get('recommend') == 'a' else 0.84} for r in seats}
        rows = publish_probs(probs, [('a', 'Definitely recommend', 'yes'), ('b', 'Not definitely', 'no')], seats, segments, f"{sid}|brief|{key}")
        if not rows:
            continue
        groups.append({'key': key, 'label': label, 'group': group, 'n': len(seats), 'people': messy_count(_subject(twin), f"brief-{key}", sum(people_by_seat.get(int(r['seat']), 0) for r in seats)),
                       'definitely_recommend_pct': rows[0]['pct']})
    groups.sort(key=lambda g: (-g['definitely_recommend_pct'], -g['n']))
    top = []
    seen_groups = set()
    for g in groups:
        if g['group'] in seen_groups and len(top) >= 2:
            continue
        top.append(g)
        seen_groups.add(g['group'])
        if len(top) >= 3:
            break
    m = (twin or {}).get('meta') or {}
    name = f"{m.get('name') or m.get('subject') or 'the twin'} launch"
    plats = sorted(((twin or {}).get('identity') or {}).get('platforms') or [], key=lambda p: -float(p.get('reach_pct') or 0))[:3]
    chan_names = [str(p.get('label')) for p in plats] or ['Meta', 'YouTube', 'Google']
    budget = messy_count(sid, 'brief-budget', 118_000)
    weights = [float(p.get('reach_pct') or 1) for p in plats] or [1.0, 1.0, 1.0]
    tot = sum(weights) or 1.0
    channels = [{'channel': c, 'spend': messy_count(sid, f"brief-spend-{c}", budget * w / tot)} for c, w in zip(chan_names, weights)]
    head = _head(session)
    goal_val = messy_count(sid, 'brief-goal', sum(g['people'] for g in top) * 0.35) if top else messy_count(sid, 'brief-goal', 250_000)
    goal = {'metric': 'viewers', 'value': goal_val, 'platform': chan_names[0] if chan_names else '', 'window_days': 42}
    seg_text = '; '.join(f"{g['label']} ({pct1(g['definitely_recommend_pct'])}% definitely recommend, {human_people(g['people'])} people)" for g in top)
    chan_text = ', '.join(f"{c['channel']} {human_money(c['spend'])}" for c in channels)
    text = (f"Campaign: {name}. Segments to activate: {seg_text}. Channels: {chan_text}. "
            f"Window: {goal['window_days']} days. Conversion: {goal['metric']} ({goal['value']:,}). Daily tracking on.")
    aim = top[0]['label'] if top else 'the whole room'
    launch_prompt = f"Run Launch Lab on the {m.get('name') or m.get('subject') or 'this'} twin: {chan_text} over {goal['window_days'] // 7} weeks, goal {human_people(goal['value'])} viewers, aimed at {aim}"
    brief = {'campaign_name': name, 'text': text, 'channels': channels, 'goal': goal, 'segments': top, 'all_segments': groups[:12], 'launch_prompt': launch_prompt,
             'headline': {'definitely_recommend_pct': head.get('definitely_recommend_pct'), 'total_positive_pct': head.get('total_positive_pct')},
             'read': (f"Activate {top[0]['label'].lower()} first: {pct1(top[0]['definitely_recommend_pct'])}% definitely recommend, {human_people(top[0]['people'])} people." if top else 'The whole room is the segment.'), 'at': _now()}
    session['activation_brief'] = brief
    session['updated_at'] = _now()
    return brief


__all__ = ['CRITIC_RX', 'REVIEWER_LENS', 'prior_snapshot', 'open_recut', 'align_scenes', 'compare_recut', 'two_audiences', 'comp_set', 'screener_titles', 'apply_quotas', 'walkouts',
           'finish_recruit_block', 'find_critics_twin', 'seat_critics', 'critic_forecast', 'concept_inputs_from_run', 'attach_concept_to_run', 'talent_fit_overlap', 'talent_attach',
           'activation_brief']
