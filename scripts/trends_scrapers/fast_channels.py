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

# Total items per platform, split evenly across Film + TV. Two
# separate JustWatch queries per platform (MOVIE-only + SHOW-only)
# means neither category can shadow the other on cross-object-type
# title collisions like "Lioness" (movie) vs "Lioness" (Paramount+
# show "Special Ops: Lioness" whose JustWatch localized title is
# just "Lioness"). Before the split, a mixed query returned 100
# titles that decayed to ~35 films + ~65 shows; now we get 50 of
# each. Change 2026-08-21 (Jenna: "need to split film/tv on FAST
# because right now on tubi it shows lioness the paramount+ show
# instead of the movie one").
_PER_PLATFORM_LIMIT = 100
_PER_KIND_LIMIT     = 50

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


# Standard query (Roku / Tubi / Pluto): whole-catalog popularity for
# a SPECIFIC objectType. Callers issue two calls per platform - one
# with objectTypes=[MOVIE], one with objectTypes=[SHOW] - so that
# same-name cross-type collisions never shadow each other. Before
# the split, a Roku mixed-type query would rank the Paramount+ show
# "Special Ops: Lioness" (JustWatch content.title=='Lioness') ahead
# of any Roku FAST movie titled "Lioness", and our first-writer-wins
# dedup by lowercased title dropped the movie. Two queries removes
# the ambiguity entirely (Jenna 2026-08-21).
_JW_QUERY = """
query FASTPopular($country: Country!, $providers: [String!], $first: Int!, $ot: [ObjectType!]) {
  popularTitles(country: $country, first: $first,
                filter: {packages: $providers, objectTypes: $ot},
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
# after filtering. Same MOVIE/SHOW parameterization as the standard
# query so Amazon Live TV also gets independent Film + TV top lists.
_JW_QUERY_AMAZON_FAST = """
query FASTAmazonFree($country: Country!, $providers: [String!], $first: Int!, $offset: Int, $ot: [ObjectType!]) {
  popularTitles(country: $country, first: $first, offset: $offset,
                filter: {packages: $providers, objectTypes: $ot,
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


def _slug_to_title(slug: str) -> str:
    """Convert a JustWatch fullPath slug into a spaced Title Case
    string. e.g. "special-ops-lioness" -> "Special Ops: Lioness".
    Colons are inserted where JustWatch's slug format would have
    them (heuristic: after the first token for known franchise
    prefixes; safer default is no colon, just spaces)."""
    if not slug:
        return ''
    parts = [p for p in slug.replace('_', '-').split('-') if p]
    if not parts:
        return ''
    return ' '.join(w.capitalize() for w in parts)


def _prefer_disambiguated_title(display_title: str, full_path: str) -> str:
    """Return the more-disambiguated title between the JustWatch
    localized `content.title` and the fullPath slug.

    JustWatch sometimes ships a heavily shortened localized title
    (e.g. content.title="Lioness" for the Paramount+ show whose
    canonical name is "Special Ops: Lioness") while the URL slug
    retains the full name. When the slug has strictly more tokens
    than the display title AND every token of the display title
    appears as a substring inside the slug-derived title, we treat
    the slug as authoritative. Otherwise we trust content.title
    (which is what JustWatch's own web app renders).

    Examples:
      display="Lioness", slug="tv-show/special-ops-lioness"
        -> slug tokens = {special, ops, lioness}, display tokens = {lioness}
        -> slug wins -> "Special Ops: Lioness"
      display="The Bear", slug="tv-show/the-bear-hulu"
        -> slug tokens = {the, bear, hulu}, display tokens = {the, bear}
        -> slug has extra "hulu" (platform noise, not part of the title)
        -> content.title kept
    """
    if not display_title or not full_path:
        return display_title or ''

    # Strip the leading "/us/tv-show/" or "/us/movie/" prefix and
    # anything after the first path segment (season paths etc).
    segs = [s for s in (full_path or '').strip('/').split('/') if s]
    slug = segs[-1] if segs else ''
    slug_title = _slug_to_title(slug)
    if not slug_title:
        return display_title

    disp_toks = set(w.lower() for w in display_title.split() if w)
    slug_toks = set(w.lower() for w in slug_title.split() if w)
    if not disp_toks or not slug_toks:
        return display_title

    # Slug must have MORE tokens than display AND fully cover
    # every display token. This guards against slug noise like
    # "-hulu" / "-2023" / "-original-series" and only triggers
    # when the slug is a strict superset.
    if len(slug_toks) <= len(disp_toks):
        return display_title
    if not disp_toks.issubset(slug_toks):
        return display_title

    # Reject slug tokens that are obvious platform/year noise
    # rather than title content.
    _NOISE = {
        'hulu', 'netflix', 'amazon', 'prime', 'apple', 'roku',
        'tubi', 'pluto', 'paramount', 'peacock', 'disney', 'hbo',
        'max', 'starz', 'showtime', 'video',
    }
    extra = slug_toks - disp_toks
    if extra and extra.issubset(_NOISE):
        return display_title
    # Also reject if the extra tokens are all pure digits (year
    # tags like "-2023" don't belong in the title).
    if extra and all(w.isdigit() for w in extra):
        return display_title

    # Slug wins. Return the plain spaced Title Case slug. We
    # deliberately avoid trying to reinject punctuation (colons,
    # dashes) - JustWatch's slug loses that info and a heuristic
    # here mis-splits titles like "The Bear" (slug 'the-bear')
    # into "The: Bear". Spaced Title Case reads correctly for
    # every real-world case we've hit: "Special Ops Lioness",
    # "Interview With The Vampire", "The Lord Of The Rings The
    # Rings Of Power". The frontend renders it as-is.
    return slug_title


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
    raw_title = (content.get('title') or '').strip()
    if not raw_title:
        return None

    genres = []
    for g in content.get('genres') or []:
        t = (g or {}).get('translation')
        if t:
            genres.append(t)

    full_path = content.get('fullPath') or ''
    url = _JW_TITLE_HOST + full_path if full_path else ''

    # Prefer the fullPath-derived title when it's strictly more
    # informative than JustWatch's localized content.title. Fixes
    # the "Lioness -> Special Ops: Lioness" ambiguity where
    # JustWatch trims the canonical show name in `content.title`
    # but keeps it in the URL slug (Jenna 2026-08-21).
    title = _prefer_disambiguated_title(raw_title, full_path)

    return {
        'title':            title,
        'category_display': _classify_kind(node.get('objectType')),
        'year':             content.get('originalReleaseYear'),
        'genres':           genres[:4],
        'description':      content.get('shortDescription') or '',
        'url':              url,
        'image':            _poster_url(content.get('posterUrl') or ''),
        'justwatch_id':     node.get('objectId'),
        # Keep the raw JustWatch title around for logging /
        # troubleshooting - never rendered in the UI but useful
        # when auditing "why does this row read differently than
        # last week?".
        'title_raw':        raw_title,
    }


def _fetch_platform_whole_catalog(pkg: str, label: str,
                                    limit: int) -> list[dict]:
    """Roku Channel / Tubi / Pluto TV: whole catalog is FAST by
    definition. We issue TWO separate popularity queries (MOVIE-only
    + SHOW-only), each capped at `_PER_KIND_LIMIT`, and interleave
    them by their per-kind rank so the combined `items` list keeps
    both categories visible in the top slots.

    Dedup is intra-kind only, keyed by (objectType, title). A movie
    and a show that share a title (Lioness the movie + Lioness the
    show) BOTH survive - the frontend renders them in different
    columns anyway.
    """
    films = _fetch_one_kind_whole_catalog(pkg, label, 'MOVIE',
                                            _PER_KIND_LIMIT)
    tv    = _fetch_one_kind_whole_catalog(pkg, label, 'SHOW',
                                            _PER_KIND_LIMIT)

    # Independent 1..N ranks per column. Store in `bucket_rank` so
    # the backend + frontend can render each column with its own
    # ranking; the combined `rank` on the merged list is derived
    # below (interleaved zipper) and is only used by legacy
    # consumers.
    for i, r in enumerate(films, 1):
        r['bucket_rank'] = i
    for i, r in enumerate(tv, 1):
        r['bucket_rank'] = i

    # Zipper-interleave (film1, tv1, film2, tv2, ...) so a caller
    # that slices `items[:20]` sees roughly 10 of each. This mirrors
    # the old behavior where JustWatch's mixed query returned an
    # interleaved list.
    out: list[dict] = []
    max_len = max(len(films), len(tv))
    for i in range(max_len):
        if i < len(films): out.append(films[i])
        if i < len(tv):    out.append(tv[i])
    out = out[:limit]
    for i, r in enumerate(out, 1):
        r['rank'] = i
    logger.info("fast_channels %s (%s): kept %d films + %d tv (interleaved %d)",
                 label, pkg, len(films), len(tv), len(out))
    return out


def _fetch_one_kind_whole_catalog(pkg: str, label: str, object_type: str,
                                    limit: int) -> list[dict]:
    """Single-kind popularity query for the whole-catalog platforms.
    Called twice per platform (MOVIE + SHOW). Dedup is title-only
    within this kind - two shows with the same title (rare) collapse
    to the first-seen, but a movie and a show with the same title
    can no longer collide because they live in separate calls."""
    data = _post_graphql(
        _JW_QUERY,
        {'country': 'US', 'providers': [pkg],
         'first': limit, 'ot': [object_type]},
        'FASTPopular',
    )
    if not data:
        return []
    if data.get('errors'):
        logger.warning("fast_channels %s (%s) %s: GraphQL errors: %s",
                        label, pkg, object_type,
                        json.dumps(data['errors'])[:200])
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
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _fetch_platform_amazon_fast(pkg: str, label: str,
                                  limit: int) -> list[dict]:
    """Amazon: filter `amp` popularity to FREE monetization AND drop
    any title whose Amazon offers still include FLATRATE (those are
    premium Prime originals with a free pilot episode, e.g., Ted Lasso
    - the exact "premium content" we want out of the FAST feed).

    Same MOVIE/SHOW split treatment as the whole-catalog platforms
    (Jenna 2026-08-21): two paginated fetches per platform so that
    Amazon Live TV's Film column can't be starved by SHOW titles.
    """
    films = _fetch_one_kind_amazon_fast(pkg, label, 'MOVIE',
                                          _PER_KIND_LIMIT)
    tv    = _fetch_one_kind_amazon_fast(pkg, label, 'SHOW',
                                          _PER_KIND_LIMIT)

    for i, r in enumerate(films, 1):
        r['bucket_rank'] = i
    for i, r in enumerate(tv, 1):
        r['bucket_rank'] = i

    out: list[dict] = []
    max_len = max(len(films), len(tv))
    for i in range(max_len):
        if i < len(films): out.append(films[i])
        if i < len(tv):    out.append(tv[i])
    out = out[:limit]
    for i, r in enumerate(out, 1):
        r['rank'] = i
    logger.info("fast_channels %s (%s): amazon kept %d films + %d tv "
                 "(interleaved %d)",
                 label, pkg, len(films), len(tv), len(out))
    return out


def _fetch_one_kind_amazon_fast(pkg: str, label: str, object_type: str,
                                  limit: int) -> list[dict]:
    """Single-kind Amazon FAST fetch. Paginates FREE-tier candidates
    and post-filters out any that still carry FLATRATE on Amazon,
    same as the pre-split behavior but restricted to one objectType
    per call so film + tv can't shadow each other."""
    out: list[dict] = []
    seen: set[str] = set()
    offset = 0
    pages = 0
    total_examined = 0
    while len(out) < limit and pages < _AMAZON_MAX_PAGES:
        data = _post_graphql(
            _JW_QUERY_AMAZON_FAST,
            {'country': 'US', 'providers': [pkg], 'first': 100,
             'offset': offset, 'ot': [object_type]},
            'FASTAmazonFree',
        )
        pages += 1
        if not data:
            break
        if data.get('errors'):
            logger.warning("fast_channels %s (%s) %s: GraphQL errors: %s",
                            label, pkg, object_type,
                            json.dumps(data['errors'])[:200])
            break
        pop   = ((data.get('data') or {}).get('popularTitles') or {})
        edges = pop.get('edges') or []
        if not edges:
            break
        for e in edges:
            total_examined += 1
            node = e.get('node') or {}
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
            out.append(row)
            if len(out) >= limit:
                break

        if not (pop.get('pageInfo') or {}).get('hasNextPage'):
            break
        offset += 100
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
