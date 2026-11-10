#!/usr/bin/env python3
"""One ask with facet questions is one ask (Jenna 2026-11-10).

Jenna's verbatim message, "do a digital journey of people who watched my
life with the walter boys ... showing how they got there. was it from the
blog on wattpad? social media contrnt? digital ads? just suggested as
content to watch next? that kind of thing.", was split by the multi_ask
lane into four unrelated asks and one started a stray read.

Rule: trailing fragment questions that enumerate facets of the one ask (no
subject or verb of their own, short, "that kind of thing" / "etc" / "for
example" nearby, each naming a channel, path, option or attribute of the
stated subject) are part of the single ask and ride into it as the facets
to cover; multi_ask splits only on full independent questions.

    REGRESSION_TEST_MODE=1 python3 scripts/test_pm_multi_ask_facets.py
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

from prometheus import multi_ask as MA  # noqa: E402
import prometheus_analysis as PMA  # noqa: E402

JENNA = ("do a digital journey of people who watched my life with the walter boys on netflix (the show just had season 3 come out "
         "in august) showing how they got there. was it from the blog on wattpad? social media contrnt? digital ads? just suggested "
         "as content to watch next? that kind of thing.")
TWO = "How big is the Reba audience, and what are the top five QSRs for Gilmore Girls viewers?"
TWO_SENTENCES = "How big is the Reba audience? What are the top five QSRs for Gilmore Girls viewers?"
GRACE = "\n".join([
    "A few questions on Gilmore Girls viewers on Prime Video:",
    "1. How many US people watched Gilmore Girls on Prime Video in the trailing 12 months?",
    "2. What share of them are new to the title?",
    "3. Break this down by age, gender, ethnicity, household income and region.",
    "4. Which other Prime Video titles do they watch most?",
    "5. How many of them also subscribe to Netflix?",
    "6. What is the Avid share, and how is Avid defined?",
    "7. Which social platforms do they use most, indexed against US adults?",
    "8. How many watched the Thanksgiving episodes in November 2025?",
    "9. What podcasts do they listen to?",
    "10. Which QSRs over-index with this audience?",
    "11. Describe the top three DMAs for this audience and their share of the viewers.",
])


class Facets(unittest.TestCase):
    def test_jennas_message_is_one_ask_with_four_facets(self):
        self.assertIsNone(MA.parse(JENNA, pma=PMA), 'one ask, never split')
        self.assertEqual(MA.split(JENNA, pma=PMA), [])
        self.assertEqual(MA.facets_of(JENNA),
                         ['the blog on wattpad', 'social media contrnt', 'digital ads', 'suggested as content to watch next'])
        # the sentence splitter alone still cuts at the question marks; the fold is the lane's
        raw = PMA.split_questions(JENNA)
        self.assertGreaterEqual(len(raw), 3)
        self.assertEqual(len(MA.fold_facets(raw, JENNA)), 1)

    def test_facets_without_the_marker_still_fold_after_a_question(self):
        t = ("show me how people got to the walter boys on netflix. was it from a clip on tiktok? a netflix row? "
             "a search? an ad?")
        self.assertIsNone(MA.parse(t, pma=PMA))
        self.assertEqual(MA.facets_of(t), ['a clip on tiktok', 'a netflix row', 'a search', 'an ad'])

    def test_a_true_two_question_message_is_still_two(self):
        for t in (TWO, TWO_SENTENCES):
            parsed = MA.parse(t, pma=PMA)
            self.assertIsNotNone(parsed, t)
            self.assertEqual(len(parsed['items']), 2, parsed)
        # a short question that names its own subject is a question, not a facet
        self.assertEqual(len(MA.split("Who watches Reba? Gilmore Girls?", pma=PMA)), 2)

    def test_grace_yins_numbered_list_still_splits(self):
        parsed = MA.parse(GRACE, pma=PMA)
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed['numbered'])
        self.assertEqual(len(parsed['items']), MA.MAX_ITEMS)
        self.assertEqual(parsed['left'], 1)
        self.assertEqual(parsed['numbers'][:3], [1, 2, 3])
        self.assertIn('household income', parsed['items'][2])

    def test_fragment_detection(self):
        for frag in ('social media contrnt?', 'digital ads?', 'just suggested as content to watch next?', 'a netflix row?'):
            self.assertTrue(MA._is_facet_fragment(frag), frag)
        for q in ('what are the top five QSRs?', 'Gilmore Girls?', 'how many watched it?', 'compare them to Reba', '3 of them?'):
            self.assertFalse(MA._is_facet_fragment(q), q)


if __name__ == '__main__':
    unittest.main(verbosity=2)
