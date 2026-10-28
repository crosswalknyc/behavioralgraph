#!/usr/bin/env python3
"""Pilot Testing: dial, events, scorecard, questions on the clock (2026-10-28).
Hermetic. No ffmpeg required for the math tests.
"""
from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('CORPUS_CATALOG', '0')

from migration import digital_twin_pilot as PI  # noqa: E402
from migration import digital_twins as DT  # noqa: E402


def _beals(name='Jennifer Beals', key='jb'):
    return {
        'meta': {'name': name, 'key': key, 'subject': name,
                 'profile_key': 'Jennifer_Beals_06_17_2026_17_09.csv'},
        'identity': {'demographics': {
            'gender': [{'label': 'Female', 'pct': 72.3516}, {'label': 'Male', 'pct': 24.0352},
                       {'label': 'Non-Binary', 'pct': 2.6698}],
            'sexual_orientation': [{'label': 'Straight / Heterosexual', 'pct': 62.3226},
                                   {'label': 'LGBTQ+', 'pct': 33.3786}],
            'education': [{'label': 'Bachelors Degree', 'pct': 33.4009},
                          {'label': 'Some College', 'pct': 22.1124},
                          {'label': 'High School or Less', 'pct': 18.8934},
                          {'label': 'Graduate or Professional Degree', 'pct': 25.5933}],
            'occupation': [{'label': 'Management/Business/Professional', 'pct': 36.0779},
                           {'label': 'Healthcare', 'pct': 13.0412}, {'label': 'Service', 'pct': 11.5613},
                           {'label': 'Education/Library', 'pct': 10.0934}, {'label': 'Sales', 'pct': 8.1123}],
            'age': [{'label': '55-64', 'pct': 25.0213}, {'label': '45-54', 'pct': 22.1124},
                    {'label': '35-44', 'pct': 18.4012}, {'label': '65+', 'pct': 16.3012},
                    {'label': '25-34', 'pct': 12.1012}, {'label': '18-24', 'pct': 6.0627}],
        }},
        'traits': {
            'franchise_loyalty': {'value': 71.4, 'label': 'loyalty', 'read': ''},
            'second_screen': {'value': 38.2, 'label': 'second', 'read': ''},
            'binge_vs_appointment': {'value': 62.1, 'label': 'binge', 'read': ''},
            'word_of_mouth': {'value': 64.8, 'label': 'wom', 'read': ''},
            'trial_speed': {'value': 44.2, 'label': 'trial', 'read': ''},
        },
        'lifecycle': {'habit_pct': 48.2, 'discovering_pct': 12.1, 'trying_pct': 10.4,
                      'lapsing_pct': 16.3, 'returning_pct': 13.0},
    }


def _young(name='Young Men Cut', key='ym'):
    t = _beals(name, key)
    t['identity']['demographics']['gender'] = [
        {'label': 'Male', 'pct': 78.2012}, {'label': 'Female', 'pct': 21.7988}]
    t['identity']['demographics']['age'] = [
        {'label': '18-24', 'pct': 41.2012}, {'label': '25-34', 'pct': 38.1124},
        {'label': '35-44', 'pct': 20.6864}]
    t['traits']['franchise_loyalty'] = {'value': 31.7, 'label': 'loyalty', 'read': ''}
    t['traits']['second_screen'] = {'value': 78.4, 'label': 'second', 'read': ''}
    t['lifecycle'] = {'habit_pct': 14.2, 'discovering_pct': 38.1, 'trying_pct': 22.4,
                      'lapsing_pct': 18.3, 'returning_pct': 7.0}
    return t


class _Body:
    def __init__(self, b):
        self._b = b

    def read(self):
        return self._b


class FakeS3:
    def __init__(self):
        self.objs = {}

    def get_object(self, Bucket, Key, **kw):
        if Key not in self.objs:
            raise KeyError(Key)
        return {'Body': _Body(self.objs[Key]), 'ContentType': 'application/json', 'ContentLength': len(self.objs[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objs[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode('utf-8')

    def delete_object(self, Bucket, Key, **kw):
        self.objs.pop(Key, None)


class Session(unittest.TestCase):
    def test_open_is_draft_and_free(self):
        sess = PI.open_session([_beals()], title='Season 2 pilot', in_room=12, created_by='jenna')
        self.assertEqual(sess['kind'], 'pilot')
        self.assertEqual(sess['status'], 'draft')
        self.assertFalse(sess['play_charged'])
        self.assertEqual(sess['questions_paid'], 0)
        self.assertEqual(PI.charge_due(sess), 'digital_twin_pilot')
        self.assertEqual(len(sess['group']['roster']), 12)

    def test_prepare_dial_matches_duration(self):
        twin = _beals()
        sess = PI.open_session([twin], title='NCIS New York pilot', in_room=8)
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=120, title='NCIS: New York pilot')
        self.assertEqual(sess['status'], 'ready')
        ts = sess['dial']['t']
        vals = sess['dial']['value']
        self.assertEqual(len(ts), len(vals))
        self.assertGreaterEqual(len(vals), 8)
        self.assertAlmostEqual(ts[-1], 120.0, delta=4.1)
        for v in vals:
            self.assertGreaterEqual(v, 4.0)
            self.assertLessEqual(v, 96.6)
            self.assertFalse(f"{v:.4f}".endswith('00'))

    def test_events_have_timestamps_and_types(self):
        twin = _beals()
        sess = PI.open_session([twin], title='The diner cut', in_room=12)
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=180, title='A comedy pilot')
        ev = sess['events']
        self.assertTrue(ev)
        types = {e['type'] for e in ev}
        self.assertTrue(types & set(PI.EVENT_TYPES))
        self.assertTrue(types & {'love', 'laugh', 'lean-in', 'wow'})
        for e in ev:
            self.assertIsNotNone(e.get('t'))
            self.assertGreaterEqual(float(e['t']), 0)
            self.assertLessEqual(float(e['t']), 180.1)
            self.assertTrue(e.get('line'))
            self.assertIn(e['type'], PI.EVENT_TYPES)

    def test_question_tagged_with_playhead(self):
        rec = {'q': 'Would you keep watching?', 'kind': 'yes_no'}
        PI.stamp_question(rec, 12 * 60 + 40)
        self.assertEqual(rec['playhead_s'], 760.0)
        self.assertEqual(rec['playhead_clock'], '12:40')

    def test_aggregate_in_range(self):
        twin = _beals()
        sess = PI.open_session([twin], title='Cut A', in_room=10)
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=90, title='Cut A')
        for v in sess['dial']['value']:
            self.assertGreater(v, 8)
            self.assertLessEqual(v, 96.6)
        sc = sess['scorecard']
        self.assertGreater(sc['overall_like'], 8)
        self.assertLessEqual(sc['overall_like'], 96.6)
        self.assertTrue(sc['favorite_moment']['clock'])
        self.assertTrue(sc['dead_spot']['clock'])

    def test_two_subjects_do_not_pin(self):
        a = _beals('Jennifer Beals', 'jb')
        b = _young()
        sa = PI.open_session([a], title='Same cut', in_room=8, session_id='aaa111')
        sb = PI.open_session([b], title='Same cut', in_room=8, session_id='bbb222')
        PI.prepare_watch(sa, {'jb': a}, duration_s=96, title='Same cut')
        PI.prepare_watch(sb, {'ym': b}, duration_s=96, title='Same cut')
        self.assertNotEqual(sa['dial']['value'], sb['dial']['value'])
        same = sum(1 for x, y in zip(sa['dial']['value'], sb['dial']['value']) if abs(x - y) < 0.00015)
        self.assertLess(same, 3)

    def test_charge_on_play_then_more(self):
        sess = PI.open_session([_beals()], title='T', in_room=12)
        self.assertEqual(PI.charge_due(sess), 'digital_twin_pilot')
        PI.mark_play_paid(sess)
        self.assertTrue(sess['play_charged'])
        self.assertIsNone(PI.charge_due(sess))
        sess['questions'] = [{'q': str(i)} for i in range(12)]
        self.assertEqual(PI.charge_due(sess), 'digital_twin_pilot_more')

    def test_delete_keeps_30_day_copy(self):
        s3 = FakeS3()
        twin = _beals()
        payload = {'meta': twin['meta'], 'pilot_sessions': []}
        sess = PI.open_session([twin], title='Delete me', in_room=6, session_id='del99')
        payload['pilot_sessions'].append(sess)
        key = 'digital-twins/jenna/Jennifer_Beals_x.json.gz'
        removed = PI.delete_session(s3, key, payload, 'del99', deleted_by='jenna')
        self.assertEqual(removed['id'], 'del99')
        self.assertEqual(payload['pilot_sessions'], [])
        undo = PI.deleted_pilot_key(key, 'del99')
        self.assertIn(undo, s3.objs)
        rec = json.loads(s3.objs[undo].decode('utf-8'))
        self.assertIn('undo_until', rec)
        self.assertEqual(rec['session']['id'], 'del99')
        self.assertEqual(PI.DELETED_PILOT_KEEP_DAYS, 30)

    def test_csv_has_dial_and_events(self):
        twin = _beals()
        sess = PI.open_session([twin], title='CSV cut', in_room=6)
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=60, title='CSV cut')
        text = PI.session_csv(sess)
        self.assertIn('Overall like', text)
        self.assertIn('Dial', text)
        self.assertIn('Clock', text)
        self.assertNotIn('\u2014', text)
        self.assertNotIn('\u2013', text)


class Copy(unittest.TestCase):
    def test_no_em_dash_in_engine_copy(self):
        twin = _beals()
        sess = PI.open_session([twin], title='Copy check', in_room=8)
        PI.prepare_watch(sess, {twin['meta']['key']: twin}, duration_s=80, title='A funny pilot')
        blob = json.dumps(sess, ensure_ascii=False)
        self.assertNotIn('\u2014', blob)
        self.assertNotIn('\u2013', blob)
        for tok in ('synth', 'synthetic', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap', 's3://'):
            self.assertNotIn(tok, blob.lower())
        for e in sess['events']:
            self.assertFalse(PI.has_banned_copy(e['line']))

    def test_how_to_item(self):
        doc = DT.how_to()
        ids = [it['id'] for it in doc['items']]
        self.assertIn('pilot', ids)
        it = next(x for x in doc['items'] if x['id'] == 'pilot')
        self.assertEqual(it['title'], 'Pilot Testing')
        blob = json.dumps(it, ensure_ascii=False) + doc['where']
        self.assertIn('Play', it['answers'])
        self.assertIn('dial', it['answers'])
        self.assertIn('$5,500', it['cost'])
        self.assertIn('Focus Group IQ', it['cost'])
        self.assertNotIn('\u2014', blob)
        for tok in ('synth', 'synthetic', 'modeled', 'claude', 'hostmap', 'pipeline'):
            self.assertNotIn(tok, blob.lower())
        self.assertIn('Pilot Testing tab', doc['where'])

    def test_pricing_line(self):
        lines = DT.pricing_lines()
        self.assertTrue(any(l.startswith('Pilot Testing - $5,500') for l in lines))
        self.assertEqual(DT.PRICING['digital_twin_pilot'], 5500.0)
        self.assertEqual(DT.PRICING['digital_twin_pilot_more'], 5500.0)
        self.assertEqual(DT.PULL_TYPES['digital_twin_pilot'], 'Digital Twins IQ Pilot Testing')

    def test_tab_id_on_the_page(self):
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            page = fh.read()
        self.assertIn('data-tab="pilot"', page)
        self.assertIn('id="dtwTabPilot"', page)
        self.assertIn('Pilot Testing', page)
        self.assertNotIn('Screen Engine', page)
        self.assertNotIn('Virtuworks', page)
        pane = page[page.find('id="dtwTabPilot"'):page.find('id="dtwTabPilot"') + 280]
        js = ''
        mark = page.find('function renderPilotConsole')
        if mark >= 0:
            js = page[mark:mark + 6000]
        start = page.find('Watch a cut with the room')
        if start >= 0:
            js += page[start:start + 4000]
        for blob in (pane, js):
            low = blob.lower()
            for tok in ('synth', 'synthetic', 'modeled', 'claude', 'hostmap'):
                self.assertNotIn(tok, low)

    def test_api_routes_named(self):
        with open(os.path.join(ROOT, 'digital_twins_api.py'), encoding='utf-8') as fh:
            api = fh.read()
        self.assertIn('/pilot/open', api)
        self.assertIn('/pilot/<sid>/play', api)
        self.assertIn('/pilot/<sid>/ask', api)
        self.assertIn('/pilot/<sid>/export.csv', api)
        self.assertIn('digital_twin_pilot', api)
        self.assertNotIn('\u2014', api)


class Beats(unittest.TestCase):
    def test_genre_shifts_tone(self):
        funny = PI.build_beats('A comedy pilot', 60, 'hash-a', notes='sitcom')
        tense = PI.build_beats('A thriller cut', 60, 'hash-b', notes='horror')
        self.assertTrue(any(b['tone'] == 'funny' for b in funny))
        self.assertTrue(any(b['tone'] in ('tense', 'quiet', 'wow') for b in tense))

    def test_transcript_pins_dialogue(self):
        beats = PI.build_beats('Title', 40, 'h', transcript=[{'t': 8, 'text': 'I never left Chicago.'}])
        hit = [b for b in beats if 'Chicago' in (b.get('dialogue') or '')]
        self.assertTrue(hit)


if __name__ == '__main__':
    unittest.main(verbosity=1)
