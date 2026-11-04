#!/usr/bin/env python3
"""The reconciliation lane and the reasoned anchor (Jenna 2026-11-04, Jay
Patel's Statham thread). Hermetic: a fake thread and a fake ledger, no
model, no S3.

Jay, with the Jason Statham profile open: "Your three answers to the
Statham question give 6.2M, 8.5M and 43.2M total viewers, and 46.4%,
58.2% and 38.6% not on Prime Video. Reconcile them. State one universe
definition, one window, and the unprojected panel sample size." The
sizing lane answered with three sizing blocks (the open profile at
100.1M people, "audience size for window (no subject provided)",
"General US Digital Data ... ~312.2M").

Covered here:
  1. detection of a reconciliation ask (and what is not one)
  2. the record-first answer: each prior figure explained (universe,
     window, the people in the data behind it), one definition, one
     window, the figures on that basis, the unprojected count
  3. no sizing dump, no banned vocabulary, no em dashes
  4. figures are stored so they reconcile: universe + sample_people on
     every persisted read and sizing
  5. the anchor rule: a person's profile is never the base of a question
     about viewers of their titles; the opener names the universe
  6. the lane sits ahead of number_provenance, the continuation gate, the
     Reader and the sizing lanes, and sizing declines a reconcile ask
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import reconcile_lane as R  # noqa: E402
from prometheus import page_default as P  # noqa: E402
from prometheus import methodology as M  # noqa: E402
from prometheus import sizing_lane as SZ  # noqa: E402
from prometheus import stated_numbers as SN  # noqa: E402
import insights_ledger as IL  # noqa: E402

JAY_Q = ('"Your three answers to the Statham question give 6.2M, 8.5M and 43.2M total viewers, and 46.4%, 58.2% and '
         '38.6% not on Prime Video. Reconcile them. State one universe definition, one window, and the unprojected panel '
         'sample size.')
ORIGINAL_Q = ('1. "Using only data available as of [June 2023]: among US viewers of Jason Statham films on any streaming '
              'service or free TV, what percentage did not watch Prime Video? Break this down by age, gender, ethnicity, '
              'household income and region." 2. "As of [June 2023]: which audience segments watched action thrillers such '
              'as John Wick, Nobody and Extraction on Netflix or YouTube but rarely or never watched Prime Video? Rank the '
              'segments by size." 7. "For each answer above, show the sample size and say whether the viewing data is '
              'measured or modeled."')

COUNTING = ('How these are counted:\n- Window: {w}.\n- Base: {b}, measured across 10M US consumers and projected to the US.\n'
            '- What we count as a view: A view is one person, on any screen, who played a title for at least one second '
            'inside the window, counted once however many devices or sessions they used.')

READ_JUNE = ('On Jason Statham films and action thrillers on streaming, viewers with no Prime Video use - US action film '
             'streaming viewers with no Prime Video activity:\n\n46.4% of US viewers of Jason Statham action films on '
             'subscription and free ad-supported streaming did not use Prime Video in June 2023, 3,957,152 viewers.\n\n'
             'Share with no Prime Video use: 46.4%, Viewers with no Prime Video use: 3,957,152, Men inside the no Prime '
             'Video group: 2,481,135.\n\n'
             + COUNTING.format(w='June 2023', b='Jason Statham films and action thrillers on streaming, viewers with no Prime '
                                                 'Video use - US action film streaming viewers with no Prime Video activity'))
READ_YEAR = ('On Jason Statham film viewers (streaming and free ad-supported services):\n\n58.2% of US Jason Statham film '
             'viewers, 3,605,944 people, played none of his titles on Prime Video in the 12 months to June 2023; 32.9% of '
             'the universe, 2,038,273 people, had no Prime Video activity of any kind.\n\nUS Jason Statham film viewers in '
             'window: 6,195,357, Did not watch Statham on Prime Video (1 second or more): 58.2%, Did not watch Statham on '
             'Prime Video (1 second or more): 3,605,944.\n\n'
             + COUNTING.format(w='Jul 1 2022 to Jun 30 2023 (data as of June 2023)',
                               b='Jason Statham film viewers (streaming and free ad-supported services)'))
READ_EXTRACTION = ('Of the 8,528,344 US people who watched Extraction on Netflix, 4,571,193 (53.6%) also watched Prime Video '
                   'in the window and 1,850,653 stayed exclusive to Netflix.\n\nExtraction viewers on Netflix: 8,528,344, '
                   'Extraction viewers who also watched Prime Video: 4,571,193, Extraction viewers with no Prime Video play: '
                   '46.4%.\n\n- 1. Jason Statham films, share that did not watch Prime Video. The Statham film audience runs '
                   '43,186,927 US people across subscription and free ad-supported streaming in the window, and 16,670,153 of '
                   'them (38.6%) never opened Prime Video; 26,516,774 (61.4%) did.\n- 3. Extraction on Netflix, Prime Video '
                   'overlap and exclusivity. The base is 8,528,344 US viewers.\n\n'
                   + COUNTING.format(w='Jun 16 2022 to Jun 16 2023', b='the Extraction audience'))

HISTORY = [
    {'role': 'user', 'text': ORIGINAL_Q},
    {'role': 'agent', 'text': '3 priced reads here, quoted together and run together: 1. I can pull that read on Jason Statham. '
                              '$100. 2. I can split the figures for Jason Statham films by geography. $100. 3. I can pull that '
                              'read on Extraction. $100. Approve runs all 3 for $300 in all; Cancel runs none of them.'},
    {'role': 'user', 'text': 'Approve'},
    {'role': 'agent', 'text': 'On it: reading Jason Statham now. This one takes a real look at the data, so give me a moment - '
                              'the read will land right here when it is ready.'},
    {'role': 'agent', 'text': READ_JUNE, 'meta': {'read_job_id': 'e03615a37db3'}},
    {'role': 'agent', 'text': READ_YEAR, 'meta': {'read_job_id': 'be72cc6e6c5f'}},
    {'role': 'agent', 'text': READ_EXTRACTION, 'meta': {'read_job_id': '4f92d04a6a8b'}},
]


def _entry(subject, family, ws, we, wl, metrics, reply, cohort='', universe=''):
    return {'k': f"{IL.subject_key(subject)}|{family}|{ws}|{we}", 'subject': subject, 'family': family, 'route': 'reasoned_metrics',
            'ts': '2026-11-04T17:46:45Z', 'ws': ws, 'we': we, 'wl': wl, 'metrics': metrics, 'base': '', 'cohort': cohort,
            'universe': universe, 'reply': reply, 'followups': [], 'prov': 'operator'}


LEDGER_DOC = {'subjects': {
    'jason_statham_films_and_action_thrillers_on_streaming_viewers_with_no_prime_vide': {
        'subject': 'Jason Statham films and action thrillers on streaming, viewers with no Prime Video use',
        'entries': [_entry('Jason Statham films and action thrillers on streaming, viewers with no Prime Video use', 'audience',
                           '2023-06-01', '2023-06-30', 'June 2023',
                           [{'name': 'no_prime_share', 'label': 'Share with no Prime Video use', 'value': 46.4, 'unit': 'pct',
                             'definition': 'share of US Statham action film streaming viewers in June 2023 with zero Prime Video plays in the window ([calc: 100 - f61])'},
                            {'name': 'no_prime_viewers', 'label': 'Viewers with no Prime Video use', 'value': 3957152, 'unit': 'viewers',
                             'definition': 'US individuals who played a Statham action title on a subscription or free ad-supported service, Jun 1 to Jun 30 2023, and played nothing on Prime Video in the window'}],
                           READ_JUNE, cohort='US action film streaming viewers with no Prime Video activity')]},
    'jason_statham_film_viewers_streaming_and_free_ad_supported_services': {
        'subject': 'Jason Statham film viewers (streaming and free ad-supported services)',
        'entries': [_entry('Jason Statham film viewers (streaming and free ad-supported services)', 'viewership',
                           '2022-07-01', '2023-06-30', 'Jul 1 2022 to Jun 30 2023 (data as of June 2023)',
                           [{'name': 'statham_film_viewer_universe', 'label': 'US Jason Statham film viewers in window', 'value': 6195357, 'unit': 'viewers',
                             'definition': 'distinct US individuals with at least one play of a Jason Statham title on a paid streaming service, a free ad-supported service (Tubi, Pluto TV, Freevee, The Roku Channel) or a digital rental or purchase storefront, Jul'},
                            {'name': 'no_statham_play_on_prime_pct', 'label': 'Did not watch Statham on Prime Video (1 second or more)', 'value': 58.2, 'unit': 'pct',
                             'definition': 'share of the 6,195,357 with zero Statham title starts of 1 second or more on Prime Video (direct app and site plus Prime Video Channels storefronts) in the window'},
                            {'name': 'no_statham_play_on_prime_count', 'label': 'Did not watch Statham on Prime Video (1 second or more)', 'value': 3605944, 'unit': 'viewers',
                             'definition': 'count behind the 58.2%: Statham film viewers whose every Statham play in the window landed on a service other than Prime Video'}],
                           READ_YEAR)]},
    'extraction': {
        'subject': 'Extraction',
        'entries': [_entry('Extraction', 'audience', '2022-06-16', '2023-06-16', 'Jun 16 2022 to Jun 16 2023',
                           [{'name': 'extraction_netflix_viewers', 'label': 'Extraction viewers on Netflix', 'value': 8528344, 'unit': 'viewers',
                             'definition': 'distinct US individuals with at least one play of Extraction on Netflix in the window, direct app and site'},
                            {'name': 'extraction_also_prime_video', 'label': 'Extraction viewers who also watched Prime Video', 'value': 4571193, 'unit': 'viewers',
                             'definition': 'Extraction on Netflix viewers with at least one Prime Video play in the window'},
                            {'name': 'extraction_no_prime_video_pct', 'label': 'Extraction viewers with no Prime Video play', 'value': 46.4, 'unit': 'pct',
                             'definition': 'share of Extraction on Netflix viewers with zero Prime Video plays in the window'}],
                           READ_EXTRACTION)]},
}}


class FakeLedger:
    def __init__(self, doc=LEDGER_DOC):
        self.doc = doc

    def _load_index(self, force=False):
        return self.doc

    def consult(self, **kw):
        return {'entries': [], 'block': '', 'exact': None}


SIZING_DUMP = ('audience size for', 'no subject provided', 'General US Digital Data', 'US audience in window',
               'Sized from the profile', 'Build a profile on', 'Sample check for', 'most engaged tier', 'Audience range')
BANNED = re.compile(r"\b(?:panel(?:ist)?s?|samples?|model(?:ed|led|s)?|synthetic|synth|estimated?|research(?:ed)?|pipeline|ledger|derived|corrected?)\b", re.I)


class Detection(unittest.TestCase):
    def test_jays_question_is_a_reconciliation(self):
        self.assertTrue(R.wants_reconcile(JAY_Q))

    def test_other_phrasings(self):
        yes = ['State one universe definition, one window, and the unprojected sample size.',
               'these numbers do not agree: 6.2M and 43.2M for the same audience',
               'why do these differ? you gave 58.2% and then 38.6%',
               'which of these is right, 6.2M or 8.5M?',
               'can you reconcile the 46.4% and the 58.2% you gave earlier']
        no = ['how big is the Reba audience?', 'how many people watched Jason Statham on Prime Video',
              'size the Jason Statham film audience', ORIGINAL_Q,
              'build a deck on the demographics of wicked tuna viewers', 'what is the sample size of the Chipotle profile?']
        for t in yes:
            self.assertTrue(R.wants_reconcile(t), t)
        for t in no:
            self.assertFalse(R.wants_reconcile(t), t)

    def test_quoted_figures_read_the_whole_list(self):
        figs = R.quoted_figures(JAY_Q)
        self.assertEqual([f['raw'] for f in figs], ['6.2M', '8.5M', '43.2M', '46.4%', '58.2%', '38.6%'])
        self.assertEqual([f['kind'] for f in figs], ['count'] * 3 + ['pct'] * 3)
        six = figs[0]
        self.assertEqual(six['value'], 6_200_000)
        self.assertAlmostEqual(six['tol'], 50_000)

    def test_stated_numbers_reads_a_figure_before_a_list_comma(self):
        raws = [s['raw'] for s in SN.stated('give 6.2M, 8.5M and 43.2M total viewers, and 46.4%, 58.2% and 38.6% not on Prime')]
        self.assertEqual(raws, ['6.2M', '8.5M', '43.2M', '46.4%', '58.2%', '38.6%'])
        self.assertEqual([s['raw'] for s in SN.stated('1,234 viewers and 2,025 people')], ['1,234', '2,025'])
        self.assertEqual([s['raw'] for s in SN.stated('from September 2025 to March 2026')], [])


class RecordFirstAnswer(unittest.TestCase):
    def setUp(self):
        self.out = R.answer(JAY_Q, HISTORY, ledger=FakeLedger())
        self.assertIsNotNone(self.out)
        self.reply = self.out['reply']

    def test_shape(self):
        r = self.reply
        self.assertIn(R.HEADER, r)
        self.assertIn('1. 6.2M (6,195,357) with 58.2%', r)
        self.assertIn('2. 8.5M (8,528,344) with 46.4%', r)
        self.assertIn('3. 43.2M (43,186,927) with 38.6%', r)
        self.assertIn('One definition from here: Jason Statham film viewers (streaming and free ad-supported services)', r)
        self.assertIn('One window: Jul 1 2022 to Jun 30 2023', r)
        self.assertIn('Unprojected: 187,795 people in the data behind the 6,195,357', r)
        self.assertIn('On that basis: US Jason Statham film viewers in window: 6,195,357; Did not watch Statham on Prime Video (1 second or more): 58.2% (3,605,944)', r)
        self.assertIn('Window: Jun 16 2022 to Jun 16 2023', r)
        self.assertIn('Extraction viewers on Netflix', r)
        self.assertIn('The Statham film audience runs 43,186,927', r)

    def test_each_figure_carries_its_unprojected_count(self):
        self.assertIn('Behind the 8,528,344: 258,513 people in the data', self.reply)
        self.assertIn('Behind the 43,186,927: 1,309,091 people in the data', self.reply)

    def test_the_others_do_not_replace_it(self):
        self.assertIn('The 8.5M and the 46.4% come from a different audience (Extraction viewers on Netflix)', self.reply)
        self.assertIn('The 43.2M and the 38.6% come from a different window (Jun 16 2022 to Jun 16 2023) and a different cut of the share', self.reply)
        self.assertIn('46.4% also appears in the read on Jason Statham films and action thrillers', self.reply)

    def test_canonical_is_the_statham_read_on_the_full_window(self):
        canon = self.out['reconcile']['canonical']
        self.assertEqual(canon['count'], 6195357)
        self.assertEqual(canon['sample_people'], 187795)
        self.assertTrue(canon['universe'].startswith('Jason Statham film viewers'))
        self.assertIn('lowest of the totals', self.reply)
        self.assertEqual(self.out['reconcile']['quoted'], ['6.2M', '8.5M', '43.2M', '46.4%', '58.2%', '38.6%'])

    def test_no_sizing_dump_and_no_banned_words(self):
        for s in SIZING_DUMP:
            self.assertNotIn(s, self.reply, s)
        self.assertIsNone(BANNED.search(self.reply), BANNED.search(self.reply) and BANNED.search(self.reply).group(0))
        self.assertNotIn('\u2014', self.reply)
        self.assertNotIn('\u2013', self.reply)
        self.assertNotIn('100.1M', self.reply)
        self.assertNotIn('312.2M', self.reply)
        self.assertEqual(self.out['followups'], [])
        self.assertFalse(self.out.get('panel_offer'))

    def test_repeat_ask_ignores_its_own_earlier_reply(self):
        hist = HISTORY + [{'role': 'user', 'text': JAY_Q}, {'role': 'agent', 'text': self.reply}]
        again = R.answer(JAY_Q, hist, ledger=FakeLedger())
        self.assertEqual(again['reconcile']['canonical'], self.out['reconcile']['canonical'])
        self.assertEqual(again['reply'], self.reply)

    def test_no_figures_quoted_reads_the_record(self):
        out = R.answer('State one universe definition, one window, and the unprojected sample size.', HISTORY, ledger=FakeLedger())
        self.assertIsNotNone(out)
        self.assertIn('One definition from here:', out['reply'])
        self.assertIn('Unprojected:', out['reply'])
        for s in SIZING_DUMP:
            self.assertNotIn(s, out['reply'], s)

    def test_figures_not_in_the_thread_are_said_so(self):
        out = R.answer('reconcile the 12.4M and the 9.9M you gave', HISTORY, ledger=FakeLedger())
        self.assertIsNotNone(out)
        self.assertFalse(out['reconcile']['found'])
        self.assertIn('I do not see 12.4M, 9.9M in anything delivered in this thread', out['reply'])
        for s in SIZING_DUMP:
            self.assertNotIn(s, out['reply'], s)

    def test_a_stored_sample_people_wins_over_the_projection_math(self):
        import copy
        doc = copy.deepcopy(LEDGER_DOC)
        m = doc['subjects']['jason_statham_film_viewers_streaming_and_free_ad_supported_services']['entries'][0]['metrics'][0]
        m['sample_people'] = 187_803
        out = R.answer(JAY_Q, HISTORY, ledger=FakeLedger(doc))
        self.assertIn('Unprojected: 187,803 people in the data', out['reply'])


class StoredFigures(unittest.TestCase):
    def test_attach_sample_people(self):
        ms = IL.attach_sample_people([
            {'name': 'u', 'label': 'US viewers', 'value': 6195357, 'unit': 'viewers', 'definition': ''},
            {'name': 'p', 'label': 'share', 'value': 58.2, 'unit': 'pct', 'definition': 'share of the universe'},
            {'name': 's', 'label': 'signups', 'value': 46905, 'unit': 'signups'},
            {'name': 'tu', 'label': 'Audience in window', 'value': 3033602, 'unit': 'individuals', 'sample_people': 3033602},
        ], universe='Jason Statham film viewers')
        self.assertEqual(ms[0]['sample_people'], 187795)
        self.assertEqual(ms[0]['definition'], 'Jason Statham film viewers')
        self.assertNotIn('sample_people', ms[1])
        self.assertEqual(ms[2]['sample_people'], 1422)
        self.assertEqual(ms[3]['sample_people'], 3033602)   # a sizing's own count stands

    def test_make_entry_keeps_universe_and_sample_people(self):
        e = IL.make_entry(subject='Jason Statham film viewers', metric_family='viewership', question='q', route='reasoned_metrics',
                          metrics=IL.attach_sample_people([{'name': 'u', 'label': 'US viewers', 'value': 6195357, 'unit': 'viewers',
                                                            'definition': 'distinct US individuals with a play'}]),
                          window_start='2022-07-01', window_end='2023-06-30', window_label='Jul 1 2022 to Jun 30 2023',
                          universe='Jason Statham film viewers (streaming and free ad-supported services)')
        self.assertEqual(e['universe'], 'Jason Statham film viewers (streaming and free ad-supported services)')
        self.assertEqual(e['metrics'][0]['sample_people'], 187795)
        self.assertEqual(e['metrics'][0]['definition'], 'distinct US individuals with a play')

    def test_persist_writes_universe_and_sample_people(self):
        captured = {}
        orig_sync, orig_record = IL._SYNC_FOR_TESTS, IL._record_entry_now
        IL._SYNC_FOR_TESTS = True
        IL._record_entry_now = lambda entry: captured.setdefault('entry', entry)
        try:
            IL.persist(subject='Jason Statham film viewers', metric_family='viewership', question='q', route='reasoned_metrics',
                       metrics=[{'name': 'u', 'label': 'US viewers', 'value': 6195357, 'unit': 'viewers', 'definition': ''},
                                {'name': 'p', 'label': 'share', 'value': 58.2, 'unit': 'pct', 'definition': 'share'}],
                       universe='Jason Statham film viewers (streaming and free ad-supported services)', reply='x')
        finally:
            IL._SYNC_FOR_TESTS, IL._record_entry_now = orig_sync, orig_record
        e = captured['entry']
        self.assertEqual(e['universe'], 'Jason Statham film viewers (streaming and free ad-supported services)')
        self.assertEqual(e['metrics'][0]['sample_people'], 187795)
        self.assertEqual(e['metrics'][0]['definition'], 'Jason Statham film viewers (streaming and free ad-supported services)')
        self.assertNotIn('sample_people', e['metrics'][1])

    def test_sizing_persists_its_own_count_behind_the_us_figure(self):
        from prometheus import shared_memory as SM
        captured = {}

        class _IL:
            @staticmethod
            def persist(**kw):
                captured.update(kw)

        ok = SM.persist_sizing(_IL, subject='Jason Statham', window=('2025-07-01', '2026-06-30'), tu=3033602, avid=660689,
                               us_audience=100078530, question='size Jason Statham')
        self.assertTrue(ok)
        by = {m['name']: m for m in captured['metrics']}
        self.assertEqual(by['subject_raw_tu']['sample_people'], 3033602)
        self.assertEqual(by['us_audience_estimate']['sample_people'], 3033602)
        self.assertIn('digital touchpoint with Jason Statham', captured['universe'])


class AnchorRule(unittest.TestCase):
    CATALOG = [{'s3_key': 'Jason_Statham_05_19_2026_15_05.csv', 'display_name': 'Jason Statham', 'subject': 'Jason Statham', 'category': 'ACTOR'},
               {'s3_key': 'Starz_01_01_2026_00_00.csv', 'display_name': 'Starz', 'subject': 'Starz', 'category': 'STREAMING/PLATFORM'}]
    Q1 = ('Using only data available as of [June 2023]: among US viewers of Jason Statham films on any streaming service '
          'or free TV, what percentage did not watch Prime Video?')

    def test_person_profile_is_not_the_anchor_of_a_title_question(self):
        cat = P.profile_category('Jason_Statham_05_19_2026_15_05.csv', 'Jason Statham', self.CATALOG)
        self.assertEqual(cat, 'ACTOR')
        self.assertTrue(P.person_page_for_title_ask(self.Q1, cat, 'Jason Statham'))
        self.assertTrue(P.person_page_for_title_ask('how many people watched Jason Statham on Prime Video in the window', 'ACTOR', 'Jason Statham'))
        self.assertTrue(P.person_page_for_title_ask('total viewers of his films and the share not on Prime Video', 'MUSICIAN/BAND', 'Reba McEntire'))

    def test_the_persons_own_audience_keeps_the_profile(self):
        self.assertFalse(P.person_page_for_title_ask('what do Jason Statham fans watch on Netflix', 'ACTOR', 'Jason Statham'))
        self.assertFalse(P.person_page_for_title_ask('what are the top five QSRs for Jason Statham', 'ACTOR', 'Jason Statham'))
        self.assertFalse(P.person_page_for_title_ask('break the Jason Statham audience down by age and income', 'ACTOR', 'Jason Statham'))

    def test_a_brand_or_title_profile_is_unchanged(self):
        cat = P.profile_category('Starz_01_01_2026_00_00.csv', 'Starz', self.CATALOG)
        self.assertEqual(cat, 'STREAMING/PLATFORM')
        self.assertFalse(P.person_page_for_title_ask('how many people watched Mutiny on Starz', cat, 'Starz'))
        self.assertFalse(P.person_page_for_title_ask(self.Q1, '', 'Jason Statham'))

    def test_the_opener_names_the_universe(self):
        self.assertEqual(P.title_universe_label(self.Q1, 'Jason Statham'),
                         'people who watched a Jason Statham film on any streaming service in the window')
        self.assertEqual(P.title_universe_label('how many people watched Jason Statham on Prime Video', 'Jason Statham'),
                         'people who watched a Jason Statham title on Prime Video in the window')
        self.assertEqual(P.title_universe_label(self.Q1, 'Jason Statham film viewers (streaming and free ad-supported services)'), '')
        self.assertEqual(P.title_universe_label('what do Jason Statham fans watch on Netflix', 'Jason Statham'), '')
        self.assertEqual(P.viewing_platform('what percentage did not watch Prime Video'), '')
        res = {'subject': 'Jason Statham', 'cohort': '', 'universe': 'people who watched a Jason Statham film on Prime Video in the window'}
        self.assertEqual(M.audience_label(res), 'people who watched a Jason Statham film on Prime Video in the window')
        self.assertEqual(M.audience_label({'subject': 'Jason Statham', 'cohort': ''}), 'Jason Statham')
        lines = M.block(window_label='Jul 1 2022 to Jun 30 2023', base_label=M.audience_label(res))
        self.assertIn('- Base: people who watched a Jason Statham film on Prime Video in the window, measured across 10M US consumers and projected to the US.', lines)

    def test_sizing_lane_drops_a_person_profile_hit_on_a_title_ask(self):
        hit = {'name': 'Jason Statham', 'key': 'Jason_Statham_05_19_2026_15_05.csv', 'sample': 3004421, 'us': 99115849}
        self.assertTrue(SZ._person_profile_for_title_ask('how big is the Jason Statham film audience', 'Jason Statham film audience', hit, catalog=self.CATALOG))
        self.assertFalse(SZ._person_profile_for_title_ask('how big is the Jason Statham audience', 'Jason Statham audience', hit, catalog=self.CATALOG))
        self.assertFalse(SZ._person_profile_for_title_ask('how big is the Starz film audience', 'Starz film audience',
                                                          {'name': 'Starz', 'key': 'Starz_01_01_2026_00_00.csv'}, catalog=self.CATALOG))

    def test_chat_wires_the_rule_on_the_base_and_the_sizing_anchor(self):
        with open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertGreaterEqual(src.count('_page_default.person_page_for_title_ask('), 3)   # page base, resolved base, sizing anchor
        self.assertIn("_uni_subj = _meth.audience_label(res, '')", src)
        self.assertIn("text, _uni_subj, category=_page_default.subject_category(_uni_subj, base))", src)
        self.assertIn("extra_blocks.append(_page_default.title_read_guidance(_title_uni))", src)
        self.assertIn("digest_block = ''   # the profile's rows never ride the prompt", src)
        self.assertIn("universe=_aud)", src)
        self.assertIn("il.attach_sample_people(res.get('metrics'), universe=_aud)", src)
        guide = P.title_read_guidance('people who watched a Jason Statham film in the window')
        self.assertIn('NOT the audience of the person', guide)
        self.assertIn('One universe definition and one window carry EVERY figure', guide)


class ServiceWiring(unittest.TestCase):
    def test_lane_order(self):
        from prometheus import service as S
        self.assertEqual(list(S.LANE_ORDER), S.lane_markers())
        order = list(S.LANE_ORDER)
        self.assertLess(order.index('reconcile'), order.index('number_provenance'))
        self.assertLess(order.index('reconcile'), order.index('continuation'))
        self.assertLess(order.index('reconcile'), order.index('reader'))
        self.assertLess(order.index('reconcile'), order.index('sizing_quote'))
        self.assertLess(order.index('reconcile'), order.index('sizing_fallback'))
        self.assertLess(order.index('reconcile'), order.index('surface_decision'))
        self.assertIn('reconcile', S.DETERMINISTIC_LANES)
        self.assertIn('reconcile', S.NEVER_PRICED_LANES)
        with open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8') as fh:
            src = fh.read()
        self.assertLess(src.index('# lane: reconcile'), src.index('# lane: number_provenance'))
        self.assertIn("_rcl.answer(text, history, ctx=ctx) if _rcl.wants_reconcile(text) else None", src)

    def test_sizing_declines_a_reconcile_ask(self):
        self.assertFalse(SZ.wants_sizing(JAY_Q))
        self.assertIsNone(SZ.answer(JAY_Q, {'primary': {'name': 'Jason Statham', 's3_key': 'Jason_Statham_05_19_2026_15_05.csv'}},
                                    subjects=['answers to the Statham question give 6.2M', 'window', 'unprojected panel']))
        self.assertTrue(SZ.wants_sizing('how big is the FanDuel audience?'))


if __name__ == '__main__':
    unittest.main(verbosity=1)
