"""
Watchlist storage for Trends IQ.

Each user has one JSON file at:

    s3://dashboard-inputs/trends_iq_watchlists/{user_slug}.json

Shape:

    {
      "user_slug":  "jenna_crosswalknyc_com",
      "updated_at": "2026-07-07T21:00:00+00:00",
      "entries": [
        {
          "kind":     "search",
          "source":   "google",
          "key":      "Fourth of July",
          "geo":      "National",
          "label":    "Fourth of July",   # display copy
          "added_at": "2026-07-01T14:23:00+00:00"
        },
        ...
      ]
    }

Entries are keyed on the (kind, source, key_slug) triple; adding an
existing one is a no-op. All operations round-trip through S3 with
strong-consistency (PUT after GET); we don't need finer concurrency
because each user only edits their own list.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_BUCKET = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')
_PREFIX = 'trends_iq_watchlists/'


def _s3():
    try:
        import boto3  # type: ignore
        return boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')
    except Exception as e:
        logger.debug("trends_watchlist: boto3 unavailable (%s)", e)
        return None


_SLUG_RE = re.compile(r'[^a-z0-9]+')


def _entry_slug(kind: str, source: str, key: str) -> str:
    return f"{(kind or '').lower()}|{(source or '').lower()}|{_SLUG_RE.sub('-', (key or '').lower()).strip('-')}"


def _key_for(user_slug: str) -> str:
    return f"{_PREFIX}{user_slug}.json"


def load_watchlist(user_slug: str) -> list[dict]:
    """Return the user's watchlist entries. Empty list on miss."""
    if not user_slug:
        return []
    s3 = _s3()
    if s3 is None:
        return []
    try:
        resp = s3.get_object(Bucket=_BUCKET, Key=_key_for(user_slug))
        data = json.loads(resp['Body'].read().decode('utf-8'))
        entries = data.get('entries') or []
        if isinstance(entries, list):
            return entries
    except Exception:
        pass
    return []


def _save_watchlist(user_slug: str, entries: list[dict]) -> list[dict]:
    s3 = _s3()
    if s3 is None:
        return entries
    payload = {
        'user_slug':  user_slug,
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'entries':    entries,
    }
    try:
        s3.put_object(
            Bucket=_BUCKET,
            Key=_key_for(user_slug),
            Body=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            ContentType='application/json',
            ServerSideEncryption='AES256',
            CacheControl='no-cache, max-age=0',
        )
    except Exception as e:
        logger.warning("watchlist save failed for %s: %s", user_slug, e)
    return entries


def add_entry(user_slug: str, entry: dict) -> list[dict]:
    """Idempotently add an entry. Returns the full updated list."""
    entries = load_watchlist(user_slug)
    target = _entry_slug(entry.get('kind', ''), entry.get('source', ''), entry.get('key', ''))
    for e in entries:
        existing = _entry_slug(e.get('kind', ''), e.get('source', ''), e.get('key', ''))
        if existing == target:
            return entries
    entry.setdefault('added_at', datetime.now(timezone.utc).isoformat())
    entries.append(entry)
    return _save_watchlist(user_slug, entries)


def remove_entry(user_slug: str, *,
                 kind: str, source: str, key: str) -> list[dict]:
    """Idempotently remove an entry. Returns the full updated list."""
    entries = load_watchlist(user_slug)
    target = _entry_slug(kind, source, key)
    filtered = [
        e for e in entries
        if _entry_slug(e.get('kind', ''), e.get('source', ''), e.get('key', '')) != target
    ]
    if len(filtered) == len(entries):
        return entries
    return _save_watchlist(user_slug, filtered)


def list_all_users() -> list[str]:
    """Return the user_slugs of every account that has a watchlist. Used
    by the daily digest job to iterate over all watchers."""
    s3 = _s3()
    if s3 is None:
        return []
    try:
        paginator = s3.get_paginator('list_objects_v2')
        out: list[str] = []
        for page in paginator.paginate(Bucket=_BUCKET, Prefix=_PREFIX):
            for obj in page.get('Contents') or []:
                k = obj.get('Key') or ''
                if k.endswith('.json'):
                    out.append(k[len(_PREFIX):-len('.json')])
        return out
    except Exception as e:
        logger.warning("watchlist list_all_users failed: %s", e)
        return []


def resolve_user_email(user_slug: str) -> Optional[str]:
    """Reverse the slugification so the digest job can email the user.
    Users are stored in `system/users.json`; we scan for a match by
    re-slugifying each user's email/id."""
    s3 = _s3()
    if s3 is None:
        return None
    try:
        resp = s3.get_object(Bucket=_BUCKET, Key='system/users.json')
        blob = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return None
    users = blob.get('users') or blob if isinstance(blob, dict) else []
    if isinstance(users, dict):
        users = list(users.values())
    for u in users or []:
        for field in ('email', 'id', 'name'):
            val = (u.get(field) or '').strip().lower()
            if val and _SLUG_RE.sub('_', val).strip('_') == user_slug:
                return u.get('email') or val
    return None
