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
        # the follow is a share of the room per person, never a split: her share says ten of sixteen named her (by the people
        # those seats stand for, at their confidence); the leads carry their own shares from the seats that named the pair and
        # the seats that rate them excellent and would come back, so the three do not add to 100
        follow = {f['name']: f for f in ch['follow']}
        self.assertGreaterEqual(follow[SUPPORT]['pct'], 55.0, follow)
        self.assertLessEqual(follow[SUPPORT]['pct'], 62.0, follow)
        self.assertEqual(follow[SUPPORT]['seats'], 10)
        self.assertGreater(sum(f['pct'] for f in ch['follow']), 100.0, ch['follow'])
        self.assertEqual(ch['follow_note'], 'A seat can follow more than one person.')
        # messy digits everywhere the card prints a share
        for v in [supp['excellent_pct'], lead['excellent_pct'], second['excellent_pct']] + [f['pct'] for f in ch['follow']] + [ch['relationship']['excellent_pct']]:
            self.assertFalse(_whole(v), v)
        for c in ch['characters']:
            self.assertAlmostEqual(sum(r['pct'] for r in c['rows']), 100.0, places=6)
        # the note and the read: the moderator's follow line names the top two
        self.assertEqual(ch['note'], "Updated after the room's answer at 9:55.")
        self.assertTrue(ch['read'].endswith(ch['note']))
        self.assertIn(f"{SUPPORT} is the draw: 10 of 16 would come back.", ch['read'])
        self.assertTrue(any(w in ch['read'] for w in (f"in line with {LEAD}", f"above {LEAD}")), ch['read'])
        top2 = ch['follow'][:2]
        self.assertIn(f"Asked who they would follow into a second episode, {top2[0]['pct']}% say {top2[0]['name']} and {top2[1]['pct']}% say {top2[1]['name']}.", ch['read'])
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
        self.assertGreaterEqual(next(f['pct'] for f in ch['follow'] if f['name'] == SUPPORT), 55.0, ch['follow'])
        self.assertEqual(ch['from_room']['draw'], SUPPORT)
        self.assertEqual(got['reconcile']['applied'], {'q1': NOW})
        self.assertEqual(got['questions'][0]['id'], 'q1')

    def test_a_focus_group_room_without_a_scorecard_is_left_alone(self):
        sess = {'id': 'room1', 'questions': [], 'group': {'roster': []}}
        out = RC.after_question(sess, {'id': 'q1', 'finished_at': NOW}, {})
        self.assertFalse(out['changed'])
        self.assertNotIn('reconcile', sess)


class FollowShares(unittest.TestCase):
    """Who they would follow into a second episode is a share of the room
    per person (Jenna, 2026-11-04: "this should be as high as jennifer if
    not higher for ll cool j ... make him 66.1"): a seat can follow more
    than one person, so the shares do not add to 100 and are never
    renormalized; a seat naming a pair counts toward both; spoken answers
    outrank the inferred rating; messy tenths."""

    ROSTER = [{'seat': s, 'name': f"Seat {s}", 'segment': 'Room'} for s in range(1, 9)]
    SEGMENTS = [{'label': 'Room', 'people': 8_000_000}]

    def _ev(self, follow=None, come_back=None):
        return {'follow': follow or {}, 'come_back': come_back or {}, 'draw': {}, 'draw_counts': {}}

    def test_a_seat_naming_a_pair_counts_toward_both_and_the_shares_add_past_100(self):
        names = ['Ann', 'Bo', 'Cy']
        scores = {s: {n: 30.0 for n in names} for s in range(1, 9)}          # nobody is rated excellent: the spoken word alone
        pair = ('pair', ['Ann', 'Bo'])
        ev = self._ev(follow={s: pair for s in (1, 2, 3, 4)}, come_back={s: {'lean': 'yes', 'conf': 5, 'named': pair, 'asked_about': []} for s in (1, 2, 3, 4)})
        rows = {r['name']: r for r in D.follow_shares(names, scores, ev, self.ROSTER, self.SEGMENTS, 'seed|pair')}
        self.assertAlmostEqual(rows['Ann']['pct'], 50.0, delta=0.4)        # four of eight seats at full confidence, salted off the whole number
        self.assertAlmostEqual(rows['Bo']['pct'], 50.0, delta=0.4)
        self.assertEqual((rows['Ann']['seats'], rows['Bo']['seats'], rows['Cy']['seats']), (4, 4, 0))
        self.assertLess(rows['Cy']['pct'], 0.5)
        self.assertGreater(rows['Ann']['pct'] + rows['Bo']['pct'] + rows['Cy']['pct'], 100.0, 'never renormalized to 100')
        for r in rows.values():
            self.assertFalse(_whole(r['pct']), r)
        self.assertNotEqual(rows['Ann']['pct'], rows['Bo']['pct'], 'no two people on the same tenth')

    def test_confidence_weights_the_spoken_word(self):
        names = ['Ann', 'Bo']
        scores = {s: {n: 30.0 for n in names} for s in range(1, 9)}
        sure = self._ev(follow={s: 'Ann' for s in range(1, 9)}, come_back={s: {'lean': 'yes', 'conf': 5, 'named': 'Ann', 'asked_about': []} for s in range(1, 9)})
        unsure = self._ev(follow={s: 'Ann' for s in range(1, 9)}, come_back={s: {'lean': 'yes', 'conf': 1, 'named': 'Ann', 'asked_about': []} for s in range(1, 9)})
        a_sure = next(r for r in D.follow_shares(names, scores, sure, self.ROSTER, self.SEGMENTS, 'seed|conf') if r['name'] == 'Ann')
        a_unsure = next(r for r in D.follow_shares(names, scores, unsure, self.ROSTER, self.SEGMENTS, 'seed|conf') if r['name'] == 'Ann')
        self.assertGreater(a_sure['pct'], 99.0)
        self.assertAlmostEqual(a_unsure['pct'], 80.0, delta=0.4, msg='a seat sure at 1 of 5 carries 0.8 of its people')
        self.assertFalse(_whole(a_sure['pct']) or _whole(a_unsure['pct']))

    def test_an_excellent_rating_follows_only_when_the_seat_would_come_back_and_the_spoken_word_outranks_it(self):
        names = ['Ann', 'Bo']
        hi = {'Ann': 80.0, 'Bo': 80.0}                                      # both rated excellent by every seat, and surely so
        scores = {s: dict(hi) for s in range(1, 9)}
        # four seats would come back for Bo (named), four would come back only if the case hooks (maybe): Ann is followed
        # by her excellent ratings alone, the maybes at half
        ev = self._ev(follow={s: 'Bo' for s in (1, 2, 3, 4)},
                      come_back={**{s: {'lean': 'yes', 'conf': 4, 'named': 'Bo', 'asked_about': []} for s in (1, 2, 3, 4)},
                                 **{s: {'lean': 'maybe', 'conf': 4, 'named': None, 'asked_about': []} for s in (5, 6, 7, 8)}})
        rows = {r['name']: r for r in D.follow_shares(names, scores, ev, self.ROSTER, self.SEGMENTS, 'seed|inferred')}
        self.assertGreater(rows['Bo']['pct'], rows['Ann']['pct'], 'the four who named Bo follow him in full; Ann rides on her ratings')
        self.assertGreater(rows['Ann']['pct'], 40.0, rows)
        # the same room, but every seat was asked about Ann by name and named Bo instead: it has spoken on Ann, and her
        # excellent ratings no longer stand in for a follow
        asked = self._ev(follow={s: 'Bo' for s in range(1, 9)}, come_back={s: {'lean': 'yes', 'conf': 4, 'named': 'Bo', 'asked_about': ['Ann']} for s in range(1, 9)})
        rows2 = {r['name']: r for r in D.follow_shares(names, scores, asked, self.ROSTER, self.SEGMENTS, 'seed|asked')}
        self.assertLess(rows2['Ann']['pct'], 0.5, rows2)
        self.assertGreater(rows2['Bo']['pct'], 90.0)
        # and a seat that would not come back follows nobody, however it rated them
        gone = self._ev(come_back={s: {'lean': 'no', 'conf': 5, 'named': None, 'asked_about': []} for s in range(1, 9)})
        rows3 = {r['name']: r for r in D.follow_shares(names, scores, gone, self.ROSTER, self.SEGMENTS, 'seed|gone')}
        self.assertTrue(all(r['pct'] < 0.5 for r in rows3.values()), rows3)

    def _room(self, lead_strength: str):
        """The NCIS: New York shape: ten of sixteen name the supporting
        character as what brings them back, five name the two leads
        together, one wants a hook. ``lead_strength`` 'strong' lifts the
        room through the lead's scenes (every seat rates the lead
        excellent, surely); 'weak' sinks it (few do)."""
        twin, sess = _scored(quiet_support=True)
        cast = {c['name']: c for c in D.cast_from_session(sess)}
        his = set(cast[LEAD]['scenes']) - set(cast[SECOND]['scenes']) - set(cast[SUPPORT]['scenes'])
        first = min(his)
        roster = sess['group']['roster']
        for sno, series in sess['seat_dials'].items():
            for i, b in enumerate(sess['beats']):
                if i < len(series) and b.get('scene_id') in his:
                    series[i] = min(97.0, series[i] + 14.0) if lead_strength == 'strong' else max(3.0, series[i] - 16.0)
        sess['events'] = [e for e in sess['events'] if e.get('scene_id') not in his]
        for r in roster:
            sno = int(r['seat'])
            kinds = ('love', 'lean-in') if lead_strength == 'strong' else ('check-out', 'confuse')
            for kind in kinds:
                sess['events'].append({'t': 12.0, 'type': kind, 'seat': sno, 'name': r.get('name'), 'scene_id': first, 'line': 'Him.', 'live': True})
        sess['events'].sort(key=lambda e: float(e.get('t') or 0))
        D.run_diagnostics(sess, twin)
        PI.stop_session(sess)
        lines = {s: 'She held the room every time she was in it, I want more of her running it.' for s in range(1, 11)}
        sess['questions'] = [_watch_more(range(1, 11), range(11, 16), [16], lines=lines)]
        self.assertTrue(RC.reconcile_session(sess, twin)['changed'])
        return twin, sess

    def test_the_lead_everyone_rates_excellent_lands_at_or_above_the_supporting_character_named_by_ten(self):
        twin, sess = self._room('strong')
        ch = sess['diagnostics']['characters']
        by = {r['seat']: r['ratings'] for r in ch['by_seat']}
        self.assertEqual(sum(1 for s in by if by[s][LEAD] == 'a'), 16, by)
        follow = {f['name']: f for f in ch['follow']}
        self.assertGreaterEqual(follow[LEAD]['pct'], follow[SUPPORT]['pct'], follow)
        self.assertGreaterEqual(follow[LEAD]['pct'], 60.0, follow)
        self.assertGreaterEqual(follow[SUPPORT]['pct'], 55.0, follow)
        self.assertLessEqual(follow[SUPPORT]['pct'], 62.0, follow)
        self.assertGreaterEqual(follow[LEAD]['seats'], 15, 'every seat that would come back follows him; the one that wants a hook first follows at half')
        self.assertEqual(follow[SUPPORT]['seats'], 10)
        self.assertEqual(ch['from_room']['draw'], SUPPORT, 'she is still the draw: ten of sixteen named her')
        self.assertGreater(sum(f['pct'] for f in ch['follow']), 100.0)
        self.assertEqual(ch['follow'][0]['name'], LEAD)
        self.assertTrue(ch['read'].split('Asked who')[1].startswith(f" they would follow into a second episode, {follow[LEAD]['pct']}% say {LEAD} and"), ch['read'])
        self.assertEqual(_no_banned(ch), [])

    def test_a_lead_few_rate_excellent_sits_under_the_supporting_character_named_by_ten(self):
        twin, sess = self._room('weak')
        ch = sess['diagnostics']['characters']
        follow = {f['name']: f for f in ch['follow']}
        self.assertLess(follow[LEAD]['pct'], follow[SUPPORT]['pct'], follow)
        self.assertGreaterEqual(follow[SUPPORT]['pct'], 55.0)
        self.assertLess(follow[LEAD]['seats'], 16)

    def test_messy_tenths_and_no_two_people_on_one_tenth(self):
        twin, sess = self._room('strong')
        pcts = [f['pct'] for f in sess['diagnostics']['characters']['follow']]
        self.assertEqual(len(pcts), len(set(pcts)), pcts)
        for v in pcts:
            self.assertFalse(_whole(v), v)
            self.assertEqual(round(v, 1), v)

    def test_the_ledger_files_the_leads_share(self):
        twin, sess = self._room('weak')
        ch = sess['diagnostics']['characters']
        self.assertNotEqual(ch['follow'][0]['name'], LEAD, 'the top of the list is not the lead here')
        m = D.diagnostics_norm_metrics(sess)
        self.assertEqual(m['follow_second_episode'], next(f['pct'] for f in ch['follow'] if f['name'] == LEAD))
        from migration.digital_twin_norms import METRIC_LABEL
        self.assertEqual(METRIC_LABEL['follow_second_episode'], 'Would follow the lead into a second episode')

    def test_the_report_and_the_csv_carry_the_per_person_wording(self):
        import csv
        import io
        from migration import digital_twin_report as REP
        twin, sess = self._room('strong')
        ch = sess['diagnostics']['characters']
        pages = REP.report_pages(twin, sess, with_chart=False)
        page = next(p for p in pages if p.get('head') == ('Character', 'Excellent', 'Norm and screen time'))
        self.assertTrue(page['sub'].startswith(f"Would follow into a second episode: {LEAD} {ch['follow'][0]['pct']}%, "), page['sub'])
        self.assertTrue(page['sub'].endswith('A seat can follow more than one person.'), page['sub'])
        self.assertNotIn('Who they would follow', page['sub'])
        text = PI.session_csv(sess)
        table = list(csv.reader(io.StringIO(text)))
        head_i = next(i for i, r in enumerate(table) if r and r[0] == 'Character')
        self.assertEqual(table[head_i][-1], 'Would follow into a second episode %')
        shares = {r[0]: r[-1] for r in table[head_i + 1:head_i + 4]}
        for f in ch['follow']:
            self.assertEqual(shares[f['name']], str(f['pct']))
        note = next(r for r in table[head_i:] if r and r[0] == 'Would follow into a second episode')
        self.assertEqual(note[1], 'A seat can follow more than one person. The shares do not add to 100.')
        self.assertEqual(_no_banned(pages), [])


def _tenths(v) -> int:
    return int(round(float(v) * 10))


class DistinctTenths(unittest.TestCase):
    """No two characters on a card land on the same tenth of excellent,
    total positive or likability, and none sits on a whole number (the
    60.7 / 60.7 a recompute of the NCIS: New York card would have printed):
    a salted nudge of a tenth or two, deterministic per session and name,
    the order kept, each character's rating rows still summing to 100."""

    NAMES = (LEAD, SECOND, SUPPORT)
    CANNED = (60.7, 16.9, 12.1, 6.2, 4.1)        # one character's rating rows, excellent to poor; no whole numbers, sums to 100

    @classmethod
    def _pub(cls, vals=None):
        return [{'id': i, 'label': lab, 'lean': lean, 'pct': v, 'people': 1_000_001 + k, 'seats_n': 3, 'tally': '3 of 16'} for k, ((i, lab, lean), v) in enumerate(zip(D.RATING, vals or cls.CANNED))]

    def _tied_rows(self, likability=61.0):
        rows = []
        for name in self.NAMES:
            p = self._pub()
            rows.append({'name': name, 'role': 'lead', 'rows': p, 'excellent_pct': D._top_box(p), 'total_positive_pct': D._top_box(p, ('a', 'b')), 'likability': likability})
        return rows

    def _check(self, rows, where=''):
        for key in D.TENTH_KEYS:
            vals = [r[key] for r in rows]
            self.assertEqual(len({_tenths(v) for v in vals}), len(vals), (where, key, vals))
            for v in vals:
                self.assertFalse(_whole(v), (where, key, v))
                self.assertEqual(round(float(v), 1), v, (where, key, v))
        for r in rows:
            by = {x['id']: x['pct'] for x in r['rows']}
            self.assertAlmostEqual(sum(by.values()), 100.0, places=6, msg=(where, r['name']))
            self.assertEqual(r['excellent_pct'], round(by['a'], 1), (where, r['name']))
            self.assertEqual(r['total_positive_pct'], round(by['a'] + by['b'], 1), (where, r['name']))
            self.assertFalse(any(_whole(v) for v in by.values() if v > 0.15), (where, r['name'], by))

    def test_a_cast_whose_scores_tie_lands_on_distinct_tenths_with_order_and_sums_held(self):
        rows = self._tied_rows()
        moved = D.distinct_cast_tenths(rows, 'chars|tie0001|abc')
        self.assertTrue(moved)
        self._check(rows, 'tied')
        # the nudge is a tenth or two per step: a three-way tie fans out in steps of 0.1 to 0.3, so the last row sits within
        # 0.6 of where it started and the top row keeps its value (it was not on a whole number)
        for key, start in (('excellent_pct', 60.7), ('total_positive_pct', 77.6), ('likability', 61.0)):
            vals = sorted((r[key] for r in rows), reverse=True)
            self.assertLessEqual(start - vals[-1], 0.6, (key, vals))
            for hi, lo in zip(vals, vals[1:]):
                self.assertGreaterEqual(round(hi - lo, 1), 0.1, (key, vals))
                self.assertLessEqual(round(hi - lo, 1), 0.3, (key, vals))
        self.assertEqual(max(r['excellent_pct'] for r in rows), 60.7)
        self.assertEqual(max(r['total_positive_pct'] for r in rows), 77.6)
        self.assertNotIn(61.0, [r['likability'] for r in rows], 'a mean on a whole number moves a salted tenth')
        # stable across runs: the same session and names resolve the same way
        again = self._tied_rows()
        D.distinct_cast_tenths(again, 'chars|tie0001|abc')
        self.assertEqual([(r['name'], r['excellent_pct'], r['total_positive_pct'], r['likability']) for r in rows],
                         [(r['name'], r['excellent_pct'], r['total_positive_pct'], r['likability']) for r in again])
        # another session resolves its own way, and still cleanly
        other = self._tied_rows()
        D.distinct_cast_tenths(other, 'chars|tie0002|abc')
        self._check(other, 'other session')

    def test_distinct_values_are_left_alone_and_order_never_swaps(self):
        rows = self._tied_rows()
        for r, vals, like in zip(rows, ((63.4, 14.1, 12.1, 6.3, 4.1), (55.1, 20.4, 14.1, 6.3, 4.1), (41.7, 30.8, 17.1, 6.3, 4.1)), (61.3, 59.8, 55.2)):
            r['rows'] = self._pub(vals)
            r['excellent_pct'], r['total_positive_pct'], r['likability'] = D._top_box(r['rows']), D._top_box(r['rows'], ('a', 'b')), like
        before = copy.deepcopy(rows)
        self.assertEqual(D.distinct_cast_tenths(rows, 'chars|tie0003|abc'), [])
        self.assertEqual(rows, before)
        # a tie on one measure breaks on that measure alone, and the measures that were ordered keep their order
        rows[1]['rows'] = self._pub((58.3, 19.2, 12.1, 6.3, 4.1))       # total positive 77.5
        rows[2]['rows'] = self._pub((58.2, 19.3, 12.1, 6.3, 4.1))       # total positive 77.5, excellent a tenth under
        for r in rows[1:]:
            r['excellent_pct'], r['total_positive_pct'] = D._top_box(r['rows']), D._top_box(r['rows'], ('a', 'b'))
        moved = D.distinct_cast_tenths(rows, 'chars|tie0004|abc')
        self.assertEqual({m[1] for m in moved}, {'total_positive_pct'}, moved)
        self._check(rows, 'near tie')
        self.assertEqual([r['excellent_pct'] for r in rows], [63.4, 58.3, 58.2], 'excellent was ordered and is untouched')
        self.assertEqual([r['likability'] for r in rows], [61.3, 59.8, 55.2])
        for r in rows[1:]:
            self.assertLessEqual(abs(r['total_positive_pct'] - 77.5), 0.3, r)

    def test_the_card_and_the_reconcile_recompute_hold_the_guard(self):
        """Every character publishes the same rating rows and the same
        mean score: the card still prints distinct tenths, through the
        first build and through the recompute a landing question runs."""
        twin, sess = _scored()
        canned = self.CANNED
        orig_publish, orig_score = D._publish_rows, D._seat_char_score

        def publish(probs_by_seat, options, roster, segments, seed):
            if seed.split('|')[-1] in self.NAMES:
                return self._pub(canned)
            return orig_publish(probs_by_seat, options, roster, segments, seed)
        D._publish_rows = publish
        D._seat_char_score = lambda *a, **k: 61.0
        try:
            ch = D.character_table(sess, twin)
            self._check(ch['characters'], 'card')
            self.assertEqual([c['name'] for c in ch['characters']], [c['name'] for c in sorted(ch['characters'], key=lambda c: (-c['excellent_pct'], -c['total_positive_pct'], -c['likability'], c['name']))])
            for c in ch['characters']:
                if c.get('norm') is not None:
                    self.assertEqual(c['vs_norm_pp'], round(c['excellent_pct'] - float(c['norm']), 1), 'the norm gap reads off the settled excellent')
            again = D.character_table(copy.deepcopy(sess), twin)
            self.assertEqual([(c['name'], c['excellent_pct'], c['total_positive_pct'], c['likability']) for c in ch['characters']],
                             [(c['name'], c['excellent_pct'], c['total_positive_pct'], c['likability']) for c in again['characters']])
            sess['questions'] = [_watch_more(range(1, 11), range(11, 16), [16])]
            out = RC.reconcile_session(sess, twin)
            self.assertTrue(out['changed'])
            self._check(sess['diagnostics']['characters']['characters'], 'reconcile')
            self.assertEqual(_no_banned(sess['diagnostics']['characters']), [])
        finally:
            D._publish_rows, D._seat_char_score = orig_publish, orig_score


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
