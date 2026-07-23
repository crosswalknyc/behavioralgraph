"""
Book charts scraper.

Aggregates top-selling books signals into a single snapshot the dashboard
renders as one tab, mirroring the structure of `music_charts.py` and
`podcast_charts.py`.

Sources (2026-07):

    Amazon Books best-sellers   -> `amazon.com/gp/bestsellers/books`, public
                                    HTML page. ~30 titles per page across
                                    Fiction / Nonfiction / all-books rails.
    Apple Books Top 100         -> `rss.marketingtools.apple.com`, public
                                    JSON RSS. Two sub-flavors (top-free,
                                    top-paid).
    Audible best-sellers        -> stub (audible.com/adblbestsellers renders
                                    client-side; ships with an operator
                                    instruction until cookies are donated).
    Spotify audiobooks          -> stub (open.spotify.com/genre/audiobooks-web
                                    is a ~6KB React shell; no public JSON
                                    chart endpoint exists and the Spotify
                                    Web API doesn't expose a "top audiobooks"
                                    surface. Populates once open.spotify.com
                                    cookies are donated - same session that
                                    unlocks Spotify Podcast Charts.

Snapshot shape (kind='book'):

    {
      "source":     "book_charts",
      "kind":       "book",
      "label":      "Books",
      "fetched_at": "...",
      "sources": {
        "amazon":  {"label": "Amazon Best-Sellers", "items": [{...}], "available": bool},
        "apple":   {"label": "Apple Books Top 100 (US)", "items": [{...}], "available": bool},
        "audible": {"label": "Audible Best-Sellers", "items": [], "available": False, "sub": "..."},
        "spotify": {"label": "Spotify Audiobooks",   "items": [], "available": False, "sub": "..."}
      }
    }

Every `items[i]` has at least:

    { rank, title, artist, url, image? }

`artist` is the primary author (kept named `artist` to match music /
podcast rows so the frontend row renderer can be shared).

Standalone:

    python3 -m scripts.trends_scrapers.book_charts
"""

from __future__ import annotations

import html as _html
import logging
import re
import sys
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) '
        'Gecko/20100101 Firefox/120.0')


# ---------------------------------------------------------------------------
# Amazon Books Best-Sellers  (public HTML)
# ---------------------------------------------------------------------------
_AMAZON_BOOKS_URL = 'https://www.amazon.com/gp/bestsellers/books'

# Amazon's zg-list ships each ranked entry inside
#     <div id="p13n-asin-index-N" ...>...</div>
# blocks. Fields we care about:
#   rank      : <span class="zg-bdg-text">#N</span>
#   title     : first `<div ... _cDEzb_p13n-sc-css-line-clamp-*>TITLE</div>`
#               inside the block
#   author    : second `_cDEzb_p13n-sc-css-line-clamp-*` div (or a
#               <span class="a-size-small a-color-base">AUTHOR</span>)
#   image     : first <img src="...images-amazon..."> in the block
#   detail_url: first <a href="/DETAIL_PATH/dp/{ASIN}/..."> in the block
#   price     : <span class="p13n-sc-price">$N.NN</span> when set
#
# The DOM is stable enough that we regex it (Beautiful Soup would work
# but adds a dep for zero benefit here).
_ASIN_BLOCK_RE = re.compile(
    r'<div id="p13n-asin-index-(\d+)"(.*?)(?=<div id="p13n-asin-index-\d+"|</ol>)',
    re.DOTALL | re.IGNORECASE,
)
_ASIN_TITLE_RE = re.compile(
    r'<div[^>]*_cDEzb_p13n-sc-css-line-clamp[^>]*>\s*([^<][^<]{2,300})\s*</div>',
    re.DOTALL,
)
_ASIN_AUTHOR_ROW_RE = re.compile(
    # Amazon's byline rail. Books have either a <a> author link or a
    # <span> for "Various" / "Multiple Authors". Sometimes the author
    # row is a Kindle format label ("Kindle Edition") on the first hit,
    # so we skip anything that matches a Kindle/format keyword.
    r'<a[^>]*class="a-size-small a-link-child"[^>]*>([^<]{2,120})</a>'
    r'|<span class="a-size-small a-color-base"[^>]*>([^<]{2,120})</span>',
    re.IGNORECASE,
)
_ASIN_RANK_RE = re.compile(r'zg-bdg-text">#(\d+)')
_ASIN_IMG_RE = re.compile(
    r'<img[^>]*src="(https://[^"]*images-amazon[^"]+)"',
    re.IGNORECASE,
)
_ASIN_URL_RE = re.compile(
    r'<a[^>]*href="(/[^"]+/dp/([A-Z0-9]{10})[^"]*)"',
    re.IGNORECASE,
)
_ASIN_PRICE_RE = re.compile(
    r'<span class="p13n-sc-price"[^>]*>\$?([\d.,]+)</span>',
    re.IGNORECASE,
)
_KINDLE_LIKE_RE = re.compile(
    r'^(Kindle Edition|Paperback|Hardcover|Audible Audiobook|Board book|'
    r'Mass Market Paperback|Spiral-bound|Library Binding|Audio CD|'
    r'Ring-bound|Loose Leaf)$',
    re.IGNORECASE,
)


def _fetch_amazon_books(limit: int = 30) -> list[dict]:
    """Parse `amazon.com/gp/bestsellers/books` into the standard chart
    row shape. ~30 titles per page; the page also carries the top
    Fiction and top Non-fiction subrails but we only pick the primary
    ranked list. Silent failure returns [].
    """
    try:
        r = requests.get(_AMAZON_BOOKS_URL,
                         headers={'User-Agent': _UA,
                                    'Accept-Language': 'en-US,en;q=0.9',
                                    'Accept': 'text/html,application/xhtml+xml'},
                         timeout=20)
    except Exception as e:
        logger.warning("amazon books: %s", e)
        return []
    if not r.ok:
        logger.warning("amazon books: http %s", r.status_code)
        return []
    html = r.text or ''
    items: list[dict] = []
    for block_m in _ASIN_BLOCK_RE.finditer(html):
        block = block_m.group(2)
        rank_m = _ASIN_RANK_RE.search(block)
        if not rank_m:
            continue
        try:
            rank = int(rank_m.group(1))
        except ValueError:
            continue

        # First non-format `_cDEzb_p13n-sc-css-line-clamp-*` div is the
        # title; the second is the author (occasionally format like
        # "Paperback" comes first, which we skip).
        title = ''
        author = ''
        titles_found: list[str] = []
        for tm in _ASIN_TITLE_RE.finditer(block):
            cand = _html.unescape(tm.group(1).strip())
            if not cand:
                continue
            if _KINDLE_LIKE_RE.match(cand):
                # Skip format labels
                continue
            titles_found.append(cand)
            if len(titles_found) >= 3:
                break
        if titles_found:
            title = titles_found[0]
            if len(titles_found) > 1:
                # Prefer titles_found[1] as author unless it also looks
                # like a format label.
                for cand in titles_found[1:]:
                    if not _KINDLE_LIKE_RE.match(cand):
                        author = cand
                        break

        if not author:
            am = _ASIN_AUTHOR_ROW_RE.search(block)
            if am:
                author = _html.unescape((am.group(1) or am.group(2) or '').strip())
                if _KINDLE_LIKE_RE.match(author or ''):
                    author = ''

        if not title:
            continue

        img_m = _ASIN_IMG_RE.search(block)
        url_m = _ASIN_URL_RE.search(block)
        price_m = _ASIN_PRICE_RE.search(block)

        image = img_m.group(1) if img_m else ''
        # Amazon serves 300x200 thumbnails by default. Upgrade to a
        # slightly larger, cropped rendering so cards render sharper
        # on retina without ballooning payload sizes.
        if image and '_SR300,200_' in image:
            image = image.replace('_SR300,200_', '_SR400,600_')
        elif image and '_UL300_' in image:
            image = image.replace('_UL300_', '_UL500_')

        detail_url = ''
        if url_m:
            path = url_m.group(1).split('?', 1)[0].split('/ref=', 1)[0]
            detail_url = f'https://www.amazon.com{path}'

        items.append({
            'rank':   rank,
            'title':  title,
            'artist': author,
            'url':    detail_url,
            'image':  image,
            'price':  (f'${price_m.group(1)}' if price_m else ''),
        })
        if len(items) >= limit:
            break

    # Stable sort by rank (some layouts return blocks out of order when
    # the page contains carousels + a grid).
    items.sort(key=lambda x: x.get('rank') or 999)
    return items


# ---------------------------------------------------------------------------
# Apple Books Top 100 US  (public marketing RSS)
# ---------------------------------------------------------------------------
_APPLE_BOOKS_URL_TMPL = ('https://rss.marketingtools.apple.com/api/v2/us/'
                          'books/top-{tier}/100/books.json')


def _fetch_apple_books(limit: int = 100, tier: str = 'free') -> list[dict]:
    """Apple's public book RSS. `tier` in ('free','paid'). Both are
    charted separately; the dashboard renders whichever has richer
    signal on a given day.
    """
    url = _APPLE_BOOKS_URL_TMPL.format(tier=tier)
    data: dict = {}
    for attempt in range(3):
        try:
            r = requests.get(url, headers={'User-Agent': _UA}, timeout=15)
        except Exception as e:
            logger.info("apple books (%s) attempt %d: %s", tier, attempt + 1, e)
            time.sleep(1 + attempt)
            continue
        if r.ok:
            try:
                data = r.json()
                break
            except Exception as e:
                logger.info("apple books (%s) attempt %d: json parse: %s",
                            tier, attempt + 1, e)
                time.sleep(1 + attempt)
                continue
        else:
            logger.info("apple books (%s) attempt %d: http %s",
                        tier, attempt + 1, r.status_code)
            time.sleep(1 + attempt)
    if not data:
        logger.warning("apple books (%s): gave up after 3 attempts", tier)
        return []
    results = ((data or {}).get('feed') or {}).get('results') or []
    items: list[dict] = []
    for i, t in enumerate(results[:limit], start=1):
        items.append({
            'rank':   i,
            'title':  t.get('name') or '',
            'artist': t.get('artistName') or '',
            'url':    t.get('url') or '',
            'image':  t.get('artworkUrl100') or '',
            'kind':   (t.get('kind') or '').lower(),  # 'book' etc
            'genres': [g.get('name') for g in (t.get('genres') or []) if g.get('name')],
        })
    return items


# ---------------------------------------------------------------------------
# Audible Best-Sellers  (stub - needs cookies)
# ---------------------------------------------------------------------------
def _fetch_audible_books(limit: int = 100) -> tuple[list[dict], str]:
    """Stub. audible.com/adblbestsellers ships a ~250KB React shell with
    no inlined product data. Populates once audible.com cookies are
    donated via `donate_cookies.py audible.com`.
    """
    return [], 'Warming up.'


# ---------------------------------------------------------------------------
# Spotify Audiobooks  (stub - needs cookies + Playwright)
# ---------------------------------------------------------------------------
# Spotify launched audiobooks as part of Premium in 2023 and the browse
# lineup lives at `open.spotify.com/genre/audiobooks-web`. That URL
# returns HTTP 200 to anonymous clients but ships only a ~6KB React
# shell (no server-rendered content); the actual audiobook shelves are
# fetched client-side after auth.
#
# Unlike podcasts, Spotify does NOT expose an `audiobookcharts.byspotify
# .com` marketing subdomain, and the Web API's `/audiobooks/{id}` and
# `/audiobooks?ids=...` endpoints are per-title only (no `top` or
# `popular` list). The only way to get a ranked audiobook lineup today
# is a Playwright scrape of the browse page with a logged-in session.
#
# Populates automatically once open.spotify.com cookies are donated -
# same session that unlocks Spotify Podcast Charts, so one cookie run
# covers both tabs.
def _fetch_spotify_audiobooks(limit: int = 100) -> tuple[list[dict], str]:
    """Stub. Returns ([], operator_sub)."""
    return [], 'Warming up.'


def fetch() -> dict[str, Any]:
    """Pull all four sources. Amazon + Apple are the primary signals
    today; Audible and Spotify audiobooks ship as available=False
    until cookies land.
    """
    amazon_items = _fetch_amazon_books(limit=50)
    # Prefer paid chart when it's populated (books people are actually
    # buying); fall back to free (which is often padded with public-domain
    # classics + Amazon-published freebies).
    apple_items  = _fetch_apple_books(limit=100, tier='paid')
    if not apple_items:
        apple_items = _fetch_apple_books(limit=100, tier='free')
    aud_items,  aud_sub = _fetch_audible_books(limit=100)
    spot_items, spot_sub = _fetch_spotify_audiobooks(limit=100)

    return {
        'national':  amazon_items[:50] or apple_items[:50],
        'available': bool(amazon_items or apple_items or aud_items or spot_items),
        'sources': {
            'amazon': {
                'label':     'Amazon Best-Sellers (Books)',
                'sub':       'The Amazon Best-Sellers list. Refreshes hourly.',
                'items':     amazon_items,
                'available': bool(amazon_items),
            },
            'apple': {
                'label':     'Apple Books Top 100 (US)',
                'sub':       'The Apple Books top-paid chart.',
                'items':     apple_items,
                'available': bool(apple_items),
            },
            'audible': {
                'label':     'Audible Best-Sellers',
                'sub':       aud_sub,
                'items':     aud_items,
                'available': bool(aud_items),
            },
            'spotify': {
                'label':     'Spotify Audiobooks',
                'sub':       spot_sub,
                'items':     spot_items,
                'available': bool(spot_items),
            },
        },
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    from ._base import run_scraper
    result = run_scraper('book_charts', 'Books', 'book', fetch)
    srcs = result.get('sources') or {}
    for slug, panel in srcs.items():
        print(f"{slug}: n={len(panel.get('items', []))}  ok={panel.get('available')}",
               file=sys.stderr)
        for it in (panel.get('items') or [])[:3]:
            print(f"   #{it['rank']} {it['title']} - {it['artist']}", file=sys.stderr)
