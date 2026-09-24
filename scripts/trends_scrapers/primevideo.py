"""
Amazon Prime Video trending scraper.

Requires donated cookies for `amazon.com` (same session as the Amazon
shopping site - Prime Video's storefront lives on amazon.com and
inherits the parent Amazon session).

Donate via:
    python3 scripts/trends_scrapers/donate_cookies.py --domain amazon.com

Standalone:
    python3 -m scripts.trends_scrapers.primevideo

Parser strategy
---------------
Prime Video ships hydrated content in

    <script id="dv-web-page-hydration-data" type="application/json">

with structure:

    init:
      preparations:
        body:
          containers: list[
            { title: "Popular now" | "Featured Originals ..." | ...
              entities: list[
                { displayTitle: "Every Year After",
                  entityType:   "TV Show" | "Movie",
                  link:         { url: "/gp/video/detail/B0GZ.../" },
                  titleID:      "B0GZ7FKMRR",
                  releaseYear:  "2026",
                  ...
                }
              ]
            }
          ]

Sampled 2026-07-07: 7 containers x ~20 entities each on the storefront,
so a full pull yields ~120 titles per page. We dedupe by displayTitle
across containers and rank in container order (Continue Watching,
Featured Originals, Popular Now, then genre rails), matching how
Amazon surfaces them.

Prime Video's own chart
-----------------------
One of those containers is the real thing: 'Top 10 TV shows in the US'
and 'Top 10 movies in the US' are Amazon's own published rankings, and
they arrive in the hydration blob in chart order like any other rail.
Until 2026-09-23 they were treated as just another container and then
lost, because the pull kept the first 20 deduped titles across four
pages and the storefront's Hero Carousel got there first. What shipped
as the Prime Video rail was therefore marketing placement: Neagley,
Elle, You+Me, four hero slots and a row of Featured Originals.

They now come first, in the order Amazon gives them, and
`_PUBLISHED_CHARTS` in stream_estimates reads them off the collection
name so those titles hold those positions on the board.

Which Top 10 rails hydrate varies by run: TV is reliable on
/gp/video/tv, movies appears on /gp/video/movies some runs and on the
storefront others. Every page is scanned and whatever charted rails
came back are used, so a run that only sees one still gets that one
right rather than falling back to promotional order for both.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from html import unescape
from typing import Any

from ._base import run_scraper
from ._playwright import render_pages

logger = logging.getLogger(__name__)


PRIME_URLS = [
    ('Storefront',  'https://www.amazon.com/gp/video/storefront'),
    ('Explore',     'https://www.amazon.com/gp/video/explore'),
    ('TV',          'https://www.amazon.com/gp/video/tv'),
    # /gp/video/movies renders nothing: measured 2026-09-24 it returns
    # a page with zero carousels while the storefront route below
    # returns 150. It had been in this list returning nothing for
    # however long the route has been dead.
    ('Movies',      'https://www.amazon.com/gp/video/storefront/movies'),
]


_PRIME_HYDRATE_SELECTORS = [
    'article[data-testid="card"]',
    'a[data-testid="card-title"]',
    'div[data-testid="carousel"]',
    'div[data-automation-id="hero-title"]',
]


_HYDRATION_BLOB_RE = re.compile(
    r'<script[^>]+id="dv-web-page-hydration-data"[^>]*>(.+?)</script>',
    re.DOTALL | re.IGNORECASE,
)


# Amazon's own published rankings, as the container is titled in the
# hydration blob. Matched loosely because the wording varies by page
# and by locale ('Top 10 in the US', 'Top 10 movies in the US', 'Top
# 10 TV shows in the US'), but anchored on 'top 10' plus a US marker
# so a 'Top 10 for you' personalised rail never qualifies.
_TOP10_RE = re.compile(r'\btop\s*10\b.*\bin\s+the\s+u\.?s\.?\b', re.I)

# 'Top 10 with subscriptions' has no US marker in its name but is the
# same kind of thing: what people are WATCHING, on the add-on channels
# rather than on Prime itself.
_TOP10_SUBS_RE = re.compile(r'\btop\s*10\b.*\bwith\s+subscriptions\b', re.I)

# 'Top 10 purchases in the US' is NOT a viewing chart. It ranks what
# people BOUGHT, which is a different behaviour and a different
# population, and averaging it into a viewing rail would make both
# meaningless. It matches the pattern above word for word, so it has
# to be excluded by name.
_TOP10_PURCHASES_RE = re.compile(r'\bpurchase', re.I)


def _is_published_chart_rail(rail: str) -> bool:
    r = rail or ''
    if _TOP10_PURCHASES_RE.search(r):
        return False
    return bool(_TOP10_RE.search(r) or _TOP10_SUBS_RE.search(r))


def _classify_entity(entity_type: str) -> str:
    """Prime uses 'TV Show', 'Movie', 'Live Event', 'Miniseries', etc."""
    et = (entity_type or '').lower()
    if 'movie' in et or 'film' in et:
        return 'Film'
    if 'tv' in et or 'series' in et or 'show' in et or 'episode' in et:
        return 'TV'
    if 'live' in et or 'event' in et:
        return 'Live'
    return ''


def _extract_prime_hydration(html: str) -> list[dict]:
    """Parse the dv-web-page-hydration-data blob and pull out real titles.
    Returns [] if the blob is missing (unauthenticated marketing shell)
    or malformed. Rails are traversed in on-screen order; Continue
    Watching is intentionally excluded since it's a per-user list, not
    trending.
    """
    m = _HYDRATION_BLOB_RE.search(html)
    if not m:
        return []
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    try:
        containers = obj['init']['preparations']['body']['containers']
    except (KeyError, TypeError):
        return []
    if not isinstance(containers, list):
        return []

    # Amazon's own Top 10 rails are read before anything else, so a
    # charted title is never dropped as a duplicate of the same title
    # sitting in a promotional rail further up the page.
    def _rail_name(c):
        rail = c.get('title') or c.get('text') or ''
        if isinstance(rail, dict):
            rail = rail.get('text') or rail.get('displayText') or ''
        return str(rail).strip()

    ordered = (
        [c for c in containers
         if isinstance(c, dict) and _is_published_chart_rail(_rail_name(c))]
        + [c for c in containers
           if isinstance(c, dict)
           and not _is_published_chart_rail(_rail_name(c))])

    seen: set[str] = set()
    out: list[dict] = []
    for c in ordered:
        if not isinstance(c, dict):
            continue
        rail = _rail_name(c)
        if c.get('isContinueWatching'):
            continue
        entities = c.get('entities') or []
        if not isinstance(entities, list):
            continue
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            title = ent.get('displayTitle')
            if not isinstance(title, str):
                title = ent.get('title')
            if not isinstance(title, str):
                continue
            title = title.strip()
            if len(title) < 2 or len(title) > 220:
                continue
            key = title.lower()
            if key in seen:
                continue
            link = ent.get('link') or {}
            url_path = ''
            if isinstance(link, dict):
                url_path = link.get('url') or ''
            if not isinstance(url_path, str):
                url_path = ''
            if url_path.startswith('/'):
                url = f'https://www.amazon.com{url_path.split("?")[0]}'
            elif url_path.startswith('http'):
                url = url_path.split('?')[0]
            elif ent.get('titleID'):
                url = f'https://www.amazon.com/gp/video/detail/{ent["titleID"]}'
            else:
                continue

            seen.add(key)
            out.append({
                'rank':             len(out) + 1,
                'title':            title,
                'url':              url,
                'category_display': _classify_entity(ent.get('entityType') or ''),
                'collection':       rail,
            })
    return out


# Pure-DOM fallback in case Amazon ships a different hydration shape one
# day. Matches <a href="/gp/video/detail/..." aria-label="Show name">.
_PRIME_CARD_RE = re.compile(
    r'<a[^>]+href="(/gp/video/detail/[A-Z0-9]+/[^"]+)"[^>]*'
    r'aria-label="([^"]{2,180})"',
    re.IGNORECASE,
)


def _extract_from_dom(html: str, limit: int = 20) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for m in _PRIME_CARD_RE.finditer(html):
        href  = m.group(1)
        title = unescape(m.group(2)).strip()
        key = title.lower()
        if key in seen or len(title) < 3:
            continue
        seen.add(key)
        url = f'https://www.amazon.com{href.split("?")[0]}'
        out.append({
            'rank':             len(out) + 1,
            'title':            title,
            'url':              url,
            'category_display': '',
            'collection':       '',
        })
        if len(out) >= limit:
            break
    return out


# ────────────────────────────────────────────────────────────────────
# The Top 10 rails are not in the hydration blob
# ────────────────────────────────────────────────────────────────────
# Measured 2026-09-24 on a signed-in storefront: the blob carries
# SEVEN containers, all above the fold (Carousel Title, On now, Fan
# favorites, Top-rated movies, Action and adventure movies, Featured
# Originals and Exclusives, Your live and upcoming events). The Top 10
# rails render further down and arrive later, so a reader that only
# parses the blob finds no chart and correctly reports that it found
# none. That is why the declaration in `_PUBLISHED_CHARTS` had nothing
# to read: the chart was never collected, not mis-matched.
#
# So the page is scrolled and the rails are read from the DOM. Rail
# identity comes from `[data-testid="carousel-title"]`, not from the
# heading's text: the H2's textContent is the rail name with the
# trending icon's label run onto the end of it ('Top 10 in the US
# Trending'), which is the same lesson HBO Max taught, where the
# visible heading and the published name were different strings.
#
# Charts are read from the STOREFRONT only. The movies storefront
# carries a rail named 'Top 10 in the US' as well, so reading both
# pages would put twenty rows in one collection and number them 1 to
# 20. One page, one chart.
_CHART_PAGES = frozenset({'Storefront'})

# A page that only ever shows one kind tells us the kind of anything on
# it. Used as the LAST resort for a chart row that carries no badge and
# is not in the hydration blob, so the row still keys to the right half
# of the estimates store instead of landing in a third key space of its
# own where nothing downstream will find it again.
_PAGE_KIND = {'TV': 'TV', 'Movies': 'Film'}

_RAILS_JS = r"""() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const el of document.querySelectorAll('[data-testid="carousel-title"]')) {
    const name = clean(el.textContent || '');
    if (!name) continue;
    let node = el, n = 0;
    for (let i = 0; i < 8 && node.parentElement; i++) {
      node = node.parentElement;
      n = node.querySelectorAll('a[href*="/gp/video/detail/"]').length;
      if (n >= 3) break;
    }
    if (n < 3) continue;
    const rows = [];
    for (const a of node.querySelectorAll('a[href*="/gp/video/detail/"]')) {
      const title = clean(a.textContent || '');
      if (!title) continue;
      const card = a.closest('article,[data-testid="card"]') || a.parentElement;
      rows.push({title: title,
                 href: (a.getAttribute('href') || '').split('?')[0],
                 badge: clean((card || {}).textContent || '').slice(0, 120)});
    }
    if (rows.length) out.push({rail: name, rows: rows});
  }
  return JSON.stringify(out);
}"""


def _collect_rails(page, label: str) -> str:
    """Scroll the page, then hand back the DOM rails alongside its HTML.

    The HTML still goes through the hydration parser for the catalog,
    so this only ADDS the charts. A page that never reveals one yields
    an empty list and the pull is unchanged.
    """
    for _ in range(18):
        try:
            page.mouse.wheel(0, 1200)
        except Exception:
            break
        page.wait_for_timeout(900)
    try:
        rails = page.evaluate(_RAILS_JS)
    except Exception as e:  # noqa: BLE001
        logger.info("primevideo %s: rail read failed (%s)", label, e)
        rails = '[]'
    return json.dumps({'rails': rails, 'html': page.content()})


def _kind_from_badge(badge: str) -> str:
    """Prime marks a card NEW MOVIE / NEW SERIES / NEW SEASON.

    Only the badge is read here. Guessing from a title is how a film
    and a series that share a name end up as one row.
    """
    b = (badge or '').upper()
    if 'MOVIE' in b:
        return 'Film'
    if 'SERIES' in b or 'SEASON' in b or 'EPISODE' in b:
        return 'TV'
    return ''


def _chart_rows_from_rails(rails_json: str, label: str,
                           kind_by_title: dict) -> list[dict]:
    """The chart rails on one page, in the order Amazon renders them."""
    try:
        rails = json.loads(rails_json or '[]')
    except (TypeError, json.JSONDecodeError):
        return []
    out: list[dict] = []
    for rail in rails:
        name = (rail.get('rail') or '').strip()
        if not _is_published_chart_rail(name):
            continue
        for i, row in enumerate(rail.get('rows') or [], 1):
            title = (row.get('title') or '').strip()
            if not (2 <= len(title) <= 220):
                continue
            href = row.get('href') or ''
            kind = (_kind_from_badge(row.get('badge') or '')
                    or kind_by_title.get(title.lower(), ''))
            out.append({
                'rank':             i,
                'title':            title,
                'url':              (f'https://www.amazon.com{href}'
                                     if href.startswith('/')
                                     else 'https://www.amazon.com/'),
                'category_display': kind,
                'collection':       name,
            })
        logger.info("primevideo %s: chart rail %r -> %d row(s)",
                    label, name, len(rail.get('rows') or []))
    return out


def fetch() -> dict[str, Any]:
    rendered = render_pages(PRIME_URLS,
                             homepage='https://www.amazon.com/',
                             cookie_domain='amazon.com',
                             wait_selectors=_PRIME_HYDRATE_SELECTORS,
                             hydration_wait_ms=10000,
                             assert_signed_in='amazon.com',
                             page_hook=_collect_rails)

    # Unpack the hook's record back into (label, rails, html).
    pages: list[tuple[str, str, str]] = []
    for label, payload in rendered:
        try:
            d = json.loads(payload)
            pages.append((label, d.get('rails') or '[]', d.get('html') or ''))
        except (TypeError, json.JSONDecodeError):
            pages.append((label, '[]', payload))

    # Film or TV for every title the hydration blob knows about, so a
    # chart row with no badge can still be keyed to the right kind.
    kind_by_title: dict[str, str] = {}
    hydrated: list[tuple[str, list[dict]]] = []
    for label, _rails, html in pages:
        items = _extract_prime_hydration(html)
        if not items:
            items = _extract_from_dom(html, limit=25)
        hydrated.append((label, items))
        for it in items:
            k = it.get('category_display')
            if k:
                kind_by_title.setdefault(it['title'].lower(), k)

    # Then the single-kind pages, which settle anything the blob and
    # the badges between them could not.
    for label, rails, _html in pages:
        page_kind = _PAGE_KIND.get(label)
        if not page_kind:
            continue
        try:
            for rail in json.loads(rails or '[]'):
                for row in rail.get('rows') or []:
                    t = (row.get('title') or '').strip().lower()
                    if t:
                        kind_by_title.setdefault(t, page_kind)
        except (TypeError, json.JSONDecodeError):
            continue

    charted: list[dict] = []
    rest: list[dict] = []
    seen: set[str] = set()

    # The charts first, so a charted title is never dropped later as a
    # duplicate of the same title sitting in a promotional rail.
    for label, rails, _html in pages:
        if label not in _CHART_PAGES:
            continue
        for it in _chart_rows_from_rails(rails, label, kind_by_title):
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            charted.append(it)

    for (label, items), (_l, _r, html) in zip(hydrated, pages):
        n_chart = 0
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            if _is_published_chart_rail(it['collection']):
                charted.append(it)
                n_chart += 1
            else:
                rest.append(it)
        logger.info("primevideo %s: parsed %d titles from %d-byte HTML "
                     "(%d on a published Top 10 rail)",
                     label, len(items), len(html), n_chart)

    if not charted:
        # Not a failure: Amazon does not hydrate the Top 10 rails on
        # every run. The pull still ships, and the collector treats
        # every row as an unranked listing rather than inventing an
        # order, which is the honest reading of a page with no chart
        # on it.
        logger.warning("primevideo: no Top 10 rail hydrated this run; "
                        "shipping the storefront with no chart")

    # 60 rather than 20. The two Top 10 rails alone are 20 rows, and
    # cutting at 20 was what buried the chart under the Hero Carousel
    # in the first place.
    all_items = charted + rest
    for i, it in enumerate(all_items[:60], start=1):
        it['rank'] = i
    logger.info("primevideo: %d titles, %d of them on a published "
                 "Top 10 rail", min(len(all_items), 60), len(charted))
    return {'national': all_items[:60]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('primevideo', 'Prime Video', 'streaming', fetch)
    print(f"primevideo: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
