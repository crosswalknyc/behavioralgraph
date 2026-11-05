#!/usr/bin/env python3
"""Team notices for Digital Twins IQ (Jenna, 2026-11-05).

Jenna: "everytime someone creates a digital twin group email
me/jessie/liz and anytime someone creates a pilot test email us when
it's created and email us the results and then email us each time the
open a new focus group room".

prometheus/twin_notify.py sends four notices to jenna@, jessie@ and liz@:
a twin built, a pilot test created, its results (the report attached),
a focus group room opened. This suite holds the contract:

  - each event mails once, to the three, with the subject shape
    '<Event>: <name> (<user>, <company>)'
  - the body carries the fields the ask named and none of our machinery
    words (and no em or en dash)
  - a probe identity never mails
  - a repeat on the same event id does not mail again (the marker under
    system/digital_twins/notified/<event>/<id>.json)
  - the results mail carries the report PDF (and the deck when it fits)
  - nothing created before the day the notices began is mailed about
  - a failure inside the notice never raises into the product action,
    and a failed send leaves no marker behind
  - inside a test process the SES door stays shut (mail_guard)
  - every landing in the product is wired to the module

Hermetic: REGRESSION_TEST_MODE=1, a fake sender, a fake store, no network.

    REGRESSION_TEST_MODE=1 python3 scripts/test_twin_notify.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_panel as P  # noqa: E402
from migration import digital_twin_pilot as PI  # noqa: E402
from prometheus import twin_notify as TN  # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_pilot import FakeS3, _beals  # noqa: E402

TEAM = ['jenna@crosswalknyc.com', 'jessie@crosswalknyc.com', 'liz@crosswalknyc.com']
# the words a reader must never meet in these mails (the ask's list, plus the module's own audit)
BANNED = ('synth', 'model', 'panel', 'sample', 'pipeline', 'queue', 'worker', 'claude', 's3', 'anthropic', 'hostmap', 'persona', '\u2014', '\u2013')
USERS = {'users': {
    'jenna': {'first_name': 'Jenna', 'last_name': 'Menking', 'company': 'Crosswalk', 'email': 'jenna@crosswalknyc.com'},
    'acme_lead': {'first_name': 'Avery', 'last_name': 'Quinn', 'company': 'Acme Studios', 'email': 'avery@acme.example'},
    'nameless': {'email': 'n@example.com'},
}}


class Sender:
    """The fake door: records every notice, can be told to fail."""

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def __call__(self, subject='', body='', files=(), to=()):
        if self.fail:
            return {'sent': False, 'reason': 'send failed: the door is shut'}
        self.sent.append({'subject': subject, 'body': body, 'files': list(files), 'to': list(to)})
        return {'sent': True, 'message_id': f"m{len(self.sent)}"}


def _store():
    s3 = FakeS3()
    s3.put_object(Bucket='dashboard-inputs', Key='system/users.json', Body=json.dumps(USERS))
    return s3


def _twin(created_by='jenna'):
    tw = _beals()
    tw['meta'].update({'created_by': created_by, 'created_at': '2026-11-05T18:00:00Z', 'size_us': 8_255_144,
                       'window_start': '2025-11-05', 'window_end': '2026-11-05', 'window_label': 'trailing twelve months',
                       'subject_kind': 'talent', 'source': 'profile', 'profile_name': 'Jennifer Beals', 'platform_home': 'Paramount+'})
    return tw


def _scored(s3, created_by='jenna', with_question=True):
    """A finished pilot test: the cut played, the card and norms final."""
    tw = _twin(created_by)
    sess = PI.open_session([tw], title='Second Helping pilot', in_room=12, created_by=created_by)
    PI.prepare_watch(sess, {'jb': tw}, duration_s=600, title='A comedy pilot')
    PI.mark_play_paid(sess)
    PI.stop_session(sess)
    try:
        PI.record_norms(sess, s3=s3)
    except Exception:
        pass
    sess['cast'] = [{'name': 'Jennifer Beals', 'role': 'Mae'}]
    sess['diagnostics'] = {'characters': {'characters': [{'name': 'Mae', 'role': 'lead', 'excellent_pct': 41.2, 'total_positive_pct': 71.6, 'norm': None,
                                                          'screen_clock': '4:12', 'time_vs_like': 'liked more than seen', 'draw_seats': 5}],
                                          'follow': [{'name': 'Mae', 'pct': 63.1}], 'read': 'Mae carries the hour; the room follows her into the next one.'}}
    if with_question:
        sess['questions'] = [{'id': 'q1', 'q': 'Would you keep watching after this scene?', 'kind': 'yes_no', 'stage': 'done', 'asked_at': '2026-11-05T20:10:00Z',
                              'options': [{'id': 'a', 'label': 'Yes', 'pct': 58.3, 'tally': '7 of 12'}, {'id': 'b', 'label': 'Not sure yet', 'pct': 25.4, 'tally': '3 of 12'},
                                          {'id': 'c', 'label': 'No', 'pct': 16.3, 'tally': '2 of 12'}]}]
    return tw, sess


def _markers(s3, event=None):
    return sorted(k for k in s3.objs if k.startswith(TN.MARKER_PREFIX) and (event is None or f"/{event}/" in k))


def _clean(text):
    """The prose only: a link is an address, not a sentence."""
    return re.sub(r'https?://\S+', ' ', str(text or ''))


class Setup(unittest.TestCase):
    def setUp(self):
        TN._USERS_CACHE.update({'at': 0.0, 'doc': None})
        self.s3 = _store()
        self.send = Sender()

    def assertClean(self, mail):
        text = _clean(mail['subject'] + '\n' + mail['body'])
        low = text.lower()
        for w in BANNED:
            self.assertNotIn(w, low, f"'{w}' in the notice: {text[:300]}")
        self.assertEqual(TN.banned_tokens(mail['subject'] + '\n' + mail['body']), [])
        self.assertEqual(mail['to'], TEAM)


class Recipients(Setup):
    def test_the_three_and_the_internal_sender(self):
        self.assertEqual(list(TN.RECIPIENTS), TEAM)
        self.assertIn('jenna@crosswalknyc.com', TN.FROM)
        self.assertNotIn('prometheus@', TN.FROM.lower())     # the internal workflow sender, never the user-facing door

    def test_seat_reads_users_json(self):
        st = TN.seat('acme_lead', self.s3)
        self.assertEqual((st['name'], st['company'], st['email']), ('Avery Quinn', 'Acme Studios', 'avery@acme.example'))
        self.assertEqual(TN.seat('nameless', self.s3)['name'], 'nameless')          # the username stands in for a name not on file
        self.assertEqual(TN.seat('nobody', self.s3)['company'], '')

    def test_via_for(self):
        self.assertEqual(TN.via_for({'_auth_via': 'api_key'}, 'chat'), 'api')
        self.assertEqual(TN.via_for({'_auth_via': 'session'}, 'chat'), 'chat')
        self.assertEqual(TN.via_for(None, 'dashboard'), 'dashboard')


class TwinBuilt(Setup):
    def test_fires_once_with_the_fields(self):
        tw = _twin()
        inputs = {'via': 'dashboard', 'ask_text': 'Build a twin of Jennifer Beals', 'profile_key': 'Jennifer_Beals_06_17_2026_17_09.csv', 'profile_name': 'Jennifer Beals'}
        rec = TN.notify_twin_built(tw, 'jb', username='jenna', inputs=inputs, s3=self.s3, send=self.send)
        self.assertTrue(rec['sent'], rec)
        self.assertEqual(len(self.send.sent), 1)
        m = self.send.sent[0]
        self.assertEqual(m['subject'], 'Digital twin built: Jennifer Beals (Jenna Menking, Crosswalk)')
        body = m['body']
        for want in ('Jenna Menking (jenna@crosswalknyc.com) at Crosswalk built a digital twin.', 'Twin: Jennifer Beals', 'Built from: the Jennifer Beals profile',
                     'Audience: 8,255,144 people in the US', 'Window: 2025-11-05 to 2026-11-05 (trailing twelve months)', 'Kind: talent', 'Home: Paramount+',
                     'Asked: "Build a twin of Jennifer Beals"', 'Asked through: the dashboard', 'Built: Nov 5, 2026', 'Open it: https://dashboard.crosswalknyc.com/?view=digitalTwinsIQ&twin=jb'):
            self.assertIn(want, body)
        self.assertClean(m)
        self.assertEqual(_markers(self.s3, 'twin_built'), [TN.marker_key('twin_built', 'jb')])
        # the same twin landing twice (a retry of the job) mails once
        rec2 = TN.notify_twin_built(tw, 'jb', username='jenna', inputs=inputs, s3=self.s3, send=self.send)
        self.assertFalse(rec2['sent'])
        self.assertEqual(rec2['reason'], 'already sent')
        self.assertEqual(len(self.send.sent), 1)

    def test_source_lines_name_how_it_was_built(self):
        tw = _twin()
        # from a list of URLs
        self.s3.put_object(Bucket='dashboard-inputs', Key='system/digital_twins/url_lists/abc.json', Body=json.dumps({'count': 412, 'file_name': 'fans.csv'}))
        TN.notify_twin_built(tw, 'jb-list', username='acme_lead', inputs={'via': 'chat', 'from_list': True, 'url_list_key': 'system/digital_twins/url_lists/abc.json'}, s3=self.s3, send=self.send)
        self.assertIn('Built from: a list of 412 URLs (fans.csv), the audience those links define', self.send.sent[-1]['body'])
        self.assertIn('Asked through: Prometheus chat', self.send.sent[-1]['body'])
        self.assertEqual(self.send.sent[-1]['subject'], 'Digital twin built: Jennifer Beals (Avery Quinn, Acme Studios)')
        # a named subject whose audience was pulled first, over the API
        TN.notify_twin_built(tw, 'jb-named', username='acme_lead', inputs={'via': 'api', 'subject': 'Jennifer Beals', 'pull_audience': True}, s3=self.s3, send=self.send)
        self.assertIn('Built from: the subject Jennifer Beals, with its audience pulled first', self.send.sent[-1]['body'])
        self.assertIn('Asked through: the API', self.send.sent[-1]['body'])
        # the demographics picked
        tw2 = _twin()
        tw2['meta'].update({'source': 'demographics', 'profile_name': '', 'demographics_spec': {'gender': ['Female'], 'age': ['25-34', '35-44']}})
        TN.notify_twin_built(tw2, 'jb-demo', username='jenna', inputs={'via': 'dashboard'}, s3=self.s3, send=self.send)
        self.assertIn('Built from: the demographics picked', self.send.sent[-1]['body'])
        for m in self.send.sent:
            self.assertClean(m)


class PilotCreated(Setup):
    def test_fires_once_with_the_fields(self):
        tw = _twin()
        sess = PI.open_session([tw], title='Second Helping pilot', in_room=12, created_by='jenna')
        PI.prepare_watch(sess, {'jb': tw}, duration_s=600, title='A comedy pilot')
        sess['cast'] = [{'name': 'Jennifer Beals', 'role': 'Mae'}, {'name': 'Scott Caan', 'role': 'Ray'}]
        rec = TN.notify_pilot_created(tw, sess, username='jenna', via='dashboard', s3=self.s3, send=self.send)
        self.assertTrue(rec['sent'], rec)
        m = self.send.sent[0]
        self.assertEqual(m['subject'], 'Pilot test created: Second Helping pilot (Jenna Menking, Crosswalk)')
        body = m['body']
        for want in ('Jenna Menking (jenna@crosswalknyc.com) at Crosswalk created a pilot test.', 'Test: Second Helping pilot', 'Twin: Jennifer Beals', 'Mode: Pilot test',
                     'Seats: 12, standing for 8,255,144 people', 'Runtime: 10:00', 'The cut: A comedy pilot', 'Compared to: ', 'Talent named: Jennifer Beals, Scott Caan',
                     'Created through: the dashboard', 'Created: Nov', f"&twin=jb&tab=pilot"):
            self.assertIn(want, body)
        self.assertClean(m)
        # the same session again (a page refresh, a second save) does not mail
        rec2 = TN.notify_pilot_created(tw, sess, username='jenna', via='dashboard', s3=self.s3, send=self.send)
        self.assertEqual((rec2['sent'], rec2['reason']), (False, 'already sent'))
        self.assertEqual(len(self.send.sent), 1)
        self.assertEqual(_markers(self.s3, 'pilot_created'), [TN.marker_key('pilot_created', sess['id'])])

    def test_modes_and_doors(self):
        tw = _twin()
        sess = PI.open_session([tw], title='Three titles', in_room=10, created_by='acme_lead', mode='title',
                               options=[{'label': 'Second Helping'}, {'label': 'The Diner'}, {'label': 'Mae'}])
        TN.notify_pilot_created(tw, sess, username='acme_lead', via='api', s3=self.s3, send=self.send)
        m = self.send.sent[-1]
        self.assertEqual(m['subject'], 'Pilot test created: Three titles (Avery Quinn, Acme Studios)')
        self.assertIn('created a title test.', m['body'])
        self.assertIn('Mode: Title test', m['body'])
        self.assertIn('Created through: the API', m['body'])
        self.assertIn('&tab=titles', m['body'])
        self.assertClean(m)
        sess2 = PI.open_session([tw], title='Cold open script', in_room=10, created_by='jenna', mode='script', text='INT. DINER - NIGHT. ' * 40)
        TN.notify_pilot_created(tw, sess2, username='jenna', via='chat', s3=self.s3, send=self.send)
        m = self.send.sent[-1]
        self.assertIn('Mode: Script read', m['body'])
        self.assertIn('words of text', m['body'])
        self.assertIn('Created through: Prometheus chat', m['body'])
        self.assertIn('&tab=script', m['body'])
        self.assertClean(m)


class PilotResults(Setup):
    def test_results_carry_the_read_and_the_report(self):
        tw, sess = _scored(self.s3)
        rec = TN.notify_pilot_results(tw, sess, username='jenna', s3=self.s3, send=self.send)
        self.assertTrue(rec['sent'], rec)
        m = self.send.sent[0]
        self.assertEqual(m['subject'], 'Pilot test results: Second Helping pilot (Jenna Menking, Crosswalk)')
        body = m['body']
        self.assertIn('The pilot test "Second Helping pilot" on the Jennifer Beals twin was stopped; the scorecard is final.', body)
        for want in ('Run by: Jenna Menking (jenna@crosswalknyc.com) at Crosswalk', 'Seats: 12, standing for 8,255,144 people', 'Stopped: Nov', 'Headline: ',
                     'Scorecard', 'Would finish: ', 'Total positive: ', '(12 of 12)', 'As the card reads them:', 'Characters', 'Mae (lead): 41.2% excellent, 63.1% would follow into a second episode, 5 seats would come back for Mae',
                     'Mae carries the hour', 'Favorite moment at ', 'Dead spot at ', 'Forecast', 'Week-one audience if released: ', 'Would finish the season: ',
                     'Questions asked: 1', '1. "Would you keep watching after this scene?": 58.3% Yes (7 of 12)', 'Attached: ', '&tab=pilot'):
            self.assertIn(want, body, f"missing {want!r}")
        self.assertClean(m)
        # the report PDF rides along (the deck too when the whole message fits)
        names = [f[1] for f in m['files']]
        self.assertTrue(any(n.endswith('.pdf') for n in names), names)
        pdf = next(f for f in m['files'] if f[1].endswith('.pdf'))
        self.assertTrue(bytes(pdf[0]).startswith(b'%PDF'))
        self.assertEqual(pdf[2], 'application/pdf')
        self.assertLessEqual(sum(len(f[0]) for f in m['files']), TN.ATTACH_LIMIT)
        for n in names:
            self.assertIn(n, body)
        # the marker carries the send
        doc = json.loads(self.s3.objs[TN.marker_key('pilot_results', sess['id'])])
        self.assertEqual(doc['event'], 'pilot_results')
        self.assertTrue(doc.get('sent_at'))
        # once per test: a later norm change or a reconcile after a room question never mails again
        rec2 = TN.notify_pilot_results(tw, sess, username='jenna', s3=self.s3, send=self.send)
        self.assertEqual((rec2['sent'], rec2['reason']), (False, 'already sent'))
        self.assertEqual(len(self.send.sent), 1)

    def test_auto_stop_wording_and_no_card_no_mail(self):
        tw, sess = _scored(self.s3, with_question=False)
        sess['auto_stopped_at'] = sess['stopped_at']
        TN.notify_pilot_results(tw, sess, username='jenna', s3=self.s3, send=self.send, with_deck=False)
        m = self.send.sent[-1]
        self.assertIn('stopped on its own at the runtime; the scorecard is final.', m['body'])
        self.assertIn('Questions asked: 0', m['body'])
        self.assertEqual([f[2] for f in m['files']], ['application/pdf'])
        self.assertClean(m)
        # a test stopped before it ever played has no card and sends nothing
        tw2 = _twin()
        draft = PI.open_session([tw2], title='Never played', in_room=8, created_by='jenna')
        PI.stop_session(draft)
        rec = TN.notify_pilot_results(tw2, draft, username='jenna', s3=self.s3, send=self.send)
        self.assertEqual((rec['sent'], rec['reason']), (False, 'no scorecard'))
        self.assertEqual(_markers(self.s3, 'pilot_results'), [TN.marker_key('pilot_results', sess['id'])])

    def test_later_runs_on_a_thread_and_lands_once(self):
        tw, sess = _scored(self.s3, with_question=False)
        TN.notify_pilot_results_later(tw, sess, username='jenna', s3=self.s3, send=self.send, with_deck=False)
        TN.notify_pilot_results_later(tw, sess, username='jenna', s3=self.s3, send=self.send, with_deck=False)
        deadline = time.time() + 60
        while time.time() < deadline and (len(self.send.sent) < 1 or not _markers(self.s3, 'pilot_results')):
            time.sleep(0.1)
        time.sleep(0.5)
        self.assertEqual(len(self.send.sent), 1, [m['subject'] for m in self.send.sent])
        self.assertEqual(self.send.sent[0]['subject'], 'Pilot test results: Second Helping pilot (Jenna Menking, Crosswalk)')


class RoomOpened(Setup):
    def test_fires_once_per_room_never_per_question(self):
        tw = _twin()
        room = P.open_session([tw], segments=None, in_room=8, created_by='jenna', session_id='room1', title='Second Helping room')
        rec = TN.notify_room_opened(tw, room, username='jenna', via='chat', s3=self.s3, send=self.send)
        self.assertTrue(rec['sent'], rec)
        m = self.send.sent[0]
        self.assertEqual(m['subject'], 'Focus group room opened: Second Helping room (Jenna Menking, Crosswalk)')
        body = m['body']
        for want in ('Jenna Menking (jenna@crosswalknyc.com) at Crosswalk opened a Focus Group IQ room.', 'Room: Second Helping room', 'Twin: Jennifer Beals',
                     'Segments: Jennifer Beals 100%, 8 seats', 'Seats: 8, standing for 8,255,144 people', 'Opened through: Prometheus chat', 'Opened: Nov', '&twin=jb&tab=panel'):
            self.assertIn(want, body)
        self.assertClean(m)
        # questions in the room are not events; the same room again does not mail
        room['questions'].append({'id': 'q1', 'q': 'What did you think of Mae?'})
        rec2 = TN.notify_room_opened(tw, room, username='jenna', via='chat', s3=self.s3, send=self.send)
        self.assertEqual((rec2['sent'], rec2['reason']), (False, 'already sent'))
        self.assertEqual(len(self.send.sent), 1)
        # an unnamed room is titled 'Room: <twins>' by the product; the notice does not say Room twice
        room2 = P.open_session([tw], segments=None, in_room=8, created_by='acme_lead', session_id='room2')
        TN.notify_room_opened(tw, room2, username='acme_lead', via='dashboard', s3=self.s3, send=self.send)
        m = self.send.sent[-1]
        self.assertEqual(m['subject'], 'Focus group room opened: Jennifer Beals (Avery Quinn, Acme Studios)')
        self.assertIn('Room: Jennifer Beals\n', m['body'])
        self.assertIn('Opened through: the dashboard', m['body'])
        self.assertClean(m)


class Gates(Setup):
    def test_probe_identity_never_mails(self):
        tw = _twin('canary:golden')
        for uname in ('canary:golden', 'smoke', 'sandbox_partner', 'canary:smoke-2'):
            rec = TN.notify_twin_built(tw, f"jb-{uname}", username=uname, inputs={'via': 'chat'}, s3=self.s3, send=self.send)
            self.assertEqual((rec['sent'], rec['reason']), (False, 'probe identity'), uname)
            room = P.open_session([tw], segments=None, in_room=8, created_by=uname, session_id=f"r-{uname}")
            self.assertEqual(TN.notify_room_opened(tw, room, username=uname, s3=self.s3, send=self.send)['reason'], 'probe identity')
            sess = PI.open_session([tw], title='Probe test', in_room=8, created_by=uname)
            self.assertEqual(TN.notify_pilot_created(tw, sess, username=uname, s3=self.s3, send=self.send)['reason'], 'probe identity')
        self.assertEqual(self.send.sent, [])
        self.assertEqual(_markers(self.s3), [])

    def test_no_backfill_before_the_day_the_notices_began(self):
        tw = _twin()
        tw['meta']['created_at'] = '2026-11-04T23:59:59Z'
        rec = TN.notify_twin_built(tw, 'jb-old', username='jenna', inputs={'via': 'dashboard'}, s3=self.s3, send=self.send)
        self.assertEqual((rec['sent'], rec['reason']), (False, 'created before the notices began'))
        sess = PI.open_session([tw], title='Old test', in_room=8, created_by='jenna')
        sess['created_at'] = '2026-10-30T12:00:00Z'
        self.assertEqual(TN.notify_pilot_created(tw, sess, username='jenna', s3=self.s3, send=self.send)['reason'], 'created before the notices began')
        self.assertEqual(self.send.sent, [])
        self.assertEqual(_markers(self.s3), [])

    def test_a_failed_send_leaves_no_marker_and_never_raises(self):
        tw = _twin()
        shut = Sender(fail=True)
        rec = TN.notify_twin_built(tw, 'jb-shut', username='jenna', inputs={'via': 'dashboard'}, s3=self.s3, send=shut)
        self.assertFalse(rec['sent'])
        self.assertTrue(str(rec['reason']).startswith('send failed'))
        self.assertEqual(_markers(self.s3), [])          # the next landing on this id tries again
        rec2 = TN.notify_twin_built(tw, 'jb-shut', username='jenna', inputs={'via': 'dashboard'}, s3=self.s3, send=self.send)
        self.assertTrue(rec2['sent'])
        # a body builder that blows up is caught: no raise, no marker, no mail
        boom = TN.notify_room_opened(tw, {'id': 'r-boom', 'created_at': '2026-11-05T00:00:01Z', 'title': 'x', 'group': 'not a dict', 'questions': None},
                                     username='jenna', s3=self.s3, send=self.send)
        self.assertFalse(boom['sent'])
        # no store at all: the action goes on, nothing mails (a test process never reaches the real store for a marker)
        self.assertIsNone(TN._default_s3())
        self.assertEqual(TN.notify_room_opened(tw, {'id': 'r-nostore', 'created_at': '2026-11-05T00:00:01Z'}, username='jenna', s3=None, send=self.send)['reason'], 'no store for the marker')
        self.assertEqual([m['subject'] for m in self.send.sent], ['Digital twin built: Jennifer Beals (Jenna Menking, Crosswalk)'])

    def test_test_process_never_reaches_ses(self):
        self.assertTrue(os.environ.get('REGRESSION_TEST_MODE'))
        out = TN._ses_send(subject='Digital twin built: x (y)', body='hello', files=(), to=TN.RECIPIENTS)
        self.assertFalse(out['sent'])
        self.assertIn('test mode', out['reason'])
        # the shared path records the suppression on the marker, so a replay never re-sends either
        tw = _twin()
        rec = TN.notify_twin_built(tw, 'jb-ses', username='jenna', inputs={'via': 'dashboard'}, s3=self.s3)
        self.assertFalse(rec['sent'])
        doc = json.loads(self.s3.objs[TN.marker_key('twin_built', 'jb-ses')])
        self.assertIn('test mode', doc.get('suppressed', ''))

    def test_banned_audit_reads_prose_not_links(self):
        self.assertEqual(TN.banned_tokens('Open it: https://dashboard.crosswalknyc.com/?view=digitalTwinsIQ&twin=jb&tab=panel'), [])
        self.assertEqual(TN.banned_tokens('The panel sample ran the model \u2014 twice'), ['panel', 'sample', 'model', 'em dash'])


class Wiring(unittest.TestCase):
    """Every landing in the product calls the module (the hooks the ask named)."""

    def _src(self, rel):
        with open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
            return fh.read()

    def test_dashboard_routes(self):
        src = self._src('digital_twins_api.py')
        self.assertIn('from prometheus import twin_notify', src)
        self.assertGreaterEqual(src.count("_team_notice('notify_room_opened'"), 2)      # a room opened, a room duplicated
        self.assertGreaterEqual(src.count("_team_notice('notify_pilot_created'"), 2)    # a test created (the form, a saved setup, a duplicate run), a recut
        self.assertGreaterEqual(src.count("_team_notice('notify_pilot_results_later'"), 2)   # the Stop button, the auto-stop at the runtime
        # the results notice comes after the card is reconciled, the norms recorded and the twin saved
        for name, end in (('def dt_pilot_stop', 'def dt_pilot_delete'), ('def _auto_stop', 'def dt_pilot_rename')):
            fn = src[src.index(name):src.index(end)]
            at = fn.index("notify_pilot_results_later")
            norms = fn.index('PI.record_norms(')
            self.assertLess(fn.index('_reconcile(payload, sess)'), norms, name)
            self.assertLess(norms, fn.rindex('DT.save_twin(', 0, at), f"{name}: the norms land before the last save")
            self.assertLess(fn.rindex('DT.save_twin(', 0, at), at, f"{name}: the notice follows the save")

    def test_chat_lane_and_job(self):
        lane = self._src('prometheus/digital_twin_lane.py')
        self.assertIn("_team_notice('notify_room_opened'", lane)
        self.assertGreaterEqual(lane.count("_team_notice('notify_pilot_created'"), 2)   # a pilot ordered in the chat, a Script read or Concept test
        self.assertIn("'via': _via(user)", lane)                                          # the chat's builds name their door
        job = self._src('prometheus/digital_twin_job.py')
        self.assertIn('notify_twin_built(payload, key, username=username, inputs=inputs', job)   # the one landing every build path crosses
        url = self._src('prometheus/twin_url_list.py')
        self.assertIn("'via': str(via or 'chat')", url)

    def test_partner_api_and_live_finish(self):
        api = self._src('digital_twins_partner_api.py')
        self.assertIn("_team_notice('notify_pilot_created'", api)               # a screening opened over the API is a test created
        self.assertIn("_team_notice('notify_pilot_results_later'", api)         # and its results when the room has watched
        fin = api[api.index('def _finalize_if_ready'):api.index('def _team_notice')]
        self.assertLess(fin.index('PI.record_norms('), fin.index('notify_pilot_results_later'))
        live = self._src('migration/digital_twin_live.py')
        self.assertIn('from prometheus.twin_notify import notify_pilot_results', live)
        run = live[live.index('def finish_in_background'):live.index('def unstick')]
        self.assertLess(run.index('PI.record_norms('), run.index('notify_pilot_results('))
        self.assertLess(run.index('save(key, payload)'), run.index('notify_pilot_results('))

    def test_module_copy_is_clean(self):
        src = self._src('prometheus/twin_notify.py')
        self.assertNotIn('\u2014', src)
        self.assertNotIn('\u2013', src)


if __name__ == '__main__':
    unittest.main(verbosity=2)
