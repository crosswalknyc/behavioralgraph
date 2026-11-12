#!/usr/bin/env python3
"""A partnership's dollar value comes from the Brand Partnership valuation
and from nowhere else (Jenna 2026-11-11, Samantha Kolton at CAA, thread
daedaedbce: a general read printed $4,217,683 for Gap x JYT on the
session meter, a deck was built on it, and the $500 order never reached
its quote). Hermetic.

A. The detector: a dollar value asked of a partnership is the
   valuation's; who the shared audience is, the overlap, the fit, which
   brands suit a talent, the product's own price and how it works are
   not taken.
B. Asked cold, the order opens filled from the words and walks to its
   quote; asked while the order is open, the open step is asked again in
   other words; nothing is charged on the way.
C. "Turn this into a deck" over a read that stated a partnership's
   dollar value leads to the order and its quote; a deck of any other
   read, or on another subject, is the ordinary deck.
D. service.ask: the cold ask and the deck ask are answered by the order
   (dashboard and key callers); an ordinary partnership question still
   reaches the ordinary answer path.
E. A read never prices a partnership: the prompt carries the rule, and
   a read that still states a dollar value loses those lines, its dollar
   metrics and its value mix, and points at the valuation. Price talk
   and other money are left alone.
F. Exceptions: a valuation already saved for the pairing may be
   restated; a seat whose plan includes the valuation walks the same
   order with no price on it.
G. A brief that only carries the words somewhere never opens a
   valuation (Mark Valdez, 2026-11-09).
H. A valuation held to a read names each people count for what it is.
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
os.environ.pop('BPIQ_CORPUS_ANCHORS', None)

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import envelope as ENV  # noqa: E402
from prometheus import intake_guard as G  # noqa: E402
from prometheus import intents as I  # noqa: E402
from prometheus import partnership_value as PV  # noqa: E402
from prometheus import partnership_roles as ROLES  # noqa: E402

# what the library knows about these names, as it does live (a hermetic
# run reads no library): which side of a pairing is the talent comes from
# this, never from the order the names were written in
ROLES.KIND_LOOKUP = {'gap': 'brand', 'nike': 'brand', 'gunna': 'talent', 'lowes': 'brand', 'target': 'brand',
                     'stanley': 'brand', 'glenpowell': 'talent'}.get
from migration import bpiq_anchors as BA  # noqa: E402
from migration.bpiq_synthesis import MemoryRegistry  # noqa: E402

TODAY = date(2026, 11, 11)
FAIL = 0
BANNED = ('synth', 'modeled', 'modelled', 'estimated', 'pipeline', 'hostmap', 'claude', '\u2014', '\u2013')


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def clean(text):
    low = str(text or '').lower()
    return not any(b in low for b in BANNED) and not re.search(r'\bdoors?\b', low)


READ_TEXT = (
    "The Gap x JYT partnership carries $4,217,683 of value in its first 51 days, built on 2,184,637 US people reached "
    "and 137,633 of them already confirmed Gap buyers.\n\n"
    "Partnership value, Sep 22 to Nov 11 2026: 4,217,683, Partnership value, first 12 months: 14,839,261, "
    "JYT projected US audience: 2,184,637.\n\n"
    "Partnership value component mix, Sep 22 2026 to Nov 11 2026 (share of the partnership's value in the window)\n"
    "- Social and short-form content: 38.3% share, 61.4% penetration. dance clips and member-led posts carry the largest share of reach\n"
    "- Gap.com collection traffic and orders: 27.1% share, 18.7% penetration. the co-designed capsule drives the confirmed-order side\n\n"
    "- Overlap is already real rather than aspirational: 137,633 of the people JYT reaches have a confirmed Gap order in "
    "the window at index 153, so the mall tour and the co-designed drop are landing on warm buyers as much as new ones.\n"
    "- Recruitment is the smaller but more durable half: 15,147 first-time Gap buyers in 51 days.")
READ_PAYLOAD = {
    'success': True, 'action': 'answer', 'reply': READ_TEXT, 'profile': 'Gap x JYT', 'offer_deck': True,
    'followups': ['Cut the Gap x JYT overlap to 18-24 only', 'Email me this file'],
    'figures': [{'value': 4217683.0, 'kind': 'money', 'label': 'typed by the model'},
                {'value': 2184637.0, 'kind': 'count', 'label': 'typed by the model'},
                {'value': 100000, 'kind': 'money', 'label': 'INCOME'}],
    'metrics': [
        {'name': 'partnership_value_window', 'label': 'Partnership value, Sep 22 to Nov 11 2026', 'unit': 'usd', 'value': 4217683},
        {'name': 'partnership_value_year_one', 'label': 'Partnership value, first 12 months', 'unit': 'usd', 'value': 14839261},
        {'name': 'jyt_us_audience', 'label': 'JYT projected US audience', 'unit': 'people', 'value': 2184637},
        {'name': 'gap_buyers_in_jyt_audience', 'label': 'Gap buyers inside the JYT audience', 'unit': 'people', 'value': 137633},
        {'name': 'new_to_gap_buyers', 'label': 'First-time Gap buyers out of the JYT audience', 'unit': 'people', 'value': 15147}],
    'breakdown': {'dimension': 'Partnership value component', 'share_basis': "share of the partnership's value in the window",
                  'rows': [{'label': 'Social and short-form content', 'share_pct': 38.3, 'penetration_pct': 61.4},
                           {'label': 'Gap.com collection traffic and orders', 'share_pct': 27.1, 'penetration_pct': 18.7}]}}
HER = 'JYT is the talent and GAP is the brand. the partnership started in September 2026'
HER_THREAD = [
    {'role': 'user', 'text': 'how does paige index with visa?'},
    {'role': 'agent', 'text': 'Visa reaches 61.5% of the Paige Spiranac audience, 1,253,620 people, and indexes 109 against the '
                              'average American.\n\nWindow: Nov 6 2025 to Nov 6 2026.\n- Visa penetration of the Paige Spiranac audience: 61.5%\n'
                              '- Projected US people, Visa: 1,251,723\n- Index vs the average American: 109\n- Avid tier size: 437,997 people',
     'meta': {'figures': [{'kind': 'pct', 'value': 61.5}], 'link': {'url': 'https://example.test/f.csv'}, 'subject': 'Paige Spiranac'}},
    {'role': 'user', 'text': 'Value a brand partnership'},
    {'role': 'agent', 'text': 'Who is the property or talent?', 'meta': {'options': [{'label': 'Cancel', 'send': 'Cancel'}]}},
    {'role': 'user', 'text': HER},
    {'role': 'agent', 'text': 'On it: reading Gap Customers now. This one takes a real look at the data, so give me a moment - '
                              'the read will land right here when it is ready.'},
    {'role': 'agent', 'text': READ_TEXT, 'meta': {'figures': READ_PAYLOAD['figures'], 'read_job_id': 'f21c0ed76df6',
                                                  'link': {'url': 'https://example.test/g.csv'}}},
]
PAIGE_THREAD = HER_THREAD[:2]


class Walk:
    """One thread the way service.ask runs the wizard lane."""

    def __init__(self, user='canary:valguard', tid='t1', hist=None, account=None):
        self.user, self.tid = user, tid
        self.account = account if account is not None else user
        self.store = W.MemoryStore()
        self.hist = list(hist or [])
        self.charged = []
        self.registry = MemoryRegistry()
        self.replies = []

    def say(self, text):
        G.reset_resume()
        body = {'thread_id': self.tid}
        mine = W.wants(text, body, self.hist, store=self.store, user=self.user, thread_id=self.tid)
        out = None
        if mine:
            out = W.analyze_hook(text, body, list(self.hist), self.account, {}, self.user, {},
                                 first=W.is_start(text), today=TODAY, store=self.store,
                                 charge_fn=lambda *a: self.charged.append(a) or True,
                                 start_fn=lambda *a: None, registry=self.registry)
        self.hist.append({'role': 'user', 'text': text})
        if out is not None:
            self.hist.append({'role': 'agent', 'text': out['reply'],
                              'meta': {'options': [{'label': c, 'send': c} for c in (out.get('followups') or [])]}})
            self.replies.append(out['reply'])
        return out

    def draft(self):
        return self.store.load(self.user, self.tid) or {}


VALUE_ASKS = (
    'what is the Gap x JYT partnership worth',
    'how much is the Nike and Gunna sponsorship worth in dollars?',
    'what was the ROI of the Stanley x Target collab',
    'what is the earned media value of the Gap x JYT partnership',
    'value the Stanley x Target partnership',
    'can you value Gunna x Nike?',
    "what's the dollar value of Nike's deal with Gunna",
    'how much did the Gap x JYT partnership generate',
    'put a number on the Gap x JYT collab',
    'what is the value of the Gap and JYT partnership',
    'How much brand value did the Celsius sponsorship drive',
    'Gap x JYT valuation please',
    'what is the gap x jyt deal worth',
    'is the Gap x JYT partnership paying off in dollars?',
    'what is this campaign worth',
    'what is the sponsorship worth',
)
ORDINARY = (
    'who is the shared audience of Gap and JYT?',
    'is JYT a good fit for Gap?',
    'which brands suit JYT?',
    'what is the overlap between Gap customers and JYT fans',
    'is a partnership with JYT worth pursuing for Gap?',
    'how much does a brand partnership valuation cost',
    'what is a brand partnership valuation?',
    'how does the valuation work',
    'how many people did the Gap x JYT partnership reach',
    'what do Gap x JYT fans buy',
    'where does the partnership value number come from',
    'is the Nike deal worth testing with this audience?',
    'how much is a valuation',
    'tell me about the Gap x JYT partnership',
    'what brands should JYT partner with',
    HER,
    'what is Paige Spiranac worth to Visa',
    'how valuable is this audience',
    'which partnership has the best fit',
    'where is my valuation',
    'what is the value of this audience to advertisers',
    'how many Gap buyers are in the JYT audience',
    'Value a brand partnership',          # the chip is the order's own start, not a question to take
)


# ------------------------------------------------------------------ A
def test_a_detector():
    print("A. a dollar value asked of a partnership is the valuation's; everything else is left alone")
    for q in VALUE_ASKS:
        check(f"the valuation's: {q!r}", PV.owns(q, []) == PV.KIND_VALUE)
    for q in ORDINARY:
        check(f"left alone: {q[:60]!r}", PV.owns(q, []) == '')
    check("the pairing is read from 'Gap x JYT'", PV.pairing('what is the Gap x JYT partnership worth') == ('Gap', 'JYT'))
    check("... from 'the Nike and Gunna sponsorship'", PV.pairing('how much is the Nike and Gunna sponsorship worth') == ('Nike', 'Gunna'))
    check("... from a possessive", PV.pairing("what's the dollar value of Nike's deal with Gunna") == ('Nike', 'Gunna'))
    # about this thread's partnership
    check("'what is it worth?' in a thread on a partnership is the valuation's", PV.owns('what is it worth?', HER_THREAD) == PV.KIND_VALUE)
    check("'how much is that worth in dollars' too", PV.owns('how much is that worth in dollars', HER_THREAD) == PV.KIND_VALUE)
    check("'what is it worth?' in a thread on no partnership is not", PV.owns('what is it worth?', PAIGE_THREAD) == '')
    check("'and what about the overlap?' in the partnership thread is an ordinary follow-up",
          PV.owns('and what about the overlap?', HER_THREAD) == '')
    check("her read is seen as one that stated a partnership's dollar value", PV.thread_value_read(HER_THREAD) is not None)
    check("the Visa read is not", PV.thread_value_read(PAIGE_THREAD) is None)
    check("the thread's pairing is hers, with the roles she gave", PV.thread_pair(HER_THREAD) == ('JYT', 'GAP', True))
    check("a promise line is never the read", not PV.turn_states_value(HER_THREAD[5]))


# ------------------------------------------------------------------ B
def test_b_cold_ask_reaches_the_quote():
    print("B. asked cold, the order opens and walks to its quote; asked mid-order, the step is asked again")
    w = Walk()
    out = w.say('what is the Gap x JYT partnership worth')
    check("the order answers (no general read runs)", out is not None and out.get('bpiq_wizard'))
    check("the reply says where the figure comes from", out['reply'].startswith(PV.LEAD_VALUE))
    check("it says what it read, the brand the library knows as the brand (never the order the names were written in)",
          'Got it: JYT is the talent and Gap is the brand.' in out['reply'] and 'wrong way round' not in out['reply'])
    check("the other way round reads the same", 'Got it: JYT is the talent and Gap is the brand.'
          in Walk().say('what is the JYT x Gap partnership worth')['reply'])
    check("it carries no dollar figure for the partnership", '$' not in out['reply'])
    check("the names were not asked for again", w.draft().get('step') == W.STEP_DATES and out['reply'].endswith(W.ASK_DATES))
    out = w.say('JYT is the talent')
    check("naming one side by its role keeps the pair", w.draft().get('talent') == 'JYT' and w.draft().get('brand_partner') == 'Gap'
          and out['reply'].startswith('Got it: JYT is the talent'))
    # nothing known settles which side is the talent: one short question, never a guess
    u = Walk()
    out2 = u.say('what is the Quill x Zorb partnership worth')
    check("an unknown pairing is asked about, not guessed",
          out2['reply'].endswith('Which one is the talent or property: Quill or Zorb?')
          and 'is the talent and' not in out2['reply'] and out2['followups'] == ['Quill', 'Zorb', 'Cancel'])
    check("neither name was dropped", u.draft().get('pair') == ['Quill', 'Zorb'] and not u.draft().get('talent'))
    out2 = u.say('Zorb')
    check("the answer sets both roles", u.draft().get('talent') == 'Zorb' and u.draft().get('brand_partner') == 'Quill'
          and not u.draft().get('pair') and u.draft().get('step') == W.STEP_DATES)
    u = Walk()
    u.say('what is the Quill x Zorb partnership worth')
    out2 = u.say('Quill is the brand')
    check("a role sentence answers it too", u.draft().get('talent') == 'Zorb' and u.draft().get('brand_partner') == 'Quill')
    u = Walk()
    u.say('what is the Stanley x Target partnership worth')
    check("two brands are asked about", u.replies[-1].endswith('Which one is the talent or property: Stanley or Target?'))
    out2 = u.say('not sure')
    check("a miss asks again in other words", 'Is it Stanley or Target?' in u.replies[-1] and u.replies[-1] != u.replies[-2])
    for m in ('September 22 to today', 'Accept', 'no', 'find them'):
        out = w.say(m)
    check("the walk ends on the quote", w.draft().get('step') == W.STEP_CHARGE and 'This is $500.' in out['reply']
          and 'Accept $500' in out['followups'])
    check("nothing was charged on the way", not w.charged)
    check("no reply repeats the one before it", all(w.replies[i] != w.replies[i - 1] for i in range(1, len(w.replies))))
    check("every reply is clean", all(clean(r) for r in w.replies))
    # the dates the question itself gave fill ahead
    w = Walk()
    out = w.say('can you value Gunna x Nike? the campaign ran June 3 to August 20, 2026')
    check("dates in the question fill the window step", w.draft().get('step') == W.STEP_ATTR
          and w.draft().get('campaign_start') == '2026-06-03' and 'Gunna x Nike: I read the campaign as' in out['reply'])
    # asked while the order is open
    w = Walk()
    w.say(W.CHIP)
    w.say(HER)
    first = w.replies[-1]
    out = w.say('what is it worth?')
    check("mid-order the wizard keeps the turn", out is not None and out['reply'].startswith(PV.LEAD_VALUE_OPEN))
    check("the open step is asked in other words, never the same words", out['reply'].split('\n\n', 1)[1] not in first)
    out2 = w.say('how much is the partnership worth?')
    check("a second one never repeats the reply before it", out2['reply'] != out['reply'])
    check("the draft stayed at its step with what she gave", w.draft().get('talent') == 'JYT' and w.draft().get('step') == W.STEP_DATES)
    for m in ('9/22/26 to today', 'yes', 'yes', 'find the campaign asset'):
        out = w.say(m)
    check("her own messages then reach the $1,000 quote", 'This is $1,000.' in out['reply'] and not w.charged)


# ------------------------------------------------------------------ C
def test_c_deck_of_a_value_read():
    print("C. a deck of a read that put a dollar value on a partnership is the valuation order")
    for q in ('Can you turn this into a deck?', 'turn this into a deck', 'build the Gap x JYT insights deck',
              'Run a deck on Gap x JYT', 'can I get slides of that', 'make this a presentation'):
        check(f"the valuation's: {q!r}", PV.owns(q, HER_THREAD) == PV.KIND_DECK)
    check("a deck on another subject is the ordinary deck", PV.owns('make a deck on Paige Spiranac', HER_THREAD) == '')
    check("a deck in a thread whose read was not a dollar valuation is the ordinary deck",
          PV.owns('Can you turn this into a deck?', PAIGE_THREAD) == '')
    check("picking the pairing for a deck is the valuation's", PV.owns('', HER_THREAD, pick='Gap x JYT') == PV.KIND_DECK)
    check("picking another candidate is not", PV.owns('', HER_THREAD, pick='Paige Spiranac') == ''
          and PV.owns('', HER_THREAD, pick='Gap Customers') == '')
    w = Walk(hist=HER_THREAD)
    out = w.say('Can you turn this into a deck?')
    check("the order answers the deck ask", out is not None and out['reply'].startswith(PV.LEAD_DECK))
    d = w.draft()
    check("filled from the thread: talent, brand, and the read's own window",
          (d.get('talent'), d.get('brand_partner'), d.get('campaign_start'), d.get('campaign_end'))
          == ('JYT', 'GAP', '2026-09-22', '2026-11-11'))
    check("it opens on the window step (nothing she already gave is asked again)", d.get('step') == W.STEP_ATTR)
    for m in ('Accept', 'no', 'find them'):
        out = w.say(m)
    check("and reaches the quote", w.draft().get('step') == W.STEP_CHARGE and 'This is $500.' in out['reply'] and not w.charged)
    # the deck surface's own gate (a pick, or the words)
    store = W.MemoryStore()
    old = W.store_for
    W.store_for = lambda body, g=None: store
    try:
        hit = W.deck_gate({'username': 'canary:valguard'}, {'thread_id': 't9'}, 'Can you turn this into a deck?',
                          HER_THREAD + [{'role': 'user', 'text': 'Can you turn this into a deck?'},
                                        {'role': 'agent', 'text': 'Which subject should the deck be on: Gap x JYT, Gap Customers or Paige Spiranac?'}],
                          pick='Gap x JYT', g={})
        check("the deck surface hands a picked partnership to the order", isinstance(hit, dict) and hit.get('bpiq_wizard')
              and hit['reply'].startswith(PV.LEAD_DECK) and 'job_id' not in hit)
        check("in the shape the deck surface draws as a line with its chips (never a deck that failed to start)",
              hit.get('guidance') is True and hit.get('success') is False and hit.get('error') == hit['reply']
              and hit['followups'][:1] == ['Accept'])
        from prometheus import order_stall as OS
        check("and it reads as an open order, not a decline",
              (OS.observe('Gap x JYT', hit, HER_THREAD) or {}).get('flow') == 'brand_partnership')
        check("a pick of a library profile builds the ordinary deck",
              W.deck_gate({'username': 'canary:valguard'}, {'thread_id': 't9'}, 'Can you turn this into a deck?',
                          HER_THREAD, pick='Paige Spiranac', g={}) is None)
        check("a deck ask in a thread with no dollar valuation builds the ordinary deck",
              W.deck_gate({'username': 'canary:valguard'}, {'thread_id': 't8'}, 'Can you turn this into a deck?',
                          PAIGE_THREAD, pick='', g={}) is None)
    finally:
        W.store_for = old
    legacy = (WEB / 'prometheus' / 'legacy' / 'chat.py').read_text(encoding='utf-8')
    check("the deck surface asks the gate before it resolves a subject",
          '_bpiq_wiz.deck_gate(user, body, text, history, _dp_pick, globals())' in legacy
          and legacy.index('_bpiq_wiz.deck_gate(') < legacy.index('_deck_pick.settle('))


# ------------------------------------------------------------------ D
def _bind_host(calls):
    from prometheus.host import host

    def _analyze_core(user, body, text, history):
        calls.append(('analyze', text))
        return {'success': True, 'action': 'answer', 'reply': 'The shared audience is 137,633 people.', 'followups': []}

    host.bind(analyze_core=_analyze_core,
              interpret_core=lambda user, body, text, history: (calls.append(('interpret', text)) or {'success': True, 'reply': 'brief'}),
              deck_core=lambda user, body: (calls.append(('deck', body.get('text'))) or {'success': True, 'reply': 'deck', 'job_id': 'x' * 12}),
              ask_hint=lambda **k: None, gate_analyze=lambda u: True, gate_pull=lambda u: True, funds_gate=lambda u: None,
              gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
              calm_payload=lambda: {'success': False, 'error': 'calm'}, error_email=lambda *a: None, has=lambda c: False,
              load_threads_index=lambda u: {'threads': [{'id': 'A'}, {'id': 'B'}, {'id': 'C'}, {'id': 'D'}], 'active': 'A'},
              max_threads=50, s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
              thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u, thread_title_from=lambda t: t[:30],
              bucket='x', s3_client=None, job_owner_ok=lambda a, b: True)


def test_d_service():
    print("D. service.ask: the order answers the cold ask and the deck ask; an ordinary question is an ordinary answer")
    from prometheus import service as S
    calls = []
    _bind_host(calls)
    U = {'username': 'canary:valguard', 'role': 'user'}
    page = {'primary': {'s3_key': 'Paige_Spiranac_05_01_2026_10_00.csv', 'name': 'Paige Spiranac'}}
    env, st = S.ask(U, {'text': 'what is the Gap x JYT partnership worth', 'client': 'dashboard', 'history': [],
                        'thread_id': 'A', 'page_context': page})
    check("cold ask: the valuation order answers", st == 200 and (env.get('decision') or {}).get('reason') == 'bpiq_wizard'
          and env['text'].startswith(PV.LEAD_VALUE))
    check("no read, no brief and no deck was started", not calls)
    check("the reply carries no dollar figure", '$' not in env['text'])
    calls.clear()
    env, st = S.ask(U, {'text': 'Can you turn this into a deck?', 'client': 'dashboard', 'history': HER_THREAD,
                        'thread_id': 'B', 'page_context': page})
    check("deck ask over the value thread: the valuation order answers", st == 200
          and (env.get('decision') or {}).get('reason') == 'bpiq_wizard' and env['text'].startswith(PV.LEAD_DECK))
    check("no deck was started", not calls)
    check("the reply offers the window, not a which-subject list",
          [o['label'] for o in env.get('options') or []][:1] == ['Accept'])
    calls.clear()
    env, st = S.ask(U, {'text': 'who is the shared audience of Gap and JYT?', 'client': 'dashboard', 'history': [],
                        'thread_id': 'C', 'page_context': page})
    check("an ordinary partnership question is not the order's", st == 200 and (env.get('decision') or {}).get('reason') != 'bpiq_wizard')
    check("it reached the ordinary answer path", calls and calls[0][0] == 'analyze')
    calls.clear()
    env, st = S.ask(U, {'text': 'Can you turn this into a deck?', 'client': 'dashboard', 'history': PAIGE_THREAD,
                        'thread_id': 'D', 'page_context': page})
    check("a deck ask with no dollar valuation in the thread stays the deck surface's",
          (env.get('decision') or {}).get('reason') != 'bpiq_wizard')
    calls.clear()
    env, st = S.ask(U, {'text': 'what was the ROI of the Stanley x Target collab', 'client': 'api', 'history': []}, via='api_key')
    check("a key caller's cold ask is the order's too", st == 200 and (env.get('decision') or {}).get('reason') == 'bpiq_wizard'
          and not calls and '$' not in env['text'])
    svc = (WEB / 'prometheus' / 'service.py').read_text(encoding='utf-8')
    check("the order's lane still sits ahead of every read and deck lane", S.LANE_ORDER[1] == 'bpiq_wizard'
          and svc.index('# lane: bpiq_wizard') < svc.index('# lane: deck_conversion') < svc.index('# lane: work_gate'))


# ------------------------------------------------------------------ E
def test_e_read_never_prices_a_partnership():
    print("E. a read never prices a partnership")
    blk = PV.prompt_block(HER)
    check("a read on a partnership carries the rule", blk.startswith('PARTNERSHIP DOLLAR VALUE (binding).')
          and 'never states what the partnership is worth' in blk)
    check("the rule keeps people, overlap and fit in scope", 'shared audience' in blk and 'overlap' in blk and 'fit' in blk)
    check("a read on anything else carries nothing", PV.prompt_block('how does paige index with visa?') == '')
    check("the wizard hands the read its block", W.saved_valuation_block(HER) == blk)
    p = copy.deepcopy(READ_PAYLOAD)
    changed = PV.guard_read(p, question=HER, history=[])
    check("her read is changed", changed and p.get('partnership_value_withheld') is True)
    check("the dollar value is gone", '4,217,683' not in p['reply'] and '14,839,261' not in p['reply'])
    check("the value mix is gone", 'breakdown' not in p and 'component mix' not in p['reply'] and '38.3% share' not in p['reply'])
    check("the dollar metrics are gone, the people metrics stay",
          [m['name'] for m in p['metrics']] == ['jyt_us_audience', 'gap_buyers_in_jyt_audience', 'new_to_gap_buyers'])
    check("the people, the buyers and the overlap stay", '2,184,637' in p['reply'] and '137,633' in p['reply'] and '15,147' in p['reply'])
    check("the reply points at the valuation", PV.POINTER_LINE in p['reply'] and p['followups'][0] == PV.CHIP)
    check("no deck is offered on it", p.get('offer_deck') is False)
    check("the money figure that was the value is gone; other money stays",
          [f['value'] for f in p['figures'] if f['kind'] == 'money'] == [100000])
    check("the copy is clean", clean(p['reply']))
    check("a second pass changes nothing", PV.guard_read(p, question=HER, history=[]) is False)
    # other reads are left alone
    for label, reply in (
            ('a read with no money', 'Visa reaches 61.5% of the Paige Spiranac audience, 1,253,620 people.'),
            ('sales in a partnership read', 'The Gap x JYT collab sold $1,284,611 of product on Gap.com in the window.'),
            ('income in an audience read', '40.5% of the audience sits at $100,000 or more.'),
            ('the product price', 'A Brand Partnership valuation is $500, or $1,000 with a control group.'),
            ('the recap', "Here's what I'll run:\nJYT\nJYT x Gap\nThis is $500.")):
        q = {'success': True, 'action': 'answer', 'reply': reply, 'followups': []}
        check(f"left alone: {label}", PV.guard_read(q) is False and q['reply'] == reply)
    # the request-time exit
    raw = {'success': True, 'action': 'answer', 'followups': [],
           'reply': 'A Visa partnership with Paige Spiranac is worth $2,381,447 a year. Visa indexes 109 with her audience.'}
    env = ENV.wrap(raw, surface='analyze', decision={'reason': 'question'})
    check("an answer written in the request loses the value and points at the valuation",
          '2,381,447' not in env['text'] and PV.POINTER_LINE in env['text'] and 'indexes 109' in env['text'])
    raw = {'success': True, 'action': 'answer', 'followups': [], 'reply': 'Pricing is: Brand Partnership - $500 + $500 Control for a $1,000 total.'}
    env = ENV.wrap(raw, surface='analyze', decision={'reason': 'pricing'})
    check("the pricing answer is never touched", '$500' in env['text'] and PV.POINTER_LINE not in env['text'])
    raw = dict(W._payload("Here's what I'll run:\nJYT\nJYT x Gap\nThis is $500.", ['Accept $500'], draft=dict(W.empty_draft(), step=W.STEP_CHARGE,
               talent='JYT', campaign='JYT x Gap', campaign_start='2026-09-22', campaign_end='2026-11-11',
               attr_start='2026-09-22', attr_end='2026-11-11', control_group=False, asset_mode='find')))
    env = ENV.wrap(raw, surface='analyze', decision={'reason': 'bpiq_wizard'})
    check("the order's own quote is never touched", 'This is $500.' in env['text'])
    legacy = (WEB / 'prometheus' / 'legacy' / 'chat.py').read_text(encoding='utf-8')
    check("a finished read passes the guard before it lands in the thread",
          "partnership_value', fromlist=['guard_read']).guard_read(payload, question=text, history=history)" in legacy
          and legacy.index("fromlist=['guard_read']") < legacy.index('_pm_append_read_to_history(pm_user, job_id, payload'))
    check("chat.py stays under its line ratchet", len(legacy.splitlines()) < 19900)


# ------------------------------------------------------------------ F
def test_f_exceptions():
    print("F. a saved valuation may be restated; an included seat walks the same order with no price")
    saved = {'subject': 'Gap x JYT', 'total_brand_value': 3911204.18, 'people': 311618}
    PV.SAVED_LOOKUP = lambda pr: saved if {x.lower() for x in pr} == {'gap', 'jyt'} else None
    try:
        check("a value ask on a valued pairing is an ordinary read (it restates the valuation)",
              PV.owns('what is the Gap x JYT partnership worth', []) == '')
        check("in either order of the names", PV.owns('what is the JYT x Gap partnership worth', []) == '')
        check("another pairing is still the valuation's", PV.owns('what is the Nike x Gunna partnership worth', []) == PV.KIND_VALUE)
        check("a deck of the valued pairing's read is the ordinary deck", PV.owns('Can you turn this into a deck?', HER_THREAD) == '')
        p = copy.deepcopy(READ_PAYLOAD)
        check("a read that states the saved valuation is left as it is", PV.guard_read(p, question=HER) is False
              and p['reply'] == READ_TEXT)
        check("no rule against pricing rides a read on a valued pairing", PV.prompt_block('how is the Gap x JYT partnership doing?') == '')
    finally:
        PV.SAVED_LOOKUP = None
    # included with the plan: the same order, no price (the flag rides the request, as it does live)
    from flask import Flask
    with Flask('pv_guard').app_context():
        w = Walk(user='casey', account={'username': 'casey', 'role': 'user', 'unlimited': True})
        for m in ('what is the Gap x JYT partnership worth', 'September 22 to today', 'Accept', 'no', 'find them'):
            out = w.say(m)
        check("an included seat reaches the same recap", w.draft().get('step') == W.STEP_CHARGE and "Here's what I'll run" in out['reply'])
        check("with no price on it", W.INCLUDED_LINE in out['reply'] and '$' not in out['reply'] and out['followups'][0] == 'Accept')
    with Flask('pv_guard').app_context():
        w = Walk(user='payer', account={'username': 'payer', 'role': 'user', 'credits': 3})
        for m in ('what is the Gap x JYT partnership worth', 'September 22 to today', 'Accept', 'yes', 'find them'):
            out = w.say(m)
        check("a paying seat reads the price on the same recap", 'This is $1,000.' in out['reply'] and W.INCLUDED_LINE not in out['reply']
              and out['followups'][0] == 'Accept $1,000')


# ------------------------------------------------------------------ G
VALDEZ = (
    "Product - Old Navy\nFocus - Kids & Family\nSub-focus Verticals - Style and Fashion, Family, Fandom and Fitness\n"
    "Anything to show how consumers are purchasing in these verticals would be key\n"
    "Competitors - Abercrombie & Fitch, Zara, H&M, American Eagle, Target, Walmart\n"
    "Marketing Strategy - An entertainment-led, creator first approach to reach modern parents with kids\n"
    "Example - Mr. Beast partnership for Back to School with purpose-led initiatives like funding kids' lunches as part of the campaign\n"
    "Other notes\nValue pricing and inflation-conscious messaging\nAmplify On-Trend Style & value through content\n"
    "Can you describe of who these parents are, where they shop, and anything key like under-served geos would be really helpful.")


def test_g_a_brief_never_opens_a_valuation():
    print("G. a brief that only carries the words somewhere never opens a valuation")
    check("the 2026-11-09 brief is not a valuation order", not I.bpiq_intent(VALDEZ, W.CHIP) and PV.owns(VALDEZ, []) == '')
    w = Walk()
    check("the wizard does not take it", w.say(VALDEZ) is None and not w.draft())
    for q in ('value the Stanley x Target partnership', 'can you value Gunna x Nike', 'Value the Glen Powell x RAM Trucks partnership',
              'run a brand partnership valuation for MMG x Lowes', 'valuation for MMG x Lowes', 'Value a brand partnership', 'bpiq'):
        check(f"still a valuation order: {q!r}", I.bpiq_intent(q, W.CHIP))
    check("a pasted deal brief still opens the intake",
          I.bpiq_intent('Colin and Samir have a Lexus deal. The first promo ran as a pre-roll on their channel, then a sponsored '
                        'episode launched the campaign in March.', W.CHIP))


# ------------------------------------------------------------------ H
def test_h_people_counts():
    print("H. a valuation held to a read names each people count for what it is")
    figs = BA.figures_of({'metrics': READ_PAYLOAD['metrics']})
    check("the read's audience metric is kept as the talent's audience", figs.get('talent_audience') == 2184637)
    payload = {'projected_audience_size': 311618, 'valuation': {'total_brand_value': 3911204.18},
               'campaign': {'audience_basis': 'campaign reach',
                            'reach_line': 'The campaign reached about 311,618 people in the window: 184,220 on the collection pages, counted once each.'}}
    before = copy.deepcopy(payload)
    out = BA.label_people(payload, dict(figs, subject='Gap x JYT'), {'qualifier': 'JYT', 'brand_partner': 'Gap'})
    cb = payload['campaign']
    check("the reach is labeled as the people the campaign reached", cb.get('reach_label') == 'People the campaign reached')
    check("the talent's audience is named at the read's figure", cb.get('talent_audience_held') == 2184637
          and "JYT's projected US audience is 2,184,637" in cb.get('people_line', ''))
    check("both counts sit in one line, each for what it is",
          cb['people_line'] == "The campaign reached 311,618 people in the window. JYT's projected US audience is 2,184,637; "
                               "the campaign reached a part of it.")
    check("the reach line carries it once", cb['reach_line'].count('2,184,637') == 1 and cb['reach_line'].startswith(before['campaign']['reach_line']))
    check("no count moved", payload['projected_audience_size'] == 311618
          and payload['valuation'] == before['valuation'] and out.get('talent_audience_held') == 2184637)
    again = copy.deepcopy(payload)
    BA.label_people(again, dict(figs), {'qualifier': 'JYT'})
    check("a second pass adds nothing", again['campaign']['reach_line'] == payload['campaign']['reach_line'])
    p2 = {'projected_audience_size': 311618, 'campaign': {}}
    BA.label_people(p2, None, {'qualifier': 'JYT'})
    check("with no held read, only the reach is labeled", p2['campaign'] == {'reach_label': 'People the campaign reached'})
    p3 = {'projected_audience_size': 2400000, 'campaign': {}}
    BA.label_people(p3, dict(figs), {'qualifier': 'JYT'})
    check("an audience smaller than the reach is never called the whole it is part of", 'people_line' not in p3['campaign'])
    check("the line is clean", clean(cb['people_line']))
    synth = (WEB / 'migration' / 'bpiq_synthesis.py').read_text(encoding='utf-8')
    check("the valuation labels its people after the hold", synth.index('_ba.hold(') < synth.index('_ba.label_people(payload, banked, inputs)'))
    job = (WEB / 'prometheus' / 'bpiq_job.py').read_text(encoding='utf-8')
    check("the ready note in the thread carries the line", "get('people_line')" in job)


if __name__ == '__main__':
    test_a_detector()
    test_b_cold_ask_reaches_the_quote()
    test_c_deck_of_a_value_read()
    test_d_service()
    test_e_read_never_prices_a_partnership()
    test_f_exceptions()
    test_g_a_brief_never_opens_a_valuation()
    test_h_people_counts()
    print()
    if FAIL:
        print(f"FAIL ({FAIL})")
        sys.exit(1)
    print("OK")
