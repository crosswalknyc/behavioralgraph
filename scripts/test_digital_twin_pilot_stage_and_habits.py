#!/usr/bin/env python3
"""Two Pilot Testing fixes Jenna chose on 2026-11-20 ("Yes, fix both"):

1. A seat's stage label (discovering, trying, in the habit, lapsing, returning) and the
   stage its bio describes are one fact: the label pinned on the seat is what the bio is
   written from and what the dial's habit pull, the avid / casual split and the stage
   lines read. A stored test whose labels drifted from the bios is relabeled in place,
   its stage and fanship lines re-counted from the stored seat dials (people-weighted),
   stamped, never re-run.
2. In rooms seated by archetype the habit pull on the dial uses each archetype's own
   habits, not the whole twin's; a quota room is unchanged.
Hermetic: no model, no network.
"""
from __future__ import annotations

import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_archetypes as A  # noqa: E402
from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twin_room as RM  # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_pilot import _beals  # noqa: E402


def _quota(sess):
    for r in sess['group']['roster']:
        r.pop('archetype', None)
        r.pop('people', None)
        r.pop('stage', None)
        r['segment'] = 'All'
    sess['group']['segments'] = [{'label': 'All', 'people': 2_000_000}]
    return sess


class Stage(unittest.TestCase):
    def test_label_and_bio_agree_on_a_prepared_test(self):
        twin = _beals()
        sess = _quota(PI.open_session([twin], title='Stage test', in_room=10))
        sess['id'] = 'st1'
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=120, title='Stage test')
        for r in sess['group']['roster']:
            self.assertTrue(r.get('persona'))
            self.assertIn(r['lifecycle_stage'], PI.STAGE_LABELS)
            self.assertEqual(r['stage'], r['lifecycle_stage'], 'the label is pinned on the seat')
            self.assertEqual(PI.bio_stage(twin, r), r['lifecycle_stage'], f"seat {r['seat']}: the bio describes the label's stage")

    def test_a_bio_written_under_another_seed_still_agrees(self):
        # the live path writes the bios ahead of play under its own seed; the scoring that follows used another, and the
        # labels drifted from the bios (11 of 16 seats on the NCIS: New York pilot). The pinned stage closes that.
        twin = _beals()
        sess = _quota(PI.open_session([twin], title='Live then scored', in_room=10))
        sess['id'] = 'st2'
        RM.ensure_personas(sess, {twin['meta']['key']: twin}, session_seed=f"pilot|{sess['id']}|live")
        bios = {r['seat']: r['persona'] for r in sess['group']['roster']}
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=120, title='Live then scored')
        for r in sess['group']['roster']:
            self.assertEqual(r['persona'], bios[r['seat']], 'the bio is written once')
            self.assertEqual(PI.bio_stage(twin, r), r['lifecycle_stage'], f"seat {r['seat']}")
        # a card drawn later under any seed reads the pinned stage
        for r in sess['group']['roster']:
            self.assertEqual(RM.persona_card(twin, r, 'some|other|seed')['lifecycle_stage'], r['lifecycle_stage'])

    def test_bio_stage_reads_the_bio(self):
        twin = _beals()
        seat = {'seat': 1, 'name': 'Andre', 'age': 45, 'gender': 'M', 'city': 'Houston',
                'persona': 'Andre, 45, lives in Houston. He has had it for years and keeps it through the year; he is firmly in the habit.'}
        self.assertEqual(PI.bio_stage(twin, seat), 'in the habit')
        seat['persona'] = 'Andre, 45, lives in Houston. He is drifting away from it lately and picks it up now and then.'
        self.assertEqual(PI.bio_stage(twin, seat), 'lapsing')
        self.assertEqual(PI.bio_stage(twin, {'seat': 2, 'name': 'Nia', 'age': 70, 'gender': 'F', 'city': 'Dallas'}), '')

    def test_stored_test_is_relabeled_and_its_group_lines_recounted(self):
        twin = _beals()
        sess = _quota(PI.open_session([twin], title='Drifted', in_room=10))
        sess['id'] = 'st3'
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=120, title='Drifted')
        PI.stop_session(sess)
        good = copy.deepcopy(sess)
        # drift three labels away from their bios, the way the live path once did
        roster = sess['group']['roster']
        drifted = []
        for r in roster[:3]:
            other = next(st for st in PI.STAGE_LABELS if st != r['lifecycle_stage'])
            drifted.append((int(r['seat']), r['lifecycle_stage'], other))
            r['lifecycle_stage'] = other
            r.pop('stage', None)
        before_line = list(sess['dial']['value'])
        before_card = copy.deepcopy(sess['scorecard'])
        got = PI.relabel_stages(sess, twin)
        self.assertIsNotNone(got)
        self.assertEqual(sorted(s for s, *_ in got['seats']), sorted(s for s, *_ in drifted))
        for seat_no, was_label, drifted_to in drifted:
            r = next(x for x in roster if int(x['seat']) == seat_no)
            self.assertEqual(r['lifecycle_stage'], was_label, 'the label is back on the stage the bio describes')
            self.assertEqual(r['stage'], was_label)
        self.assertTrue(isinstance(sess.get('stage_relabel'), dict) and sess['stage_relabel']['at'])
        self.assertEqual(len(sess['stage_relabel']['seats']), 3)
        # the stage and fanship lines are the ones a fresh people-weighted count gives on the stored seat dials
        sw = PI.seat_weights(roster, sess['group']['segments'])
        fresh = PI.all_splits(roster, sess['seat_dials'], twin, 'Jennifer Beals', sess['media']['hash'], weights=sw)
        for key, row in sess['dial']['splits'].items():
            if row.get('group') in ('lifecycle', 'fanship'):
                self.assertIn(key, fresh, key)
                self.assertEqual(row['value'], fresh[key]['value'], key)
        for key, row in fresh.items():
            if row.get('group') in ('lifecycle', 'fanship'):
                self.assertIn(key, sess['dial']['splits'])
        # the room line, the seat dials and the card are untouched: nothing was re-run
        self.assertEqual(sess['dial']['value'], before_line)
        self.assertEqual(sess['seat_dials'], good['seat_dials'])
        self.assertEqual(sess['scorecard'], before_card)
        # idempotent; a test whose labels agree is left alone
        self.assertIsNone(PI.relabel_stages(sess, twin))
        self.assertIsNone(PI.relabel_stages(good, twin))
        self.assertNotIn('stage_relabel', good)


class Habits(unittest.TestCase):
    def test_trait_for_reads_the_archetypes_own_habits(self):
        twin = _beals()   # franchise_loyalty 71.4
        quota = {'seat': 1, 'name': 'A'}
        self.assertAlmostEqual(PI._trait_for(twin, quota, 'franchise_loyalty'), 71.4)
        by_kind = {'seat': 2, 'name': 'B', 'archetype': {'id': 'k', 'traits': {'franchise_loyalty': 22.0}}}
        self.assertAlmostEqual(PI._trait_for(twin, by_kind, 'franchise_loyalty'), 22.0)
        self.assertAlmostEqual(PI._trait_for(twin, by_kind, 'second_screen'), 38.2, msg='a dial the archetype does not carry is the twin\'s')
        card = RM.persona_card(twin, by_kind, 'seed')
        self.assertAlmostEqual(card['trait_values']['franchise_loyalty'], 22.0)
        self.assertAlmostEqual(PI._trait_for(twin, card, 'franchise_loyalty'), 22.0)

    def test_the_habit_pull_differs_by_archetype_and_quota_is_unchanged(self):
        twin = _beals()
        beats = PI.build_beats('Habit test', 120.0, 'mk', notes='a drama')
        base = {'seat': 1, 'name': 'Harold', 'age': 54, 'gender': 'M', 'city': 'New York', 'segment': 'All'}
        devoted = dict(base, archetype={'id': 'd', 'label': 'Devoted regulars', 'traits': {'franchise_loyalty': 88.0, 'second_screen': 30.0}})
        drifting = dict(base, archetype={'id': 'l', 'label': 'Drifting viewers', 'traits': {'franchise_loyalty': 18.0, 'second_screen': 70.0}})
        d1 = PI.seat_dial(twin, devoted, beats, 'pilot|x', 'mk')
        d2 = PI.seat_dial(twin, drifting, beats, 'pilot|x', 'mk')
        self.assertNotEqual(d1, d2)
        self.assertGreater(sum(d1) / len(d1), sum(d2) / len(d2) + 3.0, 'the devoted archetype sits higher on its own habits')
        # a quota seat reads the twin's habits, exactly as it did
        q1 = PI.seat_dial(twin, base, beats, 'pilot|x', 'mk')
        twin_only = dict(twin)
        q2 = PI.seat_dial(twin_only, dict(base), beats, 'pilot|x', 'mk')
        self.assertEqual(q1, q2)
        # the avid / casual read uses the archetype's loyalty when the stage is not pinned
        self.assertTrue(PI._is_avid(dict(devoted), twin))
        self.assertFalse(PI._is_avid(dict(drifting), twin))

    def test_a_room_by_archetype_is_prepared_on_its_own_habits(self):
        twin = _beals()
        twin['meta']['size_us'] = 4_237_511
        A.ensure_archetypes(twin)
        sess = PI.open_session([twin], title='By archetype', in_room=0)
        sess['id'] = 'hb1'
        self.assertEqual(sess['group'].get('seating'), 'kinds')
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=120, title='By archetype')
        roster = sess['group']['roster']
        self.assertTrue(all(r.get('person_id') for r in roster), 'the twin\'s own representatives sat in the test')
        # the prepared seat dials are the ones each archetype's own habits give
        for r in roster[:4]:
            own = PI.seat_dial(twin, r, sess['beats'], f"pilot|{sess['id']}|{sess['media']['hash']}|{PI.VERSION}", sess['watch'] and f"{sess['media']['hash']}|{sess['watch'].get('media_key', '')}")
            self.assertEqual(len(own), len(sess['seat_dials'][str(r['seat'])]))
        for r in roster:
            self.assertEqual(PI.bio_stage(twin, r), r['lifecycle_stage'], f"seat {r['seat']}: label and bio agree in a room by archetype too")


if __name__ == '__main__':
    unittest.main(verbosity=1)
