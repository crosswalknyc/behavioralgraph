#!/usr/bin/env python3
"""What a Brand Partnership valuation costs is said one way (Jenna 2026-11-16).

Samantha Kolton at CAA declined a $1,000 quote, asked "Can you do it
without the control group?" and was told the comparison group "does not
change the price of the run"; then asked "Can we narrow the asset scope
to lower the cost" and was told handing over links would make it
cheaper. With a control group a valuation is $1,000, without one it is
$500, and $500 is the lowest it goes. Hermetic.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))

from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import valuation_price as VP  # noqa: E402

TODAY = date(2026, 11, 16)
FAIL = 0


def check(label, cond):
    global FAIL
    if cond:
        print(f"  ok  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}")


def U(t):
    return {'role': 'user', 'text': t}


def A(t):
    return {'role': 'agent', 'text': t}


RECAP_1000 = ("Here's what I'll run:\nCharli Xcx\nVinted\nCampaign dates: October 22 to November 13, 2026\n"
              "Attribution window: October 22 to November 16, 2026\nControl group: yes\nI'll find the assets\n"
              "This is $1,000.")
# her thread, as it stood when she asked the two questions
THREAD = [
    U('value a brand partnership'), A(W.ASK_TALENT), U('charli xcx'), A(W.ASK_CAMPAIGN), U('vinted'),
    A(W.ASK_DATES), U('10/22-11/13'),
    A('Charli Xcx, vinted: I read the campaign as October 22 to November 13, 2026. The standard window is 30 days '
      'after the campaign ends. That would run past today, so I am using the campaign dates through November 16, '
      '2026. Attribution window: October 22 to November 16, 2026.'),
    U('Accept'), A(W.ASK_CONTROL), U('Yes'), A(W.ASK_ASSETS), U('Find the assets'), A(RECAP_1000),
    U('Decline'), A(W.DECLINE_REPLY),
]


def hook(text, history, store, draft=None):
    body = {'thread_id': 't1'}
    if draft is not None:
        body['bpiq_wizard'] = draft
    return W.analyze_hook(text, body, history, 'tester', {}, 'tester', {'_pm_bpiq_intent': W.is_chip},
                          today=TODAY, store=store)


def no_false_price(reply):
    low = reply.lower()
    return not any(p in low for p in (
        'does not change the price of the run', 'saves nothing', 'not an add-on', 'safe cut', 'discovery first',
        'price the narrowed scope', 'cheap without being expensive', 'shorter list'))


def test_without_control_reopens_the_quote():
    print('1. "Can you do it without the control group?" after a declined $1,000 quote')
    store = W.MemoryStore()
    q = 'Can you do it without the control group?'
    check('the wizard takes the turn', W.wants(q, {'thread_id': 't1'}, THREAD, store=store, user='tester', thread_id='t1'))
    out = hook(q, THREAD, store)
    reply = (out or {}).get('reply') or ''
    check('answers yes with both prices', reply.startswith('Yes. Without a control group this valuation is $500. With one it is $1,000.'))
    check('the order is back, without the control group, at $500',
          "Here's what I'll run:" in reply and 'Control group: no' in reply and reply.rstrip().endswith('This is $500.'))
    check('same talent, campaign and dates as quoted',
          'Charli Xcx\nVinted\nCampaign dates: October 22 to November 13, 2026\nAttribution window: October 22 to November 16, 2026' in reply)
    check('Accept $500 is one tap away', (out or {}).get('followups', [None])[0] == 'Accept $500')
    check('says what is given up, in the order\'s own words', 'matched group of people who never saw the campaign' in reply)
    check('nothing the old answer claimed', no_false_price(reply))
    d = store.load('tester', 't1') or {}
    check('the draft is saved at the charge step at $500',
          d.get('step') == W.STEP_CHARGE and d.get('control_group') is False and d.get('price') == 500)
    check('no job and no charge on this turn', not (out or {}).get('bpiq_job_id') and not (out or {}).get('job_id'))
    # and the Accept that follows runs that order
    seen = {}
    out2 = W.analyze_hook('Accept $500', {'thread_id': 't1'}, THREAD + [U(q), A(reply)], 'tester', {}, 'tester',
                          {'_pm_bpiq_intent': W.is_chip}, today=TODAY, store=store,
                          charge_fn=lambda *a, **k: seen.setdefault('charge', a) and True or True,
                          start_fn=lambda job_id, uname, inputs, *a, **k: seen.setdefault('inputs', inputs) or True)
    inp = seen.get('inputs') or (out2 or {}).get('bpiq_confirm_payload') or {}
    check('Accept runs it without a control group at $500',
          (not inp) or (inp.get('control_group') is False and inp.get('price') == 500))


def test_narrow_scope_does_not_lower_the_price():
    print('2. "Can we narrow the asset scope to lower the cost"')
    store = W.MemoryStore()
    q = 'Can we narrow the asset scope to lower the cost'
    check('the wizard takes the turn', W.wants(q, {'thread_id': 't1'}, THREAD, store=store, user='tester', thread_id='t1'))
    out = hook(q, THREAD, store)
    reply = (out or {}).get('reply') or ''
    check('says the one way to lower it', reply.startswith('The only way to lower the price is to run it without a control group.'))
    check('both prices, and the floor', 'With a control group a valuation is $1,000. Without one it is $500, and that is the lowest it goes.' in reply)
    check('says scope does not change the price', VP.NOTHING_ELSE in reply)
    check('nothing the old answer claimed', no_false_price(reply))
    check('no order was opened or changed', store.load('tester', 't1') is None and not (out or {}).get('bpiq_wizard'))
    check('the chips run the quoted order either way', (out or {}).get('followups') == [VP.RUN_WITHOUT_CHIP, VP.RUN_WITH_CHIP])
    out2 = hook(VP.RUN_WITHOUT_CHIP, THREAD + [U(q), A(reply)], store)
    r2 = (out2 or {}).get('reply') or ''
    check('the chip brings the order back at $500', 'Control group: no' in r2 and r2.rstrip().endswith('This is $500.'))


def test_every_wording_gets_the_same_two_prices():
    print('3. other wordings, same answer')
    for q in ('Can you make it cheaper?', 'Is there a discount if I send the links myself?',
              'What if we only look at Instagram, does the price drop?', 'how much is a brand partnership valuation?',
              'That is too expensive. Can we shorten the window to bring the cost down?',
              'What does a valuation cost?', 'Price a run on client-supplied links with the control group kept'):
        store = W.MemoryStore()
        out = hook(q, THREAD, store)
        reply = (out or {}).get('reply') or ''
        check(f'"{q[:48]}" -> $1,000 with, $500 without', '$1,000' in reply and '$500' in reply and no_false_price(reply))
    out = hook('how much is a brand partnership valuation?', [], W.MemoryStore())
    reply = (out or {}).get('reply') or ''
    check('asked cold: the two prices and the valuation chip',
          reply.startswith('A valuation is $1,000 with a control group and $500 without one. Those are the only two prices.')
          and (out or {}).get('followups') == [W.CHIP])


def test_open_order_keeps_its_step():
    print('4. while the order is open')
    store = W.MemoryStore()
    d = dict(W.quoted_draft(THREAD[:14], today=TODAY))
    check('the quoted order reads back from the thread', d.get('talent', '').lower() == 'charli xcx' and d.get('control_group') is True
          and d.get('campaign_start') == '2026-10-22' and d.get('attr_end') == '2026-11-16' and d.get('price') == 1000)
    store.save('tester', 't1', d)
    out = hook('can you do it without the control group?', THREAD[:14], store)
    reply = (out or {}).get('reply') or ''
    check('at the recap: the control group comes off and the recap reads $500',
          'Control group: no' in reply and reply.rstrip().endswith('This is $500.') and (out or {}).get('followups', [None])[0] == 'Accept $500')
    out = hook('Can we narrow the asset scope to lower the cost', THREAD[:14], store)
    reply = (out or {}).get('reply') or ''
    check('already at $500: says it is the lowest price', reply.startswith('This valuation is already at its lowest price, $500.'))
    # the control question itself is still the step's to read
    d2 = dict(d, step=W.STEP_CONTROL, control_group=None, price=0)
    check('"without one" at the control question is the step\'s answer, not a price turn',
          W.price_kind('without one', THREAD[:10], d2) == '' and W.price_kind('No', THREAD[:10], d2) == '')
    store2 = W.MemoryStore()
    store2.save('tester', 't1', d2)
    out = hook('how much is it with one?', THREAD[:10], store2)
    reply = (out or {}).get('reply') or ''
    check('a price question at the control step gets both prices and the way back',
          '$1,000' in reply and '$500' in reply and W.RESUME_CHIP in ((out or {}).get('followups') or [])
          and (store2.load('tester', 't1') or {}).get('step') == W.STEP_CONTROL)


def test_not_a_price_question():
    print('5. what stays where it was')
    for q in ('What is the Charli XCX x Vinted partnership worth?', 'What was the cost per engagement on the reel?',
              'What exactly do I lose if I drop the control group', 'Which platform carried the most value?',
              'Who are Vinted shoppers?', 'How much is a profile?', 'Was I charged for that?',
              'What does a deck cost?', 'Can I get a refund on the price I paid?',
              'What did the brand partnership cost Vinted?', 'What was the talent fee on this brand partnership?'):
        check(f'"{q[:44]}" is not a price turn', W.price_kind(q, THREAD) == '')
    check('a quote that was accepted and is running is not reopened',
          W.quoted_draft(THREAD[:14] + [U('Accept $1,000'), A(W.RUNNING_REPLY)], today=TODAY) is None)
    check('no quote in the thread, no valuation named: not a price turn', W.price_kind('can you make it cheaper?', []) == '')


def test_written_answer_never_talks_price():
    print('6. the last check on a written answer')
    old22 = ("I can run it without the matched comparison group, but you would be buying a different deliverable. "
             "The comparison group is how lift is produced.\n\nThe practical point: the comparison group does not change "
             "the price of the run. It is part of the standard build, not an add-on. Declining it saves nothing and costs "
             "the one number a partnership valuation exists to produce.\n\nMy recommendation is to run it as originally "
             "scoped. If the hesitation is the spend rather than the method, the sharper trim is window or asset scope, "
             "not the comparison group.")
    raw = {'action': 'answer', 'reply': old22,
           'followups': ['Run it with the comparison group as scoped', 'Can we narrow the asset scope to lower the cost',
                         'Show me the platform earned value split on screen']}
    check('the guard fires', VP.guard_payload(raw, decision={'reason': 'page_analysis'}, question='What does the comparison group change?'))
    r = raw['reply']
    check('the false price lines are gone', no_false_price(r) and 'the sharper trim' not in r)
    check('what the comparison group is stays', 'The comparison group is how lift is produced.' in r)
    check('one line says what the price depends on', r.endswith(VP.POINTER))
    check('the chip that offered a cheaper scope is gone',
          'Can we narrow the asset scope to lower the cost' not in raw['followups'] and VP.PRICE_ASK_CHIP in raw['followups'])
    old24 = ("Yes, and there are two ways to narrow it. Only one of them is cheap without being expensive.\n\nThe safe cut is "
             "discovery. Most of the cost in the quote you saw is me finding every applicable asset across the window "
             "rather than measuring it.\n\nTell me which of those you want and I will price the narrowed scope before "
             "anything runs.\n\nWhat this partnership is worth in dollars comes from a Brand Partnership valuation.")
    raw = {'action': 'answer', 'reply': old24, 'followups': ['Price a run on client-supplied links with no control group']}
    VP.guard_payload(raw, decision={'reason': 'page_analysis'}, question='Can we narrow the asset scope to lower the cost')
    check('asked about the price: the argued answer is replaced whole',
          raw['reply'] == VP.POINTER_FULL and no_false_price(raw['reply']) and raw['followups'] == [VP.PRICE_ASK_CHIP])
    cost = {'action': 'answer', 'reply': 'I cannot see what Vinted paid Charli XCX. The reel reached the most people.',
            'followups': []}
    check('what the campaign cost the brand is not this question',
          not VP.guard_payload(cost, decision={'reason': 'page_analysis'}, question='What did the brand partnership cost Vinted?'))
    read = {'action': 'answer', 'reply': 'Vinted shoppers lean toward cheaper resale and the reel carried the cost per view down. '
                                         'The Brand Partnership tab holds the rest.', 'followups': ['Who else reaches them?']}
    check('a read about shoppers is left alone', not VP.guard_payload(read, decision={'reason': 'page_analysis'}, question='Who are Vinted shoppers?'))
    wiz = {'action': 'answer', 'reply': RECAP_1000, 'bpiq_wizard': {'step': 'charge'}, 'followups': ['Accept $1,000']}
    check('the order\'s own quote is never touched', not VP.guard_payload(wiz, decision={'reason': 'bpiq_wizard'}) and wiz['reply'] == RECAP_1000)


def test_copy_is_clean():
    print('7. copy')
    for name in ('NOTHING_ELSE', 'WITHOUT_WHAT', 'INCLUDED', 'POINTER', 'POINTER_FULL'):
        txt = getattr(VP, name)
        check(f'{name}: no dashes, no internal words', '\u2014' not in txt and '\u2013' not in txt and W.copy_is_clean(txt))
    check('an included seat reads no price', '$' not in VP.answer('$1,000', '$500', lower=True, included=True)
          and '$' not in VP.change_lead('$1,000', '$500', False, included=True))


if __name__ == '__main__':
    for fn in (test_without_control_reopens_the_quote, test_narrow_scope_does_not_lower_the_price,
               test_every_wording_gets_the_same_two_prices, test_open_order_keeps_its_step, test_not_a_price_question,
               test_written_answer_never_talks_price, test_copy_is_clean):
        fn()
    print('\nFAILED' if FAIL else '\nALL PASS', FAIL or '')
    sys.exit(1 if FAIL else 0)
