#!/usr/bin/env python3
"""Dated unlimited credits (Jenna 2026-11-19).

Kate Coke and Samantha Kolton: unlimited through December 31. After
that calendar day they are billed again even if the unlimited flag
is still on the row.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("REGRESSION_TEST_MODE", "1")

import wallet as w  # noqa: E402

UNTIL = "2026-12-31"
DEC31 = datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)
JAN1 = datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc)
PULL = "Chatbot Profile IQ (new_build)"


def _seat(**over):
    rec = {
        "username": "skolton",
        "email": "samantha.kolton@caa.com",
        "company": "CAA",
        "billing_source": "company",
        "unlimited": True,
        "unlimited_until": UNTIL,
        "credits": 0,
        "paying_customer": True,
        "require_card_to_view": False,
        "opening_topup_usd": 0,
    }
    rec.update(over)
    return rec


def _company(**over):
    rec = {
        "paying_customer": True,
        "unlimited": False,
        "credit_pool": 0,
        "billing_mode": "prepay_only",
        "wallet_balance_usd": -1.34,
        "wallet_lifetime_spend_usd": 1823.08,
        "wallet_lifetime_topups_usd": 0.0,
        "require_card_to_view": True,
        "opening_topup_usd": 250.0,
        "default_spend_scope": "*",
    }
    rec.update(over)
    return rec


def _data(seat=None, company=None):
    seat = seat or _seat()
    return {
        "users": {seat["username"]: seat},
        "companies": {"CAA": company if company is not None else _company()},
    }


class Window(unittest.TestCase):
    def test_holds_through_december_31(self):
        u = _seat()
        self.assertFalse(w.unlimited_window_expired(u, today=DEC31))
        self.assertTrue(w.is_unlimited(u, today=DEC31))
        self.assertTrue(w.is_unlimited(u, today=date(2026, 12, 31)))

    def test_ends_january_1(self):
        u = _seat()
        self.assertTrue(w.unlimited_window_expired(u, today=JAN1))
        self.assertFalse(w.is_unlimited(u, today=JAN1))
        self.assertFalse(w.is_unlimited(u, today=date(2027, 1, 1)))

    def test_no_until_stays_open(self):
        u = _seat(unlimited_until="")
        self.assertTrue(w.is_unlimited(u, today=JAN1))
        amazon = {"unlimited": True, "credits": -1, "credit_pool": -1}
        self.assertTrue(w.is_unlimited(amazon, today=JAN1))

    def test_credits_sentinel_also_ends(self):
        u = _seat(unlimited=False, credits=-1)
        self.assertTrue(w.is_unlimited(u, today=DEC31))
        self.assertFalse(w.is_unlimited(u, today=JAN1))

    def test_past_until_is_already_closed(self):
        u = _seat(unlimited_until="2026-11-18")
        self.assertFalse(w.is_unlimited(u))


class PullFunding(unittest.TestCase):
    def setUp(self):
        self._load = w.load_pricing
        w.load_pricing = lambda *a, **k: w.DEFAULT_PRICING

    def tearDown(self):
        w.load_pricing = self._load

    def test_seat_unlimited_covers_negative_company_wallet(self):
        data = _data()
        seat = data["users"]["skolton"]
        out = w.pull_funding(seat, data, PULL)
        self.assertTrue(out["covered"])
        self.assertEqual(out["reason"], "unlimited")

    def test_expired_seat_does_not_cover_negative_wallet(self):
        data = _data(seat=_seat(unlimited_until="2026-11-18"))
        seat = data["users"]["skolton"]
        self.assertFalse(w.is_unlimited(seat))
        out = w.pull_funding(seat, data, PULL)
        self.assertFalse(out["covered"])
        self.assertEqual(out["reason"], "insufficient")


class SeatIsFreeHonorsTheWindow(unittest.TestCase):
    def test_priced_read_uses_is_unlimited(self):
        from prometheus.priced_read import seat_is_free
        open_seat = _seat()
        closed = _seat(unlimited_until="2026-11-18")
        self.assertTrue(seat_is_free(open_seat, "skolton", host=None))
        self.assertFalse(seat_is_free(closed, "skolton", host=None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
