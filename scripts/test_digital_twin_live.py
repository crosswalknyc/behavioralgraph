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
    sess = PI.open_session([tw], in_room=12, title='Season 2 pilot', created_by='t', mode='pilot')
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
