"""
LG Channels channel lineup for the FAST Channel Ranker.

LG Channels is LG's own free ad-supported service, built into webOS
sets. It is NOT Xumo Play, which the Trends board already carries
separately: Xumo powered the service under an enterprise deal from
2016, but that ended, and LG has run it as a proprietary platform
since. Xumo Enterprise still supplies individual channels into LG
Channels the same way it supplies Vizio and Samsung, so three channels
in this lineup carry Xumo branding. That is channel syndication, not a
duplicate service, and the Channel Ranker already treats the same
channel on two platforms as two audiences on two rails.

There is no JustWatch package, so there is no titles catalogue to rank
and this platform ships the Channel Ranker only.

RUNS RESIDENTIALLY, and must stay that way.

`api.lgchannels.com` is geo-gated. From the build box it answers 200
with a category skeleton and an empty channel list inside every one of
the 19 categories, which is worse than an error because it looks like
a successful fetch. From a US residential address the same request
returns the full lineup. That is why this module sits in
`RESIDENTIAL_SCRAPERS` in `local_residential_run.py` alongside Hulu,
BritBox, MGM+ and Starz, and why moving it to the nightly batch would
quietly empty the rail rather than break it. `fetch` raises on an
empty lineup so a geo-gated run fails loudly instead of publishing
nothing over something.

SCOPE OF THE RAIL. 191 channels is the web-accessible lineup, which is
a subset of what an LG set shows on its own guide. The rail is scoped
and labelled to what we can actually see rather than implying the
whole on-TV lineup.

The response is base64 over zlib rather than JSON, hence the decode
step. It carries programmes inline, and the horizon is short, about 13
hours, so the airings count is converted to a weekly rate against the
span each channel actually covers. See `_fast_lineup_common`.

Standalone (from a US residential address):

    python3 -m scripts.trends_scrapers.lg_channels
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import sys
import zlib
from datetime import datetime
from typing import Any
from urllib import request as _urllib_request

from . import _fast_lineup_common as _flc
from ._base import run_scraper

logger = logging.getLogger(__name__)

SOURCE = 'lg_channels'
LABEL = 'LG Channels'

_SCHEDULE_URL = 'https://api.lgchannels.com/api/v1.0/schedulelist'

# The web client's own header set. The device headers decide which
# country's lineup comes back; a plain request without them answers
# 406.
_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/131.0.0.0 Safari/537.36'),
    'Accept': 'application/json, text/plain, */*',
    'Origin': 'https://lgchannels.com',
    'Referer': 'https://lgchannels.com/',
    'x-device-country': 'US',
    'x-device-language': 'en',
    'x-device-type': 'WEB',
}

_TIMEOUT = 60


def _decode(body: bytes) -> dict[str, Any]:
    """The endpoint answers base64 over zlib with a text/plain content
    type. Plain JSON is accepted too, so a future switch to an
    uncompressed body does not break the scraper."""
    text = body.decode('utf-8', 'replace').strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return json.loads(zlib.decompress(base64.b64decode(text)).decode('utf-8'))
    except (binascii.Error, UnicodeDecodeError, ValueError, zlib.error) as e:
        raise RuntimeError(f'LG Channels payload did not decode: {e}') from e


def _fetch_schedule() -> dict[str, Any]:
    req = _urllib_request.Request(_SCHEDULE_URL, headers=_HEADERS)
    with _urllib_request.urlopen(req, timeout=_TIMEOUT) as r:
        payload = _decode(r.read())
    if not isinstance(payload, dict):
        raise RuntimeError('LG Channels payload was not an object')
    return payload


def _epoch(value: Any) -> float:
    """LG stamps times as `2026-09-22T14:00:00Z`."""
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(
            str(value).replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError):
        return 0.0


def fetch() -> dict[str, Any]:
    payload = _fetch_schedule()
    categories = payload.get('categories') or []

    # A channel can be listed under more than one category; first
    # listing wins so the category label stays stable run to run.
    seen: dict[str, dict] = {}
    for cat in categories:
        cat_name = str((cat or {}).get('categoryName') or '').strip()
        for ch in (cat or {}).get('channels') or []:
            cid = str((ch or {}).get('channelId') or '').strip()
            name = str((ch or {}).get('channelName') or '').strip()
            if not cid or not name or cid in seen:
                continue
            seen[cid] = {'ch': ch, 'category': cat_name}

    if not seen:
        # The geo-gated response is a full category list with every
        # channel list empty. Treat it as the failure it is.
        raise RuntimeError(
            f'LG Channels returned {len(categories)} categories and no '
            f'channels, which is what a non-US address gets back; this '
            f'scraper has to run from a US residential connection')

    rows: list[dict] = []
    for entry in seen.values():
        ch = entry['ch']
        programs = ch.get('programs') or []
        hours = _flc.covered_hours(
            [_epoch(p.get('startDateTime')) for p in programs],
            [_epoch(p.get('endDateTime')) for p in programs])
        rows.append(_flc.channel_row(
            ch.get('channelName') or '',
            airings=_flc.weekly_airings(len(programs), hours),
            # LG's per-channel genre is the finer label; the category
            # it is filed under is the coarser one. Prefer the finer.
            source_genre=(str(ch.get('channelGenreName') or '').strip()
                           or entry['category']),
            scope='national',
        ))

    return _flc.lineup_payload(LABEL, rows, extra={
        'label': LABEL,
        'kind': 'fast',
        'categories_total': len(categories),
        'guide_timestamp': payload.get('timestamp') or '',
    })


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SOURCE, LABEL, 'fast', fetch)
    n = len(result.get('channels') or [])
    print(f'lg_channels  channels={n}  '
           f'scheduled={result.get("channels_scheduled")}  '
           f'err={result.get("error")}', file=sys.stderr)
    return 0 if n >= 100 else 1


if __name__ == '__main__':
    sys.exit(main())
