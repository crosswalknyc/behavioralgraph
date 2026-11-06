#!/usr/bin/env python3
"""The seats mirror the count, the headline people come from the share.

Jenna 2026-11-05, on the P-Valley room asked about S.W.A.T. Exiles:
"check my question on pvalley viewers on starz why would it say 0
people would watch?" The count said about one in twenty watch most
Fridays (5.4%, 329K people) and all twelve seats landed on skip, so
the row read '0 of 12' beside the people and the read said 'all twelve
seats' and 'nobody watching it on Fridays' next to 'about one in
twenty'.

Held here (room-4):
  * allocate_seats: largest remainder over the count, and any side worth
    half a seat of the room (4.2% with twelve) holds a seat. 88 / 7 / 5
    with twelve seats is 10 / 1 / 1 (quotas 10.56 / 0.84 / 0.60: ten
    whole seats, the two left over go to the .84 and the .60); the 5%
    side holds a seat; 100 / 0 / 0 keeps all twelve on one side.
  * the headline people equal share x the people the room stands for
    (the count's own people, messy), never the seat tally; '0 people'
    only when the count is zero.
  * a read with 'nobody' / 'all twelve' against a non-zero count is
    rewritten; a zero count may say none.
  * end to end: twelve seats all on skip with a count of 88 / 7 / 5 land
    as 10 / 1 / 1, the moved seats argue their stance in character, and
    the record's tally, headline and read agree.

Hermetic: a fake model answers every desk; no S3.

    python3 scripts/test_digital_twin_room_seats_mirror_count.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_panel as P    # noqa: E402
from migration import digital_twin_room as ROOM  # noqa: E402

LABELS = {'a': 'Still have Starz, but I skip it, not my kind of show', 'b': 'Tried an episode or two, not keeping up', 'c': "Yes, I've been watching it most Fridays"}


def _pvalley():
    return {
        'meta': {'name': 'P-Valley viewers', 'key': 'pv', 'subject': 'P-Valley viewers', 'subject_kind': 'audience', 'format': 'series', 'size_us': 6_094_342,
                 'profile_key': 'PValley_on_Starz_Viewers_11_05_2026_21_49.csv'},
        'identity': {'base': {'kind': 'viewers'}, 'read': 'People who watched P-Valley on Starz in the last year.',
                     'demographics': {
                         'gender': [{'label': 'Female', 'pct': 68.3}, {'label': 'Male', 'pct': 31.7}],
                         'age': [{'label': '18-24', 'pct': 12.1}, {'label': '25-34', 'pct': 27.4}, {'label': '35-44', 'pct': 24.2}, {'label': '45-54', 'pct': 17.1},
                                 {'label': '55-64', 'pct': 11.6}, {'label': '65+', 'pct': 7.6}],
                         'education': [{'label': 'Bachelors Degree', 'pct': 28.3}, {'label': 'Some College', 'pct': 34.2}, {'label': 'High School or Less', 'pct': 25.1},
                                       {'label': 'Graduate or Professional Degree', 'pct': 12.4}],
                         'occupation': [{'label': 'Management/Business/Professional', 'pct': 28.1}, {'label': 'Service', 'pct': 24.6}, {'label': 'Sales', 'pct': 12.1},
                                        {'label': 'Healthcare', 'pct': 15.2}, {'label': 'Education/Library', 'pct': 10.4}],
                     }},
        'traits': {'franchise_loyalty': {'value': 62.0, 'label': 'loyalty', 'read': ''}, 'second_screen': {'value': 55.0, 'label': 'second', 'read': ''},
                   'binge_vs_appointment': {'value': 48.0, 'label': 'binge', 'read': ''}, 'word_of_mouth': {'value': 58.0, 'label': 'wom', 'read': ''},
                   'trial_speed': {'value': 44.0, 'label': 'trial', 'read': ''}},
        'lifecycle': {'habit_pct': 55.0, 'discovering_pct': 10.0, 'trying_pct': 10.0, 'lapsing_pct': 15.0, 'returning_pct': 10.0},
    }


# the count: the three answers as a published split, so the anchor sets the audience-level answer at 88 / 7 / 5
MEDIA = {
    'event': {'what': 'S.W.A.T. Exiles weekly run on Starz', 'date': '2026-09-25', 'status': 'happened',
              'outcome': 'The spinoff is mid-run on Friday nights; the count of P-Valley viewers on it stands at 5% watching most Fridays.',
              'results': [{'label': 'Skip it', 'share_pct': 88.0, 'status': 'final'}, {'label': 'Tried an episode or two', 'share_pct': 7.0, 'status': 'final'},
                          {'label': 'Watching most Fridays', 'share_pct': 5.0, 'status': 'final'}],
              'turnout_pct': None},
    'polls': [],
    'coverage': [{'line': 'The spinoff drew a soft critical reception at launch.', 'reach': 'common', 'local': False, 'days_ago': 9}],
    'talk': [{'line': 'I came for Uncle Clifford and the Pynk, not a cop show', 'who': 'longtime P-Valley fans', 'loudness': 'dominant', 'feeling': 'dismissive'},
             {'line': 'Holding Starz for December 4', 'who': 'most of the audience', 'loudness': 'common', 'feeling': 'patient'}],
    'numbers': [],
    'local': [],
    'not_found': [],
}

FACTS = {'subject_status': {'line': 'S.W.A.T. Exiles is a ten-episode Starz spinoff, mid-run on Friday nights; P-Valley returns December 4.'},
         'facts': [{'fact': 'Episode 7 airs November 6; the finale is November 27.', 'source_class': 'trade', 'topic': 'subject'}],
         'not_found': [], 'implications': ['A paid standby month for most of this audience.']}

SEEN = {'persona': [], 'mirror': [], 'read': []}


def fake_model(role, system, user, **kw):
    d = json.loads(user)
    if role == 'research':
        return dict(FACTS)
    if role == 'media':
        return json.loads(json.dumps(MEDIA))
    if role == 'moderator' and 'supplied_options' in d:
        return {'options': [{'id': 'a', 'label': LABELS['a'], 'lean': 'no'}, {'id': 'b', 'label': LABELS['b'], 'lean': 'maybe'}, {'id': 'c', 'label': LABELS['c'], 'lean': 'yes'}],
                'framing': 'S.W.A.T. Exiles runs Friday nights on Starz. Which of these is you right now?', 'listen_for': ['genre', 'December 4']}
    if role == 'moderator':
        SEEN['read'].append(d)
        # the draft the live room wrote: the lead share, then 'all twelve seats' and 'nobody' against a non-zero count
        return {'read': "Almost nine in ten of these P-Valley viewers say they still have Starz and skip S.W.A.T. Exiles, and all twelve seats in the room landed there, with nobody watching it on Fridays. "
                        "The loudest lines were about genre, not quality. People said they came for Uncle Clifford and the Pynk.",
                'chat': 'Almost nine in ten say they still have Starz and skip it, and that was all twelve seats in the room. Nobody is watching it on Fridays.',
                'voices': [1, 2, 3], 'probes_next': ['Why?'], 'facts_used': ['Episode 7 airs November 6; the finale is November 27.']}
    if role == 'persona' and 'your_answer' in d:
        SEEN['mirror'].append(d)
        return {'line': f"Honestly that is me: {d['your_answer']['label']}. My cousin got me into it and now it is my Friday.", 'confidence': 4, 'reason': 'the cousin'}
    if role == 'persona':
        SEEN['persona'].append(d)
        return {'option': 'a', 'line': 'I still have Starz for December 4, but a cop procedural is not what I sit down for after work.', 'confidence': 5, 'reason': 'genre'}
    return None


class Allocation(unittest.TestCase):
    def test_largest_remainder_with_the_half_seat_floor(self):
        # quotas 10.56 / 0.84 / 0.60: ten whole seats, the two left over to the largest remainders (.84, .60)
        self.assertEqual(ROOM.allocate_seats({'a': 88, 'b': 7, 'c': 5}, 12), {'a': 10, 'b': 1, 'c': 1})
        self.assertEqual(ROOM.allocate_seats({'a': 92.1, 'b': 1.1, 'c': 5.4, 'd': 1.2, 'e': 0.2}, 12), {'a': 11, 'b': 0, 'c': 1, 'd': 0, 'e': 0},
                         'the live P-Valley count: the one-in-twenty side holds a seat')

    def test_the_floor_seats_a_side_the_remainder_would_miss(self):
        # 4.3% of twelve seats is 0.52 of a seat: no whole seat, and the remainders can run out before it; the floor seats it anyway
        got = ROOM.allocate_seats({'a': 47.0, 'b': 24.4, 'c': 24.3, 'd': 4.3}, 12)
        self.assertEqual(sum(got.values()), 12)
        self.assertGreaterEqual(got['d'], 1, 'half a seat of share holds a seat')
        self.assertEqual(ROOM.allocate_seats({'a': 96.0, 'b': 4.0}, 12)['b'], 0, 'under half a seat (4.0% of twelve is 0.48) stays with the share alone')
        self.assertEqual(ROOM.allocate_seats({'a': 95.5, 'b': 4.5}, 12)['b'], 1)

    def test_a_zero_count_keeps_every_seat_on_one_side(self):
        self.assertEqual(ROOM.allocate_seats({'a': 100, 'b': 0, 'c': 0}, 12), {'a': 12, 'b': 0, 'c': 0})
        self.assertEqual(sum(ROOM.allocate_seats({'a': 33.3, 'b': 33.3, 'c': 33.4}, 12).values()), 12)
        self.assertEqual(ROOM.allocate_seats({}, 12), {})

    def test_mirror_moves_the_fitting_seats_and_keeps_the_count(self):
        opts = [{'id': 'a', 'label': 'Skip it', 'lean': 'no', 'pct': 88.0, 'people': 880_001, 'seats': list(range(1, 13))},
                {'id': 'b', 'label': 'Tried an episode or two', 'lean': 'maybe', 'pct': 7.0, 'people': 70_003, 'seats': []},
                {'id': 'c', 'label': 'Watching most Fridays', 'lean': 'yes', 'pct': 5.0, 'people': 49_996, 'seats': []}]
        roster = [{'seat': i, 'segment': 'S', 'name': f'n{i}'} for i in range(1, 13)]
        segs = [{'label': 'S', 'people': 1_000_000, 'seats': 12}]
        bys = [{'segment': 'S', 'people': 1_000_000, 'distribution': [{'label': o['label'], 'pct': o['pct'], 'people': o['people']} for o in opts]}]
        ans = [{'seat': i, 'name': f'n{i}', 'option': 'a', 'option_label': 'Skip it', 'line': ('Not for me, I watch other things on Fridays.' if i == 5 else 'Not for me.'),
                'confidence': (5 if i < 9 else 3), 'reason': ''} for i in range(1, 13)]
        plan = ROOM.mirror_seats(opts, bys, ans, roster, segs)
        ROOM.retally(opts, bys, ans, roster)
        self.assertEqual(plan['alloc'], {'a': 10, 'b': 1, 'c': 1})
        self.assertEqual([(o['id'], o['seats_n']) for o in opts], [('a', 10), ('b', 1), ('c', 1)])
        self.assertEqual({m['to'] for m in plan['moved']}, {'b', 'c'})
        self.assertEqual(next(m['seat'] for m in plan['moved'] if m['to'] == 'c'), 5, 'the seat whose own words touched Fridays is the one that watches')
        self.assertEqual([o['pct'] for o in opts], [88.0, 7.0, 5.0], 'the count is never touched')
        self.assertEqual([o['people'] for o in opts], [880_001, 70_003, 49_996])
        self.assertEqual(bys[0]['tally'], [{'label': 'Skip it', 'seats': 10}, {'label': 'Tried an episode or two', 'seats': 1}, {'label': 'Watching most Fridays', 'seats': 1}])
        self.assertTrue(all(a.get('mirrored') for a in ans if a['seat'] in {m['seat'] for m in plan['moved']}))
        self.assertEqual(plan['floor_pct'], 4.17)


class Headline(unittest.TestCase):
    def _opts(self, yes_people=329_094, yes_pct=5.4, yes_seats=1):
        return [{'id': 'c', 'label': LABELS['c'], 'lean': 'yes', 'pct': yes_pct, 'people': yes_people, 'seats': list(range(1, yes_seats + 1)), 'seats_n': yes_seats},
                {'id': 'a', 'label': LABELS['a'], 'lean': 'no', 'pct': 92.1, 'people': 5_612_889, 'seats': list(range(yes_seats + 1, 13)), 'seats_n': 12 - yes_seats},
                {'id': 'b', 'label': LABELS['b'], 'lean': 'maybe', 'pct': 2.5, 'people': 152_359, 'seats': [], 'seats_n': 0}]

    def test_people_come_from_the_share_never_the_tally(self):
        text, people = ROOM.headline_for(self._opts(), 6_094_342, 12, 'P-Valley viewers')
        self.assertEqual(people, 329_094, 'share x the people the room stands for, the count\'s own messy figure')
        self.assertNotEqual(people, 1, 'never the seat tally')
        self.assertNotEqual(people % 10, 0)
        self.assertTrue(text.startswith('About one in twenty of the 6.09M P-Valley viewers, roughly 329.1K people, are a yes'), text)
        self.assertIn('one of the twelve seats', text)
        self.assertIn('About nine in ten, roughly 5.61M people', text)   # 92.1 is over nine in ten, so never 'almost'
        self.assertNotIn('0 people', text)
        self.assertEqual(ROOM.has_banned(text), [])
        self.assertNotIn('\u2014', text)

    def test_zero_people_only_when_the_count_is_zero(self):
        text, people = ROOM.headline_for(self._opts(yes_people=0, yes_pct=0.0, yes_seats=0), 6_094_342, 12, 'P-Valley viewers')
        self.assertEqual(people, 0)
        self.assertTrue(text.startswith('None of the 6.09M P-Valley viewers are a yes'), text)
        self.assertIn('no seat in the room', text)

    def test_a_board_without_a_yes_leads_with_the_largest_row(self):
        opts = [{'id': 'a', 'label': 'The Republican', 'lean': 'other', 'pct': 29.4, 'people': 5_475_431, 'seats': [1, 2, 3], 'seats_n': 3},
                {'id': 'b', 'label': 'The Democrat', 'lean': 'other', 'pct': 30.1, 'people': 5_605_803, 'seats': [4, 5, 6, 7], 'seats_n': 4},
                {'id': 'c', 'label': 'Did not vote', 'lean': 'other', 'pct': 40.5, 'people': 7_542_697, 'seats': [8, 9, 10, 11, 12], 'seats_n': 5}]
        text, people = ROOM.headline_for(opts, 18_623_931, 12, 'Texas registered voters')
        self.assertEqual(people, 7_542_697)
        self.assertTrue(text.startswith('About two in five of the 18.62M Texas registered voters, roughly 7.54M people, land on "Did not vote": five of the twelve seats.'), text)


class Coherence(unittest.TestCase):
    OPTS = [{'id': 'a', 'label': LABELS['a'], 'lean': 'no', 'pct': 92.1, 'people': 5_612_889, 'seats': list(range(2, 13)), 'seats_n': 11},
            {'id': 'b', 'label': LABELS['b'], 'lean': 'maybe', 'pct': 2.5, 'people': 152_359, 'seats': [], 'seats_n': 0},
            {'id': 'c', 'label': LABELS['c'], 'lean': 'yes', 'pct': 5.4, 'people': 329_094, 'seats': [1], 'seats_n': 1}]

    def test_nobody_against_a_non_zero_count_is_rewritten(self):
        draft = ("Almost nine in ten of these P-Valley viewers say they still have Starz and skip S.W.A.T. Exiles, and all twelve seats in the room landed there, "
                 "with nobody watching it on Fridays. Nobody in this group is canceling before December 4.")
        out = ROOM.cohere_read(draft, self.OPTS, 12)
        self.assertIn('eleven of the twelve seats in the room landed there', out)
        self.assertIn('with about one in twenty watching it on Fridays', out)
        self.assertNotIn('nobody watching', out.lower())
        self.assertIn('Nobody in this group is canceling before December 4.', out, 'a nobody about something that is not an option is left alone')

    def test_zero_seats_and_zero_people_are_rewritten(self):
        out = ROOM.cohere_read('No seat is watching it most Fridays, 0 of 12, so 0 people watch most Fridays.', self.OPTS, 12)
        self.assertEqual(out, 'One seat is watching it most Fridays, 1 of 12, so about one in twenty watch most Fridays.')

    def test_a_read_that_does_not_open_on_the_split_opens_with_the_headline(self):
        head, _ = ROOM.headline_for(self.OPTS, 6_094_342, 12, 'P-Valley viewers')
        out = ROOM.cohere_read('The reason was genre, not awareness. They are holding Starz for December 4.', self.OPTS, 12, head)
        self.assertTrue(out.startswith(head), out)
        kept = ROOM.cohere_read('Almost nine in ten skip it; the reason was genre, not awareness.', self.OPTS, 12, head)
        self.assertFalse(kept.startswith(head), 'a read already on the split keeps its own opening')

    def test_a_zero_count_may_say_none(self):
        opts = [{'id': 'a', 'label': 'Yes, I would buy it', 'lean': 'yes', 'pct': 0.0, 'people': 0, 'seats': [], 'seats_n': 0},
                {'id': 'b', 'label': 'No, not for me', 'lean': 'no', 'pct': 100.0, 'people': 1_000_003, 'seats': list(range(1, 13)), 'seats_n': 12}]
        draft = 'Nobody would buy it; all twelve seats said no and none of them would buy it.'
        self.assertEqual(ROOM.cohere_read(draft, opts, 12), draft)
        self.assertEqual(ROOM.plain_share(0.0), 'none')
        self.assertEqual(ROOM.plain_share(5.4), 'about one in twenty')
        self.assertEqual(ROOM.plain_share(1.1), 'about one in ninety')   # the closest fraction within ten percent (Jenna 2026-11-05)

    def test_all_twelve_of_something_else_is_left_alone(self):
        out = ROOM.cohere_read('They watched all twelve episodes of the last season.', self.OPTS, 12)
        self.assertEqual(out, 'They watched all twelve episodes of the last season.')


class Room(unittest.TestCase):
    def _ask(self, q='are you currently watching swat exiles?', fake=fake_model):
        twin = _pvalley()
        sess = P.open_session([twin], in_room=12, title='P-Valley', session_id='room-mirror-1')
        for k in SEEN:
            SEEN[k].clear()
        rec = ROOM.ask_room(sess, {twin['meta']['key']: twin}, q, s3=None, twin_key='pv', run_async=False, model_fn=fake)
        return sess, rec

    def test_twelve_seats_on_skip_mirror_a_count_of_88_7_5(self):
        sess, rec = self._ask()
        self.assertEqual(rec['stage'], 'done')
        self.assertEqual([f for f in rec['trail']['fallbacks'] if f != 'read'], [])
        self.assertEqual(rec['engine'], ROOM.ROOM_VERSION)   # room-4 landed the mirror; room-5 (the study desk) runs on top of it
        by = {o['id']: o for o in rec['options']}
        self.assertEqual(rec['anchor']['kind'], 'result')
        self.assertAlmostEqual(by['a']['pct'], 88.0, delta=1.0)
        self.assertAlmostEqual(by['c']['pct'], 5.0, delta=1.0)
        self.assertEqual((by['a']['seats_n'], by['b']['seats_n'], by['c']['seats_n']), (10, 1, 1), 'the seats mirror the count: the one-in-twenty side holds a seat')
        self.assertEqual(sum(t['seats'] for t in rec['tally']), 12)
        self.assertEqual(rec['tally'][2], {'label': LABELS['c'], 'seats': 1, 'of': 12, 'room_pct': 8.3})
        self.assertEqual(len(SEEN['persona']), 12, 'every seat answered freely first')
        self.assertEqual(len(SEEN['mirror']), 2, 'the two moved seats argue their stance in character')
        self.assertTrue(all(b['your_answer']['id'] in ('b', 'c') for b in SEEN['mirror']))
        self.assertTrue(all('what_people_like_you_have_been_hearing_and_saying' in b for b in SEEN['mirror']), 'a moved seat still hears the talk')
        moved = [a for a in rec['answers'] if a.get('mirrored')]
        self.assertEqual(len(moved), 2)
        for a in moved:
            self.assertIn(a['option'], ('b', 'c'))
            self.assertEqual(a['option_label'], LABELS[a['option']])
            self.assertIn('Honestly that is me', a['line'])
        self.assertEqual(len(rec['trail']['mirror']['moved']), 2)
        self.assertEqual(rec['trail']['mirror']['alloc'][LABELS['c']], 1)
        self.assertEqual(sum(o['people'] for o in rec['options']), 6_094_342)
        for o in rec['options']:
            self.assertNotEqual(o['people'] % 10, 0, o['label'])

    def test_headline_people_are_the_share_and_the_read_states_one_split(self):
        sess, rec = self._ask()
        by = {o['id']: o for o in rec['options']}
        self.assertEqual(rec['headline_people'], by['c']['people'], 'share x the people the room stands for')
        self.assertNotEqual(rec['headline_people'], by['c']['seats_n'])
        self.assertNotEqual(rec['headline_people'] % 10, 0)
        self.assertTrue(rec['headline'].startswith('About one in twenty of the 6.09M P-Valley viewers, roughly'), rec['headline'])
        self.assertIn(f"{ROOM.human_people(by['c']['people'])} people, are a yes", rec['headline'])
        self.assertIn('one of the twelve seats', rec['headline'])
        self.assertEqual(SEEN['read'][0]['headline'], rec['headline'], 'the moderator is handed the headline')
        self.assertIn('eleven of the twelve seats', rec['read'].replace('ten of the twelve seats', 'eleven of the twelve seats'))
        self.assertIn('ten of the twelve seats in the room landed there', rec['read'])
        self.assertIn('with about one in twenty watching it on Fridays', rec['read'])
        self.assertNotIn('nobody', rec['read'].lower())
        self.assertNotIn('all twelve', rec['read'].lower())
        self.assertIn('ten of the twelve seats', rec['read_short'])
        self.assertNotIn('nobody', rec['read_short'].lower())
        self.assertIn('About one in twenty is watching it on Fridays.', rec['read_short'])
        for s in (rec['read'], rec['read_short'], rec['headline']):
            self.assertEqual(ROOM.has_banned(s), [], s)
            self.assertNotIn('\u2014', s)
        lines = ROOM.chat_lines(sess, rec)
        self.assertIn(f"{LABELS['c']}: {by['c']['pct']}% ({by['c']['people']:,} people; in the room: 1 of 12)", lines)

    def test_a_silent_model_still_lands_a_coherent_record(self):
        def silent(role, system, user, **kw):
            if role in ('research', 'media') or (role == 'moderator' and 'supplied_options' in json.loads(user)):
                return fake_model(role, system, user, **kw)
            return None
        sess, rec = self._ask(fake=silent)
        self.assertEqual(rec['stage'], 'done')
        self.assertEqual(sum(o['seats_n'] for o in rec['options']), 12)
        alloc = ROOM.allocate_seats({o['id']: o['pct'] for o in rec['options']}, 12)
        self.assertEqual({o['id']: o['seats_n'] for o in rec['options']}, alloc, 'the tally is the count\'s own allocation')
        self.assertTrue(rec['headline'])
        self.assertTrue(rec['read'].startswith(rec['headline']), 'the deterministic read opens with the headline')
        for a in rec['answers']:
            self.assertEqual(a['option_label'], LABELS[a['option']])
            self.assertTrue(a['line'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
