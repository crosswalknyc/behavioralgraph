#!/usr/bin/env python3
"""A thread never loses a turn (2026-11-04, Jenna: "the data should always
be storing it so that it reconciles and should always keep track").

Jay Patel's thread: a dashboard tab holding a stale copy of the thread
(its own old turns plus the server-marked turns it had pulled) posted
/api/brief-chat/history; keep_server_turns started from the client's
list and folded back only server-marked agent turns, so his own
questions and three plain answers were dropped, twice. The store is now
append-only from the server's side (prometheus.thread_merge): a save
merges, a client adds turns and patches the meta of turns it holds, and
nothing it sends removes a turn. Deleting a thread is its own endpoint.

Hermetic: the merge module is pure; the save and delete handlers are
exec'd out of the legacy source against an in-memory S3 that honors
ETags the way the real one does. REGRESSION_TEST_MODE=1.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import thread_merge as tm  # noqa: E402


def T(ts, role, text, **meta):
    t = {'role': role, 'text': text, 'ts': ts}
    if meta:
        t['meta'] = meta
    return t


def sig(t):
    return (str(t.get('ts')), t.get('role'), tm.text_key(t.get('text')))


def sigs(turns):
    return [sig(t) for t in turns]


# The shape of 2026-11-04 (times real, words not): the stored thread held
# plain user and agent turns, read-job landings and a server note; the
# stale tab held an older plain turn plus the landings it had pulled, and
# two new turns it had just typed.
LANDING_1 = T('2026-11-04T17:41:06.698Z', 'agent', 'On Jason Statham film viewers: 58.2% also stream on Netflix.', read_job_id='rj1')
LANDING_2 = T('2026-11-04T19:09:59.020Z', 'agent', '45,213,554 US people use both HBO Max and Paramount+.', read_job_id='rj2')
NOTE = T('2026-11-04T19:31:01Z', 'agent', 'Here is what each of those figures counted.', server_note=True)
Q1 = T('2026-11-04T18:59:19.788Z', 'user', "what's the HBO and Paramount+ customer overlap in the U.S?")
A1 = T('2026-11-04T18:59:31.611Z', 'agent', 'I can pull that read on HBO Max. Pulling this data will cost $100.')
Q2 = T('2026-11-04T19:07:24.864Z', 'user', 'Approve ($100, not billed to a staff account)')
A2 = T('2026-11-04T19:07:31.424Z', 'agent', 'On it: reading HBO Max now.')
Q3 = T('2026-11-04T20:17:04.900Z', 'user', 'are these numbers U.S only or global?')
A3 = T('2026-11-04T20:17:17.826Z', 'agent', 'Horror is where Paramount+ sits deepest: 41.3% of the US horror audience.')
Q3B = T('2026-11-04T20:17:52.551Z', 'user', 'are these numbers U.S only or global?')          # asked again, a minute later
A3B = T('2026-11-04T20:18:03.671Z', 'agent', 'Horror is where Paramount+ sits deepest: 41.3% of the US horror audience.')
STORED = [LANDING_1, Q1, A1, Q2, A2, LANDING_2, NOTE, Q3, A3, Q3B, A3B]

OLD_PLAIN = T('2026-11-03T19:31:36.396Z', 'user', 'Build a Flywheel')
NEW_Q = T('2026-11-04T21:30:57.100Z', 'user', 'Definitions: a view = 30+ seconds of playback.')
NEW_A = T('2026-11-04T21:31:13.399Z', 'agent', 'On The Love Hypothesis, the BookTok exposed viewers on Prime Video: 2,889,107.')
STALE_CLIENT = [OLD_PLAIN, LANDING_1, LANDING_2, NOTE, NEW_Q, NEW_A]


class StaleClientSave(unittest.TestCase):
    def test_a_stale_save_never_removes_a_server_held_turn(self):
        merged = tm.merge_turns(STORED, STALE_CLIENT)
        kept = set(sigs(merged))
        for t in STORED:
            self.assertIn(sig(t), kept, 'stored turn dropped: ' + t['text'][:40])
        for t in STALE_CLIENT:
            self.assertIn(sig(t), kept, 'client turn dropped: ' + t['text'][:40])
        self.assertEqual(len(merged), len(STORED) + 3)     # Build a Flywheel + the two new turns

    def test_plain_user_and_agent_turns_survive_not_only_marked_ones(self):
        merged = tm.merge_turns(STORED, [NEW_Q, NEW_A])
        self.assertIn(sig(Q1), sigs(merged))
        self.assertIn(sig(A1), sigs(merged))
        self.assertIn(sig(Q3), sigs(merged))
        self.assertIn(sig(A3), sigs(merged))

    def test_an_empty_client_list_removes_nothing(self):
        self.assertEqual(sigs(tm.merge_turns(STORED, [])), sigs(tm._ordered(list(STORED))))
        self.assertEqual(sigs(tm.merge_turns(STORED, None)), sigs(tm._ordered(list(STORED))))

    def test_a_repeat_ask_at_a_later_moment_is_its_own_turn(self):
        merged = tm.merge_turns(STORED, STALE_CLIENT)
        same_text = [t for t in merged if t['text'] == Q3['text']]
        self.assertEqual(len(same_text), 2)       # 20:17:04 and 20:17:52 both stand
        same_answer = [t for t in merged if t['text'] == A3['text']]
        self.assertEqual(len(same_answer), 2)

    def test_order_holds_by_timestamp(self):
        merged = tm.merge_turns(STORED, STALE_CLIENT)
        eps = [tm.turn_epoch(t) for t in merged]
        self.assertEqual(eps, sorted(eps))
        self.assertEqual(merged[0]['text'], 'Build a Flywheel')
        self.assertEqual(merged[-1]['text'], NEW_A['text'])
        self.assertLess(merged.index(next(t for t in merged if t['text'] == NOTE['text'])),
                        merged.index(next(t for t in merged if t['text'] == Q3['text'])))

    def test_idempotent(self):
        once = tm.merge_turns(STORED, STALE_CLIENT)
        twice = tm.merge_turns(once, STALE_CLIENT)
        self.assertEqual(sigs(once), sigs(twice))
        self.assertEqual(tm.thread_rev(once), tm.thread_rev(twice))

    def test_differs_tells_the_tab_it_was_stale(self):
        merged = tm.merge_turns(STORED, STALE_CLIENT)
        self.assertTrue(tm.differs(STALE_CLIENT, merged))
        self.assertFalse(tm.differs(merged, tm.merge_turns(merged, merged)))


class MetaPatches(unittest.TestCase):
    def test_a_status_patch_applies_to_a_turn_the_client_holds(self):
        stored = [T('2026-11-04T10:00:00.000Z', 'agent', 'Queued: Reba McEntire.', run_id='r1', status='queued', subject='Reba McEntire')]
        client = [T('2026-11-04T10:00:00.000Z', 'agent', 'Queued: Reba McEntire.', run_id='r1', status='complete', subject='Reba McEntire')]
        merged = tm.merge_turns(stored, client)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['meta']['status'], 'complete')
        self.assertEqual(merged[0]['meta']['subject'], 'Reba McEntire')

    def test_a_done_status_never_goes_back_to_queued(self):
        stored = [T('2026-11-04T10:00:00.000Z', 'agent', 'Queued.', run_id='r1', status='complete')]
        stale = [T('2026-11-04T10:00:00.000Z', 'agent', 'Queued.', run_id='r1', status='queued')]
        self.assertEqual(tm.merge_turns(stored, stale)[0]['meta']['status'], 'complete')

    def test_server_marks_survive_a_client_copy_without_them(self):
        stored = [T('2026-11-04T10:00:00.000Z', 'agent', 'Deck ready.', deck_job_id='d1', link={'url': 'https://x/y.pptx', 'label': 'Download'})]
        client = [T('2026-11-04T10:00:00.000Z', 'agent', 'Deck ready.')]
        m = tm.merge_turns(stored, client)[0]['meta']
        self.assertEqual(m['deck_job_id'], 'd1')
        self.assertEqual(m['link']['url'], 'https://x/y.pptx')

    def test_the_client_turn_id_joins_the_stored_turn(self):
        stored = [T('2026-11-04T10:00:00.000Z', 'user', 'hello')]
        client = [dict(T('2026-11-04T10:00:00.000Z', 'user', 'hello'), turn_id='tabc')]
        self.assertEqual(tm.merge_turns(stored, client)[0]['turn_id'], 'tabc')

    def test_turn_id_is_the_identity_when_both_carry_one(self):
        stored = [dict(T('2026-11-04T10:00:00.000Z', 'user', 'hello'), turn_id='t1')]
        client = [dict(T('2026-11-04T10:00:00.000Z', 'user', 'hello'), turn_id='t1', meta={'pinned': True})]
        merged = tm.merge_turns(stored, client)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]['meta']['pinned'])
        other = [dict(T('2026-11-04T10:00:00.000Z', 'user', 'a different question'), turn_id='t2')]
        self.assertEqual(len(tm.merge_turns(stored, other)), 2)


class Corrections(unittest.TestCase):
    def test_a_correction_sticks_over_the_stale_client_text(self):
        stored = [T('2026-11-04T10:00:00.000Z', 'agent', 'US only. Everything in that read is US people.', corrected_at='2026-11-04T21:34:38Z', server_note=True)]
        client = [T('2026-11-04T10:00:00.000Z', 'agent', 'Global.')]
        merged = tm.merge_turns(stored, client)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]['text'].startswith('US only'))

    def test_a_newer_correction_replaces_an_older_one(self):
        stored = [T('2026-11-04T10:00:00.000Z', 'agent', 'first correction', corrected_at='2026-11-04T11:00:00Z')]
        client = [T('2026-11-04T10:00:00.000Z', 'agent', 'second correction', corrected_at='2026-11-04T12:00:00Z')]
        merged = tm.merge_turns(stored, client)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['text'], 'second correction')

    def test_a_server_turn_keeps_its_text_against_a_stale_copy(self):
        stored = [T('2026-11-04T10:00:00.000Z', 'agent', 'On HBO Max: 24.2% of the audience.', read_job_id='rj9')]
        client = [T('2026-11-04T10:00:00.000Z', 'agent', 'On it: reading HBO Max now.')]
        merged = tm.merge_turns(stored, client)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]['text'].startswith('On HBO Max'))
        self.assertEqual(merged[0]['meta']['read_job_id'], 'rj9')


class Repeats(unittest.TestCase):
    def test_exact_duplicates_collapse(self):
        t = T('2026-11-04T10:00:00.000Z', 'user', 'hello')
        merged = tm.merge_turns([t, t], [t, dict(t)])
        self.assertEqual(len(merged), 1)

    def test_second_and_millisecond_spellings_are_one_moment(self):
        a = T('2026-11-04T19:31:01Z', 'agent', 'note', server_note=True)
        b = T('2026-11-04T19:31:01.000Z', 'agent', 'note', server_note=True)
        self.assertEqual(len(tm.merge_turns([a], [b])), 1)

    def test_blank_agent_turns_are_filled_not_stored_blank(self):
        merged = tm.merge_turns([], [{'role': 'agent', 'ts': '2026-11-04T10:00:00.000Z'}])
        self.assertEqual(merged[0]['text'], tm.CALM_LINE)
        self.assertTrue(merged[0]['meta']['blank_filled'])
        again = tm.merge_turns(merged, [{'role': 'agent', 'ts': '2026-11-04T10:00:00.000Z'}])
        self.assertEqual(len(again), 1)

    def test_a_turn_with_no_stamp_keeps_its_place_at_the_end(self):
        stored = [T('2026-11-04T10:00:00.000Z', 'user', 'a')]
        client = [{'role': 'agent', 'text': 'no stamp'}, T('2026-11-04T09:00:00.000Z', 'user', 'earlier')]
        merged = tm.merge_turns(stored, client)
        self.assertEqual([t['text'] for t in merged], ['earlier', 'a', 'no stamp'])


# ------------------------------------------------------------ fake S3

class _S3Error(Exception):
    def __init__(self, code, status):
        super().__init__(code)
        self.response = {'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': status}}


class FakeS3:
    """ETag-aware: a stale IfMatch raises 412 the way the real client does."""

    def __init__(self):
        self.objs = {}
        self.puts = 0
        self.deleted = []

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
        self.puts += 1

    def delete_object(self, Bucket, Key):
        self.objs.pop(Key, None)
        self.deleted.append(Key)


class RacingS3(FakeS3):
    """A landing arrives between the save's read and its write, once."""

    def __init__(self, landing):
        super().__init__()
        self.landing = landing
        self.raced = False

    def put_object(self, Bucket, Key, Body, ContentType=None, IfMatch=None, IfNoneMatch=None, **kw):
        if not self.raced and IfMatch is not None:
            self.raced = True
            cur = json.loads(self.objs[Key].decode('utf-8'))
            cur.append(self.landing)
            self.objs[Key] = json.dumps(cur).encode('utf-8')
        return super().put_object(Bucket, Key, Body, ContentType=ContentType, IfMatch=IfMatch, IfNoneMatch=IfNoneMatch)


class MergeSave(unittest.TestCase):
    KEY = 'system/synth_chat_threads/jay/abc.json'

    def test_merge_save_keeps_the_stored_turns(self):
        s3 = FakeS3()
        s3.objs[self.KEY] = json.dumps(STORED).encode('utf-8')
        out = tm.merge_save(s3, 'b', self.KEY, STALE_CLIENT)
        on_disk = json.loads(s3.objs[self.KEY].decode('utf-8'))
        self.assertEqual(sigs(out), sigs(on_disk))
        self.assertEqual(len(on_disk), len(STORED) + 3)

    def test_merge_save_creates_a_missing_thread(self):
        s3 = FakeS3()
        out = tm.merge_save(s3, 'b', self.KEY, [Q1, A1])
        self.assertEqual(len(out), 2)
        self.assertIn(self.KEY, s3.objs)

    def test_a_writer_landing_mid_save_is_kept(self):
        s3 = RacingS3(LANDING_2)
        s3.objs[self.KEY] = json.dumps([Q1, A1]).encode('utf-8')
        out = tm.merge_save(s3, 'b', self.KEY, [Q1, A1, Q2])
        self.assertIn(sig(LANDING_2), sigs(out))
        self.assertIn(sig(Q2), sigs(out))
        self.assertEqual(sigs(out), sigs(json.loads(s3.objs[self.KEY].decode('utf-8'))))

    def test_the_growth_bound_trims_the_oldest(self):
        s3 = FakeS3()
        many = [T('2026-11-04T10:%02d:%02d.000Z' % (i // 60, i % 60), 'user', 'q%d' % i) for i in range(205)]
        out = tm.merge_save(s3, 'b', self.KEY, many)
        self.assertEqual(len(out), tm.MAX_TURNS)
        self.assertEqual(out[-1]['text'], 'q204')


# ------------------------------------------------------- legacy handlers

def _legacy_source(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
        return fh.read()


def _strip_decorators(src):
    return re.sub(r'^@.*\n', '', src, flags=re.M)


class _Req:
    def __init__(self, body=None, args=None):
        self._body = body or {}
        self.args = args or {}
        self.method = 'POST'

    def get_json(self, silent=False, force=False):
        return self._body


class _Host:
    def __init__(self, s3):
        self.s3_client = s3
        self.S3_BUCKET = 'b'
        self.app = self
        self.requires_auth = lambda f: f
        self.emails = []

    def route(self, *a, **k):
        return lambda f: f

    def _chatbot_route_guard(self, label):
        return lambda f: f

    def _chatbot_error_email(self, label, err, tb=None):
        self.emails.append((label, str(err)))

    def _chatbot_calm_payload(self):
        return {'success': False, 'error': 'calm'}


class LegacyHandlers(unittest.TestCase):
    """_save_synth_chat_history (the history POST) and the thread delete
    route, exec'd from source against the fake S3."""

    def _ns(self, s3, idx):
        from datetime import datetime, timezone
        import uuid
        ns = {'datetime': datetime, 'timezone': timezone, 'uuid': uuid, 'json': json, 'traceback': __import__('traceback'),
              '_H': _Host(s3), 'jsonify': lambda d: d, 'session': {'username': 'jay'},
              'SYNTH_CHAT_THREADS_PREFIX': 'system/synth_chat_threads'}
        chat = _legacy_source('prometheus/legacy/chat.py')
        thr = _legacy_source('prometheus/legacy/threads.py')

        def grab(src, name, stop=r'\n\n\n'):
            m = re.search(r'(^def %s\(.*?)(?=%s)' % (re.escape(name), stop), src, re.S | re.M)
            self.assertIsNotNone(m, name)
            return m.group(1)

        def grab_route(src, name):
            m = re.search(r'(@_H\.app\.route.*?\n)?(@_H\.requires_auth\n)?(def %s\(.*?)(?=\n\n\n)' % re.escape(name), src, re.S)
            self.assertIsNotNone(m, name)
            return m.group(3)

        def _pm_safe_user(u):
            return ''.join(c for c in (u or 'anon') if c.isalnum() or c in '-_.@').lower()

        def _pm_s3_json(key, default):
            try:
                return json.loads(s3.get_object(Bucket='b', Key=key)['Body'].read().decode('utf-8'))
            except Exception:
                return default

        def _pm_s3_put_json(key, obj):
            s3.put_object(Bucket='b', Key=key, Body=json.dumps(obj).encode('utf-8'))

        class _C:
            pass
        _C.SYNTH_CHAT_THREADS_PREFIX = 'system/synth_chat_threads'
        _C._pm_safe_user = staticmethod(_pm_safe_user)
        _C._pm_s3_json = staticmethod(_pm_s3_json)
        _C._pm_s3_put_json = staticmethod(_pm_s3_put_json)
        _C._synth_chat_gate = staticmethod(lambda allow_api_key=False: ({'username': 'jay'}, None))
        ns.update({'_C': _C, '_pm_safe_user': _pm_safe_user, '_pm_s3_json': _pm_s3_json, '_pm_s3_put_json': _pm_s3_put_json,
                   '_load_threads_index': lambda u: idx, 'request': _Req(),
                   '_synth_chat_gate': lambda allow_api_key=False: ({'username': 'jay'}, None),
                   '_pm_rotate_idle_thread': lambda u: None})
        for name in ('_pm_threads_index_key', '_pm_thread_key', '_pm_thread_title_from'):
            exec(grab(thr, name), ns)
        exec(grab_route(thr, 'api_synth_chat_threads_delete'), ns)
        for name in ('_pm_merge_save_thread', '_save_synth_chat_history', '_pm_active_thread_history', 'api_synth_chat_history'):
            exec(grab(chat, name), ns)
        return ns

    @staticmethod
    def _unpack(resp):
        return (resp[0], resp[1]) if isinstance(resp, tuple) else (resp, 200)

    def test_history_get_names_the_thread_and_its_rev(self):
        s3 = FakeS3()
        s3.objs['system/synth_chat_threads/jay/abc.json'] = json.dumps(STORED).encode('utf-8')
        ns = self._ns(s3, self._idx())
        req = _Req(); req.method = 'GET'; ns['request'] = req
        out, st = self._unpack(ns['api_synth_chat_history']())
        self.assertEqual(st, 200)
        self.assertEqual(out['thread_id'], 'abc')
        self.assertEqual(out['rev'], tm.thread_rev(STORED))
        self.assertEqual(len(out['history']), len(STORED))

    def test_history_get_by_thread_id_reads_that_thread_without_rotating(self):
        s3 = FakeS3()
        s3.objs['system/synth_chat_threads/jay/def.json'] = json.dumps([Q1, A1]).encode('utf-8')
        ns = self._ns(s3, self._idx())
        rotated = []
        ns['_pm_rotate_idle_thread'] = lambda u: rotated.append(u)
        req = _Req(args={'thread_id': 'def'}); req.method = 'GET'; ns['request'] = req
        out, st = self._unpack(ns['api_synth_chat_history']())
        self.assertEqual((st, out['thread_id'], len(out['history'])), (200, 'def', 2))
        self.assertEqual(rotated, [])
        req = _Req(args={'thread_id': 'nope'}); req.method = 'GET'; ns['request'] = req
        out, st = self._unpack(ns['api_synth_chat_history']())
        self.assertEqual(st, 404)
        req = _Req(args={'peek': '1'}); req.method = 'GET'; ns['request'] = req
        out, st = self._unpack(ns['api_synth_chat_history']())
        self.assertEqual((st, out['thread_id']), (200, 'abc'))
        self.assertEqual(rotated, [])

    def test_history_post_answers_with_the_merged_thread_when_the_tab_was_stale(self):
        s3 = FakeS3()
        s3.objs['system/synth_chat_threads/jay/abc.json'] = json.dumps(STORED).encode('utf-8')
        ns = self._ns(s3, self._idx())
        ns['request'] = _Req({'history': STALE_CLIENT, 'thread_id': 'abc'})
        out, st = self._unpack(ns['api_synth_chat_history']())
        self.assertEqual(st, 200)
        self.assertTrue(out['success'] and out['changed'])
        self.assertEqual(out['thread_id'], 'abc')
        self.assertEqual(len(out['history']), len(STORED) + 3)
        self.assertEqual(out['rev'], tm.thread_rev(out['history']))
        # in step: no history rides back, the rev does
        ns['request'] = _Req({'history': out['history'], 'thread_id': 'abc'})
        again, st = self._unpack(ns['api_synth_chat_history']())
        self.assertEqual(st, 200)
        self.assertNotIn('changed', again)
        self.assertNotIn('history', again)
        self.assertEqual(again['rev'], out['rev'])

    def test_history_post_to_a_lost_thread_is_404(self):
        s3 = FakeS3()
        ns = self._ns(s3, self._idx())
        ns['request'] = _Req({'history': [Q1], 'thread_id': 'gone'})
        out, st = self._unpack(ns['api_synth_chat_history']())
        self.assertEqual(st, 404)
        self.assertEqual(out['error'], 'unknown thread')

    def _idx(self):
        return {'active': 'abc', 'threads': [{'id': 'abc', 'title': 'New chat', 'created': 'x', 'updated': 'x', 'turns': 0},
                                             {'id': 'def', 'title': 'other', 'created': 'x', 'updated': 'x', 'turns': 2}]}

    def test_history_post_merges_and_reports_the_stale_tab(self):
        s3 = FakeS3()
        key = 'system/synth_chat_threads/jay/abc.json'
        s3.objs[key] = json.dumps(STORED).encode('utf-8')
        ns = self._ns(s3, self._idx())
        saved = ns['_save_synth_chat_history']('jay', STALE_CLIENT, 'abc')
        self.assertEqual(saved['thread_id'], 'abc')
        self.assertEqual(len(saved['history']), len(STORED) + 3)
        self.assertTrue(tm.differs(STALE_CLIENT, saved['history']))
        on_disk = json.loads(s3.objs[key].decode('utf-8'))
        for t in STORED:
            self.assertIn(sig(t), sigs(on_disk))
        self.assertEqual(ns['_load_threads_index']('jay')['threads'][0]['turns'], len(STORED) + 3)
        self.assertEqual(ns['_load_threads_index']('jay')['threads'][0]['title'], 'Build a Flywheel')

    def test_a_save_naming_a_thread_the_caller_lost_is_refused_not_redirected(self):
        s3 = FakeS3()
        key = 'system/synth_chat_threads/jay/abc.json'
        s3.objs[key] = json.dumps(STORED).encode('utf-8')
        ns = self._ns(s3, self._idx())
        saved = ns['_save_synth_chat_history']('jay', STALE_CLIENT, 'gone')
        self.assertTrue(saved.get('unknown_thread'))
        self.assertEqual(json.loads(s3.objs[key].decode('utf-8')), STORED)   # untouched

    def test_a_save_without_a_thread_lands_on_the_active_one(self):
        s3 = FakeS3()
        ns = self._ns(s3, self._idx())
        saved = ns['_save_synth_chat_history']('jay', [Q1, A1], None)
        self.assertEqual(saved['thread_id'], 'abc')
        self.assertEqual(len(saved['history']), 2)

    def test_the_explicit_delete_path_still_deletes(self):
        s3 = FakeS3()
        key = 'system/synth_chat_threads/jay/abc.json'
        s3.objs[key] = json.dumps(STORED).encode('utf-8')
        ns = self._ns(s3, self._idx())
        ns['request'] = _Req({'id': 'abc'})
        out = ns['api_synth_chat_threads_delete']()
        self.assertTrue(out['success'])
        self.assertNotIn(key, s3.objs)
        self.assertIn(key, s3.deleted)
        self.assertEqual(out['active'], 'def')
        self.assertEqual([t['id'] for t in out['threads']], ['def'])

    def test_a_shorter_list_is_not_a_delete(self):
        s3 = FakeS3()
        key = 'system/synth_chat_threads/jay/abc.json'
        s3.objs[key] = json.dumps(STORED).encode('utf-8')
        ns = self._ns(s3, self._idx())
        ns['_save_synth_chat_history']('jay', [], 'abc')
        self.assertEqual(len(json.loads(s3.objs[key].decode('utf-8'))), len(STORED))


class ServiceSaveThread(unittest.TestCase):
    """prometheus.service.save_thread (the app and API persist path) merges too."""

    def test_service_save_thread_merges_into_the_stored_thread(self):
        from prometheus import service
        from prometheus.host import host
        s3 = FakeS3()
        key = 'jay/abc'
        s3.objs[key] = json.dumps(STORED).encode('utf-8')
        idx = {'active': 'abc', 'threads': [{'id': 'abc', 'title': 'New chat'}]}
        host.bind(s3_client=s3, bucket='b', thread_key=lambda u, t: f'{u}/{t}', threads_index_key=lambda u: u + '/index',
                  load_threads_index=lambda u: idx, s3_json=lambda k, d=None: d,
                  s3_put_json=lambda k, o: s3.put_object(Bucket='b', Key=k, Body=json.dumps(o).encode('utf-8')),
                  thread_title_from=lambda t: 'title')
        service.save_thread('jay', 'abc', [Q1, A1, NEW_Q, NEW_A])
        on_disk = json.loads(s3.objs[key].decode('utf-8'))
        for t in STORED + [NEW_Q, NEW_A]:
            self.assertIn(sig(t), sigs(on_disk))
        self.assertEqual(idx['threads'][0]['turns'], len(STORED) + 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
