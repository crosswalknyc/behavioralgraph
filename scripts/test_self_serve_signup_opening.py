#!/usr/bin/env python3
"""Public dashboard signup: $500 opener, no $5k auto top-up.

Jenna 2026-11-20: take the 5k wallet top up off, let the $500 spend,
email jenna/liz on self-serve signup, email jenna/liz/czarina on the
$500 card payment, and let Prometheus open once they have paid.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("REGRESSION_TEST_MODE", "1")

import site_signup as S  # noqa: E402
import wallet_receipt_email as R  # noqa: E402


def _new():
    return S.new_self_serve_user_record(
        password_hash="x", email="emma.example@vice.com",
        first_name="Emma", last_name="Example", company="VICE",
        question="Who watches Landman, and what do they buy?",
        came_from="https://www.crosswalknyc.com/index.html")


class SelfServeOpening(unittest.TestCase):
    def test_new_seat_is_prepaid_500_not_5k_autoreload(self):
        rec = _new()
        self.assertEqual(rec["billing_mode"], "prepay_only")
        self.assertEqual(float(rec["auto_reload_amount_usd"]), 0.0)
        self.assertEqual(float(rec["opening_topup_usd"]), 500.0)
        self.assertEqual(rec["signup_status"], "pending_payment")
        self.assertTrue(rec["has_chatbot_profile_iq_access"])

    def test_checkout_metadata_disables_auto_reload(self):
        md = S.opening_checkout_metadata(
            username="emma.example@vice.com", question="q",
            came_from="/", company="VICE")
        self.assertEqual(md["enable_auto_reload"], "0")
        self.assertEqual(md["source"], "self_serve_signup")

    def test_pending_copy_is_500_not_5000(self):
        self.assertIn("$500", S.PENDING_PAYMENT_MESSAGE)
        self.assertNotIn("$5,000", S.PENDING_PAYMENT_MESSAGE)
        self.assertNotIn("$5000", S.PENDING_PAYMENT_MESSAGE)

    def test_opening_paid_once_lifetime_hits_500(self):
        rec = _new()
        self.assertFalse(S.opening_balance_paid(rec))
        rec["wallet_lifetime_topups_usd"] = 500.0
        self.assertTrue(S.opening_balance_paid(rec))
        rec["signup_status"] = "active"
        rec["wallet_lifetime_topups_usd"] = 0
        self.assertTrue(S.opening_balance_paid(rec))

    def test_signup_created_email_goes_to_jenna_and_liz(self):
        self.assertEqual(
            S.SIGNUP_CREATED_TO,
            ("jenna@crosswalknyc.com", "liz@crosswalknyc.com"))
        self.assertNotIn("jessie@crosswalknyc.com", S.SIGNUP_CREATED_TO)
        subj, body = S.build_signup_created_notice(
            username="emma.example@vice.com", first_name="Emma",
            last_name="Example", email="emma.example@vice.com",
            company="VICE", question="Who watches Landman?",
            came_from="https://www.crosswalknyc.com/index.html")
        self.assertIn("Emma Example", subj)
        self.assertIn("VICE", body)
        self.assertIn("$500.00", body)
        self.assertIn("not paid yet", body)
        self.assertNotIn("$5,000", body)
        self.assertNotIn("jessie@", body)

    def test_payment_notice_recipients_are_jenna_liz_czarina(self):
        self.assertEqual(
            R.INTERNAL_TO,
            ("jenna@crosswalknyc.com",
             "liz@crosswalknyc.com",
             "czarina@crosswalknyc.com"))

    def test_billing_routes_unskip_payment_notice_and_activate_on_skip(self):
        src = Path(ROOT, "billing_routes.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "Skip the generic top-up notice so the four", src)
        self.assertIn("def _activate_self_serve_if_needed", src)
        # Duplicate checkout skip used to return before activation.
        checkout = src.split("def _handle_checkout_session_completed", 1)[1]
        checkout = checkout.split("def _handle_payment_intent_succeeded", 1)[0]
        self.assertIn("_activate_self_serve_if_needed", checkout)
        self.assertNotIn(
            'print(f"[billing] webhook skip duplicate ${amt:.2f} for "\n'
            '              f"{subject_kind}:{subject_key} (session={ref})")\n'
            "        return",
            checkout)
        pi = src.split("def _handle_payment_intent_succeeded", 1)[1]
        pi = pi.split("def _handle_payment_intent_failed", 1)[0]
        self.assertIn("_activate_self_serve_if_needed", pi)

    def test_login_copy_in_app_is_500(self):
        src = Path(ROOT, "app.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "waiting on the $5,000 opening", src)
        self.assertIn("PENDING_PAYMENT_MESSAGE", src)
        self.assertIn("opening_balance_paid", src)

    def test_public_signup_page_drops_5k_top_up(self):
        html = Path(ROOT, "site/signup.html").read_text(encoding="utf-8")
        self.assertNotIn("$5,000 when the balance reaches $100", html)
        self.assertIn("$500 prepaid", html)


if __name__ == "__main__":
    unittest.main()
