"""
FAST channel top-titles scraper.

Covers the four largest US Free Ad-Supported Streaming TV (FAST)
platforms in one daily snapshot:

    - The Roku Channel  (JustWatch package: rkc)
    - Tubi              (JustWatch package: tbv)
    - Pluto TV          (JustWatch package: ptv)
    - Amazon            (JustWatch package: amp with monetization
                         filter [FREE] and post-filter to drop any
                         title that ALSO has FLATRATE on Amazon)

For the first three platforms the whole catalog is FAST by
definition, so a single `popularTitles(packages: [<pkg>])` call
returns exactly what we want.

Amazon needs extra work: the `amp` package covers Prime Video's
entire catalog including premium subscription-only titles
(Ted Lasso, Reacher, Lioness, etc). To isolate the actual "Amazon
Live TV" / ex-Freevee FAST surface we filter to
`monetizationTypes: [FREE]` at the GraphQL layer AND then drop any
result whose offers still include `FLATRATE` on the Amazon package
- those are premium subscription titles that happen to have a free
pilot episode and would otherwise pollute the FAST feed. What
survives is the pure-FAST catalog: The Westies, Black Sails,
Yellowjackets, Killing Eve, Interview with the Vampire, etc.

Data source: JustWatch's public GraphQL (`apis.justwatch.com/graphql`).
Same endpoint their web app hits for provider pages - no auth, no
cookies, and no IP fingerprint headaches from Hetzner (same proven
pattern that unblocks the Hulu fallback). Popularity ranking mirrors
JustWatch's "Popular Now" default which reflects real US consumer
behavior across the JustWatch panel.

Snapshot shape (single `fast_channels.json` for all four platforms,
mirroring `music_charts.py`):

    {
      "kind":     "fast",
      "label":    "FAST",
      "national": [ ...flat top-40 across all four for run_all counts... ],
      "sources": {
        "roku":   { "label": "The Roku Channel", "items": [100 items], "available": true },
        "tubi":   {...},
        "pluto":  {...},
        "amazon": {...}
      }
    }

Each item carries: rank, title, category_display (Film/TV), year,
genres, description, url (JustWatch title page), image (poster URL),
justwatch_id.

Standalone:

    python3 -m scripts.trends_scrapers.fast_channels
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Optional
from urllib import request as _urllib_request

from ._base import run_scraper

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# JustWatch GraphQL
# ────────────────────────────────────────────────────────────────────
_JW_GRAPHQL_URL = 'https://apis.justwatch.com/graphql'
_JW_IMAGE_HOST  = 'https://images.justwatch.com'
_JW_TITLE_HOST  = 'https://www.justwatch.com'

# Poster profile sizes. `s276` (~276px wide) is what JustWatch's own
# provider pages request; matches the tile size we render in the
# dashboard.
_POSTER_PROFILE = 's276'
_POSTER_FORMAT  = 'jpg'

# Per-platform: (sub-source slug, dashboard label, JustWatch package code,
#                fast_only mode). fast_only=True means the whole catalog on
# that provider is FAST content, so no monetization filter is needed. For
# Amazon we set fast_only=False so the fetcher applies FREE-only +
# FLATRATE-exclusion logic to isolate Amazon Live TV / ex-Freevee content.
FAST_PLATFORMS: list[tuple[str, str, str, bool]] = [
    ('roku',    'The Roku Channel', 'rkc', True),
    ('tubi',    'Tubi',             'tbv', True),
    ('pluto',   'Pluto TV',         'ptv', True),
    # 'amp' = Prime Video. Filter down to Amazon's FAST content via
    # monetizationTypes + FLATRATE exclusion. See _fetch_amazon_fast.
    ('amazon',  'Amazon',           'amp', False),
]

# 100 titles per platform after any filtering. JustWatch's GraphQL
# caps `first` at 100; beyond that we use cursor pagination.
_PER_PLATFORM_LIMIT = 100

# How many Amazon FREE-tier titles to page through before giving up on
# hitting `_PER_PLATFORM_LIMIT` pure-FAST results. 4 pages (400 titles)
# is plenty - the FREE monetization pool on Amazon is ~356 titles as
# of Aug 2026, and roughly 60% survive the FLATRATE-exclusion filter,
# so we typically finish inside 2-3 pages.
_AMAZON_MAX_PAGES = 4

# JustWatch package clear-name prefix used to identify Amazon offers
# when post-filtering the FREE pool. Matches "Amazon Prime Video",
# "Amazon Video", and "Amazon Freevee" - anything Amazon-owned.
_AMAZON_OFFER_PREFIX = 'amazon'


# Standard query (Roku / Tubi / Pluto): whole-catalog popularity.
_JW_QUERY = """
query FASTPopular($country: Country!, $providers: [String!], $first: Int!) {
  popularTitles(country: $country, first: $first,
                filter: {packages: $providers, objectTypes: [SHOW, MOVIE]},
                sortBy: POPULAR, sortRandomSeed: 0) {
    edges {
      node {
        objectId
        objectType
        content(country: $country, language: "en") {
          title
          shortDescription
          fullPath
          posterUrl
          originalReleaseYear
          genres { translation(language: "en") }
        }
      }
    }
    totalCount
  }
}
""".strip()


# Amazon-specific query: adds monetization filter + returns offers so
# we can post-filter out subscription-only content. Uses `offset` for
# pagination since we need more than 100 candidates to hit our target
# after filtering.
_JW_QUERY_AMAZON_FAST = """
query FASTAmazonFree($country: Country!, $providers: [String!], $first: Int!, $offset: Int) {
  popularTitles(country: $country, first: $first, offset: $offset,
                filter: {packages: $providers, objectTypes: [SHOW, MOVIE],
                          monetizationTypes: [FREE]},
                sortBy: POPULAR, sortRandomSeed: 0) {
    edges {
      node {
        objectId
        objectType
        content(country: $country, language: "en") {
          title
          shortDescription
          fullPath
          posterUrl
          originalReleaseYear
          genres { translation(language: "en") }
        }
        offers(country: $country, platform: WEB) {
          package { clearName }
          monetizationType
        }
      }
    }
    pageInfo { hasNextPage }
    totalCount
  }
}
""".strip()


def _poster_url(template: str) -> str:
    """Turn JustWatch's poster template ("/poster/<id>/{profile}/<slug>.{format}")
    into a fully-qualified https URL."""
    if not template:
        return ''
    path = (template
             .replace('{profile}', _POSTER_PROFILE)
             .replace('{format}',  _POSTER_FORMAT))
    if not path.startswith('/'):
        path = '/' + path
    return _JW_IMAGE_HOST + path


def _classify_kind(object_type: str) -> str:
    ot = (object_type or '').upper()
    if ot == 'MOVIE':
        return 'Film'
    if ot == 'SHOW':
        return 'TV'
    return ''


_JW_HEADERS = {
    'Content-Type':   'application/json',
    'Accept':         'application/json',
    'User-Agent':     ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/126.0.0.0 Safari/537.36'),
    'Origin':         _JW_TITLE_HOST,
    'Referer':        _JW_TITLE_HOST + '/',
}


def _post_graphql(query: str, variables: dict, op_name: str) -> Optional[dict]:
    """POST to JustWatch's GraphQL. Returns the `data` dict, or None on
    request/parse failure. Callers must still check for `errors` in the
    original payload if that matters."""
    body = json.dumps({
        'query':         query,
        'variables':     variables,
        'operationName': op_name,
    }).encode('utf-8')
    req = _urllib_request.Request(_JW_GRAPHQL_URL, data=body,
                                    headers=_JW_HEADERS)
    try:
        with _urllib_request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode('utf-8', 'replace'))
    except Exception as e:
        logger.warning("fast_channels: graphql request failed: %s", e)
        return None


def _normalize_node(node: dict) -> Optional[dict]:
    """Turn a JustWatch popularTitles edge node into our dashboard row
    shape. Returns None for empty/malformed nodes."""
    content = node.get('content') or {}
    title   = (content.get('title') or '').strip()
    if not title:
        return None

    genres = []
    for g in content.get('genres') or []:
        t = (g or {}).get('translation')
        if t:
            genres.append(t)

    full_path = content.get('fullPath') or ''
    url = _JW_TITLE_HOST + full_path if full_path else ''

    return {
        'title':            title,
        'category_display': _classify_kind(node.get('objectType')),
        'year':             content.get('originalReleaseYear'),
        'genres':           genres[:4],
        'description':      content.get('shortDescription') or '',
        'url':              url,
        'image':            _poster_url(content.get('posterUrl') or ''),
        'justwatch_id':     node.get('objectId'),
    }


def _fetch_platform_whole_catalog(pkg: str, label: str, limit: int) -> list[dict]:
    """Roku Channel / Tubi / Pluto TV: whole catalog is FAST by
    definition, so a single popularity query is sufficient."""
    data = _post_graphql(_JW_QUERY,
                          {'country': 'US', 'providers': [pkg], 'first': limit},
                          'FASTPopular')
    if not data:
        return []
    if data.get('errors'):
        logger.warning("fast_channels %s (%s): GraphQL errors: %s",
                        label, pkg, json.dumps(data['errors'])[:200])
        return []
    pop   = ((data.get('data') or {}).get('popularTitles') or {})
    edges = pop.get('edges') or []
    out: list[dict] = []
    seen: set[str] = set()
    for e in edges:
        row = _normalize_node(e.get('node') or {})
        if not row:
            continue
        key = row['title'].lower()
        if key in seen:
            continue
        seen.add(key)
        row['rank'] = len(out) + 1
        out.append(row)
        if len(out) >= limit:
            break
    logger.info("fast_channels %s (%s): parsed %d items (total pool=%s)",
                 label, pkg, len(out), pop.get('totalCount'))
    return out


def _fetch_platform_amazon_fast(pkg: str, label: str, limit: int) -> list[dict]:
    """Amazon: filter `amp` popularity to FREE monetization AND drop
    any title whose Amazon offers still include FLATRATE (those are
    premium Prime originals with a free pilot episode, e.g., Ted Lasso
    - the exact "premium content" we want out of the FAST feed).

    Paginates with `offset` because the raw FREE pool includes enough
    subscription-with-free-pilot titles that the first 100 filter down
    to ~60. `_AMAZON_MAX_PAGES` caps the fetcher at 4 pages so a
    catalog change on JustWatch's end can never turn this into an
    unbounded loop."""
    out: list[dict] = []
    seen: set[str] = set()
    offset = 0
    pages = 0
    total_examined = 0
    while len(out) < limit and pages < _AMAZON_MAX_PAGES:
        data = _post_graphql(
            _JW_QUERY_AMAZON_FAST,
            {'country': 'US', 'providers': [pkg], 'first': 100, 'offset': offset},
            'FASTAmazonFree',
        )
        pages += 1
        if not data:
            break
        if data.get('errors'):
            logger.warning("fast_channels %s (%s): GraphQL errors: %s",
                            label, pkg, json.dumps(data['errors'])[:200])
            break
        pop   = ((data.get('data') or {}).get('popularTitles') or {})
        edges = pop.get('edges') or []
        if not edges:
            break
        for e in edges:
            total_examined += 1
            node = e.get('node') or {}
            # Post-filter: keep the title only if Amazon offers FREE
            # and NOT FLATRATE. This is the whole point of the Amazon
            # path - it isolates the FAST catalog from premium
            # subscription content.
            amazon_mtypes: set[str] = set()
            for o in node.get('offers') or []:
                pkg_name = ((o.get('package') or {}).get('clearName') or '').lower()
                if pkg_name.startswith(_AMAZON_OFFER_PREFIX):
                    mt = o.get('monetizationType')
                    if mt:
                        amazon_mtypes.add(mt)
            if 'FREE' not in amazon_mtypes or 'FLATRATE' in amazon_mtypes:
                continue

            row = _normalize_node(node)
            if not row:
                continue
            key = row['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            row['rank'] = len(out) + 1
            out.append(row)
            if len(out) >= limit:
                break

        if not (pop.get('pageInfo') or {}).get('hasNextPage'):
            break
        offset += 100

    logger.info("fast_channels %s (%s): amazon FAST filter kept %d/%d "
                 "titles across %d page(s)",
                 label, pkg, len(out), total_examined, pages)
    return out


def _fetch_platform(pkg: str, label: str, limit: int,
                      fast_only: bool = True) -> list[dict]:
    """Dispatcher. Roku / Tubi / Pluto use the whole-catalog path;
    Amazon uses the FREE-monetization + FLATRATE-exclusion path."""
    if fast_only:
        return _fetch_platform_whole_catalog(pkg, label, limit)
    return _fetch_platform_amazon_fast(pkg, label, limit)


def fetch() -> dict[str, Any]:
    """Pull all four FAST platforms sequentially. Each is best-effort;
    a single platform failure doesn't kill the others - that platform
    just ships `available: false` in its sources entry, and the
    frontend renders a neutral "Loading" placeholder for it (same
    pattern as the Streaming sub-tabs).
    """
    sources: dict[str, dict] = {}
    national_pool: list[dict] = []

    for slug, label, pkg, fast_only in FAST_PLATFORMS:
        items = _fetch_platform(pkg, label, _PER_PLATFORM_LIMIT,
                                  fast_only=fast_only)
        # Attach the platform slug on each item so the flat `national`
        # list stays traceable back to its FAST provider (useful for
        # cross-source dedupe upstream).
        for it in items:
            it['fast_platform'] = slug
        sources[slug] = {
            'label':     label,
            'items':     items,
            'available': bool(items),
        }
        # Contribute up to 10 top items per platform to the flat
        # `national` pool - just enough for run_all's summary index
        # to report a non-trivial count. The frontend never reads
        # `national` for FAST; it reads `sources.<slug>.items`.
        national_pool.extend(items[:10])

    return {
        'label':    'FAST',
        'national': national_pool,
        'sources':  sources,
    }


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    result = run_scraper('fast_channels', 'FAST channels', 'fast', fetch)
    srcs = (result.get('sources') or {})
    for slug in ('roku', 'tubi', 'pluto', 'amazon'):
        block = srcs.get(slug) or {}
        cnt = len(block.get('items') or [])
        print(f"fast_{slug:8s}  items={cnt:3d}  avail={block.get('available')}",
               file=sys.stderr)
    total = sum(len(((srcs.get(k) or {}).get('items') or []))
                 for k in ('roku', 'tubi', 'pluto', 'amazon'))
    err = result.get('error')
    print(f"TOTAL items across all platforms: {total}  err={err}",
           file=sys.stderr)
    return 0 if total >= 200 else 1


if __name__ == '__main__':
    sys.exit(main())
