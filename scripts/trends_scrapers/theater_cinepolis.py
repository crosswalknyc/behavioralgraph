"""
Cinepolis USA card: the films with the most showtimes today across
every Cinepolis theater in the US.

cinepolisusa.com is a browser app over one GraphQL endpoint, behind
Cloudflare:

    endpoint   POST www.cinepolisusa.com/graphql
               headers circuit-id 89 (Cinepolis USA), site-id, client-type consumer
    theaters   { currentCircuit { sites { ... } } }
               (every site; the library entry, the test lab and the
               chain-wide site are not theaters and are skipped)
    showtimes  showingsForDate(date, siteIds) { data { ... } count }
               one theater per call: an answer stops at 300 showings,
               which a single theater never reaches in a day
    posters    indy-systems.imgix.net/<posterImage>
    film page  <theater page>/movie/<urlSlug>

Cloudflare turns away a plain Python request to the endpoint but
answers one that carries Chrome's TLS fingerprint (curl_cffi), so no
browser is needed. When curl_cffi is missing or is turned away, the
same queries run from inside the site's own page in headless Chrome.

showingsForDate keeps the day's earlier shows, so the count is the
same at any hour. A film is listed once per theater; each listing
carries the theater's display order, and the lowest one breaks ties.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from .theater_common import (DEFAULT_LIMIT, clean_title, looks_walled,
                             rank_by_showtimes, today_iso)

logger = logging.getLogger(__name__)

KEY = 'cinepolis'
LABEL = 'Cinépolis'
COOKIE_DOMAIN = None

TZ = 'America/Chicago'
SITE = 'https://www.cinepolisusa.com'
GRAPHQL_URL = SITE + '/graphql'
CIRCUIT_ID = '89'
CIRCUIT_SITE_ID = '175'
POSTER_URL = ('https://indy-systems.imgix.net/{key}'
              '?fit=crop&w=400&h=600&fm=jpeg&auto=format,compress&cs=origin')

SITES_QUERY = ('{ currentCircuit { id name sites { id name hostname isLibrary '
               'city state timeZone deletedAt } } }')
SHOWINGS_QUERY = ('query ($date: String, $siteIds: [ID]) { showingsForDate(date: $date, '
                  'siteIds: $siteIds) { data { id time published private movie { id name '
                  'urlSlug posterImage displayOrder } } count } }')
RESULT_CAP = 300

WORKERS = 4
PAUSE_S = 0.15
IMPERSONATE = 'chrome131'
_THEATER_URL_RE = re.compile(r'^https://www\.cinepolisusa\.com/([^/?#]+)$', re.I)
_UNLISTED_ORDER = 9999


class _Walled(Exception):
    """Cloudflare turned the request away."""


class _NoClient(Exception):
    """curl_cffi is not installed on this machine."""


def _plain_dashes(s: str) -> str:
    return (s or '').replace('\u2013', '-').replace('\u2014', '-')


def _headers(site_id: str, referer: str) -> dict:
    return {
        'circuit-id': CIRCUIT_ID,
        'site-id': site_id,
        'client-type': 'consumer',
        'is-electron-mode': 'false',
        'accept': 'application/graphql-response+json,application/json;q=0.9',
        'content-type': 'application/json',
        'origin': SITE,
        'referer': referer,
    }


def _showings_body(site_id: str, day: str) -> dict:
    return {'query': SHOWINGS_QUERY, 'variables': {'date': day, 'siteIds': [site_id]}}


# ---------------------------------------------------------------------------
# Parsers (no network)
# ---------------------------------------------------------------------------
def parse_sites(payload: Any) -> list[dict]:
    """[{'id', 'name', 'url'}] for the circuit's real theaters."""
    circuit = ((payload or {}).get('data') or {}).get('currentCircuit') if isinstance(payload, dict) else None
    out, seen = [], set()
    for s in (circuit or {}).get('sites') or []:
        if not isinstance(s, dict) or not s.get('id') or str(s['id']) in seen:
            continue
        if s.get('isLibrary') or s.get('deletedAt') or not s.get('city'):
            continue
        url = str(s.get('hostname') or '').rstrip('/')
        if not _THEATER_URL_RE.match(url):
            continue
        seen.add(str(s['id']))
        out.append({'id': str(s['id']), 'name': clean_title(s.get('name') or ''), 'url': url})
    return out


def parse_showings(payload: Any) -> Optional[tuple[list[dict], bool]]:
    """(public showings, answer was cut at the cap), or None when the
    body is not a showings answer."""
    result = ((payload or {}).get('data') or {}).get('showingsForDate') if isinstance(payload, dict) else None
    if not isinstance(result, dict) or not isinstance(result.get('data'), list):
        return None
    out, seen = [], set()
    for s in result['data']:
        if not isinstance(s, dict) or s.get('published') is False or s.get('private'):
            continue
        movie = s.get('movie') or {}
        title = clean_title(movie.get('name') or '')
        if not title:
            continue
        sid = s.get('id')
        if sid:
            if sid in seen:
                continue
            seen.add(sid)
        order = movie.get('displayOrder')
        out.append({
            'title': title,
            'slug': str(movie.get('urlSlug') or movie.get('id') or ''),
            'poster': str(movie.get('posterImage') or ''),
            'order': order if isinstance(order, int) else _UNLISTED_ORDER,
        })
    return out, len(result['data']) >= RESULT_CAP


def poster_url(key: str) -> str:
    if not key:
        return ''
    return key if key.startswith('http') else POSTER_URL.format(key=key)


def build_rows(per_site: dict[str, tuple[dict, list[dict]]]) -> list[dict]:
    """One row per listed title, the link going to the film's page at
    the theater that plays it most."""
    agg: dict[str, dict] = {}
    for sid, (site, showings) in per_site.items():
        for s in showings:
            a = agg.setdefault(s['title'], {'shows': 0, 'by_site': Counter(), 'slug': {},
                                            'poster': {}, 'order': _UNLISTED_ORDER})
            a['shows'] += 1
            a['by_site'][sid] += 1
            if s['slug']:
                a['slug'].setdefault(sid, (site['url'], s['slug']))
            if s['poster']:
                a['poster'].setdefault(sid, s['poster'])
            a['order'] = min(a['order'], s['order'])
    rows = []
    for title, a in agg.items():
        ranked = [sid for sid, _ in a['by_site'].most_common()]
        top = next((sid for sid in ranked if sid in a['slug']), None)
        poster = next((a['poster'][sid] for sid in ranked if sid in a['poster']), '')
        rows.append({
            'title': _plain_dashes(title),
            'showtimes': a['shows'],
            'url': '{}/movie/{}'.format(*a['slug'][top]) if top else SITE + '/',
            'image': poster_url(poster),
            'theater_ids': set(a['by_site']),
            'site_order': a['order'],
        })
    rows.sort(key=lambda r: -r['showtimes'])
    return rows


# ---------------------------------------------------------------------------
# Network: curl_cffi first
# ---------------------------------------------------------------------------
_local = threading.local()


def _client():
    """A per-thread curl_cffi session, or None when curl_cffi is missing."""
    s = getattr(_local, 'sess', None)
    if s is None:
        try:
            from curl_cffi import requests as ccr  # type: ignore
        except Exception:
            return None
        s = _local.sess = ccr.Session(impersonate=IMPERSONATE)
    return s


def _post(body: dict, site_id: str, referer: str) -> Optional[dict]:
    """Parsed answer, or None. Raises _Walled when Cloudflare turned the
    request away, _NoClient when curl_cffi is missing."""
    sess = _client()
    if sess is None:
        raise _NoClient('curl_cffi is not installed')
    for attempt in range(2):
        try:
            r = sess.post(GRAPHQL_URL, json=body, headers=_headers(site_id, referer), timeout=30)
        except Exception as e:
            logger.info("cinepolis: request failed (%s)", str(e)[:120])
            time.sleep(1.5 * (attempt + 1))
            continue
        text = r.text or ''
        if r.status_code in (403, 429) or looks_walled(text):
            raise _Walled(f'answered {r.status_code}')
        try:
            payload = json.loads(text)
        except Exception:
            logger.info("cinepolis: answer %s was not JSON", r.status_code)
            payload = None
        if r.status_code == 200 and isinstance(payload, dict):
            if payload.get('errors'):
                logger.info("cinepolis: query error %s", str(payload['errors'])[:200])
            return payload
        logger.info("cinepolis: answered %s (%s)", r.status_code, text[:160])
        time.sleep(1.5 * (attempt + 1))
    return None


def _read_site(site: dict, day: str) -> tuple[str, Optional[tuple[list[dict], bool]]]:
    try:
        payload = _post(_showings_body(site['id'], day), site['id'], site['url'])
    finally:
        time.sleep(PAUSE_S)
    return site['id'], parse_showings(payload)


def _read_direct(day: str) -> tuple[list[dict], dict[str, tuple[list[dict], bool]]]:
    sites = parse_sites(_post({'query': SITES_QUERY, 'variables': {}},
                              CIRCUIT_SITE_ID, SITE + '/'))
    results: dict[str, tuple[list[dict], bool]] = {}
    if not sites:
        return sites, results
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for sid, parsed in ex.map(lambda s: _read_site(s, day), sites):
            if parsed is not None:
                results[sid] = parsed
    return sites, results


# ---------------------------------------------------------------------------
# Network: the same queries from inside the site's page, headless
# ---------------------------------------------------------------------------
_POST_JS = """
async ({url, requests, concurrency, timeoutMs}) => {
  const out = new Array(requests.length).fill(null);
  let next = 0;
  async function worker() {
    while (next < requests.length) {
      const i = next++;
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), timeoutMs);
      try {
        const r = await fetch(url, {method: 'POST', credentials: 'include', signal: ctl.signal,
                                    headers: requests[i].headers, body: requests[i].body});
        out[i] = r.ok ? await r.text() : null;
      } catch (e) {
        out[i] = null;
      } finally {
        clearTimeout(t);
      }
    }
  }
  await Promise.all(Array.from({length: Math.max(1, concurrency)}, worker));
  return out;
}
"""


def _page_headers(site_id: str) -> dict:
    h = _headers(site_id, SITE + '/')
    for k in ('origin', 'referer'):
        h.pop(k, None)  # the page supplies its own
    return h


def _read_in_browser(day: str) -> tuple[list[dict], dict[str, tuple[list[dict], bool]]]:
    try:
        from ._playwright import render_pages
    except Exception as e:
        logger.info("cinepolis: headless Chrome unavailable (%s)", e)
        return [], {}
    box: dict[str, Any] = {'sites': [], 'results': {}}

    def _run(page, requests: list[dict]) -> list[Optional[str]]:
        return page.evaluate(_POST_JS, {'url': GRAPHQL_URL, 'requests': requests,
                                        'concurrency': WORKERS, 'timeoutMs': 30000}) or []

    def _hook(page, _label):
        try:
            first = _run(page, [{'headers': _page_headers(CIRCUIT_SITE_ID),
                                 'body': json.dumps({'query': SITES_QUERY, 'variables': {}})}])
            sites = parse_sites(json.loads(first[0])) if first and first[0] else []
            box['sites'] = sites
            answers = _run(page, [{'headers': _page_headers(s['id']),
                                   'body': json.dumps(_showings_body(s['id'], day))}
                                  for s in sites])
            for s, text in zip(sites, answers):
                try:
                    parsed = parse_showings(json.loads(text)) if text else None
                except Exception:
                    parsed = None
                if parsed is not None:
                    box['results'][s['id']] = parsed
        except Exception as e:
            logger.info("cinepolis: in-page read failed (%s)", str(e)[:160])
        return json.dumps({'sites': len(box['sites']), 'read': len(box['results'])})

    try:
        render_pages([('cinepolis', SITE + '/')], hydration_wait_ms=12000,
                     wait_ms=3500, page_hook=_hook)
    except Exception as e:
        logger.info("cinepolis: headless render failed (%s)", str(e)[:160])
    return box['sites'], box['results']


# ---------------------------------------------------------------------------
def _fetch(limit: int, day: str, meta: dict) -> tuple[list[dict], dict]:
    walled = False
    try:
        sites, results = _read_direct(day)
    except (_Walled, _NoClient) as e:
        logger.info("cinepolis: direct read stopped (%s), trying headless Chrome", e)
        walled = isinstance(e, _Walled)
        sites, results = _read_in_browser(day)
        meta['headless'] = True
    meta['theaters'] = len(results)
    meta['theaters_listed'] = len(sites)
    if not sites:
        meta['blocked'] = walled
        meta['reason'] = 'the theater list could not be read'
        return [], meta
    if not results:
        meta['blocked'] = walled
        meta['reason'] = 'no theater schedule could be read'
        return [], meta
    capped = [sid for sid, (_, cut) in results.items() if cut]
    notes = []
    if len(results) < len(sites):
        notes.append(f'{len(sites) - len(results)} of {len(sites)} theater schedules could not be read')
    if capped:
        logger.warning("cinepolis: %d theater answers reached the %d showing cap", len(capped), RESULT_CAP)
        notes.append(f'{len(capped)} theater schedules may be incomplete')
    if notes:
        meta['reason'] = '; '.join(notes)
    site_by_id = {s['id']: s for s in sites}
    per_site = {sid: (site_by_id[sid], showings) for sid, (showings, _) in results.items()
                if sid in site_by_id}
    return rank_by_showtimes(build_rows(per_site), limit=limit), meta


def fetch(limit: int = DEFAULT_LIMIT) -> tuple[list[dict], dict]:
    """(items, meta). Never raises."""
    meta: dict = {'theaters': 0, 'date': '', 'blocked': False}
    try:
        meta['date'] = today_iso(TZ)
        return _fetch(limit, meta['date'], meta)
    except Exception as e:
        logger.warning("cinepolis: read failed (%s)", str(e)[:200])
        meta['reason'] = f'read failed: {str(e)[:160]}'
        return [], meta


def _main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    t0 = time.time()
    items, meta = fetch(limit=10)
    print(f"{LABEL}: {meta.get('theaters')} of {meta.get('theaters_listed', '?')} theaters, "
          f"{meta.get('date')}, {time.time() - t0:.1f}s, blocked={meta.get('blocked')}"
          + (', headless' if meta.get('headless') else '')
          + (f", note: {meta['reason']}" if meta.get('reason') else ''))
    for it in items:
        print(f"{it['rank']:>2}. {it['title']}  {it['showtimes']} showtimes, "
              f"{it['theaters']} theaters  {it['url']}")


if __name__ == '__main__':
    _main()
