"""
Shared JustWatch fetch helper for subscription-streaming platform
scrapers (Paramount+, Peacock).

Why JustWatch instead of the platform sites: paramountplus.com and
peacocktv.com render signed-in home rails only (marketing shell for
anonymous visitors), which would put both platforms on the donated-
cookie + residential-IP treadmill that Disney+/Hulu/HBO Max already
ride. JustWatch's public GraphQL (`apis.justwatch.com/graphql`) is the
same endpoint their provider pages hit - no auth, no cookies, no
datacenter-IP fingerprinting - and its US popularity ranking reflects
real consumer engagement across the JustWatch panel. It is the proven
path: the FAST tab (Roku / Tubi / Pluto / Amazon Live TV) has run on
it daily since 2026-08.

Query shape mirrors `fast_channels.py`: two popularity calls per
platform (MOVIE-only + SHOW-only) so cross-object-type title
collisions ("Lioness" the movie vs the Paramount+ show) can never
shadow each other, then a zipper-interleave into the flat `national`
list the streaming snapshot contract expects.

Output items match the streaming snapshot shape consumed by
`trends_iq._fetch_streaming_trending`:

    { "rank", "title", "url", "category_display" ("Film"|"TV"),
      "image" (JustWatch s276 poster), "collection", "year",
      "genres", "description", "justwatch_id" }

Items ship with posters already attached, so the request-time
Wikipedia/iTunes poster enrichment in trends_iq.py skips them.

Failure posture: on a transient JustWatch outage the helper preserves
the platform's previous `latest/` snapshot instead of overwriting the
dashboard tile with an empty list - same pattern as starz.py /
max_streaming.py. No cookie-gap notification fires because there are
no cookies to refresh; the run_all summary + freshness monitors are
the ops signal.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

# Shared JustWatch plumbing. These are package-internal helpers that
# fast_channels.py has exercised daily since 2026-08; importing them
# keeps one source of truth for the GraphQL query shape, the poster
# URL template, and the slug-vs-localized-title disambiguation
# heuristic (which exists because of a Paramount+ show - JustWatch
# ships content.title="Lioness" for "Special Ops: Lioness").
from .fast_channels import (
    _JW_QUERY,
    _normalize_node,
    _post_graphql,
)

logger = logging.getLogger(__name__)


# Films + TV capped separately, mirroring starz.py: the dashboard
# renders films[:20] + tv[:20] per platform, and the estimator reads
# national[:40], so 20 + 20 covers both consumers exactly.
_PER_KIND_LIMIT = 20


def _fetch_one_kind(packages: list[str], label: str, object_type: str,
                    limit: int) -> list[dict]:
    """Single-kind popularity query (MOVIE or SHOW) across the
    platform's JustWatch package codes. Dedup is title-only within
    the kind - tier packages (e.g. Paramount+ Premium vs Essential)
    carry a near-identical catalog, so the same title arriving from
    both collapses to its first (highest-popularity) occurrence."""
    data = _post_graphql(
        _JW_QUERY,
        {'country': 'US', 'providers': packages,
         'first': limit, 'ot': [object_type]},
        'FASTPopular',
    )
    if not data:
        return []
    if data.get('errors'):
        logger.warning("justwatch_svod %s %s: GraphQL errors: %s",
                       label, object_type,
                       json.dumps(data['errors'])[:200])
        return []
    pop = ((data.get('data') or {}).get('popularTitles') or {})
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


def _load_previous_snapshot(slug: str) -> Optional[list[dict]]:
    """Read the current latest/ snapshot for `slug` from S3. Returns
    the national items list on success, None on any failure. Used to
    preserve last-known-good when today's JustWatch call stumbles on
    a transient outage - same pattern starz.py / max_streaming.py
    use."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(Bucket='dashboard-inputs',
                          Key=f'trends_iq_snapshots/latest/{slug}.json')
        d = json.loads(o['Body'].read().decode('utf-8'))
        items = d.get('national') or []
        return items if isinstance(items, list) and items else None
    except Exception as e:
        logger.info("%s: could not read previous snapshot: %s", slug, e)
        return None


def fetch_svod_platform(slug: str, label: str,
                        packages: list[str]) -> dict[str, Any]:
    """Fetch top Film + TV titles for one subscription platform and
    return the streaming snapshot payload (`{'national': [...]}`).

    `packages` is the list of JustWatch US package shortName codes
    that together cover the platform's catalog (tier packages union).
    """
    films = _fetch_one_kind(packages, label, 'MOVIE', _PER_KIND_LIMIT)
    tv    = _fetch_one_kind(packages, label, 'SHOW',  _PER_KIND_LIMIT)

    # Independent 1..N ranks per column; the dashboard renders each
    # column with its own ranking.
    for i, r in enumerate(films, 1):
        r['bucket_rank'] = i
        r['rank'] = i
    for i, r in enumerate(tv, 1):
        r['bucket_rank'] = i
        r['rank'] = i

    logger.info("%s: JustWatch returned %d films + %d tv (packages=%s)",
                slug, len(films), len(tv), ','.join(packages))

    if films or tv:
        # Zipper-interleave (film1, tv1, film2, tv2, ...) so consumers
        # that slice national[:25] see a balanced mix - same shape
        # starz.py writes.
        interleaved: list[dict] = []
        i = j = 0
        while i < len(films) or j < len(tv):
            if i < len(films):
                interleaved.append(films[i]); i += 1
            if j < len(tv):
                interleaved.append(tv[j]); j += 1
        for k, it in enumerate(interleaved, 1):
            it['rank'] = k
        return {'national': interleaved}

    # Empty result = transient JustWatch outage or a package-code
    # regression. Preserve yesterday's tile rather than blanking it.
    reason = (f'{slug}: JustWatch returned 0 titles for packages '
              f'{packages} - transient API outage or package-code '
              'change?')
    logger.warning("%s", reason)
    prev = _load_previous_snapshot(slug)
    if prev:
        logger.warning("%s: preserving previous snapshot (%d items) "
                       "instead of overwriting with 0", slug, len(prev))
        return {'national': prev, 'stale_from_previous': True,
                'soft_block_reason': reason}
    return {'national': []}
