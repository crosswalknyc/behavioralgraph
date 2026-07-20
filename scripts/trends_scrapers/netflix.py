"""
Netflix Top 10 scraper.

Two paths, tried in order:

1. **Authenticated daily** (preferred). Playwright + donated `netflix.com`
   cookies. Hits `https://www.netflix.com/browse` and scrapes the
   logged-in home page's "Top 10 TV Shows in the U.S. Today" and
   "Top 10 Movies in the U.S. Today" rows. Netflix refreshes these
   rows daily so the data is at most 24h old. This path requires
   cookies donated via `donate_cookies.py netflix.com` from the
   operator's Chrome (a real Netflix session), which is why this
   scraper is registered in `RESIDENTIAL_SCRAPERS`
   (`local_residential_run.py`) rather than Hetzner's `run_all.py` -
   Netflix's WAF is friendlier to residential IPs and the cookies
   come from the operator's actual browser.

2. **Weekly TSV fallback** (only if no cookies). Netflix's Tudum team
   publishes weekly rankings as public TSV files at:

       https://www.netflix.com/tudum/top10/data/all-weeks-global.tsv
       https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv

   These update every Tuesday afternoon PT with the previous Monday-
   Sunday week's data. So worst case (operator's laptop has been off
   for >7 days, no cookies to reach authenticated path) the dashboard
   shows the most recent weekly Top 10 rather than empty state.

Standalone:
    python3 -m scripts.trends_scrapers.netflix
"""

from __future__ import annotations

import io
import logging
import re
import sys
from html import unescape
from typing import Any, Optional

from ._base import http_get, run_scraper

logger = logging.getLogger(__name__)


_TSV_GLOBAL    = 'https://www.netflix.com/tudum/top10/data/all-weeks-global.tsv'
_TSV_COUNTRIES = 'https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv'


def _slugify_title(title: str) -> str:
    """Netflix's title-page slug pattern - approximate the tudum URL."""
    s = re.sub(r'[^a-z0-9]+', '-', (title or '').lower()).strip('-')
    return s


def _title_url(title: str) -> str:
    """Best-effort deep link to the show/movie's Netflix page."""
    slug = _slugify_title(title)
    if not slug:
        return 'https://www.netflix.com/tudum/top10'
    return f'https://www.netflix.com/tudum/top10/#{slug}'


# ────────────────────────────────────────────────────────────────────
# Path 1: authenticated daily scrape (Playwright + netflix.com cookies)
# ────────────────────────────────────────────────────────────────────
#
# Netflix's logged-in home ships React-rendered rows. Each Top-10 row
# has a heading whose visible text contains "Top 10" and either "TV
# Shows" or "Movies" and "Today". We locate the row by its heading
# text, then walk the sibling container for tile anchors.
#
# The tile anchor pattern (as of 2026-07):
#
#   <a href="/title/<TITLE_ID>" aria-label="Poster of <Title Name>">
#
# aria-label is the cleanest way to get the display title (the tile
# itself renders the poster art via CSS background-image, not text).
# Netflix has kept this aria-label pattern stable for years for
# accessibility reasons - if they ever change it, the scraper logs a
# clear "0 titles parsed" message so we can update the regex.

_NETFLIX_URLS = [
    ('Home', 'https://www.netflix.com/browse'),
]

# Hydrated content signals - Playwright waits until at least one of
# these appears (or the fallback timer fires) before snapshotting HTML.
_NETFLIX_HYDRATE_SELECTORS = [
    'a[href^="/title/"]',
    'div[data-uia^="title-card"]',
    'div.title-card-container',
]

# Match a Top-10 row heading and capture whether it's "TV Shows" or
# "Movies" and whether it's the daily "Today" cut (vs. weekly).
_NETFLIX_ROW_HEADING_RE = re.compile(
    r'<h2[^>]*>\s*Top\s+10\s+(TV\s+Shows|Movies)\s+in\s+the\s+U\.?S\.?\s+Today\s*</h2>',
    re.IGNORECASE,
)

# One title tile. Netflix renders "Poster of <Title>" for accessibility.
_NETFLIX_TILE_RE = re.compile(
    r'<a[^>]+href="(/title/\d+)"[^>]*'
    r'aria-label="(?:Poster of\s+)?([^"]{2,180})"',
    re.IGNORECASE,
)


def _extract_top10_rows(html: str) -> tuple[list[dict], list[dict]]:
    """Given the rendered HTML of netflix.com/browse for a logged-in
    US session, extract (top_10_tv, top_10_films). Each list is up to
    10 items ranked by DOM order (Netflix renders them in rank order).
    Returns ([], []) if the daily-today rows can't be located - the
    caller then falls back to the weekly TSV.
    """
    # Find each daily "Today" row heading and its DOM slice. We take a
    # generous 30KB window after each heading, which comfortably covers
    # the 10 tiles in that row (each tile is ~300-500 bytes of HTML).
    tv_items:    list[dict] = []
    film_items:  list[dict] = []
    for m in _NETFLIX_ROW_HEADING_RE.finditer(html):
        kind = m.group(1).lower()
        start = m.end()
        slice_html = html[start:start + 30_000]
        seen: set[str] = set()
        rows: list[dict] = []
        for tile in _NETFLIX_TILE_RE.finditer(slice_html):
            path = tile.group(1)
            title = unescape(tile.group(2)).strip()
            key = title.lower()
            if not title or key in seen:
                continue
            seen.add(key)
            rows.append({
                'rank':             len(rows) + 1,
                'title':            title,
                'category_display': 'TV' if 'tv' in kind else 'Film',
                'url':              f'https://www.netflix.com{path}',
                'source':           'authenticated_daily',
            })
            if len(rows) >= 10:
                break
        if 'tv' in kind:
            tv_items = rows
        else:
            film_items = rows
    return tv_items, film_items


def _fetch_authenticated_daily() -> Optional[dict]:
    """Try the authenticated Playwright path. Returns a payload dict on
    success, None on failure (caller falls back to weekly TSV)."""
    try:
        from ._playwright import render_pages
        from ._base import cookie_donation_status
    except Exception as e:
        logger.info("netflix: playwright helper unavailable (%s); "
                     "falling back to weekly TSV", e)
        return None

    # Skip the auth path entirely when no netflix.com cookies have been
    # donated. Running Playwright headless against Netflix without
    # a session just gets the marketing landing page, wastes ~30s per
    # invocation, and adds no signal.
    status = cookie_donation_status('netflix.com')
    if not (status and status.get('available')):
        logger.info("netflix: no donated netflix.com cookies "
                     "(donate via `donate_cookies.py netflix.com` from your "
                     "laptop). Falling back to weekly TSV.")
        return None

    logger.info("netflix: attempting authenticated daily scrape "
                 "(cookies age=%sh)", status.get('age_hours'))

    try:
        rendered = render_pages(_NETFLIX_URLS,
                                 homepage='https://www.netflix.com/',
                                 cookie_domain='netflix.com',
                                 wait_selectors=_NETFLIX_HYDRATE_SELECTORS,
                                 wait_ms=6000,
                                 scroll_ms=2500,
                                 hydration_wait_ms=15000)
    except Exception as e:
        logger.warning("netflix: Playwright render failed: %s", e)
        return None

    if not rendered:
        logger.info("netflix: Playwright returned no pages; falling back")
        return None

    # Only one URL, one result. We still guard for len(rendered) == 0.
    _, html = rendered[0]
    logger.info("netflix: rendered %d-byte body", len(html or ''))
    tv_items, film_items = _extract_top10_rows(html or '')

    if not tv_items and not film_items:
        # Two likely causes:
        #   (1) not logged in - Netflix redirected us to the marketing
        #       page, in which case the heading pattern doesn't appear
        #   (2) Netflix changed the heading/tile DOM
        # Fall through to the TSV so the tile isn't empty; the log
        # explains which one it is.
        if 'Top 10' not in (html or ''):
            logger.info("netflix: 'Top 10' text not in rendered body - "
                         "session almost certainly expired. Re-donate cookies.")
        else:
            logger.info("netflix: 'Top 10' text present but tiles didn't "
                         "parse - Netflix DOM likely changed. Update the "
                         "regexes in netflix.py.")
        return None

    logger.info("netflix: authenticated daily parsed %d TV + %d Films",
                 len(tv_items), len(film_items))

    # Interleave films + TV for the `national` display list (rank 1 film,
    # rank 1 tv, rank 2 film, rank 2 tv, ...) - same shape as the weekly
    # TSV fallback so the dashboard can render either transparently.
    national: list[dict] = []
    for i in range(max(len(film_items), len(tv_items))):
        if i < len(film_items):
            national.append({**film_items[i], 'category_display': 'Film'})
        if i < len(tv_items):
            national.append({**tv_items[i], 'category_display': 'TV'})

    from datetime import datetime, timezone
    today_iso = datetime.now(timezone.utc).date().isoformat()
    return {
        'national':     national,
        'us_films':     film_items,
        'us_tv':        tv_items,
        # For the dashboard's "Week of ..." label we stamp today's date
        # since this row is what Netflix showed users today, not a
        # weekly aggregate.
        'week_us':      today_iso,
        'week_global':  today_iso,
        'source_path':  'authenticated_daily',
    }


# ────────────────────────────────────────────────────────────────────
# Path 2: weekly TSV fallback (public data, no auth required)
# ────────────────────────────────────────────────────────────────────
def _parse_tsv(text: str) -> list[dict]:
    """Parse a Netflix top10 TSV blob. First line is the header."""
    lines = text.splitlines()
    if len(lines) < 2:
        return []
    header = lines[0].split('\t')
    rows: list[dict] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) != len(header):
            continue
        rows.append(dict(zip(header, parts)))
    return rows


def _pick_top10_for(rows: list[dict], week_iso: str,
                     category_predicate) -> list[dict]:
    """Filter to a specific week + category and shape into item dicts."""
    filtered = [r for r in rows
                if r.get('week') == week_iso and category_predicate(r.get('category') or '')]
    filtered.sort(key=lambda r: int(r.get('weekly_rank') or 999))
    out: list[dict] = []
    for r in filtered[:10]:
        title  = (r.get('show_title')   or '').strip()
        season = (r.get('season_title') or '').strip()
        if not title:
            continue
        # Netflix's TV rows often set season_title = "<show>: <season>"
        # (fully-qualified), so re-concatenating produces "X: X: Season 2".
        # Use season_title verbatim when it already starts with the show
        # title; otherwise "show: season". Films use "N/A" as season.
        if not season or season == 'N/A':
            display_title = title
        elif season.lower().startswith(title.lower() + ':'):
            display_title = season
        elif season.lower() == title.lower():
            display_title = title
        else:
            display_title = f"{title}: {season}"
        try:
            weeks_in_top10 = int(r.get('cumulative_weeks_in_top_10') or 0)
        except ValueError:
            weeks_in_top10 = 0
        try:
            rank = int(r.get('weekly_rank') or (len(out) + 1))
        except ValueError:
            rank = len(out) + 1
        out.append({
            'rank':            rank,
            'title':           display_title,
            'category':        r.get('category') or '',
            'weeks_in_top10':  weeks_in_top10,
            'url':             _title_url(title),
            'week':            week_iso,
            'source':          'weekly_tsv',
        })
    return out


def _fetch_weekly_tsv() -> dict[str, Any]:
    """Fetch Netflix's public weekly TSV rankings (fallback path).
    Same shape as the authenticated daily payload so downstream code
    doesn't care which path produced the data.
    """
    ua_headers = {'User-Agent':
                     'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                     'AppleWebKit/537.36 (KHTML, like Gecko) '
                     'Chrome/127.0.0.0 Safari/537.36'}

    # ── Country-level (US) ──
    r_countries = http_get(_TSV_COUNTRIES, timeout=30, retries=1,
                            headers=ua_headers)
    us_films: list[dict] = []
    us_tv:    list[dict] = []
    latest_us_week = ''
    if r_countries is not None and r_countries.ok:
        rows = _parse_tsv(r_countries.text)
        us_rows = [r for r in rows if r.get('country_iso2') == 'US']
        if us_rows:
            latest_us_week = max((r.get('week') or '') for r in us_rows)
            us_films = _pick_top10_for(us_rows, latest_us_week,
                                         lambda c: c.strip() == 'Films')
            us_tv    = _pick_top10_for(us_rows, latest_us_week,
                                         lambda c: c.strip() == 'TV')

    # ── Global (English + Non-English breakouts) ──
    r_global = http_get(_TSV_GLOBAL, timeout=30, retries=1,
                         headers=ua_headers)
    global_films_en:    list[dict] = []
    global_tv_en:       list[dict] = []
    global_films_nonen: list[dict] = []
    global_tv_nonen:    list[dict] = []
    latest_global_week = ''
    if r_global is not None and r_global.ok:
        rows = _parse_tsv(r_global.text)
        if rows:
            latest_global_week = max((r.get('week') or '') for r in rows)
            global_films_en = _pick_top10_for(
                rows, latest_global_week,
                lambda c: c.strip().lower() == 'films (english)')
            global_tv_en = _pick_top10_for(
                rows, latest_global_week,
                lambda c: c.strip().lower() == 'tv (english)')
            global_films_nonen = _pick_top10_for(
                rows, latest_global_week,
                lambda c: c.strip().lower() == 'films (non-english)')
            global_tv_nonen = _pick_top10_for(
                rows, latest_global_week,
                lambda c: c.strip().lower() == 'tv (non-english)')

    # Combine US films + US TV as the "national" surface for the tile.
    national: list[dict] = []
    for i in range(10):
        if i < len(us_films):
            national.append({**us_films[i], 'category_display': 'Film'})
        if i < len(us_tv):
            national.append({**us_tv[i], 'category_display': 'TV'})

    return {
        'national':          national,
        'us_films':          us_films,
        'us_tv':             us_tv,
        'global_films_en':   global_films_en,
        'global_tv_en':      global_tv_en,
        'global_films_nonen': global_films_nonen,
        'global_tv_nonen':    global_tv_nonen,
        'week_us':           latest_us_week,
        'week_global':       latest_global_week,
        'source_path':       'weekly_tsv',
    }


def fetch() -> dict[str, Any]:
    """Return the freshest Netflix top-10 we can. Prefers authenticated
    daily (updated every 24h); falls back to weekly TSV.
    """
    payload = _fetch_authenticated_daily()
    if payload and (payload.get('national') or []):
        return payload
    logger.info("netflix: using weekly TSV path")
    return _fetch_weekly_tsv()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('netflix', 'Netflix', 'streaming', fetch)
    print(f"netflix: national={len(result.get('national', []))} "
           f"us_films={len(result.get('us_films', []))} "
           f"us_tv={len(result.get('us_tv', []))} "
           f"week={result.get('week_us')} "
           f"path={result.get('source_path')} "
           f"error={result.get('error')}", file=sys.stderr)
