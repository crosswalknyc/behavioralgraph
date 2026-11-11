#!/usr/bin/env python3
"""Inline dates in an ask bind the build window (2026-11-08). Hermetic:
no model, no S3.

Grace Yin (Amazon) asked for a Hazbin Hotel Season 1 sign-ups profile
'from January 19 and February 15, 2024', then 'between January 19 and
February 15, 2024', then 'Jan 19 to Feb 15 2024'. The interpret step
stamped the trailing-12-month default each time and she typed the dates
again in the wizard.

Covered here:
  1. the reader (prometheus.inline_dates) on her three phrasings, plus
     'calendar 2025', 'Q4 2025', 'the first 28 days after January 19
     2024', and the shapes around them; a no-date ask reads nothing
  2. the binder chain in app.py: the stated window lands on the draft,
     explicit, labeled, routed to the engine window; the standing
     default never overwrites it; a no-date ask keeps the default and
     stays non-explicit
  3. order in the chain: a relative phrase and a chained 'Date range:'
     answer outrank the dates in the original ask; a coarse mention (a
     bare year) never replaces a finer explicit window the model
     resolved; a re-ask without the dates inherits them from the turn
     that stated them; a derived cut routes the window onto its cut
     fields and echoes it in the cut label
  4. the interpret prompt carries the stated window ahead of the model
  5. the brief flow (intake_reader.find_windows, bpiq_wizard.parse_dates)
     reads the same phrasings
"""
from __future__ import annotations

import datetime
import os
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

from prometheus import inline_dates as D  # noqa: E402

TODAY = datetime.date(2026, 11, 8)
GRACE = (
    "Build a profile of Hazbin Hotel Season 1 sign-ups from January 19 and February 15, 2024",
    "Hazbin Hotel Season 1 sign-ups between January 19 and February 15, 2024",
    "Hazbin Hotel Season 1 sign-ups Jan 19 to Feb 15 2024",
)
NO_DATES = "Build a profile of Hazbin Hotel Season 1 sign-ups"


def _win(text):
    return D.parse_inline_window(text, today=TODAY)


class ReaderTests(unittest.TestCase):
    def test_grace_three_phrasings(self):
        for t in GRACE:
            w = _win(t)
            self.assertIsNotNone(w, t)
            self.assertEqual((w['start'], w['end']), ('2024-01-19', '2024-02-15'), t)
            self.assertEqual(w['kind'], 'range', t)
            self.assertFalse(w['coarse'], t)

    def test_missing_year_on_the_first_date_comes_from_the_second(self):
        w = _win("from December 15 to January 10, 2024")
        self.assertEqual((w['start'], w['end']), ('2023-12-15', '2024-01-10'))
        w = _win("from January 19, 2024 to February 15")
        self.assertEqual((w['start'], w['end']), ('2024-01-19', '2024-02-15'))

    def test_calendar_year_quarter_half(self):
        w = _win("Hazbin Hotel sign-ups for calendar 2025")
        self.assertEqual((w['start'], w['end'], w['kind'], w['coarse']), ('2025-01-01', '2025-12-31', 'year', True))
        w = _win("Hazbin Hotel sign-ups in Q4 2025")
        self.assertEqual((w['start'], w['end'], w['kind'], w['coarse']), ('2025-10-01', '2025-12-31', 'quarter', True))
        self.assertEqual((_win("the fourth quarter of 2025")['start'], _win("the fourth quarter of 2025")['end']), ('2025-10-01', '2025-12-31'))
        self.assertEqual((_win("the 2025 calendar year")['start'], _win("the 2025 calendar year")['end']), ('2025-01-01', '2025-12-31'))
        self.assertEqual((_win("H1 2025 subscribers")['start'], _win("H1 2025 subscribers")['end']), ('2025-01-01', '2025-06-30'))
        self.assertEqual((_win("sign-ups in May 2024")['start'], _win("sign-ups in May 2024")['end']), ('2024-05-01', '2024-05-31'))

    def test_first_28_days_after_a_date(self):
        w = _win("Hazbin Hotel sign-ups in the first 28 days after January 19 2024")
        self.assertEqual((w['start'], w['end'], w['kind']), ('2024-01-19', '2024-02-15', 'span'))
        self.assertFalse(w['coarse'])
        w = _win("the two weeks after January 19 2024")
        self.assertEqual((w['start'], w['end']), ('2024-01-19', '2024-02-01'))
        w = _win("45 days from January 19 2024")
        self.assertEqual((w['start'], w['end']), ('2024-01-19', '2024-03-03'))

    def test_other_shapes(self):
        self.assertEqual((_win("January 19-21, 2024 viewers")['start'], _win("January 19-21, 2024 viewers")['end']), ('2024-01-19', '2024-01-21'))
        self.assertEqual((_win("from 1/19/2024 to 2/15/2024")['start'], _win("from 1/19/2024 to 2/15/2024")['end']), ('2024-01-19', '2024-02-15'))
        self.assertEqual((_win("sign-ups 1/19 - 2/15/2024")['start'], _win("sign-ups 1/19 - 2/15/2024")['end']), ('2024-01-19', '2024-02-15'))
        self.assertEqual((_win("viewers from 2024-01-19 through 2024-02-15")['start'], _win("viewers from 2024-01-19 through 2024-02-15")['end']), ('2024-01-19', '2024-02-15'))
        self.assertEqual((_win("from April to June 2024")['start'], _win("from April to June 2024")['end']), ('2024-04-01', '2024-06-30'))
        self.assertEqual((_win("between 2023 and 2024")['start'], _win("between 2023 and 2024")['end']), ('2023-01-01', '2024-12-31'))
        w = _win("sign-ups since March 2026")
        # an open end lands on yesterday, never on the day in progress (2026-11-10)
        self.assertEqual((w['start'], w['end'], w['kind']), ('2026-03-01', '2026-11-07', 'since'))
        w = _win("sign-ups from March 3 2026 to date")
        self.assertEqual((w['start'], w['end']), ('2026-03-03', '2026-11-07'))
        w = _win("How many US people activated a new Prime membership between October 29 2025 and November 25, 2025 and watched Hazbin Hotel Season 2 in that window?")
        self.assertEqual((w['start'], w['end']), ('2025-10-29', '2025-11-25'))
        # both years missing: the most recent occurrence that has ended
        self.assertEqual((_win("Jan 19 to Feb 15")['start'], _win("Jan 19 to Feb 15")['end']), ('2026-01-19', '2026-02-15'))
        self.assertEqual((D.parse_inline_window("Jan 19 to Feb 15", today=datetime.date(2026, 1, 25))['start']), '2025-01-19')

    def test_nothing_read_where_no_window_is_stated(self):
        for t in (NO_DATES, "Season 1 to Season 2", "the 2023-2024 season", "the show that premiered in January 2024",
                  "the January 2024 premiere audience", "Dune 2024 viewers", "Hazbin Hotel (2024) sign-ups",
                  "Super Bowl 2025 viewers", "1/2 to 3/4 of viewers", "Top 10 to 20 titles", "it may 20 people", ""):
            self.assertIsNone(_win(t), t)

    def test_label(self):
        self.assertEqual(D.window_label('2024-01-19', '2024-02-15'), 'Jan 19 2024 to Feb 15 2024')
        self.assertEqual(D.window_label('2024-01-19', '2024-01-19'), 'Jan 19 2024')
        self.assertEqual(D.window_label('', None), '')


class BinderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as A
        cls.A = A
        cls.ds, cls.de = A._standing_default_dates()

    def _draft(self, decision='new_build', **kw):
        d = {'subject': 'Hazbin Hotel Season 1 sign-ups', 'decision': decision,
             'date_range': {'start': self.ds, 'end': self.de}, 'date_range_explicit': False}
        d.update(kw)
        return d

    def test_grace_phrasings_bind_the_window(self):
        for t in GRACE:
            d = self._draft()
            bound = self.A._bind_explicit_windows(d, t, decision='new_build')
            self.assertEqual(bound, 'inline', t)
            self.assertEqual(d['date_range'], {'start': '2024-01-19', 'end': '2024-02-15'}, t)
            self.assertTrue(d['date_range_explicit'], t)
            self.assertEqual(d['date_window_label'], 'Jan 19 to Feb 15, 2024', t)
            self.assertEqual(d['engine_date_range'], '2024-01-19 TO 2024-02-15', t)
            # the standing default never writes over it, however often it runs
            self.assertFalse(self.A._apply_standing_default_window(d), t)
            self.assertEqual(d['date_range'], {'start': '2024-01-19', 'end': '2024-02-15'}, t)
            # the confirm copy's window field is the stated window
            self.assertEqual(self.A._draft_window_field(d, 'new_build'), 'Jan 19 to Feb 15, 2024')
            self.assertEqual(self.A._proposed_range_from_drafts([d]), {'start': '2024-01-19', 'end': '2024-02-15'})

    def test_calendar_quarter_and_span_bind(self):
        want = {"Hazbin Hotel sign-ups for calendar 2025": ('2025-01-01', '2025-12-31'),
                "Hazbin Hotel sign-ups in Q4 2025": ('2025-10-01', '2025-12-31'),
                "Hazbin Hotel sign-ups in the first 28 days after January 19 2024": ('2024-01-19', '2024-02-15')}
        for t, (s, e) in want.items():
            d = self._draft()
            self.assertEqual(self.A._bind_explicit_windows(d, t, decision='new_build'), 'inline', t)
            self.assertEqual(d['date_range'], {'start': s, 'end': e}, t)
            self.assertTrue(d['date_range_explicit'], t)
            self.assertEqual(d['engine_date_range'], f"{s} TO {e}", t)

    def test_no_date_ask_keeps_the_default_and_is_not_explicit(self):
        d = self._draft(date_range={})
        self.assertEqual(self.A._bind_explicit_windows(d, NO_DATES, decision='new_build'), '')
        self.assertEqual(d['date_range'], {'start': self.ds, 'end': self.de})
        self.assertFalse(d.get('date_range_explicit'))
        self.assertEqual(d['engine_date_range'], f"{self.ds} TO {self.de}")
        self.assertNotIn('date_window_label', d)
        # a leftover fiscal draft is rewritten to the default, still not explicit
        d = self._draft(date_range={'start': '2025-07-01', 'end': '2026-06-30'})
        self.assertEqual(self.A._bind_explicit_windows(d, NO_DATES, decision='new_build'), '')
        self.assertEqual(d['date_range'], {'start': self.ds, 'end': self.de})
        self.assertFalse(d.get('date_range_explicit'))

    def test_relative_and_chained_answers_outrank_inline_dates(self):
        d = self._draft()
        self.assertEqual(self.A._bind_explicit_windows(d, "Hazbin Hotel sign-ups trailing 60 days", decision='new_build'), 'relative')
        self.assertEqual(d['date_range']['end'], self.de)
        d = self._draft()
        self.assertEqual(self.A._bind_explicit_windows(
            d, "Hazbin Hotel sign-ups Jan 19 to Feb 15 2024. Date range: 2025-03-01 TO 2025-03-31", decision='new_build'), 'shared')
        self.assertEqual(d['date_range'], {'start': '2025-03-01', 'end': '2025-03-31'})

    def test_a_coarse_mention_keeps_a_finer_explicit_window(self):
        d = self._draft(date_range={'start': '2025-02-09', 'end': '2025-02-09'}, date_range_explicit=True)
        self.assertEqual(self.A._bind_explicit_windows(d, "Super Bowl viewers in 2025", decision='new_build'), '')
        self.assertEqual(d['date_range'], {'start': '2025-02-09', 'end': '2025-02-09'})
        # a two-sided range in the ask binds whatever the model drafted
        d = self._draft(date_range={'start': '2026-01-19', 'end': '2026-02-15'}, date_range_explicit=True)
        self.assertEqual(self.A._bind_explicit_windows(d, GRACE[0], decision='new_build'), 'inline')
        self.assertEqual(d['date_range'], {'start': '2024-01-19', 'end': '2024-02-15'})

    def test_a_re_ask_inherits_the_stated_window(self):
        hist = [{'role': 'user', 'text': GRACE[2]}, {'role': 'agent', 'text': 'Which window?'}]
        d = self._draft()
        self.assertEqual(self.A._bind_explicit_windows(d, "do the same for Helluva Boss", chat_history=hist, decision='new_build'), 'inline')
        self.assertEqual(d['date_range'], {'start': '2024-01-19', 'end': '2024-02-15'})
        # an explicit draft is left alone when only history carries dates
        d = self._draft(date_range={'start': '2025-01-01', 'end': '2025-12-31'}, date_range_explicit=True)
        self.assertEqual(self.A._bind_explicit_windows(d, "do the same for Helluva Boss", chat_history=hist, decision='new_build'), '')
        self.assertEqual(d['date_range'], {'start': '2025-01-01', 'end': '2025-12-31'})

    def test_derived_cut_routes_onto_the_cut_fields(self):
        d = {'subject': 'Hazbin Hotel', 'decision': 'derive_cut', 'cut_label': 'Female', 'date_range': {}}
        self.assertEqual(self.A._bind_explicit_windows(d, "Hazbin Hotel female cut between January 19 and February 15, 2024", decision='derive_cut'), 'inline')
        self.assertEqual(d['cut_date_range'], '2024-01-19 TO 2024-02-15')
        self.assertEqual(d['cut_window_label'], 'Jan 19 to Feb 15, 2024')
        self.assertEqual(d['cut_label'], 'Female, Jan 19 to Feb 15, 2024')
        self.assertEqual(d['cut_label_clean'], 'Female')
        self.assertNotIn('engine_date_range', d)

    def test_interpret_prompt_carries_the_stated_window(self):
        from prometheus.legacy import chat as C
        sp, _ = C._synth_chat_interpret_prompts(GRACE[0], [], {}, [], None)
        self.assertIn('STATED WINDOW', sp)
        self.assertIn('2024-01-19 to 2024-02-15', sp)
        self.assertIn('January 19 and February 15, 2024', sp)
        sp, _ = C._synth_chat_interpret_prompts(NO_DATES, [], {}, [], None)
        self.assertNotIn('STATED WINDOW', sp)
        self.assertIn('DEFAULT DATE RANGE', sp)


class BriefFlowTests(unittest.TestCase):
    def test_intake_reader_reads_the_phrasings(self):
        from prometheus import intake_reader as IR
        for t in GRACE:
            self.assertEqual(IR.find_windows(t), {'event_start': '2024-01-19', 'event_end': '2024-02-15'}, t)
        self.assertEqual(IR.find_windows("the first 28 days after January 19 2024"), {'event_start': '2024-01-19', 'event_end': '2024-02-15'})
        self.assertEqual(IR.find_windows("Q4 2025"), {'event_start': '2025-10-01', 'event_end': '2025-12-31'})
        self.assertEqual(IR.find_windows(NO_DATES), {})
        # the shapes it already read are unchanged
        self.assertEqual(IR.find_windows("Apr 2024 - Dec 2024, post through Jun 2025"),
                         {'event_start': '2024-04-01', 'event_end': '2024-12-31', 'post_end': '2025-06-30', 'post_start': '2024-12-31'})

    def test_wizard_parse_dates(self):
        from prometheus import bpiq_wizard as W
        for t in GRACE:
            self.assertEqual(W.parse_dates(t, today=TODAY), ('2024-01-19', '2024-02-15'), t)
        self.assertEqual(W.parse_dates("October 7 - 28", today=TODAY), ('2026-10-07', '2026-10-28'))
        self.assertEqual(W.parse_dates("no idea", today=TODAY), (None, None))


if __name__ == '__main__':
    unittest.main(verbosity=2)
