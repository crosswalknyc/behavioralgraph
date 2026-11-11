#!/usr/bin/env python3
"""A read agrees with itself (2026-11-11, the Mad Men compare).

Job 9e5e8d62fd5a compared Mad Men viewers in 2025 against 2026 through
Nov 9. The model's read was coherent: every 2025 row summed to 7,582,850
(the profile's own projection) and the stated loss, 641,023, was that
total less 6,941,827. The digits pass then drew figures one at a time:

  * the stat line's 2025 total became 7,619,181 (drawn on its own in the
    coherence pass), the headline's became 7,575,296 and each table row
    got a pool of its own (7,583,585, 7,592,567, 7,584,509, 7,593,935,
    7,575,296) while the reply printed 7,575,296 on all of them;
  * one bucket moved in every 2025 row, so no row summed to the pool
    printed beside it;
  * the loss stayed 641,023, the difference of two totals the reply no
    longer showed;

and the run was stamped 'pass'. This suite holds the repair:

  1. the Mad Men read, with the profile's figure on record: nothing
     moves, one 2025 total everywhere, rows sum, the loss is the
     difference, the file agrees with the reply;
  2. the same read with nothing on record (the generic case): the 2025
     total is drawn once and every place that states it follows, each
     row's buckets sum to it, the loss is recomputed from the two totals
     and the file carries the same figures;
  3. a generic read: two cohorts with round pools, a combined total, a
     stated gap and an older band that is two buckets together all move
     as one structure;
  4. the final check (prometheus.read_identities) repairs the text and
     the structure the old pass shipped, in place, and reports it;
  5. a figure on record is never drawn, and neither is one tied to it by
     an identity; a pass that cannot be made to agree leaves the read as
     written;
  6. reading: rows that are each a whole group no longer print
     '10.0% share, 100.0% penetration'; the opener appears once; a row
     label and a file name are never cut inside a word.

Hermetic: no model, no S3, no app module. Run:
    python3 scripts/test_pm_read_identities.py
"""
from __future__ import annotations

import copy
import csv
import io
import json
import os
import re
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import prometheus_analysis as pma  # noqa: E402
from prometheus import arith_coherence as AC  # noqa: E402
from prometheus import count_digits as CD  # noqa: E402
from prometheus import read_identities as RI  # noqa: E402
from prometheus import read_opener as RO  # noqa: E402

FX = json.load(open(os.path.join(HERE, 'fixtures', 'pm_madmen_compare_2026_11_11.json')))
T2025, T2026, LOSS = 7_582_850, 6_941_827, 641_023
_COUNT = re.compile(r'(?<![\d,.$])(\d{1,3}(?:,\d{3})+)(?![\d,]*\s*%)')


def run_read(data, held=None):
    """The read path's own order: coherence, the reply, the arithmetic
    pass, the opener, the final identities."""
    res = pma.enforce_metrics_coherence(copy.deepcopy(data), held=held)
    res['universe'] = data.get('universe') or ''
    reply = pma.format_generated_metrics_reply(res)
    reply, info = AC.apply(reply, res, subject=str(res.get('subject') or ''), held=held)
    reply = RO.with_opener(reply, res.get('universe'))
    reply, idinfo = RI.enforce(reply, res, held=held)
    return res, reply, info, idinfo


def rows_2025(res):
    return [r for r in res['breakdown']['rows'] if '2025' in r['label'].split(' - ', 1)[1]]


def rows_2026(res):
    return [r for r in res['breakdown']['rows'] if '2026' in r['label'].split(' - ', 1)[1]]


def table_lines(reply):
    out = {}
    for ln in reply.split('\n'):
        if ln.startswith('- ') and ' | ' in ln and 'Pool ' in ln:
            label = ln[2:].split(':', 1)[0]
            pool = int(re.search(r'Pool ([\d,]+)', ln).group(1).replace(',', ''))
            counts = [int(x.replace(',', '')) for x in re.findall(r': ([\d,]+) \(\d', ln)]
            out[label] = (pool, counts)
    return out


def csv_pools(res):
    fname, text = pma.build_generated_csv({'subject': res['subject'], 'metrics': res['metrics'],
                                           'breakdown': res['breakdown'], 'family': res.get('metric_family')})
    rows = list(csv.reader(io.StringIO(text)))
    hdr = rows[0]
    gi, ci, pi = hdr.index('Group'), hdr.index('Count'), [i for i, h in enumerate(hdr) if h.startswith('Group pool')][0]
    out = {}
    for r in rows[1:]:
        if len(r) > pi and r[ci].isdigit():
            out.setdefault(r[gi], {'pool': int(r[pi]), 'sum': 0})['sum'] += int(r[ci])
    return fname, out


class TheShippedReadWasBroken(unittest.TestCase):
    """What went to the thread, so the case is on record."""

    def test_three_totals_and_a_loss_that_is_not_the_difference(self):
        head = FX['shipped']['reply_head']
        self.assertIn('7,575,296 US viewers in 2025', head)
        self.assertIn('US viewers, Jan 1 2025 to Dec 30 2025: 7,619,181', head)
        self.assertIn('a loss of 641,023 viewers', head)
        self.assertNotEqual(7_575_296 - T2026, LOSS)
        self.assertIn('7,582,850 -> 7,575,296', FX['shipped']['trail'])

    def test_the_model_s_own_read_was_coherent(self):
        d = FX['data']
        for r in d['breakdown']['rows']:
            self.assertEqual(sum(b['count'] for b in r['buckets']), r['pool'], r['label'])
        self.assertEqual(T2025 - T2026, LOSS)


class MadMenWithTheProfileFigureOnRecord(unittest.TestCase):
    """7,582,850 is the profile's own projection and the figure the
    thread's earlier turn stated: it is on record and never drawn."""

    @classmethod
    def setUpClass(cls):
        held = CD.on_record({'value': 7582850.0, 'kind': 'count', 'label': 'projected audience', 'source': 'profile_file'},
                            ['On people who watched Mad Men: 7,582,850 US viewers in 2025 and 6,418,273 so far in 2026.'])
        cls.res, cls.reply, cls.info, cls.idinfo = run_read(FX['data'], held=held)

    def test_one_2025_total_everywhere(self):
        self.assertEqual(self.res['metrics'][0]['value'], T2025)
        for r in rows_2025(self.res):
            self.assertEqual(r['pool'], T2025, r['label'])
        self.assertIn(f"{T2025:,} US viewers in 2025", self.reply)
        self.assertIn(f"US viewers, Jan 1 2025 to Dec 30 2025: {T2025:,}", self.reply)
        for bad in ('7,575,296', '7,619,181', '7,583,585', '7,592,567'):
            self.assertNotIn(bad, self.reply)

    def test_nothing_was_drawn(self):
        self.assertFalse(self.info.get('digits'), self.info.get('digits'))
        self.assertEqual(self.idinfo['outcome'], 'pass')
        self.assertEqual(self.idinfo['fixes'], [])

    def test_every_row_sums_to_its_pool_in_the_result_the_reply_and_the_file(self):
        for r in self.res['breakdown']['rows']:
            self.assertEqual(sum(b['count'] for b in r['buckets']), r['pool'], r['label'])
        lines = table_lines(self.reply)
        self.assertEqual(len(lines), 10)
        for label, (pool, counts) in lines.items():
            self.assertEqual(sum(counts), pool, label)
        _fn, pools = csv_pools(self.res)
        self.assertEqual(len(pools), 10)
        for g, v in pools.items():
            self.assertEqual(v['sum'], v['pool'], g)

    def test_the_loss_is_the_difference_of_the_two_printed_totals(self):
        self.assertIn(f"a loss of {LOSS:,} viewers", self.reply)
        self.assertEqual(T2025 - T2026, LOSS)


class MadMenWithNothingOnRecord(unittest.TestCase):
    """The generic case: the round-ended total is drawn, once, and the
    whole read moves with it."""

    @classmethod
    def setUpClass(cls):
        cls.res, cls.reply, cls.info, cls.idinfo = run_read(FX['data'])
        cls.total = cls.res['metrics'][0]['value']

    def test_the_total_is_drawn_once(self):
        self.assertNotEqual(self.total, T2025)
        self.assertTrue(CD.is_natural(self.total), self.total)
        self.assertLessEqual(abs(self.total - T2025) / T2025, 0.004)
        for r in rows_2025(self.res):
            self.assertEqual(r['pool'], self.total, r['label'])
        self.assertIn(f"{self.total:,} US viewers in 2025", self.reply)
        self.assertIn(f"US viewers, Jan 1 2025 to Dec 30 2025: {self.total:,}", self.reply)
        self.assertNotIn(f"{T2025:,}", self.reply)
        # one 2025 total in the whole reply: every figure within 1% of it is it
        near = {int(m.group(1).replace(',', '')) for m in _COUNT.finditer(self.reply)
                if abs(int(m.group(1).replace(',', '')) - T2025) <= 0.01 * T2025}
        self.assertEqual(near, {self.total})

    def test_the_2026_side_never_moved(self):
        self.assertEqual(self.res['metrics'][1]['value'], T2026)
        for r in rows_2026(self.res):
            self.assertEqual(r['pool'], T2026)
            self.assertEqual(sum(b['count'] for b in r['buckets']), T2026)

    def test_every_row_sums_to_its_pool_in_the_result_the_reply_and_the_file(self):
        for r in self.res['breakdown']['rows']:
            self.assertEqual(sum(b['count'] for b in r['buckets']), r['pool'], r['label'])
        for label, (pool, counts) in table_lines(self.reply).items():
            self.assertEqual(sum(counts), pool, label)
        _fn, pools = csv_pools(self.res)
        for g, v in pools.items():
            self.assertEqual(v['sum'], v['pool'], g)
            if '2025' in g.split(' - ', 1)[1]:
                self.assertEqual(v['pool'], self.total, g)

    def test_the_loss_is_recomputed_from_the_two_totals(self):
        loss = self.total - T2026
        self.assertIn(f"a loss of {loss:,} viewers", self.reply)
        self.assertNotIn(f"{LOSS:,}", self.reply)
        self.assertIn(f"a loss of {loss:,} viewers", self.res['headline'])

    def test_a_bucket_the_prose_quotes_follows_the_table(self):
        # '65 or older drops from 948,364 people to 832,283': both are buckets
        age25 = [r for r in rows_2025(self.res) if r['label'].startswith('Age')][0]
        older = [b['count'] for b in age25['buckets'] if b['label'] == '65 or Older'][0]
        self.assertIn(f"65 or older drops from {older:,} people to 832,283", self.reply)

    def test_the_final_check_found_nothing_left_to_fix(self):
        self.assertEqual(self.idinfo['outcome'], 'pass', self.idinfo)
        self.assertEqual(RI.check(self.reply, self.res), [])

    def test_the_pass_is_stable(self):
        res2, reply2, _i, _d = run_read(FX['data'])
        self.assertEqual(reply2, self.reply)
        again, info = AC.apply(self.reply, copy.deepcopy(self.res), subject='Mad Men')
        self.assertEqual(again, self.reply)
        self.assertFalse(info.get('digits'))


GENERIC = {
    'subject': 'Northfield Outfitters', 'metric_family': 'audience',
    'window_label': 'Nov 11 2025 to Nov 11 2026',
    'headline': ('Northfield Outfitters reached 5,200,000 app users and 3,400,000 site-only users, a gap of 1,800,000 people, '
                 '8,600,000 across both.'),
    'reads': ['App users 45 and older number 2,340,000 against 1,190,000 on the site.',
              'The app adds 1,800,000 people the site never sees.'],
    'metrics': [{'name': 'app_users', 'label': 'App users', 'unit': 'people', 'value': 5_200_000},
                {'name': 'site_users', 'label': 'Site-only users', 'unit': 'people', 'value': 3_400_000},
                {'name': 'combined', 'label': 'App and site-only users', 'unit': 'people', 'value': 8_600_000}],
    'breakdown': {'dimension': 'Age and gender by cohort', 'share_basis': 'share of the cohort', 'rows': [
        {'label': 'Age - App users', 'share_pct': 25, 'pool': 5_200_000, 'buckets': [
            {'label': '18-24', 'count': 780_000, 'share_pct': 15.0}, {'label': '25-44', 'count': 2_080_000, 'share_pct': 40.0},
            {'label': '45-64', 'count': 1_560_000, 'share_pct': 30.0}, {'label': '65 or Older', 'count': 780_000, 'share_pct': 15.0}]},
        {'label': 'Gender - App users', 'share_pct': 25, 'pool': 5_200_000, 'buckets': [
            {'label': 'Female', 'count': 2_756_000, 'share_pct': 53.0}, {'label': 'Male', 'count': 2_444_000, 'share_pct': 47.0}]},
        {'label': 'Age - Site-only users', 'share_pct': 25, 'pool': 3_400_000, 'buckets': [
            {'label': '18-24', 'count': 850_000, 'share_pct': 25.0}, {'label': '25-44', 'count': 1_360_000, 'share_pct': 40.0},
            {'label': '45-64', 'count': 816_000, 'share_pct': 24.0}, {'label': '65 or Older', 'count': 374_000, 'share_pct': 11.0}]},
        {'label': 'Gender - Site-only users', 'share_pct': 25, 'pool': 3_400_000, 'buckets': [
            {'label': 'Female', 'count': 1_632_000, 'share_pct': 48.0}, {'label': 'Male', 'count': 1_768_000, 'share_pct': 52.0}]},
    ]},
}


class AGenericReadMovesAsOne(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res, cls.reply, cls.info, cls.idinfo = run_read(GENERIC)
        m = cls.res['metrics']
        cls.app, cls.site, cls.both = m[0]['value'], m[1]['value'], m[2]['value']

    def test_every_round_figure_was_drawn(self):
        for v in (self.app, self.site):
            self.assertTrue(CD.is_natural(v), v)
        # a figure that is arithmetic on others is their sum or difference,
        # whatever its last digit; every other count was drawn
        derived = {self.both, self.app - self.site}
        rows = self.res['breakdown']['rows']
        derived |= {rows[0]['buckets'][2]['count'] + rows[0]['buckets'][3]['count'],
                    rows[2]['buckets'][2]['count'] + rows[2]['buckets'][3]['count']}
        for m in _COUNT.finditer(self.reply):
            v = int(m.group(1).replace(',', ''))
            if v > CD.FLOOR and v not in derived:
                self.assertTrue(CD.is_natural(v), m.group(1))
            self.assertNotEqual(v % 1000, 0, m.group(1))

    def test_rows_that_share_a_pool_still_share_it_and_sum_to_it(self):
        rows = self.res['breakdown']['rows']
        self.assertEqual(rows[0]['pool'], self.app)
        self.assertEqual(rows[1]['pool'], self.app)
        self.assertEqual(rows[2]['pool'], self.site)
        self.assertEqual(rows[3]['pool'], self.site)
        for r in rows:
            self.assertEqual(sum(b['count'] for b in r['buckets']), r['pool'], r['label'])
        for label, (pool, counts) in table_lines(self.reply).items():
            self.assertEqual(sum(counts), pool, label)

    def test_the_combined_total_is_the_sum_and_the_gap_is_the_difference(self):
        self.assertEqual(self.both, self.app + self.site)
        gap = self.app - self.site
        self.assertIn(f"a gap of {gap:,} people", self.reply)
        self.assertIn(f"{self.both:,} across both", self.reply)
        self.assertIn(f"The app adds {gap:,} people", self.reply)
        self.assertNotIn('1,800,000', self.reply)

    def test_two_buckets_added_together_follow_the_buckets(self):
        rows = self.res['breakdown']['rows']
        app_older = rows[0]['buckets'][2]['count'] + rows[0]['buckets'][3]['count']
        site_older = rows[2]['buckets'][2]['count'] + rows[2]['buckets'][3]['count']
        self.assertIn(f"App users 45 and older number {app_older:,} against {site_older:,} on the site", self.reply)

    def test_the_file_agrees(self):
        _fn, pools = csv_pools(self.res)
        for g, v in pools.items():
            self.assertEqual(v['sum'], v['pool'], g)
        self.assertEqual(RI.check(self.reply, self.res), [])


class TheFinalCheckRepairsWhatTheOldPassShipped(unittest.TestCase):
    """The text and the structure the old pass left behind, put through
    the final check alone."""

    def _broken(self):
        res = pma.enforce_metrics_coherence(copy.deepcopy(FX['data']), held={T2025})
        res['universe'] = FX['data']['universe']
        # the old pass: one bucket moved in every 2025 row, a pool per row, a stat of its own
        moves = {1_747_899: 1_748_634, 3_806_819: 3_816_536, 1_704_162: 1_705_821, 3_124_131: 3_135_216, 2_745_561: 2_738_007}
        for r in rows_2025(res):
            for b in r['buckets']:
                b['count'] = moves.get(b['count'], b['count'])
            r['pool'] = sum(b['count'] for b in r['buckets'])
        res['metrics'][0]['value'] = 7_619_181
        reply = pma.format_generated_metrics_reply(res)
        for r in rows_2025(res):
            reply = reply.replace(f"Pool {r['pool']:,}", 'Pool 7,575,296')
        reply = reply.replace('7,582,850 US viewers in 2025', '7,575,296 US viewers in 2025')
        return res, reply

    def test_the_broken_read_is_what_shipped(self):
        res, reply = self._broken()
        self.assertEqual(len({r['pool'] for r in rows_2025(res)}), 5)
        self.assertIn('US viewers, Jan 1 2025 to Dec 30 2025: 7,619,181', reply)
        notes = RI.check(reply, res)
        self.assertTrue(notes)

    def test_it_is_repaired_in_place_and_reported(self):
        res, reply = self._broken()
        out, info = RI.enforce(reply, res)
        self.assertEqual(info['outcome'], 'fixed')
        self.assertTrue(RI.trail(info).startswith('identities fixed'))
        pools = {r['pool'] for r in rows_2025(res)}
        self.assertEqual(len(pools), 1, pools)
        total = pools.pop()
        self.assertEqual(res['metrics'][0]['value'], total)      # the stat is the pool
        for r in res['breakdown']['rows']:
            self.assertEqual(sum(b['count'] for b in r['buckets']), r['pool'], r['label'])
        for label, (pool, counts) in table_lines(out).items():
            self.assertEqual(sum(counts), pool, label)
            if '2025' in label.split(' - ', 1)[1]:
                self.assertEqual(pool, total)
        self.assertIn(f"US viewers, Jan 1 2025 to Dec 30 2025: {total:,}", out)
        # the loss is the difference of the two totals printed beside it
        m = re.search(r'([\d,]+) US viewers in 2025 and ([\d,]+) in 2026 through Nov 9, a loss of ([\d,]+) viewers', out)
        a, b, d = (int(x.replace(',', '')) for x in m.groups())
        self.assertEqual(a - b, d)
        self.assertEqual(RI.check(out, res), [])

    def test_it_never_raises_and_never_holds(self):
        for reply, res in (('', None), (None, {}), ('no figures here', {'breakdown': {'rows': [None, {'label': 'x'}]}}),
                           ('a loss of 5 viewers', {'metrics': [{'value': 'n/a'}]})):
            out, info = RI.enforce(reply, res)
            self.assertIn(info['outcome'], ('pass', 'fixed'))

    def test_a_change_that_is_another_quantity_is_left_alone(self):
        s = 'Netflix reaches 5,300,112 people and Hulu 4,900,221; the two share an overlap of 2,100,455 and a gap of 150,003 in weekly users.'
        out, info = RI.enforce(s, None)
        self.assertEqual(out, s)

    def test_two_months_on_one_dimension_keep_their_own_pools(self):
        res = {'breakdown': {'rows': [
            {'label': 'December 2025 - YouTube', 'pool': 5_885_952_801, 'buckets': [
                {'label': 'M18-34', 'count': 2_885_952_800}, {'label': 'F18-34', 'count': 3_000_000_001}]},
            {'label': 'January 2026 - YouTube', 'pool': 5_870_100_403, 'buckets': [
                {'label': 'M18-34', 'count': 2_870_100_402}, {'label': 'F18-34', 'count': 3_000_000_001}]}]}}
        _out, info = RI.enforce('', res)
        self.assertEqual(info['fixes'], [])
        self.assertEqual([r['pool'] for r in res['breakdown']['rows']], [5_885_952_801, 5_870_100_403])


class AFigureOnRecordIsNeverDrawn(unittest.TestCase):
    def test_on_record_collects_from_every_kind_of_record(self):
        class Reg:
            figures = [{'value': 7582850.0, 'kind': 'count'}, {'value': 48.9, 'kind': 'pct'}]
        held = CD.on_record(Reg(), [{'reply': 'Avid Fan tier (1,971,548 people)', 'metrics': [{'value': 6418273}]}],
                            ['it was 13,315,259 last week', None], {'sample': 237755, 'projection': 7843537})
        for v in (7_582_850, 1_971_548, 6_418_273, 13_315_259, 237_755, 7_843_537):
            self.assertIn(v, held)
        self.assertNotIn(48, held)

    def test_a_held_round_figure_stands_in_the_structure_and_the_prose(self):
        res = {'subject': 'X', 'metrics': [{'label': 'US viewers', 'value': 5_000_000, 'unit': 'viewers'}],
               'breakdown': {'rows': [{'label': 'Gender', 'share_pct': 100, 'pool': 5_000_000, 'buckets': [
                   {'label': 'Female', 'count': 2_600_000, 'share_pct': 52.0}, {'label': 'Male', 'count': 2_400_000, 'share_pct': 48.0}]}]}}
        out, info = CD.apply('X has 5,000,000 US viewers.', res, subject='X', held={5_000_000})
        self.assertIn('5,000,000', out)
        self.assertEqual(res['metrics'][0]['value'], 5_000_000)
        self.assertEqual(res['breakdown']['rows'][0]['pool'], 5_000_000)
        bks = res['breakdown']['rows'][0]['buckets']
        self.assertEqual(sum(b['count'] for b in bks), 5_000_000)       # the buckets are drawn, and still sum
        self.assertTrue(all(CD.is_natural(b['count']) for b in bks), bks)

    def test_a_held_difference_holds_its_two_totals(self):
        res = {'subject': 'X', 'metrics': [{'label': 'A', 'value': 4_000_000, 'unit': 'people'},
                                           {'label': 'B', 'value': 1_000_000, 'unit': 'people'}]}
        text = 'A is 4,000,000 and B is 1,000,000, a difference of 3,000,000.'
        out, info = CD.apply(text, res, subject='X', held={3_000_000})
        self.assertEqual(out, text)
        self.assertEqual(info['moved'], {})

    def test_when_the_moves_cannot_agree_the_read_is_left_as_written(self):
        # every bucket is on record and the pool is not their sum: nothing fits, nothing moves
        res = {'subject': 'X', 'breakdown': {'rows': [{'label': 'Gender', 'share_pct': 100, 'pool': 1_000_010, 'buckets': [
            {'label': 'Female', 'count': 520_003, 'share_pct': 52.0}, {'label': 'Male', 'count': 480_001, 'share_pct': 48.0}]}]}}
        before = copy.deepcopy(res)
        out, info = CD.apply('Pool 1,000,010.', res, subject='X', held={520_003, 480_001})
        self.assertEqual(res, before)
        self.assertEqual(out, 'Pool 1,000,010.')

    def test_a_top_n_list_is_not_forced_to_sum(self):
        res = {'subject': 'X', 'breakdown': {'rows': [{'label': 'Streaming', 'share_pct': 100, 'pool': 9_412_337, 'buckets': [
            {'label': 'Netflix', 'count': 6_100_441, 'share_pct': 64.8}, {'label': 'Hulu', 'count': 4_233_087, 'share_pct': 45.0},
            {'label': 'Max', 'count': 3_900_000, 'share_pct': 41.4}]}]}}
        CD.fix_result(res, subject='X')
        row = res['breakdown']['rows'][0]
        self.assertEqual(row['pool'], 9_412_337)                 # the pool is not the sum of overlapping groups
        self.assertEqual(row['buckets'][0]['count'], 6_100_441)
        self.assertTrue(CD.is_natural(row['buckets'][2]['count']))
        self.assertNotEqual(row['buckets'][2]['count'], 3_900_000)


class Reading(unittest.TestCase):
    def test_a_whole_group_row_prints_no_share_of_its_own(self):
        res, reply, _i, _d = run_read(FX['data'], held={T2025})
        self.assertNotIn('10.0% share', reply)
        self.assertNotIn('100.0% penetration', reply)
        self.assertIn('- Age / generation - 1/1/2025 - 12/30/2025: Gen X 42.6% leads, Millennials 37.5%. Pool 7,582,850 viewers. '
                      '17 and Under: 116,921 (1.5%)', reply)
        fname, text = pma.build_generated_csv({'subject': 'Mad Men', 'metrics': res['metrics'], 'breakdown': res['breakdown']})
        rows = list(csv.reader(io.StringIO(text)))
        share_col = [i for i, h in enumerate(rows[0]) if h.startswith('Row share of total')][0]
        self.assertTrue(all(r[share_col] == '' for r in rows[1:] if len(r) > share_col and r[0] != 'Note'))

    def test_rows_that_are_parts_of_a_whole_still_print_their_share(self):
        res = pma.enforce_metrics_coherence({'subject': 'X', 'headline': 'h', 'breakdown': {'dimension': 'Category', 'rows': [
            {'label': 'Comedy', 'share_pct': 44.3, 'penetration_pct': 2.0}, {'label': 'Horror', 'share_pct': 23.0, 'penetration_pct': 1.0},
            {'label': 'Faith', 'share_pct': 32.7, 'penetration_pct': 0.7}]}})
        reply = pma.format_generated_metrics_reply(res)
        self.assertIn('- Comedy: 44.3% share, 2.0% penetration', reply)

    def test_one_opener(self):
        res, reply, _i, _d = run_read(FX['data'], held={T2025})
        self.assertEqual(reply.count('On people who watched Mad Men'), 1)
        self.assertTrue(reply.startswith('On people who watched Mad Men in the window:\n\n7,582,850 US viewers in 2025'), reply[:120])
        self.assertEqual(RO.with_opener(reply, 'people who watched Mad Men in the window'), reply)
        # an opener that names something else keeps its line and gets the audience above it
        other = RO.with_opener('On Netflix: 4,112,337 viewers.', 'people who watched Mad Men in the window')
        self.assertTrue(other.startswith('On people who watched Mad Men in the window:\n\nOn Netflix: 4,112,337'))
        self.assertEqual(RO.with_opener('Plain answer.', ''), 'Plain answer.')

    def test_a_row_label_is_never_cut_inside_a_word(self):
        long = 'Lifestyle / Arts & Culture on YouTube: Tiny Desk Concerts (NPR Music)'
        res = pma.enforce_metrics_coherence({'subject': 'X', 'headline': 'h', 'breakdown': {'dimension': 'Category', 'rows': [
            {'label': long, 'share_pct': 15.2, 'penetration_pct': 10.9}, {'label': 'Comedy on YouTube: Hot Ones', 'share_pct': 50.0},
            {'label': 'Horror on TikTok: ' + 'a very long example title that keeps going well past the limit (with a qualifier at the end)',
             'share_pct': 34.8}]}})
        labels = [r['label'] for r in res['breakdown']['rows']]
        self.assertIn(long, labels)
        for lab in labels:
            self.assertEqual(lab.count('('), lab.count(')'), lab)
            self.assertFalse(lab.endswith('('), lab)
        self.assertNotIn('Tiny Desk Concerts (N:', pma.format_generated_metrics_reply(res))


if __name__ == '__main__':
    unittest.main(verbosity=1)
