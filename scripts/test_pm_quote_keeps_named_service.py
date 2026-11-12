#!/usr/bin/env python3
"""A quote never swaps a named service silently (Jenna 2026-11-12).

On prod, "How many people watched Fallout Season 2 on Netflix?" was
quoted "I can pull how many people in the US watched Fallout Season 2 on
Prime Video across one year. Pulling this data will cost $100." The
profile on record is the title on Prime Video; the quote printed the
record's name and the service the reader named was gone.

Holds, at the one place a quote is made (priced_read.offer_or_charge,
through prometheus.quote_service):
  1. the record shows the title's only US home is another service: the
     quote stays on that home and opens with one plain sentence;
  2. the record cannot settle it (several homes, no check, a check that
     does not cover the dates the words state): the quote is on what was
     asked;
  3. a question that names the service the subject is on, or no service,
     is quoted as before;
  4. nothing here changes a price.

Hermetic: the carrier record is handed in. No S3, no model, no app.
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import insights_ledger as IL  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
from prometheus import quote_service as QS  # noqa: E402
from prometheus import service_scope_match as SSM  # noqa: E402

FALLOUT = 'Fallout Season 2 Viewers on Prime Video'
WALTER = 'My Life with the Walter Boys Viewers on Netflix'
ONLY_AMAZON = {'carriers': {'services': ['amazon'], 'window': {'start': '2025-11-01', 'end': '2026-10-31'}}}
ONLY_NETFLIX = {'carriers': {'services': ['netflix'], 'window': {'start': '2025-11-10', 'end': '2026-11-10'}}}
TWO_HOMES = {'carriers': {'services': ['amazon', 'netflix'], 'window': {'start': '2025-11-01', 'end': '2026-10-31'}}}
NO_CHECK = {}


class TheRule(unittest.TestCase):
    def test_the_record_settles_it_and_the_quote_says_so(self):
        subj, line = QS.settle('How many people watched Fallout Season 2 on Netflix?', [], FALLOUT, record=ONLY_AMAZON)
        self.assertEqual((subj, line), (FALLOUT, 'Fallout Season 2 is on Prime Video in the US, not Netflix.'))
        subj, line = QS.settle('What did My Life with the Walter Boys viewers on Hulu watch next?', [], WALTER, record=ONLY_NETFLIX)
        self.assertEqual((subj, line), (WALTER, 'My Life with the Walter Boys is on Netflix in the US, not Hulu.'))

    def test_the_record_cannot_settle_it_so_the_quote_is_on_what_was_asked(self):
        q = 'How many people watched Fallout Season 2 on Netflix?'
        for rec in (TWO_HOMES, NO_CHECK):
            subj, line = QS.settle(q, [], FALLOUT, record=rec)
            self.assertEqual((subj, line), ('Fallout Season 2 Viewers on Netflix', ''), rec)
        # dates the words state that the check does not cover: unverified is never a single home
        subj, line = QS.settle('How many people watched Fallout Season 2 on Netflix from August 1, 2023 to October 31, 2023?', [], FALLOUT,
                               record=ONLY_AMAZON)
        self.assertEqual((subj, line), ('Fallout Season 2 Viewers on Netflix', ''))

    def test_the_reader_named_two_and_the_subject_is_on_one(self):
        q = 'What did Fallout Season 2 viewers on Netflix and Prime Video watch next?'
        subj, line = QS.settle(q, [], FALLOUT, record=ONLY_AMAZON)
        self.assertEqual((subj, line), (FALLOUT, 'Fallout Season 2 is on Prime Video in the US, not Netflix.'))
        subj, line = QS.settle(q, [], FALLOUT, record=TWO_HOMES)
        self.assertEqual((subj, line), ('Fallout Season 2 Viewers on Prime Video and Netflix', ''))

    def test_what_is_left_alone(self):
        for q in ('How many people watched Fallout Season 2 on Prime Video?', 'How many people watched Fallout Season 2?',
                  'What do Fallout Season 2 viewers buy most often?'):
            self.assertEqual(QS.settle(q, [], FALLOUT, record=ONLY_AMAZON), (FALLOUT, ''), q)
        # a subject with no service in its name is not this rule's business
        self.assertEqual(QS.settle('What do Costco shoppers buy on Netflix?', [], 'Costco', record=NO_CHECK), ('Costco', ''))
        self.assertEqual(QS.settle('anything', [], '', record=NO_CHECK), ('', ''))
        self.assertEqual(QS.names({'amazon', 'netflix', 'hulu'}), 'Prime Video, Hulu and Netflix')


class TheQuote(unittest.TestCase):
    def setUp(self):
        self._load, self._incl, self._home = IL._load_index, PR.included_with_plan, QS._home
        IL._load_index = lambda force=False: {'subjects': {}}
        PR.included_with_plan = lambda user, username=None, host=None: False
        self.record = ONLY_AMAZON
        QS._home = lambda ssm, title, text, record, window: self._home(ssm, title, text, self.record, window)

    def tearDown(self):
        IL._load_index, PR.included_with_plan, QS._home = self._load, self._incl, self._home

    def _quote(self, q, subject=FALLOUT, kind='viewership'):
        st, pl = PR.offer_or_charge(text=q, subject=subject, kind=kind, panel_confirm=None, username='payer_s',
                                    user={'username': 'payer_s'}, history=[])
        self.assertEqual(st, 'offer')
        return pl

    def test_the_fallout_quote_before_and_after(self):
        pl = self._quote('How many people watched Fallout Season 2 on Netflix?')
        self.assertEqual(pl['reply'], 'Fallout Season 2 is on Prime Video in the US, not Netflix. I can pull how many people in the US '
                                      'watched Fallout Season 2 on Prime Video across one year. Pulling this data will cost $100.')
        self.assertEqual(pl['panel_offer']['subject'], FALLOUT)             # what is priced is the read on the title's home

    def test_a_count_we_hold_on_the_titles_home_is_given_not_sold(self):
        """Asked with no service, the same question is answered from the profile on the dashboard. Naming the wrong
        service must not turn that into a $100 quote: the sentence, then the figure."""
        from prometheus import catalog_lane as CL
        held = ("Fallout Season 2 Viewers on Prime Video: 13,732,978 US people (Nov 1, 2025 to Oct 31, 2026). Prime Video is the only "
                "US home of Fallout Season 2 in this window, so this is the title's whole audience.")
        asked = []
        real = CL.answer

        def lane(text, ctx=None):
            asked.append(text)
            return {'success': True, 'action': 'answer', 'reply': held, 'followups': ['Open Fallout Season 2 Viewers on Prime Video'],
                    'offer_deck': False, 'deck_angle': None, 'catalog_lookup': True, 'subject': FALLOUT}
        try:
            CL.answer = lane
            pl = self._quote('How many people watched Fallout Season 2 on Netflix?')
            self.assertEqual(pl['reply'], 'Fallout Season 2 is on Prime Video in the US, not Netflix. ' + held)
            self.assertNotIn('panel_offer', pl)                              # nothing is quoted
            self.assertNotIn('$', pl['reply'])
            self.assertEqual(asked, ['How many people watched Fallout Season 2?'])   # the same question, the wrong service left out
            # a read that is not a count is still a read: the sentence, then the quote on the title's home
            pl = self._quote('What do Fallout Season 2 viewers on Netflix buy most often?', kind='list')
            self.assertTrue(pl['reply'].startswith('Fallout Season 2 is on Prime Video in the US, not Netflix. '))
            self.assertIn('will cost', pl['reply'])
            self.assertEqual(pl['panel_offer']['subject'], FALLOUT)
            # the words state their own dates: not the profile's year, so it is quoted
            pl = self._quote('How many people watched Fallout Season 2 on Netflix between January 5, 2026 and February 1, 2026?')
            self.assertIn('panel_offer', pl)
            # the lane holds nothing, or answers without a figure: the quote stands
            CL.answer = lambda text, ctx=None: None
            self.assertIn('panel_offer', self._quote('How many people watched Fallout Season 2 on Netflix?'))
            CL.answer = lambda text, ctx=None: {'success': True, 'reply': 'No profile on the dashboard yet.', 'subject': FALLOUT}
            self.assertIn('panel_offer', self._quote('How many people watched Fallout Season 2 on Netflix?'))
            # the record cannot settle it: nothing is said about another service, and the quote is on what was asked
            CL.answer = lane
            self.record = TWO_HOMES
            pl = self._quote('How many people watched Fallout Season 2 on Netflix?')
            self.assertIn('panel_offer', pl)
            self.assertNotIn('is on Prime Video in the US', pl['reply'])
        finally:
            CL.answer = real

    def test_with_no_record_to_settle_it_the_quote_is_on_netflix(self):
        self.record = NO_CHECK
        pl = self._quote('How many people watched Fallout Season 2 on Netflix?')
        self.assertEqual(pl['reply'], 'I can pull how many people in the US watched Fallout Season 2 on Netflix across one year. '
                                      'Pulling this data will cost $100.')
        self.assertNotIn('Prime Video', pl['reply'])
        self.assertEqual(pl['panel_offer']['subject'], 'Fallout Season 2 Viewers on Netflix')

    def test_the_same_service_and_no_service_quote_as_before(self):
        for q in ('How many people watched Fallout Season 2 on Prime Video?', 'How many people watched Fallout Season 2?'):
            pl = self._quote(q)
            self.assertEqual(pl['reply'], 'I can pull how many people in the US watched Fallout Season 2 on Prime Video across one year. '
                                          'Pulling this data will cost $100.', q)

    def test_the_price_is_the_same_every_way(self):
        prices = set()
        for rec in (ONLY_AMAZON, NO_CHECK, TWO_HOMES):
            self.record = rec
            for q in ('How many people watched Fallout Season 2 on Netflix?', 'How many people watched Fallout Season 2 on Prime Video?'):
                pl = self._quote(q)
                prices.add((pl['panel_offer'].get('label'), pl['panel_offer'].get('credits'), pl['panel_offer'].get('kind')))
        self.assertEqual(len(prices), 1, prices)

    def test_on_the_approve_the_subject_is_the_one_that_was_quoted(self):
        pl = self._quote('How many people watched Fallout Season 2 on Netflix?')
        charged = []
        st, ch = PR.offer_or_charge(text='How many people watched Fallout Season 2 on Netflix?', subject=pl['panel_offer']['subject'],
                                    kind='viewership', panel_confirm={'subject': pl['panel_offer']['subject']}, username='payer_s',
                                    user={'username': 'payer_s'}, history=[], consume=lambda *a, **k: charged.append(k) or True)
        self.assertEqual(st, 'charged')
        self.assertEqual(ch['subject'], FALLOUT)
        self.assertEqual(len(charged), 1)


if __name__ == '__main__':
    unittest.main(verbosity=1)
