#!/usr/bin/env python3
"""The in-chat product facts (Jenna 2026-11-05): "what can you do",
"can I upload a list", "how do I value a partnership" answer with the
same facts the client guide carries, in one or two sentences, with the
live prices. Hermetic: no model, no S3.

Covers: the three families fire on the shapes people type and never on
an order, a data question, a status ask, a twin ask or a named
valuation; the answers carry the rate card's numbers and no internal
word; a bulk order with a names sheet on the thread is left to the bulk
door; the lane sits before help_writing in service.ask and is listed
as deterministic and never priced; the help lane's own brief names the
same features.
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

from prometheus import product_facts as PF  # noqa: E402
from prometheus import rate_card as RC  # noqa: E402
from prometheus import help_lane as HL  # noqa: E402
from prometheus import service as S  # noqa: E402

BANNED = ('synth', 'model', 'panel', 'sample', 'pipeline', 'queue', 'claude', 'hostmap', 'ledger', 'feeder', 'anthropic',
          'estimated', '\u2014', '\u2013')


class Families(unittest.TestCase):
    def test_overview(self):
        for t in ('what can you do?', 'What can you do', 'what can I ask you', 'what do you do', 'how can you help me?',
                  'Hi! what can prometheus do', 'what are you able to do', 'tell me what you can do', 'what kinds of questions can I ask',
                  'give me an overview of what you can do', 'what else can you do'):
            self.assertEqual(PF.family_of(t), 'overview', t)

    def test_bulk_list(self):
        for t in ('can I upload a list?', 'can i upload a list of names and have you pull profiles for all of them',
                  'is there a way to send you a spreadsheet of talent', 'do you do bulk profile pulls', 'can you pull profiles in bulk?',
                  'how do I pull profiles for everyone on a list', 'can I upload a csv of brands', 'bulk upload?',
                  'can you build 200 profiles at once', 'could I attach an excel file of people', 'how do we run profiles for a whole roster'):
            self.assertEqual(PF.family_of(t), 'bulk_list', t)

    def test_valuation(self):
        for t in ('how do I value a partnership?', 'how do i value a brand partnership', 'how does the brand partnership valuation work',
                  'what is brand partnership iq', 'can I value a sponsorship here?', 'how do you measure a brand deal',
                  'explain how you value a partnership', 'what does a partnership valuation include', 'how does bpiq work'):
            self.assertEqual(PF.family_of(t), 'valuation', t)

    def test_never_fires(self):
        for t in ('value the Gunna x Nike partnership', 'how do I value the Stanley partnership', 'can you value Gunna x Nike',
                  'Value a brand partnership', 'pull a profile of Taylor Swift', 'how big is the Taylor Swift audience',
                  'how is my list going?', 'status of the list', 'build a twin from these URLs', 'can I upload a list of urls for a twin',
                  'what did Landman viewers watch next?', 'top QSRs for Paramount+ subscribers', 'email me this file',
                  'how many people watched Gilmore Girls on Hulu', 'can you see the full url now?', 'what is the price of a profile',
                  'build a deck on this', 'how do I use Digital Twins IQ'):
            self.assertIsNone(PF.family_of(t), t)

    def test_kill_switch(self):
        os.environ['PM_PRODUCT_FACTS'] = '0'
        try:
            self.assertIsNone(PF.family_of('what can you do?'))
        finally:
            os.environ.pop('PM_PRODUCT_FACTS', None)


class Answers(unittest.TestCase):
    def _clean(self, text):
        low = text.lower().replace("'queue the list'", '')      # the list door's own confirm chip, quoted
        for bad in BANNED:
            self.assertNotIn(bad, low, (bad, text))
        self.assertLessEqual(len(re.findall(r"[.!?](?:\s|$)", text)), 4, text)

    def test_overview_answer(self):
        out = PF.answer('what can you do?')
        self.assertEqual(out['family'], 'overview')
        self._clean(out['reply'])
        self.assertIn(RC.money(RC.default('profile_iq_build')), out['reply'])
        self.assertIn('list of names', out['reply'])
        self.assertIn('Brand Partnership valuation', out['reply'])
        self.assertIn('Flywheel', out['reply'])
        self.assertIn('Value a brand partnership', out['followups'])
        self.assertIn('Build a Flywheel', out['followups'])

    def test_bulk_answer_and_prices(self):
        out = PF.answer('can I upload a list of names?')
        self.assertEqual(out['family'], 'bulk_list')
        self._clean(out['reply'])
        self.assertTrue(out['reply'].startswith('Yes.'))
        self.assertIn("'pull profiles for everyone on this list'", out['reply'])
        self.assertIn("'Queue the list'", out['reply'])
        self.assertIn("'how is my list going'", out['reply'])
        self.assertIn('reused at no charge', out['reply'])
        self.assertIn(f"{RC.money(RC.default('profile_iq_build'))} each", out['reply'])
        self.assertIn('2,000 names', out['reply'])
        self.assertEqual(out['followups'], ['How is my list going?'])
        # a live rate rides through price_fn
        out2 = PF.answer('can I upload a list of names?', price_fn=lambda k, d: 275.0 if k == 'profile_iq_build' else d)
        self.assertIn('$275 each', out2['reply'])

    def test_valuation_answer_and_prices(self):
        out = PF.answer('how do I value a partnership?')
        self.assertEqual(out['family'], 'valuation')
        self._clean(out['reply'])
        self.assertIn("'Value a brand partnership'", out['reply'])
        for step in ('talent or property', 'campaign', 'dates', 'attribution window', 'control group', 'assets'):
            self.assertIn(step, out['reply'])
        bp = RC.default('brand_partnership_iq')
        self.assertIn(f"{RC.money(bp / 2)}, or {RC.money(bp)} with a control group", out['reply'])
        self.assertEqual(out['followups'], ['Value a brand partnership'])
        out2 = PF.answer('how do I value a partnership?', price_fn=lambda k, d: 1200.0 if k == 'brand_partnership_iq' else d)
        self.assertIn('$600, or $1,200 with a control group', out2['reply'])

    def test_bulk_order_with_a_sheet_is_the_doors(self):
        order = 'can you pull profiles for everyone on this list?'
        self.assertEqual(PF.family_of(order), 'bulk_list')
        self.assertIsNone(PF.answer(order, has_names_list=lambda: True))
        self.assertIsNotNone(PF.answer(order, has_names_list=lambda: False))
        self.assertIsNotNone(PF.answer('can I upload a list of names?', has_names_list=lambda: True))   # a question, not an order


class Wiring(unittest.TestCase):
    def test_lane_order_and_tables(self):
        markers = S.lane_markers()
        self.assertIn('product_facts', markers)
        self.assertLess(markers.index('product_facts'), markers.index('help_writing'))
        self.assertGreater(markers.index('product_facts'), markers.index('bpiq_wizard'))
        self.assertEqual(list(markers), list(S.LANE_ORDER))
        self.assertIn('product_facts', S.DETERMINISTIC_LANES)
        self.assertIn('product_facts', S.NEVER_PRICED_LANES)

    def test_help_brief_carries_the_same_facts(self):
        brief = HL._SYSTEM
        for needle in ('Value a brand partnership', 'Build a Flywheel', 'pull profiles for everyone on this list',
                       'how is my list going', 'Focus Group IQ', 'Pilot Testing', 'Approve or Cancel', 'last thing in the chat'):
            self.assertIn(needle, brief, needle)


if __name__ == '__main__':
    unittest.main(verbosity=1)
