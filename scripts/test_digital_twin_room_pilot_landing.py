#!/usr/bin/env python3
"""A room question asked in a Pilot Testing room lands on the test.

Jenna, 2026-11-02, the NCIS: NY room, "do you like the cast?" at 17:38:
the job finished in 37 seconds (12 of 12 seats, a read, four voices) and
the page kept reading 'researching'. Every write-back in the room engine
(the in-place update, the wait for the route's save, the stall sweep's
record update, the resume) looked the session up in panel_sessions
alone; a Pilot Testing room lives in pilot_sessions.

Hermetic: an in-memory S3, no model call.

    REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_room_pilot_landing.py
"""
from __future__ import annotations

import gzip
import io
import json
import os
import sys
import time
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from migration import digital_twin_room as R              # noqa: E402
from migration import digital_twin_room_jobs as RJ        # noqa: E402
from migration import digital_twin_synthesis as SYN       # noqa: E402


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objs = {}

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            raise KeyError(Key)
        enc = 'gzip' if Key.endswith('.gz') else None
        return {'Body': _Body(self.objs[Key]), 'ContentEncoding': enc}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objs[Key] = Body if isinstance(Body, bytes) else str(Body).encode('utf-8')

    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            raise KeyError(Key)
        return {}


TWIN_KEY = 'digital-twins/admin/Paramount_test_0000.json.gz'


def _roster(n=12):
    return [{'seat': i + 1, 'name': f'Seat{i + 1}', 'age': 20 + i, 'city': 'Houston', 'gender': 'M' if i % 2 else 'F', 'segment': 'Paramount+'} for i in range(n)]


def _twin(pilot_q=None):
    rec = pilot_q or {}
    return {
        'meta': {'subject': 'Paramount', 'name': 'Paramount', 'key': TWIN_KEY},
        'panel_sessions': [],
        'pilot_sessions': [{'id': 'pilot1', 'title': 'NCIS: NY', 'status': 'stopped', 'mode': 'pilot',
                            'group': {'roster': _roster(), 'in_room': 12, 'size_people': 83_791_213},
                            'questions': [rec] if rec else []}],
    }


def _save(s3, payload):
    SYN.save_twin(s3, TWIN_KEY, payload)


def _load(s3):
    return SYN.load_twin(s3, TWIN_KEY)


def _in_flight_rec():
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    return {'id': 'q1', 'q': 'do you like the cast?', 'kind': 'open', 'stage': 'researching', 'job_id': 'job0001',
            'answers': [], 'voices': [], 'read': '', 'options': [], 'distribution': [], 'seats_total': 12,
            'asked_at': now, 'started_at': now, 'finished_at': None, 'trail': {'stages': []}}


class FindSession(unittest.TestCase):
    def test_pilot_rooms_are_found(self):
        p = _twin()
        self.assertIsNotNone(R.find_session(p, 'pilot1'))
        self.assertEqual(len(R.all_sessions(p)), 1)
        p['panel_sessions'].append({'id': 'room9', 'questions': []})
        self.assertEqual(R.find_session(p, 'room9')['id'], 'room9')
        self.assertIsNone(R.find_session(p, 'nope'))
        self.assertIsNone(R.find_session(None, 'pilot1'))


class UpdatePayload(unittest.TestCase):
    def test_in_place_update_reaches_a_pilot_room(self):
        s3 = FakeS3()
        _save(s3, _twin(_in_flight_rec()))
        rec = _in_flight_rec()
        rec.update({'stage': 'done', 'read': 'The room would not commit.', 'answers': [{'seat': i + 1} for i in range(12)]})
        roster = _roster()
        roster[0]['persona'] = 'Seat1 is a nurse in Houston.'
        self.assertTrue(R._update_payload(s3, TWIN_KEY, 'pilot1', rec, roster))
        got = _load(s3)['pilot_sessions'][0]
        self.assertEqual(got['questions'][0]['stage'], 'done')
        self.assertEqual(got['questions'][0]['read'], 'The room would not commit.')
        self.assertEqual(len(got['questions'][0]['answers']), 12)
        self.assertEqual(got['group']['roster'][0]['persona'], 'Seat1 is a nurse in Houston.')

    def test_wait_for_record_returns_once_the_pilot_record_is_saved(self):
        s3 = FakeS3()
        _save(s3, _twin(_in_flight_rec()))
        t0 = time.time()
        R._wait_for_record(s3, TWIN_KEY, 'pilot1', 'q1', timeout=3.0)
        self.assertLess(time.time() - t0, 1.5)     # found at once; no six-second wait

    def test_record_update_reaches_a_pilot_room(self):
        s3 = FakeS3()
        _save(s3, _twin(_in_flight_rec()))
        out = RJ._record_update(s3, TWIN_KEY, 'pilot1', 'q1', lambda r: r.__setitem__('stage', 'failed'))
        self.assertIsNotNone(out)
        self.assertEqual(_load(s3)['pilot_sessions'][0]['questions'][0]['stage'], 'failed')


class Sweep(unittest.TestCase):
    def test_a_finished_job_lands_on_the_pilot_record(self):
        s3 = FakeS3()
        payload = _twin(_in_flight_rec())
        _save(s3, payload)
        done = _in_flight_rec()
        done.update({'stage': 'done', 'read': 'Eleven of twelve seats parked on too early to tell.',
                     'answers': [{'seat': i + 1, 'line': 'x'} for i in range(12)], 'voices': [{'name': 'Seat3', 'line': 'Too early.'}],
                     'option_labels': ['Yes', 'Too early to tell', 'No'], 'finished_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
        done.pop('trail', None)
        job = {'job_id': 'job0001', 'op': 'panel_ask', 'status': 'done', 'stage': 'done', 'twin_key': TWIN_KEY, 'session_id': 'pilot1',
               'question_id': 'q1', 'question': done, 'seats_answered': 12, 'seats': 12, 'created_at': time.time() - 60, 'updated_at': time.time() - 20}
        R._put_json(s3, R.job_key('job0001'), job)
        sess = payload['pilot_sessions'][0]
        changed = RJ.sweep_session(s3, TWIN_KEY, sess)
        self.assertEqual(changed, 1)
        rec = sess['questions'][0]
        self.assertEqual(rec['stage'], 'done')
        self.assertEqual(rec['read'], 'Eleven of twelve seats parked on too early to tell.')
        self.assertEqual(len(rec['answers']), 12)
        self.assertEqual(rec['voices'][0]['name'], 'Seat3')

    def test_a_job_that_failed_elsewhere_marks_the_pilot_record(self):
        s3 = FakeS3()
        payload = _twin(_in_flight_rec())
        _save(s3, payload)
        job = {'job_id': 'job0001', 'op': 'panel_ask', 'status': 'error', 'stage': 'failed', 'twin_key': TWIN_KEY, 'session_id': 'pilot1',
               'question_id': 'q1', 'question': dict(_in_flight_rec(), stage='failed'), 'failed_reason': 'stalled', 'created_at': time.time() - 900, 'updated_at': time.time() - 600}
        R._put_json(s3, R.job_key('job0001'), job)
        sess = payload['pilot_sessions'][0]
        self.assertEqual(RJ.sweep_session(s3, TWIN_KEY, sess, resume=False), 1)
        self.assertEqual(sess['questions'][0]['stage'], 'failed')
        self.assertEqual(sess['questions'][0]['read'], RJ.FAILED_LINE)

    def test_answers_land_on_the_record_while_the_room_is_thinking(self):
        # Jenna 2026-11-04: each seat's answer lands in the chat as it arrives. The thread publishes the answers so far on
        # the job document; the session read carries them onto the record (never a save: the saved twin moves at the
        # stage boundaries), and the public session exposes them per seat with the seat id the page places them by.
        s3 = FakeS3()
        payload = _twin(dict(_in_flight_rec(), stage='the room is thinking'))
        _save(s3, payload)
        mid = dict(_in_flight_rec(), stage='the room is thinking', framing='At 17:38, right after the bullpen: do you like the cast?',
                   options=[{'id': 'a', 'label': 'Yes', 'lean': 'yes', 'pct': 0.0, 'people': 0, 'seats': []}],
                   answers=[{'seat': 3, 'name': 'Seat3', 'option': 'a', 'option_label': 'Yes', 'line': 'Yes, for the lead.', 'answered_at': 1000.2},
                            {'seat': 1, 'name': 'Seat1', 'option': 'a', 'option_label': 'Yes', 'line': 'Mostly.', 'answered_at': 1000.9}])
        mid.pop('trail', None)
        job = {'job_id': 'job0001', 'op': 'panel_ask', 'status': 'running', 'stage': 'the room is thinking', 'twin_key': TWIN_KEY, 'session_id': 'pilot1',
               'question_id': 'q1', 'question': mid, 'seats_answered': 2, 'seats': 12, 'created_at': time.time() - 30, 'updated_at': time.time() - 1}
        R._put_json(s3, R.job_key('job0001'), job)
        sess = payload['pilot_sessions'][0]
        self.assertEqual(RJ.sweep_session(s3, TWIN_KEY, sess), 0, 'the answers so far are read only; nothing to save')
        rec = sess['questions'][0]
        self.assertEqual(rec['stage'], 'the room is thinking')
        self.assertEqual([a['seat'] for a in rec['answers']], [3, 1])
        self.assertEqual(rec['framing'], mid['framing'], 'the framing rides along for the record, never for the chat')
        self.assertEqual(rec['options'][0]['label'], 'Yes')
        from migration import digital_twin_pilot as PI
        pub = PI.public_session(sess)
        got = pub['questions'][0]['answers']
        self.assertEqual([(a['seat'], a['line']) for a in got], [(3, 'Yes, for the lead.'), (1, 'Mostly.')])
        self.assertTrue(all(a.get('answered_at') for a in got), 'the page orders the chat by arrival')
        # a second read with the same two answers changes nothing; a third answer is carried
        self.assertFalse(RJ.carry_answers(rec, mid))
        more = dict(mid, answers=mid['answers'] + [{'seat': 2, 'name': 'Seat2', 'option': 'a', 'option_label': 'Yes', 'line': 'Sure.', 'answered_at': 1001.4}])
        self.assertTrue(RJ.carry_answers(rec, more))
        self.assertEqual(len(rec['answers']), 3)
        self.assertEqual(_load(s3)['pilot_sessions'][0]['questions'][0]['answers'], [], 'the saved twin was not written by the sweep')


class _TwinForRoom:
    """A small audience the room can seat (the shape digital_twin_panel.open_session reads)."""

    @staticmethod
    def twin():
        return {'meta': {'name': 'NCIS: New York', 'key': TWIN_KEY, 'subject': 'NCIS: New York', 'subject_kind': 'title', 'format': 'series', 'size_us': 9_412_337},
                'identity': {'base': {'kind': 'title'}, 'read': 'People who watched the pilot.',
                             'demographics': {'gender': [{'label': 'Female', 'pct': 54.2}, {'label': 'Male', 'pct': 45.8}],
                                              'age': [{'label': '18-34', 'pct': 22.1}, {'label': '35-54', 'pct': 41.3}, {'label': '55+', 'pct': 36.6}]}},
                'traits': {'franchise_loyalty': {'value': 62.0, 'label': 'loyalty', 'read': ''}, 'second_screen': {'value': 50.0, 'label': 'second', 'read': ''},
                           'binge_vs_appointment': {'value': 50.0, 'label': 'binge', 'read': ''}, 'word_of_mouth': {'value': 50.0, 'label': 'wom', 'read': ''},
                           'trial_speed': {'value': 50.0, 'label': 'trial', 'read': ''}},
                'lifecycle': {'habit_pct': 60.0, 'discovering_pct': 10.0, 'trying_pct': 10.0, 'lapsing_pct': 10.0, 'returning_pct': 10.0}}


CLOCK_READ = ('At 43:13, with the screen black and the network logo up, almost nine in ten seats said something would bring them back, '
              'and about three in five of them named the woman running the team as that something. What the count alone does not show is why: '
              'nearly every one of those seats said the pilot built toward her and then handed the last image to two men on a boat.')


def _fake_in_cut(role, system, user, **kw):
    d = json.loads(user)
    if role == 'moderator' and 'supplied_options' in d:
        assert d['stimulus'].get('what_just_played'), 'the moderator sees the moment'
        return {'options': [{'id': 'a', 'label': 'More of the woman running the team', 'lean': 'yes'}, {'id': 'b', 'label': 'A stronger case to solve', 'lean': 'maybe'},
                            {'id': 'c', 'label': 'Nothing, one was enough', 'lean': 'no'}],
                'framing': 'At 43:13 the episode has just ended on black with the network logo. What would get you to press play on episode two?',
                'listen_for': ['her']}
    if role == 'moderator':
        # the model still opens with the clock and the scene (the old habit); the room strips it
        return {'read': CLOCK_READ, 'chat': 'At 43:13, with the logo up, almost nine in ten seats said something would bring them back. Most of them named the woman running the team.',
                'voices': [1, 2], 'probes_next': ['What would make the case matter?'], 'facts_used': ['The cut was paused at 43:13.']}
    if role == 'persona':
        assert 'what_just_played' in d, 'the seats answer about the moment'
        assert d['moderator'].startswith('At 43:13'), 'the seats hear the moderator framing'
        name = str(d['you']).split(',')[0].strip()
        return {'option': 'a' if len(name) % 2 else 'b', 'line': f'{name} here: more of her, she ran that room.', 'confidence': 4, 'reason': 'the lead'}
    return None


class InCutRead(unittest.TestCase):
    """A question asked on the clock (Jenna 2026-11-04): the read opens with
    what the room said, never the clock and the scene; the chat carries
    the question and the seats' lines without the framing; every answer
    names its seat and the moment it landed."""

    def _ask(self):
        from migration import digital_twin_panel as P
        twin = _TwinForRoom.twin()
        sess = P.open_session([twin], in_room=4, title='NCIS: NY', session_id='pilot-cut-1')
        stim = {'in_cut': True, 'window': {'clock': '43:13', 'from_clock': '42:10', 'frame': 'right after CBS logo animation glows on a black screen',
                                            'scene': {'on_screen': 'black, the network logo'}, 'beats': [], 'events': [], 'dial_now': 61.2, 'dial_window_mean': 58.0},
                'seat_windows': {}, 'cut': {'title': 'NCIS: New York', 'genre': 'procedural', 'format': 'pilot', 'cut_label': 'Cut A'}}
        rec = R.ask_room(sess, {TWIN_KEY: twin}, 'what would make you watch another episode?', stimulus=stim, s3=None, twin_key=TWIN_KEY, run_async=False, model_fn=_fake_in_cut)
        return sess, rec

    def test_the_prompt_no_longer_opens_the_read_with_the_clock(self):
        self.assertNotIn('open the read with the clock', R.READ_SYSTEM)
        self.assertIn('never with the clock or the scene', R.READ_SYSTEM)
        self.assertIn('"chat"', R.READ_SYSTEM)

    def test_the_read_opens_with_the_room_and_the_chat_skips_the_framing(self):
        sess, rec = self._ask()
        self.assertEqual(rec['stage'], 'done')
        self.assertFalse(rec['read'].startswith('At 43:13'), rec['read'])
        self.assertTrue(rec['read'].startswith('Almost nine in ten seats said'), rec['read'])
        self.assertIn('two men on a boat', rec['read'], 'the full read stays on the record')
        self.assertTrue(rec['read_short'].startswith('Almost nine in ten seats said'), rec['read_short'])
        self.assertNotIn('two men on a boat', rec['read_short'], 'the chat read is the short one')
        self.assertTrue(rec['framing'].startswith('At 43:13'), 'the framing is still the seats\' briefing')
        lines = R.chat_lines(sess, rec)
        self.assertEqual(lines[0], 'what would make you watch another episode?')
        self.assertNotIn(rec['framing'], lines)
        self.assertTrue(any('more of her, she ran that room' in line for line in lines), lines)
        self.assertTrue(all(not line.startswith('At 43:13') for line in lines), lines)
        # every seat's answer names its seat and when it landed, in the public session the page reads
        seats = sorted(a['seat'] for a in rec['answers'])
        self.assertEqual(seats, [1, 2, 3, 4])
        self.assertTrue(all(isinstance(a.get('answered_at'), float) for a in rec['answers']))
        from migration import digital_twin_pilot as PI
        pub = PI.public_session(sess)
        self.assertEqual(sorted(a['seat'] for a in pub['questions'][0]['answers']), [1, 2, 3, 4])
        self.assertEqual(pub['questions'][0]['read_short'], rec['read_short'])

    def test_the_fallback_read_also_gets_a_short_form(self):
        def silent(role, system, user, **kw):
            return _fake_in_cut(role, system, user, **kw) if role != 'moderator' or 'supplied_options' in json.loads(user) else None
        from migration import digital_twin_panel as P
        twin = _TwinForRoom.twin()
        sess = P.open_session([twin], in_room=3, title='NCIS: NY', session_id='pilot-cut-2')
        stim = {'in_cut': True, 'window': {'clock': '12:40', 'frame': 'after the hallway reveal', 'scene': {}, 'beats': [], 'events': []}, 'seat_windows': {}, 'cut': {'title': 'x'}}
        rec = R.ask_room(sess, {TWIN_KEY: twin}, 'would you keep watching?', stimulus=stim, s3=None, twin_key=TWIN_KEY, run_async=False, model_fn=silent)
        self.assertTrue(rec['read'])
        self.assertTrue(rec['read_short'])
        self.assertFalse(rec['read_short'].startswith('At 12:40'))
        self.assertLessEqual(len(rec['read_short']), len(rec['read']))

    def test_the_clock_opening_is_dropped_only_at_the_front(self):
        self.assertEqual(R._drop_clock_opening('At 12:40, after the hallway reveal, about two in three would keep watching. The rest were lost.'),
                         'About two in three would keep watching. The rest were lost.')
        self.assertEqual(R._drop_clock_opening('At 12:40, about two in three would keep watching.'), 'About two in three would keep watching.')
        self.assertEqual(R._drop_clock_opening('About two in three would keep watching. At 12:40 the room was split.'),
                         'About two in three would keep watching. At 12:40 the room was split.')
        self.assertEqual(R.short_read('One. Two. Three sentences, the third a good deal longer than the other two put together are.'),
                         'One. Two. Three sentences, the third a good deal longer than the other two put together are.')
        two = 'A first sentence that runs long enough on its own to carry the share in words for the chat line in the room. A second one that carries the one reason the room gave, at some length as well.'
        self.assertEqual(R.short_read(two + ' A third.'), two, 'two long sentences are enough for the chat')


if __name__ == '__main__':
    unittest.main(verbosity=2)
