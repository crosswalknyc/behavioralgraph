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
    fall back to the curated Peacock Microdramas Hub slate below, kept
    in sync with Peacock's own launch materials + trade press. Real
    observations from a cookie-authenticated pull overwrite the
    baseline as soon as donated cookies land.
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
    # Primary microdrama hub. Verified 2026-09-01: /microdramas returns
    # a real hub page and hydrated __NEXT_DATA__. The older
    # /stream/microdramas and /stream-tv/peacock-shorts paths both
    # return "Peacock Not Found" and have been retired.
    ('Microdramas hub',   'https://www.peacocktv.com/microdramas'),
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


# Curated baseline - the actual Peacock Microdramas Hub slate as
# announced by Peacock (peacocktv.com/blog, 2026-05) and observed on
# the Peacock mobile app at launch. Ten ReelShort-licensed scripted
# titles + two Bravo-original unscripted titles.
#
# Sources cross-checked before writing this list:
#   - Peacock blog, "Microdramas Are Coming to Peacock This Summer"
#     (2026-05), full scripted slate + Bravo unscripted pair
#   - LightShed / Rich Greenfield launch note (2026-05-29), calling out
#     Love Me, Bite Me + Wings of Fire as flagship examples
#   - Trade press (thestreamable.com, mediaplaynews.com, streamdiag.com)
#     confirming the ten-title scripted list + episode counts
#
# Episode counts are Peacock's published figures. Rank order reflects
# the launch marketing emphasis: Do Not Disturb: Lady Boss in Disguise
# was heroed as the "81-episode flagship romance", Love Me, Bite Me
# was called out separately as the vampire hero (68 episodes), Fated
# to My Forbidden Alpha as the werewolf hero (60 episodes).
#
# Live observations from a real cookie-authenticated pull overwrite
# these once donated cookies are in place. deep_link is left empty
# here on purpose - real content IDs land only through a live pull.
CURATED_BASELINE_TITLES = [
    # Hero rail (positions 1-2) - the two most-heavily promoted at launch
    {'title': 'Do Not Disturb: Lady Boss in Disguise',
     'series': 'Do Not Disturb: Lady Boss in Disguise',
     'rank': 1, 'surface': 'hero', 'genre': 'Romance',
     'episodes': [f'Ep {i}' for i in range(1, 82)]},  # 81 episodes
    {'title': 'Love Me, Bite Me', 'series': 'Love Me, Bite Me',
     'rank': 2, 'surface': 'hero', 'genre': 'Vampire',
     'episodes': [f'Ep {i}' for i in range(1, 69)]},  # 68 episodes
    # Top rail (positions 3-8) - the rest of the ReelShort scripted slate
    {'title': 'Fated to My Forbidden Alpha',
     'series': 'Fated to My Forbidden Alpha',
     'rank': 3, 'surface': 'top_rail', 'genre': 'Werewolf',
     'episodes': [f'Ep {i}' for i in range(1, 61)]},  # 60 episodes
    {'title': 'Straight A Pregnancy', 'series': 'Straight A Pregnancy',
     'rank': 4, 'surface': 'top_rail', 'genre': 'YA',
     'episodes': [f'Ep {i}' for i in range(1, 66)]},  # 65 episodes
    {'title': 'Wings of Fire: The Dragon Slayer Is My Ex-Lover',
     'series': 'Wings of Fire: The Dragon Slayer Is My Ex-Lover',
     'rank': 5, 'surface': 'top_rail', 'genre': 'Fantasy',
     'episodes': [f'Ep {i}' for i in range(1, 61)]},  # 60 episodes
    {'title': "30 Days Till I Marry My Husband's Nemesis",
     'series': "30 Days Till I Marry My Husband's Nemesis",
     'rank': 6, 'surface': 'top_rail', 'genre': 'Revenge',
     'episodes': [f'Ep {i}' for i in range(1, 55)]},  # 54 episodes
    {'title': 'Baby Just Say Yes', 'series': 'Baby Just Say Yes',
     'rank': 7, 'surface': 'top_rail', 'genre': 'Billionaire',
     'episodes': [f'Ep {i}' for i in range(1, 59)]},  # 58 episodes
    {'title': 'Duke with Benefits', 'series': 'Duke with Benefits',
     'rank': 8, 'surface': 'top_rail', 'genre': 'Historical Romance',
     'episodes': [f'Ep {i}' for i in range(1, 51)]},  # 50 episodes
    # Mid rail (positions 9-12) - remaining ReelShort scripted + Bravo unscripted
    {'title': 'Call Boy I Met in Paris', 'series': 'Call Boy I Met in Paris',
     'rank': 9, 'surface': 'mid_rail', 'genre': 'Billionaire',
     'episodes': [f'Ep {i}' for i in range(1, 53)]},  # 52 episodes
    {'title': 'Undercover Prison King', 'series': 'Undercover Prison King',
     'rank': 10, 'surface': 'mid_rail', 'genre': 'Crime',
     'episodes': [f'Ep {i}' for i in range(1, 49)]},  # 48 episodes
    {'title': 'Campus Confidential: Miami',
     'series': 'Campus Confidential: Miami',
     'rank': 11, 'surface': 'mid_rail', 'genre': 'Reality',
     'episodes': [f'Ep {i}' for i in range(1, 21)]},  # 20 episodes
    {'title': 'Salon Confessionals with Madison LeCroy',
     'series': 'Salon Confessionals with Madison LeCroy',
     'rank': 12, 'surface': 'mid_rail', 'genre': 'Reality',
     'episodes': [f'Ep {i}' for i in range(1, 21)]},  # 20 episodes
]

# Titles that shipped in an earlier draft of this file but do NOT
# exist on the actual Peacock Microdramas Hub. Kept here as a purge
# list so a seed-mode run can remove them from the persistent catalog
# on S3. See _purge_legacy_baseline_from_catalog() below.
#
# These strings were early working names never validated against
# Peacock's shipped slate. Every one was replaced in the 2026-09-01
# correction after a customer reported that "The Vampire's Contract
# Fiancee" (row #1 by views on the dashboard) could not be found on
# Peacock. Google surfaced the real Peacock vampire title as
# "Love Me, Bite Me" (rank 2 above) instead.
LEGACY_BASELINE_TITLES_TO_PURGE = [
    "The Billionaire's Secret Bride",
    "Mafia Prince's Runaway Wife",
    "Married to My Alpha CEO",
    "Stepbrother, You're Mine Now",
    "The Werewolf Boss Next Door",
    "Revenge on the Ivy Elite",
    "The Vampire's Contract Fiancee",
    "My Fake Marriage to a Real Mafia",
    "Rewriting My Sports-Star Ex",
    "The Undercover Cop Wants Me",
    "Secret Assassin, Doting Wife",
    "Sold to the Highest Bidder",
    "The Nanny's Billionaire",
    "Dumped, Rich, and Ready for Payback",
    "Trapped by the Vampire King",
    "Bride of the Snow Mountain General",
    "The Bodyguard Who Loved Me",
    "CEO's Substitute Bride",
    "The Return of the Divorced Wife",
    "Warrior Werewolves of the East Coast",
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


def _purge_legacy_baseline_from_catalog(catalog: dict) -> int:
    """Drop stale Peacock catalog entries whose title matches a legacy
    baseline row that has since been retired. Case + punctuation
    insensitive via `_norm_key`.

    Returns the number of entries removed. Safe to call on any catalog:
    if none of the legacy strings are present, this is a no-op.

    The persistent catalog is additive by design (see
    `microdramas_iq.integrate_snapshot`), so replacing a baseline row
    in this file does not on its own remove the ghost entry from
    S3. This helper does the removal, in place, on the same catalog
    key. Consistent with the workspace rule 'no rebuild-level
    correction': we never ask for a re-pull, never quarantine, we
    fix the persistent record in place.
    """
    try:
        # Resolve microdramas_iq from bg-webapp regardless of cwd
        here = os.path.dirname(os.path.abspath(__file__))
        bgapp = os.path.abspath(os.path.join(here, '..', '..'))
        if bgapp not in sys.path:
            sys.path.insert(0, bgapp)
        from microdramas_iq import _norm_key  # type: ignore
    except Exception:
        # Fallback: same normalization inline
        import re as _re

        def _norm_key(s: str) -> str:  # type: ignore
            return _re.sub(r'[^a-z0-9]+', '', (s or '').lower())

    titles = catalog.get('titles') or {}
    purge_keys = {_norm_key(t) for t in LEGACY_BASELINE_TITLES_TO_PURGE}
    removed = 0
    for k in list(titles.keys()):
        if k in purge_keys:
            titles.pop(k, None)
            removed += 1
    return removed


def _write_snapshot(payload: dict, *, purge_legacy: bool = False) -> None:
    """Write snapshot to S3 and merge into the persistent catalog.

    When `purge_legacy` is True, remove any ghost entries in the
    persistent catalog whose title matches `LEGACY_BASELINE_TITLES_TO_PURGE`
    BEFORE merging the new snapshot. This is how a `--seed` run
    corrects the persistent catalog in place: it replaces the stale
    rows with the current baseline atomically.
    """
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

    # Merge into the catalog (with optional in-place purge of
    # retired baseline rows).
    try:
        # Resolve microdramas_iq from bg-webapp regardless of cwd
        here = os.path.dirname(os.path.abspath(__file__))
        bgapp = os.path.abspath(os.path.join(here, '..', '..'))
        if bgapp not in sys.path:
            sys.path.insert(0, bgapp)
        import microdramas_iq  # type: ignore
        catalog = microdramas_iq.read_catalog()
        if purge_legacy:
            removed = _purge_legacy_baseline_from_catalog(catalog)
            print(f'  purged {removed} legacy baseline entries from catalog')
        catalog = microdramas_iq.integrate_snapshot(payload)  # re-reads then merges
        # integrate_snapshot re-reads inside; re-apply the purge to the
        # merged result so we always publish a clean catalog.
        if purge_legacy:
            _purge_legacy_baseline_from_catalog(catalog)
        microdramas_iq.write_catalog(catalog)
        print(f'  merged into catalog ({len(catalog.get("titles") or {})} titles total)')
    except Exception as e:
        print(f'  ! catalog merge failed: {e}')


def main() -> int:
    ap = argparse.ArgumentParser(description='Peacock microdramas scraper.')
    ap.add_argument('--seed', action='store_true',
                     help='Skip live pull; write curated baseline only. '
                          'Automatically purges retired baseline rows '
                          'from the persistent catalog before merge.')
    ap.add_argument('--dry-run', action='store_true',
                     help='Print payload to stdout, do not upload.')
    ap.add_argument('--purge-legacy', action='store_true',
                     help='Force the legacy-baseline purge even on a '
                          'live-pull run. Safe on any run.')
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

    # A seed run always purges the retired baseline rows, so the
    # persistent catalog matches the baseline in this file. Live runs
    # opt in via --purge-legacy.
    _write_snapshot(payload, purge_legacy=(args.seed or args.purge_legacy))
    return 0


if __name__ == '__main__':
    sys.exit(main())
