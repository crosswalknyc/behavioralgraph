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
  * a counted event anchors the published split; a published poll before
    the event leans on the split two to one. Since room-4 (Jenna
    2026-11-05, scripts/test_digital_twin_room_seats_mirror_count.py)
    the seats are then allocated to that split by largest remainder and
    a moved seat argues its stance in character, so the tally and the
    count tell one story (before that the seats kept their own votes).

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

    def test_the_stayed_home_row_is_found_however_it_is_worded(self):
        # Jenna's live q3 (2026-11-04): 'Did not end up voting in this one' was not found and the count filled the whole room
        self.assertTrue(ROOM.is_nonvote_label('Did not end up voting in this one'))
        for lab in ['did not end up voting', "didn't vote", 'did not vote', 'never voted', 'stayed home', 'sat this one out', 'sat it out', 'skipped it',
                    'did not make it to the polls', 'did not turn out', 'did not cast a ballot', 'no ballot', 'did not participate', 'not voting this time',
                    'chose not to vote', 'Did not vote in this election', 'Did not cast a ballot in this election', 'Did not get a chance to vote',
                    "Wasn't able to vote", 'Did not even bother to vote', 'Never made it to the polls', "Didn't show up", 'Did not go vote', 'Opted out this time',
                    'Non-voter', 'Decided against voting', 'Stayed at home', 'Skipped this election', 'Did not see the point of voting']:
            self.assertTrue(ROOM.is_nonvote_label(lab), lab)
        for lab in ['Voted the ballot but left Senate blank', 'I voted for Ken Paxton', 'Ken Paxton, the Republican', 'James Talarico, the Democrat',
                    'Someone else, third party or write-in', 'Not sure who I voted for', 'Did not go with either major party', 'Did not like either but voted Paxton',
                    'Not Paxton, voted Talarico', 'Skipped the Senate race but voted the rest', 'Sat out the Senate race', 'Left the Senate line blank',
                    'Did not vote for either, left it blank', 'Went with a third-party candidate', 'Voted, and would again']:
            self.assertFalse(ROOM.is_nonvote_label(lab), lab)
        q3 = [{'id': 'a', 'label': 'James Talarico, the Democrat', 'lean': 'other'}, {'id': 'b', 'label': 'Ken Paxton, the Republican', 'lean': 'other'},
              {'id': 'c', 'label': 'Someone else, third party or write-in', 'lean': 'other'}, {'id': 'd', 'label': 'Voted the ballot but left Senate blank', 'lean': 'other'},
              {'id': 'e', 'label': 'Did not end up voting in this one', 'lean': 'other'}]
        self.assertEqual(ROOM.nonvote_option(q3), 'e')
        self.assertEqual(ROOM.nonvote_option(q3, matched={'a', 'b'}), 'e')
        # a candidate's row is never the stayed-home row, even when its label carries a negation
        odd = [{'id': 'a', 'label': 'Did not vote Paxton, went Talarico', 'lean': 'other'}, {'id': 'b', 'label': 'Ken Paxton, the Republican', 'lean': 'other'},
               {'id': 'e', 'label': 'Stayed home', 'lean': 'other'}]
        self.assertEqual(ROOM.nonvote_option(odd, matched={'a', 'b'}), 'e')
        # no label matches: the loose pick is a negated vote word, or a 'no' lean about voting; never a row that is not about not voting
        loose = [{'id': 'a', 'label': 'Ken Paxton, the Republican', 'lean': 'other'}, {'id': 'b', 'label': 'James Talarico, the Democrat', 'lean': 'other'},
                 {'id': 'c', 'label': 'Someone else, third party or write-in', 'lean': 'other'}, {'id': 'd', 'label': 'My vote never happened this year', 'lean': 'other'}]
        self.assertIsNone(ROOM.nonvote_option(loose, matched={'a', 'b'}))
        self.assertEqual(ROOM.nonvote_option(loose, matched={'a', 'b'}, loose=True), 'd')
        self.assertIsNone(ROOM.nonvote_option([{'id': 'a', 'label': 'Yes, I voted', 'lean': 'yes'}, {'id': 'b', 'label': 'No', 'lean': 'no'}], loose=True))
        self.assertEqual(ROOM.nonvote_option([{'id': 'a', 'label': 'Yes, I voted', 'lean': 'yes'}, {'id': 'b', 'label': 'No, my ballot stayed in the drawer', 'lean': 'no'}], loose=True), 'b')
        self.assertIsNone(ROOM.nonvote_option([{'id': 'a', 'label': 'Ken Paxton', 'lean': 'other'}, {'id': 'b', 'label': 'Not sure', 'lean': 'other'}], loose=True))

    def test_q3_as_the_moderator_coded_it_anchors_with_turnout(self):
        q3 = [{'id': 'a', 'label': 'James Talarico, the Democrat', 'lean': 'other', 'pct': 40.1}, {'id': 'b', 'label': 'Ken Paxton, the Republican', 'lean': 'other', 'pct': 55.2},
              {'id': 'c', 'label': 'Someone else, third party or write-in', 'lean': 'other', 'pct': 0.9}, {'id': 'd', 'label': 'Voted the ballot but left Senate blank', 'lean': 'other', 'pct': 0.8},
              {'id': 'e', 'label': 'Did not end up voting in this one', 'lean': 'other', 'pct': 3.0}]
        brief = ROOM.merge_media(dict(FACTS), ROOM._norm_media(json.loads(json.dumps(MEDIA))))
        plan = ROOM.anchor_plan(q3, brief, 'civic')
        self.assertEqual(plan['kind'], 'result')
        self.assertAlmostEqual(plan['targets']['e'], 100.0 - 58.4, places=1, msg='stayed home is the whole electorate minus turnout')
        self.assertAlmostEqual(plan['targets']['a'], 50.3 / 100 * 58.4, places=1, msg="a candidate's row is the count's share of the votes cast")
        self.assertAlmostEqual(plan['targets']['b'], 47.9 / 100 * 58.4, places=1)
        self.assertNotIn('c', plan['targets']); self.assertNotIn('d', plan['targets'])
        self.assertEqual(plan['fill_voting'], 58.4)
        self.assertNotIn('votes cast', plan['line'])
        # the count carries no turnout: the twin's base turnout fills it
        b1 = dict(brief, event=dict(brief['event'], turnout_pct=None))
        plan1 = ROOM.anchor_plan(q3, b1, 'civic', turnout_fallback=61.2)
        self.assertAlmostEqual(plan1['targets']['e'], 38.8, places=1)
        self.assertAlmostEqual(plan1['targets']['a'], 50.3 / 100 * 61.2, places=1)
        # the loose pick rides the same arithmetic
        q3l = [dict(o) for o in q3]; q3l[4]['label'] = 'My vote never happened this year'
        self.assertAlmostEqual(ROOM.anchor_plan(q3l, brief, 'civic')['targets']['e'], 41.6, places=1)
        # no stayed-home option at all: the count's shares fill the room and the note says the split is of the votes cast
        plan2 = ROOM.anchor_plan(q3[:4], brief, 'civic')
        self.assertNotIn('e', plan2['targets'])
        self.assertAlmostEqual(plan2['targets']['a'] + plan2['targets']['b'], 100.0, places=1)
        self.assertIn('The published split is of the votes cast; the room had no stayed-home option.', plan2['line'])
        self.assertIsNone(plan2['fill_voting'])
        self.assertNotIn('votes cast', ROOM.anchor_plan(q3[:4], brief, 'title')['line'], 'the sentence is for civic rooms')


class HostFallthrough(unittest.TestCase):
    def test_a_silent_host_client_falls_through_to_the_direct_chain(self):
        # 2026-11-04: a re-run from a shell whose host client answers nothing (its reasoning switch off)
        # landed yes/maybe/no options; the named chain now answers in that case
        calls = []
        M = ROOM.Models()
        M._host_claude = lambda: (lambda *a, **k: None)
        M._chain = lambda system, user, max_tokens, temperature, surface, chain, tools=None: (calls.append((surface, tuple(chain))) or ({'options': [{'id': 'a', 'label': 'x', 'lean': 'yes'}]}, chain[0]))
        os.environ['REGRESSION_TEST_MODE'] = '0'
        try:
            data, rec = M.call('moderator', 'sys', '{}', max_tokens=10)
        finally:
            os.environ['REGRESSION_TEST_MODE'] = '1'
        self.assertEqual(data['options'][0]['label'], 'x')
        self.assertEqual(calls[0][0], 'twin_room_moderator')
        self.assertEqual(calls[0][1], tuple(ROOM.MODELS['strong']))
        self.assertTrue(rec['ok'])


class Room(unittest.TestCase):
    def _ask(self, q, fake=fake_model):
        twin = _texas()
        sess = P.open_session([twin], in_room=12, title='Voting', session_id='room-media-1')
        SEATS.clear()
        SEATS.update({str(r['name']): int(r['seat']) for r in sess['group']['roster']})
        SEEN['persona'].clear(); SEEN['options'].clear()
        ROOM.clear_room_caches()   # each test's room starts from nothing (the day caches would serve another test's board)
        rec = ROOM.ask_room(sess, {twin['meta']['key']: twin}, q, s3=None, twin_key='txv', run_async=False, model_fn=fake)
        return sess, rec

    def test_the_room_knows_the_count_and_the_talk(self):
        sess, rec = self._ask('who did you vote for for us senator today')
        self.assertEqual(rec['stage'] if 'stage' in rec else 'done', 'done')
        self.assertEqual(rec['trail']['fallbacks'], [])
        roles = [c['stage'] for c in rec['trail']['call_log']]
        self.assertIn('media', roles)
        self.assertIn('research', roles)
        # room-5: the study desk runs first (this fake answers it with nothing, so it is asked twice and the room is
        # marked unresearched only when no count anchors it); the event brief is gathered after it
        self.assertIn('study', roles)
        self.assertLess(roles.index('study'), roles.index('media'), 'the research pass runs before the event brief')
        self.assertLess(roles.index('study'), roles.index('persona'))
        self.assertEqual(rec['trail']['anchor_status'], 'result', 'the count still anchors the published split')
        self.assertEqual(rec['trail']['study']['attempts'], 2); self.assertFalse(rec['trail']['study']['ok'])
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
        # room-4: the seats mirror the count (eight of twelve voted Paxton freely; the count seats 3 / 4 / 5 with the stayed-home row)
        self.assertEqual({o['id']: o['seats_n'] for o in rec['options']}, ROOM.allocate_seats({o['id']: o['pct'] for o in rec['options']}, 12))
        self.assertEqual((pax['seats_n'], tal['seats_n'], home['seats_n']), (3, 4, 5))
        self.assertEqual(len(rec['trail']['mirror']['moved']), 5, 'five Paxton seats moved and were voiced again')
        self.assertTrue(all(a['option_label'] == home['label'] for a in rec['answers'] if a.get('mirrored')))
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

    def test_the_room_finds_the_stayed_home_row_as_the_moderator_worded_it(self):
        # the live q3 of 2026-11-04: five options, the stayed-home one worded 'Did not end up voting in this one'
        def fake(role, system, user, **kw):
            d = json.loads(user)
            if role == 'moderator' and 'supplied_options' in d:
                return {'options': [{'id': 'a', 'label': 'James Talarico, the Democrat', 'lean': 'other'}, {'id': 'b', 'label': 'Ken Paxton, the Republican', 'lean': 'other'},
                                    {'id': 'c', 'label': 'Someone else, third party or write-in', 'lean': 'other'}, {'id': 'd', 'label': 'Voted the ballot but left Senate blank', 'lean': 'other'},
                                    {'id': 'e', 'label': 'Did not end up voting in this one', 'lean': 'other'}],
                        'framing': 'Who did you vote for in the Senate race?', 'listen_for': ['the Musk money']}
            if role == 'persona':
                SEEN['persona'].append(d)
                seat = int(SEATS.get(str(d['you']).split(',')[0].strip()) or 1)
                return {'option': ('b' if seat % 3 else 'a'), 'line': 'As I said.', 'confidence': 4, 'reason': 'as said'}
            return fake_model(role, system, user, **kw)
        sess, rec = self._ask('who did you vote for for us senator today', fake)
        self.assertEqual(rec['anchor']['kind'], 'result')
        by = {o['label']: o for o in rec['options']}
        tal, pax, home = by['James Talarico, the Democrat'], by['Ken Paxton, the Republican'], by['Did not end up voting in this one']
        self.assertAlmostEqual(home['pct'], 41.6, delta=1.0, msg='the stayed-home row is the electorate minus turnout')
        self.assertAlmostEqual(tal['pct'], 50.3 / 100 * 58.4, delta=1.0, msg='a candidate reads as a share of the whole electorate, not of the votes cast')
        self.assertAlmostEqual(pax['pct'], 47.9 / 100 * 58.4, delta=1.0)
        self.assertLess(tal['people'], 6_000_000, 'about 5.5M votes for Talarico, not 10M')
        self.assertAlmostEqual(rec['anchor']['targets']['Did not end up voting in this one'], 41.6, places=1)
        self.assertEqual({o['id']: o['seats_n'] for o in rec['options']}, ROOM.allocate_seats({o['id']: o['pct'] for o in rec['options']}, 12), 'the seats mirror the count')
        self.assertEqual(home['seats_n'], 5)
        self.assertEqual(sum(o['people'] for o in rec['options']), 18_623_931)
        self.assertNotIn('votes cast', rec['trail']['prior']['note'])

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
        self.assertEqual({o['id']: o['seats_n'] for o in rec['options']}, ROOM.allocate_seats({o['id']: o['pct'] for o in rec['options']}, 12), 'the seats mirror the blended split')
        self.assertLess(pax['seats_n'], 8, 'the poll pulls the eight free Paxton votes down to the blended split')
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
        self.assertEqual(rec['trail']['anchor_status'], 'unresearched', 'no count, no poll, and this fake gives the study desk nothing: marked on the trail, silent in the read')
        self.assertNotIn('unresearched', rec['read'].lower())
        self.assertTrue(all('what_people_like_you_have_been_hearing_and_saying' not in b for b in SEEN['persona']))
        self.assertIn('media', rec['trail']['fallbacks'] + [c['stage'] for c in rec['trail']['call_log']])
        self.assertTrue(rec['options'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
