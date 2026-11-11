#!/usr/bin/env python3
"""The intake guard: an armed step only consumes an answer (2026-11-05).

Jenna, 2026-11-05 (thread 30c18c8a76, then 8bfc2b5c60): the Brand
Partnership Valuation wizard, armed at a step, read a Subscriber IQ ask,
a Power: Raising Kanan autoplay question (eight times, 'What are the
campaign dates?') and a Jeopardy discovery question on ANOTHER thread
('I could not read those dates.'). "I keep asking a question and
prometheus keeps trying to force me into a brand partnership valuation
fix."

Holds:
  1. Jenna's two exact messages against an armed dates step and an armed
     talent step are not consumed; the wizard steps aside; the reply that
     answers the question carries one trailing 'Back to the valuation'
     chip; the draft stays at the same step.
  2. 'October 7 - 28' and 'Oct 7 to Oct 28' are consumed as dates.
  3. 'cancel' cancels. 'Dark Matter' is consumed as a name at the talent
     step; 'Pull Subscriber IQ for dark matter ...' is not.
  4. A message on thread B while thread A is armed is not consumed: the
     shared stash, the in-process store, and the draft the widget echoes
     back are all keyed by (user, thread).
  5. 'Back to the valuation' and 'back to it' re-ask the open step.
  6. 'I could not read those dates' fires only for an attempt at dates.
  7. The flywheel wizard, the legacy breakout, the twin flag, the
     referents clarifier and the memory confirm honor the same rule.
  8. The widget names its thread on every ask.

Hermetic: REGRESSION_TEST_MODE=1, in-process stores, no network.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import intake_guard as G  # noqa: E402
from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import flywheel_wizard as FW  # noqa: E402
from prometheus import envelope  # noqa: E402

FAIL = 0
TODAY = '2026-11-05'
POWER = ("how many power raising kanan season 5 viewers autoplay'ed into fightland  and then of those what percent "
         "completed that entire season of fightland")
JEOPARDY = ("which content creator drove discovery to YouTube edition of Jeopardy (volume 2) and what their last touch "
            "point before watching and how did they discover the show?")
SUBIQ = 'Pull Subscriber IQ for dark matter (series Latest season) on Apple TV+'


def check(label, cond):
    global FAIL
    if cond:
        print(f"  ok  {label}")
        return
    FAIL += 1
    print(f"  FAIL  {label}")


def hook(text, store, body=None, history=None, user='tester', thread_id='t1'):
    G.reset_resume()
    body = dict(body or {})
    body.setdefault('thread_id', thread_id)
    return W.analyze_hook(text, body, list(history or []), user, {}, user, {'_pm_bpiq_intent': W.is_chip},
                          first=False, today=TODAY, store=store)


def arm(store, upto='dates', user='tester', thread_id='t1'):
    """A draft open at `upto` on (user, thread), through the wizard's own turns."""
    G.reset_resume()
    W.analyze_hook(W.CHIP, {'thread_id': thread_id}, [], user, {}, user, {'_pm_bpiq_intent': W.is_chip},
                   first=True, today=TODAY, store=store)
    if upto == 'talent':
        return store.load(user, thread_id)
    hook('Jennifer Beals', store, user=user, thread_id=thread_id)
    if upto == 'campaign':
        return store.load(user, thread_id)
    hook('Ganni x Lululemon', store, user=user, thread_id=thread_id)
    return store.load(user, thread_id)


def wizard_history(step):
    prompt = {W.STEP_TALENT: W.ASK_TALENT, W.STEP_CAMPAIGN: W.ASK_CAMPAIGN, W.STEP_DATES: W.ASK_DATES}[step]
    return [{'role': 'user', 'text': W.CHIP}, {'role': 'agent', 'text': prompt, 'meta': {'options': [{'label': 'Cancel', 'send': 'Cancel'}]}}]


# ------------------------------------------------------------ 1. the rule
def test_rule():
    print("rule: what an armed step consumes")
    for kind in ('dates', 'name', 'short', 'yes_no', 'choice'):
        check(f"Power question is not an answer to a {kind} step", not G.is_answer_to_step(POWER, kind))
        check(f"Jeopardy question is not an answer to a {kind} step", not G.is_answer_to_step(JEOPARDY, kind))
    check("October 7 - 28 answers a dates step", G.is_answer_to_step('October 7 - 28', 'dates'))
    check("Oct 7 to Oct 28 answers a dates step", G.is_answer_to_step('Oct 7 to Oct 28', 'dates'))
    check("August 28 to September 25, 2026 answers a dates step", G.is_answer_to_step('August 28 to September 25, 2026', 'dates'))
    check("last 90 days answers a dates step", G.is_answer_to_step('last 90 days', 'dates'))
    check("cancel answers any step", G.is_answer_to_step('cancel', 'dates') and G.is_control('cancel'))
    check("Dark Matter is a name", G.is_answer_to_step('Dark Matter', 'name'))
    check("Jennifer Beals is a name", G.is_answer_to_step('Jennifer Beals', 'name'))
    check("What We Do in the Shadows is a title, not a question", G.is_answer_to_step('What We Do in the Shadows', 'name'))
    check("Subscriber IQ order is not a name", not G.is_answer_to_step(SUBIQ, 'name'))
    check("Subscriber IQ order is a product order", G.is_build_order(SUBIQ))
    check("build a profile for Paw Patrol is not a name", not G.is_answer_to_step('build a profile for Paw Patrol', 'name'))
    check("did dark matter get renewed is a question", not G.is_answer_to_step('did dark matter get renewed', 'name'))
    check("a 9-word talent reply is over the name budget", not G.is_answer_to_step('the one with the actor from the thing yesterday', 'name'))
    check("a 9-word campaign line fits the short budget", G.is_answer_to_step('the Jennifer Beals NCIS New York premiere campaign', 'short'))
    check("Accept $1,000 is a control word", G.is_control('Accept $1,000'))
    check("Yes answers a yes/no step", G.is_answer_to_step('Yes', 'yes_no'))
    check("Find the assets is the chip", G.is_answer_to_step('Find the assets', 'choice', chips=['Find the assets', 'Cancel']))
    check("Power question is not a dates attempt", not G.is_dates_attempt(POWER))
    check("Jeopardy question is not a dates attempt", not G.is_dates_attempt(JEOPARDY))
    check("Octobr 7 thru 28 is a dates attempt", G.is_dates_attempt('Octobr 7 thru 28'))
    check("'no idea' is not a dates attempt", not G.is_dates_attempt('no idea'))
    check("Back to the valuation resumes", G.is_resume('Back to the valuation'))
    check("back to it resumes", G.is_resume('back to it'))
    check("where were we resumes", G.is_resume('where were we'))
    check("Back to the valuation names its flow", G.resume_flow('Back to the valuation') == 'brand_partnership')
    hist = [{'role': 'agent', 'text': 'About 1.2M.', 'meta': {'options': [{'label': 'Back to the flywheel', 'send': 'Back to the flywheel'}]}}]
    check("back to it resolves the flow from the thread's chip", G.resume_flow('back to it', hist) == 'flywheel')
    check("brief test: direction answer stays with the intake", not G.is_new_ask_for_brief('what watchers do after and in addition'))
    check("brief test: Power question leaves the intake", G.is_new_ask_for_brief(POWER))
    check("brief test: short figure ask leaves the intake", G.is_new_ask_for_brief('how many people watched it'))
    check("notice: a twin-ready turn is a notice", G.last_agent_is_notice([{'role': 'agent', 'text': 'Your twin is ready: X is live.'}]))
    check("notice: a step question is not", not G.last_agent_is_notice([{'role': 'agent', 'text': W.ASK_DATES}]))
    check("same_thread: unknown side matches", G.same_thread('', 'a') and G.same_thread('a', ''))
    check("same_thread: different ids do not", not G.same_thread('a', 'b'))


# ------------------------------------------------ 2. the wizard steps aside
def test_wizard_dates_step():
    print("wizard: Jenna's messages at the dates step")
    store = W.MemoryStore()
    d = arm(store, 'dates')
    check("armed at dates", d and d.get('step') == W.STEP_DATES)
    for msg in (POWER, JEOPARDY):
        out = hook(msg, store, history=wizard_history(W.STEP_DATES))
        check(f"not consumed: {msg[:40]!r}", out is None)
        check("the way back is marked", (G.pending_resume() or {}).get('chip') == W.RESUME_CHIP)
        d2 = store.load('tester', 't1')
        check("draft still at dates", d2 and d2.get('step') == W.STEP_DATES and d2.get('talent') == 'Jennifer Beals')
        check("wants() says no", not W.wants(msg, {'thread_id': 't1'}, wizard_history(W.STEP_DATES), store=store, user='tester', thread_id='t1'))
    # the reply that answers the question carries the chip (envelope.wrap reads the mark)
    G.reset_resume()
    hook(POWER, store, history=wizard_history(W.STEP_DATES))
    env = envelope.wrap({'success': True, 'action': 'answer', 'reply': 'About 412,870 viewers.', 'followups': []},
                        surface='analyze', decision={}, thread_id='t1')
    labels = [o['label'] for o in env['options']]
    check("reply carries one trailing Back to the valuation chip", labels == [W.RESUME_CHIP])
    check("raw followups carry it too", env['raw']['followups'] == [W.RESUME_CHIP])
    env2 = envelope.wrap({'success': True, 'action': 'answer', 'reply': 'x', 'followups': []}, surface='analyze', decision={})
    check("attach is idempotent per ask (same mark, still one chip)", [o['label'] for o in env2['options']] == [W.RESUME_CHIP])
    G.reset_resume()
    env3 = envelope.wrap({'success': True, 'action': 'answer', 'reply': 'x', 'followups': []}, surface='analyze', decision={})
    check("no mark, no chip", env3['options'] == [])
    # dates still consumed
    out = hook('October 7 - 28', store, history=wizard_history(W.STEP_DATES))
    check("October 7 - 28 consumed", out is not None and (out.get('bpiq_wizard') or {}).get('campaign_start') == '2026-10-07')
    store = W.MemoryStore()
    arm(store, 'dates')
    out = hook('Oct 7 to Oct 28', store, history=wizard_history(W.STEP_DATES))
    check("Oct 7 to Oct 28 consumed", out is not None and (out.get('bpiq_wizard') or {}).get('campaign_end') == '2026-10-28')
    # could-not-read fires only for a dates attempt
    store = W.MemoryStore()
    arm(store, 'dates')
    out = hook('Octobr 7 thru 28th', store, history=wizard_history(W.STEP_DATES))
    check("a failed dates attempt gets the could-not-read line", out and out['reply'].startswith('I could not read those dates'))
    out = hook('no idea', store, history=wizard_history(W.STEP_DATES))
    # asked plainly, and never in the words the last turn already used (2026-11-11)
    check("a reply that never tried is asked plainly", out and out['reply'] == W.ASK_DATES_ALT
          and not out['reply'].startswith('I could not read'))
    # cancel cancels
    out = hook('cancel', store, history=wizard_history(W.STEP_DATES))
    check("cancel cancels", out and out['reply'] == W.CANCEL_REPLY and store.load('tester', 't1') is None)


def test_wizard_talent_step():
    print("wizard: Jenna's messages at the talent step")
    store = W.MemoryStore()
    d = arm(store, 'talent')
    check("armed at talent", d and d.get('step') == W.STEP_TALENT)
    for msg in (POWER, JEOPARDY, SUBIQ):
        out = hook(msg, store, history=wizard_history(W.STEP_TALENT))
        check(f"not consumed: {msg[:40]!r}", out is None)
        check("marked", (G.pending_resume() or {}).get('chip') == W.RESUME_CHIP)
        d2 = store.load('tester', 't1')
        check("draft still at talent, no talent set", d2 and d2.get('step') == W.STEP_TALENT and not d2.get('talent'))
    out = hook('Dark Matter', store, history=wizard_history(W.STEP_TALENT))
    check("Dark Matter consumed as the talent", out is not None and (out.get('bpiq_wizard') or {}).get('talent') == 'Dark Matter')
    check("then the campaign is asked", out and out['reply'] == W.ASK_CAMPAIGN)
    # resume re-asks the open step
    store = W.MemoryStore()
    arm(store, 'dates')
    hook(POWER, store, history=wizard_history(W.STEP_DATES))
    hist = wizard_history(W.STEP_DATES) + [{'role': 'user', 'text': POWER},
                                           {'role': 'agent', 'text': 'About 412,870 viewers.', 'meta': {'options': [{'label': W.RESUME_CHIP, 'send': W.RESUME_CHIP}]}}]
    out = hook(W.RESUME_CHIP, store, history=hist)
    check("Back to the valuation re-asks the dates", out and out['reply'] == W.ASK_DATES)
    out = hook('back to it', store, history=hist)
    check("back to it re-asks the dates", out and out['reply'] == W.ASK_DATES)
    check("wants() takes the resume chip", W.wants(W.RESUME_CHIP, {'thread_id': 't1'}, hist, store=store, user='tester', thread_id='t1'))
    # a reply after the answered question still lands on the open step
    out = hook('October 7 - 28', store, history=hist)
    check("dates typed after the detour are consumed", out and (out.get('bpiq_wizard') or {}).get('campaign_start') == '2026-10-07')
    # the way back survives without the store (the thread alone)
    out = hook('back to it', W.MemoryStore(), history=hist)
    check("thread alone: back to it re-asks the open step", out and out['reply'] == W.ASK_DATES)


# ----------------------------------------------------------- 3. per thread
def test_per_thread():
    print("per thread: an intake armed on one thread never reads another")
    store = W.MemoryStore()
    arm(store, 'dates', thread_id='A')
    other = [{'role': 'agent', 'text': 'Your twin is ready: PValley on Starz Viewers is live.', 'meta': {'twin_key': 'k'}}]
    for msg in ('Dark Matter', 'October 7 - 28', POWER):
        out = hook(msg, store, history=other, thread_id='B')
        check(f"thread B not consumed: {msg[:30]!r}", out is None)
        check("thread B: no resume mark either (nothing armed there)", G.pending_resume() is None)
        check("wants() on thread B says no", not W.wants(msg, {'thread_id': 'B'}, other, store=store, user='tester', thread_id='B'))
    dA = store.load('tester', 'A')
    check("thread A still armed at dates", dA and dA.get('step') == W.STEP_DATES)
    check("the store answers thread A", store.load('tester', 'A') is not None)
    check("the store does not answer thread B", store.load('tester', 'B') is None)
    check("the store answers a caller that knows no thread", store.load('tester', '') is not None)
    out = hook('October 7 - 28', store, history=wizard_history(W.STEP_DATES), thread_id='A')
    check("thread A still consumes its dates", out and (out.get('bpiq_wizard') or {}).get('campaign_start') == '2026-10-07')
    # the draft the widget echoes back from thread A does not arm thread B
    echoed = dict(dA)
    check("emitted drafts carry their thread", str((out.get('bpiq_wizard') or {}).get('thread_id')) == 'A')
    out = hook('Dark Matter', W.MemoryStore(), body={'bpiq_wizard': echoed, 'bpiq_inputs': True}, history=other, thread_id='B')
    check("widget echo from A + collect flag does not arm B", out is None)
    check("wants() ignores the echo on B", not W.wants('Dark Matter', {'thread_id': 'B', 'bpiq_wizard': echoed, 'bpiq_inputs': True},
                                                      other, store=W.MemoryStore(), user='tester', thread_id='B'))
    out = hook('Dark Matter', W.MemoryStore(), body={'bpiq_inputs': True}, history=other, thread_id='B')
    check("collect flag alone does not arm a thread whose last turn is a notice", out is None)
    out = hook('Dark Matter', W.MemoryStore(), body={'bpiq_wizard': echoed}, history=wizard_history(W.STEP_DATES), thread_id='A')
    check("the echo fills in on its own thread when the store missed (a name at the dates step is asked for dates again, plainly)",
          out is not None and out['reply'] == W.ASK_DATES_ALT and W.step_from_prompt(out['reply']) == W.STEP_DATES)
    out = hook('Dark Matter', W.MemoryStore(), body={'bpiq_wizard': echoed}, history=other, thread_id='A')
    check("the echo alone does not arm a thread that moved on (the store and the thread are the truth)", out is None)
    # an unstamped echo (minted before the stamp) follows the same rule
    bare = {k: v for k, v in dA.items() if k != 'thread_id'}
    out = hook('Dark Matter', W.MemoryStore(), body={'bpiq_wizard': bare}, history=other, thread_id='B')
    check("unstamped echo on a thread that moved on does not arm it", out is None)
    out = hook('October 7 - 28', W.MemoryStore(), body={'bpiq_wizard': bare}, history=wizard_history(W.STEP_DATES), thread_id='B')
    check("unstamped echo on a thread whose last turn asked the step still works", out is not None)
    out = hook('Dark Matter', W.MemoryStore(), body={'bpiq_wizard': bare}, history=[], thread_id='')
    check("with no thread known (a direct call) the echo stands on its own", out is not None)
    # the S3 stash shape: a stash stamped A is not B's
    doc = dict(dA, thread_id='A')
    check("stash rule: A answers A", W._same_thread(doc.get('thread_id'), 'A'))
    check("stash rule: A does not answer B", not W._same_thread(doc.get('thread_id'), 'B'))


# ------------------------------------------------------------- 4. flywheel
def test_flywheel():
    print("flywheel wizard: the same rule")
    store = FW.MemoryStore()
    body = {'thread_id': 't1'}
    G.reset_resume()
    out = FW.analyze_hook(FW.CHIP, body, [], 'tester', {}, 'tester', {}, first=True, store=store, today=TODAY)
    check("chip opens the entry step", out and out['reply'] == FW.ASK_ENTRY)
    hist = [{'role': 'agent', 'text': FW.ASK_ENTRY}]
    out = FW.analyze_hook(POWER, body, hist, 'tester', {}, 'tester', {}, first=False, store=store, today=TODAY)
    check("Power question not consumed at the entry step", out is None)
    check("marked Back to the flywheel", (G.pending_resume() or {}).get('chip') == 'Back to the flywheel')
    check("draft still at entry", (store.load('tester', 't1') or {}).get('step') == FW.STEP_ENTRY)
    G.reset_resume()
    out = FW.analyze_hook('watched Gilmore Girls on Prime Video', body, hist, 'tester', {}, 'tester', {}, first=False, store=store, today=TODAY)
    check("an entry line is consumed", out is not None and (store.load('tester', 't1') or {}).get('entry_point'))
    step = (store.load('tester', 't1') or {}).get('step')
    hist2 = [{'role': 'agent', 'text': FW.ASK_DATES if step == FW.STEP_DATES else FW.ASK_ECOSYSTEM}]
    G.reset_resume()
    out = FW.analyze_hook(JEOPARDY, body, hist2, 'tester', {}, 'tester', {}, first=False, store=store, today=TODAY)
    check("Jeopardy question not consumed mid-flywheel", out is None)
    G.reset_resume()
    out = FW.analyze_hook('Back to the flywheel', body, hist2 + [{'role': 'user', 'text': JEOPARDY},
                          {'role': 'agent', 'text': 'x', 'meta': {'options': [{'label': 'Back to the flywheel', 'send': 'Back to the flywheel'}]}}],
                          'tester', {}, 'tester', {}, first=False, store=store, today=TODAY)
    check("Back to the flywheel re-asks the open step", out is not None and out.get('fw_collect') is True)
    # per thread: the stash is per (reader, thread); a flag on another thread's notice does not arm
    other = [{'role': 'agent', 'text': 'Your twin is ready: X is live.', 'meta': {'twin_key': 'k'}}]
    G.reset_resume()
    out = FW.analyze_hook('Amazon', {'thread_id': 'B', 'fw_inputs': True}, other, 'tester', {}, 'tester', {}, first=False, store=store, today=TODAY)
    check("thread B with the widget's fw_inputs over a notice is not consumed", out is None)
    check("flywheel stash rule: A does not answer B", not FW._same_thread('A', 'B') and FW._same_thread('', 'B'))


# ------------------------------------------------ 5. the other armed paths
def test_other_paths():
    print("other armed paths")
    from prometheus import referents as R
    clar = [{'role': 'user', 'text': 'rank these three creators by reach'},
            {'role': 'agent', 'text': 'Which three creators?', 'meta': {'referent_clarify': {'question': 'rank these three creators by reach', 'count': 3}}}]
    q, _lbl = R.answer_merge(clar, POWER)
    check("referents clarifier does not take a question as the names", not q)
    q, _lbl = R.answer_merge(clar, 'MrBeast, Kai Cenat, IShowSpeed')
    check("referents clarifier takes names", bool(q))
    from prometheus import service as S
    mc = [{'role': 'user', 'text': 'how many watched it'},
          {'role': 'agent', 'text': 'Do you mean for Will And Grace, or Will & Grace on Hulu?',
           'meta': {'memory_confirm': {'question': 'how many watched it', 'options': [{'label': 'Will And Grace'}, {'label': 'Will & Grace on Hulu'}]},
                    'options': [{'label': 'Will And Grace', 'send': 'Will And Grace'}, {'label': 'Will & Grace on Hulu', 'send': 'Will & Grace on Hulu'}]}}]
    check("memory confirm does not take a question as the pick", S._confirm_answer(POWER, mc) is None)
    check("memory confirm takes 'both'", S._confirm_answer('both', mc) is not None)
    check("the resume chip is never a pickable option", S._UTILITY_CHIP_RX.match('Back to the valuation') is not None)
    from prometheus import pending_intake as PI
    check("pending_intake picks only on a run order", not PI.is_run_order(POWER) and PI.is_run_order('yes, run it'))
    # the legacy breakout: the brief-shaped test
    import re
    legacy = open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8').read()
    check("legacy breakout consults the guard", 'is_new_ask_for_brief' in legacy)
    check("legacy breakout marks the flow for the way back", "_pm_intake_breakout(text, 'flywheel')" in legacy
          and "_pm_intake_breakout(text, 'digital_journey')" in legacy and "_pm_intake_breakout(text, 'attribution')" in legacy)
    check("legacy collect flags are checked against the thread", legacy.count('_pm_collect_flag_applies(body,') >= 3)
    check("legacy resume route re-asks the open intake", '_pm_intake_resume_reply(' in legacy and "_rflow == 'flywheel'" in legacy)
    twin = open(os.path.join(ROOT, 'prometheus', 'digital_twin_lane.py'), encoding='utf-8').read()
    check("twin flag is checked against the thread and marks the way back", 'armed_flag_applies' in twin and 'mark_resume(FLOW)' in twin)
    svc = open(os.path.join(ROOT, 'prometheus', 'service.py'), encoding='utf-8').read()
    check("service resets the mark per ask", '_ig0.reset_resume()' in svc)
    check("service lets the wizard stand aside (no hold_payload re-ask)", '_stood_aside' in svc)
    check("send-file clarifier does not take a question as the file name", 'is_question_shaped(text)' in svc and "mark_resume('send_file')" in svc)
    # 2026-11-11: an unknown id from the dashboard is re-listed under its own id (prometheus.thread_scope);
    # the ask never runs on the active thread
    check("a dashboard ask naming an unknown thread stays on that thread, never the active one",
          'resolve_ask_thread(' in svc and 'relist_thread(uname, t)' in svc and 'tid = active_thread_id(uname)' not in svc)
    env_src = open(os.path.join(ROOT, 'prometheus', 'envelope.py'), encoding='utf-8').read()
    check("every reply passes the attach", 'attach_resume_chip(env, hit)' in env_src)
    # the global cancel (Jenna 2026-11-05, prometheus.intake_reset) is the one lane ahead of the wizard
    check("LANE_ORDER still opens with the wizard, after the cancel lane",
          S.LANE_ORDER[0] == 'intake_cancel' and S.LANE_ORDER[1] == 'bpiq_wizard')


# ------------------------------------------------- 6. service end to end
def test_service_end_to_end():
    """service.ask with a stub host: Jenna's question under an armed dates
    step routes normally and the reply carries the way back; the same
    question on a thread that is not armed carries nothing; a name typed
    at the step is the wizard's."""
    print("service.ask end to end (stub host)")
    from prometheus.host import host
    from prometheus import service as S
    calls = []

    def _analyze_core(user, body, text, history):
        calls.append(('analyze', text))
        return {'success': True, 'reply': 'About 412,870 people.', 'followups': []}

    def _interpret_core(user, body, text, history):
        calls.append(('interpret', text))
        return {'success': True, 'reply': 'About 412,870 people.', 'followups': []}

    host.bind(analyze_core=_analyze_core, interpret_core=_interpret_core,
              deck_core=lambda *a: {'success': True, 'reply': 'deck'},
              ask_hint=lambda **k: None, gate_analyze=lambda u: True,
              gate_pull=lambda u: True, funds_gate=lambda u: None,
              gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
              calm_payload=lambda: {'success': False, 'error': 'calm'},
              error_email=lambda *a: None, has=lambda c: False,
              load_threads_index=lambda u: {'threads': [{'id': 'A'}, {'id': 'B'}], 'active': 'A'}, max_threads=50,
              s3_json=lambda *a, **k: None, s3_put_json=lambda *a, **k: None,
              thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u,
              thread_title_from=lambda t: t[:30], bucket='x', s3_client=None,
              job_owner_ok=lambda a, b: True)
    U = {'username': 'admin', 'role': 'super_admin'}
    armed = wizard_history(W.STEP_DATES)
    for msg in (POWER, JEOPARDY):
        calls.clear()
        env, st = S.ask(U, {'text': msg, 'surface': 'analyze', 'client': 'dashboard', 'history': armed, 'thread_id': 'A',
                            'bpiq_inputs': True, 'page_context': None})
        check(f"routes normally: {msg[:30]!r}", st == 200 and (env.get('decision') or {}).get('reason') != 'bpiq_wizard')
        check("the wizard did not re-ask its step", env.get('text') not in (W.ASK_DATES, 'I could not read those dates. Try something like October 7 - 28.'))
        labels = [o['label'] for o in (env.get('options') or [])]
        check("reply carries Back to the valuation, once, trailing", labels.count(W.RESUME_CHIP) == 1 and labels[-1] == W.RESUME_CHIP)
    # the same question on thread B, whose last turn is a notice: nothing armed, no chip
    other = [{'role': 'agent', 'text': 'Your twin is ready: PValley on Starz Viewers is live.', 'meta': {'twin_key': 'k'}}]
    env, st = S.ask(U, {'text': JEOPARDY, 'surface': 'analyze', 'client': 'dashboard', 'history': other, 'thread_id': 'B', 'bpiq_inputs': True})
    check("thread B routes normally", st == 200 and (env.get('decision') or {}).get('reason') != 'bpiq_wizard')
    check("thread B carries no way-back chip", W.RESUME_CHIP not in [o['label'] for o in (env.get('options') or [])])
    # a name at the armed step is the wizard's
    env, st = S.ask(U, {'text': 'October 7 - 28', 'surface': 'analyze', 'client': 'dashboard', 'history': armed, 'thread_id': 'A'})
    check("dates at the armed step stay in the wizard", st == 200 and (env.get('decision') or {}).get('reason') == 'bpiq_wizard')
    check("the wizard moved on from the dates", env.get('text') != W.ASK_DATES)
    # the dashboard naming a thread the index does not list is not a 404, and the ask stays on that
    # thread (2026-11-11: it used to run on the active one)
    # (the armed history keeps this hermetic: the wizard answers, no reader runs)
    env, st = S.ask(U, {'text': 'October 7 - 28', 'surface': 'analyze', 'client': 'dashboard', 'history': armed, 'thread_id': 'gone'})
    check("dashboard with an unknown thread id is not a 404 and stays on its own thread",
          st == 200 and (env.get('decision') or {}).get('reason') == 'bpiq_wizard' and env.get('thread_id') == 'gone')
    env, st = S.ask(U, {'text': 'October 7 - 28', 'surface': 'analyze', 'client': 'api', 'history': armed, 'thread_id': 'gone'})
    check("an API caller with an unknown thread id still gets the 404", st == 404)


# ------------------------------------------------------------- 7. widget
def test_widget_names_thread():
    print("widget: the ask names its thread")
    idx = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()
    i = idx.find('function _pmV1Request(url, body)')
    block = idx[i:i + 1400]
    check("_pmV1Request sends thread_id from the tab's thread", 'b.thread_id = String(_pmThreadsIdx.active)' in block)


if __name__ == '__main__':
    test_rule()
    test_wizard_dates_step()
    test_wizard_talent_step()
    test_per_thread()
    test_flywheel()
    test_other_paths()
    test_service_end_to_end()
    test_widget_names_thread()
    print()
    if FAIL:
        print(f"FAIL ({FAIL})")
        sys.exit(1)
    print("OK")
