#!/usr/bin/env python3
"""A list of companies is a list of companies (Jonathan Wiener, Boardroom,
2026-11-12).

With the Boardroom TV profile open he asked how it indexes against seven
companies, three of them written with the parent in brackets
("Hennessy [Moet-Hennessy]"). The brackets were read as blanks he had
not filled in and he was asked "which three companies?". He answered
"all 10 of these" and the list; the words "of these" made it a split of
an audience called "10" across services, which was quoted, approved,
charged and came back with nothing. He got "Working on it ... I will
email you". He then asked twice how much longer it would take and was
told both times about two profiles that finished a week earlier.

Hermetic: no model, no S3, no ledger. Covered, from his exact words:
  1. a bracket after a name is a note on the name, not a blank
  2. real blanks are still blanks
  3. "all 10 of these <names>" is the names, never a cross
  4. a genuine cross still parses
  5. a cross the record cannot answer is never quoted
  6. "how much longer" is a status ask; a product question is not
  7. a person waiting on an open promise is told about that promise
  8. an ask that names an older job still gets that job
  9. the status lane answers from the promise before the job stores
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import cross_sizing as CX  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
from prometheus import referents as RF  # noqa: E402
from prometheus import status_lane as SL  # noqa: E402

FIRST = ('for the boardroom profile iq can you find how boardroom indexes against these companies - Diageo \nBacardi \n'
         'Hennessy [Moet-Hennessy]\nWhite Claw [Mark Anthony]\nHigh Noon [GALLO]\nSuntory Global \nWilliam Grant & Sons')
SECOND = ('all 10 of these \nDiageo \nBacardi \nHennessy \nMoet-Hennessy\nWhite Claw \nMark Anthony\nHigh Noon \nGALLO\n'
          'Suntory Global \nWilliam Grant & Sons')
WAIT_1 = 'how much longer will it take'
WAIT_2 = 'i am asking how much longer my latest request for the index score for the boardroom profile iq across 10 comapnies'
CALM = 'Working on it. Confirmed your task is in progress and I will email you when it completes.'
CTX = {'view': 'profile-iq', 'primary': {'s3_key': 'Boardroom_TV_06_03_2026_18_32.csv', 'name': 'Boardroom TV'}}
OLD_JOBS = [{'kind': 'profile', 'subject': 'Rich Kleiman', 'status': 'done', 'pct': 100, 'updated': '2026-11-05T18:00:00Z'},
            {'kind': 'profile', 'subject': 'Speedy Morman', 'status': 'done', 'pct': 100, 'updated': '2026-11-05T19:00:00Z'}]


def _thread(*extra):
    base = [
        {'role': 'user', 'text': FIRST, 'ts': '2026-11-12T20:08:00Z'},
        {'role': 'agent', 'text': 'Which three companies should I use?', 'ts': '2026-11-12T20:08:05Z', 'meta': {}},
        {'role': 'user', 'text': SECOND, 'ts': '2026-11-12T20:10:00Z'},
        {'role': 'agent', 'text': 'I can pull that read. Pulling this data will cost $100.', 'ts': '2026-11-12T20:10:05Z', 'meta': {}},
        {'role': 'user', 'text': 'Approve', 'ts': '2026-11-12T20:11:00Z'},
        {'role': 'agent', 'text': CALM, 'ts': '2026-11-12T20:11:11Z',
         'meta': {'held_ref': 'system/ops/held_replies/2026-11-12/201112_15427a58fd.json'}},
    ]
    return base + list(extra)


class BracketNotes(unittest.TestCase):
    def test_parent_in_brackets_is_a_note(self):
        self.assertEqual(RF.placeholders(FIRST), [])
        self.assertEqual(RF.name_notes(FIRST), ['[Moet-Hennessy]', '[Mark Anthony]', '[GALLO]'])
        self.assertIsNone(RF.detect_unresolved(FIRST))

    def test_other_notes_on_names(self):
        for t in ('How big is the audience for Stranger Things [Netflix] this year',
                  'Compare Google and Alphabet [GOOGL] buyers'):
            self.assertEqual(RF.placeholders(t), [], t)

    def test_blanks_are_still_blanks(self):
        for t in ('Size the audience for [a title WWSTA passed on]',
                  'How does Nike index against [Competitor]',
                  'Compare [Brand A] and [Brand B] shoppers',
                  'Build a profile on [Artist]',
                  'What do viewers of [Show Name] buy',
                  'How big is [TBD]',
                  'Who drinks [a cognac brand]'):
            self.assertTrue(RF.placeholders(t), t)

    def test_note_becomes_parentheses_for_the_reader(self):
        out = RF.unbracket_windows('Hennessy [Moet-Hennessy]')
        self.assertNotIn('[', out)
        self.assertIn('Moet-Hennessy', out)


class NamesAreNotACross(unittest.TestCase):
    def test_all_ten_of_these_is_not_a_cross(self):
        self.assertIsNone(CX.parse(SECOND))
        self.assertIsNone(PR.classify(SECOND, None, CTX))

    def test_other_counted_lists(self):
        for t in ('all 5 of these: Nike, Adidas, Puma, Reebok, New Balance',
                  'both of these\nHulu\nPeacock',
                  'each of those 3 - Netflix, Max, Disney+'):
            self.assertIsNone(CX.parse(t), t)

    def test_first_ask_is_a_compiled_read_not_a_cross(self):
        hit = PR.classify(FIRST, None, CTX)
        self.assertTrue(hit)
        self.assertNotEqual(hit.get('kind'), 'cross')

    def test_a_real_cross_still_parses(self):
        for t in ('how many UFC fans are there out there, of those, how many are on Paramount+, on HBO Max and how many have neither service',
                  'NFL fans, of those how many are on Peacock'):
            self.assertTrue(CX.parse(t), t)
            hit = PR.classify(t, None, None)
            self.assertTrue(hit and hit.get('kind') == 'cross', t)


class NoQuoteWithoutAnAnswer(unittest.TestCase):
    Q = 'NFL fans, of those how many are on Peacock'

    def setUp(self):
        CX._DECLINED['text'] = ''

    def test_answerable_is_false_when_nothing_resolves(self):
        with mock.patch.object(CX, 'resolve_base', return_value=None):
            self.assertFalse(CX.answerable(self.Q, None, host=None))

    def test_answerable_fails_open_on_an_error(self):
        with mock.patch.object(CX, 'resolve_base', side_effect=RuntimeError('store down')):
            self.assertTrue(CX.answerable(self.Q, None, host=None))

    def test_unanswerable_cross_is_never_offered_as_a_cross(self):
        offers = []

        def _gate(**kw):
            offers.append(kw.get('kind'))
            return 'offer', {'success': True, 'action': 'answer', 'reply': 'quote', 'followups': []}

        with mock.patch.object(CX, 'answerable', return_value=False), mock.patch.object(PR, 'offer_or_charge', _gate):
            PR.service_turn(self.Q, user={'username': 'tester'}, ctx=None, history=[], host=None)
        self.assertNotIn('cross', offers)
        self.assertTrue(CX.declined(self.Q))
        self.assertFalse(PR._cross_ask(self.Q))

    def test_approved_cross_with_no_answer_is_not_charged(self):
        charged = []

        def _gate(**kw):
            charged.append(kw.get('kind'))
            return 'charged', {'user': 'tester', 'credits': 100}

        pc = {'subject': 'NFL fans', 'kind': 'cross', 'dimension': ''}
        with mock.patch.object(CX, 'answerable', return_value=False), mock.patch.object(PR, 'offer_or_charge', _gate):
            out = PR.service_turn(self.Q, user={'username': 'tester'}, ctx=None, history=[], host=None, panel_confirm=pc)
        self.assertIsNone(out)
        self.assertEqual(charged, [])


class WaitingOnAPromise(unittest.TestCase):
    def test_how_much_longer_is_a_status_ask(self):
        for t in (WAIT_1, WAIT_2, 'when will it be ready', 'any ETA?', 'how long will this take', 'when will i get the email'):
            self.assertTrue(SL.wants_status(t), t)
            self.assertTrue(SL.wants_status_fast(t), t)

    def test_product_questions_are_not(self):
        for t in ('how long does it take to build a profile', 'how long will it take to build a profile on Nike',
                  'how many of them index high', 'how long do Hulu viewers watch'):
            self.assertFalse(SL.is_wait_ask(t), t)

    def test_the_open_promise_is_found_and_names_the_request(self):
        pr = SL.open_promise(_thread())
        self.assertTrue(pr)
        self.assertTrue(pr['question'].startswith('all 10 of these Diageo'))
        self.assertNotEqual(pr['question'].lower(), 'approve')
        self.assertTrue(pr['held_ref'].endswith('201112_15427a58fd.json'))

    def test_a_status_line_does_not_close_the_promise(self):
        hist = _thread({'role': 'user', 'text': WAIT_1, 'ts': '2026-11-12T20:20:00Z'},
                       {'role': 'agent', 'text': 'Rich Kleiman is finished.', 'ts': '2026-11-12T20:20:02Z',
                        'meta': {'status_items': [{'kind': 'profile', 'subject': 'Rich Kleiman'}]}})
        self.assertTrue(SL.open_promise(hist))

    def test_a_landed_answer_closes_it(self):
        hist = _thread({'role': 'agent', 'text': 'On Boardroom TV: ...', 'ts': '2026-11-12T21:00:00Z',
                        'meta': {'landed_at': '2026-11-12T21:00:00Z'}})
        self.assertIsNone(SL.open_promise(hist))

    def test_no_promise_in_a_plain_thread(self):
        self.assertIsNone(SL.open_promise([{'role': 'user', 'text': 'hello'}, {'role': 'agent', 'text': 'Hi.', 'meta': {}}]))

    def _answer(self, text, history):
        with mock.patch.object(SL, '_job_items', return_value=list(OLD_JOBS)), \
                mock.patch.object(SL, '_engine_runs', return_value=[]), \
                mock.patch.object(SL, '_deliveries', return_value=[]):
            return SL.answer(text, 'jonathanw', host=None, user={'username': 'jonathanw', 'email': 'jonathan@boardroom.tv'},
                             history=history)

    def test_the_wait_is_answered_from_the_promise(self):
        for q in (WAIT_1, WAIT_2):
            out = self._answer(q, _thread())
            self.assertTrue(out and out.get('open_promise'), q)
            reply = out['reply']
            self.assertIn('still open', reply)
            self.assertIn('jonathan@boardroom.tv', reply)
            self.assertNotIn('Rich Kleiman', reply)
            self.assertNotIn('Speedy Morman', reply)
            self.assertNotIn('\u2014', reply)
            self.assertNotRegex(reply, r'\b\d+\s*(?:minute|hour|min)\b')      # never a time we cannot keep

    def test_an_ask_that_names_an_older_job_gets_that_job(self):
        out = self._answer('where is my Rich Kleiman profile', _thread())
        self.assertTrue(out)
        self.assertFalse(out.get('open_promise'))
        self.assertIn('Rich Kleiman', out['reply'])

    def test_without_a_promise_the_jobs_are_the_answer(self):
        out = self._answer('where are my builds', [{'role': 'user', 'text': 'hello'}])
        self.assertTrue(out)
        self.assertFalse(out.get('open_promise'))


if __name__ == '__main__':
    unittest.main(verbosity=1)
