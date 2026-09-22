"""
MyFree DIRECTV channel lineup for the FAST Channel Ranker.

WHAT THIS COVERS, AND WHAT IT DELIBERATELY DOES NOT.

MyFree DIRECTV is DIRECTV's free ad-supported service: no
subscription, no card, around 150 linear channels, launched November
2024. That is a FAST service on the same definition as Pluto TV and
Tubi, so it belongs on this tab.

DIRECTV satellite and DIRECTV Stream are paid pay-TV and do NOT belong
here. Putting a paid subscription product on a free-streaming tab
beside Roku and Pluto would be a category error, and the rail is
labelled "MyFree DIRECTV" rather than "DIRECTV" so the tab never reads
as a ranking of the paid service.

The split is DIRECTV's own, not ours. The published lineup carries all
520 channels with an `isMyFreeDTV` boolean on each, and we take only
the 161 rows carrying it. If DIRECTV moves a channel between tiers,
this rail follows without anyone re-deciding where the line sits.

There is no JustWatch package, so no titles catalogue, and this
platform ships the Channel Ranker only.

LOOKING LIKE A BROWSER MATTERS. A plain HTTP client gets 403 from
directv.com whatever address it comes from, which reads like a
datacentre block and is not one. Two things are being checked. The
header set has to be complete, sec-fetch and sec-ch-ua included, and
the TLS handshake has to look like Chrome, so this goes through
curl_cffi with Chrome impersonation the way the other Akamai-fronted
scrapers here do. Headers alone still 403 on a stock client.

With both in place the URL answers 200 from the build box and from a
residential address alike, returning the identical 161 rows, so this
runs on the nightly batch and needs no residential hop.

NO SCHEDULE. DIRECTV publishes a lineup, not a guide, so every channel
here reports no airings signal. That is honest rather than missing:
the research step prices these channels from the channel and the
platform, and the collector leaves the airings clause out of the
prompt rather than telling the model a channel airs nothing.

Standalone:

    python3 -m scripts.trends_scrapers.myfree_directv
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any, Optional

from . import _fast_lineup_common as _flc
from ._base import run_scraper

# Chrome's TLS fingerprint, not just Chrome's headers. See the module
# docstring: directv.com checks both.
try:
    from curl_cffi import requests as _cc_requests  # type: ignore
except ImportError:  # pragma: no cover - present everywhere we run
    _cc_requests = None  # type: ignore

logger = logging.getLogger(__name__)

SOURCE = 'myfree_directv'
LABEL = 'MyFree DIRECTV'

# DIRECTV's own published lineup, with the free tier flagged per row.
_LINEUP_URL = ('https://www.directv.com/channel-lineup/modal/'
                '?channels=internet&myFreeDtv=true')

# The complete set is what turns the 403 into a 200. Dropping the
# sec-fetch or sec-ch-ua headers puts it back.
_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/131.0.0.0 Safari/537.36'),
    'accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
                'image/avif,image/webp,*/*;q=0.8'),
    'accept-language': 'en-US,en;q=0.9',
    'accept-encoding': 'gzip, deflate',
    'sec-ch-ua': ('"Google Chrome";v="131", "Chromium";v="131", '
                   '"Not_A Brand";v="24"'),
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"macOS"',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'none',
    'sec-fetch-user': '?1',
    'upgrade-insecure-requests': '1',
}

_TIMEOUT = 60

_CHANNELS_KEY = re.compile(r'"channels"\s*:\s*\[')

# DIRECTV files the whole free tier under three headings. Entertainment
# carries 127 of the 161 and so says little about any one channel; it
# is dropped rather than handed to the classifier as though it were a
# genre. The other two are real and specific.
_UNINFORMATIVE_CATEGORIES = {'entertainment'}


def _fetch_html() -> str:
    if _cc_requests is None:
        raise RuntimeError(
            'curl_cffi is required for the DIRECTV lineup: a stock HTTP '
            'client is refused at the TLS handshake before the headers '
            'are read')
    r = _cc_requests.get(_LINEUP_URL, headers=_HEADERS,
                          impersonate='chrome', timeout=_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(
            f'DIRECTV lineup answered HTTP {r.status_code}')
    return r.text


def _match_array(html: str, start: int) -> Optional[str]:
    """Bracket-match the JSON array opening at or after `start`."""
    try:
        i = html.index('[', start)
    except ValueError:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(html)):
        c = html[j]
        if in_str:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                return html[i:j + 1]
    return None


def _extract_lineup(html: str) -> list[dict]:
    """The lineup ships as a JSON blob inside the server-rendered page.
    Several arrays are named `channels`; the lineup is the one whose
    rows carry the free-tier flag."""
    for m in _CHANNELS_KEY.finditer(html):
        frag = _match_array(html, m.start())
        if not frag:
            continue
        try:
            arr = json.loads(frag)
        except ValueError:
            continue
        if not isinstance(arr, list) or not arr:
            continue
        if any(isinstance(r, dict) and 'isMyFreeDTV' in r for r in arr):
            logger.info("myfree_directv: lineup blob carries %d channels",
                         len(arr))
            return [r for r in arr if isinstance(r, dict)]
    return []


def fetch() -> dict[str, Any]:
    html = _fetch_html()
    lineup = _extract_lineup(html)
    if not lineup:
        raise RuntimeError(
            'DIRECTV lineup page returned no channel blob; the page '
            'shape changed or the request was answered with a bot wall')

    seen: set[str] = set()
    rows: list[dict] = []
    for ch in lineup:
        if not ch.get('isMyFreeDTV'):
            continue          # paid tier: not a FAST channel
        name = str(ch.get('label') or '').strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        category = str(ch.get('categoryId') or '').strip()
        rows.append(_flc.channel_row(
            name,
            # DIRECTV publishes no schedule, so no airings signal.
            airings=0,
            source_genre=('' if category.lower() in _UNINFORMATIVE_CATEGORIES
                           else category),
            scope='national',
        ))

    if not rows:
        raise RuntimeError(
            f'DIRECTV lineup carried {len(lineup)} channels but none '
            f'flagged for the free tier')

    logger.info("myfree_directv: %d free-tier channels out of %d in the "
                 "published lineup", len(rows), len(lineup))
    return _flc.lineup_payload(LABEL, rows, extra={
        'label': LABEL,
        'kind': 'fast',
        'lineup_total': len(lineup),
    })


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SOURCE, LABEL, 'fast', fetch)
    n = len(result.get('channels') or [])
    print(f'myfree_directv  channels={n}  err={result.get("error")}',
           file=sys.stderr)
    return 0 if n >= 100 else 1


if __name__ == '__main__':
    sys.exit(main())
