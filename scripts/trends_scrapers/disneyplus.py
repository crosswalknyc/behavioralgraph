"""
Disney+ trending scraper (public catalog).

Disney+'s /browse/* pages ship a very rich public catalog that doesn't
require an authenticated session - Google and other search engines
index this to make Disney content discoverable. Titles are shipped in
the Next.js `stitchDocument` at:

    props.pageProps.stitchDocument.mainContent[].items[].children[]
      .displayText.content[].text | .text | .value

The DOM also carries `/browse/entity-<uuid>` anchors that point to each
title's detail page.

Important: Disney+'s CDN (Bamgrid) IP-gates aggressively. Requests from
datacenter IPs (Hetzner, generic AWS ranges, etc.) get a 76KB error
page ("Sorry, something went wrong. Please try again later."). Only
requests from residential-looking IPs receive real content.

For that reason this scraper is designed to run from either:

  1. A residential IP (Jenna's laptop via a scheduled local runner)
  2. A residential-proxy endpoint (Bright Data / Oxylabs / IPRoyal)

Cookies are NOT required for /browse/* pages, so the donation flow is
optional here. If donated cookies are present we'll still inject them
(cheap upside if Bamgrid ever pivots to session-gating).

Standalone (locally on your laptop):
    python3 -m scripts.trends_scrapers.disneyplus

The result is uploaded to
    s3://dashboard-inputs/trends_iq_snapshots/latest/disneyplus.json
so the dashboard picks it up regardless of which host produced it.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from html import unescape
from typing import Any, Iterable

from ._base import run_scraper, http_get
from ._playwright import render_pages

logger = logging.getLogger(__name__)


# Public /browse/* pages that reliably ship a real catalog. `browse/espn`
# is the ESPN+ programming (Disney bundle) - keep that here since it's
# structurally identical to any other Disney+ hub, and drop the standalone
# ESPN+ scraper.
DISNEYPLUS_URLS = [
    # These are the six real Disney+ brand hubs. Everything else
    # (`/browse/movies`, `/browse/originals`, `/browse/series`,
    # `/browse/new`, `/browse/kids`, `/browse/sports`, `/browse/brands`)
    # returns a hard 404 - the site has moved to per-brand curated
    # pages only.
    ('browse_disney',              'https://www.disneyplus.com/browse/disney'),
    ('browse_marvel',              'https://www.disneyplus.com/browse/marvel'),
    ('browse_star_wars',           'https://www.disneyplus.com/browse/star-wars'),
    ('browse_pixar',               'https://www.disneyplus.com/browse/pixar'),
    ('browse_national_geographic', 'https://www.disneyplus.com/browse/national-geographic'),
    # /browse/espn is ESPN+ programming under the Disney bundle and is
    # scraped separately by `espnplus.py`. /browse/hulu is bundled Hulu
    # content and is covered by the standalone `hulu.py` scraper via
    # hulu.com. We intentionally leave both out so each dashboard tab
    # gets a focused feed.
]


# Text that Bamgrid returns from datacenter IPs. If we see this instead
# of real content, log clearly so the operator knows to switch runner.
_BAMGRID_ERROR_MARKER = 'dss-error-page-config'


_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.+?)</script>',
    re.DOTALL,
)


def _walk_dicts(o: Any) -> Iterable[dict]:
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk_dicts(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk_dicts(v)


# Nav / footer / marketing text that appears in stitchDocument alongside
# real content. Same rejection pattern as _streaming_common but tuned to
# what Disney+'s hub pages actually emit.
_NAV_STOPWORDS_LOWER = frozenset({
    'get disney+', 'log in', 'sign up', 'home', 'movies', 'series',
    'espn', 'sports', 'kids', 'search', 'account', 'watchlist',
    'nsb login link', 'nsb hero cta - get disney+',
    'nsb nav cta - get disney+', 'footer', 'legal links',
    'help', 'gift disney+', 'about us', 'disney+ partner program',
    'disney bundle', 'press', 'privacy policy',
    'subscriber agreement', 'children\'s online privacy policy',
    'closed captioning', 'interest-based ads', 'supported devices',
    'your us state privacy rights', 'your privacy choices',
})

_NAV_STOPWORD_PREFIXES = (
    'nsb ', 'group -', 'disney+ logo', 'footer -', 'header -',
    'metadata -', 'link -',
)


def _looks_like_title(text: str) -> bool:
    """Heuristic: real content titles don't contain the marketing tokens
    Disney+ uses for nav / CTA labels."""
    t = text.strip()
    if not (2 <= len(t) <= 220):
        return False
    tl = t.lower()
    if tl in _NAV_STOPWORDS_LOWER:
        return False
    if any(tl.startswith(p) for p in _NAV_STOPWORD_PREFIXES):
        return False
    # "GET DISNEY+", "GET DISNEY+ NOW" style buttons
    if 'get disney+' in tl or 'disney+ logo' in tl:
        return False
    return True


def _extract_title_from_node(node: dict) -> str | None:
    """Disney+ stitchDocument stores titles in several equivalent shapes:

        {"displayText": "Some Show"}
        {"displayText": {"text": "Some Show"}}
        {"displayText": {"content": [{"text": "Some Show"}]}}
        {"title": {...}}, {"seriesTitle": {...}}, {"titleText": {...}}
    """
    for k in ('displayText', 'seriesTitle', 'title', 'titleText', 'name'):
        v = node.get(k)
        if isinstance(v, str):
            return v
        if isinstance(v, dict):
            for sub in ('text', 'value', 'default', 'full'):
                sv = v.get(sub)
                if isinstance(sv, str):
                    return sv
            content = v.get('content')
            if isinstance(content, list) and content:
                first = content[0]
                if isinstance(first, dict):
                    for sub in ('text', 'value'):
                        sv = first.get(sub)
                        if isinstance(sv, str):
                            return sv
    return None


def _extract_disneyplus(html: str) -> list[dict]:
    """Walk stitchDocument.mainContent and pull every content-item title.
    Rank is preserved in-order (Disney+ arranges rails by editorial
    curation, so the first N are the surfaced/promoted rails)."""
    if _BAMGRID_ERROR_MARKER in html and len(html) < 200_000:
        # Datacenter block - no point parsing the shell.
        return []
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return []
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    try:
        stitch = obj['props']['pageProps']['stitchDocument']
        main   = stitch.get('mainContent') or []
    except (KeyError, TypeError):
        return []

    seen: set[str] = set()
    out: list[dict] = []
    for node in _walk_dicts(main):
        title = _extract_title_from_node(node)
        if not title:
            continue
        title = unescape(title).strip()
        if not _looks_like_title(title):
            continue
        key = title.lower()
        if key in seen:
            continue
        # Look for a nearby entity id / url for the deep link.
        url = 'https://www.disneyplus.com/'
        for candidate_key in ('href', 'url', 'canonicalPath', 'canonicalUrl'):
            v = node.get(candidate_key)
            if isinstance(v, str) and v.startswith('/'):
                url = f'https://www.disneyplus.com{v}'
                break
            if isinstance(v, str) and v.startswith('http'):
                url = v; break
        # Try to classify: does the node carry a media-type hint?
        classify = ''
        for k in ('programType', 'contentType', 'itemType', 'mediaType', 'category'):
            v = node.get(k)
            if isinstance(v, str):
                vl = v.lower()
                if 'movie' in vl or 'film' in vl:
                    classify = 'Film'; break
                if 'series' in vl or 'show' in vl or 'episode' in vl:
                    classify = 'TV'; break

        seen.add(key)
        out.append({
            'rank':             len(out) + 1,
            'title':            title,
            'url':              url,
            'category_display': classify,
            'collection':       '',
        })
    return out


def _fetch_via_http(pages: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Plain-HTTP fetch (curl_cffi under the hood). Preferred when running
    locally on a residential IP - much lighter than Playwright and fast."""
    results: list[tuple[str, str]] = []
    for label, url in pages:
        r = http_get(url, timeout=30, cookie_domain='disneyplus.com')
        if r is None:
            logger.warning("disneyplus %s: http_get returned None", label)
            continue
        try:
            html = r.text if hasattr(r, 'text') else r.decode('utf-8')
        except Exception:
            continue
        results.append((label, html))
    return results


def fetch() -> dict[str, Any]:
    # Try Playwright first (works from a residential-IP host with cookies,
    # and gives us hydrated HTML). If we detect the Bamgrid datacenter
    # error page, fall back to curl_cffi which is cheaper and gives the
    # same result on the same IP.
    rendered = render_pages(DISNEYPLUS_URLS,
                             homepage='https://www.disneyplus.com/',
                             cookie_domain='disneyplus.com',
                             wait_ms=4000,
                             scroll_ms=2500,
                             hydration_wait_ms=12000)

    # Datacenter-block detection: if every page came back as the ~76KB
    # error shell, retry via plain HTTP (sometimes shorter path works)
    # then bail with a clear message.
    if rendered and all(_BAMGRID_ERROR_MARKER in html and len(html) < 200_000
                        for _, html in rendered):
        logger.warning("disneyplus: all pages returned the Bamgrid IP-gate "
                        "error shell. Datacenter IP is being blocked; run "
                        "this scraper from a residential IP (laptop) or a "
                        "residential proxy.")
        rendered = _fetch_via_http(DISNEYPLUS_URLS)

    all_items: list[dict] = []
    seen: set[str] = set()
    for label, html in rendered:
        items = _extract_disneyplus(html)
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            all_items.append(it)
        logger.info("disneyplus %s: parsed %d titles from %d-byte HTML",
                     label, len(items), len(html))

    for i, it in enumerate(all_items[:25], start=1):
        it['rank'] = i
    return {'national': all_items[:25]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('disneyplus', 'Disney+', 'streaming', fetch)
    print(f"disneyplus: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
