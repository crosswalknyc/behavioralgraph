#!/usr/bin/env python3
"""Regression for measurement_anchors: unit conversion, budgets, audit,
and the dry-run relevel (anchored titles set, fat tails scaled down,
nothing scaled up, values stay distinct)."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from scripts.trends_scrapers import measurement_anchors as ma  # noqa: E402


def test_conversion_matches_hand_math():
    # 1,776M minutes, binge drama: x1.12 / 1.35 / 105 / 7
    v = ma.implied_daily_account_views(1776, 'bingedrama')
    assert abs(v - 1776e6 * 1.12 / 1.35 / 105 / 7) < 1e-6
    assert 1_900_000 < v < 2_100_000


def test_budget_scales_with_share():
    pv = ma.platform_budget('primevideo')
    nf = ma.platform_budget('netflix')
    assert pv and nf and nf > pv
    assert abs(nf / pv - 0.077 / 0.042) < 1e-9
    assert ma.platform_budget('no-such-platform') is None


def _snapshot():
    items = {}
    # an under-read hit on Prime that charts in the Oct 5-11 week
    items['tv:carrie'] = {'by_platform': {'primevideo': {'us_estimate': 300_000}}}
    # a fat tail on Paramount+ (400 titles from 50K to 650K, ~140M total
    # against a budget near 10.1M)
    for i in range(400):
        items[f'film:tail {i}'] = {'by_platform': {'paramountplus': {'us_estimate': 50_000 + i * 1_500}}}
    # a charting Paramount+ title inside that tail
    items['tv:spongebob squarepants'] = {'by_platform': {'paramountplus': {'us_estimate': 900_000}}}
    return items


def test_audit_reports_budget_and_anchor_ratio():
    rep = ma.audit_items(_snapshot(), '2026-10-08')
    pp = rep['platforms']['paramountplus']
    assert pp['sum_over_budget'] > 3.0
    carrie = next(t for t in rep['titles'] if t['title'] == 'carrie')
    assert carrie['ranker'] == 300_000 and 0.1 < carrie['ratio'] < 0.4


def test_relevel_sets_anchors_and_scales_tail_only():
    items = _snapshot()
    res = ma.relevel_items(items, '2026-10-08')
    ch = res['changes']
    # the hit is lifted to its own anchor, not by a platform factor
    assert ch['tv:carrie']['primevideo'] > 1_000_000
    # SpongeBob keeps an anchored level above the tail
    sb = ch['tv:spongebob squarepants']['paramountplus']
    assert sb > 1_000_000
    # the tail is scaled down to land exactly at 90% of the budget
    budget = ma.platform_budget('paramountplus')
    tail_new = [ch[f'film:tail {i}']['paramountplus'] for i in range(400)]
    assert all(tail_new[i] < 50_000 + i * 1_500 for i in range(400))
    assert abs(sb + sum(tail_new) - 0.9 * budget) < 1.0
    # the biggest stays the biggest, the smallest the smallest, values distinct
    assert max(tail_new) == tail_new[-1] and min(tail_new) == tail_new[0]
    assert len(set(round(v) for v in tail_new)) == len(tail_new)
    assert res['stats']['scaled_platforms'].get('paramountplus') < 1.0


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            fn(); print('ok', name)
