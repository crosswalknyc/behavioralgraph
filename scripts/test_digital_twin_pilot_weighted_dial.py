#!/usr/bin/env python3
"""The Pilot Testing dial counts each seat by the people it stands for (Jenna 2026-11-20,
choosing between a plain average of the seats and the people behind each seat: "Yes,
weight the dial by the people behind each seat").

The room line and every group line are people-weighted means of the seat dials, on the
weights the room's published split uses; a room whose seats all stand for the same people
is the plain average it always was; a live room counts the same way frame by frame; a
finished test already on file is re-counted in place and stamped; and everything a
reader sees (the scorecard's dial average, the lines, the CSV, the public view) agrees
with the line. Hermetic: no model, no network.
"""
from __future__ import annotations

import base64
import copy
import os
import struct
import sys
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('DTW_WATCH_URL_FETCH', '0')

from migration import digital_twin_live as LIVE  # noqa: E402
from migration import digital_twin_pilot as PI  # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_pilot import _beals  # noqa: E402

P_PLUS = 'https://www.paramountplus.com/shows/video/ALVE01M3QWCNZYE2VR099PZGTZH04W/'


def _png_data_url(size=8):
    raw = b''.join(b'\x00' + b'\x10\x20\x30' * size for _ in range(size))

    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')


def _wmean(seat_dials: dict, weights: dict, i: int) -> float:
    tot = sum(weights[k] for k in seat_dials)
    return sum(float(seat_dials[k][i]) * weights[k] for k in seat_dials) / tot


def _plain(seat_dials: dict, i: int) -> float:
    return sum(float(v[i]) for v in seat_dials.values()) / len(seat_dials)


def _unequal_room(sess: dict) -> dict:
    """Two segments the seats split evenly, one standing for three times
    the people of the other: the weights the published split uses."""
    roster = sess['group']['roster']
    half = len(roster) // 2
    for i, r in enumerate(roster):
        r.pop('archetype', None)
        r.pop('people', None)
        r['segment'] = 'Women' if i < half else 'Men'
    sess['group']['segments'] = [{'label': 'Women', 'people': 3_000_000}, {'label': 'Men', 'people': 1_000_000}]
    return sess


def _equal_room(sess: dict) -> dict:
    """One segment, seats without their own figures: everyone stands for the same people."""
    for r in sess['group']['roster']:
        r.pop('archetype', None)
        r.pop('people', None)
        r['segment'] = 'All'
    sess['group']['segments'] = [{'label': 'All', 'people': 2_000_000}]
    return sess


def _prepared(shape, seats=8, sid='w1', duration=120):
    twin = _beals()
    sess = PI.open_session([twin], title='NCIS: New York pilot', in_room=seats)
    sess['id'] = sid
    shape(sess)
    PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=duration, title='NCIS: New York pilot')
    return twin, sess


class Weights(unittest.TestCase):
    def test_weights_are_the_people_behind_each_seat(self):
        roster = [{'seat': 1, 'segment': 'Women'}, {'seat': 2, 'segment': 'Women'}, {'seat': 3, 'segment': 'Men'}, {'seat': 4, 'segment': 'Men'}]
        w = PI.seat_weights(roster, [{'label': 'Women', 'people': 300}, {'label': 'Men', 'people': 100}])
        self.assertEqual(w, {'1': 150.0, '2': 150.0, '3': 50.0, '4': 50.0})
        # a room seated by kind: each seat carries its kind's people, scaled to its segment
        kinds = [{'seat': 1, 'segment': 'All', 'people': 600, 'archetype': {'id': 'a'}}, {'seat': 2, 'segment': 'All', 'people': 200, 'archetype': {'id': 'b'}},
                 {'seat': 3, 'segment': 'All', 'people': 200, 'archetype': {'id': 'c'}}]
        w2 = PI.seat_weights(kinds, [{'label': 'All', 'people': 2000}])
        self.assertEqual(w2, {'1': 1200.0, '2': 400.0, '3': 400.0})
        # no segment figure: the seats' own people
        w3 = PI.seat_weights(kinds, [])
        self.assertEqual(w3, {'1': 600.0, '2': 200.0, '3': 200.0})
        # nothing to go on: one each
        w4 = PI.seat_weights([{'seat': 1}, {'seat': 2}], [])
        self.assertEqual(w4, {'1': 1.0, '2': 1.0})

    def test_room_line_is_the_people_weighted_mean(self):
        a, b = [80.0] * 6, [40.0] * 6
        weighted = PI.aggregate_dial([a, b], 'NCIS', 'k', weights=[3.0, 1.0])
        plain = PI.aggregate_dial([a, b], 'NCIS', 'k')
        for v in weighted:
            self.assertAlmostEqual(v, 70.0, delta=0.6)
        for v in plain:
            self.assertAlmostEqual(v, 60.0, delta=0.6)

    def test_equal_weights_leave_the_line_as_it_was(self):
        series = [[50.0, 52.0, 54.0], [60.0, 58.0, 56.0], [40.0, 45.0, 50.0]]
        self.assertEqual(PI.aggregate_dial(series, 'NCIS', 'k', weights=[7.0, 7.0, 7.0]), PI.aggregate_dial(series, 'NCIS', 'k'))
        # a weight list that cannot be used (empty, zeros, the wrong length) is ignored, never an error
        self.assertEqual(PI.aggregate_dial(series, 'NCIS', 'k', weights=[0.0, 0.0, 0.0]), PI.aggregate_dial(series, 'NCIS', 'k'))
        self.assertEqual(PI.aggregate_dial(series, 'NCIS', 'k', weights=[1.0]), PI.aggregate_dial(series, 'NCIS', 'k'))


class Prepared(unittest.TestCase):
    def test_prepared_test_counts_by_people_and_is_stamped(self):
        twin, sess = _prepared(_unequal_room)
        self.assertEqual(sess['dial']['weighting'], 'people')
        self.assertEqual(sess['dial']['weighting'], PI.DIAL_WEIGHTING)
        self.assertTrue(PI.dial_is_weighted(sess))
        w = PI.seat_weights(sess['group']['roster'], sess['group']['segments'])
        sd = sess['seat_dials']
        line = sess['dial']['value']
        for i in range(len(line)):
            self.assertAlmostEqual(line[i], _wmean(sd, w, i), delta=0.6, msg=f"point {i}")
        # the seats differ and stand for different people, so the weighted line leaves the plain average somewhere
        gaps = [abs(_wmean(sd, w, i) - _plain(sd, i)) for i in range(len(line))]
        self.assertGreater(max(gaps), 0.0)
        at = gaps.index(max(gaps))
        self.assertAlmostEqual(line[at], _wmean(sd, w, at), delta=0.6)
        # the dial average on the scorecard reads the line
        tail = max(3, int(len(line) * 0.22))
        self.assertAlmostEqual(sess['scorecard']['overall_like'], sum(line[-tail:]) / tail, delta=0.6)

    def test_group_lines_count_their_seats_by_people(self):
        twin, sess = _prepared(_unequal_room, sid='w2')
        w = PI.seat_weights(sess['group']['roster'], sess['group']['segments'])
        sd = sess['seat_dials']
        splits = sess['dial']['splits']
        self.assertIn('women', splits)
        women = {str(r['seat']): sd[str(r['seat'])] for r in sess['group']['roster'] if PI._is_woman(r)}
        self.assertGreaterEqual(len(women), 2)
        for i in (2, len(splits['women']['value']) - 3):
            self.assertAlmostEqual(splits['women']['value'][i], _wmean(women, w, i), delta=0.6)
        # a group of seats that all stand for the same people is still its plain average
        same = {k: v for k, v in women.items()}
        self.assertAlmostEqual(_wmean(same, {k: 1.0 for k in same}, 2), _plain(same, 2), delta=1e-9)

    def test_equal_seats_are_the_plain_average(self):
        twin, sess = _prepared(_equal_room, sid='w3')
        sd = sess['seat_dials']
        line = sess['dial']['value']
        plain = PI.aggregate_dial([sd[k] for k in sd], 'Jennifer Beals', f"{sess['media']['hash']}|x")
        # the same deterministic jitter needs the same media key; compare to the plain mean within it instead
        for i in (1, len(line) // 2, len(line) - 2):
            self.assertAlmostEqual(line[i], _plain(sd, i), delta=0.6)
        self.assertEqual(len(plain), len(line))
        self.assertEqual(sess['dial']['weighting'], 'people')


class Upgrade(unittest.TestCase):
    def test_old_session_is_recounted_and_stamped(self):
        twin, sess = _prepared(_unequal_room, sid='w4')
        w = PI.seat_weights(sess['group']['roster'], sess['group']['segments'])
        sd = sess['seat_dials']
        # make it an older test: the plain average on the dial, no stamp, the old dial average on the card
        old = copy.deepcopy(sess)
        old['dial'].pop('weighting', None)
        old['dial']['value'] = [round(_plain(sd, i), 4) for i in range(len(old['dial']['t']))]
        for row in old['dial']['splits'].values():
            row['value'] = [v + 0.5 for v in row['value']]
        tail = max(3, int(len(old['dial']['value']) * 0.22))
        old['scorecard']['overall_like'] = round(sum(old['dial']['value'][-tail:]) / tail, 4)
        old_avg = old['scorecard']['overall_like']
        old['probes'] = []
        got = PI.reweight_dial(old, twin)
        self.assertIsNotNone(got)
        self.assertEqual(got['seats'], len(sd))
        self.assertAlmostEqual(got['old_avg'], old_avg)
        self.assertTrue(PI.dial_is_weighted(old))
        self.assertTrue(old['dial'].get('reweighted_at'))
        for i in (3, len(old['dial']['value']) - 2):
            self.assertAlmostEqual(old['dial']['value'][i], _wmean(sd, w, i), delta=0.6)
        # the splits came back onto the weighted line, the probes were re-read, the card reads the line
        women = {str(r['seat']): sd[str(r['seat'])] for r in old['group']['roster'] if PI._is_woman(r)}
        self.assertAlmostEqual(old['dial']['splits']['women']['value'][2], _wmean(women, w, 2), delta=0.6)
        self.assertTrue(old['probes'])
        line = old['dial']['value']
        self.assertAlmostEqual(old['scorecard']['overall_like'], sum(line[-tail:]) / tail, delta=0.6)
        self.assertAlmostEqual(got['new_avg'], old['scorecard']['overall_like'])
        fresh = PI.scorecard(twin, old['group']['roster'], old['beats'], line, old['events'], sd, 'Jennifer Beals', old['media']['hash'], segments=old['group']['segments'])
        self.assertEqual(old['scorecard']['dead_spot']['clock'], fresh['dead_spot']['clock'])
        # the battery, the people and the format are untouched
        self.assertEqual(old['scorecard']['battery'], sess['scorecard']['battery'])
        self.assertEqual(old['scorecard']['people'], sess['scorecard']['people'])
        # idempotent
        self.assertIsNone(PI.reweight_dial(old, twin))
        # a test already counted this way is left alone
        self.assertIsNone(PI.reweight_dial(sess, twin))

    def test_a_test_without_seat_dials_on_the_clock_is_left_alone(self):
        twin, sess = _prepared(_unequal_room, sid='w5')
        sess['dial'].pop('weighting', None)
        sess['seat_dials'] = {k: v[:-1] for k, v in sess['seat_dials'].items()}
        self.assertIsNone(PI.reweight_dial(sess, twin))
        self.assertFalse(PI.dial_is_weighted(sess))
        bare = {'id': 'x', 'status': 'stopped', 'group': {'roster': []}}
        self.assertIsNone(PI.reweight_dial(bare, twin))

    def test_everything_a_reader_sees_agrees_with_the_line(self):
        twin, sess = _prepared(_unequal_room, sid='w6')
        PI.stop_session(sess)
        line = sess['dial']['value']
        pub = PI.public_session(sess)
        self.assertEqual(pub['dial']['value'], line)
        self.assertEqual(pub['dial']['weighting'], 'people')
        self.assertEqual(pub['scorecard']['overall_like'], sess['scorecard']['overall_like'])
        self.assertTrue(PI.session_lines(sess))
        from migration import digital_twin_exports as EXP
        rows = EXP._scorecard_rows(sess['scorecard'])
        dial_row = next(r for r in rows if r[0] == 'Dial average')
        self.assertEqual(dial_row[1], f"{float(sess['scorecard']['overall_like']):.1f}")
        csv = PI.session_csv(sess)
        # the dial block in the CSV carries the line's own values, as cells (a value whose fourth decimal is 0 prints
        # as 54.645, not 54.6450, so the match is the cell the writer puts down, never a re-formatted string)
        self.assertIn(f",{line[0]},", csv)
        self.assertIn(f",{line[-1]},", csv)


class Live(unittest.TestCase):
    def test_live_frames_count_by_people(self):
        tw = _beals()
        tw['key'] = 'jb'
        tw['meta']['size_us'] = 4_237_511
        sess = PI.open_session([tw], in_room=8, title='Season 2 pilot', created_by='t', mode='pilot', session_id='liveweight01')
        PI.apply_watch_url(sess, P_PLUS, hint_title='NCIS: New York, episode 1', mode='pilot')
        _unequal_room(sess)
        LIVE.seat_live(sess, {'jb': tw})
        self.assertEqual(sess['dial'].get('weighting'), 'people')
        LIVE.start_live(sess)
        PI.mark_playing(sess)
        img = _png_data_url()
        for t in (12.0, 24.0, 36.0):
            out = LIVE.frame(sess, tw, t, img)
            self.assertFalse(out.get('skipped'))
        w = PI.seat_weights(sess['group']['roster'], sess['group']['segments'])
        for i, room in enumerate(sess['dial']['value']):
            seats = {k: v[i][1] for k, v in sess['live']['seat_dials'].items()}
            want = sum(seats[k] * w[k] for k in seats) / sum(w[k] for k in seats)
            self.assertAlmostEqual(room, want, delta=0.02, msg=f"frame {i}")
        women = sess['dial']['splits'].get('women')
        if women:
            wk = [str(r['seat']) for r in sess['group']['roster'] if PI._is_woman(r)]
            seats = {k: sess['live']['seat_dials'][k][0][1] for k in wk}
            want = sum(seats[k] * w[k] for k in seats) / sum(w[k] for k in seats)
            self.assertAlmostEqual(women['value'][0], want, delta=0.02)


class Page(unittest.TestCase):
    def test_page_counts_live_cohorts_by_people(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            page = fh.read()
        self.assertIn('function piSeatWeights(s)', page)
        self.assertIn('wts = piSeatWeights(s)', page)
        self.assertIn('sums[tk] = (sums[tk] || 0) + v * w; counts[tk] = (counts[tk] || 0) + w;', page)
        self.assertNotIn('\u2014', page[page.find('function piSeatWeights(s)'):page.find('function piSeatWeights(s)') + 1200])


if __name__ == '__main__':
    unittest.main(verbosity=1)
