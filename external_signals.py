"""
external_signals.py — Public external data sources for Blue IQ.

Blue IQ blends three things into one card output:
  1. Crosswalk panel signal (ClickHouse `clickstream_final` + `user_data_sanitized`)
  2. Google Trends (search-interest by state, rising queries)
  3. GDELT 2.0 (article-level news mentions by location, with tone/themes)
  4. Wikipedia pageviews (politician engagement when panel signal is thin)

Everything is best-effort. If any external source is unreachable we just skip
it and fall back to whatever did succeed. The dashboard never surfaces which
source contributed what.

No API keys required. All endpoints are public.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import requests

logger = logging.getLogger(__name__)

# Short, polite timeouts. We never want a slow external dep to delay an
# `/api/blue-iq/data` response — if Trends/GDELT are down we just blend
# whatever else we have and move on.
_HTTP_TIMEOUT_S = 8
_HTTP_RETRIES   = 2
_UA             = "CrosswalkBlueIQ/1.0 (+contact: jenna@crosswalknyc.com)"

# ── ISO geo codes (Trends + GDELT both use these) ───────────────────────────
US_STATE_TO_ISO = {
    'Alabama':'US-AL','Alaska':'US-AK','Arizona':'US-AZ','Arkansas':'US-AR',
    'California':'US-CA','Colorado':'US-CO','Connecticut':'US-CT','Delaware':'US-DE',
    'Florida':'US-FL','Georgia':'US-GA','Hawaii':'US-HI','Idaho':'US-ID',
    'Illinois':'US-IL','Indiana':'US-IN','Iowa':'US-IA','Kansas':'US-KS',
    'Kentucky':'US-KY','Louisiana':'US-LA','Maine':'US-ME','Maryland':'US-MD',
    'Massachusetts':'US-MA','Michigan':'US-MI','Minnesota':'US-MN','Mississippi':'US-MS',
    'Missouri':'US-MO','Montana':'US-MT','Nebraska':'US-NE','Nevada':'US-NV',
    'New Hampshire':'US-NH','New Jersey':'US-NJ','New Mexico':'US-NM','New York':'US-NY',
    'North Carolina':'US-NC','North Dakota':'US-ND','Ohio':'US-OH','Oklahoma':'US-OK',
    'Oregon':'US-OR','Pennsylvania':'US-PA','Rhode Island':'US-RI','South Carolina':'US-SC',
    'South Dakota':'US-SD','Tennessee':'US-TN','Texas':'US-TX','Utah':'US-UT',
    'Vermont':'US-VT','Virginia':'US-VA','Washington':'US-WA','West Virginia':'US-WV',
    'Wisconsin':'US-WI','Wyoming':'US-WY','District of Columbia':'US-DC',
}

# Two-letter -> full name lookup for callers that pass STATE postal codes.
_USPS_TO_NAME = {
    'AL':'Alabama','AK':'Alaska','AZ':'Arizona','AR':'Arkansas','CA':'California',
    'CO':'Colorado','CT':'Connecticut','DE':'Delaware','FL':'Florida','GA':'Georgia',
    'HI':'Hawaii','ID':'Idaho','IL':'Illinois','IN':'Indiana','IA':'Iowa','KS':'Kansas',
    'KY':'Kentucky','LA':'Louisiana','ME':'Maine','MD':'Maryland','MA':'Massachusetts',
    'MI':'Michigan','MN':'Minnesota','MS':'Mississippi','MO':'Missouri','MT':'Montana',
    'NE':'Nebraska','NV':'Nevada','NH':'New Hampshire','NJ':'New Jersey','NM':'New Mexico',
    'NY':'New York','NC':'North Carolina','ND':'North Dakota','OH':'Ohio','OK':'Oklahoma',
    'OR':'Oregon','PA':'Pennsylvania','RI':'Rhode Island','SC':'South Carolina',
    'SD':'South Dakota','TN':'Tennessee','TX':'Texas','UT':'Utah','VT':'Vermont',
    'VA':'Virginia','WA':'Washington','WV':'West Virginia','WI':'Wisconsin','WY':'Wyoming',
    'DC':'District of Columbia',
}


def normalize_state(state: Optional[str]) -> Optional[str]:
    """Accept either USPS code ('CA') or full name ('California'); return full name."""
    if not state:
        return None
    s = state.strip()
    if len(s) == 2 and s.upper() in _USPS_TO_NAME:
        return _USPS_TO_NAME[s.upper()]
    if s in US_STATE_TO_ISO:
        return s
    for name in US_STATE_TO_ISO:
        if name.lower() == s.lower():
            return name
    return None


def _get_json(url: str, *, params: dict | None = None, headers: dict | None = None) -> Optional[dict | list]:
    """GET with retries + short timeout. Returns None on any failure."""
    h = {'User-Agent': _UA}
    if headers:
        h.update(headers)
    last = None
    for attempt in range(_HTTP_RETRIES):
        try:
            r = requests.get(url, params=params or {}, headers=h, timeout=_HTTP_TIMEOUT_S)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            if not r.ok:
                last = f"HTTP {r.status_code}"
                continue
            ct = r.headers.get('content-type', '')
            if 'json' in ct:
                return r.json()
            try:
                return r.json()
            except Exception:
                try:
                    return json.loads(r.text)
                except Exception:
                    return None
        except Exception as e:
            last = str(e)
            time.sleep(0.5 * (attempt + 1))
    if last:
        logger.debug("external_signals GET failed %s: %s", url, last)
    return None


# ── 1. GOOGLE TRENDS (unofficial public endpoint) ───────────────────────────
# We use the same endpoint Trends UI hits. No key required, but rate-limited.
# Failures are silent and the caller proceeds with whatever else succeeded.

# NOTE: Google retired the /trends/api/dailytrends JSON endpoint
# (404 in 2026 Q2). Daily trends now use the RSS endpoint instead;
# see _TRENDS_RSS / _trends_rss_fetch below.
_TRENDS_REALTIME    = "https://trends.google.com/trends/api/realtimetrends"

# Google's geo codes for daily/realtime trends use country (US) + state (US-XX).
_TRENDS_RSS = "https://trends.google.com/trending/rss"
_TRENDS_HT_NS = 'https://trends.google.com/trending/rss'


def _trends_rss_fetch(geo: str) -> str:
    """Raw GET to the Google Trends daily-search RSS endpoint.

    This replaces the old /api/dailytrends JSON endpoint (Google retired
    that 404 in 2026 Q2). RSS is the documented public replacement and
    supports both `geo=US` (National) and `geo=US-XX` (state-level).
    Returns the raw XML body, or '' on any failure.
    """
    try:
        r = requests.get(_TRENDS_RSS, params={'geo': geo},
                          headers={'User-Agent': _UA},
                          timeout=_HTTP_TIMEOUT_S)
        if not r.ok:
            return ''
        return r.text or ''
    except Exception as e:
        logger.debug("trends RSS fetch failed for geo=%s: %s", geo, e)
        return ''


# ── Trends RSS 7-day snapshot cache ──────────────────────────────────────
# Google's RSS daily-trends feed only returns the past ~24h of trending
# searches per call. To present "past 7 days" in the dashboard we snapshot
# the live RSS to S3 once per (geo, calendar-day) and aggregate the past
# N days at request time. Snapshot key:
#
#   blue_iq/trends_rss/v1/{geo}/{YYYY-MM-DD}.json
#
# Payload is the raw [{term, score, related}, ...] for that geo on that
# UTC date. Lazy snapshotting: the first caller of any given day writes
# the snapshot; subsequent callers re-use it from S3. Days older than
# the lookback window are simply ignored (we don't garbage-collect, so
# the bucket fills slowly with one small JSON per geo per day).
_TRENDS_SNAP_BUCKET = os.environ.get('BLUE_IQ_CACHE_BUCKET', 'dashboard-inputs')
_TRENDS_SNAP_PREFIX = 'blue_iq/trends_rss/v1/'


def _trends_snap_s3():
    """boto3 client for the trends-snapshot bucket (lazy import)."""
    try:
        import boto3  # type: ignore
        return boto3.client('s3', region_name='us-east-2')
    except Exception as e:
        logger.debug("trends snapshot: boto3 unavailable (%s) — falling back to live-only", e)
        return None


def _trends_snap_key(geo: str, day_iso: str) -> str:
    return f"{_TRENDS_SNAP_PREFIX}{geo}/{day_iso}.json"


def _trends_snap_get(geo: str, day_iso: str) -> Optional[list[dict]]:
    """Read one day's trends snapshot from S3. None on miss / error."""
    s3 = _trends_snap_s3()
    if s3 is None:
        return None
    try:
        resp = s3.get_object(Bucket=_TRENDS_SNAP_BUCKET, Key=_trends_snap_key(geo, day_iso))
        return json.loads(resp['Body'].read().decode('utf-8'))
    except Exception:
        return None


def _trends_snap_put(geo: str, day_iso: str, rows: list[dict]) -> None:
    """Write one day's trends snapshot to S3. Silent on failure."""
    s3 = _trends_snap_s3()
    if s3 is None or not rows:
        return
    try:
        s3.put_object(
            Bucket=_TRENDS_SNAP_BUCKET,
            Key=_trends_snap_key(geo, day_iso),
            Body=json.dumps(rows).encode('utf-8'),
            ContentType='application/json',
        )
    except Exception as e:
        logger.debug("trends snapshot put failed for %s/%s: %s", geo, day_iso, e)


def _trends_fetch_today(geo: str) -> list[dict]:
    """Fetch and parse the live RSS for `geo` (one ~24h snapshot)."""
    body = _trends_rss_fetch(geo)
    if not body:
        return []

    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        logger.debug("trends RSS parse failed for geo=%s: %s", geo, e)
        return []

    ns = {'ht': _TRENDS_HT_NS}
    out: list[dict] = []
    for item in root.iter('item'):
        title_el = item.find('title')
        if title_el is None or not (title_el.text or '').strip():
            continue
        term = title_el.text.strip()
        # Approximate traffic looks like "200+", "50K+", "1M+". Parse loosely.
        traffic_el = item.find('ht:approx_traffic', ns)
        score = _parse_traffic((traffic_el.text or '').strip()) if traffic_el is not None else 0
        # Use related-news titles as the `related` array — they're the
        # closest analog to the old endpoint's relatedQueries and carry
        # strong topical signal for the political filter downstream.
        related: list[str] = []
        for ni in item.findall('ht:news_item', ns):
            t = ni.find('ht:news_item_title', ns)
            if t is not None and (t.text or '').strip():
                related.append(t.text.strip())
        out.append({'term': term, 'score': score, 'related': related[:6]})

    # De-dupe within the single-day fetch (RSS sometimes repeats items).
    by_term: dict[str, dict] = {}
    for row in out:
        key = row['term'].lower()
        if key not in by_term or by_term[key]['score'] < row['score']:
            by_term[key] = row
    return sorted(by_term.values(), key=lambda x: -x['score'])


def trends_top_issues(state: Optional[str] = None, lookback_days: int = 7) -> list[dict]:
    """Top trending search topics in the geo over the past `lookback_days`,
    filtered downstream to political.

    Returns `[{ 'term': str, 'score': int, 'related': [str, ...],
                'days_trending': int, 'first_seen': 'YYYY-MM-DD',
                'last_seen': 'YYYY-MM-DD' }, ...]` sorted by score desc.
    Empty list on total failure.

    state=None -> US-wide trends (geo=US). state='California' -> geo=US-CA.

    NOTE: Google's RSS daily-trends endpoint only returns the past ~24h
    of trends per call. To present a multi-day window we snapshot the
    RSS to S3 once per (geo, UTC-day) and aggregate the past
    `lookback_days` snapshots on each call:

      - score          = MAX score that term achieved on any day in
                         the window (matches "peak traffic in last
                         N days" intuition; doesn't double-count a
                         term that trended each day).
      - related        = union of every day's related-news titles,
                         capped at 6 (newest first).
      - days_trending  = number of days in the window the term hit
                         the trending list.
      - first_seen     = oldest UTC day-iso the term appeared.
      - last_seen      = newest UTC day-iso the term appeared.

    The first caller on any given day populates today's snapshot to
    S3 from the live RSS; subsequent callers re-use the snapshot. If
    S3 is unavailable, falls back gracefully to live-RSS-only (the
    pre-2026-06-29 behavior).
    """
    name = normalize_state(state)
    geo = US_STATE_TO_ISO.get(name) if name else 'US'

    # 1. Today's snapshot — try cache first, fetch live and persist on miss.
    today = datetime.now(timezone.utc).date()
    today_iso = today.isoformat()
    today_rows = _trends_snap_get(geo, today_iso)
    if today_rows is None:
        today_rows = _trends_fetch_today(geo)
        if today_rows:
            _trends_snap_put(geo, today_iso, today_rows)

    # 2. Walk backwards through the lookback window, pulling whatever
    #    snapshots exist. Missing days are silently skipped (the bucket
    #    only fills as the dashboard is used).
    per_day: list[tuple[str, list[dict]]] = []
    if today_rows:
        per_day.append((today_iso, today_rows))
    for offset in range(1, max(1, lookback_days)):
        day_iso = (today - timedelta(days=offset)).isoformat()
        rows = _trends_snap_get(geo, day_iso)
        if rows:
            per_day.append((day_iso, rows))

    if not per_day:
        return []

    # 3. Aggregate across days: max score, union of related (newest day
    #    first so newer related-news titles appear first), days_trending
    #    count, first_seen / last_seen day-iso.
    by_term: dict[str, dict] = {}
    for day_iso, rows in per_day:
        for row in rows:
            term = (row.get('term') or '').strip()
            if not term:
                continue
            key = term.lower()
            score = int(row.get('score') or 0)
            related = list(row.get('related') or [])
            existing = by_term.get(key)
            if existing is None:
                by_term[key] = {
                    'term':            term,
                    'score':           score,
                    'related':         related[:6],
                    'days_trending':   1,
                    'first_seen':      day_iso,
                    'last_seen':       day_iso,
                }
                continue
            if score > existing['score']:
                existing['score'] = score
                existing['term']  = term  # prefer casing of peak day
            existing['days_trending'] += 1
            if day_iso < existing['first_seen']:
                existing['first_seen'] = day_iso
            if day_iso > existing['last_seen']:
                existing['last_seen'] = day_iso
            # Merge related, dedupe, keep newest first (per_day is
            # already iterated newest-first).
            seen_rel = {r.lower() for r in existing['related']}
            for r in related:
                if r.lower() not in seen_rel:
                    existing['related'].append(r)
                    seen_rel.add(r.lower())
            existing['related'] = existing['related'][:6]

    return sorted(by_term.values(), key=lambda x: -x['score'])


def _parse_traffic(s: str) -> int:
    if not s:
        return 0
    s = s.replace(',', '').replace('+', '').strip().upper()
    mult = 1
    if s.endswith('K'):
        mult = 1_000
        s = s[:-1]
    elif s.endswith('M'):
        mult = 1_000_000
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except Exception:
        return 0


def trends_politician_interest(names: list[str], state: Optional[str] = None) -> dict[str, int]:
    """Per-politician relative search-interest score over the last 7 days.

    Returns `{name: 0..100}`. Empty dict on failure.

    Uses the comparison endpoint to score up to 5 names at a time.
    """
    if not names:
        return {}
    name = normalize_state(state)
    geo = US_STATE_TO_ISO.get(name) if name else 'US'
    out: dict[str, int] = {}
    # Trends comparison caps at 5 terms. Batch.
    for i in range(0, len(names), 5):
        batch = names[i:i+5]
        # Step 1: token
        widget = _get_json('https://trends.google.com/trends/api/explore', params={
            'hl': 'en-US',
            'tz': '-300',
            'req': json.dumps({
                'comparisonItem': [{'keyword': k, 'geo': geo, 'time': 'now 7-d'} for k in batch],
                'category': 0,
                'property': '',
            }),
        })
        if not widget:
            for k in batch:
                out.setdefault(k, 0)
            continue
        # Strip the )]}' Trends prefix if present
        if isinstance(widget, str):
            try:
                widget = json.loads(widget.lstrip(")]}',\n "))
            except Exception:
                continue
        # Find TIMESERIES widget token
        try:
            widgets = widget.get('widgets', [])
            ts = next((w for w in widgets if w.get('id') == 'TIMESERIES'), None)
            if not ts:
                continue
            token = ts.get('token')
            req = ts.get('request')
            if not (token and req):
                continue
            data = _get_json('https://trends.google.com/trends/api/widgetdata/multiline', params={
                'hl': 'en-US',
                'tz': '-300',
                'req': json.dumps(req),
                'token': token,
            })
            if not data:
                continue
            if isinstance(data, str):
                data = json.loads(data.lstrip(")]}',\n "))
            timeline = data.get('default', {}).get('timelineData', []) or []
            # Each row has 'value' = [v0, v1, ...] aligned to batch
            sums = [0] * len(batch)
            for row in timeline:
                for idx, v in enumerate(row.get('value', [])[:len(batch)]):
                    sums[idx] += int(v or 0)
            n = max(1, len(timeline))
            for idx, k in enumerate(batch):
                out[k] = int(round(sums[idx] / n))
        except Exception as e:
            logger.debug("trends politician batch %s failed: %s", batch, e)
            continue
    return out


# ── 2. GDELT 2.0 DOC API (free, no key) ─────────────────────────────────────
# https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
# Pulls article-level news mentions with location + tone + themes. We use it
# for Card E (top political articles) and as a secondary signal for Card D.

_GDELT_DOC = "https://api.gdeltproject.org/api/v2/doc/doc"

# A narrow ThemeID set for political-policy articles. Drops sports/celebrity.
_GDELT_POLITICAL_THEMES = [
    'TAX_POLICY', 'ELECTION', 'LEGISLATION', 'GOV_POLITICS',
    'IMMIGRATION', 'HEALTHCARE', 'HEALTH_INSURANCE', 'POVERTY',
    'UNEMPLOYMENT', 'INFLATION', 'ECON_HOUSING', 'EDUCATION_POLICY',
    'CRIME', 'ENERGY_POLICY', 'CLIMATE_CHANGE', 'ABORTION',
    'INFRASTRUCTURE', 'TRADE_POLICY', 'GUN_CONTROL', 'WELFARE',
]


def gdelt_political_articles(state: Optional[str] = None, lookback_days: int = 7, limit: int = 75) -> list[dict]:
    """Top political articles by reach in the geo over the lookback window.

    Returns `[{url, title, source, tone, language, seendate, geo}, ...]`.
    Empty list on failure.
    """
    name = normalize_state(state)
    # GDELT uses "sourcecountry:US" + free-text location filters. We use the
    # full state name in the query to bias toward state-relevant articles.
    query_parts = ['sourcecountry:US']
    if name:
        query_parts.append(f'"{name}"')
    # OR-join the theme allowlist
    themes_clause = '(' + ' OR '.join(f'theme:{t}' for t in _GDELT_POLITICAL_THEMES) + ')'
    query_parts.append(themes_clause)
    q = ' '.join(query_parts)

    start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime('%Y%m%d000000')
    end   = datetime.now(timezone.utc).strftime('%Y%m%d235959')

    data = _get_json(_GDELT_DOC, params={
        'query': q,
        'mode':  'ArtList',
        'maxrecords': max(10, min(250, limit)),
        'format': 'json',
        'sort':   'HybridRel',
        'startdatetime': start,
        'enddatetime':   end,
    })
    if not data or not isinstance(data, dict):
        return []
    arts = data.get('articles') or []
    out = []
    for a in arts[:limit]:
        url = a.get('url') or ''
        title = (a.get('title') or '').strip()
        if not url or not title:
            continue
        out.append({
            'url':      url,
            'title':    title[:280],
            'source':   a.get('domain') or a.get('sourceCommonName') or '',
            'tone':     float(a.get('tone') or 0.0),
            'language': a.get('language') or 'English',
            'seendate': a.get('seendate') or '',
            'social_image': a.get('socialimage') or '',
        })
    return out


def gdelt_politician_mentions(names: list[str], state: Optional[str] = None,
                              lookback_days: int = 7) -> dict[str, int]:
    """How many GDELT articles mention each politician in the geo+window."""
    if not names:
        return {}
    name = normalize_state(state)
    start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime('%Y%m%d000000')
    end   = datetime.now(timezone.utc).strftime('%Y%m%d235959')

    out: dict[str, int] = {}
    for pol in names:
        q = f'sourcecountry:US "{pol}"'
        if name:
            q += f' "{name}"'
        data = _get_json(_GDELT_DOC, params={
            'query': q,
            'mode':  'TimelineVolRaw',
            'format': 'json',
            'startdatetime': start,
            'enddatetime':   end,
        })
        total = 0
        if isinstance(data, dict):
            for series in (data.get('timeline') or []):
                for d in (series.get('data') or []):
                    try:
                        total += int(d.get('value') or 0)
                    except Exception:
                        continue
        out[pol] = total
    return out


# ── 3. WIKIPEDIA PAGEVIEWS (free, no key) ───────────────────────────────────
# Pageviews API is excellent baseline interest signal when panel sample
# is thin in a state. Aggregated, no PII.

_WIKI_PAGEVIEWS = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia/all-access/user/{title}/daily/{start}/{end}"
)


def wikipedia_pageviews(titles: list[str], lookback_days: int = 7) -> dict[str, int]:
    """Total English-Wikipedia pageviews per article over the window.

    Wikipedia doesn't expose per-US-state pageviews on the public REST API,
    so this is national-level. Used as a tiebreaker / smoothing signal for
    Card D (politicians) and politician-interest scoring.
    """
    if not titles:
        return {}
    start_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days + 1)
    end_dt   = datetime.now(timezone.utc) - timedelta(days=1)
    start = start_dt.strftime('%Y%m%d')
    end   = end_dt.strftime('%Y%m%d')

    out: dict[str, int] = {}
    for t in titles:
        if not t:
            continue
        page = urllib.parse.quote(t.replace(' ', '_'), safe='')
        url = _WIKI_PAGEVIEWS.format(title=page, start=start, end=end)
        data = _get_json(url)
        total = 0
        if isinstance(data, dict):
            for item in (data.get('items') or []):
                try:
                    total += int(item.get('views') or 0)
                except Exception:
                    continue
        out[t] = total
    return out


# ── 4. Convenience: best-effort fetch-all-in-parallel ───────────────────────

# Hard wall-clock budget per source. Even on slow days the dashboard never
# waits longer than _EXT_BUDGET_S total on external fetches.
_EXT_BUDGET_S       = int(os.environ.get('BLUE_IQ_EXT_BUDGET_S', '25'))
_EXT_MAX_POLS       = int(os.environ.get('BLUE_IQ_EXT_MAX_POLS', '12'))  # cap politicians fanned out per call


def fetch_all_external(state: Optional[str], lookback_days: int,
                        politician_names: Iterable[str]) -> dict:
    """Pull every external source IN PARALLEL via a small ThreadPoolExecutor.

    Returns a dict of partial results. Missing keys mean that source failed.

    Each source is bounded at the HTTP layer by `_HTTP_TIMEOUT_S` per request,
    and the WHOLE call is bounded by `_EXT_BUDGET_S` (default 25s). Any source
    that doesn't finish in time gets canceled and returns its default empty
    value. We use `cancel_futures=True` on shutdown so the executor doesn't
    block on stragglers (which was the prior 3-minute hang).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutTimeoutError
    politician_names = list(politician_names or [])[:_EXT_MAX_POLS]  # cap fan-out

    tasks = {
        'google_trends_top':         lambda: trends_top_issues(state=state, lookback_days=lookback_days),
        'google_trends_politicians': lambda: trends_politician_interest(politician_names, state=state),
        'gdelt_articles':            lambda: gdelt_political_articles(state=state, lookback_days=lookback_days),
        'gdelt_politician_mentions': lambda: gdelt_politician_mentions(politician_names, state=state, lookback_days=lookback_days),
        'wiki_pageviews':            lambda: wikipedia_pageviews(politician_names, lookback_days=lookback_days),
    }

    defaults = {
        'google_trends_top': [],
        'google_trends_politicians': {},
        'gdelt_articles': [],
        'gdelt_politician_mentions': {},
        'wiki_pageviews': {},
    }

    out: dict = dict(defaults)
    ex = ThreadPoolExecutor(max_workers=5, thread_name_prefix='blueiq-ext')
    futures = {ex.submit(fn): key for key, fn in tasks.items()}
    deadline = time.monotonic() + _EXT_BUDGET_S
    try:
        for fut in as_completed(futures, timeout=_EXT_BUDGET_S):
            key = futures[fut]
            remaining = max(0.1, deadline - time.monotonic())
            try:
                out[key] = fut.result(timeout=remaining)
            except Exception as e:
                logger.debug("external source %s failed: %s", key, e)
                out[key] = defaults[key]
    except FutTimeoutError:
        slow = [futures[f] for f in futures if not f.done()]
        logger.info("external_signals: %d source(s) exceeded %ds budget: %s",
                    len(slow), _EXT_BUDGET_S, slow)
    finally:
        # Don't wait for slow stragglers; cancel them so the dashboard returns.
        ex.shutdown(wait=False, cancel_futures=True)
    return out
