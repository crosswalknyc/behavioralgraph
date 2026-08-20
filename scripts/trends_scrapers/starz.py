"""
Starz trending scraper.

Starz (Lionsgate's premium subscription streamer, ~12M US subscribers,
home of Power, Outlander, Spartacus, and STARZ Originals) is a Next.js
site that ships its full browse catalog in `__NEXT_DATA__`. No auth
required for the browse catalog - the /us/en/movies and /us/en/series
routes serve public curated rails to anonymous visitors as marketing.

Data path:
    __NEXT_DATA__.props.pageProps.movieBlocks[N].data.slides[M]
    __NEXT_DATA__.props.pageProps.seriesBlocks[N].data.slides[M]

Each slide has:
    { title, contentId, contentType, images.portrait1200, logLine,
      detail, original, order, ... }

`contentType` = "Movie" for films, "Series with Season" for TV.

Cookies (optional but recommended - starz.com uses Segment / Rokt
analytics that occasionally soft-throttle unauth requests; a donated
session slips through cleanly):

    python3 -m scripts.trends_scrapers.donate_cookies starz.com

Standalone:
    python3 -m scripts.trends_scrapers.starz

Runs residentially (same reason as BritBox / MGM+ - Akamai fingerprints
the datacenter IP).
"""

from __future__ import annotations

import json
import logging
import re
import sys
from html import unescape
from typing import Any

from ._base import http_get, run_scraper

logger = logging.getLogger(__name__)


STARZ_URLS = [
    ('movies', 'https://www.starz.com/us/en/movies'),
    ('series', 'https://www.starz.com/us/en/series'),
]


_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>',
    re.DOTALL,
)


# Non-title strings that occasionally appear as browseMenuData/nav
# titles inside the same page tree. Case-insensitive exact match.
_NAV_STOPWORDS_LOWER = frozenset({
    'starz', 'starzplay', 'starz play', 'home', 'movies', 'series',
    'my list', 'sign in', 'log in', 'subscribe', 'account',
    'watch now', 'coming soon', 'search',
})


def _classify(content_type: str) -> str:
    """`Movie` -> Film, `Series with Season` (and anything with 'series'
    or 'show' or 'episode') -> TV. Unknown -> ''."""
    ct = (content_type or '').lower()
    if 'movie' in ct or 'film' in ct:
        return 'Film'
    if 'series' in ct or 'show' in ct or 'episode' in ct or 'season' in ct:
        return 'TV'
    return ''


def _poster(images: dict) -> str:
    """Prefer portrait1200 (the standard poster tile) then portrait
    (higher res), then landscape2560. Returns '' if no image."""
    if not isinstance(images, dict):
        return ''
    for k in ('portrait1200', 'portrait', 'portraitSchedule',
              'titleArt', 'landscape2560', 'landscapeBg'):
        v = images.get(k)
        if isinstance(v, str) and v.startswith('http'):
            return v
    return ''


def _extract_slides(blocks: list) -> list[dict]:
    """Flatten `<movie|series>Blocks[*].data.slides[*]` in block order.
    Dedupes by title (first occurrence wins, preserving editorial-rail
    rank). Returns dashboard-shaped dicts."""
    if not isinstance(blocks, list):
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        slides = (blk.get('data') or {}).get('slides') or blk.get('slides') or []
        if not isinstance(slides, list):
            continue
        for s in slides:
            if not isinstance(s, dict):
                continue
            title = s.get('title') or s.get('name')
            if not isinstance(title, str):
                continue
            title = unescape(title).strip()
            if not (2 <= len(title) <= 200):
                continue
            if title.lower() in _NAV_STOPWORDS_LOWER:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            content_type = s.get('contentType') or ''
            content_id   = s.get('contentId') or s.get('id') or ''
            # Starz doesn't expose a stable web-URL pattern for a title
            # detail page in the JSON (`clicktarget: self` is a hint
            # for the SPA router, not a URL). Link back to the browse
            # page so tiles are clickable and land on the site.
            url = 'https://www.starz.com/us/en/movies' if _classify(content_type) == 'Film' \
                    else 'https://www.starz.com/us/en/series'
            out.append({
                'rank':             len(out) + 1,
                'title':            title,
                'url':              url,
                'category_display': _classify(content_type),
                'image':            _poster(s.get('images') or {}),
                'collection':       '',
                'content_id':       content_id,
            })
    return out


def _load_previous_snapshot() -> list[dict] | None:
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(Bucket='dashboard-inputs',
                           Key='trends_iq_snapshots/latest/starz.json')
        d = json.loads(o['Body'].read().decode('utf-8'))
        items = d.get('national') or []
        return items if isinstance(items, list) and items else None
    except Exception as e:
        logger.info("starz: could not read previous snapshot: %s", e)
        return None


def _mark_cookie_gap(source: str, domain: str, reason: str = '') -> None:
    try:
        from .cookie_gap_notify import notify_cookie_gap
        notify_cookie_gap(source, domain, reason=reason)
    except Exception as e:
        logger.info("cookie_gap notify failed for %s/%s: %s",
                     source, domain, e)


def fetch() -> dict[str, Any]:
    films: list[dict] = []
    tv:    list[dict] = []
    parsed_counts: list[int] = []

    for label, url in STARZ_URLS:
        r = http_get(url, cookie_domain='starz.com', timeout=30, retries=2)
        if r is None:
            logger.warning("starz %s: http_get returned None", label)
            parsed_counts.append(0)
            continue
        try:
            html = r.text if hasattr(r, 'text') else r.decode('utf-8')
        except Exception:
            logger.warning("starz %s: could not decode response", label)
            parsed_counts.append(0)
            continue
        m = _NEXT_DATA_RE.search(html)
        if not m:
            logger.warning("starz %s: no __NEXT_DATA__ in %d-byte HTML",
                            label, len(html))
            parsed_counts.append(0)
            continue
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            logger.warning("starz %s: __NEXT_DATA__ JSON decode failed: %s",
                            label, e)
            parsed_counts.append(0)
            continue
        page_props = (obj.get('props') or {}).get('pageProps') or {}
        blocks_key = 'movieBlocks' if label == 'movies' else 'seriesBlocks'
        blocks = page_props.get(blocks_key) or []
        items = _extract_slides(blocks)
        parsed_counts.append(len(items))
        logger.info("starz %s: parsed %d titles from %d blocks (%d-byte HTML)",
                     label, len(items), len(blocks), len(html))
        if label == 'movies':
            films = items
        else:
            tv = items

    # Cap Films + TV separately (20 each) so both columns render even
    # when the movies page has 20x more slides than the series page.
    # Interleave into the flat `national` list so trends_iq's items[:25]
    # slice picks up a balanced ~12 + 13 mix.
    films = films[:20]
    tv    = tv[:20]

    if films or tv:
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
    reason = (f'starz: {len(STARZ_URLS)} pages parsed 0 titles '
               f'(counts={parsed_counts}) - datacenter IP block, '
               f'__NEXT_DATA__ shape changed, or cookies stale?')
    logger.warning("starz: %s", reason)
    _mark_cookie_gap('starz', 'starz.com', reason=reason)
    if prev:
        logger.warning("starz: preserving previous snapshot "
                        "(%d items) instead of overwriting with 0",
                        len(prev))
        return {'national': prev, 'stale_from_previous': True,
                 'soft_block_reason': reason}
    return {'national': []}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('starz', 'Starz', 'streaming', fetch)
    print(f"starz: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
