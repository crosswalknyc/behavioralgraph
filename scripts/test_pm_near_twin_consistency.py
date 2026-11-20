#!/usr/bin/env python3
"""Near-twin consistency (Jenna 2026-11-20): a new valuation on the same
pairing and a window near one already delivered stays inside a band of
it on every driver, the money recomputed from the held drivers; the
band lifts only for a dated event inside the part of the window that
changed; the same dated press pieces are counted in any run whose
window includes their dates; every held or lifted result writes a
staff record. Fixtures: the two cases that raised it (Anne Hathaway x
Shiseido, Leatherman x Matchbox), by their figures only. Hermetic."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('BPIQ_CORPUS_ANCHORS', '0')
os.environ.setdefault('PM_KNOWLEDGE_RESEARCH', '0')
for _k in ('PM_TWIN_CHECK', 'PM_TWIN_SAY_HELD', 'PM_TWIN_WINDOW_SHIFT_DAYS', 'PM_TWIN_WINDOW_OVERLAP',
           'PM_TWIN_BAND_VALUE', 'PM_TWIN_BAND_AUDIENCE', 'PM_TWIN_BAND_AOV', 'PM_TWIN_BAND_RATE_PP'):
    os.environ.pop(_k, None)

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from prometheus import consistency as X  # noqa: E402
from prometheus import pieces as P  # noqa: E402
from prometheus import knowledge as K  # noqa: E402
from prometheus import bpiq_deck as BD  # noqa: E402
from migration import bpiq_synthesis as S  # noqa: E402
from migration import bpiq_campaign as C  # noqa: E402
import test_bpiq_dedupe_and_research as T  # noqa: E402

FAIL = 0
BANNED = ('synth', 'modeled', 'modelled', 'estimated', 'pipeline', 'hostmap', 'claude', 'anthropic', 'researched',
          'calibrated', 'anchored', 'source', 'according to', 'nielsen', 'statista', 'pew ', 'band', 'twin', 'flag')


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def clean(text):
    low = str(text or '').lower()
    if '\u2014' in low or '\u2013' in low:
        return False
    return not any(w in low for w in BANNED)


def within(v, anchor, rel):
    try:
        return anchor > 0 and abs(float(v) / float(anchor) - 1.0) <= rel + 1e-9
    except (TypeError, ValueError, ZeroDivisionError):
        return False


# ---------------------------------------------------------------- fixtures
# The first valuation delivered on Anne Hathaway x Shiseido (September 1
# to 8, 2026), as its drivers and figures.
ANNE_FIRST = {
    'project_name': 'Anne Hathaway x Shiseido', 'qualifier_value': ['Anne Hathaway'], 'brand_partner': 'Shiseido',
    'start_date': '2026-09-01', 'end_date': '2026-09-08', 'created_at': '2026-11-20T03:21:23Z',
    'projected_audience_size': 92_615_994, 'audience_size': 375_429,
    'totals': {'pre_users_projected': 3_160_641, 'post_users_projected': 5_494_617,
               'audience_pen_pre_pct': 3.41, 'audience_pen_post_pct': 5.93},
    'conversions': {'enabled': True, 'pre_users_projected': 35_771, 'post_users_projected': 41_445},
    'control_group': {'enabled': True, 'incremental_lift_pp': 2.26, 'control_delta_pp': 0.26, 'background_drift_pp': 0.26},
    'campaign': {'audience_basis': 'talent audience in the window', 'aov_usd': 263.6},
    'valuation': {'total_brand_value': 102_145_967.0, 'brand_engagement_value': 13_736_542.5,
                  'earned_media_value': 67_024_697.5, 'brand_lift_value': 10_459_825.0,
                  'conversion_value': 10_924_902.0, 'incremental_users': 2_091_965,
                  'attributable_to_partnership': 17_700_375.89, 'rates': {'conv_value_per_user': 263.6}},
}

# The second (the whole ambassadorship, September 2023 to September 2026)
# and third (August 27 to September 8, 2026) valuations delivered on the
# pairing, on the designed basis: the people the campaign reached at the
# house projection.
ANNE_SECOND = {
    'project_name': 'Anne Hathaway x Shiseido', 'qualifier_value': ['Anne Hathaway'], 'brand_partner': 'Shiseido',
    'start_date': '2023-09-06', 'end_date': '2026-09-08', 'created_at': '2026-11-20T06:11:12Z',
    'projected_audience_size': 340_475, 'audience_size': 10_321,
    'totals': {'pre_users_projected': 100_912, 'post_users_projected': 162_865, 'audience_pen_pre_pct': 29.64, 'audience_pen_post_pct': 47.84},
    'conversions': {'enabled': True, 'pre_users_projected': 10_853, 'post_users_projected': 13_888},
    'control_group': {'enabled': True, 'incremental_lift_pp': 16.77, 'control_delta_pp': 1.42, 'background_drift_pp': 1.42},
    'campaign': {'audience_basis': 'campaign reach', 'aov_usd': 227.56, 'reach': {'reach_us': 340_475, 'press_pieces': 15, 'events': 1}},
    'valuation': {'total_brand_value': 6_977_878.28, 'brand_engagement_value': 407_162.5, 'earned_media_value': 3_124_847.5,
                  'brand_lift_value': 285_515.0, 'conversion_value': 3_160_353.28, 'incremental_users': 57_103,
                  'attributable_to_partnership': 2_073_609.62, 'rates': {'conv_value_per_user': 227.56}},
}
ANNE_THIRD = {
    'project_name': 'Anne Hathaway x Shiseido', 'qualifier_value': ['Anne Hathaway'], 'brand_partner': 'Shiseido',
    'start_date': '2026-08-27', 'end_date': '2026-09-08', 'created_at': '2026-11-20T06:37:44Z',
    'projected_audience_size': 53_727, 'audience_size': 1_629,
    'totals': {'pre_users_projected': 12_467, 'post_users_projected': 53_166, 'audience_pen_pre_pct': 23.2, 'audience_pen_post_pct': 98.96},
    'conversions': {'enabled': True, 'pre_users_projected': 2_342, 'post_users_projected': 3_694},
    'control_group': {'enabled': True, 'incremental_lift_pp': 74.41, 'control_delta_pp': 1.35, 'background_drift_pp': 1.35},
    'campaign': {'audience_basis': 'campaign reach', 'aov_usd': 218.1, 'reach': {'reach_us': 53_727, 'press_pieces': 1, 'events': 0}},
    'valuation': {'total_brand_value': 1_994_140.4, 'brand_engagement_value': 132_915.0, 'earned_media_value': 855_699.0,
                  'brand_lift_value': 199_865.0, 'conversion_value': 805_661.4, 'incremental_users': 39_973,
                  'attributable_to_partnership': 1_005_526.4, 'rates': {'conv_value_per_user': 218.1}},
}

# The first valuation delivered on Leatherman x Matchbox (September 29 to
# November 13, 2026): five press pieces, 173,284 people reached.
LEATHERMAN_FIRST = {
    'project_name': 'Matchbox the Film on Apple TV x Leatherman', 'qualifier_value': ['Matchbox the Film on Apple TV'],
    'brand_partner': 'Leatherman', 'start_date': '2026-09-29', 'end_date': '2026-11-13', 'created_at': '2026-11-13T00:54:58Z',
    'projected_audience_size': 173_284, 'audience_size': 5_253,
    'totals': {'pre_users_projected': 37_507, 'post_users_projected': 64_854, 'audience_pen_pre_pct': 21.64, 'audience_pen_post_pct': 37.43},
    'conversions': {'enabled': True, 'pre_users_projected': 2_045, 'post_users_projected': 7_826, 'basis': "the drop's orders"},
    'control_group': {'enabled': True, 'incremental_lift_pp': 15.18, 'control_delta_pp': 0.61, 'background_drift_pp': 0.6137},
    'campaign': {'audience_basis': 'campaign reach', 'aov_usd': 229.11,
                 'reach': {'reach_us': 173_284, 'press_people': 173_284, 'press_pieces': 5, 'events': 0}},
    'valuation': {'total_brand_value': 3_246_461.86, 'brand_engagement_value': 162_135.0, 'earned_media_value': 1_159_857.0,
                  'brand_lift_value': 131_455.0, 'conversion_value': 1_793_014.86, 'incremental_users': 26_291,
                  'attributable_to_partnership': 1_389_216.81, 'rates': {'conv_value_per_user': 229.11}},
}
LEATHERMAN_PIECES_FIRST = [
    {'outlet': 'Apple TV Press', 'title': 'Matchbox The Movie', 'url': 'https://www.apple.com/tv-pr/originals/matchbox-the-movie/', 'date': '2026-10-09'},
    {'outlet': 'Common Sense Media', 'title': "Matchbox the Movie - Parents' Guide", 'url': 'https://www.commonsensemedia.org/movie-reviews/matchbox-the-movie', 'date': '2026-10-08'},
    {'outlet': 'RogerEbert.com', 'title': 'Matchbox: The Movie review', 'url': 'https://www.rogerebert.com/reviews/matchbox-the-movie', 'date': '2026-10-11'},
    {'outlet': 'T3', 'title': 'Forget Rolex and Omega', 'url': 'https://www.t3.com/active/outdoors/leatherman-surge-matchbox-the-movie', 'date': '2026-10-14'},
    {'outlet': 'Yahoo Entertainment (T3 syndication)', 'title': 'Forget Rolex and Omega', 'url': 'https://www.yahoo.com/entertainment/movies/articles/forget-rolex-omega', 'date': '2026-10-14'},
]
LEATHERMAN_PIECES_SECOND = [LEATHERMAN_PIECES_FIRST[3], LEATHERMAN_PIECES_FIRST[4]]


def anne_inputs(start='2026-09-01', end='2026-09-08'):
    return {'qualifier': 'Anne Hathaway', 'brand_partner': 'Shiseido', 'campaign': 'Shiseido',
            'event_start': start, 'event_end': end, 'post_start': start, 'post_end': '2026-10-08',
            'control_group': True, 'include_control': True, 'links': []}


def anne_prim():
    # a new September 1 to 8 order read far above the third run: the first
    # run's own shares on 375,429 sample people at the house projection
    p = T.prim(seed=3)
    p.update({'audience_size': 375_429, 'projected_audience_size': 12_385_403, 'category': 'BEAUTY',
              'totals': {'pre_pen_pct': 3.41, 'post_pen_pct': 5.93},
              'conversions': {'pre_users_pct': 0.0386, 'post_users_pct': 0.0447},
              'gen_pop_drift_pp': 0.26})
    return p


def anne_campaign():
    c = T.campaign(sold_out=False, prices=True, inventory='large')
    c.update({'campaign_name': 'Shiseido Vital Perfection with Anne Hathaway', 'brand': 'Shiseido', 'talent': 'Anne Hathaway',
              'drop_dates': ['2026-01-08', '2026-09-14'], 'category': 'BEAUTY', 'collection_size': 15,
              'price_band': {'low_usd': 115.0, 'high_usd': 175.0},
              'products': [{'name': 'Intensive SculptDefine Serum', 'type': 'serum', 'price_usd': 175.0, 'hero': True},
                           {'name': 'Vital Perfection Cream', 'type': 'cream', 'price_usd': 135.0, 'hero': False},
                           {'name': 'Vital Perfection Eye Cream', 'type': 'eye', 'price_usd': 115.0, 'hero': False}],
              # the campaign's own footprint: its pages and coverage, so the audience is the people it reached
              'footprint': C.normalize_footprint({
                  'press': [{'outlet': 'Beauty Packaging', 'title': 'Shiseido launches', 'url': 'https://www.beautypackaging.com/x', 'date': '2026-09-03'},
                            {'outlet': 'Marie Claire', 'title': 'wear test', 'url': 'https://www.marieclaire.com/y', 'date': '2026-09-24'}],
                  'press_pieces_total': 2,
                  'site': {'brand_site_us_monthly_uniques': 9_800_000, 'collection_page_share_pct': 6.4}})})
    return c


def leatherman_inputs():
    return {'qualifier': 'Matchbox', 'brand_partner': 'Leatherman', 'campaign': 'Leatherman',
            'event_start': '2026-10-09', 'event_end': '2026-11-19', 'post_start': '2026-10-09', 'post_end': '2026-11-19',
            'control_group': True, 'include_control': True, 'links': []}


def leatherman_prim():
    p = T.prim(seed=5)
    p.update({'audience_size': 674, 'projected_audience_size': 22_234, 'category': 'SPORTS',
              'totals': {'pre_pen_pct': 31.4511, 'post_pen_pct': 47.1813},
              'conversions': {'pre_users_pct': 2.3744, 'post_users_pct': 9.6207},
              'gen_pop_drift_pp': 1.3874})
    return p


def leatherman_campaign(pieces):
    c = T.campaign(sold_out=False, prices=True, inventory='large')
    c.update({'campaign_name': 'Leatherman', 'brand': 'Leatherman', 'talent': 'Matchbox',
              'drop_dates': ['2026-07-25', '2026-10-05', '2026-10-09'], 'category': 'SPORTS', 'collection_size': 1,
              'price_band': {'low_usd': 159.95, 'high_usd': 159.95},
              'products': [{'name': 'Leatherman Surge', 'type': 'multi-tool', 'price_usd': 159.95, 'hero': True}],
              'footprint': C.normalize_footprint({'press': [dict(p) for p in pieces], 'press_pieces_total': len(pieces)})})
    return c


# ------------------------------------------------------------------- tests
def test_thresholds_and_switches():
    print('thresholds')
    t = X.thresholds()
    check('defaults: 14-day shift, 80% overlap, 25% value, 25% audience, 25% order value, 3 points on a rate',
          t['PM_TWIN_WINDOW_SHIFT_DAYS'] == 14 and t['PM_TWIN_WINDOW_OVERLAP'] == 0.8 and t['PM_TWIN_BAND_VALUE'] == 0.25
          and t['PM_TWIN_BAND_AUDIENCE'] == 0.25 and t['PM_TWIN_BAND_AOV'] == 0.25 and t['PM_TWIN_BAND_RATE_PP'] == 3.0)
    os.environ['PM_TWIN_WINDOW_SHIFT_DAYS'] = '3'
    near, _ = X.windows_near({'start': '2026-09-01', 'end': '2026-09-08'}, {'start': '2026-08-27', 'end': '2026-09-08'})
    check('the shift threshold is read from env (5 days is not near under a 3-day limit)', not near)
    os.environ.pop('PM_TWIN_WINDOW_SHIFT_DAYS', None)
    os.environ['PM_TWIN_BAND_VALUE'] = '0.10'
    check('the value band is read from env', X.band('value') == 0.10)
    os.environ.pop('PM_TWIN_BAND_VALUE', None)
    check('the held line stays internal by default', not X.say_held_to_reader())
    os.environ['PM_TWIN_SAY_HELD'] = 'client'
    check('PM_TWIN_SAY_HELD=client ships it', X.say_held_to_reader())
    os.environ.pop('PM_TWIN_SAY_HELD', None)
    os.environ['PM_TWIN_CHECK'] = '0'
    check('PM_TWIN_CHECK=0 turns the check off', not X.enabled() and X.near_twin({'talent': 'a', 'brand': 'b', 'window': {}}, []) is None)
    os.environ.pop('PM_TWIN_CHECK', None)


def test_names_and_windows():
    print('names and windows')
    check("a title with a descriptive tail is the same property ('Matchbox the Film on Apple TV' is 'Matchbox')",
          X.same_name('Matchbox the Film on Apple TV', 'Matchbox') and X.same_name('Matchbox (The AppleTV movie)', 'Matchbox'))
    check('a different title is not', not X.same_name('Matchbox', 'Hot Wheels'))
    check('the pairing matches in either order', X.same_pairing('Leatherman', 'Matchbox', 'Matchbox the Film on Apple TV', 'Leatherman'))
    check('a different brand on the same talent is not the pairing', not X.same_pairing('Anne Hathaway', 'Shiseido', 'Anne Hathaway', 'Bulgari'))
    near, why = X.windows_near({'start': '2026-09-01', 'end': '2026-09-08'}, {'start': '2026-08-27', 'end': '2026-09-08'})
    check(f'Anne: the start moved five days, near ({why})', near)
    near, why = X.windows_near({'start': '2026-09-29', 'end': '2026-11-13'}, {'start': '2026-10-09', 'end': '2026-11-19'})
    check(f'Leatherman: start moved 10 days, end moved 6, near ({why})', near)
    near, why = X.windows_near({'start': '2026-09-01', 'end': '2026-09-08'}, {'start': '2023-09-06', 'end': '2026-09-08'})
    check(f'a three-year window is not a twin of an eight-day one ({why})', not near)
    near, why = X.windows_near({'start': '2026-01-01', 'end': '2026-03-31'}, {'start': '2026-01-05', 'end': '2026-04-15'})
    check(f'windows sharing 86 of the 105 days either covers are near by the overlap rule, though the end moved 15 days ({why})', near)
    near, why = X.windows_near({'start': '2026-01-01', 'end': '2026-03-31'}, {'start': '2026-01-10', 'end': '2026-04-20'})
    check(f'81 of 110 days is not ({why})', not near)
    ch = X.changed_windows({'start': '2026-09-29', 'end': '2026-11-13'}, {'start': '2026-10-09', 'end': '2026-11-19'})
    check('the changed parts: September 29 to October 8 removed, November 14 to 19 added',
          ch == [{'start': '2026-09-29', 'end': '2026-10-08', 'change': 'removed'},
                 {'start': '2026-11-14', 'end': '2026-11-19', 'change': 'added'}])
    ch = X.changed_windows({'start': '2026-09-01', 'end': '2026-09-08'}, {'start': '2026-08-27', 'end': '2026-09-08'})
    check('Anne: August 27 to 31 added', ch == [{'start': '2026-08-27', 'end': '2026-08-31', 'change': 'added'}])
    check('the same window has no changed part', X.changed_windows({'start': '2026-09-01', 'end': '2026-09-08'}, {'start': '2026-09-01', 'end': '2026-09-08'}) == [])


def test_band_math():
    print('the band')
    v, moved = X.held_value(53_727, 92_615_994, 'audience', 'salt', 'reach')
    lo = 92_615_994 * 0.75
    check('a fresh read far below lands on the low side of the band, inside its edge (anchor low)',
          moved and lo < v < 92_615_994 * 0.85)
    v2, moved2 = X.held_value(200_000_000, 92_615_994, 'audience', 'salt', 'reach')
    check('a fresh read far above lands inside the top of the band, nearer the anchor',
          moved2 and 92_615_994 * 1.05 < v2 < 92_615_994 * 1.25)
    v3, moved3 = X.held_value(80_000_000, 92_615_994, 'audience', 'salt', 'reach')
    check('a fresh read inside the band is left alone', not moved3 and v3 == 80_000_000)
    r, m = X.held_value(47.18, 37.43, 'rate', 'salt', 'engaged')
    check('a rate is held in points (47.18 against 37.43 lands inside 34.43 to 40.43)', m and 37.43 < r < 40.43)
    r2, m2 = X.held_value(6.8757, 0.0447, 'rate', 'salt', 'order')
    check('a tiny rate is held inside the value band of its own level, never 3 points wide (6.88% against 0.045%)',
          m2 and 0.0447 < r2 < 0.0447 + 0.025)
    r3, m3 = X.held_value(15.0, 15.18, 'rate', 'salt', 'lift')
    check('a lift inside the band is left alone', not m3)
    a, b = X.held_value(10.0, 100.0, 'value', 'salt-a', 'x')[0], X.held_value(10.0, 100.0, 'value', 'salt-b', 'x')[0]
    check('two held runs do not land on the same number (salted inset)', a != b and 75.0 < a < 85.0 and 75.0 < b < 85.0)


def test_anchor_shapes():
    print('anchors')
    a = X.anchor_from_payload(ANNE_FIRST, ref='Anne_Hathaway_x_Shiseido_11_20_2026_04_21.json')
    d = a['drivers']
    check('the anchor carries the drivers: people reached, engaged shares, order share, order value, drift',
          d['audience_reached'] == 92_615_994 and d['engaged_post_pct'] == 5.93 and d['engaged_pre_pct'] == 3.41
          and abs(d['order_share_pct'] - 0.045) < 0.001 and d['aov_usd'] == 263.6 and d['drift_pp'] == 0.26)
    check('and the figures: the total and its four parts', a['figures']['total_usd'] == 102_145_967.0
          and a['figures']['conversion_usd'] == 10_924_902.0 and a['figures']['incremental_people'] == 2_091_965)
    check("the September 1 run is not an anchor: it valued the talent's whole audience at 246.69 people per sample person",
          a['eligible'] is False and '246.69' in a['ineligible_why'])
    check('the third run, on the people the campaign reached at the house ratio, is', X.anchor_from_payload(ANNE_THIRD, ref='third')['eligible'] is True)
    check('and so is the whole-ambassadorship run', X.anchor_from_payload(ANNE_SECOND, ref='second')['eligible'] is True)
    off = X.anchor_from_payload(dict(ANNE_THIRD, campaign={'audience_basis': 'talent audience in the window'}), ref='x')
    check("a file that says it valued the talent's own audience is not an anchor whatever its ratio", off['eligible'] is False)
    b = X.anchor_from_payload(LEATHERMAN_FIRST, ref='old')
    check('pieces ride the drivers when the campaign block carries them', b['drivers'].get('pieces') == 5
          and abs(b['drivers']['order_share_pct'] - 4.516) < 0.01)
    e = X.anchor_from_entry({'k': 'k1', 'ts': '2026-11-01T10:00:00', 'ws': '2026-09-01', 'we': '2026-09-30', 'fam': 'audience',
                             'metrics': [{'name': 'us_viewers', 'label': 'US viewers', 'value': 4_100_000, 'unit': 'people'}]}, subject='A Title')
    check('a banked read is an anchor with its metrics', e and e['metrics']['us_viewers']['value'] == 4_100_000 and e['window']['end'] == '2026-09-30')


def test_near_twin_picks_first():
    print('near twin')
    first = X.anchor_from_payload(ANNE_FIRST, ref='first')
    second = X.anchor_from_payload(ANNE_SECOND, ref='second')
    third = X.anchor_from_payload(ANNE_THIRD, ref='third')
    ask = {'talent': 'Anne Hathaway', 'brand': 'Shiseido', 'product': 'valuation', 'window': {'start': '2026-09-01', 'end': '2026-09-08'}}
    hit = X.near_twin(ask, [third, second, first])
    check('a new September 1 to 8 run anchors to the August 27 run (start moved five days): the first run is passed over',
          hit and hit['ref'] == 'third')
    check('the first run alone anchors nothing', X.near_twin(ask, [first]) is None)
    later = X.anchor_from_payload(dict(ANNE_THIRD, created_at='2026-11-21T00:00:00Z', projected_audience_size=61_003, audience_size=1_849), ref='later')
    check('among eligible twins the earliest delivered wins (first-held)', X.near_twin(ask, [later, third])['ref'] == 'third')
    check('the three-year window is not a twin', X.near_twin(dict(ask, window={'start': '2023-09-06', 'end': '2026-09-08'}), [third]) is None)
    check('another brand on the same talent is not a twin', X.near_twin(dict(ask, brand='Bulgari'), [third]) is None)
    cap = X.containing_cap(ask, [third, second, first])
    check('the whole ambassadorship caps any slice of it: $6,977,878.28 on 340,475 people',
          cap and cap['ref'] == 'second' and cap['total_usd'] == 6_977_878.28 and cap['people'] == 340_475)
    check('a near twin is not a cap', X.containing_cap(ask, [third]) is None)
    lea = X.anchor_from_payload(LEATHERMAN_FIRST, ref='old')
    hit2 = X.near_twin({'talent': 'Matchbox', 'brand': 'Leatherman', 'product': 'valuation',
                        'window': {'start': '2026-10-09', 'end': '2026-11-19'}}, [lea])
    check("Leatherman: 'Matchbox' finds 'Matchbox the Film on Apple TV x Leatherman'", hit2 and hit2['ref'] == 'old')


def test_candidates_from_the_valuation_list():
    print('the valuation list')
    X.META_FOR_TESTS = {
        'Matchbox_the_Film_on_Apple_TV_x_Leatherman_11_13_2026_00_54.json': {'display_name': 'Matchbox the Film on Apple TV x Leatherman'},
        'Anne_Hathaway_x_Shiseido_11_20_2026_04_21.json': {'display_name': 'Anne Hathaway x Shiseido'},
        'Coach_x_WNBA_11_17_2026_09_21.json': {'display_name': 'Coach x WNBA'},
        '_backups/Anne_Hathaway_x_Shiseido_old.json': {'display_name': 'Anne Hathaway x Shiseido'},
    }
    reads = []

    def _loader(key):
        reads.append(key)
        return {'Matchbox_the_Film_on_Apple_TV_x_Leatherman_11_13_2026_00_54.json': LEATHERMAN_FIRST,
                'Anne_Hathaway_x_Shiseido_11_20_2026_04_21.json': ANNE_FIRST}.get(key)
    X.LOADER_FOR_TESTS = _loader
    try:
        got = X.valuation_candidates('Matchbox', 'Leatherman')
        check('only the files whose names match the pairing are read', reads == ['Matchbox_the_Film_on_Apple_TV_x_Leatherman_11_13_2026_00_54.json'])
        check('and come back as anchors', len(got) == 1 and got[0]['drivers']['audience_reached'] == 173_284)
        got2 = X.valuation_candidates('Shiseido', 'Anne Hathaway')
        check('either order of the names; a backup copy is never a candidate', len(got2) == 1 and got2[0]['ref'] == 'Anne_Hathaway_x_Shiseido_11_20_2026_04_21.json')
    finally:
        X.META_FOR_TESTS = None
        X.LOADER_FOR_TESTS = None


def test_anne_is_held():
    print('Anne Hathaway x Shiseido: a new September 1 run is held near the August 27 run')
    X.RECORDS_FOR_TESTS = X.MemoryRecords()
    inputs, prim, camp = anne_inputs(), anne_prim(), anne_campaign()
    fresh = S.build_payload(inputs, prim, campaign=camp)
    fd, ff = X.drivers_of(fresh), X.figures_of(fresh)
    check(f"the fresh run reads {fd['audience_reached']:,} people and ${ff['total_usd']:,.0f} on the talent's windowed audience "
          f"at the house ratio: far above the third run", fd['audience_reached'] > 150_000 and ff['total_usd'] > 4_000_000
          and abs(fresh['projected_audience_size'] / fresh['audience_size'] - 32.99) < 0.5)
    cands = [X.anchor_from_payload(ANNE_FIRST, ref='first'), X.anchor_from_payload(ANNE_SECOND, ref='second'),
             X.anchor_from_payload(ANNE_THIRD, ref='third')]
    anchor = cands[2]
    research_calls = []
    held, rec = X.apply_valuation(
        fresh, inputs, campaign=camp, candidates=cands,
        rebuild=lambda plan: S._rebuild_held(inputs, prim, camp, plan),
        research=lambda changed: (research_calls.append(changed) or []), salt='anne-test')
    check('verdict: held to the August 27 run (no dated event in August 27 to 31)', rec.get('verdict') == 'held'
          and rec['anchor']['ref'] == 'third')
    check('the first run was passed over and the record says why', any(p['ref'] == 'first' for p in rec.get('passed_over') or []))
    check('the research step was asked once about the changed part of the window',
          research_calls == [[{'start': '2026-08-27', 'end': '2026-08-31', 'change': 'removed'}]])
    hd, hf, ad, af = X.drivers_of(held), X.figures_of(held), anchor['drivers'], anchor['figures']
    check(f"people reached held inside 25% of the third run ({hd['audience_reached']:,} against {ad['audience_reached']:,})",
          within(hd['audience_reached'], ad['audience_reached'], 0.25))
    check(f"total inside 25% of the third run (${hf['total_usd']:,.0f} against ${af['total_usd']:,.0f}), never near $102M",
          within(hf['total_usd'], af['total_usd'], 0.25))
    for k in ('engagement_usd', 'earned_usd', 'lift_usd', 'conversion_usd'):
        check(f"  {k} inside 25% (${hf[k]:,.0f} against ${af[k]:,.0f})", within(hf[k], af[k], 0.25))
    parts = hf['engagement_usd'] + hf['earned_usd'] + hf['lift_usd'] + hf['conversion_usd']
    check('the four parts still add to the total (the money was recomputed, never edited alone)', abs(parts - hf['total_usd']) < 1.0)
    check('people counts stay messy and do not sit on the band edge',
          hd['audience_reached'] % 10 != 0 and hd['audience_reached'] not in (int(ad['audience_reached'] * 0.75), int(ad['audience_reached'] * 1.25)))
    check('the whole-ambassadorship cap was checked and did not bind', (rec.get('cap') or {}).get('ref') == 'second'
          and (rec.get('cap') or {}).get('applied') is False)
    check('the held line stays internal by default: nothing on the campaign block, the record carries it',
          not (held.get('campaign') or {}).get('consistency_line') and rec.get('line') == X.HELD_LINE and rec.get('line_shipped') is False)
    diag = (held.get('diagnostics') or {}).get('consistency') or {}
    check('the payload carries the record summary under diagnostics (staff only)',
          diag.get('verdict') == 'held' and diag.get('anchor', {}).get('ref') == 'third' and diag.get('drivers_before') == fd)
    stored = X.RECORDS_FOR_TESTS.load(f"{X.RECORD_PREFIX}{rec['id']}.json")
    check('the record is written under system/pm_consistency with drivers before and after',
          stored and stored['verdict'] == 'held' and stored['drivers_before']['audience_reached'] == fd['audience_reached']
          and stored['drivers_after']['audience_reached'] == hd['audience_reached'] and stored['bands']['value'] == 0.25)
    found = X.lookup('Anne Hathaway', 'Shiseido')
    check('a staff lookup on the pairing answers from the record', found and found[0]['id'] == rec['id'])
    check('the lines carry no banned word and no dash', clean(X.HELD_LINE))
    os.environ['PM_TWIN_SAY_HELD'] = 'client'
    try:
        held2, rec2 = X.apply_valuation(fresh, inputs, campaign=camp, candidates=cands,
                                        rebuild=lambda plan: S._rebuild_held(inputs, prim, camp, plan),
                                        research=lambda changed: [], salt='anne-test')
        check('with the switch set to client the held line rides the campaign block',
              (held2.get('campaign') or {}).get('consistency_line') == X.HELD_LINE and rec2.get('line_shipped') is True)
        note = BD.note(held2, None, None)
        check('and the ready note carries it', X.HELD_LINE in note and 'total brand value' in note)
    finally:
        os.environ.pop('PM_TWIN_SAY_HELD', None)
    # the first run alone: nothing to anchor to, nothing happens
    out3, rec3 = X.apply_valuation(fresh, inputs, campaign=camp, candidates=[cands[0]],
                                   rebuild=lambda plan: S._rebuild_held(inputs, prim, camp, plan), research=lambda c: [], salt='anne-test')
    check("the first run on its own anchors nothing (it valued the talent's whole audience)", rec3 == {'verdict': 'no_twin'} and out3 is fresh)
    # only the whole ambassadorship on file: the slice is held under it
    out4, rec4 = X.apply_valuation(fresh, inputs, campaign=camp, candidates=[cands[1]],
                                   rebuild=lambda plan: S._rebuild_held(inputs, prim, camp, plan), research=lambda c: [], salt='anne-test')
    f4, d4 = X.figures_of(out4), X.drivers_of(out4)
    check(f"with only the whole deal on file the eight-day slice is held under it (${f4['total_usd']:,.0f} under $6,977,878; "
          f"{d4['audience_reached']:,} under 340,475)", rec4.get('verdict') == 'no_twin+capped' and f4['total_usd'] <= 6_977_878.28
          and d4['audience_reached'] <= 340_475 and (rec4.get('cap') or {}).get('applied') is True)
    X.RECORDS_FOR_TESTS = None


def test_anne_same_result_twice():
    print('Anne: the hold repeats')
    X.RECORDS_FOR_TESTS = X.MemoryRecords()
    inputs, prim, camp = anne_inputs(), anne_prim(), anne_campaign()
    anchor = X.anchor_from_payload(ANNE_THIRD, ref='third')
    outs = []
    for _ in range(2):
        fresh = S.build_payload(inputs, prim, campaign=camp)
        held, _rec = X.apply_valuation(fresh, inputs, campaign=camp, candidates=[anchor],
                                       rebuild=lambda plan: S._rebuild_held(inputs, prim, camp, plan),
                                       research=lambda changed: [], salt='anne-test')
        outs.append((X.drivers_of(held)['audience_reached'], X.figures_of(held)['total_usd']))
    check('the same ask held twice lands on the same figures', outs[0] == outs[1])
    X.RECORDS_FOR_TESTS = None


def test_leatherman_pieces_by_date():
    print('Leatherman x Matchbox: pieces by date')
    store = P.MemoryStore()
    n = P.remember(('Matchbox the Film on Apple TV', 'Leatherman'), LEATHERMAN_PIECES_FIRST, store)
    check('the first run leaves five dated pieces in the registry', n == 5)
    fp = C.normalize_footprint({'press': LEATHERMAN_PIECES_SECOND, 'press_pieces_total': 2})
    merged, note = P.merge_footprint(fp, ('Matchbox', 'Leatherman'), {'start': '2026-10-09', 'end': '2026-11-19'}, store)
    dates = sorted(p['date'] for p in merged['press'])
    check(f'the second run counts the pieces dated inside its window: {dates}',
          dates == ['2026-10-09', '2026-10-11', '2026-10-14', '2026-10-14'] and note['added_dates'] == ['2026-10-09', '2026-10-11'])
    check('the October 8 piece, outside the window, is not counted', '2026-10-08' not in dates)
    check('the two October 14 pieces are not doubled (deduplicated on the url)', len(merged['press']) == 4 and merged['press_pieces_total'] == 4)
    again, note2 = P.merge_footprint(merged, ('Matchbox', 'Leatherman'), {'start': '2026-10-09', 'end': '2026-11-19'}, store)
    check('a third run finds nothing new to add (idempotent)', note2 == {} and len(again['press']) == 4)
    reach_two = C.derive_reach(fp, {}, 'lea', 42)
    reach_four = C.derive_reach(merged, {}, 'lea', 42)
    check(f"more pieces, more people reached through press ({reach_two['press_pieces']} pieces {reach_two['press_people']:,} -> "
          f"{reach_four['press_pieces']} pieces {reach_four['press_people']:,})",
          reach_four['press_pieces'] == 4 and reach_four['press_people'] > reach_two['press_people'])
    os.environ['PM_PIECES_REGISTRY'] = '0'
    try:
        same, n3 = P.merge_footprint(fp, ('Matchbox', 'Leatherman'), {'start': '2026-10-09', 'end': '2026-11-19'}, store)
        check('the registry has a kill switch', n3 == {} and same is fp)
    finally:
        os.environ.pop('PM_PIECES_REGISTRY', None)


def test_leatherman_is_lifted_by_the_premiere():
    print('Leatherman x Matchbox: lifted')
    X.RECORDS_FOR_TESTS = X.MemoryRecords()
    store = P.MemoryStore()
    P.remember(('Matchbox the Film on Apple TV', 'Leatherman'), LEATHERMAN_PIECES_FIRST, store)
    inputs, prim = leatherman_inputs(), leatherman_prim()
    camp = leatherman_campaign(LEATHERMAN_PIECES_SECOND)
    camp['footprint'], _n = P.merge_footprint(camp['footprint'], (inputs['qualifier'], inputs['brand_partner']),
                                              {'start': inputs['event_start'], 'end': inputs['event_end']}, store)
    fresh = S.build_payload(inputs, prim, campaign=camp)
    anchor = X.anchor_from_payload(LEATHERMAN_FIRST, ref='old')
    asked = []
    out, rec = X.apply_valuation(fresh, inputs, campaign=camp, candidates=[anchor],
                                 rebuild=lambda plan: S._rebuild_held(inputs, prim, camp, plan),
                                 research=lambda changed: (asked.append(changed) or []), salt='lea-test')
    check('verdict: lifted (the October 5 date sits in the part of the window the new run dropped)',
          rec.get('verdict') == 'lifted' and rec.get('event', {}).get('date') == '2026-10-05' and rec['event'].get('change') == 'removed')
    check("the run's own dated facts settle it; the research step is not asked", asked == [])
    line = (out.get('campaign') or {}).get('consistency_line') or ''
    check(f'the answer carries one plain line naming the event and its date: {line!r}',
          line.startswith('The window no longer includes the October 5') and line.endswith('the audience reached.') and clean(line))
    check('the figures move freely: nothing was rebuilt', X.figures_of(out) == X.figures_of(fresh) and X.drivers_of(out) == X.drivers_of(fresh))
    check('the ready note carries the line', line in BD.note(out, {'url': 'https://x/deck.pptx'}, None))
    stored = X.RECORDS_FOR_TESTS.load(f"{X.RECORD_PREFIX}{rec['id']}.json")
    check('the record names the event and the changed parts of the window',
          stored and stored['verdict'] == 'lifted' and stored['event']['date'] == '2026-10-05' and len(stored['changed_windows']) == 2)
    # the same new run with the premiere outside both windows: held instead
    camp2 = leatherman_campaign(LEATHERMAN_PIECES_SECOND)
    camp2['drop_dates'] = ['2026-07-25', '2026-10-09']
    camp2['footprint'] = camp['footprint']
    fresh2 = S.build_payload(inputs, prim, campaign=camp2)
    out2, rec2 = X.apply_valuation(fresh2, inputs, campaign=camp2, candidates=[anchor],
                                   rebuild=lambda plan: S._rebuild_held(inputs, prim, camp2, plan),
                                   research=lambda changed: [], salt='lea-test-2')
    hd, ad, hf, af = X.drivers_of(out2), anchor['drivers'], X.figures_of(out2), anchor['figures']
    check('without a dated event the same run is held', rec2.get('verdict') == 'held')
    check(f"people reached inside 25% of the first run ({hd['audience_reached']:,} against {ad['audience_reached']:,})",
          within(hd['audience_reached'], ad['audience_reached'], 0.25))
    check(f"order share held near the first run ({hd['order_share_pct']:.2f}% against {ad['order_share_pct']:.2f}%)",
          abs(hd['order_share_pct'] - ad['order_share_pct']) <= 1.2)
    check(f"total inside 25% (${hf['total_usd']:,.0f} against ${af['total_usd']:,.0f})", within(hf['total_usd'], af['total_usd'], 0.25))
    parts = hf['engagement_usd'] + hf['earned_usd'] + hf['lift_usd'] + hf['conversion_usd']
    check('the parts add to the total', abs(parts - hf['total_usd']) < 1.0)
    check('the pieces counted stay what the dates say (four), the hold never touches them', hd.get('pieces') == 4)
    # a dated event found by the research step lifts the same way
    out3, rec3 = X.apply_valuation(fresh2, inputs, campaign=camp2, candidates=[anchor],
                                   rebuild=lambda plan: S._rebuild_held(inputs, prim, camp2, plan),
                                   research=lambda changed: [{'date': '2026-11-16', 'kind': 'award', 'name': 'Governors Awards'}], salt='lea-test-3')
    line3 = (out3.get('campaign') or {}).get('consistency_line') or ''
    check(f'an event the research step dates inside an added part lifts the band: {line3!r}',
          rec3.get('verdict') == 'lifted' and line3.startswith('The window now includes the November 16') and clean(line3))
    X.RECORDS_FOR_TESTS = None


def test_inside_and_off():
    print('inside the band, and off')
    X.RECORDS_FOR_TESTS = X.MemoryRecords()
    inputs, prim, camp = leatherman_inputs(), leatherman_prim(), leatherman_campaign(LEATHERMAN_PIECES_FIRST)
    fresh = S.build_payload(inputs, prim, campaign=camp)
    near_anchor = X.anchor_from_payload(dict(fresh, created_at='2026-11-01T00:00:00Z', end_date='2026-11-16'), ref='near')
    check('the fresh run would itself be an anchor (campaign reach at the house ratio)', near_anchor['eligible'] is True)
    out, rec = X.apply_valuation(fresh, inputs, campaign=camp, candidates=[near_anchor],
                                 rebuild=lambda plan: S._rebuild_held(inputs, prim, camp, plan), research=lambda c: [], salt='s')
    check('a run already inside the band is left as built, with a record that says so',
          rec.get('verdict') == 'inside' and X.figures_of(out) == X.figures_of(fresh))
    os.environ['PM_TWIN_CHECK'] = '0'
    try:
        out2, rec2 = X.apply_valuation(fresh, inputs, campaign=camp, candidates=[X.anchor_from_payload(LEATHERMAN_FIRST, ref='old')],
                                       rebuild=lambda plan: S._rebuild_held(inputs, prim, camp, plan), research=lambda c: [], salt='s')
        check('PM_TWIN_CHECK=0: nothing happens', rec2 == {} and out2 is fresh)
    finally:
        os.environ.pop('PM_TWIN_CHECK', None)
    n_before = len((X.RECORDS_FOR_TESTS.load(X.INDEX_KEY) or {}).get('records') or [])
    out3, rec3 = X.apply_valuation(fresh, inputs, campaign=camp, candidates=[], rebuild=None, research=None, salt='s')
    check('no twin: the run is untouched and no record is written', rec3 == {'verdict': 'no_twin'} and out3 is fresh
          and len((X.RECORDS_FOR_TESTS.load(X.INDEX_KEY) or {}).get('records') or []) == n_before)
    X.RECORDS_FOR_TESTS = None


def test_builder_hooks():
    print('the builder hooks')
    fp = C.normalize_footprint({'press': LEATHERMAN_PIECES_FIRST, 'press_pieces_total': 5})
    r0 = C.derive_reach(fp, {}, 'h', 46)
    r1 = C.derive_reach(dict(fp, reach_target_us=140_003), {}, 'h', 46)
    check('derive_reach lands on a reach target and every line moves with it',
          r1['reach_us'] == 140_003 and r1['press_people'] != r0['press_people'] and all(ln['people'] <= 140_003 for ln in r1['lines']))
    aov = S.derive_aov(T.campaign(), 'FASHION', 'h')
    d0 = C.derive_drop(T.campaign(), {}, aov, 'h')
    d1 = C.derive_drop(dict(T.campaign(), _orders_target=1_234), {}, aov, 'h')
    check('derive_drop lands on an orders target at the same basket and price', d1['orders'] == 1_234 and d1['units_us'] > 1_234
          and abs(d1['revenue_usd'] - 1_234 * d1['per_order_usd']) < 0.01 and d0['orders'] != 1_234)
    a1 = S.derive_aov(dict(T.campaign(), _aov_held_usd=241.37), 'FASHION', 'h')
    check('derive_aov lands on a held order value and keeps the basis the research gave',
          a1['aov'] == 241.37 and a1['basis'] == aov['basis'] and a1.get('held_to_twin'))
    a2 = S.derive_aov(dict(T.campaign(), _aov_held_usd=241.37), 'FASHION', 'h', caller_rate=300.0)
    check("a rate the caller set still wins", a2['aov'] == 300.0)
    inputs, prim, camp = leatherman_inputs(), leatherman_prim(), leatherman_campaign(LEATHERMAN_PIECES_FIRST)
    held = S._rebuild_held(inputs, prim, camp, {'audience_reached': 140_003, 'order_share_pct': 5.0, 'aov_usd': 229.11,
                                                 'engaged_post_pct': 39.0, 'engaged_pre_pct': 22.0, 'drift_pp': 0.61})
    hd = X.drivers_of(held)
    check(f"_rebuild_held puts every held driver at its primitive: {hd['audience_reached']:,} people, {hd['order_share_pct']:.2f}% order share, "
          f"${hd['aov_usd']} per order, {hd['engaged_post_pct']}% engaged",
          abs(hd['audience_reached'] - 140_003) <= 10 and abs(hd['order_share_pct'] - 5.0) < 0.3 and hd['aov_usd'] == 229.11
          and abs(hd['engaged_post_pct'] - 39.0) < 0.5)
    v = held['valuation']
    check('and the money is recomputed from them', abs(v['conversion_value'] - held['conversions']['post_users_projected'] * 229.11) < 1.0
          and abs(v['brand_engagement_value'] - held['totals']['post_users_projected'] * 2.5) < 1.0)


def test_read_hold_and_twin_block():
    print('a generated read')
    X.RECORDS_FOR_TESTS = X.MemoryRecords()
    entries = [
        {'k': 'k-first', 'ts': '2026-11-01T10:00:00', 'ws': '2026-09-01', 'we': '2026-09-30', 'fam': 'audience',
         'metrics': [{'name': 'us_viewers', 'label': 'US viewers in the window', 'value': 4_100_000, 'unit': 'people'},
                     {'name': 'completion_rate', 'label': 'finished the season', 'value': 61.2, 'unit': 'pct'}]},
        {'k': 'k-later', 'ts': '2026-11-10T10:00:00', 'ws': '2026-09-01', 'we': '2026-09-30', 'fam': 'audience',
         'metrics': [{'name': 'us_viewers', 'label': 'US viewers in the window', 'value': 9_000_000, 'unit': 'people'}]},
    ]
    twin = X.near_twin_entry({'subject': 'A Title', 'family': 'audience', 'window': {'start': '2026-09-05', 'end': '2026-10-02'}}, entries)
    check('the earliest banked read whose window is near is the twin', twin and twin['ref'] == 'k-first')
    blk = X.twin_block(twin, {'start': '2026-09-05', 'end': '2026-10-02'})
    check('its prompt block states the figures, the band and the changed parts of the window',
          'FIGURES ALREADY GIVEN ON A NEAR-TWIN ASK' in blk and '4100000' in blk.replace(',', '') and '2026-10-01 to 2026-10-02 (added)' in blk
          and 'within 25%' in blk and '3 points' in blk)
    data = {'metrics': [{'name': 'us_viewers', 'label': 'US viewers', 'value': 1_200_000, 'unit': 'people'},
                        {'name': 'completion_rate', 'label': 'finished the season', 'value': 59.0, 'unit': 'pct'},
                        {'name': 'something_else', 'value': 77, 'unit': 'people'}]}
    out, rec = X.hold_read_metrics(json.loads(json.dumps(data)), twin, salt='r')
    v = {m['name']: m['value'] for m in out['metrics']}
    check(f"a figure far below the twin's is held inside the band ({v['us_viewers']:,} against 4,100,000)",
          rec['verdict'] == 'held' and 3_075_000 < v['us_viewers'] < 3_500_000 and v['us_viewers'] % 10 != 0)
    check('a rate inside its band is left alone; a metric the twin never stated is untouched', v['completion_rate'] == 59.0 and v['something_else'] == 77)
    out2, rec2 = X.hold_read_metrics(json.loads(json.dumps(data)), twin, salt='r', event={'date': '2026-10-01', 'kind': 'premiere', 'name': 'season two'})
    check('a dated event in the changed part lifts the hold', rec2['verdict'] == 'lifted' and out2['metrics'][0]['value'] == 1_200_000)
    K.leave_twin('A Title', 'how many watched', twin, {'start': '2026-09-05', 'end': '2026-10-02'}, [])
    held = K.hold_pending(json.loads(json.dumps(data)), question='how many watched', subject='A Title')
    check('read_repair reaches the hold through knowledge.hold_pending and the record is written',
          held['metrics'][0]['value'] != 1_200_000 and held.get('_consistency', {}).get('verdict') == 'held'
          and any(r.get('product') == 'read' for r in (X.RECORDS_FOR_TESTS.load(X.INDEX_KEY) or {}).get('records') or []))
    again = K.hold_pending(json.loads(json.dumps(data)), question='how many watched', subject='A Title')
    check('the twin stays pending for a second pass of the same read', again['metrics'][0]['value'] != 1_200_000)
    K.take_twin('A Title', 'how many watched')
    cleared = K.hold_pending(json.loads(json.dumps(data)), question='how many watched', subject='A Title')
    check('and is cleared once the pass went through', cleared['metrics'][0]['value'] == 1_200_000)
    X.RECORDS_FOR_TESTS = None


def test_lines_are_plain():
    print('the lines')
    for ev, d in ((dict(date='2026-09-03', kind='premiere', name='Shiseido serum', change='added'), 'up'),
                  (dict(date='2026-10-05', kind='launch', name='launch', change='removed'), 'down'),
                  (dict(date='2026-10-10', kind='press_cluster', name='4 pieces of coverage', change='added'), 'up'),
                  (dict(date='2026-11-16', kind='award', name='Governors Awards', change='added'), 'down')):
        line = X.lifted_line(ev, d)
        check(f'{line!r}', clean(line) and line.endswith('the audience reached.') and len(line) < 160)
    check('the held line', clean(X.held_line()) and X.held_line() == 'These figures sit close to the earlier run on the same pairing and window.')


if __name__ == '__main__':
    test_thresholds_and_switches()
    test_names_and_windows()
    test_band_math()
    test_anchor_shapes()
    test_near_twin_picks_first()
    test_candidates_from_the_valuation_list()
    test_anne_is_held()
    test_anne_same_result_twice()
    test_leatherman_pieces_by_date()
    test_leatherman_is_lifted_by_the_premiere()
    test_inside_and_off()
    test_builder_hooks()
    test_read_hold_and_twin_block()
    test_lines_are_plain()
    print(f"\n{'FAILED ' + str(FAIL) if FAIL else 'ALL PASSED'}")
    sys.exit(1 if FAIL else 0)
