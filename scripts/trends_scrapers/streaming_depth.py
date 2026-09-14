"""
Streaming depth extender - JustWatch top-100 per platform per kind.

Why this exists (Jenna 2026-09-09: "can we ensure each list has 100 or
more items unless they legitimately do not have 100 items"):

The residential streaming scrapers (Netflix, Hulu, Disney+, HBO Max,
BritBox, MGM+, Starz - run from Jenna's laptop because those platforms
WAF-block datacenter IPs) each surface only the titles their storefront
pages render inline, typically 10-60 per platform. Prime Video runs on
Hetzner but its hydration blob carries a similar depth. That leaves the
Streaming tab's Film / TV lists far short of 100.

JustWatch's public GraphQL (the same no-cookie path `fast_channels.py`
and `_justwatch_svod.py` ride) ranks the FULL catalog of each platform
by current popularity and serves 100 titles per query from Hetzner's
datacenter IP without any block. This scraper pulls the top 100 films +
top 100 shows per platform into ONE side snapshot
(`trends_iq_snapshots/latest/streaming_depth.json`).

`trends_iq._fetch_streaming_trending` then merges: the platform's own
snapshot keeps the top ranks (official Netflix Top 10 ordering, the
storefront's own trending order, storefront-only titles), and this
snapshot's rows fill the list out to 100 per kind. The two never race:
this file is written only by Hetzner's daily run, the platform
snapshots only by their own scrapers.

Depth ceilings measured 2026-09-09 (JustWatch US catalog totals):
    netflix    nfx  100 film / 100 tv
    disneyplus dnp  100 film / 100 tv
    hulu       hlu  100 film / 100 tv
    max        mxx  100 film / 100 tv
    primevideo amp  100 film / 100 tv
    britbox    bbo   62 film / 100 tv   (62 = full US film catalog)
    mgmplus    epx  100 film /  54 tv   (54 = full US show catalog)
    starz      stz  100 film /  50 tv   (50 = full US show catalog)

ESPN+ is deliberately absent: JustWatch carries ~1 title for it (live
sports don't chart), so the ESPN+ panel keeps its residential depth.
Paramount+ and Peacock are also absent: their own Hetzner scrapers
already pull 100 per kind straight from JustWatch via
`_justwatch_svod.py`.

Standalone:
    python3 -m scripts.trends_scrapers.streaming_depth
"""

from __future__ import annotations

import json
import logging
import random
import sys
import time
from typing import Any

from ._base import run_scraper
from .fast_channels import _JW_QUERY, _normalize_node, _post_graphql

logger = logging.getLogger(__name__)

# (snapshot_slug_of_platform, display_label, justwatch_package_shortname)
# Slugs match STREAMING_PLATFORMS in trends_iq.py so the merge is a
# straight dict lookup.
_PLATFORMS: list[tuple[str, str, str]] = [
    ('netflix',    'Netflix',     'nfx'),
    ('disneyplus', 'Disney+',     'dnp'),
    ('hulu',       'Hulu',        'hlu'),
    ('max',        'HBO Max',     'mxx'),
    ('primevideo', 'Prime Video', 'amp'),
    ('britbox',    'BritBox',     'bbo'),
    ('mgmplus',    'MGM+',        'epx'),
    ('starz',      'Starz',       'stz'),
]

_PER_KIND_LIMIT = 100


def _fetch_one_kind(pkg: str, label: str, object_type: str,
                    limit: int) -> list[dict]:
    """One JustWatch popularity query for one platform + one kind.

    Degrades to the deepest depth that succeeds: if the full-depth
    query comes back empty or with errors (rate limiting on the deeper
    100-item queries, a transient outage), retry at shallower depths
    with a short jittered pause rather than returning an empty list.
    """
    depths = [limit] + [d for d in (50, 20) if d < limit]
    for attempt, depth in enumerate(depths):
        if attempt:
            time.sleep(0.8 + random.random() * 0.8)
        data = _post_graphql(
            _JW_QUERY,
            {'country': 'US', 'providers': [pkg],
             'first': depth, 'ot': [object_type]},
            'FASTPopular',
        )
        if not data:
            continue
        if data.get('errors'):
            logger.warning("streaming_depth %s %s: graphql errors at "
                           "depth %d: %s", label, object_type, depth,
                           str(data['errors'])[:200])
            continue
        edges = (((data.get('data') or {}).get('popularTitles') or {})
                 .get('edges') or [])
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
            if len(out) >= depth:
                break
        if not out:
            continue
        if depth != limit:
            logger.warning("streaming_depth %s %s: degraded to depth %d "
                           "(full depth %d returned nothing)",
                           label, object_type, depth, limit)
        for i, r in enumerate(out, 1):
            r['rank'] = i
            r['bucket_rank'] = i
        return out
    return []


def _load_previous_sources() -> dict:
    """Read the current latest/streaming_depth.json sources block from
    S3. Empty dict on any failure. Used to carry a platform's previous
    depth block forward when today's JustWatch fetch for that platform
    returns nothing - the merge side must never see a good block
    replaced by an empty one."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(
            Bucket='dashboard-inputs',
            Key='trends_iq_snapshots/latest/streaming_depth.json')
        d = json.loads(o['Body'].read().decode('utf-8'))
        sources = d.get('sources') or {}
        return sources if isinstance(sources, dict) else {}
    except Exception as e:
        logger.info("streaming_depth: could not read previous snapshot: %s",
                    e)
        return {}


def fetch() -> dict[str, Any]:
    """Pull top-100 films + top-100 shows for every extended platform.

    Best-effort per platform: one platform failing leaves the others
    intact, and the merge side treats a missing sources entry as
    "no extension available" (platform keeps its own snapshot depth).
    """
    sources: dict[str, dict] = {}
    for slug, label, pkg in _PLATFORMS:
        films = _fetch_one_kind(pkg, label, 'MOVIE', _PER_KIND_LIMIT)
        time.sleep(0.35)
        tv = _fetch_one_kind(pkg, label, 'SHOW', _PER_KIND_LIMIT)
        time.sleep(0.35)
        for r in films:
            r['category_display'] = 'Film'
        for r in tv:
            r['category_display'] = 'TV'
        sources[slug] = {
            'label':     label,
            'films':     films,
            'tv':        tv,
            'available': bool(films or tv),
        }
        logger.info("streaming_depth %s: %d films + %d tv",
                    slug, len(films), len(tv))

    # Never ship an empty platform block over a previously-good one.
    # A platform whose fetch returned nothing today (throttle, outage)
    # keeps yesterday's depth block, marked stale, so the dashboard
    # merge never loses depth it already had.
    empty_slugs = [slug for slug, block in sources.items()
                   if not (block.get('films') or block.get('tv'))]
    if empty_slugs:
        prev_sources = _load_previous_sources()
        for slug in empty_slugs:
            prev_block = prev_sources.get(slug) or {}
            if prev_block.get('films') or prev_block.get('tv'):
                prev_block = dict(prev_block)
                prev_block['available'] = True
                prev_block['stale_from_previous'] = True
                sources[slug] = prev_block
                logger.warning(
                    "streaming_depth %s: fetch returned 0 rows; "
                    "preserving previous depth block (%d films + %d tv)",
                    slug, len(prev_block.get('films') or []),
                    len(prev_block.get('tv') or []))
    return {'sources': sources}


def main() -> int:
    payload = run_scraper('streaming_depth', 'Streaming Depth',
                          'streaming', fetch)
    srcs = payload.get('sources') or {}
    total = sum(len(b.get('films') or []) + len(b.get('tv') or [])
                for b in srcs.values())
    print(f"streaming_depth: {len(srcs)} platforms, {total} rows")
    return 0 if total else 1


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    sys.exit(main())
