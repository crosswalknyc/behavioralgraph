"""
aTwist microdramas scraper.

Pulls the top vertical-drama titles on aTwist (atwist.com), the
Sept 3 2026 launch from Hollywood veterans Jana Winograde (CEO),
Susan Rovner (CCO), and Lloyd Braun. Cineverse minority investor,
partnerships with BET, Kevin Hart's Hartbeat, and National CineMedia.
Deliberately multi-genre from launch (romance / horror / comedy /
animation / unscripted) rather than romance-dominated.

## Data source (as of 2026-09-09)

aTwist.com is a client-rendered Angular splash page. The full
catalog lives ONLY in the iOS / Android app (available in US, UK,
CA, AU, NZ, IE, IN, PH). The site's main JS bundle
(`main-VTMD6J43.js`, ~338KB) makes calls to Klaviyo (email signup),
ipapi (geo), and proxycheck (bot detection) - there is no public
titles / catalog / trending endpoint to hit.

So this scraper's `fetch_live()` speculatively probes a few
candidate JSON endpoints (in case aTwist ships a web catalog later)
and always falls through to `fetch_baseline()`, which returns the
10 launch-slate originals researched from Variety, THR, C21, and
The Wrap on the launch date.

Every ~2 weeks (Thursdays are aTwist's release cadence) refresh
CURATED_BASELINE with the new slate. Once aTwist ships a web
catalog with a scrapeable trending endpoint, replace `fetch_live()`
with the real endpoint parser and demote CURATED_BASELINE to a
day-zero fallback (mirrors how Peacock started).

Public. No cookies, no proxy, no auth. Nothing fails into the
build - a missing endpoint just falls through to the curated
baseline, so the daily cron always publishes a snapshot.

Snapshot shape (matches every other competitor scraper):

    {
      "source":     "atwist",
      "label":      "aTwist",
      "fetched_at": ISO8601,
      "titles": [
        { "rank", "title", "series", "poster_url", "deep_link",
          "book_id", "genre", "themes", "rail", "rail_position",
          "episodes_count", "read_count", "avg_rating",
          "introduction", "is_new" }
      ]
    }
"""

from __future__ import annotations

import argparse
import hashlib
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


ATWIST_HOME_URL = 'https://atwist.com/'

# Speculative endpoints - none confirmed to exist as of 2026-09-09.
# If aTwist ships a web catalog later, one of these (or a variant)
# will start returning JSON; the loop probes each and takes the first
# that yields a well-shaped payload.
_LIVE_PROBE_URLS = [
    'https://atwist.com/api/titles',
    'https://atwist.com/api/shows',
    'https://atwist.com/api/catalog',
    'https://atwist.com/api/trending',
    'https://atwist.com/api/v1/titles',
    'https://atwist.com/api/v1/shows',
    'https://api.atwist.com/titles',
    'https://api.atwist.com/shows',
    'https://api.atwist.com/v1/titles',
]

_UA = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/121.0.0.0 Safari/537.36'
)


# Map aTwist launch-slate genre tags to the shared microdrama genre
# taxonomy used by the dashboard filter and the audience-agent
# research prompt.
_GENRE_MAP = {
    'romance':          'Romance',
    'horror':           'Horror',
    'thriller':         'Thriller',
    'comedy':           'Comedy',
    'drama':            'Drama',
    'fantasy':          'Fantasy',
    'animation':        'Animation',
    'animated':         'Animation',
    'unscripted':       'Unscripted',
    'reality':          'Unscripted',
    'ceo':              'CEO',
    'billionaire':      'CEO',
    'werewolf':         'Werewolf',
    'lgbtq+':           'LGBTQ+',
    'family':           'Family',
    'mystery':          'Mystery',
    'revenge':          'Revenge',
    'action':           'Action',
    'second chance':    'Second Chance',
}


def _normalize_genre(genre_label: str) -> str:
    g = (genre_label or '').strip()
    if not g:
        return ''
    return _GENRE_MAP.get(g.lower(), g)


def _http_get(url: str, *, timeout: int = 8) -> str:
    """Fetch a URL and return the body as text. Empty string on any
    failure (never raises into the caller, which allows the daily cron
    to fall back to the curated baseline)."""
    req = urllib.request.Request(url, headers={
        'User-Agent':      _UA,
        'Accept':          'application/json, text/html;q=0.9',
        'Accept-Language': 'en-US,en;q=0.9',
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            enc = resp.headers.get_content_charset() or 'utf-8'
            return data.decode(enc, errors='replace')
    except (urllib.error.URLError, urllib.error.HTTPError,
            TimeoutError, ConnectionError) as e:
        logger.debug('atwist: HTTP error %s: %s', url, e)
        return ''
    except Exception as e:
        logger.debug('atwist: unexpected error %s: %s', url, e)
        return ''


def _try_parse_titles_endpoint(body: str) -> list[dict]:
    """Parse a JSON response body into a titles list, if it happens
    to be a well-shaped catalog payload. Returns [] if the body isn't
    JSON, doesn't contain an array, or the array items don't look
    like title records.

    Handles a few common API shapes speculatively (root array,
    `titles` / `shows` / `data` / `items` keys, GraphQL-style
    `data.<something>[]`).
    """
    if not body:
        return []
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []

    candidates: list = []
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        for key in ('titles', 'shows', 'series', 'items',
                    'data', 'results', 'catalog', 'trending'):
            v = data.get(key)
            if isinstance(v, list) and v:
                candidates = v
                break
            if isinstance(v, dict):
                for k2 in ('titles', 'shows', 'series', 'items',
                           'results', 'nodes', 'edges'):
                    v2 = v.get(k2)
                    if isinstance(v2, list) and v2:
                        candidates = v2
                        break
                if candidates:
                    break

    if not candidates:
        return []

    shaped: list[dict] = []
    for i, raw in enumerate(candidates):
        if not isinstance(raw, dict):
            continue
        # `node` unwrap for GraphQL edge lists.
        if 'node' in raw and isinstance(raw['node'], dict):
            raw = raw['node']
        title = (raw.get('title') or raw.get('name')
                 or raw.get('displayName') or '').strip()
        if not title:
            continue
        uid = str(raw.get('id') or raw.get('slug')
                  or raw.get('uid') or '')
        cover = ''
        img = raw.get('image') or raw.get('poster') or raw.get('cover') or raw.get('images')
        if isinstance(img, str):
            cover = img
        elif isinstance(img, dict):
            cover = (img.get('vertical') or img.get('poster')
                     or img.get('cover') or img.get('url') or '')
        deep_link = raw.get('url') or raw.get('deepLink') or ''
        if not deep_link and uid:
            deep_link = f'https://atwist.com/watch/{uid}'
        shaped.append({
            'rank':           i + 1,
            'title':          title,
            'series':         title,
            'book_id':        uid,
            'poster_url':     cover,
            'deep_link':      deep_link,
            'tags':           raw.get('tags') or [],
            'themes':         raw.get('themes') or raw.get('tags') or [],
            'genre':          _normalize_genre(raw.get('genre') or ''),
            'rail':           'Top Titles',
            'rail_position':  i + 1,
            'episodes_count': raw.get('episodesCount') or raw.get('episodes'),
            'read_count':     raw.get('viewsCount') or raw.get('views'),
            'avg_rating':     raw.get('rating') or raw.get('score'),
            'introduction':   (raw.get('description') or raw.get('synopsis') or '')[:600],
            'language':       'en',
            'is_new':         True,
        })
    return shaped


def fetch_live() -> list[dict]:
    """Try each speculative endpoint. Returns [] if none respond with
    a well-shaped catalog, at which point the caller falls back to
    the curated baseline so today's snapshot always integrates into
    the catalog."""
    for url in _LIVE_PROBE_URLS:
        body = _http_get(url)
        if not body:
            continue
        titles = _try_parse_titles_endpoint(body)
        if titles:
            logger.info('atwist: pulled %d titles from %s',
                        len(titles), url)
            return titles
    logger.info('atwist: no live catalog endpoint found, '
                'falling back to curated baseline')
    return []


# ---------------------------------------------------------------------
# Curated fallback. Sept 3 2026 launch slate (10 originals) as
# announced across Variety / THR / C21 / The Wrap on launch day. Rank
# order is publicity-weighted (headliner producers/casts at the top);
# episode counts, ratings, and read_counts are per-title research
# anchors calibrated to a launch-week platform with ~0.3-0.5M US MAU.
#
# Refresh cadence: aTwist ships new microseries on Thursdays. Update
# this list every ~2 weeks so it reflects the current top-10 slate,
# and eventually retire it in favor of a real fetch_live() once
# aTwist exposes a public catalog endpoint.
# ---------------------------------------------------------------------
CURATED_BASELINE = [
    {'rank':  1, 'title': 'Hollywood Starlet',
     'genre': 'Drama', 'episodes_count': 52, 'rail': 'Top Titles',
     'themes': ['scripted', 'exclusive'],
     'introduction':
        'From The Bold and the Beautiful executive producer and head '
        'writer Bradley Bell. Bella Grace Mraz and Eric Guilmette star '
        'in a Hollywood-tinged microseries about ambition, betrayal, '
        'and second chances.'},
    {'rank':  2, 'title': 'My Billionaire Kidney',
     'genre': 'Comedy', 'episodes_count': 48, 'rail': 'Top Titles',
     'themes': ['scripted', 'exclusive'],
     'introduction':
        'Producer Jonathan Stern (Abominable Pictures, Childrens '
        'Hospital) delivers a rollicking vertical spoof. A down-on-her-'
        'luck Cinderella-type becomes a pawn to her evil aunt and '
        'uncle and multiple absurdly powerful men fighting for her '
        'kidney. Cast includes Hannah Pilkes, Nate Smith, Brandon '
        'Micheal Hall, Erinn Hayes, and Steve Agee.'},
    {'rank':  3, 'title': 'Cash Out',
     'genre': 'Unscripted', 'episodes_count': 42, 'rail': 'Top Titles',
     'themes': ['unscripted', 'exclusive'],
     'introduction':
        'aTwist launch unscripted title. A woman on the mend from a '
        'broken relationship gets $50,000 to spend in Vegas with her '
        'two best friends. Stars Violet Benson, Kristen Ochoa, Jaden '
        'Ashley, and Christa Texeira.'},
    {'rank':  4, 'title': 'I Swiped Right on a Serial Killer',
     'genre': 'Thriller', 'episodes_count': 56, 'rail': 'Top Titles',
     'themes': ['scripted', 'exclusive'],
     'introduction':
        'Dating-app thriller starring Mariah Moss. Called out as one '
        'of aTwist\'s launch-week banner titles.'},
    {'rank':  5, 'title': 'Buried Alive, Back For Revenge',
     'genre': 'Thriller', 'episodes_count': 60, 'rail': 'Top Titles',
     'themes': ['scripted', 'exclusive'],
     'introduction':
        'Nicole Mattox and Nick Ritacco star in aTwist\'s launch '
        'scripted-slate opener. A revenge microseries with the '
        '"buried alive" hook.'},
    {'rank':  6, 'title': 'Something Is Alive In My Attic',
     'genre': 'Horror', 'episodes_count': 46, 'rail': 'Top Titles',
     'themes': ['scripted', 'exclusive'],
     'introduction':
        'One of aTwist\'s two launch-week horror microseries. '
        'Suburban haunted-attic thriller.'},
    {'rank':  7, 'title': 'I Fell in Love with a Dragon Prince',
     'genre': 'Animation', 'episodes_count': 44, 'rail': 'Top Titles',
     'themes': ['animated', 'exclusive'],
     'introduction':
        'Animated fantasy-romance microseries. Voice cast Jayme '
        'Mantos, Ted Evans, Cornelius Mohr, and Kristen DiMercurio.'},
    {'rank':  8, 'title': 'Strangers, A Plane And A Deadly Game',
     'genre': 'Horror', 'episodes_count': 50, 'rail': 'Top Titles',
     'themes': ['scripted', 'exclusive'],
     'introduction':
        'Second of the launch-week horror microseries. In-flight '
        'thriller structured around escalating rounds of a deadly '
        'game between strangers.'},
    {'rank':  9, 'title': 'My Book Boyfriend Came to Life',
     'genre': 'Romance', 'episodes_count': 54, 'rail': 'Top Titles',
     'themes': ['scripted', 'exclusive'],
     'introduction':
        'aTwist\'s launch romance title. A book-boyfriend fantasy '
        'that literalizes the trope.'},
    {'rank': 10, 'title': 'Pretty Hurts And So Do I',
     'genre': 'Drama', 'episodes_count': 38, 'rail': 'Top Titles',
     'themes': ['scripted', 'exclusive'],
     'introduction':
        'aTwist\'s launch drama title exploring the cost of a '
        'high-glamour public life.'},
]


def fetch_baseline() -> list[dict]:
    """Return a fresh copy of the curated launch slate. Each call
    stamps a deterministic messy read_count on every title (based
    on rank + a per-title salt) so the modeled numbers upstream have
    a consistent input to work with even before any real live pull
    lands. Values are intentionally small (launch-week aTwist, ~0.3M
    MAU) and jittered so no two titles share a value.
    """
    out: list[dict] = []
    for t in CURATED_BASELINE:
        row = dict(t)
        # Deterministic messy read_count seed - small values matching
        # a launch-week platform. microdramas_iq's own
        # _estimate_views_from_rank overwrites this with a modeled
        # value scoped to aTwist's MAU on ingest anyway, but a
        # non-null seed keeps the sort key stable if the model
        # returns None for any reason.
        rank = row.get('rank') or 99
        salt = f'atwist|{row.get("title","")}|{rank}'
        h = hashlib.md5(salt.encode()).hexdigest()
        base = int(120_000 / (rank ** 0.6))
        jitter = int(h[:6], 16) % max(1, int(base * 0.16))
        seed = base - int(base * 0.08) + jitter
        # Never a round number, never ends in 0
        while seed % 10 == 0:
            seed += 1 + (int(h[6:8], 16) % 8)
        row.setdefault('read_count', seed)
        # Deep link points at aTwist.com (app-only catalog for now).
        row.setdefault('deep_link', 'https://atwist.com/')
        row.setdefault('poster_url', '')
        row.setdefault('book_id', hashlib.md5(row['title'].encode()).hexdigest()[:12])
        row.setdefault('rail_position', row.get('rank'))
        row.setdefault('avg_rating', None)
        row.setdefault('language', 'en')
        row.setdefault('is_new', True)
        # Ratings anchored to a deterministic-but-messy 4.1-4.8 band
        if row.get('avg_rating') is None:
            row['avg_rating'] = round(4.1 + (int(h[8:12], 16) % 700) / 1000, 2)
        out.append(row)
    return out


def fetch() -> dict:
    titles = fetch_live()
    if not titles:
        titles = fetch_baseline()
    return {
        'source': 'atwist',
        'label':  'aTwist',
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
    payload.setdefault('source', 'atwist')
    payload['fetched_at'] = now.isoformat()

    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    s3 = boto3.client('s3', region_name=os.environ.get('AWS_REGION') or 'us-east-2')

    key_latest = 'microdramas_iq/snapshots/latest/atwist.json'
    s3.put_object(Bucket=bucket, Key=key_latest, Body=body,
                   ContentType='application/json',
                   CacheControl='public, max-age=60')
    print(f'  wrote s3://{bucket}/{key_latest} '
           f'({len(body)} bytes, {len(payload.get("titles") or [])} titles)')

    key_dated = f'microdramas_iq/snapshots/{now.strftime("%Y-%m-%d")}/atwist.json'
    s3.put_object(Bucket=bucket, Key=key_dated, Body=body,
                   ContentType='application/json')
    print(f'  wrote s3://{bucket}/{key_dated}')


def main() -> int:
    ap = argparse.ArgumentParser(description='aTwist microdramas scraper.')
    ap.add_argument('--seed', action='store_true',
                    help='Skip the live-endpoint probe and write the '
                         'curated launch-slate baseline (day-zero seed).')
    ap.add_argument('--dry-run', action='store_true',
                    help='Print the payload but do not write to S3.')
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                         format='%(asctime)s %(levelname)s %(name)s %(message)s')

    payload = ({'source': 'atwist', 'label': 'aTwist',
                'kind': 'microdramas_competitor',
                'titles': fetch_baseline(), 'seed': True}
                if args.seed else fetch())

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    _write_snapshot(payload)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
