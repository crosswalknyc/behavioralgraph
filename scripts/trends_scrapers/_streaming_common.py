"""
Shared helpers for cookie-donation streaming scrapers.

Disney+, Hulu, and Max all render their homepage/discover screens as
React apps that ship a big JSON `state` blob in the HTML *when the
user is signed in*. Without cookies you get a marketing landing page
with no title data (see the smoke-test output in the initial commit).
With cookies you get 1-2MB of hydrated content.

Rather than write a bespoke DOM parser per service (which the services
churn every few months), we look for structured JSON payloads with
title-like fields:

    - Disney+ ships __PRELOADED_STATE__ / __APOLLO_STATE__ with
      collection > sets > items > titles > text > full > default
    - Hulu ships __PRELOADED_STORE__ with recos > collections >
      items[].name
    - Max ships __NEXT_DATA__ (surprise) with props > pageProps >
      pageData > components > items[].title

The common cases we scan for are:

    - "title" / "name" / "titleName" / "seriesName" / "displayName"
    - within objects that ALSO carry a rank-ish or position-ish sibling

The output shape matches the streaming snapshot contract:

    { "national": [ { "rank": 1, "title": "...", "url": "...",
                        "category_display": "TV" | "Film",
                        "collection": "Trending" }, ... ] }
"""

from __future__ import annotations

import json
import logging
import re
from html import unescape
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


# Common JSON-blob wrappers streaming services use.
_JSON_BLOB_PATTERNS = [
    re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.+?)</script>',
                 re.DOTALL | re.IGNORECASE),
    re.compile(r'window\.__PRELOADED_STATE__\s*=\s*(\{.+?\});\s*</script>',
                 re.DOTALL),
    re.compile(r'window\.__PRELOADED_STORE__\s*=\s*(\{.+?\});\s*</script>',
                 re.DOTALL),
    re.compile(r'window\.__APOLLO_STATE__\s*=\s*(\{.+?\});\s*</script>',
                 re.DOTALL),
    re.compile(r'window\.__INITIAL_STATE__\s*=\s*(\{.+?\});\s*</script>',
                 re.DOTALL),
]


# Field names that usually hold the human-readable title in a hydrated
# streaming state blob. Order matters - we try `titleName` before
# generic `title` so search-index rows (where `title` is a link title
# like "Play now") don't win.
_TITLE_KEYS = (
    'titleName', 'seriesName', 'displayName', 'displayText',
    'programName', 'name', 'title', 'itemName',
)


def _extract_json_blobs(html: str) -> list[Any]:
    """Return every plausible JSON payload we can pull out of the HTML."""
    blobs: list[Any] = []
    for pat in _JSON_BLOB_PATTERNS:
        for m in pat.finditer(html):
            raw = m.group(1)
            # Some blobs are HTML-escaped inside <script> for XSS safety.
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                try:
                    obj = json.loads(unescape(raw))
                except json.JSONDecodeError:
                    continue
            blobs.append(obj)
    return blobs


def _iter_walk(obj: Any) -> Iterable[dict]:
    """Depth-first walk yielding every dict in the tree."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_walk(v)


def _pull_title(node: dict) -> Optional[str]:
    """Extract the best title-ish field from a dict node."""
    for k in _TITLE_KEYS:
        v = node.get(k)
        if isinstance(v, str):
            s = v.strip()
            # Skip UI chrome
            if 5 <= len(s) <= 180 and not s.lower().startswith(('http', 'www.')):
                return s
        elif isinstance(v, dict):
            # Some services nest text.full.default
            for sub in ('full', 'default', 'text', 'value'):
                sv = v.get(sub) if isinstance(v.get(sub), (str, dict)) else None
                if isinstance(sv, str) and 5 <= len(sv.strip()) <= 180:
                    return sv.strip()
                if isinstance(sv, dict):
                    for sub2 in ('default', 'value', 'text'):
                        sv2 = sv.get(sub2)
                        if isinstance(sv2, str) and 5 <= len(sv2.strip()) <= 180:
                            return sv2.strip()
    return None


def _looks_like_title_node(node: dict) -> bool:
    """A title node usually has BOTH a title-ish field AND a type/id
    marker that says this is a content item (not a marketing card /
    button / nav link)."""
    if not any(k in node for k in _TITLE_KEYS):
        return False
    # Reject obvious non-content nodes
    node_type = str(node.get('type') or node.get('kind') or '').lower()
    if node_type in {'button', 'link', 'menu', 'nav', 'header', 'footer',
                       'banner', 'ad', 'advertisement', 'promo'}:
        return False
    # Require at least one content-marker sibling
    markers = ('contentId', 'seriesId', 'programId', 'itemId', 'id',
                'contentType', 'programType', 'family', 'mediaId',
                'callToAction', 'watchUrl', 'href', 'url', 'image',
                'artwork', 'images', 'thumbnail')
    return any(m in node for m in markers)


def _pull_url(node: dict, base_host: str) -> str:
    """Best-effort deep link. Falls back to platform home."""
    for k in ('watchUrl', 'url', 'href', 'canonicalPath', 'canonicalUrl',
              'shareUrl', 'linkUrl'):
        v = node.get(k)
        if isinstance(v, str) and v.strip():
            u = v.strip()
            if u.startswith('/'):
                return f'https://{base_host}{u}'
            if u.startswith('http'):
                return u
    # Some services carry the slug in a nested `path` or `route`
    for k in ('path', 'route', 'slug'):
        v = node.get(k)
        if isinstance(v, str) and v.strip():
            u = v.strip()
            if u.startswith('/'):
                return f'https://{base_host}{u}'
    return f'https://{base_host}/'


def _classify(node: dict) -> str:
    """Films vs TV, when we can tell. Returns '' when we can't."""
    for k in ('programType', 'contentType', 'itemType', 'family',
              'type', 'mediaType'):
        v = node.get(k)
        if isinstance(v, str):
            lv = v.lower()
            if 'movie' in lv or 'film' in lv:
                return 'Film'
            if 'series' in lv or 'show' in lv or 'tv' in lv or 'episode' in lv:
                return 'TV'
    return ''


def parse_streaming_html(html: str, *, host: str,
                          limit: int = 20) -> list[dict]:
    """Return up to `limit` trending titles extracted from a hydrated
    streaming home/discover HTML.

    Returns [] when no JSON blob was found (usually means cookies
    weren't donated / the response was the marketing shell).
    """
    if not html or len(html) < 5000:
        return []
    blobs = _extract_json_blobs(html)
    if not blobs:
        logger.info("streaming parser: no JSON blobs found in %d-byte HTML",
                     len(html))
        return []

    seen: set[str] = set()
    out: list[dict] = []
    for blob in blobs:
        for node in _iter_walk(blob):
            if not _looks_like_title_node(node):
                continue
            title = _pull_title(node)
            if not title:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({
                'rank':             len(out) + 1,
                'title':            title,
                'url':              _pull_url(node, host),
                'category_display': _classify(node),
                # `collection` fills in per-service from the containing
                # rail label - see individual scrapers.
                'collection':       '',
            })
            if len(out) >= limit:
                return out
    return out
