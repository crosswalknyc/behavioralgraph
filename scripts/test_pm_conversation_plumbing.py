#!/usr/bin/env python3
"""Conversation plumbing, the day audit of 2026-11-10 (what was left
after prometheus.thread_scope made every ask, job and reply per thread).

  1. A job launched through the key surface lands in a thread of the key
     surface, never in a dashboard chat. An ask that named no
     conversation gets a thread of its own (pinned, via: api); a
     follow-up that passes it inherits its subject; one without starts
     clean.                                         -> KeyAsksHaveTheirOwnThread
  2. The server posts the finished line into the thread that launched
     the build, with no browser open, once.          -> TheServerPostsTheFinishedLine
  3. What is said about an order follows the artifact: no calm "in
     progress" line for an order that never started. -> AnOrderIsNotRunningUntilItIs
  4. The word door never appears in deck copy or a deck's file name;
     compound brand names are untouched. A chat reply is not deck copy
     (2026-11-11) and passes as written.             -> NoDoor
  5. A banked read answers a new question only when subject, cohort and
     window match; the same question still replays.  -> TheLedgerAnswersOnlyTheSameRead

Hermetic: the stub host and in-memory store of test_pm_new_chat_no_bleed,
the legacy binder exec'd from source. No model, no S3, no app module.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import flask  # noqa: E402

_spec = importlib.util.spec_from_file_location('pm_no_bleed_scaffold', os.path.join(HERE, 'test_pm_new_chat_no_bleed.py'))
NB = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(NB)

from prometheus import api_threads as AT  # noqa: E402
from prometheus import build_ready as BR  # noqa: E402
from prometheus import door_words as DW  # noqa: E402
from prometheus import order_truth as OT  # noqa: E402
from prometheus import service as S  # noqa: E402
from prometheus import thread_presence as TP  # noqa: E402
from prometheus import thread_scope as TS  # noqa: E402
from prometheus import envelope as ENV  # noqa: E402
from prometheus.host import host  # noqa: E402

APP, World, USER, T_MAD, T_NEW, THREADS = NB.APP, NB.World, NB.USER, NB.T_MAD, NB.T_NEW, NB.THREADS
LIZ_Q = ('For the ten premium streaming services sold both through Amazon Prime Video Channels and on the services\' own sites '
         'and apps: how many new US subscriptions started in the window?')
LIZ_FOLLOW = 'Day 90 retention of Channels starts versus direct starts, by service'
LIZ_SUBJECT = 'Amazon Prime Video Channels vs direct billing, ten streaming services'
THE_PAPER = 'Pull Subscriber IQ for The Paper (series, season 1) on Peacock'
CALM = 'Working on it. Confirmed your task is in progress and I will email you when it completes.'
KEITH = ("On Target - Claire's buyers: Claire's buyers are a three-door audience: 99.4% of them bought at one. Retail door mix, "
         "Oct 31 2025 to Oct 31 2026 (share of Claire's buyers' confirmed orders across the three named doors) - Target: 47.3% "
         "share. 795,088 people. Near universal, the profile's home door.")


def _legacy_fn(name):
    return NB._legacy_fn(name)


# ============================================================================ 1
class KeyAsksHaveTheirOwnThread(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def _binder(self, *, key=True, req_tid='', g_tid=None, question=LIZ_Q, job='1f648cc05c41'):
        local = types.SimpleNamespace(tid=req_tid)
        ns = {'_PM_REQ_THREAD': local, '_PM_JOB_THREAD': {},
              '_load_threads_index': lambda u: self.w.index(),
              '_pm_read_job_doc': lambda jid: None, '__builtins__': __builtins__}
        exec(_legacy_fn('_pm_job_bind_thread'), ns)
        with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
            if key:
                flask.g._pm_api_key_owner = USER
                flask.g._pm_api_key_tag = '8b09f13b'
            if g_tid is not None:
                flask.g._pm_thread_id = g_tid
            tid = ns['_pm_job_bind_thread'](job, USER, question=question)
            return tid, AT.minted_conversation(), getattr(flask.g, '_pm_thread_id', None)

    def test_a_threadless_key_job_gets_a_key_thread_never_the_active_one(self):
        self.assertEqual(self.w.index()['active'], T_MAD)            # the dashboard is sitting on the Mad Men chat
        tid, conv, g_tid = self._binder()
        self.assertEqual(tid, 'api-8b09f13b-job-1f648cc05c41')
        self.assertNotEqual(tid, T_MAD)
        self.assertEqual(conv, 'job-1f648cc05c41')                   # the reply names it, so a follow-up can continue there
        self.assertEqual(g_tid, tid)
        idx = self.w.index()
        self.assertEqual(idx['active'], T_MAD)                       # the dashboard's active thread did not move
        ent = next(t for t in idx['threads'] if t['id'] == tid)
        self.assertEqual((ent['via'], ent['api'], ent['pinned'], ent['api_auto']), ('api', True, True, True))
        turns = self.w.get(f'{THREADS}/{USER}/{tid}.json')
        self.assertEqual([t['role'] for t in turns], ['user'])
        self.assertEqual(turns[0]['text'], ' '.join(LIZ_Q.split()))
        self.assertEqual(self.w.get(f'{THREADS}/{USER}/{T_MAD}.json'), NB.MAD_THREAD)   # nothing written into her chat

    def test_a_key_ask_on_a_conversation_keeps_that_conversations_thread(self):
        with APP.test_request_context('/'):
            S.ensure_thread(USER, 'api-8b09f13b-amazon-vs-direct-read', title='API: amazon-vs-direct-read')
        tid, conv, _ = self._binder(g_tid='api-8b09f13b-amazon-vs-direct-read')
        self.assertEqual(tid, 'api-8b09f13b-amazon-vs-direct-read')
        self.assertEqual(conv, '')

    def test_a_dashboard_ask_is_unchanged(self):
        tid, conv, _ = self._binder(key=False)
        self.assertEqual(tid, T_MAD)                                 # a web request that named none (the legacy rule)
        self.assertEqual(conv, '')

    def test_the_landing_never_falls_to_the_active_thread_for_a_key_job(self):
        ensured, relisted = [], []
        ens = lambda u, t, **k: ensured.append((t, k.get('via'), k.get('auto')))     # noqa: E731
        # a key thread the index lost is listed again under its own id
        got = AT.landing_thread(USER, 'j1', 'api-8b09f13b-job-j1', exists=lambda t: False, ensure=ens)
        self.assertEqual(got, 'api-8b09f13b-job-j1')
        self.assertEqual(ensured, [('api-8b09f13b-job-j1', 'api', True)])
        # a job the launch marked via: api with no thread at all gets its own
        got = AT.landing_thread(USER, 'j2', '', exists=lambda t: False, ensure=ens, job_doc={'via': 'api'})
        self.assertEqual(got, 'api-key-job-j2')
        # a dashboard job with no thread keeps the caller's rule; one whose thread was lost goes back to it
        self.assertIsNone(AT.landing_thread(USER, 'j3', '', exists=lambda t: False, ensure=ens, job_doc={'via': 'dashboard'}))
        got = AT.landing_thread(USER, 'j4', 'lost123', exists=lambda t: False, ensure=ens, relist=lambda u, t: relisted.append(t))
        self.assertEqual((got, relisted), ('lost123', ['lost123']))
        self.assertEqual(AT.landing_thread(USER, 'j5', T_MAD, exists=lambda t: True, ensure=ens), T_MAD)
        src = _legacy_fn('_pm_thread_for_job')
        self.assertIn('landing_thread(', src)
        self.assertIn('relist=', src)

    def test_the_rail_and_the_sweep(self):
        with APP.test_request_context('/'):
            S.ensure_thread(USER, 'api-8b09f13b-job-aaa', title='API: x', auto=True)
            S.ensure_thread(USER, 'api-8b09f13b-named', title='API: named')
            S.save_thread(USER, 'api-8b09f13b-job-aaa', [{'role': 'user', 'text': 'q', 'ts': NB._iso()}])
            S.save_thread(USER, 'api-8b09f13b-named', [{'role': 'user', 'text': 'q', 'ts': NB._iso()}])
            ids = [t['id'] for t in S.list_threads(USER)['threads']]
        self.assertIn('api-8b09f13b-named', ids)
        self.assertNotIn('api-8b09f13b-job-aaa', ids)                # a thread minted for one key ask is not in the rail
        # an empty key thread waiting on its job is not swept as an abandoned pane
        idx = {'active': T_MAD, 'threads': [
            {'id': T_MAD, 'turns': 3, 'updated': NB._iso(-100)},
            {'id': 'api-k-job-1', 'turns': 0, 'created': NB._iso(-3600), 'updated': NB._iso(-3600), 'api': True, 'via': 'api', 'pinned': True},
            {'id': 'emptypane', 'turns': 0, 'created': NB._iso(-3600), 'updated': NB._iso(-3600)}]}
        out = TP.sweep_empty_threads(idx, load=lambda k: [], put=lambda k, o: None, delete=lambda k: None, key_for=lambda t: t)
        self.assertEqual(out['removed'], ['emptypane'])
        self.assertIn('api-k-job-1', [t['id'] for t in idx['threads']])

    def test_a_followup_on_a_key_conversation_inherits_the_threads_subject(self):
        w = self.w
        conv = 'api-8b09f13b-amazon-vs-direct-read'
        hist = [{'role': 'user', 'text': LIZ_Q, 'ts': NB._iso(-600)},
                {'role': 'agent', 'text': f"On {LIZ_SUBJECT}: Across the ten services, subscribers billed through Amazon cancel at 8.7% a month.",
                 'ts': NB._iso(-500), 'meta': {'kind': 'read', 'read_job_id': '7d28b5df1e6f', 'subject': LIZ_SUBJECT, 'landed_at': NB._iso(-500)}}]
        with APP.test_request_context('/'):
            S.ensure_thread(USER, conv, title='API: amazon-vs-direct-read')
            S.save_thread(USER, conv, hist)
        self.assertEqual(AT.followup_subject(hist), LIZ_SUBJECT)
        seen = []

        def _analyze_core(user, body, text, history):
            seen.append(body.get('bind_subject'))
            if not body.get('bind_subject'):
                return {'success': True, 'action': 'build_profile', 'route_hint': 'interpret', 'reply': '',
                        'followups': [], 'offer_deck': False, 'deck_angle': None}      # the build-flow handoff a key cannot follow
            return {'success': True, 'action': 'answer', 'reply': f"On {body['bind_subject']}: day 90 retention is 51.9% against 58.7%.",
                    'followups': []}
        host.bind(analyze_core=_analyze_core)
        env, st, _ = w.ask(LIZ_FOLLOW, thread_id=conv, client='api', surface='analyze')
        self.assertEqual(seen, [None, LIZ_SUBJECT])                  # asked once bare, once on the thread's subject
        self.assertIn('day 90 retention is 51.9%', env['text'])
        self.assertNotIn('Which subject should this be on', env['text'])
        # the same words with no conversation start clean: no thread, no subject to inherit
        seen.clear()
        env, st, g = w.ask(LIZ_FOLLOW, client='api', history=[], surface='analyze')
        self.assertEqual(seen, [None])
        self.assertIsNone(g['thread_id'])
        # the dashboard keeps its handoff (its widget follows it)
        seen.clear()
        w.ask(LIZ_FOLLOW, thread_id=T_MAD, history=NB.MAD_THREAD, surface='analyze')
        self.assertEqual(seen, [None])

    def test_an_order_or_a_real_answer_is_never_rewritten(self):
        ran = []
        run = lambda b: ran.append(b) or {'success': True, 'action': 'answer', 'reply': 'x'}       # noqa: E731
        hist = [{'role': 'agent', 'text': 'On Mad Men: 7,582,850.', 'meta': {'kind': 'read', 'read_job_id': 'j', 'subject': 'Mad Men', 'landed_at': 'x'}}]
        ok = {'success': True, 'action': 'answer', 'reply': 'On Mad Men: 6,418,273 so far.'}
        self.assertIs(AT.retry_on_thread_subject(ok, hist, {}, run, text='and 2026?'), ok)
        handoff = {'success': True, 'action': 'build_profile', 'route_hint': 'interpret', 'reply': ''}
        self.assertIs(AT.retry_on_thread_subject(handoff, hist, {}, run, text='run a profile on Mad Men viewers'), handoff)
        self.assertIs(AT.retry_on_thread_subject(handoff, [], {}, run, text='and by service?'), handoff)      # nothing to inherit
        self.assertEqual(ran, [])
        self.assertEqual(AT.retry_on_thread_subject(handoff, hist, {}, run, text='and by service?')['inherited_subject'], 'Mad Men')

    def test_the_wiring(self):
        pa = Path(ROOT, 'prometheus', 'partner_api.py').read_text(encoding='utf-8')
        self.assertIn('_apit.minted_conversation()', pa)
        self.assertIn('_pm_api_key_tag', pa)
        gen = _legacy_fn('_pm_generate_metrics_response')
        self.assertIn('_pm_job_bind_thread(job_id, _pm_user) if not inline_job_id else', gen)   # the question rides the request
        svc = Path(ROOT, 'prometheus', 'service.py').read_text(encoding='utf-8')
        self.assertIn('_apit.retry_on_thread_subject(', svc)


# ============================================================================ 2
class TheServerPostsTheFinishedLine(unittest.TestCase):
    RUN = 'run_20261110_2329_walter'
    NAME = 'My Life with the Walter Boys Viewers on Netflix'

    def setUp(self):
        self.w = World()
        self.w.new_thread('3df066a21c')                 # the Sony seat's thread (the reader has since opened another chat)
        idx = self.w.index()
        idx['active'] = T_MAD
        self.w.put(f'{THREADS}/{USER}/index.json', idx)
        self.store = {}
        BR.note_launch(self.store.__setitem__, run_id=self.RUN, user=USER, thread_id='3df066a21c', subject=self.NAME,
                       decision='new_build', engine_user='armine@example.com', now=1000.0)
        self.runs = [{'run_id': self.RUN, 'status': 'running', 'subject': self.NAME}]

    def _sweep(self, now=2000.0):
        with APP.test_request_context('/'):
            return BR.sweep(store_list=lambda p: [k for k in self.store if k.startswith(p)], store_get=self.store.get,
                            store_put=self.store.__setitem__, runs_for=lambda u: list(self.runs) if u == 'armine@example.com' else [],
                            exists=S.thread_exists, load=S.load_thread, save=S.save_thread, ensure=S.ensure_thread,
                            relist=S.relist_thread, now=now, log=None)

    def _thread(self, tid='3df066a21c'):
        return self.w.get(f'{THREADS}/{USER}/{tid}.json')

    def test_nothing_is_said_before_the_run_is_finished(self):
        self.assertEqual(self._sweep(), [])
        self.assertEqual(self._thread(), [])
        self.assertEqual(self.store[BR._key(self.RUN)]['status'], 'open')

    def test_the_line_lands_in_the_launching_thread_with_no_browser(self):
        self.runs[0].update(status='complete', tu_key='My_Life_with_the_Walter_Boys_Viewers_on_Netflix_11_10_2026_23_29.csv')
        self.assertEqual(self._sweep(), [(self.RUN, 'posted')])
        turns = self._thread()
        self.assertEqual(len(turns), 1)
        t = turns[0]
        self.assertEqual(t['role'], 'agent')
        self.assertEqual(t['text'], f"{self.NAME} is ready. It is in the Select Profile dropdown under {self.NAME}.")
        self.assertEqual(t['meta']['build_run_id'], self.RUN)
        self.assertEqual(t['meta']['options'], [{'label': f"Open {self.NAME}", 'send': f"Open {self.NAME}"}])
        self.assertTrue(t['meta']['server'])
        self.assertEqual(self.w.get(f'{THREADS}/{USER}/{T_MAD}.json'), NB.MAD_THREAD)       # not the chat she has open now
        self.assertEqual(self.w.index()['active'], T_MAD)
        # idempotent: a second pass, a second process, a re-opened launch
        self.assertEqual(self._sweep(now=3000.0), [])
        self.store[BR._key(self.RUN)]['status'] = 'open'
        self.assertEqual(self._sweep(now=4000.0), [(self.RUN, 'already')])
        self.assertEqual(len(self._thread()), 1)
        for bad in ('synth', 'pipeline', 'queue', '\u2014', '\u2013'):
            self.assertNotIn(bad, t['text'].lower())

    def test_a_tab_that_already_said_it_is_not_repeated(self):
        with APP.test_request_context('/'):
            S.save_thread(USER, '3df066a21c', [{'role': 'agent', 'ts': NB._iso(),
                                                'text': f"{self.NAME} is finished and ready in the Select Profile dropdown."}])
        self.runs[0]['status'] = 'complete'
        self.assertEqual(self._sweep(), [(self.RUN, 'already')])
        self.assertEqual(len(self._thread()), 1)
        js = Path(ROOT, 'templates', 'index.html').read_text(encoding='utf-8')
        self.assertIn('t.meta.build_run_id === runId', js)                      # and the tab does not repeat the server

    def test_a_lost_thread_is_listed_again_never_swapped_for_the_active_one(self):
        idx = self.w.index()
        idx['threads'] = [t for t in idx['threads'] if t['id'] != '3df066a21c']
        self.w.put(f'{THREADS}/{USER}/index.json', idx)
        self.runs[0]['status'] = 'complete'
        self.assertEqual(self._sweep(), [(self.RUN, 'posted')])
        self.assertEqual(len(self._thread()), 1)
        self.assertEqual(self.w.get(f'{THREADS}/{USER}/{T_MAD}.json'), NB.MAD_THREAD)
        self.assertIn('3df066a21c', [t['id'] for t in self.w.index()['threads']])

    def test_no_thread_no_line_and_a_failed_run_says_what_happened(self):
        """A launch with no thread posts nothing. A run that failed has its outcome put in the launching thread, in
        the client's words (2026-11-11, prometheus.promise_truth: before this a failed run said nothing here and the
        tab drew the calm line); the calm promise is never written."""
        self.store.clear()
        BR.note_launch(self.store.__setitem__, run_id='r2', user=USER, thread_id='', subject='X', engine_user='armine@example.com', now=1000.0)
        BR.note_launch(self.store.__setitem__, run_id='r3', user=USER, thread_id='3df066a21c', subject='Y', engine_user='armine@example.com', now=1000.0)
        self.runs = [{'run_id': 'r2', 'status': 'complete', 'subject': 'X'}, {'run_id': 'r3', 'status': 'failed', 'subject': 'Y'}]
        self.assertEqual(sorted(self._sweep()), [('r2', 'no_thread'), ('r3', 'closed_failed_appended')])
        said = self._thread()
        self.assertEqual([t['text'] for t in said], ['This build could not be completed. Credits for this run have been refunded.'])
        self.assertEqual(said[0]['meta']['build_outcome']['run_id'], 'r3')
        self.assertNotIn('Working on it', said[0]['text'])
        self.assertEqual(self.w.get(f'{THREADS}/{USER}/{T_MAD}.json'), NB.MAD_THREAD)
        # an old launch the engine no longer lists is closed, not kept forever
        BR.note_launch(self.store.__setitem__, run_id='r4', user=USER, thread_id='3df066a21c', subject='Z', now=1000.0)
        self.assertEqual(self._sweep(now=1000.0 + BR.MAX_AGE_S + 5), [('r4', 'expired')])

    def test_the_wiring(self):
        rr = Path(ROOT, 'prometheus', 'read_recovery.py').read_text(encoding='utf-8')
        self.assertIn('_br.sweep_with_host(_host, _svc, now=now)', rr[rr.index('def sweep('):rr.index('def start_background(')])
        appr = _legacy_fn('_pm_approve_core')
        self.assertIn('_build_ready.note_launch(', appr)
        self.assertLess(appr.index("_session_run_id = queue_resp.get('run_id')"), appr.index('_build_ready.note_launch('))


# ============================================================================ 3
class _Resp:
    def __init__(self, code, doc=None, text=''):
        self.status_code, self._doc, self.text = code, doc, text

    def json(self):
        if self._doc is None:
            raise ValueError('no json')
        return self._doc


class AnOrderIsNotRunningUntilItIs(unittest.TestCase):
    def test_the_engine_reply_decides_what_is_said(self):
        doc, state, _ = OT.queue_post(lambda: _Resp(200, {'run_id': 'r1', 'pending_position': 2}), sleep=lambda s: None)
        self.assertEqual((doc['run_id'], state), ('r1', 'started'))
        # refused twice: not started, and it was tried again once
        calls, retried = [], []
        doc, state, detail = OT.queue_post(lambda: calls.append(1) or _Resp(503, text='busy'), sleep=lambda s: None, on_retry=retried.append)
        self.assertEqual((doc, state, len(calls), len(retried)), (None, 'not_started', 2, 1))
        # refused, then taken on the second try
        seq = [_Resp(502, text='bad gateway'), _Resp(200, {'run_id': 'r2'})]
        doc, state, _ = OT.queue_post(lambda: seq.pop(0), sleep=lambda s: None)
        self.assertEqual((doc['run_id'], state), ('r2', 'started'))
        # unreachable twice
        def _refuse():
            raise ConnectionError('connection refused')
        self.assertEqual(OT.queue_post(_refuse, sleep=lambda s: None)[1], 'not_started')
        # a timeout may have landed: never retried, never called running
        calls.clear()

        def _timeout():
            calls.append(1)
            raise TimeoutError('read timed out')
        self.assertEqual((OT.queue_post(_timeout, sleep=lambda s: None)[1], len(calls)), ('unconfirmed', 1))
        # accepted with no run id: nothing to point at
        self.assertEqual(OT.queue_post(lambda: _Resp(200, {'ok': True}), sleep=lambda s: None)[1], 'unconfirmed')
        # a 4xx is the engine saying no: not retried
        calls.clear()
        self.assertEqual((OT.queue_post(lambda: calls.append(1) or _Resp(400, text='bad spec'), sleep=lambda s: None)[1], len(calls)),
                         ('not_started', 1))

    def test_the_replies_are_plain_and_never_the_calm_promise(self):
        for p in (OT.not_started_payload('The Paper'), OT.unconfirmed_payload('The Paper')):
            self.assertFalse(p['success'])
            self.assertTrue(p['guidance'])                       # the words reach the reader as written
            self.assertNotIn('in progress', p['error'])
            self.assertNotIn('Working on it', p['error'])
            self.assertFalse(OT.is_calm(p, CALM))
            for bad in ('queue', 'engine', 'synth', 'worker', '\u2014', '\u2013'):
                self.assertNotIn(bad, p['error'].lower())
        self.assertIn('did not start', OT.NOT_STARTED)
        self.assertIn('Nothing is running', OT.NOT_STARTED)
        self.assertIn('could not confirm', OT.UNCONFIRMED)
        self.assertEqual(OT.RETRY_LINE, 'That did not start. Trying again.')

    def test_admins_order_the_paper(self):
        """21:58 UTC: the draft step failed, nothing was queued, the reply said the task was confirmed in progress."""
        calm = {'success': False, 'error': CALM}
        raw, changed = OT.settle(dict(calm), THE_PAPER, calm_message=CALM, retry=lambda: dict(calm))
        self.assertTrue(changed)
        self.assertEqual(raw['error'], OT.NOT_STARTED)
        self.assertEqual(raw['order_state'], 'not_started')
        # the second try can start it: then the reply is that draft, not an apology
        good = {'success': True, 'spec_draft': {'subject': 'The Paper'}}
        self.assertEqual(OT.settle(dict(calm), THE_PAPER, calm_message=CALM, retry=lambda: dict(good)), (good, True))
        # a question keeps the calm promise (the team is told and the answer follows by email)
        self.assertEqual(OT.settle(dict(calm), 'How old are viewers of The Paper?', calm_message=CALM, retry=lambda: dict(calm)),
                         (calm, False))
        # the claim follows the artifact: a run, a job or a draft is something to point at
        self.assertTrue(OT.has_artifact({'run_id': 'r1'}))
        self.assertTrue(OT.has_artifact({'job': {'id': 'j1', 'type': 'read'}}))
        self.assertTrue(OT.has_artifact({'spec_draft': {'subject': 'x'}}))
        self.assertFalse(OT.has_artifact({'success': False, 'error': CALM}))
        self.assertFalse(OT.is_calm({'success': False, 'error': CALM, 'run_id': 'r1'}, CALM))

    def test_through_the_ask_service(self):
        w = World()
        n = []

        def _interpret_core(user, body, text, history):
            n.append(text)
            raise RuntimeError('draft step failed')
        host.bind(interpret_core=_interpret_core, calm_payload=lambda: {'success': False, 'error': CALM})
        tid = w.new_thread()
        env, st, _ = w.ask(THE_PAPER, thread_id=tid, history=[], surface='interpret')
        self.assertEqual(len(n), 2)                                  # tried again, once
        self.assertIn('did not start', env['text'])
        self.assertNotIn('in progress', env['text'])
        self.assertIn({'route': 'order_truth', 'outcome': 'not_started'}, w.hints)

    def test_the_approve_route_never_claims_a_run_it_does_not_have(self):
        src = _legacy_fn('_pm_approve_core')
        tail = src[src.index('_order_truth.queue_post('):]
        self.assertNotIn('_chatbot_calm_payload()', tail)             # no calm line once the order is on its way to the engine
        self.assertIn('_order_truth.not_started_payload(', tail)
        self.assertIn('_order_truth.unconfirmed_payload(', tail)
        self.assertLess(tail.index('queue_resp is None'), tail.index("'run_id': _session_run_id"))


# ============================================================================ 4
class NoDoor(unittest.TestCase):
    def test_keiths_read(self):
        out = DW.scrub_read(KEITH)
        self.assertFalse(DW.has_door(out))
        self.assertIn('a three-store audience', out)
        self.assertIn('Retail store mix', out)
        self.assertIn('the three named stores', out)
        self.assertIn("the profile's home store", out)
        self.assertIn('47.3% share. 795,088 people.', out)            # nothing else moved

    def test_metaphors_and_names(self):
        self.assertEqual(DW.scrub_read('This opens the door to new viewers.'), 'This brings new viewers.')
        self.assertEqual(DW.scrub_read('The show is a door into the franchise.'), 'The show is a way into the franchise.')
        for keep in ('DoorDash orders and Glassdoor reviews', 'Nextdoor users in Austin', 'They played at The Door NYC last night',
                     'Mad Men viewers skew older.'):
            self.assertEqual(DW.scrub_read(keep), keep)
        self.assertFalse(DW.has_door('DoorDash, Glassdoor and Nextdoor'))

    def test_file_names(self):
        self.assertEqual(DW.scrub_filename('Claires_buyers_Retail_door_mix.csv'), 'Claires_buyers_Retail_store_mix.csv')
        self.assertEqual(DW.scrub_filename('Three-door audience.csv'), 'Three-store audience.csv')
        self.assertEqual(DW.scrub_filename('DoorDash_Users_Insights.pptx'), 'DoorDash_Users_Insights.pptx')
        self.assertEqual(DW.scrub_filename('Mad_Men_Insights.pptx'), 'Mad_Men_Insights.pptx')

    def test_a_chat_reply_is_not_deck_copy_and_deck_copy_still_is(self):
        """2026-11-11: the door rule is a deck rule. A chat read, its chips and a data file's name pass as written;
        a reply that IS deck copy (an outline typed into chat) and a deck's own file name are still scrubbed."""
        import prometheus_analysis as pma
        self.assertEqual(pma.scrub_user_text('Retail door mix across the three named doors'),
                         'Retail door mix across the three named doors')
        env = ENV.wrap({'success': True, 'action': 'answer', 'reply': KEITH, 'followups': ['Show the door mix by region'],
                        'file_link': {'url': 'https://x/y/Retail%20door%20mix.csv', 'name': 'Retail door mix.csv',
                                      'label': 'Download Retail door mix.csv'}},
                       surface='analyze', decision={})
        self.assertIn('a three-door audience', env['text'])
        raw = {'reply': 'the home door', 'followups': ['Show the door mix'], 'filename': 'Retail_door_mix.csv',
               'file_link': {'name': 'Retail door mix.csv', 'label': 'Download Retail door mix.csv', 'url': 'https://x/door.csv'}}
        before = {k: (dict(v) if isinstance(v, dict) else (list(v) if isinstance(v, list) else v)) for k, v in raw.items()}
        DW.scrub_payload(raw)
        self.assertEqual(raw, before)
        deck = {'reply': 'SLIDE 1\nthe home door', 'followups': ['Show the door mix'], 'filename': 'Retail_door_mix.pptx'}
        DW.scrub_payload(deck)
        self.assertEqual((deck['reply'], deck['followups'], deck['filename']),
                         ('SLIDE 1\nthe home store', ['Show the store mix'], 'Retail_store_mix.pptx'))
        chat = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')
        self.assertEqual(chat.count('_door_words.scrub_filename('), 1)             # the deck's own file name, nothing else

    def test_the_house_helper_is_the_one_scrubber(self):
        """The frames, the token and the allowlist come from the design pack's cw_door_copy; this module adds the
        retail sense and nothing else."""
        src = Path(ROOT, 'prometheus', 'door_words.py').read_text(encoding='utf-8')
        self.assertIn('door_copy_module()', src)
        self.assertIn('ds.scrub_door_metaphor(out)', src)
        from migration import design_skill as ds
        self.assertEqual(ds.scrub_door_metaphor(DW.scrub_read(KEITH)), DW.scrub_read(KEITH))   # nothing left for the house scrub


# ============================================================================ 5
class TheLedgerAnswersOnlyTheSameRead(unittest.TestCase):
    ARMINE_Q = 'which categories do best for digital original content on YouTube, TikTok'
    ONE_SUBJECT_Q = 'which categories do best for digital original content on YouTube'

    def setUp(self):
        import insights_ledger as il
        self.il = il
        self.entry = dict(NB.YT_ENTRY)
        self.doc = {'subjects': {'youtube': {'subject': 'youtube', 'entries': [self.entry]}}}
        self._saved = il._load_index
        il._load_index = lambda force=False: self.doc

    def tearDown(self):
        self.il._load_index = self._saved

    def test_another_readers_cohort_read_is_context_not_the_answer(self):
        self.assertEqual(self.entry['cohort'], 'Parents of teens 13-17, YouTube users with no TikTok use')
        for q in (self.ARMINE_Q, self.ONE_SUBJECT_Q, 'what do YouTube viewers watch most by category'):
            r = self.il.consult(subject='YouTube', question=q)
            self.assertIsNone(r.get('exact'), q)
            self.assertIsNone(r.get('match'), q)
            self.assertTrue(r.get('entries'), q)                     # at most context: the figures still ride the block
            self.assertTrue(r.get('block'), q)

    def test_the_same_question_returns_the_same_figures(self):
        r = self.il.consult(subject=None, question=NB.YT_BANKED_Q)
        self.assertEqual(r.get('match'), 'exact')
        self.assertEqual((r.get('exact') or {}).get('reply'), self.entry['reply'])

    def test_what_fits(self):
        fits = self.il.replay_fits
        e = self.entry
        # subject right, cohort wrong
        self.assertFalse(fits(e, self.ONE_SUBJECT_Q, subject='YouTube')[0])
        # cohort named, window wrong (the read is calendar 2025)
        q_cohort = 'what content categories do parents of teens 13-17 who use YouTube with no TikTok use watch'
        self.assertEqual((e['ws'], e['we']), ('2025-01-01', '2025-12-31'))
        ok, why = fits(e, q_cohort + ' from Jan 1 2026 to Jun 30 2026', subject='YouTube')
        self.assertFalse(ok)
        self.assertIn('2026-01-01', why)
        ok, why = fits(e, q_cohort, subject='YouTube')               # no window named: the standing window is asked, not calendar 2025
        self.assertFalse(ok)
        self.assertIn('names no window', why)
        # subject, cohort and window all match: it may answer
        self.assertEqual(fits(e, q_cohort + ' in calendar 2025', subject='YouTube'), (True, ''))
        self.assertEqual(fits(e, q_cohort + ', Jan 1 2025 to Dec 31 2025', subject='YouTube'), (True, ''))
        # a read on the whole universe never answers a question about a cohort
        whole = dict(e, cohort='', ws='', we='')
        self.assertFalse(fits(whole, 'what do parents of teens 13-17 watch on YouTube', subject='YouTube')[0])
        self.assertEqual(fits(whole, self.ONE_SUBJECT_Q, subject='YouTube'), (True, ''))
        # a read on the standing window answers a question that names none; one on the retired fiscal pair does not
        import datetime as _dt
        from migration.event_window import default_window
        ws, we = default_window(_dt.date(2026, 11, 1))
        self.assertTrue(fits(dict(whole, ws=ws, we=we, ts='2026-11-01T10:00:00Z'), self.ONE_SUBJECT_Q, subject='YouTube')[0])
        self.assertFalse(fits(dict(whole, ws='2025-07-01', we='2026-06-30', ts='2026-08-20T10:00:00Z'), self.ONE_SUBJECT_Q,
                              subject='YouTube')[0])
        # the decision of record answers by its own rules
        rec = dict(e, record={'by': 'decision_of_record'}) if not self.il.is_record_entry(e) else e
        if self.il.is_record_entry(rec):
            self.assertEqual(fits(rec, self.ONE_SUBJECT_Q, subject='YouTube'), (True, ''))

    def test_a_meaning_match_that_fits_still_replays(self):
        whole = dict(self.entry, cohort='', ws='', we='', k='youtube|audience||',
                     q='What content categories do YouTube users watch most?',
                     qn=self.il.normalize_question('What content categories do YouTube users watch most?'))
        self.doc['subjects']['youtube']['entries'] = [whole]
        r = self.il.consult(subject='YouTube', question='which YouTube content type categories do users watch the most')
        if r.get('exact') is not None:                               # the meaning-level match is the ledger's to make
            self.assertEqual(r['exact'].get('reply'), whole['reply'])
            self.assertEqual(r.get('match'), 'semantic')
        self.assertEqual(self.il.replay_fits(whole, 'which YouTube content type categories do users watch the most', subject='YouTube'),
                         (True, ''))


if __name__ == '__main__':
    unittest.main(verbosity=2)
