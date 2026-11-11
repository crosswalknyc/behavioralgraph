#!/usr/bin/env python3
"""Natural lower digits on every count a read states (Cara Dorfman, 2026-11-10).

Her hours table (job 23a6339b30da) carried 5,886,000,022 and 64,638,000,214:
a round millions body with a two-digit tail reads as a placeholder. This
suite holds prometheus.count_digits and the places that route through it:

  * 5,886,000,022 is a placeholder; it is re-drawn with no run of three
    zeros inside the thousands and hundreds places, a last digit of 1 to
    9, a move of at most 0.4% that never passes the lower five digits,
    the same every time for the same salt;
  * a partition keeps summing: the components are drawn natural and the
    total is their exact sum, itself natural;
  * a read's structured result and its prose move together (the
    breakdown pools, the buckets, the count metrics, the headline);
  * arith_coherence.apply (the pass every reasoned read and the matrix
    read go through) carries the digits pass; messy_count, the matrix
    fill's _messy and prometheus_analysis._messy no longer leave a round
    body with a nudged tail;
  * a count at or under 100,000 keeps the plain last-digit rule; a
    natural count is left alone; shares are never rewritten.

Hermetic: no model, no S3, no app module. Run:
    python3 scripts/test_pm_count_digits.py
"""
from __future__ import annotations

import copy
import os
import re
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from prometheus import count_digits as CD  # noqa: E402
from prometheus import arith_coherence as AC  # noqa: E402
from prometheus import matrix_read as MR  # noqa: E402

CARA_POOL = 5_886_000_022
CARA_TOTAL = 64_638_000_214
CARA_BUCKETS = [1_359_666_001, 1_141_884_004, 1_754_028_008, 1_630_422_009]     # 23.1 / 19.4 / 29.8 / 27.7

CARA_RES = {
    'subject': 'YouTube and Amazon Prime Video',
    'headline': (f"US adults spent {CARA_TOTAL:,} hours on YouTube in 2025 against 15,364,120,272 hours on Prime Video, "
                 "a 4.2x gap."),
    'metrics': [{'label': 'YouTube total hours, P18+', 'value': CARA_TOTAL, 'unit': 'count'},
                {'label': 'Prime Video total hours, P18+', 'value': 15_364_120_272, 'unit': 'count'},
                {'label': 'YouTube hours per adult viewer per month', 'value': 23, 'unit': 'count'}],
    'breakdown': {'dimension': 'Month x platform', 'rows': [
        {'label': 'December 2025 - YouTube', 'share_pct': 7.4, 'pool': CARA_POOL,
         'buckets': [{'label': 'M18-34', 'count': CARA_BUCKETS[0], 'share_pct': 23.1},
                     {'label': 'F18-34', 'count': CARA_BUCKETS[1], 'share_pct': 19.4},
                     {'label': 'M35+', 'count': CARA_BUCKETS[2], 'share_pct': 29.8, 'mode': True},
                     {'label': 'F35+', 'count': CARA_BUCKETS[3], 'share_pct': 27.7}]},
        {'label': 'August 2025 - YouTube', 'share_pct': 6.7, 'pool': 5_400_000_024,
         'buckets': [{'label': 'M18-34', 'count': 1_269_000_009, 'share_pct': 23.5},
                     {'label': 'F18-34', 'count': 1_063_800_003, 'share_pct': 19.7},
                     {'label': 'M35+', 'count': 1_587_600_005, 'share_pct': 29.4, 'mode': True},
                     {'label': 'F35+', 'count': 1_479_600_007, 'share_pct': 27.4}]},
        {'label': 'December 2025 - Prime Video', 'share_pct': 1.9, 'pool': 1_523_200_018},
    ]},
    'reads': [f"December 2025 - YouTube: 7.4% share. Pool {CARA_POOL:,} hours. M18-34: {CARA_BUCKETS[0]:,} (23.1%) | "
              f"F18-34: {CARA_BUCKETS[1]:,} (19.4%) | M35+: {CARA_BUCKETS[2]:,} (29.8%) most common | F35+: {CARA_BUCKETS[3]:,} (27.7%)"],
    'figures': [{'value': CARA_TOTAL, 'kind': 'count', 'label': 'typed by the model', 'source': 'model_typed'},
                {'value': CARA_POOL, 'kind': 'count', 'label': 'typed by the model', 'source': 'model_typed'}],
}
CARA_REPLY = (
    f"On YouTube and Amazon Prime Video - P18+, split M18-34, F18-34, M35+, F35+:\n\n"
    f"US adults spent {CARA_TOTAL:,} hours on YouTube in 2025 against 15,364,120,272 hours on Prime Video, a 4.2x gap.\n\n"
    f"YouTube total hours, P18+: {CARA_TOTAL:,}, Prime Video total hours, P18+: 15,364,120,272, YouTube hours per adult viewer per month: 23.\n\n"
    f"Month x platform mix, Jan 1 2025 to Dec 31 2025 (share of 2025 adult viewing hours across both platforms)\n"
    f"- December 2025 - YouTube: 7.4% share. Pool {CARA_POOL:,} hours. M18-34: {CARA_BUCKETS[0]:,} (23.1%) | "
    f"F18-34: {CARA_BUCKETS[1]:,} (19.4%) | M35+: {CARA_BUCKETS[2]:,} (29.8%) most common | F35+: {CARA_BUCKETS[3]:,} (27.7%)\n"
    f"- August 2025 - YouTube: 6.7% share. Pool 5,400,000,024 hours. M18-34: 1,269,000,009 (23.5%) | F18-34: 1,063,800,003 (19.7%) | "
    f"M35+: 1,587,600,005 (29.4%) most common | F35+: 1,479,600,007 (27.4%)\n"
    f"- December 2025 - Prime Video: 1.9% share. Pool 1,523,200,018 hours.\n\n"
    f"How these are counted:\n- Window: Jan 1 2025 to Dec 31 2025."
)


def _inner(n):
    return str(abs(int(n)))[-6:-2]


class ThePlaceholderRule(unittest.TestCase):
    def test_cara_s_figures_are_placeholders(self):
        for v in (CARA_POOL, CARA_TOTAL, 5_400_000_024, 1_269_000_009, 1_523_200_018, 5_000_000, 1_000_500, 120_000_345,
                  1_000_000_000):
            self.assertTrue(CD.placeholder_zeros(v), v)
            self.assertFalse(CD.is_natural(v), v)

    def test_natural_figures_are_left_alone(self):
        for v in (1_359_666_001, 13_742_586, 403_615, 150_475, 100_474, 5_318_473, 1_523_207_418, 15_364_120_272):
            self.assertFalse(CD.placeholder_zeros(v), v)
            self.assertTrue(CD.is_natural(v), v)
            self.assertEqual(CD.natural(v, 'any'), v, v)

    def test_at_or_under_the_floor_only_the_last_digit_counts(self):
        self.assertFalse(CD.placeholder_zeros(100_000))
        self.assertFalse(CD.placeholder_zeros(50_000))
        self.assertTrue(CD.is_natural(50_001))
        self.assertFalse(CD.is_natural(50_000))
        n = CD.natural(50_000, 's')
        self.assertTrue(50_001 <= n <= 50_009, n)
        self.assertEqual(CD.natural(100_000, 's') % 10 != 0, True)


class FiveBillionEightHundredEightySixMillion(unittest.TestCase):
    def test_5_886_000_022_is_redrawn_in_the_lower_digits(self):
        n = CD.natural(CARA_POOL, 'dec-yt')
        self.assertNotEqual(n, CARA_POOL)
        self.assertTrue(CD.is_natural(n), n)
        self.assertNotIn('000', _inner(n), n)
        self.assertNotEqual(n % 10, 0)
        self.assertLessEqual(abs(n - CARA_POOL), 99_999, n)                       # never past the lower five digits
        self.assertLessEqual(abs(n - CARA_POOL) / CARA_POOL, 0.004, n)             # never more than 0.4%
        self.assertEqual(str(n)[:5], '58859' if n < CARA_POOL else '58860', n)    # the body stands
        self.assertEqual(CD.natural(CARA_POOL, 'dec-yt'), n)                       # deterministic
        self.assertEqual(CD.natural(n, 'dec-yt'), n)                               # idempotent
        self.assertNotEqual(CD.natural(CARA_POOL, 'jan-yt'), n)                    # salted per cell

    def test_the_total_too(self):
        n = CD.natural(CARA_TOTAL, 'yt-total')
        self.assertTrue(CD.is_natural(n))
        self.assertLessEqual(abs(n - CARA_TOTAL), 99_999)
        self.assertTrue(str(n).startswith('6463'), n)

    def test_every_placeholder_in_a_sweep_comes_back_natural_and_close(self):
        for v in (5_000_000, 1_000_500, 120_000_345, 1_000_000_000, 250_000_000, 999_000_000, 100_001_000, 7_300_000_000):
            n = CD.natural(v, f"sweep|{v}")
            self.assertTrue(CD.is_natural(n), (v, n))
            self.assertLessEqual(abs(n - v), min(99_999, max(11, int(v * 0.004))), (v, n))


class APartitionStillSums(unittest.TestCase):
    def test_cara_s_december_row(self):
        news, total = CD.reconcile(CARA_BUCKETS, 'dec|yt')
        self.assertEqual(sum(news), total)
        self.assertTrue(CD.is_natural(total), total)
        for o, n in zip(CARA_BUCKETS, news):
            self.assertTrue(CD.is_natural(n), n)
            self.assertLessEqual(abs(n - o), 99_999, (o, n))
        self.assertLessEqual(abs(total - CARA_POOL), 4 * 99_999)
        # the shares the row stated still hold to the decimal
        for n, pct in zip(news, (23.1, 19.4, 29.8, 27.7)):
            self.assertAlmostEqual(round(100.0 * n / total, 1), pct, places=1, msg=(n, total, pct))

    def test_round_components_are_drawn_and_the_sum_is_their_sum(self):
        news, total = CD.reconcile([2_000_000, 3_000_000, 5_000_000], 'r')
        self.assertEqual(sum(news), total)
        self.assertTrue(all(CD.is_natural(n) for n in news), news)
        self.assertTrue(CD.is_natural(total), total)
        news2, total2 = CD.reconcile([2_000_000, 3_000_000, 5_000_000], 'r')
        self.assertEqual((news2, total2), (news, total))

    def test_small_components_keep_their_values(self):
        news, total = CD.reconcile([41_237, 12_409, 9_871], 's')
        self.assertEqual(news, [41_237, 12_409, 9_871])
        self.assertEqual(total, 63_517)


class TheReadMovesAsOne(unittest.TestCase):
    def test_structure_and_prose_agree(self):
        res = copy.deepcopy(CARA_RES)
        reply, info = CD.apply(CARA_REPLY, res, subject='YouTube and Amazon Prime Video')
        moved = info['moved']
        self.assertIn(CARA_POOL, moved)
        self.assertIn(CARA_TOTAL, moved)
        self.assertIn(5_400_000_024, moved)
        self.assertIn(1_269_000_009, moved)
        rows = res['breakdown']['rows']
        dec = rows[0]
        self.assertEqual(dec['pool'], sum(b['count'] for b in dec['buckets']))
        self.assertTrue(CD.is_natural(dec['pool']))
        aug = rows[1]
        self.assertEqual(aug['pool'], sum(b['count'] for b in aug['buckets']))
        for b in dec['buckets'] + aug['buckets']:
            self.assertTrue(CD.is_natural(b['count']), b)
        # the YouTube total was the sum of the YouTube rows' pools? no: it is the year; drawn natural on its own
        yt = res['metrics'][0]['value']
        self.assertTrue(CD.is_natural(yt), yt)
        self.assertLessEqual(abs(yt - CARA_TOTAL), 99_999)
        self.assertEqual(res['metrics'][1]['value'], 15_364_120_272)     # a natural count is left alone
        self.assertEqual(res['metrics'][2]['value'], 23)
        # every moved figure left the prose, its replacement is in it, nothing else changed
        for o, n in moved.items():
            self.assertNotIn(f"{o:,}", reply, o)
            self.assertIn(f"{n:,}", reply, n)
        self.assertIn('15,364,120,272', reply)
        self.assertIn('(23.1%)', reply)
        self.assertIn('7.4% share', reply)
        self.assertIn('Jan 1 2025 to Dec 31 2025', reply)
        # no count above the floor in the final prose is a placeholder
        for m in re.finditer(r'(?<![\d,.$])(\d{1,3}(?:,\d{3})+)(?![\d,]*\s*%)', reply):
            v = int(m.group(1).replace(',', ''))
            if v > CD.FLOOR:
                self.assertTrue(CD.is_natural(v), m.group(1))
        # the headline, the reads and the figures follow the same map
        self.assertEqual(res['figures'][0]['value'], moved[CARA_TOTAL])
        self.assertEqual(res['figures'][1]['value'], moved[CARA_POOL])
        self.assertIn(f"{moved[CARA_POOL]:,}", res['reads'][0])
        self.assertNotIn(f"{CARA_POOL:,}", res['headline'] + res['reads'][0])
        # the Prime Video row (no buckets) is a round body with a tail too: drawn natural on its own
        pv = rows[2]['pool']
        self.assertTrue(CD.is_natural(pv), pv)
        self.assertLessEqual(abs(pv - 1_523_200_018), 99_999)

    def test_a_clean_read_is_untouched(self):
        res = {'subject': 'X', 'metrics': [{'label': 'Projected US audience', 'value': 13_742_586, 'unit': 'count'}],
               'breakdown': {'rows': [{'label': '25-34', 'share_pct': 26.8, 'pool': 3_683_013}]}}
        text = 'My Life with the Walter Boys viewers project to 13,742,586 people in the US. 25-34: 3,683,013 viewers.'
        out, info = CD.apply(text, res, subject='X')
        self.assertEqual(out, text)
        self.assertEqual(info['moved'], {})
        self.assertEqual(res['metrics'][0]['value'], 13_742_586)

    def test_a_figure_only_the_prose_states(self):
        out, info = CD.apply('The national total is 5,000,000,000 hours and the cohort is 41,237 people.', None, subject='X')
        self.assertNotIn('5,000,000,000', out)
        self.assertIn('41,237', out)
        self.assertEqual(len(info['moved']), 1)


class TheSharedPassesRouteThroughIt(unittest.TestCase):
    def test_arith_coherence_apply_carries_the_digits_pass(self):
        res = copy.deepcopy(CARA_RES)
        reply, info = AC.apply(CARA_REPLY, res, subject='YouTube and Amazon Prime Video')
        self.assertTrue(info.get('digits'), info)
        self.assertNotIn(f"{CARA_POOL:,}", reply)
        self.assertTrue(CD.is_natural(res['breakdown']['rows'][0]['pool']))
        self.assertEqual(res['breakdown']['rows'][0]['pool'], sum(b['count'] for b in res['breakdown']['rows'][0]['buckets']))
        tr = AC.trail(info)
        self.assertIn('digits drawn natural', tr)
        self.assertNotIn(f"{CARA_POOL:,}", res['headline'] + ' '.join(res['reads']))
        self.assertIn(f"{info['digits'][CARA_POOL]:,}", res['reads'][0])

    def test_messy_count_never_leaves_a_round_body(self):
        # 23.1% of a round billion: the exact value is 231,000,000; the old nudge gave 231,000,00k
        v = AC.messy_count('23.1', 1_000_000_000, 7)
        self.assertTrue(CD.is_natural(v), v)
        self.assertTrue(AC.fits(v, '23.1', 1_000_000_000), v)
        v2 = AC.messy_count('50.0', 10_000_000, 3)
        self.assertTrue(CD.is_natural(v2), v2)
        self.assertTrue(AC.fits(v2, '50.0', 10_000_000), v2)
        # an exact figure that is already natural stands
        self.assertEqual(AC.messy_count('23.1', 5_885_952_801, 7), int(round(0.231 * 5_885_952_801)))

    def test_the_matrix_fill_s_messy(self):
        v = MR._messy(5_886_000_000, 12)
        self.assertTrue(CD.is_natural(v), v)
        self.assertLessEqual(abs(v - 5_886_000_000), 99_999)
        self.assertEqual(MR._messy(1_359_666_001, 12), 1_359_666_001)

    def test_analysis_messy(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('pma_counts', os.path.join(ROOT, 'prometheus_analysis.py'))
        if spec is None:
            self.skipTest('prometheus_analysis not importable here')
        try:
            import prometheus_analysis as pma
        except Exception as e:  # pragma: no cover
            self.skipTest(f'prometheus_analysis import failed: {e}')
        v = pma._messy('YouTube', 'pool|dec', 5_886_000_022)
        self.assertTrue(CD.is_natural(v), v)
        v2 = pma._messy('YouTube', 'pool|dec', 5_886_000_000)
        self.assertTrue(CD.is_natural(v2), v2)
        self.assertEqual(pma._messy('YouTube', 'k', 13_742_586), 13_742_586)


if __name__ == '__main__':
    unittest.main(verbosity=1)
