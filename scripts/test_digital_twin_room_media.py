#!/usr/bin/env python3
"""The room knows what people were saying, and how the event came out.

Jenna 2026-11-04, on the Texas voting room the morning after the election:
"the room needs more data to have accurately answered these because a
huge thing texans were talking about was how democracy was important to
them and it wasn't for sale and elon spending so much pissed them off.
also i ran this today and abbott lost but it predicted he would win. so
needs to do media scrapes, etc for what the room knows".

Two things, held here:
  * a media desk runs beside the facts desk on every question: the last
    two weeks of coverage, what people in this audience were actually
    saying (in their words), the latest polls, and whether the event the
    question turns on has happened and how it came out; the seats hear
    it, the page shows it;
  * a counted event anchors the published split (the seats keep their
    votes and their lines); a published poll before the event leans on
    the split two to one.

Hermetic: a fake model answers every desk; no S3.

    python3 scripts/test_digital_twin_room_media.py
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


def _texas():
    return {
        'meta': {'name': 'Texas registered voters', 'key': 'txv', 'subject': 'Texas registered voters', 'subject_kind': 'audience', 'format': 'electorate',
                 'size_us': 18_623_931, 'profile_key': 'Texas_Registered_Voters_10_30_2026_09_00.csv'},
        'identity': {'base': {'kind': 'electorate'}, 'read': 'Registered voters across Texas, from the big metros to the Panhandle.',
                     'demographics': {
                         'gender': [{'label': 'Female', 'pct': 52.1012}, {'label': 'Male', 'pct': 47.8988}],
                         'age': [{'label': '18-24', 'pct': 9.1}, {'label': '25-34', 'pct': 17.4}, {'label': '35-44', 'pct': 18.2}, {'label': '45-54', 'pct': 17.1},
                                 {'label': '55-64', 'pct': 17.6}, {'label': '65+', 'pct': 20.6}],
                         'education': [{'label': 'Bachelors Degree', 'pct': 24.3}, {'label': 'Some College', 'pct': 30.2}, {'label': 'High School or Less', 'pct': 33.1},
                                       {'label': 'Graduate or Professional Degree', 'pct': 12.4}],
                         'occupation': [{'label': 'Management/Business/Professional', 'pct': 30.1}, {'label': 'Service', 'pct': 20.6}, {'label': 'Sales', 'pct': 12.1},
                                        {'label': 'Healthcare', 'pct': 11.2}, {'label': 'Construction/Trades', 'pct': 10.4}],
                     }},
        'traits': {'franchise_loyalty': {'value': 55.0, 'label': 'loyalty', 'read': ''}, 'second_screen': {'value': 50.0, 'label': 'second', 'read': ''},
                   'binge_vs_appointment': {'value': 50.0, 'label': 'binge', 'read': ''}, 'word_of_mouth': {'value': 50.0, 'label': 'wom', 'read': ''},
                   'trial_speed': {'value': 50.0, 'label': 'trial', 'read': ''}},
        'lifecycle': {'habit_pct': 60.0, 'discovering_pct': 10.0, 'trying_pct': 10.0, 'lapsing_pct': 10.0, 'returning_pct': 10.0},
    }


MEDIA = {
    'event': {'what': 'Texas general election: US Senate and governor', 'date': '2026-11-03', 'status': 'happened',
              'outcome': 'James Talarico won the Senate seat with 50.3% to Ken Paxton\'s 47.9%, called by the AP early Wednesday.',
              'results': [{'label': 'James Talarico (D)', 'share_pct': 50.3, 'status': 'called'}, {'label': 'Ken Paxton (R)', 'share_pct': 47.9, 'status': 'called'}],
              'turnout_pct': 58.4},
    'polls': [{'label': 'James Talarico (D)', 'share_pct': 49.0, 'as_of': '2026-10-25', 'source_class': 'poll'}, {'label': 'Ken Paxton (R)', 'share_pct': 47.0, 'as_of': '2026-10-25', 'source_class': 'poll'}],
    'coverage': [{'line': 'The money Elon Musk put into the Texas races led the last week of coverage.', 'reach': 'dominant', 'local': True, 'days_ago': 2},
                 {'line': 'Paxton\'s impeachment by his own party came back up in the closing debates.', 'reach': 'common', 'local': True, 'days_ago': 5}],
    'talk': [{'line': 'Texas is not for sale', 'who': 'Democrats and a lot of independents', 'loudness': 'dominant', 'feeling': 'angry'},
             {'line': 'Democracy matters more than my property tax bill this time', 'who': 'suburban women', 'loudness': 'common', 'feeling': 'worried'},
             {'line': 'I don\'t care who pays for the ads, the border is the border', 'who': 'rural Republicans', 'loudness': 'common', 'feeling': 'dismissive'}],
    'numbers': [{'fact': 'Turnout reached 58.4% of registered voters, the highest for a midterm in Texas since 2018.', 'source_class': 'turnout'}],
    'local': ['Houston talk radio spent the week on the Musk money.'],
    'not_found': [],
}

FACTS = {'subject_status': {'line': 'Texas registered voters: 18,623,931 people on the rolls going into the Nov 3, 2026 election.'},
         'facts': [{'fact': 'The TSU poll of Oct 21-25 had Talarico at 49% and Paxton at 47%.', 'source_class': 'poll', 'topic': 'subject'}],
         'not_found': [], 'implications': ['A close race decided by turnout.']}


SEEN = {'persona': [], 'options': []}
SEATS = {}   # name -> seat, filled by the test from the roster


def fake_model(role, system, user, **kw):
    d = json.loads(user)
    if role == 'research':
        return dict(FACTS)
    if role == 'media':
        return json.loads(json.dumps(MEDIA))
    if role == 'moderator' and 'supplied_options' in d:
        SEEN['options'].append(d)
        return {'options': [{'id': 'a', 'label': 'Ken Paxton, the Republican', 'lean': 'other'}, {'id': 'b', 'label': 'James Talarico, the Democrat', 'lean': 'other'},
                            {'id': 'c', 'label': 'Another candidate or a write-in', 'lean': 'other'}, {'id': 'd', 'label': 'Did not cast a ballot in this election', 'lean': 'other'}],
                'framing': 'Who did you vote for in the Senate race?', 'listen_for': ['the Musk money', 'the border']}
    if role == 'moderator':
        return {'read': 'Talarico won, and the room shows why the margin was close.', 'voices': [1, 2, 3], 'probes_next': ['Why?'], 'facts_used': ['Turnout reached 58.4% of registered voters.']}
    if role == 'persona':
        SEEN['persona'].append(d)
        name = str(d['you']).split(',')[0].strip()
        seat = int(SEATS.get(name) or 1)
        opt = 'a' if seat % 3 else 'b'      # eight of twelve for Paxton: the seats lean the other way from the count
        return {'option': opt, 'line': 'I voted the way I said I would, border first.' if opt == 'a' else 'Texas is not for sale, that is why.', 'confidence': 4, 'reason': 'as said'}
    return None


class Desk(unittest.TestCase):
    def test_media_brief_is_shaped_and_merged(self):
        m = ROOM._norm_media(json.loads(json.dumps(MEDIA)))
        self.assertEqual(m['event']['status'], 'happened')
        self.assertEqual(len(m['talk']), 3)
        brief = ROOM.merge_media(dict(FACTS), m)
        lines = ROOM.facts_lines(brief)
        self.assertTrue(lines[0].startswith('James Talarico won'), 'how it came out is the first thing the room knew')
        self.assertIn('Turnout reached 58.4% of registered voters, the highest for a midterm in Texas since 2018.', lines)
        talk = ROOM.talk_lines(brief)
        self.assertEqual(talk[0], '\u201cTexas is not for sale\u201d (Democrats and a lot of independents; heard everywhere)')
        self.assertEqual(ROOM._norm_media({'coverage': [], 'talk': []}), {})
        self.assertEqual(ROOM._norm_media(FACTS), {}, 'a facts-shaped answer is not a media brief')

    def test_result_rows_land_on_the_options(self):
        opts = [{'id': 'a', 'label': 'Ken Paxton, the Republican'}, {'id': 'b', 'label': 'James Talarico, the Democrat'},
                {'id': 'c', 'label': 'Another candidate or a write-in'}, {'id': 'd', 'label': 'Did not cast a ballot in this election'}]
        m = ROOM.match_targets(opts, MEDIA['event']['results'])
        self.assertEqual(m, {'b': 50.3, 'a': 47.9})
        party_only = [{'id': 'a', 'label': 'The Republican'}, {'id': 'b', 'label': 'The Democrat'}]
        self.assertEqual(ROOM.match_targets(party_only, MEDIA['event']['results']), {'b': 50.3, 'a': 47.9})
        self.assertEqual(ROOM.match_targets([{'id': 'a', 'label': 'Yes'}, {'id': 'b', 'label': 'No'}], MEDIA['event']['results']), {}, 'nothing named, nothing matched')
        two_paxtons = [{'id': 'a', 'label': 'Ken Paxton, the Republican'}, {'id': 'b', 'label': 'Paxton with reservations'}, {'id': 'c', 'label': 'James Talarico, the Democrat'}]
        self.assertEqual(ROOM.match_targets(two_paxtons, [{'label': 'Paxton', 'share_pct': 47.9}, {'label': 'James Talarico (D)', 'share_pct': 50.3}]), {},
                         'a row that could be two options matches neither, and one match is not enough')
        self.assertEqual(ROOM.match_targets(two_paxtons, [{'label': 'Paxton (R)', 'share_pct': 47.9}, {'label': 'James Talarico (D)', 'share_pct': 50.3}]), {'a': 47.9, 'c': 50.3},
                         'the party word settles it')

    def test_the_plan_sets_stayed_home_from_turnout(self):
        opts = [{'id': 'a', 'label': 'Ken Paxton, the Republican', 'pct': 62.8}, {'id': 'b', 'label': 'James Talarico, the Democrat', 'pct': 33.7},
                {'id': 'c', 'label': 'Another candidate or a write-in', 'pct': 0.2}, {'id': 'd', 'label': 'Did not cast a ballot in this election', 'pct': 3.3}]
        brief = ROOM.merge_media(dict(FACTS), ROOM._norm_media(json.loads(json.dumps(MEDIA))))
        plan = ROOM.anchor_plan(opts, brief, 'civic')
        self.assertEqual(plan['kind'], 'result')
        self.assertAlmostEqual(plan['targets']['d'], 41.6, places=1)
        self.assertAlmostEqual(plan['targets']['b'], 50.3 / 100 * 58.4, places=1)
        self.assertIn('sits on the count', plan['line'])
        # no turnout in the count: the twin's own base (registered to voted last time) fills it
        b1 = dict(brief, event=dict(brief['event'], turnout_pct=None))
        self.assertNotIn('d', ROOM.anchor_plan(opts, b1, 'civic')['targets'])
        tw = _texas(); tw['identity']['base'].update(count_us=18_623_931, turnout_us=11_403_003)
        self.assertEqual(ROOM._base_turnout_pct(tw), 61.2)
        plan1 = ROOM.anchor_plan(opts, b1, 'civic', turnout_fallback=ROOM._base_turnout_pct(tw))
        self.assertAlmostEqual(plan1['targets']['d'], 38.8, places=1)
        # before the count, the poll leans
        b2 = dict(brief, event=dict(brief['event'], status='today', results=[], outcome=''))
        plan2 = ROOM.anchor_plan(opts, b2, 'civic')
        self.assertEqual(plan2['kind'], 'poll')
        self.assertEqual(plan2['weight'], ROOM.ANCHOR_POLL_WEIGHT)


class Room(unittest.TestCase):
    def _ask(self, q, fake=fake_model):
        twin = _texas()
        sess = P.open_session([twin], in_room=12, title='Voting', session_id='room-media-1')
        SEATS.clear()
        SEATS.update({str(r['name']): int(r['seat']) for r in sess['group']['roster']})
        SEEN['persona'].clear(); SEEN['options'].clear()
        rec = ROOM.ask_room(sess, {twin['meta']['key']: twin}, q, s3=None, twin_key='txv', run_async=False, model_fn=fake)
        return sess, rec

    def test_the_room_knows_the_count_and_the_talk(self):
        sess, rec = self._ask('who did you vote for for us senator today')
        self.assertEqual(rec['stage'] if 'stage' in rec else 'done', 'done')
        self.assertEqual(rec['trail']['fallbacks'], [])
        roles = [c['stage'] for c in rec['trail']['call_log']]
        self.assertIn('media', roles)
        self.assertIn('research', roles)
        self.assertTrue(rec['outcome'].startswith('James Talarico won'))
        self.assertTrue(all('what_people_like_you_have_been_hearing_and_saying' in b for b in SEEN['persona']), 'the seats hear the talk')
        self.assertTrue(all(b.get('how_it_came_out', {}).get('outcome') for b in SEEN['persona']), 'the seats know how it came out')
        self.assertIn('Texas is not for sale', json.dumps(SEEN['persona'][0]['what_people_like_you_have_been_hearing_and_saying']))
        self.assertIn('talk', SEEN['options'][0]['facts_brief'], 'the options desk reads the talk')
        self.assertEqual(rec['facts_used'][0], rec['outcome'], 'the result leads what the room knew')
        self.assertEqual(len(rec['talk']), 3)
        self.assertIn('Texas is not for sale', rec['talk'][0])
        by = {o['label']: o for o in rec['options']}
        pax, tal, home = by['Ken Paxton, the Republican'], by['James Talarico, the Democrat'], by['Did not cast a ballot in this election']
        self.assertEqual(pax['seats_n'], 8, 'the seats keep their votes')
        self.assertEqual(tal['seats_n'], 4)
        self.assertGreater(tal['pct'], pax['pct'], 'the published split sits on the count, not the eight seats')
        self.assertAlmostEqual(tal['pct'], 50.3 / 100 * 58.4, delta=1.0)
        self.assertAlmostEqual(pax['pct'], 47.9 / 100 * 58.4, delta=1.0)
        self.assertAlmostEqual(home['pct'], 41.6, delta=1.0)
        self.assertEqual(rec['anchor']['kind'], 'result')
        self.assertAlmostEqual(sum(o['pct'] for o in rec['options']), 100.0, places=1)
        self.assertEqual(sum(o['people'] for o in rec['options']), 18_623_931)
        for sr in rec['by_segment']:
            self.assertEqual(sum(r['people'] for r in sr['distribution']), sr['people'])
        for o in rec['options']:
            self.assertNotEqual(o['people'] % 10, 0, o['label'])
        self.assertIn('sits on the count', rec['trail']['prior']['note'])

    def test_before_the_count_the_poll_leans_two_to_one(self):
        def fake(role, system, user, **kw):
            if role == 'media':
                m = json.loads(json.dumps(MEDIA))
                m['event'].update(status='today', results=[], outcome='')
                return m
            return fake_model(role, system, user, **kw)
        sess, rec = self._ask('who are you voting for for us senator today', fake)
        self.assertEqual(rec['anchor']['kind'], 'poll')
        by = {o['label']: o for o in rec['options']}
        pax, tal = by['Ken Paxton, the Republican'], by['James Talarico, the Democrat']
        self.assertEqual(pax['seats_n'], 8)
        self.assertLess(pax['pct'], 60.0, 'the poll pulls the eight seats down')
        self.assertGreater(pax['pct'], tal['pct'] - 12.0)
        self.assertEqual(rec['outcome'], '')
        self.assertNotIn('won', ' '.join(rec['facts_used']).lower())

    def test_no_media_still_answers(self):
        def fake(role, system, user, **kw):
            if role == 'media':
                return None
            if role == 'persona':
                d = json.loads(user)
                SEEN['persona'].append(d)
                return {'option': 'a', 'line': 'Paxton.', 'confidence': 3, 'reason': 'as said'}
            return fake_model(role, system, user, **kw)
        sess, rec = self._ask('who did you vote for for us senator today', fake)
        self.assertEqual(rec['talk'], [])
        self.assertNotIn('anchor', rec)
        self.assertTrue(all('what_people_like_you_have_been_hearing_and_saying' not in b for b in SEEN['persona']))
        self.assertIn('media', rec['trail']['fallbacks'] + [c['stage'] for c in rec['trail']['call_log']])
        self.assertTrue(rec['options'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
