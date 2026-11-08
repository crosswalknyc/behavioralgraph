#!/usr/bin/env python3
"""The header of an overlap read names the two audiences; a service rides
only where the ask (or a bound audience) puts it (2026-11-08). Hermetic:
no model, no S3.

Grace Yin (Amazon) asked "What is the overlap between Hazbin Hotel
viewers and viewers of adult animation and anime on Netflix, Crunchyroll
and HIDIVE?" and the read opened "On people who watched Hazbin Hotel on
Netflix in the window:" (the Base line said the same). The first service
anywhere in the ask had been pinned on the title; Hazbin Hotel is not on
Netflix, and the ask named Netflix for the other audience.

Covered here:
  1. two_audiences / two_audience_label read the pair in the ask's own
     words: a platform-less overlap ask never prints a platform; an ask
     naming Netflix for one side keeps it on that side only; a bound
     audience that carries its service lends it, one without lends none
  2. title_platform / title_universe_label: the title's service comes
     from its own clause, or from the ask when it names one service for
     one audience; a second audience's service is never borrowed; the
     shapes the older tests pinned still read the same
  3. the opener and the Base line built from the universe, through
     methodology.audience_label / block, carry the pair and no platform
  4. the in-place repair of the shipped reply: the header and the Base
     line change, no figure does
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('AWS_ACCESS_KEY_ID', 'AKIATESTNOTREAL000000')
os.environ.setdefault('AWS_SECRET_ACCESS_KEY', 'not-a-real-secret')
os.environ.setdefault('AWS_SHARED_CREDENTIALS_FILE', '/nonexistent/aws/credentials')
os.environ.setdefault('AWS_CONFIG_FILE', '/nonexistent/aws/config')

from prometheus import methodology as M  # noqa: E402
from prometheus import overlap_read as O  # noqa: E402
from prometheus import page_default as P  # noqa: E402

GRACE = ("What is the overlap between Hazbin Hotel viewers and viewers of adult animation and anime on Netflix, "
         "Crunchyroll and HIDIVE?")
GRACE_PAIR = ('Hazbin Hotel viewers', 'viewers of adult animation and anime on Netflix, Crunchyroll and HIDIVE')
NO_PLATFORM = "anime viewers who overlap with a Hazbin Hotel sign-ups audience"
NETFLIX_SIDE = "What is the overlap between Hazbin Hotel viewers on Netflix and anime viewers?"

SHIPPED = (
    "On people who watched Hazbin Hotel on Netflix in the window:\n\n"
    "On people who watched Hazbin Hotel in the window: 4,657,219 of the 8,159,021 US viewers (57.1%) also watched adult "
    "animation or anime on Netflix, Crunchyroll or HIDIVE, and Netflix carries almost all of it at 4,429,143.\n\n"
    "Window: Nov 8 2025 to Nov 8 2026.\n"
    "- Crunchyroll: 895,448\n\n"
    "How these are counted:\n"
    "- 895,448: Hazbin Hotel viewers with Crunchyroll use in the window, 10.9751% of the audience, index 124.\n"
    "- Window: Nov 8 2025 to Nov 8 2026.\n"
    "- Base: people who watched Hazbin Hotel on Netflix in the window, measured across 10M US consumers and projected to the US.\n"
    "- People: Counts are US people (persons), each counted once inside the window however many devices or sessions they used; "
    "they are not accounts, households or impressions.")


class TwoAudiences(unittest.TestCase):
    def test_grace_pair(self):
        self.assertEqual(O.two_audiences(GRACE), GRACE_PAIR)
        self.assertEqual(O.two_audience_label(GRACE), 'Hazbin Hotel viewers and viewers of adult animation and anime on Netflix, Crunchyroll and HIDIVE')

    def test_platform_less_ask_never_prints_a_platform(self):
        for ask in (NO_PLATFORM,
                    "How big is the overlap between anime viewers and the Hazbin Hotel Season 1 sign-ups audience?",
                    "how much do Hazbin Hotel viewers and Helluva Boss viewers overlap?",
                    "What is the overlap between Young Sheldon viewers and Big Bang Theory viewers in 2025?"):
            lbl = O.two_audience_label(ask, bound=['Hazbin Hotel', 'Hazbin Hotel Season 1 sign-ups', 'Helluva Boss'])
            self.assertTrue(lbl, ask)
            self.assertNotRegex(lbl, r"\bon [A-Z]", ask)
            self.assertNotIn('Netflix', lbl, ask)
        self.assertEqual(O.two_audience_label(NO_PLATFORM), 'anime viewers and the Hazbin Hotel sign-ups audience')
        self.assertEqual(O.two_audience_label("How big is the overlap between anime viewers and the Hazbin Hotel Season 1 sign-ups audience?"),
                         'anime viewers and the Hazbin Hotel Season 1 sign-ups audience')
        # the window at the end of a side is the read's window, not the audience
        self.assertEqual(O.two_audiences("What is the overlap between Young Sheldon viewers and Big Bang Theory viewers in 2025?"),
                         ('Young Sheldon viewers', 'Big Bang Theory viewers'))

    def test_an_ask_naming_netflix_keeps_it_on_its_side(self):
        self.assertEqual(O.two_audiences(NETFLIX_SIDE), ('Hazbin Hotel viewers on Netflix', 'anime viewers'))
        self.assertEqual(O.two_audience_label(NETFLIX_SIDE), 'Hazbin Hotel viewers on Netflix and anime viewers')
        # Grace's ask: Netflix stays with the anime side, never on Hazbin Hotel
        lbl = O.two_audience_label(GRACE, bound=['Hazbin Hotel'])
        self.assertTrue(lbl.startswith('Hazbin Hotel viewers and '))
        self.assertNotIn('Hazbin Hotel viewers on Netflix', lbl)
        self.assertIn('anime on Netflix, Crunchyroll and HIDIVE', lbl)

    def test_bound_audience_lends_its_service_only_when_it_carries_one(self):
        ask = "What is the overlap between Young Sheldon viewers and Big Bang Theory viewers?"
        self.assertEqual(O.two_audience_label(ask, bound=['Young Sheldon on Netflix']),
                         'Young Sheldon viewers on Netflix and Big Bang Theory viewers')
        self.assertEqual(O.two_audience_label(ask, bound=['The Love Hypothesis (Amazon Prime Video)', 'Young Sheldon']),
                         'Young Sheldon viewers and Big Bang Theory viewers')
        self.assertEqual(O.two_audience_label(NETFLIX_SIDE, bound=['Hazbin Hotel on Prime Video']),
                         'Hazbin Hotel viewers on Netflix and anime viewers', 'the ask outranks the bound name')

    def test_two_titles_and_the_other_order(self):
        self.assertEqual(O.two_audiences("What is the overlap between Hazbin Hotel and Helluva Boss?"), ('Hazbin Hotel', 'Helluva Boss'))
        self.assertEqual(O.two_audiences("What is the overlap between viewers of adult animation and anime and Hazbin Hotel viewers?"),
                         ('viewers of adult animation and anime', 'Hazbin Hotel viewers'))

    def test_not_a_two_audience_ask(self):
        for ask in ("Of those viewers who watch Law & Order: SVU on Peacock, how many subscribe to Netflix?",
                    "how many people watched Dune on Max", "What does this number mean?",
                    "How many of the Crunchyroll overlap sit in the Hazbin Hotel Avid tier?", ""):
            self.assertIsNone(O.two_audiences(ask), ask)
            self.assertEqual(O.two_audience_label(ask), '', ask)


class TitlePlatform(unittest.TestCase):
    def test_the_titles_own_clause(self):
        self.assertEqual(P.title_platform(GRACE, 'Hazbin Hotel'), '')
        self.assertEqual(P.title_platform(NETFLIX_SIDE, 'Hazbin Hotel'), 'Netflix')
        self.assertEqual(P.title_platform('how many people watched Hazbin Hotel Season 2 on Prime Video', 'Hazbin Hotel'), 'Prime Video')
        self.assertEqual(P.title_platform('how many people watched Dune on Max', 'Dune (2021)'), 'Max')
        self.assertEqual(P.title_platform("Netflix's Hazbin Hotel viewers", 'Hazbin Hotel'), 'Netflix')
        self.assertEqual(P.title_platform('people who never watched Hazbin Hotel on Netflix', 'Hazbin Hotel'), '')
        self.assertEqual(P.platforms_named(GRACE), ['Netflix', 'Crunchyroll', 'HIDIVE'])
        self.assertEqual(P.platforms_named('how many people watched Dune on Max'), ['Max'])
        self.assertEqual(P.platforms_named('the max number of viewers'), [])

    def test_universe_label_never_borrows_the_other_sides_service(self):
        self.assertEqual(P.title_universe_label(GRACE, 'Hazbin Hotel', category='SERIES - ANIMATION'),
                         'people who watched Hazbin Hotel in the window')
        self.assertEqual(P.title_universe_label(NETFLIX_SIDE, 'Hazbin Hotel', category='SERIES - ANIMATION'),
                         'people who watched Hazbin Hotel on Netflix in the window')
        # one audience, one service named anywhere: the title keeps it
        self.assertEqual(P.title_universe_label('On Prime Video, how many people watched Hazbin Hotel Season 2?', 'Hazbin Hotel', category='SERIES - ANIMATION'),
                         'people who watched Hazbin Hotel on Prime Video in the window')
        # the shapes the older suites pinned
        self.assertEqual(P.title_universe_label('how many people watched Yellowstone on Paramount+ this season', 'Yellowstone - Avid Fan', category='SERIES - DRAMA'),
                         'people who watched Yellowstone on Paramount+ in the window who are avid fans')
        self.assertEqual(P.title_universe_label('how many people watched Dune on Max', 'Dune (2021)', category='MOVIE'),
                         'people who watched Dune (2021) on Max in the window')
        self.assertEqual(P.title_universe_label('how many people watched Jason Statham on Prime Video', 'Jason Statham', category='ACTOR'),
                         'people who watched a Jason Statham title on Prime Video in the window')
        self.assertEqual(P.title_universe_label('among US viewers of Jason Statham films on any streaming service, what share did not watch Prime Video', 'Jason Statham'),
                         'people who watched a Jason Statham film on any streaming service in the window')


class OpenerAndBase(unittest.TestCase):
    def test_header_and_base_line_carry_the_pair(self):
        res = {'subject': 'Hazbin Hotel', 'cohort': '', 'universe': O.two_audience_label(GRACE)}
        aud = M.audience_label(res, 'Hazbin Hotel')
        self.assertEqual(f"On {aud}:", 'On Hazbin Hotel viewers and viewers of adult animation and anime on Netflix, Crunchyroll and HIDIVE:')
        lines = M.block(window_label='Nov 8 2025 to Nov 8 2026', base_label=aud)
        self.assertIn('- Base: Hazbin Hotel viewers and viewers of adult animation and anime on Netflix, Crunchyroll and HIDIVE, '
                      'measured across 10M US consumers and projected to the US.', lines)
        res = {'subject': 'Hazbin Hotel Season 1 sign-ups', 'cohort': '', 'universe': O.two_audience_label(NO_PLATFORM)}
        self.assertEqual(f"On {M.audience_label(res, '')}:", 'On anime viewers and the Hazbin Hotel sign-ups audience:')
        self.assertNotRegex(M.audience_label(res, ''), r"\bon [A-Z]")

    def test_chat_helper_names_the_pair_on_the_read(self):
        import app  # noqa: F401  (binds the legacy host)
        from prometheus.legacy import chat as C
        res = {'subject': 'Hazbin Hotel', 'cohort': '', 'universe': 'people who watched Hazbin Hotel on Netflix in the window'}
        lbl = C._pm_name_two_audiences(res, GRACE, {'subject': 'Hazbin Hotel', 's3_key': 'Hazbin_Hotel_11_01_2026_10_00.csv', 'source': 'catalog'})
        self.assertEqual(lbl, 'Hazbin Hotel viewers and viewers of adult animation and anime on Netflix, Crunchyroll and HIDIVE')
        self.assertEqual(res['universe'], lbl)
        res = {'subject': 'Hazbin Hotel', 'cohort': '', 'universe': 'people who watched Hazbin Hotel on Prime Video in the window'}
        self.assertEqual(C._pm_name_two_audiences(res, 'how many people watched Hazbin Hotel on Prime Video', {'subject': 'Hazbin Hotel'}), '')
        self.assertEqual(res['universe'], 'people who watched Hazbin Hotel on Prime Video in the window', 'a one-audience read is untouched')


class InPlaceRepair(unittest.TestCase):
    def test_repair_changes_the_header_and_base_only(self):
        from prometheus import overlap_read as _ov
        fixed = _ov.repair_shipped_header(SHIPPED, GRACE, subject='Hazbin Hotel')
        self.assertTrue(fixed.startswith('On Hazbin Hotel viewers and viewers of adult animation and anime on Netflix, Crunchyroll and HIDIVE:\n\n'))
        self.assertIn('- Base: Hazbin Hotel viewers and viewers of adult animation and anime on Netflix, Crunchyroll and HIDIVE, '
                      'measured across 10M US consumers and projected to the US.', fixed)
        self.assertNotIn('Hazbin Hotel on Netflix', fixed)
        nums = re.compile(r"\d[\d,.]*%?")
        self.assertEqual(nums.findall(fixed), nums.findall(SHIPPED), 'no figure changes')
        self.assertEqual(fixed.count('\n'), SHIPPED.count('\n'))
        # idempotent; a reply without the header is left alone
        self.assertEqual(_ov.repair_shipped_header(fixed, GRACE, subject='Hazbin Hotel'), fixed)
        self.assertEqual(_ov.repair_shipped_header('plain text', GRACE, subject='Hazbin Hotel'), 'plain text')


if __name__ == '__main__':
    unittest.main(verbosity=2)
