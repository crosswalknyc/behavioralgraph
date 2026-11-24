#!/usr/bin/env python3
"""Brand Partnership IQ: the order value fits the brand's own price tier
(Jenna 2026-11-24).

The beauty band reads as prestige ($45 to $110), so a drugstore brand's own
reads fell under its floor and were thrown out: the Zarna Garg x Maybelline
research read a $23 typical order and the valuation used $74.87. The order
value is now reasoned from the brand itself: its own core list prices when
they can be read (the lowest, times the order's basket: the low anchor),
else the research's read of a typical order, with the band only a sanity
check whose floor gives way for a brand below it. Prestige keeps its range.
No table of brands.

Hermetic: no network, no model, no mail. Fails on main, passes with the fix.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('BPIQ_CORPUS_ANCHORS', '0')
os.environ.setdefault('PM_TWIN_CHECK', '0')
os.environ.setdefault('PM_KNOWLEDGE_RESEARCH', '0')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from migration import bpiq_synthesis as S  # noqa: E402
import test_bpiq_dedupe_and_research as T  # noqa: E402  (inputs / prim fixtures)

FAIL = 0


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def order(brand='Drugco', **kw):
    d = T.inputs(brand=brand, campaign=brand, start='2026-10-05', end='2026-11-02', post_end='2026-11-24',
                 control=False, talent='Talent Z')
    d.update(kw)
    return d


DRUG_PRICES = [{'product': 'Matte Lip Ink', 'price_usd': 12.99, 'url': 'https://www.drugco.example/lip/ink'},
               {'product': 'Daily Foundation', 'price_usd': 11.99, 'url': 'https://www.drugco.example/face/base'},
               {'product': 'Lift Gloss', 'price_usd': 12.99, 'url': 'https://www.drugco.example/lip/gloss'},
               {'product': 'Volume Mascara', 'price_usd': 13.99, 'url': 'https://www.drugco.example/eye/mascara'}]
LUX_PRICES = [{'product': 'Velvet Lipstick', 'price_usd': 38.0}, {'product': 'Glow Serum', 'price_usd': 62.0},
              {'product': 'Silk Foundation', 'price_usd': 52.0}]


def camp(**kw):
    c = {'campaign_name': 'Team Drugco', 'brand': 'Drugco', 'category': 'BEAUTY', 'products': [], 'price_band': {}}
    c.update(kw)
    return S.normalize_campaign(c)


def aov(c, brand='Drugco'):
    return S.derive_aov(c, 'BEAUTY', S.run_salt(order(brand)))


def test_drugstore_brand():
    basket = aov(camp())['basket']
    a = aov(camp(aov_hint_usd=23.0, brand_prices={'tier': 'mass (drugstore)', 'list_prices': DRUG_PRICES}))
    check("a drugstore brand's order value comes from its own list prices", a['basis'] == 'brand_list_prices')
    check("the lowest core list price times the order's basket (the low anchor)",
          abs(a['aov'] - 11.99 * basket) < 0.15 and a['aov'] < 23.0)
    check("it lands at a drugstore basket, not inside the prestige band", a['aov'] < 45.0)
    h = aov(camp(aov_hint_usd=23.0, brand_prices={'tier': 'drugstore', 'list_prices': []}))
    check("with no list price read, a drugstore brand's own $23 read stands", h['basis'] == 'researched_order_read'
          and 18.0 < h['aov'] <= 23.0)
    nt = aov(camp(aov_hint_usd=None, brand_prices={'tier': 'mass', 'list_prices': []}))
    check("a mass brand with nothing readable sits low under the band, never inside it",
          nt['basis'] == 'tier_band' and 9.0 < nt['aov'] < 45.0)
    p = aov(camp(aov_hint_usd=None, brand_prices={'tier': 'unknown', 'list_prices': DRUG_PRICES}))
    check("prices under the band's floor say the brand sits below it, whatever the tier word",
          p['basis'] == 'brand_list_prices' and p['aov'] < 45.0)


def test_prestige_keeps_its_range():
    a = aov(camp(brand_prices={'tier': 'prestige', 'list_prices': LUX_PRICES}))
    check("a prestige brand's own prices give an order inside the prestige range",
          a['basis'] == 'brand_list_prices' and 45.0 <= a['aov'] <= 110.0)
    cheap = aov(camp(aov_hint_usd=None, brand_prices={'tier': 'prestige', 'list_prices': [
        {'product': 'Lip Balm', 'price_usd': 24.0}, {'product': 'Serum', 'price_usd': 88.0}, {'product': 'Cream', 'price_usd': 96.0}]}))
    check("a prestige brand's cheapest item never drags its order under its range", 45.0 <= cheap['aov'] <= 110.0)
    h = aov(camp(aov_hint_usd=23.0, brand_prices={'tier': 'prestige', 'list_prices': []}))
    check("a $23 read on a prestige brand is still thrown out, as before", h['basis'] == 'category_band'
          and h['aov'] >= 45.0)
    none = aov(camp())
    check("a brand with no tier and no prices keeps today's band", none['basis'] == 'category_band'
          and 45.0 <= none['aov'] <= 110.0)
    collection = aov(camp(products=[{'name': 'Jacket', 'price_usd': 120.0, 'hero': True}],
                          brand_prices={'tier': 'mass', 'list_prices': DRUG_PRICES}))
    check("a campaign's own collection prices still come first", collection['basis'] == 'researched_prices')
    rate = S.derive_aov(camp(brand_prices={'tier': 'mass', 'list_prices': DRUG_PRICES}), 'BEAUTY', 's', caller_rate=31.5)
    check("a rate the caller set still wins", rate['basis'] == 'caller_rate' and rate['aov'] == 31.5)
    junk = aov(camp(brand_prices={'tier': 'mass', 'list_prices': [{'product': 'x', 'price_usd': 0.2}]}))
    check("a price that cannot be a list price is ignored", junk['basis'] != 'brand_list_prices')


def test_the_valuation():
    c = camp(aov_hint_usd=23.0, brand_prices={'tier': 'mass', 'list_prices': DRUG_PRICES})
    pr = T.prim()
    pr['category'] = 'BEAUTY'
    p = S.build_payload(order(), pr, created_by='tester', campaign=c)
    v = p['valuation']
    rate = v['rates']['conv_value_per_user']
    check("the valuation's order value is the brand's own basket", rate < 23.0 and v['aov_basis'] == 'brand_list_prices')
    check("conversion value is orders times that order value",
          abs(v['conversion_value'] - p['conversions']['post_users_projected'] * rate) < 0.02)
    line = v['rate_sources']['conv_value_per_user']
    check("the order value line reads plainly", "Drugco's own list prices start at $11.99" in line
          and '\u2014' not in line and not re.search(r'synth|model|research|estimat', line, re.I))
    check("the read line on the page carries the same figure", f"${rate:,.2f} per order" in (p['campaign'].get('read_line') or ''))
    back = S.normalize_campaign(json.loads(json.dumps(c)))
    check("the brand's prices survive the saved research read back", back['brand_prices']['list_prices']
          and S.derive_aov(back, 'BEAUTY', S.run_salt(order()))['aov'] == aov(c)['aov'])


class Research:
    def __call__(self, system, user_prompt, **kw):
        if kw.get('surface') == 'bpiq_campaign_research':
            return {'campaign_name': 'Team Drugco', 'brand': 'Drugco', 'talent': 'Talent Z', 'category': 'BEAUTY',
                    'aov_hint_usd': 23.0, 'brand_prices': {'tier': 'mass', 'list_prices': DRUG_PRICES}}
        return {}


def test_research_and_the_brands_own_page():
    pages = {'https://www.drugco.example/face/base': '<script type="application/ld+json">{"@type":"Product","offers":'
                                                     '{"@type":"Offer","price":"10.99","priceCurrency":"USD"}}</script>'}
    orig = S.with_page_prices
    S.with_page_prices = lambda bp, brand='', **kw: orig(bp, brand, fetch=lambda u: pages.get(u, ''))
    try:
        c = S.research_campaign(order(), Research(), tools=None)
    finally:
        S.with_page_prices = orig
    bp = c.get('brand_prices') or {}
    check("the research asks for the brand's tier and own list prices", 'brand_prices' in S.campaign_prompt()
          and 'list_prices' in S.campaign_prompt())
    check("the brand's tier and prices ride the campaign research", bp.get('tier') == 'mass' and len(bp.get('list_prices') or []) == 4)
    got = {p['url']: (p['price_usd'], p['source']) for p in bp.get('list_prices') or []}
    check("a price the brand's own page shows replaces the research's figure",
          got.get('https://www.drugco.example/face/base') == (10.99, 'page'))
    check("a page that shows no price keeps the research's figure",
          got.get('https://www.drugco.example/lip/ink') == (12.99, 'research'))
    retail = S.with_page_prices({'tier': 'mass', 'list_prices': [{'product': 'x', 'price_usd': 12.0, 'url': 'https://bigbox.example/p/1'}]},
                                'Drugco', fetch=lambda u: '"price":"7.50","priceCurrency":"USD"')
    check("a retailer's page is not read (its price can be a sale price)", retail['list_prices'][0]['price_usd'] == 12.0)
    from migration import bpiq_link_reads as R
    check("a page's price reads from its price tags too",
          R.parse_list_price('<meta property="product:price:amount" content="13.99"><meta property="product:price:currency" content="USD">') == 13.99)
    check("a page in another currency gives no US price", R.parse_list_price('"price":"9.99","priceCurrency":"EUR"') is None)


def main():
    for t in (test_drugstore_brand, test_prestige_keeps_its_range, test_the_valuation, test_research_and_the_brands_own_page):
        print(t.__name__)
        try:
            t()
        except Exception as e:     # a crash before the fix is a failure, never a skip
            import traceback
            traceback.print_exc()
            check(f"{t.__name__} ran ({type(e).__name__}: {e})", False)
    print(f"\n{'FAIL' if FAIL else 'PASS'}: {FAIL} failure(s)")
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
