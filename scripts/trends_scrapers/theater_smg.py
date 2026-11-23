"""
Studio Movie Grill card: the films with the most showtimes today across
every Studio Movie Grill (18 on 2026-11-23, in Texas, California,
Florida, Georgia and Illinois; the site's own theater list carries 18).

Where the count comes from
--------------------------
studiomoviegrill.com renders each theater's day on the server; its only
JSON call (`/Home/TheaterGeo`) is the theater list with map points, so
the pages are the data:

    /movies                         redirects to a default theater's
                                    page for today, whose theater picker
                                    (`li.theater-picker-item`, data-url
                                    and data-theater-id) lists every
                                    theater
    /quicktickets/<state>/<theater>/<yyyy>/<m>/<d>
                                    one theater, one day: a
                                    `#movie-grid` item per title (poster,
                                    `h2.movie-title`) and a
                                    `/ticketing/select/<film>/<theater>/
                                    <yyyy>/<mm>/<dd>/<hh>/<mm>/<am|pm>`
                                    link per showtime
    /movie/now-showing              the chain's own film order, used for
                                    ties

20 pages (the entry page, Now Showing and 18 day pages), about 3.5 MB,
read four at a time in a few seconds. Only
showtime links dated today are counted, so a theater page that falls
back to another day adds nothing. Film links go to SMG's own
`/ticketing/start/<film>` page.

SMG titles carry the rating ('Ghost Soldier (PG-13)'), and SMG lists
some prints as their own titles: 'Open Caption Hunger Games: Sunrise
on the Reaping' and the 'Special Needs' (sensory friendly) screenings.
The rating and those prefixes come off here before the prints are
merged. SMG drops a showtime from the page once it has started, so the
count is most complete in the morning (the Mac lane runs at 8 AM
Pacific, 10 AM Central).

Access
------
studiomoviegrill.com sits behind Cloudflare's browser check: plain
`requests` gets the 'Just a moment' page. curl_cffi with Chrome's TLS
fingerprint passes it with no cookies (a cold call straight to the JSON
theater list was refused on 2026-11-23, which is why the theater list
is read off the page). If the check refuses curl_cffi, the same pages
are fetched from inside a headless Chrome page opened on `/movies`
(`render_then_fetch`), checked 2026-11-23.
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Optional

from .theater_common import (DEFAULT_LIMIT, HTTP_TIMEOUT_S, base_title,
                             clean_title, rank_by_showtimes, render_then_fetch,
                             title_key, today_iso)

logger = logging.getLogger(__name__)

KEY = 'smg'
LABEL = 'Studio Movie Grill'
COOKIE_DOMAIN = None

BASE = 'https://www.studiomoviegrill.com'
ENTRY_URL = BASE + '/movies'
NOW_SHOWING_URL = BASE + '/movie/now-showing'
TZ = 'America/Chicago'
WORKERS = 4
PAUSE_S = 0.2

_WALL_TITLE_RE = re.compile(
    r'<title>\s*(?:just a moment|attention required|access denied)', re.I)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
_PICKER_OPEN_RE = re.compile(
    r'<li\b[^>]*\bclass="theater-picker-item\b[^"]*"[^>]*>', re.I)
_DATA_URL_RE = re.compile(r'\bdata-url="(/quicktickets/[^"?#]+)"', re.I)
_THEATER_ID_RE = re.compile(r'\bdata-theater-id="([^"]+)"', re.I)
_PICKER_NAME_RE = re.compile(
    r'<span\b[^>]*\bclass="theater-picker-name"[^>]*>(.*?)</span>', re.I | re.S)
_QT_HREF_RE = re.compile(r'href="(/quicktickets/[a-z-]+/[a-z0-9-]+)"', re.I)
_ITEM_OPEN_RE = re.compile(
    r'<li\b[^>]*>\s*<div\b[^>]*\bclass="movie-(?:poster|info)\b', re.I)
_START_HREF_RE = re.compile(r'href="/ticketing/start/([^"/?#]+)"', re.I)
_IMG_SRC_RE = re.compile(r'<img\b[^>]*\bsrc="([^"]+)"', re.I)
_TITLE_RE = re.compile(r'<h2\b[^>]*\bclass="movie-title"[^>]*>(.*?)</h2>', re.I | re.S)
_SELECT_HREF_RE = re.compile(
    r'href="/ticketing/select/([^"/]+)/[^"/]+/(\d{4})/(\d{1,2})/(\d{1,2})/\d{1,2}/\d{1,2}/(?:am|pm)"',
    re.I)
_TAG_RE = re.compile(r'<[^>]+>')


def parse_theaters(html: str) -> list[dict]:
    """[{'id', 'path', 'name'}] from the page's theater picker, in its
    order; failing that, from its /quicktickets/ links."""
    html = html or ''
    out: list[dict] = []
    seen: set[str] = set()
    opens = list(_PICKER_OPEN_RE.finditer(html))
    for i, m in enumerate(opens):
        end = opens[i + 1].start() if i + 1 < len(opens) else min(len(html), m.end() + 4000)
        block = html[m.start():end]
        path = _DATA_URL_RE.search(m.group(0)) or _DATA_URL_RE.search(block)
        if not path:
            continue
        p = path.group(1).rstrip('/').lower()
        if p in seen:
            continue
        seen.add(p)
        tid = _THEATER_ID_RE.search(block)
        name = _PICKER_NAME_RE.search(block)
        out.append({'id': tid.group(1) if tid else p, 'path': p,
                    'name': clean_title(_TAG_RE.sub(' ', name.group(1))) if name else ''})
    if not out:
        for m in _QT_HREF_RE.finditer(html):
            p = m.group(1).rstrip('/').lower()
            if p not in seen:
                seen.add(p)
                out.append({'id': p, 'path': p, 'name': ''})
    return out


def parse_theater_geo(text: str) -> list[dict]:
    """The same list from `/Home/TheaterGeo` JSON (the browser path
    reads it when the page carries no picker)."""
    try:
        rows = json.loads(text or '')
    except Exception:
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if isinstance(r, dict) and str(r.get('Url') or '').startswith('/quicktickets/'):
            out.append({'id': str(r.get('Id') or r['Url']), 'path': r['Url'].rstrip('/').lower(),
                        'name': clean_title(r.get('Name') or '')})
    return out


def parse_film_order(html: str) -> dict[str, int]:
    """Film slug -> position on the Now Showing page."""
    order: dict[str, int] = {}
    for m in _START_HREF_RE.finditer(html or ''):
        order.setdefault(m.group(1).lower(), len(order))
    return order


def parse_day_page(html: str, today: str) -> Optional[list[dict]]:
    """Films on one theater's day page: [{'slug', 'title', 'image',
    'showtimes'}], showtimes counting the links dated `today`. None when
    the body is not a day page."""
    html = html or ''
    start = html.find('id="movie-grid"')
    if start < 0:
        return None
    stop = html.find('<!-- /.movie-grid -->', start)
    if stop < 0:
        stop = html.find('<footer', start)
    stop = len(html) if stop < 0 else stop
    y, m, d = (int(x) for x in today.split('-'))
    opens = list(_ITEM_OPEN_RE.finditer(html, start, stop))
    films = []
    for i, mo in enumerate(opens):
        end = opens[i + 1].start() if i + 1 < len(opens) else stop
        block = html[mo.start():end]
        t = _TITLE_RE.search(block)
        title = clean_title(_TAG_RE.sub(' ', t.group(1))) if t else ''
        if not title:
            continue
        shows, slug = 0, ''
        for s in _SELECT_HREF_RE.finditer(block):
            slug = slug or s.group(1).lower()
            if (int(s.group(2)), int(s.group(3)), int(s.group(4))) == (y, m, d):
                shows += 1
        start_link = _START_HREF_RE.search(block)
        if start_link:
            slug = start_link.group(1).lower()
        img = _IMG_SRC_RE.search(block)
        films.append({'slug': slug, 'title': title,
                      'image': img.group(1) if img else '', 'showtimes': shows})
    return films


def day_path(path: str, today: str) -> str:
    """'/quicktickets/texas/plano' + '2026-12-01' -> the theater's day
    page, written the way the site writes it ('/2026/12/1')."""
    d = date.fromisoformat(today)
    return f'{BASE}{path}/{d.year}/{d.month}/{d.day}'


# ---------------------------------------------------------------------------
# Titles and rows
# ---------------------------------------------------------------------------
_RATING_TAIL_RE = re.compile(
    r'\s*\((?:G|PG|PG-13|R|NC-17|NR|TBD|Not Rated|Unrated)\)\s*$', re.I)
# SMG lists an open captioned print and its sensory friendly 'Special
# Needs' screenings as titles of their own.
_PRINT_PREFIX_RE = re.compile(
    r'^(?:open[ -]?caption(?:ed)?|special needs|sensory[ -]?friendly)\b\s*[:\-]?\s*', re.I)


def prepare_title(raw: str) -> tuple[str, bool]:
    """(title to merge on, True when this is the plain print)."""
    s = _RATING_TAIL_RE.sub('', clean_title(raw)).strip()
    tagged = bool(_PRINT_PREFIX_RE.match(s))
    s = _PRINT_PREFIX_RE.sub('', s).strip() or s
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


def build_prints(pages: dict[str, Optional[str]], today: str,
                 order: dict[str, int]) -> tuple[list[dict], dict]:
    """Merge day pages {theater id: html} into one print per film slug,
    counting only today's showtimes. Returns (prints, stats)."""
    acc: dict[str, dict] = {}
    stats = {'read': 0, 'showing': 0}
    for theater, html in pages.items():
        if not html or is_walled(html):
            continue
        films = parse_day_page(html, today)
        if films is None:
            continue
        stats['read'] += 1
        live = False
        for f in films:
            if f['showtimes'] <= 0:
                continue
            live = True
            key = f['slug'] or title_key(f['title'])
            p = acc.get(key)
            if p is None:
                title, plain = prepare_title(f['title'])
                p = acc[key] = {
                    'title': title, 'plain': plain,
                    'url': f'{BASE}/ticketing/start/{f["slug"]}' if f['slug'] else ENTRY_URL,
                    'image': f['image'], 'showtimes': 0, 'theater_ids': set(),
                    'site_order': order.get(f['slug'], 10_000 + len(acc)),
                }
            p['showtimes'] += f['showtimes']
            p['theater_ids'].add(theater)
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
    first = site.open()
    if not first:
        meta['blocked'] = site.walled
        meta['reason'] = ('a bot check refused the Movies page' if site.walled
                          else 'the Movies page did not answer')
        return []
    theaters = parse_theaters(first)
    lists: dict[str, Optional[str]] = {}
    if not theaters:
        lists = site.many([NOW_SHOWING_URL, BASE + '/Home/TheaterGeo'])
        theaters = parse_theater_geo(lists.get(BASE + '/Home/TheaterGeo') or '')
    meta['theaters_listed'] = len(theaters)
    if not theaters:
        meta['reason'] = 'no theaters in the theater picker'
        return []
    urls = {t['id']: day_path(t['path'], today) for t in theaters}
    # The Now Showing page rides along with the day pages: one browser
    # visit for both when the direct read is refused.
    got = site.many(list(urls.values()) + ([] if lists else [NOW_SHOWING_URL]))
    meta['via'] = site.mode
    order = parse_film_order(lists.get(NOW_SHOWING_URL) or got.get(NOW_SHOWING_URL) or '')
    prints, stats = build_prints({tid: got.get(u) for tid, u in urls.items()}, today, order)
    meta['theaters'] = stats['read']
    meta['theaters_showing'] = stats['showing']
    if not stats['read']:
        meta['blocked'] = site.walled
        meta['reason'] = ('a bot check refused the theater pages' if site.walled
                          else 'no theater page could be read')
        return []
    if stats['read'] < len(theaters):
        logger.info('smg: read %d of %d theaters', stats['read'], len(theaters))
    items = rank_by_showtimes(_film_rows(prints), limit=limit)
    if not items:
        meta['reason'] = 'no showtimes today'
    return items


def fetch(limit: int = DEFAULT_LIMIT) -> tuple[list[dict], dict]:
    """(items, meta) for the Studio Movie Grill card. Never raises."""
    t0 = time.time()
    today = today_iso(TZ)
    meta = {'theaters': 0, 'theaters_listed': 0, 'date': today, 'blocked': False}
    try:
        items = _read(limit, today, meta)
    except Exception as e:
        logger.warning('smg: read failed (%s)', e)
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
