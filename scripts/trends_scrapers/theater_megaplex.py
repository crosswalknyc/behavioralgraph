"""
Megaplex Theatres: the films with the most showtimes today across the
chain's theaters (Utah, plus Mesquite, Nevada).

The website (megaplex.com) sits behind a bot check, but the data
service its own pages read, apiv2.megaplex.com, answers a plain request
with JSON:

    GET /api/cinema/cinemas               every theater (about 18)
    GET /api/film/cinemaFilms/<theater>   that theater's whole schedule:
                                          every film with every session
                                          it has on sale

Today's sessions are counted by the chain's own business date, so a
12:30 AM show counts for the night it belongs to. The schedule drops
a show within the hour after it starts, so the count is the whole day
only when the read runs before the first shows (the morning lane does).
A film's page is megaplex.com/film/<code>/<slug>
and its poster is the one the site shows.

If that service ever starts answering a bot check, the same two reads
run from inside a real megaplex.com page in headless Chrome with the
cookies donated from the laptop's Chrome (cookie domain megaplex.com:
visit megaplex.com normally in Chrome, pass any check it shows, then
donate). With no fresh donation the read stops at once and says
blocked, so the caller can ask for one.

Live smoke, from the repo root:
    python3 -m scripts.trends_scrapers.theater_megaplex
    python3 -m scripts.trends_scrapers.theater_megaplex --json
    python3 -m scripts.trends_scrapers.theater_megaplex --browser --no-donation
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from .theater_common import (DEFAULT_LIMIT, clean_title, looks_walled,
                             rank_by_showtimes, render_then_fetch, session,
                             today_iso)

logger = logging.getLogger(__name__)

KEY = 'megaplex'
LABEL = 'Megaplex Theatres'
COOKIE_DOMAIN = 'megaplex.com'

SITE = 'https://megaplex.com'
API = 'https://apiv2.megaplex.com'
IMG = 'https://img.megaplextheatres.com'
TZ = 'America/Denver'

CINEMAS_URL = API + '/api/cinema/cinemas'
FILMS_URL = API + '/api/film/cinemaFilms/{cid}'

WORKERS = 3            # the site allows 4 at once; stay under it
PAUSE_S = 0.35         # between one worker's requests
TIMEOUT_S = 45         # a theater's schedule is about 2 MB
UNSEQUENCED = 9999     # a film with no display sequence lists last

_API_HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Origin': SITE,
    'Referer': SITE + '/',
}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
_tls = threading.local()


def _client():
    """One session per thread. A Chrome-like TLS handshake when
    curl_cffi is installed (the site's bot check reads it), plain
    requests otherwise."""
    sess = getattr(_tls, 'sess', None)
    if sess is None:
        try:
            from curl_cffi import requests as creq  # type: ignore
            sess = creq.Session(impersonate='chrome')
        except Exception:
            sess = session()
        _tls.sess = sess
    return sess


def _get(url: str, *, retries: int = 1) -> tuple[int, str]:
    """(status, body). Status 0 when nothing answered. Never raises."""
    last = (0, '')
    for attempt in range(retries + 1):
        try:
            r = _client().get(url, headers=_API_HEADERS, timeout=TIMEOUT_S)
            last = (int(r.status_code), r.text or '')
            if last[0] == 200 or last[0] in (401, 403, 404):
                return last
            logger.info("megaplex: %s answered %s", url[:120], last[0])
        except Exception as e:
            logger.info("megaplex: %s failed (%s)", url[:120], str(e)[:120])
            last = (0, '')
        time.sleep(1.5 * (attempt + 1))
    return last


def _walled(text: str) -> bool:
    head = (text or '')[:6000].lower()
    return looks_walled(text) or 'incapsula incident' in head \
        or 'request unsuccessful' in head


def _json(text: Optional[str]) -> Any:
    try:
        return json.loads(text) if text else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_SLUG_DROP_RE = re.compile(r'''[*+~.()'"!:@/]''')


def cinema_ids(payload: Any) -> list[str]:
    """Theater ids from /api/cinema/cinemas, in the chain's order.
    The gift store entry is not a theater."""
    out: list[str] = []
    for c in payload if isinstance(payload, list) else []:
        if not isinstance(c, dict) or c.get('isGiftStore'):
            continue
        cid = str(c.get('id') or '').strip()
        if cid and cid not in out:
            out.append(cid)
    return out


def business_day(sess: dict) -> str:
    """The chain's own schedule day for a session: its business date,
    else the day its showtime starts."""
    bd = str(sess.get('sessionBusinessDate') or '')[:10]
    if _DATE_RE.match(bd):
        return bd
    return str(sess.get('showtime') or '')[:10]


def theater_rows(cinema_id: str, films: Any, date: str) -> list[dict]:
    """One row per film in one theater's schedule, with the number of
    its sessions on `date`. Films with none that day are left out."""
    out: list[dict] = []
    for f in films if isinstance(films, list) else []:
        if not isinstance(f, dict):
            continue
        title = clean_title(f.get('title') or '')
        code = str(f.get('scheduledFilmId') or f.get('filmHOPK') or '').strip()
        if not title or not code:
            continue
        shows = sum(1 for s in (f.get('sessions') or [])
                    if isinstance(s, dict) and business_day(s) == date)
        if shows <= 0:
            continue
        seq = f.get('displaySequence')
        out.append({
            'code': code,
            'poster_code': str(f.get('filmHOPK') or code).strip(),
            'title': title,
            'showtimes': shows,
            'cinema': str(cinema_id),
            'sequence': int(seq) if isinstance(seq, (int, float)) else UNSEQUENCED,
        })
    return out


def slug(title: str) -> str:
    """The slug the site puts after the film code in its links."""
    s = unicodedata.normalize('NFKD', clean_title(title))
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace('&', 'and').replace('-', ' ')
    s = _SLUG_DROP_RE.sub('', s)
    return re.sub(r'\s+', '-', s.strip()).lower()


def film_url(code: str, title: str) -> str:
    return f'{SITE}/film/{code}/{slug(title)}'


def poster_url(code: str) -> str:
    return f'{IMG}/FilmPosterGraphic/{code}' if code else ''


def chain_rows(per_theater: list[list[dict]]) -> list[dict]:
    """Add each film up across the theaters into rank_by_showtimes rows.
    site_order is the chain's own featured order (its display sequence),
    then the order the schedules list films in. It only breaks ties."""
    films: dict[str, dict] = {}
    seen = 0
    for rows in per_theater:
        for r in rows or []:
            g = films.get(r['code'])
            if g is None:
                g = films[r['code']] = {
                    'title': r['title'], 'poster_code': r['poster_code'],
                    'showtimes': 0, 'theater_ids': set(),
                    'sequence': r['sequence'], 'seen': seen,
                }
                seen += 1
            g['showtimes'] += int(r['showtimes'])
            g['theater_ids'].add(r['cinema'])
            g['sequence'] = min(g['sequence'], r['sequence'])
    ordered = sorted(films.items(), key=lambda kv: (kv[1]['sequence'], kv[1]['seen']))
    out = []
    for order, (code, g) in enumerate(ordered):
        out.append({
            'title': g['title'],
            'showtimes': g['showtimes'],
            'url': film_url(code, g['title']),
            'image': poster_url(g['poster_code']),
            'theater_ids': sorted(g['theater_ids']),
            'site_order': order,
        })
    return out


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def _read_theater(cid: str, date: str) -> tuple[str, Optional[list[dict]]]:
    time.sleep(PAUSE_S)
    status, text = _get(FILMS_URL.format(cid=cid))
    films = _json(text) if status == 200 else None
    if isinstance(films, list):
        return 'ok', theater_rows(cid, films, date)
    # Only a body that is not the schedule can be a bot check: a film
    # called "Access Denied" must not read as one.
    return ('walled' if _walled(text) else 'failed'), None


def _read_direct(date: str) -> dict:
    """{'state': 'ok' | 'walled' | 'failed', 'per_theater', 'listed'}"""
    status, text = _get(CINEMAS_URL)
    payload = _json(text) if status == 200 else None
    if not isinstance(payload, list) and _walled(text):
        return {'state': 'walled'}
    ids = cinema_ids(payload)
    if not ids:
        return {'state': 'failed', 'error': 'the theater list could not be read'}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(lambda c: _read_theater(c, date), ids))
    per = [rows for state, rows in results if state == 'ok']
    if not per:
        walled = any(state == 'walled' for state, _ in results)
        return {'state': 'walled' if walled else 'failed', 'listed': len(ids),
                'error': 'no theater schedule could be read'}
    return {'state': 'ok', 'per_theater': per, 'listed': len(ids)}


def _read_browser(date: str, cookie_domain: Optional[str]) -> dict:
    """The same reads from inside a megaplex.com page in headless Chrome."""
    got = render_then_fetch(SITE + '/', [CINEMAS_URL], cookie_domain=cookie_domain,
                            concurrency=1)
    if not got:
        return {'state': 'failed', 'error': 'headless Chrome did not open the site'}
    text = got.get(CINEMAS_URL)
    payload = _json(text)
    ids = cinema_ids(payload)
    if not ids:
        # The page's own request was refused or answered a bot check.
        walled = text is None or (not isinstance(payload, list) and _walled(text))
        return {'state': 'walled' if walled else 'failed',
                'error': 'the theater list could not be read in the browser'}
    urls = [FILMS_URL.format(cid=c) for c in ids]
    got = render_then_fetch(SITE + '/', urls, cookie_domain=cookie_domain,
                            concurrency=WORKERS, timeout_ms=60000)
    per = []
    for cid, url in zip(ids, urls):
        films = _json(got.get(url))
        if isinstance(films, list):
            per.append(theater_rows(cid, films, date))
    if not per:
        return {'state': 'walled', 'listed': len(ids),
                'error': 'no theater schedule could be read in the browser'}
    return {'state': 'ok', 'per_theater': per, 'listed': len(ids)}


def _has_donation(domain: Optional[str]) -> bool:
    """True when the laptop's Chrome has donated fresh cookies or a
    session for `domain`. Never raises."""
    if not domain:
        return False
    try:
        from ._base import cookie_donation_status, storage_state_status
        c = cookie_donation_status(domain) or {}
        s = storage_state_status(domain) or {}
        return bool((c.get('donated') and c.get('fresh')) or
                    (s.get('donated') and s.get('fresh')))
    except Exception as e:
        logger.info("megaplex: donation check failed (%s)", e)
        return False


def read(limit: int = DEFAULT_LIMIT, *, cookie_domain: Optional[str] = COOKIE_DOMAIN,
         browser: str = 'auto') -> tuple[list[dict], dict]:
    """fetch() with the knobs the smoke run needs. browser='auto' tries
    the data service first and the donated-cookie browser only when the
    service is walled; 'force' goes straight to the browser; 'never'
    skips it. cookie_domain=None never reads the donation store."""
    date = today_iso(TZ)
    meta: dict = {'theaters': 0, 'date': date, 'blocked': False}
    try:
        res: dict = {'state': 'walled'}
        if browser != 'force':
            res = _read_direct(date)
            meta['source'] = 'api'
        if res['state'] == 'walled' and browser != 'never':
            if browser == 'force' or _has_donation(cookie_domain):
                res = _read_browser(date, cookie_domain)
                meta['source'] = 'browser'
            else:
                res = {'state': 'walled',
                       'error': 'a bot check refused the read and there is no fresh cookie donation'}
        if res.get('listed'):
            meta['theaters_listed'] = res['listed']
        if res['state'] != 'ok':
            meta['blocked'] = res['state'] == 'walled'
            meta['reason'] = res.get('error') or 'the read did not finish'
            return [], meta
        per = res['per_theater']
        meta['theaters'] = len(per)
        if res.get('listed') and len(per) < res['listed']:
            meta['reason'] = f"{res['listed'] - len(per)} of {res['listed']} theaters could not be read"
        ranked = rank_by_showtimes(chain_rows(per), limit=10 ** 6)
        meta['films_today'] = len(ranked)
        return ranked[:max(0, int(limit))], meta
    except Exception as e:
        logger.warning("megaplex: read failed (%s)", e)
        meta['reason'] = 'the read failed'
        return [], meta


def fetch(limit: int = DEFAULT_LIMIT) -> tuple[list[dict], dict]:
    """(items, meta): the films with the most showtimes today across
    Megaplex's theaters. Never raises."""
    return read(limit)


# ---------------------------------------------------------------------------
# Live smoke
# ---------------------------------------------------------------------------
def _main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description='Megaplex Theatres: most showtimes today.')
    ap.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    ap.add_argument('--json', action='store_true', help='print the raw result')
    ap.add_argument('--browser', action='store_true',
                    help='skip the data service and read through headless Chrome')
    ap.add_argument('--no-donation', action='store_true',
                    help='never read the donated cookie store')
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    t0 = time.time()
    items, meta = read(a.limit, cookie_domain=None if a.no_donation else COOKIE_DOMAIN,
                       browser='force' if a.browser else 'auto')
    if a.json:
        print(json.dumps({'items': items, 'meta': meta}, indent=1))
    else:
        for it in items:
            print(f"{it['rank']:>3}  {it['showtimes']:>5} shows  {it['theaters'] or 0:>3} theaters  "
                  f"{it['title'][:52]:<52}  {it['url']}")
        print(f"{LABEL}: {len(items)} films, meta={meta}, {time.time() - t0:.1f}s")
    return 0 if items else 1


if __name__ == '__main__':
    raise SystemExit(_main())
