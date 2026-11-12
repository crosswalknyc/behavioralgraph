#!/usr/bin/env python3
"""A two-service ask that matches a live profile is the existing match (2026-11-12).

Jessie, verbatim: "run a profile on Viewers of GEORGIE & MANDY'S FIRST
MARRIAGE, viewers ONLY on Amazon and HBO MAX. Date range August 7 2026
through November 10 2026". The live profile "Georgie & Mandy's First
Marriage Viewers (HBO Max & Amazon Prime)" covers the same title, the
same two services and the same window; the ask drafted as a new build
at $300.

The same title on exactly the same services (any order; Amazon / Amazon
Prime / Prime Video are one service, HBO Max / Max / HBOMax are one) on
the same window is the existing profile. Nothing wider: a one-service
profile is never the match for a two-service ask, or the reverse; a
different window is not a match; another title is not a match.

Hermetic: the binder over a stub catalog and stub windows; no model, no
S3, no app.

    python3 scripts/test_pm_service_scope_existing_match.py
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
from prometheus import service_scope_match as M  # noqa: E402

T = "Georgie & Mandy's First Marriage"
JESSIE = ("run a profile on Viewers of GEORGIE & MANDY'S FIRST MARRIAGE, viewers ONLY on Amazon and HBO MAX. "
          "Date range August 7 2026 through November 10 2026")
JESSIE_REVERSED = JESSIE.replace("Amazon and HBO MAX", "HBO MAX and Amazon")

TWO_KEY = "Georgie_And_Mandys_First_Marriage_Viewers_(HBO Max & Amazon Prime)_11_10_2026_23_17.csv"
HBO_KEY = "Georgie_And_Mandys_First_Marriage_Viewers_(HBO_Max)_11_11_2026_15_44.csv"
PPLUS_KEY = "GEORGIE__MANDYS_FIRST_MARRIAGE_Viewers_(Paramount+)_11_11_2026_22_26.csv"
CATALOG = [
    {'s3_key': TWO_KEY, 'display_name': T + " Viewers (HBO Max & Amazon Prime)", 'subject': T + " Viewers (HBO Max & Amazon Prime)",
     'category': 'SERIES', 'last_modified': '2026-11-12T05:00:16+00:00', 'days_old': 1},
    {'s3_key': T + " Viewers (HBO Max & Amazon Prime) - Avid Fan.csv", 'display_name': T + " Viewers (HBO Max & Amazon Prime) - Avid Fan",
     'category': 'SERIES', 'last_modified': '2026-11-12T05:01:02+00:00', 'days_old': 0},
    {'s3_key': HBO_KEY, 'display_name': T + " Viewers (HBO Max)", 'category': 'SERIES', 'last_modified': '2026-11-12T04:59:09+00:00', 'days_old': 0},
    {'s3_key': PPLUS_KEY, 'display_name': "GEORGIE & MANDY'S FIRST MARRIAGE Viewers (Paramount+)", 'category': 'SERIES',
     'last_modified': '2026-11-11T23:48:05+00:00', 'days_old': 0},
    {'s3_key': 'Young_Sheldon_Viewers_(HBO Max & Amazon Prime)_10_01_2026_10_00.csv', 'display_name': 'Young Sheldon Viewers (HBO Max & Amazon Prime)',
     'category': 'SERIES', 'last_modified': '2026-10-01T10:00:00+00:00', 'days_old': 42},
]
WINDOWS = {TWO_KEY: {'start': '2026-08-07', 'end': '2026-11-10'}, HBO_KEY: {'start': '2026-08-07', 'end': '2026-11-10'},
           PPLUS_KEY: {'start': '2024-10-25', 'end': '2026-11-10'},
           'Young_Sheldon_Viewers_(HBO Max & Amazon Prime)_10_01_2026_10_00.csv': {'start': '2026-08-07', 'end': '2026-11-10'}}


def draft(**kw):
    d = {'decision': 'new_build', 'subject': T + " Viewers", 'name': T + " Viewers", 'brand_category': 'SERIES - COMEDY',
         'is_ip_content': True, 'content_show': T, 'date_range': {'start': '2026-08-07', 'end': '2026-11-10'},
         'date_range_explicit': True, 'estimated_credits': 5}
    d.update(kw)
    return d


class _Windows:
    def __enter__(self):
        self._real = M.window_of_key
        M.window_of_key = lambda key: WINDOWS.get(key)
        return self

    def __exit__(self, *exc):
        M.window_of_key = self._real


class JessiesAsk(unittest.TestCase):

    def _bind(self, text, d=None, catalog=CATALOG):
        d = d or draft()
        with _Windows():
            got = N.bind(d, text, [], catalog=catalog)
        return d, got

    def test_her_words_are_the_live_profile(self):
        for text in (JESSIE, JESSIE_REVERSED):
            d, got = self._bind(text)
            self.assertEqual(d['decision'], 'existing_match', text)
            self.assertEqual(d['existing_match_s3_key'], TWO_KEY)
            self.assertEqual(d['existing_match_display_name'], T + " Viewers (HBO Max & Amazon Prime)")
            self.assertEqual(d['subject'], T + " Viewers (HBO Max & Amazon Prime)")        # the profile's own name, nothing minted
            self.assertEqual(got, [])                                                      # nothing is built, so nothing is bound

    def test_every_way_the_two_services_are_written(self):
        for a in ('Amazon', 'Amazon Prime', 'Prime Video', 'Amazon Prime Video'):
            for h in ('HBO Max', 'HBO MAX', 'HBOMax', 'Max'):
                for text in (f"run a profile on viewers of {T}, viewers ONLY on {a} and {h}. Date range August 7 2026 through November 10 2026",
                             f"run a profile on viewers of {T}, viewers ONLY on {h} and {a}. Date range August 7 2026 through November 10 2026"):
                    d, _ = self._bind(text)
                    self.assertEqual((d['decision'], d.get('existing_match_s3_key')), ('existing_match', TWO_KEY), text)

    def test_one_table(self):
        self.assertEqual(M.services_named(['Amazon']), M.services_named(['Amazon Prime Video']))
        self.assertEqual(M.services_named(['Amazon Prime']), M.services_named(['Prime Video']))
        self.assertEqual(M.services_named(['HBO Max']), M.services_named(['Max']))
        self.assertEqual(M.services_named(['HBOMax']), M.services_named(['HBO MAX']))
        self.assertEqual(M.services_of_name(T + " Viewers (HBO Max & Amazon Prime)"), M.services_named(['Amazon', 'Max']))
        self.assertEqual(M.services_of_name(T + " Viewers (Amazon Prime Video & HBO Max)"), M.services_named(['HBO Max', 'Prime Video']))
        self.assertEqual(M.services_of_name(T + " Viewers (HBO Max & Amazon Prime) - Avid Fan"), frozenset())       # a cut is not the profile
        self.assertEqual(M.services_of_name("Gilmore Girls Viewers ON HULU"), M.services_named(['Hulu']))
        self.assertEqual(M.title_fold("GEORGIE & MANDY'S FIRST MARRIAGE"), M.title_fold("Georgie And Mandys First Marriage"))
        self.assertEqual(M.title_of_name(T + " Viewers (HBO Max & Amazon Prime)"), T)


class NothingWider(unittest.TestCase):

    def _bind(self, text, d=None, catalog=CATALOG, history=None):
        d = d or draft()
        with _Windows():
            got = N.bind(d, text, history or [], catalog=catalog)
        return d, got

    def test_a_one_service_ask_is_the_one_service_profile_only(self):
        d, _ = self._bind(f"run a profile on viewers of {T}, viewers ONLY on HBO MAX. Date range August 7 2026 through November 10 2026")
        self.assertEqual((d['decision'], d.get('existing_match_s3_key')), ('existing_match', HBO_KEY))
        d, _ = self._bind(f"run a profile on viewers of {T}, viewers ONLY on Amazon. Date range August 7 2026 through November 10 2026")
        self.assertEqual(d['decision'], 'new_build')                    # Amazon alone is not on record: never the two-service file
        self.assertEqual(d['subject'], T + " Viewers on Amazon Prime Video")

    def test_a_two_service_ask_is_never_the_one_service_profile(self):
        cat = [c for c in CATALOG if c['s3_key'] != TWO_KEY]
        d, got = self._bind(JESSIE, catalog=cat)
        self.assertEqual(d['decision'], 'new_build')
        self.assertEqual(d['subject'], T + " Viewers (Amazon Prime Video & HBO Max)")
        self.assertEqual(got, ['Amazon Prime Video', 'HBO Max'])

    def test_three_services_are_not_two(self):
        d, _ = self._bind(f"run a profile on viewers of {T}, viewers ONLY on Amazon, HBO Max and Paramount+. "
                          "Date range August 7 2026 through November 10 2026")
        self.assertEqual(d['decision'], 'new_build')

    def test_another_window_is_not_a_match(self):
        d, _ = self._bind(JESSIE.replace('November 10 2026', 'October 31 2026'), draft(date_range={'start': '2026-08-07', 'end': '2026-10-31'}))
        self.assertEqual(d['decision'], 'new_build')
        d, _ = self._bind(JESSIE, draft(date_range={'start': '2026-08-08', 'end': '2026-11-09'}))        # a day either side is the same window
        self.assertEqual(d['decision'], 'existing_match')

    def test_a_window_that_cannot_be_read_is_not_a_match(self):
        real = M.window_of_key
        M.window_of_key = lambda key: None
        try:
            d = draft()
            N.bind(d, JESSIE, [], catalog=CATALOG)
        finally:
            M.window_of_key = real
        self.assertEqual(d['decision'], 'new_build')

    def test_another_title_is_not_a_match(self):
        d, _ = self._bind(JESSIE.replace("GEORGIE & MANDY'S FIRST MARRIAGE", "THE BIG BANG THEORY"),
                          draft(subject='The Big Bang Theory Viewers', name='The Big Bang Theory Viewers', content_show='The Big Bang Theory'))
        self.assertEqual(d['decision'], 'new_build')

    def test_a_pick_on_other_services_is_put_right(self):
        # the interpret step picked the one-service profile for the two-service ask
        d = draft(decision='existing_match', existing_match_s3_key=HBO_KEY, existing_match_display_name=T + " Viewers (HBO Max)")
        d, _ = self._bind(JESSIE, d)
        self.assertEqual((d['decision'], d['existing_match_s3_key']), ('existing_match', TWO_KEY))
        # ... and with no two-service profile on record it is built as asked, never handed the one-service file
        d = draft(decision='existing_match', existing_match_s3_key=HBO_KEY, existing_match_display_name=T + " Viewers (HBO Max)")
        d, got = self._bind(JESSIE, d, catalog=[c for c in CATALOG if c['s3_key'] != TWO_KEY])
        self.assertEqual(d['decision'], 'new_build')
        self.assertFalse(d.get('existing_match_s3_key'))
        self.assertEqual(got, ['Amazon Prime Video', 'HBO Max'])
        # a refresh picked off the profile on the same service stands
        d = draft(decision='time_shifted_refresh', existing_match_s3_key=HBO_KEY, existing_match_display_name=T + " Viewers (HBO Max)",
                  date_range={'start': '2026-08-07', 'end': '2026-10-31'})
        d, _ = self._bind(f"Run a profile for viewers of {T} on HBOMax from August 7, 2026 to October 31, 2026.", d)
        self.assertEqual((d['decision'], d['existing_match_s3_key']), ('time_shifted_refresh', HBO_KEY))

    def test_no_service_in_the_ask_changes_nothing(self):
        d, _ = self._bind(f"run a profile on viewers of {T}. Date range August 7 2026 through November 10 2026")
        self.assertEqual(d['decision'], 'new_build')
        d, _ = self._bind("audience of Nike shoppers", draft(subject='Nike', name='Nike', content_show='', is_ip_content=False, brand_category='APPAREL'))
        self.assertEqual((d['decision'], d['subject']), ('new_build', 'Nike'))

    def test_without_the_catalog_the_binder_is_as_before(self):
        d = draft()
        self.assertEqual(N.bind(d, JESSIE, []), ['Amazon Prime Video', 'HBO Max'])
        self.assertEqual(d['decision'], 'new_build')


class TheCallSite(unittest.TestCase):

    def test_the_interpret_step_hands_the_binder_its_catalog(self):
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        self.assertIn("_named_platforms.bind(spec_draft, text, history, cuts_hit=_platform_hit, catalog=catalog)", src)

    def test_a_window_is_read_from_the_sample_size_row(self):
        import re
        body = ('Column,Value,Brand Penetration (Row)\nBRAND INPUT,"a, b",100.0\n'
                'SAMPLE SIZE,SAMPLE SIZE (2026-08-07 TO 2026-11-10) | BEHAVIOR STUDY (2026-08-07 TO 2026-11-10),100.0\n')
        m = re.search(r"(?m)^SAMPLE SIZE,\"?SAMPLE SIZE \((\d{4}-\d{2}-\d{2}) TO (\d{4}-\d{2}-\d{2})\)", body)
        self.assertEqual((m.group(1), m.group(2)), ('2026-08-07', '2026-11-10'))
        self.assertEqual(M.window_of_draft({'engine_date_range': '2026-08-07 TO 2026-11-10'}), {'start': '2026-08-07', 'end': '2026-11-10'})


if __name__ == '__main__':
    unittest.main(verbosity=1)
