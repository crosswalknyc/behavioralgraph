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


class NumberProvenance(unittest.TestCase):
    HIST = [{'role': 'user', 'text': 'how are the touchpoints tagged?'},
            {'role': 'agent', 'text': "On The Influencer Project: Across the 12,004,203 potential ticket buyers, the report tags every asset to one of three phases.",
             'meta': {'read_job_id': 'r1', 'subject': 'The Influencer Project',
                      'figures': [{'value': 12004203, 'kind': 'count', 'label': 'potential ticket buyers (US people exposed to the campaign)', 'source': 'campaign_file'}]}}]

    def test_found(self):
        from prometheus import number_provenance as NP
        r = NP.answer('where does the 12,004,203 potential ticket buyers number come from?', self.HIST)
        self.assertTrue(r and r['number_provenance']['found'])
        self.assertIn('potential ticket buyers (US people exposed to the campaign)', r['reply'])
        self.assertIn("read off the campaign's own counts", r['reply'])

    def test_not_found_and_not_a_question(self):
        from prometheus import number_provenance as NP
        r = NP.answer('where does 9,999,999 come from', self.HIST)
        self.assertTrue(r and not r['number_provenance']['found'])
        self.assertIn('12,004,203', r['reply'])
        self.assertIsNone(NP.answer('Approve', self.HIST))
        self.assertIsNone(NP.answer('how many people watched Mutiny in 2025?', self.HIST))

    def test_read_turns_keep_their_ledger(self):
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'land.py'), encoding='utf-8').read()
        self.assertIn("_meta['figures'] = [", src)
        svc = open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8').read()
        self.assertLess(svc.index('# lane: number_provenance'), svc.index('_refs.answer_merge(history, text)'))


class SizingLine(unittest.TestCase):
    def test_one_number_one_definition(self):
        from prometheus import sizing_lane as SZ
        line = SZ.profile_line({'name': 'Chipotle', 'sample': 2361167, 'us': 77895196,
                                'window': {'start': '2025-01-01', 'end': '2025-12-31'}, 'avid_us': 21042337})
        self.assertIn('77,895,196 US people with at least one digital touchpoint with Chipotle', line)
        self.assertIn('The Avid tier, the most engaged of them, is 21,042,337.', line)
        self.assertIn('measured count behind that projection is 2,361,167 people inside the sample', line)
        self.assertNotIn('audience range', line)


class TypoVerbsAndChangedName(unittest.TestCase):
    def test_typo_verbs(self):
        from prometheus import subject_rules as SR
        self.assertEqual(SR.explicit_build_subject('runa profile on NCIS'), 'NCIS')
        self.assertEqual(SR.explicit_build_subject('buid a profile on Nike'), 'Nike')
        self.assertEqual(SR.explicit_build_subject('run aprofile on ncis: new york'), 'ncis: new york')
        self.assertEqual(SR.explicit_build_subject('run the numbers on Nike'), '')

    def test_changed_name_gate_wired(self):
        src = open(os.path.join(ROOT, 'app.py'), encoding='utf-8').read()
        self.assertIn('Changed-name gate', src)
        self.assertIn('_typed_swap = True', src)


class DeckPageNamed(unittest.TestCase):
    def test_deck_core_skips_the_yes_no_when_the_words_name_the_page(self):
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        self.assertIn("and not body.get('confirm_open_screen') and not _page_named:", src)


class ServerTurnsLand(unittest.TestCase):
    def test_widget_pulls_server_turns(self):
        html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()
        self.assertIn('function _pmServerTurnsPull()', html)
        self.assertIn('setInterval(_pmServerTurnsPull, 20000)', html)


class SubiqContext(unittest.TestCase):
    def test_cache_and_extra_budget(self):
        import prometheus_analysis as pma
        self.assertTrue(hasattr(pma, '_xmod_subiq_parsed'))
        self.assertGreaterEqual(pma.XMOD_SUBIQ_EXTRA_S, 1.0)


if __name__ == '__main__':
    unittest.main(verbosity=1)
