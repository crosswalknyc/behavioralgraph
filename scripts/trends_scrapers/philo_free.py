"""
Philo Free channel lineup for the FAST Channel Ranker.

WHAT THIS COVERS, AND WHAT IT DELIBERATELY DOES NOT.

Philo Free Channels is Philo's standalone free ad-supported tier: no
subscription, no payment card, no account needed to start watching,
around 150 linear channels by Philo's own count. That is a FAST
service on the same definition as Pluto TV and Tubi, so it belongs on
this tab.

Philo's PAID vMVPD plans do NOT belong here and are excluded on
purpose. Essential is $25 a month for 70+ cable networks, Bundle+ is
$33 and adds HBO Max, AMC+ and discovery+, and there are per-month
add-ons on top (STARZ $12, MGM+ $8, Movies & More $3). Putting any of
those on a free-streaming tab beside Roku and Pluto would be the same
category error MyFree DIRECTV avoids by never calling itself DIRECTV,
which is why this rail is labelled "Philo Free" and never "Philo".

THE SAME MISTAKE IS AVAILABLE FROM THE CATALOG SOURCE, SO DO NOT TAKE
IT. The catalog source carries exactly one US Philo package, `phl`
(id 2383), and its monetization is FLATRATE, meaning it describes the
PAID subscription and not this free tier. Its depth confirms it: 8,375
films and 3,561 shows led by current paid-window titles, which is the
Essential and Bundle+ on-demand library, not a free FAST lineup. There
is no free-tier Philo package to switch to. A future reader who
notices a Philo package exists there and "fixes" this scraper to use
it would silently put a paid vMVPD catalog on the free rail. Do not.
This platform ships the Channel Ranker only, and that is correct
rather than incomplete.

THE FREE / PAID LINE IS PHILO'S, NOT OURS. `help.philo.com/channels`
renders one `channel-grouping` block per plan, each carrying its own
id, in document order:

    base-package     Essential, $25/month        PAID
    bundle-channels  Bundle+, $33/month          PAID
    free-channels    Free Channels               FREE  <- the only one taken
    add-ons          STARZ / MGM+ / Movies       PAID

Slicing the document between consecutive group ids gives each plan's
channel set as Philo publishes it, so when Philo moves a channel
between tiers this rail follows without anyone re-deciding where the
line sits. Same shape as DIRECTV's `isMyFreeDTV` flag.

Two channels (HSN and QVC) sit in both the free group and a paid one,
because the shopping channels are carried on every tier. They are
free-tier available, so membership in the free group is what counts
and they ship.

GENRE COMES FROM THE OTHER PAGE, AND ONLY AS A HINT. The free-channels
landing page carries `channelGenreGroups`, which is Philo's own genre
shelving. It covers the WHOLE catalog, paid channels included, because
Philo lets a free viewer browse what an upgrade would add. So it is
joined by channel name and applied ONLY to channels that already
passed the free-group membership test above. It can never widen the
lineup, only label it. Coverage lands near 60% because the shelves
lean toward the paid networks; the rest reach the channel-type
classifier with no publisher label, which is the same position 127 of
MyFree DIRECTV's 161 channels are in, and that classifier reads the
channel NAME anyway.

NO SCHEDULE. Philo publishes a lineup and not a public guide, so every
channel here reports no airings signal. That is honest rather than
missing: the research step prices these channels from the channel and
the platform, and the collector leaves the airings clause out of the
prompt rather than telling the model a channel airs nothing.

Runs on the nightly batch. Both pages answer 200 from the build box
with the identical content a US residential address gets, so unlike
Plex Live TV and Sling Freestream (both geo-gated) this one needs no
residential hop.

Standalone:

    python3 -m scripts.trends_scrapers.philo_free
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import sys
from typing import Any, Optional
from urllib import request as _urllib_request

from . import _fast_lineup_common as _flc
from ._base import run_scraper

logger = logging.getLogger(__name__)

SOURCE = 'philo_free'
LABEL = 'Philo Free'

# The plan-by-plan lineup. This is the membership source.
_LINEUP_URL = 'https://help.philo.com/channels'
# The free-tier landing page. This is the genre source only.
_LANDING_URL = 'https://www.philo.com/go/free-channels'

_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/131.0.0.0 Safari/537.36'),
    'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
                'image/avif,image/webp,*/*;q=0.8'),
    'Accept-Language': 'en-US,en;q=0.9',
}

_TIMEOUT = 60

# Philo's id for the free tier's channel-grouping block. Everything
# else on that page is a paid plan or an add-on.
_FREE_GROUP_ID = 'free-channels'

# Where the plan groups stop. The footer follows the last one.
_GROUPS_END_MARKER = 'id="philo-footer"'

_GROUP_OPEN = re.compile(
    r'<div[^>]*\bclass="[^"]*channel-grouping[^"]*"[^>]*\bid="([a-z0-9\-]+)"')
_TILE_TITLE = re.compile(
    r'class="channel-tile"[^>]*>\s*<img[^>]*?\btitle="([^"]*)"')

# Merchandising shelves on the landing page, not genres. A channel
# promoted onto one of these also appears under its real genre, so the
# shelf is skipped and the genre wins.
_MERCH_SHELVES = {'popular', 'featured', 'recommended'}


def _get_html(url: str) -> str:
    req = _urllib_request.Request(url, headers=_HEADERS)
    with _urllib_request.urlopen(req, timeout=_TIMEOUT) as r:
        return r.read().decode('utf-8', 'replace')


def _clean(text: str) -> str:
    return _html.unescape(re.sub(r'<[^>]+>', '', text or '')).strip()


def _match_array(text: str, start: int) -> Optional[str]:
    """Bracket-match the JSON array opening at or after `start`."""
    try:
        i = text.index('[', start)
    except ValueError:
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(text)):
        c = text[j]
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
                return text[i:j + 1]
    return None


def _plan_groups(html: str) -> dict[str, list[str]]:
    """One channel-name list per plan, keyed by Philo's own group id."""
    marks = [(m.start(), m.group(1)) for m in _GROUP_OPEN.finditer(html)]
    if not marks:
        return {}
    end = html.find(_GROUPS_END_MARKER)
    marks.append((end if end > 0 else len(html), '__end__'))
    groups: dict[str, list[str]] = {}
    for (pos, gid), (next_pos, _next_gid) in zip(marks, marks[1:]):
        names: list[str] = []
        seen: set[str] = set()
        for raw in _TILE_TITLE.findall(html[pos:next_pos]):
            name = _clean(raw)
            key = name.lower()
            if name and key not in seen:
                seen.add(key)
                names.append(name)
        if names:
            groups[gid] = names
    return groups


def _genre_by_name(html: str) -> dict[str, str]:
    """Philo's genre shelving, normalized to lowercase channel name.

    Covers the whole catalog, so callers must gate on free-tier
    membership before applying it.
    """
    m = re.search(r'"channelGenreGroups"\s*:', html)
    if not m:
        logger.warning('philo_free: landing page carried no genre groups; '
                        'the lineup ships without publisher genre hints')
        return {}
    frag = _match_array(html, m.end())
    if not frag:
        return {}
    try:
        shelves = json.loads(frag)
    except ValueError as e:
        logger.warning('philo_free: genre groups did not parse (%s)', e)
        return {}
    out: dict[str, str] = {}
    for shelf in shelves:
        title = str((shelf or {}).get('title') or '').strip()
        if not title or title.lower() in _MERCH_SHELVES:
            continue
        for ch in (shelf or {}).get('channels') or []:
            name = str((ch or {}).get('name') or '').strip().lower()
            # First real shelf wins so the label stays stable run to run.
            if name and name not in out:
                out[name] = title
    return out


def fetch() -> dict[str, Any]:
    groups = _plan_groups(_get_html(_LINEUP_URL))
    if not groups:
        raise RuntimeError(
            'Philo lineup page returned no channel groups; the page shape '
            'changed or the request was answered with a bot wall')

    free = groups.get(_FREE_GROUP_ID) or []
    if not free:
        raise RuntimeError(
            f'Philo lineup page carried {len(groups)} plan groups '
            f'({", ".join(sorted(groups))}) but none with id '
            f'"{_FREE_GROUP_ID}"; the free tier is what this rail is, so '
            f'shipping the paid groups instead is never the fallback')

    paid_total = sum(len(v) for k, v in groups.items()
                      if k != _FREE_GROUP_ID)

    # Genre is a hint and must never widen the lineup, so a failure
    # here costs labels and nothing else.
    try:
        genres = _genre_by_name(_get_html(_LANDING_URL))
    except Exception as e:  # noqa: BLE001
        logger.warning('philo_free: genre fetch failed (%s); the lineup '
                        'ships without publisher genre hints', e)
        genres = {}

    rows = [
        _flc.channel_row(
            name,
            # Philo publishes no schedule, so no airings signal.
            airings=0,
            source_genre=genres.get(name.lower(), ''),
            scope='national',
        )
        for name in free
    ]

    labeled = sum(1 for r in rows if r['source_genre'])
    logger.info('philo_free: %d free-tier channels (%d carry a publisher '
                 'genre); %d channels across the paid plans left out',
                 len(rows), labeled, paid_total)

    return _flc.lineup_payload(LABEL, rows, extra={
        'label': LABEL,
        'kind': 'fast',
        'paid_channels_excluded': paid_total,
        'plan_groups': {k: len(v) for k, v in groups.items()},
    })


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SOURCE, LABEL, 'fast', fetch)
    n = len(result.get('channels') or [])
    print(f'philo_free  channels={n}  '
           f'paid_excluded={result.get("paid_channels_excluded")}  '
           f'err={result.get("error")}', file=sys.stderr)
    return 0 if n >= 100 else 1


if __name__ == '__main__':
    sys.exit(main())
