#!/usr/bin/env python3
"""Use-it-or-lose-it monthly included allowance (Jenna 2026-11-17).

Sony Pictures Television: leftover is forfeited, the wallet is set to
$11,000, pulls say included until the amount is gone, Jenna and Liz
are mailed once when it runs out. Additive wallet grants (Dick Wolf)
stay additive.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("REGRESSION_TEST_MODE", "1")

import wallet as w  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
import wallet_allowance_notify as N  # noqa: E402

NOV = datetime(2026, 11, 17, 20, 0, tzinfo=timezone.utc)
DEC = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)


def _grant(gid="spt_11k_monthly", amount=11000, mode="replace"):
    return {
        "id": gid,
        "kind": "wallet",
        "direction": "grant",
        "mode": mode,
        "amount": amount,
        "reason": "Monthly included allowance $11,000",
    }


def _spt_company(balance=11000.0):
    return {
        "paying_customer": True,
        "unlimited": False,
        "credit_pool": 0,
        "included_while_funded": True,
        "monthly_allowance_usd": 11000,
        "billing_mode": "prepay_only",
        "wallet_balance_usd": float(balance),
        "wallet_lifetime_spend_usd": 0.0,
        "wallet_lifetime_topups_usd": 0.0,
        "wallet_transactions": [],
    }


def _spt_seat():
    return {
        "username": "william_henderson",
        "email": "william_henderson@spe.sony.com",
        "company": "Sony Pictures Television",
        "billing_source": "company",
        "unlimited": False,
        "credits": 0,
        "paying_customer": True,
    }


def _data(company, seat=None):
    seat = seat or _spt_seat()
    return {
        "users": {seat["username"]: seat},
        "companies": {"Sony Pictures Television": company},
    }


class ReplaceGrant(unittest.TestCase):
    def test_additive_default_still_stacks(self):
        subj = {"wallet_balance_usd": 5000.0, "wallet_transactions": []}
        w.credit_recurring_wallet_month(
            subj, _grant(mode="add", amount=5000), now=DEC)
        self.assertEqual(w.wallet_balance(subj), 10000.0)

    def test_replace_forfeits_leftover_then_sets_eleven_k(self):
        subj = {
            "wallet_balance_usd": 8000.0,
            "wallet_lifetime_spend_usd": 214.50,
            "wallet_lifetime_topups_usd": 0.0,
            "wallet_transactions": [],
        }
        w.credit_recurring_wallet_month(subj, _grant(), now=DEC)
        self.assertEqual(w.wallet_balance(subj), 11000.0)
        self.assertEqual(subj["wallet_lifetime_spend_usd"], 214.50)
        self.assertEqual(subj["wallet_lifetime_topups_usd"], 0.0)
        kinds = [t["kind"] for t in subj["wallet_transactions"]]
        self.assertEqual(kinds, ["adjustment", "forfeit"])
        forfeits = [t for t in subj["wallet_transactions"] if t["kind"] == "forfeit"]
        self.assertEqual(forfeits[0]["amount_usd"], 8000.0)

    def test_replace_with_empty_wallet_just_sets_eleven_k(self):
        subj = {"wallet_balance_usd": 0.0, "wallet_transactions": []}
        w.credit_recurring_wallet_month(subj, _grant(), now=DEC)
        self.assertEqual(w.wallet_balance(subj), 11000.0)
        kinds = [t["kind"] for t in subj["wallet_transactions"]]
        self.assertEqual(kinds, ["adjustment"])

    def test_same_month_is_a_noop(self):
        subj = {"wallet_balance_usd": 8000.0, "wallet_transactions": []}
        first = w.credit_recurring_wallet_month(subj, _grant(), now=DEC)
        bal = w.wallet_balance(subj)
        second = w.credit_recurring_wallet_month(subj, _grant(), now=DEC)
        self.assertEqual(first.get("stripe_ref"), second.get("stripe_ref"))
        self.assertEqual(w.wallet_balance(subj), bal)
        self.assertEqual(len(subj["wallet_transactions"]), 2)


def _bind(data):
    from prometheus.host import host
    host.bind(
        load_users=lambda: data, consume_credit=lambda *a, **k: True,
        check_user_credits=lambda u: (True, 0),
        analyze_core=lambda *a, **k: {}, interpret_core=lambda *a, **k: {},
        deck_core=lambda *a, **k: {}, ask_hint=lambda **k: None,
        gate_analyze=lambda u: True, gate_pull=lambda u: True,
        funds_gate=lambda u: None,
        gate_refusal=lambda s: ({"success": False}, 403),
        calm_payload=lambda: {}, error_email=lambda *a, **k: None,
        load_threads_index=lambda u: {"threads": []}, max_threads=50,
        s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
        thread_key=lambda u, t: f"{u}/{t}", threads_index_key=lambda u: u,
        thread_title_from=lambda t: t[:30], bucket="x", s3_client=None,
        job_owner_ok=lambda a, b: True, claude_data=lambda *a, **k: {},
        pricing_question=lambda t: False,
        pricing_copy_for=lambda u: "", pricing_copy="")


class IncludedWhileFunded(unittest.TestCase):
    def test_included_while_balance_covers_the_pull(self):
        company = _spt_company(11000)
        seat = _spt_seat()
        data = _data(company, seat)
        _bind(data)
        self.assertTrue(w.allowance_is_included(seat, data))
        self.assertTrue(w.allowance_is_included(seat, data, amount_usd=500))
        self.assertTrue(PR.included_with_plan(seat, "william_henderson", None))
        self.assertFalse(PR.seat_is_free(seat, "william_henderson", None))

    def test_not_included_when_empty(self):
        company = _spt_company(0)
        seat = _spt_seat()
        data = _data(company, seat)
        _bind(data)
        self.assertFalse(w.allowance_is_included(seat, data))
        self.assertFalse(PR.included_with_plan(seat, "william_henderson", None))

    def test_leftover_smaller_than_the_price_is_not_included(self):
        company = _spt_company(50)
        seat = _spt_seat()
        data = _data(company, seat)
        self.assertTrue(w.allowance_is_included(seat, data))
        self.assertFalse(w.allowance_is_included(seat, data, amount_usd=500))

    def test_other_wallets_are_not_included(self):
        seat = {"username": "bkusin", "company": "Kartel", "billing_source": "company",
                "credits": 0, "paying_customer": True}
        company = {"paying_customer": True, "wallet_balance_usd": 3000.0}
        data = {"users": {"bkusin": seat}, "companies": {"Kartel": company}}
        self.assertFalse(w.allowance_is_included(seat, data))


class OfferConsumesAllowance(unittest.TestCase):
    def setUp(self):
        self.charges = []
        self.company = _spt_company(11000)
        self.seat = _spt_seat()
        self.data = _data(self.company, self.seat)
        from prometheus.host import host

        def _load():
            return self.data

        def _consume(username, **kw):
            self.charges.append((username, dict(kw)))
            return True

        host.bind(
            load_users=_load, consume_credit=_consume,
            check_user_credits=lambda u: (True, 0),
            analyze_core=lambda *a, **k: {},
            interpret_core=lambda *a, **k: {},
            deck_core=lambda *a, **k: {},
            ask_hint=lambda **k: None, gate_analyze=lambda u: True,
            gate_pull=lambda u: True, funds_gate=lambda u: None,
            gate_refusal=lambda s: ({"success": False}, 403),
            calm_payload=lambda: {}, error_email=lambda *a, **k: None,
            load_threads_index=lambda u: {"threads": []}, max_threads=50,
            s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
            thread_key=lambda u, t: f"{u}/{t}", threads_index_key=lambda u: u,
            thread_title_from=lambda t: t[:30], bucket="x", s3_client=None,
            job_owner_ok=lambda a, b: True, claude_data=lambda *a, **k: {},
            pricing_question=lambda t: False,
            pricing_copy_for=lambda u: "", pricing_copy="")

    def test_included_allowance_still_consumes(self):
        state, payload = PR.offer_or_charge(
            text="top 10 QSRs for Gilmore Girls viewers",
            subject="Gilmore Girls viewers",
            kind="custom", panel_confirm=None,
            username="william_henderson", user=self.seat, history=[])
        self.assertEqual(state, "charged")
        self.assertTrue(payload.get("included"))
        self.assertEqual(payload.get("credits"), 0)
        self.assertEqual(payload.get("label"), PR.INCLUDED_LABEL)
        self.assertEqual(len(self.charges), 1)
        self.assertGreaterEqual(self.charges[0][1].get("credits_used"), 1)

    def test_unlimited_still_skips_consume(self):
        self.charges.clear()
        unlim = {"username": "unlim", "credits": -1, "paying_customer": True}
        self.data["users"]["unlim"] = unlim
        state, payload = PR.offer_or_charge(
            text="top 10 QSRs for Gilmore Girls viewers",
            subject="Gilmore Girls viewers",
            kind="custom", panel_confirm=None,
            username="unlim", user=unlim, history=[])
        self.assertEqual(state, "charged")
        self.assertTrue(payload.get("included"))
        self.assertEqual(self.charges, [])


class RunoutNotice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        os.environ["ALLOWANCE_RUNOUT_STATE_FILE"] = self.tmp.name
        N._mem.clear()
        self.sent = []

    def tearDown(self):
        os.environ.pop("ALLOWANCE_RUNOUT_STATE_FILE", None)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass
        N._mem.clear()

    def test_one_email_per_company_per_month(self):
        def send(subject, body, recipients):
            self.sent.append((subject, body, list(recipients)))

        ok1 = N.send_runout("Sony Pictures Television", 11000,
                            billed_via="william_henderson", send=send)
        ok2 = N.send_runout("Sony Pictures Television", 11000,
                            billed_via="amy_ryan", send=send)
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertEqual(len(self.sent), 1)
        subj, body, to = self.sent[0]
        self.assertIn("Sony Pictures Television", subj)
        self.assertIn("$11,000", body)
        self.assertEqual(to, ["jenna@crosswalknyc.com", "liz@crosswalknyc.com"])
        self.assertNotIn("czarina", body.lower())

    def test_wallet_wrapper_skips_other_companies(self):
        seat = {"username": "bkusin", "company": "Kartel",
                "billing_source": "company"}
        data = {"users": {"bkusin": seat},
                "companies": {"Kartel": {"wallet_balance_usd": 0,
                                         "paying_customer": True}}}
        self.assertFalse(w.notify_allowance_exhausted(seat, data, billed_via="bkusin"))

    def test_wallet_wrapper_fires_for_spt(self):
        company = _spt_company(0)
        seat = _spt_seat()
        data = _data(company, seat)
        sent = []

        def send(subject, body, recipients):
            sent.append(subject)

        real = N.send_runout
        N.send_runout = lambda *a, **k: real(*a, send=send, **k)
        try:
            self.assertTrue(w.notify_allowance_exhausted(
                seat, data, billed_via="william_henderson"))
            self.assertEqual(len(sent), 1)
        finally:
            N.send_runout = real


if __name__ == "__main__":
    unittest.main()
