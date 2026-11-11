#!/usr/bin/env python3
"""Day audit, 2026-11-10 (Jenna: "go thru all questions asked today and
responses and when they were wrong find out why").

Three lane causes with a certain fix, each on the user's exact words:

1. Keith (Crosswalk), 21:09 and 22:35 UTC: "the percentage of Claire's
   shoppers that also shop at Target, Walmart and Kohl's" bound Target
   (the nested-audience frame read "shoppers that also shop at Target"
   as the audience, and the read core's library tier took the Target
   file because Target is in the words), the read ran on the Target
   profile and called Claire's buyers a row of it. A named audience in
   front of "who / that also" is the base; the places after "also" are
   the overlap targets, and a library file for one of them is dropped
   as the base (question_universe.names_other_universe).
2. The same read's headline reached him as "99.4% of the [calc:" (a
   token the model left open at the end of the line). An unclosed token
   takes its sentence with it, and the closed-token scrub stays on one
   line (it used to eat everything up to the next "]" on a later line).
3. Liz (API), 00:41 UTC: "(Amazon versus direct)" was read as Amazon
   Music through the reader's catalog match and the churn read ran on
   the Amazon Music profile. A company name expanded to one of its
   products (Amazon -> Amazon Music, Apple -> Apple TV+, Google -> Google
   Play) does not bind; the written brand stays.

Hermetic: no network, no model, no S3.
"""
import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from prometheus import figures as F  # noqa: E402
from prometheus import question_universe as QU  # noqa: E402
from prometheus import reader as RD  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402

KEITH_1 = "I'd like to know the percentage of Claire's shoppers that also shop at Target, Walmart and Kohl's?"
KEITH_3 = ("Let me clarify my question. I'd like to know the unique percent of Claire's (https://www.claires.com/) "
           "purchasers that have also made a purchase at Target (https://www.target.com/), the retail outlet, "
           "Walmart (https://www.walmart.com/), Kohl's https://www.kohls.com/), and CVS (https://www.cvs.com/).")
KEITH_READING = {'intent': 'analyze', 'confidence': 0.84, 'flags': ['multiple_subjects'],
                 'subjects': [{'as_written': "Claire's shoppers", 'catalog': '', 'kind': 'brand'},
                              {'as_written': 'Target', 'catalog': 'Target', 'kind': 'brand'},
                              {'as_written': 'Walmart', 'catalog': 'Walmart', 'kind': 'brand'},
                              {'as_written': "Kohl's", 'catalog': "Kohl's", 'kind': 'brand'}]}
LIZ_2 = ("what is the average monthly churn rate for subscribers on each path (Amazon versus direct)? "
         "State how churn is defined.?")
LIZ_READING = {'intent': 'analyze', 'subjects': [{'as_written': 'Amazon', 'catalog': 'Amazon Music', 'kind': 'brand'}]}
CATALOG = ['Amazon Music', 'Amazon Prime Video', 'HBO Max', 'Peacock', 'Target', 'Walmart', "Kohl's"]
# the live reading on Keith's first ask: Target was its only library match
KEITH_READING_LIVE = dict(KEITH_READING, subjects=[KEITH_READING['subjects'][0], KEITH_READING['subjects'][1]])


class AlsoBaseIsTheNamedAudience(unittest.TestCase):
    def test_keith_first_ask_binds_claires(self):
        s = SR.bind(KEITH_1, None, '', reading=KEITH_READING, catalog_names=CATALOG)
        self.assertEqual(s.name, "Claire's")
        self.assertEqual(s.qualifiers.get('verb'), 'shoppers')
        self.assertEqual(s.qualifiers.get('also'), ['Target', 'Walmart', "Kohl's"])
        self.assertEqual(s.source, 'words')

    def test_keith_clarified_ask_binds_claires_with_cvs(self):
        s = SR.bind(KEITH_3, None, '', reading=None, catalog_names=CATALOG)
        self.assertEqual(s.name, "Claire's")
        self.assertEqual(s.qualifiers.get('verb'), 'purchasers')
        self.assertEqual(s.qualifiers.get('also'), ['Target', 'Walmart', "Kohl's", 'CVS'])

    def test_also_base_reads_the_frame(self):
        self.assertEqual(SR.also_base(KEITH_1), ("Claire's", 'shoppers', ['Target', 'Walmart', "Kohl's"]))
        self.assertEqual(SR.also_base("people who also watched Yellowstone"), ('', '', []))
        self.assertEqual(SR.also_base("how many people watched the Hazbin Hotel pilot on YouTube"), ('', '', []))

    def test_landman_viewers_who_also_watched_stays_landman(self):
        s = SR.bind("What share of Landman viewers who also watched Yellowstone are under 35?", None, '')
        self.assertEqual(s.name, 'Landman')
        self.assertEqual(s.qualifiers.get('verb'), 'viewers')

    def test_the_frame_holds_without_a_relative_pronoun(self):
        q = "What percent of Claire's purchasers also made a purchase at Target, Walmart, Kohl's and CVS in the last 12 months?"
        self.assertEqual(SR.also_base(q), ("Claire's", 'purchasers', ['Target', 'Walmart', "Kohl's", 'CVS']))
        self.assertTrue(QU.names_other_universe(q, 'Target', page=''))
        self.assertFalse(QU.names_other_universe(q, "Claire's", page=''))
        dec = {'surface': 'analyze', 'reason': 'question'}
        self.assertIsNone(RD.steer(KEITH_READING_LIVE, dec, has_ctx=False, text=q))

    def test_nested_audience_without_a_name_still_binds_the_object(self):
        s = SR.bind("people who also watched Yellowstone", None, '')
        self.assertEqual(s.name, 'Yellowstone')
        s = SR.bind("people who watched the Among Us series on Paramount+", None, '')
        self.assertEqual((s.name, s.qualifiers.get('platform')), ('Among Us', 'Paramount+'))


class ALibrarySubjectAfterAlsoIsNotTheBase(unittest.TestCase):
    """The read core drops a page or library base the question did not
    name as its universe (names_other_universe). Target is in Keith's
    words, after "also", and is still not the base."""

    def test_target_walmart_kohls_are_overlap_targets_not_bases(self):
        for b in ('Target', 'Walmart', "Kohl's"):
            self.assertTrue(QU.names_other_universe(KEITH_1, b, page=''), b)
            self.assertTrue(QU.names_other_universe(KEITH_3, b, page=''), b)
        self.assertTrue(QU.names_other_universe(KEITH_3, 'CVS', page=''))

    def test_the_audience_itself_still_binds(self):
        self.assertFalse(QU.names_other_universe(KEITH_1, "Claire's", page=''))
        self.assertFalse(QU.names_other_universe(KEITH_3, "Claire's", page=''))

    def test_the_read_runs_on_claires(self):
        self.assertEqual(QU.subject_for(QU.question_universe(KEITH_1, ''), '', KEITH_1), "Claire's shoppers")
        self.assertEqual(QU.subject_for(QU.question_universe(KEITH_3, ''), '', KEITH_3), "Claire's purchasers")

    def test_a_subject_named_as_the_universe_keeps_its_file(self):
        self.assertFalse(QU.names_other_universe("How many Costco members shop at Target too?", 'Costco', page=''))


class TheReaderNeverHandsOverAnOverlapSubject(unittest.TestCase):
    """reader.steer binds the reading's one library subject for the read
    core. Target was Keith's only library match and it was the overlap,
    not the base: with the words in hand, steer hands over nothing."""

    def test_keiths_reading_binds_no_library_subject(self):
        dec = {'surface': 'analyze', 'reason': 'question'}
        out = RD.steer(KEITH_READING_LIVE, dec, has_ctx=False, text=KEITH_1)
        self.assertIsNone(out)

    def test_a_reading_whose_one_library_subject_is_the_base_still_binds(self):
        r = {'intent': 'analyze', 'confidence': 0.9, 'flags': [],
             'subjects': [{'as_written': 'Target shoppers', 'catalog': 'Target', 'kind': 'brand'}]}
        dec = {'surface': 'analyze', 'reason': 'question'}
        out = RD.steer(r, dec, has_ctx=False, text='How do Target shoppers split by age band?')
        self.assertIsNotNone(out)
        self.assertEqual(out[1], 'Target')

    def test_without_the_words_the_old_hand_over_stands(self):
        dec = {'surface': 'analyze', 'reason': 'question'}
        out = RD.steer(KEITH_READING_LIVE, dec, has_ctx=False)
        self.assertEqual((out or ('', ''))[1], 'Target')


class UnclosedTokenNeverReachesAReader(unittest.TestCase):
    def test_keiths_headline_fragment_goes_with_its_sentence(self):
        reg = F.Registry()
        txt = ("On Target - Claire's buyers:\n\nClaire's buyers are a three-door audience: 99.4% of the [calc:\n\n"
               "Claire's buyers, projected US people: 5,332,185, Also bought at Target: 32.6%.")
        out = reg.render(txt)
        self.assertNotIn('[calc', out)
        self.assertNotIn('99.4% of the', out)
        self.assertIn('Claire\'s buyers, projected US people: 5,332,185, Also bought at Target: 32.6%.', out)
        self.assertTrue(out.startswith("On Target - Claire's buyers:"))

    def test_only_the_hanging_sentence_goes(self):
        reg = F.Registry()
        out = reg.render("First sentence stays. Second with 1.5% and a [calc: f2/f1")
        self.assertEqual(out, 'First sentence stays.')
        out = reg.render("Held to 12.5% in the window. A tail [f")
        self.assertEqual(out, 'Held to 12.5% in the window.')

    def test_closed_token_scrub_stays_on_its_line(self):
        reg = F.Registry()
        txt = "Open here [calc:\nThis line must survive 5,332,185 people.\nClosed [calc: f3/f1*100 | pct] here."
        out = reg.render(txt)
        self.assertIn('This line must survive 5,332,185 people.', out)
        self.assertIn('Closed that figure here.', out)
        self.assertNotIn('[calc', out)

    def test_clean_text_is_untouched(self):
        reg = F.Registry()
        txt = "Headline with a real figure 12,345 and no token.\n\nNext line at 4.5%."
        self.assertEqual(reg.render(txt), txt)


class ACompanyIsNotItsProduct(unittest.TestCase):
    def test_liz_amazon_versus_direct_never_binds_amazon_music(self):
        s = SR.bind(LIZ_2, None, '', reading=LIZ_READING, catalog_names=CATALOG)
        self.assertNotEqual(s.name, 'Amazon Music')
        self.assertEqual(s.name, 'Amazon')

    def test_expansion_rule(self):
        P = SR.product_line_expansion
        self.assertTrue(P('Amazon', 'Amazon Music'))
        self.assertTrue(P('Apple', 'Apple TV+'))
        self.assertTrue(P('Google', 'Google Play'))
        self.assertFalse(P('Disney', 'Disney+'))
        self.assertFalse(P('Paramount', 'Paramount+'))
        self.assertFalse(P('Amazon Prime Video', 'Amazon Prime Video'))
        self.assertFalse(P('Prime Video', 'Amazon Prime Video'))
        self.assertFalse(P('Walter Boys', 'My Life with the Walter Boys'))
        self.assertFalse(P('Netflix', 'Netflix Ad Sales'))
        self.assertFalse(P('Target', 'Target'))

    def test_a_completed_title_still_binds(self):
        r = {'intent': 'analyze', 'subjects': [{'as_written': 'Walter Boys', 'catalog': 'My Life with the Walter Boys', 'kind': 'title'}]}
        s = SR.bind("how big is the Walter Boys audience", None, '', reading=r)
        self.assertEqual(s.name, 'My Life with the Walter Boys')


if __name__ == '__main__':
    unittest.main(verbosity=2)
