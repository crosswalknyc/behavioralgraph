"""A tile's name is the programme, not the tile read aloud.

Three scrapers were shipping label text as a title on 2026-09-25:

  ESPN+   every one of 25 rows was a live-schedule blurb
          ("LIVE Started 23 minutes ago The Pat McAfee Show ESPN
            The Pat McAfee Show Choose Feed Entry"), so the whole
          rail rendered blank because nothing can be priced against
          a blurb.
  Hulu    the position tail leaked on the hub rails
          ("SportsCenter, Item 3 of 9"), because the pattern only
          knew the "of many" spelling.
  Netflix the Apollo cache is a JavaScript string before it is JSON,
          so the day's #3 published as "Wonka\\'s The Golden Ticket".

Same mistake in three places: taking an element's full text where
only part of it is the name. These are the cases, written from the
live pages.

    python3 -m scripts.trends_scrapers.test_tile_title_reads
"""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_FAILURES: list[str] = []


def check(ok: bool, what: str, detail: str = '') -> None:
    if ok:
        print(f'  ok    {what}')
    else:
        _FAILURES.append(what)
        print(f'  FAIL  {what}' + (f'  [{detail}]' if detail else ''))


# ────────────────────────────────────────────────────────────────────
# ESPN+
# ────────────────────────────────────────────────────────────────────
# Every string here is verbatim from www.disneyplus.com/browse/espn
# rendered signed-in on 2026-09-25.
_ESPN_CASES = [
    # Live schedule rail.
    ('LIVE Started 23 minutes ago The Pat McAfee Show ESPN '
     'The Pat McAfee Show Choose Feed Entry', 'The Pat McAfee Show'),
    ('LIVE Started 1 hour 7 minutes ago First Take ESPN First Take '
     'Choose Feed Entry', 'First Take'),
    ("LIVE Started 23 minutes ago UAB vs. Temple ESPN+ "
     "NCAA Women's Volleyball Released 2026. Choose Feed Entry",
     'UAB vs. Temple'),
    ('LIVE Started 1 hour 7 minutes ago The Golics ESPN+ Released 2026. '
     'Choose Feed Entry', 'The Golics'),
    # Upcoming rail: two clock readings before the name.
    ('Upcoming 9:00 AM 9:00 AM - 3:00 PM Mecum Auctions: Nashville 2026 '
     '(Day 2) ESPN+ Mecum Auctions Released 2026.',
     'Mecum Auctions: Nashville 2026 (Day 2)'),
    ('Upcoming 11:00 AM 11:00 AM - 12:00 PM NCAA Football SECN+ '
     'Sooner Gameday', 'NCAA Football'),
    ('Upcoming 9:00 AM 9:00 AM - 11:00 AM Total Access NFL Network '
     'NFL Total Access', 'Total Access'),
    ('Upcoming 9:00 AM 9:00 AM - 12:00 PM The Rich Eisen Show ESPN+ '
     'Fri, 9/25 - The Rich Eisen Show', 'The Rich Eisen Show'),
    ("Upcoming 10:00 AM 10:00 AM - 12:00 PM Notre Dame vs. Boston College "
     "ACCNX NCAA Women's Volleyball Released 2026.",
     'Notre Dame vs. Boston College'),
    # Replay rail: no network at all, a date does the cutting.
    ('Replay Aired September 25, 2026 SportsCenter+ Fri, 9/25 - '
     'SportsCenter+ Select for details on this title.', 'SportsCenter+'),
    # Episode metadata, and a title that ends on the bundle it streams
    # on. Disney+ is not a network here or these lose half their name.
    ('Get Up for Disney+ Season 2026 Episode 91 Fri, 9/25 - '
     'Get Up for Disney+ Select for details on this title.',
     'Get Up for Disney+'),
    ('Pardon The Interruption for Disney+ Season 2026 Episode 188 '
     'Select for details on this title.',
     'Pardon The Interruption for Disney+'),
    # Catalog rails.
    ('The Two Escobars ESPN+ Released 2010. Documentaries, Biography '
     'genre. Select for details on this title.', 'The Two Escobars'),
    ('New Series Badge Setting the Tempo ESPN+ Released 2026. '
     'Docuseries, Sports genre. Select for details on this title.',
     'Setting the Tempo'),
    ('New Badge 14 Days in Gainesville ESPN Global No Bumper '
     'Select for details on this title.', '14 Days in Gainesville'),
    ('The Tuck Rule ESPN+ Select for details on this title.',
     'The Tuck Rule'),
    ("You Don't Know Bo ESPN+ Select for details on this title.",
     "You Don't Know Bo"),
    # Hero tile: the count comes first, not a network.
    ('The Many Lives of Lane Kiffin, 9 of 9 items., Lane Kiffin reflects '
     'on his tumultuous career., Rated TV-14, Released 2025., '
     'Documentaries, Biography genre.', 'The Many Lives of Lane Kiffin'),
    # A title that ENDS on the word ESPN, followed by the network. The
    # first network position is inside the name; the second is the
    # boundary.
    ('Sports Heaven: The Birth of ESPN ESPN Global No Bumper '
     'Select for details on this title.',
     'Sports Heaven: The Birth of ESPN'),
    # A badge sitting between the name and the network.
    ('ESPN Jeopardy! Disney+ Original Select for details on this title.',
     'ESPN Jeopardy!'),
    # Names nothing.
    ('Select for details on this title.', ''),
    ('1:00 PM - 4:00 PM', ''),
    ('LIVE', ''),
    ('Select for more information about this title.', ''),
    ('Released 2026.', ''),
    ('left arrow', ''),
]


def test_espnplus() -> None:
    from scripts.trends_scrapers import espnplus as ep
    print('ESPN+ programme names')
    for raw, want in _ESPN_CASES:
        got = ep._espn_programme_name(raw)
        check(got == want, f'{raw[:54]!r} -> {want!r}', f'got {got!r}')

    # Nothing survives an empty or absent label.
    for bad in ('', '   ', None):
        check(ep._espn_programme_name(bad) == '', f'{bad!r} names nothing')

    # The sport and league pickers are rails of navigation, dropped by
    # the style they declare rather than by guessing at their text.
    page = (
        '<div data-testid="set-section" data-set-style="logo_round">'
        '<a data-testid="set-item" aria-label="NCAA - Football" '
        'href="/browse/entity-67d48152-056a-4fc1-bd99-e943cfe56481"></a>'
        '</div>'
        '<div data-testid="set-section" data-set-style="poster_art">'
        '<a data-testid="set-item" aria-label="New Badge The Greatest Save '
        'ESPN Global No Bumper Select for details on this title." '
        'href="/browse/entity-aa11bb22-cc33-dd44-ee55-ff6677889900"></a>'
        '</div>')
    rows = ep._extract_espnplus(page)
    check([r['title'] for r in rows] == ['The Greatest Save'],
          'a logo_round rail is navigation and is skipped',
          f'got {[r["title"] for r in rows]}')
    check(rows and rows[0]['rank'] == 1, 'ranks start at 1 after a skip')

    # A tile naming no programme is dropped, never shipped blank.
    only_noise = (
        '<div data-testid="set-section" data-set-style="poster_linear">'
        '<a data-testid="set-item" aria-label="Select for details on this '
        'title." href="/browse/entity-11112222-3333-4444-5555-666677778888">'
        '</a></div>')
    check(ep._extract_espnplus(only_noise) == [],
          'a tile that names no programme is dropped')

    # A datacenter block parses to nothing rather than to the shell.
    check(ep._extract_espnplus(ep._BAMGRID_ERROR_MARKER) == [],
          'the IP-gate shell parses to nothing')


# ────────────────────────────────────────────────────────────────────
# Hulu
# ────────────────────────────────────────────────────────────────────
_HULU_CASES = [
    # The spelling that leaked. Nine rows on the hub rails carried it.
    ('ABC News Live First, Item 1 of 9', 'ABC News Live First'),
    ('SportsCenter, Item 3 of 9', 'SportsCenter'),
    ('Get Up!, Item 4 of 9', 'Get Up!'),
    ('#16 SMU vs. #23 Louisville, Item 5 of 9', '#16 SMU vs. #23 Louisville'),
    # The spelling that already worked, which must keep working.
    ('The Secret Lives of Mormon Wives, Item 1 of many',
     'The Secret Lives of Mormon Wives'),
    ('Play Gilmore Girls, Item 12 of many', 'Gilmore Girls'),
    # Stacked tails: one pass would leave the season behind.
    ('Abbott Elementary, Season 4, Item 2 of 9', 'Abbott Elementary'),
    ('Bob\u2019s Burgers, Season 14', 'Bob\u2019s Burgers'),
    # Directional isolates wrap the label on some layouts.
    ('\u2066Chad Powers\u2069, Item 8 of 9', 'Chad Powers'),
    # A bare title is left alone.
    ('American Horror Story', 'American Horror Story'),
    # A title that genuinely contains the word Item is not truncated.
    ('Collector Item', 'Collector Item'),
]


def test_hulu() -> None:
    from scripts.trends_scrapers import hulu
    print('Hulu tile titles')
    for raw, want in _HULU_CASES:
        got = hulu._clean_title(raw)
        check(got == want, f'{raw[:48]!r} -> {want!r}', f'got {got!r}')

    dom = ''.join(
        f'<a href="/series/{i}aaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" '
        f'aria-label="{t}"></a>'
        for i, t in enumerate(('SportsCenter, Item 3 of 9',
                                'First Take, Item 7 of 9'), start=1))
    rows = hulu._extract_hulu_dom(dom)
    check([r['title'] for r in rows] == ['SportsCenter', 'First Take'],
          'the DOM reader carries no position tail',
          f'got {[r["title"] for r in rows]}')


# ────────────────────────────────────────────────────────────────────
# Netflix
# ────────────────────────────────────────────────────────────────────
_NETFLIX_ESCAPES = [
    ("Wonka\\'s The Golden Ticket", "Wonka's The Golden Ticket"),
    ("Death of the Pastor\\'s Wife", "Death of the Pastor's Wife"),
    ('Bob\\u0027s Burgers', "Bob's Burgers"),
    ("Marvel\\x27s Daredevil", "Marvel's Daredevil"),
    ('Plain Title', 'Plain Title'),
    ('He said \\"hi\\"', 'He said "hi"'),
    # An escaped backslash is a backslash, and the quote after it is
    # its own character. One left-to-right pass is what gets this
    # right; a chain of replaces does not.
    ('A\\\\B', 'A\\B'),
    ('C:\\\\Users\\\\jenna', 'C:\\Users\\jenna'),
    ('two\\nlines', 'two\nlines'),
]


def test_netflix() -> None:
    from scripts.trends_scrapers import netflix as nf
    print('Netflix cache strings')
    for raw, want in _NETFLIX_ESCAPES:
        got = nf._js_unescape(raw)
        check(got == want, f'{raw!r} -> {want!r}', f'got {got!r}')

    # End to end off the cache shape the page ships.
    frag = ('"PinotRankedBoxshotEntityTreatment:rankedBoxshot_Video:'
            '81730492_1a2b3c4d-5e6f":{"n":1,"displayString":'
            '"Wonka\\\'s The Golden Ticket"}')
    titles = nf._pinot_titles(frag)
    check(list(titles.values()) == ["Wonka's The Golden Ticket"],
          'the ranked entry reads as its title',
          f'got {list(titles.values())}')

    # Both spellings reduce to one key, which is why the escape never
    # split a title in two or hid one behind another.
    from scripts.trends_scrapers import stream_estimates as se
    check(se._cp_normalize("Wonka\\'s The Golden Ticket")
          == se._cp_normalize("Wonka's The Golden Ticket"),
          'the key normaliser reads both spellings as one title')
    check(se._cp_normalize('Bob\u2019s Burgers')
          == se._cp_normalize("Bob's Burgers"),
          'a typographic apostrophe reads as one title too')


# ────────────────────────────────────────────────────────────────────
# The pricing pass reaches a chart-declared row
# ────────────────────────────────────────────────────────────────────
# Wiring a chart brings titles onto a rail that the ordinary collector
# never collected. On 2026-09-25 Netflix's own #2 and #3 for the day
# were two of them, blank on the most-checked rail on the board.
#
# The walk was never the problem: it reads the payload the dashboard
# renders, so it sees a row whatever put it there. What was missing
# was a pass at all. These rails refresh from the operator's laptop
# hours after the nightly orchestrator has finished, and the gate ran
# only inside that orchestrator, so a title that arrived in the
# morning batch had nothing behind it until the next night.
#
# Two things are held here: the walk still reaches a chart-only row
# (so nobody narrows it to "what the collector collected" later), and
# both mid-day re-scrape paths still end with the pass.


def _chart_only_payload() -> dict:
    """A board whose blank rows exist only because a chart declared
    them: no weekly figure, no stored reading, rank deltas only."""
    return {'cards': {'streaming_trending': {'netflix': {'tv': [
        {'rank': 1, 'title': 'Monster: The Lizzie Borden Story',
         'published_rank': 1, 'published_chart': 'Netflix Top 10 US',
         'weekly_views': 12_100_000,
         'us_streams': {'us_estimate': 743_017, 'est_basis': 'research'}},
        {'rank': 12, 'title': 'A Different World',
         'published_rank': 2, 'published_chart': 'Netflix Top 10 US',
         'us_streams': {'delta_pct': -0.5, 'direction': 'down',
                        'prev_rank': 2}},
        {'rank': 13, 'title': "Wonka's The Golden Ticket",
         'published_rank': 3, 'published_chart': 'Netflix Top 10 US',
         'us_streams': {'delta_pct': -0.5, 'direction': 'down',
                        'prev_rank': 3}},
    ]}}}}


def test_chart_only_rows_are_collected() -> None:
    from scripts.trends_scrapers import coverage_gate as cg
    print('the pricing pass reaches a chart-declared row')

    payload = _chart_only_payload()
    states = [cg._audience_state(it)
              for _p, _r, it in cg._walk_rendered(payload['cards'])]
    check(states == ['researched', 'missing', 'missing'],
          'a row carrying only rank deltas reads as blank',
          f'got {states}')

    # `collect_missing` reads the live store to decide whether a blank
    # row already has an entry (narrow per-service re-price) or needs
    # a whole-item one. This test is about the whole-item path, so the
    # store is empty here; against the live store the same rows route
    # narrowly once the terminal bracket has created their entries,
    # which is the intended behaviour and not what is under test.
    from scripts.trends_scrapers import stream_estimates as se
    real_read = se._read_snapshot
    se._read_snapshot = lambda source: {'items': {}}
    try:
        stream, _headline, total, researched, _baseline, _caps = \
            cg.collect_missing(payload)
    finally:
        se._read_snapshot = real_read
    got = sorted(i['display_title'] for i in stream)
    check(got == ['A Different World', "Wonka's The Golden Ticket"],
          'both chart-declared blanks enter the research population',
          f'got {got}')
    check(total == 3 and researched == 1,
          'the walk counts every rendered row', f'{total}/{researched}')
    # The rail is named as the SERVICE, which is what makes the answer
    # come back with a block for Netflix rather than for nothing.
    check(all(l.startswith('Netflix #') for i in stream
              for l in i['chart_labels']),
          'the research is asked about the service the row is on',
          f'got {[i["chart_labels"] for i in stream]}')


def test_midday_rescrapes_price_what_they_bring() -> None:
    import inspect
    from scripts.trends_scrapers import local_residential_run as lrr
    from scripts.trends_scrapers import refresh_after_donation as rad
    print('every re-scrape path ends with the pricing pass')

    for mod, runner in ((lrr, lrr._run_all), (rad, rad.main)):
        src = inspect.getsource(runner)
        check('coverage_gate' in src or '_run_coverage_gate' in src
              or 'run_coverage_gate' in src,
              f'{mod.__name__} prices what it brought in')
        check(hasattr(mod, '_run_coverage_gate')
              or hasattr(mod, 'run_coverage_gate'),
              f'{mod.__name__} has the pass wired')

    # It stays off when nothing published, so a batch that scraped
    # nothing does not pay for a pass with nothing to price.
    src = inspect.getsource(lrr._run_all)
    check('if ok_count and run_coverage' in src,
          'the residential batch only prices behind a scraper that '
          'published')


def main() -> int:
    test_espnplus()
    print()
    test_hulu()
    print()
    test_netflix()
    print()
    test_chart_only_rows_are_collected()
    print()
    test_midday_rescrapes_price_what_they_bring()
    print()
    if _FAILURES:
        print(f'{len(_FAILURES)} FAILURE(S):')
        for f in _FAILURES:
            print(f'  - {f}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
