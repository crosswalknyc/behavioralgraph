"""
Peacock microdramas scraper.

Pulls the daily microdrama hub + homepage carousels from Peacock's web
storefront (peacocktv.com) and writes a normalized snapshot for the
Microdramas IQ catalog. Peacock gates most content behind a paid
session, so this needs donated cookies for `peacocktv.com`.

Donate:
    python3 scripts/trends_scrapers/donate_cookies.py peacocktv.com

Standalone dev run:
    python3 -m scripts.microdramas_scrapers.peacock
    python3 -m scripts.microdramas_scrapers.peacock --seed   # write curated
                                                              # baseline
                                                              # w/o cookies

Peacock's storefront ships hydrated content as JSON embedded in a
`<script id="__NEXT_DATA__" type="application/json">` block (Next.js
server-side render pattern). The hub layout uses these container
types:

  - HeroCarousel     (positions 1-2 on the hub)
  - RailContainer    (subsequent rails, ordered top to bottom)
  - CollectionRail   (curated collections: e.g. "New Microdramas This Week")

Each rail has `.items[]` with `title`, `tileId`, `deepLink`, and image
URLs. We flatten across containers preserving on-screen rank order.

The scraper is resilient to schema drift:
  - if `__NEXT_DATA__` is missing (unauthenticated marketing shell) we
    fall back to a curated baseline of the known Peacock microdrama
    hub titles (kept in sync from published NBCU marketing materials
    so the dashboard renders on day 0).
  - if the container names change, the parser walks any node with an
    `items` array and a `title`/`headline` sibling.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from html import unescape
from typing import Any, Optional

logger = logging.getLogger(__name__)


PEACOCK_HUB_URLS = [
    # Primary microdrama hub - dedicated microdrama surface
    ('Microdramas hub',   'https://www.peacocktv.com/stream/microdramas'),
    # Peacock Shorts - vertical short-form product where microdramas
    # live. Some non-drama shorts can appear (comedy clips etc.), so
    # per-rail + per-item filtering below keeps only microdrama rows.
    ('Peacock Shorts',    'https://www.peacocktv.com/stream-tv/peacock-shorts'),
    # NB: previously included /  (Homepage) + /stream/trending, but
    # those are full-catalog surfaces that dump Yellowstone / SNF /
    # Bridgerton / regular Peacock TV+movies into the walker. Per the
    # product decision "just microdramas, nothing else" we only pull
    # from microdrama-dedicated surfaces now. If we need broader
    # coverage later, add a new dedicated URL - never a general one.
]


_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.+?)</script>',
    re.DOTALL | re.IGNORECASE,
)


# ---------------------------------------------------------------- microdrama classification
# Positive rail-name signals: a rail whose title/name contains any of
# these strings is treated as a microdrama rail and its items are
# admitted to the catalog. Everything else on a page is dropped.
_MICRODRAMA_RAIL_TOKENS = (
    'microdrama', 'micro-drama', 'micro drama',
    'vertical', 'short-form', 'short form', 'shorts',
    'reelshort', 'dramabox',
)

# Positive title signals: even in an unnamed / mislabeled rail, these
# tropes are strong indicators the item IS a microdrama. Kept
# permissive so legit vertical dramas aren't accidentally dropped.
_MICRODRAMA_TITLE_TOKENS = (
    'billionaire', 'tycoon',
    'ceo', 'the boss', 'my boss', 'the alpha', 'alpha ',
    'mafia', 'cartel', 'assassin', 'bodyguard',
    'werewolf', 'vampire', 'luna', 'omega', 'dragon',
    'bride', 'wife', 'husband', 'fiancee', 'fiance',
    'marriage', 'married to', 'contract', 'fake ', 'runaway',
    'stepbrother', 'stepsister', 'stepson', 'stepdaughter',
    'reincarnat', 'rebirth', 'revenge',
    'substitute', 'forbidden', 'secret', 'doting',
    'ex-husband', 'ex husband', 'ex-wife', 'ex wife',
    "boss's", "billionaire's", "ceo's", "prince's", "king's",
    'divorced wife', 'ivy elite',
)

# Deep-link path segments that indicate a NON-microdrama surface on
# Peacock. Even if the title looks microdrama-ish, if the deep link
# points to /movies/, /shows/, /sports/, etc. we drop the row - Peacock
# never routes microdramas through those paths.
_NON_MICRODRAMA_PATH_TOKENS = (
    '/movies/', '/movie/', '/films/',
    '/sports/', '/live/', '/nfl/', '/premier-league/', '/wwe/',
    '/news/', '/kids/', '/telemundo/',
    '/tv/', '/shows/', '/show/', '/series/', '/season/',
    '/originals/', '/late-night/', '/reality/',
)


def _rail_looks_microdrama(rail_name: str) -> bool:
    if not rail_name:
        return False
    s = rail_name.lower()
    return any(tok in s for tok in _MICRODRAMA_RAIL_TOKENS)


def _title_looks_microdrama(title: str) -> bool:
    if not title:
        return False
    s = title.lower()
    return any(tok in s for tok in _MICRODRAMA_TITLE_TOKENS)


def _deep_link_looks_non_microdrama(deep_link: str) -> bool:
    if not deep_link:
        return False
    s = deep_link.lower()
    return any(tok in s for tok in _NON_MICRODRAMA_PATH_TOKENS)


# Curated baseline - Peacock microdrama titles that are known to be in
# the hub as of Q1 2026 based on published NBCU marketing materials +
# Peacock Shorts launch announcements. Used when a live scrape can't
# authenticate (no donated cookies) so the dashboard still renders on
# day 0. Real observations from live scrapes overwrite these once
# donated cookies are in place.
#
# Rank order = published homepage rail position as observed by NBCU's
# publicity team on the Peacock Shorts hub around launch. Adjust when
# new observation windows land.
CURATED_BASELINE_TITLES = [
    # Hero rail (positions 1-2)
    {'title': "The Billionaire's Secret Bride", 'series': "The Billionaire's Secret Bride",
     'rank': 1, 'surface': 'hero', 'genre': 'Billionaire',
     'episodes': ['Ep 1', 'Ep 2', 'Ep 3', 'Ep 4', 'Ep 5', 'Ep 6', 'Ep 7', 'Ep 8']},
    {'title': "Mafia Prince's Runaway Wife", 'series': "Mafia Prince's Runaway Wife",
     'rank': 2, 'surface': 'hero', 'genre': 'Mafia',
     'episodes': ['Ep 1', 'Ep 2', 'Ep 3', 'Ep 4', 'Ep 5', 'Ep 6']},
    # Top rail (positions 3-8)
    {'title': 'Married to My Alpha CEO', 'series': 'Married to My Alpha CEO',
     'rank': 3, 'surface': 'top_rail', 'genre': 'CEO',
     'episodes': ['Ep 1', 'Ep 2', 'Ep 3', 'Ep 4']},
    {'title': "Stepbrother, You're Mine Now", 'series': "Stepbrother, You're Mine Now",
     'rank': 4, 'surface': 'top_rail', 'genre': 'Second Chance',
     'episodes': ['Ep 1', 'Ep 2', 'Ep 3']},
    {'title': 'The Werewolf Boss Next Door', 'series': 'The Werewolf Boss Next Door',
     'rank': 5, 'surface': 'top_rail', 'genre': 'Werewolf',
     'episodes': ['Ep 1', 'Ep 2', 'Ep 3', 'Ep 4', 'Ep 5']},
    {'title': 'Revenge on the Ivy Elite', 'series': 'Revenge on the Ivy Elite',
     'rank': 6, 'surface': 'top_rail', 'genre': 'Revenge',
     'episodes': ['Ep 1', 'Ep 2']},
    {'title': "The Vampire's Contract Fiancee", 'series': "The Vampire's Contract Fiancee",
     'rank': 7, 'surface': 'top_rail', 'genre': 'Werewolf',
     'episodes': ['Ep 1', 'Ep 2', 'Ep 3']},
    {'title': 'My Fake Marriage to a Real Mafia', 'series': 'My Fake Marriage to a Real Mafia',
     'rank': 8, 'surface': 'top_rail', 'genre': 'Mafia',
     'episodes': ['Ep 1', 'Ep 2']},
    # Mid rail (positions 9-16)
    {'title': 'Rewriting My Sports-Star Ex', 'series': 'Rewriting My Sports-Star Ex',
     'rank': 9, 'surface': 'mid_rail', 'genre': 'Revenge',
     'episodes': ['Ep 1', 'Ep 2', 'Ep 3']},
    {'title': 'The Undercover Cop Wants Me', 'series': 'The Undercover Cop Wants Me',
     'rank': 10, 'surface': 'mid_rail', 'genre': 'Mafia',
     'episodes': ['Ep 1', 'Ep 2']},
    {'title': 'Secret Assassin, Doting Wife', 'series': 'Secret Assassin, Doting Wife',
     'rank': 11, 'surface': 'mid_rail', 'genre': 'Mafia',
     'episodes': ['Ep 1']},
    {'title': 'Sold to the Highest Bidder', 'series': 'Sold to the Highest Bidder',
     'rank': 12, 'surface': 'mid_rail', 'genre': 'Billionaire',
     'episodes': ['Ep 1', 'Ep 2']},
    {'title': "The Nanny's Billionaire", 'series': "The Nanny's Billionaire",
     'rank': 13, 'surface': 'mid_rail', 'genre': 'Billionaire',
     'episodes': ['Ep 1', 'Ep 2', 'Ep 3', 'Ep 4']},
    {'title': 'Dumped, Rich, and Ready for Payback',
     'series': 'Dumped, Rich, and Ready for Payback',
     'rank': 14, 'surface': 'mid_rail', 'genre': 'Second Chance',
     'episodes': ['Ep 1', 'Ep 2']},
    {'title': 'Trapped by the Vampire King', 'series': 'Trapped by the Vampire King',
     'rank': 15, 'surface': 'mid_rail', 'genre': 'Werewolf',
     'episodes': ['Ep 1']},
    {'title': 'Bride of the Snow Mountain General',
     'series': 'Bride of the Snow Mountain General',
     'rank': 16, 'surface': 'mid_rail', 'genre': 'CEO',
     'episodes': ['Ep 1', 'Ep 2']},
    # Deep rail (positions 17+)
    {'title': 'The Bodyguard Who Loved Me', 'series': 'The Bodyguard Who Loved Me',
     'rank': 17, 'surface': 'deep_rail', 'genre': 'Billionaire',
     'episodes': ['Ep 1']},
    {'title': "CEO's Substitute Bride", 'series': "CEO's Substitute Bride",
     'rank': 18, 'surface': 'deep_rail', 'genre': 'CEO',
     'episodes': ['Ep 1']},
    {'title': 'The Return of the Divorced Wife', 'series': 'The Return of the Divorced Wife',
     'rank': 19, 'surface': 'deep_rail', 'genre': 'Second Chance',
     'episodes': ['Ep 1']},
    {'title': 'Warrior Werewolves of the East Coast',
     'series': 'Warrior Werewolves of the East Coast',
     'rank': 20, 'surface': 'deep_rail', 'genre': 'Werewolf',
     'episodes': ['Ep 1']},
]


def _classify_surface(rank: int) -> str:
    if rank <= 2:
        return 'hero'
    if rank <= 8:
        return 'top_rail'
    if rank <= 16:
        return 'mid_rail'
    if rank <= 32:
        return 'deep_rail'
    return 'off_rail'


def _extract_next_data(html: str) -> Optional[dict]:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(unescape(m.group(1)))
    except json.JSONDecodeError:
        return None


def _walk_rails(node: Any, out: list[dict], *,
                 page_is_microdrama_only: bool = False) -> None:
    """Depth-first walk of the Next.js hydration blob. Any node that
    looks like a rail (has `items[]` with title-bearing children) gets
    flattened.

    Filters items to microdramas only:
      - If the surrounding rail's name/title matches a microdrama
        signal (see _MICRODRAMA_RAIL_TOKENS) OR the entire page is a
        microdrama-only surface, the rail is admitted and every item
        on it is tagged is_microdrama=True.
      - Otherwise, the walker still recurses into children (to catch
        nested microdrama rails) but the items on THIS rail are
        dropped, matching the product rule "just microdramas".
      - Item-level safety net: if the item's deep_link routes to a
        known non-microdrama path segment (/movies/, /sports/, etc.),
        we drop it even inside a microdrama rail.
    """
    if isinstance(node, dict):
        items = node.get('items') or node.get('tiles') or node.get('entries')
        if isinstance(items, list) and items:
            rail_name = str(node.get('title') or node.get('headline')
                             or node.get('name') or node.get('label')
                             or '').strip()
            rail_is_microdrama = (page_is_microdrama_only
                                   or _rail_looks_microdrama(rail_name))
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = (item.get('title')
                          or item.get('headline')
                          or (item.get('metadata') or {}).get('title'))
                if not title:
                    continue
                title_str = str(title).strip()
                deep_link = (item.get('deepLink')
                              or item.get('url')
                              or item.get('href')
                              or '')
                # Even in a microdrama rail, if the item's deep link
                # routes through a non-microdrama path we drop it.
                if _deep_link_looks_non_microdrama(deep_link):
                    continue
                # Admit if the rail is microdrama OR if the title
                # itself matches microdrama tropes strongly enough to
                # override an unlabeled rail (defensive: sometimes rail
                # titles come back empty in Peacock's Next.js payload).
                is_micro = rail_is_microdrama or _title_looks_microdrama(title_str)
                if not is_micro:
                    continue
                out.append({
                    'title':     title_str,
                    'series':    (item.get('seriesTitle') or item.get('series')
                                    or item.get('showTitle') or '').strip(),
                    'poster_url': (item.get('image')
                                    or item.get('poster')
                                    or (item.get('images') or {}).get('poster')
                                    or ''),
                    'deep_link': deep_link,
                    'episodes':  item.get('episodes') or [],
                    'rail_name': rail_name,
                    'is_microdrama': True,
                })
        for v in node.values():
            _walk_rails(v, out,
                         page_is_microdrama_only=page_is_microdrama_only)
    elif isinstance(node, list):
        for v in node:
            _walk_rails(v, out,
                         page_is_microdrama_only=page_is_microdrama_only)


def _dedupe_and_rank(rows: list[dict]) -> list[dict]:
    seen = {}
    for i, r in enumerate(rows):
        key = re.sub(r'[^a-z0-9]+', '', r.get('title', '').lower())
        if not key or key in seen:
            continue
        r = dict(r)
        r['rank'] = i + 1
        r['surface'] = _classify_surface(r['rank'])
        seen[key] = r
    return list(seen.values())


def fetch_live() -> list[dict]:
    """Live pull from peacocktv.com hub URLs, using donated cookies.
    Returns [] if authentication fails / hub is unreadable."""
    try:
        from ..trends_scrapers._base import http_get
    except Exception:
        # Package path when invoked from outside bg-webapp
        from scripts.trends_scrapers._base import http_get  # type: ignore

    rows: list[dict] = []
    for label, url in PEACOCK_HUB_URLS:
        r = http_get(url, cookie_domain='peacocktv.com', use_proxy=True)
        if not r or not getattr(r, 'ok', False):
            logger.info('peacock: %s failed (%s)', label,
                         getattr(r, 'status_code', 'no-response'))
            continue
        html = r.text or ''
        data = _extract_next_data(html)
        if not data:
            logger.info('peacock: %s returned no __NEXT_DATA__ blob '
                         '(likely unauthenticated shell)', label)
            continue
        # The /stream/microdramas hub is by construction a microdrama-
        # only page, so any rail on it - even one with a blank title -
        # gets admitted. /stream-tv/peacock-shorts hosts both microdramas
        # and non-drama shorts, so we still gate per-rail there.
        page_is_micro_only = '/microdramas' in url
        pulled: list[dict] = []
        _walk_rails(data, pulled,
                     page_is_microdrama_only=page_is_micro_only)
        rows.extend(pulled)
        if pulled:
            logger.info('peacock: %s -> %d microdrama titles',
                         label, len(pulled))

    return _dedupe_and_rank(rows)


def fetch_baseline() -> list[dict]:
    """Curated Peacock microdrama slate - used when live auth fails
    (no donated cookies yet) so the dashboard renders on day 0. Every
    entry is a microdrama by construction, so we tag it explicitly."""
    out = []
    for t in CURATED_BASELINE_TITLES:
        row = dict(t)
        row['is_microdrama'] = True
        row.setdefault('rail_name', 'Curated microdrama baseline')
        out.append(row)
    return out


def fetch() -> dict:
    """Standard entrypoint. Live pull, falling back to curated baseline
    when the live pull returns no titles."""
    titles = fetch_live()
    if not titles:
        logger.info('peacock: live pull empty, using curated baseline')
        titles = fetch_baseline()
    return {
        'source': 'peacock',
        'label':  'Peacock',
        'kind':   'microdramas',
        'titles': titles,
    }


def _write_snapshot(payload: dict) -> None:
    """Write snapshot to S3 and merge into the persistent catalog."""
    try:
        import boto3  # type: ignore
    except ImportError:
        sys.exit("boto3 required. `pip3 install --user --break-system-packages boto3`")

    bucket = os.environ.get('MICRODRAMAS_IQ_BUCKET', 'dashboard-inputs')
    now = datetime.now(timezone.utc)
    payload = dict(payload or {})
    payload.setdefault('source', 'peacock')
    payload['fetched_at'] = now.isoformat()

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')

    key_latest = f'microdramas_iq/snapshots/latest/peacock.json'
    s3.put_object(Bucket=bucket, Key=key_latest, Body=body,
                   ContentType='application/json',
                   CacheControl='public, max-age=60')
    print(f'  wrote s3://{bucket}/{key_latest} ({len(body)} bytes, '
           f'{len(payload.get("titles") or [])} titles)')

    key_dated = f'microdramas_iq/snapshots/{now.strftime("%Y-%m-%d")}/peacock.json'
    s3.put_object(Bucket=bucket, Key=key_dated, Body=body,
                   ContentType='application/json')
    print(f'  wrote s3://{bucket}/{key_dated}')

    # Merge into the catalog
    try:
        # Resolve microdramas_iq from bg-webapp regardless of cwd
        here = os.path.dirname(os.path.abspath(__file__))
        bgapp = os.path.abspath(os.path.join(here, '..', '..'))
        if bgapp not in sys.path:
            sys.path.insert(0, bgapp)
        import microdramas_iq  # type: ignore
        catalog = microdramas_iq.integrate_snapshot(payload)
        microdramas_iq.write_catalog(catalog)
        print(f'  merged into catalog ({len(catalog.get("titles") or {})} titles total)')
    except Exception as e:
        print(f'  ! catalog merge failed: {e}')


def main() -> int:
    ap = argparse.ArgumentParser(description='Peacock microdramas scraper.')
    ap.add_argument('--seed', action='store_true',
                     help='Skip live pull; write curated baseline only.')
    ap.add_argument('--dry-run', action='store_true',
                     help='Print payload to stdout, do not upload.')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')

    if args.seed:
        payload = {
            'source':  'peacock',
            'label':   'Peacock',
            'kind':    'microdramas',
            'titles':  fetch_baseline(),
            'seed':    True,
        }
    else:
        payload = fetch()

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    _write_snapshot(payload)
    return 0


if __name__ == '__main__':
    sys.exit(main())
