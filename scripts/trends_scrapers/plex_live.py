"""
Plex Live TV channel lineup for the FAST Channel Ranker.

Plex Live TV is the free ad-supported linear side of Plex, running
since 2019 inside an app that also hosts a personal media server, an
AVOD catalog and premium rentals. It is free, needs no subscription
and no card, so it belongs on this tab. Plex Pass is a paid
subscription but it buys server features (DVR, hardware transcoding,
downloads) rather than a different channel lineup, so there is no paid
channel tier here to keep off the rail the way there is on Philo and
Sling.

Two endpoints, both public once a token exists:

    POST plex.tv/api/v2/users/anonymous
        Plex hands out an anonymous session to any client that
        identifies itself. Answers 201 with an `authToken`. No
        account, no card, no email. This is the app's own front door
        for a first-run visitor, which is why the lineup below is
        readable without credentials at all.

    GET epg.provider.plex.tv/lineups/plex/channels
        The whole lineup, one row per channel, each carrying Plex's
        virtual channel number, slug, summary, artwork and language.

RUNS RESIDENTIALLY, AND MUST STAY THAT WAY.

`epg.provider.plex.tv` resolves the lineup off the REQUEST IP and
fails in the worst possible way from the build box: HTTP 200 carrying
a complete, plausible, well-formed lineup for the wrong country. From
Hetzner it returns 254 channels titled "Plex Channels in DE", 59 of
them German-language, with rows like Taeterjagd and Stromberg. From a
US residential address the identical request returns 695 channels
titled "Plex Channels in US". Nothing in the response shape
distinguishes the two, so a misplaced run would quietly publish a
German rail under a US board rather than break.

That is the LG Channels trap one step worse, because LG at least
returns empty category lists when it geo-gates. Plex returns real
channels. So the guard here is not "did we get rows", it is "does the
lineup say US", asserted below, and this module sits in
`RESIDENTIAL_SCRAPERS` in `local_residential_run.py`. Do not move it
to the nightly batch.

Note that `?country=us` and `Accept-Language: en-US` are both ignored
by the endpoint; there is no header that overrides the IP. The only
fix is running from the right address.

NO SCHEDULE, AND NO PUBLISHER GENRE LABEL. Plex publishes a lineup
rather than a guide on this endpoint, so every channel reports no
airings signal, the same honest zero MyFree DIRECTV reports. Each row
does carry `genreRatingKeys`, but they are opaque hashes
(`genre_6a0c2728768cf9b31f6fe02a`) and Plex exposes no lookup that
resolves them to names, so they are not passed on: naming those
clusters ourselves would be inventing a publisher label rather than
carrying one. Channels therefore reach the channel-type classifier
with no label and get typed from the channel NAME, which is what that
classifier reads anyway.

The one publisher signal that IS unambiguous is `language`. 95 of the
695 US channels are Spanish-language, and that is exactly the
distinction the Espanol channel type exists to carry, so those rows
ship Vizio's own wording for the same thing and land on the existing
mapping rather than needing a new one.

Standalone (from a US residential address):

    python3 -m scripts.trends_scrapers.plex_live
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from typing import Any
from urllib import request as _urllib_request

from . import _fast_lineup_common as _flc
from ._base import run_scraper

logger = logging.getLogger(__name__)

SOURCE = 'plex_live'
LABEL = 'Plex Live TV'

_ANON_URL = 'https://plex.tv/api/v2/users/anonymous'
_CHANNELS_URL = 'https://epg.provider.plex.tv/lineups/plex/channels'

# Plex requires a client identity on every call and refuses anonymous
# provisioning without one. A fresh identifier per run keeps us from
# accumulating server-side state against one id.
def _client_headers() -> dict[str, str]:
    return {
        'Accept': 'application/json',
        'X-Plex-Product': 'Plex Web',
        'X-Plex-Version': '4.145.1',
        'X-Plex-Client-Identifier': str(uuid.uuid4()),
        'X-Plex-Platform': 'Chrome',
        'X-Plex-Platform-Version': '131.0',
        'X-Plex-Device': 'Windows',
        'X-Plex-Device-Name': 'Plex Web (Chrome)',
        'X-Plex-Model': 'standalone',
        'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/131.0.0.0 Safari/537.36'),
    }


_TIMEOUT_TOKEN = 45
_TIMEOUT_CHANNELS = 90

# The lineup names its own country, which is the only reliable way to
# tell a geo-gated response from the real one. See the module
# docstring.
_US_LINEUP_TITLE = re.compile(r'\bUS\b|\bUnited States\b', re.I)

# Plex tags a handful of channel names with a distribution marker
# ("365BLK [FAST]"). It is not part of the channel's name.
_NAME_SUFFIX = re.compile(r'\s*\[(?:FAST|LIVE|LINEAR)\]\s*$', re.I)

# Vizio's own wording for a Spanish-language channel, reused so these
# rows land on the existing channel-type mapping instead of needing a
# new key. See the module docstring.
_ESPANOL_LABEL = 'EN ESPANOL'
_ESPANOL_LANGS = {'es'}


def _post_json(url: str, timeout: int) -> Any:
    req = _urllib_request.Request(url, data=b'', headers=_client_headers(),
                                   method='POST')
    with _urllib_request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _get_json(url: str, token: str, timeout: int) -> Any:
    headers = _client_headers()
    headers['X-Plex-Token'] = token
    req = _urllib_request.Request(url, headers=headers)
    with _urllib_request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _anonymous_token() -> str:
    payload = _post_json(_ANON_URL, _TIMEOUT_TOKEN)
    token = (payload or {}).get('authToken') or ''
    if not token:
        raise RuntimeError(
            'Plex did not return an anonymous token; the provisioning '
            'endpoint changed or is refusing this client identity')
    return token


def _fetch_lineup(token: str) -> tuple[str, list[dict]]:
    payload = _get_json(_CHANNELS_URL, token, _TIMEOUT_CHANNELS)
    container = (payload or {}).get('MediaContainer') or {}
    title = str(container.get('title') or '')
    channels = [c for c in (container.get('Channel') or [])
                 if isinstance(c, dict)]
    return title, channels


def fetch() -> dict[str, Any]:
    title, channels = _fetch_lineup(_anonymous_token())

    if not channels:
        raise RuntimeError(
            f'Plex returned no channels (lineup title {title!r})')

    # The country assertion. A geo-gated response is a full, healthy
    # looking lineup for somewhere else, so this is the check that
    # matters and it has to run before anything is written.
    if not _US_LINEUP_TITLE.search(title):
        raise RuntimeError(
            f'Plex returned the lineup titled {title!r} carrying '
            f'{len(channels)} channels, which is not the US lineup; this '
            f'endpoint resolves country from the request IP and returns a '
            f'complete, plausible lineup for the wrong country, so this '
            f'scraper has to run from a US residential connection')

    rows: list[dict] = []
    seen: set[str] = set()
    for ch in channels:
        name = _NAME_SUFFIX.sub('', str(ch.get('title') or '')).strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        language = str(ch.get('language') or '').strip().lower()
        rows.append(_flc.channel_row(
            name,
            # Plex publishes no schedule on this endpoint.
            airings=0,
            # `genreRatingKeys` are opaque and Plex resolves no names
            # for them, so language is the only publisher label here.
            source_genre=(_ESPANOL_LABEL if language in _ESPANOL_LANGS
                           else ''),
            scope='national',
        ))

    espanol = sum(1 for r in rows if r['source_genre'] == _ESPANOL_LABEL)
    logger.info('plex_live: %d channels from %r (%d Spanish-language)',
                 len(rows), title, espanol)

    return _flc.lineup_payload(LABEL, rows, extra={
        'label': LABEL,
        'kind': 'fast',
        'lineup_title': title,
    })


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SOURCE, LABEL, 'fast', fetch)
    n = len(result.get('channels') or [])
    print(f'plex_live  channels={n}  '
           f'lineup={result.get("lineup_title")!r}  '
           f'err={result.get("error")}', file=sys.stderr)
    return 0 if n >= 400 else 1


if __name__ == '__main__':
    sys.exit(main())
