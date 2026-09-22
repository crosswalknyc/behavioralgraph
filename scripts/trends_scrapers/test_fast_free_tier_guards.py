#!/usr/bin/env python3
"""Regression: the three free-tier FAST rails refuse to publish the
wrong thing.

Each of Philo Free, Plex Live TV and Sling Freestream has one failure
mode that would not look like a failure, and each is guarded. This
file exercises those guards so they cannot rot quietly.

  * PLEX, the silent one and the reason this file exists.
    `epg.provider.plex.tv` resolves country off the request IP and
    answers HTTP 200 with a COMPLETE AND PLAUSIBLE lineup for the
    wrong country. From the build box that is 254 channels titled
    "Plex Channels in DE"; from a US address it is 691 titled "Plex
    Channels in US". Nothing in the response shape separates them, so
    a scraper that only checked for rows would publish a German rail
    onto a US board. The guard asserts the lineup names the US, and it
    must be a HARD refusal rather than a warning, so these checks
    assert `fetch` RAISES and returns no payload at all.

  * PHILO. Its lineup page renders one block per plan and only the
    `free-channels` block is the free tier. If that block were ever
    missed, the paid Essential and Bundle+ channels are sitting right
    there in the same document, so the failure would look like a
    lineup rather than a mistake. The guard refuses instead of falling
    back to any other plan group.

  * SLING. The free lineup is the full guide minus Sling's own
    `premium` tag. If that tag disappeared, the paid add-on services
    (STARZ, MGM+ and friends) would ship onto a free rail. The guard
    refuses when the tag is absent.

Hermetic: every network call is replaced. No board, no S3, no
clickstream.

    python3 -m scripts.trends_scrapers.test_fast_free_tier_guards
"""
from __future__ import annotations

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from scripts.trends_scrapers import philo_free as philo  # noqa: E402
from scripts.trends_scrapers import plex_live as plex  # noqa: E402
from scripts.trends_scrapers import sling_freestream as sling  # noqa: E402

FAILURES: list = []


def check(ok: bool, what: str) -> None:
    print(f'{"PASS" if ok else "FAIL"}  {what}')
    if not ok:
        FAILURES.append(what)


# ────────────────────────────────────────────────────────────────────
# Plex: the lineup has to name the US or nothing publishes
# ────────────────────────────────────────────────────────────────────
def _plex_payload(lineup_title: str, n: int, language: str = 'en') -> dict:
    return {'MediaContainer': {
        'title': lineup_title,
        'size': n,
        'Channel': [{'title': f'Channel {i}', 'vcn': f'{i:03d}',
                      'slug': f'channel-{i}', 'language': language,
                      'genreRatingKeys': ['genre_deadbeef']}
                     for i in range(n)],
    }}


class _FakePlexHTTP:
    """Stands in for urlopen inside plex_live."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.token_calls = 0
        self.lineup_calls = 0

    def __call__(self, req, timeout=None):  # noqa: ANN001
        url = req.full_url if hasattr(req, 'full_url') else str(req)
        if 'users/anonymous' in url:
            self.token_calls += 1
            body = json.dumps({'authToken': 'fake-anonymous-token'})
        else:
            self.lineup_calls += 1
            body = json.dumps(self.payload)
        return _FakeResponse(body)


class _FakeResponse(io.BytesIO):
    def __init__(self, text: str) -> None:
        super().__init__(text.encode('utf-8'))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _run_plex(payload: dict):
    """Returns (result, raised_exception)."""
    real = plex._urllib_request.urlopen
    plex._urllib_request.urlopen = _FakePlexHTTP(payload)  # type: ignore
    try:
        return plex.fetch(), None
    except Exception as e:  # noqa: BLE001
        return None, e
    finally:
        plex._urllib_request.urlopen = real  # type: ignore


def test_plex() -> None:
    print('\n== Plex Live TV: the geo guard ==')

    # The exact response the build box gets. This is the whole point.
    result, err = _run_plex(_plex_payload('Plex Channels in DE', 254, 'de'))
    check(err is not None,
          'a German lineup raises rather than returning a payload')
    check(result is None,
          'a German lineup publishes nothing at all')
    if err is not None:
        msg = str(err)
        check('Plex Channels in DE' in msg,
              'the failure names the lineup it actually got')
        check('residential' in msg.lower(),
              'the failure says where the scraper has to run')

    # Any other country is equally wrong, so the guard must not be a
    # DE-specific special case.
    for title in ('Plex Channels in CA', 'Plex Channels in GB',
                   'Plex Channels in AU', 'Plex Channels in BR'):
        _r, e = _run_plex(_plex_payload(title, 300))
        check(e is not None, f'{title!r} is refused too')

    # A lineup with rows but no title at all is not evidence of the US.
    _r, e = _run_plex(_plex_payload('', 500))
    check(e is not None, 'an untitled lineup is refused')

    # The US lineup goes through.
    result, err = _run_plex(_plex_payload('Plex Channels in US', 691))
    check(err is None, 'the US lineup is accepted')
    check(bool(result) and len(result.get('channels') or []) == 691,
          'the US lineup ships every channel')
    check((result or {}).get('lineup_title') == 'Plex Channels in US',
          'the payload records which lineup it came from')

    # Spanish-language rows carry the one publisher label Plex gives
    # us, and it is a label the channel-type map already knows.
    result, _err = _run_plex(
        _plex_payload('Plex Channels in US', 10, 'es'))
    genres = {c['source_genre'] for c in (result or {}).get('channels') or []}
    check(genres == {plex._ESPANOL_LABEL},
          'Spanish-language rows ship the Espanol label')
    try:
        from scripts.trends_scrapers.fast_channel_genres import (
            source_genre_label)
        check(source_genre_label(plex._ESPANOL_LABEL) == 'Espanol',
              'that label resolves to a channel type without a new key')
    except ImportError:
        check(False, 'fast_channel_genres import')

    # An empty lineup is a failure whatever it is titled.
    _r, e = _run_plex(_plex_payload('Plex Channels in US', 0))
    check(e is not None, 'a US-titled but empty lineup is refused')

    # No schedule is published on this endpoint, so the honest read is
    # no signal rather than a channel that airs nothing.
    result, _err = _run_plex(_plex_payload('Plex Channels in US', 5))
    check(all(c['airings'] == 0
               for c in (result or {}).get('channels') or []),
          'every channel reports no airings signal')


# ────────────────────────────────────────────────────────────────────
# Philo: only the free plan group ships
# ────────────────────────────────────────────────────────────────────
_PHILO_TILE = ('<div class="channel-tile"><img src="x.svg" alt="{n} logo" '
                'title="{n}" class="channel-logo"/></div>')


def _philo_page(groups: dict) -> str:
    out = []
    for gid, names in groups.items():
        out.append(f'<div class="channels-group channel-grouping" id="{gid}">')
        out.append('<div class="channels">')
        out.extend(_PHILO_TILE.format(n=n) for n in names)
        out.append('</div></div>')
    out.append('<div id="philo-footer">footer</div>')
    return ''.join(out)


def _run_philo(lineup_html: str, landing_html: str = '{}'):
    real = philo._get_html

    def fake(url: str) -> str:
        return lineup_html if url == philo._LINEUP_URL else landing_html

    philo._get_html = fake  # type: ignore
    try:
        return philo.fetch(), None
    except Exception as e:  # noqa: BLE001
        return None, e
    finally:
        philo._get_html = real  # type: ignore


def test_philo() -> None:
    print('\n== Philo Free: only the free plan group ships ==')

    page = _philo_page({
        'base-package': ['AMC', 'A&E', 'Hallmark Channel'],
        'bundle-channels': ['HBO Max Basic with Ads plan'],
        'free-channels': ['CBS News 24/7', '48 Hours', 'Judge Judy'],
        'add-ons': ['Starz', 'MGM+'],
    })
    result, err = _run_philo(page)
    check(err is None, 'a healthy page parses')
    names = [c['name'] for c in (result or {}).get('channels') or []]
    check(sorted(names) == ['48 Hours', 'CBS News 24/7', 'Judge Judy'],
          'only the free group ships')
    for paid in ('AMC', 'A&E', 'Hallmark Channel', 'Starz', 'MGM+',
                  'HBO Max Basic with Ads plan'):
        check(paid not in names, f'{paid} (paid) stays off the rail')
    check((result or {}).get('paid_channels_excluded') == 6,
          'the payload records how many paid channels were left out')

    # The free group missing is the dangerous case: the paid groups are
    # right there and would look like a lineup.
    page = _philo_page({
        'base-package': ['AMC', 'A&E'],
        'add-ons': ['Starz'],
    })
    result, err = _run_philo(page)
    check(err is not None,
          'no free group raises rather than shipping the paid groups')
    check(result is None, 'no free group publishes nothing')

    # A page shape change that yields no groups at all must not read as
    # an empty free tier.
    _r, e = _run_philo('<html><body>nothing here</body></html>')
    check(e is not None, 'a page with no plan groups is refused')

    # Genre is a hint and must never widen the lineup.
    page = _philo_page({'free-channels': ['Buzzr']})
    landing = json.dumps({'landingPage': {'channelGenreGroups': [
        {'title': 'Popular', 'channels': [{'name': 'Buzzr'}]},
        {'title': 'Family', 'channels': [{'name': 'Buzzr'},
                                          {'name': 'AMC'}]},
    ]}})
    result, err = _run_philo(page, landing)
    names = [c['name'] for c in (result or {}).get('channels') or []]
    check(names == ['Buzzr'],
          'a genre shelf naming a paid channel does not add it')
    row = ((result or {}).get('channels') or [{}])[0]
    check(row.get('source_genre') == 'Family',
          'the merchandising shelf is skipped and the real genre wins')

    # Losing the genre page costs labels and nothing else.
    result, err = _run_philo(page, 'not json at all')
    check(err is None and len((result or {}).get('channels') or []) == 1,
          'an unparseable genre page still ships the lineup')


# ────────────────────────────────────────────────────────────────────
# Sling: the free lineup is the guide minus Sling's own paid tag
# ────────────────────────────────────────────────────────────────────
class _FakeSlingSession:
    """Stands in for the browser-backed session."""

    def __init__(self, by_filter: dict, filters: dict) -> None:
        self.by_filter = by_filter
        self.filters = filters

    def guide(self, filter_id=None):  # noqa: ANN001
        return {'channels': [{'title': t}
                              for t in self.by_filter.get(filter_id, [])],
                 'grid_actions': {'FILTER_ACTIONS': [
                     {'title': k, 'payload': {'filter_id': v},
                      'enabled': True}
                     for k, v in self.filters.items()]}}

    def channel_names(self, filter_id=None):  # noqa: ANN001
        return list(self.by_filter.get(filter_id, []))


def test_sling() -> None:
    print('\n== Sling Freestream: the paid tag is what defines free ==')

    free_names = ['ABC News Live', 'CBS News 24/7', 'Cheddar News']
    paid_names = ['STARZ', 'MGM+', 'Curiosity Stream']
    by_filter = {
        'all_channels': free_names + paid_names,
        'premium': paid_names,
        'news': ['ABC News Live', 'CBS News 24/7'],
    }
    filters = {'ALL CHANNELS': 'all_channels', 'PREMIUM': 'premium',
                'NEWS & OPINION': 'news'}

    session = _FakeSlingSession(by_filter, filters)
    free, paid, genre_of = sling._collect(session)
    check(free == free_names, 'the free lineup is the guide minus premium')
    for name in paid_names:
        check(name not in free, f'{name} (paid add-on) stays off the rail')
    check(paid == set(paid_names), 'the paid set is reported back')
    check(genre_of.get('ABC News Live') == 'NEWS & OPINION',
          'the guide filter supplies the publisher genre')

    # The dangerous case: the paid tag disappears. Shipping the whole
    # guide would put STARZ and MGM+ on a free rail.
    session = _FakeSlingSession(
        {'all_channels': free_names + paid_names},
        {'ALL CHANNELS': 'all_channels'})
    try:
        sling._collect(session)
        check(False, 'a missing premium tag raises')
    except RuntimeError as e:
        check(True, 'a missing premium tag raises')
        check('premium' in str(e).lower(),
              'the failure names the tag it needed')

    # An empty guide is a failure, not an empty lineup.
    session = _FakeSlingSession({'all_channels': []},
                                 {'ALL CHANNELS': 'all_channels',
                                  'PREMIUM': 'premium'})
    try:
        sling._collect(session)
        check(False, 'an empty guide raises')
    except RuntimeError:
        check(True, 'an empty guide raises')

    # Sling writes LOCAL into the name of a single-market feed, and a
    # one-market news feed must not be priced as a national rail.
    check(sling._LOCAL_MARKER in ' FOX 5 LOCAL NEW YORK ',
          'the LOCAL marker matches how Sling writes it')

    # Narrow genres win over ENTERTAINMENT, which covers nearly half
    # the guide and says little.
    order = list(sling._GENRE_FILTERS)
    check(order.index('NEWS & OPINION') < order.index('ENTERTAINMENT'),
          'narrow genres are asked for before ENTERTAINMENT')


# ────────────────────────────────────────────────────────────────────
# Shared: every row carries a channel type the filter can use
# ────────────────────────────────────────────────────────────────────
def test_registration() -> None:
    print('\n== registration ==')
    from scripts.trends_scrapers.fast_channel_genres import (
        _API_LINEUP_SOURCES, _SOURCE_GENRE_MAP, TAXONOMY)
    from scripts.trends_scrapers.stream_estimates import (
        _API_LINEUP_SOURCES as SE_SOURCES, _FAST_SLUGS,
        _FAST_CHANNEL_PLATFORMS_META)

    for source in ('philo_free', 'plex_live', 'sling_freestream'):
        check(source in _API_LINEUP_SOURCES,
              f'{source} is registered with the channel-type classifier')

    for slug in ('philo', 'plex', 'sling_freestream'):
        check(slug in [s for s, _l in _FAST_SLUGS],
              f'{slug} is in _FAST_SLUGS so its channels get collected')
        check(slug in [s for s, _src in SE_SOURCES],
              f'{slug} lineup is folded in from its own snapshot')
        meta = [p for p in _FAST_CHANNEL_PLATFORMS_META
                 if p['key'] == slug]
        check(len(meta) == 1,
              f'{slug} has exactly one pricing anchor entry')
        if meta:
            check(meta[0]['ceiling'] > 0,
                  f'{slug} carries a ceiling')
            check(len(meta[0]['anchors']) > 400,
                  f'{slug} carries researched anchor guidance')

    # The additions have to land on the fixed taxonomy, not near it.
    for label in ('news & opinion', 'true crime', 'classics & re-runs',
                   'science & nature', 'docs & history', 'kids & family',
                   'family'):
        check(_SOURCE_GENRE_MAP.get(label) in TAXONOMY,
              f'publisher label {label!r} maps onto the taxonomy')

    # The two geo-gated platforms belong on the residential lane and
    # must not be on the Hetzner batch.
    from scripts.trends_scrapers.local_residential_run import (
        RESIDENTIAL_SCRAPERS)
    from scripts.trends_scrapers.run_all import SCRAPERS
    residential = [m for m, _l in RESIDENTIAL_SCRAPERS]
    batch = [s[0] for s in SCRAPERS]
    for module in ('plex_live', 'sling_freestream'):
        check(module in residential, f'{module} runs residentially')
        check(module not in batch,
              f'{module} is NOT on the Hetzner batch')
    check('philo_free' in batch, 'philo_free runs on the Hetzner batch')
    check('philo_free' not in residential,
          'philo_free is not on the residential lane')


def main() -> int:
    test_plex()
    test_philo()
    test_sling()
    test_registration()
    print()
    if FAILURES:
        print(f'{len(FAILURES)} FAILURE(S)')
        for f in FAILURES:
            print(f'  - {f}')
        return 1
    print('all checks passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
