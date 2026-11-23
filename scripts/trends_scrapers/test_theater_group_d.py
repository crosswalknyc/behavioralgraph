"""Offline tests for the Marcus Theatres and Megaplex Theatres readers.

Every fixture below is the shape the chain's own data service answered
on 2026-11-23, cut down to a few rows. Nothing here touches the network,
the donated cookie store, or a browser.

    cd <repo> && PYTHONPATH=. python3 scripts/trends_scrapers/test_theater_group_d.py
"""

from __future__ import annotations

import base64
import inspect
import json
import os
from unittest import mock

from scripts.trends_scrapers import theater_common as tc
from scripts.trends_scrapers import theater_marcus as mrc
from scripts.trends_scrapers import theater_megaplex as mpx

DAY = '2026-11-23'
WALL = ('<html> <head> <META NAME="robots" CONTENT="noindex,nofollow"> '
        '<script src="/_Incapsula_Resource?SWJIYLWA=5074a744e2e3d891814e9a2d"> '
        '</script> <body> </body></html>')


def _today(_tz='America/Chicago'):
    return DAY


# ---------------------------------------------------------------------------
# Megaplex fixtures: /api/cinema/cinemas and /api/film/cinemaFilms/<id>
# ---------------------------------------------------------------------------
MPX_CINEMAS = [
    {'id': '0001', 'cinemaNationalId': '', 'name': 'Sandy at Jordan Commons',
     'isGiftStore': False, 'loyaltyCode': '1', 'timeZoneId': 'Mountain Standard Time'},
    {'id': '0018', 'cinemaNationalId': '', 'name': 'Mesquite',
     'isGiftStore': False, 'loyaltyCode': '18', 'timeZoneId': 'Pacific Standard Time'},
    {'id': '0099', 'name': 'Gift Store', 'isGiftStore': True},
    {'id': '0001', 'name': 'Sandy at Jordan Commons', 'isGiftStore': False},
]


def _mpx_session(cid, sid, showtime, business):
    return {'id': f'{cid}-{sid}', 'cinemaId': cid, 'sessionId': str(sid),
            'showtime': showtime, 'sessionBusinessDate': business + 'T00:00:00',
            'screenName': 'Screen 01', 'sessionAttributesNames': ['2D', 'Luxury'],
            'allowTicketSales': True, 'soldoutStatus': 0, 'typeCode': 'N'}


def _mpx_film(cid, code, title, seq, sessions):
    return {'id': f'{cid}-{code}', 'cinemaId': cid, 'scheduledFilmId': code,
            'filmHOPK': code, 'filmHOCode': 'F' + code[-6:], 'title': title,
            'displaySequence': seq, 'graphicUrl': '', 'rating': 'PG-13',
            'openingDate': '2026-11-20T00:00:00', 'sessionCount': 0,
            'sessions': sessions}


MPX_FILMS = {
    '0001': [
        _mpx_film('0001', 'HO00000001', 'Aardvark Story', 50, [
            _mpx_session('0001', 11, '2026-11-23T13:00:00', DAY),
            _mpx_session('0001', 12, '2026-11-23T18:00:00', DAY)]),
        _mpx_film('0001', 'HO00000002', 'Hexed', 1, [
            _mpx_session('0001', 21, '2026-11-25T19:00:00', '2026-11-25')]),
        _mpx_film('0001', 'HO00003719', 'The Hunger Games: Sunrise on the Reaping', 1, [
            _mpx_session('0001', 31, '2026-11-23T13:00:00', DAY),
            _mpx_session('0001', 32, '2026-11-23T16:00:00', DAY),
            _mpx_session('0001', 33, '2026-11-23T19:00:00', DAY),
            _mpx_session('0001', 34, '2026-11-24T00:30:00', DAY),
            _mpx_session('0001', 35, '2026-11-24T13:00:00', '2026-11-24')]),
        _mpx_film('0001', 'HO00004600', 'Wicked: For Good', 50, [
            _mpx_session('0001', 41, '2026-11-23T14:00:00', DAY),
            _mpx_session('0001', 42, '2026-11-23T20:00:00', DAY)]),
        _mpx_film('0001', 'HO00004601', 'Wicked: For Good Open Caption', 50, [
            _mpx_session('0001', 43, '2026-11-23T17:00:00', DAY)]),
        _mpx_film('0001', 'HO00000003', 'Zebra Story', 2, [
            _mpx_session('0001', 51, '2026-11-23T15:00:00', DAY),
            _mpx_session('0001', 52, '2026-11-23T21:00:00', DAY)]),
    ],
    '0018': [
        _mpx_film('0018', 'HO00003719', 'The Hunger Games: Sunrise on the Reaping', 3, [
            _mpx_session('0018', 61, '2026-11-23T12:30:00', DAY),
            _mpx_session('0018', 62, '2026-11-23T19:30:00', DAY)]),
        _mpx_film('0018', 'HO00004600', 'Wicked: For Good', 50, [
            _mpx_session('0018', 71, '2026-11-23T15:15:00', DAY)]),
    ],
}


def _mpx_routes(url):
    if url == mpx.CINEMAS_URL:
        return 200, json.dumps(MPX_CINEMAS)
    for cid, films in MPX_FILMS.items():
        if url == mpx.FILMS_URL.format(cid=cid):
            return 200, json.dumps(films)
    return 404, ''


def _expect_megaplex(items, meta, source):
    assert [i['title'] for i in items] == [
        'The Hunger Games: Sunrise on the Reaping', 'Wicked: For Good',
        'Zebra Story', 'Aardvark Story'], items
    assert [i['showtimes'] for i in items] == [6, 4, 2, 2], items
    assert [i['theaters'] for i in items] == [2, 2, 1, 1], items
    assert [i['rank'] for i in items] == [1, 2, 3, 4]
    hg, wk = items[0], items[1]
    assert hg['url'] == ('https://megaplex.com/film/HO00003719/'
                         'the-hunger-games-sunrise-on-the-reaping'), hg
    assert hg['image'] == 'https://img.megaplextheatres.com/FilmPosterGraphic/HO00003719'
    # The plain print names the merged row and lends its link.
    assert wk['url'] == 'https://megaplex.com/film/HO00004600/wicked-for-good', wk
    assert meta == {'theaters': 2, 'date': DAY, 'blocked': False, 'source': source,
                    'theaters_listed': 2, 'films_today': 4}, meta


def test_megaplex_cinema_ids():
    assert mpx.cinema_ids(MPX_CINEMAS) == ['0001', '0018']
    assert mpx.cinema_ids({'error': 'x'}) == []
    assert mpx.cinema_ids(None) == []
    print('megaplex cinema ids ok')


def test_megaplex_business_day():
    rows = mpx.theater_rows('0001', MPX_FILMS['0001'], DAY)
    by = {r['title']: r['showtimes'] for r in rows}
    # 13:00, 16:00, 19:00 and the 12:30 AM show of the same business night.
    assert by['The Hunger Games: Sunrise on the Reaping'] == 4, by
    assert 'Hexed' not in by, by          # presale only, nothing today
    assert mpx.business_day({'showtime': '2026-11-24T00:30:00'}) == '2026-11-24'
    assert mpx.business_day({'showtime': '2026-11-24T00:30:00',
                             'sessionBusinessDate': '2026-11-23T00:00:00'}) == DAY
    print('megaplex business day ok')


def test_megaplex_rank_and_links():
    per = [mpx.theater_rows(cid, films, DAY) for cid, films in MPX_FILMS.items()]
    items = tc.rank_by_showtimes(mpx.chain_rows(per), limit=30)
    # Zebra Story and Aardvark Story tie on 2; the chain features Zebra
    # (display sequence 2), so A to Z order never decides it.
    assert [i['title'] for i in items][2:] == ['Zebra Story', 'Aardvark Story'], items
    assert mpx.slug('A Beautiful Mind (25th Anniversary)') == 'a-beautiful-mind-25th-anniversary'
    assert mpx.slug('Spider-Man: Brand New Day') == 'spider-man-brand-new-day'
    assert mpx.slug('Fast & Furious') == 'fast-and-furious'
    print('megaplex rank and links ok')


def test_megaplex_fetch_end_to_end():
    with mock.patch.object(mpx, 'today_iso', _today), \
            mock.patch.object(mpx, 'PAUSE_S', 0), \
            mock.patch.object(mpx, '_get', side_effect=_mpx_routes), \
            mock.patch.object(mpx, '_has_donation', return_value=False):
        items, meta = mpx.fetch(limit=30)
        _expect_megaplex(items, meta, 'api')
        top, _ = mpx.fetch(limit=2)
        assert len(top) == 2
    print('megaplex fetch ok')


def test_megaplex_walled_without_donation_is_blocked_at_once():
    calls = []
    with mock.patch.object(mpx, 'today_iso', _today), \
            mock.patch.object(mpx, '_get', return_value=(200, WALL)), \
            mock.patch.object(mpx, '_has_donation', return_value=False), \
            mock.patch.object(mpx, 'render_then_fetch',
                              side_effect=lambda *a, **k: calls.append(a) or {}):
        items, meta = mpx.fetch()
    assert items == [] and meta['blocked'] is True and meta['theaters'] == 0, meta
    assert meta['date'] == DAY and 'donation' in meta['reason'], meta
    assert calls == [], 'the browser must not open without a donation'
    print('megaplex blocked ok')


def test_megaplex_walled_with_donation_reads_in_the_browser():
    def fake_rtf(page_url, urls, **kw):
        assert page_url == 'https://megaplex.com/' and kw.get('cookie_domain') == 'megaplex.com'
        return {u: _mpx_routes(u)[1] for u in urls}
    with mock.patch.object(mpx, 'today_iso', _today), \
            mock.patch.object(mpx, 'PAUSE_S', 0), \
            mock.patch.object(mpx, '_get', return_value=(403, WALL)), \
            mock.patch.object(mpx, '_has_donation', return_value=True), \
            mock.patch.object(mpx, 'render_then_fetch', side_effect=fake_rtf):
        items, meta = mpx.fetch()
    _expect_megaplex(items, meta, 'browser')
    # A browser whose own request is refused is still a wall.
    with mock.patch.object(mpx, 'today_iso', _today), \
            mock.patch.object(mpx, 'render_then_fetch',
                              side_effect=lambda page_url, urls, **kw: {u: None for u in urls}):
        items, meta = mpx.read(browser='force', cookie_domain=None)
    assert items == [] and meta['blocked'] is True, meta
    print('megaplex browser ok')


def test_megaplex_partial_read_says_so():
    def routes(url):
        if url == mpx.FILMS_URL.format(cid='0018'):
            return 0, ''
        return _mpx_routes(url)
    with mock.patch.object(mpx, 'today_iso', _today), \
            mock.patch.object(mpx, 'PAUSE_S', 0), \
            mock.patch.object(mpx, '_get', side_effect=routes):
        items, meta = mpx.fetch()
    assert meta['theaters'] == 1 and meta['theaters_listed'] == 2, meta
    assert meta['reason'] == '1 of 2 theaters could not be read' and not meta['blocked'], meta
    assert items[0]['title'] == 'The Hunger Games: Sunrise on the Reaping' and items[0]['showtimes'] == 4
    print('megaplex partial ok')


def test_megaplex_never_raises():
    with mock.patch.object(mpx, 'today_iso', _today), \
            mock.patch.object(mpx, '_get', side_effect=RuntimeError('boom')):
        items, meta = mpx.fetch()
    assert items == [] and meta['blocked'] is False and meta['reason'], meta
    with mock.patch.object(mpx, 'today_iso', _today), \
            mock.patch.object(mpx, '_get', return_value=(500, 'oops')):
        items, meta = mpx.fetch()
    assert items == [] and meta['blocked'] is False and 'theater list' in meta['reason'], meta
    print('megaplex never raises ok')


# ---------------------------------------------------------------------------
# Marcus fixtures: the site's home page and script, then the service
# ---------------------------------------------------------------------------
HOME = """<!DOCTYPE html><html lang="en"><head>
<script src="/bition-Gripe-and-Colm-Sunne-lacken-the-Why-Swors" async></script>
<script src="/_next/static/chunks/webpack-5a9b0e285812a41e.js" async=""></script>
<script src="/_next/static/chunks/fd9d1056-0c85016c168cf46e.js" async=""></script>
<script src="/_next/static/chunks/2117-beae348eac3462ee.js" async=""></script>
<script src="/_next/static/chunks/main-app-cf6bd0cc94550bf7.js" async=""></script>
<script src="/_next/static/chunks/app/page-7fd66d4d6ff9849c.js" async=""></script>
<script src="/_next/static/chunks/1514-99aad4d1aa3b9868.js" async=""></script>
<script src="/_next/static/chunks/polyfills-42372ed130431b0a.js" noModule=""></script>
</head><body></body></html>"""
CHUNK_1514 = '/_next/static/chunks/1514-99aad4d1aa3b9868.js'
FAKE_USER, FAKE_PASS = 'visitor@example.test', 'pa{ss!^w.rd#1'
CHUNK_JS = (
    '(self.webpackChunk_N_E=self.webpackChunk_N_E||[]).push([[1514],{89969:function(y,x,t){'
    'async function c(){var x={};return x.accessTokenExpiry=2e3,new Promise((y,t)=>{(0,e.Z)({'
    'url:"https://api-injin.marcustheatres.com/user/v2/token",method:"POST",headers:'
    '{Platform:"web/prod",Version:"1.0"},data:x,auth:{username:"%s",password:"%s"}})'
    '.then(x=>{document.cookie="accessToken="+x.data.accessToken+";path=/"})})}}}]);'
    % (FAKE_USER, FAKE_PASS))

MRC_FILMS = [
    {'id': 'f-hg', 'filmid': 'HO00007650', 'title': 'The Hunger Games: Sunrise on the Reaping',
     'urlSafeName': 'the-hunger-games-sunrise-on-the-reaping-HO00007650',
     'urlsafetitle': 'the-hunger-games-sunrise-on-the-reaping-HO00007650',
     'openingdate': '2026-11-20T00:00:00.000Z', 'setasfeature': False, 'images': [
         {'code': 'x560y440', 'description': None, 'platform': 'WEBSITE',
          'imageurl': 'https://vista.marcustheatres.com/CDN/media/entity/get/FilmBackdrop/'
                      'h-HO00007650?width=560&height=440'},
         {'code': 'x280y420', 'description': '', 'platform': 'WEBSITE',
          'imageurl': 'https://vista.marcustheatres.com/CDN/media/entity/get/FilmPosterGraphic/'
                      'HO00007650?width=280&height=420'},
         {'code': 'x322y478', 'description': 'Films Portrait Poster', 'platform': 'WEBSITE',
          'imageurl': 'https://vista.marcustheatres.com/CDN/media/entity/get/FilmPosterGraphic/'
                      'HO00007650?width=322&height=478'}]},
    {'id': 'f-eb', 'filmid': 'HO00008107', 'title': 'Ebenezer', 'urlSafeName': 'ebenezer-HO00008107',
     'openingdate': '2026-11-13T00:00:00.000Z', 'images': [
         {'code': 'x280y420', 'description': '',
          'imageurl': 'https://vista.marcustheatres.com/CDN/media/entity/get/FilmPosterGraphic/'
                      'HO00008107?width=280&height=420'}]},
    {'id': 'f-oc', 'filmid': 'HO00009001', 'title': 'Ebenezer (Open Caption)',
     'urlSafeName': 'ebenezer-open-caption-HO00009001', 'openingdate': '2026-11-13T00:00:00.000Z',
     'images': []},
    {'id': 'f-zz', 'filmid': 'HO00009003', 'title': 'Zebra Story', 'urlSafeName': 'zebra-story-HO00009003',
     'openingdate': '2026-11-06T00:00:00.000Z', 'images': []},
    {'id': 'f-aa', 'filmid': 'HO00009004', 'title': 'Aardvark Story',
     'urlSafeName': 'aardvark-story-HO00009004', 'openingdate': '2026-11-06T00:00:00.000Z', 'images': []},
    {'id': 'f-cs', 'filmid': 'HO00009002', 'title': 'Coming Later', 'urlSafeName': 'coming-later-HO00009002',
     'openingdate': '2026-11-24T00:00:00.000Z', 'images': []},
]
MRC_PACK = [{'id': 24, 'packType': 'custom', 'status': 1, 'urlSafeName': 'upcomingshows',
             'title': 'All Upcoming Shows', 'packs_films_results': [
                 {'filmid': 'f-hg'}, {'filmid': 'f-zz'}, {'filmid': 'f-eb'},
                 {'filmid': 'f-aa'}, {'filmid': 'f-cs'}, {'filmid': 'f-oc'}]}]


def _mrc_show(sid, cid, fid, showtime, status='Available', tz='America/Chicago'):
    return {'id': sid, 'cinemaid': cid, 'filmid': fid, 'showtime': showtime, 'tz': tz,
            'experiences': ['783a5eb9'], 'formats': ['6f4f1135'], 'generalSeating': False,
            'sessionid': sid[-5:], 'sessionuniqueid': sid[-5:], 'ticketstatus': status}


MRC_SHOWS = [
    _mrc_show('s-01', 'c1', 'f-hg', '2026-11-23T15:00:00.000Z'),
    _mrc_show('s-01', 'c1', 'f-hg', '2026-11-23T15:00:00.000Z'),           # repeated row
    _mrc_show('s-02', 'c1', 'f-hg', '2026-11-23T18:30:00.000Z'),
    _mrc_show('s-03', 'c2', 'f-hg', '2026-11-23T19:00:00.000Z', status='SoldOut'),
    _mrc_show('s-04', 'c2', 'f-hg', '2026-11-24T00:15:00.000Z'),           # late show
    _mrc_show('s-05', 'c1', 'f-eb', '2026-11-23T16:00:00.000Z'),
    _mrc_show('s-06', 'c3', 'f-eb', '2026-11-23T17:00:00.000Z', tz='America/New_York'),
    _mrc_show('s-07', 'c1', 'f-oc', '2026-11-23T20:00:00.000Z'),
    _mrc_show('s-08', 'c2', 'f-zz', '2026-11-23T14:00:00.000Z'),
    _mrc_show('s-09', 'c3', 'f-aa', '2026-11-23T14:00:00.000Z'),
    _mrc_show('s-10', 'c1', 'f-cs', '2026-11-24T14:00:00.000Z'),           # tomorrow
    _mrc_show('s-11', 'c4', 'f-unknown', '2026-11-23T15:00:00.000Z'),
]
MRC_CINEMAS = [{'id': c, 'cinemaid': str(2100 + i), 'name': f'Cinema {i}', 'tz': 'America/Chicago',
                'urlSafeName': f'cinema-{i}'} for i, c in enumerate(['c1', 'c2', 'c3', 'c4', 'c5'])]


class FakeHttp:
    """Stands in for the HTTP client: answers by URL, checks headers."""
    tokens = ['tok-1', 'tok-2']
    unauthorized_once = False
    pack_showtimes_down = False
    home = HOME

    def __init__(self):
        self.calls = []
        self.token_posts = 0
        self._films_refused = False

    def post(self, url, body, headers=None, retries=1):
        self.calls.append(('POST', url))
        assert url == mrc.API + '/user/v2/token', url
        want = 'Basic ' + base64.b64encode(f'{FAKE_USER}:{FAKE_PASS}'.encode()).decode()
        assert headers['Authorization'] == want, headers
        assert headers['Platform'] == 'web/prod'
        assert json.loads(body)['keepMeSignedIn'] is True
        tok = self.tokens[min(self.token_posts, len(self.tokens) - 1)]
        self.token_posts += 1
        return 200, json.dumps({'accessToken': tok, 'refreshToken': 'r-' + tok,
                                'firstname': 'website'})

    def get(self, url, headers=None, retries=1):
        self.calls.append(('GET', url))
        if url == mrc.SITE + '/':
            return 200, self.home
        if url == mrc.SITE + CHUNK_1514:
            return 200, CHUNK_JS
        if url.startswith(mrc.SITE + '/_next/'):
            return 200, '(self.webpackChunk_N_E=self.webpackChunk_N_E||[]).push([[2117],{}]);'
        if not url.startswith(mrc.API):
            return 404, ''
        auth = (headers or {}).get('Authorization', '')
        assert auth.startswith('Bearer tok-'), headers
        assert headers['appplatform'] == 'WEBSITE'
        path = url[len(mrc.API):]
        if path == '/cms/v2/films':
            if self.unauthorized_once and not self._films_refused:
                self._films_refused = True
                return 500, '{"code":500,"message":"Unauthorized","data":{}}'
            return 200, json.dumps(MRC_FILMS)
        if path == f'/cms/v2/packs/upcomingshows?showdate={DAY}':
            return 200, json.dumps(MRC_PACK)
        if path == f'/cms/v2/packs/24/showtimes?showdate={DAY}':
            if self.pack_showtimes_down:
                return 502, '<html>Bad gateway</html>'
            return 200, json.dumps(MRC_SHOWS)
        if path == '/cms/v2/cinemas':
            return 200, json.dumps(MRC_CINEMAS)
        for fid in ('f-hg', 'f-zz', 'f-eb', 'f-aa', 'f-cs', 'f-oc'):
            if path == f'/cms/v2/{fid}/showtimes?showdate={DAY}':
                return 200, json.dumps([s for s in MRC_SHOWS if s['filmid'] == fid])
        return 404, '{"code":404,"message":"Not found"}'


def _expect_marcus(items, meta, source, listed=5, unnamed=1):
    assert [i['title'] for i in items] == [
        'The Hunger Games: Sunrise on the Reaping', 'Ebenezer', 'Zebra Story',
        'Aardvark Story'], items
    # Hunger Games: 15:00, 18:30, the sold-out 19:00 and the 12:15 AM late
    # show; the repeated row counts once.
    assert [i['showtimes'] for i in items] == [4, 3, 1, 1], items
    assert [i['theaters'] for i in items] == [2, 2, 1, 1], items
    hg, eb = items[0], items[1]
    assert hg['url'] == ('https://www.marcustheatres.com/movies/'
                         'the-hunger-games-sunrise-on-the-reaping-HO00007650'), hg
    assert hg['image'].endswith('FilmPosterGraphic/HO00007650?width=322&height=478'), hg
    assert eb['url'] == 'https://www.marcustheatres.com/movies/ebenezer-HO00008107', eb
    assert eb['image'].endswith('FilmPosterGraphic/HO00008107?width=280&height=420'), eb
    want = {'theaters': 3, 'date': DAY, 'blocked': False, 'source': source,
            'films_today': 4}
    if unnamed:
        want['unnamed_showtimes'] = unnamed
    if listed:
        want['theaters_listed'] = listed
    assert meta == want, meta


def test_marcus_script_order_and_login():
    urls = mrc.script_urls(HOME)
    assert urls[0] == mrc.SITE + CHUNK_1514, urls
    assert not any(('webpack-' in u or 'polyfills-' in u or 'main-app-' in u) for u in urls), urls
    assert mrc.login_from_script(CHUNK_JS) == (FAKE_USER, FAKE_PASS)
    assert mrc.login_from_script('var a="/user/v2/token";') is None
    assert mrc.login_from_script('auth:{username:"a",password:"b"}') is None  # no token call
    print('marcus login ok')


def test_marcus_day_rule():
    assert mrc.counts_for_day(DAY, '2026-11-23T10:30:00.000Z')
    assert mrc.counts_for_day(DAY, '2026-11-24T00:15:00.000Z')
    assert not mrc.counts_for_day(DAY, '2026-11-24T14:00:00.000Z')
    assert not mrc.counts_for_day(DAY, '2026-11-22T22:00:00.000Z')
    assert not mrc.counts_for_day(DAY, None)
    print('marcus day rule ok')


def test_marcus_rows_and_rank():
    _, order = mrc.pack_info(MRC_PACK)
    rows, stats = mrc.chain_rows(MRC_SHOWS, MRC_FILMS, order, DAY)
    assert stats == {'theaters': 3, 'unnamed_showtimes': 1}, stats
    items = tc.rank_by_showtimes(rows, limit=30)
    # Zebra and Aardvark tie on 1; the site's Book Now order puts Zebra
    # first, so A to Z order never decides it.
    assert [i['title'] for i in items][2:] == ['Zebra Story', 'Aardvark Story'], items
    assert 'Coming Later' not in [i['title'] for i in items]
    assert mrc.pack_info([]) == (None, [])
    print('marcus rows ok')


def _marcus_env(http_cls):
    mrc._LOGIN.clear()
    return [mock.patch.object(mrc, 'today_iso', _today),
            mock.patch.object(mrc, 'PAUSE_S', 0),
            mock.patch.object(mrc, '_Http', http_cls)]


def _run(patches, fn):
    for p in patches:
        p.start()
    try:
        return fn()
    finally:
        for p in reversed(patches):
            p.stop()


def test_marcus_fetch_end_to_end():
    made = []

    class Http(FakeHttp):
        def __init__(self):
            super().__init__()
            made.append(self)
    items, meta = _run(_marcus_env(Http) + [
        mock.patch.object(mrc, '_has_donation', return_value=False)], mrc.fetch)
    _expect_marcus(items, meta, 'api')
    gets = [u for m, u in made[0].calls if m == 'GET']
    assert gets[:2] == [mrc.SITE + '/', mrc.SITE + CHUNK_1514], gets
    assert made[0].token_posts == 1
    print('marcus fetch ok')


def test_marcus_new_token_when_refused():
    class Http(FakeHttp):
        unauthorized_once = True
    holder = []

    class Http2(Http):
        def __init__(self):
            super().__init__()
            holder.append(self)
    items, meta = _run(_marcus_env(Http2), mrc.fetch)
    _expect_marcus(items, meta, 'api')
    assert holder[0].token_posts == 2, holder[0].token_posts
    print('marcus token refresh ok')


def test_marcus_one_read_per_film_when_schedule_call_fails():
    class Http(FakeHttp):
        pack_showtimes_down = True
    items, meta = _run(_marcus_env(Http), mrc.fetch)
    # One read per film only asks for films the site lists.
    _expect_marcus(items, meta, 'api', unnamed=0)
    print('marcus per-film fallback ok')


def test_marcus_walled_without_donation_is_blocked_at_once():
    class Http(FakeHttp):
        home = WALL
    opened = []
    items, meta = _run(_marcus_env(Http) + [
        mock.patch.object(mrc, '_has_donation', return_value=False),
        mock.patch('scripts.trends_scrapers._playwright.render_pages',
                   side_effect=lambda *a, **k: opened.append(a) or [])], mrc.fetch)
    assert items == [] and meta['blocked'] is True and 'donation' in meta['reason'], meta
    assert opened == [], 'the browser must not open without a donation'
    print('marcus blocked ok')


def _fake_render(answer):
    def render_pages(pages, **kw):
        assert kw.get('cookie_domain') in ('marcustheatres.com', None), kw

        class Page:
            def evaluate(self, js, arg):
                assert arg['day'] == DAY and arg['api'] == mrc.API, arg
                assert 'accessToken' in js
                return answer
        return [(pages[0][0], kw['page_hook'](Page(), pages[0][0]))]
    return render_pages


def test_marcus_walled_with_donation_reads_in_the_browser():
    class Http(FakeHttp):
        home = WALL
    answer = json.dumps({'head': '<html>', 'token': True, 'films': json.dumps(MRC_FILMS),
                         'packs': json.dumps(MRC_PACK), 'shows': json.dumps(MRC_SHOWS),
                         'cinemas': json.dumps(MRC_CINEMAS)})
    items, meta = _run(_marcus_env(Http) + [
        mock.patch.object(mrc, '_has_donation', return_value=True),
        mock.patch('scripts.trends_scrapers._playwright.render_pages',
                   side_effect=_fake_render(answer))], mrc.fetch)
    _expect_marcus(items, meta, 'browser')
    # The page itself sat on the bot check and never got a token.
    walled = json.dumps({'head': WALL, 'token': False})
    items, meta = _run(_marcus_env(FakeHttp) + [
        mock.patch('scripts.trends_scrapers._playwright.render_pages',
                   side_effect=_fake_render(walled))],
        lambda: mrc.read(browser='force', cookie_domain=None))
    assert items == [] and meta['blocked'] is True, meta
    print('marcus browser ok')


def test_marcus_never_raises():
    class Http(FakeHttp):
        def get(self, url, headers=None, retries=1):
            raise RuntimeError('boom')
    items, meta = _run(_marcus_env(Http), mrc.fetch)
    assert items == [] and meta['blocked'] is False and meta['reason'], meta

    class NoLogin(FakeHttp):
        def get(self, url, headers=None, retries=1):
            if url == mrc.SITE + CHUNK_1514:
                return 200, 'console.log(1)'
            return super().get(url, headers, retries)
    items, meta = _run(_marcus_env(NoLogin), mrc.fetch)
    assert items == [] and meta['blocked'] is False and 'sign-in' in meta['reason'], meta
    print('marcus never raises ok')


def test_words_in_the_data_are_not_a_wall():
    odd = [_mpx_film('0001', 'HO00000009', 'Access Denied', 50, [
        _mpx_session('0001', 91, '2026-11-23T19:00:00', DAY)])] + MPX_FILMS['0001']
    with mock.patch.object(mpx, 'PAUSE_S', 0), \
            mock.patch.object(mpx, '_get', return_value=(200, json.dumps(odd))):
        state, rows = mpx._read_theater('0001', DAY)
    assert state == 'ok' and 'Access Denied' in [r['title'] for r in rows], (state, rows)

    films = [dict(MRC_FILMS[0], title='Access Denied', synopsis='An unauthorized captcha story.')]

    class Http(FakeHttp):
        def get(self, url, headers=None, retries=1):
            if url == mrc.API + '/cms/v2/films':
                return 200, json.dumps(films)
            return super().get(url, headers, retries)
    http = Http()
    data, walled = mrc._Api(http, 'tok-1', (FAKE_USER, FAKE_PASS)).get('/cms/v2/films')
    assert data == films and walled is False and http.token_posts == 0, (walled, http.calls)
    assert mrc._walled_page(WALL) is True
    assert mrc._walled_page(HOME.replace('</body>', 'Protected by reCAPTCHA</body>')) is False
    print('data words ok')


# ---------------------------------------------------------------------------
# Contract and house style
# ---------------------------------------------------------------------------
def test_contract():
    for mod, key, label, dom in ((mrc, 'marcus', 'Marcus Theatres', 'marcustheatres.com'),
                                 (mpx, 'megaplex', 'Megaplex Theatres', 'megaplex.com')):
        assert (mod.KEY, mod.LABEL, mod.COOKIE_DOMAIN) == (key, label, dom)
        assert inspect.signature(mod.fetch).parameters['limit'].default == tc.DEFAULT_LIMIT
    here = os.path.dirname(os.path.abspath(__file__))
    bad = (chr(0x2013), chr(0x2014))
    for name in ('theater_marcus.py', 'theater_megaplex.py', 'test_theater_group_d.py'):
        text = open(os.path.join(here, name), encoding='utf-8').read()
        assert not any(b in text for b in bad), name + ' carries an en or em dash'
    print('contract ok')


def _forbid_network():
    """Any socket use fails, so a fixture that leaks to the network shows up as a failure."""
    import socket

    def refuse(*_a, **_k):
        raise RuntimeError('an offline test tried the network')
    socket.socket.connect = refuse
    socket.create_connection = refuse
    socket.getaddrinfo = refuse


if __name__ == '__main__':
    _forbid_network()
    test_megaplex_cinema_ids()
    test_megaplex_business_day()
    test_megaplex_rank_and_links()
    test_megaplex_fetch_end_to_end()
    test_megaplex_walled_without_donation_is_blocked_at_once()
    test_megaplex_walled_with_donation_reads_in_the_browser()
    test_megaplex_partial_read_says_so()
    test_megaplex_never_raises()
    test_marcus_script_order_and_login()
    test_marcus_day_rule()
    test_marcus_rows_and_rank()
    test_marcus_fetch_end_to_end()
    test_marcus_new_token_when_refused()
    test_marcus_one_read_per_film_when_schedule_call_fails()
    test_marcus_walled_without_donation_is_blocked_at_once()
    test_marcus_walled_with_donation_reads_in_the_browser()
    test_marcus_never_raises()
    test_words_in_the_data_are_not_a_wall()
    test_contract()
    print('ALL OK')
