#!/usr/bin/env python3
"""The journey confirm card reads 'Included with your plan.' for an
unlimited or staff seat (Jenna 2026-11-08: priced_read.included_with_plan is
the one check every priced surface asks). On 2026-11-10 the card printed
"It prices at $500. Run it?" to Jenna's unlimited seat.

Covered, hermetically (a fake host carrying its own users document, no
model, no S3, the legacy chat module bound to it):
  1. the journey card: an unlimited seat reads the included line and no
     price; a paying seat still reads the price; both keep 'Run it?' and
     the run starts on confirm (the chips stay Run the journey / Cancel)
  2. the after-watch (media week) card, same rule
  3. the run turn's ack carries the included line for an included seat
     (source check: the one helper, the one gate)

    REGRESSION_TEST_MODE=1 python3 scripts/test_pm_journey_card_included.py
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


class _NoAppModule:
    def find_spec(self, name, path=None, target=None):
        if name == 'app':
            raise ImportError('app is not importable inside this hermetic suite')
        return None


sys.meta_path.insert(0, _NoAppModule())

USERS = {
    'unlim': {'username': 'unlim', 'email': 'isa@studio.com', 'company': 'Studio TV', 'credits': 40, 'unlimited': True, 'role': 'user'},
    'payer': {'username': 'payer', 'email': 'payer@agency.com', 'company': 'Agency', 'credits': 100, 'paying_customer': True, 'role': 'user'},
    'jenna': {'username': 'jenna', 'email': 'jenna@crosswalknyc.com', 'company': 'Crosswalk', 'credits': 50, 'role': 'super_admin'},
}


class _FakeHost:
    S3_BUCKET = 'dashboard-inputs'

    @staticmethod
    def _normalize_for_match(s):
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]+", " ", str(s or '').lower())).strip()

    @staticmethod
    def load_users():
        return {'users': {k: dict(v) for k, v in USERS.items()}}

    @staticmethod
    def _session_money_symbol(*a, **k):
        return '$'

    def __getattr__(self, name):
        return mock.MagicMock(name=name)


import prometheus.legacy as _legacy  # noqa: E402
_legacy.bind(_FakeHost())
from prometheus.legacy import chat as C  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402

PRICE = re.compile(r"[$\u00a3]\s?\d|prices at", re.I)
PARSED = {'subject': 'My Life with the Walter Boys', 'platform': 'Netflix', 'journey_kind': 'watch',
          'conversion_event': 'watched My Life with the Walter Boys on Netflix', 'start_date': '2026-08-06', 'end_date': '2026-11-10',
          'start_behavior': 'saw a social post or short-form clip about My Life with the Walter Boys', 'watch_definition': 'clicked_through'}


def _as(username):
    C.session = {'username': username}


class JourneyCard(unittest.TestCase):
    def test_unlimited_seat_reads_included_no_price(self):
        for name in ('unlim', 'jenna'):
            _as(name)
            self.assertTrue(PR.included_with_plan(USERS[name], name, None), name)
            card = C._pm_jiq_confirm_reply(dict(PARSED))
            self.assertIn(PR.INCLUDED_LINE + ' Run it?', card, (name, card))
            self.assertIsNone(PRICE.search(card), (name, card))
            self.assertTrue(C._pm_included_seat(name), name)

    def test_paying_seat_still_reads_the_price(self):
        _as('payer')
        self.assertFalse(PR.included_with_plan(USERS['payer'], 'payer', None))
        card = C._pm_jiq_confirm_reply(dict(PARSED))
        self.assertRegex(card, r"It prices at [$\u00a3]\s?\d[\d,]*\. Run it\?")
        self.assertNotIn(PR.INCLUDED_LINE, card)
        self.assertFalse(C._pm_included_seat('payer'))

    def test_media_week_card_same_rule(self):
        mw = dict(PARSED, journey_kind='media_week')
        _as('unlim')
        card = C._pm_jiq_confirm_reply(dict(mw))
        self.assertIn(PR.INCLUDED_LINE + ' Run it?', card, card)
        self.assertIsNone(PRICE.search(card), card)
        _as('payer')
        card = C._pm_jiq_confirm_reply(dict(mw))
        self.assertRegex(card, r"It prices at [$\u00a3]\s?\d[\d,]*\. Run it\?")

    def test_run_starts_on_confirm_with_the_same_chips(self):
        # the confirm card's chips do not change by seat: Run the journey starts the build, Cancel drops it
        kind, confirm_name, run_label, payload_key, collect_key, _labels = C._PM_INTAKE_UI['digital_journey']
        self.assertEqual((run_label, payload_key), ('Run the journey', 'jiq_confirm_payload'))
        src = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
        self.assertIn("'followups': [run_label, 'Cancel']", src)
        # the run turn's ack carries the included line for an included seat, through the one gate
        run_turn = src[src.index("_jiq_confirm = body.get('jiq_confirm')"):src.index("if _pm_collect_flag_applies(body, 'jiq_inputs', history)")]
        self.assertIn('_pm_included_seat(_bpiq_user)', run_turn)
        self.assertIn('with_included_line', run_turn)
        # the card's price sentence is the one helper, which asks included_with_plan
        helper = src[src.index('def _pm_price_sentence('):src.index('def _pm_included_seat(')]
        self.assertIn('included_with_plan', helper)
        self.assertIn('INCLUDED_LINE', helper)
        self.assertNotIn("It prices at {_pm_tool_price_label('journey_iq', '$500')}", src)


if __name__ == '__main__':
    unittest.main(verbosity=2)
