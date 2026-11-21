#!/usr/bin/env python3
"""Archetype representatives are one person per archetype on a twin, and they remember the
focus groups they sat in on it (Jenna 2026-11-20, choosing "Build it: they remember past
focus groups on the same twin"; her methodology: they remember "in past groups you've used
them in just like a real person would").

- Every room seated by archetype seats the twin's own representatives: the same person
  (name, age, city, person_id) room after room, the people the twin page shows.
- A room on a cut of the audience, or one asked for more seats than archetypes, gets
  stable identities too (per twin, archetype and cut).
- A returning representative's seat carries a compact memory of what the same person said
  and chose in earlier rooms, Predictive Analytics rooms and pilot tests on the twin,
  newest first and capped; only the groups the asker's company can see count; the seat
  card says so in one plain line.
- Quota seating is unchanged. Hermetic: no model, no network.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import _index_page  # noqa: E402  (the page with its split files inlined back, 2026-11-20)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_archetypes as A  # noqa: E402
from migration import digital_twin_company as CO  # noqa: E402
from migration import digital_twin_panel as P  # noqa: E402
from migration import digital_twin_portraits as PT  # noqa: E402
from migration import digital_twin_room as RM  # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_pilot import _beals  # noqa: E402

USERS = {'users': {
    'a1': {'email': 'a1@acme.example', 'role': 'user', 'company': 'Acme', 'has_digital_twins_iq_access': True},
    'a2': {'email': 'a2@acme.example', 'role': 'user', 'company': 'Acme', 'has_digital_twins_iq_access': True},
    'b1': {'email': 'b1@globex.example', 'role': 'user', 'company': 'Globex', 'has_digital_twins_iq_access': True},
    'jenna': {'email': 'jenna@crosswalknyc.com', 'role': 'super_admin'},
}}


def _twin():
    tw = _beals()
    tw['meta']['size_us'] = 4_237_511
    tw['meta']['created_by'] = 'a1'
    A.ensure_archetypes(tw)
    return tw


def _ident(r):
    return (r.get('person_id'), r.get('name'), int(r.get('age')), r.get('city'), r.get('gender'))


def _room(twin, sid, created_by, questions, person_ids, title='A room'):
    roster = [{'seat': i + 1, 'name': f'Seat {i + 1}', 'person_id': pid, 'twin': 'jb'} for i, pid in enumerate(person_ids)]
    qs = []
    for k, (q, label, line) in enumerate(questions):
        qs.append({'id': f'q{k + 1}', 'q': q, 'asked_at': f'2026-11-{10 + k:02d}T10:00:00Z', 'stage': 'done',
                   'answers': [{'seat': 1, 'option': 'o1', 'option_label': label, 'line': line, 'reason': f'because {k}'}]})
    return {'id': sid, 'title': title, 'created_by': created_by, 'created_at': f'2026-11-{10:02d}T09:00:00Z', 'updated_at': f'2026-11-{10 + len(questions):02d}T12:00:00Z',
            'twin_keys': ['jb'], 'group': {'roster': roster, 'segments': []}, 'questions': qs}


class OnePersonPerArchetype(unittest.TestCase):
    def test_rooms_seat_the_twin_pages_own_people(self):
        tw = _twin()
        reps = {g['representative']['person_id']: g['representative'] for g in tw['archetypes']['groups']}
        self.assertEqual(len(reps), len(tw['archetypes']['groups']))
        g1 = P.build_group([tw], seed='room-one')
        g2 = P.build_group([tw], seed='room-two')
        self.assertEqual(g1['seating'], 'kinds')
        self.assertEqual([_ident(r) for r in g1['roster']], [_ident(r) for r in g2['roster']], 'the same people room after room')
        for r in g1['roster']:
            rep = reps[r['person_id']]
            self.assertEqual((rep['name'], int(rep['age']), rep['city'], rep['gender']), (r['name'], int(r['age']), r['city'], r['gender']), r['person_id'])
        self.assertEqual({r['person_id'] for r in g1['roster']}, set(reps), 'every archetype, by its own person')
        # the seating invariants hold: the people sum to the segment, never under twelve
        self.assertEqual(sum(int(r['people']) for r in g1['roster']), int(g1['segments'][0]['people']))
        self.assertGreaterEqual(len(g1['roster']), A.MIN_K)

    def test_a_cut_and_a_bigger_room_get_stable_people_of_their_own(self):
        tw = _twin()
        women = [{'label': 'Women', 'filter': {'gender': 'F'}}]
        c1 = P.build_group([tw], segments=women, seed='cut-one')
        c2 = P.build_group([tw], segments=women, seed='cut-two')
        self.assertEqual([_ident(r) for r in c1['roster']], [_ident(r) for r in c2['roster']], 'the same conditioned people every time')
        self.assertTrue(all(r['gender'] == 'F' for r in c1['roster']))
        self.assertTrue(all('@' in r['person_id'] for r in c1['roster']), 'a cut carries its own signature')
        whole = {r['person_id'] for r in P.build_group([tw], seed='w')['roster']}
        self.assertFalse(whole & {r['person_id'] for r in c1['roster']}, 'the cut\'s people are not the whole audience\'s')
        self.assertEqual(sum(int(r['people']) for r in c1['roster']), int(c1['segments'][0]['people']))
        self.assertGreaterEqual(len(c1['roster']), A.MIN_K)
        # a room asked for more seats than archetypes splits the largest; the extra seats are stable too
        b1 = P.build_group([tw], in_room=20, seed='big-one')
        b2 = P.build_group([tw], in_room=20, seed='big-two')
        self.assertEqual(len(b1['roster']), 20)
        self.assertEqual([_ident(r) for r in b1['roster']], [_ident(r) for r in b2['roster']])
        self.assertTrue(any('~' in r['person_id'] for r in b1['roster']), 'a split part carries its side in its id')

    def test_quota_seating_is_unchanged(self):
        tw = _twin()
        q = P.build_group([tw], seed='q', seating='quota')
        self.assertEqual(q['seating'], 'quota')
        self.assertEqual(len(q['roster']), P.DEFAULT_IN_ROOM)
        self.assertTrue(all('person_id' not in r for r in q['roster']))

    def test_a_representatives_face_follows_the_person(self):
        rep = {'seat': 3, 'name': 'Kenji', 'age': 57, 'gender': 'M', 'city': 'Orlando', 'segment': 'Women 25 to 44', 'person_id': 'habit-regular-older'}
        twin_page = {'name': 'Kenji', 'age': 57, 'gender': 'M', 'city': 'Orlando', 'segment': ''}
        self.assertEqual(PT.portrait_hash(rep), PT.portrait_hash(twin_page), 'the same face on the twin page and in any room')
        quota = dict(rep); quota.pop('person_id')
        self.assertNotEqual(PT.portrait_hash(quota), PT.portrait_hash(twin_page), 'a quota seat keys its face as before')
        # a room on the whole audience carries the twin page's face onto the seat at seating time
        tw = _twin()
        g0 = tw['archetypes']['groups'][0]
        g0['representative']['portrait_url'] = '/api/digital-twins/portrait/abc'
        g0['representative']['portrait_hash'] = 'abc'
        room = P.build_group([tw], seed='faces')
        seat = next(r for r in room['roster'] if r['person_id'] == g0['id'])
        self.assertEqual(seat.get('portrait_url'), '/api/digital-twins/portrait/abc')


class Memory(unittest.TestCase):
    def setUp(self):
        CO.set_users_loader(lambda: USERS)

    def tearDown(self):
        CO.set_users_loader(None)

    def _payload(self):
        tw = _twin()
        qs1 = [('Would you keep watching?', 'Yes', 'I would. The bar scene had me.'), ('What would you pay?', '$8.99', 'Nine bucks, not a penny more.')]
        qs2 = [(f'Question {i}', 'Yes', f'Line {i} from the second room.') for i in range(10)]
        tw['panel_sessions'] = [_room(tw, 'r1', 'a1', qs1, ['habit-regular-older', 'habit-regular-mid'], title='Pricing room'),
                                _room(tw, 'r2', 'a1', qs2, ['habit-regular-older'], title='Season two room'),
                                _room(tw, 'r3', 'b1', [('A Globex question', 'No', 'Not for me.')], ['habit-regular-older'], title='Globex room')]
        tw['panel_sessions'][1]['guided'] = {'status': 'done'}
        tw['pilot_sessions'] = [{'id': 'p1', 'title': 'Season 2 pilot', 'mode': 'pilot', 'created_by': 'a2', 'created_at': '2026-11-01T10:00:00Z', 'updated_at': '2026-11-02T10:00:00Z',
                                 'media': {'title': 'Season 2 pilot'}, 'group': {'roster': [{'seat': 1, 'name': 'Kenji', 'person_id': 'habit-regular-older'}], 'segments': []}, 'questions': [],
                                 'events': [{'seat': 1, 't': 40.0, 'type': 'lean-in', 'line': 'The cold open pulled me right in.', 'intensity': 0.8}, {'seat': 1, 't': 90.0, 'type': 'check-out', 'line': 'Lost me at the picket fence.', 'intensity': 0.4}]}]
        return tw

    def test_memory_is_carried_newest_first_and_capped(self):
        tw = self._payload()
        got = RM.earlier_groups_for(tw, 'habit-regular-older', viewer='a1', exclude_sid='new')
        self.assertEqual(got['groups'], 3, 'two Acme rooms and the Acme pilot; never the Globex room')
        self.assertEqual(len(got['entries']), RM.EARLIER_GROUPS_LIMIT)
        self.assertTrue(all('Globex' not in (e.get('group') or '') for e in got['entries']))
        self.assertEqual(got['entries'][0]['group'], 'Season two room (Predictive Analytics)')
        self.assertEqual(got['entries'][0]['question'], 'Question 9', 'newest first')
        for e in got['entries']:
            self.assertIn('when', e)
            self.assertTrue(e.get('said'))
        # the pilot they watched: one entry with a reaction they had, the strongest one
        later = RM.earlier_groups_for(tw, 'habit-regular-older', viewer='a1', exclude_sid='r2', limit=20)
        pilot = [e for e in later['entries'] if e.get('watched')]
        self.assertEqual(len(pilot), 1)
        self.assertEqual(pilot[0]['said'], 'The cold open pulled me right in.')
        self.assertEqual(pilot[0]['group'], 'Season 2 pilot (Pilot test)')
        # the room being asked in is never its own past
        own = RM.earlier_groups_for(tw, 'habit-regular-older', viewer='a1', exclude_sid='r1', limit=20)
        self.assertFalse(any(e.get('group', '').startswith('Pricing room') for e in own['entries']))
        # a person who sat nowhere before has nothing
        self.assertEqual(RM.earlier_groups_for(tw, 'returning-mid', viewer='a1'), {'groups': 0, 'entries': []})

    def test_memory_never_crosses_companies(self):
        tw = self._payload()
        globex = RM.earlier_groups_for(tw, 'habit-regular-older', viewer='b1')
        self.assertEqual(globex['groups'], 1)
        self.assertEqual(globex['entries'][0]['group'], 'Globex room (Focus group)')
        staff = RM.earlier_groups_for(tw, 'habit-regular-older', viewer='jenna', limit=40)
        self.assertEqual(staff['groups'], 4, 'staff see every group on the twin')
        nobody = RM.earlier_groups_for(tw, 'habit-regular-older', viewer='')
        self.assertEqual(nobody, {'groups': 0, 'entries': []})

    def test_the_seat_hands_its_past_to_the_model(self):
        tw = self._payload()
        past = RM.earlier_groups_for(tw, 'habit-regular-older', viewer='a1', exclude_sid='new')
        seat = next(r for r in P.build_group([tw], seed='x')['roster'] if r['person_id'] == 'habit-regular-older')
        card = RM.persona_card(tw, seat, 'room|new|v')
        seen = {}

        def fake(role, system, user, **kw):
            seen['system'] = system
            seen['user'] = user
            return {'option': 'o1', 'line': 'Like I said last time, nine bucks is my line.', 'confidence': 4, 'reason': 'same as before'}
        opts = [{'id': 'o1', 'label': 'Yes', 'lean': 'yes'}, {'id': 'o2', 'label': 'No', 'lean': 'no'}]
        ans = RM.seat_answer(RM.Models(model_fn=fake), card, 'Would you pay $9.99?', '', opts, {}, [], 'seed', past=past)
        body = json.loads(seen['user'])
        self.assertIn('earlier_focus_groups_you_sat_in_on_this_subject', body)
        self.assertEqual(body['earlier_focus_groups_you_sat_in_on_this_subject']['how_many'], 3)
        self.assertEqual(body['earlier_focus_groups_you_sat_in_on_this_subject']['what_you_said_and_chose_newest_first'][0]['question'], 'Question 9')
        self.assertIn('earlier_focus_groups_you_sat_in_on_this_subject', seen['system'])
        self.assertIn('earlier_focus_groups_you_sat_in_on_this_subject', RM.MIRROR_SYSTEM)
        self.assertEqual(ans['line'], 'Like I said last time, nine bucks is my line.')
        # a seat with no past hands nothing extra
        body2 = RM._seat_body(card, 'q', '', opts, {}, [], None, {'groups': 0, 'entries': []})
        self.assertNotIn('earlier_focus_groups_you_sat_in_on_this_subject', body2)

    def test_the_seat_card_line(self):
        tw = self._payload()
        self.assertEqual(RM.earlier_groups_line(3), 'Sat in 3 earlier focus groups on this twin')
        self.assertEqual(RM.earlier_groups_line(1), 'Sat in 1 earlier focus group on this twin')
        self.assertEqual(RM.earlier_groups_line(0), '')
        ann = RM.annotate_earlier_groups(tw, 'a1')
        self.assertEqual(ann['r1']['1']['n'], 2, 'from the pricing room, the season two room and the pilot are its past')
        self.assertEqual(ann['r1']['1']['line'], 'Sat in 2 earlier focus groups on this twin')
        self.assertNotIn('2', ann.get('r1', {}), 'a person with no past has no line')
        self.assertEqual(RM.annotate_earlier_groups(tw, 'b1').get('r3', {}).get('1', {}).get('n'), None, 'the Globex viewer sees no Acme past')
        page = _index_page.text(ROOT)   # the page with its split files inlined back
        self.assertIn('function earlierGroupsLine(sid, seat)', page)
        self.assertIn("earlierGroupsLine(s.id, r.seat) ? '<div class=\"stands earlier\">'", page)
        self.assertIn("earlierGroupsLine((S.pilot || {}).id, r.seat)", page)
        # the reader never sees how it works
        for tok in ('person_id', 'archetype', 'memory'):
            self.assertNotIn(tok, RM.earlier_groups_line(3))


class RuleCopy(unittest.TestCase):
    def test_prompt_copy_is_plain(self):
        for s in (RM.PERSONA_SYSTEM, RM.MIRROR_SYSTEM):
            self.assertNotIn('\u2014', s)
            self.assertNotIn('kind', s.split('earlier_focus_groups_you_sat_in_on_this_subject')[-1][:400].lower().replace('kinds of', ''))


if __name__ == '__main__':
    unittest.main(verbosity=1)
