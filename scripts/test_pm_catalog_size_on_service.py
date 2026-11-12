#!/usr/bin/env python3
"""A size ask about a title on a service is answered from the profile on that service (2026-11-12).

Georgie & Mandy's First Marriage is held as three profiles: viewers on
Paramount+ (1,285,455), on HBO Max (404,556), and on HBO Max with Amazon
Prime Video (957,139). The catalog files all three under one subject,
and the size lookup answered any of them with the newest file's figure:
"How many people watched Georgie & Mandy's First Marriage on Paramount+?"
came back "957,139 US people (2026-08-07 to 2026-11-10)", the
two-service profile, on the canary replay. Gilmore Girls on Hulu was
told the all-service figure the same way.

The lookup now answers from the profile on exactly the services the
words name, under that profile's own name, and steps aside when none is.

Hermetic: the lane over a stubbed catalog page (the real facts); no S3.

    python3 scripts/test_pm_catalog_size_on_service.py
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

from migration import corpus_catalog as cc  # noqa: E402
from prometheus import catalog_lane as CL  # noqa: E402

P_KEY = "GEORGIE__MANDYS_FIRST_MARRIAGE_Viewers_(Paramount+)_11_11_2026_22_26.csv"
H_KEY = "Georgie_And_Mandys_First_Marriage_Viewers_(HBO_Max)_11_11_2026_15_44.csv"
T_KEY = "Georgie_And_Mandys_First_Marriage_Viewers_(HBO Max & Amazon Prime)_11_10_2026_23_17.csv"
W_P = {'start': '2024-10-25', 'end': '2026-11-10'}
W_H = {'start': '2026-08-07', 'end': '2026-11-10'}


def fact(kind, value, key, window, as_of, cut=False):
    f = {'product': 'profile', 'kind': kind, 'value': value, 'unit': 'people', 'window': window, 'as_of': as_of,
         'source': {'key': key, 'user': ''}, 'label': kind}
    if cut:
        f['cut'] = 'Avid Fan'
    return f


def georgie_page(with_hbo=True):
    facts = [
        fact('sample_size', 5036, "Georgie & Mandy's First Marriage Viewers (HBO Max & Amazon Prime) - Avid Fan.csv", W_H, '2026-11-12T05:31:16', True),
        fact('audience_size', 166138, "Georgie & Mandy's First Marriage Viewers (HBO Max & Amazon Prime) - Avid Fan.csv", W_H, '2026-11-12T05:31:16', True),
        fact('sample_size', 2166, "Georgie & Mandy's First Marriage Viewers (HBO Max) - Avid Fan.csv", W_H, '2026-11-12T05:00:43', True),
        fact('audience_size', 71456, "Georgie & Mandy's First Marriage Viewers (HBO Max) - Avid Fan.csv", W_H, '2026-11-12T05:00:43', True),
        fact('sample_size', 29013, T_KEY, W_H, '2026-11-12T05:00:43'), fact('audience_size', 957139, T_KEY, W_H, '2026-11-12T05:00:43'),
        fact('sample_size', 6814, "GEORGIE & MANDY'S FIRST MARRIAGE Viewers (Paramount+)- Avid Fan.csv", W_P, '2026-11-12T00:00:48', True),
        fact('audience_size', 224794, "GEORGIE & MANDY'S FIRST MARRIAGE Viewers (Paramount+)- Avid Fan.csv", W_P, '2026-11-12T00:00:48', True),
        fact('sample_size', 38965, P_KEY, W_P, '2026-11-12T00:00:48'), fact('audience_size', 1285455, P_KEY, W_P, '2026-11-12T00:00:48'),
    ]
    if with_hbo:
        facts += [fact('sample_size', 12263, H_KEY, W_H, '2026-11-12T04:59:09'), fact('audience_size', 404556, H_KEY, W_H, '2026-11-12T04:59:09')]
    return {'subject_key': 'georgie_and_mandy_s_first_marriage', 'subject': "GEORGIE & MANDY'S FIRST MARRIAGE Viewers", 'facts': facts,
            'aliases': ["GEORGIE & MANDY'S FIRST MARRIAGE Viewers", "GEORGIE & MANDY'S FIRST MARRIAGE Viewers (Paramount+)",
                        "GEORGIE & MANDY'S FIRST MARRIAGE Viewers (Paramount+) - Avid Fan", "Georgie & Mandy's First Marriage Viewers",
                        "Georgie & Mandy's First Marriage Viewers (HBO Max & Amazon Prime)",
                        "Georgie & Mandy's First Marriage Viewers (HBO Max & Amazon Prime) - Avid Fan",
                        "Georgie & Mandy's First Marriage Viewers (HBO Max)", "Georgie & Mandy's First Marriage Viewers (HBO Max) - Avid Fan",
                        "Georgie & Mandy's First Marriage Viewers (Paramount+)"]}


def plain_page():
    return {'subject_key': 'mad_men', 'subject': 'Mad Men', 'aliases': ['Mad Men', 'Mad Men - Avid Fan'],
            'facts': [fact('sample_size', 229853, 'Mad_Men_07_17_2026_07_59.csv', {'start': '2025-01-01', 'end': '2025-12-31'}, '2026-07-17T08:00:00'),
                      fact('audience_size', 7582850, 'Mad_Men_07_17_2026_07_59.csv', {'start': '2025-01-01', 'end': '2025-12-31'}, '2026-07-17T08:00:00'),
                      fact('audience_size', 1100311, 'Mad Men - Avid Fan.csv', {'start': '2025-01-01', 'end': '2025-12-31'}, '2026-07-17T08:10:00', True)]}


def gilmore_page():
    w1, w2 = {'start': '2025-07-01', 'end': '2026-06-30'}, {'start': '2026-07-01', 'end': '2026-10-14'}
    return {'subject_key': 'gilmore_girls', 'subject': 'Gilmore Girls Viewers',
            'aliases': ['Gilmore Girls Viewers', 'Gilmore Girls Viewers ON HULU', 'Gilmore Girls Viewers ON HULU - Avid Fan'],
            'facts': [fact('sample_size', 41588, 'Gilmore_Girls_Viewers_ON_HULU_10_20_2026_00_25.csv', w2, '2026-10-20T01:06:00'),
                      fact('audience_size', 1371988, 'Gilmore_Girls_Viewers_ON_HULU_10_20_2026_00_25.csv', w2, '2026-10-20T01:06:00'),
                      fact('sample_size', 287638, 'Gilmore_Girls_Viewers_08_19_2026_18_53.csv', w1, '2026-10-19T22:13:00'),
                      fact('audience_size', 9489178, 'Gilmore_Girls_Viewers_08_19_2026_18_53.csv', w1, '2026-10-19T22:13:00')]}


class _Catalog:
    """The catalog's two reads, served from one page."""

    def __init__(self, page):
        self.page = page
        self._a, self._l = cc.anchors_for, cc.load_subject_page

    def __enter__(self):
        page = self.page

        def anchors_for(subject, window=None, products=None, limit=60, with_ledger=True):
            facts = sorted(page['facts'], key=lambda f: str(f.get('as_of') or ''), reverse=True)
            return {'subject_key': page['subject_key'], 'subject': cc.best_display(page['aliases'], page['subject']),
                    'facts': facts[:limit], 'products': ['profile'], 'ledger': [], 'related': [], 'trends': []}
        cc.anchors_for = anchors_for
        cc.load_subject_page = lambda skey: page if skey == page['subject_key'] else {}
        return self

    def __exit__(self, *exc):
        cc.anchors_for, cc.load_subject_page = self._a, self._l


T = "Georgie & Mandy's First Marriage"


class ATitleOnThreeScopes(unittest.TestCase):

    def test_the_defect_as_it_shipped(self):
        # what the lookup's own anchor picks with no regard for the service: the newest file
        with _Catalog(georgie_page()):
            pa = cc.profile_anchor(cc.anchors_for(T))
        self.assertEqual(pa['audience_size'], 957139)

    def test_paramount_plus(self):
        with _Catalog(georgie_page()):
            r = CL.answer(f"How many people watched {T} on Paramount+?")
        self.assertIn('1,285,455 US people (Oct 25, 2024 to Nov 10, 2026). That is the profile', r['reply'])
        self.assertNotIn('957,139', r['reply'])
        # under the profile's own name, as the dashboard lists it
        self.assertTrue(r['reply'].startswith("GEORGIE & MANDY'S FIRST MARRIAGE Viewers (Paramount+):"), r['reply'])
        self.assertEqual(r['subject'], "GEORGIE & MANDY'S FIRST MARRIAGE Viewers (Paramount+)")

    def test_hbo_max_in_both_wordings(self):
        with _Catalog(georgie_page()):
            for q in (f"How many people watched {T} on HBO Max?", f"How big is the {T} audience on HBO Max?"):
                r = CL.answer(q)
                self.assertIn('404,556 US people (Aug 7, 2026 to Nov 10, 2026). That is the profile', r['reply'], q)
                self.assertTrue(r['reply'].startswith("Georgie & Mandy's First Marriage Viewers (HBO Max):"), r['reply'])

    def test_a_service_nothing_is_held_on_steps_aside(self):
        with _Catalog(georgie_page()):
            self.assertIsNone(CL.answer(f"How many people watched {T} on Peacock?"))
        with _Catalog(georgie_page(with_hbo=False)):
            self.assertIsNone(CL.answer(f"How many people watched {T} on HBO Max?"))      # never the two-service file for an HBO Max ask

    def test_no_service_named_steps_aside(self):
        with _Catalog(georgie_page()):
            self.assertIsNone(CL.answer(f"How many people watched {T}?"))                 # three service scopes, none of them the whole

    def test_the_two_service_profile_is_found_on_both_names(self):
        with _Catalog(georgie_page()):
            got = CL.profile_on_the_services_asked(cc, cc.anchors_for(T), f"{T} on HBO Max and Amazon Prime Video")
        self.assertTrue(got)
        self.assertEqual(got[0]['audience_size'], 957139)
        self.assertEqual(got[1], "Georgie & Mandy's First Marriage Viewers (HBO Max & Amazon Prime)")


class OtherSubjects(unittest.TestCase):

    def test_a_subject_with_no_service_scoped_profile_answers_as_before(self):
        with _Catalog(plain_page()):
            self.assertIsNone(CL.profile_on_the_services_asked(cc, cc.anchors_for('Mad Men'), 'how many people watched Mad Men'))
            r = CL.answer('how many people watched Mad Men')
        self.assertIn('Mad Men: 7,582,850 US people', r['reply'])

    def test_gilmore_girls_on_hulu_and_on_every_service(self):
        with _Catalog(gilmore_page()):
            on_hulu = CL.answer('How many people watched Gilmore Girls on Hulu?')
            whole = CL.answer('How many people watched Gilmore Girls?')
        self.assertIn('Gilmore Girls Viewers on Hulu: 1,371,988 US people', on_hulu['reply'])
        self.assertIn('9,489,178 US people', whole['reply'])                               # the profile on no one service answers the plain ask

    def test_a_profile_name_from_its_file(self):
        self.assertEqual(CL._profile_name(H_KEY), 'Georgie And Mandys First Marriage Viewers (HBO Max)')
        self.assertEqual(CL._profile_name('Mad_Men_07_17_2026_07_59.csv'), 'Mad Men')


if __name__ == '__main__':
    unittest.main(verbosity=1)
