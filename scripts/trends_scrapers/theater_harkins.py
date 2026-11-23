"""
Harkins Theatres card: the films with the most showtimes today across
every Harkins theater (Arizona, California, Colorado, Oklahoma).

Harkins publishes its whole schedule as public JSON, so this is plain
HTTPS with no browser:

    theaters   cmsservice.harkins.com/api/v1/theaters
               (regions, each with its 'theatres')
    films      cmsservice.harkins.com/api/v1/movies
               (id, title, slug, poster, runtime, credits, print ids)
    order      www.harkins.com/movies, __NEXT_DATA__ nowShowing
               (the chain's own listing: a few featured titles, then
               A to Z, so it only breaks ties)
    showtimes  webservice.harkins.com/v2/theatres/<id>/performances/<day>

A theater's performances for a day come back whole, shows that already
started included, so the count is the same at any hour.

Harkins sells some dubbed and captioned prints under a different title
('Juegos del hambre: Amanecer en la cosecha[Espanol]' and 'On-Screen
Captions: Hunger Games: Sunrise Reaping' are The Hunger Games). Such a
print is folded into its film when the catalog agrees on runtime,
release week and a credit (or, when one record lists no credits, the
exact runtime and release day) and the matches name one film; a film's
own event screenings ('Hexed: Spellbound Screening') resolve to the
film. A film shown in its own language with subtitles is never folded.
Premium and format prints listed in a film's childMoviesIds fold the
same way.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Optional

from .theater_common import (DEFAULT_LIMIT, base_title, clean_title, get_json,
                             get_text, looks_walled, rank_by_showtimes,
                             session, title_key, today_iso)

logger = logging.getLogger(__name__)

KEY = 'harkins'
LABEL = 'Harkins Theatres'
COOKIE_DOMAIN = None

TZ = 'America/Phoenix'
SITE = 'https://www.harkins.com'
THEATERS_URL = 'https://cmsservice.harkins.com/api/v1/theaters'
MOVIES_URL = 'https://cmsservice.harkins.com/api/v1/movies'
LISTING_URL = SITE + '/movies'
PERFORMANCES_URL = ('https://webservice.harkins.com/v2/theatres/'
                    '{theater_id}/performances/{day}')

WORKERS = 4
PAUSE_S = 0.15
_HEADERS = {'Origin': SITE, 'Referer': SITE + '/'}

_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_TAG_RE = re.compile(r'\[[^\]]*\]')
_COPY_TAG_RE = re.compile(
    r'doblad|dubbed|\bdub\b|espa(?:n|\u00f1)ol|spanish'
    r'|^\s*(?:on-?screen|open)[ -]?captions?\b', re.I)
_UNLISTED_ORDER = 1000


def _plain_dashes(s: str) -> str:
    return (s or '').replace('\u2013', '-').replace('\u2014', '-')


# ---------------------------------------------------------------------------
# Parsers (no network)
# ---------------------------------------------------------------------------
def parse_theaters(data: Any) -> list[dict]:
    """[{'id', 'slug', 'name'}], each theater once, from the regions list."""
    out, seen = [], set()
    for region in data if isinstance(data, list) else []:
        for t in (region or {}).get('theatres') or []:
            if not isinstance(t, dict) or t.get('id') in (None, ''):
                continue
            tid = str(t['id'])
            if tid in seen:
                continue
            seen.add(tid)
            out.append({'id': tid, 'slug': t.get('slugUrl') or '',
                        'name': clean_title(t.get('name') or '')})
    return out


def parse_listing(html: str) -> list[dict]:
    """Film entries from the /movies page, nowShowing first, in the
    page's own order."""
    m = _NEXT_RE.search(html or '')
    if not m:
        return []
    try:
        sort = json.loads(m.group(1))['props']['pageProps']['movieData']['movieSort']
    except Exception:
        return []
    out, seen = [], set()
    for key in ('nowShowing', 'comingThisWeek'):
        for f in sort.get(key) or []:
            if isinstance(f, dict) and f.get('id') and f['id'] not in seen:
                seen.add(f['id'])
                out.append(f)
    return out


def _names(entries: Any) -> set[str]:
    out = set()
    for e in entries or []:
        name = e.get('item') if isinstance(e, dict) else e
        if name:
            out.add(clean_title(str(name)).lower())
    return out


def _poster(f: dict) -> str:
    if f.get('poster'):
        return f['poster']
    for img in f.get('imgUrls') or []:
        if isinstance(img, dict) and img.get('imageType') == 'FilmPosterGraphic' and img.get('url'):
            return img['url']
    return ''


def _runtime(f: dict) -> int:
    rt = f.get('runtime')
    if isinstance(rt, (int, float)):
        return int(rt)
    m = re.match(r'\s*(?:(\d+)h)?\s*(?:(\d+)m)?', str(f.get('duration') or ''))
    if m and (m.group(1) or m.group(2)):
        return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)
    return 0


def _release(f: dict) -> Optional[date]:
    try:
        return date.fromisoformat(str(f.get('releaseDate') or '')[:10])
    except ValueError:
        return None


def parse_films(api_movies: Any, listing: Optional[list[dict]] = None) -> dict[str, dict]:
    """movieId -> {'title', 'slug', 'image', 'runtime', 'released',
    'credits', 'children', 'api_order'}. The catalog API is the main
    source; the listing page fills in films the API does not carry."""
    films: dict[str, dict] = {}
    for i, f in enumerate(api_movies if isinstance(api_movies, list) else []):
        if not isinstance(f, dict) or not f.get('movieId') or not f.get('title'):
            continue
        films[str(f['movieId'])] = {
            'title': clean_title(f['title']),
            'slug': f.get('slugUrl') or '',
            'image': _poster(f),
            'runtime': _runtime(f),
            'released': _release(f),
            'credits': _names(f.get('directors')) | _names(f.get('castMembers')),
            'children': [str(c) for c in f.get('childMoviesIds') or [] if c],
            'api_order': i,
        }
    for f in listing or []:
        fid = str(f.get('id') or '')
        if not fid or not f.get('title'):
            continue
        g = films.get(fid)
        if g is None:
            films[fid] = {
                'title': clean_title(f['title']),
                'slug': f.get('slugUrl') or '',
                'image': _poster(f),
                'runtime': _runtime(f),
                'released': _release(f),
                'credits': _names(f.get('directedBy')) | _names(f.get('starrings')),
                'children': [],
                'api_order': _UNLISTED_ORDER,
            }
        else:
            g['image'] = g['image'] or _poster(f)
            g['slug'] = g['slug'] or (f.get('slugUrl') or '')
    return films


def _same_film(a: dict, b: dict) -> bool:
    """Same runtime and release week with a shared credit, or, when one
    record lists no credits, the exact same runtime and release day."""
    if not a['runtime'] or not b['runtime'] or abs(a['runtime'] - b['runtime']) > 3:
        return False
    if not a['released'] or not b['released'] or abs((a['released'] - b['released']).days) > 10:
        return False
    if a['credits'] and b['credits']:
        return bool(a['credits'] & b['credits'])
    return a['runtime'] == b['runtime'] and a['released'] == b['released']


def _pick_parent(matches: list[tuple[str, dict]]) -> Optional[str]:
    """The one film among the matches, or None when they disagree. A
    film and its own event screenings ('Hexed', 'Hexed: Spellbound
    Screening') resolve to the film, the title the others extend."""
    keys = {title_key(p['title']) for _, p in matches}
    if not keys:
        return None
    if len(keys) > 1:
        shortest = min(keys, key=len)
        if not all(k.startswith(shortest) for k in keys):
            return None
        matches = [m for m in matches if title_key(m[1]['title']) == shortest]
    return min(matches, key=lambda m: m[1].get('api_order', 0))[0]


def _is_print(title: str) -> bool:
    """A language, caption or format print rather than the plain film:
    a bracket tag ('[Espanol]') or a tag the shared title merge strips
    ('On-Screen Captions: ...')."""
    return bool(_TAG_RE.search(title)) or clean_title(title) != base_title(title)


def _is_copy_of_another(title: str) -> bool:
    """A dubbed or captioned copy of a film listed elsewhere. A film
    shown in its own language with subtitles ('Bhogi [Telugu,
    subtitled]') is the film itself and is never folded."""
    return bool(_COPY_TAG_RE.search(title))


def film_parents(films: dict[str, dict]) -> dict[str, str]:
    """Print id -> id of the film it is a print of."""
    parents: dict[str, str] = {}
    for fid, f in films.items():
        for child in f.get('children') or ():
            if child != fid:
                parents.setdefault(child, fid)
    plain = [(fid, f) for fid, f in films.items() if not _is_print(f['title'])]
    plain_keys = {title_key(f['title']) for _, f in plain}
    for fid, f in films.items():
        if fid in parents or not _is_copy_of_another(f['title']):
            continue
        if title_key(f['title']) in plain_keys:
            continue  # the shared title merge already folds this print
        parent = _pick_parent([(pid, p) for pid, p in plain
                               if pid != fid and _same_film(f, p)])
        if parent:
            parents[fid] = parent
    return parents


def listing_order(listing: list[dict], films: dict[str, dict]) -> dict[str, int]:
    """Film id -> the chain's own listing position, used for ties."""
    order = {str(f.get('id')): i for i, f in enumerate(listing or []) if f.get('id')}
    for fid, f in films.items():
        order.setdefault(fid, _UNLISTED_ORDER + int(f.get('api_order', 0)))
    return order


def count_performances(payload: Any, day: str) -> dict[str, int]:
    """movieId -> showtimes on `day` from one theater's performances."""
    perfs = ((payload or {}).get('data') or {}).get('performances') if isinstance(payload, dict) else None
    out: dict[str, int] = defaultdict(int)
    seen = set()
    for p in perfs or []:
        if not isinstance(p, dict) or not p.get('movieId'):
            continue
        when = str(p.get('businessDate') or p.get('showtimeDate') or '')
        if not when.startswith(day):
            continue
        sid = p.get('sessionId')
        if sid:
            if sid in seen:
                continue
            seen.add(sid)
        out[str(p['movieId'])] += 1
    return dict(out)


def build_rows(films: dict[str, dict], per_theater: dict[str, dict[str, int]],
               order: dict[str, int], parents: dict[str, str]) -> tuple[list[dict], int]:
    """Rows for rank_by_showtimes, and the showtimes whose film the
    catalog does not name (left out)."""
    shows: dict[str, int] = defaultdict(int)
    theaters: dict[str, set] = defaultdict(set)
    unnamed = 0
    for tid, counts in per_theater.items():
        for mid, n in counts.items():
            fid = parents.get(mid, mid)
            if fid not in films:
                unnamed += n
                continue
            shows[fid] += n
            theaters[fid].add(tid)
    rows = []
    for fid, n in shows.items():
        f = films[fid]
        rows.append({
            'title': _plain_dashes(f['title']),
            'showtimes': n,
            'url': f"{SITE}/movies/{f['slug']}" if f.get('slug') else LISTING_URL,
            'image': f.get('image') or '',
            'theater_ids': theaters[fid],
            'site_order': order.get(fid, _UNLISTED_ORDER),
        })
    rows.sort(key=lambda r: -r['showtimes'])
    return rows, unnamed


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
_local = threading.local()


def _thread_session():
    s = getattr(_local, 'sess', None)
    if s is None:
        s = _local.sess = session(_HEADERS)
    return s


def _read_theater(theater: dict, day: str) -> tuple[str, Optional[dict]]:
    url = PERFORMANCES_URL.format(theater_id=theater['id'], day=day)
    payload = get_json(_thread_session(), url)
    time.sleep(PAUSE_S)
    if not isinstance(payload, dict) or payload.get('success') is False or 'data' not in payload:
        return theater['id'], None
    return theater['id'], payload


def _walled(sess, url: str) -> bool:
    try:
        r = sess.get(url, timeout=20)
        return r.status_code in (403, 429) or looks_walled(r.text)
    except Exception:
        return False


def _fetch(limit: int, day: str, meta: dict) -> tuple[list[dict], dict]:
    sess = session(_HEADERS)
    theaters = parse_theaters(get_json(sess, THEATERS_URL))
    if not theaters:
        meta['blocked'] = _walled(sess, THEATERS_URL)
        meta['reason'] = 'the theater list could not be read'
        return [], meta
    listing = parse_listing(get_text(sess, LISTING_URL) or '')
    films = parse_films(get_json(sess, MOVIES_URL), listing)
    if not films:
        meta['reason'] = 'the film list could not be read'
        return [], meta

    per_theater: dict[str, dict[str, int]] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for tid, payload in ex.map(lambda t: _read_theater(t, day), theaters):
            if payload is not None:
                per_theater[tid] = count_performances(payload, day)
    meta['theaters'] = len(per_theater)
    meta['theaters_listed'] = len(theaters)
    if not per_theater:
        meta['blocked'] = _walled(sess, PERFORMANCES_URL.format(
            theater_id=theaters[0]['id'], day=day))
        meta['reason'] = 'no theater schedule could be read'
        return [], meta
    if len(per_theater) < len(theaters):
        meta['reason'] = (f'{len(theaters) - len(per_theater)} of {len(theaters)} '
                          'theater schedules could not be read')

    rows, unnamed = build_rows(films, per_theater, listing_order(listing, films),
                               film_parents(films))
    if unnamed:
        logger.info("harkins: %d showtimes today belong to films the catalog does not name", unnamed)
        meta['unnamed_showtimes'] = unnamed
    return rank_by_showtimes(rows, limit=limit), meta


def fetch(limit: int = DEFAULT_LIMIT) -> tuple[list[dict], dict]:
    """(items, meta). Never raises."""
    meta: dict = {'theaters': 0, 'date': '', 'blocked': False}
    try:
        meta['date'] = today_iso(TZ)
        return _fetch(limit, meta['date'], meta)
    except Exception as e:
        logger.warning("harkins: read failed (%s)", str(e)[:200])
        meta['reason'] = f'read failed: {str(e)[:160]}'
        return [], meta


def _main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    t0 = time.time()
    items, meta = fetch(limit=10)
    print(f"{LABEL}: {meta.get('theaters')} of {meta.get('theaters_listed', '?')} theaters, "
          f"{meta.get('date')}, {time.time() - t0:.1f}s, blocked={meta.get('blocked')}"
          + (f", note: {meta['reason']}" if meta.get('reason') else ''))
    for it in items:
        print(f"{it['rank']:>2}. {it['title']}  {it['showtimes']} showtimes, "
              f"{it['theaters']} theaters  {it['url']}")


if __name__ == '__main__':
    _main()
