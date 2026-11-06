#!/usr/bin/env python3
"""Every seat says who it stands for (Jenna, 2026-11-05: "on the bios it needs to say what this person represents.
like represents x number of people from the total").

Hermetic. The server puts ``stands_for_people`` and ``stands_for_share`` on each seat where the seat objects are
built (``build_group``), re-derives them when a room's sizes are recomputed or a test is reseated to quotas, and
fills them in on the way out for records from before them: the pilot state (``public_session``, which the page and
the popped-out room both paint from) and the room state (the ``_session`` loader every Focus Group IQ route reads).
The seat people add up to the room within rounding, no two seats in a segment print the same figure, none ends in
0, the share has one decimal. The page prints the line under the name row of both bio cards, with a fallback for
an older record.
"""
from __future__ import annotations

import copy
import importlib.util
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_panel as P  # noqa: E402
from migration import digital_twin_pilot as PI  # noqa: E402


def _twin():
    spec = importlib.util.spec_from_file_location('_seats_fixture', os.path.join(HERE, 'test_digital_twin_room_seats.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    t = mod._beals()
    t['meta']['size_us'] = 8_255_144
    return t


def _check_seats(tc, roster, total, segments=None):
    """The invariants on one roster: positive, not round, distinct within a segment, share to one decimal of the total."""
    by_seg = {}
    for r in roster:
        v = r.get('stands_for_people')
        tc.assertIsInstance(v, int, r)
        tc.assertGreater(v, 0, r)
        tc.assertNotEqual(v % 10, 0, f"seat {r.get('seat')} prints a round figure {v}")
        s = r.get('stands_for_share')
        tc.assertIsNotNone(s, r)
        tc.assertEqual(round(float(s), 1), float(s), f"share {s} is not one decimal")
        tc.assertEqual(float(s), round(v / float(total) * 100.0, 1))
        by_seg.setdefault(str(r.get('segment')), []).append(v)
    for lab, vals in by_seg.items():
        tc.assertEqual(len(vals), len(set(vals)), f"two seats in {lab} print the same figure: {vals}")
    if segments:
        for seg in segments:
            vals = by_seg.get(str(seg.get('label'))) or []
            tc.assertLessEqual(abs(sum(vals) - int(seg['people'])), 12 * len(vals) + 1, (seg['label'], sum(vals), seg['people']))


class BuiltRooms(unittest.TestCase):
    def test_one_segment_adds_up_to_the_room(self):
        g = P.build_group([_twin()], in_room=12, seed='stands|one')
        roster = g['roster']
        self.assertEqual(len(roster), 12)
        _check_seats(self, roster, g['size_people'], g['segments'])
        self.assertLessEqual(abs(sum(r['stands_for_people'] for r in roster) - g['size_people']), 12 * len(roster) + 1)
        self.assertEqual(P.room_total_people(g), g['size_people'])

    def test_two_segments_each_add_up_to_their_people(self):
        segs = [{'label': 'Women', 'filter': {'gender': 'F'}}, {'label': 'Men', 'filter': {'gender': 'M'}}]
        g = P.build_group([_twin()], segments=segs, in_room=12, seed='stands|two')
        self.assertEqual(len(g['segments']), 2)
        _check_seats(self, g['roster'], g['size_people'], g['segments'])
        self.assertLessEqual(abs(sum(r['stands_for_people'] for r in g['roster']) - g['size_people']), 12 * len(g['roster']) + 2)
        # the seat's own people are kept where they already read as a count; only a round one moves, and by a few units
        for r in g['roster']:
            own = int(r['people'])
            if own % 10:
                self.assertEqual(r['stands_for_people'], own)
            else:
                self.assertLess(r['stands_for_people'] - own, 20)

    def test_deterministic_and_idempotent(self):
        a = P.build_group([_twin()], in_room=12, seed='stands|same')
        b = P.build_group([_twin()], in_room=12, seed='stands|same')
        self.assertEqual([(r['stands_for_people'], r['stands_for_share']) for r in a['roster']],
                         [(r['stands_for_people'], r['stands_for_share']) for r in b['roster']])
        before = copy.deepcopy(a['roster'])
        P.annotate_seat_stands_for(a)
        self.assertEqual(a['roster'], before)

    def test_recomputed_sizes_re_derive_the_figures(self):
        t = _twin()
        g = P.build_group([t], in_room=12, seed='stands|resize')
        sess = {'id': 'resize1', 'group': g, 'twin_keys': ['jb']}
        t2 = copy.deepcopy(t)
        t2['meta']['size_us'] = 12_413_227
        changed = P.recompute_group_sizes(sess, {'jb': t2})
        self.assertTrue(changed)
        _check_seats(self, g['roster'], g['size_people'], g['segments'])
        self.assertLessEqual(abs(sum(r['stands_for_people'] for r in g['roster']) - g['size_people']), 12 * len(g['roster']) + 1)


class OlderRecords(unittest.TestCase):
    def _old(self):
        segs = [{'label': 'Women', 'filter': {'gender': 'F'}}, {'label': 'Men', 'filter': {'gender': 'M'}}]
        g = P.build_group([_twin()], segments=segs, in_room=12, seed='stands|old')
        for r in g['roster']:
            for k in ('people', 'stands_for_people', 'stands_for_share'):
                r.pop(k, None)
        return {'id': 'old1', 'group': g}

    def test_segment_share_over_its_seats(self):
        sess = self._old()
        self.assertTrue(P.ensure_seat_stands_for(sess))
        g = sess['group']
        _check_seats(self, g['roster'], g['size_people'], g['segments'])
        self.assertFalse(P.ensure_seat_stands_for(sess))     # a second pass changes nothing
        # the fallback spreads the seats a few tenths of a percent apart, never a ladder of consecutive counts
        for seg in g['segments']:
            vals = sorted(r['stands_for_people'] for r in g['roster'] if r['segment'] == seg['label'])
            if len(vals) > 2:
                self.assertGreater(vals[-1] - vals[0], len(vals) * 10)

    def test_a_record_with_no_segment_people_reads_the_room_total(self):
        sess = self._old()
        g = sess['group']
        for s in g['segments']:
            s['people'] = 0
        P.ensure_seat_stands_for(sess)
        total = g['size_people']
        self.assertEqual(P.room_total_people(g), total)
        _check_seats(self, g['roster'], total)
        self.assertLessEqual(abs(sum(r['stands_for_people'] for r in g['roster']) - total), 12 * len(g['roster']) + 2)

    def test_nothing_to_annotate_is_safe(self):
        self.assertFalse(P.ensure_seat_stands_for({}))
        self.assertFalse(P.ensure_seat_stands_for({'group': {'roster': []}}))
        self.assertEqual(P.annotate_seat_stands_for(None), {})


class PilotState(unittest.TestCase):
    def test_public_session_carries_the_figures(self):
        sess = PI.open_session([_twin()], in_room=12, title='Stands test', created_by='jenna', session_id='stands00001')
        pub = PI.public_session(sess)
        roster = pub['group']['roster']
        self.assertEqual(len(roster), 12)
        _check_seats(self, roster, pub['group']['size_people'], pub['group']['segments'])

    def test_a_test_from_before_the_figures_gets_them_on_the_way_out(self):
        sess = PI.open_session([_twin()], in_room=12, title='Stands test', created_by='jenna', session_id='stands00002')
        for r in sess['group']['roster']:
            r.pop('stands_for_people', None)
            r.pop('stands_for_share', None)
        stored = copy.deepcopy(sess['group'])
        pub = PI.public_session(sess)
        _check_seats(self, pub['group']['roster'], pub['group']['size_people'], pub['group']['segments'])
        self.assertEqual(sess['group'], stored)      # the view fills them in; the stored record is not rewritten
        self.assertTrue(all(r['stands_for_people'] == r['people'] or r['people'] % 10 == 0 for r in pub['group']['roster']))

    def test_quotas_reseat_keeps_the_shares_against_the_room(self):
        from migration import digital_twin_compare as C
        t = _twin()
        sess = PI.open_session([t], in_room=12, title='Quota test', created_by='jenna', session_id='stands00003')
        C.apply_quotas(sess, [t], {'women_pct': 50, 'under_35_pct': 50}, seed='stands|quotas')
        g = sess['group']
        self.assertGreaterEqual(len(g['segments']), 2)
        total = P.room_total_people(g)
        _check_seats(self, g['roster'], total, g['segments'])
        # a seat drawn from a cell's sub-room carries its share of this room, not of the sub-room it came from
        for r in g['roster']:
            self.assertLess(float(r['stands_for_share']), 100.0 / len(g['roster']) * 3)


class RoomState(unittest.TestCase):
    def test_the_session_loader_fills_an_older_room(self):
        try:
            import flask  # noqa: F401
        except Exception:
            self.skipTest('flask not installed in this interpreter')
        import digital_twins_api as A
        g = P.build_group([_twin()], in_room=12, seed='stands|room')
        for r in g['roster']:
            r.pop('stands_for_people', None)
            r.pop('stands_for_share', None)
        payload = {'panel_sessions': [{'id': 'r1', 'group': g}]}
        sess = A._session(payload, 'r1')
        self.assertIsNotNone(sess)
        _check_seats(self, sess['group']['roster'], g['size_people'], g['segments'])
        self.assertIsNone(A._session(payload, 'nope'))
        with open(os.path.join(ROOT, 'digital_twins_api.py'), encoding='utf-8') as fh:
            src = fh.read()
        open_fn = src[src.find('def dt_panel_open('):src.find('def _charge_question(')]
        self.assertIn('P.ensure_seat_stands_for(sess)', open_fn)     # a reused room gets them too


class ThePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            cls.page = fh.read()

    def _fn(self, name):
        m = re.search(r'^ {12}function ' + re.escape(name) + r'\(.*?^ {12}\}', self.page, re.S | re.M)
        self.assertTrue(m, name)
        return m.group(0)

    def test_both_cards_print_the_line_under_the_name_row(self):
        helper = self._fn('seatStandsFor')
        self.assertIn('r.stands_for_people', helper)
        self.assertIn('r.stands_for_share', helper)
        self.assertIn('perSeat(g, r)', helper)                      # the fallback for a record from before the figures
        self.assertIn('people % 10 === 0) people += (idx % 9) + 1', helper)
        line = self._fn('standsForLine')
        self.assertIn("' stands for ' + fmtN(sf.people) + ' of the ' + fmtN(sf.total) + ' people in this audience'", line)
        self.assertIn('Number(sf.share).toFixed(1)', line)
        self.assertNotIn('household', line.lower())
        pilot = self._fn('piSeatBio')
        self.assertIn("standsForLine(nm, (S.pilot || {}).group || {}, r)", pilot)
        self.assertIn("return head + (stands ? '<p class=\"dtw-pi-stands\">' + stands + '</p>' : '') + thought + body;", pilot)
        room = self._fn('renderSeatPanel')
        self.assertIn("standsForLine(nm, g, r)", room)
        self.assertNotIn("'stands for about '", room)
        self.assertIn('#digitalTwinsIQView .dtw-pi-biocard .dtw-pi-stands {', self.page)

    def test_the_chat_column_wraps_inside_itself(self):
        page = self.page
        who = re.search(r'#digitalTwinsIQView \.dtw-pi-seatrow \.who \{[^\n]*\n[^\n]*\.who \.nm \{([^}]*)\}', page)
        self.assertTrue(who)
        nm = who.group(1)
        self.assertIn('white-space: normal', nm)
        self.assertIn('overflow-wrap: anywhere', nm)
        self.assertNotIn('nowrap', nm)
        self.assertNotIn('ellipsis', nm)
        thread = re.search(r'#digitalTwinsIQView \.dtw-pi-thread \{([^}]*)\}', page).group(1)
        self.assertIn('padding: 0 6px', thread)                  # room for the rows' edge and the cohort ring inside the scrolling box
        self.assertIn('overflow-y: auto', thread)
        self.assertIn('overflow-x: hidden', thread)
        self.assertIn('#digitalTwinsIQView .dtw-pi-askrow { display: flex; flex-wrap: wrap;', page)
        self.assertNotIn('#digitalTwinsIQView .dtw-pi-thread, #digitalTwinsIQView .dtw-pi-seatlist { padding-left: 2px; }', page)
        self.assertIn('#digitalTwinsIQView .dtw-pi-seatlist { padding-left: 2px; }', page)


if __name__ == '__main__':
    unittest.main()
