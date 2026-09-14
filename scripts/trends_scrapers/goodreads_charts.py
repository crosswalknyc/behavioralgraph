"""
Goodreads trending scraper.

Pulls Goodreads's "Most Read Books This Week In The United States"
rail into a single-panel snapshot the dashboard renders as one
Books-tab card, mirroring the multi-source layout the Books tab
already uses (Amazon / Apple / Audible / Libby / Wattpad).

Rails (1 total, ~50 titles):

    - goodreads_most_read   the top ~50 titles Goodreads flags as
                              "most read this week" scoped to US.
                              This is the same rail Goodreads uses
                              on its own /book/most_read landing
                              page: a community-driven weekly
                              chart of what US Goodreads users are
                              actively logging as read this week.

Snapshot shape (kind='goodreads'):

    {
      "source":     "goodreads_charts",
      "kind":       "goodreads",
      "label":      "Goodreads",
      "fetched_at": "...",
      "sources": {
        "goodreads_most_read": {
          "label":     "Goodreads - Most Read This Week",
          "sub":       "...",
          "items":     [ ~50 items ],
          "available": bool
        }
      }
    }

Every `items[i]` has:

    {
      "rank":                    int,      # Most-Read-This-Week position 1..~50
      "title":                   str,
      "artist":                  str,      # author (mirrors the
                                             # 'artist' key on music /
                                             # book / podcast rows so
                                             # the shared frontend row
                                             # renderer picks it up)
      "author":                  str,      # duplicate of artist for
                                             # readability
      "image":                   str,      # cover image url
      "cover_url":               str,      # alias for image
      "book_url":                str,      # canonical /book/show/<id>
      "url":                     str,      # alias for book_url
      "book_id":                 str,      # goodreads numeric id
      "currently_reading_count": int,      # community weekly read
                                             # count Goodreads exposes
                                             # right on the tile
                                             # ("X people read it").
                                             # Strong ground-truth
                                             # signal for how many US
                                             # Goodreads users are on
                                             # this book THIS week.
      "avg_rating":              float,    # 0..5
      "ratings_count":           int,      # cumulative ratings on
                                             # the book overall
      "published_year":          int,      # first publication year,
                                             # 0 when Goodreads doesn't
                                             # print one
      "is_new_release":          bool,     # published <= 30 days ago
                                             # (best-effort, see
                                             # `_looks_like_new_release`)
    }

Cookies: NOT required. Goodreads's /book/most_read page is a public
browse surface. `curl_cffi` Chrome-TLS impersonation is used
defensively (edge caches occasionally 403 a raw requests fingerprint
under load).

Standalone:

    python3 -m scripts.trends_scrapers.goodreads_charts
"""

from __future__ import annotations

import html as _html
import logging
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# curl_cffi is a drop-in requests replacement that impersonates real
# Chrome at the TLS layer (JA3 fingerprint). Goodreads normally serves
# /book/most_read to unauthenticated clients, but Cloudflare has been
# observed to 403 raw requests fingerprints under regional load. Chrome-
# TLS impersonation is a cheap reliability margin. Falls back to plain
# requests when curl_cffi isn't installed.
try:
    from curl_cffi import requests as _cc_requests  # type: ignore
    _HAS_CURL_CFFI = True
except ImportError:
    import requests as _cc_requests  # type: ignore
    _HAS_CURL_CFFI = False

_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/'
       '537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

# Public browse URL. `duration=w` is the weekly window (matches the
# server-rendered "Most Read Books This Week" heading), and
# `country=US` scopes the community count to US Goodreads users.
# Verified 2026-09-01: both the bare `/book/most_read` and the
# fully-parameterised `?duration=w&country=US` return the same US-
# scoped chart from a US-egress client. The explicit params future-
# proof against Goodreads defaulting to global if the reverse-proxy
# geolocation ever changes.
_GOODREADS_MOST_READ_URL = (
    'https://www.goodreads.com/book/most_read?duration=w&country=US'
)

_WARMING_UP_HINT = 'Warming up. Check back later.'


def _mark_cookie_gap(source: str, domain: str, reason: str = '') -> None:
    """Fire the operator-facing SES notification. Best-effort; never
    raises. The dashboard tile only ever sees `_WARMING_UP_HINT`.
    """
    try:
        from .cookie_gap_notify import notify_cookie_gap
        notify_cookie_gap(source, domain, reason=reason)
    except Exception as e:
        logger.info("cookie_gap notify failed for %s/%s: %s",
                    source, domain, e)


# ---------------------------------------------------------------------------
# HTML transport
# ---------------------------------------------------------------------------
def _get_html(url: str, *, retries: int = 3, timeout: int = 25) -> str:
    """GET a Goodreads HTML page. Chrome-TLS impersonate when
    available. Retries with jittered backoff. Returns '' on total
    failure so callers can no-op cleanly."""
    last_err = None
    for attempt in range(retries):
        try:
            kwargs = {'headers': {'User-Agent': _UA,
                                  'Accept': ('text/html,application/'
                                             'xhtml+xml,application/xml;'
                                             'q=0.9,*/*;q=0.8'),
                                  'Accept-Language': 'en-US,en;q=0.9'},
                      'timeout': timeout,
                      'allow_redirects': True}
            if _HAS_CURL_CFFI:
                kwargs['impersonate'] = 'chrome124'
            r = _cc_requests.get(url, **kwargs)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))
            continue
        status = getattr(r, 'status_code', 0)
        if status == 429 or status >= 500:
            last_err = f"http {status}"
            time.sleep(3 * (attempt + 1))
            continue
        if not r.ok:
            logger.info("goodreads %s: http %s", url, status)
            return ''
        return r.text or ''
    logger.warning("goodreads %s: exhausted retries; last=%s",
                   url, last_err)
    return ''


# ---------------------------------------------------------------------------
# Row parsing
# ---------------------------------------------------------------------------
# Every book row on /book/most_read is a `<tr itemscope
# itemtype="http://schema.org/Book">...</tr>` inside the single
# `<table class="tableList">`. The regexes below key off itemprop
# markers so a Goodreads CSS-class rename doesn't silently break us.

_TABLELIST_RE = re.compile(
    r'<table[^>]*class="tableList"[^>]*>([\s\S]*?)</table>',
    re.IGNORECASE,
)
_TR_RE = re.compile(
    r'<tr\s+itemscope\s+itemtype="http://schema\.org/Book">([\s\S]*?)</tr>',
    re.IGNORECASE,
)

_RANK_RE = re.compile(
    r'<td[^>]*class="number"[^>]*>\s*(\d+)\s*</td>',
    re.IGNORECASE,
)
# Book anchor: `<div id="228820257" ...>` gives us the canonical
# numeric id; the `<a class="bookTitle" href="/book/show/<id>-<slug>">`
# gives us both the URL and the human-readable slug.
_BOOK_ID_RE = re.compile(
    r'<div\s+id="(\d+)"\s+class="u-anchorTarget"',
    re.IGNORECASE,
)
_BOOK_URL_RE = re.compile(
    r'<a[^>]*class="bookTitle"[^>]*href="(/book/show/[^"]+)"',
    re.IGNORECASE,
)
_COVER_RE = re.compile(
    r'<img[^>]*class="bookCover"[^>]*itemprop="image"[^>]*src="([^"]+)"',
    re.IGNORECASE,
)
_TITLE_RE = re.compile(
    r"<span\s+itemprop=['\"]name['\"][^>]*role=['\"]heading['\"][^>]*>"
    r"([^<]+)</span>",
    re.IGNORECASE,
)
_AUTHOR_RE = re.compile(
    r'<a[^>]*class="authorName"[^>]*>[\s\S]*?'
    r'<span\s+itemprop="name">([^<]+)</span>',
    re.IGNORECASE,
)
# Rating + rating count line looks like:
#   4.62 avg rating &mdash; 311,279 ratings
_RATING_LINE_RE = re.compile(
    r'(\d+(?:\.\d+)?)\s*avg\s+rating\s*[&#\w;\s-]*?([\d,]+)\s+ratings',
    re.IGNORECASE,
)
# "25,049 people read it" - the community weekly-read count (the
# same signal that drives the Most-Read-This-Week ordering).
_READ_COUNT_RE = re.compile(
    r'<span[^>]*class="greyText\s+statistic"[^>]*>[\s\S]*?'
    r'(\d[\d,]*)\s*people\s+read\s+it',
    re.IGNORECASE,
)
# "published 2026" - single-year publication marker Goodreads prints
# right underneath the rating line.
_PUBLISHED_RE = re.compile(
    r'published\s+(\d{4})',
    re.IGNORECASE,
)


def _clean_text(s: str) -> str:
    """HTML-unescape + strip whitespace. Handles &amp;, &#39;, etc.
    that show up in book titles + author names."""
    if not s:
        return ''
    return _html.unescape(s).strip()


def _to_int(s: str) -> int:
    if not s:
        return 0
    try:
        return int((s or '').replace(',', '').strip())
    except Exception:
        return 0


def _to_float(s: str) -> float:
    try:
        return float((s or '').strip())
    except Exception:
        return 0.0


def _looks_like_new_release(published_year: int) -> bool:
    """Best-effort: True if `published_year` matches the current year
    AND we're in the first 30 days of it, OR the previous year and
    we're still within 30 days of Jan 1. Goodreads prints only the
    year, not the exact publication date, so we can't do a strict
    "published in the last 30 days" check without a per-book fetch
    (which would multiply the request count by 50). This heuristic
    is deliberately conservative - it never over-flags."""
    if not published_year:
        return False
    today = datetime.now(timezone.utc).date()
    year = today.year
    day_of_year = today.timetuple().tm_yday
    if published_year == year and day_of_year <= 30:
        return True
    if published_year == year - 1 and day_of_year <= 30:
        return True
    return False


def _canonical_book_url(book_url: str) -> str:
    if not book_url:
        return ''
    if book_url.startswith('http'):
        return book_url
    if book_url.startswith('/'):
        return f'https://www.goodreads.com{book_url}'
    return f'https://www.goodreads.com/{book_url}'


def _parse_row(tr_html: str) -> dict:
    """Turn one `<tr itemscope itemtype='...Book'>...</tr>` into a
    normalized row. Returns {} if the row is missing a title (Goodreads
    occasionally ships an empty placeholder row when a book gets
    de-indexed mid-week)."""
    row: dict[str, Any] = {}

    m = _RANK_RE.search(tr_html)
    row['rank'] = _to_int(m.group(1)) if m else 0

    m = _BOOK_ID_RE.search(tr_html)
    row['book_id'] = (m.group(1) or '').strip() if m else ''

    m = _BOOK_URL_RE.search(tr_html)
    book_path = m.group(1) if m else ''
    row['book_url'] = _canonical_book_url(book_path)
    row['url'] = row['book_url']

    m = _COVER_RE.search(tr_html)
    row['image']     = m.group(1) if m else ''
    row['cover_url'] = row['image']

    m = _TITLE_RE.search(tr_html)
    title = _clean_text(m.group(1)) if m else ''
    row['title'] = title

    m = _AUTHOR_RE.search(tr_html)
    author = _clean_text(m.group(1)) if m else ''
    row['artist'] = author
    row['author'] = author

    m = _RATING_LINE_RE.search(tr_html)
    if m:
        row['avg_rating']    = _to_float(m.group(1))
        row['ratings_count'] = _to_int(m.group(2))
    else:
        row['avg_rating']    = 0.0
        row['ratings_count'] = 0

    m = _READ_COUNT_RE.search(tr_html)
    row['currently_reading_count'] = _to_int(m.group(1)) if m else 0

    m = _PUBLISHED_RE.search(tr_html)
    published_year = _to_int(m.group(1)) if m else 0
    row['published_year'] = published_year
    row['is_new_release'] = _looks_like_new_release(published_year)

    if not row.get('title'):
        return {}
    return row


def _load_previous_panel(source: str, panel_key: str) -> list[dict]:
    """When today's fetch comes back empty, fall back to yesterday's
    snapshot so the tile always has something to render. Uses the
    standard `read_snapshot` helper (which reads `latest/{source}.json`)."""
    try:
        from ._base import read_snapshot
        prior = read_snapshot(source) or {}
    except Exception:
        return []
    return ((prior.get('sources') or {}).get(panel_key)
            or {}).get('items') or []


# ---------------------------------------------------------------------------
# Rail: Most Read This Week (US)
# ---------------------------------------------------------------------------
def _fetch_most_read_this_week(limit: int = 50) -> list[dict]:
    """Parse the /book/most_read US-scoped weekly rail. Returns up to
    `limit` rows, ordered by Goodreads's own community weekly-read
    ranking. Empty list on any transport / parse failure so the
    caller can prior-snapshot fall back."""
    html = _get_html(_GOODREADS_MOST_READ_URL)
    if not html:
        return []

    m = _TABLELIST_RE.search(html)
    if not m:
        logger.warning("goodreads most_read: no tableList in HTML "
                       "(len=%d)", len(html))
        return []
    body = m.group(1)

    rows: list[dict] = []
    seen_ids: set[str] = set()
    for tr_match in _TR_RE.finditer(body):
        row = _parse_row(tr_match.group(1))
        if not row:
            continue
        book_id = row.get('book_id') or ''
        if book_id and book_id in seen_ids:
            continue
        if book_id:
            seen_ids.add(book_id)
        rows.append(row)
        if len(rows) >= limit:
            break

    # Re-rank contiguously in case a row's inline rank was blank.
    for i, r in enumerate(rows, start=1):
        r['rank'] = i
    return rows


# ---------------------------------------------------------------------------
# Fetch entry point
# ---------------------------------------------------------------------------
def fetch() -> dict[str, Any]:
    """Run every rail (currently just the one) and return the
    snapshot dict `run_scraper` writes to S3."""
    sources: dict[str, dict] = {}
    all_flat: list[dict] = []

    _RAIL_LABELS = {
        'goodreads_most_read': (
            'Goodreads - Most Read This Week',
            "The books US Goodreads users read the most in the past "
            "7 days. Community-driven weekly signal.",
        ),
    }

    def _wrap(panel_key: str, items: list[dict]) -> None:
        label, sub = _RAIL_LABELS[panel_key]
        available = bool(items)
        if not available:
            prior = _load_previous_panel('goodreads_charts', panel_key)
            if prior:
                items = prior
                logger.info("goodreads %s: falling back to prior snapshot "
                            "(%d items)", panel_key, len(prior))
                _mark_cookie_gap('goodreads_charts', 'goodreads.com',
                                 reason=f'{panel_key} empty response')
        sources[panel_key] = {
            'label':     label,
            'sub':       sub if available else _WARMING_UP_HINT,
            'items':     items,
            'available': available,
        }
        for it in items:
            all_flat.append({**it, 'rail': panel_key})

    _wrap('goodreads_most_read', _fetch_most_read_this_week(limit=50))
    time.sleep(0.5)  # be polite even though only one page today

    return {
        'national':  all_flat[:50],
        'available': any(s['available'] for s in sources.values()),
        'sources':   sources,
    }


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    from ._base import run_scraper
    result = run_scraper('goodreads_charts', 'Goodreads', 'goodreads',
                         fetch)
    srcs = result.get('sources') or {}
    for slug, panel in srcs.items():
        print(f"{slug}: n={len(panel.get('items', []))}  "
              f"ok={panel.get('available')}",
              file=sys.stderr)
        for it in (panel.get('items') or [])[:5]:
            print(f"   #{it['rank']} {it['title'][:44]:<44} - "
                  f"{it['author'][:22]:<22}  "
                  f"reading={it['currently_reading_count']:>7,}  "
                  f"rating={it['avg_rating']} "
                  f"(n={it['ratings_count']:,})",
                  file=sys.stderr)
