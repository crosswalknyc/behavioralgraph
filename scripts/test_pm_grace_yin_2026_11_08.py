#!/usr/bin/env python3
"""Grace Yin's Prometheus session of 2026-11-08 (Amazon; the Hazbin Hotel
Season 1 Prime sign-ups profile), each miss on her exact words.

1. "are these people new sign ups for prime membership" with the profile
   open pointed at the page and was asked which audience to run on.
2. Four numbered methodology questions were taken whole as a
   reconciliation because one said "before projection", and the record of
   a 100% share came back as "Unprojected: 3 people".
3. "Are any of these figures modeled or imputed rather than directly
   counted?" and "How do you detect when someone activated ..." are the
   provenance question; one came back as "which subject should this be on".
4. "How many US people watched the Hazbin Hotel pilot on YouTube ..." bound
   YouTube (the platform) and the offer read "watched YouTube".
5. "paid sign-ups for Hazbin Hotel Season 1" with that profile open was
   asked which subject it meant: the Reader's candidate matched the page
   and was dropped.
6. An unlimited client seat saw "not billed to a staff account" on its
   Approve chip.
7. "how many US consumers signed up for Amazon Prime and Hazbin Hotel
   Season 1 was the first thing they watched" is a count, answered as a
   read, never handed to the build flow.
8. The exec summary's "31.4% of the file, 42,957,147 people" on a
   1,368,062-person file: the share applied without the division by 100.
"""
import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)



class _NoAppModule:
    """Refuse a fresh ``import app`` in this hermetic suite (the priced
    read's seat lookup falls back to the app module when the host binds no
    ``load_users``; the suite passes a stub host instead)."""

    def find_spec(self, name, path=None, target=None):
        if name == 'app':
            raise ImportError('app is not importable inside this hermetic suite')
        return None


sys.meta_path.insert(0, _NoAppModule())

from prometheus import subject_rules as sr  # noqa: E402
from prometheus import priced_read as pr  # noqa: E402
from prometheus import reconcile_lane as rl  # noqa: E402
from prometheus import multi_ask as ma  # noqa: E402
from prometheus import guards as g  # noqa: E402
from prometheus import arith_coherence as ac  # noqa: E402
import prometheus_analysis as pma  # noqa: E402

PAGE = 'Hazbin Hotel Season 1 - Amazon Prime New Sign-Ups Viewers'
CTX = {'primary': {'name': PAGE, 's3_key': 'Hazbin_Hotel_Season_1_-_Amazon_Prime_New_Sign-Ups_Viewers_11_08_2026_17_09.csv'}, 'cuts': []}

Q1 = ("How many people were paid sign-ups for Hazbin Hotel Season 1 in the US during its first 28 days "
      "from launch on Prime Video (January 19 to February 15, 2024)?")
Q2 = ("How many US consumers signed up for Amazon Prime and Hazbin Hotel Season 1 was the first thing they "
      "watched on prime video after joining from January 19 and February 15, 2024  ?")
Q5 = ("inside a cohort of 1,368,062 US viewers in the January 19 to February 15, 2024 window. "
      "are these people new sign ups for prime memberhsip")
Q6 = ("1. How do you detect when someone activated a Prime membership: card transactions, email receipts, or self-report?\n"
      "2. Does \"activated\" include free trials and people coming back after cancelling?\n"
      "3. How is a Hazbin play detected, and what is the raw panel count before projection?\n"
      "4. Why does Prime Video activity come in under 100% when everyone in the file watched Hazbin?")
Q8 = ("1. What's the raw panel count behind each figure, especially 1,230,696 and 1,071,193?\n"
      "2. Are any of these figures modeled or imputed rather than directly counted?\n"
      "3. If the base really is 3 people, how is 1,071,193 possible?")
Q9 = "How many US people watched the Hazbin Hotel pilot on YouTube between January 19, 2023 and January 18, 2024?"

R1 = {'intent': 'analyze', 'confidence': 0.86, 'flags': [],
      'subjects': [{'as_written': 'Hazbin Hotel Season 1', 'catalog': 'Hazbin Hotel', 'kind': 'title'},
                   {'as_written': 'Hazbin Hotel pilot', 'catalog': 'Hazbin Hotel', 'kind': 'title'}]}
R5 = {'intent': 'definition', 'confidence': 0.89, 'subjects': [], 'flags': ['refers_to_page']}
R9 = {'intent': 'analyze', 'confidence': 0.89, 'flags': [],
      'subjects': [{'as_written': 'Hazbin Hotel pilot', 'catalog': 'Hazbin Hotel', 'kind': 'title'},
                   {'as_written': 'YouTube', 'catalog': 'YouTube', 'kind': 'platform'}]}


class PointsAtThePage(unittest.TestCase):
    def test_these_people_points_at_the_open_profile(self):
        s = sr.bind(Q5, None, PAGE, reading={'intent': 'analyze', 'confidence': 0.8, 'subjects': [], 'flags': []})
        self.assertEqual(s.name, PAGE)
        self.assertEqual(s.source, 'page')
        self.assertTrue(s.pointed)

    def test_reader_flag_is_a_pointer_too(self):
        s = sr.bind('are those viewers on a paid plan', None, PAGE,
                    reading={'intent': 'analyze', 'confidence': 0.8, 'subjects': [], 'flags': ['refers_to_page']})
        self.assertEqual(s.name, PAGE)

    def test_no_page_no_pointer(self):
        s = sr.bind(Q5, None, '', reading={'intent': 'analyze', 'confidence': 0.8, 'subjects': [], 'flags': []})
        self.assertFalse(bool(s))

    def test_work_gate_runs_a_definition_on_the_page_instead_of_asking(self):
        body = {'text': Q5, 'reading': R5}
        out = pr.work_gate(Q5, history=None, ctx=CTX, user={'username': 'graceyin'}, body=body, username='graceyin')
        self.assertIsNone(out, out)
        self.assertEqual(body.get('bind_subject'), PAGE)

    def test_work_gate_still_asks_when_nothing_points_anywhere(self):
        q = 'how many people bought a stand mixer last year'
        body = {'text': q, 'reading': {'intent': 'analyze', 'confidence': 0.8, 'subjects': [], 'flags': []}}
        out = pr.work_gate(q, history=None, ctx=CTX, user={'username': 'graceyin'}, body=body, username='graceyin')
        if out is not None:
            self.assertTrue(out.get('audience_confirm'))
            self.assertNotIn('bind_subject', body)


class NumberedQuestionsAreNotAReconciliation(unittest.TestCase):
    def test_four_numbered_methodology_questions_step_aside(self):
        self.assertFalse(rl.wants_reconcile(Q6))
        self.assertEqual(len(ma.numbered_items(Q6)), 4)

    def test_three_numbered_questions_with_figures_step_aside(self):
        self.assertFalse(rl.wants_reconcile(Q8))

    def test_before_projection_alone_is_a_methodology_question(self):
        self.assertFalse(rl.wants_reconcile('How is a Hazbin play detected, and what is the raw panel count before projection?'))
        # one figure asked for unprojected is the unprojected-number lane's one line (Jenna 2026-11-08,
        # prometheus.unprojected); two figures to square stay a reconciliation
        self.assertFalse(rl.wants_reconcile('what is the unprojected count behind the 6,195,357?'))
        from prometheus import unprojected as unp
        self.assertTrue(unp.wants_unprojected('what is the unprojected count behind the 6,195,357?'))
        self.assertIn('187,795 of the 10 million US consumers we measure',
                      unp.answer('what is the unprojected count behind the 6,195,357?', [])['reply'])
        self.assertTrue(rl.wants_reconcile('what are the unprojected counts behind the 6,195,357 and the 1,230,696?'))
        self.assertTrue(rl.wants_reconcile('your two answers do not agree; give me the unprojected counts'))

    def test_jay_still_reconciles(self):
        self.assertTrue(rl.wants_reconcile(
            'Your three answers to the Statham question give 6.2M, 8.5M and 43.2M total viewers, and 46.4%, 58.2% and '
            '38.6% not on Prime Video. Reconcile them. State one universe definition, one window, and the unprojected '
            'panel sample size.'))
        self.assertTrue(rl.wants_reconcile('state one universe definition and one window'))

    def test_a_share_has_no_people_count(self):
        self.assertIsNone(rl._sample_people({'kind': 'pct', 'value': 100.0, 'metric': {'unit': 'pct'}}, {}))
        self.assertEqual(rl._sample_people({'kind': 'count', 'value': 1368062, 'metric': {}}, {}), 41469)


class ProvenanceQuestions(unittest.TestCase):
    def test_modeled_or_imputed(self):
        self.assertTrue(g.is_provenance_question('Are any of these figures modeled or imputed rather than directly counted?'))

    def test_how_do_you_detect(self):
        self.assertTrue(g.is_provenance_question(
            'How do you detect when someone activated a Prime membership: card transactions, email receipts, or self-report?'))
        self.assertTrue(g.is_provenance_question('How is a Hazbin play detected, and what is the raw panel count before projection?'))

    def test_a_question_about_two_figures_stays_with_the_page(self):
        self.assertFalse(g.is_provenance_question(
            "What's the raw panel count behind each figure, especially 1,230,696 and 1,071,193?"))


class TheTitleNotThePlatform(unittest.TestCase):
    def test_how_many_people_watched_the_pilot_on_youtube(self):
        for rd, page in ((R9, PAGE), (R9, ''), (None, '')):
            s = sr.bind(Q9, None, page, reading=rd)
            self.assertEqual(s.name, 'Hazbin Hotel pilot', (rd, page, s))
            self.assertEqual(s.qualifiers.get('platform'), 'YouTube')
            self.assertEqual(s.source, 'words')
        label = pr.bound_subject(Q9, None, CTX, reading=R9)
        self.assertIn('Hazbin Hotel pilot', label)
        self.assertNotEqual(label, 'YouTube')

    def test_people_who_watched_still_binds(self):
        name, platform, kind = sr.nested_audience('people who watched the Among Us series on Paramount+')
        self.assertEqual((name, platform), ('Among Us', 'Paramount+'))

    def test_short_the_title_keeps_its_article(self):
        name, _p, _k = sr.nested_audience('how many people watched the Office on Peacock')
        self.assertEqual(name.lower(), 'the office')


class TheWordsNameTheOpenPage(unittest.TestCase):
    def test_paid_signups_for_the_open_profiles_title_is_the_page(self):
        s = sr.bind(Q1, None, PAGE, reading=R1)
        self.assertEqual(s.name, PAGE)
        self.assertEqual(s.source, 'words')
        self.assertEqual(pr.bound_subject(Q1, None, CTX, reading=R1), PAGE)

    def test_without_the_page_the_catalog_title_binds(self):
        s = sr.bind(Q1, None, '', reading=R1)
        self.assertEqual(s.name, 'Hazbin Hotel')

    def test_a_candidate_sharing_only_the_catalog_keeps_its_own_name(self):
        rd = {'intent': 'analyze', 'confidence': 0.8, 'flags': [],
              'subjects': [{'as_written': 'Hazbin Hotel pilot', 'catalog': 'Hazbin Hotel', 'kind': 'title'}]}
        s = sr.bind('what did the Hazbin Hotel pilot do for the series', None, PAGE, reading=rd)
        self.assertEqual(s.name, 'Hazbin Hotel pilot')
        self.assertNotEqual(s.name, PAGE)


class _Host:
    """The seat row, served the way the bound host serves it."""

    def __init__(self, user):
        self._user = dict(user)

    def has(self, name):
        return name == 'load_users'

    def load_users(self):
        return {'users': {self._user['username']: dict(self._user)}}


class CoveredSeatChip(unittest.TestCase):
    def test_covered_seat_is_not_staff(self):
        reply, chips, _ = pr.compose_offer('I can pull that read on Hazbin Hotel.', '$100', 'q', {}, staff='covered')
        self.assertEqual(chips[0], 'Approve ($100, included in your plan)')
        self.assertNotIn('staff', chips[0])
        self.assertTrue(pr.is_accept(chips[0]))

    def test_staff_and_billed_chips_unchanged(self):
        _r, chips, _ = pr.compose_offer('lead', '$100', 'q', {}, staff=True)
        self.assertEqual(chips[0], 'Approve ($100, not billed to a staff account)')
        _r, chips, _ = pr.compose_offer('lead', '$100', 'q', {}, staff=False)
        self.assertEqual(chips[0], 'Approve')

    def test_offer_or_charge_marks_a_covered_seat(self):
        # Jenna, later the same day (2026-11-08): "for anyone with unlimited credit it just should say
        # included with your plan instead of giving a price or staff chip". A covered seat sees no offer
        # at all: the gate hands back the zero charge and the read runs in the same turn, never a chip.
        user = {'username': 'graceyin', 'email': 'graceyin@amazon.com', 'company': 'Amazon', 'role': 'user',
                'credits': -1, 'unlimited': True}
        host = _Host(user)
        state, payload = pr.offer_or_charge(text=Q9, subject='Hazbin Hotel pilot', kind='viewership',
                                            panel_confirm=None, username='graceyin', user=user, history=None, host=host)
        self.assertEqual(state, 'charged')
        self.assertTrue(payload.get('included'))
        self.assertEqual(payload.get('label'), pr.INCLUDED_LABEL)
        self.assertNotIn('followups', payload)
        self.assertNotIn('staff account', str(payload))
        state, charged = pr.offer_or_charge(text=Q9, subject='Hazbin Hotel pilot', kind='viewership',
                                            panel_confirm={'subject': 'Hazbin Hotel pilot'}, username='graceyin',
                                            user=user, history=None, host=host)
        self.assertEqual(state, 'charged')
        self.assertEqual(charged.get('credits'), 0)
        self.assertFalse(charged.get('staff'))
        self.assertTrue(charged.get('covered'))
        self.assertTrue(charged.get('included'))


class SignupCountIsAQuestion(unittest.TestCase):
    def test_her_count_question(self):
        self.assertTrue(pma.is_signup_count_ask(Q2))
        self.assertTrue(pma.is_signup_count_ask(
            'How many people were paid signs up for Hazbin Hotel Season 1 in the US during its first 28 day from launch on Prime Video?'))

    def test_orders_and_other_counts_are_not(self):
        self.assertFalse(pma.is_signup_count_ask('Pull a Subscriber IQ on Landman'))
        self.assertFalse(pma.is_signup_count_ask('how many people watched Landman'))


class ShareOfTheFile(unittest.TestCase):
    def test_the_hundredfold_slip_is_recomputed(self):
        txt = ("The shape is young, female-leaning and queer-heavy. The intense core is 31.4% of the file, 42,957,147 people, "
               "which is a large most engaged tier for a single-season sign-up cohort.")
        info = ac.check(txt, counts=[('the file', 1368062)], subject=PAGE)
        self.assertTrue(info['changed'])
        self.assertIn('31.4% of the file, 429,571 people', info['text'])
        self.assertEqual(len(info['fixes']), 1)

    def test_a_right_count_stands(self):
        txt = 'The intense core is 31.4% of the file, 429,571 people, a large tier.'
        info = ac.check(txt, counts=[('the file', 1368062)], subject=PAGE)
        self.assertFalse(info['changed'])

    def test_no_whole_passed_leaves_the_line(self):
        txt = 'The intense core is 31.4% of the file, 42,957,147 people, a large tier.'
        info = ac.check(txt, counts=None, subject=PAGE)
        self.assertFalse(info['changed'])
        self.assertTrue(info['unresolved'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
