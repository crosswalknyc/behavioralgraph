#!/usr/bin/env python3
"""Research first, then reason (room-5).

Jenna 2026-11-05, on the P-Valley room's 'are you currently watching swat
exiles?' (7.6% keeping up weekly from the seats and the twin's prior alone,
trail.anchor null): "It should do external research heavily and THEN
reason. just because we don't have anything to anchor on internally ... it
has to be smarter. have it rethink that and redo the answer".

Held here:
  * a title question with no internal read runs the study desk, with at
    least three distinct searches, before the seats answer; the media desk
    gets the study's facts;
  * the analyst's derived anchor is bounded: each engaged row in the bottom
    third of its range, the engaged rows under the title's reach (the
    record's own count when it holds one), the current rows under its
    current reach, the engaged rows under a counted neighbor's rate unless
    the audience is placed as more adjacent for a stated reason;
  * the published count lands at the anchor and the seats mirror it;
  * a cached subject is not re-searched the same day (the process cache
    and the S3 cache);
  * a study that fails outright is retried once with narrower queries and
    then marked unresearched on the trail; the room still lands;
  * the read, the chat line, the headline, the framing and the facts carry
    no source language;
  * the record's own reads are found through the ledger (the asked title's
    counted viewers become the reach ceiling).

Hermetic: a fake model answers every desk from fixtures; a fake ledger; no S3
(a dict stands in for it where the cache is tested).

    python3 scripts/test_digital_twin_room_research_first.py
"""
from __future__ import annotations

import copy
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

PEOPLE = 6_094_342
LABELS = {'a': 'Yes, keeping up with it weekly on Starz', 'b': 'Started it or watched an episode, not current', 'c': "Saw the tile, it's on my list for later",
          'd': "No, it's not my kind of show", 'e': 'No, my Starz is off right now or never heard of it'}
LEANS = {'a': 'yes', 'b': 'maybe', 'c': 'maybe', 'd': 'no', 'e': 'no'}


def _pvalley():
    return {
        'meta': {'name': 'PValley on Starz Viewers', 'key': 'pv', 'subject': 'PValley on Starz Viewers', 'subject_kind': 'audience', 'format': 'series', 'size_us': PEOPLE,
                 'platform_home': 'Starz app', 'profile_key': 'PValley_on_Starz_Viewers_11_05_2026_21_49.csv'},
        'identity': {'base': {'kind': 'viewers'}, 'read': 'People who watched P-Valley on Starz in the last year.',
                     'demographics': {
                         'gender': [{'label': 'Female', 'pct': 71.8}, {'label': 'Male', 'pct': 28.2}],
                         'age': [{'label': '18-24', 'pct': 12.1}, {'label': '25-34', 'pct': 27.4}, {'label': '35-44', 'pct': 28.4}, {'label': '45-54', 'pct': 15.1},
                                 {'label': '55-64', 'pct': 10.6}, {'label': '65+', 'pct': 6.4}],
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


# the record: what the ledger holds today (the Fightland read and the Exiles trackers), in the index's shape
LEDGER = {
    'fightland': {'subject': 'Fightland', 'entries': [{'metrics': [
        {'name': 'fightland_viewers_starz', 'label': 'Fightland US viewers on Starz', 'value': 1418263, 'unit': 'viewers', 'definition': 'distinct US people with at least one Fightland play on Starz, Jul 31 to Oct 29 2026'},
        {'name': 'any_crossover_viewers', 'label': 'Fightland viewers who started S.W.A.T. Exiles', 'value': 324176, 'unit': 'viewers', 'definition': 'Fightland viewers with at least one S.W.A.T. Exiles play in the window, by any route'},
        {'name': 'autoplay_handoff_viewers', 'label': 'Autoplay handoff into S.W.A.T. Exiles', 'value': 73118, 'unit': 'viewers', 'definition': 'people whose next Starz play after a Fightland episode was S.W.A.T. Exiles'}]}]},
    'swat_exiles_season_1_to_date': {'subject': 'SWAT Exiles - Season 1 to date', 'entries': [{'metrics': [
        {'name': 'US viewers in window', 'value': 1872084, 'unit': 'people', 'definition': 'US people who watched S.W.A.T. Exiles Season 1 on Starz, Sep 25 to Oct 29, 2026 (episodes 1 to 5)'},
        {'name': 'Starz signups attributed', 'value': 49782, 'unit': 'accounts', 'definition': 'Starz accounts opened or reopened after a S.W.A.T. Exiles touchpoint'}]}]},
    's_w_a_t_exiles_starz_season_1_opening_window': {'subject': 'S.W.A.T. Exiles (Starz, Season 1) opening window', 'entries': [{'metrics': [
        {'name': 'US viewers in window', 'value': 474957, 'unit': 'people', 'definition': 'US people who watched the S.W.A.T. Exiles premiere on Starz, Sep 25 to Sep 28, 2026'}]}]},
    # the overlap read the live record turned out to hold (banked earlier the same day): this audience's own people on the title
    'p_valley_viewers_and_w_a_t_exiles_overlap': {'subject': 'P-Valley Viewers and S.W.A.T. Exiles overlap on Starz', 'entries': [{'metrics': [
        {'name': 'shared_audience', 'label': 'Shared audience, P-Valley and S.W.A.T. Exiles', 'value': 679241, 'unit': 'viewers', 'definition': 'distinct US people with at least one play of P-Valley in the trailing 12 months and at least one play of S.W.A.T. Exiles Season 1 on Starz'},
        {'name': 'avid_overlap', 'label': 'Avid P-Valley fans in the overlap', 'value': 355831, 'unit': 'viewers', 'definition': 'people inside the Avid P-Valley tier who also watched S.W.A.T. Exiles Season 1'},
        {'name': 'combined_unique', 'label': 'Combined unique reach of the two titles', 'value': 7287185, 'unit': 'viewers', 'definition': 'distinct US people who watched either title'}]}]},
    'fightland_x_exiles': {'subject': 'Fightland viewers and S.W.A.T. Exiles overlap on Starz', 'entries': [{'metrics': [
        {'name': 'shared', 'label': 'Shared audience, Fightland and S.W.A.T. Exiles', 'value': 324176, 'unit': 'viewers', 'definition': 'distinct US people with at least one play of Fightland and at least one play of S.W.A.T. Exiles on Starz'}]}]},
    'pvalley_on_starz_viewers': {'subject': 'PValley on Starz Viewers', 'entries': [{'metrics': [
        {'name': 'twin audience, US people', 'value': 6094342.0, 'unit': 'people', 'definition': 'people in the US in the twin'}]}]},
    'texas_registered_voters': {'subject': 'Texas registered voters', 'entries': [{'metrics': [{'name': 'registered', 'value': 18623931, 'unit': 'people', 'definition': 'on the rolls'}]}]},
}

# the fake web: what a search returns (the study desk's fixtures)
SEARCH = {
    'S.W.A.T. Exiles Starz premiere schedule episodes': 'S.W.A.T. Exiles premiered Friday Sept 25, 2026 on Starz; ten episodes, new ones Fridays through Nov 27.',
    'S.W.A.T. Exiles viewership streaming chart ranking': 'No measured audience figure published; the show has not appeared on the weekly streaming top ten.',
    'S.W.A.T. Exiles reviews reception renewal': 'Mixed reviews, user ratings near 5.5 of 10; renewed for a second season before the premiere.',
    'P-Valley season 3 premiere date audience Starz': 'P-Valley returns December 4, 2026 for its final season; the audience leans Black women 25 to 44.',
    'P-Valley S.W.A.T. Exiles Starz cross promotion same night': 'Starz runs Exiles promos ahead of its Friday dramas; no shared cast or creators; Shemar Moore has a large following among Black women.',
    'Starz US subscribers 2026': 'Starz reported 12.7 million domestic streaming subscribers at the end of 2025.',
}

STUDY = {
    'asked': {'name': 'S.W.A.T. Exiles', 'kind': 'title', 'what': 'A ten-episode Starz spinoff of S.W.A.T. with Shemar Moore as Hondo.', 'platform': 'Starz',
              'premiere_date': '2026-09-25', 'schedule': 'Fridays', 'installments_total': 10, 'installments_out_by_today': 6, 'status': 'running',
              'reach': [{'figure': 'US viewers through episode five on the record', 'value': 1872084, 'unit': 'people', 'as_of': '2026-10-29', 'source_class': 'platform published'}],
              'reception': ['User ratings sit near 5.5 of 10; reviews are mixed.'], 'renewal': 'Renewed for a second season before the premiere',
              'social': {'volume': 'low', 'tone': 'Quiet; the talk is about Shemar Moore, not the plot.'}},
    'audience_subject': {'name': 'P-Valley', 'status': 'Last ran in 2022; the final season lands December 4, 2026.',
                         'size': [{'figure': 'trailing-year viewers', 'value': 6094342, 'unit': 'people', 'as_of': '2026-11-05', 'source_class': 'platform published'}],
                         'demographics_read': 'Mostly Black women aged 25 to 44.', 'where_viewers_sit': 'Between seasons; many hold Starz only around a P-Valley run.'},
    'overlap': [{'finding': 'No shared cast or creators between the two shows.', 'direction': 'against', 'strength': 'moderate'},
                {'finding': 'Shemar Moore has a large following among Black women, the core of the P-Valley audience.', 'direction': 'toward', 'strength': 'moderate'},
                {'finding': 'Starz promotes Exiles ahead of its Friday dramas; P-Valley is not on the air this fall.', 'direction': 'neutral', 'strength': 'weak'}],
    'platform': {'name': 'Starz', 'us_subscribers': {'value': 12700000, 'as_of': '2025-12-31', 'source_class': 'earnings'}, 'notes': 'Power Book III and Fightland are the fall slate.'},
    'figures': [{'fact': 'Starz reported 12.7 million domestic streaming subscribers at the end of 2025.', 'value': 12700000, 'unit': 'accounts', 'as_of': '2025-12-31', 'source_class': 'earnings', 'about': 'platform'},
                {'fact': 'Ten episodes, six out by November 5, new ones Fridays through November 27.', 'value': 10, 'unit': 'episodes', 'as_of': '2026-11-05', 'source_class': 'trade press', 'about': 'asked'}],
    'facts_for_the_room': ['S.W.A.T. Exiles runs Fridays on Starz through its November 27 finale, with six of ten episodes out.', 'P-Valley returns December 4 for its final season.',
                           'According to Nielsen, no research figure is published for the spinoff.'],
    'searches_run': list(SEARCH.keys()),
    'not_found': ['Any measured audience figure for S.W.A.T. Exiles'],
}

ANALYST = {
    'asked': 'S.W.A.T. Exiles', 'audience_people': PEOPLE,
    'ceilings': {'reach': {'low_people': 1900000, 'high_people': 2400000, 'basis': 'the record counts 1.87M viewers through episode five; a sixth episode adds a little'},
                 'current_reach': {'low_people': 500000, 'high_people': 800000, 'basis': 'the premiere window counted 475K in four days; a weekly audience sits near that, with catch-up on top'},
                 'parent': {'reach_people': 7500000, 'audience_clears_it': True, 'basis': 'P-Valley reaches more people than the trailing-year twin'},
                 'neighbor': {'name': 'Fightland viewers', 'rate_pct': 22.9, 'people': 324176, 'relation': 'less adjacent', 'reason': 'Fightland ran the same Friday nights with autoplay into Exiles; P-Valley is between seasons'},
                 'share': {'platform_people': 22860000, 'audience_share_of_platform_pct': 26.7, 'placement': 'under', 'reason': 'a lapsed, genre-distant audience holds less of the Exiles audience than of Starz'}},
    'options': [{'id': 'a', 'low_pct': 2.0, 'high_pct': 4.5, 'basis': 'a minority of the weekly audience is P-Valley viewers'},
                {'id': 'b', 'low_pct': 3.0, 'high_pct': 7.0, 'basis': 'sampled and dropped'},
                {'id': 'c', 'low_pct': 55.0, 'high_pct': 70.0, 'basis': 'parked until December'},
                {'id': 'd', 'low_pct': 8.0, 'high_pct': 15.0, 'basis': 'a cop show is not the draw'},
                {'id': 'e', 'low_pct': 10.0, 'high_pct': 18.0, 'basis': 'lapsed between seasons'}],
    'engaged_option_ids': ['a', 'b'], 'current_option_ids': ['a'], 'lead_option_id': 'a',
    'note': 'Derived from the premiere window and the Fightland rate; a strong P-Valley promo week would move it.',
}

SEEN = {'study': [], 'anchor': [], 'media': [], 'persona': [], 'mirror': [], 'read': [], 'options': []}


def fake_search(query: str) -> str:
    return SEARCH.get(query, '')


def fake_model(role, system, user, **kw):
    d = json.loads(user)
    if role == 'study':
        SEEN['study'].append(d)
        if d.get('retry'):
            return None
        # the desk "runs" its searches against the fixtures and answers from them
        ran = [q for q in SEARCH if fake_search(q)]
        out = copy.deepcopy(STUDY)
        out['searches_run'] = ran
        return out
    if role == 'anchor':
        SEEN['anchor'].append(d)
        return copy.deepcopy(ANALYST)
    if role == 'research':
        return {'subject_status': {'line': 'S.W.A.T. Exiles is a ten-episode Starz spinoff, mid-run on Friday nights.'}, 'facts': [], 'not_found': [], 'implications': []}
    if role == 'media':
        SEEN['media'].append(d)
        return {'event': {'what': 'S.W.A.T. Exiles weekly run', 'date': '2026-09-25', 'status': 'happened', 'outcome': 'The spinoff is mid-run on Friday nights.', 'results': [], 'turnout_pct': None},
                'polls': [], 'coverage': [{'line': 'The spinoff drew a soft reception at launch.', 'reach': 'common', 'local': False, 'days_ago': 9}],
                'talk': [{'line': 'I came for Uncle Clifford and the Pynk, not a cop show', 'who': 'longtime P-Valley fans', 'loudness': 'dominant', 'feeling': 'dismissive'}],
                'numbers': [], 'local': [], 'not_found': []}
    if role == 'moderator' and 'supplied_options' in d:
        SEEN['options'].append(d)
        return {'options': [{'id': k, 'label': LABELS[k], 'lean': LEANS[k]} for k in 'abcde'], 'framing': 'S.W.A.T. Exiles runs Fridays on Starz. As of right now, are you watching it?', 'listen_for': ['genre']}
    if role == 'moderator':
        SEEN['read'].append(d)
        return {'read': 'According to Nielsen, about one in thirty of these viewers are keeping up weekly, calibrated to the published figures. Almost two in three have parked it for later. '
                        'The loudest line was about genre: they came for the Pynk, not a cop show. The show runs Fridays through its November 27 finale and P-Valley is back December 4.',
                'chat': 'About one in thirty are keeping up weekly, per the trade press. Most have parked it for December.',
                'voices': [1, 2, 3], 'probes_next': ['Why?'], 'facts_used': ['The show runs Fridays through November 27, per Variety.', 'P-Valley returns December 4.']}
    if role == 'persona' and 'your_answer' in d:
        SEEN['mirror'].append(d)
        return {'line': f"That is me: {d['your_answer']['label']}. I am holding Starz for December.", 'confidence': 4, 'reason': 'December'}
    if role == 'persona':
        SEEN['persona'].append(d)
        return {'option': 'a', 'line': 'Shemar Moore on a Friday is an easy yes for me.', 'confidence': 5, 'reason': 'Shemar'}
    return None


class FakeS3:
    def __init__(self):
        self.docs = {}

    def get_object(self, Bucket, Key):
        if Key not in self.docs:
            raise KeyError(Key)
        body = self.docs[Key]
        return {'Body': type('B', (), {'read': lambda s: body})()}

    def put_object(self, Bucket, Key, Body, **kw):
        self.docs[Key] = Body


def _reset():
    for k in SEEN:
        SEEN[k].clear()
    ROOM._STUDY_MEM.clear()
    ROOM.set_ledger_fn(lambda: copy.deepcopy(LEDGER))


class Keys(unittest.TestCase):
    def test_the_thing_asked_about_keys_the_study(self):
        subj = 'PValley on Starz Viewers'
        self.assertEqual(ROOM.asked_tokens('are you currently watching swat exiles?', subj), {'swat', 'exil'})   # stemmed, as topic_tokens stems
        self.assertEqual(ROOM.asked_tokens('did you catch swat exiles last friday?', subj), {'swat', 'exil'})
        self.assertEqual(ROOM.study_key(subj, 'are you currently watching swat exiles?', '2026-11-05'), ROOM.study_key(subj, 'did you catch swat exiles last friday?', '2026-11-05'))
        self.assertNotEqual(ROOM.study_key(subj, 'are you currently watching swat exiles?', '2026-11-05'), ROOM.study_key(subj, 'are you currently watching swat exiles?', '2026-11-06'), 'a new day is a new study')
        self.assertNotEqual(ROOM.study_key(subj, 'are you watching swat exiles?', '2026-11-05'), ROOM.study_key(subj, 'are you watching fightland?', '2026-11-05'))
        # a question with no named thing keys on its topic, never on an empty string
        self.assertEqual(ROOM.asked_tokens('would you renew next month?', subj), set())
        self.assertTrue(ROOM.study_key(subj, 'would you renew next month?', '2026-11-05').endswith('.json'))
        self.assertEqual(ROOM._asked_words_raw('are you currently watching swat exiles?', subj), ['swat', 'exiles'])
        qs = ROOM.narrower_queries('are you currently watching swat exiles?', subj, {'platform_home': 'Starz app', 'name': 'PValley on Starz Viewers'})
        self.assertGreaterEqual(len(qs), ROOM.STUDY_MIN_SEARCHES)
        self.assertEqual(qs[0], 'swat exiles Starz app')
        self.assertTrue(all('swat exiles' in x or 'PValley' in x or 'Starz' in x for x in qs))


class Ledger(unittest.TestCase):
    def test_the_record_is_found_and_the_asked_title_count_is_the_reach_ceiling(self):
        _reset()
        rows = ROOM.ledger_neighbors(ROOM.asked_tokens('are you currently watching swat exiles?', 'PValley on Starz Viewers'), 'PValley on Starz Viewers')
        metrics = {r['metric'] for r in rows}
        self.assertIn('Fightland viewers who started S.W.A.T. Exiles', metrics, 'the neighbor read is on the record')
        self.assertIn('US viewers in window', metrics, "the asked title's own count is on the record")
        self.assertIn('twin audience, US people', metrics, 'the audience itself')
        self.assertNotIn('registered', metrics, 'an unrelated subject stays out')
        self.assertTrue(all(r['hit'] == 'asked' for r in rows if 'Exiles' in r['definition'] or 'Exiles' in r['subject']))
        self.assertEqual(ROOM.record_reach_people(rows), 1872084, "the asked title's largest counted viewers, never a neighbor's count, a signup count or the combined reach of two titles")
        self.assertEqual(ROOM.record_overlap_people(rows, 'PValley on Starz Viewers'), 679241, "this audience's own counted people on the title, never the Fightland overlap or the avid slice")
        self.assertEqual(ROOM.record_overlap_people(rows, 'Fightland viewers'), 324176)
        self.assertEqual(ROOM.record_overlap_people(rows, 'Starz'), None, 'the platform name alone never claims an overlap read')
        self.assertEqual(ROOM.record_overlap_people(rows, 'Outlander viewers'), None)
        # a crowded record (the live index carries a dozen Exiles tracker rows under 'Starz'): the neighbor's rate rows and the
        # title's own counts come first, so the analyst always sees them inside the limit
        crowded = copy.deepcopy(LEDGER)
        crowded['starz'] = {'subject': 'Starz', 'entries': [{'metrics': [{'name': f'S.W.A.T. Exiles tracker row {i}', 'value': i, 'unit': 'pct', 'definition': 'S.W.A.T. Exiles season read'} for i in range(30)]}]}
        ROOM.set_ledger_fn(lambda: crowded)
        rows2 = ROOM.ledger_neighbors(ROOM.asked_tokens('are you currently watching swat exiles?', 'PValley on Starz Viewers'), 'PValley on Starz Viewers', limit=12)
        metrics2 = [r['metric'] for r in rows2]
        self.assertIn('Fightland viewers who started S.W.A.T. Exiles', metrics2[:4])
        self.assertIn('Autoplay handoff into S.W.A.T. Exiles', metrics2[:4])
        self.assertIn('US viewers in window', metrics2[:6])
        self.assertLessEqual(len(rows2), 16)
        self.assertEqual(ROOM.record_reach_people([]), None)
        ROOM.set_ledger_fn(None)
        self.assertEqual(ROOM.ledger_neighbors({'swat', 'exil'}, 'x'), [], 'REGRESSION_TEST_MODE without a fake never touches the ledger')


class Derived(unittest.TestCase):
    OPTS = [{'id': k, 'label': LABELS[k], 'lean': LEANS[k]} for k in 'abcde']

    def test_engaged_rows_sit_in_the_bottom_third_and_the_rest_fill(self):
        plan = ROOM.derived_plan(self.OPTS, copy.deepcopy(ANALYST), PEOPLE, record_reach=1872084, seed='s')
        self.assertEqual(plan['kind'], 'derived')
        t = plan['targets']
        self.assertAlmostEqual(t['a'], 2.0 + 2.5 / 3.0, places=1, msg='the lead engaged row sits in the bottom third of its range')
        self.assertAlmostEqual(t['b'], 3.0 + 4.0 / 3.0, places=1)
        self.assertAlmostEqual(sum(t.values()), 100.0, places=1)
        self.assertGreater(t['c'], t['d']); self.assertGreater(t['e'], t['d'])
        self.assertEqual(plan['down'], ['a', 'b'], 'the engaged rows jitter downward only')
        self.assertEqual(plan['lead'], 'a')
        self.assertFalse(any(c['applied'] for c in plan['basis']['ceilings']), 'no ceiling binds on this answer')
        self.assertEqual({c['ceiling'] for c in plan['basis']['ceilings']}, {'current reach', 'share of current audience', 'reach', 'share of audience', 'neighbor'})
        self.assertEqual(plan['basis']['record_reach_people'], 1872084)
        self.assertIsNone(plan['basis']['record_overlap_people'])
        self.assertEqual(ROOM.has_banned(plan['line']), [], 'the line feeds the read; it carries no source language')

    def test_the_ceilings_bind(self):
        a = copy.deepcopy(ANALYST)
        a['options'][0].update(low_pct=10.0, high_pct=20.0)      # keeping up weekly claimed at 13.3%: 812K people against a weekly reach of 520K
        a['options'][1].update(low_pct=30.0, high_pct=40.0)      # started: 33.3%
        plan = ROOM.derived_plan(self.OPTS, a, PEOPLE, record_reach=1872084, seed='s')
        t = plan['targets']
        cur_cap = 800000 / PEOPLE * 100.0
        self.assertLessEqual(t['a'], cur_cap + 0.01, 'the current row never exceeds the current reach')
        self.assertLessEqual(t['a'], 800000 * 0.267 * ROOM.ANCHOR_NEIGHBOR_MARGIN / PEOPLE * 100.0 + 0.01, "nor this audience's share of the current audience")
        reach_cap = 1872084 / PEOPLE * 100.0
        self.assertLessEqual(t['a'] + t['b'], reach_cap + 0.01, "the engaged rows never exceed the record's own count of the title's viewers")
        self.assertLessEqual(t['a'] + t['b'], 22.9 * ROOM.ANCHOR_NEIGHBOR_MARGIN + 0.01, 'a less adjacent audience lands under the Fightland rate')
        applied = {c['ceiling']: c for c in plan['basis']['ceilings'] if c['applied']}
        self.assertEqual(set(applied), {'current reach', 'share of current audience', 'reach', 'share of audience'}, 'the share ceiling is the tight one here; the neighbor is listed, not binding')
        self.assertLessEqual(t['a'] + t['b'], 1872084 * 0.267 * ROOM.ANCHOR_NEIGHBOR_MARGIN / PEOPLE * 100.0 + 0.01)
        self.assertTrue(any(c['ceiling'] == 'neighbor' and not c['applied'] for c in plan['basis']['ceilings']))
        self.assertIn("record's own count of 1,872,084", applied['reach']['basis'])
        # with no share placement the neighbor's rate is the ceiling that binds
        a_ns = copy.deepcopy(a); a_ns['ceilings'].pop('share')
        plan_ns = ROOM.derived_plan(self.OPTS, a_ns, PEOPLE, record_reach=1872084, seed='s')
        self.assertTrue(any(c['ceiling'] == 'neighbor' and c['applied'] for c in plan_ns['basis']['ceilings']))
        self.assertAlmostEqual(plan_ns['targets']['a'] + plan_ns['targets']['b'], 22.9 * ROOM.ANCHOR_NEIGHBOR_MARGIN, places=1)
        self.assertAlmostEqual(sum(t.values()), 100.0, places=1)
        # the analyst's reach high is the cap when the record holds no count, and the lower of the two when it does
        plan2 = ROOM.derived_plan(self.OPTS, copy.deepcopy(a), PEOPLE, record_reach=None, seed='s')
        self.assertLessEqual(plan2['targets']['a'] + plan2['targets']['b'], 2400000 / PEOPLE * 100.0 + 0.01)
        plan3 = ROOM.derived_plan(self.OPTS, copy.deepcopy(a), PEOPLE, record_reach=900000, seed='s')
        self.assertLessEqual(plan3['targets']['a'] + plan3['targets']['b'], 900000 / PEOPLE * 100.0 + 0.01)

    def test_a_more_adjacent_audience_may_sit_above_the_neighbor_for_a_stated_reason(self):
        a = copy.deepcopy(ANALYST)
        a['options'][1].update(low_pct=20.0, high_pct=26.0)
        a['ceilings']['neighbor'].update(relation='more adjacent', reason='the same Friday slot and a shared lead')
        a['ceilings']['share'].update(placement='over', reason='the shared lead carries this audience past its platform share')
        plan = ROOM.derived_plan(self.OPTS, a, PEOPLE, record_reach=None, seed='s')
        self.assertGreater(plan['targets']['a'] + plan['targets']['b'], 22.9)
        nb = next(c for c in plan['basis']['ceilings'] if c['ceiling'] == 'neighbor')
        self.assertFalse(nb['applied']); self.assertIn('stated reason', nb['basis'])
        # the same claim with no reason is held under the neighbor
        a2 = copy.deepcopy(a); a2['ceilings']['neighbor']['reason'] = ''
        plan2 = ROOM.derived_plan(self.OPTS, a2, PEOPLE, record_reach=None, seed='s')
        self.assertLessEqual(plan2['targets']['a'] + plan2['targets']['b'], 22.9 * ROOM.ANCHOR_NEIGHBOR_MARGIN + 0.01)

    def test_the_share_of_the_things_audience_binds_the_current_row(self):
        # the live first run: 'watching as the episodes drop' at 4.3% was 264K people, over half of a weekly Exiles audience, for an
        # audience that is about a quarter of Starz's people and less adjacent than its average viewer
        a = copy.deepcopy(ANALYST)
        a['options'][0].update(low_pct=3.0, high_pct=7.0)
        a['ceilings']['current_reach'].update(low_people=380000, high_people=475000)
        plan = ROOM.derived_plan(self.OPTS, a, PEOPLE, record_reach=1872084, seed='s')
        cap = 475000 * 0.267 * ROOM.ANCHOR_NEIGHBOR_MARGIN / PEOPLE * 100.0
        self.assertLessEqual(plan['targets']['a'], cap + 0.01)
        self.assertLess(plan['targets']['a'], 2.0)
        sc = next(c for c in plan['basis']['ceilings'] if c['ceiling'] == 'share of current audience')
        self.assertTrue(sc['applied']); self.assertIn('26.7% of the platform', sc['basis'])
        # an even audience holds its platform share; an over one, for a stated reason, is not capped by the share at all
        a2 = copy.deepcopy(a); a2['ceilings']['share'].update(placement='even')
        self.assertAlmostEqual(ROOM.derived_plan(self.OPTS, a2, PEOPLE, seed='s')['targets']['a'], 475000 * 0.267 / PEOPLE * 100.0, places=1)
        a3 = copy.deepcopy(a); a3['ceilings']['share'].update(placement='over', reason='Shemar Moore carries this audience')
        p3 = ROOM.derived_plan(self.OPTS, a3, PEOPLE, seed='s')
        self.assertAlmostEqual(p3['targets']['a'], 3.0 + 4.0 / 3.0, places=1)
        self.assertTrue(any(c['ceiling'] == 'share of audience' and not c['applied'] and 'stated reason' in c['basis'] for c in p3['basis']['ceilings']))
        a4 = copy.deepcopy(a); a4['ceilings'].pop('share')
        self.assertAlmostEqual(ROOM.derived_plan(self.OPTS, a4, PEOPLE, seed='s')['targets']['a'], 3.0 + 4.0 / 3.0, places=1, msg='no share, no share ceiling')

    def test_the_records_own_overlap_count_caps_the_engaged_rows(self):
        a = copy.deepcopy(ANALYST)
        a['options'][1].update(low_pct=12.0, high_pct=16.0)   # started: 13.3%, with the yes row 16.2% engaged against a counted 679,241 (11.1%)
        a['ceilings']['share'].update(placement='over', reason='the counted overlap runs past the platform share')
        plan = ROOM.derived_plan(self.OPTS, a, PEOPLE, record_reach=1872084, seed='s', record_overlap=679241)
        self.assertLessEqual(plan['targets']['a'] + plan['targets']['b'], 679241 / PEOPLE * 100.0 + 0.01)
        ro = next(c for c in plan['basis']['ceilings'] if c['ceiling'] == 'record overlap')
        self.assertTrue(ro['applied']); self.assertIn('679,241', ro['basis'])
        self.assertEqual(plan['basis']['record_overlap_people'], 679241)
        plan2 = ROOM.derived_plan(self.OPTS, copy.deepcopy(ANALYST), PEOPLE, record_reach=1872084, seed='s', record_overlap=679241)
        self.assertFalse(next(c for c in plan2['basis']['ceilings'] if c['ceiling'] == 'record overlap')['applied'], 'under it, nothing moves')

    def test_an_answer_that_cannot_place_the_board_is_no_plan(self):
        self.assertEqual(ROOM.derived_plan(self.OPTS, None, PEOPLE), {})
        self.assertEqual(ROOM.derived_plan(self.OPTS, {'options': [{'id': 'a', 'low_pct': 1, 'high_pct': 2}]}, PEOPLE), {})
        self.assertEqual(ROOM.derived_plan(self.OPTS, copy.deepcopy(ANALYST), 0), {})
        # the engaged rows fall back to the leans and the wording when the analyst did not name them
        a = copy.deepcopy(ANALYST); a.pop('engaged_option_ids'); a.pop('current_option_ids'); a.pop('lead_option_id')
        plan = ROOM.derived_plan(self.OPTS, a, PEOPLE)
        self.assertEqual(plan['basis']['engaged'], [LABELS['a'], LABELS['b']])
        self.assertEqual(plan['basis']['current'], [LABELS['a']])
        self.assertEqual(plan['lead'], 'a')


class Study(unittest.TestCase):
    def test_the_answer_is_shaped_and_the_facts_for_the_room_are_scrubbed(self):
        s = ROOM._norm_study(copy.deepcopy(STUDY))
        self.assertEqual(s['asked']['name'], 'S.W.A.T. Exiles')
        self.assertEqual(s['asked']['installments_out_by_today'], 6)
        self.assertEqual(len(s['searches_run']), 6)
        self.assertEqual(ROOM.has_banned(' '.join(s['facts_for_the_room'])), [], 'a fact for the room never names a source')
        self.assertTrue(any('November 27' in f for f in s['facts_for_the_room']))
        self.assertTrue(ROOM.study_ok(s))
        self.assertEqual(ROOM._norm_study({'searches_run': ['x'], 'not_found': ['y']}), {}, 'a pass with nothing on the thing asked about is empty')
        self.assertFalse(ROOM.study_ok({'_failed': True}))
        brief = ROOM.merge_study({'subject_status': {'line': 'x'}, 'facts': [], 'not_found': [], 'implications': []}, s)
        self.assertEqual(brief['study']['asked']['name'], 'S.W.A.T. Exiles')
        self.assertTrue(any(f.get('topic') == 'study' for f in brief['facts']))
        self.assertIn('S.W.A.T. Exiles runs Fridays on Starz through its November 27 finale, with six of ten episodes out.', ROOM.facts_lines(brief))

    def test_the_s3_cache_holds_a_study_for_the_day(self):
        _reset()
        s3 = FakeS3()
        M = ROOM.Models(model_fn=fake_model)
        tw = _pvalley()
        s1 = ROOM.research_pass(M, tw, 'are you currently watching swat exiles?', 'yes_no', None, s3, neighbors=[])
        self.assertTrue(ROOM.study_ok(s1)); self.assertFalse(s1.get('_cached')); self.assertEqual(s1['_attempts'], 1)
        self.assertEqual(len(SEEN['study']), 1)
        self.assertTrue(any(k.startswith(ROOM.STUDY_PREFIX) for k in s3.docs), 'the study is written under its day key')
        ROOM._STUDY_MEM.clear()   # the process cache is gone (a new worker); the S3 cache answers
        s2 = ROOM.research_pass(M, tw, 'did you catch swat exiles last friday?', 'yes_no', None, s3, neighbors=[])
        self.assertTrue(s2.get('_cached')); self.assertEqual(len(SEEN['study']), 1, 'a cached subject is not re-searched the same day')
        self.assertEqual(s2['asked']['name'], 'S.W.A.T. Exiles')
        s3b = ROOM.research_pass(M, tw, 'are you currently watching swat exiles?', 'yes_no', None, s3, neighbors=[], force=True)
        self.assertFalse(s3b.get('_cached')); self.assertEqual(len(SEEN['study']), 2, 'force re-searches')


class Room(unittest.TestCase):
    def _ask(self, q='are you currently watching swat exiles?', fake=fake_model, sess=None, twin=None):
        twin = twin or _pvalley()
        sess = sess or P.open_session([twin], in_room=12, title='Exiles', session_id='room-study-1')
        rec = ROOM.ask_room(sess, {twin['meta']['key']: twin}, q, s3=None, twin_key='pv', run_async=False, model_fn=fake)
        return sess, rec

    def test_a_title_question_with_no_internal_read_is_researched_before_the_seats_answer(self):
        _reset()
        sess, rec = self._ask()
        self.assertEqual(rec['stage'], 'done')
        self.assertEqual(rec['trail']['fallbacks'], [])
        stages = [c['stage'] for c in rec['trail']['call_log']]
        self.assertIn('study', stages); self.assertIn('anchor', stages); self.assertIn('persona', stages)
        self.assertLess(stages.index('study'), stages.index('persona'), 'the study desk runs before the seats answer')
        self.assertLess(stages.index('study'), stages.index('media'), 'and before the media desk gathers the event brief')
        self.assertLess(stages.index('anchor'), stages.index('read'))
        st = rec['trail']['study']
        self.assertTrue(st['ok']); self.assertFalse(st['cached']); self.assertEqual(st['attempts'], 1)
        self.assertGreaterEqual(st['searches'], ROOM.STUDY_MIN_SEARCHES, 'at least three distinct searches')
        self.assertEqual(st['asked'], 'S.W.A.T. Exiles')
        self.assertTrue(any('Fightland' in r['subject'] for r in st['record_holds']), 'the record rode into the study')
        # the study desk was handed what the record holds, and the media desk the study's facts
        self.assertTrue(any('Exiles' in json.dumps(x) for x in SEEN['study'][0]['the_record_already_holds']))
        self.assertIn('S.W.A.T. Exiles runs Fridays on Starz through its November 27 finale, with six of ten episodes out.', SEEN['media'][0]['what_the_room_already_knows'])
        # the analyst saw the study, the record and the board
        a = SEEN['anchor'][0]
        self.assertEqual(a['study']['asked']['name'], 'S.W.A.T. Exiles')
        self.assertTrue(any(r['metric'] == 'US viewers in window' for r in a['the_record_holds']))
        self.assertEqual([o['id'] for o in a['options']], list('abcde'))
        self.assertEqual(a['audience']['people_us'], PEOPLE)
        # the seats and the options desk had the study's facts
        self.assertTrue(all(any('November 27' in f for f in b['facts_the_moderator_gave_the_room']) for b in SEEN['persona']))
        self.assertIn('study', SEEN['options'][0]['facts_brief'])

    def test_the_count_lands_at_the_anchor_and_the_seats_mirror_it(self):
        _reset()
        sess, rec = self._ask()
        self.assertEqual(rec['trail']['anchor_status'], 'derived')
        self.assertEqual(rec['anchor']['kind'], 'derived')
        by = {o['id']: o for o in rec['options']}
        plan = ROOM.derived_plan([{'id': k, 'label': LABELS[k], 'lean': LEANS[k]} for k in 'abcde'], copy.deepcopy(ANALYST), PEOPLE, record_reach=1872084)
        for k in 'abcde':
            self.assertAlmostEqual(by[k]['pct'], plan['targets'][k], delta=0.7, msg=k)
        self.assertLessEqual(by['a']['pct'], plan['targets']['a'] + 0.05, 'the engaged row jitters downward only')
        self.assertLess(by['a']['pct'], 3.5, 'twelve free yes votes do not move the published count off the derived anchor')
        self.assertEqual({o['id']: o['seats_n'] for o in rec['options']}, ROOM.allocate_seats({o['id']: o['pct'] for o in rec['options']}, 12), 'the seats mirror the count')
        self.assertEqual(by['a']['seats_n'], 0, 'under half a seat of the room, the yes row holds no seat')
        self.assertGreaterEqual(by['c']['seats_n'], 7)
        self.assertEqual(len(rec['trail']['mirror']['moved']), 12)
        self.assertEqual(sum(o['people'] for o in rec['options']), PEOPLE)
        self.assertAlmostEqual(sum(o['pct'] for o in rec['options']), 100.0, places=1)
        self.assertEqual(rec['headline_people'], by['a']['people'], 'the headline people are the share, never the tally')
        self.assertIn('0 of 12', by['a']['tally'])
        tr = rec['trail']['anchor']
        self.assertEqual(tr['status'], 'derived')
        self.assertEqual(tr['basis']['neighbor']['rate_pct'], 22.9)
        self.assertEqual(tr['basis']['record_reach_people'], 1872084)
        self.assertEqual({c['ceiling'] for c in tr['basis']['ceilings']}, {'current reach', 'share of current audience', 'reach', 'share of audience', 'record overlap', 'neighbor'})
        self.assertEqual(tr['basis']['share']['placement'], 'under')
        self.assertEqual(tr['basis']['record_overlap_people'], 679241, "the record's overlap read rode into the room's ceilings")
        self.assertIn(LABELS['a'], rec['anchor']['targets'])
        self.assertIn('the count', rec['trail']['prior']['note'])

    def test_a_cached_subject_is_not_re_searched_the_same_day(self):
        _reset()
        sess, rec1 = self._ask()
        self.assertEqual(len(SEEN['study']), 1)
        sess, rec2 = self._ask('did you catch swat exiles last friday?', sess=sess)
        self.assertEqual(len(SEEN['study']), 1, 'the second question on the same subject rides the first study')
        self.assertTrue(rec2['trail']['study']['cached'])
        self.assertEqual(rec2['trail']['study']['key'], rec1['trail']['study']['key'])
        self.assertEqual(rec2['trail']['anchor_status'], 'derived', 'the anchor is derived again for the new board')
        self.assertEqual(len(SEEN['anchor']), 2)
        sess, rec3 = self._ask('are you watching fightland?', sess=sess)
        self.assertEqual(len(SEEN['study']), 2, 'another subject is its own study')

    def test_a_failed_study_retries_once_then_marks_unresearched(self):
        _reset()

        def fake(role, system, user, **kw):
            if role == 'study':
                SEEN['study'].append(json.loads(user))
                return None
            return fake_model(role, system, user, **kw)
        sess, rec = self._ask(fake=fake)
        self.assertEqual(rec['stage'], 'done')
        self.assertEqual(len(SEEN['study']), 2, 'one retry')
        retry = SEEN['study'][1]['retry']
        self.assertEqual(retry['attempt'], 2)
        self.assertGreaterEqual(len(retry['run_these_searches']), ROOM.STUDY_MIN_SEARCHES)
        self.assertEqual(retry['run_these_searches'][0], 'swat exiles Starz app')
        self.assertEqual(rec['trail']['study']['attempts'], 2); self.assertFalse(rec['trail']['study']['ok'])
        self.assertEqual(rec['trail']['anchor_status'], 'unresearched')
        self.assertNotIn('anchor', rec)
        self.assertEqual(SEEN['anchor'], [], 'no study, nothing to derive from')
        self.assertNotIn('study', rec['trail']['fallbacks'], 'the status is on the trail, not a fallback')
        self.assertTrue(rec['read'] and rec['options'])
        self.assertNotIn('unresearched', rec['read'].lower()); self.assertNotIn('unresearched', (rec.get('read_short') or '').lower())
        # the study landed but the analyst could not place the board: unanchored, and the room still lands

        def fake2(role, system, user, **kw):
            if role == 'anchor':
                SEEN['anchor'].append(json.loads(user))
                return None
            return fake_model(role, system, user, **kw)
        _reset()
        sess, rec = self._ask(fake=fake2)
        self.assertEqual(rec['trail']['anchor_status'], 'unanchored')
        self.assertEqual(len(SEEN['anchor']), 2, 'the analyst is asked twice')
        self.assertNotIn('anchor', rec)
        self.assertTrue(rec['read'])

    def test_a_counted_event_still_wins_and_a_probe_keeps_the_prior(self):
        _reset()

        def fake(role, system, user, **kw):
            if role == 'media':
                m = fake_model(role, system, user, **kw)
                m['event']['results'] = [{'label': 'Keeping up weekly', 'share_pct': 3.0, 'status': 'final'}, {'label': 'Started it, not current', 'share_pct': 6.0, 'status': 'final'},
                                         {'label': 'On my list for later', 'share_pct': 66.0, 'status': 'final'}, {'label': 'Not my kind of show', 'share_pct': 12.0, 'status': 'final'},
                                         {'label': 'Starz off or never heard of it', 'share_pct': 13.0, 'status': 'final'}]
                return m
            return fake_model(role, system, user, **kw)
        sess, rec = self._ask(fake=fake)
        self.assertEqual(rec['trail']['anchor_status'], 'result')
        self.assertEqual(rec['anchor']['kind'], 'result')
        self.assertTrue(rec['trail']['study']['ok'], 'the study still ran')
        # a star rating is not a share of the audience on a thing: the prior stands, marked as such
        _reset()
        sess, rec = self._ask('rate swat exiles one to five stars')
        self.assertEqual(rec['trail']['anchor_status'], 'prior')
        self.assertEqual(SEEN['anchor'], [])

    def test_the_read_carries_no_source_language(self):
        _reset()
        sess, rec = self._ask()
        for field in ('read', 'read_short', 'headline', 'framing'):
            self.assertEqual(ROOM.has_banned(rec.get(field) or ''), [], field)
        self.assertEqual(ROOM.has_banned(' '.join(rec['facts_used'])), [])
        self.assertEqual(ROOM.has_banned(' '.join(rec['option_labels'])), [])
        self.assertEqual(ROOM.has_banned(' '.join(v['line'] for v in rec['voices'])), [])
        self.assertEqual(ROOM.has_banned(' '.join(a['line'] for a in rec['answers'])), [])
        self.assertEqual(ROOM.has_banned('\n'.join(ROOM.chat_lines(sess, rec))), [])
        self.assertIn('November 27', rec['read'], 'what the study found is said as plain fact')
        self.assertNotIn('Nielsen', rec['read']); self.assertNotIn('calibrated', rec['read'].lower()); self.assertNotIn('published', rec['read'].lower())
        self.assertNotIn('Variety', ' '.join(rec['facts_used'])); self.assertNotIn('trade press', (rec.get('read_short') or '').lower())
        low = rec['read'].lower()
        for w in ('research', 'anchored', 'ledger', 'study desk'):
            self.assertNotIn(w, low, w)


if __name__ == '__main__':
    unittest.main(verbosity=1)
