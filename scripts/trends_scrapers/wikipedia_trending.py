"""
Wikipedia trending topics scraper.

Uses the free Wikimedia REST API to pull the top-viewed English
Wikipedia articles per day. Compares today's pageviews against
yesterday's to compute a per-article delta, then ranks by delta so we
surface actual "what is everyone suddenly researching" rather than
just "what always has a lot of views" (Main_Page, deaths in 2026,
etc).

Endpoint (no auth, free, ~500ms):

    https://wikimedia.org/api/rest_v1/metrics/pageviews/top/
        en.wikipedia/all-access/{YYYY}/{MM}/{DD}

Filters out meta / non-topic pages (Main_Page, Special:*, Wikipedia:*,
File:*, Category:*, Portal:*, Help:*, Deaths_in_*, Lists of *) so
what's left is real cultural topics.

Snapshot shape (kind='search' - reuses the shape headlines uses):

    {
      "source":    "wikipedia_trending",
      "label":     "Wikipedia",
      "kind":      "search",
      "national":  [
        { rank, title, url, views_today, views_prior,
          delta_pct, delta_abs, prior_rank }, ...
      ],
      ...
    }

Standalone:

    python3 -m scripts.trends_scrapers.wikipedia_trending
"""

from __future__ import annotations

import logging
import re
import sys
import urllib.parse
from datetime import date, timedelta
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


_UA = 'BG-Trends/1.0 (jenna@crosswalknyc.com)'
_API = ('https://wikimedia.org/api/rest_v1/metrics/pageviews/top/'
        'en.wikipedia/all-access/{year}/{month:02d}/{day:02d}')

# Article-title prefixes / patterns we always drop before ranking.
# Wikipedia's top-1000 is >30% meta / infrastructure pages that add
# no cultural signal.
_DROP_PREFIXES = (
    'Main_Page',
    'Special:',
    'Wikipedia:',
    'Wikipedia_talk:',
    'Talk:',
    'File:',
    'Category:',
    'Portal:',
    'Help:',
    'Template:',
    'Template_talk:',
    'User:',
    'Draft:',
    'MediaWiki:',
    'Module:',
    'Book:',
)

_DROP_EXACT = {
    'Main_Page', 'Special:Search',
}

# Regex matches "Deaths_in_2026", "Deaths_in_July_2026", etc. Also
# kills the always-viral "Lists_of_..." topical lists which are
# infrastructure pages, not cultural moments.
_DROP_REGEXES = [
    re.compile(r'^Deaths_in_'),
    re.compile(r'^List_of_'),
    re.compile(r'^Lists_of_'),
    re.compile(r'^2026_in_'),
    re.compile(r'^2025_in_'),
    re.compile(r'^Index_of_'),
    re.compile(r'^Timeline_of_'),
    re.compile(r'^Outline_of_'),
]

# Article must clear this daily view floor to be considered trending.
# Below this, deltas get noisy (a page going from 500 -> 5,000 views is
# probably a single Reddit thread linking it, not a cultural moment).
_MIN_VIEWS_FLOOR = 8_000

# Cap how many articles we surface. Frontend renders up to ~30 anyway.
_TOP_N = 30


def _is_topic(title: str) -> bool:
    """Return True iff `title` looks like a real article about a
    person / place / thing / event, not a Wikipedia meta page."""
    if title in _DROP_EXACT:
        return False
    for pref in _DROP_PREFIXES:
        if title.startswith(pref):
            return False
    for rx in _DROP_REGEXES:
        if rx.match(title):
            return False
    return True


def _fetch_day(d: date) -> dict[str, dict]:
    """Return `{article_title: {rank, views}}` for the given day.

    Returns {} on any failure so caller can still produce a snapshot
    with today's data even if yesterday's is unavailable (day-1
    delta just won't be computable in that case).
    """
    url = _API.format(year=d.year, month=d.month, day=d.day)
    try:
        r = requests.get(url, headers={'User-Agent': _UA}, timeout=15)
    except Exception as e:
        logger.warning("wikipedia_trending %s: %s", d.isoformat(), e)
        return {}
    if not r.ok:
        logger.warning("wikipedia_trending %s: http %s", d.isoformat(), r.status_code)
        return {}
    try:
        data = r.json()
    except Exception as e:
        logger.warning("wikipedia_trending %s: json parse failed: %s", d.isoformat(), e)
        return {}
    items = (data.get('items') or [{}])[0].get('articles') or []
    out: dict[str, dict] = {}
    for a in items:
        title = a.get('article') or ''
        if not title:
            continue
        out[title] = {
            'rank':  int(a.get('rank') or 0),
            'views': int(a.get('views') or 0),
        }
    return out


def _display_title(article_slug: str) -> str:
    """Turn `"The_Odyssey_(2026_film)"` into `"The Odyssey (2026 film)"`."""
    return urllib.parse.unquote(article_slug).replace('_', ' ')


def _wiki_url(article_slug: str) -> str:
    return f'https://en.wikipedia.org/wiki/{article_slug}'


def fetch() -> dict[str, Any]:
    """Pull yesterday + day-before-yesterday, compute delta, rank.

    Wikimedia typically closes a day by ~04:00 UTC of the following
    day. Because scrapers run on Hetzner (UTC) at times that can land
    inside that window, we walk backwards up to 3 days looking for
    the first day that returns data, then compare against the day
    before THAT so we still get a proper WoW delta.
    """
    # Try today-1, today-2, today-3 in order. First one with data wins.
    today_anchor: Optional[date] = None
    today_map:    dict[str, dict] = {}
    for offset in range(1, 4):
        candidate = date.today() - timedelta(days=offset)
        m = _fetch_day(candidate)
        if m:
            today_anchor = candidate
            today_map    = m
            break

    if today_anchor is None or not today_map:
        return {
            'national':     [],
            'available':    False,
            'error':        'no data for last 3 days (Wikimedia aggregation lag)',
        }

    prior     = today_anchor - timedelta(days=1)
    prior_map = _fetch_day(prior)

    rows: list[dict] = []
    for title, meta in today_map.items():
        if not _is_topic(title):
            continue
        views_today = meta['views']
        if views_today < _MIN_VIEWS_FLOOR:
            continue
        prior_meta  = prior_map.get(title) or {}
        views_prior = int(prior_meta.get('views') or 0)
        rank_today  = meta['rank']
        rank_prior  = int(prior_meta.get('rank') or 0) or None

        # Delta pct: if we had prior views, use standard pct change.
        # If NOT in prior top-1000, treat as "new" - assign a large
        # sentinel value so brand-new pages surface at the top.
        if views_prior:
            delta_abs = views_today - views_prior
            delta_pct = delta_abs / views_prior
        else:
            # "New" in top-1000. Assume prior views were just below the
            # top-1000 floor (~5000 views) as a conservative baseline.
            delta_abs = views_today - 5_000
            delta_pct = delta_abs / 5_000

        rows.append({
            'title':       _display_title(title),
            'article':     title,
            'url':         _wiki_url(title),
            'views_today': views_today,
            'views_prior': views_prior,
            'delta_abs':   delta_abs,
            'delta_pct':   round(delta_pct, 4),
            'rank_today':  rank_today,
            'rank_prior':  rank_prior,
            'is_new':      views_prior == 0,
        })

    # Rank by delta_pct descending. Ties broken by delta_abs so an
    # article that gained 300K views beats one that just doubled from
    # a lower base.
    rows.sort(key=lambda r: (r['delta_pct'], r['delta_abs']), reverse=True)

    # Re-stamp our final rank (1..N).
    for i, r in enumerate(rows[:_TOP_N], start=1):
        r['rank'] = i

    return {
        'national':      rows[:_TOP_N],
        'available':     True,
        'anchor_day':    today_anchor.isoformat(),
        'compare_day':   prior.isoformat(),
    }


if __name__ == '__main__':
    from ._base import run_scraper
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('wikipedia_trending', 'Wikipedia', 'search', fetch)
    print(
        f"wikipedia_trending: national={len(result.get('national', []))} "
        f"error={result.get('error')}",
        file=sys.stderr,
    )
    for r in (result.get('national') or [])[:10]:
        marker = 'NEW' if r.get('is_new') else f"{int(r['delta_pct']*100):+d}%"
        print(f"  #{r['rank']:>2} [{marker:>5}] {r['title']:<40} "
               f"{r['views_today']:>9,} views (was {r['views_prior']:>9,})",
               file=sys.stderr)
