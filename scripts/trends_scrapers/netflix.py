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

# Match Top-10 row headings on the logged-in browse page. Netflix
# labels these rows several ways depending on locale/A-B test - we
# accept any of them and classify by keyword.
#   - "Top 10 TV Shows in the U.S. Today"
#   - "Top 10 Movies in the U.S. Today"
#   - "Today's Top 10 in the U.S." (single mixed row)
# Match text of ANY tag between opening/closing brackets since Netflix
# switches between <h2>, <h3>, and <span> in different tests.
_NETFLIX_ROW_HEADING_RE = re.compile(
    r'>\s*((?:Today\'?s\s+)?Top\s+10[^<]{0,80}?(?:TV\s+Shows|Movies|in\s+the\s+U\.?S\.?)[^<]{0,80})<',
    re.IGNORECASE,
)

# One title tile inside a Top-10 row. Netflix's authenticated home
# (2026-07) renders each ranked tile as:
#
#   <a href="/browse?jbv=<video_id>"
#      tabindex="-1"
#      aria-label="<Title>"
#      data-uia="ranked-card"
#      class="...">
#
# `data-uia="ranked-card"` is a stable accessibility identifier
# Netflix uses across all its A/B tests for Top-10 tiles - it's the
# right hook. The `href="/browse?jbv=<id>"` pattern is Netflix's
# in-app deep link (jbv = "just-be-video", opens the player). Video
# IDs are numeric.
_NETFLIX_TILE_RE = re.compile(
    r'<a[^>]+href="/browse\?jbv=(\d+)"[^>]*'
    r'aria-label="([^"]{2,220})"[^>]*'
    r'data-uia="ranked-card"',
    re.IGNORECASE,
)


def _classify_row(heading: str) -> str:
    """Classify a Top-10 row heading into 'tv', 'film', or 'mixed'."""
    h = (heading or '').lower()
    if 'tv show' in h or 'series' in h:
        return 'tv'
    if 'movie' in h or 'film' in h:
        return 'film'
    return 'mixed'


def _extract_top10_rows(html: str) -> tuple[list[dict], list[dict]]:
    """Given the rendered HTML of a logged-in browse page, extract
    (top_10_tv, top_10_films). Falls back to a single mixed list if
    Netflix's A/B test only exposes the combined daily row.
    """
    tv_items:    list[dict] = []
    film_items:  list[dict] = []
    mixed_items: list[dict] = []
    for m in _NETFLIX_ROW_HEADING_RE.finditer(html):
        heading = m.group(1).strip()
        kind = _classify_row(heading)
        # Take a generous 40KB window after each heading to cover the
        # 10 tiles in that row (Netflix tiles are ~400-1200 bytes each
        # after all the wrappers/data attributes).
        slice_html = html[m.end():m.end() + 40_000]
        seen: set[str] = set()
        rows: list[dict] = []
        for tile in _NETFLIX_TILE_RE.finditer(slice_html):
            title_id = tile.group(1)
            aria     = unescape(tile.group(2)).strip()
            # aria-label sometimes contains "<Title>. <runtime>. <rating>."
            # Take the first sentence-fragment as the title.
            title = aria.split('.')[0].strip() if '.' in aria else aria
            if len(title) < 2 or len(title) > 200:
                continue
            key = title.lower()
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                'rank':             len(rows) + 1,
                'title':            title,
                'category_display': 'TV' if kind == 'tv' else (
                                     'Film' if kind == 'film' else ''),
                'url':              f'https://www.netflix.com/title/{title_id}',
                'source':           'authenticated_daily',
            })
            if len(rows) >= 10:
                break
        if kind == 'tv':
            tv_items = tv_items or rows
        elif kind == 'film':
            film_items = film_items or rows
        else:
            mixed_items = mixed_items or rows

    # If Netflix only exposed a mixed daily row (no separate TV/Film
    # splits), promote it into both slots so the dashboard still shows
    # something (marked category_display='' so the frontend can style).
    if not tv_items and not film_items and mixed_items:
        return [], mixed_items
    return tv_items, film_items


def _run_netflix_playwright() -> Optional[str]:
    """Launch Chrome, inject cookies, click through the profile picker
    if shown, and return the final rendered HTML of the browse page.
    Returns None on any failure.
    """
    try:
        from ._playwright import _lazy_playwright, _launch_browser, _try_stealth, UA
        from ._base import load_donated_cookies_playwright
    except Exception as e:
        logger.info("netflix: playwright helper import failed: %s", e)
        return None

    sp = _lazy_playwright()
    if sp is None:
        return None

    donated = load_donated_cookies_playwright('netflix.com')
    if not donated:
        logger.info("netflix: no netflix.com cookies in S3")
        return None

    html: Optional[str] = None
    with sp() as pw:
        try:
            browser, _channel = _launch_browser(pw, prefer_chrome=True,
                                                  proxy=None)
        except Exception as e:
            logger.warning("netflix: playwright launch failed: %s", e)
            return None

        ctx = browser.new_context(
            user_agent=UA,
            viewport={'width': 1440, 'height': 900},
            locale='en-US',
            timezone_id='America/New_York',
            extra_http_headers={'Accept-Language': 'en-US,en;q=0.9'},
        )
        try:
            ctx.add_cookies(donated)
            logger.info("netflix: injected %d cookies", len(donated))
        except Exception as e:
            logger.info("netflix: cookie injection failed: %s", e)

        page = ctx.new_page()
        _try_stealth(page)

        try:
            # Homepage warm-up so the session cookies attach cleanly.
            page.goto('https://www.netflix.com/', wait_until='domcontentloaded',
                       timeout=45000)
            page.wait_for_timeout(2000)

            page.goto('https://www.netflix.com/browse',
                       wait_until='domcontentloaded', timeout=45000)

            # Wait for EITHER the profile picker OR the browse tiles
            # to appear. Which one appears first tells us where we are.
            picker_sel = '[data-uia="action-select-profile+primary"], ' \
                         '.profile-link, [data-uia^="action-select-profile"]'
            browse_sel = 'a[href^="/title/"], [data-uia^="title-card"], ' \
                         '.title-card-container'

            profile_link = None
            try:
                # Wait up to 8s for the picker to attach - if not seen we
                # probably landed directly on browse.
                page.wait_for_selector(f'{picker_sel}, {browse_sel}',
                                        timeout=8000, state='attached')
            except Exception:
                pass

            profile_link = page.query_selector(picker_sel)
            if profile_link:
                logger.info("netflix: profile picker detected, clicking primary profile")
                profile_link.click()
                # Netflix loads browse client-side after profile click;
                # wait for a ranked-card tile to appear (the Top-10
                # rows are the highest-priority lazy-loaded content).
                try:
                    page.wait_for_selector(
                        'a[data-uia="ranked-card"], a[href^="/title/"], '
                        '[data-uia^="title-card"]',
                        timeout=25000, state='attached')
                except Exception:
                    logger.info("netflix: no tile after profile click "
                                 "within 25s; snapshotting anyway")

            # Force lazy rows to render by scrolling several times
            # with pauses. Netflix loads rows in batches as they enter
            # viewport, so a single wheel event only gets ~1 row past
            # the fold. Six scrolls at 800px each covers ~5000px of
            # content, which comfortably includes both Top-10 rows.
            page.wait_for_timeout(3000)
            for i in range(6):
                page.mouse.wheel(0, 800)
                page.wait_for_timeout(1200)
            # Scroll back to top so the render captures the earlier
            # rows (which may have unmounted if the virtualizer is
            # aggressive). Then one final small scroll to let the
            # bottom row re-hydrate.
            page.mouse.wheel(0, -6000)
            page.wait_for_timeout(1500)
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(2000)
            html = page.content()
            logger.info("netflix: rendered %d-byte body on final page",
                         len(html or ''))
        except Exception as e:
            logger.warning("netflix: navigation failed: %s", e)

        try:
            ctx.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass

    return html


def _fetch_authenticated_daily() -> Optional[dict]:
    """Try the authenticated Playwright path. Returns a payload dict on
    success, None on failure (caller falls back to weekly TSV)."""
    try:
        from ._base import cookie_donation_status
    except Exception as e:
        logger.info("netflix: cookie_donation_status import failed: %s", e)
        return None

    # Skip the auth path entirely when no netflix.com cookies have been
    # donated. Running Playwright headless against Netflix without
    # a session just gets the marketing landing page, wastes ~30s per
    # invocation, and adds no signal.
    status = cookie_donation_status('netflix.com')
    if not (status and status.get('donated')):
        logger.info("netflix: no donated netflix.com cookies "
                     "(donate via `donate_cookies.py netflix.com` from your "
                     "laptop). Falling back to weekly TSV.")
        return None

    logger.info("netflix: attempting authenticated daily scrape "
                 "(cookies count=%s age=%sh)",
                 status.get('count'), status.get('age_hours'))

    html = _run_netflix_playwright()
    if not html:
        logger.info("netflix: playwright returned nothing; using weekly TSV")
        return None

    tv_items, film_items = _extract_top10_rows(html)

    if not tv_items and not film_items:
        if 'Top 10' not in html:
            logger.info("netflix: 'Top 10' text not in rendered body - "
                         "still on profile picker or session expired. "
                         "Re-donate netflix.com cookies.")
        else:
            logger.info("netflix: 'Top 10' text present but tiles didn't "
                         "parse - Netflix DOM likely changed. Inspect "
                         "/tmp/netflix_body_debug.html.")
            try:
                from pathlib import Path
                Path('/tmp/netflix_body_debug.html').write_text(html)
            except Exception:
                pass
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
