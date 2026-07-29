"""
"Why is this trending?" one-line context generator.

Runs once per day in the scraper cron. Reads the top trending items
from the other snapshots we've already collected (Wikipedia, GDELT
people, Google Trends, headlines), packs them with whatever context
we have, and asks Claude to produce a single-sentence explanation
for each in a batch.

Two-pass design:

  1. Batch pass (Haiku, cheap): send every item with its local clues
     (headlines, chart labels, bios) and get back a JSON dict of
     captions. Items with rich local signal all get a caption here.

  2. Web-search fill pass (Sonnet 4.5 with the native web_search tool):
     for every item the batch left empty, run a per-item agent that
     actually googles the name and writes a caption grounded in fresh
     news. This is what turns bare mover terms and no-signal people
     rows into actionable WHY captions on the fused feed.

Output snapshot shape (kind='meta'):

    {
      "source":       "why_trending",
      "kind":         "meta",
      "fetched_at":   "2026-07-09T...",
      "generated_at": "...",
      "items":        {                          # keyed by normalized name
        "elon musk":         "Announced new Grok model this morning.",
        "andrey santos":     "Chelsea midfielder scored winner in Club World Cup.",
        ...
      },
      "count":        <int>,
    }

`trends_iq._read_snapshot('why_trending')` is what the app calls
(unchanged pattern). `compute_view` stamps `row['why'] = items[key]`
on matching person / wikipedia / search / mover rows.

Design decisions
----------------
- Runs DAILY in cron, NOT at request time. Dashboard latency stays
  flat and Claude cost is bounded (~$0.05/day at 30 items via haiku).
- Uses a single batch prompt so we spend one round-trip per day.
- Reads other scrapers' snapshots at their `latest/` keys. If a
  source hasn't run yet, we skip - no hard dependency ordering.
- If ANTHROPIC_API_KEY is unset, writes an empty snapshot with an
  `error` field so the app renders normally.

Standalone:

    python3 -m scripts.trends_scrapers.why_trending
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
import sys
import time
import urllib.parse
from datetime import date, timedelta
from typing import Any, Optional

import boto3
import requests

logger = logging.getLogger(__name__)


# Mirror trends_iq.py so we're guaranteed to read the same S3 keys the
# app reads. If either file moves, they move together.
_S3_BUCKET      = 'dashboard-inputs'
_S3_PREFIX      = 'trends_iq_snapshots/latest/'
# Some sources (gdelt, gdelt-people) don't have a latest/ mirror -
# they're only written to the dated history path. Look back this many
# days for those.
_HISTORY_LOOKBACK_DAYS = 4

# Cap how many items we ask Claude to explain per day. We size to cover
# every row visible on the 🔥 Trending fused feed (top ~60) PLUS the
# standalone Trending People card (top 30) since that's the highest-
# scrutiny surface for the bio-fallback failure mode.
#
#   Wikipedia          -> top 30
#   GDELT people       -> top 12
#   Search             -> top 15
#   Movers (b + r)     -> top 20  (breakout + rising, the momentum core
#                                   of the fused feed)
#   Films              -> top 15  (dedupe across ticketing platforms)
#   Streaming          -> top 15  (dedupe across streaming platforms)
#   Music              -> top 15  (dedupe across music charts)
#   Headlines          -> top 15
_MAX_WIKI_ITEMS      = 30
_MAX_PEOPLE_ITEMS    = 12
_MAX_SEARCH_ITEMS    = 15
_MAX_MOVER_ITEMS     = 30
_MAX_FILM_ITEMS      = 15
_MAX_STREAMING_ITEMS = 15
_MAX_MUSIC_ITEMS         = 15
_MAX_HEADLINE_ITEMS      = 15
_MAX_PODCAST_ITEMS       = 15
_MAX_BOOK_ITEMS          = 15
_MAX_SOCIAL_ITEMS_PER    = 8       # per platform (reddit / youtube / tiktok)
_TOTAL_ITEM_CAP          = 180

# Single-sentence explanations don't need Opus. Match the model naming
# convention the rest of the workspace uses (claude_client.py defaults
# to claude-sonnet-4-5); haiku-4-5 is the cheap fast tier from the
# same family. Overridable via WHY_TRENDING_MODEL env var.
_CLAUDE_MODEL = os.environ.get('WHY_TRENDING_MODEL') or 'claude-haiku-4-5'
# Enough to fit 120 items of context-rich prompt (~200 tokens each) +
# 120 responses (~30 tokens each). Empirical cap on haiku is 8k output.
_MAX_TOKENS   = 8000

# ---------------------------------------------------------------------------
# WEB SEARCH FALLBACK (per-item, real-time)
# ---------------------------------------------------------------------------
# The batch prompt above only produces a caption when we hand Claude
# useful CLUES (headlines, chart labels, related queries, bio). Names
# with zero local signal - a mover term nobody wrote about yet, a
# music track with no news pickup, a person Wikipedia surfaced but
# neither GDELT nor the pooled news feeds cover - come back with why="".
#
# For every such item we run a SECOND per-item Claude call with the
# native `web_search_20250305` tool enabled. Claude issues a real
# Google-style query, reads the top results, and writes a one-line
# WHY caption grounded in fresh news. This is what turns "" into
# "Glen Hansard died in a motorcycle crash in Dublin" on the day it
# happens (verified against AP, BBC, RTE on 2026-07-29).
#
# Cost budget: ~$0.02 per web_search call on Sonnet 4.5, capped at
# _WEBSEARCH_MAX_ITEMS = 80 items per day = ~$1.60/day. Runs in
# parallel with _WEBSEARCH_CONCURRENCY workers so a full pass fits
# in the daily cron window.
_WEBSEARCH_MODEL       = (os.environ.get('WHY_TRENDING_WEBSEARCH_MODEL')
                           or 'claude-sonnet-4-5')
_WEBSEARCH_MAX_ITEMS   = 80
_WEBSEARCH_CONCURRENCY = 8
_WEBSEARCH_MAX_USES    = 2      # per-item cap on tool calls
_WEBSEARCH_MAX_TOKENS  = 800    # room for search results + one sentence
_WEBSEARCH_TIMEOUT_S   = 45     # per-call wall clock


_STOPWORDS = {'the', 'a', 'an', 'and', 'of', 'in', 'on', 'to', 'for', 'at'}


def _cp_normalize(text: str) -> str:
    """Case-fold, strip punctuation, drop stopwords, collapse spaces.
    MUST match the normalization used in `trends_iq._cp_normalize` so
    the app can look up entries by the same key."""
    if not text:
        return ''
    s = text.lower().lstrip('#').strip()
    s = re.sub(r'[^\w\s]+', ' ', s)
    tokens = [t for t in s.split() if t and t not in _STOPWORDS]
    return ' '.join(tokens)


def _s3():
    return boto3.client('s3')


def _read_snapshot(source: str) -> Optional[dict]:
    """Return the S3 snapshot for `source` or None on any failure.

    First tries the `latest/` prefix (fresh daily-cron output). If that
    misses, walks backwards through the dated history path up to
    _HISTORY_LOOKBACK_DAYS - covers sources like `gdelt-people` and
    `gdelt` that are only written to the dated path.
    """
    try:
        obj = _s3().get_object(Bucket=_S3_BUCKET, Key=f'{_S3_PREFIX}{source}.json')
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        pass
    for offset in range(0, _HISTORY_LOOKBACK_DAYS):
        d = date.today() - timedelta(days=offset)
        key = f'trends_iq_snapshots/{d.isoformat()}/{source}.json'
        try:
            obj = _s3().get_object(Bucket=_S3_BUCKET, Key=key)
            return json.loads(obj['Body'].read().decode('utf-8'))
        except Exception:
            continue
    logger.info("why_trending: skip %s (no snapshot in latest/ or last %d days)",
                 source, _HISTORY_LOOKBACK_DAYS)
    return None


def _build_name_headline_index(
    gdelt_people_snap: Optional[dict],
) -> dict[str, list[str]]:
    """Build the AUTHORITATIVE lookup: normalized-name -> already-
    attributed headlines that mention that person.

    Populated from `gdelt-people.national[i].context` (the scraper
    pre-attributes headlines to each person via NER). This is the
    strongest signal; the substring fallback in
    `_lookup_headlines_for` covers everyone else via a wider pool.
    """
    idx: dict[str, list[str]] = {}
    for p in (gdelt_people_snap or {}).get('national', []):
        name = p.get('name') or ''
        key = _cp_normalize(name)
        if not key:
            continue
        headlines = [h for h in (p.get('context') or []) if h and isinstance(h, str)]
        if headlines:
            idx.setdefault(key, []).extend(headlines[:5])
    return idx


def _flatten_headline_pool(*snaps: Optional[dict]) -> list[str]:
    """Flatten every headline-shaped title across the provided
    snapshots into a single list of unique strings. Used as the pool
    for the substring-match cross-reference in `_lookup_headlines_for`.

    Sources we mine (in order of trust):
    - `gdelt.national[i].title`             top world / US headlines
    - `reddit.national[i].title`            top Reddit posts
    - `philanthropy_news.national[i].title` philanthropy RSS
    - `youtube.national[i].title`           top YouTube trending videos
    - `x.national[i].title`                 X trending posts

    Deduped; cap at 400 headlines total (plenty of surface for
    substring matching, still cheap).
    """
    seen: set[str] = set()
    out:  list[str] = []
    for snap in snaps:
        if not snap:
            continue
        for k in ('national', 'items', 'articles', 'top_articles'):
            rows = snap.get(k)
            if isinstance(rows, list):
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    title = (r.get('title') or r.get('headline')
                             or r.get('text') or '').strip()
                    if not title or title in seen:
                        continue
                    seen.add(title)
                    out.append(title)
                break
        if len(out) >= 400:
            break
    return out[:400]


# ---------------------------------------------------------------------------
# GDELT DocAPI per-name search fallback
# ---------------------------------------------------------------------------
# When neither the NER-attributed people index nor the pooled headline
# scan turn up a hit for a trending name (e.g. Wikipedia has "David
# Jonsson" trending because of a specific press cycle, but the pooled
# gdelt/reddit/youtube snapshots don't mention him), fall through to
# GDELT's public DOC 2.1 API and query for that exact name directly.
#
# GDELT indexes ~100M+ news articles across 100+ languages in near real
# time; the DocAPI is free, keyless, and returns JSON. Docs:
# https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
#
# We restrict to English + a 3-day timespan so the results are always
# fresh enough to explain a WHY. Called only for rows without local
# signal, in parallel, so the per-run cost stays bounded.
_GDELT_DOC_URL = 'https://api.gdeltproject.org/api/v2/doc/doc'
_GDELT_TIMEOUT = 12
_GDELT_UA      = 'BG-Trends/1.0 (jenna@crosswalknyc.com)'
# GDELT rate-limits at "one request every 5 seconds" per IP across the
# entire DocAPI endpoint. Anything faster returns HTTP 429 (sometimes
# as plain-text 200 with a "Please limit requests..." body). 7s gives
# a comfortable safety margin so occasional network jitter doesn't
# push us over the edge, and keeps 30 misses under 4 minutes.
_GDELT_MIN_INTERVAL = 7.0
# Domains that publish trend-piece SEO fluff with the query verbatim
# ("David Jonsson net worth 2025", "Everything you need to know about
# Kobe McDonald") - they mention the name but don't describe an event.
_GDELT_DOMAIN_DENY = (
    'celebnetworth', 'famousbirthdays', 'quora', 'wikitia',
    'astrocharts', 'ranker.com',
)


def _gdelt_search_headlines(display_name: str, max_records: int = 5,
                             timespan_days: int = 3) -> list[str]:
    """Query GDELT DocAPI for recent English news articles that mention
    `display_name` verbatim. Returns up to `max_records` deduped
    headlines. Silent on any failure so a single miss never blocks the
    batch."""
    name = (display_name or '').strip()
    if len(name) < 3:
        return []
    # Empirically (2026-07-27), adding `sourcelang:eng` to the query
    # pushes every request into a stricter rate-limit bucket and
    # returns HTTP 429 on almost every call. Dropping the filter gives
    # us clean 200s + real articles; the DocAPI's default relevance
    # ranking already surfaces English news for English names anyway.
    params = {
        'query':      f'"{name}"',
        'mode':       'artlist',
        'maxrecords': str(max_records * 3),  # oversample; we filter+dedupe below
        'format':     'json',
        'sort':       'hybridrel',
        'timespan':   f'{timespan_days}d',
    }
    url = _GDELT_DOC_URL + '?' + urllib.parse.urlencode(params)
    try:
        r = requests.get(url, headers={'User-Agent': _GDELT_UA},
                         timeout=_GDELT_TIMEOUT)
    except Exception as e:
        logger.debug("gdelt search %r: %s", name, e)
        return []
    if not r.ok:
        logger.debug("gdelt search %r: http %s", name, r.status_code)
        return []
    # If we're throttled, GDELT returns HTTP 200 with a plain-text
    # message instead of JSON. Guard so the JSON parse doesn't spam
    # WARNINGs and we still return [] cleanly.
    body = (r.text or '').lstrip()
    if not body.startswith('{'):
        if 'limit requests' in body.lower():
            logger.warning("gdelt search %r: rate-limited (interval too tight)", name)
        return []
    try:
        data = r.json()
    except Exception:
        return []
    seen: set[str] = set()
    out:  list[str] = []
    for a in (data.get('articles') or []):
        title = (a.get('title') or '').strip()
        if not title or title in seen:
            continue
        # Filter SEO/bio-fluff domains.
        domain = (a.get('domain') or '').lower()
        if any(d in domain for d in _GDELT_DOMAIN_DENY):
            continue
        # Skip pure-list headlines that are just "N celebrity facts"
        # style - those are bio fluff, not event signal.
        if re.match(r'^\d+\s+(things|facts|reasons)\b', title, re.IGNORECASE):
            continue
        seen.add(title)
        out.append(title)
        if len(out) >= max_records:
            break
    return out


def _gdelt_search_many(names: list[str]) -> dict[str, list[str]]:
    """Run GDELT DocAPI searches for `names` SERIALLY, respecting the
    5s/IP rate limit. Returns `{display_name: [headlines]}`. Names with
    no hits get an empty list.

    Because GDELT throttles at one request per 5 seconds, parallel calls
    all get 429 or plain-text throttle responses instead of JSON. We
    space calls at `_GDELT_MIN_INTERVAL` seconds; 30 names -> ~3.5
    minutes, well within the daily cron budget.
    """
    if not names:
        return {}
    out: dict[str, list[str]] = {}
    last = 0.0
    for i, n in enumerate(names):
        elapsed = time.monotonic() - last
        if last and elapsed < _GDELT_MIN_INTERVAL:
            time.sleep(_GDELT_MIN_INTERVAL - elapsed)
        try:
            hits = _gdelt_search_headlines(n)
        except Exception as e:
            logger.debug("gdelt search %r: %s", n, e)
            hits = []
        out[n] = hits
        last = time.monotonic()
        logger.info("gdelt search %2d/%d %r -> %d hits",
                    i + 1, len(names), n, len(hits))
    return out


def _lookup_headlines_for(
    display_name: str,
    name_index: dict[str, list[str]],
    headline_pool: list[str],
) -> list[str]:
    """Return up to 4 news headlines that mention `display_name`.

    Two-pass:
    1. Exact normalized-name hit in `name_index` (NER-attributed).
    2. Multi-token substring scan of `headline_pool`. For multi-word
       display names ("David Jonsson"), requires EVERY 3+ char token
       to appear (case-insensitive) in the same headline. Prevents
       false hits like "David Beckham" matching "David Jonsson" via
       the shared first name; both tokens must co-occur. Single-token
       display names (e.g. "Snapple") just need one word-boundary hit.
    """
    key = _cp_normalize(display_name)
    out: list[str] = list(name_index.get(key, [])[:4])
    if len(out) >= 3:
        return out[:4]

    raw = (display_name or '').strip()
    if len(raw) < 3:
        return out

    tokens = [t.lower() for t in re.split(r'[^\w]+', raw) if len(t) >= 3]
    if not tokens:
        return out

    for h in headline_pool:
        hlow = h.lower()
        if all(t in hlow for t in tokens) and h not in out:
            out.append(h)
        if len(out) >= 4:
            break
    return out[:4]


def _dedupe_titles_across_sources(rows: list[dict], title_field: str,
                                    max_out: int) -> list[dict]:
    """Given a list of `{title_field: str, ...}` rows drawn from multiple
    platforms, keep the first occurrence of each normalized title and
    return up to `max_out` items. Preserves input order (which is
    already roughly rank-descending)."""
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        title = (r.get(title_field) or '').strip()
        key = _cp_normalize(title)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= max_out:
            break
    return out


def _flatten_music_tracks(music_snap: Optional[dict]) -> list[dict]:
    """Flatten `music_charts.json` into a single ranked list of tracks.
    Each entry: `{title, artist, chart_labels}`. Ordered by best rank
    across any chart (a track that's #1 on Spotify AND #3 on Apple ranks
    higher than one at #5 on both)."""
    if not music_snap:
        return []
    per_track: dict[str, dict] = {}
    for src_slug, panel in (music_snap.get('sources') or music_snap).items():
        if not isinstance(panel, dict):
            continue
        items = panel.get('items') or []
        chart = panel.get('label') or src_slug
        for i, it in enumerate(items[:25]):
            title  = (it.get('title')  or '').strip()
            artist = (it.get('artist') or '').strip()
            key = _cp_normalize(title + ' ' + artist)
            if not key:
                continue
            rank = i + 1
            e = per_track.setdefault(key, {
                'title':        title,
                'artist':       artist,
                'best_rank':    rank,
                'chart_labels': [],
            })
            e['chart_labels'].append(f'{chart} #{rank}')
            if rank < e['best_rank']:
                e['best_rank'] = rank
    return sorted(per_track.values(), key=lambda e: e['best_rank'])


def _flatten_titles(snap: Optional[dict], nested_keys: tuple = ('items',)) -> list[dict]:
    """Flatten a scraper snapshot whose shape is `{ src_slug: {label,
    items: [{title, ...}]}, ... }` into a single title-first ranked
    list `[{title, best_rank, platform_labels}]`. Ordered by best rank."""
    if not snap:
        return []
    per: dict[str, dict] = {}
    # Some snapshots wrap sources under `sources`, others put them at the
    # top level. Try `sources` first, fall back to top-level.
    sources = snap.get('sources') if isinstance(snap, dict) else None
    if not isinstance(sources, dict):
        sources = {k: v for k, v in (snap or {}).items()
                    if isinstance(v, dict) and 'items' in v}
    for src_slug, panel in (sources or {}).items():
        if not isinstance(panel, dict):
            continue
        items: list = []
        for key in nested_keys:
            v = panel.get(key)
            if isinstance(v, list) and v:
                items = v
                break
        label = panel.get('label') or src_slug
        for i, it in enumerate(items[:15]):
            title = (it.get('title') or '').strip()
            k = _cp_normalize(title)
            if not k:
                continue
            rank = i + 1
            e = per.setdefault(k, {
                'title':           title,
                'best_rank':       rank,
                'platform_labels': [],
            })
            e['platform_labels'].append(f'{label} #{rank}')
            if rank < e['best_rank']:
                e['best_rank'] = rank
    return sorted(per.values(), key=lambda e: e['best_rank'])


# The 6 streaming platforms each write their own snapshot. Mirror the
# slug + label pairs from trends_iq._STREAMING_PLATFORMS.
_STREAMING_PLATFORM_SLUGS = (
    ('netflix',    'Netflix'),
    ('disneyplus', 'Disney+'),
    ('hulu',       'Hulu'),
    ('max',        'Max'),
    ('primevideo', 'Prime Video'),
    ('espnplus',   'ESPN+'),
)


def _flatten_streaming_titles() -> list[dict]:
    """Read each per-platform streaming snapshot (netflix.json,
    disneyplus.json, hulu.json, max.json, primevideo.json,
    espnplus.json) and merge their US film + TV lists into a single
    dedup'd `[{title, kind, best_rank, platform_labels}]` ranked by
    best rank."""
    per: dict[str, dict] = {}
    for slug, label in _STREAMING_PLATFORM_SLUGS:
        snap = _read_snapshot(slug)
        if not snap:
            continue
        # Netflix ships us_films / us_tv (from Netflix's own weekly TSV).
        # The others put everything in `national` and we don't have a
        # film/tv split on this side; treat as "titles" of unspecified kind.
        buckets: list[tuple[str, list]] = []
        if slug == 'netflix':
            buckets.append(('film', snap.get('us_films') or []))
            buckets.append(('tv',   snap.get('us_tv')    or []))
        else:
            buckets.append(('title', snap.get('national') or []))
        for kind, items in buckets:
            for i, it in enumerate(items[:15]):
                title = (it.get('title') or '').strip()
                k = _cp_normalize(title)
                if not k:
                    continue
                rank = i + 1
                e = per.setdefault(k, {
                    'title':           title,
                    'kind':            kind,
                    'best_rank':       rank,
                    'platform_labels': [],
                })
                e['platform_labels'].append(f'{label} #{rank}')
                if rank < e['best_rank']:
                    e['best_rank'] = rank
                    e['kind']      = kind
    return sorted(per.values(), key=lambda e: e['best_rank'])


def _collect_items() -> list[dict]:
    """Gather the top items across every relevant source. Each returned
    dict looks like:

        {
          'name':      "Elon Musk",
          'source':    'wikipedia',   # or 'people', 'search', 'mover',
                                        #   'film', 'streaming', 'music',
                                        #   'headline'
          'context':   "Wikipedia views +67% (204k -> 341k). "
                       "Headlines mentioning: 'Musk unveils new Grok model.'",
        }

    De-dupes by normalized name (so an item that's top-of-list in three
    sources only gets one Claude line, applied to all three)."""
    out:  list[dict] = []
    seen: set[str]   = set()

    def _push(name: str, source: str, context: str = '') -> None:
        key = _cp_normalize(name)
        if not key or key in seen or len(key) < 3:
            return
        seen.add(key)
        out.append({'name': name, 'source': source, 'context': context.strip()})

    # Load upstream snapshots once so we can cross-reference names
    # against headlines below.
    people_snap        = _read_snapshot('gdelt-people') or _read_snapshot('gdelt')
    headlines_snap     = _read_snapshot('gdelt')
    reddit_snap        = _read_snapshot('reddit')
    youtube_snap       = _read_snapshot('youtube')
    philanthropy_snap  = _read_snapshot('philanthropy_news')
    x_snap             = _read_snapshot('x')
    wiki_snap          = _read_snapshot('wikipedia_trending')
    google_snap        = _read_snapshot('google_wide') or _read_snapshot('google_trends')

    name_index    = _build_name_headline_index(people_snap)
    headline_pool = _flatten_headline_pool(
        headlines_snap, reddit_snap, philanthropy_snap, youtube_snap, x_snap,
    )

    # Pre-compute which names have NO local headline hit and batch-fetch
    # GDELT DocAPI searches for them in parallel. This is what turns
    # rows like "David Jonsson", "Kobe McDonald", "Janet Street-Porter"
    # from bio-only into "the WHY they're trending" - if we can't find
    # a signal locally, we ask the world's news index directly.
    wiki_top    = list((wiki_snap or {}).get('national', []))[:_MAX_WIKI_ITEMS]
    people_top  = list((people_snap or {}).get('national', []))[:_MAX_PEOPLE_ITEMS]
    search_pool = (google_snap or {}).get('national') or (google_snap or {}).get('items') or []
    search_top  = list(search_pool)[:_MAX_SEARCH_ITEMS]

    # Movers (breakout + rising) come from trends_iq's request-time
    # diff of dated google_wide history. We collect them HERE so their
    # names participate in miss-detection alongside wiki/people/search;
    # mover rows are the most likely to lack local headline signal
    # (they're new/rising queries by definition), and GDELT DocAPI
    # backfill is what turns them into an actionable WHY.
    mover_terms_early: list[dict] = []
    try:
        _tiq_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        if _tiq_root not in sys.path:
            sys.path.insert(0, _tiq_root)
        import trends_iq  # type: ignore
        movers = trends_iq.compute_search_movers(state=None) or {}
        for bucket_key in ('breakout', 'rising'):
            for row in (movers.get(bucket_key) or []):
                term = (row.get('term') or '').strip()
                if term:
                    row['_bucket'] = bucket_key
                    mover_terms_early.append(row)
    except Exception as e:
        logger.info("why_trending: skip movers (compute failed: %s)", e)
    mover_terms_early = _dedupe_titles_across_sources(
        mover_terms_early, 'term', _MAX_MOVER_ITEMS)

    _misses: list[str] = []
    for w in wiki_top:
        title = (w.get('title') or '').strip()
        if title and not _lookup_headlines_for(title, name_index, headline_pool):
            _misses.append(title)
    for p in people_top:
        name = (p.get('name') or '').strip()
        if name and not (p.get('context') or []) and \
           not _lookup_headlines_for(name, name_index, headline_pool):
            _misses.append(name)
    for s in search_top:
        term = ((s.get('term') or s.get('query') or '') if isinstance(s, dict)
                else '').strip()
        if term and not (s.get('news_articles') or []) and \
           not _lookup_headlines_for(term, name_index, headline_pool):
            _misses.append(term)
    # Movers almost always miss the local pool (they're rising precisely
    # because they weren't well-covered yesterday) so all top mover
    # terms go into the GDELT search queue by default.
    for row in mover_terms_early:
        term = (row.get('term') or '').strip()
        if term and not _lookup_headlines_for(term, name_index, headline_pool):
            _misses.append(term)

    # Dedupe (a name may appear in multiple sources) and cap at 60 so
    # a full serial pass fits inside a ~7 min budget at 7s/query. Mover
    # inclusion roughly doubled the miss set vs the wiki/people/search
    # only path, so bumping the cap keeps mover terms from being
    # truncated out of the GDELT lookup.
    _seen_miss: set[str] = set()
    _uniq_miss: list[str] = []
    for n in _misses:
        k = _cp_normalize(n)
        if k in _seen_miss:
            continue
        _seen_miss.add(k)
        _uniq_miss.append(n)
    _uniq_miss = _uniq_miss[:60]
    gdelt_hits = _gdelt_search_many(_uniq_miss) if _uniq_miss else {}
    if gdelt_hits:
        logger.info("why_trending: GDELT search filled %d/%d misses with hits",
                    sum(1 for h in gdelt_hits.values() if h), len(gdelt_hits))

    def _headlines_for(name: str) -> list[str]:
        """Wrapper that adds the GDELT search result to the local
        headline lookup, so downstream callers just see one merged list."""
        local = _lookup_headlines_for(name, name_index, headline_pool)
        if local:
            return local[:4]
        return (gdelt_hits.get(name) or [])[:4]

    # Wikipedia FIRST so it takes the dedup priority - this is the
    # surface where the bio-fallback problem was most visible, so we
    # want maximum coverage here.
    for w in wiki_top:
        title    = w.get('title') or ''
        pct      = w.get('delta_pct')
        views_t  = w.get('views_today') or 0
        views_p  = w.get('views_prior') or 0

        clues: list[str] = []
        if w.get('is_new'):
            clues.append(f'Brand-new in Wikipedia top-1000; {views_t:,} views yesterday.')
        elif pct is not None:
            pct_int = int(round(pct * 100))
            clues.append(
                f'Wikipedia views {pct_int:+d}% ({views_p:,} -> {views_t:,}).'
            )
        else:
            clues.append(f'{views_t:,} Wikipedia pageviews yesterday.')

        # Cross-reference against the pooled news headlines PLUS a
        # per-name GDELT DocAPI fallback for names the local pool
        # didn't cover. Without any headline signal Claude has no event
        # to explain and correctly returns an empty string.
        headlines = _headlines_for(title)
        if headlines:
            hl_str = ' | '.join(f'"{h}"' for h in headlines)
            clues.append(f'News headlines mentioning {title}: {hl_str}')

        # Attach the Wikipedia extract as a LAST-RESORT bio clue. Claude
        # is told NEVER to output the bio directly, but the bio helps
        # it recognize domain (actor / athlete / brand) so if the only
        # signal is "views up", Claude at least won't hallucinate.
        extract = (w.get('extract') or '').strip()
        if extract:
            # Pass up to ~2 sentences (250 chars) so Claude has enough
            # to write a rich "known-for" caption when the extract is
            # the only signal. Truncating too aggressively (1 sentence)
            # left Claude with just "American football coach" - not
            # enough to say "known for engineering the Chiefs run".
            bio = extract[:250]
            if len(extract) > 250:
                bio = bio.rsplit(' ', 1)[0] + '...'
            clues.append(f'Wikipedia bio (paraphrase into known-for caption, do not quote verbatim): "{bio}"')

        _push(title, 'wikipedia', ' '.join(clues))

    # People (GDELT). Reuse the context headlines that came with each
    # person entry, then top up from GDELT DocAPI if the row didn't
    # ship any (rare - most people rows are already headline-enriched).
    for p in people_top:
        name = p.get('name') or ''
        m    = p.get('mentions')
        clues: list[str] = []
        if m:
            clues.append(f'Mentioned in {m} top-news articles this cycle.')
        headlines = [h for h in (p.get('context') or [])[:4] if h]
        if not headlines:
            headlines = _headlines_for(name)
        if headlines:
            hl_str = ' | '.join(f'"{h}"' for h in headlines)
            clues.append(f'Headlines: {hl_str}')
        _push(name, 'people', ' '.join(clues))

    # Google Trends (search). Volume + related queries are the hints.
    for s in search_top:
        term        = s.get('term') or s.get('query') or ''
        vol         = s.get('volume') or s.get('score') or 0
        related_qs  = (s.get('trend_keywords') or s.get('related_queries')
                       or s.get('related') or [])[:4]
        articles    = (s.get('news_articles') or [])[:2]
        clues: list[str] = []
        if vol:
            clues.append(f'{vol:,}+ searches.')
        if related_qs:
            clues.append('Related queries: ' + ', '.join(str(q) for q in related_qs) + '.')
        for a in articles:
            t = (a or {}).get('title') if isinstance(a, dict) else str(a)
            if t:
                clues.append(f'Headline: "{t}"')
        # Cross-reference the search term against pooled headlines + a
        # per-term GDELT search (populated in the fallback pass above).
        for h in _headlines_for(term):
            clues.append(f'Related headline: "{h}"')
        _push(term, 'search', ' '.join(clues))

    # -----------------------------------------------------------------
    # Movers (breakout + rising) - momentum core of the fused feed.
    # `mover_terms_early` was populated up top so mover names could
    # participate in GDELT miss-detection. Now we emit them with the
    # richest available signal set: the mover row itself carries
    # `related`, `trend_keywords`, `news_articles`, and `volume` from
    # google_wide, and those clues are what let Claude write a real
    # WHY instead of returning "".
    # -----------------------------------------------------------------
    for row in mover_terms_early:
        term   = (row.get('term') or '').strip()
        bucket = row.get('_bucket') or 'mover'
        vol    = row.get('volume') or row.get('score')
        pct    = row.get('mentions_change_pct') or row.get('delta_pct')
        related_qs     = list(row.get('related')        or [])[:5]
        trend_keywords = list(row.get('trend_keywords') or [])[:5]
        news_articles  = list(row.get('news_articles')  or [])[:3]

        clues: list[str] = []
        if bucket == 'breakout':
            clues.append('Breakout search query (5x+ growth off a low baseline).')
        else:
            if isinstance(pct, (int, float)):
                clues.append(f'Rising search query (up {int(round(pct * 100)):+d}% vs baseline).')
            else:
                clues.append('Rising search query (sustained 25%+ growth vs baseline).')
        if vol:
            clues.append(f'{vol:,}+ searches this window.')
        # Related queries + trend_keywords are the surrounding search
        # context - what OTHER things people search when they search
        # this term. Often names an event, person, or product that
        # is the actual reason for the spike.
        if related_qs:
            clues.append('Related queries: ' +
                          ', '.join(str(q) for q in related_qs) + '.')
        if trend_keywords:
            clues.append('Trend breakdown keywords: ' +
                          ', '.join(str(q) for q in trend_keywords) + '.')
        # News articles pre-attributed by google_wide are the strongest
        # anchoring signal. Fall back to the pooled + GDELT lookup for
        # terms google_wide didn't attribute.
        for a in news_articles:
            t = (a or {}).get('title') if isinstance(a, dict) else str(a)
            if t:
                clues.append(f'Headline: "{t}"')
        if not news_articles:
            for h in _headlines_for(term)[:3]:
                clues.append(f'Headline: "{h}"')
        _push(term, 'mover', ' '.join(clues))

    # -----------------------------------------------------------------
    # Films (theatrical ticketing). Titles that show up on 3+ of the
    # 5 ticketing platforms are the ones with real cultural presence
    # this week; the platform_labels list gives Claude the "in theaters
    # everywhere" signal.
    # -----------------------------------------------------------------
    film_snap = _read_snapshot('film_ticketing')
    film_titles = _flatten_titles(film_snap)[:_MAX_FILM_ITEMS]
    for f in film_titles:
        title  = f.get('title') or ''
        labels = f.get('platform_labels') or []
        clues: list[str] = []
        if labels:
            clues.append('In theaters this week: ' + ', '.join(labels[:5]) + '.')
        for h in _headlines_for(title)[:2]:
            clues.append(f'Headline: "{h}"')
        _push(title, 'film', ' '.join(clues))

    # -----------------------------------------------------------------
    # Streaming (film + tv titles). Same dedup pattern as films but
    # across streaming platforms (Netflix, Disney+, Hulu, Max, Prime
    # Video, ESPN+) and split by film vs tv.
    # -----------------------------------------------------------------
    streaming_titles = _flatten_streaming_titles()[:_MAX_STREAMING_ITEMS]
    for st in streaming_titles:
        title  = st.get('title') or ''
        kind   = st.get('kind')  or 'title'
        labels = st.get('platform_labels') or []
        clues: list[str] = []
        if labels:
            clues.append(f'Top-charting {kind} on streaming this week: ' +
                          ', '.join(labels[:5]) + '.')
        for h in _headlines_for(title)[:2]:
            clues.append(f'Headline: "{h}"')
        _push(title, 'streaming', ' '.join(clues))

    # -----------------------------------------------------------------
    # Music (tracks). Key by BARE TITLE (not "Title by Artist") so
    # the snapshot lookup collides with the fused-feed key, which uses
    # the bare title. Artist name goes into the clues so Claude can
    # anchor on "new album released", "featured in a viral TikTok", etc.
    # -----------------------------------------------------------------
    music_snap = _read_snapshot('music_charts') or _read_snapshot('music')
    music_tracks = _flatten_music_tracks(music_snap)[:_MAX_MUSIC_ITEMS]
    for m in music_tracks:
        title  = m.get('title')  or ''
        artist = m.get('artist') or ''
        labels = m.get('chart_labels') or []
        clues: list[str] = []
        if labels:
            clues.append('Charting on: ' + ', '.join(labels[:5]) + '.')
        if artist:
            clues.append(f'Artist: {artist}.')
        # Headline search uses the "title + artist" phrase for
        # discriminating power, but the KEY we push is the bare title.
        query = f'{title} {artist}' if artist else title
        for h in _headlines_for(query)[:2]:
            clues.append(f'Headline: "{h}"')
        _push(title, 'music', ' '.join(clues))

    # -----------------------------------------------------------------
    # Headlines. Ranked article titles - the "why" is essentially
    # the headline itself, but Claude will paraphrase it into a
    # neutral single-sentence explanation.
    # -----------------------------------------------------------------
    if headlines_snap:
        top_headlines = (headlines_snap.get('national')
                          or headlines_snap.get('items')
                          or headlines_snap.get('articles') or [])[:_MAX_HEADLINE_ITEMS]
        for h in top_headlines:
            title  = (h.get('title') or h.get('headline') or '').strip()
            source = h.get('source') or h.get('domain') or ''
            clues: list[str] = []
            if source:
                clues.append(f'Headline from {source}: "{title}".')
            else:
                clues.append(f'Headline: "{title}".')
            _push(title, 'headline', ' '.join(clues))

    # -----------------------------------------------------------------
    # Podcasts (top shows across Apple / Spotify / Amazon / Audible).
    # -----------------------------------------------------------------
    pod_snap = _read_snapshot('podcast_charts') or _read_snapshot('podcasts')
    pod_titles = _flatten_titles(pod_snap)[:_MAX_PODCAST_ITEMS]
    for p in pod_titles:
        title  = p.get('title') or ''
        labels = p.get('platform_labels') or []
        clues: list[str] = []
        if labels:
            clues.append('Top podcast on: ' + ', '.join(labels[:4]) + '.')
        for h in _headlines_for(title)[:2]:
            clues.append(f'Headline: "{h}"')
        _push(title, 'podcast', ' '.join(clues))

    # -----------------------------------------------------------------
    # Books (top titles across Amazon / Apple Books / Audible).
    # -----------------------------------------------------------------
    book_snap = _read_snapshot('book_charts') or _read_snapshot('books')
    book_titles = _flatten_titles(book_snap)[:_MAX_BOOK_ITEMS]
    for b in book_titles:
        title  = b.get('title') or ''
        labels = b.get('platform_labels') or []
        clues: list[str] = []
        if labels:
            clues.append('Top book on: ' + ', '.join(labels[:4]) + '.')
        for h in _headlines_for(title)[:2]:
            clues.append(f'Headline: "{h}"')
        _push(title, 'book', ' '.join(clues))

    # -----------------------------------------------------------------
    # Social (Reddit / YouTube / TikTok top posts, videos, topics).
    # Each social platform snapshot has `national[i].{title, topic,
    # hashtag}` shape - use whichever is present as the display name.
    # -----------------------------------------------------------------
    for social_slug in ('reddit', 'youtube', 'tiktok'):
        s_snap = _read_snapshot(social_slug)
        if not s_snap:
            continue
        items = (s_snap.get('national') or s_snap.get('items') or [])[:_MAX_SOCIAL_ITEMS_PER]
        for it in items:
            text = (it.get('title') or it.get('topic') or it.get('hashtag') or '').strip()
            if not text:
                continue
            clues: list[str] = []
            clues.append(f'Trending on {social_slug.title()} right now.')
            creator = it.get('creator') or it.get('author') or ''
            if creator:
                clues.append(f'By: {creator}.')
            for h in _headlines_for(text)[:1]:
                clues.append(f'Headline: "{h}"')
            _push(text, 'social', ' '.join(clues))

    return out[:_TOTAL_ITEM_CAP]


def _build_prompt(items: list[dict]) -> str:
    """Format the batch prompt to Claude. We give it every item with its
    context clues (news headlines, view deltas, related queries) and
    ask for a JSON map back so parsing is deterministic.

    The prompt is engineered to prevent the failure mode this pipeline
    used to have: falling back to a Wikipedia bio ("British actor born
    1993") as the caption. That describes WHO, not WHY. If the context
    clues don't reveal an actual event, Claude MUST return an empty
    string - the frontend then renders no caption at all, which is
    better than showing a bio.
    """
    header = (
        "You write ONE-LINE context captions for the following trending "
        "people, topics, movies, shows, songs, and news events. The "
        "goal is to help a dashboard reader instantly understand WHAT / "
        "WHO the item is AND (when possible) WHY it is trending right "
        "now.\n"
        "\n"
        "The `source` field tells you what kind of item it is:\n"
        "  - people / wikipedia -> a person\n"
        "  - search / mover     -> a search query (person, topic, event)\n"
        "  - film               -> a movie in theaters this week\n"
        "  - streaming          -> a title charting on a streamer\n"
        "  - music              -> a song ('Title by Artist')\n"
        "  - headline           -> a news article\n"
        "\n"
        "RULES (in priority order):\n"
        "1. If the clues include a concrete EVENT / RELEASE / news "
        "story, lead with that: what happened, who / what it "
        "involves, when if given. This is the ideal caption. Examples:\n"
        "     'Announced move to Philadelphia 76ers in free agency.'\n"
        "     'New Christopher Nolan epic opened in theaters this weekend.'\n"
        "     'Van drove into a crowd at Berlin Pride on July 25.'\n"
        "\n"
        "2. If the clues include a chart position / platform presence "
        "signal (e.g. 'Netflix Film #1', 'Charting on Spotify + Apple + "
        "Shazam') and NO event signal, describe the chart moment. "
        "Examples:\n"
        "     'Charting at #1 on Netflix and top-5 on Hulu.'\n"
        "     'New single climbing across Spotify, Apple Music, and Shazam.'\n"
        "\n"
        "3. If the clues include a Wikipedia bio / extract + a view or "
        "search spike, you MUST write a 'known-for' context caption. "
        "Do NOT return \"\" in this case - a bio-anchored caption is "
        "REQUIRED and is strictly better than empty for dashboard "
        "readers who need context. Lead with role, notable achievement, "
        "or a distinctive descriptor. Never lead with a date. Examples:\n"
        "     GOOD: 'NFL offensive coordinator known for engineering the Chiefs Super Bowl run.'\n"
        "     GOOD: 'Post-punk singer whose band influenced 1980s alternative rock.'\n"
        "     GOOD: 'HBO comedy writer known for creating hit workplace sitcoms.'\n"
        "     GOOD: 'Argentine soccer forward playing for Inter Miami alongside Messi.'\n"
        "     BAD:  'British actor born 1993.'                                    (leads with date)\n"
        "     BAD:  'Canadian-American filmmaker.'                                 (too vague)\n"
        "     BAD:  ''                                                             (empty when bio is available)\n"
        "\n"
        "4. For search queries or movers that are OBVIOUSLY an event "
        "phrase ('seattle shooting', 'appleton wisconsin tornado', "
        "'hurricane genevieve', 'xbox server status'), paraphrase the "
        "query into a plain-English event caption. Examples:\n"
        "     'seattle shooting'         -> 'Fatal shooting drew local news attention this week.'\n"
        "     'hurricane genevieve'      -> 'Named Pacific hurricane developing in the eastern Pacific.'\n"
        "     'xbox server status'       -> 'Xbox Live outage disrupting users this week.'\n"
        "     'appleton wisconsin tornado damage' -> 'Tornado damage assessment underway in Appleton, Wisconsin.'\n"
        "\n"
        "5. If the item is clearly NON-notable (obscure test query, "
        "random Finnish word, single Chinese character, personal name "
        "with zero signal anywhere), return \"\". Do NOT invent an event "
        "you cannot justify from the clues.\n"
        "\n"
        "6. HARD DON'TS across every caption:\n"
        "   - No dates ('born 1963', 'died 2020', 'January 15').\n"
        "   - No hedges ('possibly', 'likely', 'reportedly', 'may have').\n"
        "   - No 'or' constructions when the two clauses are guesses.\n"
        "   - No em dashes.\n"
        "   - No repeating the delta/rank number ('views spiked 67%').\n"
        "   - No 'Trending on X today' style tautologies.\n"
        "   - One sentence, 22 words or fewer.\n"
        "\n"
        "OUTPUT FORMAT: Return ONLY a JSON object, no prose. Keys = the "
        "input NAME strings exactly as given. Values = the caption, or "
        "\"\" if the item is non-notable per rule 5.\n"
        "\n"
        "Items:\n"
    )
    lines = []
    for i, it in enumerate(items, start=1):
        key = it['name']
        ctx = it['context'] or '(no context clues available)'
        lines.append(f'  {i}. name={key!r}  |  source={it["source"]}  |  clues={ctx}')
    return header + '\n'.join(lines) + '\n\nJSON output:'


def _extract_json_dict(text: str) -> dict:
    """Extract the first JSON object from `text` and return as dict.
    Returns {} on parse failure."""
    if not text:
        return {}
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _ask_claude(items: list[dict]) -> dict[str, str]:
    """Call Claude with the batch prompt, return {name: explanation}.
    Returns {} on any failure. Never raises."""
    if not items:
        return {}
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key:
        logger.warning("why_trending: ANTHROPIC_API_KEY not set; skipping")
        return {}
    try:
        import anthropic
    except ImportError as e:
        logger.warning("why_trending: anthropic SDK not installed: %s", e)
        return {}

    client  = anthropic.Anthropic(api_key=api_key)
    prompt  = _build_prompt(items)
    try:
        resp = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=_MAX_TOKENS,
            messages=[{'role': 'user', 'content': prompt}],
        )
    except Exception as e:
        logger.warning("why_trending: anthropic call failed: %s", e)
        return {}

    text = ''
    for block in resp.content or []:
        if getattr(block, 'text', ''):
            text += block.text
    parsed = _extract_json_dict(text)
    if not parsed:
        logger.warning("why_trending: could not parse JSON from Claude output")
        return {}

    # Re-key by _cp_normalize so the app's stamp step can look up by
    # the same key it uses for the cross-platform annotator.
    out: dict[str, str] = {}
    for raw_key, raw_val in parsed.items():
        norm = _cp_normalize(raw_key)
        val  = (raw_val or '').strip()
        if norm and val:
            out[norm] = val
    return out


# ---------------------------------------------------------------------------
# Per-item web_search caption (fallback for items the batch left empty)
# ---------------------------------------------------------------------------
_SOURCE_HINT = {
    'wikipedia': 'a person or topic',
    'people':    'a person in the news',
    'search':    'a search query',
    'mover':     'a rapidly-rising search query',
    'film':      'a movie in theaters this week',
    'streaming': 'a title charting on a streaming platform',
    'music':     'a song',
    'headline':  'a news article',
    'podcast':   'a podcast',
    'book':      'a book',
    'social':    'a social media topic',
}


def _build_websearch_prompt(name: str, source: str, context: str) -> str:
    """One-shot prompt: search the web, then return exactly one sentence
    explaining why the item is trending right now. Empty string if the
    search turns up nothing concrete.
    """
    kind = _SOURCE_HINT.get(source, 'an item')
    clue_block = ''
    if context:
        # Trim clues to ~500 chars so the prompt stays cheap.
        clue_block = f"\nExisting clues (may be thin or absent): {context[:500]}\n"
    return (
        f"You are writing a ONE-LINE 'why is this trending' caption for a "
        f"dashboard reader. The item is {kind}: {name!r}.\n"
        f"{clue_block}\n"
        f"STEP 1: Use the web_search tool AT LEAST ONCE with a query like "
        f"'{name} news' or '{name} trending' to find fresh coverage from the "
        f"last 1-7 days. Prefer results from major outlets (AP, Reuters, BBC, "
        f"NYT, Guardian, Variety, Deadline, ESPN, Billboard, NPR, CNN, Wall "
        f"Street Journal, LA Times, Washington Post).\n"
        f"\n"
        f"STEP 2: Write ONE sentence, 22 words or fewer, in the dashboard's "
        f"neutral first-person-plural voice. Lead with the EVENT / RELEASE / "
        f"news story (what happened, who / what it involves).\n"
        f"\n"
        f"If web_search returns NO fresh event but you can name a "
        f"distinctive known-for descriptor (their band, role, sport, book, "
        f"franchise), write a 'known-for' caption instead. Example: "
        f"'Post-punk singer whose band influenced 1980s alternative rock.'\n"
        f"\n"
        f"HARD DON'TS:\n"
        f"- No dates ('born 1963', 'died 2020', 'January 15', 'this weekend').\n"
        f"- No hedges ('possibly', 'likely', 'reportedly', 'may have').\n"
        f"- No em dashes (use commas, colons, or periods).\n"
        f"- No 'trending on X today' tautologies.\n"
        f"- No preamble, no quotes around the sentence, no citation markers.\n"
        f"- If both search AND known-for come up empty, output exactly: EMPTY\n"
        f"\n"
        f"Output ONLY the one sentence (or the literal word EMPTY). "
        f"No JSON, no explanation, no headers."
    )


def _extract_websearch_caption(resp_content: list) -> str:
    """Pull the final text block from a Claude web_search response and
    return a cleaned single-line caption. Empty string on any of:
    - Claude returned the literal EMPTY marker
    - the sentence starts with the trending name (tautology)
    - the sentence contains an em dash (violates rule)
    - length > 240 chars (obvious multi-sentence answer)
    """
    text = ''
    for block in resp_content or []:
        if getattr(block, 'type', '') == 'text':
            text += getattr(block, 'text', '') or ''
    text = (text or '').strip()
    if not text:
        return ''
    text = text.replace('\r', ' ').replace('\n', ' ').strip()
    while '  ' in text:
        text = text.replace('  ', ' ')
    # Strip common surround chars
    text = text.strip('"').strip("'").strip()
    # If Claude wrapped in a quote-mark JSON style value, unwrap once more
    if text.startswith('"') and text.endswith('"') and len(text) > 2:
        text = text[1:-1].strip()
    if not text or text.upper() == 'EMPTY':
        return ''
    if len(text) > 240:
        return ''
    if '\u2014' in text:  # em dash
        return ''
    return text


def _websearch_one(name: str, source: str, context: str,
                    anthropic_client) -> tuple[str, str]:
    """Run one web_search Claude call and return (name, caption)."""
    prompt = _build_websearch_prompt(name, source, context)
    try:
        resp = anthropic_client.messages.create(
            model=_WEBSEARCH_MODEL,
            max_tokens=_WEBSEARCH_MAX_TOKENS,
            tools=[{
                'type':     'web_search_20250305',
                'name':     'web_search',
                'max_uses': _WEBSEARCH_MAX_USES,
            }],
            messages=[{'role': 'user', 'content': prompt}],
            timeout=_WEBSEARCH_TIMEOUT_S,
        )
    except Exception as e:
        logger.info("why_trending web_search %r: %s", name, e)
        return name, ''
    return name, _extract_websearch_caption(resp.content or [])


def _websearch_fill_missing(items: list[dict],
                             existing: dict[str, str]) -> dict[str, str]:
    """For every input item where `existing[cp_normalize(name)]` is
    missing / empty, run a per-item web_search Claude call in parallel
    and merge the results into `existing`. Returns the merged dict.

    Cap: _WEBSEARCH_MAX_ITEMS names per run so runaway snapshots
    don't blow the daily budget. Order preserved from `items` so the
    top of the fused feed is covered first.
    """
    if not items:
        return existing
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key:
        logger.warning("why_trending: skip web_search fill (no API key)")
        return existing
    try:
        import anthropic
    except ImportError:
        logger.warning("why_trending: skip web_search fill (SDK missing)")
        return existing

    # Which items are still missing a caption?
    missing: list[dict] = []
    for it in items:
        key = _cp_normalize(it['name'])
        if not key:
            continue
        if not (existing.get(key) or '').strip():
            missing.append(it)
        if len(missing) >= _WEBSEARCH_MAX_ITEMS:
            break
    if not missing:
        logger.info("why_trending: web_search fill skipped (0 missing)")
        return existing

    logger.info("why_trending: web_search filling %d missing captions "
                "with %s (concurrency=%d)",
                len(missing), _WEBSEARCH_MODEL, _WEBSEARCH_CONCURRENCY)

    client = anthropic.Anthropic(api_key=api_key)
    filled = 0
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=_WEBSEARCH_CONCURRENCY) as ex:
        futs = {
            ex.submit(_websearch_one, it['name'], it['source'],
                       it.get('context', ''), client): it['name']
            for it in missing
        }
        for fut in concurrent.futures.as_completed(futs):
            try:
                name, caption = fut.result(timeout=_WEBSEARCH_TIMEOUT_S + 15)
            except Exception as e:
                logger.info("why_trending web_search worker: %s", e)
                continue
            caption = (caption or '').strip()
            if not caption:
                continue
            key = _cp_normalize(name)
            if key:
                existing[key] = caption
                filled += 1

    logger.info("why_trending: web_search filled %d/%d missing captions",
                filled, len(missing))
    return existing


def fetch() -> dict[str, Any]:
    items = _collect_items()
    if not items:
        return {
            'items': {},
            'count': 0,
            'error': 'no upstream snapshots available',
        }
    # Pass 1: cheap batch prompt on Haiku, keyed off local clues
    # (headlines, chart labels, bios). Covers the majority of items.
    explanations = _ask_claude(items)
    # Pass 2: for anything the batch left empty, ask a Sonnet 4.5
    # agent to do a real web_search and write a caption grounded in
    # fresh news. This is what turns bare mover terms and "no local
    # signal" people rows into actionable WHY captions.
    explanations = _websearch_fill_missing(items, explanations)
    return {
        'items':  explanations,
        'count':  len(explanations),
        'inputs': [{'name': it['name'], 'source': it['source']} for it in items],
        'model':  _CLAUDE_MODEL,
        'websearch_model': _WEBSEARCH_MODEL,
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    from ._base import run_scraper
    result = run_scraper('why_trending', 'Why is this trending?', 'meta', fetch)
    print(f"why_trending: count={result.get('count')} error={result.get('error')}",
           file=sys.stderr)
    for k, v in list((result.get('items') or {}).items())[:8]:
        print(f"  {k}: {v}", file=sys.stderr)
