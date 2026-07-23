"""
GoodShort microdramas scraper.

Pulls the top vertical-drama titles on GoodShort (www.goodshort.com web
storefront). GoodShort is the #3-#4 vertical-drama app in North America
with roughly 5-7M MAU (data.ai, Q1 2026), owned by NewTV. Coin-economy
model identical to ReelShort / DramaBox.

## Data source

`https://www.goodshort.com/` renders server-side with a big
`window.__NUXT__` state blob (~118KB) inside an inline script. That
blob contains 8+ shelf arrays keyed as `"items": [...]`, each holding
6 books with the same schema DramaBox uses:

  - `bookId`, `bookName`, `name`
  - `chapterCount`      -> episode count
  - `viewCount`         -> total reads (raw int)
  - `viewCountDisplay`  -> preformatted ("32.6M", "610.3K")
  - `bookResourceUrl`   -> deep-link slug
  - `bannerUrl`         -> poster URL (acf.goodshort.com CDN)
  - `typeTwoNames`      -> archetype (['Romance'], ['Werewolf'])
  - `typeOneNames`      -> gender lead (['英文-女']='English-Female',
                             ['英文-男']='English-Male')
  - `top`               -> featured flag
  - `bannerColorLeft` / `bannerColorRight` -> gradient palette

Fully public. No cookies, no proxy, no auth needed.

Snapshot shape (matches microdramas_iq.integrate_competitor_snapshot):

    {
      "source":     "goodshort",
      "label":      "GoodShort",
      "fetched_at": ISO8601,
      "titles": [
        { "rank", "title", "poster_url", "deep_link", "book_id",
          "genre", "themes", "rail", "rail_position",
          "episodes_count", "read_count", "read_count_display",
          "lead" }
      ]
    }

Standalone dev run:
    python3 -m scripts.microdramas_scrapers.goodshort
    python3 -m scripts.microdramas_scrapers.goodshort --seed
    python3 -m scripts.microdramas_scrapers.goodshort --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


GOODSHORT_HOMEPAGE = 'https://www.goodshort.com/'

_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_0) '
    'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15'
)


# Reuse the same 6-bucket genre map DramaBox uses. GoodShort's
# typeTwoNames overlap heavily (Romance, Werewolf, Billionaire, ...)
_TAG_TO_GENRE = {
    'werewolf':          'Werewolf',
    'wolf':              'Werewolf',
    'alpha':             'Werewolf',
    'mate':              'Werewolf',
    'dragon':            'Werewolf',
    'vampire':           'Werewolf',
    'shifter':           'Werewolf',

    'billionaire':       'Billionaire',
    'trillionaire':      'Billionaire',
    'wealthy':           'Billionaire',
    'inheritance':       'Billionaire',
    'heiress':           'Billionaire',

    'ceo':               'CEO',
    'boss':              'CEO',
    'president':         'CEO',
    'romance':           'CEO',
    'sweet love':        'CEO',
    'flash marriage':    'CEO',
    'contract marriage': 'CEO',

    'mafia':             'Mafia',
    'gangster':          'Mafia',
    'assassin':          'Mafia',
    'bodyguard':         'Mafia',
    'hidden identity':   'Mafia',
    'undercover':        'Mafia',
    'son-in-law':        'Mafia',
    'overlord':          'Mafia',
    'big shot':          'Mafia',

    'revenge':           'Revenge',
    'rebirth':           'Revenge',
    'reborn':            'Revenge',
    'reckoning':         'Revenge',
    'betrayal':          'Revenge',
    'strong female':     'Revenge',

    'second chance':     'Second Chance',
    'divorce':           'Second Chance',
    'reunion':           'Second Chance',
    'pregnancy':         'Second Chance',
    'baby':              'Second Chance',
    'family':            'Second Chance',
    'friends to lovers': 'Second Chance',
    'love after marriage': 'Second Chance',
}


def _http_get(url: str) -> Optional[str]:
    try:
        import requests  # type: ignore
    except ImportError:
        logger.warning('goodshort: requests unavailable; skipping live pull')
        return None
    try:
        r = requests.get(url, timeout=15, headers={'User-Agent': _UA})
        if r.status_code != 200:
            logger.info('goodshort: GET %s -> %s', url, r.status_code)
            return None
        return r.text
    except Exception as e:
        logger.info('goodshort: GET %s failed (%s)', url, e)
        return None


def _tags_to_genre(tags: list[str], title: str) -> str:
    hay = ' '.join(tags or []).lower()
    for k, v in _TAG_TO_GENRE.items():
        if k in hay:
            return v
    tl = (title or '').lower()
    for k, v in _TAG_TO_GENRE.items():
        if k in tl:
            return v
    return ''


def _find_items_arrays(state_js: str) -> list[str]:
    """Return every "items": [...] JSON array whose content mentions
    bookId. Uses balanced-bracket scanning because the surrounding JS
    is minified and we can't rely on regex line boundaries."""
    out: list[str] = []
    for m in re.finditer(r'"items"\s*:\s*\[', state_js):
        start = m.end() - 1  # index of '['
        depth, i = 0, start
        in_str, esc = False, False
        while i < len(state_js):
            c = state_js[i]
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = not in_str
            elif not in_str:
                if c == '[':
                    depth += 1
                elif c == ']':
                    depth -= 1
                    if depth == 0:
                        arr_str = state_js[start:i + 1]
                        if 'bookId' in arr_str:
                            out.append(arr_str)
                        break
            i += 1
    return out


def _shape_book(b: dict, rail_position: int) -> dict:
    """Turn one GoodShort book dict into our snapshot row shape."""
    tags_two = b.get('typeTwoNames') or []
    tags_one = b.get('typeOneNames') or []
    if isinstance(tags_two, str):
        tags_two = [tags_two]
    if isinstance(tags_one, str):
        tags_one = [tags_one]

    book_id   = str(b.get('bookId') or b.get('action') or '').strip()
    slug      = str(b.get('bookResourceUrl') or '').strip()
    title     = str(b.get('bookName') or b.get('name') or '').strip()
    archetype = tags_two[0] if tags_two else ''
    # Their "英文-女"/"英文-男" values are Chinese for "English-Female" /
    # "English-Male". Translate for the frontend.
    lead_raw  = tags_one[0] if tags_one else ''
    lead = 'F-Drama' if '女' in lead_raw else ('M-Drama' if '男' in lead_raw else lead_raw)

    deep_link = ''
    if book_id and slug:
        deep_link = f'https://www.goodshort.com/drama/{slug}'
    elif book_id:
        deep_link = f'https://www.goodshort.com/drama/{book_id}'

    return {
        'title':             title,
        'series':            title,
        'book_id':           book_id,
        'poster_url':        b.get('bannerUrl') or b.get('coverUrl') or '',
        'deep_link':         deep_link,
        'tags':              tags_two,
        'themes':            tags_two,
        'genre':             _tags_to_genre(tags_two, title),
        'rail':              '',   # GoodShort's Nuxt state doesn't name
                                    # each shelf; we number them below.
        'rail_position':     rail_position,
        'episodes_count':    b.get('chapterCount'),
        'read_count':        b.get('viewCount'),
        'read_count_display': b.get('viewCountDisplay'),
        'lead':              lead,
        'archetype':         archetype,
        'is_new':            bool(b.get('top')),
    }


def _parse_books(state_js: str) -> list[dict]:
    """Walk every items array, dedupe by book_id, keep first sighting
    (earlier arrays in the state blob represent higher-priority shelves)."""
    arrays = _find_items_arrays(state_js)
    logger.info('goodshort: found %d shelf arrays', len(arrays))

    seen: dict[str, dict] = {}
    for shelf_idx, arr_str in enumerate(arrays):
        try:
            arr = json.loads(arr_str)
        except json.JSONDecodeError:
            continue
        if not isinstance(arr, list):
            continue
        for pos, b in enumerate(arr):
            if not isinstance(b, dict):
                continue
            row = _shape_book(b, pos + 1)
            bid = row.get('book_id')
            if not bid:
                continue
            # Preserve highest-signal sighting: the first shelf usually
            # is the featured/hero rail, next is Trending, etc. So the
            # FIRST time we see a bookId wins.
            if bid in seen:
                continue
            row['rail'] = f'Shelf {shelf_idx + 1}'
            seen[bid] = row
    return list(seen.values())


def _rank_books(books: list[dict]) -> list[dict]:
    """Rank by view_count desc (GoodShort's shelves don't publish
    explicit ranks, so total reads is the cleanest signal). Preserve
    top ties by earlier-shelf-appearance (`rail`)."""
    def _sort_key(b: dict) -> tuple:
        rail = b.get('rail') or ''
        try:
            shelf = int(rail.split()[-1])
        except Exception:
            shelf = 99
        return (
            -(b.get('read_count') or 0),
            shelf,
            b.get('rail_position') or 999,
        )
    ordered = sorted(books, key=_sort_key)
    for i, b in enumerate(ordered):
        b['rank'] = i + 1
    return ordered[:40]


def fetch_live() -> list[dict]:
    html = _http_get(GOODSHORT_HOMEPAGE)
    if not html:
        return []
    scripts = re.findall(r'<script[^>]*>(.+?)</script>', html, re.DOTALL)
    state_js = next((s for s in scripts if 'bookId' in s and len(s) > 50000), None)
    if not state_js:
        logger.info('goodshort: state script with bookId not found')
        return []
    books = _parse_books(state_js)
    if not books:
        return []
    ranked = _rank_books(books)
    logger.info('goodshort: %d books deduped -> %d ranked (top view %s)',
                 len(books), len(ranked),
                 ranked[0].get('read_count_display') if ranked else '?')
    return ranked


# ------------------------------------------------------------------
# Curated fallback for day-zero. Only used if live pull returns nothing.
# ------------------------------------------------------------------
CURATED_BASELINE = [
    {'rank':  1, 'title': "A Mistaken Surrogate for the Ruthless Billionaire",
     'genre': 'Billionaire', 'episodes_count': 75, 'themes': ['Romance']},
    {'rank':  2, 'title': "Mistaken for a Gold Digger",
     'genre': 'Billionaire', 'episodes_count': 68, 'themes': ['Romance']},
    {'rank':  3, 'title': "The Trash Heiress Is A Dragon Master",
     'genre': 'Werewolf', 'episodes_count': 61, 'themes': ['Fantasy']},
    {'rank':  4, 'title': "Billionaire Brothers and Their Country Brides",
     'genre': 'Billionaire', 'episodes_count': 59, 'themes': ['Romance']},
    {'rank':  5, 'title': "[ENG DUB] The Cub Who Bought the World",
     'genre': 'Billionaire', 'episodes_count': 56, 'themes': ['Romance']},
    {'rank':  6, 'title': "Step by Step into His Bed",
     'genre': 'CEO', 'episodes_count': 56, 'themes': ['Romance']},
    {'rank':  7, 'title': "Mommy, That's My Father",
     'genre': 'Second Chance', 'episodes_count': 81, 'themes': ['Family']},
    {'rank':  8, 'title': "The 100th Divorce Was Final",
     'genre': 'Second Chance', 'episodes_count': 72, 'themes': ['Divorce']},
    {'rank':  9, 'title': "[ENG DUB] Rise Built on Forgotten Betrayals",
     'genre': 'Revenge', 'episodes_count': 61, 'themes': ['Revenge']},
    {'rank': 10, 'title': "Wed by Mistake to the Cold-Blooded CEO",
     'genre': 'CEO', 'episodes_count': 66, 'themes': ['Romance']},
]


def fetch_baseline() -> list[dict]:
    return [dict(t) for t in CURATED_BASELINE]


def fetch() -> dict:
    titles = fetch_live()
    if not titles:
        logger.info('goodshort: live pull empty, using curated baseline')
        titles = fetch_baseline()
    return {
        'source': 'goodshort',
        'label':  'GoodShort',
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
    payload.setdefault('source', 'goodshort')
    payload['fetched_at'] = now.isoformat()

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')

    key_latest = 'microdramas_iq/snapshots/latest/goodshort.json'
    s3.put_object(Bucket=bucket, Key=key_latest, Body=body,
                   ContentType='application/json',
                   CacheControl='public, max-age=60')
    print(f'  wrote s3://{bucket}/{key_latest} '
           f'({len(body)} bytes, {len(payload.get("titles") or [])} titles)')

    key_dated = f'microdramas_iq/snapshots/{now.strftime("%Y-%m-%d")}/goodshort.json'
    s3.put_object(Bucket=bucket, Key=key_dated, Body=body,
                   ContentType='application/json')
    print(f'  wrote s3://{bucket}/{key_dated}')


def main() -> int:
    ap = argparse.ArgumentParser(description='GoodShort microdramas scraper.')
    ap.add_argument('--seed', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')

    payload = ({'source': 'goodshort', 'label': 'GoodShort',
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
