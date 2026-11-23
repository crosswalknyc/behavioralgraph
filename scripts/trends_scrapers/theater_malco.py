"""
Malco Theatres card: the films with the most showtimes today across
every Malco cinema (31 on 2026-11-23: Memphis and the Mid-South, in
Tennessee, Arkansas, Mississippi, Kentucky, Louisiana and Missouri).

Where the count comes from
--------------------------
malco.com renders each cinema's schedule on the server (`/movies` is a
404 page; the cinema pages are the schedule):

    /                       the home page: the Locations menu
                            (`select#locationsMenuSelect`, so a new or
                            closed cinema needs no change) and the Now
                            Playing menu (`select#nowplayingMenuSelect`,
                            the chain's own film order, used for ties)
    /cinemas/<slug>/        one cinema, today: `const pageDate =
                            "2026-11-23"`, one `div.film` block per
                            title and an `a.time` link per showtime
                            (`data-datetime="2026-11-23 19:00:00"`)

The site's two JSON files (`/json/search.json`, `/cinemas/<slug>/
titles.json`) list titles but no showtimes, so they are not used. 32
pages, about 11 MB, read four at a time in a couple of seconds.

Malco keeps each format in its own block ('Hunger Games: Sunrise on the
Reaping (IMAX)', '(Dolby Atmos)', '(MXT-Atmos)'). The shared title
cleaner folds IMAX and Dolby Atmos into the plain title; MXT is Malco's
own premium screen, so its tag is taken off here. Malco writes a
remake's year as ' - 2026' ('The Cat in the Hat - 2026'); it is
written '(2026)' here, the way the shared cleaner reads a year.

Malco drops a showtime from the page a while after it starts, so the
day's count is most complete in the morning (the Mac lane runs at 8 AM
Pacific, 10 AM Central). A cinema whose page lists no showtimes today
(Wolfchase on 2026-11-23) is read and counts as read.

Access
------
malco.com sits behind Cloudflare's browser check: plain `requests`
gets the 'Just a moment' page (403). curl_cffi with Chrome's TLS
fingerprint passes it with no cookies, so that is tried first. If the
check refuses it, the same pages are fetched from inside a headless
Chrome page opened on the home page (`render_then_fetch`), which passes
the check the way a visitor does (about 8 seconds per browser visit,
checked 2026-11-23).
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import urljoin

from .theater_common import (DEFAULT_LIMIT, HTTP_TIMEOUT_S, base_title,
                             clean_title, rank_by_showtimes, render_then_fetch,
                             title_key, today_iso)

logger = logging.getLogger(__name__)

KEY = 'malco'
LABEL = 'Malco Theatres'
COOKIE_DOMAIN = None

BASE = 'https://www.malco.com'
ENTRY_URL = BASE + '/'
TZ = 'America/Chicago'
WORKERS = 4
PAUSE_S = 0.2


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
_OPTION_RE = re.compile(r'<option\b[^>]*\bvalue="([^"]*)"', re.I)
_CINEMA_HREF_RE = re.compile(
    r'href="(?:https?://(?:www\.)?malco\.com)?/cinemas/([a-z0-9-]+)/?"', re.I)
_PAGE_DATE_RE = re.compile(r'\bpageDate\s*=\s*"(\d{4}-\d\d-\d\d)"')
_FILM_OPEN_RE = re.compile(r'<div\b[^>]*\bclass="film(?:\s[^"]*)?"[^>]*>', re.I)
_ID_ATTR_RE = re.compile(r'(?<![\w-])id="([^"]+)"')
_H3_TITLE_RE = re.compile(r'<h3\b[^>]*\bclass="title"[^>]*>(.*?)</h3>', re.I | re.S)
_IMG_SRC_RE = re.compile(r'<img\b[^>]*\bsrc="([^"]+)"', re.I)
# A showtime is an `a.time`; any element with that class counts, so a
# sold-out time drawn without a link still counts.
_TIME_TAG_RE = re.compile(
    r'<(?:a|span|div|button)\b[^>]*\bclass="time(?:\s[^"]*)?"[^>]*>', re.I)
_DATETIME_RE = re.compile(r'\bdata-datetime="(\d{4}-\d\d-\d\d)')
_TAG_RE = re.compile(r'<[^>]+>')
_SLUG_RE = re.compile(r'[a-z0-9][a-z0-9-]*')
_WALL_TITLE_RE = re.compile(
    r'<title>\s*(?:just a moment|attention required|access denied)', re.I)


def _select_values(html: str, select_id: str) -> list[str]:
    m = re.search(r'<select\b[^>]*\bid="%s"[^>]*>(.*?)</select>' % re.escape(select_id),
                  html or '', re.I | re.S)
    if not m:
        return []
    out: list[str] = []
    for v in _OPTION_RE.findall(m.group(1)):
        v = v.strip().lower()
        if _SLUG_RE.fullmatch(v) and v not in out:
            out.append(v)
    return out


def parse_cinemas(html: str) -> list[str]:
    """Cinema slugs from the Locations menu, plus any /cinemas/ link the
    menu does not carry."""
    slugs = _select_values(html, 'locationsMenuSelect')
    for m in _CINEMA_HREF_RE.finditer(html or ''):
        slug = m.group(1).lower()
        if slug not in slugs:
            slugs.append(slug)
    return slugs


def parse_film_order(html: str) -> dict[str, int]:
    """Film slug -> position in the Now Playing menu."""
    return {slug: i for i, slug in enumerate(_select_values(html, 'nowplayingMenuSelect'))}


def parse_cinema_page(html: str, today: str) -> tuple[Optional[str], list[dict]]:
    """(the page's own date or None, films). Each film: {'slug',
    'title', 'image', 'showtimes'}, showtimes counting the links dated
    `today` (a link with no date counts when the page is today's)."""
    html = html or ''
    m = _PAGE_DATE_RE.search(html)
    page_date = m.group(1) if m else None
    start = html.find('<div class="films"')
    if start < 0:
        return page_date, []
    stop = html.find('const thisCinemaName', start)
    stop = len(html) if stop < 0 else stop
    opens = list(_FILM_OPEN_RE.finditer(html, start, stop))
    films = []
    for i, m in enumerate(opens):
        end = opens[i + 1].start() if i + 1 < len(opens) else stop
        block = html[m.start():end]
        t = _H3_TITLE_RE.search(block)
        title = clean_title(_TAG_RE.sub(' ', t.group(1))) if t else ''
        if not title:
            continue
        idm = _ID_ATTR_RE.search(m.group(0))
        image = ''
        at = block.find('class="poster"')
        if at >= 0:
            until = block.find('class="showtimes"', at)
            img = _IMG_SRC_RE.search(block, at, until if until > at else len(block))
            if img:
                image = urljoin(ENTRY_URL, img.group(1))
        shows = 0
        for tag in _TIME_TAG_RE.findall(block):
            d = _DATETIME_RE.search(tag)
            if (d.group(1) if d else page_date) == today:
                shows += 1
        films.append({'slug': idm.group(1).strip() if idm else '',
                      'title': title, 'image': image, 'showtimes': shows})
    return page_date, films


# ---------------------------------------------------------------------------
# Titles and rows
# ---------------------------------------------------------------------------
# Malco's premium screen (MXT) and sound tags. The shared cleaner knows
# IMAX and 'Dolby Atmos', not '(MXT-Atmos)' or a bare 'Atmos'.
_MALCO_TAG_RE = re.compile(r'\s*\((?=[^)]*\b(?:mxt|atmos|d-?box)\b)[^)]*\)', re.I)
_DASH_YEAR_RE = re.compile(r'\s+-\s+((?:19|20)\d\d)\s*$')


def prepare_title(raw: str) -> tuple[str, bool]:
    """(title to merge on, True when this is the plain print)."""
    s = clean_title(raw)
    tagged = bool(_MALCO_TAG_RE.search(s))
    s = _MALCO_TAG_RE.sub('', s).strip()
    s = _DASH_YEAR_RE.sub(r' (\1)', s)
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
    """Merge cinema pages {cinema slug: html} into one print per film
    block id, counting only today's showtimes. Returns (prints, stats)."""
    acc: dict[str, dict] = {}
    stats = {'read': 0, 'showing': 0}
    for cinema, html in pages.items():
        if not html or is_walled(html):
            continue
        page_date, films = parse_cinema_page(html, today)
        if page_date is None and '<div class="films"' not in html:
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
                    'url': f'{BASE}/nowplaying/{f["slug"]}/' if f['slug'] else ENTRY_URL,
                    'image': f['image'], 'showtimes': 0, 'theater_ids': set(),
                    'site_order': order.get(f['slug'], 10_000 + len(acc)),
                }
            p['showtimes'] += f['showtimes']
            p['theater_ids'].add(cinema)
            p['image'] = p['image'] or f['image']
        stats['showing'] += live
    return list(acc.values()), stats


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
def is_walled(text: str) -> bool:
    """True when a body is a bot-check page. The page title is the
    test: the shared `looks_walled` reads any 'captcha' in the first
    6,000 characters as a wall, and Malco's own pages load reCAPTCHA
    about 7,600 characters in, so a slightly longer head would make
    every real page read as one."""
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
    home = site.open()
    if not home:
        meta['blocked'] = site.walled
        meta['reason'] = ('a bot check refused the home page' if site.walled
                          else 'the home page did not answer')
        return []
    cinemas = parse_cinemas(home)
    meta['theaters_listed'] = len(cinemas)
    if not cinemas:
        meta['reason'] = 'no cinemas in the Locations menu'
        return []
    urls = {c: f'{BASE}/cinemas/{c}/' for c in cinemas}
    got = site.many(list(urls.values()))
    meta['via'] = site.mode
    pages = {c: got.get(u) for c, u in urls.items()}
    prints, stats = build_prints(pages, today, parse_film_order(home))
    meta['theaters'] = stats['read']
    meta['theaters_showing'] = stats['showing']
    if not stats['read']:
        meta['blocked'] = site.walled
        meta['reason'] = ('a bot check refused the cinema pages' if site.walled
                          else 'no cinema page could be read')
        return []
    if stats['read'] < len(cinemas):
        logger.info('malco: read %d of %d cinemas', stats['read'], len(cinemas))
    items = rank_by_showtimes(_film_rows(prints), limit=limit)
    if not items:
        meta['reason'] = 'no showtimes today'
    return items


def fetch(limit: int = DEFAULT_LIMIT) -> tuple[list[dict], dict]:
    """(items, meta) for the Malco card. Never raises."""
    t0 = time.time()
    today = today_iso(TZ)
    meta = {'theaters': 0, 'theaters_listed': 0, 'date': today, 'blocked': False}
    try:
        items = _read(limit, today, meta)
    except Exception as e:
        logger.warning('malco: read failed (%s)', e)
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
