#!/usr/bin/env python3
"""Rooms sit on the profile: gender quotas, orientation, personas
built from those attributes, and a named subject with no file pulls
one first (2026-10-27, Jenna).
"""
from __future__ import annotations

import csv
import io
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_panel as P  # noqa: E402
from migration import digital_twin_room as ROOM  # noqa: E402
from migration import digital_twin_synthesis as SYN  # noqa: E402
from migration import digital_twins as DT  # noqa: E402


def _beals():
    return {
        'meta': {'name': 'Jennifer Beals', 'key': 'jb', 'subject': 'Jennifer Beals',
                 'profile_key': 'Jennifer_Beals_06_17_2026_17_09.csv'},
        'identity': {'demographics': {
            'gender': [{'label': 'Female', 'pct': 72.3516}, {'label': 'Male', 'pct': 24.0352},
                       {'label': 'Non-Binary', 'pct': 2.6698}, {'label': 'Trans Male', 'pct': 0.7141},
                       {'label': 'Trans Female', 'pct': 0.2293}],
            'sexual_orientation': [{'label': 'Straight / Heterosexual', 'pct': 62.3226},
                                   {'label': 'LGBTQ+', 'pct': 33.3786},
                                   {'label': 'Prefer Not to Say', 'pct': 4.2989}],
            'education': [{'label': 'Bachelors Degree', 'pct': 33.4009},
                          {'label': 'Graduate or Professional Degree', 'pct': 25.5933},
                          {'label': 'Some College', 'pct': 22.1124},
                          {'label': 'High School or Less', 'pct': 18.8934}],
            'occupation': [{'label': 'Management/Business/Professional', 'pct': 36.0779},
                           {'label': 'Healthcare', 'pct': 13.0412}, {'label': 'Service', 'pct': 11.5613},
                           {'label': 'Education/Library', 'pct': 10.0934}, {'label': 'Sales', 'pct': 8.1123},
                           {'label': 'Office/Admin', 'pct': 7.2012}, {'label': 'Production', 'pct': 5.1123},
                           {'label': 'Construction', 'pct': 4.8123}, {'label': 'Transportation', 'pct': 3.9881}],
            'age': [{'label': '55-64', 'pct': 25.0213}, {'label': '45-54', 'pct': 22.1124},
                    {'label': '35-44', 'pct': 18.4012}, {'label': '65+', 'pct': 16.3012},
                    {'label': '25-34', 'pct': 12.1012}, {'label': '18-24', 'pct': 6.0627}],
            'income': [{'label': '$100,000 - $149,999', 'pct': 22.1124},
                       {'label': '$75,000 - $99,999', 'pct': 18.4012},
                       {'label': '$50,000 - $74,999', 'pct': 17.2012},
                       {'label': '$150,000 - $249,999', 'pct': 16.1012},
                       {'label': '$25,000 - $49,999', 'pct': 14.3012},
                       {'label': 'LESS THAN $25,000', 'pct': 6.9012},
                       {'label': '$250,000 OR MORE', 'pct': 4.9816}],
            'relationship': [{'label': 'Married', 'pct': 40.9612}, {'label': 'Single', 'pct': 34.1412},
                             {'label': 'Divorced', 'pct': 14.9012}, {'label': 'Partnered', 'pct': 9.9964}],
            'parental': [{'label': 'No Children', 'pct': 58.2012}, {'label': 'Has Children', 'pct': 41.7988}],
            'ethnicity': [{'label': 'White', 'pct': 62.1012}, {'label': 'Black or African American', 'pct': 18.4012},
                          {'label': 'Hispanic or Latino', 'pct': 12.3012}, {'label': 'Asian', 'pct': 7.1964}],
        }},
        'traits': {}, 'lifecycle': {},
    }


class Quotas(unittest.TestCase):
    def test_largest_remainder_is_nine_women(self):
        t = _beals()
        labs = P.quota_labels(t['identity']['demographics']['gender'], 12)
        self.assertEqual(sum(1 for l in labs if P._gender_code(l) == 'F'), 9)
        self.assertEqual(sum(1 for l in labs if P._gender_code(l) == 'M'), 3)

    def test_prefer_not_does_not_take_a_seat(self):
        labs = P.quota_labels(_beals()['identity']['demographics']['sexual_orientation'], 12)
        self.assertEqual(sum(1 for l in labs if 'LGBTQ' in l), 4)
        self.assertFalse(any('prefer' in l.lower() for l in labs))


class Seats(unittest.TestCase):
    def test_room_matches_the_profile(self):
        t = _beals()
        group = P.build_group([t], in_room=12, seed='beals')
        roster = group['roster']
        self.assertEqual(len(roster), 12)
        self.assertEqual(sum(1 for r in roster if r['gender'] == 'F'), 9)
        self.assertEqual(sum(1 for r in roster if r['gender'] == 'M'), 3)
        queer = [r for r in roster if 'LGBTQ' in str(r.get('orientation') or '')]
        self.assertEqual(len(queer), 4)
        self.assertTrue(all(r['gender'] == 'F' for r in queer))
        self.assertTrue(all(r.get('education') for r in roster))
        self.assertTrue(all(r.get('occupation') for r in roster))

    def test_personas_are_lesbian_and_educated(self):
        t = _beals()
        sess = {'id': 'room1', 'group': P.build_group([t], in_room=12, seed='beals')}
        n = ROOM.ensure_personas(sess, {'jb': t}, 'beals-room')
        self.assertEqual(n, 12)
        texts = [str(r.get('persona') or '') for r in sess['group']['roster']]
        self.assertTrue(all(texts))
        self.assertGreaterEqual(sum(1 for p in texts if 'lesbian' in p.lower()), 3)
        self.assertGreaterEqual(sum(1 for p in texts if "bachelor" in p.lower() or 'graduate' in p.lower()), 5)
        self.assertTrue(any('works in' in p.lower() for p in texts))
        self.assertFalse(any('synth' in p.lower() or 'modeled' in p.lower() for p in texts))

    def test_empty_six_six_room_needs_a_reseat(self):
        t = _beals()
        sess = {'id': 'old', 'questions': [],
                'group': {'in_room': 12, 'roster': [
                    {'seat': i + 1, 'name': 'X', 'gender': 'F' if i < 6 else 'M', 'age': 40, 'city': 'Chicago'}
                    for i in range(12)]}}
        self.assertTrue(P.needs_reseat(sess, [t]))
        P.rebuild_group(sess, [t], seed='old')
        roster = sess['group']['roster']
        self.assertEqual(sum(1 for r in roster if r['gender'] == 'F'), 9)
        self.assertTrue(any(r.get('orientation') for r in roster))
        self.assertFalse(any(r.get('persona') for r in roster))

    def test_a_room_with_answers_is_left_alone(self):
        t = _beals()
        sess = {'id': 'asked', 'questions': [{'id': 'q1'}],
                'group': {'in_room': 12, 'roster': [{'seat': 1, 'gender': 'M'}]}}
        self.assertFalse(P.needs_reseat(sess, [t]))
        P.rebuild_group(sess, [t], seed='asked')
        self.assertEqual(sess['group']['roster'][0]['gender'], 'M')


class Hydrate(unittest.TestCase):
    def test_orientation_copies_from_the_file(self):
        class _Body:
            def __init__(self, b):
                self._b = b

            def read(self):
                return self._b

        class FakeS3:
            def __init__(self):
                self.objs = {}

            def get_object(self, Bucket, Key):
                return {'Body': _Body(self.objs[Key])}

            def put_object(self, Bucket, Key, Body, **kw):
                self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['Column', 'Value', 'Brand Penetration (Row)', 'Original Raw Numbers', 'US Gen Pop Projection'])
        for r in [('BRAND INPUT', 'Jennifer Beals', '100.0', '283092', '9339205'),
                  ('GENDER', 'Female', '72.3516', '', ''), ('GENDER', 'Male', '24.0352', '', ''),
                  ('SEXUAL_ORIENTATION', 'LGBTQ+', '33.3786', '', ''),
                  ('SEXUAL_ORIENTATION', 'Straight / Heterosexual', '62.3226', '', ''),
                  ('OCCUPATION', 'Education/Library', '10.0934', '', ''),
                  ('EDUCATION', 'Bachelors Degree', '33.4009', '', '')]:
            w.writerow(r)
        s3 = FakeS3()
        s3.put_object(Bucket='b', Key='Jennifer_Beals_06_17_2026_17_09.csv', Body=buf.getvalue())
        twin = {'meta': {'subject': 'Jennifer Beals', 'profile_key': 'Jennifer_Beals_06_17_2026_17_09.csv', 'name': 'Jennifer Beals'},
                'identity': {'demographics': {'gender': [{'label': 'Female', 'pct': 72.3516}]}}}
        self.assertTrue(SYN.hydrate_identity_from_profile(twin, s3))
        self.assertTrue(any('LGBTQ' in str(r['label']) for r in twin['identity']['demographics']['sexual_orientation']))
        self.assertIn('occupation', twin['identity']['demographics'])
        self.assertIn('LGBTQ+', twin['identity']['read'])


class HowTo(unittest.TestCase):
    def test_how_to_says_the_room_sits_on_the_profile(self):
        doc = DT.how_to()
        twin = next(it for it in doc['items'] if it['id'] == 'twin')
        self.assertIn('pulled first', twin['put_in'])
        self.assertIn('orientation', twin['put_in'])
        panel = next(it for it in doc['items'] if it['id'] == 'panel')
        self.assertIn('gender split', panel['put_in'])
        self.assertIn('persona', panel['put_in'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
