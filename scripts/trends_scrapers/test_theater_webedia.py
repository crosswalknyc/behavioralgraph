#!/usr/bin/env python3
"""Offline checks for theater_webedia: finding the build, the theater
and film lists, counting today's showtimes, and the ranking.

The five Webedia Movies Pro chains (B&B, Showcase US, Landmark, Marquee,
EPIC) answer the same shapes. The fixtures below are small copies of
what the sites answered on 2026-11-23, with the cases that matter:

  * two screens running one print at the same minute share a showtime
    'id' and are two showtimes, while an exact repeat is one;
  * a print listed as its own film ('The Odyssey: The IMAX Experience',
    'Wicked: For Good (Open Caption)') adds to the plain film;
  * a presale (showtimes tomorrow only), a hidden film and a seat
    rental never rank;
  * a tie goes to the chain's own order, never to the alphabet first.

Hermetic: no network, no browser, no cloud.

    PYTHONPATH=. python3 scripts/trends_scrapers/test_theater_webedia.py
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from scripts.trends_scrapers import theater_webedia as tw  # noqa: E402
from scripts.trends_scrapers.theater_common import base_title, clean_title  # noqa: E402

tw.PAUSE_S = 0
tw.RETRY_PAUSE_S = 0

FAILURES: list = []


def check(ok: bool, what: str) -> None:
    print(f'{"PASS" if ok else "FAIL"}  {what}')
    if not ok:
        FAILURES.append(what)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
CONF = dict(tw.SITES['bbtheatres'])
BASE = CONF['base']
PREFIX = 'https://cms-assets.webediamovies.pro/prod/bandb/2026-11-23/public'
DAY, NEXT = '2026-11-23', '2026-11-24'

HOME_HTML = (
    '<html><head>'
    f'<link rel="preload" href="{PREFIX}/styles.b512ee.css">'
    f'<script src="{PREFIX}/webpack-runtime-4c4a87.js"></script>'
    f'<script src="{PREFIX}/framework-6dba70.js"></script>'
    f'<script src="{PREFIX}/app-5b8e94.js"></script>'
    '<img src="https://cms-assets.webediamovies.pro/prod/scus/2026-11-22/public/x.png">'
    '</head><body>B&amp;B Theatres</body></html>')

INDEX = {'componentChunkName': 'component---page-index', 'path': '/',
         'result': {'data': {}},
         'staticQueryHashes': ['1575531621', '1797587525', '2506275789',
                               '3836549025']}

SQ_SETTINGS = {'data': {
    'moviesPage': {'slug': 'showtimes'},
    'settings': {'globalExcludedTags': ['Showtime.Restriction.PrivateScreening']},
    # The real settings query also lists theaters, without time zones.
    'allTheater': {'nodes': [{'id': 'T1', 'ticketingProvider': 'default'}]},
}}

SQ_THEATERS = {'data': {'allTheater': {'nodes': [
    {'id': 'T1', 'name': 'Shawnee 18', 'timeZone': 'America/Chicago',
     'country': {'name': 'USA', 'iso31661A2': 'US'}, 'practicalInfo': {'closed': False}},
    {'id': 'T2', 'name': 'Ankeny 12', 'timeZone': 'America/Chicago',
     'country': {'iso31661A2': 'US'}, 'practicalInfo': {'closed': False}},
    {'id': 'T3', 'name': 'Wesley Chapel 16', 'timeZone': 'America/New_York',
     'country': {'iso31661A2': 'US'}, 'practicalInfo': {}},
    {'id': 'T4', 'name': 'No Zone 8', 'timeZone': None, 'country': {'iso31661A2': 'US'}},
    {'id': 'T5', 'name': 'Staff Only 4', 'timeZone': 'America/Chicago'},
    {'id': 'TC', 'name': 'Closed 6', 'timeZone': 'America/Chicago',
     'country': {'iso31661A2': 'US'}, 'practicalInfo': {'closed': True}},
    {'id': 'TX', 'name': 'Toronto 10', 'timeZone': 'America/Toronto',
     'country': {'iso31661A2': 'CA'}},
    {'id': 'T1', 'name': 'Shawnee 18 (listed twice)', 'timeZone': 'America/Chicago'},
]}}}


def _movie(mid, title, order=None, poster=True, **kw):
    node = {'id': mid, 'title': title, 'orderIndex': order, 'shouldBeHidden': False,
            'path': f'/showtimes/{mid}-{tw.film_slug(title)}',
            'poster': f'https://img.example/{mid}.jpg' if poster else None,
            'overrides': {'title': None, 'synopsis': None, 'poster': None}}
    node.update(kw)
    return node


# Listed alphabetically, the way the sites publish the list.
SQ_MOVIES = {'data': {'allMovie': {'nodes': [
    _movie('M10', 'A Christmas Story',
           overrides={'title': 'A Christmas Story (1983)', 'poster': 'https://img.example/override.jpg'}),
    _movie('M8', 'Avatar: Fire and Ash', order=3),
    _movie('M6', 'Epic Theatres VIP Rental', order=0, poster=False),
    _movie('M7', 'Staff Screening', shouldBeHidden=True),
    _movie('M4', 'The Odyssey'),
    _movie('M3', 'The Odyssey: The IMAX Experience'),
    _movie('M1', 'Wicked: For Good', order=2),
    _movie('M2', 'Wicked: For Good (Open Caption)'),
    _movie('M5', 'Zootopia 2', order=1),
]}, 'allMovieUnion': {'nodes': []}}}

MOVIES_API = [{'id': 'M9', 'title': 'B&B Mystery Movie', 'poster': 'https://img.example/M9.jpg',
               'exhibitor': {'title': None, 'poster': None}, 'orderIndex': None}]


def show(mid, tid, day, hhmm, screen, *, tags=('Format.Projection.Digital',), url=None):
    tags = list(tags)
    return {'id': f'{mid}-{day} {hhmm}:00-{",".join(tags)}',
            'startsAt': f'{day}T{hhmm}:00', 'tags': tags, 'isExpired': False,
            'data': {'ticketing': [{'urls': [url or f'https://tix.example/{tid}/{mid}/{day}/{hhmm}/{screen}'],
                                    'type': 'DESKTOP', 'provider': 'default'}]},
            'occupancy': {'rate': None}, 'screen': {'name': f'Screen {screen}'},
            'nextShowtimes': []}


def schedule(**films):
    return {'schedule': {mid: days for mid, days in films.items()},
            'moviesTags': {}, 'showtimesDates': [DAY]}


_T1_M5 = show('M5', 'T1', DAY, '13:00', 1)
SCHEDULE = {
    'T1': schedule(
        # 10:00 on two screens: one id, two showtimes. 13:00 sent twice.
        M5={DAY: [show('M5', 'T1', DAY, '10:00', 1), show('M5', 'T1', DAY, '10:00', 2),
                  _T1_M5, dict(_T1_M5)]},
        M1={DAY: [show('M1', 'T1', DAY, '12:00', 3), show('M1', 'T1', DAY, '18:00', 3)]},
        M3={DAY: [show('M3', 'T1', DAY, h, 9, tags=('Auditorium.Experience.IMAX',))
                  for h in ('11:00', '14:30', '18:00', '21:30')]},
        M8={NEXT: [show('M8', 'T1', NEXT, h, 9) for h in ('19:00', '19:30', '20:00',
                                                          '22:00', '22:30', '23:00')]},
    ),
    'T2': schedule(
        M5={DAY: [show('M5', 'T2', DAY, '11:00', 1), show('M5', 'T2', DAY, '15:00', 1),
                  show('M5', 'T2', DAY, '09:00', 4,
                       tags=('Showtime.Restriction.PrivateScreening',))]},
        M2={DAY: [show('M2', 'T2', DAY, '16:00', 2, tags=('Showtime.Accessibility.OpenCaption',)),
                  show('M2', 'T2', DAY, '19:00', 2, tags=('Showtime.Accessibility.OpenCaption',))]},
        M4={DAY: [show('M4', 'T2', DAY, h, 5) for h in ('12:00', '15:30', '19:00')]},
        M9={DAY: [show('M9', 'T2', DAY, '19:00', 6), show('M9', 'T2', DAY, '21:00', 6)]},
    ),
    'T3': schedule(
        M5={DAY: [show('M5', 'T3', DAY, '17:00', 1)], NEXT: [show('M5', 'T3', NEXT, '17:00', 1)]},
        M1={DAY: [show('M1', 'T3', DAY, '18:00', 2), show('M1', 'T3', DAY, '21:00', 2)]},
        M6={DAY: [show('M6', 'T3', DAY, h, 'VIP', tags=('Showtime.Service.VIP',))
                  for h in ('11:20', '15:10', '22:50')]},
        M10={DAY: [show('M10', 'T3', DAY, '19:30', 7)]},
    ),
    'T4': schedule(M4={DAY: [show('M4', 'T4', DAY, '20:00', 1)]}),
    'T5': schedule(M7={DAY: [show('M7', 'T5', DAY, h, 1) for h in ('09:00', '10:00', '11:00',
                                                                   '12:00', '13:00')]}),
}


# ---------------------------------------------------------------------------
# A fake site: answers every URL the reader asks for from the fixtures
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, status: int, text: str):
        self.status_code = status
        self.text = text


WALL_HTML = '<html><head><title>Just a moment...</title></head><body>Verify you are human</body></html>'


class FakeSite:
    def __init__(self, *, home=HOME_HTML, index=INDEX, sq=None, fail_multi_with=(),
                 wall_for=(), raise_all=False, builds=('2026-11-23',)):
        self.home = home
        self.index = index
        self.sq = sq if sq is not None else {'1797587525': SQ_SETTINGS,
                                             '2506275789': SQ_THEATERS,
                                             '3836549025': SQ_MOVIES,
                                             '1575531621': {'data': {'allAttribute': {'nodes': []}}}}
        self.fail_multi_with = set(fail_multi_with)
        self.wall_for = set(wall_for)
        self.raise_all = raise_all
        self.builds = set(builds)
        self.calls: list[str] = []
        self.schedule_calls: list[dict] = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(url)
        if self.raise_all:
            raise RuntimeError('connection reset')
        parts = urlsplit(url)
        q = parse_qs(parts.query)
        if url == BASE + '/':
            return _Resp(200, self.home) if self.home is not None else _Resp(403, WALL_HTML)
        m = re.match(r'^https://cms-assets\.webediamovies\.pro/prod/bandb/([^/]+)/public(/.*)$', url)
        if m:
            build, rest = m.groups()
            if build not in self.builds:
                return _Resp(404, 'not found')
            if rest == '/page-data/index/page-data.json':
                return _Resp(200, json.dumps(self.index))
            sq = re.match(r'^/page-data/sq/d/(\w+)\.json$', rest)
            if sq and sq.group(1) in self.sq:
                return _Resp(200, json.dumps(self.sq[sq.group(1)]))
            return _Resp(404, 'not found')
        if parts.path == tw.SCHEDULE_PATH:
            ths = [json.loads(v) for v in q.get('theaters', [])]
            ids = [t['id'] for t in ths]
            self.schedule_calls.append({'ids': ids, 'from': q.get('from'), 'to': q.get('to'),
                                        'nin': q.get('nin', []), 'zones': [t['timeZone'] for t in ths]})
            if self.wall_for.intersection(ids):
                return _Resp(403, WALL_HTML)
            if len(ids) > 1 and self.fail_multi_with.intersection(ids):
                return _Resp(500, 'upstream error')
            return _Resp(200, json.dumps({tid: SCHEDULE.get(tid, schedule()) for tid in ids}))
        if parts.path == tw.MOVIES_PATH:
            want = set(q.get('ids', []))
            return _Resp(200, json.dumps([m for m in MOVIES_API if m['id'] in want]))
        return _Resp(404, 'not found')


def read(site: FakeSite, render=None, limit=30):
    return tw.read_chain(CONF, limit=limit, sess=site, day=DAY, render=render)


# ---------------------------------------------------------------------------
# Pure parsing
# ---------------------------------------------------------------------------
def test_asset_prefix():
    check(tw.asset_prefix(HOME_HTML, 'bandb') == PREFIX, 'build folder found for the site code')
    check(tw.asset_prefix(HOME_HTML) == PREFIX, 'without a code, the folder named most often')
    check(tw.asset_prefix('<html></html>', 'bandb') is None, 'no folder on a page without one')


def test_slug_and_url():
    cases = {
        'B&B Mystery Movie': 'bandb-mystery-movie',
        '$5 Secret Movie 11/2/26': 'dollar5-secret-movie-11226',
        'Stray Kids World Tour <RUN IT> LIVE VIEWING': 'stray-kids-world-tour-lessrun-itgreater-live-viewing',
        'D\u00ecdi': 'didi',
        "Disney's Hexed": 'disneys-hexed',
        'Ocean\u2019s Eleven 25th Anniversary': 'oceans-eleven-25th-anniversary',
        'September 5 with Cast Q&A': 'september-5-with-cast-qanda',
        'The Royal Ballet - Swan Lake (2027)': 'the-royal-ballet-swan-lake-2027',
        'Romeo + Juliet 30th Anniversary': 'romeo-juliet-30th-anniversary',
    }
    for title, want in cases.items():
        check(tw.film_slug(title) == want, f'slug {title!r} -> {want}')
    info = {'title': 'Zootopia 2', 'path': '/movies/M5-zootopia-2'}
    check(tw.film_url(BASE, 'M5', info, 'movies') == BASE + '/movies/M5-zootopia-2/',
          'film page keeps its path and gains the trailing slash the site answers on')
    check(tw.film_url(BASE, 'M9', {'title': 'B&B Mystery Movie'}, 'showtimes')
          == BASE + '/showtimes/M9-bandb-mystery-movie/', 'a film missing from the list gets the site\'s own path')
    check(tw.film_url(BASE, 'M1', {'path': 'https://other.example/x/'}, 'movies') == 'https://other.example/x/',
          'an absolute path is used as given')


def test_titles():
    pairs = {
        'The Odyssey: The IMAX Experience': 'The Odyssey',
        'Dune: Part Three - The IMAX Experience': 'Dune: Part Three',
        'Avatar: Fire and Ash: The IMAX 3D Experience': 'Avatar: Fire and Ash',
        'Dune: Part Three Insider Screenings in Premium Large Format': 'Dune: Part Three',
        'Dune: Part Three Insider Screenings': 'Dune: Part Three',
        'The Experience': 'The Experience',
        'Wicked: For Good': 'Wicked: For Good',
    }
    for raw, want in pairs.items():
        got = base_title(tw.print_title(raw))
        check(got == want, f'{raw!r} ranks as {want!r} (got {got!r})')
    p = tw.print_title('The Odyssey: The IMAX Experience')
    check(clean_title(p) != base_title(p), 'the IMAX print still reads as a print, not the plain film')
    check(tw.not_a_film('Epic Theatres VIP Rental'), 'a VIP rental is not a film')
    check(tw.not_a_film('Private Event'), 'a private event is not a film')
    check(not tw.not_a_film('The Rental'), "'The Rental' is a film")
    check(not tw.not_a_film('Wicked: For Good'), 'a film is a film')


def test_count_day():
    got = tw.count_day({'T1': SCHEDULE['T1'], 'T2': SCHEDULE['T2']}, DAY,
                       ['Showtime.Restriction.PrivateScreening'])
    check(got.get('M5') == {'T1': 3, 'T2': 2},
          'one id on two screens counts twice, a repeat once, a hidden tag not at all')
    check(got.get('M3') == {'T1': 4}, 'all four IMAX showtimes count')
    check('M8' not in got, 'tomorrow-only presale has no showtime today')
    open_count = tw.count_day({'T2': SCHEDULE['T2']}, DAY)
    check(open_count.get('M5') == {'T2': 3}, 'without the site\'s hidden tags every show counts')
    junk = {'T9': None, 'T8': {'schedule': None}, 'T7': {'schedule': {'M1': None}},
            'T6': {'schedule': {'M1': {DAY: [None, 'x', {'startsAt': f'{DAY}T10:00:00'}]}}}}
    check(tw.count_day(junk, DAY) == {'M1': {'T6': 1}}, 'malformed blocks are skipped, not fatal')
    keyed_by_time = {'T1': {'schedule': {'M1': {'evening': [{'startsAt': f'{DAY}T19:00:00'},
                                                            {'startsAt': f'{NEXT}T19:00:00'}]}}}}
    check(tw.count_day(keyed_by_time, DAY) == {'M1': {'T1': 1}},
          'a day key that is not a date falls back to the start time')
    check(tw.count_day(None, DAY) == {} and tw.count_day([], DAY) == {}, 'no answer, no counts')


def test_lists():
    ths = tw.chain_theaters(SQ_THEATERS['data'])
    check([t['id'] for t in ths] == ['T1', 'T2', 'T3', 'T4', 'T5'],
          'open US theaters in the chain\'s order; closed, Canadian and repeated ones out')
    check(next(t for t in ths if t['id'] == 'T4')['timeZone'] == 'America/Chicago',
          'a theater with no time zone takes the chain\'s usual one')
    check(tw.main_time_zone(ths) == 'America/Chicago', 'the chain\'s day follows most of its theaters')
    check(tw.chain_theaters(SQ_SETTINGS['data']) == [] or not tw._has_theaters(SQ_SETTINGS['data']),
          'the settings query\'s zone-less theater list is not the theater list')
    check(tw.excluded_tags(SQ_SETTINGS['data']) == ['Showtime.Restriction.PrivateScreening'],
          'site-wide hidden tags read from settings')
    movies = tw.movie_index(SQ_MOVIES['data'])
    check(movies['M10']['title'] == 'A Christmas Story (1983)'
          and movies['M10']['poster'] == 'https://img.example/override.jpg',
          'the chain\'s own title and poster override win')
    check(movies['M7']['hidden'] and movies['M5']['order'] == 1, 'hidden flag and chain order kept')
    check(tw.movies_page_slug(SQ_SETTINGS['data'], movies) == 'showtimes', 'movies page slug from settings')
    check(tw.movies_page_slug(None, movies) == 'showtimes', 'movies page slug from the film paths')
    check(tw.movies_page_slug(None, {}) == 'movies', 'movies page slug default')
    url = tw.schedule_url(BASE, ths[:2], DAY, ['Showtime.Restriction.PrivateScreening'])
    q = parse_qs(urlsplit(url).query)
    check(urlsplit(url).path == tw.SCHEDULE_PATH
          and [json.loads(v)['id'] for v in q['theaters']] == ['T1', 'T2']
          and q['from'] == [f'{DAY}T00:00:00'] and q['to'] == [f'{NEXT}T00:00:00']
          and q['nin'] == ['Showtime.Restriction.PrivateScreening'],
          'schedule call: each theater with its zone, local midnight to midnight, hidden tags')


# ---------------------------------------------------------------------------
# A whole chain, offline
# ---------------------------------------------------------------------------
def test_read_chain():
    tw.THEATERS_PER_CALL = 2
    site = FakeSite()
    items, meta = read(site)
    titles = [i['title'] for i in items]
    check(titles == ['The Odyssey', 'Zootopia 2', 'Wicked: For Good', 'B&B Mystery Movie',
                     'A Christmas Story (1983)'], f'ranked by showtimes today: {titles}')
    by = {i['title']: i for i in items}
    check([i['rank'] for i in items] == [1, 2, 3, 4, 5], 'ranks run 1 to 5')
    od = by.get('The Odyssey', {})
    check(od.get('showtimes') == 8 and od.get('theaters') == 3,
          'the IMAX listing adds to the plain film: 8 showtimes in 3 theaters')
    check(od.get('url') == BASE + '/showtimes/M4-the-odyssey/' and od.get('image') == 'https://img.example/M4.jpg',
          'the plain listing lends its page and poster')
    zo, wi = by.get('Zootopia 2', {}), by.get('Wicked: For Good', {})
    check(zo.get('showtimes') == 6 and wi.get('showtimes') == 6 and wi.get('theaters') == 3,
          'Wicked and its open caption print: 6 showtimes in 3 theaters')
    check(titles.index('Zootopia 2') < titles.index('Wicked: For Good'),
          'a tie goes to the chain\'s own order (Zootopia 2 is placed first), not the alphabet')
    check(by.get('B&B Mystery Movie', {}).get('url') == BASE + '/showtimes/M9-bandb-mystery-movie/'
          and by['B&B Mystery Movie'].get('image') == 'https://img.example/M9.jpg',
          'a scheduled film missing from the list is named from the movie details call')
    check(not any('Rental' in t or 'Avatar' in t or 'Staff' in t for t in titles),
          'rental, presale and hidden film dropped')
    check(meta['theaters'] == 5 and meta['theaters_listed'] == 5 and meta['complete'],
          f"every theater read: {meta['theaters']} of {meta['theaters_listed']}")
    check(meta['theaters_showing'] == 4, 'four theaters show a film today (the fifth only a hidden one)')
    check(meta['date'] == DAY and meta['blocked'] is False and 'reason' not in meta, 'meta is clean')
    check(meta['films'] == 5 and meta['showtimes'] == 23, 'meta totals: 5 films, 23 showtimes')
    calls = site.schedule_calls
    check(len(calls) == 3 and all(len(c['ids']) <= 2 for c in calls)
          and sorted(i for c in calls for i in c['ids']) == ['T1', 'T2', 'T3', 'T4', 'T5'],
          'theaters read in batches, each one once')
    check(all(c['nin'] == ['Showtime.Restriction.PrivateScreening'] for c in calls),
          'the site\'s hidden tags go with every call')
    check(not any('1575531621' in u for u in site.calls), 'unneeded static queries are not downloaded')
    top2, _ = read(FakeSite(), limit=2)
    check([i['title'] for i in top2] == ['The Odyssey', 'Zootopia 2'], 'limit is honored')


def test_batch_failure_reads_singles():
    tw.THEATERS_PER_CALL = 2
    site = FakeSite(fail_multi_with={'T3'})
    items, meta = read(site)
    check(meta['complete'] and meta['theaters'] == 5, 'a failed batch is read one theater at a time')
    check(any(c['ids'] == ['T3'] for c in site.schedule_calls)
          and any(c['ids'] == ['T4'] for c in site.schedule_calls), 'both theaters of the failed batch asked alone')
    check(items and items[0]['title'] == 'The Odyssey', 'ranking unchanged')


def test_wall_then_headless():
    tw.THEATERS_PER_CALL = 2
    asked = {}

    def render_ok(page_url, urls, concurrency=4):
        asked['page'], asked['urls'], asked['conc'] = page_url, list(urls), concurrency
        out = {}
        for u in urls:
            ids = [json.loads(v)['id'] for v in parse_qs(urlsplit(u).query)['theaters']]
            out[u] = json.dumps({tid: SCHEDULE.get(tid, schedule()) for tid in ids})
        return out

    items, meta = read(FakeSite(wall_for={'T5'}), render=render_ok)
    check(asked.get('page') == BASE + '/' and len(asked.get('urls', [])) == 1 and asked.get('conc') <= 4,
          'a walled call is asked again from a headless page, at most 4 at a time')
    check(meta['complete'] and not meta['blocked'] and meta.get('rendered'),
          'the headless read completes the chain')
    items, meta = read(FakeSite(wall_for={'T5'}), render=lambda *a, **k: {})
    check(meta['blocked'] and not meta['complete'] and meta['theaters'] == 4,
          'a wall that holds leaves the chain incomplete and flagged blocked')
    check('bot check' in meta.get('reason', ''), 'the reason says a bot check stopped it')
    check(items and items[0]['title'] == 'The Odyssey', 'what was read still ranks')
    items, meta = read(FakeSite(wall_for={'T1', 'T2', 'T3', 'T4', 'T5'}), render=None)
    check(items == [] and meta['blocked'] and meta['theaters'] == 0, 'a chain walled everywhere: no items, blocked')


def test_static_query_search():
    tw.THEATERS_PER_CALL = 20
    moved = {'hZ': {'data': ['an odd shape']}, 'hA': SQ_SETTINGS, 'hB': {'data': {'menu': {}}},
             'hY': {'data': {'allTheater': 'not a list'}}, 'hC': SQ_THEATERS, 'hD': SQ_MOVIES}
    site = FakeSite(index=dict(INDEX, staticQueryHashes=list(moved)), sq=moved)
    items, meta = read(site)
    check(meta['complete'] and [i['title'] for i in items][:2] == ['The Odyssey', 'Zootopia 2'],
          'new query hashes are found by their shape, odd answers skipped')
    site = FakeSite(sq={'2506275789': SQ_THEATERS})
    items, meta = read(site)
    check(meta['complete'] and any(i['title'] == 'B&B Mystery Movie' for i in items)
          and not any(i['title'] == 'Zootopia 2' for i in items),
          'without the film list, only films the movie details call names rank')
    _, meta = read(FakeSite(sq={}))
    check(meta['theaters'] == 0 and meta.get('reason') == 'theater list not found', 'no theater list: a reason')


def test_homepage_fallback():
    tw.THEATERS_PER_CALL = 20
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    site = FakeSite(home=None, builds={yesterday})
    items, meta = read(site)
    check(meta['complete'] and not meta['blocked'] and items
          and any(f'/prod/bandb/{yesterday}/public/page-data/sq/' in u for u in site.calls),
          "a walled homepage falls back to the dated build folders (yesterday's here)")
    _, meta = read(FakeSite(home=None, builds=()))
    check(meta['blocked'] and meta.get('reason') == 'bot check on the homepage',
          'no build folder anywhere after a wall: blocked')
    _, meta = read(FakeSite(home='<html>moved to a new platform</html>', builds=()))
    check(not meta['blocked'] and meta.get('reason') == 'site build not found',
          'a homepage without a build folder and no wall: not blocked, with a reason')


def test_fetch_never_raises():
    items, meta = tw.fetch('nope')
    check(items == [] and 'unknown site' in meta.get('reason', ''), 'unknown site: empty with a reason')
    real_session, real_read = tw._session, tw.read_chain
    try:
        tw._session = lambda: FakeSite(raise_all=True)
        items, meta = tw.fetch('marquee')
        check(items == [] and meta.get('reason') and meta['theaters'] == 0,
              'every request failing: empty with a reason')

        def boom(*a, **k):
            raise ValueError('bad shape')
        tw.read_chain = boom
        items, meta = tw.fetch('epic')
        check(items == [] and meta.get('reason', '').startswith('read failed'), 'an exception inside: empty')
    finally:
        tw._session, tw.read_chain = real_session, real_read
    check(set(tw.SITES) == {'bbtheatres', 'showcase', 'landmark', 'marquee', 'epic'}
          and all(s['base'].startswith('https://www.') and s['label'] for s in tw.SITES.values()),
          'five chains registered with a label and their own https site')


def main() -> int:
    logging.disable(logging.CRITICAL)
    for t in (test_asset_prefix, test_slug_and_url, test_titles, test_count_day, test_lists,
              test_read_chain, test_batch_failure_reads_singles, test_wall_then_headless,
              test_static_query_search, test_homepage_fallback, test_fetch_never_raises):
        try:
            t()
        except Exception as e:
            check(False, f'{t.__name__} raised {type(e).__name__}: {e}')
    if FAILURES:
        print(f'\n{len(FAILURES)} FAILED')
        return 1
    print('\nALL OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
