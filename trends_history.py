"""
Historical arcs and per-item trajectories for Trends IQ.

Reconstructs an individual item's day-by-day trajectory from the dated
snapshot archives already written by two sources:

    1. External Google Trends daily snapshots
       s3://dashboard-inputs/blue_iq/trends_rss/v1/{geo}/{YYYY-MM-DD}.json
       Written by external_signals._trends_snap_put() every day per geo.

    2. Scraper daily snapshots
       s3://dashboard-inputs/trends_iq_snapshots/{YYYY-MM-DD}/{source}.json
       Written by scripts.trends_scrapers._base.write_snapshot() from the
       daily cron.

For any (kind, source, key) tuple this module returns a normalized
trajectory:

    {
      "kind":          "search|social|retailer|headline|article|person",
      "source":        "google|x|tiktok|youtube|instagram|target|...",
      "key":           "Fourth of July",
      "geo":           "US" | "State:Texas" | "DMA:New York",
      "days": [
        {"date": "2026-06-29", "rank": 3, "score": 47, "present": true},
        {"date": "2026-06-30", "rank": null, "score": null, "present": false},
        ...
      ],
      "first_seen":    "2026-06-29",
      "last_seen":     "2026-07-07",
      "present_days":  4,
      "best_rank":     1,
      "current_rank":  3,
      "momentum":      "breakout|rising|falling|sustained|dropped|new|new_arc"
    }

Also powers the alerts engine (see `classify_alert_transition`) which
compares yesterday's trajectory point to today's for every watched item.

Public API
----------
- history_for_search(term, *, geo, days) -> dict
- history_for_scraper(source, kind, key, *, days) -> dict
- history_for_item(kind, source, key, *, geo, days) -> dict     # dispatcher
- classify_alert_transition(yesterday, today) -> str            # for alerts

All I/O is cached in-process for the duration of the request; the
day-file S3 reads are cheap (each < 10KB) and paged access is bounded
by `days` (default 14, hard cap 60).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# S3 layout
# ────────────────────────────────────────────────────────────────────────────
_BUCKET = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')

# Google Trends (external_signals writes this)
_TRENDS_RSS_PREFIX = 'blue_iq/trends_rss/v1/'   # + {geo}/{YYYY-MM-DD}.json

# Scraper snapshots (scripts.trends_scrapers._base writes this)
_SCRAPER_DATED_PREFIX = 'trends_iq_snapshots/'  # + {YYYY-MM-DD}/{source}.json

# Per-item history cache (this module writes this)
_HISTORY_CACHE_PREFIX = 'trends_iq/history_cache/'

DEFAULT_DAYS = 14
MAX_DAYS     = 60
CACHE_TTL_S  = 6 * 3600  # rebuild cached trajectories at most every 6h


def _s3():
    try:
        import boto3  # type: ignore
        return boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')
    except Exception as e:
        logger.debug("trends_history: boto3 unavailable (%s)", e)
        return None


# ────────────────────────────────────────────────────────────────────────────
# Key normalization
# ────────────────────────────────────────────────────────────────────────────
_SLUG_CLEAN_RE = re.compile(r'[^a-z0-9]+')


def _slug(s: str) -> str:
    """Case-insensitive, punctuation-insensitive key. Trailing question
    marks, apostrophes, quotes, and Unicode punctuation all normalize
    to the same slug so 'Taylor Swift', 'taylor swift', "Taylor Swift's",
    and 'Taylor  Swift' all match."""
    if not s:
        return ''
    return _SLUG_CLEAN_RE.sub('-', s.strip().lower()).strip('-')


# In-process cache: {(cache_key, day_iso): parsed_snapshot_or_None}. Wiped
# on process restart. Guards against repeated day fetches during a single
# request that hits multiple items.
_DAY_CACHE: dict[tuple[str, str], Optional[dict]] = {}


def _fetch_day(prefix: str, day_iso: str, suffix: str = '') -> Optional[dict]:
    """Fetch one S3 JSON, in-process cached. Returns None on miss."""
    cache_key = (prefix, day_iso + '|' + suffix)
    if cache_key in _DAY_CACHE:
        return _DAY_CACHE[cache_key]
    s3 = _s3()
    if s3 is None:
        _DAY_CACHE[cache_key] = None
        return None
    key = f"{prefix}{day_iso}.json" if not suffix else f"{prefix}{day_iso}/{suffix}.json"
    try:
        resp = s3.get_object(Bucket=_BUCKET, Key=key)
        data = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        data = None
    _DAY_CACHE[cache_key] = data
    return data


def _iter_recent_days(days: int) -> list[str]:
    """Return the last `days` UTC date strings, oldest -> newest."""
    d = min(max(int(days or DEFAULT_DAYS), 1), MAX_DAYS)
    today = date.today()
    return [(today - timedelta(days=i)).isoformat() for i in range(d - 1, -1, -1)]


# ────────────────────────────────────────────────────────────────────────────
# Google Trends historical
# ────────────────────────────────────────────────────────────────────────────
def _google_geo_key(geo: str) -> str:
    """The Google Trends snapshot bucket uses one file per Google geo
    code (`US`, `US-CA`, `US-NY`, ...). The frontend passes
    dashboard-friendly labels like `National`, `State:California`,
    `DMA:New York` - map those to Google's ISO scheme here.

    Anything already in Google-shape (`US`, `US-CA`, ...) passes
    through unchanged."""
    g = (geo or '').strip()
    if not g or g.lower() == 'national':
        return 'US'
    # Already Google-shape (US or US-XX)
    if re.fullmatch(r'US(?:-[A-Z]{2})?', g):
        return g
    # Dashboard labels: try to unwrap "State:California" -> "US-CA" via
    # the same mapping external_signals uses. Import lazily to keep
    # trends_history importable even when external_signals is missing.
    try:
        from external_signals import US_STATE_TO_ISO, normalize_state  # type: ignore
        if g.startswith('State:'):
            name = normalize_state(g.split(':', 1)[1].strip())
            iso = US_STATE_TO_ISO.get(name)
            if iso:
                return iso
        # Bare state name
        name = normalize_state(g)
        iso = US_STATE_TO_ISO.get(name)
        if iso:
            return iso
    except Exception:
        pass
    # DMA labels don't have their own snapshot bucket yet (writer keys
    # to state code). Fall back to national so the arc still renders.
    return 'US'


def _find_row_for_search_term(rows: list, clicked_slug: str) -> Optional[tuple[int, dict]]:
    """Return `(rank, row)` for the best matching snapshot row given a
    user-clicked search term slug. Match rules, strongest first:

      1. Exact slug equality.
      2. Bidirectional substring containment of the clicked slug within
         a row's `term` slug OR any of its `trend_keywords` slugs.
         Google Trends re-phrases trending topics day-to-day - one day
         a story is "the odyssey", the next "the odyssey review", the
         next "christopher nolan the odyssey backlash". Exact matching
         breaks the historical arc on those. Substring matching in
         either direction reconnects them as one trend cluster.
      3. Fallback: significant-token overlap (both terms share >=1
         non-stopword token of length >= 4) - handles cases where the
         phrasing shifts too far for substring to catch.

    Returns None if nothing matches. `rank` is 1-based row position.
    """
    if not isinstance(rows, list) or not clicked_slug:
        return None

    # Pass 1: exact slug match on term (fastest, highest-confidence)
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        if _slug(r.get('term', '')) == clicked_slug:
            return i + 1, r

    # Pass 2: substring match on term or trend_keywords.
    # Prefer LONGEST candidate slug (best cluster fit) among matches.
    substring_hits: list[tuple[int, int, dict]] = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        candidate_slugs = [_slug(r.get('term', ''))]
        for kw in (r.get('trend_keywords') or [])[:12]:
            s = _slug(kw)
            if s and s not in candidate_slugs:
                candidate_slugs.append(s)
        for cand in candidate_slugs:
            if not cand or cand == clicked_slug:
                continue
            # 4-char guard prevents "the" / "war" / "usa" false hits
            if len(clicked_slug) >= 4 and clicked_slug in cand:
                substring_hits.append((len(cand), i + 1, r))
                break
            if len(cand) >= 4 and cand in clicked_slug:
                substring_hits.append((len(cand), i + 1, r))
                break
    if substring_hits:
        substring_hits.sort(key=lambda t: -t[0])
        _, rank, row = substring_hits[0]
        return rank, row

    # Pass 3: significant-token overlap fallback.
    _STOP = {'the', 'and', 'for', 'with', 'from', 'that', 'this',
             'vs', 'per', 'are', 'was', 'has', 'his', 'her', 'you'}
    clicked_toks = {t for t in re.split(r'[^a-z0-9]+', clicked_slug)
                     if len(t) >= 4 and t not in _STOP}
    if not clicked_toks:
        return None
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        term_slug = _slug(r.get('term', ''))
        row_toks = {t for t in re.split(r'[^a-z0-9]+', term_slug)
                     if len(t) >= 4 and t not in _STOP}
        overlap = clicked_toks & row_toks
        if overlap and (len(overlap) / max(1, min(len(clicked_toks), len(row_toks)))) >= 0.5:
            return i + 1, r
    return None


def history_for_search(term: str, *, geo: str = 'US',
                        days: int = DEFAULT_DAYS) -> dict:
    """Reconstruct a Google Trends search term's rank / volume / growth
    arc. Uses `_find_row_for_search_term` for fuzzy day matching so a
    trend that's re-phrased day-to-day still forms one coherent arc.
    Each day's `matched_term` records which variant matched so the UI
    can surface the day-specific phrasing if useful."""
    slug = _slug(term)
    if not slug:
        return _empty_arc('search', 'google', term, geo)
    geo_key = _google_geo_key(geo)
    day_list = _iter_recent_days(days)
    arc_days: list[dict] = []
    for day_iso in day_list:
        rows = _fetch_day(f"{_TRENDS_RSS_PREFIX}{geo_key}/", day_iso)
        # Snapshots are stored as either a bare list OR a dict payload.
        # Normalize to a list of row dicts.
        if isinstance(rows, dict):
            rows = rows.get('national') or rows.get('rows') or []
        hit = _find_row_for_search_term(rows or [], slug)
        if hit is not None:
            rank, row = hit
            arc_days.append({
                'date':          day_iso,
                'rank':          int(rank),
                'score':         row.get('score'),
                'volume':        int(row.get('volume') or 0),
                'growth_pct':    int(row.get('volume_growth_pct') or 0),
                'matched_term':  (row.get('term') or '').strip(),
                'present':       True,
                'related':       (row.get('related') or [])[:5],
            })
        else:
            arc_days.append({'date': day_iso, 'rank': None, 'score': None, 'present': False})
    return _summarize_arc(arc_days, kind='search', source='google', key=term, geo=geo)


# ────────────────────────────────────────────────────────────────────────────
# Scraper historical (retailer, social, headline, article, person)
# ────────────────────────────────────────────────────────────────────────────
_SCRAPER_KIND_MAP = {
    # source -> kind (what the render code treats as the row type)
    'x':         'social',
    'tiktok':    'social',
    'youtube':   'social',
    'instagram': 'social',
    'reddit':    'social',
    'target':    'retailer',
    'walmart':   'retailer',
    'etsy':      'retailer',
    'sephora':   'retailer',
    'lululemon': 'retailer',
    'bestbuy':   'retailer',
    'nike':      'retailer',
    'ulta':      'retailer',
    'amazon':    'retailer',
    # Streaming platforms - all use `title` as their row key
    'netflix':    'streaming',
    'disneyplus': 'streaming',
    'hulu':       'streaming',
    'max':        'streaming',
    'primevideo': 'streaming',
    'espnplus':   'streaming',
    # GDELT-derived cards: headlines and trending people. Both share the
    # source name `gdelt` in the frontend (`_tiqActions('headline',
    # 'gdelt', ...)` and `_tiqActions('person', 'gdelt', ...)`); the
    # snapshot source names differ so the dispatcher below routes by
    # kind. See `_gdelt_source_for_kind`.
    'gdelt':         'headline',
    'gdelt-people':  'person',
    # Wikipedia + Philanthropy: frontend `source` differs from the
    # snapshot filename, so the reader has to alias via
    # _SOURCE_SNAPSHOT_ALIAS below. Rows sit under `national` with the
    # standard `title` key so the generic matcher works unchanged.
    'wikipedia':    'wikipedia',
    'philanthropy': 'news',
    # Music sub-sources - registered here so callers reaching the
    # generic branch don't get a `unknown` kind, but history_for_item
    # short-circuits these to history_for_music because the rows sit
    # under sources[sub].items (not `national`) inside music_charts.json.
    'spotify': 'music',
    'apple':   'music',
    'shazam':  'music',
}


# When the frontend `source` differs from the S3 snapshot filename, map
# it here. `wikipedia` -> `wikipedia_trending.json`, `philanthropy` ->
# `philanthropy_news.json`. Everything else uses source as filename.
_SOURCE_SNAPSHOT_ALIAS = {
    'wikipedia':    'wikipedia_trending',
    'philanthropy': 'philanthropy_news',
}


# Music sub-source ids that live inside the `music_charts` snapshot
# under `sources[sub].items`. `tiktok` collides with the social scraper
# of the same name, so the dispatcher gates on `kind=='music'` before
# it looks at the source id (see history_for_item).
_MUSIC_SUB_SOURCES = {'spotify', 'shazam', 'apple', 'tiktok'}


def _gdelt_source_for_kind(kind: str) -> str:
    """Map the frontend `gdelt` source + kind pair to the actual
    snapshot file: headlines live in `gdelt.json`, people live in
    `gdelt-people.json`."""
    return 'gdelt-people' if (kind or '').lower() == 'person' else 'gdelt'


def _item_key_from_scraper_row(source: str, row: dict) -> str:
    """The user-visible identifier a row goes by. Retailers use `name`,
    social platforms use `topic`/`title`/`hashtag`. Fall back sensibly."""
    for k in ('name', 'title', 'topic', 'hashtag', 'term', 'headline', 'text'):
        v = row.get(k)
        if v:
            return str(v)
    return ''


def _find_row_for_scraper_key(source: str, rows: list, clicked_slug: str
                              ) -> Optional[tuple[int, dict]]:
    """Same fuzzy-match strategy as `_find_row_for_search_term`, applied
    to scraper rows keyed by `name`/`title`/`topic`/`hashtag`. Handles
    day-to-day title drift: Netflix's "Voicemails for Isabelle" one day
    becomes "Voicemails for Isabelle: Season 1" the next; Reddit
    threads gain edits; TikTok hashtags gain suffixes."""
    if not isinstance(rows, list) or not clicked_slug:
        return None

    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        if _slug(_item_key_from_scraper_row(source, r)) == clicked_slug:
            rank = int(r.get('rank') or (i + 1))
            return rank, r

    substring_hits: list[tuple[int, int, dict]] = []
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        cand = _slug(_item_key_from_scraper_row(source, r))
        if not cand or cand == clicked_slug:
            continue
        if len(clicked_slug) >= 4 and clicked_slug in cand:
            substring_hits.append((len(cand), int(r.get('rank') or (i + 1)), r))
        elif len(cand) >= 4 and cand in clicked_slug:
            substring_hits.append((len(cand), int(r.get('rank') or (i + 1)), r))
    if substring_hits:
        substring_hits.sort(key=lambda t: -t[0])
        _, rank, row = substring_hits[0]
        return rank, row
    return None


def history_for_scraper(source: str, key: str, *,
                        days: int = DEFAULT_DAYS,
                        geo: str = 'National') -> dict:
    """Reconstruct a retailer/social item's rank arc from dated snapshots.

    Fuzzy day-matching handles title drift across days (see
    `_find_row_for_scraper_key`). `matched_key` on each day records
    which variant matched.
    """
    slug = _slug(key)
    if not slug:
        return _empty_arc(_SCRAPER_KIND_MAP.get(source, 'unknown'), source, key, geo)
    kind = _SCRAPER_KIND_MAP.get(source, 'unknown')
    snapshot_name = _SOURCE_SNAPSHOT_ALIAS.get(source, source)
    day_list = _iter_recent_days(days)
    arc_days: list[dict] = []
    for day_iso in day_list:
        payload = _fetch_day(f"{_SCRAPER_DATED_PREFIX}", day_iso, suffix=snapshot_name)
        rows: list = []
        if isinstance(payload, dict):
            rows = payload.get('national') or []
        hit = _find_row_for_scraper_key(source, rows, slug)
        if hit is not None:
            rank, row_hit = hit
            arc_days.append({
                'date':        day_iso,
                'rank':        rank,
                'score':       None,
                'matched_key': _item_key_from_scraper_row(source, row_hit),
                'present':     True,
                'price':       row_hit.get('price'),
                'url':         row_hit.get('url'),
                'image':       row_hit.get('image'),
            })
        else:
            arc_days.append({'date': day_iso, 'rank': None, 'score': None, 'present': False})
    return _summarize_arc(arc_days, kind=kind, source=source, key=key, geo=geo)


# ────────────────────────────────────────────────────────────────────────────
# Music historical (Shazam / Apple Music / TikTok Sounds)
# ────────────────────────────────────────────────────────────────────────────
def _find_music_row(rows: list, clicked_slug: str
                    ) -> Optional[tuple[int, dict]]:
    """Match a music row by slug. Music rows are keyed on `title` +
    `artist`, but the clicked key is either `title` alone or
    `title - artist` depending on where it came from. Try direct
    combined-slug, then title-only, then substring on either side."""
    if not isinstance(rows, list) or not clicked_slug:
        return None

    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        title  = (r.get('title') or '').strip()
        artist = (r.get('artist') or '').strip()
        combined = f"{title} - {artist}" if artist else title
        if _slug(combined) == clicked_slug or _slug(title) == clicked_slug:
            rank = int(r.get('rank') or (i + 1))
            return rank, r

    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        title_slug = _slug(r.get('title') or '')
        if not title_slug or len(title_slug) < 4:
            continue
        if title_slug in clicked_slug or clicked_slug in title_slug:
            rank = int(r.get('rank') or (i + 1))
            return rank, r
    return None


def history_for_music(sub: str, key: str, *,
                      days: int = DEFAULT_DAYS,
                      geo: str = 'National') -> dict:
    """Reconstruct the arc for a music item. `sub` is one of
    `shazam`/`apple`/`tiktok` and identifies which sub-list inside the
    daily `music_charts.json` snapshot to read.

    Music snapshots differ from every other scraper: they put rows
    under `sources[sub].items[]` rather than `national`. Rows carry
    `title` + `artist`; the clicked key is usually
    `"<title> - <artist>"` so we fuzzy-match both forms."""
    slug = _slug(key)
    if not slug:
        return _empty_arc('music', sub, key, geo)
    day_list = _iter_recent_days(days)
    arc_days: list[dict] = []
    for day_iso in day_list:
        payload = _fetch_day(f"{_SCRAPER_DATED_PREFIX}", day_iso,
                             suffix='music_charts')
        rows: list = []
        if isinstance(payload, dict):
            sub_block = (payload.get('sources') or {}).get(sub) or {}
            rows = sub_block.get('items') or []
        hit = _find_music_row(rows, slug)
        if hit is not None:
            rank, r = hit
            title  = (r.get('title') or '').strip()
            artist = (r.get('artist') or '').strip()
            arc_days.append({
                'date':        day_iso,
                'rank':        rank,
                'score':       None,
                'matched_key': f"{title} - {artist}" if artist else title,
                'present':     True,
                'url':         r.get('url'),
                'image':       r.get('image'),
            })
        else:
            arc_days.append({'date': day_iso, 'rank': None,
                             'score': None, 'present': False})
    return _summarize_arc(arc_days, kind='music', source=sub,
                           key=key, geo=geo)


# ────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ────────────────────────────────────────────────────────────────────────────
def history_for_item(kind: str, source: str, key: str, *,
                     geo: str = 'National',
                     days: int = DEFAULT_DAYS,
                     force_refresh: bool = False) -> dict:
    """Route to the right historical reconstructor. Kind is a soft hint;
    the source ultimately determines which bucket layout we read."""
    if not key:
        return _empty_arc(kind, source, key, geo)

    cache_slug = f"v2:{kind}:{source}:{_slug(key)}:{_slug(geo)}:{days}"
    if not force_refresh:
        cached = _read_history_cache(cache_slug)
        if cached is not None:
            return cached

    kind_l = (kind or '').lower()

    # Music has to come first because the `tiktok` source id also
    # matches a social scraper snapshot; kind='music' disambiguates.
    if kind_l == 'music' and source in _MUSIC_SUB_SOURCES:
        arc = history_for_music(source, key, days=days, geo=geo)
    elif source == 'google' or kind_l == 'search':
        arc = history_for_search(key, geo=geo, days=days)
    elif source == 'gdelt':
        # Route by kind: headlines live in gdelt.json, people live in
        # gdelt-people.json. The frontend passes source='gdelt' for
        # both cards, so we resolve to the right snapshot file here.
        real_source = _gdelt_source_for_kind(kind)
        arc = history_for_scraper(real_source, key, days=days, geo=geo)
        # Restore the caller-facing kind label (dispatcher swapped it
        # to `person` via the _SCRAPER_KIND_MAP lookup).
        arc['source'] = 'gdelt'
        arc['kind']   = kind or arc.get('kind')
    elif source in _SCRAPER_KIND_MAP:
        arc = history_for_scraper(source, key, days=days, geo=geo)
    else:
        arc = _empty_arc(kind or 'unknown', source, key, geo)
        arc['error'] = f'no historical source for source={source!r} kind={kind!r}'

    # Don't persist empty arcs (total_days=0). Those are 'never even
    # looked' arcs coming out of _empty_arc, and caching them for 6h
    # would mask code-side fixes to the dispatcher.
    if arc.get('total_days'):
        _write_history_cache(cache_slug, arc)
    return arc


# ────────────────────────────────────────────────────────────────────────────
# Summarization
# ────────────────────────────────────────────────────────────────────────────
def _summarize_arc(arc_days: list[dict], *, kind: str, source: str,
                   key: str, geo: str) -> dict:
    """Attach derived stats + a momentum label to a day-by-day arc."""
    present = [d for d in arc_days if d.get('present')]
    first_seen  = present[0]['date']  if present else None
    last_seen   = present[-1]['date'] if present else None
    present_days = len(present)
    ranks = [d['rank'] for d in present if d.get('rank') is not None]
    best_rank    = min(ranks) if ranks else None
    current_rank = arc_days[-1]['rank'] if arc_days else None
    prev_rank    = arc_days[-2]['rank'] if len(arc_days) >= 2 else None

    momentum = _momentum(arc_days, present_days, current_rank, prev_rank)

    return {
        'kind':         kind,
        'source':       source,
        'key':          key,
        'geo':          geo,
        'days':         arc_days,
        'first_seen':   first_seen,
        'last_seen':    last_seen,
        'present_days': present_days,
        'total_days':   len(arc_days),
        'best_rank':    best_rank,
        'current_rank': current_rank,
        'prev_rank':    prev_rank,
        'momentum':     momentum,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


def _momentum(arc: list[dict], present_days: int,
              current_rank: Optional[int], prev_rank: Optional[int]) -> str:
    """One-word label describing the trajectory shape."""
    if not arc:
        return 'unknown'
    if current_rank is None and prev_rank is not None:
        return 'dropped'
    if current_rank is not None and prev_rank is None:
        # First appearance in the window vs any prior day where absent
        earlier_absent = any(not d.get('present') for d in arc[:-1])
        return 'new_arc' if not earlier_absent else 'new'
    if current_rank is not None and prev_rank is not None:
        delta = prev_rank - current_rank  # positive = climbed
        if delta >= 3:
            return 'breakout' if present_days <= 3 else 'rising'
        if delta <= -3:
            return 'falling'
        return 'sustained'
    return 'absent'


def _empty_arc(kind: str, source: str, key: str, geo: str) -> dict:
    return {
        'kind': kind, 'source': source, 'key': key, 'geo': geo,
        'days': [], 'first_seen': None, 'last_seen': None,
        'present_days': 0, 'total_days': 0,
        'best_rank': None, 'current_rank': None, 'prev_rank': None,
        'momentum': 'unknown',
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


# ────────────────────────────────────────────────────────────────────────────
# Cache (S3)
# ────────────────────────────────────────────────────────────────────────────
def _cache_key(slug: str) -> str:
    safe = re.sub(r'[^a-z0-9:_-]', '_', slug.lower())
    return f'{_HISTORY_CACHE_PREFIX}{safe}.json'


def _read_history_cache(slug: str) -> Optional[dict]:
    s3 = _s3()
    if s3 is None:
        return None
    try:
        resp = s3.get_object(Bucket=_BUCKET, Key=_cache_key(slug))
        data = json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return None
    gen = data.get('generated_at')
    if gen:
        try:
            dt = datetime.fromisoformat(gen.replace('Z', '+00:00'))
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            if age <= CACHE_TTL_S:
                return data
        except Exception:
            pass
    return None


def _write_history_cache(slug: str, arc: dict) -> None:
    s3 = _s3()
    if s3 is None:
        return
    try:
        s3.put_object(
            Bucket=_BUCKET,
            Key=_cache_key(slug),
            Body=json.dumps(arc, ensure_ascii=False).encode('utf-8'),
            ContentType='application/json',
            CacheControl='public, max-age=300',
        )
    except Exception as e:
        logger.debug("history cache write failed for %s: %s", slug, e)


# ────────────────────────────────────────────────────────────────────────────
# Alert classification (used by the digest job)
# ────────────────────────────────────────────────────────────────────────────
def classify_alert_transition(prev_arc: Optional[dict],
                              curr_arc: dict) -> Optional[dict]:
    """Return an alert dict if today's arc represents a notable change vs
    yesterday's, otherwise None.

    prev_arc is the arc as of the previous digest run (yesterday). curr_arc
    is today's freshly computed arc. Both share the same (kind, source,
    key) tuple.

    Alert types:
      NEW           - item just appeared on the chart for the first time
      BREAKOUT      - present <= 3 days AND climbed >= 3 ranks vs yesterday
      RISING        - present > 3 days AND climbed >= 3 ranks vs yesterday
      FALLING       - fell >= 3 ranks vs yesterday
      DROPPED_OFF   - was present yesterday, absent today
      RETURNED      - was absent yesterday, present today
    Rank change < 3 with same status = no alert (suppress noise).
    """
    key_tuple = (curr_arc.get('kind'), curr_arc.get('source'), curr_arc.get('key'))
    curr_rank = curr_arc.get('current_rank')
    prev_rank = (prev_arc or {}).get('current_rank')

    if prev_rank is None and curr_rank is not None:
        atype = 'NEW' if (prev_arc is None or not (prev_arc.get('days'))) else 'RETURNED'
        return _alert(key_tuple, atype, prev_rank, curr_rank, curr_arc)
    if prev_rank is not None and curr_rank is None:
        return _alert(key_tuple, 'DROPPED_OFF', prev_rank, curr_rank, curr_arc)
    if prev_rank is not None and curr_rank is not None:
        delta = prev_rank - curr_rank  # positive = climbed
        if delta >= 3:
            atype = 'BREAKOUT' if curr_arc.get('present_days', 0) <= 3 else 'RISING'
            return _alert(key_tuple, atype, prev_rank, curr_rank, curr_arc)
        if delta <= -3:
            return _alert(key_tuple, 'FALLING', prev_rank, curr_rank, curr_arc)
    return None


def _alert(key_tuple: tuple, atype: str,
            prev_rank: Optional[int], curr_rank: Optional[int],
            curr_arc: dict) -> dict:
    kind, source, key = key_tuple
    return {
        'kind':       kind,
        'source':     source,
        'key':        key,
        'alert_type': atype,
        'prev_rank':  prev_rank,
        'curr_rank':  curr_rank,
        'momentum':   curr_arc.get('momentum'),
        'best_rank':  curr_arc.get('best_rank'),
        'present_days': curr_arc.get('present_days'),
        'first_seen':   curr_arc.get('first_seen'),
        'occurred_at':  datetime.now(timezone.utc).isoformat(),
    }
