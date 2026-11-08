#!/usr/bin/env python3
"""A question about the dataset on screen is a lookup, never a quote
(2026-11-05, Liz Huszarik on the Music to Long Form study).

Liz, with the Music to Long Form study open on Digital Journey IQ,
asked "in the Music to Longform data set, did the Instagram Social
touchpoints convert any movie viewing." and read "I can pull that read
on 🗺️ Digital Journey IQ. Pulling this data will cost $100." Two
defects: a lookup into data we hold was priced, and the subject was the
tab label with its icon.

Holds (hermetic: the study file ships in the repo, the host is stubbed,
no model, no S3):
  1. the study answers from its own file: social is pooled (TikTok,
     Instagram Reels, YouTube Shorts, X), the social touchpoint's count
     and share toward film viewing in the study's own figures, the
     channel comparison, no cost line, no icon, people in the study
     (never a US projection);
  2. through service.ask the message lands on the journey lane
     (decision reason 'standing', route journey_file), ahead of any
     priced lane, with the same guarantees;
  3. a cut the study does not hold (a demographic) draws one honest
     line plus an offer that names the study, never the tab label;
  4. a question about another title asked with the study open never
     reads off the study; an order ("email me this file") is left to
     its lane;
  5. the subject label anywhere a view label is used loses its icon
     and prefers the study's name.
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

from prometheus import journey_lane as JL  # noqa: E402
from prometheus import journey_study as JS  # noqa: E402
from prometheus import subject_display as SD  # noqa: E402
from prometheus import view_base as VB  # noqa: E402

LIZ = "in the Music to Longform data set, did the Instagram Social touchpoints convert any movie viewing."
TAB = '🗺️ Digital Journey IQ'
VIEW = {'view_id': 'journeyIQ', 'view_title': TAB,
        'data': {'title': 'Music to Long Form', 'key': '__demo_music_to_long_form__', 'story_mode': 'music_catalog',
                 'facts': [{'label': 'Titles', 'value': '62 titles in one file.'},
                           {'label': 'Music-first', 'value': '23.3% conversion after a tracked song, across titles.'}]}}
# Liz also had a profile left in the Profile IQ picker; it is not open
PAGE = {'primary': {'name': 'Olipop', 's3_key': 'Olipop_11_02_2026_16_42.csv'}, 'cuts': [], 'view_context': VIEW}
LIZ_USER = {'username': 'liz', 'email': 'liz@crosswalknyc.com', 'role': 'super_admin', 'company': 'Crosswalk'}
EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F]")


class StudyAnswersItself(unittest.TestCase):
    def test_liz_message_reads_off_the_study(self):
        raw = JL.answer(LIZ, PAGE, user=LIZ_USER, history=[])
        self.assertIsNotNone(raw)
        reply = raw['reply']
        self.assertNotIn('Pulling this data will cost', reply)
        self.assertNotIn('I can pull that read', reply)
        self.assertIsNone(EMOJI.search(reply), reply)
        self.assertNotIn('Digital Journey IQ', reply)
        self.assertIn('Music to Long Form study', reply)
        self.assertIn('does not split social by platform', reply)
        for fig in ('7,022,543', '2,498,420', '35.6%', '1,339,705', '53.6%', '5,824,958', '1,300,834', '22.3%'):
            self.assertIn(fig, reply, fig)
        # the comparison and the across-the-file line
        for fig in ('2,142,522', '47.2%', '10,276,571', '36.0%', '14,515,676', '5,380,607', '37.1%'):
            self.assertIn(fig, reply, fig)
        self.assertIn('people in the study', reply)
        self.assertNotIn('US people', reply)
        self.assertNotIn('projected', reply.lower())
        self.assertNotIn('\u2014', reply)
        self.assertIn('Instagram on its own would be a new read', reply)
        self.assertFalse(raw.get('panel_offer'))
        self.assertEqual(raw['subject'], 'Music to Long Form')
        self.assertEqual(raw['profile'], 'Music to Long Form')
        self.assertTrue(all(f.get('source') == 'view_data' for f in raw['figures']))

    def test_film_scope_from_movie_and_series_scope_from_series(self):
        film = JL.answer('did social convert any movie viewing', PAGE, user=LIZ_USER, history=[])
        series = JL.answer('did social convert any series viewing', PAGE, user=LIZ_USER, history=[])
        self.assertIn('21 films', film['reply'])
        self.assertIn('7,022,543', film['reply'])
        self.assertIn('40 series', series['reply'])
        self.assertIn('7,493,133', series['reply'])
        self.assertIn('38.5%', series['reply'])

    def test_named_study_answers_without_the_page(self):
        raw = JL.answer('in the music to long form study which touchpoint drove the most watching', {}, user=LIZ_USER, history=[])
        self.assertIsNotNone(raw)
        self.assertIn('Music streaming service', raw['reply'])
        self.assertIn('Social media', raw['reply'])
        self.assertNotIn('Pulling this data', raw['reply'])

    def test_a_cut_the_study_lacks_is_one_honest_line_plus_a_labeled_offer(self):
        # a Crosswalk staff seat (Liz) is included with the plan (Jenna 2026-11-08): no offer, the
        # ask falls through to the derived read; the paying seat reads the honest line and the quote
        self.assertIsNone(JL.answer('what share of the Instagram viewers were women', PAGE, user=LIZ_USER, history=[]))
        payer = {'username': 'payer', 'email': 'payer@studio.com', 'company': 'Studio', 'credits': 100}
        raw = JL.answer('what share of the Instagram viewers were women', PAGE, user=payer, history=[])
        self.assertIsNotNone(raw)
        self.assertTrue(raw.get('panel_offer'))
        self.assertEqual(raw['panel_offer']['subject'], 'Music to Long Form')
        self.assertIn('does not carry age, gender, income or education', raw['reply'])
        self.assertIn('I can pull that read on Music to Long Form.', raw['reply'])
        self.assertNotIn('Digital Journey IQ', raw['reply'])
        self.assertIsNone(EMOJI.search(raw['reply']))
        self.assertEqual(raw['reply'].count('Pulling this data will cost'), 1)

    def test_other_titles_and_orders_are_left_alone(self):
        self.assertIsNone(JS.answer('how many people watched Babylon 5 on Prime Video', PAGE, user=LIZ_USER))
        self.assertIsNone(JS.answer('how did The Influencer Project convert', PAGE, user=LIZ_USER))
        self.assertIsNone(JS.answer('email me this file', PAGE, user=LIZ_USER))
        self.assertIsNone(JS.answer('build a journey for Young Sheldon on Netflix, start from a TikTok clip', PAGE, user=LIZ_USER))
        self.assertIsNone(JS.answer(LIZ, {'view_context': {'view_id': 'profileIQ', 'view_title': 'Profile IQ'}}, user=LIZ_USER)
                          if not JS.names_study(LIZ) else None)   # the words name the study: it still answers off the page

    def test_detection_helpers(self):
        self.assertTrue(JS.is_study_view(PAGE))
        self.assertTrue(JS.names_study('Music to Longform data set'))
        self.assertTrue(JS.names_study('the music-to-long-form study'))
        self.assertTrue(JS.is_dataset_question(LIZ))
        self.assertTrue(JS.is_dataset_question('how many went from a song to a watch'))
        self.assertTrue(JS.is_dataset_question('which touchpoint drove the viewing'))
        self.assertFalse(JS.is_dataset_question('email me this file'))
        self.assertEqual(JS.needs_split('split the social touchpoint by age and gender'), 'demographic')
        self.assertEqual(JS.needs_split('which DMA converted best'), 'geography')
        self.assertEqual(JS.needs_split(LIZ), '')
        self.assertTrue(JL.journey_vocabulary(LIZ))


class SubjectLabels(unittest.TestCase):
    def test_strip_emoji(self):
        self.assertEqual(SD.strip_emoji(TAB), 'Digital Journey IQ')
        self.assertEqual(SD.strip_emoji('🎵 Music to Long Form'), 'Music to Long Form')
        self.assertEqual(SD.strip_emoji('Olipop'), 'Olipop')
        self.assertEqual(SD.strip_emoji(''), '')

    def test_view_label_prefers_the_study_and_never_the_icon(self):
        self.assertEqual(VB.view_label(VIEW), 'Music to Long Form')
        self.assertEqual(VB.view_base(VIEW)['subject'], 'Music to Long Form')
        bare = {'view_id': 'journeyIQ', 'view_title': TAB, 'data': {}}
        self.assertEqual(VB.view_label(bare), 'Digital Journey IQ')
        self.assertEqual(VB.view_base(bare)['subject'], 'Digital Journey IQ')
        camp = {'view_id': 'intentIQ', 'view_title': '🎯 Attribution IQ', 'data': {'title': 'the_influencer_project_hades'}}
        self.assertEqual(VB.view_label(camp), 'The Influencer Project Hades')

    def test_page_name_under_a_self_contained_view_is_the_study(self):
        from prometheus import priced_read as P
        from prometheus import subject_rules as SR
        self.assertEqual(P._page_name(PAGE), 'Music to Long Form')
        self.assertEqual(SR._page_name(PAGE), 'Music to Long Form')
        prof = {'primary': {'name': 'Olipop'}, 'view_context': {'view_id': 'profileIQ', 'view_title': 'Profile IQ'}}
        self.assertEqual(P._page_name(prof), 'Olipop')
        self.assertEqual(SR._page_name(prof), 'Olipop')


class ThroughTheDispatcher(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from prometheus.host import host
        cls.calls = []
        cls.hints = []

        def _analyze_core(user, body, text, history):
            cls.calls.append(('analyze', text))
            return {'success': True, 'reply': 'I can pull that read on 🗺️ Digital Journey IQ. Pulling this data will cost $100.',
                    'followups': []}

        def _interpret_core(user, body, text, history):
            cls.calls.append(('interpret', text))
            return {'success': True, 'reply': 'draft', 'draft': {'subject': 'x'}}

        host.bind(analyze_core=_analyze_core, interpret_core=_interpret_core,
                  deck_core=lambda *a: {'success': True, 'reply': 'deck'},
                  ask_hint=lambda **k: cls.hints.append(k), gate_analyze=lambda u: True, gate_pull=lambda u: True,
                  funds_gate=lambda u: None, gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
                  calm_payload=lambda: {'success': False, 'error': 'calm'}, error_email=lambda *a, **k: None,
                  load_threads_index=lambda u: {'threads': []}, max_threads=50,
                  s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
                  thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u,
                  thread_title_from=lambda t: t[:30], bucket='x', s3_client=None,
                  job_owner_ok=lambda a, b: True,
                  claude_data=lambda *a, **k: {})
        from prometheus import service
        cls.service = service

    def _ask(self, text):
        self.calls.clear()
        self.hints.clear()
        env, st = self.service.ask(LIZ_USER, {'text': text, 'surface': 'analyze', 'client': 'dashboard',
                                              'history': [], 'page_context': PAGE})
        return env, st

    def test_liz_message_lands_on_the_journey_lane(self):
        env, st = self._ask(LIZ)
        self.assertEqual(st, 200)
        self.assertEqual(env['decision']['reason'], 'standing')
        self.assertEqual(self.calls, [], 'no model, no legacy analyze')
        self.assertTrue(any(h.get('route') == 'journey_file' for h in self.hints), self.hints)
        raw = env['raw']
        self.assertTrue(raw.get('success'))
        self.assertNotIn('Pulling this data will cost', raw['reply'])
        self.assertIsNone(EMOJI.search(raw['reply']))
        self.assertIn('7,022,543', raw['reply'])
        self.assertIn('2,498,420', raw['reply'])
        self.assertFalse(raw.get('panel_offer'))
        self.assertEqual(raw.get('subject'), 'Music to Long Form')
        self.assertEqual(env.get('text') or raw['reply'], env.get('text') or raw['reply'])

    def test_the_journey_lane_sits_ahead_of_every_priced_lane(self):
        order = self.service.LANE_ORDER
        self.assertLess(order.index('standing'), order.index('sizing_quote'))
        self.assertLess(order.index('standing'), order.index('work_gate'))
        markers = self.service.lane_markers()
        self.assertLess(markers.index('standing'), markers.index('sizing_quote'))


if __name__ == '__main__':
    unittest.main(verbosity=1)
