#!/usr/bin/env python3
"""The wizard yields to a clearly unrelated ask and takes a plain
correction; a delivered valuation is what the next words about it mean
(Jenna 2026-11-20, decisions on the rebuild report and Samantha Kolton's
thread; Prometheus rebuild stage 2 follow-up).

Hermetic: no model, no S3, no mail, no chat module, no app. The lanes run
through service.ask on the stub host (scripts/pm_corpus/replay_corpus.py,
service mode) with invented names.

1. wizard_interrupt.unrelated: while a Brand Partnership step waits, a
   message the Understanding reads with confidence as the reader's own
   order, a feature, a file to send, an insight or a price stands the
   wizard aside; a chip, an accept, a name, dates or a slot sentence
   never does.
2. wizard_interrupt.correction: at the confirm card and later steps,
   "the campaign dates are ...", "I gave the wrong dates ...", "the
   correct dates are ..." replace the campaign dates and the card comes
   back; a bare range at the card still sets the attribution window; at
   the dates step the step itself takes the dates.
3. valuation_referent: the delivered valuation's subject, pairing, window
   and total are read off the thread; the four live misses read as a
   restate, a deck, a deck (the four parts) and a re-run on new dates; a
   deck of another pairing is not it.
4. Through the door: the four misses land on the right executor (never
   "Which campaign", never Attribution IQ, never a window as a subject,
   never a promise with nothing running, never a generic deck); the
   interrupt answers the status ask with the way back; the correction
   shows the card with the new dates; the kill switches restore the old
   paths.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ['PM_MAIL_OFF'] = '1'

from pm_corpus import replay_corpus as RC  # noqa: E402
RC.install_isolation_light()
from prometheus import understanding as U  # noqa: E402
from prometheus import wizard_interrupt as WI  # noqa: E402
from prometheus import valuation_referent as VR  # noqa: E402
from prometheus import bpiq_wizard as W  # noqa: E402

TALENT, BRAND = 'Example Talent', 'Northwind Beauty'
PAIR = f'{TALENT} x {BRAND}'

# a wizard walked to the dates step (the thread as the dashboard keeps it)
WIZ_TO_DATES = [
    {'role': 'user', 'text': 'Value a brand partnership'},
    {'role': 'agent', 'text': 'Who is the property or talent?', 'meta': {'options': ['Cancel']}},
    {'role': 'user', 'text': TALENT},
    {'role': 'agent', 'text': 'What campaign should I value?', 'meta': {'options': ['Cancel']}},
    {'role': 'user', 'text': BRAND},
    {'role': 'agent', 'text': 'What are the campaign dates?', 'meta': {'options': ['Cancel']}},
]
# the confirm card, read off a pasted history with the wrong dates
WIZ_AT_CARD = WIZ_TO_DATES + [
    {'role': 'user', 'text': 'the Feb 4 to 6, 2025 activation and the Sep 2026 relaunch'},
    {'role': 'agent', 'text': f'{TALENT}, {BRAND}: I read the campaign as February 4 to 6, 2025. The standard window is the campaign '
                              'dates plus 30 days after it ends. Attribution window: February 4 to March 8, 2025.',
     'meta': {'options': ['Accept', 'Different window', 'Cancel']}},
]
# a delivered valuation on August 27 to September 8, 2026, then a full-period read
DELIVERED = WIZ_TO_DATES + [
    {'role': 'user', 'text': '8/27/26-9/8/26'},
    {'role': 'agent', 'text': f'{TALENT}, {BRAND}: I read the campaign as August 27 to September 8, 2026. The standard window is the campaign '
                              'dates plus 30 days after it ends. Attribution window: August 27 to October 8, 2026.', 'meta': {'options': ['Accept']}},
    {'role': 'user', 'text': 'Accept'},
    {'role': 'agent', 'text': f"Here's what I'll run:\n{TALENT}\n{BRAND}\nCampaign dates: August 27 to September 8, 2026\nAttribution window: "
                              "August 27 to October 8, 2026\nControl group: yes\nI'll find the assets\nIncluded with your plan.", 'meta': {'options': ['Accept', 'Cancel']}},
    {'role': 'user', 'text': 'Accept'},
    {'role': 'agent', 'text': f'Your Brand Partnership valuation is ready: {PAIR}, total brand value $1,234,567.89. The deck is below, and the link '
                              'is good for 7 days.\nIncluded with your plan.', 'meta': {'bpiq_job_id': 'ab12cd34ef56', 'kind': 'bpiq', 'subject': PAIR}},
    {'role': 'user', 'text': 'Can you resend for the full period?'},
    {'role': 'agent', 'text': f'Across the full partnership period, {PAIR} carries a total brand value of $6,543,210.00.\n\nWindow: Sep 6 2023 to Sep 8 2026.',
     'meta': {'kind': 'read', 'read_job_id': '3f3f3f3f3f3f'}},
]
MISS_A = 'I want the period of 8/27/26 to 9/8/26 - i had given the wrong dates for that campaign'
MISS_B = 'Rebuild the deck for the Aug 27 to Sep 8 window'
MISS_C = 'can you rerun this and break the campaign into the 4 parts?'
MISS_D = 'Can you just send a refreshed deck for the brand valuation for the correct dates of 8/27/26 to 9/6/26'


def _clear_flags():
    for k in list(os.environ):
        if k.startswith('PM_U_OFF_') or k == 'PM_UNDERSTANDING':
            os.environ.pop(k, None)


def _draft(step, **kw):
    d = W.empty_draft()
    d.update({'talent': TALENT, 'brand_partner': BRAND, 'brand_named': True, 'campaign': BRAND, 'step': step})
    d.update(kw)
    return d


def _body(text, history):
    return U.attach({'text': text, 'history': history}, U.features(text, None, history, {}))


class Unrelated(unittest.TestCase):
    def setUp(self):
        _clear_flags()

    def test_own_order_status_stands_the_wizard_aside(self):
        d = _draft(W.STEP_DATES)
        self.assertTrue(WI.unrelated(_body('where is my Nurses file, it has not arrived', WIZ_TO_DATES), 'where is my Nurses file, it has not arrived', d))
        self.assertTrue(WI.unrelated(_body('email me the Harbor Lights breakdown', WIZ_TO_DATES), 'email me the Harbor Lights breakdown', d))
        self.assertTrue(WI.unrelated(_body('where is the avid fan option', WIZ_TO_DATES), 'where is the avid fan option', d))

    def test_step_answers_never_do(self):
        d = _draft(W.STEP_DATES)
        for t in ('8/27/26-9/8/26', 'Accept', 'Cancel', 'the campaign dates are sep 1 - 8, 2026', f'{BRAND} is the brand', 'Different window'):
            self.assertFalse(WI.unrelated(_body(t, WIZ_TO_DATES), t, d), t)

    def test_a_low_confidence_reading_never_interrupts(self):
        d = _draft(W.STEP_DATES)
        t = 'who skews older, Example Brand or Northwind Brand?'
        self.assertLess(U.on_body(_body(t, WIZ_TO_DATES)).confidence, WI.MIN_CONF)
        self.assertFalse(WI.unrelated(_body(t, WIZ_TO_DATES), t, d))

    def test_kill_switch(self):
        d = _draft(W.STEP_DATES)
        os.environ['PM_U_OFF_WIZARD_INTERRUPT'] = '1'
        try:
            self.assertFalse(WI.enabled())
            self.assertFalse(WI.unrelated(_body('where is my Nurses file, it has not arrived', WIZ_TO_DATES), 'where is my Nurses file, it has not arrived', d))
            self.assertIsNone(WI.correction('the campaign dates are sep 1 - 8, 2026', _draft(W.STEP_ATTR, campaign_start='2025-02-04', campaign_end='2025-02-06')))
        finally:
            os.environ.pop('PM_U_OFF_WIZARD_INTERRUPT', None)


class Correction(unittest.TestCase):
    def setUp(self):
        _clear_flags()
        self.card = _draft(W.STEP_ATTR, campaign_start='2025-02-04', campaign_end='2025-02-06', attr_start='2025-02-04', attr_end='2025-03-08')

    def test_named_dates_at_the_card_replace_the_campaign_dates(self):
        for t in ('the campaign dates are sep 1 - 8, 2026', 'I gave the wrong dates, it ran 8/27/26 to 9/8/26', 'the correct dates are Aug 27 to Sep 8, 2026',
                  'actually the period of 8/27/26 to 9/8/26', 'the dates should be September 1 to September 8, 2026'):
            c = WI.correction(t, self.card)
            self.assertIsNotNone(c, t)
            self.assertTrue(c['campaign_start'].startswith('2026-'), c)

    def test_a_bare_range_at_the_card_is_left_to_the_card(self):
        self.assertIsNone(WI.correction('9/1/26 to 9/8/26', self.card))
        self.assertIsNone(WI.correction('Accept', self.card))
        self.assertIsNone(WI.correction('did the campaign dates change to sep 1 - 8, 2026?', self.card))

    def test_the_dates_step_keeps_its_own_dates(self):
        self.assertIsNone(WI.correction('the campaign dates are sep 1 - 8, 2026', _draft(W.STEP_DATES)))

    def test_the_same_dates_again_are_not_a_correction(self):
        self.assertIsNone(WI.correction('the campaign dates are Feb 4 to 6, 2025', self.card))

    def test_later_steps_take_the_correction_too(self):
        d = _draft(W.STEP_CONTROL, campaign_start='2025-02-04', campaign_end='2025-02-06', attr_start='2025-02-04', attr_end='2025-03-08', attr_ok=True)
        c = WI.correction('i had given the wrong dates for that campaign, it is 8/27/26 to 9/8/26', d)
        self.assertEqual((c['campaign_start'], c['campaign_end']), ('2026-08-27', '2026-09-08'))
        d2 = WI.apply_correction(d, c, 'x')
        self.assertEqual(d2['step'], W.STEP_ATTR)
        self.assertFalse(d2['attr_ok'])
        self.assertEqual(d2['attr_start'], '2026-08-27')
        self.assertGreater(d2['attr_end'], '2026-09-08')
        self.assertTrue(d2['corrected_dates'])


class Referent(unittest.TestCase):
    def setUp(self):
        _clear_flags()

    def test_delivered_reads_the_thread(self):
        h = VR.delivered(DELIVERED)
        self.assertEqual((h['subject'], h['talent'], h['brand']), (PAIR, TALENT, BRAND))
        self.assertEqual((h['start'], h['end']), ('2026-08-27', '2026-09-08'))
        self.assertEqual(h['total'], '$1,234,567.89')
        self.assertEqual(h['job_id'], 'ab12cd34ef56')
        self.assertIsNone(VR.delivered(WIZ_TO_DATES))

    def test_the_four_misses_read_as_the_valuation(self):
        a = VR.read(MISS_A, DELIVERED)
        self.assertEqual((a['kind'], a['window'], a['same_window']), ('rerun', ('2026-08-27', '2026-09-08'), True))
        b = VR.read(MISS_B, DELIVERED)
        self.assertEqual((b['kind'], b['same_window']), ('deck', True))
        c = VR.read(MISS_C, DELIVERED)
        self.assertEqual((c['kind'], c['window'], c['same_window']), ('deck', None, True))
        d = VR.read(MISS_D, DELIVERED)
        self.assertEqual((d['kind'], d['window'], d['same_window']), ('deck', ('2026-08-27', '2026-09-06'), False))

    def test_another_pairing_or_no_valuation_is_not_it(self):
        self.assertIsNone(VR.read('rebuild the deck for the Gunna x Nike valuation', DELIVERED))
        self.assertIsNone(VR.read(MISS_B, WIZ_TO_DATES))
        self.assertIsNone(VR.read('how many people watched Harbor Lights', DELIVERED))

    def test_a_later_read_on_something_else_takes_the_bare_deck_words(self):
        # a read on another title landed after the valuation: "rebuild the deck" names nothing, so it is not the
        # valuation's; the pairing, the word valuation or a window still point at it
        later = DELIVERED + [{'role': 'user', 'text': 'how many people watched Harbor Lights'},
                             {'role': 'agent', 'text': 'On Harbor Lights: 4,567,891 US people watched in the trailing year.', 'meta': {'kind': 'read', 'read_job_id': 'aaaa11112222'}}]
        self.assertIsNone(VR.read('rebuild the deck', later))
        self.assertIsNotNone(VR.read('rebuild the valuation deck', later))
        self.assertIsNotNone(VR.read(f'rebuild the {TALENT} x {BRAND} deck', later))
        self.assertIsNotNone(VR.read(MISS_B, later))
        self.assertIsNotNone(VR.read('rebuild the deck', DELIVERED))     # the last turn is still on the pairing

    def test_the_understanding_carries_it(self):
        u = U.features(MISS_A, None, DELIVERED, {})
        self.assertEqual((u.intent, u.referent, u.subjects[0].as_written, u.window), ('valuation', 'last_valuation', PAIR, '2026-08-27 to 2026-09-08'))
        u2 = U.features(MISS_C, None, DELIVERED, {}, is_order=True)
        self.assertEqual((u2.intent, u2.referent), ('deck', 'last_valuation'))     # never the build order the gate read
        u3 = U.features(MISS_D, None, DELIVERED, {})
        self.assertEqual((u3.intent, u3.window), ('deck', '2026-08-27 to 2026-09-06'))
        self.assertIn('valuation_ref:deck', u3.evidence)


class ThroughTheDoor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _clear_flags()
        cls.stub = RC._Stub()
        cls.stub.bind()

    def _ask(self, text, history, surface='analyze', view=''):
        return RC._service_once(self.stub, {'id': 'x', 'question': text, 'view': view, 'surface': surface, 'subject': None,
                                            'history': list(history), 'reading': None})

    def test_a_date_correction_after_the_valuation_names_it(self):
        live = self._ask(MISS_A, DELIVERED)
        self.assertEqual(live['route'], 'valuation_referent', live['reply'][:200])
        self.assertIn(PAIR, live['reply'])
        self.assertIn('$1,234,567.89', live['reply'])
        self.assertNotIn('Which campaign', live['reply'])
        self.assertNotIn('Attribution IQ', live['reply'])
        self.assertIn('flip=1', live['u'])

    def test_rebuild_the_deck_for_the_window_is_the_valuation_deck(self):
        live = self._ask(MISS_B, DELIVERED, surface='deck', view='brandPartnershipIQ')
        self.assertEqual(live['route'], 'valuation_deck', live['reply'][:200])
        self.assertEqual(live['core'], 'deck')
        body = self.stub.cores[-1][1]
        self.assertEqual(body.get('bind_subject'), PAIR)          # the valuation picked, never the window as a subject
        self.assertTrue(body.get('confirm_open_screen'))
        self.assertNotIn('do not have a profile', live['reply'])

    def test_rerun_and_the_four_parts_is_the_deck_not_a_build(self):
        live = self._ask(MISS_C, DELIVERED)
        self.assertEqual(live['route'], 'valuation_deck', live['reply'][:200])
        self.assertNotIn('Confirmed your task is in progress', live['reply'])
        self.assertNotEqual(live['core'], 'interpret')

    def test_a_refreshed_deck_on_new_dates_opens_the_valuation_on_them(self):
        live = self._ask(MISS_D, DELIVERED, surface='deck', view='brandPartnershipIQ')
        self.assertEqual(live['route'], 'valuation_referent', live['reply'][:200])
        self.assertEqual(live['outcome'], 'rerun')
        self.assertIn('August 27 to September 6, 2026', live['reply'])
        self.assertIn('I read the campaign as', live['reply'])
        self.assertEqual(live['core'], '')                           # no generic deck started

    def test_the_interrupt_answers_the_status_ask_with_the_way_back(self):
        live = self._ask('where is my Nurses file, it has not arrived', WIZ_TO_DATES)
        self.assertIn(live['route'], ('status_lookup', 'deliverable_lookup'), live['reply'][:200])
        self.assertNotIn('campaign dates', live['reply'].lower())
        self.assertIn('flip=1', live['u'])

    def test_the_correction_at_the_card_shows_the_card_with_the_new_dates(self):
        live = self._ask('the campaign dates are sep 1 - 8, 2026', WIZ_AT_CARD)
        self.assertEqual(live['route'], 'bpiq_wizard', live['reply'][:200])
        self.assertIn('September 1 to September 8, 2026', live['reply'])
        self.assertIn('Attribution window: September 1 to October 8, 2026', live['reply'])

    def test_dates_at_the_dates_step_stay_with_the_step(self):
        live = self._ask('8/27/26-9/8/26', WIZ_TO_DATES)
        self.assertEqual(live['route'], 'bpiq_wizard')
        self.assertIn('I read the campaign as August 27 to September 8, 2026', live['reply'])

    def test_kill_switches_restore_the_old_paths(self):
        os.environ['PM_U_OFF_WIZARD_INTERRUPT'] = '1'
        try:
            live = self._ask('the campaign dates are sep 1 - 8, 2026', WIZ_AT_CARD)
            self.assertEqual(live['route'], 'bpiq_wizard')
            self.assertNotIn('I read the campaign as September 1', live['reply'])     # the old path: the dates set the attribution window
        finally:
            os.environ.pop('PM_U_OFF_WIZARD_INTERRUPT', None)
        os.environ['PM_U_OFF_VALUATION_REFERENT'] = '1'
        try:
            live = self._ask(MISS_B, DELIVERED, surface='deck', view='brandPartnershipIQ')
            self.assertNotEqual(live['route'], 'valuation_deck')
        finally:
            os.environ.pop('PM_U_OFF_VALUATION_REFERENT', None)


if __name__ == '__main__':
    unittest.main(verbosity=1)
