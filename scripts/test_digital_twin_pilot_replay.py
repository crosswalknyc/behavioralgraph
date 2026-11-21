#!/usr/bin/env python3
"""Pilot Testing keeps the dial after a test finishes and runs it back (Jenna 2026-11-19:
"after one finishes you see the dial test still and can run the timeline back/forward/etc
to see what was happening at that time if you want").

The replay reads the test's own record: the dial on its clock, every seat's dial on the
same clock, the room's splits, the reactions with their second, the scenes with their
bounds, the probes and the questions with the second they were asked. This suite holds
the record a finished test carries into its public view, keeps a test without a record
harmless, and pins the page's transport, scrubber and moment panel with clean copy.
Hermetic: no model, no network.
"""
from __future__ import annotations

import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import _index_page  # noqa: E402  (the page with its split files inlined back, 2026-11-20)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_pilot as PI  # noqa: E402

sys.path.insert(0, HERE)
from test_digital_twin_pilot import _beals  # noqa: E402


def _finished(duration=120, seats=8, sid='replay1'):
    twin = _beals()
    sess = PI.open_session([twin], title='NCIS: New York pilot', in_room=seats)
    sess['id'] = sid
    PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=duration, title='NCIS: New York pilot')
    PI.mark_playing(sess)
    PI.stop_session(sess)
    return sess


class Record(unittest.TestCase):
    def test_finished_test_carries_the_record_the_replay_reads(self):
        sess = _finished()
        self.assertEqual(sess['status'], 'stopped')
        pub = PI.public_session(sess)
        d = pub['dial']
        self.assertGreater(len(d['t']), 1)
        self.assertEqual(len(d['t']), len(d['value']))
        self.assertEqual(d['t'], sorted(d['t']))
        # every seat's dial sits on the dial's clock, so a seat's move between two seconds can be read
        roster = pub['group']['roster']
        self.assertEqual(len(roster), 8)
        for r in roster:
            series = pub['seat_dials'][str(r['seat'])]
            self.assertEqual(len(series), len(d['t']), r['seat'])
        # the reactions carry the second, the seat and the words
        self.assertTrue(pub['events'])
        for e in pub['events']:
            self.assertIn('t', e)
            self.assertIn('seat', e)
            self.assertTrue(str(e.get('line') or e.get('type') or '').strip())
            self.assertLessEqual(float(e['t']), d['t'][-1] + 0.01)
        # the scenes bound every second of the cut
        sc = pub['scenes']
        self.assertTrue(sc)
        self.assertAlmostEqual(float(sc[0]['t_start']), float(d['t'][0]), delta=4.1)
        self.assertGreaterEqual(float(sc[-1]['t_end']), float(d['t'][-1]) - 0.01)
        for s in sc:
            self.assertIn('caption', s)
            self.assertIn('act', s)
        # the probes and the splits ride along
        self.assertIn('probes', pub)
        self.assertIn('splits', d)
        # and the scorecard is still there: nothing the finished test showed is lost
        self.assertTrue(pub['scorecard'])

    def test_stop_keeps_the_record(self):
        sess = _finished(sid='replay2')
        before = (list(sess['dial']['t']), list(sess['dial']['value']), len(sess['events']), len(sess['scenes']))
        PI.stop_session(sess)   # a second Stop (the page's end-of-cut and the button can both fire) changes nothing
        after = (list(sess['dial']['t']), list(sess['dial']['value']), len(sess['events']), len(sess['scenes']))
        self.assertEqual(before, after)
        self.assertEqual(sess['status'], 'stopped')

    def test_question_asked_during_the_test_keeps_its_second(self):
        sess = _finished(sid='replay3')
        rec = PI.stamp_question({'id': 'q1', 'q': 'Would you keep watching?'}, 61.3)
        self.assertAlmostEqual(float(rec['playhead_s']), 61.3)
        self.assertEqual(rec['playhead_clock'], '1:01')
        sess['questions'] = [rec]
        pub = PI.public_session(sess)
        self.assertEqual(pub['questions'][0]['playhead_clock'], '1:01')

    def test_a_test_without_a_record_is_harmless(self):
        twin = _beals()
        sess = PI.open_session([twin], title='Never prepared', in_room=4)
        PI.stop_session(sess)
        pub = PI.public_session(sess)
        self.assertEqual(pub['status'], 'stopped')
        self.assertFalse((pub.get('dial') or {}).get('t'))
        self.assertEqual(pub.get('events') or [], [])
        # the lines and the CSV still write
        self.assertTrue(PI.session_lines(sess))
        self.assertIn('NCIS', PI.session_csv(_finished(sid='replay4')))
        self.assertTrue(PI.session_csv(sess))


class Page(unittest.TestCase):
    def setUp(self):
        self.page = _index_page.text(ROOT)   # the page with its split files inlined back
        start = self.page.find('function hasMedia(s)')
        end = self.page.find('window.dtwPilotRename')
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        self.blob = self.page[start:end]

    def test_transport_scrubber_and_moment_are_on_the_page(self):
        for must in ('window.dtwPilotReplay', 'window.dtwPilotStep', 'window.dtwPilotJump', 'window.dtwPilotRate',
                     'function piTransportHtml', 'function piScrubHtml', 'function paintPilotScrub', 'function piNowHtml', 'function paintPilotNow',
                     'id="dtwPiTransport"', 'id="dtwPiScrub"', 'id="dtwPiReplayBtn"', 'role="slider"',
                     "k === 'ArrowLeft'", "k === 'ArrowRight'", "k === 'Home'", "k === 'End'", "k === ' '",
                     'lineRest', 'rgba(199,242,62,0.9)',
                     'Run it back from any second', 'Said in the last half minute', 'Moved most', 'Nothing said in the last half minute',
                     'kept no second-by-second readings'):
            self.assertIn(must, self.blob, must)
        # the player mounts them only on a finished test, and the console opens a finished test at its stop point
        self.assertIn("piReplayable(s) ? piTransportHtml(s)", self.blob)
        self.assertIn("piReplayable(s) ? piScrubHtml(s) : ''", self.blob)
        self.assertIn("S.pilotClock.t = piStopPoint(S.pilot)", self.blob)
        # the picture reaching its end during a replay is never a second Stop
        self.assertIn("if (piReplayable(S.pilot)) { piReplayHalt(true); return; } try { dtwPilotStop(true); }", self.blob)

    def test_copy_is_plain(self):
        low = self.blob.lower()
        for tok in ('synth', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap', 'panel-projected'):
            self.assertNotIn(tok, low, tok)
        self.assertNotIn('\u2014', self.blob)
        self.assertNotIn('\u2013', self.blob)
        # the reader's strings inside the replay block
        a = self.blob.find('S.pilotReplay = S.pilotReplay ||')
        b = self.blob.find('function playheadNow()')
        self.assertGreater(a, 0)
        self.assertGreater(b, a)
        block = self.blob[a:b]
        strings = re.findall(r"'([^'\\]*(?:\\.[^'\\]*)*)'", block)
        text = ' '.join(s for s in strings if ' ' in s).lower()
        for tok in ('research', 'model', 'survey', 'archetype', 'persona', 'segment', 'cohort', 'prediction', 'synthetic', 'calibrat', 'nielsen', 'confidence'):
            self.assertNotIn(tok, text, tok)

    def test_styles_are_present(self):
        for sel in ('.dtw-pi-transport', '.dtw-pi-tb', '.dtw-pi-scrub', '.dtw-pi-scrub .hd', '.dtw-pi-now', '.dtw-pi-nowface', '.dtw-pi-ticker .ph.rp'):
            self.assertIn('#digitalTwinsIQView ' + sel, self.page, sel)


if __name__ == '__main__':
    unittest.main(verbosity=1)
