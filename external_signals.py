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

_TRENDS_DAILY_TRENDS = "https://trends.google.com/trends/api/dailytrends"
_TRENDS_REALTIME    = "https://trends.google.com/trends/api/realtimetrends"

# Google's geo codes for daily/realtime trends use country (US) + state (US-XX).
def trends_top_issues(state: Optional[str] = None, lookback_days: int = 7) -> list[dict]:
    """Top trending search topics in the geo, filtered (downstream) to political.

    Returns `[{ 'term': str, 'score': int, 'related': [str, ...] }, ...]` sorted
    by score desc. Empty list on failure or no signal.

    NOTE: Google's daily-trends endpoint returns ALL trending topics — political
    filtering is the caller's job (Blue IQ's issue-bucket classifier handles it).
    """
    name = normalize_state(state)
    geo = US_STATE_TO_ISO.get(name) if name else 'US'

    out: list[dict] = []
    # daily trends gives a per-day list; we request the last `lookback_days`.
    # Trends only goes back ~30d on this endpoint.
    today = datetime.now(timezone.utc).strftime('%Y%m%d')
    raw = _get_json(_TRENDS_DAILY_TRENDS, params={
        'hl': 'en-US',
        'tz': '-300',
        'geo': geo,
        'ns': 15,
        'ed': today,
    })
    if not raw:
        return out

    # Trends prepends ")]}'," to its JSON. Some clients strip it; ours may not.
    if isinstance(raw, str):
        try:
            raw = json.loads(raw.lstrip(")]}',\n "))
        except Exception:
            return out

    try:
        days = raw.get('default', {}).get('trendingSearchesDays', []) or []
        for day in days[:lookback_days]:
            for t in day.get('trendingSearches', []) or []:
                title = (t.get('title', {}) or {}).get('query') or ''
                if not title:
                    continue
                # traffic looks like "200K+" — convert to int loosely
                traffic_raw = (t.get('formattedTraffic') or '').strip()
                score = _parse_traffic(traffic_raw)
                related = [
                    (a.get('query') or '') for a in (t.get('relatedQueries') or [])
                    if a.get('query')
                ]
                out.append({'term': title, 'score': score, 'related': related})
    except Exception as e:
        logger.debug("trends parse failed: %s", e)
        return []
    # de-dupe by term, keep max score
    by_term: dict[str, dict] = {}
    for row in out:
        key = row['term'].lower()
        if key not in by_term or by_term[key]['score'] < row['score']:
            by_term[key] = row
    out = sorted(by_term.values(), key=lambda x: -x['score'])
    return out


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

def fetch_all_external(state: Optional[str], lookback_days: int,
                        politician_names: Iterable[str]) -> dict:
    """Pull every external source IN PARALLEL via a small ThreadPoolExecutor.

    Returns a dict of partial results. Missing keys mean that source failed.
    Bounded to 8s per source (see `_HTTP_TIMEOUT_S`) so the dashboard never
    waits more than that on any single source even on the slowest network day.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    politician_names = list(politician_names or [])

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
    # 5 workers — one per source — so all five run concurrently.
    with ThreadPoolExecutor(max_workers=5, thread_name_prefix='blueiq-ext') as ex:
        futures = {ex.submit(fn): key for key, fn in tasks.items()}
        for fut in as_completed(futures, timeout=30):
            key = futures[fut]
            try:
                out[key] = fut.result(timeout=15)
            except Exception as e:
                logger.debug("external source %s failed: %s", key, e)
                out[key] = defaults[key]
    return out
