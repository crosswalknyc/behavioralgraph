#!/usr/bin/env python3
"""Pilot Testing, minute by minute (Jenna 2026-11-05): the ticker under the
dial, the five-minute averages on the active line, the minute hover read
and the click that lights the chat from that minute.

Hermetic: a hand-built session (four seats in two cohorts, a dial every
twelve seconds over twenty-two minutes, reactions and one question), a
fake model counted per call, FakeS3 behind the route. REGRESSION_TEST_MODE
never calls a model. The page's chart helpers are checked in Node when
node is on the path (tick positions for a 22 and a 48 minute runtime,
the label cadence, the five-minute marks)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_minutes as MIN  # noqa: E402
from migration import digital_twin_room as ROOM  # noqa: E402
from migration.digital_twin_room import Models, has_banned  # noqa: E402

RUNTIME = 1320          # twenty-two minutes
STEP = 12               # a dial point every twelve seconds
SEATS = [
    {'seat': 1, 'name': 'Ana Ruiz', 'age': 48, 'city': 'Austin', 'gender': 'F', 'segment': 'Women'},
    {'seat': 2, 'name': 'Bo Lee', 'age': 51, 'city': 'Tampa', 'gender': 'M', 'segment': 'Men 45 and older'},
    {'seat': 3, 'name': 'Cy Park', 'age': 58, 'city': 'Reno', 'gender': 'M', 'segment': 'Men 45 and older'},
    {'seat': 4, 'name': 'Di Moss', 'age': 29, 'city': 'Denver', 'gender': 'F', 'segment': 'Women'},
]


def _seat_value(seat, i):
    return 50.0 + seat * 5 + (i % 3)


def _fixture(status='stopped'):
    ts = [STEP * i for i in range(RUNTIME // STEP + 1)]      # 0 .. 1320
    seat_dials = {str(r['seat']): [_seat_value(r['seat'], i) for i in range(len(ts))] for r in SEATS}
    room = [round(sum(seat_dials[str(r['seat'])][i] for r in SEATS) / len(SEATS), 2) for i in range(len(ts))]
    def mean_of(seats):
        return [round(sum(seat_dials[str(s)][i] for s in seats) / len(seats), 2) for i in range(len(ts))]
    splits = {
        'cohort_women': {'label': 'Women', 'group': 'cohort', 'n': 2, 'value': mean_of([1, 4])},
        'cohort_men_45_and_older': {'label': 'Men 45 and older', 'group': 'cohort', 'n': 2, 'value': mean_of([2, 3])},
        'women': {'label': 'Women', 'group': 'gender', 'n': 2, 'value': mean_of([1, 4])},
        'men': {'label': 'Men', 'group': 'gender', 'n': 2, 'value': mean_of([2, 3])},
    }
    events = [
        {'t': 25.0, 'type': 'lean-in', 'seat': 3, 'name': 'Cy Park', 'line': 'Okay, a cold open. Good.'},
        {'t': 70.0, 'type': 'love', 'seat': 1, 'name': 'Ana Ruiz', 'line': 'Oh I love her already.'},
        {'t': 95.0, 'type': 'confuse', 'seat': 2, 'name': 'Bo Lee', 'line': 'Wait, who is that supposed to be?'},
        {'t': 130.0, 'type': 'laugh', 'seat': 3, 'name': 'Cy Park', 'line': 'Ha. That line landed.'},
        {'t': 149.0, 'type': 'wow', 'seat': 4, 'name': 'Di Moss', 'line': 'Did not see that coming.'},
        {'t': 150.0, 'type': 'check-out', 'seat': 2, 'name': 'Bo Lee', 'line': 'Phone is out.'},
        {'t': 400.0, 'type': 'check-out', 'seat': 4, 'name': 'Di Moss', 'line': 'This bit drags.'},
        {'t': 1300.0, 'type': 'wow', 'seat': 1, 'name': 'Ana Ruiz', 'line': 'That ending. I am in.'},
    ]
    questions = [{'id': 'q1', 'q': 'Would you keep watching?', 'playhead_s': 620.0, 'kind': 'open',
                  'answers': [{'seat': 2, 'name': 'Bo Lee', 'line': 'Yes, for the lead.'}, {'seat': 4, 'name': 'Di Moss', 'line': 'Maybe. The pace worries me.'}]}]
    return {'id': 'fixt0001', 'title': 'NCIS: New York pilot', 'mode': 'pilot', 'status': status, 'created_by': 'a1',
            'media': {'runtime_s': RUNTIME, 'title': 'NCIS: New York pilot'},
            'group': {'roster': [dict(r) for r in SEATS], 'segments': []},
            'dial': {'t': ts, 'value': room, 'splits': splits}, 'seat_dials': seat_dials, 'events': events, 'questions': questions}


class _FakeModel:
    def __init__(self, read='They warmed to her fast, and one seat lost the thread for a beat.', lean='leans warm'):
        self.calls = 0
        self.read, self.lean = read, lean
        self.last_user = ''

    def __call__(self, role, system, user, **kw):
        self.calls += 1
        self.last_user = user
        return {'read': self.read, 'lean': self.lean}


class Series(unittest.TestCase):
    def setUp(self):
        self.s = _fixture()

    def test_room_cohort_and_person_lines_sit_on_the_dial_clock(self):
        ts, vs = MIN.series(self.s, 'room')
        self.assertEqual(len(ts), RUNTIME // STEP + 1)
        self.assertEqual(vs[0], self.s['dial']['value'][0])
        for ident in ('cohort_women', 'Women', 'women'):
            ct, cv = MIN.series(self.s, 'cohort', ident)
            self.assertEqual(len(ct), len(ts), ident)
            self.assertEqual(cv[3], self.s['dial']['splits']['cohort_women']['value'][3], ident)
        pt, pv = MIN.series(self.s, 'person', 2)
        self.assertEqual(pv[0], 60.0)
        self.assertEqual(MIN.series(self.s, 'cohort', 'nobody'), ([], []))
        self.assertEqual(MIN.series(self.s, 'person', 9), ([], []))

    def test_a_live_seat_reads_its_frame_pairs(self):
        s = _fixture(status='playing')
        s['seat_dials'] = {}
        s['live'] = {'on': True, 'seat_dials': {'1': [[12.0, 55.0], [24.0, 57.5], [36.0, 61.0]]}}
        self.assertEqual(MIN.series(s, 'person', 1), ([12.0, 24.0, 36.0], [55.0, 57.5, 61.0]))

    def test_minute_average_takes_the_points_inside_the_minute(self):
        # minute 1 holds t = 60, 72, 84, 96, 108 (five points); seat 2 reads 60 + (i % 3) with i = 5..9
        avg, n = MIN.minute_avg(self.s, 'person', 2, 1)
        self.assertEqual(n, 5)
        want = sum(_seat_value(2, i) for i in range(5, 10)) / 5
        self.assertAlmostEqual(avg, want, places=6)
        ravg, rn = MIN.minute_avg(self.s, 'room', '', 1)
        self.assertEqual(rn, 5)
        self.assertAlmostEqual(ravg, sum(self.s['dial']['value'][5:10]) / 5, places=6)
        cavg, cn = MIN.minute_avg(self.s, 'cohort', 'cohort_women', 1)
        self.assertEqual(cn, 5)
        self.assertAlmostEqual(cavg, sum(self.s['dial']['splits']['cohort_women']['value'][5:10]) / 5, places=6)
        self.assertEqual(MIN.minute_avg(self.s, 'room', '', 40), (None, 0))

    def test_five_minute_marks_and_the_end(self):
        marks = MIN.bucket_marks(RUNTIME, RUNTIME, finished=True)
        self.assertEqual(marks, [300.0, 600.0, 900.0, 1200.0, 1320])
        self.assertEqual(MIN.bucket_marks(RUNTIME, 700, finished=False), [300.0, 600.0])       # live: only the marks reached
        self.assertEqual(MIN.bucket_marks(1230, 1230, finished=True), [300.0, 600.0, 900.0, 1200.0])   # an end within 45 s of a mark is that mark
        self.assertEqual(MIN.bucket_marks(2880, 2880, finished=True), [300.0 * k for k in range(1, 10)] + [2880])

    def test_five_minute_averages_room_cohort_person(self):
        room = MIN.bucket_avgs(self.s, 'room')
        self.assertEqual([b['t'] for b in room], [300.0, 600.0, 900.0, 1200.0, 1320])
        self.assertEqual([b['from'] for b in room], [0.0, 300.0, 600.0, 900.0, 1020.0])
        self.assertEqual([b['n'] for b in room], [25, 25, 25, 25, 25])
        want = sum(self.s['dial']['value'][1:26]) / 25       # points t in (0, 300]
        self.assertAlmostEqual(room[0]['avg'], round(want, 1), places=6)
        self.assertEqual(room[0]['clock'], '5:00')
        self.assertEqual(room[-1]['clock'], '22:00')
        person = MIN.bucket_avgs(self.s, 'person', 3)
        self.assertEqual(len(person), 5)
        self.assertAlmostEqual(person[1]['avg'], round(sum(_seat_value(3, i) for i in range(26, 51)) / 25, 1), places=6)
        cohort = MIN.bucket_avgs(self.s, 'cohort', 'cohort_men_45_and_older')
        p2 = MIN.bucket_avgs(self.s, 'person', 2)
        for c, a, b in zip(cohort, p2, person):
            self.assertAlmostEqual(c['avg'], round((a['avg'] + b['avg']) / 2, 1), delta=0.11)   # the cohort is the mean of its seats
        # a live test shows only the marks the room has reached
        live = _fixture(status='playing')
        self.assertEqual([b['t'] for b in MIN.bucket_avgs(live, 'room', upto=700)], [300.0, 600.0])
        self.assertEqual(MIN.bucket_avgs({'dial': {'t': [], 'value': []}}, 'room'), [])


class Lines(unittest.TestCase):
    def setUp(self):
        self.s = _fixture()

    def test_the_minute_with_its_margin(self):
        m, a, b = MIN.window_bounds(70)
        self.assertEqual((m, a, b), (1, 30, 150))
        lines = MIN.lines_in_window(self.s, 70, 'room')
        self.assertEqual([l['t'] for l in lines], [70.0, 95.0, 130.0, 149.0])    # 25 is before 0:30, 150 is at the edge and out
        self.assertEqual([l['seat'] for l in lines], [1, 2, 3, 4])
        self.assertEqual(lines[0]['clock'], '1:10')
        self.assertEqual(lines[0]['type'], 'love')
        self.assertEqual(lines[0]['rating'], round(_seat_value(1, 5), 1))     # seat 1 at 1:10 reads its 1:00 point
        self.assertEqual(lines[1]['name'], 'Bo Lee')
        self.assertEqual(MIN.lines_in_window(self.s, 70, 'room', margin_s=0), MIN.lines_in_window(self.s, 100, 'room', margin_s=0))
        self.assertEqual([l['t'] for l in MIN.lines_in_window(self.s, 70, 'room', margin_s=0)], [70.0, 95.0])

    def test_the_active_line_filters_the_seats(self):
        self.assertEqual([l['seat'] for l in MIN.lines_in_window(self.s, 70, 'cohort', 'cohort_women')], [1, 4])
        self.assertEqual([l['seat'] for l in MIN.lines_in_window(self.s, 70, 'cohort', 'Men 45 and older')], [2, 3])
        self.assertEqual([l['seat'] for l in MIN.lines_in_window(self.s, 70, 'person', 2)], [2])
        self.assertEqual(MIN.lines_in_window(self.s, 70, 'person', 9), [])
        self.assertEqual(MIN.seats_for(self.s, 'room'), None)
        self.assertEqual(MIN.seats_for(self.s, 'cohort', 'cohort_women'), {'1', '4'})

    def test_answers_to_a_question_asked_in_the_minute_are_lines(self):
        lines = MIN.lines_in_window(self.s, 600, 'room')
        self.assertEqual([(l['seat'], l['type']) for l in lines], [(2, 'answer'), (4, 'answer')])
        self.assertEqual(lines[0]['q'], 'Would you keep watching?')
        self.assertEqual([l['seat'] for l in MIN.lines_in_window(self.s, 600, 'cohort', 'cohort_women')], [4])


class Digest(unittest.TestCase):
    def setUp(self):
        self.s = _fixture()
        ROOM.set_model_fn(None)

    def tearDown(self):
        ROOM.set_model_fn(None)

    def test_one_model_call_per_key_then_cached(self):
        fake = _FakeModel()
        models = Models(model_fn=fake)
        out = MIN.digest(self.s, 70, 'room', '', models=models)
        self.assertEqual(fake.calls, 1)
        self.assertEqual(out['minute'], 1)
        self.assertEqual(out['t'], 60)
        self.assertEqual(out['clock'], '1:00')
        self.assertEqual(out['n'], 4)
        self.assertEqual(len(out['lines']), 4)
        self.assertEqual(out['avg'], round(sum(self.s['dial']['value'][5:10]) / 5, 1))
        self.assertEqual(out['summary'], fake.read)
        self.assertEqual(out['lean'], 'leans warm')
        self.assertFalse(out['cached'])
        self.assertTrue(out['fresh'])
        self.assertIn('1:10 seat 1 (Ana Ruiz)', fake.last_user)
        self.assertIn('Oh I love her already.', fake.last_user)
        # the record carries it; the second hover is instant
        self.assertIn('room::1', self.s['minute_digests'])
        again = MIN.digest(self.s, 95, 'room', '', models=models)        # the same minute, another second of it
        self.assertEqual(fake.calls, 1)
        self.assertTrue(again['cached'])
        self.assertFalse(again['fresh'])
        self.assertEqual(again['summary'], fake.read)
        # another line is another key
        MIN.digest(self.s, 70, 'cohort', 'cohort_women', models=models)
        self.assertEqual(fake.calls, 2)
        self.assertIn('cohort:cohort_women:1', self.s['minute_digests'])
        MIN.digest(self.s, 70, 'person', 2, models=models)
        self.assertEqual(fake.calls, 3)
        # a new line landing in that minute re-reads it once
        self.s['events'].append({'t': 100.0, 'type': 'love', 'seat': 4, 'name': 'Di Moss', 'line': 'More of this.'})
        fresh = MIN.digest(self.s, 70, 'room', '', models=models)
        self.assertEqual(fake.calls, 4)
        self.assertTrue(fresh['fresh'])
        self.assertEqual(fresh['n'], 4)
        self.assertEqual(len(fresh['lines']), 5)

    def test_a_quiet_minute_never_calls_a_model(self):
        fake = _FakeModel()
        out = MIN.digest(self.s, 1000, 'room', '', models=Models(model_fn=fake))      # minute 16: nobody spoke
        self.assertEqual(fake.calls, 0)
        self.assertEqual(out['summary'], MIN.QUIET)
        self.assertEqual(out['n'], 0)
        self.assertEqual(out['lines'], [])
        self.assertIn(out['lean'], MIN.LEANS)
        self.assertNotIn('minute_digests', self.s)
        self.assertFalse(out['fresh'])

    def test_no_model_lands_on_a_plain_read(self):
        out = MIN.digest(self.s, 70, 'room', '', models=Models())        # REGRESSION_TEST_MODE: nothing answers
        self.assertTrue(out['summary'])
        self.assertNotEqual(out['summary'], MIN.QUIET)
        self.assertIn('4 seats spoke up', out['summary'])
        self.assertIn('Oh I love her already', out['summary'])
        self.assertEqual(has_banned(out['summary']), [])
        self.assertNotIn('\u2014', out['summary'])
        self.assertIn(out['lean'], MIN.LEANS)
        self.assertTrue(out['fresh'])

    def test_the_read_is_scrubbed_and_the_lean_validated(self):
        fake = _FakeModel(read='The panel loved her \u2014 the model says the sample warmed; synthetic twin data agrees.', lean='very warm')
        out = MIN.digest(self.s, 70, 'room', '', models=Models(model_fn=fake))
        self.assertEqual(has_banned(out['summary']), [], out['summary'])
        self.assertNotIn('\u2014', out['summary'])
        for word in ('panel', 'model', 'sample', 'synthetic'):
            self.assertNotRegex(out['summary'].lower(), r'\b' + word + r'\b')
        self.assertIn(out['lean'], MIN.LEANS)          # 'very warm' is not a lean: the computed one stands

    def test_the_lean_follows_the_lines_and_the_line(self):
        self.assertEqual(MIN.lean_of(self.s, 'room', '', 1, [{'type': 'love'}, {'type': 'laugh'}]), 'leans warm')
        self.assertEqual(MIN.lean_of(self.s, 'room', '', 1, [{'type': 'check-out'}]), 'leans cool')
        self.assertEqual(MIN.lean_of(self.s, 'room', '', 1, [{'type': 'love'}, {'type': 'check-out'}]), 'split')
        self.assertEqual(MIN.lean_of(self.s, 'room', '', 1, []), 'steady')
        s = _fixture()
        s['dial']['value'] = [40.0] * 5 + [70.0] * (len(s['dial']['t']) - 5)
        self.assertEqual(MIN.lean_of(s, 'room', '', 1, []), 'warming')
        s['dial']['value'] = [70.0] * 5 + [40.0] * (len(s['dial']['t']) - 5)
        self.assertEqual(MIN.lean_of(s, 'room', '', 1, [{'type': 'love'}]), 'cooling')

    def test_precompute_at_stop_covers_every_minute_and_the_buckets(self):
        fake = _FakeModel()
        n = MIN.precompute(self.s, models=Models(model_fn=fake))
        self.assertEqual(n, 22)
        spoken = {MIN.window_bounds(e['t'])[0] for e in self.s['events']} | {MIN.window_bounds(q['playhead_s'])[0] for q in self.s['questions']}
        # the margin lets a line sit in two minutes: every minute a line reaches has its read, nothing else does
        reached = set()
        for m in range(22):
            if MIN.lines_in_window(self.s, m * 60, 'room'):
                reached.add(m)
        self.assertTrue(spoken <= reached)
        self.assertEqual(set(int(k.rsplit(':', 1)[1]) for k in self.s['minute_digests']), reached)
        self.assertEqual(fake.calls, len(reached))
        self.assertEqual([b['t'] for b in self.s['minute_buckets']['room']], [300.0, 600.0, 900.0, 1200.0, 1320])
        self.assertTrue(self.s['minute_digests_done'])
        # hovering a finished test never waits: every spoken minute comes off the record, the quiet ones never needed a call
        for m in range(22):
            out = MIN.digest(self.s, m * 60, 'room', '', models=Models(model_fn=fake))
            self.assertFalse(out['fresh'], m)
        self.assertEqual(fake.calls, len(reached))
        # the view the page gets carries the digests and the buckets
        from migration import digital_twin_pilot as PI
        pub = PI.public_session(self.s)
        self.assertEqual(set(pub['minute_digests']), set(self.s['minute_digests']))
        self.assertIn('room', pub['minute_buckets'])

    def test_precompute_in_background_then_saves(self):
        fake = _FakeModel()
        ROOM.set_model_fn(fake)
        saved = []
        th = MIN.precompute_in_background(self.s, key='k', payload={'pilot_sessions': [self.s]}, save=lambda k, p: saved.append(k))
        th.join(timeout=30)
        self.assertEqual(saved, ['k'])
        self.assertIn('room::1', self.s['minute_digests'])

    def test_live_finish_runs_the_precompute_after_the_scorecard(self):
        import inspect
        from migration import digital_twin_live as LIVE
        src = inspect.getsource(LIVE.finish_in_background)
        self.assertIn('MIN.precompute(sess', src)
        self.assertLess(src.index('notify_pilot_results'), src.index('MIN.precompute'))


class Route(unittest.TestCase):
    """The minute route answers the shape, refuses a bad mode, and reads
    another company's twin as not there (the company scope landed today)."""

    @classmethod
    def setUpClass(cls):
        try:
            import flask  # noqa: F401
        except Exception:
            raise unittest.SkipTest('flask not installed')

    def setUp(self):
        import flask
        import digital_twins_api as A
        from prometheus.host import host
        from scripts.test_digital_twins_company_scope import FakeS3, USERS, KEY_A, KEY_B, _payload, _user
        from migration import digital_twin_company as CO
        from migration import digital_twin_synthesis as SYN
        self.CO, self.SYN, self.KEY_A, self.KEY_B, self._user = CO, SYN, KEY_A, KEY_B, _user
        self.s3 = FakeS3()
        pa = _payload('Acme Show', 'a1', KEY_A)
        pa['pilot_sessions'] = [_fixture()]
        SYN.save_twin(self.s3, KEY_A, pa)
        SYN.save_twin(self.s3, KEY_B, _payload('Globex Fans', 'b1', KEY_B))
        self.current = {'user': _user('a1')}
        fake_app = types.SimpleNamespace(s3_client=self.s3, get_current_user=lambda: self.current['user'], _normalize_role=lambda r: r,
                                         consume_credit=lambda *a, **k: True, load_users=lambda: json.loads(json.dumps(USERS)))
        self._old_app = sys.modules.get('app')
        sys.modules['app'] = fake_app
        CO.set_users_loader(None)
        host.bind(gate=lambda allow_api_key=False: (self.current['user'], None), s3_client=self.s3)
        app = flask.Flask('t')
        app.secret_key = 'x'
        A.register(app)
        self.c = app.test_client()
        self.fake = _FakeModel()
        ROOM.set_model_fn(self.fake)
        MIN.clear_memo()          # the process memo would carry a read from one test to the next

    def tearDown(self):
        ROOM.set_model_fn(None)
        if self._old_app is not None:
            sys.modules['app'] = self._old_app
        else:
            sys.modules.pop('app', None)
        self.CO.set_users_loader(None)

    def _as(self, name):
        self.current['user'] = self._user(name)
        with self.c.session_transaction() as sess:
            sess['username'] = name

    def test_the_route_answers_the_minute_and_keeps_it_on_the_test(self):
        self._as('a1')
        r = self.c.get(f'/api/digital-twins/{self.KEY_A}/pilot/fixt0001/minute', query_string={'t': 70, 'mode': 'room'})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[:300])
        d = r.get_json()
        self.assertTrue(d['success'])
        for k in ('t', 'avg', 'n', 'lines', 'summary', 'lean'):
            self.assertIn(k, d)
        self.assertEqual(d['t'], 60)
        self.assertEqual(d['n'], 4)
        self.assertEqual([l['seat'] for l in d['lines']], [1, 2, 3, 4])
        self.assertEqual(set(d['lines'][0]), {'seat', 't', 'clock', 'text', 'rating', 'type', 'name'})
        self.assertEqual(d['summary'], self.fake.read)
        self.assertEqual(self.fake.calls, 1)
        self.assertNotIn('fresh', d)
        twin = self.SYN.load_twin(self.s3, self.KEY_A)
        self.assertIn('room::1', twin['pilot_sessions'][0]['minute_digests'])      # saved, so the next hover is instant
        r2 = self.c.get(f'/api/digital-twins/{self.KEY_A}/pilot/fixt0001/minute', query_string={'t': 80, 'mode': 'room'})
        self.assertTrue(r2.get_json()['cached'])
        self.assertEqual(self.fake.calls, 1)
        # a cohort and a seat
        r3 = self.c.get(f'/api/digital-twins/{self.KEY_A}/pilot/fixt0001/minute', query_string={'t': 70, 'mode': 'cohort', 'id': 'cohort_women'})
        self.assertEqual([l['seat'] for l in r3.get_json()['lines']], [1, 4])
        r4 = self.c.get(f'/api/digital-twins/{self.KEY_A}/pilot/fixt0001/minute', query_string={'t': 70, 'mode': 'person', 'id': '2'})
        self.assertEqual([l['seat'] for l in r4.get_json()['lines']], [2])
        # a quiet minute
        r5 = self.c.get(f'/api/digital-twins/{self.KEY_A}/pilot/fixt0001/minute', query_string={'t': 1000, 'mode': 'room'})
        self.assertEqual(r5.get_json()['summary'], MIN.QUIET)
        self.assertEqual(self.fake.calls, 3)

    def test_bad_inputs(self):
        self._as('a1')
        self.assertEqual(self.c.get(f'/api/digital-twins/{self.KEY_A}/pilot/fixt0001/minute', query_string={'t': 70, 'mode': 'bogus'}).status_code, 400)
        self.assertEqual(self.c.get(f'/api/digital-twins/{self.KEY_A}/pilot/fixt0001/minute', query_string={'t': 70, 'mode': 'cohort'}).status_code, 400)
        self.assertEqual(self.c.get(f'/api/digital-twins/{self.KEY_A}/pilot/nope/minute', query_string={'t': 70}).status_code, 404)
        self.assertEqual(self.fake.calls, 0)

    def test_another_companys_twin_reads_as_not_there(self):
        self._as('b1')
        r = self.c.get(f'/api/digital-twins/{self.KEY_A}/pilot/fixt0001/minute', query_string={'t': 70, 'mode': 'room'})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()['error'], self.CO.NOT_IN_LIBRARY)
        self.assertEqual(self.fake.calls, 0)
        self._as('canary:probe')
        self.assertEqual(self.c.get(f'/api/digital-twins/{self.KEY_A}/pilot/fixt0001/minute', query_string={'t': 70}).status_code, 404)
        self._as('jenna')
        self.assertEqual(self.c.get(f'/api/digital-twins/{self.KEY_A}/pilot/fixt0001/minute', query_string={'t': 70}).status_code, 200)


def _fn(page, name):
    m = re.search(r'^ {12}function ' + re.escape(name) + r'\(.*?^ {12}\}', page, re.S | re.M)
    assert m, name
    return m.group(0)


class Page(unittest.TestCase):
    """The page: the ticker is painted with the dial, the chat lines carry
    their clock, the lit minute survives a repaint, the hover opens no bio."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            cls.page = fh.read()

    def test_wiring(self):
        p = self.page
        self.assertIn('<div class="dtw-pi-ticker" id="dtwPiTicker"', p)
        self.assertIn('drawPilotDial(t);\n                paintPilotTicker(t);', p)
        self.assertIn('piDrawBuckets(ctx, s, view, X, Y, upto, dur, w, h);', _fn(p, 'drawPilotDial'))
        self.assertIn("'/minute?t='", _fn(p, 'piMinuteFetch'))
        self.assertIn("box.setAttribute('data-k', key);\n                        piMinuteApply(box, false);", p)
        self.assertIn("piMinuteApply(box.querySelector('#dtwPiSeats'), false);", _fn(p, 'piPaintPopout'))
        self.assertIn("data-t=", _fn(p, 'piSeatRowHtml'))
        self.assertIn("data-t=", _fn(p, 'piPromRowHtml'))
        self.assertEqual(_fn(p, 'renderPilotSeats').count('t: it.t'), 4)
        self.assertIn('onmouseenter="dtwPilotCohortOver(', _fn(p, 'splitLegend'))
        self.assertIn("if (e.key === 'Escape' || e.key === 'Esc') piMinuteClear();", p)
        # the minute hover never opens who someone is
        for name in ('piMinuteTipShow', 'piMinuteDialMove', 'paintPilotTicker', 'piMinuteApply'):
            self.assertNotIn('piBioShow', _fn(p, name), name)
            self.assertNotIn('piBioToggle', _fn(p, name), name)
        tip = _fn(p, 'piMinuteTipHtml')
        for word in ('synth', 'model', 'panel', 'sample'):
            self.assertNotIn(word, tip.lower())
        self.assertNotIn('\u2014', tip)

    def test_chart_helpers_in_node(self):
        node = shutil.which('node')
        if not node:
            raise unittest.SkipTest('node not on the path')
        p = self.page
        js = '\n'.join(_fn(p, n) for n in ('piTickPlan', 'piMinuteWindow', 'piMinuteAvgOf', 'piBucketMarks'))
        prog = ("function clockFmt(t) { t = Math.max(0, Math.round(Number(t) || 0)); var m = Math.floor(t / 60), s = t % 60; return m + ':' + (s < 10 ? '0' : '') + s; }\n"
                + js + "\n"
                "var a = piTickPlan(1320), b = piTickPlan(2880);\n"
                "var ts = [], vs = []; for (var i = 0; i <= 110; i++) { ts.push(12 * i); vs.push(50 + (i % 3)); }\n"
                "var fin = piBucketMarks(ts, vs, 1320, 1320, true), live = piBucketMarks(ts, vs, 1320, 700, false);\n"
                "console.log(JSON.stringify({ a: { n: a.ticks.length, every: a.every, t: a.ticks.map(function(k) { return k.t; }), labels: a.ticks.filter(function(k) { return k.label; }).map(function(k) { return k.label; }), majors: a.ticks.filter(function(k) { return k.major; }).length },"
                " b: { n: b.ticks.length, every: b.every, labels: b.ticks.filter(function(k) { return k.label; }).map(function(k) { return k.label; }), last: b.ticks[b.ticks.length - 1].t },"
                " fin: fin.map(function(x) { return [x.t, x.from, x.n, Math.round(x.avg * 100) / 100]; }), live: live.map(function(x) { return x.t; }),"
                " m1: piMinuteAvgOf(ts, vs, 1), win: piMinuteWindow(7), none: piMinuteAvgOf(ts, vs, 40) }));\n")
        out = subprocess.run([node, '-e', prog], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout.strip())
        self.assertEqual(got['a']['n'], 23)                                  # 0:00 to 22:00, a mark every minute
        self.assertEqual(got['a']['every'], 1)
        self.assertEqual(got['a']['t'], [60 * m for m in range(23)])
        self.assertEqual(got['a']['labels'][:3], ['0:00', '1:00', '2:00'])   # every minute labeled under thirty minutes
        self.assertEqual(len(got['a']['labels']), 23)
        self.assertEqual(got['a']['majors'], 5)                              # 0, 5, 10, 15, 20
        self.assertEqual(got['b']['n'], 49)                                  # 0:00 to 48:00
        self.assertEqual(got['b']['every'], 5)
        self.assertEqual(got['b']['labels'], ['0:00', '5:00', '10:00', '15:00', '20:00', '25:00', '30:00', '35:00', '40:00', '45:00'])
        self.assertEqual(got['b']['last'], 2880)
        self.assertEqual([x[0] for x in got['fin']], [300, 600, 900, 1200, 1320])
        self.assertEqual([x[1] for x in got['fin']], [0, 300, 600, 900, 1020])
        self.assertEqual([x[2] for x in got['fin']], [25, 25, 25, 25, 25])
        self.assertEqual(got['live'], [300, 600])
        self.assertEqual(got['m1'], {'avg': 51.0, 'n': 5})                  # t = 60..108, i = 5..9, values 52,50,51,52,50
        self.assertEqual(got['win'], {'a': 390, 'b': 510})
        self.assertIsNone(got['none'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
