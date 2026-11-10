#!/usr/bin/env python3
"""hold_to_norms passes the title's viewers into meta.norms (2026-11-10).

The every-viewer table (gold section 10) totals from meta.norms.title_viewers
or the title anchor. The hold knew the researched title_viewers_in_window
(it set the 45% title cap from it) but never wrote it to the trail, so a
file with researched viewers and no title anchor summed that table off the
end step instead: Jenna's My Life with the Walter Boys run read 2,618,392
against the file's own 9,178,463 in-window viewers, and was restated in
place. Fixed at source: the trail carries title_viewers, the table totals to
it, and the read agrees.

Hermetic: a four-step social spine, a band with researched title viewers,
no title anchor, no model (the gold pass falls back to its standard rows).

    REGRESSION_TEST_MODE=1 python3 scripts/test_journey_norms_title_viewers.py
"""
from __future__ import annotations

import copy
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('JOURNEY_CORPUS_ANCHORS', '0')

from migration import journey_norms as JN  # noqa: E402
from migration import journey_gold as G  # noqa: E402

TITLE, PLATFORM = 'My Life with the Walter Boys', 'Netflix'
VIEWERS = 9_178_463
INPUTS = {'subject': TITLE, 'platform': PLATFORM, 'journey_kind': 'watch', 'watch_definition': 'clicked_through',
          'conversion_event': f'watched {TITLE} on {PLATFORM}', 'start_behavior': f'saw a social post about {TITLE}',
          'start_date': '2025-11-10', 'end_date': '2026-11-10', 'notes': 'returning series with a new season in the window'}


def _payload():
    spine = [
        {'id': 'tam', 'label': 'US population', 'accounts': 329_900_000, 'kept': 100.0, 'dropped': 0},
        {'id': 'social_view', 'label': f'Saw a social post about {TITLE}', 'accounts': 46_845_806},
        {'id': 'click_through', 'label': 'Clicked through from the post to the title', 'accounts': 1_754_383},
        {'id': 'title_page', 'label': f'Opened the {TITLE} page on {PLATFORM}', 'accounts': 1_167_279},
        {'id': 'watched_30d', 'label': 'Watched an episode within 30 days of the post', 'accounts': 185_345},
    ]
    fork = [{'id': 'abandoned', 'label': 'Left without converting', 'accounts': 1_040_000},
            {'id': 'retargeted', 'label': 'Saw a retarget', 'accounts': 420_000},
            {'id': 'returned', 'label': 'Came back', 'accounts': 160_000},
            {'id': 'paid_return', 'label': 'Converted after coming back', 'accounts': 58_066},
            {'id': 'paid_first', 'label': 'Converted in the same session', 'accounts': 127_279}]
    return {'meta': {'project_name': f'{TITLE} on {PLATFORM}', 'target_name': f'{TITLE} on {PLATFORM}', 'journey_kind': 'watch',
                     'target_type': 'watch_journey', 'start_date': '2025-11-10', 'end_date': '2026-11-10', 'product': TITLE,
                     'platform_label': PLATFORM},
            'fragrance_shop_journey': {'spine': spine, 'fork': fork, 'detours': [], 'meta': {'window': '2025-11-10 to 2026-11-10'}},
            'facts': [], 'kpis': {'total_users': 185_345}}


class TitleViewersRideTheTrail(unittest.TestCase):
    def setUp(self):
        prim = {'title_viewers_in_window': VIEWERS, 'category': 'Streaming teen drama series', 'title_status': 'returning'}
        self.band = JN.band_for(INPUTS, prim, seed=f'{TITLE}|{PLATFORM}')
        self.assertEqual(self.band['title_viewers'], VIEWERS)

    def test_the_hold_records_the_viewers_and_the_cap(self):
        p = JN.hold_to_norms(_payload(), self.band, INPUTS, seed=f'{TITLE}|{PLATFORM}')
        norms = p['meta']['norms']
        self.assertTrue(norms.get('applied'), norms)
        self.assertEqual(norms.get('title_viewers'), VIEWERS)
        self.assertEqual(norms.get('title_cap'), int(VIEWERS * JN.TITLE_CAP_SHARE))
        self.assertNotIn('title_anchor', p['meta'])
        self.assertEqual(JN.title_viewers_from_payload(p), VIEWERS)

    def test_the_every_viewer_table_totals_the_viewers(self):
        p = JN.hold_to_norms(_payload(), self.band, INPUTS, seed=f'{TITLE}|{PLATFORM}')
        end = int(p['fragrance_shop_journey']['spine'][-1]['accounts'])
        p = G.apply(p, INPUTS, claude_json=None, seed=f'{TITLE}|{PLATFORM}')
        ev = G.section(p, 'every_viewer')
        self.assertIsNotNone(ev)
        rows = ev['rows']
        self.assertEqual(sum(int(r['accounts']) for r in rows), VIEWERS)
        self.assertEqual(int(ev['base']), VIEWERS)
        self.assertEqual(int(rows[0]['accounts']), end)
        self.assertGreater(int(rows[2]['accounts']), 0)
        s = G.ship_summary(p)
        self.assertEqual(s['viewers'], VIEWERS)
        rest = next(f for f in p['facts'] if f.get('label') == 'The rest')
        self.assertIn(f"{int(rows[2]['accounts']):,}", rest['value'])

    def test_without_researched_viewers_the_table_derives_as_before(self):
        prim = {'category': 'Streaming teen drama series'}
        band = JN.band_for(INPUTS, prim, seed=f'{TITLE}|{PLATFORM}')
        self.assertIsNone(band['title_viewers'])
        p = JN.hold_to_norms(_payload(), band, INPUTS, seed=f'{TITLE}|{PLATFORM}')
        self.assertNotIn('title_viewers', p['meta']['norms'])
        self.assertNotIn('title_cap', p['meta']['norms'])
        p = G.apply(p, INPUTS, claude_json=None, seed=f'{TITLE}|{PLATFORM}')
        ev = G.section(p, 'every_viewer')
        end = int(p['fragrance_shop_journey']['spine'][-1]['accounts'])
        self.assertEqual(int(ev['rows'][0]['accounts']), end)
        self.assertGreater(sum(int(r['accounts']) for r in ev['rows']), end)


if __name__ == '__main__':
    unittest.main(verbosity=2)
