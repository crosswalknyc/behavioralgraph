#!/usr/bin/env python3
"""The performance forecast on a scored pilot test (Jenna 2026-11-03): week one if released, the season, the
subscriptions it would move, and the calibration ledger that anchors it to episodes as aired.

Run:  REGRESSION_TEST_MODE=1 python3 scripts/test_digital_twin_forecast.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from migration import digital_twin_forecast as F  # noqa: E402


def _twin(size=83_791_763, lapsing=17.9, second=61.7, loyalty=63.8, churn=54.2, name='Paramount+'):
    return {'meta': {'name': name, 'subject': name, 'size_us': size, 'platform_home': name, 'format': 'brand', 'subject_kind': 'audience'},
            'traits': {'second_screen': {'value': second}, 'franchise_loyalty': {'value': loyalty}, 'churn_propensity': {'value': churn}},
            'lifecycle': {'lapsing_pct': lapsing, 'habit_pct': 36.8, 'trying_pct': 16.1, 'discovering_pct': 16.5, 'returning_pct': 12.7},
            'pilot_sessions': []}


def _sess(sid, title, tp, rec, fin, mode='pilot', genre='action', seats=12, checked=5, dial=None):
    roster = [{'seat': i + 1, 'name': f'Seat{i + 1}', 'age': 40, 'gender': 'F' if i % 2 else 'M'} for i in range(seats)]
    events = [{'type': 'check-out', 'seat': i + 1, 't': 30.0 * i} for i in range(checked)] + [{'type': 'laugh', 'seat': 1, 't': 12.0}]
    return {'id': sid, 'title': title, 'mode': mode, 'genre': genre, 'status': 'stopped',
            'media': {'title': title, 'runtime_s': 2580.0},
            'group': {'roster': roster}, 'events': events,
            'dial': {'t': list(range(0, 600, 10)), 'value': dial or [50.0 + (i % 7) for i in range(60)]},
            'scorecard': {'battery': {'headline': {'total_positive_pct': tp, 'definitely_recommend_pct': rec, 'finish_pct': fin, 'excellent_pct': 0.7}},
                          'norm_columns': {'total_positive': {'norm': 65.0, 'genre_norm': 65.0}, 'definitely_recommend': {'norm': 50.0, 'genre_norm': 50.0}, 'finish': {'norm': None}}}}


class _S3:
    def __init__(self):
        self.store = {}

    def get_object(self, Bucket, Key):
        if Key not in self.store:
            raise KeyError('NoSuchKey')
        import io
        return {'Body': io.BytesIO(self.store[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.store[Key] = Body


class Forecast(unittest.TestCase):
    def test_appeal_reads_the_room_against_the_norms(self):
        a = F.appeal(_sess('a', 'X Pilot', 65.0, 50.0, 85.0)['scorecard'])
        self.assertAlmostEqual(a['score'], 1.0, places=2)
        weak = F.appeal(_sess('b', 'X Pilot', 19.3, 1.6, 81.9)['scorecard'])
        strong = F.appeal(_sess('c', 'X Episode 2', 38.1, 30.0, 91.8)['scorecard'])
        self.assertLess(weak['score'], strong['score'])

    def test_a_better_cut_draws_more_and_moves_more_subscriptions(self):
        tw = _twin()
        weak = F.forecast_for_session(tw, _sess('w', 'X Pilot', 19.3, 1.6, 81.9), ledger=F._empty_ledger())
        strong = F.forecast_for_session(tw, _sess('s', 'X Pilot', 38.1, 30.0, 91.8), ledger=F._empty_ledger())
        self.assertGreater(strong['episode']['week_one_audience'], weak['episode']['week_one_audience'])
        self.assertGreater(strong['subscriptions']['subscribe_pct_of_nonsubscribers'], weak['subscriptions']['subscribe_pct_of_nonsubscribers'])
        self.assertGreater(strong['subscriptions']['reactivate_pct_of_dormant'], weak['subscriptions']['reactivate_pct_of_dormant'])
        self.assertGreaterEqual(strong['series']['season_completion_pct'], weak['series']['season_completion_pct'])
        for fc in (weak, strong):
            self.assertEqual(fc['calibration']['scope'], 'none')
            self.assertIn('low end', fc['notes'][0])
            for k in ('week_one_audience', 'low', 'high'):
                self.assertNotEqual(fc['episode'][k] % 10, 0, 'people counts are never round')
            self.assertLess(fc['episode']['low'], fc['episode']['week_one_audience'])
            self.assertLess(fc['episode']['week_one_audience'], fc['episode']['high'])
            self.assertTrue(0 < fc['series']['second_screen_pct'] < 100)
            self.assertTrue(0 < fc['series']['season_completion_pct'] < 100)
            self.assertEqual(fc['subscriptions']['platform'], 'Paramount+')

    def test_second_screen_follows_the_room_and_the_habit(self):
        tw = _twin(second=61.7)
        few = F.second_screen_rate(_sess('a', 'X', 30, 10, 80, checked=1), tw)
        many = F.second_screen_rate(_sess('b', 'X', 30, 10, 80, checked=9), tw)
        self.assertLess(few, many)
        self.assertLess(F.second_screen_rate(_sess('c', 'X', 30, 10, 80, checked=5), _twin(second=20)), F.second_screen_rate(_sess('c', 'X', 30, 10, 80, checked=5), _twin(second=90)))

    def test_the_ledger_calibrates_show_then_platform_then_all(self):
        s3 = _S3()
        tw = _twin()
        pilot = _sess('p1', 'NCIS: New York Pilot', 19.3, 1.6, 81.9)
        ep2 = _sess('e2', 'NCIS: New York Episode 2', 38.1, 30.0, 91.8, mode='episode', genre='')
        tw['pilot_sessions'] = [pilot, ep2]
        before = F.forecast_for_session(tw, pilot, ledger=F._empty_ledger())
        led = F.record_actual(s3, tw, pilot, people=10_250_000, measure='week-one audience, every screen', aired='2026-10-06', episode=1)
        led = F.record_actual(s3, tw, ep2, people=8_430_000, measure='week-one audience, every screen', aired='2026-10-13', episode=2, ledger=led)
        self.assertIn(F.LEDGER_KEY, s3.store)
        cal = F.factor_for(led, tw, pilot)
        self.assertEqual(cal['scope'], 'show')
        self.assertEqual(cal['n'], 2)
        self.assertGreater(cal['factor'], 1.0)
        after = F.forecast_for_session(tw, pilot, ledger=led, actual={'people': 10_250_000, 'measure': 'week-one audience, every screen', 'aired': '2026-10-06'})
        self.assertGreater(after['episode']['week_one_audience'], before['episode']['week_one_audience'])
        self.assertEqual(after['actual']['week_one_audience'], 10_250_000)
        self.assertIsNotNone(after['actual']['forecast_vs_actual_pct'])
        self.assertIn("Calibrated to NCIS: New York's first 2 episodes", after['notes'][0])
        # the forecast lands within the anchors' own spread
        self.assertLess(abs(after['actual']['forecast_vs_actual_pct']), 25.0)
        # another show on the same platform borrows the platform's factor; another platform borrows everything
        other = _sess('o1', 'Tulsa King Pilot', 40.0, 30.0, 88.0)
        self.assertEqual(F.factor_for(led, tw, other)['scope'], 'platform')
        self.assertEqual(F.factor_for(led, _twin(name='Peacock'), other)['scope'], 'all')
        # the same test recorded twice replaces its row, never doubles it
        led = F.record_actual(s3, tw, pilot, people=10_250_000, measure='week-one audience, every screen', aired='2026-10-06', episode=1, ledger=led)
        self.assertEqual(len(led['records']), 2)
        # the episode without a genre of its own reads the show's genre from its sibling for the completion base
        self.assertEqual(F._genre_key(ep2, tw), 'action')
        # the ledger reloads from S3
        self.assertEqual(len(F.load_ledger(s3)['records']), 2)

    def test_attach_keeps_a_recorded_actual_and_the_board_note(self):
        tw = _twin()
        sess = _sess('p1', 'NCIS: New York Pilot', 19.3, 1.6, 81.9)
        sess['forecast'] = {'actual': {'week_one_audience': 10_250_000, 'measure': 'week-one audience, every screen', 'aired': '2026-10-06'}, 'board_notes': ['On our board it climbed.']}
        fc = F.attach_forecast(tw, sess, ledger=F._empty_ledger())
        self.assertEqual(fc['actual']['week_one_audience'], 10_250_000)
        self.assertEqual(fc['board_notes'], ['On our board it climbed.'])
        self.assertIs(sess['forecast'], fc)
        self.assertIsNone(F.attach_forecast(tw, {'id': 'x', 'scorecard': None}))

    def test_nothing_a_reader_sees_names_a_source_or_a_method(self):
        tw = _twin()
        fc = F.forecast_for_session(tw, _sess('p1', 'NCIS: New York Pilot', 19.3, 1.6, 81.9), ledger=F._empty_ledger())
        text = json.dumps({k: v for k, v in fc.items() if k in ('episode', 'series', 'subscriptions', 'notes')}).lower()
        for bad in ('nielsen', 'synth', 'model', 'estimat', 'panel', 'research'):
            self.assertNotIn(bad, text)


if __name__ == '__main__':
    unittest.main()
