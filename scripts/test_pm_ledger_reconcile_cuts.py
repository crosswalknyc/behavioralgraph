#!/usr/bin/env python3
"""A cut of a subject is never evidence against the base profile's read (2026-11-11).

When a profile file is indexed, the catalog hands the ledger the file's
audience figure and any banked entry that disagrees with it is retired
('dashboard figures win', 2026-10-06). The comparison ran across every
file under the subject, so indexing 'Cruel Summer Viewers - Avid Fan.csv'
(501,151) retired the base profile's own delivered record (2,573,979)
and the read banked on the base minutes earlier. In the two weeks to
2026-11-11, 83 of the 98 entries the catalog retired went that way, and
the base profile of nearly every new build had no figure on record.

Holds insights_ledger.retire_conflicting_entries / conflicting_fact:

  1. the base profile's record and reads survive its Avid Fan cut, a
     season cut and a year cut being indexed;
  2. the base figure still wins over a stale read of the base;
  3. a cut's figure still wins over a stale record of that cut, and the
     base figure never speaks for an entry about a cut;
  4. a count of part of the audience ('Core 25-44 audience') is in
     conflict only when it is larger than the audience itself;
  5. a total that agrees with one of the figures that apply is kept;
  6. the window rule and the platform rule read as before.

Hermetic: the index lives in memory. Run:
    python3 scripts/test_pm_ledger_reconcile_cuts.py
"""
from __future__ import annotations

import copy
import os
import sys
import unittest

os.environ.setdefault('REGRESSION_TEST_MODE', '1')
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import insights_ledger as il  # noqa: E402

BASE_KEY = 'Cruel_Summer_Viewers_11_11_2026_02_14.csv'
AVID_KEY = 'Cruel Summer Viewers - Avid Fan.csv'
YEAR_KEY = 'Cruel Summer Viewers - 2021 Total Universe.csv'
WIN = {'start': '2025-11-11', 'end': '2026-11-11'}


def fact(value, key, *, cut=False, kind='audience_size', label='US audience', window=WIN, product='profile'):
    return {'product': product, 'kind': kind, 'label': label, 'value': value, 'unit': 'people',
            'window': window, 'source': {'key': key, 'user': ''}, 'note': 'Profile IQ: x', 'cut': cut}


def entry(k, route, metrics, *, base='', q='', reply='', ws='', we='', subject='Cruel Summer Viewers', cohort=''):
    return {'k': k, 'subject': subject, 'family': 'audience', 'q': q, 'route': route, 'ts': '2026-11-11T03:22:29Z',
            'ws': ws, 'we': we, 'metrics': metrics, 'base': base, 'cohort': cohort, 'universe': '', 'reply': reply}


def people(label, value, definition='', name=''):
    return {'name': name or label, 'label': label, 'value': value, 'unit': 'people', 'definition': definition}


DELIVERED = entry('delivered', 'delivered_profile',
                  [people('US audience', 2_573_979.0, 'Total Universe, projected to the US'),
                   people('Avid fans', 501_151.0, 'Avid Fan cut, projected to the US')],
                  q='Profile IQ profile: Cruel Summer Viewers', reply=f'Profile IQ profile {BASE_KEY}')
READ = entry('read', 'reasoned_metrics',
             [people('Unique US viewers, seasons 1 and 2', 2_573_979,
                     'distinct US individuals with at least one observed play of any Cruel Summer episode in the window'),
              people('Season 1 viewers', 2_253_521, 'distinct US individuals with a play of a season 1 episode')],
             base=BASE_KEY, q='Cruel Summer series was in 2021 and it only has 2 seasons for now',
             ws='2025-11-11', we='2026-11-11')


class Index:
    """The ledger index, in memory, behind the module's own two doors."""

    def __init__(self, entries, subject='Cruel Summer Viewers'):
        self.doc = {'subjects': {il.subject_key(subject): {'subject': subject, 'entries': copy.deepcopy(entries)}}}
        self.skey = il.subject_key(subject)

    def __enter__(self):
        self._load, self._update = il._load_index, il._update_index
        il._load_index = lambda force=False: self.doc

        def _update(mutate_fn, max_retries=5):
            new = mutate_fn(self.doc)
            if new is not None:
                self.doc = new
            return new
        il._update_index = _update
        return self

    def __exit__(self, *a):
        il._load_index, il._update_index = self._load, self._update

    def live(self):
        return [e['k'] for e in self.doc['subjects'][self.skey]['entries'] if not e.get('retired')]

    def retired(self, k):
        e = [x for x in self.doc['subjects'][self.skey]['entries'] if x['k'] == k][0]
        return e.get('retired')


class TheBaseSurvivesItsCuts(unittest.TestCase):
    def test_the_avid_fan_cut_does_not_retire_the_base_record_or_its_read(self):
        with Index([DELIVERED, READ]) as ix:
            n = il.retire_conflicting_entries('Cruel Summer Viewers', [fact(501_151, AVID_KEY, cut=True)])
            self.assertEqual(n, 0)
            self.assertEqual(ix.live(), ['delivered', 'read'])

    def test_a_year_cut_and_a_season_cut_do_not_either(self):
        with Index([DELIVERED, READ]) as ix:
            n = il.retire_conflicting_entries('Cruel Summer Viewers', [
                fact(3_809_982, YEAR_KEY, cut=True, window={'start': '2021-01-01', 'end': '2021-12-31'}),
                fact(1_402_118, 'Cruel Summer Viewers - Season 2 Viewers.csv', cut=True)])
            self.assertEqual(n, 0)
            self.assertEqual(ix.live(), ['delivered', 'read'])

    def test_every_file_of_the_subject_at_once(self):
        # the bulk sync hands every fact of the subject in one call
        with Index([DELIVERED, READ]) as ix:
            n = il.retire_conflicting_entries('Cruel Summer Viewers', [
                fact(2_573_979, BASE_KEY), fact(501_151, AVID_KEY, cut=True),
                fact(3_809_982, YEAR_KEY, cut=True, window={'start': '2021-01-01', 'end': '2021-12-31'})])
            self.assertEqual(n, 0)
            self.assertEqual(ix.live(), ['delivered', 'read'])

    def test_the_sony_read_survives_the_season_3_cut(self):
        base = 'My_Life_with_the_Walter_Boys_Viewers_on_Netflix_11_10_2026_23_29.csv'
        read = entry('mix', 'reasoned_metrics', [people('Projected US audience', 13_315_259)], base=base,
                     q='Give me the full brand and retail mix table', ws='2025-11-10', we='2026-11-10',
                     subject='My Life with the Walter Boys Viewers')
        cut_fact = fact(7_843_537, 'My Life with the Walter Boys Viewers on Netflix - Season 3 Viewers.csv', cut=True,
                        window={'start': '2025-11-10', 'end': '2026-11-10'})
        with Index([read], subject='My Life with the Walter Boys Viewers') as ix:
            self.assertEqual(il.retire_conflicting_entries('My Life with the Walter Boys Viewers', [cut_fact]), 0)
            self.assertEqual(ix.live(), ['mix'])


class TheDashboardFigureStillWins(unittest.TestCase):
    def test_a_stale_read_of_the_base_is_retired_by_the_base(self):
        stale = copy.deepcopy(READ)
        stale['metrics'][0]['value'] = 5_842_173          # the read before the profile was rebuilt
        with Index([DELIVERED, stale]) as ix:
            n = il.retire_conflicting_entries('Cruel Summer Viewers', [fact(2_573_979, BASE_KEY), fact(501_151, AVID_KEY, cut=True)])
            self.assertEqual(n, 1)
            self.assertEqual(ix.live(), ['delivered'])
            r = ix.retired('read')
            self.assertEqual(r['by'], 'corpus_catalog')
            self.assertEqual(r['fact_source'], BASE_KEY)
            self.assertEqual(r['dashboard_value'], 2_573_979.0)

    def test_a_stale_record_of_the_base_is_retired_by_a_new_build(self):
        with Index([DELIVERED]) as ix:
            n = il.retire_conflicting_entries('Cruel Summer Viewers', [fact(3_000_117, 'Cruel_Summer_Viewers_12_01_2026_09_00.csv')])
            self.assertEqual(n, 1)

    def test_a_stale_record_of_a_cut_is_retired_by_that_cut_only(self):
        s3 = 'My Life with the Walter Boys Viewers on Netflix - Season 3 Viewers'
        rec = entry('s3', 'delivered_profile', [people('US audience', 725_813.0, 'Season 3 Viewers cut, projected to the US')],
                    q=f'Profile IQ profile: {s3}', reply=f'Profile IQ profile {s3}.csv',
                    subject='My Life with the Walter Boys Viewers')
        base_fact = fact(13_315_259, 'My_Life_with_the_Walter_Boys_Viewers_on_Netflix_11_10_2026_23_29.csv')
        cut_fact = fact(7_843_537, s3 + '.csv', cut=True)
        with Index([rec], subject='My Life with the Walter Boys Viewers') as ix:
            self.assertEqual(il.retire_conflicting_entries('My Life with the Walter Boys Viewers', [base_fact]), 0)
            self.assertEqual(il.retire_conflicting_entries('My Life with the Walter Boys Viewers', [base_fact, cut_fact]), 1)
            self.assertEqual(ix.retired('s3')['fact_source'], s3 + '.csv')

    def test_an_entry_that_names_the_cut_in_words(self):
        avid = entry('avid', 'reasoned_metrics', [people('Projected US audience', 934_970)], q='Who are the avid fans?',
                     cohort='Avid Fan')
        with Index([avid]) as ix:
            self.assertEqual(il.retire_conflicting_entries('Cruel Summer Viewers', [fact(2_573_979, BASE_KEY)]), 0)
            self.assertEqual(il.retire_conflicting_entries('Cruel Summer Viewers', [fact(501_151, AVID_KEY, cut=True)]), 1)


class APartIsNotTheWhole(unittest.TestCase):
    LC = 'Las_Culturistas_Culture_Awards_Viewers_11_10_2026_23_25.csv'

    def _demo(self, total, core):
        return entry('demo', 'reasoned_metrics',
                     [people('Projected US audience', total, 'US people who watched the special on Peacock in the window'),
                      people('Core 25-44 audience', core, 'viewers aged 25 to 44'),
                      people('Avid tier', 160_529, 'Avid Fan cut, projected to the US')],
                     base=self.LC, subject='Las Culturistas Culture Awards Viewers')

    def test_a_part_smaller_than_the_audience_is_no_conflict(self):
        with Index([self._demo(581_977, 330_197)], subject='Las Culturistas Culture Awards Viewers') as ix:
            self.assertEqual(il.retire_conflicting_entries('Las Culturistas Culture Awards Viewers', [fact(581_977, self.LC)]), 0)
            self.assertEqual(ix.live(), ['demo'])

    def test_a_part_larger_than_the_audience_is(self):
        # the read made before the profile was re-leveled: 2,380,031 aged 25 to 44 of a 581,977 audience
        stale = entry('demo', 'reasoned_metrics', [people('Core 25-44 audience', 2_380_031, 'viewers aged 25 to 44')],
                      base=self.LC, subject='Las Culturistas Culture Awards Viewers')
        with Index([stale], subject='Las Culturistas Culture Awards Viewers') as ix:
            self.assertEqual(il.retire_conflicting_entries('Las Culturistas Culture Awards Viewers', [fact(581_977, self.LC)]), 1)

    def test_a_total_that_agrees_with_one_figure_is_kept(self):
        e = entry('t', 'reasoned_metrics', [people('US audience', 581_977)], subject='Las Culturistas Culture Awards Viewers')
        facts = [fact(581_977, self.LC), fact(214_006, 'journey-iq/x/lc.json', product='journey', label='journey end point, US people')]
        with Index([e], subject='Las Culturistas Culture Awards Viewers') as ix:
            self.assertEqual(il.retire_conflicting_entries('Las Culturistas Culture Awards Viewers', facts), 0)

    def test_the_sample_is_not_the_audience(self):
        e = entry('s', 'delivered_profile', [people('People in file', 38_312.0, 'people in the file')],
                  q='Profile IQ profile: X', reply='Profile IQ profile X_10_29_2026_20_26.csv', subject='X')
        with Index([e], subject='X') as ix:
            self.assertEqual(il.retire_conflicting_entries('X', [fact(1_263_913, 'X_10_29_2026_20_26.csv')]), 0)


class TheOldRulesStillRead(unittest.TestCase):
    def test_another_window_is_never_compared(self):
        old = copy.deepcopy(READ)
        old['ws'], old['we'] = '2021-01-01', '2021-12-31'
        old['metrics'][0]['value'] = 3_809_982
        with Index([old]) as ix:
            self.assertEqual(il.retire_conflicting_entries('Cruel Summer Viewers', [fact(2_573_979, BASE_KEY)]), 0)

    def test_a_platform_figure_compares_with_a_platform_metric_only(self):
        e = entry('p', 'reasoned_metrics',
                  [people('US audience', 2_573_979), people('US viewers on Hulu', 9_000_000, 'viewers on hulu')], base=BASE_KEY)
        hulu = fact(1_800_442, BASE_KEY, kind='platform_count', label='US viewers on Hulu')
        with Index([e]) as ix:
            self.assertEqual(il.retire_conflicting_entries('Cruel Summer Viewers', [fact(2_573_979, BASE_KEY), hulu]), 1)
            self.assertEqual(ix.retired('p')['metric'], 'US viewers on Hulu')
        ok = entry('p', 'reasoned_metrics', [people('US audience', 2_573_979)], base=BASE_KEY)
        with Index([ok]) as ix:
            self.assertEqual(il.retire_conflicting_entries('Cruel Summer Viewers', [fact(2_573_979, BASE_KEY), hulu]), 0)

    def test_chat_facts_and_shares_are_ignored(self):
        with Index([DELIVERED]) as ix:
            self.assertEqual(il.retire_conflicting_entries('Cruel Summer Viewers', [fact(9, BASE_KEY, product='chat')]), 0)
            self.assertEqual(il.retire_conflicting_entries('', [fact(1, BASE_KEY)]), 0)

    def test_labels(self):
        self.assertEqual(il._cut_of(AVID_KEY), 'avid fan')
        self.assertEqual(il._cut_of(BASE_KEY), '')
        self.assertEqual(il._cut_of('P-Valley Viewers.csv'), '')
        self.assertEqual(il._file_label(BASE_KEY), 'cruel summer viewers')
        self.assertEqual(il._entry_file(DELIVERED), (BASE_KEY, ''))


if __name__ == '__main__':
    unittest.main(verbosity=1)
