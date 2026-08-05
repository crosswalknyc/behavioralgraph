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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


_UA = 'BG-Trends/1.0 (jenna@crosswalknyc.com)'
_API = ('https://wikimedia.org/api/rest_v1/metrics/pageviews/top/'
        'en.wikipedia/all-access/{year}/{month:02d}/{day:02d}')

# Wikimedia's page-summary REST endpoint. Returns a short "description"
# (one-line, e.g. "American Deaf actress") and a 1-2 sentence "extract".
# Free, no auth. We hit this for every article we surface so each row
# comes with a "what is this / why is this trending" caption.
_SUMMARY_API = 'https://en.wikipedia.org/api/rest_v1/page/summary/{slug}'

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


# ────────────────────────────────────────────────────────────────────────────
# US-person classifier (Jenna 2026-08-05).
#
# The Wikipedia top-viewed list surfaces events, organizations, places,
# and foreign celebs alongside real US people. The Trending People card
# is meant to show trending US individuals only, so we filter every
# candidate row through this classifier before returning.
#
# We use the Wikimedia page-summary API `description` field (a
# one-liner like "American Deaf actress" / "Indian actor" / "American
# right-wing organization" / "county in Utah" / "primary election in
# Michigan"). Two-tier check:
#
#   1. Description mentions a personhood role token (actor, singer,
#      politician, athlete, ...). Rejects non-people (organizations,
#      events, places).
#   2. Description mentions "American" / "U.S." / "United States" AND
#      does NOT mention an explicit foreign-nationality word (Indian,
#      British, ...). Rejects US-relevant-audience-facing "trending"
#      that's actually foreign celebs whose fans hit English Wikipedia.
#
# Both checks are case-insensitive. When description is empty, we
# reject (safer default - a row we can't classify usually isn't a
# well-formed person page).
# ────────────────────────────────────────────────────────────────────────────
_PERSON_ROLE_TOKENS = frozenset({
    'actor', 'actress', 'singer', 'musician', 'songwriter', 'rapper',
    'composer', 'producer', 'dj', 'band-member',
    'athlete', 'player', 'quarterback', 'pitcher', 'batter', 'runner',
    'swimmer', 'boxer', 'wrestler', 'coach', 'manager', 'referee',
    'gymnast', 'skater', 'cyclist', 'skier', 'golfer', 'racer', 'driver',
    'politician', 'president', 'senator', 'representative', 'governor',
    'mayor', 'ambassador', 'congressman', 'congresswoman', 'lawmaker',
    'diplomat', 'nominee', 'candidate', 'commissioner',
    'journalist', 'anchor', 'correspondent', 'reporter', 'columnist',
    'host', 'presenter', 'broadcaster', 'commentator', 'pundit',
    'comedian', 'satirist', 'writer', 'novelist', 'author', 'poet',
    'playwright', 'screenwriter', 'director', 'filmmaker',
    'businessman', 'businesswoman', 'businessperson', 'entrepreneur',
    'executive', 'ceo', 'founder', 'investor', 'financier', 'banker',
    'philanthropist', 'socialite', 'heir', 'heiress',
    'model', 'personality', 'influencer', 'creator', 'blogger',
    'youtuber', 'streamer', 'podcaster', 'tiktoker', 'vlogger',
    'chef', 'restaurateur', 'sommelier',
    'scientist', 'researcher', 'physicist', 'biologist', 'chemist',
    'engineer', 'inventor', 'astronaut',
    'doctor', 'physician', 'surgeon', 'psychiatrist', 'psychologist',
    'judge', 'justice', 'lawyer', 'attorney', 'prosecutor',
    'activist', 'organizer', 'campaigner', 'protester', 'dissident',
    'artist', 'painter', 'sculptor', 'photographer', 'designer',
    'illustrator', 'cartoonist', 'animator',
    'wrestler', 'fighter', 'martial-artist', 'bodybuilder',
    'preacher', 'minister', 'rabbi', 'imam', 'pastor', 'evangelist',
    'general', 'admiral', 'colonel', 'captain', 'commander',
    'monarch', 'royal', 'prince', 'princess', 'duke', 'duchess',
    'criminal', 'defendant', 'convict', 'suspect', 'victim',
    'ballerina', 'dancer', 'choreographer', 'magician',
    # Generic person nouns - Wikipedia often uses these when the
    # subject's occupation is historical or the article is a
    # biography of a private person tried for a crime, etc. ("American
    # woman tried and acquitted...", "American man convicted of...")
    'woman', 'man', 'person', 'individual', 'girl', 'boy', 'child',
    'teenager', 'adult', 'youth', 'minor',
    'businessman', 'businesswoman',  # keep - also flagged above
})

# Foreign-nationality descriptors. If any of these appear in the
# description, the row is dropped even if a person role also matches
# (unless "American" ALSO appears, which flags dual-nationals - we
# keep those; e.g. "British-American actress").
_FOREIGN_NATIONALITY_TOKENS = frozenset({
    'indian', 'pakistani', 'bangladeshi', 'sri lankan', 'nepalese',
    'british', 'english', 'welsh', 'scottish', 'irish', 'northern irish',
    'chinese', 'japanese', 'korean', 'taiwanese', 'hong kong', 'thai',
    'vietnamese', 'filipino', 'indonesian', 'malaysian', 'singaporean',
    'russian', 'ukrainian', 'belarusian', 'georgian', 'kazakh',
    'french', 'german', 'italian', 'spanish', 'portuguese', 'dutch',
    'belgian', 'swiss', 'austrian', 'polish', 'czech', 'slovak',
    'hungarian', 'romanian', 'bulgarian', 'greek', 'turkish',
    'norwegian', 'swedish', 'danish', 'finnish', 'icelandic',
    'israeli', 'palestinian', 'lebanese', 'syrian', 'jordanian',
    'egyptian', 'iranian', 'iraqi', 'saudi', 'emirati', 'yemeni',
    'nigerian', 'kenyan', 'ethiopian', 'ghanaian', 'south african',
    'moroccan', 'algerian', 'tunisian', 'sudanese',
    'mexican', 'guatemalan', 'honduran', 'salvadoran', 'nicaraguan',
    'costa rican', 'panamanian', 'colombian', 'venezuelan', 'peruvian',
    'chilean', 'argentine', 'argentinian', 'brazilian', 'uruguayan',
    'ecuadorian', 'bolivian', 'paraguayan',
    'canadian', 'australian', 'new zealand', 'kiwi',
    'cuban', 'dominican', 'haitian', 'jamaican', 'puerto rican',
    'trinidadian',
})


def _classify_person_row(description: str, extract: str = '') -> bool:
    """Return True iff the description says this article is a US
    (or US-relevant) person.

    Uses substring matching on the lowercased description. Extract
    is a fallback when description is empty or very short.
    """
    text = (description or '').strip().lower()
    if not text and extract:
        # Fall back to the first sentence of extract - typical wiki
        # lead sentence "X (born YYYY) is an American {role} who...".
        text = (extract or '').split('.')[0].lower()
    if not text:
        return False

    has_us    = ('american' in text
                  or 'u.s. ' in text
                  or 'united states' in text)
    has_foreign = False
    for nat in _FOREIGN_NATIONALITY_TOKENS:
        if nat in text:
            has_foreign = True
            break
    # Dual-nationals ("British-American actress") stay.
    if has_foreign and not has_us:
        return False
    if not has_us:
        # Some Americans have descriptions that omit nationality
        # ("Deaf actress", "professional wrestler born in Ohio").
        # Fall through to the role check; if we can prove personhood
        # AND the description doesn't say foreign, we tentatively
        # accept as a plausible US person. Better than false-rejecting
        # sparse-description Americans.
        pass

    # Personhood: some role token must appear. We split on non-word
    # boundaries so "American actress" tokenizes as ["american",
    # "actress"] and matches "actress" in the role set.
    tokens = re.findall(r"[a-z']+", text)
    for tok in tokens:
        if tok in _PERSON_ROLE_TOKENS:
            return True
    # Multi-word roles ("martial artist", "band member") - split the
    # role set into hyphenated singletons; also try 2-word matches.
    for bigram in (' '.join(pair) for pair in zip(tokens, tokens[1:])):
        if bigram.replace(' ', '-') in _PERSON_ROLE_TOKENS:
            return True
    # Biographical "(born YYYY)" or "YYYY-YYYY" pattern in the raw
    # description is a strong personhood signal even without a role
    # token.
    if re.search(r'\bborn\s+\d{4}\b', text):
        return has_us or not has_foreign
    if re.search(r'\b\d{4}[-–]\d{4}\b', text):
        return has_us or not has_foreign
    return False


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


def _fetch_summary(article_slug: str) -> dict:
    """Hit the page-summary REST endpoint for a single article.

    Returns `{"description": ..., "extract": ..., "thumbnail_url": ...}`.
    Any field may be empty. Silent on network / parse failures so a
    single missing summary never blocks the batch.
    """
    url = _SUMMARY_API.format(slug=article_slug)
    try:
        r = requests.get(url, headers={'User-Agent': _UA}, timeout=8)
    except Exception as e:
        logger.debug("wiki summary %s: %s", article_slug, e)
        return {}
    if not r.ok:
        # 404 is expected for redirected / renamed pages, don't spam
        # the logs.
        if r.status_code != 404:
            logger.debug("wiki summary %s: http %s", article_slug, r.status_code)
        return {}
    try:
        data = r.json()
    except Exception:
        return {}
    thumb = (data.get('thumbnail') or {}).get('source') or ''
    return {
        'description': (data.get('description') or '').strip(),
        'extract':     (data.get('extract') or '').strip(),
        'thumbnail':   thumb,
    }


def _enrich_with_summaries(rows: list[dict], max_workers: int = 12) -> None:
    """Attach `description` + `extract` + `thumbnail` fields to each row.

    Runs summary lookups in a thread pool because we're doing ~30 HTTP
    calls that are each ~200ms; sequentially that's 6 seconds, in
    parallel it's ~500ms. Wikimedia rate-limits generously (200 req/s
    per IP) so 12 concurrent is safe.
    """
    if not rows:
        return
    slugs = [r['article'] for r in rows]
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_summary, s): s for s in slugs}
        for fut in as_completed(futures):
            slug = futures[fut]
            try:
                results[slug] = fut.result() or {}
            except Exception as e:
                logger.debug("wiki summary future %s: %s", slug, e)
                results[slug] = {}
    for r in rows:
        s = results.get(r['article']) or {}
        r['description'] = s.get('description') or ''
        r['extract']     = s.get('extract')     or ''
        if s.get('thumbnail'):
            r['thumbnail'] = s['thumbnail']


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
    top = rows[:_TOP_N]
    for i, r in enumerate(top, start=1):
        r['rank'] = i

    # Attach a one-liner "what is this" caption to every surfaced row.
    # We use Wikipedia's own summary API - free, fast in parallel,
    # always available. For rows that are trending BECAUSE of a
    # current event (which is nearly all of them, by construction),
    # the article description reads as the "why" a normal person needs
    # to make sense of the entry ("American Deaf actress" for Kaylee
    # Hottle, "1965 Boeing 727 accident" for Pan Am Flight 526A, etc).
    _enrich_with_summaries(top)

    # US-person subset for the Trending People card. Keeps only rows
    # whose description classifies as an American individual (per
    # `_classify_person_row`) and re-ranks the survivors 1..N. The
    # unfiltered `national` list stays intact so the standalone
    # Wikipedia trending tab (and any downstream consumer that wants
    # events / places / orgs) still gets everything.
    people_rows = []
    for r in top:
        if _classify_person_row(r.get('description') or '',
                                  r.get('extract')     or ''):
            people_rows.append(dict(r))
    for i, r in enumerate(people_rows, start=1):
        r['rank'] = i

    return {
        'national':      top,
        'people':        people_rows,
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
