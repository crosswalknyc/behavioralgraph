"""Offline tests for the Malco, Santikos, Studio Movie Grill and Galaxy
theater cards (theater_malco.py, theater_santikos.py, theater_smg.py,
theater_galaxy.py). Small inline fixtures shaped like each site's own
pages; no network.

    PYTHONPATH=. python3 scripts/trends_scrapers/test_theater_group_c.py
"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

from scripts.trends_scrapers import theater_galaxy as galaxy
from scripts.trends_scrapers import theater_malco as malco
from scripts.trends_scrapers import theater_santikos as santikos
from scripts.trends_scrapers import theater_smg as smg
from scripts.trends_scrapers.theater_common import rank_by_showtimes

TODAY = '2026-11-23'
WALL = ('<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>'
        '</head><body>checking your browser</body></html>')
MODULES = (malco, santikos, smg, galaxy)


def ranked(mod, prints, limit=30):
    return rank_by_showtimes(mod._film_rows(prints), limit=limit)


# ---------------------------------------------------------------------------
# Malco
# ---------------------------------------------------------------------------
MALCO_HOME = """
<select name="locationsMenu" id="locationsMenuSelect">
  <option value="">Select a location</option>
  <optgroup label="Memphis">
    <option value="paradiso-cinema-grill-and-imax">Paradiso Cinema Grill &amp; IMAX</option>
    <option value="wolfchase-cinema-grill">Wolfchase Cinema Grill</option>
  </optgroup>
  <option value="paradiso-cinema-grill-and-imax">Paradiso Cinema Grill &amp; IMAX</option>
</select>
<a href="/cinemas/smyrna-cinema">Smyrna</a>
<a href="/cinemas/paradiso-cinema-grill-and-imax/2026-11-24/">another day</a>
<select name="nowplayingMenu" id="nowplayingMenuSelect">
  <option value="">Select a Film</option>
  <option value="focker-in-law">Focker-in-Law</option>
  <option value="hunger-games-sunrise-on-the-reaping">Hunger Games</option>
  <option value="hunger-games-sunrise-on-the-reaping-imax">Hunger Games (IMAX)</option>
  <option value="hunger-games-sunrise-on-the-reaping-mxt-atmos">Hunger Games (MXT-Atmos)</option>
  <option value="the-cat-in-the-hat-2026">The Cat in the Hat - 2026</option>
  <option value="wicker">Wicker</option>
</select>
"""

MALCO_CINEMA = """
<div class="dateSelection"><small>Showtimes for Mon November 23</small></div>
<div class="films" style="">
  <div class="film" id="hunger-games-sunrise-on-the-reaping-imax">
    <div class="filmInfo"><div class="titleWrapper">
      <h3 class="title">Hunger Games: Sunrise on the Reaping (IMAX)</h3></div>
      <div class="poster"><div class="posterWrapper"><picture>
        <source type="image/webp" srcset="/assets/images/imax-150.webp 150w">
        <img src="/assets/images/imax-300.webp" width="300" alt="poster"></picture></div></div>
      <div class="showtimes"><div class="times">
        <a class="time" data-datetime="2026-11-23 15:20:00" href="https://tickets.malco.com/a">
          <span>3:20pm</span><div class="features"><img src="/assets/images/icon-75.webp"></div></a>
        <a class="time" data-datetime="2026-11-23 19:00:00" href="https://tickets.malco.com/b"></a>
        <a class="time" data-datetime="2026-11-24 13:00:00" href="https://tickets.malco.com/c"></a>
      </div></div>
    </div>
  </div>
  <div class="film" id="hunger-games-sunrise-on-the-reaping-mxt-atmos">
    <h3 class="title">Hunger Games: Sunrise on the Reaping (MXT-Atmos)</h3>
    <div class="showtimes"><img src="/assets/images/icon-75.webp">
      <a class="time" data-datetime="2026-11-23 20:00:00" href="https://tickets.malco.com/d"></a></div>
  </div>
  <div class="film" data-id="not-this-one" id="hunger-games-sunrise-on-the-reaping">
    <h3 class="title">Hunger Games: Sunrise on the Reaping</h3>
    <div class="poster"><img src="/assets/images/plain-300.webp"></div>
    <div class="showtimes"><div class="times">
      <a class="time" data-datetime="2026-11-23 13:00:00" href="e"></a>
      <a class="time" data-datetime="2026-11-23 16:00:00" href="f"></a>
      <span class="time soldout" data-datetime="2026-11-23 19:30:00">7:30pm</span></div></div>
  </div>
  <div class="film" id="the-cat-in-the-hat-2026">
    <h3 class="title">The Cat in the Hat - 2026</h3>
    <div class="showtimes"><a class="time" href="g"><span>4:00pm</span></a></div>
  </div>
  <div class="film" id="wicker">
    <h3 class="title">Wicker</h3>
    <div class="showtimes"><a class="time" data-datetime="2026-11-24 18:00:00" href="h"></a></div>
  </div>
</div>
<script>
    const thisCinemaName = "paradiso-cinema-grill-and-imax";
    const pageDate = "2026-11-23";
</script>
<div class="featureDescriptions"><img src="/assets/images/feature-150.webp">
  <a class="time" data-datetime="2026-11-23 10:00:00"></a></div>
"""

MALCO_EMPTY = """
<div class="films" style="">
  <div class="no-showtimes"><p>There are no showtimes available for the selected date.</p></div>
</div>
<script>
    const thisCinemaName = "wolfchase-cinema-grill";
    const pageDate = "2026-11-23";
</script>
"""


class MalcoTests(unittest.TestCase):
    def test_cinema_list_from_menu_and_links(self):
        self.assertEqual(malco.parse_cinemas(MALCO_HOME),
                         ['paradiso-cinema-grill-and-imax', 'wolfchase-cinema-grill',
                          'smyrna-cinema'])
        order = malco.parse_film_order(MALCO_HOME)
        self.assertEqual(order['focker-in-law'], 0)
        self.assertLess(order['hunger-games-sunrise-on-the-reaping'], order['wicker'])

    def test_cinema_page(self):
        day, films = malco.parse_cinema_page(MALCO_CINEMA, TODAY)
        self.assertEqual(day, TODAY)
        by_id = {f['slug']: f for f in films}
        self.assertEqual(len(films), 5)
        imax = by_id['hunger-games-sunrise-on-the-reaping-imax']
        self.assertEqual(imax['showtimes'], 2)                 # tomorrow's link not counted
        self.assertEqual(imax['image'], 'https://www.malco.com/assets/images/imax-300.webp')
        # No poster block: a showtime feature icon is never taken as the poster.
        self.assertEqual(by_id['hunger-games-sunrise-on-the-reaping-mxt-atmos']['image'], '')
        self.assertEqual(by_id['the-cat-in-the-hat-2026']['showtimes'], 1)   # undated link, today's page
        self.assertEqual(by_id['hunger-games-sunrise-on-the-reaping']['showtimes'], 3)  # sold out counts
        self.assertEqual(by_id['wicker']['showtimes'], 0)
        self.assertEqual(malco.parse_cinema_page(MALCO_EMPTY, TODAY), (TODAY, []))

    def test_titles(self):
        self.assertEqual(malco.prepare_title('Hunger Games: Sunrise on the Reaping (MXT-Atmos)'),
                         ('Hunger Games: Sunrise on the Reaping', False))
        self.assertEqual(malco.prepare_title('I Play Rocky (Dolby Atmos)'),
                         ('I Play Rocky', False))
        self.assertEqual(malco.prepare_title('The Cat in the Hat - 2026'),
                         ('The Cat in the Hat (2026)', True))
        self.assertEqual(malco.prepare_title('Clayface'), ('Clayface', True))
        title, plain = malco.prepare_title('Hunger Games: Sunrise on the Reaping (IMAX)')
        self.assertFalse(plain)

    def test_rank_merges_formats_under_the_plain_print(self):
        pages = {'paradiso-cinema-grill-and-imax': MALCO_CINEMA,
                 'wolfchase-cinema-grill': MALCO_EMPTY,
                 'smyrna-cinema': None, 'walled-cinema': WALL}
        prints, stats = malco.build_prints(pages, TODAY, malco.parse_film_order(MALCO_HOME))
        self.assertEqual(stats, {'read': 2, 'showing': 1})
        items = ranked(malco, prints)
        self.assertEqual([i['title'] for i in items],
                         ['Hunger Games: Sunrise on the Reaping', 'The Cat in the Hat (2026)'])
        hg = items[0]
        self.assertEqual((hg['rank'], hg['showtimes'], hg['theaters']), (1, 6, 1))
        self.assertEqual(hg['url'], 'https://www.malco.com/nowplaying/hunger-games-sunrise-on-the-reaping/')
        self.assertEqual(hg['image'], 'https://www.malco.com/assets/images/plain-300.webp')


# ---------------------------------------------------------------------------
# Santikos
# ---------------------------------------------------------------------------
SANTIKOS_PAGE = """
<script>
  var movie_list_url = "https://www.santikos.com/get-movie-lists";
  HEADER_MOVIE_SESSIONS = [];
  LOCATION_JSON = [{"id":28,"cinema_id":"27","url":"https:\\/\\/www.santikos.com\\/go-to-location?loc=the-grand-winston-salem","cinema_name":"The Grand Winston-Salem","image":"x"},{"id":34,"cinema_id":"4","cinema_name":"Santikos Silverado"},{"id":28,"cinema_name":"again"}];
  var later = [1, 2];
</script>
"""


def _full(day, time='19:00:00', **extra):
    s = {'SessionId': f'{day}{time}', 'Showtime': f'{day}T{time}',
         'SessionBusinessDate': f'{day}T00:00:00', 'date_f': day,
         'IsPublicScreening': True, 'Status': 'Open'}
    s.update(extra)
    return s


def _short(day, time='21:00:00'):
    return {'SessionId': f's{day}{time}', 'Showtime': f'{day}T{time}', 'date_f': day}


def _film(fid, title, slug, sessions, **extra):
    f = {'ScheduledFilmId': fid, 'Title': title, 'dont_show': 0,
         'booking_url': f'https://www.santikos.com/movie-info/the-grand-winston-salem/{slug}',
         'image_url': f'https://sanaz-web01.santikos.com/CDN/media/entity/get/FilmPosterGraphic/{fid}?height=500',
         'Sessions': sessions}
    f.update(extra)
    return f


SANTIKOS_28 = json.dumps({'data': [
    _film('HO1', 'The Hunger Games: Sunrise on the Reaping', 'the-hunger-games-sunrise-on-the-reaping',
          [_full(TODAY), _full(TODAY, '21:30:00'), _full('2026-11-24'),
           _full(TODAY, '10:00:00', IsPublicScreening=False), _short(TODAY)]),
    _film('HO2', 'Hunger Games: Sunrise on the Reaping (Spanish Sub)',
          'hunger-games-sunrise-on-the-reaping-spanish-sub', [_full(TODAY)]),
    _film('HO3', 'Heart of the Beast (Final Cut)', 'heart-of-the-beast-final-cut',
          [_full(TODAY), _full(TODAY, '20:00:00')]),
    _film('HO4', 'Elf - Rewind', 'elf---rewind', [_short('2026-12-09')]),
    _film('HO5', 'Hidden Title', 'hidden-title', [_full(TODAY)], dont_show=1),
]})
SANTIKOS_34 = json.dumps({'data': [
    _film('HO3', 'Heart of the Beast (Final Cut)', 'heart-of-the-beast-final-cut', [_full(TODAY)]),
    _film('HO1', 'The Hunger Games: Sunrise on the Reaping', 'the-hunger-games-sunrise-on-the-reaping',
          [_full(TODAY)]),
]})


class SantikosTests(unittest.TestCase):
    def test_locations(self):
        self.assertEqual(santikos.parse_locations(SANTIKOS_PAGE),
                         [{'id': '28', 'name': 'The Grand Winston-Salem'},
                          {'id': '34', 'name': 'Santikos Silverado'}])
        self.assertEqual(santikos.parse_locations('<html>no list</html>'), [])

    def test_movie_list(self):
        films = {f['film_id']: f for f in santikos.parse_movie_list(SANTIKOS_28, TODAY)}
        self.assertNotIn('HO5', films)                         # hidden on the site
        hg = films['HO1']
        self.assertEqual(hg['showtimes'], 3)                   # both session shapes, no private show
        self.assertEqual(hg['slug'], 'the-hunger-games-sunrise-on-the-reaping')
        self.assertTrue(hg['image'].startswith('https://sanaz-web01.santikos.com/'))
        self.assertEqual(films['HO4']['showtimes'], 0)
        self.assertIsNone(santikos.parse_movie_list(WALL, TODAY))
        self.assertIsNone(santikos.parse_movie_list('{"error": "x"}', TODAY))

    def test_program_labels(self):
        self.assertEqual(santikos.prepare_title('Heart of the Beast (Final Cut)'),
                         ('Heart of the Beast', False))
        self.assertEqual(santikos.prepare_title('Elf - Rewind'), ('Elf', False))
        self.assertEqual(santikos.prepare_title('Ghost Soldier'), ('Ghost Soldier', True))

    def test_rank_folds_the_stray_print(self):
        prints, stats = santikos.build_prints({'28': SANTIKOS_28, '34': SANTIKOS_34, '13': WALL},
                                              TODAY)
        self.assertEqual(stats, {'read': 2, 'showing': 2})
        items = ranked(santikos, prints)
        self.assertEqual([(i['title'], i['showtimes'], i['theaters']) for i in items],
                         [('The Hunger Games: Sunrise on the Reaping', 5, 2),
                          ('Heart of the Beast', 3, 2)])
        self.assertEqual(items[0]['url'],
                         'https://www.santikos.com/movie-info/the-hunger-games-sunrise-on-the-reaping')
        self.assertEqual(items[1]['url'],
                         'https://www.santikos.com/movie-info/heart-of-the-beast-final-cut')


# ---------------------------------------------------------------------------
# Studio Movie Grill
# ---------------------------------------------------------------------------
SMG_FIRST = """
<ul class="theater-picker-list">
  <li class="theater-picker-item is-current has-no-movie-showtimes" data-url="/quicktickets/california/simi-valley" data-search="simi valley california" data-has-showtimes="false">
    <a href="/quicktickets/california/simi-valley" class="theater-picker-link" data-url="/quicktickets/california/simi-valley" data-theater-id="62">
      <span class="theater-picker-main"><span class="theater-picker-name">Simi Valley</span></span></a>
  </li>
  <li class="theater-picker-item  has-no-movie-showtimes" data-url="/quicktickets/texas/dallas-royal-ln" data-has-showtimes="false">
    <a href="/quicktickets/texas/dallas-royal-ln" class="theater-picker-link" data-theater-id="6">
      <span class="theater-picker-name">Dallas, Royal Lane</span></a>
  </li>
</ul>
"""


def _smg_item(slug, title, times, img='https://cdn.studiomoviegrill.com/content/x.jpg'):
    links = ''.join(f'<li><a href="/ticketing/select/{slug}/simi-valley/{t}" class="button tiny">x</a></li>'
                    for t in times)
    return (f'<li>\n  <div class="movie-poster"><a href="/ticketing/start/{slug}">'
            f'<img src="{img}" alt="{title} Movie Poster" /></a></div>'
            f'<div class="movie-info"><h2 class="movie-title">{title}</h2>'
            f'<ul class="movie-times">{links}</ul></div></li>')


SMG_DAY = ('<div id="movie-grid" class="with-info with-times"><ul class="small-block-grid-1">'
           + _smg_item('hunger-games-sunrise-on-the-reaping',
                       'The Hunger Games: Sunrise on the Reaping (PG-13)',
                       ['2026/11/23/12/30/pm', '2026/11/23/07/00/pm', '2026/11/24/01/00/pm'],
                       img='https://cdn.studiomoviegrill.com/content/hg.jpg')
           + _smg_item('open-caption-hunger-games-sunrise-on-the-reaping',
                       'Open Caption Hunger Games: Sunrise on the Reaping (PG-13)',
                       ['2026/11/23/04/15/pm'])
           + _smg_item('special-needs-cat-in-the-hat', 'Special Needs: Cat in the Hat (TBD)',
                       ['2026/11/23/10/00/am'])
           + _smg_item('the-cat-in-the-hat', 'The Cat in the Hat (PG)', ['2026/11/23/03/30/pm'],
                       img='https://cdn.studiomoviegrill.com/content/cat.jpg')
           + _smg_item('violent-night-2', 'Violent Night 2 (R)', ['2026/12/03/07/00/pm'])
           + '</ul></div><!-- /.movie-grid -->'
           '<footer><a href="/ticketing/select/x/simi-valley/2026/11/23/01/00/pm">x</a></footer>')


class SmgTests(unittest.TestCase):
    def test_theater_list(self):
        self.assertEqual(smg.parse_theaters(SMG_FIRST), [
            {'id': '62', 'path': '/quicktickets/california/simi-valley', 'name': 'Simi Valley'},
            {'id': '6', 'path': '/quicktickets/texas/dallas-royal-ln', 'name': 'Dallas, Royal Lane'}])
        self.assertEqual(smg.parse_theaters('<a href="/quicktickets/texas/plano">Plano</a>'),
                         [{'id': '/quicktickets/texas/plano', 'path': '/quicktickets/texas/plano',
                           'name': ''}])
        geo = '[{"Id":82,"Url":"/quicktickets/california/bakersfield","Name":"Bakersfield"}]'
        self.assertEqual(smg.parse_theater_geo(geo), [
            {'id': '82', 'path': '/quicktickets/california/bakersfield', 'name': 'Bakersfield'}])
        self.assertEqual(smg.day_path('/quicktickets/texas/plano', '2026-12-01'),
                         'https://www.studiomoviegrill.com/quicktickets/texas/plano/2026/12/1')

    def test_day_page(self):
        films = {f['slug']: f for f in smg.parse_day_page(SMG_DAY, TODAY)}
        self.assertEqual(films['hunger-games-sunrise-on-the-reaping']['showtimes'], 2)
        self.assertEqual(films['hunger-games-sunrise-on-the-reaping']['image'],
                         'https://cdn.studiomoviegrill.com/content/hg.jpg')
        self.assertEqual(films['violent-night-2']['showtimes'], 0)
        self.assertIsNone(smg.parse_day_page(WALL, TODAY))
        order = smg.parse_film_order('<a href="/ticketing/start/hexed">h</a>'
                                     '<a href="/ticketing/start/the-cat-in-the-hat">c</a>')
        self.assertEqual(order, {'hexed': 0, 'the-cat-in-the-hat': 1})

    def test_titles(self):
        self.assertEqual(smg.prepare_title('Ghost Soldier (PG-13)'), ('Ghost Soldier', True))
        self.assertEqual(smg.prepare_title('Open Caption Hunger Games: Sunrise on the Reaping (PG-13)'),
                         ('Hunger Games: Sunrise on the Reaping', False))
        self.assertEqual(smg.prepare_title('Special Needs: Cat in the Hat (TBD)'),
                         ('Cat in the Hat', False))

    def test_rank_folds_open_caption_and_special_needs(self):
        prints, stats = smg.build_prints({'62': SMG_DAY, '6': SMG_DAY, '9': None}, TODAY, {})
        self.assertEqual(stats, {'read': 2, 'showing': 2})
        items = ranked(smg, prints)
        self.assertEqual([(i['title'], i['showtimes'], i['theaters']) for i in items],
                         [('The Hunger Games: Sunrise on the Reaping', 6, 2),
                          ('The Cat in the Hat', 4, 2)])
        self.assertEqual(items[0]['url'],
                         'https://www.studiomoviegrill.com/ticketing/start/hunger-games-sunrise-on-the-reaping')
        self.assertEqual(items[1]['image'], 'https://cdn.studiomoviegrill.com/content/cat.jpg')


# ---------------------------------------------------------------------------
# Galaxy
# ---------------------------------------------------------------------------
GALAXY_LIST = """
<nav><a class="nav__a nav__a--subnav nav__a--locationNav" href="/movie-theater/tucson">Tucson</a>
<a class="nav__a" href="/movie-theater/cannery">Cannery</a>
<a class="nav__a" href="/movie-theater/cannery">Cannery</a>
<a href="/movie-theater/cannery/thank-you">thanks</a></nav>
<section id="playingAll">
  <a href="/movie/SWAN-LAKE-2027" class="allMovies__item">Swan Lake</a>
  <a href="/movie/EBENEZER" class="allMovies__item">Ebenezer</a>
  <a href="/movie/THE-CAT-IN-THE-HAT" class="allMovies__item">Cat</a>
  <a href="/movie/THE-HUNGER-GAMES-SUNRISE-ON-THE-REAPING" class="allMovies__item">HG</a>
</section>
"""


def _galaxy_item(slug, title, times, poster='', sold_out=0):
    pic = (f'<div class="moviePoster nowPlaying__poster "><picture>'
           f'<source data-srcset="{poster}" /><img data-src="{poster}" class="lazyload" /></picture></div>'
           if poster else '')
    shows = ''.join(f'<div class="performance__item"><a class="button button--showtime" href="#">'
                    f'<span>{t}</span></a></div>' for t in times)
    shows += '<div class="button button--showtime button--soldOut"><span>9:45 PM</span></div>' * sold_out
    return (f'<div class="nowPlaying__item " data-link="/movie/Cannery/{slug}">{pic}'
            f'<div class="nowPlaying__item--background"><img data-src="{poster}?big" /></div>'
            f'<h3 class="nowPlaying__movieTitle" data-title="WRONG" title="{title}" role="heading" aria-level="2">'
            f'<a href="/movie/Cannery/{slug}"><span><strong>{title}</strong></span></a></h3>'
            f'<div class="nowPlaying__showtimes ">{shows}</div></div>')


def _galaxy_page(day, items):
    return (f'<div class="dates__item active " data-date="{day}" role="listitem"></div>'
            '<div class="dates__item  " data-date="11/24/2026" role="listitem"></div>'
            '<div class="showtimeLegend"><a class="button button--showtime" href="#">7:00 PM</a></div>'
            '<div id="nowPlaying" class="flex__container nowPlaying">' + ''.join(items) + '</div>'
            '<footer><a class="button button--showtime" href="#">not a showtime</a></footer>')


GALAXY_CANNERY = _galaxy_page('11/23/2026', [
    _galaxy_item('THE-HUNGER-GAMES-SUNRISE-ON-THE-REAPING', 'THE HUNGER GAMES: SUNRISE ON THE REAPING',
                 ['1:45 PM', '5:30 PM'], poster='https://galaxy-digital-cdn.app.vista.co/HO1?width=500',
                 sold_out=1),
    _galaxy_item('THE-HUNGER-GAMES-SUNRISE-ON-THE-REAPING-OPEN-CAP',
                 'THE HUNGER GAMES SUNRISE ON THE REAPING (OPEN CAP)', ['4:00 PM'],
                 poster='https://galaxy-digital-cdn.app.vista.co/HO2?width=500'),
    _galaxy_item('EBENEZER', 'EBENEZER', ['2:00 PM']),
    _galaxy_item('THE-CAT-IN-THE-HAT', 'THE CAT IN THE HAT', ['3:00 PM']),
    _galaxy_item('SWAN-LAKE-2027', 'SWAN LAKE (2027)', []),
])
GALAXY_TUCSON = _galaxy_page('11/24/2026', [_galaxy_item('EBENEZER', 'EBENEZER', ['9:00 PM'])])


class GalaxyTests(unittest.TestCase):
    def test_lists(self):
        self.assertEqual(galaxy.parse_theater_slugs(GALAXY_LIST), ['tucson', 'cannery'])
        order = galaxy.parse_site_order(GALAXY_LIST)
        self.assertEqual(order['SWAN-LAKE-2027'], 0)
        self.assertLess(order['EBENEZER'], order['THE-CAT-IN-THE-HAT'])

    def test_theater_page(self):
        day, prints = galaxy.parse_theater_page(GALAXY_CANNERY)
        self.assertEqual(day, TODAY)
        by = {p['slug']: p for p in prints}
        hg = by['THE-HUNGER-GAMES-SUNRISE-ON-THE-REAPING']
        self.assertEqual(hg['title'], 'THE HUNGER GAMES: SUNRISE ON THE REAPING')
        # Two links and a sold-out time; the legend and footer buttons are not counted.
        self.assertEqual(hg['showtimes'], 3)
        self.assertEqual(hg['image'], 'https://galaxy-digital-cdn.app.vista.co/HO1?width=500')
        self.assertEqual(by['SWAN-LAKE-2027']['showtimes'], 0)
        self.assertEqual(galaxy.parse_theater_page(GALAXY_TUCSON)[0], '2026-11-24')

    def test_title_case(self):
        cases = {
            'THE HUNGER GAMES: SUNRISE ON THE REAPING': 'The Hunger Games: Sunrise on the Reaping',
            'HOW TO ROB A BANK': 'How to Rob a Bank',
            "BTS WORLD TOUR 'ARIRANG' IN JAKARTA LIVE": "BTS World Tour 'Arirang' in Jakarta Live",
            'ZERO A.D.: THE BIRTH OF CHRIST': 'Zero A.D.: The Birth of Christ',
            'SPIDER-MAN: (BRAND NEW) BRAND NEW DAY': 'Spider-Man: (Brand New) Brand New Day',
            'MR AND MRS SMITH (1941)': 'Mr and Mrs Smith (1941)',
            "NATIONAL LAMPOON'S CHRISTMAS VACATION": "National Lampoon's Christmas Vacation",
            'ROCKY II': 'Rocky II',
            'A BEAUTIFUL MIND 25TH ANNIVERSARY': 'A Beautiful Mind 25th Anniversary',
            'GALAXY MYSTERY MOVIE - 11/23': 'Galaxy Mystery Movie - 11/23',
            'THE MET OPERA: LA FANCIULLA DEL WEST': 'The Met Opera: La Fanciulla del West',
            'FOCKER-IN-LAW': 'Focker-in-Law',
            'THE LAST OF US': 'The Last of Us',
            'AVENGERS: DOOMSDAY 3D': 'Avengers: Doomsday 3D',
            'I PLAY ROCKY': 'I Play Rocky',
        }
        for raw, want in cases.items():
            self.assertEqual(galaxy.title_case(raw), want, raw)
        # Mixed-case titles are left as the chain wrote them.
        self.assertEqual(galaxy.prepare_title('Wicked: For Good'), ('Wicked: For Good', True))
        self.assertEqual(galaxy.prepare_title('THE HUNGER GAMES SUNRISE ON THE REAPING (OPEN CAP)'),
                         ('The Hunger Games Sunrise on the Reaping (Open Caption)', False))

    def test_rank(self):
        pages = {'cannery': GALAXY_CANNERY, 'tucson': GALAXY_TUCSON, 'walled': WALL, 'down': None}
        prints, stats = galaxy.build_prints(pages, TODAY, galaxy.parse_site_order(GALAXY_LIST))
        self.assertEqual(stats, {'read': 2, 'showing': 1, 'walled': 1, 'other_day': 1})
        items = ranked(galaxy, prints)
        self.assertEqual([(i['rank'], i['title'], i['showtimes'], i['theaters']) for i in items], [
            (1, 'The Hunger Games: Sunrise on the Reaping', 4, 1),
            # A tie: Galaxy's own list puts Ebenezer first, and only for that.
            (2, 'Ebenezer', 1, 1),
            (3, 'The Cat in the Hat', 1, 1)])
        self.assertEqual(items[0]['url'],
                         'https://www.galaxytheatres.com/movie/THE-HUNGER-GAMES-SUNRISE-ON-THE-REAPING')
        self.assertEqual(items[0]['image'], 'https://galaxy-digital-cdn.app.vista.co/HO1?width=500')


# ---------------------------------------------------------------------------
# The contract every chain module keeps
# ---------------------------------------------------------------------------
class ContractTests(unittest.TestCase):
    def test_constants(self):
        want = {malco: ('malco', 'Malco Theatres'), santikos: ('santikos', 'Santikos Entertainment'),
                smg: ('smg', 'Studio Movie Grill'), galaxy: ('galaxy', 'Galaxy Theatres')}
        for mod, (key, label) in want.items():
            self.assertEqual((mod.KEY, mod.LABEL, mod.COOKIE_DOMAIN), (key, label, None))
            for word in ('scrape', 'modeled', 'synthetic', 'estimated'):
                self.assertNotIn(word, mod.LABEL.lower())

    def test_fetch_never_raises(self):
        for mod in MODULES:
            with mock.patch.object(mod, '_read', side_effect=RuntimeError('boom')), \
                    mock.patch.object(mod.logger, 'warning'):
                items, meta = mod.fetch()
            self.assertEqual(items, [])
            self.assertFalse(meta['blocked'])
            self.assertTrue(meta['reason'].startswith('error'))
            self.assertRegex(meta['date'], r'^\d{4}-\d\d-\d\d$')

    def test_blocked_when_every_way_in_is_walled(self):
        def walled(self, url, headers=None):
            self.walled = True
            return None
        for mod in (malco, santikos, smg):
            with mock.patch.object(mod._Site, 'direct', walled), \
                    mock.patch.object(mod, 'render_then_fetch', return_value={}):
                items, meta = mod.fetch()
            self.assertEqual(items, [])
            self.assertTrue(meta['blocked'], mod.KEY)
        with mock.patch.object(galaxy, '_get', return_value=WALL):
            items, meta = galaxy.fetch()
        self.assertEqual(items, [])
        self.assertTrue(meta['blocked'])

    def test_browser_path_reads_what_the_direct_path_was_refused(self):
        bodies = {malco.ENTRY_URL: MALCO_HOME,
                  f'{malco.BASE}/cinemas/paradiso-cinema-grill-and-imax/': MALCO_CINEMA,
                  f'{malco.BASE}/cinemas/wolfchase-cinema-grill/': MALCO_EMPTY,
                  f'{malco.BASE}/cinemas/smyrna-cinema/': WALL}

        def walled(self, url, headers=None):
            self.walled = True
            return None

        def browser(page_url, urls, **kw):
            return {u: bodies.get(u) for u in urls}

        with mock.patch.object(malco._Site, 'direct', walled), \
                mock.patch.object(malco, 'render_then_fetch', side_effect=browser), \
                mock.patch.object(malco, 'today_iso', return_value=TODAY):
            items, meta = malco.fetch()
        self.assertEqual((meta['theaters'], meta['theaters_listed'], meta['via'], meta['blocked']),
                         (2, 3, 'browser', False))
        self.assertEqual(items[0]['title'], 'Hunger Games: Sunrise on the Reaping')

    def test_no_long_dashes_in_the_modules(self):
        here = Path(__file__).resolve().parent
        for name in ('theater_malco.py', 'theater_santikos.py', 'theater_smg.py',
                     'theater_galaxy.py', Path(__file__).name):
            text = (here / name).read_text(encoding='utf-8')
            for code in (0x2013, 0x2014):       # en dash, em dash
                self.assertNotIn(chr(code), text, name)


if __name__ == '__main__':
    result = unittest.main(exit=False, verbosity=1).result
    if result.wasSuccessful():
        print('ALL OK')
    sys.exit(0 if result.wasSuccessful() else 1)
