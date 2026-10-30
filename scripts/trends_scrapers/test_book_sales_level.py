"""Tests for the 3M daily US book-sales level (book_sales_level.py)."""
import unittest

from scripts.trends_scrapers import book_sales_level as bsl


def _key(t, a):
    return f'book:{t.lower()} {a.lower()}'.strip()


class ChartLevelTests(unittest.TestCase):
    def test_top_row_takes_its_share_of_the_pool(self):
        for pl, share in bsl.PLATFORM_SHARE.items():
            v = bsl.chart_levels(pl, ['a'], '2026-10-29')[0]
            want = bsl.POOL_DAILY * share * bsl.TOP_TITLE_SHARE
            self.assertAlmostEqual(v / want, 1.0, delta=0.035)

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
        self.assertEqual(len(touched), 2)


if __name__ == '__main__':
    unittest.main()
