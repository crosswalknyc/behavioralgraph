#!/usr/bin/env python3
"""The mix block's title line is whole (2026-11-05). Hermetic.

The Jeopardy Volume 2 read shipped 'Discovery path to the YouTube edition
of Jeopardy Volume 2 ( mix, Nov 5 2025 to Nov 5 2026': the dimension
'Discovery path to the YouTube edition of Jeopardy Volume 2 (first
touch)' was cut at 60 characters, which left a bare '(' on the label,
the chat header, the ledger entry and the CSV header cell.

Covered here:
  1. clean_dimension cuts on a word boundary and never leaves an
     unbalanced parenthesis
  2. breakdown_header prints the qualifier parenthetical only when the
     label is non-empty, always closed, in the filled and empty cases
  3. format_generated_metrics_reply renders both cases whole
  4. the coherence pass keeps a long dimension with its qualifier
"""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

import prometheus_analysis as pma  # noqa: E402

WIN = 'Nov 5 2025 to Nov 5 2026'
TITLE = 'Discovery path to the YouTube edition of Jeopardy Volume 2'
ROWS = [{'label': 'Suggested video off another Jeopardy or game show clip', 'share_pct': 27.4},
        {'label': 'Third party creator video or Short', 'share_pct': 18.3},
        {'label': 'Subscription feed, already following the channel', 'share_pct': 14.9},
        {'label': 'YouTube search query', 'share_pct': 12.6},
        {'label': 'Shorts feed', 'share_pct': 9.7},
        {'label': 'Off platform social post', 'share_pct': 7.3},
        {'label': 'Press, newsletter or article link', 'share_pct': 5.1},
        {'label': 'Direct link shared person to person', 'share_pct': 4.7}]


def _res(dimension, basis=''):
    return {'subject': 'Jeopardy (YouTube edition, Volume 2)', 'window_label': WIN,
            'metrics': [{'label': 'US viewers of the YouTube edition, Volume 2', 'value': 1884317, 'unit': 'viewers'}],
            'breakdown': {'dimension': dimension, 'share_basis': basis, 'rows': ROWS}}


class CleanDimension(unittest.TestCase):
    def test_long_label_with_qualifier_survives_whole(self):
        self.assertEqual(pma.clean_dimension(f'{TITLE} (first touch)'), f'{TITLE} (first touch)')

    def test_never_ends_in_an_open_parenthesis(self):
        self.assertEqual(pma.clean_dimension(f'{TITLE} ('), TITLE)
        self.assertEqual(pma.clean_dimension(f'{TITLE} (first'), f'{TITLE} (first)')
        long = f'{TITLE} (first touch) for every viewer counted in the window and after it'
        out = pma.clean_dimension(long)
        self.assertLessEqual(len(out), pma.DIMENSION_LABEL_MAX)
        self.assertEqual(out.count('('), out.count(')'))
        self.assertFalse(out.endswith('('))
        self.assertTrue(long.startswith(out.rsplit(' ', 1)[0]))

    def test_defaults_and_short_labels(self):
        self.assertEqual(pma.clean_dimension(''), 'Category')
        self.assertEqual(pma.clean_dimension(None), 'Category')
        self.assertEqual(pma.clean_dimension('  Streaming   platform '), 'Streaming platform')
        self.assertEqual(pma.clean_dimension('Age (years)'), 'Age (years)')


class BreakdownHeader(unittest.TestCase):
    def test_empty_label_prints_no_parenthetical(self):
        head = pma.breakdown_header({'dimension': f'{TITLE} (', 'share_basis': ''}, WIN)
        self.assertEqual(head, f'{TITLE} mix, {WIN}')

    def test_filled_label_folds_into_a_closed_parenthetical(self):
        head = pma.breakdown_header({'dimension': f'{TITLE} (first touch)', 'share_basis': ''}, WIN)
        self.assertEqual(head, f'{TITLE} (first touch mix, {WIN})')

    def test_share_basis_follows_only_when_present(self):
        head = pma.breakdown_header({'dimension': 'Streaming platform', 'share_basis': 'share of the cohort'}, WIN)
        self.assertEqual(head, f'Streaming platform mix, {WIN} (share of the cohort)')
        head = pma.breakdown_header({'dimension': 'Month', 'share_basis': ''}, WIN)
        self.assertEqual(head, f'Month mix, {WIN}')
        head = pma.breakdown_header({'dimension': '', 'share_basis': ''}, WIN)
        self.assertEqual(head, f'Category mix, {WIN}')

    def test_every_parenthesis_closes(self):
        for dim in (f'{TITLE} (', f'{TITLE} (first touch)', f'{TITLE} (first touch', 'Group', '', '(first touch)'):
            for basis in ('', 'Share of the 1,884,317 US Volume 2 viewers'):
                head = pma.breakdown_header({'dimension': dim, 'share_basis': basis}, WIN)
                self.assertEqual(head.count('('), head.count(')'), head)
                self.assertNotIn('( mix', head)
                self.assertNotIn('(  ', head)
                self.assertIn(f'mix, {WIN}', head)


class RenderedReply(unittest.TestCase):
    def test_empty_label_case(self):
        reply = pma.format_generated_metrics_reply(_res(f'{TITLE} ('))
        self.assertIn(f'{TITLE} mix, {WIN}\n- Suggested video', reply)
        self.assertNotIn('( mix', reply)

    def test_filled_case(self):
        reply = pma.format_generated_metrics_reply(_res(f'{TITLE} (first touch)'))
        self.assertIn(f'{TITLE} (first touch mix, {WIN})\n- Suggested video', reply)

    def test_coherence_pass_keeps_the_qualifier(self):
        res = pma.enforce_metrics_coherence({'subject': 'Jeopardy (YouTube edition, Volume 2)',
                                             'metrics': _res('')['metrics'],
                                             'breakdown': {'dimension': f'{TITLE} (first touch)',
                                                           'share_basis': 'Share of the 1,884,317 US Volume 2 viewers',
                                                           'rows': ROWS}})
        bd = res.get('breakdown') or {}
        self.assertEqual(bd.get('dimension'), f'{TITLE} (first touch)')
        reply = pma.format_generated_metrics_reply(dict(res, window_label=WIN))
        self.assertIn(f'{TITLE} (first touch mix, {WIN}) (Share of the 1,884,317 US Volume 2 viewers)', reply)


if __name__ == '__main__':
    unittest.main(verbosity=2)
