"""Daily encyclopedia audience for every IQ Rankers entity.

The scraped boards cover roughly 4,200 items a day. The ranker tracks
about 2,250 entities, most of them actors, and on any given day only
about one in fifteen of them is on a chart, in the news, or trending in
search. Scoring the board off those surfaces alone leaves most of the
leaderboard with nothing behind it.

Encyclopedia pageviews close that gap honestly. Almost every notable
person, brand and title has an English Wikipedia article, and the
Wikimedia Foundation publishes that article's pageviews per day. It is
a real, measured, daily count of people reading about the entity, for
essentially the whole ranker population, with history back far enough
to build a proper trailing baseline.

Two conversions, both anchored, both applied here so nothing downstream
has to guess:

  1. Global to US. Wikimedia's own pageviews-by-country API reports
     2,690,939,000 English Wikipedia pageviews from the United States in
     August 2026 against 6,497,616,866 total reader pageviews for the
     same month, so the United States is 41.4% of English Wikipedia
     readership. Refreshed monthly by `refresh_us_share`, with the
     measured figure as the fallback.
  2. Views to people. One article read is one person per day. Unlike a
     song stream or a video view, nobody reads the same encyclopedia
     article ten times in an afternoon, so pageviews and readers are the
     same count here. This is the same footing the news estimates
     already stand on, where the unit is "daily US readers".

Nothing here reads the clickstream. See
`.cursor/rules/trends-rankers-never-clickstream.mdc`.

Article resolution
------------------
Each entity is resolved once against the MediaWiki API with redirects
followed, and the mapping is cached in S3. Disambiguation pages are
rejected outright, which is what stops a profile called "Max" or
"Power" from inheriting the pageviews of a list of unrelated meanings.
Talent entities additionally have to resolve to something that reads as
a person, so an actor never picks up the article for a film of the same
name. An entity that does not resolve is left with no encyclopedia
signal rather than a wrong one.

Usage on Hetzner:

    python3 -m scripts.trends_scrapers.iq_ranker_wiki_audience --resolve
    python3 -m scripts.trends_scrapers.iq_ranker_wiki_audience
    python3 -m scripts.trends_scrapers.iq_ranker_wiki_audience \
        --date 2026-09-14 --days 35
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone

import boto3

_HERE = os.path.dirname(os.path.abspath(__file__))
_BGWEBAPP = os.path.abspath(os.path.join(_HERE, '..', '..'))
if _BGWEBAPP not in sys.path:
    sys.path.insert(0, _BGWEBAPP)

import iq_ranker_signals as sig  # noqa: E402
import iq_rankers as R           # noqa: E402

BUCKET = os.environ.get('IQR_SIGNAL_BUCKET', sig.S3_DEFAULT_BUCKET)
CACHE_KEY = 'system/s3_cache.json'
MAP_KEY = f'{sig.SNAPSHOT_PREFIX}/system/iq_ranker_wiki_map.json'
SHARE_KEY = f'{sig.SNAPSHOT_PREFIX}/system/iq_ranker_wiki_us_share.json'
DAY_FILENAME = 'iq_ranker_wiki_audience.json'

UA = 'CrosswalkTrends/1.0 (jenna@crosswalknyc.com)'
_MW_API = 'https://en.wikipedia.org/w/api.php'
_PV_ARTICLE = ('https://wikimedia.org/api/rest_v1/metrics/pageviews/'
               'per-article/en.wikipedia/all-access/user/{title}/daily/'
               '{start}/{end}')
_PV_COUNTRY = ('https://wikimedia.org/api/rest_v1/metrics/pageviews/'
               'top-by-country/en.wikipedia/all-access/{ym}')
_PV_TOTAL = ('https://wikimedia.org/api/rest_v1/metrics/pageviews/'
             'aggregate/en.wikipedia/all-access/user/monthly/'
             '{start}/{end}')

# Measured 2026-08 from the two Wikimedia endpoints above.
US_SHARE_FALLBACK = 0.414

# A talent profile must not inherit the article for a film, album or
# place that happens to share its name.
_NON_PERSON_DESC = re.compile(
    r'\b(film|movie|album|song|single|tv series|television series|'
    r'video game|novel|book|comic|company|corporation|brand|city|town|'
    r'village|river|county|band|album by|episode|magazine|newspaper)\b',
    re.I)


def _get_json(url: str, timeout: int = 20, attempts: int = 4):
    """GET with backoff.

    The Wikimedia REST API throttles per IP and answers 429 well before
    it answers slowly. A silent failure here would look exactly like an
    entity with no encyclopedia audience, so every retryable status is
    retried rather than swallowed.
    """
    req = urllib.request.Request(url, headers={'User-Agent': UA,
                                               'Accept': 'application/json'})
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode('utf-8', errors='ignore'))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None          # article genuinely has no data
            if e.code not in (429, 500, 502, 503, 504) or i == attempts - 1:
                return None
        except Exception:
            if i == attempts - 1:
                return None
        time.sleep(1.5 * (2 ** i))
    return None


def _s3():
    return boto3.client('s3')


def _get_s3_json(s3, key: str):
    try:
        return json.loads(s3.get_object(Bucket=BUCKET, Key=key)['Body'].read())
    except Exception:
        return None


def _put_s3_json(s3, key: str, payload) -> None:
    s3.put_object(Bucket=BUCKET, Key=key,
                  Body=json.dumps(payload).encode(),
                  ContentType='application/json')


# ---------------------------------------------------------------------------
# US share of English Wikipedia readership
# ---------------------------------------------------------------------------


def refresh_us_share(s3) -> float:
    """Recompute the US share from Wikimedia's own published figures.

    Uses the most recent complete month. Falls back to the cached value,
    then to the measured constant, so a failed refresh never changes the
    numbers on the page.
    """
    cached = _get_s3_json(s3, SHARE_KEY) or {}
    today = datetime.now(timezone.utc).date()
    first = today.replace(day=1)
    last_month = first - timedelta(days=1)
    ym = last_month.strftime('%Y/%m')
    if cached.get('month') == ym and cached.get('us_share'):
        return float(cached['us_share'])

    by_country = _get_json(_PV_COUNTRY.format(ym=ym))
    start = last_month.replace(day=1).strftime('%Y%m%d') + '00'
    end = last_month.strftime('%Y%m%d') + '00'
    total = _get_json(_PV_TOTAL.format(start=start, end=end))
    try:
        us = 0
        for c in by_country['items'][0]['countries']:
            if (c.get('country') or '').upper() == 'US':
                us = int(c['views_ceil'])
                break
        all_views = int(total['items'][0]['views'])
        if us > 0 and all_views > 0:
            share = us / all_views
            if 0.2 <= share <= 0.7:
                _put_s3_json(s3, SHARE_KEY, {
                    'month': ym, 'us_share': round(share, 4),
                    'us_pageviews': us, 'total_pageviews': all_views,
                    'refreshed_at': datetime.now(timezone.utc).isoformat()})
                return share
    except Exception:
        pass
    return float(cached.get('us_share') or US_SHARE_FALLBACK)


# ---------------------------------------------------------------------------
# Entity to article
# ---------------------------------------------------------------------------


def _resolve_batch(names: list[str]) -> dict[str, dict]:
    """Resolve up to 50 names through the MediaWiki API in one call."""
    params = {
        'action': 'query', 'format': 'json', 'redirects': '1',
        'prop': 'pageprops|description', 'ppprop': 'disambiguation',
        'titles': '|'.join(names),
    }
    data = _get_json(_MW_API + '?' + urllib.parse.urlencode(params))
    if not isinstance(data, dict):
        return {}
    q = data.get('query') or {}
    # normalized + redirects chains map the requested spelling to the
    # title the API actually answered on.
    forward: dict[str, str] = {}
    for hop in ('normalized', 'redirects'):
        for r in q.get(hop) or []:
            forward[r.get('from') or ''] = r.get('to') or ''

    def final(name: str) -> str:
        seen = set()
        cur = name
        while cur in forward and cur not in seen:
            seen.add(cur)
            cur = forward[cur]
        return cur

    pages = {}
    for _pid, page in (q.get('pages') or {}).items():
        pages[page.get('title') or ''] = page
    out: dict[str, dict] = {}
    for name in names:
        page = pages.get(final(name))
        if not page or 'missing' in page:
            continue
        out[name] = {
            'article': page.get('title') or '',
            'description': page.get('description') or '',
            'disambiguation': 'disambiguation' in (page.get('pageprops') or {}),
        }
    return out


def resolve_articles(s3, entities: list[dict], *, force: bool = False,
                     workers: int = 6) -> dict:
    """Entity subject to Wikipedia article, cached in S3.

    Cache entries record a miss as well as a hit so a name that does not
    resolve is not retried every night.
    """
    cache = ({} if force else (_get_s3_json(s3, MAP_KEY) or {}))
    # Several subjects share a base name: a profile and its derived cuts
    # ("Vin Diesel", "Vin Diesel - 2025 Avid Male Fan") all resolve to
    # the same article, so the name maps to a LIST of subjects. Keying
    # this by name alone silently dropped every duplicate.
    by_name: dict[str, list[dict]] = defaultdict(list)
    for job in entities:
        subj = job.get('profile_subject') or ''
        if not subj or subj in cache:
            continue
        name = (job.get('display_name') or job.get('project_name')
                or subj).split(' - ', 1)[0].strip()
        if not name:
            continue
        sub = R.normalize_subcategory(job.get('category'))
        by_name[name].append({'subject': subj,
                              'master': R.get_master_category(sub)})
    if not by_name:
        return cache

    todo = sorted(by_name)
    print(f'[wiki_audience] resolving {len(todo)} name(s) across '
          f'{sum(len(v) for v in by_name.values())} subject(s)')

    def _run(names: list[str]) -> dict[str, dict]:
        results: dict[str, dict] = {}
        batches = [names[i:i + 50] for i in range(0, len(names), 50)]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_resolve_batch, b) for b in batches]
            for f in as_completed(futs):
                try:
                    results.update(f.result() or {})
                except Exception:
                    pass
        return results

    results = _run(todo)

    # Apostrophes never survive into a profile name (they never appear in
    # a clickstream slug either), so "Josh O Connor" and "Conan O Brien"
    # arrive spelled without one and miss their own article. Put the
    # apostrophe back for the patterns where it is the only thing wrong
    # and try those names once more.
    retry_map: dict[str, str] = {}
    for name in todo:
        if name in results:
            continue
        fixed = re.sub(r"\b([OD]) ([A-Z][a-z])", r"\1'\2", name)
        if fixed != name and fixed not in retry_map:
            retry_map[fixed] = name
    if retry_map:
        for fixed, got in _run(sorted(retry_map)).items():
            results[retry_map[fixed]] = got
        print(f'[wiki_audience] apostrophe retry recovered '
              f'{sum(1 for f in retry_map if retry_map[f] in results)} name(s)')

    kept = rejected = 0
    for name, subjects in by_name.items():
        got = results.get(name)
        if not got:
            verdict = {'article': None, 'reason': 'not_found'}
        elif got['disambiguation']:
            # A disambiguation page is a list of unrelated meanings. Its
            # pageviews belong to none of them.
            verdict = {'article': None, 'reason': 'disambiguation',
                       'candidate': got['article']}
        else:
            verdict = {'article': got['article'],
                       'description': got['description'] or ''}
        for info in subjects:
            v = dict(verdict)
            desc = v.get('description') or ''
            if (v.get('article') and info['master'] == 'TALENT'
                    and _NON_PERSON_DESC.search(desc)):
                v = {'article': None, 'reason': 'not_a_person',
                     'candidate': verdict['article'], 'description': desc}
            cache[info['subject']] = v
            if v.get('article'):
                kept += 1
            else:
                rejected += 1

    _put_s3_json(s3, MAP_KEY, cache)
    print(f'[wiki_audience] resolved={kept} unresolved={rejected} '
          f'cached={len(cache)}')
    return cache


# ---------------------------------------------------------------------------
# Daily pageviews
# ---------------------------------------------------------------------------


def fetch_series(article: str, start: str, end: str) -> dict[str, int]:
    """Daily global pageviews for one article across an inclusive range."""
    slug = urllib.parse.quote(article.replace(' ', '_'), safe='')
    url = _PV_ARTICLE.format(title=slug,
                             start=start.replace('-', ''),
                             end=end.replace('-', ''))
    data = _get_json(url)
    out: dict[str, int] = {}
    if not isinstance(data, dict):
        return out
    for row in data.get('items') or []:
        ts = str(row.get('timestamp') or '')[:8]
        if len(ts) != 8:
            continue
        day = f'{ts[0:4]}-{ts[4:6]}-{ts[6:8]}'
        try:
            out[day] = int(row.get('views') or 0)
        except (TypeError, ValueError):
            continue
    return out


def collect(s3, *, start: str, end: str, workers: int = 10) -> dict:
    """Pull the daily series for every resolved article, once."""
    cache = _get_s3_json(s3, MAP_KEY) or {}
    resolved = {subj: v['article'] for subj, v in cache.items()
                if isinstance(v, dict) and v.get('article')}
    print(f'[wiki_audience] {len(resolved)} resolved article(s), '
          f'{start} to {end}')

    series: dict[str, dict[str, int]] = {}
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_series, art, start, end): subj
                for subj, art in resolved.items()}
        for f in as_completed(futs):
            subj = futs[f]
            done += 1
            if done % 500 == 0:
                print(f'[wiki_audience]   {done}/{len(resolved)} '
                      f'({round(time.time() - t0)}s)')
            try:
                got = f.result()
            except Exception:
                got = {}
            if got:
                series[subj] = got

    # A series that came back empty is almost always a throttled request
    # rather than an article with no readers, and an empty series is
    # indistinguishable on the page from an entity nobody looked up. Go
    # back for them once, slowly.
    gaps = [s for s in resolved if s not in series]
    if gaps:
        print(f'[wiki_audience] retrying {len(gaps)} empty series')
        with ThreadPoolExecutor(max_workers=2) as ex:
            futs = {ex.submit(fetch_series, resolved[s], start, end): s
                    for s in gaps}
            for f in as_completed(futs):
                try:
                    got = f.result()
                except Exception:
                    got = {}
                if got:
                    series[futs[f]] = got

    print(f'[wiki_audience] fetched {len(series)} of {len(resolved)} series '
          f'in {round(time.time() - t0)}s')
    return series


def write_days(s3, series: dict, *, start: str, end: str,
               us_share: float) -> list[dict]:
    """One snapshot per day, keyed by entity."""
    by_day: dict[str, dict] = defaultdict(dict)
    for subj, days in series.items():
        for day, views in days.items():
            if views > 0:
                by_day[day][subj] = views

    cache = _get_s3_json(s3, MAP_KEY) or {}
    out = []
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    cur = d0
    while cur <= d1:
        day = cur.isoformat()
        items = by_day.get(day) or {}
        payload = {
            'day': day,
            'us_share': round(us_share, 4),
            'count': len(items),
            'items': {
                subj: {
                    'article': (cache.get(subj) or {}).get('article') or '',
                    'views_global': v,
                    # One article read is one reader that day, so the US
                    # share of English Wikipedia readership converts
                    # pageviews straight to US readers.
                    'us_readers': int(round(v * us_share)),
                }
                for subj, v in items.items()
            },
        }
        key = f'{sig.SNAPSHOT_PREFIX}/{day}/{DAY_FILENAME}'
        _put_s3_json(s3, key, payload)
        out.append({'day': day, 'entities': len(items)})
        cur += timedelta(days=1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', help='last ISO date (default: yesterday)')
    ap.add_argument('--days', type=int, default=2,
                    help='how many days back from --date to write')
    ap.add_argument('--resolve', action='store_true',
                    help='resolve articles for any new entity and exit')
    ap.add_argument('--force-resolve', action='store_true',
                    help='rebuild the whole entity to article map')
    # Six is the highest concurrency the REST API tolerates from one IP
    # without throttling most of the run into retries.
    ap.add_argument('--workers', type=int, default=6)
    args = ap.parse_args()

    s3 = _s3()
    body = s3.get_object(Bucket=BUCKET, Key=CACHE_KEY)['Body'].read()
    entities = list(R._iter_profile_jobs((json.loads(body) or {}).get('jobs') or []))
    print(f'[wiki_audience] {len(entities)} ranker entities')

    resolve_articles(s3, entities, force=args.force_resolve)
    if args.resolve:
        return 0

    us_share = refresh_us_share(s3)
    print(f'[wiki_audience] US share of English Wikipedia readership: '
          f'{us_share:.3f}')

    end = args.date or (date.today() - timedelta(days=1)).isoformat()
    start = (date.fromisoformat(end)
             - timedelta(days=max(1, args.days) - 1)).isoformat()
    series = collect(s3, start=start, end=end, workers=args.workers)
    for row in write_days(s3, series, start=start, end=end, us_share=us_share):
        print(json.dumps(row))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
