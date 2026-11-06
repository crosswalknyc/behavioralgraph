#!/usr/bin/env python3
"""Cancel means gone; a new thread starts clean (Jenna 2026-11-05). Hermetic.

Jenna, verbatim: "if you cancel a wizard or open a new thread it should
drop the wizard not just keep it open."

  * with the valuation wizard's draft armed (per-thread key + the user's
    pending-intake stash) and a journey brief stashed, 'cancel' through
    service.ask drops both, answers with one plain line and no chips, and
    the next message is no longer the wizard's (wants() is False, nothing
    is armed);
  * opening a new thread (the threads/new hook, on_new_thread) clears
    every intake the reader had, whatever thread it was armed on;
  * the first message on a thread no agent has answered in drops too;
  * a probe identity is harmless: nothing is written for it;
  * a cancel word with nothing armed routes on (the lane declines);
  * the cancel vocabulary: the chip, 'cancel that', 'stop the wizard',
    'drop it', 'never mind'; never a question or a sentence.

Fake S3 and a bound host; no network, no model.
"""
from __future__ import annotations

import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import bpiq_wizard as W  # noqa: E402
from prometheus import intake_reset as IR  # noqa: E402
from prometheus import pending_intake as PI  # noqa: E402
from prometheus import service as S  # noqa: E402
from prometheus.host import host  # noqa: E402

USER = 'jenna'
BUCKET = 'b'
THREADS = 'system/synth_chat_threads'


class _S3Error(Exception):
    def __init__(self, code, status):
        super().__init__(code)
        self.response = {'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': status}}


class FakeS3:
    def __init__(self):
        self.objs = {}
        self.deleted = []
        self.puts = 0

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            raise _S3Error('NoSuchKey', 404)
        return {'Body': io.BytesIO(self.objs[Key]), 'ETag': '"x"'}

    def put_object(self, Bucket, Key, Body, ContentType=None, **kw):
        self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')
        self.puts += 1

    def delete_object(self, Bucket, Key):
        self.objs.pop(Key, None)
        self.deleted.append(Key)

    def list_objects_v2(self, Bucket, Prefix='', MaxKeys=1000, ContinuationToken=None):
        keys = sorted(k for k in self.objs if k.startswith(Prefix))
        return {'Contents': [{'Key': k} for k in keys], 'IsTruncated': False}


def _bind(s3, idx):
    def s3_json(key, default=None):
        try:
            return json.loads(s3.get_object(Bucket=BUCKET, Key=key)['Body'].read().decode('utf-8'))
        except Exception:
            return default

    def s3_put_json(key, obj):
        s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(obj).encode('utf-8'))

    def thread_key(u, tid):
        return f"{THREADS}/{u}/{tid}.json"

    def threads_index_key(u):
        return f"{THREADS}/{u}/index.json"

    def load_threads_index(u):
        return s3_json(threads_index_key(u), None) or idx

    host.bind(s3_client=s3, bucket=BUCKET, s3_json=s3_json, s3_put_json=s3_put_json,
              thread_key=thread_key, threads_index_key=threads_index_key,
              load_threads_index=load_threads_index, thread_title_from=lambda h: 'New chat',
              ask_hint=lambda **kw: None, max_threads=40)
    s3_put_json(threads_index_key(USER), idx)
    for t in idx['threads']:
        if thread_key(USER, t['id']) not in s3.objs:
            s3_put_json(thread_key(USER, t['id']), [])


def _idx(active='thr1'):
    return {'active': active, 'threads': [
        {'id': 'thr1', 'title': 'Value a brand partnership', 'created': 'x', 'updated': 'x', 'turns': 2},
        {'id': 'thr2', 'title': 'New chat', 'created': 'x', 'updated': 'x', 'turns': 0}]}


def _arm(s3, thread='thr1'):
    """The wizard's draft where the wizard itself puts it, plus a journey brief."""
    draft = dict(W.empty_draft(), step=W.STEP_DATES, talent='Gunna', campaign='Gunna x Nike')
    W._s3_store(host).save(USER, thread, draft)
    PI.stash(s3, BUCKET, USER, 'digital_journey', {'subject': 'Landman', 'platform': 'Paramount+'}, 'Run the journey')
    return draft


def _uk():
    import prometheus_memory as pmm
    return pmm._user_key(USER)


WIZARD_HISTORY = [
    {'role': 'user', 'text': 'Value a brand partnership', 'ts': '2026-11-05T10:00:00Z'},
    {'role': 'agent', 'text': W.ASK_DATES, 'ts': '2026-11-05T10:00:01Z',
     'meta': {'bpiq_wizard': {'step': W.STEP_DATES, 'talent': 'Gunna'}, 'options': [{'label': 'Cancel'}]}},
]


class DropAll(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        _bind(self.s3, _idx())

    def test_armed_sees_the_draft_and_the_brief(self):
        _arm(self.s3)
        self.assertIn(f"system/pm_pending_intake/{_uk()}/bpiq_wizard_thr1.json", self.s3.objs)
        got = IR.armed(USER, 'thr1')
        self.assertIn('brand_partnership_wizard', got)
        self.assertIn('digital_journey', got)
        self.assertEqual(IR.labels_for(got), ['the valuation', 'the journey'])

    def test_drop_all_clears_every_store(self):
        _arm(self.s3)
        res = IR.drop_all(USER, 'thr1')
        self.assertEqual(set(res['dropped']), {'brand_partnership_wizard', 'digital_journey'})
        self.assertEqual(res['labels'], ['the valuation', 'the journey'])
        self.assertNotIn(f"system/pm_pending_intake/{_uk()}/bpiq_wizard_thr1.json", self.s3.objs)
        self.assertEqual(PI.active_flows(self.s3, BUCKET, USER), [])
        self.assertIsNone(W._s3_store(host).load(USER, 'thr1'))
        self.assertEqual(IR.armed(USER, 'thr1'), [])
        # the wizard's own gate agrees: the next message is not its turn
        self.assertFalse(W.wants('who is Gunna', {}, [{'role': 'user', 'text': 'cancel'},
                                                      {'role': 'agent', 'text': IR.cancel_reply(res)}],
                                 store=W._s3_store(host), user=USER, thread_id='thr1'))

    def test_the_run_marker_is_not_an_intake(self):
        _arm(self.s3)
        W._s3_store(host).save_marker(USER, 'thr1', {'job_id': 'j1', 'run_key': 'k', 'charged': True})
        IR.drop_all(USER, 'thr1')
        self.assertIn(f"system/pm_pending_intake/{_uk()}/bpiq_run_thr1.json", self.s3.objs)

    def test_new_thread_drops_a_wizard_armed_on_another_thread(self):
        _arm(self.s3, thread='thr1')
        res = IR.on_new_thread(USER, 'thr2')
        self.assertIn('brand_partnership_wizard', res['dropped'])
        self.assertEqual([k for k in self.s3.objs if '/bpiq_wizard_' in k], [])
        self.assertEqual(PI.active_flows(self.s3, BUCKET, USER), [])
        self.assertEqual(IR.armed(USER, 'thr1'), [])
        self.assertEqual(IR.armed(USER, 'thr2'), [])

    def test_nothing_armed_is_a_quiet_no_op(self):
        before = self.s3.puts
        res = IR.drop_all(USER, 'thr1')
        self.assertEqual(res['dropped'], [])
        self.assertEqual(self.s3.puts, before)          # no write when nothing was there

    def test_a_probe_identity_is_harmless(self):
        _arm(self.s3)
        res = IR.drop_all('canary:golden', 'thr1')
        self.assertTrue(res.get('probe'))
        self.assertEqual(self.s3.deleted, [])
        # the real reader's draft is untouched by the probe
        self.assertIsNotNone(W._s3_store(host).load(USER, 'thr1'))

    def test_offers_close_on_an_explicit_cancel_only(self):
        from prometheus import pending_offers as PO
        PO.record(USER, 'thr1', {'panel_offer': {'subject': 'Gunna', 'kind': 'read', 'price_label': '$100'}}, 'how big is Gunna')
        PO.record(USER, 'thr2', {'panel_offer': {'subject': 'Nike', 'kind': 'read', 'price_label': '$100'}}, 'how big is Nike')
        _arm(self.s3)
        IR.on_new_thread(USER, 'thr2')                   # a new thread leaves offers alone
        self.assertEqual([o['status'] for o in PO._load(USER)['offers']], ['open', 'open'])
        _arm(self.s3)
        res = IR.drop_all(USER, 'thr1', close_offers=True)
        self.assertEqual(res['offers_closed'], 1)
        self.assertIn('the offer', res['labels'])
        self.assertIn('nothing was charged', IR.cancel_reply(res))
        self.assertEqual([o['status'] for o in PO._load(USER)['offers']], ['cancelled', 'open'])

    def test_fresh_thread_is_one_no_agent_has_answered_in(self):
        self.assertTrue(IR.thread_is_fresh(USER, 'thr2', []))
        host.s3_put_json(host.thread_key(USER, 'thr2'), [{'role': 'user', 'text': 'hi'}])
        self.assertTrue(IR.thread_is_fresh(USER, 'thr2', []))      # the widget saved its own turn first
        host.s3_put_json(host.thread_key(USER, 'thr2'), [{'role': 'user', 'text': 'hi'}, {'role': 'agent', 'text': 'Hello.'}])
        self.assertFalse(IR.thread_is_fresh(USER, 'thr2', []))
        self.assertFalse(IR.thread_is_fresh(USER, 'thr2', [{'role': 'user', 'text': 'hi'}]))


class CancelWords(unittest.TestCase):
    def test_the_words_that_mean_cancel(self):
        for t in ('Cancel', 'cancel', 'cancel that', 'cancel it', 'stop the wizard', 'drop it', 'never mind',
                  'Never mind.', 'nvm', 'not now', 'stop', 'forget it', 'start over', 'cancel the valuation',
                  'drop the wizard', 'quit'):
            self.assertTrue(IR.is_cancel_text(t), t)

    def test_what_is_never_a_cancel(self):
        for t in ('cancel my netflix subscription', 'how do I cancel?', 'no', 'yes', 'Gunna',
                  'cancel the Nike campaign read', 'stop the presses and run nike', 'October 7 - 28',
                  'who cancelled Netflix last year', ''):
            self.assertFalse(IR.is_cancel_text(t), t)

    def test_the_reply_names_what_went(self):
        self.assertEqual(IR.cancel_reply({'labels': ['the valuation']}), 'Dropped the valuation. Ask me anything.')
        self.assertEqual(IR.cancel_reply({'labels': ['the valuation', 'the journey']}),
                         'Dropped the valuation and the journey. Ask me anything.')
        self.assertEqual(IR.cancel_reply({}), 'Dropped it. Ask me anything.')
        p = IR.cancel_payload({'dropped': ['brand_partnership_wizard'], 'labels': ['the valuation']})
        self.assertEqual(p['followups'], [])
        self.assertEqual(p['intake_dropped'], ['brand_partnership_wizard'])
        self.assertFalse(p['bpiq_collect'])
        for s in ('synth', 'model', 'pipeline', '\u2014'):
            self.assertNotIn(s, p['reply'].lower())


class CancelLane(unittest.TestCase):
    """service.ask: the cancel lane runs first and ends the wizard everywhere."""

    def setUp(self):
        self.s3 = FakeS3()
        _bind(self.s3, _idx())
        host.s3_put_json(host.thread_key(USER, 'thr1'), WIZARD_HISTORY)
        self._persist = S._persist_turn
        self.persisted = []
        S._persist_turn = lambda *a, **k: self.persisted.append(a)

    def tearDown(self):
        S._persist_turn = self._persist

    def _ask(self, text, **extra):
        body = dict({'text': text, 'client': 'dashboard', 'surface': 'analyze', 'thread_id': 'thr1',
                     'history': WIZARD_HISTORY}, **extra)
        env, status = S.ask({'username': USER, 'role': 'super_admin'}, body)
        return env, status

    def test_cancel_drops_the_wizard_and_the_brief(self):
        _arm(self.s3)
        env, status = self._ask('Cancel')
        self.assertEqual(status, 200)
        self.assertEqual((env.get('decision') or {}).get('reason'), 'intake_cancel')
        raw = env.get('raw') or {}
        self.assertEqual(raw['reply'], 'Dropped the valuation and the journey. Ask me anything.')
        self.assertEqual(raw['followups'], [])
        self.assertIn('brand_partnership_wizard', raw['intake_dropped'])
        self.assertNotIn('bpiq_wizard', raw)
        self.assertEqual(PI.active_flows(self.s3, BUCKET, USER), [])
        self.assertIsNone(W._s3_store(host).load(USER, 'thr1'))
        self.assertEqual(len(self.persisted), 1)
        # the next message is not the wizard's: its own gate declines and nothing is armed
        hist = WIZARD_HISTORY + [{'role': 'user', 'text': 'Cancel'}, {'role': 'agent', 'text': raw['reply']}]
        self.assertFalse(W.wants('who is Gunna', {'thread_id': 'thr1'}, hist, store=W._s3_store(host), user=USER, thread_id='thr1'))
        self.assertEqual(IR.armed(USER, 'thr1', body={'text': 'who is Gunna'}, history=hist), [])

    def test_the_widgets_cancel_flag_counts_as_armed(self):
        # the widget's Cancel branch sends the flag with the wizard already cleared on its side
        env, status = self._ask('cancel', intake_cancel=True, history=[])
        self.assertEqual((env.get('decision') or {}).get('reason'), 'intake_cancel')
        self.assertEqual((env.get('raw') or {})['reply'], 'Dropped the valuation. Ask me anything.')
        # the other collect steps name their intake
        env, status = self._ask('Cancel', intake_cancel='flywheel', history=[])
        self.assertEqual((env.get('raw') or {})['reply'], 'Dropped the flywheel. Ask me anything.')
        env, status = self._ask('never mind', intake_cancel='digital_journey', history=[])
        self.assertEqual((env.get('raw') or {})['reply'], 'Dropped the journey. Ask me anything.')
        # a decline at a priced confirm step keeps the promise that copy made
        env, status = self._ask('no thanks', intake_cancel='digital_journey', intake_confirm=True, history=[])
        self.assertEqual((env.get('raw') or {})['reply'],
                         'Dropped the journey. Nothing is queued and nothing was charged. Ask me anything.')

    def test_the_interpret_surface_gets_the_guidance_shape(self):
        _arm(self.s3)
        env, status = self._ask('cancel', surface='interpret')
        raw = env.get('raw') or {}
        self.assertTrue(raw.get('guidance'))
        self.assertEqual(raw.get('error'), 'Dropped the valuation and the journey. Ask me anything.')
        self.assertEqual(raw.get('followups'), [])
        self.assertIn('brand_partnership_wizard', raw.get('intake_dropped') or [])

    def test_nothing_armed_routes_on(self):
        # an empty thread, no stores, no body flag: the lane declines and the ask goes on
        host.s3_put_json(host.thread_key(USER, 'thr2'), [{'role': 'user', 'text': 'hi'}, {'role': 'agent', 'text': 'Hello.'}])
        seen = {}
        orig = IR.drop_all

        def spy(*a, **k):
            seen['called'] = True
            return orig(*a, **k)
        IR.drop_all = spy
        try:
            self.assertEqual(IR.armed(USER, 'thr2', body={'text': 'cancel'},
                                      history=[{'role': 'user', 'text': 'hi'}, {'role': 'agent', 'text': 'Hello.'}]), [])
        finally:
            IR.drop_all = orig
        self.assertNotIn('called', seen)

    def test_the_lane_is_first_and_never_priced(self):
        self.assertEqual(S.LANE_ORDER[0], 'intake_cancel')
        self.assertEqual(S.LANE_ORDER[1], 'bpiq_wizard')
        self.assertEqual(list(S.LANE_ORDER), S.lane_markers())
        self.assertIn('intake_cancel', S.NEVER_PRICED_LANES)

    def test_first_message_on_a_fresh_thread_drops(self):
        _arm(self.s3, thread='thr1')
        res = IR.on_fresh_thread(USER, 'thr2', [])
        self.assertIsNotNone(res)
        self.assertIn('brand_partnership_wizard', res['dropped'])
        self.assertEqual(PI.active_flows(self.s3, BUCKET, USER), [])
        # a thread with an answer in it is not fresh: nothing happens
        _arm(self.s3, thread='thr1')
        self.assertIsNone(IR.on_fresh_thread(USER, 'thr1', WIZARD_HISTORY))
        self.assertNotEqual(PI.active_flows(self.s3, BUCKET, USER), [])


class WizardOwnCancel(unittest.TestCase):
    """A cancel that reaches a wizard through its own hook (not the
    intake_cancel lane) still drops the other flows and this thread's
    open offers. The wizard's reply text stays its own."""

    def setUp(self):
        self.s3 = FakeS3()
        _bind(self.s3, _idx())

    def _offers(self):
        from prometheus import pending_offers as PO
        PO.record(USER, 'thr1', {'panel_offer': {'subject': 'Gunna', 'kind': 'read', 'price_label': '$100'}}, 'how big is Gunna')
        PO.record(USER, 'thr2', {'panel_offer': {'subject': 'Nike', 'kind': 'read', 'price_label': '$100'}}, 'how big is Nike')
        return PO

    def test_bpiq_wizard_cancel_drops_the_brief_and_the_offer(self):
        PO = self._offers()
        _arm(self.s3)                                   # the wizard's draft on thr1 + a journey brief
        store = W._s3_store(host)
        out = W.analyze_hook('Cancel', {'thread_id': 'thr1'}, WIZARD_HISTORY, USER, {}, USER,
                             {'_pm_bpiq_intent': W.is_chip}, first=None, store=store)
        self.assertIsNotNone(out)
        self.assertEqual(out['reply'], W.CANCEL_REPLY)          # the wizard's own words, unchanged
        self.assertEqual(out['followups'], [])
        self.assertFalse(out['bpiq_collect'])
        self.assertIsNone(store.load(USER, 'thr1'))
        self.assertNotIn(f"system/pm_pending_intake/{_uk()}/bpiq_wizard_thr1.json", self.s3.objs)
        self.assertEqual(PI.active_flows(self.s3, BUCKET, USER), [])        # the journey brief went too
        self.assertEqual([o['status'] for o in PO._load(USER)['offers']], ['cancelled', 'open'])  # this thread's offer only
        self.assertEqual(IR.armed(USER, 'thr1'), [])

    def test_flywheel_wizard_cancel_drops_the_brief_and_the_offer(self):
        from prometheus import flywheel_wizard as FW
        PO = self._offers()
        store = FW._s3_store(host)
        store.save(USER, 'thr1', dict(FW.empty_draft(), step=FW.STEP_ECOSYSTEM, entry_point='watched Gilmore Girls on Prime Video'))
        PI.stash(self.s3, BUCKET, USER, 'digital_journey', {'subject': 'Landman', 'platform': 'Paramount+'}, 'Run the journey')
        self.assertEqual(set(IR.armed(USER, 'thr1')), {'flywheel_wizard', 'digital_journey'})
        hist = [{'role': 'user', 'text': 'Build a Flywheel'}, {'role': 'agent', 'text': FW.ASK_ECOSYSTEM}]
        out = FW.analyze_hook('Cancel', {'thread_id': 'thr1'}, hist, USER, None, USER, {}, first=False, store=store)
        self.assertIsNotNone(out)
        self.assertEqual(out['reply'], FW.CANCEL_REPLY)         # the wizard's own words, unchanged
        self.assertFalse(out['fw_collect'])
        self.assertIsNone(store.load(USER, 'thr1'))
        self.assertEqual(PI.active_flows(self.s3, BUCKET, USER), [])        # the journey brief went too
        self.assertEqual([o['status'] for o in PO._load(USER)['offers']], ['cancelled', 'open'])  # this thread's offer only
        self.assertEqual(IR.armed(USER, 'thr1'), [])

    def test_a_probe_cancel_through_the_wizard_writes_nothing(self):
        # the golden set and the canary keep their drafts in-process; a real reader's stores stay as they were
        PO = self._offers()
        _arm(self.s3)
        mem = W.MemoryStore()
        mem.save('canary:golden', 'p1', dict(W.empty_draft(), step=W.STEP_DATES, talent='Gunna'))
        out = W.analyze_hook('Cancel', {'thread_id': 'p1', 'persist': False}, [], 'canary:golden', {}, 'canary:golden',
                             {'_pm_bpiq_intent': W.is_chip}, first=None, store=mem)
        self.assertEqual(out['reply'], W.CANCEL_REPLY)
        self.assertIsNone(mem.load('canary:golden', 'p1'))
        self.assertEqual(self.s3.deleted, [])
        self.assertNotEqual(PI.active_flows(self.s3, BUCKET, USER), [])
        self.assertEqual([o['status'] for o in PO._load(USER)['offers']], ['open', 'open'])


class Hooks(unittest.TestCase):
    def test_threads_new_route_and_bind_call_the_reset(self):
        import inspect
        # the legacy module binds the Flask app at import; read it as source
        from pathlib import Path
        src = Path(ROOT, 'prometheus', 'legacy', 'threads.py').read_text(encoding='utf-8')
        i = src.index('def api_synth_chat_threads_new')
        j = src.index('def api_synth_chat_threads_activate')
        self.assertIn('intake_reset', src[i:j])
        self.assertIn('on_new_thread', src[i:j])
        ssrc = inspect.getsource(S.thread_for_subject)
        self.assertIn('on_new_thread', ssrc)

    def test_new_thread_into_never_wipes_a_thread_with_turns(self):
        from pathlib import Path
        src = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')
        i = src.index('def _pm_new_thread_into')
        j = src.index('def _pm_rotate_idle_thread')
        block = src[i:j]
        self.assertIn("_pm_s3_json(_pm_thread_key(username, act['id']), [])", block)
        self.assertEqual(block.count("_pm_s3_put_json(_pm_thread_key(username, act['id']), [])"), 0)


if __name__ == '__main__':
    unittest.main(verbosity=1)
