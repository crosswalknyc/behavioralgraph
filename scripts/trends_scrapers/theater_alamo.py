"""
Alamo Drafthouse card: the films with the most showtimes today across
every Alamo Drafthouse market.

Alamo's site reads its schedule from public JSON, so this is plain
HTTPS with no browser:

    markets    drafthouse.com/s/mother/v1/page/cclamp?useUnifiedSchedule=true
               (marketSummaries, the site's own market picker)
    order      drafthouse.com/s/mother/v2/schedule/national
               (presentations in the chain's own listing order, no
               sessions, so it only breaks ties)
    showtimes  drafthouse.com/s/mother/v2/schedule/market/<market>
               (the market's cinemas, presentations and every session
               with its local business date)

A session counts when its business date is today and it is neither
hidden nor cancelled. Alamo keeps a show in the feed (status PAST) for
about half an hour after it starts and then drops it, so a read late
in the day undercounts. At the 8 AM Pacific run only the earliest
East Coast shows are gone.

One film is often sold as several presentations: the plain run plus
events built on it (a sing-along, a Crafthouse kids show, an
anniversary night). Each presentation names its film in show.title,
so the events add up under the film, and the film's own page leads
the link.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from .theater_common import (DEFAULT_LIMIT, clean_title, get_json,
                             looks_walled, rank_by_showtimes, session,
                             today_iso)

logger = logging.getLogger(__name__)

KEY = 'alamo'
LABEL = 'Alamo Drafthouse'
COOKIE_DOMAIN = None

TZ = 'America/Chicago'
SITE = 'https://drafthouse.com'
MARKETS_URL = SITE + '/s/mother/v1/page/cclamp?useUnifiedSchedule=true'
NATIONAL_URL = SITE + '/s/mother/v2/schedule/national'
MARKET_URL = SITE + '/s/mother/v2/schedule/market/{slug}'

WORKERS = 4
PAUSE_S = 0.15
_HEADERS = {'Accept': 'application/json, text/plain, */*', 'Referer': SITE + '/'}

# Used only when the market list itself cannot be read.
FALLBACK_MARKETS = (
    'austin', 'boston', 'charlottesville', 'chicago', 'corpus-christi',
    'dc-metro-area', 'dfw', 'denver', 'indianapolis', 'laredo',
    'los-angeles', 'nyc', 'northern-virginia', 'omaha', 'raleigh',
    'san-antonio', 'sf', 'springfield', 'st-louis', 'twin-cities',
    'winchester', 'yonkers',
)

_CANCELLED_RE = re.compile(r'cancel|void', re.I)
_UNLISTED_ORDER = 10000


def _plain_dashes(s: str) -> str:
    return (s or '').replace('\u2013', '-').replace('\u2014', '-')


# ---------------------------------------------------------------------------
# Parsers (no network)
# ---------------------------------------------------------------------------
def parse_markets(payload: Any) -> list[str]:
    """Market slugs from the market picker, open and opening markets
    alike (a market that is not open yet simply has no sessions)."""
    data = (payload or {}).get('data') if isinstance(payload, dict) else None
    out = []
    for m in (data or {}).get('marketSummaries') or []:
        if not isinstance(m, dict) or not m.get('slug') or m.get('isHollywoodPromo'):
            continue
        if str(m.get('marketStatus') or '').upper() == 'PRE_OPEN':
            continue
        if m['slug'] not in out:
            out.append(m['slug'])
    return out


def parse_national(payload: Any) -> tuple[dict[str, int], set[str]]:
    """(presentation or show slug -> listing position, national slugs)."""
    data = (payload or {}).get('data') if isinstance(payload, dict) else None
    order: dict[str, int] = {}
    slugs = set()
    for i, p in enumerate((data or {}).get('presentations') or []):
        if not isinstance(p, dict) or not p.get('slug'):
            continue
        slugs.add(p['slug'])
        order.setdefault(p['slug'], i)
        show_slug = (p.get('show') or {}).get('slug')
        if show_slug:
            order.setdefault(show_slug, i)
    return order, slugs


def _poster(show: dict) -> str:
    for img in show.get('posterImages') or []:
        if isinstance(img, dict) and img.get('uri'):
            return img['uri']
    for key in ('portraitHeroImage', 'landscapeHeroImage'):
        img = show.get(key) or {}
        if isinstance(img, dict) and img.get('uri'):
            return img['uri']
    return ''


def parse_market(payload: Any, day: str) -> Optional[dict]:
    """One market's schedule: {'cinemas': set, 'presentations': {slug:
    info}, 'sessions': [(cinema_id, session_id, presentation_slug)]}
    with only today's visible sessions. None when the body is not a
    schedule."""
    data = (payload or {}).get('data') if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get('sessions'), list):
        return None
    cinemas = set()
    for m in data.get('market') or []:
        for c in (m or {}).get('cinemas') or []:
            if isinstance(c, dict) and c.get('id'):
                cinemas.add(str(c['id']))
    presentations = {}
    for p in data.get('presentations') or []:
        if not isinstance(p, dict) or not p.get('slug'):
            continue
        show = p.get('show') or {}
        title = clean_title(show.get('title') or '')
        if not title:
            continue
        presentations[p['slug']] = {
            'title': title,
            'image': _poster(show),
            'event': bool(p.get('event')),
            'hidden': bool(p.get('isHidden')),
        }
    sessions = []
    for s in data['sessions']:
        if not isinstance(s, dict) or s.get('businessDateClt') != day:
            continue
        if s.get('isHidden') or _CANCELLED_RE.search(str(s.get('status') or '')):
            continue
        slug = s.get('presentationSlug')
        info = presentations.get(slug)
        if not info or info['hidden']:
            continue
        sessions.append((str(s.get('cinemaId') or ''), str(s.get('sessionId') or ''), slug))
        if s.get('cinemaId'):
            cinemas.add(str(s['cinemaId']))
    return {'cinemas': cinemas, 'presentations': presentations, 'sessions': sessions}


def film_url(slug: str, event: bool, market: Optional[str] = None) -> str:
    kind = 'event' if event else 'show'
    return f"{SITE}/{market}/{kind}/{slug}" if market else f"{SITE}/{kind}/{slug}"


def build_rows(markets: dict[str, dict], order: dict[str, int],
               national: set[str]) -> list[dict]:
    """One row per presentation, today's sessions counted once each."""
    seen = set()
    shows: dict[str, int] = defaultdict(int)
    cinemas: dict[str, set] = defaultdict(set)
    by_market: dict[str, Counter] = defaultdict(Counter)
    info: dict[str, dict] = {}
    for market, sched in markets.items():
        for slug, p in sched['presentations'].items():
            info.setdefault(slug, p)
        for cinema_id, session_id, slug in sched['sessions']:
            key = (cinema_id, session_id)
            if session_id and key in seen:
                continue
            seen.add(key)
            shows[slug] += 1
            cinemas[slug].add(cinema_id)
            by_market[slug][market] += 1
    live_titles = {info[s]['title'] for s in shows if s in info}
    rows = []
    for slug, p in info.items():
        if p['hidden'] or p['title'] not in live_titles:
            continue
        market = None if slug in national else (
            by_market[slug].most_common(1)[0][0] if by_market[slug] else None)
        if market is None and slug not in national:
            market = next((m for m, s in markets.items() if slug in s['presentations']), None)
        rows.append({
            'title': _plain_dashes(p['title']),
            'showtimes': shows.get(slug, 0),
            'url': film_url(slug, p['event'], market),
            'image': p['image'],
            'theater_ids': cinemas.get(slug, set()),
            'site_order': order.get(slug, _UNLISTED_ORDER),
        })
    # The film's own run first, so it lends its page and poster to the
    # events built on it.
    rows.sort(key=lambda r: ('/event/' in r['url'], -r['showtimes']))
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


def _read_market(slug: str, day: str) -> tuple[str, Optional[dict]]:
    payload = get_json(_thread_session(), MARKET_URL.format(slug=slug))
    time.sleep(PAUSE_S)
    return slug, parse_market(payload, day)


def _walled(sess, url: str) -> bool:
    try:
        r = sess.get(url, timeout=20)
        return r.status_code in (403, 429) or looks_walled(r.text)
    except Exception:
        return False


def _fetch(limit: int, day: str, meta: dict) -> tuple[list[dict], dict]:
    sess = session(_HEADERS)
    slugs = parse_markets(get_json(sess, MARKETS_URL))
    if not slugs:
        logger.info("alamo: market list unavailable, using the stored market list")
        slugs = list(FALLBACK_MARKETS)
    order, national = parse_national(get_json(sess, NATIONAL_URL))

    markets: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for slug, sched in ex.map(lambda s: _read_market(s, day), slugs):
            if sched is not None:
                markets[slug] = sched
    theaters = set().union(*(m['cinemas'] for m in markets.values())) if markets else set()
    meta['theaters'] = len(theaters)
    meta['markets'] = len(markets)
    meta['markets_listed'] = len(slugs)
    if not markets:
        meta['blocked'] = _walled(sess, MARKET_URL.format(slug=slugs[0]))
        meta['reason'] = 'no market schedule could be read'
        return [], meta
    if len(markets) < len(slugs):
        meta['reason'] = (f'{len(slugs) - len(markets)} of {len(slugs)} '
                          'market schedules could not be read')
    return rank_by_showtimes(build_rows(markets, order, national), limit=limit), meta


def fetch(limit: int = DEFAULT_LIMIT) -> tuple[list[dict], dict]:
    """(items, meta). Never raises."""
    meta: dict = {'theaters': 0, 'date': '', 'blocked': False}
    try:
        meta['date'] = today_iso(TZ)
        return _fetch(limit, meta['date'], meta)
    except Exception as e:
        logger.warning("alamo: read failed (%s)", str(e)[:200])
        meta['reason'] = f'read failed: {str(e)[:160]}'
        return [], meta


def _main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    t0 = time.time()
    items, meta = fetch(limit=10)
    print(f"{LABEL}: {meta.get('theaters')} theaters in {meta.get('markets')} of "
          f"{meta.get('markets_listed')} markets, {meta.get('date')}, "
          f"{time.time() - t0:.1f}s, blocked={meta.get('blocked')}"
          + (f", note: {meta['reason']}" if meta.get('reason') else ''))
    for it in items:
        print(f"{it['rank']:>2}. {it['title']}  {it['showtimes']} showtimes, "
              f"{it['theaters']} theaters  {it['url']}")


if __name__ == '__main__':
    _main()
