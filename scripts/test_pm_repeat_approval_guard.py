#!/usr/bin/env python3
"""A repeat approval is answered, never run twice (Jenna 2026-11-17).

Naomi Inoshita at Sony, 2026-11-17: the team started her Subscriber IQ
for her from the build server after her Approve was refused. It ran, was
delivered and was charged once. The Approve & run card for the very same
brief was still open in her tab; one more click would have started a
second identical run and taken a second charge.

  1. The Naomi case: a Subscriber IQ delivered through the operator
     path, then an approval of the identical brief, answers "already
     delivered", with no charge, no run and nothing written.
                                                  -> TheNaomiCase
  2. The same while the first run is still going. -> TheNaomiCase
  3. The same for a profile build started by a chat Approve.
                                                  -> AProfileBuildFromChat
  4. A different window, platform, subject or cut is a different order
     and runs.                                    -> ADifferentOrderRuns
  5. The identical order outside the day runs.    -> OutsideTheDay
  6. A lookup that fails never blocks an approval. -> ItFailsOpen
  7. Nothing in the request switches the check off. -> NothingInTheRequestSkipsIt
  8. The reply carries no price, no internal word and no long dash.
                                                  -> WhatTheReaderIsTold
  9. A run is seen however it was started: the operator path, the
     partner route (the engine's own record and the run owners), a seat
     on the same company wallet.                  -> SeenHoweverItStarted
 10. A probe leaves nothing behind; the operator path is never refused.
                                                  -> ProbesAndTheOperatorPath

Hermetic: the approve core and its helpers are lifted from the chat
module's source and run against an in-memory store, a fake engine and a
fake users file. No model call, no live store, no mail, no app module.
"""
from __future__ import annotations

import ast
import builtins
import io
import json
import os
import re
import sys
import time
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('PM_MAIL_OFF', '1')
from _pm_legacy_view import legacy_view as _legacy_view, chat_py_lines as _chat_py_lines, legacy_lines as _legacy_lines, LEGACY_CEILING as _LEGACY_CEILING  # noqa: E402  (stage 5: chat.py plus its extracted cores; stage 7: the ratchet counts the legacy code)
HERE = Path(os.path.abspath(__file__)).parent      # not resolved: the parent repo is found by walking up
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prometheus import build_ready as BR  # noqa: E402
from prometheus import order_truth as OT  # noqa: E402
from prometheus import repeat_approval as RA  # noqa: E402

CHAT = _legacy_view()
BUCKET = 'dashboard-inputs'
SUBIQ_BUCKET = 'svod-acquisition'
NAOMI = 'naomi_inoshita'
NAOMI_EMAIL = 'naomi@studio.example'
RUN = 'QCI0NWUsymkBpw'
LABEL = 'Outlander: Blood of My Blood - Season 2 to date'
TRACKER = 'Outlander_Blood_of_My_Blood_Season_2_to_date_11_18_2026_02_30.csv'
BANNED = ('synth', 'model', 'estimat', 'pipeline', 'queue', 'worker', 'build server', 'engine', 'hetzner',
          'run id', 'guard', 'record', 'spec', 'credit', 'wallet', 'charge')


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def subiq_block():
    return {'title': 'Outlander: Blood of My Blood', 'platform': 'Starz', 'medium': 'series', 'season': 2,
            'genre': 'Drama', 'content_cadence': 'Weekly', 'is_new_show': False,
            'air_window': {'start': '2026-09-18', 'end': '2026-11-27'},
            'episode_dates': ['2026-09-18', '2026-09-25'], 'movie_scope': None, 'season_to_date': True,
            'through_date': '2026-11-18', 'deliverable_label': LABEL, 'addon_profile': False}


def naomi_draft(**over):
    """The brief on Naomi's open card."""
    d = {'decision': 'subscriber_iq', 'subject': 'Outlander: Blood of My Blood Viewers on Starz',
         'brand_category': 'SERIES - DRAMA', 'date_range': {'start': '2026-09-18', 'end': '2026-11-18'},
         'subiq': subiq_block()}
    d.update(over)
    return d


def operator_spec():
    """The spec the operator queued for her, as it sat on the build server."""
    return {'name': 'Outlander: Blood of My Blood Viewers on Starz', 'brand_category': 'SERIES - DRAMA',
            'date_range': '2026-09-18 TO 2026-11-18', 'anchor_title': 'Outlander: Blood of My Blood',
            'anchor_season': 2, 'subiq': subiq_block()}


def profile_draft(subject='Trinity Tatum', start='2025-11-17', end='2026-11-17', **over):
    d = {'decision': 'new_build', 'subject': subject, 'brand_category': 'ATHLETE',
         'date_range': {'start': start, 'end': end}, 'tu_demos': {'GENDER': {'FEMALE': 60}}}
    d.update(over)
    return d


# ------------------------------------------------------------------ the fakes
class Missing(Exception):
    def __str__(self):
        return 'An error occurred (NoSuchKey): the key does not exist'


class Store:
    """Every bucket in memory, with a list of what was written."""

    def __init__(self):
        self.objs, self.writes, self.down = {}, [], False

    def put_json(self, key, doc, bucket=BUCKET):
        self.objs[(bucket, key)] = json.dumps(doc).encode('utf-8')

    def get_json(self, key, bucket=BUCKET):
        return json.loads(self.objs[(bucket, key)].decode('utf-8'))

    def get_object(self, Bucket, Key, **_k):
        if self.down:
            raise RuntimeError('store unreachable')
        if (Bucket, Key) not in self.objs:
            raise Missing()
        return {'Body': io.BytesIO(self.objs[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, **_k):
        if self.down:
            raise RuntimeError('store unreachable')
        self.objs[(Bucket, Key)] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')
        self.writes.append(Key)

    def head_object(self, Bucket, Key, **_k):
        if self.down:
            raise RuntimeError('store unreachable')
        if (Bucket, Key) not in self.objs:
            raise Missing()
        return {}

    def list_objects_v2(self, Bucket, Prefix='', StartAfter='', **_k):
        if self.down:
            raise RuntimeError('store unreachable')
        keys = sorted(k for (b, k) in self.objs if b == Bucket and k.startswith(Prefix) and k > StartAfter)
        return {'Contents': [{'Key': k} for k in keys]}


class Resp:
    def __init__(self, code, doc=None):
        self.status_code, self._doc, self.text = code, doc, json.dumps(doc) if doc is not None else ''

    def json(self):
        return self._doc


class Engine:
    """The build engine: its run records, and what was posted to it."""

    def __init__(self):
        self.runs, self.posts, self.gets, self.down, self.refuse, self.stamp = {}, [], [], False, False, True

    def add(self, run_id, **doc):
        now = time.time()
        base = {'run_id': run_id, 'status': 'running', 'decision': 'new_build', 'user_email': '', 'subject': '',
                'started_at': _iso(now), 'updated_at': _iso(now)}
        base.update(doc)
        self.runs[run_id] = base
        return base

    def finish(self, run_id, ago_s=0, **doc):
        when = _iso(time.time() - ago_s)
        self.runs[run_id].update({'status': 'complete', 'completed_at': when, 'updated_at': when})
        self.runs[run_id].update(doc)

    def get(self, url, params=None, timeout=None, headers=None):
        self.gets.append(url)
        if self.down:
            raise TimeoutError('the read timed out')
        assert timeout and timeout <= 6, 'an engine lookup must be bounded'
        if url.endswith('/synth/list'):
            docs = sorted(self.runs.values(), key=lambda d: str(d.get('updated_at') or ''), reverse=True)
            return Resp(200, [dict(d) for d in docs])
        rid = url.rsplit('/', 1)[-1]
        return Resp(200, dict(self.runs[rid])) if rid in self.runs else Resp(404, {'status': 'unknown'})

    def post(self, url, json=None, timeout=None, headers=None):   # noqa: A002 - the requests signature
        self.posts.append(json)
        if self.down:
            raise TimeoutError('the read timed out')
        if self.refuse:
            return Resp(409, {'error': 'a build for this subject is already in progress'})
        rid = f"RUN{len(self.runs) + 1:03d}"
        spec = (json or {}).get('spec') or {}
        doc = self.add(rid, status='queued', decision=(json or {}).get('decision'),
                       user_email=(json or {}).get('user_email'), subject=spec.get('name'))
        if self.stamp:   # what the engine stamps at job start (migration/synth_queue_worker._order_key_for)
            doc['order_key'] = RA.spec_key((json or {}).get('decision'), spec,
                                           derive_type=(json or {}).get('derive_type') or '',
                                           parent_key=(json or {}).get('parent_s3_key') or '',
                                           cut_label=(json or {}).get('cut_label') or '')
        return Resp(200, {'run_id': rid, 'status': 'queued', 'pending_position': 1})


USERS = {
    'users': {
        NAOMI: {'email': NAOMI_EMAIL, 'company': 'Studio Television', 'billing_source': 'company'},
        'colleague': {'email': 'colleague@studio.example', 'company': 'Studio Television', 'billing_source': 'company'},
        'elsewhere': {'email': 'pat@elsewhere.example', 'company': 'Elsewhere Co', 'billing_source': 'company'},
        'smclain': {'email': 'smclain@agency.example'},
        'scott': {'email': 'scott@agency.example'},
        'partner_key': {'email': 'api@studio.example', 'company': 'Studio Television', 'billing_source': 'company'},
    },
    'companies': {'Studio Television': {'paying_customer': True, 'wallet_balance_usd': 11000.0},
                  'Elsewhere Co': {'paying_customer': True, 'wallet_balance_usd': 500.0}},
}


class Host:
    """The app globals the approve core reads. Anything not named here is a harmless stand-in."""

    def __init__(self, world):
        self._w = world
        self.s3_client, self.S3_BUCKET, self.SUBSCRIBER_S3_BUCKET = world.store, BUCKET, SUBIQ_BUCKET
        self.SYNTH_QUEUE_URL, self.SYNTH_QUEUE_SECRET = 'http://engine.test', 'secret-for-tests'
        self._V1_CREDITS = {'new_build': 10, 'time_shifted_refresh': 8, 'derive_cut': 3, 'cut_needs_parent': 8,
                            'subscriber_iq': 10}
        self.CREDITS_PROFILE_ANALYSIS, self.ADDON_CUT_CREDITS = 10, 3

    def load_users(self):
        if self._w.users_down:
            raise RuntimeError('users file unreachable')
        return json.loads(json.dumps(USERS))

    def _normalize_v1_decision(self, draft):
        return (str(draft.get('decision') or 'new_build'), str(draft.get('existing_match_s3_key') or ''),
                str(draft.get('derive_type') or ''))

    def has_credits_for(self, *a, **k):
        self._w.priced.append(a)
        return True

    def consume_credit(self, *a, **k):
        self._w.charges.append((a, k))
        return True

    def check_user_credits(self, *_a, **_k):
        return True, 50

    def _scrub_spec_text(self, value, **_k):
        return '' if value is None else str(value)

    def _estimated_audience_range(self, *_a, **_k):
        return None

    def _chatbot_error_email(self, *a, **k):
        self._w.ops_mail.append(a)

    def _clean_queue_error_text(self, t):
        return str(t or '')

    def get(self, name, default=None):
        return getattr(self, name, default) if name in self.__dict__ or hasattr(type(self), name) else default

    def __getattr__(self, name):      # only reached for a name not defined above
        if name.startswith('__'):
            raise AttributeError(name)
        return MagicMock(name=f'_H.{name}')


class Faults:
    def __init__(self):
        self.notes, self.probe = [], False

    def note(self, where, exc=None):
        self.notes.append((where, repr(exc)))

    def is_probe(self):
        return self.probe


class Namespace(dict):
    """exec globals: a name the harness did not provide is a harmless stand-in, so a change elsewhere in the
    approve core (a new helper on the pricing path) does not break this suite for a mechanical reason."""

    def __missing__(self, name):
        if hasattr(builtins, name):
            raise KeyError(name)
        stand_in = MagicMock(name=name)
        self[name] = stand_in
        return stand_in


def _top_level(tree, names):
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            out.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
            out.append(node)
    return out


LIFTED = ('_PM_RECENT_BUILDS_KEY', '_PM_NO_DRAFT_REPLY', '_pm_recent_build_guard', '_pm_approve_core')
_TREE = ast.parse(CHAT)
_NODES = _top_level(_TREE, LIFTED)
LIFTED_SRC = '\n\n\n'.join(ast.get_source_segment(CHAT, n) for n in _NODES)
APPROVE_SRC = next(ast.get_source_segment(CHAT, n) for n in _NODES
                   if isinstance(n, ast.FunctionDef) and n.name == '_pm_approve_core')


class World:
    """One dashboard, one engine, one store; approvals go through the real approve core."""

    def __init__(self):
        self.store, self.engine = Store(), Engine()
        self.priced, self.charges, self.ops_mail, self.users_down = [], [], [], False
        self.faults, self.host, self.session = Faults(), None, {}
        self.host = Host(self)
        self.probe_caller = ''
        req = types.ModuleType('requests')
        req.get, req.post = self.engine.get, self.engine.post
        self._requests = req
        ns = Namespace()
        ns.update({
            '_H': self.host, 'session': self.session, 'jsonify': lambda d: d, '_faults': self.faults,
            '_pm_probe_caller': lambda: self.probe_caller,
            '_pm_is_probe_user': lambda u: str(u or '').strip().lower().startswith('canary'),
            '_spec_from_draft': self._spec_from_draft, '_order_truth': OT, '_build_ready': BR,
            '_question_first': types.SimpleNamespace(take_origin=lambda d: None, keep=lambda *a, **k: None),
            '_seams': types.SimpleNamespace(tag_queue=lambda p: p),
            '_api_threads': types.SimpleNamespace(is_api_request=lambda: False),
            '_load_threads_index': lambda u: {'active': 't-open'},
            '_pm_s3_put_json': lambda key, doc: self.store.put_object(BUCKET, key, json.dumps(doc).encode('utf-8')),
            '_pm_ask_stage': lambda *a, **k: None, '_pm_remember_ask_build': lambda *a, **k: None,
            '_pm_usage_extras': lambda *a, **k: {}, '_pm_usd_label': lambda v, *a: f"{v}",
            'time': time, 'traceback': __import__('traceback'), 're': re, 'json': json, 'datetime': datetime,
        })
        exec(compile(LIFTED_SRC, '<approve core>', 'exec'), ns)
        self.ns = ns

    @staticmethod
    def _spec_from_draft(draft):
        dr = draft.get('date_range') if isinstance(draft.get('date_range'), dict) else {}
        spec = {'name': draft.get('subject'), 'file_stem': str(draft.get('subject') or '').replace(' ', '_'),
                'brand_category': draft.get('brand_category') or 'BRAND',
                'date_range': f"{dr.get('start')} TO {dr.get('end')}" if dr.get('start') and dr.get('end') else ''}
        for k in ('platform_scope', 'addon_cuts', 'cut_date_range'):
            if draft.get(k):
                spec[k] = draft[k]
        return spec

    def approve(self, draft, as_user=NAOMI, **body):
        """One Approve & run click. Returns (reply, started, charged)."""
        rec = (USERS['users'].get(as_user) or {})
        user = {'username': as_user, 'email': rec.get('email') or ''}
        self.session.clear()
        self.session['username'] = as_user
        posts, charges = len(self.engine.posts), len(self.charges)
        full = {'spec_draft': json.loads(json.dumps(draft)), 'run_avid': True, 'email_to': '', 'thread_id': 't-open'}
        full.update(body)
        saved = sys.modules.get('requests')
        sys.modules['requests'] = self._requests
        try:
            out = self.ns['_pm_approve_core'](user, full)
        finally:
            if saved is not None:
                sys.modules['requests'] = saved
            else:
                sys.modules.pop('requests', None)
        if isinstance(out, tuple):
            out = out[0]
        return out, len(self.engine.posts) - posts, len(self.charges) - charges

    # the records, as the real writers leave them
    def entries(self):
        try:
            return self.store.get_json(RA.STORE_KEY).get('entries') or []
        except KeyError:
            return []

    def age_entries(self, seconds):
        doc = self.store.get_json(RA.STORE_KEY)
        for e in doc['entries']:
            e['t'] = float(e['t']) - seconds
        self.store.put_json(RA.STORE_KEY, doc)

    def deliver_tracker(self, run_id=RUN, username=NAOMI, ago_s=0, window=('2026-09-18', '2026-11-18')):
        self.engine.finish(run_id, ago_s=ago_s, subiq_s3_key=TRACKER)
        self.store.objs[(SUBIQ_BUCKET, TRACKER)] = b'Column,Value\n'
        stamp = time.strftime('%Y%m%dT%H%M%S', time.gmtime(time.time() - ago_s))
        self.store.put_json(f"{RA.DELIVERY_PREFIX}{username}/{stamp}_subiq_Outlander.json", {
            'kind': 'subiq', 'user': username, 'subject': LABEL, 'key': TRACKER, 'job_id': run_id,
            'window': {'start': window[0], 'end': window[1]},
            'where': f'Find it in the dashboard under Subscriber IQ tab: {LABEL}.'})

    def operator_starts(self, run_id=RUN, username=NAOMI, thread_id='', stamp=True, noted=True, ago_s=0):
        """migration/local_override_profile.py --decision subscriber_iq --bill-to naomi_inoshita."""
        spec = operator_spec()
        doc = self.engine.add(run_id, decision='subscriber_iq', user_email=NAOMI_EMAIL, subject=spec['name'])
        if stamp:
            doc['order_key'] = RA.spec_key('subscriber_iq', spec)
        if noted:
            got = RA.note_operator_start(self.host, username=username, run_id=run_id, decision='subscriber_iq',
                                         spec=spec, thread_id=thread_id, engine_user=NAOMI_EMAIL,
                                         now=time.time() - ago_s)
            return got
        return {}


def assert_untouched(test, world, before_writes, reply, started, charged):
    test.assertEqual(started, 0, 'nothing may be sent to the engine')
    test.assertEqual(charged, 0, 'nothing may be charged')
    test.assertEqual(world.priced, [], 'the check sits before pricing: the funds preflight is never reached')
    test.assertEqual(world.store.writes, before_writes, 'nothing may be written (no ledger entry, no record)')
    test.assertFalse(reply.get('success'))
    test.assertTrue(reply.get('guidance'), 'the chat shows a guidance reply as it is')
    test.assertFalse(reply.get('run_id'))


# ------------------------------------------------------------------ 1 and 2
class TheNaomiCase(unittest.TestCase):

    def test_delivered_through_the_operator_path_then_the_open_card_is_clicked(self):
        w = World()
        w.operator_starts(ago_s=50 * 60)
        w.deliver_tracker(ago_s=20 * 60)
        before = list(w.store.writes)
        reply, started, charged = w.approve(naomi_draft())
        assert_untouched(self, w, before, reply, started, charged)
        self.assertEqual(reply['already'], 'delivered')
        self.assertEqual(reply['error'], f"{LABEL} is already delivered. It is in the Subscriber IQ tab, and there "
                                         "is nothing more to approve.")
        self.assertEqual(reply['followups'], [f"Open {LABEL}"])
        # and a second click, and a tenth, say the same thing
        for _ in range(3):
            again, started, charged = w.approve(naomi_draft())
            self.assertEqual((again['error'], started, charged), (reply['error'], 0, 0))

    def test_the_same_while_the_first_run_is_still_going(self):
        w = World()
        w.operator_starts(ago_s=50 * 60)
        before = list(w.store.writes)
        reply, started, charged = w.approve(naomi_draft())
        assert_untouched(self, w, before, reply, started, charged)
        self.assertEqual(reply['already'], 'running')
        self.assertEqual(reply['error'], f"{LABEL} is already running, so I did not start it again. The ready notice "
                                         "comes when it finishes.")
        self.assertEqual(reply['followups'], [])

    def test_her_real_run_as_it_sits_today_is_seen_with_no_new_record_at_all(self):
        # the run of 2026-11-17 was started before any of this existed: no entry on the guard's record, no key on
        # the engine's record. The engine's run under her address plus the delivery record are enough.
        w = World()
        w.operator_starts(stamp=False, noted=False)
        w.deliver_tracker(ago_s=45 * 60)
        self.assertEqual(w.entries(), [])
        before = list(w.store.writes)
        reply, started, charged = w.approve(naomi_draft())
        assert_untouched(self, w, before, reply, started, charged)
        self.assertEqual(reply['already'], 'delivered')
        self.assertIn('Subscriber IQ tab', reply['error'])

    def test_her_real_run_is_matched_by_its_tracker_when_the_brief_was_named_differently(self):
        w = World()
        w.operator_starts(stamp=False, noted=False)
        w.deliver_tracker(ago_s=45 * 60)
        reply, started, _ = w.approve(naomi_draft(subject='Outlander Blood of My Blood on Starz (Season 2)'))
        self.assertEqual((reply.get('already'), started), ('delivered', 0))

    def test_still_going_with_no_new_record_is_already_running(self):
        w = World()
        w.operator_starts(stamp=False, noted=False)
        reply, started, charged = w.approve(naomi_draft())
        self.assertEqual((reply.get('already'), started, charged), ('running', 0, 0))

    def test_a_tracker_that_is_no_longer_in_the_library_is_not_called_delivered(self):
        w = World()
        w.operator_starts(ago_s=50 * 60)
        w.deliver_tracker(ago_s=20 * 60)
        del w.store.objs[(SUBIQ_BUCKET, TRACKER)]
        reply, started, charged = w.approve(naomi_draft())
        self.assertTrue(reply.get('success'))
        self.assertEqual((started, charged), (1, 1))


# ------------------------------------------------------------------ 3
class AProfileBuildFromChat(unittest.TestCase):

    def test_first_approval_runs_and_is_charged_once(self):
        w = World()
        reply, started, charged = w.approve(profile_draft(), as_user='smclain')
        self.assertTrue(reply.get('success'))
        self.assertEqual((started, charged, reply['run_id']), (1, 1, 'RUN001'))
        (entry,) = w.entries()
        self.assertEqual((entry['u'], entry['k'], entry['s'], entry['r'], entry['via']),
                         ('smclain', 'profile', 'trinity tatum', 'RUN001', 'chat'))

    def test_a_second_click_a_minute_later_is_already_running(self):
        w = World()
        w.approve(profile_draft(), as_user='smclain')
        before = list(w.store.writes)
        w.priced.clear()
        reply, started, charged = w.approve(profile_draft(), as_user='smclain')
        assert_untouched(self, w, before, reply, started, charged)
        self.assertEqual(reply['error'], "Trinity Tatum is already running, so I did not start it again. The ready "
                                         "notice comes when it finishes.")

    def test_the_half_hour_rule_still_catches_a_drifted_default_window(self):
        # smclain, 2026-09-29: the same subject on a window six days off ran twice
        w = World()
        w.approve(profile_draft(), as_user='smclain')
        reply, started, charged = w.approve(profile_draft(start='2025-11-11', end='2026-11-11'), as_user='smclain')
        self.assertEqual((reply.get('already'), started, charged), ('running', 0, 0))

    def test_hours_later_and_still_going_is_already_running(self):
        w = World()
        w.approve(profile_draft(), as_user='smclain')
        w.age_entries(3 * 3600)
        w.engine.runs['RUN001']['status'] = 'running'
        w.priced.clear()
        before = list(w.store.writes)
        reply, started, charged = w.approve(profile_draft(), as_user='smclain')
        assert_untouched(self, w, before, reply, started, charged)
        self.assertEqual(reply['already'], 'running')

    def test_hours_later_and_delivered_says_where_it_is(self):
        w = World()
        w.approve(profile_draft(), as_user='smclain')
        w.age_entries(5 * 3600)
        w.engine.finish('RUN001', ago_s=4 * 3600, tu_key='Trinity_Tatum_11_17_2026_10_00.csv')
        w.store.objs[(BUCKET, 'Trinity_Tatum_11_17_2026_10_00.csv')] = b'Column,Value\n'
        w.priced.clear()
        before = list(w.store.writes)
        reply, started, charged = w.approve(profile_draft(), as_user='smclain')
        assert_untouched(self, w, before, reply, started, charged)
        self.assertEqual(reply['error'], "Trinity Tatum is already delivered. It is in Profile IQ under Select "
                                         "Profile, and there is nothing more to approve.")

    def test_subject_match_is_case_and_punctuation_insensitive(self):
        w = World()
        w.approve(profile_draft(), as_user='smclain')
        reply, started, _ = w.approve(profile_draft(subject='trinity-tatum!'), as_user='smclain')
        self.assertEqual((reply.get('already'), started), ('running', 0))

    def test_a_run_that_failed_may_be_placed_again(self):
        w = World()
        w.approve(profile_draft(), as_user='smclain')
        w.engine.runs['RUN001']['status'] = 'failed'
        reply, started, charged = w.approve(profile_draft(), as_user='smclain')
        self.assertTrue(reply.get('success'))
        self.assertEqual((started, charged), (1, 1))

    def test_an_order_the_engine_refused_gives_its_hold_back(self):
        w = World()
        w.engine.refuse = True
        reply, started, charged = w.approve(profile_draft(), as_user='smclain')
        self.assertEqual((reply.get('order_state'), started, charged), ('not_started', 1, 0))
        self.assertEqual(w.entries(), [], 'the hold comes off: "send it again" must work')
        w.engine.refuse = False
        reply, started, charged = w.approve(profile_draft(), as_user='smclain')
        self.assertTrue(reply.get('success'))
        self.assertEqual((started, charged), (1, 1))

    def test_an_order_that_could_not_be_confirmed_and_never_landed_can_be_sent_again(self):
        # "I could not confirm that it started ... if it is not listed, send it again and I will start it."
        w = World()
        key = RA.spec_key('new_build', WORLD_SPEC(profile_draft()))
        hit, token = RA.reserve(key, username='smclain', records=RA.records_for(w.host))
        self.assertTrue(token and hit is None)
        # still on its way to the engine: a second click is already running
        reply, started, _ = w.approve(profile_draft(), as_user='smclain')
        self.assertEqual((reply.get('already'), started), ('running', 0))
        # three minutes on, the engine answers and holds no such run: it never started
        w.age_entries(180)
        reply, started, charged = w.approve(profile_draft(), as_user='smclain')
        self.assertTrue(reply.get('success'), reply)
        self.assertEqual((started, charged), (1, 1))

    def test_when_the_engine_cannot_be_asked_the_half_hour_hold_stands(self):
        w = World()
        key = RA.spec_key('new_build', WORLD_SPEC(profile_draft()))
        RA.reserve(key, username='smclain', records=RA.records_for(w.host))
        w.age_entries(180)
        w._requests.get = lambda *a, **k: (_ for _ in ()).throw(TimeoutError('the read timed out'))
        reply, started, charged = w.approve(profile_draft(), as_user='smclain')
        self.assertEqual((reply.get('already'), started, charged), ('running', 0, 0))

    def test_every_product_an_approval_starts_is_covered(self):
        cut = profile_draft(decision='derive_cut', derive_type='female', cut_label='Female',
                            existing_match_s3_key='Trinity_Tatum_11_17_2026_10_00.csv')
        refresh = profile_draft(decision='time_shifted_refresh', start='2024-01-01', end='2024-12-31',
                                existing_match_s3_key='Trinity_Tatum_11_17_2026_10_00.csv')
        parent_cut = profile_draft(decision='cut_needs_parent', derive_type='gen_z', cut_label='Gen Z')
        for draft, user in ((cut, 'smclain'), (refresh, 'smclain'), (parent_cut, 'smclain'), (naomi_draft(), NAOMI)):
            w = World()
            first, started, charged = w.approve(draft, as_user=user)
            self.assertTrue(first.get('success'), draft['decision'])
            self.assertEqual((started, charged), (1, 1), draft['decision'])
            w.age_entries(2 * 3600)
            w.engine.runs['RUN001']['status'] = 'running'
            again, started, charged = w.approve(draft, as_user=user)
            self.assertEqual((again.get('already'), started, charged), ('running', 0, 0), draft['decision'])


# ------------------------------------------------------------------ 4
class ADifferentOrderRuns(unittest.TestCase):

    def _delivered_world(self):
        w = World()
        w.operator_starts(ago_s=3 * 3600)
        w.deliver_tracker(ago_s=2 * 3600)
        self.assertEqual(w.approve(naomi_draft())[0].get('already'), 'delivered')
        return w

    def _runs(self, w, draft, user=NAOMI):
        reply, started, charged = w.approve(draft, as_user=user)
        self.assertTrue(reply.get('success'), reply)
        self.assertEqual((started, charged), (1, 1))
        self.assertTrue(reply.get('run_id'))

    def test_a_different_window_runs(self):
        w = self._delivered_world()
        sq = subiq_block()
        sq.update({'season_to_date': False, 'through_date': None})      # the full season, through Nov 27
        self._runs(w, naomi_draft(subiq=sq, date_range={'start': '2026-09-18', 'end': '2026-11-27'}))

    def test_a_window_one_day_off_runs_once_the_half_hour_has_passed(self):
        w = self._delivered_world()
        sq = subiq_block()
        sq['through_date'] = '2026-11-19'
        self._runs(w, naomi_draft(subiq=sq, date_range={'start': '2026-09-18', 'end': '2026-11-19'}))

    def test_a_different_platform_runs(self):
        w = self._delivered_world()
        sq = subiq_block()
        sq['platform'] = 'Netflix'
        self._runs(w, naomi_draft(subject='Outlander: Blood of My Blood Viewers on Netflix', subiq=sq))

    def test_a_different_subject_runs(self):
        w = self._delivered_world()
        sq = subiq_block()
        sq.update({'title': 'The Serpent Queen', 'deliverable_label': 'The Serpent Queen - Season 2 to date'})
        self._runs(w, naomi_draft(subject='The Serpent Queen Viewers on Starz', subiq=sq))

    def test_a_different_season_runs(self):
        w = self._delivered_world()
        sq = subiq_block()
        sq.update({'season': 1, 'deliverable_label': 'Outlander: Blood of My Blood - Season 1'})
        self._runs(w, naomi_draft(subiq=sq))

    def test_a_different_product_on_the_same_subject_runs(self):
        w = self._delivered_world()
        self._runs(w, profile_draft(subject='Outlander: Blood of My Blood Viewers on Starz',
                                    start='2026-09-18', end='2026-11-18'))

    def test_profile_builds_differ_by_window_subject_platform_and_cut(self):
        w = World()
        self._runs(w, profile_draft(), 'smclain')
        w.age_entries(2 * 3600)
        w.engine.finish('RUN001', ago_s=3600, tu_key='Trinity_Tatum_11_17_2026_10_00.csv')
        w.store.objs[(BUCKET, 'Trinity_Tatum_11_17_2026_10_00.csv')] = b'x'
        self.assertEqual(w.approve(profile_draft(), as_user='smclain')[0].get('already'), 'delivered')
        self._runs(w, profile_draft(start='2024-01-01', end='2024-12-31'), 'smclain')            # another window
        self._runs(w, profile_draft(subject='Hoshimachi Suisei'), 'smclain')                    # another subject
        self._runs(w, profile_draft(platform_scope=['Hulu']), 'smclain')                        # another platform
        self._runs(w, profile_draft(addon_cuts=[{'cut_id': 'female', 'label': 'Female'}]), 'smclain')   # with a cut

    def test_two_different_cuts_of_one_parent_are_two_orders_even_a_minute_apart(self):
        w = World()
        base = dict(decision='derive_cut', existing_match_s3_key='Trinity_Tatum_11_17_2026_10_00.csv')
        self._runs(w, profile_draft(derive_type='female', cut_label='Female', **base), 'smclain')
        self._runs(w, profile_draft(derive_type='male', cut_label='Male', **base), 'smclain')
        again, started, _ = w.approve(profile_draft(derive_type='female', cut_label='Female', **base), 'smclain')
        self.assertEqual((again.get('already'), started), ('running', 0))

    def test_another_account_with_no_shared_wallet_runs(self):
        w = self._delivered_world()
        self._runs(w, naomi_draft(), 'elsewhere')
        w2 = World()
        self._runs(w2, profile_draft(), 'smclain')
        self._runs(w2, profile_draft(), 'scott')


# ------------------------------------------------------------------ 5
class OutsideTheDay(unittest.TestCase):

    def test_the_identical_order_a_day_later_runs(self):
        w = World()
        w.operator_starts(ago_s=26 * 3600)
        w.deliver_tracker(ago_s=25 * 3600)
        reply, started, charged = w.approve(naomi_draft())
        self.assertTrue(reply.get('success'))
        self.assertEqual((started, charged), (1, 1))

    def test_inside_the_day_it_does_not(self):
        w = World()
        w.operator_starts(ago_s=23 * 3600)
        w.deliver_tracker(ago_s=22 * 3600)
        reply, started, charged = w.approve(naomi_draft())
        self.assertEqual((reply.get('already'), started, charged), ('delivered', 0, 0))

    def test_the_window_is_a_day_and_the_half_hour_rule_is_half_an_hour(self):
        self.assertEqual((RA.WINDOW_S, RA.RESERVE_S, RA.DRIFT_DAYS), (24 * 3600, 30 * 60, 21))

    def test_a_profile_build_from_chat_a_day_later_runs(self):
        w = World()
        w.approve(profile_draft(), as_user='smclain')
        w.age_entries(25 * 3600)
        w.engine.finish('RUN001', ago_s=24.5 * 3600, tu_key='Trinity_Tatum_11_17_2026_10_00.csv')
        w.store.objs[(BUCKET, 'Trinity_Tatum_11_17_2026_10_00.csv')] = b'x'
        reply, started, charged = w.approve(profile_draft(), as_user='smclain')
        self.assertTrue(reply.get('success'))
        self.assertEqual((started, charged), (1, 1))

    def test_past_the_half_hour_an_entry_with_nothing_behind_it_does_not_block(self):
        # the old guard's contract: its hold lasts thirty minutes and no longer
        w = World()
        key = RA.spec_key('new_build', WORLD_SPEC(profile_draft()))
        recs = RA.records_for(w.host)
        self.assertEqual(RA.reserve(key, username='smclain', records=recs)[0], None)
        w.age_entries(3600)
        reply, started, charged = w.approve(profile_draft(), as_user='smclain')
        self.assertTrue(reply.get('success'))
        self.assertEqual((started, charged), (1, 1))


def WORLD_SPEC(draft):
    return World._spec_from_draft(draft)


# ------------------------------------------------------------------ 6
class ItFailsOpen(unittest.TestCase):

    def _first(self, w, user=NAOMI, draft=None):
        reply, started, charged = w.approve(draft or naomi_draft(), as_user=user)
        self.assertTrue(reply.get('success'), reply)
        self.assertEqual((started, charged), (1, 1))

    def test_the_engine_cannot_be_reached_for_the_lookup(self):
        w = World()
        w.operator_starts(ago_s=50 * 60)
        w.deliver_tracker(ago_s=20 * 60)
        real_get = w.engine.get
        w._requests.get = lambda *a, **k: (_ for _ in ()).throw(TimeoutError('the read timed out'))
        self._first(w)
        w._requests.get = real_get

    def test_one_failed_engine_lookup_is_not_followed_by_a_second_wait(self):
        w = World()
        w.operator_starts(ago_s=50 * 60)
        calls = []

        def _down(url, **_k):
            calls.append(url)
            raise TimeoutError('the read timed out')
        w._requests.get = _down
        self._first(w)
        self.assertEqual(len(calls), 1)

    def test_the_store_cannot_be_read(self):
        w = World()
        w.operator_starts(ago_s=50 * 60)
        w.deliver_tracker(ago_s=20 * 60)
        w.engine.runs.clear()                 # and the engine has nothing either
        real = w.store.get_object
        w.store.get_object = lambda **k: (_ for _ in ()).throw(RuntimeError('store unreachable'))
        self._first(w)
        w.store.get_object = real
        self.assertEqual(len(w.entries()), 1, 'a record that could not be read is never written over')

    def test_the_users_file_cannot_be_read(self):
        w = World()
        w.users_down = True
        self._first(w)
        # and the account itself is still checked
        again, started, _ = w.approve(naomi_draft())
        self.assertEqual((again.get('already'), started), ('running', 0))

    def test_everything_is_down_at_once(self):
        w = World()
        w.users_down = True
        w._requests.get = lambda *a, **k: (_ for _ in ()).throw(ConnectionError('refused'))
        real = w.store.get_object
        w.store.get_object = lambda **k: (_ for _ in ()).throw(RuntimeError('store unreachable'))
        self._first(w, user='smclain', draft=profile_draft())
        w.store.get_object = real

    def test_the_check_itself_raising_never_blocks(self):
        real = (RA.find_prior, RA.spec_key, RA.reserve)
        for name in ('find_prior', 'spec_key', 'reserve'):
            w = World()
            if name != 'reserve':           # the hold is only reached by an order the check let through
                w.operator_starts(ago_s=50 * 60)
                w.deliver_tracker(ago_s=20 * 60)
            w.engine.stamp = False          # the fake engine's own stamp uses the same function
            setattr(RA, name, lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
            try:
                reply, started, charged = w.approve(naomi_draft())
            finally:
                RA.find_prior, RA.spec_key, RA.reserve = real
            self.assertTrue(reply.get('success'), name)
            self.assertEqual((started, charged), (1, 1), name)

    def test_find_prior_never_raises(self):
        key = RA.spec_key('subscriber_iq', operator_spec())
        self.assertIsNone(RA.find_prior(key, username=NAOMI, records=RA.Records()))
        self.assertIsNone(RA.find_prior(key, username=NAOMI, records=None))
        self.assertIsNone(RA.find_prior(None, username=NAOMI, records=RA.Records()))
        self.assertEqual(RA.reserve(key, username=NAOMI, records=RA.Records()), (None, ''))

    def test_a_run_the_engine_does_not_know_is_not_a_block(self):
        w = World()
        RA.note_operator_start(w.host, username=NAOMI, run_id='GONE', decision='subscriber_iq', spec=operator_spec(),
                               now=time.time() - 3 * 3600)
        self._first(w)


# ------------------------------------------------------------------ 7
class NothingInTheRequestSkipsIt(unittest.TestCase):
    FLAGS = ('force', 'force_new', 'force_new_build', 'force_refresh', 'force_run', 'rerun', 'run_again', 'again',
             'override', 'bypass', 'bypass_cache', 'bypass_guard', 'skip_guard', 'skip_check', 'no_guard', 'mode',
             'reuse_only', 'duplicate_ok', 'allow_duplicate', 'confirm', 'confirmed', '_pm_force', '_pm_rerun',
             '_pm_skip_repeat', '_pm_no_auto_start', '_pm_order_text', 'probe', 'canary', 'test', 'dry_run')

    def test_no_request_field_starts_a_second_run(self):
        w = World()
        w.operator_starts(ago_s=50 * 60)
        w.deliver_tracker(ago_s=20 * 60)
        for flag in self.FLAGS:
            for value in (True, 1, 'true', 'yes', 'new_build'):
                reply, started, charged = w.approve(naomi_draft(), **{flag: value})
                self.assertEqual((reply.get('already'), started, charged), ('delivered', 0, 0), (flag, value))
        reply, started, charged = w.approve(naomi_draft(), **{f: True for f in self.FLAGS})
        self.assertEqual((reply.get('already'), started, charged), ('delivered', 0, 0))

    def test_a_flag_inside_the_brief_does_not_either(self):
        w = World()
        w.operator_starts(ago_s=50 * 60)
        w.deliver_tracker(ago_s=20 * 60)
        for flag in self.FLAGS:
            reply, started, charged = w.approve(naomi_draft(**{flag: True}))
            self.assertEqual((reply.get('already'), started, charged), ('delivered', 0, 0), flag)

    def test_run_avid_and_the_email_field_do_not_make_it_a_different_order(self):
        w = World()
        w.approve(profile_draft(), as_user='smclain')
        for body in ({'run_avid': False}, {'email_to': 'someone@agency.example'}, {'thread_id': 'another'}):
            reply, started, _ = w.approve(profile_draft(), as_user='smclain', **body)
            self.assertEqual((reply.get('already'), started), ('running', 0), body)

    def test_a_request_that_calls_itself_a_probe_is_still_checked(self):
        w = World()
        w.operator_starts(ago_s=50 * 60)
        w.deliver_tracker(ago_s=20 * 60)
        w.probe_caller = 'canary'            # the X-Prometheus-Caller header, which any client can send
        w.faults.probe = True
        reply, started, charged = w.approve(naomi_draft())
        self.assertEqual((reply.get('already'), started, charged), ('delivered', 0, 0))

    def test_the_check_reads_nothing_from_the_request_body(self):
        i = APPROVE_SRC.index('# ---- A repeat approval is answered, never run twice')
        j = APPROVE_SRC.index('# ---- Credit pricing + preflight')
        self.assertLess(i, j, 'the check sits before pricing')
        block = APPROVE_SRC[i:j]
        self.assertNotIn('body.get', block)
        self.assertNotIn('body[', block)
        self.assertNotIn('request.', block)
        self.assertIn('_rpa.check(decision, spec, draft, d_type, ex_key)', block)   # the brief, and nothing else
        mod = (ROOT / 'prometheus' / 'repeat_approval.py').read_text(encoding='utf-8')
        gate = mod[mod.index('class Gate:'):mod.index('def legacy_hold(')]
        for word in ('body', 'request', 'session', 'headers', 'environ'):
            self.assertNotIn(word, re.sub(r'"""(?:.|\n)*?"""', '', gate), word)
        code = re.sub(r'"""(?:.|\n)*?"""', '', mod)
        for word in ('force', 'bypass', 'override=', 'skip=', 'os.environ', 'getenv', 'request.', 'flask'):
            self.assertNotIn(word, code, word)

    def test_the_check_comes_before_the_charge_and_the_queue(self):
        order = [APPROVE_SRC.index(s) for s in ('_rpa.check(decision, spec, draft, d_type, ex_key)',
                                                '_H.has_credits_for(', '_rpa.hold()', '/synth/queue',
                                                '_rpa.started(_session_run_id)', '_H.consume_credit(')]
        self.assertEqual(order, sorted(order))
        self.assertEqual(APPROVE_SRC.count('_rpa.not_started()'), 2, 'both ways an order can fail to start')
        self.assertLess(_legacy_lines(), _LEGACY_CEILING, 'the legacy chat code stays under its line ceiling')
        self.assertNotIn("if decision == 'new_build':\n        _dup_dr", APPROVE_SRC, 'the old new-build-only block')
        self.assertEqual(CHAT.count('already building from your request'), 0, 'one guard, one reply')


# ------------------------------------------------------------------ 8
class WhatTheReaderIsTold(unittest.TestCase):

    def _replies(self):
        out = []
        for kind, name in (('subiq', LABEL), ('profile', 'Trinity Tatum'), ('refresh', 'Trinity Tatum'),
                           ('cut', 'Trinity Tatum - Female'), ('profile', 'The $100,000 Pyramid Viewers'),
                           ('profile', 'Spider\u2014Man \u2013 Fans'), ('profile', '')):
            for state in ('running', 'delivered'):
                out.append((kind, state, RA.reply({'state': state, 'kind': kind, 'name': name})))
        return out

    def test_the_exact_words(self):
        self.assertEqual(RA.reply({'state': 'running', 'kind': 'subiq', 'name': LABEL}),
                         f"{LABEL} is already running, so I did not start it again. The ready notice comes when it "
                         "finishes.")
        self.assertEqual(RA.reply({'state': 'delivered', 'kind': 'subiq', 'name': LABEL}),
                         f"{LABEL} is already delivered. It is in the Subscriber IQ tab, and there is nothing more "
                         "to approve.")
        self.assertEqual(RA.reply({'state': 'delivered', 'kind': 'profile', 'name': 'Trinity Tatum'}),
                         "Trinity Tatum is already delivered. It is in Profile IQ under Select Profile, and there "
                         "is nothing more to approve.")

    def test_no_price_no_internal_word_no_long_dash(self):
        for kind, state, text in self._replies():
            where = (kind, state, text)
            # what we wrote around the name: the name is the reader's own title and may carry a figure of its own
            ours = text.replace('The $100,000 Pyramid Viewers', '').replace(LABEL, '')
            self.assertNotIn('$', ours, where)
            self.assertIsNone(re.search(r"\d", ours), where)             # no amount, no count, no id
            self.assertNotRegex(text, r"(?i)\b(?:credits?|dollars?|usd|price|cost|fee|charged?|refund)\b", where)
            for dash in ('\u2014', '\u2013'):
                self.assertNotIn(dash, text, where)
            for word in BANNED:
                self.assertNotIn(word, ours.lower(), (word,) + where)
            self.assertLessEqual(len(re.findall(r"[.!?](?:\s|$)", ours)), 2, where)
            self.assertTrue(text.endswith('.'), where)

    def test_it_names_the_tab_the_reader_sees(self):
        for kind, state, text in self._replies():
            if state == 'delivered':
                self.assertIn('Subscriber IQ tab' if kind == 'subiq' else 'Profile IQ under Select Profile', text)
                self.assertIn('nothing more to approve', text)
            else:
                self.assertIn('already running', text)
                self.assertIn('ready notice', text)

    def test_the_approve_reply_carries_nothing_else_a_reader_could_see(self):
        w = World()
        w.operator_starts(ago_s=50 * 60)
        w.deliver_tracker(ago_s=20 * 60)
        reply, _, _ = w.approve(naomi_draft())
        self.assertEqual(sorted(reply), ['already', 'error', 'followups', 'guidance', 'subject', 'success'])
        blob = json.dumps(reply).lower()
        for word in ('run_id', RUN.lower(), 'operator', 'credits', 'usd', 'price', '$'):
            self.assertNotIn(word, blob, word)


# ------------------------------------------------------------------ 9
class SeenHoweverItStarted(unittest.TestCase):

    def test_the_operator_path_leaves_the_order_and_the_thread_on_record(self):
        w = World()
        got = w.operator_starts(thread_id='t-naomi')
        self.assertEqual(got, {'guard': True, 'launch': True})
        (entry,) = w.entries()
        self.assertEqual((entry['u'], entry['k'], entry['p'], entry['ws'], entry['we'], entry['r'], entry['via']),
                         (NAOMI, 'subiq', 'starz', '2026-09-18', '2026-11-18', RUN, 'operator'))
        launch = w.store.get_json(f"{BR.PREFIX}{RUN}.json")
        self.assertEqual((launch['user'], launch['thread_id'], launch['decision'], launch['via'], launch['engine_user'],
                          launch['status']), (NAOMI, 't-naomi', 'subscriber_iq', 'operator', NAOMI_EMAIL, 'open'))

    def test_the_finished_line_then_posts_into_her_chat(self):
        w = World()
        w.operator_starts(thread_id='t-naomi')
        w.deliver_tracker()
        threads = {(NAOMI, 't-naomi'): [{'role': 'user', 'text': 'Pull Subscriber IQ for Outlander'}]}
        out = BR.sweep(
            store_list=lambda prefix: [k for (b, k) in w.store.objs if b == BUCKET and k.startswith(prefix)],
            store_get=lambda key: w.store.get_json(key), store_put=lambda key, doc: w.store.put_json(key, doc),
            runs_for=lambda user: [d for d in w.engine.runs.values() if d.get('user_email') == user],
            exists=lambda u, t: (u, t) in threads, load=lambda u, t: threads[(u, t)],
            save=lambda u, t, turns: threads.__setitem__((u, t), turns), log=None)
        self.assertEqual(out, [(RUN, 'posted')])
        self.assertIn('Subscriber IQ tab', threads[(NAOMI, 't-naomi')][-1]['text'])

    def test_active_reads_the_open_chat_and_writes_no_thread(self):
        w = World()
        w.host._pm_threads_index_key = lambda u: f"synth_chat_threads/{u}/index.json"
        w.store.put_json(f"synth_chat_threads/{NAOMI}/index.json", {'active': 't-live', 'threads': [{'id': 't-live'}]})
        got = w.operator_starts(thread_id='active')
        self.assertTrue(got['launch'])
        self.assertEqual(w.store.get_json(f"{BR.PREFIX}{RUN}.json")['thread_id'], 't-live')
        self.assertFalse([k for k in w.store.writes if k.startswith('synth_chat_threads/')])

    def test_without_a_thread_only_the_order_is_recorded(self):
        w = World()
        self.assertEqual(w.operator_starts(), {'guard': True, 'launch': False})
        self.assertEqual(w.store.writes, [RA.STORE_KEY])

    def test_a_run_from_the_partner_route_is_seen_through_the_engine_record(self):
        # POST /api/v1/profiles/run leaves the run under the key seat's address with the engine's own key on it
        w = World()
        spec = World._spec_from_draft(profile_draft())
        w.engine.add('APIRUN1', decision='new_build', user_email='smclain@agency.example', subject=spec['name'],
                     order_key=RA.spec_key('new_build', spec))
        reply, started, charged = w.approve(profile_draft(), as_user='smclain')
        self.assertEqual((reply.get('already'), started, charged), ('running', 0, 0))
        w.engine.finish('APIRUN1', ago_s=600, tu_key='Trinity_Tatum_11_17_2026_10_00.csv')
        w.store.objs[(BUCKET, 'Trinity_Tatum_11_17_2026_10_00.csv')] = b'x'
        reply, started, charged = w.approve(profile_draft(), as_user='smclain')
        self.assertEqual((reply.get('already'), started, charged), ('delivered', 0, 0))
        self.assertEqual(w.entries(), [], 'seen with nothing on the guard record at all')
        # and its other window is still another order
        reply, started, charged = w.approve(profile_draft(start='2024-01-01', end='2024-12-31'), as_user='smclain')
        self.assertTrue(reply.get('success'))

    def test_a_run_listed_under_another_identity_is_placed_by_the_run_owners(self):
        w = World()
        spec = World._spec_from_draft(profile_draft())
        w.engine.add('APIRUN2', decision='new_build', user_email='integration@vendor.example', subject=spec['name'],
                     order_key=RA.spec_key('new_build', spec))
        first, started, _ = w.approve(profile_draft(), as_user='smclain')
        self.assertEqual(started, 1, 'a stranger\'s run is not this account\'s order')
        w2 = World()
        w2.engine.add('APIRUN2', decision='new_build', user_email='integration@vendor.example', subject=spec['name'],
                      order_key=RA.spec_key('new_build', spec))
        w2.store.put_json(RA.OWNERS_KEY, {'APIRUN2': 'smclain', 'OTHER': 'scott'})
        reply, started, charged = w2.approve(profile_draft(), as_user='smclain')
        self.assertEqual((reply.get('already'), started, charged), ('running', 0, 0))

    def test_a_seat_on_the_same_company_wallet_is_answered_too(self):
        w = World()
        w.operator_starts(ago_s=50 * 60)
        w.deliver_tracker(ago_s=20 * 60)
        reply, started, charged = w.approve(naomi_draft(), as_user='colleague')
        self.assertEqual((reply.get('already'), started, charged), ('delivered', 0, 0))
        # the company's key seat started it on the partner route; Naomi approves the same brief in chat
        w2 = World()
        spec = World._spec_from_draft(profile_draft())
        w2.engine.add('APIRUN3', decision='new_build', user_email='api@studio.example', subject=spec['name'],
                      order_key=RA.spec_key('new_build', spec))
        reply, started, charged = w2.approve(profile_draft(), as_user=NAOMI)
        self.assertEqual((reply.get('already'), started, charged), ('running', 0, 0))

    def test_the_chat_the_operator_and_the_engine_arrive_at_one_key(self):
        chat = World._spec_from_draft(naomi_draft())
        chat['subiq'] = subiq_block()
        a = RA.spec_key('subscriber_iq', chat, naomi_draft())
        b = RA.spec_key('subscriber_iq', operator_spec())
        self.assertEqual(a, b)
        self.assertEqual((a['kind'], a['subject'], a['platform'], a['ws'], a['we']),
                         ('subiq', 'outlander blood of my blood season 2', 'starz', '2026-09-18', '2026-11-18'))
        self.assertEqual(RA.window_of('2026-09-18 TO 2026-11-18'), RA.window_of({'start': '2026-09-18', 'end': '2026-11-18'}))
        self.assertEqual(RA.norm("Conan O'Brien: Must-Go"), RA.norm('conan obrien must go'))


# ------------------------------------------------------------------ 10
class ProbesAndTheOperatorPath(unittest.TestCase):

    def test_a_probe_identity_runs_as_before_and_leaves_nothing_behind(self):
        w = World()
        reply, started, charged = w.approve(profile_draft(), as_user='canary:golden')
        self.assertTrue(reply.get('success'))
        self.assertEqual(started, 1)
        self.assertFalse([k for k in w.store.writes if k == RA.STORE_KEY], 'no entry on the guard record')
        self.assertEqual(w.ops_mail, [], 'and no mail')
        w.probe_caller = 'smoke'
        reply, started, _ = w.approve(profile_draft(subject='Another Subject'), as_user='smclain')
        self.assertTrue(reply.get('success'))
        self.assertFalse([k for k in w.store.writes if k == RA.STORE_KEY])

    def test_the_module_sends_no_mail_and_starts_nothing(self):
        mod = (ROOT / 'prometheus' / 'repeat_approval.py').read_text(encoding='utf-8')
        code = re.sub(r'"""(?:.|\n)*?"""', '', mod)
        for word in ('send_email', 'send_raw_email', 'outbound_mail', "client('ses'", 'consume_credit',
                     'refund_credit', '.post(', '/synth/queue', '/synth/cancel', 'delete_object'):
            self.assertNotIn(word, code, word)
        self.assertNotIn('\u2014', mod)
        self.assertNotIn('\u2013', mod)

    def _parent(self):
        named = os.environ.get('PM_PARENT_REPO')
        for up in ([Path(named)] if named else []) + list(ROOT.parents):
            if (up / 'migration' / 'local_override_profile.py').exists() and (up / 'migration' / 'synth_queue_worker.py').exists():
                return up
        return None

    def test_the_operator_path_records_what_it_starts_and_is_never_refused(self):
        parent = self._parent()
        if parent is None:
            self.skipTest('the parent repo is not laid out around this checkout')
        src = (parent / 'migration' / 'local_override_profile.py').read_text(encoding='utf-8')
        self.assertIn('note_operator_start(', src)
        self.assertIn('"--thread-id"', src)
        for word in ('find_prior', '.reserve(', 'repeat_approval.payload'):
            self.assertNotIn(word, src, 'the operator path is the override: it never asks the check')
        self.assertNotIn('\u2014', src)

    def test_the_operator_script_puts_its_run_on_the_seats_record(self):
        parent = self._parent()
        if parent is None:
            self.skipTest('the parent repo is not laid out around this checkout')
        import importlib.util
        spec = importlib.util.spec_from_file_location('lop_under_test', parent / 'migration' / 'local_override_profile.py')
        lop = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lop)
        w = World()
        payload = {'user_email': NAOMI_EMAIL, 'username': NAOMI, 'decision': 'subscriber_iq'}
        got = lop.note_started_for_seat(NAOMI, RUN, 'subscriber_iq', operator_spec(), payload, thread_id='t-naomi',
                                        app=w.host)
        self.assertEqual(got, {'guard': True, 'launch': True})
        w.engine.add(RUN, decision='subscriber_iq', user_email=NAOMI_EMAIL, subject=operator_spec()['name'])
        reply, started, charged = w.approve(naomi_draft())
        self.assertEqual((reply.get('already'), started, charged), ('running', 0, 0))
        # and the script queues a second identical run when an operator asks for one: it posts to the engine
        # itself and nothing between its argument parsing and that post reads the record
        src = (parent / 'migration' / 'local_override_profile.py').read_text(encoding='utf-8')
        main = src[src.index('def main() -> int:'):]
        self.assertLess(main.index('f"{queue_url}/synth/queue"'), main.index('note_started_for_seat('))

    def test_the_engine_stamps_the_order_key_on_every_run(self):
        parent = self._parent()
        if parent is None:
            self.skipTest('the parent repo is not laid out around this checkout')
        src = (parent / 'migration' / 'synth_queue_worker.py').read_text(encoding='utf-8')
        self.assertIn('from prometheus.repeat_approval import spec_key as _spec_key', src)
        self.assertIn('status["order_key"] = _okey', src)
        i, j = src.index('    status = {\n        "run_id": run_id,\n        "status": "running",'), src.index('status["order_key"] = _okey')
        self.assertLess(i, j)
        fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == '_order_key_for')
        ns = {'os': os, 'sys': sys, '__file__': str(parent / 'migration' / 'synth_queue_worker.py')}
        exec(ast.get_source_segment(src, fn), ns)
        job = {'run_id': RUN, 'decision': 'subscriber_iq', 'spec': operator_spec()}
        self.assertEqual(ns['_order_key_for'](job, job['spec'], 'subscriber_iq'), RA.spec_key('subscriber_iq', operator_spec()))
        cut = {'run_id': 'c1', 'derive_type': 'female', 'parent_s3_key': 'Trinity_Tatum_11_17_2026_10_00.csv',
               'cut_label': 'Female'}
        spec = WORLD_SPEC(profile_draft())
        self.assertEqual(ns['_order_key_for'](cut, spec, 'derive_cut'),
                         RA.spec_key('derive_cut', spec, derive_type='female', cut_label='Female',
                                     parent_key='Trinity_Tatum_11_17_2026_10_00.csv'))
        self.assertIsNone(ns['_order_key_for']({'run_id': 'x'}, None, 'new_build'))

    def test_the_partner_route_contract_is_untouched(self):
        app = (ROOT / 'app.py').read_text(encoding='utf-8')
        self.assertNotIn('repeat_approval', app)
        i = app.index('def _scrub_v1_status(')
        self.assertNotIn('order_key', app[i:i + 6000], 'the run key never reaches a partner')


if __name__ == '__main__':
    unittest.main(verbosity=1)
