"""
Hallmark+ catalog and Hallmark+'s own 'Top Movies Today' chart.

Hallmark+ (Hallmark Media's subscription service, the September 2024
rebrand of Hallmark Movies Now, $7.99 a month, US only) runs on the
Accedo One platform. The web app at hallmarkplus.com is a thin SPA; the
pages it renders are served as JSON by the site's own API and that JSON
is served to an anonymous visitor exactly as it is to a member:

    GET /api/core/menu                       the navigation, page ids
    GET /api/core/page/<pageId>              a page: its rails + feeds
    GET /api/core/catalog/collection/<id>    one rail's titles, paged

So the catalog is read the same way the app reads it, with no HTML to
parse and no session. The movies page lists about two dozen rails
(Top Movies Today, Christmas Classics, Popular Mystery Movies, Recently
Added Movies, Hallmark Hall of Fame, ...) and the series page about ten
(Hallmark Original Series, Mystery, Drama, Holiday, Reality TV With
Heart, British Mysteries, ...). Every rail is read, every page of every
rail, and the titles are deduplicated with first occurrence winning.

THE CHART
---------
Hallmark+ publishes one ranking of its own. The movies page carries a
rail headed 'Top Movies Today', and the collection behind it describes
itself as 'Dynamic, Top 20 Movies Today': dynamic, so the service
computes it; today, so it is dated; and it is twenty deep. It is the
only rail on the service whose order means anything. Checked
2026-11-18 it ran five new Countdown to Christmas premieres, then A
Biltmore Christmas (2023), A Shoe Addict's Christmas (2018), Christmas
Under Wraps (2014) and Miss Christmas (2017) beside them: a mix of this
season's premieres and catalog the audience went back to, which is
what viewing looks like and what a merchandising shelf does not.

Films only, by construction: Hallmark+ publishes no series ranking.
The series page is browse rails, and the order of 'Hallmark Original
Series' is the service's own shelf (Mistletoe Murders, The Way Home,
Hope Valley: 1874, When Calls the Heart), which is exactly what a
service merchandises hardest. It is not read as a chart, the same call
made against Starz's 'Popular' series rail.

The chart rows lead `national` and carry `collection = CHART_NAME`;
every other row carries a blank collection, which is what
`stream_estimates._PUBLISHED_CHARTS['hallmarkplus']` keys on. The
chart is looked up by its rail heading on the movies page first, so a
re-published collection id does not lose it; the id seen on 2026-11-18
is the fallback.

WHERE IT RUNS
-------------
Residentially. From the build box the API answers 301 to
corporate.hallmarkmedia.com/landing ('Hallmark+ Availability'), the
geo wall for a non-US address; from a US residential address the same
request returns the JSON. It sits in `local_residential_run.py` with
BritBox, MGM+ and Starz. No cookies and no session are needed for any
of the reads above. Jenna's own Hallmark+ login (2026-11-18) is not
used here and nothing in this module signs in; if the service ever
gates the chart behind membership, store the credential with
`trends_login_store set hallmarkplus.com` and add a recipe, never
paste it into code.

Standalone:
    python3 -m scripts.trends_scrapers.hallmarkplus
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from html import unescape
from typing import Any, Optional

from ._base import http_get, run_scraper

logger = logging.getLogger(__name__)


SLUG  = 'hallmarkplus'
LABEL = 'Hallmark+'

SITE = 'https://www.hallmarkplus.com'
API  = SITE + '/api/core'

CHART_NAME = 'Hallmark+ Top Movies Today'
# The rail heading on the movies page, matched case-insensitively.
_CHART_RAIL_TITLE = 'top movies today'
# The collection behind that rail as served on 2026-11-18. Fallback
# only; the heading lookup above wins.
_CHART_COLLECTION_ID = '01JCK9EPR7XN5CXMZWSPMM83XA'
_CHART_DEPTH = 20
_CHART_MIN_HEALTHY = 10

# Page ids as served on 2026-11-18. The menu is read first and these
# are the fallback when the menu cannot be read or has moved a page.
_PAGE_IDS = {
    'movies': '01HYEGMR9J4669T67EMZ9DT50K',
    'series': '01HYEGMR9JWPDERZVVZNSBHASQ',
}
_PAGE_KIND = {'movies': 'Film', 'series': 'TV'}

# Rails whose heading says they are not catalog: the hero carousel and
# spotlight are marketing placements of titles already in the rails
# below them, Coming Soon is not yet watchable, and the two
# personalised rails are empty for an anonymous visitor.
_SKIP_RAIL_WORDS = ('hero', 'spotlight', 'coming soon', 'continue watching',
                    'recommended for you')

_PAGE_SIZE = 50
_MAX_PAGES_PER_RAIL = 6          # 300 titles; no rail is that deep today
_PER_KIND_LIMIT = 150

_HEADERS = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'en-US,en;q=0.9',
    'referer': SITE + '/',
    'origin': SITE,
}

_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = f'trends_iq_snapshots/latest/{SLUG}.json'


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _get_json(url: str, *, timeout: int = 30) -> Optional[Any]:
    r = http_get(url, headers=_HEADERS, timeout=timeout, retries=2)
    if r is None:
        logger.warning('%s: no response from %s', SLUG, url)
        return None
    status = getattr(r, 'status_code', 0)
    if status != 200:
        # 301 to corporate.hallmarkmedia.com/landing is the geo wall:
        # the host is not reading from a US address.
        logger.warning('%s: HTTP %s from %s', SLUG, status, url)
        return None
    try:
        return r.json() if hasattr(r, 'json') else json.loads(r.text)
    except Exception as e:  # noqa: BLE001
        logger.warning('%s: non-JSON body from %s (%s)', SLUG, url, e)
        return None


# ---------------------------------------------------------------------------
# Page structure
# ---------------------------------------------------------------------------
def page_ids_from_menu(menu: Any) -> dict[str, str]:
    """`{'movies': <pageId>, 'series': <pageId>}` from the menu JSON.

    The menu is served per form factor (`desktop`, `mobile`, ...), each
    a tree of entries shaped

        {"display": {"title": "Movies"},
         "action":  {"kind": "NAVIGATE_TO_PAGE",
                     "parameters": {"pageId": "01HY..."}}}

    The two we need are headed 'Movies' and 'Series'; every form
    factor points them at the same pages. Any entry not found keeps
    the fallback id.
    """
    out = dict(_PAGE_IDS)
    want = {'movies': 'movies', 'series': 'series'}

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            disp = node.get('display') if isinstance(node.get('display'), dict) else {}
            title = str(disp.get('title') or node.get('title')
                        or node.get('label') or '').strip().lower()
            act = node.get('action') if isinstance(node.get('action'), dict) else {}
            params = act.get('parameters') if isinstance(act.get('parameters'), dict) else {}
            pid = params.get('pageId') or node.get('pageId')
            if title in want and isinstance(pid, str) and pid:
                out[want[title]] = pid
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)

    _walk(menu)
    return out


def rails_from_page(page: Any) -> list[tuple[str, str]]:
    """`[(rail heading, feed url), ...]` in page order.

    A page is `containerCollections[*].containers[*]`; each container
    carries `data.feed` (the collection URL) and a per-form-factor
    `templates.<ff>.properties.title`. The desktop title is read, then
    any form factor that has one.
    """
    out: list[tuple[str, str]] = []
    if not isinstance(page, dict):
        return out
    root = page.get('page') if isinstance(page.get('page'), dict) else page
    for coll in root.get('containerCollections') or []:
        if not isinstance(coll, dict):
            continue
        for c in coll.get('containers') or []:
            if not isinstance(c, dict):
                continue
            feed = str(((c.get('data') or {}).get('feed')) or '').strip()
            if not feed.startswith('http'):
                continue
            title = ''
            tpls = c.get('templates') or {}
            for ff in ('desktop', 'tv', 'tablet', 'mobile'):
                props = ((tpls.get(ff) or {}).get('properties')) or {}
                t = props.get('title')
                if isinstance(t, str) and t.strip():
                    title = unescape(t).strip()
                    break
            out.append((title, feed))
    return out


def _is_catalog_rail(title: str) -> bool:
    t = (title or '').lower()
    return not any(w in t for w in _SKIP_RAIL_WORDS)


def _collection_id(feed: str) -> str:
    tail = feed.split('/collection/', 1)[-1]
    return tail.split('?', 1)[0].split('/', 1)[0].strip()


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------
def _classify(item: dict) -> str:
    """A film is an ITEM whose subtype is VIDEO (or MOVIE); a series is
    a COLLECTION whose subtype is TV_SHOW. Everything else on a rail
    (a nested collection of collections, a star's page, a trailer)
    is not a title and returns ''."""
    typ = str(item.get('type') or '').upper()
    sub = str(item.get('subtype') or '').upper()
    cat = str(((item.get('extras') or {}).get('category')) or '').upper()
    if cat and cat not in ('LONG', ''):
        # SHORT is a trailer or featurette, never a title.
        return ''
    if typ == 'ITEM' and sub in ('VIDEO', 'MOVIE', 'FILM', ''):
        return 'Film'
    if typ == 'COLLECTION' and sub in ('TV_SHOW', 'SERIES', 'SHOW'):
        return 'TV'
    return ''


def _poster(item: dict) -> str:
    images = item.get('images') or {}
    for group in ('poster', 'thumbnail', 'backdrop', 'still'):
        g = images.get(group) or {}
        if not isinstance(g, dict):
            continue
        for orient in ('portrait', 'landscape'):
            for e in g.get(orient) or []:
                u = (e or {}).get('url') if isinstance(e, dict) else None
                if isinstance(u, str) and u.startswith('http'):
                    return u
    return ''


def _detail_url(item: dict) -> str:
    share = ((item.get('refs') or {}).get('share')) or ''
    if isinstance(share, str) and share.startswith('http'):
        return share
    return SITE + '/'


def _year(item: dict) -> Optional[int]:
    rd = item.get('releaseDate')
    if isinstance(rd, str) and len(rd) >= 4 and rd[:4].isdigit():
        return int(rd[:4])
    return None


def rows_from_items(items: list, kind_hint: str = '') -> list[dict]:
    """Dashboard-shaped rows from a collection's `data` list, in order.
    `kind_hint` ('Film' / 'TV') fills in when the item carries no
    usable type, which the chart's own items always do."""
    out: list[dict] = []
    seen: set[str] = set()
    for it in items or []:
        if not isinstance(it, dict):
            continue
        title = it.get('title')
        if not isinstance(title, str):
            continue
        title = unescape(title).strip()
        if not (2 <= len(title) <= 200):
            continue
        kind = _classify(it) or kind_hint
        if not kind:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        row: dict[str, Any] = {
            'title':            title,
            'url':              _detail_url(it),
            'category_display': kind,
            'image':            _poster(it),
            'collection':       '',
            'content_id':       str(it.get('id') or ''),
        }
        # A film's releaseDate is its premiere. A series carries the
        # date of its newest (or next) season instead, which reads as
        # a year in the future for a returning show, so no year there.
        y = _year(it) if kind == 'Film' else None
        if y:
            row['year'] = y
        genres = it.get('genres')
        if isinstance(genres, list) and genres:
            row['genres'] = [str(g) for g in genres if isinstance(g, str)][:6]
        out.append(row)
    return out


def _fetch_collection(coll_id: str, *, max_pages: int = _MAX_PAGES_PER_RAIL
                      ) -> list[dict]:
    """Every item of a collection across its pages. Stops at the
    service's own `hasMore` or the page cap, whichever comes first."""
    items: list[dict] = []
    for page_no in range(1, max_pages + 1):
        url = (f'{API}/catalog/collection/{coll_id}'
               f'?locale=en&pageSize={_PAGE_SIZE}&page={page_no}')
        data = _get_json(url)
        if not isinstance(data, dict):
            break
        batch = data.get('data') or []
        if not isinstance(batch, list) or not batch:
            break
        items.extend(batch)
        info = data.get('pageInfo') or {}
        if not info.get('hasMore'):
            break
        time.sleep(0.25)
    return items


# ---------------------------------------------------------------------------
# Previous snapshot (never overwrite a healthy panel with nothing)
# ---------------------------------------------------------------------------
def _load_previous_snapshot() -> dict:
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(Bucket=_S3_BUCKET, Key=_S3_LATEST)
        d = json.loads(o['Body'].read().decode('utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.info('%s: no previous snapshot (%s)', SLUG, e)
        return {}


def _mark_cookie_gap(reason: str) -> None:
    try:
        from .cookie_gap_notify import notify_cookie_gap
        notify_cookie_gap(SLUG, 'hallmarkplus.com', reason=reason)
    except Exception as e:  # noqa: BLE001
        logger.info('cookie_gap notify failed for %s: %s', SLUG, e)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------
def assemble(chart_rows: list[dict], film_rows: list[dict],
             tv_rows: list[dict]) -> list[dict]:
    """One `national` list: the chart leads the films, then the rest of
    each kind in rail order, zipper-interleaved so a consumer slicing
    `national[:25]` sees both columns (the shape every streaming
    snapshot writes). First occurrence wins, which is what keeps a
    charted title from also appearing as a catalog row."""
    seen: set[str] = set()
    films: list[dict] = []
    tv: list[dict] = []
    for r in chart_rows:
        k = r['title'].lower()
        if k in seen:
            continue
        seen.add(k)
        films.append(r)
    for r in film_rows:
        k = r['title'].lower()
        if k in seen:
            continue
        seen.add(k)
        films.append(r)
    for r in tv_rows:
        k = r['title'].lower()
        if k in seen:
            continue
        seen.add(k)
        tv.append(r)
    films = films[:_PER_KIND_LIMIT]
    tv = tv[:_PER_KIND_LIMIT]
    for i, r in enumerate(films, 1):
        r['bucket_rank'] = i
    for i, r in enumerate(tv, 1):
        r['bucket_rank'] = i

    out: list[dict] = []
    i = j = 0
    while i < len(films) or j < len(tv):
        if i < len(films):
            out.append(films[i]); i += 1
        if j < len(tv):
            out.append(tv[j]); j += 1
    for k, r in enumerate(out, 1):
        r['rank'] = k
    return out


def fetch() -> dict[str, Any]:
    menu = _get_json(f'{API}/menu')
    page_ids = page_ids_from_menu(menu) if menu is not None else dict(_PAGE_IDS)

    chart_feed_id = ''
    film_rows: list[dict] = []
    tv_rows: list[dict] = []
    rails_read = 0
    pages_read = 0

    for page_key in ('movies', 'series'):
        kind = _PAGE_KIND[page_key]
        page = _get_json(f'{API}/page/{page_ids[page_key]}')
        rails = rails_from_page(page) if page is not None else []
        if not rails:
            logger.warning('%s: %s page returned no rails', SLUG, page_key)
            continue
        pages_read += 1
        for title, feed in rails:
            coll_id = _collection_id(feed)
            if not coll_id:
                continue
            if page_key == 'movies' and title.lower() == _CHART_RAIL_TITLE:
                chart_feed_id = coll_id
                continue            # read below, as the chart
            if not _is_catalog_rail(title):
                continue
            items = _fetch_collection(coll_id)
            rows = rows_from_items(items)
            rails_read += 1
            logger.info('%s: %s rail %r -> %d row(s)', SLUG, page_key,
                        title or coll_id, len(rows))
            (film_rows if kind == 'Film' else tv_rows).extend(
                r for r in rows if r['category_display'] == kind)
            time.sleep(0.2)

    # The chart. Looked up by heading above; the id seen on 2026-11-18
    # is the fallback so a page that failed to read still gets it.
    chart_items = _fetch_collection(chart_feed_id or _CHART_COLLECTION_ID,
                                    max_pages=1)
    chart_rows = rows_from_items(chart_items, kind_hint='Film')[:_CHART_DEPTH]
    for r in chart_rows:
        r['collection'] = CHART_NAME
    chart_ok = len(chart_rows) >= _CHART_MIN_HEALTHY
    logger.info('%s: %r -> %d row(s)%s', SLUG, CHART_NAME, len(chart_rows),
                '' if chart_ok else ' (below the health floor)')

    if pages_read and (film_rows or tv_rows or chart_ok):
        payload: dict[str, Any] = {
            'national': assemble(chart_rows if chart_ok else [],
                                 film_rows, tv_rows),
            'rails_read': rails_read,
        }
        if chart_ok:
            payload['chart_rails'] = [CHART_NAME]
            payload['chart_positions'] = len(chart_rows)
            payload['chart_captured_at'] = datetime.now(timezone.utc).isoformat()
        else:
            # The catalog read but the chart did not. Carry the last
            # published chart forward at the head so the service does
            # not render chartless on one bad read; the estimator's own
            # carry window (`_CHART_CARRY_DAYS`) still bounds it.
            prev = _load_previous_snapshot()
            prev_chart = [r for r in (prev.get('national') or [])
                          if isinstance(r, dict)
                          and r.get('collection') == CHART_NAME]
            if len(prev_chart) >= _CHART_MIN_HEALTHY:
                payload['national'] = assemble(prev_chart, film_rows, tv_rows)
                payload['chart_rails'] = [CHART_NAME]
                payload['chart_positions'] = len(prev_chart)
                payload['chart_captured_at'] = prev.get('chart_captured_at')
                payload['chart_stale_from_previous'] = True
        logger.info('%s: %d row(s) across %d rail(s) on %d page(s)', SLUG,
                    len(payload['national']), rails_read, pages_read)
        return payload

    reason = (f'{SLUG}: read {pages_read} page(s), {rails_read} rail(s), '
              f'{len(chart_rows)} chart row(s) - geo wall (301 to the '
              f'availability page means a non-US address), API moved, or '
              f'bot wall?')
    logger.warning(reason)
    _mark_cookie_gap(reason)
    prev = _load_previous_snapshot()
    prev_rows = prev.get('national') or []
    if prev_rows:
        logger.warning('%s: preserving previous snapshot (%d rows)', SLUG,
                       len(prev_rows))
        out: dict[str, Any] = {'national': prev_rows,
                               'stale_from_previous': True,
                               'soft_block_reason': reason}
        for k in ('chart_rails', 'chart_positions', 'chart_captured_at'):
            if prev.get(k) is not None:
                out[k] = prev[k]
        return out
    return {'national': [], 'soft_block_reason': reason}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SLUG, LABEL, 'streaming', fetch)
    rows = result.get('national') or []
    print(f"{SLUG}: {len(rows)} items  chart={result.get('chart_positions')}  "
          f"error={result.get('error')}", file=sys.stderr)
    for r in rows:
        if r.get('collection') == CHART_NAME:
            print(f"  #{r.get('bucket_rank'):>2} {r['title']}", file=sys.stderr)
