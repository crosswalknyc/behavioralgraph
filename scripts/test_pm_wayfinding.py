#!/usr/bin/env python3
"""Wayfinding (Jenna 2026-11-10): "it someone asks something like this it
should tell them where to find it. like go to the drop down menu and
click on digital journey iq kinda thing".

The case: a Sony Television seat asked "where is the digital jounrey in
the dashboard?" and got "Your read for Select a Journey finished 5
minutes ago. Here it is:" with a whole Walter Boys read under it.

Covers, hermetically (fake S3, bound host, no model):
  * the Sony question routes to the wayfinding lane and names the
    SELECT PRODUCT dropdown, Digital Journey IQ and Select a journey;
  * a seat without access gets the ask-your-admin line and no chip;
  * 'where is my Walter Boys journey' names the journey the seat holds;
  * 'where is pilot testing' names the Digital Twins IQ tab;
  * a data question, a named deliverable and the reader's own results
    are not this lane's;
  * a finished read is never glued onto the reply to another question;
    the one trailing line appears only when the landed turn is not
    visible in the copy the client sent;
  * a picker placeholder never becomes a subject label;
  * the lane is registered early, deterministic and never priced; the
    widget carries the Open chip switch and the landed-since pull.
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

from prometheus import dashboard_map as M  # noqa: E402
from prometheus import wayfinding as W  # noqa: E402
from prometheus import read_landing as RL  # noqa: E402
from prometheus import status_lane as STL  # noqa: E402
from prometheus import service as S  # noqa: E402
from prometheus import subject_rules as SR  # noqa: E402
from prometheus import thread_binding as TB  # noqa: E402
from prometheus import view_base as VB  # noqa: E402
from prometheus import envelope as ENV  # noqa: E402
from prometheus.host import host  # noqa: E402

USER = 'sonytv'
BUCKET = 'b'
THREADS = 'system/synth_chat_threads'
READS = 'system/prometheus_reads/'
SONY_Q = 'where is the digital jounrey in the dashboard?'
WALTER = 'My Life with the Walter Boys (Season 3 cut vs full viewers universe)'
READ_TEXT = ('On My Life with the Walter Boys (Season 3 cut vs full viewers universe) - Season 3 watchers:\n\n'
             'It is in the picker now: the Season 3 cut is 8,146,329 viewers, 59.3% of the 13,742,586 full universe.')
BANNED = ('synth', 'model', 'panel', 'pipeline', 'queue', 'claude', 'hostmap', 'anthropic', 'estimated', '\u2014', '\u2013')


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

    def list_objects_v2(self, Bucket, Prefix='', MaxKeys=1000, ContinuationToken=None):
        keys = sorted(k for k in self.objs if k.startswith(Prefix))
        return {'Contents': [{'Key': k, 'ETag': '"x"'} for k in keys], 'IsTruncated': False}


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
              ask_hint=lambda **kw: None, max_threads=40, read_prefix=READS)
    s3_put_json(threads_index_key(USER), idx)
    for t in idx['threads']:
        if thread_key(USER, t['id']) not in s3.objs:
            s3_put_json(thread_key(USER, t['id']), [])


def _idx():
    return {'active': 'thr1', 'threads': [
        {'id': 'thr1', 'title': 'fragrance tiktok', 'created': 'x', 'updated': 'x', 'turns': 4},
        {'id': 'thr2', 'title': 'Dark Matter Season 2', 'created': 'x', 'updated': 'x', 'turns': 2}]}


def _user(**flags):
    u = {'username': USER, 'role': 'user', 'company': 'Sony Television Group'}
    u.update(flags)
    return u


SONY_USER = _user(has_profile_iq_access=True, has_journey_iq_access=True, has_digital_twins_iq_access=True, has_intent_iq_access=True)


def _clean(tc, text):
    low = text.lower()
    for bad in BANNED:
        tc.assertNotIn(bad, low, (bad, text))


# ------------------------------------------------------------------ routing

class Routing(unittest.TestCase):
    def test_sony_question_and_its_shapes_want_wayfinding(self):
        for t in (SONY_Q, 'where is the digital journey in the dashboard', 'where do I find digital journey iq', 'how do i get to digital journey iq?',
                  'which tab is digital journey iq under', 'where is profile iq', 'where can I see my twins', 'where is attribution iq',
                  'where do I find the select product dropdown', 'where are my old chats', 'how do I open digital twins iq', 'where is pilot testing',
                  'where is my Walter Boys journey', 'where does a finished journey show up', 'where do I download the csv'):
            self.assertTrue(W.wants(t), t)

    def test_not_wayfinding(self):
        for t in ('where did the viewers come from', 'where is the fall-off from the ticket step', 'where is the howdy deck created last night',
                  'where are my results', 'where do i see the answer', 'how many people watched Gilmore Girls on Hulu', 'where is my list',
                  'what did Landman viewers watch next?', 'where do Walter Boys viewers shop', 'build a twin of Gilmore Girls viewers',
                  'Can you look into My Life With the Walter Boys viewers? I want to add them as a profile'):
            self.assertFalse(W.wants(t), t)

    def test_targets(self):
        t = W.target_of(SONY_Q)
        self.assertEqual(t['kind'], 'product')
        self.assertEqual(t['product']['value'], 'journeyIQ')
        self.assertEqual(t.get('name'), '')
        t = W.target_of('where is my Walter Boys journey')
        self.assertEqual((t['kind'], t['noun'], t['name']), ('artifact', 'journey', 'Walter Boys'))
        t = W.target_of('where is pilot testing')
        self.assertEqual((t['kind'], t['tab']['id'], t['product']['value']), ('tab', 'pilot', 'digitalTwinsIQ'))
        t = W.target_of('where do I find focus group iq')
        self.assertEqual(t['tab']['label'], 'Focus Group IQ')
        self.assertEqual(W.target_of('where is the select product dropdown')['kind'], 'dropdown')
        self.assertEqual(W.target_of('where are my old chats')['kind'], 'rail')
        self.assertEqual(W.target_of('where can i see digital twins')['kind'], 'product')

    def test_map_names_the_dropdown_as_the_page_shows_it(self):
        labels = [p['label'] for p in M.PRODUCTS]
        for want in ('Profile IQ', 'Subscriber IQ', 'Digital Journey IQ', 'Digital Twins IQ', 'Attribution IQ', 'Trends', 'Rankers',
                     'Brand Partnership IQ', 'Flywheel', 'Micro-Series', 'Fin IQ', 'Brand Tracking', 'SF Conversion', 'Flywheel Conversion', 'Blue IQ'):
            self.assertIn(want, labels)
        self.assertEqual([t[2] for t in M.TABS if t[0] == 'digitalTwinsIQ'],
                         ['Twins', 'Greenlight', 'Launch', 'Focus Group IQ', 'Pilot Testing', 'Trailers', 'Titles and Key Art', 'Norms'])
        self.assertEqual(M.picker_of('journeyIQ'), 'Select a journey')
        self.assertEqual(M.picker_of('profileIQ'), 'Select Profile')
        self.assertEqual(M.deep_link('journeyIQ'), 'https://dashboard.crosswalknyc.com/?view=journeyIQ')
        self.assertEqual(M.deep_link('digitalTwinsIQ', tab='pilot'), 'https://dashboard.crosswalknyc.com/?view=digitalTwinsIQ&tab=pilot')

    def test_access_mirrors_the_template(self):
        acc = M.access_for(_user())
        self.assertEqual(M.can_open('journeyIQ', acc), 'no_access')
        self.assertEqual(M.can_open('profileIQ', acc), 'open')          # the template's default
        self.assertEqual(M.can_open('intentIQ', acc), 'open')
        self.assertEqual(M.can_open('journeyIQ', M.access_for(_user(role='admin'))), 'open')
        self.assertEqual(M.can_open('journeyIQ', M.access_for(_user(analysis_iq_modules=['journey_iq']))), 'open')
        self.assertEqual(M.can_open('digitalTwinsIQ', M.access_for(_user(has_digital_twins_iq_access=True)), {'digitalTwinsIQ': True}), 'hidden')
        self.assertEqual(M.can_open('digitalTwinsIQ', M.access_for(_user(has_digital_twins_iq_access=True)), locked=True), 'locked')
        self.assertEqual(M.can_open('brandPartnershipIQ', M.access_for(_user(analysis_iq_modules=['brand_partnership_iq']))), 'open')
        self.assertEqual(M.can_open('admin', M.access_for(_user())), 'no_access')
        for p in M.PRODUCTS:
            self.assertEqual(M.can_open(p['value'], M.access_for(_user(role='super_admin'))), 'open', p['value'])


# ------------------------------------------------------------------ answers

class Answers(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        _bind(self.s3, _idx())
        self._persist = S._persist_turn
        S._persist_turn = lambda *a, **k: None

    def tearDown(self):
        S._persist_turn = self._persist

    def _ask(self, text, user=None, history=None, **extra):
        body = dict({'text': text, 'client': 'dashboard', 'surface': 'analyze', 'thread_id': 'thr1',
                     'history': history if history is not None else []}, **extra)
        return S.ask(user or SONY_USER, body)

    def test_sony_question_gets_directions_not_a_read(self):
        env, status = self._ask(SONY_Q)
        self.assertEqual(status, 200)
        self.assertEqual((env.get('decision') or {}).get('reason'), 'wayfinding')
        raw = env['raw']
        reply = raw['reply']
        _clean(self, reply)
        self.assertIn('SELECT PRODUCT dropdown at the top right', reply)
        self.assertIn('Digital Journey IQ', reply)
        self.assertIn('Select a journey', reply)
        self.assertIn('?view=journeyIQ', reply)
        self.assertNotIn('Here it is', reply)
        self.assertNotIn('Your read for', reply)
        self.assertNotIn('minutes ago', reply)
        self.assertNotIn('Select a Journey', reply.replace('Select a journey', ''))
        self.assertEqual(raw['followups'], ['Open Digital Journey IQ'])
        self.assertEqual(raw['action_hint'], {'view': 'journeyIQ', 'label': 'Open Digital Journey IQ'})
        self.assertLessEqual(reply.count('. ') + 1, 5)
        self.assertEqual(env['kind'], 'answer')

    def test_no_access_gets_the_admin_line_and_no_chip(self):
        out = W.answer(_user(), SONY_Q, holdings_fn=lambda n, nm: {'items': [], 'found': []})
        self.assertIn('ask your admin for access', out['reply'])
        self.assertIn('Digital Journey IQ', out['reply'])
        self.assertNotIn('Open the SELECT PRODUCT', out['reply'])
        self.assertEqual(out['followups'], [])
        self.assertNotIn('action_hint', out)
        hidden = W.answer(SONY_USER, SONY_Q, hidden={'journeyIQ': True}, holdings_fn=lambda n, nm: {'items': [], 'found': []})
        self.assertIn('not on the dashboard right now', hidden['reply'])
        self.assertEqual(hidden['followups'], [])

    def test_named_journey_the_seat_holds(self):
        held = {'items': [{'name': 'My Life with the Walter Boys', 'key': 'journey-iq/sonytv/walter.json.gz', 'created_at': '2026-11-10'}],
                'found': [{'name': 'My Life with the Walter Boys', 'key': 'journey-iq/sonytv/walter.json.gz', 'created_at': '2026-11-10'}]}
        out = W.answer(SONY_USER, 'where is my Walter Boys journey', holdings_fn=lambda n, nm: held)
        self.assertIn('Your My Life with the Walter Boys journey is there under Select a journey.', out['reply'])
        self.assertIn('click Digital Journey IQ', out['reply'])
        self.assertEqual(out['followups'], ['Open Digital Journey IQ'])
        # the product asked for bare lists the newest held
        out2 = W.answer(SONY_USER, SONY_Q, holdings_fn=lambda n, nm: {'items': held['items'], 'found': []})
        self.assertIn('Your newest journey there: My Life with the Walter Boys.', out2['reply'])
        # a named thing that is not there yet says so and offers the build
        out3 = W.answer(SONY_USER, 'where is my Walter Boys journey', holdings_fn=lambda n, nm: {'items': [], 'found': []})
        self.assertIn('I do not see a Walter Boys journey there yet.', out3['reply'])
        self.assertIn("Say 'Build a Digital Journey for Walter Boys'", out3['reply'])

    def test_chat_only_journey_is_never_in_the_picker(self):
        # the Sony case (2026-11-10): the Walter Boys read landed in the chat and nothing was written to the
        # Digital Journey IQ registry; the read itself had said "It is in the picker now"
        hist = [USER_TURN, ACK, LANDED]
        empty = lambda n, nm: {'items': [], 'found': []}   # noqa: E731
        for q in (SONY_Q, 'where is my Walter Boys journey'):
            out = W.answer(SONY_USER, q, history=hist, holdings_fn=empty)
            reply = out['reply']
            self.assertIn(f"Your {WALTER} read is in this chat, above", reply, q)
            self.assertIn('it is not in Digital Journey IQ yet', reply, q)
            self.assertNotIn('is there under Select a journey', reply, q)
            self.assertNotIn('in the picker', reply, q)
            self.assertIn("Say 'Add it to Digital Journey IQ'", reply, q)
            self.assertEqual(out['followups'], ['Open Digital Journey IQ', 'Add it to Digital Journey IQ'], q)
            self.assertIn('click Digital Journey IQ', reply, q)
            _clean(self, reply)
        # the same read in the registry is said to be in the picker, and the add chip is not offered
        held = {'items': [{'name': WALTER}], 'found': [{'name': WALTER}]}
        out = W.answer(SONY_USER, 'where is my Walter Boys journey', history=hist, holdings_fn=lambda n, nm: held)
        self.assertIn(f"Your {WALTER} journey is there under Select a journey.", out['reply'])
        self.assertEqual(out['followups'], ['Open Digital Journey IQ'])
        # a read on another subject in the chat does not answer for the one asked about
        out = W.answer(SONY_USER, 'where is my Suits journey', history=hist, holdings_fn=empty)
        self.assertIn('I do not see a Suits journey there yet.', out['reply'])
        self.assertNotIn('Add it to Digital Journey IQ', out['followups'])

    def test_add_it_to_digital_journey_iq_is_the_build(self):
        hist = [USER_TURN, ACK, LANDED]
        self.assertEqual(W.add_order('Add it to Digital Journey IQ'), ('it', False))
        self.assertEqual(W.add_order('Add My Life with the Walter Boys to Digital Journey IQ'), ('My Life with the Walter Boys', True))
        self.assertEqual(W.add_order('put that read into the journey picker'), ('it', False))
        self.assertIsNone(W.add_order('add Tide to my cart'))
        self.assertTrue(W.wants('Add it to Digital Journey IQ'))
        out = W.answer(SONY_USER, 'Add it to Digital Journey IQ', history=hist)
        self.assertEqual(out['action'], 'rewrite')
        self.assertEqual(out['rewrite_text'], f"Build a Digital Journey for {WALTER}")
        self.assertEqual(out['subject'], WALTER)
        out2 = W.answer(SONY_USER, 'Add Suits on Peacock to Digital Journey IQ', history=[])
        self.assertEqual(out2['rewrite_text'], 'Build a Digital Journey for Suits on Peacock')
        # nothing in the chat names the read: one question back, never a guess
        out3 = W.answer(SONY_USER, 'Add it to Digital Journey IQ', history=[])
        self.assertEqual(out3['action'], 'answer')
        self.assertIn('Which read should I add to Digital Journey IQ?', out3['reply'])
        # the service rewrites the words and the ask goes on: the very next lane sees the build order, not the
        # chip (stopped there so no later lane runs in the test)
        class _Stop(BaseException):
            pass

        seen, hints = [], []

        def stop(history_, text_):
            seen.append(text_)
            raise _Stop()

        orig = S.accept_offered_run
        S.accept_offered_run = stop
        host.bind(ask_hint=lambda **kw: hints.append(kw))
        try:
            with self.assertRaises(_Stop):
                self._ask('Add it to Digital Journey IQ', history=hist)
        finally:
            S.accept_offered_run = orig
            host.bind(ask_hint=lambda **kw: None)
        self.assertEqual(seen, [f"Build a Digital Journey for {WALTER}"])
        self.assertIn({'route': 'wayfinding', 'outcome': 'add_to_journey', 'subject': WALTER}, hints)

    def test_pilot_testing_names_the_twins_tab(self):
        out = W.answer(SONY_USER, 'where is pilot testing', holdings_fn=lambda n, nm: {'items': [], 'found': []})
        self.assertIn('click Digital Twins IQ, then the Pilot Testing tab', out['reply'])
        self.assertEqual(out['action_hint'], {'view': 'digitalTwinsIQ', 'label': 'Open Digital Twins IQ', 'tab': 'pilot'})
        self.assertIn('?view=digitalTwinsIQ&tab=pilot', out['reply'])
        _clean(self, out['reply'])

    def test_dropdown_rail_and_export(self):
        out = W.answer(SONY_USER, 'where is the select product dropdown', holdings_fn=lambda n, nm: {'items': [], 'found': []})
        self.assertIn('top right of the dashboard', out['reply'])
        self.assertIn('Digital Journey IQ', out['reply'])
        self.assertNotIn('Subscriber IQ', out['reply'])       # not on this seat
        out = W.answer(SONY_USER, 'where are my old chats')
        self.assertIn('Chats & Tasks', out['reply'])
        self.assertIn('New chat', out['reply'])
        out = W.answer(SONY_USER, 'where do I download the csv')
        self.assertIn('export buttons', out['reply'])
        self.assertIn('CSV link', out['reply'])

    def test_data_question_falls_through_the_lane(self):
        self.assertIsNone(W.answer(SONY_USER, 'where did the viewers come from'))
        self.assertIsNone(W.answer(SONY_USER, 'where are my results'))
        self.assertIsNone(W.answer(SONY_USER, 'where is the howdy deck created last night'))

    def test_interpret_surface_keeps_the_chip_and_the_hint(self):
        env, status = self._ask(SONY_Q, surface='interpret')
        raw = env['raw']
        self.assertTrue(raw.get('guidance'))
        self.assertIn('Digital Journey IQ', raw.get('error') or '')
        self.assertEqual(raw.get('followups'), ['Open Digital Journey IQ'])
        self.assertEqual((raw.get('action_hint') or {}).get('view'), 'journeyIQ')


# ------------------------------------------------- finished reads stay put

def _T(ts, role, text, **meta):
    t = {'role': role, 'text': text, 'ts': ts}
    if meta:
        t['meta'] = meta
    return t


ACK = _T('2026-11-10T20:10:31.911Z', 'agent', 'Included with your plan. On it: reading ' + WALTER + ' now. This one takes a real look at the data, '
         'so give me a moment - the read will land right here when it is ready.', read_job_id='6187e945a129')
LANDED = _T('2026-11-10T20:12:54.000Z', 'agent', READ_TEXT, read_job_id='6187e945a129', kind='read', landed_at='2026-11-10T20:12:54.227Z', subject=WALTER)
USER_TURN = _T('2026-11-10T20:10:03.059Z', 'user', 'but i want it in the dashboard where it says "select a journey"')


class FinishedReadsStayPut(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        _bind(self.s3, _idx())
        self._persist = S._persist_turn
        S._persist_turn = lambda *a, **k: None
        RL.reset_landed_since()

    def tearDown(self):
        S._persist_turn = self._persist
        RL.reset_landed_since()

    def test_landed_since_last_user(self):
        stored = [USER_TURN, ACK, LANDED]
        hits = RL.landed_since_last_user(stored, [USER_TURN, ACK])
        self.assertEqual([h['job_id'] for h in hits], ['6187e945a129'])
        self.assertEqual(hits[0]['name'], WALTER)
        # visible in the client's copy: nothing to say
        self.assertEqual(RL.landed_since_last_user(stored, [USER_TURN, ACK, LANDED]), [])
        # a landing before the reader's last message is old news
        later_user = _T('2026-11-10T20:18:23.876Z', 'user', SONY_Q)
        self.assertEqual(RL.landed_since_last_user(stored + [later_user], [USER_TURN, ACK, later_user]), [])
        # no client copy (a caller that reads the stored thread): nothing
        self.assertEqual(RL.landed_since_last_user(stored, None), [])
        self.assertEqual(RL.landed_line(hits), f"Your {WALTER} read landed above.")

    def test_attach_once_and_never_the_read(self):
        RL._LANDED_SINCE.set([{'job_id': 'j1', 'name': 'Suits', 'ts': 'x'}])
        raw = {'success': True, 'action': 'answer', 'reply': 'Open the dropdown.', 'followups': []}
        env = ENV.wrap(raw, surface='analyze', decision={'reason': 'wayfinding'}, thread_id='thr1')
        self.assertTrue(env['text'].endswith('Your Suits read landed above.'))
        self.assertTrue(raw['reply'].endswith('Your Suits read landed above.'))
        self.assertEqual(raw['landed_since'], ['j1'])
        self.assertNotIn(READ_TEXT, env['text'])
        # cleared after one reply: the next envelope in the same ask carries nothing
        raw2 = {'success': True, 'action': 'answer', 'reply': 'Second.', 'followups': []}
        env2 = ENV.wrap(raw2, surface='analyze', decision={}, thread_id='thr1')
        self.assertEqual(env2['text'], 'Second.')
        self.assertNotIn('landed_since', raw2)
        # a status answer that already said where the read is gets the mark only
        RL._LANDED_SINCE.set([{'job_id': 'j1', 'name': 'Suits', 'ts': 'x'}])
        raw3 = {'success': True, 'action': 'answer', 'reply': 'Your read for Suits finished. It is in this chat, above.', 'landed_noted': True, 'landed_since': ['j1']}
        env3 = ENV.wrap(raw3, surface='analyze', decision={}, thread_id='thr1')
        self.assertNotIn('landed above', env3['text'])
        self.assertEqual(raw3['landed_since'], ['j1'])

    def test_end_to_end_trailing_line_only_when_not_visible(self):
        host.s3_put_json(host.thread_key(USER, 'thr1'), [USER_TURN, ACK, LANDED])
        body = {'text': SONY_Q, 'client': 'dashboard', 'surface': 'analyze', 'thread_id': 'thr1', 'history': [USER_TURN, ACK]}
        env, status = S.ask(SONY_USER, body)
        self.assertEqual((env.get('decision') or {}).get('reason'), 'wayfinding')
        self.assertTrue(env['text'].endswith(f"Your {WALTER} read landed above."), env['text'])
        self.assertNotIn('It is in the picker now', env['text'])
        self.assertEqual(env['raw']['landed_since'], ['6187e945a129'])
        self.assertEqual(env['text'].count('landed above'), 1)
        # the widget already shows the landed turn: the answer is the answer, nothing trailing
        body2 = dict(body, history=[USER_TURN, ACK, LANDED])
        env2, _ = S.ask(SONY_USER, body2)
        self.assertNotIn('landed above', env2['text'])
        self.assertNotIn('landed_since', env2['raw'])

    def test_status_lane_never_glues_the_read(self):
        doc = {'job_id': '6187e945a129', 'user': USER, 'status': 'done', 'subject': 'Select a Journey', 'thread_id': 'thr1',
               'question': 'but i want it in the dashboard where it says "select a journey"', 'started_at': 1794341437.3,
               'finished_at': 1794341573.0, 'payload': {'reply': READ_TEXT, 'profile': WALTER, 'followups': ['Which bands grew most?']}}
        self.s3.put_object(Bucket=BUCKET, Key=f"{READS}6187e945a129.json", Body=json.dumps(doc).encode('utf-8'))
        out = STL.answer('where is my read', USER, host=host, user=SONY_USER, history=[USER_TURN, ACK, LANDED], thread_id='thr1')
        reply = out['reply']
        self.assertIn(f"Your read for {WALTER} finished", reply)
        self.assertNotIn('Select a Journey', reply)
        self.assertNotIn('Here it is', reply)
        self.assertNotIn('It is in the picker now', reply)
        self.assertNotIn('open the tab again', reply)
        self.assertIn('It is in this chat, above', reply)
        self.assertEqual(out['followups'], [])                      # the read's own chips stay on the read's turn
        self.assertTrue(out['landed_noted'])
        self.assertEqual(out['landed_since'], ['6187e945a129'])
        # landed in another chat: the rail is named, never the read
        doc2 = dict(doc, job_id='abc', thread_id='thr2')
        self.s3.put_object(Bucket=BUCKET, Key=f"{READS}abc.json", Body=json.dumps(doc2).encode('utf-8'))
        self.s3.delete_object(Bucket=BUCKET, Key=f"{READS}6187e945a129.json")
        STL._DOC_CACHE.clear()
        out2 = STL.answer('where is my read', USER, host=host, user=SONY_USER, history=[], thread_id='thr1')
        self.assertIn("It is in your 'Dark Matter Season 2' chat", out2['reply'])
        self.assertIn('Chats & Tasks', out2['reply'])
        self.assertNotIn(READ_TEXT, out2['reply'])

    def test_other_kinds_name_where_they_land(self):
        self.assertEqual(STL._where_lands('jiq'), 'It is under Select a journey in Digital Journey IQ.')
        self.assertEqual(STL._where_lands('profile'), 'It is in the Select Profile dropdown in Profile IQ.')
        self.assertIn('Brand Partnership IQ', STL._where_lands('bpiq'))
        self.assertNotIn('open the tab again', STL._where_lands('aiq'))


# ------------------------------------------------ placeholders never bind

class Placeholders(unittest.TestCase):
    def test_is_placeholder(self):
        for s in ('Select a Journey', 'Select a journey', 'Select a journey to view...', 'Select Profile', 'Select a twin', 'Choose...', 'Choose a run',
                  'Select a journey from the dropdown above.', 'select module to navigate', 'Pick one', 'No profile selected'):
            self.assertTrue(M.is_placeholder(s), s)
        for s in ('Steven Universe', 'Selected Shorts', 'Choose Me', 'The Choice', 'My Life with the Walter Boys', 'NCIS New York Viewers', 'Selling Sunset'):
            self.assertFalse(M.is_placeholder(s), s)

    def test_binders_drop_the_placeholder(self):
        self.assertEqual(SR.clean_display('select a journey'), '')
        self.assertEqual(SR.clean_display('Select Profile'), '')
        self.assertEqual(SR.clean_display('Steven Universe'), 'Steven Universe')
        s = SR.bind('but i want it in the dashboard where it says "select a journey"', [], '')
        self.assertNotEqual(getattr(s, 'display', ''), 'Select a Journey')
        self.assertFalse(s and s.source == 'words' and s.display)
        self.assertEqual(SR.bind("the title 'mutiny'", [], '').display, 'Mutiny')
        pc = {'view': 'journeyIQ', 'primary': None, 'view_context': {'view_id': 'journeyIQ', 'view_title': '🗺️ Digital Journey IQ', 'subject_label': 'Select a journey',
                                                                       'data': {'key': 'journey-iq/u/x.json.gz', 'title': 'Select a journey to view...'}}}
        key, label, prod = TB.subject_key_for(pc)
        self.assertEqual(key, 'journey:journey-iq/u/x.json.gz')
        self.assertFalse(M.is_placeholder(label))
        pc['view_context']['data']['title'] = WALTER
        self.assertEqual(TB.subject_key_for(pc)[1], WALTER)
        self.assertEqual(VB.data_title({'view_id': 'journeyIQ', 'data': {'title': 'Select a journey to view...'}}), '')
        self.assertEqual(VB.view_label({'view_id': 'journeyIQ', 'view_title': '🗺️ Digital Journey IQ', 'data': {'title': 'Select a journey'}}), 'Digital Journey IQ')


# -------------------------------------------------------------------- wiring

class Wiring(unittest.TestCase):
    def test_lane_registered_early_deterministic_never_priced(self):
        markers = S.lane_markers()
        self.assertEqual(list(markers), list(S.LANE_ORDER))
        self.assertIn('wayfinding', markers)
        self.assertLess(markers.index('wayfinding'), markers.index('product_facts'))
        self.assertLess(markers.index('wayfinding'), markers.index('capability'))
        self.assertLess(markers.index('wayfinding'), markers.index('status_fast'))
        self.assertGreater(markers.index('wayfinding'), markers.index('intake_cancel'))
        self.assertIn('wayfinding', S.DETERMINISTIC_LANES)
        self.assertIn('wayfinding', S.NEVER_PRICED_LANES)

    def test_host_bindings_are_optional(self):
        from prometheus import host as H
        for name in ('product_access', 'live_features', 'twins_locked'):
            self.assertIn(name, H._APP_BINDINGS)
            self.assertIn(name, H._OPTIONAL)

    def test_map_feeds_the_help_brief_and_product_facts(self):
        from prometheus import help_lane as HL, product_facts as PF
        self.assertIn('Where things are on the dashboard', HL._SYSTEM)
        self.assertIn('Select a journey', HL._SYSTEM)
        self.assertIn('Chats & Tasks', HL._SYSTEM)
        f = PF.facts()
        self.assertEqual(f['where']['journey'], 'under Select a journey in Digital Journey IQ')
        self.assertIn('Select a journey', PF.answer('what can you do?')['reply'])
        para = M.where_things_are()
        self.assertIn('SELECT PRODUCT', para)
        self.assertNotIn('Admin', para)

    def test_widget_carries_the_switch_and_the_pull(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            html = fh.read()
        self.assertIn('function _pmOpenView(view, tab)', html)
        self.assertIn('window._pmOpenViewHint', html)
        self.assertIn('data.landed_since && data.landed_since.length', html)
        self.assertEqual(html.count('function _pmOpenView('), 1)


if __name__ == '__main__':
    unittest.main(verbosity=1)
