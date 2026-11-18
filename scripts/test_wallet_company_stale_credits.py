#!/usr/bin/env python3
"""A funded company wallet is never refused as "costs $0" (2026-11-17).

Naomi Inoshita (Sony Pictures Television) confirmed a Subscriber IQ on
Outlander: Blood of My Blood and hit Approve five times. Every click
came back 402 with "This run costs $0. Wallet balance is $11,000. Top
up to keep going." The company row carried a stale ``credits: -1``
next to ``unlimited: False`` and a $11,000 funded wallet; the wallet
helpers read the company row as unlimited, should_charge_wallet said
"nothing to charge", and the approve preflight refused.

Pinned here:
  * a company row's unlimited state is its own flag or pool sentinel,
    never a stray ``credits`` key;
  * an unlimited billing subject covers a pull instead of refusing it;
  * a Prometheus Subscriber IQ debits the tracker key it quoted.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("REGRESSION_TEST_MODE", "1")

import wallet as w  # noqa: E402

PULL = "Chatbot Profile IQ (subscriber_iq)"
TRACKER_USD = float(w.DEFAULT_PRICING["per_tool_usd"]["api_subscriber_iq_build"])


def _company(**over):
    rec = {
        "paying_customer": True,
        "unlimited": False,
        "credit_pool": 0,
        "credit_pool_used": 0,
        "included_while_funded": True,
        "monthly_allowance_usd": 11000,
        "billing_mode": "prepay_only",
        "default_spend_scope": "*",
        "wallet_balance_usd": 11000.0,
        "wallet_lifetime_spend_usd": 0.0,
        "wallet_lifetime_topups_usd": 0.0,
        "wallet_transactions": [],
    }
    rec.update(over)
    return rec


def _seat(**over):
    rec = {
        "username": "naomi_inoshita",
        "email": "naomi_inoshita@spe.sony.com",
        "company": "Sony Pictures Television",
        "billing_source": "company",
        "company_spend_scope": "inherit",
        "unlimited": False,
        "credits": 0,
        "credit_ceiling": -1,
        "paying_customer": True,
    }
    rec.update(over)
    return rec


def _data(company, seat=None):
    seat = seat or _seat()
    return {
        "users": {seat["username"]: seat},
        "companies": {"Sony Pictures Television": company},
    }


class Hermetic(unittest.TestCase):
    def setUp(self):
        self._load_pricing = w.load_pricing
        w.load_pricing = lambda *a, **k: w.DEFAULT_PRICING

    def tearDown(self):
        w.load_pricing = self._load_pricing


class CompanyUnlimitedReadsItsOwnFields(Hermetic):
    def test_stale_credits_key_is_not_unlimited(self):
        company = _company(credits=-1)
        self.assertFalse(w.is_unlimited(company))

    def test_company_flag_is_unlimited(self):
        self.assertTrue(w.is_unlimited(_company(unlimited=True)))

    def test_unlimited_pool_is_unlimited(self):
        self.assertTrue(w.is_unlimited(_company(credit_pool=-1)))

    def test_seat_semantics_unchanged(self):
        self.assertTrue(w.is_unlimited(_seat(credits=-1)))
        self.assertFalse(w.is_unlimited(_seat(credits=0)))
        self.assertTrue(w.is_unlimited(_seat(unlimited=True)))


class FundedCompanyWalletCoversThePull(Hermetic):
    def test_naomi_shape_is_covered_and_included(self):
        data = _data(_company(credits=-1))
        seat = data["users"]["naomi_inoshita"]
        usd, mode = w.should_charge_wallet(
            data["companies"]["Sony Pictures Television"],
            w.pull_type_to_tool_key(PULL))
        self.assertEqual((usd, mode), (TRACKER_USD, "wallet"))
        self.assertTrue(w.user_wallet_covers_pull(seat, data, PULL))
        self.assertTrue(w.allowance_is_included(seat, data))
        self.assertTrue(w.allowance_is_included(seat, data, amount_usd=TRACKER_USD))

    def test_unlimited_company_covers_the_pull(self):
        for company in (_company(unlimited=True), _company(credit_pool=-1)):
            data = _data(company)
            seat = data["users"]["naomi_inoshita"]
            self.assertTrue(w.user_wallet_covers_pull(seat, data, PULL))

    def test_empty_prepay_wallet_still_refuses(self):
        data = _data(_company(wallet_balance_usd=0.0))
        seat = data["users"]["naomi_inoshita"]
        self.assertFalse(w.user_wallet_covers_pull(seat, data, PULL))
        self.assertFalse(w.allowance_is_included(seat, data))

    def test_not_paying_still_refuses(self):
        data = _data(_company(paying_customer=False))
        seat = data["users"]["naomi_inoshita"]
        self.assertFalse(w.user_wallet_covers_pull(seat, data, PULL))


class SubscriberIqDebitsTheTrackerKey(Hermetic):
    def test_prometheus_subscriber_iq_maps_to_tracker(self):
        self.assertEqual(w.pull_type_to_tool_key(PULL), "api_subscriber_iq_build")
        self.assertEqual(w.pull_type_to_tool_key("Chatbot Profile IQ v1 (subscriber_iq)"),
                         "api_subscriber_iq_build")

    def test_other_decisions_unchanged(self):
        self.assertEqual(w.pull_type_to_tool_key("Chatbot Profile IQ (new_build)"),
                         "api_chatbot_profile_iq_build")
        self.assertEqual(w.pull_type_to_tool_key("Chatbot Profile IQ (derive_cut)"),
                         "profile_iq_derived_cut")
        self.assertEqual(w.pull_type_to_tool_key("Chatbot Profile IQ v1 (derive_cut)"),
                         "api_profile_iq_cut")


if __name__ == "__main__":
    unittest.main(verbosity=2)
