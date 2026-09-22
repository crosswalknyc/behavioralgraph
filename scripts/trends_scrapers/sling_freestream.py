"""
Sling Freestream channel lineup for the FAST Channel Ranker.

WHAT THIS COVERS, AND WHAT IT DELIBERATELY DOES NOT.

Sling Freestream is Sling's always-free ad-supported service: no
subscription, no payment card, no account, and Sling markets it at
over 600 free channels. That is a FAST service on the same definition
as Pluto TV and Tubi, so it belongs on this tab.

Sling's PAID vMVPD plans do NOT belong here and are excluded on
purpose. Sling Orange is about 30 channels and Sling Blue about 40,
both around $46 a month, and EchoStar reported 1.707 million Sling TV
subscribers at 30 June 2026. That subscriber count describes the PAID
service and is not an audience figure for this rail; treating it as
one is the same error as pricing MyFree DIRECTV off DIRECTV satellite
subscribers. The rail is labelled "Sling Freestream" and never
"Sling".

THE SAME MISTAKE IS AVAILABLE FROM THE CATALOG SOURCE, AND THERE IT IS
AVOIDABLE. The catalog source carries the free tier as its own package
(`sli`, id 2766, monetization FAST) and keeps the paid tiers as four
separate FLATRATE packages (`stv` Sling TV, `slo` Orange, `slb` Blue,
`sse` Sports Extras). Only the FAST one describes this service. Never
union a FLATRATE Sling package into anything on this tab.

THE FREE / PAID LINE IS SLING'S, NOT OURS. The guide publishes its own
filters, and two of them settle this without us arbitrating anything:

    all_channels   577 channels   everything an unauthenticated
                                  visitor can see
    premium         11 channels   the paid add-on services carried
                                  inside that guide (STARZ, MGM+,
                                  Curiosity Stream, FlixFling,
                                  Here TV, Kartoon Channel!, Magnolia
                                  Selects, Outside TV Features,
                                  Travelxp HD, UP Faith & Family,
                                  Grokker)

The free lineup is `all_channels` minus `premium`. That definition was
chosen over the two free-branded filters on purpose: `freestream` (225)
and `sling_free` (484) overlap only partially, neither is a subset of
the other, and 53 channels (mostly the free international feeds) carry
neither tag, so picking one of them would silently drop real free
channels and picking between them would be us deciding where Sling's
line sits. Subtracting Sling's own paid tag from Sling's own full
guide needs no such judgement.

Verified independently of the tags: the unauthenticated guide carries
no paid flagship at all. No ESPN, TNT, TBS, CNN, Disney Channel, Food
Network, HGTV, live AMC, Bravo, USA, Nickelodeon, Fox News Channel,
MSNBC, Discovery Channel, Lifetime, History, TLC, Comedy Central,
Hallmark Channel, NFL Network, Freeform, Syfy or E!. The AMC and A&E
and Bravo entries that do appear are FAST channels (Stories By AMC,
AMC Thrillers, A&E Crime 360, Bravo Vault), the same ones Philo and
Plex carry.

RUNS RESIDENTIALLY, AND MUST STAY THAT WAY. Sling geo-gates in two
places at once. `www.sling.com` answers 403 from the build box off an
F5 edge in Frankfurt, and `p-geo.movetv.com/geo` returns
`playback_disallowed: true` with code 5 there, against `country: usa`
from a US residential address. Unlike Plex, this one fails loudly
rather than returning a foreign lineup, but it still has to run from
the right address, so this module sits in `RESIDENTIAL_SCRAPERS` in
`local_residential_run.py`.

The 403 is NOT a TLS fingerprint. Worth stating plainly because the
house reflex is to reach for curl_cffi first, and that reflex was
right on an Amazon block we once read as geography. Here it is the
other way round: stock `requests` and curl_cffi impersonating Chrome
both get 403 from the build box on the identical URL, and both get 200
from a US address. Impersonation changes nothing.

WHY PLAYWRIGHT RATHER THAN A PLAIN HTTP CLIENT. The guide sits behind
a bearer token, and Sling mints that token by signing the request with
OAuth 1.0a HMAC-SHA1 using a consumer secret compiled into its web
bundle. Reimplementing that signature here would mean lifting a
private signing key out of minified JavaScript and keeping it working,
which is a different category of thing from the app-client
impersonation the other scrapers here do (Vizio sends the app's own
okhttp UA, MyFree DIRECTV matches Chrome's TLS). So the browser is
allowed to do the auth it was built to do, exactly as hulu.py and
mgmplus.py let it, and then the guide is read with the session the
page already holds. Nothing is signed by us and no secret is copied
into this repo.

Two headers on top of the token are mandatory and the API names them
in its error body if they are missing: `timezone` and `dma`. They are
what make the guide resolvable at all, not a personalization nicety.

NO AIRINGS SIGNAL, AND THAT IS A CHOICE. The guide response does carry
a `grid.schedules` block, so unlike Philo and Plex a schedule is
technically in reach. It is deliberately not used. One page spans
about two and a half hours and pages forward through a `next` pointer,
so turning it into the airings-per-week figure this column holds would
mean either extrapolating a 2.5-hour window by a factor of 67, which
is precisely the invented cadence `_fast_lineup_common` warns against,
or walking the whole week nightly for what the research step treats as
one hint among several. Reporting no schedule signal is the honest
read, and it is the same zero MyFree DIRECTV and Plex Live TV report.

Standalone (from a US residential address):

    python3 -m scripts.trends_scrapers.sling_freestream
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Optional

from . import _fast_lineup_common as _flc
from ._base import run_scraper
from ._playwright import _lazy_playwright, _launch_browser

logger = logging.getLogger(__name__)

SOURCE = 'sling_freestream'
LABEL = 'Sling Freestream'

# The app's own front door. Loading it as an anonymous visitor mints
# the prospect session the guide read below rides on.
_APP_URL = 'https://watch.sling.com/'
_API_HOST = 'https://p-cmwnext-fast.movetv.com'

# Required by the API. It names both in its 400 body when either is
# absent. Los Angeles, matching the browser timezone set below so the
# two agree.
_TZ_OFFSET = '-0800'
_DMA = '803'

# Sling's own filter ids. `all_channels` is the full unauthenticated
# guide; `premium` is Sling's label for the paid add-on services
# carried inside it. See the module docstring for why the two
# free-branded filters are not used.
_FILTER_ALL = 'all_channels'
_FILTER_PREMIUM = 'premium'

# The guide's genre filters, in the order they should win when a
# channel appears under more than one. Narrow and specific first,
# because ENTERTAINMENT alone covers 252 of 577 and says little.
_GENRE_FILTERS = (
    'NEWS & OPINION', 'SPORTS', 'MOVIES', 'TRUE CRIME', 'COMEDY',
    'REALITY', 'MUSIC', 'KIDS & FAMILY', 'KIDS', 'GAMES & ANIME',
    'DOCS & HISTORY', 'DOCUMENTARY', 'SCIENCE & NATURE',
    'CLASSICS & RE-RUNS', 'ACTION & THRILLERS', 'LIFESTYLE',
    'BLACK ENTERTAINMENT', 'SPANISH', 'NEWS', 'ENTERTAINMENT',
)

# Sling writes LOCAL into the name of a single-market broadcast feed
# ("FOX 5 LOCAL New York"). That is the publisher saying so, not an
# inference off the name, and it keeps a one-market news feed from
# being priced as a national rail, which is the defining error on a
# lineup that carries both.
_LOCAL_MARKER = ' LOCAL '

_NAV_TIMEOUT_MS = 90_000
_SESSION_WAIT_MS = 18_000

# Must be set on the context. Playwright's default advertises
# HeadlessChrome, and Sling's app stops after its own config fetches
# without ever asking for a token when it sees that, which surfaces
# here as the same empty-token failure a geo-blocked run gives. A
# current desktop Chrome string is enough; nothing else about the
# browser needs disguising.
_CONTEXT_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/131.0.0.0 Safari/537.36')

# One page of JS that runs inside the authenticated page: optionally
# set a filter, then read the guide back. Returning the raw text keeps
# the JSON parsing on the Python side.
_GUIDE_JS = """async (args) => {
    const [token, filterId, tz, dma] = args;
    const headers = {
        'authorization': 'Bearer ' + token,
        'accept': 'application/json',
        'content-type': 'application/json',
        'timezone': tz,
        'dma': dma,
    };
    const base = 'https://p-cmwnext-fast.movetv.com';
    if (filterId) {
        await fetch(base + '/config/v2/grid_guide/filter', {
            method: 'POST', headers: headers,
            body: JSON.stringify({ filter_id: filterId }) });
    }
    const r = await fetch(base + '/pres/grid_guide', { headers: headers });
    return { status: r.status, text: await r.text() };
}"""


class _Session:
    """A loaded Sling page plus the bearer token it minted."""

    def __init__(self, page: Any, token: str) -> None:
        self.page = page
        self.token = token

    def guide(self, filter_id: Optional[str] = None) -> dict:
        res = self.page.evaluate(
            _GUIDE_JS, [self.token, filter_id or '', _TZ_OFFSET, _DMA])
        status = (res or {}).get('status')
        if status != 200:
            raise RuntimeError(
                f'Sling guide answered HTTP {status} for filter '
                f'{filter_id or "(none)"}')
        try:
            payload = json.loads((res or {}).get('text') or '')
        except ValueError as e:
            raise RuntimeError(
                f'Sling guide payload did not parse: {e}') from e
        if payload.get('status_code') == 401:
            raise RuntimeError(
                'Sling guide refused the session token; the prospect '
                'session did not establish')
        return payload

    def channel_names(self, filter_id: Optional[str] = None) -> list[str]:
        payload = self.guide(filter_id)
        return [str((c or {}).get('title') or '').strip()
                 for c in (payload.get('channels') or [])
                 if (c or {}).get('title')]


def _filter_ids(payload: dict) -> dict[str, str]:
    """Filter title to filter id, as the guide publishes them."""
    actions = (payload.get('grid_actions') or {}).get('FILTER_ACTIONS') or []
    out: dict[str, str] = {}
    for f in actions:
        title = str((f or {}).get('title') or '').strip().upper()
        fid = str(((f or {}).get('payload') or {}).get('filter_id') or '')
        if title and fid:
            out[title] = fid
    return out


def _collect(session: _Session) -> tuple[list[str], set[str], dict[str, str]]:
    """Free channel names, the paid set that was removed, and the
    publisher genre per channel."""
    opening = session.guide()
    filters = _filter_ids(opening)

    all_names = session.channel_names(
        filters.get(_FILTER_ALL.upper()) or _FILTER_ALL)
    if not all_names:
        raise RuntimeError('Sling guide returned no channels')

    # Sling's own paid tag. If the tag ever disappears we must not
    # silently ship the paid services onto a free rail.
    premium_key = _FILTER_PREMIUM.upper()
    if premium_key not in filters:
        raise RuntimeError(
            f'Sling guide carried {len(all_names)} channels and no '
            f'"{premium_key}" filter; that filter is how the paid add-on '
            f'services are told apart from the free lineup, so without it '
            f'there is no safe way to publish this rail')
    paid = set(session.channel_names(filters[premium_key]))

    free = [n for n in all_names if n not in paid]

    genre_of: dict[str, str] = {}
    for title in _GENRE_FILTERS:
        fid = filters.get(title)
        if not fid:
            continue
        try:
            names = session.channel_names(fid)
        except RuntimeError as e:
            # A genre is a hint, so losing one costs labels only.
            logger.warning('sling_freestream: genre filter %s failed (%s)',
                            title, e)
            continue
        for name in names:
            if name not in genre_of:
                genre_of[name] = title

    return free, paid, genre_of


def fetch() -> dict[str, Any]:
    sync_playwright = _lazy_playwright()
    if sync_playwright is None:
        raise RuntimeError(
            'playwright is required for the Sling Freestream guide: the '
            'session token is signed by the web app and is not something '
            'this repo reproduces')

    with sync_playwright() as pw:
        browser, flavor = _launch_browser(pw)
        try:
            ctx = browser.new_context(
                locale='en-US',
                # Agrees with the timezone header sent above.
                timezone_id='America/Los_Angeles',
                viewport={'width': 1440, 'height': 900},
                user_agent=_CONTEXT_UA,
            )
            page = ctx.new_page()

            token: dict[str, str] = {}

            def _on_request(req: Any) -> None:
                auth = req.headers.get('authorization') or ''
                if auth.startswith('Bearer ') and 'cmwnext' in req.url:
                    token['bearer'] = auth[len('Bearer '):]

            page.on('request', _on_request)
            page.goto(_APP_URL, wait_until='load', timeout=_NAV_TIMEOUT_MS)
            page.wait_for_timeout(_SESSION_WAIT_MS)

            bearer = token.get('bearer') or ''
            if not bearer:
                raise RuntimeError(
                    'Sling never minted a session token; the app did not '
                    'reach its API, which is what a geo-blocked run looks '
                    'like from here, so check this is running from a US '
                    'residential connection')

            logger.info('sling_freestream: prospect session established '
                         '(%s)', flavor)
            free, paid, genre_of = _collect(_Session(page, bearer))
        finally:
            browser.close()

    rows: list[dict] = []
    seen: set[str] = set()
    for name in free:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append(_flc.channel_row(
            name,
            # See the module docstring: a schedule exists but a
            # 2.5-hour page is not a weekly rate.
            airings=0,
            source_genre=genre_of.get(name, ''),
            scope=('local' if _LOCAL_MARKER in f' {name.upper()} '
                    else 'national'),
        ))

    if not rows:
        raise RuntimeError(
            'Sling guide carried channels but none survived the paid-tier '
            'exclusion')

    labeled = sum(1 for r in rows if r['source_genre'])
    local = sum(1 for r in rows if r['scope'] == 'local')
    logger.info('sling_freestream: %d free channels (%d carry a publisher '
                 'genre, %d single-market); %d paid add-on services left '
                 'out', len(rows), labeled, local, len(paid))

    return _flc.lineup_payload(LABEL, rows, extra={
        'label': LABEL,
        'kind': 'fast',
        'paid_channels_excluded': len(paid),
        'guide_dma': _DMA,
    })


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SOURCE, LABEL, 'fast', fetch)
    n = len(result.get('channels') or [])
    print(f'sling_freestream  channels={n}  '
           f'local={result.get("channels_local")}  '
           f'paid_excluded={result.get("paid_channels_excluded")}  '
           f'err={result.get("error")}', file=sys.stderr)
    return 0 if n >= 300 else 1


if __name__ == '__main__':
    sys.exit(main())
