#!/usr/bin/env python3
"""A what-if about the audience on screen is a read on that audience, never
a sizing of a new one.

Jay Patel (Amazon), 2026-11-03, with the Ordeal readers open: "if there
was an animated adaptation of the series, how many of these readers do
you think would tune in and sign up for Prime Video?" was read as sizing
an audience called "sign up for Prime Video", quoted at $100 and answered
with the whole country's sign-up audience. Jenna: it asked how many new
subscribers the show would bring to Prime Video, and how many would watch
in all. Hermetic.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import priced_read as P    # noqa: E402
from prometheus import sizing_lane as SZ   # noqa: E402

JAY = "if there was an animated adaptation of the series, how many of these readers do you think would tune in and sign up for Prime Video?"
JAY2 = "ok - so how about this, how many of those avid Ordeal readers have engaged with Prime Video in the trailing six months?"
JAY3 = "and how many of them do you think would sign up for Prime Video to watch an adaptation?"
CTX = {'view': 'profile-iq', 'primary': {'name': 'Ordeal from Webtoon', 's3_key': 'Ordeal_from_Webtoon_11_02_2026_19_31.csv'}}


class Sizing(unittest.TestCase):
    def test_the_pages_own_people_are_never_an_audience_size(self):
        for t in (JAY, JAY2, JAY3, 'how many of them are under 35', 'how many of these viewers would come back for a second season'):
            self.assertFalse(SZ.wants_sizing(t), t)
            self.assertTrue(SZ.is_audience_behavior_ask(t), t)

    def test_real_sizing_asks_still_size(self):
        for t in ('how big is the audience for Prime Video sign-ups', 'size the Taylor Swift fans audience',
                  'how many people are in the Ordeal readers audience', 'what is the sample size for Godzilla Minus One'):
            self.assertTrue(SZ.wants_sizing(t), t)
            self.assertFalse(SZ.is_audience_behavior_ask(t), t)

    def test_a_groups_past_behavior_is_an_event_count(self):
        self.assertTrue(SZ._EVENT_RX.search('how many of those avid Ordeal readers have engaged with Prime Video'))
        self.assertTrue(SZ._EVENT_RX.search('how many people signed up for Peacock in June'))
        self.assertTrue(SZ._EVENT_RX.search('how many of these viewers subscribed'))


class Classify(unittest.TestCase):
    def test_jays_ask_is_an_adaptation_read_on_the_open_audience(self):
        hit = P.classify(JAY, [], CTX)
        self.assertEqual((hit['kind'], hit['via'], hit['usd']), ('adaptation', 'generate', 100))
        hit3 = P.classify(JAY3, [], CTX)
        self.assertEqual(hit3['kind'], 'adaptation')
        self.assertIsNone(P.classify(JAY2, [], CTX))          # a share of the page's people: the reader, metered
        self.assertEqual(P.classify('how big is the audience for Prime Video sign-ups', [], CTX)['kind'], 'sizing')

    def test_other_what_ifs_and_the_measured_past(self):
        self.assertEqual(P.classify('if Netflix made a live-action Lore Olympus series, what share of these readers would subscribe to watch it?', [], CTX)['kind'], 'adaptation')
        self.assertEqual(P.classify('what if Peacock picked up a sequel series, how many of these viewers would come back and subscribe', [], CTX)['kind'], 'adaptation')
        self.assertEqual(P.classify('If the book were adapted into a TV series on Max, how many of these readers would watch?', [], CTX)['kind'], 'adaptation')
        self.assertEqual(P.classify('how many people watched the Netflix adaptation of One Piece last year', [], CTX)['kind'], 'viewership')
        self.assertFalse(P.is_adaptation_ask('email me the file'))

    def test_the_details_and_the_offer_line(self):
        self.assertEqual(P.adaptation_details(JAY), ('an animated adaptation', 'Prime Video'))
        self.assertEqual(P.adaptation_details('if Netflix made a live-action Lore Olympus series, what share of these readers would subscribe?'),
                         ('a live-action Lore Olympus series', 'Netflix'))
        lead = P.lead_for('adaptation', 'Ordeal from Webtoon readers', work='an animated adaptation', service='Prime Video')
        self.assertEqual(lead, 'I can read how many of Ordeal from Webtoon readers would tune in to an animated adaptation on Prime Video, '
                               'and how many of those would be new Prime Video sign-ups rather than people already on the service.')
        self.assertIn('adaptation', P.CUSTOM_FAMILY)
        label, credits, pull_type, kind = P.price_plan({'kind': 'adaptation', 'usd': 100}, 'jaypatel14')
        self.assertEqual(kind, 'adaptation')

    def test_the_gate_offers_the_two_number_read(self):
        out = P.work_gate(JAY, [], CTX, user={'username': 'jaypatel14'}, body={'bind_subject': 'Ordeal from Webtoon readers'}, username='jaypatel14')
        self.assertIsNotNone(out)
        reply = str(out.get('error') or out.get('reply') or '')
        self.assertIn('would tune in to an animated adaptation on Prime Video', reply)
        self.assertIn('new Prime Video sign-ups', reply)
        self.assertIn('Pulling this data will cost', reply)
        self.assertNotIn('I can size', reply)
        self.assertEqual((out.get('panel_offer') or {}).get('kind'), 'adaptation')


class Prompt(unittest.TestCase):
    def test_the_reasoning_rules_carry_the_two_numbers(self):
        import prometheus_analysis as pma
        for name in ('REASONED_METRICS_SYSTEM_PROMPT', 'ANALYSIS_SYSTEM_PROMPT'):
            txt = getattr(pma, name)
            self.assertIn('how many of these readers would tune in and sign up for Prime Video', txt, name)
            self.assertIn('NEW to the service', txt, name)
            self.assertIn('1 - that share', txt.replace('one minus the service', '1 - that share'), name)
            self.assertNotIn('\u2014', txt)


if __name__ == '__main__':
    unittest.main(verbosity=1)
