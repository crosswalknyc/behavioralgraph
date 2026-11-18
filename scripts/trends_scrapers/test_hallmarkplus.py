#!/usr/bin/env python3
"""Regression for the Hallmark+ panel (2026-11-18).

Hermetic: the fixtures below are trimmed copies of what the site's JSON
API returned on 2026-11-18 (the menu, the movies page, three rows of
the 'Top Movies Today' collection, two series rows). Covers:

  * the menu walker finds the Movies and Series page ids where the
    API keeps them (`display.title` + `action.parameters.pageId`)
  * the page parser returns rails in page order and the chart rail by
    its heading, and the catalog filter drops hero / spotlight shelves
  * item classification: ITEM/VIDEO is a Film with a year, COLLECTION/
    TV_SHOW is TV with no year (a series' releaseDate is its newest or
    next season, which reads as the future)
  * assembly: chart rows lead the films and carry the chart name as
    `collection`, catalog rows carry a blank one, first occurrence
    wins, both kinds are zipper-interleaved, ranks are contiguous
  * the registry: the panel's published chart is wired in collection
    mode and the Amazon breakout's ceiling is exactly the parent's
    ceiling times the top of its band

    python3 -m scripts.trends_scrapers.test_hallmarkplus
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from scripts.trends_scrapers import hallmarkplus as hp  # noqa: E402

FAILURES: list = []


def check(ok: bool, what: str) -> None:
    print(f'{"PASS" if ok else "FAIL"}  {what}')
    if not ok:
        FAILURES.append(what)


_SITE = 'https://www.hallmarkplus.com'
_IMG = ('https://097d0e9f.customer.static.core.one.accedo.tv/'
        '097d0e9f2de0f04b/image/item/%s/poster/portrait/960x1440_tt')


def _film(iid: str, title: str, slug: str, date: str) -> dict:
    return {
        'id': iid, 'type': 'ITEM', 'subtype': 'VIDEO', 'title': title,
        'releaseDate': date, 'genres': ['Holiday', 'Romance'],
        'extras': {'category': 'LONG'},
        'refs': {'share': f'{_SITE}/details/VIDEO/item/{iid}/{slug}'},
        'images': {'poster': {'portrait': [{'url': _IMG % iid}]}},
    }


def _show(iid: str, title: str, slug: str, seasons: int) -> dict:
    return {
        'id': iid, 'type': 'COLLECTION', 'subtype': 'TV_SHOW', 'title': title,
        'releaseDate': '2026-01-02T00:00:00Z', 'genres': ['Mystery'],
        'extras': {'category': 'LONG'},
        'refs': {'share': f'{_SITE}/details/TV_SHOW/collection/{iid}/{slug}'},
        'images': {'poster': {'portrait': [{'url': _IMG % iid}]}},
        'show': {'seasonCount': seasons},
    }


CHART = [
    _film('MOVINA038738', 'The Nights Before Christmas',
          'the-nights-before-christmas', '2026-11-14T08:00:00Z'),
    _film('MOVINA038846', 'Mistletoe and Mimosas',
          'mistletoe-and-mimosas', '2026-11-15T08:00:00Z'),
    _film('MOVINA038767', 'My Christmas Cowboy',
          'my-christmas-cowboy', '2026-11-13T08:00:00Z'),
]
CATALOG_FILMS = [
    _film('MOVINA038846', 'Mistletoe and Mimosas',   # also on the chart
          'mistletoe-and-mimosas', '2026-11-15T08:00:00Z'),
    _film('MOVINA011111', 'A Biltmore Christmas',
          'a-biltmore-christmas', '2023-11-26T08:00:00Z'),
    # a short-form extra: not a title, must be dropped
    {'id': 'CLIP1', 'type': 'ITEM', 'subtype': 'VIDEO', 'title': 'Trailer',
     'extras': {'category': 'SHORT'}, 'refs': {'share': f'{_SITE}/x'}},
]
SERIES = [
    _show('MSTMNA00', 'Mistletoe Murders', 'mistletoe-murders', 3),
    _show('WAYHNA00', 'The Way Home', 'the-way-home', 4),
]

MENU = {
    'desktop': [
        {'id': 'a', 'display': {'type': 'TEXT', 'title': 'Home'},
         'action': {'kind': 'NAVIGATE_TO_PAGE',
                    'parameters': {'pageId': 'HOMEPAGE'}}},
        {'id': 'b', 'display': {'type': 'TEXT', 'title': 'Movies'},
         'action': {'kind': 'NAVIGATE_TO_PAGE',
                    'parameters': {'pageId': 'MOVIESPAGE'}}},
        {'id': 'c', 'display': {'type': 'TEXT', 'title': 'Series'},
         'action': {'kind': 'NAVIGATE_TO_PAGE',
                    'parameters': {'pageId': 'SERIESPAGE'}}},
    ],
}


def _container(title: str, coll_id: str, suffix: str = '') -> dict:
    return {'data': {'feed': f'{_SITE}/api/core/catalog/collection/{coll_id}{suffix}'},
            'templates': {'desktop': {'properties': {'title': title}}}}


PAGE = {'page': {'containerCollections': [{'containers': [
    _container('Movies Hero', '01HYEGMR7S5GJFCN90HWD5J3JB'),
    _container('Spotlight', '01HYEGMR7SNAT2ZZCPYYNVAVFP', '?limit=5'),
    _container('Top Movies Today', hp._CHART_COLLECTION_ID),
    _container('Christmas Classics', '01K0SNZ7FNG6DD5DZDHSX2C6PA'),
]}]}}


def main() -> int:
    # --- menu ----------------------------------------------------------
    ids = hp.page_ids_from_menu(MENU)
    check(ids['movies'] == 'MOVIESPAGE' and ids['series'] == 'SERIESPAGE',
          'menu walker reads display.title + action.parameters.pageId')
    fallback = hp.page_ids_from_menu({})
    check(fallback == hp._PAGE_IDS, 'empty menu keeps the fallback page ids')

    # --- page rails ----------------------------------------------------
    rails = hp.rails_from_page(PAGE)
    check([t for t, _ in rails] == ['Movies Hero', 'Spotlight',
                                    'Top Movies Today', 'Christmas Classics'],
          'rails come back in page order with their headings')
    catalog = [t for t, _ in rails if hp._is_catalog_rail(t)]
    check(catalog == ['Top Movies Today', 'Christmas Classics'],
          'hero and spotlight shelves are not catalog rails')
    chart_feed = next(f for t, f in rails if t.lower() == hp._CHART_RAIL_TITLE)
    check(hp._collection_id(chart_feed) == hp._CHART_COLLECTION_ID,
          'chart rail resolves to the Top 20 collection id')
    check(hp._collection_id(f'{_SITE}/api/core/catalog/collection/ABC?limit=5') == 'ABC',
          'collection id strips the query string')

    # --- rows ----------------------------------------------------------
    chart_rows = hp.rows_from_items(CHART)
    check(len(chart_rows) == 3 and all(r['category_display'] == 'Film' for r in chart_rows),
          'chart items classify as Film')
    check(chart_rows[0]['year'] == 2026 and chart_rows[0]['url'].startswith(
        f'{_SITE}/details/VIDEO/item/MOVINA038738/'),
          'film rows carry a year and the share URL')
    check(chart_rows[0]['image'].startswith('https://') and chart_rows[0]['content_id'] == 'MOVINA038738',
          'film rows carry the poster and the content id')
    film_rows = hp.rows_from_items(CATALOG_FILMS)
    check([r['title'] for r in film_rows] == ['Mistletoe and Mimosas', 'A Biltmore Christmas'],
          'short-form extras are dropped from the catalog')
    tv_rows = hp.rows_from_items(SERIES)
    check(len(tv_rows) == 2 and all(r['category_display'] == 'TV' for r in tv_rows),
          'series items classify as TV')
    check(all('year' not in r for r in tv_rows),
          'series rows carry no year (releaseDate is the newest season)')
    check(all(r['collection'] == '' for r in film_rows + tv_rows),
          'catalog rows carry a blank collection')

    # --- assembly ------------------------------------------------------
    for r in chart_rows:
        r['collection'] = hp.CHART_NAME
    national = hp.assemble(chart_rows, film_rows, tv_rows)
    titles = [r['title'] for r in national]
    check(titles == ['The Nights Before Christmas', 'Mistletoe Murders',
                     'Mistletoe and Mimosas', 'The Way Home',
                     'My Christmas Cowboy', 'A Biltmore Christmas'],
          'chart leads the films, kinds zipper-interleave, dupes collapse to the chart row')
    check([r['rank'] for r in national] == [1, 2, 3, 4, 5, 6],
          'ranks are contiguous across the interleave')
    films = [r for r in national if r['category_display'] == 'Film']
    check([r['bucket_rank'] for r in films] == [1, 2, 3, 4],
          'film bucket ranks follow chart order then catalog order')
    charted = [r for r in national if r['collection'] == hp.CHART_NAME]
    check(len(charted) == 3 and charted[1]['title'] == 'Mistletoe and Mimosas',
          'a title on both the chart and a catalog rail keeps its chart row')
    check(sum(1 for r in national if r['title'] == 'Mistletoe and Mimosas') == 1,
          'no duplicate titles across rails')

    # --- registry ------------------------------------------------------
    from scripts.trends_scrapers import stream_estimates as se
    from scripts.trends_scrapers import derived_rails as dr
    from scripts.trends_scrapers import carriage_mix as cm
    chart = se._PUBLISHED_CHARTS.get('hallmarkplus') or {}
    check(chart.get('mode') == 'collection' and chart.get('depth') == 20,
          'published chart registered in collection mode, twenty deep')
    check(se._collection_matches(hp.CHART_NAME.lower(), list(chart.get('collections', ()))),
          "the scraper's chart name matches the registered collection")
    slugs = dict(se._STREAMING_SLUGS)
    check('hallmarkplus' in slugs, 'hallmarkplus is a registered streaming slug')
    meta = {m['key']: m for m in se._STREAMING_PLATFORMS_META}
    check('hallmarkplus' in meta and 'hallmarkplus_amazon' in meta,
          'both panels carry a platform meta entry')
    check(meta.get('hallmarkplus_amazon', {}).get('derived_from') == 'hallmarkplus',
          'the Amazon panel is marked as derived from Hallmark+')
    rail = dr.rail_for('hallmarkplus_amazon')
    check(rail is not None and rail.parent == 'hallmarkplus', 'derived rail registered')
    top = max(hi for _, hi in rail.bands.values()) if rail else 0
    check(rail is not None and meta['hallmarkplus_amazon']['ceiling'] == int(meta['hallmarkplus']['ceiling'] * top),
          'child ceiling == parent ceiling x top of band')
    check(dr.registered_ceiling_check() == [], 'registered_ceiling_check passes across the registry')
    mix = cm._MIX.get('hallmarkplus')
    check(mix is not None and mix.breakout and mix.on_amazon and 'Per title:' in mix.basis,
          'carriage mix registered with a breakout and a per-title sentence')
    check(mix is not None and abs(mix.amazon_share - rail.anchor_share) < 1e-9,
          'carriage mix centre matches the derived rail anchor share')

    # --- language ------------------------------------------------------
    import inspect
    src = inspect.getsource(hp)
    check('\u2014' not in src and '\u2013' not in src, 'no em or en dashes in the module')

    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILED')
        for f in FAILURES:
            print('  -', f)
        return 1
    print('ALL PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
