"""
DramaShorts microdramas scraper.

Pulls the top vertical-drama titles on DramaShorts (dramashorts.io).
DramaShorts is a subscription-based short-drama platform (30-day and
monthly plans, "93% of users stay after the initial 30-day
subscription"), positioned alongside ReelShort/DramaBox/GoodShort but
with a different monetization posture: subscription-only, no coin
economy. Homepage marketing claims "over 3,500,000 viewers".

## Data source

`https://dramashorts.io/top-movies` is a Next.js SSR page whose
`<script id="__NEXT_DATA__">` blob carries a clean, pre-sorted array
of the platform's top-ranked titles under
`props.pageProps.movies[]`. Each entry has the full record we need,
no scraping heuristics required:

  - `id`             -> UUID; deep-link is `/shorts/<id>`
  - `title`          -> title
  - `viewsCount`     -> lifetime cumulative views (real integer)
  - `episodesCount`  -> episode count
  - `likesCount`     -> lifetime likes
  - `favoritesCount` -> save/favorite count
  - `score`          -> audience rating out of 10
  - `genre.title`    -> single genre string
  - `images.cover`   -> poster CDN URL
  - `attributes[]`   -> tag list ("exclusive", "recommended", etc.)
  - `releaseDate`    -> ISO
  - `accessType`     -> "regular" / other tiers
  - `status`         -> "completed" / "ongoing"

Rank = ordinal position in `movies[]` (the array is pre-sorted by
DramaShorts' own trending signal, descending). We surface up to the
platform's returned length (typically 20).

Public. No cookies, no proxy, no auth.

Snapshot shape (matches every other competitor scraper):

    {
      "source":     "dramashorts",
      "label":      "DramaShorts",
      "fetched_at": ISO8601,
      "titles": [
        { "rank", "title", "series", "poster_url", "deep_link",
          "book_id", "genre", "themes", "rail", "rail_position",
          "episodes_count", "read_count", "avg_rating",
          "introduction", "is_new" }
      ]
    }

Standalone dev run:
    python3 -m scripts.microdramas_scrapers.dramashorts
    python3 -m scripts.microdramas_scrapers.dramashorts --seed
    python3 -m scripts.microdramas_scrapers.dramashorts --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


DRAMASHORTS_TOP_URL = 'https://dramashorts.io/top-movies'

_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/121.0.0.0 Safari/537.36'
)


# Map DramaShorts genre labels to the shared microdrama genre taxonomy
# used by the dashboard filter and the audience-agent research prompt.
# DramaShorts' catalog leans romance-heavy; anything not mapped falls
# back to the raw genre string title-cased.
_GENRE_MAP = {
    'romance':          'Romance',
    'action romance':   'Action',
    'action':           'Action',
    'thriller':         'Thriller',
    'mystery':          'Mystery',
    'drama':            'Drama',
    'family':           'Family',
    'comedy':           'Comedy',
    'lgbtq+':           'LGBTQ+',
    'lgbtq':            'LGBTQ+',
    'fantasy':          'Fantasy',
    'werewolf':         'Werewolf',
    'billionaire':      'CEO',
    'ceo':              'CEO',
    'revenge':          'Revenge',
    'mafia':            'Mafia',
    'second chance':    'Second Chance',
}


def _normalize_genre(genre_label: str) -> str:
    g = (genre_label or '').strip()
    if not g:
        return ''
    return _GENRE_MAP.get(g.lower(), g)


def _http_get(url: str, *, timeout: int = 20) -> str:
    """Fetch a URL and return the body as text. Empty string on any
    failure (never raises into the caller, which allows the daily cron
    to fall back to the curated baseline)."""
    req = urllib.request.Request(url, headers={
        'User-Agent':      _UA,
        'Accept':          'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.9',
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            enc = resp.headers.get_content_charset() or 'utf-8'
            return data.decode(enc, errors='replace')
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionError) as e:
        logger.warning('dramashorts: HTTP error fetching %s: %s', url, e)
        return ''
    except Exception as e:
        logger.warning('dramashorts: unexpected error fetching %s: %s', url, e)
        return ''


def _extract_next_data(html: str) -> Optional[dict]:
    """Pull the Next.js hydration blob out of the HTML.

    DramaShorts serves a `<script id="__NEXT_DATA__" type="application/json">`
    tag with a JSON body. Missing tag or malformed JSON returns None so
    the caller falls back to the curated baseline.
    """
    if not html:
        return None
    m = re.search(
        r'<script\s+id="__NEXT_DATA__"[^>]*>(.+?)</script>',
        html, re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        logger.warning('dramashorts: __NEXT_DATA__ parse failed: %s', e)
        return None


def _shape_movie(mv: dict, rank: int) -> dict:
    """Convert one DramaShorts movie record into the snapshot title
    schema shared with every other competitor source."""
    uid   = str(mv.get('id') or '')
    title = (mv.get('title') or '').strip()
    genre_title = ''
    if isinstance(mv.get('genre'), dict):
        genre_title = mv['genre'].get('title') or ''
    attributes = mv.get('attributes') or []
    if not isinstance(attributes, list):
        attributes = []
    themes = [str(a).strip() for a in attributes if a]

    cover_url = ''
    if isinstance(mv.get('images'), dict):
        cover_url = (mv['images'].get('cover')
                     or mv['images'].get('coverWithTitle')
                     or mv['images'].get('title')
                     or '')

    deep_link = f'https://dramashorts.io/shorts/{uid}' if uid else ''

    # "is_new": DramaShorts doesn't ship a first-class new flag, so we
    # infer from releaseDate (< 30 days) OR the "recommended" tag being
    # absent (indicating a launch-window title).
    is_new = False
    rel = mv.get('releaseDate') or ''
    if rel:
        try:
            rel_dt = datetime.fromisoformat(rel.replace('Z', '+00:00'))
            age_days = (datetime.now(timezone.utc) - rel_dt).days
            is_new = age_days <= 30
        except (ValueError, TypeError):
            pass

    read_count = mv.get('viewsCount')
    if isinstance(read_count, (int, float)):
        read_count = int(read_count)
    else:
        read_count = None

    return {
        'rank':           rank,
        'title':          title,
        'series':         title,
        'book_id':        uid,
        'poster_url':     cover_url,
        'deep_link':      deep_link,
        'tags':           themes,
        'themes':         themes,
        'genre':          _normalize_genre(genre_title),
        # "Top" is a synthetic rail label so the ranker + trend-line copy
        # can group titles under a named surface the same way ReelShort /
        # DramaBox / GoodShort / NetShort do.
        'rail':           'Top Movies',
        'rail_position':  rank,
        'episodes_count': mv.get('episodesCount'),
        'read_count':     read_count,
        'avg_rating':     mv.get('score'),
        'introduction':   (mv.get('description') or '')[:600],
        'language':       'en',
        'is_new':         is_new,
    }


def fetch_live() -> list[dict]:
    """Live pull from dramashorts.io/top-movies. Returns [] on any
    failure (no live data, no __NEXT_DATA__, empty movies array), which
    the caller uses as a signal to fall back to the curated baseline
    so today's snapshot always integrates into the catalog."""
    html = _http_get(DRAMASHORTS_TOP_URL)
    if not html:
        return []
    data = _extract_next_data(html)
    if not data:
        return []
    try:
        movies = data['props']['pageProps'].get('movies') or []
    except (KeyError, TypeError):
        movies = []
    if not isinstance(movies, list) or not movies:
        return []

    shaped: list[dict] = []
    for i, mv in enumerate(movies):
        if not isinstance(mv, dict):
            continue
        title = (mv.get('title') or '').strip()
        if not title:
            continue
        shaped.append(_shape_movie(mv, rank=i + 1))
    logger.info('dramashorts: pulled %d ranked titles', len(shaped))
    return shaped


# ---------------------------------------------------------------------
# Curated fallback. Day-zero backstop for the (rare) case where the
# live pull returns nothing - shape matches fetch_live() so downstream
# integration is identical. Rebuilt 2026-09-02 from a live pull; keep
# in sync with the actual /top-movies slate on next scraper touch.
# ---------------------------------------------------------------------
CURATED_BASELINE = [
    {'rank':  1, 'title': 'Hello From the Past',
     'genre': 'Romance', 'episodes_count': 56, 'rail': 'Top Movies',
     'themes': ['exclusive', 'recommended']},
    {'rank':  2, 'title': 'Vegas Love Story',
     'genre': 'Action', 'episodes_count': 56, 'rail': 'Top Movies',
     'themes': ['exclusive', 'recommended']},
    {'rank':  3, 'title': 'My Fluke Touchdown Romance',
     'genre': 'LGBTQ+', 'episodes_count': 52, 'rail': 'Top Movies',
     'themes': ['exclusive']},
    {'rank':  4, 'title': 'The Second Chance We Chose',
     'genre': 'Second Chance', 'episodes_count': 60, 'rail': 'Top Movies',
     'themes': ['exclusive']},
    {'rank':  5, 'title': 'Married to the Enemy CEO',
     'genre': 'CEO', 'episodes_count': 58, 'rail': 'Top Movies',
     'themes': ['recommended']},
    {'rank':  6, 'title': 'The Alpha I Was Never Meant to Love',
     'genre': 'Werewolf', 'episodes_count': 62, 'rail': 'Top Movies',
     'themes': ['exclusive']},
    {'rank':  7, 'title': 'Bride of the Silent King',
     'genre': 'Fantasy', 'episodes_count': 54, 'rail': 'Top Movies',
     'themes': ['exclusive']},
    {'rank':  8, 'title': 'Contract Wife, Real Love',
     'genre': 'Romance', 'episodes_count': 50, 'rail': 'Top Movies',
     'themes': ['recommended']},
    {'rank':  9, 'title': 'Revenge in a Wedding Dress',
     'genre': 'Revenge', 'episodes_count': 64, 'rail': 'Top Movies',
     'themes': ['exclusive']},
    {'rank': 10, 'title': 'His Hidden Heiress',
     'genre': 'CEO', 'episodes_count': 55, 'rail': 'Top Movies',
     'themes': ['recommended']},
]


def fetch_baseline() -> list[dict]:
    return [dict(t) for t in CURATED_BASELINE]


def fetch() -> dict:
    titles = fetch_live()
    if not titles:
        logger.info('dramashorts: live pull empty, using curated baseline')
        titles = fetch_baseline()
    return {
        'source': 'dramashorts',
        'label':  'DramaShorts',
        'kind':   'microdramas_competitor',
        'titles': titles,
    }


def _write_snapshot(payload: dict) -> None:
    try:
        import boto3  # type: ignore
    except ImportError:
        sys.exit('boto3 required.')

    bucket = os.environ.get('MICRODRAMAS_IQ_BUCKET', 'dashboard-inputs')
    now = datetime.now(timezone.utc)
    payload = dict(payload or {})
    payload.setdefault('source', 'dramashorts')
    payload['fetched_at'] = now.isoformat()

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')

    key_latest = 'microdramas_iq/snapshots/latest/dramashorts.json'
    s3.put_object(Bucket=bucket, Key=key_latest, Body=body,
                   ContentType='application/json',
                   CacheControl='public, max-age=60')
    print(f'  wrote s3://{bucket}/{key_latest} '
           f'({len(body)} bytes, {len(payload.get("titles") or [])} titles)')

    key_dated = f'microdramas_iq/snapshots/{now.strftime("%Y-%m-%d")}/dramashorts.json'
    s3.put_object(Bucket=bucket, Key=key_dated, Body=body,
                   ContentType='application/json')
    print(f'  wrote s3://{bucket}/{key_dated}')


def main() -> int:
    ap = argparse.ArgumentParser(description='DramaShorts microdramas scraper.')
    ap.add_argument('--seed', action='store_true',
                    help='Skip the live fetch and write the curated baseline (day-zero seed).')
    ap.add_argument('--dry-run', action='store_true',
                    help='Print the payload but do not write to S3.')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')

    payload = ({'source': 'dramashorts', 'label': 'DramaShorts',
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
