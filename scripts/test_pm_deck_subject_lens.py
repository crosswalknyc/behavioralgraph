#!/usr/bin/env python3
"""A deck ask that names the open profile through a lens ("build a deck
with the wicked tuna demographics", "a deck on the demographics of the
profile wicked tuna viewers") resolves to that profile and asks nothing
(Armine Meseldzhyan at Lionsgate, 2026-10-31: the first drew "Which
subject should the deck be on: Tubi, New Heights ... or WICKED TUNA
Viewers?", the second hung 17 minutes in the fuzzy fallback and came
back as the calm failure line). Hermetic."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import prometheus_analysis as pma  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402

PAGE = 'WICKED TUNA Viewers'
HIST = [{'role': 'user', 'text': 'can you give the summary of the comparison between New Heights podcast listeners and Tubi platform'},
        {'role': 'agent', 'text': 'On New Heights podcast listeners vs Tubi - New Heights listeners who also use Tubi: 7,547,413 US listeners.',
         'meta': {'subject': 'Tubi'}}]


class Brief(unittest.TestCase):
    def test_lens_shapes_name_the_subject(self):
        self.assertEqual(pma.extract_deck_brief('build a deck with the wicked tuna demogrpahics')['subject'].lower(), 'wicked tuna')
        self.assertEqual(pma.extract_deck_brief('can we build a deck on the demographics of the profile wicked tuna viewers')['subject'].lower(), 'wicked tuna viewers')
        self.assertEqual(pma.extract_deck_brief('build a deck of the interests and behaviors of Godzilla Minus One viewers')['subject'], 'Godzilla Minus One viewers')
        self.assertEqual(pma.extract_deck_brief('make me a deck on the New Heights podcast demographics')['subject'], 'New Heights podcast')

    def test_older_shapes_unchanged(self):
        self.assertEqual(pma.extract_deck_brief('build the Paige Bueckers insights deck')['subject'], 'Paige Bueckers')
        self.assertEqual(pma.extract_deck_brief('Build a deck from this data')['subject'], '')
        self.assertEqual(pma.extract_deck_brief('build a deck')['subject'], '')
        b = pma.extract_deck_brief('build a deck on Reba McEntire for the Lexus pitch')
        self.assertEqual((b['subject'], b['partner']), ('Reba McEntire', 'Lexus'))


class PageNamed(unittest.TestCase):
    def setUp(self):
        self._orig = SR.bound_thread_subject
        SR.bound_thread_subject = lambda: PAGE

    def tearDown(self):
        SR.bound_thread_subject = self._orig

    def test_words_name_page(self):
        self.assertTrue(SR.words_name_page('build a deck with the wicked tuna demogrpahics', PAGE))
        self.assertTrue(SR.words_name_page('make a deck on New Heights', 'New Heights Podcast Listeners'))
        self.assertFalse(SR.words_name_page('build a deck from this data', PAGE))
        self.assertFalse(SR.words_name_page('build a deck on Tubi', PAGE))

    def test_no_question_when_the_words_name_the_page(self):
        self.assertIsNone(SR.open_screen_question('build a deck with the wicked tuna demogrpahics', PAGE, HIST, deck=True, what='the deck'))

    def test_question_still_asked_when_nothing_is_named(self):
        q = SR.open_screen_question('build a deck from this data', PAGE, HIST, deck=True, what='the deck')
        self.assertIsNotNone(q)
        self.assertIn('Which subject should the deck be on', q['reply'])


class Wiring(unittest.TestCase):
    def test_fuzzy_fallback_gates_the_shortlist_only(self):
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        i = src.index('def _pm_deck_fuzzy_suggestions(')
        body = src[i:i + 3500]
        self.assertIn('for c in pool[:40]:', body)
        self.assertNotIn('for c in (catalog or []):\n        k = str((c or {}).get(\'s3_key\')', body)
        self.assertIn("if _bind_pick and body.get('confirm_open_screen'):", src)
        html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()
        self.assertIn("_dbody.bind_subject = String(bindSubject)", html)
        self.assertIn("_mcTyped === _oSubj", html)


if __name__ == '__main__':
    unittest.main(verbosity=1)
