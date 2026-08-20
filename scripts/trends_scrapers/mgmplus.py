"""
MGM+ trending scraper.

MGM+ (formerly Epix, owned by Amazon post-MGM acquisition) is a
premium subscription streamer with a Cloudfront-backed React SPA. The
initial HTML shell is ~29KB with zero title data - all rails hydrate
client-side.

Anonymous browse-page rails DO populate under Playwright without an
authenticated session - MGM+ shows the marketing catalog to unauthed
visitors to entice signups. Any donated cookies we have (aws-waf-token,
Braze analytics IDs) help slip past their WAF challenge faster; a real
login isn't required.

Titles are surfaced as `<img alt="Title" src="...cloudfront|epix..." >`
poster tiles inside each rail. There are no href anchors on the
poster cards, so we key on alt-text and classify by which page the
title came from (/movies -> Film, /series -> TV, /browse -> both).

Cookies (optional but recommended - the aws-waf-token cuts scrape
time by ~10s):

    python3 -m scripts.trends_scrapers.donate_cookies mgmplus.com

Standalone:
    python3 -m scripts.trends_scrapers.mgmplus

Runs residentially by default - MGM+'s WAF is aggressive against
datacenter IPs even with a valid token.
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


# Pages to render. Order matters because we resolve category conflicts
# in dict-merge order (movies first, then series, then browse):
#   1. /movies    -> everything here is a Film
#   2. /series    -> everything here is TV
#   3. /browse    -> mix; only used to catch titles that aren't yet
#                    surfaced on the dedicated /movies or /series pages
MGMPLUS_URLS = [
    ('movies', 'https://www.mgmplus.com/movies'),
    ('series', 'https://www.mgmplus.com/series'),
    ('browse', 'https://www.mgmplus.com/browse'),
]


# Poster-tile image src regex. MGM+ serves posters from CloudFront under
# the mgmplus / epix (legacy) hostnames. We ONLY match imgs whose src is
# on one of those hosts - marketing brand imagery (MGM+ Logo, campaign
# hero, "Live TV or On Demand - No Ads", etc.) is served from static
# asset paths that don't match.
_POSTER_IMG_RE = re.compile(
    r'<img\b[^>]*?\balt="([^"]{2,180})"[^>]*?\bsrc="([^"]*'
    r'(?:mgmplus\.com|epix\.com|cloudfront\.net)[^"]*)"',
    re.IGNORECASE | re.DOTALL,
)
_POSTER_IMG_RE_REV = re.compile(
    r'<img\b[^>]*?\bsrc="([^"]*'
    r'(?:mgmplus\.com|epix\.com|cloudfront\.net)[^"]*)"'
    r'[^>]*?\balt="([^"]{2,180})"',
    re.IGNORECASE | re.DOTALL,
)


# Non-title strings that show up in poster-tile alt text. Case-insensitive
# exact match; anything lexically distinct passes.
_NAV_STOPWORDS_LOWER = frozenset({
    'mgm+ logo', 'mgm plus logo', 'campaign', 'get mgm+ your way',
    'live tv or on demand - no ads', 'live tv or on demand no ads',
    'watch offline', '1000s of movies & hit tv series on demand or live.',
    '1000s of movies and hit tv series on demand or live.',
    'mgm+', 'mgm plus', 'hero', 'banner', 'promo',
    'try it free', 'start free trial', 'get mgm+', 'subscribe',
    'sign in', 'log in', 'my account', 'watchlist',
    'play', 'watch trailer', 'more info',
})


def _is_real_title(text: str) -> bool:
    t = (text or '').strip()
    if not (2 <= len(t) <= 200):
        return False
    tl = t.lower()
    if tl in _NAV_STOPWORDS_LOWER:
        return False
    # Anything containing 'mgm+' or 'mgm plus' as branding rather than
    # a title (a real show called "MGM+ Presents" would still pass
    # because it wouldn't lexically equal the branded chrome strings
    # above).
    if 'mgm+ logo' in tl or 'mgm plus logo' in tl:
        return False
    return True


def _extract_titles(html: str) -> list[str]:
    """Return unique poster-tile alt-text titles in first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _POSTER_IMG_RE.finditer(html):
        alt = unescape(m.group(1)).strip()
        if _is_real_title(alt):
            key = alt.lower()
            if key not in seen:
                seen.add(key)
                out.append(alt)
    for m in _POSTER_IMG_RE_REV.finditer(html):
        alt = unescape(m.group(2)).strip()
        if _is_real_title(alt):
            key = alt.lower()
            if key not in seen:
                seen.add(key)
                out.append(alt)
    return out


def _load_previous_snapshot() -> list[dict] | None:
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(Bucket='dashboard-inputs',
                           Key='trends_iq_snapshots/latest/mgmplus.json')
        d = json.loads(o['Body'].read().decode('utf-8'))
        items = d.get('national') or []
        return items if isinstance(items, list) and items else None
    except Exception as e:
        logger.info("mgmplus: could not read previous snapshot: %s", e)
        return None


def _mark_cookie_gap(source: str, domain: str, reason: str = '') -> None:
    try:
        from .cookie_gap_notify import notify_cookie_gap
        notify_cookie_gap(source, domain, reason=reason)
    except Exception as e:
        logger.info("cookie_gap notify failed for %s/%s: %s",
                     source, domain, e)


def fetch() -> dict[str, Any]:
    rendered = render_pages(MGMPLUS_URLS,
                             homepage='https://www.mgmplus.com/',
                             cookie_domain='mgmplus.com',
                             wait_ms=5000,
                             scroll_ms=3000,
                             hydration_wait_ms=15000)

    # Per-page title lists so we know which category to assign a title
    # to. If a title appears in multiple pages (browse + movies, etc.)
    # the first assignment wins: /movies overrides /browse, /series
    # overrides /browse, and /movies vs /series will only conflict for
    # anthology-style content which is rare.
    by_page: dict[str, list[str]] = {}
    for label, html in rendered:
        titles = _extract_titles(html)
        by_page[label] = titles
        logger.info("mgmplus %s: parsed %d titles from %d-byte HTML",
                     label, len(titles), len(html))

    seen: set[str] = set()
    all_items: list[dict] = []

    # Assign category by source page in priority order. `browse` is
    # the fallback for titles not classified on the dedicated pages.
    for source_label, category in (
        ('movies', 'Film'),
        ('series', 'TV'),
        ('browse', ''),
    ):
        for title in by_page.get(source_label, []):
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            all_items.append({
                'rank':             len(all_items) + 1,
                'title':            title,
                'url':              'https://www.mgmplus.com/',
                'category_display': category,
                'collection':       source_label,
            })

    # Interleave Film + TV so trends_iq's items[:25] slice picks up a
    # balanced mix (both columns render on the dashboard). Without
    # interleaving, sequential [films..., tv...] gets truncated to the
    # first 25 - which for MGM+'s catalog is 20 films + only 5 TV,
    # since /movies renders first. Zip-like round-robin gives ~12 + 13
    # inside items[:25] and the flat 40-item list keeps films first so
    # the dashboard sort still prefers Film on ties.
    if all_items:
        films = [it for it in all_items if it['category_display'] == 'Film'][:20]
        tv    = [it for it in all_items if it['category_display'] == 'TV'  ][:20]
        interleaved: list[dict] = []
        i = j = 0
        while i < len(films) or j < len(tv):
            if i < len(films):
                interleaved.append(films[i]); i += 1
            if j < len(tv):
                interleaved.append(tv[j]); j += 1
        for k, it in enumerate(interleaved, start=1):
            it['rank'] = k
        return {'national': interleaved}

    prev = _load_previous_snapshot()
    per_page = ', '.join(f'{k}={len(v)}' for k, v in by_page.items())
    reason = (f'mgmplus: all {len(MGMPLUS_URLS)} pages parsed 0 titles '
               f'({per_page}) - WAF challenge failed or cookies stale?')
    logger.warning("mgmplus: %s", reason)
    _mark_cookie_gap('mgmplus', 'mgmplus.com', reason=reason)
    if prev:
        logger.warning("mgmplus: preserving previous snapshot "
                        "(%d items) instead of overwriting with 0",
                        len(prev))
        return {'national': prev, 'stale_from_previous': True,
                 'soft_block_reason': reason}
    return {'national': []}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('mgmplus', 'MGM+', 'streaming', fetch)
    print(f"mgmplus: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
