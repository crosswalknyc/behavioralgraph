"""
Netflix Top 10 scraper (public data, no auth).

Netflix publishes weekly rankings as clean TSV files at:

    https://www.netflix.com/tudum/top10/data/all-weeks-global.tsv     (~850KB)
    https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv  (~31MB)

The country file carries per-country rankings for every week since Feb
2021, columns: country_name, country_iso2, week, category, weekly_rank,
show_title, season_title, cumulative_weeks_in_top_10. Categories are
"Films" and "TV" for the country file; "Films (English)", "Films
(Non-English)", "TV (English)", "TV (Non-English)" for the global file.

Standalone:
    python3 -m scripts.trends_scrapers.netflix
"""

from __future__ import annotations

import io
import logging
import re
import sys
from typing import Any

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
        })
    return out


def fetch() -> dict[str, Any]:
    """Return national (US) rankings + global rankings + per-category
    breakouts. Shape aligns with the other trending scrapers so it drops
    into the `trends_iq_snapshots/latest/` bucket cleanly.
    """
    # ── Country-level (US) ──
    r_countries = http_get(_TSV_COUNTRIES, timeout=30, retries=1,
                            headers={'User-Agent':
                                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                                'AppleWebKit/537.36 (KHTML, like Gecko) '
                                'Chrome/127.0.0.0 Safari/537.36'})
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
                         headers={'User-Agent':
                             'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                             'AppleWebKit/537.36 (KHTML, like Gecko) '
                             'Chrome/127.0.0.0 Safari/537.36'})
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
    # Ranks are separate per category, so we tag each with a category
    # label and merge in interleaved order (rank 1 film, rank 1 tv, ...).
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
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('netflix', 'Netflix', 'streaming', fetch)
    print(f"netflix: national={len(result.get('national', []))} "
           f"us_films={len(result.get('us_films', []))} "
           f"us_tv={len(result.get('us_tv', []))} "
           f"week={result.get('week_us')} "
           f"error={result.get('error')}", file=sys.stderr)
