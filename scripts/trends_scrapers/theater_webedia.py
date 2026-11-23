"""
Theater-chain cards for the five chains on the Webedia Movies Pro
platform: B&B Theatres, Showcase Cinemas (US), Landmark Theatres,
Marquee Cinemas and EPIC Theatres.

All five run the same Gatsby theater site, and all of it answers plain
HTTP, so the daily read needs no browser:

  * The homepage names the build folder on the platform's asset host,
    https://cms-assets.webediamovies.pro/prod/<site code>/<build>/public.
  * Three Gatsby static queries in that folder carry what the read
    needs: the theater list (`allTheater`: id, time zone, country,
    closure), the film list (`allMovie`: id, title, page path, poster,
    the chain's own order) and the site settings (the movies page slug,
    the showtime tags the site hides everywhere).
  * The site's own data endpoint answers the schedule for a set of
    theaters and a time window, the same call its showtimes pages make:
    /api/gatsby-source-boxofficeapi/schedule?theaters={"id","timeZone"}
    &from=<local start>&to=<local end>. The window is read in each
    theater's own time zone and the answer is grouped by calendar day.

The count is every showtime a film has today across all of the chain's
open US theaters, the morning's past shows included, so the number does
not move with the hour the read runs. A chain reads in a few seconds
(one schedule call per 20 theaters).

    SITES                      the five chains, keyed for the registry
    fetch(site, limit)         (items, meta), never raises

    python3 -m scripts.trends_scrapers.theater_webedia [site ...]
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter

from .theater_common import (DEFAULT_LIMIT, HTTP_TIMEOUT_S, clean_title,
                             looks_walled, rank_by_showtimes,
                             render_then_fetch, session, today_iso)

logger = logging.getLogger(__name__)

SITES: dict[str, dict] = {
    'bbtheatres': {'label': 'B&B Theatres',
                   'base': 'https://www.bbtheatres.com',
                   'code': 'bandb', 'cookie_domain': None},
    'showcase': {'label': 'Showcase Cinemas',
                 'base': 'https://www.showcasecinemas.com',
                 'code': 'scus', 'cookie_domain': None},
    'landmark': {'label': 'Landmark Theatres',
                 'base': 'https://www.landmarktheatres.com',
                 'code': 'landmarktheatres', 'cookie_domain': None},
    'marquee': {'label': 'Marquee Cinemas',
                'base': 'https://www.marqueecinemas.com',
                'code': 'marquee-cinemas', 'cookie_domain': None},
    'epic': {'label': 'EPIC Theatres',
             'base': 'https://www.epictheatres.com',
             'code': 'epictheatres', 'cookie_domain': None},
}

ASSET_HOST = 'https://cms-assets.webediamovies.pro/prod'
SCHEDULE_PATH = '/api/gatsby-source-boxofficeapi/schedule'
MOVIES_PATH = '/api/gatsby-source-boxofficeapi/movies'

THEATERS_PER_CALL = 20
MOVIES_PER_CALL = 50
MAX_WORKERS = 4          # concurrent requests per site, never more
PAUSE_S = 0.25           # before every request a worker makes
RETRY_PAUSE_S = 1.5
BUDGET_S = 110           # a chain stops asking for more after this

_DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_PREFIX_RE = re.compile(
    r'https://cms-assets\.webediamovies\.pro/prod/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/public')


# ---------------------------------------------------------------------------
# Static queries
# ---------------------------------------------------------------------------
def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _nodes(data: Any, key: str) -> list:
    nodes = _dict(_dict(data).get(key)).get('nodes')
    return nodes if isinstance(nodes, list) else []


def _has_theaters(data: Any) -> bool:
    return any(isinstance(n, dict) and n.get('id') and n.get('timeZone')
               for n in _nodes(data, 'allTheater'))


def _has_movies(data: Any) -> bool:
    return any(isinstance(n, dict) and n.get('id') and n.get('path')
               for n in _nodes(data, 'allMovie'))


def _has_settings(data: Any) -> bool:
    return isinstance(data, dict) and 'moviesPage' in data


# The query hashes come from the query text, so every site on one theme
# build shares them. They are tried first; when the theme changes, the
# homepage's own list of hashes is searched for the same shapes.
_SQ: dict[str, tuple[str, Callable[[Any], bool]]] = {
    'theaters': ('2506275789', _has_theaters),
    'movies': ('3836549025', _has_movies),
    'settings': ('1797587525', _has_settings),
}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _session() -> requests.Session:
    s = session()
    adapter = HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
    s.mount('https://', adapter)
    return s


def _get(sess, url: str, *, retries: int = 2,
         timeout: int = HTTP_TIMEOUT_S) -> tuple[int, Optional[str]]:
    """(status, body). Status 0 when nothing answered. Never raises.
    Unlike the shared get_text this keeps the body of a refusal, which
    is how a bot check is told apart from an outage."""
    status, body = 0, None
    for attempt in range(retries + 1):
        try:
            r = sess.get(url, timeout=timeout)
            status, body = int(r.status_code), r.text
            if status == 200 or status in (401, 403, 404, 410, 429):
                return status, body
            logger.info("webedia: %s answered %s", url[:120], status)
        except Exception as e:
            status, body = 0, None
            logger.info("webedia: %s failed (%s)", url[:120], str(e)[:120])
        if attempt < retries:
            time.sleep(RETRY_PAUSE_S * (attempt + 1))
    return status, body


def _json(body: Optional[str]) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def _walled(status: int, body: Optional[str]) -> bool:
    """A bot check rather than the site. JSON bodies are never walls:
    the sites' own copy carries words like 'captcha'."""
    if status in (403, 429):
        return True
    text = (body or '').lstrip()
    if not text or text[:1] in '{[':
        return False
    return looks_walled(text)


def _pool_map(fn: Callable, items: list) -> list:
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(items))) as ex:
        return list(ex.map(fn, items))


# ---------------------------------------------------------------------------
# Parsing (pure, covered by test_theater_webedia.py)
# ---------------------------------------------------------------------------
def asset_prefix(html: str, code: Optional[str] = None) -> Optional[str]:
    """The build folder the homepage loads its assets from."""
    counts = Counter(m.group(0) for m in _PREFIX_RE.finditer(html or ''))
    if code:
        mine = Counter({p: n for p, n in counts.items() if f'/prod/{code}/' in p})
        counts = mine or counts
    return counts.most_common(1)[0][0] if counts else None


def chain_theaters(data: Any) -> list[dict]:
    """[{'id', 'timeZone', 'name'}] for the chain's open US theaters, in
    the chain's own order."""
    nodes = _nodes(data, 'allTheater')
    zones = Counter(n.get('timeZone') for n in nodes
                    if isinstance(n, dict) and n.get('timeZone'))
    fallback = zones.most_common(1)[0][0] if zones else 'America/New_York'
    out, seen = [], set()
    for n in nodes:
        if not isinstance(n, dict) or not n.get('id'):
            continue
        tid = str(n['id'])
        iso = str(_dict(n.get('country')).get('iso31661A2') or '').upper()
        if tid in seen or (iso and iso != 'US') or _dict(n.get('practicalInfo')).get('closed'):
            continue
        seen.add(tid)
        out.append({'id': tid, 'timeZone': str(n.get('timeZone') or fallback),
                    'name': clean_title(n.get('name') or '')})
    return out


def main_time_zone(theaters: list[dict]) -> str:
    zones = Counter(t['timeZone'] for t in theaters if t.get('timeZone'))
    return zones.most_common(1)[0][0] if zones else 'America/Chicago'


def excluded_tags(settings_data: Any) -> list[str]:
    tags = _dict(_dict(settings_data).get('settings')).get('globalExcludedTags')
    return sorted({str(t) for t in tags if t}) if isinstance(tags, list) else []


def _int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def movie_index(data: Any) -> dict[str, dict]:
    """{movie id: {'title', 'path', 'poster', 'order', 'pos', 'hidden'}}.
    'order' is the chain's own placement (orderIndex), 'pos' the place
    in the published list; both only ever break a tie."""
    nodes = _nodes(data, 'allMovie')
    out: dict[str, dict] = {}
    for pos, n in enumerate(nodes):
        if not isinstance(n, dict) or not n.get('id'):
            continue
        ov = _dict(n.get('overrides'))
        out[str(n['id'])] = {
            'title': clean_title(ov.get('title') or n.get('title') or ''),
            'path': str(n.get('path') or ''),
            'poster': str(ov.get('poster') or n.get('poster') or ''),
            'order': _int(n.get('orderIndex')),
            'pos': pos,
            'hidden': bool(n.get('shouldBeHidden')),
        }
    return out


def movies_page_slug(settings_data: Any, movies: dict[str, dict]) -> str:
    slug = str(_dict(_dict(settings_data).get('moviesPage')).get('slug') or '').strip('/')
    if slug:
        return slug
    heads = Counter(m['path'].strip('/').split('/')[0]
                    for m in movies.values() if m.get('path', '').strip('/'))
    return heads.most_common(1)[0][0] if heads else 'movies'


_SLUG_WORDS = (('&', 'and'), ('$', 'dollar'), ('%', 'percent'),
               ('<', 'less'), ('>', 'greater'), ('|', 'or'))


def film_slug(title: str) -> str:
    """The slug the sites put after the film id in a page path:
    'B&B Mystery Movie' -> 'bandb-mystery-movie'."""
    s = unicodedata.normalize('NFKD', clean_title(title))
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    for ch, word in _SLUG_WORDS:
        s = s.replace(ch, word)
    s = re.sub(r'[^a-z0-9\s-]', '', s.lower())
    return re.sub(r'[\s-]+', '-', s).strip('-')


def film_url(base: str, movie_id: str, info: dict, movies_slug: str) -> str:
    """The chain's own page for the film. The sites answer the path
    with a trailing slash and redirect the bare one to it."""
    path = info.get('path') or ''
    if path.startswith('http'):
        url = path
    elif path:
        url = base + '/' + path.lstrip('/')
    else:
        slug = film_slug(info.get('title') or '')
        url = f'{base}/{movies_slug}/{movie_id}' + (f'-{slug}' if slug else '')
    return url if url.endswith('/') else url + '/'


# 'The Odyssey: The IMAX Experience' is a print of The Odyssey, but the
# shared base_title only strips the format words and would leave 'The
# Odyssey: The'. Put the experience in parentheses so the shared
# cleaner takes all of it off and reads the row as a print.
_EXPERIENCE_RE = re.compile(
    r'(?:\s*[-:|]\s*|\s+)the\s+((?:imax|dolby(?:\s+cinema)?|4dx|screenx|d-?box'
    r'|rpx|xd|plf|mx4d|3d|premium large format|grand screen|epic xl'
    r'|superscreen|ultrascreen)(?:\s+(?:3d|2d|70mm|laser|with laser))*)'
    r'\s+experience\s*$', re.I)
# 'Insider Screenings' is these chains' name for an early access run of
# the film, which the shared cleaner already folds into the film.
_INSIDER_RE = re.compile(r'\binsider\s+screenings?\b', re.I)
# A chain lists a few products that sell seats but are not films.
_NOT_A_FILM_RE = re.compile(
    r'\b(?:(?:vip|private|theat(?:er|re)|auditorium|screen|party|suite|lounge'
    r'|gaming|cinema)\s+rentals?|private\s+(?:events?|screenings?|part(?:y|ies))'
    r'|birthday\s+part(?:y|ies)|gift\s+cards?)\b', re.I)


def print_title(raw: str) -> str:
    t = _EXPERIENCE_RE.sub(lambda m: f' ({m.group(1)} Experience)', clean_title(raw))
    return _INSIDER_RE.sub('Early Access', t).strip()


def not_a_film(title: str) -> bool:
    return bool(_NOT_A_FILM_RE.search(title or ''))


def _show_key(show: dict) -> Optional[tuple]:
    """What tells one showtime from an exact repeat of it. A showtime's
    own 'id' is film + start + tags, so two screens running the same
    print at the same minute share it; the screen and the ticket link
    do not."""
    urls = tuple(u for t in (_dict(show.get('data')).get('ticketing') or [])
                 if isinstance(t, dict) for u in (t.get('urls') or []))
    name = _dict(show.get('screen')).get('name')
    if not urls and not name:
        return None
    return (show.get('startsAt'), name, urls)


def count_day(payload: Any, day: str,
              excluded: Iterable[str] = ()) -> dict[str, dict[str, int]]:
    """{movie id: {theater id: showtimes on `day`}} from one schedule
    answer. Days follow the site's own grouping; a showtime carrying a
    tag the site hides everywhere is not counted."""
    out: dict[str, dict[str, int]] = {}
    hide = set(excluded or ())
    if not isinstance(payload, dict):
        return out
    for tid, block in payload.items():
        sched = block.get('schedule') if isinstance(block, dict) else None
        if not isinstance(sched, dict):
            continue
        for mid, days in sched.items():
            if not isinstance(days, dict):
                continue
            n, seen = 0, set()
            for key, shows in days.items():
                for s in shows if isinstance(shows, list) else []:
                    if not isinstance(s, dict):
                        continue
                    when = key if _DAY_RE.match(str(key)) else str(s.get('startsAt') or '')[:10]
                    if when != day or (hide and hide.intersection(s.get('tags') or ())):
                        continue
                    k = _show_key(s)
                    if k is not None:
                        if k in seen:
                            continue
                        seen.add(k)
                    n += 1
            if n:
                out.setdefault(str(mid), {})[str(tid)] = n
    return out


def schedule_url(base: str, theaters: list[dict], day: str,
                 excluded: Iterable[str] = ()) -> str:
    """One schedule call for `theaters`, midnight to midnight local."""
    nxt = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    params = [('theaters', json.dumps({'id': t['id'], 'timeZone': t['timeZone']},
                                      separators=(',', ':'))) for t in theaters]
    params += [('from', f'{day}T00:00:00'), ('to', f'{nxt}T00:00:00')]
    params += [('nin', t) for t in excluded or ()]
    return base + SCHEDULE_PATH + '?' + urlencode(params)


def film_rows(films: dict[str, dict], movies: dict[str, dict], base: str,
              movies_slug: str) -> list[dict]:
    """Rows for rank_by_showtimes, in the chain's own order."""
    rows = []
    for mid, f in films.items():
        info = movies.get(mid) or {}
        title = info.get('title') or ''
        if not title or info.get('hidden') or not_a_film(title):
            continue
        order = info.get('order')
        rows.append({
            'title': print_title(title),
            'showtimes': int(f['showtimes']),
            'url': film_url(base, mid, info, movies_slug),
            'image': info.get('poster') or '',
            'theater_ids': sorted(f['theaters']),
            'site_order': order if order is not None else 100_000 + int(info.get('pos') or 0),
        })
    rows.sort(key=lambda r: r['site_order'])
    return rows


# ---------------------------------------------------------------------------
# Reading a chain
# ---------------------------------------------------------------------------
def _find_asset_prefix(sess, base: str, code: Optional[str]) -> tuple[Optional[str], bool]:
    """(build folder, homepage walled). When the homepage cannot be read,
    the last three dated builds for the site's code are tried."""
    status, html = _get(sess, base + '/')
    prefix = asset_prefix(html if status == 200 else '', code)
    if prefix:
        return prefix, False
    walled = _walled(status, html)
    if code:
        today = datetime.now(timezone.utc).date()
        for back in range(3):
            guess = f'{ASSET_HOST}/{code}/{(today - timedelta(days=back)).isoformat()}/public'
            st, body = _get(sess, guess + '/page-data/index/page-data.json', retries=0)
            if st == 200 and isinstance(_json(body), dict):
                return guess, walled
    return None, walled


def _static_queries(sess, prefix: str) -> dict[str, Any]:
    """{'theaters' | 'movies' | 'settings': static query data}."""
    _, body = _get(sess, prefix + '/page-data/index/page-data.json')
    index = _json(body)
    hashes = [str(h) for h in ((index or {}).get('staticQueryHashes') or [])] \
        if isinstance(index, dict) else []
    fetched: dict[str, Any] = {}

    def load(h: str) -> Any:
        if h not in fetched:
            time.sleep(PAUSE_S)
            st, raw = _get(sess, f'{prefix}/page-data/sq/d/{h}.json')
            doc = _json(raw) if st == 200 else None
            fetched[h] = doc.get('data') if isinstance(doc, dict) else None
        return fetched[h]

    found: dict[str, Any] = {}
    for kind, (hint, test) in _SQ.items():
        if hashes and hint not in hashes:
            continue
        data = load(hint)
        if test(data):
            found[kind] = data
    for h in hashes:
        missing = [k for k in _SQ if k not in found]
        if not missing:
            break
        if h in fetched:
            continue
        data = load(h)
        for kind in missing:
            if _SQ[kind][1](data):
                found[kind] = data
    return found


class _Tally:
    def __init__(self, day: str, excluded: Iterable[str]):
        self.day = day
        self.excluded = list(excluded or ())
        self.films: dict[str, dict] = {}
        self.read: set[str] = set()
        self.walled = False
        self.rendered = False

    def add(self, payload: dict, batch: list[dict]) -> None:
        self.read.update(t['id'] for t in batch)
        for mid, per in count_day(payload, self.day, self.excluded).items():
            f = self.films.setdefault(mid, {'showtimes': 0, 'theaters': set()})
            for tid, n in per.items():
                f['showtimes'] += n
                f['theaters'].add(tid)


def _read_schedule(sess, base: str, theaters: list[dict], day: str,
                   excluded: list[str], *, render: Optional[Callable],
                   deadline: float) -> _Tally:
    tally = _Tally(day, excluded)
    walled: dict[str, list[dict]] = {}

    def one(batch: list[dict]):
        url = schedule_url(base, batch, day, excluded)
        if time.monotonic() > deadline:
            return batch, url, 0, None
        time.sleep(PAUSE_S)
        status, body = _get(sess, url)
        return batch, url, status, body

    def settle(results) -> list[dict]:
        failed = []
        for batch, url, status, body in results:
            data = _json(body) if status == 200 else None
            if isinstance(data, dict):
                tally.add(data, batch)
            elif _walled(status, body):
                walled[url] = batch
            else:
                failed.append(batch)
        return failed

    batches = [theaters[i:i + THEATERS_PER_CALL]
               for i in range(0, len(theaters), THEATERS_PER_CALL)]
    failed = settle(_pool_map(one, batches))
    # A batch that failed for any reason but a wall is read one theater
    # at a time, so one bad theater cannot hide twenty.
    singles = [[t] for b in failed if len(b) > 1 for t in b]
    if singles:
        settle(_pool_map(one, singles))
    if walled and render is not None and time.monotonic() < deadline:
        try:
            got = render(base + '/', list(walled), concurrency=MAX_WORKERS) or {}
        except Exception as e:
            logger.info("webedia: headless read of %s failed (%s)", base, str(e)[:120])
            got = {}
        for url, batch in list(walled.items()):
            data = _json(got.get(url))
            if isinstance(data, dict):
                tally.add(data, batch)
                tally.rendered = True
                del walled[url]
    tally.walled = bool(walled)
    return tally


def _movie_details(sess, base: str, ids: Iterable[str]) -> dict[str, dict]:
    """Title and poster for scheduled films the published list lacks."""
    ids = sorted({str(i) for i in ids})
    out: dict[str, dict] = {}
    for i in range(0, len(ids), MOVIES_PER_CALL):
        chunk = ids[i:i + MOVIES_PER_CALL]
        time.sleep(PAUSE_S)
        status, body = _get(sess, base + MOVIES_PATH + '?' + urlencode(
            [('basic', 'true')] + [('ids', x) for x in chunk]))
        data = _json(body) if status == 200 else None
        for pos, m in enumerate(data if isinstance(data, list) else []):
            if not isinstance(m, dict) or not m.get('id'):
                continue
            ex = _dict(m.get('exhibitor'))
            out[str(m['id'])] = {
                'title': clean_title(ex.get('title') or m.get('title') or ''),
                'path': '',
                'poster': str(ex.get('poster') or m.get('poster') or ''),
                'order': _int(m.get('orderIndex')),
                'pos': 50_000 + i + pos,
                'hidden': False,
            }
    return out


def read_chain(conf: dict, *, limit: int = DEFAULT_LIMIT, sess=None,
               day: Optional[str] = None,
               render: Optional[Callable] = render_then_fetch,
               budget_s: float = BUDGET_S) -> tuple[list[dict], dict]:
    """(items, meta) for one chain. `sess`, `day` and `render` are for
    tests; fetch() is the entry point and never raises."""
    t0 = time.monotonic()
    deadline = t0 + budget_s
    sess = sess or _session()
    base = conf['base'].rstrip('/')
    meta: dict[str, Any] = {'label': conf.get('label'), 'theaters': 0,
                            'theaters_listed': 0, 'date': day or today_iso(),
                            'blocked': False, 'complete': False}

    prefix, home_walled = _find_asset_prefix(sess, base, conf.get('code'))
    if not prefix:
        meta['blocked'] = home_walled
        meta['reason'] = 'bot check on the homepage' if home_walled else 'site build not found'
        logger.warning("webedia: %s: %s", base, meta['reason'])
        return [], meta
    sq = _static_queries(sess, prefix)
    theaters = chain_theaters(sq.get('theaters'))
    if not theaters:
        meta['reason'] = 'theater list not found'
        logger.warning("webedia: %s: theater list not found under %s", base, prefix)
        return [], meta
    if day is None:
        try:
            day = today_iso(main_time_zone(theaters))
        except Exception:
            day = today_iso()
    meta['date'] = day
    meta['theaters_listed'] = len(theaters)

    settings = sq.get('settings')
    excluded = excluded_tags(settings)
    movies = movie_index(sq.get('movies'))
    slug = movies_page_slug(settings, movies)

    tally = _read_schedule(sess, base, theaters, day, excluded,
                           render=render, deadline=deadline)
    missing = [mid for mid in tally.films if not (movies.get(mid) or {}).get('title')]
    if missing:
        movies.update(_movie_details(sess, base, missing))
        unnamed = [mid for mid in missing if not (movies.get(mid) or {}).get('title')]
        if unnamed:
            logger.info("webedia: %s: no title for scheduled film ids %s", base, unnamed[:10])

    rows = film_rows(tally.films, movies, base, slug)
    ranked = rank_by_showtimes(rows, limit=len(rows))
    items = ranked[:max(0, int(limit))]
    meta.update({
        'theaters': len(tally.read),
        'theaters_showing': len({tid for r in rows for tid in r['theater_ids']}),
        'complete': len(tally.read) >= len(theaters),
        'blocked': tally.walled,
        'films': len(ranked),
        'showtimes': sum(r['showtimes'] for r in ranked),
        'seconds': round(time.monotonic() - t0, 1),
    })
    if tally.rendered:
        meta['rendered'] = True
    if not meta['complete']:
        meta['reason'] = (f"read {len(tally.read)} of {len(theaters)} theaters"
                          + (' (bot check)' if tally.walled else ''))
        logger.warning("webedia: %s: %s", base, meta['reason'])
    return items, meta


def fetch(site: str, limit: int = DEFAULT_LIMIT) -> tuple[list[dict], dict]:
    """(items, meta) for one of SITES. Never raises: any failure is
    ([], meta) with a 'reason'."""
    conf = SITES.get(site)
    if conf is None:
        logger.warning("webedia: unknown site %r", site)
        return [], {'site': site, 'theaters': 0, 'date': today_iso(),
                    'blocked': False, 'reason': f'unknown site {site!r}'}
    try:
        items, meta = read_chain(conf, limit=limit)
    except Exception as e:
        logger.warning("webedia: %s read failed (%s)", site, e, exc_info=True)
        return [], {'site': site, 'label': conf['label'], 'theaters': 0,
                    'date': today_iso(), 'blocked': False,
                    'reason': f'read failed: {str(e)[:160]}'}
    meta['site'] = site
    return items, meta


# ---------------------------------------------------------------------------
# Live smoke: python3 -m scripts.trends_scrapers.theater_webedia [site ...]
# ---------------------------------------------------------------------------
def _main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.WARNING,
                        format='%(levelname)s %(name)s: %(message)s')
    keys = argv or list(SITES)
    unknown = [k for k in keys if k not in SITES]
    if unknown:
        print(f"unknown site(s): {', '.join(unknown)}. Known: {', '.join(SITES)}")
        return 2
    for key in keys:
        t0 = time.monotonic()
        items, meta = fetch(key, limit=10)
        took = time.monotonic() - t0
        head = (f"{SITES[key]['label']} ({key})  {meta.get('date')}  "
                f"theaters read {meta.get('theaters')} of {meta.get('theaters_listed')}, "
                f"{meta.get('theaters_showing')} showing today  "
                f"{meta.get('showtimes')} showtimes, {meta.get('films')} films  {took:.1f}s")
        if meta.get('blocked'):
            head += '  BLOCKED'
        if meta.get('reason'):
            head += f"  ({meta['reason']})"
        print('\n' + head)
        for it in items:
            print(f"  {it['rank']:>2}. {it['showtimes']:>5} showtimes  "
                  f"{it['theaters'] or 0:>3} theaters  {it['title']}")
            print(f"      {it['url']}")
    return 0


if __name__ == '__main__':
    sys.exit(_main(sys.argv[1:]))
