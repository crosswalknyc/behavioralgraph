#!/usr/bin/env python3
"""No priced order stalls silently (Jenna 2026-11-11, after Samantha
Kolton's valuation order was left for a general read at its first step,
asked one question twice in the same words at its last, and never
reached its quote). Hermetic replays, one battery per priced or
approval-gated order Prometheus takes:

  1. Brand Partnership valuation   (the step wizard, $500 / $1,000)
  2. Flywheel                      (the step wizard, $500)
  3. Digital Journey               (the brief intake, $500)
  4. Attribution IQ campaign       (the brief intake, setup + daily)
  5. Digital Twin                  (the brief intake)
  6. Custom read / sizing offer    (the quote is the first reply)
  7. Deck                          (a pick builds on the pick or says so)
  8. Profile build                 (an order reaches the build flow)

Each walk is natural phrasing: full sentences that answer several steps
at once, "you find it", "not sure", "same as before", typos, a yes or a
no at the wrong step, a question in the middle. Each asserts the order
reaches its quote, never asks the same question twice in the same
words, never stores a yes or a no as a name, and never leaves the order
for a general answer while it is open unless the reader cancels or asks
about something else.

The brief intakes (3 to 5) read a first message with the model and fall
back to rules; with no model here the rules read them, which is the
path a model outage takes live.
"""
from __future__ import annotations

import os
import re
import sys
from datetime import date
from pathlib import Path
from unittest import mock

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))


class _NoAppModule:
    def find_spec(self, name, path=None, target=None):
        if name == 'app':
            raise ImportError('app is not importable inside this hermetic suite')
        return None


sys.meta_path.insert(0, _NoAppModule())

from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import flywheel_wizard as FW  # noqa: E402
from prometheus import intake_guard as G  # noqa: E402
from prometheus import order_stall as OS  # noqa: E402
from prometheus import deck_pick as DP  # noqa: E402
from prometheus import partnership_roles as ROLES  # noqa: E402

# what the library knows about these names, as it does live (a hermetic
# run reads no library): which side of a pairing is the talent comes from
# this, never from the order the names were written in
ROLES.KIND_LOOKUP = {'gap': 'brand', 'nike': 'brand', 'gunna': 'talent', 'lowes': 'brand', 'target': 'brand',
                     'stanley': 'brand', 'glenpowell': 'talent'}.get
from migration.bpiq_synthesis import MemoryRegistry  # noqa: E402

TODAY = date(2026, 11, 11)
FAIL = 0
FILLER = {'yes', 'no', 'ok', 'okay', 'sure', 'yep', 'nope', 'not sure', 'same as before', 'idk', 'you find it', 'find it',
          'maybe', 'hmm', 'thanks', 'accept', 'acept', 'yse', 'i guess', "i'm not sure", 'whatever'}


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def norm(t):
    return ' '.join(str(t or '').split()).lower()


def no_repeat(replies):
    return all(norm(replies[i]) != norm(replies[i - 1]) for i in range(1, len(replies)))


def thread_no_repeat(hist):
    """No two agent turns in a row say the same thing (the reader's view
    of the thread: a step asked again after a question was answered in
    between is the way back, not a repeat)."""
    agent = [norm(t.get('text')) for t in hist if t.get('role') == 'agent']
    return all(agent[i] != agent[i - 1] for i in range(1, len(agent)))


# ================================================================ 1
class ValWalk:
    def __init__(self, tid='v1'):
        self.user, self.tid = 'canary:orders', tid
        self.store = W.MemoryStore()
        self.registry = MemoryRegistry()
        self.hist, self.replies, self.charged, self.left = [], [], [], []

    def say(self, text):
        G.reset_resume()
        body = {'thread_id': self.tid}
        out = None
        if W.wants(text, body, self.hist, store=self.store, user=self.user, thread_id=self.tid):
            out = W.analyze_hook(text, body, list(self.hist), self.user, {}, self.user, {}, first=W.is_start(text),
                                 today=TODAY, store=self.store, charge_fn=lambda *a: self.charged.append(a) or True,
                                 start_fn=lambda *a: None, registry=self.registry)
        self.hist.append({'role': 'user', 'text': text})
        if out is None:
            # the service would answer this elsewhere; the reply carries the way back
            self.left.append(text)
            self.hist.append({'role': 'agent', 'text': 'About 412,870 people.',
                              'meta': {'options': [{'label': W.RESUME_CHIP, 'send': W.RESUME_CHIP}]}})
        else:
            self.hist.append({'role': 'agent', 'text': out['reply'],
                              'meta': {'options': [{'label': c, 'send': c} for c in (out.get('followups') or [])]}})
            self.replies.append(out['reply'])
        return out

    def draft(self):
        return self.store.load(self.user, self.tid) or {}


VAL_WALKS = (
    ('her own sequence', 1000, (), [
        W.CHIP, 'JYT is the talent and GAP is the brand. the partnership started in September 2026',
        '9/22/26 to today', 'yes', 'yes', 'find the campaign asset']),
    ('one sentence, then the day', 500, (), [
        W.CHIP, 'the partnership between JYT and Gap started in September 2026', 'the 22nd', 'Accept', 'no', 'you find it']),
    ('a one-shot ask with its dates', 500, (), [
        'can you value Gunna x Nike? the campaign ran June 3 to August 20, 2026', 'Accept', 'no thanks', 'not sure']),
    ('yes and no at the wrong steps', 500, (), [
        W.CHIP, 'yes', 'JYT', 'no', 'Gap x JYT', '9/22/26 to today', 'ok', 'no', "I don't have one"]),
    ('typos', 1000, (), [
        W.CHIP, 'jyt', 'gap x jyt', 'sept 22 2026 - nov 11 2026', 'acept', 'acept', 'yse', 'yes', 'find them']),
    ('not sure, twice, at every step that has a default', 500, (), [
        W.CHIP, 'JYT is the talent and Gap is the brand, it started in September 2026', 'not sure', 'not sure',
        'hmm', 'hmm', 'no', 'maybe', 'maybe']),
    ('same as before with nothing before, then the names', 500, (), [
        W.CHIP, 'same as before', 'JYT', 'same as before', 'Gap x JYT', 'October 7 - 28', 'Accept', 'No', 'Find the assets']),
    ('a remark instead of a name', 500, (), [
        W.CHIP, "it's the back to school thing they did with the mall tour and the capsule collection this fall",
        'JYT', 'the one with Gap that everybody was talking about on tiktok for weeks in the fall', 'Gap x JYT',
        'it ran from September 22 through today', 'Accept', 'no', 'use whatever you can find']),
    ('a question in the middle, then back', 500, ('how many people watched Landman on Paramount+ last month?',), [
        W.CHIP, 'JYT', 'how many people watched Landman on Paramount+ last month?', W.RESUME_CHIP, 'Gap x JYT',
        '9/22/26 to today', 'Accept', 'no', 'find it']),
    ('what it is worth, asked in the middle', 500, (), [
        W.CHIP, 'JYT is the talent and Gap is the brand', 'what is it worth?', 'September 22 to today', 'Accept', 'no',
        'find them']),
    ('pasted links', 1000, (), [
        W.CHIP, 'Your Friends & Neighbors', 'Pure Insurance x Your Friends & Neighbors', '4/3/26-7/3/26', 'Accept', 'Yes',
        'https://www.youtube.com/watch?v=a-RDIOWM6-Q, https://www.linkedin.com/posts/pure_insurance_watch']),
    ('a different window', 500, (), [
        W.CHIP, 'JYT', 'Gap x JYT', 'September 22 to October 20, 2026', 'Use a different window',
        'September 22 to November 5, 2026', 'no', 'find them']),
    ('a cold question about what it is worth', 500, (), [
        'what is the Gap x JYT partnership worth', 'JYT is the talent', 'it started on 9/22/26', 'Accept', 'without one',
        'find every applicable campaign asset']),
)


def test_1_valuation():
    print("1. Brand Partnership valuation: every walk reaches its quote")
    for name, usd, may_leave, msgs in VAL_WALKS:
        w = ValWalk()
        out = None
        for m in msgs:
            out = w.say(m) or out
        d = w.draft()
        check(f"[{name}] reaches the quote, ${usd:,}", d.get('step') == W.STEP_CHARGE and f"This is ${usd:,}." in w.replies[-1])
        check(f"[{name}] no question in the same words twice", thread_no_repeat(w.hist))
        check(f"[{name}] no yes or no stored as a name",
              all(norm(d.get(k)) not in FILLER for k in ('talent', 'campaign', 'brand_partner')))
        check(f"[{name}] never left the order for a general answer", sorted(w.left) == sorted(may_leave))
        check(f"[{name}] nothing charged before Accept", not w.charged)
        obs = [OS.observe(msgs[-1], {'reply': w.replies[-1], 'bpiq_wizard': d, 'bpiq_confirm_payload': {}}, w.hist[:-2])]
        check(f"[{name}] the last reply reads as the quote", obs[0] and obs[0]['state'] == 'quote' and not obs[0].get('flags'))
    # a long run of non-answers: the order never loops on one wording and never leaves
    w = ValWalk()
    w.say(W.CHIP)
    for m in ('ok', 'hmm', 'sure', 'whatever', 'i guess', 'you find it', 'maybe', 'not sure', 'yes', 'no'):
        w.say(m)
    check("ten non-answers at the first step: never the same words twice, never a name, never left",
          no_repeat(w.replies) and not w.draft().get('talent') and not w.left)
    # the reader cancels: the order is gone and says so once
    w = ValWalk()
    for m in (W.CHIP, 'JYT', 'Cancel'):
        out = w.say(m)
    check("Cancel drops the order", out['reply'] == W.CANCEL_REPLY and not w.draft())
    # a question on something else is the one way out, and the way back is on the reply
    w = ValWalk()
    for m in (W.CHIP, 'JYT'):
        w.say(m)
    out = w.say('what are the top shows on Netflix right now?')
    check("a question about something else is answered on its own, the order stays open at its step",
          out is None and w.draft().get('step') == W.STEP_CAMPAIGN and G.pending_resume() and G.pending_resume()['flow'] == W.FLOW)
    out = w.say('please describe what you believe the task is so I know we are clear')
    check("a request typed as an instruction is let through too (it is an ask, not an answer)", out is None)
    out = w.say('honestly I would have to check with the team on that one and get back to you tomorrow')
    check("a remark is not: the order keeps it and asks its step in other words", out is not None
          and out['reply'] in (W.ASK_CAMPAIGN, W.ASK_CAMPAIGN_ALT))


# ================================================================ 2
class FwWalk:
    def __init__(self, tid='f1'):
        self.user, self.tid = 'canary:orders', tid
        self.store = W.MemoryStore()
        self.hist, self.replies, self.left = [], [], []
        self.payload = None

    def say(self, text, first=None):
        G.reset_resume()
        body = {'thread_id': self.tid, 'fw_inputs': bool(self.hist)}
        out = FW.analyze_hook(text, body, list(self.hist), self.user, {}, self.user, {}, first=first, store=self.store,
                              price_label='$500', today=TODAY)
        self.hist.append({'role': 'user', 'text': text})
        if out is None:
            self.left.append(text)
            self.hist.append({'role': 'agent', 'text': 'About 412,870 people.',
                              'meta': {'options': [{'label': 'Back to the flywheel', 'send': 'Back to the flywheel'}]}})
        else:
            self.payload = out
            self.hist.append({'role': 'agent', 'text': out['reply'],
                              'meta': {'options': [{'label': c, 'send': c} for c in (out.get('followups') or [])],
                                       **({'fw_collect': True} if out.get('fw_collect') else {})}})
            self.replies.append(out['reply'])
        return out


FW_WALKS = (
    ('step by step', (), ['Build a Flywheel', 'watched Gilmore Girls on Prime Video', 'Amazon', 'Trailing 12 months']),
    ('one sentence that answers every step', (), [
        'run a flywheel for people who watched Gilmore Girls on Prime Video across the Amazon ecosystem in the trailing 12 months']),
    ('yes to the proposed company, then not sure about the window', (), [
        'Build a Flywheel', 'bought a Ring doorbell on Amazon', 'yes', 'not sure', 'not sure']),
    ('a non-answer at the company step, twice', (), [
        'Build a Flywheel', 'watched Reacher on Prime Video', 'hmm what do you think is best here honestly', 'you pick',
        'calendar 2025']),
    ('typos and a window typed out', (), [
        'Build a Flywheel', 'wathced Gilmore Girls on Prime Video', 'amazn', 'Amazon', 'march 1 2026 to august 31 2026']),
    ('other, then a named company', (), ['Build a Flywheel', 'watched Bluey on Disney+', 'Other', 'Disney', 'Trailing 12 months']),
    ('a question in the middle, then back', ('how many people watched Landman on Paramount+ last month?',), [
        'Build a Flywheel', 'watched Gilmore Girls on Prime Video', 'how many people watched Landman on Paramount+ last month?',
        'Back to the flywheel', 'Amazon', 'trailing 12 months']),
    ('a remark at the window step', (), [
        'Build a Flywheel', 'watched Gilmore Girls on Prime Video', 'Amazon',
        'honestly I would have to check with the team on that one and get back to you tomorrow',
        'I really am not sure what window they want for this one to be honest with you']),
)


def test_2_flywheel():
    print("2. Flywheel: every walk reaches its quote")
    for name, may_leave, msgs in FW_WALKS:
        w = FwWalk()
        for i, m in enumerate(msgs):
            w.say(m, first=True if i == 0 else None)
        p = w.payload or {}
        check(f"[{name}] reaches the confirm card with its price", bool(p.get('fw_confirm_payload'))
              and 'It prices at $500. Run it?' in p['reply'] and p['followups'][0] == FW.RUN_CHIP)
        check(f"[{name}] no question in the same words twice", thread_no_repeat(w.hist))
        inp = p.get('fw_confirm_payload') or {}
        check(f"[{name}] no yes or no stored as the entry or the company",
              norm(inp.get('entry_point')) not in FILLER and norm(inp.get('ecosystem')) not in FILLER
              and norm(inp.get('subject')) not in FILLER)
        check(f"[{name}] never left the order for a general answer", sorted(w.left) == sorted(may_leave))
        check(f"[{name}] the card reads as a quote", (OS.observe(msgs[-1], p, w.hist[:-2]) or {}).get('state') == 'quote')
    # a default is said, never taken silently
    w = FwWalk()
    for i, m in enumerate(['Build a Flywheel', 'bought a Ring doorbell on Amazon', 'yes', 'not sure', 'not sure']):
        out = w.say(m, first=True if i == 0 else None)
    check("the window default is said in one plain line", out['reply'].startswith("I'll use the trailing 12 months."))
    w = FwWalk()
    for i, m in enumerate(['Build a Flywheel', 'watched Reacher on Prime Video', 'hmm what do you think is best here honestly', 'you pick']):
        out = w.say(m, first=True if i == 0 else None)
    check("the company default is the one the entry belongs to, said in one plain line",
          out['reply'].startswith("I'll go with Amazon, the company the entry point belongs to."))
    # yes / no at the entry step is never the entry
    w = FwWalk()
    w.say('Build a Flywheel', first=True)
    a = w.say('yes')
    b = w.say('ok')
    check("a yes at the entry step is asked again, in other words the second time",
          a and b and a['reply'] != b['reply'] and not (w.store.load(w.user, w.tid) or {}).get('entry_point'))
    w = FwWalk()
    for i, m in enumerate(['Build a Flywheel', 'watched Gilmore Girls on Prime Video', 'Cancel']):
        out = w.say(m, first=True if i == 0 else None)
    check("Cancel drops the order and says nothing was charged", out['reply'] == FW.CANCEL_REPLY and not w.store.load(w.user, w.tid))


# ================================================================ 3-5
USERS = {'payer': {'username': 'payer', 'email': 'payer@agency.com', 'company': 'Agency', 'credits': 0,
                   'paying_customer': True, 'role': 'user'}}


class _FakeHost:
    S3_BUCKET = 'dashboard-inputs'

    @staticmethod
    def _normalize_for_match(s):
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]+", " ", str(s or '').lower())).strip()

    @staticmethod
    def load_users():
        return {'users': {k: dict(v) for k, v in USERS.items()}}

    @staticmethod
    def _session_money_symbol(*a, **k):
        return '$'

    def __getattr__(self, name):
        return mock.MagicMock(name=name)


def _legacy_chat():
    import prometheus.legacy as _legacy
    _legacy.bind(_FakeHost())
    from prometheus.legacy import chat as C
    C.session = {'username': 'payer'}
    return C


def _brief_walk(C, flow, flag, msgs):
    hist, outs = [], []
    for i, m in enumerate(msgs):
        G.reset_resume()
        out = C._pm_intake_turn(flow, m, {flag: i > 0, 'thread_id': 'b1'}, list(hist), dict(USERS['payer']), {}, first=(i == 0))
        hist.append({'role': 'user', 'text': m})
        hist.append({'role': 'agent', 'text': str((out or {}).get('reply') or '')})
        outs.append(out or {})
    return outs, hist


def test_3_to_5_brief_intakes():
    print("3-5. Digital Journey, Attribution IQ and Digital Twin: a brief reaches its confirm card")
    C = _legacy_chat()
    # 3. Digital Journey
    outs, hist = _brief_walk(C, 'digital_journey', 'jiq_inputs', [
        'Build a digital journey for Young Sheldon on Netflix, start from a social post and end at watching the first episode'])
    card = outs[-1]
    check("journey: a one-sentence order reaches the confirm card in one turn", bool(card.get('jiq_confirm_payload'))
          and card['reply'].startswith("Here's the Digital Journey I'll build") and card['followups'][0] == 'Run the journey')
    check("journey: the card carries its price or the included line", bool(re.search(r"It prices at \$\d|Included with your plan", card['reply'])))
    check("journey: the card names the title and the service, not a yes or a no",
          'Young Sheldon' in card['reply'] and 'Netflix' in card['reply']
          and norm((card.get('jiq_confirm_payload') or {}).get('subject')) not in FILLER)
    check("journey: the card reads as a quote", (OS.observe('x', card, hist[:-2]) or {}).get('state') == 'quote')
    outs, hist = _brief_walk(C, 'digital_journey', 'jiq_inputs', [
        'Create a digital journey', 'Young Sheldon on Netflix, from a social post to the first episode'])
    check("journey: the chip, then one sentence, reaches the card", bool(outs[-1].get('jiq_confirm_payload')))
    check("journey: the opener and the card are not the same words", norm(outs[0].get('reply')) != norm(outs[-1].get('reply')))
    # the direction question is asked once: a non-answer takes the default, never the same question again
    from prometheus import intake_reader as IR
    asked = [{'role': 'agent', 'text': IR.direction_question('Mark Rober Netflix content')}]
    check("journey: the which-way question, once asked, is never asked again in the same words (Debi, 2026-10-21)",
          IR.direction_asked_already(asked) and callable(IR.default_direction))
    # 4. Attribution IQ
    outs, hist = _brief_walk(C, 'attribution', 'aiq_inputs', ['Set up attribution tracking'])
    check("attribution: the chip asks for the brief in one message", bool(outs[0].get('aiq_collect'))
          and 'campaign name' in outs[0]['reply'])
    check("attribution: an open brief reads as an open order", (OS.observe('x', outs[0], []) or {}).get('state') == 'open'
          and (OS.observe('x', outs[0], []) or {}).get('flow') == 'attribution')
    # 5. Digital Twin
    outs, hist = _brief_walk(C, 'digital_twin', 'twin_inputs', ['Build a twin'])
    check("twin: the chip opens the intake", isinstance(outs[0], dict) and bool(outs[0].get('reply')))
    # a brief that asks the same thing twice in the same words is caught on the way out
    from prometheus import ask_outcome as AO
    line = 'Almost there - I still need the campaign name, tagged URLs, the conversion. Send the missing piece(s) and I will line it up.'
    prev = [{'role': 'user', 'text': 'not sure'}, {'role': 'agent', 'text': line}]
    outcome, _detail = AO.classify('analyze', {'success': True, 'action': 'answer', 'reply': line, 'aiq_collect': True}, 200, prev)
    check("a brief's clarifier sent twice in the same words is classed as a repeat (the answer gate holds it)",
          outcome == 'clarified_repeat' and 'clarified_repeat' in AO.ALERT_OUTCOMES)
    obs = OS.observe('not sure', {'success': True, 'action': 'answer', 'reply': line, 'aiq_collect': True}, prev)
    check("and it is flagged on the ask record", obs and OS.FLAG_REPEAT in (obs.get('flags') or []) and obs['flow'] == 'attribution')
    legacy = (WEB / 'prometheus' / 'legacy' / 'chat.py').read_text(encoding='utf-8')
    check("a question typed while a brief collects is answered on its own with the way back",
          "_pm_intake_breakout(text, 'digital_journey')" in legacy and "_pm_intake_breakout(text, 'attribution')" in legacy
          and 'mark_resume(FLOW)' in (WEB / 'prometheus' / 'digital_twin_lane.py').read_text(encoding='utf-8'))


# ================================================================ 6-8
def _bind_service(calls):
    from prometheus.host import host

    def _analyze_core(user, body, text, history):
        calls.append(('analyze', text))
        return {'success': True, 'action': 'answer', 'reply': 'About 412,870 people.', 'followups': []}

    def _interpret_core(user, body, text, history):
        calls.append(('interpret', text))
        return {'success': True, 'spec_draft': {'subject': 'Paw Patrol', 'decision': 'new_build',
                                                 'price_table': {'new_build': 300.0, 'addon_cut': 100.0}},
                'reply': ''}

    host.bind(analyze_core=_analyze_core, interpret_core=_interpret_core,
              deck_core=lambda user, body: (calls.append(('deck', body.get('text'))) or {'success': True, 'reply': 'deck', 'job_id': 'd' * 12}),
              ask_hint=lambda **k: None, gate_analyze=lambda u: True, gate_pull=lambda u: True, funds_gate=lambda u: None,
              gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
              calm_payload=lambda: {'success': False, 'error': 'calm'}, error_email=lambda *a: None, has=lambda c: False,
              load_threads_index=lambda u: {'threads': [{'id': x} for x in 'ABCDEFGH'], 'active': 'A'}, max_threads=50,
              s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None, thread_key=lambda u, t: f'{u}/{t}',
              threads_index_key=lambda u: u, thread_title_from=lambda t: t[:30], bucket='x', s3_client=None,
              job_owner_ok=lambda a, b: True)


def test_6_to_8_one_turn_orders():
    print("6-8. a custom read, a deck and a profile build: the quote or the build flow is the first reply")
    from prometheus import service as S
    calls = []
    _bind_service(calls)
    U = {'username': 'canary:orders', 'role': 'user'}
    page = {'primary': {'s3_key': 'Paige_Spiranac_05_01_2026_10_00.csv', 'name': 'Paige Spiranac'}}
    # 6. a put-together read: the offer with its price is the first reply, before anything runs
    from prometheus import priced_read as PR
    liz_qsr = ("Which QSR brands do Paramount+ subscribers over-index on compared with the US general population? "
               "Give me the top five with the index for each.")
    pctx = {'primary': {'name': 'Paramount+'}}
    seat = {'username': 'payer', 'company': 'Agency', 'role': 'user', 'credits': 40}
    offer = PR.work_gate(liz_qsr, [], pctx, seat, {}, username='payer')
    check("custom read: the first reply is the offer, never the read", isinstance(offer, dict) and bool(offer.get('panel_offer')))
    if isinstance(offer, dict) and offer.get('panel_offer'):
        check("custom read: the offer names its price and carries Approve and Cancel",
              bool(re.search(r"\$\d", offer['reply'])) and 'Approve' in offer['followups'] and 'Cancel' in offer['followups'])
        check("custom read: the offer reads as a quote", (OS.observe(liz_qsr, offer, []) or {}).get('state') == 'quote')
        hist = [{'role': 'user', 'text': liz_qsr},
                {'role': 'agent', 'text': offer['reply'], 'meta': {'panel_offer': offer['panel_offer'],
                                                                   'options': [{'label': c, 'send': c} for c in offer['followups']]}}]
        for word in ('aprove', 'hmm not sure', 'what does that cost again?', 'same as before'):
            check(f"custom read: {word!r} at the offer is not approval", not PR.is_accept(word))
        for word in ('Approve', 'approve', 'Accept', 'yes'):
            check(f"custom read: {word!r} is", PR.is_accept(word))
        again = PR.work_gate(liz_qsr, hist, pctx, seat, {}, username='payer')
        check("custom read: asking the same thing again shows the offer, it never runs unpaid",
              again is None or bool(again.get('panel_offer')) or bool(again.get('audience_confirm')))
    # 7. a deck: the pick builds on the pick, or one line says it cannot; never another subject
    thread = [{'role': 'agent', 'text': 'Visa reaches 61.5% of the Paige Spiranac audience.',
               'meta': {'kind': 'read', 'read_job_id': 'be58d72cd4ca', 'subject': 'Paige Spiranac'}},
              {'role': 'user', 'text': 'Can you turn this into a deck?'},
              {'role': 'agent', 'text': 'Which subject should the deck be on: Paige Spiranac or Gap Customers?'}]

    def library(text, ctx):
        low = str(text).lower()
        if 'paige spiranac' in low:
            return {'ctx': page, 'subject': 'Paige Spiranac', 'partner': '', 'clarify': None}, None
        return {'ctx': ctx, 'subject': 'Paige Spiranac', 'partner': '', 'clarify': None, 'used_open_page': True}, None

    res, err, msg = DP.settle('Paige Spiranac', 'Can you turn this into a deck?', page, thread, library, load_read=lambda j: None)
    check("deck: a picked profile builds on that profile", res and res.get('subject') == 'Paige Spiranac' and not msg)
    res, err, msg = DP.settle('Old Navy x JYT', 'Can you turn this into a deck?', page, thread, library, load_read=lambda j: None)
    check("deck: a pick with nothing to build on starts no deck and says so in one line", res is None and 'Old Navy x JYT' in (msg or '')
          and 'Paige' not in (msg or ''))
    for word in ('yes', 'no', 'ok'):
        res, err, msg = DP.settle(word, 'Can you turn this into a deck?', page, thread, library, load_read=lambda j: None)
        check(f"deck: {word!r} is never built as a subject called {word!r}", res is None or norm(res.get('subject')) != word)
    # 8. a profile build order reaches the build flow, whatever rides behind it
    for text in ('Please queue one Profile IQ: Paw Patrol viewers, trailing 12 months',
                 'build a profile for Paw Patrol', 'run a profile on Paw Patrol please, same window as before'):
        calls.clear()
        env, st = S.ask(U, {'text': text, 'client': 'dashboard', 'history': [], 'thread_id': 'B', 'page_context': None})
        raw = env.get('raw') or {}
        reached = bool(env.get('draft')) or raw.get('action') == 'build_profile' or any(c[0] == 'interpret' for c in calls) \
            or (env.get('decision') or {}).get('surface') == 'interpret'
        check(f"profile build: {text[:44]!r} reaches the build flow", st == 200 and reached
              and not any(c[0] == 'analyze' for c in calls))
    from prometheus import ask_outcome as AO
    draft = {'subject': 'Paw Patrol', 'decision': 'new_build', 'price_table': {'new_build': 300.0, 'addon_cut': 100.0}}
    check("profile build: the brief card is its quote ($300)", AO.draft_price_usd(draft) == 300.0
          and (OS.observe('x', {'success': True, 'spec_draft': draft}, []) or {}).get('state') == 'quote')


if __name__ == '__main__':
    test_1_valuation()
    test_2_flywheel()
    test_3_to_5_brief_intakes()
    test_6_to_8_one_turn_orders()
    print()
    if FAIL:
        print(f"FAIL ({FAIL})")
        sys.exit(1)
    print("OK")
