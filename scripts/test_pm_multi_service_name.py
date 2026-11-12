#!/usr/bin/env python3
"""A draft scoped to several services carries a name that reads cleanly (2026-11-12).

Jessie, verbatim: "run a profile on Viewers of GEORGIE & MANDY'S FIRST
MARRIAGE, viewers ONLY on Amazon and HBO MAX. Date range August 7 2026
through November 10 2026". The interpret draft came back named
"Georgie & Mandy's First Marriage Viewers ( /) on Amazon Prime Video and
HBO Max": the model had written the two services in brackets, the binder
took the service names out to fold them into the house shape, and the
brackets and the slash were left behind.

The house shape for several services is the one the live two-service
file carries: "... Viewers (HBO Max & Amazon Prime)". One service keeps
"... Viewers on Peacock". A name that already carries every service is
left as written.

Hermetic: the binder and the season-scope name only; no model, no app.

    python3 scripts/test_pm_multi_service_name.py
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import named_platforms as N  # noqa: E402
from migration.viewer_content_scope import scoped_subject_name  # noqa: E402

JESSIE = ("run a profile on Viewers of GEORGIE & MANDY'S FIRST MARRIAGE, viewers ONLY on Amazon and HBO MAX. "
          "Date range August 7 2026 through November 10 2026")
TITLE = "Georgie & Mandy's First Marriage"
TWO = ['Amazon Prime Video', 'HBO Max']
CLEAN = TITLE + " Viewers (Amazon Prime Video & HBO Max)"


def draft(subject, **kw):
    d = {'decision': 'new_build', 'subject': subject, 'name': subject, 'brand_category': 'SERIES - COMEDY', 'is_ip_content': True}
    d.update(kw)
    return d


class JessieTwoServices(unittest.TestCase):

    def _bind(self, subject, **kw):
        d = draft(subject, **kw)
        got = N.bind(d, JESSIE, [])
        return d, got

    def test_the_name_the_model_wrote_in_brackets(self):
        for written in (TITLE + " Viewers (Amazon / HBO Max)", TITLE + " Viewers (HBO Max/Amazon)", TITLE + " Viewers (Amazon & HBO Max)",
                        TITLE + " Viewers (Amazon and HBO MAX)", TITLE + " Viewers [Amazon + Max]"):
            d, got = self._bind(written)
            self.assertEqual(got, TWO, written)
            self.assertEqual(d['subject'], CLEAN, written)
            self.assertEqual(d['name'], CLEAN, written)

    def test_nothing_is_left_of_the_brackets(self):
        for written in (TITLE + " Viewers (Amazon / HBO Max)", TITLE + " Viewers - Amazon and HBO Max", TITLE + " Viewers on Amazon and HBO Max",
                        TITLE + " (Amazon + Max) Viewers", TITLE + " Viewers", TITLE + " Viewers ( /)"):
            d, _ = self._bind(written)
            self.assertEqual(d['subject'], CLEAN, written)
            for junk in ('( /)', '()', '( )', ' on Amazon', '/)', '(/', ' - '):
                self.assertNotIn(junk, d['subject'], written)

    def test_both_services_are_bound_strict(self):
        d, _ = self._bind(TITLE + " Viewers (Amazon / HBO Max)")
        self.assertEqual(d['named_platforms'], TWO)
        self.assertTrue(d['platform_scope_strict'])
        self.assertEqual([r[1] for r in d['home_platform_rows']], TWO)
        self.assertFalse(d.get('platform'))                       # no single home for two
        self.assertEqual(d['content_show'], TITLE)                # the title, without the services
        self.assertIn('viewers on Amazon Prime Video and HBO Max only', d['resolved_identity'])

    def test_a_name_that_already_carries_them_is_left_as_written(self):
        live = TITLE + " Viewers (HBO Max & Amazon Prime)"        # the live two-service file's own name
        d, got = self._bind(live)
        self.assertEqual(got, TWO)
        self.assertEqual(d['subject'], live)
        self.assertEqual(N.scoped_name(CLEAN, TWO), CLEAN)        # and the house shape is stable

    def test_the_title_inside_a_bracketed_name(self):
        self.assertEqual(N._bare_title(TITLE + " Viewers (HBO Max & Amazon Prime)"), TITLE)
        self.assertEqual(N._bare_title(TITLE + " Viewers (HBO Max)"), TITLE)
        self.assertEqual(N._bare_title("Yellowstone (2018) Viewers"), "Yellowstone (2018)")     # a year in the title is the title's


class OneServiceIsUnchanged(unittest.TestCase):

    def test_one_service_keeps_its_shape(self):
        self.assertEqual(N.scoped_name('Fallout Season 2 Viewers', ['Amazon Prime Video']), 'Fallout Season 2 Viewers on Amazon Prime Video')
        self.assertEqual(N.scoped_name('Gilmore Girls Viewers ON HULU', ['Hulu']), 'Gilmore Girls Viewers ON HULU')
        self.assertEqual(N.scoped_name(TITLE + ' Viewers (HBO Max)', ['HBO Max']), TITLE + ' Viewers (HBO Max)')
        self.assertEqual(N.scoped_name('The Daily', ['Spotify'], 'listeners'), 'The Daily Listeners on Spotify')
        self.assertEqual(N.scoped_name('X Viewers (Max)', ['HBO Max']), 'X Viewers on HBO Max')


class SeasonScopeKeepsTheTail(unittest.TestCase):

    def test_a_season_goes_in_front_of_the_noun_and_the_services_stay(self):
        five = ({'kind': 'series'}, {'mode': 'specific', 'season': 5})
        self.assertEqual(scoped_subject_name('Yellowstone Viewers (HBO Max & Amazon Prime Video)', *five),
                         'Yellowstone Season 5 Viewers (HBO Max & Amazon Prime Video)')
        self.assertEqual(scoped_subject_name('Yellowstone Viewers (Peacock)', *five), 'Yellowstone Season 5 Viewers (Peacock)')
        self.assertEqual(scoped_subject_name('Yellowstone Viewers on Peacock', *five), 'Yellowstone Season 5 Viewers on Peacock')
        self.assertEqual(scoped_subject_name('Yellowstone Viewers', *five), 'Yellowstone Season 5 Viewers')
        self.assertEqual(scoped_subject_name('Yellowstone (2018) Viewers', *five), 'Yellowstone (2018) Season 5 Viewers')


if __name__ == '__main__':
    unittest.main(verbosity=1)
