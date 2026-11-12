#!/usr/bin/env python3
"""A quote prints a name as the record carries it (2026-11-12).

A priced sizing of Georgie & Mandy's First Marriage on HBO Max was
quoted "I can size Georgie and the Mandy's First Marriage audience on
HBO Max." The sizing step listed the things an ask names by splitting on
commas, "and" and every ampersand, so a title with an ampersand became
two things; the quote then gave the second one its article and joined
them with "and". The split was not only wording: the two halves were
what would have been sized.

Holds:
  0. the lead-in of a sizing ask ("how big is the ... audience") never
     eats the front of a name, and a title's own first word stays on it;
  1. an ampersand joins a name; it separates two subjects only when the
     dashboard holds each side and not the whole;
  2. the sizing quote prints the dashboard's own spelling of a name it
     holds (capitals, ampersand, possessive, a leading "The");
  3. every other quote, the binder and the cleaner leave these names as
     they are: "Georgie & Mandy's First Marriage", the same with its
     services in brackets, "NCIS: New York", "The Rainmaker", "Law &
     Order", "Barnes & Noble", "Tom's of Maine", "Ben & Jerry's".

Hermetic: the library is a dict. No S3, no model, no app module.
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
from prometheus import cut_list as CL  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
from prometheus import sizing_lane as SZ  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402

GM = "Georgie & Mandy's First Marriage"
GM_SCOPED = "Georgie & Mandy's First Marriage Viewers (Amazon Prime Video & HBO Max)"
NAMES = [GM, GM_SCOPED, 'NCIS: New York', 'The Rainmaker', 'Law & Order', 'Barnes & Noble', "Tom's of Maine", "Ben & Jerry's"]
LIBRARY = {n.lower(): n for n in NAMES + ['Nike', 'Adidas', 'Babylon 5', 'Howie Mandel', 'Forbes', 'Onyx Collective']}
LIBRARY['rainmaker'] = 'The Rainmaker'            # the library answers to the name with or without its article


def _resolve(phrase, ctx=None):
    key = ' '.join(str(phrase or '').lower().split())
    return {'name': LIBRARY[key], 'sample': 41237, 'us': 1360411} if key in LIBRARY else None


class _Base(unittest.TestCase):
    def setUp(self):
        self._resolve, self._load, self._incl = SZ.resolve, IL._load_index, PR.included_with_plan
        SZ.resolve = _resolve
        IL._load_index = lambda force=False: {'subjects': {}}
        PR.included_with_plan = lambda user, username=None, host=None: False

    def tearDown(self):
        SZ.resolve, IL._load_index, PR.included_with_plan = self._resolve, self._load, self._incl


class AnAmpersandJoinsAName(_Base):
    def test_the_quote_that_was_garbled(self):
        q = f"How big is the {GM} audience on HBO Max?"
        self.assertEqual(SZ.subjects(q), [f"{GM} audience on HBO Max"])       # one thing, where it was two
        self.assertEqual(PR.sizing_lead(q), f"I can size the {GM} audience on HBO Max.")
        self.assertNotIn('and the', PR.sizing_lead(q))

    def test_each_name_is_one_subject_and_is_printed_as_the_record_carries_it(self):
        for name in NAMES:
            q = f"How big is the {name} audience?" if not name.startswith('The ') else f"How big is {name} audience?"
            subs = SZ.subjects(q)
            self.assertEqual(len(subs), 1, (name, subs))
            lead = PR.sizing_lead(q)
            self.assertIn(name, lead, (name, lead))
            self.assertNotRegex(lead, r" and (?:the )?(?:Mandy|Order|Noble|Jerry)", lead)
        # typed in lower case, the record's capitals come back
        self.assertEqual(PR.sizing_lead('how big is the law & order audience?'), 'I can size Law & Order.')
        self.assertEqual(PR.sizing_lead("how big is the ben & jerry's audience"), "I can size Ben & Jerry's.")
        self.assertEqual(PR.sizing_lead('How big is The Rainmaker audience?'), 'I can size The Rainmaker.')
        self.assertEqual(PR.sizing_lead(f"how big is {GM_SCOPED.lower()}"), f"I can size the {GM_SCOPED}.")

    def test_two_subjects_written_with_an_ampersand_are_still_two(self):
        self.assertEqual(SZ.subjects('How big are the Nike & Adidas audiences?'), ['Nike', 'Adidas'])
        self.assertEqual(PR.sizing_lead('How big are the Nike & Adidas audiences?'), 'I can size Nike and Adidas.')
        self.assertEqual(SZ.subjects('How big are the Nike and Adidas audiences?'), ['Nike', 'Adidas'])
        # a name the dashboard does not hold is kept whole, as written (never half of it)
        self.assertEqual(SZ.subjects('how big is the Simon & Schuster audience'), ['Simon & Schuster'])
        self.assertEqual(PR.sizing_lead('how big is the Simon & Schuster audience'), 'I can size Simon & Schuster.')

    def test_what_did_not_change(self):
        self.assertEqual(PR.sizing_lead('How big is the Babylon 5 audience on Prime Video?'), 'I can size the Babylon 5 audience on Prime Video.')
        self.assertEqual(SZ.subjects("How big is Howie Mandel's audience?"), ['Howie Mandel'])
        self.assertEqual(SZ.subjects('size prank content, horror and comedy'), ['prank content', 'horror', 'comedy'])

    def test_with_no_library_to_ask_a_name_is_kept_whole(self):
        SZ.resolve = lambda phrase, ctx=None: None
        self.assertEqual(SZ.subjects(f"How big is the {GM} audience?"), [GM])
        self.assertEqual(SZ.subjects('How big are the Nike & Adidas audiences?'), ['Nike & Adidas'])

        def boom(phrase, ctx=None):
            raise RuntimeError('catalog down')
        SZ.resolve = boom
        self.assertEqual(SZ.subjects('how big is the Law & Order audience'), ['Law & Order'])
        self.assertEqual(PR.sizing_lead('how big is the Law & Order audience'), 'I can size Law & Order.')


class TheLeadInNeverEatsTheFrontOfAName(_Base):
    """On prod before the fix: "How big is the Forbes audience?" was quoted "I can size bes.", The Office on
    Peacock "I can size the fice audience on Peacock.", Onyx Collective "I can size yx Collective.", The
    Rainmaker "I can size Rainmaker." The lead-in's optional words ("of", "for", "on", "reach", a count, "group")
    had no word boundary, and a title's own first word was read as one of them."""

    def test_the_words_of_the_lead_in_are_whole_words(self):
        for q, want in (('How big is the Forbes audience?', ['Forbes']),
                        ('How big is the Onyx Collective audience?', ['Onyx Collective']),
                        ('How big is the Reacher audience?', ['Reacher']),
                        ('How big is the Groupon audience?', ['Groupon']),
                        ('How big is the 49ers audience?', ['49ers']),
                        ('How big is the Sizemore audience?', ['Sizemore']),
                        ('how big is the office audience', ['office']),
                        ("how big is Of Montreal's audience", ['Of Montreal'])):
            self.assertEqual(SZ.subjects(q), want, q)
        self.assertEqual(PR.sizing_lead('How big is the Forbes audience?'), 'I can size Forbes.')
        self.assertEqual(PR.sizing_lead('How big is the Onyx Collective audience?'), 'I can size Onyx Collective.')

    def test_a_titles_own_first_word_stays_on_it(self):
        for q, want in (('How big is The Rainmaker audience?', 'I can size The Rainmaker.'),
                        ('What is the size of The Rainmaker audience?', 'I can size The Rainmaker.'),
                        ('How big is The Office audience on Peacock?', 'I can size The Office audience on Peacock.'),
                        ('How big is the audience of The Bear on Hulu?', 'I can size The Bear on Hulu.'),
                        ('How big is the On Patrol: Live audience?', 'I can size On Patrol: Live.'),
                        ('how big is the For All Mankind audience', 'I can size For All Mankind.'),
                        ('how big is the Band of Brothers audience', 'I can size Band of Brothers.')):
            self.assertEqual(PR.sizing_lead(q), want, q)

    def test_speaking_to_prometheus_by_name_names_no_audience(self):
        self.assertEqual(SZ.subjects('Hey Prometheus, how big is the Forbes audience'), ['Forbes'])
        self.assertEqual(SZ.subjects('Prometheus, how big is the Forbes audience?'), ['Forbes'])
        self.assertEqual(SZ.subjects('how big is the Prometheus audience'), ['Prometheus'])       # the film is still a subject

    def test_the_asks_that_already_read_right_still_do(self):
        for q, want in (('what is the size of the Nike audience', ['Nike']),
                        ('what is the audience size for Nike and Adidas', ['Nike', 'Adidas']),
                        ('how big are the two audiences: Nike, Adidas', ['Nike', 'Adidas']),
                        ('give me the sample size of both datasets: Landman and Tulsa King', ['Landman', 'Tulsa King']),
                        ("what's the reach of Forbes", ['Forbes']),
                        ('can I get the size of the Nike audience', ['Nike']),
                        ('Thanks. How big is the Forbes audience?', ['Forbes']),
                        ('What is the Size of the Nike audience?', ['Nike']),
                        ('how many people are in the Nike audience', ['Nike']),
                        ('size of 2 audiences: Nike and Adidas', ['Nike', 'Adidas']),
                        ('I need the size of the Landman dataset', ['Landman']),
                        ('how many Costco shoppers are there', ['Costco shoppers']),
                        ('sample size for FanDuel customers', ['FanDuel customers']),
                        ('how large is the Tulsa King audience on Paramount+', ['Tulsa King audience on Paramount+']),
                        ('what is the size of the Taylor Swift and Travis Kelce audiences', ['Taylor Swift', 'Travis Kelce']),
                        ('How big is it?', []), ('how big is the 2024 election audience', [])):
            self.assertEqual(SZ.subjects(q), want, q)


class EveryOtherQuoteLeavesTheNameAlone(_Base):
    def test_the_cleaner_and_the_plain_spelling(self):
        for name in NAMES:
            self.assertEqual(SR.clean_display(name), name)
            self.assertEqual(CL.quote_name(name), name)

    def test_every_kind_of_quote(self):
        for name in NAMES:
            for kind in ('report', 'custom', 'list', 'path', 'overlap', 'cross', 'sizing', 'derived', 'viewership', 'adaptation', 'rerun'):
                lead = PR.lead_for(kind, name, verb='watched', span='one year', dimension='age', noun='read')
                # "watched <title>", never "watched <title> viewers": the audience noun may come off, nothing else changes
                self.assertTrue(name in lead or (kind == 'viewership' and name.replace(' Viewers', '') in lead), (kind, name, lead))
                self.assertNotIn(' and the ', lead, (kind, lead))

    def test_the_binder_and_the_quote_through_the_one_gate(self):
        for name in NAMES:
            q = f"What do {name} buy most often?" if 'Viewers' in name else f"What do {name} viewers buy most often?"
            rd = {'intent': 'analyze', 'subjects': [{'as_written': name, 'catalog': '', 'kind': 'title'}]}
            subject = PR.bound_subject(q, [], None, reading=rd)
            self.assertIn(name, subject, (name, subject))
            state, pl = PR.offer_or_charge(text=q, subject=subject, kind='report', panel_confirm=None, username='payer_n',
                                           user={'username': 'payer_n'}, history=[])
            self.assertEqual(state, 'offer')
            self.assertIn(f"I can pull that read on {name}", pl['reply'], (name, pl['reply']))
            self.assertIn(name, pl['panel_offer']['subject'])

    def test_a_title_keeps_its_the(self):
        self.assertEqual(SR.sentence_audience('What do The Rainmaker viewers buy most often?'), ('The Rainmaker', 'viewers'))
        self.assertEqual(SR.sentence_audience('The Rainmaker viewers skew older?', {'the rainmaker': 'The Rainmaker'}), ('The Rainmaker', 'viewers'))
        self.assertEqual(SR.sentence_audience('What did Landman viewers watch next'), ('Landman', 'viewers'))
        self.assertEqual(SR.sentence_audience('The Landman viewers skew older')[0], 'Landman')        # a sentence's first word is only a word
        self.assertEqual(SR.sentence_audience('do the Landman viewers skew older')[0], 'Landman')


if __name__ == '__main__':
    unittest.main(verbosity=1)
