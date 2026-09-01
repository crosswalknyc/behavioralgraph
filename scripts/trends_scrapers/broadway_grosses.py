"""
Broadway weekly-attendance scraper - Playbill grosses page.

Mirrors the Broadway League's Tuesday-published weekly report, which
Playbill re-hosts at https://www.playbill.com/grosses. Every
currently-running Broadway production shows up in a single well-
structured HTML table with attendance (seats sold), % capacity,
performances, and week-over-week % capacity change.

Attendance-only per Jenna 2026-09-01 ("we dont need gross, just need
attendance"). Every dollar column on the source page (This Week Gross,
Diff $, Avg Ticket, Top Ticket) is READ from the HTML but DROPPED
before the snapshot is written. The panel is about who's actually
sitting in a theatre; the grosses page is just the most reliable
source for that count.

Snapshot shape (kind='broadway'):

    {
      "source":      "broadway_grosses",
      "kind":        "broadway",
      "label":       "Broadway",
      "fetched_at":  "2026-09-01T17:00:00+00:00",
      "week_ending": "2026-08-23",
      "sources": {
        "broadway_weekly_attendance": {
          "label":     "Show Rank",
          "items":     [ ~25-40 shows, sorted by attendance desc ],
          "available": true
        }
      }
    }

Each items[i]:

    {
      "rank":                    1,
      "title":                   "Wicked",
      "subtitle":                "Gershwin Theatre",
      "theatre":                 "Gershwin Theatre",
      "attendance":              12958,
      "pct_capacity":            0.8964,
      "pct_capacity_change":     -0.0074,
      "weekly_change_attendance": -0.0082,        # implied from
                                                   # cap change + this
                                                   # week's cap
      "performances":            8,
      "previews":                0,
      "is_preview":              false,
      "url":                     "https://playbill.com/production/gross?production=<uuid>",
      "image_url":               ""
    }

Access notes (verified 2026-09-01):

    Playbill's Cloudflare front redirects `http://` to `https://` (301)
    but the https URL serves ~650KB of SSR HTML with no cookie
    challenge on either a residential Mac or a Hetzner Linux box.
    curl_cffi Chrome-131 impersonation is used defensively so the
    scraper survives if Cloudflare tightens the fingerprint check.

Standalone:

    python3 -m scripts.trends_scrapers.broadway_grosses
    python3 -m scripts.trends_scrapers.broadway_grosses --dry-run
"""

from __future__ import annotations

import argparse
import html as _html
import logging
import re
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)


_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/126.0.0.0 Safari/537.36')

_TIMEOUT = 25

_PLAYBILL_URL = 'https://www.playbill.com/grosses'


# ---------------------------------------------------------------------------
# HTTP fetch
# ---------------------------------------------------------------------------
def _fetch_html(url: str, timeout: int = _TIMEOUT) -> str:
    """Return the page HTML or '' on any failure. curl_cffi Chrome-131
    first (real Chrome TLS fingerprint bypasses most CDN bot walls),
    plain requests as a fallback if curl_cffi isn't installed."""
    # Preferred: curl_cffi. Same pattern as film_ticketing / comics_charts.
    try:
        from curl_cffi import requests as _ccr  # type: ignore
        r = _ccr.get(url, impersonate='chrome131',
                      headers={'Accept': 'text/html,application/xhtml+xml'},
                      timeout=timeout)
        if r.status_code == 200 and (r.text or ''):
            return r.text
        logger.info("playbill curl_cffi status=%s bytes=%d",
                     r.status_code, len(r.text or ''))
    except Exception as e:
        logger.info("playbill curl_cffi failed: %s", e)

    # Fallback: plain requests.
    try:
        import requests
        r = requests.get(url,
                          headers={'User-Agent': _UA,
                                    'Accept': 'text/html'},
                          timeout=timeout,
                          allow_redirects=True)
        if r.ok:
            return r.text or ''
        logger.warning("playbill plain requests http=%d", r.status_code)
    except Exception as e:
        logger.warning("playbill plain requests failed: %s", e)
    return ''


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------
# Row-level regexes are keyed off `data-label="..."` attributes rather
# than column-index selectors because Playbill occasionally reorders
# columns between refreshes and the labels stay stable. Every field
# below is optional at the individual-row level; a missing field just
# leaves that key unset (or defaulted to a safe value) instead of
# dropping the whole row.
_ROW_SPLIT_RE = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL)
_SHOW_NAME_RE = re.compile(
    r'data-label="Show"[^>]*>.*?<span class="data-value">([^<]+)</span>',
    re.DOTALL,
)
_SHOW_THEATRE_RE = re.compile(
    r'data-label="Show"[^>]*>.*?<span class="subtext">([^<]+)</span>',
    re.DOTALL,
)
_SHOW_URL_RE = re.compile(
    r'data-label="Show"[^>]*>.*?<a href="([^"]+)"',
    re.DOTALL,
)
# Attendance: `data-sort-value` is the raw integer; `data-value` HTML
# is the display string (e.g. "12,958"). Prefer the sort-value because
# it survives thousands-separators and typography changes.
_ATTENDANCE_RE = re.compile(
    r'data-label="Seats Sold"[^>]*data-sort-value="([0-9.-]+)"',
)
# % capacity: sort-value is a fraction 0.0-1.0 (e.g. "0.8964"), NOT
# the 86.20% display string. Parse as float.
_PCT_CAP_RE = re.compile(
    r'data-label="% Cap"[^>]*data-sort-value="(-?[0-9.]+)"',
)
# Week-over-week change in % capacity. Sort-value is a signed fraction
# (e.g. "-0.0325" = 3.25 percentage points lower than last week).
_DIFF_CAP_RE = re.compile(
    r'data-label="Diff % cap"[^>]*data-sort-value="(-?[0-9.]+)"',
)
# Performances this week (data-value) + previews (subtext).
_PERFS_VALUE_RE = re.compile(
    r'data-label="Perfs"[^>]*data-sort-value="([0-9]+)"',
)
_PERFS_PREVIEWS_RE = re.compile(
    r'data-label="Perfs"[^>]*>.*?<span class="subtext">([0-9]+)</span>',
    re.DOTALL,
)


def _find_grosses_table(html: str) -> str:
    """Locate the vault-grosses-result wrapper and return the <table>
    inside it. Returns '' when the wrapper isn't present (site redesign
    or empty page)."""
    if not html:
        return ''
    anchor = html.find('vault-grosses-result')
    if anchor == -1:
        return ''
    tab_open = html.find('<table', anchor)
    if tab_open == -1:
        return ''
    tab_close = html.find('</table>', tab_open)
    if tab_close == -1:
        return ''
    return html[tab_open:tab_close + len('</table>')]


def _find_selected_week(html: str) -> str:
    """Return the currently-selected week-ending date as YYYY-MM-DD, or
    '' when the week-select dropdown can't be parsed. Playbill's week
    picker is a `<select>` whose FIRST option is always the latest
    published week (the report the page is currently rendering); we
    prefer the option with `selected` marked but fall back to the
    first entry."""
    if not html:
        return ''
    sel_m = re.search(r'<select[^>]*>(.*?)</select>', html, re.DOTALL)
    if not sel_m:
        return ''
    sel_body = sel_m.group(1)
    # Prefer explicitly-selected option.
    m = re.search(r'<option[^>]*selected[^>]*>([^<]+)</option>', sel_body,
                   re.IGNORECASE)
    if not m:
        # First option is the latest week.
        m = re.search(r'<option[^>]*>([^<]+)</option>', sel_body)
    if not m:
        return ''
    label = (m.group(1) or '').strip()
    # Match ISO-style YYYY-MM-DD, which is Playbill's canonical form.
    iso_m = re.search(r'(\d{4}-\d{2}-\d{2})', label)
    return iso_m.group(1) if iso_m else label


def _clean_text(s: str) -> str:
    """HTML-entity decode + whitespace collapse. Also normalizes a
    couple of typographic marks Playbill mixes in show titles."""
    if not s:
        return ''
    t = _html.unescape(s).strip()
    t = re.sub(r'\s+', ' ', t)
    return t


def _parse_float(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _parse_int(s: Optional[str]) -> Optional[int]:
    v = _parse_float(s)
    if v is None:
        return None
    try:
        return int(round(v))
    except Exception:
        return None


def _weekly_change_attendance(pct_cap_now: Optional[float],
                                pct_cap_diff: Optional[float]) -> Optional[float]:
    """Derive the week-over-week percent change in attendance from the
    two capacity numbers Playbill exposes.

    Playbill doesn't report last-week attendance directly, but it
    does report:

      - this week's % capacity (seats sold / seats potential)
      - the WoW change in that % capacity, in absolute percentage
        points (e.g. -0.0325 means 3.25pp lower than last week)

    For a show whose theatre + performance count is stable WoW (true
    for every continuing production; the exceptions are brief
    schedule blips), attendance = % capacity * capacity_per_week, and
    capacity_per_week cancels out of the ratio. So:

        prev_pct_cap  = this_pct_cap - pct_cap_diff
        weekly_change = (this_pct_cap - prev_pct_cap) / prev_pct_cap
                       = pct_cap_diff / prev_pct_cap

    Returns None when either input is missing or when prev_pct_cap
    lands at or below 0 (theatre was dark last week; no comparable
    baseline).
    """
    if pct_cap_now is None or pct_cap_diff is None:
        return None
    prev = pct_cap_now - pct_cap_diff
    if prev <= 0:
        return None
    try:
        return pct_cap_diff / prev
    except Exception:
        return None


def _parse_rows(table_html: str) -> list[dict]:
    """Walk the <tr> rows in the grosses table and emit one dict per
    show. Sorted by attendance descending. Skips any row that fails
    to yield an integer attendance (defensive; drops summary rows or
    partial data rows)."""
    if not table_html:
        return []
    rows: list[dict] = []
    for tr_body in _ROW_SPLIT_RE.findall(table_html):
        if 'data-label=' not in tr_body:
            # thead / rows without our expected data attributes.
            continue
        nm_m = _SHOW_NAME_RE.search(tr_body)
        title = _clean_text(nm_m.group(1)) if nm_m else ''
        if not title:
            continue

        thr_m = _SHOW_THEATRE_RE.search(tr_body)
        theatre = _clean_text(thr_m.group(1)) if thr_m else ''

        url_m = _SHOW_URL_RE.search(tr_body)
        url = (url_m.group(1) or '').strip() if url_m else ''

        att_m = _ATTENDANCE_RE.search(tr_body)
        attendance = _parse_int(att_m.group(1)) if att_m else None
        if not attendance or attendance <= 0:
            # No usable attendance for this row; drop it rather than
            # ship a zero-audience card.
            continue

        cap_m = _PCT_CAP_RE.search(tr_body)
        pct_capacity = _parse_float(cap_m.group(1)) if cap_m else None

        diff_m = _DIFF_CAP_RE.search(tr_body)
        pct_capacity_change = _parse_float(diff_m.group(1)) if diff_m else None

        weekly_change_attendance = _weekly_change_attendance(
            pct_capacity, pct_capacity_change)

        perfs_m = _PERFS_VALUE_RE.search(tr_body)
        performances = _parse_int(perfs_m.group(1)) if perfs_m else None

        prev_m = _PERFS_PREVIEWS_RE.search(tr_body)
        previews = _parse_int(prev_m.group(1)) if prev_m else 0
        if previews is None:
            previews = 0

        # `is_preview`: true when the week's schedule is preview-only
        # (previews > 0 AND regular perfs == 0). Preview + regular
        # weeks (crossover) don't get the flag because the show has
        # officially opened.
        is_preview = bool(previews and (performances or 0) == 0)

        rows.append({
            'title':                     title,
            'subtitle':                  theatre,   # rendered as row subtitle
            'theatre':                   theatre,
            'attendance':                int(attendance),
            'pct_capacity':              pct_capacity,
            'pct_capacity_change':       pct_capacity_change,
            'weekly_change_attendance':  weekly_change_attendance,
            'performances':              performances,
            'previews':                  previews,
            'is_preview':                is_preview,
            'url':                       url,
            'image_url':                 '',        # posters not on this page
        })

    rows.sort(key=lambda r: -(r.get('attendance') or 0))
    for i, r in enumerate(rows, start=1):
        r['rank'] = i
    return rows


# ---------------------------------------------------------------------------
# Poster enrichment (official Playbill cover art)
# ---------------------------------------------------------------------------
# The grosses table only links to each show's grosses DETAIL page
# (`/production/gross?production=<uuid>`), which carries no poster. That
# detail page, however, links out to the show's MAIN production page
# (`/production/<slug>`), whose <meta property="og:image"> is the
# official Playbill cover art. So poster fetch is a 2-hop walk:
#   grosses detail  ->  main production page  ->  og:image
# Everything here is best-effort: any miss just leaves image_url='' so
# the frontend falls back to the branded placeholder tile.
_MAIN_PROD_RE = re.compile(
    r'href="(https://(?:www\.)?playbill\.com/production/[^"?]+)"',
    re.IGNORECASE,
)
# Playbill emits `<meta content="..." property="og:image">` (content
# BEFORE property), so match the whole og:image tag first, then pull
# its content attribute regardless of attribute order.
_OG_IMAGE_TAG_RE = re.compile(
    r'<meta[^>]+property="og:image"[^>]*>',
    re.IGNORECASE,
)
_META_CONTENT_RE = re.compile(r'content="([^"]+)"', re.IGNORECASE)
_POSTER_TIMEOUT = 12


def _extract_main_production_url(gross_html: str) -> str:
    """First non-gross `/production/<slug>` link on the grosses detail
    page = the show's main production page."""
    if not gross_html:
        return ''
    for href in _MAIN_PROD_RE.findall(gross_html):
        if 'gross' in href.lower():
            continue
        return href
    return ''


def _extract_poster(main_html: str) -> str:
    """og:image on the main production page = official Playbill cover."""
    if not main_html:
        return ''
    tag = _OG_IMAGE_TAG_RE.search(main_html)
    if not tag:
        return ''
    c = _META_CONTENT_RE.search(tag.group(0))
    return (c.group(1) or '').strip() if c else ''


def _enrich_posters(rows: list[dict]) -> list[dict]:
    """Fill `row['image_url']` with the show's official Playbill cover
    via the 2-hop walk above. Fully defensive: never raises; a failed
    lookup simply leaves the row's image_url as-is (empty)."""
    for row in rows:
        gross_url = (row.get('url') or '').strip()
        if not gross_url or row.get('image_url'):
            continue
        try:
            gross_html = _fetch_html(gross_url, timeout=_POSTER_TIMEOUT)
            main_url = _extract_main_production_url(gross_html)
            if not main_url:
                continue
            poster = _extract_poster(_fetch_html(main_url,
                                                  timeout=_POSTER_TIMEOUT))
            if poster:
                row['image_url'] = poster
        except Exception as e:
            logger.info("broadway poster enrich failed for %r: %s",
                         row.get('title'), e)
    return rows


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def fetch() -> dict[str, Any]:
    """Pull the Playbill grosses page, parse out attendance rows, wrap
    in the standard snapshot payload shape. Never raises; empty rows +
    error string set on any failure so the read-side always has
    something to serve."""
    html = _fetch_html(_PLAYBILL_URL)
    week_ending = _find_selected_week(html)
    table_html = _find_grosses_table(html)
    items = _parse_rows(table_html)

    # Best-effort: attach official Playbill cover art per show. Guarded
    # so a poster-fetch hiccup can never sink the attendance snapshot.
    try:
        _enrich_posters(items)
    except Exception as e:
        logger.info("broadway poster enrichment skipped: %s", e)

    available = bool(items)
    panel = {
        'label':      'Show Rank',
        'sub':        ('Every currently-running Broadway show, ranked '
                        'by weekly ticket buyers.') if available else '',
        'items':      items,
        'available':  available,
    }

    return {
        # `national` mirrors the single panel's items list so any
        # historic-view code path expecting the standard snapshot shape
        # keeps working.
        'national':    items,
        'week_ending': week_ending,
        'available':   available,
        'sources': {
            'broadway_weekly_attendance': panel,
        },
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    p = argparse.ArgumentParser(description='Broadway weekly-attendance scraper')
    p.add_argument('--dry-run', action='store_true',
                    help='print results without writing an S3 snapshot')
    args = p.parse_args()

    if args.dry_run:
        result = fetch()
    else:
        from ._base import run_scraper
        result = run_scraper('broadway_grosses', 'Broadway', 'broadway', fetch)

    items = ((result.get('sources') or {})
              .get('broadway_weekly_attendance') or {}).get('items') or []
    print(f"broadway_grosses: n={len(items)}  week_ending={result.get('week_ending')!r}",
           file=sys.stderr)
    for it in items[:10]:
        cap = it.get('pct_capacity')
        wca = it.get('weekly_change_attendance')
        cap_s = f'{cap*100:.1f}%' if cap is not None else '   ?'
        wca_s = f'{wca*100:+.2f}%' if wca is not None else '   ?'
        print(f"   #{it['rank']:>2}  {it['title']:38s}  {it['theatre']:34s}  "
               f"seats={it['attendance']:>6}  cap={cap_s:>6}  wow={wca_s:>7}",
               file=sys.stderr)
