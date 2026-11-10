#!/usr/bin/env python3
"""Account-holder universes are adults in every Prometheus read
(Grace Yin, Amazon, 2026-11-10: "We saw minors counted as 'sign-ups,'
so it matters for an 18 to 34 cut").

Hermetic: no S3, no model, no Flask. On a sign-ups profile fixture whose
AGE block still carries 17 AND UNDER at 4.6290 (the shipped defect):

  * the panel-fact age split prints no under-18 band and sums to 100;
    asking for the under-18 share answers that the audience is adults;
  * the profile digest's AGE line carries no under-18 band;
  * a measured read's age breakdown drops the under-18 row when the base
    is an account-holder universe or the measure is an account action
    (paid sign-ups by age band), and the adult rows sum to 100;
  * a twin built from the profile seats adults only, whatever the age
    picker asked for.

A viewers fixture keeps the band everywhere. Also pins the interpret
prompt line, the spec flag, and the shared twin module.
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pandas as pd  # noqa: E402

import prometheus_analysis as pma  # noqa: E402
from migration import adult_universe as AU  # noqa: E402
from migration import digital_twin_panel as P  # noqa: E402
from migration import digital_twin_synthesis as SYN  # noqa: E402

SIGNUPS = 'Hazbin Hotel Season 1 - Amazon Prime New Sign-Ups Viewers'
SIGNUPS_KEY = 'Hazbin_Hotel_Season_1_-_Amazon_Prime_New_Sign-Ups_Viewers_11_08_2026_17_09.csv'
VIEWERS = 'Hazbin Hotel Viewers'
VIEWERS_KEY = 'Hazbin_Hotel_Viewers_11_08_2026_17_09.csv'
BP = 'Brand Penetration (Row)'
SAMPLE = 41_469
UNIVERSE = 1_368_062
AGE = [('25-34', 33.0678), ('18-24', 28.7789), ('35-44', 18.7586), ('45-54', 8.9617),
       ('17 AND UNDER', 4.6290), ('55-64', 3.7707), ('65 OR OLDER', 2.0332)]


def fixture(subject_row='Hazbin Hotel Season 1'):
    rows = [
        ('BRAND INPUT', 'detail/B0CLM8LHS7, detail/B0CLM9D76B', 100.0, 100.0, SAMPLE, UNIVERSE),
        ('SAMPLE SIZE', 'SAMPLE SIZE (2024-01-19 TO 2024-02-15) | BEHAVIOR STUDY (2024-01-19 TO 2024-02-15)',
         100.0, 100.0, SAMPLE, UNIVERSE),
        ('BRAND CATEGORY', 'STREAMING/PLATFORM', '', '', '', ''),
        ('SUBJECT', subject_row, 100.0, 100.0, SAMPLE, UNIVERSE),
        ('GENDER', 'FEMALE', 57.8123, 57.8123, 23974, 790875),
        ('GENDER', 'MALE', 30.1177, 30.1177, 12489, 412008),
        ('GENDER', 'NON-BINARY', 12.07, 12.07, 5005, 165117),
    ]
    for lab, bp in AGE:
        raw = int(round(SAMPLE * bp / 100.0))
        rows.append(('AGE', lab, bp, bp, raw, int(round(raw / 10_000_000 * 329_900_000))))
    rows.append(('STREAMING/PLATFORM', 'Amazon Prime Video', 100.0, 61.2, SAMPLE, UNIVERSE))
    rows.append(('STREAMING/PLATFORM', 'Netflix', 71.3, 38.8, 29567, 975283))
    rows.append(('STREAMING/PLATFORM', 'Disney+/Hulu', 51.3, 27.9, 21273, 701565))
    rows.append(('QSR', 'Starbucks', 38.1, 52.0, 15799, 521200))
    rows.append(('QSR', 'Chipotle', 35.2, 48.0, 14597, 481560))
    return pd.DataFrame(rows, columns=['Column', 'Value', BP, 'Category Share', 'Original Raw Numbers',
                                       'US Gen Pop Projection'])


def meta_for(df, name, key):
    m = pma._profile_meta(df, name)
    m['s3_key'] = key
    return m


class PanelFactAgeSplit(unittest.TestCase):
    def test_signups_split_has_no_minor_band_and_sums_to_100(self):
        df = fixture()
        ans = pma.answer_panel_fact({'kind': 'demo', 'column': 'AGE'}, df, meta_for(df, SIGNUPS, SIGNUPS_KEY), {})
        self.assertTrue(ans and ans.get('reply'))
        low = ans['reply'].lower()
        self.assertNotIn('17 and under', low)
        self.assertNotIn('under 18', low)
        rows = ans['breakdown']['rows']
        self.assertFalse(any(AU.is_minor_age_label(r['label']) for r in rows))
        self.assertAlmostEqual(sum(r['share_pct'] for r in rows), 100.0, places=3)
        self.assertEqual(rows[0]['label'], '25-34')
        self.assertGreater(rows[0]['share_pct'], 33.0678)      # the adult shares grew when the band left
        self.assertFalse(any(abs(r['share_pct'] * 100 - round(r['share_pct'] * 100)) < 1e-6 for r in rows))

    def test_viewers_split_keeps_the_band(self):
        df = fixture()
        ans = pma.answer_panel_fact({'kind': 'demo', 'column': 'AGE'}, df, meta_for(df, VIEWERS, VIEWERS_KEY), {})
        self.assertIn('17 AND UNDER 4.6%', ans['reply'])
        self.assertTrue(any(AU.is_minor_age_label(r['label']) for r in ans['breakdown']['rows']))

    def test_asking_for_the_minor_share_on_signups_says_adults(self):
        df = fixture()
        ans = pma.answer_panel_fact({'kind': 'demo', 'column': 'AGE', 'bucket': '17 AND UNDER'}, df,
                                    meta_for(df, SIGNUPS, SIGNUPS_KEY), {})
        self.assertIn('No one under 18', ans['reply'])
        self.assertIn('18 and older', ans['reply'])

    def test_adult_bucket_share_on_signups_is_the_renormalized_one(self):
        df = fixture()
        ans = pma.answer_panel_fact({'kind': 'demo', 'column': 'AGE', 'bucket': '25-34'}, df,
                                    meta_for(df, SIGNUPS, SIGNUPS_KEY), {})
        self.assertIn('25-34', ans['reply'])
        self.assertEqual(len(ans['metrics']), 1)
        self.assertGreater(ans['metrics'][0]['value'], 33.0678)

    def test_the_key_alone_names_the_universe(self):
        # the SUBJECT row says 'Hazbin Hotel Season 1'; only the s3 key carries 'Sign-Ups'
        df = fixture()
        m = pma._profile_meta(df, '')
        m['s3_key'] = SIGNUPS_KEY
        self.assertTrue(pma.is_adult_universe(df, m))
        m2 = pma._profile_meta(df, '')
        m2['s3_key'] = VIEWERS_KEY
        self.assertFalse(pma.is_adult_universe(df, m2))


class DigestAgeLine(unittest.TestCase):
    def _age_line(self, name, key):
        df = fixture()
        digest = pma.build_profile_digest(df, meta_for(df, name, key), {}, norms=None)
        return next(ln for ln in digest.splitlines() if ln.strip().startswith('AGE:'))

    def test_signups_digest_carries_no_minor_band(self):
        line = self._age_line(SIGNUPS, SIGNUPS_KEY)
        self.assertNotIn('17 AND UNDER', line)
        vals = [float(p.strip().rsplit(' ', 1)[1]) for p in line.split(':', 1)[1].split('|') if p.strip()]
        self.assertAlmostEqual(sum(vals), 100.0, delta=0.35)     # one-decimal prints
        self.assertEqual(len(vals), 6)

    def test_viewers_digest_keeps_it(self):
        self.assertIn('17 AND UNDER 4.6', self._age_line(VIEWERS, VIEWERS_KEY))


def _age_read(subject, headline, dimension='Age band', basis='share of the paid sign-ups'):
    return {
        'subject': subject, 'metric_family': 'conversion', 'window_label': 'Jan 19 to Feb 15 2024',
        'headline': headline,
        'metrics': [{'name': 'paid', 'label': 'Paid sign-ups with a prior pilot view', 'value': 304517, 'unit': 'people',
                     'definition': 'paid sign-ups inside the first 28 days'}],
        'breakdown': {'dimension': dimension, 'share_basis': basis,
                      'rows': [{'label': '25-34', 'share_pct': 33.1, 'penetration_pct': 9.2},
                               {'label': '18-24', 'share_pct': 28.8, 'penetration_pct': 6.1},
                               {'label': '35-44', 'share_pct': 18.8, 'penetration_pct': 12.8},
                               {'label': '45-54', 'share_pct': 9.0, 'penetration_pct': 13.5},
                               {'label': '55 and over', 'share_pct': 5.7, 'penetration_pct': 13.0},
                               {'label': '17 and under', 'share_pct': 4.6, 'penetration_pct': 4.9}]},
    }


class BreakdownAgeBands(unittest.TestCase):
    def test_signup_rate_by_age_never_reports_a_minor_band(self):
        data = _age_read('Hazbin Hotel pilot viewers on YouTube',
                         '8.4% became paid Prime sign-ups in the first 28 days, climbing with age')
        res = pma.enforce_metrics_coherence(data, question='How does the paid sign-up rate split by age band inside the pilot viewer base?')
        labels = [r['label'] for r in res['breakdown']['rows']]
        self.assertNotIn('17 and under', labels)
        self.assertEqual(len(labels), 5)
        self.assertAlmostEqual(sum(r['share_pct'] for r in res['breakdown']['rows']), 100.0, places=3)
        reply = pma.format_generated_metrics_reply(res)
        self.assertNotIn('17 and under', reply.lower())

    def test_account_holder_base_drops_the_band_without_account_words_in_the_ask(self):
        data = _age_read('Hazbin Hotel Season 1', 'the cohort skews 25-34')
        data['metrics'] = [{'name': 'people', 'label': 'People', 'value': 1368062, 'unit': 'people', 'definition': 'the file'}]
        data['breakdown']['share_basis'] = 'share of the cohort'
        res = pma.enforce_metrics_coherence(data, question='split this by age', base_label=SIGNUPS_KEY)
        self.assertNotIn('17 and under', [r['label'] for r in res['breakdown']['rows']])

    def test_viewers_by_age_keeps_the_band(self):
        data = _age_read('Hazbin Hotel pilot viewers on YouTube', '3,614,827 US people played the pilot',
                         basis='share of the 3,614,827 US pilot viewers')
        data['metrics'] = [{'name': 'viewers', 'label': 'US pilot viewers', 'value': 3614827, 'unit': 'people',
                            'definition': 'distinct US people with a play'}]
        res = pma.enforce_metrics_coherence(data, question='Split the 3,614,827 pilot viewers by age', base_label=VIEWERS_KEY)
        self.assertIn('17 and under', [r['label'] for r in res['breakdown']['rows']])

    def test_a_children_age_dimension_is_never_an_age_breakdown(self):
        bd = {'dimension': 'Age of children at home', 'rows': [{'label': 'Under 3', 'share_pct': 20},
                                                                {'label': '14 to 17', 'share_pct': 30},
                                                                {'label': '6 to 10', 'share_pct': 50}]}
        self.assertFalse(pma.is_age_breakdown(bd))
        self.assertTrue(pma.is_age_breakdown({'dimension': 'Age band', 'rows': []}))
        self.assertTrue(pma.is_age_breakdown({'dimension': 'Mix', 'rows': [{'label': '18-24'}, {'label': '25-34'}]}))


class TwinSeatsAdults(unittest.TestCase):
    def _rows(self):
        df = fixture()
        return [{k: ('' if (isinstance(v, float) and v != v) else v) for k, v in r.items()} for r in df.to_dict('records')]

    def test_profile_identity_from_a_signups_file_has_no_minor_band(self):
        ident = SYN.profile_identity(self._rows(), 'Hazbin Hotel Season 1', profile_key=SIGNUPS_KEY)
        labels = [r['label'] for r in ident['demographics']['age']]
        self.assertFalse(any(AU.is_minor_age_label(l) for l in labels), labels)
        self.assertTrue(ident.get('adults_only'))
        self.assertAlmostEqual(sum(r['pct'] for r in ident['demographics']['age']), 100.0, places=3)

    def test_profile_identity_from_a_viewers_file_keeps_it(self):
        ident = SYN.profile_identity(self._rows(), 'Hazbin Hotel', profile_key=VIEWERS_KEY)
        self.assertTrue(any(AU.is_minor_age_label(r['label']) for r in ident['demographics']['age']))
        self.assertFalse(ident.get('adults_only'))

    def _twin(self, adults_only):
        ident = SYN.profile_identity(self._rows(), 'Hazbin Hotel Season 1',
                                     profile_key=SIGNUPS_KEY if adults_only else VIEWERS_KEY)
        return {'meta': {'name': SIGNUPS if adults_only else VIEWERS, 'key': 'hz', 'subject': 'Hazbin Hotel Season 1',
                         'profile_key': SIGNUPS_KEY if adults_only else VIEWERS_KEY,
                         **({'adults_only': True} if adults_only else {})},
                'identity': ident, 'traits': {}, 'lifecycle': {}}

    def test_room_on_a_signups_twin_seats_adults_only(self):
        for seed in ('a', 'grace', 'room-3'):
            group = P.build_group([self._twin(True)], in_room=24, seed=seed)
            ages = [int(r['age']) for r in group['roster']]
            self.assertEqual(len(ages), 24)
            self.assertTrue(all(a >= 18 for a in ages), ages)

    def test_picker_asking_for_17_and_under_on_a_signups_twin_still_seats_adults(self):
        t = self._twin(True)
        t['identity']['demographics']['age'] = [{'label': '17 and Under', 'pct': 40.0}, {'label': '18-24', 'pct': 60.0}]
        seg = [{'label': 'young', 'twin': 'hz', 'filter': {'demos': {'AGE': ['17 and Under', '18-24']}}}]
        group = P.build_group([t], segments=seg, in_room=12, seed='picker')
        self.assertTrue(all(int(r['age']) >= 18 for r in group['roster']), [r['age'] for r in group['roster']])

    def test_a_viewers_twin_with_a_stale_minor_row_is_not_forced(self):
        # the adult rule is scoped to account-holder universes; a viewers twin keeps its rows
        t = self._twin(False)
        rows = P._demo(t, 'age')
        self.assertTrue(any(AU.is_minor_age_label(r['label']) for r in rows))


class InterpretAndSpec(unittest.TestCase):
    def test_interpret_prompt_carries_the_adults_line_and_the_flag(self):
        with open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn('ACCOUNT-HOLDER UNIVERSES ARE ADULTS', src)
        self.assertIn('"account_holder\\": <true|false', src)
        self.assertIn('apply_to_spec_demos', src)

    def test_spec_flag_and_detection_zero_the_age_bucket(self):
        spec = {'name': SIGNUPS, 'file_stem': 'Hazbin_Hotel_Season_1_-_Amazon_Prime_New_Sign-Ups_Viewers',
                'tu_demos': {'AGE': dict(AGE)}, 'avid_demos': {'AGE': dict(AGE)}}
        self.assertEqual(AU.apply_to_spec_demos(spec), 2)
        self.assertIs(spec['account_holder'], True)
        self.assertEqual(spec['tu_demos']['AGE']['17 AND UNDER'], 0.0)
        self.assertAlmostEqual(sum(spec['avid_demos']['AGE'].values()), 100.0, places=4)
        viewers = {'name': VIEWERS, 'tu_demos': {'AGE': dict(AGE)}}
        self.assertEqual(AU.apply_to_spec_demos(viewers), 0)
        self.assertNotIn('account_holder', viewers)

    def test_twin_module_is_the_parent_mirror(self):
        here = os.path.join(ROOT, 'migration', 'adult_universe.py')
        self.assertTrue(os.path.exists(here))
        with open(here, encoding='utf-8') as fh:
            self.assertIn('ACCOUNT_HOLDER_RX', fh.read())


if __name__ == '__main__':
    unittest.main(verbosity=1)
