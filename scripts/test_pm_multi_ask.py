#!/usr/bin/env python3
"""Two asks in one message (Jenna 2026-10-30): each question routes on its
own; priced parts are quoted together in one itemized offer, metered and
free parts answer alongside; Approve charges and runs each part. Hermetic:
run_one is a stub."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import multi_ask as M  # noqa: E402

REBA_Q = 'How big is the Reba audience'
GG_Q = 'what are the top five QSRs for Gilmore Girls viewers?'
TEXT = f"{REBA_Q}, and {GG_Q}"


def _offer(subject, label, lead):
    return {'success': True, 'action': 'answer',
            'reply': f"{lead} Pulling this data will cost {label}.",
            'followups': ['Approve', 'Cancel'],
            'panel_offer': {'question': '', 'subject': subject, 'kind': 'report', 'frame': '', 'price_label': label}}


class Split(unittest.TestCase):
    def test_one_sentence_two_questions(self):
        parts = M.split(TEXT)
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].lower().startswith('how big is the reba'))
        self.assertTrue(parts[1].lower().startswith('what are the top five'))
        self.assertTrue(parts[0].endswith('?') and parts[1].endswith('?'))

    def test_two_sentences(self):
        parts = M.split('How many people watched Mutiny on Starz? How many of them also watch Prime Video?')
        self.assertEqual(len(parts), 2)

    def test_one_question_is_one_question(self):
        self.assertEqual(M.split('How big is the Reba audience?'), [])
        self.assertEqual(M.split('Compare Reba and Gilmore Girls and tell me which skews older'), [])
        self.assertEqual(M.split('what is the share of men and women?'), [])


class Combine(unittest.TestCase):
    def test_two_priced_parts_one_itemized_quote(self):
        raws = [_offer('Reba McEntire', '$100', 'I can size Reba McEntire: US people in the window.'),
                _offer('Gilmore Girls viewers', '$100', 'I can rank the QSRs Gilmore Girls viewers use most.')]
        out = M.combined_offer(TEXT, M.split(TEXT), raws)
        self.assertEqual(out['followups'], ['Approve', 'Cancel'])
        self.assertIn('Two priced reads here, quoted together and run together:', out['reply'])
        self.assertIn('1. I can size Reba McEntire: US people in the window. $100.', out['reply'])
        self.assertIn('2. I can rank the QSRs Gilmore Girls viewers use most. $100.', out['reply'])
        self.assertIn('Approve runs both for $200 in all; Cancel runs none of them.', out['reply'])
        po = out['panel_offer']
        self.assertEqual(po['kind'], 'multi')
        self.assertEqual(po['price_label'], '$200')
        self.assertEqual([p['subject'] for p in po['parts']], ['Reba McEntire', 'Gilmore Girls viewers'])
        self.assertEqual(po['parts'][0]['kind'], 'report')
        self.assertNotIn('Pulling this data will cost', out['reply'].split('Two priced')[0])

    def test_metered_answer_rides_beside_the_quote(self):
        raws = [_offer('Reba McEntire', '$100', 'I can size Reba McEntire: US people in the window.'),
                {'success': True, 'action': 'answer', 'reply': 'Top QSRs: Starbucks, Chick-fil-A, Dunkin.', 'followups': ['CSV']}]
        out = M.combined_offer(TEXT, M.split(TEXT), raws)
        self.assertIn('Top QSRs: Starbucks, Chick-fil-A, Dunkin.', out['reply'])
        self.assertIn('priced read', out['reply'])
        self.assertEqual(len(out['panel_offer']['parts']), 1)
        self.assertEqual(out['panel_offer']['price_label'], '$100')

    def test_two_answers_two_jobs(self):
        raws = [{'success': True, 'action': 'answer', 'reply': 'On it.', 'read_job_id': 'j1', 'subject': 'Reba McEntire'},
                {'success': True, 'action': 'answer', 'reply': 'On it.', 'read_job_id': 'j2'}]
        out = M.combine_answers(M.split(TEXT), raws)
        self.assertEqual(out['read_job_id'], 'j1')
        self.assertEqual(out['read_job_ids'], ['j1', 'j2'])
        self.assertEqual(out['subject'], 'Reba McEntire')
        self.assertIn('1) How big is the Reba audience?', out['reply'])
        self.assertIn('2) what are the top five QSRs for Gilmore Girls viewers?', out['reply'])

    def test_confirm_parts(self):
        pc = {'kind': 'multi', 'subject': 'Reba McEntire',
              'parts': [{'text': REBA_Q + '?', 'subject': 'Reba McEntire', 'kind': 'sizing'},
                        {'text': GG_Q, 'subject': 'Gilmore Girls viewers', 'kind': 'report', 'years': 2}]}
        parts = M.confirm_parts(pc)
        self.assertEqual([q for q, _ in parts], [REBA_Q + '?', GG_Q])
        self.assertEqual(parts[0][1], {'subject': 'Reba McEntire', 'kind': 'sizing', 'dimension': ''})
        self.assertEqual(parts[1][1]['years'], 2)
        self.assertEqual(M.confirm_parts({'kind': 'report', 'subject': 'X'}), [])


class Handle(unittest.TestCase):
    def test_fresh_message_quotes_both_then_confirm_runs_both(self):
        calls = []

        def run_one(q, pc, prior=None):
            calls.append((q, pc))
            if 'gilmore' in q.lower():
                assert prior and 'reba' in prior[0].lower(), 'the second part reads with the first behind it'
            if pc:
                return ({'raw': {'success': True, 'action': 'answer', 'reply': f'Done: {q}', 'read_job_id': 'job-' + str(len(calls))}}, 200)
            if 'reba' in q.lower():
                return ({'raw': _offer('Reba McEntire', '$100', 'I can size Reba McEntire.')}, 200)
            return ({'raw': _offer('Gilmore Girls viewers', '$100', 'I can rank the QSRs.')}, 200)

        out = M.handle(TEXT, {'text': TEXT}, run_one)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(pc is None for _, pc in calls))
        self.assertEqual(out['panel_offer']['kind'], 'multi')
        # the chip comes back with the parts
        pc = {'subject': out['panel_offer']['subject'], 'kind': 'multi', 'parts': out['panel_offer']['parts']}
        calls.clear()
        out2 = M.handle(TEXT, {'text': TEXT, 'panel_confirm': pc}, run_one)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1]['subject'], 'Reba McEntire')
        self.assertEqual(calls[1][1]['subject'], 'Gilmore Girls viewers')
        self.assertEqual(out2['read_job_ids'], ['job-1', 'job-2'])
        self.assertTrue(out2['multi_ask']['confirmed'])

    def test_lane_steps_aside(self):
        def run_one(q, pc, prior=None):
            raise AssertionError('must not run')
        self.assertIsNone(M.handle('How big is the Reba audience?', {'text': 'x'}, run_one))
        self.assertIsNone(M.handle(TEXT, {'text': TEXT, '_multi_part': True}, run_one))
        self.assertIsNone(M.handle(TEXT, {'text': TEXT, 'panel_confirm': {'kind': 'report', 'subject': 'Reba'}}, run_one))

    def test_wiring(self):
        svc = open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8').read()
        self.assertLess(svc.index('# lane: multi_ask'), svc.index('# lane: where_watched'))
        self.assertIn("_multi.handle(text, body, _multi_run_one)", svc)
        pend = open(os.path.join(ROOT, 'prometheus', 'pending_offers.py'), encoding='utf-8').read()
        self.assertIn("body['parts'] =", pend)
        html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()
        self.assertIn('data.read_job_ids', html)
        self.assertIn('parts: Array.isArray(_po.parts)', html)


if __name__ == '__main__':
    unittest.main(verbosity=1)
