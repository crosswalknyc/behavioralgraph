"""
Emagine Theatres card: the films with the most showtimes today across
every Emagine theater (Michigan, Minnesota, Illinois, Wisconsin,
Indiana, Colorado).

The Emagine site is WordPress with its own REST routes, read with
plain HTTPS and no browser:

    theaters   www.emagine-entertainment.com/wp-json/emagine/v1/theatres
    showtimes  www.emagine-entertainment.com/wp-json/emagine/v1/theatres/<id>/sessions
               (the theater's films plus every session by business
               date, grouped by print)

Emagine's feed lists only shows that have not started yet, so a read
late in the day undercounts. At the 8 AM Pacific run (11 AM Eastern)
only the earliest matinees are already gone.

The chain's own film list is A to Z, so ties fall to title order. Two
listings are not films a moviegoer can name and are left out: the
Monday mystery screening sold as 'Error_MovieTitle_Unknown' and the
'Secret Movie Night' series.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from .theater_common import (DEFAULT_LIMIT, clean_title, get_json,
                             looks_walled, rank_by_showtimes, session,
                             today_iso)

logger = logging.getLogger(__name__)

KEY = 'emagine'
LABEL = 'Emagine Theatres'
COOKIE_DOMAIN = None

TZ = 'America/Detroit'
SITE = 'https://www.emagine-entertainment.com'
API = SITE + '/wp-json/emagine/v1'
THEATERS_URL = API + '/theatres'
SESSIONS_URL = API + '/theatres/{theater_id}/sessions'
LISTING_URL = SITE + '/movies/'

WORKERS = 4
PAUSE_S = 0.2
_HEADERS = {'Accept': 'application/json, text/plain, */*', 'Referer': LISTING_URL}

_HIDDEN_TITLE_RE = re.compile(
    r'^\s*(?:error[ _]?movie[ _]?title[ _]?unknown|secret movie night|mystery movie)\b', re.I)
_ACCESS_TAG_RE = re.compile(r'\s*\(\s*dementia[ -]friendly\s*\)', re.I)


def _plain_dashes(s: str) -> str:
    return (s or '').replace('\u2013', '-').replace('\u2014', '-')


def display_title(raw: str) -> str:
    """The film title as listed, with the accessible-screening tag
    the shared title merge does not know taken off."""
    return _plain_dashes(_ACCESS_TAG_RE.sub('', clean_title(raw))).strip()


# ---------------------------------------------------------------------------
# Parsers (no network)
# ---------------------------------------------------------------------------
def parse_theaters(payload: Any) -> list[dict]:
    data = (payload or {}).get('data') if isinstance(payload, dict) else None
    out, seen = [], set()
    for t in data if isinstance(data, list) else []:
        if not isinstance(t, dict) or not t.get('id') or str(t['id']) in seen:
            continue
        seen.add(str(t['id']))
        out.append({'id': str(t['id']), 'name': clean_title(t.get('title') or ''),
                    'url': t.get('permalink') or ''})
    return out


def parse_sessions(payload: Any, theater_id: str, day: str) -> Optional[tuple[dict, dict]]:
    """({movie_id: showtimes today}, {movie_id: film}) for one theater,
    or None when the body is not a schedule."""
    data = (payload or {}).get('data') if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get('sessions_by_theatre'), dict):
        return None
    films = {}
    for mid, m in (data.get('movies') or {}).items():
        if isinstance(m, dict) and m.get('title'):
            films[str(m.get('id') or mid)] = {
                'title': clean_title(m['title']),
                'url': m.get('permalink') or '',
                'image': m.get('poster') or '',
            }
    theater = data['sessions_by_theatre'].get(str(theater_id))
    if theater is None and len(data['sessions_by_theatre']) == 1:
        theater = next(iter(data['sessions_by_theatre'].values()))
    entries = ((theater or {}).get('business_dates') or {}).get(day) or {}
    counts: dict[str, int] = defaultdict(int)
    seen = set()
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        mid = str(entry.get('movie_id') or key)
        for group in entry.get('groups') or []:
            for s in (group or {}).get('sessions') or []:
                if not isinstance(s, dict):
                    continue
                if s.get('business_date') and s['business_date'] != day:
                    continue
                sid = s.get('vista_session_id')
                if sid:
                    if sid in seen:
                        continue
                    seen.add(sid)
                counts[mid] += 1
    return dict(counts), films


def build_rows(per_theater: dict[str, tuple[dict, dict]]) -> list[dict]:
    shows: dict[str, int] = defaultdict(int)
    theaters: dict[str, set] = defaultdict(set)
    films: dict[str, dict] = {}
    for tid, (counts, theater_films) in per_theater.items():
        for mid, f in theater_films.items():
            films.setdefault(mid, f)
        for mid, n in counts.items():
            shows[mid] += n
            theaters[mid].add(tid)
    named = {mid: f for mid, f in films.items() if not _HIDDEN_TITLE_RE.search(f['title'])}
    titles = sorted({display_title(f['title']).lower() for f in named.values()})
    alpha = {t: i for i, t in enumerate(titles)}
    rows = []
    for mid, n in shows.items():
        f = named.get(mid)
        if not f:
            continue
        title = display_title(f['title'])
        rows.append({
            'title': title,
            'showtimes': n,
            'url': f['url'] or LISTING_URL,
            'image': f['image'],
            'theater_ids': theaters[mid],
            'site_order': alpha.get(title.lower(), len(alpha)),
        })
    rows.sort(key=lambda r: -r['showtimes'])
    return rows


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------
_local = threading.local()


def _thread_session():
    s = getattr(_local, 'sess', None)
    if s is None:
        s = _local.sess = session(_HEADERS)
    return s


def _read_theater(theater: dict, day: str) -> tuple[str, Optional[tuple[dict, dict]]]:
    payload = get_json(_thread_session(), SESSIONS_URL.format(theater_id=theater['id']))
    time.sleep(PAUSE_S)
    return theater['id'], parse_sessions(payload, theater['id'], day)


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
    per_theater: dict[str, tuple[dict, dict]] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for tid, parsed in ex.map(lambda t: _read_theater(t, day), theaters):
            if parsed is not None:
                per_theater[tid] = parsed
    meta['theaters'] = len(per_theater)
    meta['theaters_listed'] = len(theaters)
    if not per_theater:
        meta['blocked'] = _walled(sess, SESSIONS_URL.format(theater_id=theaters[0]['id']))
        meta['reason'] = 'no theater schedule could be read'
        return [], meta
    if len(per_theater) < len(theaters):
        meta['reason'] = (f'{len(theaters) - len(per_theater)} of {len(theaters)} '
                          'theater schedules could not be read')
    return rank_by_showtimes(build_rows(per_theater), limit=limit), meta


def fetch(limit: int = DEFAULT_LIMIT) -> tuple[list[dict], dict]:
    """(items, meta). Never raises."""
    meta: dict = {'theaters': 0, 'date': '', 'blocked': False}
    try:
        meta['date'] = today_iso(TZ)
        return _fetch(limit, meta['date'], meta)
    except Exception as e:
        logger.warning("emagine: read failed (%s)", str(e)[:200])
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
