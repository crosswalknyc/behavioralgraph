"""
FAST channel top-titles scraper.

Covers the four largest US Free Ad-Supported Streaming TV (FAST)
platforms in one daily snapshot:

    - The Roku Channel  (JustWatch package: rkc)
    - Tubi              (JustWatch package: tbv)
    - Pluto TV          (JustWatch package: ptv)
    - Amazon            (JustWatch package: amp - Prime Video ad-tier,
                         the surface that absorbed Amazon Freevee when
                         it was folded into Prime Video with ads in
                         November 2024. Closest 1:1 match on JustWatch
                         to the phrase "Amazon Live TV" for the
                         free/ad-supported side of Amazon's catalog.)

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
from typing import Any
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

# Per-platform: (sub-source slug, dashboard label, JustWatch package code).
# The slug becomes the key in the snapshot's `sources` dict AND the
# sub-tab key in the frontend.
FAST_PLATFORMS: list[tuple[str, str, str]] = [
    ('roku',    'The Roku Channel', 'rkc'),
    ('tubi',    'Tubi',             'tbv'),
    ('pluto',   'Pluto TV',         'ptv'),
    # 'amp' = Prime Video with ads (the default Prime tier as of Nov
    # 2024, which absorbed Freevee's catalog). Best JustWatch proxy
    # for "Amazon's free / ad-supported streaming surface".
    ('amazon',  'Amazon',           'amp'),
]

# 100 titles per platform. JustWatch's GraphQL happily returns 100
# in a single request; beyond that they enforce cursor pagination.
_PER_PLATFORM_LIMIT = 100


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


def _fetch_platform(pkg: str, label: str, limit: int) -> list[dict]:
    """Call JustWatch GraphQL for one FAST provider. Returns a list of
    normalized item dicts, ranked in popularity order. Empty list on
    any error so the daily run still gets partial snapshots for the
    platforms that did succeed."""
    body = json.dumps({
        'query':         _JW_QUERY,
        'variables':     {'country': 'US', 'providers': [pkg], 'first': limit},
        'operationName': 'FASTPopular',
    }).encode('utf-8')
    req = _urllib_request.Request(
        _JW_GRAPHQL_URL,
        data=body,
        headers={
            'Content-Type':   'application/json',
            'Accept':         'application/json',
            'User-Agent':     ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                                'AppleWebKit/537.36 (KHTML, like Gecko) '
                                'Chrome/126.0.0.0 Safari/537.36'),
            'Origin':         _JW_TITLE_HOST,
            'Referer':        _JW_TITLE_HOST + '/',
        },
    )
    try:
        with _urllib_request.urlopen(req, timeout=25) as r:
            raw = r.read().decode('utf-8', 'replace')
        data = json.loads(raw)
    except Exception as e:
        logger.warning("fast_channels %s (%s): request failed: %s",
                        label, pkg, e)
        return []

    if data.get('errors'):
        logger.warning("fast_channels %s (%s): GraphQL errors: %s",
                        label, pkg, json.dumps(data['errors'])[:200])
        return []

    pop   = ((data.get('data') or {}).get('popularTitles') or {})
    edges = pop.get('edges') or []
    out: list[dict] = []
    seen_titles: set[str] = set()
    for e in edges:
        node    = e.get('node') or {}
        content = node.get('content') or {}
        title   = (content.get('title') or '').strip()
        if not title:
            continue
        # Guard against dupes coming out of JustWatch (rare, but
        # a network reissue during their cache warm can double-tap).
        key = title.lower()
        if key in seen_titles:
            continue
        seen_titles.add(key)

        genres = []
        for g in content.get('genres') or []:
            t = (g or {}).get('translation')
            if t:
                genres.append(t)

        full_path = content.get('fullPath') or ''
        url = _JW_TITLE_HOST + full_path if full_path else ''

        out.append({
            'rank':               len(out) + 1,
            'title':              title,
            'category_display':   _classify_kind(node.get('objectType')),
            'year':               content.get('originalReleaseYear'),
            'genres':             genres[:4],
            'description':        content.get('shortDescription') or '',
            'url':                url,
            'image':              _poster_url(content.get('posterUrl') or ''),
            'justwatch_id':       node.get('objectId'),
        })
        if len(out) >= limit:
            break
    logger.info("fast_channels %s (%s): parsed %d items (total pool=%s)",
                 label, pkg, len(out), pop.get('totalCount'))
    return out


def fetch() -> dict[str, Any]:
    """Pull all four FAST platforms sequentially. Each is best-effort;
    a single platform failure doesn't kill the others - that platform
    just ships `available: false` in its sources entry, and the
    frontend renders a neutral "Loading" placeholder for it (same
    pattern as the Streaming sub-tabs).
    """
    sources: dict[str, dict] = {}
    national_pool: list[dict] = []

    for slug, label, pkg in FAST_PLATFORMS:
        items = _fetch_platform(pkg, label, _PER_PLATFORM_LIMIT)
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
