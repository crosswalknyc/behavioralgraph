"""
Comics charts scraper.

Aggregates comics / manga / graphic-novel best-seller signals into a
single snapshot the dashboard renders as one Trends IQ tab, mirroring
the structure of `book_charts.py`.

Sources (2026-08):

    Amazon Comics & Graphic Novels best-sellers -> `amazon.com/gp/
                                    bestsellers/books/4366`, public HTML.
                                    Amazon's Kindle-Store bestseller
                                    endpoint (digital-text/6190) does
                                    not server-render; the page ships
                                    a shell with a literal "undefined"
                                    title and hydrates via JS that
                                    refuses to run for headless Chrome,
                                    even with real Google Chrome +
                                    donated session cookies (verified
                                    2026-08-31). The physical-books
                                    Comics & Graphic Novels category
                                    IS server-rendered and covers the
                                    same reading signal (top titles
                                    like Berserk, Fourth Wing GN,
                                    Absolute Batman, Dog Man overlap
                                    Kindle and print).
    Apple Books Comics/Manga    -> `itunes.apple.com/us/rss/topebooks/
                                    limit=25/genre=9026/json`, public
                                    JSON RSS. Genre 9026 = Comics &
                                    Graphic Novels (Books). Two
                                    sub-flavors (top-paid, top-free).
    Libby Comics                -> OverDrive Thunder API keyword search
                                    `query='comic OR manga OR "graphic
                                    novel"'` against LA County Library.
                                    Post-filtered client-side to items
                                    whose `subjects` include a comics
                                    tag (Comic and Graphic Books /
                                    Comics & Manga). The `subject=` and
                                    `subjects=` query params return a
                                    HTTP 400 or are silently ignored by
                                    Thunder v2, so `query` + client-
                                    side filter is the working path
                                    verified 2026-08-31.

Snapshot shape (kind='comics'):

    {
      "source":     "comics_charts",
      "kind":       "comics",
      "label":      "Comics",
      "fetched_at": "...",
      "sources": {
        "amazon_kindle":  {"label": "Amazon Comics", "items": [{...}], "available": bool},
        "apple_comics":   {"label": "Apple Books Comics", "items": [{...}], "available": bool},
        "libby_comics":   {"label": "Libby Comics", "items": [{...}], "available": bool}
      }
    }

Every `items[i]` has at least:

    { rank, title, artist, url, image? }

`artist` is the primary author (kept named `artist` to match
music / book / podcast rows so the frontend row renderer can be
shared).

Standalone:

    python3 -m scripts.trends_scrapers.comics_charts
"""

from __future__ import annotations

import html as _html
import logging
import re
import sys
import time
import urllib.parse
from typing import Any

import requests

logger = logging.getLogger(__name__)

_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:120.0) '
        'Gecko/20100101 Firefox/120.0')


# ---------------------------------------------------------------------------
# Amazon Comics & Graphic Novels bestsellers  (public HTML, curl_cffi)
# ---------------------------------------------------------------------------
# The Kindle Store bestseller endpoint (digital-text/6190) does not
# server-render its top 100 - Amazon serves a shell whose <title> is
# literally "Best undefined" and whose product grid hydrates from
# a JS payload that never fires for headless clients (including real
# Chrome via Playwright, verified 2026-08-31). The physical-books
# Comics & Graphic Novels category IS server-rendered and returns the
# same p13n-asin-index block layout book_charts.py already parses.
# The bestseller signal overlaps heavily: the top-selling comics on
# Amazon (Fourth Wing GN, Berserk Vol 1-3, Absolute Batman, Dog Man,
# Jujutsu Kaisen, Warriors GN) are the same series across Kindle and
# print bestseller feeds.
_AMAZON_COMICS_URL_TMPL = (
    'https://www.amazon.com/gp/bestsellers/books/4366?_encoding=UTF8&pg={pg}')
_AMAZON_COMICS_PAGES = 2   # 30 items per page - 60 top titles is plenty

# Reuse the same DOM regexes book_charts.py uses. Amazon's zg-list
# markup is stable across every /gp/bestsellers/books/<id> subcategory.
_ASIN_BLOCK_RE = re.compile(
    r'<div id="p13n-asin-index-(\d+)"(.*?)(?=<div id="p13n-asin-index-\d+"|</ol>)',
    re.DOTALL | re.IGNORECASE,
)
_ASIN_TITLE_RE = re.compile(
    r'<div[^>]*_cDEzb_p13n-sc-css-line-clamp[^>]*>\s*([^<][^<]{2,300})\s*</div>',
    re.DOTALL,
)
_ASIN_AUTHOR_ROW_RE = re.compile(
    r'<a[^>]*class="a-size-small a-link-child"[^>]*>([^<]{2,120})</a>'
    r'|<span class="a-size-small a-color-base"[^>]*>([^<]{2,120})</span>',
    re.IGNORECASE,
)
_ASIN_RANK_RE = re.compile(r'zg-bdg-text">#(\d+)')
_ASIN_IMG_RE = re.compile(
    r'<img[^>]*src="(https://[^"]*images-amazon[^"]+)"', re.IGNORECASE)
_ASIN_URL_RE = re.compile(
    r'<a[^>]*href="(/[^"]+/dp/([A-Z0-9]{10})[^"]*)"', re.IGNORECASE)
_ASIN_PRICE_RE = re.compile(
    r'<span class="p13n-sc-price"[^>]*>\$?([\d.,]+)</span>', re.IGNORECASE)
_KINDLE_LIKE_RE = re.compile(
    r'^(Kindle Edition|Paperback|Hardcover|Audible Audiobook|Board book|'
    r'Mass Market Paperback|Spiral-bound|Library Binding|Audio CD|'
    r'Ring-bound|Loose Leaf|Comic)$',
    re.IGNORECASE,
)


def _fetch_amazon_page(pg: int) -> list[dict]:
    """Parse one page of Amazon's Comics & Graphic Novels bestsellers.
    Uses curl_cffi if available (Chrome-TLS impersonation lets us slip
    past Amazon's JA3 fingerprint gate); falls back to plain requests
    otherwise. Silent failure returns []."""
    url = _AMAZON_COMICS_URL_TMPL.format(pg=pg)
    try:
        from ._base import _HAS_CURL_CFFI
        if _HAS_CURL_CFFI:
            from curl_cffi import requests as cc  # type: ignore
            r = cc.get(url, impersonate='chrome124', headers={
                'User-Agent': _UA,
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': ('text/html,application/xhtml+xml,'
                             'application/xml;q=0.9,*/*;q=0.8'),
                'Referer': 'https://www.amazon.com/',
            }, timeout=25)
        else:
            r = requests.get(url, headers={
                'User-Agent': _UA,
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': ('text/html,application/xhtml+xml,'
                             'application/xml;q=0.9,*/*;q=0.8'),
                'Referer': 'https://www.amazon.com/',
            }, timeout=25)
    except Exception as e:
        logger.warning("amazon comics pg=%d: %s", pg, e)
        return []
    if not getattr(r, 'ok', False):
        logger.warning("amazon comics pg=%d: http %s", pg,
                       getattr(r, 'status_code', 0))
        return []

    html_body = r.text or ''
    items: list[dict] = []
    for block_m in _ASIN_BLOCK_RE.finditer(html_body):
        block = block_m.group(2)
        rank_m = _ASIN_RANK_RE.search(block)
        if not rank_m:
            continue
        try:
            rank = int(rank_m.group(1))
        except ValueError:
            continue

        title = ''
        author = ''
        titles_found: list[str] = []
        for tm in _ASIN_TITLE_RE.finditer(block):
            cand = _html.unescape(tm.group(1).strip())
            if not cand or _KINDLE_LIKE_RE.match(cand):
                continue
            titles_found.append(cand)
            if len(titles_found) >= 3:
                break
        if titles_found:
            title = titles_found[0]
            if len(titles_found) > 1:
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
        # Amazon serves ~300x200 thumbnails by default; upgrade to a
        # larger poster-shaped crop so the cover reads sharper.
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
    return items


def _fetch_amazon_comics(limit: int = 60) -> list[dict]:
    """Pull ranks 1-60 from Amazon's Comics & Graphic Novels
    bestsellers across two paginated HTMLs, dedupe by rank (Amazon
    sometimes doubles up on page joins)."""
    combined: dict[int, dict] = {}
    for pg in range(1, _AMAZON_COMICS_PAGES + 1):
        page_items = _fetch_amazon_page(pg)
        for it in page_items:
            combined.setdefault(it['rank'], it)
        if pg < _AMAZON_COMICS_PAGES:
            time.sleep(0.4)
    ordered = sorted(combined.values(), key=lambda x: x.get('rank') or 999)
    return ordered[:limit]


# ---------------------------------------------------------------------------
# Apple Books Comics/Manga Top charts  (public iTunes RSS)
# ---------------------------------------------------------------------------
# Apple's older itunes.apple.com RSS still supports the `genre=<id>`
# path segment. Genre 9026 is Comics & Graphic Novels (Books). The
# newer rss.applemarketingtools.com endpoint accepts `?genre=9026`
# in the query string but silently ignores it, returning the overall
# top-books chart - verified 2026-08-31. The legacy path is the only
# working per-genre feed today.
_APPLE_COMICS_URL_TMPL = (
    'https://itunes.apple.com/us/rss/top{tier}ebooks/limit=100/genre=9026/json')


def _apple_pick_image(entry: dict) -> str:
    imgs = entry.get('im:image') or []
    if isinstance(imgs, list) and imgs:
        last = imgs[-1]
        if isinstance(last, dict):
            return last.get('label') or ''
    return ''


def _apple_pick_link(entry: dict) -> str:
    links = entry.get('link') or []
    if isinstance(links, dict):
        return (links.get('attributes') or {}).get('href') or ''
    for l in links:
        if not isinstance(l, dict):
            continue
        attrs = l.get('attributes') or {}
        if attrs.get('rel') == 'alternate' and attrs.get('href'):
            return attrs['href']
    if links and isinstance(links[0], dict):
        return (links[0].get('attributes') or {}).get('href') or ''
    return ''


def _apple_pick_price(entry: dict) -> str:
    price = entry.get('im:price') or {}
    if isinstance(price, dict):
        attrs = price.get('attributes') or {}
        amount = attrs.get('amount')
        currency = (attrs.get('currency') or '').upper()
        label = price.get('label') or ''
        if amount == '0' or amount == '0.00' or 'get' in (label or '').lower():
            return 'Free'
        if amount:
            try:
                v = float(amount)
                if currency == 'USD':
                    return f'${v:.2f}'
                return f'{v:.2f} {currency}'.strip()
            except Exception:
                pass
        return label or ''
    return ''


def _fetch_apple_comics(limit: int = 50, tier: str = 'paid') -> list[dict]:
    """Apple's per-genre iTunes RSS. `tier` in ('paid','free'). Both
    are charted separately; the dashboard renders whichever has richer
    signal on a given day."""
    tier_path = '' if tier == 'paid' else 'free'
    url = _APPLE_COMICS_URL_TMPL.format(tier=tier_path)
    data: dict = {}
    for attempt in range(3):
        try:
            r = requests.get(url, headers={'User-Agent': _UA}, timeout=15)
        except Exception as e:
            logger.info("apple comics (%s) attempt %d: %s", tier, attempt + 1, e)
            time.sleep(1 + attempt)
            continue
        if r.ok:
            try:
                data = r.json()
                break
            except Exception as e:
                logger.info("apple comics (%s) attempt %d: json parse: %s",
                            tier, attempt + 1, e)
                time.sleep(1 + attempt)
                continue
        else:
            logger.info("apple comics (%s) attempt %d: http %s",
                        tier, attempt + 1, r.status_code)
            time.sleep(1 + attempt)
    if not data:
        logger.warning("apple comics (%s): gave up after 3 attempts", tier)
        return []

    entries = ((data or {}).get('feed') or {}).get('entry') or []
    if isinstance(entries, dict):
        entries = [entries]
    items: list[dict] = []
    for i, e in enumerate(entries[:limit], start=1):
        name = ''
        if isinstance(e.get('im:name'), dict):
            name = e['im:name'].get('label') or ''
        elif isinstance(e.get('title'), dict):
            name = e['title'].get('label') or ''
        elif isinstance(e.get('title'), str):
            name = e.get('title') or ''
        if not name:
            continue
        artist = ''
        if isinstance(e.get('im:artist'), dict):
            artist = e['im:artist'].get('label') or ''
        genre = ''
        cat = e.get('category')
        if isinstance(cat, dict):
            genre = (cat.get('attributes') or {}).get('label') or ''
        items.append({
            'rank':   i,
            'title':  name,
            'artist': artist,
            'url':    _apple_pick_link(e),
            'image':  _apple_pick_image(e),
            'price':  _apple_pick_price(e),
            'genre':  genre,
        })
    return items


# ---------------------------------------------------------------------------
# Libby (OverDrive) Comics popular  (Thunder API keyword search)
# ---------------------------------------------------------------------------
# OverDrive's Thunder v2 subject filter is broken for the API: sending
# `subject=<name>` returns HTTP 400 ("Bad value for arg subject") and
# sending `subjects=<id>` / `subjectId=<id>` / `subjectSlug=<slug>` is
# silently ignored (returns the overall popularity list unchanged),
# verified 2026-08-31 against the la-county-library instance. The
# working path is the freeform `query` param combined with client-side
# subject filtering: `query='comic OR manga OR "graphic novel"'`
# returns 95/100 comics-adjacent items in the ebook popularity feed
# and lets us drop the 5 non-comics matches (books ABOUT comics, etc.)
# by checking the row's `subjects` list for a comics/manga/graphic tag.
_LIBRARY_KEY = 'lacountylibrary'
_THUNDER_URL = ('https://thunder.api.overdrive.com/v2/libraries/'
                 f'{_LIBRARY_KEY}/media')
_LIBBY_QUERY = 'comic OR manga OR "graphic novel"'
_LIBBY_SUBJECT_KEYWORDS = ('comic', 'graphic', 'manga')

_LIBBY_MEDIA_TYPES = ['ebook', 'audiobook']


def _libby_deep_link(reserve_id: str) -> str:
    if not reserve_id:
        return ''
    return (f'https://libbyapp.com/library/{_LIBRARY_KEY}/'
            f'similar-{urllib.parse.quote(str(reserve_id))}/page-1')


def _best_cover(covers_obj: dict | None) -> str:
    covers = covers_obj or {}
    for key in ('cover510Wide', 'cover300Wide', 'cover150Wide', 'cover'):
        val = covers.get(key)
        if isinstance(val, dict):
            href = val.get('href') or val.get('url') or ''
            if href:
                return href
        elif isinstance(val, str) and val:
            return val
    return ''


def _first_creator_name(item: dict) -> str:
    creators = item.get('creators') or []
    for c in creators:
        name = (c or {}).get('name')
        role = (c or {}).get('role') or ''
        if name and role in ('', 'Author', 'Artist', 'Illustrator'):
            return name
    if creators and (creators[0] or {}).get('name'):
        return creators[0]['name']
    return item.get('firstCreatorName') or ''


def _row_is_comics(item: dict) -> bool:
    """Client-side filter: keep rows whose `subjects` list carries a
    comics/manga/graphic-novel tag. Filters out books ABOUT comics
    (e.g. Marvel Comics: The Untold Story) that the fulltext keyword
    match let through."""
    for s in (item.get('subjects') or []):
        name = (s or {}).get('name') or ''
        low = name.lower()
        if any(k in low for k in _LIBBY_SUBJECT_KEYWORDS):
            return True
    return False


def _fetch_libby_comics_media(media_type: str, limit: int = 30) -> list[dict]:
    """Query Thunder for one media type and filter down to comics."""
    params = {
        'query':      _LIBBY_QUERY,
        'sortBy':     'popularity:desc',
        'mediaTypes': media_type,
        'perPage':    '100',
        'page':       '1',
    }
    resp = None
    for attempt in range(3):
        try:
            resp = requests.get(_THUNDER_URL,
                                headers={'User-Agent': _UA,
                                          'Accept': 'application/json'},
                                params=params,
                                timeout=15)
        except Exception as e:
            logger.info("libby comics %s attempt %d: %s",
                         media_type, attempt + 1, e)
            time.sleep(1 + attempt)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            logger.info("libby comics %s attempt %d: http %s",
                         media_type, attempt + 1, resp.status_code)
            time.sleep(2 + attempt)
            continue
        break
    if not resp or not resp.ok:
        logger.warning("libby comics %s: gave up (last=%s)", media_type,
                        getattr(resp, 'status_code', None))
        return []
    try:
        data = resp.json()
    except Exception as e:
        logger.warning("libby comics %s: json parse: %s", media_type, e)
        return []

    raw = data.get('items') or []
    kept = [it for it in raw if _row_is_comics(it)]
    items: list[dict] = []
    for i, it in enumerate(kept[:limit], start=1):
        rid = it.get('id') or it.get('reserveId') or ''
        title = it.get('title') or ''
        if not title:
            continue
        author = _first_creator_name(it)
        image = _best_cover(it.get('covers'))
        formats = [(f or {}).get('name') for f in (it.get('formats') or [])
                     if (f or {}).get('name')]
        subjects = [(s or {}).get('name') for s in (it.get('subjects') or [])
                     if (s or {}).get('name')]
        items.append({
            'rank':          i,
            'title':         title,
            'artist':        author,
            'url':           _libby_deep_link(rid),
            'image':         image,
            'reserve_id':    rid,
            'holds':         it.get('holdsCount') or 0,
            'availability':  'Available' if it.get('isAvailable') else 'On hold',
            'formats':       formats[:3],
            'subjects':      subjects[:3],
            'media_type':    media_type,
        })
    return items


def _fetch_libby_comics(limit_per_type: int = 30) -> dict[str, list[dict]]:
    """Return {ebook: [...], audiobook: [...]} for the LA County
    Libby collection, ranked by popularity within the comics subject
    filter."""
    out: dict[str, list[dict]] = {}
    for mt in _LIBBY_MEDIA_TYPES:
        out[mt] = _fetch_libby_comics_media(mt, limit=limit_per_type)
    return out


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------
def fetch() -> dict[str, Any]:
    """Pull all live comics sources. Every source is independent - a
    failure on one leaves the others intact and the panel renders a
    "warming up" tile for the missing one."""
    amazon_items = _fetch_amazon_comics(limit=60)

    apple_paid = _fetch_apple_comics(limit=50, tier='paid')
    apple_items = apple_paid
    apple_label_sub = 'Apple Books top-paid comics, manga, and graphic novels.'
    if not apple_items:
        apple_items = _fetch_apple_comics(limit=50, tier='free')
        apple_label_sub = 'Apple Books top-free comics, manga, and graphic novels.'

    libby_by_type = _fetch_libby_comics(limit_per_type=30)
    # Merge ebook + audiobook into a single Libby panel, dedup by title.
    libby_items: list[dict] = []
    seen: set[str] = set()
    for mt in _LIBBY_MEDIA_TYPES:
        for it in libby_by_type.get(mt) or []:
            key = f"{(it.get('title') or '').lower().strip()}|{(it.get('artist') or '').lower().strip()}"
            if key in seen:
                continue
            seen.add(key)
            libby_items.append(it)
    # Re-rank the merged list by original popularity within its type
    # (ebook-first because ebook is the deeper Libby comics collection).
    for i, it in enumerate(libby_items, start=1):
        it['rank'] = i

    national = (amazon_items or apple_items or libby_items)[:50]
    return {
        'national':  national,
        'available': bool(amazon_items or apple_items or libby_items),
        'sources': {
            'amazon_kindle': {
                'label':     'Amazon Comics',
                'sub':       ("Amazon's Best Sellers list for Comics & Graphic "
                              'Novels. Refreshes hourly.'),
                'items':     amazon_items,
                'available': bool(amazon_items),
            },
            'apple_comics': {
                'label':     'Apple Books Comics',
                'sub':       apple_label_sub,
                'items':     apple_items,
                'available': bool(apple_items),
            },
            'libby_comics': {
                'label':     'Libby Comics',
                'sub':       ('Most-borrowed comics, manga, and graphic novels '
                              'on Libby right now.'),
                'items':     libby_items,
                'available': bool(libby_items),
            },
        },
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    from ._base import run_scraper
    result = run_scraper('comics_charts', 'Comics', 'comics', fetch)
    srcs = result.get('sources') or {}
    for slug, panel in srcs.items():
        print(f"{slug}: n={len(panel.get('items', []))}  ok={panel.get('available')}",
               file=sys.stderr)
        for it in (panel.get('items') or [])[:3]:
            print(f"   #{it['rank']} {it['title']} - {it['artist']}",
                   file=sys.stderr)
