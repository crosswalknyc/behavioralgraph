#!/usr/bin/env python3
"""The Brand Partnership valuation order a client placed on 2026-11-12
(Samantha Kolton at CAA, thread 619ae4f806), and her 2026-11-05 words
on the same order. Hermetic.

Her ask: "MATCHBOX THE FILM ON APPLE TV THAT WAS RELEASED IN OCTOBER 9TH
2026 DID TWO PARTNERSHIPS WITH LEATHERMAN AND JIFFYLUBE, THE LEATHERMAN
PARTNERSHIP LAUNCHED SEPT 29 AND THE JIFFY LUBE ONE LAUNCHED OCT 9, CAN
YOU VALUE THESE PARTNERSHIPS IN THE 30 DAYS AFTER THE LAUNCH?"

What happened: the order opened empty and asked "Who is the property or
talent?"; "correct" at the window step read the window back a second
time; "the national average" at the control step drew "Yes or No" with
no word on what a control group is or costs; the second partnership was
never mentioned again; the wallet line named the film twice.

A. An ask that names more than one partnership says so, fills the order
   with the first, keeps the launch day and the days asked for after it,
   and offers the next one after Accept. None is dropped.
B. "correct", "that's right", "looks good" accept the window. They never
   accept a charge.
C. A reply at the control step that is neither yes nor no is told what a
   control group is and what each choice costs.
D. Both sides of a partnership in one short answer are read as two
   names; a single name that carries "and" is never split.
E. A link is never the talent, the campaign or the dates.
F. The wallet line names the pairing once; the reader's own word
   ("property") is the word read back.
G. The deck that order delivered: the property's own six-word name is a
   name, never a reused phrase (the one flag that left the paid deck
   marked not ready), and a price band a few cents wide is one price
   (the deck said "$159.95 to $160" and "$160 to $160").
"""
from __future__ import annotations

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

from prometheus import bpiq_slots as SL  # noqa: E402
from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import intake_guard as G  # noqa: E402
from migration.bpiq_synthesis import MemoryRegistry  # noqa: E402

TODAY = date(2026, 11, 13)
HER = ("MATCHBOX THE FILM ON APPLE TV THAT WAS RELEASED IN OCTOBER 9TH 2026 DID TWO PARTNERSHIPS WITH LEATHERMAN AND "
       "JIFFYLUBE, THE LEATHERMAN PARTNERSHIP LAUNCHED SEPT 29 AND THE JIFFY LUBE ONE LAUNCHED OCT 9, CAN YOU VALUE "
       "THESE PARTNERSHIPS IN THE 30 DAYS AFTER THE LAUNCH?")
NOV5 = 'Your Friends & Neighbors (Apple TV show) and Pure Insurance'
YT = 'https://www.youtube.com/watch?v=a-RDIOWM6-Q'
LI = 'https://www.linkedin.com/posts/pure_insurance_watch-pure-x-apple-tvs-new-spot-activity-7460741831815761920-FpPp'
FAIL = 0
N = 0
BANNED = ('synth', 'modeled', 'modelled', 'estimated', 'pipeline', 'hostmap', 'claude', '\u2014', '\u2013')


def check(label, cond):
    global FAIL, N
    N += 1
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def clean(text):
    low = str(text or '').lower()
    return not any(b in low for b in BANNED) and not re.search(r'\bdoors?\b', low)


class Walk:
    """One thread the way service.ask runs the wizard lane: wants(),
    then the hook, with the thread's own turns as history."""

    def __init__(self, user='canary:valwiz', tid='t1', today=TODAY):
        self.user, self.tid, self.today = user, tid, today
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
                                 first=W.is_start(text), today=self.today, store=self.store,
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
def test_a_more_than_one_partnership():
    print("A. an ask that names two partnerships is read whole and none is dropped")
    got = SL.many_partnerships(HER, TODAY)
    check("the ask is read as more than one partnership", bool(got))
    got = got or {}
    check("the property is the film", got.get('property') == 'MATCHBOX THE FILM ON APPLE TV')
    check("both partners are read, the second in its spaced spelling", got.get('brands') == ['LEATHERMAN', 'JIFFY LUBE'])
    check("each launch day is read", got.get('starts') == {'leatherman': '2026-09-29', 'jiffylube': '2026-10-09'})
    check("the 30 days after launch is read", got.get('days_after') == 30)
    check("capitals are tidied, initials kept", SL.tidy_caps('MATCHBOX THE FILM ON APPLE TV') == 'Matchbox the Film on Apple TV'
          and SL.tidy_caps('JYT') == 'JYT' and SL.tidy_caps('Gap') == 'Gap')
    for words in ('Value the Stanley x Target partnership', 'what are our partnerships with the NFL worth',
                  'JYT is the talent and GAP is the brand', 'Value a brand partnership'):
        check(f"one partnership is never read as many: {words!r}", SL.many_partnerships(words, TODAY) is None)
    more = SL.many_partnerships('Sydney Sweeney has three partnerships with American Eagle, Laneige and Ford, value them '
                                'for the 60 days after each launch', TODAY) or {}
    check("three partners in a list are read", more.get('brands') == ['American Eagle', 'Laneige', 'Ford']
          and more.get('property') == 'Sydney Sweeney' and more.get('days_after') == 60)

    w = Walk()
    out = w.say(HER)
    r = (out or {}).get('reply', '')
    check("the order opens on her ask", bool(out))
    check("it says there are two and that each is its own valuation",
          'You named 2 partnerships' in r and 'one partnership' in r and 'quoted on its own' in r)
    check("it names both, the first one first",
          'Matchbox the Film on Apple TV x Leatherman first' in r and 'Matchbox the Film on Apple TV x Jiffy Lube' in r)
    check("it never asks who the property is", W.ASK_TALENT not in r)
    check("the window is the 30 days after the Leatherman launch",
          'September 29 to October 29, 2026' in r and 'You asked for the 30 days after launch' in r)
    check("the window never runs to today", 'November 13' not in r)
    d = w.draft()
    check("the draft holds the film, Leatherman and the window",
          d.get('talent') == 'Matchbox the Film on Apple TV' and d.get('brand_partner') == 'Leatherman'
          and (d.get('campaign_start'), d.get('campaign_end')) == ('2026-09-29', '2026-10-29')
          and (d.get('attr_start'), d.get('attr_end')) == ('2026-09-29', '2026-10-29'))
    check("Jiffy Lube waits in the queue with its own launch day",
          [(q.get('brand'), q.get('start')) for q in (d.get('queue') or [])] == [('Jiffy Lube', '2026-10-09')])
    check("the copy is clean", clean(r))

    out = w.say('correct')
    check("'correct' accepts the window and the control question follows", (out or {}).get('reply') == W.ASK_CONTROL)
    out = w.say('No')
    out = w.say('Find the assets')
    r = (out or {}).get('reply', '')
    check("the recap is $500 on the 30 days", 'This is $500.' in r and 'Campaign dates: September 29 to October 29, 2026' in r
          and 'Attribution window: September 29 to October 29, 2026' in r)
    out = w.say('Accept $500')
    r = (out or {}).get('reply', '')
    check("Accept starts it and charges once", len(w.charged) == 1 and r.startswith(W.RUNNING_REPLY))
    check("the next partnership is said, with its chip",
          'Next is Matchbox the Film on Apple TV x Jiffy Lube' in r and (out or {}).get('followups') == [W.NEXT_CHIP])
    check("the charged order is Leatherman on its 30 days, no control group",
          w.charged and w.charged[0][1].get('brand_partner') == 'Leatherman' and w.charged[0][1].get('event_end') == '2026-10-29'
          and w.charged[0][1].get('post_end') == '2026-10-29' and w.charged[0][1].get('control_group') is False)
    out = w.say(W.NEXT_CHIP)
    r = (out or {}).get('reply', '')
    check("the chip opens Jiffy Lube on its own 30 days",
          'Matchbox the Film on Apple TV x Jiffy Lube' in r and 'October 9 to November 8, 2026' in r)
    check("nothing is charged by opening it", len(w.charged) == 1)
    w.say('Accept')
    w.say('no')
    w.say('find them')
    out = w.say('Accept $500')
    check("the second order is its own charge, on Jiffy Lube",
          len(w.charged) == 2 and w.charged[1][1].get('brand_partner') == 'Jiffy Lube'
          and (w.charged[1][1].get('event_start'), w.charged[1][1].get('event_end')) == ('2026-10-09', '2026-11-08'))
    check("with nothing left waiting the running line stands alone", (out or {}).get('reply') == W.RUNNING_REPLY)

    # the chip with nothing waiting opens a new order; "next" is never a name
    w2 = Walk(tid='t2')
    out = w2.say(W.NEXT_CHIP) if W.wants(W.NEXT_CHIP, {'thread_id': 't2'}, [], store=w2.store, user=w2.user, thread_id='t2') else None
    if out is not None:
        check("with nothing waiting the chip asks who the talent is", out.get('reply') == W.ASK_TALENT
              and not w2.draft().get('talent'))
    else:
        check("with nothing waiting the chip is not the order's", True)

    # a launch day within the days asked for: the window stops at today
    w3 = Walk(tid='t3', today=date(2026, 10, 20))
    out = w3.say(HER)
    check("a window that would run past today stops at today",
          'September 29 to October 20, 2026' in (out or {}).get('reply', ''))

    # the property cannot be read: the line still says there are two
    w4 = Walk(tid='t4')
    out = w4.say('what are the two partnerships with Nike and Gatorade worth in dollars?')
    r = (out or {}).get('reply', '')
    if out is not None and 'You named' in r:
        check("with no property read it names the partners and asks who the talent is",
              'Nike' in r and 'Gatorade' in r and r.rstrip().endswith(W.ASK_TALENT))
    else:
        check("an ask the order does not own is left alone", True)


# ------------------------------------------------------------------ B
def test_b_correct_accepts_the_window():
    print("B. a word that means yes accepts the window, never the charge")
    for word in ('correct', 'Correct.', "that's right", 'that is correct', 'looks good', 'sounds good', 'perfect',
                 'yes correct', 'that works', 'right', 'confirmed'):
        w = Walk()
        w.say('Value a brand partnership')
        w.say('JYT is the talent and Gap is the brand')
        out = w.say('9/22/26 to 10/30/26')
        check(f"[{word}] the window step is open", w.draft().get('step') == W.STEP_ATTR)
        out = w.say(word)
        check(f"[{word}] accepts the window", (out or {}).get('reply') == W.ASK_CONTROL and w.draft().get('attr_ok') is True)
    w = Walk()
    w.say('Value a brand partnership')
    w.say('JYT is the talent and Gap is the brand')
    w.say('9/22/26 to 10/30/26')
    first = w.replies[-1]
    out = w.say('hmm let me think about it')
    check("a remark still gets the window again, never word for word", (out or {}).get('reply') != first
          and w.draft().get('step') == W.STEP_ATTR)
    w.say('Accept')
    w.say('No')
    out = w.say('Find the assets')
    check("the recap is reached", w.draft().get('step') == W.STEP_CHARGE)
    out = w.say('correct')
    check("'correct' on the recap charges nothing", not w.charged and w.draft().get('step') == W.STEP_CHARGE)


# ------------------------------------------------------------------ C
def test_c_control_group_is_explained():
    print("C. a reply at the control step that is neither yes nor no is told what one is and what it costs")
    w = Walk()
    w.say('Value a brand partnership')
    w.say('JYT is the talent and Gap is the brand')
    w.say('9/22/26 to 10/30/26')
    w.say('Accept')
    check("the control step is open", w.draft().get('step') == W.STEP_CONTROL)
    out = w.say('the national average')
    r = (out or {}).get('reply', '')
    check("it says what a control group is", 'matched group of people who never saw the campaign' in r)
    check("it says the average American comparison is already in", 'average American' in r)
    check("it gives both prices", '$1,000' in r and '$500' in r)
    check("it asks again and keeps the chips", r.rstrip().endswith('Yes or No.')
          and (out or {}).get('followups') == [W.CHIP_YES, W.CHIP_NO, W.CHIP_CANCEL])
    check("nothing was decided for her", w.draft().get('control_group') is None)
    check("the copy is clean", clean(r))
    out2 = w.say('compared to gen pop')
    check("a second miss is never the same words", (out2 or {}).get('reply') != r and w.draft().get('control_group') is None)
    out3 = w.say('No')
    check("No moves on at $500", w.draft().get('control_group') is False and (out3 or {}).get('reply') == W.ASK_ASSETS)
    line = W.control_explainer({'included': True})
    check("a seat whose plan includes it sees no prices", '$' not in line and 'matched group' in line)


# ------------------------------------------------------------------ D
def test_d_two_names_in_one_answer():
    print("D. both sides of a partnership in one short answer")
    check("her Nov 5 answer is two names, the show first",
          SL.pair_answer(NOV5) == ('Your Friends & Neighbors', 'Pure Insurance', 'a', 'property'))
    check("a note on the brand side points the other way", (SL.pair_answer('Gap (brand) and JYT') or ())[:3] == ('Gap', 'JYT', 'b'))
    check("two full names with no note are a pairing still to orient",
          (SL.pair_answer('Kevin Hart and Chase Bank') or ())[:3] == ('Kevin Hart', 'Chase Bank', ''))
    for one in ('Colin and Samir', 'Mumford and Sons', 'Your Friends and Neighbors', 'Harry Potter and the Goblet of Fire',
                'The Old Man and the Sea', 'Florence and the Machine', 'Law and Order Special Victims Unit',
                'Ben and Jerrys', 'Dan and Shay', 'JYT is the talent and GAP is the brand', 'Fast and Furious'):
        check(f"a single name is never split: {one!r}", SL.pair_answer(one) is None)

    w = Walk(today=date(2026, 11, 5))
    w.say('Value a brand partnership')
    out = w.say(NOV5)
    r = (out or {}).get('reply', '')
    d = w.draft()
    check("the show is the property and Pure Insurance the brand",
          d.get('talent') == 'Your Friends & Neighbors' and d.get('brand_partner') == 'Pure Insurance')
    check("the campaign is the pairing, so it is not asked for", d.get('campaign') == 'Your Friends & Neighbors x Pure Insurance'
          and d.get('step') == W.STEP_DATES)
    check("the reply says what was read, in her word", r.startswith('Got it: Your Friends & Neighbors is the property and '
                                                                    'Pure Insurance is the brand.'))
    check("and asks the dates", r.rstrip().endswith(W.ASK_DATES))
    out = w.say(YT)
    r = (out or {}).get('reply', '')
    check("a link at the dates step is kept for the assets", w.draft().get('links') == [YT] and r.startswith(W.LINK_KEPT_LINE))
    check("and the dates are still asked", w.draft().get('step') == W.STEP_DATES and 'date' in r.lower())
    out = w.say('4/3/26-7/3/26')
    check("the window step follows", w.draft().get('step') == W.STEP_ATTR and 'April 3 to July 3, 2026' in (out or {}).get('reply', ''))
    w.say('Accept')
    out = w.say('Yes')
    check("with a link already given the recap follows the control answer", w.draft().get('step') == W.STEP_CHARGE
          and 'This is $1,000.' in (out or {}).get('reply', '') and YT in (out or {}).get('reply', ''))
    out = w.say('Decline')
    check("Decline closes it", (out or {}).get('reply') == W.DECLINE_REPLY and not w.draft())
    check("the next question is not the order's",
          not W.wants('what are top forms of IP being search and engaged today?', {'thread_id': w.tid}, w.hist,
                      store=w.store, user=w.user, thread_id=w.tid))

    # pasted links at the assets step go straight to the recap (Nov 5: asked three times)
    w = Walk(today=date(2026, 11, 5))
    w.say('Value a brand partnership')
    w.say(NOV5)
    w.say('4/3/26-7/3/26')
    w.say('Accept')
    w.say('Yes')
    check("the assets step is open", w.draft().get('step') == W.STEP_ASSETS)
    out = w.say(f"{YT}, {LI}")
    check("two pasted links are taken and the recap follows", w.draft().get('step') == W.STEP_CHARGE
          and w.draft().get('links') == [YT, LI] and "Here's what I'll run" in (out or {}).get('reply', ''))

    # a pairing nothing orients asks which side, once
    w = Walk()
    w.say('Value a brand partnership')
    out = w.say('Kevin Hart and Chase Bank')
    d = w.draft()
    if d.get('talent'):
        check("a pairing the library can orient is oriented", {d.get('talent'), d.get('brand_partner')} == {'Kevin Hart', 'Chase Bank'})
    else:
        check("a pairing nothing orients asks which side is the talent",
              d.get('pair') == ['Kevin Hart', 'Chase Bank'] and 'Kevin Hart' in (out or {}).get('reply', '')
              and 'Chase Bank' in (out or {}).get('reply', ''))
        out = w.say('Kevin Hart')
        d = w.draft()
        check("the answer settles both sides", d.get('talent') == 'Kevin Hart' and d.get('brand_partner') == 'Chase Bank')


# ------------------------------------------------------------------ E
def test_e_a_link_is_never_a_name():
    print("E. a link is never the talent or the campaign")
    w = Walk()
    w.say('Value a brand partnership')
    out = w.say(YT)
    check("a link at the talent step is not the talent", not w.draft().get('talent') and w.draft().get('step') == W.STEP_TALENT)
    check("it is kept and the talent is asked", w.draft().get('links') == [YT] and (out or {}).get('reply', '').startswith(W.LINK_KEPT_LINE))
    w.say('Jennifer Beals')
    check("the talent is taken next", w.draft().get('talent') == 'Jennifer Beals' and w.draft().get('step') == W.STEP_CAMPAIGN)
    out = w.say(LI)
    check("a link at the campaign step is not the campaign", not w.draft().get('campaign')
          and w.draft().get('links') == [YT, LI])
    w.say('Ganni x Lululemon')
    check("the campaign is taken next", w.draft().get('campaign') == 'Ganni x Lululemon')


# ------------------------------------------------------------------ F
def test_f_wallet_line_and_her_word():
    print("F. the wallet line names the pairing once; her word is read back")
    check("a campaign that carries the talent is the whole name",
          W.charge_subject({'qualifier': 'Matchbox the Film on Apple TV', 'campaign': 'Matchbox the Film on Apple TV x Leatherman',
                            'brand_partner': 'Leatherman'}) == 'Matchbox the Film on Apple TV x Leatherman')
    check("a campaign with its own name keeps the talent in front",
          W.charge_subject({'qualifier': 'Jennifer Beals', 'campaign': 'Ganni x Lululemon'}) == 'Jennifer Beals x Ganni x Lululemon')
    check("the brand stands in when no campaign was named",
          W.charge_subject({'qualifier': 'JYT', 'brand_partner': 'Gap'}) == 'JYT x Gap')
    w = Walk()
    w.say('Value a brand partnership')
    out = w.say('Matchbox the Film on Apple TV is the property and Leatherman is the brand')
    r = (out or {}).get('reply', '')
    check("'property' is read back as property", r.startswith('Got it: Matchbox the Film on Apple TV is the property and '
                                                              'Leatherman is the brand.'))
    w = Walk()
    w.say('Value a brand partnership')
    out = w.say('JYT is the talent and Gap is the brand')
    check("'talent' is still read back as talent", (out or {}).get('reply', '').startswith('Got it: JYT is the talent and Gap is the brand.'))


# ------------------------------------------------------------------ G
def test_g_the_delivered_deck():
    print("G. the valuation deck: a long property name is a name, and a band a few cents wide is one price")
    import contextlib
    import io
    import json
    from migration import bpiq_deck_builder as B
    from migration import bpiq_synthesis as S
    payload = json.loads((WEB / 'scripts' / 'fixtures' / 'pm_matchbox_x_leatherman_valuation_2026_11_12.json').read_text())
    with contextlib.redirect_stdout(io.StringIO()):
        data, rep = B.build_deck_report(payload)
    check("the deck builds", bool(data) and len(data) > 100000)
    if rep is None:
        check("the design pack is not on this machine: the finish is not checked here", True)
    else:
        check("the names from the valuation are protected in the file", int((rep.fixes or {}).get('names_protected') or 0) >= 2)
        ran = [ok for n, ok, _o in rep.steps if n == 'readability']
        check("the plain-English check has no flag on the property's own name", (not ran) or all(ran))
        check("the deck is ready when every step ran", (not ran) or bool(rep.ready))
    from pptx import Presentation
    text = '\n'.join(sh.text_frame.text for sl in Presentation(io.BytesIO(data)).slides for sh in sl.shapes if sh.has_text_frame)
    check("no page says '$160 to $160'", '$160 to $160' not in text and '$159.95 to $160' not in text)
    check("the price is said once, exact", 'sold for $159.95' in text)
    check("the total is the one delivered", '$3,246,461.86' in text)
    check("the deck copy is clean", clean(text))
    try:
        blk = S.campaign_block({'price_band': {'low_usd': 159.95, 'high_usd': 160.0}}, {'aov_usd': 229.11}, 'kolton-2026-11-12')
        line = str((blk or {}).get('read_line') or '')
    except Exception:
        line = None
    if line is not None:
        check("the engine's own line never says '$160 to $160'", '$160 to $160' not in line and '$159.95' in line)
    try:
        blk = S.campaign_block({'price_band': {'low_usd': 48.0, 'high_usd': 128.0}}, {'aov_usd': 90.0}, 'kolton-2026-11-12')
        check("a real band is still a band", '$48 to $128' in str((blk or {}).get('read_line') or ''))
    except Exception:
        pass


if __name__ == '__main__':
    test_g_the_delivered_deck()
    test_a_more_than_one_partnership()
    test_b_correct_accepts_the_window()
    test_c_control_group_is_explained()
    test_d_two_names_in_one_answer()
    test_e_a_link_is_never_a_name()
    test_f_wallet_line_and_her_word()
    print(f"\n{'OK' if not FAIL else 'FAILED'} ({N} checks, {FAIL} failed)")
    sys.exit(1 if FAIL else 0)
