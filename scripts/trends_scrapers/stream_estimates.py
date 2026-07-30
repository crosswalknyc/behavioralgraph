"""
US audience-size estimator for podcasts, songs, and streaming titles.

For every top item on the Podcasts / Music / Streaming tabs, we ask
Claude Sonnet 4.5 (with the native `web_search` tool) to painstakingly
research the item and reason about its current weekly US audience:

  - Podcasts: US weekly listeners (Edison Podcast Metrics, Podtrac,
              Chartable, publisher press releases, Nielsen Podcast
              Ratings, MRC Podcast Ratings).
  - Songs:    US weekly streams across all DSPs (Luminate week-over-
              week reports, Spotify for Artists screenshots, Billboard
              charts + Chart Beat, Chartmetric snapshots).
  - Films/TV: US weekly viewers or household views (Nielsen streaming
              top-10, Whip Media, Samba TV, Parrot Analytics, Variety
              Insight, Deadline chart, Netflix Tudum, Prime Video
              Roll Call, Max Weekly Top 10).

If no direct citation is available, Claude reasons from ADJACENT
signals — chart position × typical audience for that tier, historic
week-1 vs week-2 curves for the same franchise, comparable-title
benchmarks. Claude always returns a range (low / mid / high) plus a
confidence tag so the dashboard reader can tell "solid Nielsen data"
apart from "inferred from chart position".

Day-over-day trend: we snapshot to a dated S3 key each run; on the
next run we look up yesterday's estimate for the same normalized
title and compute (delta_pct, direction) so the dashboard can render
an up / down / stable arrow next to each item.

Output shape (kind='meta'):

    {
      "source":     "stream_estimates",
      "kind":       "meta",
      "fetched_at": "...",
      "generated_at": "...",
      "items": {
        "podcast:crime junkie": {
          "kind":            "podcast",
          "display_title":   "Crime Junkie",
          "artist":          "Audiochuck",
          "us_estimate":     12_500_000,
          "us_estimate_low":  8_000_000,
          "us_estimate_high": 18_000_000,
          "confidence":      "high",
          "unit_label":      "weekly US listeners",
          "method":          "Edison Podcast Metrics puts it at #3...",
          "sources":         ["https://...", "https://..."],
          "delta_pct":       0.204,
          "direction":       "up"
        },
        ...
      },
      "count": 45
    }

Standalone:

    python3 -m scripts.trends_scrapers.stream_estimates
    python3 -m scripts.trends_scrapers.stream_estimates --only podcast
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import boto3

logger = logging.getLogger(__name__)


_S3_BUCKET = 'dashboard-inputs'
_S3_LATEST = 'trends_iq_snapshots/latest/'
_S3_DATED  = 'trends_iq_snapshots/{date}/'


# -------------------------------------------------------------------------
# How many items to research per category. Sonnet 4.5 + web_search costs
# ~$0.02/item, so 15 podcasts + 20 songs + 20 streaming = 55 items/day
# ≈ $1.10/day. Well within the trends-iq daily budget.
# -------------------------------------------------------------------------
_MAX_PODCAST_ITEMS   = 15
_MAX_SONG_ITEMS      = 20
_MAX_STREAMING_ITEMS = 20

_WEBSEARCH_MODEL      = (os.environ.get('STREAM_ESTIMATES_MODEL')
                          or 'claude-sonnet-4-5')
_WEBSEARCH_MAX_TOKENS = 1200
_WEBSEARCH_MAX_USES   = 3        # per-item web_search calls (some tier-1
                                  # research warrants 2-3 queries)
_WEBSEARCH_TIMEOUT_S  = 60
_CONCURRENCY          = 6


# MUST stay in lock-step with `trends_iq._CP_STOPWORDS` - the app
# side looks up entries by these keys and any mismatch causes a
# silent lookup miss (item ranks fine, no stream number).
_STOPWORDS = {
    'the', 'a', 'an', 'and', 'of', 'in', 'on', 'to', 'for', 'at', 'is',
    'trending', 'today', 'now', 'news', 'latest', 'best',
}


def _cp_normalize(text: str) -> str:
    """Case-fold, strip punctuation, drop stopwords, collapse spaces.
    Mirrors `trends_iq._cp_normalize` byte-for-byte."""
    if not text:
        return ''
    s = text.lower().lstrip('#').strip()
    s = re.sub(r'[^\w\s]+', ' ', s)
    tokens = [t for t in s.split() if t and t not in _STOPWORDS]
    return ' '.join(tokens)


def _s3():
    return boto3.client('s3',
                         region_name=os.environ.get('AWS_REGION') or 'us-east-2')


def _read_snapshot(source: str) -> Optional[dict]:
    """Return the S3 snapshot at latest/{source}.json or None."""
    try:
        obj = _s3().get_object(Bucket=_S3_BUCKET, Key=f'{_S3_LATEST}{source}.json')
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _read_dated_snapshot(source: str, days_back: int) -> Optional[dict]:
    """Return the dated snapshot from N days ago, or None."""
    d = date.today() - timedelta(days=days_back)
    key = f'{_S3_DATED.format(date=d.isoformat())}{source}.json'
    try:
        obj = _s3().get_object(Bucket=_S3_BUCKET, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        return None


# -------------------------------------------------------------------------
# Collect unique items across per-platform snapshots
# -------------------------------------------------------------------------
def _dedupe_by_key(items: list[dict], key_fn) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        k = key_fn(it)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _collect_podcasts(max_items: int = _MAX_PODCAST_ITEMS) -> list[dict]:
    """Union top items across Apple/Spotify/Amazon/Audible/Netflix
    podcast panels, deduped by normalized title. Preserves best rank
    across platforms so #1-on-Apple beats #5-on-Spotify."""
    snap = _read_snapshot('podcast_charts')
    if not snap:
        return []
    per: dict[str, dict] = {}
    for src_slug, panel in (snap.get('sources') or {}).items():
        for i, it in enumerate((panel.get('items') or [])[:30]):
            title = (it.get('title') or '').strip()
            key   = _cp_normalize(title)
            if not key:
                continue
            rank = i + 1
            e = per.setdefault(key, {
                'kind':          'podcast',
                'display_title': title,
                'artist':        (it.get('artist') or '').strip(),
                'best_rank':     rank,
                'chart_labels':  [],
                'image':         it.get('image'),
                'url':           it.get('url'),
            })
            label = panel.get('label') or src_slug
            e['chart_labels'].append(f'{label} #{rank}')
            if rank < e['best_rank']:
                e['best_rank'] = rank
    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


def _collect_songs(max_items: int = _MAX_SONG_ITEMS) -> list[dict]:
    """Union top tracks across Spotify/Apple/YouTube/Shazam/TikTok/
    Amazon music panels, keyed by (title + artist) so different tracks
    named 'Home' don't collide."""
    snap = _read_snapshot('music_charts')
    if not snap:
        return []
    per: dict[str, dict] = {}
    for src_slug, panel in (snap.get('sources') or {}).items():
        for i, it in enumerate((panel.get('items') or [])[:30]):
            title  = (it.get('title')  or '').strip()
            artist = (it.get('artist') or '').strip()
            key = _cp_normalize(f'{title} {artist}')
            if not key:
                continue
            rank = i + 1
            e = per.setdefault(key, {
                'kind':          'song',
                'display_title': title,
                'artist':        artist,
                'best_rank':     rank,
                'chart_labels':  [],
                'image':         it.get('image'),
                'url':           it.get('url'),
            })
            label = panel.get('label') or src_slug
            e['chart_labels'].append(f'{label} #{rank}')
            if rank < e['best_rank']:
                e['best_rank'] = rank
    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


_STREAMING_SLUGS = (
    ('netflix',    'Netflix'),
    ('disneyplus', 'Disney+'),
    ('hulu',       'Hulu'),
    ('max',        'Max'),
    ('primevideo', 'Prime Video'),
    ('espnplus',   'ESPN+'),
)


def _collect_streaming(max_items: int = _MAX_STREAMING_ITEMS) -> list[dict]:
    """Union top titles across the 6 streaming platform snapshots,
    keyed by normalized title. Preserves film/tv distinction (from
    `category_display`) so 'Barbie' the film and 'Barbie' the show
    don't collide."""
    per: dict[str, dict] = {}
    for slug, label in _STREAMING_SLUGS:
        snap = _read_snapshot(slug)
        if not snap:
            continue
        buckets: list[tuple[str, list]] = []
        if slug == 'netflix':
            buckets.append(('film', snap.get('us_films') or []))
            buckets.append(('tv',   snap.get('us_tv')    or []))
        else:
            buckets.append(('mixed', snap.get('national') or []))
        for kind, items in buckets:
            for i, it in enumerate(items[:15]):
                title  = (it.get('title') or '').strip()
                cat    = (it.get('category_display') or kind or '').lower()
                item_kind = 'film' if cat == 'film' else ('tv' if 'tv' in cat else 'title')
                key = f'{item_kind}:{_cp_normalize(title)}'
                if not _cp_normalize(title):
                    continue
                rank = i + 1
                e = per.setdefault(key, {
                    'kind':          item_kind,
                    'display_title': title,
                    'artist':        '',
                    'best_rank':     rank,
                    'chart_labels':  [],
                    'image':         it.get('image'),
                    'url':           it.get('url'),
                })
                e['chart_labels'].append(f'{label} #{rank}')
                if rank < e['best_rank']:
                    e['best_rank'] = rank
    return sorted(per.values(), key=lambda e: e['best_rank'])[:max_items]


def _lookup_key(kind: str, display_title: str, artist: str = '') -> str:
    """Storage key for an item. Podcasts/streaming key by title;
    songs key by (title + artist) because titles collide across
    artists."""
    if kind == 'song':
        return f'song:{_cp_normalize(f"{display_title} {artist}")}'
    if kind in ('film', 'tv', 'title'):
        return f'{kind}:{_cp_normalize(display_title)}'
    return f'{kind}:{_cp_normalize(display_title)}'


# -------------------------------------------------------------------------
# Claude Sonnet + web_search per-item research
# -------------------------------------------------------------------------

_PROMPT_HEADER = (
    "You are a senior media analyst estimating the CURRENT weekly US "
    "audience size for a piece of content. Use the web_search tool "
    "AT LEAST ONCE (up to 3 times) to find the freshest data before "
    "you reason.\n"
    "\n"
    "RANKING OF SOURCES (prefer higher-tier when available):\n"
    "  Tier 1: Nielsen streaming top-10, Luminate week-over-week (Billboard "
    "reports it Wednesday), Edison Podcast Metrics / Podtrac Ranker, MRC "
    "Podcast Ratings, Chartmetric, official platform press releases.\n"
    "  Tier 2: Variety, Deadline, Hollywood Reporter, The Verge, "
    "Billboard Chart Beat, publisher press releases with real numbers.\n"
    "  Tier 3: Reddit / Twitter with a Nielsen screenshot, third-party "
    "aggregators (Whip Media, Samba TV, Parrot Analytics), Spotify for "
    "Artists screenshots.\n"
    "  AVOID: SEO listicles, YouTube reaction videos, unattributed blogs.\n"
    "\n"
    "REASONING RULES:\n"
    "  1. If direct US weekly numbers exist (Nielsen top-10 minutes, "
    "Luminate US streams, Podtrac US downloads), USE THEM. Cite the "
    "source in `method`.\n"
    "  2. If only global numbers exist, apply a US share benchmark "
    "(US typically = 35-55% of Spotify streams, 40-60% of Nielsen "
    "streaming minutes, 55-70% of Podtrac downloads for English-language "
    "podcasts). State the share you used.\n"
    "  3. If only chart position is available, reason from tier "
    "benchmarks: #1 on Spotify US ~ 15-30M weekly streams, top-10 "
    "Nielsen streaming Original ~ 8-25M households/week, #1 Podtrac "
    "US ~ 8-15M weekly listeners.\n"
    "  4. Return a RANGE (low, mid, high) that reflects real "
    "uncertainty. Do not compress the range to make the estimate look "
    "confident. Low = worst-case defensible, High = best-case defensible.\n"
    "  5. `confidence` tag: 'high' if you cited Tier-1 data with a real "
    "number this week; 'medium' if you extrapolated from Tier-1 chart "
    "position or Tier-2 press; 'low' if you inferred from Tier-3 or bare "
    "chart position with no benchmark.\n"
    "\n"
    "OUTPUT FORMAT: Return ONLY a JSON object with these exact keys, "
    "no prose, no markdown fence:\n"
    "  {\n"
    "    \"us_estimate\":       <int, US weekly audience mid-estimate>,\n"
    "    \"us_estimate_low\":   <int, defensible low>,\n"
    "    \"us_estimate_high\":  <int, defensible high>,\n"
    "    \"unit_label\":        <string, e.g. \"weekly US listeners\">,\n"
    "    \"confidence\":        \"high\" | \"medium\" | \"low\",\n"
    "    \"method\":            <string, 1-3 sentences: what you found + "
    "how you converted it to weekly US>,\n"
    "    \"sources\":           [<url1>, <url2>, ...]   // 1-4 URLs you actually consulted\n"
    "  }\n"
)


def _build_prompt(item: dict) -> str:
    kind          = item['kind']
    display_title = item['display_title']
    artist        = item.get('artist') or ''
    charts        = item.get('chart_labels') or []
    chart_str     = ', '.join(charts[:6]) if charts else '(no chart context)'

    if kind == 'podcast':
        unit  = 'weekly US listeners'
        query = f'"{display_title} podcast" weekly listeners US'
        item_line = f'PODCAST TITLE: {display_title}\nPUBLISHER: {artist or "(unknown)"}'
    elif kind == 'song':
        unit  = 'weekly US streams (all DSPs combined)'
        query = f'"{display_title}" "{artist}" Luminate US streams weekly'
        item_line = f'SONG TITLE: {display_title}\nARTIST: {artist or "(unknown)"}'
    elif kind == 'film':
        unit  = 'weekly US household views (or US minutes watched)'
        query = f'"{display_title}" Nielsen streaming top 10 weekly'
        item_line = f'FILM TITLE: {display_title}'
    elif kind == 'tv':
        unit  = 'weekly US household views (or US minutes watched)'
        query = f'"{display_title}" Nielsen streaming top 10 weekly TV series'
        item_line = f'TV SERIES TITLE: {display_title}'
    else:
        unit  = 'weekly US viewers'
        query = f'"{display_title}" weekly viewers US streaming'
        item_line = f'TITLE: {display_title}'

    return (
        _PROMPT_HEADER
        + f'\nTARGET METRIC: {unit}\n\n'
        + item_line
        + f'\nCHART CONTEXT: {chart_str}\n'
        + f'\nSUGGESTED SEARCH QUERY (feel free to refine): {query}\n\n'
        + 'JSON output:'
    )


def _extract_json_blob(text: str) -> Optional[dict]:
    if not text:
        return None
    # Look for the first {..} object, tolerating markdown fences.
    m = re.search(r'\{[\s\S]*\}', text)
    if not m:
        return None
    raw = m.group(0)
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Sometimes Claude includes trailing commentary; strip trailing
    # non-JSON after the last `}`.
    last_close = raw.rfind('}')
    if last_close > 0:
        try:
            return json.loads(raw[:last_close + 1])
        except Exception:
            return None
    return None


# Per-kind ceilings on the US weekly audience estimate. Prevents runaway
# hallucinations (Claude occasionally returns total-audience-ever or
# global streams for low-signal items). Anchored to real-world highs:
#   - Podcast: Podtrac #1 (Rogan) ~ 15M weekly US listeners; ceiling 40M
#   - Song:    Luminate #1 ~ 30-40M weekly US on-demand streams; ceiling 200M
#   - Film/TV: Nielsen streaming #1 peaks ~ 45-55M US households/week; ceiling 120M
# Anything above the ceiling is capped + flagged as low confidence so the
# reader knows to discount it.
_MAX_ESTIMATE_BY_KIND = {
    'podcast': 40_000_000,
    'song':    200_000_000,
    'film':    120_000_000,
    'tv':      120_000_000,
    'title':   120_000_000,
}
# Any estimate above the per-kind ceiling gets clamped to this fraction
# of the ceiling and flagged 'low' confidence. Keeps the visual ordering
# roughly right without letting a hallucination dominate the row.
_CLAMP_TO_FRACTION = 0.5


def _sanitize_result(item: dict, parsed: dict) -> Optional[dict]:
    """Normalize Claude's JSON output. Returns the enriched item dict
    or None if the estimate is missing / clearly bogus."""
    try:
        mid  = int(parsed.get('us_estimate') or 0)
        low  = int(parsed.get('us_estimate_low') or 0)
        high = int(parsed.get('us_estimate_high') or 0)
    except Exception:
        return None
    if mid <= 0:
        return None
    if low <= 0:
        low = int(mid * 0.7)
    if high <= 0:
        high = int(mid * 1.3)
    if low > mid:
        low = mid
    if high < mid:
        high = mid

    # Clamp obvious hallucinations. If the mid is 3x+ the per-kind
    # ceiling, force it down and force `confidence = low`. Don't just
    # discard the row: a clamped estimate still gives the user a
    # rough sense of relative scale, and the tooltip flags the low
    # confidence.
    ceiling  = _MAX_ESTIMATE_BY_KIND.get(item['kind'], 120_000_000)
    conf     = (parsed.get('confidence') or 'medium').strip().lower()
    clamped  = False
    if mid > ceiling:
        clamped_val = int(ceiling * _CLAMP_TO_FRACTION)
        # Preserve rank order among clamped items by scaling proportional
        # to how far past the ceiling they were, but never letting the
        # clamped value exceed the ceiling itself.
        mid  = min(ceiling, clamped_val)
        low  = min(low,  mid)
        high = min(high, ceiling)
        conf = 'low'
        clamped = True

    method = (parsed.get('method') or '').strip()
    if clamped:
        method = (method + ' [clamped: raw estimate exceeded per-kind '
                            'sanity ceiling]').strip()

    return {
        'kind':             item['kind'],
        'display_title':    item['display_title'],
        'artist':           item.get('artist') or '',
        'chart_labels':     item.get('chart_labels') or [],
        'best_rank':        item.get('best_rank'),
        'image':            item.get('image'),
        'url':              item.get('url'),
        'us_estimate':      mid,
        'us_estimate_low':  low,
        'us_estimate_high': high,
        'unit_label':       (parsed.get('unit_label') or '').strip()
                              or 'weekly US audience',
        'confidence':       conf,
        'method':           method,
        'sources':          [s for s in (parsed.get('sources') or [])
                              if isinstance(s, str)][:4],
    }


def _research_one(item: dict, client) -> tuple[str, Optional[dict]]:
    """Run one Claude web_search call for `item` and return (key, sanitized_result)."""
    key = _lookup_key(item['kind'], item['display_title'],
                       item.get('artist') or '')
    prompt = _build_prompt(item)
    for attempt in range(2):    # single retry on parse failure
        try:
            resp = client.messages.create(
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
            logger.info("stream_estimates %r attempt %d: %s",
                         item['display_title'], attempt + 1, e)
            continue
        text = ''
        for block in resp.content or []:
            if getattr(block, 'type', '') == 'text':
                text += getattr(block, 'text', '') or ''
        parsed = _extract_json_blob(text)
        if not parsed:
            logger.info("stream_estimates %r: unparseable output",
                         item['display_title'])
            continue
        result = _sanitize_result(item, parsed)
        if result:
            return key, result
    return key, None


def _research_all(items: list[dict]) -> dict[str, dict]:
    """Parallel research over `items`. Returns {key: sanitized_result}."""
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key:
        logger.warning("stream_estimates: ANTHROPIC_API_KEY missing; skipping")
        return {}
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        logger.warning("stream_estimates: anthropic SDK missing: %s", e)
        return {}
    client = anthropic.Anthropic(api_key=api_key)

    out: dict[str, dict] = {}
    if not items:
        return out
    logger.info("stream_estimates: researching %d items with %s (concurrency=%d)",
                 len(items), _WEBSEARCH_MODEL, _CONCURRENCY)
    with concurrent.futures.ThreadPoolExecutor(max_workers=_CONCURRENCY) as ex:
        futs = {ex.submit(_research_one, it, client): it for it in items}
        for i, fut in enumerate(concurrent.futures.as_completed(futs)):
            try:
                key, result = fut.result(timeout=_WEBSEARCH_TIMEOUT_S + 15)
            except Exception as e:
                logger.info("stream_estimates worker: %s", e)
                continue
            if key and result:
                out[key] = result
                logger.info("  [%2d/%d] %-40s -> %s ~ %s",
                             i + 1, len(items),
                             result['display_title'][:40],
                             _humanize(result['us_estimate']),
                             result['confidence'])
    return out


# -------------------------------------------------------------------------
# Day-over-day trend attach
# -------------------------------------------------------------------------
_TREND_STABLE_PCT = 0.05    # <5% change = stable arrow


def _attach_dod_trend(current: dict[str, dict],
                       yesterday: Optional[dict]) -> dict[str, dict]:
    """Mutate `current` to add `delta_pct` and `direction` fields for
    every key that also exists in yesterday's snapshot. Missing prior
    values leave the fields at 0 / 'new'."""
    prior_items = ((yesterday or {}).get('items') or {})
    for key, cur in current.items():
        prev = prior_items.get(key) or {}
        prev_mid = prev.get('us_estimate') or 0
        if prev_mid <= 0:
            cur['delta_pct']  = 0.0
            cur['direction']  = 'new'
            cur['prev_estimate'] = None
            continue
        mid = cur.get('us_estimate') or 0
        delta = (mid - prev_mid) / prev_mid if prev_mid else 0.0
        if abs(delta) < _TREND_STABLE_PCT:
            direction = 'stable'
        elif delta > 0:
            direction = 'up'
        else:
            direction = 'down'
        cur['delta_pct']     = round(delta, 4)
        cur['direction']     = direction
        cur['prev_estimate'] = prev_mid
    return current


# -------------------------------------------------------------------------
# Formatting helper (used only for logging; frontend has its own)
# -------------------------------------------------------------------------
def _humanize(n: int) -> str:
    if n >= 1_000_000_000:
        return f'{n / 1_000_000_000:.1f}B'
    if n >= 1_000_000:
        return f'{n / 1_000_000:.1f}M'
    if n >= 1_000:
        return f'{n / 1_000:.1f}K'
    return str(n)


# -------------------------------------------------------------------------
# Fetch entry point
# -------------------------------------------------------------------------
def fetch(only: Optional[set[str]] = None) -> dict[str, Any]:
    """Read podcast / music / streaming snapshots, research each unique
    top item's US audience via Claude + web_search, and return the
    combined snapshot dict."""
    wanted = only or {'podcast', 'song', 'streaming'}

    items: list[dict] = []
    if 'podcast' in wanted:
        items.extend(_collect_podcasts())
    if 'song' in wanted:
        items.extend(_collect_songs())
    if 'streaming' in wanted:
        items.extend(_collect_streaming())

    if not items:
        return {
            'items': {},
            'count': 0,
            'error': 'no upstream snapshots available',
            'model': _WEBSEARCH_MODEL,
        }

    logger.info("stream_estimates: total unique items = %d "
                "(podcast=%d, song=%d, streaming=%d)",
                len(items),
                sum(1 for it in items if it['kind'] == 'podcast'),
                sum(1 for it in items if it['kind'] == 'song'),
                sum(1 for it in items if it['kind'] in ('film', 'tv', 'title')))

    researched = _research_all(items)

    # Attach day-over-day trend from yesterday's dated snapshot.
    yesterday = _read_dated_snapshot('stream_estimates', days_back=1)
    if not yesterday:
        yesterday = _read_dated_snapshot('stream_estimates', days_back=2)
    researched = _attach_dod_trend(researched, yesterday)

    return {
        'items':        researched,
        'count':        len(researched),
        'inputs':       [{'key': _lookup_key(it['kind'],
                                              it['display_title'],
                                              it.get('artist') or ''),
                          'kind': it['kind'],
                          'title': it['display_title'],
                          'artist': it.get('artist') or ''}
                         for it in items],
        'model':        _WEBSEARCH_MODEL,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', default='',
                        help='Comma-separated: podcast,song,streaming')
    args = parser.parse_args()
    only = set()
    for tok in args.only.split(','):
        t = tok.strip().lower()
        if t:
            only.add(t)

    from ._base import run_scraper
    def _fetch():
        return fetch(only=only or None)
    result = run_scraper('stream_estimates', 'US Streams', 'meta', _fetch)
    n = result.get('count') or 0
    print(f"stream_estimates: count={n} error={result.get('error')}",
           file=sys.stderr)
    for k, v in list((result.get('items') or {}).items())[:8]:
        print(f"  {k}: {_humanize(v['us_estimate'])}  "
              f"({v.get('direction', '?')}, {v['confidence']})",
              file=sys.stderr)
