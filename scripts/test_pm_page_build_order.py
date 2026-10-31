#!/usr/bin/env python3
"""A build order that points at the open profile builds that profile on
the window named (Jenna 2026-10-30, Jay Patel on Godzilla Minus One:
"can you do one june 2024- june 2025", then "can you make this
profile?" were answered as reads with a made-up price). Hermetic."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import subject_rules as SR  # noqa: E402

PAGE = 'Godzilla Minus One'


class Windows(unittest.TestCase):
    def test_month_year_ranges(self):
        self.assertEqual(SR.window_in_text('can you do one june 2024- june 2025'), ('2024-06-01', '2025-06-01'))
        self.assertEqual(SR.window_in_text('Jun 1 2024 to Jun 1 2025'), ('2024-06-01', '2025-06-01'))
        self.assertEqual(SR.window_in_text('July 2023 through December 2023'), ('2023-07-01', '2023-12-01'))
        self.assertEqual(SR.window_in_text('run it for 2025'), ('2025-01-01', '2025-12-31'))
        self.assertEqual(SR.window_in_text('2023-2024'), ('2023-01-01', '2024-12-31'))
        self.assertEqual(SR.window_in_text('2024-06-01 to 2025-06-01'), ('2024-06-01', '2025-06-01'))
        self.assertIsNone(SR.window_in_text('can you make this profile?'))
        self.assertIsNone(SR.window_in_text('what did Godzilla Minus One do in theaters'))


class PageOrders(unittest.TestCase):
    def test_make_this_profile_takes_the_threads_window(self):
        hist = [{'role': 'user', 'text': 'can you do one june 2024- june 2025'},
                {'role': 'agent', 'text': 'On Godzilla Minus One: ... Window: Jun 1 2024 to Jun 1 2025.'}]
        o = SR.open_profile_build_order('can you make this profile?', PAGE, hist)
        self.assertIsNotNone(o)
        self.assertEqual(o['subject'], PAGE)
        self.assertEqual((o['start'], o['end']), ('2024-06-01', '2025-06-01'))
        self.assertEqual(o['prompt'], 'Run a profile for Godzilla Minus One. Date range: 2024-06-01 to 2025-06-01')

    def test_do_one_with_window_in_the_ask(self):
        o = SR.open_profile_build_order('can you do one june 2024- june 2025', PAGE, [])
        self.assertEqual((o['start'], o['end']), ('2024-06-01', '2025-06-01'))
        o2 = SR.open_profile_build_order('run this for 2025', PAGE, [])
        self.assertEqual((o2['start'], o2['end']), ('2025-01-01', '2025-12-31'))
        o3 = SR.open_profile_build_order('No, make another one for calendar 2024', PAGE, [])
        self.assertEqual((o3['start'], o3['end']), ('2024-01-01', '2024-12-31'))

    def test_no_window_anywhere_still_orders_the_profile(self):
        o = SR.open_profile_build_order('can you make this profile?', PAGE, [])
        self.assertEqual(o['prompt'], 'Run a profile for Godzilla Minus One')
        self.assertEqual(o['start'], '')

    def test_not_an_order(self):
        self.assertIsNone(SR.open_profile_build_order('how many people watched it in June 2024?', PAGE, []))
        self.assertIsNone(SR.open_profile_build_order('what would this cost?', PAGE, []))
        self.assertIsNone(SR.open_profile_build_order('why did you keep the June window?', PAGE, []))
        self.assertIsNone(SR.open_profile_build_order('can you make this profile?', '', []))        # nothing open
        self.assertIsNone(SR.open_profile_build_order('run a profile on Mountain Dew', PAGE, []))   # a named subject has its own lane

    def test_chat_wires_the_lane(self):
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        i = src.index('open_profile_build_order(text')
        j = src.index('SINGLE ROUTER (2026-08-28)')
        self.assertLess(i, j, 'the page build order must run ahead of the router')
        self.assertIn("prompt=_pb['prompt']", src)
        html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()
        self.assertIn('_pmRerouteText = (data.prompt', html)
        self.assertIn('text: _interpretText', html)
        self.assertIn('I will email you when it is complete.', html)



class YearIsNeverAnAnchor(unittest.TestCase):
    """migration/sample_sizing_guards: the Godzilla Minus One draft prose
    'since mid-2024 broadened the casual viewer base' was read as anchor
    2,024 and the profile shipped at a sample of 517 (2026-10-30)."""

    def test_parse_anchor_rejects_bare_years(self):
        from migration import sample_sizing_guards as G
        self.assertIsNone(G.parse_anchor('2024'))
        self.assertIsNone(G.parse_anchor(2024))
        self.assertIsNone(G.parse_anchor(' 1999 '))
        self.assertEqual(G.parse_anchor('2,024'), 2024)
        self.assertEqual(G.parse_anchor(188000), 188000)

    def test_prose_repair_skips_years_and_keeps_counts(self):
        from migration import sample_sizing_guards as G
        prose = ("The film's Academy Award win (2024) drove a second wave. Netflix "
                 "availability since mid-2024 broadened the casual viewer base further.")
        self.assertEqual(G.extract_anchor_from_prose(prose), (None, None))
        a, src = G.extract_anchor_from_prose(prose + " Toho reported 1,850,000 US ticket buyers.")
        self.assertEqual(a, 1850000)
        self.assertIn('ticket buyers', src)

    def test_godzilla_sample_survives_a_year_anchor(self):
        from migration import sample_sizing_guards as G
        logs = []
        tu, av, meta = G.enforce_anchor_derivation(
            'Godzilla Minus One', 188212, 58104, universe_anchor='2024', engaged_share=0.8359,
            anchor_source='2024 broadened the casual viewer',
            prose='Netflix availability since mid-2024 broadened the casual viewer base.', log=logs.append)
        self.assertEqual((tu, av), (188212, 58104))
        self.assertTrue(meta['anchor_missing'])
        self.assertFalse(meta['recomputed'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
