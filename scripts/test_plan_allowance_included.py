#!/usr/bin/env python3
"""A plan allowance reads as included, runs, meters, and asks for a top-up
only when it is used up (Jenna 2026-11-17, Naomi Inoshita at Sony).

Naomi approved a Subscriber IQ five times and read "This run costs $0.
Wallet balance is $11,000. Top up to keep going." each time, then asked
"is it running?" and was told nothing was running or delivered.

Jenna: "it should just say included with your plan and run everything
UNTIL it hits 11k of usage at which point it would ask them to top up
and email me and liz".

Hermetic: no network, no mail, nothing charged. The dashboard's own
charge functions (check_user_credits, has_credits_for, consume_credit,
the wallet fallback, the refusal copy) are lifted out of app.py by name
and run against a users document held in memory, so what is tested is
the code that ships, without booting the dashboard.

A. A plan allowance seat reads "Included with your plan." with no price
   and no balance, on the price sentence and on the Subscriber IQ step.
B. Approve is allowed and meters the list price while allowance remains;
   a product with no price set runs at no charge.
C. At run-out the top-up ask appears (no price, no balance, never "$0")
   and Jenna and Liz are asked for exactly one notice; a top-up resets it.
D. An unlimited seat and a staff seat are unchanged.
E. A pay-per-use seat that is short reads its real price, never "$0".
F. A canary identity never mails.
G. "is it running?" after a refused Approve says it has not started.
"""
from __future__ import annotations

import ast
import copy
import os
import re
import sys
import tempfile
import traceback
import unittest
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("REGRESSION_TEST_MODE", "1")
os.environ.setdefault("PM_MAIL_OFF", "1")
os.environ.setdefault("CORPUS_CATALOG", "0")
os.environ.setdefault("PM_PROBE_LOOKUP", "0")     # no sandbox-key lookup over the network

WEB = Path(__file__).resolve().parents[1]
if str(WEB) not in sys.path:
    sys.path.insert(0, str(WEB))

import wallet as W  # noqa: E402
import wallet_allowance_notify as N  # noqa: E402
from prometheus import plan_allowance as PLAN  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
from prometheus import status_lane as SL  # noqa: E402

# the pricing table is read from memory: nothing reaches for the app or the network
PRICING = copy.deepcopy(W.DEFAULT_PRICING)
W._pricing_cache["value"] = PRICING
W._pricing_cache["loaded_at"] = W._now_ts() + 10 ** 9

APP_SRC = (WEB / "app.py").read_text(encoding="utf-8")
CHAT_SRC = (WEB / "prometheus" / "legacy" / "chat.py").read_text(encoding="utf-8")
INDEX_SRC = (WEB / "templates" / "index.html").read_text(encoding="utf-8")

SUBIQ = "Chatbot Profile IQ (subscriber_iq)"
SUBIQ_USD = float(PRICING["per_tool_usd"]["api_subscriber_iq_build"])
COMPANY = "Sony Pictures Television"
DASH_RX = re.compile("[\u2014\u2013]")
INTERNAL_RX = re.compile(
    r"\b(synth\w*|modeled|estimated|pipeline|queue|worker|hostmap|wallet|credits?|claude|anthropic|opus|sonnet)\b", re.I)


def _lift(src, names):
    """The named top-level functions of a source file, as source text."""
    tree = ast.parse(src)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            out[node.name] = ast.get_source_segment(src, node)
    missing = [n for n in names if n not in out]
    assert not missing, f"not found: {missing}"
    return "\n\n\n".join(out[n] for n in names)


APP_FUNCS = ("_get_company_pool", "_numeric_credits_balance", "_credit_num",
             "_seat_is_unlimited",
             "check_user_credits", "_usd_label", "_funds_short_error",
             "has_credits_for", "_funds_refusal_copy", "_try_wallet_fallback",
             "_credit_history_ts", "_record_wallet_fallback_usage",
             "consume_credit")
CHAT_FUNCS = ("_pm_seat_reads_included", "_pm_price_sentence", "_pm_tool_price_label")
# lifted and compiled once: parsing the dashboard is the slow part
APP_CODE = compile(_lift(APP_SRC, APP_FUNCS), "app.py (lifted)", "exec")
CHAT_CODE = compile(_lift(CHAT_SRC, CHAT_FUNCS), "chat.py (lifted)", "exec")


class _Faults:
    @staticmethod
    def note(*a, **k):
        return None


class Desk:
    """The dashboard's charge functions over a users document in memory."""

    def __init__(self, data):
        self.data = data
        self.session = {}
        ns = {
            "load_users": lambda: self.data,
            "_users_cas_mutate": lambda fn: fn(self.data),
            "_stamp_paid_profile_after_consume": lambda *a, **k: None,
            "_session_money_symbol": lambda username=None: "$",
            "session": self.session,
            "traceback": traceback, "datetime": datetime, "timezone": timezone,
        }
        exec(APP_CODE, ns)
        self.app = ns
        chat_ns = {
            "_H": self, "session": self.session, "_faults": _Faults, "_plan": PLAN,
            "_pm_usd_label": lambda v, username=None: ns["_usd_label"](v, username),
        }
        exec(CHAT_CODE, chat_ns)
        self.chat = chat_ns

    # the host capabilities priced_read asks for
    def has(self, name):
        return name in ("load_users", "check_user_credits", "consume_credit")

    def load_users(self):
        return self.data

    def check_user_credits(self, username):
        return self.app["check_user_credits"](username)

    def consume_credit(self, *a, **k):
        return self.app["consume_credit"](*a, **k)

    def company(self, name=COMPANY):
        return self.data["companies"][name]

    def deducts(self, name=COMPANY):
        return [t for t in self.company(name).get("wallet_transactions") or []
                if t.get("kind") == "deduct"]

    def approve(self, username, pull_type=SUBIQ, credits=10, what=""):
        """The approve path in the order the chat runs it: the shared
        preflight, the refusal body the chat returns, then the charge.
        (started, refusal line)."""
        if not self.app["has_credits_for"](username, credits, pull_type=pull_type, what=what):
            body = PLAN.refusal_payload(self, username, pull_type, 0, credits,
                                        lambda v: self.app["_usd_label"](v, username))
            line, _v = self.app["_funds_refusal_copy"](username, pull_type)
            assert body["error"] == line, (body["error"], line)     # the chat and the dashboard read one line
            assert body["success"] is False and body["top_up_url"] == "/wallet"
            self.last_refusal = body
            return False, line
        ok = self.app["consume_credit"](username, description=what or "run",
                                        job_id="", pull_type=pull_type,
                                        credits_used=credits)
        return bool(ok), ""


def _allowance_company(balance=11000.0, **over):
    rec = {
        "paying_customer": True, "unlimited": False, "no_cap": False,
        "credits": -1,                     # the stale sentinel the live row carried
        "credit_pool": 0, "credit_pool_used": 0,
        "included_while_funded": True, "monthly_allowance_usd": 11000,
        "billing_mode": "prepay_only", "default_spend_scope": "*",
        "wallet_balance_usd": float(balance),
        "wallet_lifetime_spend_usd": round(11000.0 - float(balance), 2),
        "wallet_lifetime_topups_usd": 0.0,
        "wallet_transactions": [{
            "ts": "2026-11-18T00:32:53Z", "kind": "adjustment", "amount_usd": 11000.0,
            "balance_after_usd": 11000.0, "description": "Monthly included allowance $11,000",
            "stripe_ref": "recurring_wallet_spt_11k_monthly_2026_11"}],
    }
    rec.update(over)
    return rec


def _seat(username="naomi_inoshita", company=COMPANY, **over):
    rec = {
        "username": username, "email": f"{username}@spe.sony.com",
        "first_name": "Naomi", "last_name": "Inoshita",
        "company": company, "billing_source": "company",
        "company_spend_scope": "inherit", "role": "user",
        "credits": 0, "credit_ceiling": -1, "unlimited": False, "no_cap": False,
        "paying_customer": True,
    }
    rec.update(over)
    return rec


def _data(company=None, seats=None):
    seats = seats or [_seat()]
    return {"users": {s["username"]: s for s in seats},
            "companies": {COMPANY: company if company is not None else _allowance_company()}}


class _MailCase(unittest.TestCase):
    """Every send is captured; the real sender is never reached."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
        self.tmp.close()
        os.unlink(self.tmp.name)
        os.environ["ALLOWANCE_RUNOUT_STATE_FILE"] = self.tmp.name
        N._mem.clear()
        self.sent = []
        self._real = N.send_runout

        def _capture(company, amount_usd=0, billed_via="", send=None, **kw):
            return self._real(
                company, amount_usd, billed_via,
                send=lambda s, b, to: self.sent.append((s, b, list(to))), **kw)
        N.send_runout = _capture

    def tearDown(self):
        N.send_runout = self._real
        os.environ.pop("ALLOWANCE_RUNOUT_STATE_FILE", None)
        for p in (self.tmp.name, self.tmp.name + ".tmp"):
            try:
                os.unlink(p)
            except OSError:
                pass
        N._mem.clear()


class A_ReadsIncluded(_MailCase):
    def test_price_sentence_is_the_included_line(self):
        desk = Desk(_data())
        line = desk.chat["_pm_price_sentence"]("subscriber_iq_build", "$500", "naomi_inoshita")
        self.assertEqual(line, "Included with your plan.")
        self.assertEqual(line, PR.INCLUDED_LINE)
        self.assertNotIn("$", line)
        self.assertTrue(desk.chat["_pm_seat_reads_included"](SUBIQ_USD, "naomi_inoshita"))
        self.assertTrue(PR.included_with_plan(desk.data["users"]["naomi_inoshita"], "naomi_inoshita", desk))
        # a metered allowance is not an unlimited seat: it still draws down
        self.assertFalse(PR.seat_is_free(desk.data["users"]["naomi_inoshita"], "naomi_inoshita", desk))
        self.assertFalse(W.is_unlimited(desk.company()))

    def test_subscriber_iq_step_prints_no_price_to_an_included_seat(self):
        # the three lines of the add-on step branch on the included check
        # (the figures weighed are the approval's own charge since 2026-11-17: prometheus.order_quote,
        # scripts/test_quote_equals_charge.py)
        self.assertIn("_plan.SUBIQ_JUST if _pm_seat_reads_included(_sq['alone']) else", CHAT_SRC)
        self.assertIn("_plan.SUBIQ_ADDED if _pm_seat_reads_included(_sq['total']) else", CHAT_SRC)
        self.assertIn("_plan.subiq_ask(label) if _pm_seat_reads_included(_sq['total']) else", CHAT_SRC)
        for line in (PLAN.SUBIQ_JUST, PLAN.SUBIQ_ADDED, PLAN.subiq_ask("Outlander")):
            self.assertIn("ncluded with your plan", line)
            self.assertNotIn("$", line)
            self.assertIsNone(DASH_RX.search(line))
        self.assertTrue(PLAN.SUBIQ_JUST.startswith("Just the Subscriber IQ. Included with your plan."))
        # the add-on cut lines carry no per-cut price for an included seat
        self.assertEqual(PLAN.cut_tag(True, "$100"), "")
        self.assertEqual(PLAN.cut_tag(False, "$100"), " (+$100)")
        self.assertNotIn("$", PLAN.cut_rate(True, "$100") + PLAN.cut_each(True, "$100") + PLAN.cut_word(True, "$100")
                         + PLAN.lock_total(True, "$500", "$300") + PLAN.lock_cuts(True, 2, "$100")
                         + PLAN.build_cost_clause(True, "$500"))
        self.assertEqual(PLAN.lock_total(False, "$500", "$300") + PLAN.lock_cuts(False, 2, "$100"),
                         "Total: $500 (base $300 2 x $100 for the cuts, ")
        self.assertEqual(PLAN.approve_what("subscriber_iq", {"name": "Outlander: Blood of My Blood"}),
                         "Subscriber IQ: Outlander: Blood of My Blood")
        # the widget's chip and question drop the price for an included seat
        self.assertIn("? 'Add the Profile IQ'   // included with the plan", INDEX_SRC)
        self.assertIn("audience alongside it? It is included with your plan. ", INDEX_SRC)

    def test_no_surface_prices_a_refusal_on_its_own(self):
        for src in (APP_SRC, CHAT_SRC):
            self.assertNotIn("This run costs {_pm_usd_label(_usd)}", src)
            self.assertNotIn('This run costs ${_usd', src)
        self.assertNotIn("This run costs", INDEX_SRC)
        # every dashboard preflight names its product for the shared check
        bare = re.findall(r"has_credits_for\(username, [A-Za-z_]+\)", APP_SRC)
        self.assertEqual(bare, [])


class B_RunsAndMeters(_MailCase):
    def test_approve_starts_and_draws_the_list_price(self):
        desk = Desk(_data())
        started, line = desk.approve("naomi_inoshita", what="Subscriber IQ: Outlander: Blood of My Blood")
        self.assertTrue(started)
        self.assertEqual(line, "")
        self.assertEqual(W.wallet_balance(desk.company()), 11000.0 - SUBIQ_USD)
        rows = desk.deducts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount_usd"], -SUBIQ_USD)
        self.assertEqual(rows[0]["tool"], "api_subscriber_iq_build")
        self.assertEqual(rows[0]["billed_via_username"], "naomi_inoshita")
        self.assertEqual(self.sent, [])
        # still included for the next one
        self.assertEqual(desk.chat["_pm_price_sentence"]("journey_iq", "$500", "naomi_inoshita"),
                         "Included with your plan.")

    def test_dashboard_products_meter_their_own_price(self):
        desk = Desk(_data())
        for pull, key in (("SVOD", "subscriber_iq_build"), ("Digital Journey IQ", "journey_iq"),
                          ("Brand Partnership IQ", "brand_partnership_iq"), ("Profile Analysis", "profile_iq_build")):
            before = W.wallet_balance(desk.company())
            started, _ = desk.approve("naomi_inoshita", pull_type=pull)
            self.assertTrue(started, pull)
            self.assertEqual(round(before - W.wallet_balance(desk.company()), 2),
                             float(PRICING["per_tool_usd"][key]), pull)

    def test_a_product_with_no_price_runs_at_no_charge(self):
        desk = Desk(_data(_allowance_company(balance=40.0)))
        started, _ = desk.approve("naomi_inoshita", pull_type="Ticket Sales", credits=1)
        self.assertTrue(started)
        self.assertEqual(W.wallet_balance(desk.company()), 40.0)
        self.assertEqual(desk.deducts(), [])
        hist = desk.data["users"]["naomi_inoshita"].get("credit_usage_history") or []
        self.assertEqual(len(hist), 1)
        self.assertEqual(self.sent, [])


class C_RunOut(_MailCase):
    def test_short_allowance_asks_for_a_top_up_and_mails_once(self):
        desk = Desk(_data(_allowance_company(balance=300.0)))
        started, line = desk.approve("naomi_inoshita", what="Subscriber IQ: Outlander: Blood of My Blood")
        self.assertFalse(started)
        self.assertEqual(line, W.ALLOWANCE_SHORT_LINE)
        for banned in ("$", "balance", "costs", "Wallet"):
            self.assertNotIn(banned, line)
        self.assertIn("Top up to keep going.", line)
        self.assertIsNone(desk.last_refusal["wallet_balance_usd"])      # no balance on the body either
        self.assertTrue(desk.last_refusal["not_started"])
        self.assertEqual(W.wallet_balance(desk.company()), 300.0)
        self.assertEqual(desk.deducts(), [])
        # repeated clicks, another seat and the charge door itself: still one notice
        desk.data["users"]["amy_ryan"] = _seat("amy_ryan", first_name="Amy", last_name="Ryan")
        desk.approve("naomi_inoshita")
        desk.approve("amy_ryan")
        self.assertFalse(desk.app["consume_credit"]("naomi_inoshita", description="x", pull_type=SUBIQ, credits_used=10))
        self.assertEqual(len(self.sent), 1)
        subject, body, to = self.sent[0]
        self.assertEqual(subject, "Sony Pictures Television has used its plan allowance")
        self.assertEqual(to, ["jenna@crosswalknyc.com", "liz@crosswalknyc.com"])
        self.assertIn("Who: Naomi Inoshita (naomi_inoshita@spe.sony.com)", body)
        self.assertIn("Tried to run: Subscriber IQ: Outlander: Blood of My Blood ($500)", body)
        self.assertIn("Usage to date: $10,700 of $11,000, $300 left", body)
        self.assertIn("did not start and nothing was charged", body)
        self.assertIsNone(DASH_RX.search(subject + body))
        self.assertNotIn("jessie", (subject + body + " ".join(to)).lower())

    def test_an_empty_allowance_reads_used_up(self):
        desk = Desk(_data(_allowance_company(balance=0.0)))
        started, line = desk.approve("naomi_inoshita")
        self.assertFalse(started)
        self.assertEqual(line, W.ALLOWANCE_USED_LINE)
        self.assertNotIn("$", line)
        self.assertEqual(len(self.sent), 1)
        # the dashboard's own short-funds line reads the same, never a price
        self.assertEqual(desk.app["_funds_short_error"]("Subscriber IQ", "subscriber_iq_build", "naomi_inoshita"),
                         W.ALLOWANCE_USED_LINE)
        # and the included line is gone, so the next brief shows its price
        self.assertFalse(desk.chat["_pm_seat_reads_included"](SUBIQ_USD, "naomi_inoshita"))

    def test_a_top_up_resets_the_notice(self):
        desk = Desk(_data(_allowance_company(balance=300.0)))
        desk.approve("naomi_inoshita")
        desk.approve("naomi_inoshita")
        self.assertEqual(len(self.sent), 1)
        W.apply_wallet_topup(desk.company(), 5000.0, description="Top up", stripe_ref="pi_test_1")
        started, _ = desk.approve("naomi_inoshita")
        self.assertTrue(started)
        self.assertEqual(len(self.sent), 1)
        # it runs out again: a new run-out, a new notice, once
        desk.company()["wallet_balance_usd"] = 120.0
        desk.approve("naomi_inoshita")
        desk.approve("naomi_inoshita")
        self.assertEqual(len(self.sent), 2)

    def test_the_run_that_uses_the_last_of_it_goes_through_and_mails_once(self):
        desk = Desk(_data(_allowance_company(balance=SUBIQ_USD)))
        started, _ = desk.approve("naomi_inoshita", what="Subscriber IQ: Outlander")
        self.assertTrue(started)
        self.assertEqual(W.wallet_balance(desk.company()), 0.0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Last run: Subscriber IQ: Outlander ($500)", self.sent[0][1])
        self.assertIn("That run went through.", self.sent[0][1])
        started, line = desk.approve("naomi_inoshita")
        self.assertFalse(started)
        self.assertEqual(line, W.ALLOWANCE_USED_LINE)
        self.assertEqual(len(self.sent), 1)

    def test_user_facing_lines_are_plain(self):
        for line in (W.ALLOWANCE_USED_LINE, W.ALLOWANCE_SHORT_LINE, W.SEAT_NOT_SET_UP_LINE, W.NO_COVER_LINE):
            self.assertIsNone(DASH_RX.search(line), line)
            self.assertIsNone(INTERNAL_RX.search(line), line)


class D_UnlimitedAndStaffUnchanged(_MailCase):
    def test_unlimited_seat(self):
        seat = _seat("unlim", company="", billing_source="user", credits=-1)
        desk = Desk({"users": {"unlim": seat}, "companies": {}})
        self.assertTrue(PR.seat_is_free(seat, "unlim", desk))
        self.assertEqual(desk.chat["_pm_price_sentence"]("journey_iq", "$500", "unlim"), "Included with your plan.")
        started, _ = desk.approve("unlim")
        self.assertTrue(started)
        self.assertEqual(seat.get("wallet_transactions") or [], [])
        self.assertEqual(seat["credits"], -1)
        self.assertEqual(self.sent, [])

    def test_unlimited_company_pool(self):
        co = {"paying_customer": True, "unlimited": True, "credits": -1, "credit_pool": -1,
              "credit_pool_used": 0, "billing_mode": "prepay_only", "wallet_balance_usd": 0.0}
        seat = _seat("amzn", company="Amazon")
        desk = Desk({"users": {"amzn": seat}, "companies": {"Amazon": co}})
        self.assertTrue(W.is_unlimited(co))
        self.assertTrue(PR.seat_is_free(seat, "amzn", desk))
        started, _ = desk.approve("amzn")
        self.assertTrue(started)
        self.assertEqual(co.get("wallet_transactions") or [], [])
        self.assertEqual(co["wallet_balance_usd"], 0.0)
        self.assertEqual(self.sent, [])

    def test_a_company_flagged_unlimited_covers_its_seats_and_charges_nothing(self):
        # the preflight says an unlimited billing subject covers the pull; the charge door agrees
        co = {"paying_customer": True, "unlimited": True, "credit_pool": 0, "credit_pool_used": 0,
              "billing_mode": "auto_reload", "wallet_balance_usd": 0.0, "default_spend_scope": "*"}
        seat = _seat("bgi", company="Be Good")
        desk = Desk({"users": {"bgi": seat}, "companies": {"Be Good": co}})
        started, line = desk.approve("bgi")
        self.assertTrue(started, line)
        self.assertEqual(co["wallet_balance_usd"], 0.0)
        self.assertEqual(co.get("wallet_transactions") or [], [])
        self.assertEqual(len(seat.get("credit_usage_history") or []), 1)
        self.assertEqual(self.sent, [])

    def test_staff_seat(self):
        seat = _seat("jenna", company="", billing_source="user", role="super_admin",
                     email="jenna@crosswalknyc.com")
        desk = Desk({"users": {"jenna": seat}, "companies": {}})
        self.assertTrue(PR.seat_is_free(seat, "jenna", desk))
        self.assertTrue(PR.included_with_plan(seat, "jenna", desk))
        self.assertEqual(self.sent, [])


class E_PayPerUse(_MailCase):
    def _desk(self, balance):
        co = {"paying_customer": True, "unlimited": False, "credit_pool": 0, "credit_pool_used": 0,
              "billing_mode": "prepay_only", "default_spend_scope": "*",
              "wallet_balance_usd": float(balance), "wallet_transactions": []}
        seat = _seat("bkusin", company="Kartel")
        return Desk({"users": {"bkusin": seat}, "companies": {"Kartel": co}})

    def test_short_wallet_reads_its_real_price(self):
        desk = self._desk(120.0)
        started, line = desk.approve("bkusin")
        self.assertFalse(started)
        self.assertEqual(line, "This run costs $500. Wallet balance is $120. Top up to keep going.")
        self.assertEqual(desk.last_refusal["wallet_balance_usd"], 120.0)
        self.assertNotIn("$0", line.replace("$500", "").replace("$120", ""))
        self.assertFalse(desk.chat["_pm_seat_reads_included"](SUBIQ_USD, "bkusin"))
        self.assertEqual(desk.chat["_pm_price_sentence"]("subscriber_iq_build", "$500", "bkusin"),
                         "It prices at $500.")
        self.assertEqual(self.sent, [])

    def test_a_run_with_no_price_never_reads_costs_zero(self):
        desk = self._desk(120.0)
        line, v = desk.app["_funds_refusal_copy"]("bkusin", "Ticket Sales")
        self.assertFalse(v.get("covered"))
        self.assertEqual(line, W.NO_COVER_LINE)
        self.assertNotIn("$", line)
        self.assertNotIn("costs", line)

    def test_an_unpriced_product_keeps_the_old_preflight_on_a_wallet(self):
        # before the routes named their product the preflight asked for a Profile build's worth; unchanged
        self.assertTrue(self._desk(4100.0).app["has_credits_for"]("bkusin", 3, pull_type="Talent Fit Assessment"))
        self.assertFalse(self._desk(120.0).app["has_credits_for"]("bkusin", 3, pull_type="Talent Fit Assessment"))
        self.assertEqual(self.sent, [])

    def test_funded_wallet_still_pays_and_runs(self):
        desk = self._desk(4100.0)
        started, _ = desk.approve("bkusin")
        self.assertTrue(started)
        self.assertEqual(desk.data["companies"]["Kartel"]["wallet_balance_usd"], 4100.0 - SUBIQ_USD)


class F_ProbeNeverMails(_MailCase):
    def test_canary_identity(self):
        probe = _seat("canary:plan", email="canary@crosswalknyc.com")
        desk = Desk(_data(_allowance_company(balance=0.0), [probe]))
        started, _ = desk.approve("canary:plan")
        self.assertFalse(started)
        self.assertFalse(desk.app["consume_credit"]("canary:plan", description="x", pull_type=SUBIQ, credits_used=10))
        self.assertFalse(W.note_allowance_shortfall(probe, desk.data, None, username="canary:plan", ran=True))
        self.assertEqual(self.sent, [])
        # the sender itself refuses a probe too
        self.assertFalse(self._real(COMPANY, 11000, "canary:plan",
                                    send=lambda *a: self.sent.append(a)))
        self.assertEqual(self.sent, [])
        self.assertEqual(desk.company()["wallet_balance_usd"], 0.0)


def _thread():
    """Naomi's thread, as stored, down to her question."""
    refusal = {"role": "agent",
               "text": "This run costs $0. Wallet balance is $11,000. Top up to keep going.",
               "meta": {"options": [{"label": "Add funds", "chipAction": "buy_credits", "url": "/wallet"}]}}
    return [
        {"role": "user", "text": "just the tracker"},
        {"role": "agent", "text": "Just the Subscriber IQ - $500. Review the brief below and approve to start the build."},
        dict(refusal), dict(refusal),
    ]


class G_IsItRunning(unittest.TestCase):
    def test_her_two_questions_are_status_asks(self):
        self.assertTrue(SL.wants_status("is it running?"))
        self.assertTrue(SL.wants_status("why is it not running when i hit approve and run"))
        self.assertTrue(SL.wants_status_fast("is it running?"))

    def test_a_refused_approve_says_it_has_not_started(self):
        hist = _thread()
        self.assertEqual(SL.pending_approval(hist)["state"], "refused")
        desk = Desk(_data())
        out = SL.answer("is it running?", "naomi_inoshita", host=desk,
                        user=desk.data["users"]["naomi_inoshita"], history=hist)
        self.assertTrue(out and out.get("not_started"))
        reply = out["reply"]
        self.assertTrue(reply.startswith("It has not started."))
        self.assertIn("hit Approve & run again and it starts", reply)
        self.assertNotIn("anything running or delivered", reply)
        self.assertNotIn("$", reply)
        # asked again after our own answer: the same answer, not the canned one
        hist2 = hist + [{"role": "user", "text": "is it running?"}, {"role": "agent", "text": reply},
                        {"role": "user", "text": "why is it not running when i hit approve and run"}]
        out2 = SL.answer("why is it not running when i hit approve and run", "naomi_inoshita", host=desk,
                         user=desk.data["users"]["naomi_inoshita"], history=hist2)
        self.assertTrue(out2 and out2["reply"].startswith("It has not started."))

    def test_when_the_allowance_is_used_up_it_says_so(self):
        desk = Desk(_data(_allowance_company(balance=0.0)))
        out = SL.answer("is it running?", "naomi_inoshita", host=desk,
                        user=desk.data["users"]["naomi_inoshita"], history=_thread())
        self.assertEqual(out["reply"], "It has not started, and nothing was charged. " + W.ALLOWANCE_USED_LINE)

    def test_a_brief_still_waiting_on_approve(self):
        hist = _thread()[:2]
        self.assertEqual(SL.pending_approval(hist)["state"], "waiting")
        desk = Desk(_data())
        out = SL.answer("is it running?", "naomi_inoshita", host=desk,
                        user=desk.data["users"]["naomi_inoshita"], history=hist)
        self.assertIn("It has not started yet.", out["reply"])
        self.assertIn("hit Approve & run on the card", out["reply"])

    def test_a_run_started_since_the_refusal_is_the_answer(self):
        # the run was started another way after the refused Approve: never "hit Approve again"
        hist = _thread()
        for t in hist:
            t["ts"] = "2026-11-18T00:56:49.169Z"
        desk = Desk(_data())
        desk.user_runs = lambda user, limit=12: [{
            "run_id": "r1", "status": "running", "queued_at": "2026-11-18T01:29:07Z",
            "subject": "Outlander: Blood of My Blood Viewers on Starz"}]
        desk.has = lambda name: name in ("load_users", "check_user_credits", "consume_credit", "user_runs")
        out = SL.answer("is it running?", "naomi_inoshita", host=desk,
                        user=desk.data["users"]["naomi_inoshita"], history=hist)
        self.assertTrue(out)
        self.assertFalse(out.get("not_started"))
        self.assertIn("Outlander: Blood of My Blood Viewers on Starz", out["reply"])
        self.assertNotIn("Approve & run again", out["reply"])
        # an older run does not stand in for the refused one
        desk.user_runs = lambda user, limit=12: [{
            "run_id": "r0", "status": "complete", "queued_at": "2026-11-10T01:29:07Z",
            "completed_at": "2026-11-10T02:00:00Z", "subject": "Something older"}]
        out = SL.answer("is it running?", "naomi_inoshita", host=desk,
                        user=desk.data["users"]["naomi_inoshita"], history=hist)
        self.assertTrue(out.get("not_started"))

    def test_a_thread_with_no_open_approval_is_unchanged(self):
        hist = [{"role": "user", "text": "who watches Outlander"},
                {"role": "agent", "text": "Outlander viewers skew female and 35 to 54."}]
        self.assertIsNone(SL.pending_approval(hist))
        self.assertIsNone(SL.pending_approval([]))


if __name__ == "__main__":
    unittest.main()
