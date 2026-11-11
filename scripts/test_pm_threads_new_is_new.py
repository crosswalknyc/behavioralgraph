#!/usr/bin/env python3
"""New thread opens a NEW thread (Jenna 2026-11-05: "if you click for a new
one it opens a new thread not an old one"). Hermetic: the legacy thread
store and routes exec'd from source against a fake S3.

The defect: _pm_new_thread_into reused the active thread whenever the
index's turn count still read 0 and the title was 'New chat', and WIPED
its body to []. The widget's save of the thread it was leaving and its
New-thread call raced; when New landed first the server handed back the
SAME id and the save then merged the old turns straight back in. The
"new" thread was the old one, wizard step and all.

Covered here:
  * create: an active thread with turns in its body is never reused and
    never wiped, whatever the index says; a truly empty 'New chat' is
    reused (the rail never stacks blank chats) and keeps its body;
  * two threads: the new one sorts first, the old one is intact, the
    first save under the new id lands in a new document;
  * switch: activating the old thread returns exactly its turns;
  * stale save: a save that names the OLD thread id after the switch
    lands on the old thread, never the new one; a save naming a thread
    the caller lost is refused (404), never redirected;
  * the New-thread route calls the intake reset and the index it returns
    lists the new thread on top.
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from test_pm_thread_history_merge import FakeS3, _Host, _Req  # noqa: E402

CHAT = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')
THR = Path(ROOT, 'prometheus', 'legacy', 'threads.py').read_text(encoding='utf-8')
PREFIX = 'system/synth_chat_threads'


def _grab(src, name, stop=r'\n\n\n'):
    m = re.search(r'(^def %s\(.*?)(?=%s)' % (re.escape(name), stop), src, re.S | re.M)
    assert m, name
    return m.group(1)


def _grab_route(src, name):
    m = re.search(r'(def %s\(.*?)(?=\n\n\n)' % re.escape(name), src, re.S)
    assert m, name
    return m.group(1)


def T(ts, role, text):
    return {'role': role, 'text': text, 'ts': ts, 'turn_id': 't' + ts.replace(':', '').replace('-', '')[-10:] + role[0]}


OLD_TURNS = [T('2026-11-05T17:00:00.000Z', 'user', 'Value a brand partnership'),
             T('2026-11-05T17:00:01.000Z', 'agent', 'Who is the property or talent?'),
             T('2026-11-05T17:00:20.000Z', 'user', 'Jennifer Beals'),
             T('2026-11-05T17:00:21.000Z', 'agent', 'What campaign should I value?')]
NEW_TURNS = [T('2026-11-05T17:05:00.000Z', 'user', 'how big is the Gunna audience'),
             T('2026-11-05T17:05:09.000Z', 'agent', 'Gunna reaches 4,218,377 US people.')]


class Harness:
    def __init__(self, idx=None, bodies=None):
        self.s3 = FakeS3()
        self.reset_calls = []
        self.ns = self._ns()
        idx = idx if idx is not None else {'active': 'old1', 'threads': [
            {'id': 'old1', 'title': 'New chat', 'created': '2026-11-05T17:00:00Z', 'updated': '2026-11-05T17:00:00Z', 'turns': 0}]}
        self.put(f'{PREFIX}/jay/index.json', idx)
        for tid, body in (bodies or {}).items():
            self.put(f'{PREFIX}/jay/{tid}.json', body)

    def put(self, key, obj):
        self.s3.objs[key] = json.dumps(obj).encode('utf-8')

    def get(self, key, default=None):
        try:
            return json.loads(self.s3.objs[key].decode('utf-8'))
        except KeyError:
            return default

    def _ns(self):
        import uuid
        s3 = self.s3
        class _Faults:
            def note(self, *a, **k):
                return 0
        ns = {'datetime': datetime, 'timezone': timezone, 'uuid': uuid, 'json': json, 'traceback': __import__('traceback'),
              '_H': _Host(s3), 'jsonify': lambda d: d, 'session': {'username': 'jay'}, '_faults': _Faults(),
              'SYNTH_CHAT_THREADS_PREFIX': PREFIX, '_PM_MAX_THREADS': 40}

        def _pm_safe_user(u):
            return ''.join(c for c in (u or 'anon') if c.isalnum() or c in '-_.@').lower()

        def _pm_s3_json(key, default):
            try:
                return json.loads(s3.get_object(Bucket='b', Key=key)['Body'].read().decode('utf-8'))
            except Exception:
                return default

        def _pm_s3_put_json(key, obj):
            s3.put_object(Bucket='b', Key=key, Body=json.dumps(obj).encode('utf-8'))

        def _synth_chat_history_key(u):
            return f'system/synth_chat_history/{u}.json'

        class _C:
            pass
        _C.SYNTH_CHAT_THREADS_PREFIX = PREFIX
        _C._pm_safe_user = staticmethod(_pm_safe_user)
        _C._pm_s3_json = staticmethod(_pm_s3_json)
        _C._pm_s3_put_json = staticmethod(_pm_s3_put_json)
        _C._synth_chat_history_key = staticmethod(_synth_chat_history_key)
        _C._synth_chat_gate = staticmethod(lambda allow_api_key=False: ({'username': 'jay'}, None))
        ns.update({'_C': _C, '_pm_safe_user': _pm_safe_user, '_pm_s3_json': _pm_s3_json, '_pm_s3_put_json': _pm_s3_put_json,
                   '_synth_chat_history_key': _synth_chat_history_key, 'request': _Req(),
                   '_synth_chat_gate': lambda allow_api_key=False: ({'username': 'jay'}, None),
                   '_pm_rotate_idle_thread': lambda u: None})
        for name in ('_pm_threads_index_key', '_pm_thread_key', '_pm_thread_title_from', '_load_threads_index'):
            exec(_grab(THR, name), ns)
        for name in ('_pm_new_thread_into', '_pm_merge_save_thread', '_save_synth_chat_history', '_pm_active_thread_history',
                     'api_synth_chat_history'):
            exec(_grab(CHAT, name), ns)
        _C._pm_new_thread_into = staticmethod(ns['_pm_new_thread_into'])
        for name in ('api_synth_chat_threads_new', 'api_synth_chat_threads_activate', 'api_synth_chat_threads',
                     'api_synth_chat_threads_fresh'):
            exec(_grab_route(THR, name), ns)
        # the route imports prometheus.intake_reset; record the call instead of touching stores
        import types
        fake_pkg = types.ModuleType('prometheus')
        fake_reset = types.ModuleType('prometheus.intake_reset')
        fake_reset.on_new_thread = lambda u, t=None: self.reset_calls.append((u, t))
        fake_pkg.intake_reset = fake_reset
        fake_svc = types.ModuleType('prometheus.service')
        fake_svc.repair_thread_labels = lambda u: (ns['_load_threads_index'](u), [])
        fake_pkg.service = fake_svc
        from prometheus import thread_presence as _real_tp, thread_merge as _real_tm
        fake_pkg.thread_presence = _real_tp
        fake_pkg.thread_merge = _real_tm
        self._mods = {'prometheus': fake_pkg, 'prometheus.intake_reset': fake_reset, 'prometheus.service': fake_svc,
                      'prometheus.thread_presence': _real_tp, 'prometheus.thread_merge': _real_tm}
        return ns

    def call(self, name, body=None, args=None, method='POST'):
        req = _Req(body or {}, args or {})
        req.method = method
        self.ns['request'] = req
        saved = {k: sys.modules.get(k) for k in self._mods}
        sys.modules.update(self._mods)
        try:
            out = self.ns[name]()
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
        return (out[0], out[1]) if isinstance(out, tuple) else (out, 200)

    def index(self):
        return self.get(f'{PREFIX}/jay/index.json')

    def thread(self, tid):
        return self.get(f'{PREFIX}/jay/{tid}.json', [])


class NewThreadIsNew(unittest.TestCase):
    def test_an_active_thread_with_turns_in_its_body_is_never_reused_or_wiped(self):
        # the race: the body holds four turns, the index still says 0 (the save had not landed)
        h = Harness(bodies={'old1': OLD_TURNS})
        out, st = h.call('api_synth_chat_threads_new')
        self.assertEqual(st, 200)
        self.assertNotEqual(out['active'], 'old1')
        self.assertEqual(h.thread('old1'), OLD_TURNS)                       # intact, not wiped
        self.assertEqual(h.thread(out['active']), [])                       # the new document exists and is empty
        # the rail lists only threads with turns (2026-11-06): the new one is hidden until its first
        # turn (old1's lagging count is the sweep's to repair on the next rail load, see SweepAndPresence)
        self.assertNotIn(out['active'], [t['id'] for t in out['threads']])
        self.assertEqual({t['id'] for t in h.index()['threads']}, {'old1', out['active']})
        self.assertEqual(h.index()['active'], out['active'])
        self.assertEqual(h.reset_calls, [('jay', out['active'])])         # the intakes went with the old thread

    def test_a_truly_empty_new_chat_is_reused_and_kept(self):
        h = Harness(bodies={'old1': []})
        out, st = h.call('api_synth_chat_threads_new')
        self.assertEqual(out['active'], 'old1')
        self.assertEqual(len(h.index()['threads']), 1)
        self.assertEqual(out['threads'], [])                                # an empty thread is never listed
        self.assertEqual(h.thread('old1'), [])
        self.assertEqual(h.reset_calls, [('jay', 'old1')])

    def test_two_threads_save_list_switch_and_a_stale_save(self):
        h = Harness(bodies={'old1': OLD_TURNS})
        h.index()['threads'][0]['turns'] = 4
        idx = h.index(); idx['threads'][0]['turns'] = 4; h.put(f'{PREFIX}/jay/index.json', idx)
        out, _ = h.call('api_synth_chat_threads_new')
        new = out['active']
        # the first save under the new id lands in the new document, never the old one
        saved, st = h.call('api_synth_chat_history', {'history': NEW_TURNS, 'thread_id': new})
        self.assertEqual(st, 200)
        self.assertEqual(saved['thread_id'], new)
        self.assertEqual(h.thread(new), NEW_TURNS)
        self.assertEqual(h.thread('old1'), OLD_TURNS)
        # the list: new first (most recently updated), both present, titles from their first question
        lst, _ = h.call('api_synth_chat_threads', method='GET')
        self.assertEqual([t['id'] for t in lst['threads']], [new, 'old1'])
        self.assertEqual(lst['threads'][0]['title'], 'how big is the Gunna audience')
        self.assertEqual(lst['threads'][0]['turns'], 2)
        self.assertEqual(lst['active'], new)
        # a stale tab still on the OLD thread saves under the old id: it lands there, never on the new one
        stale = OLD_TURNS + [T('2026-11-05T17:06:00.000Z', 'user', 'Nike'), T('2026-11-05T17:06:01.000Z', 'agent', 'What are the campaign dates?')]
        saved, st = h.call('api_synth_chat_history', {'history': stale, 'thread_id': 'old1'})
        self.assertEqual((st, saved['thread_id']), (200, 'old1'))
        self.assertEqual(len(h.thread('old1')), 6)
        self.assertEqual(h.thread(new), NEW_TURNS)
        self.assertEqual(h.index()['active'], new)                          # a save never moves the active thread
        # switching back to the old thread returns exactly its turns
        act, st = h.call('api_synth_chat_threads_activate', {'id': 'old1'})
        self.assertEqual((st, act['active']), (200, 'old1'))
        self.assertEqual(act['history'], h.thread('old1'))
        self.assertEqual(len(act['history']), 6)
        self.assertEqual(h.index()['active'], 'old1')
        # and the new one is still exactly what was saved to it
        got, st = h.call('api_synth_chat_history', args={'thread_id': new}, method='GET')
        self.assertEqual((st, got['thread_id'], got['history']), (200, new, NEW_TURNS))
        # a save naming a thread the caller lost is refused, never redirected onto another thread
        lost, st = h.call('api_synth_chat_history', {'history': NEW_TURNS, 'thread_id': 'gone'})
        self.assertEqual(st, 404)
        self.assertEqual(h.thread(new), NEW_TURNS)
        self.assertEqual(len(h.thread('old1')), 6)

    def test_activate_an_unknown_thread_is_404(self):
        h = Harness(bodies={'old1': OLD_TURNS})
        out, st = h.call('api_synth_chat_threads_activate', {'id': 'nope'})
        self.assertEqual(st, 404)


def _ago(seconds, now=None):
    from datetime import timedelta
    from prometheus import thread_presence as TP
    return TP.iso((now or TP.now_utc()) - timedelta(seconds=seconds))


class SweepAndPresence(unittest.TestCase):
    """Jenna 2026-11-06: an empty chat is never saved to the rail; a reopen
    resumes unless the thread has been idle 15 minutes."""

    def _idx(self, now):
        return {'active': 'live', 'threads': [
            {'id': 'live', 'title': 'Build a Flywheel', 'created': _ago(3600, now), 'updated': _ago(120, now), 'turns': 18},
            {'id': 'e_old', 'title': 'New chat', 'created': _ago(3600, now), 'updated': _ago(3600, now), 'turns': 0},
            {'id': 'e_new', 'title': 'New chat', 'created': _ago(60, now), 'updated': _ago(60, now), 'turns': 0},
            {'id': 'lag', 'title': 'New chat', 'created': _ago(3600, now), 'updated': _ago(3600, now), 'turns': 0},
            {'id': 'proj', 'title': 'Costco', 'created': _ago(7200, now), 'updated': _ago(7200, now), 'turns': 4,
             'binding': {'subject_key': 'profile:Costco.csv', 'subject_label': 'Costco'}}]}

    def test_new_chat_creates_nothing_until_the_first_message(self):
        from prometheus import thread_presence as TP
        h = Harness(bodies={'old1': OLD_TURNS})
        idx = h.index(); idx['threads'][0]['turns'] = 4; h.put(f'{PREFIX}/jay/index.json', idx)
        n_before = len(h.index()['threads'])
        out, st = h.call('api_synth_chat_threads_fresh')
        self.assertEqual((st, out['success'], out['fresh'], out['active']), (200, True, True, None))
        self.assertEqual(len(h.index()['threads']), n_before)                 # no thread, no document
        self.assertEqual(sorted(k for k in h.s3.objs if k.startswith(f'{PREFIX}/jay/')), [f'{PREFIX}/jay/index.json', f'{PREFIX}/jay/old1.json'])
        self.assertTrue(h.index().get('fresh_at'))
        self.assertEqual(h.reset_calls, [('jay', None)])                       # the armed intakes went with the thread left
        # a reload lands on the same empty pane: the open decides fresh, names no thread
        got, st = h.call('api_synth_chat_history', args={'open': '1'}, method='GET')
        self.assertEqual((st, got['fresh'], got['thread_id'], got['history']), (200, True, None, []))
        self.assertEqual(got['why'], 'fresh_requested')
        # the first message mints the thread and ends the request
        out, st = h.call('api_synth_chat_threads_new')
        new = out['active']
        self.assertNotEqual(new, 'old1')
        self.assertNotIn('fresh_at', h.index())
        self.assertEqual(TP.thread_of(h.index(), new)['last_seen'][:2], '20')
        saved, st = h.call('api_synth_chat_history', {'history': NEW_TURNS, 'thread_id': new})
        self.assertEqual(st, 200)
        lst, _ = h.call('api_synth_chat_threads', method='GET')
        self.assertIn(new, [t['id'] for t in lst['threads']])                 # listed once it has a turn

    def test_the_greeting_alone_persists_nothing(self):
        h = Harness(bodies={'old1': []})
        greet = [{'role': 'agent', 'text': 'Good afternoon - Costco is open. Want me to dig into the data?',
                  'ts': '2026-11-06T17:00:00.000Z', 'meta': {'greeting': True}}]
        saved, st = h.call('api_synth_chat_history', {'history': greet, 'thread_id': 'old1'})
        self.assertEqual(st, 200)
        self.assertEqual(h.thread('old1'), [])                                  # nothing written
        self.assertEqual(h.index()['threads'][0]['turns'], 0)
        lst, _ = h.call('api_synth_chat_threads', method='GET')
        self.assertEqual(lst['threads'], [])                                    # and nothing in the rail
        # a reader's turn is what makes a thread
        saved, st = h.call('api_synth_chat_history', {'history': greet + [T('2026-11-06T17:01:00.000Z', 'user', 'how big is Costco')], 'thread_id': 'old1'})
        self.assertEqual(len(h.thread('old1')), 2)
        lst, _ = h.call('api_synth_chat_threads', method='GET')
        self.assertEqual([t['id'] for t in lst['threads']], ['old1'])

    def test_list_hides_zero_turn_threads(self):
        from prometheus import thread_presence as TP
        now = TP.now_utc()
        idx = self._idx(now)
        self.assertEqual([t['id'] for t in TP.visible_threads(idx['threads'])], ['live', 'proj'])
        h = Harness(idx=idx, bodies={'live': OLD_TURNS, 'proj': OLD_TURNS, 'e_old': [], 'e_new': [], 'lag': NEW_TURNS})
        lst, _ = h.call('api_synth_chat_threads', method='GET')
        self.assertEqual(sorted(t['id'] for t in lst['threads']), ['live', 'proj'])

    def test_sweep_removes_stale_empties_keeps_the_rest_and_repairs_a_lagging_count(self):
        from prometheus import thread_presence as TP
        now = TP.now_utc()
        idx = self._idx(now)
        store = {'live': OLD_TURNS, 'proj': OLD_TURNS, 'e_old': [], 'e_new': [], 'lag': NEW_TURNS}
        deleted = []
        res = TP.sweep_empty_threads(idx, load=lambda k: store.get(k.split('/')[-1][:-5]), put=lambda k, o: None,
                                     delete=lambda k: deleted.append(k.split('/')[-1][:-5]), key_for=lambda tid: f'x/{tid}.json',
                                     now=now, title_from=lambda body: 'how big is the Gunna audience')
        self.assertEqual(res['removed'], ['e_old'])                            # stale and truly empty: gone
        self.assertEqual(deleted, ['e_old'])
        self.assertEqual(res['kept_recent'], ['e_new'])                        # sixty seconds old: still being typed into
        self.assertEqual(res['repaired'], ['lag'])                             # a body with turns is never deleted: the count is fixed
        lag = TP.thread_of(idx, 'lag')
        self.assertEqual((lag['turns'], lag['title']), (2, 'how big is the Gunna audience'))
        self.assertEqual([t['id'] for t in idx['threads']], ['live', 'e_new', 'lag', 'proj'])
        self.assertEqual(idx['active'], 'live')
        self.assertTrue(idx.get('swept_at'))
        self.assertFalse(TP.sweep_due(idx, now))                               # once per ten minutes
        # the active empty thread and the last remaining thread are kept
        idx2 = {'active': 'only', 'threads': [{'id': 'only', 'title': 'New chat', 'created': _ago(9000, now), 'updated': _ago(9000, now), 'turns': 0}]}
        res2 = TP.sweep_empty_threads(idx2, load=lambda k: [], put=lambda k, o: None, delete=lambda k: deleted.append(k), key_for=lambda t: t, now=now)
        self.assertEqual(res2['removed'], [])
        self.assertEqual(len(idx2['threads']), 1)

    def test_sweep_through_the_host_writes_the_index_once(self):
        from prometheus import thread_presence as TP
        from prometheus.host import host
        now = TP.now_utc()
        s3 = FakeS3()

        def s3_json(key, default=None):
            try:
                return json.loads(s3.objs[key].decode('utf-8'))
            except KeyError:
                return default

        def s3_put_json(key, obj):
            s3.objs[key] = json.dumps(obj).encode('utf-8')
        host.bind(s3_client=s3, bucket='b', s3_json=s3_json, s3_put_json=s3_put_json,
                  thread_key=lambda u, t: f'{PREFIX}/{u}/{t}.json', threads_index_key=lambda u: f'{PREFIX}/{u}/index.json',
                  load_threads_index=lambda u: s3_json(f'{PREFIX}/{u}/index.json', {}), thread_title_from=lambda b: 'titled')
        idx = self._idx(now)
        s3_put_json(f'{PREFIX}/kim/index.json', idx)
        for tid, body in (('live', OLD_TURNS), ('proj', OLD_TURNS), ('e_old', []), ('e_new', []), ('lag', NEW_TURNS)):
            s3_put_json(f'{PREFIX}/kim/{tid}.json', body)
        res = TP.sweep_for_user('kim', now=now)
        self.assertEqual(res['removed'], ['e_old'])
        self.assertNotIn(f'{PREFIX}/kim/e_old.json', s3.objs)
        self.assertIn(f'{PREFIX}/kim/e_new.json', s3.objs)
        after = s3_json(f'{PREFIX}/kim/index.json')
        self.assertEqual([t['id'] for t in after['threads']], ['live', 'e_new', 'lag', 'proj'])
        self.assertEqual(TP.thread_of(after, 'lag')['turns'], 2)
        self.assertIsNone(TP.sweep_for_user('kim', now=now))                  # not due again

    def test_reopen_within_fifteen_minutes_resumes_the_same_thread(self):
        from prometheus import thread_presence as TP
        now = TP.now_utc()
        idx = self._idx(now)
        h = Harness(idx=idx, bodies={'live': OLD_TURNS})
        got, st = h.call('api_synth_chat_history', args={'open': '1'}, method='GET')
        self.assertEqual((st, got.get('fresh'), got['thread_id'], len(got['history'])), (200, None, 'live', 4))
        self.assertTrue(got['resumed'])
        self.assertEqual(got['why'], 'recent')
        self.assertTrue(TP.thread_of(h.index(), 'live').get('last_seen'))     # the open is activity
        # the tab names its own thread: the decision is that thread's
        got, st = h.call('api_synth_chat_history', args={'open': '1', 'thread_id': 'proj'}, method='GET')
        self.assertEqual((st, got.get('fresh'), got['thread_id']), (200, True, None))   # proj idle two hours
        got, st = h.call('api_synth_chat_history', args={'open': '1', 'thread_id': 'live'}, method='GET')
        self.assertEqual((got.get('fresh'), got['thread_id']), (None, 'live'))
        # a faked clock: the same index, fourteen minutes later, still resumes
        self.assertEqual(TP.fresh_decision(idx, now=TP.parse_iso(_ago(-14 * 60 + 120, now))), (False, 'recent'))

    def test_reopen_after_fifteen_minutes_opens_fresh_and_the_old_thread_stays_listed(self):
        from prometheus import thread_presence as TP
        now = TP.now_utc()
        idx = self._idx(now)
        idx['threads'][0]['updated'] = _ago(16 * 60, now)                    # last turn sixteen minutes ago, never seen since
        h = Harness(idx=idx, bodies={'live': OLD_TURNS, 'proj': OLD_TURNS})
        got, st = h.call('api_synth_chat_history', args={'open': '1'}, method='GET')
        self.assertEqual((st, got['fresh'], got['thread_id'], got['history'], got['fresh_from']), (200, True, None, [], 'live'))
        self.assertEqual(got['why'], 'idle')
        self.assertEqual(len([k for k in h.s3.objs if k.startswith(f'{PREFIX}/jay/')]), 3)   # nothing created
        lst, _ = h.call('api_synth_chat_threads', method='GET')
        self.assertIn('live', [t['id'] for t in lst['threads']])             # one click away in the rail
        self.assertEqual(h.index()['active'], 'live')
        # the widget open on it keeps it live: the pull's seen=1 stamps last_seen (throttled)
        got, st = h.call('api_synth_chat_history', args={'thread_id': 'live', 'seen': '1'}, method='GET')
        self.assertEqual(st, 200)
        self.assertTrue(TP.thread_of(h.index(), 'live').get('last_seen'))
        self.assertEqual(TP.fresh_decision(h.index(), now=now), (False, 'recent'))
        before = h.s3.puts
        h.call('api_synth_chat_history', args={'thread_id': 'live', 'seen': '1'}, method='GET')
        self.assertEqual(h.s3.puts, before)                                    # a stamp younger than a minute is not rewritten
        # the faked clock: fifteen minutes later with no turn and no widget, fresh again
        self.assertEqual(TP.fresh_decision(h.index(), now=TP.parse_iso(_ago(-15 * 60 - 1, now)))[0], True)

    def test_opened_by_the_project_follow_is_not_activity(self):
        from prometheus import thread_presence as TP
        now = TP.now_utc()
        th = {'id': 'p', 'turns': 3, 'updated': _ago(3600, now), 'opened': _ago(5, now)}
        self.assertEqual(TP.fresh_decision({'active': 'p', 'threads': [th]}, now=now), (True, 'idle'))
        th['last_seen'] = _ago(5, now)
        self.assertEqual(TP.fresh_decision({'active': 'p', 'threads': [th]}, now=now), (False, 'recent'))
        # an empty thread is the empty pane already, never 'fresh'
        self.assertEqual(TP.fresh_decision({'active': 'e', 'threads': [{'id': 'e', 'turns': 0, 'updated': _ago(9000, now)}]}, now=now), (False, 'empty_thread'))
        self.assertEqual(TP.fresh_decision({'active': 'x', 'threads': []}, now=now), (False, 'no_thread'))


class WidgetSource(unittest.TestCase):
    """The spliced widget: the new thread drops every armed step, the save of
    the thread being left lands first, every ask names its thread, Cancel
    reaches the server."""

    @classmethod
    def setUpClass(cls):
        cls.html = Path(ROOT, 'templates', 'index.html').read_text(encoding='utf-8')

    def _fn(self, name):
        i = self.html.index('function ' + name + '(')
        j = self.html.index('\n        function ', i + 10)
        return self.html[i:j]

    def test_reset_armed_states_releases_everything(self):
        src = self._fn('_pmResetArmedStates')
        self.assertIn('_pmReleaseArmedSteps()', src)
        self.assertIn('_pmBpiqWizard = null', src)
        self.assertIn("sessionStorage.removeItem('pmBpiqWiz')", src)

    def test_new_chat_waits_for_the_leaving_save_and_releases(self):
        src = self._fn('pmNewChat')
        # a pane, not a thread (2026-11-06): the leaving save lands, then threads/fresh; never threads/new
        self.assertNotIn("threads/new", src)
        self.assertLess(src.index('_pmHistorySave({ leaving: true'), src.index("fetch('/api/brief-chat/threads/fresh'"))
        self.assertIn('Promise.race', src)
        self.assertIn('_pmReleaseArmedSteps()', src)
        self.assertIn('_pmGoFresh(null, false)', src)
        gf = self._fn('_pmGoFresh')
        self.assertIn("inp.value = ''", gf)
        self.assertIn('_pmAdoptHistory([])', gf)
        self.assertIn('_pmPendingNew = true', gf)

    def test_every_ask_names_its_thread(self):
        src = self._fn('_pmV1Request')
        self.assertIn('b.thread_id = ', src)
        self.assertIn('_pmThreadsIdx.active', src)

    def test_cancel_reaches_the_server_and_the_drop_clears_the_tab(self):
        self.assertIn('data.intake_dropped', self.html)
        self.assertIn('_abody.intake_cancel = extra.intake_cancel', self.html)
        for flow in ('brand_partnership_wizard', 'attribution', 'flywheel', 'digital_twin', 'digital_journey'):
            self.assertIn("{ intake_cancel: '" + flow + "' }", self.html, flow)
        # every client-only cancel line is gone from the intakes: Cancel reaches the server
        self.assertEqual(self.html.count("'No problem. Ask me anything else when ready.'"), 0, 'a client-only cancel line survives')
        self.assertEqual(self.html.count("'Understood. Nothing is queued and nothing was charged. Ask me ' +"), 1,
                         'only the priced-read offer keeps its own client line')
        for flow in ('attribution', 'flywheel', 'digital_twin', 'digital_journey'):
            self.assertIn("{ intake_cancel: '" + flow + "', intake_confirm: true }", self.html, flow)
        # at a collect step a bare "no" is an answer, never a cancel: only the confirm steps and the
        # priced-read offer keep the broad regex
        self.assertEqual(self.html.count("no( thanks?)?|not now|cancel( (that|it))?)[.! ]*$/i.test("), 5)

    def test_switching_threads_lets_the_leaving_save_land_first(self):
        """pmSwitchThread (and the project follow) activate only after the save
        of the thread being left answered: the two used to race on the server's
        index and the save could put the thread just left back as active."""
        import shutil
        import subprocess
        import tempfile
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed')
        js = '\n'.join([
            "var calls = [], adopted = null;",
            "var window = {}; var _pmCarried = null; var synthChatHistory = [{ role: 'user', text: 'x' }];",
            "var _pmThreadsIdx = { active: 'old1', threads: [] }; var _pmFollow = { key: '' };",
            "function _pmHistorySave(o) { calls.push('save'); return new Promise(function (r) { setTimeout(function () { calls.push('saved'); r({ success: true }); }, 30); }); }",
            "function _pmAdoptHistory(h) { adopted = h; }",
            "function _pmProjectIdentity() { return null; }",
            "function _pmThreadsRender() {} function _pmThreadsLoad() {}",
            "function fetch(url, opts) { calls.push('fetch:' + url); return Promise.resolve({ json: function () { return Promise.resolve({ success: true, active: 'thr2', history: [{ role: 'user', text: 'old' }] }); } }); }",
            self._fn('pmSwitchThread'),
            "pmSwitchThread('thr2');",
            "setTimeout(function () { console.log(JSON.stringify({ calls: calls, active: _pmThreadsIdx.active, adopted: adopted })); }, 300);",
        ])
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as fh:
            fh.write(js)
            path = fh.name
        try:
            out = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(got['calls'], ['save', 'saved', 'fetch:/api/brief-chat/threads/activate'])
        self.assertEqual(got['active'], 'thr2')
        self.assertEqual(len(got['adopted']), 1)
        follow = self._fn('_pmThreadFollow')
        self.assertLess(follow.index('_pmHistorySave({ leaving: true'), follow.index("fetch('/api/brief-chat/threads/bind'"))
        self.assertIn('Promise.race', follow)

    def test_the_spliced_functions_run(self):
        """The widget functions, run in Node against stubs and a faked clock:
        New chat creates no thread (threads/fresh only), the pane is fresh, the
        first message mints the thread once (threads/new), a fresh pane saves
        nothing, the reopen check resumes or goes fresh on the server's word,
        and the ask body names the thread once one exists."""
        import shutil
        import subprocess
        import tempfile
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed')
        js = '\n'.join([
            "var clock = 1000000; Date.now = function () { return clock; };",
            "var calls = [], released = 0, adopted = null, rendered = 0, greeted = [], inp = { value: 'half typed', style: {}, focus: function () { calls.push('focus'); } };",
            "var document = { getElementById: function (id) { return id === 'synthChatInput' ? inp : null; }, visibilityState: 'visible', addEventListener: function () {} };",
            "var window = {}; var _pmCarried = { from: 'a' }; var synthChatHistory = [{ role: 'user', text: 'x' }];",
            "var _pmThreadsIdx = { active: 'old1', threads: [{ id: 'old1', turns: 2 }] }; var _pmFollow = { key: null }; var _pmHistoryRev = null;",
            "var synthChatInflightController = null, _pmDeckInFlight = false; function _pmHistoryBody(h) { return JSON.stringify({ history: h, thread_id: _pmThreadsIdx.active }); }",
            "function _pmReleaseArmedSteps() { released++; }",
            "function _pmAdoptHistory(h) { adopted = h; synthChatHistory = h; }",
            "function _pmProjectIdentity() { return null; }",
            "function _pmThreadsRender() { rendered++; }",
            "function _pmThreadsLoad() { calls.push('threadsLoad'); }",
            "function _pmHistoryMergeIn(h) { calls.push('mergeIn:' + h.length); return false; }",
            "function _synthChatShowWelcomeBack(force) { greeted.push(!!force); }",
            "var serverSays = { success: true, fresh: true, thread_id: null, history: [] };",
            "function fetch(url, opts) { calls.push('fetch:' + url); if (url.indexOf('threads/new') !== -1) return Promise.resolve({ json: function () { return Promise.resolve({ success: true, active: 'new9', threads: [] }); } }); if (url.indexOf('threads/fresh') !== -1) return Promise.resolve({ json: function () { return Promise.resolve({ success: true, fresh: true, active: null, threads: [{ id: 'old1', turns: 2 }] }); } }); return Promise.resolve({ ok: true, json: function () { return Promise.resolve(serverSays); } }); }",
            "var _PM_V1_SURFACES = { '/api/brief-chat/analyze': 'analyze' };",
            self._fn('_pmHistorySave'),
            self._fn('_pmIdleCheck'),
            self._fn('_pmGoFresh'),
            self._fn('_pmReopenCheck'),
            self._fn('_pmEnsureThread'),
            self._fn('pmNewChat'),
            self._fn('_pmV1Request'),
            "var out = {};",
            "pmNewChat(function (d) {",
            "  out.newchat = { calls: calls.slice(), pending: _pmPendingNew, active: _pmThreadsIdx.active, adopted: adopted, input: inp.value, released: released, greeted: greeted.slice() };",
            "  var saved = _pmHistorySave(); saved.then(function (v) { out.saveWhilePending = { value: v, fetched: calls.filter(function (c) { return c === 'fetch:/api/brief-chat/history'; }).length };",
            "  calls.length = 0;",
            "  _pmEnsureThread().then(function (tid) { out.ensure = { tid: tid, pending: _pmPendingNew, active: _pmThreadsIdx.active, calls: calls.slice() };",
            "    return _pmEnsureThread(); }).then(function (tid2) { out.ensureAgain = { tid: tid2, calls: calls.slice() };",
            "    out.ask = _pmV1Request('/api/brief-chat/analyze', { text: 'hi' }).body.thread_id;",
            "    /* reopen within fifteen minutes: the server resumes the thread the reader was last in */",
            "    calls.length = 0; clock += 60 * 1000; serverSays = { success: true, resumed: true, thread_id: 'new9', history: [{ role: 'user', text: 'hi' }, { role: 'agent', text: 'Hello.' }], rev: 'r1' };",
            "    _pmReopenCheck(); return new Promise(function (r) { setTimeout(r, 20); }); }).then(function () { out.reopenRecent = { calls: calls.slice(), active: _pmThreadsIdx.active, pending: _pmPendingNew, hist: synthChatHistory.length };",
            "    /* reopen after fifteen minutes: the server says fresh; the pane empties, no thread is created */",
            "    calls.length = 0; clock += 16 * 60 * 1000; serverSays = { success: true, fresh: true, thread_id: null, history: [], fresh_from: 'new9' };",
            "    _pmReopenCheck(); return new Promise(function (r) { setTimeout(r, 20); }); }).then(function () { out.reopenIdle = { calls: calls.slice(), active: _pmThreadsIdx.active, pending: _pmPendingNew, hist: synthChatHistory.length, greeted: greeted.slice(-1) };",
            "    console.log(JSON.stringify(out)); }); });",
            "});",
        ])
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as fh:
            fh.write(js)
            path = fh.name
        try:
            out = subprocess.run([node, path], capture_output=True, text=True, timeout=30)
        finally:
            os.unlink(path)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout.strip().splitlines()[-1])
        nc = got['newchat']
        # New chat: the leaving save, then threads/fresh; never threads/new; the pane is fresh and empty
        self.assertNotIn('fetch:/api/brief-chat/threads/new', nc['calls'])
        self.assertIn('fetch:/api/brief-chat/threads/fresh', nc['calls'])
        self.assertLess(nc['calls'].index('fetch:/api/brief-chat/history'), nc['calls'].index('fetch:/api/brief-chat/threads/fresh'))
        self.assertTrue(nc['pending'])
        self.assertIsNone(nc['active'])
        self.assertEqual(nc['adopted'], [])
        self.assertEqual(nc['input'], '')
        self.assertGreaterEqual(nc['released'], 1)
        self.assertEqual(nc['greeted'], [])                          # New chat shows the empty-state panel, not the greeting
        # a fresh pane saves nothing
        self.assertIsNone(got['saveWhilePending']['value'])
        self.assertEqual(got['saveWhilePending']['fetched'], 1)      # only the leaving save before the pane went fresh
        # the first message mints the thread exactly once
        self.assertEqual(got['ensure']['tid'], 'new9')
        self.assertFalse(got['ensure']['pending'])
        self.assertEqual(got['ensure']['active'], 'new9')
        self.assertEqual(got['ensure']['calls'], ['fetch:/api/brief-chat/threads/new'])
        self.assertEqual(got['ensureAgain']['tid'], 'new9')
        self.assertEqual(got['ensureAgain']['calls'], ['fetch:/api/brief-chat/threads/new'])
        self.assertEqual(got['ask'], 'new9')
        # a reopen inside fifteen minutes resumes the same thread and names it to the server
        rr = got['reopenRecent']
        self.assertEqual(rr['calls'], ['fetch:/api/brief-chat/history?open=1&thread_id=new9', 'mergeIn:2'])   # the server's turns fold in
        self.assertEqual((rr['active'], rr['pending']), ('new9', False))
        # a reopen after fifteen minutes opens fresh: no thread, empty pane, the greeting with its chips
        ri = got['reopenIdle']
        self.assertEqual(ri['calls'][0], 'fetch:/api/brief-chat/history?open=1&thread_id=new9')
        self.assertNotIn('fetch:/api/brief-chat/threads/new', ri['calls'])
        self.assertEqual((ri['active'], ri['pending'], ri['hist']), (None, True, 0))
        self.assertEqual(ri['greeted'], [True])

    def test_the_idle_rotation_on_tab_return_is_retired(self):
        src = self._fn('_pmIdleCheck')
        self.assertNotIn('fetch(', src)
        self.assertIn('return;', src)
        self.assertIn("'/api/brief-chat/history?open=1'", self._fn('synthChatInit'))
        self.assertIn('_pmReopenCheck()', self.html[self.html.index('window.prometheusExpand = function()'):self.html.index('window.prometheusCollapse = function()')])
        self.assertNotIn('pmNewChat(', self.html[self.html.index('window.prometheusExpand = function()'):self.html.index('window.prometheusCollapse = function()')])
        self.assertIn("'&seen=1'", self._fn('_pmServerTurnsPull'))
        # 2026-11-11: synthChatSubmit is the wrapper that keeps the ask's thread; the ask itself is _synthChatSubmitAsk
        sub = self.html[self.html.index('async function _synthChatSubmitAsk()'):]
        sub = sub[:sub.index('\n        }\n', 0) + 1] if '\n        }\n' in sub[:20000] else sub[:20000]
        self.assertLess(sub.index('await _pmEnsureThread()'), sub.index("synthChatPushTurn('user', text)"))
        self.assertLess(sub.index('await _pmEnsureThread()'), sub.index('_pmAskOriginPin(text)'))
        self.assertLess(sub.index('_pmAskOriginPin(text)'), sub.index("synthChatPushTurn('user', text)"))

if __name__ == '__main__':
    unittest.main(verbosity=1)
