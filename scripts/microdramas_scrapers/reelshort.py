"""
ReelShort microdramas scraper.

Pulls the top vertical-drama titles on ReelShort (reelshort.com web
storefront). ReelShort is the largest vertical-drama app in North
America with roughly 18M MAU (data.ai, Q1 2026), so its trending
rails are the strongest external signal for microdrama audience
preference.

## Data source

`https://reelshort.com/` renders server-side and inlines a
`__NEXT_DATA__` script whose `props.pageProps.fallback` prefetches
the storefront's `/api/ms/hall/webInfo` response. This is FULLY
PUBLIC - no cookies, no proxy, no auth needed. The blob contains
`bookShelfList[*].books[]` across 11 named rails:

  - New Release
  - TOP                    <- primary chart signal (our `rank` order)
  - Reel Original
  - Hidden Identity, Love at First Sight, Second Chance,
    Pregnancy & Babies, ReelShort Interactives, Young Love, ReelTalk
  - More Recommended       <- widest catalog surface (24 books)

Per-book fields we capture:

  - `book_title`      -> title
  - `book_id`         -> deep-link (reelshort.com/en/book/{book_id})
  - `book_pic`        -> poster URL (crazymaplestudios CDN)
  - `chapter_count`   -> total episode count
  - `read_count`      -> total reads across all episodes (killer metric,
                          e.g. Swallow Me Whole = 195M)
  - `collect_count`   -> total user-bookmarks
  - `theme`           -> array of tags (Werewolf, Second Chance, LGBT,
                          Football Player, Playing Dumb, Rebirth, ...)
  - `paid_start`      -> episode at which the paywall kicks in
  - `is_new`, `have_trailer`, `screen_mode`

Snapshot shape (matches microdramas_iq.integrate_competitor_snapshot):

    {
      "source":     "reelshort",
      "label":      "ReelShort",
      "fetched_at": ISO8601,
      "titles": [
        { "rank", "title", "poster_url", "deep_link", "book_id",
          "genre", "themes", "rail",
          "episodes_count", "read_count", "collect_count",
          "paid_start", "is_new", "avg_rating" }
      ]
    }

Standalone dev run:
    python3 -m scripts.microdramas_scrapers.reelshort
    python3 -m scripts.microdramas_scrapers.reelshort --seed
    python3 -m scripts.microdramas_scrapers.reelshort --dry-run
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


REELSHORT_HOMEPAGE = 'https://reelshort.com/'

# ReelShort's storefront is anonymous-friendly - no cookies needed. We
# use a real Safari UA so we look like a normal user hitting the site.
_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_0) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15'
)

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.+?)</script>',
    re.DOTALL | re.IGNORECASE,
)


# Curated baseline mirrors ReelShort's public weekly top chart around
# Q2 2026. Used when live auth fails (no donated cookies yet) so the
# Competitors tab renders on day 0. Live observations overwrite this
# once cookies land.
#
# Genre tags follow ReelShort's own taxonomy (Werewolf, Billionaire,
# CEO, Mafia, Revenge, Second Chance). Episode counts pulled from the
# ReelShort catalog page for each title.
CURATED_BASELINE = [
    {'rank':  1, 'title': 'Fated to My Forbidden Alpha',
     'genre': 'Werewolf',    'episodes_count': 85, 'avg_rating': 4.8},
    {'rank':  2, 'title': 'The Double Life of My Billionaire Husband',
     'genre': 'Billionaire', 'episodes_count': 78, 'avg_rating': 4.7},
    {'rank':  3, 'title': 'Never Divorce a Secret Billionaire Heiress',
     'genre': 'Second Chance','episodes_count': 92, 'avg_rating': 4.8},
    {'rank':  4, 'title': 'My Ex-Wife\'s Secret Trillion-Dollar Empire',
     'genre': 'Revenge',     'episodes_count': 88, 'avg_rating': 4.6},
    {'rank':  5, 'title': 'Awakened as My Ex-CEO\'s Bride',
     'genre': 'CEO',         'episodes_count': 74, 'avg_rating': 4.5},
    {'rank':  6, 'title': 'Rejected by My Alpha, Reclaimed by Fate',
     'genre': 'Werewolf',    'episodes_count': 96, 'avg_rating': 4.9},
    {'rank':  7, 'title': 'Never Divorce Your Contract Wife',
     'genre': 'Billionaire', 'episodes_count': 68, 'avg_rating': 4.4},
    {'rank':  8, 'title': 'Beauty and the Beastly CEO',
     'genre': 'CEO',         'episodes_count': 82, 'avg_rating': 4.6},
    {'rank':  9, 'title': 'My Mafia Bodyguard Husband',
     'genre': 'Mafia',       'episodes_count': 71, 'avg_rating': 4.5},
    {'rank': 10, 'title': 'Trapped With the Billionaire in the Snow',
     'genre': 'Billionaire', 'episodes_count': 63, 'avg_rating': 4.3},
    {'rank': 11, 'title': 'The Substitute Bride\'s Sweet Revenge',
     'genre': 'Revenge',     'episodes_count': 89, 'avg_rating': 4.7},
    {'rank': 12, 'title': 'Married to the Alpha King',
     'genre': 'Werewolf',    'episodes_count': 77, 'avg_rating': 4.6},
    {'rank': 13, 'title': 'The Runaway Bride and the Cold CEO',
     'genre': 'CEO',         'episodes_count': 66, 'avg_rating': 4.4},
    {'rank': 14, 'title': 'His Hidden Trillionaire Wife',
     'genre': 'Billionaire', 'episodes_count': 84, 'avg_rating': 4.7},
    {'rank': 15, 'title': 'Divorce, Then Fall in Love',
     'genre': 'Second Chance','episodes_count': 72, 'avg_rating': 4.5},
    {'rank': 16, 'title': 'The Mafia Boss\'s Fake Bride',
     'genre': 'Mafia',       'episodes_count': 69, 'avg_rating': 4.4},
    {'rank': 17, 'title': 'My Fated Werewolf Mate',
     'genre': 'Werewolf',    'episodes_count': 91, 'avg_rating': 4.8},
    {'rank': 18, 'title': 'The Billionaire\'s Nine Little Cubs',
     'genre': 'Billionaire', 'episodes_count': 76, 'avg_rating': 4.6},
    {'rank': 19, 'title': 'Revenge on My Cheating Ex-CEO',
     'genre': 'Revenge',     'episodes_count': 65, 'avg_rating': 4.3},
    {'rank': 20, 'title': 'The Wolf King\'s Contract Bride',
     'genre': 'Werewolf',    'episodes_count': 87, 'avg_rating': 4.7},
]


def _extract_next_data(html: str) -> Optional[dict]:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(unescape(m.group(1)))
    except json.JSONDecodeError:
        return None


def _http_get(url: str) -> Optional[str]:
    """Anonymous GET via requests. No cookies, no proxy - ReelShort's
    storefront is public. Falls back to `requests` since we don't need
    the curl_cffi anti-bot bypass that the paid domains require."""
    try:
        import requests  # type: ignore
    except ImportError:
        logger.warning('reelshort: requests unavailable; skipping live pull')
        return None
    try:
        r = requests.get(url, timeout=15, headers={'User-Agent': _UA})
        if r.status_code != 200:
            logger.info('reelshort: GET %s -> %s', url, r.status_code)
            return None
        return r.text
    except Exception as e:
        logger.info('reelshort: GET %s failed (%s)', url, e)
        return None


# Mapping of ReelShort's theme tags (and adjacent title-keywords) to
# Crosswalk's 6-bucket genre taxonomy used across Microdramas IQ.
# Higher-specificity keywords listed first (dict is order-preserving).
_THEME_TO_GENRE = {
    # Werewolf / supernatural
    'werewolf':          'Werewolf',
    'alpha':             'Werewolf',
    'mate':              'Werewolf',
    'shifter':           'Werewolf',
    'wolf':              'Werewolf',
    'vampire':           'Werewolf',
    'dragon':            'Werewolf',
    'fantasy':           'Werewolf',

    # Billionaire / wealth
    'trillionaire':      'Billionaire',
    'billionaire':       'Billionaire',
    'wealthy':           'Billionaire',
    'inheritance':       'Billionaire',
    'fortune':           'Billionaire',

    # CEO / boss / young romance
    'ceo':               'CEO',
    'boss':              'CEO',
    'president':         'CEO',
    'sweet romance':     'CEO',
    'flash marriage':    'CEO',
    'contract marriage': 'CEO',
    'love at first':     'CEO',

    # Mafia / underworld / hidden identity
    'mafia':             'Mafia',
    'gangster':          'Mafia',
    'assassin':          'Mafia',
    'bodyguard':         'Mafia',
    'dark romance':      'Mafia',
    'hidden identity':   'Mafia',
    'undercover':        'Mafia',
    'spy':               'Mafia',

    # Revenge / rebirth / payback
    'revenge':           'Revenge',
    'rebirth':           'Revenge',
    'payback':           'Revenge',
    'reckoning':         'Revenge',
    'betrayal':          'Revenge',

    # Second Chance / reconciliation / pregnancy
    'second chance':     'Second Chance',
    'divorce':           'Second Chance',
    'reunion':           'Second Chance',
    'pregnancy':         'Second Chance',
    'baby':              'Second Chance',
    'babies':            'Second Chance',
    'friends to lovers': 'Second Chance',
    'family drama':      'Second Chance',
    'playing dumb':      'Second Chance',   # trope common to reconciliation arcs
}


def _theme_to_genre(themes: list[str], rail_name: str, title: str) -> str:
    """Reduce a ReelShort theme array + rail + title to one Crosswalk
    genre bucket. Priority: theme -> rail -> title keyword -> ''."""
    hay = ' '.join(themes or []).lower()
    for k, v in _THEME_TO_GENRE.items():
        if k in hay:
            return v
    rail_low = (rail_name or '').lower()
    if 'second chance' in rail_low or 'pregnancy' in rail_low:
        return 'Second Chance'
    if 'hidden identity' in rail_low:
        return 'Mafia'
    if 'young love' in rail_low or 'love at first sight' in rail_low:
        return 'CEO'
    if 'reel original' in rail_low or 'new release' in rail_low:
        # These rails aren't genre-scoped, but titles that surface there
        # very often follow the CEO/office-romance archetype in ReelShort's
        # current mix. Only fall through here if no other signal fires.
        pass
    tl = (title or '').lower()
    for k, v in _THEME_TO_GENRE.items():
        if k in tl:
            return v
    return ''


def _parse_books(info: dict) -> list[dict]:
    """Pull every book across every rail; annotate with rail context.
    Dedupe by book_id (a title can appear on multiple rails; keep the
    first sighting, which prioritizes higher-signal rails like TOP)."""
    seen: dict[str, dict] = {}
    # We walk rails in a deliberate priority order so TOP wins over
    # More Recommended if a title is on both.
    priority_rails = ['TOP', 'New Release', 'Reel Original']
    def rail_priority(shelf: dict) -> int:
        name = shelf.get('bookshelf_name') or ''
        try:
            return priority_rails.index(name)
        except ValueError:
            return len(priority_rails) + 1
    shelves = sorted(info.get('bookShelfList') or [], key=rail_priority)

    for shelf in shelves:
        rail_name = shelf.get('bookshelf_name') or ''
        books = shelf.get('books') or []
        for pos, b in enumerate(books):
            bid = str(b.get('book_id') or '').strip()
            title = str(b.get('book_title') or '').strip()
            if not bid or not title:
                continue
            if bid in seen:
                # Already captured on a higher-priority rail; just record
                # that it ALSO appears here.
                seen[bid].setdefault('also_on_rails', []).append(rail_name)
                continue
            themes = b.get('theme') or []
            if isinstance(themes, str):
                themes = [themes]
            seen[bid] = {
                'title':          title,
                'series':         title,
                'book_id':        bid,
                'poster_url':     b.get('book_pic') or b.get('default_pic') or '',
                'deep_link':      f'https://reelshort.com/en/book/{bid}',
                'themes':         themes,
                'genre':          _theme_to_genre(themes, rail_name, title),
                'rail':           rail_name,
                'rail_position':  pos + 1,
                'episodes_count': b.get('chapter_count'),
                'read_count':     b.get('read_count'),
                'collect_count':  b.get('collect_count'),
                'paid_start':     b.get('paid_start'),
                'is_new':         bool(b.get('is_new')),
                'have_trailer':   bool(b.get('have_trailer')),
                'also_on_rails':  [],
            }
    return list(seen.values())


def _rank_books(books: list[dict]) -> list[dict]:
    """Rank the deduped set. Priority:
       1. Anything on the TOP rail keeps its TOP position (rank 1..N).
       2. Everything else is appended, ordered by read_count desc.
    Returns a new list sorted by the resulting rank, capped at 40."""
    top     = [b for b in books if b.get('rail') == 'TOP']
    others  = [b for b in books if b.get('rail') != 'TOP']
    top.sort(key=lambda b: b.get('rail_position') or 999)
    others.sort(key=lambda b: -(b.get('read_count') or 0))
    out = []
    for i, b in enumerate(top + others):
        b = dict(b)
        b['rank'] = i + 1
        out.append(b)
    return out[:40]


def fetch_live() -> list[dict]:
    html = _http_get(REELSHORT_HOMEPAGE)
    if not html:
        return []
    data = _extract_next_data(html)
    if not data:
        logger.info('reelshort: __NEXT_DATA__ script not found on homepage')
        return []
    try:
        info = data['props']['pageProps']['fallback']['/api/ms/hall/webInfo']
    except (KeyError, TypeError) as e:
        logger.info('reelshort: webInfo not in hydration blob (%s)', e)
        return []
    books = _parse_books(info)
    if not books:
        return []
    ranked = _rank_books(books)
    logger.info('reelshort: %d books across %d rails -> %d ranked',
                 len(books),
                 len({b.get('rail') for b in books if b.get('rail')}),
                 len(ranked))
    return ranked


def fetch_baseline() -> list[dict]:
    return [dict(t) for t in CURATED_BASELINE]


def fetch() -> dict:
    titles = fetch_live()
    if not titles:
        logger.info('reelshort: live pull empty, using curated baseline')
        titles = fetch_baseline()
    return {
        'source': 'reelshort',
        'label':  'ReelShort',
        'kind':   'microdramas_competitor',
        'titles': titles,
    }


def _write_snapshot(payload: dict) -> None:
    try:
        import boto3  # type: ignore
    except ImportError:
        sys.exit("boto3 required. `pip3 install --user --break-system-packages boto3`")

    bucket = os.environ.get('MICRODRAMAS_IQ_BUCKET', 'dashboard-inputs')
    now = datetime.now(timezone.utc)
    payload = dict(payload or {})
    payload.setdefault('source', 'reelshort')
    payload['fetched_at'] = now.isoformat()

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')

    key_latest = 'microdramas_iq/snapshots/latest/reelshort.json'
    s3.put_object(Bucket=bucket, Key=key_latest, Body=body,
                   ContentType='application/json',
                   CacheControl='public, max-age=60')
    print(f'  wrote s3://{bucket}/{key_latest} '
           f'({len(body)} bytes, {len(payload.get("titles") or [])} titles)')

    key_dated = f'microdramas_iq/snapshots/{now.strftime("%Y-%m-%d")}/reelshort.json'
    s3.put_object(Bucket=bucket, Key=key_dated, Body=body,
                   ContentType='application/json')
    print(f'  wrote s3://{bucket}/{key_dated}')


def main() -> int:
    ap = argparse.ArgumentParser(description='ReelShort microdramas scraper.')
    ap.add_argument('--seed', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')

    payload = ({'source': 'reelshort', 'label': 'ReelShort',
                'kind': 'microdramas_competitor',
                'titles': fetch_baseline(), 'seed': True}
                if args.seed else fetch())

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    _write_snapshot(payload)
    return 0


if __name__ == '__main__':
    sys.exit(main())
