#!/usr/bin/env python3
"""Pilot Testing, live (2026-10-30): a watch link is never read ahead of
Play; the room reacts to frames off the mirrored screen; Stop scores
only what was watched. Hermetic (REGRESSION_TEST_MODE answers frames
deterministically)."""
from __future__ import annotations

import base64
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
from scripts.test_digital_twin_pilot import _beals  # noqa: E402

P_PLUS = 'https://www.paramountplus.com/shows/video/ALVE01M3QWCNZYE2VR099PZGTZH04W/'


def _png_data_url(size=8):
    raw = b''.join(b'\x00' + b'\x10\x20\x30' * size for _ in range(size))
    def chunk(tag, data):
        c = struct.pack('>I', len(data)) + tag + data
        return c + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')


def _session():
    tw = _beals()
    tw['key'] = 'jb'
    tw['meta']['size_us'] = 4_237_511   # the people behind the room, which the forecast needs for its counts
    sess = PI.open_session([tw], in_room=12, title='Season 2 pilot', created_by='t', mode='pilot', session_id='livetest0001')   # a fixed id seats the same room every run
    PI.apply_watch_url(sess, P_PLUS, hint_title='NCIS: New York, episode 1', mode='pilot')
    return tw, sess


class LiveWatch(unittest.TestCase):
    def test_watch_link_is_live_and_file_is_not(self):
        tw, sess = _session()
        self.assertTrue(LIVE.is_live(sess))
        sess['media']['s3_key'] = 'digital-twins/media/x.mp4'
        self.assertFalse(LIVE.is_live(sess))

    def test_seat_live_reads_nothing_ahead_of_play(self):
        tw, sess = _session()
        LIVE.seat_live(sess, {'jb': tw})
        self.assertEqual(sess['status'], 'ready')
        self.assertIsNone(sess['scorecard'])
        self.assertEqual(sess['dial']['value'], [])
        self.assertEqual(sess['events'], [])
        self.assertEqual(sess['watch']['source'], 'live')
        self.assertIn('mirror', sess['prepare_note'].lower())
        pub = PI.public_session(sess)
        self.assertIsNone(pub['scorecard'])

    def test_frames_move_the_room(self):
        tw, sess = _session()
        LIVE.seat_live(sess, {'jb': tw})
        LIVE.start_live(sess)
        PI.mark_playing(sess)
        img = _png_data_url()
        first = LIVE.frame(sess, tw, 12.0, img)
        self.assertFalse(first.get('skipped'))
        self.assertTrue(first['caption'])
        self.assertEqual(len(sess['dial']['t']), 1)
        self.assertEqual(len(sess['live']['frames']), 1)
        self.assertEqual(len(sess['live']['seat_dials']), 12)
        too_soon = LIVE.frame(sess, tw, 14.0, img)
        self.assertTrue(too_soon.get('skipped'))
        for t in (24.0, 36.0, 48.0, 60.0):
            out = LIVE.frame(sess, tw, t, img)
            self.assertFalse(out.get('skipped'))
        self.assertEqual(len(sess['dial']['t']), 5)
        self.assertEqual(len(sess['scenes']), 5)
        self.assertEqual(sess['scenes'][0]['t_end'], 24.0)
        for key in ('women', 'men'):
            if key in sess['dial']['splits']:
                self.assertEqual(len(sess['dial']['splits'][key]['value']), 5)
        for ev in sess['events']:
            self.assertIn(ev['type'], LIVE.EVENTS)
            self.assertTrue(ev.get('live'))
        self.assertIsNone(sess['scorecard'])

    def test_seats_take_their_own_shape(self):
        # Jenna 2026-11-03: the cohort lines all ebbed and flowed together at different levels. A model read that
        # herds the room (+3 for everyone) on an ordinary frame comes out with the cohorts pulling apart; a tender
        # beat moves the women and the men differently; a shared moment (a joke) keeps the room moving together.
        tw, sess = _session()
        LIVE.seat_live(sess, {'jb': tw})
        LIVE.start_live(sess)
        PI.mark_playing(sess)
        roster = sess['group']['roster']
        for i, r in enumerate(roster):   # two cohorts, by gender, so the split lines exist
            r['segment'] = 'Women' if str(r.get('gender') or 'F').upper().startswith('F') else 'Men'
        before = {str(r['seat']): 55.0 for r in roster}
        herd = {str(r['seat']): 58.0 for r in roster}
        quiet = LIVE.shape_moves(sess, tw, roster, {'t': 12.0, 'tone': 'quiet', 'joke': False, 'shift': False}, before, herd)
        moves = [quiet[str(r['seat'])] - 55.0 for r in roster]
        self.assertEqual(len(set(round(m, 2) for m in moves)), len(moves), 'no two seats move alike')
        self.assertTrue(min(moves) < 0.6 or (max(moves) - min(moves)) > 3.0, f'the room is no longer a herd: {moves}')
        self.assertLess(abs(sum(moves) / len(moves) - 3.0 * LIVE.HERD_KEEP), 2.0, 'the room level still follows the read, damped')
        tender = LIVE.shape_moves(sess, tw, roster, {'t': 24.0, 'tone': 'tender', 'joke': False, 'shift': False}, before, herd)
        w = [tender[str(r['seat'])] - 55.0 for r in roster if r['segment'] == 'Women']
        m = [tender[str(r['seat'])] - 55.0 for r in roster if r['segment'] == 'Men']
        if w and m:
            self.assertGreater(sum(w) / len(w), sum(m) / len(m) + 1.0, 'a tender beat lifts the women past the men')
        action = LIVE.shape_moves(sess, tw, roster, {'t': 36.0, 'tone': 'action', 'joke': False, 'shift': False}, before, herd)
        w2 = [action[str(r['seat'])] - 55.0 for r in roster if r['segment'] == 'Women']
        m2 = [action[str(r['seat'])] - 55.0 for r in roster if r['segment'] == 'Men']
        if w2 and m2:
            self.assertGreater(sum(m2) / len(m2), sum(w2) / len(w2), 'an action beat pulls the men past the women')
        joke = LIVE.shape_moves(sess, tw, roster, {'t': 48.0, 'tone': 'funny', 'joke': True, 'shift': False}, before, herd)
        jm = [joke[str(r['seat'])] - 55.0 for r in roster]
        self.assertGreater(sum(jm) / len(jm), 2.2, 'a shared laugh still moves the whole room up')
        # through frame(): the cohort split lines diverge in shape over a run, not only in level
        img = _png_data_url()
        for t in range(12, 12 * 9, 12):
            LIVE.frame(sess, tw, float(t), img)
        sp = sess['dial']['splits']
        if 'cohort_women' in sp and 'cohort_men' in sp:
            a, b = sp['cohort_women']['value'], sp['cohort_men']['value']
            da = [a[i] - a[i - 1] for i in range(1, len(a))]
            db = [b[i] - b[i - 1] for i in range(1, len(b))]
            same = sum(1 for x, y in zip(da, db) if (x > 0) == (y > 0))
            self.assertLess(same, len(da), 'the two cohort lines do not rise and fall in lockstep')
        for r in roster:
            r.pop('segment', None)

    def test_each_seat_follows_its_own_thought(self):
        # Jenna 2026-11-03: each seat responds the way that persona would, the dial matches what they think, and the
        # cohort is the aggregate of its seats. A read that carries a thought and a move per seat is taken as given.
        tw, sess = _session()
        LIVE.seat_live(sess, {'jb': tw})
        LIVE.start_live(sess)
        PI.mark_playing(sess)
        roster = sess['group']['roster']
        for r in roster:
            r['segment'] = 'Women' if str(r.get('gender') or 'F').upper().startswith('F') else 'Men'
        moves = {}
        for i, r in enumerate(roster):
            moves[str(r['seat'])] = [-6, 4, 0, 7, -2, 3, -5, 1, 6, -3, 2, -1][i % 12]
        real_fake = LIVE._fake_read

        def thoughtful(sess_, roster_, t, dials):
            out = {'caption': 'Two detectives argue in a hallway.', 'tone': 'tense', 'joke': False, 'setting': 'precinct', 'shift': False,
                   'seats': {s: {'say': f'seat {s} thinks about this', 'move': m} for s, m in moves.items()}, 'reactions': []}
            return out
        LIVE._fake_read = thoughtful
        try:
            before = {str(r['seat']): LIVE.DEFAULT_DIAL for r in roster}
            LIVE.frame(sess, tw, 12.0, _png_data_url())
        finally:
            LIVE._fake_read = real_fake
        sd = sess['live']['seat_dials']
        for r in roster:
            sno = str(r['seat'])
            self.assertAlmostEqual(sd[sno][-1][1], before[sno] + moves[sno], places=2, msg='the seat moved exactly where its thought took it')
            self.assertEqual(sess['live']['thoughts'][sno][-1][1], f'seat {sno} thinks about this')
        sp = sess['dial']['splits']
        for key, lab in (('cohort_women', 'Women'), ('cohort_men', 'Men')):
            if key in sp:
                xs = [sd[str(r['seat'])][-1][1] for r in roster if r['segment'] == lab]
                self.assertAlmostEqual(sp[key]['value'][-1], round(sum(xs) / len(xs), 2), places=2, msg='the cohort line is the mean of its seats')
        room = sess['dial']['value'][-1]
        allv = [sd[str(r['seat'])][-1][1] for r in roster]
        self.assertAlmostEqual(room, round(sum(allv) / len(allv), 2), places=2)
        # a lazy read (everyone +3, give or take) on an ordinary frame is the room, not the people: it is reshaped
        lazy = {str(r['seat']): 55.0 + 3.0 + (0.1 * (i % 3)) for i, r in enumerate(roster)}
        base = {str(r['seat']): 55.0 for r in roster}
        shaped = LIVE.shape_moves(sess, tw, roster, {'t': 24.0, 'tone': 'quiet', 'joke': False, 'shift': False}, base, lazy, trusted=True)
        self.assertGreater(len(set(round(v, 1) for v in shaped.values())), 6, 'a lazy herd comes out as different people')
        # an honest spread of thoughts stands untouched
        honest = {str(r['seat']): 55.0 + moves[str(r['seat'])] for r in roster}
        kept = LIVE.shape_moves(sess, tw, roster, {'t': 36.0, 'tone': 'quiet', 'joke': False, 'shift': False}, base, honest, trusted=True)
        for sno, v in honest.items():
            self.assertAlmostEqual(kept[sno], v, places=2)
        for r in roster:
            r.pop('segment', None)

    def test_the_test_stops_on_its_own_when_the_runtime_runs_out(self):
        # Jenna 2026-11-03: 'if they put in 42:35 it would stop automatically at 42 minutes and 35 seconds'
        import time as _time
        self.assertEqual(PI.parse_runtime('42:35'), 42 * 60 + 35)
        self.assertEqual(PI.parse_runtime('1:02:10'), 3600 + 120 + 10)
        self.assertEqual(PI.parse_runtime('42'), 2520.0)
        self.assertEqual(PI.parse_runtime('42.5'), 2550.0)
        self.assertEqual(PI.parse_runtime(''), 0.0)
        self.assertEqual(PI.parse_runtime('abc'), 0.0)
        tw, sess = _session()
        sess['media']['runtime_s'] = 42 * 60 + 35
        LIVE.seat_live(sess, {'jb': tw})
        LIVE.start_live(sess)
        PI.mark_playing(sess)
        self.assertTrue(sess.get('play_started_at'))
        now = _time.time()
        self.assertFalse(PI.auto_stop_due(sess, now=now + 60), 'a minute in, the test runs')
        self.assertFalse(PI.auto_stop_due(sess, now=now + 42 * 60 + 35 + 10), 'at the runtime the page stops it; the server waits out the grace')
        self.assertTrue(PI.auto_stop_due(sess, now=now + 42 * 60 + 35 + PI.AUTO_STOP_GRACE_S + 5), 'past the grace the server stops it')
        sess['status'] = 'stopped'
        self.assertFalse(PI.auto_stop_due(sess, now=now + 10_000), 'a stopped test is never stopped again')
        sess['status'] = 'playing'; sess['media']['runtime_s'] = 0
        self.assertFalse(PI.auto_stop_due(sess, now=now + 10_000), 'no runtime, no auto-stop')

    def test_bad_frames_are_refused(self):
        tw, sess = _session()
        LIVE.seat_live(sess, {'jb': tw})
        self.assertTrue(LIVE.frame(sess, tw, 10.0, '').get('skipped'))
        huge = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff' * (LIVE.FRAME_MAX_BYTES + 10)).decode('ascii')
        self.assertTrue(LIVE.frame(sess, tw, 10.0, huge).get('skipped'))
        self.assertEqual(sess['live']['frames'], [])

    def test_finish_scores_what_was_watched(self):
        tw, sess = _session()
        LIVE.seat_live(sess, {'jb': tw})
        LIVE.start_live(sess)
        PI.mark_playing(sess)
        img = _png_data_url()
        for t in range(12, 12 * 9, 12):
            LIVE.frame(sess, tw, float(t), img)
        live_events = len(sess['events'])
        an = LIVE.analysis_from_live(sess)
        self.assertEqual(len(an['scenes']), 8)
        self.assertEqual(an['runtime_s'], 108.0)
        LIVE.finish(sess, {'jb': tw})
        self.assertEqual(sess['status'], 'stopped')
        self.assertIsNotNone(sess['scorecard'])
        self.assertIn('headline', sess['scorecard']['battery'])
        self.assertTrue(sess['live']['finished_at'])
        self.assertEqual(len(sess['live']['record']['dial']['t']), 8)
        fc = sess.get('forecast') or {}   # the performance forecast rides on every scored test (2026-11-03)
        self.assertTrue(fc.get('episode') and fc['episode'].get('week_one_audience'), fc)
        self.assertTrue(fc.get('series') and fc.get('subscriptions'))
        self.assertEqual(len(sess['live']['record']['events']), live_events)
        self.assertEqual(float(sess['media']['runtime_s']), 108.0)
        self.assertTrue(sess['watch'].get('live'))

    def test_finish_with_no_frames_still_stops(self):
        tw, sess = _session()
        LIVE.seat_live(sess, {'jb': tw})
        self.assertIsNone(LIVE.analysis_from_live(sess))
        LIVE.finish(sess, {'jb': tw})
        self.assertEqual(sess['status'], 'stopped')

    def test_unstick_releases_a_lost_finish(self):
        tw, sess = _session()
        LIVE.seat_live(sess, {'jb': tw})
        sess['status'] = 'preparing'
        sess['live']['finishing_at'] = '2026-10-30T10:00:00Z'
        self.assertTrue(LIVE.unstick(sess))
        self.assertEqual(sess['status'], 'stopped')
        sess['status'] = 'preparing'
        sess['live']['finishing_at'] = LIVE._now()
        self.assertFalse(LIVE.unstick(sess))


if __name__ == '__main__':
    unittest.main(verbosity=1)
