"""
Offline checks for the theater chain cards, group B: Harkins Theatres,
Alamo Drafthouse, Emagine Theatres and Cinepolis. Small inline fixtures
in each chain's own shape; nothing here touches the network.

    PYTHONPATH=. python3 scripts/trends_scrapers/test_theater_group_b.py
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from scripts.trends_scrapers import theater_alamo as alamo
from scripts.trends_scrapers import theater_cinepolis as cinepolis
from scripts.trends_scrapers import theater_emagine as emagine
from scripts.trends_scrapers import theater_harkins as harkins
from scripts.trends_scrapers.theater_common import DEFAULT_LIMIT, rank_by_showtimes

DAY = '2026-11-23'
TOMORROW = '2026-11-24'
MODULES = (harkins, alamo, emagine, cinepolis)


# ---------------------------------------------------------------------------
# Harkins
# ---------------------------------------------------------------------------
def _hk_film(mid, title, runtime, released, directors=(), cast=(), children=(), slug=None):
    return {'movieId': mid, 'title': title, 'slugUrl': slug or mid.lower(),
            'runtime': runtime, 'releaseDate': f'{released}T00:00:00Z' if released else '',
            'directors': [{'item': d} for d in directors],
            'castMembers': [{'item': c} for c in cast],
            'childMoviesIds': list(children),
            'imgUrls': [{'imageType': 'FilmStill', 'url': f'https://img.example/{mid}-still.jpg'},
                        {'imageType': 'FilmPosterGraphic', 'url': f'https://img.example/{mid}.jpg'}]}


def _hk_perf(mid, sid, day=DAY):
    return {'movieId': mid, 'sessionId': sid, 'businessDate': f'{day}T00:00:00'}


def test_harkins():
    regions = [
        {'name': 'Arizona', 'theatres': [{'id': 65, 'slugUrl': 'flagstaff', 'name': 'Flagstaff'},
                                         {'id': 19, 'slugUrl': 'prescott-valley', 'name': 'Prescott Valley'}]},
        {'name': 'Oklahoma', 'theatres': [{'id': 27, 'slugUrl': 'bricktown', 'name': 'Bricktown'},
                                          {'id': 65, 'slugUrl': 'flagstaff', 'name': 'Flagstaff'}]},
    ]
    assert [t['id'] for t in harkins.parse_theaters(regions)] == ['65', '19', '27']
    assert harkins.parse_theaters(None) == [] and harkins.parse_theaters({'x': 1}) == []

    api = [
        _hk_film('HG', 'The Hunger Games: Sunrise on the Reaping', 158, '2026-11-20',
                 ['Francis Lawrence'], ['Jesse Plemons'], children=['HG3D'],
                 slug='the-hunger-games-sunrise-on-the-reaping'),
        _hk_film('HGES', 'Juegos del hambre: Amanecer en la cosecha[Espa\u00f1ol]', 158,
                 '2026-11-20', ['Francis Lawrence']),
        _hk_film('HGOC', 'On-Screen Captions: Hunger Games: Sunrise Reaping', 158,
                 '2026-11-20', cast=['Jesse Plemons']),
        _hk_film('HX', 'Hexed', 109, '2026-11-25', ['Josie Trinidad']),
        _hk_film('HXEV', 'Hexed: Spellbound Screening', 109, '2026-11-25', ['Josie Trinidad']),
        _hk_film('HXES', 'Hechizo: Bienvenidos a Hexe [doblado en Espa\u00f1ol]', 109,
                 '2026-11-25', ['Josie Trinidad']),
        _hk_film('DN', 'DUNE: Part Three', 150, '2026-12-18'),
        _hk_film('DNIS', 'DUNE: Part Three - Insider Screenings', 150, '2026-12-15', ['Denis Villeneuve']),
        _hk_film('DNES', 'DUNA: Parte Tres [doblado en Espa\u00f1ol]', 150, '2026-12-18', ['Denis Villeneuve']),
        _hk_film('BH', 'Bhogi [Telugu, subtitled]', 160, '2026-11-27', ['Sampath Nandi']),
        _hk_film('QN', 'Quiet Neighbor', 160, '2026-11-27'),
        _hk_film('OM', 'OM Chapter 1: Udhiram \u2013 The Blood Wood [Tamil]', 179, '2026-11-20'),
        _hk_film('RK', 'I Play Rocky', 128, '2026-11-20', ['Peter Farrelly']),
        _hk_film('EB', 'Ebenezer', 115, '2026-11-12', ['Scott Cooper']),
    ]
    next_data = {'props': {'pageProps': {'movieData': {'movieSort': {
        'nowShowing': [
            {'id': 'RK', 'title': 'I Play Rocky', 'slugUrl': 'i-play-rocky'},
            {'id': 'HG', 'title': 'The Hunger Games: Sunrise on the Reaping'},
            {'id': 'EB', 'title': 'Ebenezer', 'slugUrl': 'ebenezer'},
            {'id': 'LS', 'title': 'Only On The Page', 'slugUrl': 'only-on-the-page',
             'poster': 'https://img.example/LS-page.jpg'},
        ],
        'comingThisWeek': [{'id': 'RK', 'title': 'I Play Rocky'}],
    }}}}}
    html = ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(next_data) + '</script></body></html>')
    listing = harkins.parse_listing(html)
    assert [f['id'] for f in listing] == ['RK', 'HG', 'EB', 'LS']
    assert harkins.parse_listing('<html></html>') == []

    films = harkins.parse_films(api, listing)
    assert films['RK']['image'] == 'https://img.example/RK.jpg'
    assert films['LS']['image'] == 'https://img.example/LS-page.jpg'
    assert films['HG']['runtime'] == 158 and str(films['HG']['released']) == '2026-11-20'

    parents = harkins.film_parents(films)
    assert parents['HG3D'] == 'HG', parents
    assert parents['HGES'] == 'HG' and parents['HGOC'] == 'HG', parents
    assert parents['HXES'] == 'HX', parents            # the film, not its event screening
    assert parents['DNES'] == 'DN', parents            # exact day when the film lists no credits
    for fid in ('BH', 'HXEV', 'DNIS', 'OM', 'RK'):
        assert fid not in parents, (fid, parents)

    flagstaff = {'success': True, 'data': {'performances': [
        _hk_perf('HG', '1'), _hk_perf('HG', '2'), _hk_perf('HG', '2'),
        _hk_perf('HG3D', '3'), _hk_perf('HGES', '4'), _hk_perf('RK', '5'),
        _hk_perf('RK', '6', TOMORROW), _hk_perf('EB', '7'), _hk_perf('NOPE', '8'),
        _hk_perf('OM', '12'), {'movieId': None, 'sessionId': '13'},
    ]}}
    counts = harkins.count_performances(flagstaff, DAY)
    assert counts == {'HG': 2, 'HG3D': 1, 'HGES': 1, 'RK': 1, 'EB': 1, 'NOPE': 1, 'OM': 1}, counts
    assert harkins.count_performances({'success': True, 'data': {}}, DAY) == {}
    prescott = {'success': True, 'data': {'performances': [
        _hk_perf('RK', '9'), _hk_perf('EB', '10'), _hk_perf('HXES', '11')]}}
    per_theater = {'65': counts, '19': harkins.count_performances(prescott, DAY)}

    rows, unnamed = harkins.build_rows(films, per_theater, harkins.listing_order(listing, films), parents)
    assert unnamed == 1
    items = rank_by_showtimes(rows, limit=10)
    titles = [i['title'] for i in items]
    # Rocky and Ebenezer tie at 2; the chain's own listing puts Rocky
    # first, ahead of the A to Z order.
    assert titles == ['The Hunger Games: Sunrise on the Reaping', 'I Play Rocky', 'Ebenezer',
                      'Hexed', 'OM Chapter 1: Udhiram - The Blood Wood'], titles
    hg = items[0]
    assert hg['showtimes'] == 4 and hg['theaters'] == 1, hg
    assert hg['url'] == 'https://www.harkins.com/movies/the-hunger-games-sunrise-on-the-reaping'
    assert hg['image'] == 'https://img.example/HG.jpg'
    assert items[1]['theaters'] == 2 and items[3]['url'] == 'https://www.harkins.com/movies/hx'
    assert [i['rank'] for i in items] == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Alamo Drafthouse
# ---------------------------------------------------------------------------
def _ad_pres(slug, title, event=False, hidden=False):
    return {'slug': slug, 'isHidden': hidden, 'event': {'slug': slug} if event else None,
            'show': {'title': title, 'slug': slug,
                     'posterImages': [{'uri': f'https://img.example/{slug}.jpg'}]}}


def _ad_sess(cinema, sid, slug, day=DAY, status='ONSALE', hidden=False):
    return {'cinemaId': cinema, 'sessionId': sid, 'presentationSlug': slug,
            'businessDateClt': day, 'status': status, 'isHidden': hidden}


def test_alamo():
    picker = {'data': {'marketSummaries': [
        {'slug': 'austin', 'marketStatus': 'OPEN'},
        {'slug': 'nyc', 'marketStatus': 'OPEN'},
        {'slug': 'hollywood', 'marketStatus': 'PRE_OPEN', 'isHollywoodPromo': True},
        {'slug': 'naples', 'marketStatus': 'PRE_OPEN'},
        {'slug': 'austin', 'marketStatus': 'OPEN'},
    ]}}
    assert alamo.parse_markets(picker) == ['austin', 'nyc']
    assert alamo.parse_markets(None) == []

    national = {'data': {'presentations': [
        {'slug': 'wicked-for-good', 'show': {'slug': 'wicked-for-good'}},
        {'slug': 'hexed', 'show': {'slug': 'hexed'}},
        {'slug': 'elf-sing-along', 'show': {'slug': 'elf'}},
        {'slug': 'zeta', 'show': {'slug': 'zeta'}},
        {'slug': 'alpha', 'show': {'slug': 'alpha'}},
    ]}}
    order, slugs = alamo.parse_national(national)
    assert order['hexed'] == 1 and order['elf'] == 2 and 'elf-sing-along' in slugs

    austin = {'data': {
        'market': [{'cinemas': [{'id': '0001'}, {'id': '0002'}]}],
        'presentations': [_ad_pres('hexed', 'Hexed'),
                          _ad_pres('alamo-crafthouse-hexed', 'Hexed', event=True),
                          _ad_pres('secret', 'Secret Show', hidden=True),
                          _ad_pres('zeta', 'Zeta'), _ad_pres('alpha', 'Alpha'),
                          _ad_pres('point-blank-night', 'Point Blank (1967)', event=True)],
        'sessions': [_ad_sess('0001', 's1', 'hexed'),
                     _ad_sess('0001', 's2', 'hexed', status='PAST'),
                     _ad_sess('0002', 's3', 'alamo-crafthouse-hexed'),
                     _ad_sess('0002', 's4', 'hexed', status='SOLDOUT'),
                     _ad_sess('0002', 's5', 'hexed', status='CANCELLED'),
                     _ad_sess('0002', 's6', 'hexed', hidden=True),
                     _ad_sess('0002', 's7', 'hexed', day=TOMORROW),
                     _ad_sess('0001', 's8', 'secret'),
                     _ad_sess('0001', 's9', 'zeta'),
                     _ad_sess('0002', 's10', 'alpha'),
                     _ad_sess('0001', 's11', 'point-blank-night'),
                     _ad_sess('0001', 's12', 'nowhere')],
    }}
    nyc = {'data': {
        'market': [{'cinemas': [{'id': '2103'}]}],
        'presentations': [_ad_pres('hexed', 'Hexed'),
                          _ad_pres('elf-sing-along', 'Elf', event=True)],
        'sessions': [_ad_sess('2103', 'n1', 'hexed'),
                     _ad_sess('2103', 'n2', 'elf-sing-along', day='2026-11-30'),
                     _ad_sess('0001', 's1', 'hexed')],
    }}
    a = alamo.parse_market(austin, DAY)
    assert len(a['sessions']) == 7 and a['cinemas'] == {'0001', '0002'}, a['sessions']
    n = alamo.parse_market(nyc, DAY)
    assert alamo.parse_market({'data': {}}, DAY) is None and alamo.parse_market(None, DAY) is None

    items = rank_by_showtimes(alamo.build_rows({'austin': a, 'nyc': n}, order, slugs), limit=10)
    titles = [i['title'] for i in items]
    # Zeta and Alpha tie at 1; the national listing puts Zeta first.
    assert titles == ['Hexed', 'Zeta', 'Alpha', 'Point Blank (1967)'], titles
    hexed = items[0]
    assert hexed['showtimes'] == 5 and hexed['theaters'] == 3, hexed
    assert hexed['url'] == 'https://drafthouse.com/show/hexed'
    assert hexed['image'] == 'https://img.example/hexed.jpg'
    assert items[3]['url'] == 'https://drafthouse.com/austin/event/point-blank-night'
    assert alamo.film_url('elf-sing-along', True) == 'https://drafthouse.com/event/elf-sing-along'


# ---------------------------------------------------------------------------
# Emagine
# ---------------------------------------------------------------------------
def _em_movie(mid, title):
    return {'id': mid, 'title': title,
            'permalink': f'https://www.emagine-entertainment.com/movies/m{mid}/',
            'poster': f'https://www.emagine-entertainment.com/wp-content/uploads/p{mid}.jpg'}


def _em_sess(sid, day=DAY):
    return {'vista_session_id': sid, 'business_date': day, 'showtime': f'{day} 19:00'}


def test_emagine():
    listed = {'status': 'success', 'data': [
        {'id': 3671, 'title': 'Emagine Novi', 'permalink': 'https://www.emagine-entertainment.com/theatres/emagine-novi/'},
        {'id': 3664, 'title': 'Emagine Delano'},
        {'id': 3671, 'title': 'Emagine Novi'},
    ]}
    assert [t['id'] for t in emagine.parse_theaters(listed)] == ['3671', '3664']
    assert emagine.parse_theaters(None) == []

    novi = {'status': 'success', 'data': {
        'movies': {'1': _em_movie(1, 'The Hunger Games: Sunrise on the Reaping'),
                   '2': _em_movie(2, 'Error_MovieTitle_Unknown - Nov. 23'),
                   '3': _em_movie(3, 'White Christmas {1954} (Dementia Friendly)'),
                   '4': _em_movie(4, 'White Christmas {1954}'),
                   '5': _em_movie(5, 'Ebenezer'),
                   '6': _em_movie(6, 'Clayface')},
        'sessions_by_theatre': {'3671': {'theatre_id': 3671, 'business_dates': {
            DAY: {'1': {'movie_id': 1, 'groups': [
                      {'version_id': 10, 'sessions': [_em_sess('a'), _em_sess('b')]},
                      {'version_id': 11, 'sessions': [_em_sess('c'), _em_sess('c')]}]},
                  '2': {'movie_id': 2, 'groups': [{'sessions': [_em_sess(x) for x in 'defgh']}]},
                  '3': {'movie_id': 3, 'groups': [{'sessions': [_em_sess('i')]}]},
                  '4': {'movie_id': 4, 'groups': [{'sessions': [_em_sess('j')]}]},
                  '5': {'movie_id': 5, 'groups': [{'sessions': [_em_sess('k')]}]},
                  '6': {'movie_id': 6, 'groups': [{'sessions': [_em_sess('l')]}]}},
            TOMORROW: {'5': {'movie_id': 5, 'groups': [{'sessions': [_em_sess('m', TOMORROW)]}]}},
        }}}}}
    counts, films = emagine.parse_sessions(novi, '3671', DAY)
    assert counts == {'1': 3, '2': 5, '3': 1, '4': 1, '5': 1, '6': 1}, counts
    delano = {'data': {'movies': {}, 'sessions_by_theatre': {'3664': {'business_dates': {}}}}}
    assert emagine.parse_sessions(delano, '3664', DAY) == ({}, {})
    assert emagine.parse_sessions({'data': {}}, '1', DAY) is None

    items = rank_by_showtimes(emagine.build_rows({'3671': (counts, films), '3664': ({}, {})}), limit=10)
    titles = [i['title'] for i in items]
    assert titles == ['The Hunger Games: Sunrise on the Reaping', 'White Christmas (1954)',
                      'Clayface', 'Ebenezer'], titles
    assert items[0]['showtimes'] == 3 and items[0]['theaters'] == 1
    assert items[0]['url'] == 'https://www.emagine-entertainment.com/movies/m1/'
    assert items[0]['image'].endswith('/p1.jpg')
    assert items[1]['showtimes'] == 2


# ---------------------------------------------------------------------------
# Cinepolis
# ---------------------------------------------------------------------------
def _cp_show(sid, name, slug, order, published=True, private=False, poster='key1'):
    return {'id': sid, 'time': '2026-11-23T20:00:00Z', 'published': published, 'private': private,
            'movie': {'id': 'm-' + slug, 'name': name, 'urlSlug': slug,
                      'posterImage': poster, 'displayOrder': order}}


def test_cinepolis():
    circuit = {'data': {'currentCircuit': {'id': '89', 'sites': [
        {'id': '132', 'name': 'NW Austin', 'hostname': 'https://www.cinepolisusa.com/nwaustin',
         'isLibrary': False, 'city': 'Austin', 'deletedAt': None},
        {'id': '134', 'name': 'LIBRARY', 'hostname': 'https://thisisnothing.com', 'isLibrary': True},
        {'id': '136', 'name': 'Test Lab', 'hostname': 'https://test-lab.netlify.app',
         'isLibrary': False, 'city': 'Austin'},
        {'id': '156', 'name': 'Coconut Grove', 'hostname': 'https://www.cinepolisusa.com/coconut/',
         'isLibrary': False, 'city': 'Miami'},
        {'id': '175', 'name': 'Cinepolis USA', 'hostname': 'https://www.cinepolisusa.com',
         'isLibrary': False, 'city': None},
        {'id': '199', 'name': 'Gone', 'hostname': 'https://www.cinepolisusa.com/gone',
         'isLibrary': False, 'city': 'Nowhere', 'deletedAt': '2026-01-01T00:00:00Z'},
    ]}}}
    sites = cinepolis.parse_sites(circuit)
    assert [s['id'] for s in sites] == ['132', '156'], sites
    assert sites[1]['url'] == 'https://www.cinepolisusa.com/coconut'
    assert cinepolis.parse_sites({'errors': [{'message': 'x'}]}) == []

    austin = {'data': {'showingsForDate': {'count': 8, 'data': [
        _cp_show('1', 'The Hunger Games: Sunrise on the Reaping', 'hunger', 3),
        _cp_show('2', 'The Hunger Games: Sunrise on the Reaping', 'hunger', 3),
        _cp_show('2', 'The Hunger Games: Sunrise on the Reaping', 'hunger', 3),
        _cp_show('3', 'Ramayana (Hindi with English Subtitles)', 'ramayana-hindi', 12),
        _cp_show('4', 'Private Party', 'party', 1, private=True),
        _cp_show('5', 'Unpublished', 'draft', 1, published=False),
        _cp_show('6', 'Ebenezer: A Christmas Carol', 'ebenezer', 2),
        _cp_show('7', 'Clayface', 'clayface', 6),
    ]}}}
    coconut = {'data': {'showingsForDate': {'count': 4, 'data': [
        _cp_show('11', 'The Hunger Games: Sunrise on the Reaping', 'the-hunger-games', 1, poster='key2'),
        _cp_show('12', 'The Hunger Games: Sunrise on the Reaping', 'the-hunger-games', 1, poster='key2'),
        _cp_show('13', 'The Hunger Games: Sunrise on the Reaping', 'the-hunger-games', 1, poster='key2'),
        _cp_show('14', 'Ramayana', 'ramayana', 9),
    ]}}}
    a, cut = cinepolis.parse_showings(austin)
    assert len(a) == 5 and cut is False, a
    c, _ = cinepolis.parse_showings(coconut)
    assert cinepolis.parse_showings({'errors': [{'message': 'nope'}]}) is None
    full = {'data': {'showingsForDate': {'count': 300, 'data': [
        _cp_show(str(i), 'Film', 'film', 1) for i in range(cinepolis.RESULT_CAP)]}}}
    assert cinepolis.parse_showings(full)[1] is True

    items = rank_by_showtimes(cinepolis.build_rows({'132': (sites[0], a), '156': (sites[1], c)}), limit=10)
    titles = [i['title'] for i in items]
    # Ebenezer and Clayface tie at 1; Ebenezer sits higher in the
    # chain's own display order, ahead of A to Z.
    assert titles == ['The Hunger Games: Sunrise on the Reaping', 'Ramayana',
                      'Ebenezer: A Christmas Carol', 'Clayface'], titles
    hg = items[0]
    assert hg['showtimes'] == 5 and hg['theaters'] == 2, hg
    assert hg['url'] == 'https://www.cinepolisusa.com/coconut/movie/the-hunger-games', hg['url']
    assert hg['image'].startswith('https://indy-systems.imgix.net/key2?'), hg['image']
    assert items[1]['showtimes'] == 2 and items[1]['theaters'] == 2


# ---------------------------------------------------------------------------
# Contract: constants, never raising, the blocked flag
# ---------------------------------------------------------------------------
class _Patch:
    def __init__(self, module, **attrs):
        self.module, self.attrs, self.saved = module, attrs, {}

    def __enter__(self):
        for k, v in self.attrs.items():
            self.saved[k] = getattr(self.module, k)
            setattr(self.module, k, v)

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(self.module, k, v)


def _boom(*_a, **_k):
    raise RuntimeError('connection reset')


def _no_browser(day):
    raise AssertionError('an offline test must not open a browser')


def test_contract():
    logging.disable(logging.WARNING)
    try:
        _contract_checks()
    finally:
        logging.disable(logging.NOTSET)


def _contract_checks():
    expected = {harkins: ('harkins', 'Harkins Theatres'), alamo: ('alamo', 'Alamo Drafthouse'),
                emagine: ('emagine', 'Emagine Theatres'), cinepolis: ('cinepolis', 'Cin\u00e9polis')}
    for mod, (key, label) in expected.items():
        assert (mod.KEY, mod.LABEL, mod.COOKIE_DOMAIN) == (key, label, None), mod.__name__
        assert mod.fetch.__defaults__ == (DEFAULT_LIMIT,), mod.__name__
        for word in ('scrape', 'modeled', 'synthetic', 'estimated'):
            assert word not in mod.LABEL.lower()

    for mod in (harkins, alamo, emagine):
        with _Patch(mod, get_json=_boom):
            items, meta = mod.fetch()
        assert items == [] and meta['theaters'] == 0 and meta['date'] and meta.get('reason'), meta
    with _Patch(cinepolis, _read_direct=_boom, _read_in_browser=_no_browser):
        items, meta = cinepolis.fetch()
    assert items == [] and meta.get('reason') and not meta.get('headless'), meta

    def _no_curl(*_a, **_k):
        raise cinepolis._NoClient('curl_cffi is not installed')
    with _Patch(cinepolis, _read_direct=_no_curl, _read_in_browser=lambda day: ([], {})):
        items, meta = cinepolis.fetch()
    assert items == [] and meta['blocked'] is False and meta.get('headless'), meta

    # Nothing answers: an empty card with a plain reason, not an error.
    for mod in (harkins, emagine):
        with _Patch(mod, get_json=lambda *a, **k: None, _walled=lambda *a, **k: True):
            items, meta = mod.fetch()
        assert items == [] and meta['blocked'] is True and meta['reason'], meta
    with _Patch(alamo, get_json=lambda *a, **k: None, _walled=lambda *a, **k: True):
        items, meta = alamo.fetch()
    assert items == [] and meta['blocked'] is True and meta['markets_listed'] == len(alamo.FALLBACK_MARKETS)

    def _walled(*_a, **_k):
        raise cinepolis._Walled('answered 403')
    with _Patch(cinepolis, _read_direct=_walled, _read_in_browser=lambda day: ([], {})):
        items, meta = cinepolis.fetch()
    assert items == [] and meta['blocked'] is True and meta.get('headless'), meta

    # One theater unread: the card still ships and says so.
    sites = [{'id': '132', 'name': 'NW Austin', 'url': 'https://www.cinepolisusa.com/nwaustin'},
             {'id': '156', 'name': 'Coconut Grove', 'url': 'https://www.cinepolisusa.com/coconut'}]
    one = ([{'title': 'Clayface', 'slug': 'clayface', 'poster': '', 'order': 1}], False)
    with _Patch(cinepolis, _read_direct=lambda day: (sites, {'132': one}),
                _read_in_browser=_no_browser):
        items, meta = cinepolis.fetch()
    assert [i['title'] for i in items] == ['Clayface'] and meta['theaters'] == 1
    assert meta['theaters_listed'] == 2 and '1 of 2' in meta['reason'], meta


def test_no_long_dashes():
    here = Path(__file__).resolve().parent
    for name in ('theater_harkins.py', 'theater_alamo.py', 'theater_emagine.py',
                 'theater_cinepolis.py', Path(__file__).name):
        text = (here / name).read_text(encoding='utf-8')
        assert '\u2013' not in text and '\u2014' not in text, name


def main() -> None:
    for test in (test_harkins, test_alamo, test_emagine, test_cinepolis,
                 test_contract, test_no_long_dashes):
        test()
        print(f'ok  {test.__name__}')
    print('ALL OK')


if __name__ == '__main__':
    main()
