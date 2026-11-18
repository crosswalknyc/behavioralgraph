#!/usr/bin/env python3
"""What a build is quoted at is what its approval takes (Jenna 2026-11-17).

A Subscriber IQ ordered with the Profile IQ add-on read "Total: $800
($500 Subscriber IQ + $300 Profile IQ)" in the chat and "+$300" on the
add-on question, and the wallet was charged $500. Jenna was asked which
side should move and chose: "Quote what is charged ($500)". The charge
is the fixed point; every quote reads it.

Hermetic: no network, no mail, no model, nothing charged. The
dashboard's own charge functions and price helpers are lifted out of
app.py by name and run against a users document held in memory, so what
is tested is the code that ships, without booting the dashboard.

A. The debit is byte-for-byte what it was: every product combination on
   every payment path takes the pinned amount (recorded on main before
   this change), the pull names and tool keys are the pinned ones, and
   the factored arithmetic equals the old sum.
B. Every quote equals the debit, for each combination and each paying
   path: the chat's figure, the brief card's table (Python and the
   widget's own JavaScript), the ask log line, the question copy's
   "Cost on approval", the brief the Prometheus app stores, and the
   partner API's price.
C. The Subscriber IQ with the Profile IQ add-on reads $500 where the
   wallet draws $500, on the step line, the question and the chip.
D. A seat whose plan includes the run reads no price; the amount metered
   against the allowance is the same debit.
E. The quote and the charge are one computation: the approve step, the
   partner API and the stored brief name the pull and count the cuts
   through the functions the quote reads.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import re
import shutil
import subprocess
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
import prometheus_watch_notify as WN  # noqa: E402
from prometheus import ask_outcome as AO  # noqa: E402
from prometheus import order_quote as Q  # noqa: E402
from prometheus import pending_briefs as PB  # noqa: E402
from prometheus import plan_allowance as PLAN  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402
from prometheus.host import host  # noqa: E402

# the pricing table is read from memory: nothing reaches for the app or the network
PRICING = copy.deepcopy(W.DEFAULT_PRICING)
W._pricing_cache["value"] = PRICING
W._pricing_cache["loaded_at"] = W._now_ts() + 10 ** 9
W.try_auto_reload = lambda *a, **k: {}            # a card on file is never charged from a test
N.send_runout = lambda *a, **k: None              # and nobody is mailed

APP_SRC = (WEB / "app.py").read_text(encoding="utf-8")
CHAT_SRC = (WEB / "prometheus" / "legacy" / "chat.py").read_text(encoding="utf-8")
INDEX_SRC = (WEB / "templates" / "index.html").read_text(encoding="utf-8")
PB_SRC = (WEB / "prometheus" / "pending_briefs.py").read_text(encoding="utf-8")
DASH_RX = re.compile("[\u2014\u2013]")
INTERNAL_RX = re.compile(
    r"\b(synth\w*|modeled|estimated|pipeline|queue|worker|hostmap|wallet|credits?|claude|anthropic|opus|sonnet)\b", re.I)


def _lift(src, names, assigns=()):
    """The named top-level functions and assignments of a source file."""
    tree = ast.parse(src)
    found, out = set(), []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            out.append(ast.get_source_segment(src, node))
            found.add(node.name)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in assigns for t in node.targets):
            out.append(ast.get_source_segment(src, node))
            found.update(t.id for t in node.targets if isinstance(t, ast.Name))
    missing = [n for n in tuple(names) + tuple(assigns) if n not in found]
    assert not missing, f"not found: {missing}"
    return "\n\n\n".join(out)


APP_CODE = compile(_lift(
    APP_SRC,
    ("_get_company_pool", "_numeric_credits_balance", "_credit_num", "check_user_credits", "_usd_label",
     "has_credits_for", "_try_wallet_fallback", "_credit_history_ts", "_record_wallet_fallback_usage",
     "consume_credit", "_v1_tool_price_usd", "_v1_price_usd_for", "_v1_price_table_for"),
    ("_V1_USD_FALLBACK", "_V1_USD_ADDON_CUT_FALLBACK", "CREDITS_PROFILE_ANALYSIS", "CREDITS_SVOD",
     "ADDON_CUT_CREDITS", "_V1_CREDITS")), "app.py (lifted)", "exec")


# ---------------------------------------------------------------- the seats, one per payment path
def _company(**over):
    rec = {"paying_customer": True, "credit_pool": 0, "credit_pool_used": 0, "billing_mode": "prepay_only",
           "default_spend_scope": "*", "wallet_balance_usd": 50000.0, "wallet_transactions": []}
    rec.update(over)
    return rec


def _seat(username, **over):
    rec = {"username": username, "email": username + "@example.com", "role": "user", "credits": 0,
           "credit_ceiling": -1, "unlimited": False, "no_cap": False, "paying_customer": True,
           "prometheus_mode": "both"}
    rec.update(over)
    return rec


def _data():
    return {"companies": {
        "Acme": _company(),
        "Kartel": _company(profile_pull_usd=275.0, tool_price_overrides={"brand_partnership_iq": 925.0}),
        "Sony": _company(included_while_funded=True, monthly_allowance_usd=11000, wallet_balance_usd=11000.0,
                         wallet_transactions=[{"ts": "2026-11-18T00:32:53Z", "kind": "adjustment", "amount_usd": 11000.0,
                                               "balance_after_usd": 11000.0,
                                               "description": "Monthly included allowance $11,000"}]),
    }, "users": {s["username"]: s for s in (
        _seat("co", company="Acme", billing_source="company"),                    # a company wallet
        _seat("rate", company="Kartel", billing_source="company"),                # a company wallet on its own Profile rate
        _seat("plan", company="Sony", billing_source="company"),                  # a plan allowance, metered
        _seat("self", wallet_balance_usd=50000.0, billing_mode="prepay_only", wallet_transactions=[]),   # a personal wallet
        _seat("card", wallet_balance_usd=50000.0, billing_mode="auto_reload", stripe_customer_id="cus_x",
              stripe_payment_method_id="pm_x", wallet_transactions=[]),           # a card on file tops the wallet up
        _seat("invoice", wallet_balance_usd=0.0, billing_mode="monthly_invoice", monthly_invoice_limit_usd=50000.0,
              wallet_transactions=[]),                                            # invoiced monthly
        _seat("unl", unlimited=True, paying_customer=False),                      # never charged
        _seat("cred", credits=495, paying_customer=False),                        # still holds the old credits
    )}}


DOLLAR_PATHS = ("co", "rate", "self", "card", "invoice")
ALL_PATHS = DOLLAR_PATHS + ("plan", "unl", "cred")


class Desk:
    """The dashboard's charge functions and price helpers over a users document in memory."""

    def __init__(self, data=None):
        self.data = data if data is not None else _data()
        ns = {"load_users": lambda: self.data, "_users_cas_mutate": lambda fn: fn(self.data),
              "_stamp_paid_profile_after_consume": lambda *a, **k: None,
              "_session_money_symbol": lambda username=None: "$", "session": {},
              "traceback": traceback, "datetime": datetime, "timezone": timezone}
        exec(APP_CODE, ns)
        self.app = ns

    def load_users(self):
        return self.data

    def balance(self, username):
        rec = self.data["users"][username]
        if rec.get("billing_source") == "company":
            return float(self.data["companies"][rec["company"]]["wallet_balance_usd"])
        return float(rec.get("wallet_balance_usd") or 0)

    def credits_for(self, decision, cuts, addon_profile=False, d_type=""):
        """The credits the approve step names, as it adds them up."""
        c = self.app["_V1_CREDITS"]
        if d_type == "addon_cuts" and cuts:
            price = self.app["ADDON_CUT_CREDITS"] * cuts
        else:
            price = c.get(decision, self.app["CREDITS_PROFILE_ANALYSIS"]) + self.app["ADDON_CUT_CREDITS"] * cuts
        if decision == "subscriber_iq" and addon_profile:
            price += self.app["CREDITS_PROFILE_ANALYSIS"]
        return price

    def approve(self, username, decision, cuts=0, addon_profile=False, v1=False, d_type=""):
        """The approve step's own preflight and charge, in its order.
        (started, dollars the wallet moved, credits the seat moved)."""
        pull = W.chat_pull_type(decision, v1=v1)
        credits = self.credits_for(decision, cuts, addon_profile and not v1, d_type)
        b0, c0 = self.balance(username), float(self.data["users"][username].get("credits") or 0)
        ok = self.app["has_credits_for"](username, credits, pull_type=pull, addon_cuts=cuts) and \
            self.app["consume_credit"](username, description="run", job_id="r1", pull_type=pull,
                                       credits_used=credits, addon_cuts=cuts)
        return bool(ok), round(b0 - self.balance(username), 2), c0 - float(self.data["users"][username].get("credits") or 0)


def _draft(decision, cuts=0, addon_profile=None, table=None, **over):
    d = {"decision": decision, "subject": "Outlander", "addon_cuts": [{"cut_id": f"c{i}", "label": f"cut {i}"} for i in range(cuts)]}
    if decision == "subscriber_iq":
        d["subiq"] = {"title": "Outlander", "addon_profile": bool(addon_profile)}
    if table is not None:
        d["price_table"] = table
    d.update(over)
    return d


def _money(v):
    return f"${v:,.0f}" if abs(v - round(v)) < 0.009 else f"${v:,.2f}"


# (name, decision, billable cuts, the Profile IQ add-on, derive type)
COMBOS = (
    ("Subscriber IQ alone", "subscriber_iq", 0, False, ""),
    ("Subscriber IQ with the Profile IQ add-on", "subscriber_iq", 0, True, ""),
    ("Profile with Avid", "new_build", 0, False, ""),
    ("Profile with one add-on cut", "new_build", 1, False, ""),
    ("Profile with three add-on cuts", "new_build", 3, False, ""),
    ("Refresh", "time_shifted_refresh", 0, False, ""),
    ("Refresh with two cuts", "time_shifted_refresh", 2, False, ""),
    ("One year of a year package", "new_build", 0, False, ""),
    ("One cut of a profile we hold", "derive_cut", 1, False, "addon_cuts"),
    ("Three cuts of a profile we hold", "derive_cut", 3, False, "addon_cuts"),
    ("A custom cut of a profile we hold", "derive_cut", 0, False, "intersect_cut"),
    ("Profile and its listed cut, no parent yet", "cut_needs_parent", 1, False, ""),
    ("Profile and a custom cut, no parent yet", "cut_needs_parent", 0, False, ""),
)

# What each combination debits, recorded on main (a96a2704) before this change by running the same lifted charge
# functions there: dollars from a wallet at the published price, dollars at the Kartel rate ($275 Profile), and
# credits from a seat that still holds them. A change to any of these is a change to what is charged.
PINNED_USD = {
    "Subscriber IQ alone": 500.0, "Subscriber IQ with the Profile IQ add-on": 500.0, "Profile with Avid": 300.0,
    "Profile with one add-on cut": 400.0, "Profile with three add-on cuts": 600.0, "Refresh": 300.0,
    "Refresh with two cuts": 500.0, "One year of a year package": 300.0, "One cut of a profile we hold": 100.0,
    "Three cuts of a profile we hold": 300.0, "A custom cut of a profile we hold": 100.0,
    "Profile and its listed cut, no parent yet": 400.0, "Profile and a custom cut, no parent yet": 300.0,
}
PINNED_USD_RATE = dict(PINNED_USD, **{
    "Profile with Avid": 275.0, "Profile with one add-on cut": 375.0, "Profile with three add-on cuts": 575.0,
    "Refresh": 275.0, "Refresh with two cuts": 475.0, "One year of a year package": 275.0,
    "Profile and its listed cut, no parent yet": 375.0, "Profile and a custom cut, no parent yet": 275.0})
PINNED_CREDITS = {
    "Subscriber IQ alone": 10, "Subscriber IQ with the Profile IQ add-on": 15, "Profile with Avid": 5,
    "Profile with one add-on cut": 8, "Profile with three add-on cuts": 14, "Refresh": 5, "Refresh with two cuts": 11,
    "One year of a year package": 5, "One cut of a profile we hold": 3, "Three cuts of a profile we hold": 9,
    "A custom cut of a profile we hold": 3, "Profile and its listed cut, no parent yet": 11,
    "Profile and a custom cut, no parent yet": 8,
}


def _pinned(name, path):
    return (PINNED_USD_RATE if path == "rate" else PINNED_USD)[name]


class A_TheDebitIsUnchanged(unittest.TestCase):
    def test_every_combination_debits_the_pinned_amount_on_every_path(self):
        for name, decision, cuts, addon, d_type in COMBOS:
            for path in ALL_PATHS:
                for v1 in ((False,) if addon else (False, True)):
                    with self.subTest(combo=name, path=path, api=v1):
                        ok, usd, credits = Desk().approve(path, decision, cuts, addon, v1=v1, d_type=d_type)
                        self.assertTrue(ok)
                        if path == "unl":
                            self.assertEqual((usd, credits), (0.0, 0.0))      # an unlimited seat is never charged
                        elif path == "cred":
                            self.assertEqual(usd, 0.0)                        # credits, not dollars
                            want = PINNED_CREDITS[name] - (5 if (addon and v1) else 0)
                            self.assertEqual(credits, want)
                        else:
                            self.assertEqual(usd, _pinned(name, path))        # includes the plan allowance's metering
                            self.assertEqual(credits, 0.0)

    def test_the_addon_adds_nothing_to_a_dollar_debit_and_five_credits_to_a_credit_one(self):
        for path in DOLLAR_PATHS + ("plan",):
            alone = Desk().approve(path, "subscriber_iq", 0, False)[1]
            both = Desk().approve(path, "subscriber_iq", 0, True)[1]
            self.assertEqual((alone, both), (500.0, 500.0))
        self.assertEqual(Desk().approve("cred", "subscriber_iq", 0, False)[2], 10)
        self.assertEqual(Desk().approve("cred", "subscriber_iq", 0, True)[2], 15)
        # the approve step still adds the Profile's credits, and only credits
        self.assertIn("price += _H.CREDITS_PROFILE_ANALYSIS", CHAT_SRC)

    def test_the_factored_sum_is_the_old_sum(self):
        data = _data()
        pricing = W.load_pricing()
        subjects = [data["companies"]["Acme"], data["companies"]["Kartel"], data["companies"]["Sony"],
                    data["users"]["self"], data["users"]["card"]]
        keys = ("api_chatbot_profile_iq_build", "profile_iq_build", "api_profile_iq_cut", "profile_iq_derived_cut",
                "api_subscriber_iq_build", "subscriber_iq_build", "journey_iq", "viewership_read", "not_a_tool")
        for subject in subjects:
            for tk in keys:
                for cuts in (0, 1, 2, 7):
                    for qty in (1, 3):
                        base = W.subject_tool_price_usd(subject, tk, pricing)
                        old = (0.0, "no_charge") if base <= 0 else \
                            (round(base * qty + W.addon_cuts_usd(subject, tk, cuts, pricing), 2), "wallet")
                        self.assertEqual(W.should_charge_wallet(subject, tk, addon_cuts=cuts, quantity=qty), old)
                        self.assertEqual(W.pull_price_usd(subject, tk, cuts, qty), old[0])
        # who is charged at all is still the charge's own decision
        self.assertEqual(W.should_charge_wallet(data["users"]["unl"], "api_subscriber_iq_build"), (0.0, "not_paying"))
        self.assertEqual(W.should_charge_wallet(dict(data["users"]["self"], unlimited=True), "api_subscriber_iq_build"),
                         (0.0, "unlimited"))
        self.assertEqual(W.should_charge_wallet(None, "api_subscriber_iq_build"), (0.0, "not_paying"))

    def test_the_pull_names_and_tool_keys_are_the_pinned_ones(self):
        want = {
            ("new_build", False): ("Chatbot Profile IQ (new_build)", "api_chatbot_profile_iq_build"),
            ("time_shifted_refresh", False): ("Chatbot Profile IQ (time_shifted_refresh)", "api_chatbot_profile_iq_build"),
            ("cut_needs_parent", False): ("Chatbot Profile IQ (cut_needs_parent)", "api_chatbot_profile_iq_build"),
            ("existing_match", False): ("Chatbot Profile IQ (existing_match)", "api_chatbot_profile_iq_build"),
            ("derive_cut", False): ("Chatbot Profile IQ (derive_cut)", "profile_iq_derived_cut"),
            ("subscriber_iq", False): ("Chatbot Profile IQ (subscriber_iq)", "api_subscriber_iq_build"),
            ("new_build", True): ("Chatbot Profile IQ v1 (new_build)", "api_chatbot_profile_iq_build"),
            ("derive_cut", True): ("Chatbot Profile IQ v1 (derive_cut)", "api_profile_iq_cut"),
            ("subscriber_iq", True): ("Chatbot Profile IQ v1 (subscriber_iq)", "api_subscriber_iq_build"),
        }
        for (decision, v1), (pull, key) in want.items():
            self.assertEqual(W.chat_pull_type(decision, v1=v1), pull)
            self.assertEqual(W.pull_type_to_tool_key(pull), key)

    def test_billable_cuts_is_the_approve_steps_old_filter(self):
        cases = [None, {}, {"addon_cuts": None}, {"addon_cuts": []}, {"addon_cuts": "abc"}, {"addon_cuts": {"a": 1}},
                 {"addon_cuts": [{"cut_id": "f"}, {"label": "no id"}, "x", None, {"cut_id": ""}, {"cut_id": "m"}]}]
        for d in cases:
            src = (d or {}).get("addon_cuts") or []
            old = [c for c in src if isinstance(c, dict) and c.get("cut_id")] if isinstance(src, (list, tuple, dict, str)) else []
            self.assertEqual(W.billable_cuts(d), old if isinstance(src, list) else [])
        self.assertEqual(len(W.billable_cuts(cases[-1])), 2)


# ---------------------------------------------------------------- every quote, for one seat and one order
_NODE = shutil.which("node")


def _extract_js(marker):
    start = INDEX_SRC.index(marker)
    end = INDEX_SRC.index("\n        }", start)
    return INDEX_SRC[start:end + len("\n        }")]


_JS = "\n".join(_extract_js(m) for m in (
    "function _synthChatUsd(v) {", "function _synthChatPriceUsd(pt, decision, nCuts) {",
    "function _synthChatBillableCuts(d) {", "function _synthChatSubiqAddonUsd(d) {")) if _NODE else ""


def _node(expr_js):
    """Run the widget's own price functions in node; the JSON it prints."""
    script = "var synthChatCurrentDraft = null;\n" + _JS + "\nconsole.log(JSON.stringify(" + expr_js + "));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
        tf.write(script)
        tmp = tf.name
    try:
        res = subprocess.run([_NODE, tmp], capture_output=True, text=True, timeout=30)
        assert res.returncode == 0, res.stderr[:400]
        return json.loads(res.stdout.strip())
    finally:
        os.unlink(tmp)


def _quotes(desk, username, decision, cuts, addon=False):
    """{surface: dollars} for every place this seat is quoted this order."""
    out = {}
    table = desk.app["_v1_price_table_for"](username)             # what the server stamps on the draft
    draft = _draft(decision, cuts, addon, table=table)
    out["the chat's figure"] = Q.for_draft(desk, username, draft)
    out["the brief card's table"] = Q.draft_usd(draft)
    out["the ask log line"] = AO.draft_price_usd(draft)
    m = re.search(r"\$([\d,]+(?:\.\d\d)?)\)$", AO.reply_text({"spec_draft": draft}))
    out["the ask log text"] = float(m.group(1).replace(",", "")) if m else None
    m = re.search(r"Cost on approval: \$([\d,]+(?:\.\d\d)?)", WN.answer_text({"spec_draft": draft}))
    out["the question copy's cost on approval"] = float(m.group(1).replace(",", "")) if m else None
    out["the brief the app stores"] = PB.quote(username, desk.data["users"][username], draft, decision)["price_usd"]
    return out


class B_EveryQuoteIsTheDebit(unittest.TestCase):
    def setUp(self):
        self.desk = Desk()
        host.bind(load_users=lambda: self.desk.data,
                  price_table_for=lambda u=None: self.desk.app["_v1_price_table_for"](u))

    def test_each_combination_on_each_paying_path(self):
        for name, decision, cuts, addon, d_type in COMBOS:
            for path in DOLLAR_PATHS:
                with self.subTest(combo=name, path=path):
                    debit = Desk().approve(path, decision, cuts, addon, d_type=d_type)[1]
                    self.assertEqual(debit, _pinned(name, path))
                    for surface, usd in _quotes(self.desk, path, decision, cuts, addon).items():
                        self.assertEqual(usd, debit, f"{surface} quotes {usd}, the wallet takes {debit}")

    def test_the_partner_api_quotes_what_its_key_is_charged(self):
        for name, decision, cuts, addon, d_type in COMBOS:
            if addon:
                continue                 # the API has no add-on
            for path in DOLLAR_PATHS:
                with self.subTest(combo=name, path=path):
                    debit = Desk().approve(path, decision, cuts, v1=True, d_type=d_type)[1]
                    self.assertEqual(debit, _pinned(name, path))
                    self.assertEqual(self.desk.app["_v1_price_usd_for"](decision, cuts, username=path), debit)
        # reusing a profile that exists stays $0 on the tier line
        self.assertEqual(self.desk.app["_v1_price_usd_for"]("existing_match", 0, username="co"), 0.0)
        # the two quotes that were not the charge before this change: a three-cut derive read $100 and took
        # $300; a profile with a custom cut and no parent read $400 and took $300
        self.assertEqual(self.desk.app["_v1_price_usd_for"]("derive_cut", 3, username="co"), 300.0)
        self.assertEqual(self.desk.app["_v1_price_usd_for"]("cut_needs_parent", 0, username="co"), 300.0)
        self.assertEqual(self.desk.app["_v1_price_usd_for"]("cut_needs_parent", 1, username="co"), 400.0)

    def test_the_table_is_the_charge_row_by_row(self):
        for path in DOLLAR_PATHS:
            table = self.desk.app["_v1_price_table_for"](path)
            self.assertEqual(sorted(table["by_cuts"]), sorted(d for d in Q.DECISIONS if d != "existing_match"))
            for decision, row in table["by_cuts"].items():
                self.assertEqual(len(row), Q.GRID + 1)
                for cuts in (0, 1, 2, 5, Q.GRID):
                    debit = Desk().approve(path, decision, cuts,
                                           d_type="addon_cuts" if decision == "derive_cut" and cuts else "")[1]
                    self.assertEqual(row[cuts], debit, (path, decision, cuts))
                    self.assertEqual(Q.from_table(table, decision, cuts), debit)
            # past the last row the charge is one more cut each time
            debit = Desk().approve(path, "new_build", Q.GRID + 4)[1]
            self.assertEqual(Q.from_table(table, "new_build", Q.GRID + 4), debit)
            self.assertEqual(table["existing_match"], 0.0)
            self.assertEqual(table["subiq_profile_addon"], 0.0)
            self.assertEqual(Q.from_table(table, "existing_match", 3), 0.0)

    def test_a_table_from_before_the_rows_is_still_read_the_old_way(self):
        old = {"new_build": 300.0, "time_shifted_refresh": 300.0, "derive_cut": 100.0, "cut_needs_parent": 400.0,
               "subscriber_iq": 500.0, "existing_match": 0.0, "addon_cut": 100.0}
        self.assertEqual(Q.from_table(old, "new_build", 2), 500.0)
        self.assertEqual(Q.from_table(old, "derive_cut", 3), 300.0)
        self.assertEqual(Q.from_table(old, "cut_needs_parent", 2), 500.0)
        self.assertEqual(AO.draft_price_usd({"decision": "subscriber_iq", "price_table": old}), 500.0)
        self.assertIsNone(AO.draft_price_usd({"decision": "new_build"}))
        self.assertIsNone(Q.from_table(None, "new_build", 0))

    @unittest.skipUnless(_NODE, "node is not on PATH")
    def test_the_widgets_own_card_reads_the_same_rows(self):
        for path in ("co", "rate"):
            table = self.desk.app["_v1_price_table_for"](path)
            asks = [(d, n) for d in table["by_cuts"] for n in (0, 1, 2, 3, Q.GRID, Q.GRID + 3)] + [("existing_match", 2)]
            got = _node("(function(){var pt=" + json.dumps(table) + ";return " + json.dumps(asks)
                        + ".map(function(a){return _synthChatPriceUsd(pt,a[0],a[1]);});})()")
            for (decision, cuts), usd in zip(asks, got):
                self.assertEqual(usd, Q.from_table(table, decision, cuts), (path, decision, cuts))
                if decision != "existing_match":
                    debit = Desk().approve(path, decision, cuts,
                                           d_type="addon_cuts" if decision == "derive_cut" and cuts else "")[1]
                    self.assertEqual(usd, debit, (path, decision, cuts))
        # a card that still holds a table from before the rows adds up the way it did
        old = {"new_build": 300.0, "time_shifted_refresh": 300.0, "derive_cut": 100.0, "cut_needs_parent": 400.0,
               "subscriber_iq": 500.0, "existing_match": 0.0, "addon_cut": 100.0}
        got = _node("(function(){var pt=" + json.dumps(old) + ";return [_synthChatPriceUsd(pt,'new_build',2),"
                    "_synthChatPriceUsd(pt,'cut_needs_parent',1),_synthChatPriceUsd(pt,'derive_cut',3)];})()")
        self.assertEqual(got, [500, 400, 300])
        # only the cuts an approval charges for are counted
        got = _node("_synthChatBillableCuts({addon_cuts:[{cut_id:'f'},{label:'x'},null,{cut_id:'m'}]})")
        self.assertEqual(got, 2)


class C_TheSubscriberIqWithTheProfileAddOnReadsFiveHundred(unittest.TestCase):
    def setUp(self):
        self.desk = Desk()

    def test_the_step_lines_say_what_the_wallet_takes(self):
        for path in DOLLAR_PATHS:
            sq = Q.subiq(self.desk, _draft("subscriber_iq"), "Outlander", _money, username=path)
            debit = Desk().approve(path, "subscriber_iq", 0, True)[1]
            self.assertEqual(debit, 500.0)
            self.assertEqual((sq["total"], sq["alone"], sq["extra"]), (500.0, 500.0, 0.0))
            self.assertIn("Total: $500 for both.", sq["added"])
            self.assertIn("The price stays at $500 with it added.", sq["ask"])
            self.assertTrue(sq["just"].startswith("Just the Subscriber IQ - $500."))
            for line in (sq["ask"], sq["added"], sq["just"]):
                self.assertNotIn("$800", line)
                self.assertNotIn("$300", line)
                self.assertNotIn("$275", line)
                self.assertNotIn("+$", line)
                self.assertIsNone(DASH_RX.search(line))
                self.assertIsNone(INTERNAL_RX.search(line), line)
            self.assertTrue(sq["ask"].endswith("Reply add it or just the tracker."))
            self.assertTrue(sq["added"].endswith("Review the brief below and approve to start the build."))

    def test_the_chat_reads_those_lines_and_keeps_no_sum_of_its_own(self):
        self.assertIn("_sq = _oq.subiq(_H, draft, label, _pm_usd_label)", CHAT_SRC)
        self.assertIn("_plan.SUBIQ_ADDED if _pm_seat_reads_included(_sq['total']) else _sq['added']", CHAT_SRC)
        self.assertIn("_plan.SUBIQ_JUST if _pm_seat_reads_included(_sq['alone']) else _sq['just']", CHAT_SRC)
        self.assertIn("_plan.subiq_ask(label) if _pm_seat_reads_included(_sq['total']) else _sq['ask']", CHAT_SRC)
        self.assertNotIn("_usd_sub + _usd_prof", CHAT_SRC)
        self.assertNotIn("_pm_usd('subscriber_iq_build'", CHAT_SRC)
        self.assertNotIn("_pm_usd('profile_iq_build'", CHAT_SRC)
        self.assertNotIn("_pm_usd('profile_iq_derived_cut'", CHAT_SRC)
        self.assertNotIn("(+{_H.ADDON_CUT_CREDITS})", CHAT_SRC)      # a cut's tag is its price, never a credit count

    def test_a_charge_that_did_add_for_the_profile_would_be_quoted_with_it(self):
        # the lines follow the charge: were the approval to take more with the add-on, the quote says how much
        real = Q._draft_price

        def _with_addon(user, data, draft, v1=False, pricing=None):
            extra = 300.0 if (draft.get("subiq") or {}).get("addon_profile") else 0.0
            return real(user, data, draft, v1=v1, pricing=pricing) + extra
        Q._draft_price = _with_addon
        try:
            sq = Q.subiq(self.desk, _draft("subscriber_iq"), "Outlander", _money, username="co")
            self.assertEqual((sq["total"], sq["alone"], sq["extra"]), (800.0, 500.0, 300.0))
            self.assertIn("(+$300)?", sq["ask"])
            self.assertIn("Total: $800 ($500 Subscriber IQ + $300 Profile IQ).", sq["added"])
            self.assertEqual(Q.table(self.desk.data["users"]["co"], self.desk.data)["subiq_profile_addon"], 300.0)
        finally:
            Q._draft_price = real

    def test_the_chip_and_the_question_in_the_widget(self):
        self.assertNotIn("'Add the Profile IQ (+' + _synthChatProfilePriceLabel(d) + ')'", INDEX_SRC)
        self.assertNotIn("' audience alongside it (+' + _synthChatProfilePriceLabel(_suQD)", INDEX_SRC)
        self.assertIn("_synthChatSubiqAddonUsd(d) > 0", INDEX_SRC)
        self.assertIn("'? The price stays at ' + _synthChatUsd(_synthChatPriceUsd(_suPt, 'subscriber_iq', _synthChatBillableCuts(_suQD))) + ' with it added.'",
                      INDEX_SRC)
        if _NODE:
            table = self.desk.app["_v1_price_table_for"]("co")
            got = _node("(function(){var d={price_table:" + json.dumps(table) + "};return [_synthChatSubiqAddonUsd(d),"
                        "_synthChatUsd(_synthChatPriceUsd(d.price_table,'subscriber_iq',0))];})()")
            self.assertEqual(got, [0, "$500"])

    def test_the_cut_lock_line_totals_what_the_wallet_takes(self):
        for path in DOLLAR_PATHS:
            for decision in ("new_build", "time_shifted_refresh", "cut_needs_parent"):
                for cuts in (1, 3):
                    lk = Q.lock(self.desk, _draft(decision, cuts), username=path)
                    debit = Desk().approve(path, decision, cuts)[1]
                    self.assertEqual(lk["total"], debit)
                    self.assertEqual(lk["n"], cuts)
                    self.assertEqual(round(lk["base"] + lk["each"] * lk["n"], 2), debit)
        # a dated quarter read is not charged by the approval, so it is not in the total
        d = _draft("new_build", 2, quarter_cuts=[{"label": "Q1 2026", "start": "2026-01-01", "end": "2026-03-31"}])
        lk = Q.lock(self.desk, d, username="co")
        self.assertEqual((lk["total"], lk["n"]), (500.0, 2))
        self.assertEqual(Desk().approve("co", "new_build", len(W.billable_cuts(d)))[1], 500.0)
        only_q = _draft("new_build", 0, quarter_cuts=d["quarter_cuts"])
        self.assertEqual(Q.lock(self.desk, only_q, username="co"), {"total": 300.0, "n": 0, "each": 0.0, "base": 300.0})
        self.assertEqual(PLAN.lock_cuts(False, 0, "$0"), "the quarter reads are ")
        self.assertEqual(PLAN.lock_cuts(False, 2, "$100"), "2 x $100 for the cuts, ")
        self.assertIn("_lq = _oq.lock(_H, draft)", CHAT_SRC)

    def test_one_more_cut_and_a_tracker_on_its_own(self):
        for path in DOLLAR_PATHS:
            self.assertEqual(Q.cut_each(self.desk, _draft("new_build"), username=path), 100.0)
            self.assertEqual(Q.cut_each(self.desk, _draft("derive_cut"), username=path), 100.0)
            self.assertEqual(Q.build_usd(self.desk, "subscriber_iq", username=path),
                             Desk().approve(path, "subscriber_iq")[1])
        self.assertEqual(Q.build_usd(self.desk, "new_build", username="rate"), 275.0)


class D_AnIncludedSeatReadsNoPrice(unittest.TestCase):
    def setUp(self):
        self.desk = Desk()
        host.bind(load_users=lambda: self.desk.data,
                  price_table_for=lambda u=None: self.desk.app["_v1_price_table_for"](u))

    def test_a_plan_allowance_seat_reads_included_and_meters_the_same_debit(self):
        user = self.desk.data["users"]["plan"]
        for name, decision, cuts, addon, d_type in COMBOS:
            with self.subTest(combo=name):
                amount = Q.for_draft(self.desk, "plan", _draft(decision, cuts, addon))
                self.assertTrue(PR.included_with_plan(user, "plan", self.desk, amount_usd=amount))
                self.assertTrue(PLAN.seat_reads_included(self.desk, None, amount, "plan"))
                stored = PB.quote("plan", user, _draft(decision, cuts, addon), decision)
                self.assertTrue(stored["included"])
                self.assertIsNone(stored["price_usd"])
                self.assertEqual(stored["label"], "")
                metered = Desk().approve("plan", decision, cuts, addon, d_type=d_type)[1]
                self.assertEqual(metered, PINNED_USD[name])
                self.assertEqual(amount, metered)             # the included check weighs the figure that is metered
        for line in (PLAN.SUBIQ_JUST, PLAN.SUBIQ_ADDED, PLAN.subiq_ask("Outlander")):
            self.assertIn("ncluded with your plan", line)
            self.assertNotIn("$", line)

    def test_an_unlimited_seat_reads_included_and_is_charged_nothing(self):
        user = self.desk.data["users"]["unl"]
        self.assertTrue(PR.included_with_plan(user, "unl", self.desk, amount_usd=500.0))
        self.assertEqual(Desk().approve("unl", "subscriber_iq", 0, True)[1:], (0.0, 0.0))

    def test_the_draft_line_prints_no_price_for_a_covered_seat(self):
        table = self.desk.app["_v1_price_table_for"]("plan")
        text = AO.reply_text({"spec_draft": _draft("subscriber_iq", 0, True, table=table), AO.INCLUDED_KEY: True})
        self.assertNotIn("$", text)
        self.assertIn("Included with your plan.", text)


class E_OneComputation(unittest.TestCase):
    def test_the_approve_step_names_its_pull_and_counts_its_cuts_through_the_wallet(self):
        self.assertIn("_billable_cuts = __import__('wallet').billable_cuts(draft)", CHAT_SRC)
        self.assertIn("_approve_pull_type = __import__('wallet').chat_pull_type(decision)", CHAT_SRC)
        self.assertIn("_em_pt = __import__('wallet').chat_pull_type(decision)", CHAT_SRC)
        self.assertIn("pull_type=_approve_pull_type,\n                    credits_used=price,\n"
                      "                    addon_cuts=len(_billable_cuts)):", CHAT_SRC)
        self.assertNotIn("f'Chatbot Profile IQ ({decision})'", CHAT_SRC)

    def test_the_partner_api_does_too(self):
        self.assertEqual(APP_SRC.count("__import__('wallet').chat_pull_type(decision, v1=True)"), 3)
        self.assertNotIn("f'Chatbot Profile IQ v1 ({decision})'", APP_SRC)
        body = _lift(APP_SRC, ("_v1_price_usd_for",))
        self.assertIn("_oq.price(user, data, d, cut_count, v1=v1)", body)
        self.assertIn("return 0.0", body)
        self.assertNotIn("cut_each", body)                     # no sum of its own
        self.assertIn("out = _oq.table(_pt_user, _pt_data, v1=False)", _lift(APP_SRC, ("_v1_price_table_for",)))

    def test_the_stored_brief_reads_the_same_pull(self):
        self.assertIn("_w.pull_funding(rec, data, _w.chat_pull_type(decision), addon_cuts=cuts)", PB_SRC)
        self.assertIn("return _w.billable_cuts(draft)", PB_SRC)
        self.assertNotIn("f'Chatbot Profile IQ ({decision})'", PB_SRC)

    def test_the_quote_is_the_wallets_own_sum(self):
        import inspect
        src = inspect.getsource(Q.price)
        self.assertIn("order_price_usd(", src)
        src = inspect.getsource(W.order_price_usd)
        self.assertIn("pull_type_to_tool_key(chat_pull_type(decision, v1))", src)
        self.assertIn("return pull_price_usd(", src)
        self.assertIn("usd = pull_price_usd(user, tool_key, addon_cuts=addon_cuts,", inspect.getsource(W.should_charge_wallet))
        # the ask log and the question copy keep no sum of their own either
        self.assertIn("_oq.draft_usd(d)", inspect.getsource(AO.draft_price_usd))
        self.assertIn("_oq_cost.draft_usd(draft)", inspect.getsource(WN.answer_text))

    def test_a_price_change_moves_the_quote_and_the_debit_together(self):
        saved = copy.deepcopy(PRICING["per_tool_usd"])
        try:
            PRICING["per_tool_usd"]["api_subscriber_iq_build"] = 650.0
            PRICING["per_tool_usd"]["api_profile_iq_cut"] = 125.0
            PRICING["per_tool_usd"]["profile_iq_derived_cut"] = 90.0       # the dashboard's derive is its own key
            desk = Desk()
            for decision, cuts, d_type in (("subscriber_iq", 0, ""), ("new_build", 2, ""), ("derive_cut", 3, "addon_cuts"),
                                           ("cut_needs_parent", 1, "")):
                debit = Desk().approve("co", decision, cuts, d_type=d_type)[1]
                table = desk.app["_v1_price_table_for"]("co")
                self.assertEqual(Q.for_draft(desk, "co", _draft(decision, cuts)), debit)
                self.assertEqual(Q.draft_usd(_draft(decision, cuts, table=table)), debit)
                api = Desk().approve("co", decision, cuts, v1=True, d_type=d_type)[1]
                self.assertEqual(desk.app["_v1_price_usd_for"](decision, cuts, username="co"), api)
            self.assertEqual(Desk().approve("co", "subscriber_iq", 0, True)[1], 650.0)
            self.assertEqual(Q.subiq(desk, _draft("subscriber_iq"), "Outlander", _money, username="co")["total"], 650.0)
            self.assertEqual(Desk().approve("co", "derive_cut", 3, d_type="addon_cuts")[1], 270.0)
            self.assertEqual(Desk().approve("co", "derive_cut", 3, v1=True, d_type="addon_cuts")[1], 375.0)
        finally:
            PRICING["per_tool_usd"].clear()
            PRICING["per_tool_usd"].update(saved)

    def test_nobody_named_reads_the_published_price(self):
        self.assertEqual(Q.price(None, {}, "subscriber_iq"), 500.0)
        self.assertEqual(Q.price({}, {}, "new_build", 2), 500.0)
        self.assertEqual(Q.price(None, {}, "existing_match"), 0.0)
        self.assertEqual(Q.price(None, {}, "existing_match", owed=True), 300.0)
        self.assertEqual(Q.for_draft(Desk(), "nobody-by-this-name", _draft("subscriber_iq", 0, True)), 500.0)


if __name__ == "__main__":
    unittest.main(verbosity=1)
