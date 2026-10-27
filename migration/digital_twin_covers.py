"""Cover art for a Digital Twin (2026-10-27, Jenna: "those images are
awful. it should use logos or photos of people if it's a person").

The Twins tab is a cover wall. Each tile is the subject's own mark:

- a person (talent, a creator, a MUSICIAN/BAND or other TALENT
  category) gets a photo of that person;
- everything else (a series, a film, a brand, an audience named after
  a title) gets the official logo.

Never a mood photograph of a place, a ranch, a store aisle or a set.
Those were the tiles Jenna rejected. The lookup never searches the
bare subject name as a stock-photo query.

Resolution, in order, fail-safe, never raises:

  1. the subject's Profile IQ image when we already hold one;
  2. a person: the image_backfill TALENT ladder (IMDb headshot,
     Wikipedia, then a "{name} photo" web image);
  3. a logo: Wikipedia (the infobox mark), then "{name} official logo"
     / "{name} logo", then the CONTENT poster only as a last resort
     for a title, then the site favicon.

The bytes land under ``digital-twins/covers/<sha>.<ext>`` and the twin
meta carries ``hero_image`` (the dashboard URL) and ``cover_kind``
(``person`` or ``logo``). The index copies both so the wall does not
open every twin. A twin that already has a hero is left alone.

``schedule_missing`` fills twins that shipped without a cover, on a
thread, the same way room portraits do. Kill switch ``DTIQ_COVERS=0``.
Tests inject the lookups and never reach the network.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
from typing import Callable, Optional

PREFIX = 'digital-twins/covers/'
BUCKET = 'dashboard-inputs'
URL_BASE = '/api/profile-image-file/'

_AUDIENCE_TAIL = re.compile(
    r"\b(viewers?|fans?|audience|shoppers?|buyers?|customers?|"
    r"members?|users?|subscribers?|watchers?|listeners?|readers?|"
    r"players?|eaters?)\b",
    re.I,
)
_TRAIL_PLATFORM = re.compile(
    r"\s+\b(hulu|netflix|max|hbo|peacock|paramount\+?|disney\+?|"
    r"prime\s*video|amazon|apple\s*tv\+?|youtube|tubi|pluto|"
    r"roku|amc\+?|starz|showtime)\s*$",
    re.I,
)
_TITLE_FMT = {'series', 'film', 'book', 'game', 'podcast'}
_PERSON_KIND = {'talent'}
_PERSON_FMT = {'creator'}
_cfg = {'profile': None, 'resolve': None, 'search': None, 'wiki': None,
        'download': None, 'store': None, 'background': True}
_lock = threading.Lock()
_inflight: set = set()


def configure(**kwargs) -> None:
    """Tests: inject lookups (name -> url), run the fill inline, skip
    the network. ``configure()`` with no arguments restores defaults."""
    if not kwargs:
        for k in list(_cfg):
            _cfg[k] = True if k == 'background' else None
        return
    for k, v in kwargs.items():
        if k in _cfg:
            _cfg[k] = v


def enabled() -> bool:
    if str(os.environ.get('DTIQ_COVERS', '1')).strip().lower() in ('0', 'false', 'off', 'no'):
        return False
    if str(os.environ.get('REGRESSION_TEST_MODE') or '').strip() in ('1', 'true', 'yes'):
        return _cfg.get('resolve') is not None or _cfg.get('profile') is not None
    return True


def clean_entity(subject: str) -> str:
    """'Gilmore Girls Hulu viewers' -> 'Gilmore Girls'. Drops the
    audience noun and the cohort suffixes the image ladder already
    knows, so the cover is the subject, not the qualifier."""
    try:
        from image_backfill import normalize_search_name
        s = normalize_search_name(subject)
    except Exception:
        s = ' '.join(str(subject or '').replace('_', ' ').split())
    s = _AUDIENCE_TAIL.sub(' ', s)
    s = re.sub(r'\s+', ' ', s).strip(' -')
    trimmed = _TRAIL_PLATFORM.sub('', s).strip(' -')
    if trimmed:
        s = trimmed
    return s or ' '.join(str(subject or '').split())


def cover_kind(subject: str = '', kind: str = '', fmt: str = '',
               brand_category: str = '') -> str:
    """``person`` or ``logo``. A person is talent, a creator, or a
    TALENT-family brand category. A title, a brand and an audience
    named after either of those are a logo."""
    k = str(kind or '').strip().lower()
    f = str(fmt or '').strip().lower()
    if k in _PERSON_KIND or f in _PERSON_FMT:
        return 'person'
    try:
        from image_backfill import master_category
        if master_category(brand_category) == 'TALENT':
            return 'person'
    except Exception:
        cat = str(brand_category or '').strip().upper()
        if cat in ('ACTOR', 'ATHLETE', 'COMEDIAN', 'CREATOR/INFLUENCER',
                   'INFLUENCER/CREATOR', 'EMERGING TALENT', 'HOST/PERSONALITY',
                   'MUSICIAN/BAND', 'PODCASTER', 'POLITICS/ACTIVIST',
                   'WRITER/DIRECTOR/AUTHOR/ARTIST'):
            return 'person'
    return 'logo'


def search_names(subject: str, kind: str = '', fmt: str = '') -> list:
    """The names to look up, most specific first. A series is tried as
    '(TV series)' before the bare name so Yellowstone is the show, not
    the park."""
    name = clean_entity(subject)
    if not name:
        return []
    f = str(fmt or '').strip().lower()
    out = []
    if f == 'series':
        out.append(f"{name} (TV series)")
    elif f == 'film':
        out.append(f"{name} (film)")
    elif f == 'podcast':
        out.append(f"{name} (podcast)")
    out.append(name)
    return list(dict.fromkeys(out))


def _profile_image(name: str, host=None) -> Optional[str]:
    fn = _cfg.get('profile')
    if callable(fn):
        try:
            return fn(name)
        except Exception:
            return None
    if host is not None:
        try:
            from prometheus.jiq_image import profile_iq_image
            hit = profile_iq_image(host, name)
            if hit:
                return hit
        except Exception:
            pass
    try:
        import sys
        app = sys.modules.get('app')
        if app is None:
            return None
        if hasattr(app, 'load_profile_image_cache'):
            app.load_profile_image_cache()
        cache = getattr(app, 'profile_image_cache', None) or {}
        keys_fn = getattr(app, '_profile_image_lookup_keys', None)
        keys = list(keys_fn(name) or []) if callable(keys_fn) else [str(name or '').lower().strip()]
        for key in keys:
            hit = cache.get(key) or {}
            url = str(hit.get('image_url') or '').strip()
            if url:
                return url
    except Exception:
        return None
    return None


def _wiki(name: str) -> Optional[str]:
    fn = _cfg.get('wiki')
    if callable(fn):
        try:
            return fn(name)
        except Exception:
            return None
    try:
        from image_backfill import wiki_lookup
        return wiki_lookup(name)
    except Exception:
        return None


def _search(query: str) -> Optional[str]:
    """A web image search for an explicit query (always carries 'logo'
    or 'photo'). Never the bare subject."""
    fn = _cfg.get('search')
    if callable(fn):
        try:
            return fn(query)
        except Exception:
            return None
    try:
        from image_backfill import google_image_search
        url, _tag = google_image_search(query)
        return url
    except Exception:
        return None


def _resolve_bucket(name: str, master: str) -> Optional[str]:
    fn = _cfg.get('resolve')
    if callable(fn):
        try:
            return fn(name, master)
        except Exception:
            return None
    try:
        from image_backfill import resolve_image_url
        url, _tag = resolve_image_url(name, master)
        return url
    except Exception:
        return None


def _download(url: str):
    fn = _cfg.get('download')
    if callable(fn):
        try:
            return fn(url)
        except Exception:
            return None
    try:
        from image_backfill import download_image
        return download_image(url)
    except Exception:
        return None


def _store(s3, data: bytes, ext: str) -> Optional[str]:
    fn = _cfg.get('store')
    if callable(fn):
        try:
            return fn(data, ext)
        except Exception:
            return None
    if s3 is None or not data:
        return None
    ext = (ext or 'jpg').lstrip('.').lower()
    if ext == 'jpeg':
        ext = 'jpg'
    if ext not in ('jpg', 'png', 'webp', 'gif'):
        ext = 'jpg'
    key = f"{PREFIX}{hashlib.sha256(data).hexdigest()[:20]}.{ext}"
    try:
        ctype = 'image/jpeg' if ext == 'jpg' else f'image/{ext}'
        s3.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType=ctype)
        return f"{URL_BASE}{key}"
    except Exception as e:
        print(f"[twin-cover] store failed: {e}")
        return None


def _is_ours(url: str) -> bool:
    u = str(url or '')
    return u.startswith('/api/profile-image-file/') or u.startswith('/api/digital-twins/')


def _keep(url: str, s3) -> Optional[str]:
    """A dashboard URL is kept. A remote URL is downloaded and stored
    so the tile does not die when the upstream page moves."""
    url = str(url or '').strip()
    if not url:
        return None
    if _is_ours(url):
        return url
    got = _download(url)
    if not got:
        return None
    data, ext = got
    return _store(s3, data, ext)


def resolve_cover_url(subject: str, *, kind: str = '', fmt: str = '',
                      brand_category: str = '', s3=None, host=None) -> tuple:
    """(url, cover_kind, note). url is None on a miss. Never raises."""
    if not enabled():
        return None, cover_kind(subject, kind, fmt, brand_category), 'off'
    mode = cover_kind(subject, kind, fmt, brand_category)
    names = search_names(subject, kind, fmt)
    if not names:
        return None, mode, 'none'
    for name in names:
        hit = _profile_image(name, host=host)
        if hit:
            kept = _keep(hit, s3) or (hit if _is_ours(hit) else None)
            if kept:
                return kept, mode, f'profile:{name}'
    if mode == 'person':
        for name in names:
            url = _resolve_bucket(name, 'TALENT')
            kept = _keep(url, s3) if url else None
            if kept:
                return kept, mode, f'talent:{name}'
        for name in names:
            url = _search(f"{name} photo")
            kept = _keep(url, s3) if url else None
            if kept:
                return kept, mode, f'photo:{name}'
        return None, mode, 'none'
    for name in names:
        url = _wiki(name)
        kept = _keep(url, s3) if url else None
        if kept:
            return kept, mode, f'wiki:{name}'
    for name in names:
        for q in (f"{name} official logo", f"{name} logo"):
            url = _search(q)
            kept = _keep(url, s3) if url else None
            if kept:
                return kept, mode, f'logo:{q}'
    if str(fmt or '').strip().lower() in _TITLE_FMT:
        for name in names:
            url = _resolve_bucket(name, 'CONTENT')
            kept = _keep(url, s3) if url else None
            if kept:
                return kept, mode, f'poster:{name}'
    try:
        from image_backfill import google_favicon
        fav = google_favicon(names[-1])
        kept = _keep(fav, s3) if fav else None
        if kept:
            return kept, mode, f'favicon:{names[-1]}'
    except Exception:
        pass
    return None, mode, 'none'


def attach_cover(s3, payload: dict, *, host=None, force: bool = False) -> bool:
    """Write hero_image + cover_kind on the twin meta when missing.
    Returns True when the meta changed. Never raises."""
    if not isinstance(payload, dict):
        return False
    meta = payload.setdefault('meta', {})
    have = str(meta.get('hero_image') or '').strip()
    if have and not force:
        if not meta.get('cover_kind'):
            meta['cover_kind'] = cover_kind(meta.get('subject') or meta.get('name') or '',
                                            meta.get('subject_kind') or '',
                                            meta.get('format') or '',
                                            meta.get('brand_category') or '')
            return True
        return False
    try:
        url, mode, note = resolve_cover_url(
            str(meta.get('subject') or meta.get('name') or ''),
            kind=str(meta.get('subject_kind') or ''),
            fmt=str(meta.get('format') or ''),
            brand_category=str(meta.get('brand_category') or ''),
            s3=s3, host=host,
        )
    except Exception as e:
        print(f"[twin-cover] resolve failed: {e}")
        return False
    meta['cover_kind'] = mode
    if url:
        meta['hero_image'] = url
        print(f"[twin-cover] {meta.get('name') or meta.get('subject')}: {note}")
        return True
    print(f"[twin-cover] {meta.get('name') or meta.get('subject')}: no cover ({note})")
    return bool(meta.get('cover_kind'))


def schedule_missing(s3, runs: list, *, load_fn: Optional[Callable] = None,
                     save_fn: Optional[Callable] = None, limit: int = 6) -> int:
    """Fill twins whose index row has no hero_image. Returns how many
    were queued. The list route stays fast; the wall picks the image
    up on the next list load."""
    if not enabled() or s3 is None:
        return 0
    missing = [r for r in (runs or []) if isinstance(r, dict) and r.get('key')
               and not str(r.get('hero_image') or '').strip()]
    if not missing:
        return 0
    queued = []
    with _lock:
        for r in missing[:max(1, int(limit))]:
            k = str(r.get('key') or '')
            if k and k not in _inflight:
                _inflight.add(k)
                queued.append(r)
    if not queued:
        return 0

    def work():
        load = load_fn
        save = save_fn
        if load is None or save is None:
            try:
                from migration.digital_twin_synthesis import load_twin, save_twin
                load = load or load_twin
                save = save or save_twin
            except Exception as e:
                print(f"[twin-cover] fill skipped: {e}")
                with _lock:
                    for r in queued:
                        _inflight.discard(str(r.get('key') or ''))
                return
        for r in queued:
            key = str(r.get('key') or '')
            try:
                payload = load(s3, key)
                if isinstance(payload, dict) and attach_cover(s3, payload):
                    save(s3, key, payload)
                    r['hero_image'] = (payload.get('meta') or {}).get('hero_image') or ''
                    r['cover_kind'] = (payload.get('meta') or {}).get('cover_kind') or ''
            except Exception as e:
                print(f"[twin-cover] fill {key}: {e}")
            finally:
                with _lock:
                    _inflight.discard(key)

    if _cfg.get('background') is False:
        work()
    else:
        threading.Thread(target=work, daemon=True, name='dtw-covers').start()
    return len(queued)


__all__ = ['PREFIX', 'clean_entity', 'cover_kind', 'search_names', 'resolve_cover_url',
           'attach_cover', 'schedule_missing', 'configure', 'enabled']
