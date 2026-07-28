"""
"Why is this trending?" one-line context generator.

Runs once per day in the scraper cron. Reads the top trending items
from the other snapshots we've already collected (Wikipedia, GDELT
people, Google Trends, headlines), packs them with whatever context
we have, and asks Claude to produce a single-sentence explanation
for each in a batch.

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
_MAX_MOVER_ITEMS     = 20
_MAX_FILM_ITEMS      = 15
_MAX_STREAMING_ITEMS = 15
_MAX_MUSIC_ITEMS     = 15
_MAX_HEADLINE_ITEMS  = 15
_TOTAL_ITEM_CAP      = 120

# Single-sentence explanations don't need Opus. Match the model naming
# convention the rest of the workspace uses (claude_client.py defaults
# to claude-sonnet-4-5); haiku-4-5 is the cheap fast tier from the
# same family. Overridable via WHY_TRENDING_MODEL env var.
_CLAUDE_MODEL = os.environ.get('WHY_TRENDING_MODEL') or 'claude-haiku-4-5'
# Enough to fit 120 items of context-rich prompt (~200 tokens each) +
# 120 responses (~30 tokens each). Empirical cap on haiku is 8k output.
_MAX_TOKENS   = 8000


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

    # Dedupe (a name may appear in multiple sources) and cap at 30 so
    # a full serial pass fits inside a ~3 min budget at 5.5s/query.
    _seen_miss: set[str] = set()
    _uniq_miss: list[str] = []
    for n in _misses:
        k = _cp_normalize(n)
        if k in _seen_miss:
            continue
        _seen_miss.add(k)
        _uniq_miss.append(n)
    _uniq_miss = _uniq_miss[:30]
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
            # Truncate to first sentence-ish so we don't blow the token
            # budget on 60 bios.
            first = extract.split('. ')[0]
            clues.append(f'Wikipedia bio (context only, do NOT quote): "{first}."')

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
    # Movers snapshots are stored under `movers.json` (built by
    # movers_from_history.py). Breakout + rising bucket items are the
    # ones the fusion score weighs highest, so they deserve WHY captions.
    # -----------------------------------------------------------------
    movers_snap = _read_snapshot('movers')
    mover_terms: list[dict] = []
    if movers_snap:
        for bucket_key in ('breakout', 'rising'):
            for row in (movers_snap.get(bucket_key) or []):
                term = (row.get('term') or '').strip()
                if term:
                    row['_bucket'] = bucket_key
                    mover_terms.append(row)
    mover_terms = _dedupe_titles_across_sources(mover_terms, 'term', _MAX_MOVER_ITEMS)
    for row in mover_terms:
        term   = (row.get('term') or '').strip()
        bucket = row.get('_bucket') or 'mover'
        pct    = row.get('mentions_change_pct') or row.get('delta_pct')
        clues: list[str] = []
        if bucket == 'breakout':
            clues.append('Breakout query: 5x+ session growth off a low baseline this window.')
        else:
            if isinstance(pct, (int, float)):
                clues.append(f'Rising query: session count up {int(round(pct * 100)):+d}% vs baseline.')
            else:
                clues.append('Rising query: sustained 25%+ growth in panel sessions.')
        # Pull associated headlines (local pool + GDELT fallback) so
        # Claude has the actual event to anchor on.
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
    # Music (tracks). Feed the artist as extra context so Claude can
    # anchor on "new album released", "featured in a viral TikTok",
    # etc. - the reasons a specific track climbs the charts.
    # -----------------------------------------------------------------
    music_snap = _read_snapshot('music_charts') or _read_snapshot('music')
    music_tracks = _flatten_music_tracks(music_snap)[:_MAX_MUSIC_ITEMS]
    for m in music_tracks:
        title  = m.get('title')  or ''
        artist = m.get('artist') or ''
        labels = m.get('chart_labels') or []
        display = f'{title} by {artist}' if artist else title
        clues: list[str] = []
        if labels:
            clues.append('Charting on: ' + ', '.join(labels[:5]) + '.')
        if artist:
            clues.append(f'Artist: {artist}.')
        for h in _headlines_for(display)[:2]:
            clues.append(f'Headline: "{h}"')
        _push(display, 'music', ' '.join(clues))

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
        "You write ONE-LINE explanations of WHY the following people, "
        "topics, movies, shows, songs, or news events are TRENDING "
        "RIGHT NOW.\n"
        "\n"
        "The `source` field on each item tells you what kind of item it "
        "is:\n"
        "  - people / wikipedia -> a person (real human)\n"
        "  - search / mover     -> a search query (person, topic, or event)\n"
        "  - film               -> a movie in theaters this week\n"
        "  - streaming          -> a title (film or tv) charting on a streamer\n"
        "  - music              -> a song ('Title by Artist' in the name field)\n"
        "  - headline           -> a specific news article\n"
        "\n"
        "STRICT RULES:\n"
        "1. Answer WHY (the concrete event, release, cultural moment, "
        "or news story driving the trend right now). NEVER answer WHO "
        "or WHAT alone (biography, plot summary, genre, occupation).\n"
        "2. You MUST anchor every answer to a concrete signal in the "
        "clues: (a) a news headline quoted in the clues, (b) a related "
        "search query, (c) a chart position / platform presence ('Top "
        "on Netflix + Hulu' is a valid signal for a streaming title), "
        "(d) an event described in an extract.\n"
        "3. If the clues contain ONLY a bio line and NO event / "
        "headline / chart / release signal, return an empty string "
        "\"\". Do NOT guess. Do NOT extrapolate from a bio (\"probably "
        "in a new film\", \"likely upcoming match\") - that is "
        "HALLUCINATION and is banned.\n"
        "4. If a clue explicitly says 'do NOT quote' or 'context only', "
        "treat it as background only. Never paraphrase it as the answer.\n"
        "5. You MAY cross-reference other items in this batch. If item "
        "A's clues mention item B's name, the connection is a fair "
        "signal to use in either explanation.\n"
        "6. One sentence, present tense, 20 words or fewer. Lead with "
        "the event / release / connection, not the item name. Avoid "
        "the words 'or', 'possibly', 'likely', 'reportedly' - those "
        "are hedges that indicate you're guessing. If you'd need a "
        "hedge, return \"\".\n"
        "7. Do not include em dashes.\n"
        "\n"
        "EXAMPLES:\n"
        "  GOOD (person):     \"Van driven into crowd at Berlin Pride on July 25.\"\n"
        "  GOOD (person):     \"Directing upcoming Star Wars: Starfighter film announced this week.\"\n"
        "  GOOD (film):       \"Christopher Nolan's new epic opened in theaters this weekend.\"\n"
        "  GOOD (streaming):  \"New season dropped on Netflix, charting in the top 3 of both Netflix and Hulu.\"\n"
        "  GOOD (music):      \"Lead single from Post Malone's new country album, up across Spotify and Shazam.\"\n"
        "  GOOD (mover):      \"Overnight breakout after live-broadcast incident during the game.\"\n"
        "  GOOD (headline):   \"Federal Reserve signals rate cut at September meeting per new minutes.\"\n"
        "  BAD:               \"Canadian-American filmmaker and actor.\"                     (bio)\n"
        "  BAD:               \"British actor born 1993.\"                                    (bio)\n"
        "  BAD:               \"Appeared in a recently released film generating interest.\"   (hallucination)\n"
        "  BAD:               \"Announced or completed significant match or career decision.\" (hedge)\n"
        "  BAD:               \"Wikipedia views spiked +67% yesterday.\"                      (restates the delta)\n"
        "  BAD:               \"Trending on Wikipedia today.\"                                (says nothing)\n"
        "\n"
        "OUTPUT FORMAT: Return ONLY a JSON object, no prose. Keys = the "
        "input NAME strings exactly as given. Values = the one-sentence "
        "explanation, or \"\" if no anchoring signal is present.\n"
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


def fetch() -> dict[str, Any]:
    items = _collect_items()
    if not items:
        return {
            'items': {},
            'count': 0,
            'error': 'no upstream snapshots available',
        }
    explanations = _ask_claude(items)
    return {
        'items':  explanations,
        'count':  len(explanations),
        'inputs': [{'name': it['name'], 'source': it['source']} for it in items],
        'model':  _CLAUDE_MODEL,
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
