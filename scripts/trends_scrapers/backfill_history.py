#!/usr/bin/env python3
"""
Backfill Trends IQ history from real archive APIs.

The Movers panel and per-item Historical arcs both compute day-over-day
deltas from the dated snapshot archive on S3:

    - blue_iq/trends_rss/v1/{geo}/{YYYY-MM-DD}.json          (Google Trends narrow)
    - blue_iq/trends_rss_wide/v1/{YYYY-MM-DD}.json           (Google Trends wide)
    - trends_iq_snapshots/{YYYY-MM-DD}/{source}.json         (all scrapers)

Fresh installs have only a few days of these files, so deltas look thin.
This tool backfills the ONLY two sources with a real free archive:

    1. GDELT DOC 2.0 (headlines + trending people)
       Full 30-day archive is trivially available via startdatetime and
       enddatetime query params on api.gdeltproject.org/api/v2/doc/doc.
       Writes trends_iq_snapshots/{date}/gdelt.json + gdelt-people.json.

    2. Google Trends via trendspy trending_now(hours=191)
       Google's trending_now endpoint returns trends currently in a
       rolling window up to 191 hours (~8 days) back, each row tagged
       with started_ts. We bucket rows by started_ts date and write one
       narrow-pool file per day per geo AND one wide-pool file per day.

Sources we CANNOT backfill (no public archive at any accessible endpoint):
    - X trends24, Instagram, TikTok Creative Center
    - Amazon / Nike / Target / Best Buy / Sephora / Walmart / Etsy /
      Lululemon
    - Disney+ / Hulu / Max / Prime Video / ESPN+ / YouTube trending
      (YouTube's Data API most-popular chart is present-only; you can
       search by publish date but that's a different signal.)

These will start producing deltas naturally once we have >=2 daily
scrapes.

Usage
-----

    # Local, using the AWS creds already in your shell:
    cd bg-webapp
    python3 -m scripts.trends_scrapers.backfill_history --days 30

    # Skip the Google Trends pass (if trendspy is rate-limited):
    python3 -m scripts.trends_scrapers.backfill_history --days 30 --skip-google

    # Skip GDELT (headlines archive is by far the slower pass):
    python3 -m scripts.trends_scrapers.backfill_history --days 30 --skip-gdelt

    # Dry-run: show what would be written but don't touch S3.
    python3 -m scripts.trends_scrapers.backfill_history --days 30 --dry-run

    # Overwrite existing dated snapshots (default: skip days already
    # written so the tool is safely re-runnable):
    python3 -m scripts.trends_scrapers.backfill_history --days 30 --overwrite

After a successful run, purge the trends_iq dashboard cache so the app
re-reads the freshly backfilled history:

    aws s3 rm s3://dashboard-inputs/system/trends_iq_cache.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

# Make the parent bg-webapp/ importable so we can reuse the exact person
# extractor + normalization the live pipeline uses. Without this the
# script would drift from the live rules over time.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)

# ── S3 layout (must match trends_history.py + external_signals.py + _base.py)
BUCKET               = os.environ.get('TRENDS_IQ_CACHE_BUCKET', 'dashboard-inputs')
SCRAPER_SNAP_PREFIX  = 'trends_iq_snapshots/'          # + {date}/{source}.json
TRENDS_NARROW_PREFIX = 'blue_iq/trends_rss/v1/'        # + {geo}/{date}.json
TRENDS_WIDE_PREFIX   = 'blue_iq/trends_rss_wide/v1/'   # + {date}.json

# Same 16 geos the live wide-pool scraper unions. Sticking to the same
# set means the backfill produces snapshots the movers panel already
# knows how to read.
WIDE_GEOS = [
    'US', 'US-CA', 'US-TX', 'US-FL', 'US-NY', 'US-PA',
    'US-IL', 'US-OH', 'US-GA', 'US-NC', 'US-MI', 'US-NJ',
    'US-VA', 'US-WA', 'US-AZ', 'US-MA',
]

US_GEN_POP = 329_990_000


def _s3():
    import boto3  # type: ignore
    region = os.environ.get('AWS_REGION') or 'us-east-2'
    return boto3.client('s3', region_name=region)


def _put(key: str, payload, *, overwrite: bool, dry_run: bool) -> bool:
    """Write JSON to S3. Returns True if we actually wrote, False if
    skipped (already exists and not --overwrite) or in dry-run."""
    if dry_run:
        body = json.dumps(payload, ensure_ascii=False)
        logger.info("dry-run: would write s3://%s/%s (%d bytes)",
                     BUCKET, key, len(body))
        return False
    s3 = _s3()
    if not overwrite:
        try:
            s3.head_object(Bucket=BUCKET, Key=key)
            logger.info("skip existing s3://%s/%s (use --overwrite to replace)",
                         BUCKET, key)
            return False
        except Exception:
            pass
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3.put_object(Bucket=BUCKET, Key=key, Body=body,
                   ContentType='application/json')
    logger.info("wrote s3://%s/%s (%d bytes)", BUCKET, key, len(body))
    return True


# ────────────────────────────────────────────────────────────────────────────
# 1) GDELT DOC 2.0 — headlines + trending people, real 30-day archive
# ────────────────────────────────────────────────────────────────────────────
# Same endpoint external_signals.gdelt_political_articles hits. We drop
# the political theme filter and let sort=HybridRel surface the top
# general-interest articles for each 24h window.
_GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"

# Language filter reduces noise: English-language sources only.
_GDELT_BASE_QUERY = 'sourcecountry:US sourcelang:english'


def _gdelt_top_articles_for_day(day_iso: str, limit: int = 100,
                                  *, max_retries: int = 5) -> list[dict]:
    """Pull the top ~100 general-interest articles for the UTC day
    matching `day_iso`. Rank order is HybridRel (GDELT's blended
    relevance + reach + freshness score) so what we archive matches
    what the live scraper would have surfaced on that same day.

    GDELT's doc/api throttles aggressively without a stated policy.
    Retries with exponential backoff on 429 / 5xx / timeouts up to
    max_retries times so single-day pulls don't fail the batch."""
    import requests
    start = f'{day_iso.replace("-", "")}000000'
    end   = f'{day_iso.replace("-", "")}235959'
    params = {
        'query':          _GDELT_BASE_QUERY,
        'mode':           'ArtList',
        'maxrecords':     max(10, min(250, limit)),
        'format':         'json',
        'sort':           'HybridRel',
        'startdatetime':  start,
        'enddatetime':    end,
    }
    for attempt in range(max_retries):
        try:
            r = requests.get(_GDELT_DOC, params=params, timeout=45,
                              headers={'User-Agent': 'BG-Trends-Backfill/1.0'})
            status = r.status_code
            if status == 200:
                try:
                    return (r.json() or {}).get('articles') or []
                except Exception as e:
                    logger.warning("gdelt %s: json decode failed (%s)", day_iso, e)
                    return []
            # 429 = rate-limited; 5xx = transient. Both are retryable.
            if status == 429 or status >= 500:
                sleep_s = min(60, 5 * (2 ** attempt))
                logger.info("gdelt %s: http %s (attempt %d/%d, sleeping %ds)",
                             day_iso, status, attempt + 1, max_retries, sleep_s)
                time.sleep(sleep_s)
                continue
            # Any other status = don't retry, log and bail.
            logger.warning("gdelt %s: http %s (no retry)", day_iso, status)
            return []
        except Exception as e:
            sleep_s = min(60, 5 * (2 ** attempt))
            logger.info("gdelt %s: %s (attempt %d/%d, sleeping %ds)",
                         day_iso, e, attempt + 1, max_retries, sleep_s)
            time.sleep(sleep_s)
    logger.warning("gdelt %s: exhausted %d retries", day_iso, max_retries)
    return []


def _normalize_gdelt_headline_rows(articles: list[dict]) -> list[dict]:
    """Match the live gdelt.json schema written by
    trends_iq._write_history_snapshots. See _put_dated_snapshot's
    'national' array shape."""
    out: list[dict] = []
    seen_titles: set[str] = set()
    for i, a in enumerate(articles):
        title = (a.get('title') or '').strip()
        url = (a.get('url') or '').strip()
        if not title or not url:
            continue
        # Case + punctuation-insensitive de-dupe within the day.
        norm = re.sub(r'\W+', '', title.lower())
        if norm in seen_titles:
            continue
        seen_titles.add(norm)
        out.append({
            'rank':   len(out) + 1,
            'title':  title[:280],
            'url':    url,
            'source': a.get('domain') or a.get('sourceCommonName') or '',
            'geo':    'National',
        })
        if len(out) >= 30:
            break
    return out


def _extract_people_from_titles(articles: list[dict], lookback_days: int
                                ) -> list[dict]:
    """Reuse the live person-name extractor + wiki-pageview enrichment
    so the backfilled people match what the live pipeline would have
    produced from the same corpus."""
    try:
        from trends_iq import _extract_person_names  # type: ignore
    except Exception as e:
        logger.warning("could not import _extract_person_names: %s", e)
        return []

    counts: Counter = Counter()
    contexts: dict[str, list[str]] = defaultdict(list)
    for a in articles:
        title = (a.get('title') or '').strip()
        if not title:
            continue
        for name in _extract_person_names(title):
            counts[name] += 1
            if len(contexts[name]) < 3:
                contexts[name].append(title[:140])

    people: list[dict] = []
    for name, cnt in counts.most_common(60):
        if cnt < 2:
            continue
        people.append({
            'name':     name,
            'mentions': cnt,
            'context':  contexts.get(name, [])[:3],
        })
        if len(people) >= 40:
            break

    # Wikipedia pageview enrichment for that same day so the trend
    # arrow logic downstream has both mentions AND pageviews to work
    # with, matching the live snapshot shape exactly.
    if people:
        try:
            from external_signals import wikipedia_pageviews  # type: ignore
            titles = [p['name'] for p in people]
            views = wikipedia_pageviews(titles, lookback_days=lookback_days) or {}
            for p in people:
                p['pageviews'] = int(views.get(p['name']) or 0)
            people.sort(key=lambda x: (-x['mentions'], -x.get('pageviews', 0)))
        except Exception as e:
            logger.debug("wiki pageviews failed: %s", e)
            for p in people:
                p['pageviews'] = 0

    # Rank + gen-pop projection (same as _fetch_trending_people).
    for i, p in enumerate(people):
        p['rank']                = i + 1
        p['mentions_projected']  = int(p.get('mentions')  or 0) * US_GEN_POP
        p['pageviews_projected'] = int(p.get('pageviews') or 0) * US_GEN_POP
    return people


def backfill_gdelt(days: int, *, overwrite: bool, dry_run: bool,
                    throttle_s: float = 5.0) -> tuple[int, int]:
    """Backfill gdelt.json + gdelt-people.json for the past `days` days.
    Returns (headlines_written, people_written)."""
    today = date.today()
    headlines_written = 0
    people_written = 0
    for offset in range(1, days + 1):
        day = today - timedelta(days=offset)
        day_iso = day.isoformat()

        articles = _gdelt_top_articles_for_day(day_iso, limit=100)
        if not articles:
            logger.info("gdelt %s: 0 articles - skipping", day_iso)
            time.sleep(throttle_s)
            continue

        headlines = _normalize_gdelt_headline_rows(articles)
        if headlines:
            headlines_payload = {
                'source':     'gdelt',
                'fetched_at': datetime.now(timezone.utc).isoformat(),
                'backfilled': True,
                'as_of_date': day_iso,
                'national':   headlines,
            }
            key = f'{SCRAPER_SNAP_PREFIX}{day_iso}/gdelt.json'
            if _put(key, headlines_payload, overwrite=overwrite, dry_run=dry_run):
                headlines_written += 1

        people = _extract_people_from_titles(articles, lookback_days=1)
        if people:
            people_payload = {
                'source':     'gdelt-people',
                'fetched_at': datetime.now(timezone.utc).isoformat(),
                'backfilled': True,
                'as_of_date': day_iso,
                'national':   people,
            }
            key = f'{SCRAPER_SNAP_PREFIX}{day_iso}/gdelt-people.json'
            if _put(key, people_payload, overwrite=overwrite, dry_run=dry_run):
                people_written += 1

        # Gentle throttle: GDELT's doc/api rate limit is generous but
        # unpublished. One request per second per day is safe.
        time.sleep(throttle_s)

    return headlines_written, people_written


# ────────────────────────────────────────────────────────────────────────────
# 2) Google Trends via trendspy — bucket by started_ts to backfill ~7 days
# ────────────────────────────────────────────────────────────────────────────
# trendspy's trending_now(hours=191) returns every trend currently
# active-or-recent within the last ~8 days. Each row has started_ts.
# We call it once per geo, group rows by started_ts date, and stamp a
# per-day snapshot at the geo AND a merged wide-pool file. This means
# the wide-pool aggregator can now compute deltas over up to ~8 days
# where before it had ~3.

_GOOGLE_LOOKBACK_HOURS = 191   # trendspy max


def _score_from_growth(growth_pct: int) -> int:
    """Same fallback score external_signals._score_from_growth uses."""
    if growth_pct >= 1000:
        return 100000
    if growth_pct >= 500:
        return 50000
    if growth_pct >= 200:
        return 20000
    return max(1000, growth_pct * 10)


def _fetch_trendspy_wide_window(geo: str) -> list[dict]:
    """Pull ~8 days of trending searches for `geo`. Rows carry
    started_ts so we can bucket per-day downstream. Returns [] on any
    failure so the caller can move on."""
    try:
        from trendspy import Trends  # type: ignore
    except ImportError:
        logger.warning("trendspy not installed - skipping google backfill")
        return []
    try:
        tr = Trends()
        trends = tr.trending_now(geo=geo, hours=_GOOGLE_LOOKBACK_HOURS) or []
    except Exception as e:
        logger.warning("trendspy geo=%s hours=%s failed: %s",
                        geo, _GOOGLE_LOOKBACK_HOURS, e)
        return []

    rows: list[dict] = []
    for t in trends:
        term = (getattr(t, 'keyword', None) or '').strip()
        if not term:
            continue
        volume = int(getattr(t, 'volume', 0) or 0)
        growth = int(getattr(t, 'volume_growth_pct', 0) or 0)
        started_ts_raw = getattr(t, 'started_timestamp', None)
        if isinstance(started_ts_raw, list) and started_ts_raw:
            started_ts = int(started_ts_raw[0])
        elif isinstance(started_ts_raw, (int, float)):
            started_ts = int(started_ts_raw)
        else:
            started_ts = 0
        rows.append({
            'term':               term,
            'score':              volume or _score_from_growth(growth),
            'related':            [],
            'volume':             volume,
            'volume_growth_pct':  growth,
            'started_ts':         started_ts,
            'trend_keywords':     list(getattr(t, 'trend_keywords', None) or [])[:12],
            'topics':             list(getattr(t, 'topics', None) or []),
            'news_articles':      [],
            '_geo':               geo,
        })
    logger.info("trendspy geo=%s: %d rows in %dh window",
                 geo, len(rows), _GOOGLE_LOOKBACK_HOURS)
    return rows


def _bucket_by_started_day(rows: list[dict]) -> dict[str, list[dict]]:
    """Group rows by the UTC date of their started_ts. Rows with no
    started_ts land under today (best we can do)."""
    today_iso = date.today().isoformat()
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        ts = int(r.get('started_ts') or 0)
        if ts <= 0:
            buckets[today_iso].append(r)
            continue
        d_iso = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        buckets[d_iso].append(r)
    for d in list(buckets.keys()):
        buckets[d].sort(key=lambda x: -(x.get('volume') or 0))
    return buckets


def _merge_wide_pool_rows(per_geo_rows: dict[str, list[dict]]) -> list[dict]:
    """Union the per-geo rows for one calendar day into the wide-pool
    schema google_trends_wide.build_wide_pool writes. Same de-dup +
    geo-count logic so downstream readers see identical structure."""
    by_term: dict[str, dict] = {}
    for geo, rows in per_geo_rows.items():
        for r in rows:
            term = (r.get('term') or '').strip()
            if not term:
                continue
            key = term.lower()
            score = int(r.get('score') or 0)
            entry = by_term.get(key)
            if entry is None:
                by_term[key] = {
                    'term':      term,
                    'score':     score,
                    'related':   list(r.get('related') or [])[:6],
                    'geos':      [geo],
                    'peak_geo':  geo,
                    # Preserve rich fields for movers panel.
                    'volume':             r.get('volume'),
                    'volume_growth_pct':  r.get('volume_growth_pct'),
                    'started_ts':         r.get('started_ts'),
                    'trend_keywords':     list(r.get('trend_keywords') or [])[:12],
                    'topics':             list(r.get('topics') or []),
                    'news_articles':      list(r.get('news_articles') or []),
                }
            else:
                entry['geos'].append(geo)
                if score > entry['score']:
                    entry['score']    = score
                    entry['peak_geo'] = geo
                    entry['term']     = term
                    # Only overwrite the volume-ish fields if the higher-
                    # scored copy actually has them (some geos return
                    # zeros).
                    if r.get('volume') is not None:
                        entry['volume']            = r.get('volume')
                        entry['volume_growth_pct'] = r.get('volume_growth_pct')
                # Union trend_keywords (order-preserving, deduped).
                if r.get('trend_keywords'):
                    seen = {x.lower() for x in entry['trend_keywords']}
                    for kw in r['trend_keywords']:
                        if kw.lower() not in seen:
                            entry['trend_keywords'].append(kw)
                            seen.add(kw.lower())
                    entry['trend_keywords'] = entry['trend_keywords'][:12]

    for e in by_term.values():
        e['geos'] = sorted(set(g for g in e['geos'] if g))
        e['geo_count'] = len(e['geos'])
    return sorted(by_term.values(),
                    key=lambda x: (-int(x.get('score') or 0), -x['geo_count']))


def backfill_google_trends(days: int, *, overwrite: bool, dry_run: bool
                            ) -> tuple[int, int]:
    """Backfill narrow + wide Google Trends snapshots for as many of
    the past `days` days as trendspy's rolling window will let us
    reach (capped at ~8 days by the API). Returns (narrow_written,
    wide_written)."""
    # 1) Pull the ~8-day window ONCE per geo. That's 16 calls total.
    per_geo_all: dict[str, list[dict]] = {}
    for geo in WIDE_GEOS:
        per_geo_all[geo] = _fetch_trendspy_wide_window(geo)
        # trendspy uses Google's internal API - be polite.
        time.sleep(1.5)

    # 2) Bucket each geo's rows by started_ts date.
    per_geo_by_day: dict[str, dict[str, list[dict]]] = {}
    for geo, rows in per_geo_all.items():
        per_geo_by_day[geo] = _bucket_by_started_day(rows)

    # 3) For each of the past `days` days, if any geo produced rows
    # for that day, write both the narrow-per-geo and wide-merged
    # snapshots. Cap at the window's natural depth.
    today = date.today()
    max_lookback_days = _GOOGLE_LOOKBACK_HOURS // 24  # 7
    depth = min(days, max_lookback_days)

    narrow_written = 0
    wide_written = 0
    for offset in range(1, depth + 1):
        day = today - timedelta(days=offset)
        d_iso = day.isoformat()

        # narrow-per-geo
        per_geo_this_day: dict[str, list[dict]] = {}
        for geo in WIDE_GEOS:
            rows = per_geo_by_day.get(geo, {}).get(d_iso, [])
            if not rows:
                continue
            per_geo_this_day[geo] = rows
            key = f'{TRENDS_NARROW_PREFIX}{geo}/{d_iso}.json'
            # Narrow schema is a bare list (see external_signals._trends_snap_put)
            payload_narrow = [
                {k: v for k, v in r.items() if not k.startswith('_')}
                for r in rows
            ]
            if _put(key, payload_narrow, overwrite=overwrite, dry_run=dry_run):
                narrow_written += 1

        if not per_geo_this_day:
            logger.info("google-trends %s: nothing in window for any geo", d_iso)
            continue

        # wide-merged
        merged = _merge_wide_pool_rows(per_geo_this_day)
        wide_payload = {
            'date':          d_iso,
            'generated_at':  datetime.now(timezone.utc).isoformat(),
            'geos_pulled':   WIDE_GEOS,
            'geos_ok':       sorted(per_geo_this_day.keys()),
            'unique_terms':  len(merged),
            'terms':         merged,
            'backfilled':    True,
        }
        key = f'{TRENDS_WIDE_PREFIX}{d_iso}.json'
        if _put(key, wide_payload, overwrite=overwrite, dry_run=dry_run):
            wide_written += 1

    if depth < days:
        logger.info("google-trends: window only reaches %d days back "
                     "(trendspy caps at hours=%d); older days skipped",
                     depth, _GOOGLE_LOOKBACK_HOURS)
    return narrow_written, wide_written


# ────────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30,
                     help='How many days back to backfill (default 30)')
    ap.add_argument('--overwrite', action='store_true',
                     help='Overwrite existing dated snapshots')
    ap.add_argument('--dry-run', action='store_true',
                     help="Don't touch S3, just log what would happen")
    ap.add_argument('--skip-gdelt', action='store_true',
                     help='Skip the GDELT headline + people backfill')
    ap.add_argument('--skip-google', action='store_true',
                     help='Skip the Google Trends backfill (trendspy pass)')
    ap.add_argument('--throttle', type=float, default=5.0,
                     help='Sleep between GDELT day pulls (default 5s)')
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )

    started = time.time()
    logger.info("backfill_history start: days=%d overwrite=%s dry_run=%s",
                 args.days, args.overwrite, args.dry_run)

    totals = {
        'gdelt_headlines': 0,
        'gdelt_people':    0,
        'google_narrow':   0,
        'google_wide':     0,
    }

    if not args.skip_gdelt:
        h, p = backfill_gdelt(args.days, overwrite=args.overwrite,
                                dry_run=args.dry_run, throttle_s=args.throttle)
        totals['gdelt_headlines'] = h
        totals['gdelt_people']    = p

    if not args.skip_google:
        n, w = backfill_google_trends(args.days, overwrite=args.overwrite,
                                        dry_run=args.dry_run)
        totals['google_narrow'] = n
        totals['google_wide']   = w

    elapsed = time.time() - started
    logger.info("backfill_history done in %.1fs: %s", elapsed, totals)
    print(json.dumps({'ok': True, 'elapsed_s': round(elapsed, 1),
                       'written': totals}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
