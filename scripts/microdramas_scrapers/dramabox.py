"""
DramaBox microdramas scraper.

Pulls the top vertical-drama titles on DramaBox (www.dramabox.com web
storefront). DramaBox is the second-largest vertical-drama app after
ReelShort with roughly 13M MAU (data.ai, Q1 2026).

## Data source

`https://www.dramabox.com/` renders server-side and inlines a
`__NEXT_DATA__` script whose `props.pageProps` includes:

  - `bigList`  : 3 hero/featured books with full detail
  - `smallData`: 3 curated rails, 6 books each:
      * `必看好剧` (Must Watch)
      * `当前热播` (Now Trending) - our primary chart signal
      * `精彩剧集` (Featured Dramas)

Public. No cookies, no proxy, no auth needed.

Per-book fields we capture:

  - `bookId`, `bookNameEn`     -> deep-link
      https://www.dramabox.com/drama/{bookId}/{bookNameLower}
  - `bookName`                 -> title
  - `cover`                    -> poster URL (thwztchapter.dramaboxdb.com)
  - `chapterCount`             -> total episode count
  - `viewCount` / `viewCountDisplay` -> views ("18.5K" style)
  - `tags`, `labels`           -> trope tags (Hidden Identity, Revenge,
                                   Family Bonds, The Chosen One, etc.)
  - `typeOneNames`             -> ['F-Drama'] or ['M-Drama'] (gender lead)
  - `typeTwoNames`             -> archetype (Billionaire, Son-in-Law, ...)
  - `introduction`             -> synopsis (feeds the audience agent)
  - `author`                   -> content-partner label

Snapshot shape (matches microdramas_iq.integrate_competitor_snapshot):

    {
      "source":     "dramabox",
      "label":      "DramaBox",
      "fetched_at": ISO8601,
      "titles": [
        { "rank", "title", "poster_url", "deep_link", "book_id",
          "genre", "themes", "rail", "rail_position",
          "episodes_count", "read_count", "read_count_display",
          "lead", "archetype", "introduction", "avg_rating" }
      ]
    }

Standalone dev run:
    python3 -m scripts.microdramas_scrapers.dramabox
    python3 -m scripts.microdramas_scrapers.dramabox --seed
    python3 -m scripts.microdramas_scrapers.dramabox --dry-run
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


DRAMABOX_HOMEPAGE = 'https://www.dramabox.com/'

# DramaBox's storefront is anonymous-friendly. Safari UA to look normal.
_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_0) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15'
)

_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.+?)</script>',
    re.DOTALL | re.IGNORECASE,
)


# Translate DramaBox's Chinese rail names into what we render on the
# dashboard. Falls back to the raw Chinese if we haven't mapped it.
_RAIL_TRANSLATIONS = {
    '必看好剧':  'Must Watch',
    '当前热播':  'Now Trending',
    '精彩剧集':  'Featured Dramas',
    '热门推荐':  'Hot Picks',
    '新剧上线':  'New Release',
    '男频精选':  'For Him',
    '女频精选':  'For Her',
}


# Rail priority for ranking. Titles surfaced on higher-priority rails
# win rank positions first; ties broken by rail_position, then
# view_count desc.
_RAIL_PRIORITY = ['__HERO__', 'Now Trending', 'Must Watch',
                  'Featured Dramas', 'New Release', 'Hot Picks']


# Map DramaBox's tag taxonomy to Crosswalk's 6-bucket genre scheme.
_TAG_TO_GENRE = {
    # Werewolf / supernatural
    'werewolf':          'Werewolf',
    'wolf':              'Werewolf',
    'alpha':             'Werewolf',
    'mate':              'Werewolf',
    'dragon':            'Werewolf',
    'vampire':           'Werewolf',
    'chosen one':        'Werewolf',   # fated/prophesied trope
    'a nobody':          'Werewolf',   # hero rises trope, adjacent

    # Billionaire / wealth
    'billionaire':       'Billionaire',
    'trillionaire':      'Billionaire',
    'wealthy':           'Billionaire',
    'inheritance':       'Billionaire',
    'lady diamond':      'Billionaire',
    'heiress':           'Billionaire',

    # CEO / boss / romance
    'ceo':               'CEO',
    'boss':              'CEO',
    'lady boss':         'CEO',
    'president':         'CEO',
    'sweet love':        'CEO',
    'sweet romance':     'CEO',
    'love at first':     'CEO',
    'flash marriage':    'CEO',
    'contract marriage': 'CEO',

    # Mafia / underworld / hidden identity
    'mafia':             'Mafia',
    'gangster':          'Mafia',
    'assassin':          'Mafia',
    'bodyguard':         'Mafia',
    'dark romance':      'Mafia',
    'hidden identity':   'Mafia',
    'hidden king':       'Mafia',
    'hidden boss':       'Mafia',
    'undercover':        'Mafia',
    'son-in-law':        'Mafia',      # "hidden identity" archetype
    'overlord':          'Mafia',
    'big shot':          'Mafia',

    # Revenge / rebirth
    'revenge':           'Revenge',
    'rebirth':           'Revenge',
    'reborn':            'Revenge',
    'payback':           'Revenge',
    'reckoning':         'Revenge',
    'betrayal':          'Revenge',
    'all-too-late':      'Revenge',    # regret-after-loss trope
    'strong female':     'Revenge',

    # Second Chance / family / pregnancy
    'second chance':     'Second Chance',
    'divorce':           'Second Chance',
    'reunion':           'Second Chance',
    'pregnancy':         'Second Chance',
    'baby':              'Second Chance',
    'babies':            'Second Chance',
    'friends to lovers': 'Second Chance',
    'family drama':      'Second Chance',
    'family bonds':      'Second Chance',
    'love after marriage': 'Second Chance',
    'playing dumb':      'Second Chance',
}


def _extract_next_data(html: str) -> Optional[dict]:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(unescape(m.group(1)))
    except json.JSONDecodeError:
        return None


def _http_get(url: str) -> Optional[str]:
    try:
        import requests  # type: ignore
    except ImportError:
        logger.warning('dramabox: requests unavailable; skipping live pull')
        return None
    try:
        r = requests.get(url, timeout=15, headers={'User-Agent': _UA})
        if r.status_code != 200:
            logger.info('dramabox: GET %s -> %s', url, r.status_code)
            return None
        return r.text
    except Exception as e:
        logger.info('dramabox: GET %s failed (%s)', url, e)
        return None


def _tags_to_genre(tags: list[str], archetype: str, title: str) -> str:
    """Reduce DramaBox tag array + typeTwoName + title to a Crosswalk
    6-bucket genre. Priority: tag -> archetype -> title keyword -> ''."""
    hay = ' '.join(tags or []).lower()
    for k, v in _TAG_TO_GENRE.items():
        if k in hay:
            return v
    arch_low = (archetype or '').lower()
    for k, v in _TAG_TO_GENRE.items():
        if k in arch_low:
            return v
    tl = (title or '').lower()
    for k, v in _TAG_TO_GENRE.items():
        if k in tl:
            return v
    return ''


def _rail_name(raw: str) -> str:
    return _RAIL_TRANSLATIONS.get(raw or '', raw or '')


def _shape_book(b: dict, rail_name: str, rail_position: int) -> dict:
    """Turn one DramaBox book dict into our snapshot row shape."""
    tags       = b.get('tags')   or b.get('labels') or []
    type_two   = b.get('typeTwoNames') or []
    type_one   = b.get('typeOneNames') or []
    if isinstance(tags, str):
        tags = [tags]
    if isinstance(type_two, str):
        type_two = [type_two]
    if isinstance(type_one, str):
        type_one = [type_one]

    book_id     = str(b.get('bookId') or b.get('action') or '').strip()
    book_slug   = str(b.get('bookNameLower') or b.get('bookNameEn') or '').strip()
    title       = str(b.get('bookName') or b.get('name') or '').strip()
    archetype   = type_two[0] if type_two else ''
    lead        = type_one[0] if type_one else ''
    read_count  = b.get('viewCount')
    read_disp   = b.get('viewCountDisplay')

    deep_link = ''
    if book_id and book_slug:
        deep_link = f'https://www.dramabox.com/drama/{book_id}/{book_slug}'
    elif book_id:
        deep_link = f'https://www.dramabox.com/drama/{book_id}'

    return {
        'title':             title,
        'series':            title,
        'book_id':           book_id,
        'poster_url':        b.get('cover') or b.get('coverUrl') or '',
        'deep_link':         deep_link,
        'tags':              tags,
        'themes':            tags,           # alias for the frontend chip renderer
        'genre':             _tags_to_genre(tags, archetype, title),
        'rail':              rail_name,
        'rail_position':     rail_position,
        'episodes_count':    b.get('chapterCount'),
        'read_count':        read_count,
        'read_count_display': read_disp,
        'lead':              lead,           # 'F-Drama' or 'M-Drama'
        'archetype':         archetype,
        'introduction':      (b.get('introduction') or '')[:600],
        'author':            b.get('author') or '',
        'is_new':            bool(b.get('top')),
    }


def _parse_books(pp: dict) -> list[dict]:
    """Walk bigList + smallData, dedupe by book_id, keep highest-
    priority rail sighting per book."""
    seen: dict[str, dict] = {}

    # 1. Featured heroes (bigList) - promoted, treat as HERO rail
    for pos, b in enumerate(pp.get('bigList') or []):
        row = _shape_book(b, '__HERO__', pos + 1)
        # Display name for the hero rail
        row['rail'] = 'Featured Hero'
        if row.get('book_id'):
            seen[row['book_id']] = row

    # 2. Every named rail in smallData
    for rail in pp.get('smallData') or []:
        rail_name = _rail_name(rail.get('name') or '')
        for pos, b in enumerate(rail.get('items') or []):
            row = _shape_book(b, rail_name, pos + 1)
            bid = row.get('book_id')
            if not bid:
                continue
            if bid in seen:
                seen[bid].setdefault('also_on_rails', []).append(rail_name)
                continue
            seen[bid] = row

    return list(seen.values())


def _rank_books(books: list[dict]) -> list[dict]:
    """Rank the deduped set.
    Priority order:
      1. Featured heroes (bigList) keep rail_position
      2. Then Now Trending -> Must Watch -> Featured Dramas
      3. Ties: rail_position asc, then read_count desc
    Cap at 40."""
    def _sort_key(b: dict) -> tuple:
        rail = b.get('rail') or ''
        # Featured Hero mapped to slot 0
        if rail == 'Featured Hero':
            priority = 0
        else:
            try:
                priority = _RAIL_PRIORITY.index(rail) + 1
            except ValueError:
                priority = len(_RAIL_PRIORITY) + 1
        return (
            priority,
            b.get('rail_position') or 999,
            -(b.get('read_count') or 0),
        )

    ordered = sorted(books, key=_sort_key)
    for i, b in enumerate(ordered):
        b['rank'] = i + 1
    return ordered[:40]


def fetch_live() -> list[dict]:
    html = _http_get(DRAMABOX_HOMEPAGE)
    if not html:
        return []
    data = _extract_next_data(html)
    if not data:
        logger.info('dramabox: __NEXT_DATA__ script not found on homepage')
        return []
    try:
        pp = data['props']['pageProps']
    except (KeyError, TypeError) as e:
        logger.info('dramabox: pageProps not in hydration blob (%s)', e)
        return []
    books = _parse_books(pp)
    if not books:
        return []
    ranked = _rank_books(books)
    logger.info('dramabox: %d books across %d rails -> %d ranked',
                 len(books),
                 len({b.get('rail') for b in books if b.get('rail')}),
                 len(ranked))
    return ranked


# ------------------------------------------------------------------
# Curated fallback (used only if live fetch returns nothing). Mirrors
# DramaBox's public shelves around Q2 2026 so day-zero renders sanely.
# ------------------------------------------------------------------
CURATED_BASELINE = [
    {'rank':  1, 'title': "Fear Her, My Mom's the Lady Boss!",
     'genre': 'CEO',         'episodes_count': 58, 'avg_rating': 4.7,
     'rail':  'Now Trending', 'themes': ['Family Bonds', 'Strong Female Lead']},
    {'rank':  2, 'title': "Silence! Boss Lady Speaks",
     'genre': 'Revenge',     'episodes_count': 63, 'avg_rating': 4.6,
     'rail':  'Featured Dramas', 'themes': ['Strong Female Lead', 'Revenge']},
    {'rank':  3, 'title': "Definitely Not The Dragon God",
     'genre': 'Werewolf',    'episodes_count': 68, 'avg_rating': 4.8,
     'rail':  'Must Watch', 'themes': ['A Nobody', 'The Chosen One']},
    {'rank':  4, 'title': "Step Back! I'm the Hidden King",
     'genre': 'Mafia',       'episodes_count': 63, 'avg_rating': 4.7,
     'rail':  'Featured Hero', 'themes': ['Hidden Identity', 'All-Too-Late']},
    {'rank':  5, 'title': "Love The Way You Lie",
     'genre': 'Second Chance', 'episodes_count': 59, 'avg_rating': 4.6,
     'rail':  'Featured Hero', 'themes': ['Love After Marriage', 'Revenge']},
    {'rank':  6, 'title': "Tempest: The Last Mecha",
     'genre': 'Revenge',     'episodes_count': 58, 'avg_rating': 4.5,
     'rail':  'Featured Hero', 'themes': ['The Chosen One', 'Revenge']},
    {'rank':  7, 'title': "Guess Who They Miss Now",
     'genre': 'Second Chance', 'episodes_count': 58, 'avg_rating': 4.5,
     'rail':  'Now Trending', 'themes': ['Second Chance']},
    {'rank':  8, 'title': "Think Again! I'm the Hidden Boss Mom",
     'genre': 'Mafia',       'episodes_count': 52, 'avg_rating': 4.6,
     'rail':  'Now Trending', 'themes': ['Hidden Identity']},
    {'rank':  9, 'title': "My Brother's Wrath Awaits",
     'genre': 'Revenge',     'episodes_count': 61, 'avg_rating': 4.7,
     'rail':  'Featured Dramas', 'themes': ['Revenge']},
    {'rank': 10, 'title': "The Unrivaled Overlord",
     'genre': 'Mafia',       'episodes_count': 93, 'avg_rating': 4.7,
     'rail':  'Featured Dramas', 'themes': ['Overlord']},
    {'rank': 11, 'title': "No Escape as the Dragon King's Mate",
     'genre': 'Werewolf',    'episodes_count': 52, 'avg_rating': 4.8,
     'rail':  'Now Trending', 'themes': ['Werewolf']},
    {'rank': 12, 'title': "Don't Mess With The Billionaire Sister",
     'genre': 'Billionaire', 'episodes_count': 54, 'avg_rating': 4.6,
     'rail':  'Must Watch', 'themes': ['Billionaire']},
]


def fetch_baseline() -> list[dict]:
    return [dict(t) for t in CURATED_BASELINE]


def fetch() -> dict:
    titles = fetch_live()
    if not titles:
        logger.info('dramabox: live pull empty, using curated baseline')
        titles = fetch_baseline()
    return {
        'source': 'dramabox',
        'label':  'DramaBox',
        'kind':   'microdramas_competitor',
        'titles': titles,
    }


def _write_snapshot(payload: dict) -> None:
    try:
        import boto3  # type: ignore
    except ImportError:
        sys.exit("boto3 required.")

    bucket = os.environ.get('MICRODRAMAS_IQ_BUCKET', 'dashboard-inputs')
    now = datetime.now(timezone.utc)
    payload = dict(payload or {})
    payload.setdefault('source', 'dramabox')
    payload['fetched_at'] = now.isoformat()

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')

    key_latest = 'microdramas_iq/snapshots/latest/dramabox.json'
    s3.put_object(Bucket=bucket, Key=key_latest, Body=body,
                   ContentType='application/json',
                   CacheControl='public, max-age=60')
    print(f'  wrote s3://{bucket}/{key_latest} '
           f'({len(body)} bytes, {len(payload.get("titles") or [])} titles)')

    key_dated = f'microdramas_iq/snapshots/{now.strftime("%Y-%m-%d")}/dramabox.json'
    s3.put_object(Bucket=bucket, Key=key_dated, Body=body,
                   ContentType='application/json')
    print(f'  wrote s3://{bucket}/{key_dated}')


def main() -> int:
    ap = argparse.ArgumentParser(description='DramaBox microdramas scraper.')
    ap.add_argument('--seed', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')

    payload = ({'source': 'dramabox', 'label': 'DramaBox',
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
