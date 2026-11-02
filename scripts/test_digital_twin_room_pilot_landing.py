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


if __name__ == '__main__':
    unittest.main(verbosity=2)
