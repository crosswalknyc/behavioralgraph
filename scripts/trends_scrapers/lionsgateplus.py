"""
Lionsgate+ trending scraper.

Jenna 2026-09-22: "for streaming let's add streaming services such as
MovieSphere+, and Lionsgate+".

LIONSGATE+ IS AVAILABLE IN THE UNITED STATES. An earlier read of this
ask concluded it was not, which was wrong; Jenna corrected it with the
live site. Confirmed against `https://lionsgateplus.com/` on
2026-09-22: $6.99 a month, ad-free, seven-day free trial, eighteen
countries with the United States listed first, and the service's own
FAQ answering "How do I subscribe" with "add it as an additional
channel to Amazon Prime Video. You do not need to be an Amazon Prime
subscriber," and "How do I cancel" with amazon.com/yms. It went live
in the US on 2026-04-09. It is the studio's own library service, the
same brand Starz used internationally as STARZPLAY before the 2022
rebrand, now standing on its own after the 2025 Starz separation.

THIS IS A STANDALONE SERVICE TAB, NOT A PARENT WITH A BREAKOUT
--------------------------------------------------------------
Starz is the shape where a service tab has a distribution-path child:
the Starz tab is the whole service and "Starz on Amazon" is an
enforced subset of it, always strictly below, computed from the parent
at render time (`scripts/trends_scrapers/derived_rails.py`).

LIONSGATE+ IS NOT THAT SHAPE, and a "Lionsgate+ on Amazon" rail must
never be added. In the US it is sold only as a Prime Video add-on
channel, it has no app of its own to be sold around, and its own FAQ
names Amazon as the only way to subscribe or cancel. Its entire US
audience therefore already IS its Amazon audience. A breakout would be
100% of its parent on the first render, and a subset rail must sit
strictly below its parent, so such a rail could never be valid. If the
question comes up again, the answer is that this panel already is the
Amazon-carried service, which is why the scope label says so.

WHERE THE CATALOG COMES FROM
----------------------------
Three sources were tried before this one, and the two that failed are
recorded so nobody pays for them twice:

  * JustWatch has no Lionsgate+ package. All 350 live US packages were
    walked on 2026-09-22 and none matches lionsgate, lions gate,
    starzplay or starz play; the ninety Amazon Channel packages
    JustWatch does carry do not include this one. So the
    `_justwatch_svod.py` path that serves Paramount+, Peacock, AMC+ and
    MovieSphere+ is simply not available here.
  * lionsgateplus.com has no catalog to scrape. It is a single-page
    marketing site that returns the same document for /browse,
    /series, /shows, /titles, /collections and /all-titles, and its
    one JavaScript bundle names eight spotlight titles and nothing
    else.

What works is the channel's own storefront on Prime Video, which
Jenna supplied:

    https://www.amazon.com/gp/video/channel/c1efd0a7-cefe-db33-3693-6099f26ccf1a

That page ships its rails inline as JSON inside a `<script>` block
(`init.preparations.body.containers`), one `TitleCard` entity per
title carrying the title, the Amazon title id, the release year, the
runtime, the synopsis and the poster. Two things about reaching it:

  * It must be requested with Chrome's TLS fingerprint. A plain curl
    from the scraper host, even with a real user agent, gets the bot
    wall: a 1.37MB document full of "Oops!" and captcha markup. The
    same request through curl_cffi impersonating Chrome returns the
    real 1.26MB storefront. That is the whole difference, and it means
    this runs from the daily Hetzner batch like any other scraper. No
    residential hop, no donated cookies, nothing for the operator to
    do. Headless Chrome under Playwright is worse than useless here -
    it is fingerprinted and served the generic page even from a home
    connection - so this module deliberately does not use it.
  * The impersonation profile matters. `chrome120` is the one that
    passes; `chrome124` comes back with a 3.8KB stub and `safari17_0`
    with a page carrying no rails. `_IMPERSONATE` lists the working
    profile first and the others as fallbacks, so a future Amazon
    change that retires one leaves the ladder to find another.

The storefront rotates which rails it serves per request (Recently
added and Comedy movies are constant; Family movies, Thrillers, Horror
movies and the Emmy rail rotate through the remaining slots), so
`_STOREFRONT_PASSES` requests it a few times and unions what comes
back. That is also why depth grows a little run to run rather than
being fixed.

Two rails matter more than the rest:

  * `Charts / Top 10 in the U.S.` is Amazon's own published ranking of
    the channel, and it leads the list.
  * Each carousel carries a `seeMore` link whose `serviceToken` holds
    the plain catalog query Amazon itself wrote, including
    `field-subscription_id=lionsgateplusus` and
    `sort=featured-rank`. Following that link returns the channel's
    full featured-rank grid, and the grid reports the true catalog
    size: 704 films and 50 shows on 2026-09-22. The token is minted
    fresh by the page on every run, so nothing stale is ever replayed.

The one edit made to that token is the entity-type refinement, an
eleven-digit id Amazon put in its own query: `14069184011` selects
films and `14069185011` selects shows. Swapping one for the other is a
same-length byte substitution, which is why it needs no re-encoding.
Amazon paginates the grid only for a signed-in session, so each grid
gives its first page; the rails carry the rest and the pass count
carries the breadth.

DEPTH IS WHAT THE SOURCE GIVES. On 2026-09-22 four passes plus the two
grids returned roughly 150 unique titles out of a catalog Amazon sizes
at 754. The gap is reported rather than padded, the same way BritBox's
62 films and MovieSphere+'s 48 shows are. `streaming_depth.py` cannot
extend this slug because the JustWatch package it would need does not
exist, so what is here is what the channel publishes about itself.

Audience anchors live in the `lionsgateplus` entry of
`scripts/trends_scrapers/stream_estimates.py::_STREAMING_PLATFORMS_META`.

Standalone:
    python3 -m scripts.trends_scrapers.lionsgateplus
"""

from __future__ import annotations

import base64
import json
import logging
import random
import re
import sys
import time
import urllib.parse
from html import unescape
from typing import Any, Optional

from ._base import run_scraper

logger = logging.getLogger(__name__)


SLUG       = 'lionsgateplus'
LABEL      = 'Lionsgate+'
CHANNEL_ID = 'c1efd0a7-cefe-db33-3693-6099f26ccf1a'
STOREFRONT = f'https://www.amazon.com/gp/video/channel/{CHANNEL_ID}'
BROWSE     = 'https://www.amazon.com/gp/video/browse'
DETAIL     = 'https://www.amazon.com/gp/video/detail/'

# curl_cffi impersonation profiles, best first. See the module
# docstring: only the Chrome-120 fingerprint gets the real storefront
# back from Amazon today, and the other two are here so the ladder has
# somewhere to go if that stops being true.
_IMPERSONATE = ('chrome120', 'chrome116', 'chrome110')

# How many times to ask for the storefront. The rails it serves rotate
# per request, so each pass adds a little catalog. Four is where the
# curve flattens: passes five and six were returning rails already
# seen on 2026-09-22.
_STOREFRONT_PASSES = 4

# How many distinct See more queries to follow, each once for films
# and once for shows. The whole-catalog query comes first and the
# genre ones after it; four keeps the run to roughly a dozen requests
# while reaching the show catalog the rotating rails mostly skip.
_MAX_GRID_QUERIES = 4

# Amazon's own entity-type refinement ids, lifted from the query it
# writes into every carousel's See more link. Same length, so swapping
# one for the other inside the token needs no re-encoding.
_ENTITY_TYPE_FILM = '14069184011'
_ENTITY_TYPE_TV   = '14069185011'

# Per-column cap. The dashboard renders films[:20] + tv[:20] and the
# estimator reads national[:40], so 100 a side leaves headroom without
# letting one rotating rail run away with the panel.
_PER_KIND_LIMIT = 100

_HEADERS = {
    'Accept': ('text/html,application/xhtml+xml,application/xml;q=0.9,'
               'image/avif,image/webp,*/*;q=0.8'),
    'Accept-Language': 'en-US,en;q=0.9',
    'Upgrade-Insecure-Requests': '1',
}

# The storefront hero card is the channel's own brand tile, not a
# title. Matched case-insensitively against the whole string.
_NOT_A_TITLE = frozenset({
    'lionsgate', 'lionsgate+', 'lionsgate plus',
    'prime video', 'amazon prime video', 'channels',
})


def _get(url: str) -> Optional[str]:
    """Fetch `url` with a Chrome TLS fingerprint. None on failure."""
    try:
        from curl_cffi import requests as cr  # type: ignore
    except ImportError:
        logger.warning('%s: curl_cffi is not installed; install it with '
                       '`pip3 install --break-system-packages curl_cffi`',
                       SLUG)
        return None
    for profile in _IMPERSONATE:
        try:
            r = cr.get(url, impersonate=profile, timeout=45,
                       headers=_HEADERS)
        except Exception as e:
            logger.info('%s: %s fetch failed on %s: %s',
                        SLUG, profile, url[:80], e)
            continue
        body = r.text or ''
        if r.status_code == 200 and '"widgetType":"TitleCard"' in body:
            return body
        logger.info('%s: %s returned %d / %d bytes with no title cards',
                    SLUG, profile, r.status_code, len(body))
    return None


def _page_body(html: str) -> Optional[dict]:
    """Pull `init.preparations.body` out of the storefront HTML.

    Amazon ships the whole rendered page as one JSON object inside a
    `<script>` block. Find the block that carries title cards and
    parse it; anything else on the page is chrome.
    """
    if not html:
        return None
    for m in re.finditer(r'<script[^>]*>', html):
        start = m.end()
        end = html.find('</script>', start)
        if end < 0:
            continue
        block = html[start:end]
        if '"widgetType":"TitleCard"' not in block:
            continue
        try:
            doc = json.loads(block)
        except json.JSONDecodeError:
            try:
                doc = json.loads(unescape(block))
            except json.JSONDecodeError:
                continue
        body = (((doc.get('init') or {}).get('preparations') or {})
                .get('body'))
        if isinstance(body, dict) and isinstance(body.get('containers'),
                                                 list):
            return body
    return None


def _row(entity: dict, rail: str) -> Optional[dict]:
    """One storefront entity to one snapshot row. None when the entity
    is the channel's brand tile rather than a title."""
    title = (entity.get('title') or entity.get('displayTitle') or '').strip()
    if not title or title.lower() in _NOT_A_TITLE:
        return None
    kind = (entity.get('entityType') or '').strip().lower()
    category = 'Film' if kind == 'movie' else ('TV' if 'tv' in kind or
                                               'show' in kind else '')
    title_id = (entity.get('titleID') or '').strip()
    image = (((entity.get('images') or {}).get('cover') or {})
             .get('url') or '')
    year = (entity.get('releaseYear') or '').strip()
    row = {
        'title':            title,
        'url':              DETAIL + title_id if title_id else STOREFRONT,
        'category_display': category,
        'collection':       rail,
        'image':            image,
        'year':             year,
        'description':      (entity.get('synopsis') or '').strip(),
        'amazon_title_id':  title_id,
    }
    return {k: v for k, v in row.items() if v != ''}


def _token_query(token: str) -> str:
    """The plain catalog query Amazon packed inside a See more token.

    The token is base64 with a `v0_` prefix and the query sits in it as
    readable text, so this reads it rather than reconstructing it.
    Empty string when the token does not decode, which only costs the
    caller its preference order.
    """
    try:
        raw = token[3:] if token.startswith('v0_') else token
        blob = base64.b64decode(raw + '=' * (-len(raw) % 4))
    except Exception:
        return ''
    m = re.search(rb'qs-offer_type=[\x20-\x7e]{20,800}', blob)
    return m.group(0).decode('utf-8', 'replace') if m else ''


def _see_more_tokens(body: dict) -> list[tuple[str, str]]:
    """Every retargetable See more query on the page, whole catalog
    first. Amazon mints these per page load, so nothing stale is ever
    replayed.

    Rails do not all carry the same query. Recently added carries the
    clean one, scoped to the channel and sorted by featured rank; the
    genre rails carry narrower ones scoped to their own genre. All of
    them are useful, because retargeting a genre query to shows is
    what reaches the show catalog the rotating rails mostly skip.
    Returns `(label, token)` with the clean query first.
    """
    whole: list[tuple[str, str]] = []
    genre: list[tuple[str, str]] = []
    for c in body.get('containers') or []:
        url = (((c.get('seeMore') or {}).get('link') or {}).get('url') or '')
        if not url:
            continue
        parsed = urllib.parse.parse_qs(
            urllib.parse.urlparse(unescape(url)).query)
        tokens = parsed.get('serviceToken') or []
        if not tokens:
            continue
        query = _token_query(tokens[0])
        if 'p_n_entity_type=' not in query:
            continue
        rail = (c.get('title') or 'Featured').strip()
        if 'field-subscription_id=' in query and 'sort=' in query:
            whole.append(('Featured', tokens[0]))
        else:
            genre.append((rail, tokens[0]))
    return whole + genre


def _retarget(token: str, entity_type: str) -> Optional[str]:
    """The same catalog query pointed at films or at shows.

    The refinement is an eleven-digit id Amazon wrote into its own
    query, and both ids are eleven digits, so this is a same-length
    substitution inside the decoded token: the length prefixes around
    it stay correct and the token re-encodes as it was. Returns None
    when the token carries no refinement to retarget.
    """
    try:
        raw = token[3:] if token.startswith('v0_') else token
        blob = base64.b64decode(raw + '=' * (-len(raw) % 4))
    except Exception:
        return None
    current = next((et.encode() for et in (_ENTITY_TYPE_FILM,
                                           _ENTITY_TYPE_TV)
                    if et.encode() in blob), None)
    if current is None:
        return None
    swapped = blob.replace(current, entity_type.encode())
    return 'v0_' + base64.b64encode(swapped).decode().rstrip('=')


def _grid(token: str, entity_type: str, label: str
          ) -> tuple[list[dict], Optional[int]]:
    """The channel's featured-rank grid for one entity type.

    Returns the rows and the catalog size Amazon reports for that type
    (`estimatedTotal`), which is worth logging even though the grid
    itself only serves its first page to a signed-out request.
    """
    swapped = _retarget(token, entity_type)
    if swapped is None:
        logger.info('%s: the catalog query does not carry the entity-type '
                    'refinement; skipping the %s grid', SLUG, label)
        return [], None
    html = _get(BROWSE + '?serviceToken=' +
                urllib.parse.quote(swapped, safe=''))
    body = _page_body(html or '')
    if not body:
        return [], None
    rows: list[dict] = []
    total: Optional[int] = None
    for c in body.get('containers') or []:
        if total is None and isinstance(c.get('estimatedTotal'), int):
            total = c['estimatedTotal']
        for e in c.get('entities') or []:
            r = _row(e, label)
            if r:
                rows.append(r)
    return rows, total


def _load_previous_snapshot() -> list[dict]:
    try:
        import boto3
        s3 = boto3.client('s3', region_name='us-east-2')
        o = s3.get_object(Bucket='dashboard-inputs',
                          Key=f'trends_iq_snapshots/latest/{SLUG}.json')
        d = json.loads(o['Body'].read().decode('utf-8'))
        items = d.get('national') or []
        return items if isinstance(items, list) else []
    except Exception as e:
        logger.info('%s: no previous snapshot: %s', SLUG, e)
        return []


def _harvest() -> tuple[tuple[list[dict], list[dict]],
                        list[tuple[str, str]], dict[str, int]]:
    """Walk the storefront a few times.

    Returns `((charted, railed), tokens, rails_seen)`: Amazon's own
    Top 10 kept apart from the rotating genre rails so the caller can
    put the featured-rank grid between them, the See more queries the
    grids will follow, and the rails seen for the log line.
    """
    charted: list[dict] = []
    railed: list[dict] = []
    tokens: list[tuple[str, str]] = []
    queries_seen: set[str] = set()
    rails_seen: dict[str, int] = {}

    for i in range(_STOREFRONT_PASSES):
        if i:
            time.sleep(0.6 + random.random() * 0.9)
        body = _page_body(_get(STOREFRONT) or '')
        if not body:
            logger.info('%s: storefront pass %d returned nothing usable',
                        SLUG, i + 1)
            continue
        for label, token in _see_more_tokens(body):
            query = _token_query(token)
            if query in queries_seen:
                continue
            queries_seen.add(query)
            tokens.append((label, token))
        for c in body.get('containers') or []:
            rail = (c.get('title') or '').strip()
            entities = c.get('entities') or []
            if rail:
                rails_seen[rail] = max(rails_seen.get(rail, 0), len(entities))
            bucket = charted if c.get('containerType') == 'Charts' else railed
            for e in entities:
                r = _row(e, rail or 'Storefront')
                if r:
                    bucket.append(r)

    return (charted, railed), tokens, rails_seen


def fetch() -> dict[str, Any]:
    railed, tokens, rails_seen = _harvest()
    charted, railed = railed

    gridded: list[dict] = []
    totals: dict[str, Optional[int]] = {}
    if not tokens:
        logger.info('%s: no See more query on the storefront; shipping the '
                    'rails alone', SLUG)
    for label, token in tokens[:_MAX_GRID_QUERIES]:
        # A rail named "Comedy movies" retargeted to shows is the
        # comedy shows, so drop the rail's own kind word before
        # naming the grid.
        stem = re.sub(r'\s+(movies|films|shows|series|tv)$', '', label,
                      flags=re.IGNORECASE).strip() or 'Featured'
        for entity_type, kind in ((_ENTITY_TYPE_FILM, 'films'),
                                  (_ENTITY_TYPE_TV, 'shows')):
            grid_rows, total = _grid(token, entity_type, f'{stem} {kind}')
            # Only the whole-catalog query reports the catalog size; a
            # genre query reports the size of its own slice.
            if label == 'Featured' and total and kind not in totals:
                totals[kind] = total
            gridded.extend(grid_rows)
            time.sleep(0.35)

    # Reading order, and it matters beyond presentation. Amazon's own
    # Top 10 leads, then its featured-rank grid, then the rotating
    # genre rails. The estimator reads the first 40 rows of a
    # platform's list when it prices a day, so whatever sits at the
    # head of this list is what gets a reading of its own; anything
    # below it can only fall back to a cross-platform number. Putting
    # Amazon's two published orderings first is what keeps the titles
    # a reader will look for first off that fallback.
    # Yesterday's list rides along at the tail. The storefront serves a
    # rotating subset of its rails per request and a grid query fails
    # now and then, so two runs an hour apart legitimately return 97
    # and 124 titles out of the same unchanged catalog. Without this
    # the panel would visibly shrink and regrow on nothing, and the
    # rows that dropped would lose the readings they had earned. Only
    # the PREVIOUS snapshot is carried, never an accumulating set, so
    # a title genuinely pulled from the channel leaves the panel after
    # one day rather than lingering.
    fresh = charted + gridded + railed
    rows = fresh + _load_previous_snapshot()

    # First occurrence wins, which is what holds that order and what
    # keeps a carried row from displacing a title seen today.
    seen: set[str] = set()
    films: list[dict] = []
    tv: list[dict] = []
    unclassified: list[dict] = []
    for r in rows:
        key = r['title'].lower()
        if key in seen:
            continue
        seen.add(key)
        if r.get('category_display') == 'Film':
            films.append(r)
        elif r.get('category_display') == 'TV':
            tv.append(r)
        else:
            unclassified.append(r)
    films = films[:_PER_KIND_LIMIT]
    tv = tv[:_PER_KIND_LIMIT]

    logger.info('%s: %d films + %d tv (+%d unclassified), %d seen today '
                'across %d rails %s; Amazon reports %s films and %s shows '
                'in the catalog',
                SLUG, len(films), len(tv), len(unclassified), len(fresh),
                len(rails_seen), sorted(rails_seen),
                totals.get('films'), totals.get('shows'))

    for i, r in enumerate(films, 1):
        r['bucket_rank'] = i
    for i, r in enumerate(tv, 1):
        r['bucket_rank'] = i

    if fresh and (films or tv):
        # Zipper-interleave so a consumer slicing national[:25] sees
        # both columns, which is the shape every other streaming
        # snapshot writes.
        out: list[dict] = []
        i = j = 0
        while i < len(films) or j < len(tv):
            if i < len(films):
                out.append(films[i]); i += 1
            if j < len(tv):
                out.append(tv[j]); j += 1
        for k, r in enumerate(out, 1):
            r['rank'] = k
        payload: dict[str, Any] = {'national': out}
        if totals.get('films') or totals.get('shows'):
            payload['catalog_size'] = {
                'films': totals.get('films'),
                'shows': totals.get('shows'),
            }
        return payload

    reason = (f'{SLUG}: the Prime Video storefront returned no titles across '
              f'{_STOREFRONT_PASSES} passes - Amazon served the bot wall or '
              'the channel page moved?')
    logger.warning('%s', reason)
    prev = _load_previous_snapshot()
    if prev:
        logger.warning('%s: keeping the previous %d-title catalog instead of '
                       'overwriting with nothing', SLUG, len(prev))
        return {'national': prev, 'stale_from_previous': True,
                'soft_block_reason': reason}
    return {'national': []}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    result = run_scraper(SLUG, LABEL, 'streaming', fetch)
    print(f"{SLUG}: {len(result.get('national', []))} items  "
          f"error={result.get('error')}", file=sys.stderr)
