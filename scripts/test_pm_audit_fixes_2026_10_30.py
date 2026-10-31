#!/usr/bin/env python3
"""The 2026-10-30 audit fixes (Jenna: "fix all of these"). Hermetic.

1  a promise in prose followed by a bare yes runs the promised order
2  a conversion ask goes straight to the deck surface
3  writing / help / engineering asks are answered, never built or priced
4  a follow-up that points at the last read is metered, never re-quoted;
   a different named audience in the same thread is a new first ask
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import service as S  # noqa: E402
from prometheus import deck_sources as D  # noqa: E402
from prometheus import help_lane as H  # noqa: E402
from prometheus import priced_read as P  # noqa: E402


class PromiseInProse(unittest.TestCase):
    HIST = [{'role': 'user', 'text': "You sent me a beautiful Mark Rober deck, but it's a pdf. Can you build a PPT please?"},
            {'role': 'agent', 'text': 'Yes. Same deck, editable PowerPoint, same slides and same numbers as the PDF you already have. '
                                      'It lands here in the thread with its link and goes to your inbox with the file attached.',
             'meta': {'subject': 'Mark Rober'}}]

    def test_yes_after_a_deck_promise_orders_the_deck(self):
        order = S._prose_promise_order(self.HIST[-1], self.HIST)
        self.assertTrue(order.startswith('Build the Mark Rober deck as a PowerPoint'))
        self.assertTrue(D.is_conversion_ask(order))
        self.assertTrue(S._ACTION_CHIP_RX.match(order))

    def test_yes_after_a_read_promise_reruns_the_ask(self):
        hist = [{'role': 'user', 'text': 'can you size the Chipotle audience?'},
                {'role': 'agent', 'text': 'I can size Chipotle: US people in the window. Want me to run it?'}]
        self.assertEqual(S._prose_promise_order(hist[-1], hist), 'can you size the Chipotle audience?')

    def test_no_promise_no_order(self):
        hist = [{'role': 'user', 'text': 'top QSRs?'}, {'role': 'agent', 'text': "Raising Cane's leads at 14.6%."}]
        self.assertEqual(S._prose_promise_order(hist[-1], hist), '')
        chips = {'role': 'agent', 'text': 'I can build it.', 'meta': {'options': [{'label': 'Build a MrBeast profile'}]}}
        self.assertEqual(S._prose_promise_order(chips, hist + [chips]), '')   # the chips own the yes

    def test_bare_reply_runs_it(self):
        rewrite, payload = S._bare_reply({'text': 'Yes!!'}, 'Yes!!', self.HIST, True, uname='debip')
        self.assertIsNone(payload)
        self.assertTrue(str(rewrite).startswith('Build the Mark Rober deck'))


class DeckConversion(unittest.TestCase):
    def test_conversion_phrases(self):
        self.assertTrue(D.is_conversion_ask("You sent me a beautiful Mark Rober deck, but it's a pdf. Can you build a PPT please?"))
        self.assertTrue(D.is_conversion_ask('can you recreate the uploaded deck into a PPT please? exactly the same'))
        self.assertFalse(D.is_conversion_ask('build a deck on the demographics of wicked tuna viewers'))

    def test_lane_wired_before_referents(self):
        src = open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8').read()
        self.assertLess(src.index('# lane: deck_conversion'), src.index('_refs.answer_merge(history, text)'))
        self.assertIn("host.deck_core(user, _deck_body(body, text, history, ctx, tid))\n                raw, status = _unpack(resp)", src)


class HelpLane(unittest.TestCase):
    def test_detection(self):
        yes = ["I am speaking at TVOT a Digital Media, AI, Media Ad Conference. I need 3 killer bullets on what the audience will learn based on the Crosswalk Dashboard and Prometheus.",
               "For the electronics vertical, build a classification that assigns every relevant URL to one page type: search_engine_query, retailer_search, category_page, product_page",
               "help me explain the index to my client", "write me a two line blurb on the Reba audience for the upfront"]
        no = ["run a profile on mountain dew", "build a deck on the demographics of wicked tuna viewers", "how many people watched Mutiny on Starz",
              "Value a brand partnership", "what are the top five QSRs for Gilmore Girls viewers?", "Create a digital journey",
              "can you build a profile for the toys category that is for the brand Lego?"]
        for t in yes:
            self.assertTrue(H.is_help_ask(t), t)
        for t in no:
            self.assertFalse(H.is_help_ask(t), t)

    def test_answer_uses_the_model_once(self):
        calls = []

        def reason(sys_, usr):
            calls.append((sys_, usr))
            return 'Three bullets. One: you see the whole digital life. Two: the read is first party. Three: it answers in plain English.'
        out = H.answer('I need 3 bullets for my talk', [{'role': 'user', 'text': 'hi'}], 'Reba McEntire', reason=reason)
        self.assertEqual(len(calls), 1)
        self.assertIn('THE ASK', calls[0][1])
        self.assertIn('Three bullets', out['reply'])

    def test_lane_wired_ahead_of_the_build_lanes(self):
        src = open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8').read()
        self.assertLess(src.index('# lane: help_writing'), src.index('# lane: dataset_build'))
        self.assertLess(src.index('# lane: help_writing'), src.index('# lane: surface_decision'))


class FollowUpMetered(unittest.TestCase):
    HIST = [{'role': 'user', 'text': 'What did the BookTok exposed viewers watch on Prime Video in the 30 days after the film?'},
            {'role': 'agent', 'text': 'On The Love Hypothesis - The Love Hypothesis Prime Video viewers, BookTok exposed: 87.3% of the BookTok exposed cohort '
                                      '(2,522,187 viewers) started another Prime Video title within 30 days of the film.',
             'meta': {'read_job_id': 'j1', 'subject': 'The Love Hypothesis'}}]

    def _gate(self, text, subject, kind='report'):
        return P.offer_or_charge(text=text, subject=subject, kind=kind, panel_confirm=None, username='u', user={'username': 'u'}, history=self.HIST)

    def test_referents_meter(self):
        for text, subj in [('is the film you started with The Love Hypotheis, if so I should expect Off Campus to be on the list?', 'The Love Hypotheis'),
                           ('what about gender age and international viewers', 'What About Gender Age and International'),
                           ('how many of them also watch Peacock?', 'Peacock'), ('do they skew male?', 'male'),
                           ('where does the 2,522,187 come from', 'the 2,522,187')]:
            state, pl = self._gate(text, subj)
            self.assertEqual(state, 'charged', text)
            self.assertTrue(pl.get('metered'), text)
            self.assertEqual(pl.get('subject'), 'The Love Hypothesis', text)

    def test_new_audience_is_a_new_quote(self):
        for text, subj, kind in [('how big is the Reba McEntire audience?', 'Reba McEntire', 'sizing'),
                                 ('what are the top QSRs for Gilmore Girls viewers', 'Gilmore Girls viewers', 'report'),
                                 ('what about Peacock subscribers', 'Peacock subscribers', 'report')]:
            state, pl = self._gate(text, subj, kind)
            self.assertEqual(state, 'offer', text)
            self.assertIn('panel_offer', pl, text)


if __name__ == '__main__':
    unittest.main(verbosity=1)
