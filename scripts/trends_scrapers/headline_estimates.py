"""
US-gen-pop daily-readership estimator for headlines on the Trends IQ
Headlines tab.

For every headline that surfaces in the Trends IQ "Top trending" +
"By news source" + "Philanthropy" boards, we ask Claude Sonnet 4.5
(with the native `web_search` tool) to painstakingly reason about how
many US adults actually read the article in a typical 24h window:

  - Publisher's US daily unique visitors (SimilarWeb, Comscore, press
    kits, media-kit rate cards, publicly disclosed monthly-uniques).
  - Article's position (front-page banner vs. section lead vs. buried
    river item) via web_search of the outlet's homepage / section.
  - Topic amplification: syndication via Google News Top Stories,
    Apple News, X/Twitter share velocity, Facebook News Feed lift.
  - Comparable-article baselines: what did a similar-topic story on
    the same outlet reach in the last 30 days?
  - Conservative discount when signals are weak.

Claude always returns a range (low / mid / high) plus a confidence
tag so the reader can tell "solid Comscore data" apart from "inferred
from publisher averages".

Day-over-day trend: we snapshot to a dated S3 key each run; on the
next run we look up yesterday's estimate for the same normalized
title and compute (delta_pct, direction) so the dashboard can render
an up / down / stable arrow next to each headline row.

Output shape (kind='meta'):

    {
      "source":     "headline_estimates",
      "kind":       "meta",
      "fetched_at": "...",
      "generated_at": "...",
      "items": {
        "trump signs executive order on chips": {
          "kind":            "headline",
          "display_title":   "Trump signs executive order on chips",
          "source":          "New York Times",
          "url":             "https://nytimes.com/...",
          "us_estimate":     1_800_000,
          "us_estimate_low":  1_200_000,
          "us_estimate_high": 2_500_000,
          "confidence":      "medium",
          "unit_label":      "daily US readers",
          "method":          "NYT US daily uniques ~5.5M...",
          "sources":         ["https://...", "https://..."],
          "delta_pct":       0.15,
          "direction":       "up",
          "prev_estimate":   1_565_000,
          "prev_date":       "2026-08-04",
          "as_of_date":      "2026-08-05"
        },
        ...
      },
      "count": 87
    }

Standalone:

    python3 -m scripts.trends_scrapers.headline_estimates
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
# Caps. Bumped 2026-08-31 (Jenna: "everything should have a value in US
# Audience except films") to 800: the dashboard renders top 15 overall +
# ~19 outlets x 5 + Business 40 + Wall Street 50 + Philanthropy 40 +
# each of the *_by_source dicts (~200 more) ~= 500-600 unique headlines
# per compute_view. 800 gives headroom for churn. Sonnet 4.5 web_search
# ~$0.02/item -> ~$12-16/day, offset intra-day by the "already researched
# today" dedupe below.
# -------------------------------------------------------------------------
_MAX_HEADLINE_ITEMS  = 800

_WEBSEARCH_MODEL      = (os.environ.get('HEADLINE_ESTIMATES_MODEL')
                          or 'claude-sonnet-4-5')
_WEBSEARCH_MAX_TOKENS = 1200
_WEBSEARCH_MAX_USES   = 3
_WEBSEARCH_TIMEOUT_S  = 60
_CONCURRENCY          = 8


# Byte-for-byte identical to trends_iq._CP_STOPWORDS / stream_estimates
# so cross-module title-key lookups stay in sync.
_STOPWORDS = {
    'the', 'a', 'an', 'and', 'of', 'in', 'on', 'to', 'for', 'at', 'is',
    'trending', 'today', 'now', 'news', 'latest', 'best',
}


def _cp_normalize(text: str) -> str:
    if not text:
        return ''
    s = text.lower().lstrip('#').strip()
    s = re.sub(r'[^\w\s]+', ' ', s)
    tokens = [t for t in s.split() if t and t not in _STOPWORDS]
    return ' '.join(tokens)


def _s3():
    return boto3.client('s3',
                         region_name=os.environ.get('AWS_REGION') or 'us-east-2')


def _read_dated_snapshot(source: str, days_back: int) -> Optional[dict]:
    d = date.today() - timedelta(days=days_back)
    key = f'{_S3_DATED.format(date=d.isoformat())}{source}.json'
    try:
        obj = _s3().get_object(Bucket=_S3_BUCKET, Key=key)
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _read_snapshot(source: str) -> Optional[dict]:
    try:
        obj = _s3().get_object(Bucket=_S3_BUCKET,
                                Key=f'{_S3_LATEST}{source}.json')
        return json.loads(obj['Body'].read().decode('utf-8'))
    except Exception:
        return None


# -------------------------------------------------------------------------
# Item collection.
#
# 2026-08-31 update: collectors now seed from compute_view's live
# render output FIRST so every headline the dashboard is actually
# showing gets priced. Snapshot + RSS pools backfill the tail so
# tomorrow's compute_view still has ready-to-serve estimates for
# stories that broke overnight without a fresh scraper run.
# -------------------------------------------------------------------------
_COMPUTE_VIEW_CACHE: Optional[dict] = None


def _compute_view_cards() -> dict:
    """Return `cards` from a compute_view() call, cached for the
    duration of this process. Prefers the warm S3 cache (matches what
    the dashboard is currently rendering); falls back to a fresh
    build if that returns empty. Best-effort: returns {} on total
    failure."""
    global _COMPUTE_VIEW_CACHE
    if _COMPUTE_VIEW_CACHE is not None:
        return _COMPUTE_VIEW_CACHE
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        from trends_iq import compute_view  # type: ignore
        try:
            payload = compute_view({}, force_refresh=False) or {}
        except Exception as e_warm:
            logger.warning("headline_estimates: compute_view warm read "
                            "failed (%s), retrying force_refresh=True",
                            e_warm)
            payload = {}
        cards = payload.get('cards') or {}
        if not cards or not (cards.get('trending_headlines')
                              or cards.get('articles_by_source')):
            try:
                payload = compute_view({}, force_refresh=True) or {}
                cards = payload.get('cards') or {}
            except Exception as e_fresh:
                logger.warning("headline_estimates: compute_view "
                                "force_refresh failed (%s)", e_fresh)
        _COMPUTE_VIEW_CACHE = cards or {}
        logger.info("headline_estimates: compute_view cached %d card keys",
                     len(_COMPUTE_VIEW_CACHE))
    except Exception as e:
        logger.warning("headline_estimates: compute_view unavailable (%s); "
                        "falling back to snapshot-only collection", e)
        _COMPUTE_VIEW_CACHE = {}
    return _COMPUTE_VIEW_CACHE


def _collect_headlines(max_items: int = _MAX_HEADLINE_ITEMS) -> list[dict]:
    """Union of every headline compute_view is currently rendering
    (Trending Headlines + by-source outlets + Business + Wall Street
    + Philanthropy + each of the *_by_source dicts) PLUS the live
    RSS pool + snapshot backfill. compute_view rows go FIRST so the
    dashboard-visible items always land inside the cap; snapshot +
    RSS backfill fills the tail."""
    per: dict[str, dict] = {}
    next_rank = 1

    def _add(title: str, source: str, url: str = '', image: str = '',
              seendate: str = '', origin: str = '') -> None:
        nonlocal next_rank
        t = (title or '').strip()
        if not t:
            return
        key = _cp_normalize(t)
        if not key or key in per:
            return
        per[key] = {
            'kind':          'headline',
            'display_title': t[:200],
            'source':        source or '',
            'domain':        '',
            'url':           url or '',
            'image':         image or '',
            'seendate':      seendate or '',
            'best_rank':     next_rank,
            'chart_labels':  [f'{origin or (source or "unknown")} #{next_rank}'],
        }
        next_rank += 1

    # 1) compute_view live pass FIRST - this is what the dashboard
    #    is rendering right now, so every priced key hits a visible
    #    row.
    cards = _compute_view_cards()
    if cards:
        for a in (cards.get('trending_headlines') or []):
            _add(a.get('title') or '', a.get('source') or '',
                  a.get('url') or '', a.get('image') or '',
                  a.get('published') or a.get('seendate') or '',
                  origin='trending')
        for outlet in (cards.get('articles_by_source') or []):
            src = (outlet.get('source') or '').strip()
            for a in (outlet.get('articles') or []):
                _add(a.get('title') or '', src,
                      a.get('url') or '', a.get('image') or '',
                      a.get('published') or a.get('seendate') or '',
                      origin=src)
        for a in (cards.get('business_news') or []):
            _add(a.get('title') or '',
                  a.get('source_label') or a.get('source') or 'business',
                  a.get('url') or '', a.get('image') or '',
                  a.get('published') or a.get('seendate') or '',
                  origin='business')
        for a in (cards.get('wall_street_news') or []):
            _add(a.get('title') or '',
                  a.get('source_label') or a.get('source') or 'wall_street',
                  a.get('url') or '', a.get('image') or '',
                  a.get('published') or a.get('seendate') or '',
                  origin='wall_street')
        for a in (cards.get('philanthropy_news') or []):
            _add(a.get('title') or '',
                  a.get('source_label') or a.get('source') or 'philanthropy',
                  a.get('url') or '', a.get('image') or '',
                  a.get('published') or a.get('seendate') or '',
                  origin='philanthropy')
        for _slug, rows in (cards.get('business_news_by_source') or {}).items():
            for a in (rows or []):
                _add(a.get('title') or '',
                      a.get('source_label') or a.get('source') or _slug,
                      a.get('url') or '', a.get('image') or '',
                      a.get('published') or a.get('seendate') or '',
                      origin=f'business/{_slug}')
        for _slug, rows in (cards.get('wall_street_news_by_source') or {}).items():
            for a in (rows or []):
                _add(a.get('title') or '',
                      a.get('source_label') or a.get('source') or _slug,
                      a.get('url') or '', a.get('image') or '',
                      a.get('published') or a.get('seendate') or '',
                      origin=f'wall_street/{_slug}')
        for _slug, rows in (cards.get('philanthropy_news_by_source') or {}).items():
            for a in (rows or []):
                _add(a.get('title') or '',
                      a.get('source_label') or a.get('source') or _slug,
                      a.get('url') or '', a.get('image') or '',
                      a.get('published') or a.get('seendate') or '',
                      origin=f'philanthropy/{_slug}')

    # 2) Live RSS pool backfill (matches what the dashboard fetches
    #    on its next request but not yet cached).
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        from trends_iq import _fetch_all_news_feeds  # type: ignore
        outlet_lists = _fetch_all_news_feeds()
    except Exception as e:
        logger.warning("headline_estimates: NEWS_FEEDS fetch failed: %s", e)
        outlet_lists = []

    for outlet_items in outlet_lists:
        for art in (outlet_items or [])[:8]:
            _add(art.get('title') or '', art.get('source') or '',
                  art.get('url') or '', art.get('image') or '',
                  art.get('seendate') or art.get('published') or '',
                  origin=art.get('source') or 'rss')

    # 3) Snapshot backfill - philanthropy / business / wall_street /
    #    articles_by_source. Same-day snapshots the daily scraper cron
    #    already wrote for us.
    for snap_key, origin in (('philanthropy_news', 'philanthropy'),
                              ('business_news', 'business'),
                              ('wall_street_news', 'wall_street')):
        try:
            obj = _s3().get_object(Bucket=_S3_BUCKET,
                                    Key=f'{_S3_LATEST}{snap_key}.json')
            snap = json.loads(obj['Body'].read().decode('utf-8'))
        except Exception:
            snap = {}
        for art in (snap.get('national') or [])[:60]:
            _add(art.get('title') or '',
                  art.get('source_label') or art.get('source') or origin,
                  art.get('url') or '', art.get('image') or '',
                  art.get('published') or art.get('seendate') or '',
                  origin=origin)

    return sorted(per.values(),
                   key=lambda e: (e['best_rank'], e['display_title']))[:max_items]


# -------------------------------------------------------------------------
# Claude prompt
# -------------------------------------------------------------------------
_PROMPT_HEADER = (
    "You are a media-audience research analyst. For the article below "
    "estimate how many US ADULTS (18+) will read it (i.e. land on the "
    "publisher's article page from any surface: direct, search, social, "
    "newsletter, aggregator, syndicator) over the 24-hour window that "
    "starts when it went live.\n\n"

    "GUIDING PRINCIPLE:\n"
    "  Always err CONSERVATIVE. When two defensible readings exist, take "
    "the LOWER one. When a signal is weak (no fresh SimilarWeb / Comscore "
    "data), bias the mid-point 20-30% below the naive publisher-average "
    "figure. Better a defensible low reading than a puffy top-line.\n\n"

    "US GEN POP CALIBRATION (HARD RULE):\n"
    "  Every number you return is a DAILY US COUNT of unique adult readers. "
    "It must be commensurate with the fraction of the ~260M US adults 18+ "
    "who realistically land on this article in a typical day. Anchor to "
    "the outlet's publicly disclosed US daily uniques:\n"
    "    - NYT ~5.5M US daily uniques, WSJ ~2.2M, WaPo ~3.5M, USA Today ~4.8M,\n"
    "      LA Times ~2.0M, CNN ~4.5M, Fox News ~3.5M, MSNBC ~1.4M,\n"
    "      NBC News ~4.0M, CBS News ~3.0M, ABC News ~3.5M, BBC (US) ~2.0M,\n"
    "      NPR ~2.5M, Politico ~0.8M, The Hill ~1.2M, HuffPost ~2.5M,\n"
    "      Bloomberg (unpaywalled) ~1.2M, Reuters (US) ~1.5M, Axios ~1.0M,\n"
    "      Guardian US ~2.5M, Yahoo News ~6.0M, Vox ~0.9M, CNBC ~2.5M.\n"
    "      Chronicle of Philanthropy ~30K, Nonprofit Quarterly ~15K,\n"
    "      SSIR ~20K, Blue Avocado ~5K, Guardian Global Development ~150K.\n"
    "  A SINGLE article never captures the outlet's full daily uniques. "
    "Typical article-to-outlet ratios by placement:\n"
    "    - Front-page banner / hero:      15-30% of outlet's daily uniques\n"
    "    - Section lead / secondary hero:  5-12%\n"
    "    - River item on homepage:         1-4%\n"
    "    - Buried section piece:           0.3-1%\n"
    "  If web_search confirms the article is on Google News Top Stories "
    "OR Apple News's top slot, multiply your placement estimate by 1.5-2x "
    "(cap total at 40% of outlet daily uniques). Otherwise no aggregator "
    "boost.\n"
    "  Political + celebrity + national-security stories skew high (2-4x "
    "vs local/niche); business + policy skew low (0.5-1x vs the placement "
    "average).\n\n"

    "CONFIDENCE TAGGING:\n"
    "  - high:   fresh SimilarWeb / Comscore data for the exact article OR "
    "for the outlet's daily uniques in the last 30 days.\n"
    "  - medium: publisher-average anchor + placement inferred from a live "
    "web_search of the outlet's homepage/section.\n"
    "  - low:    no placement evidence + no fresh publisher-average; "
    "reasoning only from category-average and topic amplification.\n\n"

    "REQUIRED OUTPUT (JSON, no prose before or after):\n"
    "{\n"
    "  \"us_estimate\":     <int, daily unique adult US readers>,\n"
    "  \"us_estimate_low\":  <int>,\n"
    "  \"us_estimate_high\": <int>,\n"
    "  \"confidence\":      \"high|medium|low\",\n"
    "  \"unit_label\":      \"daily US readers\",\n"
    "  \"method\":          \"<one-sentence explanation>\",\n"
    "  \"sources\":         [\"<url>\", \"<url>\"]\n"
    "}\n"
)


def _build_prompt(item: dict) -> str:
    title  = item['display_title']
    source = item['source'] or 'unknown'
    url    = item['url']    or ''
    body = (
        _PROMPT_HEADER +
        f"\nARTICLE:\n"
        f"  Title:  {title}\n"
        f"  Outlet: {source}\n"
        f"  URL:    {url}\n"
    )
    return body


# -------------------------------------------------------------------------
# Response parsing + sanitization
# -------------------------------------------------------------------------
_JSON_RE = re.compile(r'\{[\s\S]*\}')


def _extract_json_blob(text: str) -> Optional[dict]:
    if not text:
        return None
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# Sanity ceilings so a hallucinated 100M number doesn't propagate.
# Any outlet-article combo above 15M in a single day is almost
# certainly wrong; clamp with 30% pull toward the low bound.
_ARTICLE_MAX_READERS = 15_000_000


def _sanitize_result(item: dict, parsed: dict) -> Optional[dict]:
    try:
        mid  = int(parsed.get('us_estimate') or 0)
        low  = int(parsed.get('us_estimate_low')  or 0)
        high = int(parsed.get('us_estimate_high') or 0)
    except (TypeError, ValueError):
        return None
    if mid <= 0 and low <= 0 and high <= 0:
        return None
    # Order sanity
    lo = max(0, min(low, mid))
    hi = max(mid, high)
    md = mid if mid > 0 else (lo + hi) // 2

    # Ceiling clamp
    if md > _ARTICLE_MAX_READERS:
        md = int(_ARTICLE_MAX_READERS * 0.6)
    if hi > _ARTICLE_MAX_READERS:
        hi = _ARTICLE_MAX_READERS

    confidence = (parsed.get('confidence') or 'low').strip().lower()
    if confidence not in ('high', 'medium', 'low'):
        confidence = 'low'

    return {
        'kind':             'headline',
        'display_title':    item['display_title'],
        'source':           item['source'],
        'url':              item['url'],
        'image':            item.get('image')    or '',
        'seendate':         item.get('seendate') or '',
        'best_rank':        item.get('best_rank'),
        'chart_labels':     item.get('chart_labels') or [],
        'us_estimate':      md,
        'us_estimate_low':  lo,
        'us_estimate_high': hi,
        'confidence':       confidence,
        'unit_label':       parsed.get('unit_label') or 'daily US readers',
        'method':           (parsed.get('method') or '').strip()[:400],
        'sources':          [s for s in (parsed.get('sources') or [])
                              if isinstance(s, str)][:4],
    }


def _lookup_key(title: str) -> str:
    return _cp_normalize(title)


# -------------------------------------------------------------------------
# Claude web_search invocation (parallel)
# -------------------------------------------------------------------------
def _research_one(item: dict, client) -> tuple[str, Optional[dict]]:
    key = _lookup_key(item['display_title'])
    prompt = _build_prompt(item)
    for attempt in range(2):
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
            logger.info("headline_estimates %r attempt %d: %s",
                         item['display_title'][:60], attempt + 1, e)
            continue
        text = ''
        for block in resp.content or []:
            if getattr(block, 'type', '') == 'text':
                text += getattr(block, 'text', '') or ''
        parsed = _extract_json_blob(text)
        if not parsed:
            logger.info("headline_estimates %r: unparseable",
                         item['display_title'][:60])
            continue
        result = _sanitize_result(item, parsed)
        if result:
            return key, result
    return key, None


def _research_all(items: list[dict]) -> dict[str, dict]:
    api_key = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()
    if not api_key:
        logger.warning("headline_estimates: ANTHROPIC_API_KEY missing; skipping")
        return {}
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        logger.warning("headline_estimates: anthropic SDK missing: %s", e)
        return {}
    client = anthropic.Anthropic(api_key=api_key)

    out: dict[str, dict] = {}
    if not items:
        return out
    logger.info("headline_estimates: researching %d items with %s (concurrency=%d)",
                 len(items), _WEBSEARCH_MODEL, _CONCURRENCY)
    with concurrent.futures.ThreadPoolExecutor(max_workers=_CONCURRENCY) as ex:
        futs = {ex.submit(_research_one, it, client): it for it in items}
        for i, fut in enumerate(concurrent.futures.as_completed(futs)):
            try:
                key, result = fut.result(timeout=_WEBSEARCH_TIMEOUT_S + 15)
            except Exception as e:
                logger.info("headline_estimates worker: %s", e)
                continue
            if key and result:
                out[key] = result
                logger.info("  [%3d/%d] %-52s -> %s ~ %s",
                             i + 1, len(items),
                             result['display_title'][:52],
                             _humanize(result['us_estimate']),
                             result['confidence'])
    return out


# -------------------------------------------------------------------------
# Day-over-day attach
# -------------------------------------------------------------------------
_TREND_STABLE_PCT = 0.05


def _direction_and_delta(cur_mid: int, prev_mid: int) -> tuple[str, float]:
    if prev_mid <= 0 or cur_mid <= 0:
        return 'new', 0.0
    delta = (cur_mid - prev_mid) / prev_mid
    if abs(delta) < _TREND_STABLE_PCT:
        return 'stable', round(delta, 4)
    return ('up' if delta > 0 else 'down'), round(delta, 4)


def _attach_dod_trend(current: dict[str, dict],
                       yesterday: Optional[dict],
                       prev_date_iso: Optional[str] = None,
                       today_iso: Optional[str] = None) -> dict[str, dict]:
    prior_items = ((yesterday or {}).get('items') or {})
    for key, cur in current.items():
        prev = prior_items.get(key) or {}
        prev_mid = prev.get('us_estimate') or 0
        cur_mid  = cur.get('us_estimate')  or 0
        direction, delta = _direction_and_delta(cur_mid, prev_mid)
        cur['direction']     = direction
        cur['delta_pct']     = delta
        cur['prev_estimate'] = prev_mid if prev_mid > 0 else None
        if prev_date_iso: cur['prev_date']   = prev_date_iso
        if today_iso:     cur['as_of_date']  = today_iso
    return current


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
def fetch() -> dict[str, Any]:
    items = _collect_headlines()

    if not items:
        # Preserve prior snapshot on a no-op run (same safety principle
        # as stream_estimates.fetch()) so an intra-day rerun with a
        # temporarily empty RSS pool doesn't clobber the already-priced
        # snapshot.
        prior_snap = _read_snapshot('headline_estimates') or {}
        return {
            'items':           prior_snap.get('items') or {},
            'count':           len(prior_snap.get('items') or {}),
            'error':           'no headlines collected upstream',
            'model':           _WEBSEARCH_MODEL,
            'preserved_prior': bool(prior_snap.get('items')),
        }

    logger.info("headline_estimates: total unique headlines = %d", len(items))

    # Incremental behavior: don't re-price items already covered today
    # (matches stream_estimates.fetch()'s intra-day dedupe pattern so
    # a re-run to catch newly-broken headlines doesn't pay for the
    # ones the earlier cron already priced).
    today_iso = date.today().isoformat()
    prior_snap = _read_snapshot('headline_estimates') or {}
    prior_items = prior_snap.get('items') or {}
    already_today = {
        k for k, v in prior_items.items()
        if v.get('as_of_date') == today_iso and v.get('us_estimate')
    }
    items_to_research = [
        it for it in items
        if _lookup_key(it['display_title']) not in already_today
    ]
    if len(items_to_research) < len(items):
        logger.info("headline_estimates: skipping %d items already researched "
                     "today (intra-day rerun); researching %d new items",
                     len(items) - len(items_to_research),
                     len(items_to_research))

    researched_new = _research_all(items_to_research)

    prev_date_iso = (date.today() - timedelta(days=1)).isoformat()
    yesterday = _read_dated_snapshot('headline_estimates', days_back=1)
    if not yesterday:
        yesterday = _read_dated_snapshot('headline_estimates', days_back=2)
        prev_date_iso = (date.today() - timedelta(days=2)).isoformat()

    # Compose the final researched dict as the union of prior_items
    # (preserves items priced earlier today) + newly researched
    # (wins on collision).
    researched = dict(prior_items)
    researched.update(researched_new)
    researched = _attach_dod_trend(researched, yesterday,
                                     prev_date_iso=prev_date_iso,
                                     today_iso=today_iso)

    return {
        'items':        researched,
        'count':        len(researched),
        'inputs':       [{'key': _lookup_key(it['display_title']),
                          'title': it['display_title'],
                          'source': it['source']}
                         for it in items],
        'model':        _WEBSEARCH_MODEL,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    parser = argparse.ArgumentParser()
    args = parser.parse_args()

    from ._base import run_scraper
    def _fetch():
        return fetch()
    result = run_scraper('headline_estimates', 'US Headline Readers',
                          'meta', _fetch)
    n = result.get('count') or 0
    print(f"headline_estimates: count={n} error={result.get('error')}",
           file=sys.stderr)
    for k, v in list((result.get('items') or {}).items())[:8]:
        print(f"  {v['source'][:20]:20s} {_humanize(v['us_estimate']):>7s} "
              f"({v.get('direction', '?')}, {v['confidence']}) "
              f"{v['display_title'][:60]}",
              file=sys.stderr)
