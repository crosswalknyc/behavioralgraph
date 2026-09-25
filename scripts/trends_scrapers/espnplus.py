"""
ESPN+ trending scraper.

ESPN+ programming lives at `disneyplus.com/browse/espn` under the
Disney bundle. It's a public catalog page - no auth cookies required
to render the content. Uses the exact same stitchDocument parser as
the Disney+ scraper (see `disneyplus.py`).

IP-gate note: same story as Disney+ - Bamgrid IP-gates datacenter
ranges. Run this scraper from a residential IP (Jenna's laptop) or
a residential proxy. See `local_residential_run.py`.

Standalone:
    python3 -m scripts.trends_scrapers.espnplus
"""

from __future__ import annotations

import logging
import re
import sys
from html import unescape
from typing import Any

from ._base import run_scraper, http_get
from ._playwright import render_pages
from .disneyplus import (_extract_disneyplus, _extract_disneyplus_dom,
                          _BAMGRID_ERROR_MARKER)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# Reading a programme name off an ESPN tile
# ────────────────────────────────────────────────────────────────────
# /browse/espn is not the Disney+ catalog page its parser was written
# for. Three of its eleven rails are the live schedule, and a schedule
# tile's accessible name is the whole tile read aloud:
#
#   "LIVE Started 23 minutes ago The Pat McAfee Show ESPN
#    The Pat McAfee Show Choose Feed Entry"
#   "Upcoming 9:00 AM 9:00 AM - 3:00 PM Mecum Auctions: Nashville 2026
#    (Day 2) ESPN+ Mecum Auctions Released 2026."
#
# Taking that whole string as the title put a schedule blurb on the
# board, and a blurb is not a name: nothing can be priced against it,
# so all 25 rows rendered blank. The programme name is in there, in a
# fixed position - after the state clause, before the network. So the
# label is read rather than copied.
#
# Two more rails are the sport and league pickers ("Football",
# "NCAA - Football"). Those are navigation, not programming, and are
# skipped by the style their rail declares.

_ESPN_ISOLATE_RE = re.compile(r'[\u2066-\u2069\u200e\u200f\u061c]')

# Rail styles that hold navigation rather than programmes.
_ESPN_SKIP_SET_STYLES = frozenset({'logo_round'})

_ESPN_SET_SECTION_RE = re.compile(
    r'data-testid="set-section"[^>]*?data-set-style="([a-z_]+)"',
    re.IGNORECASE)

_ESPN_TILE_RE = re.compile(
    r'<a\s[^>]*?data-testid="set-item"[^>]*?aria-label="([^"]{2,400})"'
    r'[^>]*?href="(/browse/entity-[0-9a-f\-]{8,})"',
    re.IGNORECASE | re.DOTALL)

# The state clause a schedule tile opens with. Removed first, because
# everything after it is the tile proper.
_ESPN_STATE_PREFIX_RE = re.compile(
    r'^\s*(?:'
    r'LIVE\s+Started\s+.{1,40}?\s+ago'
    r'|LIVE\b'
    r'|Upcoming\s+\d{1,2}:\d{2}\s*[AP]M(?:\s+\d{1,2}:\d{2}\s*[AP]M\s*-\s*'
    r'\d{1,2}:\d{2}\s*[AP]M)?'
    r'|Upcoming\b'
    r'|Replay\s+Aired\s+[A-Z][a-z]+\s+\d{1,2},\s*\d{4}'
    r'|Replay\b'
    r'|\d{1,2}:\d{2}\s*[AP]M\s*-\s*\d{1,2}:\d{2}\s*[AP]M'
    r'|(?:New\s+Series|New\s+Episode|New\s+Season|Coming\s+Soon|New)\s+Badge'
    r')\s*[-:]?\s*',
    re.IGNORECASE)

# The networks an ESPN tile names after the programme. Longest first so
# "ESPN+" and "ESPN Global" win over the "ESPN" inside them, and each
# has to stand as its own run of words.
_ESPN_NETWORKS = (
    'ESPN Deportes+', 'ESPN Deportes', 'ESPN Unlimited', 'ESPN Global',
    'ESPN on ABC', 'ESPN Classic', 'ESPNEWS', 'ESPNU', 'ESPN3', 'ESPN2',
    'ESPN+', 'ESPN',
    'ACC Network Extra', 'ACC Network', 'ACCNX', 'ACCN',
    'SEC Network+', 'SEC Network', 'SECN+', 'SECN',
    'Longhorn Network', 'NFL Network', 'NHL Network', 'MLB Network',
    'ABC',
)
# Deliberately NOT in that list: Disney+. The network column on this
# page is always an ESPN-family or partner sports network, and several
# ESPN programmes are named for the bundle they stream on - "Get Up
# for Disney+", "Pardon The Interruption for Disney+". Treating it as
# a network cut those names in half.

# A name ending on one of these was cut mid-phrase, so the marker that
# produced it was inside the title rather than after it.
_ESPN_DANGLING_WORDS = frozenset({
    'for', 'with', 'and', 'the', 'a', 'an', 'of', 'on', 'in', 'at',
    'to', 'vs', 'vs.', 'v', 'from', 'by', 'presents', 'featuring',
    'feat', 'ft', '&', 'plus',
})
_ESPN_NETWORK_RE = re.compile(
    r'\s(?:' + '|'.join(re.escape(n) for n in _ESPN_NETWORKS) + r')(?=\s|$)')

# Everything else that follows a programme name on one of these tiles.
# The first one that leaves a non-empty name in front of it wins.
_ESPN_TAIL_MARKERS = (
    re.compile(r'\s(?:Season|Episode)\s+\d', re.IGNORECASE),
    re.compile(r'\sS\d{1,4}\s*:\s*E\d{1,4}', re.IGNORECASE),
    re.compile(r'\sReleased\s+\d{4}', re.IGNORECASE),
    re.compile(r'\sRated\s+(?:TV|G|PG|R|NC)\b', re.IGNORECASE),
    re.compile(r',?\s\d{1,3}\s+of\s+\d{1,3}\s+items\b', re.IGNORECASE),
    re.compile(r'\sSelect\s+for\s+(?:details|more)\b', re.IGNORECASE),
    re.compile(r'\sChoose\s+Feed\b', re.IGNORECASE),
    re.compile(r'\sNo\s+Bumper\b', re.IGNORECASE),
    re.compile(r'\sDisney\+\s+Originals?\b', re.IGNORECASE),
    re.compile(r'\s(?:Mon|Tue|Tues|Wed|Thu|Thur|Thurs|Fri|Sat|Sun)'
               r'(?:day)?,\s*\d{1,2}/\d{1,2}', re.IGNORECASE),
    re.compile(r'\s\d{1,2}:\d{2}\s*[AP]M\b', re.IGNORECASE),
    re.compile(r'\s[A-Z][A-Za-z/&\' -]*\s+genre\.', 0),
)

_ESPN_TRAILING_JUNK_RE = re.compile(r'[\s,;:\-\u2013\u2014.]+$')

# A label that OPENS on one of those markers is the suffix on its own:
# the tile named no programme. The markers above all want a space in
# front of them, so without this the whole suffix would survive as the
# name.
_ESPN_LEADING_NOISE_RE = re.compile(
    r'^(?:Select\s+for\s+(?:details|more)|Choose\s+Feed|No\s+Bumper'
    r'|Released\s+\d{4}|Rated\s+(?:TV|G|PG|R|NC)\b'
    r'|(?:Season|Episode)\s+\d|S\d{1,4}\s*:\s*E\d{1,4}'
    r'|\d{1,2}:\d{2}\s*[AP]M)',
    re.IGNORECASE)

# A cleaned name that is only a state word, a sport bucket or a nav
# word is not a programme. Sport buckets reach here from a rail whose
# style we do not skip.
_ESPN_NOT_A_TITLE = frozenset({
    'live', 'upcoming', 'replay', 'on now', 'next up', 'details',
    'watchlist', 'search', 'menu', 'home', 'browse', 'espn', 'espn+',
    'more', 'view all', 'see all', 'left arrow', 'right arrow',
    'arrow-left', 'arrow-right',
})


def _espn_programme_name(raw: str) -> str:
    """The programme name inside one ESPN tile's accessible name.

    Returns '' when nothing survives, which is the right answer for a
    tile that names no programme. A marker only truncates when a name
    is left standing in front of it, so a programme actually called
    "ESPN Films Presents" keeps its name instead of vanishing.
    """
    s = _ESPN_ISOLATE_RE.sub('', unescape(raw or ''))
    s = re.sub(r'\s+', ' ', s).strip()
    if not s:
        return ''

    # State clauses stack on a live tile ("LIVE" then the elapsed
    # phrase arrive as separate spans on some layouts).
    for _ in range(3):
        stripped = _ESPN_STATE_PREFIX_RE.sub('', s, count=1).strip()
        if stripped == s:
            break
        s = stripped
    if not s or _ESPN_LEADING_NOISE_RE.match(s):
        return ''

    # Every network position, not just the first: a title can end on
    # the word ESPN and be followed by the network ("Sports Heaven:
    # The Birth of ESPN" on ESPN Global). The walk below picks which
    # of them is the real boundary.
    cuts = {len(s)}
    for m in _ESPN_NETWORK_RE.finditer(s):
        if m.start() > 0:
            cuts.add(m.start())
    for rx in _ESPN_TAIL_MARKERS:
        m = rx.search(s)
        if m and m.start() > 0:
            cuts.add(m.start())

    # Earliest cut first, but a cut that leaves a dangling connector
    # landed inside the name rather than after it, so the next one is
    # tried. "Get Up for Disney+ Season 2026 Episode 91" cuts at the
    # season, not at the bundle in its name.
    fallback = ''
    for c in sorted(cuts):
        cand = _ESPN_TRAILING_JUNK_RE.sub('', s[:c]).strip()
        if len(cand) < 2 or cand.lower() in _ESPN_NOT_A_TITLE:
            continue
        if not fallback:
            fallback = cand
        last = cand.rsplit(' ', 1)[-1].lower()
        if last in _ESPN_DANGLING_WORDS:
            continue
        return cand
    return fallback


def _espn_content_sections(html: str) -> list[str]:
    """The page's rails, minus the navigation ones.

    Split on the rail boundary rather than parsed, because the only
    thing needed per rail is the style it declares and the tiles
    inside it.
    """
    bounds = [m.start() for m in
              re.finditer(r'data-testid="set-section"', html)]
    if not bounds:
        return [html]
    bounds.append(len(html))
    out: list[str] = []
    for i in range(len(bounds) - 1):
        chunk = html[bounds[i]:bounds[i + 1]]
        m = _ESPN_SET_SECTION_RE.match(chunk)
        style = (m.group(1).lower() if m else '')
        if style in _ESPN_SKIP_SET_STYLES:
            continue
        out.append(chunk)
    return out


def _extract_espnplus(html: str) -> list[dict]:
    """Titles off /browse/espn, in the order ESPN+ arranges them.

    Falls back to the shared Disney+ readers when the page carries no
    rails we recognise, so a layout change degrades to the old
    behaviour rather than to nothing.
    """
    if _BAMGRID_ERROR_MARKER in html and len(html) < 200_000:
        return []

    out: list[dict] = []
    seen_uuid: set[str] = set()
    seen_title: set[str] = set()
    dropped = 0
    for chunk in _espn_content_sections(html):
        for m in _ESPN_TILE_RE.finditer(chunk):
            path = m.group(2)
            uuid = path.rsplit('-', 1)[-1].lower()
            if uuid in seen_uuid:
                continue
            title = _espn_programme_name(m.group(1))
            if not title:
                dropped += 1
                continue
            key = title.lower()
            if key in seen_title:
                continue
            seen_uuid.add(uuid)
            seen_title.add(key)
            out.append({
                'rank':             len(out) + 1,
                'title':            title,
                'url':              f'https://www.disneyplus.com{path}',
                'category_display': '',
                'collection':       '',
            })

    if out:
        logger.info("espnplus: read %d programme name(s) from the tiles "
                    "(%d tile(s) named none)", len(out), dropped)
        return out

    # No tiles we could name. Either the page is the logged-out
    # server-rendered one (which carries __NEXT_DATA__) or the tile
    # shape moved.
    logger.warning("espnplus: no programme names off the rails; falling "
                   "back to the shared Disney+ readers")
    return _extract_disneyplus(html) or _extract_disneyplus_dom(html)


ESPNPLUS_URLS = [
    # /browse/espn is the only ESPN+ landing on disneyplus.com. Other
    # sport-shaped paths (/browse/sports, /browse/football, etc.) return
    # a hard 404 - Disney+ organizes ESPN+ content into leagues/shows
    # deeper inside /browse/espn, not into top-level browse paths.
    ('browse_espn', 'https://www.disneyplus.com/browse/espn'),
]


def _fetch_via_http(pages: list[tuple[str, str]]) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for label, url in pages:
        r = http_get(url, timeout=30, cookie_domain='disneyplus.com')
        if r is None:
            continue
        try:
            html = r.text if hasattr(r, 'text') else r.decode('utf-8')
        except Exception:
            continue
        results.append((label, html))
    return results


def _load_previous_espnplus_snapshot() -> list[dict] | None:
    """Read the current latest/ ESPN+ snapshot from S3. Returns the
    national items list on success, None on any failure. Used to
    preserve last-known-good when today's fetch stumbles on a
    transient network glitch (like the 2026-09-01 08:00 launchd run
    that got net::ERR_INTERNET_DISCONNECTED after Wi-Fi flickered),
    a Bamgrid soft-block that survives the http_get retry, or a
    future parser regression - same pattern max_streaming.py and
    disneyplus.py use."""
    try:
        import boto3, json as _json
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(Bucket='dashboard-inputs',
                          Key='trends_iq_snapshots/latest/espnplus.json')
        d = _json.loads(o['Body'].read().decode('utf-8'))
        items = d.get('national') or []
        return items if isinstance(items, list) and items else None
    except Exception as e:
        logger.info("espnplus: could not read previous snapshot: %s", e)
        return None


def fetch() -> dict[str, Any]:
    rendered = render_pages(ESPNPLUS_URLS,
                             homepage='https://www.disneyplus.com/',
                             cookie_domain='disneyplus.com',
                             wait_ms=4000,
                             scroll_ms=2500,
                             hydration_wait_ms=12000)

    if rendered and all(_BAMGRID_ERROR_MARKER in html and len(html) < 200_000
                        for _, html in rendered):
        logger.warning("espnplus: all pages returned the Bamgrid IP-gate "
                        "error shell. Datacenter IP is being blocked; run "
                        "from a residential IP.")
        rendered = _fetch_via_http(ESPNPLUS_URLS)

    all_items: list[dict] = []
    seen: set[str] = set()
    for label, html in rendered:
        items = _extract_espnplus(html)
        for it in items:
            key = it['title'].lower()
            if key in seen:
                continue
            seen.add(key)
            it['collection'] = it.get('collection') or label
            all_items.append(it)
        logger.info("espnplus %s: parsed %d titles from %d-byte HTML",
                     label, len(items), len(html))

    for i, it in enumerate(all_items[:25], start=1):
        it['rank'] = i

    # Empty result => Bamgrid soft-block that survived the http_get
    # retry, transient network glitch (2026-09-01 08:00 launchd hit
    # ERR_INTERNET_DISCONNECTED on the browse/espn load), consent
    # shell, or a future parser regression. Fire the offline notifier
    # so operators know to look; the dashboard itself just shows a
    # neutral 'warming up' tile per the no-operator-hints rule.
    #
    # Then preserve yesterday's snapshot rather than overwriting the
    # tile with an empty list. Same pattern as max_streaming.py and
    # disneyplus.py. Only falls back to empty when there is no prior
    # good snapshot to preserve (first-ever run, permanent regression,
    # etc.), so the cookie-gap 'warming up' state can still take over.
    if not all_items:
        biggest = max((len(html) for _, html in rendered), default=0)
        reason = (f'ESPN+ browse/espn returned 0 titles from largest '
                  f'{biggest}-byte page; check that disneyplus.com is '
                  'reachable from the residential IP, re-donate cookies '
                  'for disneyplus.com if needed, or wait for the next '
                  'scheduled run if this was a transient network glitch')
        try:
            from .cookie_gap_notify import notify_cookie_gap
            notify_cookie_gap('espnplus', 'disneyplus.com', reason=reason)
        except Exception as e:
            logger.info("espnplus cookie_gap notify failed: %s", e)
        prev = _load_previous_espnplus_snapshot()
        if prev:
            logger.warning("espnplus: preserving previous snapshot "
                           "(%d items) instead of overwriting with 0",
                           len(prev))
            return {'national': prev,
                    'stale_from_previous': True,
                    'soft_block_reason': reason}
        logger.warning("espnplus: no previous snapshot available; "
                       "letting empty result write so the cookie-gap "
                       "'warming up' state takes over")

    return {'national': all_items[:25]}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper('espnplus', 'ESPN+', 'streaming', fetch)
    print(f"espnplus: {len(result.get('national', []))} items  "
           f"error={result.get('error')}", file=sys.stderr)
