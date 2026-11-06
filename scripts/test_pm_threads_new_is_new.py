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
        ns = {'datetime': datetime, 'timezone': timezone, 'uuid': uuid, 'json': json, 'traceback': __import__('traceback'),
              '_H': _Host(s3), 'jsonify': lambda d: d, 'session': {'username': 'jay'},
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
        for name in ('api_synth_chat_threads_new', 'api_synth_chat_threads_activate', 'api_synth_chat_threads'):
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
        self._mods = {'prometheus': fake_pkg, 'prometheus.intake_reset': fake_reset, 'prometheus.service': fake_svc}
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
        self.assertEqual([t['id'] for t in out['threads']][0], out['active'])   # new one on top
        self.assertEqual(len(out['threads']), 2)
        self.assertEqual(h.index()['active'], out['active'])
        self.assertEqual(h.reset_calls, [('jay', out['active'])])         # the intakes went with the old thread

    def test_a_truly_empty_new_chat_is_reused_and_kept(self):
        h = Harness(bodies={'old1': []})
        out, st = h.call('api_synth_chat_threads_new')
        self.assertEqual(out['active'], 'old1')
        self.assertEqual(len(out['threads']), 1)
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
        self.assertLess(src.index('_pmHistorySave({ leaving: true'), src.index("fetch('/api/brief-chat/threads/new'"))
        self.assertIn('Promise.race', src)
        self.assertIn('_pmReleaseArmedSteps()', src)
        self.assertIn("inp.value = ''", src)
        self.assertIn('_pmAdoptHistory([])', src)

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

    def test_the_spliced_functions_run(self):
        """The widget functions, run in Node against stubs: a new chat waits for
        the leaving save, releases every armed step, posts to threads/new,
        adopts an empty history and clears the composer; the ask body names
        the active thread."""
        import shutil
        import subprocess
        import tempfile
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed')
        js = '\n'.join([
            "var calls = [], released = 0, adopted = null, rendered = 0, inp = { value: 'half typed', style: {}, focus: function () { calls.push('focus'); } };",
            "var document = { getElementById: function (id) { return id === 'synthChatInput' ? inp : null; } };",
            "var window = {}; var _pmCarried = { from: 'a' }; var synthChatHistory = [{ role: 'user', text: 'x' }];",
            "var _pmThreadsIdx = { active: 'old1', threads: [] }; var _pmFollow = { key: null };",
            "function _pmHistorySave(o) { calls.push('save:' + JSON.stringify(o)); return new Promise(function (r) { setTimeout(function () { calls.push('saved'); r({ success: true }); }, 30); }); }",
            "function _pmReleaseArmedSteps() { released++; }",
            "function _pmAdoptHistory(h) { adopted = h; }",
            "function _pmProjectIdentity() { return null; }",
            "function _pmThreadsRender() { rendered++; }",
            "function fetch(url, opts) { calls.push('fetch:' + url); return Promise.resolve({ json: function () { return Promise.resolve({ success: true, active: 'new9', threads: [{ id: 'new9' }, { id: 'old1' }] }); } }); }",
            "var _PM_V1_SURFACES = { '/api/brief-chat/analyze': 'analyze' };",
            self._fn('pmNewChat'),
            self._fn('_pmV1Request'),
            "pmNewChat(function (d) {",
            "  var req = _pmV1Request('/api/brief-chat/analyze', { text: 'hi' });",
            "  console.log(JSON.stringify({ calls: calls, released: released, adopted: adopted, rendered: rendered, input: inp.value,",
            "    active: _pmThreadsIdx.active, carried: _pmCarried, askUrl: req.url, askTid: req.body.thread_id }));",
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
        # the save of the thread being left lands before the new thread is asked for
        self.assertEqual(got['calls'][:3], ['save:{"leaving":true,"keepalive":true}', 'saved', 'fetch:/api/brief-chat/threads/new'])
        self.assertGreaterEqual(got['released'], 1)
        self.assertEqual(got['adopted'], [])
        self.assertEqual(got['input'], '')
        self.assertEqual(got['active'], 'new9')
        self.assertIsNone(got['carried'])
        # the next ask names the new thread
        self.assertEqual(got['askUrl'], '/api/prometheus/v1/ask')
        self.assertEqual(got['askTid'], 'new9')


if __name__ == '__main__':
    unittest.main(verbosity=1)
