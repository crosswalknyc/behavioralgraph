#!/usr/bin/env python3
"""Brand Partnership IQ: the deck's next step sends people to the brand's
own product or shop pages (Jenna 2026-11-24).

Emi Hill's Zarna Garg x Maybelline deck told the reader to send people to a
race organizer's news page: the next step took the campaign's newest page
in the list, and the campaign's newest pages were a news announcement, a
roster page, a recap and the talent's own posts. A next step for orders now
points to the brand's own product or shop page on the brand's own site,
never a press, news, event, partner or retailer page and never the
talent's posts; with no brand shop page known, to the product pages of
the brand's own site; a domain is never guessed.

Hermetic: no network, no model, no mail. Fails on main, passes with the fix.
"""
from __future__ import annotations

import copy
import os
import re
import sys
from pathlib import Path

os.environ.setdefault('CORPUS_CATALOG', '0')
os.environ.setdefault('REGRESSION_TEST_MODE', '1')
os.environ.setdefault('BPIQ_CORPUS_ANCHORS', '0')
os.environ.setdefault('PM_TWIN_CHECK', '0')

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(WEB / 'scripts'))

from migration import bpiq_synthesis as S  # noqa: E402
from migration import bpiq_campaign as C  # noqa: E402
from migration import bpiq_deck_builder as DB  # noqa: E402
import test_bpiq_dedupe_and_research as T  # noqa: E402  (inputs / prim fixtures)

FAIL = 0


def check(label, cond):
    global FAIL
    print(f"  {'ok ' if cond else 'FAIL'}  {label}")
    if not cond:
        FAIL += 1


def page(name, before, after, new=False):
    return {'common_name': name, 'before': before, 'after': after, 'new': new}


NEWS_CASE = [
    page('brandco.com homepage', 4800, 7000),
    page('Bigbox Brandco brand shelf', 4400, 6900),               # a retailer's shelf, visited more than the brand's pages
    page('Talent Y race-day Instagram posts', 0, 4600, new=True),  # the talent's own posts
    page('brandco.com Long Wear mascara product page', 2900, 4100),
    page('brandco.com Stay lip category', 3100, 3900),
    page('Race Org 2026 celebrity runner announcement', 0, 3000, new=True),   # a race organizer's news page
    page('Team Brandco 2026 marathon roster page', 0, 2000, new=True),
    page('brandco.com x Charity mental health page', 600, 1400),  # a partner page on the brand's own site
    page('Race Org 2026 finisher recap naming Talent Y', 0, 1200, new=True),
]


def payload_with(pages):
    camp = {'campaign_name': 'Brandco', 'footprint': C.normalize_footprint(
        {'link_reads': [{'url': f'https://www.tiktok.com/@talenty/video/{(1792000000 + i * 86400) << 32}', 'views': 600_000,
                         'published': '2026-10-20'} for i in range(10)]})}
    pr = T.prim()
    pr['category'] = 'BEAUTY'
    pr['conversions'] = {'pre_users_pct': 1.63, 'post_users_pct': 2.12}
    pr['top_brand_properties'] = [{'common_name': p['common_name'], 'pre_hits': p['before'], 'post_hits': p['after'],
                                   'new': p['new']} for p in pages]
    inp = T.inputs(brand='Brandco', campaign='Brandco', start='2026-10-05', end='2026-11-02', post_end='2026-11-24',
                   control=False, talent='Talent Y')
    inp['rates'] = {'conv_value_per_user': 16.13}
    return S.build_payload(inp, pr, created_by='tester', campaign=camp)


def ctx_with(pages):
    return DB._derive_context(payload_with(pages), None, 'BEAUTY')


def lines(ctx):
    return DB._next_step(ctx)


def test_news_page_case():
    ctx = ctx_with(NEWS_CASE)
    short, full = lines(ctx)
    both = short + ' ' + full
    check("the orders branch speaks (attention high, orders few)", 'send people to' in full and 'point it at' in short)
    check("the next step never sends people to a news page", 'announcement' not in both and 'recap' not in both)
    check("never to the talent's posts", 'Instagram posts' not in both)
    check("never to a roster, event or partner page", 'roster' not in both and 'mental health' not in both)
    check("never to a retailer's shelf, however busy", 'Bigbox' not in both)
    check("it sends people to the brand's own product page people visited most",
          'the brandco.com Long Wear mascara product page' in short and 'the brandco.com Long Wear mascara product page' in full)
    check("each sentence stays short and clean", all(len(s.split()) <= 25 for s in re.split(r'(?<=\.)\s+', both))
          and '\u2014' not in both and not re.search(r'\bdoors?\b', both))


def test_talent_post_case():
    pages = [page('brandco.com homepage', 4800, 7000), page('Talent Y race-day Instagram posts', 0, 4600, new=True),
             page('brandco.com Stay lip category', 3100, 3900)]
    short, full = lines(ctx_with(pages))
    check("with the talent's posts the only new page, the brand's own shop page leads",
          'the brandco.com Stay lip category' in short and 'Instagram posts' not in short + full)


def test_no_shop_page_known():
    pages = [page('brandco.com homepage', 4800, 7000), page('Bigbox Brandco brand shelf', 4400, 6900),
             page('Race Org 2026 celebrity runner announcement', 0, 3000, new=True)]
    short, full = lines(ctx_with(pages))
    check("with no brand shop page known, the product pages on the brand's own site", 'the product pages on Brandco.com' in short)
    pages2 = [page('Bigbox Brandco brand shelf', 4400, 6900), page('Race Org 2026 celebrity runner announcement', 0, 3000, new=True)]
    short2, _ = lines(ctx_with(pages2))
    check("with no brand site in the list, the brand's own product pages, and no domain guessed",
          "Brandco's own product pages" in short2 and '.com' not in short2)


def test_campaign_shop_page_first():
    pages = copy.deepcopy(NEWS_CASE) + [page('brandco.com/collections/talent-y-edit', 0, 900, new=True)]
    short, _ = lines(ctx_with(pages))
    check("the campaign's own shop page on the brand's site comes first, as written",
          'point it at brandco.com/collections/talent-y-edit.' in short)


def main():
    for t in (test_news_page_case, test_talent_post_case, test_no_shop_page_known, test_campaign_shop_page_first):
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
