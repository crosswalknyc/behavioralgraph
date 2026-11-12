#!/usr/bin/env python3
"""A priced order that stalls is visible the same day, in the mail that
already goes out (Jenna 2026-11-11, Samantha Kolton at CAA: her
valuation order was left for a general read, asked one question twice
in the same words and never reached its quote, and nothing said so).
Hermetic: no network, nothing is sent.

A. At answer time the reply is read against the thread: a repeated
   question, a general answer printed while an order was open, an order
   left with no quote and no way back.
B. The flag rides the ask-log record (extra.order), first among the
   extras, in the short form the log keeps.
C. The existing question copy marks its subject and carries one plain
   line; an ask that went as it should is unmarked. No new email, the
   same recipients, a probe never mails.
D. A walk's one email says when the walk stalled: a question asked
   twice, or a walk that ended with no quote. A walk that reached its
   quote, or that the reader cancelled, is unmarked.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

import prometheus_watch_notify as PWN  # noqa: E402
from prometheus import ask_email_bundle as B  # noqa: E402
from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import order_stall as OS  # noqa: E402

FAIL = 0
RECORD = {'email': 'sam@agency.test', 'company': 'Agency Co', 'first_name': 'Sam', 'last_name': 'Reader'}
HER = 'JYT is the talent and GAP is the brand. the partnership started in September 2026'


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def wiz(reply, step, chips=('Cancel',), **extra):
    d = dict(W.empty_draft(), step=step)
    out = {'success': True, 'action': 'answer', 'reply': reply, 'followups': list(chips), 'bpiq_collect': True, 'bpiq_wizard': d}
    out.update(extra)
    return out


def agent(payload):
    return {'role': 'agent', 'text': payload.get('reply') or '',
            'meta': {'options': [{'label': c, 'send': c} for c in payload.get('followups') or []],
                     **({'bpiq_wizard': payload['bpiq_wizard']} if payload.get('bpiq_wizard') else {})}}


# ------------------------------------------------------------------ A
def test_a_observe():
    print("A. where a priced order stands is read at answer time")
    hist = []
    p1 = wiz(W.ASK_TALENT, W.STEP_TALENT)
    o = OS.observe(W.CHIP, p1, hist)
    check("the chip opens the order at the talent step", o == {'flow': 'brand_partnership', 'state': 'open', 'step': 'talent'})
    hist += [{'role': 'user', 'text': W.CHIP}, agent(p1)]
    # 2026-11-11: her sentence was answered by a general read; the reply carried the way back
    ack = {'success': True, 'action': 'answer', 'reply': 'On it: reading Gap Customers now. This one takes a real look at the data.',
           'read_job_id': 'f21c0ed76df6', 'intake_resume': 'brand_partnership', 'followups': ['Back to the valuation']}
    o = OS.observe(HER, ack, hist)
    check("a general answer while the order was open is flagged, with the step it was on",
          o and o['state'] == 'left' and o['flags'] == [OS.FLAG_LEFT] and o['flow'] == 'brand_partnership' and o.get('step') == 'talent')
    # the assets question, asked twice in the same words
    pa = wiz(W.ASK_ASSETS, W.STEP_ASSETS, chips=("I'll paste links", 'Find the assets', 'Cancel'))
    hist2 = [{'role': 'user', 'text': 'yes'}, agent(pa)]
    o = OS.observe('find the campaign asset', pa, hist2)
    check("the same question in the same words is flagged, with its step",
          o and o['flags'] == [OS.FLAG_REPEAT] and o['step'] == 'assets' and o['state'] == 'open')
    o = OS.observe('find the campaign asset', wiz(W.ASK_ASSETS_ALT, W.STEP_ASSETS), hist2)
    check("the same step in other words is not", o and not o.get('flags'))
    # an order left with no way back
    plain = {'success': True, 'action': 'answer', 'reply': 'Gap Customers skew 60.4% female.', 'followups': []}
    o = OS.observe('who are gap customers', plain, hist2)
    check("an order left for another answer with no way back is flagged as an exit without a quote",
          o and o['state'] == 'left' and o['flags'] == [OS.FLAG_EXIT] and o.get('step') == 'assets')
    # the quote, the accept, the cancel, the decline
    recap = wiz("Here's what I'll run:\nJYT\nJYT x Gap\nThis is $500.", W.STEP_CHARGE, chips=('Accept $500', 'Decline', 'Cancel'),
                bpiq_confirm_payload={'qualifier': 'JYT'}, bpiq_collect=False)
    check("the recap is the quote", OS.observe('find them', recap, hist2) == {'flow': 'brand_partnership', 'state': 'quote', 'step': 'charge'})
    hist3 = [{'role': 'user', 'text': 'find them'}, agent(recap)]
    check("Accept reads as accepted", (OS.observe('Accept $500', {'success': True, 'reply': W.RUNNING_REPLY, 'bpiq_job_id': 'a' * 12}, hist3) or {}).get('state') == 'accepted')
    check("Decline reads as declined", (OS.observe('Decline', {'success': True, 'reply': W.DECLINE_REPLY}, hist3) or {}).get('state') == 'declined')
    check("Cancel reads as cancelled", (OS.observe('Cancel', {'success': True, 'reply': W.CANCEL_REPLY}, hist2) or {}).get('state') == 'cancelled')
    check("a wallet that cannot cover it is still at the quote",
          (OS.observe('Accept $500', {'success': True, 'reply': W.FUNDS_REPLY, 'bpiq_wizard': dict(W.empty_draft(), step='charge')}, hist3) or {}).get('state') == 'quote')
    # the other orders
    check("a flywheel step is an open flywheel order", (OS.observe('x', {'reply': 'Which ecosystem?', 'fw_collect': True}, []) or {}).get('flow') == 'flywheel')
    check("a journey confirm card is a quote", (OS.observe('x', {'reply': "Here's the Digital Journey I'll build", 'jiq_confirm_payload': {'subject': 'X'}}, []) or {}).get('state') == 'quote')
    check("a brief card is a quote", (OS.observe('x', {'success': True, 'spec_draft': {'subject': 'Paw Patrol'}}, []) or {}).get('state') == 'quote')
    check("a priced offer is a quote", (OS.observe('x', {'reply': 'Pulling this data will cost $100.', 'panel_offer': {'subject': 'X'}}, []) or {}).get('state') == 'quote')
    # no order, no stamp
    check("an ordinary answer in an ordinary thread carries nothing",
          OS.observe('how old is this audience', plain, [{'role': 'user', 'text': 'hi'}, {'role': 'agent', 'text': 'Hello.'}]) is None)
    check("any clarifier asked twice in the same words is flagged, order or not",
          (OS.observe('uh', {'reply': 'Which title do you mean?'}, [{'role': 'agent', 'text': 'Which title do you mean?'}]) or {}).get('flags') == [OS.FLAG_REPEAT])
    check("a quote already given is not an order left open",
          OS.observe('thanks', plain, [{'role': 'agent', 'text': 'Pulling this data will cost $100.', 'meta': {'panel_offer': {'subject': 'X'}}}]) is None)


# ------------------------------------------------------------------ B
def test_b_ask_log():
    print("B. the flag rides the ask-log record")
    hist = [{'role': 'user', 'text': 'yes'}, agent(wiz(W.ASK_ASSETS, W.STEP_ASSETS))]
    payload = wiz(W.ASK_ASSETS, W.STEP_ASSETS)
    extra = {'result': 'answered', 'trace_id': 't1', 'thread': {'id': 'daedaedbce'}, 'reading': {}, 'user_signal': 'x',
             'rejects': 'y', 'approved_offer': 'z', 'decision': 'd', 'ninth': 9}
    out = OS.stamp(extra, 'find the campaign asset', payload, hist)
    check("the order's standing leads the extras (the log keeps eight)", list(out)[0] == 'order')
    check("in the short form the log keeps", out['order'] == 'brand_partnership|open|assets|repeated_question' and len(out['order']) <= 200)
    check("it decodes back", OS.decode(out['order']) == {'flow': 'brand_partnership', 'state': 'open', 'step': 'assets', 'flags': ['repeated_question']})
    check("the payload the question copy is built from carries it too", payload.get('order_stall', {}).get('flags') == ['repeated_question'])
    check("the caller's extras are not mutated", 'order' not in extra)
    same = {'result': 'answered'}
    check("no order, no stamp", OS.stamp(same, 'hi', {'reply': 'Hello.'}, []) is same)
    # the log's own trim keeps it
    import render_usage_log as RUL
    kept = {}
    old = RUL._put_ask_record_safe
    RUL._put_ask_record_safe = lambda rec: kept.update(rec)
    old_sync = RUL._SYNC_FOR_TESTS
    RUL._SYNC_FOR_TESTS = True
    try:
        RUL.record_ask(user='wallet_seat', view='profileIQ', question='find the campaign asset', surface='analyze',
                       route='bpiq_wizard', outcome='turn', ms=12, extra=out, reply=W.ASK_ASSETS)
    finally:
        RUL._put_ask_record_safe = old
        RUL._SYNC_FOR_TESTS = old_sync
    check("the written record carries extra.order", (kept.get('extra') or {}).get('order') == 'brand_partnership|open|assets|repeated_question')
    legacy = (WEB / 'prometheus' / 'legacy' / 'chat.py').read_text(encoding='utf-8')
    i = legacy.index("order_stall', fromlist=['stamp']).stamp(extra, question, payload, ask_history)")
    check("the stamp is taken just before the record is written, and before the question copy is sent",
          i < legacy.index('_rul.record_ask(', i) < legacy.index('_pm_watch_notify(', i))


# ------------------------------------------------------------------ C
def test_c_question_copy():
    print("C. the existing question copy marks its subject; no new email, the same recipients")
    sent = []
    old = PWN._ses_send
    PWN._ses_send = lambda subject, text, html: sent.append({'subject': subject, 'text': text, 'html': html})
    old_link = PWN._correct_link
    PWN._correct_link = lambda *a, **k: ''
    try:
        hist = [{'role': 'user', 'text': W.CHIP}, agent(wiz(W.ASK_TALENT, W.STEP_TALENT))]
        payload = {'success': True, 'action': 'answer', 'reply': 'The Gap x JYT partnership reaches 2,184,637 people.',
                   'intake_resume': 'brand_partnership', 'followups': ['Back to the valuation']}
        OS.stamp({}, HER, payload, hist)
        PWN.send_single('wallet_seat', RECORD, HER, payload, subject='Gap Customers', surface='analyze')
        m = sent[-1]
        check("the subject is marked", m['subject'].startswith('ORDER STALLED (general answer while the order was open): '))
        check("the rest of the subject is what it always was", m['subject'].endswith('Agency Co: Sam Reader asked Prometheus'))
        check("the body says which order and where it stopped",
              'ORDER CHECK\nBrand Partnership valuation: general answer while the order was open. It stopped at the talent.' in m['text']
              and 'Order check' in m['html'])
        check("the question and the answer are still there", HER in m['text'] and '2,184,637' in m['text'])
        plain = {'success': True, 'action': 'answer', 'reply': 'Gap Customers skew 60.4% female.'}
        OS.stamp({}, 'who are gap customers', plain, [])
        PWN.send_single('wallet_seat', RECORD, 'who are gap customers', plain, subject='Gap Customers', surface='analyze')
        check("an ordinary ask is unmarked", sent[-1]['subject'] == 'Agency Co: Sam Reader asked Prometheus'
              and 'ORDER CHECK' not in sent[-1]['text'])
        recap = wiz("Here's what I'll run:\nJYT\nJYT x Gap\nThis is $500.", W.STEP_CHARGE, bpiq_confirm_payload={'qualifier': 'JYT'})
        OS.stamp({}, 'find them', recap, [])
        PWN.send_single('wallet_seat', RECORD, 'find them', recap, surface='analyze')
        check("an order that reached its quote is unmarked", not sent[-1]['subject'].startswith('ORDER STALLED'))
        n = len(sent)
        PWN.notify('canary:probe', RECORD, HER, payload, surface='analyze')
        PWN.notify('canary:valguard', {'email': ''}, HER, payload, surface='analyze')
        check("a probe never mails, stalled or not", len(sent) == n)
    finally:
        PWN._ses_send = old
        PWN._correct_link = old_link
    check("the recipients are unchanged", PWN._TO == 'jenna@crosswalknyc.com'
          and PWN._BCC == ('liz@crosswalknyc.com', 'jessie@crosswalknyc.com'))
    src = (WEB / 'prometheus_watch_notify.py').read_text(encoding='utf-8')
    check("one sender, one send function: no new email was added", src.count('send_raw_email(') == 1 and src.count('def _ses_send(') == 1)
    check("the mail guard still stands in front of the send", 'sending_allowed()' in src)
    check("the subject mark reads plainly", OS.subject_mark('X asked Prometheus', [OS.FLAG_REPEAT]) == 'ORDER STALLED (repeated question): X asked Prometheus'
          and OS.subject_mark('X asked Prometheus', []) == 'X asked Prometheus')


# ------------------------------------------------------------------ D
def _walk(turns, flush_outcome=None):
    """Run (question, payload) turns through the bundle; returns the sends."""
    store = B.MemoryDocs()
    sends = []

    def send_fn(kind, **kw):
        sends.append(dict(kw, kind=kind))

    hist = []
    now = 1_000_000.0
    for q, payload in turns:
        OS.stamp({}, q, payload, hist)
        B.handle_notify('wallet_seat', RECORD, q, payload, thread_id='t1', store=store, send_fn=send_fn, now=now)
        hist += [{'role': 'user', 'text': q}, agent(payload) if payload.get('followups') is not None else {'role': 'agent', 'text': payload.get('reply')}]
        now += 30
    if flush_outcome:
        B.flush_idle(store=store, now=now + B.IDLE_S + 5,
                     send_fn=lambda kind, **kw: sends.append(dict(kw, kind=kind, stall=B.walk_stall(kw.get('bundle'), kw.get('outcome')))))
    return sends


def test_d_walk_email():
    print("D. a walk's one email says when the walk stalled")
    assets = wiz(W.ASK_ASSETS, W.STEP_ASSETS, chips=("I'll paste links", 'Find the assets', 'Cancel'))
    her_walk = [
        (W.CHIP, wiz(W.ASK_TALENT, W.STEP_TALENT)),
        ('gap x jyt', wiz(W.ASK_DATES, W.STEP_DATES)),
        ('9/22/26 to today', wiz('gap x jyt: I read the campaign as September 22 to November 11, 2026.', W.STEP_ATTR, chips=('Accept', 'Cancel'))),
        ('yes', wiz(W.ASK_CONTROL, W.STEP_CONTROL, chips=('Yes', 'No', 'Cancel'))),
        ('yes', assets),
        ('find the campaign asset', assets),
    ]
    sends = _walk(her_walk, flush_outcome='Walk ended')
    check("her walk mails once, when it goes idle", len(sends) == 1 and sends[0]['kind'] == 'walk')
    st = sends[0].get('stall') or {}
    check("the walk is marked: the question asked twice, and no quote reached",
          OS.FLAG_REPEAT in (st.get('flags') or []) and OS.FLAG_NO_QUOTE in (st.get('flags') or []))
    check("it says which order and where it stopped", st.get('flow') == 'brand_partnership' and st.get('step') == 'assets')
    check("the subject would lead with the repeat", OS.subject_mark('Agency Co: Sam Reader asked Prometheus', st)
          == 'ORDER STALLED (repeated question): Agency Co: Sam Reader asked Prometheus')
    check("the body line names both", OS.order_block(st)
          == 'Brand Partnership valuation: repeated question, did not reach a quote. It stopped at the campaign assets.')
    # the real sender marks a walk the same way
    sent = []
    old = PWN._ses_send
    PWN._ses_send = lambda subject, text, html: sent.append({'subject': subject, 'text': text})
    old_link = PWN._correct_link
    PWN._correct_link = lambda *a, **k: ''
    try:
        PWN.send_walk('wallet_seat', RECORD, [{'q': q, 'a': p['reply']} for q, p in her_walk], 'Walk ended', stall=st)
        check("the walk email's subject is marked and its body carries the line",
              sent[-1]['subject'].startswith('ORDER STALLED (repeated question): ') and 'ORDER CHECK' in sent[-1]['text']
              and 'OUTCOME\nWalk ended' in sent[-1]['text'])
        PWN.send_walk('wallet_seat', RECORD, [{'q': 'a', 'a': 'b'}], 'Accepted $500, running')
        check("a walk with nothing wrong is unmarked", sent[-1]['subject'] == 'Agency Co: Sam Reader asked Prometheus')
    finally:
        PWN._ses_send = old
        PWN._correct_link = old_link
    # a walk that reaches its quote and is accepted
    recap = wiz("Here's what I'll run:\nJYT\nJYT x Gap\nThis is $500.", W.STEP_CHARGE, chips=('Accept $500', 'Decline', 'Cancel'),
                bpiq_confirm_payload={'qualifier': 'JYT'}, bpiq_collect=False)
    good = her_walk[:5] + [('find them', recap),
                           ('Accept $500', {'success': True, 'action': 'answer', 'reply': W.RUNNING_REPLY, 'bpiq_job_id': 'a' * 12, 'followups': []})]
    sends = _walk(good)
    check("a walk that reached its quote and was accepted mails once, unmarked",
          len(sends) == 1 and sends[0]['kind'] == 'walk' and not sends[0].get('stall') and 'Accepted' in sends[0]['outcome'])
    # a walk the reader cancelled is the reader's choice
    sends = _walk(her_walk[:3] + [('Cancel', {'success': True, 'action': 'answer', 'reply': W.CANCEL_REPLY, 'followups': []})])
    check("a walk the reader cancelled is unmarked", len(sends) == 1 and not sends[0].get('stall') and sends[0]['outcome'] == 'Cancelled')
    # a walk left on another message, with no quote
    plain = {'success': True, 'action': 'answer', 'reply': 'Gap Customers skew 60.4% female.', 'followups': []}
    sends = _walk(her_walk[:4] + [('who are gap customers', plain)])
    walk = [s for s in sends if s['kind'] == 'walk']
    check("a walk left on another message mails the walk, marked: it did not reach a quote",
          len(walk) == 1 and OS.FLAG_NO_QUOTE in ((walk[0].get('stall') or {}).get('flags') or []))
    check("and the message that left it still mails on its own", any(s['kind'] == 'single' for s in sends))
    check("walk_flags leaves an accepted or cancelled walk alone",
          OS.walk_flags([{'q': 'a', 'a': "Here's what I'll run:\nThis is $500."}], 'Accepted $500, running', 'brand_partnership') == []
          and OS.walk_flags([{'q': 'a', 'a': W.ASK_TALENT}], 'Cancelled', 'brand_partnership') == [])
    for text in (OS.subject_mark('x', [OS.FLAG_REPEAT]), OS.order_block({'flags': [OS.FLAG_NO_QUOTE], 'flow': 'flywheel'})):
        check(f"the copy is plain: {text[:50]!r}", '\u2014' not in text and not re.search(r"synth|model|pipeline", text, re.I))


if __name__ == '__main__':
    test_a_observe()
    test_b_ask_log()
    test_c_question_copy()
    test_d_walk_email()
    print()
    if FAIL:
        print(f"FAIL ({FAIL})")
        sys.exit(1)
    print("OK")
