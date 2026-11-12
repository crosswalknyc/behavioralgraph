#!/usr/bin/env python3
"""A read brief is one read on the titles it names (Grace Yin, Amazon,
2026-11-11; prometheus.read_brief).

Grace sent three messages with a Men 18-34 profile open. The first sized
the fan audience of two novels, year by year and by source, with two
questions about how we count and a file to send. It was cut in two; one
half came back as an offer to build eight year profiles of the open
profile and the other was read as a cross, found no figures and ended on
"Working on it ... I will email you". The second disowned the offer and
asked whether it was one task and what it cost; the half that carried
the word "unprojected" got the unprojected size of the open profile. The
third ran as a split of a held figure on a library file a demographic
word resembled.

Hermetic: a stub host, no model, no S3, no ledger. Covered, each from
her exact words:
  1. the three messages are read for what they are (a brief, a message
     about the brief, a brief)
  2. the first message goes to the read lane whole, bound to the two
     titles: never split, never a cross, never the calm line, never an
     ops note, never the year-profile offer, never the page
  3. the message about the brief is answered in plain words and starts
     nothing; the go-ahead after it runs the brief
  4. the third message is the same read, not a split of a held figure
  5. a question about how we count is never a cross
  6. the brief's universe is US people with the Men 18-34 page open
  7. the frame the read lane holds the brief to
  8. a priced pass with no figures and nothing charged goes on to the
     read lanes; an approved one keeps the calm line and the ops note
  9. the lane's place in the order; what is not a brief stays where it was
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import read_brief as RB  # noqa: E402

FIRST = (
    'Please size the fan audience for two novels in one run: The Love Hypothesis by Ali Hazelwood and The Map That Leads To You '
    'by J.P. Monninger.\n\nUse the same setup for both titles:\n- Population: all US people (not only streaming users), projected '
    'to the US population\n- Years: each calendar year from 2019 through 2026 (2026 = year to date)\n- "Engaged" means any of: '
    'buying the book (print, ebook or audiobook), Goodreads activity, reading on Wattpad or fan-fiction sites, BookTok/TikTok '
    'videos, YouTube videos, fan sites, and searching for the title\n- Count each person once per year, even if they engaged in '
    'several ways\n\nFor each title and each year, please show:\n1. Total US people engaged\n2. The same total split by source '
    '(books, Goodreads, Wattpad/fan fiction, TikTok, YouTube, fan sites, search)\n3. Share who are women 18\u201334 and share who '
    'are men 18\u201334\n4. The unprojected panelist count behind every figure\n\nPlease also tell me:\n- Which of these sources '
    'your panel can\'t capture, or only partly captures (for example, phone apps)\n- Whether any figure is modeled rather than '
    'counted\n\nPlease send it as a CSV. If any cut falls below 100 panelists, merge it and note what you changed.')
SECOND = (
    "I didn't request an 8-year profile of Men 18\u201334 Prime Members Low Prime Video Use; please cancel it and confirm in "
    "writing that nothing is charged.\n\u258e\n\u258e My request was a single run comparing two novels, The Love Hypothesis (Ali "
    "Hazelwood) and The Map That Leads To You (J.P. Monninger): US people engaged with each, by year 2019\u20132026, split by "
    "source, with women 18\u201334 / men 18\u201334 shares and unprojected panelist counts, delivered as a CSV.\n\u258e\n\u258e "
    "Before running it, please tell me: is that one task or 16 profiles, and what does it cost? Please don't start anything "
    "billable without my written OK.\n\u258e\n\u258e Note that the minimum is 100 panelists, not 100 projected people.")
THIRD = (
    'Please run this read: US people engaged with The Love Hypothesis (Ali Hazelwood) and The Map That Leads To You (J.P. '
    'Monninger), side by side, using the same population, engagement definition and windows for both. Show each year '
    '2019\u20132026 (one profile per year is fine), split by source (book purchases, Goodreads, Wattpad/fan fiction, TikTok, '
    'YouTube, fan sites, search), with the share who are women 18\u201334 and men 18\u201334, and the unprojected panelist count '
    'for every figure.')
# the half the splitter handed the priced lane on 2026-11-12 00:23 UTC
SPLIT_HALF = ("tell me: - Which of these sources your panel can't capture, or only partly captures (for example, phone apps) - "
              "Whether any figure is modeled rather than counted Please send it as a CSV. If any cut falls below 100 panelists, "
              "merge it and note what you changed.?")
YEAR_OFFER = ("A 8-year read of Men 18-34 Amazon Prime Members Low Prime Video Use needs one profile per year - each year's "
              "audience is measured in its own window, and the file on hand covers a single window only.\n\nThe package: 8 year "
              "profiles (2019, 2020, 2021, 2022, 2023, 2024, 2025 and 2026) at $300 each.")
PAGE = {'primary': {'name': 'Men 18-34 Amazon Prime Members Low Prime Video Use',
                    's3_key': 'Men_18_34_Amazon_Prime_Members_Low_Prime_Video_Use_11_10_2026_22_35.csv'}}
SUBJECT = 'The Love Hypothesis vs The Map That Leads To You'
INTERNAL_WORDS = re.compile(r"\b(?:panelists?|synth\w*|modell?ed|estimated?|pipeline|sample)\b", re.I)


class TheWords(unittest.TestCase):
    def test_the_titles_are_the_two_novels(self):
        for q in (FIRST, SECOND, THIRD):
            self.assertEqual(RB.titles(q), [('The Love Hypothesis', 'Ali Hazelwood'), ('The Map That Leads To You', 'J.P. Monninger')], q[:40])

    def test_the_first_and_third_are_briefs_to_run(self):
        for q in (FIRST, THIRD):
            got = RB.read(q)
            self.assertIsNotNone(got, q[:40])
            self.assertEqual(got['subject'], SUBJECT)
            self.assertEqual(got['titles'], ['The Love Hypothesis', 'The Map That Leads To You'])

    def test_the_second_is_about_the_brief_and_starts_nothing(self):
        self.assertTrue(RB.holds(SECOND))
        self.assertIsNone(RB.read(SECOND))

    def test_the_two_questions_about_how_we_count(self):
        self.assertEqual(RB.method_questions(FIRST), ['coverage', 'counted'])
        self.assertEqual(RB.method_questions(SPLIT_HALF), ['coverage', 'counted'])
        self.assertEqual(RB.method_questions('Are any of these figures modeled or imputed rather than directly counted?'), ['counted'])
        for q in ('Which of these services do Hazbin Hotel viewers use most?',
                  'how many UFC fans are there out there, of those, how many are on Paramount+, on HBO Max and how many have neither service',
                  'How many people watched Gilmore Girls on Netflix?'):
            self.assertEqual(RB.method_questions(q), [], q)

    def test_what_is_not_a_brief(self):
        for q in ('How many people watched Gilmore Girls on Netflix by year 2019-2026?',
                  'Size the audience for Reba McEntire',
                  'How big is the fan audience for The Love Hypothesis by Ali Hazelwood?',
                  'Please queue one Profile IQ: US people who watched Fallout Season 2 on Prime Video, Nov 1 2025 - Oct 31 2026. '
                  'Deliver as a CSV with the unprojected panelist count for each figure.',
                  'Give me a 4-5 year analysis of Will and Grace', 'What does a profile cost?', 'cancel that'):
            self.assertFalse(RB.is_brief(q), q)
            self.assertIsNone(RB.read(q), q)
            self.assertIsNone(RB.housekeeping(q, []), q)


class TheLanesThatTookIt(unittest.TestCase):
    def test_a_question_about_how_we_count_is_never_a_cross(self):
        from prometheus import cross_sizing as CX, priced_read as PR
        self.assertIsNone(CX.parse(SPLIT_HALF))
        self.assertIsNone(PR.classify(SPLIT_HALF, [], PAGE))
        # the cross it was built for still parses
        self.assertTrue(CX.parse('how many UFC fans are there out there, of those, how many are on Paramount+, on HBO Max and '
                                 'how many have neither service'))

    def test_the_year_profile_offer_stands_aside(self):
        from prometheus.legacy import rankers as R
        for q in (FIRST, SECOND, THIRD):
            self.assertIsNone(R._pm_detect_multi_year_ask(q), q[:40])
        # the study it was built for is still detected
        self.assertTrue(R._pm_detect_multi_year_ask('Give me a 4-5 year analysis of Will and Grace'))
        self.assertEqual(R._pm_detect_multi_year_ask('How has the Reba audience changed 2021-2024?'), [2021, 2022, 2023, 2024])

    def test_the_universe_is_us_people_with_the_men_18_34_page_open(self):
        from prometheus import question_universe as QU
        page = PAGE['primary']['name']
        for q in (FIRST, SECOND, THIRD):
            u = QU.question_universe(q, page)
            self.assertEqual((u.kind, u.label, u.base), ('named', 'US people', 'US people'), q[:40])
        # a question that does point at the page keeps it
        self.assertEqual(QU.question_universe('What share of this audience is men 18-34?', page).kind, 'page')

    def test_split_by_source_is_not_a_split_of_a_held_figure(self):
        import prometheus_analysis as pma
        from prometheus import derived_read as DR, named_subject as NS
        kw = dict(pma=pma, ns=NS, normalize=lambda s: str(s).lower(), stop=set(), plausible=lambda s: True, consumption=lambda t: '')
        for q in (FIRST, THIRD):
            self.assertIsNone(DR.plan(q, [], PAGE, {'text': q}, **kw), q[:40])

    def test_the_frame_the_read_lane_holds(self):
        from prometheus import question_universe as QU
        block = QU.framing_block(QU.question_universe(FIRST, PAGE['primary']['name']), FIRST)
        for line in ('TITLE SIZING BRIEF', 'The Love Hypothesis (Ali Hazelwood); The Map That Leads To You (J.P. Monninger)',
                     'never share a share, a curve or a ratio', 'keeps it', 'low end of what can be stood behind',
                     'A year before a book was published carries no purchases', 'the sources add to more than the total',
                     'One value per fact', 'multiplied by 10,000,000 and divided by 329,900,000', 'merge any cell under it',
                     RB.counted_line()):
            self.assertIn(line, block)
        self.assertEqual(RB.framing_lines('How many people watched Gilmore Girls on Netflix?'), [])


class ThroughTheDoor(unittest.TestCase):
    """service.ask with a stub host."""

    @classmethod
    def setUpClass(cls):
        from prometheus.host import host
        cls.calls, cls.mails, cls.hints = [], [], []

        def _analyze_core(user, body, text, history):
            cls.calls.append(('analyze', text, dict(body)))
            return {'success': True, 'action': 'answer', 'reply': 'On it: reading the two titles now.', 'followups': [], 'read_job_id': 'job1'}

        def _interpret_core(user, body, text, history):
            cls.calls.append(('interpret', text, dict(body)))
            return {'success': True, 'reply': 'draft', 'draft': {'subject': 'x'}}

        host.bind(analyze_core=_analyze_core, interpret_core=_interpret_core,
                  deck_core=lambda *a: {'success': True, 'reply': 'deck'},
                  ask_hint=lambda **k: cls.hints.append(k), gate_analyze=lambda u: True, gate_pull=lambda u: True,
                  funds_gate=lambda u: None, gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
                  calm_payload=lambda: {'success': False, 'error': 'calm'}, error_email=lambda *a, **k: cls.mails.append(a),
                  load_threads_index=lambda u: {'threads': []}, max_threads=50,
                  s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
                  thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u,
                  thread_title_from=lambda t: t[:30], bucket='x', s3_client=None,
                  job_owner_ok=lambda a, b: True, claude_data=lambda *a, **k: {},
                  load_users=lambda: {'users': {'canary:brief': {'username': 'canary:brief', 'unlimited': True, 'company': 'Amazon'}}},
                  check_user_credits=lambda u: (True, 100))
        from prometheus import service
        cls.service = service
        cls.user = {'username': 'canary:brief', 'unlimited': True, 'company': 'Amazon'}

    def _ask(self, text, surface='analyze', history=None, page=PAGE, **extra):
        self.calls.clear(), self.mails.clear(), self.hints.clear()
        body = {'text': text, 'surface': surface, 'client': 'probe', 'history': list(history or [])}
        if page:
            body['page_context'] = page
        body.update(extra)
        return self.service.ask(self.user, body)

    def _routes(self):
        return [h.get('route') for h in self.hints if h.get('route')]

    def test_the_first_message_is_one_read_on_the_two_titles(self):
        env, st = self._ask(FIRST)
        self.assertEqual(st, 200)
        self.assertEqual(len(self.calls), 1, self.calls)                 # one core call: never split
        kind, text, body = self.calls[0]
        self.assertEqual(kind, 'analyze')                                # a read, never the build flow
        self.assertEqual(text, FIRST)                                    # the whole brief, both questions and the file note
        self.assertEqual(body.get('bind_subject'), SUBJECT)              # bound to the titles, never the page
        self.assertNotEqual(env['decision']['reason'], 'multi_ask')
        self.assertNotEqual(env['decision']['reason'], 'priced_read')
        self.assertNotIn('Working on it. Confirmed your task is in progress', env['text'])
        self.assertNotIn('year profiles', env['text'])
        self.assertEqual(self.mails, [])                                 # ops is not told a person needs an email
        self.assertIn('read_brief', self._routes())
        for gone in ('multi_ask', 'priced_read', 'cross', 'sizing', 'unprojected'):
            self.assertNotIn(gone, self._routes())

    def test_the_first_message_on_the_build_surface_is_still_a_read(self):
        env, _st = self._ask(FIRST, surface='interpret')
        self.assertEqual([c[0] for c in self.calls], ['analyze'])
        self.assertEqual(self.calls[0][2].get('bind_subject'), SUBJECT)

    def test_the_message_about_the_brief_is_answered_and_starts_nothing(self):
        hist = [{'role': 'user', 'text': FIRST},
                {'role': 'agent', 'text': YEAR_OFFER, 'meta': {'options': [{'label': 'Build Men 18-34 year profiles'}]}}]
        env, st = self._ask(SECOND, history=hist)
        self.assertEqual(st, 200)
        self.assertEqual(self.calls, [])                                 # nothing ran
        self.assertEqual(env['decision']['reason'], 'read_brief')
        reply = env['text']
        self.assertIn('Nothing was started and nothing is charged.', reply)
        self.assertIn('that offer is withdrawn', reply)
        self.assertIn('Your request is one task: one read covering both titles and every year, delivered as one file.', reply)
        self.assertIn('It needs no profiles.', reply)
        # a probe reads the paying seat's shape (priced_read.included_with_plan); the covered seat's line is below
        self.assertIn('Pulling this data will cost $100, charged once, only after you approve it.', reply)
        self.assertIn('will not start until you say so', reply)
        self.assertIn('fewer than 100 people in the data', reply)
        self.assertNotIn('224,164', reply)                               # never the open profile's unprojected size
        self.assertNotIn('Men 18-34', reply)
        self.assertIsNone(INTERNAL_WORDS.search(reply), reply)
        self.assertNotIn('\u2014', reply)
        self.assertEqual(env['raw'].get('followups'), [RB.HOUSEKEEPING_RUN_CHIP])

    def test_a_covered_seat_reads_included_and_no_price(self):
        got = RB.housekeeping(SECOND, [{'role': 'agent', 'text': YEAR_OFFER}], included=True)
        self.assertIn('Included with your plan, so it carries no charge.', got['reply'])
        self.assertNotIn('$', got['reply'])
        self.assertIsNone(INTERNAL_WORDS.search(got['reply']), got['reply'])

    def test_an_offer_that_did_start_something_is_not_waved_away(self):
        hist = [{'role': 'agent', 'text': 'Profile building: Men 18-34.', 'meta': {'run_id': 'abc'}}]
        self.assertIsNone(RB.housekeeping(SECOND, hist, included=True))

    def test_the_go_ahead_runs_the_brief(self):
        hk = RB.housekeeping(SECOND, [{'role': 'agent', 'text': YEAR_OFFER}], included=True)
        hist = [{'role': 'user', 'text': FIRST}, {'role': 'agent', 'text': YEAR_OFFER},
                {'role': 'user', 'text': SECOND}, {'role': 'agent', 'text': hk['reply']}]
        for word in ('Run it', 'Yes, run it', 'go ahead'):
            env, _st = self._ask(word, history=hist)
            self.assertEqual([c[0] for c in self.calls], ['analyze'], word)     # the read, never a build of the page
            self.assertNotIn('build_order', self._routes(), word)
        _kind, text, body = self.calls[0]
        self.assertEqual(body.get('bind_subject'), SUBJECT)
        self.assertIn('The Love Hypothesis (Ali Hazelwood) and The Map That Leads To You (J.P. Monninger)', text)
        self.assertIn('split by source', text)
        self.assertIn('minimum is 100 panelists', text)                  # her floor rides the read
        for gone in ("don't start anything billable", 'what does it cost', 'please cancel it'):
            self.assertNotIn(gone, text)
        # a bare go-ahead with no held brief behind it is not this lane's
        self.assertEqual(RB.pending_run('Run it', [{'role': 'agent', 'text': 'Pulling this data will cost $100.'}]), '')

    def test_the_third_message_is_the_same_read(self):
        env, _st = self._ask(THIRD)
        self.assertEqual([c[0] for c in self.calls], ['analyze'])
        self.assertEqual(self.calls[0][1], THIRD)
        self.assertEqual(self.calls[0][2].get('bind_subject'), SUBJECT)
        self.assertEqual(self.mails, [])

    def test_a_priced_pass_with_no_figures_and_no_charge_goes_on_to_the_read_lanes(self):
        from prometheus import priced_read as PR
        q = 'How many UFC fans are there, and of those how many are on Paramount+ and how many on HBO Max?'

        def _held(text, *, user=None, ctx=None, history=None, host=None, panel_confirm=None):
            host.error_email('prometheus/priced-read', 'priced cross on UFC fans came back with no figures; the user got the calm line')
            return {'success': True, 'action': 'answer', 'reply': PR.with_included_line(PR.CALM_LINE), 'followups': [], 'held': True,
                    'subject': 'UFC fans', 'priced_paid': {'kind': 'cross', 'usd': 0, 'included': True, 'subject': 'UFC fans'}}

        with mock.patch.object(PR, 'service_turn', _held):
            env, _st = self._ask(q, page=None, _multi_part=True)
        self.assertEqual([c[0] for c in self.calls], ['analyze'])       # the read lane took it in the same turn
        self.assertNotIn('Working on it. Confirmed your task is in progress', env['text'])
        self.assertEqual(self.mails, [])                                 # nobody read the calm line, so ops is not told so
        self.assertIn('no_figures_retry', [h.get('outcome') for h in self.hints])

    def test_an_approved_pass_with_no_figures_keeps_the_calm_line_and_the_ops_note(self):
        from prometheus import priced_read as PR
        q = 'How many UFC fans are there, and of those how many are on Paramount+ and how many on HBO Max?'

        def _held(text, *, user=None, ctx=None, history=None, host=None, panel_confirm=None):
            host.error_email('prometheus/priced-read', 'priced cross on UFC fans came back with no figures; the user got the calm line')
            return {'success': True, 'action': 'answer', 'reply': PR.CALM_LINE, 'followups': [], 'held': True, 'subject': 'UFC fans',
                    'priced_paid': {'kind': 'cross', 'usd': 100, 'included': False, 'subject': 'UFC fans'}}

        with mock.patch.object(PR, 'service_turn', _held):
            env, _st = self._ask(q, page=None, _multi_part=True, panel_confirm={'kind': 'cross', 'subject': 'UFC fans'})
        self.assertEqual(self.calls, [])
        self.assertIn('Working on it. Confirmed your task is in progress', env['text'])
        self.assertEqual(len(self.mails), 1)                             # a person looks at the charge

    def test_should_retry(self):
        self.assertTrue(RB.should_retry({'held': True, 'priced_paid': {'usd': 0, 'included': True}}))
        self.assertFalse(RB.should_retry({'held': True, 'priced_paid': {'usd': 100, 'included': False}}))
        self.assertFalse(RB.should_retry({'held': True, 'priced_paid': {'usd': 0, 'included': True}}, {'kind': 'cross'}))
        self.assertFalse(RB.should_retry({'reply': 'About 8.2M people'}))
        self.assertFalse(RB.should_retry(None))


class Wiring(unittest.TestCase):
    def test_lane_order(self):
        from prometheus import service as S
        self.assertEqual(list(S.LANE_ORDER), S.lane_markers())
        order = list(S.LANE_ORDER)
        for later in ('reconcile', 'us_scope', 'segment_sizing', 'number_provenance', 'unprojected', 'standing', 'multi_ask',
                      'sizing_quote', 'reader', 'sizing_fallback', 'surface_decision', 'work_gate'):
            self.assertLess(order.index('read_brief'), order.index(later), later)
        for earlier in ('intake_cancel', 'offer_acceptance', 'priced_offer_acceptance', 'referents'):
            self.assertLess(order.index(earlier), order.index('read_brief'), earlier)
        self.assertIn('read_brief', S.DETERMINISTIC_LANES)
        self.assertIn('read_brief', S.NEVER_PRICED_LANES)

    def test_a_profile_order_is_still_the_build_flow(self):
        from prometheus import orders as O
        q = ('Please queue one Profile IQ: US people who watched Fallout Season 2 on Prime Video, Nov 1 2025 \u2013 Oct 31 2026. '
             'Deliver as a CSV with the unprojected panelist count for each figure.')
        self.assertTrue(O.is_profile_order(q))
        self.assertIsNone(RB.read(q))


if __name__ == '__main__':
    unittest.main(verbosity=1)
