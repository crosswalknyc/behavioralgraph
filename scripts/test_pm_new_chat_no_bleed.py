#!/usr/bin/env python3
"""A new chat never carries anything from an earlier one (Jenna 2026-11-11).

Armine Meseldzhyan (Lionsgate), verbatim: "sometimes when I start a new
chat with Prometheus and ask a question about a new profile, such as
Cruel Summer, it will pull in information from past chats, like Mad Men,
and switch the subject."

What her record showed on 2026-11-10 (thread documents, ask log, the two
read job documents), each held here:

  * the read worker re-derived a job's thread as "the reader's active
    thread", so the Mad Men read (asked on thread 07feee4242) carried
    thread bdacfec6e6, the Cruel Summer thread she had opened while it
    ran, and landed there                         -> JobKeepsItsThread
  * the widget wrote every reply and finished read into the thread on
    screen when it arrived (the Mad Men "On it" line in the Cruel Summer
    thread, the Cruel Summer read under the Mad Men question, two
    replies in fresh panes that became threads)   -> Widget
  * an ask naming no thread, or one the index had lost, ran on the
    active thread: its pin, its binding, its turns -> TheAskStaysOnItsThread
  * carried_from, cross-thread offer approval, the per-reader intake
    stash, remembered subjects and windows, private notes, the page
    confirm and the last file all crossed threads  -> NothingCrosses...

The rule (prometheus/thread_scope.py): a turn belongs to the thread it
was asked in; a follow-up inherits inside its thread; nothing inherits
across threads.

Hermetic: a stub host over an in-memory store inside a Flask request
context, the legacy job binder exec'd from source, the widget functions
run in Node against stubs. No model, no S3, no app module. One section
(TheRealDoor) drives the real routes through the replay harness when
the parent repo's behavior harness is on the path, and skips otherwise.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import flask  # noqa: E402

from prometheus import thread_scope as TS  # noqa: E402
from prometheus import service as S  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402
from prometheus import pending_offers as PO  # noqa: E402
from prometheus import pending_intake as PI  # noqa: E402
from prometheus import shared_memory as SM  # noqa: E402
from prometheus import thread_presence as TP  # noqa: E402
from prometheus.host import host  # noqa: E402
import prometheus_memory as PMM  # noqa: E402

APP = flask.Flask('pm_no_bleed')
BUCKET = 'b'
THREADS = 'system/synth_chat_threads'
USER = 'armine'
ARMINE = {'username': USER, 'email': 'armine@example.com', 'company': 'Lionsgate', 'role': 'user', 'credits': -1,
          'unlimited': True, 'has_chatbot_profile_iq_access': True}

T_MAD = '07feee4242'          # the finished Mad Men thread
T_NEW = 'bdacfec6e6'          # the new chat
MAD_PAGE = {'view': 'profile-iq', 'primary': {'s3_key': 'Mad_Men_07_17_2026_07_59.csv', 'name': 'Mad Men'},
            'view_context': {'view_id': 'profileIQ', 'view_title': 'Mad Men', 'data': {}}}
MAD_Q = 'For Mad Men series viewers, can we compare 2025 against 2026 to date by age and gender?'
MAD_READ = ('On people who watched Mad Men in the window:\n\nOn people who watched Mad Men: 7,582,850 US viewers in 2025 and '
            '6,418,273 so far in 2026 through Sep 14, with the mix moving younger.\n\n- Age: 35-44 and 45-54 carry the period.')
MAD_THREAD = [
    {'role': 'user', 'text': MAD_Q, 'ts': '2026-11-10T21:41:26.394Z', 'turn_id': 'tmad1'},
    {'role': 'agent', 'text': 'On it: reading Mad Men now. This one takes a real look at the data, so give me a moment - the read '
                              'will land right here when it is ready.', 'ts': '2026-11-10T21:41:45.000Z', 'turn_id': 'tmad2',
     'meta': {'read_job_id': '9248ff753809', 'subject': 'Mad Men'}},
    {'role': 'agent', 'text': MAD_READ, 'ts': '2026-11-10T21:44:47.000Z',
     'meta': {'kind': 'read', 'read_job_id': '9248ff753809', 'subject': 'Mad Men', 'landed_at': '2026-11-10T21:44:47Z'}},
]
OFFER_Q = 'What other titles did Mad Men viewers watch on Netflix?'
OFFER_TURN = {'role': 'agent', 'text': 'I can pull that read on Mad Men viewers. Pulling this data will cost $100.',
              'ts': None, 'meta': {'panel_offer': {'question': OFFER_Q, 'subject': 'Mad Men', 'kind': 'custom', 'price_label': '$100'},
                                   'options': [{'label': 'Approve', 'send': 'Approve'}, {'label': 'Cancel', 'send': 'Cancel'}]}}
CRUEL_BUILD = 'Run a profile for cruel summer series'
CRUEL_Q = 'What share of Cruel Summer viewers are women?'


def _iso(dt_s=0):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + dt_s))


class _S3Error(Exception):
    def __init__(self, code, status):
        super().__init__(code)
        self.response = {'Error': {'Code': code}, 'ResponseMetadata': {'HTTPStatusCode': status}}


class FakeS3:
    def __init__(self):
        self.objs = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            raise _S3Error('NoSuchKey', 404)
        return {'Body': io.BytesIO(self.objs[Key]), 'ETag': '"x"'}

    def put_object(self, Bucket, Key, Body, ContentType=None, **kw):
        self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')

    def delete_object(self, Bucket, Key):
        self.objs.pop(Key, None)

    def list_objects_v2(self, Bucket, Prefix='', **kw):
        return {'Contents': [{'Key': k} for k in sorted(self.objs) if k.startswith(Prefix)], 'IsTruncated': False}


class World:
    """One reader, a finished Mad Men thread that is the ACTIVE thread on
    the server, and every per-reader store a chat can leave behind."""

    def __init__(self):
        self.s3 = FakeS3()
        self.calls = []            # what reached a core: (surface, text, body, seen)
        self.hints = []
        self.memory = {'asks': [], 'notes': [], 'preferences': []}
        self._bind()
        idx = {'active': T_MAD, 'threads': [
            {'id': T_MAD, 'title': 'For Mad Men series viewers, can we compare', 'created': _iso(-900), 'updated': _iso(-300),
             'last_seen': _iso(-300), 'turns': len(MAD_THREAD),
             'pin': {'subject': 'Mad Men', 'source': 'offer', 'set_at': _iso(-880), 'dropped': []}}]}
        self.put(f'{THREADS}/{USER}/index.json', idx)
        self.put(f'{THREADS}/{USER}/{T_MAD}.json', MAD_THREAD)
        self._patch_memory()

    # ---- store
    def put(self, key, obj):
        self.s3.objs[key] = json.dumps(obj).encode('utf-8')

    def get(self, key, default=None):
        try:
            return json.loads(self.s3.objs[key].decode('utf-8'))
        except KeyError:
            return default

    def index(self):
        return self.get(f'{THREADS}/{USER}/index.json')

    def _bind(self):
        w = self

        def _seen():
            g = flask.g
            return {'thread_id': getattr(g, '_pm_thread_id', None), 'pin': getattr(g, '_pm_thread_pin', None),
                    'binding': getattr(g, '_pm_thread_binding', None), 'scoped': getattr(g, '_pm_thread_scoped', False),
                    'referents': PMM.recent_referents(USER, k=3), 'window': PMM.last_window(USER),
                    'notes': SM.notes_block(USER)}

        def _analyze_core(user, body, text, history):
            w.calls.append(('analyze', text, dict(body), _seen()))
            out = {'success': True, 'action': 'answer', 'reply': 'On it: reading now. The read will land right here.',
                   'read_job_id': 'job-' + str(len(w.calls)), 'followups': []}
            if 'digital original content' in text.lower():
                out['subject'] = 'Digital original content categories'
            return out

        def _interpret_core(user, body, text, history):
            w.calls.append(('interpret', text, dict(body), _seen()))
            return {'success': True, 'spec_draft': {'subject': 'Cruel Summer'}, 'estimated_credits': 5}

        def _deck_core(user, body):
            w.calls.append(('deck', str(body.get('text') or ''), dict(body), _seen()))
            return {'success': True, 'clarify': {'question': 'Which profile should the deck cover?', 'options': []}}

        host.bind(analyze_core=_analyze_core, interpret_core=_interpret_core,
                  deck_core=_deck_core,
                  ask_hint=lambda **k: w.hints.append(k), gate_analyze=lambda u: True, gate_pull=lambda u: True,
                  funds_gate=lambda u: None, gate_refusal=lambda s: ({'success': False, 'error': 'no'}, 403),
                  calm_payload=lambda: {'success': False, 'error': 'calm'}, error_email=lambda *a, **k: None,
                  load_threads_index=lambda u: w.get(f'{THREADS}/{u}/index.json') or {'threads': []},
                  max_threads=40, s3_json=lambda key, default=None: w.get(key, default), s3_put_json=w.put,
                  thread_key=lambda u, t: f'{THREADS}/{u}/{t}.json', threads_index_key=lambda u: f'{THREADS}/{u}/index.json',
                  thread_title_from=lambda turns: next((str(t.get('text'))[:48] for t in turns if t.get('role') == 'user'), 'New chat'),
                  bucket=BUCKET, s3_client=w.s3, job_owner_ok=lambda a, b: True, claude_data=lambda *a, **k: {},
                  load_users=lambda: {'users': {USER: dict(ARMINE)}}, check_user_credits=lambda u: (True, -1),
                  consume_credit=lambda *a, **k: True, pricing_question=lambda t: False,
                  pricing_copy_for=lambda u: '', pricing_copy='')

    def _patch_memory(self):
        w = self
        PMM._SYNC_FOR_TESTS = True
        PMM._read_cache.clear()
        PMM._read_with_etag = lambda user: (w.memory, None)

        def _update(user, mutate_fn, max_retries=4):
            out = mutate_fn(w.memory)
            if isinstance(out, dict):
                w.memory = out
            PMM._read_cache.clear()
            return w.memory
        PMM._update = _update

    # ---- what the Mad Men chat left behind, written the way production writes it (on thread T_MAD)
    def leave_mad_men_state(self):
        with APP.test_request_context('/'):
            TS.set_current_thread(T_MAD)
            PMM.remember(USER, MAD_Q, subject='Mad Men', cohort='Mad Men viewers', route='generated',
                         window={'start': '2025-01-01', 'end': '2025-12-30'}, region='Los Angeles')
            SM.add_notes(USER, ['Is comparing Mad Men viewers year over year for Lionsgate.'], subject='Mad Men')
            PI.stash(self.s3, BUCKET, USER, 'digital_journey', {'subject': 'Mad Men', 'platform': 'Netflix'}, 'Run the journey')
            PO.record(USER, T_MAD, {'panel_offer': OFFER_TURN['meta']['panel_offer'], 'subject': 'Mad Men'}, OFFER_Q)
        PMM._read_cache.clear()

    # ---- one ask through the service door
    def ask(self, text, *, thread_id=None, history=None, client='dashboard', page=None, surface=None, extra=None):
        self.calls.clear()
        body = {'text': text, 'client': client}
        if history is not None:
            body['history'] = list(history)
        if thread_id:
            body['thread_id'] = thread_id
        if page is not None:
            body['context'] = page
        if surface:
            body['surface'] = surface
            body['_client_surface_sent'] = surface
        body.update(extra or {})
        with APP.test_request_context('/api/prometheus/v1/ask', method='POST'):
            env, st = S.ask(dict(ARMINE), body)
            g_tid = getattr(flask.g, '_pm_thread_id', None)
            g_pin = getattr(flask.g, '_pm_thread_pin', None)
        return env, st, {'thread_id': g_tid, 'pin': g_pin}

    def new_thread(self, tid=T_NEW):
        """The widget minted a thread for a fresh pane (threads/new)."""
        idx = self.index()
        idx['threads'].append({'id': tid, 'title': 'New chat', 'created': _iso(), 'updated': _iso(), 'last_seen': _iso(), 'turns': 0})
        idx['active'] = tid
        self.put(f'{THREADS}/{USER}/index.json', idx)
        self.put(f'{THREADS}/{USER}/{tid}.json', [])
        return tid


def _bind_in(tid, text, history, page=''):
    """The one binder, read the way a core reads it on thread ``tid``."""
    with APP.test_request_context('/'):
        S.stash_binding(USER, tid, strict=True)
        from prometheus import thread_subject as _tsub
        _tsub.scope_to_history(history, extra=(text,))
        s = SR.bind(text, history, page)
        return s, SR.thread_subjects(history, page), SR.bound_thread_subject()


# ============================================================================ 1
class TheRule(unittest.TestCase):
    def test_a_named_thread_the_reader_has_is_that_thread(self):
        self.assertEqual(TS.resolve_ask_thread('t1', 'dashboard', exists=lambda t: True), ('t1', 'named'))
        self.assertEqual(TS.resolve_ask_thread('t1', 'api', exists=lambda t: True), ('t1', 'named'))

    def test_a_lost_thread_is_relisted_for_the_dashboard_never_swapped(self):
        relisted = []
        got = TS.resolve_ask_thread('lost123', 'dashboard', exists=lambda t: False, relist=lambda t: relisted.append(t),
                                    mint=lambda: 'should-not-mint')
        self.assertEqual(got, ('lost123', 'relisted'))
        self.assertEqual(relisted, ['lost123'])
        # an id that is not an id is never written to the store
        got = TS.resolve_ask_thread('../../etc', 'dashboard', exists=lambda t: False, relist=lambda t: relisted.append(t),
                                    mint=lambda: 'fresh1')
        self.assertEqual(got, ('fresh1', 'minted'))
        self.assertEqual(relisted, ['lost123'])

    def test_no_thread_named(self):
        self.assertEqual(TS.resolve_ask_thread('', 'dashboard', exists=lambda t: False, mint=lambda: 'fresh1'), ('fresh1', 'minted'))
        self.assertEqual(TS.resolve_ask_thread(None, 'api', exists=lambda t: True, mint=lambda: 'fresh1'), (None, 'none'))
        self.assertEqual(TS.resolve_ask_thread('', 'golden', exists=lambda t: True, mint=lambda: 'fresh1'), (None, 'none'))
        self.assertEqual(TS.resolve_ask_thread('nope', 'api', exists=lambda t: False), (None, 'unknown'))

    def test_a_job_keeps_the_thread_the_launch_recorded(self):
        self.assertEqual(TS.job_thread(recorded=T_MAD, request='other', stashed='other2', active=lambda: T_NEW), T_MAD)
        self.assertEqual(TS.job_thread(request=T_MAD, stashed='other', active=lambda: T_NEW), T_MAD)
        self.assertEqual(TS.job_thread(stashed=T_MAD, active=lambda: T_NEW), T_MAD)
        self.assertEqual(TS.job_thread(active=lambda: T_NEW), T_NEW)                 # a web request that named none
        self.assertEqual(TS.job_thread(active=lambda: T_NEW, web=False), '')         # a worker never falls back to active

    def test_per_reader_records_answer_only_on_their_thread(self):
        rec = {'thread': T_MAD, 'subject': 'Mad Men'}
        self.assertTrue(TS.mine(rec))                       # outside an ask nothing is scoped: stores read as before
        with APP.test_request_context('/'):
            TS.set_current_thread(T_MAD)
            self.assertEqual(TS.scope(), (True, T_MAD))
            self.assertTrue(TS.mine(rec))
            self.assertFalse(TS.mine({'subject': 'no stamp'}))
        with APP.test_request_context('/'):
            TS.set_current_thread(T_NEW)
            self.assertFalse(TS.mine(rec))
            self.assertEqual(TS.only_mine([rec, {'thread': T_NEW, 'x': 1}]), [{'thread': T_NEW, 'x': 1}])
        with APP.test_request_context('/'):
            TS.set_current_thread('')                       # an ask scoped to no thread reads nothing
            self.assertEqual(TS.scope(), (True, ''))
            self.assertFalse(TS.mine(rec))
        self.assertFalse(TS.same('', ''))
        self.assertEqual(TS.stamp({'a': 1}, T_MAD), {'a': 1, 'thread': T_MAD})


# ============================================================================ 2
class TheAskStaysOnItsThread(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.w.leave_mad_men_state()

    def test_a_new_thread_asks_about_cruel_summer_and_nothing_is_mad_men(self):
        """The reader's case: a finished Mad Men thread, a new chat, a Cruel Summer build order."""
        tid = self.w.new_thread()
        env, st, g = self.w.ask(CRUEL_BUILD, thread_id=tid, history=[])
        self.assertEqual(st, 200)
        self.assertEqual(env.get('thread_id'), tid)
        self.assertEqual(g['thread_id'], tid)
        surface, text, body, seen = self.w.calls[-1]
        self.assertIsNone(seen['pin'])                       # the new thread arrives with no subject of its own
        self.assertEqual((g['pin'] or {}).get('subject'), 'Cruel Summer')   # and leaves with the one this ask drafted
        self.assertEqual((self.w.index()['threads'][0].get('pin') or {}).get('subject'), 'Mad Men')   # the Mad Men thread keeps its own
        self.assertEqual(surface, 'interpret', env.get('decision'))
        self.assertEqual(body.get('thread_id'), tid)
        self.assertEqual(body.get('history'), [])
        self.assertIn('cruel summer', text.lower())
        self.assertNotIn('mad men', json.dumps([text, body], default=str).lower())
        self.assertEqual(seen['referents'], [])              # no remembered subject from the earlier chat
        self.assertIsNone(seen['window'])                    # no remembered window
        self.assertEqual(seen['notes'], '')                  # no private note from the earlier chat
        self.assertIsNone(seen['binding'])
        s, subs, bound = _bind_in(tid, CRUEL_BUILD, [])
        self.assertEqual(s.source, 'words')
        self.assertIn('Cruel Summer', s.name)
        self.assertNotIn('Mad Men', s.name)
        self.assertEqual(subs, ['Cruel Summer'])             # the thread's only subject is the one it was asked about
        self.assertEqual(bound, 'Cruel Summer')

    def test_the_same_with_the_page_sitting_on_the_mad_men_profile(self):
        tid = self.w.new_thread()
        env, st, g = self.w.ask(CRUEL_Q, thread_id=tid, history=[], page=MAD_PAGE, surface='analyze')
        self.assertEqual(st, 200)
        self.assertEqual(g['thread_id'], tid)
        self.assertNotEqual((g['pin'] or {}).get('subject'), 'Mad Men')
        self.assertTrue(self.w.calls)
        for surface, text, body, seen in self.w.calls:
            self.assertIsNone(seen['pin'])
        s, subs, bound = _bind_in(tid, CRUEL_Q, [], 'Mad Men')
        self.assertEqual(s.source, 'words')
        self.assertIn('cruel summer', s.name.lower())
        self.assertNotIn('mad men', s.name.lower())
        self.assertEqual(subs, [])
        with APP.test_request_context('/'):
            S.stash_binding(USER, tid, strict=True)
            hit = SR.bind_or_continue(CRUEL_Q, MAD_PAGE, [])                # the open-screen question: the words bind
            self.assertEqual((hit or {}).get('route'), 'bind', hit)         # a subject that is not the page
            self.assertIn('cruel summer', str(hit.get('subject')).lower())
        for surface, text, body, seen in self.w.calls:
            self.assertEqual(body.get('thread_id'), tid)
            self.assertNotEqual(str(body.get('bind_subject') or '').lower(), 'mad men')
            self.assertEqual(seen['referents'], [])
        # a question that names nothing, in the new thread with the Mad Men page open, is never silently Mad Men
        # from the earlier chat: the thread has no subject, and only a pointer at the page binds the page
        s2, subs2, _ = _bind_in(tid, 'what is the age breakdown?', [], 'Mad Men')
        self.assertEqual((s2.kind, s2.source), ('none', 'none'))
        self.assertEqual(subs2, [])

    def test_a_thread_the_widget_names_as_left_behind_adds_nothing(self):
        """carried_from (2026-10-30) is ignored: the Mad Men turns never ride into the new thread."""
        tid = self.w.new_thread()
        env, st, g = self.w.ask(CRUEL_Q, thread_id=tid, history=[], surface='analyze', extra={'carried_from': T_MAD})
        self.assertEqual(st, 200)
        self.assertTrue(self.w.calls)
        for surface, text, body, seen in self.w.calls:
            self.assertEqual(body.get('history'), [], body.get('history'))
        # and a turn a stale client marks carried is dropped at the door
        stale = [dict(t, carried=True) for t in MAD_THREAD]
        env, st, g = self.w.ask(CRUEL_Q, thread_id=tid, history=stale, surface='analyze')
        for surface, text, body, seen in self.w.calls:
            self.assertEqual(body.get('history'), [])

    def test_an_explicit_switch_inside_one_thread_goes_to_the_named_subject(self):
        s, subs, bound = _bind_in(T_MAD, CRUEL_Q, MAD_THREAD, 'Mad Men')
        self.assertEqual(s.source, 'words')
        self.assertIn('cruel summer', s.name.lower())
        self.assertNotIn('mad men', s.name.lower())
        env, st, g = self.w.ask(CRUEL_Q, thread_id=T_MAD, history=MAD_THREAD, page=MAD_PAGE, surface='analyze')
        self.assertEqual(g['thread_id'], T_MAD)
        for surface, text, body, seen in self.w.calls:
            self.assertNotEqual(str(body.get('bind_subject') or '').lower(), 'mad men')
            self.assertIn('cruel summer', text.lower())

    def test_a_pronoun_follow_up_inside_the_thread_still_inherits(self):
        for q in ('and for women?', 'how about the men?', 'what about 18 to 34?'):
            s, subs, bound = _bind_in(T_MAD, q, MAD_THREAD)
            self.assertEqual((s.name, s.source), ('Mad Men', 'thread'), q)
            self.assertEqual(subs[0], 'Mad Men', q)
            self.assertEqual(bound, 'Mad Men', q)
        env, st, g = self.w.ask('and for women?', thread_id=T_MAD, history=MAD_THREAD, surface='analyze')
        self.assertEqual(g['thread_id'], T_MAD)
        self.assertEqual((g['pin'] or {}).get('subject'), 'Mad Men')
        surface, text, body, seen = self.w.calls[-1]
        self.assertEqual(body.get('thread_id'), T_MAD)
        self.assertEqual(len(body.get('history') or []), len(MAD_THREAD))
        # the thread's own memory is still its own
        self.assertEqual([r['subject'] for r in seen['referents']], ['Mad Men'])
        self.assertEqual(seen['window'], {'start': '2025-01-01', 'end': '2025-12-30'})
        self.assertIn('Mad Men', seen['notes'])

    def test_a_thread_the_index_lost_is_relisted_never_the_active_thread(self):
        """The dashboard names a thread the index does not list: before, the ask ran on the ACTIVE thread
        (here the Mad Men thread: its pin, its turns)."""
        env, st, g = self.w.ask(CRUEL_BUILD, thread_id='lost1234ab', history=[])
        self.assertEqual(st, 200)
        self.assertEqual(env.get('thread_id'), 'lost1234ab')
        self.assertEqual(g['thread_id'], 'lost1234ab')
        self.assertNotEqual((g['pin'] or {}).get('subject'), 'Mad Men')
        self.assertIsNone(self.w.calls[-1][3]['pin'])
        idx = self.w.index()
        self.assertIn('lost1234ab', [t['id'] for t in idx['threads']])
        self.assertEqual(idx['active'], T_MAD)                # the active pointer does not move
        surface, text, body, seen = self.w.calls[-1]
        self.assertEqual(body.get('thread_id'), 'lost1234ab')
        # a body still in the store keeps every turn when its entry is re-listed
        self.w.put(f'{THREADS}/{USER}/kept5678cd.json', [{'role': 'user', 'text': 'an earlier question'}, {'role': 'agent', 'text': 'its answer'}])
        S.relist_thread(USER, 'kept5678cd')
        self.assertEqual(len(self.w.get(f'{THREADS}/{USER}/kept5678cd.json')), 2)
        self.assertEqual(next(t for t in self.w.index()['threads'] if t['id'] == 'kept5678cd')['turns'], 2)

    def test_a_dashboard_ask_that_names_no_thread_never_runs_on_a_conversation(self):
        env, st, g = self.w.ask(CRUEL_BUILD, history=[])
        self.assertEqual(st, 200)
        self.assertNotEqual(g['thread_id'], T_MAD)
        self.assertTrue(g['thread_id'])
        self.assertNotEqual((g['pin'] or {}).get('subject'), 'Mad Men')
        self.assertIsNone(self.w.calls[-1][3]['pin'])
        first = g['thread_id']
        # the next ask with no thread named stays in that empty thread (one conversation, not one thread per ask)
        env, st, g = self.w.ask('viewers', history=[], surface='interpret')
        self.assertEqual(g['thread_id'], first)
        self.assertEqual(len(self.w.get(f'{THREADS}/{USER}/{T_MAD}.json')), len(MAD_THREAD))   # the Mad Men thread is untouched

    def test_a_probe_leaves_no_thread_behind(self):
        before = json.dumps(self.w.index(), sort_keys=True)
        self.w.calls.clear()
        with APP.test_request_context('/api/prometheus/v1/ask', method='POST', headers={'X-Prometheus-Caller': 'canary'}):
            env, st = S.ask(dict(ARMINE), {'text': CRUEL_BUILD, 'client': 'dashboard', 'history': []})
            g_tid, g_pin = getattr(flask.g, '_pm_thread_id', None), getattr(flask.g, '_pm_thread_pin', None)
            env2, st2 = S.ask(dict(ARMINE), {'text': CRUEL_BUILD, 'client': 'dashboard', 'history': [], 'thread_id': 'gone000001'})
        self.assertEqual((st, st2), (200, 200))
        self.assertIsNone(g_tid)                              # no thread: never the active Mad Men one, never a minted one
        self.assertIsNone(g_pin)
        self.assertEqual(json.dumps(self.w.index(), sort_keys=True), before)   # nothing minted, nothing re-listed, nothing pinned
        self.assertIsNone(self.w.get(f'{THREADS}/{USER}/gone000001.json'))

    def test_an_api_call_without_a_thread_starts_clean(self):
        env, st, g = self.w.ask(CRUEL_Q, client='api', history=[], surface='analyze')
        self.assertEqual(st, 200)
        self.assertIsNone(g['thread_id'])                    # no thread at all: never the dashboard's active one
        self.assertIsNone(g['pin'])
        self.assertTrue(self.w.calls)
        for surface, text, body, seen in self.w.calls:
            self.assertIsNone(body.get('thread_id'))
            self.assertEqual(body.get('history'), [])
            self.assertTrue(seen['scoped'])
            self.assertIsNone(seen['pin'])
            self.assertIsNone(seen['binding'])
            self.assertEqual(seen['referents'], [])
            self.assertIsNone(seen['window'])
            self.assertEqual(seen['notes'], '')
        # with no history sent either, the active thread's stored turns are not loaded in its place
        env, st, g = self.w.ask(CRUEL_Q, client='api', surface='analyze')
        for surface, text, body, seen in self.w.calls:
            self.assertEqual(body.get('history'), [])
        self.assertEqual(len(self.w.get(f'{THREADS}/{USER}/{T_MAD}.json')), len(MAD_THREAD))
        # a thread that is not the caller's is still refused
        env, st, g = self.w.ask(CRUEL_Q, client='api', thread_id='not-mine', history=[])
        self.assertEqual(st, 404)


# ============================================================================ 3
class NothingCrossesFromThreadAToThreadB(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.w.leave_mad_men_state()
        self.tid = self.w.new_thread()

    def test_an_offer_is_approved_only_on_the_thread_it_was_made_on(self):
        self.assertTrue(any(o['status'] == 'open' and o['thread_id'] == T_MAD for o in PO._load(USER)['offers']))
        for word in ('Approve', 'yes', 'go ahead', 'approved'):
            self.assertIsNone(PO.resolve_reply(USER, self.tid, word, []), word)
            self.assertIsNone(PO.find(USER, [], self.tid), word)
        self.assertFalse(PO.anything_open(USER, [], self.tid))
        # through the door: nothing runs, nothing is charged, the line says where the offer waits
        env, st, g = self.w.ask('Approve', thread_id=self.tid, history=[], surface='analyze')
        self.assertEqual(st, 200)
        self.assertFalse([c for c in self.w.calls if c[2].get('panel_confirm')], self.w.calls)
        self.assertTrue(str(env.get('text') or '').startswith('Nothing is waiting on an approval in this chat'), env.get('text'))
        self.assertIn('another chat', env.get('text'))
        self.assertNotIn('\u2014', env.get('text'))
        self.assertTrue(any(o['status'] == 'open' and o['thread_id'] == T_MAD for o in PO._load(USER)['offers']))
        # on the thread it was made on, the same word is the chip
        hist_a = MAD_THREAD + [{'role': 'user', 'text': OFFER_Q, 'ts': _iso(-60)}, dict(OFFER_TURN, ts=_iso(-50))]
        hit = PO.resolve_reply(USER, T_MAD, 'Approve', hist_a)
        self.assertEqual((hit or {}).get('action'), 'approve')
        self.assertEqual(hit['offer']['question'], OFFER_Q)
        self.assertEqual(hit['offer']['thread_id'], T_MAD)
        # an ask on no thread has no offers
        self.assertIsNone(PO.find(USER, [], None))
        self.assertEqual(PO.open_elsewhere(USER, self.tid)['thread_id'], T_MAD)
        self.assertIsNone(PO.open_elsewhere(USER, T_MAD))

    def test_a_stashed_brief_answers_a_run_order_only_on_its_thread(self):
        with APP.test_request_context('/'):
            TS.set_current_thread(self.tid)
            self.assertFalse(PI.waiting(self.w.s3, BUCKET, USER, 'Run the journey'))
            self.assertIsNone(PI.peek(self.w.s3, BUCKET, USER, 'digital_journey'))
            self.assertIsNone(PI.pick(self.w.s3, BUCKET, USER, 'digital_journey', 'Run the journey'))
            self.assertEqual(PI.active_flows(self.w.s3, BUCKET, USER), [])
            self.assertEqual(PI.active_flows(self.w.s3, BUCKET, USER, any_thread=True), ['digital_journey'])
        with APP.test_request_context('/'):
            TS.set_current_thread('')                        # an ask on no thread
            self.assertIsNone(PI.pick(self.w.s3, BUCKET, USER, 'digital_journey', 'yes'))
        with APP.test_request_context('/'):
            TS.set_current_thread(T_MAD)
            self.assertTrue(PI.waiting(self.w.s3, BUCKET, USER, 'Run the journey'))
            picked = PI.pick(self.w.s3, BUCKET, USER, 'digital_journey', 'Run the journey')
            self.assertEqual((picked or {}).get('subject'), 'Mad Men')

    def test_a_wizard_step_armed_on_thread_a_never_takes_thread_b(self):
        """The widget still sends its armed flag; thread B's last turn is not a wizard step."""
        from prometheus import bpiq_wizard as BW
        armed_a = [{'role': 'user', 'text': 'Value a brand partnership'},
                   {'role': 'agent', 'text': BW.ASK_TALENT if hasattr(BW, 'ASK_TALENT') else 'Who is the property or talent?',
                    'meta': {'bpiq_wizard': {'step': 'talent'}}}]
        env, st, g = self.w.ask(CRUEL_Q, thread_id=self.tid, history=[], surface='analyze', extra={'bpiq_inputs': True})
        self.assertEqual(st, 200)
        self.assertNotEqual((env.get('decision') or {}).get('reason'), 'bpiq_wizard')
        self.assertEqual(g['thread_id'], self.tid)
        self.assertTrue(armed_a)

    def test_memory_from_the_earlier_chat_is_not_read_in_the_new_one(self):
        with APP.test_request_context('/'):
            TS.set_current_thread(self.tid)
            self.assertEqual(PMM.recent_referents(USER, k=3), [])
            self.assertIsNone(PMM.last_window(USER))
            self.assertIsNone(PMM.last_region(USER))
            self.assertEqual(SM.recall_notes(USER), [])
            # what this thread remembers is stamped with this thread
            PMM.remember(USER, CRUEL_Q, subject='Cruel Summer Viewers', route='generated')
            PMM._read_cache.clear()
            self.assertEqual([r['subject'] for r in PMM.recent_referents(USER, k=3)], ['Cruel Summer Viewers'])
        with APP.test_request_context('/'):
            TS.set_current_thread(T_MAD)
            self.assertEqual([r['subject'] for r in PMM.recent_referents(USER, k=3)], ['Mad Men'])
            self.assertEqual(PMM.last_region(USER), 'Los Angeles')
        # every record carries its thread; the replay guard and the reports still read them all
        self.assertEqual({a.get('thread') for a in PMM.recall(USER)}, {T_MAD, self.tid})

    def test_the_about_this_user_block_carries_no_subject_and_no_window(self):
        blk = PMM.user_block({'display_name': 'Armine', 'role': 'analyst', 'company': 'Lionsgate', 'views': ['profileIQ'],
                              'recent_subjects': ['Mad Men', 'Cruel Summer'], 'last_window': {'start': '2025-01-01', 'end': '2025-12-30'}})
        self.assertIn('Armine analyst at Lionsgate.', blk)
        self.assertNotIn('Mad Men', blk)
        self.assertNotIn('Recent subjects', blk)
        self.assertNotIn('2025-01-01', blk)
        src = Path(ROOT, 'prometheus_memory.py').read_text(encoding='utf-8')
        prof = src[src.index('def user_profile('):src.index('def user_block(')]
        self.assertNotIn("out['recent_subjects']", prof)
        self.assertNotIn("out['last_window']", prof)
        # a standing preference is the reader's rule and stays with the reader in every thread
        self.w.memory['preferences'] = [{'text': 'Always give me a CSV.', 'ts': time.time()}]
        with APP.test_request_context('/'):
            TS.set_current_thread(self.tid)
            self.assertIn('Always give me a CSV.', SM.notes_block(USER))
            self.assertNotIn('Mad Men', SM.notes_block(USER))

    def test_the_page_confirm_and_the_last_file_stay_with_their_thread(self):
        watch = Path(ROOT, 'prometheus', 'legacy', 'watch.py').read_text(encoding='utf-8')
        m = re.search(r"(^def _pm_page_confirm_slot\(.*?)(?=\n\n\n)", watch, re.S | re.M)
        self.assertIsNotNone(m)
        ns = {'_H': types.SimpleNamespace(_normalize_for_match=lambda s: ' '.join(str(s).lower().split()))}
        exec(m.group(1), ns)
        self.assertEqual(ns['_pm_page_confirm_slot']('Mad Men'), 'mad men')             # outside an ask: as before
        with APP.test_request_context('/'):
            TS.set_current_thread(T_MAD)
            self.assertEqual(ns['_pm_page_confirm_slot']('Mad Men'), 'mad men@' + T_MAD)
        with APP.test_request_context('/'):
            TS.set_current_thread(self.tid)
            self.assertEqual(ns['_pm_page_confirm_slot']('Mad Men'), 'mad men@' + self.tid)
        with APP.test_request_context('/'):
            TS.set_current_thread('')
            self.assertEqual(ns['_pm_page_confirm_slot']('Mad Men'), '')                # no thread: no mark kept or read
        jobs = Path(ROOT, 'prometheus', 'legacy', 'jobs.py').read_text(encoding='utf-8')
        code = jobs[jobs.index('def _pm_file_stash_write('):jobs.index("@_H.app.route('/api/brief-chat/active-runs'")]
        s3 = FakeS3()
        ns2 = {'_H': types.SimpleNamespace(s3_client=s3, S3_BUCKET=BUCKET), 'json': json, 'time': time,
               'traceback': __import__('traceback'), '_PM_LAST_FILE_PREFIX': 'system/usage/pm_last_file/'}
        exec(code, ns2)
        with APP.test_request_context('/'):
            TS.set_current_thread(T_MAD)
            ns2['_pm_file_stash_write'](USER, 'https://x/mad.csv', 'Mad Men by period.csv', 'generated_data/mad.csv', 'Mad Men', MAD_Q)
            self.assertEqual(ns2['_pm_file_stash_read'](USER).get('filename'), 'Mad Men by period.csv')
        with APP.test_request_context('/'):
            TS.set_current_thread(self.tid)
            self.assertEqual(ns2['_pm_file_stash_read'](USER), {})                     # "email me this file" in the new chat is never that one


# ============================================================================ 3b
LAS_PAGE = {'view': 'profile-iq', 'primary': {'s3_key': 'Las_Culturistas_Culture_Awards_Viewers_11_10_2026_23_25.csv',
                                               'name': 'Las Culturistas Culture Awards Viewers'},
            'view_context': {'view_id': 'profileIQ', 'view_title': 'Las Culturistas Culture Awards Viewers', 'data': {}}}
DIGITAL_Q = ('Looking at the digital original content such as YouTube, TikTok, etc. What type of categories do best for digital '
             'original content: Comedy, lifestyle/arts & culture, Faith, Young Adult, and Horror do in this space.? Can we give some '
             'examples?  how is the audience like? Can we summarize this in a powerpoint?')
YT_BANKED_Q = ('Please give an overview of Teen parents using youtube platform not using tiktok platform. And then show me what '
               'types of content they are most interested in viewing on youtube')
YT_ENTRY = {
    'k': 'youtube|audience|2025-01-01|2025-12-31', 'subject': 'YouTube', 'family': 'audience', 'q': YT_BANKED_Q,
    'qn': ('please give an overview of teen parents using youtube platform not using tiktok platform and then show me what types of '
           'content they are most interested in viewing on youtube'),
    'route': 'reasoned_metrics', 'ts': '2026-10-23T17:59:13Z', 'ws': '2025-01-01', 'we': '2025-12-31', 'wl': 'Jan 1 2025 to Dec 31 2025',
    'base': 'YouTube_06_16_2026_16_53.csv', 'cohort': 'Parents of teens 13-17, YouTube users with no TikTok use', 'prov': 'operator',
    'reply': ('On Parents of teens 13-17, YouTube users with no TikTok use:\n\nParents of teens who use YouTube and carry no TikTok '
              'activity number 8,412,679 people, 9.6% of the 87,959,093 US YouTube users who stay off TikTok entirely.\n\n'
              "YouTube content type mix, Jan 1 2025 to Dec 31 2025 (share of the cohort's YouTube viewing signals in the window)\n"
              '- Gaming and gameplay: 14.3% share, 83.6% penetration. Steam, EA Sports and NBA 2K channels lead\n'
              '- Technology, reviews and how-to hardware: 12.7% share, 80.4% penetration. device reviews and setup walkthroughs lead\n'
              '- Sports and highlights: 11.6% share, 49.8% penetration. CBS Sports, Yahoo Sports and NBC Sports clips lead\n'
              '- Music and live performance: 10.4% share, 46.2% penetration. rap and hip hop is the strongest single genre\n'
              '- News and politics: 9.8% share, 45.2% penetration. Google News, NBC News and Fox News uploads lead\n'
              '- Learning, tutorials and digital reading: 8.9% share, 45.4% penetration. Khan Academy and homework-help channels lead\n'
              '- Fitness and strength training: 7.3% share, 46.0% penetration. strength and home-gym programming leads\n'
              '- Movies, TV and trailers: 6.7% share, 41.3% penetration. studio trailer channels and recap uploads lead\n'
              '- Comedy and entertainment: 2.7% share, 15.4% penetration. stand-up sets and sketch uploads lead'),
    'metrics': [{'name': 'cohort_projected_us_people', 'label': 'Teen parents on YouTube, no TikTok use (projected US people)',
                 'value': 8412679, 'unit': 'people'}],
    'breakdown': {'dimension': 'YouTube content type', 'share_basis': "share of the cohort's YouTube viewing signals in the window",
                  'rows': [{'label': 'Gaming and gameplay', 'share_pct': 14.3, 'penetration_pct': 83.6},
                           {'label': 'Technology, reviews and how-to hardware', 'share_pct': 12.7, 'penetration_pct': 80.4}]}}


class TheLedgerNeverAnswersForAnotherAudience(unittest.TestCase):
    """2026-11-11 01:26 UTC, her thread 676c09e4d1: "Looking at the digital original content such as YouTube, TikTok,
    etc. What type of categories do best?" was answered "On Parents of teens 13-17, YouTube users with no TikTok use",
    a read banked nineteen days earlier from another reader's question, because 'YouTube' appears in her words."""

    def setUp(self):
        import insights_ledger as il
        self.il = il
        self.doc = {'subjects': {
            'youtube': {'subject': 'youtube', 'entries': [dict(YT_ENTRY)]},
            'tiktok': {'subject': 'TikTok', 'entries': [{'k': 'tiktok|audience||', 'subject': 'TikTok', 'family': 'audience',
                                                         'q': 'how big is the TikTok audience', 'qn': 'how big is the tiktok audience',
                                                         'route': 'reasoned_metrics', 'ts': '2026-10-01T00:00:00Z',
                                                         'reply': 'On TikTok: 202,574,667 US people used TikTok in the window.'}]}}}
        self._saved = il._load_index
        il._load_index = lambda force=False: self.doc

    def tearDown(self):
        self.il._load_index = self._saved

    def test_a_question_that_names_two_subjects_is_not_answered_by_one_subjects_read(self):
        q = 'Looking at the digital original content such as YouTube, TikTok, etc. What type of categories do best?'
        self.assertEqual(self.il.other_subjects_named(self.doc, q, 'youtube'), ['TikTok'])
        for subj in (None, 'YouTube'):
            r = self.il.consult(subject=subj, question=q)
            self.assertEqual(r.get('skey'), 'youtube')
            self.assertIsNone(r.get('exact'), (subj, (r.get('exact') or {}).get('cohort')))
            self.assertIsNone(r.get('match'))
            self.assertTrue(r.get('entries'))                 # the published figures still ride the block

    def test_the_guard_is_what_stopped_it(self):
        # the same ledger without a TikTok subject: the read is matched by meaning (the behavior the guard narrows)
        doc = {'subjects': {'youtube': self.doc['subjects']['youtube']}}
        self.il._load_index = lambda force=False: doc
        r = self.il.consult(subject=None, question='Looking at the digital original content such as YouTube, etc. What type of categories do best?')
        self.assertEqual((r.get('exact') or {}).get('cohort'), YT_ENTRY['cohort'])
        self.assertEqual(r.get('match'), 'semantic')

    def test_the_same_question_still_replays(self):
        r = self.il.consult(subject=None, question=YT_BANKED_Q)       # it names YouTube and TikTok too; it is the same question
        self.assertEqual(r.get('match'), 'exact')
        self.assertEqual((r.get('exact') or {}).get('cohort'), YT_ENTRY['cohort'])

    def test_one_subject_under_a_longer_name_is_not_two(self):
        doc = {'subjects': {'paw_patrol': {'subject': 'Paw Patrol', 'entries': [{'reply': 'x', 'q': 'q', 'qn': 'q'}]},
                            'paw_patrol_series': {'subject': 'Paw Patrol Series', 'entries': [{'reply': 'x', 'q': 'q', 'qn': 'q'}]},
                            'retired_one': {'subject': 'Bluey', 'entries': [{'reply': 'x', 'retired': True}]}}}
        self.assertEqual(self.il.other_subjects_named(doc, 'top toy categories for paw patrol series viewers', 'paw_patrol_series'), [])
        self.assertEqual(self.il.other_subjects_named(doc, 'paw patrol viewers who also watch bluey', 'paw_patrol'), [])   # every Bluey read is retired


class APointerInsideOneMessageIsNeverThePage(unittest.TestCase):
    """2026-11-11 01:35 UTC, her thread 9f6746da26, the Las Culturistas profile open on the page: the last question of
    one message, "Can we summarize this in a powerpoint?", ran on its own, bound to the page, and a read about a Las
    Culturistas deck landed in a thread about digital original content."""

    def setUp(self):
        self.w = World()
        self.tid = self.w.new_thread()

    def test_points_back(self):
        from prometheus import multi_ask as MA
        for q in ('Can we summarize this in a powerpoint?', 'Can we give some examples?', 'how is the audience like?'):
            self.assertTrue(MA.points_back(q), q)
        self.assertFalse(MA.points_back(CRUEL_Q))
        self.assertFalse(MA.same_subject('Las Culturistas Culture Awards Viewers', 'Digital original content categories'))
        self.assertTrue(MA.same_subject('Mad Men', 'Mad Men - Avid Fan'))

    def test_the_last_question_of_her_message_runs_on_the_messages_subject(self):
        env, st, g = self.w.ask(DIGITAL_Q, thread_id=self.tid, history=[], page=LAS_PAGE, surface='analyze')
        self.assertEqual(st, 200)
        self.assertEqual((env.get('decision') or {}).get('reason'), 'multi_ask', env.get('decision'))
        calls = {text: (surface, body) for surface, text, body, seen in self.w.calls}
        first = next(t for t in calls if t.startswith('Looking at the digital original content'))
        self.assertTrue(calls[first][1].get('page_context'))                 # the first question keeps the page as context
        tail = [t for t in calls if t != first]
        self.assertTrue(tail, list(calls))
        for t in tail:
            surface, body = calls[t]
            self.assertFalse(body.get('page_context'), (t, body.get('page_context')))      # never the Las Culturistas page
            self.assertNotIn('Las Culturistas', json.dumps(body, default=str), t)
            if surface == 'analyze':
                self.assertEqual(body.get('bind_subject'), 'Digital original content categories', t)
        self.assertIn('Can we summarize this in a powerpoint?', calls)

    def test_a_message_about_the_page_keeps_the_page(self):
        page = {'view': 'profile-iq', 'primary': {'s3_key': 'Digital.csv', 'name': 'Digital original content categories'},
                'view_context': {'view_id': 'profileIQ', 'view_title': 'Digital original content categories', 'data': {}}}
        env, st, g = self.w.ask(DIGITAL_Q, thread_id=self.tid, history=[], page=page, surface='analyze')
        self.assertEqual(st, 200)
        for surface, text, body, seen in self.w.calls:
            if surface == 'analyze':
                self.assertTrue(body.get('page_context'), text)


# ============================================================================ 4
def _legacy_fn(name):
    src = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')
    m = re.search(r'(^def %s\(.*?)(?=\n\n\n)' % re.escape(name), src, re.S | re.M)
    assert m, name
    return m.group(1)


class JobKeepsItsThread(unittest.TestCase):
    """Her Mad Men read: asked on 07feee4242, job document thread bdacfec6e6 (the thread she had opened while it ran)."""

    def _binder(self, *, req_tid='', g_tid=None, active=T_NEW, doc_tid=None):
        local = types.SimpleNamespace(tid=req_tid)
        ns = {'_PM_REQ_THREAD': local, '_PM_JOB_THREAD': {},
              '_load_threads_index': lambda u: {'active': active, 'threads': [{'id': T_MAD}, {'id': T_NEW}]},
              '_pm_read_job_doc': lambda jid: ({'job_id': jid, 'thread_id': doc_tid} if doc_tid is not None else None),
              '__builtins__': __builtins__}
        exec(_legacy_fn('_pm_job_bind_thread'), ns)

        def run(resumed=False):
            with APP.test_request_context('/'):
                if g_tid is not None:
                    flask.g._pm_thread_id = g_tid
                return ns['_pm_job_bind_thread']('9248ff753809', USER, resumed=resumed) if resumed else ns['_pm_job_bind_thread']('9248ff753809', USER)
        return run, ns

    def test_the_worker_keeps_the_thread_the_launch_recorded(self):
        # the launch wrote 07feee4242; the reader has since opened bdacfec6e6 (now active); the worker's own
        # per-thread marker still holds whatever it ran last
        run, ns = self._binder(req_tid='stale-from-another-job', active=T_NEW, doc_tid=T_MAD)
        self.assertEqual(run(resumed=True), T_MAD)
        self.assertEqual(ns['_PM_JOB_THREAD'], {'9248ff753809': T_MAD})

    def test_a_worker_with_no_recorded_thread_never_guesses_the_active_one(self):
        run, ns = self._binder(req_tid='stale-from-another-job', active=T_NEW, doc_tid='')
        self.assertIsNone(run(resumed=True))

    def test_the_launch_takes_the_request_thread_then_the_stashed_one(self):
        run, _ = self._binder(req_tid=T_MAD, g_tid='other', active=T_NEW)
        self.assertEqual(run(), T_MAD)
        run, _ = self._binder(req_tid='', g_tid=T_MAD, active=T_NEW)
        self.assertEqual(run(), T_MAD)
        run, _ = self._binder(req_tid='', g_tid=None, active=T_NEW)        # a web request that named none
        self.assertEqual(run(), T_NEW)

    def test_the_wiring(self):
        gen = _legacy_fn('_pm_generate_metrics_response')
        self.assertIn("_pm_job_bind_thread(job_id, _pm_user, resumed=True)", gen)
        self.assertIn('if not inline_job_id else', gen)
        rr = Path(ROOT, 'prometheus', 'read_recovery.py').read_text(encoding='utf-8')
        run_inline = rr[rr.index('def run_inline('):rr.index('def _status_doc(')]
        self.assertIn("_tscope.set_current_thread(status.get('thread_id'))", run_inline)
        self.assertLess(run_inline.index('_tscope.set_current_thread'), run_inline.index('c._pm_generate_metrics_response('))

    def test_the_per_worker_marker_follows_each_ask(self):
        """The legacy marker was written by the analyze and deck cores only and never cleared: a build ask served
        next on the same worker thread read the previous request's thread."""
        mod = types.ModuleType('prometheus.legacy.chat')
        mod._PM_REQ_THREAD = types.SimpleNamespace(tid='left-by-the-last-request')
        saved = sys.modules.get('prometheus.legacy.chat')
        sys.modules['prometheus.legacy.chat'] = mod
        try:
            w = World()
            tid = w.new_thread()
            w.ask(CRUEL_BUILD, thread_id=tid, history=[])
            self.assertEqual(mod._PM_REQ_THREAD.tid, tid)
            w.ask(CRUEL_Q, client='api', history=[], surface='analyze')
            self.assertEqual(mod._PM_REQ_THREAD.tid, '')
        finally:
            if saved is not None:
                sys.modules['prometheus.legacy.chat'] = saved
            else:
                sys.modules.pop('prometheus.legacy.chat', None)


# ============================================================================ 5
class ThePaneNeverDropsIntoAnOldChat(unittest.TestCase):
    def setUp(self):
        self.w = World()

    def test_leaving_every_project_goes_fresh_when_the_general_chat_is_stale(self):
        idx = self.w.index()
        idx['threads'][0]['updated'] = _iso(-3600)
        idx['threads'][0]['last_seen'] = _iso(-3600)
        self.w.put(f'{THREADS}/{USER}/index.json', idx)
        out = S.thread_for_subject(USER, '', '', '')
        self.assertTrue(out.get('fresh'))
        self.assertIsNone(out.get('tid'))
        self.assertEqual(out.get('history'), [])
        self.assertEqual(self.w.index()['active'], T_MAD)       # nothing moved, nothing created
        self.assertEqual(len(self.w.index()['threads']), 1)

    def test_a_general_chat_used_minutes_ago_is_resumed(self):
        idx = self.w.index()
        idx['threads'].append({'id': 'proj000001', 'title': 'Cruel Summer Viewers', 'created': _iso(-100), 'updated': _iso(-100), 'turns': 2,
                               'binding': {'subject_key': 'profile:Cruel.csv', 'subject_label': 'Cruel Summer Viewers', 'product': 'profileIQ'}})
        idx['active'] = 'proj000001'
        self.w.put(f'{THREADS}/{USER}/index.json', idx)
        out = S.thread_for_subject(USER, '', '', '')
        self.assertFalse(out.get('fresh'))
        self.assertEqual(out.get('tid'), T_MAD)
        self.assertTrue(out.get('switched'))
        self.assertEqual(len(out.get('history')), len(MAD_THREAD))

    def test_a_project_thread_still_follows_its_project(self):
        out = S.thread_for_subject(USER, 'profile:Cruel_Summer_Viewers_11_10_2026_22_13.csv', 'Cruel Summer Viewers', 'profileIQ')
        self.assertTrue(out.get('created') and out.get('switched'))
        self.assertEqual(out['binding']['subject_label'], 'Cruel Summer Viewers')
        self.assertEqual(out.get('history'), [])                 # a thread the page opened starts with nothing

    def test_a_reopen_on_an_unlisted_thread_is_not_handed_the_active_one(self):
        chat = Path(ROOT, 'prometheus', 'legacy', 'chat.py').read_text(encoding='utf-8')
        hist = chat[chat.index('def api_synth_chat_history('):]
        hist = hist[:hist.index('\n\n\n')]
        self.assertIn("'unlisted': True", hist)
        self.assertNotIn("want = ''   # a tab opening on a thread the rail retired decides on the active one", hist)
        self.assertLess(hist.index("'unlisted': True"), hist.index('fresh, why = _tp.fresh_decision'))


# ============================================================================ 6
NODE = shutil.which('node')


class Widget(unittest.TestCase):
    """The spliced widget functions, run in Node against stubs."""

    @classmethod
    def setUpClass(cls):
        cls.html = Path(ROOT, 'templates', 'index.html').read_text(encoding='utf-8')

    def _fn(self, name):
        for head in ('        async function ' + name + '(', '        function ' + name + '('):
            if head in self.html:
                i = self.html.index(head)
                j = self.html.index('\n        }\n', i) + len('\n        }\n')
                return self.html[i:j]
        raise AssertionError('missing ' + name)

    def _origin_block(self):
        i = self.html.index('        var _pmAskOrigin = null;      // the foreground ask: { tid, hist, q }')
        j = self.html.index('        function synthChatPushTurn(role, text) {')
        return self.html[i:j] + self._fn('synthChatPushTurn') + self._fn('_synthChatPushTurnWithMeta')

    def _run(self, lines):
        if not NODE:
            self.skipTest('node not installed')
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False) as fh:
            fh.write('\n'.join(lines))
            path = fh.name
        try:
            out = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(path)
        self.assertEqual(out.returncode, 0, out.stderr[-3000:])
        return json.loads(out.stdout.strip().splitlines()[-1])

    STUBS = [
        "var posts = [], renders = 0, saves = 0, released = 0, shownBrief = 0, switched = [];",
        "var panel = { style: { display: 'none' } };",
        "var box = { children: [], scrollTop: 0, scrollHeight: 10, appendChild: function (n) { this.children.push(n); } };",
        "var sub = { textContent: '', title: '' };",
        "var document = { getElementById: function (id) { return id === 'synthChatBriefPreview' ? panel : (id === 'synthChatMessages' ? box : (id === 'prometheusSub' ? sub : null)); },",
        "                 createElement: function () { return { style: {}, attrs: {}, setAttribute: function (k, v) { this.attrs[k] = v; } }; } };",
        "var window = {};",
        "var synthChatHistory = [], synthChatCurrentDraft = null, synthChatBatchDrafts = null;",
        "var _pmThreadsIdx = { active: 'A', threads: [{ id: 'A', title: 'Run a profile for cruel summer series', turns: 2 }, { id: 'B', title: 'For Mad Men series viewers', turns: 3 }] };",
        "var _pmPendingNew = false, _pmHistoryRev = null, _pmSearchQ = '';",
        "var _PM_FOLLOW_SUB_DEFAULT = 'Ask any read across your workspace.';",
        "function _synthChatAgentText(role, text) { return text; }",
        "function _pmTurnId() { return 't' + Math.random().toString(36).slice(2, 8); }",
        "function _synthChatBumpActivity() {}",
        "function synthChatRerender() { renders++; }",
        "function _pmHistorySave() { saves++; return Promise.resolve(null); }",
        "function _pmThreadsLoad() {} function _pmThreadsRender() {}",
        "function _pmReleaseArmedSteps() { released++; }",
        "function _pmResetArmedStates() { _pmReleaseArmedSteps(); }",
        "function _synthChatShowBrief(d) { shownBrief++; panel.style.display = 'block'; }",
        "function pmSwitchThread(tid) { switched.push(tid); }",
        "function _pmThreadBindingLabel() { var th = (_pmThreadsIdx.threads || []).filter(function (t) { return t.id === _pmThreadsIdx.active; })[0]; return (th && th.binding) ? th.binding.subject_label : ''; }",
        "function fetch(url, opts) { posts.push({ url: url, body: opts && opts.body ? JSON.parse(opts.body) : null }); return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ success: true }); } }); }",
    ]

    def _base(self):
        return self.STUBS + [self._origin_block(), self._fn('_pmThreadHeaderRefresh'), self._fn('_pmThreadNameHere'),
                             self._fn('_pmAdoptHistory')]

    def test_a_reply_that_arrives_after_new_chat_goes_to_the_thread_that_asked(self):
        """Her 'viewers' and 'all seasons' replies landed in fresh panes that became threads of their own."""
        got = self._run(self._base() + [
            "var A = [{ role: 'user', text: 'Run a profile for cruel summer series', turn_id: 'u1' }];",
            "synthChatHistory = A; _pmPaneTid = 'A';",
            "var own = { tid: '', hist: null, q: '' }; _pmAskOrigin = own; _pmAskOriginPin('Run a profile for cruel summer series');",
            "/* the reader clicks New chat while the ask is in flight: an empty pane, no thread */",
            "_pmPendingNew = true; _pmThreadsIdx.active = null; _pmAdoptHistory([]);",
            "/* the reply arrives, with a brief and an armed clarify step */",
            "synthChatCurrentDraft = { subject: 'Cruel Summer' }; panel.style.display = 'block';",
            "_synthChatPushTurnWithMeta('agent', 'New Cruel Summer profile, ready to run.', { options: [{ label: 'Viewers only', send: 'viewers' }] });",
            "var before = released; _pmAskOriginEnd(own);",
            "console.log(JSON.stringify({ pane: synthChatHistory.length, A: A.length, posts: posts, notes: box.children.map(function (n) { return { text: n.textContent, tid: n.attrs['data-pm-elsewhere'] }; }),",
            "  released: released - before, draftHere: synthChatCurrentDraft, parked: Object.keys(_pmDraftsByThread), panel: panel.style.display, origin: _pmAskOrigin, saves: saves }));",
        ])
        self.assertEqual(got['pane'], 0)                                   # nothing was written into the new chat
        self.assertEqual(got['A'], 2)                                      # the asking thread's own turns took it
        self.assertEqual(len(got['posts']), 1)
        self.assertEqual(got['posts'][0]['url'], '/api/brief-chat/history')
        self.assertEqual(got['posts'][0]['body']['thread_id'], 'A')
        self.assertEqual(got['posts'][0]['body']['history'][-1]['text'], 'New Cruel Summer profile, ready to run.')
        self.assertEqual(got['saves'], 0)                                  # the pane's own save never ran for it
        self.assertEqual(len(got['notes']), 1)
        self.assertEqual(got['notes'][0]['tid'], 'A')
        self.assertIn('is in the chat it was asked in', got['notes'][0]['text'])
        self.assertIn('Run a profile for cruel summer series', got['notes'][0]['text'])
        self.assertGreaterEqual(got['released'], 1)                        # nothing the reply armed stays armed here
        self.assertIsNone(got['draftHere'])                                # the brief is not in the new chat
        self.assertEqual(got['parked'], ['A'])                             # it waits on the thread that asked
        self.assertEqual(got['panel'], 'none')
        self.assertIsNone(got['origin'])

    def test_a_reply_that_arrives_while_another_thread_is_open_never_lands_there(self):
        """The Mad Men 'On it' line landed in the Cruel Summer thread she had opened while it ran."""
        got = self._run(self._base() + [
            "var MAD = [{ role: 'user', text: 'For Mad Men series viewers, can we compare', turn_id: 'm1' }];",
            "var CRUEL = [{ role: 'user', text: 'Run a profile for cruel summer series', turn_id: 'c1' }, { role: 'agent', text: 'New Cruel Summer profile, ready to run.', turn_id: 'c2' }];",
            "_pmThreadsIdx.active = 'B'; synthChatHistory = MAD; _pmPaneTid = 'B';",
            "var own = { tid: '', hist: null, q: '' }; _pmAskOrigin = own; _pmAskOriginPin('For Mad Men series viewers, can we compare');",
            "_pmThreadsIdx.active = 'A'; _pmAdoptHistory(CRUEL);     /* she opens the Cruel Summer thread from the rail */",
            "synthChatPushTurn('agent', 'On it: reading Mad Men now.');",
            "_pmAskOriginEnd(own);",
            "/* a stale client flag never pushes into the pane afterwards */",
            "synthChatPushTurn('user', 'all seasons');",
            "console.log(JSON.stringify({ cruel: CRUEL.map(function (t) { return t.text; }), mad: MAD.map(function (t) { return t.text; }), post: posts[0].body, npost: posts.length, hist: _pmAskHist() === synthChatHistory }));",
        ])
        self.assertNotIn('On it: reading Mad Men now.', got['cruel'][:2])
        self.assertEqual(got['cruel'], ['Run a profile for cruel summer series', 'New Cruel Summer profile, ready to run.', 'all seasons'])
        self.assertEqual(got['mad'], ['For Mad Men series viewers, can we compare', 'On it: reading Mad Men now.'])
        self.assertEqual(got['post']['thread_id'], 'B')
        self.assertEqual(got['npost'], 1)
        self.assertTrue(got['hist'])

    def test_every_request_of_an_ask_names_the_thread_it_started_on(self):
        got = self._run(self.STUBS + [
            self._origin_block(), "var _PM_V1_SURFACES = { '/api/brief-chat/analyze': 'analyze' };", self._fn('_pmV1Request'),
            "var MAD = [{ role: 'user', text: 'q1' }, { role: 'user', text: 'For Mad Men series viewers' }];",
            "_pmThreadsIdx.active = 'B'; synthChatHistory = MAD;",
            "var own = { tid: '', hist: null, q: '' }; _pmAskOrigin = own; _pmAskOriginPin('For Mad Men series viewers');",
            "var first = _pmV1Request('/api/brief-chat/analyze', { text: 'x', history: _pmAskHist().slice(0, -1) }).body;",
            "_pmThreadsIdx.active = 'A'; synthChatHistory = [{ role: 'user', text: 'cruel 1' }, { role: 'agent', text: 'cruel 2' }, { role: 'user', text: 'cruel 3' }];",
            "var later = _pmV1Request('/api/brief-chat/analyze', { text: 'x', history: _pmAskHist().slice(0, -1) }).body;",
            "_pmAskOriginEnd(own);",
            "var after = _pmV1Request('/api/brief-chat/analyze', { text: 'y', history: _pmAskHist().slice(0, -1) }).body;",
            "console.log(JSON.stringify({ first: first, later: later, after: after }));",
        ])
        self.assertEqual(got['first']['thread_id'], 'B')
        self.assertEqual(got['later']['thread_id'], 'B')                   # the pane moved; the ask did not
        self.assertEqual([t['text'] for t in got['later']['history']], ['q1'])   # and it sends its own thread's turns
        self.assertEqual(got['after']['thread_id'], 'A')
        for b in (got['first'], got['later'], got['after']):
            self.assertNotIn('carried_from', b)
            self.assertEqual(b['client'], 'dashboard')
        self.assertNotIn('carried_from', self.html)
        self.assertNotIn('_pmCarried', self.html)

    def test_a_finished_read_lands_in_the_thread_that_asked_for_it(self):
        """The Cruel Summer read was written under the Mad Men question; a resumed poll (the list is shared by
        every tab) wrote into whatever thread that tab was showing."""
        poll = self.html[self.html.index('        async function _pmPollReadJob(jobId, originTid) {'):self.html.index('        async function _pmRefreshLandedRead(')]
        self.assertIn("var _po = originTid ? { tid: String(originTid), hist: null, q: '' } : _pmOriginNow();", poll)
        self.assertIn("_pmTrackReadJob(jobId, _po && _po.tid);", poll)
        self.assertIn("_pmPushMetaFor(_po, 'agent', p.reply, _rjMeta);", poll)
        self.assertNotIn("_synthChatPushTurnWithMeta(", poll)
        self.assertNotIn("            synthChatPushTurn(", poll)
        self.assertIn("try { _pmPollReadJob(j.id, j.tid || ''); } catch (_) {}", self.html)
        got = self._run(self._base() + [
            "var store = {}; var localStorage = { getItem: function (k) { return store[k] || null; }, setItem: function (k, v) { store[k] = v; } };",
            self._fn('_pmReadJobsLoad'), self._fn('_pmReadJobsSave'), self._fn('_pmTrackReadJob'),
            "var CRUEL = [{ role: 'user', text: 'Cruel Summer series was in 2021', turn_id: 'c1' }];",
            "_pmThreadsIdx.active = 'A'; synthChatHistory = CRUEL; _pmPaneTid = 'A';",
            "var po = _pmOriginNow(); _pmTrackReadJob('dcdeb527973b', po.tid);",
            "/* she starts the Mad Men chat; the Cruel Summer read finishes while it is on screen */",
            "var MAD = [{ role: 'user', text: 'For Mad Men series viewers', turn_id: 'm1' }];",
            "_pmThreadsIdx.active = 'B'; _pmAdoptHistory(MAD);",
            "_pmPushMetaFor(po, 'agent', 'Correct: the series ran two seasons.', { read_job_id: 'dcdeb527973b' });",
            "/* a poll resumed after a reload names the thread from the stored entry */",
            "var resumed = { tid: JSON.parse(store.pmReadJobs)[0].tid, hist: null, q: '' };",
            "_pmPushMetaFor(resumed, 'agent', 'Correct: the series ran two seasons.', { read_job_id: 'dcdeb527973b' });",
            "console.log(JSON.stringify({ mad: MAD.length, cruel: CRUEL.length, posts: posts.map(function (p) { return { tid: p.body.thread_id, job: p.body.history[p.body.history.length - 1].meta.read_job_id }; }), stored: JSON.parse(store.pmReadJobs), notes: box.children.length }));",
        ])
        self.assertEqual(got['mad'], 1)                                    # nothing under the Mad Men question
        self.assertEqual(got['cruel'], 2)
        self.assertEqual(got['posts'], [{'tid': 'A', 'job': 'dcdeb527973b'}, {'tid': 'A', 'job': 'dcdeb527973b'}])
        self.assertEqual(got['stored'][0]['tid'], 'A')
        self.assertEqual(got['notes'], 1)                                  # one line per thread, not one per push

    def test_every_poller_carries_its_thread(self):
        for name in ('_pmPollFwJob', '_pmPollAiqJob', '_pmPollTwinJob', '_pmPollJiqJob', '_pmPollBpiqJob'):
            src = self._fn(name)
            self.assertIn('var _po = _pmOriginNow();', src, name)
            self.assertNotIn('            synthChatPushTurn(', src, name)
            self.assertNotIn(' synthChatPushTurn(', src, name)
            self.assertNotIn('_synthChatPushTurnWithMeta(', src, name)
            self.assertIn('_pmPushTurnFor(_po, ', src, name)
        build = self.html[self.html.index('        function _synthChatStartPolling(runId, subjectHint, knownStatus) {'):
                          self.html.index('        // Custom Analysis Profile IQ now uses original formView')]
        self.assertIn('var _po = _pmOriginNow();', build)
        self.assertNotIn(' synthChatPushTurn(', build)
        self.assertNotIn('_synthChatPushTurnWithMeta(', build)
        # the build's follow-up question is never asked in another chat
        self.assertIn("if (cur === 'complete' && data.pending_question && _pmOriginMoved(_po)) {", build)
        self.assertLess(build.index('_pmOriginMoved(_po)'), build.index('synthChatChipSend(_pq)'))
        deck = self.html[self.html.index('        async function _pmStartDeck('):self.html.index('        async function synthChatSubmit() {')]
        self.assertIn('var _po = _pmOriginNow();', deck)
        self.assertNotIn(' synthChatPushTurn(', deck)
        self.assertNotIn('_synthChatPushTurnWithMeta(', deck)
        self.assertNotIn(' _pmPushGuidanceTurn(', deck)
        self.assertIn('if (_pmAskOrigin === _po) _pmAskOrigin = null;', deck)
        up = self.html[self.html.index('        async function synthChatFilesChosen(files) {'):self.html.index('        (function _synthChatWireDrop() {')]
        self.assertIn('var _po = _pmOriginNow();', up)
        self.assertNotIn(' synthChatPushTurn(', up)

    def test_the_ask_is_wrapped_and_pinned(self):
        sub = self._fn('synthChatSubmit')
        self.assertIn("if (_sb && _sb.dataset.pmStop === '1') return _synthChatSubmitAsk();", sub)
        self.assertIn('_pmAskOrigin = _own;', sub)
        self.assertIn('finally { _pmAskOriginEnd(_own); }', sub)
        ask = self.html[self.html.index('        async function _synthChatSubmitAsk() {'):]
        ask = ask[:20000]
        self.assertLess(ask.index('await _pmEnsureThread()'), ask.index('_pmAskOriginPin(text)'))
        self.assertLess(ask.index('_pmAskOriginPin(text)'), ask.index("synthChatPushTurn('user', text)"))
        appr = self.html[self.html.index('        async function synthChatApproveBrief(opts) {'):][:1500]
        self.assertIn('_apOwn = _pmAskOriginBeginHere();', appr)
        self.assertIn('finally { try { _pmAskOriginEnd(_apOwn); } catch (_) {} }', appr)
        self.assertIn('history: _pmAskHist().slice(0, -1).slice(-12),', self.html)
        self.assertIn('history: _pmAskHist().slice(0, -1)   //', self.html)
        self.assertIn('history: _pmAskHist().slice(-14)', self.html)
        self.assertNotIn('history: synthChatHistory.slice(', self.html)

    def test_a_pending_brief_waits_on_its_thread_and_approve_never_runs_another_threads(self):
        """A brief (its subject, its window, its cuts) drafted in one chat used to sit in the pane across a New
        chat: 'approve' typed there ran it."""
        got = self._run(self._base() + [
            "var A = [{ role: 'user', text: 'Run a profile for cruel summer series' }, { role: 'agent', text: 'New Cruel Summer profile, ready to run.' }];",
            "synthChatHistory = A; _pmPaneTid = 'A';",
            "synthChatCurrentDraft = { subject: 'Cruel Summer', addon_cuts: [{ label: 'Female' }], date_range: { start: '2021-01-01', end: '2021-12-31' } }; panel.style.display = 'block';",
            "var out = {};",
            "_pmPendingNew = true; _pmThreadsIdx.active = null; _pmAdoptHistory([]);          /* New chat */",
            "out.fresh = { draft: synthChatCurrentDraft, batch: synthChatBatchDrafts, panel: panel.style.display, parked: Object.keys(_pmDraftsByThread) };",
            "_pmPendingNew = false; _pmThreadsIdx.active = 'B'; _pmAdoptHistory([{ role: 'user', text: 'For Mad Men series viewers' }]);   /* another thread */",
            "out.other = { draft: synthChatCurrentDraft, panel: panel.style.display };",
            "_pmThreadsIdx.active = 'A'; _pmAdoptHistory(A);                                       /* back to the thread that drafted it */",
            "out.back = { draft: synthChatCurrentDraft, panel: panel.style.display, parked: Object.keys(_pmDraftsByThread), shown: shownBrief };",
            "console.log(JSON.stringify(out));",
        ])
        self.assertIsNone(got['fresh']['draft'])
        self.assertIsNone(got['fresh']['batch'])
        self.assertEqual(got['fresh']['panel'], 'none')
        self.assertEqual(got['fresh']['parked'], ['A'])
        self.assertIsNone(got['other']['draft'])
        self.assertEqual(got['other']['panel'], 'none')
        self.assertEqual(got['back']['draft']['subject'], 'Cruel Summer')
        self.assertEqual(got['back']['draft']['addon_cuts'], [{'label': 'Female'}])
        self.assertEqual(got['back']['panel'], 'block')
        self.assertEqual(got['back']['parked'], [])
        # the typed approval reads the pane's draft, which is now only ever this thread's
        ask = self.html[self.html.index('        async function _synthChatSubmitAsk() {'):]
        self.assertIn('_pmTypedApproval(text)', ask[:60000])

    def test_a_brief_the_pane_restored_survives_another_threads_reply(self):
        got = self._run(self._base() + [
            "var MAD = [{ role: 'user', text: 'For Mad Men series viewers' }];",
            "_pmThreadsIdx.active = 'B'; synthChatHistory = MAD; _pmPaneTid = 'B';",
            "var own = { tid: '', hist: null, q: '' }; _pmAskOrigin = own; _pmAskOriginPin('For Mad Men series viewers');",
            "_pmDraftsByThread['A'] = { draft: { subject: 'Cruel Summer' }, batch: null, shown: true };",
            "_pmThreadsIdx.active = 'A'; _pmAdoptHistory([{ role: 'user', text: 'Run a profile for cruel summer series' }]);",
            "synthChatPushTurn('agent', 'On it: reading Mad Men now.');      /* a reply that drafted nothing */",
            "_pmAskOriginEnd(own);",
            "console.log(JSON.stringify({ draft: synthChatCurrentDraft, panel: panel.style.display, parked: Object.keys(_pmDraftsByThread) }));",
        ])
        self.assertEqual(got['draft'], {'subject': 'Cruel Summer'})        # thread A's own brief stays in thread A's pane
        self.assertEqual(got['panel'], 'block')
        self.assertEqual(got['parked'], [])

    def test_a_lost_thread_never_swaps_the_pane_to_another_chat(self):
        src = self._fn('_pmHistoryResync')
        self.assertNotIn('fetch(', src)                                    # the server's active thread is never fetched and adopted
        self.assertNotIn('_pmAdoptHistory(d.history)', src)
        got = self._run(self.STUBS + [
            "var went = 0, timers = []; function _pmGoFresh() { went++; } function setTimeout(fn, ms) { timers.push(ms); return 1; }",
            "var _pmResyncTimer = null;", src,
            "synthChatHistory = [{ role: 'user', text: 'Run a profile for cruel summer series' }];",
            "_pmHistoryResync(); _pmHistoryResync();",
            "var withTurns = { went: went, hist: synthChatHistory.length, timers: timers.slice(), active: _pmThreadsIdx.active };",
            "synthChatHistory = []; _pmResyncTimer = null; _pmHistoryResync();",
            "console.log(JSON.stringify({ withTurns: withTurns, empty: { went: went }, posts: posts.length }));",
        ])
        self.assertEqual(got['withTurns'], {'went': 0, 'hist': 1, 'timers': [6000], 'active': 'A'})   # the pane keeps its own conversation
        self.assertEqual(got['empty']['went'], 1)                          # nothing typed: simply a fresh pane
        self.assertEqual(got['posts'], 0)

    def test_the_follow_moves_the_pane_only_on_a_real_move_and_carries_nothing(self):
        src = self._fn('_pmThreadFollow')
        self.assertIn('if (d.fresh) {', src)
        self.assertIn('var _moved = !!(d.switched || d.created);', src)
        self.assertIn('_pmThreadsIdx = { active: _moved ? d.active : (_prevActive || null), threads: d.threads || [] };', src)
        self.assertIn("if (typeof synthChatInflightController !== 'undefined' && synthChatInflightController) return;", src)
        self.assertLess(src.index('synthChatInflightController) return;'), src.index("fetch('/api/brief-chat/threads/bind'"))
        if not NODE:
            self.skipTest('node not installed')
        got = self._run(self.STUBS + [
            "var adopted = [], fresh = 0, notes = [], reply = null, key = 'profile:Mad_Men.csv';",
            "var synthChatInflightController = null, _pmFollow = { key: '', inflight: false, pending: false };",
            "function _pmProjectIdentity() { return key ? { subject_key: key, product: 'profileIQ' } : null; }",
            "function _pmAdoptHistory(h) { adopted.push(h.length); synthChatHistory = h; }",
            "function _pmGoFresh() { fresh++; _pmPendingNew = true; _pmThreadsIdx.active = null; }",
            "function _pmThreadSwitchNote(l) { notes.push(l || ''); } function _pmThreadHeaderRefresh() {}",
            "function _pmHistorySave() { return Promise.resolve(null); }",
            "function fetch(url, opts) { posts.push(url); return Promise.resolve({ json: function () { return Promise.resolve(reply); } }); }",
            src,
            "var out = {}; synthChatHistory = [{ role: 'user', text: 'cruel' }];",
            "function tick() { return new Promise(function (r) { setImmediate(function () { setImmediate(function () { setImmediate(r); }); }); }); }",
            "(async function () {",
            "  /* an ask in flight: the pane does not move under it */",
            "  synthChatInflightController = {}; _pmThreadFollow(); await tick(); out.inflight = { posts: posts.length, active: _pmThreadsIdx.active };",
            "  synthChatInflightController = null;",
            "  /* the server's active pointer is another tab's thread and nothing switched: this tab stays where it is */",
            "  reply = { success: true, active: 'OTHER', switched: false, created: false, history: [{}, {}, {}], threads: [] };",
            "  _pmThreadFollow(); await tick(); out.notMoved = { active: _pmThreadsIdx.active, adopted: adopted.slice() };",
            "  /* the page leaves every project and the general chat is stale: an empty pane */",
            "  key = ''; reply = { success: true, active: null, fresh: true, switched: false, created: false, history: [], threads: [] };",
            "  _pmThreadFollow(); await tick(); out.fresh = { fresh: fresh, pending: _pmPendingNew, active: _pmThreadsIdx.active, adopted: adopted.slice() };",
            "  /* a real move to a project's thread adopts that thread's turns and nothing else */",
            "  key = 'profile:Cruel.csv'; _pmPendingNew = false; _pmThreadsIdx.active = 'A';",
            "  reply = { success: true, active: 'P', switched: true, created: true, history: [], binding: { subject_label: 'Cruel Summer Viewers' }, threads: [] };",
            "  _pmThreadFollow(); await tick(); out.moved = { active: _pmThreadsIdx.active, adopted: adopted.slice(), notes: notes.slice() };",
            "  console.log(JSON.stringify(out));",
            "})();",
        ])
        self.assertEqual(got['inflight'], {'posts': 0, 'active': 'A'})
        self.assertEqual(got['notMoved'], {'active': 'A', 'adopted': []})
        self.assertEqual(got['fresh']['fresh'], 1)
        self.assertTrue(got['fresh']['pending'])
        self.assertIsNone(got['fresh']['active'])
        self.assertEqual(got['fresh']['adopted'], [])
        self.assertEqual(got['moved'], {'active': 'P', 'adopted': [0], 'notes': ['Cruel Summer Viewers']})

    def test_the_header_says_which_chat_this_is(self):
        got = self._run(self._base() + [
            "var out = {};",
            "synthChatHistory = [{ role: 'user', text: 'Run a profile for cruel summer series' }];",
            "_pmThreadHeaderRefresh(); out.thread = { text: sub.textContent, tip: sub.title };",
            "_pmThreadsIdx.threads[0].title = 'New chat'; _pmThreadHeaderRefresh(); out.untitled = sub.textContent;",
            "_pmPendingNew = true; _pmThreadsIdx.active = null; synthChatHistory = []; _pmThreadHeaderRefresh(); out.fresh = { text: sub.textContent, tip: sub.title };",
            "_pmPendingNew = false; _pmThreadsIdx.active = 'B'; _pmThreadsIdx.threads[1].binding = { subject_key: 'profile:x', subject_label: 'Mad Men' }; _pmThreadHeaderRefresh(); out.bound = sub.textContent;",
            "console.log(JSON.stringify(out));",
        ])
        self.assertEqual(got['thread']['text'], 'Chat: Run a profile for cruel summer series')
        self.assertIn('Chats & Tasks', got['thread']['tip'])
        self.assertEqual(got['untitled'], 'Chat: Run a profile for cruel summer series')   # the first question names it until the rail does
        self.assertEqual(got['fresh']['text'], 'New chat')
        self.assertIn('Nothing carries over', got['fresh']['tip'])
        self.assertEqual(got['bound'], 'On Mad Men')
        for text in (got['thread']['text'], got['thread']['tip'], got['fresh']['tip']):
            self.assertNotIn('\u2014', text)
            self.assertNotIn('\u2013', text)

    def test_new_chat_is_a_fresh_pane_that_mints_its_thread_on_the_first_message(self):
        new = self._fn('pmNewChat')
        self.assertIn('_pmGoFresh(null, false)', new)
        self.assertNotIn('threads/new', new)
        ens = self._fn('_pmEnsureThread')
        self.assertIn("fetch('/api/brief-chat/threads/new'", ens)
        self.assertIn('_pmPaneTid = String(d.active);', ens)
        got = self._run(self.STUBS + [
            "var adopted = null; function _pmAdoptHistory(h) { adopted = h; synthChatHistory = h; }",
            "function _pmProjectIdentity() { return null; } var _pmFollow = { key: null }; var _pmReopenBusy = false;",
            "function _synthChatShowWelcomeBack() {}",
            "var minted = 0; function fetch(url, opts) { posts.push(url); if (url.indexOf('threads/new') !== -1) { minted++; return Promise.resolve({ json: function () { return Promise.resolve({ success: true, active: 'new' + minted, threads: [] }); } }); } return Promise.resolve({ ok: true, json: function () { return Promise.resolve({ success: true, threads: [] }); } }); }",
            "var _PM_V1_SURFACES = { '/api/brief-chat/interpret': 'interpret' };",
            self._origin_block(), self._fn('_pmGoFresh'), self._fn('_pmEnsureThread'), self._fn('_pmV1Request'),
            "synthChatHistory = [{ role: 'user', text: 'For Mad Men series viewers' }, { role: 'agent', text: 'On people who watched Mad Men' }];",
            "_pmThreadsIdx.active = 'B';",
            "_pmGoFresh(null, false);",
            "var out = { pane: synthChatHistory.length, active: _pmThreadsIdx.active, pending: _pmPendingNew };",
            "_pmEnsureThread().then(function (tid) {",
            "  var own = { tid: '', hist: null, q: '' }; _pmAskOrigin = own; _pmAskOriginPin('Run a profile for cruel summer series');",
            "  synthChatHistory.push({ role: 'user', text: 'Run a profile for cruel summer series' });",
            "  var body = _pmV1Request('/api/brief-chat/interpret', { text: 'Run a profile for cruel summer series', history: _pmAskHist().slice(0, -1) }).body;",
            "  out.first = { tid: tid, body: body, minted: minted, pane: _pmPaneTid };",
            "  console.log(JSON.stringify(out));",
            "});",
        ])
        self.assertEqual(got['pane'], 0)                                   # the pane is empty: no Mad Men turn in it
        self.assertIsNone(got['active'])
        self.assertTrue(got['pending'])
        self.assertEqual(got['first']['tid'], 'new1')                      # a fresh thread id
        self.assertEqual(got['first']['minted'], 1)
        self.assertEqual(got['first']['body']['thread_id'], 'new1')        # and the first post carries it
        self.assertEqual(got['first']['body']['history'], [])              # with empty context
        self.assertEqual(got['first']['pane'], 'new1')
        self.assertNotIn('Mad Men', json.dumps(got['first']['body']))


# ============================================================================ 7
REPLAY_CHILD = r'''
import json, os, sys
sys.path.insert(0, __WEBAPP__)
os.environ['PM_MAIL_OFF'] = '1'
from prometheus import replay
replay.install_isolation()
h = replay.harness()
u = dict(replay.REPLAY_USER)
S = {'username': u['username']}
import prometheus.legacy.chat as C
out = {}

def ctx_for(subject):
    key = f"{replay._slug(subject)}_09_01_2026_10_00.csv"
    h.s3.put_object(Bucket='dashboard-inputs', Key=key, Body=replay._profile_csv(subject))
    return {'view': 'profile-iq', 'primary': {'s3_key': key, 'name': subject},
            'view_context': {'view_id': 'profileIQ', 'view_title': subject, 'data': {}}}

mad = ctx_for('Mad Men')
h.host.set('s3_cache', {'jobs': [{'s3_key': mad['primary']['s3_key'], 'display_name': 'Mad Men', 'category': 'SERIES - DRAMA', 'last_modified': '2026-09-01T10:00:00'}]})
h.host.set('_admin_hidden_profile_keys', lambda *a, **k: set())
h.host.set('maybe_refresh_persisted_cache_if_changed', lambda *a, **k: None)
h.host.set('_profile_age_days', lambda *a, **k: 12)

# the finished Mad Men thread, active on the server, with its pin
st, d = h.post('/api/brief-chat/threads/new', {}, session=S)
tid_mad = d.get('active')
mad_turns = [{'role': 'user', 'text': 'how big is the Mad Men audience?', 'turn_id': 'm1'},
             {'role': 'agent', 'text': 'On Mad Men: Mad Men reaches 7,582,851 US people.', 'turn_id': 'm2', 'meta': {'subject': 'Mad Men'}}]
h.post('/api/brief-chat/history', {'thread_id': tid_mad, 'history': mad_turns}, session=S)
idx = C._load_threads_index(u['username'])
for t in idx['threads']:
    if t['id'] == tid_mad:
        t['pin'] = {'subject': 'Mad Men', 'source': 'offer', 'set_at': '2026-11-10T21:41:45Z', 'dropped': []}
C._pm_s3_put_json(C._pm_threads_index_key(u['username']), idx)

# New chat, then the first message mints its thread
h.post('/api/brief-chat/threads/fresh', {}, session=S)
st, d = h.post('/api/brief-chat/threads/new', {}, session=S)
tid_new = d.get('active')
out['ids'] = {'mad': tid_mad, 'new': tid_new, 'distinct': bool(tid_new) and tid_new != tid_mad}
st, hd = h.get('/api/brief-chat/history?thread_id=' + str(tid_new), session=S)
out['new_history'] = (hd or {}).get('history')

def ask(text, tid, page=None, history=None, surface='analyze'):
    n0 = len(replay._STATE['captured'])
    body = {'text': text, 'history': list(history or []), 'client': 'dashboard', 'persist': False, 'surface': surface, 'thread_id': tid}
    if page is not None:
        body['page_context'] = page
    st, pl = h.post(replay.DOOR_PATH, body, session=S)
    raw = pl.get('raw') if isinstance(pl.get('raw'), dict) else pl
    recs = replay._STATE['captured'][n0:]
    return {'status': st, 'thread_id': pl.get('thread_id'), 'reply': str(raw.get('reply') or raw.get('error') or pl.get('text') or ''),
            'subject': raw.get('subject') or raw.get('profile'), 'job': raw.get('read_job_id'),
            'log': [{'subject': r.get('subject'), 'route': r.get('route'), 'outcome': r.get('outcome'),
                     'thread': (r.get('extra') or {}).get('thread')} for r in recs]}

h.patch('_pm_claude_json', lambda *a, **k: {'success': True, 'data': {'action': 'answer', 'reply': 'Cruel Summer viewers are 68.4% women.'}, 'model': 'seq'})
Q = 'What share of Cruel Summer viewers are women?'
def pins():
    return {t['id']: (t.get('pin') or {}).get('subject') for t in C._load_threads_index(u['username'])['threads']}

out['new_no_page'] = ask(Q, tid_new)
out['new_mad_page'] = ask(Q, tid_new, page=mad)
out['pins_after_new'] = pins()
# back in the Mad Men thread: a pronoun follow-up inherits, then an explicit switch moves the thread cleanly
out['pronoun'] = ask('and for women?', tid_mad, history=mad_turns)
out['pins_after_pronoun'] = pins()
out['same_thread_switch'] = ask(Q, tid_mad, page=mad, history=mad_turns)
out['pins'] = pins()
jobs = {}
for k in ('new_no_page', 'new_mad_page', 'same_thread_switch', 'pronoun'):
    jid = out[k].get('job')
    if jid:
        doc = C._pm_read_job_doc(jid) or {}
        rr = doc.get('resume') or {}
        jobs[k] = {'thread_id': doc.get('thread_id'), 'subject': doc.get('subject'), 'bind_subject': rr.get('bind_subject'),
                   'base_key': rr.get('base_key'), 'history': len(rr.get('history') or [])}
out['jobs'] = jobs
print('__OUT__' + json.dumps(out, default=str))
'''


class TheRealDoor(unittest.TestCase):
    """The real routes and cores through the replay harness (the parent repo's scripts/_pm_behavior.py); skipped
    when bg-webapp is checked out on its own."""

    @classmethod
    def setUpClass(cls):
        parent_scripts = os.path.join(os.path.dirname(ROOT), 'scripts')
        if not os.path.exists(os.path.join(parent_scripts, '_pm_behavior.py')):
            raise unittest.SkipTest('the replay harness lives in the parent repo; not on this path')
        child = REPLAY_CHILD.replace('__WEBAPP__', repr(ROOT))
        proc = subprocess.run([sys.executable, '-c', child], capture_output=True, text=True, timeout=600,
                              env=dict(os.environ, PM_REPLAY_CHILD='1'))
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith('__OUT__')), '')
        if not line:
            raise AssertionError('the replay child produced no result\n' + proc.stdout[-2000:] + '\n' + proc.stderr[-3000:])
        cls.out = json.loads(line[len('__OUT__'):])

    def test_new_chat_is_a_new_thread_with_empty_context(self):
        self.assertTrue(self.out['ids']['distinct'], self.out['ids'])
        self.assertEqual(self.out['new_history'], [])

    def _about_cruel_summer(self, r, tid):
        blob = json.dumps(r, default=str).lower()
        self.assertEqual(r['status'], 200, r)
        self.assertEqual(r['thread_id'], tid, r)
        self.assertIn('cruel summer', blob, r)
        self.assertNotIn('mad men', r['reply'].lower(), r)
        self.assertNotIn('mad men', str(r.get('subject') or '').lower(), r)
        for rec in r['log']:
            self.assertNotIn('mad men', str(rec.get('subject') or '').lower(), rec)
            self.assertEqual((rec.get('thread') or {}).get('id'), tid, rec)

    def test_cruel_summer_in_the_new_thread(self):
        self._about_cruel_summer(self.out['new_no_page'], self.out['ids']['new'])

    def test_cruel_summer_in_the_new_thread_with_the_mad_men_page_open(self):
        self._about_cruel_summer(self.out['new_mad_page'], self.out['ids']['new'])

    def test_an_explicit_switch_inside_the_mad_men_thread(self):
        self._about_cruel_summer(self.out['same_thread_switch'], self.out['ids']['mad'])

    def test_a_pronoun_follow_up_inside_the_mad_men_thread_stays_on_mad_men(self):
        r = self.out['pronoun']
        self.assertEqual(r['thread_id'], self.out['ids']['mad'], r)
        blob = json.dumps(r, default=str).lower()
        self.assertIn('mad men', blob, r)
        self.assertNotIn('cruel summer', blob, r)

    def test_every_job_carries_the_thread_and_the_subject_of_its_own_ask(self):
        for k, tid_key in (('new_no_page', 'new'), ('new_mad_page', 'new'), ('same_thread_switch', 'mad'), ('pronoun', 'mad')):
            job = self.out['jobs'].get(k)
            if not job:
                continue
            self.assertEqual(job['thread_id'], self.out['ids'][tid_key], (k, job))
            if k != 'pronoun':
                self.assertNotIn('mad men', str(job.get('bind_subject') or job.get('subject') or '').lower(), (k, job))
                self.assertNotIn('Mad_Men', str(job.get('base_key') or ''), (k, job))
            if tid_key == 'new':
                self.assertEqual(job['history'], 0, (k, job))

    def test_each_thread_keeps_its_own_subject(self):
        mad, new = self.out['ids']['mad'], self.out['ids']['new']
        # two Cruel Summer asks in the new thread left the Mad Men thread's subject alone
        self.assertEqual(self.out['pins_after_new'].get(mad), 'Mad Men')
        self.assertNotIn('mad men', str(self.out['pins_after_new'].get(new) or '').lower())
        # a pronoun follow-up in the Mad Men thread kept it on Mad Men
        self.assertEqual(self.out['pins_after_pronoun'].get(mad), 'Mad Men')
        # and the new thread never took the Mad Men subject at any point
        self.assertNotIn('mad men', str(self.out['pins'].get(new) or '').lower())


if __name__ == '__main__':
    unittest.main(verbosity=1)
