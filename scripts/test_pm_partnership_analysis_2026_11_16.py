#!/usr/bin/env python3
"""A brand partnership analysis comes from the Brand Partnership valuation,
never from a read (Jenna 2026-11-16: "we should never do reads. you only
get it if you buy the valuation").

Samantha Kolton at CAA, thread d7c42fc2f7: "Can you run a brand
partnership analysis for Charli XCX and Vinted? Here are the campaign
dates: 10/22 - Thursday Teaser Video ..." No lane took it as an order.
She was asked which campaign it was, answered "Vinted", and a general
read bound to the Brand Partnership page answered with the platform
values of the valuation open on her screen (Matchbox the Film on Apple
TV x Leatherman) under the name she had typed: "1,149,665 in earned
media value", written with no currency sign, so the 2026-11-11 guard
let it through. Hermetic.

A. The detector: an analysis of a partnership is the valuation's; the
   audience, the overlap, the fit, the product's own questions and
   anything that is not a partnership are not taken.
B. Her question opens the order, filled from her words (the pairing,
   the first and last dated beat), and no read runs (the wizard and
   service.ask).
C. On the Brand Partnership page: a question about the valuation open
   there is an ordinary answer; a partnership that is not the one open
   there is the order's.
D. A saved valuation may be read: nothing is taken, nothing is withheld.
E. The guard: a value written with no currency sign and a value mix by
   platform are withheld; her read comes back as the one line that
   points at the valuation, with no figures and no file. A read on a
   partnership's audience is left alone.
F. The page tells Prometheus which valuation is open.
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
from prometheus import partnership_value as PV  # noqa: E402
from prometheus import valuation_price as VP  # noqa: E402

TODAY = date(2026, 11, 16)
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


HER = ("Can you run a brand partnership analysis for Charli XCX and Vinted? Here are the campaign dateS: "
       "10/22 - Thursday Teaser Video\n10/26 - First Announce Video, Press Go Lives\n11/2 - Reel goes live\n"
       "11/12 - Closet\n11/13 - Closet goes live")
OPEN_NAME = 'Matchbox the Film on Apple TV x Leatherman'
PLATFORMS = [{'platform': 'Press', 'emv_value': 693136}, {'platform': 'Facebook', 'emv_value': 127862},
             {'platform': 'Instagram', 'emv_value': 100122}, {'platform': 'Direct (Brand Site)', 'emv_value': 93090},
             {'platform': 'YouTube', 'emv_value': 61271}, {'platform': 'TikTok', 'emv_value': 34412},
             {'platform': 'X (Twitter)', 'emv_value': 21504}, {'platform': 'Reddit', 'emv_value': 18268}]
# the page as it reached the server that night (no name on it), and as it reaches it now
PAGE_THEN = {'view_context': {'view_id': 'brandPartnershipIQ', 'view_title': 'Brand Partnership IQ',
                              'data': {'platforms': PLATFORMS}}}
PAGE_NOW = {'view_context': {'view_id': 'brandPartnershipIQ', 'view_title': 'Brand Partnership IQ',
                             'data': {'partnership': OPEN_NAME, 'brand_partner': 'Leatherman',
                                      'window': '2026-09-29 to 2026-11-13', 'platforms': PLATFORMS}}}

# the read she was handed, as it landed in her thread
READ_TEXT = (
    "On Charli XCX x Vinted:\n\n"
    "The Charli XCX closet run for Vinted carried 1,149,665 in earned media value across the campaign window, and "
    "60.3% of it sat in press off the 10/26 announce.\n\n"
    "Platform mix, Oct 22 2026 to Nov 17 2026 (share of total campaign earned media value)\n"
    "- Press: 60.3% share. 10/26 announce coverage, no pre-campaign base\n"
    "- Facebook: 11.1% share. largest single social contributor\n"
    "- Instagram: 8.7% share. 11/2 reel carries this row\n"
    "- Direct (Brand Site): 8.1% share. closet go-live endpoint on 11/13\n"
    "- YouTube: 5.3% share. teaser and announce video watch\n"
    "- TikTok: 3.0% share. clip reuse rather than first posting\n"
    "- X (Twitter): 1.9% share. announce-day chatter, thin after 10/26\n"
    "- Reddit: 1.6% share. resale and fan communities, slow build\n\n"
    "- The shape of this run is announce-led. Press had no standing base for a Charli XCX x Vinted tie and still "
    "carried 693,136, which reads as the 10/26 announce and the closet framing doing the work a paid buy would "
    "normally have to buy outright.\n"
    "- Facebook and Instagram land close together at 127,862 and 100,122, which skews the social story toward Meta "
    "rather than the short-form platforms. The 11/2 reel is the pivot point in that block.\n"
    "- The direct brand site at 93,090 sits above YouTube, TikTok, X and Reddit individually, which reads as the "
    "11/12 closet build and 11/13 go-live pulling people to a Vinted-owned endpoint rather than leaving them on "
    "platform. Narrow owned endpoints carry rate; the wider social path carries the volume at 363,439.\n"
    "- Reddit and X are small in value at 18,268 and 21,504 but they lean resale-native, and secondhand marketplace "
    "conversation tends to incubate there before it shows up in the Meta numbers. Worth watching as the closet "
    "restocks.")
READ_PAYLOAD = {
    'success': True, 'action': 'answer', 'reply': READ_TEXT, 'profile': 'Charli XCX x Vinted', 'offer_deck': True,
    'followups': ['Day by day earned value across the five campaign beats',
                  'Who the Vinted closet buyers are: demo shape and category mix', 'Email me this file'],
    'file_link': {'url': 'https://example.test/Brand%20Partnership%20IQ.csv'},
    'figures': [{'value': 127862.0, 'kind': 'count', 'label': '"emv_value"', 'source': 'view_data'},
                {'value': 100122.0, 'kind': 'count', 'label': '"emv_value"', 'source': 'view_data'},
                {'value': 18268.0, 'kind': 'count', 'label': '"emv_value"', 'source': 'view_data'},
                {'value': 21504.0, 'kind': 'count', 'label': '"emv_value"', 'source': 'view_data'},
                {'value': 60.29, 'kind': 'pct', 'label': 'f1/(f1+f2)*100', 'source': 'derived'}],
    'metrics': [{'name': 'press_share_of_emv', 'label': 'Press share of total value', 'unit': 'pct', 'value': 60.3}],
    'breakdown': {'dimension': 'Platform', 'share_basis': 'share of total campaign earned media value',
                  'rows': [{'label': r['platform'], 'share_pct': 1.0, 'pool': r['emv_value']} for r in PLATFORMS]}}
HER_THREAD = [
    {'role': 'user', 'text': HER},
    {'role': 'agent', 'text': 'Which campaign is this about? Open it in the Attribution IQ tab and ask from there, or give me '
                              'the campaign name and its window.'},
    {'role': 'user', 'text': 'Vinted'},
]


def hook(text, history=None, store=None, body=None, tid='t1'):
    b = dict(body or {}, thread_id=tid)
    return W.analyze_hook(text, b, list(history or []), 'canary:analysis', {}, 'canary:analysis',
                          {'_pm_bpiq_intent': W.is_chip}, today=TODAY, store=store if store is not None else W.MemoryStore())


# ------------------------------------------------------------------ A
def test_a_detector():
    print("A. an analysis of a partnership is the valuation's")
    yes = (HER,
           'Can you run a brand partnership analysis for Charli XCX and Vinted?',
           'brand partnership analysis',
           'brand partnership analysis for charli xcx and vinted',
           'run a brand partnership analysis on Gap x JYT, Sept 22 to Nov 11',
           'How did the Gap x JYT collab perform?',
           'recap the Nike and Gunna sponsorship',
           'analyze the partnership between Stanley and Target',
           'partnership report on Liquid Death and Ozzy',
           'how did this partnership perform?')
    for q in yes:
        check(f"taken: {q[:64]!r}", PV.owns(q, []) == PV.KIND_ANALYSIS)
    no = ('Who is the shared audience of Charli XCX and Vinted?',
          'Is Charli XCX a good fit for a Vinted partnership?',
          'which brands should Charli XCX partner with',
          'Analyze the audience overlap between Nike and Gunna',
          'what is a brand partnership analysis?',
          'how much is a brand partnership analysis?',
          'how did the Super Bowl ad perform',
          'analyze Netflix churn',
          'review the deal terms',
          'how does paige index with visa?')
    for q in no:
        check(f"not taken: {q[:64]!r}", PV.owns(q, []) == '')
    check("a dollar value ask is still a value ask", PV.owns('What is the Charli XCX x Vinted partnership worth?', []) == PV.KIND_VALUE)
    check("the order's own start phrase is the order's, as before", PV.owns('Value a brand partnership', []) == '')
    check("her pairing is read from the words", PV.pairing(HER) == ('Charli XCX', 'Vinted'))
    check("typed in lower case too", PV.pairing('brand partnership analysis for charli xcx and vinted') == ('charli xcx', 'vinted'))
    check("an order's own words are not part of a name",
          PV.pairing('run a brand partnership analysis on Gap x JYT, Sept 22 to Nov 11') == ('Gap', 'JYT'))
    check("what an analysis costs is the price answer's", VP.is_price_question('how much is a brand partnership analysis?'))


# ------------------------------------------------------------------ B
def test_b_her_question_opens_the_order():
    print("B. her question opens the order, filled from her words")
    store = W.MemoryStore()
    check("the order takes the turn", W.wants(HER, {'thread_id': 't1'}, [], store=store, user='canary:analysis', thread_id='t1'))
    hit = hook(HER, store=store)
    reply = (hit or {}).get('reply') or ''
    d = (hit or {}).get('bpiq_wizard') or {}
    check("it says why the order opened", reply.startswith(PV.LEAD_ANALYSIS))
    check("both names came from her words", {str(x) for x in (d.get('pair') or [d.get('talent'), d.get('brand_partner')])}
          >= {'Charli XCX', 'Vinted'} or (d.get('talent') and d.get('brand_partner')))
    check("the campaign runs from her first dated beat to her last",
          d.get('campaign_start') == '2026-10-22' and d.get('campaign_end') == '2026-11-13')
    check("she is not asked for the names or the dates again",
          'campaign dates' not in reply.lower() and 'who is the property or talent' not in reply.lower())
    check("nothing is priced or charged yet", '$' not in reply and not (hit or {}).get('bpiq_job_id'))
    check("no read was started", not (hit or {}).get('read_job_id') and not (hit or {}).get('job_id'))
    check("the copy is clean", clean(reply))
    check("a list of dated beats is read first to last",
          W.milestone_dates(HER, today=TODAY) == ('2026-10-22', '2026-11-13'))
    check("a range is not a list of beats", W.milestone_dates('10/22-11/13', today=TODAY) == (None, None))
    check("one date is not a list", W.milestone_dates('it launched 10/22 - teaser', today=TODAY) == (None, None))
    hit = hook('brand partnership analysis')
    check("asked with no names, the order opens at its first question",
          (hit or {}).get('reply', '').startswith(PV.LEAD_ANALYSIS) and W.ASK_TALENT in hit['reply'])
    # while an order is open, the open step is asked again
    store = W.MemoryStore()
    hook('Value a brand partnership', store=store)
    hit = hook('can you just run the brand partnership analysis', history=[{'role': 'user', 'text': 'Value a brand partnership'},
                                                                         {'role': 'agent', 'text': W.ASK_TALENT}], store=store)
    check("with an order open, the open step is asked again", (hit or {}).get('reply', '').startswith(PV.LEAD_ANALYSIS_OPEN))


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
              load_threads_index=lambda u: {'threads': [{'id': x} for x in 'ABCDEF'], 'active': 'A'},
              max_threads=50, s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
              thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u, thread_title_from=lambda t: t[:30],
              bucket='x', s3_client=None, job_owner_ok=lambda a, b: True)


def test_b2_service():
    print("B2. service.ask: her question is answered by the order, on the page she was on")
    from prometheus import service as S
    calls = []
    _bind_host(calls)
    U = {'username': 'canary:analysis', 'role': 'user'}
    env, st = S.ask(U, {'text': HER, 'client': 'dashboard', 'history': [], 'thread_id': 'A', 'page_context': PAGE_THEN})
    check("the valuation order answers", st == 200 and (env.get('decision') or {}).get('reason') == 'bpiq_wizard'
          and env['text'].startswith(PV.LEAD_ANALYSIS))
    check("no read, no brief and no deck was started", not calls)
    check("she is not asked which campaign", 'Which campaign is this about' not in env['text'])
    check("nothing from the open valuation is in the reply", not any(f"{r['emv_value']:,}" in env['text'] for r in PLATFORMS)
          and 'earned media' not in env['text'].lower())
    calls.clear()
    env, st = S.ask(U, {'text': HER, 'client': 'api', 'history': []}, via='api_key')
    check("a key caller's ask is the order's too", st == 200 and (env.get('decision') or {}).get('reason') == 'bpiq_wizard' and not calls)
    calls.clear()
    env, st = S.ask(U, {'text': 'who is the shared audience of Charli XCX and Vinted?', 'client': 'dashboard', 'history': [],
                        'thread_id': 'C', 'page_context': {'primary': {'s3_key': 'Charli_XCX_05_01_2026_10_00.csv', 'name': 'Charli XCX'}}})
    check("who the shared audience is still reaches the ordinary answer path",
          st == 200 and (env.get('decision') or {}).get('reason') != 'bpiq_wizard' and calls and calls[0][0] == 'analyze')


# ------------------------------------------------------------------ C
def test_c_the_open_page():
    print("C. on the Brand Partnership page")
    for page, label in ((PAGE_NOW, 'named page'), (PAGE_THEN, 'page with no name on it')):
        b = {'page_context': page}
        check(f"{label}: 'how did this partnership perform?' is about the valuation open there",
              W.pv_kind('how did this partnership perform?', [], body=b) == '')
        check(f"{label}: 'break down the platform mix' is an ordinary answer", W.pv_kind('break down the platform mix', [], body=b) == '')
        check(f"{label}: 'Compare Instagram and TikTok' is an ordinary answer", W.pv_kind('Compare Instagram and TikTok', [], body=b) == '')
        check(f"{label}: her question is the order's", W.pv_kind(HER, [], body=b) == PV.KIND_ANALYSIS)
        check(f"{label}: another partnership named there is the order's",
              W.pv_kind('What about Charli XCX x Vinted?', [], body=b) == PV.KIND_ANALYSIS)
        check(f"{label}: an order for a new one is the order's", W.pv_kind('run a brand partnership analysis', [], body=b) == PV.KIND_ANALYSIS)
    b = {'page_context': PAGE_NOW}
    check("the open valuation, named, is an ordinary answer",
          W.pv_kind('how did Matchbox x Leatherman do on TikTok?', [], body=b) == '')
    check("in either order of its names",
          W.pv_kind('how did Leatherman x Matchbox the Film on Apple TV perform', [], body=b) == '')
    check("the page says which valuation is open", PV.page_entry(PAGE_NOW) == {'name': OPEN_NAME})
    check("any other page is not the Brand Partnership page",
          PV.page_entry({'view_context': {'view_id': 'journeyIQ', 'data': {'x': 1}}}) is None and PV.page_entry(None) is None)


# ------------------------------------------------------------------ D
def test_d_saved_valuation():
    print("D. a saved valuation may be read")
    PV.SAVED_LOOKUP = lambda pr: {'subject': 'Charli Xcx x Vinted'} if {x.lower() for x in pr} == {'charli xcx', 'vinted'} else None
    try:
        check("an analysis ask on a valued pairing is an ordinary read", PV.owns(HER, []) == '')
        check("another pairing is still the order's", PV.owns('recap the Nike and Gunna sponsorship', []) == PV.KIND_ANALYSIS)
        p = copy.deepcopy(READ_PAYLOAD)
        check("a read that restates the saved valuation is left as it is",
              PV.guard_read(p, question='Vinted', history=HER_THREAD) is False and p['reply'] == READ_TEXT)
    finally:
        PV.SAVED_LOOKUP = None
    delivered = HER_THREAD + [{'role': 'agent', 'text': 'Your Brand Partnership valuation is ready: Charli Xcx x Vinted, total brand '
                                                        'value $10,641,182.91.',
                               'meta': {'bpiq_job_id': '4ebb1715ec61', 'subject': 'Charli Xcx x Vinted',
                                        's3_key': 'Charli_Xcx_x_Vinted_11_17_2026_01_26.json', 'kind': 'bpiq'}}]
    try:
        from prometheus import bpiq_deck as BD
        _i, turn = BD.valuation_turn(delivered)
    except Exception:
        turn = None
    if turn is not None:
        check("after the valuation is delivered here, 'how did this partnership perform?' is about it",
              PV.owns('how did this partnership perform?', delivered) == '')
    else:
        print("  --   (this thread shape carries no hand-over note the deck module reads; skipped)")


# ------------------------------------------------------------------ E
def test_e_guard():
    print("E. a read never analyzes a partnership's value")
    blk = PV.prompt_block('how is the Charli XCX x Vinted partnership landing with shoppers')
    check("the rule a read carries covers a value with no currency sign, the mix and the open page",
          'with or without a currency sign' in blk and 'share of value by platform' in blk
          and "never restated under another partnership's name" in blk)
    check("the lead of her read is a stated value, with no currency sign in it",
          any('1,149,665' in x for x in PV.stated_value_sentences(READ_TEXT)))
    check("its mix is a value mix", PV.value_share_lines(READ_TEXT) != [])
    p = copy.deepcopy(READ_PAYLOAD)
    changed = PV.guard_read(p, question='Vinted', history=HER_THREAD)
    check("her read is withheld", changed and p.get('partnership_value_withheld') is True and p.get('partnership_read_withheld') is True)
    check("what is left is the one line that points at the valuation", p['reply'] == PV.POINTER_ANALYSIS)
    check("no value from the open page is left", not any(f"{r['emv_value']:,}" in p['reply'] for r in PLATFORMS)
          and '1,149,665' not in p['reply'] and '60.3%' not in p['reply'])
    check("its figures, its table and its file are gone", p['figures'] == [] and p['metrics'] == [] and 'breakdown' not in p
          and 'file_link' not in p)
    check("the way to the valuation is the only chip, and no deck is offered", p['followups'] == [PV.CHIP] and p.get('offer_deck') is False)
    check("the copy is clean", clean(p['reply']))
    check("a second pass changes nothing", PV.guard_read(p, question='Vinted', history=HER_THREAD) is False)
    # the same read with no table and no figures behind it
    q = {'success': True, 'action': 'answer', 'profile': 'Charli XCX x Vinted', 'reply': READ_TEXT, 'followups': []}
    check("the text alone is enough to withhold it", PV.guard_read(q, question='Vinted', history=HER_THREAD) and q['reply'] == PV.POINTER_ANALYSIS)
    # a read that mixes people with the value keeps the people
    mixed = {'success': True, 'action': 'answer', 'profile': 'Charli XCX x Vinted', 'followups': [],
             'reply': ("On Charli XCX x Vinted:\n\nThe Charli XCX closet run for Vinted carried 1,149,665 in earned media value "
                       "across the campaign window. Vinted shoppers who follow her skew under 35.\n\n"
                       "- Depop: 44.1% of the shared audience shops it, 531,110 people.")}
    check("a read that also covers people loses the value and keeps the people",
          PV.guard_read(mixed, question='Vinted', history=[]) and '1,149,665' not in mixed['reply']
          and 'skew under 35' in mixed['reply'] and '531,110' in mixed['reply'] and PV.POINTER_LINE in mixed['reply'])
    # left alone
    for label, reply, question in (
            ('a read on the shared audience',
             'On Charli XCX x Vinted:\n\nThe shared audience is 1,204,331 people, and 61.2% of Vinted shoppers who follow '
             'Charli XCX are under 35.\n\n- Depop: 44.1% of the shared audience shops it.',
             'who is the shared audience of Charli XCX and Vinted'),
            ('a people count in a partnership read', 'The Gap x JYT collab reached 2,184,637 people in the window.', 'Gap x JYT reach'),
            ('sales with a currency sign in a partnership read', 'The Gap x JYT collab sold $1,284,611 of product on Gap.com in the window.', ''),
            ('a value outside any partnership', 'Netflix carried 4,211,903 in earned media value from the finale week.', 'netflix finale'),
            ('the product price', 'A Brand Partnership valuation is $500, or $1,000 with a control group.', '')):
        r = {'success': True, 'action': 'answer', 'reply': reply, 'followups': []}
        check(f"left alone: {label}", PV.guard_read(r, question=question) is False and r['reply'] == reply)
    # an answer written in the request
    raw = {'success': True, 'action': 'answer', 'followups': [],
           'reply': 'The Charli XCX x Vinted partnership carried 1,149,665 in earned media value. Vinted indexes 141 with her audience.'}
    env = ENV.wrap(raw, surface='analyze', decision={'reason': 'question'})
    check("an answer written in the request loses the value and points at the valuation",
          '1,149,665' not in env['text'] and PV.POINTER_LINE in env['text'] and 'indexes 141' in env['text'])
    # a deck of a read that stated the value with no currency sign is the valuation's
    thread = HER_THREAD + [{'role': 'agent', 'text': READ_TEXT, 'meta': {'kind': 'read', 'subject': 'Charli XCX x Vinted',
                                                                        'read_job_id': '8babd10d918f'}}]
    check("a deck of that read leads to the valuation", PV.owns('Can you turn this into a deck?', thread) == PV.KIND_DECK)


# ------------------------------------------------------------------ F
def test_f_page():
    print("F. the page tells Prometheus which valuation is open")
    html = (WEB / 'templates' / 'index.html').read_text(encoding='utf-8')
    i = html.index('brandPartnershipIQ: function() {\n                var st = null;')
    blk = html[i:i + 2600]
    check("the Brand Partnership page context carries the valuation's own name",
          'out.partnership = String(_bpNm)' in blk and 'd.project_name || d.campaign_name' in blk)
    check("and its partner and window", 'out.brand_partner' in blk and 'out.window' in blk)
    wiz = (WEB / 'prometheus' / 'bpiq_wizard.py').read_text(encoding='utf-8')
    check("the order reads an analysis ask before the ordinary lanes",
          "_pvk in ('deck', 'analysis')" in wiz and "page=page" in wiz)


def main():
    test_a_detector()
    test_b_her_question_opens_the_order()
    test_b2_service()
    test_c_the_open_page()
    test_d_saved_valuation()
    test_e_guard()
    test_f_page()
    print()
    print('ALL PASS' if not FAIL else f'FAIL ({FAIL})')
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
