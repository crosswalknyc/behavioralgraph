#!/usr/bin/env python3
"""A split ask names every dimension it asked for, in the reader's words.

Jay Patel (Amazon), 2026-11-04: 'Break this down by age, gender, ethnicity,
household income and region' was offered as 'I can split the figures for
Jason Statham films by geography', because the single-ask path took the
first dimension in the table (device, daypart, geography, ...) rather than
the dimensions the ask named. Now every named dimension rides the offer and
the read, in the order and the words the reader used.

Hermetic. python3 scripts/test_pm_split_dimensions.py
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

import prometheus_analysis as pma                  # noqa: E402
from prometheus import derived_read as DR          # noqa: E402
from prometheus import priced_read as PR           # noqa: E402

JAY_1 = ("Using only data available as of June 2023: among US viewers of Jason Statham films on any streaming service or free TV, "
         "what percentage did not watch Prime Video? Break this down by age, gender, ethnicity, household income and region.")


class Dimensions(unittest.TestCase):
    def test_every_named_dimension_in_the_readers_order(self):
        self.assertEqual(pma.split_dimensions(JAY_1), ['age', 'gender', 'ethnicity', 'income', 'geography'])
        self.assertEqual(pma.dimension_phrase(pma.split_dimensions(JAY_1), JAY_1), 'age, gender, ethnicity, household income and region')
        self.assertEqual(pma.split_dimensions('break that out by region and age'), ['geography', 'age'])
        self.assertEqual(pma.dimension_phrase(['geography', 'age'], 'break that out by region and age'), 'region and age')
        self.assertEqual(pma.dimension_phrase(['device']), 'device')
        self.assertEqual(pma.dimension_phrase(['geography'], 'split it by DMA'), 'market')
        self.assertEqual(pma.split_dimensions('how many watched it'), [])
        # the old single pick still answers the device-first rule it documents
        self.assertEqual(pma.split_dimension('mobile and tablet by age'), 'device')

    def test_the_guidance_carries_one_breakdown_per_dimension(self):
        g = pma.dimension_split_guidance(JAY_1, None, 'age, gender, ethnicity, household income and region')
        self.assertTrue(g.startswith('DIMENSION SPLITS (this ask breaks a delivered figure out by 5 dimensions, age, gender, ethnicity, household income and region'))
        self.assertIn('one breakdown per dimension, in the order named', g)
        self.assertIn('never drop one of the named dimensions', g)
        for b in ('18-24', 'Female', 'Hispanic or Latino', '$250,000 or more', 'Northeast'):
            self.assertIn(b, g, b)
        one = pma.dimension_split_guidance('split it by device', None, '')
        self.assertTrue(one.startswith('DIMENSION SPLIT (this ask breaks a delivered figure out by device'))
        self.assertNotIn('\u2014', g + one)

    def test_the_plan_and_the_offer_say_the_dimensions(self):
        plan = DR.plan(JAY_1, [], {}, {}, pma=pma, ns=_NS(), normalize=lambda x: x, stop=set(), plausible=lambda s: True, consumption=lambda t: '')
        self.assertIsNotNone(plan)
        self.assertEqual(plan['dimension'], 'age, gender, ethnicity, household income and region')
        self.assertEqual(plan['metric_request']['dimension'], 'age, gender, ethnicity, household income and region')
        line = PR.lead_for('derived', 'Jason Statham films', dimension=plan['dimension'])
        self.assertIn('by age, gender, ethnicity, household income and region', line)
        self.assertNotIn('geography', PR.lead_for('derived', 'Jason Statham films', dimension='geography'))
        self.assertIn('by region', PR.lead_for('derived', 'Jason Statham films', dimension='geography'))


class _NS:
    """A named-subject helper that finds nothing: the plan's dimension is
    what this test is about, not the subject."""
    def mentioned_subject(self, *a, **k):
        return ''

    def thread_subject(self, *a, **k):
        return ''


if __name__ == '__main__':
    unittest.main(verbosity=1)
