#!/usr/bin/env python3
"""The Brand Partnership valuation flow a client hit on 2026-11-11
(Samantha Kolton at CAA, thread daedaedbce). Hermetic.

A. "JYT is the talent and GAP is the brand. the partnership started in
   September 2026" fills the wizard's talent, brand and start; it is
   never routed away as a new question; a bare yes / no is never a name.
B. "find the campaign asset", "find it", "you find it", "I don't have
   one", "not sure" are accepted at the assets step; no step is asked in
   the same words twice in a row; a second non-answer takes the step's
   default and says so.
C. The wizard reaches its quote: $500 without a control group, $1,000
   with one.
D. The line that confirms a deck names the subject the reader picked.
E. A deck made from a read says which deck it is and keeps the read's
   figures.
F. A valuation is held at or below a read already delivered on the same
   pairing and window (either order of the names), every lens still
   adds up, and a read on a pairing with a saved valuation is handed the
   valuation's figures. BPIQ_CORPUS_ANCHORS=0 turns both off.
G. A valuation deck with no control group reads as a before and after
   and carries the real logo.
"""
from __future__ import annotations

import copy
import hashlib
import io
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

from prometheus import bpiq_slots as SL  # noqa: E402
from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import deck_pick as DP  # noqa: E402
from prometheus import intake_guard as G  # noqa: E402
from migration import bpiq_anchors as BA  # noqa: E402
from migration import bpiq_synthesis as S  # noqa: E402
from migration.bpiq_synthesis import MemoryRegistry  # noqa: E402
import test_bpiq_dedupe_and_research as T  # noqa: E402  (inputs / prim / campaign fixtures)

TODAY = date(2026, 11, 11)
HER = 'JYT is the talent and GAP is the brand. the partnership started in September 2026'
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


class Walk:
    """One thread the way service.ask runs the wizard lane: wants(),
    then the hook, with the thread's own turns as history."""

    def __init__(self, user='canary:valwiz', tid='t1'):
        self.user, self.tid = user, tid
        self.store = W.MemoryStore()
        self.hist = []
        self.charged = []
        self.registry = MemoryRegistry()
        self.replies = []

    def say(self, text):
        G.reset_resume()
        body = {'thread_id': self.tid}
        mine = W.wants(text, body, self.hist, store=self.store, user=self.user, thread_id=self.tid)
        out = None
        if mine:
            out = W.analyze_hook(text, body, list(self.hist), self.user, {}, self.user, {},
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


# ------------------------------------------------------------------ A
def test_a_sentence_fills_the_slots():
    print("A. a sentence that names the talent, the brand and the start fills the wizard")
    got = SL.parse(HER, TODAY)
    check("the sentence names the talent", got.get('talent') == 'JYT')
    check("the sentence names the brand", got.get('brand') == 'GAP')
    check("the start is September 2026, a month and not a day",
          got.get('start') == '2026-09-01' and got.get('start_precise') is False
          and got.get('start_label') == 'September 2026')
    check("a question is never a slot sentence", not SL.names_slots('who is the talent for the gap campaign?', TODAY))
    check("a plain name is not a slot sentence", not SL.names_slots('Jennifer Beals', TODAY))
    check("a brand whose name carries 'and' stays whole",
          SL.parse('Dolce and Gabbana is the brand', TODAY).get('brand') == 'Dolce and Gabbana')
    check("role first reads too", SL.parse('the talent is JYT and the brand is Gap', TODAY).get('brand') == 'Gap')
    check("'between X and Y' names the pairing",
          (SL.parse('the partnership between JYT and Gap started Sept 22', TODAY).get('talent'),
           SL.parse('the partnership between JYT and Gap started Sept 22', TODAY).get('start')) == ('JYT', '2026-09-22'))

    d = W.empty_draft()
    d['step'] = W.STEP_TALENT
    check("the open talent step takes the sentence as its answer (it is not routed away)",
          W.answers_step(HER, d, today=TODAY))
    check("the intake guard alone would have called it a new question (the 2026-11-11 defect)",
          not G.is_answer_to_step(HER, 'name'))
    for step in (W.STEP_CAMPAIGN, W.STEP_DATES, W.STEP_CONTROL, W.STEP_ASSETS):
        d['step'] = step
        check(f"the sentence is the wizard's at the {step} step too", W.answers_step(HER, d, today=TODAY))
    d['step'] = W.STEP_TALENT
    check("a real question at the talent step still stands aside",
          not W.answers_step('how many people watched Landman on Paramount+ last month?', d, today=TODAY))

    W.CASING_LOOKUP = {'gap': 'Gap - Avid Fan'}.get      # the library's spelling for a name typed in capitals
    try:
        w = Walk()
        out = w.say(W.CHIP)
        check("the chip opens the talent step", out and out['reply'] == W.ASK_TALENT)
        out = w.say(HER)
        check("the wizard answers her sentence (no general read runs in its place)", out is not None)
        dr = w.draft()
        check("talent = JYT", dr.get('talent') == 'JYT')
        check("brand = Gap (the library's spelling for GAP)", dr.get('brand_partner') == 'Gap')
        check("the campaign is the pairing itself", dr.get('campaign') == 'JYT x Gap')
        check("the start is held as September 2026", dr.get('start_hint') == 'September 2026'
              and dr.get('start_guess') == '2026-09-01')
        check("the wizard moved on to the dates", dr.get('step') == W.STEP_DATES)
        check("the reply says what was read", out['reply'].startswith('Got it: JYT is the talent and Gap is the brand.'))
        check("and asks for the day, naming the month she gave", 'September 2026' in out['reply']
              and 'What day did it start' in out['reply'])
        check("the first of the month through today is one tap away", 'September 1 to today' in out['followups'])
        check("the reply maps back to the dates step", W.step_from_prompt(out['reply']) == W.STEP_DATES)
        check("the reply is clean", clean(out['reply']))
    finally:
        W.CASING_LOOKUP = None

    # without a casing table the name stays as typed, never invented
    w = Walk()
    w.say(W.CHIP)
    w.say(HER)
    check("with no library spelling, GAP stays as she typed it", w.draft().get('brand_partner') == 'GAP')

    # a bare yes / no is never a name
    w = Walk()
    w.say(W.CHIP)
    out = w.say('yes')
    check("'yes' at the talent step is not stored as the talent", not w.draft().get('talent'))
    check("the talent is asked for again, in other words", out and out['reply'] == W.ASK_TALENT_ALT)
    w.say('JYT')
    out = w.say('no')
    check("'no' at the campaign step is not stored as the campaign", not w.draft().get('campaign'))
    check("the campaign is asked for again, in other words", out and out['reply'] == W.ASK_CAMPAIGN_ALT)
    stale = dict(W.empty_draft(), talent='yes', campaign='gap x jyt', brand_partner='jyt', step=W.STEP_DATES)
    fixed = W.sane_draft(stale)
    check("a stored draft that holds 'yes' as its talent loses it and goes back to the talent step",
          fixed['talent'] == '' and fixed['step'] == W.STEP_TALENT)
    for word in ('yes', 'Yes.', 'no', 'ok', 'sure', 'nope', 'thanks', '3'):
        d0, err = W.apply_answer(dict(W.empty_draft(), step=W.STEP_TALENT), word, today=TODAY)
        check(f"{word!r} is never a talent", err and not d0.get('talent'))


# ------------------------------------------------------------------ B
def test_b_assets_and_no_repeat():
    print("B. the assets step takes 'find it'; no step is asked twice in the same words")
    for text in ('find the campaign asset', 'find it', 'you find it', "I don't have one", 'not sure',
                 'Find the assets', 'find them', 'idk', 'none', "I don't have any links", 'you do it',
                 'use whatever you can find', 'find every applicable campaign asset', 'No'):
        d = dict(W.empty_draft(), talent='JYT', campaign='JYT x Gap', brand_partner='Gap',
                 campaign_start='2026-09-22', campaign_end='2026-11-11', attr_start='2026-09-22',
                 attr_end='2026-11-11', attr_ok=True, control_group=False, step=W.STEP_ASSETS)
        check(f"{text!r} is an answer to the assets step", W.answers_step(text, d, today=TODAY))
        d2, err = W.apply_answer(d, text, today=TODAY)
        check(f"{text!r} means: find the assets", err is None and d2.get('asset_mode') == 'find'
              and d2.get('step') == W.STEP_CHARGE)
    d = dict(W.empty_draft(), talent='JYT', campaign='JYT x Gap', brand_partner='Gap',
             campaign_start='2026-09-22', campaign_end='2026-11-11', attr_start='2026-09-22',
             attr_end='2026-11-11', attr_ok=True, control_group=False, step=W.STEP_ASSETS)
    d2, err = W.apply_answer(d, "I'll paste links", today=TODAY)
    check("'I'll paste links' still asks for the links", err is None and d2.get('step') == W.STEP_LINKS)
    d2, err = W.apply_answer(d, 'here: https://www.gap.com/browse/jyt', today=TODAY)
    check("a pasted link is taken as the link", err is None and d2.get('links') == ['https://www.gap.com/browse/jyt']
          and d2.get('step') == W.STEP_CHARGE)
    d3, err = W.apply_answer(dict(d, step=W.STEP_LINKS, asset_mode='links'), 'find them', today=TODAY)
    check("'find them' at the links step switches to finding the assets", err is None and d3.get('asset_mode') == 'find')

    # a reply that answers nothing: other words first, then the default
    w = Walk()
    for m in (W.CHIP, 'JYT', 'Gap x JYT', '9/22/26 to today', 'Accept', 'No'):
        w.say(m)
    check("the walk is at the assets step", w.draft().get('step') == W.STEP_ASSETS and w.replies[-1] == W.ASK_ASSETS)
    out = w.say('hmm maybe')
    check("the first non-answer gets the question in other words", out and out['reply'] == W.ASK_ASSETS_ALT
          and out['reply'] != W.ASK_ASSETS)
    out = w.say('hmm maybe')
    check("the second non-answer moves on with the default, said in one plain line",
          out and out['reply'].startswith(W.DEFAULT_FIND_LINE) and "Here's what I'll run" in out['reply'])
    check("the default is: find the assets", w.draft().get('asset_mode') == 'find' and w.draft().get('step') == W.STEP_CHARGE)

    # never the identical words twice in a row, at any step, over a long run of non-answers
    w = Walk()
    w.say(W.CHIP)
    for m in ('yes', 'ok', 'JYT', 'sure', 'yes', 'gap x jyt', 'hmm ok', 'not sure', 'sure', 'Accept', 'thanks'):
        w.say(m)
    twice = [i for i in range(1, len(w.replies)) if ' '.join(w.replies[i].split()) == ' '.join(w.replies[i - 1].split())]
    check("no reply repeats the one before it word for word", not twice)
    check("the names she did give were kept", w.draft().get('talent') == 'JYT' and w.draft().get('campaign') == 'gap x jyt')

    # the hinted dates step: a day on its own, then the default
    w = Walk()
    w.say(W.CHIP)
    w.say(HER)
    out = w.say('the 22nd')
    dr = w.draft()
    check("'the 22nd' under 'started in September 2026' is September 22 through today",
          dr.get('campaign_start') == '2026-09-22' and dr.get('campaign_end') == TODAY.isoformat()
          and dr.get('step') == W.STEP_ATTR)
    check("the window line says the end was read as today", 'If the campaign has ended' in out['reply'])
    out = w.say('it ended October 20')
    dr = w.draft()
    check("an end date typed at the window step is the campaign's end",
          dr.get('campaign_end') == '2026-10-20' and dr.get('step') == W.STEP_ATTR and not dr.get('dates_inferred'))
    w = Walk()
    w.say(W.CHIP)
    w.say(HER)
    first = w.replies[-1]
    out = w.say('not sure')
    check("a non-answer at the hinted dates step is asked in other words",
          out and out['reply'] != first and not first.endswith(out['reply']) and 'September 2026' in out['reply'])
    out = w.say('not sure')
    dr = w.draft()
    check("the second takes the first of the month through today and says so",
          out['reply'].startswith("I'll run it from September 1, 2026 through today.")
          and dr.get('campaign_start') == '2026-09-01' and dr.get('campaign_end') == TODAY.isoformat())
    check("'9/22/26 to today' ends on the wizard's own today", W.parse_dates('9/22/26 to today', today=TODAY)
          == ('2026-09-22', '2026-11-11'))


# ------------------------------------------------------------------ C
def test_c_reaches_the_quote():
    print("C. her messages reach the quote: $500 without a control group, $1,000 with one")
    check("prices are unchanged", (W.PRICE_BASE, W.PRICE_CONTROL) == (500, 1000)
          and W.price_usd(False) == 500 and W.price_usd(True) == 1000)
    for control, usd in (('yes', 1000), ('no', 500)):
        W.CASING_LOOKUP = {'gap': 'Gap Customers'}.get
        try:
            w = Walk()
            for m in (W.CHIP, HER, '9/22/26 to today', 'yes', control, 'find the campaign asset'):
                out = w.say(m)
                check(f"[{control}] the wizard answered {m[:32]!r}", out is not None)
        finally:
            W.CASING_LOOKUP = None
        dr = w.draft()
        check(f"[{control}] the walk ends on the recap", dr.get('step') == W.STEP_CHARGE
              and out['reply'].startswith("Here's what I'll run"))
        check(f"[{control}] the quote is ${usd:,}", f"This is ${usd:,}." in out['reply']
              and f"Accept ${usd:,}" in out['followups'] and dr.get('price') == usd)
        check(f"[{control}] nothing was charged before Accept", not w.charged)
        check(f"[{control}] the recap names JYT and the pairing, never 'Yes'",
              '\nJYT\n' in out['reply'] and 'JYT x Gap' in out['reply'] and 'Yes' not in out['reply'].split('\n')[1])
        inp = W.engine_inputs(dr)
        check(f"[{control}] the engine gets talent JYT, brand Gap, Sep 22 to today, find the assets",
              (inp['qualifier'], inp['brand_partner'], inp['event_start'], inp['event_end'], inp['asset_mode'])
              == ('JYT', 'Gap', '2026-09-22', '2026-11-11', 'find'))
        check(f"[{control}] control group {'on' if usd == 1000 else 'off'}", inp['control_group'] is (usd == 1000)
              and inp['price'] == usd)
        check(f"[{control}] every reply is clean", all(clean(r) for r in w.replies))
        check(f"[{control}] no reply repeats the one before it",
              all(w.replies[i] != w.replies[i - 1] for i in range(1, len(w.replies))))
    # the control question in a few more words
    for text, want in (('yes please', True), ('no thanks', False), ('without one', False), ('include it', True)):
        d, err = W.apply_answer(dict(W.empty_draft(), talent='JYT', campaign='JYT x Gap', campaign_start='2026-09-22',
                                     campaign_end='2026-11-11', attr_start='2026-09-22', attr_end='2026-11-11',
                                     attr_ok=True, step=W.STEP_CONTROL), text, today=TODAY)
        check(f"{text!r} answers the control question", err is None and d.get('control_group') is want)


# -------------------------------------------------------------- D and E
READ_TEXT = ("The Gap x JYT partnership carries $4,217,683 of value in its first 51 days, built on 2,184,637 US "
             "people reached and 137,633 of them already confirmed Gap buyers. Recruitment is the smaller half: "
             "15,147 first-time Gap buyers in 51 days.")
READ_DOC = {'user': 'canary:valwiz', 'payload': {
    'reply': READ_TEXT, 'profile': 'Gap x JYT',
    'metrics': [
        {'name': 'partnership_value_window', 'label': 'Partnership value, Sep 22 to Nov 11 2026', 'unit': 'usd',
         'value': 4217683, 'definition': 'reasoned value of the partnership, not a measured row'},
        {'name': 'partnership_value_year_one', 'label': 'Partnership value, first 12 months', 'unit': 'usd', 'value': 14839261},
        {'name': 'jyt_us_audience', 'label': 'JYT projected US audience', 'unit': 'people', 'value': 2184637,
         'definition': 'distinct US individuals with at least one JYT digital touchpoint in the window'},
        {'name': 'gap_buyers_in_jyt_audience', 'label': 'Gap buyers inside the JYT audience', 'unit': 'people', 'value': 137633},
        {'name': 'new_to_gap_buyers', 'label': 'First-time Gap buyers out of the JYT audience', 'unit': 'people', 'value': 15147},
    ],
    'breakdown': {'dimension': 'Partnership value component', 'share_basis': "share of the partnership's value in the window",
                  'rows': [{'label': 'Social and short-form content', 'share_pct': 38.3, 'penetration_pct': 61.4},
                           {'label': 'Gap.com collection traffic and orders', 'share_pct': 27.1, 'penetration_pct': 18.7}]}}}
THREAD = [
    {'role': 'agent', 'text': 'Visa reaches 61.5% of the Paige Spiranac audience.',
     'meta': {'kind': 'read', 'read_job_id': 'be58d72cd4ca', 'subject': 'Paige Spiranac'}},
    {'role': 'user', 'text': HER},
    {'role': 'agent', 'text': 'On it: reading Gap Customers now.'},
    {'role': 'agent', 'text': READ_TEXT, 'meta': {'kind': 'read', 'read_job_id': 'f21c0ed76df6', 'subject': 'Gap x JYT'}},
    {'role': 'user', 'text': 'Can you turn this into a deck?'},
    {'role': 'agent', 'text': 'Which subject should the deck be on: Gap x JYT, Gap Customers or Paige Spiranac?'},
]
PAGE = {'primary': {'s3_key': 'Paige_Spiranac_05_01_2026_10_00.csv', 'name': 'Paige Spiranac'}, 'cuts': [], 'extras': []}


def library(text, ctx):
    """The library resolver as it behaves for this seat: Paige Spiranac
    and Gap Customers are profiles; Gap x JYT is not, and an ask with no
    library subject falls back to the profile on screen."""
    low = str(text).lower()
    if 'paige spiranac' in low:
        return {'ctx': PAGE, 'subject': 'Paige Spiranac', 'partner': '', 'clarify': None}, None
    if 'gap customers' in low:
        return {'ctx': {'primary': {'s3_key': 'Gap_Customers.csv', 'name': 'Gap Customers'}}, 'subject': 'Gap Customers',
                'partner': '', 'clarify': None}, None
    return {'ctx': ctx, 'subject': 'Paige Spiranac', 'partner': '', 'clarify': None, 'used_open_page': True}, None


def test_d_e_deck_names_the_pick_and_keeps_the_read():
    print("D/E. the deck is on the subject she picked; a deck from a read says so and keeps its figures")
    loads = []

    def load(job):
        loads.append(job)
        return READ_DOC if job == 'f21c0ed76df6' else None

    res, err, msg = DP.settle('Gap x JYT', 'Can you turn this into a deck?', PAGE, THREAD, library, load_read=load)
    check("picking Gap x JYT builds on Gap x JYT, not on the profile on screen",
          res and res.get('subject') == 'Gap x JYT' and not err and not msg)
    check("the deck's material is the read, with no library profile behind it",
          res['ctx'].get('deck_source') == 'read' and res['ctx'].get('primary') is None and loads == ['f21c0ed76df6'])
    line = DP.confirm_line(res)
    check("the confirm line names Gap x JYT", 'Gap x JYT' in line)
    check("the confirm line never names another candidate", 'Paige' not in line and 'Gap Customers' not in line)
    check("the confirm line says which deck this is", 'insights deck from the Gap x JYT read' in line
          and 'not the Brand Partnership valuation' in line)
    check("the confirm line is clean", clean(line))
    mat = '\n'.join(res['ctx']['view_context']['material'])
    check("the material carries the read's own figures", '15,147' in mat and '$4,217,683' in mat and '2,184,637' in mat)
    check("the material says nothing about how a figure was produced", 'reasoned' not in mat and 'measured row' not in mat)
    check("the view data carries words only (no count for the page projection to touch)",
          all(isinstance(v, str) for v in res['ctx']['view_context']['data'].values()))
    angle = DP.read_angle('Gap x JYT', 'Can you turn this into a deck?')
    check("the brief binds the deck to the read", 'exactly as the read states it' in angle and 'nothing added' in angle
          and 'no product' in angle and len(angle) <= 400)

    for pick, want in (('Paige Spiranac', 'Paige Spiranac'), ('Gap Customers', 'Gap Customers')):
        r2, e2, m2 = DP.settle(pick, 'Can you turn this into a deck?', PAGE, THREAD, library, load_read=load)
        check(f"picking {pick} builds on {pick}", r2 and r2.get('subject') == want and not r2.get('from_read'))
        check(f"its confirm line names {pick}", DP.confirm_line(r2).startswith(f'Building the {want} insights deck.'))
    r3, e3, m3 = DP.settle('Old Navy x JYT', 'Can you turn this into a deck?', PAGE, THREAD, library, load_read=load)
    check("a pick with no profile and no read starts no deck and says so in one line",
          r3 is None and e3 is None and 'Old Navy x JYT' in m3 and 'Paige' not in m3 and clean(m3))
    r4, e4, m4 = DP.settle('', 'build a deck from this', PAGE, THREAD, library, load_read=load)
    check("an ask that names nothing resolves as it always did", r4 and r4.get('used_open_page') and not m4)
    r5, _e5, _m5 = DP.settle('', 'build the Gap x JYT insights deck', PAGE, THREAD, library, load_read=load)
    check("words that name a read in the thread build from that read", r5 and r5.get('from_read') and r5['subject'] == 'Gap x JYT')
    check("the pairing is the same pairing in either order", DP.same_subject('Gap x JYT', 'JYT x Gap')
          and not DP.same_subject('Gap x JYT', 'Gap Customers') and not DP.same_subject('Gap x JYT', 'Gap'))
    check("a read record is handed over only to the seat it belongs to",
          DP.load_read_doc(_FakeS3({'system/prometheus_reads/f21c0ed76df6.json': READ_DOC}), 'b', 'system/prometheus_reads/',
                           'f21c0ed76df6', 'someone_else') is None
          and DP.load_read_doc(_FakeS3({'system/prometheus_reads/f21c0ed76df6.json': READ_DOC}), 'b',
                               'system/prometheus_reads/', 'f21c0ed76df6', 'canary:valwiz') is not None)

    # the delivered deck's defect: 48,217 new buyers against the read's 15,147
    figs = res['ctx']['view_context']['read_figures']
    plan = {'title': 'JYT turns Gap browsers into buyers.', 'slides': [
        {'tiles': [{'value': '2,184,637', 'label': 'JYT engagers, projected US people'},
                   {'value': '137,633', 'label': 'Gap buyers already in the audience'},
                   {'value': '48,217', 'label': 'New Gap buyers in 51 days'},
                   {'value': '$4,217,683', 'label': 'Partnership value to date'}],
         'takeaway': 'The partnership added 48,217 first-time buyers in 51 days.'},
        {'cards': [{'title': 'The buyers are new', 'body': '48,217 people placed a first Gap order on a JYT path.'},
                   {'title': 'The year is worth booking', 'body': 'The partnership reads as $14,839,261 over 12 months.'}]}]}
    found = DP.conflicts(plan, figs)
    check("the new-buyer count that disagrees with the read is caught", len(found) >= 3
          and all(c['kind'] == 'new_buyers' and c['found'] == '48,217' and int(c['want']) == 15147 for c in found))
    check("figures the read does state are not flagged",
          not DP.conflicts({'slides': [plan['slides'][0]['tiles'][:2] + plan['slides'][0]['tiles'][3:]]}, figs))
    notes = []

    def replan(note):
        notes.append(note)
        fixed = copy.deepcopy(plan)
        fixed['slides'][0]['tiles'][2]['value'] = '15,147'
        fixed['slides'][0]['takeaway'] = 'The partnership added 15,147 first-time buyers in 51 days.'
        return fixed          # the second card is still wrong: the in-place fix catches it
    held = DP.hold_plan_to_read(copy.deepcopy(plan), res['ctx'], replan=replan, post=lambda p: p)
    blob = repr(held)
    check("the plan was made once more with the conflict spelled out",
          len(notes) == 1 and '48,217' in notes[0] and '15,147' in notes[0] and clean(notes[0]))
    check("the deck ships with the read's own new-buyer count everywhere", '48,217' not in blob and blob.count('15,147') == 3)
    check("nothing is left in conflict", not DP.conflicts(held, figs))
    untouched = DP.hold_plan_to_read(copy.deepcopy(plan), PAGE, replan=replan)
    check("a deck that is not made from a read is not touched", '48,217' in repr(untouched) and len(notes) == 1)

    chat = (WEB / 'prometheus' / 'legacy' / 'chat.py').read_text(encoding='utf-8')
    check("the deck route settles the pick through deck_pick", '_deck_pick.settle(' in chat
          and "if _bind_pick and body.get('confirm_open_screen'):" in chat)
    check("the old fall back to the open page is gone",
          "if res_err or not resolution or resolution.get('clarify'):\n                resolution, res_err = _pm_resolve_deck_subject(text, ctx)" not in chat)
    check("the deck route returns the confirm line", chat.count("'confirm_line': _deck_pick.confirm_line(resolution)") == 2)
    check("the deck job holds the plan to the read", '_deck_pick.hold_plan_to_read(' in chat)
    check("chat.py stays under its line ratchet", chat.count('\n') < 19900)
    html = (WEB / 'templates' / 'index.html').read_text(encoding='utf-8')
    check("the page prints the server's confirm line", 'data.confirm_line ? String(data.confirm_line)' in html)


class _FakeS3:
    def __init__(self, objs):
        self.objs = objs

    def get_object(self, Bucket, Key):
        import json
        if Key not in self.objs:
            raise KeyError(Key)
        return {'Body': io.BytesIO(json.dumps(self.objs[Key]).encode())}


# ------------------------------------------------------------------ F
def ledger(subject, ws, we, value, people, retired=False):
    return {'subjects': {re.sub(r'[^a-z0-9]+', '_', subject.lower()): {'subject': subject, 'entries': [{
        'k': f"{subject}|revenue|{ws}|{we}", 'subject': subject, 'family': 'revenue', 'ws': ws, 'we': we,
        'ts': '2026-10-30T12:00:00Z', 'retired': retired,
        'metrics': [
            {'name': 'partnership_value_window', 'label': 'Partnership value in the window', 'unit': 'usd', 'value': value},
            {'name': 'partnership_value_year_one', 'label': 'Partnership value, first 12 months', 'unit': 'usd', 'value': value * 3.5},
            {'name': 'talent_us_audience', 'label': 'Projected US audience', 'unit': 'people', 'value': people},
            {'name': 'brand_buyers', 'label': 'Lululemon buyers inside the audience', 'unit': 'people', 'value': 9173},
            {'name': 'new_buyers', 'label': 'First-time Lululemon buyers', 'unit': 'people', 'value': 1387},
            {'name': 'pen', 'label': 'Purchase penetration', 'unit': 'pct', 'value': 7.6}]}]}}}


def lenses_add_up(p):
    v = p['valuation']
    parts = (v['brand_engagement_value'], v['earned_media_value'], v['brand_lift_value'], v['conversion_value'])
    return (abs(round(sum(parts), 2) - v['total_brand_value']) < 0.01
            and abs(round(sum(r['emv_value'] for r in v['emv_breakdown']), 2) - v['earned_media_value']) < 0.01
            and abs(v['brand_engagement_value'] - p['totals']['post_users_projected'] * v['rates']['bev_per_user']) < 0.01
            and abs(v['brand_lift_value'] - v['incremental_users'] * v['rates']['blv_per_incr_user']) < 0.01)


def no_round_counts(p):
    bad = []

    def walk(o, path=''):
        if isinstance(o, dict):
            for k, x in o.items():
                walk(x, f"{path}/{k}")
        elif isinstance(o, list):
            for i, x in enumerate(o):
                walk(x, f"{path}[{i}]")
        elif isinstance(o, int) and not isinstance(o, bool) and o >= 100 and o % 10 == 0 and 'rates' not in path \
                and 'diagnostics' not in path and 'days' not in path:
            bad.append((path, o))
    for k in ('audience_size', 'projected_audience_size', 'totals', 'per_platform', 'conversions', 'valuation'):
        walk(p.get(k), k)
    return not bad


def test_f_valuation_never_starts_cold():
    print("F. a valuation is held at or below a read already delivered on the pairing; a read gets a saved valuation")
    inp = T.inputs(control=False)
    windows = S._dates(inp)
    camp = S.normalize_campaign(T.campaign(), inp)
    cold = S.build_payload(inp, T.prim(), campaign=camp)
    tot0, ppl0 = cold['valuation']['total_brand_value'], cold['projected_audience_size']
    check("the cold valuation adds up", lenses_add_up(cold))

    # the lookup: either order of the names, an overlapping window, a live entry
    idx = ledger('Lululemon x Jennifer Beals', '2026-10-15', '2026-10-29', round(tot0 * 0.71, 0) + 3, ppl0 * 5 + 7)
    a = BA.banked_read(inp, windows, index=idx)
    check("a read filed brand first is found for a valuation asked talent first", a and a['subject'] == 'Lululemon x Jennifer Beals')
    check("its figures are read: the window's value, not the 12-month one; the people reached; the buyers",
          a and a['value_usd'] == round(tot0 * 0.71, 0) + 3 and a['people'] == ppl0 * 5 + 7
          and a['brand_buyers'] == 9173 and a['first_time_buyers'] == 1387)
    check("the same read is found filed talent first",
          BA.banked_read(inp, windows, index=ledger('Jennifer Beals x Lululemon', '2026-10-15', '2026-10-29', 1e6, 5e5)) is not None)
    check("a read on another pairing is not this pairing's",
          BA.banked_read(inp, windows, index=ledger('Lululemon x Someone Else', '2026-10-15', '2026-10-29', 1e6, 5e5)) is None
          and BA.banked_read(inp, windows, index=ledger('Lululemon', '2026-10-15', '2026-10-29', 1e6, 5e5)) is None)
    check("a read whose window does not overlap is not used",
          BA.banked_read(inp, windows, index=ledger('Lululemon x Jennifer Beals', '2026-06-01', '2026-06-30', 1e6, 5e5)) is None)
    check("a retired read is not used",
          BA.banked_read(inp, windows, index=ledger('Lululemon x Jennifer Beals', '2026-10-15', '2026-10-29', 1e6, 5e5, retired=True)) is None)
    check("no ledger read happens in a hermetic run", BA.banked_read(inp, windows) is None)

    # the research prompt is handed the read
    pf = BA.prompt_fields(a)
    check("the prompt carries the delivered read and a binding rule",
          pf['delivered_read']['partnership_value_usd'] == a['value_usd']
          and pf['delivered_read']['people_reached_us'] == a['people'] and pf['delivered_read_rule'].startswith('Binding.'))
    check("no read, no prompt fields", BA.prompt_fields(None) == {})

    # the hold: down only, under both ceilings, every lens recomputed
    rebuild = lambda f: S.build_payload(inp, T.prim(), campaign=camp, hold=f)   # noqa: E731
    held, rec = BA.hold(cold, a, inp, windows, rebuild, salt=S.run_salt(inp))
    tot1, ppl1 = held['valuation']['total_brand_value'], held['projected_audience_size']
    check("the total lands at or below the read's value, close under it and not on it",
          tot1 <= a['value_usd'] and tot1 >= a['value_usd'] * 0.93 and abs(tot1 - a['value_usd']) > 1)
    check("the people reached never went up", ppl1 <= ppl0 and ppl1 <= a['people'])
    check("every lens, row and line still adds up after the hold", lenses_add_up(held))
    check("shares and lifts did not move",
          abs(held['totals']['audience_pen_post_pct'] - cold['totals']['audience_pen_post_pct']) < 0.15
          and abs(held['totals']['audience_pen_pre_pct'] - cold['totals']['audience_pen_pre_pct']) < 0.15
          and held['valuation']['talent_share'] == cold['valuation']['talent_share'])
    check("her share is her share of the held total",
          abs(held['valuation']['attributable_to_talent']
              - round(held['valuation']['total_brand_value'] * held['valuation']['talent_share'], 2)) < 0.01)
    check("counts stay messy", no_round_counts(held))
    check("dollars keep their cents", isinstance(tot1, float) and round(tot1, 2) == tot1)
    check("the record says what was held and by how much",
          rec.get('read_key') == a['key'] and 0 < rec['factor'] < 1 and rec['total_before_usd'] == tot0
          and rec['total_after_usd'] == tot1)
    same, rec2 = BA.hold(cold, a, inp, windows, rebuild, salt=S.run_salt(inp))
    check("the same run draws the same hold", same['valuation']['total_brand_value'] == tot1 and rec2['factor'] == rec['factor'])

    # pull down only
    high = BA.banked_read(inp, windows, index=ledger('Lululemon x Jennifer Beals', '2026-10-15', '2026-10-29', tot0 * 3, ppl0 * 9))
    kept, rec3 = BA.hold(cold, high, inp, windows, rebuild, salt=S.run_salt(inp))
    check("a valuation already under the read is not touched (never pulled up)", kept is cold and rec3 == {})
    # the people ceiling binds too
    lowp = BA.banked_read(inp, windows, index=ledger('Lululemon x Jennifer Beals', '2026-10-15', '2026-10-29', tot0 * 3, int(ppl0 * 0.6) + 1))
    heldp, _r = BA.hold(cold, lowp, inp, windows, rebuild, salt=S.run_salt(inp))
    check("people reached above the read's are held at or below it",
          heldp['projected_audience_size'] <= lowp['people'] and lenses_add_up(heldp))
    # a longer valuation window scales the ceiling; a window inside the read keeps it
    cap_in = BA.ceilings(a, inp, windows)
    longer = dict(inp, post_end='2026-11-28')
    cap_long = BA.ceilings(a, longer, S._dates(longer)) if date.today() >= date(2026, 11, 28) else None
    check("a window inside the read's keeps the read's own figures",
          cap_in['value_usd'] == a['value_usd'] and cap_in['people'] == a['people'])
    if cap_long:
        check("a longer window scales the ceilings up", cap_long['value_usd'] > a['value_usd'] and cap_long['people'] > a['people'])

    # end to end through synthesize, with the hold saved and replayed
    calls, prompts = [], []

    def fake_claude(system, user_prompt, **kw):
        calls.append(kw.get('surface'))
        prompts.append(user_prompt)
        if kw.get('surface') == 'bpiq_campaign_research':
            return T.campaign()
        if kw.get('surface') in ('bpiq_sellout_check', 'bpiq_campaign_footprint'):
            return {}
        return T.prim()
    real_loader = BA._load_ledger_index
    BA._load_ledger_index = lambda: idx
    try:
        store = S.MemoryUnderpinnings()
        p1 = S.synthesize(inp, fake_claude, underpinnings=store, job_id='j1')
        check("the valuation engine holds its total to the delivered read",
              p1['valuation']['total_brand_value'] <= a['value_usd'] and lenses_add_up(p1))
        check("the research prompt was handed the delivered read",
              any('"delivered_read"' in str(p) and 'delivered_read_rule' in str(p) for p in prompts))
        check("the hold is on the run's own record", (p1['diagnostics'].get('held_to_delivered_read') or {}).get('read_key') == a['key'])
        saved = store.load(S.run_key(inp))
        check("the underpinnings keep the read and the hold", saved.get('banked_read', {}).get('key') == a['key']
              and 0 < saved.get('hold', {}).get('factor', 0) < 1)
        BA._load_ledger_index = lambda: (_ for _ in ()).throw(AssertionError('a repeat run looks nothing up'))
        n = len(calls)
        p2 = S.synthesize(inp, fake_claude, underpinnings=store, job_id='j2')
        check("a repeat run replays the same hold and draws nothing again",
              len(calls) == n and p2['valuation']['total_brand_value'] == p1['valuation']['total_brand_value'])
        # the kill switch
        BA._load_ledger_index = lambda: idx
        os.environ[BA.ENV] = '0'
        p3 = S.synthesize(inp, fake_claude, underpinnings=S.MemoryUnderpinnings(), job_id='j3')
        check("BPIQ_CORPUS_ANCHORS=0: the valuation builds exactly as before",
              p3['valuation']['total_brand_value'] == tot0 and 'held_to_delivered_read' not in p3['diagnostics'])
        check("BPIQ_CORPUS_ANCHORS=0: no lookup, no block", BA.banked_read(inp, windows, index=idx) is None
              and BA.valuation_block('Gap x JYT', anchors_for=lambda *a, **k: {'facts': [{}]}) == '')
    finally:
        os.environ.pop(BA.ENV, None)
        BA._load_ledger_index = real_loader
    check("with no read on the pairing the engine builds as before",
          S.synthesize(inp, fake_claude, underpinnings=S.MemoryUnderpinnings())['valuation']['total_brand_value'] == tot0)

    # the reverse: a read on a pairing with a saved valuation is handed the valuation
    asked = []

    def anchors_for(subject, window=None, products=None, with_ledger=True):
        asked.append(subject)
        if subject.lower() != 'jyt x gap':      # the catalog resolves a name whatever its case
            return {'subject': subject, 'facts': []}
        w = {'start': '2026-09-22', 'end': '2026-11-11'}
        mk = lambda kind, label, value: {'product': 'bpiq', 'kind': kind, 'label': label, 'value': value,   # noqa: E731
                                         'window': w, 'as_of': '2026-11-11T21:00:00', 'source_key': 'brand-partnership-iq/JYT_x_Gap.json'}
        return {'subject': 'JYT x Gap', 'facts': [
            mk('audience_size', 'US audience reached by the partnership', 311618),
            mk('valuation', 'total brand value, USD', 4129457.18),
            mk('valuation', 'value attributable to the partnership, USD', 424080.0),
            mk('valuation', 'incremental US users', 84816)]}
    blk = BA.valuation_block(HER, anchors_for=anchors_for)
    check("her sentence names the pairing", BA.pairing_in(HER) == ('JYT', 'GAP'))
    check("'Gap x JYT' in a question names the pairing", BA.pairing_in('what is the Gap x JYT partnership worth') == ('Gap', 'JYT'))
    check("a pairing typed in lower case is still the pairing", BA.pairing_in('what is gap x jyt worth so far') == ('gap', 'jyt'))
    check("a question that names no pairing gets no block", BA.valuation_block('how does paige index with visa?', anchors_for=anchors_for) == '')
    check("the saved valuation is found whichever name comes first", 'JYT x GAP' in asked and 'GAP x JYT' in asked and bool(blk))
    check("the read is handed the valuation's own figures, binding",
          '$4,129,457.18' in blk and '311,618' in blk and '(binding)' in blk)
    check("a valuation on a window that does not overlap the read's is not handed over",
          BA.valuation_block(HER, window={'start': '2026-01-01', 'end': '2026-03-31'}, anchors_for=anchors_for) == '')
    chat = (WEB / 'prometheus' / 'legacy' / 'chat.py').read_text(encoding='utf-8')
    check("the read lanes carry the saved valuation block", chat.count('_bpiq_wiz.saved_valuation_block(text') >= 2)
    src = (WEB / 'migration' / 'bpiq_synthesis.py').read_text(encoding='utf-8')
    check("the engine also looks the pairing up brand first in the catalog", 'partnership_brand_first' in src)


# ------------------------------------------------------------------ G
def deck_texts(blob):
    from pptx import Presentation
    prs = Presentation(io.BytesIO(blob))
    texts, pics = [], []
    for sl in prs.slides:
        for sh in sl.shapes:
            if sh.has_text_frame:
                t = ' '.join(p.text for p in sh.text_frame.paragraphs).strip()
                if t:
                    texts.append(t)
            if sh.shape_type == 13:
                pics.append(hashlib.sha1(sh.image.blob).hexdigest())
    return texts, pics


CONTROL_WORDS = re.compile(r"matched|control|comparison group|exposed group|holdout|adjusted|held .{0,24}baseline|\+0\.00pp", re.I)


def test_g_no_control_deck_reads_before_and_after():
    print("G. a valuation deck with no control group reads as a before and after, with the real logo")
    from migration import bpiq_deck_builder as B
    from migration import design_skill as DS
    inp = T.inputs(control=False)
    camp = S.normalize_campaign(T.campaign(), inp)
    p = S.build_payload(inp, T.prim(), campaign=camp)
    check("the engine names no comparison group when none was bought",
          not CONTROL_WORDS.search(p['valuation']['talent_share_basis'])
          and not CONTROL_WORDS.search(p['valuation']['comparison_note'])
          and not CONTROL_WORDS.search(p['campaign']['talent_line']))
    readme = S.underpinnings_readme(inp, camp, {'aov': 150.0, 'basket': 1.3}, p)
    check("the run's notes define brand lift as a before and after", 'before and after' in readme
          and not CONTROL_WORDS.search(readme))
    texts, pics = deck_texts(B.build_deck(dict(p), image_url=None, category='FASHION'))
    hits = [t for t in texts if CONTROL_WORDS.search(t)]
    check("no page speaks of a matched group, an adjusted lift or a held baseline", not hits)
    if hits:
        print('     ', hits[:3])
    blob = ' '.join(texts)
    check("no '+0.00pp' anywhere", '+0.00pp' not in blob and '0.00pp' not in blob)
    # wording as of the clean-deck rewrite later the same day (test_bpiq_valuation_deck_clean.py):
    # the same before and after read, in the new pages' words
    check("the lift page reads as a before and after", 'since the campaign began' in blob
          and re.search(r"counts only people who engaged with \w+ after launch and had not in the \d+ days before", blob) is not None)
    check("the method note says before and after",
          re.search(r"Brand lift is the change in \w+ engagement inside the same group of people, after launch against before", blob) is not None)
    check("the headline figure is labeled as new since the campaign began", 'OF WHICH NEW SINCE THE CAMPAIGN BEGAN' in blob
          and 'ATTRIBUTABLE TO PARTNERSHIP' not in blob)
    check("the rise is stated against the days before, and the close says what the partnership did",
          re.search(r"\d+ DAYS BEFORE LAUNCH", blob) is not None and re.search(r"\d+ DAYS AFTER LAUNCH", blob) is not None
          and 'got more people engaging with' in blob)
    logo = DS.logo_path('white')
    check("the design pack's logo is on disk", bool(logo) and Path(str(logo)).is_file())
    if logo:
        want = hashlib.sha1(Path(str(logo)).read_bytes()).hexdigest()
        check("the deck carries the real logo (cover and close)", pics.count(want) >= 2)
        from PIL import Image
        check("the logo is a transparent lockup, not an opaque plate", Image.open(str(logo)).mode == 'RGBA')
        check("the builder asks the design pack for it", B._render_crosswalk_logo_png(on_dark=True) == str(logo))
    # a flat window with no control: level, never a held baseline
    flat = copy.deepcopy(p)
    flat['totals']['audience_pen_post_pct'] = flat['totals']['audience_pen_pre_pct']
    flat['totals']['post_users_projected'] = flat['totals']['pre_users_projected']
    t2, _ = deck_texts(B.build_deck(flat, image_url=None, category='FASHION'))
    check("a flat window reads as held before and after, never 'held baseline'",
          not [t for t in t2 if CONTROL_WORDS.search(t)]
          and any('engagement held at' in t and 'before and after' in t for t in t2))
    # with a control group bought, the matched-group account stays
    inc = T.inputs(control=True)
    pc = S.build_payload(inc, T.prim(), campaign=S.normalize_campaign(T.campaign(), inc))
    t3, pics3 = deck_texts(B.build_deck(dict(pc), image_url=None, category='FASHION'))
    b3 = ' '.join(t3)
    check("a valuation bought with a control group keeps its matched-group pages",
          'LIFT OVER THE MATCHED GROUP' in b3 and 'OF WHICH ATTRIBUTABLE TO PARTNERSHIP' in b3
          and 'matched group kept' in pc['valuation']['talent_share_basis'])
    if logo:
        check("and carries the real logo too", pics3.count(want) >= 2)


def main():
    test_a_sentence_fills_the_slots()
    test_b_assets_and_no_repeat()
    test_c_reaches_the_quote()
    test_d_e_deck_names_the_pick_and_keeps_the_read()
    test_f_valuation_never_starts_cold()
    test_g_no_control_deck_reads_before_and_after()
    print()
    if FAIL:
        print(f"FAIL ({FAIL})")
        return 1
    print("OK")
    return 0


if __name__ == '__main__':
    sys.exit(main())
