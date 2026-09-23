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
import json
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


# Netflix's browse page ships its rails as an Apollo cache rather
# than as markup with readable tiles. A rail is a
# `PinotCarouselSection` whose `displayString` names it, and whose
# `entities.edges` reference ranked entries in chart order:
#
#   "displayString":"Top 10 TV Shows in the U.S. Today"
#   "entities":{"totalCount":10,...,"edges":[
#       {"node":{"__ref":"PinotRankedBoxshotEntityTreatment:
#                          rankedBoxshot_Video:82068293_<section>"},
#        "cursor":"MA=="}, ...
#
# and each of those refs resolves, elsewhere in the same cache, to an
# entry carrying the title:
#
#   "...rankedBoxshot_Video:82068293_<section>":{...,
#       "displayString":"Monster: The Lizzie Borden Story",...}
#
# The cursors are base64 ordinals ("MA==" is "0"), but the edges
# already arrive in order, so position in the list is the rank.
_PINOT_SECTION_RE = re.compile(
    r'"displayString"\s*:\s*"(Top 10 [^"]{0,40}in the U\.S\. Today)"'
    r'(.{0,40000}?)"edges"\s*:\s*\[(.*?)\]', re.S)
_PINOT_REF_RE = re.compile(
    r'"__ref"\s*:\s*"(PinotRankedBoxshotEntityTreatment:'
    r'rankedBoxshot_Video:(\d+)_[0-9a-f-]+)"')


def _pinot_titles(html: str) -> dict[str, str]:
    """Every ranked-entry ref in the cache, mapped to its title."""
    out: dict[str, str] = {}
    for m in re.finditer(
            r'"(PinotRankedBoxshotEntityTreatment:rankedBoxshot_Video:'
            r'\d+_[0-9a-f-]+)"\s*:\s*\{(.{0,1200}?)"displayString"'
            r'\s*:\s*"([^"]{1,200})"', html, re.S):
        out.setdefault(m.group(1), unescape(m.group(3)).strip())
    return out


def _extract_pinot_daily(html: str) -> tuple[list[dict], list[dict]]:
    """(tv, films) off the live 'Top 10 ... in the U.S. Today' rails.

    These are what Netflix is showing US members RIGHT NOW, which is
    a different thing from the weekly file: the file covers the week
    that ended the previous Sunday and is published on the Tuesday
    after, so it trails the day by three to nine days. The daily rail
    is the honest order for a day view.
    """
    titles = _pinot_titles(html)
    tv: list[dict] = []
    films: list[dict] = []
    for m in _PINOT_SECTION_RE.finditer(html):
        label = m.group(1)
        edges = m.group(3)
        bucket = films if 'Movie' in label else tv
        if bucket:
            continue
        seen: set = set()
        for rank, r in enumerate(_PINOT_REF_RE.finditer(edges), start=1):
            ref, vid = r.group(1), r.group(2)
            title = titles.get(ref)
            if not title or title.lower() in seen:
                continue
            seen.add(title.lower())
            bucket.append({
                'rank':   rank,
                'title':  title,
                'url':    f'https://www.netflix.com/title/{vid}',
                'week':   '',
                'source': 'daily_rail',
                'netflix_video_id': vid,
            })
            if len(bucket) >= 10:
                break
        logger.info("netflix: %s parsed %d rows", label, len(bucket))
    return tv, films


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
    """Render the signed-in browse page and return its HTML.

    This used to launch its own browser and inject donated cookies
    directly. It stopped reaching a session: the cookie loader drops
    a jar past its freshness window and returns nothing, so the
    bespoke runner rendered anonymously and the daily rails were
    never there, which is why the rail quietly fell back to the
    weekly file for weeks while reporting itself healthy.

    Session handling now lives in the shared renderer, which carries
    a full signed-in session rather than cookies alone, so this goes
    through it instead of keeping a second implementation in step.
    Everything the old runner did by hand (warm the homepage, wait
    for tiles, scroll the lazy rows in) the shared helper already
    does, and it does it the same way for every other service.

    Returns None on any failure; the caller falls back to the weekly
    file.
    """
    try:
        from ._playwright import render_pages
    except Exception as e:
        logger.info("netflix: renderer import failed: %s", e)
        return None
    try:
        rendered = render_pages(
            [('Browse', 'https://www.netflix.com/browse')],
            homepage='https://www.netflix.com/',
            cookie_domain='netflix.com',
            # A ranked tile is the highest-priority lazy content on
            # the page, so waiting on a title link is enough.
            wait_selectors=['a[href*="/title/"]', 'a[href*="/watch/"]',
                            '.title-card'],
            wait_ms=6000, scroll_ms=12000, hydration_wait_ms=12000)
    except Exception as e:
        logger.warning("netflix: browse render failed: %s", e)
        return None
    for _label, html in rendered or []:
        if html and len(html) > 50_000:
            return html
    logger.info("netflix: browse render came back too small to parse")
    return None


def _load_previous_daily() -> dict:
    """Read the current latest/netflix.json from S3. Empty dict on any
    failure, and on anything that did not come through the authenticated
    daily path, so a carried-forward rail can never mix row shapes with
    the weekly fallback. Used to keep a previously-good films or TV rail
    when today's render only produced one of the two."""
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(
            Bucket='dashboard-inputs',
            Key='trends_iq_snapshots/latest/netflix.json')
        d = json.loads(o['Body'].read().decode('utf-8'))
        if not isinstance(d, dict):
            return {}
        return d if d.get('source_path') == ('authenticated_daily', 'daily_rail+weekly_figures') else {}
    except Exception as e:
        logger.info("netflix: could not read previous snapshot: %s", e)
        return {}


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

    tv_items, film_items = _extract_pinot_daily(html)
    if not tv_items and not film_items:
        # Netflix has cycled its markup before; the older tile parse
        # stays as a second chance rather than being deleted.
        tv_items, film_items = _extract_top10_rows(html)

    # Both rails live on the same page, so exactly one coming back
    # empty is a lazy-render miss rather than Netflix publishing an
    # empty chart. Render once more before accepting it: the first
    # run after this path shipped caught the films rail short and
    # published a TV-only day, which is the half-width archive day
    # the carry-forward guard below exists to prevent and could not,
    # because the previous snapshot came from the weekly file.
    if bool(tv_items) != bool(film_items):
        logger.info("netflix: only one rail rendered (%d TV, %d films); "
                     "rendering once more", len(tv_items), len(film_items))
        retry = _run_netflix_playwright()
        if retry:
            r_tv, r_film = _extract_pinot_daily(retry)
            if r_tv and r_film:
                tv_items, film_items = r_tv, r_film
            else:
                tv_items = tv_items or r_tv
                film_items = film_items or r_film
        if bool(tv_items) != bool(film_items):
            logger.warning(
                "netflix: still only one rail after a second render "
                "(%d TV, %d films)", len(tv_items), len(film_items))

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

    # Never ship an empty rail over a previously-good one. Both rails
    # render off the same page, so one of them coming back empty is a
    # transient render or parse miss rather than Netflix publishing an
    # empty chart. Keep yesterday's rows for that rail, marked stale, so
    # the archive never lands a half-width day. Two days were lost this
    # way before the guard existed.
    if bool(film_items) != bool(tv_items):
        prev = _load_previous_daily()
        if not (prev.get('us_films') or prev.get('us_tv')):
            # No previous daily capture to carry. The published
            # weekly file still describes the missing rail better
            # than shipping without it, and its rows say which
            # source they came from.
            try:
                prev = _fetch_weekly_tsv() or {}
            except Exception:
                prev = {}
        for label, key, items in (('films', 'us_films', film_items),
                                   ('TV', 'us_tv', tv_items)):
            if items:
                continue
            carried = [dict(r, stale_from_previous=True)
                        for r in (prev.get(key) or []) if isinstance(r, dict)]
            if not carried:
                logger.warning(
                    "netflix: %s rail empty and no previous rail to carry "
                    "forward; shipping without it", label)
                continue
            items.extend(carried)
            logger.warning(
                "netflix: %s rail parsed 0 rows; carrying %d rows forward "
                "from the previous capture", label, len(carried))

    # Their chart owns the ORDER, their published data owns the SIZE.
    #
    # The daily rail is current and is what a day view implies, but it
    # carries no figures. The weekly file is three to nine days behind
    # depending when you look, but it holds the only real audience
    # numbers Netflix publishes anywhere. So the order comes from the
    # rail above and the figures are attached here from the file,
    # matched on title.
    #
    # The two do not line up perfectly and should not be forced to. A
    # title on today's rail but absent from last week's file is new or
    # climbing, and is reasoned into the set downstream the same way
    # any unpublished title is. A title in the file but off today's
    # rail simply is not on the day's chart and does not appear.
    #
    # The figures stay labelled as what they are: a WEEKLY worldwide
    # count for the chart week, carried alongside a DAILY US position.
    # Converting one into the other is the estimator's job and the
    # prompt states both conversions, because treating a weekly
    # worldwide view count as a daily US number is exactly how a
    # plausible figure ends up an order of magnitude too big.
    try:
        weekly = _fetch_weekly_tsv()
    except Exception:
        weekly = {}
    # The two sources spell a series differently. The weekly file
    # qualifies it ("Monster: The Lizzie Borden Story: Season 1")
    # because it ranks seasons; the daily rail names the show. Joining
    # on the exact string matched 7 of 20 rows and left most of the TV
    # chart without the figures it should have had.
    def _join_key(t: str) -> str:
        t = (t or '').strip().lower()
        t = re.sub(r'\s*:\s*(?:season|series|part|volume|book|'
                   r'limited series|chapter)\b.*$', '', t)
        t = re.sub(r'[^a-z0-9]+', ' ', t)
        return ' '.join(t.split())

    figs: dict[str, dict] = {}
    for _k in ('us_films', 'us_tv', 'global_films_en', 'global_tv_en',
                'global_films_nonen', 'global_tv_nonen'):
        for _r in (weekly.get(_k) or []):
            if not _r.get('weekly_views'):
                continue
            _t = _join_key(_r.get('title'))
            if _t:
                figs.setdefault(_t, _r)
    _week = weekly.get('week_us') or weekly.get('week_global') or ''
    _matched = 0
    for _r in film_items + tv_items:
        _g = figs.get(_join_key(_r.get('title')))
        if not _g:
            continue
        for _f in ('weekly_views', 'weekly_hours_viewed', 'runtime_hours',
                    'weeks_in_top10'):
            if _g.get(_f):
                _r[_f] = _g[_f]
        _r['weekly_figures_week'] = _week
        _r['weekly_figures_scope'] = 'worldwide'
        _matched += 1
    logger.info("netflix: daily rail of %d rows, %d carrying published "
                 "weekly figures from the week ending %s",
                 len(film_items) + len(tv_items), _matched,
                 _week or 'unknown')

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
        'source_path':  'daily_rail+weekly_figures',
        # What the attached figures cover, kept distinct from the day
        # the rail was read so nothing downstream has to guess.
        'figures_week': _week,
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
        # Netflix publishes the numbers its own ranking is built
        # from, on the global file: hours viewed and views for the
        # week, per title. They were being parsed and dropped. They
        # are the only first-party audience figure anywhere in this
        # fleet, and carrying them means a Netflix row can be sized
        # from what Netflix reported rather than reasoned from its
        # rank, which is also what makes the readings descend across
        # the chart by construction instead of by correction.
        #
        # The country file carries rank only, so a US row picks these
        # up from its global twin where it has one. Absent (a US-only
        # title, or a week the global file has not landed) the row
        # simply ships without them.
        row = {
            'rank':            rank,
            'title':           display_title,
            'category':        r.get('category') or '',
            'weeks_in_top10':  weeks_in_top10,
            'url':             _title_url(title),
            'week':            week_iso,
            'source':          'weekly_tsv',
        }
        for src, dst in (('weekly_views', 'weekly_views'),
                          ('weekly_hours_viewed', 'weekly_hours_viewed')):
            try:
                v = int(float(r.get(src) or 0))
            except (TypeError, ValueError):
                v = 0
            if v > 0:
                row[dst] = v
        try:
            rt = float(r.get('runtime') or 0)
        except (TypeError, ValueError):
            rt = 0.0
        if rt > 0:
            row['runtime_hours'] = rt
        out.append(row)
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

    # Carry the published weekly figures across to the US rows. The
    # country file does not repeat them, so a US title that also
    # charted globally takes them from its global twin. Matched on the
    # title as printed in both files.
    _global_figs: dict[str, dict] = {}
    for _lst in (global_films_en, global_tv_en,
                  global_films_nonen, global_tv_nonen):
        for _r in _lst:
            _k = (_r.get('title') or '').strip().lower()
            if _k and _r.get('weekly_views'):
                _global_figs.setdefault(_k, _r)
    _matched = 0
    for _lst in (us_films, us_tv):
        for _r in _lst:
            _g = _global_figs.get((_r.get('title') or '').strip().lower())
            if not _g:
                continue
            for _f in ('weekly_views', 'weekly_hours_viewed',
                        'runtime_hours'):
                if _g.get(_f) and not _r.get(_f):
                    _r[_f] = _g[_f]
            _r['weekly_figures_scope'] = 'global'
            _matched += 1
    logger.info("netflix: %d of %d US top-10 rows carry Netflix's "
                 "published weekly figures", _matched,
                 len(us_films) + len(us_tv))

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

    # Falling back to the weekly file is a DEGRADED run, not a
    # healthy one, and has to say so. This path ran every night for
    # weeks while the rail reported itself fine: the browse render
    # could not reach a signed-in session, the daily rails were
    # never in the page, and the weekly file produced perfectly
    # plausible rows covering a week that ended days earlier. Fresh
    # fetch, stale content, no signal anywhere.
    out = _fetch_weekly_tsv()
    try:
        from . import source_health
        source_health.record(
            'netflix',
            primary=source_health.PRIMARY_SIGNED_IN,
            used='the published weekly file',
            reason='the daily Top 10 rails need a signed-in browse '
                   'page and the render did not produce one',
            covers=(out or {}).get('week_us') or 'the previous chart week')
        source_health.stamp(out, 'netflix')
    except Exception:
        pass
    return out


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
