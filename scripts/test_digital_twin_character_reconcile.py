#!/usr/bin/env python3
"""Pilot Testing: the scorecard reconciles with what the room said out loud
(Jenna, 2026-11-04: "I asked the group if jennifer beals would make them
watch more and they said yes and they all loved her. so why is her rating
so low ... she should have a rating higher than scott caan and inline with
LL Cool J based on just that").

Hermetic (REGRESSION_TEST_MODE=1, no model, no network).
"""
from __future__ import annotations

import copy
import json
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twin_diagnostics as D  # noqa: E402
from migration import digital_twin_reconcile as RC  # noqa: E402
from migration import digital_twin_room as R  # noqa: E402
from migration import digital_twin_synthesis as SYN  # noqa: E402
from test_digital_twin_pilot import _beals, FakeS3  # noqa: E402

LEAD, SECOND, SUPPORT = 'Mae Carter', 'Ray Dunn', 'Lorelai Voss'
# the lead and the second lead talk through the cut; the supporting character has two short stretches in the middle
TRANSCRIPT = ([{'t': t, 'end': t + 4, 'text': 'We are closed', 'speaker': LEAD} for t in range(10, 560, 60)]
              + [{'t': t, 'end': t + 4, 'text': 'Not for me', 'speaker': SECOND} for t in range(30, 580, 60)]
              + [{'t': 280, 'end': 284, 'text': 'Sit down, both of you', 'speaker': SUPPORT}, {'t': 340, 'end': 344, 'text': 'I run this room', 'speaker': SUPPORT},
                 {'t': 400, 'end': 404, 'text': 'Bring me the file', 'speaker': SUPPORT}])
BANNED = ('synth', 'synthetic', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap', 's3://', 'worker', 'persona', 'agent', 'panel', 'sample', 'research', '\u2014', '\u2013')
NOW = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _no_banned(obj):
    blob = json.dumps(obj, default=str).lower()
    return [b for b in BANNED if b in blob]


def _whole(v) -> bool:
    return int(round(float(v) * 10)) % 10 == 0


def _scored(in_room=16, title='A procedural pilot', quiet_support=False):
    """A scored sixteen-seat test with three named people. ``quiet_support``
    sinks every seat's dial through the supporting character's scenes (the
    NCIS: New York shape: her scenes were the quiet ops-room stretch), so
    the inferred card reads her under both leads before the room speaks."""
    twin = _beals()
    sess = PI.open_session([twin], title='Pilot', in_room=in_room, session_id='pilottest0001')   # a fixed id keeps every salted draw, the roster included, the same run to run
    sess['cast'] = [LEAD, SECOND, SUPPORT]
    sess['play_charged'] = True
    PI.prepare_watch(sess, {'jb': twin}, duration_s=600, title=title, transcript=TRANSCRIPT)
    if quiet_support:
        # an ordinary room (the leads near the lead norm) whose dial sagged through her scenes
        hers = set(next(c for c in D.cast_from_session(sess) if c['name'] == SUPPORT)['scenes'])
        for sno, series in sess['seat_dials'].items():
            for i, b in enumerate(sess['beats']):
                if i < len(series):
                    series[i] = max(3.0, series[i] - 7.0 - (9.0 if b.get('scene_id') in hers else 0.0))
        D.run_diagnostics(sess, twin)
    PI.stop_session(sess)
    return twin, sess


def _rec(qid, q, options, picks, *, clock='9:55', framing='', kind='open', lines=None, stimulus=None):
    """A finished room question: options [(id, label, lean)], picks {seat: option id}."""
    rec = {'id': qid, 'q': q, 'framing': framing, 'kind': kind, 'stage': 'done', 'engine': 'room-2', 'asked_at': NOW, 'started_at': NOW, 'finished_at': NOW,
           'playhead_clock': clock, 'playhead_s': 595.0, 'options': [{'id': i, 'label': lab, 'lean': lean, 'pct': 0.0, 'people': 0, 'seats': []} for i, lab, lean in options],
           'option_labels': [lab for _i, lab, _l in options], 'answers': [], 'voices': [], 'read': 'The room answered.', 'distribution': [], 'by_segment': [], 'tally': []}
    for seat, oid in sorted(picks.items()):
        rec['answers'].append({'seat': seat, 'option': oid, 'confidence': 4, 'line': (lines or {}).get(seat, 'Sure.')})
    if stimulus:
        rec['stimulus'] = stimulus
    return rec


def _watch_more(namers, pair_seats, rest, lines=None):
    picks = {}
    for s in namers:
        picks[s] = 'a'
    for s in pair_seats:
        picks[s] = 'b'
    for s in rest:
        picks[s] = 'c'
    return _rec('q1', f"what would make you watch another episode? if you knew there was more {SUPPORT}?",
                [('a', 'More of the woman running the team brings me back', 'yes'), ('b', 'More quiet partner moments between the two of them', 'yes'),
                 ('c', 'Only if the next case has a real hook', 'maybe'), ('d', 'Nothing here, one episode was enough for me', 'no')],
                picks, framing=f"At 9:55 the cut has ended. What would get you to press play on episode two, and does more of the woman running the team change that?", lines=lines)


class Classification(unittest.TestCase):
    def test_watch_more_question_maps_options_to_characters(self):
        twin, sess = _scored()
        rec = _watch_more(range(1, 11), range(11, 16), [16])
        b = RC.question_bearing(rec, sess)
        self.assertEqual(b['kind'], 'watch_more')
        self.assertEqual(b['option_character']['a'], SUPPORT, 'the woman the question named')
        self.assertEqual(b['option_character']['b'], ('pair', [LEAD, SECOND]), 'partner moments are the key relationship')
        self.assertIsNone(b['option_character']['c'])
        self.assertIsNone(b['option_character']['d'], 'a no option never names a reason to come back')

    def test_feel_questions_and_battery_and_scene_questions_are_classified(self):
        twin, sess = _scored()
        feel = _rec('q2', f"how did you feel about {SUPPORT}?", [('a', 'She fixed it, strongest person on screen', 'yes'), ('b', 'Liked her, but wanted more of her', 'maybe'), ('c', 'Too stiff, no warmth', 'no')], {1: 'a', 2: 'b', 3: 'c'})
        self.assertEqual(RC.question_bearing(feel, sess)['kind'], 'character')
        pair = _rec('q3', f"how did you feel about {LEAD} and {SECOND} as a pair?", [('a', 'The two of them are why I would keep watching', 'yes'), ('b', 'Liked them fine apart', 'maybe'), ('c', 'Did not register', 'no')], {1: 'a', 2: 'b', 3: 'c'})
        self.assertEqual(RC.question_bearing(pair, sess)['kind'], 'pair')
        keep = _rec('q4', 'Would you watch a second episode?', [('a', 'Yes', 'yes'), ('b', 'No', 'no')], {1: 'a', 2: 'b'})
        self.assertEqual(RC.question_bearing(keep, sess), {'kind': 'battery', 'measure': 'tune_in', 'clock': '9:55'})
        rec_q = _rec('q5', 'Would you recommend this to a friend?', [('a', 'Definitely', 'yes'), ('b', 'Probably not', 'no')], {1: 'a', 2: 'b'})
        self.assertEqual(RC.question_bearing(rec_q, sess)['measure'], 'recommend')
        scene = _rec('q6', 'Did the diner scene at 5:00 land for you?', [('a', 'Yes, it landed', 'yes'), ('b', 'No, it lost me', 'no')], {1: 'a', 2: 'b'})
        b = RC.question_bearing(scene, sess)
        self.assertEqual(b['kind'], 'scene')
        self.assertEqual(b['target']['mode'], 'clock')

    def test_a_question_that_bears_on_nothing_is_none(self):
        twin, sess = _scored()
        hypo = _rec('q7', 'If the next episode gave her the final scene instead of the two men, would that change how you rate the show overall, or just how you rate her?',
                    [('a', '1 star', 'no'), ('b', '3 stars', 'maybe'), ('c', '5 stars', 'yes')], {1: 'c', 2: 'b'}, kind='rating')
        self.assertIsNone(RC.question_bearing(hypo, sess))
        price = _rec('q8', 'Would you pay $4.99 a month for this?', [('a', 'Yes', 'yes'), ('b', 'No', 'no')], {1: 'a'})
        self.assertIsNone(RC.question_bearing(price, sess))
        unfinished = _watch_more(range(1, 11), range(11, 16), [16])
        unfinished['finished_at'] = None
        self.assertIsNone(RC.question_bearing(unfinished, sess), 'a question still running bears on nothing yet')


class TheDraw(unittest.TestCase):
    def test_ten_of_sixteen_naming_the_supporting_character_makes_her_the_draw(self):
        twin, sess = _scored(quiet_support=True)
        before = copy.deepcopy(sess['diagnostics']['characters'])
        by_name = {c['name']: c for c in before['characters']}
        self.assertEqual(by_name[LEAD]['role'], 'lead')
        self.assertEqual(by_name[SUPPORT]['role'], 'supporting')
        self.assertLess(by_name[SUPPORT]['share_pct'], 25.0)
        # before the room speaks the inferred card reads her under both leads, the NCIS: New York shape
        self.assertLess(by_name[SUPPORT]['excellent_pct'], by_name[SECOND]['excellent_pct'])
        self.assertNotEqual(before['follow'][0]['name'], SUPPORT)
        self.assertNotIn('note', before)
        namers = list(range(1, 11))
        lines = {s: 'She held the room every time she was in it, I want more of her running it.' for s in namers}
        sess['questions'] = [_watch_more(namers, range(11, 16), [16], lines=lines)]
        out = RC.reconcile_session(sess, twin)
        self.assertTrue(out['changed'])
        self.assertEqual(out['applied'], ['q1'])
        self.assertIn('characters', out['measures'])
        ch = sess['diagnostics']['characters']
        rows = {c['name']: c for c in ch['characters']}
        lead, second, supp = rows[LEAD], rows[SECOND], rows[SUPPORT]
        # her excellent sits above the second lead and in line with the lead (the room's best-rated other character)
        top_other = max(lead['excellent_pct'], second['excellent_pct'])
        self.assertGreater(supp['excellent_pct'], second['excellent_pct'])
        self.assertGreaterEqual(supp['excellent_pct'] - top_other, -0.7, (supp['excellent_pct'], lead['excellent_pct'], second['excellent_pct']))
        self.assertLessEqual(supp['excellent_pct'] - top_other, 1.4, (supp['excellent_pct'], lead['excellent_pct'], second['excellent_pct']))
        self.assertEqual(supp['draw_seats'], 10)
        # she leads the follow with a share that says ten of sixteen named her; the three shares sum to 100.0
        follow = ch['follow']
        self.assertEqual(follow[0]['name'], SUPPORT)
        self.assertGreaterEqual(follow[0]['pct'], 55.0)
        self.assertLessEqual(follow[0]['pct'], 62.0)
        self.assertAlmostEqual(sum(f['pct'] for f in follow), 100.0, places=6)
        # messy digits everywhere the card prints a share
        for v in [supp['excellent_pct'], lead['excellent_pct'], second['excellent_pct']] + [f['pct'] for f in follow] + [ch['relationship']['excellent_pct']]:
            self.assertFalse(_whole(v), v)
        for c in ch['characters']:
            self.assertAlmostEqual(sum(r['pct'] for r in c['rows']), 100.0, places=6)
        # the note and the read
        self.assertEqual(ch['note'], "Updated after the room's answer at 9:55.")
        self.assertTrue(ch['read'].endswith(ch['note']))
        self.assertIn(f"{SUPPORT} is the draw: 10 of 16 would come back.", ch['read'])
        self.assertTrue(any(w in ch['read'] for w in (f"in line with {LEAD}", f"above {LEAD}")), ch['read'])
        self.assertIn(f"say {SUPPORT}", ch['read'])
        self.assertEqual(ch['from_room']['draw'], SUPPORT)
        # the lead rows moved only as much as that forced
        self.assertLess(abs(lead['excellent_pct'] - by_name[LEAD]['excellent_pct']), 3.0)
        # screen time is still a fact on the card, untouched
        self.assertEqual(supp['screen_clock'], by_name[SUPPORT]['screen_clock'])
        self.assertEqual(supp['share_pct'], by_name[SUPPORT]['share_pct'])
        # a seat who named her never rates her below good
        for row in ch['by_seat']:
            if row['seat'] in namers:
                self.assertIn(row['ratings'][SUPPORT], ('a', 'b', 'c'))
        self.assertEqual(_no_banned(ch), [])
        # applied once: a second pass changes nothing
        again = RC.reconcile_session(sess, twin)
        self.assertFalse(again['changed'])
        self.assertEqual(sess['reconcile']['applied'], {'q1': NOW})
        self.assertEqual(sess['reconcile']['notes'], [ch['note']])

    def test_a_minority_naming_her_lifts_her_but_does_not_anchor_her_to_the_lead(self):
        twin, sess = _scored(quiet_support=True)
        before = {c['name']: c for c in sess['diagnostics']['characters']['characters']}
        sess['questions'] = [_watch_more([1, 2, 3], range(4, 14), [14, 15, 16])]
        RC.reconcile_session(sess, twin)
        ch = sess['diagnostics']['characters']
        rows = {c['name']: c for c in ch['characters']}
        self.assertGreater(rows[SUPPORT]['excellent_pct'], before[SUPPORT]['excellent_pct'])
        self.assertIsNone(ch['from_room']['draw'])
        self.assertNotEqual(ch['follow'][0]['name'], SUPPORT)
        self.assertNotIn('is the draw', ch['read'])

    def test_a_feel_question_with_no_answers_caps_the_character(self):
        twin, sess = _scored()
        before = {c['name']: c for c in sess['diagnostics']['characters']['characters']}
        sess['questions'] = [_rec('q2', f"how did you feel about {SECOND}?", [('a', 'The best thing in it', 'yes'), ('b', 'Liked him fine', 'maybe'), ('c', 'Too stiff, no warmth', 'no')],
                                  {s: 'c' for s in range(1, 17)})]
        RC.reconcile_session(sess, twin)
        rows = {c['name']: c for c in sess['diagnostics']['characters']['characters']}
        self.assertLess(rows[SECOND]['excellent_pct'], before[SECOND]['excellent_pct'])
        self.assertLess(rows[SECOND]['excellent_pct'], 15.0)
        self.assertAlmostEqual(rows[LEAD]['excellent_pct'], before[LEAD]['excellent_pct'], delta=0.3, msg='a question about one character leaves the others alone')


class ScreenTime(unittest.TestCase):
    def test_time_on_screen_never_moves_appeal(self):
        twin, sess = _scored()
        beats, seat_dials = sess['beats'], sess['seat_dials']
        cast = D.cast_from_session(sess)
        lead = next(c for c in cast if c['name'] == LEAD)
        short = dict(lead, share_pct=4.0, screen_s=20.0)
        long = dict(lead, share_pct=70.0, screen_s=2000.0)
        series = seat_dials['1']
        a = D._seat_char_score(series, beats, short, {}, twin, 'seed|seat1', events=sess['events'], seat_no=1)
        b = D._seat_char_score(series, beats, long, {}, twin, 'seed|seat1', events=sess['events'], seat_no=1)
        self.assertEqual(a, b)

    def test_reactions_in_a_characters_scenes_move_appeal(self):
        twin, sess = _scored()
        beats, seat_dials = sess['beats'], sess['seat_dials']
        cast = D.cast_from_session(sess)
        supp = next(c for c in cast if c['name'] == SUPPORT)
        series = seat_dials['1']
        quiet = D._seat_char_score(series, beats, supp, {}, twin, 'seed|seat1', events=[], seat_no=1)
        loved = D._seat_char_score(series, beats, supp, {}, twin, 'seed|seat1', events=[{'seat': 1, 'type': 'love', 'scene_id': supp['scenes'][0]}, {'seat': 1, 'type': 'lean-in', 'scene_id': supp['scenes'][0]}], seat_no=1)
        lost = D._seat_char_score(series, beats, supp, {}, twin, 'seed|seat1', events=[{'seat': 1, 'type': 'check-out', 'scene_id': supp['scenes'][0]}], seat_no=1)
        self.assertGreater(loved, quiet)
        self.assertLess(lost, quiet)
        self.assertLessEqual(loved - quiet, D.REACT_CAP + 1e-9)


class Battery(unittest.TestCase):
    def test_a_keep_watching_question_sets_tune_in_from_the_seats(self):
        twin, sess = _scored()
        head0 = copy.deepcopy(sess['scorecard']['battery']['headline'])
        sess['questions'] = [_rec('q1', 'Would you watch a second episode?', [('a', 'Yes', 'yes'), ('b', 'No', 'no')], {s: ('a' if s <= 15 else 'b') for s in range(1, 17)})]
        out = RC.reconcile_session(sess, twin)
        self.assertIn('battery', out['measures'])
        bat = sess['scorecard']['battery']
        tune = {int(r['seat']): r['answers']['tune_in'] for r in bat['by_seat']}
        self.assertEqual([tune[s] for s in range(1, 16)], ['a'] * 15)
        self.assertEqual(tune[16], 'b')
        self.assertGreater(bat['headline']['tune_in_pct'], 80.0)
        self.assertEqual(sess['scorecard']['reconcile_notes'], ["Updated after the room's answer at 9:55."])
        self.assertEqual(sess['watch']['card']['answers']['16']['tune_in'], 'b')
        q = next(x for x in bat['questions'] if x['id'] == 'tune_in')
        self.assertAlmostEqual(sum(r['pct'] for r in q['rows']), 100.0, places=6)
        self.assertEqual(head0.get('finish_pct'), bat['headline'].get('finish_pct'), 'the finish answer was not asked about and did not move')
        self.assertTrue(sess.get('forecast'))
        self.assertTrue(sess['scorecard'].get('norm_lines'))

    def test_a_recommend_question_moves_definitely_recommend(self):
        twin, sess = _scored()
        sess['questions'] = [_rec('q1', 'Would you recommend it to a friend?', [('a', 'Definitely', 'yes'), ('b', 'Probably not', 'no')], {s: 'a' for s in range(1, 17)})]
        RC.reconcile_session(sess, twin)
        bat = sess['scorecard']['battery']
        self.assertTrue(all(r['answers']['recommend'] == 'a' for r in bat['by_seat']))
        self.assertGreater(bat['headline']['definitely_recommend_pct'], 70.0)
        self.assertFalse(_whole(bat['headline']['definitely_recommend_pct']))


class Scenes(unittest.TestCase):
    def test_a_question_naming_a_scene_by_its_clock_moves_liked_best_and_least(self):
        twin, sess = _scored()
        scene = sess['scenes'][len(sess['scenes']) // 2]
        clock = D._clock(scene['t_start'])
        sess['questions'] = [_rec('q1', f"Did the scene at {clock} land for you?", [('a', 'Yes, it landed', 'yes'), ('b', 'No, it lost me', 'no')], {s: ('a' if s <= 11 else 'b') for s in range(1, 17)})]
        out = RC.reconcile_session(sess, twin)
        self.assertIn('scenes', out['measures'])
        sc = sess['diagnostics']['scenes']
        self.assertEqual(sc['liked_best'][0]['scene_id'], int(scene['id']))
        self.assertEqual(sc['liked_best'][0]['mentions'], 11)
        least = next(s for s in sc['liked_least'] if s['scene_id'] == int(scene['id']))
        self.assertEqual(least['mentions'], 5)
        self.assertTrue(sc['read'].endswith("Updated after the room's answer at 9:55."))


class NothingBearing(unittest.TestCase):
    def test_a_question_that_bears_on_nothing_leaves_the_card_unchanged(self):
        twin, sess = _scored()
        before = copy.deepcopy({'diagnostics': sess['diagnostics'], 'scorecard': sess['scorecard']})
        sess['questions'] = [_rec('q1', 'Would you pay $4.99 a month for this?', [('a', 'Yes', 'yes'), ('b', 'No', 'no')], {s: 'a' for s in range(1, 17)})]
        out = RC.reconcile_session(sess, twin)
        self.assertFalse(out['changed'])
        self.assertEqual(sess['diagnostics'], before['diagnostics'])
        self.assertEqual(sess['scorecard'], before['scorecard'])
        self.assertNotIn('reconcile', sess)


class Landing(unittest.TestCase):
    def test_the_in_place_landing_of_a_finished_question_reconciles_the_stored_test(self):
        twin, sess = _scored()
        key = 'digital-twins/admin/Test_0000.json.gz'
        twin['meta']['key'] = key
        twin['pilot_sessions'] = [sess]
        s3 = FakeS3()
        SYN.save_twin(s3, key, twin)
        rec = _watch_more(range(1, 11), range(11, 16), [16])
        self.assertTrue(R._update_payload(s3, key, sess['id'], rec, (sess.get('group') or {}).get('roster')))
        self.assertNotIn('note', sess['diagnostics']['characters'], 'the in-memory copy is not the stored one')
        got = SYN.load_twin(s3, key)['pilot_sessions'][0]
        ch = got['diagnostics']['characters']
        self.assertEqual(ch['follow'][0]['name'], SUPPORT)
        self.assertEqual(ch['from_room']['draw'], SUPPORT)
        self.assertEqual(got['reconcile']['applied'], {'q1': NOW})
        self.assertEqual(got['questions'][0]['id'], 'q1')

    def test_a_focus_group_room_without_a_scorecard_is_left_alone(self):
        sess = {'id': 'room1', 'questions': [], 'group': {'roster': []}}
        out = RC.after_question(sess, {'id': 'q1', 'finished_at': NOW}, {})
        self.assertFalse(out['changed'])
        self.assertNotIn('reconcile', sess)


class Report(unittest.TestCase):
    def test_the_report_and_lines_carry_the_draw(self):
        from migration import digital_twin_report as REP
        twin, sess = _scored(quiet_support=True)
        sess['questions'] = [_watch_more(range(1, 11), range(11, 16), [16])]
        RC.reconcile_session(sess, twin)
        pages = REP.report_pages(twin, sess)
        chars_page = next(p for p in pages if p.get('head') == ('Character', 'Excellent', 'Norm and screen time'))
        self.assertEqual(chars_page['header'], f"{SUPPORT} is the draw: 10 of 16 would come back.")
        concl = pages[-1]['body']
        self.assertTrue(any(line.startswith(f"Give {SUPPORT} more of the next episode") for line in concl), concl)
        tldr = pages[1]['body']
        self.assertTrue(any(SUPPORT in line for line in tldr), tldr)
        self.assertEqual(_no_banned(pages), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
