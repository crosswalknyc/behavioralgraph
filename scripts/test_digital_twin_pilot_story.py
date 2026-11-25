#!/usr/bin/env python3
"""Pilot Testing reads the cut with what came before (Jenna 2026-11-24).

Her words on the line "a payoff late in the cut is not read against a
setup the room saw early on": "wouldnt we want it to be contextually
aware of what happened previouslky in the show?" Then: "just build it".

Hermetic: fake models, no network. Covers:
  * the story so far rides into every later part and is updated after each
  * the story stays inside its cap
  * each archetype representative's own watch so far rides into the next part, capped
  * a payoff scene is read with its setup in hand
  * a big part splits its seats; every seat still carries its own path
  * live frames carry both (the story so far up to that second, the seat's own memory)
  * the dial's settings and weights are unchanged (goldens taken on plain main)
  * an old finished test is untouched; nothing reader-facing changes
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from test_digital_twin_pilot import _beals, _fake_cut  # noqa: E402
from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twin_room as R  # noqa: E402
from migration import digital_twin_story as S  # noqa: E402
from migration import digital_twin_watch as W  # noqa: E402
from migration import digital_twin_live as LIVE  # noqa: E402

SETUP = 'Maya hides the brass key under the lighthouse stairs'
PAYOFF = 'Maya unlocks the lighthouse door with the brass key'
SETUP_ID, MIDDLE_ID, PAYOFF_ID = 5, 15, 27


def _story_cut(n=30, scene_s=12.0):
    """A cut with a setup in the setup part and its payoff in the close."""
    scenes, caps, tr = [], {}, []
    for i in range(n):
        scenes.append({'id': i, 't_start': i * scene_s, 't_end': (i + 1) * scene_s})
        cap = {SETUP_ID: SETUP, PAYOFF_ID: PAYOFF, MIDDLE_ID: 'Maya argues with her brother Theo on the pier'}.get(i, f"Waves roll against the rocks, beat {i}")
        caps[i] = {'caption': cap, 'tone': 'tense' if i in (SETUP_ID, PAYOFF_ID) else 'quiet', 'joke': False, 'setting': 'coast', 'people': 1, 'text': '',
                   'cast': ['Ana Ortiz'] if i in (SETUP_ID, PAYOFF_ID) else []}
        tr.append({'t': i * scene_s + 2, 'end': i * scene_s + 6, 'text': 'Theo, keep the key safe.' if i == SETUP_ID else 'Did anyone see the light?'})
    return {'runtime_s': n * scene_s, 'scenes': scenes, 'captions': caps, 'transcript': tr, 'silences': [], 'genre_votes': {'drama': 3},
            'source': 'file', 'tools': {}, 'notes': []}


class Recorder:
    """The fake model: a recap that follows the scenes it is handed, and a
    room whose levels never depend on what it is told (so the dial math can
    be compared across reads), whose lines name a setup when one is in hand."""

    def __init__(self, huge_story=False):
        self.watch = []
        self.story = []
        self.huge = huge_story

    def __call__(self, role, system, user, **kw):
        d = json.loads(user)
        if role == 'watch_story':
            self.story.append(d)
            st = copy.deepcopy(d.get('story_so_far') or {})
            for k in ('who', 'set_up', 'resolved'):
                st.setdefault(k, [])
            for sc in d.get('scenes') or []:
                on = str(sc.get('on_screen') or '')
                if 'hides the brass key' in on:
                    st['set_up'].append('Maya hid the brass key under the lighthouse stairs')
                if 'unlocks the lighthouse door' in on:
                    st['set_up'] = [x for x in st['set_up'] if 'brass key' not in x]
                    st['resolved'].append('The brass key opened the lighthouse door')
                if 'Theo' in on and not any('Theo' in w for w in st['who']):
                    st['who'].append('Theo: Maya\'s brother')
            if not any('Maya' in w for w in st['who']):
                st['who'].append('Maya: the lighthouse keeper')
            st['where_it_stands'] = f"After the {d.get('part')}, the tide is coming in"
            st['tone'] = 'tense and quiet'
            if self.huge:
                st['set_up'] = st['set_up'] + [f"Open thread number {i} about the harbor and the storm that is coming in fast tonight" for i in range(40)]
                st['who'] = st['who'] + [f"Person {i}: a harbor worker who saw the light on the water late at night" for i in range(30)]
            return st
        if role == 'watch':
            self.watch.append(d)
            story = json.dumps(d.get('story_so_far') or {})
            seats = []
            for p in d['room']:
                rows = []
                for s in d['scenes']:
                    lvl = 60 + (s['id'] % 7) - (3 if p['seat'] % 2 else 0)
                    ev, say = '', ''
                    if s['id'] == SETUP_ID:
                        ev, say = 'lean-in', f"Seat {p['seat']}: where is she hiding that key?"
                    elif s['id'] == PAYOFF_ID:
                        ev = 'wow'
                        say = 'She finally used the brass key from the lighthouse stairs!' if 'brass key' in story else 'Nice door.'
                    elif s['id'] == MIDDLE_ID and p['seat'] % 2:
                        ev, say = 'check-out', 'Another argument on the pier, I looked at my phone.'
                    rows.append({'id': s['id'], 'dial': lvl, 'event': ev, 'say': say})
                seats.append({'seat': p['seat'], 'scenes': rows, 'act_note': f"The {d['act']['label']} sat fine with seat {p['seat']}.",
                              'waiting_for': 'to see what the brass key opens' if d['act']['label'] in ('setup', 'middle', 'turn') else ''})
            return {'seats': seats, '_stats': {'model': 'fake', 'input_tokens': 10, 'output_tokens': 10}}
        if role == 'battery':
            return {'seats': [{'seat': p['seat'], 'answers': {'overall': 'a', 'recommend': 'a', 'finish': 'a', 'tune_in': 'a', 'urgency_pilot': 'a',
                                                               'expectations': 'a'}, 'why': 'The key paid off.'} for p in d['room']]}
        return None


def _prepare(rec, sid='story-1', in_room=12, analysis=None, env=None, twin=None):
    twin = twin or _beals()
    sess = PI.open_session([twin], title='The Keeper', in_room=in_room, session_id=sid)
    old = {k: os.environ.get(k) for k in (env or {})}
    os.environ.update(env or {})
    R.set_model_fn(rec)
    try:
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=360, title='The Keeper', analysis=analysis or _story_cut())
    finally:
        R.set_model_fn(None)
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return sess, twin


def _parts(rec):
    """The watch calls in the order the parts came, grouped by part."""
    out = {}
    for d in rec.watch:
        out.setdefault(d['act']['label'], []).append(d)
    return out


class StorySoFar(unittest.TestCase):
    def test_story_rides_into_every_later_part_and_is_updated_after_each(self):
        rec = Recorder()
        sess, _ = _prepare(rec)
        parts = _parts(rec)
        order = [a for a in W.ACTS if a in parts]
        self.assertEqual(order, ['open', 'setup', 'middle', 'turn', 'close', 'end'])
        # one recap after every part but the last, in order, each handed the recap before it
        self.assertEqual([d['part'] for d in rec.story], order[:-1])
        for i, d in enumerate(rec.story):
            if i == 0:
                self.assertTrue(S.is_empty(d['story_so_far']))
            else:
                self.assertIn(f"After the {order[i - 1]}", d['story_so_far']['where_it_stands'])
        # the first part has nothing before it; every later part carries the story as it stood after the part before
        for d in parts['open']:
            self.assertNotIn('story_so_far', d)
        for i, a in enumerate(order[1:], 1):
            for d in parts[a]:
                self.assertIn('story_so_far', d, a)
                self.assertIn(f"After the {order[i - 1]}", d['story_so_far']['where_it_stands'], a)
                self.assertEqual(d['act']['part'], f"{i + 1} of {len(order)}")
        # the setup enters the story once the setup part has played, and is resolved once the close has
        self.assertFalse(any('brass key' in json.dumps(d['story_so_far']) for d in parts['setup']))
        self.assertTrue(all('brass key' in ' '.join(d['story_so_far']['set_up']) for d in parts['middle'] + parts['turn'] + parts['close']))
        self.assertTrue(all('brass key' in ' '.join(d['story_so_far']['resolved']) for d in parts['end']))
        # the cast matched by face stays in who is who
        self.assertTrue(any('Ana Ortiz' in w for w in parts['middle'][0]['story_so_far']['who']))
        # the record on the test keeps the story each part was read with (server-side)
        st = sess['watch']['story']
        self.assertEqual(sorted(st['stories']), sorted(order))
        self.assertIsNone(st['stories']['open'])
        self.assertIn('brass key', json.dumps(st['stories']['close']))

    def test_story_stays_inside_its_cap(self):
        rec = Recorder(huge_story=True)
        _prepare(rec, sid='story-cap')
        later = [d for d in rec.watch if d.get('story_so_far')]
        self.assertTrue(later)
        for d in later:
            st = d['story_so_far']
            self.assertLessEqual(S.story_words(st), S.STORY_MAX_WORDS)
            self.assertLessEqual(len(st['who']), S.STORY_LIMITS['who'])
            self.assertLessEqual(len(st['set_up']), S.STORY_LIMITS['set_up'])
            for line in st['who'] + st['set_up'] + st['resolved']:
                self.assertLessEqual(len(line.split()), S.STORY_ITEM_WORDS + 1)
        # the recap chain is handed the clipped story too
        for d in rec.story[1:]:
            self.assertLessEqual(S.story_words(d['story_so_far']), S.STORY_MAX_WORDS)

    def test_clip_story_unit(self):
        big = {'who': [f"Name {i}: " + 'word ' * 40 for i in range(30)], 'set_up': ['thread ' * 50] * 20, 'resolved': [f"done {i} " + 'x ' * 30 for i in range(20)],
               'where_it_stands': 'w ' * 80, 'tone': 't ' * 40}
        st = S.clip_story(big)
        self.assertLessEqual(S.story_words(st), S.STORY_MAX_WORDS)
        self.assertLessEqual(len(st['resolved']), S.STORY_LIMITS['resolved'])
        self.assertEqual(S.clip_story({}), S.empty_story())
        self.assertIsNone(S.story_for_prompt(S.empty_story()))
        self.assertNotIn('\u2014', S.story_text({'who': ['Maya \u2014 the keeper'], 'where_it_stands': 'At sea \u2013 late'}))


class SeatMemory(unittest.TestCase):
    def test_each_seat_carries_its_own_watch_so_far(self):
        rec = Recorder()
        sess, _ = _prepare(rec, sid='story-mem')
        parts = _parts(rec)
        for d in parts['open']:
            for p in d['room']:
                self.assertNotIn('your_watch_so_far', p)
        for a in ('setup', 'middle', 'turn', 'close', 'end'):
            for d in parts[a]:
                for p in d['room']:
                    mem = p.get('your_watch_so_far') or ''
                    self.assertTrue(mem.startswith('Your dial started at'), (a, p['seat'], mem))
                    self.assertLessEqual(len(mem.split()), S.MEMORY_MAX_WORDS + 1)
        # after the setup part, each seat remembers its own line and what it is waiting for
        mids = {p['seat']: p['your_watch_so_far'] for d in parts['middle'] for p in d['room']}
        for seat, mem in mids.items():
            self.assertIn(f"Seat {seat}: where is she hiding that key?", mem)
            self.assertIn('Waiting to see: to see what the brass key opens', mem)
            self.assertIn('leaned in', mem)
        # the seats that checked out in the middle carry that into the turn; the others do not
        turns = {p['seat']: p['your_watch_so_far'] for d in parts['turn'] for p in d['room']}
        for seat, mem in turns.items():
            self.assertEqual('checked out' in mem, bool(seat % 2), (seat, mem))
        # every seat's path is its own
        self.assertEqual(len(set(turns.values())), len(turns))
        # the paths are kept on the test (server-side)
        self.assertEqual(set(sess['watch']['story']['memory']), {str(r['seat']) for r in sess['group']['roster']})

    def test_memory_text_cap_keeps_the_newest(self):
        m = S.new_memory(52.0)
        rows = {}
        scene_rows = {}
        for i in range(60):
            scene_rows[i] = {'clock': f"{i // 60}:{i % 60:02d}", 'on_screen': f"A long description of scene {i} with many words in it to fill the line"}
        for part in ('open', 'setup', 'middle', 'turn', 'close'):
            rows = {i: {'dial': 50 + i % 9, 'event': 'laugh' if i % 3 == 0 else '', 'say': f"Line {i} " + 'and more words ' * 6} for i in range(60)}
            m = S.add_part(m, part, rows, scene_rows, note=f"The {part} went long " + 'and on ' * 20, waiting_for=f"what happens after the {part}")
        text = S.memory_text(m)
        self.assertLessEqual(len(text.split()), S.MEMORY_MAX_WORDS + 1)
        self.assertIn('Close: ended at', text)
        self.assertLessEqual(len(m['reactions']), S.MEMORY_REACTIONS)
        self.assertLessEqual(len(m['said']), S.MEMORY_SAID)
        self.assertEqual(S.memory_text(S.new_memory()), '')


class Payoff(unittest.TestCase):
    def test_a_payoff_is_read_with_its_setup_in_hand(self):
        rec = Recorder()
        sess, _ = _prepare(rec, sid='story-pay')
        close = _parts(rec)['close']
        self.assertTrue(close)
        for d in close:
            self.assertIn('Maya hid the brass key under the lighthouse stairs', d['story_so_far']['set_up'])
            self.assertTrue(any(s['id'] == PAYOFF_ID for s in d['scenes']))
            for p in d['room']:
                self.assertIn('where is she hiding that key?', p['your_watch_so_far'])
        lines = [e['line'] for e in sess['events'] if e.get('source') == 'watch' and e.get('type') == 'wow']
        self.assertTrue(lines)
        self.assertTrue(all('brass key from the lighthouse stairs' in l for l in lines), lines)

    def test_without_the_story_the_payoff_has_no_setup(self):
        rec = Recorder()
        sess, _ = _prepare(rec, sid='story-off', env={'DTIQ_PILOT_STORY': '0'})
        self.assertFalse(any('story_so_far' in d for d in rec.watch))
        self.assertFalse(rec.story)
        self.assertNotIn('story', sess['watch'])
        lines = [e['line'] for e in sess['events'] if e.get('source') == 'watch' and e.get('type') == 'wow']
        self.assertTrue(lines and all(l == 'Nice door.' for l in lines), lines)


class Groups(unittest.TestCase):
    def test_seat_groups_unit(self):
        ids = list(range(1, 17))
        g = S.seat_groups(ids, 46, rows_cap=96)
        self.assertEqual([len(x) for x in g], [2] * 8)
        self.assertEqual(S.seat_groups(ids, 5, rows_cap=96), [ids])
        g = S.seat_groups(ids, 16, rows_cap=96)
        self.assertEqual(sorted(sum(g, [])), ids)
        self.assertLessEqual(max(len(x) for x in g) - min(len(x) for x in g), 1)
        self.assertEqual(S.seat_groups([], 10), [])

    def test_a_big_part_splits_its_seats_and_every_seat_keeps_its_own_path(self):
        rec = Recorder()
        sess, _ = _prepare(rec, sid='story-groups', env={'DTIQ_PILOT_ROWS_PER_CALL': '24'})
        parts = _parts(rec)
        seats = {int(r['seat']) for r in sess['group']['roster']}
        for a, calls in parts.items():
            got = [p['seat'] for d in calls for p in d['room']]
            self.assertEqual(sorted(got), sorted(seats), a)     # every seat read every part, once
            self.assertGreater(len(calls), 1, a)
            for d in calls:
                self.assertLessEqual(len(d['room']) * len(d['scenes']), max(24, len(d['scenes'])), a)
        # a seat's memory in a later part is its own line, never a neighbor's
        for d in parts['close']:
            for p in d['room']:
                self.assertIn(f"Seat {p['seat']}: where is she hiding that key?", p['your_watch_so_far'])
                others = [s for s in seats if s != p['seat']]
                self.assertFalse(any(f"Seat {o}: where" in p['your_watch_so_far'] for o in others))
        self.assertTrue(all(len(sess['watch']['by_seat'][str(s)]) == 30 for s in seats))


class DialUnchanged(unittest.TestCase):
    """The settings, their weights, the people weighting, the habit pull and
    the settle at the end are the pilot's own: goldens taken on plain main
    (bg-webapp 2e30025f) before this change."""

    def _fixture(self):
        twin = _beals()
        sess = PI.open_session([twin], title='Golden', in_room=12, session_id='golden-1')
        roster = sess['group']['roster']
        an = _fake_cut('funny')
        scenes = PI.build_scenes('Golden', 300, 'mk', scenes=an['scenes'], captions=an['captions'], transcript=an['transcript'], silences=[])
        beats = PI.build_beats_from_scenes(scenes, 300, 'mk', genre='comedy')
        return twin, sess, roster, scenes, beats, 'pilot|golden-1|h|v'

    def test_constants(self):
        self.assertEqual(PI.PRIOR_WEIGHT, 0.30)
        self.assertEqual(PI.DIAL_WEIGHTING, 'people')
        self.assertEqual((LIVE.HERD_SHARE, LIVE.HERD_KEEP, LIVE.FIT_GAIN, LIVE.FIT_CAP, LIVE.DRIFT_GAIN, LIVE.DEFAULT_DIAL), (0.8, 0.45, 0.55, 4.5, 0.35, 55.0))

    def test_habit_line_blend_fit_and_room_line(self):
        twin, sess, roster, scenes, beats, seed = self._fixture()
        want = {'1': [56.981, 68.3072, 63.0821, 6924.8352, 101], '2': [55.4163, 70.8741, 60.7407, 6816.5202, 101], '3': [53.9021, 66.3072, 58.7029, 6510.6277, 101]}
        for r in roster[:3]:
            d = PI.seat_dial(twin, r, beats, seed, 'mk')
            self.assertEqual([d[0], d[len(d) // 2], d[-1], round(sum(d), 4), len(d)], want[str(r['seat'])])
        prior = PI.seat_dial(twin, roster[0], beats, seed, 'mk')
        b = PI.blend_dial(prior, beats, {int(s['id']): 70.0 + (int(s['id']) % 5) for s in scenes}, 'Jennifer Beals', str(roster[0].get('name')), 'mk', seed + '|seat1')
        self.assertEqual([b[0], b[len(b) // 2], b[-1], round(sum(b), 4), len(b)], [66.5466, 70.8372, 70.4018, 7170.5616, 101])
        fits = {}
        for i, c in enumerate([PI._card_for(twin, r, seed) for r in roster[:4]]):
            for tone in ('funny', 'tender', 'tense', 'action', 'wow', 'quiet', 'warm', 'curious'):
                for act in ('open', 'middle', 'close'):
                    fits[f"{i}|{tone}|{act}"] = round(PI._tone_fit(c, {'tone': tone, 'act': act, 'cue': '', 'joke': tone == 'funny'}, twin), 4)
        self.assertEqual(hashlib.sha1(json.dumps(fits, sort_keys=True).encode()).hexdigest(), '7d03cebca56ddb718112398f9e7e836ab2e0015b')
        w = PI.seat_weights(roster, sess['group'].get('segments') or [])
        agg = PI.aggregate_dial([PI.seat_dial(twin, r, beats, seed, 'mk') for r in roster], 'Jennifer Beals', 'mk', weights=[w.get(str(r['seat']), 0.0) for r in roster])
        self.assertEqual([agg[0], agg[len(agg) // 2], agg[-1], round(sum(agg), 4), len(agg)], [56.4979, 65.7089, 58.5276, 6639.003, 101])
        sm = LIVE.shape_moves({'id': 'g', 'live': {}}, twin, roster[:6], {'t': 30, 'tone': 'quiet', 'joke': False}, {str(r['seat']): 55.0 for r in roster[:6]},
                              {str(r['seat']): 55.0 + i for i, r in enumerate(roster[:6])}, trusted=False)
        self.assertEqual(sm, {'1': 56.69, '2': 55.57, '3': 55.02, '4': 52.55, '5': 55.6, '6': 61.95})

    def test_same_readings_give_the_same_dial_with_or_without_the_story(self):
        """The fake's levels never depend on what it is told, so any change in
        the dial between the two reads would be the math moving."""
        a, _ = _prepare(Recorder(), sid='same-dial')
        b, _ = _prepare(Recorder(), sid='same-dial', env={'DTIQ_PILOT_STORY': '0'})
        self.assertEqual(a['seat_dials'], b['seat_dials'])
        self.assertEqual(a['dial']['value'], b['dial']['value'])
        self.assertEqual(a['dial']['splits'], b['dial']['splits'])
        self.assertEqual(a['watch']['by_seat'].keys(), b['watch']['by_seat'].keys())
        for k in a['watch']['by_seat']:
            self.assertEqual({sid: r['dial'] for sid, r in a['watch']['by_seat'][k].items()}, {sid: r['dial'] for sid, r in b['watch']['by_seat'][k].items()})


class OldTests(unittest.TestCase):
    def test_a_finished_test_is_never_read_again(self):
        """A test read before this change carries no story: preparing it again
        hits the cache, calls nothing and leaves the record as it was."""
        rec = Recorder()
        sess, twin = _prepare(rec, sid='old-1', env={'DTIQ_PILOT_STORY': '0'})
        PI.stop_session(sess)
        before = {k: copy.deepcopy(sess.get(k)) for k in ('seat_dials', 'dial', 'events')}
        before_read = copy.deepcopy(sess['watch']['by_seat'])
        self.assertNotIn('story', sess['watch'])
        again = Recorder()
        R.set_model_fn(again)
        try:
            PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=360, title='The Keeper', analysis=_story_cut())
        finally:
            R.set_model_fn(None)
        self.assertEqual(again.watch, [])
        self.assertEqual(again.story, [])
        self.assertEqual(sess['watch']['by_seat'], before_read)
        self.assertNotIn('story', sess['watch'])
        for k, v in before.items():
            self.assertEqual(sess.get(k), v, k)

    def test_a_story_read_keeps_its_record_on_a_cached_prepare(self):
        rec = Recorder()
        sess, twin = _prepare(rec, sid='old-2')
        kept = copy.deepcopy(sess['watch']['story'])
        again = Recorder()
        R.set_model_fn(again)
        try:
            PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=360, title='The Keeper', analysis=_story_cut())
        finally:
            R.set_model_fn(None)
        self.assertEqual(again.watch, [])
        self.assertEqual(sess['watch']['story'], kept)


class ReaderFacing(unittest.TestCase):
    def test_the_page_payload_is_the_same_shape(self):
        a, _ = _prepare(Recorder(), sid='pub-a')
        b, _ = _prepare(Recorder(), sid='pub-b', env={'DTIQ_PILOT_STORY': '0'})
        pa, pb = PI.public_session(a), PI.public_session(b)
        self.assertEqual(sorted(pa.keys()), sorted(pb.keys()))
        self.assertEqual(pa['watch'], {'source': 'file', 'persona': True, 'scenes_n': 30, 'captions_n': 30})
        self.assertEqual(sorted(pa['scorecard'].keys()), sorted(pb['scorecard'].keys()))
        self.assertNotIn('Your dial started', json.dumps(pa))
        self.assertNotIn('story_so_far', json.dumps(pa))

    def test_prompts_stay_internal_and_plain(self):
        for text in (S.STORY_SYSTEM, S.WATCH_CONTEXT, S.LIVE_CONTEXT, W.WATCH_STORY_SYSTEM, LIVE.LIVE_SYSTEM):
            self.assertNotIn('\u2014', text)
            self.assertNotIn('\u2013', text)
        self.assertTrue(W.WATCH_STORY_SYSTEM.startswith(W.WATCH_SYSTEM[:200]))
        self.assertIn('waiting_for', W.WATCH_STORY_SYSTEM)


# ------------------------------------------------------------- live
_JPEG = base64.b64encode(b'\xff\xd8\xff' + b'0' * 900).decode('ascii')


class LiveFake:
    def __init__(self, huge=False):
        self.heads = []
        self.huge = huge

    def __call__(self, system, blocks, **kw):
        head = blocks[0]['text']
        self.heads.append(head)
        n = len(self.heads)
        seats = {}
        for line in head.split('\n'):
            if line.startswith('seat '):
                sno = line.split(':')[0].split()[1]
                seats[sno] = {'say': f"Seat {sno} thinks about frame {n}", 'move': 2 if int(sno) % 2 else -2}
        out = {'caption': 'Maya hides the brass key under the stairs' if n == 2 else f"The tide comes in, frame {n}", 'tone': 'tense', 'joke': False,
               'setting': 'coast', 'shift': n == 2, 'seats': seats,
               'reactions': [{'seat': int(next(iter(seats))), 'type': 'lean-in', 'line': 'Where is that key going?'}] if n == 2 else []}
        if 'Update the story so far' in head:
            st = {'who': ['Maya: the lighthouse keeper'], 'set_up': ['Maya hid the brass key under the stairs'], 'resolved': [],
                  'where_it_stands': f"The tide is in at frame {n}", 'tone': 'tense'}
            if self.huge:
                st['set_up'] += [f"Open thread {i} about the harbor storm coming in fast tonight with many words" for i in range(40)]
            out['story_so_far'] = st
            out['waiting'] = {s: 'to see what the brass key opens' for s in seats}
        return out, 'fake-vision', {'input_tokens': 10, 'output_tokens': 10}


def _live_session(sid='live-1'):
    twin = _beals()
    sess = PI.open_session([twin], title='The Keeper live', in_room=8, session_id=sid)
    sess['media'] = {'source': 'watch_url', 'title': 'The Keeper', 'platform_label': 'the service'}
    LIVE.seat_live(sess, {twin['meta']['key']: twin})
    LIVE.start_live(sess)
    return sess, twin


class Live(unittest.TestCase):
    def setUp(self):
        self._saved = (LIVE.regression_mode, LIVE.model_available, LIVE._vision_json)

    def tearDown(self):
        LIVE.regression_mode, LIVE.model_available, LIVE._vision_json = self._saved

    def _run(self, frames=11, huge=False, env=None):
        fake = LiveFake(huge=huge)
        LIVE.regression_mode = lambda: False
        LIVE.model_available = lambda *a, **k: True
        LIVE._vision_json = fake
        old = {k: os.environ.get(k) for k in (env or {})}
        os.environ.update(env or {})
        try:
            sess, twin = _live_session()
            for i in range(frames):
                out = LIVE.frame(sess, twin, 12.0 * i, _JPEG)
                self.assertFalse(out.get('skipped'), out)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return sess, fake

    def test_frames_carry_the_story_so_far_and_each_seats_own_memory(self):
        sess, fake = self._run()
        h = fake.heads
        self.assertIn('This is the first frame.', h[0])
        self.assertNotIn('Their watch so far', h[0])
        # from the second frame on, each seat carries its own path, its own last thought and its own reactions
        self.assertIn('Their watch so far: started', h[1])
        self.assertIn('thought: "Seat 1 thinks about frame 1"', h[1])
        self.assertIn('reacted: leaned in at 0:12', h[2])
        # the recap is asked for on the fourth frame and every sixth after it, and rides on every frame between
        asked = [i for i, x in enumerate(h) if 'Update the story so far' in x]
        self.assertEqual(asked, [3, 9])
        self.assertIn('What the room has seen so far:', h[3])
        for i in range(4, 11):
            self.assertIn('The story so far (through 0:36)' if i < 10 else 'The story so far (through 1:48)', h[i])
            self.assertIn('Maya hid the brass key under the stairs', h[i])
        self.assertIn('Since then:', h[5])
        self.assertIn('waiting to see: to see what the brass key opens', h[4])
        st = sess['live']['story']
        self.assertEqual(st['frames'], 10)
        self.assertIn('frame 10', st['story']['where_it_stands'])
        # the page never sees it
        pub = PI.public_session(sess)
        self.assertNotIn('story', pub['live'])
        self.assertNotIn('waiting', pub['live'])
        self.assertIn('frames', pub['live'])

    def test_live_story_stays_inside_its_cap(self):
        sess, fake = self._run(frames=6, huge=True)
        self.assertLessEqual(S.story_words(sess['live']['story']['story']), S.STORY_MAX_WORDS)
        for head in fake.heads:
            for line in head.split('\n'):
                if 'Their watch so far:' in line:
                    mem = line.split('Their watch so far:', 1)[1]
                    self.assertLessEqual(len(mem.split()), S.LIVE_MEMORY_MAX_WORDS + 2)

    def test_live_without_the_story_reads_as_before(self):
        sess, fake = self._run(frames=6, env={'DTIQ_PILOT_STORY': '0'})
        self.assertFalse(any('Their watch so far' in x or 'Update the story so far' in x or 'The story so far' in x for x in fake.heads))
        self.assertIn('What the room has seen so far:', fake.heads[1])
        self.assertNotIn('story', sess['live'])

    def test_live_offline_is_deterministic_and_keeps_a_story(self):
        a, twin = _live_session('live-off')
        b, _ = _live_session('live-off')
        for i in range(5):
            LIVE.frame(a, twin, 12.0 * i, _JPEG)
            LIVE.frame(b, twin, 12.0 * i, _JPEG)
        self.assertEqual(a['live']['seat_dials'], b['live']['seat_dials'])
        self.assertTrue(a['live']['story']['fallback'])
        self.assertEqual(a['live']['story'], b['live']['story'] | {'at': a['live']['story']['at']})

    def test_live_due_cadence(self):
        live = {'frames': [{}] * 2}
        self.assertFalse(S.live_story_due(live))
        live['frames'] = [{}] * 3
        self.assertTrue(S.live_story_due(live))
        live['story'] = {'frames': 4, 't': 36}
        live['frames'] = [{}] * 8
        self.assertFalse(S.live_story_due(live))
        live['frames'] = [{}] * 9
        self.assertTrue(S.live_story_due(live))


if __name__ == '__main__':
    unittest.main(verbosity=1)
