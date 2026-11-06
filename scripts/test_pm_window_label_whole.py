#!/usr/bin/env python3
"""The window line prints whole, with its year and its closing
parenthesis (2026-11-05). Hermetic: no model, no S3.

A Subscriber IQ overlap read (P-Valley Viewers and S.W.A.T. Exiles)
shipped its counting block with, twice:

    Window: Nov 5 2025 to Nov 5 2026 (S.W.A.T. Exiles Season 1 to date, Sep 25 to Oct 29 202.

The model's window_label ran 82 characters; enforce_metrics_coherence
clipped it to a raw [:80], methodology.block appended its full stop, so
the year lost a digit and the parenthesis never closed. The ledger entry
(wl) and the Reader's window phrase carried the same raw slice.

Covered here:
  1. the exact shipped label survives the coherence pass whole, the
     counting block prints it with '2026).', and so does the demand pass
  2. a label past the bound loses its trailing parenthetical whole,
     never a digit of a year; no clip ever ends mid-token
  3. the cut detector: a three-digit year, an open parenthesis, a
     trailing 'to' read as cut; whole labels of every house shape do not
  4. the counting block repairs a cut label (an older record) from the
     read's dates rather than printing the fragment
  5. the completion pass (complete_cells) never carries a cut window: the
     frame, the prompt and the completed reply print it whole
  6. the ledger entry stores the label whole; the Reader's window phrase
     is never cut inside a date
  7. the in-place repair of a shipped reply rebuilds the line from the
     full label when it is known
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
import prometheus_verify as V  # noqa: E402
import insights_ledger as IL  # noqa: E402
from prometheus import methodology as M  # noqa: E402
from prometheus import window_label as WL  # noqa: E402

FULL = 'Nov 5 2025 to Nov 5 2026 (S.W.A.T. Exiles Season 1 to date, Sep 25 to Oct 29 2026)'
CUT = 'Nov 5 2025 to Nov 5 2026 (S.W.A.T. Exiles Season 1 to date, Sep 25 to Oct 29 202'
SHIPPED_LINE = '- Window: ' + CUT + '.'
WANT_LINE = '- Window: ' + FULL + '.'

READ = {
    'subject': 'P-Valley Viewers and S.W.A.T. Exiles overlap',
    'metric_family': 'viewership',
    'window_label': FULL,
    'window_start': '2025-11-05',
    'window_end': '2026-11-05',
    'headline': 'P-Valley viewers who also watched S.W.A.T. Exiles.',
    'metrics': [
        {'name': 'pvalley_viewers', 'label': 'P-Valley viewers', 'unit': 'people', 'value': 4812337,
         'definition': 'US people with at least one P-Valley view in the window'},
        {'name': 'overlap_viewers', 'label': 'Also watched S.W.A.T. Exiles', 'unit': 'people', 'value': 611291,
         'definition': 'of those, people with a S.W.A.T. Exiles view since its premiere'},
        {'name': 'overlap_share', 'label': 'Share of P-Valley viewers', 'unit': 'pct', 'value': 12.7,
         'definition': 'overlap viewers as a share of P-Valley viewers'},
    ],
    'reads': ['The overlap leans toward the back half of the premiere window.'],
}


class ExactShippedShape(unittest.TestCase):
    def test_the_coherence_pass_keeps_the_82_character_label_whole(self):
        self.assertEqual(len(FULL), 82)
        res = pma.enforce_metrics_coherence(dict(READ))
        self.assertEqual(res['window_label'], FULL)
        self.assertTrue(res['window_label'].endswith('2026)'))

    def test_the_counting_block_prints_the_year_and_the_close(self):
        res = pma.enforce_metrics_coherence(dict(READ))
        lines = M.block(window_start=res['window_start'], window_end=res['window_end'],
                        window_label=res['window_label'], base_label='P-Valley viewers')
        self.assertIn(WANT_LINE, lines)
        self.assertNotIn(SHIPPED_LINE, lines)
        for ln in lines:
            self.assertFalse(WL.cut_mid_token(ln.split('Window:', 1)[-1]) if ln.startswith('- Window:') else False, ln)

    def test_the_reply_carries_the_whole_line_once_through_ensure(self):
        res = pma.enforce_metrics_coherence(dict(READ))
        reply = pma.format_generated_metrics_reply(res)
        self.assertIn(FULL, reply)
        out = M.ensure(reply, res, question='How many P-Valley viewers also watched S.W.A.T. Exiles?',
                       base_label='P-Valley viewers')
        self.assertIn(WANT_LINE, out)
        self.assertNotIn('202.', out)
        self.assertEqual(WL.window_line_fragments(out), [])

    def test_the_demand_pass_keeps_the_label_whole_too(self):
        res = pma.enforce_demand_coherence({'subject': 'S.W.A.T. Exiles', 'window_label': FULL,
                                            'window_start': '2025-11-05', 'window_end': '2026-11-05',
                                            'first_touch': [{'label': 'Search', 'count': 120311}]})
        self.assertEqual(res['window_label'], FULL)


class ClipNeverCutsMidToken(unittest.TestCase):
    def test_a_label_past_the_bound_loses_the_parenthetical_whole(self):
        long_tail = FULL[:-1] + ', the finale week Oct 23 to Oct 29 2026 and the two weeks after it)'
        out = WL.clip_window_label(long_tail, limit=100)
        self.assertEqual(out, 'Nov 5 2025 to Nov 5 2026')
        self.assertFalse(WL.cut_mid_token(out))

    def test_a_clip_at_the_old_bound_never_strands_a_year(self):
        out = WL.clip_window_label(FULL, limit=80)
        self.assertFalse(WL.cut_mid_token(out), out)
        self.assertNotRegex(out, r'\b20\d$')
        self.assertEqual(out.count('('), out.count(')'))

    def test_every_cut_point_reads_whole(self):
        for limit in range(10, 90):
            out = WL.clip_window_label(FULL, limit=limit)
            self.assertFalse(WL.cut_mid_token(out), (limit, out))
            self.assertLessEqual(len(out), limit)

    def test_a_label_inside_the_bound_is_untouched(self):
        for lab in ('Jul 1 2022 to Jun 30 2023', 'Sep 23 to Oct 22, 2026', 'trailing 12 months',
                    'Calendar 2025 (Jan 1, 2025 to Dec 31, 2025)', FULL,
                    'Jul 31 2026 to Oct 29 2026, handoff window Sep 25 to Oct 29 2026'):
            self.assertEqual(WL.clip_window_label(lab), lab)


class CutDetector(unittest.TestCase):
    def test_cut_fragments_read_cut(self):
        for frag in (CUT, 'Sep 25 to Oct 29 202', 'Nov 5 2025 to', 'Nov 5 2025 to Nov 5 2026 (S.W.A.T.',
                     'Jan 1 2026 to Nov 4 2', 'Sep 23 to Oct 22,'):
            self.assertTrue(WL.cut_mid_token(frag), frag)

    def test_whole_labels_do_not(self):
        for lab in (FULL, 'Nov 5 2025 to Nov 5 2026', 'Jul 1 2022 to Jun 30 2023', 'Sep 23 to Oct 22, 2026',
                    'trailing 12 months', 'the last 12 months', 'Q3 2026', 'Calendar 2025',
                    'Calendar 2025 (Jan 1, 2025 to Dec 31, 2025)', 'Sep 25 to Oct 29', 'Toronto',
                    'Nov 4 2025 to Nov 4 2026.', 'Jun 16 2022 to Jun 16 2023'):
            self.assertFalse(WL.cut_mid_token(lab), lab)


class OlderRecordsRepair(unittest.TestCase):
    def test_the_block_rebuilds_a_cut_label_from_the_dates(self):
        lines = M.block(window_start='2025-11-05', window_end='2026-11-05', window_label=CUT,
                        base_label='P-Valley viewers')
        win = [ln for ln in lines if ln.startswith('- Window:')]
        self.assertEqual(len(win), 1)
        self.assertEqual(win[0], '- Window: Nov 5 2025 to Nov 5 2026.')
        self.assertNotIn('202.', win[0])

    def test_a_cut_label_with_no_dates_drops_the_dangling_parenthetical(self):
        self.assertEqual(WL.repair_window_label(CUT), 'Nov 5 2025 to Nov 5 2026')
        self.assertEqual(WL.repair_window_label('Sep 25 to Oct 29 202'), 'Sep 25 to Oct 29')
        self.assertEqual(WL.repair_window_label('Nov 5 2025 to'), 'Nov 5 2025')

    def test_a_shipped_reply_is_rebuilt_from_the_full_label_when_known(self):
        reply = ('On P-Valley Viewers and S.W.A.T. Exiles overlap:\n\n611,291 people.\n\n'
                 'How these are counted:\n' + SHIPPED_LINE + '\n- Base: P-Valley viewers.\n\n'
                 'Full table:\n' + SHIPPED_LINE)
        out = WL.repair_window_lines(reply, {'window_label': FULL, 'window_start': '2025-11-05',
                                             'window_end': '2026-11-05'})
        self.assertEqual(out.count(WANT_LINE), 2)
        self.assertNotIn(SHIPPED_LINE, out)
        self.assertEqual(WL.window_line_fragments(out), [])
        # a reply whose lines are whole is returned as given
        self.assertEqual(WL.repair_window_lines(out, {'window_label': FULL}), out)

    def test_a_shipped_reply_with_only_the_dates_known_drops_the_fragment(self):
        out = WL.repair_window_lines(SHIPPED_LINE, {'window_label': CUT, 'window_start': '2025-11-05',
                                                    'window_end': '2026-11-05'})
        self.assertEqual(out, '- Window: Nov 5 2025 to Nov 5 2026.')


class CompletionPassGuard(unittest.TestCase):
    def test_the_frame_and_the_prompt_carry_the_window_whole(self):
        res2 = {'subject': 'P-Valley Viewers and S.W.A.T. Exiles overlap', 'window_label': CUT,
                'window_start': '2025-11-05', 'window_end': '2026-11-05', 'metrics': []}
        frame = V.partition_frame({}, res2, [], question='split the overlap by service')
        self.assertFalse(WL.cut_mid_token(frame['window']), frame['window'])
        self.assertEqual(frame['window'], 'Nov 5 2025 to Nov 5 2026')
        prompt = V.completion_prompt('split the overlap by service', res2, [], frame)
        self.assertIn('Window: Nov 5 2025 to Nov 5 2026', prompt)
        self.assertNotIn('202\n', prompt)

    def test_a_whole_frame_window_passes_untouched(self):
        res2 = {'subject': 'x', 'window_label': FULL, 'window_start': '2025-11-05', 'window_end': '2026-11-05'}
        frame = V.partition_frame({}, res2, [], question='q')
        self.assertEqual(frame['window'], FULL)

    def test_the_completed_reply_lines_are_whole(self):
        res3 = {'window_label': FULL, 'window_start': '2025-11-05', 'window_end': '2026-11-05'}
        self.assertEqual(V._whole_window_lines('x\n' + SHIPPED_LINE, res3), 'x\n' + WANT_LINE)
        self.assertEqual(V._whole_window_lines('x\n' + WANT_LINE, res3), 'x\n' + WANT_LINE)
        self.assertIsNone(V._whole_window_lines(None, res3))


class LedgerAndReader(unittest.TestCase):
    def test_the_ledger_entry_stores_the_label_whole(self):
        e = IL.make_entry(subject='P-Valley Viewers and S.W.A.T. Exiles overlap', metric_family='viewership',
                          question='How many P-Valley viewers also watched S.W.A.T. Exiles?',
                          route='reasoned_metrics', metrics=READ['metrics'], window_start='2025-11-05',
                          window_end='2026-11-05', window_label=FULL, reply='On P-Valley viewers: 611,291 people.')
        self.assertEqual(e['wl'], FULL)

    def test_the_reader_window_phrase_is_never_cut_inside_a_date(self):
        from prometheus import reader as R
        intent = sorted(R.INTENTS)[0] if isinstance(R.INTENTS, (set, frozenset, list, tuple)) else list(R.INTENTS)[0]
        d = {'intent': intent, 'confidence': 0.9, 'subjects': [{'as_written': 'P-Valley', 'kind': 'title'}],
             'window': FULL + ' as the reader put it, the premiere window through the finale week of Oct 29 2026'}
        out = R._normalize(d, 'how many P-Valley viewers also watched S.W.A.T. Exiles', [])
        self.assertIsNotNone(out)
        self.assertFalse(WL.cut_mid_token(out['window']), out['window'])
        self.assertIn(FULL, out['window'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
