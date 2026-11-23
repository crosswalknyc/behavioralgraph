"""
Galaxy Theatres card: the films with the most showtimes today across
every Galaxy theater (16 on 2026-11-23, in California, Nevada,
Arizona, Texas and Washington).

Where the count comes from
--------------------------
galaxytheatres.com runs on a hosted theater-site platform over Vista
ticketing (posters come from galaxy-digital-cdn.app.vista.co), and it
renders every page on the server. The web front end has no JSON
showtimes call: the date strip swaps in server-rendered HTML. So the
theater pages are the data:

    /movies                      the chain-wide Now Playing list (its
                                 order breaks ties) and the Locations
                                 menu (the theater list, so a theater
                                 that opens or closes needs no change)
    /movie-theater/<slug>        one theater, opened on today's date
                                 (the active `dates__item` carries
                                 data-date="11/23/2026"), one
                                 `nowPlaying__item` per title and an
                                 `a.button--showtime` per showtime

17 pages, about 4 MB, read four at a time in a few seconds. Plain
requests work; there is no bot check.

Titles come in capitals ('THE HUNGER GAMES: SUNRISE ON THE REAPING').
They are set in title case for the card, with acronyms, roman numerals
and dotted abbreviations kept as written. Galaxy tags an open captioned
print '(OPEN CAP)', which the shared title cleaner does not know, so it
is spelled out here before the prints are merged. A Spanish-language
print that Galaxy lists under its Spanish title ('LOS JUEGOS DEL
HAMBRE: AMANECER EN LA COSECHA') keeps its own row: nothing on the page
ties it to the English title.

Event cinema (the Met Opera, ballet, concert films, the Mystery Movie
night) stays in when it has a showtime today. Galaxy drops a showtime
from the page once it has started, so the count is most complete in
the morning (the Mac lane runs at 8 AM Pacific).
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from .theater_common import (DEFAULT_LIMIT, HTTP_TIMEOUT_S, base_title,
                             clean_title, rank_by_showtimes, session,
                             title_key, today_iso)

logger = logging.getLogger(__name__)

KEY = 'galaxy'
LABEL = 'Galaxy Theatres'
COOKIE_DOMAIN = None

BASE = 'https://www.galaxytheatres.com'
LIST_URL = BASE + '/movies'
# Most Galaxy theaters are on Pacific time; the two in Texas are an
# hour or two ahead, which only matters for a run near midnight.
TZ = 'America/Los_Angeles'
WORKERS = 4
PAUSE_S = 0.2


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
_THEATER_HREF_RE = re.compile(r'href="/movie-theater/([A-Za-z0-9-]+)"')
_MOVIE_HREF_RE = re.compile(r'href="/movie/([^"/#?]+)"')
_ACTIVE_DATE_TAG_RE = re.compile(
    r'<div\b[^>]*\bclass="[^"]*\bdates__item\b[^"]*\bactive\b[^"]*"[^>]*>', re.I)
_DATA_DATE_RE = re.compile(r'\bdata-date="(\d{1,2})/(\d{1,2})/(\d{4})"')
_ITEM_OPEN_RE = re.compile(
    r'<div\b[^>]*\bclass="nowPlaying__item(?:\s[^"]*)?"[^>]*>', re.I)
_DATA_LINK_RE = re.compile(r'\bdata-link="([^"]*)"', re.I)
_H3_OPEN_RE = re.compile(r'<h3\b[^>]*\bclass="nowPlaying__movieTitle"[^>]*>', re.I)
_TITLE_ATTR_RE = re.compile(r'(?<![\w-])title="([^"]*)"', re.I)
_POSTER_RE = re.compile(r'\bdata-src="(https?://[^"]+)"', re.I)
# Inside a title block every element with this class is a showtime, so a
# sold-out time drawn without a link still counts.
_SHOWTIME_RE = re.compile(
    r'<(?:a|div|span|button)\b[^>]*\bclass="[^"]*\bbutton--showtime\b[^"]*"', re.I)
_TAG_RE = re.compile(r'<[^>]+>')
_WALL_TITLE_RE = re.compile(
    r'<title>\s*(?:just a moment|attention required|access denied)', re.I)


def parse_theater_slugs(html: str) -> list[str]:
    """Theater slugs in the order the site's Locations menu lists them."""
    out: list[str] = []
    for m in _THEATER_HREF_RE.finditer(html or ''):
        slug = m.group(1).lower()
        if slug not in out:
            out.append(slug)
    return out


def parse_site_order(html: str) -> dict[str, int]:
    """Film slug -> position on the chain-wide Now Playing list."""
    order: dict[str, int] = {}
    for m in _MOVIE_HREF_RE.finditer(html or ''):
        order.setdefault(m.group(1).upper(), len(order))
    return order


def parse_theater_page(html: str) -> tuple[Optional[str], list[dict]]:
    """(the date the page shows as YYYY-MM-DD or None, prints).

    Each print: {'slug', 'title', 'image', 'showtimes'} for one title
    block on the page. The block runs from its own opening tag to the
    next one; the last runs to the footer, so the sample showtime
    button in the page's legend (which sits above the list) is never
    counted.
    """
    html = html or ''
    day = None
    tag = _ACTIVE_DATE_TAG_RE.search(html)
    if tag:
        d = _DATA_DATE_RE.search(tag.group(0))
        if d:
            day = f'{int(d.group(3)):04d}-{int(d.group(1)):02d}-{int(d.group(2)):02d}'
    start = html.find('id="nowPlaying"')
    start = 0 if start < 0 else start
    stop = html.find('<footer', start)
    stop = len(html) if stop < 0 else stop
    opens = list(_ITEM_OPEN_RE.finditer(html, start, stop))
    prints = []
    for i, m in enumerate(opens):
        end = opens[i + 1].start() if i + 1 < len(opens) else stop
        block = html[m.start():end]
        link = _DATA_LINK_RE.search(m.group(0))
        slug = link.group(1).strip('/').rsplit('/', 1)[-1] if link else ''
        title = ''
        h3 = _H3_OPEN_RE.search(block)
        if h3:
            attr = _TITLE_ATTR_RE.search(h3.group(0))
            if attr:
                title = clean_title(attr.group(1))
            if not title:
                close = block.find('</h3>', h3.end())
                title = clean_title(_TAG_RE.sub(' ', block[h3.end():close]))
        if not title:
            continue
        poster = _POSTER_RE.search(block)
        prints.append({
            'slug': slug,
            'title': title,
            'image': poster.group(1) if poster else '',
            'showtimes': len(_SHOWTIME_RE.findall(block)),
        })
    return day, prints


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------
_SMALL_WORDS = frozenset(
    'a an and as at but by de del en et for from in into nor of on onto or '
    'per the to via vs with'.split())
_KEEP_UPPER = frozenset({
    'AMC', 'BBC', 'BTS', 'DJ', 'ESPN', 'HBO', 'IMAX', 'MLB', 'MTV', 'NBA',
    'NCAA', 'NFL', 'NHL', 'OK', 'TCM', 'TV', 'UFC', 'USA', 'WNBA', 'WWE'})
# Short words with no vowel that are abbreviations read as words.
_NO_VOWEL_WORDS = frozenset({'MR', 'MRS', 'MS', 'DR', 'ST', 'JR', 'SR', 'MT', 'FT'})
_ROMAN_RE = re.compile(r'^(?=[IVX]{2,}$)X{0,3}(?:IX|IV|V?I{0,3})$')
_ORDINAL_RE = re.compile(r'^(\d+)(ST|ND|RD|TH)$', re.I)
_EDGE_RE = re.compile(r'^([^A-Za-z0-9]*)(.*?)([^A-Za-z0-9]*)$', re.S)
_OPEN_CAP_RE = re.compile(r'\bopen[\s-]+cap\b\.?', re.I)


def _case_part(part: str, force: bool) -> str:
    if not part:
        return part
    letters = re.sub(r'[^A-Za-z]', '', part)
    if not letters:
        return part
    if any(ch.isdigit() for ch in part):
        m = _ORDINAL_RE.match(part)
        return m.group(1) + m.group(2).lower() if m else part
    if '.' in part:
        return part                      # A.D., U.S., L.A.
    up = part.upper()
    if up in _KEEP_UPPER or _ROMAN_RE.match(up):
        return up
    if (len(letters) >= 2 and not re.search(r'[AEIOUY]', up)
            and up not in _NO_VOWEL_WORDS):
        return up                        # BTS, MXT, NCT
    low = part.lower()
    if low in _SMALL_WORDS and not force:
        return low
    pieces = re.split(r"(['\u2019])", low)
    out = [pieces[0][:1].upper() + pieces[0][1:]]
    for j in range(1, len(pieces), 2):
        tail = pieces[j + 1] if j + 1 < len(pieces) else ''
        out.append(pieces[j])
        out.append(tail if len(tail) <= 2 else tail[:1].upper() + tail[1:])
    return ''.join(out)


def title_case(text: str) -> str:
    """'THE HUNGER GAMES: SUNRISE ON THE REAPING' -> 'The Hunger Games:
    Sunrise on the Reaping'. Small words stay lower case unless they
    open the title, follow a colon or a dash, open a parenthesis or end
    the title; acronyms, roman numerals and dotted abbreviations keep
    their capitals."""
    words = (text or '').split(' ')
    out = []
    force = True
    for i, w in enumerate(words):
        lead, core, trail = _EDGE_RE.match(w).groups()
        if not core:
            out.append(w)
            force = force or bool(w.strip())
            continue
        last_word = i == len(words) - 1
        first = force or '(' in lead or last_word
        parts = core.split('-')
        cased = [_case_part(p, first if j == 0 else (last_word and j == len(parts) - 1))
                 for j, p in enumerate(parts)]
        out.append(lead + '-'.join(cased) + trail)
        force = trail.endswith((':', '-'))
    return ' '.join(out)


def prepare_title(raw: str) -> tuple[str, bool]:
    """(title to merge on, True when this is the plain print)."""
    s = clean_title(raw)
    if s and not any(ch.islower() for ch in s):
        s = title_case(s)
    s = _OPEN_CAP_RE.sub('Open Caption', s)
    return s, base_title(s) == s


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------
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


def build_prints(pages: dict[str, str], today: str,
                 order: dict[str, int]) -> tuple[list[dict], dict]:
    """Merge theater pages {theater slug: html} into one print per film
    slug, counting only showtimes on `today`. Returns (prints, stats)."""
    acc: dict[str, dict] = {}
    stats = {'read': 0, 'showing': 0, 'walled': 0, 'other_day': 0}
    for theater, html in pages.items():
        if not html:
            continue
        if is_walled(html):
            stats['walled'] += 1
            continue
        day, found = parse_theater_page(html)
        if day is None and not found:
            continue
        stats['read'] += 1
        if day != today:
            stats['other_day'] += 1
            continue
        live = False
        for f in found:
            if f['showtimes'] <= 0:
                continue
            live = True
            key = (f['slug'] or f['title']).upper()
            p = acc.get(key)
            if p is None:
                title, plain = prepare_title(f['title'])
                p = acc[key] = {
                    'title': title, 'plain': plain,
                    'url': f'{BASE}/movie/{f["slug"]}' if f['slug'] else LIST_URL,
                    'image': f['image'], 'showtimes': 0, 'theater_ids': set(),
                    'site_order': order.get(key, 10_000 + len(acc)),
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


def _get(sess, url: str, retries: int = 2) -> Optional[str]:
    """Body text (a bot-check page included, so the caller can tell),
    or None. Never raises."""
    for attempt in range(retries + 1):
        try:
            r = sess.get(url, timeout=HTTP_TIMEOUT_S)
        except Exception as e:
            logger.info('galaxy: %s failed (%s)', url[:120], str(e)[:120])
        else:
            if r.status_code == 200 or is_walled(r.text):
                return r.text
            logger.info('galaxy: %s answered %s', url[:120], r.status_code)
            if r.status_code in (401, 403, 404, 410):
                return None
        time.sleep(1.5 * (attempt + 1))
    return None


def _read(limit: int, today: str, meta: dict) -> list[dict]:
    sess = session()
    listing = _get(sess, LIST_URL)
    if not listing:
        meta['reason'] = 'the Now Playing page did not answer'
        return []
    if is_walled(listing):
        meta['blocked'] = True
        meta['reason'] = 'a bot check answered the Now Playing page'
        return []
    slugs = parse_theater_slugs(listing)
    meta['theaters_listed'] = len(slugs)
    if not slugs:
        meta['reason'] = 'no theaters on the Now Playing page'
        return []

    def one(slug: str) -> tuple[str, Optional[str]]:
        time.sleep(PAUSE_S)
        return slug, _get(sess, f'{BASE}/movie-theater/{slug}')

    with ThreadPoolExecutor(WORKERS) as ex:
        pages = dict(ex.map(one, slugs))
    prints, stats = build_prints(pages, today, parse_site_order(listing))
    meta['theaters'] = stats['read']
    meta['theaters_showing'] = stats['showing']
    if stats['walled'] and not stats['read']:
        meta['blocked'] = True
        meta['reason'] = 'a bot check answered every theater page'
        return []
    if stats['read'] < len(slugs):
        logger.info('galaxy: read %d of %d theaters', stats['read'], len(slugs))
    items = rank_by_showtimes(_film_rows(prints), limit=limit)
    if not items:
        meta['reason'] = ("no theater page showed today's date"
                          if stats['other_day'] == stats['read'] else 'no showtimes today')
    return items


def fetch(limit: int = DEFAULT_LIMIT) -> tuple[list[dict], dict]:
    """(items, meta) for the Galaxy card. Never raises."""
    t0 = time.time()
    today = today_iso(TZ)
    meta = {'theaters': 0, 'theaters_listed': 0, 'date': today, 'blocked': False}
    try:
        items = _read(limit, today, meta)
    except Exception as e:
        logger.warning('galaxy: read failed (%s)', e)
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
