"""
Marcus Theatres (with Movie Tavern): the films with the most showtimes
today across the chain's US theaters (about 77, mostly the Midwest).

marcustheatres.com is a Next.js site behind a bot check. Its pages read
everything from the chain's ticketing service, api-injin.marcustheatres.com,
which answers a plain request with JSON once it carries a visitor token:

    POST /user/v2/token                      the anonymous visitor token
                                             every page asks for, signed
                                             with the visitor login the
                                             site's own script carries
    GET  /cms/v2/films                       every film (title, link, poster)
    GET  /cms/v2/packs/upcomingshows?showdate=<day>
                                             the site's Book Now list for
                                             a day (its id and film order)
    GET  /cms/v2/packs/<id>/showtimes?showdate=<day>
                                             every showtime on that day at
                                             every theater, in one answer
    GET  /cms/v2/cinemas                     the theater list (count only)

The visitor login is read from the site's own script on every run, the
same way a browser gets it, so nothing about it is stored here. The
schedule drops a show a few minutes after it starts, so the count is
the whole day only when the read runs before the first shows (the
morning lane does). A film's page is
www.marcustheatres.com/movies/<name>, and its poster is the one the site
shows.

If the site or the service starts answering a bot check, the same reads
run from inside a real marcustheatres.com page in headless Chrome with
the cookies donated from the laptop's Chrome (cookie domain
marcustheatres.com: visit www.marcustheatres.com normally in Chrome,
pass any check it shows, then donate). The page gets its own visitor
token, and the reads reuse it. With no fresh donation the read stops at
once and says blocked, so the caller can ask for one.

Live smoke, from the repo root:
    python3 -m scripts.trends_scrapers.theater_marcus
    python3 -m scripts.trends_scrapers.theater_marcus --json
    python3 -m scripts.trends_scrapers.theater_marcus --browser --no-donation
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date as _date, timedelta
from typing import Any, Optional

from .theater_common import (DEFAULT_LIMIT, clean_title, looks_walled,
                             rank_by_showtimes, session, today_iso)

logger = logging.getLogger(__name__)

KEY = 'marcus'
LABEL = 'Marcus Theatres'
COOKIE_DOMAIN = 'marcustheatres.com'

SITE = 'https://www.marcustheatres.com'
API = 'https://api-injin.marcustheatres.com'
TZ = 'America/Chicago'
PACK = 'upcomingshows'      # the site's Book Now list
HOME_COMPONENTS = ('https://mts-web-app.s3.us-east-2.amazonaws.com/'
                   'nextjs_website/json/home-page-components.json')

TIMEOUT_S = 45
PAUSE_S = 0.4
MAX_SCRIPT_READS = 12       # the site's scripts read to find its visitor login
MAX_FILM_READS = 120        # per-film fallback when the one-call schedule fails
FILM_WORKERS = 3
LATE_NIGHT_HOUR = 5         # a 12:30 AM show belongs to the night before

_SITE_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}
_API_HEADERS = {
    'Accept': 'application/json, text/plain, */*',
    'Origin': SITE,
    'Referer': SITE + '/',
    'dataversion': 'en-US',
    'appplatform': 'WEBSITE',
    'appversion': '1.0.0',
}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _new_session():
    """A Chrome-like TLS handshake when curl_cffi is installed (the
    site's bot check reads it), plain requests otherwise."""
    try:
        from curl_cffi import requests as creq  # type: ignore
        return creq.Session(impersonate='chrome')
    except Exception:
        return session()


class _Http:
    def __init__(self):
        self.sess = _new_session()

    def get(self, url: str, headers: Optional[dict] = None,
            retries: int = 1) -> tuple[int, str]:
        return self._call('GET', url, headers, None, retries)

    def post(self, url: str, body: str, headers: Optional[dict] = None,
             retries: int = 1) -> tuple[int, str]:
        return self._call('POST', url, headers, body, retries)

    def _call(self, method, url, headers, body, retries) -> tuple[int, str]:
        """(status, body). Status 0 when nothing answered. Never raises."""
        last = (0, '')
        for attempt in range(retries + 1):
            try:
                if method == 'POST':
                    r = self.sess.post(url, data=body, headers=headers, timeout=TIMEOUT_S)
                else:
                    r = self.sess.get(url, headers=headers, timeout=TIMEOUT_S)
                last = (int(r.status_code), r.text or '')
                # The service says 500 "Unauthorized" for a bad token.
                if last[0] in (200, 401, 403, 404) or 'unauthorized' in last[1][:200].lower():
                    return last
                logger.info("marcus: %s answered %s", url[:120], last[0])
            except Exception as e:
                logger.info("marcus: %s failed (%s)", url[:120], str(e)[:120])
                last = (0, '')
            time.sleep(1.5 * (attempt + 1))
        return last


def _walled(text: str) -> bool:
    head = (text or '')[:6000].lower()
    return looks_walled(text) or 'incapsula incident' in head \
        or 'request unsuccessful' in head


def _walled_page(html: str) -> bool:
    """A bot check in place of the site's page. The real page always
    loads its own Next.js scripts, so a page that does is never a wall,
    whatever words it carries."""
    return '/_next/static/' not in (html or '') and _walled(html)


def _walled_script(js: str) -> bool:
    return 'webpack' not in (js or '')[:2000] and _walled(js)


def _json(text: Optional[str]) -> Any:
    try:
        return json.loads(text) if text else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# The visitor login the site's own script carries
# ---------------------------------------------------------------------------
_SCRIPT_RE = re.compile(r'''<script[^>]+src=["'](/_next/static/chunks/[^"']+\.js)["']''', re.I)
_LOGIN_RE = re.compile(
    r'''username\s*:\s*(["'])((?:\\.|(?!\1).)+)\1\s*,\s*password\s*:\s*(["'])((?:\\.|(?!\3).)*)\3''')
_FRAMEWORK_RE = re.compile(r'/(?:polyfills|webpack|main-app|framework|main)-[0-9a-f]+\.js$')
_LOGIN: dict = {}           # one read per process


def script_urls(home_html: str) -> list[str]:
    """The page's script chunks, most likely carrier of the visitor
    login first: shared numbered chunks (the API client lives in one),
    then page chunks. Framework chunks never carry it."""
    seen: list[str] = []
    for m in _SCRIPT_RE.finditer(home_html or ''):
        path = m.group(1)
        if path not in seen and not _FRAMEWORK_RE.search(path):
            seen.append(path)

    def rank(path: str) -> tuple:
        name = path.rsplit('/', 1)[-1]
        if name.startswith('1514-'):
            return (0, 0)
        if re.match(r'^\d+-[0-9a-f]+\.js$', name):
            return (1, 0)
        if '/app/' in path:
            return (2, 0)
        return (3, 0)
    return [SITE + p for p in sorted(seen, key=rank)]


def _unescape_js(s: str) -> str:
    try:
        return json.loads('"' + s.replace('"', '\\"') + '"')
    except Exception:
        return s


def login_from_script(js: str) -> Optional[tuple[str, str]]:
    """(user, password) of the anonymous visitor login, from the chunk
    that asks for the visitor token. None when this chunk has no such
    call."""
    if not js or '/user/v2/token' not in js:
        return None
    i = js.find('/user/v2/token')
    for window in (js[i:i + 4000], js):
        m = _LOGIN_RE.search(window)
        if m:
            return _unescape_js(m.group(2)), _unescape_js(m.group(4))
    m = re.search(r'''Buffer\.from\(\s*["']([^"':]+):([^"']+)["']\s*\)''', js)
    if m:
        return m.group(1), m.group(2)
    return None


def _find_login(http: _Http) -> tuple[Optional[tuple[str, str]], bool]:
    """(login, walled). Reads the home page, then its scripts."""
    if _LOGIN.get('login'):
        return _LOGIN['login'], False
    status, home = http.get(SITE + '/', headers=_SITE_HEADERS)
    if _walled_page(home):
        return None, True
    if status != 200:
        return None, False
    for url in script_urls(home)[:MAX_SCRIPT_READS]:
        time.sleep(PAUSE_S)
        st, js = http.get(url, headers={'Accept': '*/*', 'Referer': SITE + '/'})
        if _walled_script(js):
            return None, True
        found = login_from_script(js) if st == 200 else None
        if found:
            _LOGIN['login'] = found
            return found, False
    return None, False


def _visitor_token(http: _Http, login: tuple[str, str]) -> tuple[Optional[str], bool]:
    """(token, walled)."""
    user, pw = login
    basic = base64.b64encode(f'{user}:{pw}'.encode('utf-8')).decode('ascii')
    body = json.dumps({'accessTokenExpiry': 2000, 'getUserInfo': True,
                       'keepMeSignedIn': True})
    headers = {'Accept': 'application/json, text/plain, */*', 'Origin': SITE,
               'Referer': SITE + '/', 'Content-Type': 'application/json',
               'Platform': 'web/prod', 'Version': '1.0',
               'Authorization': 'Basic ' + basic}
    status, text = http.post(API + '/user/v2/token', body, headers=headers)
    d = _json(text)
    if not isinstance(d, dict):
        return None, _walled(text)
    tok = d.get('accessToken')
    if not tok and isinstance(d.get('data'), dict):
        tok = d['data'].get('accessToken')
    return (str(tok) if tok else None), False


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def pack_info(payload: Any) -> tuple[Any, list[str]]:
    """(pack id, film ids in the site's listing order) from
    /cms/v2/packs/<name>?showdate=<day>."""
    one = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(one, dict):
        return None, []
    order = []
    for x in one.get('packs_films_results') or []:
        fid = x.get('filmid') if isinstance(x, dict) else None
        if fid and fid not in order:
            order.append(str(fid))
    return one.get('id'), order


def counts_for_day(day: str, showtime: str) -> bool:
    """A show counts for `day` when it starts that day, or in the small
    hours of the next one (a late show belongs to the night before).
    The service writes local wall-clock time with a trailing Z."""
    s = str(showtime or '')
    if s[:10] == day:
        return True
    try:
        nxt = (_date.fromisoformat(day) + timedelta(days=1)).isoformat()
        return s[:10] == nxt and int(s[11:13]) < LATE_NIGHT_HOUR
    except Exception:
        return False


def poster(film: dict) -> str:
    """The portrait poster the site shows for a film."""
    imgs = [i for i in (film.get('images') or []) if isinstance(i, dict) and i.get('imageurl')]
    for test in (lambda i: (i.get('description') or '').lower() == 'films portrait poster',
                 lambda i: 'filmpostergraphic' in i['imageurl'].lower(),
                 lambda i: True):
        for i in imgs:
            if test(i):
                return str(i['imageurl'])
    return ''


def film_url(film: dict) -> str:
    name = film.get('urlSafeName') or film.get('urlsafetitle') or ''
    return f'{SITE}/movies/{name}' if name else f'{SITE}/movies'


def chain_rows(showtimes: Any, films: Any, order: list[str], day: str) -> tuple[list[dict], dict]:
    """rank_by_showtimes rows from one day's showtimes at every theater.
    site_order is the site's Book Now order; it only breaks ties.
    Returns (rows, stats)."""
    fmap = {}
    for f in films if isinstance(films, list) else []:
        if isinstance(f, dict) and f.get('id'):
            fmap[str(f['id'])] = f
    position = {fid: i for i, fid in enumerate(order or [])}
    agg: dict[str, dict] = {}
    seen_ids: set = set()
    cinemas: set = set()
    unnamed = 0
    for s in showtimes if isinstance(showtimes, list) else []:
        if not isinstance(s, dict) or not counts_for_day(day, s.get('showtime')):
            continue
        sid = s.get('id') or (s.get('cinemaid'), s.get('sessionid'), s.get('showtime'))
        sid = json.dumps(sid, sort_keys=True) if not isinstance(sid, str) else sid
        if sid in seen_ids:
            continue
        seen_ids.add(sid)
        fid, cid = str(s.get('filmid') or ''), str(s.get('cinemaid') or '')
        film = fmap.get(fid)
        if not film or not clean_title(film.get('title') or ''):
            unnamed += 1
            continue
        g = agg.setdefault(fid, {'showtimes': 0, 'theater_ids': set()})
        g['showtimes'] += 1
        if cid:
            g['theater_ids'].add(cid)
            cinemas.add(cid)
    tail = len(position)
    rows = []
    for fid, g in agg.items():
        film = fmap[fid]
        rows.append({
            'title': clean_title(film.get('title') or ''),
            'showtimes': g['showtimes'],
            'url': film_url(film),
            'image': poster(film),
            'theater_ids': sorted(g['theater_ids']),
            'site_order': position.get(fid, tail + len(rows)),
        })
    rows.sort(key=lambda r: r['site_order'])
    return rows, {'theaters': len(cinemas), 'unnamed_showtimes': unnamed}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
class _Api:
    def __init__(self, http: _Http, token: str, login: tuple[str, str]):
        self.http, self.token, self.login = http, token, login

    def get(self, path: str) -> tuple[Any, bool]:
        """(parsed JSON or None, walled). Gets a new visitor token once
        when the service says the token is no good."""
        for attempt in range(2):
            headers = dict(_API_HEADERS, Authorization='Bearer ' + self.token)
            status, text = self.http.get(API + path, headers=headers)
            data = _json(text)
            refused = status == 401 or (isinstance(data, dict) and str(
                data.get('message') or '').strip().lower() == 'unauthorized')
            if status == 200 and data is not None and not refused:
                return data, False
            # Only a body that is not JSON can be a bot check: a film
            # called "Access Denied" must not read as one.
            if data is None and _walled(text):
                return None, True
            if attempt == 0 and refused:
                tok, walled = _visitor_token(self.http, self.login)
                if walled:
                    return None, True
                if tok:
                    self.token = tok
                    continue
            return None, False
        return None, False


def _pack_name(http: _Http) -> str:
    """The site's own name for its Book Now list, from its home page
    settings; the usual name when that file does not answer."""
    status, text = http.get(HOME_COMPONENTS, headers={'Accept': 'application/json'})
    data = _json(text) if status == 200 else None
    for c in data if isinstance(data, list) else []:
        props = c.get('componentProps') if isinstance(c, dict) else None
        if isinstance(props, dict) and c.get('datepicker') and props.get('packsToShow'):
            return str(props['packsToShow'])
    return PACK


def _showtimes_by_film(api: _Api, film_ids: list[str], day: str) -> tuple[list, bool]:
    """Fallback: one read per film, the way a film's own page asks.
    Each worker has its own connection; they share the token."""
    local = threading.local()

    def one(fid):
        time.sleep(PAUSE_S)
        if not hasattr(local, 'api'):
            local.api = _Api(_Http(), api.token, api.login)
        return local.api.get(f'/cms/v2/{fid}/showtimes?showdate={day}')
    out: list = []
    walled = False
    with ThreadPoolExecutor(max_workers=FILM_WORKERS) as ex:
        for data, w in ex.map(one, film_ids[:MAX_FILM_READS]):
            walled = walled or w
            if isinstance(data, list):
                out.extend(data)
    return out, walled


def _read_direct(day: str) -> dict:
    """{'state': 'ok' | 'walled' | 'failed', ...}"""
    http = _Http()
    login, walled = _find_login(http)
    if walled:
        return {'state': 'walled'}
    if not login:
        return {'state': 'failed', 'error': "the site's visitor sign-in could not be found"}
    token, walled = _visitor_token(http, login)
    if walled:
        return {'state': 'walled'}
    if not token:
        _LOGIN.pop('login', None)
        return {'state': 'failed', 'error': 'the visitor sign-in was refused'}
    api = _Api(http, token, login)
    films, w1 = api.get('/cms/v2/films')
    if w1:
        return {'state': 'walled'}
    pack_payload, w2 = api.get(f'/cms/v2/packs/{PACK}?showdate={day}')
    pack_id, order = pack_info(pack_payload)
    if pack_id is None and not w2:
        name = _pack_name(http)
        if name != PACK:
            pack_payload, w2 = api.get(f'/cms/v2/packs/{name}?showdate={day}')
            pack_id, order = pack_info(pack_payload)
    shows, w3 = (api.get(f'/cms/v2/packs/{pack_id}/showtimes?showdate={day}')
                 if pack_id is not None else (None, False))
    if not isinstance(shows, list) and not (w2 or w3):
        # Films already open by today, when the Book Now list is missing too.
        ids = order or [str(f['id']) for f in films if isinstance(f, dict) and f.get('id')
                        and str(f.get('openingdate') or '')[:10] <= day] \
            if isinstance(films, list) else order
        shows, w3 = _showtimes_by_film(api, ids, day)
        shows = shows or None
    if not isinstance(shows, list):
        return {'state': 'walled' if (w2 or w3) else 'failed',
                'error': 'the schedule could not be read'}
    cinemas, _ = api.get('/cms/v2/cinemas')
    return {'state': 'ok', 'films': films, 'order': order, 'shows': shows,
            'listed': len(cinemas) if isinstance(cinemas, list) else None}


_PAGE_JS = r"""
async ({api, day, pack, waitMs, timeoutMs}) => {
  const head = (document.documentElement ? document.documentElement.outerHTML : '').slice(0, 6000);
  const readToken = () => {
    const m = document.cookie.match(/(?:^|;\s*)accessToken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    try { return sessionStorage.getItem('userAccessToken'); } catch (e) { return null; }
  };
  let token = readToken();
  const t0 = Date.now();
  while (!token && Date.now() - t0 < waitMs) {
    await new Promise(r => setTimeout(r, 250));
    token = readToken();
  }
  if (!token) return JSON.stringify({head, token: false});
  const headers = {'Authorization': 'Bearer ' + token, 'dataversion': 'en-US',
                   'appplatform': 'WEBSITE', 'appversion': '1.0.0',
                   'Accept': 'application/json, text/plain, */*'};
  const get = async (path) => {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const r = await fetch(api + path, {headers, signal: ctl.signal});
      return r.ok ? await r.text() : null;
    } catch (e) {
      return null;
    } finally {
      clearTimeout(t);
    }
  };
  const films = await get('/cms/v2/films');
  const packs = await get('/cms/v2/packs/' + pack + '?showdate=' + day);
  let packId = null;
  try {
    const p = JSON.parse(packs || 'null');
    const one = Array.isArray(p) ? p[0] : p;
    packId = one ? one.id : null;
  } catch (e) {}
  const shows = packId !== null && packId !== undefined
    ? await get('/cms/v2/packs/' + packId + '/showtimes?showdate=' + day) : null;
  const cinemas = await get('/cms/v2/cinemas');
  return JSON.stringify({head, token: true, films, packs, shows, cinemas});
}
"""


def _read_browser(day: str, cookie_domain: Optional[str]) -> dict:
    """The same reads from inside a marcustheatres.com page in headless
    Chrome, with the token the page gets for itself."""
    try:
        from ._playwright import render_pages
    except Exception as e:
        logger.info("marcus: headless Chrome unavailable (%s)", e)
        return {'state': 'failed', 'error': 'headless Chrome is not available'}
    box: dict = {}

    def hook(page, _label):
        try:
            box['res'] = page.evaluate(_PAGE_JS, {'api': API, 'day': day, 'pack': PACK,
                                                  'waitMs': 15000, 'timeoutMs': 45000})
        except Exception as e:
            logger.info("marcus: in-page read failed (%s)", str(e)[:160])
        return box.get('res') or json.dumps({'head': '', 'token': False})

    try:
        render_pages([('marcus', SITE + '/')], cookie_domain=cookie_domain,
                     hydration_wait_ms=12000, wait_ms=3500, page_hook=hook)
    except Exception as e:
        logger.info("marcus: browser read failed (%s)", e)
    res = _json(box.get('res'))
    if not isinstance(res, dict):
        return {'state': 'failed', 'error': 'headless Chrome did not open the site'}
    if not res.get('token'):
        walled = _walled_page(res.get('head') or '')
        return {'state': 'walled' if walled else 'failed',
                'error': 'the page did not get a visitor token'}
    shows = _json(res.get('shows'))
    if not isinstance(shows, list):
        return {'state': 'walled', 'error': 'the schedule could not be read in the browser'}
    _, order = pack_info(_json(res.get('packs')))
    cinemas = _json(res.get('cinemas'))
    return {'state': 'ok', 'films': _json(res.get('films')), 'order': order,
            'shows': shows, 'listed': len(cinemas) if isinstance(cinemas, list) else None}


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
        logger.info("marcus: donation check failed (%s)", e)
        return False


def read(limit: int = DEFAULT_LIMIT, *, cookie_domain: Optional[str] = COOKIE_DOMAIN,
         browser: str = 'auto') -> tuple[list[dict], dict]:
    """fetch() with the knobs the smoke run needs. browser='auto' tries
    the service first and the donated-cookie browser only when the read
    is walled; 'force' goes straight to the browser; 'never' skips it.
    cookie_domain=None never reads the donation store."""
    day = today_iso(TZ)
    meta: dict = {'theaters': 0, 'date': day, 'blocked': False}
    try:
        res: dict = {'state': 'walled'}
        if browser != 'force':
            res = _read_direct(day)
            meta['source'] = 'api'
        if res['state'] == 'walled' and browser != 'never':
            if browser == 'force' or _has_donation(cookie_domain):
                res = _read_browser(day, cookie_domain)
                meta['source'] = 'browser'
            else:
                res = {'state': 'walled',
                       'error': 'a bot check refused the read and there is no fresh cookie donation'}
        if res['state'] != 'ok':
            meta['blocked'] = res['state'] == 'walled'
            meta['reason'] = res.get('error') or 'the read did not finish'
            return [], meta
        rows, stats = chain_rows(res['shows'], res.get('films'), res.get('order') or [], day)
        meta['theaters'] = stats['theaters']
        if res.get('listed'):
            meta['theaters_listed'] = res['listed']
        ranked = rank_by_showtimes(rows, limit=10 ** 6)
        meta['films_today'] = len(ranked)
        if stats['unnamed_showtimes']:
            meta['unnamed_showtimes'] = stats['unnamed_showtimes']
        return ranked[:max(0, int(limit))], meta
    except Exception as e:
        logger.warning("marcus: read failed (%s)", e)
        meta['reason'] = 'the read failed'
        return [], meta


def fetch(limit: int = DEFAULT_LIMIT) -> tuple[list[dict], dict]:
    """(items, meta): the films with the most showtimes today across
    Marcus Theatres. Never raises."""
    return read(limit)


# ---------------------------------------------------------------------------
# Live smoke
# ---------------------------------------------------------------------------
def _main(argv: Optional[list[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description='Marcus Theatres: most showtimes today.')
    ap.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    ap.add_argument('--json', action='store_true', help='print the raw result')
    ap.add_argument('--browser', action='store_true',
                    help='skip the direct read and read through headless Chrome')
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
