#!/usr/bin/env python3
"""The read a person is mailed once their room session ended (Jenna
2026-11-20, verbatim: "after a person finishes a session with a digital
twin (they ask questions and are gone for a good period of time, close
the room, browser, leave the room to go elsewhere) email that person
the pdf read of what has happened in thqat room.").

Hermetic: a FakeS3, a fake sender, no network. Covers the three endings
(quiet, left, a closed browser is the quiet clock), the holds (a
question still running, a Predictive Analytics run still going), one
read per visit and a new read for a new visit, nothing for a visit with
no questions, nothing for a probe, one sender across two workers through
the lock, the copy through the house door's own audit, the PDF attached,
the From / Reply-To / BCC of the door, the routes and the page hooks.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('DTIQ_ROOM_HOST', '0')

from migration import digital_twin_session_mail as SM  # noqa: E402

KEY = 'digital-twins/carol/Jennifer_Beals_20261028_120000_abc123def0.json.gz'
SID = 'fg01'
NOW = 1_800_000_000.0
BANNED = re.compile(r"\b(synthetic|synth|modell?ed|model|research\w*|panel\w*|survey\w*|confidence|prediction|persona\w*|segment\w*|cohort\w*|calibrat\w*|nielsen|estimat\w*|pipeline|hostmap|claude)\b", re.I)


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objs = {}

    def _etag(self, key):
        return hashlib.md5(self.objs[key]).hexdigest()

    def get_object(self, Bucket, Key, **kw):
        if Key not in self.objs:
            raise KeyError(Key)
        return {'Body': _Body(self.objs[Key]), 'ETag': f'"{self._etag(Key)}"'}

    def put_object(self, Bucket, Key, Body, **kw):
        if 'IfMatch' in kw and (Key not in self.objs or self._etag(Key) != kw['IfMatch']):
            raise RuntimeError('PreconditionFailed 412')
        if 'IfNoneMatch' in kw and Key in self.objs:
            raise RuntimeError('PreconditionFailed 412')
        self.objs[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode('utf-8')

    def delete_object(self, Bucket, Key, **kw):
        self.objs.pop(Key, None)

    def head_object(self, Bucket, Key, **kw):
        if Key not in self.objs:
            raise KeyError(Key)
        return {}

    def list_objects_v2(self, **kw):
        return {'Contents': [{'Key': k, 'LastModified': '2026'} for k in list(self.objs) if k.startswith(kw.get('Prefix', ''))]}


SEATS = [(1, 'Yara', 42, 'Kansas City'), (2, 'Jamal', 29, 'Houston'), (3, 'Isaiah', 35, 'Dallas'), (4, 'Ethan', 51, 'El Paso')]
ASKED = ['Would you pay $12.99 a month for this?', 'Would you tell a friend about the show?', 'Which night would you watch it?']
HEADS = ['About one in three of the 3.81M viewers, roughly 1.3M people, are a yes: one of the four seats.',
         'Half of the 3.81M viewers, roughly 1.9M people, land on "Yes": two of the four seats.',
         'About one in five of the 3.81M viewers, roughly 760K people, land on "Thursday": one of the four seats.']


def _q(i, user='carol', stage='done', asked_at='2026-11-20T18:14:00Z', finished_at='2026-11-20T18:15:30Z', guided_i=None):
    dist = [{'label': 'Yes', 'pct': 34.6, 'people': 1_318_410}, {'label': 'No', 'pct': 52.1, 'people': 1_985_124}, {'label': 'Not sure', 'pct': 13.3, 'people': 506_773}]
    voices = [{'seat': s, 'name': n, 'age': a, 'city': c, 'segment': 'Viewers', 'line': f"{n} says what {n} says about question {i + 1}."} for s, n, a, c in SEATS]
    rec = {'id': f'q{i + 1}', 'q': ASKED[i % 3], 'kind': 'yes_no', 'read': 'Most would.', 'read_short': 'Most would.', 'headline': HEADS[i % 3],
           'distribution': dist, 'voices': voices, 'stage': stage, 'asked_by': user, 'asked_at': asked_at, 'finished_at': finished_at if stage == 'done' else None,
           'job_id': f'job{i + 1}'}
    if guided_i is not None:
        rec['guided_i'] = guided_i
    return rec


def _twin(questions=None, title='Season 2 pricing', guided=None):
    roster = [{'seat': s, 'name': n, 'age': a, 'city': c, 'segment': 'Viewers'} for s, n, a, c in SEATS]
    sess = {'id': SID, 'title': title, 'created_by': 'carol', 'created_at': '2026-11-20T18:10:00Z', 'updated_at': '2026-11-20T18:15:30Z',
            'group': {'in_room': 4, 'size_people': 3_810_307, 'segments': [{'label': 'Viewers', 'share_pct': 100.0, 'people': 3_810_307, 'seats': 4}], 'roster': roster},
            'questions': questions if questions is not None else [_q(0), _q(1)]}
    if guided is not None:
        sess['guided'] = guided
    return {'meta': {'name': 'Jennifer Beals', 'subject': 'Jennifer Beals', 'key': KEY, 'created_by': 'carol', 'size_us': 3_810_307,
                     'window_start': '2025-11-20', 'window_end': '2026-11-20'},
            'identity': {}, 'traits': {}, 'lifecycle': {}, 'runs': [], 'panel_sessions': [sess], 'pilot_sessions': []}


def _visit(asked=2, last=NOW - 60, leaving=None, **kw):
    rec = {'v': 1, 'key': KEY, 'session_id': SID, 'user': 'carol', 'opened_at': NOW - 600, 'last_activity': last, 'leaving_at': leaving,
           'asked': asked, 'first_ask_at': NOW - 500, 'last_ask_at': NOW - 120, 'attempts': 0, 'last_attempt': None}
    rec.update(kw)
    return rec


class Sender:
    """A fake house door: records every call; refuses what it is told to."""

    def __init__(self, refuse_when=None, fail=False):
        self.calls = []
        self.refuse_when = refuse_when
        self.fail = fail

    def __call__(self, **kw):
        self.calls.append(kw)
        if self.fail:
            return {'sent': False, 'reason': 'send failed: SES down'}
        if self.refuse_when and self.refuse_when(kw):
            return {'sent': False, 'reason': "banned vocabulary after scrub: ['panel']"}
        return {'sent': True, 'message_id': f"m{len(self.calls)}", 'destinations': [kw['to'], 'jenna@crosswalknyc.com']}


USER = {'username': 'carol', 'email': 'carol@acme.com', 'first_name': 'Carol', 'last_name': 'Reyes', 'company': 'Acme'}


def _sweep(s3, twin, sender, now=NOW, user=USER, **kw):
    return SM.sweep(s3, load_twin=lambda k: twin if k == KEY else None, user_for=lambda u: dict(user, username=u), sender=sender, now=now, **kw)


# ------------------------------------------------------------------ judge
class Judge(unittest.TestCase):
    def test_quiet_ends_the_session(self):
        twin = _twin()
        self.assertEqual(SM.judge(_visit(last=NOW - 60), twin['panel_sessions'][0], None, now=NOW)[0], 'wait', 'still in the room')
        self.assertEqual(SM.judge(_visit(last=NOW - SM.quiet_s() + 5), twin['panel_sessions'][0], None, now=NOW)[0], 'wait')
        self.assertEqual(SM.judge(_visit(last=NOW - SM.quiet_s() - 1), twin['panel_sessions'][0], None, now=NOW), ('send', 'the room went quiet'))

    def test_a_closed_browser_is_the_quiet_clock(self):
        # nothing from the page at all after the ask: the server's clock alone sends the read
        twin = _twin()
        rec = _visit(last=NOW - 1900, leaving=None)
        self.assertEqual(SM.judge(rec, twin['panel_sessions'][0], None, now=NOW)[0], 'send')

    def test_leaving_shortens_the_wait_and_coming_back_cancels_it(self):
        twin = _twin()
        sess = twin['panel_sessions'][0]
        self.assertEqual(SM.judge(_visit(last=NOW - 200, leaving=NOW - 60), sess, None, now=NOW)[0], 'wait')
        self.assertEqual(SM.judge(_visit(last=NOW - 200, leaving=NOW - SM.leave_s() - 1), sess, None, now=NOW), ('send', 'the room was left'))
        s3 = FakeS3()
        SM.note_activity(s3, KEY, SID, 'carol', 'ask', now=NOW - 300)
        SM.note_activity(s3, KEY, SID, 'carol', 'leaving', now=NOW - 200)
        rec, _ = SM._read(s3, SM.visit_key(KEY, SID, 'carol'))
        self.assertEqual(rec['leaving_at'], NOW - 200)
        SM.note_activity(s3, KEY, SID, 'carol', 'here', now=NOW - 100)       # back in the room
        rec, _ = SM._read(s3, SM.visit_key(KEY, SID, 'carol'))
        self.assertIsNone(rec['leaving_at'])
        self.assertEqual(SM.judge(rec, sess, None, now=NOW)[0], 'wait')

    def test_no_question_this_visit_means_no_read(self):
        sess = _twin()['panel_sessions'][0]
        self.assertEqual(SM.judge(_visit(asked=0, last=NOW - 60), sess, None, now=NOW)[0], 'wait')
        self.assertEqual(SM.judge(_visit(asked=0, last=NOW - 2000), sess, None, now=NOW), ('drop', 'no question asked this visit'))
        self.assertEqual(SM.judge(_visit(asked=0, last=NOW - 300, leaving=NOW - 150), sess, None, now=NOW)[0], 'drop')

    def test_a_running_question_or_run_holds_the_read(self):
        twin = _twin([_q(0), _q(1, stage='researching')])
        self.assertEqual(SM.judge(_visit(last=NOW - 2000), twin['panel_sessions'][0], None, now=NOW), ('wait', 'a question is still running'))
        twin = _twin([_q(0)], guided={'status': 'running', 'done': 2, 'total': 7})
        self.assertEqual(SM.judge(_visit(asked=1, last=NOW - 2000), twin['panel_sessions'][0], None, now=NOW), ('wait', 'a Predictive Analytics run is still going'))
        twin = _twin([_q(0, guided_i=0), _q(1, guided_i=1), _q(2)], guided={'status': 'done', 'done': 2, 'total': 2})
        self.assertEqual(SM.judge(_visit(asked=1, last=NOW - 2000), twin['panel_sessions'][0], None, now=NOW)[0], 'send', 'the run is done and the person asked their own question')

    def test_nothing_new_since_the_last_read(self):
        sess = _twin()['panel_sessions'][0]
        sent = {'questions_at_send': 2}
        self.assertEqual(SM.judge(_visit(asked=1, last=NOW - 60), sess, sent, now=NOW), ('wait', 'nothing new since the last read'))
        self.assertEqual(SM.judge(_visit(asked=1, last=NOW - 2000), sess, sent, now=NOW), ('drop', 'nothing new since the last read'))
        self.assertEqual(SM.judge(_visit(asked=1, last=NOW - 2000), sess, {'questions_at_send': 1}, now=NOW)[0], 'send')

    def test_a_gone_room_drops_the_visit(self):
        self.assertEqual(SM.judge(_visit(), None, None, now=NOW)[0], 'drop')

    def test_the_planned_questions_of_a_run_are_not_the_persons_own(self):
        sess = _twin([_q(0, guided_i=0), _q(1, guided_i=1), _q(2), _q(0, user='dave')])['panel_sessions'][0]
        self.assertEqual([q['id'] for q in SM.own_questions(sess, 'carol')], ['q3'])
        self.assertEqual([q['id'] for q in SM.own_questions(sess, 'dave')], ['q1'])


# --------------------------------------------------------------- activity
class Activity(unittest.TestCase):
    def setUp(self):
        SM._LAST_STAMP.clear()

    def test_every_kind_stamps_and_an_ask_counts(self):
        s3 = FakeS3()
        self.assertIsNotNone(SM.note_activity(s3, KEY, SID, 'carol', 'open', now=NOW - 400))
        rec, _ = SM._read(s3, SM.visit_key(KEY, SID, 'carol'))
        self.assertEqual((rec['asked'], rec['last_activity'], rec['leaving_at']), (0, NOW - 400, None))
        SM.note_activity(s3, KEY, SID, 'carol', 'ask', now=NOW - 300)
        SM.note_activity(s3, KEY, SID, 'carol', 'ask', now=NOW - 200)
        rec, _ = SM._read(s3, SM.visit_key(KEY, SID, 'carol'))
        self.assertEqual((rec['asked'], rec['first_ask_at'], rec['last_ask_at'], rec['last_activity']), (2, NOW - 300, NOW - 200, NOW - 200))
        self.assertEqual(len(SM.visits(s3)), 1)

    def test_a_poll_restamps_at_most_every_thirty_seconds_per_process(self):
        s3 = FakeS3()
        SM.note_activity(s3, KEY, SID, 'carol', 'poll', now=NOW)
        self.assertIsNone(SM.note_activity(s3, KEY, SID, 'carol', 'poll', now=NOW + 5))
        self.assertIsNotNone(SM.note_activity(s3, KEY, SID, 'carol', 'poll', now=NOW + SM.STAMP_MIN_GAP_S + 1))

    def test_a_probe_never_has_a_visit(self):
        s3 = FakeS3()
        self.assertIsNone(SM.note_activity(s3, KEY, SID, 'canary:probe', 'ask', now=NOW))
        self.assertIsNone(SM.note_activity(s3, KEY, SID, 'smoke', 'ask', now=NOW))
        self.assertIsNone(SM.note_activity(s3, KEY, SID, '', 'ask', now=NOW))
        self.assertEqual(SM.visits(s3), [])

    def test_bad_input_is_ignored(self):
        s3 = FakeS3()
        self.assertIsNone(SM.note_activity(s3, KEY, SID, 'carol', 'dance', now=NOW))
        self.assertIsNone(SM.note_activity(s3, '', SID, 'carol', 'ask', now=NOW))
        self.assertIsNone(SM.note_activity(None, KEY, SID, 'carol', 'ask', now=NOW))


# ----------------------------------------------------------------- the copy
class Copy(unittest.TestCase):
    def test_the_read_in_prometheus_voice(self):
        twin = _twin()
        msg = SM.compose(twin, twin['panel_sessions'][0], _visit(asked=2, first_ask_at=NOW - 600, last_ask_at=NOW - 60), USER)
        self.assertEqual(msg['subject'], 'Your read: Season 2 pricing')
        body = msg['body']
        self.assertTrue(body.startswith('Hi Carol,'))
        self.assertIn('Here is the read from your room on Jennifer Beals ("Season 2 pricing"). You asked 2 questions on ', body)
        self.assertIn(', over 9 minutes.', body)
        self.assertIn('The headline reads:', body)
        self.assertIn('- Would you pay $12.99 a month for this? About one in three of the 3.81M viewers', body)
        self.assertIn('The full read is attached: every question, how the room split, and what the people in it said.', body)
        self.assertIn(f"Open the room: https://dashboard.crosswalknyc.com/?view=digitalTwinsIQ&twin={KEY.replace('/', '%2F')}&room={SID}", body)
        self.assertNotIn('tab=', body, 'the link names the room, never a tab id the door would read as a word')
        self.assertTrue(body.rstrip().endswith('Prometheus\nCrosswalk'))
        self.assertNotIn('\u2014', body + msg['subject'])
        self.assertNotIn('Jenna', body)
        self.assertEqual(BANNED.findall(body + ' ' + msg['subject']), [], 'no method or source word in the copy')
        self.assertTrue(msg['pdf_name'].endswith('.pdf') and 'Jennifer_Beals' in msg['pdf_name'])

    def test_one_question_a_plain_room_and_a_first_name_from_the_address(self):
        twin = _twin([_q(0)], title='Room')
        msg = SM.compose(twin, twin['panel_sessions'][0], _visit(asked=1, first_ask_at=NOW - 30, last_ask_at=NOW - 10), {'email': 'casey.pearson@paramount.com'})
        self.assertEqual(msg['subject'], 'Your read from the room on Jennifer Beals')
        self.assertIn('Hi Casey,', msg['body'])
        self.assertIn('You asked 1 question on ', msg['body'])
        self.assertNotIn('over ', msg['body'].split('You asked')[1].split('\n')[0])
        self.assertIn('("Room")', '("Room")')     # the generic title never prints
        self.assertNotIn('("Room")', msg['body'])

    def test_how_long_the_session_ran_in_the_readers_units(self):
        self.assertEqual(SM._span_words(NOW, NOW + 60), '')
        self.assertEqual(SM._span_words(NOW, NOW + 9 * 60), ', over 9 minutes')
        self.assertEqual(SM._span_words(NOW, NOW + 85 * 60), ', over 85 minutes')
        self.assertEqual(SM._span_words(NOW, NOW + 70 * 60), ', over 70 minutes')
        self.assertEqual(SM._span_words(NOW, NOW + 95 * 60), ', over 2 hours')
        self.assertEqual(SM._span_words(NOW, NOW + 91 * 60), ', over 2 hours')
        self.assertEqual(SM._span_words(NOW, NOW + 3 * 3600), ', over 3 hours')
        self.assertEqual(SM._span_words(NOW, NOW + 1085 * 60), ', over 18 hours')
        self.assertEqual(SM._span_words(NOW, NOW + 5 * 86400), '', 'a span over days says nothing; the date is enough')
        self.assertEqual(SM._span_words(None, NOW), '')

    def test_the_neutral_copy_carries_none_of_the_users_words(self):
        twin = _twin(title='Our panel on pricing')
        msg = SM.compose(twin, twin['panel_sessions'][0], _visit(asked=2), USER, neutral=True)
        self.assertEqual(msg['subject'], 'Your read from the room')
        self.assertNotIn('panel', msg['body'].lower())
        self.assertNotIn('headline', msg['body'].lower())
        self.assertIn('your room on your twin', msg['body'])

    def test_the_copy_passes_the_house_door(self):
        """The real outbound door, in test mode (it never reaches SES): the
        copy clears its story check and its banned-token check, so the only
        reason it does not send is the test flag."""
        from prometheus import outbound_mail as OM
        twin = _twin()
        msg = SM.compose(twin, twin['panel_sessions'][0], _visit(asked=2, first_ask_at=NOW - 600, last_ask_at=NOW - 60), USER)
        rec = OM.send_user_email(to='carol@acme.com', subject=msg['subject'], body=msg['body'], instructed=True, caller=SM.CALLER, bcc_liz=False, family=SM.FAMILY)
        self.assertFalse(rec['sent'])
        self.assertTrue(str(rec.get('reason') or '').startswith('test mode'), rec)
        self.assertEqual(rec['destinations'], ['carol@acme.com', 'jenna@crosswalknyc.com'], 'the person, Jenna on BCC, nobody else')
        # the door's own pipeline leaves the copy as written: no view definition under a room read, no words changed
        from prometheus import exit as X
        s, b, refusal = X.mail(msg['subject'], msg['body'], family=SM.FAMILY)
        self.assertEqual((refusal, s, b), ('', msg['subject'], msg['body']))
        self.assertEqual(OM.FROM, 'Prometheus <prometheus@crosswalknyc.com>')
        self.assertEqual(OM.REPLY_TO, 'jenna@crosswalknyc.com')


# ---------------------------------------------------------------- the sweep
class Sweep(unittest.TestCase):
    def setUp(self):
        SM._LAST_STAMP.clear()

    def _due(self, s3, asked=2, last=NOW - 2000, leaving=None):
        rec = _visit(asked=asked, last=last, leaving=leaving)
        SM._write(s3, SM.visit_key(KEY, SID, 'carol'), rec)
        return rec

    def test_a_quiet_session_is_mailed_its_read_once(self):
        s3, twin, sender = FakeS3(), _twin(), Sender()
        self._due(s3)
        self.assertEqual(_sweep(s3, twin, sender), 1)
        self.assertEqual(len(sender.calls), 1)
        call = sender.calls[0]
        self.assertEqual((call['to'], call['instructed'], call['caller'], call['bcc_liz'], call['family']), ('carol@acme.com', True, SM.CALLER, False, 'sizing'))
        self.assertTrue(call['pdf'][:4] == b'%PDF' and len(call['pdf']) > 2000, 'the room PDF rides along')
        self.assertTrue(call['pdf_name'].endswith('.pdf'))
        self.assertEqual(BANNED.findall(call['subject'] + ' ' + call['body']), [])
        sent, _ = SM._read(s3, SM.sent_key(KEY, SID, 'carol'))
        self.assertEqual((sent['questions_at_send'], sent['message_id'], sent['why']), (2, 'm1', 'the room went quiet'))
        self.assertEqual(SM.visits(s3), [], 'the visit is done')
        self.assertEqual(_sweep(s3, twin, sender), 0)
        self.assertEqual(len(sender.calls), 1, 'a second pass sends nothing')

    def test_a_new_visit_with_new_questions_earns_a_new_read_and_one_without_earns_nothing(self):
        s3, twin, sender = FakeS3(), _twin(), Sender()
        self._due(s3)
        _sweep(s3, twin, sender)
        # back the next day, reads the room, leaves without asking: nothing
        SM.note_activity(s3, KEY, SID, 'carol', 'open', now=NOW + 86400)
        SM.note_activity(s3, KEY, SID, 'carol', 'leaving', now=NOW + 86400 + 60)
        self.assertEqual(_sweep(s3, twin, sender, now=NOW + 86400 + 400), 0)
        self.assertEqual(SM.visits(s3), [])
        # back again with a new question: a new read, covering three
        twin['panel_sessions'][0]['questions'].append(_q(2, asked_at='2026-11-21T18:14:00Z', finished_at='2026-11-21T18:15:30Z'))
        SM.note_activity(s3, KEY, SID, 'carol', 'ask', now=NOW + 172800)
        SM.note_activity(s3, KEY, SID, 'carol', 'leaving', now=NOW + 172800 + 30)
        self.assertEqual(_sweep(s3, twin, sender, now=NOW + 172800 + 200), 1)
        self.assertEqual(len(sender.calls), 2)
        self.assertIn('You asked 1 question', sender.calls[1]['body'])
        sent, _ = SM._read(s3, SM.sent_key(KEY, SID, 'carol'))
        self.assertEqual(sent['questions_at_send'], 3)

    def test_two_workers_one_sender(self):
        s3, twin = FakeS3(), _twin()
        self._due(s3)
        vid = SM.visit_id(KEY, SID, 'carol')
        self.assertTrue(SM.acquire(s3, vid, 'worker-a', now=NOW))
        b = Sender()
        self.assertEqual(_sweep(s3, twin, b, owner='worker-b'), 0, 'the lock is held: worker b sends nothing')
        self.assertEqual(len(SM.visits(s3)), 1)
        self.assertFalse(SM.acquire(s3, vid, 'worker-b', now=NOW))
        self.assertTrue(SM.acquire(s3, vid, 'worker-b', now=NOW + SM.LOCK_STALE_S + 1), 'a dead sender loses its lock')
        SM.release(s3, vid, 'worker-b')
        a = Sender()
        self.assertEqual(_sweep(s3, twin, a, owner='worker-a'), 1)
        self.assertEqual(len(a.calls), 1)
        self.assertNotIn(SM.lock_key(vid), s3.objs, 'the lock is released after the send')

    def test_a_guided_room_with_only_the_planned_questions_never_mails(self):
        s3 = FakeS3()
        twin = _twin([_q(0, guided_i=0), _q(1, guided_i=1)], guided={'status': 'done', 'done': 2, 'total': 2})
        # the run's own questions carry no ask of the person's: the visit record has asked 0
        SM.note_activity(s3, KEY, SID, 'carol', 'open', now=NOW - 3000)
        SM.note_activity(s3, KEY, SID, 'carol', 'poll', now=NOW - 2900)
        sender = Sender()
        self.assertEqual(_sweep(s3, twin, sender), 0)
        self.assertEqual(sender.calls, [])
        self.assertEqual(SM.visits(s3), [], 'dropped as a visit with no question')
        # while the run is going, even a person's own ask waits
        twin = _twin([_q(0, guided_i=0), _q(2)], guided={'status': 'running', 'done': 1, 'total': 5})
        self._due(s3, asked=1)
        self.assertEqual(_sweep(s3, twin, sender), 0)
        self.assertEqual(len(SM.visits(s3)), 1, 'held, not dropped')

    def test_a_probe_and_a_seat_without_an_address_get_nothing(self):
        s3, twin = FakeS3(), _twin()
        rec = self._due(s3)
        rec['user'] = 'canary:probe'
        SM._write(s3, SM.visit_key(KEY, SID, 'canary:probe'), rec)
        s3.delete_object(Bucket='b', Key=SM.visit_key(KEY, SID, 'carol'))
        twin['panel_sessions'][0]['questions'] = [_q(0, user='canary:probe'), _q(1, user='canary:probe')]
        sender = Sender()
        self.assertEqual(_sweep(s3, twin, sender, user={'email': 'canary@example.com'}), 0)
        self.assertEqual(sender.calls, [])
        self.assertEqual(SM.visits(s3), [])
        # no address on file: handled, nothing sent, the visit dropped
        twin = _twin()
        self._due(s3)
        self.assertEqual(_sweep(s3, twin, sender, user={'username': 'carol'}), 0)
        self.assertEqual(sender.calls, [])
        self.assertEqual(SM.visits(s3), [])
        sent, _ = SM._read(s3, SM.sent_key(KEY, SID, 'carol'))
        self.assertEqual(sent['suppressed'], 'no email on the seat')

    def test_a_failed_send_waits_and_tries_again(self):
        s3, twin = FakeS3(), _twin()
        self._due(s3)
        bad = Sender(fail=True)
        self.assertEqual(_sweep(s3, twin, bad), 0)
        rec = SM.visits(s3)[0]
        self.assertEqual((rec['attempts'], rec['last_attempt']), (1, NOW))
        self.assertEqual(_sweep(s3, twin, bad, now=NOW + 60), 0)
        self.assertEqual(len(bad.calls), 1, 'no second try inside the wait')
        good = Sender()
        self.assertEqual(_sweep(s3, twin, good, now=NOW + SM.RETRY_WAIT_S + 1), 1)
        self.assertEqual(SM.visits(s3), [])

    def test_a_refused_title_is_sent_with_our_words_only(self):
        s3 = FakeS3()
        twin = _twin(title='Our panel on pricing')
        self._due(s3)
        sender = Sender(refuse_when=lambda kw: 'panel' in kw['subject'].lower() or 'panel' in kw['body'].lower())
        self.assertEqual(_sweep(s3, twin, sender), 1)
        self.assertEqual(len(sender.calls), 2)
        self.assertEqual(sender.calls[1]['subject'], 'Your read from the room')
        self.assertNotIn('panel', sender.calls[1]['body'].lower())
        self.assertTrue(sender.calls[1]['pdf'][:4] == b'%PDF')

    def test_a_stalled_question_is_settled_before_the_judge(self):
        # a question whose job died reads as failed the way a page read would make it, then the read goes out
        s3 = FakeS3()
        twin = _twin([_q(0), _q(1, stage='researching')])
        self._due(s3)
        saved = []
        sender = Sender()
        n = _sweep(s3, twin, sender, save_for=lambda k: (lambda p: saved.append(k)))
        q2 = twin['panel_sessions'][0]['questions'][1]
        self.assertNotEqual(q2.get('stage'), 'researching', 'the room jobs sweep settled the dead question')
        self.assertEqual(n, 1)
        self.assertEqual(len(sender.calls), 1)
        self.assertEqual(saved, [KEY])


# ------------------------------------------------------------------ routes
class Api(unittest.TestCase):
    def setUp(self):
        SM._LAST_STAMP.clear()
        self._flag = os.environ.get('DTIQ_SESSION_MAIL')
        os.environ['DTIQ_SESSION_MAIL'] = '1'

    def tearDown(self):
        if self._flag is None:
            os.environ.pop('DTIQ_SESSION_MAIL', None)
        else:
            os.environ['DTIQ_SESSION_MAIL'] = self._flag

    def test_open_ask_poll_and_presence_stamp_the_visit(self):
        from test_digital_twin_pricing import Harness, KEY as HKEY
        h = Harness(self)
        try:
            r = h.c.post(f'/api/digital-twins/{HKEY}/panel/open', json={'title': 'Season 2 pricing'})
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
            sid = r.get_json()['session']['id']
            rec, _ = SM._read(h.s3, SM.visit_key(HKEY, sid, 'carol'))
            self.assertIsNotNone(rec, 'opening the room starts the visit')
            self.assertEqual((rec['asked'], rec['leaving_at'], rec['last_kind']), (0, None, 'open'))
            r = h.c.post(f'/api/digital-twins/{HKEY}/panel/{sid}/ask', json={'q': 'Would you pay $12.99?'})
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
            rec, _ = SM._read(h.s3, SM.visit_key(HKEY, sid, 'carol'))
            self.assertEqual((rec['asked'], rec['last_kind']), (1, 'ask'))
            SM._LAST_STAMP.clear()
            r = h.c.get(f'/api/digital-twins/{HKEY}/panel/{sid}')
            self.assertEqual(r.status_code, 200)
            rec, _ = SM._read(h.s3, SM.visit_key(HKEY, sid, 'carol'))
            self.assertEqual(rec['last_kind'], 'poll')
            r = h.c.post(f'/api/digital-twins/{HKEY}/panel/{sid}/presence', json={'state': 'leaving'})
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
            self.assertEqual(r.get_json()['noted'], True)
            rec, _ = SM._read(h.s3, SM.visit_key(HKEY, sid, 'carol'))
            self.assertIsNotNone(rec['leaving_at'])
            # a beacon posts text, not a JSON content type
            r = h.c.post(f'/api/digital-twins/{HKEY}/panel/{sid}/presence', data=json.dumps({'state': 'here'}), content_type='text/plain')
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
            rec, _ = SM._read(h.s3, SM.visit_key(HKEY, sid, 'carol'))
            self.assertIsNone(rec['leaving_at'])
            self.assertEqual(h.c.post(f'/api/digital-twins/{HKEY}/panel/{sid}/presence', json={'state': 'dance'}).status_code, 400)
            self.assertEqual(h.c.post(f'/api/digital-twins/{HKEY}/panel/nope/presence', json={'state': 'here'}).status_code, 404)
            self.assertIsNone(h.A._MAIL_SWEEPER.get('thread'), 'no sweeper thread in the regression harness')
        finally:
            h.close()

    def test_the_hooks_stay_quiet_when_the_feature_is_off(self):
        os.environ['DTIQ_SESSION_MAIL'] = '0'
        self.assertFalse(SM.enabled())
        from test_digital_twin_pricing import Harness, KEY as HKEY
        h = Harness(self)
        try:
            r = h.c.post(f'/api/digital-twins/{HKEY}/panel/open', json={'title': 'Quiet'})
            sid = r.get_json()['session']['id']
            self.assertEqual(SM.visits(h.s3), [])
            r = h.c.post(f'/api/digital-twins/{HKEY}/panel/{sid}/presence', json={'state': 'here'})
            self.assertEqual((r.status_code, r.get_json()['noted']), (200, False))
        finally:
            h.close()

    def test_the_sweeper_leaves_a_heartbeat(self):
        s3 = FakeS3()
        SM.heartbeat(s3, seen=3, sent=1, now=NOW)
        doc, _ = SM._read(s3, SM.HEARTBEAT_KEY)
        self.assertEqual((doc['visits_seen'], doc['sent'], doc['at']), (3, 1, NOW))
        self.assertTrue(doc['owner'] and doc['at_iso'].endswith('Z'))

    def test_the_switches(self):
        os.environ.pop('DTIQ_SESSION_MAIL', None)
        self.assertFalse(SM.enabled(), 'off in the regression harness unless forced on')
        os.environ['DTIQ_SESSION_MAIL'] = '1'
        self.assertTrue(SM.enabled())
        self.assertEqual(SM.quiet_s(), 1800.0)
        self.assertEqual(SM.leave_s(), 120.0)
        os.environ['DTIQ_SESSION_MAIL_QUIET_S'] = '600'
        try:
            self.assertEqual(SM.quiet_s(), 600.0)
        finally:
            os.environ.pop('DTIQ_SESSION_MAIL_QUIET_S', None)


# -------------------------------------------------------------------- page
class Page(unittest.TestCase):
    def test_the_page_hints_and_the_room_deep_link(self):
        src = open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8').read()
        self.assertIn("'/panel/' + sid + '/presence'", src)
        self.assertIn("navigator.sendBeacon(url, new Blob([body], { type: 'application/json' }))", src)
        self.assertIn("window.addEventListener('pagehide'", src)
        self.assertIn("rpSend(RP.key, RP.sid, 'leaving', !!beacon)", src)
        self.assertIn("room = q.get('room') || ''", src)
        self.assertIn("dtwOpen(fromUrl.twin_key, null, fromUrl.room || null, null, fromUrl.tab || (fromUrl.room ? 'panel' : null))", src)
        i = src.index('var RP = { key:')
        block = src[i:src.index('function renderPanelConsole()', i)]
        self.assertIn("rpSend(key, sid, 'here')", block)
        self.assertNotIn('\u2014', block)
        # the API and the module carry the switch, the route and the sweeper
        api = open(os.path.join(ROOT, 'digital_twins_api.py'), encoding='utf-8').read()
        self.assertIn("/panel/<sid>/presence'", api)
        self.assertIn('_start_session_mail_sweeper(app)', api)
        self.assertIn("_session_mail_note(key, sess, user, 'ask')", api)
        self.assertIn("_session_mail_note(key, sess, user, 'poll')", api)


if __name__ == '__main__':
    unittest.main(verbosity=1)
