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


# Nav shell / profile / UI chrome that the SPA state blob keeps
# alongside actual content. Every one of these was a false positive on
# hulu.com in the first live run (2026-07-07): profile picker labels
# ("Jenna", "Anastasia", "Guest"), top-nav categories ("Movies",
# "Sports", "Search", "Account", "My Stuff"), and settings pages.
# Match is case-insensitive on the whole title.
_NAV_STOPWORDS = frozenset({
    # top-level nav
    'home', 'movies', 'tv', 'series', 'shows', 'sports', 'live',
    'live tv', 'kids', 'browse', 'my stuff', 'my list', 'watchlist',
    'search', 'account', 'settings', 'help', 'log out', 'sign out',
    'sign in', 'log in', 'profile', 'profiles', 'switch profile',
    'add profile', 'edit profile', 'manage profiles', 'guest',
    'downloads', 'notifications', 'menu', 'more', 'discover',
    'originals', 'new', 'popular', 'trending', 'featured', 'network',
    'networks', 'channels', 'hubs', 'collections', 'espn', 'espn+',
    'hulu', 'disney+', 'max', 'netflix', 'prime video', 'peacock',
    'apple tv+', 'paramount+',
    # ratings / classifiers that leak as titles
    'tv-14', 'tv-ma', 'tv-pg', 'tv-y', 'tv-y7', 'tv-g',
    'r', 'pg-13', 'pg', 'nr', 'g',
})


# A real content deep-link on any streaming service almost always
# contains one of these path segments. Reject nodes whose URL is just
# `/`, `/profiles/...`, `/account`, etc.
_CONTENT_URL_HINTS = (
    '/browse/', '/details/', '/detail/', '/watch/', '/video/',
    '/videos/', '/movie/', '/movies/', '/series/', '/show/',
    '/shows/', '/programs/', '/episode/', '/live/', '/gp/video/detail/',
    '/gp/video/watchparty/', '/originals/', '/dp/',
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
    button / nav link).

    Hardened 2026-07-07 after the Hulu run returned "Jenna", "Movies",
    "Sports" (profile picker + top nav) as if they were trending
    titles. New rules:
      1. Reject when `type`/`kind`/`__typename` looks like nav/profile/UI chrome
      2. Require at least one strong content marker (id / contentType /
         image) AND either a length > 3 words OR an explicit programType
    """
    if not any(k in node for k in _TITLE_KEYS):
        return False
    node_type = str(
        node.get('type') or node.get('kind') or node.get('__typename') or ''
    ).lower()
    nav_ish = ('button', 'link', 'menu', 'nav', 'tab', 'header', 'footer',
               'banner', 'ad', 'advertisement', 'promo', 'profile',
               'profileswitcher', 'profileselector', 'navitem',
               'menuitem', 'navigation', 'settings', 'preferences',
               'account', 'auth', 'signin', 'signout', 'login', 'logout')
    if any(tag in node_type for tag in nav_ish):
        return False
    # Strong content markers: platform-specific IDs, artwork, or a
    # programType/contentType that names a real media kind.
    strong_markers = ('contentId', 'seriesId', 'programId', 'itemId',
                      'mediaId', 'family', 'contentType', 'programType',
                      'image', 'artwork', 'images', 'thumbnail', 'poster',
                      'tileImage', 'canonicalPath', 'canonicalUrl')
    if not any(m in node for m in strong_markers):
        return False
    return True


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
            # Nav shell / profile / UI chrome slips past the type-check
            # when the SPA stores it in the same shape as content. Guard
            # with a stopword list and a URL-pattern check.
            if key in _NAV_STOPWORDS:
                continue
            url = _pull_url(node, host)
            # Require a URL that looks like a content deep-link. If
            # there's no URL at all OR the URL is just the domain root
            # OR a profile/account path, skip.
            url_lower = url.lower()
            if url_lower == f'https://{host.lower()}/' or url_lower == f'https://{host.lower()}':
                continue
            if '/profile' in url_lower or '/account' in url_lower or '/settings' in url_lower:
                continue
            if not any(hint in url_lower for hint in _CONTENT_URL_HINTS):
                # Allow when the node has strong content signals even
                # without a URL hint - some services only put the id in
                # the tile and load via API. But require an image AND
                # a contentType/programType so we don't drift back to
                # nav-junk territory.
                has_image = any(k in node for k in ('image', 'images',
                                                    'artwork', 'poster',
                                                    'thumbnail', 'tileImage'))
                has_program_type = any(
                    isinstance(node.get(k), str)
                    for k in ('programType', 'contentType', 'itemType',
                              'family', 'mediaType')
                )
                if not (has_image and has_program_type):
                    continue
            seen.add(key)
            out.append({
                'rank':             len(out) + 1,
                'title':            title,
                'url':              url,
                'category_display': _classify(node),
                # `collection` fills in per-service from the containing
                # rail label - see individual scrapers.
                'collection':       '',
            })
            if len(out) >= limit:
                return out
    return out
