#!/usr/bin/env python3
"""One Prometheus ask-copy email per full walk (Jenna 2026-10-28).

A wizard or other guided intake holds every tap and sends one note
when the walk ends. Single-turn asks still send once. Canary users
send none. Hermetic: no SES, no Flask boot.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))

from prometheus import ask_email_bundle as B  # noqa: E402
from prometheus import bpiq_wizard as W  # noqa: E402
import prometheus_watch_notify as pwn  # noqa: E402

TODAY = date(2026, 10, 28)
FAIL = 0

RECORD = {
    'email': 'jenna@crosswalknyc.com',
    'company': 'Crosswalk',
    'first_name': 'Jenna',
    'last_name': 'Menking',
}


def check(label, cond):
    global FAIL
    if cond:
        print(f"  ok  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}")


def mailer():
    sent = []

    def send_fn(kind, **kw):
        sent.append((kind, kw))

    return sent, send_fn


def step(text, wiz, docs, sent_fn, first=None, charge_fn=None, start_fn=None):
    out = W.analyze_hook(
        text, {'thread_id': 't1'}, [],
        {'username': 'admin', 'email': RECORD['email']},
        {}, 'admin',
        {'_pm_bpiq_intent': W.is_chip},
        first=first, today=TODAY, store=wiz,
        charge_fn=charge_fn, start_fn=start_fn)
    pwn.notify('admin', RECORD, text, out or {}, thread_id='t1',
               store=docs, send_fn=sent_fn)
    return out


def walk_to_charge(wiz, docs, sent_fn, control=True):
    step(W.CHIP, wiz, docs, sent_fn, first=True)
    step('Jennifer Beals', wiz, docs, sent_fn)
    step('Ganni x Lululemon', wiz, docs, sent_fn)
    step('October 7 - 28', wiz, docs, sent_fn)
    step('Accept', wiz, docs, sent_fn)
    step('Yes' if control else 'No', wiz, docs, sent_fn)
    step(W.CHIP_FIND, wiz, docs, sent_fn)


def test_full_wizard_accept_is_one_email():
    sent, send_fn = mailer()
    wiz = W.MemoryStore()
    docs = B.MemoryDocs()
    charged, started = [], []
    walk_to_charge(wiz, docs, send_fn, control=True)
    check("zero emails before Accept", sent == [])
    out = step(W.accept_chip(1000), wiz, docs, send_fn,
               charge_fn=lambda *a: charged.append(a) or True,
               start_fn=lambda *a: started.append(a))
    check("Accept ran", (out or {}).get('reply') == W.RUNNING_REPLY)
    check("exactly one email on Accept",
          len(sent) == 1 and sent[0][0] == 'walk')
    kw = sent[0][1]
    turns = kw.get('turns') or []
    blob = '\n'.join(
        f"{t.get('q')}\n{t.get('a')}" for t in turns)
    check("transcript has the chip", W.CHIP in blob)
    check("transcript has Jennifer Beals", 'Jennifer Beals' in blob)
    check("transcript has the campaign", 'Ganni x Lululemon' in blob)
    check("transcript has the dates", 'October 7' in blob)
    check("transcript has Yes", 'Yes' in blob)
    check("transcript has Find the assets", W.CHIP_FIND in blob)
    check("transcript has Accept $1,000", 'Accept $1,000' in blob)
    check("outcome is Accepted $1,000, running",
          kw.get('outcome') == 'Accepted $1,000, running')
    check("turns are in order (chip then talent)",
          turns[0]['q'] == W.CHIP and turns[1]['q'] == 'Jennifer Beals')
    check("no leftover bundle",
          not (docs.load_doc('admin').get('ask_bundles') or {}))


def test_decline_and_cancel_each_send_one():
    for label, tap, outcome, reply in (
        ('Decline', W.CHIP_DECLINE, 'Declined', W.DECLINE_REPLY),
        ('Cancel', W.CHIP_CANCEL, 'Cancelled', W.CANCEL_REPLY),
    ):
        sent, send_fn = mailer()
        wiz = W.MemoryStore()
        docs = B.MemoryDocs()
        walk_to_charge(wiz, docs, send_fn, control=True)
        check(f"{label}: still silent before the tap", sent == [])
        out = step(tap, wiz, docs, send_fn)
        check(f"{label}: reply matches", (out or {}).get('reply') == reply)
        check(f"{label}: exactly one email",
              len(sent) == 1 and sent[0][0] == 'walk')
        check(f"{label}: outcome", sent[0][1].get('outcome') == outcome)
        blob = '\n'.join(
            f"{t.get('q')}\n{t.get('a')}" for t in (sent[0][1].get('turns') or []))
        check(f"{label}: transcript still has Jennifer Beals",
              'Jennifer Beals' in blob)


def test_single_turn_still_sends_one():
    sent, send_fn = mailer()
    docs = B.MemoryDocs()
    pwn.notify(
        'admin', RECORD, 'How many people stream Obsession?',
        {'reply': 'Obsession reached 19,847,331 viewers.'},
        thread_id='solo', store=docs, send_fn=send_fn)
    check("single-turn ask sends one email",
          len(sent) == 1 and sent[0][0] == 'single')
    check("single-turn question is the ask",
          sent[0][1].get('question') == 'How many people stream Obsession?')


def test_abandoned_draft_sends_on_unrelated():
    sent, send_fn = mailer()
    wiz = W.MemoryStore()
    docs = B.MemoryDocs()
    step(W.CHIP, wiz, docs, send_fn, first=True)
    step('Jennifer Beals', wiz, docs, send_fn)
    check("held after two wizard taps", sent == [])
    pwn.notify(
        'admin', RECORD, 'Who watches Yellowstone?',
        {'reply': 'Yellowstone leans 25 to 54 and west of the Mississippi.'},
        thread_id='t1', store=docs, send_fn=send_fn)
    kinds = [k for k, _ in sent]
    check("abandoned walk then the new ask",
          kinds == ['walk', 'single'])
    check("abandoned outcome is Walk ended",
          sent[0][1].get('outcome') == 'Walk ended')
    blob = '\n'.join(
        f"{t.get('q')}\n{t.get('a')}" for t in (sent[0][1].get('turns') or []))
    check("abandoned transcript has the taps so far",
          W.CHIP in blob and 'Jennifer Beals' in blob)
    check("the new ask is its own email",
          sent[1][1].get('question') == 'Who watches Yellowstone?')


def test_idle_flush_sends_one():
    sent, send_fn = mailer()
    wiz = W.MemoryStore()
    docs = B.MemoryDocs()
    now = 1_000_000.0
    pwn.notify('admin', RECORD, W.CHIP,
               W.analyze_hook(W.CHIP, {'thread_id': 't1'}, [], 'admin', {},
                              'admin', {'_pm_bpiq_intent': W.is_chip},
                              first=True, today=TODAY, store=wiz) or {},
               thread_id='t1', store=docs, send_fn=send_fn, now=now)
    check("idle walk has not mailed yet", sent == [])
    n = B.flush_idle(store=docs, now=now + B.IDLE_S + 5, send_fn=send_fn)
    check("idle sweep flushed one", n == 1)
    check("idle email is the walk",
          len(sent) == 1 and sent[0][0] == 'walk')
    check("idle outcome is Walk ended",
          sent[0][1].get('outcome') == 'Walk ended')
    n2 = B.flush_idle(store=docs, now=now + B.IDLE_S + 50, send_fn=send_fn)
    check("idle sweep is once", n2 == 0 and len(sent) == 1)


def test_canary_sends_none():
    sent, send_fn = mailer()
    docs = B.MemoryDocs()
    pwn.notify(
        'canary:golden', RECORD, W.CHIP,
        {'reply': W.ASK_TALENT, 'bpiq_collect': True,
         'bpiq_wizard': {'step': 'talent'}},
        thread_id='t1', store=docs, send_fn=send_fn)
    pwn.notify(
        'canary:golden', RECORD, 'How many people stream Obsession?',
        {'reply': 'Obsession reached 19,847,331 viewers.'},
        thread_id='solo', store=docs, send_fn=send_fn)
    check("canary users send none", sent == [])


def test_notify_skips_placeholder():
    sent, send_fn = mailer()
    docs = B.MemoryDocs()
    pwn.notify(
        'admin', RECORD, 'Read Landman',
        {'reply': 'On it. Building the read now.', 'read_job_id': 'abc'},
        thread_id='r1', store=docs, send_fn=send_fn)
    check("placeholder still skips", sent == [])


if __name__ == '__main__':
    print('Ask-copy walk bundles')
    test_full_wizard_accept_is_one_email()
    test_decline_and_cancel_each_send_one()
    test_single_turn_still_sends_one()
    test_abandoned_draft_sends_on_unrelated()
    test_idle_flush_sends_one()
    test_canary_sends_none()
    test_notify_skips_placeholder()
    if FAIL:
        print(f'\n{FAIL} FAILURE(S)')
        sys.exit(1)
    print('\nOK')
