#!/usr/bin/env python3
"""A started valuation order never takes the next message; a question is
never a slot; which brands suit a talent is a read, never a valuation
(Jenna 2026-11-20, her own seat).

The live thread: "Value a brand partnership" walked to its confirm card,
Accept started the order ("It's running"), and two minutes later the
question "Help me identify which 5 brands should partner with <talent>
based on her fan base. and what are the pitch angles for each." came
back as a confirm card with the first sentence as the talent and "And
What Are the Pitch Angles for Each" as the brand, on the first order's
dates, control group and assets; Accept ran a second valuation.

Three causes, each held here:

1. "It's running" was read as an open wizard turn, so the thread (the
   page sends its last twelve turns) was replayed into a draft after the
   start: the first order's dates and settings with no names, at the
   talent step. A started order has left the wizard; nothing is re-armed
   from the thread after it; a repeated Accept is told the order is
   already running.
2. The one-shot filler split the sentence on its full stop into two names.
   A question is never a name and neither is a clause of one, at any step.
3. The valuation gate read "should partner with" as a valuation order, so
   the Understanding read valuation (which a waiting step never yields
   to) and the wizard kept the message. Which brands suit a talent names
   no pairing to value: brand_fit, a read on the talent; the gate stands
   down and an open order yields to it.

Hermetic: no model, no S3, no mail, no chat module, no app. The lanes run
through service.ask on the stub host (scripts/pm_corpus/replay_corpus.py,
service mode) with invented names.
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ['REGRESSION_TEST_MODE'] = '1'
os.environ['PM_MAIL_OFF'] = '1'

from pm_corpus import replay_corpus as RC  # noqa: E402
RC.install_isolation_light()
from prometheus import understanding as U  # noqa: E402
from prometheus import intents as I  # noqa: E402
from prometheus import intent_classes as IC  # noqa: E402
from prometheus import bpiq_slots as S  # noqa: E402
from prometheus import bpiq_wizard as W  # noqa: E402

TALENT, BRAND = 'Example Talent', 'Northwind Pay'
QUESTION = (f'Help me identify which 5 brands should partner with {TALENT} based on her fan base. '
            'and what are the pitch angles for each.')

# the thread as the dashboard keeps it (its own prompts and chips, no wizard meta on the turns)
WALK = [
    {'role': 'user', 'text': 'Value a brand partnership'},
    {'role': 'agent', 'text': 'Who is the property or talent?', 'meta': {'options': [{'label': 'Cancel', 'send': 'Cancel'}]}},
    {'role': 'user', 'text': TALENT},
    {'role': 'agent', 'text': 'What campaign should I value?', 'meta': {'options': [{'label': 'Cancel', 'send': 'Cancel'}]}},
    {'role': 'user', 'text': BRAND},
    {'role': 'agent', 'text': 'What are the campaign dates?', 'meta': {'options': [{'label': 'Cancel', 'send': 'Cancel'}]}},
    {'role': 'user', 'text': 'September 1, 2026 - November 1, 2026'},
    {'role': 'agent', 'text': f'{TALENT}, {BRAND}: I read the campaign as September 1 to November 1, 2026. The standard window is '
                              '30 days after the campaign ends. That would run past today, so I read it to November 20, 2026. '
                              'Attribution window: September 1 to November 20, 2026.',
     'meta': {'options': [{'label': 'Accept', 'send': 'Accept'}, {'label': 'Use a different window', 'send': 'Use a different window'},
                          {'label': 'Cancel', 'send': 'Cancel'}]}},
    {'role': 'user', 'text': 'Accept'},
    {'role': 'agent', 'text': 'Include a control group?',
     'meta': {'options': [{'label': 'Yes', 'send': 'Yes'}, {'label': 'No', 'send': 'No'}, {'label': 'Cancel', 'send': 'Cancel'}]}},
    {'role': 'user', 'text': 'Yes'},
    {'role': 'agent', 'text': 'Do you have specific campaign links to include, or should I find every applicable campaign asset?',
     'meta': {'options': [{'label': "I'll paste links", 'send': "I'll paste links"}, {'label': 'Find the assets', 'send': 'Find the assets'},
                          {'label': 'Cancel', 'send': 'Cancel'}]}},
    {'role': 'user', 'text': 'Find the assets'},
    {'role': 'agent', 'text': f"Here's what I'll run:\n{TALENT}\n{BRAND}\nCampaign dates: September 1 to November 1, 2026\n"
                              "Attribution window: September 1 to November 20, 2026\nControl group: yes\nI'll find the assets\n"
                              "Included with your plan.",
     'meta': {'options': [{'label': 'Accept', 'send': 'Accept'}, {'label': 'Decline', 'send': 'Decline'}, {'label': 'Cancel', 'send': 'Cancel'}]}},
    {'role': 'user', 'text': 'Accept'},
    {'role': 'agent', 'text': "It's running. Keep talking here as usual. I'll email you when it is done."},
]
STARTED_FULL = WALK                     # the whole thread
STARTED_PAGE = WALK[-12:]               # what the page sends: its last twelve turns (the first order's names fall off)
OPEN_TALENT = WALK[:2]                  # the talent step waits
OPEN_DATES = WALK[:6]                   # the dates step waits
# the second live miss the same evening, sanitized: a who-should-partner question right after a delivered valuation
# started a new order with the whole question as the talent ("What campaign should I value?")
NFL_QUESTION = ('what digital creators should Northwind Sports partner with to bring new women 18-24 viewers to its championship '
                'game who do not already watch the sport but who would tune in if the creator made a culturally compelling piece of content?')
DELIVERED = WALK + [
    {'role': 'agent', 'text': f'{TALENT}, {BRAND}: Total brand value $1,492,072.48. Attributable to the partnership: $812,000.11. '
                              'The deck is on the Brand Partnership tab.'},
]


def _clear_flags():
    for k in list(os.environ):
        if k.startswith('PM_U_OFF_') or k == 'PM_UNDERSTANDING':
            os.environ.pop(k, None)


class StartedOrder(unittest.TestCase):
    """1. A started order has left the wizard."""

    def test_its_running_is_not_a_wizard_turn(self):
        self.assertFalse(W.last_agent_is_wizard(STARTED_FULL))
        self.assertFalse(W.last_agent_is_wizard(STARTED_PAGE))
        self.assertTrue(W.last_agent_is_wizard(WALK[:-2]))          # the confirm card still waits for its Accept
        self.assertTrue(W.last_agent_is_wizard(OPEN_DATES))

    def test_the_thread_is_not_replayed_into_a_draft_after_a_start(self):
        self.assertIsNone(W.replay_history(STARTED_FULL))
        self.assertIsNone(W.replay_history(STARTED_PAGE))
        self.assertIsNone(W.open_draft({}, STARTED_PAGE, None, 'seat', 't1'))

    def test_a_walk_before_the_start_still_replays(self):
        d = W.replay_history(WALK[:-2])
        self.assertIsNotNone(d)
        self.assertEqual(d.get('step'), W.STEP_CHARGE)
        self.assertEqual(d.get('talent'), TALENT)

    def test_a_new_order_after_a_start_opens_clean(self):
        d = W.replay_history(STARTED_FULL + [
            {'role': 'user', 'text': 'Value a brand partnership'},
            {'role': 'agent', 'text': 'Who is the property or talent?'}])
        self.assertIsNotNone(d)
        self.assertEqual(d.get('step'), W.STEP_TALENT)
        self.assertFalse(d.get('talent'))
        self.assertFalse(d.get('campaign_start'))                    # nothing of the started order rides into the new one

    def test_the_wizard_does_not_want_the_question_after_a_start(self):
        self.assertFalse(W.wants(QUESTION, {'thread_id': 't1'}, STARTED_PAGE, store=W.store_for({}), user='seat', thread_id='t1'))
        self.assertFalse(W.wants('what is her audience like', {'thread_id': 't1'}, STARTED_PAGE, store=W.store_for({}), user='seat', thread_id='t1'))

    def test_a_repeated_accept_after_the_start_is_the_wizards_and_says_it_is_running(self):
        store = W.store_for({})
        self.assertTrue(W.wants('Accept', {'thread_id': 't1'}, STARTED_PAGE, store=store, user='seat', thread_id='t1'))
        out = W.analyze_hook('Accept', {'thread_id': 't1'}, list(STARTED_PAGE), {'username': 'seat'}, {}, 'seat', {}, store=store)
        self.assertIsNotNone(out)
        self.assertEqual(out['reply'], W.ALREADY_RUNNING_REPLY)
        self.assertIsNone(out.get('bpiq_wizard'))
        self.assertIsNone(store.load('seat', 't1'))                  # nothing re-armed


class NeverASlot(unittest.TestCase):
    """2. A question is never a name, and neither is a clause of one."""

    def test_the_one_shot_filler_takes_no_names_from_the_question(self):
        d = W.empty_draft()
        d['step'] = W.STEP_TALENT
        out = W.apply_ahead(dict(d), QUESTION)
        self.assertFalse(out.get('talent'))
        self.assertFalse(out.get('campaign'))
        self.assertFalse(out.get('brand_partner'))

    def test_the_filler_takes_no_names_from_a_question_on_a_part_filled_draft(self):
        # the draft the replay used to leave after a start: dates and settings, no names
        d = W.empty_draft()
        d.update({'step': W.STEP_TALENT, 'campaign_start': '2026-09-01', 'campaign_end': '2026-11-01', 'control_group': True, 'asset_mode': 'find'})
        out = W.apply_ahead(dict(d), QUESTION)
        self.assertFalse(out.get('talent'))
        self.assertFalse(out.get('brand_partner'))

    def test_a_valuation_order_asked_as_a_question_keeps_its_names(self):
        d = W.empty_draft()
        d['step'] = W.STEP_TALENT
        out = W.apply_ahead(dict(d), 'can you value Gunna x Nike? the campaign ran June 3 to August 20, 2026')
        self.assertEqual(out.get('campaign'), 'Gunna x Nike')
        self.assertEqual(out.get('campaign_start'), '2026-06-03')

    def test_the_name_steps_ask_again_on_a_question(self):
        for step, q in ((W.STEP_TALENT, 'Which brands fit her audience?'), (W.STEP_TALENT, QUESTION),
                        (W.STEP_CAMPAIGN, 'what are the pitch angles for each'), (W.STEP_CAMPAIGN, 'and what are the pitch angles for each')):
            d = W.empty_draft()
            d['step'] = step
            if step == W.STEP_CAMPAIGN:
                d['talent'] = TALENT
            out, err = W.apply_answer(dict(d), q)
            self.assertTrue(err, (step, q))
            self.assertFalse(out.get('talent') if step == W.STEP_TALENT else out.get('campaign'), (step, q))

    def test_a_clause_is_not_a_name(self):
        for bad in ('And What Are the Pitch Angles for Each', 'which brands should partner with her', 'based on her fan base',
                    'help me identify the brands', 'so what would work'):
            self.assertFalse(W._name_shaped(bad), bad)
        for good in (TALENT, BRAND, 'Gunna x Nike', 'What We Do in the Shadows', 'Who Wants to Be a Millionaire', 'Harbor Lights'):
            self.assertTrue(W._name_shaped(good), good)

    def test_the_pair_splitter_never_splits_a_question(self):
        self.assertIsNone(S.pair_answer(QUESTION))
        self.assertIsNone(S.pair_answer('should she work with Nike and Adidas'))
        self.assertEqual(S.pair_answer('Kevin Hart and Chase Bank')[:2], ('Kevin Hart', 'Chase Bank'))


class BrandFit(unittest.TestCase):
    """3. Which brands suit a talent is a read on the talent, never a valuation order."""

    def test_the_gate_stands_down_on_an_open_recommendation(self):
        for q in (QUESTION, 'Which brands fit her audience?', f'what brands would work with {TALENT}', f'Find me five potential partners for {TALENT}',
                  f'who should {TALENT} partner with', 'what are the pitch angles for Northwind Beauty'):
            self.assertTrue(I.brand_fit_ask(q), q)
            self.assertFalse(I.bpiq_intent(q), q)

    def test_a_valuation_order_and_a_named_pairing_brief_are_still_the_valuations(self):
        for q in ('Mastercard is partnering with Mr. Beast, I want to understand why this is a good idea',
                  'Value the Stanley x Target partnership', 'can you value Gunna x Nike?', 'Value a brand partnership',
                  f'what is the {TALENT} x {BRAND} partnership worth',
                  f'which brands should partner with {TALENT} and what would each deal be worth'):
            self.assertFalse(I.brand_fit_ask(q), q)
            self.assertTrue(I.bpiq_intent(q), q)

    def test_partnership_words_inside_a_question_never_open_the_wizard(self):
        # Jenna 2026-11-20: "it already ran my valuation now I want to ask other questions. make sure it is
        # answering my questions in chat". The wizard starts on an explicit valuation order only.
        for q in (NFL_QUESTION, f'should Nike partner with {TALENT}?',
                  'why it makes sense for Mastercard to partner with Mr. Beast over potential other creators',
                  'Is Charli XCX a good fit for a Vinted partnership?', 'who should the league partner with'):
            self.assertFalse(I.bpiq_intent(q), q)
            self.assertFalse(W.is_start(q), q)
        self.assertTrue(I.brand_fit_ask(NFL_QUESTION))

    def test_the_understanding_reads_brand_fit_with_confidence(self):
        _clear_flags()
        u = U.features(QUESTION, {}, list(STARTED_PAGE), {})
        self.assertEqual(u.intent, 'brand_fit')
        self.assertGreaterEqual(u.confidence, U.WIZARD_YIELD_CONF)
        self.assertEqual(u.action, 'answer')
        self.assertIn('brand_fit', U.WIZARD_YIELDS_TO)
        self.assertEqual(IC.class_of_intent('brand_fit'), 'read')

    def test_the_understanding_reads_it_the_same_while_a_step_waits(self):
        _clear_flags()
        u = U.features(QUESTION, {}, list(OPEN_TALENT), {})
        self.assertEqual(u.intent, 'brand_fit')
        self.assertFalse(u.scope.armed_step)

    def test_the_reader_cannot_turn_it_into_a_valuation(self):
        _clear_flags()
        u = U.features(QUESTION, {}, list(OPEN_TALENT), {})
        u = U.absorb_reading(u, {'intent': 'valuation', 'confidence': 0.9, 'subjects': [{'as_written': TALENT, 'kind': 'talent'}]})
        self.assertEqual(u.intent, 'brand_fit')
        u = U.absorb_reading(u, {'intent': 'analyze', 'confidence': 0.9, 'subjects': [{'as_written': TALENT, 'kind': 'talent'}]})
        self.assertEqual(u.intent, 'analyze')                          # the analysis read is the same class

    def test_the_switch_restores_the_old_reading(self):
        os.environ['PM_U_OFF_BRAND_FIT'] = '1'
        try:
            u = U.features(QUESTION, {}, list(OPEN_TALENT), {})
            self.assertNotEqual(u.intent, 'brand_fit')
        finally:
            os.environ.pop('PM_U_OFF_BRAND_FIT', None)


class ThroughTheDoor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _clear_flags()
        cls.stub = RC._Stub()
        cls.stub.bind()

    def _ask(self, text, history, surface='analyze', view=''):
        return RC._service_once(self.stub, {'id': 'x', 'question': text, 'view': view, 'surface': surface, 'subject': None,
                                            'history': list(history), 'reading': None})

    def _env(self, text, history, thread_id='t-door'):
        from prometheus import service
        self.stub.hints.clear()
        self.stub.cores.clear()
        self.stub.reading = None
        body = {'text': text, 'client': 'dashboard', 'persist': False, 'surface': 'analyze', 'history': list(history),
                'context': None, 'thread_id': thread_id}
        with self.stub.app.test_request_context('/api/prometheus/v1/ask', method='POST', headers={'X-Prometheus-Caller': 'replay'}):
            env, _status = service.ask(dict(RC.REPLAY_USER), body)
        return env

    def test_the_live_defect_the_question_after_a_start_is_a_read_on_the_talent(self):
        for hist in (STARTED_PAGE, STARTED_FULL):
            live = self._ask(QUESTION, hist)
            self.assertEqual(live['route'], 'page_analysis', live['reply'][:200])
            self.assertEqual(live['core'], 'analyze')
            self.assertNotIn("Here's what I'll run", live['reply'])
            self.assertNotIn('Pitch Angles', live['reply'])
            self.assertIn('intent=brand_fit', live['u'])
            self.assertEqual(self.stub.charges, [])

    def test_the_question_while_a_step_waits_is_answered_with_the_way_back(self):
        env = self._env(QUESTION, OPEN_TALENT)
        self.assertEqual(env.get('text'), '<core:analyze>')
        opts = [(o.get('label') if isinstance(o, dict) else o) for o in (env.get('options') or [])]
        self.assertIn(W.RESUME_CHIP, opts)
        # the next name lands on the step that waited
        hist = list(OPEN_TALENT) + [{'role': 'user', 'text': QUESTION},
                                    {'role': 'agent', 'text': env.get('text') or '', 'meta': {'options': env.get('options') or []}}]
        env2 = self._env(TALENT, hist)
        self.assertIn('What campaign should I value', env2.get('text') or '')

    def test_a_repeated_accept_after_the_start_is_told_it_is_running(self):
        live = self._ask('Accept', STARTED_PAGE)
        self.assertEqual(live['route'], 'bpiq_wizard', live['reply'][:200])
        self.assertEqual(live['reply'], W.ALREADY_RUNNING_REPLY)
        self.assertEqual(self.stub.charges, [])

    def test_a_who_should_partner_question_after_a_delivered_valuation_is_answered_in_chat(self):
        live = self._ask(NFL_QUESTION, DELIVERED)
        self.assertEqual(live['route'], 'page_analysis', live['reply'][:200])
        self.assertNotIn('What campaign should I value', live['reply'])
        self.assertNotIn("Here's what I'll run", live['reply'])
        self.assertIn('intent=brand_fit', live['u'])
        self.assertEqual(self.stub.charges, [])
        self.assertIsNone(W.store_for({}).load(RC.REPLAY_USER.get('username') or '', 'x'))

    def test_a_valuation_order_after_a_start_opens_a_clean_new_order(self):
        live = self._ask('Value a brand partnership', STARTED_PAGE)
        self.assertEqual(live['route'], 'bpiq_wizard')
        self.assertIn('Who is the property or talent', live['reply'])

    def test_the_switches_restore_the_old_reading_but_never_the_junk_order(self):
        os.environ['PM_U_OFF_BRAND_FIT'] = '1'
        try:
            live = self._ask(QUESTION, STARTED_PAGE)
            self.assertNotIn('intent=brand_fit', live['u'])
            self.assertNotIn("Here's what I'll run", live['reply'])    # the started order still takes nothing
            self.assertNotIn('Pitch Angles', live['reply'])
        finally:
            os.environ.pop('PM_U_OFF_BRAND_FIT', None)


if __name__ == '__main__':
    unittest.main(verbosity=1)
