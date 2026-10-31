#!/usr/bin/env python3
"""The priced gate puts a price only on words that name an audience, and
a misspelling of the thread's subject is the thread's subject (audit of
the 2026-10-19 to 10-30 ask log: quotes on "see waht the", "high level
on this", "Name of show", "Unique", "The 8,102,006 Mentions"; $500 three
times on "The Love Hypotheis" for a follow-up). Hermetic."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import priced_read as P  # noqa: E402

LH_HIST = [{'role': 'user', 'text': 'Among people who watched The Love Hypothesis on Prime Video, what share engaged with BookTok?'},
           {'role': 'agent', 'text': 'On The Love Hypothesis - The Love Hypothesis Prime Video viewers, BookTok exposed: 31.6% came first.',
            'meta': {'subject': 'The Love Hypothesis', 'priced_paid': {'subject': 'The Love Hypothesis', 'kind': 'report'}}}]


class Junk(unittest.TestCase):
    def test_not_subjects(self):
        for t in ['see waht the', 'high level on this', 'Name of show', 'Unique',
                  'What About Gender Age and International', 'The 8,102,006 Mentions', 'DMA', 'just',
                  'Viewership Trends', 'Ad Campaign Related', 'where do i see the answer']:
            self.assertFalse(P.looks_like_subject(t), t)

    def test_subjects(self):
        for t in ['Reba McEntire', 'UVA', 'Godzilla Minus One', 'Chipotle buyers who are avid gamers',
                  'market size for prank content', 'Babylon 5 audience on Prime Video', 'cost of living',
                  'Mutiny', 'people pirated the film mutiny', 'Kevin Langue', 'K-pop fans in the US']:
            self.assertTrue(P.looks_like_subject(t), t)


class Gate(unittest.TestCase):
    def test_junk_gets_the_question_not_a_price(self):
        state, pl = P.offer_or_charge(text='and see waht the sample size', subject='see waht the', kind='sizing',
                                      panel_confirm=None, username='eriley', user={'username': 'eriley'}, history=[])
        self.assertEqual(state, 'offer')
        self.assertTrue(pl.get('subject_unclear'))
        self.assertNotIn('Pulling this data will cost', pl['reply'])
        self.assertIn('Which subject', pl['reply'])
        self.assertNotIn('panel_offer', pl)

    def test_misspelled_thread_subject_is_a_metered_follow_up(self):
        self.assertEqual(P.snap_to_thread_subject('The Love Hypotheis', LH_HIST), 'The Love Hypothesis')
        state, pl = P.offer_or_charge(text='is the film you started with The Love Hypotheis, should Off Campus be on the list?',
                                      subject='The Love Hypotheis', kind='report', panel_confirm=None,
                                      username='jaypatel14', user={'username': 'jaypatel14'}, history=LH_HIST)
        self.assertEqual(state, 'charged')
        self.assertTrue(pl.get('metered'))
        self.assertEqual(pl.get('subject'), 'The Love Hypothesis')

    def test_a_real_new_subject_still_quotes(self):
        state, pl = P.offer_or_charge(text='how big is the Godzilla Minus One audience', subject='Godzilla Minus One',
                                      kind='sizing', panel_confirm=None, username='x', user={'username': 'x'}, history=LH_HIST)
        self.assertEqual(state, 'offer')
        self.assertIn('panel_offer', pl)
        self.assertEqual(pl['panel_offer']['subject'], 'Godzilla Minus One')

    def test_a_confirm_is_never_second_guessed(self):
        # the chip came back with the subject the quote carried; the gate does not re-read it
        state, pl = P.offer_or_charge(text='x', subject='Godzilla Minus One', kind='sizing',
                                      panel_confirm={'subject': 'Godzilla Minus One', 'kind': 'sizing', 'resume_charge': {'ok': True}},
                                      username='x', user={'username': 'x'}, history=[])
        self.assertEqual(state, 'charged')


if __name__ == '__main__':
    unittest.main(verbosity=1)
