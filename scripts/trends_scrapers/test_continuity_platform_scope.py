"""Continuity guard when an item's platform set changes overnight.

Run: python3 scripts/trends_scrapers/test_continuity_platform_scope.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from scripts.trends_scrapers import stream_estimates as se  # noqa: E402

PREV_BP = {'amazon': 1771, 'apple': 186, 'audible': 290,
           'libby_ebook': 4296, 'libby_audio': 2162}


def _item(by_platform, total, prev_total=8412, prev_bp=PREV_BP, spec=''):
    return {
        'kind': 'book', 'display_title': 'House of Sky and Breath',
        'us_estimate': total, 'us_estimate_low': int(total * 0.8),
        'us_estimate_high': int(total * 1.2),
        'prev_day_estimate': prev_total, 'prev_day_date': '2026-10-26',
        'prev_day_by_platform': dict(prev_bp) if prev_bp else None,
        'day_specificity': spec,
        'by_platform': {k: {'us_estimate': v, 'us_estimate_low': int(v * 0.8),
                            'us_estimate_high': int(v * 1.2)}
                        for k, v in by_platform.items()},
    }


class PlatformScopeGuard(unittest.TestCase):
    def test_single_platform_today_is_not_read_as_a_collapse(self):
        it = _item({'audible': 127}, 127)
        se._apply_continuity_guard({'book:house sky breath sarah j maas': it}, '2026-10-27')
        self.assertEqual(it['by_platform']['audible']['us_estimate'], 127)
        self.assertEqual(it['us_estimate'], 127)
        self.assertNotIn('prev_day_by_platform', it)

    def test_shared_platform_still_guarded_against_its_own_prior(self):
        it = _item({'audible': 20}, 20)
        se._apply_continuity_guard({'book:x': it}, '2026-10-27')
        a = it['by_platform']['audible']['us_estimate']
        self.assertTrue(0.47 * 290 <= a <= 0.78 * 290, a)
        self.assertGreater(it['us_estimate'], 20)

    def test_new_platform_left_alone(self):
        it = _item({'goodreads': 9000, 'audible': 300}, 9300)
        se._apply_continuity_guard({'book:x': it}, '2026-10-27')
        self.assertEqual(it['by_platform']['goodreads']['us_estimate'], 9000)
        self.assertEqual(it['by_platform']['audible']['us_estimate'], 300)

    def test_event_cited_keeps_raw(self):
        it = _item({'audible': 20}, 20, spec='Audiobook release of the new edition today.')
        se._apply_continuity_guard({'book:x': it}, '2026-10-27')
        self.assertEqual(it['by_platform']['audible']['us_estimate'], 20)

    def test_same_platform_set_uses_item_level_guard(self):
        bp = {'audible': 100}
        it = _item({'audible': 10}, 10, prev_total=100, prev_bp=bp)
        se._apply_continuity_guard({'book:x': it}, '2026-10-27')
        self.assertTrue(47 <= it['us_estimate'] <= 78, it['us_estimate'])

    def test_prompt_line_lists_platforms(self):
        line = se._prev_platform_prompt_line(PREV_BP)
        self.assertIn('audible 290', line)
        self.assertEqual(se._prev_platform_prompt_line({}), '')

    def test_prev_levels_skip_empty(self):
        self.assertEqual(se._prev_platform_levels(
            {'by_platform': {'a': {'us_estimate': 5}, 'b': {'us_estimate': 0}, 'c': None}}),
            {'a': 5})


if __name__ == '__main__':
    unittest.main()
