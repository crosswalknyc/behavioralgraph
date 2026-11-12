#!/usr/bin/env python3
"""A paid valuation lands on the figure the reader already holds
(Jenna 2026-11-11).

A wallet seat was given $4,217,683 for a partnership, "so far", in a
read. The paid valuation of the same partnership has to state that
figure, to the cent, whenever the order is placed and however the two
names are written. Three things used to move it:

  1. the saved research was keyed by the names as written and the end
     date, so "to today" on a later day, or the names the other way
     round, missed it and drew new research;
  2. the hold landed under the figure ($4,129,457.18);
  3. from the fourth day on, a longer "so far" window raised the
     ceiling ($4,548,481.67, then $4,796,580.67).

This replays the order end to end: the wizard as a wallet seat shaped
like hers, the real wallet arithmetic, the engine on the saved research,
on Nov 11, 12, 15 and 18, with the names in both orders. Nothing reaches
the network, the app, or any real account.

  A. the replay with the valuation of record saved: every run is the
     review deck's valuation, component for component, one $500 charge
  B. the replay with only the research saved (no record): the total is
     still the figure to the cent and every page adds up
  C. the window: "to today" on a later day runs on the read's window and
     says so; an end date named in words is a new read; another company's
     seat is not pinned
  D. the ceiling never rises with a longer "so far" window; a window
     named in words still only pulls down
  E. the exact landing itself: adds up, deterministic, never raises
     research that is under the figure
  F. the valuation tab's tile and the deck say what the people count is

Run: python3 scripts/test_pm_valuation_matches_held_figure.py
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ['CORPUS_CATALOG'] = '0'
sys.modules.setdefault('app', None)          # the dashboard app is never imported here

import test_pm_valuation_charge_path as CP  # noqa: E402  (the wallet seat harness)
from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import intake_guard as G  # noqa: E402
from prometheus import partnership_roles as ROLES  # noqa: E402
from migration import bpiq_anchors as BA  # noqa: E402
from migration import bpiq_exact as BX  # noqa: E402
from migration import bpiq_synthesis as S  # noqa: E402
from migration.bpiq_synthesis import MemoryRegistry, MemoryUnderpinnings  # noqa: E402

FAIL = 0
N = 0
FX = json.load(open(os.path.join(HERE, 'fixtures', 'pm_gap_x_jyt_valuation_2026_11_11.json'), encoding='utf-8'))
FIGURE = 4217683.00
KEY = FX['run_key']
REVIEW = FX['payload_of_record']
READ_KEY = 'gap_x_jyt|revenue|2026-09-22|2026-11-11'
DAYS = (date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 15), date(2026, 11, 18))
# how the reader may name the pairing: the roles in words, and the bare
# pairing written either way round
NAMINGS = (
    ('roles said', 'JYT is the talent and Gap is the brand'),
    ('Gap x JYT', 'Gap x JYT'),
    ('JYT x Gap', 'JYT x Gap'),
)
# what the library knows, as it does live: Gap is a brand
ROLES.KIND_LOOKUP = {'gap': 'brand'}.get


def check(label, cond):
    global FAIL, N
    N += 1
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def no_model(*a, **k):
    raise AssertionError('new research was drawn: the saved research was missed')


def seat_doc():
    """Her seat's shape, plus a colleague at the same company (the seat
    the figure was given to) and a seat at another company."""
    doc = CP.seat_doc()
    doc['users']['colleague'] = dict(doc['users']['wallet_seat'], username='colleague', email='c@example.test')
    doc['users']['outsider'] = dict(doc['users']['wallet_seat'], username='outsider', email='o@example.test',
                                    company='Other Co')
    doc['companies']['Other Co'] = copy.deepcopy(doc['companies']['Agency Co'])
    return doc


class Engine:
    """The valuation engine on an in-memory research store. `record`
    seeds the valuation of record beside the research."""

    def __init__(self, record=True):
        self.store = MemoryUnderpinnings()
        doc = copy.deepcopy(FX['underpinnings'])
        if record:
            doc['payload_of_record'] = copy.deepcopy(REVIEW)
            doc['banked_read'] = BA.read_on_pair('JYT', 'Gap')
            doc['hold'] = {'set_exactly': True, 'target_usd': FIGURE, 'read_key': READ_KEY}
        self.store.save(KEY, doc)
        self.runs = []
        self.log = ''

    def run(self, job_id, uname, inputs, extras):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            payload = S.synthesize(dict(inputs), no_model, created_by=uname, underpinnings=self.store, job_id=job_id)
        self.log += buf.getvalue()
        self.runs.append({'job_id': job_id, 'inputs': dict(inputs), 'payload': payload, 'key': S.run_key(inputs)})


class Walk(CP.Walk):
    def __init__(self, ledger, today, engine, user='wallet_seat', registry=None):
        super().__init__(ledger, registry=registry, user=user)
        self.today, self.engine = today, engine

    def say(self, text):
        G.reset_resume()
        body = {'thread_id': self.tid}
        account = dict(self.ledger.data['users'].get(self.user) or {'username': self.user})
        out = None
        if W.wants(text, body, self.hist, store=self.store, user=self.user, thread_id=self.tid):
            out = W.analyze_hook(text, body, list(self.hist), account, {}, self.user, {'_H': self.host},
                                 first=W.is_start(text), today=self.today, store=self.store,
                                 start_fn=self.engine.run, registry=self.registry, refund_fn=self.ledger.refund)
        self.hist.append({'role': 'user', 'text': text})
        if out is not None:
            self.hist.append({'role': 'agent', 'text': out['reply'],
                              'meta': {'options': [{'label': c, 'send': c} for c in (out.get('followups') or [])]}})
            self.replies.append(out['reply'])
        return out

    def order(self, naming, dates='9/22/26 to today'):
        """The whole order, to Accept on the quote. Returns the recap."""
        recap = None
        for m in (W.CHIP, naming, dates, 'Accept', 'no', 'find the campaign asset'):
            recap = self.say(m)
        self.say('Accept $500')
        return recap


def install(asker='colleague', users=None):
    BA.INDEX_FOR_TESTS = FX['ledger_index']
    BA.ASKER_LOOKUP = (lambda anchor: asker) if asker else (lambda anchor: '')
    BA.USERS_FOR_TESTS = users
    BA._ASKERS.clear()


def cents(v):
    return int(round(float(v) * 100))


def adds_up(p):
    v = p['valuation']
    lenses = sum(cents(v[k]) for k in ('earned_media_value', 'brand_engagement_value', 'brand_lift_value', 'conversion_value'))
    rows = sum(cents(r['emv_value']) for r in v['emv_breakdown'])
    per_row = all(abs(r['incremental_users_projected'] * r['emv_per_user_rate'] - r['emv_value']) < 0.005 for r in v['emv_breakdown'])
    share = abs(float(v['attributable_to_talent']) - round(float(v['total_brand_value']) * float(v['talent_share']), 2)) < 0.011
    conv = cents(v['conversion_value']) == cents(p['conversions']['post_users_projected'] * float(v['rates']['conv_value_per_user']))
    return lenses == cents(v['total_brand_value']) and rows == cents(v['earned_media_value']) and per_row and share and conv


COMPONENTS = ('total_brand_value', 'earned_media_value', 'brand_engagement_value', 'brand_lift_value', 'conversion_value',
              'incremental_users', 'attributable_to_talent', 'talent_share')


def same_as_review(p):
    v, r = p['valuation'], REVIEW['valuation']
    return (all(v[k] == r[k] for k in COMPONENTS)
            and v['emv_breakdown'] == r['emv_breakdown']
            and p['projected_audience_size'] == REVIEW['projected_audience_size']
            and p['totals'] == REVIEW['totals']
            and p['conversions'] == REVIEW['conversions']
            and p['per_platform'] == REVIEW['per_platform'])


TABLE = []


# ------------------------------------------------------------------ A, B
def replay(record):
    tag = 'A' if record else 'B'
    for day in DAYS:
        for label, naming in NAMINGS:
            led = CP.Ledger(seat_doc())
            install(users=led.data)
            eng = Engine(record=record)
            W.UNDERPINNINGS = eng.store
            w = Walk(led, day, eng)
            recap = w.order(naming)
            name = f"[{tag} {day.isoformat()} {label}]"
            ran = eng.runs[-1] if eng.runs else None
            total = float(ran['payload']['valuation']['total_brand_value']) if ran else 0.0
            charges = led.deducts()
            TABLE.append((tag, day.isoformat(), label, total, [abs(float(t['amount_usd'])) for t in charges]))
            check(f"{name} the quote is $500 and the order ran once", recap is not None and 'This is $500.' in recap['reply']
                  and len(eng.runs) == 1)
            check(f"{name} one $500 charge, nothing else", [abs(float(t['amount_usd'])) for t in charges] == [500.0]
                  and led.balance() == 3712.37 and not led.refunds)
            check(f"{name} the valuation is ${FIGURE:,.2f}, to the cent", ran is not None and cents(total) == cents(FIGURE))
            if not ran:
                continue
            p = ran['payload']
            check(f"{name} the order ran on the saved research (same key, no new research)", ran['key'] == KEY)
            check(f"{name} the window is the read's own, Sep 22 to Nov 11",
                  (ran['inputs']['event_start'], ran['inputs']['event_end']) == ('2026-09-22', '2026-11-11')
                  and (ran['inputs']['post_start'], ran['inputs']['post_end']) == ('2026-09-22', '2026-11-11'))
            check(f"{name} JYT is the talent and Gap is the brand", ran['inputs']['qualifier'] == 'JYT'
                  and ran['inputs']['brand_partner'] == 'Gap')
            check(f"{name} every page adds up", adds_up(p))
            if record:
                check(f"{name} every component is the review deck's", same_as_review(p))
                check(f"{name} it is stamped for this seat, not the review's", p.get('created_by') == 'wallet_seat')
            else:
                check(f"{name} the people stay at or under the read's", int(p['projected_audience_size']) <= 2184637)
                check(f"{name} the valuation is now the record for its key",
                      cents((eng.store.load(KEY) or {}).get('payload_of_record', {}).get('valuation', {}).get('total_brand_value') or 0)
                      == cents(FIGURE))
            # Accept a second time: the same run, no second charge
            w.say('Accept $500')
            check(f"{name} a second Accept charges nothing", len(led.deducts()) == 1 and len(eng.runs) == 1)
    W.UNDERPINNINGS = None


def test_a_replay_with_the_record():
    print("A. the replay with the valuation of record saved: the review deck's valuation, one $500 charge")
    replay(True)
    # the engine alone, on the names the other way round and a later "so far" end
    install()
    eng = Engine(record=True)
    for inputs in (
        {'qualifier': 'JYT', 'brand_partner': 'Gap', 'campaign': 'JYT x Gap', 'event_start': '2026-09-22',
         'event_end': '2026-11-11', 'post_start': '2026-09-22', 'post_end': '2026-11-11', 'control_group': False},
        {'qualifier': 'Gap', 'brand_partner': 'JYT', 'campaign': 'Gap x JYT', 'event_start': '2026-09-22',
         'event_end': '2026-11-11', 'post_start': '2026-09-22', 'post_end': '2026-11-11', 'control_group': False},
        {'qualifier': 'JYT', 'brand_partner': 'Gap', 'campaign': '', 'event_start': '2026-09-22',
         'event_end': '2026-11-11', 'post_start': '2026-09-22', 'post_end': '2026-11-11', 'control_group': False},
    ):
        eng.run('j', 'wallet_seat', inputs, {})
        p = eng.runs[-1]['payload']
        check(f"the engine finds the record for {inputs['qualifier']} | {inputs['campaign'] or '(no campaign name)'}",
              cents(p['valuation']['total_brand_value']) == cents(FIGURE) and same_as_review(p))
    # an order that reached the engine another way, "so far" on a later day, the names the other way round
    led = CP.Ledger(seat_doc())
    install(users=led.data)
    eng.run('j', 'wallet_seat', {'qualifier': 'JYT', 'brand_partner': 'Gap', 'campaign': 'JYT x Gap', 'event_start': '2026-09-22',
                                 'event_end': '2026-11-18', 'post_start': '2026-09-22', 'post_end': '2026-11-18',
                                 'control_group': False, 'window_open': True}, {})
    p = eng.runs[-1]['payload']
    check("the engine itself runs a later 'so far' order on the read's window and returns the record",
          cents(p['valuation']['total_brand_value']) == cents(FIGURE) and same_as_review(p)
          and p.get('_bpiq_category') and '_category_for_deck' not in p)
    install()
    check("the keys the three spellings used to make are all different (the old miss)",
          len({S.run_key({'qualifier': 'JYT', 'brand_partner': 'Gap', 'campaign': c, 'event_start': '2026-09-22',
                          'event_end': '2026-11-11', 'control_group': False}) for c in ('JYT x Gap', 'Gap x JYT', '')}) == 3)
    check("the record lookup tries the order's own key first, then the pairing either way",
          BA.record_keys({'qualifier': 'JYT', 'brand_partner': 'Gap', 'campaign': 'JYT x Gap', 'event_start': '2026-09-22',
                          'event_end': '2026-11-11', 'control_group': False}, S.run_key)[0]
          == S.run_key({'qualifier': 'JYT', 'brand_partner': 'Gap', 'campaign': 'JYT x Gap', 'event_start': '2026-09-22',
                        'event_end': '2026-11-11', 'control_group': False})
          and KEY in BA.record_keys({'qualifier': 'Gap', 'brand_partner': 'JYT', 'campaign': 'JYT x Gap',
                                     'event_start': '2026-09-22', 'event_end': '2026-11-11', 'control_group': False}, S.run_key))


def test_b_replay_without_the_record():
    print("B. the replay with only the research saved: the total is still the figure, to the cent")
    replay(False)
    # two runs of the same order give the same valuation (no new draw)
    install()
    eng = Engine(record=False)
    inputs = dict(FX['underpinnings']['inputs'], given_read={'key': READ_KEY})
    eng.run('j1', 'wallet_seat', inputs, {})
    first = copy.deepcopy(eng.runs[-1]['payload'])
    eng2 = Engine(record=False)
    eng2.run('j2', 'wallet_seat', inputs, {})
    second = eng2.runs[-1]['payload']
    check("two runs on the same research give the same components",
          all(first['valuation'][k] == second['valuation'][k] for k in COMPONENTS)
          and first['valuation']['emv_breakdown'] == second['valuation']['emv_breakdown'])
    # a later figure on the same window does not move the valuation off the first one
    idx = copy.deepcopy(FX['ledger_index'])
    later = copy.deepcopy(idx['subjects']['gap_x_jyt']['entries'][0])
    later.update(k='gap_x_jyt|value|2026-09-22|2026-11-11', ts='2026-11-14T10:00:00Z')
    for m in later.get('metrics') or []:
        if m.get('unit') == 'usd' and m.get('value') == 4217683:
            m['value'] = 4129457
    idx['subjects']['gap_x_jyt']['entries'].append(later)
    BA.INDEX_FOR_TESTS = idx
    eng3 = Engine(record=False)
    eng3.run('j3', 'wallet_seat', dict(FX['underpinnings']['inputs'], given_read={'key': READ_KEY, 'value_usd': FIGURE}), {})
    check("with two figures on the window, the first one given is the one stated",
          cents(eng3.runs[-1]['payload']['valuation']['total_brand_value']) == cents(FIGURE))
    install()
    check("the landing is on the record (how far it was held, and that it was set exactly)",
          (first.get('diagnostics') or {}).get('held_to_delivered_read', {}).get('set_exactly') is True)
    check("no page of it says how", all(w not in json.dumps(first.get('valuation')).lower()
                                        for w in ('held', 'ceiling', 'anchor', 'calibrat')))


# ------------------------------------------------------------------ C
def test_c_the_window():
    print("C. the window: a later 'to today' runs on the read's window; an end named in words is a new read")
    led = CP.Ledger(seat_doc())
    install(users=led.data)
    eng = Engine(record=True)
    W.UNDERPINNINGS = eng.store
    w = Walk(led, date(2026, 11, 15), eng)
    for m in (W.CHIP, 'Gap x JYT', '9/22/26 to today'):
        out = w.say(m)
    d = w.draft()
    check("the reply shows the read's window, not one that ends today",
          'September 22 to November 11, 2026' in out['reply'] and 'November 15' not in out['reply'])
    check("and says why, in plain words", 'the same window as the partnership figure you already have' in out['reply'])
    check("it says nothing about how the figure is produced",
          all(x not in out['reply'].lower() for x in ('pinned', 'anchor', 'held', 'ledger', 'saved research', 'ceiling')))
    out = w.say('Accept')
    check("the draft carries the read's window", (w.draft().get('campaign_start'), w.draft().get('campaign_end'))
          == ('2026-09-22', '2026-11-11') and (w.draft().get('attr_start'), w.draft().get('attr_end')) == ('2026-09-22', '2026-11-11'))
    # an end date named in words: a new read on that window
    led = CP.Ledger(seat_doc())
    install(users=led.data)
    w = Walk(led, date(2026, 11, 18), eng)
    for m in (W.CHIP, 'JYT is the talent and Gap is the brand', 'September 22 to November 18, 2026'):
        out = w.say(m)
    d = w.draft()
    check("an end date named in words is kept", d.get('campaign_end') == '2026-11-18' and not d.get('pinned_read')
          and d.get('end_named') and not d.get('window_open'))
    check("and is not called the same window", 'the same window as' not in out['reply'])
    inp = W.engine_inputs(dict(d, control_group=False, asset_mode='find'))
    check("the engine is told the end was named", inp.get('end_named') is True and inp.get('event_end') == '2026-11-18'
          and S.run_key(W.adopt_saved(inp)) != KEY)
    # pinned first, then a new end named at the window step
    led = CP.Ledger(seat_doc())
    install(users=led.data)
    w = Walk(led, date(2026, 11, 18), eng)
    for m in (W.CHIP, 'Gap x JYT', '9/22/26 to today', 'September 22 to November 18, 2026'):
        out = w.say(m)
    d = w.draft()
    check("a new end named at the window step makes it a new read", d.get('campaign_end') == '2026-11-18'
          and not d.get('pinned_read') and d.get('end_named') and d.get('attr_end') == '2026-11-18')
    # the same day as the read: nothing to pin, the figure is still noted
    led = CP.Ledger(seat_doc())
    install(users=led.data)
    w = Walk(led, date(2026, 11, 11), eng)
    for m in (W.CHIP, 'Gap x JYT', '9/22/26 to today'):
        out = w.say(m)
    check("on the read's own day the window is simply the window", 'the same window as' not in out['reply']
          and w.draft().get('campaign_end') == '2026-11-11')
    # a seat at another company: the figure was not given to it
    led = CP.Ledger(seat_doc())
    install(users=led.data)
    w = Walk(led, date(2026, 11, 15), eng, user='outsider')
    for m in (W.CHIP, 'Gap x JYT', '9/22/26 to today'):
        out = w.say(m)
    check("another company's seat is not moved to the read's window", w.draft().get('campaign_end') == '2026-11-15'
          and not w.draft().get('pinned_read') and not w.draft().get('given_read'))
    check("a seat at the reader's company is", BA.given_to(BA.read_on_pair('Gap', 'JYT'), 'wallet_seat', users=led.data)
          and not BA.given_to(BA.read_on_pair('Gap', 'JYT'), 'outsider', users=led.data))
    # the thread that shows the figure counts, whoever the ask log names
    install(asker='', users=led.data)
    hist = [{'role': 'agent', 'text': 'The partnership is worth $4,217,683 so far.'}]
    check("the figure on the reader's own thread counts", BA.given_to(BA.read_on_pair('Gap', 'JYT'), 'outsider', history=hist))
    check("no figure, no asker: not given", not BA.given_to(BA.read_on_pair('Gap', 'JYT'), 'outsider', history=[]))
    # the pairing is found either way round, first-held first
    a, b = BA.read_on_pair('Gap', 'JYT'), BA.read_on_pair('JYT', 'Gap')
    check("the read is found by the pairing in either order", a and b and a['key'] == b['key'] == READ_KEY
          and a['value_usd'] == FIGURE)
    idx = copy.deepcopy(FX['ledger_index'])
    later = copy.deepcopy(idx['subjects']['gap_x_jyt']['entries'][0])
    later.update(k='gap_x_jyt|revenue|2026-09-22|2026-11-18', ts='2026-11-18T10:00:00Z', we='2026-11-18')
    for m in later.get('metrics') or []:
        if m.get('unit') == 'usd' and m.get('value') == 4217683:
            m['value'] = 4796581
    idx['subjects']['gap_x_jyt']['entries'].append(later)
    check("the first figure given on the pairing is the one that holds", BA.read_on_pair('Gap', 'JYT', index=idx)['key'] == READ_KEY)
    # the engine's own pin, for an order that did not come through the wizard
    got, pinned = BA.pin_window({'qualifier': 'JYT', 'brand_partner': 'Gap', 'event_start': '2026-09-22', 'event_end': '2026-11-18',
                                 'window_open': True}, a)
    check("an open-ended order is pinned to the read's window", pinned and got['event_end'] == '2026-11-11'
          and got['post_end'] == '2026-11-11')
    got, pinned = BA.pin_window({'qualifier': 'JYT', 'brand_partner': 'Gap', 'event_start': '2026-09-22', 'event_end': '2026-11-18'}, a)
    check("an order with a named end is not", not pinned and got['event_end'] == '2026-11-18')
    got, pinned = BA.pin_window({'qualifier': 'JYT', 'brand_partner': 'Gap', 'event_start': '2026-10-01', 'event_end': '2026-11-18',
                                 'window_open': True}, a)
    check("a different start is a different read", not pinned)
    W.UNDERPINNINGS = None


# ------------------------------------------------------------------ D
def test_d_the_ceiling():
    print("D. the ceiling never rises because the window got longer")
    install()
    a = BA.read_on_pair('Gap', 'JYT')
    for end in ('2026-11-11', '2026-11-12', '2026-11-15', '2026-11-18', '2026-12-31'):
        inp = {'event_start': '2026-09-22', 'event_end': end, 'window_open': True}
        cap = BA.ceilings(a, inp, None)
        check(f"so far, ending {end}: the ceiling is the figure", cents(cap['value_usd']) == cents(FIGURE)
              and int(cap['people']) == 2184637)
    cap = BA.ceilings(a, {'event_start': '2026-09-22', 'event_end': '2026-11-18', 'end_named': True}, None)
    check("an end named in words, a week longer: the ceiling is for the longer window",
          cap['value_usd'] > FIGURE and abs(cap['value_usd'] - FIGURE * 58 / 51) < 1.0)
    cap = BA.ceilings(a, {'event_start': '2026-09-22', 'event_end': '2026-11-13', 'end_named': True}, None)
    check("within three days it is the same read", cents(cap['value_usd']) == cents(FIGURE))
    # a longer window named in words: held at or under its ceiling, never set on the old figure
    eng = Engine(record=False)
    inp = dict(FX['underpinnings']['inputs'], event_end='2026-11-18', post_end='2026-11-18', end_named=True)
    doc = copy.deepcopy(FX['underpinnings'])
    doc['inputs'] = dict(inp)
    eng.store.save(S.run_key(inp), doc)
    eng.run('j', 'wallet_seat', inp, {})
    p = eng.runs[-1]['payload']
    tot = float(p['valuation']['total_brand_value'])
    check("a new read on a longer, named window is held at or under its own ceiling",
          0 < tot <= FIGURE * 58 / 51 and cents(tot) != cents(FIGURE) and adds_up(p))
    check("and is not saved as the record of the first", not (eng.store.load(KEY) or {}).get('payload_of_record'))


# ------------------------------------------------------------------ E
def test_e_the_landing():
    print("E. the exact landing: adds up, repeats, never raises research that is under the figure")
    u = FX['underpinnings']
    inputs, prim = dict(u['inputs']), dict(u['primitives'])
    campaign = S.normalize_campaign(u.get('campaign') or {}, inputs)
    salt = S.run_salt(inputs)

    def build(f=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return S.build_payload(inputs, prim, created_by='t', campaign=campaign, hold=f)

    base = build()
    check("the research on its own is over the figure", float(base['valuation']['total_brand_value']) > FIGURE)
    out, rec = BX.land(base, FIGURE, build, salt)
    check("it lands on the figure, to the cent", rec.get('set_exactly') and cents(out['valuation']['total_brand_value']) == cents(FIGURE))
    check("every page adds up", adds_up(out))
    check("the research payload was not changed in place", float(base['valuation']['total_brand_value']) > FIGURE)
    out2, rec2 = BX.land(build(), FIGURE, build, salt)
    check("the same landing every time", out2['valuation'] == out['valuation'] and rec2.get('factor') == rec.get('factor'))
    for target in (4217683.00, 3999999.00, 2500001.00, 5000001.00, 1234567.00, 4217683.38, 987653.00):
        o, r = BX.land(build(), target, build, salt)
        check(f"a figure of ${target:,.2f} is met to the cent and adds up",
              r.get('set_exactly') and cents(o['valuation']['total_brand_value']) == cents(target) and adds_up(o))
    # every lens is a count times a rate in even cents, so an odd cent cannot be the sum of them: the
    # landing says so and the valuation is left held under the figure (a read states whole dollars)
    o, r = BX.land(build(), 4217683.37, build, salt)
    check("a figure no sum of the lenses can equal is not forced", not r.get('set_exactly') and bool(r.get('reason')))
    over = float(base['valuation']['total_brand_value']) + 250000.0
    o, r = BX.land(build(), over, build, salt)
    check("research under the figure is left as it is (nothing is raised to meet a figure)",
          not r.get('set_exactly') and cents(o['valuation']['total_brand_value']) == cents(base['valuation']['total_brand_value']))
    # with a comparison group ($1,000): different research key, the total still lands
    inp2 = dict(inputs, control_group=True, include_control=True)

    def build2(f=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return S.build_payload(inp2, prim, created_by='t', campaign=campaign, hold=f)

    o, r = BX.land(build2(), FIGURE, build2, S.run_salt(inp2))
    check("with a comparison group the total lands on the figure too",
          r.get('set_exactly') and cents(o['valuation']['total_brand_value']) == cents(FIGURE) and adds_up(o))
    # the engine with the kill switch off the anchors: no landing, nothing breaks
    os.environ['BPIQ_CORPUS_ANCHORS'] = '0'
    try:
        install()
        eng = Engine(record=False)
        eng.run('j', 'wallet_seat', dict(inputs), {})
        check("with the anchors switched off the engine still returns a valuation",
              float(eng.runs[-1]['payload']['valuation']['total_brand_value']) > 0)
    finally:
        os.environ.pop('BPIQ_CORPUS_ANCHORS', None)


# ------------------------------------------------------------------ F
def test_f_what_the_people_count_is():
    print("F. the valuation tab's tile and the deck say what the people count is")
    install()
    eng = Engine(record=True)
    eng.run('j', 'wallet_seat', dict(FX['underpinnings']['inputs']), {})
    camp = eng.runs[-1]['payload']['campaign']
    check("the record read back carries the label for its count", camp.get('reach_label') == 'People the campaign reached')
    check("and one line naming the talent's audience beside the reach",
          camp.get('people_line') == ("The campaign reached 319,506 people in the window. JYT's projected US audience is "
                                      "2,184,637; the campaign reached a part of it.") and camp.get('talent_audience_held') == 2184637)
    check("no count moved", eng.runs[-1]['payload']['projected_audience_size'] == REVIEW['projected_audience_size'])
    p = {'projected_audience_size': 911003, 'campaign': {'audience_basis': 'talent audience in the window'}}
    check("a valuation on the talent's own audience is not labeled a campaign reach",
          BA.label_people(p, {'talent_audience': 2184637}, {'qualifier': 'JYT'}) == {} and 'reach_label' not in p['campaign'])
    html = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()
    check("the tile's label has its own element, Consumers until a valuation says otherwise",
          html.count('id="bpiqKpiAudienceLabel"') == 1 and 'id="bpiqKpiAudienceLabel" style="font-size: 0.7rem; color: var(--text-secondary); '
          'text-transform: uppercase; letter-spacing: 0.05em;">Consumers</div>' in html)
    check("the render sets the label from the valuation",
          "_bpiqAudLabel.textContent = _bpiqCamp.reach_label ? String(_bpiqCamp.reach_label) : 'Consumers';" in html)
    check("the line under the row shows only when the valuation carries one",
          html.count('id="bpiqKpiPeopleLine"') == 1
          and "_bpiqPeopleLine.style.display = _bpiqCamp.people_line ? 'block' : 'none';" in html)
    deck = open(os.path.join(ROOT, 'migration', 'bpiq_deck_builder.py'), encoding='utf-8').read()
    check("the deck names the talent's audience beside the reach from the same field",
          'camp.get("talent_audience_held")' in deck and 'The campaign reached a part of it.' in deck)
    check("the deck's reach card says who the people are", '"People reached"' in deck and 'the campaign reached in' in deck)


def table():
    print()
    print("replay (date the order was placed, how the pairing was named, total, charge)")
    for tag, day, label, total, charges in TABLE:
        print(f"  {tag}  {day}  {label:<10}  ${total:,.2f}  {', '.join('$%.2f' % c for c in charges) or 'none'}")


if __name__ == '__main__':
    try:
        test_a_replay_with_the_record()
        test_b_replay_without_the_record()
        test_c_the_window()
        test_d_the_ceiling()
        test_e_the_landing()
        test_f_what_the_people_count_is()
    finally:
        BA.INDEX_FOR_TESTS = None
        BA.ASKER_LOOKUP = None
        BA.USERS_FOR_TESTS = None
        W.UNDERPINNINGS = None
        ROLES.KIND_LOOKUP = None
    table()
    print()
    if FAIL:
        print(f"FAIL ({FAIL} of {N})")
        sys.exit(1)
    print(f"OK ({N} checks)")
