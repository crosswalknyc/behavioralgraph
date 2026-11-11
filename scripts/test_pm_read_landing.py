#!/usr/bin/env python3
"""A read that finishes with the tab closed is in the thread when the
user reopens it, once (2026-11-05). Hermetic: no S3, no Flask app, no
model.

Found that day: a read launched from an operator session (a persisting
session: the server writes the ack turn "On it: reading X now" into the
thread with meta.read_job_id) carried no thread_id on its job document,
and _pm_append_read_to_history read "a turn carries this job id" as
"already landed", so the finished read sat in the job payload and never
reached the thread.

Covered here:
  1. an ack turn carrying the job id alone: the server lands the payload
     into it (text, chips, CSV link, chart, figures, the landed mark;
     the ack keeps its turn_id and moment)
  2. an ack turn already landed (the mark, or the payload text under
     the job id): no duplicate, nothing written
  3. a job launched with no thread on the request: the job document
     carries a thread_id after _pm_job_bind_thread (the request's
     stashed thread, else the active thread, else the first)
  4. two landings race: one copy (the ETag re-merge and the pure merge
     both collapse two readings of one job to one turn; a stale copy of
     the ack never writes over the landed payload; the widget's own
     copy and the server's fold into one)
  5. the end-to-end landing step (prometheus.legacy.land) against a fake
     chat core: fills the ack, idempotent on the second call, appends
     when the thread holds no ack (the dashboard flow)
  6. the recovery fault line and the drilldown landing use the same
     contract; the worker lands the thread before the job reads done;
     the widget refreshes the server's copy before writing its own
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from prometheus import read_landing as RL  # noqa: E402
from prometheus import thread_merge as tm  # noqa: E402

JOB = 'a1b2c3d4e5f6'
ACK_TEXT = 'On it: reading P-Valley Viewers and S.W.A.T. Exiles overlap now. The read will land right here when it is ready.'
REPLY = ('On P-Valley Viewers and S.W.A.T. Exiles overlap:\n\n611,291 of the 4,812,337 P-Valley viewers (12.7%) also '
         'watched S.W.A.T. Exiles since its premiere.\n\nHow these are counted:\n- Window: Nov 5 2025 to Nov 5 2026 '
         '(S.W.A.T. Exiles Season 1 to date, Sep 25 to Oct 29 2026).\n- Base: P-Valley viewers.')
PAYLOAD = {'success': True, 'action': 'answer', 'reply': REPLY,
           'followups': ['Split the overlap by service', 'Show the weekly curve'],
           'file_link': {'url': 'https://dashboard-inputs.s3.amazonaws.com/x/overlap.csv', 'label': 'Download CSV',
                         'key': 'x/overlap.csv', 'name': 'overlap.csv'},
           'chart': {'url': 'https://dashboard-inputs.s3.amazonaws.com/x/overlap.png', 'alt': 'Overlap by week'},
           'profile': 'P-Valley Viewers and S.W.A.T. Exiles overlap',
           'figures': [{'value': 611291, 'kind': 'people', 'label': 'Also watched S.W.A.T. Exiles', 'source': 'read'}]}


def T(ts, role, text, **meta):
    t = {'role': role, 'text': text, 'ts': ts}
    if meta:
        t['meta'] = meta
    return t


USER_Q = T('2026-11-05T18:02:11.100Z', 'user', 'How many P-Valley viewers also watched S.W.A.T. Exiles?')
ACK = T('2026-11-05T18:02:19.400Z', 'agent', ACK_TEXT, kind='job', surface='analyze', read_job_id=JOB)
LATER_Q = T('2026-11-05T18:05:40.000Z', 'user', 'status?')
LATER_A = T('2026-11-05T18:05:44.000Z', 'agent', 'Still reading; it lands right here when it is ready.')


def job_turns(turns, job=JOB):
    return [t for t in turns if (t.get('meta') or {}).get('read_job_id') == job]


# ------------------------------------------------------------ 1 + 2: the pure landing

class AckOnlyLands(unittest.TestCase):
    def test_a_job_id_alone_is_the_ack_not_a_landing(self):
        self.assertEqual(RL.landing_state([USER_Q, ACK], 'read_job_id', JOB, REPLY), ('ack', 1))
        self.assertEqual(RL.landing_state([USER_Q], 'read_job_id', JOB, REPLY), ('none', None))

    def test_the_payload_fills_the_ack_turn(self):
        out, how = RL.land([USER_Q, ACK, LATER_Q, LATER_A], 'read_job_id', JOB, REPLY, RL.read_meta(JOB, PAYLOAD))
        self.assertEqual(how, 'filled')
        self.assertEqual(len(out), 4)                          # no second turn
        landed = job_turns(out)
        self.assertEqual(len(landed), 1)
        t = landed[0]
        self.assertEqual(t['text'], REPLY)
        self.assertEqual(t['ts'], ACK['ts'])                   # the ack's moment: every copy sees one turn change text
        m = t['meta']
        self.assertEqual(m['read_job_id'], JOB)
        self.assertEqual(m['kind'], 'read')
        self.assertTrue(m['landed_at'])
        self.assertEqual([o['send'] for o in m['options']], PAYLOAD['followups'])
        self.assertEqual(m['link']['url'], PAYLOAD['file_link']['url'])
        self.assertEqual(m['chart']['url'], PAYLOAD['chart']['url'])
        self.assertEqual(m['subject'], PAYLOAD['profile'])
        self.assertEqual(m['figures'][0]['value'], 611291)
        self.assertNotIn('surface', m)                         # ack-only keys come off
        self.assertEqual(RL.landing_state(out, 'read_job_id', JOB, REPLY), ('landed', 1))

    def test_the_ack_keeps_its_turn_id(self):
        ack = dict(ACK, turn_id='t1abc')
        out, how = RL.land([USER_Q, ack], 'read_job_id', JOB, REPLY, RL.read_meta(JOB, PAYLOAD))
        self.assertEqual(how, 'filled')
        self.assertEqual(out[1]['turn_id'], 't1abc')

    def test_no_ack_in_the_thread_appends_one_turn(self):
        out, how = RL.land([USER_Q, T('2026-11-05T18:02:19.400Z', 'agent', ACK_TEXT)],   # the widget's own ack: no job id
                           'read_job_id', JOB, REPLY, RL.read_meta(JOB, PAYLOAD), ts='2026-11-05T18:09:00.000Z')
        self.assertEqual(how, 'appended')
        self.assertEqual(len(out), 3)
        self.assertEqual(out[-1]['text'], REPLY)
        self.assertEqual(out[-1]['meta']['read_job_id'], JOB)
        self.assertTrue(out[-1]['meta']['landed_at'])

    def test_an_already_landed_turn_is_left_alone(self):
        once, _ = RL.land([USER_Q, ACK], 'read_job_id', JOB, REPLY, RL.read_meta(JOB, PAYLOAD))
        again, how = RL.land(once, 'read_job_id', JOB, REPLY, RL.read_meta(JOB, PAYLOAD))
        self.assertEqual(how, 'already')
        self.assertEqual(again, once)
        self.assertEqual(len(job_turns(again)), 1)

    def test_the_payload_text_under_the_job_id_counts_as_landed_without_the_mark(self):
        # the widget's own copy (its poll filled the turn before the server did): same text, same job id, no mark
        widget_copy = T('2026-11-05T18:08:59.000Z', 'agent', REPLY, read_job_id=JOB,
                        options=[{'label': f, 'send': f} for f in PAYLOAD['followups']])
        out, how = RL.land([USER_Q, widget_copy], 'read_job_id', JOB, REPLY, RL.read_meta(JOB, PAYLOAD))
        self.assertEqual(how, 'already')
        self.assertEqual(len(job_turns(out)), 1)

    def test_a_faulted_line_under_the_job_id_is_filled_by_the_real_read(self):
        faulted = T('2026-11-05T18:06:00.000Z', 'agent', tm.CALM_LINE, read_job_id=JOB, kind='read', faulted='no_figures')
        out, how = RL.land([USER_Q, faulted], 'read_job_id', JOB, REPLY, RL.read_meta(JOB, PAYLOAD))
        self.assertEqual(how, 'filled')
        self.assertEqual(out[1]['text'], REPLY)
        self.assertEqual(len(job_turns(out)), 1)


# ------------------------------------------------------------ 3: the job document carries a thread

def _legacy_fn(name):
    with open(os.path.join(ROOT, 'prometheus', 'legacy', 'chat.py'), encoding='utf-8') as fh:
        src = fh.read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(name + ' not found in chat.py')


class _G:
    pass


def _bind_thread_ns(req_tid='', g_tid='', index=None, index_raises=False):
    """_pm_job_bind_thread exec'd with its collaborators faked."""
    flask = types.ModuleType('flask')
    g = _G()
    if g_tid:
        g._pm_thread_id = g_tid
    flask.g = g
    flask.has_app_context = lambda: True
    local = types.SimpleNamespace(tid=req_tid)

    def _idx(username):
        if index_raises:
            raise RuntimeError('s3 down')
        return index if index is not None else {'active': '', 'threads': []}
    ns = {'_PM_REQ_THREAD': local, '_PM_JOB_THREAD': {}, '_load_threads_index': _idx, '__builtins__': __builtins__}
    code = _legacy_fn('_pm_job_bind_thread')
    saved = sys.modules.get('flask')
    sys.modules['flask'] = flask
    try:
        exec(code, ns)
        return ns
    finally:
        if saved is not None:
            sys.modules['flask'] = saved
        else:
            sys.modules.pop('flask', None)


class JobDocumentCarriesAThread(unittest.TestCase):
    IDX = {'active': 'act0001', 'threads': [{'id': 'first01'}, {'id': 'act0001'}]}

    def _bind(self, **kw):
        ns = _bind_thread_ns(**kw)
        flask = types.ModuleType('flask')
        g = _G()
        if kw.get('g_tid'):
            g._pm_thread_id = kw['g_tid']
        flask.g = g
        flask.has_app_context = lambda: True
        saved = sys.modules.get('flask')
        sys.modules['flask'] = flask
        try:
            return ns['_pm_job_bind_thread']('job1', 'admin'), ns['_PM_JOB_THREAD']
        finally:
            if saved is not None:
                sys.modules['flask'] = saved
            else:
                sys.modules.pop('flask', None)

    def test_the_request_thread_wins(self):
        tid, pinned = self._bind(req_tid='78905e9c57', g_tid='other', index=self.IDX)
        self.assertEqual(tid, '78905e9c57')
        self.assertEqual(pinned, {'job1': '78905e9c57'})

    def test_an_operator_session_with_no_thread_on_the_body_takes_the_stashed_thread(self):
        # the service stashes the thread it resolved on the request (g._pm_thread_id); the legacy core
        # read only its own thread-local, which the operator path never set
        tid, _ = self._bind(req_tid='', g_tid='78905e9c57', index=self.IDX)
        self.assertEqual(tid, '78905e9c57')

    def test_no_thread_anywhere_on_the_request_takes_the_active_thread(self):
        tid, _ = self._bind(req_tid='', g_tid='', index=self.IDX)
        self.assertEqual(tid, 'act0001')

    def test_an_index_with_no_active_takes_the_first_thread(self):
        tid, _ = self._bind(req_tid='', g_tid='', index={'active': '', 'threads': [{'id': 'first01'}]})
        self.assertEqual(tid, 'first01')

    def test_only_a_user_with_no_threads_at_all_yields_none(self):
        tid, pinned = self._bind(req_tid='', g_tid='', index={'active': '', 'threads': []})
        self.assertIsNone(tid)
        self.assertEqual(pinned, {})
        tid, _ = self._bind(req_tid='', g_tid='', index_raises=True)
        self.assertIsNone(tid)

    def test_the_launch_stamps_the_thread_on_the_job_document(self):
        src = _legacy_fn('_pm_generate_metrics_response')
        # 2026-11-11: a resumed copy (the read worker) keeps the thread the launch recorded
        self.assertIn("_head['thread_id'] = _pm_job_bind_thread(job_id, _pm_user) if not inline_job_id else _pm_job_bind_thread(job_id, _pm_user, resumed=True)", src)
        self.assertLess(src.index("_head['thread_id'] = _pm_job_bind_thread"), src.index('_rr.enqueue(job_id, _head)'))

    def test_the_job_body_keeps_the_launch_thread_on_every_stage_write(self):
        # the shipped defect (job d54a5f96891f, 2026-11-05): the body's fresh head carried no thread_id, so
        # the first stage write replaced the launch document without it, and the worker landed by the
        # user's active thread
        from prometheus import read_recovery as RR
        s3 = FakeS3()
        s3.objs['system/prometheus_reads/job1.json'] = json.dumps({
            'job_id': 'job1', 'user': 'sonytv', 'status': 'working', 'thread_id': '959e2f9e5d', 'via': 'dashboard',
            'subject': 'P-Valley Viewers and S.W.A.T. Exiles overlap', 'resume': {'text': 'q'}, 'resume_attempts': 1}).encode('utf-8')
        saved_s3, saved_prefix = RR._s3, RR._prefix
        RR._s3, RR._prefix = (lambda: (s3, 'b')), (lambda: 'system/prometheus_reads/')
        try:
            head = RR.carry_resume('job1', {'job_id': 'job1', 'user': 'sonytv', 'probe': False, 'question': 'q', 'started_at': 1.0})
        finally:
            RR._s3, RR._prefix = saved_s3, saved_prefix
        self.assertEqual(head['thread_id'], '959e2f9e5d')
        self.assertEqual(head['via'], 'dashboard')
        self.assertEqual(head['subject'], 'P-Valley Viewers and S.W.A.T. Exiles overlap')
        self.assertEqual(head['resume'], {'text': 'q'})
        self.assertEqual(head['resume_attempts'], 1)
        # a resumed copy's own guess never displaces the launch's thread
        RR._s3, RR._prefix = (lambda: (s3, 'b')), (lambda: 'system/prometheus_reads/')
        try:
            head2 = RR.carry_resume('job1', {'job_id': 'job1', 'thread_id': 'act0001'})
        finally:
            RR._s3, RR._prefix = saved_s3, saved_prefix
        self.assertEqual(head2['thread_id'], '959e2f9e5d')


# ------------------------------------------------------------ 4: two landings, one copy

class _S3Error(Exception):
    def __init__(self, code, status):
        super().__init__(code)
        self.response = {'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': status}}


class FakeS3:
    def __init__(self):
        self.objs = {}

    def _etag(self, key):
        return hashlib.md5(self.objs[key]).hexdigest()

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            raise _S3Error('NoSuchKey', 404)
        return {'Body': io.BytesIO(self.objs[Key]), 'ETag': '"%s"' % self._etag(Key)}

    def put_object(self, Bucket, Key, Body, ContentType=None, IfMatch=None, IfNoneMatch=None, **kw):
        if IfMatch is not None and (Key not in self.objs or self._etag(Key) != IfMatch):
            raise _S3Error('PreconditionFailed', 412)
        if IfNoneMatch == '*' and Key in self.objs:
            raise _S3Error('PreconditionFailed', 412)
        self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')


class TwoLandingsOneCopy(unittest.TestCase):
    KEY = 'system/synth_chat_threads/admin/78905e9c57.json'

    def _stored(self, s3):
        return json.loads(s3.objs[self.KEY].decode('utf-8'))

    def test_two_landings_into_the_ack_race_to_one_turn(self):
        s3 = FakeS3()
        s3.objs[self.KEY] = json.dumps([USER_Q, ACK]).encode('utf-8')
        # both landings read the same snapshot (the ack, unfilled) and both write
        a, _ = RL.land([USER_Q, ACK], 'read_job_id', JOB, REPLY, RL.read_meta(JOB, PAYLOAD))
        b, _ = RL.land([USER_Q, ACK], 'read_job_id', JOB, REPLY, RL.read_meta(JOB, PAYLOAD))
        tm.merge_save(s3, 'b', self.KEY, a)
        tm.merge_save(s3, 'b', self.KEY, b)
        on_disk = self._stored(s3)
        self.assertEqual(len(on_disk), 2)
        self.assertEqual(len(job_turns(on_disk)), 1)
        self.assertEqual(job_turns(on_disk)[0]['text'], REPLY)
        self.assertTrue(job_turns(on_disk)[0]['meta']['landed_at'])

    def test_two_appended_landings_race_to_one_turn(self):
        # the dashboard flow: the thread holds the widget's own ack (no job id); two landings append
        s3 = FakeS3()
        base = [USER_Q, T('2026-11-05T18:02:19.400Z', 'agent', ACK_TEXT)]
        s3.objs[self.KEY] = json.dumps(base).encode('utf-8')
        a, _ = RL.land(base, 'read_job_id', JOB, REPLY, RL.read_meta(JOB, PAYLOAD), ts='2026-11-05T18:09:00.000Z')
        b, _ = RL.land(base, 'read_job_id', JOB, REPLY, RL.read_meta(JOB, PAYLOAD), ts='2026-11-05T18:09:00.700Z')
        tm.merge_save(s3, 'b', self.KEY, a)
        tm.merge_save(s3, 'b', self.KEY, b)
        on_disk = self._stored(s3)
        self.assertEqual(len(on_disk), 3)
        self.assertEqual(len(job_turns(on_disk)), 1)

    def test_a_stale_ack_never_writes_over_the_landed_payload(self):
        landed, _ = RL.land([USER_Q, ACK], 'read_job_id', JOB, REPLY, RL.read_meta(JOB, PAYLOAD))
        merged = tm.merge_turns(landed, [USER_Q, ACK])                 # a tab still holding the ack saves
        self.assertEqual(len(job_turns(merged)), 1)
        self.assertEqual(job_turns(merged)[0]['text'], REPLY)
        self.assertTrue(job_turns(merged)[0]['meta']['landed_at'])

    def test_the_landing_over_the_stored_ack_takes_the_payload(self):
        landed, _ = RL.land([USER_Q, ACK], 'read_job_id', JOB, REPLY, RL.read_meta(JOB, PAYLOAD))
        merged = tm.merge_turns([USER_Q, ACK, LATER_Q], landed)
        self.assertEqual(len(job_turns(merged)), 1)
        self.assertEqual(job_turns(merged)[0]['text'], REPLY)
        self.assertEqual([t['role'] for t in merged], ['user', 'agent', 'user'])   # in place, in order

    def test_the_widgets_own_copy_and_the_servers_fold_into_one(self):
        # the widget wrote its copy first (its own moment, its own id); the server's landing came after
        widget_copy = dict(T('2026-11-05T18:08:59.000Z', 'agent', REPLY, read_job_id=JOB,
                             options=[{'label': f, 'send': f} for f in PAYLOAD['followups']]), turn_id='tW1')
        server_copy = T('2026-11-05T18:09:00.300Z', 'agent', REPLY, **RL.read_meta(JOB, PAYLOAD))
        merged = tm.merge_turns([USER_Q, widget_copy], [server_copy])
        self.assertEqual(len(merged), 2)
        self.assertEqual(len(job_turns(merged)), 1)
        self.assertTrue(job_turns(merged)[0]['meta']['landed_at'])
        self.assertEqual(job_turns(merged)[0]['meta']['link']['url'], PAYLOAD['file_link']['url'])
        # and the other order
        merged2 = tm.merge_turns([USER_Q, server_copy], [widget_copy])
        self.assertEqual(len(job_turns(merged2)), 1)
        self.assertEqual(job_turns(merged2)[0]['text'], REPLY)

    def test_an_unmarked_payload_over_the_ack_still_wins(self):
        # an older record: payload under the job id without the mark against the persisted ack
        copy = T('2026-11-05T18:08:59.000Z', 'agent', REPLY, read_job_id=JOB, kind='read')
        merged = tm.merge_turns([USER_Q, ACK], [copy])
        self.assertEqual(len(job_turns(merged)), 1)
        self.assertEqual(job_turns(merged)[0]['text'], REPLY)
        merged2 = tm.merge_turns([USER_Q, copy], [ACK])
        self.assertEqual(job_turns(merged2)[0]['text'], REPLY)

    def test_two_different_jobs_stay_two_turns(self):
        other = T('2026-11-05T18:02:19.400Z', 'agent', ACK_TEXT, read_job_id='zz9999')
        merged = tm.merge_turns([USER_Q, ACK], [other])
        self.assertEqual(len([t for t in merged if (t.get('meta') or {}).get('read_job_id')]), 2)

    def test_the_merge_suite_shape_still_holds(self):
        # plain turns, corrections and the calm fill are untouched by the job rule
        a = T('2026-11-05T18:00:00.000Z', 'agent', 'x')
        b = T('2026-11-05T18:00:00.000Z', 'agent', 'y', corrected_at='2026-11-05T18:00:05Z')
        self.assertEqual(tm.merge_turns([a], [b])[0]['text'], 'y')


# ------------------------------------------------------------ 5: the landing step end to end

class FakeCore:
    """The chat helpers prometheus.legacy.land reaches through _C."""

    def __init__(self, history, tid='78905e9c57'):
        self.store = {tid: list(history)}
        self.tid = tid
        self.saves = []

    def _pm_thread_for_job(self, username, job_id):
        return self.tid

    def _pm_load_thread_or_active(self, username, tid):
        return list(self.store.get(tid) or [])

    def _pm_save_thread_or_active(self, username, tid, history):
        self.saves.append(tid)
        self.store[tid] = tm.merge_turns(self.store.get(tid) or [], history)
        return True

    def _pm_history_has_job_turn(self, history, meta_key, job_id):
        return any((t.get('meta') or {}).get(meta_key) == job_id for t in history)

    def _pm_iso_now(self):
        return '2026-11-05T18:09:00.000Z'


class LandingStep(unittest.TestCase):
    def setUp(self):
        from prometheus import legacy as L
        self.L = L
        self.prev_c = object.__getattribute__(L.C, '_mod')
        self.prev_h = object.__getattribute__(L.H, '_mod')
        L.bind(types.SimpleNamespace())

    def tearDown(self):
        self.L.C._bind(self.prev_c)
        self.L.H._bind(self.prev_h)

    def test_the_operator_session_ack_is_filled_once(self):
        core = FakeCore([USER_Q, ACK, LATER_Q, LATER_A])
        self.L.bind_core(core)
        from prometheus.legacy import land as LAND
        how = LAND._pm_append_read_to_history('admin', JOB, PAYLOAD, started_at=None)
        self.assertEqual(how, 'filled')
        th = core.store['78905e9c57']
        self.assertEqual(len(th), 4)
        self.assertEqual(len(job_turns(th)), 1)
        self.assertEqual(job_turns(th)[0]['text'], REPLY)
        self.assertEqual(job_turns(th)[0]['meta']['link']['url'], PAYLOAD['file_link']['url'])
        self.assertEqual([o['send'] for o in job_turns(th)[0]['meta']['options']], PAYLOAD['followups'])
        # the thread reopened: the read is there with the CSV link and the chips, no 'On it' left for this job
        self.assertNotIn(ACK_TEXT, [t['text'] for t in th])
        # a second landing (the worker and the sweep, or a retry) writes nothing
        how2 = LAND._pm_append_read_to_history('admin', JOB, PAYLOAD, started_at=None)
        self.assertEqual(how2, 'already')
        self.assertEqual(core.saves, ['78905e9c57'])
        self.assertEqual(len(job_turns(core.store['78905e9c57'])), 1)

    def test_the_dashboard_flow_appends_the_read_once(self):
        core = FakeCore([USER_Q, T('2026-11-05T18:02:19.400Z', 'agent', ACK_TEXT)])   # the widget's ack carries no job id
        self.L.bind_core(core)
        from prometheus.legacy import land as LAND
        self.assertEqual(LAND._pm_append_read_to_history('admin', JOB, PAYLOAD), 'appended')
        self.assertEqual(LAND._pm_append_read_to_history('admin', JOB, PAYLOAD), 'already')
        th = core.store['78905e9c57']
        self.assertEqual(len(th), 3)
        self.assertEqual(th[-1]['text'], REPLY)
        self.assertTrue(th[-1]['meta']['landed_at'])

    def test_an_empty_reply_lands_nothing(self):
        core = FakeCore([USER_Q, ACK])
        self.L.bind_core(core)
        from prometheus.legacy import land as LAND
        self.assertIsNone(LAND._pm_append_read_to_history('admin', JOB, {'reply': ''}))
        self.assertEqual(core.saves, [])


# ------------------------------------------------------------ 6: the other writers and the widget

class SameContractEverywhere(unittest.TestCase):
    def _src(self, rel):
        with open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
            return fh.read()

    def test_the_recovery_fault_line_and_the_drilldown_land_through_the_helper(self):
        rr = self._src('prometheus/read_recovery.py')
        self.assertIn("_rl.land(history, 'read_job_id', job_id, line", rr)
        self.assertNotIn("if not c._pm_history_has_job_turn(history, 'read_job_id', job_id):\n                history.append", rr)
        dd = self._src('prometheus/drilldown.py')
        self.assertIn("_rl.land(history, 'read_job_id', job_id, reply", dd)

    def test_the_worker_lands_the_thread_before_the_job_reads_done(self):
        src = _legacy_fn('_pm_run_read_job')
        land_at = src.index("_pm_append_read_to_history(pm_user, job_id, payload, held=held")
        done_at = src.index("_pm_read_status_write(job_id, _done)")
        self.assertLess(land_at, done_at)

    def test_the_widget_refreshes_the_servers_copy_before_writing_its_own(self):
        html = self._src('templates/index.html')
        self.assertIn('async function _pmRefreshLandedRead(jobId, tries)', html)
        self.assertIn('function _pmTurnIsLanded(t)', html)
        self.assertIn('function _pmTurnJobKey(t)', html)
        # 2026-11-11: the poll carries the thread the read was asked in (_po) and its lines land there
        poll = html[html.index('async function _pmPollReadJob(jobId, originTid)'):html.index('async function _pmRefreshLandedRead')]
        self.assertIn("await _pmRefreshLandedRead(jobId, 2)", poll)
        self.assertLess(poll.index('await _pmRefreshLandedRead(jobId, 2)'), poll.index("_pmPushMetaFor(_po, 'agent', p.reply, _rjMeta)"))
        self.assertNotIn("_synthChatPushTurnWithMeta('agent', p.reply, _rjMeta)", poll)
        # the ack this tab holds under the job id is filled, never doubled
        self.assertIn('_ack.text = _synthChatAgentText(\'agent\', p.reply);', poll)
        # the pull takes the server's landed text into the turn the tab holds
        self.assertIn("(sm.landed_at && !lm.landed_at && _pmTurnTextKey(s) !== _pmTurnTextKey(local))", html)
        # two turns under one job id are one turn to the tab
        self.assertIn("if (ja && ja === _pmTurnJobKey(b)) return true;", html)


if __name__ == '__main__':
    unittest.main(verbosity=2)
