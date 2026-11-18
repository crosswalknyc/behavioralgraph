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

Jenna, 2026-11-18, on the three items that change left open. Two are
approved changes to what is charged, and they are the only two:
  "Charge $100 each, as the chat used to say" (dated quarter reads)
  "Make it 10 credits either way, to match the dollar price" (a seat on
  the old credits ordering a Subscriber IQ with the Profile IQ add-on)
and "Leave it at $300" (a profile with a custom cut and no parent).

F. A dated quarter read is charged at the cut price on every path: on a
   profile, with add-on cuts, on a refresh, on the partner API; an
   included seat is metered and reads no price; an unlimited seat is
   charged nothing; a seat on credits pays a cut's credits for each.
   What counts as one, and where none is charged.
G. The pin table moved on those two things and nothing else.
H. A brief quoted before the change is never charged more than it was
   quoted: the dashboard's Approve and the app's stored brief both stop
   and ask for a fresh brief; a repeat of an order is still one order.
   Two seats are never told a price by that stop: a seat whose record
   cannot be read just then (the figure in hand is the published one,
   not a company's own rate) and a seat on a plan allowance.
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


def _load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location("pm_" + name.replace(".py", "") + "_for_quote_charge",
                                                  Path(__file__).resolve().parent / name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# the approve step itself, lifted, with the real charge functions, an engine and a store (the in-app approval suite)
AB = _load("test_pm_app_brief_approval_2026_11_17.py")

# the pricing table is read from memory: nothing reaches for the app or the network
PRICING = copy.deepcopy(W.DEFAULT_PRICING)
W._pricing_cache["value"] = PRICING
W._pricing_cache["loaded_at"] = W._now_ts() + 10 ** 9
W.try_auto_reload = lambda *a, **k: {}            # a card on file is never charged from a test
N.send_runout = lambda *a, **k: None              # and nobody is mailed

APP_SRC = (WEB / "app.py").read_text(encoding="utf-8")
CHAT_SRC = (WEB / "prometheus" / "legacy" / "chat.py").read_text(encoding="utf-8")
WATCH_SRC = (WEB / "prometheus" / "legacy" / "watch.py").read_text(encoding="utf-8")
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

    def credits_for(self, decision, cuts, quarters=0, d_type=""):
        """The credits the approve step names: the one rule, on the dashboard's own tables."""
        return W.order_credits(self.app["_V1_CREDITS"], self.app["ADDON_CUT_CREDITS"], decision, cuts, quarters,
                               cuts_only=(d_type == "addon_cuts"), default=self.app["CREDITS_PROFILE_ANALYSIS"])

    def approve(self, username, decision, cuts=0, addon_profile=False, v1=False, d_type="", quarters=0):
        """The approve step's own counting, preflight and charge, in its
        order, on the draft the order would arrive as.
        (started, dollars the wallet moved, credits the seat moved)."""
        draft = _draft(decision, cuts, addon_profile, quarters=quarters)
        n = len(W.billable_cuts(draft))
        q = len(W.billable_quarter_reads(draft, decision))
        pull = W.chat_pull_type(decision, v1=v1)
        credits = self.credits_for(decision, n, q, d_type)
        b0, c0 = self.balance(username), float(self.data["users"][username].get("credits") or 0)
        ok = self.app["has_credits_for"](username, credits, pull_type=pull, addon_cuts=n, quarter_reads=q) and \
            self.app["consume_credit"](username, description="run", job_id="r1", pull_type=pull,
                                       credits_used=credits, quarter_reads=q, addon_cuts=n)
        return bool(ok), round(b0 - self.balance(username), 2), c0 - float(self.data["users"][username].get("credits") or 0)


_Q_DAYS = {1: ("01-01", "03-31"), 2: ("04-01", "06-30"), 3: ("07-01", "09-30"), 4: ("10-01", "12-31")}


def _quarter(n, year=2026):
    q = (n - 1) % 4 + 1
    y = year - (n - 1) // 4
    return {"label": f"Q{q} {y}", "start": f"{y}-{_Q_DAYS[q][0]}", "end": f"{y}-{_Q_DAYS[q][1]}"}


def _draft(decision, cuts=0, addon_profile=None, table=None, quarters=0, **over):
    d = {"decision": decision, "subject": "Outlander", "addon_cuts": [{"cut_id": f"c{i}", "label": f"cut {i}"} for i in range(cuts)]}
    if quarters:
        d["quarter_cuts"] = [_quarter(i + 1) for i in range(quarters)]
    if decision == "subscriber_iq":
        d["subiq"] = {"title": "Outlander", "addon_profile": bool(addon_profile)}
    if table is not None:
        d["price_table"] = table
    d.update(over)
    return d


def _money(v):
    return f"${v:,.0f}" if abs(v - round(v)) < 0.009 else f"${v:,.2f}"


# Every combination, with what it debited before this change and what it debits now:
#   (name, decision, billable cuts, the Profile IQ add-on, derive type, dated quarter reads,
#    (dollars, dollars at the Kartel rate, credits) before, the same three now)
# "Before" was recorded on main (3814646e, 2026-11-18 morning) by running the same lifted charge functions there
# the way that approve step called them: quarter reads were on the order and were not charged. Dollars come from
# a wallet at the published price; the Kartel rate is its $275 Profile; credits come from a seat that still holds
# them. A change to any "now" figure is a change to what is charged.
TABLE = (
    ("Subscriber IQ alone", "subscriber_iq", 0, False, "", 0, (500.0, 500.0, 10), (500.0, 500.0, 10)),
    ("Subscriber IQ with the Profile IQ add-on", "subscriber_iq", 0, True, "", 0, (500.0, 500.0, 15), (500.0, 500.0, 10)),
    ("Profile with Avid", "new_build", 0, False, "", 0, (300.0, 275.0, 5), (300.0, 275.0, 5)),
    ("Profile with one add-on cut", "new_build", 1, False, "", 0, (400.0, 375.0, 8), (400.0, 375.0, 8)),
    ("Profile with three add-on cuts", "new_build", 3, False, "", 0, (600.0, 575.0, 14), (600.0, 575.0, 14)),
    ("Refresh", "time_shifted_refresh", 0, False, "", 0, (300.0, 275.0, 5), (300.0, 275.0, 5)),
    ("Refresh with two cuts", "time_shifted_refresh", 2, False, "", 0, (500.0, 475.0, 11), (500.0, 475.0, 11)),
    ("One year of a year package", "new_build", 0, False, "", 0, (300.0, 275.0, 5), (300.0, 275.0, 5)),
    ("One cut of a profile we hold", "derive_cut", 1, False, "addon_cuts", 0, (100.0, 100.0, 3), (100.0, 100.0, 3)),
    ("Three cuts of a profile we hold", "derive_cut", 3, False, "addon_cuts", 0, (300.0, 300.0, 9), (300.0, 300.0, 9)),
    ("A custom cut of a profile we hold", "derive_cut", 0, False, "intersect_cut", 0, (100.0, 100.0, 3), (100.0, 100.0, 3)),
    ("Profile and its listed cut, no parent yet", "cut_needs_parent", 1, False, "", 0, (400.0, 375.0, 11), (400.0, 375.0, 11)),
    ("Profile and a custom cut, no parent yet", "cut_needs_parent", 0, False, "", 0, (300.0, 275.0, 8), (300.0, 275.0, 8)),
    # ---- dated quarter reads: $100 each from 2026-11-18, where the build delivers them
    ("Profile with two quarter reads", "new_build", 0, False, "", 2, (300.0, 275.0, 5), (500.0, 475.0, 11)),
    ("Profile with one add-on cut and three quarter reads", "new_build", 1, False, "", 3, (400.0, 375.0, 8), (700.0, 675.0, 17)),
    ("Refresh with two quarter reads", "time_shifted_refresh", 0, False, "", 2, (300.0, 275.0, 5), (500.0, 475.0, 11)),
    ("Refresh with two cuts and one quarter read", "time_shifted_refresh", 2, False, "", 1, (500.0, 475.0, 11), (600.0, 575.0, 14)),
    ("One year of a year package with four quarter reads", "new_build", 0, False, "", 4, (300.0, 275.0, 5), (700.0, 675.0, 17)),
    ("Profile and its listed cut, no parent yet, with two quarter reads", "cut_needs_parent", 1, False, "", 2,
     (400.0, 375.0, 11), (600.0, 575.0, 17)),
    # ---- quarters named where the build does not deliver them: never charged, before or now
    ("One cut of a profile we hold, quarters named", "derive_cut", 1, False, "addon_cuts", 2, (100.0, 100.0, 3), (100.0, 100.0, 3)),
    ("Subscriber IQ, quarters named", "subscriber_iq", 0, False, "", 2, (500.0, 500.0, 10), (500.0, 500.0, 10)),
)
COMBOS = tuple(row[:6] for row in TABLE)
BEFORE = {row[0]: row[6] for row in TABLE}
NOW = {row[0]: row[7] for row in TABLE}
PINNED_USD = {name: v[0] for name, v in NOW.items()}
PINNED_USD_RATE = {name: v[1] for name, v in NOW.items()}
PINNED_CREDITS = {name: v[2] for name, v in NOW.items()}


def _pinned(name, path):
    return (PINNED_USD_RATE if path == "rate" else PINNED_USD)[name]


class A_TheDebitIsUnchanged(unittest.TestCase):
    def test_every_combination_debits_the_pinned_amount_on_every_path(self):
        for name, decision, cuts, addon, d_type, quarters in COMBOS:
            for path in ALL_PATHS:
                for v1 in ((False,) if addon else (False, True)):
                    with self.subTest(combo=name, path=path, api=v1):
                        ok, usd, credits = Desk().approve(path, decision, cuts, addon, v1=v1, d_type=d_type, quarters=quarters)
                        self.assertTrue(ok)
                        if path == "unl":
                            self.assertEqual((usd, credits), (0.0, 0.0))      # an unlimited seat is never charged
                        elif path == "cred":
                            self.assertEqual(usd, 0.0)                        # credits, not dollars
                            want = PINNED_CREDITS[name]
                            self.assertEqual(credits, want)
                        else:
                            self.assertEqual(usd, _pinned(name, path))        # includes the plan allowance's metering
                            self.assertEqual(credits, 0.0)

    def test_the_addon_adds_nothing_to_a_dollar_debit_or_to_a_credit_one(self):
        for path in DOLLAR_PATHS + ("plan",):
            alone = Desk().approve(path, "subscriber_iq", 0, False)[1]
            both = Desk().approve(path, "subscriber_iq", 0, True)[1]
            self.assertEqual((alone, both), (500.0, 500.0))
        # credits follow the dollars since 2026-11-18 ("Make it 10 credits either way, to match the dollar price")
        self.assertEqual(Desk().approve("cred", "subscriber_iq", 0, False)[2], 10)
        self.assertEqual(Desk().approve("cred", "subscriber_iq", 0, True)[2], 10)
        self.assertNotIn("price += _H.CREDITS_PROFILE_ANALYSIS", CHAT_SRC)
        self.assertNotIn("total = subiq_credits + prof_credits", CHAT_SRC)
        self.assertIn("draft['estimated_credits'] = subiq_credits   #", CHAT_SRC)

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
    "function _synthChatUsd(v) {", "function _synthChatPriceUsd(pt, decision, nCuts, nQuarters) {",
    "function _synthChatBillableCuts(d) {", "function _synthChatSubiqAddonUsd(d) {",
    "function _synthChatQuarterReads(d, decision) {")) if _NODE else ""


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


def _quotes(desk, username, decision, cuts, addon=False, quarters=0):
    """{surface: dollars} for every place this seat is quoted this order."""
    out = {}
    draft = _draft(decision, cuts, addon, quarters=quarters)
    # what the server stamps on the draft: the seat's table, with the brief's own quarter reads inside it
    draft["price_table"] = Q.table_for_draft(desk, username, draft, desk.app["_v1_price_table_for"](username))
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
        for name, decision, cuts, addon, d_type, quarters in COMBOS:
            for path in DOLLAR_PATHS:
                with self.subTest(combo=name, path=path):
                    debit = Desk().approve(path, decision, cuts, addon, d_type=d_type, quarters=quarters)[1]
                    self.assertEqual(debit, _pinned(name, path))
                    for surface, usd in _quotes(self.desk, path, decision, cuts, addon, quarters).items():
                        self.assertEqual(usd, debit, f"{surface} quotes {usd}, the wallet takes {debit}")

    def test_the_partner_api_quotes_what_its_key_is_charged(self):
        for name, decision, cuts, addon, d_type, quarters in COMBOS:
            if addon:
                continue                 # the API has no add-on
            for path in DOLLAR_PATHS:
                with self.subTest(combo=name, path=path):
                    debit = Desk().approve(path, decision, cuts, v1=True, d_type=d_type, quarters=quarters)[1]
                    self.assertEqual(debit, _pinned(name, path))
                    billed = len(W.billable_quarter_reads(_draft(decision, cuts, quarters=quarters), decision))
                    self.assertEqual(self.desk.app["_v1_price_usd_for"](decision, cuts, username=path,
                                                                        quarter_reads=billed), debit)
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
            self.assertEqual((table["quarter_read"], table["quarter_reads"]), (100.0, 0))
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

        def _with_addon(user, data, draft, v1=False, pricing=None, decision=None):
            extra = 300.0 if (draft.get("subiq") or {}).get("addon_profile") else 0.0
            return real(user, data, draft, v1=v1, pricing=pricing, decision=decision) + extra
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
        for name, decision, cuts, addon, d_type, quarters in COMBOS:
            with self.subTest(combo=name):
                amount = Q.for_draft(self.desk, "plan", _draft(decision, cuts, addon, quarters=quarters))
                self.assertTrue(PR.included_with_plan(user, "plan", self.desk, amount_usd=amount))
                self.assertTrue(PLAN.seat_reads_included(self.desk, None, amount, "plan"))
                stored = PB.quote("plan", user, _draft(decision, cuts, addon, quarters=quarters), decision)
                self.assertTrue(stored["included"])
                self.assertIsNone(stored["price_usd"])
                self.assertEqual(stored["label"], "")
                metered = Desk().approve("plan", decision, cuts, addon, d_type=d_type, quarters=quarters)[1]
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
                      "                    quarter_reads=_quarter_reads,\n"
                      "                    addon_cuts=len(_billable_cuts)):", CHAT_SRC)
        self.assertIn("_quarter_reads = len(__import__('wallet').billable_quarter_reads(draft, decision))", CHAT_SRC)
        self.assertIn("price = __import__('wallet').order_credits(\n"
                      "        _H._V1_CREDITS, _H.ADDON_CUT_CREDITS, decision, len(_billable_cuts), _quarter_reads,\n"
                      "        cuts_only=(d_type == 'addon_cuts'), default=_H.CREDITS_PROFILE_ANALYSIS)", CHAT_SRC)
        self.assertIn("addon_cuts=len(_billable_cuts), what=_plan.approve_what(decision, spec),\n"
                      "            quarter_reads=_quarter_reads):", CHAT_SRC)
        # and the build step ships the list the wallet charges for
        self.assertIn("_clean_q = __import__('wallet').clean_quarter_reads(draft)", CHAT_SRC)
        self.assertNotIn("f'Chatbot Profile IQ ({decision})'", CHAT_SRC)

    def test_the_partner_api_does_too(self):
        self.assertEqual(APP_SRC.count("__import__('wallet').chat_pull_type(decision, v1=True)"), 3)
        self.assertNotIn("f'Chatbot Profile IQ v1 ({decision})'", APP_SRC)
        body = _lift(APP_SRC, ("_v1_price_usd_for",))
        self.assertIn("_oq.price(user, data, d, cut_count, v1=v1,\n                     quarter_reads=quarter_reads)", body)
        self.assertIn("return 0.0", body)
        self.assertNotIn("cut_each", body)                     # no sum of its own
        self.assertIn("out = _oq.table(_pt_user, _pt_data, v1=False)", _lift(APP_SRC, ("_v1_price_table_for",)))

    def test_the_stored_brief_reads_the_same_pull(self):
        self.assertIn("_w.pull_funding(rec, data, _w.chat_pull_type(decision), addon_cuts=cuts,\n"
                      "                                  quarter_reads=quarters)", PB_SRC)
        self.assertIn("quarters = len(_w.billable_quarter_reads(draft, decision))", PB_SRC)
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
        self.assertIn("usd += quarter_reads_usd(subject, tool_key, quarter_reads, pricing)", inspect.getsource(W.pull_price_usd))
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


# ============================================================================ quarter reads
_JS_CARD = "\n".join(_extract_js(m) for m in (
    "function _synthChatFormatRange(range) {", "function _synthChatBuildActionSummary(s) {")) if _NODE else ""


def _card(draft, included=False):
    """The brief card's own words for a draft, from the widget's script."""
    draft = dict({"date_range": {"start": "2025-11-18", "end": "2026-11-17"}}, **draft)
    script = ("var synthChatCurrentDraft = null;\nvar window = " + json.dumps({"PM_SEAT_INCLUDED": bool(included)}) + ";\n"
              + _JS + "\n" + _JS_CARD + "\nconsole.log(JSON.stringify(_synthChatBuildActionSummary("
              + json.dumps(draft) + ")));\n")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
        tf.write(script)
        tmp = tf.name
    try:
        res = subprocess.run([_NODE, tmp], capture_output=True, text=True, timeout=30)
        assert res.returncode == 0, res.stderr[:600]
        return json.loads(res.stdout.strip())
    finally:
        os.unlink(tmp)


def _old_table(desk, username):
    """The table a brief carried before quarter reads were charged (2026-11-17): the rows, no quarter price."""
    t = desk.app["_v1_price_table_for"](username)
    t.pop("quarter_read", None)
    t.pop("quarter_reads", None)
    return t


class F_AQuarterReadIsChargedAtTheCutPrice(unittest.TestCase):
    def setUp(self):
        self.desk = Desk()
        host.bind(load_users=lambda: self.desk.data,
                  price_table_for=lambda u=None: self.desk.app["_v1_price_table_for"](u))

    def test_what_counts_as_one(self):
        q1, q2 = _quarter(1), _quarter(2)
        d = {"decision": "new_build", "quarter_cuts": [
            q1, dict(q2, label="q2 2026"),                       # any casing
            {"label": "3Q 2026", "start": "2026-07-01", "end": "2026-09-30"},     # as the cut step labels a typed quarter
            dict(q1),                                            # the same quarter again: one quarter read
            {"label": "Q4 2026", "start": "2026-12-31", "end": "2026-10-01"},     # dates the wrong way round
            {"label": "Spring", "start": "2026-03-01", "end": "2026-05-31"},      # not a quarter
            {"label": "Q4 2025", "start": "2025-10-01"}, "Q1", None]}
        self.assertEqual([q["label"] for q in W.clean_quarter_reads(d)], ["Q1 2026", "Q2 2026", "Q3 2026"])
        self.assertEqual(W.clean_quarter_reads(d)[2], {"label": "Q3 2026", "start": "2026-07-01", "end": "2026-09-30"})
        many = {"decision": "new_build", "quarter_cuts": [_quarter(i + 1) for i in range(11)]}
        self.assertEqual(len(W.clean_quarter_reads(many)), W.QUARTER_READS_MAX)
        self.assertEqual(W.QUARTER_READS_MAX, 8)
        for junk in (None, {}, {"quarter_cuts": None}, {"quarter_cuts": "Q1 2026"}, {"quarter_cuts": {"label": "Q1 2026"}}):
            self.assertEqual(W.clean_quarter_reads(junk), [])
        # charged where the build delivers them: a profile, a refresh, a profile built for its cut
        self.assertEqual(sorted(W.QUARTER_READ_DECISIONS), ["cut_needs_parent", "new_build", "time_shifted_refresh"])
        for decision in W.QUARTER_READ_DECISIONS:
            self.assertEqual(len(W.billable_quarter_reads(d, decision)), 3)
        # never on a cut of a profile we hold, a Subscriber IQ or a reuse: none is delivered there
        for decision in ("derive_cut", "subscriber_iq", "existing_match"):
            self.assertEqual(W.billable_quarter_reads(d, decision), [])
            self.assertEqual(Q.price(None, {}, decision, 0, quarter_reads=3), Q.price(None, {}, decision, 0))
        self.assertEqual(len(W.billable_quarter_reads(d)), 3)                       # the draft's own decision by default
        self.assertEqual(Q.quarters_of(d), 3)
        # the widget counts the same list
        if _NODE:
            got = _node("[_synthChatQuarterReads(" + json.dumps(d) + "), _synthChatQuarterReads(" + json.dumps(many)
                        + ").length, _synthChatQuarterReads(" + json.dumps(dict(d, decision="derive_cut")) + ").length]")
            self.assertEqual(got, [["Q1 2026", "Q2 2026", "Q3 2026"], 8, 0])

    def test_each_one_costs_one_cut_on_every_paying_path(self):
        for path in DOLLAR_PATHS + ("plan",):
            for decision in sorted(W.QUARTER_READ_DECISIONS):
                base = Desk().approve(path, decision)[1]
                for n in (1, 2, 4, 8):
                    with self.subTest(path=path, decision=decision, quarters=n):
                        self.assertEqual(Desk().approve(path, decision, quarters=n)[1], base + 100.0 * n)
                        self.assertEqual(Desk().approve(path, decision, cuts=2, quarters=n)[1], base + 200.0 + 100.0 * n)
        # a company's Profile rate is for the build; an add-on is the cut price, exactly as with cuts
        self.assertEqual(Desk().approve("rate", "new_build", quarters=1)[1] - Desk().approve("rate", "new_build")[1], 100.0)
        self.assertEqual(Desk().approve("rate", "new_build", cuts=1)[1] - Desk().approve("rate", "new_build")[1], 100.0)
        # a company with its own cut rate pays that for a quarter read too
        data = _data()
        data["companies"]["Acme"]["tool_price_overrides"] = {"api_profile_iq_cut": 80.0}
        self.assertEqual(Desk(copy.deepcopy(data)).approve("co", "new_build", quarters=2)[1], 300.0 + 160.0)
        self.assertEqual(Desk(copy.deepcopy(data)).approve("co", "new_build", cuts=2)[1], 300.0 + 160.0)
        self.assertEqual(W.quarter_reads_usd(data["companies"]["Acme"], "api_chatbot_profile_iq_build", 2), 160.0)
        self.assertEqual(W.quarter_reads_usd(None, "api_chatbot_profile_iq_build", 0), 0.0)
        self.assertEqual(W.quarter_reads_usd(None, "api_chatbot_profile_iq_build", "junk"), 0.0)

    def test_the_ledger_row_says_how_many(self):
        desk = Desk()
        self.assertEqual(desk.approve("co", "new_build", cuts=1, quarters=2), (True, 600.0, 0.0))
        row = desk.data["users"]["co"]["credit_usage_history"][0]
        self.assertEqual((row["wallet_charged_usd"], row["wallet_addon_cuts"], row["wallet_quarter_reads"]), (600.0, 1, 2))
        (tx,) = [t for t in desk.data["companies"]["Acme"]["wallet_transactions"] if t.get("kind") == "deduct"]
        self.assertEqual(abs(float(tx["amount_usd"])), 600.0)                       # one debit, the whole order
        plain = Desk()
        plain.approve("co", "new_build")
        self.assertNotIn("wallet_quarter_reads", plain.data["users"]["co"]["credit_usage_history"][0])

    def test_an_included_seat_is_metered_and_reads_no_price(self):
        desk = Desk()
        self.assertEqual(desk.approve("plan", "new_build", quarters=2), (True, 500.0, 0.0))     # $100 a quarter read, against the allowance
        self.assertEqual(desk.data["companies"]["Sony"]["wallet_balance_usd"], 11000.0 - 500.0)
        draft = _draft("new_build", 0, quarters=2)
        stored = PB.quote("plan", self.desk.data["users"]["plan"], draft, "new_build")
        self.assertEqual((stored["included"], stored["price_usd"], stored["label"]), (True, None, ""))
        self.assertTrue(PLAN.seat_reads_included(self.desk, None, Q.for_draft(self.desk, "plan", draft), "plan"))
        self.assertIsNone(Q.stale_quote(self.desk, "plan", dict(draft, price_table=_old_table(self.desk, "plan"))))
        self.assertEqual(PLAN.cut_tag(True, "$100"), "")
        self.assertNotIn("$", PLAN.lock_total(True, "$500", "$300") + PLAN.lock_cuts(True, 2, "$100"))
        if _NODE:
            draft["price_table"] = Q.table_for_draft(self.desk, "plan", draft, self.desk.app["_v1_price_table_for"]("plan"))
            draft.update(estimated_credits=11, addon_cuts=[{"cut_id": "female", "label": "Female"}])
            card = _card(draft, included=True)
            self.assertIn("Cost: Included with your plan", card)
            self.assertIn("\n\nQuarter reads:\n- Q1 2026\n- Q2 2026", card)
            self.assertIn("\n\nAdd-on cuts:\n- Female", card)
            self.assertNotIn("$", card)                                             # no price anywhere on the card

    def test_an_unlimited_seat_is_charged_nothing(self):
        self.assertEqual(Desk().approve("unl", "new_build", cuts=1, quarters=3), (True, 0.0, 0.0))
        self.assertTrue(PR.included_with_plan(self.desk.data["users"]["unl"], "unl", self.desk, amount_usd=700.0))

    def test_a_seat_on_credits_pays_a_cuts_credits_for_each(self):
        self.assertEqual(Desk().approve("cred", "new_build")[2], 5)
        self.assertEqual(Desk().approve("cred", "new_build", quarters=1)[2], 8)     # 3 credits, what an add-on cut takes
        self.assertEqual(Desk().approve("cred", "new_build", cuts=1)[2], 8)
        self.assertEqual(Desk().approve("cred", "new_build", cuts=1, quarters=3)[2], 17)
        self.assertEqual(Desk().approve("cred", "time_shifted_refresh", quarters=2)[1:], (0.0, 11))   # credits, no dollars
        tiers = {"new_build": 5, "derive_cut": 3, "cut_needs_parent": 8, "subscriber_iq": 10}
        self.assertEqual(W.order_credits(tiers, 3, "new_build"), 5)
        self.assertEqual(W.order_credits(tiers, 3, "new_build", 2, 1), 14)
        self.assertEqual(W.order_credits(tiers, 3, "derive_cut", 3, cuts_only=True), 9)          # the cuts are the order
        self.assertEqual(W.order_credits(tiers, 3, "derive_cut", 0, cuts_only=True), 3)
        self.assertEqual(W.order_credits(tiers, 3, "subscriber_iq"), 10)                         # with or without the Profile IQ
        self.assertEqual(W.order_credits(tiers, 3, "something_new", default=5), 5)
        self.assertEqual(W.order_credits(tiers, 3, "new_build", "x", None), 5)

    def test_the_partner_api_quotes_and_charges_them(self):
        for path in DOLLAR_PATHS:
            for decision, cuts, n in (("new_build", 0, 2), ("time_shifted_refresh", 1, 3), ("cut_needs_parent", 1, 1)):
                debit = Desk().approve(path, decision, cuts, v1=True, quarters=n)[1]
                self.assertEqual(debit, Desk().approve(path, decision, cuts, v1=True)[1] + 100.0 * n)
                self.assertEqual(self.desk.app["_v1_price_usd_for"](decision, cuts, username=path, quarter_reads=n), debit)
        self.assertEqual(Desk().approve("cred", "new_build", v1=True, quarters=2)[2], 11)
        # one list on the conclusion prices /check, charges /run and is what the build ships
        self.assertIn("quarter_reads = _w_price.billable_quarter_reads(draft, decision)", APP_SRC)
        self.assertIn("price = _w_price.order_credits(\n        _V1_CREDITS, ADDON_CUT_CREDITS, decision, len(cuts),\n"
                      "        len(quarter_reads), cuts_only=(d_type == 'addon_cuts'),\n"
                      "        default=CREDITS_PROFILE_ANALYSIS)", APP_SRC)
        self.assertIn("'quarter_reads': quarter_reads,", APP_SRC)
        self.assertEqual(APP_SRC.count("quarter_reads=_v1_run_quarters"), 5)       # the preflight, both quotes of a refusal, the charge, charge_usd
        self.assertIn("quarter_reads=len(_v1_quarters))),", APP_SRC)               # price_usd on /check
        self.assertIn("resp['quarter_reads'] = [", APP_SRC)
        self.assertIn("'quarter_read_each': _v1_price_usd_for_cut(),", APP_SRC)
        self.assertIn("'quarter_read_each': ADDON_CUT_CREDITS,", APP_SRC)

    def test_the_cut_lock_line_tags_and_totals_them(self):
        for path in DOLLAR_PATHS:
            d = _draft("new_build", 1, quarters=3)
            lk = Q.lock(self.desk, d, username=path)
            debit = Desk().approve(path, "new_build", 1, quarters=3)[1]
            self.assertEqual(lk["total"], debit)
            self.assertEqual((lk["n"], lk["cuts"], lk["quarters"], lk["each"], lk["quarter_each"]), (4, 1, 3, 100.0, 100.0))
            self.assertEqual(round(lk["base"] + lk["each"] * lk["cuts"] + lk["quarter_each"] * lk["quarters"], 2), debit)
        only_q = _draft("time_shifted_refresh", 0, quarters=2)
        lk = Q.lock(self.desk, only_q, username="co")
        self.assertEqual((lk["total"], lk["n"], lk["quarters"], lk["each"], lk["base"]), (500.0, 2, 2, 100.0, 300.0))
        self.assertEqual(lk["total"], Desk().approve("co", "time_shifted_refresh", quarters=2)[1])
        # quarters named on a brief that does not deliver them are in no total and carry no tag
        lk = Q.lock(self.desk, _draft("derive_cut", 1, quarters=2), username="co")
        self.assertEqual((lk["total"], lk["quarters"], lk["quarter_each"]), (100.0, 0, 0.0))
        self.assertEqual(Q.quarter_each(self.desk, _draft("new_build"), username="rate"), 100.0)
        self.assertEqual(Q.quarter_each(self.desk, _draft("subscriber_iq"), username="co"), 0.0)
        # the chat's line, as it used to read: each quarter read tagged at its price and inside the total
        self.assertIn("_lq = _oq.lock(_H, draft)   # what Approve charges: the build, its add-on cuts and its quarter reads", CHAT_SRC)
        self.assertIn("f\"{_plan.cut_tag(_lk or not _lq['quarters'], _pm_usd_label(_lq['quarter_each']))}\" for q in qcuts])", CHAT_SRC)
        self.assertIn("f\"{_plan.lock_cuts(_lk, _lq['n'], _pm_usd_label(_lq['each']))}\"", CHAT_SRC)
        self.assertEqual(PLAN.cut_tag(False, "$100"), " (+$100)")
        self.assertEqual(PLAN.lock_total(False, "$700", "$300") + "covers the national audience + avid; "
                         + PLAN.lock_cuts(False, 4, "$100"),
                         "Total: $700 (base $300 covers the national audience + avid; 4 x $100 for the cuts, ")

    def test_a_briefs_table_holds_its_quarter_reads(self):
        for path in DOLLAR_PATHS:
            base = self.desk.app["_v1_price_table_for"](path)
            plain = _draft("new_build", 2)
            self.assertIs(Q.table_for_draft(self.desk, path, plain, base), base)        # no quarter reads: the seat's table as it is
            d = _draft("new_build", 2, quarters=3)
            t = Q.table_for_draft(self.desk, path, d, base)
            self.assertEqual((t["quarter_reads"], t["quarter_read"]), (3, 100.0))
            debit = Desk().approve(path, "new_build", 2, quarters=3)[1]
            self.assertEqual(t["by_cuts"]["new_build"][2], debit)                       # the row is the charge: a card adds nothing
            self.assertEqual(t["by_cuts"]["time_shifted_refresh"][2], Desk().approve(path, "time_shifted_refresh", 2, quarters=3)[1])
            self.assertEqual(t["by_cuts"]["derive_cut"], base["by_cuts"]["derive_cut"])      # not delivered there, not in the row
            self.assertEqual(t["by_cuts"]["subscriber_iq"], base["by_cuts"]["subscriber_iq"])
            self.assertIs(Q.table_for_draft(self.desk, path, d, t), t)                  # already this brief's
            # the brief's quarter reads moved after the table was written: the difference is added at the table's price
            d["price_table"] = t
            d["quarter_cuts"] = d["quarter_cuts"][:1]
            self.assertEqual(Q.draft_usd(d), Desk().approve(path, "new_build", 2, quarters=1)[1])
            d["quarter_cuts"] = []
            self.assertEqual(Q.draft_usd(d), Desk().approve(path, "new_build", 2)[1])
            self.assertEqual(Q.table_for_draft(self.desk, path, d, t)["quarter_reads"], 0)   # and the next stamp puts it right
            # a seat's plain table under a brief that carries quarter reads says the same (the app's fallback reads it so)
            d2 = _draft("new_build", 1, quarters=2, price_table=base)
            self.assertEqual(Q.draft_usd(d2), Desk().approve(path, "new_build", 1, quarters=2)[1])
            # a table from before quarter reads were charged adds nothing: it says what its card showed
            d3 = _draft("new_build", 1, quarters=2, price_table=_old_table(self.desk, path))
            self.assertEqual(Q.draft_usd(d3), Desk().approve(path, "new_build", 1)[1])
        # the build step stamps the brief's own table, at the draft and again when the cut step adds quarters
        self.assertIn("spec_draft['price_table'] = _ptab = _oq.table_for_draft(_H, None, spec_draft, _pm_price_table())", CHAT_SRC)
        self.assertIn("spec_draft['price_table'] = _oq.table_for_draft(_H, None, spec_draft, _pm_price_table())", CHAT_SRC)
        self.assertIn("draft['price_table'] = _oq.table_for_draft(_H, None, draft, draft.get('price_table'))", CHAT_SRC)
        self.assertNotIn("_pm_price_table()\n", CHAT_SRC.replace("_pm_price_table())", ""))      # no brief is stamped with a bare table
        # the seat's table carries the by-cuts rows through to the draft
        self.assertIn("out['by_cuts'] = {str(k): [float(x) for x in v] for k, v in by.items()", WATCH_SRC)
        # where the chat offers quarter reads it says what one costs
        self.assertEqual(PLAN.quarter_price("quarter", False, "$100"), ", $100 each")
        self.assertEqual(PLAN.quarter_price("quarter", True, "$100") + PLAN.quarter_price("month", False, "$100")
                         + PLAN.quarter_price("quarter", False, "$0"), "")
        self.assertIn("{_plan.quarter_price(_win['kind'], _pm_seat_reads_included(), _pm_usd_label(_oq.quarter_each(_H, draft)))}", CHAT_SRC)
        self.assertIn("_d['price_table'] = _oq_batch.table_for_draft(", APP_SRC)

    @unittest.skipUnless(_NODE, "node is not on PATH")
    def test_the_widgets_card_counts_and_lists_them(self):
        d = _draft("new_build", 1, quarters=2, estimated_credits=14)
        d["price_table"] = Q.table_for_draft(self.desk, "co", d, self.desk.app["_v1_price_table_for"]("co"))
        card = _card(d)
        self.assertIn("Cost: $600 on approval", card)
        self.assertIn("\n\nAdd-on cuts ($100 each):\n- cut 0", card)
        self.assertIn("\n\nQuarter reads ($100 each):\n- Q1 2026\n- Q2 2026", card)
        self.assertIsNone(DASH_RX.search(card))
        # the seat's plain table under the same brief reads the same cost (the count is added at the table's price)
        self.assertIn("Cost: $600 on approval", _card(dict(d, price_table=self.desk.app["_v1_price_table_for"]("co"))))
        # a script from before this change reads the row and nothing else: the row already holds the quarter reads
        got = _node("(function(){var pt=" + json.dumps(d["price_table"]) + ";return [_synthChatPriceUsd(pt,'new_build',1),"
                    "_synthChatPriceUsd(pt,'new_build',1,2),_synthChatPriceUsd(pt,'new_build',1,0),"
                    "_synthChatPriceUsd(pt,'derive_cut',1,2)];})()")
        self.assertEqual(got, [600, 600, 400, 100])
        # a card drawn from a table of before the change shows the price of before: no quarter read in the cost
        old = _card(dict(d, price_table=_old_table(self.desk, "co")))
        self.assertIn("Cost: $400 on approval", old)
        self.assertIn("\n\nQuarter reads:\n- Q1 2026", old)
        # quarters on a brief that does not deliver them are not listed or counted
        sub = _draft("derive_cut", 1, quarters=2, estimated_credits=3, cut_label="Female",
                     existing_match_display_name="Outlander")
        sub["price_table"] = Q.table_for_draft(self.desk, "co", sub, self.desk.app["_v1_price_table_for"]("co"))
        self.assertNotIn("Quarter reads", _card(sub))
        self.assertIn("Cost: $100 on approval", _card(sub))


# ============================================================================ nothing else moved
class G_OnlyTheTwoApprovedChargesMoved(unittest.TestCase):
    MOVED_DOLLARS = {"Profile with two quarter reads": 2, "Profile with one add-on cut and three quarter reads": 3,
                     "Refresh with two quarter reads": 2, "Refresh with two cuts and one quarter read": 1,
                     "One year of a year package with four quarter reads": 4,
                     "Profile and its listed cut, no parent yet, with two quarter reads": 2}
    MOVED_CREDITS_ONLY = "Subscriber IQ with the Profile IQ add-on"

    def test_the_pin_table_moved_on_quarter_reads_and_the_addons_credits_only(self):
        moved = {name for name in NOW if NOW[name] != BEFORE[name]}
        self.assertEqual(moved, set(self.MOVED_DOLLARS) | {self.MOVED_CREDITS_ONLY})
        for name, n in self.MOVED_DOLLARS.items():
            b, a = BEFORE[name], NOW[name]
            self.assertEqual((a[0] - b[0], a[1] - b[1], a[2] - b[2]), (100.0 * n, 100.0 * n, 3 * n), name)
        b, a = BEFORE[self.MOVED_CREDITS_ONLY], NOW[self.MOVED_CREDITS_ONLY]
        self.assertEqual((b, a), ((500.0, 500.0, 15), (500.0, 500.0, 10)))         # dollars as they were; credits 15 to 10
        # the thirteen combinations of 2026-11-17, bar that one credit figure, are to the cent what they were
        same = [row[0] for row in TABLE[:13] if row[0] != self.MOVED_CREDITS_ONLY]
        self.assertEqual(len(same), 12)
        for name in same:
            self.assertEqual(NOW[name], BEFORE[name], name)
        # and quarters named where none is delivered stay uncharged
        for name in ("One cut of a profile we hold, quarters named", "Subscriber IQ, quarters named"):
            self.assertEqual(NOW[name], BEFORE[name], name)

    def test_called_the_way_it_was_before_the_charge_takes_what_it_took_before(self):
        """The charge functions, called as the approve step called them until 2026-11-18 (no quarter reads handed
        over; the old credit sum), debit the 'before' column on every path: nothing in them moved for any order
        that carries no quarter read."""
        for name, decision, cuts, addon, d_type, _quarters in COMBOS:
            for path in ALL_PATHS:
                with self.subTest(combo=name, path=path):
                    desk = Desk()
                    app = desk.app
                    if d_type == "addon_cuts" and cuts:
                        credits = app["ADDON_CUT_CREDITS"] * cuts
                    else:
                        credits = app["_V1_CREDITS"].get(decision, app["CREDITS_PROFILE_ANALYSIS"]) + app["ADDON_CUT_CREDITS"] * cuts
                    if decision == "subscriber_iq" and addon:
                        credits += app["CREDITS_PROFILE_ANALYSIS"]
                    pull = W.chat_pull_type(decision)
                    b0, c0 = desk.balance(path), float(desk.data["users"][path].get("credits") or 0)
                    ok = app["has_credits_for"](path, credits, pull_type=pull, addon_cuts=cuts) and \
                        app["consume_credit"](path, description="run", job_id="r1", pull_type=pull, credits_used=credits,
                                              addon_cuts=cuts)
                    self.assertTrue(ok)
                    usd = round(b0 - desk.balance(path), 2)
                    took = c0 - float(desk.data["users"][path].get("credits") or 0)
                    want = BEFORE[name]
                    if path == "unl":
                        self.assertEqual((usd, took), (0.0, 0.0))
                    elif path == "cred":
                        self.assertEqual((usd, took), (0.0, want[2]))
                    else:
                        self.assertEqual((usd, took), (want[1] if path == "rate" else want[0], 0.0))

    def test_every_other_pull_prices_as_it_did(self):
        pricing = W.load_pricing()
        data = _data()
        for subject in (data["companies"]["Acme"], data["companies"]["Kartel"], data["users"]["self"]):
            for tk in sorted(pricing["per_tool_usd"]):
                for cuts in (0, 2):
                    for qty in (1, 3):
                        base = W.subject_tool_price_usd(subject, tk, pricing)
                        old = 0.0 if base <= 0 else round(base * qty + W.addon_cuts_usd(subject, tk, cuts, pricing), 2)
                        self.assertEqual(W.pull_price_usd(subject, tk, cuts, qty), old, tk)
                        self.assertEqual(W.should_charge_wallet(subject, tk, addon_cuts=cuts, quantity=qty)[0], old, tk)


# ============================================================================ a brief quoted before the change
def _world(extra_users=None):
    """The in-app approval suite's world, with the quote module where the approve step reads it."""
    data = AB.users()
    data["users"].update(extra_users or {})
    w = AB.World(data)
    w.raw.ns["_oq"] = Q
    # the table the dashboard stamps on a draft (that suite's own stand-in is a table of before the rows existed)
    w.table = lambda username=None: Q.table((w.data["users"].get(username) or {}) if username else {}, w.data)
    host.bind(price_table_for=w.table)
    return w


def _ab_table(w, username, draft=None):
    """The table the dashboard stamps for this seat, in that world."""
    rec = w.data["users"].get(username) or {}
    t = Q.table(rec, w.data)
    return Q.table_for_draft(types_host(w), username, draft, t) if draft else t


def types_host(w):
    class _H:
        @staticmethod
        def load_users():
            return w.data
    return _H


BUILD_USD, CUT_USD = AB.BUILD_USD, AB.CUT_USD


class H_ABriefQuotedBeforeIsNeverChargedMore(unittest.TestCase):
    def tearDown(self):
        W._pricing_cache["value"] = PRICING

    def test_the_guard_itself(self):
        desk = Desk()
        fresh = _draft("new_build", 1, quarters=2)
        fresh["price_table"] = Q.table_for_draft(desk, "co", fresh, desk.app["_v1_price_table_for"]("co"))
        self.assertIsNone(Q.stale_quote(desk, "co", fresh, "new_build", _money))            # quoted $600, takes $600
        old = _draft("new_build", 1, quarters=2, price_table=_old_table(desk, "co"))        # its card showed $400
        reply = Q.stale_quote(desk, "co", old, "new_build", _money)
        self.assertEqual(reply, {"success": False, "guidance": True, "not_started": True, "price_moved": True,
                                 "error": "The price on this brief has changed since I drafted it, so I did not start it and "
                                          "nothing was charged. It now comes to $600. Ask for it again and I will draft a "
                                          "fresh brief at that price."})
        self.assertIsNone(DASH_RX.search(reply["error"]))
        self.assertIsNone(INTERNAL_RX.search(reply["error"]), reply["error"])
        # a brief of before with no quarter read on it is charged what it showed: it goes ahead
        self.assertIsNone(Q.stale_quote(desk, "co", _draft("new_build", 2, price_table=_old_table(desk, "co")), "new_build"))
        self.assertIsNone(Q.stale_quote(desk, "co", _draft("subscriber_iq", 0, True, price_table=_old_table(desk, "co")),
                                        "subscriber_iq"))
        # a table from before the rows existed (a card open since before 2026-11-17) is read the way that card read it
        flat = {"new_build": 300.0, "time_shifted_refresh": 300.0, "derive_cut": 100.0, "cut_needs_parent": 400.0,
                "subscriber_iq": 500.0, "existing_match": 0.0, "addon_cut": 100.0}
        self.assertIsNone(Q.stale_quote(desk, "co", _draft("new_build", 2, price_table=dict(flat)), "new_build"))
        self.assertIsNotNone(Q.stale_quote(desk, "co", _draft("new_build", 2, quarters=1, price_table=dict(flat)), "new_build"))
        # never shown a price: no table on the draft, or a seat whose plan includes it
        self.assertIsNone(Q.stale_quote(desk, "co", _draft("new_build", 1, quarters=2), "new_build"))
        self.assertIsNone(Q.stale_quote(desk, "plan", dict(old), "new_build"))
        self.assertIsNone(Q.stale_quote(desk, "unl", dict(old), "new_build"))
        # a price that went down is simply charged; a card that priced another decision is not a moved price
        cheap = dict(fresh, quarter_cuts=fresh["quarter_cuts"][:1])
        cheap["price_table"] = dict(fresh["price_table"], quarter_reads=1, quarter_read=500.0)
        self.assertIsNone(Q.stale_quote(desk, "co", cheap, "new_build"))
        self.assertIsNone(Q.stale_quote(desk, "co", dict(old, decision="derive_cut"), "cut_needs_parent"))
        self.assertIsNone(Q.stale_quote(desk, "co", dict(old, decision="existing_match"), "existing_match"))
        # any price that rose since the card was drawn is caught the same way
        saved = copy.deepcopy(PRICING["per_tool_usd"])
        try:
            PRICING["per_tool_usd"]["api_chatbot_profile_iq_build"] = 350.0
            self.assertIn("$650", Q.stale_quote(Desk(), "co", fresh, "new_build", _money)["error"])
        finally:
            PRICING["per_tool_usd"].clear()
            PRICING["per_tool_usd"].update(saved)
        # the approve step asks it before the funds check, the engine and the charge, and after the repeat check
        core = CHAT_SRC[CHAT_SRC.index("def _pm_approve_core(user, body):"):]
        order = [core.index("_rpa_reply = _rpa.check(decision, spec, draft, d_type, ex_key)"),
                 core.index("_stale = _oq.stale_quote(_H, _charge_user, draft, decision,"),
                 core.index("if isinstance(_stale, dict):\n        return jsonify(_stale)"),
                 core.index("if price > 0 and _charge_user and not _H.has_credits_for("),
                 core.index("_rpa_reply = _rpa.hold()"), core.index("/synth/queue"),
                 core.index("if not _H.consume_credit(")]
        self.assertEqual(order, sorted(order))

    def test_the_dashboards_approve_refuses_an_old_card_and_charges_nothing(self):
        w = _world()
        quarters = [_quarter(1), _quarter(2)]
        d = AB.draft(quarter_cuts=quarters)
        d["price_table"] = {k: v for k, v in _ab_table(w, "payer").items() if k not in ("quarter_read", "quarter_reads")}
        self.assertEqual(Q.draft_usd(d), BUILD_USD)                                 # what that card shows
        out = w._core(w.gated("payer"), {"spec_draft": copy.deepcopy(d), "run_avid": True, "email_to": "", "thread_id": "t1"})
        out = out[0] if isinstance(out, tuple) else out
        self.assertTrue(out.get("price_moved") and out.get("guidance") and out.get("not_started"))
        self.assertIn(_money(BUILD_USD + 2 * CUT_USD), out["error"])
        self.assertEqual(len(w.engine.posts), 0)                                    # nothing started
        self.assertEqual((w.balance("Acme Studios"), w.deducts("Acme Studios"), w.charges), (2000.0, [], []))
        # drafted again, the card shows the charge and the approval takes exactly that, once
        d["price_table"] = _ab_table(w, "payer", d)
        self.assertEqual(Q.draft_usd(d), BUILD_USD + 2 * CUT_USD)
        out = w._core(w.gated("payer"), {"spec_draft": copy.deepcopy(d), "run_avid": True, "email_to": "", "thread_id": "t1"})
        out = out[0] if isinstance(out, tuple) else out
        self.assertTrue(out.get("success") and out.get("run_id"), out)
        self.assertEqual(len(w.engine.posts), 1)
        self.assertEqual(w.balance("Acme Studios"), 2000.0 - BUILD_USD - 2 * CUT_USD)
        (tx,) = w.deducts("Acme Studios")
        self.assertEqual(abs(float(tx["amount_usd"])), BUILD_USD + 2 * CUT_USD)
        # the same order again inside the day is the same order: answered, not run, not charged a second time
        again = w._core(w.gated("payer"), {"spec_draft": copy.deepcopy(d), "run_avid": True, "email_to": "", "thread_id": "t1"})
        again = again[0] if isinstance(again, tuple) else again
        self.assertTrue(again.get("already"), again)
        self.assertEqual(len(w.engine.posts), 1)
        self.assertEqual(w.balance("Acme Studios"), 2000.0 - BUILD_USD - 2 * CUT_USD)
        self.assertEqual(len(w.deducts("Acme Studios")), 1)

    def test_the_apps_stored_brief_quotes_them_and_an_old_one_is_out_of_date(self):
        quarters = [_quarter(1), _quarter(2)]
        w = _world()
        row = w.rows(w.order("payer", [AB.draft(quarter_cuts=quarters)]))[0]
        total = BUILD_USD + 2 * CUT_USD
        self.assertEqual((row["price_usd"], row["price_label"], row["included"]), (total, _money(total), False))
        (held,) = w.held("payer")
        self.assertEqual(held["quote"]["price_usd"], total)
        v1, status = w.send("payer", row["approve"])
        self.assertEqual((v1["kind"], status), ("answer", 200))
        self.assertIn(f"The price is {_money(total)}, as quoted on the brief.", v1["answer"]["text"])
        self.assertEqual(w.balance("Acme Studios"), 2000.0 - total)
        (tx,) = w.deducts("Acme Studios")
        self.assertEqual(abs(float(tx["amount_usd"])), total)

        # a brief stored before the change carries the old quote (no quarter read in it): it is not charged the
        # new price, and it is not started; the reader drafts it again
        w = _world()
        row = w.rows(w.order("payer", [AB.draft(quarter_cuts=quarters)]))[0]
        key = AB.PB.key_for("payer", AB.PAPI.conversation_thread_id(w.gated("payer"), "chat-1118-a1b2"))
        doc = w.store.get_json(key)
        doc["briefs"][0]["quote"].update(price_usd=BUILD_USD, label=_money(BUILD_USD))
        w.store.put_object(AB.BUCKET, key, json.dumps(doc).encode("utf-8"))
        v1, _status = w.send("payer", row["approve"])
        self.assertEqual(AB.text_of(v1), AB.PB.OUT_OF_DATE)
        self.assertEqual(len(w.engine.posts), 0)
        self.assertEqual((w.balance("Acme Studios"), w.deducts("Acme Studios"), w.charges), (2000.0, [], []))
        (held,) = w.held("payer")
        self.assertEqual((held["state"], held["result"]["state"]), ("done", "out_of_date"))

    def test_an_included_seats_brief_starts_and_is_metered_with_no_price_shown(self):
        quarters = [_quarter(1), _quarter(2)]
        w = _world()
        before = w.balance(AB.PA.COMPANY)
        row = w.rows(w.order("naomi", [AB.draft(quarter_cuts=quarters)]))[0]
        self.assertTrue(row["included"])
        self.assertIsNone(row.get("price_usd"))
        v1, _status = w.send("naomi", row["approve"])
        self.assertEqual(v1["kind"], "answer")
        self.assertTrue(v1["answer"]["text"].startswith("Included with your plan."))
        self.assertNotIn("$", v1["answer"]["text"])
        self.assertEqual(w.balance(AB.PA.COMPANY), before - BUILD_USD - 2 * CUT_USD)         # metered, silently

    def test_a_credit_seats_tracker_takes_ten_credits_with_the_addon_through_the_real_approve_step(self):
        seat = AB._seat("creds", "", billing_source="user", paying_customer=False, credits=40)
        for addon in (False, True):
            w = _world({"creds": copy.deepcopy(seat)})
            d = AB.RAW.naomi_draft()
            d["subiq"] = dict(d["subiq"], addon_profile=addon)
            out = w._core(w.gated("creds"), {"spec_draft": d, "run_avid": True, "email_to": "", "thread_id": "t1"})
            out = out[0] if isinstance(out, tuple) else out
            self.assertTrue(out.get("success") and out.get("run_id"), out)
            self.assertEqual(w.data["users"]["creds"]["credits"], 40 - 10, f"add-on {addon}")
            self.assertEqual(w.data["users"]["creds"]["credit_usage_history"][0]["credits_used"], 10)

    def test_a_seat_whose_record_cannot_be_read_is_never_told_a_price(self):
        # The users file cannot be read at the moment of Approve, so the only figure in hand is the published
        # one. A company on its own rate was told "It now comes to $300" for a build it pays $275 for.
        class Down:
            def load_users(self):
                raise RuntimeError("the users file is unreachable")

        class Empty:
            def load_users(self):
                return {"users": {}, "companies": {}}

        desk = Desk()
        fresh = _draft("new_build", 0)
        fresh["price_table"] = Q.table_for_draft(desk, "rate", fresh, desk.app["_v1_price_table_for"]("rate"))
        self.assertEqual(Q.draft_usd(fresh), 275.0)                                 # what the company's card shows
        self.assertIsNone(Q.stale_quote(desk, "rate", fresh, "new_build", _money))  # and it is what the approval takes
        for host_down in (Down(), Empty()):
            reply = Q.stale_quote(host_down, "rate", fresh, "new_build", _money)
            self.assertEqual(reply, {"success": False, "guidance": True, "not_started": True, "error": Q.SEAT_UNREAD})
            self.assertNotIn("price_moved", reply)
        self.assertNotIn("$", Q.SEAT_UNREAD)                                        # no price is named, least of all the published one
        self.assertIn("nothing was charged", Q.SEAT_UNREAD)
        self.assertIsNone(DASH_RX.search(Q.SEAT_UNREAD))
        self.assertIsNone(INTERNAL_RX.search(Q.SEAT_UNREAD), Q.SEAT_UNREAD)
        # a brief that shows the published price has nothing to protect when the seat cannot be read: the funds
        # check answers it, as it did before
        plain = _draft("new_build", 1)
        plain["price_table"] = desk.app["_v1_price_table_for"]("co")
        self.assertIsNone(Q.stale_quote(Down(), "co", plain, "new_build", _money))
        # a brief of before with quarter reads on it is still held when the seat cannot be read, and names no price
        old = _draft("new_build", 1, quarters=2, price_table=_old_table(desk, "co"))
        self.assertEqual(Q.stale_quote(Down(), "co", old, "new_build", _money)["error"], Q.SEAT_UNREAD)

    def test_a_plan_allowance_never_reads_a_price_here_either(self):
        # An allowance that cannot cover the run is answered by the funds check with the plan's own line; a brief
        # of before with quarter reads on it read "It now comes to $600" to a seat that never reads a price.
        desk = Desk()
        desk.data["companies"]["Sony"]["wallet_balance_usd"] = 450.0
        old = _draft("new_build", 1, quarters=2, price_table=_old_table(desk, "plan"))
        self.assertFalse(PLAN.seat_reads_included(desk, None, 600.0, "plan"))       # it cannot cover the $600
        self.assertIsNone(Q.stale_quote(desk, "plan", old, "new_build", _money))
        verdict = W.pull_funding(desk.data["users"]["plan"], desk.data, W.chat_pull_type("new_build"),
                                 addon_cuts=1, quarter_reads=2)
        self.assertEqual((verdict["covered"], verdict["usd"]), (False, 600.0))
        line = W.funds_refusal_line(verdict)
        self.assertEqual(line, W.ALLOWANCE_SHORT_LINE)
        self.assertNotIn("$", line)
        ok, usd, credits = desk.approve("plan", "new_build", 1, quarters=2)         # so nothing starts and nothing is metered
        self.assertEqual((ok, usd, credits, desk.balance("plan")), (False, 0.0, 0.0, 450.0))
        # an allowance with nothing left reads the same way
        desk = Desk()
        desk.data["companies"]["Sony"]["wallet_balance_usd"] = 0.0
        self.assertIsNone(Q.stale_quote(desk, "plan", dict(old), "new_build", _money))
        # a pay-per-use wallet on the same old brief is still told the price as it stands
        self.assertIn("$600", Q.stale_quote(Desk(), "co", _draft("new_build", 1, quarters=2,
                                                                 price_table=_old_table(Desk(), "co")), "new_build", _money)["error"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
