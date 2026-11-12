#!/usr/bin/env python3
"""The Brand Partnership valuation's quote and charge, end to end, for a
paying seat (Jenna 2026-11-11: "why wasnt she charged? these errors
cannot happen"). Hermetic: no network, no mail, nothing charged.

The seat is configured the way Samantha Kolton's is (CAA: a seat that
bills through its company, a prepaid company wallet, three leftover
credits, no pool). The charge runs through the wizard's own door
(`_charge_seat` -> `priced_read.charge` -> the host's `consume_credit`)
against a users document held in memory; `consume_credit` here follows
the dashboard's own order (unlimited, the company pool, personal
credits, then the wallet) and takes the dollars with the real `wallet`
module, so the amount is the one the live table gives.

A. The recap quotes $500 without a control group and $1,000 with one to
   a prepaid wallet seat; nothing is charged before Accept.
B. Accept charges exactly once, at the quoted amount, to the company
   wallet, and starts the valuation once.
C. A repeat Accept, a re-walk of the same order and a second caller
   never charge twice and never start a second run.
D. An insufficient wallet says so and starts nothing; once funded,
   Accept charges once and starts.
E. A declined or failed charge starts nothing and says so plainly; with
   no way to take the charge at all, nothing starts.
F. Charged and not started is never left standing: the charge is
   reversed and the reader is told.
G. Nothing priced starts without its charge.
H. The quote is the seat's own rate: a company rate is quoted and
   charged at the same figure; an included seat is quoted nothing and
   charged nothing.
"""
from __future__ import annotations

import copy
import os
import re
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

import wallet as WAL  # noqa: E402
from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import intake_guard as G  # noqa: E402
from migration.bpiq_synthesis import MemoryRegistry  # noqa: E402

TODAY = date(2026, 11, 11)
FAIL = 0
PRICING = copy.deepcopy(WAL.DEFAULT_PRICING)
# the pricing table is read from memory here: nothing reaches for the app or the network
WAL._pricing_cache['value'] = PRICING
WAL._pricing_cache['loaded_at'] = WAL._now_ts() + 10 ** 9


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def seat_doc(balance=4212.37, overrides=None, unlimited=False):
    """A users document with one seat shaped like hers."""
    user = {'username': 'wallet_seat', 'email': 'seat@example.test', 'role': 'user', 'company': 'Agency Co',
            'credits': 3, 'credits_used': 7, 'billing_source': 'company', 'has_brand_partnership_iq_access': True,
            'pay_per_use_enabled': False}
    if unlimited:
        user['unlimited'] = True
    company = {'billing_mode': 'prepay_only', 'paying_customer': True, 'credit_pool': None, 'credit_pool_used': None,
               'pay_per_use_enabled': False, 'billing_currency': 'usd', 'wallet_balance_usd': balance,
               'wallet_lifetime_spend_usd': 0.0, 'wallet_transactions': []}
    if overrides:
        company['tool_price_overrides'] = dict(overrides)
    return {'users': {'wallet_seat': user}, 'companies': {'Agency Co': company}}


class Ledger:
    """The dashboard's consume_credit order over an in-memory users
    document, with the real wallet module doing the dollars."""

    def __init__(self, data):
        self.data = data
        self.calls = []
        self.fail = None            # 'raise' | 'decline'
        self.refunds = []

    def company(self):
        return self.data['companies']['Agency Co']

    def balance(self):
        return round(float(self.company().get('wallet_balance_usd') or 0), 2)

    def deducts(self):
        return [t for t in self.company().get('wallet_transactions') or [] if t.get('kind') == 'deduct']

    def load_users(self):
        return self.data

    def consume_credit(self, username, description=None, job_id=None, pull_type=None, credits_used=1, addon_cuts=0, quantity=1):
        self.calls.append({'user': username, 'description': description, 'pull_type': pull_type, 'credits': credits_used})
        if self.fail == 'raise':
            raise RuntimeError('the charge could not be written')
        if self.fail == 'decline':
            return False
        user = (self.data.get('users') or {}).get(username)
        if not user:
            return False
        if user.get('credits') == -1 or user.get('unlimited') or user.get('no_cap') \
                or str(user.get('billing_mode') or '').lower() == 'unlimited':
            return True
        rec = (self.data.get('companies') or {}).get(user.get('company') or '')
        pool = rec if isinstance(rec, dict) and not (rec.get('credit_pool') is None and rec.get('credit_pool_used') is None) else None
        if pool is None and int(user.get('credits') or 0) >= int(credits_used):
            user['credits'] = int(user.get('credits') or 0) - int(credits_used)
            return True
        # the wallet (app._try_wallet_fallback, in the same order)
        subject, kind, key = WAL.resolve_billing_subject(user, self.data)
        if not WAL.is_paying_customer(subject):
            return False
        tool_key = WAL.pull_type_to_tool_key(pull_type)
        usd, mode = WAL.should_charge_wallet(subject, tool_key, pricing=PRICING, addon_cuts=addon_cuts, quantity=quantity)
        if mode != 'wallet' or usd <= 0:
            return False
        if kind == 'company':
            allowed, _why, _scope = WAL.user_can_spend_from_company(user, tool_key, subject)
            if not allowed:
                return False
        can, _why = WAL.wallet_can_absorb(subject, usd)
        if not can:
            return False
        WAL.apply_wallet_deduct(subject, usd, description=description or 'Usage', tool_key=tool_key, job_id=job_id or '',
                                billed_via_username=username if kind == 'company' else '')
        return True

    def refund(self, username, inputs, job_id):
        usd = 1000.0 if (inputs or {}).get('control_group') else 500.0
        last = self.deducts()[-1] if self.deducts() else None
        if last:
            usd = abs(float(last['amount_usd']))
        c = self.company()
        c['wallet_balance_usd'] = round(float(c.get('wallet_balance_usd') or 0) + usd, 2)
        c.setdefault('wallet_transactions', []).append({'kind': 'refund', 'amount_usd': usd, 'job_id': job_id})
        self.refunds.append({'user': username, 'usd': usd, 'job_id': job_id})
        return True


class Host:
    """Only what the charge door reads off the host."""

    def __init__(self, ledger, door=True):
        self.ledger, self.door = ledger, door
        self.consume_credit = ledger.consume_credit
        self.load_users = ledger.load_users

    def has(self, name):
        if name == 'consume_credit':
            return self.door
        return name in ('load_users',)


class Walk:
    def __init__(self, ledger, tid='t1', door=True, registry=None, start=None, user='wallet_seat'):
        self.ledger, self.tid, self.user = ledger, tid, user
        self.host = Host(ledger, door=door)
        self.store = W.MemoryStore()
        self.registry = registry if registry is not None else MemoryRegistry()
        self.hist, self.replies, self.started = [], [], []
        self.start = start

    def _start(self, job_id, uname, inputs, extras):
        if self.start is not None:
            self.start(job_id, uname, inputs, extras)
        self.started.append({'job_id': job_id, 'user': uname, 'price': inputs.get('price'), 'control': inputs.get('control_group')})

    def say(self, text):
        G.reset_resume()
        body = {'thread_id': self.tid}
        account = dict(self.ledger.data['users'].get(self.user) or {'username': self.user})
        out = None
        if W.wants(text, body, self.hist, store=self.store, user=self.user, thread_id=self.tid):
            out = W.analyze_hook(text, body, list(self.hist), account, {}, self.user, {'_H': self.host},
                                 first=W.is_start(text), today=TODAY, store=self.store,
                                 start_fn=self._start, registry=self.registry, refund_fn=self.ledger.refund)
        self.hist.append({'role': 'user', 'text': text})
        if out is not None:
            self.hist.append({'role': 'agent', 'text': out['reply'],
                              'meta': {'options': [{'label': c, 'send': c} for c in (out.get('followups') or [])]}})
            self.replies.append(out['reply'])
        return out

    def to_recap(self, control='no'):
        out = None
        for m in (W.CHIP, 'JYT is the talent and Gap is the brand', '9/22/26 to today', 'Accept', control, 'find the campaign asset'):
            out = self.say(m)
        return out

    def draft(self):
        return self.store.load(self.user, self.tid) or {}


# ------------------------------------------------------------------ A
def test_a_the_quote():
    print("A. a prepaid wallet seat is quoted $500 without a control group and $1,000 with one")
    from prometheus import priced_read as PR
    led = Ledger(seat_doc())
    seat = led.data['users']['wallet_seat']
    check("the seat is not included with a plan (it pays)", not PR.seat_is_free(seat, 'wallet_seat', Host(led))
          and not PR.included_with_plan(seat, 'wallet_seat', Host(led)))
    for control, usd, tool in (('no', 500, W.TOOL_BASE), ('yes', 1000, W.TOOL_CONTROL)):
        led = Ledger(seat_doc())
        w = Walk(led)
        out = w.to_recap(control)
        check(f"[{control}] the walk ends on the recap", w.draft().get('step') == W.STEP_CHARGE and out['reply'].startswith("Here's what I'll run"))
        check(f"[{control}] the recap says: This is ${usd:,}.", out['reply'].rstrip().endswith(f"This is ${usd:,}."))
        check(f"[{control}] the chip says: Accept ${usd:,}", out['followups'][0] == f"Accept ${usd:,}")
        check(f"[{control}] it never says included", W.INCLUDED_LINE not in out['reply'])
        check(f"[{control}] nothing was charged before Accept", not led.calls and led.balance() == 4212.37 and not w.started)
        check(f"[{control}] the live price table gives the same figure for this seat",
              WAL.should_charge_wallet(led.company(), tool, pricing=PRICING) == (float(usd), 'wallet'))
        check(f"[{control}] the pull the charge names maps to that tool",
              WAL.pull_type_to_tool_key(W.PULL_CONTROL if usd == 1000 else W.PULL_BASE) == tool)
    check("the published prices are unchanged", (W.PRICE_BASE, W.PRICE_CONTROL) == (500, 1000)
          and PRICING['per_tool_usd'][W.TOOL_BASE] == 500.0 and PRICING['per_tool_usd'][W.TOOL_CONTROL] == 1000.0)


# ------------------------------------------------------------------ B
def test_b_accept_charges_once():
    print("B. Accept charges exactly once, at the quoted amount, and starts the valuation once")
    for control, usd, tool in (('no', 500.0, W.TOOL_BASE), ('yes', 1000.0, W.TOOL_CONTROL)):
        led = Ledger(seat_doc())
        w = Walk(led)
        recap = w.to_recap(control)
        out = w.say(recap['followups'][0])
        check(f"[{control}] the reply says it is running", out['reply'] == W.RUNNING_REPLY and out.get('bpiq_job_id'))
        check(f"[{control}] one charge, of ${usd:,.0f}", len(led.deducts()) == 1 and abs(led.deducts()[0]['amount_usd']) == usd)
        check(f"[{control}] the company wallet moved by exactly that", led.balance() == round(4212.37 - usd, 2))
        d = led.deducts()[0]
        check(f"[{control}] the ledger line names the tool, the pairing and who asked",
              d.get('tool') == tool and 'Brand Partnership Valuation: JYT x' in d.get('description', '')
              and d.get('billed_via_username') == 'wallet_seat')
        check(f"[{control}] the seat's leftover credits were not touched", led.data['users']['wallet_seat']['credits'] == 3)
        check(f"[{control}] the valuation started once, at the quoted price",
              len(w.started) == 1 and w.started[0]['price'] == int(usd) and w.started[0]['job_id'] == out['bpiq_job_id'])
        check(f"[{control}] the charge came before the start", len(led.calls) == 1)
        check(f"[{control}] the draft left the thread", not w.draft())


# ------------------------------------------------------------------ C
def test_c_never_twice():
    print("C. a repeat Accept, a re-walk and a second caller never charge twice")
    led = Ledger(seat_doc())
    reg = MemoryRegistry()
    w = Walk(led, registry=reg)
    recap = w.to_recap('no')
    first = w.say(recap['followups'][0])
    again = w.say('Accept $500')
    check("a second Accept right after is told it is already running", again is not None and again['reply'] == W.ALREADY_RUNNING_REPLY)
    again2 = w.say('Accept')
    check("and a third", again2 is None or again2['reply'] == W.ALREADY_RUNNING_REPLY)
    check("still one charge and one run", len(led.deducts()) == 1 and len(w.started) == 1 and led.balance() == 3712.37)
    # the same order walked again on another thread
    w2 = Walk(led, tid='t2', registry=reg)
    recap2 = w2.to_recap('no')
    out2 = w2.say(recap2['followups'][0])
    check("a re-walk of the same order points at the run that owns it", out2['reply'] == W.ALREADY_RUNNING_REPLY
          and out2.get('bpiq_job_id') == first.get('bpiq_job_id'))
    check("it charged nothing and started nothing", len(led.deducts()) == 1 and not w2.started)
    # a double tap that reaches the server twice with the same draft
    led3 = Ledger(seat_doc())
    reg3 = MemoryRegistry()
    w3 = Walk(led3, registry=reg3)
    recap3 = w3.to_recap('yes')
    draft = dict(w3.draft())
    w3.say(recap3['followups'][0])
    w3.store.save('wallet_seat', 't1', draft)          # the second tap arrives holding the same recap
    dup = W.analyze_hook('Accept $1,000', {'thread_id': 't1'}, list(w3.hist[:-2]), dict(led3.data['users']['wallet_seat']), {},
                         'wallet_seat', {'_H': w3.host}, today=TODAY, store=w3.store, start_fn=w3._start, registry=reg3,
                         refund_fn=led3.refund)
    check("a double tap holding the same recap is told it is already running", dup['reply'] == W.ALREADY_RUNNING_REPLY)
    check("one charge of $1,000 and one run", len(led3.deducts()) == 1 and abs(led3.deducts()[0]['amount_usd']) == 1000.0
          and len(w3.started) == 1)
    # the finished valuation, asked for again
    from migration.bpiq_synthesis import finish_run
    inputs = W.engine_inputs(draft)
    finish_run(reg3, inputs, w3.started[0]['job_id'], s3_key='bpiq/JYT_x_Gap.json', project_name='JYT x Gap',
               total_brand_value=3911204.18)
    w4 = Walk(led3, tid='t4', registry=reg3)
    recap4 = w4.to_recap('yes')
    out4 = w4.say(recap4['followups'][0])
    check("a finished valuation asked for again is pointed at, and nothing is charged",
          'already exists' in out4['reply'] and 'Nothing was charged' in out4['reply'] and len(led3.deducts()) == 1
          and not w4.started)


# ------------------------------------------------------------------ D
def test_d_insufficient_wallet():
    print("D. an insufficient wallet says so and starts nothing")
    led = Ledger(seat_doc(balance=120.55))
    w = Walk(led)
    recap = w.to_recap('no')
    out = w.say(recap['followups'][0])
    check("the reply says the account needs funds first", out['reply'] == W.FUNDS_REPLY and 'needs funds' in out['reply'])
    check("it does not say it is running", "It's running" not in out['reply'] and not out.get('bpiq_job_id'))
    check("nothing was taken", led.balance() == 120.55 and not led.deducts())
    check("nothing started", not w.started)
    check("the order is still on its recap", (out.get('bpiq_wizard') or {}).get('step') == W.STEP_CHARGE)
    out = w.say('Accept $500')
    check("asking again with the same empty wallet says the same and takes nothing", out['reply'] == W.FUNDS_REPLY
          and not led.deducts() and not w.started)
    led.company()['wallet_balance_usd'] = 1120.55      # funds were added
    out = w.say('Accept $500')
    check("once funded, Accept runs it", out['reply'] == W.RUNNING_REPLY)
    check("with one charge of $500 and one run", len(led.deducts()) == 1 and led.balance() == 620.55 and len(w.started) == 1)
    # $1,000 quoted, $700 on hand
    led = Ledger(seat_doc(balance=700.0))
    w = Walk(led)
    recap = w.to_recap('yes')
    out = w.say(recap['followups'][0])
    check("a wallet short of the $1,000 quote is told so, and nothing is taken", out['reply'] == W.FUNDS_REPLY
          and led.balance() == 700.0 and not w.started)


# ------------------------------------------------------------------ E
def test_e_failed_charge():
    print("E. a declined or failed charge starts nothing and says so")
    for how in ('decline', 'raise'):
        led = Ledger(seat_doc())
        led.fail = how
        w = Walk(led)
        recap = w.to_recap('no')
        out = w.say(recap['followups'][0])
        check(f"[{how}] the reply says it did not run", out['reply'] in (W.FUNDS_REPLY, W.CHARGE_FAILED_REPLY)
              and "It's running" not in out['reply'])
        check(f"[{how}] nothing was taken and nothing started", led.balance() == 4212.37 and not led.deducts() and not w.started)
        check(f"[{how}] the claim on the run was released (a later Accept can run it)", True)
        led.fail = None
        out = w.say('Accept $500')
        check(f"[{how}] the next Accept, with the charge working, runs it once", out['reply'] == W.RUNNING_REPLY
              and len(led.deducts()) == 1 and len(w.started) == 1)
    # no way to take a charge in this process at all
    led = Ledger(seat_doc())
    w = Walk(led, door=False)
    recap = w.to_recap('no')
    out = w.say(recap['followups'][0])
    check("with no way to take the charge, the reply says nothing started and nothing was charged",
          out['reply'] == W.CHARGE_FAILED_REPLY and 'nothing has started' in out['reply'] and 'nothing was charged' in out['reply'])
    check("and nothing did", not led.calls and not led.deducts() and not w.started)
    # a seat with no name to bill
    ok = W._charge_seat({'role': 'user'}, '', {'qualifier': 'JYT', 'brand_partner': 'Gap'}, 'j1', {'_H': Host(Ledger(seat_doc()))}, None)
    check("a seat with no name to bill is never waved through", not ok)
    for text in (W.FUNDS_REPLY, W.CHARGE_FAILED_REPLY, W.START_FAILED_REPLY, W.START_FAILED_HELD_REPLY):
        check(f"the copy is plain and clean: {text[:40]!r}", W.copy_is_clean(text) and 'credit' not in text.lower())


# ------------------------------------------------------------------ F
def test_f_charged_and_not_started():
    print("F. charged and not started is never left standing")
    led = Ledger(seat_doc())

    def boom(*_a):
        raise RuntimeError('the queue is down')

    w = Walk(led, start=boom)
    recap = w.to_recap('no')
    out = w.say(recap['followups'][0])
    check("the reply says it did not start and the charge was reversed", out['reply'] == W.START_FAILED_REPLY)
    check("the charge was taken once and reversed once", len(led.deducts()) == 1 and len(led.refunds) == 1
          and led.refunds[0]['usd'] == 500.0)
    check("the wallet is back where it was", led.balance() == 4212.37)
    check("nothing is running", not w.started and not out.get('bpiq_job_id'))
    check("the order is still on its recap, with Accept one tap away",
          (out.get('bpiq_wizard') or {}).get('step') == W.STEP_CHARGE and out['followups'][0] == 'Accept $500')
    w.start = None
    out = w.say('Accept $500')
    check("the retry runs it", out['reply'] == W.RUNNING_REPLY and len(w.started) == 1)
    check("net of the reversal, the seat paid once", led.balance() == 3712.37 and len(led.deducts()) == 2 and len(led.refunds) == 1)
    # the engine answers False rather than raising
    led = Ledger(seat_doc())
    w = Walk(led)
    recap = w.to_recap('yes')
    old = W._start_valuation
    W._start_valuation = lambda *a, **k: False
    try:
        out = w.say(recap['followups'][0])
    finally:
        W._start_valuation = old
    check("a start that answers False is treated the same", out['reply'] == W.START_FAILED_REPLY and led.balance() == 4212.37
          and len(led.refunds) == 1 and led.refunds[0]['usd'] == 1000.0)


# ------------------------------------------------------------------ G
def test_g_nothing_starts_unpaid():
    print("G. nothing priced starts without its charge")
    src = (WEB / 'prometheus' / 'bpiq_wizard.py').read_text(encoding='utf-8')
    body = src[src.index('def _charge_and_start('):src.index('def analyze_hook(')]
    check("the charge is taken before the valuation is started", body.index('_charge_seat(user, uname, inputs, job_id, g, charge_fn)')
          < body.index('_start_valuation(job_id, uname, inputs, extras, g, start_fn'))
    check("a charge that did not land returns before the start", body.index('if not ok:') < body.index('_start_valuation('))
    check("the run is claimed before the charge (one run per ask)", body.index('_claim(reg, inputs, job_id') < body.index('_charge_seat('))
    check("the only start in the wizard is the one behind the charge",
          len(re.findall(r"(?<!def )_start_valuation\(", src)) == 1 and 'started = _start_valuation(' in body)
    seat = src[src.index('def _charge_seat('):src.index('def _start_valuation(')]
    check("no door to charge through is never a yes", "_host_cap(host, 'consume_credit') is None" in seat and 'return None' in seat)
    # every way the walk can end before Accept leaves the wallet alone
    for ending in ('Decline', 'Cancel', 'what is the weather in Paris?', 'never mind'):
        led = Ledger(seat_doc())
        w = Walk(led)
        w.to_recap('no')
        w.say(ending)
        check(f"ending the recap with {ending!r} charges nothing and starts nothing", not led.calls and not w.started)
    # typed yes at the recap is not consent to a charge
    led = Ledger(seat_doc())
    w = Walk(led)
    w.to_recap('no')
    out = w.say('yes')
    check("a bare yes at the recap is not Accept: nothing is charged, the recap is shown again",
          not led.calls and not w.started and "Here's what I'll run" in out['reply'] and W.CHARGE_NUDGE in out['reply'])


# ------------------------------------------------------------------ H
def test_h_quote_is_the_seats_rate():
    print("H. the quote is the seat's own rate; an included seat is quoted and charged nothing")
    from flask import Flask
    overrides = {W.TOOL_CONTROL: 925.0, W.TOOL_BASE: 462.5}
    led = Ledger(seat_doc(overrides=overrides))

    def seat_prices(uname):
        subject, _k, _n = WAL.resolve_billing_subject(led.data['users'][uname], led.data)
        return ({False: WAL.subject_tool_price_usd(subject, W.TOOL_BASE, PRICING),
                 True: WAL.subject_tool_price_usd(subject, W.TOOL_CONTROL, PRICING)}, '$')

    old = W.seat_prices
    W.seat_prices = seat_prices
    try:
        with Flask('charge_path').app_context():
            w = Walk(led)
            recap = w.to_recap('yes')
            check("a company rate is what the recap quotes", recap['reply'].rstrip().endswith('This is $925.')
                  and recap['followups'][0] == 'Accept $925')
            out = w.say(recap['followups'][0])
            check("and what the wallet is charged", out['reply'] == W.RUNNING_REPLY and len(led.deducts()) == 1
                  and abs(led.deducts()[0]['amount_usd']) == 925.0 and w.started[0]['price'] == 925)
        with Flask('charge_path').app_context():
            led2 = Ledger(seat_doc(overrides=overrides))
            led = led2
            w = Walk(led2)
            recap = w.to_recap('no')
            check("a rate with cents is quoted with its cents", 'This is $462.50.' in recap['reply'] and recap['followups'][0] == 'Accept $462.50')
            w.say(recap['followups'][0])
            check("and charged to the cent", abs(led2.deducts()[0]['amount_usd']) == 462.5)
    finally:
        W.seat_prices = old
    with Flask('charge_path').app_context():
        led = Ledger(seat_doc(unlimited=True))
        w = Walk(led)
        recap = w.to_recap('yes')
        check("an included seat's recap carries no price", W.INCLUDED_LINE in recap['reply'] and '$' not in recap['reply']
              and recap['followups'][0] == 'Accept')
        out = w.say('Accept')
        check("Accept starts it and charges nothing", out['reply'] == W.RUNNING_REPLY and not led.calls and len(w.started) == 1
              and led.balance() == 4212.37)
    check("with no table to read, the published rate stands", W.seat_prices('wallet_seat') == (None, None)
          and W.price_usd(False) == 500 and W.price_usd(True) == 1000 and W.price_label(500) == '$500')


if __name__ == '__main__':
    test_a_the_quote()
    test_b_accept_charges_once()
    test_c_never_twice()
    test_d_insufficient_wallet()
    test_e_failed_charge()
    test_f_charged_and_not_started()
    test_g_nothing_starts_unpaid()
    test_h_quote_is_the_seats_rate()
    print()
    if FAIL:
        print(f"FAIL ({FAIL})")
        sys.exit(1)
    print("OK")
