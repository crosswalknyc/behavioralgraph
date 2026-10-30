"""One simple genre per book row on the Trends Books tab.

The Books tab mixes charts that describe genre very differently: Apple
Books rows carry Apple's genre list, Libby rows carry OverDrive subject
tags, Wattpad rows carry a story category, and Amazon, Audible and
Goodreads rows carry nothing. To filter the tab by genre, every row is
mapped onto one short list (`GENRES`), and the same title reads the
same genre on every platform.

Where a row's genre comes from, first hit wins:
  1. its own source tags (Apple genres, Libby subjects, Wattpad
     category or rail);
  2. the same book on another chart in this payload (title with the
     subtitle dropped, plus the author's last name);
  3. the cached Apple catalog lookup (iTunes Search API, free, no key).

Lookups never run inside a page build. `annotate()` only reads the
cache; titles it could not resolve are handed to one background thread
that looks them up at a polite pace and writes the cache, and the next
recompute picks them up. Every resolved genre is also cached, so a
title keeps its genre after it drops off the chart that supplied it.

Cache: s3://dashboard-inputs/trends_iq_snapshots/system/book_genres.json
Run a fill by hand: python3 -m scripts.trends_scrapers.book_genres --fill
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import threading
import time
import urllib.parse
from datetime import date, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

S3_BUCKET = 'dashboard-inputs'
S3_KEY = 'trends_iq_snapshots/system/book_genres.json'

GENRES = (
    'Fantasy & Sci-Fi', 'Romance', 'Thriller & Mystery', 'Horror',
    'Action & Adventure', 'Historical Fiction', 'Literary & General Fiction',
    'Young Adult', 'Kids', 'Comics & Graphic Novels', 'Fan Fiction',
    'Biography & Memoir', 'Self-Help & Wellness', 'Business & Money',
    'History & Politics', 'True Crime', 'Religion & Spirituality',
    'Other Nonfiction', 'Magazines',
)
UNCLASSIFIED = 'Unclassified'

# Checked in order against each source tag; the first rule that matches
# the first specific tag decides. Order matters: "Fiction for Kids" is
# Kids, "Science Fiction" is not Other Nonfiction, "Travel & Adventure"
# is not Action & Adventure.
_RULES = (
    ('Kids', r'\bkids?\b|juvenile|children|picture book|\bbaby\b|toddler'),
    ('Young Adult', r'young adult|\bteens?\b|teen ?fiction'),
    ('Comics & Graphic Novels', r'comic|graphic (novel|book)|manga'),
    ('Fan Fiction', r'fan ?fiction|fanfic'),
    ('True Crime', r'true crime'),
    ('Horror', r'horror'),
    ('Fantasy & Sci-Fi', r'fantasy|science fiction|sci-?fi|paranormal|dystopi|vampire|werewolf|supernatural|mytholog'),
    ('Thriller & Mystery', r'myster|thriller|suspense|crime|detective|\bspy\b|espionage|noir'),
    ('Romance', r'romanc|romantic|chick ?lit|new ?adult|erotica'),
    ('Historical Fiction', r'historical'),
    ('Other Nonfiction', r'travel'),
    ('Action & Adventure', r'\baction\b|adventure|war fiction|westerns?'),
    ('Biography & Memoir', r'biograph|memoir'),
    ('Self-Help & Wellness', r'self-?improvement|self-?help|\bhealth\b|mind & body|psycholog|personal growth|wellness|parenting|relationships|family &'),
    ('Business & Money', r'business|financ|money|econom|management|leadership|investing|career|marketing'),
    ('History & Politics', r'\bhistory\b|politic|current events|military|sociolog|social science'),
    ('Religion & Spirituality', r'religio|spiritual|christian|bible|faith|inspiration'),
    ('Other Nonfiction', r'\bcook|\bfood\b|science|nature|sports?\b|\barts?\b|reference|lifestyle|\bhome\b|craft|education|language|\bmusic\b|\bpets?\b|games|puzzle|performing|photograph|humor \(non'),
)
_GENERIC = re.compile(r'^(books?|fiction & literature|fiction|literature|nonfiction|non-fiction|general)$', re.I)
_FICTION_FALLBACK = re.compile(r'fiction|literat|contemporary|humou?r|classic|poetry|short stor|lgbt|general|random|novel', re.I)
_NONFICTION_FALLBACK = re.compile(r'non-?fiction', re.I)
_COMPILED = tuple((g, re.compile(p, re.I)) for g, p in _RULES)

_WATTPAD_RAIL = {
    'wattpad_romance': 'Romance',
    'wattpad_fantasy': 'Fantasy & Sci-Fi',
    'wattpad_teen_fiction': 'Young Adult',
    'wattpad_fanfiction': 'Fan Fiction',
}


def classify(tags) -> Optional[str]:
    """One genre from an ordered list of source tags, or None."""
    tags = [str(t or '').strip() for t in (tags or []) if str(t or '').strip()]
    for t in tags:
        if _GENERIC.match(t):
            continue
        for genre, rx in _COMPILED:
            if rx.search(t):
                return genre
    if any(_NONFICTION_FALLBACK.search(t) for t in tags):
        return 'Other Nonfiction'
    if any(_FICTION_FALLBACK.search(t) for t in tags):
        return 'Literary & General Fiction'
    return None


_STOP = {'the', 'a', 'an', 'of', 'and', 'to', 'in', 'on', 'for', 'by'}


def _norm(s: str) -> str:
    s = re.sub(r'[^\w\s]+', ' ', str(s or '').lower())
    return ' '.join(t for t in s.split() if t not in _STOP)


def clean_title(title: str) -> str:
    """Title without subtitle, series note or edition note."""
    t = str(title or '')
    t = re.split(r'\s*[:(\[]|\s+-\s+|\s+—\s+', t, maxsplit=1)[0]
    return t.strip() or str(title or '').strip()


def _last_name(author: str) -> str:
    a = re.split(r',|&|\band\b|;', str(author or ''), maxsplit=1)[0]
    toks = [t for t in _norm(a).split() if len(t) > 1 and t not in ('jr', 'sr', 'ii', 'iii', 'md', 'phd')]
    return toks[-1] if toks else ''


def book_key(title: str, author: str = '') -> str:
    return f'{_norm(clean_title(title))}|{_last_name(author)}'


def _own_genre(it: dict, slug: str) -> Optional[str]:
    if slug.startswith('libby-magazine') or slug == 'magazine':
        return 'Magazines'
    g = classify(it.get('genres'))
    if g:
        return g
    g = classify(it.get('subjects'))
    if g:
        return g
    if slug.startswith('wattpad'):
        g = classify([it.get('genre_primary')]) if it.get('genre_primary') else None
        return g or _WATTPAD_RAIL.get(slug)
    return None


# ---------------------------------------------------------------- cache
_cache: dict = {}
_cache_at = 0.0
_cache_lock = threading.Lock()
_CACHE_TTL_S = 600
_MISS_RETRY_DAYS = 14


def _s3():
    import boto3
    return boto3.client('s3', region_name='us-east-1')


def load_cache(force: bool = False) -> dict:
    global _cache, _cache_at
    with _cache_lock:
        if not force and _cache and time.time() - _cache_at < _CACHE_TTL_S:
            return _cache
    try:
        body = _s3().get_object(Bucket=S3_BUCKET, Key=S3_KEY)['Body'].read()
        items = (json.loads(body) or {}).get('items') or {}
    except Exception as e:
        if 'NoSuchKey' not in str(e):
            logger.info('book_genres: cache read failed (%s)', e)
        items = {}
    with _cache_lock:
        _cache, _cache_at = items, time.time()
        return _cache


def save_cache(updates: dict) -> None:
    if not updates:
        return
    items = dict(load_cache(force=True))
    items.update(updates)
    body = json.dumps({'version': 1, 'items': items}, ensure_ascii=False).encode('utf-8')
    _s3().put_object(Bucket=S3_BUCKET, Key=S3_KEY, Body=body, ContentType='application/json')
    global _cache, _cache_at
    with _cache_lock:
        _cache, _cache_at = items, time.time()


def _cached_genre(entry) -> Optional[str]:
    return entry.get('g') if isinstance(entry, dict) else None


def _needs_lookup(entry) -> bool:
    if not isinstance(entry, dict):
        return True
    if entry.get('g'):
        return False
    try:
        return date.fromisoformat(entry.get('at') or '2000-01-01') < date.today() - timedelta(days=_MISS_RETRY_DAYS)
    except Exception:
        return True


# --------------------------------------------------------------- lookup
_ITUNES = 'https://itunes.apple.com/search'
_LOOKUP_SPACING_S = 3.2


def _title_match(want: str, got: str) -> bool:
    w, g = _norm(clean_title(want)), _norm(clean_title(got))
    return bool(w) and (w == g or g.startswith(w + ' ') or w.startswith(g + ' ') and len(g) > 6)


def lookup(title: str, author: str = '', session=None) -> Optional[dict]:
    """Apple catalog genre for one book, or None when nothing matched."""
    session = session or requests
    last = _last_name(author)
    term = f'{clean_title(title)} {author or ""}'.strip()
    for media in ('ebook', 'audiobook'):
        try:
            r = session.get(_ITUNES, params={'term': term, 'media': media, 'country': 'us', 'limit': 8}, timeout=10)
            if r.status_code == 403 or r.status_code == 429:
                raise RuntimeError(f'itunes throttled ({r.status_code})')
            results = (r.json() or {}).get('results') or []
        except RuntimeError:
            raise
        except Exception:
            results = []
        for res in results:
            name = res.get('trackName') or res.get('collectionName') or ''
            artist = _norm(res.get('artistName') or '')
            if not _title_match(title, name):
                continue
            if last and last not in artist.split():
                continue
            tags = res.get('genres') or [res.get('primaryGenreName')]
            g = classify(tags)
            if g:
                return {'g': g, 'raw': [t for t in tags if t][:5], 'src': f'apple_{media}'}
        time.sleep(_LOOKUP_SPACING_S)
    return None


def fill(pending: dict, limit: int = 150) -> int:
    """Look up `pending` {key: (title, author)}; returns titles resolved."""
    session = requests.Session()
    updates, resolved, today = {}, 0, date.today().isoformat()
    for i, (key, (title, author)) in enumerate(list(pending.items())[:limit]):
        try:
            hit = lookup(title, author, session=session)
        except RuntimeError as e:
            logger.info('book_genres: stopping fill, %s', e)
            break
        updates[key] = dict(hit or {'g': None}, at=today, t=str(title)[:120])
        resolved += 1 if hit else 0
        if len(updates) >= 25:
            save_cache(updates)
            updates = {}
        time.sleep(_LOOKUP_SPACING_S)
    save_cache(updates)
    return resolved


_fill_lock = threading.Lock()
_fill_running = False


def _fill_in_background(pending: dict, learned: dict) -> None:
    global _fill_running
    with _fill_lock:
        if _fill_running or not (pending or learned):
            return
        _fill_running = True

    def run():
        global _fill_running
        try:
            save_cache(learned)
            n = fill(pending) if pending else 0
            logger.info('book_genres: background fill resolved %d of %d', n, min(len(pending), 150))
        except Exception:
            logger.exception('book_genres: background fill failed')
        finally:
            with _fill_lock:
                _fill_running = False

    threading.Thread(target=run, name='book-genres-fill', daemon=True).start()


# ------------------------------------------------------------- annotate
def _rows(books_trending: dict, libby_trending: dict):
    for slug, panel in (books_trending or {}).items():
        for it in (panel or {}).get('items') or []:
            if isinstance(it, dict):
                yield slug, it
    for slug, panel in (libby_trending or {}).items():
        for it in (panel or {}).get('items') or []:
            if isinstance(it, dict):
                yield f'libby-{slug}', it


def annotate(books_trending: dict, libby_trending: dict, background: bool = True) -> dict:
    """Stamp `book_genre` on every Books-tab row. Never raises."""
    stats = {'own': 0, 'cross': 0, 'cached': 0, 'unclassified': 0}
    try:
        rows = list(_rows(books_trending, libby_trending))
        cache = load_cache()
        found: dict = {}
        by_title: dict = {}
        for slug, it in rows:
            g = _own_genre(it, slug)
            if g:
                it['book_genre'] = g
                stats['own'] += 1
                if g != 'Magazines':
                    k = book_key(it.get('title'), it.get('author') or it.get('artist'))
                    found.setdefault(k, g)
                    by_title.setdefault(k.split('|')[0], g)
        pending, learned = {}, {}
        for slug, it in rows:
            if it.get('book_genre'):
                continue
            k = book_key(it.get('title'), it.get('author') or it.get('artist'))
            g = found.get(k) or (by_title.get(k.split('|')[0]) if not k.split('|')[1] else None)
            if g:
                stats['cross'] += 1
            else:
                g = _cached_genre(cache.get(k))
                if g:
                    stats['cached'] += 1
            if g:
                it['book_genre'] = g
                continue
            it['book_genre'] = UNCLASSIFIED
            stats['unclassified'] += 1
            if not slug.startswith('wattpad') and _needs_lookup(cache.get(k)):
                pending[k] = (it.get('title') or '', it.get('author') or it.get('artist') or '')
        today = date.today().isoformat()
        for k, g in found.items():
            if _cached_genre(cache.get(k)) != g:
                learned[k] = {'g': g, 'src': 'chart', 'at': today}
        if background:
            _fill_in_background(pending, learned)
        stats['pending'] = len(pending)
    except Exception:
        logger.exception('book_genres: annotate failed (non-fatal)')
    return stats


def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--fill', action='store_true', help='look up every unresolved title on today\'s Books tab')
    ap.add_argument('--limit', type=int, default=1000)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    import trends_iq
    books = dict((trends_iq._read_snapshot('book_charts') or {}).get('sources') or {})
    for name in ('wattpad_charts', 'goodreads_charts'):
        books.update((trends_iq._read_snapshot(name) or {}).get('sources') or {})
    libby = (trends_iq._read_snapshot('libby_trends') or {}).get('sources') or {}
    stats = annotate(books, libby, background=False)
    print('before fill', stats)
    if args.fill:
        cache = load_cache(force=True)
        pending = {}
        for slug, it in _rows(books, libby):
            k = book_key(it.get('title'), it.get('author') or it.get('artist'))
            if it.get('book_genre') == UNCLASSIFIED and not slug.startswith('wattpad') and _needs_lookup(cache.get(k)):
                pending[k] = (it.get('title') or '', it.get('author') or it.get('artist') or '')
        print('looking up', len(pending))
        print('resolved', fill(pending, limit=args.limit))
        for slug, it in _rows(books, libby):
            it.pop('book_genre', None)
        print('after fill', annotate(books, libby, background=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(_main())
