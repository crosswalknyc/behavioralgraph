"""Republish a parent rail's catalog under a derived rail's slug.

A derived rail is one distribution path through another rail's
service (`scripts/trends_scrapers/derived_rails.py`). The catalog is
the SAME on both: Starz sold through Prime Video Channels is the same
entitlement and the same title list as Starz, and so is Paramount+.
What differs is the audience, and that split is researched and applied
at render time, never scraped.

So a derived rail needs no scrape of its own. It needs the parent's
title list republished under its own slug, which is what this does.
One implementation for every derived rail, so a new rail is a registry
entry plus one thin module rather than a fourth copy of the same
twenty lines.

Failure posture matches every other streaming scraper: if the parent
catalog reads empty, keep the rail's previous mirror and mark it
stale rather than blanking the panel.

Never queries clickstream
(`.cursor/rules/trends-rankers-never-clickstream.mdc`).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_BUCKET = 'dashboard-inputs'
_PREFIX = 'trends_iq_snapshots/latest'


def _read_latest(slug: str) -> dict:
    """Read `latest/<slug>.json`. Empty dict on any failure."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(Bucket=_BUCKET, Key=f'{_PREFIX}/{slug}.json')
        d = json.loads(o['Body'].read().decode('utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception as e:
        logger.info("derived_rail_mirror: could not read the %s "
                    "snapshot: %s", slug, e)
        return {}


def mirror(slug: str, source_slug: str) -> dict[str, Any]:
    """The payload for `slug`, mirrored from `source_slug`."""
    src = _read_latest(source_slug)
    items = src.get('national') or []
    if not isinstance(items, list):
        items = []

    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict) or not (it.get('title') or '').strip():
            continue
        row = dict(it)
        row['rank'] = len(out) + 1
        out.append(row)

    if out:
        payload: dict[str, Any] = {
            'national':          out,
            'mirrors':           source_slug,
            'source_fetched_at': src.get('fetched_at'),
        }
        # A mirror of a stale catalog is itself stale, and the panel
        # should say so the same way the parent panel does.
        if src.get('stale_from_previous'):
            payload['stale_from_previous'] = True
        logger.info("%s: mirrored %d titles from the %s catalog",
                    slug, len(out), source_slug)
        return payload

    prev = (_read_latest(slug).get('national') or [])
    if isinstance(prev, list) and prev:
        logger.warning("%s: the %s catalog read empty; keeping the "
                       "previous %d-title mirror", slug, source_slug,
                       len(prev))
        return {'national': prev, 'stale_from_previous': True,
                'mirrors': source_slug}
    return {'national': []}
