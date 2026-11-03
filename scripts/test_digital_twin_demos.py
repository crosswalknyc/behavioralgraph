#!/usr/bin/env python3
"""Cohorts picked from the standard demographics (Jenna 2026-11-03).

The list, the words, the matching against a twin's rows, the share and
the seats, the saved-setup round trip, and the page's copy of the list.
Offline.
"""
import json
import os
import re
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from migration import digital_twin_demos as D      # noqa: E402
from migration import digital_twin_panel as P      # noqa: E402
from migration import digital_twin_pilot as PI     # noqa: E402

KEY = 'digital-twins/admin/Paramount_20261030_202216_418f0c1d10.json.gz'


def _twin():
    return {
        'meta': {'name': 'Paramount+', 'subject': 'Paramount+', 'key': KEY, 'size_us': 83_791_763},
        'identity': {'demographics': {
            'gender': [{'label': 'Male', 'pct': 49.2653}, {'label': 'Female', 'pct': 49.0747}, {'label': 'Non-Binary', 'pct': 0.7523}, {'label': 'Trans Male', 'pct': 0.5745}, {'label': 'Trans Female', 'pct': 0.3332}],
            'age': [{'label': '25-34', 'pct': 27.8091}, {'label': '18-24', 'pct': 22.1098}, {'label': '35-44', 'pct': 18.6355}, {'label': '45-54', 'pct': 12.4954}, {'label': '17 and Under', 'pct': 8.4647}, {'label': '55-64', 'pct': 6.7603}, {'label': '65 or Older', 'pct': 3.7252}],
            'ethnicity': [{'label': 'White', 'pct': 55.5663}, {'label': 'Hispanic or Latino', 'pct': 18.0139}, {'label': 'Black or African American', 'pct': 14.4463}, {'label': 'Asian', 'pct': 9.4566}, {'label': 'Another Race/Ethnicity', 'pct': 2.5169}],
            'income': [{'label': '$25,000 - $49,999', 'pct': 21.0406}, {'label': '$50,000 - $74,999', 'pct': 18.8926}, {'label': '$100,000 - $149,999', 'pct': 17.6122}, {'label': 'Less than $25,000', 'pct': 15.2438}, {'label': '$75,000 - $99,999', 'pct': 14.425}, {'label': '$150,000 - $249,999', 'pct': 10.4082}, {'label': '$250,000 or More', 'pct': 2.3776}],
            'education': [{'label': 'High School or Less', 'pct': 30.9119}, {'label': 'Some College / Associate Degree', 'pct': 28.141}, {'label': 'Bachelors Degree', 'pct': 26.308}, {'label': 'Graduate or Professional Degree', 'pct': 14.6391}],
            'parental': [{'label': 'No Children', 'pct': 61.0808}, {'label': 'Has Children', 'pct': 37.5875}, {'label': 'Prefer Not to Say', 'pct': 1.3317}],
            'relationship': [{'label': 'Married', 'pct': 45.8554}, {'label': 'Single', 'pct': 37.4954}, {'label': 'Divorced or Separated', 'pct': 8.6844}, {'label': 'In a Relationship', 'pct': 7.9648}],
            'sexual_orientation': [{'label': 'Straight / Heterosexual', 'pct': 85.8395}, {'label': 'LGBTQ+', 'pct': 12.2731}, {'label': 'Prefer Not to Say', 'pct': 1.8874}],
            'occupation': [{'label': 'Management, Business & Professional', 'pct': 26.8561}, {'label': 'Service & Hospitality', 'pct': 14.4893}, {'label': 'Sales & Retail', 'pct': 9.3693}, {'label': 'Other', 'pct': 3.8476}],
        }},
        'panel_sessions': [], 'pilot_sessions': [],
    }


class TheList(unittest.TestCase):
    def test_twelve_categories_with_the_panels_values(self):
        self.assertEqual(D.KEYS, ['GENDER', 'AGE', 'ETHNICITY', 'PRIMARY_LANGUAGE', 'INCOME', 'EDUCATION', 'RELATIONSHIP', 'PARENTAL_STATUS',
                                  'NUMBER_OF_CHILDREN', 'AGE_OF_CHILDREN', 'OCCUPATION', 'SEXUAL_ORIENTATION'])
        for d in D.DEMOS:
            self.assertTrue(d['values'])
            self.assertEqual(len(d['values']), len(set(d['values'])))
            for v in d['values']:
                self.assertNotIn('Prefer', v)
                self.assertNotIn('\u2013', v)
        self.assertIn('$100,000 - $149,999', dict((d['key'], d['values']) for d in D.DEMOS)['INCOME'])

    def test_the_page_carries_the_same_list(self):
        html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()
        m = re.search(r"var DTW_DEMOS = (\[.*?\]);\n", html)
        self.assertTrue(m, 'DTW_DEMOS not found on the page')
        self.assertEqual(json.loads(m.group(1)), D.public_demos())

    def test_canon_and_clean(self):
        self.assertEqual(D.canon_value('AGE', '25\u201334'), '25-34')
        self.assertEqual(D.canon_value('INCOME', '$75,000 \u2013 $99,999'), '$75,000 - $99,999')
        self.assertEqual(D.canon_value('EDUCATION', "Bachelor's degree"), 'Bachelors Degree')
        self.assertEqual(D.canon_value('GENDER', 'women'), 'Female')
        self.assertIsNone(D.canon_value('GENDER', 'Prefer Not to Say'))
        self.assertIsNone(D.canon_value('NOPE', 'x'))
        self.assertEqual(D.clean_demos({'AGE': ['35-44', '18-24', 'bogus'], 'gender': 'Male', 'PETS': ['dog']}), {'GENDER': ['Male'], 'AGE': ['18-24', '35-44']})
        self.assertEqual(D.clean_demos('men'), {})


class Words(unittest.TestCase):
    def test_plain_words(self):
        self.assertEqual(D.demos_words({'GENDER': ['Male'], 'AGE': ['18-24', '25-34']}), 'men, 18 to 34')
        self.assertEqual(D.demos_words({'GENDER': ['Female'], 'AGE': ['25-34', '45-54']}), 'women, 25 to 34 or 45 to 54')
        self.assertEqual(D.demos_words({'AGE': ['55-64', '65 or Older']}), '55 and older')
        self.assertEqual(D.demos_words({'PRIMARY_LANGUAGE': ['Spanish']}), 'Spanish speakers')
        self.assertEqual(D.demos_words({'INCOME': ['$100,000 - $149,999', '$150,000 - $249,999', '$250,000 or More']}), 'household income $100k and up')
        self.assertEqual(D.demos_words({'INCOME': ['Less than $25,000', '$25,000 - $49,999']}), 'household income under $50k')
        self.assertEqual(D.demos_words({'GENDER': ['Male', 'Trans Male'], 'PARENTAL_STATUS': ['Has Children'], 'AGE_OF_CHILDREN': ['6 to 10']}),
                         'men or trans men, with children at home, children 6 to 10')
        self.assertEqual(D.demos_words({'EDUCATION': ['Bachelors Degree', 'Graduate or Professional Degree'], 'OCCUPATION': ['Legal']}),
                         'college graduates or graduate degrees, working in legal')
        self.assertEqual(D.suggest_name({'GENDER': ['Male'], 'AGE': ['18-24', '25-34']}), 'Men 18 to 34')
        self.assertEqual(D.suggest_name({'PRIMARY_LANGUAGE': ['Spanish']}), 'Spanish speakers')
        self.assertEqual(D.suggest_name({'ETHNICITY': ['Asian']}), 'Asian')
        self.assertEqual(D.suggest_name({'INCOME': ['$250,000 or More']}), 'Household income $250k and up')
        for w in (D.demos_words({k: d['values'] for k, d in ((x['key'], x) for x in D.DEMOS)}),):
            self.assertNotIn('\u2014', w)


class Matching(unittest.TestCase):
    def test_predicates_read_a_twins_rows(self):
        self.assertTrue(D.value_pred('AGE', ['18-24', '25-34'])('25-34'))
        self.assertFalse(D.value_pred('AGE', ['18-24', '25-34'])('35-44'))
        self.assertTrue(D.value_pred('AGE', ['18-24'])('18 to 24'))
        self.assertTrue(D.value_pred('INCOME', ['$50,000 - $74,999'])('$50,000 - $74,999'))
        self.assertFalse(D.value_pred('INCOME', ['$50,000 - $74,999'])('$75,000 - $99,999'))
        self.assertTrue(D.value_pred('INCOME', ['$250,000 or More'])('$250,000+'))
        self.assertTrue(D.value_pred('SEXUAL_ORIENTATION', ['Gay or Lesbian'])('LGBTQ+'))
        self.assertFalse(D.value_pred('SEXUAL_ORIENTATION', ['Straight / Heterosexual'])('LGBTQ+'))
        self.assertTrue(D.value_pred('PARENTAL_STATUS', ['Has Children'])('Parent of children under 18'))
        self.assertFalse(D.value_pred('PARENTAL_STATUS', ['Has Children'])('No Children'))
        self.assertTrue(D.value_pred('PARENTAL_STATUS', ['No Children'])('Not a parent of children under 18'))
        self.assertTrue(D.value_pred('GENDER', ['Male', 'Trans Male'])('Trans Male'))
        self.assertFalse(D.value_pred('GENDER', ['Male'])('Female'))
        self.assertTrue(D.value_pred('EDUCATION', ['Bachelors Degree'])("Bachelor's degree"))
        self.assertEqual(D.gender_codes(['Female', 'Trans Female', 'Non-Binary']), ['F', 'X'])
        self.assertEqual(D.age_span(['18-24', '35-44']), (18, 44))
        self.assertAlmostEqual(D.fallback_share('NUMBER_OF_CHILDREN', ['2']), 0.4271, places=3)


class Share(unittest.TestCase):
    def test_a_selection_narrows_by_the_twins_own_rows(self):
        t = _twin()
        self.assertAlmostEqual(P._segment_share(t, {'demos': {'GENDER': ['Male']}}), 0.4927, places=3)
        self.assertAlmostEqual(P._segment_share(t, {'demos': {'GENDER': ['Male'], 'AGE': ['18-24', '25-34']}}), 0.4927 * 0.4992, places=3)
        # the children categories condition on the twin's parents once
        kids = P._segment_share(t, {'demos': {'PARENTAL_STATUS': ['Has Children'], 'AGE_OF_CHILDREN': ['6 to 10']}})
        self.assertAlmostEqual(kids, 0.3759 / (1 - 0.013317) * 0.1789, places=2)
        # Spanish reads off the twin's Hispanic share
        self.assertAlmostEqual(P._segment_share(t, {'demos': {'PRIMARY_LANGUAGE': ['Spanish']}}), 0.1801 * P.SPANISH_AT_HOME, places=3)
        # a legacy filter is unchanged, and a demos filter never double counts a legacy key beside it
        self.assertAlmostEqual(P._segment_share(t, {'gender': 'M'}), 0.4927 + 0.0057, places=3)     # the older word filter counts trans men as men too
        self.assertAlmostEqual(P._segment_share(t, {'demos': {'GENDER': ['Male']}, 'gender': 'M', 'age': [18, 34]}), 0.4927, places=3)
        self.assertEqual(P.cohort_filter_words({'demos': {'GENDER': ['Female'], 'AGE': ['25-34']}, 'city': 'Dallas'}), 'women, 25 to 34, in Dallas')


class Seats(unittest.TestCase):
    def test_cohort_segments_and_the_room(self):
        t = _twin()
        cohorts = [
            {'name': 'Men 18 to 34', 'seats': 4, 'demos': {'GENDER': ['Male'], 'AGE': ['18-24', '25-34']}},
            {'name': '', 'seats': 3, 'demos': {'GENDER': ['Female'], 'PARENTAL_STATUS': ['Has Children'], 'NUMBER_OF_CHILDREN': ['2', '3']}},
            {'name': 'Spanish', 'seats': 3, 'demos': {'PRIMARY_LANGUAGE': ['Spanish'], 'INCOME': ['$50,000 - $74,999', '$75,000 - $99,999']}},
            {'name': 'typed only', 'seats': 2, 'composition': 'men with children'},      # the older free-text path still works
        ]
        segs = PI.cohort_segments(cohorts, KEY)
        self.assertEqual([s['label'] for s in segs], ['Men 18 to 34', 'Women with kids', 'Spanish', 'typed only'])
        self.assertEqual(segs[0]['composition'], 'men, 18 to 34')
        self.assertEqual(segs[0]['filter'], {'demos': {'GENDER': ['Male'], 'AGE': ['18-24', '25-34']}, 'gender': 'M', 'age': [18, 34]})
        self.assertEqual(segs[1]['composition'], 'women, with children at home, 2 or 3 children')
        self.assertEqual(segs[2]['filter']['language'], 'Spanish')
        self.assertNotIn('filter', segs[3])
        g = P.build_group([t], segments=segs, in_room=12, seed='x')
        by = {s['label']: s for s in g['segments']}
        self.assertEqual([s['seats'] for s in g['segments']], [4, 3, 3, 2])
        self.assertAlmostEqual(by['Men 18 to 34']['share_pct'], 24.6, delta=0.3)
        self.assertAlmostEqual(by['Women with kids']['share_pct'], 100 * 0.4907 * 0.3759 / (1 - 0.013317) * (0.4271 + 0.1803), delta=0.4)
        men = [r for r in g['roster'] if r['cohort'] == 'Men 18 to 34']
        self.assertTrue(all(r['gender'] == 'M' and 18 <= r['age'] <= 34 for r in men), men)
        moms = [r for r in g['roster'] if r['cohort'] == 'Women with kids']
        self.assertTrue(all(r['gender'] == 'F' and r['parental'] == 'Has Children' and r['children'] in ('2', '3') for r in moms), moms)
        sp = [r for r in g['roster'] if r['cohort'] == 'Spanish']
        self.assertTrue(all(r['language'] == 'Spanish' and r['income'] in ('$50,000 - $74,999', '$75,000 - $99,999') for r in sp), sp)
        typed = [r for r in g['roster'] if r['cohort'] == 'typed only']
        self.assertTrue(all(r['gender'] == 'M' and r['parental'] == 'Has Children' for r in typed), typed)
        # the saved room re-derives its figures from the stored filter, not from words
        sess = {'id': 's1', 'group': g, 'twin_keys': [KEY]}
        self.assertFalse(P.recompute_group_sizes(sess, {KEY: t}))

    def test_saved_setup_keeps_the_picks(self):
        payload = {'meta': {'name': 'Paramount+', 'key': KEY}, 'pilot_sessions': []}
        rec = PI.save_setup(payload, {'name': 'x', 'tab': 'pilot', 'mode': 'episode', 'fields': {'dtwPiTitle': 'x'},
                                      'cohorts': [{'name': '', 'seats': 4, 'demos': {'GENDER': ['Male'], 'AGE': ['25\u201334']}}, {'name': 'Women', 'seats': 4, 'demos': {'GENDER': ['Female']}}],
                                      'cohort_n': 2}, 'jenna')
        self.assertEqual(rec['cohorts'][0]['demos'], {'GENDER': ['Male'], 'AGE': ['25-34']})
        self.assertEqual(rec['cohorts'][0]['composition'], 'men, 25 to 34')
        self.assertEqual(rec['cohorts'][1]['demos'], {'GENDER': ['Female']})


if __name__ == '__main__':
    unittest.main(verbosity=1)
