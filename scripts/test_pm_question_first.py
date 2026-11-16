#!/usr/bin/env python3
"""A question typed where profiles are built is a question first
(Jenna 2026-11-13).

Samantha Kolton (CAA), 2026-11-06, thread 272ed6b2a4, in the place
profiles are built:

    "What type of credit card does Paige Spiranac's have?"
    -> "Brief drafted: Paige Spiranac (new build, $300)"
    -> approved, built, emailed. The question was never answered.

Held here:

  TheCause          the router read "credit card" as a question about
                    credits; the keeping of a question that led to a
                    build lost its only writer on 2026-10-08
  TheQuestion       her words now reach the read lanes, on the build
                    surface too; the ops words keep their lane; an order
                    is still an order
  TheBuildChip      the priced offer keeps its words and its two chips
                    and gains "Build a profile on <subject>", only where
                    profiles are built
  TheFollowThrough  a build ordered after a quoted question carries the
                    question on its brief; starting the build keeps it;
                    the pop hands it back once
  AtAnOpenStep      a can-you question typed at a brief's open question
                    is answered and the brief stays open; "not building
                    a profile here" sets it aside (David Carter, Sony)
  ThroughTheDoor    her words through service.ask with the widget
                    sitting in its build flow: never the build flow

Hermetic: the stub host of test_pm_new_chat_no_bleed. No model, no S3.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
import unittest.mock

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location('pm_nb_for_question_first', os.path.join(HERE, 'test_pm_new_chat_no_bleed.py'))
NB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NB)

from prometheus import service as S  # noqa: E402
from prometheus import understand as U  # noqa: E402
from prometheus import question_first as QF  # noqa: E402
from prometheus import pending_answers as PA  # noqa: E402
from prometheus import priced_read as PR  # noqa: E402

APP, World = NB.APP, NB.World

HER = "What type of credit card does Paige Spiranac's have?"
CHIP = 'Build a profile on Paige Spiranac'
OFFER = {'success': True, 'action': 'answer',
         'reply': 'I can pull that read on Paige Spiranac. ' + PR.COST_LINE.format(price='$100'),
         'followups': [PR.APPROVE_CHIP, PR.CANCEL_CHIP], 'offer_deck': False, 'deck_angle': None, 'subject': 'Paige Spiranac',
         'panel_offer': {'question': HER, 'subject': 'Paige Spiranac', 'kind': 'report', 'frame': '', 'price_label': '$100'}}
OPEN_OFFER = {'id': 'o1', 'thread_id': 't1', 'status': 'open', 'question': HER, 'subject': 'Paige Spiranac', 'kind': 'report'}


def _read(name):
    with open(os.path.join(ROOT, *name.split('/')), encoding='utf-8') as fh:
        return fh.read()


def _ask(w, text, tid=None, surface='interpret', history=None):
    w.calls.clear()
    body = {'text': text, 'client': 'dashboard', 'surface': surface, '_client_surface_sent': surface}
    if tid:
        body['thread_id'] = tid
    if history is not None:
        body['history'] = list(history)
    with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
        return S.ask(dict(NB.ARMINE), body)


# ============================================================================ 1
class TheCause(unittest.TestCase):
    def test_credit_card_is_not_a_question_about_credits(self):
        for q in (HER, 'what credit cards do Paige Spiranac fans carry?', 'Which credit union do Costco members use?',
                  'do Chime users have a higher credit score?', 'What credit providers lead with golf fans?'):
            self.assertFalse(U._OPS_WORDS_RX.search(q), q)
            for has_ctx in (False, True):
                d = U.decide(q, has_ctx=has_ctx, open_tabs=[])
                self.assertEqual(d['surface'], 'analyze', (q, d))

    def test_the_ops_words_keep_their_lane(self):
        for q in ('how many credits do I have?', 'what does a credit cost', 'how many credits is a profile?',
                  'what is the incidence for Nike buyers?', 'what is the status of my run?', 'refresh the Nike profile',
                  'what is the sample size for a Nike profile?'):
            self.assertTrue(U._OPS_WORDS_RX.search(q), q)

    def test_the_keeping_had_no_writer_and_now_has_one(self):
        chat, watch, jobs = _read('prometheus/legacy/chat.py'), _read('prometheus/legacy/watch.py'), _read('prometheus/legacy/jobs.py')
        self.assertIn('def _pm_stash_pending_question(', watch)                      # the writer was always there
        self.assertIn('_C._pm_pop_pending_question(', jobs)                           # and so was the hand-back
        self.assertIn('def _pm_pop_pending_question(', watch)
        self.assertIn('_pm_stash_pending_question, _pm_pop_pending_question,', chat)   # chat still carries the name jobs calls
        core = chat[chat.index('def _pm_approve_core('):]
        core = core[:core.index('\ndef ', 10)]
        call = '_question_first.keep(_pm_stash_pending_question,'
        self.assertIn(call, core, 'the launch keeps the question')
        self.assertLess(core.index('_session_run_id = queue_resp.get'), core.index(call))   # only once the build is started
        self.assertIn('_pm_is_probe_user(_approve_username)', core[core.index(call) - 200:core.index(call)])   # a probe keeps nothing


# ============================================================================ 2
class TheQuestion(unittest.TestCase):
    def test_what_is_a_question_and_what_is_an_order(self):
        for q in (HER, 'what credit cards do Paige Spiranac fans carry?', 'who watches Landman',
                  'what are top forms of IP being search and engaged today?', 'Is the Nike audience mostly men?'):
            self.assertTrue(QF.is_question(q), q)
        for q in ('Run a profile for jordan chiles', CHIP, 'Can you build me a profile for Call Her Daddy?',
                  'Paige Spiranac', 'can you cut the Nike profile by gender?', 'build the profile instead', ''):
            self.assertFalse(QF.is_question(q), q)


# ============================================================================ 3
class TheBuildChip(unittest.TestCase):
    def test_the_offer_gains_the_build_and_keeps_everything_else(self):
        out = QF.with_build_chip(dict(OFFER), HER, 'interpret', held=lambda n: False)
        self.assertEqual(out['followups'], [PR.APPROVE_CHIP, PR.CANCEL_CHIP, CHIP])
        self.assertEqual(out['reply'], OFFER['reply'])                                # the house's own wording, the same price
        self.assertEqual(out['panel_offer'], OFFER['panel_offer'])
        self.assertEqual(OFFER['followups'], [PR.APPROVE_CHIP, PR.CANCEL_CHIP])       # the input is not touched
        from prometheus import orders as ORD
        self.assertTrue(ORD.is_profile_order(CHIP))                                   # the chip is an order the build flow takes
        # it survives the shape the build flow's pane renders
        shaped = S._interpret_shape(out)
        self.assertEqual(shaped['followups'], [PR.APPROVE_CHIP, PR.CANCEL_CHIP, CHIP])
        self.assertTrue(shaped.get('panel_offer'))

    def test_the_audience_word_comes_off_the_name(self):
        o = dict(OFFER, panel_offer=dict(OFFER['panel_offer'], subject='Los Angeles Rams Fans'), subject='Los Angeles Rams Fans')
        self.assertEqual(QF.with_build_chip(o, 'what IP are LA rams fans into?', 'interpret', held=lambda n: False)['followups'][-1],
                         'Build a profile on Los Angeles Rams')

    def test_where_it_does_not_appear(self):
        no = lambda n: False  # noqa: E731
        self.assertEqual(QF.with_build_chip(dict(OFFER), HER, 'analyze', held=no)['followups'], OFFER['followups'])       # asked anywhere else
        self.assertEqual(QF.with_build_chip(dict(OFFER), HER, '', held=no)['followups'], OFFER['followups'])
        self.assertEqual(QF.with_build_chip(dict(OFFER), HER, 'interpret', held=lambda n: True)['followups'], OFFER['followups'])   # already on file
        for kind in ('sizing', 'derived', 'viewership'):
            o = dict(OFFER, panel_offer=dict(OFFER['panel_offer'], kind=kind))
            self.assertEqual(QF.with_build_chip(o, HER, 'interpret', held=no)['followups'], OFFER['followups'], kind)
        multi = dict(OFFER, panel_offer=dict(OFFER['panel_offer'], parts=[{'subject': 'Paige'}, {'subject': 'Visa'}]))
        self.assertEqual(QF.with_build_chip(multi, HER, 'interpret', held=no)['followups'], OFFER['followups'])
        everyone = dict(OFFER, panel_offer=dict(OFFER['panel_offer'], subject=PR.GEN_POP_LABEL))
        self.assertEqual(QF.with_build_chip(everyone, HER, 'interpret', held=no)['followups'], OFFER['followups'])
        answer = {'success': True, 'action': 'answer', 'reply': 'Visa leads.', 'followups': ['x']}
        self.assertEqual(QF.with_build_chip(dict(answer), HER, 'interpret', held=no), answer)                             # no quote, no chip
        self.assertEqual(QF.with_build_chip(dict(OFFER), CHIP, 'interpret', held=no)['followups'], OFFER['followups'])    # an order is not a question

    def test_kill_switch(self):
        old = os.environ.get('PM_QUESTION_FIRST')
        os.environ['PM_QUESTION_FIRST'] = '0'
        try:
            self.assertEqual(QF.with_build_chip(dict(OFFER), HER, 'interpret', held=lambda n: False)['followups'], OFFER['followups'])
            self.assertIsNone(QF.origin_for_build(CHIP, [], OPEN_OFFER))
            self.assertIsNone(QF.at_open_step('not building a profile here', 'ip_scope', 'X'))
        finally:
            if old is None:
                os.environ.pop('PM_QUESTION_FIRST', None)
            else:
                os.environ['PM_QUESTION_FIRST'] = old


# ============================================================================ 4
class TheFollowThrough(unittest.TestCase):
    def test_a_build_ordered_after_the_quote_is_started_from_her_question(self):
        for words in (CHIP, 'build the profile instead', 'Run a profile on Paige Spiranac'):
            o = QF.origin_for_build(words, [], OPEN_OFFER)
            self.assertEqual(o, {'question': HER, 'subject': 'Paige Spiranac'}, words)
        self.assertIsNone(QF.origin_for_build(CHIP, [], None))                         # no quote open: an order is just an order
        self.assertIsNone(QF.origin_for_build(CHIP, [], dict(OPEN_OFFER, question='Run a profile on Nike')))
        self.assertIsNone(QF.origin_for_build(CHIP, [], dict(OPEN_OFFER, parts=[{'subject': 'a'}, {'subject': 'b'}])))

    def test_the_question_rides_the_brief_on_the_same_subject_only(self):
        origin = {'question': HER, 'subject': 'Paige Spiranac'}
        for key in ('spec_draft', 'draft'):
            raw = {'success': True, key: {'subject': 'Paige Spiranac', 'decision': 'new_build'}}
            self.assertTrue(QF.attach_origin(raw, origin, 't1'))
            self.assertEqual(raw[key]['origin_question'], {'question': HER, 'thread_id': 't1'})
        other = {'success': True, 'spec_draft': {'subject': 'Jordan Chiles', 'decision': 'new_build'}}
        self.assertFalse(QF.attach_origin(other, origin, 't1'))
        self.assertNotIn('origin_question', other['spec_draft'])
        self.assertFalse(QF.attach_origin({'success': False, 'guidance': True, 'error': 'x'}, origin, 't1'))
        self.assertFalse(QF.attach_origin({'success': True, 'spec_draft': {'subject': 'Paige Spiranac'}}, None, 't1'))
        # a brief that already carries its question keeps it
        raw = {'success': True, 'spec_draft': {'subject': 'Paige Spiranac', 'origin_question': {'question': 'first?', 'thread_id': 't0'}}}
        self.assertFalse(QF.attach_origin(raw, origin, 't1'))
        self.assertEqual(raw['spec_draft']['origin_question']['question'], 'first?')

    def test_starting_the_build_keeps_the_question(self):
        draft = {'subject': 'Paige Spiranac', 'origin_question': {'question': HER, 'thread_id': 't1'}}
        self.assertEqual(QF.origin_for_launch(draft), {'question': HER, 'thread_id': 't1'})
        self.assertIsNone(QF.origin_for_launch({'subject': 'Paige Spiranac'}))                     # an ordinary build keeps nothing
        self.assertIsNone(QF.origin_for_launch({'subject': 'X', 'origin_question': {'question': 'Run a profile on X'}}))   # never an order
        # a build that starts in the turn it was ordered (a covered seat): the request's note, on this subject
        note = {'question': HER, 'subject': 'Paige Spiranac', 'thread_id': 't1'}
        self.assertEqual(QF.origin_for_launch({'subject': 'Paige Spiranac'}, note, 'Paige Spiranac'), {'question': HER, 'thread_id': 't1'})
        self.assertIsNone(QF.origin_for_launch({'subject': 'Jordan Chiles'}, note, 'Jordan Chiles'))

    def test_the_kept_question_is_handed_back_once_when_her_profile_lands(self):
        """The store the launch writes is the one the existing follow-through reads."""
        asked = 1_000_000.0
        stash = {'skolton': [{'subject': 'Paige Spiranac', 'question': HER, 'ts': asked, 'thread_id': '272ed6b2a4'}]}
        jobs = [{'display_name': 'Paige Spiranac', 'created_at': asked + 1200},
                {'display_name': 'Paige Spiranac - Avid Fan', 'created_at': asked + 1300},
                {'display_name': 'Jordan Chiles', 'created_at': asked + 1200}]
        due = PA.due_entries(stash, jobs, now=asked + 1200 + PA.GRACE_S + 5)
        self.assertEqual([(u, e['question'], j['display_name']) for u, e, j in due], [('skolton', HER, 'Paige Spiranac')])
        self.assertEqual(PA.due_entries(stash, jobs[2:], now=asked + 5000), [])       # someone else's build is not hers
        # one shot
        doc = {'skolton': list(stash['skolton'])}

        def cas(bucket, key, mut, default=dict, log_name=''):
            new = mut(doc.copy())
            doc.clear()
            doc.update(new)
        self.assertEqual(PA.pop_entry(cas, 'b', 'skolton', HER)['thread_id'], '272ed6b2a4')
        self.assertIsNone(PA.pop_entry(cas, 'b', 'skolton', HER))

    def test_the_wiring(self):
        svc = _read('prometheus/service.py')
        self.assertIn('_qf.origin_for_build(', svc)
        self.assertIn('_qf.attach_origin(', svc)
        self.assertIn('_qf.with_build_chip(', svc)
        chat = _read('prometheus/legacy/chat.py')
        self.assertIn('_question_first.take_origin(draft)', chat)
        self.assertLess(chat.index('_question_first.take_origin(draft)'), chat.index('spec = _spec_from_draft(draft)', chat.index('_question_first.take_origin(draft)')))
        d = {'subject': 'Paige Spiranac', 'origin_question': {'question': HER, 'thread_id': 't1'}}
        self.assertEqual(QF.take_origin(d), {'question': HER, 'thread_id': 't1'})
        self.assertNotIn('origin_question', d)                                         # never rides to the engine
        kept = []
        self.assertTrue(QF.keep(lambda u, s, q, thread_id=None: kept.append((u, s, q, thread_id)), 'u1', 'Paige Spiranac', {'question': HER, 'thread_id': 't1'}))
        self.assertEqual(kept, [('u1', 'Paige Spiranac', HER, 't1')])
        self.assertFalse(QF.keep(lambda *a, **k: kept.append(a), 'u1', 'Paige Spiranac', None))


# ============================================================================ 5
class AtAnOpenStep(unittest.TestCase):
    RANKERS = ('new question: do the Apple Podcasts and Spotify Podcasts rankers in Crosswalk combine audio and video '
               'versions into one figure?')

    def test_his_question_is_answered_and_the_brief_stays_open(self):
        hit = QF.at_open_step(self.RANKERS, 'ip_scope', 'Dark Matter: The Official Podcast')
        self.assertTrue(hit and hit.get('keep'))
        self.assertTrue(hit['message'].startswith('Yes, each board is one figure per show.'))
        self.assertIn('Your Dark Matter: The Official Podcast brief is still open.', hit['message'])
        self.assertIn('say cancel to set it aside', hit['message'])
        self.assertNotIn('Two ways to build this', hit['message'])
        self.assertNotIn('Profile IQ profile builds', hit['message'])

    def test_not_building_sets_the_brief_aside(self):
        for words in ('not building a profile here', "I'm not building a profile", 'no, not building anything',
                      "I don't want a profile", 'no profile'):
            hit = QF.at_open_step(words, 'ip_scope', 'Dark Matter: The Official Podcast')
            self.assertTrue(hit and hit.get('discard'), words)
            self.assertIn('set aside and nothing is queued', hit['message'])

    def test_an_answer_to_the_step_is_still_an_answer(self):
        for words in ('broad', 'listeners', 'listeners only', 'all seasons', 'season 2', 'yes', 'national', 'trailing 6 months',
                      'LA and San Diego', 'none', 'ok', 'a separate profile for each'):
            self.assertIsNone(QF.at_open_step(words, 'ip_scope', 'X'), words)

    def test_the_clarify_route_asks_first(self):
        chat = _read('prometheus/legacy/chat.py')
        route = chat[chat.index("step = str(body.get('step') or '').strip().lower()"):]
        head = route[:route.index("if step == 'identity':")]
        self.assertIn('_question_first.at_open_step(answer, step', head)
        self.assertIn("'next_step': '' if _side.get('discard') else step", head)
        self.assertIn("'discard': bool(_side.get('discard'))", head)


# ============================================================================ 6
class ThroughTheDoor(unittest.TestCase):
    def test_her_words_where_profiles_are_built_never_reach_the_build_flow(self):
        w = World()
        tid = w.new_thread()
        env, st = _ask(w, HER, tid=tid, surface='interpret')
        self.assertEqual(st, 200)
        self.assertFalse([c for c in w.calls if c[0] == 'interpret'], w.calls)       # no brief was drafted
        self.assertTrue([c for c in w.calls if c[0] == 'analyze'], w.calls)          # the read lanes took it
        self.assertFalse(env.get('spec_draft'))

    def test_question_then_quote_then_build_the_brief_carries_her_question(self):
        """The whole walk on one thread: her question is quoted with the build one tap away; she takes the build;
        the brief the build flow drafts carries the question; a build on something else carries nothing."""
        from prometheus.host import host
        w = World()
        tid = w.new_thread()

        def _analyze(user, body, text, history):
            w.calls.append(('analyze', text, dict(body), {}))
            return dict(OFFER, panel_offer=dict(OFFER['panel_offer']), followups=list(OFFER['followups']))

        def _interpret(user, body, text, history):
            w.calls.append(('interpret', text, dict(body), {}))
            subj = 'Jordan Chiles' if 'jordan' in text.lower() else 'Paige Spiranac'
            return {'success': True, 'spec_draft': {'subject': subj, 'decision': 'new_build'}, 'estimated_credits': 5}

        host.bind(analyze_core=_analyze, interpret_core=_interpret)
        with unittest.mock.patch.object(QF, '_held', lambda name: False):
            env, _st = _ask(w, HER, tid=tid, surface='interpret')
        text = env.get('text') or ''
        self.assertIn('I can pull that read on Paige Spiranac.', text)
        self.assertIn(PR.COST_LINE.format(price='$100'), text)
        chips = [str(c.get('send') or c.get('label') or c) if isinstance(c, dict) else str(c)
                 for c in (env.get('followups') or env.get('options') or (env.get('raw') or {}).get('followups') or [])]
        self.assertEqual(chips[-1], CHIP, env)
        self.assertEqual(chips[:2], [PR.APPROVE_CHIP, PR.CANCEL_CHIP])
        self.assertFalse([c for c in w.calls if c[0] == 'interpret'])
        hist = [{'role': 'user', 'text': HER},
                {'role': 'agent', 'text': text, 'meta': {'panel_offer': dict(OFFER['panel_offer']),
                                                         'options': [{'label': c, 'send': c} for c in chips]}}]
        # she takes the build
        env2, _st = _ask(w, CHIP, tid=tid, surface='interpret', history=hist)
        self.assertTrue([c for c in w.calls if c[0] == 'interpret'])
        raw2 = env2.get('raw') if isinstance(env2.get('raw'), dict) else env2
        draft = raw2.get('spec_draft') or env2.get('spec_draft') or {}
        self.assertEqual(draft.get('subject'), 'Paige Spiranac', env2)
        self.assertEqual(draft.get('origin_question'), {'question': HER, 'thread_id': tid})
        self.assertEqual(QF.origin_for_launch(draft), {'question': HER, 'thread_id': tid})   # what starting the build keeps
        # an order for someone else on the same thread is not started from her question
        env3, _st = _ask(w, 'Run a profile for jordan chiles', tid=tid, surface='interpret', history=hist)
        raw3 = env3.get('raw') if isinstance(env3.get('raw'), dict) else env3
        self.assertNotIn('origin_question', (raw3.get('spec_draft') or env3.get('spec_draft') or {}))
        # an order with no question before it carries nothing
        w2 = World()
        host.bind(analyze_core=_analyze, interpret_core=_interpret)
        tid2 = w2.new_thread()
        env4, _st = _ask(w2, CHIP, tid=tid2, surface='interpret')
        raw4 = env4.get('raw') if isinstance(env4.get('raw'), dict) else env4
        self.assertNotIn('origin_question', (raw4.get('spec_draft') or env4.get('spec_draft') or {}))

    def test_an_order_there_is_still_the_build_flow(self):
        w = World()
        tid = w.new_thread()
        _env, _st = _ask(w, 'Run a profile for jordan chiles', tid=tid, surface='interpret')
        self.assertTrue([c for c in w.calls if c[0] == 'interpret'])
        w = World()
        tid = w.new_thread()
        _env, _st = _ask(w, CHIP, tid=tid, surface='interpret')
        self.assertTrue([c for c in w.calls if c[0] == 'interpret'])

    def test_a_can_you_question_there_is_answered_not_priced_and_not_built(self):
        w = World()
        tid = w.new_thread()
        env, _st = _ask(w, 'Can you differentiate between audio podcasts and video podcasts?', tid=tid, surface='interpret')
        self.assertTrue((env.get('text') or '').startswith('Yes. '))
        self.assertEqual(w.calls, [])
        self.assertFalse(env.get('offer') or env.get('panel_offer') or env.get('spec_draft'))


if __name__ == '__main__':
    unittest.main(verbosity=1)
