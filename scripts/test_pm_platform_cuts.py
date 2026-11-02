#!/usr/bin/env python3
"""One title asked across streaming services is one build (Jay Patel,
Amazon, 2026-11-02; Jenna: "a wuthering heights profile with two cuts
based on streaming service ie so one would be 100% in HBO max and one
would be 100% APV"). Hermetic."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import platform_cuts as PC  # noqa: E402

JAY = ('can you make three distinct profiles\n\n'
       '1/ customers who watched "Wuthering Heights" on a _any_ streaming service?\n\n'
       '2/ customers who watched it on Amazon Prime Video?\n\n'
       '3/ customers who watched it on HBO Max?\n\n'
       'talk me through how you will build these 3 reports, including any relevant queries/filters')


class Detect(unittest.TestCase):
    def test_jays_ask_is_one_title_with_two_service_cuts(self):
        h = PC.detect(JAY)
        self.assertIsNotNone(h)
        self.assertEqual(h['title'], 'Wuthering Heights')
        self.assertEqual(h['platforms'], ['Amazon Prime Video', 'HBO Max'])
        self.assertTrue(h['any'])
        self.assertTrue(h['method_question'])
        self.assertEqual(h['items'], 3)

    def test_inline_and_noun_first_shapes(self):
        h = PC.detect('build profiles of people who watched Landman on Paramount+ and people who watched it on Prime Video, trailing 12 months')
        self.assertEqual((h['title'], h['platforms']), ('Landman', ['Paramount+', 'Amazon Prime Video']))
        h = PC.detect('1. viewers of The Bear on Hulu\n2. viewers of The Bear on Disney+')
        self.assertEqual((h['title'], h['platforms']), ('The Bear', ['Hulu', 'Disney+']))
        h = PC.detect('customers who listened to Call Her Daddy on Spotify and customers who listened to it on SiriusXM')
        self.assertEqual((h['verb'], h['platforms']), ('listened to', ['Spotify', 'SiriusXM']))

    def test_other_asks_are_left_alone(self):
        for t in ('run a profile on Nike shoppers',
                  'people who watched Severance on Apple TV+',
                  'make two profiles: 1) people who watched Saltburn on Prime Video 2) people who watched Barbie on HBO Max',
                  'what platforms do Glen Powell fans stream on',
                  'journey for Young Sheldon on Netflix, start from people who watched a clip on TikTok, end at the first episode watched',
                  'Build a digital journey for Anthony Bourdain No Reservations on Amazon Prime just like the one you did for Babylon 5',
                  'can we build a deck on the demographics of the profile wicked tuna viewers'):
            self.assertIsNone(PC.detect(t), t)


class Rewrite(unittest.TestCase):
    def test_model_reads_the_universe_sentence(self):
        h = PC.detect(JAY)
        s = PC.rewrite_for_interpret(JAY, h)
        self.assertIn('Wuthering Heights', s)
        self.assertIn('any streaming service', s)
        for p in ('Amazon Prime Video', 'HBO Max', 'three', 'talk me through'):
            self.assertNotIn(p, s)

    def test_cuts_are_behavioral_and_pin_the_service(self):
        h = PC.detect(JAY)
        cuts = PC.addon_cuts(h, 'Wuthering Heights 2026')
        self.assertEqual([c['label'] for c in cuts], ['Amazon Prime Video Viewers', 'HBO Max Viewers'])
        for c in cuts:
            self.assertEqual(c['kind'], 'behavioral')
            self.assertIsNone(c['pin_category'])
            self.assertIn('100% of this cohort', c['cohort_description'])
            self.assertTrue(c['from_prompt_qualifier'])

    def test_apply_to_draft_rides_cuts_and_note_and_unpins_named_services(self):
        h = PC.detect(JAY)
        d = {'subject': 'Wuthering Heights 2026', 'decision': 'new_build', 'addon_cuts': [],
             'named_platforms': ['HBO Max'], 'home_platform_rows': [['STREAMING/PLATFORM', 'HBO Max']], 'platform': 'HBO Max'}
        PC.apply_to_draft(d, h, JAY)
        self.assertEqual([c['label'] for c in d['addon_cuts']], ['Amazon Prime Video Viewers', 'HBO Max Viewers'])
        self.assertEqual(d['named_platforms'], [])
        self.assertEqual(d['home_platform_rows'], [])
        self.assertIsNone(d['platform'])
        self.assertIn('How this gets built', d['method_note'])
        self.assertIn('The HBO Max cut is the people who watched it on HBO Max.', d['method_note'])
        self.assertIn('not mutually exclusive', d['method_note'])
        self.assertIn('four or more times', d['method_note'])
        # idempotent
        PC.apply_to_draft(d, h, JAY)
        self.assertEqual(len(d['addon_cuts']), 2)

    def test_no_note_without_the_question(self):
        h = PC.detect('1. viewers of The Bear on Hulu\n2. viewers of The Bear on Disney+')
        self.assertEqual(PC.method_note(h, 'The Bear', PC.addon_cuts(h, 'The Bear')), '')
        self.assertFalse(PC.asks_method('run a profile on The Bear viewers'))
        self.assertTrue(PC.asks_method('run a profile on The Bear viewers and walk me through how you build it'))

    def test_note_for_a_single_title_build(self):
        d = {'subject': 'Severance', 'content_show': 'Severance', 'home_platform_rows': [['STREAMING/PLATFORM', 'Apple TV+']]}
        n = PC.note_for_title_build(d, 'run a profile of people who watched Severance on Apple TV+ and explain how you build it')
        self.assertIn('The Apple TV+ cut is the people who watched it on Apple TV+.', n)
        self.assertEqual(PC.note_for_title_build({'subject': 'Nike', 'brand_category': 'APPAREL'}, 'run Nike and explain how you build it'), '')


class Vocabulary(unittest.TestCase):
    def test_note_carries_no_internal_words(self):
        h = PC.detect(JAY)
        n = PC.method_note(h, 'Wuthering Heights 2026', PC.addon_cuts(h, 'Wuthering Heights 2026')).lower()
        for bad in ('synth', 'model', 'pipeline', 'panel', 'hostmap', 'claude', 'clickstream', '\u2014'):
            self.assertNotIn(bad, n, bad)


if __name__ == '__main__':
    unittest.main(verbosity=1)
