"""
Santikos Entertainment card: the films with the most showtimes today
across every theater on santikos.com (27 on 2026-11-23: the Santikos
theaters around San Antonio plus the Grand and AmStar theaters the
company now runs in Texas and the Southeast, all on one site and one
schedule).

Where the count comes from
--------------------------
santikos.com builds its Movies grid in the browser from one JSON call
per location, the same call its own front end makes:

    /movies                              the page: `LOCATION_JSON = [...]`
                                         lists every location id and name
    /get-movie-lists?location_id=<id>    {"data": [film, ...]}, each film
                                         with its Vista `Sessions`
                                         (`SessionBusinessDate` /
                                         `date_f`, `Showtime`)

Each film carries every session it has on sale, today and weeks ahead,
so only sessions whose business date is today are counted. 27 calls,
about 13 MB, read four at a time in about 12 seconds. Film links go to
the chain-wide page `/movie-info/<film slug>` (the slug is the last
part of the location-level `booking_url`); posters are the Vista poster
graphic the grid itself shows.

Santikos runs a weekly $5 'Final Cut' feature and a 'Rewind' classics
night, and names those prints 'Heart of the Beast (Final Cut)' and 'Elf
- Rewind'. Those are program labels, not titles, so they come off here
before the prints are merged. The feed drops a session once it starts,
so the day's count is most complete in the morning (the Mac lane runs
at 8 AM Pacific, 10 AM Central).

Access
------
santikos.com sits behind Cloudflare's browser check: plain `requests`
gets the 'Just a moment' page. curl_cffi with Chrome's TLS fingerprint
passes it, but a cold call straight to `get-movie-lists` (or one with an
odd location id) can be refused, so the session opens `/movies` first
and calls the endpoint the way the page does (XHR headers, Referer,
the page's cookies). If the check refuses that, the same calls are
made from inside a headless Chrome page opened on `/movies`
(`render_then_fetch`), checked 2026-11-23.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from .theater_common import (DEFAULT_LIMIT, HTTP_TIMEOUT_S, base_title,
                             clean_title, rank_by_showtimes, render_then_fetch,
                             title_key, today_iso)

logger = logging.getLogger(__name__)

KEY = 'santikos'
LABEL = 'Santikos Entertainment'
COOKIE_DOMAIN = None

BASE = 'https://www.santikos.com'
ENTRY_URL = BASE + '/movies'
LIST_API = BASE + '/get-movie-lists'
XHR_HEADERS = {
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'X-Requested-With': 'XMLHttpRequest',
    'Referer': ENTRY_URL,
}
TZ = 'America/Chicago'
WORKERS = 4
PAUSE_S = 0.3

_WALL_TITLE_RE = re.compile(
    r'<title>\s*(?:just a moment|attention required|access denied)', re.I)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
def parse_locations(html: str) -> list[dict]:
    """[{'id', 'name'}] from the `LOCATION_JSON` the Movies page carries,
    in the page's own order."""
    html = html or ''
    at = html.find('LOCATION_JSON')
    start = html.find('[', at) if at >= 0 else -1
    if start < 0:
        return []
    try:
        locs, _ = json.JSONDecoder().raw_decode(html, start)
    except Exception:
        return []
    out, seen = [], set()
    for loc in locs if isinstance(locs, list) else []:
        if not isinstance(loc, dict):
            continue
        lid = str(loc.get('id') or '').strip()
        if lid.isdigit() and lid not in seen:
            seen.add(lid)
            out.append({'id': lid, 'name': clean_title(loc.get('cinema_name') or '')})
    return out


def _session_day(s: dict) -> str:
    return str(s.get('SessionBusinessDate') or s.get('date_f')
               or s.get('Showtime') or '')[:10]


def parse_movie_list(text: str, today: str) -> Optional[list[dict]]:
    """Films at one location: [{'film_id', 'slug', 'title', 'image',
    'showtimes'}], showtimes counting today's public sessions. None
    when the body is not the movie-list JSON."""
    try:
        data: Any = json.loads(text or '')
    except Exception:
        return None
    films = data.get('data') if isinstance(data, dict) else None
    if not isinstance(films, list):
        return None
    out = []
    for f in films:
        if not isinstance(f, dict) or str(f.get('dont_show') or 0) not in ('0', 'False', ''):
            continue
        title = clean_title(f.get('Title') or '')
        if not title:
            continue
        shows = 0
        for s in f.get('Sessions') or []:
            if not isinstance(s, dict) or s.get('IsPublicScreening') is False:
                continue
            if _session_day(s) == today:
                shows += 1
        slug = str(f.get('booking_url') or '').split('?', 1)[0].rstrip('/').rsplit('/', 1)[-1]
        out.append({
            'film_id': str(f.get('ScheduledFilmId') or ''),
            'slug': slug if '/movie-info/' in str(f.get('booking_url') or '') else '',
            'title': title,
            'image': str(f.get('image_url') or ''),
            'showtimes': shows,
        })
    return out


# ---------------------------------------------------------------------------
# Titles and rows
# ---------------------------------------------------------------------------
# Santikos program labels: the weekly $5 'Final Cut' feature and the
# 'Rewind' classics night.
_PROGRAM_RE = re.compile(
    r'\s*(?:\(\s*(?:final cut|rewind)\s*\)|\s[-:]\s*(?:final cut|rewind))\s*$', re.I)


def prepare_title(raw: str) -> tuple[str, bool]:
    """(title to merge on, True when this is the plain print)."""
    s = clean_title(raw)
    tagged = bool(_PROGRAM_RE.search(s))
    s = _PROGRAM_RE.sub('', s).strip()
    return s, not tagged and base_title(s) == s


def _film_rows(prints: list[dict]) -> list[dict]:
    """Rows for `rank_by_showtimes`, every print of a film carrying the
    film's own title, link and poster.

    A print is {'title' (prepared), 'plain', 'url', 'image',
    'showtimes', 'theater_ids', 'site_order'}. The plain print names
    the film; failing that, the busiest print does. A tagged print that
    differs only by a leading article ('Hunger Games: Sunrise on the
    Reaping (Spanish Sub)' next to 'The Hunger Games: Sunrise on the
    Reaping') is folded into the film it belongs to.
    """
    groups: dict[str, list[dict]] = {}
    for p in prints:
        if int(p.get('showtimes') or 0) > 0 and title_key(p['title']):
            groups.setdefault(title_key(p['title']), []).append(p)
    for k in list(groups):
        for art in ('the', 'a', 'an'):
            home = groups.get(art + k)
            if home is not None and k in groups and not any(p['plain'] for p in groups[k]):
                home.extend(groups.pop(k))
                break
    rows = []
    for ps in groups.values():
        lead = max(ps, key=lambda p: (p['plain'], p['showtimes'], -p['site_order']))
        url = lead.get('url') or next((p['url'] for p in ps if p.get('url')), '')
        image = lead.get('image') or next((p['image'] for p in ps if p.get('image')), '')
        for p in ps:
            rows.append({'title': base_title(lead['title']), 'url': url,
                         'image': image, 'showtimes': p['showtimes'],
                         'theater_ids': sorted(p['theater_ids']),
                         'site_order': p['site_order']})
    return rows


def build_prints(bodies: dict[str, Optional[str]],
                 today: str) -> tuple[list[dict], dict]:
    """Merge the movie lists {location id: body} (in the page's location
    order) into one print per Vista film id. The site order is the
    order films first appear, the default location's grid first."""
    acc: dict[str, dict] = {}
    stats = {'read': 0, 'showing': 0}
    first_seen: dict[str, int] = {}
    for loc, body in bodies.items():
        if not body or is_walled(body):
            continue
        films = parse_movie_list(body, today)
        if films is None:
            continue
        stats['read'] += 1
        live = False
        for f in films:
            key = f['film_id'] or title_key(f['title'])
            first_seen.setdefault(key, len(first_seen))
            if f['showtimes'] <= 0:
                continue
            live = True
            p = acc.get(key)
            if p is None:
                title, plain = prepare_title(f['title'])
                p = acc[key] = {
                    'title': title, 'plain': plain,
                    'url': f'{BASE}/movie-info/{f["slug"]}' if f['slug'] else ENTRY_URL,
                    'image': f['image'], 'showtimes': 0, 'theater_ids': set(),
                    'site_order': first_seen[key],
                }
            p['showtimes'] += f['showtimes']
            p['theater_ids'].add(loc)
            p['image'] = p['image'] or f['image']
        stats['showing'] += live
    return list(acc.values()), stats


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
def is_walled(text: str) -> bool:
    """True when a body is a bot-check page. The page title is the
    test: the shared `looks_walled` reads any 'captcha' near the top as
    a wall, and a real page that loads reCAPTCHA in its head would then
    read as one."""
    head = (text or '')[:4000]
    return bool(_WALL_TITLE_RE.search(head)) or '_incapsula_resource' in head


class _Site:
    """Reads a site that sits behind Cloudflare's browser check.

    curl_cffi with Chrome's TLS fingerprint first: it passes the check
    with no cookies and costs a fraction of a browser. When the check
    refuses it (or curl_cffi is missing), pages are fetched from inside
    a headless Chrome page opened on `entry_url` instead, which carries
    the cookies and origin the site's own front end has.
    """

    def __init__(self, entry_url: str, tag: str):
        self.entry_url = entry_url
        self.tag = tag
        self.mode = ''          # 'direct', 'browser' or 'direct+browser'
        self.walled = False     # the check refused a direct read
        self._sess = None

    def _session(self):
        if self._sess is None:
            from curl_cffi import requests as cr  # type: ignore
            # One session across the worker threads: curl_cffi keeps a
            # curl handle per thread and shares the cookie jar.
            self._sess = cr.Session(impersonate='chrome')
            self._sess.headers.update({'Accept-Language': 'en-US,en;q=0.9'})
        return self._sess

    def direct(self, url: str, headers: Optional[dict] = None) -> Optional[str]:
        """Body text, or None. Never raises."""
        try:
            sess = self._session()
        except Exception as e:
            logger.info('%s: curl_cffi unavailable (%s)', self.tag, e)
            return None
        for attempt in range(3):
            try:
                r = sess.get(url, headers=headers, timeout=HTTP_TIMEOUT_S)
            except Exception as e:
                logger.info('%s: %s failed (%s)', self.tag, url[:120], str(e)[:120])
            else:
                if (r.headers.get('cf-mitigated') or '').lower() == 'challenge' or is_walled(r.text):
                    self.walled = True
                    return None
                if r.status_code == 200:
                    return r.text
                logger.info('%s: %s answered %s', self.tag, url[:120], r.status_code)
                if r.status_code in (401, 403, 404, 410):
                    return None
            time.sleep(1.5 * (attempt + 1))
        return None

    def _browser(self, urls: list[str]) -> dict[str, Optional[str]]:
        got = render_then_fetch(self.entry_url, urls)
        return {u: (got.get(u) if got.get(u) and not is_walled(got.get(u)) else None)
                for u in urls}

    def open(self) -> Optional[str]:
        """The entry page, read directly or else from headless Chrome."""
        text = self.direct(self.entry_url)
        if text is not None:
            self.mode = 'direct'
            return text
        text = self._browser([self.entry_url]).get(self.entry_url)
        if text:
            self.mode = 'browser'
        return text

    def many(self, urls: list[str], headers: Optional[dict] = None) -> dict[str, Optional[str]]:
        """{url: body or None}, at most WORKERS requests at a time."""
        urls = list(dict.fromkeys(urls))
        if self.mode == 'browser':
            return self._browser(urls)

        def one(u: str) -> tuple[str, Optional[str]]:
            if self.walled:             # refused once: stop asking directly
                return u, None
            time.sleep(PAUSE_S)
            return u, self.direct(u, headers)

        with ThreadPoolExecutor(WORKERS) as ex:
            out = dict(ex.map(one, urls))
        missing = [u for u in urls if out.get(u) is None]
        if missing and self.walled:
            logger.info('%s: the check refused %d direct reads; reading them '
                        'in headless Chrome', self.tag, len(missing))
            out.update({u: t for u, t in self._browser(missing).items() if t})
            self.mode = 'direct+browser'
        return out


def _read(limit: int, today: str, meta: dict) -> list[dict]:
    site = _Site(ENTRY_URL, KEY)
    page = site.open()
    if not page:
        meta['blocked'] = site.walled
        meta['reason'] = ('a bot check refused the Movies page' if site.walled
                          else 'the Movies page did not answer')
        return []
    locations = parse_locations(page)
    meta['theaters_listed'] = len(locations)
    if not locations:
        meta['reason'] = 'no locations on the Movies page'
        return []
    urls = {loc['id']: f'{LIST_API}?location_id={loc["id"]}' for loc in locations}
    got = site.many(list(urls.values()), headers=XHR_HEADERS)
    meta['via'] = site.mode
    prints, stats = build_prints({lid: got.get(u) for lid, u in urls.items()}, today)
    meta['theaters'] = stats['read']
    meta['theaters_showing'] = stats['showing']
    if not stats['read']:
        meta['blocked'] = site.walled
        meta['reason'] = ('a bot check refused the movie lists' if site.walled
                          else 'no movie list could be read')
        return []
    if stats['read'] < len(locations):
        logger.info('santikos: read %d of %d locations', stats['read'], len(locations))
    items = rank_by_showtimes(_film_rows(prints), limit=limit)
    if not items:
        meta['reason'] = 'no showtimes today'
    return items


def fetch(limit: int = DEFAULT_LIMIT) -> tuple[list[dict], dict]:
    """(items, meta) for the Santikos card. Never raises."""
    t0 = time.time()
    today = today_iso(TZ)
    meta = {'theaters': 0, 'theaters_listed': 0, 'date': today, 'blocked': False}
    try:
        items = _read(limit, today, meta)
    except Exception as e:
        logger.warning('santikos: read failed (%s)', e)
        meta['reason'] = f'error: {str(e)[:160]}'
        items = []
    meta['seconds'] = round(time.time() - t0, 1)
    return items, meta


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(name)s %(message)s')
    found, info = fetch()
    print(LABEL, info)
    for it in found[:10]:
        print(f"{it['rank']:>2}. {it['showtimes']:>4} showtimes  "
              f"{it['theaters'] or 0:>3} theaters  {it['title']}  {it['url']}")
