"""
Vizio WatchFree+ channel lineup for the FAST Channel Ranker.

WatchFree+ is the free ad-supported service built into every Vizio
SmartCast TV (Walmart acquired Vizio in late 2024 and is rolling
SmartCast onto Onn-branded sets as well). It has no JustWatch package,
so there is no titles catalogue to rank and this platform ships the
Channel Ranker only.

Two public endpoints, both unauthenticated, both reachable from the
build box (verified byte-identical against a US residential IP, so
this does NOT need the residential batch):

    /api/channels
        The whole lineup. Every row is typed FAST and carries Vizio's
        own category label, its guide key, and - for the local
        broadcast feeds - the list of ZIP codes it is carried in.

    /api/airings/?start=&end=&startChannel=&channelCount=
        The guide. One row per airing, keyed to the channel by
        `stationId`, which matches the channel's `airingsKey` and
        NOT its numeric `channelId` (that one is a CDN identifier).

Two things about this data shape are worth knowing before changing
anything here.

**The guide is short.** Vizio publishes about 22 hours ahead and
ignores a longer request window: asking for 168 hours returns the same
rows as asking for 24. So the airings count is converted to a weekly
rate against the span each channel actually covers, via
`_fast_lineup_common.weekly_airings`, and lands at a median around
110 a week against the workbook platforms' ~200. That gap is a real
difference in how Vizio programmes, not a unit mismatch. Roughly 85%
of channels carry a schedule; the rest are live passthrough feeds
(FIFA+, fubo Sports, conference networks) that report a single
open-ended block and so ship no schedule signal at all.

**A fifth of the lineup is local.** 88 channels sit in Vizio's LOCAL
CHANNELS category and 86 of those name the ZIP codes they reach, which
is a few hundred ZIPs each rather than the country. They are marked
`scope='local'` with their carriage size attached so the research
step prices them against the markets they actually reach. Pricing a
Dallas broadcast feed as though it were a national channel is the
single easiest way to make this rail wrong.

Standalone:

    python3 -m scripts.trends_scrapers.vizio_watchfree
"""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import request as _urllib_request

from . import _fast_lineup_common as _flc
from ._base import run_scraper

logger = logging.getLogger(__name__)

SOURCE = 'vizio_watchfree'
LABEL = 'Vizio WatchFree+'

_CHANNELS_URL = 'https://watchfreeplus-epg-prod.smartcasttv.com/api/channels'
_AIRINGS_URL = 'https://watchfreeplus-epg-prod.smartcasttv.com/api/airings/'

# The app's own client string. The endpoint is anonymous but answers a
# browser UA with a different (thinner) payload, so keep this as-is.
_HEADERS = {
    'User-Agent': 'okhttp/4.12.0',
    'Accept': 'application/json',
}

# Asking for more than this changes nothing (verified: a 168-hour
# request returns the same rows as a 24-hour one), so we ask for a day
# and measure what actually comes back.
_GUIDE_WINDOW_HOURS = 24

# Vizio's merchandising shelf, not a genre. A channel in FEATURED also
# appears under its real category, so the duplicate collapses to the
# categorised copy.
_MERCH_CATEGORY = 'FEATURED'

_TIMEOUT_CHANNELS = 45
_TIMEOUT_AIRINGS = 180



def _get_json(url: str, timeout: int) -> Any:
    req = _urllib_request.Request(url, headers=_HEADERS)
    with _urllib_request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))


def _fetch_channels() -> list[dict]:
    data = _get_json(_CHANNELS_URL, _TIMEOUT_CHANNELS)
    rows = (data or {}).get('channels') or []
    logger.info("vizio_watchfree: %d channels in the lineup", len(rows))
    return [r for r in rows if isinstance(r, dict)]


def _fetch_guide(channels: list[dict]) -> list[dict]:
    """Every airing in one call. `startChannel` takes the first
    channel's numeric id and `channelCount` the size of the lineup;
    the endpoint returns the whole grid rather than a page."""
    if not channels:
        return []
    now = datetime.now(timezone.utc)
    start = now.strftime('%Y-%m-%dT%H:%M:%S.000Z')
    end = (now + timedelta(hours=_GUIDE_WINDOW_HOURS)
            ).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    url = (f'{_AIRINGS_URL}?start={start}&end={end}'
            f'&startChannel={channels[0].get("id")}'
            f'&channelCount={len(channels)}')
    try:
        data = _get_json(url, _TIMEOUT_AIRINGS)
    except Exception as e:  # noqa: BLE001
        # The lineup alone still makes a usable ranker: every channel
        # is present and the research step prices it from the channel
        # rather than the schedule. Losing the guide costs a hint.
        logger.warning("vizio_watchfree: guide fetch failed (%s); the "
                        "lineup ships without a schedule signal", e)
        return []
    airings = (data or {}).get('airings') or []
    logger.info("vizio_watchfree: %d airings across the published guide",
                 len(airings))
    return [a for a in airings if isinstance(a, dict)]


def _weekly_by_guide_key(airings: list[dict]) -> dict[str, int]:
    """Airings per week per channel, measured against the span each
    channel's own guide covers rather than the window we requested."""
    starts: dict[str, list[float]] = defaultdict(list)
    ends: dict[str, list[float]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    for a in airings:
        key = a.get('stationId')
        if not key:
            continue
        counts[key] += 1
        # Epoch fields are milliseconds.
        s, e = a.get('epochTimeStart'), a.get('epochTimeEnd')
        if s:
            starts[key].append(float(s) / 1000.0)
        if e:
            ends[key].append(float(e) / 1000.0)
    out: dict[str, int] = {}
    for key, n in counts.items():
        hours = _flc.covered_hours(starts.get(key) or [],
                                     ends.get(key) or [])
        out[key] = _flc.weekly_airings(n, hours)
    return out


def _dedupe(channels: list[dict]) -> list[dict]:
    """One row per channel name. A channel promoted to the
    merchandising shelf appears twice; keep the categorised copy."""
    best: dict[str, dict] = {}
    for c in channels:
        name = (c.get('channelName') or '').strip()
        if not name:
            continue
        key = name.lower()
        cur = best.get(key)
        if cur is None:
            best[key] = c
            continue
        cur_merch = (cur.get('category') or '') == _MERCH_CATEGORY
        new_merch = (c.get('category') or '') == _MERCH_CATEGORY
        if cur_merch and not new_merch:
            best[key] = c
    return list(best.values())


def fetch() -> dict[str, Any]:
    channels = _fetch_channels()
    if not channels:
        raise RuntimeError('Vizio WatchFree+ returned no channels')

    weekly = _weekly_by_guide_key(_fetch_guide(channels))

    rows: list[dict] = []
    for c in _dedupe(channels):
        category = (c.get('category') or '').strip()
        zips = c.get('zipCodes') or []
        is_local = category.upper() == 'LOCAL CHANNELS' or bool(zips)
        rows.append(_flc.channel_row(
            c.get('channelName') or '',
            airings=weekly.get(c.get('airingsKey') or '', 0),
            content_type=(c.get('channelType') or '').strip(),
            # FEATURED is a shelf, so it carries no genre meaning and
            # is dropped rather than passed to the classifier.
            source_genre='' if category == _MERCH_CATEGORY else category,
            scope='local' if is_local else 'national',
            dma_zip_count=len(zips) if isinstance(zips, list) else 0,
        ))

    return _flc.lineup_payload(LABEL, rows, extra={
        'label': LABEL,
        'kind': 'fast',
        'guide_window_hours': _GUIDE_WINDOW_HOURS,
    })


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SOURCE, LABEL, 'fast', fetch)
    n = len(result.get('channels') or [])
    print(f'vizio_watchfree  channels={n}  '
           f'scheduled={result.get("channels_scheduled")}  '
           f'local={result.get("channels_local")}  '
           f'err={result.get("error")}', file=sys.stderr)
    return 0 if n >= 200 else 1


if __name__ == '__main__':
    sys.exit(main())
