"""Tests for the daily US book-sales band (book_sales_level.py)."""
from datetime import date, timedelta
import unittest

from scripts.trends_scrapers import book_sales_level as bsl


def _key(t, a):
    return f'book:{t.lower()} {a.lower()}'.strip()


class ChartLevelTests(unittest.TestCase):
    def test_top_row_takes_its_share_of_the_pool(self):
        for pl, share in bsl.PLATFORM_SHARE.items():
            v = bsl.chart_levels(pl, ['a'], '2026-10-29')[0]
            want = bsl.platform_daily(pl, '2026-10-29') * bsl.TOP_TITLE_SHARE
            self.assertAlmostEqual(v / want, 1.0, delta=0.035)
            # and the day's pool sits in the band, never on the floor
            self.assertGreater(bsl.pool_daily('stores', '2026-10-29'), bsl.POOL_DAILY)

    def test_never_rises_never_ends_in_zero(self):
        v = bsl.chart_levels('apple', [f'k{i}' for i in range(100)], '2026-10-29')
        self.assertTrue(all(a >= b for a, b in zip(v, v[1:])))
        self.assertTrue(all(x % 10 for x in v if x > 1))
        self.assertGreater(v[-1], 5)

    def test_deterministic(self):
        keys = [f'k{i}' for i in range(60)]
        self.assertEqual(bsl.chart_levels('amazon', keys, '2026-10-29'),
                         bsl.chart_levels('amazon', keys, '2026-10-29'))


class LevelTests(unittest.TestCase):
    def test_sets_platform_block_and_aggregate(self):
        items = {'book:fourth wing rebecca yarros': {
            'kind': 'book', 'us_estimate': 50,
            'by_platform': {'audible': {'us_estimate': 50},
                            'libby_ebook': {'us_estimate': 900}}}}
        charts = {'sources': {
            'audible': {'items': [{'title': 'Fourth Wing', 'artist': 'Rebecca Yarros'}]},
            'apple': {'items': [{'title': 'New Book', 'artist': 'A Writer'}]}}}
        touched = bsl.level(items, charts, '2026-10-29', key_fn=_key)
        fw = items['book:fourth wing rebecca yarros']
        self.assertEqual(fw['by_platform']['libby_ebook']['us_estimate'], 900)
        self.assertGreater(fw['by_platform']['audible']['us_estimate'], 1000)
        self.assertEqual(fw['us_estimate'], 900 + fw['by_platform']['audible']['us_estimate'])
        self.assertIn('book:new book a writer', items)
        self.assertEqual(fw['by_platform']['audible']['est_basis'], 'sales_pool')
        self.assertEqual(len(touched), 2)


class OffChartTests(unittest.TestCase):
    def test_book_off_the_chart_sits_under_it(self):
        items = {'book:old hit someone': {'kind': 'book',
                                          'by_platform': {'apple': {'us_estimate': 2213}}},
                 'song:old hit someone': {'kind': 'song',
                                          'by_platform': {'apple': {'us_estimate': 90000}}}}
        charts = {'sources': {'apple': {'items': [
            {'title': f'T{i}', 'artist': 'A'} for i in range(100)]}}}
        bsl.level(items, charts, '2026-10-29', key_fn=_key)
        last = items['book:t99 a']['by_platform']['apple']['us_estimate']
        off = items['book:old hit someone']['by_platform']['apple']
        self.assertLessEqual(off['us_estimate'], last)
        self.assertEqual(off['est_basis'], 'sales_pool')
        self.assertEqual(items['song:old hit someone']['by_platform']['apple']['us_estimate'], 90000)


class LibbyTests(unittest.TestCase):
    def test_libby_pool_and_magazines_untouched(self):
        items = {'book:mag x': {'kind': 'book', 'by_platform': {'libby_magazine': {'us_estimate': 77}}}}
        libby = {'sources': {
            'ebook': {'items': [{'title': 'Whistler', 'artist': 'Ann Patchett'}]},
            'audiobook': {'items': [{'title': 'Whistler', 'artist': 'Ann Patchett'}]},
            'magazine': {'items': [{'title': 'Mag', 'artist': 'X'}]}}}
        bsl.level(items, libby, '2026-10-29', key_fn=_key, panels=bsl.LIBBY_PANELS)
        bp = items['book:whistler ann patchett']['by_platform']
        self.assertAlmostEqual(bp['libby_ebook']['us_estimate'] / (bsl.platform_daily('libby_ebook', '2026-10-29') * 0.0033), 1, delta=0.035)
        self.assertAlmostEqual(bp['libby_audio']['us_estimate'] / (bsl.platform_daily('libby_audio', '2026-10-29') * 0.0033), 1, delta=0.035)
        self.assertGreater(bsl.pool_daily('libby', '2026-10-29'), bsl.LIBBY_POOL_DAILY)
        self.assertEqual(items['book:mag x']['by_platform']['libby_magazine']['us_estimate'], 77)


class BandTests(unittest.TestCase):
    """Jenna 2026-10-30: never at or below the floor, never the same
    day to day, never a round number, always deterministic."""
    DAYS = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(730)]

    def test_never_at_or_below_floor_and_under_ceiling(self):
        for kind, floor, hi in (('stores', bsl.POOL_DAILY, 1.58),
                                ('libby', bsl.LIBBY_POOL_DAILY, 1.45)):
            vals = [bsl.pool_daily(kind, d) for d in self.DAYS]
            self.assertTrue(all(v > floor for v in vals), kind)
            self.assertTrue(all(v <= floor * hi for v in vals), kind)
            self.assertTrue(all(v % 10 for v in vals), kind)
            self.assertTrue(all(v % 1000 for v in vals), kind)

    def test_moves_every_day_and_is_not_flat(self):
        for kind in ('stores', 'libby'):
            vals = [bsl.pool_daily(kind, d) for d in self.DAYS]
            # no two neighbouring days agree, and a coincidental repeat
            # anywhere in two years is the rare exception, not a level
            self.assertTrue(all(a != b for a, b in zip(vals, vals[1:])), kind)
            self.assertGreater(len(set(vals)) / len(vals), 0.99, kind)
            lo, hi = min(vals), max(vals)
            self.assertGreater(hi / lo, 1.25, kind)

    def test_ordinary_day_sits_in_the_middle_of_the_band(self):
        s = [bsl.pool_daily('stores', d) for d in self.DAYS]
        l = [bsl.pool_daily('libby', d) for d in self.DAYS]
        self.assertTrue(3_250_000 < sum(s) / len(s) < 3_750_000)
        self.assertTrue(2_000_000 < sum(l) / len(l) < 2_300_000)

    def test_calendar_shape(self):
        cyber_monday = bsl._thanksgiving(2026) + timedelta(days=4)
        self.assertGreater(bsl.pool_daily('stores', cyber_monday.isoformat()),
                           bsl.pool_daily('stores', '2026-07-11'))
        self.assertGreater(bsl.pool_daily('libby', '2026-01-12'),
                           bsl.pool_daily('libby', '2026-09-11'))

    def test_deterministic_and_floor_without_day(self):
        self.assertEqual(bsl.pool_daily('stores', '2026-10-30'),
                         bsl.pool_daily('stores', '2026-10-30'))
        self.assertGreater(bsl.pool_daily('stores', None), bsl.POOL_DAILY)
        self.assertGreater(bsl.pool_daily('libby', 'garbage'), bsl.LIBBY_POOL_DAILY)

    def test_platform_shares_sum_to_the_pool(self):
        for day in ('2026-10-30', '2026-12-14'):
            stores = sum(bsl.platform_daily(p, day) for p in bsl.PLATFORM_SHARE)
            self.assertAlmostEqual(stores / bsl.pool_daily('stores', day), 1.0, places=6)
            libby = sum(bsl.platform_daily(p, day) for p in bsl.LIBBY_SHARE)
            self.assertAlmostEqual(libby / bsl.pool_daily('libby', day), 1.0, places=6)


if __name__ == '__main__':
    unittest.main()
