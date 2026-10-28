#!/usr/bin/env python3
"""Room names: asked on open, editable on the page (2026-10-27, Jenna)."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('REGRESSION_TEST_MODE', '1')

from migration import digital_twin_panel as P  # noqa: E402
from migration import digital_twins as DT  # noqa: E402


def _twin(name='Gilmore Girls', key='gg'):
    return {'meta': {'name': name, 'key': key},
            'identity': {'demographics': {'gender': [{'label': 'Female', 'pct': 62.1}]}}}


class RoomTitle(unittest.TestCase):
    def test_named_open_keeps_the_name(self):
        sess = P.open_session([_twin()], title='  Season 2 pricing  ', session_id='abc123def0')
        self.assertEqual(sess['title'], 'Season 2 pricing')

    def test_blank_open_falls_back(self):
        sess = P.open_session([_twin()], title='   ', session_id='abc123def1')
        self.assertEqual(sess['title'], 'Room: Gilmore Girls')

    def test_rename_sets_the_name(self):
        sess = P.open_session([_twin()], title='Season 2', session_id='abc123def2')
        self.assertEqual(P.rename_session(sess, '  Holiday drop  '), 'Holiday drop')
        self.assertEqual(sess['title'], 'Holiday drop')

    def test_rename_rejects_blank(self):
        sess = P.open_session([_twin()], title='Season 2', session_id='abc123def3')
        self.assertEqual(P.rename_session(sess, '   '), '')
        self.assertEqual(sess['title'], 'Season 2')

    def test_named_room_does_not_reuse_a_differently_named_empty(self):
        payload = {'panel_sessions': []}
        first = P.open_session([_twin()], title='Season 2', in_room=12, session_id='abc123def4')
        first['twin_keys'] = ['gg']
        first['group']['twins'] = ['gg']
        payload['panel_sessions'].append(first)
        hit = P.find_reusable_session(payload, [_twin()], in_room=12, title='Holiday drop')
        self.assertIsNone(hit)
        same = P.find_reusable_session(payload, [_twin()], in_room=12, title='Season 2')
        self.assertIs(same, first)

    def test_unnamed_open_still_reuses_empty(self):
        payload = {'panel_sessions': []}
        first = P.open_session([_twin()], title='Season 2', in_room=12, session_id='abc123def5')
        first['twin_keys'] = ['gg']
        first['group']['twins'] = ['gg']
        payload['panel_sessions'].append(first)
        hit = P.find_reusable_session(payload, [_twin()], in_room=12, title='')
        self.assertIs(hit, first)

    def test_how_to_and_page_ask_for_the_name(self):
        doc = DT.how_to()
        panel = next(it for it in doc['items'] if it['id'] == 'panel')
        self.assertIn('name for the room', panel['put_in'].lower())
        with open(os.path.join(ROOT, 'templates', 'index.html'), encoding='utf-8') as fh:
            page = fh.read()
        self.assertIn('What do you want to name this room?', page)
        self.assertIn('id="dtwFgTitle"', page)
        self.assertIn('window.dtwPanelRename', page)
        self.assertIn('Name the room first.', page)
        self.assertNotIn('Room title (optional)', page)


if __name__ == '__main__':
    unittest.main(verbosity=1)
