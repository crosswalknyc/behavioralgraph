#!/usr/bin/env python3
"""Brand Partnership IQ: the campaign's reach, the drop's revenue and the
talent's share come from the campaign, not a slice of the fan base
(Jenna 2026-10-28, Jennifer Beals x Ganni x Lululemon read on 79,738
people). Hermetic."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from migration import bpiq_synthesis as S  # noqa: E402
from migration import bpiq_campaign as C  # noqa: E402
from migration import bpiq_matched_control as MC  # noqa: E402
import test_bpiq_dedupe_and_research as T  # noqa: E402  (inputs / prim / campaign fixtures)

FAIL = 0
SOURCE_WORDS = ('pew', 'statista', 'nielsen', 'similarweb', 'semrush', 'vidiq', 'elle', 'variety', 'vogue',
                'hollywood reporter', 'wwd', 'e! online', 'source:', 'according to')
METHOD_WORDS = ('synth', 'modeled', 'modelled', 'estimated', 'pipeline', 'hostmap', 'claude', 'anthropic',
                'researched', 'calibrated', 'anchored')


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def clean(text):
    low = str(text or '').lower()
    if '\u2014' in low or '\u2013' in low or re.search(r'\bdoors?\b', low):
        return False
    return not any(w in low for w in SOURCE_WORDS) and not any(w in low for w in METHOD_WORDS)


def footprint(views_youtube=753130, press_n=20, events=True, talent_share_pct=66.0):
    press = [{'outlet': o, 'url': f'https://{o.lower().replace(" ", "")}.example/x', 'date': '2026-10-15', 'country': 'US',
              'outlet_us_monthly_uniques': u, 'leads_with_talent': True}
             for o, u in (('Elle', 9_500_000), ('Variety', 21_000_000), ('The Hollywood Reporter', 19_000_000),
                          ('E! Online', 18_000_000), ('WWD', 4_800_000), ('Marie Claire', 7_600_000),
                          ('The Cut', 11_000_000), ('Good Morning America', 16_000_000), ('Refinery29', 9_000_000),
                          ('Hypebae', 1_200_000), ('Haute Living', None), ('GO Magazine', None),
                          ('OK! Magazine', 7_000_000), ('Vogue Scandinavia', None), ('Vogue Italia', None),
                          ('Harpers Bazaar', None), ('Elle Australia', None), ('Drapers', None),
                          ('FashionNetwork', None), ('Retail Boss', None))[:press_n]]
    for p in press:
        if p['outlet'] in ('Vogue Scandinavia', 'Vogue Italia', 'Harpers Bazaar', 'Elle Australia', 'Drapers'):
            p['country'] = 'EU'
    fp = {
        'press': press, 'press_pieces_total': press_n + 14,
        'events': ([{'name': 'Club Dance launch night', 'date': '2026-10-14', 'place': 'Brooklyn', 'host': 'the brands',
                     'guests': ['Jennifer Beals', 'METTE', 'Honey Dijon', 'Matt James', 'Yumi Nu'],
                     'coverage': ['OK! Magazine', 'CelebMafia', 'BFA', 'LinkedIn'], 'attendee_followers_total': 3_900_000,
                     'social_posts': 60, 'talent_attended': True},
                    {'name': 'Club Dance public nights', 'date': '2026-10-15', 'place': 'Brooklyn', 'host': 'the brand',
                     'guests': [], 'coverage': ['pulsd', 'NYC Plugged'], 'attendee_followers_total': 600_000}] if events else []),
        'commercial': {'title': 'Joy is a Power Move', 'platforms': [
            {'platform': 'YouTube', 'account': 'lululemon', 'url': 'https://youtube/x', 'views': views_youtube, 'as_of': '2026-10-24'},
            {'platform': 'Instagram', 'account': 'lululemon', 'url': 'https://instagram/x', 'comments': 4000, 'followers': 5_500_000},
            {'platform': 'TikTok', 'account': 'lululemon', 'url': 'https://tiktok/x', 'followers': 1_500_000}]},
        'viral_posts': [
            {'platform': 'Instagram', 'account': 'thejenniferbeals', 'followers': 629_000, 'url': 'https://instagram/p', 'likes': 85_500, 'comments': 2_740, 'features_talent': True},
            {'platform': 'Instagram', 'account': 'thejenniferbeals', 'followers': 629_000, 'url': 'https://instagram/q', 'likes': 33_900, 'comments': 540, 'features_talent': True},
            {'platform': 'X', 'account': 'fan', 'url': 'https://x/x', 'likes': 9_000, 'features_talent': True}],
        'brand_social': {'accounts': [{'platform': 'Instagram', 'account': 'ganni', 'followers': 1_600_000, 'posts_about_campaign': 6},
                                      {'platform': 'Instagram', 'account': 'mettenarrative', 'followers': 280_000, 'posts_about_campaign': 5}]},
        'search': {'interest_note': 'spiked at launch', 'us_searches_window': 260_000},
        'site': {'brand_site_us_monthly_visits': 14_470_000, 'brand_site_us_monthly_uniques': None, 'collection_page_share_pct': None},
        'inventory': {'skus': None, 'colorways_avg': 1.7, 'sizes_avg': 5.5, 'units_hint': None, 'comparable_note': '22 pieces, global'},
        'talent_asset_share_pct': talent_share_pct, 'talent_is_face': True,
        'sources': [{'title': 'x', 'url': 'https://x', 'used_for': 'y'}],
    }
    return fp


def camp_with(fp=None, **kw):
    c = T.campaign(**kw) if kw else T.campaign(sold_out=True, days=1.0, partial=True)
    c = dict(c, footprint=fp if fp is not None else footprint())
    return S.normalize_campaign(c, T.inputs())


def test_reach():
    salt = S.run_salt(T.inputs())
    camp = camp_with()
    r = C.derive_reach(camp['footprint'], camp, salt, 14)
    check("reach is millions for a national sell-out drop", r['reach_us'] >= 2_000_000)
    check("reach is at least the film's own deduped viewers", r['reach_us'] >= r['commercial_people'] > 0)
    check("reach is at least the collection page anchor", r['reach_us'] >= r['site_people'] > 0)
    check("reach is at least every line", all(r['reach_us'] >= ln['people'] for ln in r['lines']))
    check("reach is under the gross (people are counted once)", r['reach_us'] < r['gross_people'])
    check("press line counted and deduped", 0 < r['press_people'] < sum(1 for _ in camp['footprint']['press']) * 420_000)
    check("event line present", r['event_people'] > 0 and r['events'] == 2)
    check("messy last digits on the reach", r['reach_us'] % 10 != 0)
    # a viral film at N views: the window audience can never sit under N deduped
    big = footprint(views_youtube=25_000_000)
    camp_big = camp_with(big)
    r2 = C.derive_reach(camp_big['footprint'], camp_big, salt, 14)
    check("a film at 25M views lifts the reach above its deduped viewers",
          r2['reach_us'] >= r2['commercial_people'] and r2['reach_us'] > r['reach_us'])
    p = S.build_payload(T.inputs(), dict(T.prim(), _talent_audience_in_window=79_738), campaign=camp_big)
    check("window audience is never under the film's deduped viewers",
          p['projected_audience_size'] >= r2['commercial_people'] and p['projected_audience_size'] >= 79_738)
    # no footprint: the talent audience path as before
    plain = S.build_payload(T.inputs(), T.prim(), campaign=T.campaign())
    check("without a footprint the audience is the talent audience in the window",
          plain['campaign']['audience_basis'] == 'talent audience in the window'
          and plain['projected_audience_size'] == T.prim()['projected_audience_size'])
    # two salts never share a reach; one salt always does
    r3 = C.derive_reach(camp['footprint'], camp, S.run_salt(T.inputs(talent='Someone Else')), 14)
    check("reach is salted, not a constant", r3['reach_us'] != r['reach_us'])
    r4 = C.derive_reach(camp['footprint'], camp, S.run_salt(T.inputs(brand='lulu lemon', campaign='ganni x lulu lemon')), 14)
    check("same ask in another spelling gives the same reach", r4['reach_us'] == r['reach_us'])


def test_drop_and_lenses():
    salt = S.run_salt(T.inputs())
    camp = camp_with()
    aov = S.derive_aov(camp, 'FASHION', salt)
    d = C.derive_drop(camp, camp['footprint'], aov, salt)
    check("units come from SKUs x units per SKU x US share x sell-through",
          abs(d['units_us'] - d['skus'] * d['units_per_sku'] * d['us_share_of_run'] * d['sell_through']) < d['units_us'] * 0.02 + 20)
    check("revenue = orders x the order value", abs(d['revenue_usd'] - d['orders'] * d['per_order_usd']) < 0.01)
    check("orders = units / basket", abs(d['orders'] - d['units_us'] / aov['basket']) < d['orders'] * 0.02 + 10)
    check("a sold-out drop runs in the millions", d['revenue_usd'] >= 2_000_000)
    full = camp_with(kw=None) if False else S.normalize_campaign(dict(T.campaign(sold_out=True, days=1.0, partial=False), footprint=footprint()), T.inputs())
    d2 = C.derive_drop(full, full['footprint'], aov, salt)
    check("a full sell-out sells the whole run", d2['sell_through'] == 1.0 and d2['units_us'] > d['units_us'])
    hinted = S.normalize_campaign(dict(T.campaign(), footprint=dict(footprint(), inventory={'units_hint': 90_123})), T.inputs())
    d3 = C.derive_drop(hinted, hinted['footprint'], aov, salt)
    check("a published units figure wins", d3['units_us'] == 90_123 and d3['basis'] == 'published units')
    p = S.build_payload(T.inputs(), dict(T.prim(), _talent_audience_in_window=79_738), campaign=camp)
    v = p['valuation']
    check("converters in the window are the drop's orders",
          p['conversions']['post_users_projected'] == p['campaign']['drop']['orders'] and p['conversions'].get('basis') == "the drop's orders")
    check("conversion value equals the drop's revenue", abs(v['conversion_value'] - p['campaign']['drop']['revenue_usd']) < 1.0)
    rows = {r['platform']: r for r in v['emv_breakdown']}
    check("the press is its own earned line", 'Press' in rows and rows['Press']['emv_value'] > 0)
    check("the launch event is its own earned line", 'Launch event' in rows and rows['Launch event']['emv_value'] > 0)
    check("earned lines are people reached times the card rate, not scaled by the holdout",
          rows['Press']['incremental_users_projected'] == p['campaign']['reach']['press_people']
          and abs(rows['Press']['emv_value'] - rows['Press']['incremental_users_projected'] * rows['Press']['emv_per_user_rate']) < 0.01)
    check("earned media sums its rows", abs(v['earned_media_value'] - sum(r['emv_value'] for r in v['emv_breakdown'])) < 0.05)
    check("total is the four lenses", abs(v['total_brand_value'] - (v['brand_engagement_value'] + v['earned_media_value']
                                                                      + v['brand_lift_value'] + v['conversion_value'])) < 0.05)
    no_event = camp_with(footprint(events=False))
    p2 = S.build_payload(T.inputs(), dict(T.prim(), _talent_audience_in_window=79_738), campaign=no_event)
    check("no event, no event line", 'Launch event' not in {r['platform'] for r in p2['valuation']['emv_breakdown']})
    check("the drop line reads plainly", 'pieces sold in the US' in p['campaign']['drop_line'] and clean(p['campaign']['drop_line']))


def test_talent_share():
    salt = S.run_salt(T.inputs())
    camp = camp_with()
    r = C.derive_reach(camp['footprint'], camp, salt, 14)
    a = C.derive_talent_share(camp['footprint'], camp, r, 0.9, salt)
    b = C.derive_talent_share(camp_with(footprint(talent_share_pct=20.0))['footprint'], camp, r, 0.9, salt)
    check("a face carrying more of the exposure holds a larger share", a['talent_share'] > b['talent_share'])
    c = C.derive_talent_share(camp['footprint'], camp, r, 0.3, salt)
    check("a weaker holdout result lowers the share", c['talent_share'] < a['talent_share'])
    d = C.derive_talent_share(dict(camp['footprint'], talent_asset_share_pct=None), camp, r, 0.9, S.run_salt(T.inputs(talent='Other Face')))
    check("without a researched asset share the band is salted, never a constant",
          d['talent_share'] not in (a['talent_share'], b['talent_share']) and 0.02 <= d['talent_share'] <= 0.85)
    quiet = dict(camp['footprint'], viral_posts=[], commercial={'title': 'x', 'platforms': []})
    e = C.derive_talent_share(quiet, camp, {'views_total': 0}, 0.9, salt)
    check("a viral film raises the face uplift", a['face_uplift'] > e['face_uplift'])
    p = S.build_payload(T.inputs(), dict(T.prim(), _talent_audience_in_window=79_738), campaign=camp)
    v = p['valuation']
    check("her dollar share is the share of the settled total",
          abs(v['attributable_to_talent'] - v['total_brand_value'] * v['talent_share']) < 0.02)
    check("her share is not the house attributable formula", v['attributable_to_talent'] != v['attributable_to_partnership'])
    check("talent line reads plainly", "share" in p['campaign']['talent_line'] and clean(p['campaign']['talent_line']))
    # a re-drawn comparison group settles her dollar share again
    p2, _ = MC.enforce_matched_control(p, subject=salt)
    check("her dollar share follows the lenses after any redraw",
          abs(p2['valuation']['attributable_to_talent'] - p2['valuation']['total_brand_value'] * p2['valuation']['talent_share']) < 0.02)


def test_windows():
    w = S._dates(T.inputs())
    check("the pre window matches the campaign window length", w['pre']['days'] == w['event']['days'] == 14 and w['matched'])
    check("the pre window sits right before the campaign", w['pre']['end'] == '2026-10-14' and w['pre']['start'] == '2026-10-01')
    check("the year before rides along as baseline", w['baseline']['days'] == 365 and w['baseline']['end'] == '2026-10-14')
    w2 = S._dates(dict(T.inputs(), pre_start='2025-10-15', pre_end='2026-10-14'))
    check("an explicit pre window is honored", w2['pre']['days'] == 365 and not w2['matched'])
    p = S.build_payload(T.inputs(), T.prim(), campaign=T.campaign())
    check("payload carries the matched windows", p['pre_period']['days'] == 14 and p['windows']['matched_pre_window'] is True
          and p['windows']['baseline_period']['days'] == 365)
    check("hits per day compare like windows", p['totals']['pre_hits_per_day'] > 0 and p['totals']['post_hits_per_day'] > 0)


def test_text_and_readme():
    camp = camp_with()
    p = S.build_payload(T.inputs(), dict(T.prim(), _talent_audience_in_window=79_738), campaign=camp)
    blob = json.dumps(p)
    check("no outlet or tracker name on the payload", clean(blob) or all(w not in blob.lower() for w in SOURCE_WORDS))
    for t in (p['campaign']['reach_line'], p['campaign']['drop_line'], p['campaign']['talent_line'],
              p['conversions'].get('note', ''), p['valuation']['rate_sources']['conv_value_per_user']):
        check(f"clean: {t[:50]}", clean(t))
    aov = {'aov': p['valuation']['rates']['conv_value_per_user'], 'basket': 1.5}
    readme = S.underpinnings_readme(T.inputs(), camp, aov, p)
    check("readme carries reach, the drop and her share", 'People reached in the window' in readme
          and 'Pieces sold in the US' in readme and 'Her share' in readme)
    check("readme names no outlet, tracker or method word", clean(readme))
    check("the footprint with its outlet names stays off the payload", 'footprint' not in p['campaign'] and 'Elle' not in blob)


def test_normalize_footprint():
    fp = C.normalize_footprint({'press': [{'outlet': 'Elle', 'outlet_us_monthly_uniques': '9.5M'}],
                                'commercial': {'platforms': [{'platform': 'YouTube', 'views': '753,130'}]},
                                'inventory': {'units_hint': '90k'}})
    check("string counts parse (9.5M, 753,130, 90k)", fp['press'][0]['outlet_us_monthly_uniques'] == 9_500_000
          and fp['commercial']['platforms'][0]['views'] == 753_130 and fp['inventory']['units_hint'] == 90_000)
    check("has_footprint reads the press", C.has_footprint(fp) and not C.has_footprint({}))


if __name__ == '__main__':
    for fn in (test_reach, test_drop_and_lenses, test_talent_share, test_windows, test_text_and_readme,
               test_normalize_footprint):
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{'PASS' if not FAIL else f'{FAIL} FAILED'}")
    sys.exit(1 if FAIL else 0)
