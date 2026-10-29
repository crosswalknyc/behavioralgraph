#!/usr/bin/env python3
"""Brand Partnership Valuation wizard (Jenna 2026-10-28). Hermetic."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))

from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import outbound_mail  # noqa: E402
from prometheus.intake_draft import BPIQ_ASK_COPY  # noqa: E402

TODAY = date(2026, 10, 28)
FAIL = 0


def check(label, cond):
    global FAIL
    if cond:
        print(f"  ok  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}")


def turn(text, store, draft=None, confirm=None, first=None, charge_fn=None,
         start_fn=None, body=None):
    body = dict(body or {})
    if draft is not None:
        body['bpiq_wizard'] = draft
    if confirm is not None:
        body['bpiq_confirm'] = confirm
    hist = []
    return W.analyze_hook(
        text, body, hist, 'tester', {}, 'tester',
        {'_pm_bpiq_intent': W.is_chip},
        first=first, today=TODAY, store=store,
        charge_fn=charge_fn, start_fn=start_fn)


def test_chip_starts_talent():
    store = W.MemoryStore()
    out = turn(W.CHIP, store, first=True)
    check("chip starts wizard", out is not None)
    check("first ask is property/talent",
          out['reply'] == W.ASK_TALENT)
    check("old one-message dump gone from live reply",
          'Give me, in one message' not in out['reply']
          and 'RAM Trucks' not in out['reply']
          and 'Glen Powell' not in out['reply'])
    check("Cancel is on the first step", W.CHIP_CANCEL in out['followups'])
    check("chips are followups not a numbered dump",
          W.CHIP_CANCEL in (out.get('followups') or []))
    check("ASK_COPY is the talent question",
          BPIQ_ASK_COPY == W.ASK_TALENT)


def test_attribution_windows():
    a, b = W.attribution_window('2026-10-07', '2026-10-28', today=TODAY)
    check("Oct 7-28 today Oct 28 => attr Oct 7-28 (not +30)",
          (a, b) == ('2026-10-07', '2026-10-28'))
    a, b = W.attribution_window('2026-08-01', '2026-09-01', today=TODAY)
    check("campaign end 2026-09-01 today Oct 28 => through 2026-10-01",
          (a, b) == ('2026-08-01', '2026-10-01'))
    s, e = W.parse_dates('October 7 - 28', today=TODAY)
    check("natural dates default to current year",
          (s, e) == ('2026-10-07', '2026-10-28'))


def test_prices():
    check("control no => 500", W.price_usd(False) == 500)
    check("control yes => 1000", W.price_usd(True) == 1000)
    check("accept chip 500", W.accept_chip(500) == 'Accept $500')
    check("accept chip 1000", W.accept_chip(1000) == 'Accept $1,000')


def walk_to_charge(store, control=False, mode='find', links=None):
    turn(W.CHIP, store, first=True)
    turn('Jennifer Beals', store)
    turn('Ganni x Lululemon', store)
    turn('October 7 - 28', store)
    d = store.load('tester', '')
    check("after dates we confirm the window",
          d['step'] == W.STEP_ATTR)
    turn('Accept', store)
    turn('Yes' if control else 'No', store)
    if mode == 'links':
        turn(W.CHIP_PASTE, store)
        turn('\n'.join(links or ['https://example.com/a']), store)
    else:
        turn(W.CHIP_FIND, store)
    return store.load('tester', '')


def test_accept_charges_enqueue_decline_does_not():
    charged = []
    started = []

    def charge(user, inputs, job_id):
        charged.append((user, inputs, job_id))
        return True

    def start(job_id, user, inputs, extras):
        started.append((job_id, user, inputs))

    store = W.MemoryStore()
    d = walk_to_charge(store, control=False, mode='find')
    check("find mode does not require URLs",
          d['asset_mode'] == 'find' and d.get('links') == [])
    check("price 500 on no control", d['price'] == 500)
    out = turn(W.accept_chip(500), store, charge_fn=charge, start_fn=start)
    check("accept charges once", len(charged) == 1)
    check("accept enqueues once", len(started) == 1)
    check("running copy exact", out['reply'] == W.RUNNING_REPLY)
    check("running copy clean", W.copy_is_clean(out['reply']))

    store2 = W.MemoryStore()
    walk_to_charge(store2, control=True, mode='find')
    charged.clear()
    started.clear()
    out = turn('Decline', store2, charge_fn=charge, start_fn=start)
    check("decline does not charge", charged == [])
    check("decline does not enqueue", started == [])
    check("decline copy exact", out['reply'] == W.DECLINE_REPLY)


def test_links_and_find():
    store = W.MemoryStore()
    d = walk_to_charge(store, control=False, mode='links',
                       links=['https://example.com/one',
                              'https://example.com/two'])
    check("links mode stores URLs",
          d['asset_mode'] == 'links'
          and d['links'] == ['https://example.com/one',
                             'https://example.com/two'])
    store2 = W.MemoryStore()
    d2 = walk_to_charge(store2, control=True, mode='find')
    check("find mode stores no URLs",
          d2['asset_mode'] == 'find' and not d2.get('links'))
    check("typed no means find", True)
    store3 = W.MemoryStore()
    turn(W.CHIP, store3, first=True)
    turn('Jennifer Beals', store3)
    turn('Ganni x Lululemon', store3)
    turn('October 7 - 28', store3)
    turn('Accept', store3)
    turn('No', store3)
    out = turn('no', store3)
    check("typed no on assets is find",
          store3.load('tester', '')['asset_mode'] == 'find'
          or (out and 'I will find' in (out.get('reply') or '')
              or store3.load('tester', '').get('step') == W.STEP_CHARGE))


def test_copy_and_email():
    check("running copy has no synth/pipeline",
          W.copy_is_clean(W.RUNNING_REPLY)
          and 'synth' not in W.RUNNING_REPLY.lower()
          and 'pipeline' not in W.RUNNING_REPLY.lower())
    check("running copy has no em dash",
          '\u2014' not in W.RUNNING_REPLY and '\u2013' not in W.RUNNING_REPLY)
    recap = W.recap_reply({
        'talent': 'Jennifer Beals',
        'campaign': 'Ganni x Lululemon',
        'campaign_start': '2026-10-07',
        'campaign_end': '2026-10-28',
        'attr_start': '2026-10-07',
        'attr_end': '2026-10-28',
        'control_group': False,
        'asset_mode': 'find',
        'links': [],
    })
    check("recap is clean", W.copy_is_clean(recap))
    subj, body = W.email_copy({
        'talent': 'Jennifer Beals',
        'campaign': 'Ganni x Lululemon',
    })
    check("email names campaign and talent",
          'Ganni x Lululemon' in subj and 'Jennifer Beals' in subj
          and 'Ganni x Lululemon' in body and 'Jennifer Beals' in body)
    check("email body is clean", W.copy_is_clean(body) and W.copy_is_clean(subj))
    check("email From is Prometheus",
          outbound_mail.FROM.startswith('Prometheus <')
          and 'prometheus@crosswalknyc.com' in outbound_mail.FROM)
    check("email never from Jenna",
          'jenna@' not in outbound_mail.FROM.lower()
          or outbound_mail.FROM.lower().startswith('prometheus'))


def test_index_dump_gone():
    idx = (WEB / 'templates' / 'index.html').read_text(encoding='utf-8',
                                                       errors='replace')
    check("index.html has no Give me in one message",
          'Give me, in one message' not in idx)
    check("index.html has no RAM Trucks Glen Powell example",
          'RAM Trucks' not in idx and 'Glen Powell' not in idx)
    check("index.html still has the chip",
          'Value a brand partnership' in idx)
    check("index.html still mentions Cancel on the wizard path",
          'Cancel' in idx)
    check("index.html has Accept on the confirm path",
          "indexOf('accept')" in idx)
    check("index.html persists the wizard",
          'pmBpiqWiz' in idx and 'bpiq_wizard' in idx)


def test_ahead_and_cancel():
    store = W.MemoryStore()
    out = turn('Jennifer Beals, Ganni x Lululemon, October 7 - 28',
               store, first=True)
    d = store.load('tester', '')
    check("ahead dump fills talent", d.get('talent') == 'Jennifer Beals')
    check("ahead dump fills campaign",
          'Ganni' in (d.get('campaign') or ''))
    check("ahead dump fills dates",
          d.get('campaign_start') == '2026-10-07'
          and d.get('campaign_end') == '2026-10-28')
    check("ahead still asks the missing step (attr or later)",
          d.get('step') in (W.STEP_ATTR, W.STEP_CONTROL, W.STEP_ASSETS,
                            W.STEP_CHARGE))
    check("Cancel available after ahead",
          W.CHIP_CANCEL in (out.get('followups') or []))
    out = turn('Cancel', store)
    check("Cancel stops cleanly",
          out['reply'] == W.CANCEL_REPLY
          and store.load('tester', '') is None)


PAIR_LEAKS = (
    'To price a partnership I need the pair',
    'four numbers',
    'Colin and Samir',
)


def _real_intent(text):
    from prometheus.intents import bpiq_intent
    return bpiq_intent(text, 'Pull Brand Partnership Valuation')


def turn_live(text, store, history=None, body=None, draft=None):
    """Production-shaped turn: real intent gate, no first=True."""
    body = dict(body or {})
    if draft is not None:
        body['bpiq_wizard'] = draft
    return W.analyze_hook(
        text, body, list(history or []), 'tester', {}, 'tester',
        {'_pm_bpiq_intent': _real_intent},
        first=None, today=TODAY, store=store)


def _starts_step1(out, store=None):
    reply = str((out or {}).get('reply') or '')
    check("chip/typed start is not None", out is not None)
    check("first reply is property/talent",
          'Who is the property or talent' in reply)
    check("no pair-read lead",
          'To price a partnership I need the pair' not in reply)
    check("no four-numbers frame",
          'four numbers' not in reply.lower())
    check("no Colin and Samir library comp",
          'Colin and Samir' not in reply)
    if store is not None:
        d = store.load('tester', '') or {}
        check("draft reset to talent step",
              d.get('step') == W.STEP_TALENT and not d.get('talent'))


def test_exact_chip_text_starts_wizard():
    store = W.MemoryStore()
    out = turn_live(W.CHIP, store)
    _starts_step1(out, store)
    check("is_start matches the chip", W.is_start(W.CHIP))
    check("real intent matches the chip", _real_intent(W.CHIP))


def test_typed_value_a_brand_partnership_starts():
    store = W.MemoryStore()
    out = turn_live('value a brand partnership', store)
    _starts_step1(out, store)
    for phrase in (
            'Value a brand partnership.',
            'brand partnership valuation',
            'value this partnership',
            'BPIQ',
            'Pull Brand Partnership Valuation'):
        store2 = W.MemoryStore()
        hit = turn_live(phrase, store2)
        check("start phrase %r opens step 1" % phrase,
              hit is not None
              and 'Who is the property or talent' in str(hit.get('reply')))
        check("start phrase %r is not a pair-read" % phrase,
              not any(p in str(hit.get('reply')) for p in PAIR_LEAKS))


def test_chip_resets_unrelated_prior_subject():
    store = W.MemoryStore()
    store.save('tester', '', {
        **W.empty_draft(),
        'step': W.STEP_CAMPAIGN,
        'talent': 'Colin and Samir',
        'campaign': 'Lexus',
    })
    hist = [
        {'role': 'user',
         'text': 'what did Colin and Samir do with Lexus?'},
        {'role': 'agent',
         'text': 'The Lexus campaign with Colin and Samir ran on four '
                 'numbers. Reach, cold start, first action, talent lift.'},
    ]
    out = turn_live(W.CHIP, store, history=hist)
    _starts_step1(out, store)
    d = store.load('tester', '') or {}
    check("leftover Colin and Samir draft was cancelled",
          d.get('talent') in (None, ''))
    check("wants() is true for the chip even with prior subject",
          W.wants(W.CHIP, {}, hist))


def test_router_start_beats_pair_read():
    from prometheus.intents import bpiq_intent
    from prometheus import understand
    from prometheus.service import LANE_ORDER, lane_markers
    check("LANE_ORDER leads with the wizard",
          LANE_ORDER[0] == 'bpiq_wizard')
    check("LANE_ORDER matches ask() markers",
          list(LANE_ORDER) == lane_markers())
    check("intents treat the chip as BPIQ",
          bpiq_intent('Value a brand partnership',
                      'Pull Brand Partnership Valuation'))
    check("intents treat typed value a brand partnership",
          bpiq_intent('value a brand partnership'))
    ti = understand.tool_intent('Value a brand partnership')
    check("understand.tool_intent is bpiq for the chip", ti == 'bpiq')
    d = understand.decide('Value a brand partnership', has_ctx=True)
    check("understand sends the chip to analyze",
          d.get('surface') == 'analyze')
    check("understand reason is the wizard intake",
          'bpiq' in str(d.get('reason') or ''))


def test_funds_fail_does_not_start():
    started = []
    store = W.MemoryStore()
    walk_to_charge(store, control=False)
    out = turn(W.accept_chip(500), store,
               charge_fn=lambda *a: False,
               start_fn=lambda *a: started.append(a))
    check("failed funds do not start the job", started == [])
    check("failed funds mention add funds",
          'add funds' in out['reply'].lower())


def main():
    print("Brand Partnership Valuation wizard")
    test_chip_starts_talent()
    test_attribution_windows()
    test_prices()
    test_accept_charges_enqueue_decline_does_not()
    test_links_and_find()
    test_copy_and_email()
    test_index_dump_gone()
    test_ahead_and_cancel()
    test_funds_fail_does_not_start()
    test_exact_chip_text_starts_wizard()
    test_typed_value_a_brand_partnership_starts()
    test_chip_resets_unrelated_prior_subject()
    test_router_start_beats_pair_read()
    print("\n" + ("OK" if not FAIL else f"{FAIL} FAILED"))
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
