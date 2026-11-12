#!/usr/bin/env python3
"""A size reply shows one number, with the dates in plain words (2026-11-12).

The canary replies read:

    Fallout Season 2 Viewers on Prime Video: 13,732,978 US people
    (2025-11-01 to 2026-10-31), 416,277. Prime Video is the only US
    home of Fallout Season 2 in this window, so this is the title's
    whole audience.

    Helluva BOSS: 6,526,808 US people, 197,842. That is the profile on
    the dashboard.

The lookup wrote "..., 416,277 inside the 10 million sample." and the
house scrub took the last five words off, which left a second figure
with no label. The standing rule for a sizing answer is one number, one
definition. The reply now carries the US people and nothing beside
them, writes the window as "Nov 1, 2025 to Oct 31, 2026", and prints
the profile's name without the half-capitals the file name left on it.

Hermetic: the lane over stubbed catalog pages (the real facts), then
the same text through the house scrub; no S3, no network.

    python3 scripts/test_pm_size_reply_wording.py
"""
from __future__ import annotations

import os
import re
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from migration import corpus_catalog as cc  # noqa: E402
from migration import service_scope_sizing as SS  # noqa: E402
from prometheus import catalog_lane as CL  # noqa: E402
from prometheus import guards as G  # noqa: E402


def fact(kind, value, key, window, as_of='2026-11-10T22:45:22'):
    return {'product': 'profile', 'kind': kind, 'value': value, 'unit': 'people', 'window': window, 'as_of': as_of,
            'source': {'key': key, 'user': ''}, 'label': kind}


def page(skey, subject, aliases, *profiles):
    facts = []
    for key, sample, people, window in profiles:
        facts += [fact('sample_size', sample, key, window), fact('audience_size', people, key, window)]
    return {'subject_key': skey, 'subject': subject, 'aliases': list(aliases), 'facts': facts}


W_F = {'start': '2025-11-01', 'end': '2026-10-31'}
W_W = {'start': '2025-11-10', 'end': '2026-11-10'}
W_G1 = {'start': '2025-07-01', 'end': '2026-06-30'}
W_G2 = {'start': '2026-07-01', 'end': '2026-10-14'}
F_NAME = 'Fallout Season 2 Viewers on Prime Video'
W_NAME = 'My Life with the Walter Boys Viewers on Netflix'
PAGES = {
    'fallout season 2': page('fallout_season_2', F_NAME, [F_NAME, F_NAME + ' - Avid Fan'],
                             ('Fallout_Season_2_Viewers_on_Prime_Video_11_10_2026_21_37.csv', 416277, 13732978, W_F)),
    'my life with the walter boys': page('my_life_with_the_walter_boys', W_NAME, [W_NAME, W_NAME + ' - Avid Fan'],
                                         ('My_Life_with_the_Walter_Boys_Viewers_on_Netflix_11_10_2026_23_29.csv', 403615, 13315259, W_W)),
    # the dashboard's own list reads 'Helluva BOSS': the file is HELLUVA_BOSS_..., and a word of two to four
    # capitals is kept as it was found. The file has no window on its size row.
    'helluva boss': page('helluva_boss', 'Helluva BOSS', ['Helluva BOSS', 'Helluva BOSS - Avid Fan', 'Helluva BOSS - Female'],
                         ('HELLUVA_BOSS_08_01_2026_16_30.csv', 197842, 6526808, None)),
    'gilmore girls': page('gilmore_girls', 'Gilmore Girls Viewers',
                          ['Gilmore Girls Viewers', 'Gilmore Girls Viewers ON HULU', 'Gilmore Girls Viewers ON HULU - Avid Fan'],
                          ('Gilmore_Girls_Viewers_ON_HULU_10_20_2026_00_25.csv', 41588, 1371988, W_G2),
                          ('Gilmore_Girls_Viewers_08_19_2026_18_53.csv', 287638, 9489178, W_G1)),
}


def carriers(names, window):
    return {'carriers': {'services': SS.carrier_services(names), 'window': dict(window), 'verified_at': '2026-11-12T11:00:00Z',
                         'basis': ['carriage research']}}


# the title records, as the carrier check wrote them
RECORDS = {
    'FALLOUT': carriers(['Amazon Prime Video', 'Amazon Prime Video with Ads'], W_F),
    'MYLIFEWITHTHEWALTERBOYS': carriers(['Netflix', 'Netflix Standard with Ads'], W_W),
    'HELLUVABOSS': carriers(['YouTube (VivziePop / SpindleHorse official channel)', 'Amazon Prime Video (SVOD)',
                             'Amazon Prime Video with Ads (AVOD)'], {'start': '2024-01-01', 'end': '2026-09-30'}),
}


class _World:
    """The catalog's reads and the single-home record, served from the pages above."""

    def __enter__(self):
        self._a, self._l, self._c = cc.anchors_for, cc.load_subject_page, SS.load_record

        def anchors(subject, **kw):
            want = str(subject or '').strip().lower()
            pg = next((p for name, p in PAGES.items() if want.startswith(name)), None)
            if not pg:
                return {'facts': [], 'related': []}
            return {'subject_key': pg['subject_key'], 'subject': cc.best_display(pg['aliases'], pg['subject']),
                    'facts': list(pg['facts']), 'products': ['profile'], 'ledger': [], 'related': [], 'trends': []}

        cc.anchors_for = anchors
        cc.load_subject_page = lambda skey: next((p for p in PAGES.values() if p['subject_key'] == skey), {})
        SS.load_record = lambda title, s3=None: dict(RECORDS.get(SS.title_key(title)) or {})
        return self

    def __exit__(self, *exc):
        cc.anchors_for, cc.load_subject_page, SS.load_record = self._a, self._l, self._c


def figures(reply):
    """Every figure in a reply that is not part of a date."""
    bare = re.sub(r"\((?:from )?[A-Z][a-z]{2} \d{1,2}, \d{4}(?: to [A-Z][a-z]{2} \d{1,2}, \d{4})?\)", '', reply)
    bare = re.sub(r"Season \d+", '', bare)
    return re.findall(r"\d[\d,]*\d|\d", bare)


class TheFourTitles(unittest.TestCase):

    def reply(self, q):
        with _World():
            r = CL.answer(q)
        self.assertIsNotNone(r, q)
        return r

    def test_fallout_season_2(self):
        r = self.reply("How many people watched Fallout Season 2?")
        self.assertEqual(r['reply'], "Fallout Season 2 Viewers on Prime Video: 13,732,978 US people (Nov 1, 2025 to Oct 31, 2026). "
                                     "Prime Video is the only US home of Fallout Season 2 in this window, so this is the title's "
                                     "whole audience.")

    def test_my_life_with_the_walter_boys(self):
        r = self.reply("How many people watched My Life with the Walter Boys?")
        self.assertEqual(r['reply'], "My Life with the Walter Boys Viewers on Netflix: 13,315,259 US people (Nov 10, 2025 to Nov 10, 2026). "
                                     "Netflix is the only US home of My Life with the Walter Boys in this window, so this is the "
                                     "title's whole audience.")

    def test_helluva_boss(self):
        for q in ("How many people watched Helluva Boss?", "how many people watched helluva boss", "How big is Helluva Boss"):
            r = self.reply(q)
            self.assertEqual(r['reply'], "Helluva Boss: 6,526,808 US people. That is the profile on the dashboard.", q)
            # the follow-ups and the subject stay on the dashboard's own name, so opening it still finds it
            self.assertEqual(r['subject'], 'Helluva BOSS')
            self.assertIn('Open Helluva BOSS', r['followups'])

    def test_gilmore_girls(self):
        r = self.reply("How many people watched Gilmore Girls?")
        self.assertEqual(r['reply'], "Gilmore Girls Viewers: 9,489,178 US people (Jul 1, 2025 to Jun 30, 2026). "
                                     "That is the profile on the dashboard.")
        r = self.reply("How many people watched Gilmore Girls on Hulu?")
        self.assertEqual(r['reply'], "Gilmore Girls Viewers on Hulu: 1,371,988 US people (Jul 1, 2026 to Oct 14, 2026). "
                                     "That is the profile on the dashboard.")

    def test_one_number_before_and_after_the_house_scrub(self):
        asks = ("How many people watched Fallout Season 2?", "How many people watched My Life with the Walter Boys?",
                "How many people watched Helluva Boss?", "How many people watched Gilmore Girls?",
                "How many people watched Gilmore Girls on Hulu?", "How many people watched Fallout Season 2 on Prime Video?")
        for q in asks:
            raw = self.reply(q)['reply']
            for text in (raw, G.scrub_method_language(raw), G.scrub_jargon(raw)):
                self.assertEqual(len(figures(text)), 1, (q, text))
                self.assertNotIn('10 million', text)
                self.assertNotIn('sample', text.lower())
                self.assertIsNone(re.search(r"\d{4}-\d{2}-\d{2}", text), text)
                self.assertNotIn('\u2014', text)
            for sample in ('416,277', '403,615', '197,842', '287,638', '41,588'):
                self.assertNotIn(sample, raw)


class TheWindowInPlainWords(unittest.TestCase):

    def test_dates(self):
        self.assertEqual(CL._window_text({'start': '2025-11-01', 'end': '2026-10-31'}), ' (Nov 1, 2025 to Oct 31, 2026)')
        self.assertEqual(CL._window_text({'start': '2024-02-29', 'end': '2025-01-09'}), ' (Feb 29, 2024 to Jan 9, 2025)')
        self.assertEqual(CL._window_text({'start': '2026-07-01', 'end': ''}), ' (from Jul 1, 2026)')
        self.assertEqual(CL._window_text({'start': '2026-07-01T00:00:00', 'end': '2026-10-14T23:59:59'}), ' (Jul 1, 2026 to Oct 14, 2026)')
        self.assertEqual(CL._window_text(None), '')
        self.assertEqual(CL._window_text({}), '')
        self.assertEqual(CL._plain_date('last spring'), 'last spring')      # not a date: left as it came


class TheNameAsShown(unittest.TestCase):

    def test_a_word_left_in_capitals_by_the_file_name(self):
        f = CL.name_as_shown
        self.assertEqual(f('Helluva BOSS', 'How many people watched Helluva Boss?'), 'Helluva Boss')
        self.assertEqual(f('Helluva BOSS', 'how many watched helluva boss'), 'Helluva Boss')
        self.assertEqual(f('Helluva BOSS'), 'Helluva Boss')
        self.assertEqual(f('Gilmore Girls Viewers ON HULU', 'gilmore girls on hulu'), 'Gilmore Girls Viewers on Hulu')
        self.assertEqual(f('THE Rookie ON HULU'), 'The Rookie on Hulu')
        self.assertEqual(f('KATY Perry'), 'Katy Perry')
        self.assertEqual(f('Florence AND THE Machine'), 'Florence and the Machine')
        self.assertEqual(f('Wicked TUNA Viewers'), 'Wicked Tuna Viewers')

    def test_initials_stay(self):
        f = CL.name_as_shown
        for name in ('NCIS New York Viewers', 'HBO Max', 'Pluto TV', 'JD Vance', 'Yahya Abdul Mateen II', 'FX Now', 'NBC Sports',
                     'IT Connectivity Hardware Shoppers', 'Netflix SVOD Subscribers', 'US Bank', 'LA Sparks', 'MLB Network',
                     'Amazon Prime Video EST Buyers', 'BNY Mellon', 'TD Garden Event Attendees', 'FKA Twigs'):
            self.assertEqual(f(name, name.lower()), name)
            self.assertEqual(f(name), name)

    def test_the_reader_s_capitals_are_kept(self):
        self.assertEqual(CL.name_as_shown('Helluva BOSS', 'HELLUVA BOSS size'), 'Helluva BOSS')

    def test_a_name_with_nothing_to_compare_against_is_left(self):
        for name in ('FBI', 'ASPIRATION', 'IT', '', 'Fallout Season 2 Viewers on Prime Video', "Georgie & Mandy's First Marriage Viewers (HBO Max)",
                     "GEORGIE & MANDY'S FIRST MARRIAGE Viewers (Paramount+)"):
            self.assertEqual(CL.name_as_shown(name, 'how big is it'), name)


if __name__ == '__main__':
    unittest.main(verbosity=1)
