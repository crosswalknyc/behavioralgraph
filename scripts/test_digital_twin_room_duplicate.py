#!/usr/bin/env python3
"""Duplicate a Focus Group IQ room (Jenna, 2026-11-04: "have a duplicate
button where you could duplicate a room with all of the input if you want
to so if you just want to change one thing you can"). Hermetic.
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

from migration import digital_twin_panel as P  # noqa: E402


def _beals(name='Jennifer Beals', key='jb'):
    return {
        'meta': {'name': name, 'key': key, 'subject': name, 'size_us': 9339205},
        'identity': {'demographics': {
            'gender': [{'label': 'Female', 'pct': 72.3516}, {'label': 'Male', 'pct': 24.0352}, {'label': 'Non-Binary', 'pct': 2.6698}],
            'sexual_orientation': [{'label': 'Straight / Heterosexual', 'pct': 62.3226}, {'label': 'LGBTQ+', 'pct': 33.3786}],
            'education': [{'label': 'Bachelors Degree', 'pct': 33.4009}, {'label': 'Some College', 'pct': 22.1124},
                          {'label': 'High School or Less', 'pct': 18.8934}, {'label': 'Graduate or Professional Degree', 'pct': 25.5933}],
            'age': [{'label': '55-64', 'pct': 25.0213}, {'label': '45-54', 'pct': 22.1124}, {'label': '35-44', 'pct': 18.4012},
                    {'label': '65+', 'pct': 16.3012}, {'label': '25-34', 'pct': 12.1012}, {'label': '18-24', 'pct': 6.0627}],
        }},
        'traits': {'franchise_loyalty': {'value': 71.4}, 'second_screen': {'value': 38.2}, 'binge_vs_appointment': {'value': 62.1},
                   'word_of_mouth': {'value': 64.8}, 'trial_speed': {'value': 44.2}},
        'lifecycle': {'habit_pct': 48.2, 'discovering_pct': 12.1, 'trying_pct': 10.4, 'lapsing_pct': 16.3, 'returning_pct': 13.0},
    }


def _young(name='Young Men Cut', key='ym'):
    t = _beals(name, key)
    t['identity']['demographics']['gender'] = [{'label': 'Male', 'pct': 78.2012}, {'label': 'Female', 'pct': 21.7988}]
    t['identity']['demographics']['age'] = [{'label': '18-24', 'pct': 41.2012}, {'label': '25-34', 'pct': 38.1124}, {'label': '35-44', 'pct': 20.6864}]
    return t


def _room(twins, sid='r1', title='Season 2 pricing', segments=None, in_room=10, asked=True):
    sess = P.open_session(twins, segments=segments, in_room=in_room, title=title, created_by='jenna', session_id=sid)
    sess['twin_keys'] = [t['meta']['key'] for t in twins]
    if asked:
        sess['questions'] = [{'id': 'q1', 'q': 'Would you pay $12.99?', 'kind': 'price', 'read': 'Half would.'}]
        P.mark_paid(sess, 12)
    return sess


class Duplicate(unittest.TestCase):
    def test_same_group_plan_no_questions_nothing_paid(self):
        twin = _beals()
        src = _room([twin], segments=[{'label': 'women 25 to 44'}, {'label': 'men'}], in_room=10)
        payload = {'meta': twin['meta'], 'panel_sessions': [src]}
        before = json.dumps(src, sort_keys=True, default=str)
        dup = P.duplicate_room(payload, [twin], 'r1', created_by='jenna', session_id='r2')
        self.assertIsNotNone(dup)
        self.assertEqual(dup['id'], 'r2')
        self.assertEqual(dup['title'], 'Season 2 pricing copy')
        self.assertEqual(dup['questions'], [])
        self.assertEqual(dup['questions_paid'], 0)
        self.assertNotIn('panelists_paid', dup)
        self.assertEqual(P.charge_due(dup), 'digital_twin_panel')      # priced like any new room: the session on the first question
        self.assertEqual(dup['group']['in_room'], src['group']['in_room'])
        self.assertEqual(dup['group']['twins'], src['group']['twins'])
        self.assertEqual([(s['label'], s['seats'], s['filter']) for s in dup['group']['segments']],
                         [(s['label'], s['seats'], s['filter']) for s in src['group']['segments']])
        self.assertEqual(len(dup['group']['roster']), len(src['group']['roster']))
        self.assertEqual([r['segment'] for r in dup['group']['roster']], [r['segment'] for r in src['group']['roster']])
        self.assertNotEqual([r['name'] for r in dup['group']['roster']], [r['name'] for r in src['group']['roster']])   # seated fresh
        self.assertEqual(dup['twin_keys'], ['jb'])
        self.assertEqual(dup['created_by'], 'jenna')
        self.assertEqual(dup['duplicated_from'], 'r1')
        self.assertIs(payload['panel_sessions'][0], dup)
        self.assertEqual(json.dumps(src, sort_keys=True, default=str), before)
        self.assertEqual(src['questions_paid'], 12)

    def test_two_twins_and_a_second_copy(self):
        twin, other = _beals(), _young()
        src = _room([twin, other], in_room=12, title='Two audiences')
        payload = {'meta': twin['meta'], 'panel_sessions': [src]}
        a = P.duplicate_room(payload, [twin, other], 'r1', created_by='jessie', session_id='r2')
        b = P.duplicate_room(payload, [twin, other], 'r1', created_by='jessie', session_id='r3')
        self.assertEqual(a['title'], 'Two audiences copy')
        self.assertEqual(b['title'], 'Two audiences copy 2')
        self.assertEqual(a['twin_keys'], ['jb', 'ym'])
        self.assertEqual(a['group']['twins'], ['jb', 'ym'])
        self.assertEqual({r['twin'] for r in a['group']['roster']}, {'jb', 'ym'})
        self.assertEqual([s['id'] for s in payload['panel_sessions']], ['r3', 'r2', 'r1'])
        c = P.duplicate_room(payload, [twin, other], 'r2', created_by='jessie', session_id='r4')
        self.assertEqual(c['title'], 'Two audiences copy 3')      # a copy of the copy counts up, never 'copy copy'

    def test_empty_room_and_missing_room(self):
        twin = _beals()
        src = _room([twin], asked=False, in_room=6)
        payload = {'meta': twin['meta'], 'panel_sessions': [src]}
        dup = P.duplicate_room(payload, [twin], 'r1', created_by='jenna', session_id='r2')
        self.assertEqual(dup['group']['in_room'], 6)
        self.assertEqual(dup['questions_paid'], 0)
        self.assertIsNone(P.duplicate_room(payload, [twin], 'nope', created_by='jenna'))
        self.assertEqual(len(payload['panel_sessions']), 2)

    def test_copy_name(self):
        self.assertEqual(P.copy_name('Season 2 pricing', []), 'Season 2 pricing copy')
        self.assertEqual(P.copy_name('Season 2 pricing copy', ['Season 2 pricing copy']), 'Season 2 pricing copy 2')
        self.assertEqual(P.copy_name('  Season   2 ', ['season 2 copy']), 'Season 2 copy 2')
        self.assertEqual(P.copy_name('', []), 'Untitled copy')

    def test_route_and_page_wiring(self):
        with open(os.path.join(ROOT, 'digital_twins_api.py'), encoding='utf-8') as fh:
            api = fh.read()
        self.assertIn('/panel/<sid>/duplicate', api)
        i = api.find('def dt_panel_duplicate')
        body = api[i:i + 2600]
        self.assertIn('duplicate_room', body)
        self.assertIn('ensure_personas', body)
        self.assertIn('save_twin', body)
        self.assertNotIn('\u2014', body)
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            page = fh.read()
        self.assertIn('window.dtwPanelDuplicate', page)
        self.assertIn("'/duplicate'", page)
        self.assertIn('title="Duplicate this room"', page)
        self.assertIn('class="dtw-roomdup"', page)
        blob = page[page.find('window.dtwPanelDuplicate'):page.find('window.dtwPanelResume')]
        self.assertIn('Same twins, cohorts and seats', blob)
        self.assertNotIn('\u2014', blob)
        for tok in ('synth', 'modeled', 'estimated', 'pipeline', 'claude', 'hostmap', 's3'):
            self.assertNotIn(tok, blob.lower(), tok)


if __name__ == '__main__':
    unittest.main(verbosity=1)
