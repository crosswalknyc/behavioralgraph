#!/usr/bin/env python3
"""Pilot Testing cohorts (Jenna, 2026-11-02).

"you need to have the ability to segment the audience (when you are
building it and then in the results) by cohorts ... first select how
many cohorts you want, then name each cohort, then give the composition
of each cohort (from your overall digital twin base) ... then how many
people I want in each cohort (the numbers would be representative of
what % that comprises of the whole twin audience) and then the dial,
line chart, etc would all be split by your cohorts."

Hermetic: REGRESSION_TEST_MODE, a small twin built in the test.

    REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_cohorts.py
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ['REGRESSION_TEST_MODE'] = '1'
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from migration import digital_twin_panel as P      # noqa: E402
from migration import digital_twin_pilot as PI     # noqa: E402
from migration import digital_twin_live as LIVE    # noqa: E402


def _twin():
    demo = {
        'gender': [{'label': 'Male', 'pct': 49.3}, {'label': 'Female', 'pct': 49.1}, {'label': 'Non-Binary', 'pct': 1.6}],
        'age': [{'label': '18-24', 'pct': 22.1}, {'label': '25-34', 'pct': 27.8}, {'label': '35-44', 'pct': 18.6}, {'label': '45-54', 'pct': 12.5},
                {'label': '55-64', 'pct': 10.8}, {'label': '65 or Older', 'pct': 8.2}],
        'ethnicity': [{'label': 'White', 'pct': 55.6}, {'label': 'Hispanic or Latino', 'pct': 18.0}, {'label': 'Black or African American', 'pct': 14.4},
                      {'label': 'Asian', 'pct': 9.5}, {'label': 'Another Race/Ethnicity', 'pct': 2.5}],
        'parental': [{'label': 'No Children', 'pct': 61.1}, {'label': 'Has Children', 'pct': 37.6}, {'label': 'Prefer Not to Say', 'pct': 1.3}],
        'income': [{'label': 'Less than $25,000', 'pct': 15.2}, {'label': '$25,000 - $49,999', 'pct': 21.0}, {'label': '$50,000 - $74,999', 'pct': 18.9},
                   {'label': '$75,000 - $99,999', 'pct': 14.4}, {'label': '$100,000 - $149,999', 'pct': 17.6}, {'label': '$150,000 - $249,999', 'pct': 10.4},
                   {'label': '$250,000 or More', 'pct': 2.5}],
        'education': [{'label': 'High School or Less', 'pct': 30.9}, {'label': 'Some College / Associate Degree', 'pct': 28.1},
                      {'label': 'Bachelors Degree', 'pct': 26.3}, {'label': 'Graduate or Professional Degree', 'pct': 14.7}],
        'relationship': [{'label': 'Married', 'pct': 45.9}, {'label': 'Single', 'pct': 37.5}, {'label': 'Divorced or Separated', 'pct': 8.7}, {'label': 'In a Relationship', 'pct': 7.9}],
        'sexual_orientation': [{'label': 'Straight / Heterosexual', 'pct': 85.8}, {'label': 'LGBTQ+', 'pct': 12.3}, {'label': 'Prefer Not to Say', 'pct': 1.9}],
        'occupation': [{'label': 'Management, Business & Professional', 'pct': 60.0}, {'label': 'Service & Hospitality', 'pct': 40.0}],
    }
    return {'meta': {'key': 'digital-twins/t/paramount.json.gz', 'name': 'Paramount+', 'subject': 'Paramount+', 'size_us': 83_791_763},
            'identity': {'demographics': demo, 'geography': [{'label': 'New York NY', 'pct': 13.2}, {'label': 'Los Angeles CA', 'pct': 10.1}]}}


COHORTS = [{'name': 'Men', 'composition': 'men', 'seats': 4}, {'name': 'Women', 'composition': 'women', 'seats': 4},
           {'name': 'Dads', 'composition': 'men with children', 'seats': 3}, {'name': 'Spanish speakers', 'composition': 'Spanish speakers', 'seats': 3}]


class Vocabulary(unittest.TestCase):
    def test_the_examples_parse(self):
        self.assertEqual(P.parse_segment('men'), {'gender': 'M'})
        self.assertEqual(P.parse_segment('women'), {'gender': 'F'})
        self.assertEqual(P.parse_segment('men with children'), {'gender': 'M', 'parent': True})
        self.assertEqual(P.parse_segment('Spanish speakers'), {'ethnicity': 'hispanic', 'language': 'Spanish'})
        self.assertEqual(P.parse_segment('moms 25 to 44'), {'gender': 'F', 'age': (25, 44), 'parent': True})
        self.assertEqual(P.parse_segment('college grads $100k+'), {'income_min': 100000, 'college': True})
        self.assertEqual(P.parse_segment('households under $50k'), {'income_max': 50000})
        self.assertEqual(P.parse_segment('single women'), {'gender': 'F', 'relationship': 'single'})
        self.assertEqual(P.parse_segment('LGBTQ+ viewers'), {'lgbtq': True})
        self.assertEqual(P.parse_segment('suburban parents'), {'parent': True, 'urbanicity': 'suburban'})
        self.assertEqual(P.parse_segment('Republicans in Texas'), {'city': 'Texas', 'party': 'republican'})
        self.assertEqual(P.parse_segment(P.strip_twin_words('Gilmore Girls Hulu viewers', {'meta': {'name': 'Gilmore Girls'}})), {})   # the twin's own words are not a filter

    def test_shares_come_from_the_twins_own_rows(self):
        t = _twin()
        self.assertAlmostEqual(P._segment_share(t, {'gender': 'M'}), 0.493, places=3)
        self.assertAlmostEqual(P._segment_share(t, {'gender': 'M', 'parent': True}), 0.493 * 0.376, places=3)
        self.assertAlmostEqual(P._segment_share(t, {'ethnicity': 'hispanic', 'language': 'Spanish'}), 0.18 * P.SPANISH_AT_HOME, places=3)
        self.assertAlmostEqual(P._segment_share(t, {'income_min': 100000}), 0.305, places=3)
        self.assertAlmostEqual(P._segment_share(t, {'college': True}), 0.41, places=3)
        self.assertAlmostEqual(P._segment_share(t, {'lgbtq': True}), 0.123, places=3)
        self.assertAlmostEqual(P._segment_share(t, {'party': 'democrat'}), 0.33, places=3)   # no party rows: the fallback

    def test_filter_words(self):
        self.assertEqual(P.cohort_filter_words({'gender': 'M', 'parent': True}), 'men, with children at home')
        self.assertEqual(P.cohort_filter_words({'ethnicity': 'hispanic', 'language': 'Spanish'}), 'who speak Spanish at home')


class Room(unittest.TestCase):
    def test_cohorts_seat_as_asked_and_stand_for_their_share_of_the_twin(self):
        t = _twin()
        g = P.build_group([t], segments=PI.cohort_segments(COHORTS, t['meta']['key']), in_room=12, seed='t|cohorts')
        self.assertEqual(g['in_room'], 14)                               # the seats asked for, not the field above them
        # the room stands for the people inside its cuts as one union (Jenna 2026-11-05): men and women together are
        # 99% of this twin's rows, dads sit inside men and Spanish speakers inside both; never the whole twin, never
        # the cuts added up
        self.assertEqual(g['size_people'], 82_984_783)
        self.assertEqual(g['twin_people'], 83_791_763)
        self.assertLess(g['size_people'], 83_791_763)
        self.assertLess(g['size_people'], sum(s['twin_people'] for s in g['segments']))
        self.assertEqual(sum(s['people'] for s in g['segments']), g['size_people'])      # the segments partition what the room stands for
        self.assertEqual(g['sizing']['people'], g['size_people'])
        self.assertTrue(g['sizing']['is_cut'])
        self.assertEqual(g['sizing']['line'], 'Standing for the 4 groups you chose: 83.0M of the 83.8M people in this audience.')
        segs = {s['label']: s for s in g['segments']}
        self.assertEqual([s['seats'] for s in g['segments']], [4, 4, 3, 3])
        self.assertAlmostEqual(segs['Men']['share_pct'], 49.3, delta=0.2)             # share of the whole twin, not of the sum
        self.assertAlmostEqual(segs['Dads']['share_pct'], 18.5, delta=0.3)
        self.assertAlmostEqual(segs['Spanish speakers']['share_pct'], 13.0, delta=0.3)
        self.assertEqual(segs['Dads']['composition'], 'men with children')
        for s in g['segments']:
            self.assertTrue(s['people'] % 10 != 0, s)
        by = {}
        for r in g['roster']:
            by.setdefault(r['segment'], []).append(r)
        self.assertTrue(all(r['gender'] == 'M' and r['parental'] == 'Has Children' for r in by['Dads']))
        self.assertTrue(all(r['ethnicity'] == 'Hispanic or Latino' and r.get('language') == 'Spanish' for r in by['Spanish speakers']))
        self.assertTrue(all(r['gender'] == 'F' for r in by['Women']))
        self.assertTrue(all(r.get('cohort') == r['segment'] for r in g['roster']))
        self.assertEqual(len({r['name'] for r in g['roster']}), 14)

    def test_a_plain_room_is_unchanged(self):
        t = _twin()
        g = P.build_group([t], segments=[{'label': 'Paramount+'}], in_room=12, seed='x')
        self.assertEqual(g['in_room'], 12)
        self.assertEqual([(s['label'], s['people'], s['share_pct']) for s in g['segments']], [('Paramount+', 83_791_763, 100.0)])
        self.assertFalse(any(r.get('cohort') for r in g['roster']))

    def test_many_cohorts_fit_the_room(self):
        # Custom on the picker (2026-11-03): up to twenty cohorts; the seats scale down to the room's cap, at least one each
        t = _twin()
        many = [{'name': f'Cohort {i + 1}', 'composition': ('men' if i % 2 else 'women'), 'seats': 3} for i in range(14)]
        segs = PI.cohort_segments(many, t['meta']['key'])
        self.assertEqual(len(segs), 14)
        g = P.build_group([t], segments=segs, in_room=12, seed='t|many')
        self.assertLessEqual(g['in_room'], P.MAX_IN_ROOM)
        self.assertEqual(len(g['segments']), 14)
        self.assertTrue(all(sg['seats'] >= 1 for sg in g['segments']))
        self.assertEqual(len(PI.cohort_segments([{'name': f'C{i}', 'seats': 1} for i in range(30)], 'k')), PI.MAX_COHORTS)

    def test_cohort_segments_needs_two(self):
        self.assertEqual(PI.cohort_segments([{'name': 'Men', 'composition': 'men', 'seats': 4}], 'k'), [])
        self.assertEqual(PI.cohort_segments(None, 'k'), [])
        segs = PI.cohort_segments([{'name': 'Men', 'seats': '5'}, {'name': 'Women', 'composition': 'women', 'panelists': 99}], 'k')
        self.assertEqual([(s['label'], s['composition'], s['seats']) for s in segs], [('Men', 'Men', 5), ('Women', 'women', 24)])

    def test_the_session_carries_its_cohorts(self):
        t = _twin()
        sess = PI.open_session([t], segments=PI.cohort_segments(COHORTS, t['meta']['key']), in_room=12, title='Season 2 pilot', session_id='c0h0rt5')
        self.assertEqual([c['label'] for c in sess['cohorts']], ['Men', 'Women', 'Dads', 'Spanish speakers'])
        self.assertEqual(sess['cohorts'][2]['composition'], 'men with children')
        self.assertEqual(sess['cohorts'][2]['seats'], 3)
        pub = PI.public_session(sess)
        self.assertEqual(len(pub['cohorts']), 4)


class Results(unittest.TestCase):
    def _sess(self):
        t = _twin()
        return t, PI.open_session([t], segments=PI.cohort_segments(COHORTS, t['meta']['key']), in_room=12, title='x', session_id='c0h0rt5')

    def test_splits_lead_with_the_cohorts(self):
        t, sess = self._sess()
        specs = PI.split_specs(sess['group']['roster'], t)
        self.assertEqual([k for k, _l, g, _p in specs if g == 'cohort'], ['cohort_men', 'cohort_women', 'cohort_dads', 'cohort_spanish_speakers'])
        self.assertEqual(specs[0][2], 'cohort')
        pred = dict((k, p) for k, _l, _g, p in specs)['cohort_dads']
        self.assertEqual(sum(1 for r in sess['group']['roster'] if pred(r)), 3)
        self.assertEqual(PI.cohort_labels([{'segment': 'Paramount+'}] * 12), [])

    def test_the_live_dial_splits_by_cohort(self):
        t, sess = self._sess()
        sess['media'] = {'title': 'NCIS: New York', 'runtime_s': 2580}
        LIVE.seat_live(sess, {t['meta']['key']: t})
        LIVE.start_live(sess)
        import base64
        frame = base64.b64encode(b'\xff\xd8' + b'x' * 600).decode('ascii')
        out = LIVE.frame(sess, t, 12.0, frame)
        self.assertFalse(out.get('skipped'), out)
        splits = sess['dial']['splits']
        self.assertEqual([k for k in splits if splits[k]['group'] == 'cohort'], ['cohort_men', 'cohort_women', 'cohort_dads', 'cohort_spanish_speakers'])
        self.assertEqual(splits['cohort_dads']['n'], 3)
        self.assertEqual(len(splits['cohort_men']['value']), 1)
        self.assertIn('women', splits)


if __name__ == '__main__':
    unittest.main(verbosity=2)
